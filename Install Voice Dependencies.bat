@echo off
setlocal
cd /d "%~dp0"
title MyLocalAI v8.7 - Install Voice Dependencies
set HF_HUB_DISABLE_XET=1
set HF_HUB_DOWNLOAD_TIMEOUT=60
set HF_HUB_ETAG_TIMEOUT=60
set PY=python
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set PY="%LocalAppData%\Programs\Python\Python313\python.exe"
echo ============================================
echo MyLocalAI v8.7 Voice Dependency Installer
echo ============================================
echo Working folder: %CD%
%PY% --version
%PY% -m pip install --upgrade pip
%PY% -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
 echo Installation failed. Use the Debug launcher and logs for details.
 pause
 exit /b 1
)
echo.
echo Done. Open Voice ^> Run Diagnostics ^> Test Microphone ^> Load/Test Whisper.
pause
