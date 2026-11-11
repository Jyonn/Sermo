import json
import base64
import hashlib
import time
import jwt
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from Chat import voice
from Chat.models import Chat, ChatTypeChoice
from Chat.voice_models import VoiceCall, VoiceIdentity, VoiceWeeklyQuota
from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import Message, MessageEvent, MessageEventTypeChoice, MessageTypeChoice
from Space.models import Space
from User.models import NotificationEvent, NotificationDelivery, NotificationTopicPreference, NotificationEventTypeChoice, User
from utils import auth


@override_settings(VOICE_CALLS_ENABLED=True, VOICE_CALL_NOTIFICATIONS_ENABLED=False,
                   VOICE_LIVEKIT_URL='ws://localhost:7880', VOICE_LIVEKIT_API_KEY='local-test', VOICE_LIVEKIT_API_SECRET='test-only-secret')
class VoiceCallTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Voice', slug='voice-tests', email='admin@example.com')
        self.caller = self.user('Caller', '+8613800000101')
        self.callee = self.user('Callee', '+8613800000102')
        self.chat = self.direct(self.caller, self.callee)
        self.device, self.peer_device = uuid.uuid4(), uuid.uuid4()

    def user(self, name, phone, space=None):
        user = User.create(space or self.space, name, verified=True)
        user.phone, user.phone_verified_at, user.is_permanent_vip = phone, timezone.now(), True
        user.save(update_fields=['phone', 'phone_verified_at', 'is_permanent_vip'])
        return user

    def direct(self, one, two):
        Friendship.objects.create(space=one.space, user_low=one, user_high=two, status=FriendshipStatusChoice.ACCEPTED)
        return Chat.get_or_create_direct(one, two)

    def create(self, key=None):
        return voice.create(self.chat, self.caller, key or uuid.uuid4(), self.device)

    def authorize(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer '+auth.get_login_token(user)['auth']}

    def activate(self, call, seconds=0):
        call = voice.mutate(call.id, self.callee, 'accept', self.peer_device)
        now = timezone.now()
        call.state, call.connected_at = 'active', now-timedelta(seconds=seconds)
        call.deadline_at = call.connected_at+timedelta(seconds=call.max_seconds)
        call.caller_seen_at = call.callee_seen_at = now
        call.save()
        return call

    def test_non_vip_cannot_create_bubble_or_invitation(self):
        self.callee.is_permanent_vip = False
        self.callee.save(update_fields=['is_permanent_vip'])
        with self.assertRaises(voice.CallError) as err:
            self.create()
        self.assertEqual(err.exception.reason, 'vip_required')
        self.assertFalse(VoiceCall.objects.exists())
        self.assertFalse(Message.objects.filter(type=14).exists())

    def test_recipient_without_balance_cannot_be_called(self):
        key = voice.identity_key(self.callee)
        VoiceIdentity.objects.create(digest=key)
        VoiceWeeklyQuota.objects.create(identity_id=key, week=voice.week_at(timezone.now()), used_seconds=300)
        self.assertEqual(voice.capability(self.chat, self.caller)['reason'], 'peer_quota_empty')
        with self.assertRaises(voice.CallError):
            self.create()
        self.assertFalse(NotificationEvent.objects.filter(event_type=11).exists())

    def test_same_phone_cannot_call_itself(self):
        self.callee.phone = self.caller.phone
        self.callee.save(update_fields=['phone'])
        self.assertEqual(voice.capability(self.chat, self.caller)['reason'], 'same_person')

    def test_reserve_minimum_and_idempotent_create(self):
        key = voice.identity_key(self.callee)
        VoiceIdentity.objects.create(digest=key)
        VoiceWeeklyQuota.objects.create(identity_id=key, week=voice.week_at(timezone.now()), used_seconds=215)
        request_key = uuid.uuid4()
        call = self.create(request_key)
        self.assertEqual(call.max_seconds, 85)
        self.assertEqual(self.create(request_key).id, call.id)
        self.assertEqual(Message.objects.filter(type=14).count(), 1)
        self.assertEqual(NotificationEvent.objects.filter(event_type=11).count(), 1)
        self.assertEqual(list(VoiceWeeklyQuota.objects.values_list('reserved_seconds', flat=True)), [85, 85])

    def test_no_charge_for_cancel_and_reservation_released(self):
        call = self.create()
        voice.mutate(call.id, self.caller, 'end', self.device)
        voice.mutate(call.id, self.caller, 'end', self.device)
        self.assertEqual(sum(VoiceWeeklyQuota.objects.values_list('used_seconds', flat=True)), 0)
        self.assertEqual(sum(VoiceWeeklyQuota.objects.values_list('reserved_seconds', flat=True)), 0)
        self.assertFalse(VoiceIdentity.objects.exclude(active_call=None).exists())

    def test_both_participants_charged_once_with_single_record(self):
        call = self.activate(self.create(), seconds=7.1)
        ended = voice.mutate(call.id, self.caller, 'end', self.device)
        voice.mutate(call.id, self.callee, 'end', self.peer_device)
        self.assertEqual(ended.duration_seconds, 8)
        self.assertEqual(list(VoiceWeeklyQuota.objects.values_list('used_seconds', flat=True)), [8, 8])
        self.assertEqual(Message.objects.filter(type=14).count(), 1)
        self.assertEqual(Message.objects.get(pk=call.message_id)._payload_for_type()['state'], 'ended')

    def test_timeout_and_quota_capped_on_recovery(self):
        call = self.activate(self.create(), seconds=310)
        voice.reconcile()
        call.refresh_from_db()
        self.assertEqual((call.reason, call.duration_seconds), ('quota', 300))
        self.assertEqual(list(VoiceWeeklyQuota.objects.values_list('used_seconds', flat=True)), [300, 300])

    def test_week_reset_uses_shanghai_monday_not_server_timezone(self):
        sunday = datetime(2026, 11, 15, 15, 59, 59, tzinfo=dt_timezone.utc)
        monday = sunday+timedelta(seconds=1)
        self.assertEqual(str(voice.week_at(sunday)), '2026-11-09')
        self.assertEqual(str(voice.week_at(monday)), '2026-11-16')
        self.assertEqual(voice.week_end(voice.week_at(sunday)), monday)

    def test_cross_space_phone_shares_ledger_and_busy_slot(self):
        call = self.activate(self.create(), seconds=20)
        voice.mutate(call.id, self.caller, 'end', self.device)
        other_space = Space.objects.create(name='Other', slug='other-voice', email='other@example.com')
        alias = self.user('Alias', self.caller.phone, other_space)
        other = self.user('Other', '+8613800000103', other_space)
        chat = self.direct(alias, other)
        self.assertEqual(voice.capability(chat, alias)['remaining_seconds'], 279)
        voice.create(chat, alias, uuid.uuid4(), uuid.uuid4())
        with self.assertRaises(voice.CallError):
            self.create()

    def test_foreign_user_and_device_cannot_control_or_get_credentials(self):
        call = self.create()
        with self.assertRaises(voice.CallError):
            voice.mutate(call.id, self.caller, 'accept', self.device)
        voice.mutate(call.id, self.callee, 'accept', self.peer_device)
        with self.assertRaises(voice.CallError) as err:
            voice.mutate(call.id, self.callee, 'accept', uuid.uuid4())
        self.assertEqual(err.exception.reason, 'other_device')
        response = self.client.post(f'/chats/voice/calls/{call.id}/media', data=json.dumps({'device_id':str(uuid.uuid4())}), content_type='application/json', **self.authorize(self.caller))
        self.assertEqual(response.status_code, 409)

    def test_heartbeat_loss_settles_and_releases_busy_slot(self):
        call = self.activate(self.create(), seconds=4)
        call.caller_seen_at = timezone.now()-timedelta(seconds=11)
        call.save(update_fields=['caller_seen_at'])
        voice.reconcile()
        call.refresh_from_db()
        self.assertEqual(call.reason, 'disconnected')
        self.assertFalse(VoiceIdentity.objects.exclude(active_call=None).exists())

    def test_updates_sync_without_duplicate_created_events(self):
        call = self.create()
        voice.mutate(call.id, self.callee, 'accept', self.peer_device)
        voice.mutate(call.id, self.callee, 'end', self.peer_device)
        self.assertEqual(MessageEvent.objects.filter(message_id=call.message_id, type=MessageEventTypeChoice.CREATED).count(), 1)
        events = MessageEvent.sync_for_user(self.callee, 0, 100)['events']
        updates = [event for event in events if event['type']=='message.updated']
        self.assertEqual(len(updates), 2)
        self.assertTrue(all(event.get('message', {}).get('payload', {}).get('state')=='ended' for event in updates))

    def test_client_cannot_forge_call_message(self):
        with self.assertRaises(Exception):
            Message.create(self.chat, self.caller, MessageTypeChoice.VOICE_CALL, '{}')

    def test_invalid_and_unauthorized_api_requests(self):
        response = self.client.post('/chats/voice/calls', data='{}', content_type='application/json', **self.authorize(self.caller))
        self.assertEqual(response.status_code, 400)
        response = self.client.get('/chats/voice/current')
        self.assertEqual(response.status_code, 400)  # Existing auth rejects a missing header as malformed.

    def test_disabled_by_default_and_webhook_signature_required(self):
        with override_settings(VOICE_CALLS_ENABLED=False):
            self.assertFalse(voice.capability(self.chat, self.caller)['enabled'])
        response = self.client.post('/chats/voice/webhook', data='{}', content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_expired_invitation_is_not_delivered(self):
        self.create()
        event = NotificationEvent.objects.filter(event_type=NotificationEventTypeChoice.VOICE_CALL).get()
        VoiceCall.objects.update(state='ended')
        with patch.object(NotificationDelivery, 'enqueue_web_for_event') as web:
            self.assertEqual(NotificationDelivery.enqueue_voice_call(event), [])
            web.assert_not_called()

    def webhook(self, call, event='track_published'):
        body=json.dumps({'room':{'name':f'frienden-{call.id}'},'event':event}).encode()
        signature=jwt.encode({'iss':'local-test','exp':int(time.time())+60,
            'sha256':base64.b64encode(hashlib.sha256(body).digest()).decode()},'test-only-secret',algorithm='HS256')
        return self.client.post('/chats/voice/webhook',data=body,content_type='application/json',HTTP_AUTHORIZATION=signature)

    def test_signed_webhook_requires_both_microphones_and_is_idempotent(self):
        call=self.create()
        voice.mutate(call.id,self.callee,'accept',self.peer_device)
        with patch('Chat.voice_media.rpc',return_value={'participants':[{'identity':str(self.caller.id),'tracks':[{'source':'MICROPHONE'}]}]}):
            self.assertEqual(self.webhook(call).status_code,200)
        call.refresh_from_db()
        self.assertEqual(call.state,'connecting')
        rows=[{'identity':str(user.id),'tracks':[{'source':'MICROPHONE','muted':True}]} for user in [self.caller,self.callee]]
        with patch('Chat.voice_media.rpc',return_value={'participants':rows}):
            self.assertEqual(self.webhook(call).status_code,200)
            call.refresh_from_db()
            started=call.connected_at
            self.assertEqual(call.state,'active')
            self.assertEqual(self.webhook(call).status_code,200)
        call.refresh_from_db()
        self.assertEqual(call.connected_at,started)

    def test_vip_revoked_before_accept_releases_reservation(self):
        call=self.create()
        self.caller.is_permanent_vip=False
        self.caller.save(update_fields=['is_permanent_vip'])
        ended=voice.mutate(call.id,self.callee,'accept',self.peer_device)
        self.assertEqual((ended.state,ended.reason,ended.duration_seconds),('ended','ineligible',0))
        self.assertEqual(sum(VoiceWeeklyQuota.objects.values_list('reserved_seconds',flat=True)),0)

    def test_token_limits_identity_room_and_microphone(self):
        from Chat.voice_media import token
        call=self.create()
        claims=jwt.decode(token(call,self.caller),'test-only-secret',algorithms=['HS256'])
        self.assertEqual(claims['sub'],str(self.caller.id))
        self.assertEqual(claims['video']['room'],f'frienden-{call.id}')
        self.assertEqual(claims['video']['canPublishSources'],['microphone'])
        self.assertFalse(claims['video']['canPublishData'])

    def test_muted_conversation_disables_invitation_routes(self):
        from Chat.models import ChatUserPreference
        self.create()
        event=NotificationEvent.objects.get(event_type=NotificationEventTypeChoice.VOICE_CALL)
        ChatUserPreference.objects.create(chat=self.chat,user=self.callee,notifications_muted=True)
        self.assertFalse(NotificationTopicPreference.is_enabled_for_event(event,0))
        self.assertFalse(NotificationTopicPreference.is_enabled_for_event(event,3))

    def test_invalid_call_id_has_controlled_error(self):
        with self.assertRaises(voice.CallError) as err:
            voice.mutate('not-a-uuid',self.caller,'end',self.device)
        self.assertEqual(err.exception.reason,'not_found')
