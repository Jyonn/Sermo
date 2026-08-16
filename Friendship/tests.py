from django.test import TestCase

from Space.models import Space
from User.models import User
from utils import auth


class FriendshipExactSearchTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Search Space', slug='search-space', email='owner@example.com')
        self.verified_user = User.create(
            space=self.space,
            name='Verified Searcher',
            email='verified@example.com',
            verified=True,
        )
        self.unverified_target = User.create(space=self.space, name='Unverified Target')
        self.unverified_searcher = User.create(space=self.space, name='Unverified Searcher')

    @staticmethod
    def authorization(user):
        token = auth.get_login_token(user)['auth']
        return {'HTTP_AUTHORIZATION': f'Bearer {token}'}

    def test_verified_user_can_find_unverified_user_by_exact_name(self):
        response = self.client.get(
            '/friends/search?name=Unverified%20Target',
            **self.authorization(self.verified_user),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['user']['user_id'], self.unverified_target.id)
        self.assertFalse(response.json()['body']['user']['verified'])

    def test_unverified_user_cannot_search(self):
        response = self.client.get(
            '/friends/search?name=Unverified%20Target',
            **self.authorization(self.unverified_searcher),
        )

        self.assertEqual(response.status_code, 403, response.content)

# Create your tests here.
