from utils import function
from utils.error import Error
from utils.validation.validator import Validator, ValidatorErrors


class DictValidator(Validator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.field_validators = dict()

    def copy(self):
        new = super().copy()
        new.field_validators = self.field_validators.copy()
        return new

    def field(self, validator: Validator):
        if validator.key is None:
            raise ValueError('Validator key is required for DictValidator field')
        if validator.key in self.field_validators:
            raise ValidatorErrors.EXIST_PARAM_KEY(key=validator.key)
        self.field_validators[validator.key] = validator
        return self

    def fields(self, *validators):
        for validator in validators:
            self.field(validator)
        return self

    def restrict_keys(self):
        def key_validator(value):
            for key in value:
                if key not in self.field_validators:
                    raise ValidatorErrors.INVALID_KEY(key=key)
        self.exception(key_validator)
        return self

    def clean(self, value):
        value = super().clean(value)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValidatorErrors.NOT_A_DICT

        new_value = dict()
        for key, validator in self.field_validators.items():
            try:
                final_value = validator.clean(value.get(key.name, Validator.unset()))
            except Error as error:
                raise error
            new_value[key.final_name] = final_value
        return new_value

    def __str__(self):
        string = super().__str__()
        if self.field_validators:
            string += ' with fields:'
            for key, validator in self.field_validators.items():
                string += function.indent('\n' + str(validator))
        return string
