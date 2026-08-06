import string

from django.utils.translation import gettext_lazy as _

from smartdjango import Error, Code


@Error.register
class UserErrors:
    NOT_EXISTS = Error(message=_('User ({attr}={value}) does not exist'), code=Code.NotFound)
    EXISTS = Error(message=_('User already exists'), code=Code.BadRequest)
    EMPTY_NAME = Error(message=_('Name cannot be empty'), code=Code.BadRequest)
    INTERVAL_TOO_SMALL = Error(message=_('Interval should be greater than {offline_interval} minutes'), code=Code.BadRequest)
    PASSWORD_TOO_SHORT = Error(message=_('Password should be at least {password_length} characters long'), code=Code.BadRequest)
    PASSWORD_ERROR = Error(message=_('Password error'), code=Code.BadRequest)
    OLD_PASSWORD_REQUIRED = Error(message=_('old_password is required'), code=Code.BadRequest)
    PASSWORD_NOT_SET = Error(message=_('Password is not set'), code=Code.Forbidden)
    SPACE_IN_NAME = Error(message=_('Name cannot contain spaces'), code=Code.BadRequest)
    SPACE_SLUG_TOO_SHORT = Error(message=_('Space slug should be at least {min_length} characters long'), code=Code.BadRequest)
    SPACE_SLUG_INVALID = Error(message=_('Space slug can only contain lowercase letters, numbers and hyphens'), code=Code.BadRequest)
    PASSWORD_REQUIRED = Error(message=_('Nickname is already taken, password required'), code=Code.BadRequest)
    ACCOUNT_DELETE_PASSWORD_REQUIRED = Error(message=_('Password is required to delete account'), code=Code.BadRequest)
    ACCOUNT_DELETE_NAME_CONFIRMATION_REQUIRED = Error(message=_('Name confirmation is required to delete account'), code=Code.BadRequest)
    ACCOUNT_DELETE_NAME_CONFIRMATION_MISMATCH = Error(message=_('Name confirmation does not match'), code=Code.BadRequest)
    SPACE_SLUG_REQUIRED = Error(message=_('Space slug is required'), code=Code.BadRequest)
    SPACE_SLUG_TAKEN = Error(message=_('Space slug is already taken'), code=Code.BadRequest)
    SPACE_SLUG_RESERVED = Error(message=_('Space slug is reserved'), code=Code.BadRequest)
    USER_DELETED = Error(message=_('User has been deleted'), code=Code.BadRequest)
    USER_OFFICIAL_REMOVE_FORBIDDEN = Error(message=_('Official account cannot be removed'), code=Code.BadRequest)
    USER_FORBIDDEN = Error(message=_('User does not belong to this space'), code=Code.Forbidden)
    SPACE_FORBIDDEN = Error(message=_('Users are not in the same space'), code=Code.Forbidden)
    SPACE_MEMBER_LIMIT_REACHED = Error(message=_('This space has reached its member limit'), code=Code.BadRequest)
    LANGUAGE_INVALID = Error(message=_('Unsupported language'), code=Code.BadRequest)
    WELCOME_MESSAGE_EMPTY = Error(message=_('Welcome message cannot be empty'), code=Code.BadRequest)
    WELCOME_MESSAGE_TOO_LONG = Error(message=_('Welcome message is too long'), code=Code.BadRequest)
    CONTACT_CODE_INVALID = Error(message=_('Invalid contact verification code'), code=Code.BadRequest)
    CONTACT_CODE_EXPIRED = Error(message=_('Contact verification code expired'), code=Code.BadRequest)
    CONTACT_CHANNEL_INVALID = Error(message=_('Invalid contact channel'), code=Code.BadRequest)
    CONTACT_SEND_FAILED = Error(message=_('Failed to send contact verification message'), code=Code.InternalServerError)
    CONTACT_ALREADY_BOUND = Error(message=_('Contact is already bound in this space'), code=Code.BadRequest)
    CONTACT_NOT_BOUND = Error(message=_('Contact is not bound'), code=Code.BadRequest)
    CONTACT_UNBIND_TARGET_MISMATCH = Error(message=_('Contact verification target does not match current binding'), code=Code.BadRequest)
    CONTACT_UNBIND_TOO_FREQUENT = Error(message=_('Contact cannot be unbound again until {available_at}'), code=Code.BadRequest)
    PRIVATE_ACCOUNT_CONTACTS_REQUIRED = Error(message=_('Verified phone is required for a private account'), code=Code.BadRequest)
    ACCOUNT_SWITCH_FORBIDDEN = Error(message=_('Account switch is not allowed'), code=Code.Forbidden)
    ACCOUNT_SWITCH_TICKET_INVALID = Error(message=_('Invalid account switch ticket'), code=Code.BadRequest)
    ACCOUNT_SWITCH_TICKET_EXPIRED = Error(message=_('Account switch ticket expired'), code=Code.BadRequest)
    OFFICIAL_LOGIN_TICKET_INVALID = Error(message=_('Invalid official login ticket'), code=Code.BadRequest)
    OFFICIAL_LOGIN_TICKET_EXPIRED = Error(message=_('Official login ticket expired'), code=Code.BadRequest)
    AVATAR_PRESET_INVALID = Error(message=_('Invalid avatar preset id'), code=Code.BadRequest)
    AVATAR_STORAGE_NOT_CONFIGURED = Error(message=_('Avatar storage is not configured'), code=Code.InternalServerError)
    AVATAR_FILE_TYPE_INVALID = Error(message=_('Invalid avatar image type'), code=Code.BadRequest)
    AVATAR_KEY_INVALID = Error(message=_('Invalid avatar key'), code=Code.BadRequest)
    AVATAR_DELETE_FAILED = Error(message=_('Failed to delete previous avatar'), code=Code.InternalServerError)
    CHAT_BACKGROUND_FILE_TYPE_INVALID = Error(message=_('Invalid chat background image type'), code=Code.BadRequest)
    CHAT_BACKGROUND_KEY_INVALID = Error(message=_('Invalid chat background key'), code=Code.BadRequest)
    CHAT_BACKGROUND_THEME_INVALID = Error(message=_('Invalid chat background theme'), code=Code.BadRequest)
    PERSONALIZATION_INVALID = Error(message=_('Invalid personalization option'), code=Code.BadRequest)
    WEB_PUSH_SUBSCRIPTION_INVALID = Error(message=_('Invalid web push subscription'), code=Code.BadRequest)
    EMAIL_NOT_VERIFIED = Error(message=_('Email is not verified'), code=Code.Forbidden)
    GESTURE_LOCK_PAYLOAD_INVALID = Error(message=_('Invalid gesture lock payload'), code=Code.BadRequest)
    GROWTH_LEVEL_REQUIRED = Error(message=_('Level {level} is required for this feature'), code=Code.Forbidden)
    GROWTH_ACKNOWLEDGEMENT_INVALID = Error(message=_('Growth level acknowledgement is out of sequence'), code=Code.BadRequest)
    PERMANENT_VIP_NOT_ELIGIBLE = Error(message=_('Permanent VIP requirements are not met'), code=Code.Forbidden)
    PERMANENT_VIP_CAMPAIGN_FULL = Error(message=_('Permanent VIP campaign is full'), code=Code.BadRequest)
    NICKNAME_CHANGE_COOLDOWN = Error(message=_('Nickname can be changed again at {available_at}'), code=Code.BadRequest)
    PASSWORD_RECOVERY_UNAVAILABLE = Error(message=_('Password recovery is unavailable for this account'), code=Code.BadRequest)
    PASSWORD_RECOVERY_CHANNEL_INVALID = Error(message=_('Invalid password recovery channel'), code=Code.BadRequest)
    PASSWORD_RECOVERY_TOO_FREQUENT = Error(message=_('Please wait before requesting another recovery code'), code=Code.BadRequest)
    PASSWORD_RECOVERY_CODE_INVALID = Error(message=_('Invalid password recovery code'), code=Code.BadRequest)
    PASSWORD_RECOVERY_CODE_EXPIRED = Error(message=_('Password recovery code expired'), code=Code.BadRequest)
    PASSWORD_RECOVERY_ATTEMPTS_EXCEEDED = Error(message=_('Too many password recovery attempts'), code=Code.BadRequest)
    PASSWORD_RECOVERY_TOKEN_INVALID = Error(message=_('Invalid password reset token'), code=Code.BadRequest)
    PASSWORD_RECOVERY_TOKEN_EXPIRED = Error(message=_('Password reset token expired'), code=Code.BadRequest)


