from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error


@Error.register
class PlatformAdminErrors:
    ACCESS_DENIED = Error(_('Platform administrator access denied'), code=Code.Forbidden)
    CODE_INVALID = Error(_('Verification code is invalid or expired'), code=Code.BadRequest)
    MFA_REQUIRED = Error(_('MFA code required'), code=Code.Unauthorized)
    MFA_INVALID = Error(_('MFA code is invalid'), code=Code.BadRequest)
    MFA_NOT_PENDING = Error(_('MFA setup has not started'), code=Code.BadRequest)
    IDENTITY_NOT_PENDING = Error(_('Identity review is not pending'), code=Code.BadRequest)
