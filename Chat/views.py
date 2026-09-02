from django.db import transaction
from django.views import View
from smartdjango import analyse, OK

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatPurposeChoice, ChatReadState, ChatUserPreference
from Chat.params import ChatParams, ChatMemberParams, ChatPreferenceParams
from Chat.validators import ChatErrors
from Message.models import MediaResource, Message, MessageTypeChoice
from Message.params import MessageParams
from Message.validators import MessageErrors
from Friendship.models import Friendship, FriendshipStatusChoice
from Space.models import SpaceOperator
from utils import auth


class ChatListView(View):
    @staticmethod
    def build_chat_payload(chat, user, request):
        data = chat.jsonl()
        if chat.submission:
            data['submission'] = chat.submission_record.jsonl()
            data['submission_role'] = chat.submission_record.role_for(user)
        last_message = Message.latest_preview_for_user(chat, user)
        if last_message is not None:
            data['last_message'] = last_message.jsonl(request=request)
            data['last_chat_at'] = last_message.created_at.timestamp()
        else:
            data['last_message'] = None
            data['last_chat_at'] = chat.created_at.timestamp()
        preference = ChatUserPreference.objects.filter(chat=chat, user=user).first()
        data['unread_count'] = ChatReadState.unread_count(chat, user)
        data['has_unread_mention'] = ChatReadState.has_unread_mention(chat, user) if chat.group else False
        last_read_at = ChatReadState.get_last_read_at(chat, user)
        data['last_read_at'] = last_read_at.timestamp() if last_read_at else None
        data['pinned'] = bool(preference and preference.pinned)
        data['online_reminder_enabled'] = bool(preference and preference.online_reminder_enabled)
        data['statement_reminder_enabled'] = bool(preference and preference.statement_reminder_enabled)
        data['notifications_muted'] = bool(preference and preference.notifications_muted)
        data['unread_badge_muted'] = bool(preference and preference.unread_badge_muted)
        return data

    @auth.require_user
    def get(self, request):
        request.user.space.require_chat_enabled()
        purpose = ChatPurposeChoice.SUBMISSION if request.GET.get('purpose') == 'submission' else ChatPurposeChoice.NORMAL
        if purpose == ChatPurposeChoice.SUBMISSION:
            request.user.space.require_submission_enabled()
        submission_role = request.GET.get('role') if purpose == ChatPurposeChoice.SUBMISSION else None
        if submission_role not in (None, 'author', 'reviewer'):
            raise ChatErrors.FORBIDDEN
        chats = Chat.get_user_chats(request.user, purpose=purpose, submission_role=submission_role)
        payloads = [self.build_chat_payload(chat, request.user, request) for chat in chats]
        payloads.sort(key=lambda item: (bool(item['pinned']), item['last_chat_at']), reverse=True)
        return payloads


class DirectChatView(View):
    @auth.require_user
    @analyse.json(ChatParams.peer_user_id)
    def post(self, request):
        request.user.space.require_chat_enabled()
        chat = Chat.get_or_create_direct(request.user, request.json.peer_user)
        return chat.json()


class GroupChatView(View):
    @auth.require_user
    @analyse.json(ChatParams.users, ChatParams.title.copy().null().default(None))
    def post(self, request):
        request.user.space.require_chat_enabled()
        chat = Chat.create_group(request.user, request.json.users, request.json.title)
        return chat.json()

    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: Chat = request.query.chat
        if not chat.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=chat.id)
        chat.remove()
        return OK


class SubmissionRecipientView(View):
    @staticmethod
    def _relationship(user, target):
        if user.id == target.id:
            return 'self'
        relation = Friendship.between(user, target)
        if relation is None:
            return 'none'
        return {
            FriendshipStatusChoice.PENDING: 'pending',
            FriendshipStatusChoice.ACCEPTED: 'friend',
        }.get(relation.status, 'none')

    @auth.require_user
    def get(self, request):
        request.user.space.require_submission_enabled()
        recipients = []
        official = request.user.space.official_user
        if official and not official.is_deleted and official.id != request.user.id:
            recipients.append(dict(
                user=official.json_friend(),
                relationship=self._relationship(request.user, official),
                role='official',
            ))
        for item in SpaceOperator.objects.filter(space=request.user.space).select_related('user'):
            if item.user_id == request.user.id or item.user.is_deleted:
                continue
            recipients.append(dict(
                user=item.user.json_friend(),
                relationship=self._relationship(request.user, item.user),
                role='operator',
            ))
        return recipients


