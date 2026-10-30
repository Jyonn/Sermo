"""Space-gated research workspace; chat membership is the authority source."""
import json
import uuid
from Chat.research_status import research_status, normalize_tasks
from datetime import date
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from Chat.models import Chat, ChatMember, ChatPurposeChoice, ChatTypeChoice, ChatMemberRoleChoice, ChatMemberStatusChoice, ResearchProject
from Chat.validators import ChatErrors
from User.models import User
from utils import auth
from utils.content_safety import check_user_text, ContentSafetyScene


def fail(message, status=400):
    return JsonResponse({'code': status, 'message': message, 'body': None}, status=status)


def text(body, key, limit, required=True):
    value = body.get(key, '')
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('Invalid ' + key)
    return value.strip()


def period(body):
    start = date.fromisoformat(body['start'])
    end = date.fromisoformat(body['end']) if body.get('end') else None
    if end and end < start:
        raise ValueError('Invalid date range')
    return start, end


def members(project):
    return project.chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE, user__is_deleted=False).select_related('user')


def payload(project):
    from Chat.models import SubmissionInvite, SubmissionInviteStatusChoice
    rows = list(members(project))
    return dict(id=project.pk, chat_id=project.chat_id, title=project.chat.title, description=project.description,
                start=project.start.isoformat(), end=project.end.isoformat() if project.end else None,
                status=research_status(project.status), revision=project.revision, tasks=normalize_tasks(project.tasks),
                pending_invitee_ids=list(SubmissionInvite.objects.filter(chat=project.chat,status=SubmissionInviteStatusChoice.PENDING,expires_at__gt=timezone.now()).values_list('invitee_id',flat=True)),
                members=[dict(id=row.user_id, name=row.user.name, owner=row.role == ChatMemberRoleChoice.OWNER,
                              **row.user.dictify('avatar_uri', 'avatar_cache_key', 'avatar_frame_style')) for row in rows])


