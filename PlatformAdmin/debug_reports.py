import datetime
import json
import math

from django.db import transaction
from django.utils import timezone
from django.views import View

from PlatformAdmin.models import ClientDebugReport
from PlatformAdmin.validators import PlatformAdminErrors
from PlatformAdmin.views import _audit
from User.models import User
from utils import auth


REPORT_LIFETIME = datetime.timedelta(hours=6)
ALLOWED_EVENTS = {'viewport', 'layout', 'keyboard', 'windowScroll', 'scrollCorrection'}
ALLOWED_METRICS = {
    'viewportHeight', 'viewportOffsetTop', 'viewportPageTop', 'windowScrollY',
    'bodyTop', 'rootTop', 'bodyHeight', 'scrollerHeight', 'scrollerTop',
    'scrollTop', 'scrollHeight', 'renderedRows', 'visibleRows', 'inputTop',
    'inputBottom', 'composerHeight', 'height', 'width', 'offsetTop', 'pageTop',
    'keyboardOpen', 'open',
}


def prune_expired():
    ClientDebugReport.objects.filter(created_at__lt=timezone.now() - REPORT_LIFETIME).delete()


def safe_report(request):
    if len(request.body) > 100_000:
        raise PlatformAdminErrors.DEBUG_REPORT_INVALID
    try:
        report = json.loads(request.body)
    except (TypeError, ValueError):
        raise PlatformAdminErrors.DEBUG_REPORT_INVALID
    if not isinstance(report, dict) or not isinstance(report.get('generatedAt'), str):
        raise PlatformAdminErrors.DEBUG_REPORT_INVALID
    entries = report.get('entries')
    if not isinstance(entries, list) or len(entries) > 180:
        raise PlatformAdminErrors.DEBUG_REPORT_INVALID
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get('event') not in ALLOWED_EVENTS:
            raise PlatformAdminErrors.DEBUG_REPORT_INVALID
        at, elapsed, metrics = entry.get('at'), entry.get('elapsed'), entry.get('metrics')
        if type(at) not in (int, float) or type(elapsed) not in (int, float) or not isinstance(metrics, dict) or len(metrics) > 40:
            raise PlatformAdminErrors.DEBUG_REPORT_INVALID
        if not math.isfinite(at) or not math.isfinite(elapsed):
            raise PlatformAdminErrors.DEBUG_REPORT_INVALID
        safe_metrics = {}
        for key, value in metrics.items():
            if key not in ALLOWED_METRICS:
                raise PlatformAdminErrors.DEBUG_REPORT_INVALID
            if value is not None and type(value) not in (bool, int, float):
                raise PlatformAdminErrors.DEBUG_REPORT_INVALID
            if type(value) is float and not math.isfinite(value):
                raise PlatformAdminErrors.DEBUG_REPORT_INVALID
            safe_metrics[key] = value
        cleaned.append({'at': at, 'elapsed': elapsed, 'event': entry['event'], 'metrics': safe_metrics})
    return {'generatedAt': report['generatedAt'][:40], 'entries': cleaned}


class DebugReportUploadView(View):
    @auth.require_user
    def post(self, request):
        if not request.user.verified:
            raise PlatformAdminErrors.DEBUG_REPORT_VERIFICATION_REQUIRED
        report = safe_report(request)
        if not report['entries']:
            raise PlatformAdminErrors.DEBUG_REPORT_INVALID
        with transaction.atomic():
            User.objects.select_for_update().get(pk=request.user.pk)
            prune_expired()
            item = ClientDebugReport.objects.create(user=request.user, report=report)
            stale_ids = list(ClientDebugReport.objects.filter(user=request.user).values_list('id', flat=True)[5:])
            if stale_ids:
                ClientDebugReport.objects.filter(pk__in=stale_ids).delete()
        return {'report_id': item.pk}


class DebugReportListView(View):
    @auth.require_platform_admin
    def get(self, request):
        prune_expired()
        items = ClientDebugReport.objects.select_related('user__space').all()[:100]
        return {'items': [
            {
                'report_id': item.pk,
                'user_id': item.user_id,
                'user_name': item.user.name,
                'space': item.user.space.slug,
                'created_at': item.created_at.timestamp(),
                'entry_count': len(item.report.get('entries', [])),
            }
            for item in items
        ]}


class DebugReportDetailView(View):
    @auth.require_platform_admin
    def get(self, request, report_id):
        prune_expired()
        item = ClientDebugReport.objects.get(pk=report_id)
        _audit(request, 'debug.report_viewed', 'debug_report', item.pk, f'查看调试报告 #{item.pk}')
        return {'report_id': item.pk, 'report': item.report}

    @auth.require_platform_admin
    def delete(self, request, report_id):
        ClientDebugReport.objects.filter(pk=report_id).delete()
        _audit(request, 'debug.report_deleted', 'debug_report', report_id, f'删除调试报告 #{report_id}')
        return {'deleted': True}
