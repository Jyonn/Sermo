"""Dedicated WebSocket sidecar. Keep one worker for bounded in-memory admission."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Sermo.settings')
django.setup()

from Message.live_transcription import stream_application as application
