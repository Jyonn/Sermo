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
    client_draft_id = Validator('client_draft_id').to(str).to(lambda value: value.strip()).bool(
        lambda value: 1 <= len(value) <= 64,
        message=_('Invalid submission draft id'),
    )
    submission_action = Validator('action', final_name='submission_action').to(str).bool(
        lambda value: value in ('revision', 'terminate', 'ready'),
        message=_('Invalid submission action'),
    )


class ChatMemberParams(metaclass=Params):
    model_class = ChatMember

    chat_id = ChatParams.chat_id

    users = ListValidator('users') \
        .element(Validator().to(User.index)) \
        .bool(lambda x: len({item for item in x}) == len(x), message=_('duplicated users'))

    accept = Validator('accept') \
        .to(int) \
        .bool(lambda x: x in (0, 1), message=_('accept should be 0 or 1'))

    user_id = Validator('user_id', final_name='user') \
        .to(int) \
        .to(User.index)

    submission_role = Validator('role', final_name='submission_role') \
        .to(str) \
        .bool(lambda x: x in ('author', 'reviewer'), message=_('Invalid submission member role'))


class ChatPreferenceParams(metaclass=Params):
    pinned = Validator('pinned') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('pinned should be 0 or 1'))
    online_reminder_enabled = Validator('online_reminder_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('online_reminder_enabled should be 0 or 1'))
    statement_reminder_enabled = Validator('statement_reminder_enabled') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('statement_reminder_enabled should be 0 or 1'))
    notifications_muted = Validator('notifications_muted') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('notifications_muted should be 0 or 1'))
    unread_badge_muted = Validator('unread_badge_muted') \
        .to(int) \
        .null().default(None) \
        .bool(lambda x: x is None or x in (0, 1), message=_('unread_badge_muted should be 0 or 1'))
