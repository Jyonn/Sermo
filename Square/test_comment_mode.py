from django.test import TestCase
from django.utils import timezone

from Friendship.models import Friendship, FriendshipStatusChoice
from Space.models import Space
from Square.models import Statement, StatementComment, StatementVisibilityChoice
from User.models import NotificationEvent, NotificationEventTypeChoice, User
from utils import auth


class SquareCommentModeTests(TestCase):
    def setUp(self):
        space = Space.objects.create(
            name='Square', slug='square-comment-mode', email='square@example.com',
            group_square_enabled=True, admin_phone_verified_at=timezone.now(),
        )
        self.author = User.create(space, 'Author', verified=True)
        self.reader = User.create(space, 'Reader', verified=True)
        self.readers_friend = User.create(space, 'ReaderFriend', verified=True)
        self.authors_friend = User.create(space, 'AuthorFriend', verified=True)
        self.friendship = Friendship.ensure_locked_friendship(self.reader, self.readers_friend)
        Friendship.ensure_locked_friendship(self.author, self.authors_friend)
        self.post = Statement.objects.create(space=space, user=self.author, text='An existing post')
        self.url = f'/square/statements/{self.post.id}'
        self.hidden = self.comment(self.authors_friend, 'Author friend')
        self.visible_reply = self.comment(self.readers_friend, 'Reader friend', parent=self.hidden)
        self.own_reply = self.comment(self.reader, 'Own reply', parent=self.visible_reply)

    def comment(self, user, text, parent=None):
        return StatementComment.objects.create(
            statement=self.post, user=user, text=text, parent=parent,
            reply_to_user=parent.user if parent else None,
        )

    def headers(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user)['auth']}

    def mode(self, mode, user=None):
        return self.client.post('/users/me/square-comment-mode', {'mode': mode},
                                content_type='application/json', **self.headers(user or self.author))

    def comments(self, user):
        response = self.client.get(self.url + '/comments', **self.headers(user))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()['body']

    def test_switches_existing_posts_and_keeps_each_readers_own_and_friends_comments(self):
        public, = self.comments(self.reader)
        self.assertEqual(public['comment_id'], self.hidden.id)
        self.assertEqual([row['comment_id'] for row in public['replies']], [self.visible_reply.id, self.own_reply.id])
        self.assertEqual(self.mode('friends').json()['body'], {'square_comment_mode': 'friends'})
        me = self.client.get('/users/me', **self.headers(self.author)).json()['body']
        self.assertEqual(me['square_comment_mode'], 'friends')
        self.assertEqual(self.mode('invalid').status_code, 400)
        root, = self.comments(self.reader)
        self.assertEqual(root['comment_id'], self.visible_reply.id)
        self.assertIsNone(root['parent_id'])
        self.assertIsNone(root['reply_to_user'])
        self.assertEqual(root['reply_count'], 1)
        reply, = root['replies']
        self.assertEqual(reply['comment_id'], self.own_reply.id)
        self.assertEqual(reply['root_id'], self.visible_reply.id)
        self.assertEqual([row['comment_id'] for row in self.comments(self.author)], [self.hidden.id])
        self.friendship.status = FriendshipStatusChoice.DELETED
        self.friendship.save(update_fields=['status'])
        own, = self.comments(self.reader)
        self.assertEqual(own['comment_id'], self.own_reply.id)
        self.assertIsNone(own['reply_to_user'])
        self.assertEqual(self.mode('public').status_code, 200)
        self.assertEqual(self.comments(self.reader)[0]['reply_count'], 2)

    def test_counts_are_viewer_specific_and_post_audience_is_independent(self):
        self.mode('friends')
        headers = self.headers(self.reader)
        feed = self.client.get('/square/statements', **headers).json()['body']
        self.assertEqual(feed[0]['comment_count'], 2)
        self.assertEqual(self.client.get(self.url, **headers).json()['body']['comment_count'], 2)
        sync = self.client.post('/square/statements/sync', {'statement_ids': [self.post.id]},
                                content_type='application/json', **headers).json()['body']
        self.assertEqual(sync['items'][0]['comment_count'], 2)
        self.post.visibility = StatementVisibilityChoice.FRIENDS
        self.post.save(update_fields=['visibility'])
        self.assertEqual(self.client.get(self.url, **headers).status_code, 404)
        self.assertEqual(self.client.get(self.url + '/comments', **headers).status_code, 404)

    def test_hidden_comments_cannot_be_liked_replied_to_edited_or_deleted(self):
        self.mode('friends')
        headers = self.headers(self.reader)
        path = f'/square/comments/{self.hidden.id}'
        self.assertEqual(self.client.post(path + '/like', **headers).status_code, 404)
        self.assertEqual(self.client.delete(path + '/like', **headers).status_code, 404)
        self.assertEqual(self.client.post(self.url + '/comments', {'text': 'Reply', 'parent_id': self.hidden.id},
                                          content_type='application/json', **headers).status_code, 404)
        self.assertEqual(self.client.patch(path, {'text': 'Edit', 'revision': 0},
                                           content_type='application/json', **headers).status_code, 404)
        self.assertEqual(self.client.delete(path, **headers).status_code, 404)
        root_path = f'/square/comments/{self.visible_reply.id}'
        edit = self.client.patch(root_path, {'text': 'Edited', 'revision': 0},
                                 content_type='application/json', **self.headers(self.readers_friend))
        self.assertEqual(edit.status_code, 200, edit.content)
        self.assertIsNone(edit.json()['body']['parent_id'])

    def test_comment_notifications_follow_current_permission(self):
        kind = NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT
        event = NotificationEvent.emit_square_event(
            self.author, self.reader, kind, self.post.id, self.own_reply.id,
        )
        visible = NotificationEvent.emit_square_event(
            self.author, self.authors_friend, kind, self.post.id, self.hidden.id,
        )
        self.mode('friends')
        self.assertIsNone(NotificationEvent.emit_square_event(
            self.author, self.reader, kind, self.post.id, self.own_reply.id,
        ))
        endpoint = '/users/me/notification-events?category=square'
        body = self.client.get(endpoint, **self.headers(self.author)).json()['body']
        self.assertEqual([row['notification_event_id'] for row in body['events']], [visible.id])
        self.assertEqual(body['unread_count'], 1)
        self.assertEqual(NotificationEvent.mark_square_events_read(self.author), (1, 0))
        event.refresh_from_db()
        self.assertFalse(event.is_read)
