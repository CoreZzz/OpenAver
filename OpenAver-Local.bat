@echo off
setlocal

set "APP_DIR=%~dp0"
cd /d "%APP_DIR%"

set "PYTHON=%APP_DIR%venv\Scripts\python.exe"
set "PYTHONW=%APP_DIR%venv\Scripts\pythonw.exe"

if not exist "%PYTHON%" (
    echo OpenAver local venv was not found.
    echo Please install dependencies first, or run:
    echo   python -m venv venv
    echo   venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%PYTHONW%" (
    set "PYTHONW=%PYTHON%"
)

"%PYTHON%" -c "import fastapi, uvicorn, webview" >nul 2>nul
if errorlevel 1 (
    echo Installing OpenAver runtime dependencies...
    "%PYTHON%" -m pip install -r "%APP_DIR%requirements.txt"
    if errorlevel 1 (
        echo Dependency installation failed.
        pause
        exit /b 1
    )
)

start "OpenAver Local" "%PYTHONW%" "%APP_DIR%windows\standalone.py"
exit /b 0
