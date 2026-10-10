# -*- coding: utf-8 -*-


def 设备类别(是耳机, 是音箱, 是麦克风, 是键盘, 是存储):
    if 是耳机:
        return '耳机'
    if 是音箱:
        return '音箱'
    if 是麦克风:
        return '麦克风'
    if 是键盘:
        return '键鼠'
    if 是存储:
        return '存储设备'
    return '新设备'

def 设备提示(是新增, 是耳机, 是音箱, 是麦克风, 是键盘, 是存储):
    类别 = 设备类别(是耳机, 是音箱, 是麦克风, 是键盘, 是存储)
    if 是新增:
        if 类别 == '耳机':
            return '检测到耳机已接入'
        if 类别 == '音箱':
            return '音箱已就绪'
        if 类别 == '麦克风':
            return '检测到麦克风已接入'
        if 类别 == '键鼠':
            return '检测到新的输入设备'
        if 类别 == '存储设备':
            return '检测到新的存储设备'
        return '检测到新设备接入'
    if 类别 == '耳机':
        return '耳机已断开'
    if 类别 == '音箱':
        return '音箱已断开'
    if 类别 == '麦克风':
        return '麦克风已断开'
    return '有一个设备已断开'

def 该说(距上次秒数, 已经报过):
    if 已经报过:
        return False
    if 距上次秒数 < 20:
        return False
    return True
