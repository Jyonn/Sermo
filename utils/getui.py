import hashlib
import json
import time
import uuid
from urllib.parse import quote

import requests

from utils.global_settings import Globals


GETUI_PACKAGE_NAME = 'space.jyonn.sermo'
GETUI_ROUTER_ACTIVITY = 'space.jyonn.sermo.PushRouterActivity'
_auth_token = None
_auth_expire_at_ms = 0


class GetuiNotConfigured(Exception):
    pass


class GetuiAPIError(Exception):
    pass


def _required(value, name: str):
    normalized = (str(value).strip() if value is not None else '')
    if not normalized:
        raise GetuiNotConfigured(f'{name} is not configured')
    return normalized


def _base_url():
    app_id = _required(getattr(Globals, 'GETUI_APP_ID', None), 'GETUI_APP_ID')
    base = (getattr(Globals, 'GETUI_BASE_URL', None) or 'https://restapi.getui.com/v2').rstrip('/')
    return f'{base}/{app_id}'


def is_configured():
    return all(
        (getattr(Globals, key, None) or '').strip()
        for key in ('GETUI_APP_ID', 'GETUI_APP_KEY', 'GETUI_APP_SECRET')
    )


def _auth_sign(timestamp_ms: str):
    app_key = _required(getattr(Globals, 'GETUI_APP_KEY', None), 'GETUI_APP_KEY')
    app_secret = _required(getattr(Globals, 'GETUI_APP_SECRET', None), 'GETUI_APP_SECRET')
    return hashlib.sha256(f'{app_key}{timestamp_ms}{app_secret}'.encode()).hexdigest()


def _get_auth_token(force_refresh=False):
    global _auth_token, _auth_expire_at_ms
    now_ms = int(time.time() * 1000)
    if not force_refresh and _auth_token and _auth_expire_at_ms - now_ms > 60_000:
        return _auth_token

    timestamp_ms = str(now_ms)
    app_key = _required(getattr(Globals, 'GETUI_APP_KEY', None), 'GETUI_APP_KEY')
    response = requests.post(
        f'{_base_url()}/auth',
        json=dict(
            sign=_auth_sign(timestamp_ms),
            timestamp=timestamp_ms,
            appkey=app_key,
        ),
        headers={'Content-Type': 'application/json;charset=utf-8'},
        timeout=10,
    )
    try:
        payload = response.json()
    except ValueError as err:
        raise GetuiAPIError(f'auth invalid json: {response.text[:120]}') from err

    if response.status_code != 200 or int(payload.get('code', -1)) != 0:
        raise GetuiAPIError(f'auth failed: {payload}')

    data = payload.get('data') or {}
    _auth_token = data.get('token')
    _auth_expire_at_ms = int(data.get('expire_time') or 0)
    if not _auth_token:
        raise GetuiAPIError(f'auth token missing: {payload}')
    return _auth_token


def build_intent(payload: dict):
    encoded_payload = quote(json.dumps(payload or {}, ensure_ascii=False, separators=(',', ':')), safe='')
    return (
        'intent://sermo/push?#Intent;'
        'scheme=sermo;'
        'launchFlags=0x10000000;'
        f'package={GETUI_PACKAGE_NAME};'
        f'component={GETUI_PACKAGE_NAME}/{GETUI_ROUTER_ACTIVITY};'
        f'S.payload={encoded_payload};'
        'end'
    )


def send_to_cid(cid: str, title: str, body: str, payload: dict):
    normalized_cid = (cid or '').strip()
    if not normalized_cid:
        raise GetuiAPIError('cid is empty')
    if not is_configured():
        raise GetuiNotConfigured('getui is not configured')

    intent = build_intent(payload)
    request_body = dict(
        request_id=uuid.uuid4().hex,
        audience=dict(cid=[normalized_cid]),
        settings=dict(ttl=24 * 60 * 60 * 1000),
        push_message=dict(
            notification=dict(
                title=title,
                body=body,
                click_type='intent',
                intent=intent,
            )
        ),
        push_channel=dict(
            android=dict(
                ups=dict(
                    notification=dict(
                        title=title,
                        body=body,
                        click_type='intent',
                        intent=intent,
                    )
                )
            )
        ),
    )
    return _post_push(request_body)


def _post_push(request_body: dict, force_refresh=False):
    token = _get_auth_token(force_refresh=force_refresh)
    response = requests.post(
        f'{_base_url()}/push/single/cid',
        json=request_body,
        headers={
            'Content-Type': 'application/json;charset=utf-8',
            'token': token,
        },
        timeout=10,
    )
    try:
        payload = response.json()
    except ValueError as err:
        raise GetuiAPIError(f'push invalid json: {response.text[:120]}') from err

    if int(payload.get('code', -1)) == 10001 and not force_refresh:
        return _post_push(request_body, force_refresh=True)
    if response.status_code != 200 or int(payload.get('code', -1)) != 0:
        raise GetuiAPIError(f'push failed: {payload}')
    return payload
