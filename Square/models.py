import secrets
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from django.urls import reverse
from smartdjango import Choice, models

from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import MessageValidator
from Square.validators import SquareErrors
from utils.qiniu import avatar_uri_for_key, validate_message_media_key


class StatementVisibilityChoice(Choice):
    PUBLIC = 0
    FRIENDS = 1


class StatementMediaKindChoice(Choice):
    IMAGE = 0
    AUDIO = 1
    VIDEO = 2


def _frequency_limits(level):
    if level <= 5:
        return 1, 5
    if level <= 9:
        return 2, 10
    if level <= 13:
        return 2, 12
    if level <= 17:
        return 3, 18
    return 3, 21


def _enforce_frequency(queryset, user, multiplier=1):
    if user.is_official:
        return
    daily, weekly = _frequency_limits(user.effective_growth_level())
    now = timezone.now()
    if queryset.filter(user=user, created_at__gte=now - timedelta(days=1)).count() >= daily * multiplier:
        raise SquareErrors.DAILY_LIMIT_REACHED
    if queryset.filter(user=user, created_at__gte=now - timedelta(days=7)).count() >= weekly * multiplier:
        raise SquareErrors.WEEKLY_LIMIT_REACHED


class Statement(models.Model):
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='statements', db_index=True)
    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='statements')
    text = models.CharField(max_length=140, blank=True, default='')
    visibility = models.IntegerField(
        choices=StatementVisibilityChoice.to_choices(),
        default=StatementVisibilityChoice.PUBLIC,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-id']

    @classmethod
    def visible_for(cls, user):
        friend_ids = Friendship.objects.filter(
            space=user.space,
            status=FriendshipStatusChoice.ACCEPTED,
        ).filter(Q(user_low=user) | Q(user_high=user)).values_list('user_low_id', 'user_high_id')
        visible_author_ids = {user.id}
        for low_id, high_id in friend_ids:
            visible_author_ids.add(high_id if low_id == user.id else low_id)
        return cls.objects.filter(space=user.space, is_deleted=False).filter(
            Q(visibility=StatementVisibilityChoice.PUBLIC)
            | Q(user_id__in=visible_author_ids)
        )

    @classmethod
    def feed(cls, user, before=None, limit=20, request=None, scope='all', user_id=None):
        queryset = cls.visible_for(user).select_related('user').prefetch_related('media').annotate(
            visible_comment_count=Count('comments', filter=Q(comments__is_deleted=False), distinct=True),
            visible_like_count=Count('likes', distinct=True),
            viewer_liked=Exists(StatementLike.objects.filter(statement_id=OuterRef('pk'), user=user)),
        )
        if scope == 'friends':
            friendships = Friendship.objects.filter(
                space=user.space,
                status=FriendshipStatusChoice.ACCEPTED,
            ).filter(Q(user_low=user) | Q(user_high=user)).values_list('user_low_id', 'user_high_id')
            friend_ids = [high_id if low_id == user.id else low_id for low_id, high_id in friendships]
            queryset = queryset.filter(user_id__in=[user.id, *friend_ids])
        elif scope == 'mine':
            queryset = queryset.filter(user=user)
        if user_id is not None:
            queryset = queryset.filter(user_id=user_id)
        if before:
            queryset = queryset.filter(id__lt=before)
        return [item.jsonl(request=request) for item in queryset.order_by('-created_at', '-id')[:limit]]

    @classmethod
    def detail(cls, user, statement_id, request=None):
        try:
            statement = cls.visible_for(user).select_related('user').prefetch_related('media').annotate(
                visible_comment_count=Count('comments', filter=Q(comments__is_deleted=False), distinct=True),
                visible_like_count=Count('likes', distinct=True),
                viewer_liked=Exists(StatementLike.objects.filter(statement_id=OuterRef('pk'), user=user)),
            ).get(id=statement_id)
        except cls.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        return statement.jsonl(request=request)

    @classmethod
    def create_statement(cls, user, text, visibility, media):
        if not user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        _enforce_frequency(cls.objects.filter(space=user.space, is_deleted=False), user)
        normalized_text = (text or '').strip()
        if len(normalized_text) > 140:
            raise SquareErrors.TEXT_TOO_LONG
        visibility_value = {
            'public': StatementVisibilityChoice.PUBLIC,
            'friends': StatementVisibilityChoice.FRIENDS,
        }.get(visibility)
        if visibility_value is None:
            raise SquareErrors.VISIBILITY_INVALID

        normalized_media = StatementMedia.normalize_payload(media)
        level = user.effective_growth_level()
        if any(item['kind'] == StatementMediaKindChoice.AUDIO for item in normalized_media) and level < 6 and not user.is_official:
            raise SquareErrors.AUDIO_LEVEL_REQUIRED
        if any(item['kind'] == StatementMediaKindChoice.VIDEO for item in normalized_media) and level < 8 and not user.is_official:
            raise SquareErrors.VIDEO_LEVEL_REQUIRED
        if not normalized_text and not normalized_media:
            raise SquareErrors.CONTENT_REQUIRED
        statement = cls.objects.create(
            space=user.space,
            user=user,
            text=normalized_text,
            visibility=visibility_value,
        )
        StatementMedia.objects.bulk_create([
            StatementMedia(statement=statement, position=index, **item)
            for index, item in enumerate(normalized_media)
        ])
        user.award_growth('explore:square_statement')
        if visibility_value == StatementVisibilityChoice.FRIENDS:
            user.award_growth('explore:square_friends')
        media_events = {
            StatementMediaKindChoice.IMAGE: 'explore:square_image',
            StatementMediaKindChoice.AUDIO: 'explore:square_audio',
            StatementMediaKindChoice.VIDEO: 'explore:square_video',
        }
        for kind in {item['kind'] for item in normalized_media}:
            if kind in media_events:
                user.award_growth(media_events[kind])
        media_ids = list(statement.media.values_list('id', flat=True))
        transaction.on_commit(lambda: [StatementMedia.fetch_metadata_async(media_id) for media_id in media_ids])
        return cls.objects.select_related('user').prefetch_related('media').get(id=statement.id)

    def jsonl(self, request=None):
        viewer = getattr(request, 'user', None) if request else None
        return dict(
            statement_id=self.id,
            user=self.user.tiny_json(),
            text=self.text,
            visibility='friends' if self.visibility == StatementVisibilityChoice.FRIENDS else 'public',
            media=[item.jsonl(request=request) for item in self.media.all()],
            comment_count=getattr(self, 'visible_comment_count', self.comments.filter(is_deleted=False).count()),
            like_count=getattr(self, 'visible_like_count', self.likes.count()),
            liked=bool(getattr(self, 'viewer_liked', viewer and self.likes.filter(user=viewer).exists())),
            can_delete=bool(viewer and (viewer.id == self.user_id or viewer.is_official and viewer.space_id == self.space_id)),
            can_pin=bool(viewer and viewer.id == self.user_id and viewer.is_official),
            is_pinned=bool(self.user.is_official and self.user.pinned_square_statement_id == self.id),
            created_at=self.created_at.timestamp(),
        )

    def delete_for(self, user):
        if user.id != self.user_id and not (user.is_official and user.space_id == self.space_id):
            raise SquareErrors.DELETE_FORBIDDEN
        self.is_deleted = True
        self.save(update_fields=['is_deleted'])


class StatementComment(models.Model):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name='comments', db_index=True)
    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='statement_comments')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.CASCADE, related_name='replies')
    text = models.CharField(max_length=140)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ['-id']

    @classmethod
    def statement_for_user(cls, user, statement_id):
        try:
            return Statement.visible_for(user).get(id=statement_id)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS

    @classmethod
    def feed(cls, user, statement_id, offset=0, limit=30, sort='hot'):
        statement = cls.statement_for_user(user, statement_id)
        queryset = cls.objects.filter(statement=statement, is_deleted=False).select_related('statement', 'user', 'parent__user').annotate(
            visible_like_count=Count('likes', distinct=True),
            viewer_liked=Exists(StatementCommentLike.objects.filter(comment_id=OuterRef('pk'), user=user)),
        )
        comments = list(queryset)
        comments_by_id = {comment.id: comment for comment in comments}
        roots = [comment for comment in comments if comment.parent_id is None]
        replies_by_root = {comment.id: [] for comment in roots}

        for comment in comments:
            if comment.parent_id is None:
                continue
            root = comment
            visited = set()
            while root.parent_id is not None and root.parent_id not in visited:
                visited.add(root.id)
                parent = comments_by_id.get(root.parent_id)
                if parent is None:
                    root = None
                    break
                root = parent
            if root is not None and root.id in replies_by_root:
                comment.thread_root_id = root.id
                replies_by_root[root.id].append(comment)

        for root in roots:
            root.visible_replies = sorted(replies_by_root[root.id], key=lambda item: (item.created_at, item.id))
            root.visible_reply_count = len(root.visible_replies)
        if sort == 'latest':
            roots.sort(key=lambda item: (-item.created_at.timestamp(), -item.id))
        else:
            roots.sort(key=lambda item: (-item.visible_like_count, -item.visible_reply_count, -item.created_at.timestamp(), -item.id))
        return [comment.jsonl(viewer=user, include_replies=True) for comment in roots[offset:offset + limit]]

    @classmethod
    def create_comment(cls, user, statement_id, text, parent_id=None):
        if not user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        _enforce_frequency(cls.objects.filter(statement__space=user.space, is_deleted=False), user, multiplier=5)
        normalized_text = (text or '').strip()
        if not normalized_text:
            raise SquareErrors.COMMENT_REQUIRED
        if len(normalized_text) > 140:
            raise SquareErrors.COMMENT_TOO_LONG
        statement = cls.statement_for_user(user, statement_id)
        parent = None
        if parent_id is not None:
            try:
                parent = cls.objects.select_related('user', 'parent').get(
                    id=parent_id,
                    statement=statement,
                    is_deleted=False,
                )
            except cls.DoesNotExist:
                raise SquareErrors.NOT_EXISTS
        comment = cls.objects.create(statement=statement, user=user, text=normalized_text, parent=parent)
        if parent is not None:
            root = parent
            while root.parent_id is not None:
                root = root.parent
            comment.thread_root_id = root.id
        comment.visible_like_count = 0
        comment.viewer_liked = False
        user.award_growth('explore:square_reply' if parent is not None else 'explore:square_comment')
        return comment

    def jsonl(self, viewer=None, include_replies=False):
        like_count = self.visible_like_count if hasattr(self, 'visible_like_count') else self.likes.count()
        reply_count = 0 if self.parent_id else (
            self.visible_reply_count if hasattr(self, 'visible_reply_count') else self.replies.filter(is_deleted=False).count()
        )
        payload = dict(
            comment_id=self.id,
            statement_id=self.statement_id,
            parent_id=self.parent_id,
            root_id=getattr(self, 'thread_root_id', self.parent_id),
            reply_to_user=self.parent.user.tiny_json() if self.parent_id else None,
            user=self.user.tiny_json(),
            text=self.text,
            like_count=like_count,
            reply_count=reply_count,
            liked=bool(getattr(self, 'viewer_liked', viewer and self.likes.filter(user=viewer).exists())),
            can_delete=bool(viewer and (
                viewer.id == self.user_id
                or viewer.id == self.statement.user_id
                or viewer.is_official and viewer.space_id == self.statement.space_id
            )),
            created_at=self.created_at.timestamp(),
        )
        if include_replies:
            payload['replies'] = [
                reply.jsonl(viewer=viewer)
                for reply in getattr(self, 'visible_replies', [])
            ]
        return payload

    def delete_for(self, user):
        if user.id not in (self.user_id, self.statement.user_id) and not (
            user.is_official and user.space_id == self.statement.space_id
        ):
            raise SquareErrors.COMMENT_DELETE_FORBIDDEN

        delete_ids = {self.id}
        if self.parent_id is None:
            descendants = list(type(self).objects.filter(
                statement_id=self.statement_id,
                is_deleted=False,
            ).values_list('id', 'parent_id'))
            while True:
                next_ids = {comment_id for comment_id, parent_id in descendants if parent_id in delete_ids}
                if next_ids.issubset(delete_ids):
                    break
                delete_ids.update(next_ids)
        deleted_count = type(self).objects.filter(id__in=delete_ids, is_deleted=False).update(is_deleted=True)
        return deleted_count


