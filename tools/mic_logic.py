# -*- coding: utf-8 -*-
# 麦克风接入提示的判断层：kind 分类 / 说什么 / 该不该说（纯 Python 版）
#
# 调用方：mic_watch.py（用 importlib 按路径加载为 mod，然后调 该不该提示 / 该提示什么）
#
# kind 码约定（由 mic_watch.py 的字符串分类得出，CNSH 没有字符串操作所以那样切）：
#   1 板载 3.5mm 麦 · 2 USB 麦 · 3 蓝牙免提 · 4 无线接收器 · 5 阵列麦 · 9 回环/镜像 · 0 未知


def 是真麦克风(kind):
    """kind 1~5 才算真麦克风（9=回环、0=未知 都不算）。"""
    if kind >= 1:
        if kind <= 5:
            return True
    return False


def 是回环(kind):
    """9 = 回环/镜像设备（立体声混音、Sound Mapper、Nahimic 镜像等）。

    ⚠ 这类设备【能打开】但会把自己的声音录回去 -> 会变成自我对话，必须拒绝。
    """
    if kind == 9:
        return True
    return False


def 该提示什么(kind, 是新增):
    """按 kind 与「新增/移除」返回播报文案。

    注意：回环(kind=9) 返回【空串】，调用方据此静默跳过（与原版一致）。
    """
    if 是新增:
        if kind == 3:
            return '蓝牙耳机连上了，我能听见你说话了'
        if kind == 2:
            return '检测到USB麦克风，我能听见你了'
        if kind == 1:
            return '检测到麦克风，我能听见你了'
        if kind == 4:
            return '检测到无线麦克风接收器'
        if kind == 5:
            return '检测到麦克风阵列'
        if kind == 9:
            return ''
        return '检测到一个新的音频输入设备'
    if kind == 3:
        return '蓝牙耳机断开了'
    if kind == 2:
        return 'USB麦克风已拔掉'
    if kind == 9:
        return ''
    return '有个音频输入设备断开了'


def 该不该提示(启动后秒数, kind, 距上次秒数):
    """总闸：启动 10 秒内不提示 / 回环不提示 / 非真麦克风不提示 / 60 秒内不重复。"""
    if 启动后秒数 < 10:
        return False
    if 是回环(kind):
        return False
    if 是真麦克风(kind) == False:
        return False
    if 距上次秒数 < 60:
        return False
    return True
