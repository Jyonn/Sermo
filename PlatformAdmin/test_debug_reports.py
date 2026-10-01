import datetime
import json

from django.test import RequestFactory, TestCase
from django.utils import timezone

from PlatformAdmin.debug_reports import DebugReportDetailView, DebugReportListView, DebugReportUploadView
from PlatformAdmin.models import ClientDebugReport
from PlatformAdmin.validators import PlatformAdminErrors
from Space.models import Space
from User.models import User, UserAccountLevelChoice
from utils import auth


class DebugReportTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        space = Space.objects.create(name='Debug Space', slug='debug-space', email='debug@example.com')
        self.user = User.objects.create(
            space=space, name='Debug User', account_level=UserAccountLevelChoice.VERIFIED,
        )
        self.user_token = auth.get_login_token(self.user)['auth']
        self.admin_token = auth.get_platform_admin_token('admin@example.com')['auth']
        self.report = {
            'generatedAt': '2026-10-01T12:00:00.000Z',
            'entries': [{'at': 1790856000000, 'elapsed': 100, 'event': 'keyboard', 'metrics': {'height': 400}}],
        }

    def upload(self, payload=None):
        request = self.factory.post(
            '/platform-admin/debug-reports/upload',
            data=json.dumps(payload if payload is not None else self.report),
            content_type='application/json',
            HTTP_AUTHORIZATION=f'Bearer {self.user_token}',
        )
        return DebugReportUploadView.as_view()(request)

    def test_only_verified_users_can_upload(self):
        self.upload()
        self.assertEqual(ClientDebugReport.objects.count(), 1)
        self.user.account_level = UserAccountLevelChoice.BASIC
        self.user.save(update_fields=['account_level'])
        with self.assertRaises(PlatformAdminErrors.DEBUG_REPORT_VERIFICATION_REQUIRED.__class__):
            self.upload()

    def test_rejects_non_metric_content(self):
        payload = {**self.report, 'entries': [{**self.report['entries'][0], 'metrics': {'message': 'secret'}}]}
        with self.assertRaises(PlatformAdminErrors.DEBUG_REPORT_INVALID.__class__):
            self.upload(payload)
        self.assertFalse(ClientDebugReport.objects.exists())

    def test_retains_only_five_recent_reports_and_admin_can_delete(self):
        old = ClientDebugReport.objects.create(user=self.user, report=self.report)
        ClientDebugReport.objects.filter(pk=old.pk).update(created_at=timezone.now() - datetime.timedelta(hours=7))
        for _ in range(6):
            self.upload()
        self.assertEqual(ClientDebugReport.objects.filter(user=self.user).count(), 5)
        self.assertFalse(ClientDebugReport.objects.filter(pk=old.pk).exists())

        request = self.factory.get('/platform-admin/debug-reports', HTTP_AUTHORIZATION=f'Bearer {self.admin_token}')
        items = DebugReportListView.as_view()(request)['items']
        self.assertEqual(len(items), 5)
        report_id = items[0]['report_id']
        detail_request = self.factory.get(f'/platform-admin/debug-reports/{report_id}', HTTP_AUTHORIZATION=f'Bearer {self.admin_token}')
        self.assertEqual(DebugReportDetailView.as_view()(detail_request, report_id=report_id)['report'], self.report)
        delete_request = self.factory.delete(f'/platform-admin/debug-reports/{report_id}', HTTP_AUTHORIZATION=f'Bearer {self.admin_token}')
        DebugReportDetailView.as_view()(delete_request, report_id=report_id)
        self.assertFalse(ClientDebugReport.objects.filter(pk=report_id).exists())
