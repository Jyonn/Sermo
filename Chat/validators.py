from django.utils.translation import gettext_lazy as _

from smartdjango import Error


@Error.register
class ChatErrors:
    NOT_EXISTS = Error(_('Chat {chat} does not exist'))
    UNALIGNED_SPACE = Error(_('Users are not in the same space'))
    USER_DELETED = Error(_('User {user} has been deleted'))
    FORBIDDEN = Error(_('You are not allowed to operate this chat'))
    NOT_GROUP_CHAT = Error(_('Chat {chat} is not a group chat'))
    NOT_DIRECT_CHAT = Error(_('Chat {chat} is not a direct chat'))
    NOT_FRIENDS = Error(_('Users are not friends'))
    CREATOR_NOT_VERIFIED = Error(_('Only verified users can create or invite members to group chats'))
    TARGET_NOT_FRIEND = Error(_('User {user} is not your friend'))
    SUBMISSION_RECIPIENT_INVALID = Error(_('Submissions can only be sent to the official account or an operator'))
    SUBMISSION_TITLE_REQUIRED = Error(_('Submission title is required'))
    SUBMISSION_LEAVE_FORBIDDEN = Error(_('Submission chats cannot be left'))
    SUBMISSION_REVIEW_FORBIDDEN = Error(_('Only the official account or an operator can review submission invites'))
    SUBMISSION_SEND_FORBIDDEN = Error(_('Messages cannot be sent in the current submission state'))
    SUBMISSION_TRANSITION_FORBIDDEN = Error(_('This submission action is not available'))
    SUBMISSION_EMPTY = Error(_('A submission must contain at least one message'))


@Error.register
class ChatMemberErrors:
    NOT_MEMBER = Error(_('User {user} is not an active member of chat {chat}'))
    ALREADY_MEMBER = Error(_('User {user} is already a member of chat {chat}'))
    INVITE_PENDING = Error(_('User {user} already has a pending invite in chat {chat}'))
    NOT_EXISTS = Error(_('Chat member {chat} does not exist'))
    INVITE_NOT_FOUND = Error(_('Invite not found for this chat'))
    INVITE_CLOSED = Error(_('Invite is not pending'))
    OWNER_LEAVE_FORBIDDEN = Error(_('Owner cannot leave group chat directly'))
    OWNER_TRANSFER_TO_SELF = Error(_('Owner cannot transfer the group to themselves'))


class ChatValidator:
    TITLE_MAX_LENGTH = 50


class ChatMemberValidator:
    pass
