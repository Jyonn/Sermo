import io
import json
from unittest.mock import patch, Mock
from PIL import Image
from django.test import TestCase, RequestFactory
from Sticker.models import ManagedResource, ResourceCollection
from Sticker.resources import publish_resource, image_variants, upload_object, ResourceAdminView, ResourceAdminAssetView
from Sticker.services import delete_unreferenced_sticker_asset
from Sticker.validators import StickerErrors
from utils.qiniu import (build_avatar_key, build_chat_background_key, build_message_media_key,
                         validate_avatar_key, validate_message_media_key, validate_chat_background_key,
                         key_from_avatar_uri, key_from_chat_background_uri)


class ResourceTests(TestCase):
    def setUp(self):
        self.collection = ResourceCollection.objects.create(slug='haibara-v2', title='小哀', version='v2', published=True)
        image = Image.new('RGBA', (400, 400), (255, 50, 20, 128))
        output = io.BytesIO(); image.save(output, 'PNG'); self.data = output.getvalue()

    def resource(self, slug='joy'):
        return publish_resource(self.collection, slug, '开心', 'Joy', self.data, upload=lambda *args:None)

    def test_new_uploads_use_frienden_and_old_keys_still_read(self):
        self.assertTrue(build_avatar_key('photo.png').startswith('frienden/avatar/'))
        self.assertTrue(build_chat_background_key('wall.webp').startswith('frienden/chat-background/'))
        self.assertTrue(build_message_media_key('image', 'photo.png').startswith('frienden/messages/image/'))
        for root in ('sermo', 'frienden'):
            avatar=f'{root}/avatar/old.png'; background=f'{root}/chat-background/old.webp'
            self.assertEqual(validate_avatar_key(avatar), avatar)
            self.assertEqual(key_from_avatar_uri(f'https://example.test/{avatar}'), avatar)
            self.assertEqual(validate_chat_background_key(background),background)
            self.assertEqual(key_from_chat_background_uri(f'https://example.test/{background}'),background)
            key=f'{root}/messages/image/{"a"*32}.png'
            self.assertEqual(validate_message_media_key('image',key),key)
        for key in ('another-app/avatar/file.png','frienden/avatar/../file.png'):
            with self.assertRaises(Exception): validate_avatar_key(key)

    def test_derivatives_keep_alpha_and_limit_dimensions(self):
        variants=image_variants(self.data,'emoji')
        for variant,limit in [('display',384),('thumbnail',96)]:
            with Image.open(io.BytesIO(variants[variant])) as image:
                self.assertLessEqual(max(image.size),limit)
                self.assertEqual(image.mode,'RGBA')
        for invalid in (b'<svg onload="bad"/>',b'not an image',b'x'*(10*1024*1024+1)):
            with self.assertRaises(StickerErrors.INVALID_IMAGE.__class__): image_variants(invalid,'emoji')

    def test_import_is_idempotent_and_shared_stickers_are_protected(self):
        row=self.resource();again=self.resource()
        self.assertEqual(row.id,again.id)
        self.assertEqual(ManagedResource.objects.count(),1)
        self.assertTrue(row.storage_key.startswith('frienden/assets/emojis/haibara-v2/v2/'))
        self.assertFalse(delete_unreferenced_sticker_asset(row.sticker_asset))
        other=ResourceCollection.objects.create(slug='second',title='Second')
        copy=publish_resource(other,'joy','Joy','Joy',self.data,upload=lambda *args:None)
        self.assertEqual(row.sticker_asset_id,copy.sticker_asset_id)

    def test_public_catalog_hides_disabled_resources_but_old_message_files_remain(self):
        row=self.resource()
        result=self.client.get('/resources/collections/haibara-v2')
        self.assertEqual(result.status_code,200)
        self.assertEqual(len(result.json()['body']['assets']),1)
        row.enabled=False;row.save()
        self.assertEqual(len(self.client.get('/resources/collections/haibara-v2').json()['body']['assets']),0)
        with patch('Sticker.resources.avatar_uri_for_key',return_value='https://assets.example.test/file.webp'),patch('Sticker.resources.sign_private_download_url',side_effect=lambda uri:uri):
            old=self.client.get(f'/resources/assets/{row.id}/display/{row.content_hash}')
        self.assertEqual(old.status_code,302)
        self.collection.published=False;self.collection.save()
        self.assertNotEqual(self.client.get('/resources/collections/haibara-v2').status_code,200)

    def test_admin_mutations_require_platform_token_and_reject_namespace_injection(self):
        for path in ('/platform-admin/resources','/platform-admin/resources/upload','/platform-admin/resources/1'):
            self.assertNotEqual(self.client.post(path,data='{}',content_type='application/json').status_code,200)
        request=RequestFactory().post('/platform-admin/resources',data=json.dumps({'slug':'../other-app','title':'Bad'}),content_type='application/json',HTTP_AUTHORIZATION='Bearer fixture')
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.test'}):
            with self.assertRaises(StickerErrors.INVALID_HASH.__class__):ResourceAdminView.as_view()(request)

    def test_admin_state_changes_are_audited_and_not_destructive(self):
        from PlatformAdmin.models import PlatformAuditLog
        row=self.resource()
        request=RequestFactory().post('/platform-admin/resources/1',data=json.dumps({'enabled':False,'position':9}),content_type='application/json',HTTP_AUTHORIZATION='Bearer fixture')
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.test'}):ResourceAdminAssetView.as_view()(request,asset_id=row.id)
        row.refresh_from_db();self.assertFalse(row.enabled);self.assertEqual(row.position,9)
        self.assertTrue(row.sticker_asset_id)
        self.assertEqual(PlatformAuditLog.objects.filter(action='resource.asset.update').count(),1)

    def test_insert_only_upload_checks_existing_bytes_and_never_accepts_other_app_keys(self):
        key=f'frienden/assets/test/v1/{"a"*64}.webp'
        with self.assertRaises(StickerErrors.INVALID_HASH.__class__):upload_object('other-app/assets/file.webp',b'file','image/webp')
        with patch('Sticker.resources._required_config',return_value='fixture'),patch('Sticker.resources.put_data',return_value=(None,Mock(status_code=614))),patch('Sticker.resources.BucketManager') as manager:
            manager.return_value.stat.return_value=({'hash':'wrong','fsize':4},Mock(status_code=200))
            with self.assertRaises(StickerErrors.DOWNLOAD_FAILED.__class__):upload_object(key,b'file','image/webp')
