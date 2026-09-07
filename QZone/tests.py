from django.test import TestCase
from django.utils import timezone

from QZone.models import QZoneComment, QZonePost, QZoneUser
from Space.models import Space
from Square.models import Statement, StatementComment
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
