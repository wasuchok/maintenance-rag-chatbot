@echo off
setlocal

cd /d "%~dp0"

if exist ".venv312\Scripts\python.exe" (
  set "PYTHON_BIN=.venv312\Scripts\python.exe"
  goto run_api
)

if exist "venv\Scripts\python.exe" (
  set "PYTHON_BIN=venv\Scripts\python.exe"
  goto run_api
)

echo ไม่พบ virtualenv ที่พร้อมใช้งานสำหรับรัน Django API 1>&2
echo. 1>&2
echo ให้สร้าง environment ก่อน เช่น: 1>&2
echo   py -3.12 -m venv .venv312 1>&2
echo   .venv312\Scripts\python.exe -m pip install -r requirements.txt 1>&2
echo. 1>&2
echo จากนั้นรันใหม่: 1>&2
echo   run_api.bat 1>&2
exit /b 1

:run_api
if not defined HOST set "HOST=0.0.0.0"
if not defined PORT set "PORT=8000"

if /I "%RELOAD%"=="true" (
  "%PYTHON_BIN%" -m uvicorn config.asgi:application --host "%HOST%" --port "%PORT%" --reload %*
) else (
  "%PYTHON_BIN%" -m uvicorn config.asgi:application --host "%HOST%" --port "%PORT%" %*
)
exit /b %ERRORLEVEL%
