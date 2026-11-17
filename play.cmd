@echo off
rem Drag a .mid file onto this play.cmd to play it.
rem CLI:  play.cmd "D:\music\xxx.mid" --duration 10
setlocal
if "%~1"=="" (
    echo.
    echo   Usage: drag a .mid file onto play.cmd
    echo          or:  play.cmd "path\file.mid" [--duration N] [--blocksize 128]
    echo.
    python "%~dp0rtplayer.py" --help
    pause
    exit /b 1
)
python "%~dp0rtplayer.py" %*
if errorlevel 1 pause
exit /b %ERRORLEVEL%
