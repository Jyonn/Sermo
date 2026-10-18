"""Text-only, field-allowlisted adapters. Never proxy an ordinary session API."""
from django.db import transaction
from django.db.models import Q
from django.views import View

from Chat.models import Chat, ChatPurposeChoice, Submission, SubmissionStatusChoice
from Chat.validators import ChatValidator
from Friendship.models import Friendship
from Message.models import Message, MessageTypeChoice
from Message.validators import MessageValidator
from Square.models import Statement, StatementComment
from User.models import User, NotificationEvent, NotificationEventTypeChoice
from User.oauth import OAuthError
from User.oauth_chat import integer, serialize_message
from User.oauth_delegation import require_scope
from User.oauth_views import _payload
from utils.content_safety import ContentSafetyScene, check_user_text


def fields(request, required, optional=()):
    body = _payload(request)
    if not set(required) <= set(body) or set(body) - set(required) - set(optional):
        raise OAuthError('invalid_request', 'Unexpected or missing fields')
    return body


def text(body, key, maximum):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise OAuthError('invalid_request', 'Invalid text field')
    return value


def account(user):
    return dict(user_id=user.id, name=user.name)


def peer(request, body):
    user = User.objects.filter(pk=integer(body, 'peer_user_id'), space_id=request.oauth_grant.target_space_id, is_deleted=False).first()
    if not user or user.id == request.user.id:
        raise OAuthError('access_denied', 'Account unavailable')
    return user


def statement_payload(item):
    return dict(statement_id=item.id, user=None if item.is_anonymous else account(item.user),
                is_anonymous=item.is_anonymous, text=item.text, created_at=item.created_at.timestamp())


def comment_payload(item):
    return dict(comment_id=item.id, statement_id=item.statement_id, parent_id=item.parent_id,
                user=None if item.is_anonymous else account(item.user), is_anonymous=item.is_anonymous,
                text=item.text if not item.sticker_asset_id else None, created_at=item.created_at.timestamp())


class OAuthSquareView(View):
    @require_scope('square.read')
    def get(self, request):
        scope = request.GET.get('scope', 'friends')
        if scope not in ('all', 'friends', 'mine'):
            raise OAuthError('invalid_request', 'Invalid feed scope')
        request.user.space.require_square_enabled(scope=scope)
        rows = Statement._apply_feed_scope(Statement.visible_for(request.user), request.user, scope=scope)
        rows = Statement._apply_before_cursor(rows, request.user.space, integer(request.GET, 'before'))
        limit = integer(request.GET, 'limit', 20, 100)
        rows = list(rows.select_related('user').order_by('-created_at', '-id')[:limit+1])
        return dict(statements=[statement_payload(row) for row in rows[:limit]], has_more=len(rows)>limit,
                    next_before=rows[min(limit,len(rows))-1].id if rows else None)

    @require_scope('square.write')
    def post(self, request):
        body = fields(request, ('text', 'visibility'))
        content = text(body, 'text', 140)
        if body['visibility'] not in ('public', 'friends'):
            raise OAuthError('invalid_request', 'Invalid visibility')
        request.user.space.require_square_free_post_enabled()
        check_user_text(request, content, ContentSafetyScene.SOCIAL)
        with transaction.atomic():
            item = Statement.create_statement(request.user, content, body['visibility'], media=[])
        return dict(statement=statement_payload(item))


class OAuthSquareCommentsView(View):
    @require_scope('square.read')
    def get(self, request, statement_id):
        statement = StatementComment.statement_for_user(request.user, statement_id)
        rows = StatementComment.objects.filter(statement=statement, is_deleted=False, id__gt=integer(request.GET, 'after'))
        limit = integer(request.GET, 'limit', 30, 100)
        rows = list(rows.select_related('user').order_by('id')[:limit+1])
        return dict(comments=[comment_payload(row) for row in rows[:limit]], has_more=len(rows)>limit,
                    next_after=rows[min(limit,len(rows))-1].id if rows else integer(request.GET, 'after'))

    @require_scope('square.write')
    def post(self, request, statement_id):
        body = fields(request, ('text',))
        content = text(body, 'text', 140)
        check_user_text(request, content, ContentSafetyScene.COMMENT)
        with transaction.atomic():
            item = StatementComment.create_comment(request.user, statement_id, content)
            NotificationEvent.emit_square_event(item.statement.user, request.user,
                NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT, statement_id, item.id)
        return dict(comment=comment_payload(item))


