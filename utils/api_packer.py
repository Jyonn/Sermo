import json
from typing import Optional

from django.http import HttpResponse

from utils.error import Error, OK


def pack(resp):
    body, err = (None, resp) if isinstance(resp, Error) else (resp, OK)  # type: Optional[dict], Error

    resp = err.to_json()
    resp['body'] = body

    resp = json.dumps(resp, ensure_ascii=False)

    response = HttpResponse(
        resp,
        status=err.code,
        content_type="application/json; encoding=utf-8",
    )
    return response
