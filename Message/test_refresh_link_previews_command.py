from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import TransactionTestCase
from django.utils import timezone

from Message.models import LinkPreview, LinkPreviewStatusChoice


class RefreshLinkPreviewsCommandTests(TransactionTestCase):
    URL = 'https://example.com/article'

    def create_preview(self, status=LinkPreviewStatusChoice.FAILED):
        return LinkPreview.objects.create(
            url=self.URL,
            url_hash=LinkPreview.hash_url(self.URL),
            status=status,
            error='http 403',
            fetched_at=timezone.now(),
        )

    @patch.object(LinkPreview, '_require_public_host')
    @patch.object(LinkPreview, 'fetch_preview_data')
    def test_force_refreshes_failed_preview_and_prints_result(self, fetch_preview_data, _require_public_host):
        preview = self.create_preview()
        fetch_preview_data.return_value = {
            'url': self.URL,
            'title': 'Example title',
            'description': 'Example description',
            'image_url': '',
            'site_name': 'example.com',
            'favicon_url': '',
        }
        stdout = StringIO()

        call_command('refresh_link_previews', '--failed', '--force', stdout=stdout)

        preview.refresh_from_db()
        self.assertEqual(preview.status, LinkPreviewStatusChoice.READY)
        self.assertEqual(preview.title, 'Example title')
        self.assertIn('READY  Example title', stdout.getvalue())

    @patch.object(LinkPreview, '_require_public_host')
    @patch.object(LinkPreview, 'fetch_preview_data')
    def test_fresh_ready_url_is_skipped_without_force(self, fetch_preview_data, _require_public_host):
        self.create_preview(status=LinkPreviewStatusChoice.READY)
        stdout = StringIO()

        call_command('refresh_link_previews', self.URL, stdout=stdout)

        fetch_preview_data.assert_not_called()
        self.assertIn('SKIP', stdout.getvalue())
