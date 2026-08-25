import json
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Friendship.models import Friendship
from Space.models import Space
from Message.models import MediaAsset
from Square.models import Statement, StatementComment, StatementCommentLike, StatementLike, StatementMedia
from User.models import NotificationEvent, NotificationEventTypeChoice, User
from utils import auth


class StatementApiTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Square Test',
            slug='square-test',
            email='owner@example.com',
            group_square_enabled=True,
            admin_phone_verified_at=timezone.now(),
        )
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

    def test_pinned_statement_returns_empty_success_when_none_exists(self):
        response = self.client.get('/square/statements/pinned', **self.authorization(self.stranger))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()['body'])

    def test_pinned_statement_returns_empty_success_when_reference_is_stale(self):
        self.author.pinned_square_statement_id = 999999
        self.author.save(update_fields=['pinned_square_statement_id'])
        self.space.official_user = self.author
        self.space.save(update_fields=['official_user'])

        response = self.client.get('/square/statements/pinned', **self.authorization(self.stranger))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json()['body'])

    def test_unverified_user_can_read_but_cannot_publish(self):
        Statement.create_statement(self.author, '公开发言', 'public', [])

        feed = self.client.get('/square/statements?limit=20', **self.authorization(self.stranger))
        self.assertEqual(feed.status_code, 200, feed.content)
        self.assertEqual(feed.json()['body'][0]['text'], '公开发言')

        denied = self.post_statement(self.stranger, {'text': '不能发布', 'visibility': 'public', 'media': []})
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(denied.json()['identifier'], 'ACCESSPOLICY@CAPABILITY_DENIED')

    def test_friends_only_statement_is_filtered_by_relationship(self):
        Statement.create_statement(self.author, '朋友可见', 'friends', [])

        friend_feed = self.client.get('/square/statements?limit=20', **self.authorization(self.friend))
        stranger_feed = self.client.get('/square/statements?limit=20', **self.authorization(self.stranger))

        self.assertEqual(len(friend_feed.json()['body']), 1)
        self.assertEqual(stranger_feed.json()['body'], [])

    def test_admin_feed_includes_friends_only_statement(self):
        Statement.create_statement(self.author, '朋友可见', 'friends', [])
        response = self.client.get(
            '/square/admin/statements?limit=20',
            HTTP_AUTHORIZATION=f"Bearer {auth.get_space_login_token(self.space)['auth']}",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body'][0]['text'], '朋友可见')

    def test_square_status_tracks_new_feed_items_without_counting_own_posts(self):
        baseline = self.client.get('/square/status', **self.authorization(self.friend))
        self.assertFalse(baseline.json()['body']['explore_unread'])
        self.assertFalse(baseline.json()['body']['friends_unread'])

        Statement.create_statement(self.author, '新朋友发言', 'public', [])
        updated = self.client.get('/square/status', **self.authorization(self.friend)).json()['body']
        self.assertTrue(updated['explore_unread'])
        self.assertTrue(updated['friends_unread'])

        marked = self.client.post(
            '/square/status',
            data=json.dumps({'scope': 'friends'}),
            content_type='application/json',
            **self.authorization(self.friend),
        ).json()['body']
        self.assertTrue(marked['explore_unread'])
        self.assertFalse(marked['friends_unread'])

        Statement.create_statement(self.friend, '自己的发言', 'public', [])
        own = self.client.get('/square/status', **self.authorization(self.friend)).json()['body']
        self.assertFalse(own['friends_unread'])

    def test_square_notification_feed_can_start_with_unread_and_page_history(self):
        old = NotificationEvent.objects.create(
            space=self.space,
            user=self.friend,
            actor=self.author,
            event_type=NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE,
            payload={'statement_id': 1},
            is_read=True,
        )
        unread = NotificationEvent.objects.create(
            space=self.space,
            user=self.friend,
            actor=self.author,
            event_type=NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT,
            payload={'statement_id': 2},
        )

        first = self.client.get(
            '/users/me/notification-events?category=square&unread_only=1&limit=30',
            **self.authorization(self.friend),
        ).json()['body']
        self.assertEqual([row['notification_event_id'] for row in first['events']], [unread.id])

        history = self.client.get(
            f'/users/me/notification-events?category=square&before={unread.id}&limit=30',
            **self.authorization(self.friend),
        ).json()['body']
        self.assertEqual([row['notification_event_id'] for row in history['events']], [old.id])

    def test_statement_supports_nine_ordered_photos_without_user_location(self):
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
        self.assertIsNone(body['media'][0]['location'])

    @patch('Message.image_metadata.reverse_geocode', return_value=('福建省厦门市思明区', 'opencage'))
    def test_statement_stores_location_independently_from_media(self, reverse_geocode):
        response = self.post_statement(self.author, {
            'text': '带位置的发言',
            'visibility': 'public',
            'media': [],
            'location': {'latitude': 24.4798, 'longitude': 118.0894},
        })

        self.assertEqual(response.status_code, 200, response.content)
        location = response.json()['body']['location']
        self.assertEqual(location['address'], '福建省厦门市思明区')
        self.assertEqual(location['geocoding_provider'], 'opencage')
        self.assertAlmostEqual(location['latitude'], 24.4798)
        reverse_geocode.assert_called_once_with(24.4798, 118.0894)

    def test_statement_rejects_invalid_location(self):
        response = self.post_statement(self.author, {
            'text': '错误位置',
            'visibility': 'public',
            'media': [],
            'location': {'latitude': 91, 'longitude': 118},
        })

        self.assertEqual(response.status_code, 400, response.content)

    def test_statements_can_share_media_asset(self):
        asset = MediaAsset.objects.create(
            source_key='sermo/messages/image/reused.jpg',
            source_uri='https://resource.example.com/sermo/messages/image/reused.jpg',
            kind=MediaAsset.KIND_IMAGE,
            status=MediaAsset.STATUS_READY,
        )
        first = Statement.objects.create(space=self.space, user=self.author, text='第一条')
        second = Statement.objects.create(space=self.space, user=self.author, text='第二条')
        StatementMedia.objects.create(
            statement=first, media_asset=asset, position=0,
        )
        StatementMedia.objects.create(
            statement=second, media_asset=asset, position=0,
        )

        self.assertEqual(asset.statement_media_items.count(), 2)

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

    def test_comment_delete_permissions_and_thread_cleanup(self):
        statement = Statement.create_statement(self.author, '评论治理', 'public', [])
        root = StatementComment.create_comment(self.friend, statement.id, '一级评论')
        StatementComment.create_comment(self.author, statement.id, '二级回复', parent_id=root.id)

        forbidden = self.client.delete(f'/square/comments/{root.id}', **self.authorization(self.stranger))
        self.assertEqual(forbidden.status_code, 403, forbidden.content)

        deleted = self.client.delete(f'/square/comments/{root.id}', **self.authorization(self.author))
        self.assertEqual(deleted.status_code, 200, deleted.content)
        self.assertEqual(deleted.json()['body']['deleted_count'], 2)
        self.assertTrue(deleted.json()['body']['root_deleted'])
        self.assertEqual(StatementComment.objects.filter(statement=statement, is_deleted=False).count(), 0)

    def test_quota_reports_current_rolling_usage(self):
        Statement.create_statement(self.author, '今日发言', 'public', [])
        statement = Statement.objects.get(user=self.author)
        StatementComment.create_comment(self.author, statement.id, '今日评论')
        StatementLike.objects.create(statement=statement, user=self.author)

        response = self.client.get('/square/quota', **self.authorization(self.author))
        self.assertEqual(response.status_code, 200, response.content)
        quota = response.json()['body']
        self.assertEqual(quota['statements']['daily_used'], 1)
        self.assertEqual(quota['statements']['daily_limit'], 1)
        self.assertEqual(quota['comments']['daily_used'], 1)
        self.assertEqual(quota['comments']['daily_limit'], 5)
        self.assertEqual(quota['likes']['daily_used'], 1)
        self.assertFalse(quota['media']['audio'])

    def test_permanent_vip_uses_level_18_frequency_limits(self):
        self.author.is_permanent_vip = True
        self.author.save(update_fields=['is_permanent_vip'])

        response = self.client.get('/square/quota', **self.authorization(self.author))

        self.assertEqual(response.status_code, 200, response.content)
        quota = response.json()['body']
        self.assertTrue(quota['vip'])
        self.assertEqual(quota['statements']['daily_limit'], 5)
        self.assertEqual(quota['statements']['weekly_limit'], 35)
        self.assertEqual(quota['comments']['daily_limit'], 25)
        self.assertEqual(quota['comments']['weekly_limit'], 175)

    def test_friends_feed_and_threaded_comments(self):
        public = Statement.create_statement(self.author, '好友动态', 'public', [])
        Statement.create_statement(self.friend, '朋友动态', 'public', [])
        reply = StatementComment.create_comment(self.friend, public.id, '一级评论')
        StatementComment.create_comment(self.author, public.id, '二级回复', parent_id=reply.id)

        feed = self.client.get('/square/statements?scope=friends&limit=20', **self.authorization(self.author))
        self.assertEqual([item['text'] for item in feed.json()['body']], ['朋友动态', '好友动态'])
        self.assertEqual(feed.json()['body'][0]['user']['growth_level'], self.friend.growth_level)

        mine = self.client.get('/square/statements?scope=mine&limit=20', **self.authorization(self.author))
        self.assertEqual([item['text'] for item in mine.json()['body']], ['好友动态'])

        comments = self.client.get(f'/square/statements/{public.id}/comments?offset=0&limit=30', **self.authorization(self.author))
        body = comments.json()['body'][0]
        self.assertEqual(body['reply_count'], 1)
        self.assertEqual(body['replies'][0]['text'], '二级回复')

    def test_reply_to_nested_comment_is_flattened_into_second_level(self):
        statement = Statement.create_statement(self.author, '两级评论', 'public', [])
        root = StatementComment.create_comment(self.friend, statement.id, '一级评论')
        reply = StatementComment.create_comment(self.author, statement.id, '回复一级', parent_id=root.id)
        nested_reply = StatementComment.create_comment(self.friend, statement.id, '回复二级', parent_id=reply.id)

        comments = StatementComment.feed(self.author, statement.id)

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]['reply_count'], 2)
        self.assertEqual([item['comment_id'] for item in comments[0]['replies']], [reply.id, nested_reply.id])
        self.assertEqual(comments[0]['replies'][1]['parent_id'], reply.id)
        self.assertEqual(comments[0]['replies'][1]['root_id'], root.id)
        self.assertEqual(comments[0]['replies'][1]['reply_to_user']['user_id'], self.author.id)
        self.assertNotIn('replies', comments[0]['replies'][1])

    def test_square_exploration_growth_is_awarded_once(self):
        response = self.post_statement(self.author, {
            'text': '好友圈图片发言',
            'visibility': 'friends',
            'media': [{'kind': 'image', 'key': 'sermo/messages/image/growth.jpg'}],
        })
        self.assertEqual(response.status_code, 200, response.content)
        statement_id = response.json()['body']['statement_id']
        root = StatementComment.create_comment(self.friend, statement_id, '评论')
        StatementComment.create_comment(self.author, statement_id, '回复', parent_id=root.id)

        self.client.post(f'/square/statements/{statement_id}/like', **self.authorization(self.friend))
        self.client.post(f'/square/statements/{statement_id}/like', **self.authorization(self.friend))
        self.client.post(f'/square/comments/{root.id}/like', **self.authorization(self.author))

        self.assertTrue(self.author.growth_events.filter(event_key='explore:square_statement').exists())
        self.assertTrue(self.author.growth_events.filter(event_key='explore:square_friends').exists())
        self.assertTrue(self.author.growth_events.filter(event_key='explore:square_image').exists())
        self.assertTrue(self.author.growth_events.filter(event_key='explore:square_reply').exists())
        self.assertTrue(self.friend.growth_events.filter(event_key='explore:square_comment').exists())
        self.assertEqual(self.friend.growth_events.filter(event_key='explore:square_like').count(), 1)
        self.assertTrue(self.author.growth_events.filter(event_key='explore:square_comment_like').exists())

    def test_comments_can_switch_between_hot_and_latest_order(self):
        statement = Statement.create_statement(self.author, '评论排序', 'public', [])
        older = StatementComment.create_comment(self.friend, statement.id, '较早热门')
        newer = StatementComment.create_comment(self.author, statement.id, '较新评论')
        StatementCommentLike.objects.create(comment=older, user=self.author)

        hot = StatementComment.feed(self.author, statement.id, sort='hot')
        latest = StatementComment.feed(self.author, statement.id, sort='latest')

        self.assertEqual([item['comment_id'] for item in hot], [older.id, newer.id])
        self.assertEqual([item['comment_id'] for item in latest], [newer.id, older.id])

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
