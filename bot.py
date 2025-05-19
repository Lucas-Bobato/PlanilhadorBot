import logging
import os
import json
from io import BytesIO
from datetime import datetime
import re 

import google.generativeai as genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from dotenv import load_dotenv
from telegram import Update, BotCommand
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Carregar variáveis de ambiente do arquivo .env
load_dotenv()

# Configurações
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID")
DEFAULT_SHEET_TAB_NAME = os.getenv("GOOGLE_SHEET_TAB_NAME", "Página1") 
GOOGLE_CREDENTIALS_FILE = 'credentials.json'
SCOPES_SHEETS = ['https://www.googleapis.com/auth/spreadsheets']

ACTIVE_SHEET_TAB_NAME = DEFAULT_SHEET_TAB_NAME

logging.basicConfig(
    format="%(asctime)s | [%(levelname)s] | %(name)s: %(message)s", 
    datefmt='%H:%M:%S',
    level=logging.INFO
)
logger = logging.getLogger("PlanilhadorBot")


sheets_service = None 
gemini_model = None

def initialize_sheets_service_on_startup(credentials_file_path, scopes):
    global sheets_service
    try:
        abs_credentials_file_path = os.path.abspath(credentials_file_path)
        if not os.path.exists(abs_credentials_file_path):
            logger.error(f"ARQUIVO DE CREDENCIAIS NÃO ENCONTRADO em: {abs_credentials_file_path}")
            return
        creds = Credentials.from_service_account_file(
            abs_credentials_file_path, scopes=scopes
        )
        service_instance = build('sheets', 'v4', credentials=creds, cache_discovery=False)
        logger.info("Serviço do Google Sheets inicializado.")
        sheets_service = service_instance
    except Exception as e:
        logger.error(f"Erro ao inicializar serviço do Google Sheets: {e}", exc_info=True)

def initialize_gemini_model():
    global gemini_model
    try:
        if GEMINI_API_KEY:
            genai.configure(api_key=GEMINI_API_KEY)
            gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')
            logger.info(f"API Gemini configurada ({gemini_model.model_name}).")
        else:
            logger.error("Chave da API Gemini não encontrada.")
    except Exception as e:
        logger.error(f"Erro ao configurar API Gemini: {e}", exc_info=True)

async def get_first_empty_row_in_col(service, spreadsheet_id, tab_name, column_letter='B'):
    if not service: return None
    try:
        range_to_check = f"{tab_name}!{column_letter}:{column_letter}"
        result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_to_check).execute()
        data_values = result.get('values', [])
        return len(data_values) + 1
    except Exception as e:
        logger.error(f"Erro ao buscar primeira linha vazia em '{tab_name}': {e}", exc_info=True)
        return None

async def update_sheet_row(service, spreadsheet_id, tab_name, row_number, values_for_row):
    if not service: logger.error("Serviço do Google Sheets não inicializado."); return False
    try:
        write_range = f"{tab_name}!A{row_number}:H{row_number}"
        body = {'values': [values_for_row]}
        update_result = service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id, range=write_range,
            valueInputOption='USER_ENTERED', body=body
        ).execute()
        logger.info(f"{update_result.get('updatedCells')} células atualizadas ({tab_name} linha {row_number}).")
        return True
    except Exception as e:
        logger.error(f"Erro ao atualizar linha na planilha (aba: {tab_name}, linha: {row_number}): {e}", exc_info=True)
        return False

