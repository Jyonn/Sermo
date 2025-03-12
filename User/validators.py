from django.utils.translation import gettext as _

from utils.error import Error, Code


@Error.register
class UserErrors:
    NOT_EXISTS = Error(message=_('User ({attr}={value}) does not exist'), code=Code.NotFound)
    EXISTS = Error(message=_('User already exists'), code=Code.BadRequest)
    EMPTY_NAME = Error(message=_('Name cannot be empty'), code=Code.BadRequest)
    INTERVAL_TOO_SMALL = Error(message=_('Interval should be greater than {offline_interval} minutes'), code=Code.BadRequest)
    PASSWORD_TOO_SHORT = Error(message=_('Password should be at least {password_length} characters long'), code=Code.BadRequest)
    PASSWORD_ERROR = Error(message=_('Password error'), code=Code.BadRequest)
    SPACE_IN_NAME = Error(message=_('Name cannot contain spaces'), code=Code.BadRequest)


class BaseUserValidator:
    OFFLINE_MIN_INTERVAL = 5
    PASSWORD_MIN_LENGTH = 6
    PASSWORD_MAX_LENGTH = 64
    SALT_MAX_LENGTH = 32
    NAME_MAX_LENGTH = 20

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
