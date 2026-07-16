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
        GETUI_APP_ID = Config.get_value_by_key(CI.GETUI_APP_ID)
        GETUI_APP_KEY = Config.get_value_by_key(CI.GETUI_APP_KEY)
        GETUI_APP_SECRET = Config.get_value_by_key(CI.GETUI_APP_SECRET)
        GETUI_MASTER_SECRET = Config.get_value_by_key(CI.GETUI_MASTER_SECRET)
        GETUI_BASE_URL = Config.get_value_by_key(CI.GETUI_BASE_URL, default='https://restapi.getui.com/v2')
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
