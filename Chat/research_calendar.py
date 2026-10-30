"""Member-scoped restricted projections. The browser never selects a relationship tier."""
import json
import uuid
import jwt
from django.db import transaction
from django.utils import timezone
from django.views import View
from Chat.models import ResearchProject, ResearchCalendarPreference, ChatMemberStatusChoice
from Chat.research import fail
from User.models import ChronelleAccount
from User.chronelle_account import BindingFailure, calendar, config, credentials, remote, revocation_credentials, unpack
from User.chronelle_origins import authorization_site, APP_ORIGINS
from utils import auth


def project_for(user, pk):
    if not user.space.research_enabled:
        raise BindingFailure('research_disabled',403)
    project = ResearchProject.objects.filter(pk=pk,chat__space_id=user.space_id,chat__is_deleted=False,
        chat__chat_members__user=user,chat__chat_members__status=ChatMemberStatusChoice.ACTIVE).select_related('chat').first()
    if not project:
        raise BindingFailure('project_unavailable',404)
    return project


def error_response(error):
    return fail(error.code,error.status)


def shared_policy(user, project, group_ids, viewer=None):
    from Chat.calendar_policy import projection
    return projection(user, viewer, group_ids)


def revoke_displays(project, base, tokens):
    revoked=set()
    for item in project.calendar_displays:
        if item['until'] <= timezone.now().timestamp():
            continue
        if item.get('protocol') != 'oauth-v1':
            continue
        value = tokens.get(item['owner'])
        if value is None:
            raise BindingFailure('binding_required')
        if value.get('revoked'):
            continue
        if item['owner'] in revoked:
            continue
        try:
            # Stateless child JWTs cannot be revoked individually. Revoke the
            # parent grant when a privacy/membership change requires immediate removal.
            remote(base,'/api/oauth/revoke/',body={'token':value['refresh_token']})
            revoked.add(item['owner'])
        except BindingFailure as error:
            if error.code not in ('binding_revoked','binding_expired','binding_superseded','calendar_resource_unavailable'):
                raise
    project.calendar_displays=[]


