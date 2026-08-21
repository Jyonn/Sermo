from django.utils.translation import gettext_lazy as _
from smartdjango import ListValidator, Params, Validator

from Chat.models import Chat
from Message.models import Message
from Message.validators import MessageErrors


def validate_message_ids(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 50:
        raise MessageErrors.PAYLOAD_INVALID
    try:
        normalized = list(dict.fromkeys(int(value) for value in values))
    except (TypeError, ValueError):
        raise MessageErrors.PAYLOAD_INVALID
    if len(normalized) != len(values) or any(value <= 0 for value in normalized):
        raise MessageErrors.PAYLOAD_INVALID
    return normalized


def validate_target_chat_ids(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 10:
        raise MessageErrors.FORWARD_TARGET_INVALID
    try:
        normalized = list(dict.fromkeys(int(value) for value in values))
    except (TypeError, ValueError):
        raise MessageErrors.FORWARD_TARGET_INVALID
    if len(normalized) != len(values) or any(value <= 0 for value in normalized):
        raise MessageErrors.FORWARD_TARGET_INVALID
    return normalized


class MessageParams(metaclass=Params):
    model_class = Message

    message_id = Validator('message_id', final_name='message').to(int).to(Message.index)
    message_ids = Validator('message_ids').to(validate_message_ids)
    target_chat_ids = Validator('target_chat_ids').to(validate_target_chat_ids)
    forward_mode = Validator('mode', final_name='forward_mode').to(str).bool(
        lambda value: value in ('individual', 'bundle'),
        message=_('Unsupported forwarding mode'),
    )
    reply_to_message_id = Validator('reply_to_message_id', final_name='reply_to').to(int).to(Message.index).null().default(None)
    client_message_id = Validator('client_message_id').to(str).null().default(None)
    mention_user_ids = ListValidator('mention_user_ids').element(Validator().to(int)).default([])
    chat_id = Validator('chat_id', final_name='chat').to(int).to(Chat.index)

    content: Validator
    type: Validator
    kind = Validator('kind').to(str)
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str).null().default(None)
    file_size = Validator('file_size').to(int).null().default(None)
    content_hash = Validator('content_hash').to(str).null().default(None)
    duration_seconds = Validator('duration_seconds').to(float).null().default(None)
    asset_id = Validator('asset_id').to(int).null().default(None)
    resource_kind = Validator('kind', final_name='resource_kind').to(str).null().default(None)
    password = Validator('password').to(str)

    limit = Validator('limit').to(int) \
        .bool(lambda x: x >= 5, message=_('limit should be greater than 5')) \
        .bool(lambda x: x <= 100, message=_('limit should be less than 100'))

    before = Validator('before').to(int).null().default(None)
    after = Validator('after').to(int).null().default(None)
    keyword = Validator('keyword').to(str).null().default(None)
    search_type = Validator('type', final_name='search_type').to(int).bool(
        lambda value: value in (0, 1, 2, 4, 5, 6, 7, 8, 9),
        message=_('Unsupported message type'),
    ).null().default(None)
    delete_scope = Validator('scope', final_name='delete_scope').to(str).bool(lambda value: value in ('me', 'everyone')).default('everyone')
