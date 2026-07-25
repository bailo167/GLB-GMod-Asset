@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Ember Guided GMod Character Builder v2
set "PYTHONUTF8=1"

echo ========================================================
echo  EMBER GUIDED GMOD CHARACTER BUILDER V2
echo ========================================================
echo  GLB  ^>  LANDMARKS  ^>  VALVEBIPED  ^>  GMOD
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 app.py
  set "EXIT_CODE=%ERRORLEVEL%"
  goto :finish
)

where python >nul 2>nul
if %errorlevel%==0 (
  python app.py
  set "EXIT_CODE=%ERRORLEVEL%"
  goto :finish
)

echo [FAIL] Python 3.10 or newer was not found.
echo Install 64 bit Python for Windows and enable Add Python to PATH.
set "EXIT_CODE=1"

:finish
if not "%EXIT_CODE%"=="0" (
  echo.
  echo Ember stopped with exit code %EXIT_CODE%.
  pause
)
exit /b %EXIT_CODE%
