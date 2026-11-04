@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m venv .venv
) else (
    python -m venv .venv
)
if errorlevel 1 (
    echo ERRO: instale Python 3.12+ e marque Add python.exe to PATH.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo Falha ao instalar dependencias.
    pause
    exit /b 1
)
echo Instalacao concluida. Execute iniciar.bat.
pause
