import json

from django.http import HttpResponse
from django.utils.functional import Promise

from smartdjango.error import Error, OK


def _safe_error_eq(self, other):
    if not isinstance(other, Error):
        return False
    return self.identifier == other.identifier


Error.__eq__ = _safe_error_eq


def _to_jsonable(value):
    if isinstance(value, Promise):
        return str(value)
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_to_jsonable(item) for item in value)
    return value


class APIPacker:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request, *args, **kwargs):
        response = self.get_response(request, *args, **kwargs)
        if isinstance(response, HttpResponse):
            return response
        return self.pack(response)

    @classmethod
    def process_exception(cls, _, error):
        if isinstance(error, Error):
            return cls.pack(error)
        return None

    @staticmethod
    def pack(response):
        if isinstance(response, Error):
            body, error = None, response
        else:
            body, error = response, OK

        payload = error.json()
        payload['body'] = body
        payload = _to_jsonable(payload)
        serialized = json.dumps(payload, ensure_ascii=False, default=str)

        return HttpResponse(
            serialized,
            status=error.code,
            content_type='application/json; encoding=utf-8',
        )
