"""Chat creation and group management through the original business methods."""
from django.db import transaction
from django.views import View

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatPurposeChoice, ChatReadState
from Chat.validators import ChatValidator
from Friendship.models import Friendship
from User.models import User
from User.oauth import OAuthError
from User.oauth_actions import fields, text, account, peer
from User.oauth_chat import chat_for_user
from User.oauth_delegation import require_scope
from utils.chat_moderation import CHAT_MUTE_DURATIONS
from utils.content_safety import ContentSafetyScene, check_user_text


def member_payload(member):
    return dict(user=account(member.user),role=member.role,status=member.status,
                joined_at=member.joined_at.timestamp() if member.joined_at else None)


def collaboration_capabilities(user):
    return dict(create_group=bool(user.space.chat_enabled and user.has_capability('chat.group.create')
                                  and user.has_capability('chat.group.join')))


def allowed_group_actions(chat,user):
    if not chat.group or chat.submission or not chat.has_active_member(user): return []
    owner=chat.is_owner(user)
    actions=[]
    if user.has_capability('chat.group.invite'): actions.append('invite')
    if user.has_capability('chat.group.rename') and (not chat.is_space_group or user.is_official): actions.append('rename')
    if owner:
        actions.extend(('remove','background'))
        if not chat.is_space_group: actions.append('transfer_owner')
    else: actions.append('leave')
    if owner or user.can_operate_square: actions.append('mute')
    if owner or user.is_space_operator: actions.append('unmute')
    return actions


def chat_payload(chat, user):
    return dict(chat_id=chat.id,title=chat.title,group=chat.group,is_space_group=chat.is_space_group,
                is_owner=chat.is_owner(user),background_theme=chat.group_background_theme,
                allowed_actions=allowed_group_actions(chat,user),
                members=[member_payload(member) for member in chat.chat_members.filter(
                    status=ChatMemberStatusChoice.ACTIVE,user__is_deleted=False).select_related('user')])


def users_for_body(request, body):
    ids=body['user_ids']
    if not isinstance(ids,list) or len(ids)>100 or any(type(value) is not int or value<=0 or value>2**63-1 for value in ids) or len(set(ids))!=len(ids):
        raise OAuthError('invalid_request','Provide at most 100 unique integer user IDs')
    users=list(User.objects.filter(id__in=ids,space_id=request.oauth_grant.target_space_id,is_deleted=False))
    if len(users)!=len(ids):
        raise OAuthError('access_denied','Account unavailable')
    return users


def managed_group(request,chat_id):
    chat=chat_for_user(request,chat_id)
    if not chat.group:
        raise OAuthError('access_denied','Group conversation required')
    return chat


class OAuthDirectChatView(View):
    @require_scope('chat.write')
    def post(self,request):
        body=fields(request,('peer_user_id',))
        with transaction.atomic():
            chat=Chat.get_or_create_direct(request.user,peer(request,body))
        return dict(chat=chat_payload(chat,request.user))


class OAuthChatCapabilitiesView(View):
    @require_scope('chat.read')
    def get(self,request):
        return collaboration_capabilities(request.user)


class OAuthGroupCreateView(View):
    @require_scope('chat.write')
    def post(self,request):
        body=fields(request,('user_ids',),('title',))
        title=text(body,'title',ChatValidator.TITLE_MAX_LENGTH) if 'title' in body else None
        users=users_for_body(request,body)
        # Empty list creates an owner-only research group; no memberships are
        # fabricated. Subsequent additions still require friendship/capability.
        check_user_text(request,title or '',ContentSafetyScene.PROFILE)
        with transaction.atomic():
            if not collaboration_capabilities(request.user)['create_group']:
                raise OAuthError('access_denied','Current account cannot create groups')
            chat=Chat.create_group(request.user,users,title)
        return dict(chat=chat_payload(chat,request.user))


class OAuthChatDetailView(View):
    @require_scope('chat.read')
    def get(self,request,chat_id):
        return dict(chat=chat_payload(chat_for_user(request,chat_id),request.user))


