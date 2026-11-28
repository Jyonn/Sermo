from uuid import uuid4
from unittest.mock import patch

from django.test import TestCase

from Friendship.models import Friendship, FriendshipStatusChoice
from User import tests_chronelle_account as fixtures
from User.chronelle_account import BindingFailure, store


class ChronelleFriendInvitationTests(TestCase):
    setUp = fixtures.ChronelleAccountTests.setUp
    bound = fixtures.ChronelleAccountTests.bound
    call = fixtures.ChronelleAccountTests.call

    def path_for_friend(self, options=False):
        return f'/users/{self.other.pk}/chronelle/invitations' + ('?options=1' if options else '')

    def make_friends(self):
        return Friendship.objects.create(
            space=self.space, user_low=self.user, user_high=self.other, status=FriendshipStatusChoice.ACCEPTED,
        )

    def draft(self, context):
        return {
            'context': context, 'command_id': str(uuid4()), 'title': 'Coffee',
            'start': '2026-12-01T10:00:00+08:00', 'end': '2026-12-01T10:30:00+08:00',
            'timezone': 'Asia/Shanghai', 'group_id': 7,
        }

    @patch('User.chronelle_friend_invitation.remote')
    def test_only_accepted_friends_with_distinct_bindings_are_eligible(self, remote):
        self.bound()
        self.assertEqual(self.call(path=self.path_for_friend()).json()['body'], {'available': False})
        relation = self.make_friends()
        self.assertEqual(self.call(path=self.path_for_friend()).json()['body'], {'available': False})
        self.bound(self.other)
        response = self.call(path=self.path_for_friend())
        self.assertEqual(response.json()['body'], {'available': True})
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        relation.remove(self.user)
        self.assertEqual(self.call(path=self.path_for_friend()).json()['body'], {'available': False})
        remote.assert_not_called()

    @patch('User.chronelle_friend_invitation.check_user_text')
    @patch('User.chronelle_friend_invitation.remote')
    def test_sends_only_to_friend_using_organizer_grant_and_idempotency_key(self, remote, _):
        self.make_friends()
        _, sender = self.bound()
        _, recipient = self.bound(self.other)
        self.headers['HTTP_ORIGIN'] = 'https://frienden.6-79.cn'
        remote.return_value = [{'id': 7, 'name': 'Personal', 'categories': []}]
        options = self.call(path=self.path_for_friend(options=True)).json()['body']
        self.assertEqual(options['groups'], [{'id': 7, 'name': 'Personal'}])
        self.assertNotIn(recipient['binding']['subject'], str(options))
        self.assertEqual(remote.call_args.kwargs['token'], sender['access_token'])

        draft = self.draft(options['context'])
        invitation = str(uuid4())
        remote.return_value = {'id': invitation, 'share_token': 'private'}
        for _attempt in range(2):
            response = self.call(draft, self.path_for_friend())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['body'], {'id': invitation})
            self.assertEqual(remote.call_args.args, (
                'https://chronelle.6-79.cn', '/api/oauth/calendar/invitations/',
            ))
            submitted = remote.call_args.kwargs
            self.assertEqual(submitted['key'], f"frienden-chat-{draft['command_id']}")
            self.assertEqual(submitted['token'], sender['access_token'])
            self.assertEqual(submitted['body']['subjects'], [recipient['binding']['subject']])
            self.assertNotIn('context', submitted['body'])

    @patch('User.chronelle_friend_invitation.check_user_text')
    @patch('User.chronelle_friend_invitation.remote')
    def test_rejects_rebound_accounts_invalid_origins_and_supplied_recipients(self, remote, _):
        self.make_friends()
        self.bound()
        row, recipient = self.bound(self.other)
        remote.return_value = [{'id': 7, 'name': 'Personal'}]
        options = self.call(path=self.path_for_friend(options=True)).json()['body']
        draft = self.draft(options['context'])

        self.headers['HTTP_ORIGIN'] = 'https://untrusted.example'
        self.assertEqual(self.call(draft, self.path_for_friend()).status_code, 403)
        self.headers['HTTP_ORIGIN'] = 'https://frienden.jyonn.space'
        self.assertEqual(self.call({**draft, 'subjects': [str(uuid4())]}, self.path_for_friend()).status_code, 400)

        remote.side_effect = BindingFailure('invitation_recipient_unavailable', 403)
        response = self.call(draft, self.path_for_friend())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['message'], 'invitation_recipient_unavailable')
        remote.reset_mock()
        recipient['binding']['subject'] = str(uuid4())
        store(row, recipient)
        self.assertEqual(self.call(draft, self.path_for_friend()).json()['message'], 'binding_superseded')
        remote.assert_not_called()
