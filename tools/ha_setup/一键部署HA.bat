@echo off
chcp 65001 >nul
title FairyX - Home Assistant 一键部署
echo ============================================
echo   FairyX × Home Assistant 一键部署
echo   （需要：已重启 Windows + WSL 生效）
echo ============================================
echo.

rem 检查 WSL 是否有 Ubuntu
wsl --list --quiet 2>nul | findstr /i "Ubuntu" >nul
if errorlevel 1 (
    echo [错误] 未找到 Ubuntu 发行版，请先运行：
    echo     wsl --install -d Ubuntu
    pause
    exit /b 1
)

echo [1/3] 启动 Ubuntu 并运行 HA 部署脚本...
wsl -d Ubuntu -u root -- bash /mnt/d/FAIRY/tools/ha_setup/ha_setup.sh
if errorlevel 1 (
    echo [错误] HA 部署脚本执行失败，看上方日志。
    pause
    exit /b 1
)

echo [2/3] 启动 Home Assistant 服务...
wsl -d Ubuntu -u root -- bash /root/start_ha.sh

echo [3/3] 完成！
echo.
echo   网页：http://localhost:8123
echo   首次打开请按提示创建管理员账号。
echo   创建后在左下角 个人资料 → 长期访问令牌 生成 token，
echo   填进 <项目根>/config.json 的 smart_home.ha 即可。
echo.
pause
