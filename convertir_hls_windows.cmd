@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%convertir_hls_windows.exe" (
  if "%~1"=="" (
    "%SCRIPT_DIR%convertir_hls_windows.exe" "%SCRIPT_DIR%"
  ) else (
    "%SCRIPT_DIR%convertir_hls_windows.exe" %*
  )
) else (
  if "%~1"=="" (
    py -3 "%SCRIPT_DIR%convertir_hls.py" "%SCRIPT_DIR%"
  ) else (
    py -3 "%SCRIPT_DIR%convertir_hls.py" %*
  )
)

if errorlevel 1 pause
endlocal
