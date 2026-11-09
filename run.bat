@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0run.py" %*
) else (
    python "%~dp0run.py" %*
)
if errorlevel 1 (
    echo.
    echo [DiffScope] 启动未成功，请查看上方提示。
    pause
)
endlocal
