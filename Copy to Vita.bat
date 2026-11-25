@echo off
rem Copy the game you built to your Vita, over Wi-Fi or a USB cable. Run "HLD Vita Builder.bat" first.
setlocal
cd /d "%~dp0"
if not exist "kit.json" (
  echo This is the launcher template from the source repository, not the builder kit.
  echo Use the HLD-Vita-Builder zip from the project's Releases page.
  pause
  exit /b 1
)
set "PY="
if exist "deps\venv\Scripts\python.exe" set "PY=deps\venv\Scripts\python.exe"
if not defined PY if exist "deps\python\python.exe" set "PY=deps\python\python.exe"
if not defined PY (
  for %%P in (py python) do (
    if not defined PY (
      %%P -c "import sys; sys.exit(sys.version_info < (3, 8))" >nul 2>&1 && set "PY=%%P"
    )
  )
)
if not defined PY (
  echo Run "HLD Vita Builder.bat" first.
  pause
  exit /b 1
)
echo Wi-Fi: on the Vita open VitaShell, press SELECT to start FTP, and type the IP address it shows.
echo USB:   on the Vita open VitaShell, press START, set "USB device" to Memory Card, go back,
echo        press SELECT and connect the cable. Then type usb.
echo.
set "TARGET="
set /p "TARGET=IP address or usb: "
if not defined TARGET exit /b 0
if /i "%TARGET%"=="usb" (
  "%PY%" scripts\install.py --usb
) else (
  "%PY%" scripts\install.py %TARGET%
)
echo.
pause
