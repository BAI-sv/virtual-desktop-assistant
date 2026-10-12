# -*- coding: utf-8 -*-
"""FairyX 智能家居控制
=====================
语音"打开灯 / 关空调 / 调亮 / 开窗帘" -> 识别目标+动作 -> 控制后端执行。
后端优先顺序：
  ① ADB 桥（雷电模拟器智慧生活 App，免重启即用；华为生态设备）
     自动检测：雷电模拟器 adb + 智慧生活 App，设备列表页直接点快捷开关
  ② Home Assistant（通用中枢，支持米家/涂鸦/HomeKit/ESPHome 等 1000+ 生态）
     配置：config.json 的 smart_home.ha = {"url": "http://192.168.x.x:8123", "token": "..."}
  ③ 米家 miio 局域网直控（python-miio；需设备 token）
     配置：config.json 的 smart_home.miio = {"设备名": {"ip": "192.168.x.x", "token": "...", "type": "plug|light|fan|air"}}
  ④ 预留：涂鸦 Local / HomeKit / 红外（后续按需接入）

未接入任何桥时：发现设备照常播报，控制返回"暂未接入控制桥"。
"""
import json
import os
import re
import subprocess
import time

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(D, "config.json")

# ---- ADB 桥（雷电模拟器 + 华为智慧生活）----
ADB_EXE = r"D:\雷电\LDPlayer9\adb.exe"
ADB_SERIAL = "emulator-5554"
ADB_PKG = "com.huawei.smarthome"

# 常见智能家居目标（语音匹配用）
TARGETS = [
    ("灯", ["灯", "台灯", "吸顶灯", "筒灯", "射灯", "床头灯", "落地灯"]),
    ("空调", ["空调", "中央空调"]),
    ("风扇", ["风扇", "电扇", "循环扇"]),
    ("窗帘", ["窗帘", "窗帘电机"]),
    ("插座", ["插座", "插排", "排插"]),
    ("扫地机", ["扫地机", "扫地机器人", "扫拖机"]),
    ("电视", ["电视", "电视机"]),
    ("热水器", ["热水器", "电热水器"]),
    ("加湿器", ["加湿器"]),
    ("净化器", ["净化器", "空气净化器"]),
    ("门锁", ["门锁", "智能门锁"]),
    ("晾衣架", ["晾衣架", "智能晾衣"]),
    ("摄像头", ["摄像头", "监控"]),
]
# 动作 -> 后端指令
ACTS = {
    "开": "on", "打开": "on", "开启": "on", "启动": "on",
    "关": "off", "关闭": "off", "关掉": "off", "关灯": "off",
    "调亮": "bright", "调暗": "dim",
    "调高": "up", "调低": "down",
    "切换": "toggle", "换个": "toggle",
    "暂停": "pause", "继续": "resume",
}


def _cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def scan_bridges():
    """扫描已配置/在线控制桥，返回可播报状态。"""
    out = []
    sm = (_cfg().get("smart_home") or {})
    ha = sm.get("ha") or {}
    if ha.get("url"):
        out.append("已接入 Home Assistant")
    mi = sm.get("miio") or {}
    if mi:
        out.append("已接入米家设备 %d 个" % len(mi))
    if not out:
        out.append("暂未接入智能家居控制桥")
    return "，".join(out)


def _pick_target(text):
    """识别目标设备；返回 (目标中文名, 关键词)。"""
    for _cat, _kws in TARGETS:
        for _k in _kws:
            if _k in text:
                return _cat, _k
    return None, None


def _pick_action(text):
    """识别动作；返回 (动作key, 动作词)。动作词优先长匹配。"""
    _best = None
    for _a in sorted(ACTS, key=len, reverse=True):
        if _a in text:
            return ACTS[_a], _a
    return None, None


