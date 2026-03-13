import datetime

import jwt

from Sermo.settings import SECRET_KEY


class SpaceTokenSymbols:
    EXPIRE = 'expire'
    TIME = 'time'
    TYPE = 'type'
    ALGORITHM = 'HS256'
    SPACE_ACCESS = 'space_access'


SPACE_ACCESS_EXPIRE_SECONDS = 24 * 60 * 60


def _encode_token(data: dict, expire_second: int, token_type: str):
    payload = dict(data)
    payload[SpaceTokenSymbols.TIME] = datetime.datetime.now().timestamp()
    payload[SpaceTokenSymbols.EXPIRE] = expire_second
    payload[SpaceTokenSymbols.TYPE] = token_type
    token = jwt.encode(payload, key=SECRET_KEY, algorithm=SpaceTokenSymbols.ALGORITHM)
    if isinstance(token, bytes):
        token = token.decode()
    return token, payload


def get_login_token(space):
    access_token, access_payload = _encode_token(
        dict(
            space_id=space.id,
            slug=space.slug,
            email=space.email,
        ),
        expire_second=SPACE_ACCESS_EXPIRE_SECONDS,
        token_type=SpaceTokenSymbols.SPACE_ACCESS,
    )
    return dict(
        auth=access_token,
        data=access_payload,
    )
