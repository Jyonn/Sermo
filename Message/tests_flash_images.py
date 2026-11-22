import json
from base64 import b64decode
from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
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
        self.cloud = {}
        self.store = self.enterContext(patch('Message.flash_images.store_photo', side_effect=self.store_fixture))
        self.read = self.enterContext(patch('Message.flash_images.read_photo', side_effect=lambda key: self.cloud[key]))

    def store_fixture(self, data, chat_id, user_id, client_id):
        key = f'fixture/{chat_id}/{user_id}/{client_id}.jpg'
        self.cloud[key] = data
        return key

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

    def reveal(self, user, message_id, position=None):
        url = f'/messages/flash-image/reveal?message_id={message_id}'
        if position is not None:
            url += f'&position={position}'
        return self.client.post(url, **self.headers(user))

    def forward(self, user, message_ids, targets, mode):
        return self.client.post('/messages/forward',
            json.dumps({'message_ids': message_ids, 'target_chat_ids': targets, 'mode': mode}),
            content_type='application/json', **self.headers(user))

    def test_private_once_only_delivery_and_retry(self):
        sent = self.send()
        message_id = sent['message_id']
        self.assertEqual(sent['type'], MessageTypeChoice.FLASH_IMAGE)
        self.assertTrue(sent['payload']['flash_available'])
        self.assertEqual(sent['payload']['flash_viewed_count'], 0)
        self.assertEqual(sent['payload']['flash_recipient_count'], 1)
        self.assertNotIn('uri', sent['payload'])
        message = Message.objects.get(pk=message_id)
        self.assertIsNone(message.media_resource_id)
        self.assertEqual(message.content, '')
        thumbnail = FlashImage.objects.get(message_id=message_id).thumbnail
        self.assertGreater(len(thumbnail), Message.vldt.MAX_CONTENT_LENGTH)
        self.assertEqual(sent['payload']['flash_thumbnail'], thumbnail)
        with Image.open(BytesIO(b64decode(thumbnail))) as preview:
            self.assertLessEqual(max(preview.size), 10)
        self.assertEqual(self.send()['message_id'], message_id)
        self.assertEqual(self.reveal(self.outsider, message_id).status_code, 404)
        self.assertEqual(self.reveal(self.sender, message_id).status_code, 200)
        self.assertEqual(self.reveal(self.sender, message_id).status_code, 200)
        self.read.reset_mock()
        self.read.assert_not_called()
        peer_view = self.client.get(f'/messages/?chat_id={self.chat.pk}&limit=5', **self.headers(self.peer)).json()['body'][0]
        self.assertTrue(peer_view['payload']['flash_available'])
        self.assertEqual(peer_view['payload']['flash_thumbnail'], thumbnail)
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
        sender_events = MessageEvent.sync_for_user(
            self.sender, cursor, 10, request=SimpleNamespace(user=self.sender)
        )['events']
        self.assertEqual(sender_events[0]['message']['payload']['flash_viewed_count'], 1)
        self.assertEqual(MessageEvent.sync_for_user(self.outsider, cursor, 10)['events'], [])
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 410)
        peer_after = self.client.get(f'/messages/?chat_id={self.chat.pk}&limit=5', **self.headers(self.peer)).json()['body'][0]
        self.assertIsNone(peer_after['payload']['flash_thumbnail'])
        self.assertTrue(peer_after['payload']['flash_viewed'])
        retained = FlashImage.objects.get(message_id=message_id)
        self.assertEqual(retained.pending_viewers, [])
        self.assertEqual(bytes(retained.pixels), b'')
        self.assertEqual(self.cloud[retained.storage_key], revealed.content)
        self.assertEqual(self.send()['message_id'], message_id)
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 410)
        self.store.assert_called_once()
        self.read.assert_called_once()
        # Receipts recorded before the viewed_users column remain visible.
        FlashImage.objects.filter(message_id=message_id).update(viewed_users=None)
        owner = Message.objects.get(pk=message_id)._payload_for_type(SimpleNamespace(user=self.sender))
        self.assertEqual(owner['flash_viewed_count'], 1)

    def test_expired_and_forwarding_are_blocked(self):
        message_id = self.send()['message_id']
        for mode in ('individual', 'bundle'):
            response = self.forward(self.peer, [message_id], [self.target.pk], mode)
            self.assertEqual(response.status_code, 400, response.content)
        FlashImage.objects.filter(message_id=message_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 410)
        self.assertIn(FlashImage.objects.get(message_id=message_id).storage_key, self.cloud)
        self.read.assert_not_called()
        self.assertEqual(self.reveal(self.sender, message_id).status_code, 200)

    def test_sender_can_forward_without_reusing_view_permissions(self):
        message_id = self.send()['message_id']
        source = FlashImage.objects.get(message_id=message_id)
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 200)
        Friendship.ensure_locked_friendship(self.sender, self.outsider)
        new_chat = Chat.get_or_create_direct(self.sender, self.outsider)
        sent = self.forward(self.sender, [message_id], [new_chat.pk], 'individual')
        self.assertEqual(sent.status_code, 200, sent.content)
        copy = sent.json()['body']['messages'][0]['message']
        self.assertEqual(copy['payload']['flash_viewed_count'], 0)
        self.assertEqual(FlashImage.objects.get(message_id=copy['message_id']).storage_key, source.storage_key)
        self.assertEqual(self.reveal(self.peer, copy['message_id']).status_code, 404)
        self.assertEqual(self.reveal(self.outsider, copy['message_id']).status_code, 200)
        self.assertEqual(self.reveal(self.outsider, copy['message_id']).status_code, 410)

    def test_bundle_deliveries_keep_separate_receipts_and_authorization(self):
        message_id = self.send()['message_id']
        Friendship.ensure_locked_friendship(self.sender, self.outsider)
        new_chat = Chat.get_or_create_direct(self.sender, self.outsider)
        response = self.forward(self.sender, [message_id], [self.chat.pk, new_chat.pk], 'bundle')
        self.assertEqual(response.status_code, 200, response.content)
        entries = {entry['chat_id']: entry['message'] for entry in response.json()['body']['messages']}
        first = entries[self.chat.pk]
        second = entries[new_chat.pk]
        self.assertEqual(first['payload']['bundle_id'], second['payload']['bundle_id'])
        for message in (first, second):
            self.assertEqual(message['payload']['delivery_message_id'], message['message_id'])
            self.assertEqual(message['payload']['items'][0]['payload']['flash_message_id'], message['message_id'])
        self.assertEqual(self.reveal(self.peer, second['message_id'], 0).status_code, 404)
        self.assertEqual(self.reveal(self.peer, first['message_id'], 1).status_code, 410)
        self.assertEqual(self.reveal(self.peer, first['message_id'], 0).status_code, 200)
        self.assertEqual(self.reveal(self.peer, first['message_id'], 0).status_code, 410)
        self.assertEqual(self.reveal(self.outsider, second['message_id'], 0).status_code, 200)
        self.assertEqual(self.reveal(self.sender, first['message_id'], 0).status_code, 200)
        original = FlashImage.objects.get(message_id=message_id)
        self.assertEqual(original.pending_viewers, [self.peer.pk])
        self.store.assert_called_once()

    def test_storage_failure_does_not_consume_view(self):
        message_id = self.send()['message_id']
        self.read.side_effect = ValueError('temporary read failure')
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 503)
        self.assertEqual(FlashImage.objects.get(message_id=message_id).pending_viewers, [self.peer.pk])
        self.read.side_effect = lambda key: self.cloud[key]
        self.assertEqual(self.reveal(self.peer, message_id).status_code, 200)
