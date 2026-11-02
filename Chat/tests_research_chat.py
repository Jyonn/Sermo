import datetime

from django.test import TestCase
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatReadState, ResearchProject
from Message.models import Message, MessageUserState
from Space.models import Space
from User.models import User
from utils import auth


class ResearchChatReadTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(slug='research-chat-read', name='Study', email='a@example.com', research_enabled=True, chat_enabled=False)
        self.user = User.create(self.space, 'Member', verified=True)
        self.other = User.create(self.space, 'Other', verified=True)
        self.chat = Chat.objects.create(space=self.space, created_by=self.other, chat_type=1, purpose=2, title='Canonical study')
        self.member = ChatMember.objects.create(chat=self.chat, user=self.user, status=ChatMemberStatusChoice.ACTIVE)
        self.project = ResearchProject.objects.create(chat=self.chat, start=datetime.date(2026, 10, 30))

    def call(self, user=None, project_id=None, query=None, authenticated=True):
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user or self.user)['auth']} if authenticated else {}
        return self.client.get('/research/' + str(project_id or self.project.pk) + '/chat/', query or {}, **headers)

    def test_reads_canonical_chat_independently_of_normal_chat_without_marking_read(self):
        Message.objects.create(chat=self.chat, user=self.other, type=0, content='Study message')
        before = ChatReadState.objects.count()
        response = self.call()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        body = response.json()['body']
        self.assertEqual((body['chat_id'], body['purpose'], body['title']), (self.chat.pk, 'research', 'Canonical study'))
        self.assertEqual(body['last_message']['content'], 'Study message')
        self.assertEqual(ChatReadState.objects.count(), before)

    def test_authentication_and_research_gate(self):
        self.assertNotEqual(self.call(authenticated=False).status_code, 200)
        self.space.research_enabled = False; self.space.save(update_fields=['research_enabled'])
        self.assertEqual(self.call().status_code, 403)

    def test_preview_respects_hidden_and_recalled_messages(self):
        message = Message.objects.create(chat=self.chat, user=self.other, type=0, content='Hidden study message')
        state = MessageUserState.objects.create(message=message, user=self.user)
        self.assertIsNone(self.call().json()['body']['last_message'])
        state.delete()
        message.is_deleted = True; message.save(update_fields=['is_deleted'])
        self.assertIsNone(self.call().json()['body']['last_message'])

    def test_only_active_members_can_read_and_deleted_chat_is_unavailable(self):
        self.assertEqual(self.call(user=self.other).status_code, 404)
        self.member.status = ChatMemberStatusChoice.LEFT; self.member.save(update_fields=['status'])
        self.assertEqual(self.call().status_code, 404)
        self.member.status = ChatMemberStatusChoice.ACTIVE; self.member.save(update_fields=['status'])
        self.chat.is_deleted = True; self.chat.save(update_fields=['is_deleted'])
        self.assertEqual(self.call().status_code, 404)

    def test_other_space_unknown_identity_query_and_nonresearch_chat_are_rejected(self):
        space = Space.objects.create(slug='research-chat-foreign', name='Other', email='b@example.com', research_enabled=True)
        foreign = User.create(space, 'Foreign', verified=True)
        self.assertEqual(self.call(user=foreign).status_code, 404)
        self.assertEqual(self.call(project_id=2 ** 100).status_code, 404)
        self.assertEqual(self.call(query={'user_id': str(self.user.pk)}).status_code, 404)
        self.chat.purpose = 0; self.chat.save(update_fields=['purpose'])
        self.assertEqual(self.call().status_code, 404)

    def test_errors_and_unsupported_methods_are_private_and_not_cached(self):
        response = self.call(authenticated=False)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        response = self.client.post('/research/' + str(self.project.pk) + '/chat/')
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