RESERVED_SPACE_SLUGS = {
    'api', 'www', 'admin', 'static', 'cdn', 'mail', 'smtp', 'imap', 'pop',
    'ftp', 'docs', 'status', 'support', 'help', 'blog', 'dev', 'test', 'staging'
}


def is_reserved_space_slug(value: str) -> bool:
    return value in RESERVED_SPACE_SLUGS


class UserValidator:
    OFFLINE_MIN_INTERVAL = 5
    PASSWORD_MIN_LENGTH = 6
    PASSWORD_MAX_LENGTH = 64
    DESCRIPTION_MAX_LENGTH = 100
    SALT_MAX_LENGTH = 32
    NAME_MAX_LENGTH = 20
    SPACE_SLUG_MAX_LENGTH = 15
    SPACE_SLUG_MIN_LENGTH = 3
    SPACE_SLUG_RANDOM_LENGTH = 5
    WELCOME_MESSAGE_MAX_LENGTH = 500
    PLAZA_GREETING_MAX_LENGTH = 30
    LANGUAGE_MAX_LENGTH = 16
    DEFAULT_LANGUAGE = 'en'
    SUPPORTED_LANGUAGES = {'en', 'zh-CN'}
    LANGUAGE_PREFERENCES = {'system', 'en', 'zh-CN'}
    LANGUAGE_ALIASES = {
        'en': 'en',
        'en-us': 'en',
        'en_us': 'en',
        'zh-cn': 'zh-CN',
        'zh_cn': 'zh-CN',
    }
    AVATAR_PRESET_MIN_ID = 1
    AVATAR_PRESET_MAX_ID = 80
    CHAT_BACKGROUND_THEMES = {
        'default', 'paper', 'mint', 'dusk', 'comic', 'zen', 'hero', 'dragon', 'bauhaus', 'mosaic',
        'tidepool', 'forest', 'desert', 'snowfield', 'sakura', 'sunrise', 'midnight', 'rain', 'galaxy',
        'aurora-sky', 'linen', 'terrazzo', 'blueprint', 'newsprint', 'hologram', 'arcade', 'jazz',
        'spaceport', 'candy', 'noir-film', 'custom',
    }
    PERSONALIZATION_OPTIONS = {
        'chat_bubble_style': {
            'default', 'comic', 'vip', 'zen', 'hero', 'dragon', 'bauhaus', 'mosaic',
            'typewriter', 'newspaper', 'receipt', 'sticker', 'toybrick', 'niko', 'fufu', 'xiaobai',
        },
        'avatar_frame_style': {
            'none', 'orbit', 'aurora', 'polaroid', 'soundwave', 'portal', 'butterfly',
            'moon', 'camera', 'comet', 'snowfall', 'papercut', 'mechanical', 'niko-run', 'fufu-wave', 'xiaobai-run', 'vip',
        },
        'square_outfit_style': {'sunset', 'varsity', 'noir', 'cloud', 'raincoat', 'hanfu', 'utility', 'sailor'},
        'square_prop_style': {'none', 'star', 'coffee', 'flag', 'camera', 'bouquet', 'umbrella', 'skateboard'},
        'square_motion_style': {'walk', 'bounce', 'float', 'dash', 'wave', 'dance', 'skate', 'tiptoe'},
        'square_limb_style': {'line', 'chunky', 'robot', 'ribbon', 'hinged', 'wooden', 'spring', 'ink'},
    }
    GESTURE_LOCK_MIN_MINUTES = 1
    GESTURE_LOCK_MAX_MINUTES = 30

    @staticmethod
    def name(value):
        if value.strip() != value:
            raise UserErrors.SPACE_IN_NAME
        if not value:
            raise UserErrors.EMPTY_NAME

    @classmethod
    def offline_notification_interval(cls, value):
        if value < cls.OFFLINE_MIN_INTERVAL:
            raise UserErrors.INTERVAL_TOO_SMALL(offline_interval=cls.OFFLINE_MIN_INTERVAL)

    @classmethod
    def password(cls, value):
        if len(value) < cls.PASSWORD_MIN_LENGTH:
            raise UserErrors.PASSWORD_TOO_SHORT(password_length=cls.PASSWORD_MIN_LENGTH)

    @classmethod
    def space_slug(cls, value):
        if len(value) < cls.SPACE_SLUG_MIN_LENGTH:
            raise UserErrors.SPACE_SLUG_TOO_SHORT(min_length=cls.SPACE_SLUG_MIN_LENGTH)
        allow_string = string.ascii_lowercase + string.digits + '-'
        if not all(c in allow_string for c in value):
            raise UserErrors.SPACE_SLUG_INVALID

    @classmethod
    def normalize_language(cls, value):
        raw = (value or cls.DEFAULT_LANGUAGE).strip()
        if not raw:
            return cls.DEFAULT_LANGUAGE
        lower = raw.lower().replace('_', '-')
        if lower in cls.LANGUAGE_ALIASES:
            return cls.LANGUAGE_ALIASES[lower]
        return raw

    @classmethod
    def language(cls, value):
        normalized = cls.normalize_language(value)
        if normalized not in cls.SUPPORTED_LANGUAGES:
            raise UserErrors.LANGUAGE_INVALID
        return normalized

    @classmethod
    def language_preference(cls, value):
        raw = (value or 'system').strip()
        if raw == 'system':
            return raw
        normalized = cls.normalize_language(raw)
        if normalized not in cls.LANGUAGE_PREFERENCES:
            raise UserErrors.LANGUAGE_INVALID
        return normalized

    @classmethod
    def welcome_message(cls, value):
        message = (value or '').strip()
        if not message:
            raise UserErrors.WELCOME_MESSAGE_EMPTY
        if len(message) > cls.WELCOME_MESSAGE_MAX_LENGTH:
            raise UserErrors.WELCOME_MESSAGE_TOO_LONG
        return message

    @classmethod
    def plaza_greeting(cls, value):
        message = (value or '').strip()
        if not message:
            raise UserErrors.WELCOME_MESSAGE_EMPTY
        if len(message) > cls.PLAZA_GREETING_MAX_LENGTH:
            raise UserErrors.WELCOME_MESSAGE_TOO_LONG
        return message

    @classmethod
    def avatar_preset_id(cls, value):
        preset_id = int(value)
        if not (cls.AVATAR_PRESET_MIN_ID <= preset_id <= cls.AVATAR_PRESET_MAX_ID):
            raise UserErrors.AVATAR_PRESET_INVALID
        return preset_id

    @classmethod
    def chat_background_theme(cls, value):
        theme = (value or '').strip().lower()
        if theme not in cls.CHAT_BACKGROUND_THEMES:
            raise UserErrors.CHAT_BACKGROUND_THEME_INVALID
        return theme

    @classmethod
    def personalization(cls, field, value):
        normalized = (value or '').strip().lower()
        if normalized not in cls.PERSONALIZATION_OPTIONS[field]:
            raise UserErrors.PERSONALIZATION_INVALID
        return normalized
