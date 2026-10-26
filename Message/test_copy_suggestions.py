import json
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageCopy, MessageTypeChoice, MessageUserState
from Message.copy_suggestions import copy_suggestions, record_copies
from Space.models import Space
from User.models import User
from utils import auth


class CopySuggestionsTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Copy', slug='copy-test', email='copy@example.test')
        self.users = [User.create(self.space, name, verified=True) for name in ['Reader', 'Writer', 'Other', 'Fourth']]
        self.reader, self.writer, self.other, self.fourth = self.users
        self.chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, title='Group', created_by=self.writer)
        for user in self.users:
            ChatMember.objects.create(chat=self.chat, user=user, status=ChatMemberStatusChoice.ACTIVE,
                                      joined_at=timezone.now()-timedelta(days=2))
        self.headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.reader)['auth']}"}

    def message(self, content, user=None, age=0, kind=MessageTypeChoice.TEXT):
        message = Message.objects.create(chat=self.chat, user=user or self.writer, type=kind, content=content)
        if age:
            Message.objects.filter(pk=message.pk).update(created_at=timezone.now()-timedelta(hours=age))
            message.refresh_from_db()
        return message

    def test_unique_people_ranking_newest_ties_and_own_messages_last_after_limit(self):
        rows = [self.message(str(index)) for index in range(5)]
        for message in rows:
            record_copies(self.chat, self.other, [message.id])
        record_copies(self.chat, self.other, [rows[0].id])
        self.assertEqual(MessageCopy.objects.filter(message=rows[0]).count(), 1)
        record_copies(self.chat, self.fourth, [rows[0].id])
        own = self.message('Own', self.reader)
        record_copies(self.chat, self.other, [own.id])
        record_copies(self.chat, self.fourth, [own.id])
        result = copy_suggestions(self.chat, self.reader)
        self.assertEqual([row['message_id'] for row in result], [rows[0].id, rows[4].id, own.id])
        self.assertEqual([row['own'] for row in result], [False, False, True])
        self.assertNotIn('copy_count', result[0])
        record_copies(self.chat, self.reader, [rows[0].id])
        self.assertTrue(copy_suggestions(self.chat, self.reader)[0]['copied'])

    def test_shared_policy_rejects_old_own_non_text_hidden_deleted_and_pre_join_messages(self):
        old = self.message('Old', age=25)
        own = self.message('Mine', self.reader)
        media = self.message('Media', kind=MessageTypeChoice.IMAGE)
        deleted = self.message('Deleted')
        Message.objects.filter(pk=deleted.pk).update(is_deleted=True)
        hidden = self.message('Hidden')
        MessageUserState.objects.create(message=hidden, user=self.reader)
        self.assertEqual(record_copies(self.chat, self.reader, [old.id, own.id, media.id, deleted.id, hidden.id]), [])
        message = self.message('Before joining')
        record_copies(self.chat, self.other, [message.id])
        ChatMember.objects.filter(chat=self.chat, user=self.reader).update(joined_at=timezone.now()+timedelta(seconds=1))
        self.assertEqual(copy_suggestions(self.chat, self.reader), [])

    def test_api_permissions_scoping_and_idempotent_reporting(self):
        message = self.message('Useful')
        url = f'/messages/copies?chat_id={self.chat.id}'
        self.assertNotEqual(self.client.get(url).status_code, 200)
        for _ in range(2):
            response = self.client.post(url, json.dumps({'message_ids':[message.id]}), content_type='application/json', **self.headers)
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(MessageCopy.objects.filter(message=message).count(), 1)
        self.assertTrue(self.client.get(url, **self.headers).json()['body']['items'][0]['copied'])
        outsider = User.create(self.space, 'Outsider', verified=True)
        headers = {'HTTP_AUTHORIZATION':f"Bearer {auth.get_login_token(outsider)['auth']}"}
        self.assertNotEqual(self.client.get(url, **headers).status_code, 200)
        self.assertNotEqual(self.client.post(url, json.dumps({'message_ids':[message.id]}), content_type='application/json', **headers).status_code, 200)
        foreign = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, title='Foreign', created_by=self.writer)
        foreign_message = Message.objects.create(chat=foreign, user=self.writer, type=0, content='Secret')
        self.client.post(url, json.dumps({'message_ids':[foreign_message.id]}), content_type='application/json', **self.headers)
        self.assertFalse(MessageCopy.objects.filter(message=foreign_message).exists())

    def test_direct_chats_have_no_candidates_or_copy_records(self):
        self.chat.chat_type = ChatTypeChoice.DIRECT
        self.chat.save(update_fields=['chat_type'])
        message = self.message('Private')
        self.assertEqual(record_copies(self.chat, self.reader, [message.id]), [])
        self.assertEqual(copy_suggestions(self.chat, self.reader), [])
