import json

from django.test import TestCase

from Friendship.models import Friendship
from Space.models import Space
from Square.models import Statement
from User.models import User
from utils import auth


class StatementApiTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Square Test', slug='square-test', email='owner@example.com')
        self.author = User.create(
            space=self.space,
            name='Author',
            email='author@example.com',
            verified=True,
        )
        self.friend = User.create(
            space=self.space,
            name='Friend',
            email='friend@example.com',
            verified=True,
        )
        self.stranger = User.create(space=self.space, name='Reader')
        Friendship.ensure_locked_friendship(self.author, self.friend)

    @staticmethod
    def authorization(user):
        token = auth.get_login_token(user)['auth']
        return dict(HTTP_AUTHORIZATION=f'Bearer {token}')

    def post_statement(self, user, payload):
        return self.client.post(
            '/square/statements',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(user),
        )

    def test_unverified_user_can_read_but_cannot_publish(self):
        Statement.create_statement(self.author, '公开发言', 'public', [])

        feed = self.client.get('/square/statements?limit=20', **self.authorization(self.stranger))
        self.assertEqual(feed.status_code, 200, feed.content)
        self.assertEqual(feed.json()['body'][0]['text'], '公开发言')

        denied = self.post_statement(self.stranger, {'text': '不能发布', 'visibility': 'public', 'media': []})
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(denied.json()['identifier'], 'SQUARE@PUBLISH_REQUIRES_VERIFICATION')

    def test_friends_only_statement_is_filtered_by_relationship(self):
        Statement.create_statement(self.author, '朋友可见', 'friends', [])

        friend_feed = self.client.get('/square/statements?limit=20', **self.authorization(self.friend))
        stranger_feed = self.client.get('/square/statements?limit=20', **self.authorization(self.stranger))

        self.assertEqual(len(friend_feed.json()['body']), 1)
        self.assertEqual(stranger_feed.json()['body'], [])

    def test_statement_supports_nine_ordered_photos_and_location(self):
        media = [
            {
                'kind': 'image',
                'key': f'sermo/messages/image/photo-{index}.jpg',
                'mime_type': 'image/jpeg',
                'location': {'latitude': 24.48, 'longitude': 118.08, 'address': '厦门'},
            }
            for index in range(9)
        ]
        response = self.post_statement(self.author, {'text': '九张图', 'visibility': 'public', 'media': media})

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()['body']
        self.assertEqual(len(body['media']), 9)
        self.assertEqual(body['media'][0]['location']['address'], '厦门')

    def test_statement_rejects_more_than_nine_photos_and_long_audio(self):
        photos = [
            {'kind': 'image', 'key': f'sermo/messages/image/photo-{index}.jpg'}
            for index in range(10)
        ]
        too_many = self.post_statement(self.author, {'text': '', 'visibility': 'public', 'media': photos})
        self.assertEqual(too_many.status_code, 400, too_many.content)

        long_audio = self.post_statement(self.author, {
            'text': '',
            'visibility': 'public',
            'media': [{
                'kind': 'audio',
                'key': 'sermo/messages/audio/voice.m4a',
                'duration_seconds': 61,
            }],
        })
        self.assertEqual(long_audio.status_code, 400, long_audio.content)
