from notificator import Notificator

from Config.models import Config, CI
from Sermo.settings import PROJ_INIT


class Globals:
    try:
        NOTIFICATOR_NAME = Config.get_value_by_key(CI.NOTIFICATOR_NAME)
        NOTIFICATOR_TOKEN = Config.get_value_by_key(CI.NOTIFICATOR_TOKEN)
        NOTIFICATOR_HOST = Config.get_value_by_key(CI.NOTIFICATOR_HOST)
        NOTIFICATOR_TIMEOUT = Config.get_value_by_key(CI.NOTIFICATOR_TIMEOUT, to=int)
        SECRET_KEY = Config.get_value_by_key(CI.SECRET_KEY)
        QINIU_ACCESS_KEY = Config.get_value_by_key(CI.QINIU_ACCESS_KEY)
        QINIU_SECRET_KEY = Config.get_value_by_key(CI.QINIU_SECRET_KEY)
        QINIU_BUCKET = Config.get_value_by_key(CI.QINIU_BUCKET)
        QINIU_DOMAIN = Config.get_value_by_key(CI.QINIU_DOMAIN)
        WEB_PUSH_VAPID_PUBLIC_KEY = Config.get_value_by_key(CI.WEB_PUSH_VAPID_PUBLIC_KEY)
        WEB_PUSH_VAPID_PRIVATE_KEY = Config.get_value_by_key(CI.WEB_PUSH_VAPID_PRIVATE_KEY)
        WEB_PUSH_VAPID_SUBJECT = Config.get_value_by_key(CI.WEB_PUSH_VAPID_SUBJECT, default='mailto:admin@sermo.jyonn.space')
        REVERSE_GEOCODING_URL = Config.get_value_by_key(CI.REVERSE_GEOCODING_URL, default='https://nominatim.openstreetmap.org/reverse')
        REVERSE_GEOCODING_USER_AGENT = Config.get_value_by_key(CI.REVERSE_GEOCODING_USER_AGENT, default='Sermo/0.2 (admin@sermo.jyonn.space)')
        AMAP_WEBSERVICE_KEY = Config.get_value_by_key(CI.AMAP_WEBSERVICE_KEY, default='')
        AMAP_REVERSE_GEOCODING_URL = Config.get_value_by_key(
            CI.AMAP_REVERSE_GEOCODING_URL,
            default='https://restapi.amap.com/v3/geocode/regeo',
        )
        OPENCAGE_API_KEY = Config.get_value_by_key(CI.OPENCAGE_API_KEY, default='')
        OPENCAGE_GEOCODING_URL = Config.get_value_by_key(
            CI.OPENCAGE_GEOCODING_URL,
            default='https://api.opencagedata.com/geocode/v1/json',
        )
    except Exception as e:
        if not PROJ_INIT:
            raise e


try:
    notificator = Notificator(
        host=Globals.NOTIFICATOR_HOST,
        name=Globals.NOTIFICATOR_NAME,
        token=Globals.NOTIFICATOR_TOKEN,
        timeout=Globals.NOTIFICATOR_TIMEOUT,
    )
except Exception as e:
    if not PROJ_INIT:
        raise e
    notificator = None
