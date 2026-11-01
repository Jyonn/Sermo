import json
import datetime
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

    def clock_fixture(self):
        rows = []
        for day, clock in [(1,'09:59:59'),(1,'10:00:00'),(1,'17:59:59'),(1,'18:00:00'),(2,'10:30:00'),(1,'23:00:00'),(2,'01:30:00'),(2,'02:00:00')]:
            message = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'clock fixture')
            moment = datetime.datetime.fromisoformat(f'2026-11-{day:02d}T{clock}+08:00')
            Message.objects.filter(id=message.id).update(created_at=moment)
            rows.append(message)
        return rows

    def test_daily_window_is_repeated_on_each_date_and_shared_by_cleanup(self):
        rows = self.clock_fixture()
        filters = dict(keyword='clock fixture', time_from=600, time_to=1080, snapshot=rows[-1].id)
        ids = {row['message_id'] for row in read_history(self.chat, self.user, filters)['items']}
        self.assertEqual(ids, {rows[1].id, rows[2].id, rows[4].id})
        manage_history(self.chat, self.user, filters, 'retain', [rows[1].id])
        self.assertEqual(manage_history(self.chat, self.user, filters, 'preview', [])['deletable_count'], 2)
        self.assertEqual(set(manage_history(self.chat, self.user, filters, 'delete', [])['changed_ids']), {rows[2].id, rows[4].id})
        self.assertFalse(MessageUserState.objects.filter(message=rows[3]).exists())

    def test_overnight_window_midnight_and_one_sided_bounds(self):
        rows = self.clock_fixture()
        filters = dict(keyword='clock fixture', time_from=1320, time_to=120)
        self.assertEqual({row['message_id'] for row in read_history(self.chat, self.user, filters)['items']}, {rows[5].id, rows[6].id})
        self.assertEqual({row['message_id'] for row in read_history(self.chat, self.user, dict(keyword='clock fixture', time_to=0))['items']}, set())
        self.assertEqual(read_history(self.chat, self.user, dict(keyword='clock fixture', time_from=0))['total_count'], 8)
        day_start = datetime.datetime.fromisoformat('2026-11-02T00:00:00+08:00').timestamp()
        page = read_history(self.chat, self.user, dict(filters, start=day_start, end=day_start+86400))
        self.assertEqual([row['message_id'] for row in page['items']], [rows[6].id])

    def test_daily_window_endpoint_and_invalid_bounds(self):
        self.clock_fixture()
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(self.user)['auth']}
        response = self.client.get('/messages/history', dict(chat_id=self.chat.id, keyword='clock fixture', time_from=600, time_to=1080), **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['total_count'], 3)
        response = self.client.post('/messages/history', data=json.dumps(dict(chat_id=self.chat.id, keyword='clock fixture', time_from=600, time_to=1080, snapshot=Message.objects.filter(chat=self.chat).latest('id').id, action='preview')), content_type='application/json', **headers)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['deletable_count'], 3)
        for values in [dict(time_from=-1),dict(time_to=1440),dict(time_from=600,time_to=600)]:
            with self.assertRaises(Exception): read_history(self.chat, self.user, values)