class ResearchView(View):
    @auth.require_user
    def get(self, request, project_id=None):
        if not request.user.space.research_enabled:
            return fail('Research is disabled in this space', 403)
        rows = ResearchProject.objects.filter(chat__space_id=request.user.space_id, chat__is_deleted=False,
            chat__chat_members__user=request.user, chat__chat_members__status=ChatMemberStatusChoice.ACTIVE).select_related('chat')
        if project_id:
            project = rows.filter(pk=project_id).first()
            return payload(project) if project else fail('Project unavailable', 404)
        return [dict(id=p.pk, chat_id=p.chat_id, title=p.chat.title, status=research_status(p.status), start=p.start.isoformat(), end=p.end.isoformat() if p.end else None) for p in rows.order_by('-updated_at')[:200]]

    @auth.require_user
    def post(self, request, project_id=None):
        if not request.user.space.research_enabled:
            return fail('Research is disabled in this space', 403)
        try:
            if len(request.body) > 100000:
                return fail('Request too large', 413)
            body = json.loads(request.body)
            if not isinstance(body, dict):
                raise ValueError('Invalid request')
            calendar_revoke = None
            if project_id and body.get('action') in ('leave','remove','dissolve'):
                from Chat.research_calendar import project_for
                from User.chronelle_account import revocation_credentials, BindingFailure
                from User.chronelle_origins import authorization_site
                try:
                    snapshot = project_for(request.user,project_id)
                    if body['action'] != 'leave' and not snapshot.chat.is_owner(request.user):
                        return fail('Only the project owner can manage this project',403)
                    base,_ = authorization_site(request)
                    tokens = {}
                    for display in snapshot.calendar_displays:
                        if display['until'] > timezone.now().timestamp():
                            tokens[display['owner']] = revocation_credentials(User.objects.get(pk=display['owner']),base)
                    calendar_revoke = (base,tokens)
                except BindingFailure as error:
                    return fail(error.code,error.status)
            with transaction.atomic():
                if project_id is None:
                    request.user.require_capability('chat.group.create')
                    if ResearchProject.objects.filter(chat__space_id=request.user.space_id, chat__is_deleted=False).count() >= 200:
                        return fail('Project limit reached')
                    title = text(body, 'title', 50)
                    description = text(body, 'description', 20000, False)
                    start, end = period(body)
                    check_user_text(request, title+'\n'+description, ContentSafetyScene.PROFILE)
                    chat = Chat.objects.create(space=request.user.space, chat_type=ChatTypeChoice.GROUP, purpose=ChatPurposeChoice.RESEARCH, title=title, created_by=request.user)
                    ChatMember.objects.create(chat=chat, user=request.user, role=ChatMemberRoleChoice.OWNER, status=ChatMemberStatusChoice.ACTIVE, joined_at=timezone.now(), invited_by=request.user)
                    project = ResearchProject.objects.create(chat=chat, description=description, start=start, end=end)
                else:
                    project = ResearchProject.objects.select_for_update().select_related('chat').filter(pk=project_id, chat__space_id=request.user.space_id, chat__is_deleted=False).first()
                    if not project or not project.chat.has_active_member(request.user):
                        return fail('Project unavailable', 404)
                    if type(body.get('revision')) is not int or body['revision'] != project.revision:
                        return fail('Project changed. Refresh and try again.', 409)
                    project.status = research_status(project.status)
                    project.tasks = normalize_tasks(project.tasks)
                    action = body.get('action')
                    owner = project.chat.is_owner(request.user)
                    if action not in ('progress', 'leave') and not owner:
                        return fail('Only the project owner can manage this project', 403)
                    if project.status == 'archived' and action not in ('settings', 'leave', 'dissolve', 'transfer'):
                        return fail('Archived projects are read-only', 403)
                    active = set(members(project).values_list('user_id', flat=True))
                    if calendar_revoke:
                        from Chat.research_calendar import revoke_displays
                        try:
                            revoke_displays(project,*calendar_revoke)
                        except BindingFailure as error:
                            return fail(error.code,error.status)
                    if action == 'invite':
                        user = User.objects.filter(pk=body.get('user_id'), space_id=request.user.space_id, is_deleted=False).first()
                        if not user:
                            return fail('Member unavailable', 404)
                        if user.pk in active:
                            return fail('User is already a project member')
                        request.user.require_capability('chat.group.invite')
                        Chat._require_friend_of(request.user, user)
                        request.user.space.require_group_join_allowed(user)
                        from Chat.models import SubmissionInvite, SubmissionMemberRoleChoice
                        from Message.models import Message
                        invitation = SubmissionInvite.create_invitation(project.chat, request.user, user, SubmissionMemberRoleChoice.AUTHOR)
                        Message.create_submission_invite(invitation)
                    elif action in ('leave', 'remove'):
                        user_id = request.user.pk if action == 'leave' else body.get('user_id')
                        member = members(project).filter(user_id=user_id).first()
                        if not member or member.role == ChatMemberRoleChoice.OWNER:
                            return fail('Transfer ownership before leaving or removing the owner')
                        member.status = ChatMemberStatusChoice.LEFT if action == 'leave' else ChatMemberStatusChoice.KICKED
                        member.left_at = timezone.now()
                        member.save()
                    elif action == 'transfer':
                        target = members(project).filter(user_id=body.get('user_id')).first()
                        if not target or target.user_id == request.user.pk:
                            return fail('Choose another active member')
                        members(project).filter(user=request.user).update(role=ChatMemberRoleChoice.MEMBER)
                        target.role = ChatMemberRoleChoice.OWNER
                        target.save()
                        from Chat.models import SubmissionInvite, SubmissionInviteStatusChoice
                        SubmissionInvite.objects.filter(chat=project.chat,status=SubmissionInviteStatusChoice.PENDING).update(status=SubmissionInviteStatusChoice.EXPIRED)
                    elif action == 'dissolve':
                        project.chat.is_deleted = True
                        project.chat.save(update_fields=['is_deleted'])
                    elif action == 'settings':
                        if not any(key in body for key in ('status', 'description', 'start', 'end')):
                            raise ValueError('No settings provided')
                        if 'status' in body:
                            project.status = research_status(body['status'])
                        if 'description' in body:
                            description = text(body, 'description', 20000, False)
                            check_user_text(request, description, ContentSafetyScene.PROFILE)
                            project.description = description
                        if 'start' in body or 'end' in body:
                            project.start, project.end = period(dict(start=body.get('start', project.start.isoformat()), end=body.get('end', project.end.isoformat() if project.end else None)))
                    elif action == 'task':
                        if len(project.tasks) >= 100:
                            raise ValueError('Task limit reached')
                        title = text(body, 'title', 160)
                        content = text(body, 'body', 20000)
                        start, end = period(body)
                        assignees = body.get('assignees')
                        if not isinstance(assignees, list) or not assignees or any(type(i) is not int or i not in active for i in assignees):
                            raise ValueError('Choose active members')
                        check_user_text(request, title+'\n'+content, ContentSafetyScene.PROFILE)
                        project.tasks.append(dict(id=str(uuid.uuid4()), title=title, body=content, start=start.isoformat(), end=end.isoformat() if end else None, assignees=list(set(assignees)), status='active', progress=[]))
                    elif action in ('task_status', 'task_dates', 'progress'):
                        task = next((t for t in project.tasks if t['id'] == body.get('task_id')), None)
                        if not task:
                            return fail('Task unavailable', 404)
                        if action == 'task_status':
                            task['status'] = research_status(body.get('status'))
                        elif action == 'task_dates':
                            start, end = period(body)
                            task['start'] = start.isoformat()
                            task['end'] = end.isoformat() if end else None
                        else:
                            if task['status'] == 'archived':
                                return fail('Archived tasks are read-only', 403)
                            if len(task['progress']) >= 500:
                                raise ValueError('Progress limit reached')
                            content = text(body, 'body', 20000)
                            reply = body.get('reply_to')
                            if reply and not any(p['id'] == reply for p in task['progress']):
                                raise ValueError('Reply unavailable')
                            check_user_text(request, content, ContentSafetyScene.PROFILE)
                            task['progress'].insert(0, dict(id=str(uuid.uuid4()), user_id=request.user.pk, body=content, created_at=timezone.now().isoformat(), reply_to=reply))
                    else:
                        raise ValueError('Unknown action')
                    if len(json.dumps(project.tasks)) > 2000000:
                        raise ValueError('Project content limit reached')
                    project.revision += 1
                    project.save()
                    from Chat.research_notifications import notify_research
                    notify_research(project,request.user,action,body)
                if project_id is None or body.get('action') in ('invite', 'remove', 'leave', 'transfer', 'dissolve'):
                    previous_members = active if project_id else {request.user.pk}
                    transaction.on_commit(lambda: project.chat._emit_state_changed(previous_members))
                return payload(project)
        except (ValueError, TypeError, KeyError):
            return fail('Invalid research request')
