@echo off
rem ============================================================
rem  关机前检查：抓一次 CPU 快照 + 跑一次快速健康检查
rem  用 PATH 里的 pythonw，不绑定本机绝对路径
rem ============================================================
setlocal

set PY=pythonw
where pythonw >nul 2>&1
if errorlevel 1 set PY=python

if exist "%~dp0cpu_watch.py" start "" /b %PY% "%~dp0cpu_watch.py"
if exist "%~dp0health_check.py" start "" /b %PY% "%~dp0health_check.py" --quiet --fast
