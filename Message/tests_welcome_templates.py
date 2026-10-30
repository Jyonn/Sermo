import json
from unittest.mock import patch

from django.test import TestCase

from Message.models import MessageTypeChoice, WelcomeMessageTemplate
from Space.models import Space
from User.models import User, UserRoleChoice
from User.validators import UserErrors
from utils import auth


class WelcomeMessageTemplateTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Welcome Templates', slug='welcome-msgs', email='welcome@example.com',
        )
        self.user = User.create(space=self.space, name='Member', password='secret1')
        WelcomeMessageTemplate.objects.filter(user=self.user).delete()

    def test_default_setup_does_not_award_customization_or_read_archive(self):
        self.user.welcome_message = 'Stale archived text'
        self.user.save(update_fields=['welcome_message'])
        with patch.object(User, 'award_growth') as award:
            self.user.ensure_welcome_messages()
            self.user.ensure_welcome_messages()
        award.assert_not_called()
        self.assertEqual(self.user.welcome_message_templates.count(), 1)
        self.assertEqual(self.user.welcome_message_templates.get().content,
                         User.default_welcome_message(self.space, self.user.role, self.user.language))

    def test_saving_defaults_does_not_award_but_custom_message_does(self):
        default = User.default_welcome_message(self.space, self.user.role, self.user.language)
        with patch.object(User, 'require_capability'), patch.object(User, 'award_growth') as award:
            WelcomeMessageTemplate.replace_for(self.user, [self.text_item(default)])
            award.assert_not_called()
            WelcomeMessageTemplate.replace_for(self.user, [self.text_item('A real custom welcome')])
            award.assert_called_once_with('explore:welcome')
        self.user.refresh_from_db()
        self.assertEqual(self.user.welcome_message, '')
        self.assertEqual(self.user._dictify_welcome_message(), 'A real custom welcome')
        self.assertEqual(self.user.dictify('welcome_message')['welcome_message'], 'A real custom welcome')
        self.assertEqual(self.user.json_me()['welcome_message_count'], 1)

    def test_reading_and_sending_empty_list_never_revives_archived_text(self):
        from Chat.models import Chat
        self.user.welcome_message = 'Archived greeting'
        self.user.save(update_fields=['welcome_message'])
        other = User.create(self.space, 'Recipient')
        from Friendship.models import Friendship
        Friendship.ensure_locked_friendship(self.user, other)
        chat = Chat.get_or_create_direct(self.user, other)
        self.assertEqual(WelcomeMessageTemplate.payload_for(self.user)['messages'], [])
        self.assertEqual(WelcomeMessageTemplate.materialize_for(self.user, chat), [])
        self.assertFalse(self.user.welcome_message_templates.exists())

    def test_legacy_backfill_preserves_existing_lists_and_is_idempotent(self):
        from importlib import import_module
        from django.apps import apps
        from django.db import connection
        from types import SimpleNamespace

        self.user.welcome_message = 'An unmigrated welcome'
        self.user.save(update_fields=['welcome_message'])
        configured = User.create(self.space, 'Configured')
        configured.welcome_message = 'A stale shadow'
        configured.save(update_fields=['welcome_message'])
        empty = User.create(self.space, 'Empty legacy list', language='en')
        empty.welcome_message_templates.all().delete()
        official = self.space.ensure_official_user()
        official.welcome_message_templates.all().delete()
        migrate = import_module('Message.migrations.0037_backfill_remaining_welcome_templates').backfill_welcome_templates
        migrate(apps, SimpleNamespace(connection=connection))
        migrate(apps, SimpleNamespace(connection=connection))
        self.assertEqual(self.user.welcome_message_templates.get().content, 'An unmigrated welcome')
        self.assertEqual(configured.welcome_message_templates.count(), 1)
        self.assertNotEqual(configured.welcome_message_templates.get().content, 'A stale shadow')
        self.assertEqual(empty.welcome_message_templates.get().content, User.MEMBER_WELCOME_MESSAGE_EN)
        self.assertEqual(official.welcome_message_templates.get().content,
                         User.default_welcome_message(self.space, official.role, official.language))

    @staticmethod
    def text_item(content, template_message_id=None):
        item = {'type': MessageTypeChoice.TEXT, 'content': content}
        if template_message_id is not None:
            item['template_message_id'] = template_message_id
        return item

    def test_regular_user_over_limit_can_only_delete_existing_messages(self):
        templates = [
            WelcomeMessageTemplate.objects.create(
                user=self.user, position=position, type=MessageTypeChoice.TEXT, content=f'Hello {position}',
            )
            for position in range(5)
        ]
        deletion = [self.text_item(item.content, item.id) for item in templates[:4]]

        with patch.object(User, 'require_capability', return_value=self.user):
            WelcomeMessageTemplate.replace_for(self.user, deletion)
            remaining = list(WelcomeMessageTemplate.objects.filter(user=self.user))
            changed = [self.text_item(item.content, item.id) for item in remaining[:2]]
            changed.append(self.text_item('New before reaching limit'))
            with self.assertRaises(type(UserErrors.WELCOME_MESSAGES_DELETE_FIRST)):
                WelcomeMessageTemplate.replace_for(self.user, changed)

    def test_official_api_accepts_ten_messages_and_rejects_eleven(self):
        official = User.create(
            space=self.space,
            name='Welcome Admin',
            password='Welcome9!Fixture',
            role=UserRoleChoice.OFFICIAL,
        )
        authorization = {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(official)['auth']}"}
        ten = [self.text_item(f'Official hello {index}') for index in range(10)]

        response = self.client.post(
            '/users/me/welcome-message',
            data=json.dumps({'messages': ten}),
            content_type='application/json',
            **authorization,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(len(response.json()['body']['messages']), 10)
        self.assertEqual(response.json()['body']['max_messages'], 10)

        response = self.client.post(
            '/users/me/welcome-message',
            data=json.dumps({'messages': ten + [self.text_item('One too many')]}),
            content_type='application/json',
            **authorization,
        )
        self.assertEqual(response.status_code, 400, response.content)