async def ensure_sheet_headers(spreadsheet_id, tab_name, headers_map_managed_by_bot, service):
    if not service: logger.error(f"Serviço do Sheets não inicializado para aba {tab_name}."); return
    try:
        managed_cols = sorted(headers_map_managed_by_bot.keys())
        if not managed_cols: logger.info(f"Nenhum cabeçalho a ser gerenciado pelo bot para aba {tab_name}."); return
        max_managed_col_char = managed_cols[-1]
        range_managed_headers = f"{tab_name}!A1:{max_managed_col_char}1"
        
        try:
            sheet_metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            sheets = sheet_metadata.get('sheets', [])
            sheet_exists = any(s.get('properties', {}).get('title') == tab_name for s in sheets)
            if not sheet_exists:
                logger.info(f"Aba '{tab_name}' não encontrada. Tentando criar...")
                body_add_sheet = {'requests': [{'addSheet': {'properties': {'title': tab_name}}}]}
                service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body_add_sheet).execute()
                logger.info(f"Aba '{tab_name}' criada com sucesso.")
        except Exception as e_sheet_create:
            logger.error(f"Erro ao verificar/criar aba '{tab_name}': {e_sheet_create}")
            # Prossegue para tentar ler/escrever cabeçalhos, pode falhar se a criação falhou
            
        result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_managed_headers).execute()
        current_row_values = result.get('values', [[]])[0]
        num_cols_to_write = ord(max_managed_col_char) - ord('A') + 1
        header_row_to_write = [''] * num_cols_to_write
        needs_update = False
        for col_char, header_text in headers_map_managed_by_bot.items():
            col_index = ord(col_char) - ord('A')
            if col_index < num_cols_to_write: header_row_to_write[col_index] = header_text
            if col_index >= len(current_row_values) or current_row_values[col_index] != header_text: needs_update = True
        if not current_row_values and headers_map_managed_by_bot: needs_update = True
        if needs_update:
            logger.info(f"Cabeçalhos em '{tab_name}' precisam ser definidos/atualizados.")
            body = {'values': [header_row_to_write]}
            service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range=range_managed_headers,
                valueInputOption='USER_ENTERED', body=body
            ).execute()
            logger.info(f"Cabeçalhos definidos/atualizados em '{tab_name}'.")
        else:
            logger.info(f"Cabeçalhos OK em '{tab_name}'.")
    except Exception as e:
        logger.error(f"Erro ao verificar/adicionar cabeçalhos para '{tab_name}': {e}", exc_info=True)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global ACTIVE_SHEET_TAB_NAME
    user = update.effective_user
    await update.message.reply_html(
        f"Olá {user.mention_html()}! Envie-me uma imagem de uma aposta.\n"
        f"A aba ativa é: <b>{ACTIVE_SHEET_TAB_NAME}</b>.\n"
        f"Use <code>/pagina nome_da_aba</code> para mudar.\n"
        f"Se houver uma unidade específica (ex: 1.5u), coloque na legenda da imagem.",
    )

async def change_page_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global ACTIVE_SHEET_TAB_NAME, sheets_service, GOOGLE_SHEET_ID
    if not context.args:
        await update.message.reply_text("Uso: /pagina NovoNomeDaAba"); return
    new_tab_name = " ".join(context.args)
    old_tab_name = ACTIVE_SHEET_TAB_NAME
    ACTIVE_SHEET_TAB_NAME = new_tab_name
    logger.info(f"Usuário {update.effective_user.username} mudou aba de '{old_tab_name}' para '{new_tab_name}'.")
    await update.message.reply_text(f"Aba ativa alterada para: {ACTIVE_SHEET_TAB_NAME}")
    if sheets_service and GOOGLE_SHEET_ID:
        headers_map = {
            'B': "Data", 'C': "Entrada", 'D': "Casa", 'E': "Odd", 'F': "Unidades"
        }
        await ensure_sheet_headers(GOOGLE_SHEET_ID, ACTIVE_SHEET_TAB_NAME, headers_map, sheets_service)
        await update.message.reply_text(f"Cabeçalhos verificados/configurados para '{ACTIVE_SHEET_TAB_NAME}'.")
    else:
        await update.message.reply_text(f"Aviso: Serviço do Sheets não pronto para configurar cabeçalhos em '{ACTIVE_SHEET_TAB_NAME}'.")

