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
            password='secret1',
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
