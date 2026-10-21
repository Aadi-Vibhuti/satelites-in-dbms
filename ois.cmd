@echo off
setlocal
set "PROJECT_DIR=%~dp0"
set "PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo Python environment not found. Run setup.ps1 first.
    exit /b 1
)
set "PYTHONPATH=%PROJECT_DIR%;%PYTHONPATH%"
"%PYTHON%" -m app %*
exit /b %ERRORLEVEL%
