from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatReadState, ChatTypeChoice
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

        self.assertEqual(first_sync['chat_states'][0]['unread_count'], 1)

        ChatReadState.mark_read(self.chat, self.me)
        second_sync = MessageEvent.sync_for_user(self.me, after=0, limit=50)

        self.assertEqual(second_sync['chat_states'][0]['unread_count'], 0)
        self.assertIsNotNone(second_sync['chat_states'][0]['last_read_at'])
