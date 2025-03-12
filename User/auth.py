import datetime
from typing import Optional

import jwt

from django.utils.translation import gettext as _

from Chat.models import SingleChat, GroupChat
from Sermo.settings import SECRET_KEY
from User.models import BaseUser, UserRoleChoice
from utils import analyse
from utils.analyse import get_request
from utils.code import Code
from utils.error import Error
from utils.validation.dict_validator import DictValidator
from utils.validation.validator import Validator


@Error.register
class AuthErrors:
    EXPIRED = Error(_('Token expired'), code=Code.Unauthorized)
    FORMAT = Error(_('Incorrect format'), code=Code.BadRequest)


class Symbols:
    EXPIRE = 'expire'
    TIME = 'time'
    ALGORITHM = 'HS256'


def encrypt(data: dict, expire_second=7 * 60 * 60 * 24):
    data[Symbols.TIME] = datetime.datetime.now().timestamp()
    data[Symbols.EXPIRE] = expire_second
    encode_str = jwt.encode(data, key=SECRET_KEY, algorithm=Symbols.ALGORITHM)
    if isinstance(encode_str, bytes):
        encode_str = encode_str.decode()
    return dict(
        auth=encode_str,
        data=data,
    )


def decrypt(data_str: str):
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
    return data


def _require_user(func, user_role: Optional[int]):
    def wrapper(*args, **kwargs):
        request = get_request(*args)
        token = request.headers.get('Authorization')
        if not token:
            raise AuthErrors.FORMAT(details=_('Missing authorization header'))
        data = decrypt(token)

        user = BaseUser.jwt_login(data)
        request.user = user

        if user_role is not None and user.role != user_role:
            raise AuthErrors.FORMAT(details=_('Invalid user role'))

        return func(*args, **kwargs)
    return wrapper


def require_host_user(func):
    return _require_user(func, user_role=UserRoleChoice.HOST)


def require_guest_user(func):
    return _require_user(func, user_role=UserRoleChoice.GUEST)


def require_user(func):
    return _require_user(func, user_role=None)


def get_login_token(user: BaseUser):
    auth_data = user.jwt_json()
    return encrypt(auth_data)


def _is_chat_host(request):
    return request.user == request.data.chat.host


def _is_singlechat_guest(request):
    return request.user == request.data.chat.guest


def _is_groupchat_guest(request):
    return request.user in request.data.chat.guests.all()


def _is_singlechat_member(request):
    return _is_chat_host(request) or _is_singlechat_guest(request)


def _is_groupchat_member(request):
    return _is_chat_host(request) or _is_groupchat_guest(request)


def _is_chat_member(request):
    return _is_chat_host(request) \
        or (isinstance(request.data.chat, SingleChat) and _is_singlechat_guest(request)) \
        or (isinstance(request.data.chat, GroupChat) and _is_groupchat_guest(request))


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
