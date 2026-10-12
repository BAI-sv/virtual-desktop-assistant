@echo off
rem shutdown_check.bat - quick health check before shutdown (optional autostart)
rem No hard-coded paths: script dir from %~dp0, interpreter found via PATH.
set "TOOLS=%~dp0"
set "PY="
for %%P in (pythonw.exe) do set "PY=%%~$PATH:P"
if not defined PY exit /b 0
"%PY%" "%TOOLS%cpu_watch.py"
"%PY%" "%TOOLS%health_check.py" --quiet --fast
