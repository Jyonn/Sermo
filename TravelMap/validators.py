from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error


@Error.register
class TravelMapErrors:
    ACCESS_DENIED = Error(message=_('You do not have access to this map'), code=Code.Forbidden)
    RECIPROCAL_GRANT_DENIED = Error(message=_('The other user has not shared their map with you'), code=Code.Forbidden)
    SAME_USER = Error(message=_('You cannot grant map access to yourself'), code=Code.BadRequest)
    SPACE_MISMATCH = Error(message=_('Map access is limited to the same space'), code=Code.Forbidden)
    REGION_INVALID = Error(message=_('Map region is invalid'), code=Code.BadRequest)
    GEOMETRY_UNAVAILABLE = Error(message=_('Map geometry is temporarily unavailable'), code=Code.ServiceUnavailable)


class TravelMapValidator:
    REGION_CODE_MAX_LENGTH = 80
    REGION_NAME_MAX_LENGTH = 120
    COUNTRY_CODE_MAX_LENGTH = 3
    COUNTRY_NAME_MAX_LENGTH = 120
