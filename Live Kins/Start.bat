@echo off
rem Starts the app. The first time, it installs what the app needs: Python, its parts, and its browser.
setlocal
cd /d "%~dp0"
title Live Kins

call :find_python
if defined PY goto have_python

echo This app needs Python (free). It is not on this computer yet.
echo.
choice /c YN /m "Install it now"
if errorlevel 2 goto no_python
call :install_with_winget
call :find_python
if defined PY goto have_python
call :install_from_python_org
call :find_python
if defined PY goto have_python
echo.
echo Python could not be installed by itself.
echo Get it from python.org/downloads (tick "Add python.exe to PATH"), then double-click Start.bat again.
goto stop

:have_python
"%PY%" "%~dp0install.py"
if errorlevel 2 (
  echo.
  pause
  goto start_app
)
if errorlevel 1 goto stop

:start_app
if "%~1"=="--check" exit /b 0
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" (
  start "" "%PYW%" "%~dp0gui.py"
) else (
  start "" "%PY%" "%~dp0gui.py"
)
exit /b 0

:no_python
echo Without Python the app can't start.
goto stop

:stop
echo.
pause
exit /b 1


rem ------------------------------------------------------------ helpers

:find_python
rem PY = the full path of a Python 3.11 or newer. The Microsoft Store's stand-in "python" prints nothing, so it is skipped.
set "PY="
for %%c in (python py) do call :try_python %%c
if defined PY exit /b 0
for /d %%d in ("%LOCALAPPDATA%\Programs\Python\Python3*") do call :try_python "%%d\python.exe"
for /d %%d in ("%ProgramFiles%\Python3*") do call :try_python "%%d\python.exe"
exit /b 0

:try_python
rem A Python that is new enough writes down where it is.
if defined PY exit /b 0
set "FOUND=%TEMP%\live-kins-python.txt"
del "%FOUND%" >nul 2>&1
%1 -c "import os, sys; sys.version_info >= (3, 11) and open(os.path.join(os.environ['TEMP'], 'live-kins-python.txt'), 'w').write(sys.executable)" >nul 2>&1
if exist "%FOUND%" set /p PY=<"%FOUND%"
del "%FOUND%" >nul 2>&1
exit /b 0

:install_with_winget
where winget >nul 2>&1
if errorlevel 1 exit /b 1
echo.
echo Installing Python...
winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
exit /b 0

:install_from_python_org
echo.
echo Downloading Python from python.org...
set "SETUP=%TEMP%\python-3.12-setup.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile '%SETUP%'"
if not exist "%SETUP%" exit /b 1
echo Installing Python...
"%SETUP%" /passive InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_tcltk=1 Include_pip=1
del "%SETUP%" >nul 2>&1
exit /b 0
