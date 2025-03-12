import json
from functools import wraps

from django.http import HttpRequest
from django.utils.translation import gettext as _
from oba import Obj

from User.models import BaseUser
from utils.code import Code
from utils.error import Error
from utils.validation.dict_validator import DictValidator
from utils.validation.validator import Validator


class Request(HttpRequest):
    user: BaseUser
    body: Obj
    query: Obj
    argument: Obj
    data: Obj

@Error.register
class AnalyseErrors:
    REQUEST_NOT_FOUND = Error(_("Cannot find request"), code=Code.InternalServerError)


def get_request(*args):
    for i, arg in enumerate(args):
        if isinstance(arg, HttpRequest):
            return arg
    raise AnalyseErrors.REQUEST_NOT_FOUND


def update_to_data(request: Request, target):
    data = getattr(request, '_data', None)
    data = data() if data is not None else {}
    data.update(target())
    request.data = Obj(data)


def analyse(*validators: Validator, target_getter, target_setter, restrict_keys):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            request = get_request(*args)
            target = target_getter(request, kwargs)

            validator = DictValidator().fields(*validators)
            if restrict_keys:
                validator.restrict_keys()
            target = validator.clean(target)
            target_setter(request, Obj(target))

            return func(*args, **kwargs)
        return wrapper
    return decorator


def body(*validators: Validator, restrict_keys=True):
    def getter(request, kwargs):
        return json.loads(request.body.decode())

    def setter(request, target):
        request._body = target
        update_to_data(request, target)

    return analyse(
        *validators,
        target_getter=getter,
        target_setter=setter,
        restrict_keys=restrict_keys
    )


def query(*validators: Validator, restrict_keys=False):
    def getter(request, kwargs):
        return request.GET.dict()

    def setter(request, target):
        request.query = target
        update_to_data(request, target)

    return analyse(
        *validators,
        target_getter=getter,
        target_setter=setter,
        restrict_keys=restrict_keys
    )


def argument(*validators: Validator, restrict_keys=True):
    def getter(request, kwargs):
        return kwargs

    def setter(request, target):
        request.argument = target
        update_to_data(request, target)

    return analyse(
        *validators,
        target_getter=getter,
        target_setter=setter,
        restrict_keys=restrict_keys
    )


def request(bool_func, message=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            request = get_request(*args)
            Validator().bool(bool_func, message=message).clean(request)
            return func(*args, **kwargs)

        return wrapper
    return decorator
