from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatReadState, ChatTypeChoice, ChatUserPreference
from Message.models import Message, MessageEvent, MessageTypeChoice
from Space.models import Space
from User.models import User


class ChatUnreadSyncTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Unread Test', slug='unread-test', email='admin@example.com')
        self.me = User.create(self.space, 'Me', verified=True)
        self.peer = User.create(self.space, 'Peer', verified=True)
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Unread group',
            created_by=self.me,
        )
        for user in (self.me, self.peer):
            ChatMember.objects.create(
                chat=self.chat,
                user=user,
                status=ChatMemberStatusChoice.ACTIVE,
                joined_at=timezone.now(),
            )

    def test_own_messages_never_count_as_unread(self):
        Message.create(self.chat, self.me, MessageTypeChoice.TEXT, 'sent by me')

        self.assertEqual(ChatReadState.unread_count(self.chat, self.me), 0)
        self.assertEqual(ChatReadState.unread_count(self.chat, self.peer), 1)

    def test_sync_returns_authoritative_read_state_across_devices(self):
        Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'new message')
        first_sync = MessageEvent.sync_for_user(self.me, after=0, limit=50)
        first_state = next(state for state in first_sync['chat_states'] if state['chat_id'] == self.chat.id)

        self.assertEqual(first_state['unread_count'], 1)

        ChatReadState.mark_read(self.chat, self.me)
        second_sync = MessageEvent.sync_for_user(self.me, after=0, limit=50)
        second_state = next(state for state in second_sync['chat_states'] if state['chat_id'] == self.chat.id)

        self.assertEqual(second_state['unread_count'], 0)
        self.assertIsNotNone(second_state['last_read_at'])

    def test_mentions_and_weak_badges_are_synchronized_separately(self):
        message = Message.create(
            self.chat,
            self.peer,
            MessageTypeChoice.TEXT,
            '@Me 看这里',
            mention_user_ids=[self.me.id],
        )
        ChatUserPreference.update(self.chat, self.me, unread_badge_muted=True)

        sync = MessageEvent.sync_for_user(self.me, after=0, limit=50)
        state = next(state for state in sync['chat_states'] if state['chat_id'] == self.chat.id)
        message_event = next(event for event in sync['events'] if event['message_id'] == message.id)

        self.assertEqual(state['unread_count'], 1)
        self.assertTrue(state['unread_badge_muted'])
        self.assertTrue(state['has_unread_mention'])
        self.assertTrue(message_event['message']['mentioned_me'])

        ChatReadState.mark_read(self.chat, self.me)
        self.assertFalse(ChatReadState.has_unread_mention(self.chat, self.me))
