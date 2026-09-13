import requests

from django.core import signing
from django.db import transaction

from Config.models import Config, CI
from Space.models import Space
from User.models import User, WeChatMiniProgramIdentity
from User.validators import UserErrors
from utils import function


CODE_TO_SESSION_URL = 'https://api.weixin.qq.com/sns/jscode2session'
DEFAULT_SPACE_SLUG = 'jzdxq'
ONBOARDING_TICKET_SALT = 'wechat-miniprogram-onboarding-v1'
ONBOARDING_TICKET_MAX_AGE = 10 * 60


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
    base = base[:User.vldt.NICKNAME_MAX_LENGTH]
    User.vldt.nickname(base)
    if not User.objects.filter(space=space, lower_name=base.lower(), is_deleted=False).exists():
        return base
    suffix = 2
    while True:
        marker = str(suffix)
        candidate = f'{base[:User.vldt.NICKNAME_MAX_LENGTH - len(marker)]}{marker}'
        if not User.objects.filter(space=space, lower_name=candidate.lower(), is_deleted=False).exists():
            return candidate
        suffix += 1


def _identity_user(session, space, language):
    identity = WeChatMiniProgramIdentity.objects.select_related('user').filter(
        app_id=session['app_id'], open_id=session['open_id'], space=space,
    ).first()
    if identity is None:
        return None
    user = identity.user
    if user.is_deleted:
        raise UserErrors.USER_DELETED
    if session['union_id'] and identity.union_id != session['union_id']:
        identity.union_id = session['union_id']
        identity.save(update_fields=['union_id', 'updated_at'])
    user.set_language(language)
    return user


def begin_wechat_login(code, language='zh-CN', space_slug=None):
    session = exchange_code(code)
    space_slug = space_slug or Config.get_value_by_key(
        CI.WECHAT_MINIPROGRAM_SPACE_SLUG, default=DEFAULT_SPACE_SLUG,
    )
    space = Space.get_by_slug(space_slug)
    user = _identity_user(session, space, language)
    if user is not None:
        return user, None, space
    ticket = signing.dumps({**session, 'space_id': space.id}, salt=ONBOARDING_TICKET_SALT, compress=True)
    return None, ticket, space


def _load_onboarding_ticket(ticket):
    try:
        payload = signing.loads(ticket, salt=ONBOARDING_TICKET_SALT, max_age=ONBOARDING_TICKET_MAX_AGE)
    except signing.SignatureExpired:
        raise UserErrors.WECHAT_ONBOARDING_TICKET_EXPIRED
    except signing.BadSignature:
        raise UserErrors.WECHAT_ONBOARDING_TICKET_INVALID
    required = ('app_id', 'open_id', 'space_id')
    if not isinstance(payload, dict) or any(not payload.get(key) for key in required):
        raise UserErrors.WECHAT_ONBOARDING_TICKET_INVALID
    return payload


def complete_wechat_onboarding(ticket, mode, nickname=None, password=None, language='zh-CN'):
    session = _load_onboarding_ticket(ticket)
    space = Space.objects.get(id=session['space_id'])
    with transaction.atomic():
        identity = WeChatMiniProgramIdentity.objects.select_for_update().select_related('user').filter(
            app_id=session['app_id'], open_id=session['open_id'], space=space,
        ).first()
        if identity is not None:
            user = identity.user
            if user.is_deleted:
                raise UserErrors.USER_DELETED
            user.set_language(language)
            return user, False
        if mode == 'existing':
            user = User.objects.select_for_update().filter(
                space=space, lower_name=(nickname or '').strip().lower(), is_deleted=False,
            ).first()
            if user is None:
                raise UserErrors.NOT_EXISTS(attr='name', value=nickname or '')
            if not user.has_password:
                raise UserErrors.WECHAT_EXISTING_ACCOUNT_PASSWORD_REQUIRED
            if not password or not function.verify_password(password, user.salt, user.password):
                raise UserErrors.PASSWORD_ERROR
            user.set_language(language)
            WeChatMiniProgramIdentity.objects.create(user=user, space=space, **{
                key: session.get(key, '') for key in ('app_id', 'open_id', 'union_id')
            })
            return user, False
        space.ensure_member_limit_available()
        user = User.create(
            space=space,
            name=_available_name(space, nickname, session['open_id']),
            language=language,
        )
        WeChatMiniProgramIdentity.objects.create(user=user, space=space, **session)
        transaction.on_commit(space.notify_capacity_if_needed)
        return user, True


def login_with_wechat_code(code, nickname=None, language='zh-CN', space_slug=None):
    user, ticket, _space = begin_wechat_login(code, language=language, space_slug=space_slug)
    if user is not None:
        return user, False
    return complete_wechat_onboarding(ticket, 'new', nickname=nickname, language=language)
