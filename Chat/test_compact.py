import json
import gzip
from copy import deepcopy
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from Chat.compact import compact_chat_list
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth


def unpack(table):
    return [dict(zip(table['schemas'][schema], values)) for schema, values in table['rows']]


def decode(value):
    if isinstance(value, list):
        return value
    users = unpack(value['users'])
    def user(index):
        return deepcopy(users[index]) if index is not None else None
    def message(row):
        if row is None:
            return None
        row = deepcopy(row)
        if 'user' in row:
            row['user'] = user(row['user'])
        if 'mentions' in row:
            row['mentions'] = [user(index) for index in row['mentions']]
        if isinstance(row.get('reply_to'), dict):
            row['reply_to'] = message(row['reply_to'])
        return row
    rows = unpack(value['chats'])
    for row in rows:
        row['owner'] = user(row['owner'])
        row['members'] = [{**user(index), **context} for index, context in row['members']]
        row['last_message'] = message(row['last_message'])
    return rows


class CompactTransportTests(SimpleTestCase):
    def test_lossless_repeated_members_and_per_chat_context(self):
        users = [{'user_id': n, 'name': f'User {n}', 'avatar_uri': f'https://images.example.test/avatars/{n}.png', 'verified': True, 'official': False, 'profile_card_theme': 'default', 'welcome_message': 'Hello'} for n in range(12)]
        chats = [{'chat_id': n, 'owner': users[0], 'members': [{**user, 'joined_at': n, 'submission_role': None, 'group_chat_mute': {'active': n == 1}} for user in users], 'last_message': {'user': users[1], 'mentions': [users[2]], 'reply_to': {'user': users[3], 'content': 'reply'}, 'payload': {'text': 'unchanged'}}} for n in range(8)]
        original = deepcopy(chats)
        compact = compact_chat_list(chats)
        self.assertEqual(compact['format'], 'compact-v1')
        self.assertEqual(decode(compact), original)
        self.assertEqual(chats, original)
        size = lambda value: len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode())
        ratio = size(compact) / size(chats)
        print(f'\nChat transport fixture: {size(chats)} -> {size(compact)} bytes ({ratio:.1%})')
        zipped = lambda value: len(gzip.compress(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
        print(f'Chat transport fixture gzip: {zipped(chats)} -> {zipped(compact)} bytes')
        self.assertLess(ratio, .55)

    def test_small_and_empty_lists_keep_legacy_shape(self):
        self.assertEqual(compact_chat_list([]), [])
        row = [{'chat_id': 1, 'owner': None, 'members': [], 'last_message': None}]
        self.assertEqual(compact_chat_list(row), row)

    def test_selects_smaller_gzip_representation(self):
        user = {'user_id': 1, 'name': 'Repeated user', 'official': False}
        rows = [{'chat_id': index, 'owner': user, 'members': [user], 'last_message': None} for index in range(20)]
        selected = compact_chat_list(rows, accept_gzip=True)
        zipped = lambda value: len(gzip.compress(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
        self.assertEqual(decode(selected), rows)
        self.assertLessEqual(zipped(selected), zipped(rows))


@override_settings(MIDDLEWARE=['utils.middleware.ChatListCompression', 'utils.middleware.APIPacker'])
class CompactChatListTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Compact', slug='compact', email='compact@example.com')
        self.me = User.create(self.space, 'Me', verified=True)
        self.peer = User.create(self.space, 'Peer', verified=True)
        self.headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.me)['auth']}"}
        for index in range(3):
            chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, title=f'Group {index}', created_by=self.me)
            for user in (self.me, self.peer):
                ChatMember.objects.create(chat=chat, user=user, status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now())
            Message.create(chat, self.peer, MessageTypeChoice.TEXT, 'Unread preview')

    def test_opt_in_matches_legacy_read_state_order_and_member_details(self):
        legacy = self.client.get('/chats/', **self.headers).json()['body']
        compact = self.client.get('/chats/?format=compact-v1', **self.headers).json()['body']
        self.assertIsInstance(legacy, list)
        self.assertEqual(decode(compact), legacy)
        self.assertEqual(compact['format'], 'compact-v1')

    def test_non_member_chats_are_not_included(self):
        outsider = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, title='Not mine', created_by=self.peer)
        rows = decode(self.client.get('/chats/?format=compact-v1', **self.headers).json()['body'])
        self.assertNotIn(outsider.id, [row['chat_id'] for row in rows])

    def test_list_does_not_serialize_last_message_twice(self):
        with patch.object(Chat, '_dictify_last_message', side_effect=AssertionError('duplicate preview serialization')):
            response = self.client.get('/chats/?format=compact-v1', **self.headers).json()
        self.assertIsNotNone(response['body'])

    def test_disabled_chat_access_still_enforced(self):
        self.space.chat_enabled = False
        self.space.save(update_fields=['chat_enabled'])
        response = self.client.get('/chats/?format=compact-v1', **self.headers).json()
        self.assertIsNone(response['body'])

    def test_http_gzip_preserves_both_contracts_and_varies_by_encoding(self):
        for suffix in ('', '?format=compact-v1'):
            normal = self.client.get(f'/chats/{suffix}', **self.headers)
            compressed = self.client.get(f'/chats/{suffix}', HTTP_ACCEPT_ENCODING='gzip', **self.headers)
            self.assertEqual(compressed.headers['Content-Encoding'], 'gzip')
            self.assertIn('Accept-Encoding', compressed.headers['Vary'])
            self.assertEqual(decode(json.loads(gzip.decompress(compressed.content))['body']), decode(normal.json()['body']))
            self.assertLess(len(compressed.content), len(normal.content))
            print(f'Chat endpoint {suffix or "legacy"}: {len(normal.content)} -> {len(compressed.content)} bytes over gzip')
        other = self.client.get('/users/me', HTTP_ACCEPT_ENCODING='gzip', **self.headers)
        self.assertNotIn('Content-Encoding', other.headers)
