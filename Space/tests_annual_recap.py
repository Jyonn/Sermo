import json
from datetime import timedelta

from django.test import TestCase

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.annual_recap import CUTOFF, START, summarize
from Space.models import Space, SpaceFeatureGrant, SpaceFeatureKeyChoice
from Sticker.models import StickerAsset, StickerSeries
from User.models import User
from utils.auth import get_login_token, get_space_login_token


class AnnualRecapTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Recap', slug='recap', email='recap@example.test')
        self.user = User.create(self.space, 'Member')
        self.chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=self.user)
        ChatMember.objects.create(chat=self.chat, user=self.user, status=ChatMemberStatusChoice.ACTIVE)

    def enable(self):
        return self.client.post('/spaces/admin/settings', {
            'name': self.space.name, 'group_square_enabled': 0, 'chat_enabled': 1,
            'square_explore_enabled': 0, 'member_limit': 5, 'annual_recap_enabled': 1,
        }, content_type='application/json',
            HTTP_AUTHORIZATION='Bearer ' + get_space_login_token(self.space)['auth'])

    def test_recap_requires_grant_and_admin_activation(self):
        def recap():
            return self.client.get('/spaces/annual-recap',
                HTTP_AUTHORIZATION='Bearer ' + get_login_token(self.user)['auth'])

        self.assertEqual(self.enable().status_code, 403)
        SpaceFeatureGrant.set_granted(self.space, SpaceFeatureKeyChoice.ANNUAL_RECAP, True, 'root@example.test')
        self.assertEqual(recap().status_code, 403)
        self.assertEqual(self.enable().status_code, 200)
        response = recap()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['body']['year'], 2026)
        self.assertIn('no-store', response['Cache-Control'])
        self.assertIn('private', response['Cache-Control'])
        SpaceFeatureGrant.set_granted(self.space, SpaceFeatureKeyChoice.ANNUAL_RECAP, False, 'root@example.test')
        self.assertEqual(recap().status_code, 403)

    def test_cutoff_and_personal_results(self):
        other = User.create(self.space, 'Other')
        Message.objects.filter(chat__space=self.space).update(created_at=START - timedelta(seconds=1))
        for user, sent_at, deleted in (
            (self.user, CUTOFF - timedelta(seconds=1), False),
            (self.user, CUTOFF, False),
            (self.user, CUTOFF - timedelta(hours=1), True),
            (other, CUTOFF - timedelta(hours=1), False),
        ):
            message = Message.objects.create(chat=self.chat, user=user, type=MessageTypeChoice.TEXT,
                                             content='Private text', is_deleted=deleted)
            Message.objects.filter(pk=message.pk).update(created_at=sent_at)
        result = summarize(self.user, now=CUTOFF + timedelta(days=1))
        self.assertEqual(result['space_message_count'], 2)
        self.assertEqual(result['personal_message_count'], 1)
        self.assertEqual(result['favorite_chat'], {'chat_id': self.chat.id, 'count': 1, 'name': None})
        self.assertNotIn('Private text', str(result))

    def test_only_published_stickers_can_appear_in_space_recap(self):
        public = StickerAsset.objects.create(content_hash='a' * 64, storage_key='public.png')
        private = StickerAsset.objects.create(content_hash='b' * 64, storage_key='private.png')
        StickerSeries.objects.create(user=self.user, name='Published',
                                     published_snapshot={'name': 'Published', 'asset_ids': [public.pk]})
        for asset in (private, private, public):
            message = Message.objects.create(chat=self.chat, user=self.user, type=MessageTypeChoice.STICKER,
                                             content=json.dumps({'kind': 'sticker', 'asset_id': asset.pk}))
            Message.objects.filter(pk=message.pk).update(created_at=CUTOFF - timedelta(hours=1))
        result = summarize(self.user, now=CUTOFF)
        self.assertEqual(result['favorite_public_sticker'], {'uri': public.resource_uri(), 'count': 1})
