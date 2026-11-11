"""Opt-in, secret-free local call sandbox. Never connects to the production DB."""
import os
from Sermo.isolated_test_settings import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3',
                         'NAME': os.environ.get('VOICE_LOCAL_DB', '/tmp/frienden-voice-local.sqlite3')}}
VOICE_CALLS_ENABLED = True
VOICE_CALL_NOTIFICATIONS_ENABLED = False
VOICE_LIVEKIT_URL = os.environ.get('VOICE_LIVEKIT_URL', 'ws://127.0.0.1:7880')
VOICE_LIVEKIT_API_KEY = os.environ.get('VOICE_LIVEKIT_API_KEY', 'devkey')
VOICE_LIVEKIT_API_SECRET = os.environ.get('VOICE_LIVEKIT_API_SECRET', 'local-voice-development-key-not-for-production')
