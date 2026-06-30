@echo off
REM Build Horizon Chamber as a standalone Windows executable.
REM
REM Usage:
REM     build.bat                    build folder executable
REM     build.bat --onefile          build single-file executable
REM     build.bat --debug            keep console window for debugging
REM     build.bat --onefile --debug  combine flags
REM
REM Requires: pyinstaller (pip install pyinstaller)

cd /d "%~dp0"

echo ============================================
echo  Building Horizon Chamber
echo ============================================

python build.py %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Build failed with code %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)

echo.
echo Done. Executable is in the dist\ folder.
pause