def build_gemini_prompt(image_bytes: bytes):
    image_parts = [{"mime_type": "image/jpeg", "data": image_bytes}]
    prompt_text = """
    Analise a imagem desta aposta esportiva e extraia as seguintes informações em formato JSON.
    Se uma informação não estiver claramente visível, retorne null ou uma string vazia para o campo correspondente.

    Campos a serem extraídos:
    - "evento_times": String. Os times ou evento principal da aposta.
    - "aposta_descricao_completa": String. A descrição principal da aposta.
    - "odd": String ou Float. A odd principal, priorize promocional/maior. Formate com ponto decimal.
    - "unidade_imagem": String ou Float. A unidade da aposta, APENAS SE VISÍVEL DENTRO DA IMAGEM (ex: "0.75u" -> "0.75"). Se não estiver na imagem, retorne null.
    - "casa_de_aposta_tentativa_gemini": String. Identifique a casa de apostas ("Superbet", "Bet365", ou "A Definir") com base nos seguintes critérios, priorizando o primeiro que se aplicar:
        1. Texto Explícito: Se o nome "Superbet" ou "Bet365" estiver claramente visível na imagem, use esse nome.
        2. Cores Distintivas da Interface:
           - SUPERBET: Geralmente apresenta uma distinta BARRA LATERAL VERMELHA à esquerda da área principal da aposta, OU a interface tem um tema escuro com elementos de destaque importantes (como botões ou odds promocionais) em VERMELHO ou LARANJA.
           - BET365: Geralmente apresenta um tema ESCURO, e em layouts de "Criar Aposta" ou "Bet Builder", os ITENS DE SELEÇÃO INDIVIDUAL ou os NOMES DOS JOGADORES são destacados em tons de VERDE. A palavra "Stake" também pode estar presente perto de um campo de valor.
        Se nenhum desses critérios for atendido de forma clara, ou se houver ambiguidade, retorne "A Definir".
    - "data_hora_evento_imagem": String. Data ou hora do evento na imagem.
    - "todos_textos_visiveis": Array de Strings. Extraia o máximo de texto possível da imagem, incluindo botões como "+ Adicionar", "Crear Apuesta", "BET BUILDER", "Stake", "Importe", etc. Este campo é MUITO IMPORTANTE para uma possível identificação da casa de apostas por palavras-chave no código do bot se a identificação por cor/layout falhar.

    Exemplo de JSON esperado:
    {
      "evento_times": "Liquid vs FaZe",
      "aposta_descricao_completa": "s1mple terá Mais de 30.5 abates nos mapas 1-2 (Inc. prorrogação)",
      "odd": "1.95",
      "unidade_imagem": "1", 
      "casa_de_aposta_tentativa_gemini": "Superbet", 
      "data_hora_evento_imagem": "Hoje, 13:30",
      "todos_textos_visiveis": ["Counter-Strike 2", "ESL", "IEM Dallas", "Hoje, 13:30", "Liquid", "FaZe", "s1mple terá Mais de 30.5 abates nos mapas 1-2 (Inc. prorrogação)", "1.85", "1.95", "1u - Min: 1.95", "Bobets - EV+ - VIP", "+ Adicionar"]
    }
    """
    return [prompt_text, image_parts[0]]

