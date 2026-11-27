from django.test import TestCase

from Space.models import Space
from User.models import User
from utils.auth import get_login_token


class InterfacePreferencesTests(TestCase):
    url = '/users/me/interface-preferences'

    def setUp(self):
        space = Space.objects.create(name='Settings', slug='settings-test', email='settings@example.test')
        self.member = User.create(space, 'First', verified=True)
        self.other_member = User.create(space, 'Second', verified=True)
        self.headers = self.headers_for(self.member)

    def headers_for(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(user)['auth']}

    def save(self, changes, operation='patch'):
        return self.client.post(self.url, {operation: changes}, content_type='application/json', **self.headers)

    def test_unset_entry_stays_unset_until_selected(self):
        result = self.client.get(self.url, **self.headers)
        self.assertEqual(result.json()['body'], {'values': {}, 'revision': 0})
        self.assertIn('no-store', result['Cache-Control'])
        self.member.refresh_from_db()
        self.assertIsNone(self.member.interface_preferences)
        self.assertNotEqual(self.client.get(self.url).json().get('code'), 0)

    def test_account_settings_persist_and_are_private(self):
        values = {'appearance': 'dark', 'mobile_voice_input': 'tap', 'square_entry': 'explore',
                  'square_layout_mode': 'per-channel', 'square_layout_explore': 'masonry',
                  'square_layout_friends': 'list', 'square_layout_user': 'masonry',
                  'personal_theme': {'theme': 'default', 'period': 'default|christmas:2026'},
                  'christmas_background_override': False}
        saved = self.save(values).json()['body']
        self.assertEqual(saved, {'values': values, 'revision': 1})
        self.assertEqual(self.client.get(self.url, **self.headers_for(self.member)).json()['body'], saved)
        self.assertEqual(self.client.get(self.url, **self.headers_for(self.other_member)).json()['body']['values'], {})
        self.member.refresh_from_db()
        self.assertEqual(self.member.interface_preferences['appearance'], 'dark')

    def test_updates_merge_independent_fields_and_migration_is_idempotent(self):
        self.save({'square_entry': 'friends', 'square_layout_friends': 'masonry'})
        old_device = {'square_entry': 'explore', 'square_layout_mode': 'per-channel',
                      'square_layout_friends': 'list', 'appearance': 'dark'}
        saved = self.save(old_device, 'migrate').json()['body']
        self.assertEqual(saved, {'revision': 2, 'values': {'square_entry': 'friends', 'appearance': 'dark',
                         'square_layout_friends': 'masonry', 'square_layout_mode': 'per-channel'}})
        self.assertEqual(self.save(old_device, 'migrate').json()['body'], saved)
        patched = self.save({'mobile_voice_input': 'tap'}).json()['body']
        self.assertEqual(patched['values'], {**saved['values'], 'mobile_voice_input': 'tap'})

    def test_seen_themes_are_merged_and_bounded(self):
        self.save({'seen_theme_tokens': [f'christmas:{2000 + i}' for i in range(20)]})
        saved = self.save({'seen_theme_tokens': ['christmas:2019', 'birthday:123:2026']}).json()['body']['values']
        self.assertEqual(len(saved['seen_theme_tokens']), 20)
        self.assertNotIn('christmas:2000', saved['seen_theme_tokens'])
        self.assertEqual(saved['seen_theme_tokens'][-1], 'birthday:123:2026')

    def test_invalid_writes_cannot_change_existing_preferences(self):
        saved = self.save({'appearance': 'dark'}).json()['body']
        for invalid in ({'appearance': False}, {'square_layout_user': 'unknown'}, {'square_entry': ''},
                        {'user_id': self.other_member.id}, {'christmas_background_override': 1},
                        {'personal_theme': {'theme': 'christmas-2026', 'period': 'default||bad'}},
                        {'seen_theme_tokens': ['bad token']}, {'seen_theme_tokens': ['default'] * 21}):
            with self.subTest(invalid=invalid):
                self.assertEqual(self.save(invalid).status_code, 400)
        self.assertEqual(self.client.get(self.url, **self.headers).json()['body'], saved)
