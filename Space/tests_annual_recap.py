import json
from datetime import timedelta

from django.test import TestCase

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice
from Message.models import Message, MessageTypeChoice
from Space.annual_recap import CUTOFF, START, summarize
from Space.models import Space, SpaceFeatureGrant, SpaceFeatureKeyChoice
from Square.models import Statement, StatementComment, StatementLike, StatementVisibilityChoice
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
        self.assertEqual(result['monthly_message_counts'], [0] * 11 + [1])
        self.assertEqual(result['conversation_count'], 1)
        self.assertIsNone(result['square'])
        self.assertEqual(result['favorite_chat'], {'chat_id': self.chat.id, 'count': 1, 'name': None})
        self.assertNotIn('Private text', str(result))

    def test_month_boundaries_and_current_chat_membership(self):
        former_chat = Chat.objects.create(space=self.space, chat_type=ChatTypeChoice.GROUP, created_by=self.user)
        ChatMember.objects.filter(chat=former_chat, user=self.user).delete()
        Message.objects.filter(chat__space=self.space).update(created_at=START - timedelta(seconds=1))
        for chat, sent_at, kind in (
            (self.chat, START + timedelta(days=31, seconds=-1), MessageTypeChoice.TEXT),
            (self.chat, START + timedelta(days=31), MessageTypeChoice.FLASH_IMAGE),
            (self.chat, START + timedelta(days=31), MessageTypeChoice.SYSTEM),
            (former_chat, START + timedelta(days=31), MessageTypeChoice.TEXT),
        ):
            message = Message.objects.create(chat=chat, user=self.user, type=kind, content='Private content')
            Message.objects.filter(pk=message.pk).update(created_at=sent_at)
        result = summarize(self.user, now=CUTOFF)
        self.assertEqual(result['monthly_message_counts'], [1, 1] + [0] * 10)
        self.assertEqual(result['personal_message_count'], 2)
        self.assertEqual(result['space_message_count'], 3)
        self.assertEqual(result['active_days'], 2)
        self.assertEqual(result['conversation_count'], 1)

    def test_square_counts_respect_visibility_and_cutoff(self):
        self.space.group_square_enabled = True
        self.space.save(update_fields=['group_square_enabled'])
        other = User.create(self.space, 'Other')
        own = Statement.objects.create(space=self.space, user=self.user, text='Personal post', is_anonymous=True)
        removed = Statement.objects.create(space=self.space, user=self.user, is_deleted=True)
        friends_only = Statement.objects.create(space=self.space, user=other, visibility=StatementVisibilityChoice.FRIENDS)
        older = Statement.objects.create(space=self.space, user=other)
        Statement.objects.filter(pk__in=[own.pk, removed.pk, friends_only.pk]).update(created_at=START)
        Statement.objects.filter(pk=older.pk).update(created_at=START - timedelta(days=1))
        for statement, deleted in ((own, False), (older, False), (own, True), (friends_only, False), (removed, False)):
            comment = StatementComment.objects.create(statement=statement, user=self.user, text='Comment', is_deleted=deleted)
            StatementComment.objects.filter(pk=comment.pk).update(created_at=START)
        after = StatementComment.objects.create(statement=own, user=self.user)
        StatementComment.objects.filter(pk=after.pk).update(created_at=CUTOFF)
        like = StatementLike.objects.create(statement=own, user=other)
        self_like = StatementLike.objects.create(statement=own, user=self.user)
        StatementLike.objects.filter(pk__in=[like.pk, self_like.pk]).update(created_at=START)
        result = summarize(self.user, now=CUTOFF)
        self.assertEqual(result['square'], {'posts': 1, 'comments': 2, 'received_likes': 1})
        self.assertNotIn('Personal post', str(result))

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