def extract_unit_from_caption(caption: str) -> str | None:
    if not caption: return None
    matches = re.findall(r"(\d+(?:[.,]\d+)?)\s*(u|unid\.?|unidades|unit|units)", caption, re.IGNORECASE)
    if not matches: return None
    max_unit = 0.0
    found_any_unit = False
    for match_tuple in matches:
        unit_str = match_tuple[0].replace(',', '.')
        try:
            unit_val = float(unit_str)
            if unit_val > max_unit: max_unit = unit_val
            found_any_unit = True
        except ValueError:
            logger.warning(f"Não foi possível converter a unidade '{match_tuple[0]}' para float na legenda.")
            continue
    return str(max_unit) if found_any_unit else None

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global ACTIVE_SHEET_TAB_NAME 
    data_from_gemini = {} 
    if not gemini_model: await update.message.reply_text("Serviço de análise de imagem não configurado."); return
    if not sheets_service: await update.message.reply_text("Integração com a planilha não configurada."); return

    photo_file = await update.message.photo[-1].get_file()
    image_caption = update.message.caption 
    image_bytes_io = BytesIO()
    await photo_file.download_to_memory(image_bytes_io)
    image_bytes_io.seek(0)
    
    try:
        img_bytes_for_gemini = image_bytes_io.getvalue()
        await update.message.reply_text(f"Analisando imagem para aba '{ACTIVE_SHEET_TAB_NAME}'...")
        prompt_parts = build_gemini_prompt(img_bytes_for_gemini)
        response = gemini_model.generate_content(prompt_parts)
        extracted_text = response.text.strip()
        if extracted_text.startswith("```json"): extracted_text = extracted_text[7:]
        if extracted_text.endswith("```"): extracted_text = extracted_text[:-3]
        logger.info(f"Texto extraído do Gemini para JSON: {extracted_text}")
        try:
            data_from_gemini = json.loads(extracted_text)
        except json.JSONDecodeError as e:
            logger.error(f"Erro ao decodificar JSON da resposta do Gemini: {e}")
            await update.message.reply_text(f"Não consegui entender a estrutura da resposta da IA.\nResposta: {response.text[:500]}...")
            return

        target_row_number = await get_first_empty_row_in_col(
            sheets_service, GOOGLE_SHEET_ID, ACTIVE_SHEET_TAB_NAME, column_letter='B'
        )
        if not target_row_number:
            await update.message.reply_text(f"Não foi possível determinar a próxima linha vazia na aba '{ACTIVE_SHEET_TAB_NAME}'."); return
        logger.info(f"Próxima linha para inserção na aba '{ACTIVE_SHEET_TAB_NAME}': {target_row_number}")

        data_aposta_str = data_from_gemini.get("data_hora_evento_imagem", "")
        data_col_b = datetime.now().strftime("%d/%m/%y") 
        if data_aposta_str and "hoje" not in data_aposta_str.lower(): data_col_b = data_aposta_str 
        
        entrada_col_c = data_from_gemini.get("aposta_descricao_completa", "N/A")
        
        casa_col_d = data_from_gemini.get("casa_de_aposta_tentativa_gemini", "A Definir")
        if casa_col_d in ["A Definir", "null", None, ""]: 
            todos_textos_visiveis = data_from_gemini.get("todos_textos_visiveis", [])
            texto_completo_para_analise = " ".join(str(t).lower() for t in todos_textos_visiveis)
            texto_completo_para_analise += f" {str(entrada_col_c).lower()} {str(data_from_gemini.get('evento_times','')).lower()}"
            if "+ adicionar" in texto_completo_para_analise: casa_col_d = "Superbet"
            elif any(keyword in texto_completo_para_analise for keyword in ["crear apuesta", "bet builder", "stake"]): casa_col_d = "Bet365"
            logger.info(f"Casa definida por fallback: {casa_col_d} (Gemini: '{data_from_gemini.get('casa_de_aposta_tentativa_gemini')}')")
        else:
            logger.info(f"Casa definida pelo Gemini: {casa_col_d}")
        
        odd_str = str(data_from_gemini.get("odd", "N/A"))
        odd_col_e = odd_str.replace('.', ',') if odd_str != "N/A" else "N/A"
        
        unidade_final_str = "N/A"
        unidade_da_legenda = extract_unit_from_caption(image_caption)
        if unidade_da_legenda:
            unidade_final_str = unidade_da_legenda
            logger.info(f"Unidade da legenda: {unidade_final_str}")
        else:
            unidade_gemini_str = str(data_from_gemini.get("unidade_imagem", "N/A"))
            if unidade_gemini_str not in ["N/A", "None", "null", ""]:
                unidade_final_str = unidade_gemini_str
                logger.info(f"Unidade da imagem (Gemini): {unidade_final_str}")
            else:
                logger.info("Unidade não encontrada.")
        unidade_col_f = unidade_final_str.replace('.', ',') if unidade_final_str not in ["N/A", "None", "null", ""] else "N/A"
        
        status_col_g = "Pré-Live"

        row_to_insert_values = [
            "", data_col_b, entrada_col_c, casa_col_d, odd_col_e, 
            unidade_col_f, status_col_g
        ]
        
        success_write = await update_sheet_row(
            sheets_service, GOOGLE_SHEET_ID, ACTIVE_SHEET_TAB_NAME,
            target_row_number, row_to_insert_values
        )
        
        if success_write:
            # --- Mensagem de Resposta Mais Concisa ---
            confirm_message = f"✅ Aposta '{entrada_col_c}' adicionada à aba '{ACTIVE_SHEET_TAB_NAME}' (linha {target_row_number})."
            if casa_col_d != "A Definir":
                confirm_message += f"\nCasa: {casa_col_d}"
            if unidade_col_f != "N/A":
                confirm_message += f" | Unidades: {unidade_col_f}"
            await update.message.reply_text(confirm_message)
            # --- Fim da Mensagem de Resposta Mais Concisa ---
        else:
            await update.message.reply_text(f"Falha ao adicionar dados à aba '{ACTIVE_SHEET_TAB_NAME}'.")

    except Exception as e:
        logger.error(f"Erro ao processar imagem: {e}", exc_info=True)
        await update.message.reply_text(f"Ocorreu um erro ao processar a imagem: {str(e)}")

