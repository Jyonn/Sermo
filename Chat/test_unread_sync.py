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

    def test_new_device_baseline_skips_history_and_receives_future_events(self):
        historical = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'historical message')

        baseline = MessageEvent.sync_baseline_for_user(self.me)
        initial_sync = MessageEvent.sync_for_user(self.me, after=baseline['next_after'], limit=50)

        self.assertEqual(initial_sync['events'], [])

        future = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'future message')
        future_sync = MessageEvent.sync_for_user(self.me, after=baseline['next_after'], limit=50)

        self.assertNotIn(historical.id, [event['message_id'] for event in future_sync['events']])
        self.assertEqual([event['message_id'] for event in future_sync['events']], [future.id])

    def test_sync_baseline_ignores_events_outside_user_chats(self):
        outsider = User.create(self.space, 'Outsider', verified=True)
        outsider_chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Outsider group',
            created_by=outsider,
        )
        ChatMember.objects.create(
            chat=outsider_chat,
            user=outsider,
            status=ChatMemberStatusChoice.ACTIVE,
            joined_at=timezone.now(),
        )
        visible = Message.create(self.chat, self.peer, MessageTypeChoice.TEXT, 'visible')
        Message.create(outsider_chat, outsider, MessageTypeChoice.TEXT, 'hidden')

        baseline = MessageEvent.sync_baseline_for_user(self.me)
        visible_event_id = MessageEvent.objects.get(message=visible).id

        self.assertEqual(baseline['next_after'], visible_event_id)

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

    def test_structured_mention_token_records_user_and_renders_readable_text(self):
        message = Message.create(
            self.chat,
            self.peer,
            MessageTypeChoice.TEXT,
            f'<@{self.me.id}>不加空格也能识别',
        )

        self.assertEqual(list(message.chat_mentions.values_list('user_id', flat=True)), [self.me.id])
        self.assertEqual(message.preview_text(), '@Me不加空格也能识别')
        self.assertEqual(message.jsonl()['content'], '@Me不加空格也能识别')
