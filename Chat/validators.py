from django.utils.translation import gettext as _

from smartdjango import Error


@Error.register
class ChatErrors:
    UNALIGNED_HOST = Error(_('All members of a group chat should have the same host'))
    NOT_MEMBER = Error(_('Guest {guest} is not a member of the group chat {chat}'))
    NOT_EXISTS = Error(_('Chat {chat} does not exist'))
    GUEST_DELETED = Error(_('Guest {guest} has been deleted'))
