from datetime import date, timedelta
from django.test import TestCase
from django.utils import timezone
from Chat.models import (Chat, ChatMember, ChatPurposeChoice, ChatTypeChoice, ResearchProject,
                         SubmissionInvite, SubmissionInviteStatusChoice, SubmissionMemberRoleChoice)
from Space.models import Space
from Chat.validators import ChatErrors
from User.models import User
from utils import auth


class InvitationDeclineTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(slug='decline', name='Decline', email='admin@example.invalid',
                                          group_square_enabled=True, submission_enabled=True, research_enabled=True,
                                          admin_phone='+8613800000000', admin_phone_verified_at=timezone.now())
        self.sender = User.create(self.space, 'Sender', verified=True)
        self.recipient = User.create(self.space, 'Recipient', verified=True)
        self.other = User.create(self.space, 'Other', verified=True)
        self.chat, _ = Chat.create_submission(self.sender, self.space.ensure_official_user(), 'Paper', 'decline-paper')

    def invite(self, chat=None):
        return SubmissionInvite.create_invitation(chat or self.chat, self.sender, self.recipient, SubmissionMemberRoleChoice.AUTHOR)

    def call(self, invitation, user=None, method='delete'):
        user = user or self.recipient
        return getattr(self.client, method)(f'/chats/submissions/invite?invite_id={invitation.pk}',
            HTTP_AUTHORIZATION='Bearer ' + auth.get_login_token(user)['auth'])

    def assert_error(self, response, error):
        self.assertEqual(response.json()['identifier'], error.identifier)
        self.assertIsNone(response.json()['body'])

    def test_recipient_declines_without_joining_and_repeat_is_idempotent(self):
        invitation = self.invite()
        self.assertTrue(invitation.jsonl(self.recipient)['can_decline'])
        before = ChatMember.objects.filter(chat=self.chat).count()
        for _ in range(2):
            response = self.call(invitation)
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()['body']['status'], 'declined')
            self.assertFalse(response.json()['body']['can_accept'])
            self.assertFalse(response.json()['body']['can_decline'])
        self.assertEqual(ChatMember.objects.filter(chat=self.chat).count(), before)
        self.assertFalse(ChatMember.objects.filter(chat=self.chat, user=self.recipient).exists())
        self.assert_error(self.call(invitation, method='post'), ChatErrors.SUBMISSION_INVITE_CLOSED)

    def test_sender_other_and_cross_space_cannot_decline(self):
        invitation = self.invite()
        elsewhere = Space.objects.create(slug='elsewhere', name='Elsewhere', email='elsewhere@example.invalid')
        foreign = User.create(elsewhere, 'Foreign', verified=True)
        for user in [self.sender, self.other, foreign]:
            self.assertFalse(invitation.jsonl(user)['can_decline'])
            self.assert_error(self.call(invitation, user), ChatErrors.SUBMISSION_INVITE_FORBIDDEN)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SubmissionInviteStatusChoice.PENDING)

    def test_expired_or_accepted_invitation_cannot_be_declined(self):
        invitation = self.invite()
        for status in [SubmissionInviteStatusChoice.EXPIRED, SubmissionInviteStatusChoice.ACCEPTED]:
            SubmissionInvite.objects.filter(pk=invitation.pk).update(status=status)
            self.assert_error(self.call(invitation), ChatErrors.SUBMISSION_INVITE_CLOSED)
        SubmissionInvite.objects.filter(pk=invitation.pk).update(status=0, expires_at=timezone.now() - timedelta(seconds=1))
        self.assert_error(self.call(invitation), ChatErrors.SUBMISSION_INVITE_CLOSED)

    def test_declined_is_terminal_after_expiry_and_allows_new_invitation(self):
        invitation = self.invite()
        self.assertEqual(self.call(invitation).status_code, 200)
        SubmissionInvite.objects.filter(pk=invitation.pk).update(expires_at=timezone.now() - timedelta(days=1))
        invitation.refresh_from_db()
        self.assertEqual(invitation.effective_status, 'declined')
        self.assertNotEqual(self.invite().pk, invitation.pk)

    def test_existing_member_cannot_rewrite_pending_invitation_as_declined(self):
        invitation = self.invite()
        ChatMember.objects.create(chat=self.chat, user=self.recipient, status=1,
                                  submission_role=SubmissionMemberRoleChoice.AUTHOR)
        self.assertFalse(invitation.jsonl(self.recipient)['can_decline'])
        self.assert_error(self.call(invitation), ChatErrors.SUBMISSION_INVITE_CLOSED)
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SubmissionInviteStatusChoice.PENDING)

    def test_unauthenticated_request_does_not_mutate_invitation(self):
        invitation = self.invite()
        response = self.client.delete(f'/chats/submissions/invite?invite_id={invitation.pk}')
        self.assertNotEqual(response.json()['identifier'], 'OK')
        self.assertIsNone(response.json()['body'])
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, SubmissionInviteStatusChoice.PENDING)

    def test_research_decline_versions_pending_list_without_membership(self):
        chat = Chat.objects.create(space=self.space, created_by=self.sender, title='Research',
                                   chat_type=ChatTypeChoice.GROUP, purpose=ChatPurposeChoice.RESEARCH)
        project = ResearchProject.objects.create(chat=chat, start=date(2026, 11, 6))
        invitation = self.invite(chat)
        revision = project.revision
        self.assertEqual(self.call(invitation).status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.revision, revision + 1)
        self.assertEqual(self.call(invitation).status_code, 200)
        project.refresh_from_db()
        self.assertEqual(project.revision, revision + 1)
        self.assertFalse(ChatMember.objects.filter(chat=chat, user=self.recipient).exists())
