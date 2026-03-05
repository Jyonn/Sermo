from django.utils.translation import gettext as _
from smartdjango import Params, Validator

from User.models import BaseUser, HostUser, GuestUser
from User.validators import BaseUserValidator


class BaseUserParams(metaclass=Params):
    model_class = BaseUser

    name: Validator
    lower_name: Validator

    offline_notification_interval: Validator
    notification_choice: Validator

    is_online: Validator
    last_heartbeat: Validator

    email: Validator
    phone: Validator
    bark: Validator

    password: Validator

    user_id: Validator


class HostUserParams(BaseUserParams):
    model_class = HostUser


class GuestUserParams(BaseUserParams):
    model_class = GuestUser

    host_id: Validator
    guest_id: Validator


class AuthParams(metaclass=Params):
    refresh = Validator('refresh') \
        .to(str) \
        .bool(lambda x: len(x) > 0, message=_('Empty refresh token'))


class SubdomainParams(metaclass=Params):
    subdomain = Validator('subdomain').to(str)


class GuestListParams(metaclass=Params):
    q = Validator('q').to(str).null().default(None)
    online = Validator('online') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: True if x is None else x in (0, 1), message=_('online should be 0 or 1'))
    limit = Validator('limit') \
        .to(int) \
        .null().default(50) \
        .bool(lambda x: 1 <= x <= 200, message=_('limit should be between 1 and 200'))
    offset = Validator('offset') \
        .to(int) \
        .null().default(0) \
        .bool(lambda x: x >= 0, message=_('offset should be greater than or equal to 0'))


class GuestDeleteParams(metaclass=Params):
    purge_group_messages = Validator('purge_group_messages') \
        .to(int) \
        .null().default(0) \
        .bool(lambda x: x in (0, 1), message=_('purge_group_messages should be 0 or 1'))


HostUserParams.user_id = Validator('user_id', final_name='user').to(int).to(HostUser.index)
GuestUserParams.user_id = Validator('user_id', final_name='user').to(int).to(GuestUser.index)
GuestUserParams.guest_id = Validator('guest_id', final_name='guest').to(int).to(GuestUser.index)
GuestUserParams.host_id = Validator('host_id', final_name='host').to(int).to(HostUser.index)
GuestUserParams.password = Validator('password') \
    .to(str) \
    .null().default('') \
    .bool(
        lambda x: x == '' or (BaseUserValidator.PASSWORD_MIN_LENGTH <= len(x) <= BaseUserValidator.PASSWORD_MAX_LENGTH),
        message=_('Password should be at least {password_length} characters long').format(
            password_length=BaseUserValidator.PASSWORD_MIN_LENGTH
        )
    )
