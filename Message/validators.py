from django.utils.translation import gettext_lazy as _

from smartdjango import Error, Code


@Error.register
class MessageErrors:
    NOT_A_MEMBER = Error(message=_('You are not a member of this chat'), code=Code.Forbidden)
    NOT_EXISTS = Error(message=_('Message does not exist'), code=Code.NotFound)


class MessageValidator:
    MAX_CONTENT_LENGTH = 512
