from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import MediaAsset, Message, MessageTypeChoice
from Message.validators import MessageErrors
from Space.models import Space
from User.models import User


class CloudResourceTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Cloud', slug='cloud', email='admin@example.com', admin_phone_verified_at=timezone.now(),
        )
        self.user = User.create(self.space, 'Sender', email='sender@example.com', verified=True)
        self.peer = User.create(self.space, 'Peer', email='peer@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        self.chat = Chat.get_or_create_direct(self.user, self.peer)

    def create_asset(self, **overrides):
        values = dict(
            source_key='sermo/messages/file/report.pdf',
            source_uri='https://example.com/sermo/messages/file/report.pdf',
            kind=MediaAsset.KIND_FILE,
            owner=self.user,
            status=MediaAsset.STATUS_READY,
            file_name='report.pdf',
            file_size=1024,
            content_hash='a' * 64,
        )
        values.update(overrides)
        return MediaAsset.objects.create(**values)

    def test_hash_reuses_owned_resource(self):
        asset = self.create_asset()
        self.assertEqual(MediaAsset.find_duplicate(self.user, MediaAsset.KIND_FILE, 'a' * 64, 1024), asset)
        self.assertIsNone(MediaAsset.find_duplicate(self.peer, MediaAsset.KIND_FILE, 'a' * 64, 1024))

    def test_quota_counts_video_and_files_but_not_images(self):
        self.create_asset()
        self.create_asset(
            source_key='sermo/messages/image/photo.jpg',
            source_uri='https://example.com/sermo/messages/image/photo.jpg',
            kind=MediaAsset.KIND_IMAGE,
            file_size=9_999_999,
            content_hash='',
        )
        self.assertEqual(MediaAsset.quota_for(self.user)['used'], 1024)

    def test_message_references_asset_without_duplicate_content(self):
        asset = self.create_asset()
        message = Message.create(
            self.chat, self.user, MessageTypeChoice.FILE, '', media_asset=asset,
        )
        self.assertEqual(message.content, '')
        self.assertEqual(message.media_asset_id, asset.id)
        self.assertEqual(message._payload_for_type()['file_name'], 'report.pdf')
        asset.refresh_from_db()
        self.assertEqual(asset.reference_count, 1)

    def test_capacity_rejects_more_than_remaining_space(self):
        self.create_asset(file_size=MediaAsset.STORAGE_LIMIT_BYTES)
        with self.assertRaises(Exception) as context:
            MediaAsset.require_capacity(self.user, 1)
        self.assertEqual(context.exception, MessageErrors.MEDIA_STORAGE_EXCEEDED)

    def test_pending_media_can_be_sent_while_metadata_is_loading(self):
        asset = self.create_asset(
            source_key='sermo/messages/image/loading.jpg',
            source_uri='https://example.com/sermo/messages/image/loading.jpg',
            kind=MediaAsset.KIND_IMAGE,
            status=MediaAsset.STATUS_PENDING,
            file_name='loading.jpg',
        )
        with patch.object(User, 'require_capability', return_value=None):
            message = Message.create(self.chat, self.user, MessageTypeChoice.IMAGE, '', media_asset=asset)
        self.assertEqual(message.media_asset_id, asset.id)

    def test_forward_and_recall_keep_reference_count_in_sync(self):
        asset = self.create_asset()
        source = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_asset=asset)
        forwarded = Message.forward_individual(source, self.chat, self.peer)
        asset.refresh_from_db()
        self.assertEqual(asset.reference_count, 2)
        forwarded.remove()
        asset.refresh_from_db()
        self.assertEqual(asset.reference_count, 1)
