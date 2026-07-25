@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Import Previous Ember Character Builder Workspace

echo ========================================================
echo  IMPORT PREVIOUS EMBER PROJECTS INTO GUIDED V2
echo ========================================================
echo.
echo This preserves original GLB files and project history.
echo Previous automatic rigs and compiled outputs are not trusted.
echo Every imported project must be guided and rebuilt in V2.
echo.

set "SOURCE=%~1"
if not defined SOURCE set /p "SOURCE=Paste the previous tool folder or workspace folder: "
set "SOURCE=%SOURCE:"=%"
if exist "%SOURCE%\workspace\projects" set "SOURCE=%SOURCE%\workspace"
if not exist "%SOURCE%\projects" (
  echo [FAIL] No projects folder was found at %SOURCE%
  pause
  exit /b 1
)

if not exist "%~dp0workspace\projects" mkdir "%~dp0workspace\projects"
robocopy "%SOURCE%\projects" "%~dp0workspace\projects" /E /COPY:DAT /DCOPY:DAT /R:1 /W:1 /NFL /NDL /NJH /NJS
set "ROBO=%ERRORLEVEL%"
if %ROBO% GEQ 8 (
  echo [FAIL] Project copy failed with robocopy code %ROBO%.
  pause
  exit /b %ROBO%
)
if exist "%SOURCE%\toolchain.json" copy /Y "%SOURCE%\toolchain.json" "%~dp0workspace\toolchain.json" >nul

echo.
echo [PASS] Previous projects copied.
echo Open each project, assign and lock landmarks, then run a new V2 build.
pause
exit /b 0
