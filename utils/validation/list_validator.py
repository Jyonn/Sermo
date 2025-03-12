from utils import function
from utils.validation.validator import Validator, ValidatorErrors


class ListValidator(Validator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.element_validator = None

    def element(self, validator: Validator):
        self.element_validator = validator
        return self

    def elements(self, *validators: list):
        self.element_validator = validators
        return self

    def copy(self):
        new = super().copy()
        new.element_validator = self.element_validator
        if isinstance(self.element_validator, list):
            new.element_validator = new.element_validator.copy()
        return new

    def clean(self, value):
        value = super().clean(value)
        if value is None:
            return None
        if not isinstance(value, list):
            raise ValidatorErrors.NOT_A_LIST
        if self.element_validator is None:
            return value

        if isinstance(self.element_validator, list):
            if len(self.element_validator) != len(value):
                raise ValidatorErrors.LIST_LENGTH_MISMATCH
            values = [validator.clean(v) for validator, v in zip(self.element_validator, value)]
        else:
            values = [self.element_validator.clean(v) for v in value]

        return values

    def __str__(self):
        string = super().__str__()
        if self.element_validator is not None:
            string += ' with element validator:'

            if isinstance(self.element_validator, list):
                for validator in self.element_validator:
                    string += function.indent('\n' + str(validator))
            else:
                string += function.indent('\n' + str(self.element_validator))
