@echo off
setlocal
cd /d "%~dp0"
set "ROOT=%~dp0"
set "PYTHONW="
if exist "%LocalAppData%\Programs\Python\Python314\pythonw.exe" set "PYTHONW=%LocalAppData%\Programs\Python\Python314\pythonw.exe"
if not defined PYTHONW if exist "%LocalAppData%\Programs\Python\Python313\pythonw.exe" set "PYTHONW=%LocalAppData%\Programs\Python\Python313\pythonw.exe"
if not defined PYTHONW for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do if not defined PYTHONW set "PYTHONW=%%P"
if defined PYTHONW (
  start "MyLocalAI Autonomous Lab" /min "%PYTHONW%" "%ROOT%services\autonomous_lab.py"
  echo Autonomous AI Lab started in the background.
  echo Log: "%ROOT%logs\autonomous_lab.log"
  goto :done
)
set "PYTHON="
if exist "%LocalAppData%\Programs\Python\Python314\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python314\python.exe"
if not defined PYTHON if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PYTHON for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined PYTHON set "PYTHON=%%P"
if defined PYTHON (
  start "MyLocalAI Autonomous Lab" /min "%PYTHON%" "%ROOT%services\autonomous_lab.py"
  echo Autonomous AI Lab started in the background.
  echo Log: "%ROOT%logs\autonomous_lab.log"
  goto :done
)
echo Could not find Python or pythonw.exe.
echo Install Python and make sure it is available to this Windows user.
exit /b 1
:done
endlocal
