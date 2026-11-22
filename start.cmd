@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
if exist "apps\api\.venv\Scripts\python.exe" (
    "apps\api\.venv\Scripts\python.exe" -X utf8 scripts\launch_windows.py %*
) else (
    py -3.10 -V >nul 2>nul
    if errorlevel 1 (
        where python.exe >nul 2>nul
        if errorlevel 1 (
            echo 未找到 Python。请安装 Python 3.10 并添加到 PATH。
            if "%~1"=="" pause
            exit /b 1
        )
        python -X utf8 scripts\launch_windows.py %*
    ) else (
        py -3.10 -X utf8 scripts\launch_windows.py %*
    )
)
if errorlevel 1 (
    echo.
    echo 启动未完成，请查看上方信息。
    if "%~1"=="" pause
    exit /b 1
)
