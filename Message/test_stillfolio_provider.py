from unittest.mock import Mock, patch
from datetime import timedelta

from django.test import SimpleTestCase
from django.utils import timezone

from Message.models import LinkPreview, LinkPreviewStatusChoice
from Message.providers.stillfolio import StillfolioProvider


TOKEN = '85c821cc-1593-44aa-80ea-1f6a67a2374d'
URL = f'https://stillfolio.jyonn.space/share/{TOKEN}'


class StillfolioProviderTests(SimpleTestCase):
    def test_only_accepts_exact_share_host_and_uuid(self):
        self.assertEqual(StillfolioProvider.token(URL), TOKEN)
        self.assertIsNone(StillfolioProvider.token(f'https://stillfolio.jyonn.space.evil/share/{TOKEN}'))
        self.assertIsNone(StillfolioProvider.token(f'https://stillfolio.jyonn.space/share/{TOKEN}/assets'))

    def test_failed_share_retries_after_one_minute(self):
        preview = LinkPreview(url=URL, status=LinkPreviewStatusChoice.FAILED, fetched_at=timezone.now() - timedelta(minutes=2))
        self.assertTrue(LinkPreview._is_expired(preview))

    def test_empty_collection_cache_expires_after_one_minute(self):
        now = timezone.now()
        preview = LinkPreview(url=URL, status=LinkPreviewStatusChoice.READY, fetched_at=now - timedelta(minutes=2),
                              provider_data={'provider': 'stillfolio_collection', 'images': [], 'photo_count': 0})
        self.assertTrue(LinkPreview._is_expired(preview, now))
        preview.fetched_at = now - timedelta(seconds=30)
        self.assertFalse(LinkPreview._is_expired(preview, now))

    def test_nonempty_collection_cache_expires_after_five_minutes(self):
        now = timezone.now()
        preview = LinkPreview(url=URL, status=LinkPreviewStatusChoice.READY, fetched_at=now - timedelta(minutes=6),
                              provider_data={'provider': 'stillfolio_collection', 'images': ['https://api.stillfolio.jyonn.space/photo'], 'photo_count': 1})
        self.assertTrue(LinkPreview._is_expired(preview, now))
        preview.fetched_at = now - timedelta(minutes=4)
        self.assertFalse(LinkPreview._is_expired(preview, now))

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.providers.stillfolio.requests.get')
    def test_other_links_produce_plain_cards_without_requests(self, get, _require_public_host):
        for path in ('/', '/auth/sermo/callback', '/share', '/share/not-a-uuid'):
            with self.subTest(path=path):
                url = f'https://{StillfolioProvider.HOST}{path}'
                preview = LinkPreview.fetch_preview_data(url)
                self.assertEqual(preview['url'], url)
                self.assertEqual(preview['provider_data'], {})
                self.assertEqual(preview['title'], '留幅 Stillfolio')
        get.assert_not_called()

    @patch('Message.providers.stillfolio.requests.get')
    def test_parses_public_collection_in_photo_order(self, get):
        get.return_value = Mock(status_code=200, headers={'Content-Type': 'application/json'}, content=b'{}')
        get.return_value.json.return_value = {
            'name': '草原来信', 'description': '秋天的照片', 'owner': {'nickname': 'Jyonn'},
            'photo_order': ['second', 'first'],
            'photos': [
                {'source_id': 'first', 'content_url': f'/api/v1/sync/share/{TOKEN}/assets/11111111-1111-1111-1111-111111111111'},
                {'source_id': 'second', 'content_url': f'/api/v1/sync/share/{TOKEN}/assets/22222222-2222-2222-2222-222222222222'},
            ],
        }
        preview = LinkPreview.fetch_preview_data(URL)
        self.assertEqual(preview['provider_data']['provider'], 'stillfolio_collection')
        self.assertEqual(preview['provider_data']['photo_count'], 2)
        self.assertIn('22222222', preview['image_url'])
        self.assertTrue(preview['image_url'].startswith('https://api.stillfolio.jyonn.space/'))
        self.assertEqual(get.call_args.args[0], f'https://api.stillfolio.jyonn.space/api/v1/sync/share/{TOKEN}')
        self.assertEqual(preview['site_name'], '留幅 Stillfolio')
        get.assert_called_once()

    @patch('Message.providers.stillfolio.requests.get')
    def test_does_not_follow_redirects(self, get):
        get.return_value = Mock(status_code=302, headers={'Location': 'https://elsewhere.example/'}, content=b'')
        with self.assertRaises(ValueError):
            StillfolioProvider.parse(URL)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