def relation_payload(item, user):
    other = item.user_high if item.user_low_id == user.id else item.user_low
    return dict(request_id=item.id, peer=account(other), status=item.status,
                outgoing=item.requested_by_id == user.id)


class OAuthContactsView(View):
    @require_scope('contacts.read')
    def get(self, request):
        friends = [dict(user=account(user), request_id=relation.id) for user, relation in Friendship.friend_relations_of(request.user)]
        requests = Friendship.request_history_incoming(request.user) + Friendship.request_history_outgoing(request.user)
        groups = [dict(chat_id=chat.id, title=chat.title) for chat in Chat.get_user_chats(request.user) if chat.group] if request.user.space.chat_enabled else []
        return dict(friends=friends, requests=[relation_payload(item,request.user) for item in requests], groups=groups)

    @require_scope('contacts.write')
    def post(self, request):
        body = fields(request, ('peer_user_id',))
        with transaction.atomic():
            item = Friendship.create(request.user, peer(request,body), source=Friendship.SOURCE_DIRECT)
        return dict(request=relation_payload(item,request.user))


class OAuthContactResponseView(View):
    @require_scope('contacts.modify')
    def post(self, request, request_id):
        body = fields(request, ('accept',))
        if type(body['accept']) is not bool:
            raise OAuthError('invalid_request', 'Boolean accept required')
        with transaction.atomic():
            item = Friendship.objects.select_for_update().filter(pk=request_id,space=request.user.space,user_low__is_deleted=False,user_high__is_deleted=False).filter(Q(user_low=request.user)|Q(user_high=request.user)).first()
            if not item:
                raise OAuthError('access_denied', 'Request unavailable')
            item.accept(request.user) if body['accept'] else item.reject(request.user)
        return dict(request=relation_payload(item,request.user))


def submission_for_user(request, chat_id):
    chat = Chat.objects.filter(pk=chat_id, space_id=request.oauth_grant.target_space_id, purpose=ChatPurposeChoice.SUBMISSION, is_deleted=False).first()
    if not chat or not chat.has_active_member(request.user):
        raise OAuthError('access_denied', 'Submission unavailable')
    item = Submission.objects.get(chat=chat)
    role = item.role_for(request.user)
    if role not in ('author','reviewer') or role == 'reviewer' and item.status == SubmissionStatusChoice.DRAFT:
        raise OAuthError('access_denied', 'Submission unavailable')
    return item


def submission_payload(item, user):
    return dict(chat_id=item.chat_id, title=item.chat.title, status=item.STATUS_NAMES[item.status], role=item.role_for(user))


def submission_message(request, chat, body):
    content = text(body, 'content', MessageValidator.MAX_CONTENT_LENGTH)
    key = text(body, 'client_message_id', MessageValidator.MAX_CLIENT_MESSAGE_ID_LENGTH)
    check_user_text(request, content, ContentSafetyScene.FORUM, title=chat.title)
    return Message.create(chat, request.user, MessageTypeChoice.TEXT, content, client_message_id=key)


