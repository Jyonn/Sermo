from django.test import TestCase

from Space.models import Space
from Friendship.models import Friendship, FriendshipStatusChoice
from User.models import User, UserStateEvent, UserStateEventKindChoice
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


class FriendshipStateEventTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='State Space', slug='state-space', email='owner@example.com')
        self.requester = User.create(space=self.space, name='Requester', verified=True)
        self.recipient = User.create(space=self.space, name='Recipient', verified=True)
        self.friendship = Friendship.objects.create(
            space=self.space,
            user_low=self.requester,
            user_high=self.recipient,
            requested_by=self.requester,
            status=FriendshipStatusChoice.PENDING,
        )

    def event_kinds(self, user):
        return set(UserStateEvent.objects.filter(user=user).values_list('kind', flat=True))

    def test_accept_invalidates_requests_friends_and_chats_for_both_users(self):
        self.friendship.accept(self.recipient)

        expected = {
            UserStateEventKindChoice.CHATS_CHANGED,
            UserStateEventKindChoice.FRIENDS_CHANGED,
            UserStateEventKindChoice.FRIEND_REQUESTS_CHANGED,
        }
        self.assertEqual(self.event_kinds(self.requester), expected)
        self.assertEqual(self.event_kinds(self.recipient), expected)

    def test_removing_friend_invalidates_friends_and_chats_for_both_users(self):
        self.friendship.status = FriendshipStatusChoice.ACCEPTED
        self.friendship.save(update_fields=['status'])
        self.friendship.remove(self.requester)

        expected = {
            UserStateEventKindChoice.CHATS_CHANGED,
            UserStateEventKindChoice.FRIENDS_CHANGED,
        }
        self.assertEqual(self.event_kinds(self.requester), expected)
        self.assertEqual(self.event_kinds(self.recipient), expected)

# Create your tests here.
