import datetime
from unittest.mock import patch
from django.test import TestCase, SimpleTestCase
from django.utils import timezone
from Message.models import LinkPreview, LinkPreviewStatusChoice, SocialParseUsage
from Message.providers.justone import JustOneProvider
from Message.social_quota import consume, eligibility, week_start
from User.models import User


class AdapterTests(SimpleTestCase):
    @patch.object(JustOneProvider, 'request')
    def test_douyin_exact_video(self, request):
        request.return_value = {'aweme_detail': {'aweme_id': '7708301695272389930', 'desc': 'Video', 'video': {'play_addr': {'url_list': ['https://v5.zjcdn.com/a.mp4']}}}}
        data = JustOneProvider.parse('https://www.douyin.com/video/7708301695272389930')
        self.assertEqual(data['provider_data']['video_url'], 'https://v5.zjcdn.com/a.mp4')
        self.assertEqual(data['provider_data']['adapter'], 'justone')

    @patch.object(JustOneProvider, 'request')
    def test_xhs_rednote_images(self, request):
        ident = '691d3d47000000001b027c80'
        request.return_value = [{'note_list': [{'id': ident, 'type': 'normal', 'title': '九寨沟', 'images_list': [{'url': 'https://sns.rednotecdn.com/image.heif'}]}]}]
        data = JustOneProvider.parse('https://www.xiaohongshu.com/explore/' + ident)
        self.assertEqual(data['provider_data']['images'], ['https://sns.rednotecdn.com/image.heif'])

    @patch.object(JustOneProvider, 'request')
    def test_video_without_stream_is_not_gallery(self, request):
        ident = '69874282000000001a0340e8'
        request.return_value = [{'note_list': [{'id': ident, 'type': 'video', 'images_list': [{'url': 'https://sns.rednotecdn.com/cover.jpg'}]}]}]
        with self.assertRaisesMessage(ValueError, 'social_video_unavailable'):
            JustOneProvider.parse('https://www.xiaohongshu.com/explore/' + ident)

    def test_media_allowlist(self):
        self.assertEqual(JustOneProvider.media('https://rednotecdn.com.attacker.test/a'), '')


