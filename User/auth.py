import datetime
from typing import Optional

import jwt

from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from oba import Obj
from smartdjango import Error, Code, Validator, DictValidator, analyse
from smartdjango.analyse import get_request, Request as BaseRequest

from Chat.models import BaseChat, SingleChat, GroupChat
from Sermo.settings import SECRET_KEY
from User.models import BaseUser, UserRoleChoice, RefreshToken


class Request(BaseRequest):
    json: Obj
    query: Obj
    argument: Obj
    data: Obj
    user: BaseUser


@Error.register
class AuthErrors:
    EXPIRED = Error(_('Token expired'), code=Code.Unauthorized)
    FORMAT = Error(_('Incorrect format'), code=Code.BadRequest)
    REVOKED = Error(_('Token revoked'), code=Code.Unauthorized)


class Symbols:
    EXPIRE = 'expire'
    TIME = 'time'
    TYPE = 'type'
    JTI = 'jti'
    ALGORITHM = 'HS256'
    ACCESS = 'access'
    REFRESH = 'refresh'


ACCESS_EXPIRE_SECONDS = 15 * 60
REFRESH_EXPIRE_SECONDS = 30 * 24 * 60 * 60


def _encode_token(data: dict, expire_second: int, token_type: str, jti: Optional[str] = None):
    payload = dict(data)
    payload[Symbols.TIME] = datetime.datetime.now().timestamp()
    payload[Symbols.EXPIRE] = expire_second
    payload[Symbols.TYPE] = token_type
    if jti:
        payload[Symbols.JTI] = jti
    token = jwt.encode(payload, key=SECRET_KEY, algorithm=Symbols.ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode()
    return token, payload


def decrypt(data_str: str, expected_type: Optional[str] = None, allow_legacy_access: bool = False):
    try:
        data = jwt.decode(data_str, key=SECRET_KEY, algorithms=Symbols.ALGORITHM)
    except jwt.DecodeError as err:
        raise AuthErrors.FORMAT(details=err)
    DictValidator().fields(
        Validator(Symbols.EXPIRE).bool(lambda x: isinstance(x, int), message=_('Invalid JWT expire')),
        Validator(Symbols.TIME).bool(lambda x: isinstance(x, float), message=_('Invalid JWT time')),
    ).clean(data)
    if datetime.datetime.now().timestamp() > data[Symbols.TIME] + data[Symbols.EXPIRE]:
        raise AuthErrors.EXPIRED
    if expected_type is not None:
        token_type = data.get(Symbols.TYPE)
        if token_type is None and allow_legacy_access and expected_type == Symbols.ACCESS:
            return data
        if token_type != expected_type:
            raise AuthErrors.FORMAT(details=_('Invalid token type'))
    return data


def _require_user(func, user_role: Optional[int]):
    def wrapper(*args, **kwargs):
        request = get_request(*args)
        token = _get_authorization_token(request)
        data = decrypt(token, expected_type=Symbols.ACCESS, allow_legacy_access=True)

        user = BaseUser.jwt_login(data)
        request.user = user

        if user_role is not None and user.role != user_role:
            raise AuthErrors.FORMAT(details=_('Invalid user role'))

        return func(*args, **kwargs)
    return wrapper


def _get_authorization_token(request):
    token = request.headers.get('Authorization')
    if not token:
        raise AuthErrors.FORMAT(details=_('Missing authorization header'))
    token = token.strip()
    if token.lower().startswith('bearer '):
        token = token[7:].strip()
    if not token:
        raise AuthErrors.FORMAT(details=_('Missing authorization header'))
    return token


SUBDOMAIN_HEADER = 'X-Sermo-Subdomain'


def get_request_subdomain(request):
    subdomain = request.headers.get(SUBDOMAIN_HEADER) or request.headers.get(SUBDOMAIN_HEADER.lower())
    if not subdomain:
        return None
    subdomain = subdomain.strip().lower()
    return subdomain or None


def require_host_user(func):
    return _require_user(func, user_role=UserRoleChoice.HOST)


def require_guest_user(func):
    return _require_user(func, user_role=UserRoleChoice.GUEST)


def require_user(func):
    return _require_user(func, user_role=None)


def get_login_token(user: BaseUser):
    access_token, access_payload = _encode_token(
        user.jwt_json(),
        expire_second=ACCESS_EXPIRE_SECONDS,
        token_type=Symbols.ACCESS,
    )
    refresh_token = _issue_refresh_token(user)
    return dict(
        auth=access_token,
        refresh=refresh_token,
        data=access_payload,
    )


def _issue_refresh_token(user: BaseUser):
    jti = get_random_string(32)
    refresh_token, _payload = _encode_token(
        {'user_id': user.id},
        expire_second=REFRESH_EXPIRE_SECONDS,
        token_type=Symbols.REFRESH,
        jti=jti,
    )
    RefreshToken.objects.create(
        user=user,
        jti=jti,
        expires_at=timezone.now() + datetime.timedelta(seconds=REFRESH_EXPIRE_SECONDS),
    )
    return refresh_token


def refresh_login_token(refresh_token: str):
    data = decrypt(refresh_token, expected_type=Symbols.REFRESH)
    jti = data.get(Symbols.JTI)
    if not jti:
        raise AuthErrors.FORMAT(details=_('Missing refresh token id'))
    token = RefreshToken.objects.filter(jti=jti, user_id=data['user_id']).first()
    if token is None:
        raise AuthErrors.REVOKED
    if token.revoked_at is not None:
        raise AuthErrors.REVOKED
    if token.expires_at <= timezone.now():
        token.revoke()
        raise AuthErrors.EXPIRED
    token.revoke()
    user = BaseUser.index(data['user_id'])
    return get_login_token(user)


def revoke_refresh_token(refresh_token: str):
    data = decrypt(refresh_token, expected_type=Symbols.REFRESH)
    jti = data.get(Symbols.JTI)
    if not jti:
        return
    token = RefreshToken.objects.filter(jti=jti, user_id=data['user_id']).first()
    if token is None:
        return
    token.revoke()


def _is_chat_host(request):
    return request.user == request.data.chat.host


def _is_singlechat_guest(request):
    chat = request.data.chat.specify()
    return request.user == chat.guest


def _is_groupchat_guest(request):
    chat = request.data.chat.specify()
    return request.user in chat.guests.all()


def _is_singlechat_member(request):
    return _is_chat_host(request) or _is_singlechat_guest(request)


def _is_groupchat_member(request):
    return _is_chat_host(request) or _is_groupchat_guest(request)


def _is_chat_member(request):
    chat = request.data.chat.specify()
    return _is_chat_host(request) \
        or (isinstance(chat, SingleChat) and _is_singlechat_guest(request)) \
        or (isinstance(chat, GroupChat) and _is_groupchat_guest(request))


def _is_message_owner(request):
    return request.user == request.data.message.user or request.user == request.data.message.chat.host


def require_chat_owner():
    return analyse.request(_is_chat_host, message=_("You are not the owner of this chat"))


def require_singlechat_member():
    return analyse.request(_is_singlechat_member, message=_("You are not a member of this chat"))


def require_groupchat_member():
    return analyse.request(_is_groupchat_member, message=_("You are not a member of this chat"))


def require_chat_member():
    return analyse.request(_is_chat_member, message=_("You are not a member of this chat"))


def require_message_owner():
    return analyse.request(_is_message_owner, message=_("You are not the owner of this message"))
