from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from Message.models import LinkPreview, LinkPreviewStatusChoice


class LinkPreviewExpiryTests(TestCase):
    URL = 'https://example.com/article'

    def create_preview(self, status, age):
        return LinkPreview.objects.create(
            url=self.URL,
            url_hash=LinkPreview.hash_url(self.URL),
            status=status,
            fetched_at=timezone.now() - age,
        )

    @patch.object(LinkPreview, '_require_public_host')
    @patch.object(LinkPreview, 'fetch_async')
    def test_fresh_ready_preview_is_reused(self, fetch_async, _require_public_host):
        preview = self.create_preview(LinkPreviewStatusChoice.READY, timedelta(days=1))

        with self.captureOnCommitCallbacks(execute=True):
            result = LinkPreview.queue_for_text(self.URL)

        self.assertEqual(result.id, preview.id)
        fetch_async.assert_not_called()

    @patch.object(LinkPreview, '_require_public_host')
    @patch.object(LinkPreview, 'fetch_async')
    def test_expired_ready_preview_refreshes_without_hiding_stale_card(self, fetch_async, _require_public_host):
        preview = self.create_preview(LinkPreviewStatusChoice.READY, timedelta(days=8))

        with self.captureOnCommitCallbacks(execute=True):
            result = LinkPreview.queue_for_text(self.URL)

        result.refresh_from_db()
        self.assertEqual(result.status, LinkPreviewStatusChoice.READY)
        fetch_async.assert_called_once_with(preview.id, force=True)

    @patch.object(LinkPreview, '_require_public_host')
    @patch.object(LinkPreview, 'fetch_async')
    def test_expired_failed_preview_is_retried(self, fetch_async, _require_public_host):
        preview = self.create_preview(LinkPreviewStatusChoice.FAILED, timedelta(hours=2))

        with self.captureOnCommitCallbacks(execute=True):
            LinkPreview.queue_for_text(self.URL)

        fetch_async.assert_called_once_with(preview.id, force=True)
