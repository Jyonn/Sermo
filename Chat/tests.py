import json
from datetime import datetime, timezone as datetime_timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatPurposeChoice, ChatReadState, ChatTypeChoice, ChatUserPreference, SubmissionStatusChoice
from Message.models import Message, MessageTypeChoice, PinnedMessage
from Space.models import Space, SpaceOperator
from User.models import NotificationEvent, User, UserStateEvent, UserStateEventKindChoice
from utils import auth


class SubmissionChatTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Submission Space',
            slug='submissions',
            email='admin@example.com',
            submission_enabled=True,
        )
        self.official = self.space.ensure_official_user()
        self.author = User.create(self.space, 'Author', verified=True)
        self.operator = User.create(self.space, 'Operator', verified=True)
        SpaceOperator.objects.create(space=self.space, user=self.operator)

    def authorization(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def test_submission_is_created_with_first_message_and_isolated_from_chat_list(self):
        response = self.client.post(
            '/chats/submissions/start',
            data=json.dumps({
                'peer_user_id': self.operator.id,
                'title': 'Campus confession',
                'client_draft_id': 'draft-1',
                'client_message_id': 'message-1',
                'type': MessageTypeChoice.TEXT,
                'content': 'Please publish this anonymously.',
                'resource_id': None,
            }),
            content_type='application/json',
            **self.authorization(self.author),
        )
        self.assertEqual(response.status_code, 200, response.content)
        chat = Chat.objects.get(id=response.json()['body']['chat']['chat_id'])
        self.assertEqual(chat.purpose, ChatPurposeChoice.SUBMISSION)
        self.assertEqual(Message.objects.filter(chat=chat).count(), 1)

        ordinary = self.client.get('/chats/', **self.authorization(self.author)).json()['body']
        submissions = self.client.get('/chats/?purpose=submission', **self.authorization(self.author)).json()['body']
        reviewer_drafts = self.client.get('/chats/?purpose=submission&role=reviewer', **self.authorization(self.operator)).json()['body']
        self.assertNotIn(chat.id, [item['chat_id'] for item in ordinary])
        self.assertIn(chat.id, [item['chat_id'] for item in submissions])
        self.assertEqual(reviewer_drafts, [])
        self.assertTrue(chat.is_owner(self.operator))

        submitted = self.client.post(
            f'/chats/submissions/submit?chat_id={chat.id}',
            **self.authorization(self.author),
        )
        self.assertEqual(submitted.status_code, 200, submitted.content)
        self.assertEqual(submitted.json()['body']['chat_id'], chat.id)
        self.assertEqual(submitted.json()['body']['submission']['status'], 'review')
        reviewer_rows = self.client.get('/chats/?purpose=submission&role=reviewer', **self.authorization(self.operator)).json()['body']
        self.assertIn(chat.id, [item['chat_id'] for item in reviewer_rows])
        self.assertEqual(reviewer_rows[0]['submission']['status'], 'review')

    def test_submission_start_is_idempotent(self):
        payload = {
            'peer_user_id': self.operator.id,
            'title': 'One draft',
            'client_draft_id': 'same-draft',
            'client_message_id': 'same-message',
            'type': MessageTypeChoice.TEXT,
            'content': 'Only once',
            'resource_id': None,
        }
        first = self.client.post('/chats/submissions/start', data=json.dumps(payload), content_type='application/json', **self.authorization(self.author))
        second = self.client.post('/chats/submissions/start', data=json.dumps(payload), content_type='application/json', **self.authorization(self.author))
        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json()['body']['chat']['chat_id'], second.json()['body']['chat']['chat_id'])
        self.assertEqual(Chat.objects.filter(purpose=ChatPurposeChoice.SUBMISSION).count(), 1)
        self.assertEqual(Message.objects.filter(chat_id=first.json()['body']['chat']['chat_id']).count(), 1)

    def test_submission_invite_requires_operator_review_and_reveals_full_history(self):
        chat, _ = Chat.create_submission(self.author, self.operator, 'Invite review', 'invite-draft')
        old_message = Message.create(chat, self.author, MessageTypeChoice.TEXT, 'Earlier context')
        chat.submission_record.submit(self.author)
        invited = User.create(self.space, 'Invited', verified=True)
        from Friendship.models import Friendship
        Friendship.ensure_locked_friendship(self.author, invited)

        with patch.object(self.author, 'require_capability'):
            member = chat.invite_member(self.author, invited)
        self.assertEqual(member.status, ChatMemberStatusChoice.PENDING)
        self.assertFalse(chat.has_active_member(invited))

        member.review_submission_invite(self.operator, True)
        self.assertTrue(chat.has_active_member(invited))
        self.assertIn(old_message.id, Message.visible_for_user(chat, invited).values_list('id', flat=True))
        with self.assertRaises(Exception):
            chat.leave(invited)

    def test_submission_workflow_enforces_sending_and_draft_recall(self):
        chat, _ = Chat.create_submission(self.author, self.operator, 'Workflow', 'workflow-draft')
        draft_message = Message.create(chat, self.author, MessageTypeChoice.TEXT, 'Editable draft')
        delete = self.client.delete(
            f'/messages/?message_id={draft_message.id}&scope=everyone',
            **self.authorization(self.author),
        )
        self.assertEqual(delete.status_code, 200, delete.content)
        Message.create(chat, self.author, MessageTypeChoice.TEXT, 'Final draft')
        submission = chat.submission_record
        submission.submit(self.author)
        self.assertEqual(submission.status, SubmissionStatusChoice.REVIEW)
        with self.assertRaises(Exception):
            Message.create(chat, self.author, MessageTypeChoice.TEXT, 'Locked author message')
        Message.create(chat, self.operator, MessageTypeChoice.TEXT, 'Please revise')
        revision = self.client.post(
            f'/chats/submissions/status?chat_id={chat.id}',
            data=json.dumps({'action': 'revision'}),
            content_type='application/json',
            **self.authorization(self.operator),
        )
        self.assertEqual(revision.status_code, 200, revision.content)
        self.assertEqual(revision.json()['body']['submission']['status'], 'revision')
        submission.refresh_from_db()
        review_chat = Chat.get_or_create_direct(self.operator, self.author)
        revision_notice = Message.objects.filter(chat=review_chat, type=MessageTypeChoice.OFFICIAL_NOTICE).latest('id')
        self.assertEqual(revision_notice._parse_payload(revision_notice.content)['event'], 'submission_revision')
        self.assertIn('Workflow', revision_notice.system_message_text(self.author))
        Message.create(chat, self.author, MessageTypeChoice.TEXT, 'Revision')
        submission.submit(self.author)
        submission.review(self.operator, 'ready')
        ready_notice = Message.objects.filter(chat=review_chat, type=MessageTypeChoice.OFFICIAL_NOTICE).latest('id')
        self.assertEqual(ready_notice._parse_payload(ready_notice.content)['event'], 'submission_ready')
        with self.assertRaises(Exception):
            Message.create(chat, self.operator, MessageTypeChoice.TEXT, 'Locked reviewer message')


class ChatNotificationPreferenceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Mute Test', slug='mute-test', email='admin@example.com')
        self.sender = User.create(self.space, 'Sender', verified=True)
        self.recipient = User.create(self.space, 'Recipient', verified=True)
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Muted group',
            created_by=self.sender,
        )
        ChatMember.objects.create(
            chat=self.chat,
            user=self.sender,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )
        ChatMember.objects.create(
            chat=self.chat,
            user=self.recipient,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )

    def authorization(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def test_muted_group_suppresses_external_notification_but_keeps_unread(self):
        preference_response = self.client.post(
            f'/chats/preference?chat_id={self.chat.id}',
            data=json.dumps({'notifications_muted': 1}),
            content_type='application/json',
            **self.authorization(self.recipient),
        )
        self.assertEqual(preference_response.status_code, 200, preference_response.content)
        self.assertTrue(preference_response.json()['body']['notifications_muted'])

        message = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'still delivered')
        events = NotificationEvent.emit_message_notifications(message, actor=self.sender, enqueue=False)
        self.assertFalse(any(event.user_id == self.recipient.id for event in events))

        chat_list = self.client.get('/chats/', **self.authorization(self.recipient))
        self.assertEqual(chat_list.status_code, 200, chat_list.content)
        payload = next(item for item in chat_list.json()['body'] if item['chat_id'] == self.chat.id)
        self.assertEqual(payload['unread_count'], 1)
        self.assertTrue(payload['notifications_muted'])
        self.assertFalse(payload['unread_badge_muted'])
        self.assertEqual(payload['last_message']['content'], 'still delivered')

    def test_weak_unread_keeps_count_but_marks_badge_as_muted(self):
        ChatUserPreference.update(self.chat, self.recipient, unread_badge_muted=True)
        Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'quiet unread')

        chat_list = self.client.get('/chats/', **self.authorization(self.recipient))
        payload = next(item for item in chat_list.json()['body'] if item['chat_id'] == self.chat.id)

        self.assertEqual(payload['unread_count'], 1)
        self.assertTrue(payload['notifications_muted'])
        self.assertTrue(payload['unread_badge_muted'])

    def test_unmuted_group_keeps_normal_notification_behavior(self):
        message = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'notify me')
        events = NotificationEvent.emit_message_notifications(message, actor=self.sender, enqueue=False)
        self.assertTrue(any(event.user_id == self.recipient.id for event in events))
        self.assertFalse(ChatUserPreference.ensure(self.chat, self.recipient).notifications_muted)

    def test_removed_member_and_remaining_members_receive_chat_state_event(self):
        UserStateEvent.objects.all().delete()

        self.chat.remove_member(self.sender, self.recipient)

        self.assertTrue(UserStateEvent.objects.filter(
            user=self.sender,
            kind=UserStateEventKindChoice.CHATS_CHANGED,
            resource_id=self.chat.id,
        ).exists())
        self.assertTrue(UserStateEvent.objects.filter(
            user=self.recipient,
            kind=UserStateEventKindChoice.CHATS_CHANGED,
            resource_id=self.chat.id,
        ).exists())

    def test_group_rename_creates_attributed_system_message_once(self):
        with patch.object(self.sender, 'require_capability'):
            self.chat.rename(self.sender, 'A clearer name')
            self.chat.rename(self.sender, 'A clearer name')

        messages = Message.objects.filter(chat=self.chat, type=MessageTypeChoice.SYSTEM)
        self.assertEqual(messages.count(), 1)
        message = messages.get()
        self.assertEqual(message.user_id, self.sender.id)
        self.assertEqual(message._payload_for_type()['event'], 'group_renamed')
        self.assertEqual(message._payload_for_type()['old_title'], 'Muted group')
        self.assertEqual(message._payload_for_type()['new_title'], 'A clearer name')

    def test_user_message_factory_rejects_system_message(self):
        with self.assertRaises(Exception) as raised:
            Message.create(self.chat, self.sender, MessageTypeChoice.SYSTEM, 'forged')
        self.assertIn('System messages cannot be managed by users', str(raised.exception))

        with self.assertRaises(Exception):
            Message.create(self.chat, self.sender, MessageTypeChoice.OFFICIAL_NOTICE, 'forged')
        with self.assertRaises(Exception):
            Message.create_official_notice(self.chat, self.sender, 'operator_assigned')

    def test_official_notice_is_notifiable_unread_and_used_as_chat_preview(self):
        SpaceOperator.objects.create(space=self.space, user=self.sender)

        notice = Message.create_official_notice(
            self.chat,
            self.sender,
            'operator_assigned',
        )

        self.assertEqual(ChatReadState.unread_count(self.chat, self.recipient), 1)
        self.assertEqual(notice._payload_for_type()['kind'], 'official_notice')
        self.assertTrue(NotificationEvent.objects.filter(
            user=self.recipient,
            payload__message_id=notice.id,
        ).exists())
        payload = self.client.get('/chats/', **self.authorization(self.recipient)).json()['body']
        chat_payload = next(item for item in payload if item['chat_id'] == self.chat.id)
        self.assertEqual(chat_payload['last_message']['message_id'], notice.id)

    def test_system_message_is_not_notified_unread_or_used_as_chat_preview(self):
        ordinary = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'visible preview')
        system = Message.create_system(self.chat, self.sender, 'group_renamed', new_title='Quiet rename')

        events = NotificationEvent.emit_message_notifications(system, actor=self.sender, enqueue=False)
        self.assertEqual(events, [])
        self.assertEqual(ChatReadState.unread_count(self.chat, self.recipient), 1)

        chat_list = self.client.get('/chats/', **self.authorization(self.recipient))
        payload = next(item for item in chat_list.json()['body'] if item['chat_id'] == self.chat.id)
        self.assertEqual(payload['last_message']['message_id'], ordinary.id)
        self.assertEqual(payload['last_chat_at'], ordinary.created_at.timestamp())
        self.assertEqual(self.chat.json()['last_message']['message_id'], ordinary.id)

        self.recipient.language = 'zh-CN'
        serialized = system.jsonl(request=SimpleNamespace(user=self.recipient))
        self.assertEqual(serialized['content'], 'Sender 将群名修改为“Quiet rename”')
        self.assertEqual(serialized['payload']['text'], serialized['content'])

    def test_pin_state_changes_create_system_messages_once(self):
        message = Message.create(self.chat, self.sender, MessageTypeChoice.TEXT, 'pin target')

        PinnedMessage.pin(message, self.sender)
        PinnedMessage.pin(message, self.sender)
        PinnedMessage.unpin(message, self.sender)
        PinnedMessage.unpin(message, self.sender)

        events = list(
            Message.objects.filter(chat=self.chat, type=MessageTypeChoice.SYSTEM)
            .order_by('id')
            .values_list('content', flat=True)
        )
        self.assertEqual(len(events), 2)
        self.assertEqual([json.loads(content)['event'] for content in events], [
            'message_pinned',
            'message_unpinned',
        ])

    def test_member_departure_creates_system_message_before_leaving(self):
        self.chat.leave(self.recipient)

        message = Message.objects.filter(chat=self.chat, type=MessageTypeChoice.SYSTEM).get()
        self.assertEqual(message.user_id, self.recipient.id)
        self.assertEqual(message._payload_for_type()['event'], 'member_left')

    def test_batch_member_removal_creates_one_combined_system_message(self):
        another = User.create(self.space, 'Another', verified=True)
        ChatMember.objects.create(
            chat=self.chat,
            user=another,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )

        self.chat.remove_members(self.sender, [self.recipient, another])

        messages = Message.objects.filter(chat=self.chat, type=MessageTypeChoice.SYSTEM)
        self.assertEqual(messages.count(), 1)
        payload = messages.get()._payload_for_type()
        self.assertEqual(payload['event'], 'members_removed')
        self.assertEqual(payload['member_names'], ['Recipient', 'Another'])

    def test_owner_can_transfer_group_to_active_member(self):
        self.chat.transfer_ownership(self.sender, self.recipient)

        self.assertFalse(self.chat.is_owner(self.sender))
        self.assertTrue(self.chat.is_owner(self.recipient))
        payload = Message.objects.filter(chat=self.chat, type=MessageTypeChoice.SYSTEM).get()._payload_for_type()
        self.assertEqual(payload['event'], 'ownership_transferred')
        self.assertEqual(payload['new_owner_name'], 'Recipient')

    def test_non_owner_cannot_transfer_group(self):
        with self.assertRaises(Exception):
            self.chat.transfer_ownership(self.recipient, self.sender)

        self.assertTrue(self.chat.is_owner(self.sender))


class GroupMessageVisibilityBoundaryTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='History Boundary', slug='history', email='admin@example.com')
        self.owner = User.create(self.space, 'Owner', verified=True)
        self.new_member = User.create(self.space, 'New Member', verified=True)
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Private history',
            created_by=self.owner,
        )
        ChatMember.objects.create(
            chat=self.chat,
            user=self.owner,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )
        self.old_message = Message.create(self.chat, self.owner, MessageTypeChoice.TEXT, 'before joining')
        PinnedMessage.objects.create(chat=self.chat, message=self.old_message, pinned_by=self.owner)
        self.membership = ChatMember.objects.create(
            chat=self.chat,
            user=self.new_member,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )
        self.new_message = Message.create(self.chat, self.owner, MessageTypeChoice.TEXT, 'after joining')

    def authorization(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def test_history_search_unread_and_pins_start_at_joined_at(self):
        visible_ids = list(Message.visible_for_user(self.chat, self.new_member).values_list('id', flat=True))
        self.assertEqual(visible_ids, [self.new_message.id])
        self.assertEqual(ChatReadState.unread_count(self.chat, self.new_member), 1)

        history = self.client.get(
            f'/messages/?chat_id={self.chat.id}&limit=50',
            **self.authorization(self.new_member),
        )
        self.assertEqual([item['message_id'] for item in history.json()['body']], [self.new_message.id])

        search = self.client.get(
            f'/messages/search?chat_id={self.chat.id}&keyword=before&limit=30',
            **self.authorization(self.new_member),
        )
        self.assertEqual(search.json()['body']['items'], [])

        pins = self.client.get(
            f'/messages/pins?chat_id={self.chat.id}',
            **self.authorization(self.new_member),
        )
        self.assertEqual(pins.json()['body'], [])

    def test_sync_does_not_deliver_pre_join_message_payload(self):
        sync = self.client.get('/messages/sync-v2?after=0&limit=100', **self.authorization(self.new_member))
        events = sync.json()['body']['events']
        old_event = next(event for event in events if event['message_id'] == self.old_message.id)
        new_event = next(event for event in events if event['message_id'] == self.new_message.id)
        self.assertNotIn('message', old_event)
        self.assertEqual(new_event['message']['message_id'], self.new_message.id)

    def test_search_excludes_sticker_messages(self):
        sticker = Message.objects.create(
            chat=self.chat,
            user=self.owner,
            type=MessageTypeChoice.STICKER,
            content='{"kind":"sticker","asset_id":999999}',
        )
        system = Message.objects.create(chat=self.chat, user=self.owner, type=MessageTypeChoice.SYSTEM, content='system activity')

        search = self.client.get(
            f'/messages/search?chat_id={self.chat.id}&limit=30',
            **self.authorization(self.new_member),
        )

        body = search.json()['body']
        message_ids = [item['message_id'] for item in body['items']]
        self.assertIn(self.new_message.id, message_ids)
        self.assertNotIn(sticker.id, message_ids)
        self.assertNotIn(system.id, message_ids)
        self.assertEqual(body['total_count'], 1)

    def test_search_calendar_returns_first_visible_message_for_shanghai_day(self):
        later = Message.create(self.chat, self.owner, MessageTypeChoice.TEXT, 'later that day')
        first_time = datetime(2026, 8, 12, 16, 30, tzinfo=datetime_timezone.utc)
        later_time = datetime(2026, 8, 13, 3, 0, tzinfo=datetime_timezone.utc)
        ChatMember.objects.filter(id=self.membership.id).update(
            joined_at=datetime(2026, 1, 1, tzinfo=datetime_timezone.utc),
        )
        Message.objects.filter(id=self.old_message.id).update(
            created_at=datetime(2025, 12, 31, tzinfo=datetime_timezone.utc),
        )
        Message.objects.filter(id=self.new_message.id).update(created_at=first_time)
        Message.objects.filter(id=later.id).update(created_at=later_time)

        response = self.client.get(
            f'/messages/search/calendar?chat_id={self.chat.id}&year=2026&month=8',
            **self.authorization(self.new_member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['days'], [{
            'date': '2026-08-13',
            'first_message_id': self.new_message.id,
        }])
