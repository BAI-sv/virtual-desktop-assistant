# -*- coding: utf-8 -*-
"""FairyX 智能家居控制
=====================
语音"打开灯 / 关空调 / 调亮 / 开窗帘" -> 识别目标+动作 -> 控制后端执行。
后端优先顺序：
  ① Home Assistant（通用中枢，支持米家/涂鸦/HomeKit/ESPHome 等 1000+ 生态）
     配置：config.json 的 smart_home.ha = {"url": "http://192.168.x.x:8123", "token": "..."}
  ② 米家 miio 局域网直控（python-miio；需设备 token）
     配置：config.json 的 smart_home.miio = {"设备名": {"ip": "192.168.x.x", "token": "...", "type": "plug|light|fan|air"}}
  ③ 预留：涂鸦 Local / HomeKit / 红外（后续按需接入）
（★ 2026-10-12 已删除 ADB/雷电模拟器桥——不再自动拉起模拟器）

未接入任何桥时：发现设备照常播报，控制返回"暂未接入控制桥"。
"""
import json
import os
import sys

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(D, "config.json")

# 常见智能家居目标（语音匹配用）
TARGETS = [
    ("灯", ["灯", "台灯", "吸顶灯", "筒灯", "射灯", "床头灯", "落地灯"]),
    ("空调", ["空调", "中央空调"]),
    ("风扇", ["风扇", "电扇", "循环扇"]),
    ("窗帘", ["窗帘", "窗帘电机"]),
    ("插座", ["插座", "插排", "排插"]),
    ("扫地机", ["扫地机", "扫地机器人", "扫拖机"]),
    ("电视", ["电视", "电视机"]),
    ("电脑", ["电脑", "计算机", "主机", "pc"]),
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
    svc = ("turn_on" if action in ("on",) else
           ("turn_off" if action in ("off",) else "toggle"))
    # ★ 2026-10-12【修复】HA 服务名是 turn_on/turn_off/toggle（不带域前缀），
    #   之前拼成 light_turn_off 导致 /api/services/light/light_turn_off 404，
    #   HA 控制一直失败。
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


# 可控制的 HA 域
_HA_DOMAINS = ("switch", "light", "fan", "climate", "cover", "media_player")
# SonoffLAN 自动生成的辅助实体（不参与语音匹配）
_HA_SKIP_SUFFIX = ("_pulse", "_led", "_startup", "_pulsewidth", "_restart", "_shutdown")


def _ha_discover():
    """从 Home Assistant 自动发现真实设备实体。
    返回 [(domain, entity_id, 中文名, state)]，按"设备名"排序去重。
    """
    ha = (_cfg().get("smart_home") or {}).get("ha") or {}
    url = (ha.get("url") or "").rstrip("/")
    token = ha.get("token") or ""
    if not url or not token:
        return []
    import urllib.request
    req = urllib.request.Request(url + "/api/states",
                                 headers={"Authorization": "Bearer " + token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            states = json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return []
    out = []
    for s in states:
        eid = s.get("entity_id") or ""
        domain = eid.split(".", 1)[0] if "." in eid else ""
        if domain not in _HA_DOMAINS:
            continue
        low = eid.lower()
        if any(low.endswith(x) for x in _HA_SKIP_SUFFIX):
            continue
        name = (s.get("attributes") or {}).get("friendly_name") or eid
        out.append((domain, eid, str(name).strip(), s.get("state") or ""))
    return out


# 常见房间位置词（语音定位用）
_POS_WORDS = ("卧室", "客厅", "厨房", "书房", "阳台", "卫生间", "洗手间", "次卧", "主卧")


def _ha_find(target, kw, text=""):
    """按语音目标在 HA 发现实体中匹配。
    话里指定了房间位置时只匹配该位置，否则同类全部匹配。
    返回 [(domain, entity_id, 中文名, state)]。
    """
    pos = ""
    for p in _POS_WORDS:
        if p in text:
            pos = p
            break
    hits = []
    for domain, eid, name, state in _ha_discover():
        if target == "灯":
            if "灯" not in name and "light" not in eid:
                continue
        elif not (kw in name or kw in eid):
            continue
        if pos and pos not in name:
            continue
        hits.append((domain, eid, name, state))
    return hits


def _ha_control_all(hits, action):
    """控制一批实体，返回 (成功名串, 离线名串, 失败名串)。"""
    ok, off, fail = [], [], []
    for domain, eid, name, state in hits:
        if state == "unavailable":
            off.append(name)
            continue
        if _ha_control(domain, eid, action):
            ok.append(name)
        else:
            fail.append(name)
    return "、".join(ok), "、".join(off), "、".join(fail)


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


def control(text):
    """语音控制入口。返回播报文本。
    ★ 2026-10-13【默认关闭】config.json 的 smart_home.enabled 为 true 才启用，
    默认 false —— 不连接任何智能家居后端。
    """
    _sm0 = (_cfg().get("smart_home") or {})
    if not _sm0.get("enabled", False):
        return "智能家居未启用（如需使用，在 config.json 的 smart_home 里设置 enabled 为 true）"
    _t0, _kw0 = _pick_target(text)
    if not _t0:
        return None  # 不是智能家居控制目标，交给其他路由
    _a0, _aw0 = _pick_action(text)
    if not _a0:
        return "请说清楚要开还是要关，比如：打开" + _kw0
    _sm = (_cfg().get("smart_home") or {})
    # ① Home Assistant：显式映射优先，其次自动发现真实实体
    _ha = _sm.get("ha") or {}
    if _ha.get("url"):
        _ent = _ha.get("entities") or {}
        _eid = _ent.get(_t0) or _ent.get(_kw0)
        if _eid and not _eid.endswith(".xxx"):
            # 显式映射（config 指定实体）优先
            try:
                _r = _ha_control(_eid.split(".", 1)[0], _eid, _a0)
                if _r:
                    return _r
            except Exception:
                pass
            return "HA 控制%s没成功，检查设备是否在线或实体名是否对" % _kw0
        # 自动发现（SonoffLAN 等集成拉到的真实实体）
        try:
            _hits = _ha_find(_t0, _kw0, text)
            if _hits:
                _okn, _offn, _failn = _ha_control_all(_hits, _a0)
                _verb = "打开" if _a0 == "on" else ("关闭" if _a0 == "off" else "切换")
                _parts = []
                if _okn:
                    _parts.append("已%s%s" % (_verb, _okn))
                if _offn:
                    _parts.append("%s离线没操作" % _offn)
                if _failn:
                    _parts.append("%s控制失败" % _failn)
                if _parts:
                    return "，".join(_parts)
                return "HA 控制%s没成功，检查设备是否在线" % _kw0
        except Exception:
            pass
    # ② 易微联 eWeLink 云直连（账号在 config.json smart_home.ewelink）
    try:
        import ewelink_bridge as _ew
        _ds = _ew.get_devices()
        if _ds:
            _hitd = None
            for _d in _ds:
                _nm = _d.get("name") or ""
                if _kw0 and (_kw0 in _nm or _nm in _kw0):
                    _hitd = _d
                    break
            if not _hitd:
                for _d in _ds:
                    _nm = _d.get("name") or ""
                    if _t0 == "灯" and "灯" in _nm:
                        _hitd = _d
                        break
            if _hitd:
                _want_on = _a0 == "on"
                if _ew.control_device(_hitd["deviceid"], {"switch": _a0}):
                    return "已%s %s" % ("打开" if _want_on else "关闭", _hitd["name"])
                return "易微联控制%s没成功，检查设备是否在线" % _hitd["name"]
    except Exception:
        pass
    # ③ 米家 miio 直控
    mi = _sm.get("miio") or {}
    for _nm, _info in mi.items():
        if _kw0 in _nm or _t0 in _nm:
            _r = _miio_control(_nm, _info.get("ip", ""),
                               _info.get("token", ""),
                               _info.get("type", "plug"), _a0)
            if _r:
                return _r
    return "已发现%s设备，但还没接入控制桥。可以在 config.json 里配 smart_home（Home Assistant 或米家设备），我就帮你控制。" % _t0


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(control(" ".join(sys.argv[1:])))
    else:
        print(scan_bridges())
