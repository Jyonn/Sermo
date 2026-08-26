from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Exists, OuterRef, Prefetch, Q
from django.utils import timezone
from django.urls import reverse
from smartdjango import Choice, models

from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import ForwardBundle, ForwardBundleItem, MessageValidator
from Square.validators import SquareErrors
from utils.qiniu import avatar_uri_for_key, validate_message_media_key


class StatementVisibilityChoice(Choice):
    PUBLIC = 0
    FRIENDS = 1


class StatementMediaKindChoice(Choice):
    IMAGE = 0
    AUDIO = 1
    VIDEO = 2


class SquareReadState(models.Model):
    user = models.OneToOneField('User.User', on_delete=models.CASCADE, related_name='square_read_state')
    explore_statement_id = models.PositiveBigIntegerField(default=0)
    friends_statement_id = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def ensure(cls, user):
        state, _ = cls.objects.get_or_create(user=user)
        return state


def statement_media_prefetch():
    return Prefetch('media', queryset=StatementMedia.objects.select_related('media_asset'))


def statement_forward_bundle_prefetch():
    return Prefetch(
        'forward_bundle__items',
        queryset=ForwardBundleItem.objects.select_related('media_resource__asset').order_by('position'),
    )


def _frequency_limits(level):
    if level <= 5:
        return 1, 5
    if level <= 9:
        return 2, 10
    if level <= 13:
        return 3, 15
    if level <= 17:
        return 4, 20
    return 5, 35


def frequency_limits_for_user(user):
    if user.is_permanent_vip:
        daily, weekly = _frequency_limits(18)
    else:
        daily, weekly = _frequency_limits(user.effective_growth_level())
    policy_limits = user.capability_decision('square.statement.publish').limits
    return min(daily, policy_limits.get('daily', daily)), min(weekly, policy_limits.get('weekly', weekly))


def anonymous_weekly_limit_for_user(user):
    if user.is_official:
        return None
    _daily, weekly = frequency_limits_for_user(user)
    return int(weekly * 0.4)


def anonymous_user_json():
    return dict(
        user_id=0,
        name='',
        official=False,
        anonymous=True,
        avatar_type='preset',
        avatar_uri='',
        is_permanent_vip=False,
        growth_level=0,
    )


