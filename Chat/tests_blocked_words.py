from django.test import TestCase
from django.utils import timezone

from Chat.blocked_words import change_rules, check_text, normalize_word
from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice
from Chat.validators import ChatErrors
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from User.models import User


class BlockedWordTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Rules', slug='rules', email='rules@example.com', admin_phone_verified_at=timezone.now())
        self.owner = User.create(self.space, 'Owner', email='owner@example.com', verified=True)
        self.peer = User.create(self.space, 'Peer', email='peer@example.com', verified=True)
        self.direct = Chat.get_or_create_direct(self.owner, self.peer)
        self.group = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=self.owner, title='Group')
        for user, role in ((self.owner, ChatMemberRoleChoice.OWNER), (self.peer, ChatMemberRoleChoice.MEMBER)):
            ChatMember.objects.create(chat=self.group, user=user, role=role, status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now())

    def test_normalization_ignores_unicode_spacing_and_case(self):
        self.assertEqual(normalize_word('Ａ\u200b B\u3000C'), 'abc')

    def test_direct_rule_set_by_sender_blocks_sender_and_peer(self):
        change_rules(self.direct, self.owner, 'add', word='Ａ B')
        for sender in (self.owner, self.peer):
            with self.assertRaises(ChatErrors.BLOCKED_WORD_MATCHED.__class__) as error:
                check_text(self.direct, sender, 'Say ab now')
            self.assertIn('Ａ B', str(error.exception))

    def test_direct_rules_show_both_owners_but_only_count_own_rules(self):
        change_rules(self.direct, self.owner, 'add', word='spoiler')
        response = change_rules(self.direct, self.peer, 'add', word='advertisement')
        self.assertEqual(response['own_count'], 1)
        self.assertEqual({item['owner_name'] for item in response['words']}, {'Owner', 'Peer'})

    def test_group_request_requires_owner_approval_and_can_be_withdrawn(self):
        proposal = change_rules(self.group, self.peer, 'request', word='Bad Word')['requests'][0]
        self.assertEqual(proposal['status'], 'pending')
        self.assertIsNotNone(proposal['message_id'])
        change_rules(self.group, self.owner, 'approve', item_id=proposal['id'])
        self.assertEqual(Message.objects.get(id=proposal['message_id'])._payload_for_type()['blocked_word_request']['status'], 'approved')
        with self.assertRaises(ChatErrors.BLOCKED_WORD_MATCHED.__class__):
            Message.create(self.group, self.owner, MessageTypeChoice.TEXT, 'bad word')

    def test_individual_forward_checks_destination_rules(self):
        change_rules(self.direct, self.peer, 'add', word='forbidden')
        source = Message.objects.create(chat=self.group, user=self.owner, type=MessageTypeChoice.TEXT, content='forbidden')
        with self.assertRaises(ChatErrors.BLOCKED_WORD_MATCHED.__class__):
            Message.forward_individual(source, self.direct, self.owner)

    def test_pending_limit_and_withdrawal(self):
        for word in ('word one', 'word two', 'word three'):
            change_rules(self.group, self.peer, 'request', word=word)
        with self.assertRaises(ChatErrors.BLOCKED_WORD_PENDING_LIMIT.__class__):
            change_rules(self.group, self.peer, 'request', word='word four')
        first = self.group.blocked_word_requests.first()
        change_rules(self.group, self.peer, 'withdraw', item_id=first.id)
        change_rules(self.group, self.peer, 'request', word='word four')

    def test_non_owner_cannot_approve_or_remove_group_word(self):
        proposal = change_rules(self.group, self.peer, 'request', word='bad word')['requests'][0]
        with self.assertRaises(ChatErrors.FORBIDDEN.__class__):
            change_rules(self.group, self.peer, 'approve', item_id=proposal['id'])
        rules = change_rules(self.group, self.owner, 'approve', item_id=proposal['id'])
        with self.assertRaises(ChatErrors.FORBIDDEN.__class__):
            change_rules(self.group, self.peer, 'remove', item_id=rules['words'][0]['id'])