def _ha_control(domain, entity, action):
    """通过 Home Assistant REST 控制。action: on/off/toggle/..."""
    ha = (_cfg().get("smart_home") or {}).get("ha") or {}
    url = (ha.get("url") or "").rstrip("/")
    token = ha.get("token") or ""
    if not url:
        return None
    svc = "%s_turn_on" if action in ("on",) else (
        "%s_turn_off" if action in ("off",) else "%s_toggle")
    svc = svc % domain
    import urllib.request
    req = urllib.request.Request(
        url + "/api/services/%s/%s" % (domain, svc),
        data=json.dumps({"entity_id": entity}).encode("utf-8"),
        headers={"Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return "已控制 %s" % entity if r.status == 200 else None
    except Exception:
        return None


def _miio_control(name, ip, token, kind, action):
    """通过 python-miio 局域网直控米家设备。"""
    try:
        if kind == "light":
            from miio import Yeelight  # noqa
        elif kind == "fan":
            from miio import Fan
        elif kind == "air":
            from miio import AirConditionerMiot
        else:
            from miio import Plug
    except Exception:
        return None
    try:
        if action in ("on", "off"):
            if kind == "light":
                from miio import Yeelight
                dev = Yeelight(ip, token)
                (dev.on if action == "on" else dev.off)()
            elif kind == "fan":
                dev = Fan(ip, token)
                (dev.on if action == "on" else dev.off)()
            elif kind == "air":
                dev = AirConditionerMiot(ip, token)
                (dev.on if action == "on" else dev.off)()
            else:
                dev = Plug(ip, token)
                (dev.on if action == "on" else dev.off)()
        return "已控制 %s" % name
    except Exception as e:
        return None


def _adb_run(*args):
    """执行雷电 adb 命令，返回文本输出。"""
    try:
        r = subprocess.run([ADB_EXE, "-s", ADB_SERIAL] + list(args),
                           capture_output=True, timeout=30)
        return (r.stdout or b"").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _adb_online():
    """雷电模拟器是否在线。"""
    try:
        return ADB_SERIAL in _adb_run("devices")
    except Exception:
        return False


def _adb_launch():
    """启动华为智慧生活 App。"""
    _adb_run("shell", "monkey", "-p", ADB_PKG,
             "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)


def _adb_dump():
    """uiautomator dump 当前界面，返回 XML 文本。"""
    _adb_run("shell", "uiautomator", "dump", "/sdcard/fairy_ui.xml")
    time.sleep(1)
    import tempfile
    _loc = os.path.join(tempfile.gettempdir(), "fairy_ui.xml")
    _adb_run("pull", "/sdcard/fairy_ui.xml", _loc)
    try:
        with open(_loc, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _adb_tap(x, y):
    _adb_run("shell", "input", "tap", str(int(x)), str(int(y)))
    time.sleep(1.5)


def _parse_bounds(b):
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1, y1, x2, y2)


def _bbox_contains(bb, x, y):
    return bb and bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3]


def _parse_devices(xml):
    """解析智慧生活设备页：返回 [{name,status,btn_on,btn_off}]。"""
    try:
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml)
    except Exception:
        return []
    name_nodes, card_cands, sw_nodes = [], [], []
    for n in root.iter("node"):
        rid = n.get("resource-id") or ""
        if rid == "com.huawei.smarthome:id/smarthome_devices_1_listitem_name":
            name_nodes.append(n)
        if n.get("class") == "android.view.ViewGroup" and n.get("clickable") == "true":
            card_cands.append(n)
        if rid == "com.huawei.smarthome:id/smarthome_devices_1_listitem_ligth_status":
            sw_nodes.append(n)
    devs = []
    for n in name_nodes:
        nm = (n.get("text") or "").strip()
        if not nm:
            continue
        nb = _parse_bounds(n.get("bounds"))
        if not nb:
            continue
        cx, cy = (nb[0] + nb[2]) // 2, (nb[1] + nb[3]) // 2
        card = None
        card_area = None
        for c in card_cands:
            cb = _parse_bounds(c.get("bounds"))
            if _bbox_contains(cb, cx, cy):
                _area = (cb[2] - cb[0]) * (cb[3] - cb[1])
                if card_area is None or _area < card_area:
                    card, card_area = c, _area
        btn_on = btn_off = None
        if card is not None:
            cb = _parse_bounds(card.get("bounds"))
            for s in sw_nodes:
                sb = _parse_bounds(s.get("bounds"))
                if not sb:
                    continue
                scx, scy = (sb[0] + sb[2]) // 2, (sb[1] + sb[3]) // 2
                if not _bbox_contains(cb, scx, scy):
                    continue
                for q in s.iter("node"):
                    cd = q.get("content-desc") or ""
                    if cd in ("开启", "关闭"):
                        qb = _parse_bounds(q.get("bounds"))
                        qc = ((qb[0] + qb[2]) // 2, (qb[1] + qb[3]) // 2) if qb else (scx, scy)
                        if cd == "开启":
                            btn_on = qc
                        else:
                            btn_off = qc
        st = "on" if btn_off else ("off" if btn_on else "")
        devs.append({"name": nm, "status": st, "btn_on": btn_on, "btn_off": btn_off})
    return devs


def _adb_ensure_devices_page():
    """启动智慧生活并确保停在设备列表页，返回 XML。"""
    _adb_launch()
    _xml = _adb_dump()
    if _xml and "smarthome_devices_1_listitem_name" not in _xml:
        m = re.search(r'text="家居"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', _xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            _adb_tap((x1 + x2) // 2, (y1 + y2) // 2)
            time.sleep(2)
            _xml = _adb_dump()
    return _xml or ""


def _adb_control(text):
    """通过雷电智慧生活控制设备（快捷开关）。"""
    _a, _aw = _pick_action(text)
    if not _a:
        return None
    _goal = text
    if _aw:
        _goal = _goal.replace(_aw, "", 1).strip()
    _xml = _adb_ensure_devices_page()
    if not _xml:
        return "雷电里的智慧生活没打开成功，检查一下模拟器"
    _devs = _parse_devices(_xml)
    if not _devs:
        return "智慧生活设备页没识别到设备，检查模拟器界面"
    _dev = None
    for _d in _devs:
        if _goal and (_goal in _d["name"] or _d["name"] in _goal):
            _dev = _d
            break
    if not _dev:
        # 目标不是家居设备词（如"微信/QQ"）→ 放行给软件路由
        _SMART_WORDS = ("灯", "空调", "风扇", "窗帘", "插座", "扫地", "电视",
                        "热水器", "加湿器", "净化器", "门锁", "晾衣", "摄像头",
                        "监控", "音箱", "电脑", "打印机", "能耗", "冰箱",
                        "洗衣机", "烤箱", "电饭煲", "开关", "智能")
        if not _goal or not any(_w in _goal for _w in _SMART_WORDS):
            return None
        _names = "、".join(d["name"] for d in _devs[:5])
        return "设备列表里没找到「%s」，现在能看到：%s" % (_goal or "目标设备", _names)
    _want_on = (_a == "on")
    _btn = _dev.get("btn_on") if _want_on else _dev.get("btn_off")
    if not _btn:
        if _dev.get("status") == ("off" if _want_on else "on"):
            return _dev["name"] + (" 已经关了" if not _want_on else " 已经开着")
        return _dev["name"] + " 没有快捷开关，进设备页手动控制吧"
    _adb_tap(*_btn)
    return "已%s %s" % ("打开" if _want_on else "关闭", _dev["name"])


def control(text):
    """语音控制入口。返回播报文本。"""
    # ① ADB 桥优先（雷电智慧生活：音箱/电脑/摄像头等真实设备名）
    try:
        if _adb_online():
            _r = _adb_control(text)
            if _r:
                return _r
    except Exception:
        pass
    _t, _kw = _pick_target(text)
    if not _t:
        return None  # 不是智能家居控制目标，交给其他路由
    _a, _aw = _pick_action(text)
    if not _a:
        return "请说清楚要开还是要关，比如：打开" + _kw
    sm = (_cfg().get("smart_home") or {})
    # ① Home Assistant：配置了就直接按实体名控制
    ha = sm.get("ha") or {}
    if ha.get("url"):
        ent = ha.get("entities") or {}
        _eid = ent.get(_t) or ent.get(_kw)
        if _eid:
            _r = _ha_control("light" if _t == "灯" else (
                "climate" if _t == "空调" else "switch"), _eid, _a)
            if _r:
                return _r
    # ② 米家 miio 直控
    mi = sm.get("miio") or {}
    for _nm, _info in mi.items():
        if _kw in _nm or _t in _nm:
            _r = _miio_control(_nm, _info.get("ip", ""),
                               _info.get("token", ""),
                               _info.get("type", "plug"), _a)
            if _r:
                return _r
    return "已发现%s设备，但还没接入控制桥。可以在 config.json 里配 smart_home（米家设备或 Home Assistant），我就帮你控制。" % _t


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(control(" ".join(sys.argv[1:])))
    else:
        print(scan_bridges())