class ResearchCalendarView(View):
    @auth.require_user
    def get(self,request,project_id):
        try:
            project = project_for(request.user,project_id)
            config()
            base,_ = authorization_site(request)
            subject = credentials(request.user,base)['binding']['subject']
            groups = calendar(request.user,base,'groups/')
            if credentials(request.user,base)['binding']['subject'] != subject:
                raise BindingFailure('binding_superseded')
            configured = ResearchCalendarPreference.objects.filter(user=request.user).first()
            if configured and configured.binding_subject != credentials(request.user,base)['binding']['subject']:
                configured = None
            policy = shared_policy(request.user, project, [g['id'] for g in groups])
            if configured is None:
                policy = {'detail': [], 'busy': [g['id'] for g in groups]}
            return {'status':'active','groups':[{'id':g['id'],'name':g['name']} for g in groups],
                'policy':policy, 'policy_configured':configured is not None, 'default_busy':configured.default_busy if configured else True,
                'members':[{'id':m.user_id,'name':m.user.name} for m in project.chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE,user__is_deleted=False).select_related('user')]}
        except BindingFailure as error:
            if error.code in ('research_disabled','project_unavailable'):
                return error_response(error)
            return {'status':error.code,'groups':[],'policy':{'detail':[],'busy':[]},'members':[]}

    @auth.require_user
    def post(self,request,project_id):
        try:
            if len(request.body)>100000:raise BindingFailure('invalid_request',413)
            body=json.loads(request.body)
            if not isinstance(body,dict):raise BindingFailure('invalid_request',400)
            project=project_for(request.user,project_id)
            base,origin=authorization_site(request)
            if request.headers.get('Origin') not in APP_ORIGINS:
                raise BindingFailure('origin_not_allowed',403)
            if body.get('action')=='policy':
                existing=ResearchCalendarPreference.objects.filter(user=request.user).first()
                if existing and existing.overrides.get('version') == 2:
                    raise BindingFailure('global_policy_update_required',400)
                binding_subject=credentials(request.user,base)['binding']['subject']
                group_ids={g['id'] for g in calendar(request.user,base,'groups/')}
                if credentials(request.user,base)['binding']['subject'] != binding_subject:
                    raise BindingFailure('binding_superseded')
                detail,busy=body.get('detail'),body.get('busy')
                legacy = 'default_busy' not in body
                if legacy and ResearchCalendarPreference.objects.filter(user=request.user).exists():
                    raise BindingFailure('global_policy_update_required',400)
                default_busy = body.get('default_busy', False)
                if type(default_busy) is not bool:
                    raise BindingFailure('invalid_policy',400)
                if not isinstance(detail,list) or not isinstance(busy,list) or any(type(i) is not int for i in detail+busy) or not set(detail+busy)<=group_ids or set(detail)&set(busy):
                    raise BindingFailure('invalid_policy',400)
                # A global privacy change must revoke this owner's projections in every project.
                projects = [project] if legacy else list(ResearchProject.objects.filter(chat__chat_members__user=request.user,
                    chat__chat_members__status=ChatMemberStatusChoice.ACTIVE).distinct().order_by('pk'))
                tokens={}
                if any(item['owner']==request.user.pk and item['until']>timezone.now().timestamp() for p in projects for item in p.calendar_displays):
                    tokens[request.user.pk]=revocation_credentials(request.user,base)
                with transaction.atomic():
                    projects = list(ResearchProject.objects.select_for_update().filter(pk__in=[p.pk for p in projects]).order_by('pk'))
                    project_for(request.user,project.pk)
                    for item in projects:
                        others = [d for d in item.calendar_displays if d['owner']!=request.user.pk]
                        item.calendar_displays = [d for d in item.calendar_displays if d['owner']==request.user.pk]
                        revoke_displays(item,base,tokens)
                        item.calendar_displays = others
                        item.save(update_fields=['calendar_displays'])
                    if legacy:
                        locked = next(item for item in projects if item.pk==project.pk)
                        locked.calendar_policies[str(request.user.pk)]={'detail':sorted(set(detail)),'busy':sorted(set(busy))}
                        locked.save(update_fields=['calendar_policies'])
                    else:
                        ResearchCalendarPreference.objects.update_or_create(user=request.user, defaults={
                            'default_busy':default_busy,
                            'binding_subject':binding_subject,
                            'overrides':{str(i):'detail' if i in detail else 'busy' if i in busy else 'hidden' for i in group_ids}})
                return {'status':'saved'}
            if body.get('action')!='display':
                raise BindingFailure('invalid_request',400)
            selected=body.get('members')
            if not isinstance(selected,list) or not 1<=len(selected)<=20 or len(set(selected))!=len(selected) or any(type(i) is not int for i in selected):
                raise BindingFailure('invalid_selection',400)
            active={m.user_id:m.user for m in project.chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE,user__is_deleted=False).select_related('user')}
            if not set(selected)<=set(active):
                raise BindingFailure('member_unavailable',403)
            viewer=credentials(request.user,base)
            tokens={uid:credentials(active[uid],base) for uid in selected}
            # Only the viewer's own new groups are automatically visible. Peers
            # retain their explicit saved group policy; never expand it on read.
            groups={uid:{g['id'] for g in calendar(active[uid],base,'groups/')} for uid in selected}
            with transaction.atomic():
                project=ResearchProject.objects.select_for_update().get(pk=project.pk)
                project_for(request.user,project.pk)
                current=set(project.chat.chat_members.filter(status=ChatMemberStatusChoice.ACTIVE).values_list('user_id',flat=True))
                if not set(selected)<=current:
                    raise BindingFailure('member_unavailable',403)
                project.calendar_displays=[d for d in project.calendar_displays if d['until']>timezone.now().timestamp()]
                if len(project.calendar_displays)+len(selected)>1000 or sum(d['viewer']==request.user.pk for d in project.calendar_displays)+len(selected)>100:
                    raise BindingFailure('display_limit',429)
                results=[]
                # Preflight all policies before issuing anything. No partial merge fallback.
                policies={}
                for uid in selected:
                    policy={'detail':list(groups[uid]),'busy':[]} if uid==request.user.pk else shared_policy(active[uid],project,groups[uid],request.user)
                    if uid!=request.user.pk and not (policy['detail'] or policy['busy']):
                        raise BindingFailure('member_calendar_not_shared',403)
                    policies[uid]=policy
                for uid in selected:
                    policy=policies[uid]
                    allowed=groups[uid]
                    binding=tokens[uid]['binding']
                    status=remote(base,'/api/oauth/introspect/',body={'subject':binding['subject']})
                    grant=status.get('grant',{})
                    if not status.get('active') or grant.get('id')!=binding['binding_id'] or grant.get('authorization_type')!='account_binding':
                        raise BindingFailure('binding_required')
                    from django.utils.dateparse import parse_datetime
                    expiry=parse_datetime(grant['expires_at'])
                    if expiry is None or timezone.is_naive(expiry):raise BindingFailure('invalid_binding')
                    now=int(timezone.now().timestamp());until=min(now+300,int(expiry.timestamp()))
                    if until<=now:raise BindingFailure('binding_required')
                    display_id=str(uuid.uuid4());client,secret,_=config()
                    token=jwt.encode({'key':client,'sub':binding['subject'],'grant_id':binding['binding_id'],
                        'grant_version':grant['version'],'purpose':'calendar-display','viewer_ref':'frienden:'+str(request.user.pk),
                        'origins':[origin],'detail_group_ids':sorted(set(policy['detail'])&allowed),
                        'busy_group_ids':sorted(set(policy['busy'])&allowed),'task_group_ids':[],
                        'aud':'chronelle-display-api','iat':now,'exp':until,'jti':display_id},secret,
                        algorithm='HS256',headers={'typ':'chronelle-display+jwt'})
                    results.append({'member_id':uid,'id':display_id,'token':token,'url':base+'/oauth/embed/'+client+'/'})
                    project.calendar_displays.append({'protocol':'oauth-v1','id':display_id,'owner':uid,'viewer':request.user.pk,'until':until,'base':base})
                project.save(update_fields=['calendar_displays'])
            return {'displays':results,'renew_after':240}
        except BindingFailure as error:
            return error_response(error)
        except (ValueError,TypeError,KeyError):
            return fail('invalid_calendar_request',400)
