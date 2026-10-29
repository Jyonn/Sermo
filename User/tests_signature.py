import json
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from Space.models import Space
from User.models import User, UserStateEvent, UserStateEventKindChoice
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Chat.test_compact import decode
from utils import auth


class SignatureTests(TestCase):
    def setUp(self):
        self.space=Space.objects.create(name='Signatures',slug='signatures',email='signature@example.com')
        self.me=User.create(self.space,'Me',password='TestPassword123!',verified=True)
        self.peer=User.create(self.space,'Peer',verified=True)
        self.chat=Chat.objects.create(space=self.space,chat_type=ChatTypeChoice.DIRECT,created_by=self.me)
        for user in (self.me,self.peer):
            ChatMember.objects.create(chat=self.chat,user=user,status=ChatMemberStatusChoice.ACTIVE,joined_at=timezone.now())
        self.headers={'HTTP_AUTHORIZATION':'Bearer '+auth.get_login_token(self.me)['auth']}

    def update(self,value):
        return self.client.post('/users/me/signature',json.dumps({'signature':value}),content_type='application/json',**self.headers)

    @patch('User.views.check_user_text')
    def test_signature_saved_exposed_and_live_chat_refresh_emitted(self,check):
        text='人间忽晚，山河已秋。'
        response=self.update(' '+text+' ')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['body']['signature'],text)
        self.me.refresh_from_db()
        for payload in (self.me.jsonl(),self.me.json_friend(),self.me.json_me()):
            self.assertEqual(payload['signature'],text)
        check.assert_called_once()
        self.assertTrue(UserStateEvent.objects.filter(user=self.peer,kind=UserStateEventKindChoice.CHATS_CHANGED,resource_id=self.chat.pk).exists())
        rows=decode(self.client.get('/chats/?format=compact-v1',**self.headers).json()['body'])
        self.assertEqual(next(m for m in rows[0]['members'] if m['user_id']==self.me.pk)['signature'],text)
        self.peer.refresh_from_db();self.assertEqual(self.peer.signature,'')

    @patch('User.views.check_user_text')
    def test_clear_idempotence_and_limits(self,check):
        self.assertEqual(self.update('x'*80).status_code,200)
        count=UserStateEvent.objects.filter(kind=UserStateEventKindChoice.CHATS_CHANGED).count()
        self.assertEqual(self.update('x'*80).status_code,200)
        self.assertEqual(UserStateEvent.objects.filter(kind=UserStateEventKindChoice.CHATS_CHANGED).count(),count)
        for invalid in ('x'*81,'two\nlines','tab\tvalue',{},None):
            self.assertNotEqual(self.update(invalid).status_code,200)
        self.assertEqual(self.update('').status_code,200)
        self.me.refresh_from_db();self.assertEqual(self.me.signature,'')

    @patch('User.views.check_user_text')
    def test_moderation_failure_and_anonymous_requests_do_not_change_signature(self,check):
        from User.validators import UserErrors
        check.side_effect=UserErrors.PASSWORD_NOT_SET
        self.assertNotEqual(self.update('Blocked').status_code,200)
        self.me.refresh_from_db();self.assertEqual(self.me.signature,'')
        self.assertNotEqual(self.client.post('/users/me/signature',json.dumps({'signature':'Anonymous'}),content_type='application/json').status_code,200)
