from django.utils.translation import gettext as _

from Chat.models import BaseChat
from Message.models import Message
from User.models import BaseUser
from utils import processor
from utils.validation.params import Params
from utils.validation.validator import Validator


class MessageParams(metaclass=Params):
    model_class = Message

    message_id = Validator('message_id', final_name='message').to(processor.int).to(Message.index)
    chat_id = Validator('chat_id', final_name='chat').to(processor.int).to(BaseChat.index)
    user_id = Validator('user_id', final_name='user').to(processor.int).to(BaseUser.index)

    content: Validator
    type: Validator

    limit = Validator('limit').to(int) \
        .bool(lambda x: x >= 5, message=_('limit should be greater than 5')) \
        .bool(lambda x: x <= 100, message=_('limit should be less than 100'))

    before = Validator('before').to(processor.int).null().default(None)
    after = Validator('after').to(processor.int).null().default(None)
