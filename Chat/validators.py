from django.utils.translation import gettext as _

from smartdjango import Error


@Error.register
class ChatErrors:
    NOT_EXISTS = Error(_('Chat {chat} does not exist'))
    UNALIGNED_SPACE = Error(_('Users are not in the same space'))
    USER_DELETED = Error(_('User {user} has been deleted'))
    FORBIDDEN = Error(_('You are not allowed to operate this chat'))
    NOT_GROUP_CHAT = Error(_('Chat {chat} is not a group chat'))


@Error.register
class ChatMemberErrors:
    NOT_MEMBER = Error(_('User {user} is not an active member of chat {chat}'))
    ALREADY_MEMBER = Error(_('User {user} is already a member of chat {chat}'))
    INVITE_PENDING = Error(_('User {user} already has a pending invite in chat {chat}'))
    NOT_EXISTS = Error(_('Chat member {chat} does not exist'))
    INVITE_NOT_FOUND = Error(_('Invite not found for this chat'))
    INVITE_CLOSED = Error(_('Invite is not pending'))
    OWNER_LEAVE_FORBIDDEN = Error(_('Owner cannot leave group chat directly'))


class ChatValidator:
    TITLE_MAX_LENGTH = 50


class ChatMemberValidator:
    pass