class OAuthSubmissionsView(View):
    @require_scope('submission.read')
    def get(self, request):
        rows = {chat.id: chat for role in ('author','reviewer') for chat in Chat.get_user_chats(request.user,purpose=ChatPurposeChoice.SUBMISSION,submission_role=role)}
        return dict(submissions=[submission_payload(chat.submission_record,request.user) for chat in rows.values()])

    @require_scope('submission.write')
    def post(self, request):
        body = fields(request, ('peer_user_id','title','client_draft_id','content','client_message_id'))
        title = text(body,'title',ChatValidator.TITLE_MAX_LENGTH)
        draft = text(body,'client_draft_id',64)
        check_user_text(request,title,ContentSafetyScene.FORUM,title=title)
        with transaction.atomic():
            chat, created = Chat.create_submission(request.user, peer(request,body), title, draft)
            message = submission_message(request, chat, body)
            if created: chat._emit_state_changed()
        return dict(submission=submission_payload(chat.submission_record,request.user), message=serialize_message(message))


class OAuthSubmissionMessagesView(View):
    @require_scope('submission.read')
    def get(self, request, chat_id):
        item = submission_for_user(request,chat_id)
        rows = Message.visible_for_user(item.chat,request.user).filter(id__gt=integer(request.GET,'after')).select_related('user').order_by('id')
        limit = integer(request.GET,'limit',30,100)
        rows = list(rows[:limit+1])
        return dict(submission=submission_payload(item,request.user), messages=[serialize_message(row) for row in rows[:limit]], has_more=len(rows)>limit,
                    next_after=rows[min(limit,len(rows))-1].id if rows else integer(request.GET,'after'))

    @require_scope('submission.write')
    def post(self, request, chat_id):
        body = fields(request,('content','client_message_id'))
        with transaction.atomic():
            item = submission_for_user(request,chat_id)
            message = submission_message(request,item.chat,body)
        return dict(message=serialize_message(message))


class OAuthSubmissionSubmitView(View):
    @require_scope('submission.write')
    def post(self, request, chat_id):
        fields(request,())
        with transaction.atomic():
            item = submission_for_user(request,chat_id)
            item = Submission.objects.select_for_update().get(pk=item.pk)
            item.submit(request.user)
        return dict(submission=submission_payload(item,request.user))


class OAuthSubmissionReviewView(View):
    @require_scope('submission.modify')
    def post(self, request, chat_id):
        body = fields(request,('action',))
        # Termination/publication/deletion require their own explicit future design.
        if body['action'] not in ('revision','ready'):
            raise OAuthError('invalid_request', 'Only revision or ready is supported')
        with transaction.atomic():
            item = submission_for_user(request,chat_id)
            item = Submission.objects.select_for_update().get(pk=item.pk)
            item.review(request.user,body['action'])
        return dict(submission=submission_payload(item,request.user))


PERSONAL_FIELDS = ('language','language_preference','chat_bubble_style','avatar_frame_style','statement_card_style','show_self_avatar','profile_card_theme')
STYLE_FIELDS = PERSONAL_FIELDS[2:]


def personal_payload(user):
    return dict(user_id=user.id,space_id=user.space_id,name=user.name,**{field:getattr(user,field) for field in PERSONAL_FIELDS})


class OAuthPersonalView(View):
    @require_scope('personal.read')
    def get(self, request):
        return personal_payload(request.user)

    @require_scope('personal.modify')
    def post(self, request):
        body = fields(request,(),('name','language_preference','system_language',*STYLE_FIELDS))
        if not body or 'system_language' in body and 'language_preference' not in body:
            raise OAuthError('invalid_request', 'No supported update')
        for key,value in body.items():
            if key == 'show_self_avatar':
                if type(value) is not bool: raise OAuthError('invalid_request','Boolean value required')
            elif not isinstance(value,str) or len(value)>100:
                raise OAuthError('invalid_request','Invalid personal setting')
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if 'name' in body:
                from User.views import _require_profile_edit_enabled
                _require_profile_edit_enabled(user)
                check_user_text(request,body['name'],ContentSafetyScene.PROFILE,nickname=body['name'])
                user.set_name(body['name'])
            if 'language_preference' in body:
                user.set_language_preference(body['language_preference'],system_language=body.get('system_language'))
            if set(body) & set(STYLE_FIELDS):
                user.set_personalization(**{key:body.get(key,getattr(user,key)) for key in STYLE_FIELDS})
        return personal_payload(user)
