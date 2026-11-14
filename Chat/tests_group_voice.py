import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from Chat import group_voice, voice
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Chat.voice_models import VoiceWeeklyQuota
from Message.models import Message
from Space.models import Space
from User.models import User
from utils import auth


@override_settings(VOICE_CALLS_ENABLED=True, VOICE_LIVEKIT_URL='ws://127.0.0.1:7880',
                   VOICE_LIVEKIT_API_KEY='local-test', VOICE_LIVEKIT_API_SECRET='test-only-secret')
class GroupVoiceTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Group voice', slug='group-voice', email='test@example.com')
        self.users = []
        for index in range(3):
            user = User.create(space, f'Participant {index}', verified=True)
            user.phone = f'+861380000032{index}'
            user.phone_verified_at = timezone.now()
            user.is_permanent_vip = True
            user.save(update_fields=['phone', 'phone_verified_at', 'is_permanent_vip'])
            self.users.append(user)
        self.chat = Chat.objects.create(space=space, chat_type=ChatTypeChoice.GROUP, title='Voice test',
                                       created_by=self.users[0])
        for user in self.users:
            ChatMember.objects.create(chat=self.chat, user=user, invited_by=self.users[0],
                                      status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now())

    def test_authenticated_api_routes_three_members_to_one_room(self):
        call_ids = set()
        for user in self.users:
            token = auth.get_login_token(user)['auth']
            headers = {'HTTP_AUTHORIZATION': f'Bearer {token}'}
            device = str(uuid.uuid4())
            result = self.client.post('/chats/voice/calls', {
                'chat_id': self.chat.id, 'client_key': str(uuid.uuid4()), 'device_id': device,
            }, content_type='application/json', **headers)
            self.assertEqual(result.status_code, 200)
            call = result.json()['body']
            self.assertEqual(call['state'], 'connecting')
            self.assertTrue(call['owned'])
            self.assertEqual(call['group_name'], self.chat.title)
            call_ids.add(call['call_id'])
            media = self.client.post(f"/chats/voice/calls/{call['call_id']}/media", {
                'device_id': device,
            }, content_type='application/json', **headers)
            self.assertEqual(media.status_code, 200)
            self.assertEqual(self.client.get('/chats/voice/media-admission',
                HTTP_X_VOICE_TOKEN=media.json()['body']['token']).status_code, 204)
        self.assertEqual(len(call_ids), 1)

    def test_three_members_join_one_room_and_settle_independently(self):
        self.assertEqual(group_voice.capability(self.chat, self.users[0])['remaining_seconds'], 1800)
        seats = [group_voice.join(self.chat, user, uuid.uuid4(), uuid.uuid4()) for user in self.users]
        call_id = seats[0].call_id
        self.assertEqual({seat.call_id for seat in seats}, {call_id})
        self.assertEqual(Message.objects.filter(chat=self.chat, type=14).count(), 1)
        now = timezone.now()
        for index, seat in enumerate(seats):
            tracks = [{'identity': str(seat.id), 'tracks': [{'source': 2}]}]
            with patch('Chat.voice_media.rpc', return_value={'participants': tracks}), patch(
                    'Chat.group_voice.timezone.now', return_value=now + timedelta(seconds=index)):
                group_voice.webhook(call_id, dict(event='track_published', participant={'identity': str(seat.id)}))
        for index, seat in enumerate(seats):
            with patch('Chat.group_voice.timezone.now', return_value=now + timedelta(seconds=4 + 2 * index)):
                ended = group_voice.action(call_id, seat.user, 'end', seat.device)
                duplicate = group_voice.action(call_id, seat.user, 'end', seat.device)
                self.assertEqual(ended['duration_seconds'], 4 + index)
                self.assertEqual(duplicate['duration_seconds'], ended['duration_seconds'])
                self.assertFalse(group_voice.admit(call_id, seat.id))
                if index < 2:
                    self.assertTrue(group_voice.admit(call_id, seats[index + 1].id))
        self.assertEqual(list(VoiceWeeklyQuota.objects.order_by('used_seconds').values_list(
            'used_seconds', 'reserved_seconds')), [(4, 0), (5, 0), (6, 0)])
        self.assertEqual(Message.objects.get(chat=self.chat, type=14)._payload_for_type()['state'], 'ended')

    def test_membership_and_eligibility_control_join_and_reentry(self):
        user = self.users[0]
        device, request = uuid.uuid4(), uuid.uuid4()
        seat = group_voice.join(self.chat, user, request, device)
        self.assertEqual(group_voice.join(self.chat, user, request, device).id, seat.id)
        self.assertTrue(voice.identities_busy([voice.identity_key(user)]))
        with self.assertRaisesRegex(voice.CallError, 'other_device'):
            group_voice.action(seat.call_id, user, 'media', uuid.uuid4())
        ChatMember.objects.filter(chat=self.chat, user=user).delete()
        self.assertFalse(group_voice.admit(seat.call_id, seat.id))
        self.assertFalse(voice.identities_busy([voice.identity_key(user)]))
        self.assertEqual(VoiceWeeklyQuota.objects.get().reserved_seconds, 0)
        with self.assertRaisesRegex(voice.CallError, 'forbidden'):
            group_voice.join(self.chat, user, uuid.uuid4(), device)
        other = self.users[1]
        other.is_permanent_vip = False
        with self.assertRaisesRegex(voice.CallError, 'vip_required'):
            group_voice.join(self.chat, other, uuid.uuid4(), device)
        other.is_permanent_vip = True
        other.phone_verified_at = None
        with self.assertRaisesRegex(voice.CallError, 'phone_required'):
            group_voice.join(self.chat, other, uuid.uuid4(), device)