async def post_init(application: Application) -> None:
    global ACTIVE_SHEET_TAB_NAME 
    await application.bot.set_my_commands([
        BotCommand("start", "Inicia o bot e mostra aba ativa"),
        BotCommand("pagina", "Muda a aba da planilha. Ex: /pagina Junho")
    ])
    logger.info("Comandos do bot definidos.")
    headers_bot_gerencia_map = {
        'B': "Data", 'C': "Entrada", 'D': "Casa", 'E': "Odd", 'F': "Unidades"
    }
    if sheets_service and GOOGLE_SHEET_ID and ACTIVE_SHEET_TAB_NAME:
        await ensure_sheet_headers(GOOGLE_SHEET_ID, ACTIVE_SHEET_TAB_NAME, headers_bot_gerencia_map, sheets_service)
    else:
        logger.warning("Serviço do Sheets não disponível para verificar cabeçalhos na aba ativa inicial.")

def main() -> None:
    initialize_sheets_service_on_startup(GOOGLE_CREDENTIALS_FILE, SCOPES_SHEETS)
    initialize_gemini_model()

    if not TELEGRAM_BOT_TOKEN:
        logger.critical("Token do Telegram não definido. Encerrando.")
        return
    if not GOOGLE_SHEET_ID:
         logger.critical("ID da Planilha Google não definido. Encerrando.")
         return
    global ACTIVE_SHEET_TAB_NAME 
    if not ACTIVE_SHEET_TAB_NAME: 
        logger.warning("Nome da aba da planilha não definido no .env. Usando 'Página1' como padrão.")
        ACTIVE_SHEET_TAB_NAME = "Página1"
        
    if not sheets_service: 
        logger.critical("Serviço do Google Sheets não pôde ser inicializado. Verifique os logs e credentials.json. Encerrando.")
        return
    if not gemini_model:
        logger.warning("Modelo Gemini não pôde ser inicializado. Análise de imagem desabilitada.")
        
    builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
    application = builder.post_init(post_init).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("pagina", change_page_command))
    application.add_handler(MessageHandler(filters.PHOTO, handle_image))
    logger.info(f"Bot Planilhador iniciado. Aba ativa inicial: {ACTIVE_SHEET_TAB_NAME}.")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()