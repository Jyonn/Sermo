from django.db import transaction
from django.http import HttpResponseRedirect
from django.views import View
from oba import raw
from smartdjango import analyse

from Square.models import Statement, StatementComment, StatementCommentLike, StatementLike, StatementMedia, StatementMediaKindChoice
from Square.params import SquareParams
from Square.quota import quota_for_user
from Square.validators import SquareErrors
from User.models import NotificationEvent, NotificationEventTypeChoice
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


class PinnedStatementView(View):
    @auth.require_user
    def get(self, request: Request):
        official = request.user.space.official_user
        if not official or not official.pinned_square_statement_id:
            return None
        if not Statement.objects.filter(id=official.pinned_square_statement_id, space=request.user.space, is_deleted=False).exists():
            return None
        return Statement.detail(request.user, official.pinned_square_statement_id, request=request)


class StatementPinView(View):
    @auth.require_user
    @analyse.json(SquareParams.pin)
    def post(self, request: Request, statement_id: int):
        if not request.user.is_official:
            raise SquareErrors.PIN_FORBIDDEN
        try:
            statement = Statement.objects.select_related('user').prefetch_related('media').get(
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
        return quota_for_user(request.user)


class StatementUploadView(View):
    @auth.require_user
    @analyse.json(SquareParams.kind, SquareParams.file_name, SquareParams.content_type)
    def post(self, request: Request):
        if not request.user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        if request.json.kind not in {'image', 'audio', 'video'}:
            raise SquareErrors.MEDIA_INVALID
        return issue_message_upload(
            kind=request.json.kind,
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class StatementDetailView(View):
    @auth.require_user
    def get(self, request: Request, statement_id: int):
        return Statement.detail(request.user, statement_id, request=request)

    @auth.require_user
    def delete(self, request: Request, statement_id: int):
        try:
            statement = Statement.objects.get(id=statement_id, space=request.user.space, is_deleted=False)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        statement.delete_for(request.user)
        return dict(statement_id=statement.id, deleted=True)


class StatementLikeView(View):
    @auth.require_user
    def post(self, request: Request, statement_id: int):
        if not request.user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        try:
            statement = Statement.visible_for(request.user).get(id=statement_id)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        _like, created = StatementLike.objects.get_or_create(statement=statement, user=request.user)
        if created:
            NotificationEvent.emit_square_event(
                statement.user, request.user, NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE, statement.id,
            )
        return dict(liked=True, like_count=statement.likes.count())

    @auth.require_user
    def delete(self, request: Request, statement_id: int):
        try:
            statement = Statement.visible_for(request.user).get(id=statement_id)
        except Statement.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementLike.objects.filter(statement=statement, user=request.user).delete()
        return dict(liked=False, like_count=statement.likes.count())


class StatementCommentLikeView(View):
    @auth.require_user
    def post(self, request: Request, comment_id: int):
        if not request.user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        try:
            comment = StatementComment.objects.select_related('statement').get(id=comment_id, is_deleted=False)
        except StatementComment.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementComment.statement_for_user(request.user, comment.statement_id)
        _like, created = StatementCommentLike.objects.get_or_create(comment=comment, user=request.user)
        if created:
            NotificationEvent.emit_square_event(
                comment.user, request.user, NotificationEventTypeChoice.SQUARE_COMMENT_LIKE,
                comment.statement_id, comment.id,
            )
        return dict(liked=True, like_count=comment.likes.count())

    @auth.require_user
    def delete(self, request: Request, comment_id: int):
        try:
            comment = StatementComment.objects.select_related('statement').get(id=comment_id, is_deleted=False)
        except StatementComment.DoesNotExist:
            raise SquareErrors.NOT_EXISTS
        StatementComment.statement_for_user(request.user, comment.statement_id)
        StatementCommentLike.objects.filter(comment=comment, user=request.user).delete()
        return dict(liked=False, like_count=comment.likes.count())


class StatementCommentView(View):
    @auth.require_user
    @analyse.query(SquareParams.offset, SquareParams.limit)
    def get(self, request: Request, statement_id: int):
        return StatementComment.feed(
            request.user,
            statement_id=statement_id,
            offset=request.query.offset,
            limit=request.query.limit,
        )

    @auth.require_user
    @analyse.json(SquareParams.comment_text, SquareParams.parent_id)
    def post(self, request: Request, statement_id: int):
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
        media = StatementMedia.index_by_blob_slug(blob_slug)
        response = HttpResponseRedirect(sign_private_download_url(media.source_uri()))
        response['Cache-Control'] = 'private, max-age=86400'
        return response


class StatementMediaThumbnailView(View):
    def get(self, request: Request, blob_slug: str):
        media = StatementMedia.index_by_blob_slug(blob_slug)
        if media.kind not in {StatementMediaKindChoice.IMAGE, StatementMediaKindChoice.VIDEO}:
            raise SquareErrors.NOT_EXISTS
        thumbnail_uri = build_message_image_thumbnail_uri(media.source_uri(), width=480) if media.kind == StatementMediaKindChoice.IMAGE else build_message_video_thumbnail_uri(media.source_uri(), width=480)
        response = HttpResponseRedirect(thumbnail_uri)
        response['Cache-Control'] = 'private, max-age=86400'
        return response