class QuotaTests(TestCase):
    def setUp(self):
        from Space.models import Space
        space = Space.objects.create(name='Social', slug='social', email='social@example.test')
        self.vip = User.objects.create(space=space, name='requester', lower_name='requester', is_permanent_vip=True)
        self.sender = User.objects.create(space=space, name='sender', lower_name='sender', is_permanent_vip=True)
        self.other = User.objects.create(space=space, name='nonvip', lower_name='nonvip')
        self.preview = LinkPreview.objects.create(url='https://www.douyin.com/video/7708301695272389930', url_hash='a' * 64, status=LinkPreviewStatusChoice.PENDING)

    def test_seven_total_across_platforms_and_nonvip(self):
        self.assertEqual(consume(self.other.id), 'vip_required')
        for _ in range(7):
            self.assertEqual(consume(self.vip.id), '')
        self.assertEqual(consume(self.vip.id), 'weekly_limit')
        self.assertFalse(SocialParseUsage.objects.filter(user=self.sender).exists())

    def test_new_week(self):
        SocialParseUsage.objects.create(user=self.vip, week=week_start() - datetime.timedelta(days=7), count=7)
        self.assertEqual(consume(self.vip.id), '')

    @patch.dict('os.environ', {'JUSTONEAPI_TOKEN': 'test-only'})
    @patch.object(LinkPreview, 'fetch_preview_data')
    def test_refresh_charged_to_requester_and_cache_free(self, fetch):
        fetch.return_value = dict(url=self.preview.url, title='title', description='', image_url='', site_name='抖音', favicon_url='', provider_data={'provider': 'douyin_video', 'adapter': 'justone'})
        preview, refreshed = LinkPreview.refresh_now(self.preview.id, requester_id=self.vip.id)
        self.assertTrue(refreshed)
        LinkPreview.refresh_now(preview.id, requester_id=self.other.id)
        self.assertEqual(SocialParseUsage.objects.get(user=self.vip).count, 1)
        self.assertFalse(SocialParseUsage.objects.filter(user=self.sender).exists())
        self.assertEqual(fetch.call_count, 1)

    @patch.dict('os.environ', {'JUSTONEAPI_TOKEN': 'test-only'})
    @patch.object(LinkPreview, 'fetch_preview_data', side_effect=ValueError('social_provider_failed'))
    def test_failed_attempt_has_negative_cache(self, fetch):
        preview, _ = LinkPreview.refresh_now(self.preview.id, requester_id=self.vip.id)
        LinkPreview.refresh_now(preview.id, requester_id=self.vip.id)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(SocialParseUsage.objects.get(user=self.vip).count, 1)

    def test_old_provider_and_signed_expiry(self):
        self.preview.status = LinkPreviewStatusChoice.READY
        self.preview.fetched_at = timezone.now()
        self.assertTrue(LinkPreview._is_expired(self.preview))

    @patch.dict('os.environ', {'JUSTONEAPI_TOKEN': 'test-only'})
    @patch.object(LinkPreview, 'fetch_preview_data')
    def test_expired_upstream_response_is_negatively_cached(self, fetch):
        fetch.return_value = dict(url=self.preview.url, title='expired', description='', image_url='', site_name='抖音', favicon_url='', provider_data={'adapter': 'justone', 'video_url': 'https://v.zjcdn.com/a?expires=1'})
        preview, refreshed = LinkPreview.refresh_now(self.preview.id, requester_id=self.vip.id)
        self.assertFalse(refreshed)
        self.assertEqual(preview.status, LinkPreviewStatusChoice.FAILED)
        LinkPreview.refresh_now(preview.id, requester_id=self.vip.id)
        self.assertEqual(fetch.call_count, 1)
        self.preview.provider_data = {'adapter': 'justone', 'video_url': 'https://v.zjcdn.com/a?expires=1'}
        self.assertTrue(LinkPreview._is_expired(self.preview))

    def test_serialization_cannot_charge_sender(self):
        with patch.object(LinkPreview, 'fetch_async') as fetch:
            LinkPreview.queue_for_text(self.preview.url)
            fetch.assert_not_called()
        self.assertFalse(SocialParseUsage.objects.exists())

    @patch.dict('os.environ', {'JUSTONEAPI_TOKEN': 'test-only'})
    @patch.object(LinkPreview, 'fetch_preview_data')
    def test_denial_is_request_local_and_does_not_poison_cache(self, fetch):
        before = self.preview.status
        denied, refreshed = LinkPreview.refresh_now(self.preview.id, requester_id=self.other.id)
        self.assertFalse(refreshed)
        self.assertEqual(denied.jsonl()['parse_error'], 'vip_required')
        self.preview.refresh_from_db()
        self.assertEqual(self.preview.status, before)
        fetch.assert_not_called()

    @patch.dict('os.environ', {'JUSTONEAPI_TOKEN': 'test-only'})
    @patch.object(LinkPreview, 'fetch_preview_data')
    def test_expired_cache_refresh_charges_new_viewer(self, fetch):
        self.preview.status = LinkPreviewStatusChoice.READY
        self.preview.provider_data = {'adapter': 'justone'}
        self.preview.fetched_at = timezone.now() - datetime.timedelta(hours=1)
        self.preview.save()
        fetch.return_value = dict(url=self.preview.url, title='new', description='', image_url='', site_name='抖音', favicon_url='', provider_data={'adapter': 'justone'})
        LinkPreview.refresh_now(self.preview.id, requester_id=self.vip.id)
        self.assertEqual(SocialParseUsage.objects.get(user=self.vip).count, 1)
        self.assertFalse(SocialParseUsage.objects.filter(user=self.sender).exists())
