from django.utils.text import format_lazy
from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from User.models import (
    User,
    UserNotificationChoice,
    NotificationPreference,
    EmailVerificationCode,
    UserContactVerificationCode,
)
from User.validators import UserValidator


class UserParams(metaclass=Params):
    model_class = User

    user_id = Validator('user_id', final_name='user').to(int).to(User.index)
    name: Validator
    lower_name: Validator
    password: Validator
    welcome_message: Validator
    avatar_preset_id = Validator('avatar_preset_id') \
        .to(int) \
        .to(UserValidator.avatar_preset_id)
    language = Validator('language') \
        .to(str) \
        .null().default(None) \
        .bool(lambda x: x is not None, message=_('language is required')) \
        .to(UserValidator.language)


class AuthParams(metaclass=Params):
    refresh = Validator('refresh') \
        .to(str) \
        .bool(lambda x: len(x) > 0, message=_('Empty refresh token'))


class NotificationPreferenceParams(metaclass=Params):
    model_class = NotificationPreference

    channel = Validator('channel') \
        .to(int) \
        .bool(
            lambda x: x in (
                UserNotificationChoice.EMAIL,
                UserNotificationChoice.SMS,
                UserNotificationChoice.BARK,
            ),
            message=_('Invalid notification channel')
        )
    enabled = Validator('enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('enabled should be 0 or 1'))
    offline_threshold_minutes = Validator('offline_threshold_minutes') \
        .to(int) \
        .null().default(None) \
        .bool(
            lambda x: x is None or 1 <= x <= 10080,
            message=_('offline_threshold_minutes should be between 1 and 10080')
        )


class EmailVerificationCodeParams(metaclass=Params):
    model_class = EmailVerificationCode

    email: Validator
    code: Validator
    password = Validator('password') \
        .to(str) \
        .bool(
            lambda x: UserValidator.PASSWORD_MIN_LENGTH <= len(x) <= UserValidator.PASSWORD_MAX_LENGTH,
            message=format_lazy(
                _('Password should be at least {password_length} characters long'),
                password_length=UserValidator.PASSWORD_MIN_LENGTH,
            ),
        )


class UserContactVerificationCodeParams(metaclass=Params):
    model_class = UserContactVerificationCode

    channel = NotificationPreferenceParams.channel.copy()
    target: Validator
    code: Validator
