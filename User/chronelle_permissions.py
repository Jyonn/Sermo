import json
from django.db.models import Q
from django.http import JsonResponse
from django.views import View
from Chat.calendar_policy import (
    accepted_friendships, defaults, detail_fields, friend_overrides, friend_relation,
    inherited_friend_policy, modes, review_required, save, save_friend_policy,
)
from Chat.models import ResearchCalendarPreference
from Chat.research import fail
from User.chronelle_account import BindingFailure, calendar, credentials, unpack
from User.chronelle_origins import authorization_site, APP_ORIGINS
from User.models import ChronelleAccount
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
                    'audiences': audiences, 'detail_fields': detail_fields(pref if pref and pref.binding_subject == subject else None),
                    'review_required': review_required(request.user, subject)}
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
            if 'detail_fields' in body and body['detail_fields'] is None:
                raise BindingFailure('invalid_policy', 400)
            return save(request.user, subject, body.get('audiences'), group_ids, base, body.get('detail_fields'))
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError):
            return fail('invalid_policy', 400)


def friend_identity(friend):
    return friend.dictify('user_id', 'name', 'avatar_uri', 'avatar_cache_key')


class PrivatePermissionsView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if isinstance(response, dict):
            response = JsonResponse({'identifier': 'OK', 'body': response})
        response['Cache-Control'] = 'private, no-store'
        return response


class ChronelleFriendExceptionsView(PrivatePermissionsView):
    @auth.require_user
    def get(self, request):
        user = request.user
        pref = ResearchCalendarPreference.objects.filter(user=user).first()
        exceptions = friend_overrides(pref)
        if not exceptions:
            return {'friends': []}
        account = ChronelleAccount.objects.filter(user=user).first()
        try:
            binding = unpack(account) if account else {}
        except BindingFailure as error:
            return fail(error.code, error.status)
        if binding.get('protocol') != 'oauth-v1' or binding.get('binding', {}).get('subject') != pref.binding_subject:
            return {'friends': []}
        rows = []
        ids = list(map(int, exceptions))
        relations = accepted_friendships(user).filter(Q(user_low_id__in=ids) | Q(user_high_id__in=ids))
        for relation in relations.select_related('user_low', 'user_high'):
            friend = relation.user_high if relation.user_low_id == user.pk else relation.user_low
            policy = exceptions.get(str(friend.pk))
            if policy:
                rows.append({'friend': friend_identity(friend), 'policy': policy})
        rows.sort(key=lambda item: (item['friend']['name'].casefold(), item['friend']['user_id']))
        return {'friends': rows}


class ChronelleFriendPermissionsView(PrivatePermissionsView):
    @staticmethod
    def target(user, friend_id):
        relation = friend_relation(user, friend_id).select_related('user_low', 'user_high').first()
        if relation is None:
            raise BindingFailure('friend_unavailable', 404)
        return relation.user_high if relation.user_low_id == user.pk else relation.user_low

    @auth.require_user
    def get(self, request, friend_id):
        user = request.user
        try:
            friend = self.target(user, friend_id)
            base, _ = authorization_site(request)
            subject = credentials(user, base)['binding']['subject']
            groups = calendar(user, base, 'groups/')
            if credentials(user, base)['binding']['subject'] != subject:
                raise BindingFailure('binding_superseded')
            pref = ResearchCalendarPreference.objects.filter(user=user, binding_subject=subject).first()
            known = {str(group['id']) for group in groups}
            policy = friend_overrides(pref).get(str(friend_id), inherited_friend_policy())
            inherited = modes(pref)['friends']
            for entry in (policy, inherited):
                entry['overrides'] = {key: mode for key, mode in entry['overrides'].items() if key in known}
            return {'status': 'active', 'friend': friend_identity(friend), 'policy': policy, 'inherited': inherited,
                    'groups': [{'id': group['id'], 'name': group['name']} for group in groups],
                    'detail_fields': detail_fields(pref)}
        except BindingFailure as error:
            if error.code in ('binding_required', 'binding_expired', 'binding_revoked'):
                return {'status': error.code}
            return fail(error.code, error.status)

    @auth.require_user
    def post(self, request, friend_id):
        try:
            if len(request.body) > 100000:
                raise BindingFailure('invalid_request', 413)
            if request.headers.get('Origin') not in APP_ORIGINS:
                raise BindingFailure('origin_not_allowed', 403)
            body = json.loads(request.body)
            if not isinstance(body, dict) or set(body) != {'policy'}:
                raise BindingFailure('invalid_policy', 400)
            user = request.user
            self.target(user, friend_id)
            base, _ = authorization_site(request)
            subject = credentials(user, base)['binding']['subject']
            group_ids = {group['id'] for group in calendar(user, base, 'groups/')}
            return save_friend_policy(user, subject, friend_id, body['policy'], group_ids, base)
        except BindingFailure as error:
            return fail(error.code, error.status)
        except (ValueError, TypeError):
            return fail('invalid_policy', 400)