class OAuthGroupManageView(View):
    @require_scope('chat.modify')
    def post(self,request,chat_id):
        # Named actions deliberately exclude group deletion and arbitrary fields.
        body=fields(request,('action',),('user_ids','peer_user_id','title','background_theme','duration'))
        action=body['action']
        expected={'invite':{'action','user_ids'},'remove':{'action','user_ids'},
            'transfer_owner':{'action','peer_user_id'},'rename':{'action','title'},
            'background':{'action','background_theme'},'mute':{'action','peer_user_id','duration'},
            'unmute':{'action','peer_user_id'},'leave':{'action'}}
        if not isinstance(action,str) or action not in expected or set(body)!=expected[action]:
            raise OAuthError('invalid_request','Unsupported group action or fields')
        with transaction.atomic():
            chat=managed_group(request,chat_id)
            # Serialize membership/owner changes against one group row.
            Chat.objects.select_for_update().get(pk=chat.pk)
            if action not in allowed_group_actions(chat,request.user):
                raise OAuthError('access_denied','The current group role does not allow this action')
            if action in ('invite','remove'):
                users=users_for_body(request,body)
                if not users: raise OAuthError('invalid_request','At least one user is required')
                if action=='invite': chat.invite_members(request.user,users)
                else:
                    if not chat.is_owner(request.user) or any(chat.is_owner(user) for user in users):
                        raise OAuthError('access_denied','Only the owner can remove non-owner members')
                    chat.remove_members(request.user,users)
            elif action=='transfer_owner': chat.transfer_ownership(request.user,peer(request,body))
            elif action=='rename':
                title=text(body,'title',ChatValidator.TITLE_MAX_LENGTH)
                check_user_text(request,title,ContentSafetyScene.PROFILE)
                chat.rename(request.user,title)
            elif action=='background':
                if not isinstance(body['background_theme'],str) or len(body['background_theme'])>32:
                    raise OAuthError('invalid_request','Invalid background theme')
                chat.set_group_background(request.user,body['background_theme'])
            elif action=='mute':
                if not isinstance(body['duration'],str) or body['duration'] not in CHAT_MUTE_DURATIONS or body['duration']=='permanent':
                    raise OAuthError('invalid_request','A supported finite group mute duration is required')
                chat.mute_member(request.user,peer(request,body),body['duration'])
            elif action=='unmute': chat.unmute_member(request.user,peer(request,body))
            else: chat.leave(request.user)
            # A departing member receives no group/member snapshot afterward.
            return dict(chat_id=chat.id,left=True) if action=='leave' else dict(chat=chat_payload(chat,request.user))


class OAuthGroupInvitationsView(View):
    @require_scope('chat.read')
    def get(self,request):
        rows=ChatMember.objects.filter(user=request.user,status=ChatMemberStatusChoice.PENDING,
            chat__space_id=request.oauth_grant.target_space_id,chat__purpose=ChatPurposeChoice.NORMAL,
            chat__is_deleted=False).select_related('chat','invited_by').order_by('-id')[:100]
        return dict(invitations=[dict(chat_id=row.chat_id,title=row.chat.title,
            invited_by=account(row.invited_by) if row.invited_by_id else None) for row in rows])


class OAuthGroupInvitationResponseView(View):
    @require_scope('chat.modify')
    def post(self,request,chat_id):
        body=fields(request,('accept',))
        if type(body['accept']) is not bool: raise OAuthError('invalid_request','Boolean accept required')
        with transaction.atomic():
            chat=Chat.objects.select_for_update().filter(pk=chat_id,space_id=request.oauth_grant.target_space_id,
                purpose=ChatPurposeChoice.NORMAL,is_deleted=False).first()
            if not chat or not chat.group or not ChatMember.objects.filter(chat=chat,user=request.user,status=ChatMemberStatusChoice.PENDING).exists():
                raise OAuthError('access_denied','Invitation unavailable')
            if body['accept']:
                request.user.space.require_group_join_allowed(request.user)
                if chat.is_space_group and (not request.user.space.space_group_enabled or not (request.user.is_official or request.user.has_password)):
                    raise OAuthError('access_denied','Space conversation unavailable')
            chat.respond_invite(request.user,body['accept'])
        return dict(chat_id=chat.id,accepted=body['accept'])


class OAuthChatReadView(View):
    @require_scope('chat.modify')
    def post(self,request,chat_id):
        fields(request,())
        state=ChatReadState.mark_read(chat_for_user(request,chat_id),request.user)
        return dict(chat_id=chat_id,last_read_at=state.last_read_at.timestamp() if state.last_read_at else None)


class OAuthContactSearchView(View):
    @require_scope('contacts.read')
    def get(self,request):
        if set(request.GET)!={'name'} or len(request.GET.getlist('name'))!=1:
            raise OAuthError('invalid_request','Provide one exact account name')
        name=text(request.GET,'name',20)
        request.user.require_capability('contacts.search')
        user=User.objects.filter(space_id=request.oauth_grant.target_space_id,is_deleted=False,
            lower_name=User.normalizers.lower_name(name)).exclude(pk=request.user.id).first()
        relation=Friendship.between(request.user,user) if user else None
        return dict(user=account(user) if user else None,relationship=relation.status if relation else None)
