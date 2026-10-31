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
        self.assertEqual(Friendship.between(self.unverified_searcher, self.verified_user).status, FriendshipStatusChoice.ACCEPTED)

    def test_regular_request_to_operator_auto_accepts_but_member_request_does_not(self):
        SpaceOperator.objects.create(space=self.space, user=self.verified_user)
        relation = Friendship.create(self.unverified_searcher, self.verified_user)
        self.assertEqual(relation.status, FriendshipStatusChoice.ACCEPTED)
        self.assertIsNotNone(relation.responded_at)
        normal = Friendship.create(self.verified_user, self.unverified_target)
        self.assertEqual(normal.status, FriendshipStatusChoice.PENDING)

    def test_operator_list_marks_current_operator_as_self(self):
        SpaceOperator.objects.create(space=self.space, user=self.verified_user)
        listing = self.client.get('/friends/operators', **self.authorization(self.verified_user))
        self.assertEqual(listing.json()['body'][0]['relationship'], 'self')

    def test_pending_operator_request_is_accepted_and_other_spaces_are_rejected(self):
        SpaceOperator.objects.create(space=self.space, user=self.verified_user)
        low, high = sorted([self.unverified_searcher, self.verified_user], key=lambda user: user.id)
        Friendship.objects.create(space=self.space, user_low=low, user_high=high, requested_by=self.unverified_searcher, status=FriendshipStatusChoice.PENDING)
        self.assertEqual(Friendship.create(self.unverified_searcher, self.verified_user).status, FriendshipStatusChoice.ACCEPTED)
        other_space = Space.objects.create(name='Other', slug='other-operator', email='other@example.com')
        other_operator = User.create(other_space, 'Other operator', verified=True)
        SpaceOperator.objects.create(space=other_space, user=other_operator)
        rejected = self.client.post('/friends/operators', data=json.dumps({'to_user_id': other_operator.id}), content_type='application/json', **self.authorization(self.verified_user))
        self.assertEqual(rejected.status_code, 400)


class FriendshipStatusTests(TestCase):
    setUp = FriendshipExactSearchTests.setUp
    authorization = staticmethod(FriendshipExactSearchTests.authorization)

    def status(self, viewer, target):
        return self.client.get('/friends/status', {'user_id': target.id}, **self.authorization(viewer))

    def test_absent_relation_is_explicitly_resolved_without_writes(self):
        before = Friendship.objects.count()
        response = self.status(self.verified_user, self.unverified_target)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body'], dict(is_friend=False, relationship_resolved=True, relationship=None))
        self.assertEqual(Friendship.objects.count(), before)

    def test_all_relation_states_preserve_legacy_friendship_contract(self):
        low, high = sorted([self.verified_user, self.unverified_target], key=lambda user: user.id)
        relation = Friendship.objects.create(space=self.space, user_low=low, user_high=high, requested_by=high)
        for state in (0, 1, 2, 3):
            relation.status = state
            relation.save(update_fields=['status'])
            for viewer, peer in ((low, high), (high, low)):
                body = self.status(viewer, peer).json()['body']
                self.assertTrue(body['relationship_resolved'])
                self.assertEqual(body['relationship']['request_id'], relation.id)
                self.assertEqual(body['relationship']['status'], state)
                self.assertEqual(body['is_friend'], state == 1)
                self.assertEqual('friendship' in body, state == 1)
        relation.refresh_from_db()
        self.assertEqual(relation.status, 3)

    def test_history_window_does_not_hide_exact_pending_relation(self):
        low, high = sorted([self.verified_user, self.unverified_target], key=lambda user: user.id)
        relation = Friendship.objects.create(space=self.space, user_low=low, user_high=high, requested_by=low)
        for index in range(200):
            peer = User.create(space=self.space, name=f'History peer {index}')
            Friendship.objects.create(space=self.space, user_low=low, user_high=peer, requested_by=low)
            Friendship.objects.create(space=self.space, user_low=high, user_high=peer, requested_by=peer)
        self.assertNotIn(relation.id, [row.id for row in Friendship.request_history_outgoing(low)])
        self.assertNotIn(relation.id, [row.id for row in Friendship.request_history_incoming(high)])
        for viewer, peer in ((low, high), (high, low)):
            body = self.status(viewer, peer).json()['body']
            self.assertEqual(body['relationship']['request_id'], relation.id)
            self.assertEqual(body['relationship']['status'], 0)

    def test_status_requires_auth_and_rejects_cross_space_target(self):
        self.assertNotEqual(self.client.get('/friends/status', {'user_id': self.verified_user.id}).status_code, 200)
        other = Space.objects.create(name='Other status', slug='other-status', email='other-status@example.com')
        peer = User.create(space=other, name='Other peer')
        self.assertNotEqual(self.status(self.verified_user, peer).status_code, 200)


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
