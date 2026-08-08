from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from Square.validators import SquareErrors


def validate_text(value):
    normalized = str(value or '').strip()
    if len(normalized) > 140:
        raise SquareErrors.TEXT_TOO_LONG
    return normalized


def validate_visibility(value):
    normalized = str(value or '').strip().lower()
    if normalized not in {'public', 'friends'}:
        raise SquareErrors.VISIBILITY_INVALID
    return normalized


def validate_media(value):
    if not isinstance(value, list):
        raise SquareErrors.MEDIA_INVALID
    if len(value) > 10 or any(not hasattr(item, 'get') for item in value):
        raise SquareErrors.MEDIA_INVALID
    return value


def validate_comment_text(value):
    normalized = str(value or '').strip()
    if not normalized:
        raise SquareErrors.COMMENT_REQUIRED
    if len(normalized) > 140:
        raise SquareErrors.COMMENT_TOO_LONG
    return normalized


class SquareParams(metaclass=Params):
    text = Validator('text').to(validate_text).null().default('')
    visibility = Validator('visibility').to(validate_visibility).default('public')
    media = Validator('media').to(validate_media).default([])
    before = Validator('before').to(int).null().default(None)
    offset = Validator('offset').to(int).bool(lambda value: 0 <= value <= 5000).default(0)
    friends_only = Validator('friends_only').to(int).bool(lambda value: value in (0, 1)).default(0)
    parent_id = Validator('parent_id').to(int).null().default(None)
    limit = Validator('limit').to(int).bool(
        lambda value: 5 <= value <= 50,
        message=_('limit should be between 5 and 50'),
    ).default(20)
    kind = Validator('kind').to(str)
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str).null().default(None)
    comment_text = Validator('text').to(validate_comment_text)
