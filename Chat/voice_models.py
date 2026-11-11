import uuid

from django.db import models


class VoiceIdentity(models.Model):
    """Stable verified-phone identity, shared by accounts in different spaces."""
    digest = models.CharField(max_length=64, primary_key=True)
    active_call = models.ForeignKey('Chat.VoiceCall', null=True, blank=True, on_delete=models.SET_NULL)


class VoiceWeeklyQuota(models.Model):
    identity = models.ForeignKey(VoiceIdentity, on_delete=models.CASCADE)
    week = models.DateField()
    used_seconds = models.PositiveIntegerField(default=0)
    reserved_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['identity', 'week'], name='voice_identity_week_unique')]


class VoiceCall(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chat = models.ForeignKey('Chat.Chat', on_delete=models.CASCADE)
    caller = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='outgoing_voice_calls')
    callee = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='incoming_voice_calls')
    caller_identity = models.ForeignKey(VoiceIdentity, on_delete=models.PROTECT, related_name='+')
    callee_identity = models.ForeignKey(VoiceIdentity, on_delete=models.PROTECT, related_name='+')
    message = models.OneToOneField('Message.Message', null=True, on_delete=models.SET_NULL)
    client_key = models.UUIDField()
    caller_device = models.UUIDField()
    callee_device = models.UUIDField(null=True)
    week = models.DateField()
    state = models.CharField(max_length=16, default='ringing', db_index=True)
    reason = models.CharField(max_length=24, blank=True)
    max_seconds = models.PositiveIntegerField()
    duration_seconds = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True)
    connected_at = models.DateTimeField(null=True)
    ended_at = models.DateTimeField(null=True)
    expires_at = models.DateTimeField()
    deadline_at = models.DateTimeField(null=True)
    caller_seen_at = models.DateTimeField()
    callee_seen_at = models.DateTimeField(null=True)
    media_closed_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['caller', 'client_key'], name='voice_caller_client_unique')]
