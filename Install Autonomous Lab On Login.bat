@echo off
setlocal
cd /d "%~dp0"
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
  set "PYTHONW=%%P"
  goto :found
)
:found
if not defined PYTHONW (
  echo Could not find pythonw.exe on PATH.
  echo Install Python and ensure it is available to this account, then retry.
  pause
  exit /b 1
)
set "TASK=MyLocalAI Autonomous Lab"
schtasks /create /tn "%TASK%" /tr "\"%PYTHONW%\" \"%~dp0services\autonomous_lab.py\"" /sc onlogon /f >nul
if errorlevel 1 (
  echo Failed to create the scheduled task.
  pause
  exit /b 1
)
echo Installed: %TASK%
echo The autonomous lab will start automatically when this Windows user logs in.
endlocal
pause
