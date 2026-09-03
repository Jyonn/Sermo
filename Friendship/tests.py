import json

from django.test import TestCase

from Space.models import Space, SpaceOperator
from Friendship.models import Friendship, FriendshipStatusChoice
from User.models import User, UserStateEvent, UserStateEventKindChoice
from Message.models import Message, MessageTypeChoice, WelcomeMessageTemplate
from Chat.models import Chat
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

    def test_operator_list_exposes_relationship_and_allows_unverified_requester(self):
        SpaceOperator.objects.create(space=self.space, user=self.verified_user)
        listing = self.client.get('/friends/operators', **self.authorization(self.unverified_searcher))
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()['body'][0]['relationship'], 'none')

        created = self.client.post(
            '/friends/operators',
            data=json.dumps({'to_user_id': self.verified_user.id}),
            content_type='application/json',
            **self.authorization(self.unverified_searcher),
        )
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(Friendship.between(self.unverified_searcher, self.verified_user).status, FriendshipStatusChoice.PENDING)

    def test_operator_list_marks_current_operator_as_self(self):
        SpaceOperator.objects.create(space=self.space, user=self.verified_user)
        listing = self.client.get('/friends/operators', **self.authorization(self.verified_user))
        self.assertEqual(listing.json()['body'][0]['relationship'], 'self')


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

    def test_accept_sends_all_welcome_template_messages_once(self):
        WelcomeMessageTemplate.objects.filter(user=self.recipient).delete()
        WelcomeMessageTemplate.objects.create(
            user=self.recipient, position=0, type=MessageTypeChoice.TEXT, content='First hello',
        )
        WelcomeMessageTemplate.objects.create(
            user=self.recipient, position=1, type=MessageTypeChoice.TEXT, content='Second hello',
        )

        self.friendship.accept(self.recipient)
        chat = Chat.get_or_create_direct(self.recipient, self.requester)
        sent = list(Message.objects.filter(chat=chat, user=self.recipient).order_by('id'))

        self.assertEqual([message.content for message in sent], ['First hello', 'Second hello'])
        Friendship.send_welcome_message(
            sender=self.recipient,
            receiver=self.requester,
            event_key=f'friendship:{self.friendship.id}',
        )
        self.assertEqual(Message.objects.filter(chat=chat, user=self.recipient).count(), 2)

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
