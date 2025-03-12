from django.utils.translation import gettext as _

from utils.validation.validator import ValidatorErrors


def exception_processor(processor, message=None):
    def func(value):
        try:
            return processor(value)
        except Exception as e:
            raise ValidatorErrors.PROCESSOR_CRUSHED(details=message or str(e))
    return func


int = exception_processor(int, message=_('Invalid integer'))
str = exception_processor(str, message=_('Invalid string'))
