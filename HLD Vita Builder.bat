@echo off
rem HLD Vita Builder (Windows 10/11). Uses Python 3.12+ if installed, otherwise downloads a pinned standalone Python
rem (python-build-standalone 3.12.16, checksummed) into deps\python. Then runs scripts\kit_build.py.
setlocal
cd /d "%~dp0"
if not exist "kit.json" (
  echo This is the launcher template from the source repository, not the builder kit.
  echo Use the HLD-Vita-Builder zip from the project's Releases page.
  pause
  exit /b 1
)
set "PY="
if exist "deps\python\python.exe" set "PY=deps\python\python.exe"
if not defined PY (
  for %%P in (py python) do (
    if not defined PY (
      %%P -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>&1 && set "PY=%%P"
    )
  )
)
if not defined PY (
  echo Downloading Python 3.12 ^(first run only^)...
  if not exist deps\downloads mkdir deps\downloads
  set "F=cpython-3.12.16+20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
  curl.exe -fL -o "deps\downloads\python.tar.gz" "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%%2B20261026-x86_64-pc-windows-msvc-install_only.tar.gz" || goto :fail
  certutil -hashfile "deps\downloads\python.tar.gz" SHA256 | findstr /i "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5" >nul || (echo Checksum mismatch for the Python download & del "deps\downloads\python.tar.gz" & goto :fail)
  tar -xzf "deps\downloads\python.tar.gz" -C deps || goto :fail
  set "PY=deps\python\python.exe"
)
"%PY%" scripts\kit_build.py %*
echo.
pause
exit /b %errorlevel%
:fail
echo.
echo Setup failed.
pause
exit /b 1
