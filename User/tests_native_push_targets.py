import datetime

from django.test import TestCase

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ResearchProject
from Message.models import Message, MessageUserState
from Square.models import Statement, StatementVisibilityChoice
from User import tests_native_push_bindings as fixtures
from User.models import NotificationEvent, User
from utils import auth


class NativePushTargetTests(TestCase):
    def setUp(self):
        fixtures.NativePushBindingTests.setUp(self)
        self.space.group_square_enabled = self.space.research_enabled = self.space.chat_enabled = True
        self.space.save()
        self.peer = User.create(self.space, 'Peer', verified=True)

    def event(self, event_type=4, payload=None, user=None, space=None):
        user = user or self.a
        return NotificationEvent.objects.create(user=user, space=space or user.space,
            actor=self.peer, event_type=event_type, payload=payload or {})

    def call(self, event_id, user=None, authenticated=True, query=None):
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user or self.a)['auth']} if authenticated else {}
        return self.client.get('/users/me/native-push/events/' + str(event_id), query or {}, **headers)

    def target(self, event):
        response = self.call(event.pk)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        return response.json()['body']['target']

    def chat(self, purpose=0):
        chat = Chat.objects.create(space=self.space, created_by=self.peer, chat_type=1, purpose=purpose)
        member = ChatMember.objects.create(chat=chat, user=self.a, status=ChatMemberStatusChoice.ACTIVE)
        message = Message.objects.create(chat=chat, user=self.peer, type=0, content='Private message')
        return chat, member, message

    def test_requires_authentication_and_no_store_even_on_failure(self):
        response = self.call(999, authenticated=False)
        self.assertNotEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-store, private')

    def test_foreign_missing_and_corrupt_space_events_are_indistinguishable(self):
        foreign = self.event(user=self.b)
        corrupt = self.event(space=self.other_space)
        responses = [self.call(value) for value in (foreign.pk, corrupt.pk, foreign.pk + 1000)]
        self.assertTrue(all(response.status_code == 404 for response in responses))
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[1].json(), responses[2].json())
        self.assertTrue(all(response['Cache-Control'] == 'no-store, private' for response in responses))

    def test_metadata_is_canonical_private_and_read_only(self):
        event = self.event(payload={'kind': 'friend_request', 'content': 'Secret', 'url': 'https://evil.invalid',
                                    'account_id': self.b.pk, 'token': 'credential'})
        count = NotificationEvent.objects.count()
        response = self.call(event.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['body'], dict(notification_event_id=event.pk, account_id=self.a.pk,
            space_slug=self.space.slug, target={'kind': 'contacts'}))
        self.assertNotIn('Secret', response.content.decode())
        self.assertNotIn('evil.invalid', response.content.decode())
        self.assertNotIn('credential', response.content.decode())
        event.refresh_from_db(); self.assertFalse(event.is_read)
        self.assertEqual(NotificationEvent.objects.count(), count)

    def test_client_supplied_identity_query_is_rejected(self):
        event = self.event()
        self.assertEqual(self.call(event.pk, query={'user_id': self.b.pk}).status_code, 404)

    def test_current_visible_message_routes_to_chat_without_text(self):
        chat, member, message = self.chat()
        event = self.event(2, {'chat_id': chat.pk, 'message_id': message.pk, 'content': 'Private message'})
        self.assertEqual(self.target(event), dict(kind='chat', chat_id=chat.pk, message_id=message.pk))

    def test_recalled_hidden_left_and_deleted_chat_do_not_route(self):
        chat, member, message = self.chat()
        event = self.event(2, {'chat_id': chat.pk, 'message_id': message.pk})
        message.is_deleted = True; message.save(update_fields=['is_deleted'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        message.is_deleted = False; message.save(update_fields=['is_deleted'])
        MessageUserState.objects.create(message=message, user=self.a)
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        MessageUserState.objects.all().delete()
        member.status = ChatMemberStatusChoice.LEFT; member.save(update_fields=['status'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        member.status = ChatMemberStatusChoice.ACTIVE; member.save(update_fields=['status'])
        chat.is_deleted = True; chat.save(update_fields=['is_deleted'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_message_identity_is_strict_and_cannot_cross_chats(self):
        chat, member, message = self.chat()
        for payload in ({'chat_id': chat.pk, 'message_id': True}, {'chat_id': str(chat.pk), 'message_id': message.pk},
                        {'chat_id': chat.pk + 100, 'message_id': message.pk}, {'chat_id': chat.pk, 'message_id': -1}):
            self.assertEqual(self.target(self.event(2, payload)), {'kind': 'unavailable'})

    def test_chat_flag_and_current_membership_are_enforced_but_mute_is_not_access_control(self):
        from Chat.models import ChatUserPreference
        chat, member, message = self.chat()
        event = self.event(2, {'chat_id': chat.pk, 'message_id': message.pk})
        ChatUserPreference.objects.create(chat=chat, user=self.a, notifications_muted=True)
        self.assertEqual(self.target(event)['kind'], 'chat')
        self.space.chat_enabled = False; self.space.save(update_fields=['chat_enabled'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_square_uses_current_visibility_and_space_availability(self):
        statement = Statement.objects.create(space=self.space, user=self.peer, text='Private square body', visibility=StatementVisibilityChoice.PUBLIC)
        event = self.event(6, {'statement_id': statement.pk, 'comment_id': 123})
        self.assertEqual(self.target(event), dict(kind='statement', statement_id=statement.pk))
        statement.visibility = StatementVisibilityChoice.FRIENDS; statement.save(update_fields=['visibility'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        statement.visibility = StatementVisibilityChoice.PUBLIC; statement.save(update_fields=['visibility'])
        self.space.group_square_enabled = False; self.space.save(update_fields=['group_square_enabled'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_removed_or_missing_square_statement_is_unavailable(self):
        statement = Statement.objects.create(space=self.space, user=self.peer, text='Body', visibility=StatementVisibilityChoice.PUBLIC, is_deleted=True)
        for event_type, statement_id in ((5, statement.pk), (9, statement.pk), (6, statement.pk + 100)):
            self.assertEqual(self.target(self.event(event_type, {'statement_id': statement_id})), {'kind': 'unavailable'})

    def test_research_task_requires_live_project_membership_and_task(self):
        chat, member, message = self.chat(purpose=2)
        project = ResearchProject.objects.create(chat=chat, start=datetime.date(2026, 11, 2),
            tasks=[{'id': 'task-one', 'body': 'Private research'}])
        event = self.event(payload={'kind': 'research_task', 'project_id': project.pk, 'task_id': 'task-one'})
        self.assertEqual(self.target(event), dict(kind='research', project_id=project.pk, task_id='task-one'))
        project.tasks = []; project.save(update_fields=['tasks'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        project.tasks = [{'id': 'task-one'}]; project.save(update_fields=['tasks'])
        member.status = ChatMemberStatusChoice.LEFT; member.save(update_fields=['status'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_research_chat_uses_research_flag_not_regular_chat_flag(self):
        chat, member, message = self.chat(purpose=2)
        project = ResearchProject.objects.create(chat=chat, start=datetime.date(2026, 11, 2))
        self.space.chat_enabled = False; self.space.save(update_fields=['chat_enabled'])
        event = self.event(2, {'chat_id': chat.pk, 'message_id': message.pk})
        self.assertEqual(self.target(event), dict(kind='research_chat', project_id=project.pk,
                                                chat_id=chat.pk, message_id=message.pk))
        self.space.research_enabled = False; self.space.save(update_fields=['research_enabled'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_unknown_event_never_uses_arbitrary_payload_url(self):
        self.assertEqual(self.target(self.event(payload={'url': 'https://evil.invalid'})), {'kind': 'unavailable'})

    def test_direct_chat_requires_current_friendship(self):
        from Friendship.models import Friendship
        Friendship.ensure_locked_friendship(self.a, self.peer)
        chat = Chat.get_or_create_direct(self.a, self.peer)
        message = Message.objects.create(chat=chat, user=self.peer, type=0, content='Body')
        event = self.event(1, {'chat_id': chat.pk, 'message_id': message.pk})
        self.assertEqual(self.target(event)['kind'], 'chat')
        Friendship.objects.filter(space=self.space).update(status=0)
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_submission_uses_current_round_visibility_and_space_flag(self):
        from Chat.models import Submission, SubmissionStatusChoice
        chat, member, message = self.chat(purpose=1)
        event = self.event(2, {'chat_id': chat.pk, 'message_id': message.pk})
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        submission = Submission.objects.create(chat=chat, author=self.peer, recipient=self.a,
            client_draft_id='native-target', status=SubmissionStatusChoice.REVIEW)
        self.space.submission_enabled = True; self.space.save(update_fields=['submission_enabled'])
        self.assertEqual(self.target(event), dict(kind='submission', chat_id=chat.pk, message_id=message.pk))
        submission.status = SubmissionStatusChoice.DRAFT; submission.save(update_fields=['status'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})
        submission.status = SubmissionStatusChoice.REVIEW; submission.save(update_fields=['status'])
        self.space.submission_enabled = False; self.space.save(update_fields=['submission_enabled'])
        self.assertEqual(self.target(event), {'kind': 'unavailable'})

    def test_large_event_id_and_unsupported_method_fail_without_side_effects(self):
        self.assertEqual(self.call(2 ** 100).status_code, 404)
        event = self.event()
        response = self.client.post('/users/me/native-push/events/' + str(event.pk), {},
            HTTP_AUTHORIZATION='Bearer ' + auth.get_login_token(self.a)['auth'])
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        event.refresh_from_db(); self.assertFalse(event.is_read)
