import json

from django.test import TestCase
from django.utils import timezone

from Chat.models import Chat
from Friendship.models import Friendship
from Message.models import ForwardBundleItem, MediaAsset, Message, MessageTypeChoice
from Space.models import Space
from User.models import User
from utils import auth


class MessageForwardingTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Forwarding', slug='forwarding', email='admin@example.com', admin_phone_verified_at=timezone.now(),
        )
        self.user = User.create(self.space, 'Sender', email='sender@example.com', verified=True)
        self.peer = User.create(self.space, 'Peer', email='peer@example.com', verified=True)
        self.target_peer = User.create(self.space, 'Target', email='target@example.com', verified=True)
        Friendship.ensure_locked_friendship(self.user, self.peer)
        Friendship.ensure_locked_friendship(self.user, self.target_peer)
        self.source_chat = Chat.get_or_create_direct(self.user, self.peer)
        self.target_chat = Chat.get_or_create_direct(self.user, self.target_peer)

    def authorization(self):
        return dict(HTTP_AUTHORIZATION=f"Bearer {auth.get_login_token(self.user)['auth']}")

    def post_forward(self, message_ids, mode):
        return self.client.post(
            '/messages/forward',
            data=json.dumps({
                'message_ids': message_ids,
                'target_chat_ids': [self.target_chat.id],
                'mode': mode,
            }),
            content_type='application/json',
            **self.authorization(),
        )

    def test_individual_forward_reuses_media_asset(self):
        asset = MediaAsset.objects.create(
            source_key='sermo/messages/image/source.jpg',
            source_uri='https://example.com/sermo/messages/image/source.jpg',
            kind=MediaAsset.KIND_IMAGE,
            status=MediaAsset.STATUS_READY,
        )
        source = Message.objects.create(
            chat=self.source_chat,
            user=self.peer,
            type=MessageTypeChoice.IMAGE,
            content=json.dumps({'kind': 'image', 'uri': asset.source_uri}),
            media_asset=asset,
        )

        response = self.post_forward([source.id], 'individual')

        self.assertEqual(response.status_code, 200, response.content)
        forwarded = Message.objects.get(chat=self.target_chat, type=MessageTypeChoice.IMAGE)
        self.assertEqual(forwarded.media_asset_id, asset.id)
        source.remove()
        self.assertFalse(forwarded.is_deleted)
        self.assertEqual(forwarded.media_asset_id, asset.id)

    def test_bundle_keeps_snapshot_after_original_is_recalled(self):
        first = Message.create(self.source_chat, self.peer, MessageTypeChoice.TEXT, 'First line')
        second = Message.create(self.source_chat, self.user, MessageTypeChoice.TEXT, 'Second line')

        response = self.post_forward([first.id, second.id], 'bundle')

        self.assertEqual(response.status_code, 200, response.content)
        forwarded = Message.objects.get(chat=self.target_chat, type=MessageTypeChoice.FORWARD_BUNDLE)
        self.assertEqual(ForwardBundleItem.objects.filter(bundle=forwarded.forward_bundle).count(), 2)
        first.remove()
        payload = forwarded._payload_for_type()
        self.assertEqual([item['content'] for item in payload['items']], ['First line', 'Second line'])
