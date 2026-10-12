#!/bin/bash
# ============================================================
# FairyX × Home Assistant 一键部署脚本（WSL Ubuntu 内运行）
# 用法（Windows 管理员 PowerShell 里执行）：
#   wsl -d Ubuntu -u root -- bash /mnt/d/FAIRY/tools/ha_setup/ha_setup.sh
# 或进入 Ubuntu 后： bash /mnt/d/FAIRY/tools/ha_setup/ha_setup.sh
# 说明：装 HA Core（官方支持的 Linux 方式），然后由 Fairy 的
#       smart_home.py 通过 REST API 控制（配置见 config.json）。
# ============================================================
set -e

echo "==> [1/6] 更新软件源并安装基础依赖"
apt-get update -y
apt-get install -y python3 python3-venv python3-pip curl git ca-certificates

echo "==> [2/6] 创建 HA 虚拟环境（~/ha_venv）"
cd /root
python3 -m venv ~/ha_venv
# shellcheck disable=SC1091
source ~/ha_venv/bin/activate
pip install --upgrade pip wheel setuptools

echo "==> [3/6] 安装 Home Assistant Core（依赖较多，约 5-15 分钟）"
pip install homeassistant

echo "==> [4/6] 首次启动初始化配置目录（生成 configuration.yaml）"
# 首次启动会自动创建 ~/.homeassistant/configuration.yaml
mkdir -p ~/.homeassistant

echo "==> [5/6] 写入最小配置（允许 Fairy 的 REST 调用）"
cat > ~/.homeassistant/configuration.yaml <<'EOF'
# Home Assistant 基础配置 —— 由 FairyX 部署脚本生成
homeassistant:
  name: FairyHome
  latitude: 35.76
  longitude: 115.03
  elevation: 50
  unit_system: metric
  time_zone: Asia/Shanghai
  currency: CNY

# 基本组件
default_config:

# 日志与 API（Fairy 通过 /api 控制设备）
logger:
  default: info

# 可选：先空着，等用户装好各生态集成后自动发现
# 集成安装提示：
#   eWeLink(易微联) -> HACS 搜 ewelink
#   海尔智家       -> HACS 搜 haier
#   华为智慧生活   -> HACS 搜 huawei（社区集成，按需使用）
EOF

echo "==> [6/6] 生成启动脚本（重启后自动拉起 HA）"
cat > /root/start_ha.sh <<'EOF'
#!/bin/bash
# 启动 Home Assistant（在 WSL 里后台运行）
source /root/ha_venv/bin/activate
cd /root
nohup hass --config /root/.homeassistant > /root/ha.log 2>&1 &
echo "HA 已启动，日志：/root/ha.log  网页：http://localhost:8123"
EOF
chmod +x /root/start_ha.sh

echo ""
echo "============================================================="
echo " ✅ Home Assistant Core 安装完成"
echo " 启动：  bash /root/start_ha.sh"
echo " 网页：  http://localhost:8123（首次打开设置账号）"
echo " 日志：  tail -f /root/ha.log"
echo ""
echo " Fairy 对接（Windows 上改 D:\\FAIRY\\config.json）："
echo '  "smart_home": {'
echo '    "ha": {'
echo '      "url": "http://localhost:8123",'
echo '      "token": "在HA网页 左下角个人资料→长期访问令牌 生成",'
echo '      "entities": {"灯": "light.xxx", "空调": "climate.xxx"}'
echo '    }'
echo '  }'
echo " 然后说：打开灯 / 关空调 / 调亮 —— Fairy 就会调 HA 控制设备"
echo "============================================================="
