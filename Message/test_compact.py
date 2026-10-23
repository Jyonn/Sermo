import gzip
import json
from copy import deepcopy

from django.test import TestCase, SimpleTestCase, override_settings
from Chat.models import Chat, ChatMember
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth
from utils.compact import compact_payload


def decode(payload):
    if not isinstance(payload, dict) or payload.get('format') != 'compact-v1':
        return payload

    def visit(node):
        if not isinstance(node, list):
            return node
        if node[0] == -1:
            return [visit(item) for item in node[1:]]
        row = payload['records'][node[1]]
        return dict(zip(payload['schemas'][row[0]], [visit(item) for item in row[1:]]))
    return visit(payload['root'])


class CompactPayloadTests(SimpleTestCase):
    def test_lossless_nested_data_nulls_empty_fields_and_viewer_specific_users(self):
        user = dict(user_id=1, name='User', avatar_uri='https://example.test/avatar.png', theme='default', optional=None)
        rows = [dict(message_id=i, user=user, mentions=[user], reply_to={'user': {**user, 'name': 'Other view'}, 'content': 'hello'}, payload={'items': [], 'flags': [False, 0, '', None]}) for i in range(31)]
        original = deepcopy(rows)
        compact = compact_payload(rows, 'messages')
        self.assertEqual(compact['format'], 'compact-v1')
        self.assertEqual(decode(compact), original)
        self.assertEqual(rows, original)
        for zipped in (False, True):
            chosen = compact_payload(rows, 'messages', accept_gzip=zipped)
            size = lambda value: len(gzip.compress(json.dumps(value).encode())) if zipped else len(json.dumps(value).encode())
            self.assertLessEqual(size(chosen), size(rows))
        self.assertEqual(compact_payload([], 'messages'), [])


@override_settings(MIDDLEWARE=['utils.middleware.ChatListCompression', 'utils.middleware.APIPacker'])
class CompactReadAPITests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Compact read', slug='compact-read', email='compact@example.test')
        self.user = User.create(self.space, 'Me', verified=True)
        self.peer = User.create(self.space, 'Peer', verified=True)
        self.chat = Chat.objects.create(space=self.space, chat_type=1, created_by=self.user, title='Read')
        ChatMember.objects.create(chat=self.chat, user=self.user, status=1)
        ChatMember.objects.create(chat=self.chat, user=self.peer, status=1)
        self.headers = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(self.user)['auth']}"}
        self.ids = [Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, f'Message {i}').id for i in range(35)]

    def response(self, url, zipped=False):
        response = self.client.get(url, **self.headers, **({'HTTP_ACCEPT_ENCODING': 'gzip'} if zipped else {}))
        body = gzip.decompress(response.content) if response.headers.get('Content-Encoding') == 'gzip' else response.content
        return response, json.loads(body)

    def test_messages_latest_before_after_and_gzip_are_lossless(self):
        for page in ('', f'&before={self.ids[20]}', f'&after={self.ids[10]}'):
            url = f'/messages/?chat_id={self.chat.id}&limit=31{page}'
            normal, legacy = self.response(url)
            self.assertIsInstance(legacy['body'], list)
            raw_compact_size = None
            for zipped in (False, True):
                response, compact = self.response(url + '&format=compact-v1', zipped)
                self.assertEqual(decode(compact['body']), legacy['body'])
                self.assertLess(len(response.content), len(normal.content))
                if not zipped:
                    raw_compact_size = len(response.content)
                if zipped:
                    self.assertIn('Accept-Encoding', response.headers['Vary'])
            gzip_normal, gzip_legacy = self.response(url, True)
            self.assertEqual(gzip_legacy['body'], legacy['body'])
            print(f'Messages {page or "latest"}: {len(normal.content)} -> {raw_compact_size} compact -> {len(response.content)} compact+gzip bytes; legacy+gzip {len(gzip_normal.content)}')

    def test_growth_preserves_all_rewards_and_capabilities(self):
        normal, legacy = self.response('/users/me/growth')
        raw_compact_size = None
        for zipped in (False, True):
            response, compact = self.response('/users/me/growth?format=compact-v1', zipped)
            self.assertEqual(decode(compact['body']), legacy['body'])
            self.assertLess(len(response.content), len(normal.content))
            if not zipped:
                raw_compact_size = len(response.content)
        print(f'Growth: {len(normal.content)} -> {raw_compact_size} compact -> {len(response.content)} compact+gzip bytes')

    def test_nonmember_and_disabled_chat_stay_forbidden(self):
        self.space.chat_enabled = False
        self.space.save(update_fields=['chat_enabled'])
        _, result = self.response(f'/messages/?chat_id={self.chat.id}&limit=31&format=compact-v1')
        self.assertIsNone(result['body'])
        self.space.chat_enabled = True
        self.space.save(update_fields=['chat_enabled'])
        ChatMember.objects.filter(chat=self.chat, user=self.user).delete()
        _, result = self.response(f'/messages/?chat_id={self.chat.id}&limit=31&format=compact-v1')
        self.assertIsNone(result['body'])

    def test_unauthenticated_growth_does_not_leak_data(self):
        result = self.client.get('/users/me/growth?format=compact-v1').json()
        self.assertIsNone(result['body'])

    def test_growth_acknowledgement_opt_in_preserves_contract(self):
        results = []
        for query in ('', '?format=compact-v1'):
            User.objects.filter(pk=self.user.pk).update(growth_acknowledged_level=0)
            response = self.client.post('/users/me/growth' + query, data=json.dumps({'level': 1}), content_type='application/json', **self.headers).json()
            self.assertIsNotNone(response['body'])
            results.append(decode(response['body']))
        self.assertEqual(results[0], results[1])
