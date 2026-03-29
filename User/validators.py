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
    OFFICIAL_LOGIN_TICKET_INVALID = Error(message=_('Invalid official login ticket'), code=Code.BadRequest)
    OFFICIAL_LOGIN_TICKET_EXPIRED = Error(message=_('Official login ticket expired'), code=Code.BadRequest)
    AVATAR_PRESET_INVALID = Error(message=_('Invalid avatar preset id'), code=Code.BadRequest)
    AVATAR_STORAGE_NOT_CONFIGURED = Error(message=_('Avatar storage is not configured'), code=Code.InternalServerError)
    AVATAR_FILE_TYPE_INVALID = Error(message=_('Invalid avatar image type'), code=Code.BadRequest)
    AVATAR_KEY_INVALID = Error(message=_('Invalid avatar key'), code=Code.BadRequest)
    AVATAR_DELETE_FAILED = Error(message=_('Failed to delete previous avatar'), code=Code.InternalServerError)


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
    LANGUAGE_MAX_LENGTH = 16
    DEFAULT_LANGUAGE = 'en'
    SUPPORTED_LANGUAGES = {'en', 'zh-CN'}
    LANGUAGE_ALIASES = {
        'en': 'en',
        'en-us': 'en',
        'en_us': 'en',
        'zh-cn': 'zh-CN',
        'zh_cn': 'zh-CN',
    }
    AVATAR_PRESET_MIN_ID = 1
    AVATAR_PRESET_MAX_ID = 80

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
    def welcome_message(cls, value):
        message = (value or '').strip()
        if not message:
            raise UserErrors.WELCOME_MESSAGE_EMPTY
        if len(message) > cls.WELCOME_MESSAGE_MAX_LENGTH:
            raise UserErrors.WELCOME_MESSAGE_TOO_LONG
        return message

    @classmethod
    def avatar_preset_id(cls, value):
        preset_id = int(value)
        if not (cls.AVATAR_PRESET_MIN_ID <= preset_id <= cls.AVATAR_PRESET_MAX_ID):
            raise UserErrors.AVATAR_PRESET_INVALID
        return preset_id
