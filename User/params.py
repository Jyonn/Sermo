from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from User.models import (
    User,
    UserNotificationChoice,
    NotificationPreference,
    NotificationAudienceChoice,
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
    plaza_greeting = Validator('plaza_greeting').to(str).to(User.validators.plaza_greeting)
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
    chat_background_theme = Validator('theme', final_name='chat_background_theme') \
        .to(str) \
        .to(User.validators.chat_background_theme)
    chat_background_key = Validator('key', final_name='chat_background_key') \
        .to(str) \
        .null().default('')
    chat_bubble_style = Validator('chat_bubble_style').to(str).to(
        lambda value: User.validators.personalization('chat_bubble_style', value)
    )
    avatar_frame_style = Validator('avatar_frame_style').to(str).to(
        lambda value: User.validators.personalization('avatar_frame_style', value)
    )
    statement_card_style = Validator('statement_card_style').to(str).null().default(None).to(
        lambda value: User.validators.personalization('statement_card_style', value)
    )
    language = Validator('language') \
        .to(str) \
        .null().default(None) \
        .bool(lambda x: x is not None, message=_('language is required')) \
        .to(User.normalizers.language) \
        .exception(User.validators.language)
    language_preference = Validator('language_preference') \
        .to(str) \
        .to(User.validators.language_preference)
    system_language = Validator('system_language') \
        .to(str) \
        .to(User.normalizers.language) \
        .exception(User.validators.language)


class AuthParams(metaclass=Params):
    refresh = Validator('refresh') \
        .to(str) \
        .bool(lambda x: len(x) > 0, message=_('Empty refresh token'))
    account_user_id = Validator('user_id').to(int)
    switch_ticket = Validator('ticket').to(str).bool(lambda x: len(x.strip()) > 0, message=_('Empty account switch ticket'))


class UserPrivateAccountParams(metaclass=Params):
    enabled = Validator('enabled').to(int).bool(lambda x: x in (0, 1), message=_('enabled should be 0 or 1'))


class UserGrowthEventParams(metaclass=Params):
    event = Validator('event').to(str).bool(
        lambda value: value == 'install_webapp',
        message=_('Invalid growth event'),
    )


class UserGrowthAcknowledgementParams(metaclass=Params):
    level = Validator('level').to(int).bool(
        lambda value: 1 <= value <= 18,
        message=_('Invalid growth level'),
    )


class UserFeatureDiscoveryParams(metaclass=Params):
    reward_id = Validator('reward_id').to(str).to(lambda value: value.strip()).bool(
        lambda value: 0 < len(value) <= 80,
        message=_('Invalid feature discovery'),
    )


class UserPasswordParams(metaclass=Params):
    old_password = UserParams.password.copy().rename('old_password', final_name='old_password') \
        .null().default(None)
    new_password = UserParams.password.copy().rename('new_password', final_name='new_password') \
        .null().default(None)


class UserPasswordRecoveryParams(metaclass=Params):
    slug = Validator('slug').to(str).to(lambda value: value.strip().lower())
    name = Validator('name').to(str).to(lambda value: value.strip())
    channel = Validator('channel').to(int)
    challenge_id = Validator('challenge_id').to(int)
    code = Validator('code').to(str).to(lambda value: value.strip())
    reset_token = Validator('reset_token').to(str).to(lambda value: value.strip())
    new_password = UserParams.password.copy().rename('new_password', final_name='new_password')


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
    hidden_direct_message_title = Validator('hidden_direct_message_title') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(
            lambda x: x is None or len(x) <= 80,
            message=_('hidden_direct_message_title should be at most 80 characters')
        )
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
    hidden_group_message_title = Validator('hidden_group_message_title') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(
            lambda x: x is None or len(x) <= 80,
            message=_('hidden_group_message_title should be at most 80 characters')
        )
    friend_online_message_title = Validator('friend_online_message_title') \
        .to(str) \
        .null().default(None) \
        .to(lambda x: None if x is None else x.strip()) \
        .bool(
            lambda x: x is None or len(x) <= 80,
            message=_('friend_online_message_title should be at most 80 characters')
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
    bark_icon_mode = Validator('bark_icon_mode') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1, 2), message=_('bark_icon_mode should be 0, 1 or 2'))


class NotificationTopicPreferenceParams(metaclass=Params):
    channel = Validator('channel').to(int).bool(
        lambda value: value in range(4), message=_('Invalid notification channel'),
    )
    topic = Validator('topic').to(int).bool(
        lambda value: value in range(1, 7), message=_('Invalid notification topic'),
    )
    audience = Validator('audience').to(int).bool(
        lambda value: value in (
            NotificationAudienceChoice.ANY,
            NotificationAudienceChoice.FRIEND,
            NotificationAudienceChoice.OTHER,
        ),
        message=_('Invalid notification audience'),
    )
    enabled = Validator('enabled').to(int).bool(
        lambda value: value in (0, 1), message=_('enabled should be 0 or 1'),
    )


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


class UserContactUnbindParams(metaclass=Params):
    channel = NotificationPreferenceParams.channel.copy()
    code = UserContactVerificationCodeParams.code.copy().null().default(None)


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
