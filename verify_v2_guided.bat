@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Ember Guided Character Builder v2 Verification

echo ========================================================
echo  EMBER GUIDED CHARACTER BUILDER V2 SELF TEST
echo ========================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m compileall -q app.py ember_gmod blender self_test.py
  if errorlevel 1 goto :failed
  py -3 self_test.py
  if errorlevel 1 goto :failed
  goto :passed
)

where python >nul 2>nul
if %errorlevel%==0 (
  python -m compileall -q app.py ember_gmod blender self_test.py
  if errorlevel 1 goto :failed
  python self_test.py
  if errorlevel 1 goto :failed
  goto :passed
)

echo [FAIL] Python 3 was not found.
goto :failed

:passed
echo.
echo [PASS] Guided projects, landmark locking, large GLB preservation,
echo        strict TGA generation, native VTF enforcement and install layout passed.
pause
exit /b 0

:failed
echo.
echo [FAIL] One or more V2 checks failed.
pause
exit /b 1
