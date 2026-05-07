@echo off
setlocal

cd /d "%~dp0"

set "DEBUG=false"

if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info < (3, 14) else 1)" >nul 2>&1
  if not errorlevel 1 (
    set "PYTHON_BIN=venv\Scripts\python.exe"
    goto run_chainlit
  )
)

if exist ".venv312\Scripts\python.exe" (
  set "PYTHON_BIN=.venv312\Scripts\python.exe"
  goto run_chainlit
)

echo Chainlit ในโปรเจกต์นี้มีปัญหาบน Python 3.14 1>&2
echo. 1>&2
echo ให้สร้าง venv สำหรับ Chainlit ด้วย Python 3.12 ก่อน: 1>&2
echo   py -3.12 -m venv .venv312 1>&2
echo   .venv312\Scripts\python.exe -m pip install -r requirements.txt 1>&2
echo. 1>&2
echo จากนั้นรันใหม่: 1>&2
echo   run_chainlit.bat 1>&2
exit /b 1

:run_chainlit
if not defined HOST set "HOST=0.0.0.0"
if not defined PORT set "PORT=8100"

"%PYTHON_BIN%" -m chainlit run chainlit_app.py -w --host "%HOST%" --port "%PORT%" %*
exit /b %ERRORLEVEL%
