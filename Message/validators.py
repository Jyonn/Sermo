from django.utils.translation import gettext_lazy as _

from smartdjango import Error, Code


@Error.register
class MessageErrors:
    NOT_A_MEMBER = Error(message=_('You are not a member of this chat'), code=Code.Forbidden)
    NOT_EXISTS = Error(message=_('Message does not exist'), code=Code.NotFound)
    TYPE_INVALID = Error(message=_('Invalid message type'), code=Code.BadRequest)
    CONTENT_EMPTY = Error(message=_('Message content cannot be empty'), code=Code.BadRequest)
    CONTENT_TOO_LONG = Error(message=_('Message content is too long'), code=Code.BadRequest)
    PAYLOAD_INVALID = Error(message=_('Invalid message payload'), code=Code.BadRequest)
    MEDIA_KIND_INVALID = Error(message=_('Invalid media kind'), code=Code.BadRequest)
    AUDIO_DURATION_INVALID = Error(message=_('Audio message cannot exceed 60 seconds'), code=Code.BadRequest)


class MessageValidator:
    MAX_CONTENT_LENGTH = 512
    MAX_AUDIO_DURATION_SECONDS = 60
