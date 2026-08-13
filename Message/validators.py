from django.utils.translation import gettext_lazy as _

from smartdjango import Error, Code


@Error.register
class MessageErrors:
    NOT_A_MEMBER = Error(message=_('You are not a member of this chat'), code=Code.Forbidden)
    NOT_EXISTS = Error(message=_('Message does not exist'), code=Code.NotFound)
    NOT_OWNER = Error(message=_('You are not the owner of this message'), code=Code.Forbidden)
    RECALL_WINDOW_EXPIRED = Error(message=_('The message recall window has expired'), code=Code.Forbidden)
    TYPE_INVALID = Error(message=_('Invalid message type'), code=Code.BadRequest)
    CONTENT_EMPTY = Error(message=_('Message content cannot be empty'), code=Code.BadRequest)
    CONTENT_TOO_LONG = Error(message=_('Message content is too long'), code=Code.BadRequest)
    PAYLOAD_INVALID = Error(message=_('Invalid message payload'), code=Code.BadRequest)
    MEDIA_KIND_INVALID = Error(message=_('Invalid media kind'), code=Code.BadRequest)
    AUDIO_DURATION_INVALID = Error(message=_('Audio message cannot exceed 60 seconds'), code=Code.BadRequest)
    REPLY_TARGET_INVALID = Error(message=_('The replied message is not in this chat'), code=Code.BadRequest)
    PIN_FORBIDDEN = Error(message=_('You cannot manage pinned messages in this chat'), code=Code.Forbidden)
    PIN_LIMIT_REACHED = Error(message=_('This chat has reached the pinned message limit'), code=Code.BadRequest)
    MAP_ACCESS_DIRECT_ONLY = Error(message=_('Map access can only be shared in a direct chat'), code=Code.BadRequest)
    MAP_ACCESS_TARGET_INVALID = Error(message=_('The map access recipient is invalid'), code=Code.BadRequest)
    SYSTEM_MESSAGE_FORBIDDEN = Error(message=_('System messages cannot be managed by users'), code=Code.Forbidden)


class MessageValidator:
    MAX_CONTENT_LENGTH = 512
    MAX_AUDIO_DURATION_SECONDS = 60
    MAX_CLIENT_MESSAGE_ID_LENGTH = 64
