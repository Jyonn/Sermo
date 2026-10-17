import base64
import hashlib
import json
from urllib.parse import parse_qs, urlsplit

from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from Space.models import Space
from User.models import OAuthApplication, OAuthApplicationAudit, OAuthGrant, User
from User.management.commands.seed_oauth_applications import seed
from User.oauth import OAuthError, authorize, exchange, userinfo, validate_authorization
from User.oauth_registry import RegistryError, approved, configuration, mutate
from utils.auth import get_login_token, get_platform_admin_token

CALLBACK = 'https://stillfolio.jyonn.space/auth/sermo/callback'


class RegistryTests(TestCase):
    def setUp(self):
        seed(CALLBACK)
        self.space = Space.objects.create(name='Registry', slug='registry', email='test@example.com')
        self.owner = User.create(self.space, 'Developer', verified=True)
        User.objects.filter(pk=self.owner.pk).update(email='verified@example.test', email_verified_at=timezone.now(), phone='+8613800138000', phone_verified_at=timezone.now())
        self.owner.refresh_from_db()
        self.other = User.create(self.space, 'Other', verified=True)
        self.config = dict(name='Example app', description='Example', redirect_uris=['https://example.com/callback'], scopes=['profile'], loopback_uris=[])
        self.params = dict(client_id='supervision-local', redirect_uri='http://127.0.0.1:8001/api/auth/friendom/callback/', scope='profile', state='s' * 32,
                           code_challenge=base64.urlsafe_b64encode(hashlib.sha256(('v' * 43).encode()).digest()).rstrip(b'=').decode(), code_challenge_method='S256', response_type='code')

    def create(self, admin=False):
        return mutate(actor='admin@example.com' if admin else f'user:{self.owner.id}', owner=None if admin else self.owner,
                      admin=admin, data=dict(action='create', client_id='example', **self.config))

    def change(self, app, action, admin=False, **data):
        return mutate(actor='admin@example.com' if admin else f'user:{self.owner.id}', owner=None if admin else self.owner,
                      admin=admin, app_id=app['id'], data=dict(action=action, version=app['version'], **data))

    def grant(self, **overrides):
        params = {**self.params, **overrides}
        code = parse_qs(urlsplit(authorize(self.owner, **params)).query)['code'][0]
        return exchange(client_id=params['client_id'], redirect_uri=params['redirect_uri'], code=code, code_verifier='v' * 43)

    def test_developer_ownership_review_and_version(self):
        app = self.create()
        self.assertIsNone(approved('example'))
        rev = app['revisions'][0]['id']
        with self.assertRaises(RegistryError):
            mutate(actor='other', owner=self.other, app_id=app['id'], data=dict(action='submit', version=app['version'], revision_id=rev))
        with self.assertRaises(RegistryError):
            self.change(app, 'approve', revision_id=rev)
        pending = self.change(app, 'submit', revision_id=rev)
        with self.assertRaises(RegistryError) as stale:
            self.change(app, 'submit', revision_id=rev)
        self.assertEqual(stale.exception.status, 409)
        with self.assertRaises(RegistryError):
            self.change(pending, 'approve', revision_id=rev)
        result = self.change(pending, 'approve', admin=True, revision_id=rev)
        self.assertEqual(approved('example').id, rev)
        self.assertEqual(result['version'], 3)
        self.assertEqual(OAuthApplicationAudit.objects.filter(application_id=app['id']).count(), 3)

    def test_pending_and_rejected_revision_do_not_change_approved(self):
        app = self.create(admin=True)
        original = approved('example').id
        app = self.change(app, 'revise', **{**self.config, 'scopes': ['profile', 'email']}, admin=True)
        rev = app['revisions'][0]['id']
        app = self.change(app, 'submit', admin=True, revision_id=rev)
        self.assertEqual(approved('example').id, original)
        with self.assertRaises(RegistryError):
            self.change(app, 'reject', admin=True, revision_id=rev, review_note='')
        app = self.change(app, 'reject', admin=True, revision_id=rev, review_note='Explain use of email')
        self.assertEqual(approved('example').id, original)
        with self.assertRaises(RegistryError):
            self.change(app, 'edit', admin=True, revision_id=rev, **self.config)

    def test_exact_redirects_and_explicit_loopback_review(self):
        for uri in ['https://*.example.com/cb', 'https://example.com/cb#', 'http://example.com/cb', 'https://user@example.com/cb', 'https://example.com/\\cb', 'http://127.0.0.1/cb']:
            with self.subTest(uri=uri), self.assertRaises(RegistryError):
                configuration({**self.config, 'redirect_uris': [uri]})
        uri = self.params['redirect_uri']
        with self.assertRaises(RegistryError):
            configuration({**self.config, 'redirect_uris': [uri]})
        self.config.update(redirect_uris=[uri], loopback_uris=[uri])
        app = self.create()
        rev = app['revisions'][0]['id']
        app = self.change(app, 'submit', revision_id=rev)
        with self.assertRaises(RegistryError):
            self.change(app, 'approve', admin=True, revision_id=rev)
        self.change(app, 'approve', admin=True, revision_id=rev, approved_loopback_uris=[uri])
        for changes in [dict(scope='email'), dict(scope='profile phone'), dict(code_challenge_method='plain'), dict(redirect_uri=uri.replace('127.0.0.1', 'localhost')), dict(redirect_uri=uri+'?x=1'), dict(redirect_uri=uri.rstrip('/')), dict(redirect_uri=uri.replace('8001', '8002'))]:
            with self.subTest(changes=changes), self.assertRaises(OAuthError):
                validate_authorization(**{**self.params, **changes})

    def test_disable_restore_preserves_grants(self):
        token = self.grant()['access_token']
        app = OAuthApplication.objects.get(client_id='supervision-local')
        data = dict(id=app.id, version=app.version)
        disabled = self.change(data, 'disable', admin=True)
        with self.assertRaises(OAuthError):
            userinfo(token)
        with self.assertRaises(OAuthError):
            self.grant()
        self.change(disabled, 'restore', admin=True)
        self.assertEqual(userinfo(token)['nickname'], self.owner.name)
        self.assertEqual(OAuthGrant.objects.count(), 1)

    def test_email_is_independent_incremental_and_verified_truthfully(self):
        old_token = self.grant()['access_token']
        app = OAuthApplication.objects.get(client_id='supervision-local')
        current = app.approved_revision
        cfg = dict(name=current.name, description=current.description, redirect_uris=current.redirect_uris, loopback_uris=current.loopback_uris, scopes=['profile', 'email'])
        app = self.change(dict(id=app.id, version=app.version), 'revise', admin=True, **cfg)
        rev = app['revisions'][0]['id']
        app = self.change(app, 'submit', admin=True, revision_id=rev)
        with self.assertRaises(OAuthError):
            self.grant(scope='profile email')
        self.change(app, 'approve', admin=True, revision_id=rev, approved_loopback_uris=current.loopback_uris)
        token = self.grant(scope='profile email')['access_token']
        self.assertNotIn('email', userinfo(old_token))
        self.assertIs(userinfo(token)['email_verified'], True)
        self.assertEqual(userinfo(token)['email'], 'verified@example.test')
        self.owner.email = 'dev@example.com'
        self.owner.email_verified_at = None
        self.owner.save(update_fields=['email', 'email_verified_at'])
        for access_token in (token, old_token):
            with self.assertRaises(OAuthError) as error:
                userinfo(access_token)
            self.assertEqual(error.exception.code, 'verification_required')
        self.owner.email_verified_at = timezone.now()
        self.owner.save(update_fields=['email_verified_at'])
        self.assertIs(userinfo(token)['email_verified'], True)
        self.assertEqual(userinfo(token)['email'], 'dev@example.com')
        self.assertEqual(userinfo(token)['sub'], userinfo(old_token)['sub'])
        self.assertNotIn('email', userinfo(old_token))
        self.assertEqual(OAuthApplication.objects.get(client_id='stillfolio').approved_revision.scopes, ['phone', 'profile', 'vip'])
        self.assertEqual(OAuthApplication.objects.get(client_id='chronelle').approved_revision.scopes, ['profile'])

    def test_seed_idempotence_conflict_atomic_and_grants_unchanged(self):
        self.grant()
        grants = list(OAuthGrant.objects.values())
        audits = OAuthApplicationAudit.objects.count()
        seed(CALLBACK)
        self.assertEqual(OAuthApplicationAudit.objects.count(), audits)
        self.assertEqual(list(OAuthGrant.objects.values()), grants)
        with self.assertRaises(CommandError):
            seed('https://different.example.com/cb')
        self.assertEqual(OAuthApplication.objects.count(), 3)
        self.assertEqual(list(OAuthGrant.objects.values()), grants)

    def test_metadata_and_separate_admin_auth(self):
        response = self.client.get('/users/oauth/metadata', self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Supervision')
        self.assertEqual(response.json()['scopes'], ['profile'])
        self.assertEqual(self.client.get('/users/oauth/metadata', {**self.params, 'scope': 'email'}).status_code, 400)
        user_token = get_login_token(self.owner)['auth']
        admin_token = get_platform_admin_token('admin@example.com')['auth']
        denied = self.client.get('/platform-admin/oauth/applications', HTTP_AUTHORIZATION=f'Bearer {user_token}')
        self.assertNotEqual(denied.status_code, 200)
        allowed = self.client.get('/platform-admin/oauth/applications', HTTP_AUTHORIZATION=f'Bearer {admin_token}')
        self.assertEqual(len(allowed.json()['applications']), 3)
        response = self.client.post('/users/oauth/applications', json.dumps(dict(client_id='example', **self.config)), content_type='application/json', HTTP_AUTHORIZATION=f'Bearer {user_token}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['revisions'][0]['status'], 'draft')
        other_token = get_login_token(self.other)['auth']
        own = self.client.get('/users/oauth/applications', HTTP_AUTHORIZATION=f'Bearer {other_token}')
        self.assertEqual(own.json()['applications'], [])

    def test_existing_code_and_token_survive_approved_callback_revision(self):
        app = self.create(admin=True)
        params = {**self.params, 'client_id': 'example', 'redirect_uri': self.config['redirect_uris'][0]}
        code = parse_qs(urlsplit(authorize(self.owner, **params)).query)['code'][0]
        app = self.change(app, 'revise', admin=True, **{**self.config, 'redirect_uris': ['https://example.com/new']})
        rev = app['revisions'][0]['id']
        app = self.change(app, 'submit', admin=True, revision_id=rev)
        self.change(app, 'approve', admin=True, revision_id=rev)
        with self.assertRaises(OAuthError):
            authorize(self.owner, **params)
        result = exchange(client_id='example', redirect_uri=params['redirect_uri'], code=code, code_verifier='v' * 43)
        self.assertEqual(userinfo(result['access_token'])['nickname'], self.owner.name)

    def test_seed_rolls_back_earlier_additions_on_late_conflict(self):
        # Remove seeded records only inside this disposable test database.
        OAuthApplication.objects.update(approved_revision=None)
        OAuthApplicationAudit.objects.all().delete()
        OAuthApplication.objects.all().delete()
        self.config.update(redirect_uris=['https://other.example/cb'])
        mutate(actor='admin@example.com', admin=True, data=dict(action='create', client_id='supervision-local', **self.config))
        with self.assertRaises(CommandError):
            seed(CALLBACK)
        self.assertEqual(list(OAuthApplication.objects.values_list('client_id', flat=True)), ['supervision-local'])

    def test_invalid_configuration_and_revision_ids_fail_closed(self):
        for changes in [dict(scopes=['email', 'inbox']), dict(scopes='profile'), dict(redirect_uris=['https://example.com/cb?code=old']), dict(redirect_uris=['https://example.com/cb?state=old']), dict(redirect_uris=['https://example.com:99999/cb']), dict(loopback_uris=['https://example.com/callback'])]:
            with self.subTest(changes=changes), self.assertRaises(RegistryError):
                configuration({**self.config, **changes})
        app = self.create()
        for revision_id in [{}, [], 'bad', True]:
            with self.subTest(revision_id=revision_id), self.assertRaises(RegistryError):
                self.change(app, 'submit', revision_id=revision_id)

    def test_disabled_application_cannot_exchange_existing_code(self):
        code = parse_qs(urlsplit(authorize(self.owner, **self.params)).query)['code'][0]
        app = OAuthApplication.objects.get(client_id=self.params['client_id'])
        self.change(dict(id=app.id, version=app.version), 'disable', admin=True)
        with self.assertRaises(OAuthError):
            exchange(client_id=app.client_id, redirect_uri=self.params['redirect_uri'], code=code, code_verifier='v' * 43)
