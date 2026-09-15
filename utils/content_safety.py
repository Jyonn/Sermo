import json
import logging

import requests
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Config.models import CI, Config


logger = logging.getLogger(__name__)

MINIPROGRAM_CLIENT = 'wechat-miniprogram'
STABLE_TOKEN_URL = 'https://api.weixin.qq.com/cgi-bin/stable_token'
MESSAGE_CHECK_URL = 'https://api.weixin.qq.com/wxa/msg_sec_check'
TOKEN_CACHE_KEY = 'wechat:miniprogram:stable-access-token'
TOKEN_REFRESH_ERRORS = {40001, 40014, 42001}


@Error.register
class ContentSafetyErrors:
    REJECTED = Error(
        message=_('The content may not comply with the rules. Please revise it and try again.'),
        code=Code.BadRequest,
    )
    UNAVAILABLE = Error(
        message=_('Content review is temporarily unavailable. Please try again later.'),
        code=Code.ServiceUnavailable,
    )


class ContentSafetyScene:
    PROFILE = 1
    COMMENT = 2
    FORUM = 3
    SOCIAL = 4


def is_miniprogram_request(request):
    return request.headers.get('X-Sermo-Client', '').strip().lower() == MINIPROGRAM_CLIENT


def _required_config(key):
    value = str(Config.get_value_by_key(key, default='') or '').strip()
    if not value:
        raise ContentSafetyErrors.UNAVAILABLE
    return value


def _access_token(force_refresh=False):
    if not force_refresh:
        cached = cache.get(TOKEN_CACHE_KEY)
        if cached:
            return cached
    try:
        response = requests.post(
            STABLE_TOKEN_URL,
            json={
                'grant_type': 'client_credential',
                'appid': _required_config(CI.WECHAT_MINIPROGRAM_APP_ID),
                'secret': _required_config(CI.WECHAT_MINIPROGRAM_APP_SECRET),
                'force_refresh': bool(force_refresh),
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get('access_token') or '').strip()
        if not token:
            raise ValueError(payload.get('errmsg') or 'missing access_token')
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.warning('Unable to obtain WeChat content review token: %s', error)
        raise ContentSafetyErrors.UNAVAILABLE
    expires_in = max(60, int(payload.get('expires_in') or 7200) - 300)
    cache.set(TOKEN_CACHE_KEY, token, expires_in)
    return token


def check_text(content, open_id, scene, *, title=None, nickname=None):
    normalized = str(content or '').strip()
    if not normalized:
        return None
    if not open_id:
        raise ContentSafetyErrors.UNAVAILABLE
    body = {
        'content': normalized,
        'version': 2,
        'scene': scene,
        'openid': open_id,
    }
    if title:
        body['title'] = str(title).strip()
    if nickname:
        body['nickname'] = str(nickname).strip()

    payload = _perform_check(body, _access_token())
    if payload.get('errcode') in TOKEN_REFRESH_ERRORS:
        cache.delete(TOKEN_CACHE_KEY)
        payload = _perform_check(body, _access_token(force_refresh=True))
    if payload.get('errcode'):
        logger.warning(
            'WeChat content review failed: errcode=%s trace_id=%s',
            payload.get('errcode'), payload.get('trace_id'),
        )
        _raise_review_error(ContentSafetyErrors.UNAVAILABLE, _review_debug(payload, scene))
    result = payload.get('result') or {}
    debug = _review_debug(payload, scene)
    if result.get('suggest') != 'pass':
        logger.info(
            'WeChat content rejected: suggest=%s label=%s trace_id=%s',
            result.get('suggest'), result.get('label'), payload.get('trace_id'),
        )
        _raise_review_error(ContentSafetyErrors.REJECTED, debug)
    return debug


def _review_debug(payload, scene):
    return {
        'scene': scene,
        'errcode': payload.get('errcode'),
        'errmsg': payload.get('errmsg'),
        'trace_id': payload.get('trace_id'),
        'result': payload.get('result'),
        'detail': payload.get('detail'),
    }


def _raise_review_error(error_template, debug):
    error = error_template(details=json.dumps(debug, ensure_ascii=False, default=str))
    error.wechat_content_safety = debug
    raise error


def _perform_check(body, token):
    try:
        response = requests.post(
            MESSAGE_CHECK_URL,
            params={'access_token': token},
            json=body,
            timeout=8,
        )
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError, TypeError) as error:
        logger.warning('Unable to call WeChat content review: %s', error)
        raise ContentSafetyErrors.UNAVAILABLE


def check_user_text(request, content, scene, *, title=None, nickname=None):
    if not is_miniprogram_request(request):
        return
    from User.models import WeChatMiniProgramIdentity

    identity = WeChatMiniProgramIdentity.objects.filter(
        user=request.user,
        space=request.user.space,
        app_id=_required_config(CI.WECHAT_MINIPROGRAM_APP_ID),
    ).only('open_id').first()
    if identity is None:
        raise ContentSafetyErrors.UNAVAILABLE
    try:
        debug = check_text(content, identity.open_id, scene, title=title, nickname=nickname)
    except Error as error:
        if getattr(error, 'wechat_content_safety', None):
            request.wechat_content_safety = error.wechat_content_safety
        raise
    if debug:
        request.wechat_content_safety = debug
    return debug
