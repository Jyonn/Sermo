from django.utils.translation import gettext_lazy as _
from smartdjango import ListValidator, Params, Validator

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


def validate_location(value):
    if value in (None, ''):
        return None
    if not hasattr(value, 'get'):
        raise SquareErrors.LOCATION_INVALID
    try:
        latitude = round(float(value.get('latitude')), 6)
        longitude = round(float(value.get('longitude')), 6)
    except (TypeError, ValueError):
        raise SquareErrors.LOCATION_INVALID
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise SquareErrors.LOCATION_INVALID
    return {
        'latitude': latitude,
        'longitude': longitude,
        'address': str(value.get('address') or '').strip()[:255],
        'geocoding_provider': str(value.get('geocoding_provider') or '').strip()[:32],
    }


def validate_comment_text(value):
    normalized = str(value or '').strip()
    if len(normalized) > 140:
        raise SquareErrors.COMMENT_TOO_LONG
    return normalized


def validate_mute_duration(value):
    normalized = str(value or '').strip().lower()
    if normalized not in {'1d', '3d', '7d', '30d', 'permanent'}:
        raise SquareErrors.MUTE_DURATION_INVALID
    return normalized


def validate_mute_reason(value):
    normalized = str(value or '').strip()
    if not normalized:
        raise SquareErrors.MUTE_REASON_REQUIRED
    return normalized[:240]


class SquareParams(metaclass=Params):
    text = Validator('text').to(validate_text).null().default('')
    visibility = Validator('visibility').to(validate_visibility).default('public')
    media = Validator('media').to(validate_media).default([])
    location = Validator('location').to(validate_location).null().default(None)
    pin = Validator('pin').to(int).bool(lambda value: value in (0, 1)).default(0)
    before = Validator('before').to(int).null().default(None)
    offset = Validator('offset').to(int).bool(lambda value: 0 <= value <= 5000).default(0)
    scope = Validator('scope').to(str).bool(lambda value: value in ('all', 'friends', 'mine')).default('all')
    user_id = Validator('user_id').to(int).null().default(None)
    parent_id = Validator('parent_id').to(int).null().default(None)
    comment_sort = Validator('sort').to(str).bool(lambda value: value in ('hot', 'latest')).default('hot')
    limit = Validator('limit').to(int).bool(
        lambda value: 5 <= value <= 50,
        message=_('limit should be between 5 and 50'),
    ).default(20)
    kind = Validator('kind').to(str)
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str).null().default(None)
    comment_text = Validator('text').to(validate_comment_text).default('')
    comment_sticker_asset_id = Validator('sticker_asset_id').to(int).null().default(None)
    comment_mention_user_ids = ListValidator('mention_user_ids').element(Validator().to(int)).default([])
    anonymous = Validator('anonymous').to(int).bool(lambda value: value in (0, 1)).default(0)
    redact_chat_record = Validator('redact_chat_record').to(int).bool(lambda value: value in (0, 1)).default(0)
    read_scope = Validator('scope').to(str).bool(lambda value: value in ('all', 'friends'))
    mute_duration = Validator('duration').to(validate_mute_duration)
    mute_reason = Validator('reason').to(validate_mute_reason)
