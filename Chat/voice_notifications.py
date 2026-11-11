from django.utils import timezone


def invitation_expired(event):
    if (event.payload or {}).get('kind') != 'voice_call':
        return False
    from Chat.voice_models import VoiceCall
    return not VoiceCall.objects.filter(pk=event.payload.get('call_id'), state='ringing', expires_at__gt=timezone.now()).exists()
