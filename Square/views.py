from django.db import transaction
from django.http import HttpResponseRedirect
from django.views import View
from oba import raw
from smartdjango import OK, analyse

from Square.models import SquareReadState, Statement, StatementComment, StatementCommentLike, StatementLike, StatementMedia, statement_media_prefetch
from Activity.models import ActivityCampaign, ActivityService
from Friendship.models import Friendship, FriendshipStatusChoice
from django.db.models import Q
from Message.models import MediaAsset, MediaAssetAlias
from Square.params import SquareParams
from Square.quota import quota_for_user
from Square.validators import SquareErrors
from User.models import NotificationEvent, NotificationEventTypeChoice, PermanentVipCampaign
from utils import auth
from utils.auth import Request
from utils.qiniu import (
    build_message_image_thumbnail_uri,
    build_message_video_thumbnail_uri,
    issue_message_upload,
    sign_private_download_url,
)


class StatementView(View):
    @auth.require_user
    @analyse.query(SquareParams.before, SquareParams.limit, SquareParams.scope, SquareParams.user_id)
    def get(self, request: Request):
        request.user.space.require_square_enabled(scope=request.query.scope)
        return Statement.feed(
            request.user,
            before=request.query.before,
            limit=request.query.limit,
            request=request,
            scope=request.query.scope,
            user_id=request.query.user_id,
        )

    @auth.require_user
    @analyse.json(SquareParams.text, SquareParams.visibility, SquareParams.media, SquareParams.pin)
    def post(self, request: Request):
        request.user.space.require_square_enabled()
        with transaction.atomic():
            statement = Statement.create_statement(
                user=request.user,
                text=request.json.text,
                visibility=request.json.visibility,
                media=raw(request.json.media),
            )
            if request.json.pin:
                if not request.user.is_official:
                    raise SquareErrors.PIN_FORBIDDEN
                request.user.pinned_square_statement_id = statement.id
                request.user.save(update_fields=['pinned_square_statement_id'])
        return statement.jsonl(request=request)


class AdminStatementView(View):
    @auth.require_space
    @analyse.query(SquareParams.before, SquareParams.limit)
    def get(self, request: Request):
        request.space.require_square_enabled()
        official = request.space.ensure_official_user()
        request.user = official
        return Statement.admin_feed(
            request.space,
            official,
            before=request.query.before,
            limit=request.query.limit,
            request=request,
        )


class PinnedStatementView(View):
    @auth.require_user
    def get(self, request: Request):
        request.user.space.require_square_enabled()
        official = request.user.space.official_user
        if not official or not official.pinned_square_statement_id:
            return OK
        if not Statement.objects.filter(id=official.pinned_square_statement_id, space=request.user.space, is_deleted=False).exists():
            return OK
        return Statement.detail(request.user, official.pinned_square_statement_id, request=request)


