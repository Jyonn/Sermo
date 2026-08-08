import json

from django.test import TestCase

from Friendship.models import Friendship
from Space.models import Space
from Square.models import Statement, StatementComment
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

    def test_verified_user_can_comment_on_visible_statement(self):
        statement = Statement.create_statement(self.author, '公开发言', 'public', [])
        created = self.client.post(
            f'/square/statements/{statement.id}/comments',
            data=json.dumps({'text': '第一条评论'}),
            content_type='application/json',
            **self.authorization(self.friend),
        )
        self.assertEqual(created.status_code, 200, created.content)
        self.assertEqual(created.json()['body']['text'], '第一条评论')

        comments = self.client.get(
            f'/square/statements/{statement.id}/comments?limit=30',
            **self.authorization(self.stranger),
        )
        self.assertEqual(comments.status_code, 200, comments.content)
        self.assertEqual(comments.json()['body'][0]['user']['user_id'], self.friend.id)

        feed = self.client.get('/square/statements?limit=20', **self.authorization(self.author))
        self.assertEqual(feed.json()['body'][0]['comment_count'], 1)

    def test_unverified_user_cannot_comment_or_read_hidden_statement_comments(self):
        public_statement = Statement.create_statement(self.author, '公开发言', 'public', [])
        denied = self.client.post(
            f'/square/statements/{public_statement.id}/comments',
            data=json.dumps({'text': '不能评论'}),
            content_type='application/json',
            **self.authorization(self.stranger),
        )
        self.assertEqual(denied.status_code, 403, denied.content)

        hidden_statement = Statement.objects.create(space=self.space, user=self.author, text='朋友可见', visibility=1)
        hidden = self.client.get(
            f'/square/statements/{hidden_statement.id}/comments?limit=30',
            **self.authorization(self.stranger),
        )
        self.assertEqual(hidden.status_code, 404, hidden.content)

    def test_statement_like_and_official_delete(self):
        statement = Statement.create_statement(self.author, '可互动', 'public', [])
        liked = self.client.post(f'/square/statements/{statement.id}/like', **self.authorization(self.friend))
        self.assertEqual(liked.status_code, 200, liked.content)
        self.assertEqual(liked.json()['body']['like_count'], 1)

        official = self.space.official_user or self.space.ensure_official_user()
        deleted = self.client.delete(f'/square/statements/{statement.id}', **self.authorization(official))
        self.assertEqual(deleted.status_code, 200, deleted.content)
        statement.refresh_from_db()
        self.assertTrue(statement.is_deleted)

    def test_comment_like_and_frequency_limit(self):
        statement = Statement.create_statement(self.author, '评论区', 'public', [])
        comment = StatementComment.create_comment(self.friend, statement.id, '值得点赞')
        liked = self.client.post(f'/square/comments/{comment.id}/like', **self.authorization(self.author))
        self.assertEqual(liked.status_code, 200, liked.content)
        self.assertEqual(liked.json()['body']['like_count'], 1)

        limited = self.post_statement(self.author, {'text': '当天第二条', 'visibility': 'public', 'media': []})
        self.assertEqual(limited.status_code, 403, limited.content)
        self.assertEqual(limited.json()['identifier'], 'SQUARE@DAILY_LIMIT_REACHED')

    def test_friends_feed_and_threaded_comments(self):
        public = Statement.create_statement(self.author, '好友动态', 'public', [])
        Statement.create_statement(self.friend, '朋友动态', 'public', [])
        reply = StatementComment.create_comment(self.friend, public.id, '一级评论')
        StatementComment.create_comment(self.author, public.id, '二级回复', parent_id=reply.id)

        feed = self.client.get('/square/statements?friends_only=1&limit=20', **self.authorization(self.author))
        self.assertEqual([item['text'] for item in feed.json()['body']], ['朋友动态', '好友动态'])
        self.assertEqual(feed.json()['body'][0]['user']['growth_level'], self.friend.growth_level)

        comments = self.client.get(f'/square/statements/{public.id}/comments?offset=0&limit=30', **self.authorization(self.author))
        body = comments.json()['body'][0]
        self.assertEqual(body['reply_count'], 1)
        self.assertEqual(body['replies'][0]['text'], '二级回复')

    def test_statement_rejects_mixed_media_types(self):
        response = self.post_statement(self.author, {
            'text': '混合媒体',
            'visibility': 'public',
            'media': [
                {'kind': 'image', 'key': 'sermo/messages/image/photo.jpg'},
                {'kind': 'audio', 'key': 'sermo/messages/audio/voice.m4a', 'duration_seconds': 3},
            ],
        })
        self.assertEqual(response.status_code, 400, response.content)
