@echo off
setlocal
cd /d "%~dp0"
set HF_HUB_DISABLE_XET=1
set HF_HUB_DOWNLOAD_TIMEOUT=60
set HF_HUB_ETAG_TIMEOUT=60
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
 "%LocalAppData%\Programs\Python\Python313\python.exe" gui.py
) else (
 python gui.py
)
echo.
echo MyLocalAI exited. Review logs\mylocalai.log for details.
pause
