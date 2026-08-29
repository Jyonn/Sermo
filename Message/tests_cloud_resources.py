from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import MediaAsset, MediaResource, Message, MessageTypeChoice
from Message.management.commands.backfill_media_asset_hashes import Command as BackfillCommand
from Message.validators import MessageErrors
from Space.models import Space
from User.models import User
from utils import auth


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
            status=MediaAsset.STATUS_READY,
            file_size=1024,
            content_hash='a' * 64,
        )
        values.update(overrides)
        return MediaAsset.objects.create(**values)

    def create_resource(self, asset=None, owner=None, kind=None, file_name='report.pdf'):
        asset = asset or self.create_asset()
        return MediaResource.acquire(owner or self.user, asset, kind if kind is not None else asset.kind, file_name)

    def authorization(self):
        return dict(HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.user)['auth']}")

    def test_resource_list_excludes_assets_without_visible_message_references(self):
        available = self.create_resource()
        unavailable_asset = self.create_asset(
            source_key='sermo/messages/file/unavailable.pdf',
            source_uri='https://example.com/sermo/messages/file/unavailable.pdf',
            content_hash='b' * 64,
        )
        self.create_resource(asset=unavailable_asset, file_name='unavailable.pdf')
        message = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=available)

        response = self.client.get('/messages/resources?resource_kind=file', **self.authorization())

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item['resource_id'] for item in response.json()['body']['items']], [available.id])

        message.remove()
        response = self.client.get('/messages/resources?resource_kind=file', **self.authorization())
        self.assertEqual(response.json()['body']['items'], [])

    def test_resource_list_uses_asset_first_upload_time(self):
        older_asset = self.create_asset(content_hash='c' * 64)
        newer_asset = self.create_asset(
            source_key='sermo/messages/file/newer.pdf',
            source_uri='https://example.com/sermo/messages/file/newer.pdf',
            content_hash='d' * 64,
        )
        older = self.create_resource(asset=older_asset, file_name='older.pdf')
        newer = self.create_resource(asset=newer_asset, file_name='newer.pdf')
        older_message = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=older)
        newer_message = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=newer)
        older_time = timezone.now() - timedelta(days=4)
        newer_time = timezone.now() - timedelta(days=1)
        Message.objects.filter(id=older_message.id).update(created_at=older_time)
        Message.objects.filter(id=newer_message.id).update(created_at=newer_time)
        MediaAsset.objects.filter(id=older_asset.id).update(created_at=older_time)
        MediaAsset.objects.filter(id=newer_asset.id).update(created_at=newer_time)

        response = self.client.get('/messages/resources?resource_kind=file', **self.authorization())

        items = response.json()['body']['items']
        self.assertEqual([item['resource_id'] for item in items], [newer.id, older.id])
        self.assertEqual(items[1]['created_at'], older_time.timestamp())

    def test_resource_list_supports_offset_pagination(self):
        resources = []
        now = timezone.now()
        for index in range(3):
            asset = self.create_asset(
                source_key=f'sermo/messages/file/page-{index}.pdf',
                source_uri=f'https://example.com/sermo/messages/file/page-{index}.pdf',
                content_hash=str(index + 1) * 64,
            )
            resource = self.create_resource(asset=asset, file_name=f'page-{index}.pdf')
            Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=resource)
            MediaAsset.objects.filter(id=asset.id).update(created_at=now - timedelta(days=index))
            resources.append(resource)

        first = self.client.get('/messages/resources?kind=file&offset=0&limit=2', **self.authorization()).json()['body']
        second = self.client.get('/messages/resources?kind=file&offset=2&limit=2', **self.authorization()).json()['body']

        self.assertEqual([item['resource_id'] for item in first['items']], [resource.id for resource in resources[:2]])
        self.assertEqual(first['total_count'], 3)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['next_offset'], 2)
        self.assertEqual([item['resource_id'] for item in second['items']], [resources[2].id])
        self.assertFalse(second['has_more'])
        self.assertEqual(second['next_offset'], 3)

    def test_resource_list_searches_file_names(self):
        report = self.create_resource(file_name='quarterly-report.pdf')
        notes_asset = self.create_asset(
            source_key='sermo/messages/file/notes.txt',
            source_uri='https://example.com/sermo/messages/file/notes.txt',
            content_hash='9' * 64,
        )
        notes = self.create_resource(asset=notes_asset, file_name='meeting-notes.txt')
        Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=report)
        Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=notes)

        response = self.client.get('/messages/resources?kind=file&keyword=REPORT', **self.authorization())

        self.assertEqual(
            [item['resource_id'] for item in response.json()['body']['items']],
            [report.id],
        )

    def test_image_resource_includes_shared_media_metadata(self):
        asset = self.create_asset(
            source_key='sermo/messages/image/camera.jpg',
            source_uri='https://example.com/sermo/messages/image/camera.jpg',
            kind=MediaAsset.KIND_IMAGE,
            content_hash='f' * 64,
            make='FUJIFILM',
            model='X100VI',
            lens_model='23mm F2',
            taken_at=timezone.now() - timedelta(days=2),
            address='Shanghai',
            geocoding_status=MediaAsset.GEOCODING_READY,
        )
        resource = self.create_resource(asset=asset, kind=MediaAsset.KIND_IMAGE, file_name='camera.jpg')
        with patch.object(User, 'require_capability', return_value=None):
            Message.create(self.chat, self.user, MessageTypeChoice.IMAGE, '', media_resource=resource)

        response = self.client.get('/messages/resources?resource_kind=image', **self.authorization())

        metadata = response.json()['body']['items'][0]['metadata']
        self.assertEqual(metadata['make'], 'FUJIFILM')
        self.assertEqual(metadata['model'], 'X100VI')
        self.assertEqual(metadata['lens_model'], '23mm F2')
        self.assertEqual(metadata['address'], 'Shanghai')

    def test_backfill_media_asset_created_at_uses_earliest_message(self):
        asset = self.create_asset(content_hash='e' * 64)
        resource = self.create_resource(asset=asset)
        first = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=resource)
        second = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=resource)
        first_time = timezone.now() - timedelta(days=10)
        second_time = timezone.now() - timedelta(days=3)
        Message.objects.filter(id=first.id).update(created_at=first_time)
        Message.objects.filter(id=second.id).update(created_at=second_time)

        call_command('backfill_media_asset_created_at', stdout=StringIO())

        asset.refresh_from_db()
        self.assertEqual(asset.created_at, first_time)

    def test_hash_reuses_physical_asset_globally(self):
        asset = self.create_asset()
        self.assertEqual(MediaAsset.find_duplicate('a' * 64, 1024), asset)

    def test_one_asset_can_keep_multiple_user_filenames(self):
        asset = self.create_asset()
        first = self.create_resource(asset=asset, file_name='report.pdf')
        second = self.create_resource(asset=asset, file_name='renamed.pdf')
        peer = self.create_resource(asset=asset, owner=self.peer, file_name='peer-copy.pdf')
        self.assertEqual({first.asset_id, second.asset_id, peer.asset_id}, {asset.id})
        self.assertEqual(MediaResource.objects.count(), 3)

    def test_quota_counts_video_and_files_but_not_images(self):
        file_asset = self.create_asset()
        image_asset = self.create_asset(
            source_key='sermo/messages/image/photo.jpg',
            source_uri='https://example.com/sermo/messages/image/photo.jpg',
            kind=MediaAsset.KIND_IMAGE,
            file_size=9_999_999,
            content_hash=None,
        )
        self.create_resource(asset=file_asset)
        self.create_resource(asset=file_asset, file_name='same-content.pdf')
        self.create_resource(asset=image_asset, kind=MediaAsset.KIND_IMAGE, file_name='photo.jpg')
        self.assertEqual(MediaResource.quota_for(self.user)['used'], 1024)

    def test_message_references_asset_without_duplicate_content(self):
        asset = self.create_asset()
        resource = self.create_resource(asset=asset)
        message = Message.create(
            self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=resource,
        )
        self.assertEqual(message.content, '')
        self.assertEqual(message.media_resource_id, resource.id)
        self.assertEqual(message._payload_for_type()['file_name'], 'report.pdf')
        resource.refresh_from_db()
        self.assertEqual(resource.reference_count, 1)

    def test_capacity_rejects_more_than_remaining_space(self):
        asset = self.create_asset(file_size=MediaResource.STORAGE_LIMIT_BYTES)
        self.create_resource(asset=asset)
        with self.assertRaises(Exception) as context:
            MediaResource.require_capacity(self.user, 1)
        self.assertEqual(context.exception, MessageErrors.MEDIA_STORAGE_EXCEEDED)

    def test_pending_media_can_be_sent_while_metadata_is_loading(self):
        asset = self.create_asset(
            source_key='sermo/messages/image/loading.jpg',
            source_uri='https://example.com/sermo/messages/image/loading.jpg',
            kind=MediaAsset.KIND_IMAGE,
            status=MediaAsset.STATUS_PENDING,
            content_hash=None,
        )
        resource = self.create_resource(asset=asset, kind=MediaAsset.KIND_IMAGE, file_name='loading.jpg')
        with patch.object(User, 'require_capability', return_value=None):
            message = Message.create(self.chat, self.user, MessageTypeChoice.IMAGE, '', media_resource=resource)
        self.assertEqual(message.media_resource_id, resource.id)

    def test_forward_and_recall_keep_reference_count_in_sync(self):
        asset = self.create_asset()
        resource = self.create_resource(asset=asset)
        source = Message.create(self.chat, self.user, MessageTypeChoice.FILE, '', media_resource=resource)
        forwarded = Message.forward_individual(source, self.chat, self.peer)
        peer_resource = forwarded.media_resource
        self.assertEqual(peer_resource.asset_id, asset.id)
        resource.refresh_from_db()
        peer_resource.refresh_from_db()
        self.assertEqual(resource.reference_count, 1)
        self.assertEqual(peer_resource.reference_count, 1)
        forwarded.remove()
        peer_resource.refresh_from_db()
        self.assertEqual(peer_resource.reference_count, 0)

    @patch('Message.management.commands.backfill_media_asset_hashes.delete_message_media_file')
    def test_backfill_merges_global_assets_and_preserves_names(self, delete_file):
        canonical = self.create_asset()
        duplicate = self.create_asset(
            source_key='sermo/messages/file/duplicate.pdf',
            source_uri='https://example.com/sermo/messages/file/duplicate.pdf',
            content_hash=None,
        )
        first = self.create_resource(asset=canonical, file_name='first.pdf')
        second = self.create_resource(asset=duplicate, owner=self.peer, file_name='second.pdf')
        BackfillCommand._merge_asset(duplicate, canonical)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.asset_id, canonical.id)
        self.assertEqual(second.asset_id, canonical.id)
        self.assertEqual({first.file_name, second.file_name}, {'first.pdf', 'second.pdf'})
        self.assertFalse(MediaAsset.objects.filter(source_key='sermo/messages/file/duplicate.pdf').exists())
        delete_file.assert_called_once()

    @patch.object(BackfillCommand, '_digest', return_value=('b' * 64, 2048, 'application/pdf'))
    def test_backfill_includes_empty_hash_images_and_fills_file_details(self, digest):
        asset = self.create_asset(
            source_key='sermo/messages/image/empty.jpg',
            source_uri='https://example.com/sermo/messages/image/empty.jpg',
            kind=MediaAsset.KIND_IMAGE,
            content_hash=None,
            file_size=None,
            mime_type='',
        )
        BackfillCommand().handle(limit=0, force=False, dry_run=False)
        asset.refresh_from_db()
        self.assertEqual(asset.content_hash, 'b' * 64)
        self.assertEqual(asset.file_size, 2048)
        self.assertEqual(asset.mime_type, 'application/pdf')
        digest.assert_called_once()
