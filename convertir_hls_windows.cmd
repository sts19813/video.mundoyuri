@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%convertir_hls_windows.exe" (
  "%SCRIPT_DIR%convertir_hls_windows.exe" "%SCRIPT_DIR%"
) else (
  py -3 "%SCRIPT_DIR%convertir_hls.py" "%SCRIPT_DIR%"
)

if errorlevel 1 pause
endlocal