class StatementLike(models.Model):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='statement_likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['statement', 'user'], name='unique_statement_like')]


class StatementCommentLike(models.Model):
    comment = models.ForeignKey(StatementComment, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='statement_comment_likes')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['comment', 'user'], name='unique_statement_comment_like')]


class StatementMedia(models.Model):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name='media')
    media_metadata = models.ForeignKey(
        'Message.MediaMetadata',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='statement_media_items',
    )
    kind = models.IntegerField(choices=StatementMediaKindChoice.to_choices())
    position = models.PositiveSmallIntegerField(default=0)
    key = models.CharField(max_length=255)
    blob_slug = models.CharField(max_length=32, unique=True, db_index=True)
    mime_type = models.CharField(max_length=100, blank=True, default='')
    duration_seconds = models.PositiveSmallIntegerField(null=True, blank=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.CharField(max_length=255, blank=True, default='')

    class Meta:
        ordering = ['position', 'id']

    @classmethod
    def normalize_payload(cls, media):
        if not isinstance(media, list):
            raise SquareErrors.MEDIA_INVALID
        images = 0
        audio = 0
        video = 0
        normalized = []
        for item in media:
            if not hasattr(item, 'get'):
                raise SquareErrors.MEDIA_INVALID
            kind = str(item.get('kind') or '').strip().lower()
            if kind == 'image':
                images += 1
                kind_value = StatementMediaKindChoice.IMAGE
            elif kind == 'audio':
                audio += 1
                kind_value = StatementMediaKindChoice.AUDIO
            elif kind == 'video':
                video += 1
                kind_value = StatementMediaKindChoice.VIDEO
            else:
                raise SquareErrors.MEDIA_INVALID
            if images > 9:
                raise SquareErrors.IMAGE_LIMIT_EXCEEDED
            if audio > 1:
                raise SquareErrors.AUDIO_LIMIT_EXCEEDED
            if video > 1:
                raise SquareErrors.VIDEO_LIMIT_EXCEEDED
            if sum(bool(count) for count in (images, audio, video)) > 1:
                raise SquareErrors.MEDIA_INVALID
            key = validate_message_media_key(kind, str(item.get('key') or ''))
            duration = item.get('duration_seconds') if kind in {'audio', 'video'} else None
            if duration is not None:
                try:
                    duration = max(1, int(round(float(duration))))
                except (TypeError, ValueError):
                    raise SquareErrors.AUDIO_DURATION_INVALID
                if duration > MessageValidator.MAX_AUDIO_DURATION_SECONDS:
                    raise SquareErrors.AUDIO_DURATION_INVALID
            location = item.get('location') or {}
            if not hasattr(location, 'get'):
                raise SquareErrors.MEDIA_INVALID
            try:
                raw_latitude = location.get('latitude')
                raw_longitude = location.get('longitude')
                latitude = round(float(raw_latitude), 6) if raw_latitude is not None else None
                longitude = round(float(raw_longitude), 6) if raw_longitude is not None else None
            except (TypeError, ValueError):
                raise SquareErrors.MEDIA_INVALID
            if latitude is not None and not -90 <= latitude <= 90:
                raise SquareErrors.MEDIA_INVALID
            if longitude is not None and not -180 <= longitude <= 180:
                raise SquareErrors.MEDIA_INVALID
            normalized.append(dict(
                kind=kind_value,
                key=key,
                blob_slug=secrets.token_hex(12),
                mime_type=str(item.get('mime_type') or '')[:100],
                duration_seconds=duration,
                latitude=latitude,
                longitude=longitude,
                address=str(location.get('address') or '')[:255],
            ))
        return normalized

    @classmethod
    def fetch_metadata_async(cls, media_id):
        from Message.models import MediaMetadata

        try:
            media = cls.objects.get(id=media_id)
        except cls.DoesNotExist:
            return None
        kind = {
            StatementMediaKindChoice.IMAGE: MediaMetadata.KIND_IMAGE,
            StatementMediaKindChoice.VIDEO: MediaMetadata.KIND_VIDEO,
        }.get(media.kind)
        if kind is None:
            return None
        metadata = MediaMetadata.queue(media.key, media.source_uri(), kind)
        if media.media_metadata_id != metadata.id:
            media.media_metadata = metadata
            media.save(update_fields=['media_metadata'])
        return metadata

    @classmethod
    def index_by_blob_slug(cls, blob_slug):
        try:
            return cls.objects.select_related('statement', 'statement__user').get(
                blob_slug=(blob_slug or '').strip().lower(),
                statement__is_deleted=False,
            )
        except cls.DoesNotExist:
            raise SquareErrors.NOT_EXISTS

    def source_uri(self):
        return avatar_uri_for_key(self.key)

    def jsonl(self, request=None):
        path = reverse('square media', kwargs={'blob_slug': self.blob_slug})
        uri = request.build_absolute_uri(path) if request else path
        thumbnail_uri = None
        if self.kind in (StatementMediaKindChoice.IMAGE, StatementMediaKindChoice.VIDEO):
            thumbnail_path = reverse('square media thumbnail', kwargs={'blob_slug': self.blob_slug})
            thumbnail_uri = request.build_absolute_uri(thumbnail_path) if request else thumbnail_path
        location = None
        if self.latitude is not None and self.longitude is not None:
            location = dict(
                latitude=float(self.latitude),
                longitude=float(self.longitude),
                address=self.address,
            )
        metadata = self.media_metadata
        return dict(
            media_id=self.id,
            kind={StatementMediaKindChoice.IMAGE: 'image', StatementMediaKindChoice.AUDIO: 'audio', StatementMediaKindChoice.VIDEO: 'video'}[self.kind],
            uri=uri,
            thumbnail_uri=thumbnail_uri,
            mime_type=self.mime_type,
            duration_seconds=self.duration_seconds,
            location=location,
            metadata_status=metadata.status if metadata else 0,
            metadata=metadata.jsonl() if metadata else {},
        )
