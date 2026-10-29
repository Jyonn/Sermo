"""Member-only resource spaces, with bounded project folders and flat task scopes."""
import json
from urllib.parse import urlsplit

from django.db import transaction
from django.views import View

from Chat.models import ResearchProject, ResearchResource
from Chat.research import fail, text
from Chat.research_status import research_status, normalize_tasks
from Message.models import MediaResource, MediaAsset
from utils import auth
from utils.content_safety import check_user_text, ContentSafetyScene
from utils.qiniu import sign_private_download_url


def scope(project, task_id):
    if not isinstance(task_id, str) or len(task_id) > 64:
        raise ValueError('Invalid task')
    task = next((item for item in normalize_tasks(project.tasks) if item['id'] == task_id), None)
    if task_id and not task:
        raise ValueError('Task unavailable')
    return task


def serialize(row, user, owner):
    return dict(id=row.pk, parent_id=row.parent_id, kind=row.kind, name=row.name,
                creator_id=row.creator_id, created_at=row.created_at.isoformat(),
                size=row.media.asset.file_size if row.media_id else None,
                url=row.url if row.kind == 'link' else None,
                can_delete=owner or row.creator_id == user.pk)


class ResearchResourceView(View):
    @staticmethod
    def project(request, project_id, lock=False):
        if not request.user.space.research_enabled:
            return None
        rows = ResearchProject.objects.select_related('chat')
        if lock:
            rows = rows.select_for_update()
        project = rows.filter(pk=project_id, chat__space_id=request.user.space_id, chat__is_deleted=False).first()
        return project if project and project.chat.has_active_member(request.user) else None

    @auth.require_user
    def get(self, request, project_id):
        project = self.project(request, project_id)
        if not project:
            return fail('Project unavailable', 404)
        try:
            task_id = request.GET.get('task_id', '')
            task = scope(project, task_id)
        except ValueError as error:
            return fail(str(error))
        owner = project.chat.is_owner(request.user)
        rows = project.resources.filter(task_id=task_id).select_related('media__asset').order_by('kind', 'name', 'id')
        return dict(items=[serialize(row, request.user, owner) for row in rows],
                    writable=research_status(project.status) == 'active' and (not task or research_status(task['status']) == 'active'))

    @auth.require_user
    def post(self, request, project_id):
        try:
            if len(request.body) > 10000:
                return fail('Request too large', 413)
            body = json.loads(request.body)
            if not isinstance(body, dict):
                raise ValueError('Invalid request')
            with transaction.atomic():
                project = self.project(request, project_id, lock=True)
                if not project:
                    return fail('Project unavailable', 404)
                task_id = body.get('task_id', '')
                task = scope(project, task_id)
                rows = project.resources.filter(task_id=task_id)
                action = body.get('action')
                if action == 'open':
                    row = rows.select_related('media__asset').filter(pk=body.get('id')).first()
                    if not row or row.kind == 'folder':
                        return fail('Resource unavailable', 404)
                    return dict(url=row.url if row.kind == 'link' else sign_private_download_url(row.media.asset.source_uri))
                if research_status(project.status) != 'active' or (task and research_status(task['status']) != 'active'):
                    return fail('Archived resources are read-only', 403)
                if action == 'delete':
                    row = rows.filter(pk=body.get('id')).first()
                    if not row:
                        return fail('Resource unavailable', 404)
                    if row.creator_id != request.user.pk and not project.chat.is_owner(request.user):
                        return fail('Only the uploader or project owner can delete this resource', 403)
                    if row.children.exists():
                        return fail('Directory is not empty', 409)
                    media = row.media
                    row.delete()
                    if media and media.recalculate_reference_count() == 0:
                        media.library_active = False
                        media.save(update_fields=['library_active'])
                    return dict(deleted=True)
                if action != 'add':
                    raise ValueError('Invalid action')
                if project.resources.count() >= 1000:
                    raise ValueError('Resource limit reached')
                kind = body.get('kind')
                if kind not in ('folder', 'file', 'link') or (task_id and kind == 'folder'):
                    raise ValueError('Invalid resource type')
                parent = None
                if body.get('parent_id') is not None:
                    if task_id:
                        raise ValueError('Task resources cannot have folders')
                    parent = rows.filter(pk=body['parent_id'], kind='folder').first()
                    if not parent:
                        raise ValueError('Directory unavailable')
                    if kind == 'folder' and parent.parent_id:
                        raise ValueError('Only two directory levels are allowed')
                name = text(body, 'name', 180)
                if kind == 'folder' and rows.filter(parent=parent, kind='folder', name=name).exists():
                    return fail('Directory already exists', 409)
                url = ''
                media = None
                if kind == 'link':
                    url = text(body, 'url', 2048)
                    parsed = urlsplit(url)
                    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                        raise ValueError('Only HTTP(S) links are supported')
                elif kind == 'file':
                    media = MediaResource.objects.select_related('asset').filter(pk=body.get('resource_id'), owner=request.user, library_active=True, asset__status=MediaAsset.STATUS_READY).first()
                    if not media:
                        return fail('Uploaded file unavailable', 400)
                check_user_text(request, name + '\n' + url, ContentSafetyScene.PROFILE)
                row = ResearchResource.objects.create(project=project, task_id=task_id, parent=parent,
                    creator=request.user, kind=kind, name=name, url=url, media=media)
                if media:
                    media.recalculate_reference_count()
                return serialize(row, request.user, project.chat.is_owner(request.user))
        except (ValueError, TypeError, KeyError) as error:
            return fail(str(error))
