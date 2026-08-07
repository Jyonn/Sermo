import secrets

from django.db.models import Count, Q
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
    def feed(cls, user, before=None, limit=20, request=None):
        queryset = cls.visible_for(user).select_related('user').prefetch_related('media').annotate(
            visible_comment_count=Count('comments', filter=Q(comments__is_deleted=False)),
        )
        if before:
            queryset = queryset.filter(id__lt=before)
        return [item.jsonl(request=request) for item in queryset[:limit]]

    @classmethod
    def create_statement(cls, user, text, visibility, media):
        if not user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
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
        return cls.objects.select_related('user').prefetch_related('media').get(id=statement.id)

    def jsonl(self, request=None):
        return dict(
            statement_id=self.id,
            user=self.user.tiny_json(),
            text=self.text,
            visibility='friends' if self.visibility == StatementVisibilityChoice.FRIENDS else 'public',
            media=[item.jsonl(request=request) for item in self.media.all()],
            comment_count=getattr(self, 'visible_comment_count', self.comments.filter(is_deleted=False).count()),
            created_at=self.created_at.timestamp(),
        )


class StatementComment(models.Model):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name='comments', db_index=True)
    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='statement_comments')
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
    def feed(cls, user, statement_id, before=None, limit=30):
        statement = cls.statement_for_user(user, statement_id)
        queryset = cls.objects.filter(statement=statement, is_deleted=False).select_related('user')
        if before:
            queryset = queryset.filter(id__lt=before)
        return [comment.jsonl() for comment in queryset[:limit]]

    @classmethod
    def create_comment(cls, user, statement_id, text):
        if not user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        normalized_text = (text or '').strip()
        if not normalized_text:
            raise SquareErrors.COMMENT_REQUIRED
        if len(normalized_text) > 140:
            raise SquareErrors.COMMENT_TOO_LONG
        statement = cls.statement_for_user(user, statement_id)
        return cls.objects.create(statement=statement, user=user, text=normalized_text)

    def jsonl(self):
        return dict(
            comment_id=self.id,
            statement_id=self.statement_id,
            user=self.user.tiny_json(),
            text=self.text,
            created_at=self.created_at.timestamp(),
        )


class StatementMedia(models.Model):
    statement = models.ForeignKey(Statement, on_delete=models.CASCADE, related_name='media')
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
            else:
                raise SquareErrors.MEDIA_INVALID
            if images > 9:
                raise SquareErrors.IMAGE_LIMIT_EXCEEDED
            if audio > 1:
                raise SquareErrors.AUDIO_LIMIT_EXCEEDED
            key = validate_message_media_key(kind, str(item.get('key') or ''))
            duration = item.get('duration_seconds') if kind == 'audio' else None
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
        if self.kind == StatementMediaKindChoice.IMAGE:
            thumbnail_path = reverse('square media thumbnail', kwargs={'blob_slug': self.blob_slug})
            thumbnail_uri = request.build_absolute_uri(thumbnail_path) if request else thumbnail_path
        location = None
        if self.latitude is not None and self.longitude is not None:
            location = dict(
                latitude=float(self.latitude),
                longitude=float(self.longitude),
                address=self.address,
            )
        return dict(
            media_id=self.id,
            kind='image' if self.kind == StatementMediaKindChoice.IMAGE else 'audio',
            uri=uri,
            thumbnail_uri=thumbnail_uri,
            mime_type=self.mime_type,
            duration_seconds=self.duration_seconds,
            location=location,
        )
