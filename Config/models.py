from django.db import models
from smartdjango import Error

from Config.validators import ConfigValidator, ConfigErrors


class Config(models.Model):
    vldt = ConfigValidator

    key = models.CharField(
        max_length=vldt.MAX_KEY_LENGTH,
        unique=True,
        validators=[vldt.key],
    )
    value = models.CharField(
        max_length=vldt.MAX_VALUE_LENGTH,
        validators=[vldt.value],
    )

    @classmethod
    def get_config_by_key(cls, key):
        try:
            return cls.objects.get(key=key)
        except cls.DoesNotExist as err:
            raise ConfigErrors.NOT_FOUND(details=err)

    @classmethod
    def get_value_by_key(cls, key, default=None, to=None):
        try:
            value = cls.get_config_by_key(key).value
            if callable(to):
                value = to(value)
            return value
        except Error as err:
            if err == ConfigErrors.NOT_FOUND:
                return default
            raise err

    @classmethod
    def update_value(cls, key, value):
        cls.vldt.key(key)
        cls.vldt.value(value)
        try:
            config, _created = cls.objects.update_or_create(
                key=key,
                defaults=dict(value=value),
            )
            return config
        except Exception as err:
            raise ConfigErrors.CREATE(details=err)


class ConfigInstance:
    NOTIFICATOR_NAME = 'NOTIFICATOR_NAME'
    NOTIFICATOR_TOKEN = 'NOTIFICATOR_TOKEN'
    NOTIFICATOR_HOST = 'NOTIFICATOR_HOST'
    NOTIFICATOR_TIMEOUT = 'NOTIFICATOR_TIMEOUT'
    SECRET_KEY = 'SECRET_KEY'
    QINIU_ACCESS_KEY = 'QINIU_ACCESS_KEY'
    QINIU_SECRET_KEY = 'QINIU_SECRET_KEY'
    QINIU_BUCKET = 'QINIU_BUCKET'
    QINIU_DOMAIN = 'QINIU_DOMAIN'


CI = ConfigInstance
