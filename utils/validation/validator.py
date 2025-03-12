import datetime
from typing import Callable, Union

from django.core.validators import MaxLengthValidator, BaseValidator
from django.db import models
from django.utils.translation import gettext as _

from utils.code import Code
from utils.error import Error
from utils.validation.key import Key


@Error.register
class ValidatorErrors:
    NO_DEFAULT = Error(_('No default value'), code=Code.BadRequest)
    NULL_NOT_ALLOW = Error(_('Null is not allowed'), code=Code.BadRequest)
    NOT_VALID = Error(_('Not valid: {message}'), code=Code.BadRequest)
    VALIDATOR_CRUSHED = Error(_('Validator crushed'), code=Code.InternalServerError)
    PROCESSOR_CRUSHED = Error(_('Processor crushed'), code=Code.InternalServerError)
    NOT_A_LIST = Error(_('Not a list'), code=Code.BadRequest)
    LIST_LENGTH_MISMATCH = Error(_('List length mismatch'), code=Code.BadRequest)
    NOT_A_DICT = Error(_('Not a dict'), code=Code.BadRequest)
    INVALID_KEY = Error(_('{key} is an invalid key'), code=Code.BadRequest)
    EXIST_PARAM_KEY = Error(_('Param key {key} already exists'), code=Code.InternalServerError)


class Validator:
    class __NoDefaultValue: pass
    class __UnSetValue: pass

    @classmethod
    def unset(cls):
        return cls.__UnSetValue

    def __init__(self, name=None, verbose_name=None, final_name=None, **kwargs):
        self.allow_null = False
        self.default_value = self.__NoDefaultValue
        self.to_python = []
        self.validators = []

        if isinstance(name, str):
            name = Key(name, verbose_name, final_name)
        if name and not isinstance(name, Key):
            raise TypeError('name must be a string or Key instance')
        self.key = name

    def _carry_key_info(self, error: Error):
        if self.key is None:
            return error

        err = error()
        err.details.append(_('Target key: {key}').format(key=str(self.key)))
        return err

    def rename(self, name: Union[str, Key], verbose_name=__UnSetValue, final_name=__UnSetValue):
        if isinstance(name, Key):
            self.key = name
            return self

        self.key = self.key.copy() if self.key else Key(name)

        self.key.name = name
        if verbose_name is not self.unset():
            self.key.verbose_name = verbose_name
        if final_name is not self.unset():
            self.key.final_name = final_name
        return self

    def copy(self):
        new = Validator()
        new.allow_null = self.allow_null
        new.default_value = self.default_value
        new.to_python = self.to_python.copy()
        new.validators = self.validators.copy()
        new.key = self.key.copy()
        return new

    def null(self, allow_null=True):
        self.allow_null = allow_null
        return self

    def default(self, value):
        self.default_value = value
        return self

    def to(self, to_python: Callable):
        self.to_python.append(to_python)
        return self

    def exception(self, validator: Callable, message: str = None):
        def wrap(value):
            try:
                validator(value)
            except Error as e:
                raise e
            except Exception as err:
                raise ValidatorErrors.NOT_VALID(message=message, details=err)
        self.validators.append(wrap)
        return self

    def bool(self, validator: Callable, message: str = None):
        def wrap(value):
            if not validator(value):
                raise ValidatorErrors.NOT_VALID(message=message or '')
        return self.exception(wrap)

    def clean(self, value):
        if value is self.__UnSetValue:
            if self.default_value is self.__NoDefaultValue:
                raise self._carry_key_info(ValidatorErrors.NO_DEFAULT)
            value = self.default_value

        if value is None:
            if not self.allow_null:
                raise self._carry_key_info(ValidatorErrors.NULL_NOT_ALLOW)
            return None

        for to_python in self.to_python:
            try:
                value = to_python(value)
            except Error as e:
                raise self._carry_key_info(e)
            except Exception as err:
                raise self._carry_key_info(ValidatorErrors.PROCESSOR_CRUSHED(details=err))
        for validator in self.validators:
            try:
                validator(value)
            except Error as e:
                raise self._carry_key_info(e)
            except Exception as err:
                raise self._carry_key_info(ValidatorErrors.VALIDATOR_CRUSHED(details=err))
        return value

    def __call__(self, value):
        return self.clean(value)

    @classmethod
    def from_field(cls, field: models.Field, *args, **kwargs):
        validator = cls(*args, **kwargs)
        validator.null(field.null)
        if field.default != models.fields.NOT_PROVIDED:
            validator.default(field.default)
        if field.choices:
            validator.bool(lambda x: x in dict(field.choices), message=_('Invalid choice'))
        if field.validators:
            for field_validator in field.validators:
                if not isinstance(field_validator, BaseValidator):
                    validator.exception(field_validator)
        if isinstance(field, models.CharField):
            validator.bool(lambda x: isinstance(x, str), message=_('Not a string'))
            validator.bool(lambda x: len(x) <= field.max_length, message=_('Too long'))
        if isinstance(field, models.IntegerField):
            validator.bool(lambda x: isinstance(x, int), message=_('Not an integer'))
        if isinstance(field, models.FloatField):
            validator.bool(lambda x: isinstance(x, float), message=_('Not a float'))
        if isinstance(field, models.BooleanField):
            validator.bool(lambda x: isinstance(x, bool), message=_('Not a boolean'))
        if isinstance(field, models.DateField):
            validator.bool(lambda x: isinstance(x, datetime.date), message=_('Not a date'))
        if isinstance(field, models.DateTimeField):
            validator.bool(lambda x: isinstance(x, datetime.datetime), message=_('Not a datetime'))
        return validator

    @property
    def classname(self):
        return self.__class__.__name__

    def __str__(self):
        num_validators = len(self.validators)
        name_str = f'{self.key}' if self.key else ''
        if num_validators == 0:
            validator_str = ''
        elif num_validators == 1:
            validator_str = ' 1 validator'
        else:
            validator_str = f'{num_validators} validators'
        if name_str and validator_str:
            name_str += ', '

        return f'{self.classname}({name_str}{validator_str})'
