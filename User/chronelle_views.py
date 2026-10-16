import base64
import hashlib
import secrets
from uuid import UUID

import requests
from django.db import transaction
from django.utils import timezone
from django.views import View
from smartdjango import Error, Code, OK

from User.models import User
from User.chronelle_branding import application_icon
from utils import auth

BASE = 'https://chronelle.jyonn.space'


@Error.register
class ChronelleErrors:
    UNAVAILABLE = Error(message='Chronelle authorization unavailable. Please retry.', code=Code.BadRequest)


def active_pending(user):
    data = user.chronelle_authorization
    return data if data.get('expires_at', 0) > timezone.now().timestamp() else {}


class ChronelleAuthorizationView(View):
    @auth.require_user
    def post(self, request):
        try:
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                previous = active_pending(user)
                if previous and previous.get('branding_version') == 1:
                    return {'authorize_url': f'{BASE}/#/authorize/{previous["request_id"]}'}
                verifier = secrets.token_urlsafe(48)
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                state = secrets.token_urlsafe(32)
                response = requests.post(f'{BASE}/api/integrations/requests/', json={
                    'app_name': 'Sermo', 'app_url': 'https://sermo.jyonn.space',
                    'app_icon': application_icon(),
                    'purpose': 'Verify calendar ownership and display the public calendar on your Sermo profile. Private groups are not published.',
                    'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': state,
                }, timeout=15, allow_redirects=False)
                if response.status_code != 201:
                    raise ChronelleErrors.UNAVAILABLE
                request_id = str(UUID(response.json()['request_id']))
                user.chronelle_authorization = {
                    'request_id': request_id, 'verifier': verifier, 'state': state,
                    'branding_version': 1,
                    'expires_at': timezone.now().timestamp() + 590, 'claim_after': 0,
                }
                user.save(update_fields=['chronelle_authorization'])
            return {'authorize_url': f'{BASE}/#/authorize/{request_id}'}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise ChronelleErrors.UNAVAILABLE


class ChronelleClaimView(View):
    @auth.require_user
    def post(self, request):
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            pending = active_pending(user)
            if not pending:
                if user.chronelle_authorization:
                    user.chronelle_authorization = {}
                    user.save(update_fields=['chronelle_authorization'])
                return {'status': 'expired'}
            if pending.get('claim_after', 0) > timezone.now().timestamp():
                return {'status': 'pending'}
            pending = {**pending, 'claim_after': timezone.now().timestamp() + 20}
            user.chronelle_authorization = pending
            user.save(update_fields=['chronelle_authorization'])
        try:
            response = requests.post(f'{BASE}/api/integrations/requests/{pending["request_id"]}/claim/',
                                     json={'code_verifier': pending['verifier']}, timeout=15, allow_redirects=False)
            if response.status_code in (202, 429):
                return {'status': 'pending'}
            if response.status_code not in (200, 403, 410):
                raise ChronelleErrors.UNAVAILABLE
            calendar_id = ''
            if response.status_code == 200:
                data = response.json()
                if not secrets.compare_digest(str(data.get('state', '')), pending['state']):
                    raise ChronelleErrors.UNAVAILABLE
                calendar_id = str(UUID(data['calendar']))
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                if user.chronelle_authorization != pending:
                    return {'status': 'expired'}
                user.chronelle_authorization = {}
                if calendar_id:
                    # Never publish the private read key returned by Chronelle.
                    user.chronelle_calendar_id = calendar_id
                user.save(update_fields=['chronelle_calendar_id', 'chronelle_authorization'])
            if calendar_id:
                return {'status': 'approved', 'chronelle_calendar_id': calendar_id}
            return {'status': 'denied' if response.status_code == 403 else 'expired'}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise ChronelleErrors.UNAVAILABLE
        finally:
            with transaction.atomic():
                user = User.objects.select_for_update().get(pk=request.user.pk)
                if user.chronelle_authorization == pending:
                    user.chronelle_authorization = {**pending, 'claim_after': timezone.now().timestamp() + 4}
                    user.save(update_fields=['chronelle_authorization'])


class ChronelleBindingView(View):
    @auth.require_user
    def delete(self, request):
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            user.chronelle_authorization = {}
            user.chronelle_calendar_id = ''
            user.save(update_fields=['chronelle_calendar_id', 'chronelle_authorization'])
        return OK