class StatementPinView(View):
    @auth.require_user
    @analyse.json(SquareParams.pin)
    def post(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        if not request.user.is_official:
            raise SquareErrors.PIN_FORBIDDEN
        try:
            statement = Statement.objects.select_related('user').prefetch_related(statement_media_prefetch()).get(
                id=statement_id,
                user=request.user,
                space=request.user.space,
                is_deleted=False,
            )
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        request.user.pinned_square_statement_id = statement.id if request.json.pin else None
        request.user.save(update_fields=['pinned_square_statement_id'])
        return statement.jsonl(request=request)


class SquareQuotaView(View):
    @auth.require_user
    def get(self, request: Request):
        request.user.space.require_square_enabled()
        return quota_for_user(request.user)


class SquareStatusView(View):
    @staticmethod
    def _latest_visible_id(user, scope):
        queryset = Statement.visible_for(user).exclude(user=user)
        if scope == 'friends':
            friendships = Friendship.objects.filter(
                space=user.space,
                status=FriendshipStatusChoice.ACCEPTED,
            ).filter(Q(user_low=user) | Q(user_high=user)).values_list('user_low_id', 'user_high_id')
            friend_ids = [high_id if low_id == user.id else low_id for low_id, high_id in friendships]
            queryset = queryset.filter(user_id__in=friend_ids)
        return queryset.order_by('-id').values_list('id', flat=True).first() or 0

    @classmethod
    def _payload(cls, user):
        explore_latest = cls._latest_visible_id(user, 'all')
        friends_latest = cls._latest_visible_id(user, 'friends')
        state, _ = SquareReadState.objects.get_or_create(user=user, defaults={
            'explore_statement_id': explore_latest,
            'friends_statement_id': friends_latest,
        })
        activities = [ActivityService.payload(campaign, user) for campaign in ActivityCampaign.active()]
        activity_claimable = any(
            item['claimable_points'] or item['personal_reward_claimable']
            for item in activities
        )
        vip_campaign = PermanentVipCampaign.status_for(user)
        vip_claimable = vip_campaign['active'] and vip_campaign['eligible']
        _updated, notification_unread = NotificationEvent.mark_square_events_read(user, statement_id=-1)
        return dict(
            notification_unread=notification_unread,
            explore_unread=explore_latest > state.explore_statement_id,
            friends_unread=friends_latest > state.friends_statement_id,
            activity_claimable=activity_claimable or vip_claimable,
            claimable_activity_keys=[
                item['key'] for item in activities
                if item['claimable_points'] or item['personal_reward_claimable']
            ] + (['vip:founding-100'] if vip_claimable else []),
        )

    @auth.require_user
    def get(self, request: Request):
        request.user.space.require_square_enabled()
        return self._payload(request.user)

    @auth.require_user
    @analyse.json(SquareParams.read_scope)
    def post(self, request: Request):
        request.user.space.require_square_enabled()
        state = SquareReadState.ensure(request.user)
        latest_id = self._latest_visible_id(request.user, request.json.scope)
        field = 'explore_statement_id' if request.json.scope == 'all' else 'friends_statement_id'
        if latest_id > getattr(state, field):
            setattr(state, field, latest_id)
            state.save(update_fields=[field, 'updated_at'])
        return self._payload(request.user)


class StatementUploadView(View):
    @auth.require_user
    @analyse.json(SquareParams.kind, SquareParams.file_name, SquareParams.content_type)
    def post(self, request: Request):
        request.user.space.require_square_enabled()
        if request.json.kind not in {'image', 'audio', 'video'}:
            raise SquareErrors.MEDIA_INVALID
        request.user.require_capability(f'square.statement.publish.{request.json.kind}')
        return issue_message_upload(
            kind=request.json.kind,
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class StatementDetailView(View):
    @auth.require_user
    def get(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        return Statement.detail(request.user, statement_id, request=request)

    @auth.require_user
    def delete(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        try:
            statement = Statement.objects.get(id=statement_id, space=request.user.space, is_deleted=False)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        removed_by_admin = request.user.id != statement.user_id
        owner = statement.user
        excerpt = (statement.text or '').strip()[:80]
        statement.delete_for(request.user)
        if removed_by_admin:
            NotificationEvent.objects.create(
                space=request.user.space,
                user=owner,
                actor=request.user,
                event_type=NotificationEventTypeChoice.SQUARE_STATEMENT_REMOVED,
                payload=dict(statement_id=statement.id, statement_excerpt=excerpt, removed_by_admin=True),
            )
        return dict(statement_id=statement.id, deleted=True)


class StatementLikeView(View):
    @auth.require_user
    def post(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        request.user.require_capability('square.interaction.like')
        try:
            statement = Statement.visible_for(request.user).get(id=statement_id)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        _like, created = StatementLike.objects.get_or_create(statement=statement, user=request.user)
        if created:
            request.user.award_growth('explore:square_like')
            NotificationEvent.emit_square_event(
                statement.user, request.user, NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE, statement.id,
            )
        return dict(liked=True, like_count=statement.likes.count())

    @auth.require_user
    def delete(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        try:
            statement = Statement.visible_for(request.user).get(id=statement_id)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementLike.objects.filter(statement=statement, user=request.user).delete()
        return dict(liked=False, like_count=statement.likes.count())


class StatementCommentLikeView(View):
    @auth.require_user
    def post(self, request: Request, comment_id: int):
        request.user.space.require_square_enabled()
        request.user.require_capability('square.interaction.like')
        try:
            comment = StatementComment.objects.select_related('statement').get(id=comment_id, is_deleted=False)
        except StatementComment.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementComment.statement_for_user(request.user, comment.statement_id)
        _like, created = StatementCommentLike.objects.get_or_create(comment=comment, user=request.user)
        if created:
            request.user.award_growth('explore:square_comment_like')
            NotificationEvent.emit_square_event(
                comment.user, request.user, NotificationEventTypeChoice.SQUARE_COMMENT_LIKE,
                comment.statement_id, comment.id,
            )
        return dict(liked=True, like_count=comment.likes.count())

    @auth.require_user
    def delete(self, request: Request, comment_id: int):
        request.user.space.require_square_enabled()
        try:
            comment = StatementComment.objects.select_related('statement').get(id=comment_id, is_deleted=False)
        except StatementComment.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementComment.statement_for_user(request.user, comment.statement_id)
        StatementCommentLike.objects.filter(comment=comment, user=request.user).delete()
        return dict(liked=False, like_count=comment.likes.count())


class StatementCommentDetailView(View):
    @auth.require_user
    def delete(self, request: Request, comment_id: int):
        request.user.space.require_square_enabled()
        try:
            comment = StatementComment.objects.select_related('statement', 'user').get(
                id=comment_id,
                is_deleted=False,
            )
        except StatementComment.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementComment.statement_for_user(request.user, comment.statement_id)
        deleted_count = comment.delete_for(request.user)
        return dict(
            comment_id=comment.id,
            statement_id=comment.statement_id,
            deleted_count=deleted_count,
            root_deleted=comment.parent_id is None,
        )


class StatementCommentView(View):
    @auth.require_user
    @analyse.query(SquareParams.offset, SquareParams.limit, SquareParams.comment_sort)
    def get(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        return StatementComment.feed(
            request.user,
            statement_id=statement_id,
            offset=request.query.offset,
            limit=request.query.limit,
            sort=request.query.comment_sort,
        )

    @auth.require_user
    @analyse.json(SquareParams.comment_text, SquareParams.parent_id)
    def post(self, request: Request, statement_id: int):
        request.user.space.require_square_enabled()
        comment = StatementComment.create_comment(
            request.user,
            statement_id,
            request.json.text,
            parent_id=request.json.parent_id,
        )
        if comment.parent_id:
            NotificationEvent.emit_square_event(
                comment.parent.user, request.user, NotificationEventTypeChoice.SQUARE_COMMENT_REPLY,
                comment.statement_id, comment.id,
            )
        else:
            NotificationEvent.emit_square_event(
                comment.statement.user, request.user, NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT,
                comment.statement_id, comment.id,
            )
        return comment.jsonl(viewer=request.user)


class StatementMediaView(View):
    def get(self, request: Request, blob_slug: str):
        asset = MediaAssetAlias.resolve(blob_slug)
        if asset is None or not asset.statement_media_items.filter(statement__is_deleted=False).exists():
            raise SquareErrors.NOT_EXISTS
        response = HttpResponseRedirect(sign_private_download_url(asset.source_uri))
        response['Cache-Control'] = 'private, max-age=86400'
        return response


class StatementMediaThumbnailView(View):
    def get(self, request: Request, blob_slug: str):
        asset = MediaAssetAlias.resolve(blob_slug)
        if asset is None or asset.kind not in {MediaAsset.KIND_IMAGE, MediaAsset.KIND_VIDEO} or not asset.statement_media_items.filter(statement__is_deleted=False).exists():
            raise SquareErrors.NOT_EXISTS
        thumbnail_uri = build_message_image_thumbnail_uri(asset.source_uri, width=480) if asset.kind == MediaAsset.KIND_IMAGE else build_message_video_thumbnail_uri(asset.source_uri, width=480)
        response = HttpResponseRedirect(thumbnail_uri)
        response['Cache-Control'] = 'private, max-age=86400'
        return response
