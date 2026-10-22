@echo off
rem FairyX 开机自启：拉起 WSL 里的 Home Assistant（常驻会话）
wsl -d Ubuntu -u root -- bash -c "source /root/ha_venv/bin/activate && nohup hass --config /root/.homeassistant > /root/ha.log 2>&1 & sleep 31536000"
