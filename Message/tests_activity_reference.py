import json

from django.test import RequestFactory, TestCase

from Activity.models import ActivityCampaign, ActivityService
from Chat.models import Chat, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Message.validators import MessageErrors
from Space.models import Space
from User.models import User


class ActivityMessageReferenceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Activity Space', slug='activity-space', email='owner@example.com')
        self.author = User.create(space=self.space, name='Author', verified=True)
        self.viewer = User.create(space=self.space, name='Viewer')
        self.chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.DIRECT,
            created_by=self.author,
        )
        self.campaign = ActivityCampaign.objects.create(
            key='baxian-test',
            title='八仙聚力',
            title_en='Baxian Gathering',
            duration_seconds=15 * 24 * 60 * 60,
        )
        ActivityService.claim_for_space(self.campaign, self.space)

    def test_activity_reference_is_normalized_and_resolved(self):
        content = Message.normalize_content(MessageTypeChoice.ACTIVITY, json.dumps({
            'activity_key': self.campaign.key,
            'url': f'https://sermo.jyonn.space/{self.space.slug}/app/square/activities/{self.campaign.key}',
            'title': '八仙聚力',
        }))
        message = Message.objects.create(
            chat=self.chat,
            user=self.author,
            type=MessageTypeChoice.ACTIVITY,
            content=content,
        )
        request = RequestFactory().get('/')
        request.user = self.viewer

        payload = message._payload_for_type(request)

        self.assertEqual(payload['kind'], 'activity')
        self.assertEqual(payload['activity_key'], self.campaign.key)
        self.assertEqual(payload['activity']['title'], '八仙聚力')
        self.assertEqual(message.preview_text(), '[活动]')

    def test_unknown_activity_is_rejected(self):
        with self.assertRaises(MessageErrors.PAYLOAD_INVALID.__class__):
            Message.normalize_content(MessageTypeChoice.ACTIVITY, json.dumps({'activity_key': 'missing'}))
