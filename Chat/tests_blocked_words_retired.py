from django.test import TestCase

from Chat.models import Chat, ChatBlockedWord, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import User


class RetiredBlockedWordsTests(TestCase):
    def test_historical_rules_do_not_block_text_or_individual_forward(self):
        space = Space.objects.create(name='Legacy rules', slug='legacy-rules', email='legacy@example.com')
        author = User.create(space, 'Author', verified=True)
        peer = User.create(space, 'Peer', verified=True)
        chat = Chat.objects.create(space=space, chat_type=ChatTypeChoice.DIRECT, created_by=author)
        for user in (author, peer):
            ChatMember.objects.create(chat=chat, user=user, role=ChatMemberRoleChoice.MEMBER, status=ChatMemberStatusChoice.ACTIVE)
        ChatBlockedWord.objects.create(chat=chat, owner=author, created_by=author, word='sun', normalized='sun')

        source = Message.create(chat, author, MessageTypeChoice.TEXT, 'sunrise')
        forwarded = Message.forward_individual(source, chat, peer)

        self.assertEqual(source.content, 'sunrise')
        self.assertEqual(forwarded.content, 'sunrise')

    def test_blocked_words_endpoint_is_removed(self):
        response = self.client.get('/chats/blocked-words?chat_id=1')
        self.assertEqual(response.status_code, 404)
