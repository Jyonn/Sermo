import re

from smartdjango import Params, Validator

from Message.models import Message
from Sticker.models import UserSticker
from Sticker.validators import StickerErrors


def validate_hash(value):
    normalized = str(value or '').strip().lower()
    if not re.fullmatch(r'[0-9a-f]{64}', normalized):
        raise StickerErrors.INVALID_HASH
    return normalized


class StickerParams(metaclass=Params):
    content_hash = Validator('content_hash').to(validate_hash)
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str).null().default(None)
    file_size = Validator('file_size').to(int).bool(lambda value: 0 <= value <= 10 * 1024 * 1024)
    storage_key = Validator('key', final_name='storage_key').to(str)
    message_id = Validator('message_id', final_name='message').to(int).to(Message.index)
    sticker_id = Validator('sticker_id', final_name='sticker').to(int).to(UserSticker.index)
