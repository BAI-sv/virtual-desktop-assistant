@echo off
rem ============================================================
rem  FairyX 启动脚本
rem  用系统 PATH 里的 pythonw 启动，不绑定任何本机绝对路径
rem ============================================================
setlocal

where py >nul 2>&1
if not errorlevel 1 (
    start "" /b py -3.12 "%~dp0tools\fairy.py" start
    goto :eof
)

where pythonw >nul 2>&1
if not errorlevel 1 (
    start "" /b pythonw "%~dp0tools\fairy.py" start
    goto :eof
)

where python >nul 2>&1
if not errorlevel 1 (
    start "" /b python "%~dp0tools\fairy.py" start
    goto :eof
)

echo [错误] 没有找到 Python。请先装 Python 3.10+ 并勾选 Add to PATH。
echo 下载: https://www.python.org/downloads/
pause
exit /b 1
