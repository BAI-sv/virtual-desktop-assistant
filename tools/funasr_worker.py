# -*- coding: utf-8 -*-
"""
funasr_worker.py —— FunASR GPU 推理 worker（在 D:\\FAIRY\\envs\\funasr 独立环境运行）

由 asr_bridge.py 通过 subprocess 调用；DirectML 让 AMD 显卡（RX 9070 XT）
也能跑 PyTorch。GPU 不可用时自动降级 CPU。

用法：
  D:\\FAIRY\\envs\\funasr\\Scripts\\python.exe funasr_worker.py --file x.wav [--kind paraformer|sensevoice]
输出：{"ok": bool, "text": "...", "load_s": ..., "inf_s": ..., "device": "..."}
"""
import argparse
import json
import os
import re
import sys
import time

try:
    import torch_directml as _dml
    _DEV = _dml.device()
    print("[worker] GPU: %s" % _dml.device_name(0), file=sys.stderr)
except Exception as e:
    _DEV = "cpu"
    print("[worker] GPU 不可用，降级 CPU: %s" % e, file=sys.stderr)

MODELS = r"D:\FAIRY\models\models"
PARA = os.path.join(MODELS,
                    "iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                    "snapshots", "master")
SENSE = os.path.join(MODELS, "iic--SenseVoiceSmall", "snapshots", "master")
VAD = os.path.join(MODELS, "iic--speech_fsmn_vad_zh-cn-16k-common-pytorch",
                   "snapshots", "master")


def main():
    ap = argparse.ArgumentParser(description="FunASR GPU worker")
    ap.add_argument("--file", required=True, help="wav 文件")
    ap.add_argument("--kind", default="paraformer",
                    choices=["paraformer", "sensevoice"])
    a = ap.parse_args()

    from funasr import AutoModel
    t0 = time.time()
    kw = dict(trust_remote_code=True, vad_model=VAD)
    if a.kind == "paraformer":
        kw["model"] = PARA
    else:
        kw["model"] = SENSE
    m = AutoModel(**kw)
    print("[worker] 模型加载 %.1fs" % (time.time() - t0), file=sys.stderr)

    try:
        m.to(_DEV)
        print("[worker] 模型已放 GPU", file=sys.stderr)
    except Exception as e:
        print("[worker] 放 GPU 失败，保持默认设备: %s" % e, file=sys.stderr)

    t1 = time.time()
    res = m.generate(input=a.file, cache={})
    text = (res[0].get("text") or "").strip()
    if a.kind == "sensevoice":
        text = re.sub(r"<\|[^|]*\|>", "", text).strip()
    out = {"ok": bool(text), "text": text,
           "load_s": round(t1 - t0, 1), "inf_s": round(time.time() - t1, 1),
           "device": str(_DEV)}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
