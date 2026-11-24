@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 app.py
) else (
  python app.py
)
if errorlevel 1 (
  echo.
  echo Could not start the school system.
  echo Install Python 3.8 on Windows 7 and try again.
  pause
)
endlocal
