@echo off
setlocal enabledelayedexpansion

echo Verificando instalacao do Python 3.13...
set PYTHON_EXE=py -3.13
%PYTHON_EXE% --version >nul 2>nul
if !errorlevel! neq 0 (
    echo Python 3.13 (comando '%PYTHON_EXE%') nao encontrado ou nao esta funcionando.
    echo Verificando 'python' como fallback...
    set PYTHON_EXE=python
    %PYTHON_EXE% --version >nul 2>nul
    if !errorlevel! neq 0 (
        echo Python (nem 'py -3.13' nem 'python') nao encontrado no PATH.
        echo Por favor, instale Python 3.13 e certifique-se de que esta acessivel.
        echo Baixe em: https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

for /f "delims=" %%i in ('%PYTHON_EXE% --version') do set PYTHON_VERSION=%%i
echo Python encontrado: !PYTHON_VERSION! (Usando: %PYTHON_EXE%)

set VENV_NAME=venv_py313
echo Ambiente virtual sera criado/usado em: %VENV_NAME%

if not exist "%VENV_NAME%\Scripts\activate.bat" (
    echo.
    echo Criando ambiente virtual "%VENV_NAME%" com %PYTHON_EXE%...
    %PYTHON_EXE% -m venv "%VENV_NAME%"
    if !errorlevel! neq 0 (
        echo Falha ao criar o ambiente virtual. Verifique sua instalacao do Python.
        pause
        exit /b 1
    )
    echo Ambiente virtual "%VENV_NAME%" criado com sucesso.
) else (
    echo.
    echo Ambiente virtual "%VENV_NAME%" ja existe. Usando o existente.
)

echo.
echo Ativando ambiente virtual...
call "%VENV_NAME%\Scripts\activate.bat"

echo.
echo Atualizando pip...
python -m pip install --upgrade pip
if !errorlevel! neq 0 (
    echo Falha ao atualizar o pip.
    pause
    exit /b 1
)

echo.
echo Instalando dependencias do requirements.txt...
if not exist "requirements.txt" (
    echo Arquivo requirements.txt nao encontrado nesta pasta!
    pause
    exit /b 1
)
pip install -r requirements.txt
if !errorlevel! neq 0 (
    echo Falha ao instalar dependencias. Verifique o arquivo requirements.txt, sua conexao com a internet e se o ambiente virtual esta ativo.
    pause
    exit /b 1
)

echo.
echo --- Instalacao e Configuracao Concluidas com Sucesso ---
echo.
echo Para iniciar o bot, execute o arquivo start.bat
echo.
echo Lembretes:
echo   - Certifique-se de que os arquivos .env e credentials.json estao configurados corretamente nesta pasta.
echo   - Se voce fechar este terminal, o ambiente virtual sera desativado.
echo     O start.bat ira reativa-lo automaticamente.
echo.
echo Pressione qualquer tecla para sair...
pause > nul
exit /b 0