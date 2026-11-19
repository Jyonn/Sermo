import io
from unittest.mock import Mock, patch
from django.test import SimpleTestCase
from PIL import Image
from Message.social_cover import cache_cover


class SocialCoverTests(SimpleTestCase):
    @patch('Message.social_cover.requests.get')
    @patch('Message.social_cover.requests.post')
    @patch('Message.social_cover.build_upload_token', return_value='fixture-token')
    def test_bounded_reencoded_cover_uses_stable_key(self, token, post, get):
        source = io.BytesIO()
        Image.new('RGB', (1200, 600), '#789d94').save(source, 'PNG')
        response = Mock(status_code=200)
        response.iter_content.return_value = [source.getvalue()]
        get.return_value.__enter__.return_value = response
        post.side_effect = lambda *args, **kwargs: Mock(json=lambda: {'key': kwargs['data']['key']})
        key = cache_cover('https://p3.douyinpic.com/cover.png')
        self.assertRegex(key, r'^frienden/social-covers/[a-f0-9]{64}\.webp$')
        request = post.call_args.kwargs
        with Image.open(io.BytesIO(request['files']['file'][1])) as cover:
            self.assertEqual(cover.format, 'WEBP')
            self.assertLessEqual(max(cover.size), 960)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    @patch('Message.social_cover.requests.get')
    def test_untrusted_cover_never_downloaded(self, get):
        self.assertEqual(cache_cover('https://localhost/private'), '')
        get.assert_not_called()

    @patch('Message.social_cover.requests.get')
    @patch('Message.social_cover.requests.post')
    def test_oversized_cover_does_not_upload(self, post, get):
        response = Mock(status_code=200)
        response.iter_content.return_value = [b'x' * (3 * 1024 * 1024 + 1)]
        get.return_value.__enter__.return_value = response
        self.assertEqual(cache_cover('https://p3.douyinpic.com/huge.png'), '')
        post.assert_not_called()
