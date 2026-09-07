from django.db import models
from django.utils import timezone


class QZoneUser(models.Model):
    qq = models.CharField(max_length=20, primary_key=True)
    nickname = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        db_table = 'qzone_user'


class QZonePost(models.Model):
    id = models.BigAutoField(primary_key=True)
    source_post_id = models.CharField(max_length=64)
    author = models.ForeignKey(
        QZoneUser,
        db_column='author_qq',
        to_field='qq',
        on_delete=models.PROTECT,
        related_name='posts',
    )
    content_raw = models.TextField(blank=True, default='')
    content_text = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(db_index=True)
    visibility = models.CharField(max_length=16, default='public')
    media = models.JSONField(default=list)
    source_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    statement = models.OneToOneField(
        'Square.Statement',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='qzone_source',
    )

    class Meta:
        db_table = 'qzone_post'
        ordering = ('published_at', 'id')
        constraints = [
            models.UniqueConstraint(
                fields=['author', 'source_post_id'],
                name='unique_qzone_post_author_source',
            ),
        ]


class QZoneComment(models.Model):
    id = models.BigAutoField(primary_key=True)
    post = models.ForeignKey(
        QZonePost,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    author = models.ForeignKey(
        QZoneUser,
        db_column='author_qq',
        to_field='qq',
        on_delete=models.PROTECT,
        related_name='comments',
    )
    parent = models.ForeignKey(
        'self',
        db_column='parent_comment_id',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='replies',
    )
    reply_to = models.ForeignKey(
        QZoneUser,
        db_column='reply_to_qq',
        to_field='qq',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='targeted_comments',
    )
    content_raw = models.TextField(blank=True, default='')
    published_at = models.DateTimeField(db_index=True)
    source_payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    statement_comment = models.OneToOneField(
        'Square.StatementComment',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='qzone_source',
    )

    class Meta:
        db_table = 'qzone_comment'
        ordering = ('published_at', 'id')
