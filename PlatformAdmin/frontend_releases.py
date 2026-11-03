"""Frontend-only release registry. GET never probes sites, workers or backend versions."""
import json
import re
from datetime import date
from django.db import transaction
from django.http import JsonResponse
from django.views import View
from PlatformAdmin.models import FrontendRelease, FrontendDeployment, PlatformAuditLog
from utils import auth

SITES = {
    'https://frienden.jyonn.space':'friendenPrimary',
    'https://frienden.6-79.cn':'friendenMirror',
    'https://sermo.jyonn.space':'primary',
    'https://sermo.6-79.cn':'mirror',
}


def version_parts(value):
    if not isinstance(value,str) or len(value)>32 or not re.fullmatch(r'\d{4}\.\d{2}\.\d{2}\.[1-9]\d*',value):raise ValueError('Invalid frontend release ID')
    parts=tuple(map(int,value.split('.')))
    date(*parts[:3])
    return parts


def validate_notes(notes):
    if not isinstance(notes,dict) or set(notes)!={'id','publishedAt','locales'}:raise ValueError('Expected frontend release notes only')
    version_parts(notes.get('id'))
    published=date.fromisoformat(notes['publishedAt'])
    if notes['id'][:10].replace('.','-')!=published.isoformat():raise ValueError('Release date mismatch')
    locales=notes.get('locales')
    if not isinstance(locales,dict) or not {'zh-CN','en'}.issubset(locales):raise ValueError('Chinese and English release notes required')
    if len(json.dumps(notes,ensure_ascii=False).encode())>32768:raise ValueError('Release notes too large')
    for value in locales.values():
        if not isinstance(value,dict) or set(value)!={'title','items'} or not isinstance(value['title'],str) or not 1<=len(value['title'].strip())<=160:raise ValueError('Invalid release title')
        if not isinstance(value['items'],list) or not 1<=len(value['items'])<=5 or any(not isinstance(item,str) or not 1<=len(item.strip())<=500 for item in value['items']):raise ValueError('Invalid release items')
    return published


@transaction.atomic
def publish_frontend_release(origin,notes,actor):
    if origin not in SITES:raise ValueError('Unknown frontend origin')
    published=validate_notes(notes)
    release,created=FrontendRelease.objects.get_or_create(version=notes['id'],defaults=dict(notes=notes,published_on=published))
    if not created and release.notes!=notes:raise ValueError('Release notes are immutable; use a new release ID')
    deployment,deployment_created=FrontendDeployment.objects.get_or_create(origin=origin,defaults=dict(release=release))
    deployment=FrontendDeployment.objects.select_for_update().select_related('release').get(id=deployment.id)
    if version_parts(deployment.release.version)>version_parts(release.version):raise ValueError('Cannot publish an older frontend release')
    if not deployment_created and deployment.release_id==release.id:return release
    if not deployment_created:
        deployment.release=release;deployment.save(update_fields=['release','updated_at'])
    PlatformAuditLog.objects.create(action='frontend.release.publish',target_type='frontend_release',target_id=release.id,summary=release.version,metadata={'actor':actor,'origin':origin})
    return release


def release_catalog():
    deployments={row.origin:row for row in FrontendDeployment.objects.select_related('release')}
    return dict(kind='frontend',sources=[dict(key=key,origin=origin,status='ok' if origin in deployments else 'error',releaseId=deployments[origin].release.version if origin in deployments else None,release=deployments[origin].release.notes if origin in deployments else None) for origin,key in SITES.items()])


class FrontendReleaseView(View):
    def get(self,request):
        response=JsonResponse(dict(message='OK',code=200,identifier='OK',body=release_catalog()))
        response['Cache-Control']='no-store'
        return response


class FrontendReleasePublishView(View):
    @auth.require_platform_admin
    def put(self,request):
        try:
            if len(request.body)>35000:raise ValueError('Request too large')
            value=json.loads(request.body)
            if not isinstance(value,dict) or value.get('kind')!='frontend':raise ValueError('Only frontend releases are accepted')
            row=publish_frontend_release(value.get('origin'),value.get('release'),request.platform_admin_email)
            return dict(id=row.version,origin=value['origin'])
        except (ValueError,TypeError,KeyError):
            return JsonResponse(dict(message='Invalid frontend release',identifier='FRONTEND_RELEASE_INVALID',body=None),status=400)
