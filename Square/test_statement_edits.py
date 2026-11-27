from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from Friendship.models import Friendship
from Message.models import ForwardBundle, LinkPreview, MediaAsset
from Space.models import Space
from Square.models import Statement, StatementLike, StatementMedia, StatementVisibilityChoice
from Square.statement_edits import issue_edit_upload_grant
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

    def edit(self, text, revision=0, user=None, **content):
        body = {'text': text, 'revision': revision}
        if content:
            body['content'] = content
        return self.client.patch(self.url, data=body,
                                 content_type='application/json', **self.headers(user or self.author))

    def history(self, user, before=None):
        with patch('utils.qiniu._required_config', return_value='test-history-signing-key'):
            return self.client.get(self.url + '/history', data={} if before is None else {'before': before},
                                   **self.headers(user))

    def add_photo(self, post, suffix, position=0):
        asset = MediaAsset.objects.create(source_key=f'sermo/messages/image/{suffix * 32}.jpg',
                                          source_uri=f'https://cdn.example.test/{suffix}.jpg', kind=MediaAsset.KIND_IMAGE)
        return StatementMedia.objects.create(statement=post, media_asset=asset, position=position)

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

    def test_keeps_selected_media_and_updates_location_audience_and_sync(self):
        removed = self.add_photo(self.post, 'a')
        kept = self.add_photo(self.post, 'b', 1)
        location = dict(latitude=30.29, longitude=120.13, address='Library')
        edited = self.edit('', media=[{'media_id': kept.id}], visibility='friends', location=location)
        self.assertEqual(edited.status_code, 200, edited.content)
        current = edited.json()['body']
        self.assertEqual(current['created_at'], self.post.created_at.timestamp())
        self.assertEqual(current['visibility'], 'friends')
        self.assertEqual(current['location'], {**location, 'geocoding_provider': ''})
        self.assertEqual([row['media_id'] for row in current['media']], [kept.id])
        self.assertFalse(StatementMedia.objects.filter(pk=removed.id).exists())
        self.assertTrue(MediaAsset.objects.filter(pk=removed.media_asset_id).exists())
        self.assertEqual(Statement.objects.filter(user=self.author).count(), 1)
        self.assertEqual(set(self.history(self.author).json()['body']['items'][1]['changes']),
                         {'text', 'location', 'visibility', 'media'})
        current_version, original_version = self.history(self.author).json()['body']['items']
        self.assertEqual(current_version['location']['address'], 'Library')
        self.assertEqual(current_version['visibility'], 'friends')
        self.assertEqual(original_version['visibility'], 'public')
        self.assertIsNone(original_version['location'])
        self.assertEqual([row['media_id'] for row in original_version['media']], [removed.pk, kept.pk])
        self.assertIn('https://cdn.example.test/a.jpg', original_version['media'][0]['uri'])
        self.assertIn('&token=', original_version['media'][0]['uri'])
        self.assertEqual(original_version['unavailable_fields'], [])
        self.assertNotIn('https://', str(self.post.revisions.get(revision=0).snapshot))

        def sync(user, revision):
            return self.client.post(
                '/square/statements/sync',
                data={'statement_ids': [self.post.pk], 'known_revisions': {str(self.post.pk): revision}},
                content_type='application/json', **self.headers(user),
            ).json()['body']

        self.assertEqual(sync(self.reader, 0)['removed_ids'], [self.post.pk])
        Friendship.ensure_locked_friendship(self.reader, self.author)
        result = sync(self.reader, 0)['items'][0]
        self.assertEqual(result['edit_revision'], 1)
        self.assertEqual(result['location']['address'], 'Library')
        self.assertEqual(result['media'][0]['media_id'], kept.id)
        self.assertNotIn('media', sync(self.reader, 1)['items'][0])
        self.assertEqual(self.edit('Only text', 1, media=[], location=None).status_code, 200)
        self.assertFalse(self.post.media.exists())
        self.post.refresh_from_db()
        self.assertIsNone(self.post.location_payload)

    def test_originally_friends_only_text_stays_private_when_a_post_becomes_public(self):
        self.post.visibility = StatementVisibilityChoice.FRIENDS
        self.post.save(update_fields=['visibility'])
        self.assertEqual(self.edit('Second private version').status_code, 200)
        self.post.revisions.update(snapshot=None)  # Old app only stored text.
        self.assertEqual(self.edit('Now public', 1, visibility='public').status_code, 200)
        self.assertEqual([row['text'] for row in self.history(self.reader).json()['body']['items']], ['Now public'])
        self.assertEqual(len(self.history(self.author).json()['body']['items']), 3)
        Friendship.ensure_locked_friendship(self.reader, self.author)
        self.assertEqual(len(self.history(self.reader).json()['body']['items']), 3)

    def test_legacy_history_recovers_unchanged_fields_across_pages(self):
        photo = self.add_photo(self.post, 'e')
        self.assertEqual(self.edit('Second').status_code, 200)
        self.post.revisions.update(snapshot={'visibility': 'public', 'changes': ['text']})
        self.assertEqual(self.edit('Third', 1).status_code, 200)
        original, = self.history(self.author, before=1).json()['body']['items']
        self.assertEqual(original['revision'], 0)
        self.assertEqual([item['media_id'] for item in original['media']], [photo.pk])
        self.assertEqual(original['unavailable_fields'], [])

        self.assertEqual(self.edit('Fourth', 2, location={'latitude': 30.3, 'longitude': 120.1}).status_code, 200)
        self.post.revisions.filter(revision=2).update(snapshot={'visibility': 'public', 'changes': ['text', 'location']})
        original, = self.history(self.author, before=1).json()['body']['items']
        # The full second snapshot still supplies the earlier location.
        self.assertIsNone(original['location'])
        self.assertEqual(original['unavailable_fields'], [])
        self.post.revisions.filter(revision=1).update(snapshot={'visibility': 'public', 'changes': ['text']})
        original, = self.history(self.author, before=1).json()['body']['items']
        self.assertIsNone(original['location'])
        self.assertEqual(original['unavailable_fields'], ['location'])
        self.assertEqual([item['media_id'] for item in original['media']], [photo.pk])

    def test_history_does_not_expose_attachments_from_friends_only_versions(self):
        self.post.visibility = StatementVisibilityChoice.FRIENDS
        self.post.save(update_fields=['visibility'])
        photo = self.add_photo(self.post, 'f')
        self.assertEqual(self.edit('Public', media=[], visibility='public').status_code, 200)
        public, = self.history(self.reader).json()['body']['items']
        self.assertEqual(public['media'], [])
        self.assertEqual(public['revision'], 1)
        Friendship.ensure_locked_friendship(self.reader, self.author)
        original = self.history(self.reader).json()['body']['items'][1]
        self.assertEqual([item['media_id'] for item in original['media']], [photo.pk])

    def test_new_media_requires_an_upload_bound_to_the_author(self):
        other_post = Statement.objects.create(space=self.space, user=self.reader, text='Someone else’s photo')
        other_media = self.add_photo(other_post, 'c')
        self.assertEqual(self.edit('New', media=[{'media_id': other_media.id}]).status_code, 400)
        key = 'sermo/messages/image/' + 'd' * 32 + '.jpg'
        media = {'kind': 'image', 'key': key, 'mime_type': 'image/jpeg'}
        wrong = issue_edit_upload_grant(self.reader, key, 'image')
        self.assertEqual(self.edit('New', media=[{**media, 'edit_upload_grant': wrong}]).status_code, 400)
        with patch('Square.views.issue_message_upload', return_value={'key': key, 'kind': 'image'}):
            upload = self.client.post('/square/upload', data={'kind': 'image', 'file_name': 'photo.jpg'},
                                      content_type='application/json', **self.headers(self.author))
        self.assertEqual(upload.status_code, 200, upload.content)
        valid = {**media, 'edit_upload_grant': upload.json()['body']['edit_upload_grant']}
        self.assertEqual(self.edit('New', media=[valid], external_media_url='https://example.test').status_code, 400)
        edited = self.edit('New', media=[valid])
        self.assertEqual(edited.status_code, 200, edited.content)
        self.assertEqual(self.post.media.get().media_asset.source_key, key)
        self.assertNotIn('edit_upload_grant', edited.content.decode())

    def test_replaces_or_removes_shared_content_without_republishing(self):
        self.post.forward_bundle = ForwardBundle.objects.create(created_by=self.author)
        self.post.chat_record_redacted = True
        self.post.save(update_fields=['forward_bundle', 'chat_record_redacted'])
        url = 'https://example.test/song'
        link = LinkPreview.objects.create(url=url, url_hash=LinkPreview.hash_url(url))
        with patch('Square.statement_edits.LinkPreview.queue_for_text', return_value=link) as resolve:
            self.assertEqual(self.edit('', external_media_url=url).status_code, 400)
            saved = self.edit('', keep_chat_record=False, external_media_url=url)
            self.assertEqual(saved.status_code, 200, saved.content)
            self.assertEqual(saved.json()['body']['external_media']['url'], url)
            self.assertIsNone(saved.json()['body']['chat_record'])
            self.assertEqual(self.edit('', 1, external_media_url=url).json()['body']['edit_revision'], 1)
            resolve.assert_called_once_with(url, requester=self.author, initial=True)
        self.assertEqual(self.edit('', 1, external_media_url=None).status_code, 400)
        self.assertEqual(self.edit('No link', 1, external_media_url=None).status_code, 200)
        newest, shared_link, original = self.history(self.author).json()['body']['items']
        self.assertIsNone(newest['external_media'])
        self.assertEqual(shared_link['external_media']['url'], url)
        self.assertTrue(original['chat_record']['redacted_identity'])
        self.assertIsNotNone(original['chat_record']['bundle_id'])
        self.post.refresh_from_db()
        self.assertIsNone(self.post.link_preview_id)
        self.assertFalse(self.post.chat_record_redacted)

    def test_anonymous_posts_keep_their_published_identity(self):
        self.post.is_anonymous = True
        self.post.save(update_fields=['is_anonymous'])
        self.assertEqual(self.edit('Private attempt', visibility='friends').status_code, 400)
        result = self.edit('Still anonymous', visibility='public').json()['body']
        self.assertTrue(result['is_anonymous'])
        self.assertEqual(result['user']['user_id'], 0)

    def test_attachment_only_edit_preserves_long_legacy_text(self):
        original = '旧' * 160
        self.post.text = original
        self.post.save(update_fields=['text'])
        response = self.edit(None, visibility='friends')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['text'], original)
