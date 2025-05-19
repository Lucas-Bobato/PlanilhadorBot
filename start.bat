@echo off
set VENV_NAME=venv_py313

if not exist "%VENV_NAME%\Scripts\activate.bat" (
    echo Ambiente virtual %VENV_NAME% nao encontrado.
    echo Execute setup.bat primeiro para criar o ambiente e instalar as dependencias.
    pause
    exit /b 1
)

echo Ativando ambiente virtual %VENV_NAME%...
call "%VENV_NAME%\Scripts\activate.bat"

echo Iniciando o PlanilhadorBot...
rem Garante que o Python do ambiente virtual seja usado
python bot.py

echo Bot encerrado. Pressione qualquer tecla para fechar esta janela.
pause > nul