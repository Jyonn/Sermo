import datetime
from typing import Optional, Callable

import jwt

from django.utils import timezone
from django.utils.crypto import get_random_string
from django.utils.translation import gettext as _
from oba import Obj
from smartdjango import Error, Code, Validator, DictValidator, analyse
from smartdjango.analyse import get_request, Request as BaseRequest

from Chat.models import Chat
from Sermo.settings import SECRET_KEY
from User.models import User, RefreshToken


class Request(BaseRequest):
    json: Obj
    query: Obj
    argument: Obj
    data: Obj
    user: User


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
SPACE_ACCESS_EXPIRE_SECONDS = 24 * 60 * 60


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


def decrypt(data_str: str, expected_type: Optional[str] = None):
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
        if token_type != expected_type:
            raise AuthErrors.FORMAT(details=_('Invalid token type'))
    return data


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


def _require_user(func, checker: Optional[Callable[[User], bool]] = None):
    def wrapper(*args, **kwargs):
        request = get_request(*args)
        token = _get_authorization_token(request)
        data = decrypt(token, expected_type=Symbols.ACCESS)

        user = User.jwt_login(data['user_id'])
        request.user = user

        if checker is not None and not checker(user):
            raise AuthErrors.FORMAT(details=_('Invalid user role'))

        return func(*args, **kwargs)
    return wrapper


def require_user(func):
    return _require_user(func)


def get_login_token(user: User):
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


def get_space_login_token(space):
    access_token, access_payload = _encode_token(
        dict(
            space_id=space.id,
            slug=space.slug,
            email=space.email,
        ),
        expire_second=SPACE_ACCESS_EXPIRE_SECONDS,
        token_type='space_access',
    )
    return dict(
        auth=access_token,
        data=access_payload,
    )


def _issue_refresh_token(user: User):
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
    user = User.index(data['user_id'])
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


def _is_groupchat_owner(request):
    chat: Chat = request.data.chat
    return chat.group and chat.is_owner(request.user)


def _is_chat_member(request):
    chat: Chat = request.data.chat
    return chat.has_active_member(request.user)


def _is_message_owner(request):
    return request.user.id == request.data.message.user_id


def require_chat_owner():
    return analyse.request(
        _is_groupchat_owner,
        message=_("You are not the owner of this chat")
    )


def require_chat_member():
    return analyse.request(_is_chat_member, message=_("You are not a member of this chat"))


def require_message_owner():
    return analyse.request(_is_message_owner, message=_("You are not the owner of this message"))
