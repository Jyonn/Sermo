"""Native installation preferences, separate from browser Web Push subscriptions."""
import json

from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views import View

from User.native_push_models import NativePushBinding as Binding, NativePushConflict
from utils import auth


def _error(conflict=False):
    return JsonResponse(dict(
        identifier='NATIVE_PUSH@CONFLICT' if conflict else 'NATIVE_PUSH@INVALID',
        message=_('Native push settings changed; refresh before retrying') if conflict else _('Invalid native push request'),
        body=None,
    ), status=409 if conflict else 400)


def _unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('duplicate fields')
    return result


def _body(request, required, optional=()):
    if len(request.body) > 4096:
        raise ValueError('request too large')
    value = json.loads(request.body, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or not set(required).issubset(value) or set(value) - set(required) - set(optional):
        raise ValueError('invalid fields')
    return value


def _status(binding, installation, environment):
    value = binding.public_status() if binding else dict(
        installation_id=str(installation), environment=environment,
        opted_in=False, revision=0, registered=False,
    )
    # Binding support is not a claim of provider configuration or delivery.
    return dict(value, delivery_available=False)


class NativePushBindingView(View):
    @auth.require_user
    def get(self, request):
        try:
            if set(request.GET) != {'installation_id', 'environment'} or any(len(request.GET.getlist(key)) != 1 for key in request.GET):
                raise ValueError('invalid query')
            environment = request.GET['environment']
            installation = Binding._identity(request.GET['installation_id'], environment, 0)
            binding = Binding.objects.filter(user=request.user, space_id=request.user.space_id,
                                             installation_id=installation, environment=environment).first()
            return _status(binding, installation, environment)
        except (ValueError, TypeError):
            return _error()

    @auth.require_user
    def put(self, request):
        try:
            value = _body(request, ('installation_id', 'environment', 'expected_revision', 'opted_in'), ('token',))
            binding = Binding.set_preference(request.user, value['installation_id'], value['environment'],
                opted_in=value['opted_in'], expected_revision=value['expected_revision'], token=value.get('token'))
            return _status(binding, binding.installation_id, binding.environment)
        except NativePushConflict:
            return _error(conflict=True)
        except (ValueError, TypeError, UnicodeDecodeError):
            return _error()

    @auth.require_user
    def delete(self, request):
        try:
            value = _body(request, ('installation_id', 'environment', 'expected_revision'))
            binding = Binding.set_preference(request.user, value['installation_id'], value['environment'],
                opted_in=False, expected_revision=value['expected_revision'])
            return _status(binding, binding.installation_id, binding.environment)
        except NativePushConflict:
            return _error(conflict=True)
        except (ValueError, TypeError, UnicodeDecodeError):
            return _error()


class NativePushTokenView(View):
    @auth.require_user
    def post(self, request):
        try:
            value = _body(request, ('installation_id', 'environment', 'expected_revision', 'token'))
            binding = Binding.refresh_registration(request.user, value['installation_id'], value['environment'],
                token=value['token'], expected_revision=value['expected_revision'])
            installation = Binding._identity(value['installation_id'], value['environment'], value['expected_revision'])
            return _status(binding, installation, value['environment'])
        except NativePushConflict:
            return _error(conflict=True)
        except (ValueError, TypeError, UnicodeDecodeError):
            return _error()
