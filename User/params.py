from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from User.models import (
    User,
    UserNotificationChoice,
    NotificationPreference,
    UserGestureLockPreference,
    UserWebReminderPreference,
    UserContactVerificationCode,
    WebPushSubscription,
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
    friend_online_message_text = Validator('friend_online_message_text') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(
            lambda x: x is None or len(x) <= 255,
            message=_('friend_online_message_text should be at most 255 characters')
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


class UserGestureLockPreferenceParams(metaclass=Params):
    model_class = UserGestureLockPreference

    enabled = Validator('enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('enabled should be 0 or 1'))
    pattern_hash = Validator('pattern_hash') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(lambda x: x is None or 0 < len(x) <= 128, message=_('Invalid gesture lock payload'))
    salt = Validator('salt') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(lambda x: x is None or 0 < len(x) <= 64, message=_('Invalid gesture lock payload'))
    decoy_enabled = Validator('decoy_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('decoy_enabled should be 0 or 1'))
    decoy_pattern_hash = Validator('decoy_pattern_hash') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(lambda x: x is None or 0 < len(x) <= 128, message=_('Invalid gesture lock payload'))
    decoy_salt = Validator('decoy_salt') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(lambda x: x is None or 0 < len(x) <= 64, message=_('Invalid gesture lock payload'))
    lock_after_minutes = Validator('lock_after_minutes') \
        .to(int) \
        .null().default(None) \
        .bool(
            lambda x: x is None or User.validators.GESTURE_LOCK_MIN_MINUTES <= x <= User.validators.GESTURE_LOCK_MAX_MINUTES,
            message=_('lock_after_minutes should be between 1 and 30')
        )


class UserContactVerificationCodeParams(metaclass=Params):
    model_class = UserContactVerificationCode

    channel = NotificationPreferenceParams.channel.copy()
    target: Validator
    code: Validator


class WebPushSubscriptionParams(metaclass=Params):
    model_class = WebPushSubscription

    endpoint = Validator('endpoint') \
        .to(str) \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: 0 < len(x) <= 2048 and x.startswith('https://'), message=_('Invalid push endpoint'))
    p256dh = Validator('p256dh') \
        .to(str) \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: 0 < len(x) <= 255, message=_('Invalid push key'))
    auth = Validator('auth') \
        .to(str) \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: 0 < len(x) <= 255, message=_('Invalid push auth secret'))
    origin = Validator('origin') \
        .to(str) \
        .to(lambda x: (x or '').strip()) \
        .bool(lambda x: 0 < len(x) <= 255, message=_('Invalid push origin'))
