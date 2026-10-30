"""Profile agenda projections. Parent and child credentials stay on the server."""
import uuid
import jwt
from datetime import datetime
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views import View
from Chat.calendar_policy import projection
from Chat.research import fail
from User.models import User, ChronelleAccount
from User.chronelle_account import BindingFailure, calendar, config, credentials, remote, unpack
from User.chronelle_origins import authorization_site
from utils import auth


class ChronelleProfileView(View):
    def dispatch(self, request, *args, **kwargs):
        response = super().dispatch(request, *args, **kwargs)
        if isinstance(response,dict):
            response = JsonResponse({'identifier':'OK','body':response})
        response['Cache-Control'] = 'private, no-store'
        return response

    @auth.require_user
    def get(self, request, user_id):
        try:
            owner = User.objects.filter(pk=user_id, space_id=request.user.space_id, is_deleted=False).first()
            if owner is None:
                return fail('user_unavailable',404)
            audience = request.GET.get('audience')
            if audience and (owner.pk != request.user.pk or audience not in ('friend','stranger')):
                return fail('invalid_audience',403)
            start,end = (parse_datetime(request.GET.get(key,'')) for key in ('start','end'))
            if not start or not end or timezone.is_naive(start) or timezone.is_naive(end) or not 0 < (end-start).total_seconds() <= 35*86400:
                return fail('invalid_date_range',400)
            account = ChronelleAccount.objects.filter(user=owner).first()
            value = unpack(account) if account else {}
            if value.get('protocol') != 'oauth-v1' or not value.get('binding'):
                return {'status':'hidden','events':[],'availability':'unknown'}
            base,origin = authorization_site(request)
            binding = credentials(owner,base)['binding']
            groups = {group['id'] for group in calendar(owner,base,'groups/')}
            policy = {'detail':list(groups),'busy':[]} if owner.pk == request.user.pk and not audience else projection(
                owner,request.user,groups,preview_tier={'friend':'friends','stranger':'strangers'}.get(audience))
            if not policy['detail'] and not policy['busy']:
                return {'status':'hidden','events':[],'availability':'unknown'}
            status = remote(base,'/api/oauth/introspect/',body={'subject':binding['subject']})
            grant = status.get('grant',{})
            if status.get('active') is not True or grant.get('id') != binding['binding_id'] or grant.get('authorization_type') != 'account_binding':
                raise BindingFailure('binding_required')
            expiry = parse_datetime(grant.get('expires_at',''))
            now = int(timezone.now().timestamp())
            until = min(now+30,int(expiry.timestamp())) if expiry and not timezone.is_naive(expiry) else 0
            if until <= now:
                raise BindingFailure('binding_required')
            client,secret,_ = config()
            token = jwt.encode({'key':client,'sub':binding['subject'],'grant_id':binding['binding_id'],'grant_version':grant['version'],
                'purpose':'calendar-display','viewer_ref':'frienden:'+str(request.user.pk),'origins':[origin],
                'detail_group_ids':policy['detail'],'busy_group_ids':policy['busy'],'task_group_ids':[],
                'aud':'chronelle-display-api','iat':now,'exp':until,'jti':str(uuid.uuid4())},secret,
                algorithm='HS256',headers={'typ':'chronelle-display+jwt'})
            result = remote(base,'/api/oauth/display/events/',method='GET',token=token,origin=origin,
                params={'start':start.isoformat(),'end':end.isoformat()})
            if not isinstance(result,dict) or not isinstance(result.get('events'),list) or len(result['events']) > 10000:
                raise BindingFailure('chronelle_unavailable',503)
            events = []
            for index,event in enumerate(result['events']):
                if not isinstance(event,dict) or not isinstance(event.get('start'),str) or not isinstance(event.get('end'),str) or event.get('visibility') not in ('busy','details'):
                    raise BindingFailure('chronelle_unavailable',503)
                if datetime.fromisoformat(event['end']) <= datetime.fromisoformat(event['start']):
                    raise BindingFailure('chronelle_unavailable',503)
                busy = event['visibility'] == 'busy'
                events.append({'id':str(index),'start':event['start'],'end':event['end'],'all_day':bool(event.get('all_day')),
                    'locked':busy,'title':'' if busy else event.get('title',''),'location':'' if busy else event.get('location','')})
            return {'status':'active','events':events,'availability':'known' if result.get('availability') == 'known' else 'unknown'}
        except BindingFailure as error:
            return fail(error.code,error.status)
        except (ValueError,TypeError,KeyError):
            return fail('chronelle_unavailable',503)
