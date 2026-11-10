"""Connect the current Frienden space account to its verified Pocklo account."""
import re

import requests
from django.http import HttpResponseRedirect
from django.views import View

from Message.providers import pocklo as public_shares
from User.external_account_binding import (
    ExternalAccountBinding, ExternalBindingError as PockloError, ExternalCallbackView,
    ExternalConnectionView, payload, result,
)
from User.models import PockloConnection, PockloCredential
from utils import auth


binding = ExternalAccountBinding(
    'pocklo', 'https://disk.6-79.cn/api/oauth/', 'files.read shares.create',
    PockloConnection, PockloCredential,
)
access_for = binding.access_for
consume = binding.consume
finish = binding.finish
start = binding.start
status = binding.status
RESOURCE_ID = re.compile(r'[a-zA-Z0-9_-]{1,128}')


class PockloCallbackView(ExternalCallbackView):
    binding = binding


class PockloConnectionView(ExternalConnectionView):
    binding = binding


class PockloFilesView(View):
    @auth.require_user
    def get(self, request, resource_id=''):
        try:
            if resource_id and not RESOURCE_ID.fullmatch(resource_id):
                raise PockloError('invalid_request', 400)
            search = request.GET.get('q', '').strip()
            if len(search) > 256:
                raise PockloError('invalid_request', 400)
            access = access_for(request.user)
            path = 'files/' + (resource_id + '/' if resource_id else '')
            params = {'q': search} if search else {'offset': request.GET.get('offset', '0')}
            value = binding.remote(path, access=access, params=params)
            return result(value)
        except PockloError as error:
            return result({'error': error.code}, error.status)


class PockloShareView(View):
    @auth.require_user
    def post(self, request):
        try:
            data = payload(request)
            item, key = data.get('resource_id'), data.get('operation_id')
            if not isinstance(item, str) or not RESOURCE_ID.fullmatch(item) or not isinstance(key, str) or not re.fullmatch(public_shares.TOKEN, key):
                raise PockloError('invalid_request', 400)
            response = binding.remote('shares/', data={'resource_id': item}, access=access_for(request.user),
                              idempotency_key=f'{request.user.pk}:{key}')
            token = public_shares.share_token(response.get('url') or '')
            if not token:
                raise PockloError('unavailable', 503)
            return result({'url': f'https://{public_shares.HOST}/share/{token}'})
        except PockloError as error:
            return result({'error': error.code}, error.status)


class PockloShareDownloadView(View):
    resolve_target = staticmethod(public_shares.download_target)

    def get(self, request, token):
        try:
            target = self.resolve_target(str(token))
        except ValueError:
            return result({'error': 'share_unavailable'}, 404)
        except requests.RequestException:
            return result({'error': 'unavailable'}, 503)
        response = HttpResponseRedirect(target)
        response['Cache-Control'] = 'no-store'
        response['Referrer-Policy'] = 'no-referrer'
        return response


class PockloShareThumbnailView(PockloShareDownloadView):
    resolve_target = staticmethod(public_shares.thumbnail_target)
