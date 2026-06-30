@echo off
REM Start the Horizon Chamber FastAPI server via uvicorn.
REM
REM Usage:
REM     server.bat                    start on port 8001
REM     server.bat --port 9000        start on a custom port
REM     server.bat --reload           enable auto-reload on file changes
REM     server.bat --host 0.0.0.0     listen on all interfaces
REM
REM Any extra arguments are forwarded directly to uvicorn.

cd /d "%~dp0"

set PORT=8001

REM Scan for --port argument
set "ARGS="
:parse
if "%~1"=="--port" (
    set PORT=%~2
    shift
    shift
    goto :parse
)
if "%~1"=="" goto :run
set "ARGS=%ARGS% %1"
shift
goto :parse

:run

echo ============================================
echo  Starting Horizon Chamber server
echo ============================================
echo  Port : %PORT%
echo  URL  : http://127.0.0.1:%PORT%
echo ============================================
echo.

uvicorn main:app --host 127.0.0.1 --port %PORT% %ARGS%

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Server stopped with code %ERRORLEVEL%
    pause
    exit /b %ERRORLEVEL%
)
