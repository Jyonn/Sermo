import json

from django.test import RequestFactory, TestCase

from Chat.models import Chat, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from Square.models import Statement
from User.models import User


class StatementMessageReferenceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Square Link', slug='square-link', email='owner@example.com')
        self.author = User.create(
            space=self.space,
            name='Author',
            email='author@example.com',
            verified=True,
        )
        self.viewer = User.create(space=self.space, name='Viewer')
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.DIRECT,
            created_by=self.author,
        )
        self.statement = Statement.create_statement(self.author, '站内发言', 'public', [])

    def test_consolidated_space_url_is_recognized(self):
        url = f'https://sermo.jyonn.space/{self.space.slug}/app/square/statements/{self.statement.id}'

        reference = Message.statement_reference_from_text(f'看看这个：{url}', self.viewer)

        self.assertEqual(reference['kind'], 'statement')
        self.assertEqual(reference['statement_id'], self.statement.id)
        self.assertEqual(reference['url'], url)

    def test_mirror_space_url_is_recognized(self):
        url = f'https://sermo.6-79.cn/{self.space.slug}/app/square/statements/{self.statement.id}'

        reference = Message.statement_reference_from_text(f'看看这个：{url}', self.viewer)

        self.assertEqual(reference['kind'], 'statement')
        self.assertEqual(reference['statement_id'], self.statement.id)
        self.assertEqual(reference['url'], url)

    def test_untrusted_or_foreign_space_url_stays_text(self):
        untrusted = f'https://example.com/{self.space.slug}/app/square/statements/{self.statement.id}'
        foreign = f'https://sermo.jyonn.space/another/app/square/statements/{self.statement.id}'

        self.assertIsNone(Message.statement_reference_from_text(untrusted, self.viewer))
        self.assertIsNone(Message.statement_reference_from_text(foreign, self.viewer))

    def test_statement_payload_is_resolved_for_each_viewer(self):
        content = Message.normalize_content(MessageTypeChoice.STATEMENT, json.dumps({
            'statement_id': self.statement.id,
            'url': f'https://sermo.jyonn.space/{self.space.slug}/app/square/statements/{self.statement.id}',
            'text': '分享发言',
        }))
        message = Message.objects.create(
            chat=self.chat,
            user=self.author,
            type=MessageTypeChoice.STATEMENT,
            content=content,
        )
        request = RequestFactory().get('/')
        request.user = self.viewer

        payload = message._payload_for_type(request)

        self.assertEqual(payload['kind'], 'statement')
        self.assertEqual(payload['statement']['text'], '站内发言')
        self.assertEqual(payload['statement']['like_count'], 0)

    def test_deleted_statement_keeps_message_without_leaking_content(self):
        content = Message.normalize_content(MessageTypeChoice.STATEMENT, json.dumps({
            'statement_id': self.statement.id,
            'url': f'https://sermo.jyonn.space/{self.space.slug}/app/square/statements/{self.statement.id}',
        }))
        message = Message.objects.create(
            chat=self.chat,
            user=self.author,
            type=MessageTypeChoice.STATEMENT,
            content=content,
        )
        self.statement.is_deleted = True
        self.statement.save(update_fields=['is_deleted'])
        request = RequestFactory().get('/')
        request.user = self.viewer

        payload = message._payload_for_type(request)

        self.assertIsNone(payload['statement'])
