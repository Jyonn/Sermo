import json
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import FlashImage, Message, MessageEvent, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth


class FlashImageTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Photos', slug='photos', email='admin@example.test',
                                     admin_phone_verified_at=timezone.now())
        self.sender = User.create(space, 'Sender', verified=True)
        self.peer = User.create(space, 'Peer', verified=True)
        self.outsider = User.create(space, 'Outsider', verified=True)
        Friendship.ensure_locked_friendship(self.sender, self.peer)
        Friendship.ensure_locked_friendship(self.peer, self.outsider)
        self.chat = Chat.get_or_create_direct(self.sender, self.peer)
        self.target = Chat.get_or_create_direct(self.peer, self.outsider)

    def headers(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def send(self):
        stream = BytesIO()
        exif = Image.Exif()
        exif[0x010e] = 'private description'
        Image.new('RGB', (64, 40), '#9a6338').save(stream, 'JPEG', exif=exif)
        upload = SimpleUploadedFile('test.jpg', stream.getvalue(), content_type='image/jpeg')
        with patch.object(User, 'require_capability'):
            response = self.client.post(f'/messages/flash-image?chat_id={self.chat.pk}',
                {'image': upload, 'client_message_id': 'photo:fixture'}, **self.headers(self.sender))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['body']

    def reveal(self, user, message_id):
        return self.client.post(f'/messages/flash-image/reveal?message_id={message_id}', **self.headers(user))

    def test_private_once_only_delivery_and_retry(self):
        sent = self.send()
        message_id = sent['message_id']
        self.assertEqual(sent['type'], MessageTypeChoice.FLASH_IMAGE)
        self.assertFalse(sent['payload']['flash_available'])
        self.assertNotIn('uri', sent['payload'])
        self.assertIsNone(Message.objects.get(pk=message_id).media_resource_id)
        self.assertEqual(self.send()['message_id'], message_id)
        self.assertEqual(self.reveal(self.outsider, message_id).status_code, 404)
        self.assertEqual(self.reveal(self.sender, message_id).status_code, 410)
        peer_view = self.client.get(f'/messages/?chat_id={self.chat.pk}&limit=5', **self.headers(self.peer)).json()['body'][0]
        self.assertTrue(peer_view['payload']['flash_available'])
        self.assertNotIn('uri', peer_view['payload'])
        cursor = MessageEvent.sync_baseline_for_user(self.peer)['next_after']
        revealed = self.reveal(self.peer, message_id)
        self.assertEqual(revealed.status_code, 200, revealed.content[:80])
        self.assertIn('no-store', revealed['Cache-Control'])
        with Image.open(BytesIO(revealed.content)) as image:
            self.assertFalse(image.getexif())
        peer_events = MessageEvent.sync_for_user(self.peer, cursor, 10)['events']
        self.assertEqual([(event['type'], event['message_id']) for event in peer_events],
                         [('message.updated', message_id)])
        self.assertEqual(MessageEvent.sync_for_user(self.sender, cursor, 10)['events'], [])
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 410)
        self.assertFalse(FlashImage.objects.filter(message_id=message_id).exists())
        self.assertEqual(self.send()['message_id'], message_id)
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 410)

    def test_expired_and_forwarding_are_blocked(self):
        message_id = self.send()['message_id']
        for mode in ('individual', 'bundle'):
            response = self.client.post('/messages/forward',
                json.dumps({'message_ids': [message_id], 'target_chat_ids': [self.target.pk], 'mode': mode}),
                content_type='application/json', **self.headers(self.peer))
            self.assertEqual(response.status_code, 400, response.content)
        FlashImage.objects.filter(message_id=message_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 410)
        self.assertFalse(FlashImage.objects.filter(message_id=message_id).exists())
