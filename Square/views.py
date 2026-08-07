from django.db import transaction
from django.http import HttpResponseRedirect
from django.views import View
from oba import raw
from smartdjango import analyse

from Square.models import Statement, StatementMedia, StatementMediaKindChoice
from Square.params import SquareParams
from Square.validators import SquareErrors
from utils import auth
from utils.auth import Request
from utils.qiniu import (
    build_message_image_thumbnail_uri,
    issue_message_upload,
    sign_private_download_url,
)


class StatementView(View):
    @auth.require_user
    @analyse.query(SquareParams.before, SquareParams.limit)
    def get(self, request: Request):
        return Statement.feed(
            request.user,
            before=request.query.before,
            limit=request.query.limit,
            request=request,
        )

    @auth.require_user
    @analyse.json(SquareParams.text, SquareParams.visibility, SquareParams.media)
    def post(self, request: Request):
        with transaction.atomic():
            statement = Statement.create_statement(
                user=request.user,
                text=request.json.text,
                visibility=request.json.visibility,
                media=raw(request.json.media),
            )
        return statement.jsonl(request=request)


class StatementUploadView(View):
    @auth.require_user
    @analyse.json(SquareParams.kind, SquareParams.file_name, SquareParams.content_type)
    def post(self, request: Request):
        if not request.user.verified:
            raise SquareErrors.PUBLISH_REQUIRES_VERIFICATION
        if request.json.kind not in {'image', 'audio'}:
            raise SquareErrors.MEDIA_INVALID
        return issue_message_upload(
            kind=request.json.kind,
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class StatementMediaView(View):
    def get(self, request: Request, blob_slug: str):
        media = StatementMedia.index_by_blob_slug(blob_slug)
        response = HttpResponseRedirect(sign_private_download_url(media.source_uri()))
        response['Cache-Control'] = 'private, max-age=86400'
        return response


class StatementMediaThumbnailView(View):
    def get(self, request: Request, blob_slug: str):
        media = StatementMedia.index_by_blob_slug(blob_slug)
        if media.kind != StatementMediaKindChoice.IMAGE:
            raise SquareErrors.NOT_EXISTS
        response = HttpResponseRedirect(build_message_image_thumbnail_uri(media.source_uri(), width=480))
        response['Cache-Control'] = 'private, max-age=86400'
        return response
