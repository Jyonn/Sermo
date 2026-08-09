import re


CITY_BUBBLE_RULES = {
    'city-jdz': {
        'country_codes': {'CHN'},
        'region_codes': {'CN-JX'},
        'region_names': {'jiangxi', 'jiangxiprovince', '江西', '江西省'},
        'label': 'Jiangxi Province',
    },
    'city-shanghai': {
        'country_codes': {'CHN'},
        'region_codes': {'CN-SH'},
        'region_names': {'shanghai', 'shanghaimunicipality', '上海', '上海市'},
        'label': 'Shanghai',
    },
    'city-beijing': {
        'country_codes': {'CHN'},
        'region_codes': {'CN-BJ'},
        'region_names': {'beijing', 'beijingmunicipality', '北京', '北京市'},
        'label': 'Beijing',
    },
    'city-nyc': {
        'country_codes': {'USA', 'US'},
        'region_codes': {'US-NY'},
        'region_names': {'newyork', 'newyorkstate', '纽约', '纽约州'},
        'label': 'New York State',
    },
}


def _compact(value):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', (value or '').casefold())


def checkin_unlocks_style(checkin, style):
    rule = CITY_BUBBLE_RULES.get(style)
    if not rule or (checkin.country_code or '').upper() not in rule['country_codes']:
        return False
    region_code = (checkin.region_code or '').upper()
    region_name = _compact(checkin.region_name)
    return region_code in rule['region_codes'] or region_name in rule['region_names']


def unlocked_city_bubble_styles(user):
    from TravelMap.models import MapCheckIn

    checkins = MapCheckIn.objects.filter(user=user).only('country_code', 'region_code', 'region_name')
    return [
        style
        for style in CITY_BUBBLE_RULES
        if any(checkin_unlocks_style(checkin, style) for checkin in checkins)
    ]


def city_bubble_requirement(style):
    rule = CITY_BUBBLE_RULES.get(style)
    return rule['label'] if rule else ''
