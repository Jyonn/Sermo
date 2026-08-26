import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatReadState, ChatTypeChoice, ChatUserPreference
from Message.models import Message, MessageTypeChoice, PinnedMessage
from Space.models import Space
from User.models import NotificationEvent, User
from utils import auth


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
        sticker = Message.create(self.chat, self.owner, MessageTypeChoice.STICKER, 'sticker payload')

        search = self.client.get(
            f'/messages/search?chat_id={self.chat.id}&limit=30',
            **self.authorization(self.new_member),
        )

        message_ids = [item['message_id'] for item in search.json()['body']['items']]
        self.assertIn(self.new_message.id, message_ids)
        self.assertNotIn(sticker.id, message_ids)
