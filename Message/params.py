from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator

from Chat.models import Chat
from Message.models import Message


class MessageParams(metaclass=Params):
    model_class = Message

    message_id = Validator('message_id', final_name='message').to(int).to(Message.index)
    chat_id = Validator('chat_id', final_name='chat').to(int).to(Chat.index)

    content: Validator
    type: Validator

    limit = Validator('limit').to(int) \
        .bool(lambda x: x >= 5, message=_('limit should be greater than 5')) \
        .bool(lambda x: x <= 100, message=_('limit should be less than 100'))

    before = Validator('before').to(int).null().default(None)
    after = Validator('after').to(int).null().default(None)
