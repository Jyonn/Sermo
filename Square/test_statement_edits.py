from django.test import TestCase
from django.utils import timezone

from Friendship.models import Friendship
from Space.models import Space
from Square.models import Statement, StatementLike, StatementVisibilityChoice
from User.models import User
from utils import auth


class StatementEditTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Square', slug='square-edit', email='square@example.com',
            group_square_enabled=True, admin_phone_verified_at=timezone.now(),
        )
        self.author = User.create(self.space, 'Author', verified=True)
        self.reader = User.create(self.space, 'Reader', verified=True)
        self.post = Statement.objects.create(space=self.space, user=self.author, text='Original')
        self.url = f'/square/statements/{self.post.pk}'

    def headers(self, user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user)['auth']}

    def edit(self, text, revision=0, user=None):
        return self.client.patch(self.url, data={'text': text, 'revision': revision},
                                 content_type='application/json', **self.headers(user or self.author))

    def history(self, user):
        return self.client.get(self.url + '/history', **self.headers(user))

    def test_updates_are_versioned_without_republishing_and_sync_to_other_viewers(self):
        StatementLike.objects.create(statement=self.post, user=self.reader)
        response = self.edit(' Second version ')
        self.assertEqual(response.status_code, 200, response.content)
        current = response.json()['body']
        self.assertEqual(current['text'], 'Second version')
        self.assertEqual(current['edit_revision'], 1)
        self.assertTrue(current['can_edit'])
        self.assertEqual(current['created_at'], self.post.created_at.timestamp())
        self.assertEqual(current['like_count'], 1)
        self.assertEqual(self.edit('Duplicate save', 0).status_code, 409)
        self.assertEqual(self.edit('Second version', 1).status_code, 200)
        self.assertEqual(self.edit('Third version', 1).status_code, 200)
        result = self.history(self.reader).json()['body']
        self.assertEqual([(row['revision'], row['text']) for row in result['items']],
                         [(2, 'Third version'), (1, 'Second version'), (0, 'Original')])
        sync = self.client.post('/square/statements/sync', data={'statement_ids': [self.post.pk]},
                                content_type='application/json', **self.headers(self.reader)).json()['body']['items'][0]
        self.assertFalse(sync['can_edit'])
        self.assertEqual((sync['text'], sync['edit_revision']), ('Third version', 2))

    def test_history_requires_current_visibility_and_only_author_can_save(self):
        official = self.space.official_user or self.space.ensure_official_user()
        self.assertEqual(self.edit('Other member', user=self.reader).status_code, 403)
        self.assertEqual(self.edit('Administrator', user=official).status_code, 403)
        self.assertEqual(self.edit(' ').status_code, 400)
        self.assertEqual(self.edit('a' * 141).status_code, 400)
        self.post.visibility = StatementVisibilityChoice.FRIENDS
        self.post.is_anonymous = False
        self.post.save(update_fields=['visibility', 'is_anonymous'])
        self.assertEqual(self.edit('Private').status_code, 200)
        self.assertEqual(self.history(self.reader).status_code, 404)
        Friendship.ensure_locked_friendship(self.reader, self.author)
        self.assertEqual(self.history(self.reader).status_code, 200)
        self.assertEqual(self.client.delete(self.url, **self.headers(self.author)).status_code, 200)
        self.assertEqual(self.history(self.reader).status_code, 404)
        self.assertEqual(self.edit('Revive', 1).status_code, 404)
