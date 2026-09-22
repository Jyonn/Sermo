import json
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

    @staticmethod
    def json_response(data, status_code=200):
        response = Mock()
        response.status_code = status_code
        response.json.return_value = data
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

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_uses_largest_icon_as_image_fallback(self, get, _require_public_host):
        get.return_value = self.response(
            200,
            html=(
                b'<html><head><title>Icons</title>'
                b'<link rel="icon" sizes="16x16" href="/favicon-16.png">'
                b'<link rel="icon" sizes="192x192" href="/favicon-192.png">'
                b'</head></html>'
            ),
        )

        result = LinkPreview.fetch_preview_data('https://example.com/article')

        self.assertEqual(result['image_url'], 'https://example.com/favicon-192.png')
        self.assertEqual(result['favicon_url'], 'https://example.com/favicon-192.png')

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_open_graph_image_has_priority_over_icon(self, get, _require_public_host):
        get.return_value = self.response(
            200,
            html=(
                b'<html><head><meta property="og:image" content="/cover.jpg">'
                b'<link rel="apple-touch-icon" sizes="180x180" href="/touch.png">'
                b'</head></html>'
            ),
        )

        result = LinkPreview.fetch_preview_data('https://example.com/article')

        self.assertEqual(result['image_url'], 'https://example.com/cover.jpg')
        self.assertEqual(result['favicon_url'], 'https://example.com/touch.png')

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_netease_song_preview_includes_music_and_lyrics(self, get, _require_public_host):
        redirect = self.response(302, location='https://y.music.163.com/m/song?id=287726')
        redux_state = {
            'Song': {
                'id': 287726,
                'name': '累赘',
                'ar': [{'id': 9272, 'name': '孙燕姿'}],
                'al': {'name': '我要的幸福', 'picUrl': 'http://p1.music.126.net/cover.jpg'},
                'dt': 194093,
            },
        }
        html = (
            '<html><head><meta property="og:title" content="累赘"></head><body>'
            f'<script>window.REDUX_STATE = {json.dumps(redux_state, ensure_ascii=False)};</script>'
            '</body></html>'
        ).encode()
        song_page = self.response(200, html=html)
        lyrics = self.json_response({'lrc': {'lyric': '[00:00.00]累赘'}, 'tlyric': {'lyric': ''}})
        get.side_effect = [redirect, song_page, lyrics]

        result = LinkPreview.fetch_preview_data('https://163cn.tv/example')

        self.assertEqual(result['url'], 'https://music.163.com/#/song?id=287726')
        self.assertEqual(result['title'], '累赘')
        self.assertEqual(result['description'], '孙燕姿')
        self.assertEqual(result['image_url'], 'https://p1.music.126.net/cover.jpg')
        self.assertEqual(result['site_name'], '网易云音乐')
        self.assertEqual(result['provider_data']['audio_url'], 'https://music.163.com/song/media/outer/url?id=287726.mp3')
        self.assertEqual(result['provider_data']['lyrics']['original'], '[00:00.00]累赘')

    @patch.object(LinkPreview, '_require_public_host')
    def test_normalizes_netease_hash_song_url(self, _require_public_host):
        result = LinkPreview.normalize_public_url('https://music.163.com/#/song?id=287726')

        self.assertEqual(result, 'https://y.music.163.com/m/song?id=287726')

    def test_playlist_id_is_not_a_song(self):
        self.assertIsNone(LinkPreview._netease_song_id_from_url('https://music.163.com/#/playlist?id=287726'))
        self.assertIsNone(LinkPreview._netease_song_id_from_url('https://music.163.com/mv?id=287726'))

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_douyin_short_link_uses_official_embed_when_page_is_blocked(self, get, _require_public_host):
        video_id = '7146408143612000000'
        get.side_effect = [
            self.response(302, location=f'https://www.douyin.com/video/{video_id}'),
            self.response(403),
            self.json_response({
                'err_no': 0,
                'data': {
                    'iframe_code': f'<iframe src="https://open.douyin.com/player/video?vid={video_id}&autoplay=0"></iframe>',
                    'video_title': '一段视频',
                    'video_width': 720,
                    'video_height': 1280,
                },
            }),
        ]

        result = LinkPreview.fetch_preview_data('https://v.douyin.com/AbCdEf/')

        self.assertEqual(result['url'], f'https://www.douyin.com/video/{video_id}')
        self.assertEqual(result['provider_data']['provider'], 'douyin_video')
        self.assertEqual(result['provider_data']['title'], '一段视频')
        self.assertEqual(result['provider_data']['width'], 720)
        self.assertEqual(get.call_args.kwargs['params'], {'video_id': video_id})

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_douyin_rejects_untrusted_iframe(self, get, _require_public_host):
        get.return_value = self.json_response({
            'err_no': 0,
            'data': {'iframe_code': '<iframe src="https://evil.example/player/video?vid=7146408143612000000"></iframe>'},
        })

        self.assertEqual(LinkPreview._douyin_video_data('https://www.douyin.com/video/7146408143612000000'), {})

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_douyin_private_video_has_no_embed(self, get, _require_public_host):
        get.return_value = self.json_response({'err_no': 28003004, 'err_msg': '非公开视频'})

        self.assertEqual(LinkPreview._douyin_video_data('https://www.douyin.com/video/7146408143612000000'), {})

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_douyin_page_metadata_is_kept_with_official_player(self, get, _require_public_host):
        video_id = '7146408143612000000'
        get.side_effect = [
            self.response(200, html=b'<meta property="og:image" content="https://www.douyin.com/cover.jpg">'),
            self.json_response({'err_no': 0, 'data': {
                'iframe_code': f'<iframe src="https://open.douyin.com/player/video?vid={video_id}"></iframe>',
                'video_title': 'Video title',
            }}),
        ]

        result = LinkPreview.fetch_preview_data(f'https://www.douyin.com/video/{video_id}')

        self.assertEqual(result['title'], 'Video title')
        self.assertEqual(result['image_url'], 'https://www.douyin.com/cover.jpg')
        self.assertEqual(result['provider_data']['video_id'], video_id)
