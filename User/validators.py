import string

from django.utils.translation import gettext as _

from smartdjango import Error, Code


@Error.register
class UserErrors:
    NOT_EXISTS = Error(message=_('User ({attr}={value}) does not exist'), code=Code.NotFound)
    EXISTS = Error(message=_('User already exists'), code=Code.BadRequest)
    EMPTY_NAME = Error(message=_('Name cannot be empty'), code=Code.BadRequest)
    INTERVAL_TOO_SMALL = Error(message=_('Interval should be greater than {offline_interval} minutes'), code=Code.BadRequest)
    PASSWORD_TOO_SHORT = Error(message=_('Password should be at least {password_length} characters long'), code=Code.BadRequest)
    PASSWORD_ERROR = Error(message=_('Password error'), code=Code.BadRequest)
    SPACE_IN_NAME = Error(message=_('Name cannot contain spaces'), code=Code.BadRequest)
    SUBDOMAIN_TOO_SHORT = Error(message=_('Subdomain should be at least {min_length} characters long'), code=Code.BadRequest)
    SUBDOMAIN_INVALID = Error(message=_('Subdomain can only contain lowercase letters and numbers'), code=Code.BadRequest)
    PASSWORD_REQUIRED = Error(message=_('Nickname is already taken, password required'), code=Code.BadRequest)
    SUBDOMAIN_REQUIRED = Error(message=_('Subdomain is required'), code=Code.BadRequest)
    SUBDOMAIN_TAKEN = Error(message=_('Subdomain is already taken'), code=Code.BadRequest)
    SUBDOMAIN_RESERVED = Error(message=_('Subdomain is reserved'), code=Code.BadRequest)
    GUEST_DELETED = Error(message=_('Guest has been deleted'), code=Code.BadRequest)
    GUEST_FORBIDDEN = Error(message=_('Guest does not belong to this host'), code=Code.Forbidden)
    SPACE_FORBIDDEN = Error(message=_('Users are not in the same space'), code=Code.Forbidden)
    FRIEND_INVALID = Error(message=_('Invalid friend target'), code=Code.BadRequest)
    FRIEND_ALREADY = Error(message=_('You are already friends'), code=Code.BadRequest)
    FRIEND_REQUEST_FORBIDDEN = Error(message=_('You are not allowed to perform this friend request action'), code=Code.Forbidden)
    FRIEND_REQUEST_EXISTS = Error(message=_('A pending friend request already exists'), code=Code.BadRequest)
    FRIEND_REQUEST_CLOSED = Error(message=_('This friend request is not pending'), code=Code.BadRequest)
    EMAIL_CODE_INVALID = Error(message=_('Invalid email verification code'), code=Code.BadRequest)
    EMAIL_CODE_EXPIRED = Error(message=_('Email verification code expired'), code=Code.BadRequest)
    EMAIL_SEND_FAILED = Error(message=_('Failed to send verification email'), code=Code.InternalServerError)


@Error.register
class ConfigErrors:
    CREATE = Error(message=_('Failed to update config'), code=Code.InternalServerError)
    NOT_FOUND = Error(message=_('Config not found'), code=Code.NotFound)
    KEY_TOO_LONG = Error(message=_('Config key too long, max length is {key_length}'), code=Code.BadRequest)
    VALUE_TOO_LONG = Error(message=_('Config value too long, max length is {value_length}'), code=Code.BadRequest)


RESERVED_SUBDOMAINS = {
    'api', 'www', 'admin', 'static', 'cdn', 'mail', 'smtp', 'imap', 'pop',
    'ftp', 'docs', 'status', 'support', 'help', 'blog', 'dev', 'test', 'staging'
}


def is_reserved_subdomain(value: str) -> bool:
    return value in RESERVED_SUBDOMAINS


class BaseUserValidator:
    OFFLINE_MIN_INTERVAL = 5
    PASSWORD_MIN_LENGTH = 6
    PASSWORD_MAX_LENGTH = 64
    DESCRIPTION_MAX_LENGTH = 100
    SALT_MAX_LENGTH = 32
    NAME_MAX_LENGTH = 20
    SUBDOMAIN_MAX_LENGTH = 15
    SUBDOMAIN_MIN_LENGTH = 3
    SUBDOMAIN_RANDOM_LENGTH = 5

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
    def subdomain(cls, value):
        if len(value) < cls.SUBDOMAIN_MIN_LENGTH:
            raise UserErrors.SUBDOMAIN_TOO_SHORT(min_length=cls.SUBDOMAIN_MIN_LENGTH)
        allow_string = string.ascii_lowercase + string.digits
        if not all(c in allow_string for c in value):
            raise UserErrors.SUBDOMAIN_INVALID


class ConfigValidator:
    MAX_KEY_LENGTH = 255
    MAX_VALUE_LENGTH = 255

    @classmethod
    def key(cls, value):
        if len(value) > cls.MAX_KEY_LENGTH:
            raise ConfigErrors.KEY_TOO_LONG(key_length=cls.MAX_KEY_LENGTH)

    @classmethod
    def value(cls, value):
        if len(value) > cls.MAX_VALUE_LENGTH:
            raise ConfigErrors.VALUE_TOO_LONG(value_length=cls.MAX_VALUE_LENGTH)
