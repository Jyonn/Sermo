from datetime import timedelta
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.utils import timezone
from django.test import override_settings
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import OAuthGrant, User
from User.oauth import OAuthError, authorize, exchange, revoke
from User.oauth_requests import create, resolve
from User.oauth_registry import CHAT_SCOPES, approved
from User.tests_oauth import OAuthTests


@override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=True)
class DelegatedChatTests(OAuthTests):
    def setUp(self):
        super().setUp()
        revision=approved('stillfolio')
        revision.scopes=sorted(set(revision.scopes)|CHAT_SCOPES)
        revision.save(update_fields=['scopes'])
        self.chat=Chat.objects.create(space=self.space,chat_type=ChatTypeChoice.GROUP,created_by=self.user,title='fixture conversation')
        ChatMember.objects.create(chat=self.chat,user=self.user,status=ChatMemberStatusChoice.ACTIVE)
        self.check=patch('User.oauth_chat.check_user_text').start()
        self.notify=patch('User.oauth_chat.NotificationEvent.emit_message_notifications').start()
        self.addCleanup(patch.stopall)

    def token(self, scopes='chat.read chat.write chat.modify'):
        params=dict(client_id='stillfolio',redirect_uri=approved('stillfolio').redirect_uris[0],scope=scopes,
            state='s'*32,code_challenge=self.challenge,code_challenge_method='S256',response_type='code')
        flow=resolve(create({**params,'space_id':self.space.id})['request_id'])
        code=parse_qs(urlparse(authorize(self.user,**flow.parameters,flow=flow)).query)['code'][0]
        return exchange(client_id='stillfolio',redirect_uri=params['redirect_uri'],code=code,code_verifier=self.verifier)['access_token']

    def headers(self,token):
        return dict(HTTP_AUTHORIZATION='Bearer '+token,HTTP_X_OAUTH_CLIENT_ID='stillfolio')

    def messages(self,chat=None):return f'/users/oauth/chat/chats/{(chat or self.chat).id}/messages'

    def test_scoped_read_send_events_and_idempotency(self):
        header=self.headers(self.token())
        self.assertIn(self.chat.id,[chat['chat_id'] for chat in self.client.get('/users/oauth/chat/chats',**header).json()['chats']])
        body={'content':'fixture text','client_message_id':'fixture-id'}
        first=self.client.post(self.messages(),body,content_type='application/json',**header)
        self.assertEqual(first.status_code,200,first.content)
        second=self.client.post(self.messages(),body,content_type='application/json',**header)
        self.assertEqual(first.json(),second.json())
        self.assertEqual(Message.objects.filter(chat=self.chat,client_message_id='fixture-id').count(),1)
        history=self.client.get(self.messages(),**header).json()['messages']
        self.assertEqual(history[0]['content'],'fixture text')
        self.assertNotIn('payload',history[0])
        self.assertNotIn('email',history[0]['user'])
        events=self.client.get(f'/users/oauth/chat/chats/{self.chat.id}/events',**header)
        self.assertEqual(events.status_code,200)
        self.assertTrue(events.json()['events'])
        self.notify.assert_called_once()

    def test_permission_separation_and_normal_jwt_rejection(self):
        read=self.headers(self.token('chat.read'))
        self.assertEqual(self.client.post(self.messages(),{'content':'no','client_message_id':'no'},content_type='application/json',**read).status_code,403)
        self.assertEqual(self.client.get(f'/users/oauth/chat/chats/{self.chat.id}/events',**read).status_code,200)
        send=self.headers(self.token('chat.write'))
        self.assertEqual(self.client.get(self.messages(),**send).status_code,403)
        from utils.auth import get_login_token
        self.assertEqual(self.client.get(self.messages(),**self.headers(get_login_token(self.user)['auth'])).status_code,401)
        for obsolete in ('chat.events', 'chat.send'):
            with self.assertRaises(OAuthError):self.token(obsolete)
        with self.assertRaises(OAuthError):self.authorize(scope='chat.read')

    def test_wrong_client_space_membership_and_removed_permission(self):
        token=self.token()
        header=self.headers(token)
        self.assertEqual(self.client.get(self.messages(),**{**header,'HTTP_X_OAUTH_CLIENT_ID':'other'}).status_code,401)
        other=Space.objects.create(name='other',slug='other-chat-fixture',email='other@example.test')
        chat=Chat.objects.create(space=other,chat_type=ChatTypeChoice.GROUP,created_by=User.create(other,'other'),title='other')
        self.assertEqual(self.client.get(self.messages(chat),**header).status_code,403)
        outsider=Chat.objects.create(space=self.space,chat_type=ChatTypeChoice.GROUP,created_by=self.user,title='not joined')
        self.assertEqual(self.client.get(self.messages(outsider),**header).status_code,403)
        member=ChatMember.objects.get(chat=self.chat,user=self.user)
        member.status=ChatMemberStatusChoice.LEFT
        member.save(update_fields=['status'])
        self.assertEqual(self.client.get(self.messages(),**header).status_code,403)
        revision=approved('stillfolio'); revision.scopes=['profile']; revision.save(update_fields=['scopes'])
        self.assertEqual(self.client.get('/users/oauth/chat/chats',**header).status_code,401)

    def test_revoked_expired_and_lost_verification(self):
        token=self.token(); header=self.headers(token)
        User.objects.filter(pk=self.user.pk).update(phone_verified_at=None)
        self.assertEqual(self.client.get(self.messages(),**header).status_code,403)
        User.objects.filter(pk=self.user.pk).update(phone_verified_at=timezone.now())
        revoke(token)
        self.assertEqual(self.client.get(self.messages(),**header).status_code,401)
        token=self.token(); header=self.headers(token)
        OAuthGrant.objects.filter(code_used_at__isnull=False).update(access_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.client.get(self.messages(),**header).status_code,401)

    def test_media_or_impersonation_payload_and_disabled_space_rejected(self):
        header=self.headers(self.token())
        body={'content':'no','client_message_id':'no','user_id':999}
        self.assertEqual(self.client.post(self.messages(),body,content_type='application/json',**header).status_code,400)
        self.space.chat_enabled=False; self.space.save(update_fields=['chat_enabled'])
        self.assertEqual(self.client.get(self.messages(),**header).status_code,403)

    def test_media_urls_and_prejoin_history_never_escape(self):
        from Message.models import MessageEvent
        header=self.headers(self.token())
        image=Message.objects.create(chat=self.chat,user=self.user,type=MessageTypeChoice.IMAGE,content='https://private.example.test/secret-image')
        MessageEvent.record_created(image)
        response=self.client.get(self.messages(),**header)
        self.assertNotIn(b'private.example',response.content)
        self.assertIsNone(response.json()['messages'][0]['content'])
        response=self.client.get(f'/users/oauth/chat/chats/{self.chat.id}/events',**header)
        self.assertNotIn(b'private.example',response.content)
        member=ChatMember.objects.get(chat=self.chat,user=self.user)
        member.joined_at=timezone.now()+timedelta(seconds=1)
        member.save(update_fields=['joined_at'])
        self.assertEqual(self.client.get(self.messages(),**header).json()['messages'],[])
        self.assertEqual(self.client.get(f'/users/oauth/chat/chats/{self.chat.id}/events',**header).json()['events'],[])

    def test_disabled_client_and_explicit_scope_validation(self):
        header=self.headers(self.token())
        app=approved('stillfolio').application
        app.enabled=False; app.save(update_fields=['enabled'])
        self.assertEqual(self.client.get(self.messages(),**header).status_code,401)

    def test_disabled_space_group_cannot_be_read_by_guessing_its_id(self):
        header=self.headers(self.token())
        self.chat.is_space_group=True
        self.chat.save(update_fields=['is_space_group'])
        self.assertEqual(self.client.get(self.messages(),**header).status_code,403)
        self.assertNotIn(self.chat.id,[row['chat_id'] for row in self.client.get('/users/oauth/chat/chats',**header).json()['chats']])

    def test_rollout_off_blocks_request_exchange_and_existing_token(self):
        header=self.headers(self.token())
        with override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=False):
            with self.assertRaises(OAuthError): self.token()
            self.assertEqual(self.client.get(self.messages(),**header).status_code,401)
        self.assertEqual(self.client.get(self.messages(),**header).status_code,200)

    def test_modify_only_changes_own_pin_and_never_notification_fields(self):
        from Chat.models import ChatUserPreference
        path=f'/users/oauth/chat/chats/{self.chat.id}/preference'
        read=self.headers(self.token('chat.read'))
        self.assertEqual(self.client.post(path,{'pinned':True},content_type='application/json',**read).status_code,403)
        modify=self.headers(self.token('chat.modify'))
        self.assertEqual(self.client.get(self.messages(),**modify).status_code,403)
        preference=ChatUserPreference.ensure(self.chat,self.user)
        preference.notifications_muted=True
        preference.save(update_fields=['notifications_muted'])
        for body in ({'pinned':True,'notifications_muted':False},{'pinned':1},{'online_reminder_enabled':True}):
            self.assertEqual(self.client.post(path,body,content_type='application/json',**modify).status_code,400)
        response=self.client.post(path,{'pinned':True},content_type='application/json',**modify)
        self.assertEqual(response.json(),{'chat_id':self.chat.id,'pinned':True})
        preference.refresh_from_db()
        self.assertTrue(preference.pinned)
        self.assertTrue(preference.notifications_muted)
