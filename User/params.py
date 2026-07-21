from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from User.models import (
    User,
    UserNotificationChoice,
    NotificationPreference,
    UserWebReminderPreference,
    UserContactVerificationCode,
    PushDevice,
)


class UserParams(metaclass=Params):
    model_class = User

    user_id = Validator('user_id', final_name='user').to(int).to(User.index)
    admin_user_id = Validator('user_id', final_name='user').to(int).to(User.index_any)
    name: Validator
    lower_name: Validator
    password: Validator
    welcome_message: Validator
    avatar_preset_id = Validator('avatar_preset_id') \
        .to(int) \
        .to(User.validators.avatar_preset_id)
    avatar_key = Validator('key') \
        .to(str)
    avatar_file_name = Validator('file_name') \
        .to(str)
    avatar_content_type = Validator('content_type') \
        .to(str) \
        .null().default(None)
    language = Validator('language') \
        .to(str) \
        .null().default(None) \
        .bool(lambda x: x is not None, message=_('language is required')) \
        .to(User.normalizers.language) \
        .exception(User.validators.language)


class AuthParams(metaclass=Params):
    refresh = Validator('refresh') \
        .to(str) \
        .bool(lambda x: len(x) > 0, message=_('Empty refresh token'))


class UserPasswordParams(metaclass=Params):
    old_password = UserParams.password.copy().rename('old_password', final_name='old_password') \
        .null().default(None)
    new_password = UserParams.password.copy().rename('new_password', final_name='new_password') \
        .null().default(None)


class UserDeleteParams(metaclass=Params):
    password = Validator('password') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip())
    name_confirmation = Validator('name_confirmation') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip())


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
    hide_message_content = Validator('hide_message_content') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('hide_message_content should be 0 or 1'))
    hidden_direct_message_text = Validator('hidden_direct_message_text') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(
            lambda x: x is None or len(x) <= 255,
            message=_('hidden_direct_message_text should be at most 255 characters')
        )
    hidden_group_message_text = Validator('hidden_group_message_text') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(
            lambda x: x is None or len(x) <= 255,
            message=_('hidden_group_message_text should be at most 255 characters')
        )
    open_chat_on_tap = Validator('open_chat_on_tap') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('open_chat_on_tap should be 0 or 1'))


class UserWebReminderPreferenceParams(metaclass=Params):
    model_class = UserWebReminderPreference

    sound_enabled = Validator('sound_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('sound_enabled should be 0 or 1'))
    title_enabled = Validator('title_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('title_enabled should be 0 or 1'))


class UserContactVerificationCodeParams(metaclass=Params):
    model_class = UserContactVerificationCode

    channel = NotificationPreferenceParams.channel.copy()
    target: Validator
    code: Validator


class PushDeviceParams(metaclass=Params):
    model_class = PushDevice

    provider = Validator('provider') \
        .to(str) \
        .null().default(PushDevice.PROVIDER_GETUI) \
        .to(lambda x: (x or '').strip().lower()) \
        .bool(lambda x: x == PushDevice.PROVIDER_GETUI, message=_('Invalid push provider'))
    client_id = Validator('client_id') \
        .to(str) \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: 0 < len(x) <= 128, message=_('Invalid push client id'))
    platform = Validator('platform') \
        .to(str) \
        .null().default(PushDevice.PLATFORM_ANDROID) \
        .to(lambda x: (x or '').strip().lower()) \
        .bool(lambda x: 0 < len(x) <= 32, message=_('Invalid push platform'))
    device_id = Validator('device_id') \
        .to(str) \
        .null().default('') \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: len(x) <= 128, message=_('Invalid device id'))
    app_version = Validator('app_version') \
        .to(str) \
        .null().default('') \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: len(x) <= 32, message=_('Invalid app version'))