class SubmissionStartView(View):
    @auth.require_user
    @analyse.json(
        ChatParams.peer_user_id,
        ChatParams.title,
        ChatParams.client_draft_id,
        MessageParams.content,
        MessageParams.type,
        MessageParams.client_message_id,
        MessageParams.resource_id,
    )
    def post(self, request):
        request.user.space.require_submission_enabled()
        if request.json.type in (MessageTypeChoice.SYSTEM, MessageTypeChoice.FORWARD_BUNDLE, MessageTypeChoice.OFFICIAL_NOTICE):
            raise MessageErrors.SYSTEM_MESSAGE_FORBIDDEN
        with transaction.atomic():
            chat, created = Chat.create_submission(
                request.user,
                request.json.peer_user,
                request.json.title,
                request.json.client_draft_id,
            )
            media_resource = MediaResource.objects.select_related('asset').filter(
                id=request.json.resource_id,
            ).first() if request.json.resource_id else None
            if request.json.resource_id and media_resource is None:
                raise MessageErrors.MEDIA_ASSET_INVALID
            message = Message.create(
                chat=chat,
                user=request.user,
                message_type=request.json.type,
                content=request.json.content,
                client_message_id=request.json.client_message_id,
                media_resource=media_resource,
            )
            if created:
                chat._emit_state_changed()
        payload = ChatListView.build_chat_payload(chat, request.user, request)
        return dict(chat=payload, message=message.jsonl(request=request))


class SubmissionSubmitView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        chat = request.query.chat
        if not chat.submission:
            raise ChatErrors.FORBIDDEN
        chat.submission_record.submit(request.user)
        return ChatListView.build_chat_payload(chat, request.user, request)


class SubmissionStatusView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @analyse.json(ChatParams.submission_action)
    @auth.require_chat_member()
    def post(self, request):
        chat = request.query.chat
        if not chat.submission:
            raise ChatErrors.FORBIDDEN
        chat.submission_record.review(request.user, request.json.submission_action)
        return ChatListView.build_chat_payload(chat, request.user, request)


class SubmissionInviteReviewView(View):
    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.user_id, ChatMemberParams.accept)
    def post(self, request):
        member = ChatMember.objects.filter(chat=request.query.chat, user=request.json.user).select_related('chat').first()
        if member is None:
            raise ChatErrors.FORBIDDEN
        member.review_submission_invite(request.user, bool(request.json.accept))
        return request.query.chat.json()

    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    def get(self, request):
        chat = request.query.chat
        if not chat.submission or not (request.user.is_official or request.user.is_space_operator):
            raise ChatErrors.FORBIDDEN
        return [
            member.json()
            for member in ChatMember.objects.filter(
                chat=chat,
                status=ChatMemberStatusChoice.PENDING,
            ).select_related('user', 'invited_by')
        ]


class GroupChatNameView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @analyse.json(ChatParams.title)
    @auth.require_chat_member()
    def post(self, request):
        chat: Chat = request.query.chat
        chat.rename(request.user, request.json.title)
        return chat.json()


class GroupChatMemberView(View):
    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.users)
    @auth.require_chat_member()
    def post(self, request):
        chat: Chat = request.query.chat
        chat.invite_members(request.user, request.json.users)
        return chat.json()

    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.users)
    @auth.require_chat_owner()
    def delete(self, request):
        chat: Chat = request.query.chat
        chat.remove_members(request.user, request.json.users)
        return chat.json()


class GroupChatOwnerView(View):
    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.user_id)
    @auth.require_chat_owner()
    def post(self, request):
        chat: Chat = request.query.chat
        chat.transfer_ownership(request.user, request.json.user)
        return chat.json()


class GroupChatInviteRespondView(View):
    @auth.require_user
    @analyse.query(ChatMemberParams.chat_id)
    @analyse.json(ChatMemberParams.accept)
    def post(self, request):
        chat: Chat = request.query.chat
        chat.respond_invite(request.user, bool(request.json.accept))
        return chat.json()


class GroupChatInviteListView(View):
    @auth.require_user
    def get(self, request):
        return ChatMember.pending_for_user(request.user, limit=100)


class GroupChatLeaveView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        chat: Chat = request.query.chat
        if not chat.group:
            raise ChatErrors.NOT_GROUP_CHAT(chat=chat.id)
        chat.leave(request.user)
        return OK


class ChatReadView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def post(self, request):
        state = ChatReadState.mark_read(request.query.chat, request.user)
        return dict(last_read_at=state.last_read_at.timestamp())


class ChatPreferenceView(View):
    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @auth.require_chat_member()
    def get(self, request):
        return ChatUserPreference.ensure(request.query.chat, request.user).json()

    @auth.require_user
    @analyse.query(ChatParams.chat_id)
    @analyse.json(
        ChatPreferenceParams.pinned,
        ChatPreferenceParams.online_reminder_enabled,
        ChatPreferenceParams.statement_reminder_enabled,
        ChatPreferenceParams.notifications_muted,
        ChatPreferenceParams.unread_badge_muted,
    )
    @auth.require_chat_member()
    def post(self, request):
        if request.json.online_reminder_enabled:
            request.user.require_capability('chat.reminder.online')
        preference = ChatUserPreference.update(
            request.query.chat,
            request.user,
            pinned=request.json.pinned,
            online_reminder_enabled=request.json.online_reminder_enabled,
            statement_reminder_enabled=request.json.statement_reminder_enabled,
            notifications_muted=request.json.notifications_muted,
            unread_badge_muted=request.json.unread_badge_muted,
        )
        return preference.json()
