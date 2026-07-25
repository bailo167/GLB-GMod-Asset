@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Ember V2 Windows Readiness Check

echo ========================================================
echo  EMBER V2 WINDOWS READINESS CHECK
echo ========================================================
echo.
ver
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  echo [OK] Python launcher found
  py -3 --version
  py -3 -c "import json; from ember_gmod.config import detect_toolchain,tool_status; print(json.dumps(tool_status(detect_toolchain()),indent=2))"
  goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
  echo [OK] python.exe found
  python --version
  python -c "import json; from ember_gmod.config import detect_toolchain,tool_status; print(json.dumps(tool_status(detect_toolchain()),indent=2))"
  goto :done
)

echo [MISSING] Python 3.10 or newer

:done
echo.
echo Blender, StudioMDL, GMad and Garry's Mod paths can also be set inside Toolchain. VTEX is optional and automatic builds do not use it.
pause
