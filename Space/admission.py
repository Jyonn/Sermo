import hashlib
import re
import secrets

from django.utils import timezone

from Space.models import SpaceJoinApplication
from Space.validators import SpaceErrors


def invite_code():
    value = secrets.token_hex(8).upper()
    return '-'.join(value[index:index+4] for index in range(0, len(value), 4))


def receipt_hash(receipt):
    if not receipt or not re.fullmatch(r'[0-9a-f]{64}', receipt):
        return None
    return hashlib.sha256(receipt.encode('ascii')).hexdigest()


def application_for_receipt(space, receipt):
    key = receipt_hash(receipt)
    application = SpaceJoinApplication.objects.filter(space=space, receipt_hash=key).first() if key else None
    if application is None:
        raise SpaceErrors.JOIN_APPLICATION_NOT_FOUND
    return application


def require_admission(space, name, code=None, receipt=None, password=None, wechat=False):
    """Called under the space row lock by every member registration path."""
    if space.join_mode == 'open':
        return None
    if space.join_mode == 'invite':
        normalized = (code or '').strip().upper().replace('-', '')
        if not space.join_invite_code or not secrets.compare_digest(
            normalized.encode('utf-8'), space.join_invite_code.replace('-', '').encode('ascii'),
        ):
            raise SpaceErrors.JOIN_INVITE_REQUIRED
        application = None
    else:
        key = receipt_hash(receipt)
        application = SpaceJoinApplication.objects.select_for_update().filter(
            space=space, receipt_hash=key, status='approved',
        ).first() if key else None
        if application is None or application.name.lower() != name.lower():
            raise SpaceErrors.JOIN_APPROVAL_REQUIRED
    if not wechat and not password:
        raise SpaceErrors.JOIN_PASSWORD_REQUIRED
    return application


def mark_joined(application):
    if application is not None:
        application.status = 'joined'
        application.save(update_fields=['status'])


def review_application(application, decision):
    status = 'approved' if decision == 'approve' else 'rejected'
    if application.status == status:
        return
    if application.status != 'pending':
        raise SpaceErrors.JOIN_APPLICATION_CONFLICT
    application.status = status
    application.reviewed_at = timezone.now()
    application.save(update_fields=['status', 'reviewed_at'])
