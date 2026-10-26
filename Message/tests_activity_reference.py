import json
from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.utils import timezone

from Activity.models import ActivityCampaign, ActivityService, NumberDuel
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

    def test_four_sites_activity_links_are_normalized_on_send(self):
        from Chat.models import ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice
        from Friendship.models import Friendship
        Friendship.ensure_locked_friendship(self.author, self.viewer)
        for user in [self.author, self.viewer]:
            ChatMember.objects.create(chat=self.chat, user=user, role=ChatMemberRoleChoice.MEMBER,
                                      status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now())
        for host in ['sermo.jyonn.space', 'frienden.jyonn.space', 'sermo.6-79.cn', 'frienden.6-79.cn']:
            with self.subTest(host=host):
                url = f'https://{host}/{self.space.slug}/app/square/activities/{self.campaign.key}'
                message = Message.create(self.chat, self.author, MessageTypeChoice.TEXT, url)
                self.assertEqual(message.type, MessageTypeChoice.ACTIVITY)
                self.assertEqual(json.loads(message.content)['activity_key'], self.campaign.key)

    def test_room_links_and_legacy_links_are_scoped_to_space_and_campaign(self):
        from Activity.models import SpaceActivity
        self.campaign.config = {'mode': 'number_duel'}
        self.campaign.save(update_fields=['config'])
        run = SpaceActivity.objects.get(campaign=self.campaign, space=self.space)
        room = NumberDuel.objects.create(run=run, host=self.author, code='ABCD2345', deadline=timezone.now() + timedelta(minutes=10))
        for host in ['sermo.jyonn.space', 'frienden.jyonn.space', 'sermo.6-79.cn', 'frienden.6-79.cn']:
            for path in [f'app/number-duel/{room.code}', f'app/square/activities/{self.campaign.key}?room={room.code}']:
                with self.subTest(host=host, path=path):
                    reference = Message.site_reference_from_text(f'https://{host}/{self.space.slug}/{path}', self.viewer)
                    self.assertEqual(reference['kind'], 'number_duel_room')
                    self.assertEqual(reference['room_code'], room.code)
        for url in [
            f'https://frienden.jyonn.space.evil.test/{self.space.slug}/app/number-duel/{room.code}',
            f'https://frienden.jyonn.space/other/app/number-duel/{room.code}',
            f'https://frienden.jyonn.space@evil.test/{self.space.slug}/app/number-duel/{room.code}',
            f'https://frienden.jyonn.space/{self.space.slug}/app/number-duel/ZZZZ2345',
            f'https://frienden.jyonn.space/{self.space.slug}/app/square/activities/missing?room={room.code}',
            f'https://frienden.jyonn.space/{self.space.slug}/app/square/activities/{self.campaign.key}?room=invalid',
        ]:
            self.assertIsNone(Message.site_reference_from_text(url, self.viewer))
        other = Space.objects.create(name='Other', slug='elsewhere', email='elsewhere@example.test')
        outsider = User.create(space=other, name='Outsider')
        self.assertIsNone(Message.site_reference_from_text(f'https://frienden.6-79.cn/app/number-duel/{room.code}', outsider))
        self.campaign.enabled = False
        self.campaign.save(update_fields=['enabled'])
        self.assertIsNone(Message.site_reference_from_text(f'https://frienden.6-79.cn/{self.space.slug}/app/number-duel/{room.code}', self.viewer))

    def test_duel_room_reference_preserves_card_and_only_exposes_public_details(self):
        self.campaign.config = {'mode': 'number_duel'}
        self.campaign.save(update_fields=['config'])
        from Activity.models import SpaceActivity
        run = SpaceActivity.objects.get(campaign=self.campaign, space=self.space)
        room = NumberDuel.objects.create(run=run, host=self.author, code='ABCD2345', deadline=timezone.now() + timedelta(minutes=10))
        content = Message.normalize_content(MessageTypeChoice.ACTIVITY, json.dumps({
            'kind': 'number_duel_room', 'activity_key': self.campaign.key, 'room_code': room.code,
            'host_secret': '1234', 'name': 'Spoofed name',
        }))
        self.assertEqual(json.loads(content)['room_code'], room.code)
        self.assertNotIn('host_secret', json.loads(content))
        message = Message(chat=self.chat, user=self.author, type=MessageTypeChoice.ACTIVITY, content=content)
        request = RequestFactory().get('/')
        request.user = self.viewer
        payload = message._payload_for_type(request)
        self.assertEqual(payload['duel_room']['name'], self.author.name)
        self.assertEqual(payload['room_code'], room.code)
        self.assertNotIn('own_secret', payload['duel_room'])
        self.assertEqual(json.loads(Message.normalize_content(MessageTypeChoice.ACTIVITY, content))['room_code'], room.code)
        other_space = Space.objects.create(name='Other', slug='other-activity-space', email='other@example.test')
        request.user = User.create(space=other_space, name='Outsider')
        private_payload = message._payload_for_type(request)
        self.assertNotIn('duel_room', private_payload)
        self.assertIsNone(private_payload['activity'])

    def test_room_reference_requires_matching_campaign(self):
        with self.assertRaises(type(MessageErrors.PAYLOAD_INVALID)):
            Message.normalize_content(MessageTypeChoice.ACTIVITY, json.dumps({
                'kind': 'number_duel_room', 'activity_key': self.campaign.key, 'room_code': 'ABCD2345',
            }))
