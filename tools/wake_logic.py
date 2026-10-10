# -*- coding: utf-8 -*-


def 该唤醒(含候选, 编辑距离, 距上次秒, 冷却秒, 文本长度):
    if 距上次秒 < 冷却秒:
        return False
    if 含候选 == True:
        return True
    if 编辑距离 <= 1:
        if 文本长度 <= 12:
            return True
    return False

def 应答序号(随机数, 上一条, 池大小):
    if 池大小 <= 0:
        return 0
    if 池大小 <= 1:
        return 0
    if 随机数 >= 池大小:
        return 0
    if 随机数 == 上一条:
        return (((随机数 + 1) - 池大小) + 池大小)
    return 随机数

def 该提示设备(是真麦克风, 是回环, 距上次秒, 冷却秒):
    if 是回环 == True:
        return False
    if 是真麦克风 == False:
        return False
    if 距上次秒 < 冷却秒:
        return False
    return True

def 声音够大(峰值千分比, 门限千分比):
    if 峰值千分比 < 门限千分比:
        return False
    return True

def 设备得分(类型分, 是系统默认):
    if 类型分 <= 0:
        return 0
    if 是系统默认 == True:
        return 类型分 + 1
    return 类型分
