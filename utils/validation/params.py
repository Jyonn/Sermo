from typing import Type

from django.db import models

from utils.validation.validator import Validator


class Params(type):
    model_class: Type[models.Model]
    __params = None

    def __getattr__(cls, field_name):
        if cls.__params is None:
            cls.__params = {}

        if field_name not in cls.__params:
            field = cls.model_class._meta.get_field(field_name)
            validator = Validator.from_field(field, name=field.name, verbose_name=field.verbose_name)
            cls.__params[field_name] = validator
        return cls.__params[field_name]
