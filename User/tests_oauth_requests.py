from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from django.test import override_settings
from django.utils import timezone

from Space.models import Space
from User.models import OAuthAuthorizationRequest, OAuthGrant, User
from User.oauth import OAuthError, authorize, exchange, userinfo
from User.oauth_requests import create, resolve
from User.tests_oauth import OAuthTests


class SpaceRequestTests(OAuthTests):
    def request(self, **overrides):
        data = dict(client_id='stillfolio', redirect_uri=self.redirect, scope='profile', state='s'*32,
            code_challenge=self.challenge, code_challenge_method='S256', response_type='code', space_id=self.space.id)
        data.update(overrides)
        return create(data)['request_id']

    def setUp(self):
        super().setUp()
        from User.oauth import registered_redirect_uri
        self.redirect = registered_redirect_uri()

    def issue(self, token):
        flow = resolve(token)
        return parse_qs(urlparse(authorize(self.user, **flow.parameters, flow=flow)).query)['code'][0]

    def test_request_immutable_metadata_wrong_space_and_single_use(self):
        token = self.request()
        response = self.client.get('/users/oauth/metadata', {'request_id': token})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['target_space']['id'], self.space.id)
        self.assertEqual(self.client.get('/users/oauth/metadata', {'request_id':token, 'space_id':1}).status_code,400)
        other = Space.objects.create(name='other', slug='other', email='other@example.test')
        flow = resolve(token)
        wrong = User.create(other, 'wrong')
        wrong.email, wrong.email_verified_at = 'wrong@example.test', timezone.now()
        wrong.phone, wrong.phone_verified_at = '+8613800138001', timezone.now()
        wrong.save()
        with self.assertRaises(OAuthError): authorize(wrong, **flow.parameters, flow=flow)
        self.assertEqual(OAuthGrant.objects.count(),0)
        code = self.issue(token)
        with self.assertRaises(OAuthError): resolve(token)
        access = exchange(client_id='stillfolio',redirect_uri=self.redirect,code=code,code_verifier=self.verifier)['access_token']
        self.assertEqual(userinfo(access)['space_id'], self.space.id)
        self.assertNotIn('space_admin',userinfo(access))
        User.objects.filter(pk=self.user.pk).update(space=other)
        with self.assertRaises(OAuthError): userinfo(access)

    def test_cancel_expired_disabled_and_admin_denied(self):
        token=self.request()
        self.assertEqual(self.client.post('/users/oauth/requests/cancel', data={'request_id':token}, content_type='application/json').status_code,200)
        with self.assertRaises(OAuthError): resolve(token)
        token=self.request()
        OAuthAuthorizationRequest.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(OAuthError): resolve(token)
        with override_settings(OAUTH_SPACE_REQUESTS_ENABLED=False):
            with self.assertRaises(OAuthError): self.request()
        with self.assertRaises(OAuthError): self.request(purpose='space_admin')

    def test_bound_post_rejects_tamper_and_resumes_after_verification(self):
        from utils.auth import get_login_token
        token=self.request()
        header={'HTTP_AUTHORIZATION':'Bearer '+get_login_token(self.user)['auth']}
        response=self.client.post('/users/oauth/authorize', data={'request_id':token,'space_id':self.space.id}, content_type='application/json', **header)
        self.assertEqual(response.status_code,400)
        User.objects.filter(pk=self.user.pk).update(email_verified_at=None)
        response=self.client.post('/users/oauth/authorize', data={'request_id':token}, content_type='application/json', **header)
        self.assertEqual(response.status_code,403)
        self.assertIsNone(resolve(token).used_at)
        User.objects.filter(pk=self.user.pk).update(email_verified_at=timezone.now())
        response=self.client.post('/users/oauth/authorize', data={'request_id':token}, content_type='application/json', **header)
        self.assertEqual(response.status_code,200)
        self.assertEqual(parse_qs(urlparse(response.json()['redirect_uri']).query)['state'],['s'*32])

    def test_admin_capability_requires_platform_admin_and_audits(self):
        from User.models import OAuthApplication
        from User.oauth_registry import mutate, RegistryError
        app=OAuthApplication.objects.get(client_id='stillfolio')
        with self.assertRaises(RegistryError):
            mutate(actor='developer',owner=self.user,app_id=app.pk,data={'action':'admin_proof','version':app.version,'enabled':True})
        result=mutate(actor='fixture-admin',admin=True,app_id=app.pk,data={'action':'admin_proof','version':app.version,'enabled':True})
        self.assertTrue(result['space_admin_proof_enabled'])
        self.assertEqual(result['audit'][0]['action'],'admin_proof')

    def test_admin_proof_revocation_and_double_verification(self):
        from User.models import OAuthApplication
        OAuthApplication.objects.filter(client_id='stillfolio').update(space_admin_proof_enabled=True)
        token=self.request(purpose='space_admin')
        with self.assertRaises(OAuthError): self.issue(token)
        from User.models import UserRoleChoice
        self.user.role=UserRoleChoice.OFFICIAL
        self.user.save(update_fields=['role'])
        self.space.official_user=self.user
        self.space.save(update_fields=['official_user'])
        code=self.issue(token)
        User.objects.filter(pk=self.user.pk).update(phone_verified_at=None)
        with self.assertRaises(OAuthError): exchange(client_id='stillfolio',redirect_uri=self.redirect,code=code,code_verifier=self.verifier)
        User.objects.filter(pk=self.user.pk).update(phone_verified_at=timezone.now())
        access=exchange(client_id='stillfolio',redirect_uri=self.redirect,code=code,code_verifier=self.verifier)['access_token']
        self.assertTrue(userinfo(access)['space_admin'])
        OAuthApplication.objects.filter(client_id='stillfolio').update(space_admin_proof_enabled=False)
        with self.assertRaises(OAuthError): userinfo(access)