def _enforce_frequency(queryset, user, multiplier=1):
    if user.is_official:
        return
    daily, weekly = frequency_limits_for_user(user)
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
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    address = models.CharField(max_length=255, blank=True, default='')
    geocoding_provider = models.CharField(max_length=32, blank=True, default='')
    forward_bundle = models.ForeignKey(
        'Message.ForwardBundle', on_delete=models.PROTECT, null=True, blank=True,
        related_name='square_statements',
    )
    chat_record_redacted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)
    is_anonymous = models.BooleanField(default=False, db_index=True)

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
        queryset = cls.visible_for(user).select_related('user', 'forward_bundle').prefetch_related(
            statement_media_prefetch(), statement_forward_bundle_prefetch(),
        ).annotate(
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
            queryset = queryset.filter(user_id__in=[user.id, *friend_ids], is_anonymous=False)
        elif scope == 'mine':
            queryset = queryset.filter(user=user)
        if user_id is not None:
            queryset = queryset.filter(user_id=user_id, is_anonymous=False)
        if before:
            queryset = queryset.filter(id__lt=before)
        return [item.jsonl(request=request) for item in queryset.order_by('-created_at', '-id')[:limit]]

    @classmethod
    def admin_feed(cls, space, viewer, before=None, limit=20, request=None):
        queryset = cls.objects.filter(space=space, is_deleted=False).select_related('user', 'forward_bundle').prefetch_related(
            statement_media_prefetch(), statement_forward_bundle_prefetch(),
        ).annotate(
            visible_comment_count=Count('comments', filter=Q(comments__is_deleted=False), distinct=True),
            visible_like_count=Count('likes', distinct=True),
            viewer_liked=Exists(StatementLike.objects.filter(statement_id=OuterRef('pk'), user=viewer)),
        )
        if before:
            queryset = queryset.filter(id__lt=before)
        return [item.jsonl(request=request) for item in queryset.order_by('-created_at', '-id')[:limit]]

    @classmethod
    def detail(cls, user, statement_id, request=None):
        try:
            statement = cls.visible_for(user).select_related('user', 'forward_bundle').prefetch_related(
                statement_media_prefetch(), statement_forward_bundle_prefetch(),
            ).annotate(
                visible_comment_count=Count('comments', filter=Q(comments__is_deleted=False), distinct=True),
                visible_like_count=Count('likes', distinct=True),
                viewer_liked=Exists(StatementLike.objects.filter(statement_id=OuterRef('pk'), user=user)),
            ).get(id=statement_id)
        except cls.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        return statement.jsonl(request=request)

    @classmethod
    def create_statement(
        cls, user, text, visibility, media, location=None, forward_bundle=None,
        is_anonymous=False, chat_record_redacted=False,
    ):
        user.require_capability('square.statement.publish')
        _enforce_frequency(cls.objects.filter(space=user.space, is_deleted=False), user)
        is_anonymous = bool(is_anonymous)
        if is_anonymous:
            if not user.space.square_explore_enabled:
                raise SquareErrors.ANONYMOUS_REQUIRES_EXPLORE
            if visibility != 'public':
                raise SquareErrors.ANONYMOUS_VISIBILITY_INVALID
            anonymous_limit = anonymous_weekly_limit_for_user(user)
            if anonymous_limit is not None and cls.objects.filter(
                space=user.space,
                user=user,
                is_deleted=False,
                is_anonymous=True,
                created_at__gte=timezone.now() - timedelta(days=7),
            ).count() >= anonymous_limit:
                raise SquareErrors.ANONYMOUS_WEEKLY_LIMIT_REACHED
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
        if forward_bundle is not None:
            if not user.is_official or forward_bundle.created_by_id != user.id:
                raise SquareErrors.CHAT_RECORD_FORBIDDEN
            if normalized_media:
                raise SquareErrors.CHAT_RECORD_EXCLUSIVE
        media_capabilities = {
            StatementMediaKindChoice.IMAGE: 'square.statement.publish.image',
            StatementMediaKindChoice.AUDIO: 'square.statement.publish.audio',
            StatementMediaKindChoice.VIDEO: 'square.statement.publish.video',
        }
        if not normalized_media and forward_bundle is None:
            user.require_capability('square.statement.publish.text')
        for media_kind in {item['kind'] for item in normalized_media}:
            user.require_capability(media_capabilities[media_kind])
        if not normalized_text and not normalized_media and forward_bundle is None:
            raise SquareErrors.CONTENT_REQUIRED
        StatementMedia.attach_assets(normalized_media)
        normalized_location = location or None
        if normalized_location and not normalized_location.get('address'):
            try:
                from Message.image_metadata import reverse_geocode
                address, provider = reverse_geocode(
                    normalized_location['latitude'], normalized_location['longitude'],
                )
                normalized_location = {
                    **normalized_location,
                    'address': str(address or '')[:255],
                    'geocoding_provider': str(provider or '')[:32],
                }
            except Exception:
                normalized_location = {**normalized_location, 'address': '', 'geocoding_provider': ''}
        statement = cls.objects.create(
            space=user.space,
            user=user,
            text=normalized_text,
            visibility=visibility_value,
            latitude=normalized_location['latitude'] if normalized_location else None,
            longitude=normalized_location['longitude'] if normalized_location else None,
            address=normalized_location.get('address', '') if normalized_location else '',
            geocoding_provider=normalized_location.get('geocoding_provider', '') if normalized_location else '',
            forward_bundle=forward_bundle,
            chat_record_redacted=bool(chat_record_redacted and forward_bundle is not None),
            is_anonymous=is_anonymous,
        )
        StatementMedia.objects.bulk_create([
            StatementMedia(statement=statement, position=index, media_asset=item['media_asset'])
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
        if not is_anonymous:
            from Chat.models import ChatUserPreference
            transaction.on_commit(lambda: ChatUserPreference.emit_peer_statement_events(statement))
        from Activity.models import ActivityService
        ActivityService.record_event(user, 'square.statement.publish', statement.id)
        return cls.objects.select_related('user', 'forward_bundle').prefetch_related(
            statement_media_prefetch(), statement_forward_bundle_prefetch(),
        ).get(id=statement.id)

    def jsonl(self, request=None):
        viewer = getattr(request, 'user', None) if request else None
        if viewer is not None and not hasattr(viewer, 'space_id'):
            viewer = None
        return dict(
            statement_id=self.id,
            user=anonymous_user_json() if self.is_anonymous else self.user.tiny_json(),
            is_anonymous=self.is_anonymous,
            is_mine=bool(viewer and viewer.id == self.user_id),
            text=self.text,
            visibility='friends' if self.visibility == StatementVisibilityChoice.FRIENDS else 'public',
            location=(dict(
                latitude=float(self.latitude),
                longitude=float(self.longitude),
                address=self.address,
                geocoding_provider=self.geocoding_provider,
            ) if self.latitude is not None and self.longitude is not None else None),
            media=[item.jsonl(request=request) for item in self.media.all()],
            chat_record=(
                self.forward_bundle.jsonl(request=request, redact_identity=self.chat_record_redacted)
                if self.forward_bundle_id else None
            ),
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
    is_anonymous = models.BooleanField(default=False, db_index=True)

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
    def create_comment(cls, user, statement_id, text, parent_id=None, is_anonymous=False):
        user.require_capability('square.interaction.reply' if parent_id is not None else 'square.interaction.comment')
        _enforce_frequency(cls.objects.filter(statement__space=user.space, is_deleted=False), user, multiplier=5)
        normalized_text = (text or '').strip()
        if not normalized_text:
            raise SquareErrors.COMMENT_REQUIRED
        if len(normalized_text) > 140:
            raise SquareErrors.COMMENT_TOO_LONG
        statement = cls.statement_for_user(user, statement_id)
        is_anonymous = bool(is_anonymous)
        if is_anonymous and (not statement.is_anonymous or statement.user_id != user.id):
            raise SquareErrors.ANONYMOUS_COMMENT_FORBIDDEN
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
        comment = cls.objects.create(
            statement=statement,
            user=user,
            text=normalized_text,
            parent=parent,
            is_anonymous=is_anonymous,
        )
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
            reply_to_user=(anonymous_user_json() if self.parent.is_anonymous else self.parent.user.tiny_json()) if self.parent_id else None,
            user=anonymous_user_json() if self.is_anonymous else self.user.tiny_json(),
            is_anonymous=self.is_anonymous,
            is_author=self.user_id == self.statement.user_id,
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
    media_asset = models.ForeignKey(
        'Message.MediaAsset',
        on_delete=models.PROTECT,
        related_name='statement_media_items',
    )
    position = models.PositiveSmallIntegerField(default=0)

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
            normalized.append(dict(
                key=key,
                mime_type=item.get('mime_type'),
                duration_seconds=duration,
                kind=kind_value,
            ))
        return normalized

    @classmethod
    def attach_assets(cls, normalized):
        from Message.models import MediaAsset
        for item in normalized:
            kind_name = {
                StatementMediaKindChoice.IMAGE: 'image',
                StatementMediaKindChoice.AUDIO: 'audio',
                StatementMediaKindChoice.VIDEO: 'video',
            }[item['kind']]
            try:
                source_uri = avatar_uri_for_key(item['key'])
            except Exception:
                source_uri = item['key']
            item['media_asset'] = MediaAsset.queue(
                item['key'], source_uri, MediaAsset.kind_for_name(kind_name),
                mime_type=item['mime_type'], duration_seconds=item['duration_seconds'],
            )

    @classmethod
    def fetch_metadata_async(cls, media_id):
        try:
            media = cls.objects.select_related('media_asset').get(id=media_id)
        except cls.DoesNotExist:
            return None
        return media.media_asset

    def source_uri(self):
        return self.media_asset.source_uri

    def jsonl(self, request=None):
        asset = self.media_asset
        path = reverse('square media', kwargs={'blob_slug': asset.blob_slug})
        uri = request.build_absolute_uri(path) if request else path
        thumbnail_uri = None
        if asset.kind in (asset.KIND_IMAGE, asset.KIND_VIDEO):
            thumbnail_path = reverse('square media thumbnail', kwargs={'blob_slug': asset.blob_slug})
            thumbnail_uri = request.build_absolute_uri(thumbnail_path) if request else thumbnail_path
        return dict(
            media_id=self.id,
            kind={asset.KIND_IMAGE: 'image', asset.KIND_AUDIO: 'audio', asset.KIND_VIDEO: 'video'}[asset.kind],
            uri=uri,
            thumbnail_uri=thumbnail_uri,
            mime_type=asset.mime_type,
            duration_seconds=asset.duration_seconds,
            location=None,
            metadata_status=asset.status,
            metadata=asset.jsonl(),
        )
