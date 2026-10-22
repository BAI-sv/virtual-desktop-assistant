@echo off
rem ============================================================
rem  FairyX 安装入口
rem
rem  真正干活的安装器是 Fairy.exe（图形界面 + 实时进度条）。
rem  这个 bat 只是给人一个不用找文件的入口。
rem ============================================================
title FairyX 安装
chcp 65001 >nul 2>&1

echo ============================================
echo    FairyX 依赖安装
echo ============================================
echo.

if exist "%~dp0Fairy.exe" (
    echo 正在启动图形安装器...
    start "" "%~dp0Fairy.exe"
    goto :eof
)

if exist "%~dp0dist\Fairy.exe" (
    echo 正在启动图形安装器...
    start "" "%~dp0dist\Fairy.exe"
    goto :eof
)

echo [提示] 没有找到 Fairy.exe，改用命令行方式检测依赖。
echo         图形安装器可以单独打包：见 README 的「打包」一节。
echo.

rem ---------- 退回到命令行：检测 -> 安装 ----------
py -3.12 --version >nul 2>&1
if errorlevel 1 goto :nopython

echo [1/2] Python 3.12 已就绪
echo [2/2] 安装依赖（首次 2~5 分钟，清华镜像）
echo.

py -3.12 -m pip install -r "%~dp0requirements.txt" ^
    -i https://pypi.tuna.tsinghua.edu.cn/simple ^
    --disable-pip-version-check
if errorlevel 1 (
    echo.
    echo [警告] 清华源失败，改用阿里源重试...
    py -3.12 -m pip install -r "%~dp0requirements.txt" ^
        -i https://mirrors.aliyun.com/pypi/simple ^
        --trusted-host mirrors.aliyun.com ^
        --disable-pip-version-check
)

echo.
echo 安装完成。双击「启动FairyX.bat」即可运行。
pause
goto :eof

:nopython
echo [错误] 没有找到 Python 3.12。
echo 请先安装：https://www.python.org/downloads/
echo 安装时务必勾选 "Add python.exe to PATH"。
pause
exit /b 1
