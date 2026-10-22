# -*- coding: utf-8 -*-
"""
glasses_adapter.py —— 智能眼镜适配器（接入 device_adapters 统一契约）

【这是什么】
  智能眼镜（ESP32-S3）通过 WiFi/4G 连上眼镜 AI 中枢（glasses/ai_center.py），
  开机即 POST /device/register 注册、每 30 秒心跳。本适配器负责让 FairyX
  「发现」眼镜：调中枢 GET /devices，只把【在线】的眼镜报上来。

【铁律（强扩展，全设备统一）】
  连接上了才显示、才播报；连不上 = 不出现。discover() 只返回 online=True 的设备。

【配置】
  眼镜中枢地址优先级：环境变量 FAIRY_GLASSES_CENTER > 默认 http://127.0.0.1:5000
  （中枢可以跑在本机，也可以跑在云服务器，填 IP 即可）

【契约】
  caps() / discover() / state() / act() 四件套，注册进 device_adapters.ADAPTERS。
"""
import os
import json
import urllib.request


class GlassesAdapter:
    """智能眼镜适配器：通过眼镜 AI 中枢的 HTTP 接口发现/查询眼镜。"""

    name = "glasses"
    protocol = "智能眼镜（HTTP AI 中枢）"
    needs_account = False
    needs_pairing = False

    DEFAULT_CENTER = "http://127.0.0.1:5000"

    def __init__(self, center=None):
        self.center = (center or os.environ.get("FAIRY_GLASSES_CENTER") or
                       self.DEFAULT_CENTER).rstrip("/")

    # ---- 契约四件套 ----

    def caps(self):
        return {
            "protocol": self.protocol,
            "actions": ["devices", "status"],
            "needs_account": self.needs_account,
            "needs_pairing": self.needs_pairing,
            "discovery": "http",
            "center": self.center,
            "note": "眼镜在线才上报（离线不出现在设备池，符合强扩展铁律）",
        }

    def _get(self, path, timeout=6):
        with urllib.request.urlopen(self.center + path, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def discover(self):
        """查中枢设备表，只返回【在线】的眼镜。连不上中枢 = 没有设备。"""
        try:
            j = self._get("/devices")
        except Exception:
            return []
        out = []
        for d in (j.get("devices") or []):
            if not d.get("online"):
                continue
            out.append({
                "id": "glasses:" + str(d.get("device_id") or ""),
                "name": d.get("name") or "智能眼镜",
                "kind": "glasses",
                "ip": d.get("ip", ""),
                "type": d.get("type", "glasses"),
                "info": d.get("info", ""),
                "online": True,
            })
        return out

    def state(self, dev_id):
        """读一台眼镜的状态（在线/详情/最后心跳）。"""
        try:
            j = self._get("/devices")
        except Exception as e:
            return {"ok": False, "error": "连不上眼镜中枢: %s" % str(e)[:80]}
        did = str(dev_id).split(":", 1)[-1]
        for d in (j.get("devices") or []):
            if d.get("device_id") == did:
                return {"ok": True, "result": d}
        return {"ok": False, "error": "没有这台眼镜: %s" % dev_id}

    def act(self, dev_id, action, params=None):
        """执行动作。目前只读（查询）；写指令等眼镜端接收通道就绪后再开。"""
        if action == "devices":
            return {"ok": True, "result": self.discover()}
        if action == "status":
            return self.state(dev_id)
        return {"ok": False,
                "error": "支持 devices / status（写指令待眼镜端接收通道就绪）"}


if __name__ == "__main__":
    import sys
    a = GlassesAdapter()
    if "--caps" in sys.argv:
        print(json.dumps(a.caps(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(a.discover(), ensure_ascii=False, indent=1))
