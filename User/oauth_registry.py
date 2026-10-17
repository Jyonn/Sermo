"""One registry policy for management, consent, and token enforcement."""
import re
from urllib.parse import urlsplit, parse_qs

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from User.models import OAuthApplication, OAuthApplicationRevision, OAuthApplicationAudit

SCOPES = frozenset({'profile', 'phone', 'vip', 'email'})


class RegistryError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def configuration(data):
    name, description = data.get('name'), data.get('description', '')
    uris, scopes = data.get('redirect_uris'), data.get('scopes')
    loopbacks = data.get('loopback_uris', [])
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or not isinstance(description, str) or len(description) > 1000:
        raise RegistryError('A name and description of at most 120/1000 characters are required')
    if not isinstance(scopes, list) or not scopes or any(not isinstance(s, str) for s in scopes) or not set(scopes) <= SCOPES:
        raise RegistryError('Unsupported scopes')
    if not isinstance(uris, list) or not 1 <= len(uris) <= 20 or any(not isinstance(u, str) for u in uris):
        raise RegistryError('Provide 1 to 20 exact redirect URIs')
    if not isinstance(loopbacks, list) or any(not isinstance(u, str) for u in loopbacks) or not set(loopbacks) <= set(uris):
        raise RegistryError('Loopback exceptions must be exact registered URIs')
    actual_loopbacks = []
    for uri in uris:
        try:
            parsed = urlsplit(uri)
            port = parsed.port
        except ValueError:
            raise RegistryError('Invalid redirect URI')
        if len(uri) > 512 or any(c.isspace() or ord(c) < 32 for c in uri) or any(c in uri for c in ('*', '\\', '#')) or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise RegistryError('Redirect URIs must be exact URLs without credentials, fragments, or wildcards')
        if {'code', 'state'} & parse_qs(parsed.query, keep_blank_values=True).keys():
            raise RegistryError('Redirect URLs cannot predefine code or state')
        if parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1', 'localhost') and port:
            actual_loopbacks.append(uri)
        elif parsed.scheme != 'https':
            raise RegistryError('HTTPS required except explicitly approved HTTP loopback URLs')
    if set(actual_loopbacks) != set(loopbacks):
        raise RegistryError('Every HTTP loopback URI requires an explicit exact exception')
    return dict(name=name.strip(), description=description, redirect_uris=list(dict.fromkeys(uris)), scopes=sorted(set(scopes)), loopback_uris=list(dict.fromkeys(loopbacks)))


def approved(client_id, redirect_uri=None):
    if not isinstance(client_id, str) or redirect_uri is not None and not isinstance(redirect_uri, str):
        return None
    app = OAuthApplication.objects.select_related('approved_revision').filter(client_id=client_id, enabled=True).first()
    revision = app.approved_revision if app else None
    if not revision or revision.status != 'approved' or redirect_uri is not None and redirect_uri not in revision.redirect_uris:
        return None
    return revision


def serialize(app):
    def revision(r):
        return dict(id=r.id, name=r.name, description=r.description, redirect_uris=r.redirect_uris, scopes=r.scopes,
                    loopback_uris=r.loopback_uris, status=r.status, review_note=r.review_note,
                    reviewed_by=r.reviewed_by, reviewed_at=r.reviewed_at.isoformat() if r.reviewed_at else None)
    return dict(id=app.id, client_id=app.client_id, owner_id=app.owner_id, enabled=app.enabled, version=app.version,
                approved_revision_id=app.approved_revision_id, revisions=[revision(r) for r in app.revisions.order_by('-id')],
                audit=[dict(actor=a.actor, action=a.action, version=a.version, detail=a.detail, created_at=a.created_at.isoformat()) for a in app.audit.order_by('-id')[:100]])


@transaction.atomic
def mutate(*, actor, owner=None, admin=False, app_id=None, data):
    action = data.get('action', 'create')
    if app_id is None:
        if action != 'create':
            raise RegistryError('Create action required')
        client_id = data.get('client_id')
        if not isinstance(client_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}', client_id):
            raise RegistryError('Invalid client ID')
        config = configuration(data)
        if OAuthApplication.objects.filter(client_id=client_id).exists():
            raise RegistryError('Client ID already exists', 409)
        app = OAuthApplication.objects.create(client_id=client_id, owner=owner)
        rev = OAuthApplicationRevision.objects.create(application=app, **config, status='approved' if admin else 'draft')
        if admin:
            rev.reviewed_by, rev.reviewed_at = actor, timezone.now()
            rev.save(update_fields=['reviewed_by', 'reviewed_at'])
            app.approved_revision = rev
            app.save(update_fields=['approved_revision'])
    else:
        app = OAuthApplication.objects.select_for_update().filter(pk=app_id).first()
        if not app or not admin and app.owner_id != getattr(owner, 'id', None):
            raise RegistryError('Application not found', 404)
        version = data.get('version')
        if type(version) is not int or version != app.version:
            raise RegistryError('Application changed; reload before retrying', 409)
        # Conditional write also protects engines where SELECT FOR UPDATE is ineffective.
        if not OAuthApplication.objects.filter(pk=app.pk, version=version).update(version=F('version') + 1):
            raise RegistryError('Application changed; reload before retrying', 409)
        app.version += 1
        rev = None
        if action == 'revise':
            if app.revisions.filter(status__in=['draft', 'pending']).exists():
                raise RegistryError('Finish the current draft or review first', 409)
            rev = OAuthApplicationRevision.objects.create(application=app, **configuration(data))
        elif action in ('edit', 'submit', 'approve', 'reject'):
            if type(data.get('revision_id')) is not int:
                raise RegistryError('Integer revision ID required')
            rev = app.revisions.filter(pk=data.get('revision_id')).first()
            if not rev:
                raise RegistryError('Revision not found', 404)
            if action in ('edit', 'submit'):
                if rev.status != 'draft':
                    raise RegistryError('Only drafts can be edited or submitted', 409)
                if action == 'edit':
                    for key, value in configuration(data).items():
                        setattr(rev, key, value)
                else:
                    configuration(dict(name=rev.name, description=rev.description, redirect_uris=rev.redirect_uris, scopes=rev.scopes, loopback_uris=rev.loopback_uris))
                    rev.status = 'pending'
            else:
                if not admin:
                    raise RegistryError('Platform administrator required', 403)
                if rev.status != 'pending':
                    raise RegistryError('Only pending revisions can be reviewed', 409)
                if action == 'approve' and rev.loopback_uris and data.get('approved_loopback_uris') != rev.loopback_uris:
                    raise RegistryError('Explicitly approve the exact HTTP loopback exceptions')
                note = data.get('review_note', '')
                if not isinstance(note, str) or len(note) > 1000 or action == 'reject' and not note.strip():
                    raise RegistryError('Provide a review note (up to 1000 characters)')
                configuration(dict(name=rev.name, description=rev.description, redirect_uris=rev.redirect_uris, scopes=rev.scopes, loopback_uris=rev.loopback_uris))
                rev.status = 'approved' if action == 'approve' else 'rejected'
                rev.reviewed_by, rev.reviewed_at, rev.review_note = actor, timezone.now(), note
                if action == 'approve':
                    app.approved_revision = rev
            rev.save()
        elif action in ('disable', 'restore') and admin:
            app.enabled = action == 'restore'
        else:
            raise RegistryError('Action not allowed', 403)
        app.save(update_fields=['enabled', 'approved_revision'])
    OAuthApplicationAudit.objects.create(application=app, revision=rev, actor=actor, action=action, version=app.version,
                                         detail={'review_note': rev.review_note} if rev else {})
    return serialize(app)
