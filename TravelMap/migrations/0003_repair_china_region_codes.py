from django.db import migrations


CHINA_REGION_CODES = {
    'hainan': 'CN-HI', '海南': 'CN-HI',
    'taiwan': 'CN-TW', '台湾': 'CN-TW',
    'guangxi': 'CN-GX', '广西': 'CN-GX',
    'fujian': 'CN-FJ', '福建': 'CN-FJ',
    'yunnan': 'CN-YN', '云南': 'CN-YN',
    'guizhou': 'CN-GZ', '贵州': 'CN-GZ',
    'jiangxi': 'CN-JX', '江西': 'CN-JX',
    'hunan': 'CN-HN', '湖南': 'CN-HN',
    'zhejiang': 'CN-ZJ', '浙江': 'CN-ZJ',
    'shanghai': 'CN-SH', '上海': 'CN-SH',
    'chongqing': 'CN-CQ', '重庆': 'CN-CQ',
    'hubei': 'CN-HB', '湖北': 'CN-HB',
    'sichuan': 'CN-SC', '四川': 'CN-SC',
    'anhui': 'CN-AH', '安徽': 'CN-AH',
    'jiangsu': 'CN-JS', '江苏': 'CN-JS',
    'henan': 'CN-HA', '河南': 'CN-HA',
    'tibet': 'CN-XZ', '西藏': 'CN-XZ',
    'shandong': 'CN-SD', '山东': 'CN-SD',
    'qinghai': 'CN-QH', '青海': 'CN-QH',
    'ningxia': 'CN-NX', '宁夏': 'CN-NX',
    'shaanxi': 'CN-SN', '陕西': 'CN-SN',
    'tianjin': 'CN-TJ', '天津': 'CN-TJ',
    'shanxi': 'CN-SX', '山西': 'CN-SX',
    'beijing': 'CN-BJ', '北京': 'CN-BJ',
    'gansu': 'CN-GS', '甘肃': 'CN-GS',
    'hebei': 'CN-HE', '河北': 'CN-HE',
    'liaoning': 'CN-LN', '辽宁': 'CN-LN',
    'jilin': 'CN-JL', '吉林': 'CN-JL',
    'xinjiang': 'CN-XJ', '新疆': 'CN-XJ',
    'inner mongolia': 'CN-NM', '内蒙古': 'CN-NM',
    'heilongjiang': 'CN-HL', '黑龙江': 'CN-HL',
    'macau': 'CN-MO', '澳门': 'CN-MO',
    'hong kong': 'CN-HK', '香港': 'CN-HK',
    'guangdong': 'CN-GD', 'guangzhou': 'CN-GD', '广东': 'CN-GD',
}


def repair_china_region_codes(apps, schema_editor):
    MapCheckIn = apps.get_model('TravelMap', 'MapCheckIn')
    rows = MapCheckIn.objects.filter(country_code='CHN')
    for row in rows.iterator():
        name = (row.region_name or '').strip().lower()
        code = next((value for prefix, value in CHINA_REGION_CODES.items() if name.startswith(prefix)), None)
        if code:
            target_code = f'CHN:{code}'
            if MapCheckIn.objects.filter(user_id=row.user_id, region_code=target_code).exclude(pk=row.pk).exists():
                row.delete()
                continue
            row.region_code = target_code
            row.save(update_fields=['region_code'])


class Migration(migrations.Migration):
    dependencies = [
        ('TravelMap', '0002_map_checkin_location_and_chat_grants'),
    ]

    operations = [
        migrations.RunPython(repair_china_region_codes, migrations.RunPython.noop),
    ]
