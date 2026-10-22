# -*- coding: utf-8 -*-
"""fairy_ball 的模块级常量（2026-10-23 抽出）。

抽出来的原因：ball_mixins 里有些方法把常量当默认参数用
（def _clamp(self, x, y, w=WIN_W)），默认参数在类定义时就求值，
所以常量必须比 Mixin 先存在。
"""
import os
import sys

import fairy_root

# SIZE 在 A+B 搬迁时去了 ball_extracted
from ball_extracted import SIZE

__all__ = [
    "POS_FILE",
    "SPEAK_DONE",
    "STRATA_STATUS_URL",
    "STRATA_LOAD_URL",
    "STRATA_UNLOAD_URL",
    "STRATA_LOG",
    "BALANCE_REFRESH_S",
    "VOICE_INDEX",
    "GAP",
    "BAR_W",
    "WIN_W",
    "WIN_H",
    "MARGIN",
    "_EDGE_PX",
    "_PANEL_RINSET",
    "_PANEL_RPAD",
    "_PANEL_FUSE_FILL",
    "SIZE",
]

POS_FILE = fairy_root.BALL_POS
SPEAK_DONE = "主人我已完成既定目标"      # 游戏原声「主人，我已完成既定目标。」（2.2s）
STRATA_STATUS_URL = "http://127.0.0.1:8080/v1/status"
STRATA_LOAD_URL = "http://127.0.0.1:8080/v1/load"
STRATA_UNLOAD_URL = "http://127.0.0.1:8080/v1/unload"
STRATA_LOG = fairy_root.log("strata_server.log")
BALANCE_REFRESH_S = 300      # 每 5 分钟刷一次余额
VOICE_INDEX = fairy_root.VOICE_INDEX
GAP = 24                   # ★ 2026-10-19 四周统一 12px 节奏(底板离球 12 + 内容离底板 12)
BAR_W = 208                # 右侧进度条面板宽度
WIN_W = SIZE + GAP + BAR_W
WIN_H = SIZE + 11
MARGIN = 60                # 离屏幕边缘的安全距离
_EDGE_PX = 40
_PANEL_RINSET = 10        # 内容右边内收（px）：让左右露出的底色一样长
_PANEL_RPAD = 12           # 右对齐文字与底板右缘的间距（px）
_PANEL_FUSE_FILL = True   # ★ 填充段（蓝/红条）是否也跟底板一起渐隐
