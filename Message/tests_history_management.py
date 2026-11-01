import json
from django.test import TestCase
from django.utils import timezone
from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import Message, MessageTypeChoice, MessageRetention, MessageUserState, MessageEvent, MessageEventTypeChoice
from Message.history_management import read_history, manage_history
from Space.models import Space
from User.models import User
from utils import auth


class HistoryManagementTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='History', slug='history', email='admin@example.com', admin_phone_verified_at=timezone.now())
        self.user = User.create(self.space, 'Member', password='secret1', email='one@example.com', verified=True)
        self.peer = User.create(self.space, 'Peer', email='two@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        self.chat = Chat.get_or_create_direct(self.user, self.peer)
        self.one = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'keep me')
        self.two = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'delete me')
        self.system = Message.objects.create(chat=self.chat, user=self.peer, type=MessageTypeChoice.SYSTEM, content=json.dumps({'text': 'system event'}))
        self.filters = dict(snapshot=self.system.id)

    def test_private_retention_excludes_cleanup_but_does_not_affect_peer(self):
        manage_history(self.chat, self.user, self.filters, 'retain', [self.one.id])
        preview = manage_history(self.chat, self.user, self.filters, 'preview', [])
        self.assertEqual(preview['retained_count'], 1)
        result = manage_history(self.chat, self.user, self.filters, 'delete', [])
        self.assertNotIn(self.one.id, result['changed_ids'])
        self.assertIn(self.system.id, result['changed_ids'])
        self.assertFalse(MessageUserState.objects.filter(user=self.peer).exists())
        self.assertFalse(Message.objects.filter(is_deleted=True).exists())
        self.assertTrue(MessageEvent.objects.filter(message=self.system, target_user=self.user, type=MessageEventTypeChoice.HIDDEN).exists())
        self.assertEqual(Message.clear_for_user(self.chat, self.user), 0)

    def test_snapshot_and_filters_keep_new_messages_and_unmatched_records(self):
        fresh = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'delete fresh')
        result = manage_history(self.chat, self.user, dict(self.filters, types='0', keyword='delete'), 'delete', [])
        self.assertEqual(result['changed_ids'], [self.two.id])
        self.assertFalse(MessageUserState.objects.filter(message=fresh).exists())
        self.assertEqual(read_history(self.chat, self.user, dict(types='3'))['items'][0]['message_id'], self.system.id)

    def test_protection_is_scoped_to_account_and_can_be_released(self):
        manage_history(self.chat, self.user, self.filters, 'retain', [self.one.id])
        self.assertFalse(read_history(self.chat, self.peer, self.filters)['items'][-1]['retained'])
        manage_history(self.chat, self.user, self.filters, 'release', [self.one.id])
        self.assertFalse(MessageRetention.objects.exists())

    def test_foreign_ids_or_non_member_fail_without_partial_changes(self):
        stranger = User.create(self.space, 'Stranger', email='stranger@example.com', verified=True)
        with self.assertRaises(Exception):
            manage_history(self.chat, stranger, self.filters, 'delete', [])
        with self.assertRaises(Exception):
            manage_history(self.chat, self.user, self.filters, 'delete', [self.one.id, self.system.id + 10000])
        self.assertFalse(MessageUserState.objects.exists())

    def test_routes_and_filter_validation(self):
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(self.user)['auth']}
        response = self.client.get('/messages/history', {'chat_id': self.chat.id, 'types': '3'}, **headers)
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.post('/messages/history', data=json.dumps({'chat_id': self.chat.id, 'action': 'retain', 'message_ids': [self.one.id]}), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(MessageRetention.objects.filter(message=self.one).exists())
        with self.assertRaises(Exception):
            read_history(self.chat, self.user, dict(types='999'))

    def test_large_cleanup_is_bounded_and_keeps_cursor_and_protection(self):
        Message.objects.bulk_create([Message(chat=self.chat, user=self.peer, type=0, content='bulk fixture') for _ in range(505)])
        page = read_history(self.chat, self.user, dict(keyword='bulk fixture'))
        self.assertEqual(len(page['items']), 100)
        self.assertTrue(page['has_more'])
        older = read_history(self.chat, self.user, dict(keyword='bulk fixture', snapshot=page['snapshot_id']), page['next_before'])
        self.assertFalse(set(row['message_id'] for row in page['items']) & set(row['message_id'] for row in older['items']))
        filters = dict(keyword='bulk fixture', snapshot=page['snapshot_id'])
        protected_id = page['items'][0]['message_id']
        manage_history(self.chat, self.user, filters, 'retain', [protected_id])
        first = manage_history(self.chat, self.user, filters, 'delete', [])
        self.assertEqual(len(first['changed_ids']), 500)
        self.assertEqual(first['remaining'], 4)
        second = manage_history(self.chat, self.user, filters, 'delete', [])
        self.assertEqual(len(second['changed_ids']), 4)
        self.assertEqual(second['remaining'], 0)
        self.assertFalse(MessageUserState.objects.filter(message_id=protected_id).exists())

    def test_date_bounds_and_existing_batch_deletion_honor_retention(self):
        start = self.one.created_at.timestamp()
        page = read_history(self.chat, self.user, dict(start=start, end=start + 1, types='0'))
        self.assertIn(self.one.id, [row['message_id'] for row in page['items']])
        self.assertNotIn(self.one.id, [row['message_id'] for row in read_history(self.chat, self.user, dict(end=start))['items']])
        manage_history(self.chat, self.user, self.filters, 'retain', [self.one.id])
        response = self.client.delete('/messages/batch', data=json.dumps({'message_ids': [self.one.id, self.two.id]}), content_type='application/json', HTTP_AUTHORIZATION='Bearer ' + auth.get_login_token(self.user)['auth'])
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(MessageUserState.objects.filter(user=self.user, message=self.one).exists())
        self.assertTrue(MessageUserState.objects.filter(user=self.user, message=self.two).exists())
