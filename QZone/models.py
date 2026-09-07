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


class QZoneMedia(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_READY = 'ready'
    STATUS_MISSING = 'missing'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, STATUS_PENDING),
        (STATUS_READY, STATUS_READY),
        (STATUS_MISSING, STATUS_MISSING),
        (STATUS_FAILED, STATUS_FAILED),
    )

    post = models.ForeignKey(
        QZonePost,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='media_items',
    )
    comment = models.ForeignKey(
        QZoneComment,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='media_items',
    )
    position = models.PositiveSmallIntegerField(default=0)
    kind = models.CharField(max_length=16)
    source_path = models.CharField(max_length=500)
    source_url = models.CharField(max_length=1000, blank=True, default='')
    mime_type = models.CharField(max_length=100, blank=True, default='')
    content_hash = models.CharField(max_length=64, blank=True, default='', db_index=True)
    file_size = models.BigIntegerField(null=True, blank=True)
    media_asset = models.ForeignKey(
        'Message.MediaAsset',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='qzone_import_items',
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    error = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'qzone_media'
        ordering = ('id',)
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(post__isnull=False, comment__isnull=True)
                    | models.Q(post__isnull=True, comment__isnull=False)
                ),
                name='qzone_media_has_one_owner',
            ),
            models.UniqueConstraint(fields=['post', 'position'], name='unique_qzone_post_media_position'),
            models.UniqueConstraint(fields=['comment', 'position'], name='unique_qzone_comment_media_position'),
        ]
