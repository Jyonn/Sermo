from django.utils.translation import gettext as _

from utils.error import Error


@Error.register
class ChatErrors:
    GROUP_CHAT_EMPTY = Error(_('Cannot create an empty group chat'))
    GROUP_CHAT_TOO_SMALL = Error(_('Group chat should have at least 3 members'))
    UNALIGNED_HOST = Error(_('All members of a group chat should have the same host'))
    NOT_MEMBER = Error(_('Guest {guest} is not a member of the group chat {chat}'))
    NOT_EXISTS = Error(_('Chat {chat} does not exist'))
