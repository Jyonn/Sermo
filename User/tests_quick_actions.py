import json
from django.test import TestCase
from Space.models import Space
from User.models import User, UserStateEvent
from utils import auth


class ChatShortcutTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Shortcuts', slug='shortcut-test', email='shortcuts@example.invalid')
        self.user = User.create(space, 'First', verified=True)
        self.other = User.create(space, 'Second', verified=True)
        self.headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(self.user)['auth']}
        self.url = '/users/me/chat-quick-actions'

    def save(self, body):
        return self.client.post(self.url, json.dumps(body), content_type='application/json', **self.headers)

    def test_authentication_and_account_isolation(self):
        self.assertNotEqual(self.client.get(self.url).json().get('code'), 0)
        result = self.save({'kind': 'direct', 'actions': ['call', 'search']})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['body']['preferences']['direct'], ['call', 'search'])
        self.other.refresh_from_db()
        self.assertEqual(self.other.chat_quick_actions, {})
        self.assertIn('no-store', result['Cache-Control'])

    def test_two_devices_edit_independent_kinds(self):
        self.save({'kind': 'direct', 'actions': ['call']})
        result = self.save({'kind': 'group', 'actions': ['status', 'search']}).json()['body']
        self.assertEqual(result['preferences'], {'direct': ['call'], 'group': ['status', 'search']})
        self.assertEqual(result['revision'], 2)
        self.assertTrue(result['call_hint_seen'])
        self.assertEqual(UserStateEvent.objects.filter(user=self.user, kind='chat_shortcuts.changed').count(), 2)

    def test_adding_or_reordering_call_acknowledges_guide_but_migration_does_not(self):
        migrated = self.save({'migrate': {'direct': ['locked', 'call'], 'group': ['copy']}}).json()['body']
        self.assertFalse(migrated['call_hint_seen'])
        reordered = self.save({'kind': 'direct', 'actions': ['call', 'locked']}).json()['body']
        self.assertTrue(reordered['call_hint_seen'])
        self.assertEqual(reordered['preferences']['direct'], ['call', 'locked'])
        self.assertEqual(reordered['preferences']['group'], ['copy'])

    def test_local_migration_never_overwrites_another_device(self):
        self.save({'acknowledge_call_hint': True})
        migrated = self.save({'migrate': {'direct': ['search'], 'group': ['copy']}}).json()['body']
        self.assertTrue(migrated['call_hint_seen'])
        self.assertEqual(migrated['preferences']['direct'], ['search'])
        ignored = self.save({'migrate': {'direct': ['call'], 'group': ['status']}}).json()['body']
        self.assertEqual(ignored, migrated)
        again = self.save({'acknowledge_call_hint': True}).json()['body']
        self.assertEqual(again, migrated)

    def test_invalid_actions_and_unknown_fields_are_rejected(self):
        for body in [{'kind': 'group', 'actions': ['call']}, {'kind': 'direct', 'actions': []},
                     {'kind': 'direct', 'actions': ['call', 'call']}, {'kind': 'direct', 'actions': ['unknown']},
                     {'kind': [], 'actions': ['call']}, {'kind': 'direct', 'actions': ['call'], 'user_id': self.other.id},
                     {'migrate': {'direct': ['call']}}, {'acknowledge_call_hint': False}]:
            with self.subTest(body=body):
                self.assertEqual(self.save(body).status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.chat_quick_actions, {})

    def test_defaults_are_private_and_reads_do_not_write(self):
        before = UserStateEvent.objects.count()
        result = self.client.get(self.url, **self.headers).json()['body']
        self.assertEqual(result['preferences'], {'direct': ['locked', 'calendar'], 'group': ['locked', 'copy', 'status']})
        self.assertFalse(result['configured'])
        self.assertFalse(result['call_hint_seen'])
        self.assertEqual(UserStateEvent.objects.count(), before)
