import json
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from Config.models import Config, ConfigInstance
from Space.models import Space
from Sticker.models import StickerAsset, UserSticker
from Message.models import Message, MessageTypeChoice, WelcomeMessageTemplate
from User.models import User
from utils import auth


class AdminStickerLibraryTests(TestCase):
    def setUp(self):
        Config.objects.create(key=ConfigInstance.QINIU_DOMAIN, value='https://resource.example.test')
        self.space = Space.objects.create(name='Sticker space', slug='sticker-space', email='admin@example.test')
        self.other = Space.objects.create(name='Other space', slug='other-stickers', email='other@example.test')
        self.official = self.space.ensure_official_user()
        self.other_official = self.other.ensure_official_user()
        self.member = User.create(self.space, 'Member', verified=True)
        self.assets = [StickerAsset.objects.create(content_hash=str(index)*64, storage_key=f'sermo/messages/sticker/{index}.webp',
            pixel_width=120, pixel_height=120, dimensions_checked_at=timezone.now()) for index in range(1,4)]
        for user, asset in zip([self.official,self.member,self.other_official],self.assets):
            UserSticker.objects.create(user=user,asset=asset)

    def headers(self, space=None):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_space_login_token(space or self.space)['auth']}"}

    def test_only_space_admin_can_read_the_official_library(self):
        self.assertNotEqual(self.client.get('/spaces/admin/stickers').status_code, 200)
        user_headers={'HTTP_AUTHORIZATION':f"Bearer {auth.get_login_token(self.member)['auth']}"}
        self.assertNotEqual(self.client.get('/spaces/admin/stickers',**user_headers).status_code,200)
        response=self.client.get('/spaces/admin/stickers', {'user_id':self.member.id}, **self.headers())
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual([row['sticker_asset_id'] for row in response.json()['body']['items']],[self.assets[0].id])
        response=self.client.get('/spaces/admin/stickers',**self.headers(self.other))
        self.assertEqual([row['sticker_asset_id'] for row in response.json()['body']['items']],[self.assets[2].id])

    def test_admin_explore_uses_the_same_library_policy_and_pagination(self):
        admin=self.client.get('/spaces/admin/stickers/explore?limit=1',**self.headers())
        user=self.client.get('/stickers/explore?limit=1',HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.official)['auth']}")
        self.assertEqual(admin.status_code,200,admin.content)
        self.assertEqual(admin.json()['body'],user.json()['body'])
        self.assertTrue(admin.json()['body']['has_more'])
        self.assertNotEqual(self.client.get('/spaces/admin/stickers?limit=1000',**self.headers()).status_code,200)

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_broadcast_and_welcome_preserve_stickers_as_stickers(self, _enqueue):
        content=json.dumps({'asset_id':self.assets[0].id})
        response=self.client.post('/spaces/admin/broadcast',data=json.dumps({'type':9,'content':content,'broadcast_id':'test-sticker-broadcast'}),content_type='application/json',**self.headers())
        self.assertEqual(response.status_code,200,response.content)
        message=Message.objects.get(type=MessageTypeChoice.STICKER,user=self.official)
        self.assertEqual(json.loads(message.content)['asset_id'],self.assets[0].id)
        WelcomeMessageTemplate.replace_for(self.official,[{'type':9,'content':content}])
        self.assertEqual(self.official.welcome_message_templates.get().type,MessageTypeChoice.STICKER)
