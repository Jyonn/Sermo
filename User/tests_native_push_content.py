import datetime
from unittest.mock import patch

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatUserPreference, ResearchProject
from Square.models import Statement, StatementVisibilityChoice
from User import tests_native_push_dispatch as fixtures
from User.models import NotificationEventTypeChoice as Kind, User
from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_dispatch import dispatch_native_delivery
from django.test import TestCase


class NativePushContentTests(TestCase):
    setUp = fixtures.NativePushDispatchTests.setUp
    prefer = fixtures.NativePushDispatchTests.prefer
    setup_delivery = fixtures.NativePushDispatchTests.setup_delivery

    def project_delivery(self):
        row = self.setup_delivery()
        self.space.chat_enabled = self.space.research_enabled = True
        self.space.save(update_fields=['chat_enabled', 'research_enabled'])
        peer = User.create(self.space, 'Research peer', verified=True)
        chat = Chat.objects.create(space=self.space, created_by=peer, chat_type=1, purpose=2)
        member = ChatMember.objects.create(chat=chat, user=self.a, status=ChatMemberStatusChoice.ACTIVE)
        project = ResearchProject.objects.create(chat=chat, start=datetime.date(2026, 11, 7), tasks=[{'id': 'task-one'}])
        row.event.payload = dict(kind='research_task', project_id=project.pk, task_id='task-one')
        row.event.save(update_fields=['payload'])
        return row, member, project

    def assert_not_sent(self, row):
        dispatch_native_delivery(row.pk, self.transport)
        row.refresh_from_db()
        self.assertEqual(row.state, 'skipped')
        self.transport.send.assert_not_called()

    def test_current_research_member_can_receive_existing_task(self):
        row, _, _ = self.project_delivery()
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'sent')
        self.transport.send.assert_called_once()

    def test_left_research_member_cannot_receive_queued_alert(self):
        row, member, _ = self.project_delivery()
        member.status = ChatMemberStatusChoice.LEFT; member.save(update_fields=['status'])
        self.assert_not_sent(row)

    def test_deleted_task_cannot_receive_queued_alert(self):
        row, _, project = self.project_delivery()
        project.tasks = []; project.save(update_fields=['tasks'])
        self.assert_not_sent(row)

    def test_chat_disabled_after_enqueue_blocks_research_alert(self):
        row, _, _ = self.project_delivery()
        self.space.chat_enabled = False; self.space.save(update_fields=['chat_enabled'])
        self.assert_not_sent(row)

    def test_membership_revoked_after_claim_blocks_provider_submission(self):
        row, member, _ = self.project_delivery()
        original = Delivery.claim
        def claim(*args, **kwargs):
            result = original(*args, **kwargs)
            member.status = ChatMemberStatusChoice.LEFT; member.save(update_fields=['status'])
            return result
        with patch.object(Delivery, 'claim', side_effect=claim):
            self.assert_not_sent(row)

    def test_research_chat_muted_after_enqueue_blocks_task_alert(self):
        row, _, project = self.project_delivery()
        preference = ChatUserPreference.objects.create(chat=project.chat, user=self.a, notifications_muted=True)
        from User.native_push_targets import notification_target
        self.assertEqual(notification_target(row.event, self.a)['kind'], 'research')
        self.assert_not_sent(row)
        preference.notifications_muted = False; preference.save(update_fields=['notifications_muted'])
        self.assertIsNone(dispatch_native_delivery(row.pk, self.transport))
        self.transport.send.assert_not_called()

    def test_research_chat_muted_after_claim_blocks_task_alert(self):
        row, _, project = self.project_delivery()
        original = Delivery.claim
        def claim(*args, **kwargs):
            result = original(*args, **kwargs)
            ChatUserPreference.objects.create(chat=project.chat, user=self.a, notifications_muted=True)
            return result
        with patch.object(Delivery, 'claim', side_effect=claim):
            self.assert_not_sent(row)

    def test_another_members_research_mute_does_not_block_recipient(self):
        row, _, project = self.project_delivery()
        peer = project.chat.created_by
        ChatMember.objects.create(chat=project.chat, user=peer, status=ChatMemberStatusChoice.ACTIVE)
        ChatUserPreference.objects.create(chat=project.chat, user=peer, notifications_muted=True)
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'sent')
        self.transport.send.assert_called_once()

    def test_another_project_mute_does_not_block_current_project(self):
        row, _, project = self.project_delivery()
        other = Chat.objects.create(space=self.space, created_by=project.chat.created_by, chat_type=1, purpose=2)
        ChatMember.objects.create(chat=other, user=self.a, status=ChatMemberStatusChoice.ACTIVE)
        ResearchProject.objects.create(chat=other, start=datetime.date(2026, 11, 7))
        ChatUserPreference.objects.create(chat=other, user=self.a, notifications_muted=True)
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'sent')
        self.transport.send.assert_called_once()

    def test_private_square_statement_cannot_receive_queued_alert(self):
        row, statement = self.square_delivery()
        statement.visibility = StatementVisibilityChoice.FRIENDS
        statement.save(update_fields=['visibility'])
        self.assert_not_sent(row)

    def test_public_square_statement_can_receive_alert(self):
        row, _ = self.square_delivery()
        self.assertEqual(dispatch_native_delivery(row.pk, self.transport), 'sent')
        self.transport.send.assert_called_once()

    def square_delivery(self):
        row = self.setup_delivery()
        self.space.group_square_enabled = True; self.space.save(update_fields=['group_square_enabled'])
        peer = User.create(self.space, 'Square peer', verified=True)
        statement = Statement.objects.create(space=self.space, user=peer, text='Statement body', visibility=StatementVisibilityChoice.PUBLIC)
        row.event.event_type = Kind.SQUARE_STATEMENT_COMMENT
        row.event.payload = dict(statement_id=statement.pk)
        row.event.save(update_fields=['event_type', 'payload'])
        return row, statement
