from django.utils.translation import gettext as _

from utils.code import Code
from utils.error import Error


@Error.register
class MessageErrors:
    NOT_A_MEMBER = Error(message=_('You are not a member of this chat'), code=Code.Forbidden)
    NOT_EXISTS = Error(message=_('Message does not exist'), code=Code.NotFound)

