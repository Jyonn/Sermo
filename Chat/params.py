from django.utils.translation import gettext as _

from Chat.models import BaseChat, SingleChat, GroupChat
from User.models import GuestUser
from utils import processor
from utils.validation.list_validator import ListValidator
from utils.validation.params import Params
from utils.validation.validator import Validator


class BaseChatParams(metaclass=Params):
    model_class = BaseChat

    chat_id = Validator('chat_id', final_name='chat') \
        .to(processor.int) \
        .to(BaseChat.index)


class SingleChatParams(BaseChatParams):
    model_class = SingleChat


class GroupChatParams(BaseChatParams):
    model_class = GroupChat

    name: Validator

    guests = ListValidator('guests') \
        .element(Validator().to(GuestUser.index)) \
        .bool(lambda x: len(set(x)) == len(x), message=_('duplicated guests'))

    chat_id = BaseChatParams.chat_id.copy().bool(lambda x: x.group, message=_('not a group chat'))
