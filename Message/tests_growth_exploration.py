import json
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice
from Friendship.models import Friendship
from Message.models import Message, MessageTypeChoice, PinnedMessage
from Space.models import Space
from User.models import User


class MessageGrowthExplorationTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Message Growth', slug='message-growth', email='admin@example.com')
        self.user = User.create(self.space, 'Sender', verified=True)
        self.peer = User.create(self.space, 'Peer', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.DIRECT,
            created_by=self.user,
        )
        for user, role in ((self.user, ChatMemberRoleChoice.OWNER), (self.peer, ChatMemberRoleChoice.MEMBER)):
            ChatMember.objects.create(
                chat=self.chat,
                user=user,
                role=role,
                status=ChatMemberStatusChoice.ACTIVE,
                joined_at=timezone.now(),
            )

    @patch('Message.models.avatar_uri_for_key', return_value='https://assets.example/report.pdf')
    def test_file_reply_and_pin_exploration_events_are_idempotent(self, _avatar_uri_for_key):
        file_message = Message.create(
            self.chat,
            self.user,
            MessageTypeChoice.FILE,
            json.dumps({
                'key': 'sermo/messages/file/report.pdf',
                'file_name': 'report.pdf',
                'file_size': 120,
            }),
        )
        Message.create(self.chat, self.user, MessageTypeChoice.TEXT, '收到', reply_to=file_message)
        PinnedMessage.pin(file_message, self.user)
        PinnedMessage.pin(file_message, self.user)

        self.assertTrue(self.user.growth_events.filter(event_key='explore:file').exists())
        self.assertTrue(self.user.growth_events.filter(event_key='explore:message_reply').exists())
        self.assertEqual(self.user.growth_events.filter(event_key='explore:pin_message').count(), 1)
