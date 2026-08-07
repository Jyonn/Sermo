from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error


@Error.register
class SquareErrors:
    NOT_EXISTS = Error(message=_('Statement does not exist'), code=Code.NotFound)
    PUBLISH_REQUIRES_VERIFICATION = Error(message=_('Verify your email before publishing'), code=Code.Forbidden)
    CONTENT_REQUIRED = Error(message=_('Add text, photos, or a voice recording'), code=Code.BadRequest)
    TEXT_TOO_LONG = Error(message=_('A statement can contain at most 140 characters'), code=Code.BadRequest)
    MEDIA_INVALID = Error(message=_('Invalid statement attachment'), code=Code.BadRequest)
    IMAGE_LIMIT_EXCEEDED = Error(message=_('A statement can contain at most 9 photos'), code=Code.BadRequest)
    AUDIO_LIMIT_EXCEEDED = Error(message=_('A statement can contain only one voice recording'), code=Code.BadRequest)
    AUDIO_DURATION_INVALID = Error(message=_('Voice recording cannot exceed 60 seconds'), code=Code.BadRequest)
    VISIBILITY_INVALID = Error(message=_('Invalid statement visibility'), code=Code.BadRequest)
    COMMENT_REQUIRED = Error(message=_('Comment cannot be empty'), code=Code.BadRequest)
    COMMENT_TOO_LONG = Error(message=_('A comment can contain at most 140 characters'), code=Code.BadRequest)
