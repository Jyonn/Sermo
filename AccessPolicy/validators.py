from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error


@Error.register
class AccessPolicyErrors:
    CAPABILITY_NOT_FOUND = Error(_('Capability does not exist'), code=Code.NotFound)
    CAPABILITY_DENIED = Error(_('This feature is not available for the current account'), code=Code.Forbidden)
    POLICY_INVALID = Error(_('Capability policy is invalid'), code=Code.BadRequest)
    SPACE_POLICY_FORBIDDEN = Error(_('This capability cannot be configured by a space administrator'), code=Code.Forbidden)

