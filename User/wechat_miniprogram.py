import requests

from django.db import transaction

from Config.models import Config, CI
from Space.models import Space
from User.models import User, WeChatMiniProgramIdentity
from User.validators import UserErrors


CODE_TO_SESSION_URL = 'https://api.weixin.qq.com/sns/jscode2session'
DEFAULT_SPACE_SLUG = 'jzdxq'


def _required_config(key):
    value = Config.get_value_by_key(key, default='')
    normalized = str(value or '').strip()
    if not normalized:
        raise UserErrors.WECHAT_MINIPROGRAM_NOT_CONFIGURED
    return normalized


def exchange_code(code):
    app_id = _required_config(CI.WECHAT_MINIPROGRAM_APP_ID)
    app_secret = _required_config(CI.WECHAT_MINIPROGRAM_APP_SECRET)
    try:
        response = requests.get(
            CODE_TO_SESSION_URL,
            params={
                'appid': app_id,
                'secret': app_secret,
                'js_code': code,
                'grant_type': 'authorization_code',
            },
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as error:
        raise UserErrors.WECHAT_LOGIN_FAILED(details=error)
    open_id = str(payload.get('openid') or '').strip()
    if payload.get('errcode') or not open_id:
        raise UserErrors.WECHAT_LOGIN_CODE_INVALID(details=payload.get('errmsg'))
    return dict(
        app_id=app_id,
        open_id=open_id,
        union_id=str(payload.get('unionid') or '').strip(),
    )


def _available_name(space, requested, open_id):
    base = (requested or '').strip() or f'微信用户{open_id[-6:]}'
    base = base[:User.vldt.NAME_MAX_LENGTH]
    User.vldt.name(base)
    if not User.objects.filter(space=space, lower_name=base.lower(), is_deleted=False).exists():
        return base
    suffix = 2
    while True:
        marker = str(suffix)
        candidate = f'{base[:User.vldt.NAME_MAX_LENGTH - len(marker)]}{marker}'
        if not User.objects.filter(space=space, lower_name=candidate.lower(), is_deleted=False).exists():
            return candidate
        suffix += 1


def login_with_wechat_code(code, nickname=None, language='zh-CN'):
    session = exchange_code(code)
    space_slug = Config.get_value_by_key(
        CI.WECHAT_MINIPROGRAM_SPACE_SLUG, default=DEFAULT_SPACE_SLUG,
    )
    space = Space.get_by_slug(space_slug)
    with transaction.atomic():
        identity = WeChatMiniProgramIdentity.objects.select_for_update().select_related('user').filter(
            app_id=session['app_id'], open_id=session['open_id'],
        ).first()
        if identity is not None:
            user = identity.user
            if user.is_deleted:
                raise UserErrors.USER_DELETED
            if user.space_id != space.id:
                raise UserErrors.SPACE_FORBIDDEN
            if session['union_id'] and identity.union_id != session['union_id']:
                identity.union_id = session['union_id']
                identity.save(update_fields=['union_id', 'updated_at'])
            user.set_language(language)
            return user, False

        space.ensure_member_limit_available()
        user = User.create(
            space=space,
            name=_available_name(space, nickname, session['open_id']),
            language=language,
        )
        WeChatMiniProgramIdentity.objects.create(user=user, **session)
        transaction.on_commit(space.notify_capacity_if_needed)
        return user, True
