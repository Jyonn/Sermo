import json
from django.views import View
from Chat.calendar_policy import defaults, modes, review_required, save
from Chat.models import ResearchCalendarPreference
from Chat.research import fail
from User.chronelle_account import BindingFailure, calendar, credentials
from User.chronelle_origins import authorization_site, APP_ORIGINS
from utils import auth


class ChronellePermissionsView(View):
    @auth.require_user
    def get(self, request):
        try:
            base, _ = authorization_site(request)
            subject = credentials(request.user, base)['binding']['subject']
            groups = calendar(request.user, base, 'groups/')
            if credentials(request.user, base)['binding']['subject'] != subject:
                raise BindingFailure('binding_superseded')
            pref = ResearchCalendarPreference.objects.filter(user=request.user).first()
            audiences = modes(pref) if pref is None or pref.binding_subject == subject else defaults('hidden')
            known = {str(g['id']) for g in groups}
            for entry in audiences.values():
                entry['overrides'] = {key: mode for key, mode in entry['overrides'].items() if key in known}
            return {'status': 'active', 'groups': [{'id': g['id'], 'name': g['name']} for g in groups],
                    'audiences': audiences, 'review_required': review_required(request.user, subject)}
        except BindingFailure as error:
            return {'status': error.code, 'groups': []}

    @auth.require_user
    def post(self, request):
        try:
            if len(request.body) > 100000:
                raise BindingFailure('invalid_request', 413)
            if request.headers.get('Origin') not in APP_ORIGINS:
                raise BindingFailure('origin_not_allowed', 403)
            body = json.loads(request.body)
            if not isinstance(body, dict):
                raise BindingFailure('invalid_request', 400)
            base, _ = authorization_site(request)
            subject = credentials(request.user, base)['binding']['subject']
            group_ids = {g['id'] for g in calendar(request.user, base, 'groups/')}
            return save(request.user, subject, body.get('audiences'), group_ids, base)
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError):
            return fail('invalid_policy', 400)
