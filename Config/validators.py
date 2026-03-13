from django.utils.translation import gettext as _
from smartdjango import Error, Code


@Error.register
class ConfigErrors:
    CREATE = Error(message=_('Failed to update config'), code=Code.InternalServerError)
    NOT_FOUND = Error(message=_('Config not found'), code=Code.NotFound)
    KEY_TOO_LONG = Error(message=_('Config key too long, max length is {key_length}'), code=Code.BadRequest)
    VALUE_TOO_LONG = Error(message=_('Config value too long, max length is {value_length}'), code=Code.BadRequest)


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
