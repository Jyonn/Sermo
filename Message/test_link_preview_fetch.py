from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase

from Message.models import LinkPreview


class LinkPreviewFetchTests(SimpleTestCase):
    @staticmethod
    def response(status_code, *, location='', html=b''):
        response = Mock()
        response.status_code = status_code
        response.headers = {'Content-Type': 'text/html; charset=utf-8'}
        if location:
            response.headers['Location'] = location
        response.encoding = 'utf-8'
        response.iter_content.return_value = [html]
        return response

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_fetch_uses_browser_navigation_headers(self, get, _require_public_host):
        get.return_value = self.response(
            200,
            html=b'<html><head><title>Example</title></head></html>',
        )

        result = LinkPreview.fetch_preview_data('https://example.com/article')

        self.assertEqual(result['title'], 'Example')
        headers = get.call_args.kwargs['headers']
        self.assertIn('Mozilla/5.0', headers['User-Agent'])
        self.assertIn('Chrome/', headers['User-Agent'])
        self.assertEqual(headers['Sec-Fetch-Mode'], 'navigate')
        self.assertIn('zh-CN', headers['Accept-Language'])

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_redirects_keep_browser_navigation_headers(self, get, _require_public_host):
        redirect = self.response(302, location='/final')
        success = self.response(
            200,
            html=b'<html><head><meta property="og:title" content="Final"></head></html>',
        )
        get.side_effect = [redirect, success]

        result = LinkPreview.fetch_preview_data('https://example.com/start')

        self.assertEqual(result['title'], 'Final')
        expected_options = {
            'headers': LinkPreview.BROWSER_HEADERS,
            'timeout': (3, 5),
            'allow_redirects': False,
            'stream': True,
        }
        self.assertEqual(
            get.call_args_list,
            [
                call('https://example.com/start', **expected_options),
                call('https://example.com/final', **expected_options),
            ],
        )
