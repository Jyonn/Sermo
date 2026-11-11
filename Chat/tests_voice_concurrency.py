"""Real row-lock tests: run against a dedicated MySQL test schema."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import close_old_connections, connection
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from Chat import voice
from Chat.models import Chat
from Chat.voice_models import VoiceCall, VoiceWeeklyQuota
from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import Message
from Space.models import Space
from User.models import User


@skipUnless(connection.vendor == 'mysql', 'Requires MySQL row locks, not SQLite')
@override_settings(VOICE_CALLS_ENABLED=True, VOICE_CALL_NOTIFICATIONS_ENABLED=False,
    VOICE_LIVEKIT_URL='ws://localhost:7880', VOICE_LIVEKIT_API_KEY='test',
    VOICE_LIVEKIT_API_SECRET='isolated-mysql-test-secret-at-least-32-bytes')
class VoiceCallConcurrencyTests(TransactionTestCase):
    def setUp(self):
        space = Space.objects.create(name='Concurrency', slug='voice-race', email='test@example.invalid')
        self.users = []
        for index in range(2):
            user = User.create(space, f'RTC {index}', verified=True)
            user.phone = f'+861380000920{index}'
            user.phone_verified_at = timezone.now()
            user.is_permanent_vip = True
            user.save(update_fields=['phone', 'phone_verified_at', 'is_permanent_vip'])
            self.users.append(user)
        Friendship.objects.create(space=space, user_low=self.users[0], user_high=self.users[1], status=FriendshipStatusChoice.ACCEPTED)
        self.chat = Chat.get_or_create_direct(*self.users)
        self.devices = [uuid.uuid4(), uuid.uuid4()]

    def race(self, operation):
        barrier = Barrier(2)
        def invoke(index):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return operation(index)
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(invoke, range(2)))

    def test_simultaneous_calls_reserve_only_once(self):
        def create(index):
            try:
                call = voice.create(self.chat, self.users[index], uuid.uuid4(), self.devices[index])
                return ('created', str(call.id))
            except voice.CallError as exc:
                return (exc.reason, None)
        results = self.race(create)
        self.assertEqual(sorted(result[0] for result in results), ['busy', 'created'])
        self.assertEqual(VoiceCall.objects.count(), 1)
        self.assertEqual(Message.objects.filter(type=14).count(), 1)
        self.assertEqual(list(VoiceWeeklyQuota.objects.values_list('reserved_seconds', flat=True)), [1800, 1800])

    def test_simultaneous_hangups_charge_each_identity_once(self):
        call = voice.create(self.chat, self.users[0], uuid.uuid4(), self.devices[0])
        voice.mutate(call.id, self.users[1], 'accept', self.devices[1])
        now = timezone.now()
        call.refresh_from_db()
        call.state = 'active'
        call.connected_at = now-timedelta(seconds=2.1)
        call.deadline_at = now+timedelta(seconds=290)
        call.caller_seen_at = call.callee_seen_at = now
        call.save()
        self.race(lambda index: voice.mutate(call.id, self.users[index], 'end', self.devices[index]))
        call.refresh_from_db()
        self.assertEqual(call.state, 'ended')
        self.assertGreaterEqual(call.duration_seconds, 3)
        self.assertEqual(list(VoiceWeeklyQuota.objects.values_list('used_seconds', flat=True)), [call.duration_seconds]*2)
        self.assertEqual(list(VoiceWeeklyQuota.objects.values_list('reserved_seconds', flat=True)), [0, 0])
