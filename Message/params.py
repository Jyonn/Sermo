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


def validate_history_ids(values):
    if not isinstance(values, list) or len(values) > 500 or any(type(value) is not int or value <= 0 for value in values) or len(set(values)) != len(values):
        raise MessageErrors.PAYLOAD_INVALID
    return values


class MessageParams(metaclass=Params):
    model_class = Message
    history_types = Validator('types').to(str).bool(lambda value: len(value) <= 64).default('')
    history_start = Validator('start').to(float).bool(lambda value: 0 <= value <= 4133980800).null().default(None)
    history_end = Validator('end').to(float).bool(lambda value: 0 <= value <= 4133980800).null().default(None)
    history_time_from = Validator('time_from').to(int).bool(lambda value: 0 <= value < 1440).null().default(None)
    history_time_to = Validator('time_to').to(int).bool(lambda value: 0 <= value < 1440).null().default(None)
    history_snapshot = Validator('snapshot').to(int).bool(lambda value: value >= 0).null().default(None)
    history_retained = Validator('retained').to(int).bool(lambda value: value in (0, 1)).default(0)
    history_action = Validator('action').to(str).bool(lambda value: value in ('preview', 'delete', 'retain', 'release'))
    history_ids = Validator('message_ids').to(validate_history_ids).default([])

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
    preview_text = Validator('text', final_name='preview_text').to(str).bool(lambda value: len(value) <= 4096)
    preview_id = Validator('preview_id').to(int).bool(lambda value: value > 0)
    force_refresh = Validator('force', final_name='force_refresh').to(bool).default(False)
    chat_id = Validator('chat_id', final_name='chat').to(int).to(Chat.index)

    content: Validator
    type: Validator
    kind = Validator('kind').to(str)
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str).null().default(None)
    file_size = Validator('file_size').to(int).null().default(None)
    content_hash = Validator('content_hash').to(str).null().default(None)
    duration_seconds = Validator('duration_seconds').to(float).null().default(None)
    resource_id = Validator('resource_id').to(int).null().default(None)
    resource_kind = Validator('kind', final_name='resource_kind').to(str).null().default(None)
    resource_keyword = Validator('keyword', final_name='resource_keyword').to(str).null().default(None)
    resource_offset = Validator('offset', final_name='resource_offset').to(int) \
        .bool(lambda value: value >= 0, message=_('offset should not be negative')).default(0)
    resource_limit = Validator('limit', final_name='resource_limit').to(int) \
        .bool(lambda value: value >= 1, message=_('limit should be positive')) \
        .bool(lambda value: value <= 100, message=_('limit should be at most 100')).default(60)
    password = Validator('password').to(str)

    limit = Validator('limit').to(int) \
        .bool(lambda x: x >= 5, message=_('limit should be greater than 5')) \
        .bool(lambda x: x <= 100, message=_('limit should be less than 100'))

    before = Validator('before').to(int).null().default(None)
    after = Validator('after').to(int).null().default(None)
    keyword = Validator('keyword').to(str).null().default(None)
    search_type = Validator('type', final_name='search_type').to(int).bool(
        lambda value: value in (0, 1, 2, 4, 5, 6, 7, 8, 9, 11),
        message=_('Unsupported message type'),
    ).null().default(None)
    calendar_year = Validator('year', final_name='calendar_year').to(int).bool(
        lambda value: 2000 <= value <= 2100,
        message=_('Unsupported calendar year'),
    )
    calendar_month = Validator('month', final_name='calendar_month').to(int).bool(
        lambda value: 1 <= value <= 12,
        message=_('Unsupported calendar month'),
    )
    delete_scope = Validator('scope', final_name='delete_scope').to(str).bool(lambda value: value in ('me', 'everyone')).default('everyone')
