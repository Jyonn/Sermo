from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

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


class MessageParams(metaclass=Params):
    model_class = Message

    message_id = Validator('message_id', final_name='message').to(int).to(Message.index)
    message_ids = Validator('message_ids').to(validate_message_ids)
    reply_to_message_id = Validator('reply_to_message_id', final_name='reply_to').to(int).to(Message.index).null().default(None)
    client_message_id = Validator('client_message_id').to(str).null().default(None)
    chat_id = Validator('chat_id', final_name='chat').to(int).to(Chat.index)

    content: Validator
    type: Validator
    kind = Validator('kind').to(str)
    file_name = Validator('file_name').to(str)
    content_type = Validator('content_type').to(str).null().default(None)

    limit = Validator('limit').to(int) \
        .bool(lambda x: x >= 5, message=_('limit should be greater than 5')) \
        .bool(lambda x: x <= 100, message=_('limit should be less than 100'))

    before = Validator('before').to(int).null().default(None)
    after = Validator('after').to(int).null().default(None)
    delete_scope = Validator('scope', final_name='delete_scope').to(str).bool(lambda value: value in ('me', 'everyone')).default('everyone')
