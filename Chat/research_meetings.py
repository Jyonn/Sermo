"""Explicit participant invitations, never writes directly into peers' calendars."""
import json
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View
from Chat.models import ResearchMeeting, ResearchProject, ChatMemberStatusChoice
from Chat.research import fail
from Chat.research_calendar import project_for
from User.chronelle_account import BindingFailure, credentials, remote
from User.chronelle_views import authorization_site, APP_ORIGINS
from utils import auth
from utils.content_safety import check_user_text, ContentSafetyScene


def meeting_json(row):
    return dict(id=str(row.pk),creator_id=row.creator_id,status=row.status,
        invitation_id=str(row.invitation_id) if row.invitation_id else None,
        **{key:value for key,value in row.payload.items() if not key.startswith('_')})


def validate(body,user_id):
    command=uuid.UUID(body['command_id'])
    if command.version!=4:raise ValueError()
    start,end=parse_datetime(body['start']),parse_datetime(body['end'])
    if not start or not end or timezone.is_naive(start) or timezone.is_naive(end) or not timedelta(0)<end-start<=timedelta(days=7):raise ValueError()
    ZoneInfo(body['timezone'])
    if type(body.get('all_day')) is not bool:raise ValueError()
    if body['all_day'] and any(value.astimezone(ZoneInfo(body['timezone'])).time().isoformat()!='00:00:00' for value in (start,end)):raise ValueError()
    members=body['members']
    if not isinstance(members,list) or not 2<=len(members)<=20 or any(type(i) is not int for i in members) or len(set(members))!=len(members) or user_id not in members:raise ValueError()
    payload={key:body.get(key,'').strip() for key in ('title','description','location')}
    if not payload['title'] or len(payload['title'])>160 or len(payload['description'])>20000 or len(payload['location'])>1000:raise ValueError()
    group=body.get('group_id')
    if type(group) is not int:raise ValueError()
    payload.update(start=start.isoformat(),end=end.isoformat(),timezone=body['timezone'],all_day=body['all_day'],members=sorted(members),group_id=group)
    return command,payload


class ResearchMeetingView(View):
    @auth.require_user
    def get(self,request,project_id):
        try:
            project=project_for(request.user,project_id)
            return [meeting_json(row) for row in project.meetings.order_by('-created_at')[:50]]
        except BindingFailure as error:return fail(error.code,error.status)

    @auth.require_user
    def post(self,request,project_id):
        command=None
        try:
            if request.headers.get('Origin') not in APP_ORIGINS:raise BindingFailure('origin_not_allowed',403)
            if len(request.body)>100000:raise BindingFailure('invalid_request',413)
            body=json.loads(request.body)
            command,payload=validate(body,request.user.pk)
            project=project_for(request.user,project_id)
            if project.status in ('completed','archived'):raise BindingFailure('project_read_only',403)
            active={m.user_id:m.user for m in project.chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE,user__is_deleted=False).select_related('user')}
            if not set(payload['members'])<=set(active):raise BindingFailure('member_unavailable',403)
            check_user_text(request,'\n'.join(payload[k] for k in ('title','description','location')),ContentSafetyScene.PROFILE)
            base,_=authorization_site(request)
            identities={uid:credentials(active[uid],base) for uid in payload['members']}
            viewer=identities[request.user.pk]
            subjects=[value['binding']['subject'] for uid,value in identities.items() if uid!=request.user.pk]
            if len(set(subjects))!=len(subjects) or viewer['binding']['subject'] in subjects:raise BindingFailure('duplicate_calendar_identity',400)
            authority={str(uid):value['binding']['subject'] for uid,value in identities.items()}
            with transaction.atomic():
                ResearchProject.objects.select_for_update().get(pk=project.pk)
                if ResearchMeeting.objects.filter(project=project,creator=request.user,status__in=['pending','unknown']).exclude(pk=command).exists():
                    raise BindingFailure('meeting_unresolved',409)
                row,created=ResearchMeeting.objects.get_or_create(pk=command,defaults={'project':project,'creator':request.user,'payload':{**payload,'_authority':authority}})
                row=ResearchMeeting.objects.select_for_update().get(pk=command)
                original={key:value for key,value in row.payload.items() if not key.startswith('_')}
                if row.project_id!=project.pk or row.creator_id!=request.user.pk or original!=payload:raise BindingFailure('meeting_command_conflict',409)
                if row.status=='invited':return meeting_json(row)
                # Retrying after switching a linked account must never send the
                # old uncertain operation as a different organizer or recipient.
                if row.payload.get('_authority')!=authority:raise BindingFailure('meeting_unresolved',409)
                if row.lease_until and row.lease_until>timezone.now():raise BindingFailure('meeting_processing',409)
                row.status='pending';row.lease_until=timezone.now()+timedelta(seconds=60)
                row.save(update_fields=['status','lease_until'])
            try:
                with transaction.atomic():
                    project=ResearchProject.objects.select_for_update().get(pk=project.pk)
                    project_for(request.user,project.pk)
                    current=set(project.chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE).values_list('user_id',flat=True))
                    if project.status=='completed' or not set(payload['members'])<=current:raise BindingFailure('member_unavailable',403)
                    result=remote(base,'/api/oauth/calendar/invitations/',token=viewer['access_token'],key='research-'+str(command),body={
                        **{key:payload[key] for key in ('title','description','location','start','end','timezone','all_day','group_id')},
                        'subjects':subjects,'client_id':str(command)})
                    invitation=uuid.UUID(result['id'])
                    row=ResearchMeeting.objects.select_for_update().get(pk=command)
                    row.invitation_id=invitation;row.status='invited';row.lease_until=None
                    row.save(update_fields=['invitation_id','status','lease_until'])
                    return meeting_json(row)
            except (BindingFailure,ValueError,KeyError,TypeError) as error:
                status='failed' if isinstance(error,BindingFailure) and error.status<500 else 'unknown'
                ResearchMeeting.objects.filter(pk=command,creator=request.user).update(status=status,lease_until=None)
                if isinstance(error,BindingFailure) and error.status<500:raise
                raise BindingFailure('meeting_result_unknown',503)
        except BindingFailure as error:return fail(error.code,error.status)
        except (ValueError,TypeError,KeyError,AttributeError):return fail('invalid_meeting_request',400)
