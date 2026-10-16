@echo off
rem One-click start on Windows: sets up Python packages once, then opens the website.
rem Needs Python 3.12 or newer (https://www.python.org/downloads/).
cd /d "%~dp0"
if not exist .venv (
    echo Creating the Python environment ^(first start only^)...
    python -m venv .venv || (echo Python 3.12 or newer is required: https://www.python.org/downloads/ & pause & exit /b 1)
)
echo Installing or checking packages...
call .venv\Scripts\python.exe -m pip install -q --upgrade pip
call .venv\Scripts\python.exe -m pip install -q -r app\requirements.txt || (echo Package installation failed. & pause & exit /b 1)
if not exist .env (
    copy .env.example .env >nul
    echo Created .env - put your own ANTHROPIC_API_KEY there to enable Claude ^(optional^).
)
echo Opening the website in your browser. Close this window to stop it.
.venv\Scripts\streamlit.exe run app\streamlit_app.py
pause
