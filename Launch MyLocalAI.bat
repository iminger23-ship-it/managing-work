@echo off
setlocal
cd /d "%~dp0"
set HF_HUB_DISABLE_XET=1
set HF_HUB_DOWNLOAD_TIMEOUT=60
set HF_HUB_ETAG_TIMEOUT=60
if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" (
 start "MyLocalAI" /D "%~dp0" "%LocalAppData%\Programs\Python\Python313\pythonw.exe" gui.py
) else (
 where pythonw >nul 2>nul && (start "MyLocalAI" /D "%~dp0" pythonw.exe gui.py) || (start "MyLocalAI" /D "%~dp0" python.exe gui.py)
)
endlocal
