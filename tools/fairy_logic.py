# -*- coding: utf-8 -*-


def 问候语(小时):
    if 小时 < 5:
        return '凌晨了'
    if 小时 < 11:
        return '早上好'
    if 小时 < 13:
        return '中午好'
    if 小时 < 18:
        return '下午好'
    if 小时 < 23:
        return '晚上好'
    return '夜深了'

def 穿衣建议(温度):
    if 温度 <= 0:
        return '零度以下，羽绒服加厚毛衣，手套围巾都带上'
    if 温度 <= 8:
        return '很冷，羽绒服或厚大衣，里面加毛衣'
    if 温度 <= 15:
        return '偏冷，外套加长袖，早晚温差大注意加衣'
    if 温度 <= 22:
        return '舒适微凉，薄外套或卫衣就够了'
    if 温度 <= 28:
        return '温暖，长袖或短袖都行，随身带件薄外搭'
    return '偏热，短袖短裤，注意防晒补水'

def 播报语气(温度, 湿度):
    if 温度 >= 33:
        return '提醒型'
    if 湿度 >= 85:
        return '提醒型'
    if 温度 <= 5:
        return '关心型'
    return '常规型'

def 天气点评(温度, 湿度, 风速):
    if 风速 >= 40:
        return '风很大，出门注意'
    if 湿度 >= 85:
        return '很潮湿，注意防潮'
    if 温度 >= 33:
        return '高温，注意补水防晒'
    if 温度 <= 5:
        return '很冷，注意保暖'
    return '天气还行'

def 是否提醒带伞(描述):
    if 描述 == '小雨':
        return True
    if 描述 == '中雨':
        return True
    if 描述 == '大雨':
        return True
    if 描述 == '雨':
        return True
    if 描述 == '阵雨':
        return True
    if 描述 == '雷':
        return True
    return False


_降水词 = ('雨', '雪', '雷')
_雪词 = ('雪',)

def 含任一(文本, 词表):
    s = 文本 or ''
    for w in 词表:
        if w in s:
            return True
    return False

def 温度说法(最低, 最高):
    if 最低 is None and 最高 is None:
        return '温度未知'
    if 最低 is None:
        return '%s 度' % (最高,)
    if 最高 is None:
        return '%s 度' % (最低,)
    return '%s 到 %s 度' % (最低, 最高)

def 一天说法(称呼, 最低, 最高, 天气, 降水):
    s = '%s %s' % (称呼, 温度说法(最低, 最高))
    t = 天气 or ''
    if 含任一(t, _降水词):
        return s + '，' + t
    if t:
        s = s + '，' + t
    if 降水:
        s = s + '，' + 降水
    return s

def 三天天气句(甲, 乙):
    if 甲:
        seg = []
        for d in 甲:
            seg.append(一天说法(d.get('称呼') or '', d.get('最低'), d.get('最高'),
                              d.get('天气') or '', d.get('降水') or ''))
        if seg:
            return '未来三天：' + '；'.join(seg) + '。'
    if 乙:
        seg = []
        for d in 乙:
            seg.append('%s %s' % (d.get('称呼') or '', 温度说法(d.get('最低'), d.get('最高'))))
        if seg:
            return '未来三天：' + '；'.join(seg) + '。'
    return ''
