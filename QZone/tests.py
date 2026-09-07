import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from Message.models import MediaAsset
from QZone.importer import QZoneImporter
from QZone.models import QZoneComment, QZoneEmoticon, QZoneMedia, QZonePost, QZoneUser
from Space.models import Space, SpaceFeatureGrant, SpaceFeatureKeyChoice
from Square.models import Statement, StatementComment, StatementMedia
from User.qq_identity import ensure_qzone_placeholder


class QZoneSourceModelTests(TestCase):
    def test_source_rows_keep_qq_relations_and_optional_square_projection(self):
        wall = QZoneUser.objects.create(qq='1493732945', nickname='江中东墙')
        commenter = QZoneUser.objects.create(qq='1275685745', nickname='林杨')
        post = QZonePost.objects.create(
            id=1,
            source_post_id='518e085989d48a54ac220100',
            author=wall,
            content_raw='hi 我是江中表白东墙',
            content_text='hi 我是江中表白东墙',
            published_at=timezone.now(),
        )
        source_comment = QZoneComment.objects.create(
            id=1,
            post=post,
            author=commenter,
            reply_to=wall,
            content_raw='你和西墙是一对嘛[em]e328524[/em]',
            published_at=timezone.now(),
        )
        space = Space.objects.create(name='江中东西墙', slug='jzdxq', email='wall@example.com')
        wall_identity = ensure_qzone_placeholder(space, wall.qq, wall.nickname)
        comment_identity = ensure_qzone_placeholder(space, commenter.qq, commenter.nickname)
        statement = Statement.objects.create(space=space, user=wall_identity.user, text=post.content_text)
        statement_comment = StatementComment.objects.create(
            statement=statement,
            user=comment_identity.user,
            text=source_comment.content_raw,
        )

        post.statement = statement
        post.save(update_fields=['statement'])
        source_comment.statement_comment = statement_comment
        source_comment.save(update_fields=['statement_comment'])

        self.assertEqual(post.author_id, wall.qq)
        self.assertEqual(source_comment.author_id, commenter.qq)
        self.assertEqual(source_comment.reply_to_id, wall.qq)
        self.assertEqual(statement.qzone_source.id, post.id)
        self.assertEqual(statement_comment.qzone_source.id, source_comment.id)


class QZoneImporterTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='江中东西墙', slug='jzdxq', email='wall@example.com')
        SpaceFeatureGrant.set_granted(
            self.space,
            SpaceFeatureKeyChoice.QQ_IDENTITY_BINDING,
            True,
            granted_by='platform@example.com',
        )
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.input_path = Path(self.temp_dir.name) / 'qzone-migration.json'

    @staticmethod
    def _row_dates():
        return {
            'created_at': '2026-09-07T04:59:39.374Z',
            'updated_at': '2026-09-07T04:59:39.374Z',
        }

    def _write(self, posts, comments, users=None):
        payload = {
            'qzone_user': users or [
                {'qq': '1493732945', 'nickname': '江中东墙'},
                {'qq': '377489624', 'nickname': 'ugly'},
            ],
            'qzone_post': posts,
            'qzone_comment': comments,
        }
        self.input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')

    def test_imports_source_identities_and_historical_projection(self):
        dates = self._row_dates()
        self._write(
            posts=[
                {
                    'id': 1,
                    'source_post_id': 'east-1',
                    'author_qq': '1493732945',
                    'content_raw': '历史说说',
                    'content_text': '历史说说',
                    'published_at': '2014-12-13 12:22:06',
                    'visibility': 'public',
                    'media': '[]',
                    'source_payload': '{}',
                    **dates,
                },
                {
                    'id': 2,
                    'source_post_id': 'east-private',
                    'author_qq': '1493732945',
                    'content_raw': '私密内容',
                    'content_text': '私密内容',
                    'published_at': '2014-12-14 12:22:06',
                    'visibility': 'private',
                    'media': '[]',
                    'source_payload': '{}',
                    **dates,
                },
            ],
            comments=[{
                'id': 1,
                'post_id': 1,
                'author_qq': '377489624',
                'parent_comment_id': '',
                'reply_to_qq': '1493732945',
                'content_raw': '@{uin:1493732945,nick:江中东墙,who:1,auto:1}你好',
                'published_at': '2014-12-13 13:00:00',
                'source_payload': '{}',
                **dates,
            }],
        )
        importer = QZoneImporter(self.space, self.input_path)

        importer.preflight()
        importer.import_source()
        importer.import_identities()
        result = importer.project()

        self.assertEqual(result, {'posts_created': 1, 'comments_created': 1})
        self.assertIsNotNone(QZonePost.objects.get(id=1).statement_id)
        self.assertIsNone(QZonePost.objects.get(id=2).statement_id)
        statement = Statement.objects.get(qzone_source__id=1)
        comment = StatementComment.objects.get(qzone_source__id=1)
        self.assertEqual(statement.created_at.year, 2014)
        self.assertEqual(comment.created_at.year, 2014)
        self.assertEqual(comment.text, '@江中东墙你好')
        self.assertEqual(comment.reply_to_user.qq_identity.qq, '1493732945')

        self.assertEqual(importer.project(), {'posts_created': 0, 'comments_created': 0})

    def test_source_limit_keeps_all_dependencies_for_selected_posts(self):
        dates = self._row_dates()
        self._write(
            posts=[
                {
                    'id': 1,
                    'source_post_id': 'east-1',
                    'author_qq': '1493732945',
                    'content_raw': '首条',
                    'content_text': '首条',
                    'published_at': '2014-12-13 12:22:06',
                    'visibility': 'public',
                    'media': '[]',
                    'source_payload': '{}',
                    **dates,
                },
                {
                    'id': 2,
                    'source_post_id': 'east-2',
                    'author_qq': '1493732945',
                    'content_raw': '第二条',
                    'content_text': '第二条',
                    'published_at': '2014-12-14 12:22:06',
                    'visibility': 'public',
                    'media': '[]',
                    'source_payload': '{}',
                    **dates,
                },
            ],
            comments=[
                {
                    'id': 1,
                    'post_id': 1,
                    'author_qq': '377489624',
                    'parent_comment_id': '',
                    'reply_to_qq': '',
                    'content_raw': '首条评论',
                    'published_at': '2014-12-13 13:00:00',
                    'source_payload': '{}',
                    **dates,
                },
                {
                    'id': 2,
                    'post_id': 2,
                    'author_qq': '377489624',
                    'parent_comment_id': '',
                    'reply_to_qq': '',
                    'content_raw': '第二条评论',
                    'published_at': '2014-12-14 13:00:00',
                    'source_payload': '{}',
                    **dates,
                },
            ],
        )

        result = QZoneImporter(self.space, self.input_path).import_source(limit=1)

        self.assertEqual(result, {'users': 2, 'posts': 1, 'comments': 1})
        self.assertEqual(list(QZonePost.objects.values_list('id', flat=True)), [1])
        self.assertEqual(list(QZoneComment.objects.values_list('id', flat=True)), [1])
        self.assertSetEqual(set(QZoneUser.objects.values_list('qq', flat=True)), {'1493732945', '377489624'})

    def test_source_import_preserves_comment_parents_and_skips_unchanged_updates(self):
        dates = self._row_dates()
        self._write(
            posts=[{
                'id': 1,
                'source_post_id': 'east-1',
                'author_qq': '1493732945',
                'content_raw': '历史说说',
                'content_text': '历史说说',
                'published_at': '2014-12-13 12:22:06',
                'visibility': 'public',
                'media': '[]',
                'source_payload': '{"source_file":"east.json"}',
                **dates,
            }],
            comments=[
                {
                    'id': 1,
                    'post_id': 1,
                    'author_qq': '377489624',
                    'parent_comment_id': '',
                    'reply_to_qq': '',
                    'content_raw': '首条评论',
                    'published_at': '2014-12-13 13:00:00',
                    'source_payload': '{}',
                    **dates,
                },
                {
                    'id': 2,
                    'post_id': 1,
                    'author_qq': '1493732945',
                    'parent_comment_id': 1,
                    'reply_to_qq': '377489624',
                    'content_raw': '回复评论',
                    'published_at': '2014-12-13 13:01:00',
                    'source_payload': '{}',
                    **dates,
                },
            ],
        )
        importer = QZoneImporter(self.space, self.input_path, batch_size=1)

        importer.import_source()

        self.assertEqual(QZoneComment.objects.get(id=2).parent_id, 1)
        with CaptureQueriesContext(connection) as queries:
            importer.import_source()
        source_updates = [
            query['sql'] for query in queries.captured_queries
            if query['sql'].lstrip().upper().startswith('UPDATE')
            and ('qzone_post' in query['sql'] or 'qzone_comment' in query['sql'])
        ]
        self.assertEqual(source_updates, [])

    def test_preflight_rejects_non_list_source_tables(self):
        self.input_path.write_text(json.dumps({
            'qzone_user': {},
            'qzone_post': [],
            'qzone_comment': [],
        }), encoding='utf-8')

        with self.assertRaisesMessage(CommandError, 'qzone_user must be a list.'):
            QZoneImporter(self.space, self.input_path).preflight()

    @patch('QZone.importer.put_file')
    def test_media_stage_reuses_existing_asset_by_content_hash(self, put_file_mock):
        media_directory = Path(self.temp_dir.name) / '江中东墙HTML' / 'Messages' / 'images'
        media_directory.mkdir(parents=True)
        media_path = media_directory / 'photo.jpeg'
        media_path.write_bytes(b'legacy-image')
        digest = hashlib.sha256(b'legacy-image').hexdigest()
        asset = MediaAsset.objects.create(
            source_key='sermo/messages/image/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.jpeg',
            source_uri='https://resource.example.com/existing.jpeg',
            kind=MediaAsset.KIND_IMAGE,
            content_hash=digest,
            file_size=len(b'legacy-image'),
            status=MediaAsset.STATUS_READY,
        )
        dates = self._row_dates()
        self._write(
            posts=[{
                'id': 1,
                'source_post_id': 'east-media',
                'author_qq': '1493732945',
                'content_raw': '',
                'content_text': '',
                'published_at': '2014-12-13 12:22:06',
                'visibility': 'public',
                'media': json.dumps([{
                    'type': 'image',
                    'custom_filepath': 'Messages/images/photo.jpeg',
                    'custom_url': 'https://qzone.example/photo.jpeg',
                }]),
                'source_payload': json.dumps({
                    'source_file': '江中东墙HTML/Messages/json/messages.json',
                }),
                **dates,
            }],
            comments=[],
            users=[{'qq': '1493732945', 'nickname': '江中东墙'}],
        )
        importer = QZoneImporter(self.space, self.input_path)
        importer.import_source()
        importer.import_identities()
        importer.prepare_media()

        result = importer.upload_media()
        importer.project()

        self.assertEqual(result['reused'], 1)
        put_file_mock.assert_not_called()
        media = QZoneMedia.objects.get()
        self.assertEqual(media.media_asset_id, asset.id)
        self.assertEqual(media.status, QZoneMedia.STATUS_READY)
        self.assertEqual(StatementMedia.objects.get().media_asset_id, asset.id)

    @patch('QZone.importer.put_file')
    def test_media_stage_prepares_and_projects_inline_emoticons(self, put_file_mock):
        emoticon_directory = Path(self.temp_dir.name) / '江中东墙HTML' / 'Common' / 'images'
        emoticon_directory.mkdir(parents=True)
        emoticon_path = emoticon_directory / 'e101.gif'
        emoticon_path.write_bytes(b'legacy-emoticon')
        digest = hashlib.sha256(b'legacy-emoticon').hexdigest()
        asset = MediaAsset.objects.create(
            source_key='sermo/qzone/emoticon/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.gif',
            source_uri='https://resource.example.com/e101.gif',
            kind=MediaAsset.KIND_IMAGE,
            content_hash=digest,
            file_size=len(b'legacy-emoticon'),
            status=MediaAsset.STATUS_READY,
        )
        dates = self._row_dates()
        source_payload = json.dumps({
            'source_file': '江中东墙HTML/Messages/json/messages.json',
        })
        self._write(
            posts=[{
                'id': 1,
                'source_post_id': 'east-emoticon',
                'author_qq': '1493732945',
                'content_raw': '历史[em]e101[/em]说说',
                'content_text': '历史说说',
                'published_at': '2014-12-13 12:22:06',
                'visibility': 'public',
                'media': '[]',
                'source_payload': source_payload,
                **dates,
            }],
            comments=[{
                'id': 1,
                'post_id': 1,
                'author_qq': '377489624',
                'parent_comment_id': '',
                'reply_to_qq': '',
                'content_raw': '评论[em]e101[/em][em]e999[/em]',
                'published_at': '2014-12-13 13:00:00',
                'source_payload': source_payload,
                **dates,
            }],
        )
        output = io.StringIO()
        importer = QZoneImporter(self.space, self.input_path, stdout=output)
        importer.import_source()
        importer.import_identities()

        manifest = importer.prepare_media()

        self.assertEqual(manifest, {
            'attachments': 0,
            'emoticon_references': 3,
            'emoticon_codes': 2,
            'emoticons_missing': 1,
        })
        self.assertSetEqual(set(QZonePost.objects.get(id=1).emoticons.values_list('code', flat=True)), {'e101'})
        self.assertSetEqual(
            set(QZoneComment.objects.get(id=1).emoticons.values_list('code', flat=True)),
            {'e101', 'e999'},
        )
        self.assertEqual(QZoneEmoticon.objects.get(code='e999').status, QZoneEmoticon.STATUS_MISSING)

        result = importer.upload_media()
        importer.project()

        self.assertEqual(result['emoticons']['reused'], 1)
        put_file_mock.assert_not_called()
        self.assertEqual(QZoneEmoticon.objects.get(code='e101').media_asset_id, asset.id)
        statement = QZonePost.objects.select_related('statement').get(id=1).statement
        self.assertEqual(statement.text, '历史[em]e101[/em]说说')
        statement_payload = Statement.detail(statement.user, statement.id)
        self.assertEqual(statement_payload['inline_emoticons'], [{
            'code': 'e101',
            'token': '[em]e101[/em]',
            'uri': f'/square/emoticons/{asset.blob_slug}',
        }])
        comment_payload = StatementComment.feed(statement.user, statement.id)[0]
        self.assertEqual(comment_payload['text'], '评论[em]e101[/em][em]e999[/em]')
        self.assertEqual([item['code'] for item in comment_payload['inline_emoticons']], ['e101'])

        statement.text = '历史说说'
        statement.save(update_fields=['text'])
        projected_comment = QZoneComment.objects.select_related('statement_comment').get(id=1).statement_comment
        projected_comment.text = '评论'
        projected_comment.save(update_fields=['text'])

        self.assertEqual(importer.project(), {'posts_created': 0, 'comments_created': 0})
        statement.refresh_from_db()
        projected_comment.refresh_from_db()
        self.assertEqual(statement.text, '历史[em]e101[/em]说说')
        self.assertEqual(projected_comment.text, '评论[em]e101[/em][em]e999[/em]')
        progress_output = output.getvalue()
        self.assertIn('manifest posts', progress_output)
        self.assertIn('manifest comments', progress_output)
        self.assertIn('upload attachments', progress_output)
        self.assertIn('upload emoticons', progress_output)
        self.assertIn('100.0%', progress_output)
