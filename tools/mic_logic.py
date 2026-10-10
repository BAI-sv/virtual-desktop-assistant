# -*- coding: utf-8 -*-


def 是真麦克风(kind):
    if kind >= 1:
        if kind <= 5:
            return True
    return False

def 是回环(kind):
    if kind == 9:
        return True
    return False

def 该提示什么(kind, 是新增):
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
    if 启动后秒数 < 10:
        return False
    if 是回环(kind):
        return False
    if 是真麦克风(kind) == False:
        return False
    if 距上次秒数 < 60:
        return False
    return True
