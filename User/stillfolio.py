import base64
import re

from cryptography.fernet import Fernet, InvalidToken
from django.utils.crypto import salted_hmac
from django.views import View

from Config.models import Config, CI
from Message.providers.stillfolio import StillfolioProvider
from User.external_account_binding import (
    ExternalAccountBinding, ExternalBindingError, ExternalCallbackView,
    ExternalConnectionView, payload, result,
)
from User.models import StillfolioConnection, StillfolioCredential
from utils import auth


CLIENT_ID = 'stillfolio_e58b090cd054901b8dcddcffb705e657fa83'


def config_cipher():
    return Fernet(base64.urlsafe_b64encode(salted_hmac('stillfolio-config', 'credentials', algorithm='sha256').digest()))


class StillfolioAccountBinding(ExternalAccountBinding):
    def config(self):
        client = Config.get_value_by_key(CI.STILLFOLIO_CLIENT_ID) or CLIENT_ID
        secret = Config.get_value_by_key(CI.STILLFOLIO_CLIENT_SECRET)
        key = Config.get_value_by_key(CI.STILLFOLIO_STORAGE_KEY)
        if secret and key:
            try:
                cipher = config_cipher()
                return client, cipher.decrypt(secret.encode()).decode(), Fernet(cipher.decrypt(key.encode()))
            except (InvalidToken, ValueError):
                pass
        raise ExternalBindingError('unavailable', 503)


binding = StillfolioAccountBinding(
    'stillfolio', f'https://{StillfolioProvider.API_HOST}/api/oauth/', 'albums.read shares.create',
    StillfolioConnection, StillfolioCredential,
)
UUID_PATTERN = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
SHARE_PATTERN = re.compile(rf'https://{re.escape(StillfolioProvider.HOST)}/share/({UUID_PATTERN.pattern})')


class StillfolioCallbackView(ExternalCallbackView):
    binding = binding


class StillfolioConnectionView(ExternalConnectionView):
    binding = binding


class StillfolioAlbumsView(View):
    @auth.require_user
    def get(self, request):
        try:
            offset = request.GET.get('offset', '0')
            if not re.fullmatch(r'[0-9]{1,8}', offset):
                raise ExternalBindingError('invalid_request', 400)
            value = binding.remote('albums/', access=binding.access_for(request.user), params={'offset': offset})
            if not isinstance(value.get('albums'), list):
                raise ExternalBindingError('unavailable', 503)
            return result({'albums': value['albums'], 'next_offset': value.get('next_offset')})
        except ExternalBindingError as error:
            return result({'error': error.code}, error.status)


class StillfolioShareView(View):
    @auth.require_user
    def post(self, request):
        try:
            data = payload(request)
            album_id, operation_id = data.get('album_id'), data.get('operation_id')
            if any(not isinstance(value, str) or not UUID_PATTERN.fullmatch(value) for value in (album_id, operation_id)):
                raise ExternalBindingError('invalid_request', 400)
            share = binding.remote('shares/', data={'album_id': album_id}, access=binding.access_for(request.user),
                                   idempotency_key=f'{request.user.pk}:{operation_id}')
            url = share.get('url')
            if not isinstance(url, str) or not SHARE_PATTERN.fullmatch(url):
                raise ExternalBindingError('unavailable', 503)
            return result({'url': url})
        except ExternalBindingError as error:
            return result({'error': error.code}, error.status)
