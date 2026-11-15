import json
from django.test import TestCase

from User.models import NativePushBinding as Binding
from User.tests_native_push_bindings import NativePushBindingTests
from utils import auth


class NativePushAPITests(TestCase):
    setUp = NativePushBindingTests.setUp

    def call(self, method='get', user=None, values=None, token_route=False, raw=None, authenticated=True):
        path = '/users/me/native-push' + ('/token' if token_route else '')
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user or self.a)['auth']} if authenticated else {}
        if method == 'get':
            return self.client.get(path, values or self.identity(), **headers)
        return getattr(self.client, method)(path, raw if raw is not None else json.dumps(values or {}), content_type='application/json', **headers)

    def identity(self):
        return dict(installation_id=str(self.installation), environment='sandbox')

    def preference(self, **changes):
        return dict(self.identity(), expected_revision=0, opted_in=True, token=self.token, **changes)

    def body(self, response):
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        return response.json()['body']

    def test_status_is_private_and_never_creates_a_binding(self):
        value = self.body(self.call())
        self.assertEqual(value, dict(self.identity(), opted_in=False, revision=0, registered=False, delivery_available=False))
        self.assertFalse(Binding.objects.exists())

    def test_all_routes_require_member_authentication(self):
        for method, token_route in [('get', False), ('put', False), ('delete', False), ('post', True)]:
            response = self.call(method, values=self.preference(), token_route=token_route, authenticated=False)
            self.assertNotEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertFalse(Binding.objects.exists())

    def test_explicit_enable_has_revision_but_does_not_claim_delivery(self):
        value = self.body(self.call('put', values=self.preference()))
        self.assertTrue(value['opted_in']); self.assertTrue(value['registered'])
        self.assertEqual(value['revision'], 1); self.assertFalse(value['delivery_available'])
        self.assertEqual(value, self.body(self.call()))
        self.assertNotIn(self.token, str(value))
        self.assertNotIn('token_digest', value)

    def test_disable_creates_a_tombstone_and_stale_registration_conflicts(self):
        value = self.body(self.call('delete', values=dict(self.identity(), expected_revision=0)))
        self.assertFalse(value['opted_in']); self.assertEqual(value['revision'], 1)
        stale = self.call('post', token_route=True, values=dict(self.identity(), expected_revision=0, token=self.token))
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()['identifier'], 'NATIVE_PUSH@CONFLICT')
        fresh = self.body(self.call('post', token_route=True, values=dict(self.identity(), expected_revision=1, token=self.token)))
        self.assertFalse(fresh['opted_in']); self.assertEqual(fresh['revision'], 2)

    def test_background_registration_without_existing_choice_stays_absent(self):
        value = self.body(self.call('post', token_route=True, values=dict(self.identity(), expected_revision=0, token=self.token)))
        self.assertFalse(value['opted_in']); self.assertEqual(value['revision'], 0)
        self.assertFalse(Binding.objects.exists())

    def test_same_installation_accounts_stay_independent(self):
        self.body(self.call('put', values=self.preference()))
        self.body(self.call('delete', user=self.b, values=dict(self.identity(), expected_revision=0)))
        a = self.body(self.call()); b = self.body(self.call(user=self.b))
        self.assertTrue(a['opted_in']); self.assertFalse(b['opted_in'])
        self.assertEqual(Binding.objects.get(user=self.a).space_id, self.space.pk)
        self.assertEqual(Binding.objects.get(user=self.b).space_id, self.other_space.pk)

    def test_stale_enable_cannot_undo_a_newer_disable(self):
        self.body(self.call('put', values=self.preference()))
        self.body(self.call('delete', values=dict(self.identity(), expected_revision=1)))
        value = self.preference(); value['expected_revision'] = 1
        response = self.call('put', values=value)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.body(self.call())['opted_in'])

    def test_token_rotation_preserves_opt_out_and_never_echoes_token(self):
        self.body(self.call('put', values=self.preference()))
        self.body(self.call('delete', values=dict(self.identity(), expected_revision=1)))
        response = self.call('post', token_route=True, values=dict(self.identity(), expected_revision=2, token='cd' * 32))
        value = self.body(response)
        self.assertFalse(value['opted_in']); self.assertTrue(value['registered'])
        self.assertNotIn('cd' * 32, response.content.decode())
        self.assertEqual(Binding.objects.get(user=self.a).token, 'cd' * 32)

    def test_environment_selection_never_leaks_another_registration(self):
        self.body(self.call('put', values=self.preference()))
        value = self.body(self.call(values=dict(self.identity(), environment='production')))
        self.assertEqual(value['revision'], 0)
        self.assertFalse(value['registered'])

    def test_unknown_fields_cannot_select_another_user_or_space(self):
        for field, item in [('user_id', self.b.pk), ('space_id', self.other_space.pk), ('unexpected', True)]:
            value = self.preference(); value[field] = item
            self.assertEqual(self.call('put', values=value).status_code, 400)
            self.assertEqual(self.call(values=dict(self.identity(), **{field: item})).status_code, 400)
        self.assertFalse(Binding.objects.exists())

    def test_invalid_and_duplicate_json_fail_without_mutations(self):
        for raw in ('{}', '[]', 'null', '{', '{"opted_in":false,"opted_in":true}', ' ' * 4097):
            with self.subTest(raw=raw[:40]):
                self.assertEqual(self.call('put', raw=raw).status_code, 400)
        for field, item in [('environment', []), ('environment', None), ('expected_revision', True),
                            ('expected_revision', -1), ('expected_revision', 2 ** 63), ('opted_in', 'true'),
                            ('token', 'secret-invalid-token')]:
            value = self.preference(); value[field] = item
            response = self.call('put', values=value)
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('secret-invalid-token', response.content.decode())
        self.assertFalse(Binding.objects.exists())

    def test_missing_or_repeated_query_fields_are_rejected(self):
        self.assertEqual(self.call(values={'environment': 'sandbox'}).status_code, 400)
        response = self.client.get('/users/me/native-push',
            {'installation_id': str(self.installation), 'environment': ['sandbox', 'production']},
            HTTP_AUTHORIZATION='Bearer ' + auth.get_login_token(self.a)['auth'])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Binding.objects.exists())

    def test_refresh_and_delete_reject_unknown_or_incomplete_bodies(self):
        for method, token_route in [('post', True), ('delete', False)]:
            for value in ({}, dict(self.identity(), expected_revision=0, user_id=self.b.pk)):
                self.assertEqual(self.call(method, values=value, token_route=token_route).status_code, 400)
        self.assertFalse(Binding.objects.exists())
