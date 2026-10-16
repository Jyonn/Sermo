from django.views import View
from smartdjango import analyse, Validator, Error, Code, OK

from utils import auth
from User.chronelle import calendar_id_from_url


@Error.register
class ChronelleErrors:
    INVALID_LINK = Error(message='Please enter a public Chronelle share link without access keys or parameters', code=Code.BadRequest)


def validate_share_link(value):
    try:
        return calendar_id_from_url(value)
    except (ValueError, TypeError):
        raise ChronelleErrors.INVALID_LINK


class ChronelleBindingView(View):
    @auth.require_user
    @analyse.json(Validator('url', final_name='calendar_id').to(validate_share_link))
    def post(self, request):
        request.user.chronelle_calendar_id = request.json.calendar_id
        request.user.save(update_fields=['chronelle_calendar_id'])
        return {'chronelle_calendar_id': request.user.chronelle_calendar_id}

    @auth.require_user
    def delete(self, request):
        request.user.chronelle_calendar_id = ''
        request.user.save(update_fields=['chronelle_calendar_id'])
        return OK
