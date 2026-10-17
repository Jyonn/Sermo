import json

from django.http import JsonResponse
from django.views import View

from User.oauth import OAuthError, authorize, exchange, revoke, userinfo
from utils import auth
from User.oauth_eligibility import eligibility, VERIFICATION_PATH


def _payload(request):
    if request.content_type == 'application/json':
        try:
            value = json.loads(request.body)
            return value if isinstance(value, dict) else {}
        except (ValueError, UnicodeDecodeError):
            return {}
    return request.POST


def _error(error):
    status = 403 if error.code == 'verification_required' else 401 if error.code == 'invalid_token' else 400
    payload = {'error': error.code, 'error_description': str(error)}
    if error.code == 'verification_required':
        payload['verification_path'] = VERIFICATION_PATH
    response = JsonResponse(payload, status=status)
    response['Cache-Control'] = 'no-store'
    return response


def _bearer(request):
    header = request.headers.get('Authorization', '')
    return header[7:].strip() if header.lower().startswith('bearer ') else ''


class OAuthAuthorizeView(View):
    @auth.require_user
    def post(self, request):
        data = _payload(request)
        try:
            redirect_url = authorize(request.user, **{
                key: data.get(key, '') for key in (
                    'client_id', 'redirect_uri', 'scope', 'state', 'code_challenge', 'code_challenge_method', 'response_type',
                )
            })
        except OAuthError as error:
            return _error(error)
        response = JsonResponse({'redirect_uri': redirect_url})
        response['Cache-Control'] = 'no-store'
        return response


class OAuthEligibilityView(View):
    @auth.require_user
    def get(self, request):
        response = JsonResponse(eligibility(request.user))
        response['Cache-Control'] = 'no-store'
        return response


class OAuthTokenView(View):
    def post(self, request):
        data = _payload(request)
        if data.get('grant_type') != 'authorization_code':
            return _error(OAuthError('unsupported_grant_type', 'Authorization code required'))
        try:
            result = exchange(**{key: data.get(key, '') for key in ('client_id', 'redirect_uri', 'code', 'code_verifier')})
        except OAuthError as error:
            return _error(error)
        response = JsonResponse(result)
        response['Cache-Control'] = 'no-store'
        response['Pragma'] = 'no-cache'
        return response


class OAuthUserinfoView(View):
    def get(self, request):
        try:
            response = JsonResponse(userinfo(_bearer(request)))
            response['Cache-Control'] = 'no-store'
            return response
        except OAuthError as error:
            return _error(error)


class OAuthRevokeView(View):
    def post(self, request):
        try:
            revoke(_bearer(request))
        except OAuthError as error:
            return _error(error)
        return JsonResponse({'revoked': True})
