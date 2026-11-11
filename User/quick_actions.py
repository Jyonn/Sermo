"""Private, account-scoped chat shortcut preferences."""
import json

from django.db import transaction
from django.http import JsonResponse
from django.views import View

from User.models import User, UserStateEvent, UserStateEventKindChoice
from utils import auth

ALLOWED = {
    'direct': ['locked', 'calendar', 'search', 'call'],
    'group': ['locked', 'copy', 'status', 'search'],
}
DEFAULTS = {'direct': ['locked', 'calendar'], 'group': ['locked', 'copy', 'status']}


def valid_actions(kind, actions):
    return (isinstance(kind, str) and kind in ALLOWED and isinstance(actions, list) and 1 <= len(actions) <= 4
            and all(isinstance(item, str) and item in ALLOWED[kind] for item in actions)
            and len(set(actions)) == len(actions))


def snapshot(value):
    return {
        'preferences': {kind: value.get(kind, default) for kind, default in DEFAULTS.items()},
        'configured': any(kind in value for kind in ALLOWED),
        'revision': value.get('revision', 0),
        'call_hint_seen': bool(value.get('call_hint_seen', False)),
    }


class ChatQuickActionsView(View):
    @auth.require_user
    def get(self, request):
        return snapshot(request.user.chat_quick_actions)

    @auth.require_user
    def post(self, request):
        try:
            data = json.loads(request.body)
            if not isinstance(data, dict):
                raise ValueError()
            if set(data) == {'kind', 'actions'}:
                if not valid_actions(data['kind'], data['actions']):
                    raise ValueError()
            elif set(data) == {'migrate'}:
                if (not isinstance(data['migrate'], dict) or set(data['migrate']) != set(ALLOWED)
                        or not all(valid_actions(kind, actions) for kind, actions in data['migrate'].items())):
                    raise ValueError()
            elif data != {'acknowledge_call_hint': True}:
                raise ValueError()
        except (ValueError, TypeError):
            return JsonResponse({'error': 'Invalid chat shortcuts'}, status=400)
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            previous = user.chat_quick_actions
            value = dict(previous)
            if 'kind' in data:
                value[data['kind']] = data['actions']
                if data['kind'] == 'direct' and 'call' in data['actions']:
                    value['call_hint_seen'] = True
            elif 'migrate' in data:
                if not any(kind in value for kind in ALLOWED):
                    value.update(data['migrate'])
            else:
                value['call_hint_seen'] = True
            if value != previous:
                value['revision'] = previous.get('revision', 0) + 1
                user.chat_quick_actions = value
                user.save(update_fields=['chat_quick_actions'])
                UserStateEvent.emit(user, UserStateEventKindChoice.CHAT_SHORTCUTS_CHANGED)
            return snapshot(value)
