from django.test import TestCase

from Space.models import Space, SpaceJoinApplication
from User.models import User
from utils import auth


class SpaceAdmissionTests(TestCase):
    password = 'FixturePass7!'
    receipt = '1' * 64

    def setUp(self):
        self.space = Space.objects.create(name='Admission', slug='admission', email='admin@example.com')
        self.other_space = Space.objects.create(name='Other', slug='other-admission', email='other@example.com')
        self.admin = self.space_headers(self.space)
        self.entry = '/spaces/join'
        self.admin_url = '/spaces/admin/admission'

    def space_headers(self, space):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_space_login_token(space)['auth']}

    def post(self, url, body, headers=None):
        return self.client.post(url, data=body, content_type='application/json', **(headers or {}))

    def set_mode(self, mode, rotate=0):
        response = self.post(self.admin_url, {'mode': mode, 'rotate': rotate}, self.admin)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['body']

    def join(self, name, **kwargs):
        return self.post(self.entry, {'slug': self.space.slug, 'name': name, 'language': 'zh-CN', **kwargs})

    def apply(self, name, receipt):
        return self.post(self.entry + '/applications', {'slug': self.space.slug, 'name': name, 'note': 'Research group', 'receipt': receipt})

    def status(self, receipt, space=None):
        return self.post(self.entry + '/applications/status', {'slug': (space or self.space).slug, 'receipt': receipt})

    def test_default_open_and_existing_members_unchanged_after_invite_mode(self):
        self.assertEqual(self.join('Existing').status_code, 200)
        mode = self.set_mode('invite')
        code = mode['invite_code']
        self.assertEqual(mode['mode'], 'invite')
        self.assertEqual(self.join('Existing', new_user_intent='check').status_code, 200)
        self.assertEqual(self.join('Rejected').status_code, 403)
        self.assertEqual(self.join('New1', invite_code=code).status_code, 400)
        self.assertEqual(self.join('New1', invite_code=code.replace('-', '').lower(), password=self.password).status_code, 200)
        lookup = self.client.get('/spaces/lookup', {'slug': self.space.slug}).json()['body']
        self.assertEqual(lookup['join_mode'], 'invite')
        self.assertNotIn('join_invite_code', lookup)
        self.assertNotIn('invite_code', lookup)
        self.assertNotEqual(self.client.get(self.admin_url).status_code, 200)
        self.assertNotEqual(self.set_mode('invite', rotate=1)['invite_code'], code)
        self.assertEqual(self.join('New2', password=self.password, invite_code=code).status_code, 403)
        self.assertIsNone(self.set_mode('open')['invite_code'])
        self.assertEqual(self.join('New2').status_code, 200)

    def test_review_is_space_scoped_and_registration_requires_the_approved_receipt(self):
        self.set_mode('approval')
        self.assertEqual(self.join('NewName', password=self.password).status_code, 403)
        self.assertEqual(self.apply('Student', self.receipt).status_code, 200)
        self.assertEqual(self.apply('Student', self.receipt).status_code, 200)
        self.assertEqual(SpaceJoinApplication.objects.filter(space=self.space).count(), 1)
        self.assertFalse(User.objects.filter(space=self.space, name='Student').exists())
        self.assertEqual(self.join('Student', password=self.password, join_receipt=self.receipt).status_code, 403)
        self.assertEqual(self.status(self.receipt, self.other_space).status_code, 404)
        rows = self.client.get(self.admin_url + '/applications', **self.admin).json()['body']['items']
        self.assertEqual(len(rows), 1)
        self.assertNotIn('receipt_hash', rows[0])
        application_url = self.admin_url + f"/applications/{rows[0]['id']}"
        self.assertEqual(self.post(application_url, {'decision': 'approve'}, self.space_headers(self.other_space)).status_code, 404)
        self.assertEqual(self.post(application_url, {'decision': 'approve'}, self.admin).status_code, 200)
        self.assertEqual(self.status(self.receipt).json()['body']['status'], 'approved')
        self.assertEqual(self.join('Impostor', password=self.password, join_receipt=self.receipt).status_code, 403)
        self.assertEqual(self.join('Student', password=self.password, join_receipt='2' * 64).status_code, 403)
        self.assertEqual(self.join('Student', password=self.password, join_receipt=self.receipt).status_code, 200)
        self.assertEqual(self.status(self.receipt).json()['body']['status'], 'joined')
        self.assertEqual(self.join('Student', password=self.password, new_user_intent='check').status_code, 200)
        self.assertEqual(self.post(application_url, {'decision': 'reject'}, self.admin).status_code, 409)

    def test_declined_request_never_grants_membership(self):
        self.set_mode('approval')
        self.assertEqual(self.apply('Student', self.receipt).status_code, 200)
        application_id = SpaceJoinApplication.objects.get(space=self.space).pk
        result = self.post(self.admin_url + f'/applications/{application_id}', {'decision': 'reject'}, self.admin)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(self.status(self.receipt).json()['body']['status'], 'rejected')
        self.assertEqual(self.join('Student', password=self.password, join_receipt=self.receipt).status_code, 403)
