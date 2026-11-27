"""Account-scoped preferences for the web interface."""
import json
import re

from django.db import transaction
from django.http import JsonResponse
from django.views import View

from User.models import User
from utils import auth

CHOICES = {
    'appearance': ('system', 'light', 'dark'),
    'mobile_voice_input': ('hold', 'tap'),
    'square_entry': ('friends', 'explore'),
    'square_layout_mode': ('unified', 'per-channel'),
    **{f'square_layout_{channel}': ('list', 'masonry') for channel in ('explore', 'friends', 'user')},
}
THEMES = ('default', 'christmas-2026', 'birthday-botanical')
TOKEN = re.compile(r'[A-Za-z0-9:_-]{1,120}')
MAX_SEEN = 20


def valid_setting(key, value):
    if key in CHOICES:
        return isinstance(value, str) and value in CHOICES[key]
    if key == 'christmas_background_override':
        return isinstance(value, bool)
    if key == 'personal_theme':
        return (isinstance(value, dict) and set(value) == {'theme', 'period'}
                and value['theme'] in THEMES and isinstance(value['period'], str)
                and 1 <= len(value['period']) <= 365
                and all(TOKEN.fullmatch(token) for token in value['period'].split('|')))
    if key == 'seen_theme_tokens':
        return (isinstance(value, list) and len(value) <= MAX_SEEN
                and all(isinstance(token, str) and TOKEN.fullmatch(token) for token in value))
    return False


def snapshot(user):
    preferences = dict(user.interface_preferences or {})
    revision = preferences.pop('revision', 0)
    return {'values': preferences, 'revision': revision}


class InterfacePreferencesView(View):
    @auth.require_user
    def get(self, request):
        return snapshot(request.user)

    @auth.require_user
    def post(self, request):
        try:
            if len(request.body) > 8192:
                raise ValueError()
            payload = json.loads(request.body)
            if not isinstance(payload, dict) or len(payload) != 1:
                raise ValueError()
            operation, changes = next(iter(payload.items()))
            if operation not in ('patch', 'migrate') or not isinstance(changes, dict) or not changes:
                raise ValueError()
            if not all(valid_setting(key, value) for key, value in changes.items()):
                raise ValueError()
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Invalid interface preferences'}, status=400)

        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            saved = user.interface_preferences or {}
            updated = dict(saved)
            for key, value in changes.items():
                if key == 'seen_theme_tokens':
                    updated[key] = list(dict.fromkeys([*saved.get(key, []), *value]))[-MAX_SEEN:]
                elif operation == 'patch' or key not in saved:
                    updated[key] = value
            if updated != saved:
                updated['revision'] = saved.get('revision', 0) + 1
                user.interface_preferences = updated
                user.save(update_fields=['interface_preferences'])
            return snapshot(user)
