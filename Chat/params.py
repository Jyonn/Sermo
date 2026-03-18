from django.utils.translation import gettext_lazy as _
from smartdjango import Params, Validator, ListValidator

from Chat.models import Chat, ChatMember
from User.models import User


class ChatParams(metaclass=Params):
    model_class = Chat

    chat_id = Validator('chat_id', final_name='chat') \
        .to(int) \
        .to(Chat.index)

    peer_user_id = Validator('peer_user_id', final_name='peer_user') \
        .to(int) \
        .to(User.index)

    users = ListValidator('users') \
        .element(Validator().to(User.index)) \
        .bool(lambda x: len({item for item in x}) == len(x), message=_('duplicated users')) \
        .bool(lambda x: len(x) >= 1, message=_('group chat should have at least 1 invited member'))

    title: Validator


class ChatMemberParams(metaclass=Params):
    model_class = ChatMember

    chat_id = ChatParams.chat_id

    users = ListValidator('users') \
        .element(Validator().to(User.index)) \
        .bool(lambda x: len({item for item in x}) == len(x), message=_('duplicated users'))

    accept = Validator('accept') \
        .to(int) \
        .bool(lambda x: x in (0, 1), message=_('accept should be 0 or 1'))
