from django.utils.translation import gettext as _

from Chat.models import BaseChat, SingleChat, GroupChat
from User.models import GuestUser
from smartdjango import Validator, ListValidator, Params


class BaseChatParams(metaclass=Params):
    model_class = BaseChat

    chat_id = Validator('chat_id', final_name='chat') \
        .to(int) \
        .to(BaseChat.index)


class SingleChatParams(BaseChatParams):
    model_class = SingleChat


class GroupChatParams(BaseChatParams):
    model_class = GroupChat

    name: Validator

    guests = ListValidator('guests') \
        .element(Validator().to(GuestUser.index)) \
        .bool(lambda x: len(set(x)) == len(x), message=_('duplicated guests')) \
        .bool(lambda x: len(x) >= 1, message=_('group chat should have at least 2 members'))

    chat_id = BaseChatParams.chat_id.copy().bool(lambda x: x.group, message=_('not a group chat'))


class GroupChatMemberParams(BaseChatParams):
    guests = ListValidator('guests') \
        .element(Validator().to(GuestUser.index)) \
        .bool(lambda x: len(set(x)) == len(x), message=_('duplicated guests'))

    chat_id = BaseChatParams.chat_id.copy().bool(lambda x: x.group, message=_('not a group chat'))


class GroupChatInviteParams(metaclass=Params):
    accept = Validator('accept') \
        .to(int) \
        .bool(lambda x: x in (0, 1), message=_('accept should be 0 or 1'))
