import sys
from pathlib import Path

EMAIL_CHANNEL = 1
SMS_CHANNEL = 2
BARK_CHANNEL = 3

_client_cache = None
_client_cache_key = None


def _try_get_config_value(keys, default=''):
    try:
        from User.models import Config, CI
    except Exception:
        return default

    normalized_keys = []
    for key in keys:
        normalized_keys.append(key)
        if isinstance(key, str):
            normalized_keys.append(key.upper())
            normalized_keys.append(key.lower())

    ci_alias = {
        'NOTIFICATOR_SDK_PATH': getattr(CI, 'NOTIFICATOR_SDK_PATH', None),
        'NOTIFICATOR_NAME': getattr(CI, 'NOTIFICATOR_NAME', None),
        'NOTIFICATOR_TOKEN': getattr(CI, 'NOTIFICATOR_TOKEN', None),
        'NOTIFICATOR_HOST': getattr(CI, 'NOTIFICATOR_HOST', None),
        'NOTIFICATOR_TIMEOUT': getattr(CI, 'NOTIFICATOR_TIMEOUT', None),
    }
    normalized_keys.extend([value for value in ci_alias.values() if value])

    for key in normalized_keys:
        value = Config.get_value_by_key(key, default=None)
        if value is None:
            continue
        value = str(value).strip()
        if value:
            return value
    return default


def _read_notificator_config():
    sdk_path = _try_get_config_value(
        keys=['NOTIFICATOR_SDK_PATH', 'notificator.sdk_path', 'notificator_sdk_path'],
        default='~/Projects/Apps/Notificator/notificator-sdk',
    )
    name = _try_get_config_value(
        keys=['NOTIFICATOR_NAME', 'notificator.name', 'notificator_name'],
        default='',
    )
    token = _try_get_config_value(
        keys=['NOTIFICATOR_TOKEN', 'notificator.token', 'notificator_token'],
        default='',
    )
    host = _try_get_config_value(
        keys=['NOTIFICATOR_HOST', 'notificator.host', 'notificator_host'],
        default='',
    )
    timeout_str = _try_get_config_value(
        keys=['NOTIFICATOR_TIMEOUT', 'notificator.timeout', 'notificator_timeout'],
        default='15',
    )
    try:
        timeout = int(timeout_str)
    except (TypeError, ValueError):
        timeout = 15
    return dict(
        sdk_path=sdk_path,
        name=name,
        token=token,
        host=host,
        timeout=timeout,
    )


def _ensure_sdk_path(sdk_path):
    if not sdk_path:
        return
    expanded = str(Path(sdk_path).expanduser())
    if expanded not in sys.path and Path(expanded).exists():
        sys.path.insert(0, expanded)


def _client_key(conf):
    return conf['name'], conf['token'], conf['host'], conf['timeout'], conf['sdk_path']


def get_client():
    global _client_cache, _client_cache_key

    conf = _read_notificator_config()
    name = conf['name']
    token = conf['token']
    if not name or not token:
        return None

    key = _client_key(conf)
    if _client_cache is not None and _client_cache_key == key:
        return _client_cache

    _ensure_sdk_path(conf['sdk_path'])
    from notificator import Notificator

    host = (conf['host'] or '').strip() or None
    timeout = int(conf['timeout'] or 15)
    _client_cache = Notificator(name=name, token=token, host=host, timeout=timeout)
    _client_cache_key = key
    return _client_cache


def send(channel: int, target: str, title: str, body: str, recipient_name: str = None):
    client = get_client()
    if client is None:
        return False, 'notificator_not_configured'

    try:
        if channel == EMAIL_CHANNEL:
            client.mail(
                mail=target,
                format='text',
                body=body,
                title=title,
                recipient_name=recipient_name,
            )
        elif channel == SMS_CHANNEL:
            client.sms(
                phone=target,
                format='text',
                body=body,
                title=title,
            )
        elif channel == BARK_CHANNEL:
            client.bark(
                uri=target,
                format='text',
                body=body,
                title=title,
            )
        else:
            return False, 'unsupported_channel'
    except Exception as err:
        return False, str(err)

    return True, None
