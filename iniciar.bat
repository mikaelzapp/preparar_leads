@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Execute instalar.bat antes.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app.py --server.address localhost
pause
