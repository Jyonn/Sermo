import json
from datetime import timedelta
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from Message.models import LinkPreview, LinkPreviewStatusChoice


class LinkPreviewFetchTests(SimpleTestCase):
    def test_failed_xiaohongshu_login_retries_after_one_minute(self):
        preview = LinkPreview(
            url='https://xhslink.cn/o/8YSCxhrTmhu',
            status=LinkPreviewStatusChoice.FAILED,
            error='xiaohongshu login redirect',
            fetched_at=timezone.now() - timedelta(minutes=2),
        )
        self.assertTrue(LinkPreview._is_expired(preview))

    def test_failed_douyin_gallery_link_retries_after_one_minute(self):
        preview = LinkPreview(
            url='https://v.douyin.com/DpoX9g4EaHU/',
            status=LinkPreviewStatusChoice.FAILED,
            error='douyin provider could not resolve media',
            fetched_at=timezone.now() - timedelta(minutes=2),
        )
        self.assertTrue(LinkPreview._is_expired(preview))

    def test_other_failed_douyin_link_retries_after_five_minutes(self):
        preview = LinkPreview(
            url='https://v.douyin.com/Ruk0ENzuOGE/',
            status=LinkPreviewStatusChoice.FAILED,
            error='connection timeout',
            fetched_at=timezone.now() - timedelta(minutes=6),
        )
        self.assertTrue(LinkPreview._is_expired(preview))

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
    @patch('Message.models.MusicProvider.parse')
    @patch('Message.models.requests.get')
    def test_music_provider_retries_original_link_when_redirect_drops_song_id(self, get, parse, _require_public_host):
        get.side_effect = [
            self.response(302, location='https://music.apple.com/cn/new'),
            self.response(200, html=b'<html><head><title>Apple Music</title></head></html>'),
        ]
        parse.side_effect = [None, {
            'provider': 'apple_music', 'song_id': '1616728075', 'title': 'Power Of A Woman',
            'artists': ['Ella Mai'], 'album': 'Heart On My Sleeve', 'cover_url': '',
            'duration_ms': 0, 'audio_url': '', 'canonical_url': 'https://music.apple.com/us/album/example?i=1616728075',
            'lyrics': {},
        }]

        result = LinkPreview.fetch_preview_data(
            'https://music.apple.com/us/album/power-of-a-woman/1616728060?i=1616728075',
        )

        self.assertEqual(result['provider_data']['provider'], 'apple_music')
        self.assertEqual(parse.call_args_list[0].args[0], 'https://music.apple.com/cn/new')
        self.assertIn('i=1616728075', parse.call_args_list[1].args[0])

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
    @patch('Message.models.DouyinProvider.parse')
    @patch('Message.models.requests.get')
    def test_douyin_short_link_uses_provider(self, get, parse, _require_public_host):
        video_id = '7146408143612000000'
        get.side_effect = [
            self.response(302, location=f'https://www.douyin.com/video/{video_id}'),
            self.response(403),
        ]
        parse.return_value = {
            'provider': 'douyin_video', 'video_id': video_id, 'title': '一段视频',
            'canonical_url': f'https://www.douyin.com/video/{video_id}',
            'video_url': 'https://v3-web.douyinvod.com/video.mp4',
            'cover_url': 'https://p3.douyinpic.com/cover.jpg', 'width': 720, 'height': 1280,
        }

        result = LinkPreview.fetch_preview_data('https://v.douyin.com/AbCdEf/')

        self.assertEqual(result['url'], f'https://www.douyin.com/video/{video_id}')
        self.assertEqual(result['provider_data']['provider'], 'douyin_video')
        self.assertEqual(result['provider_data']['title'], '一段视频')
        self.assertEqual(result['provider_data']['width'], 720)
        self.assertEqual(result['provider_data']['video_url'], 'https://v3-web.douyinvod.com/video.mp4')
        parse.assert_called_once_with(f'https://www.douyin.com/video/{video_id}')

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.DouyinProvider.parse')
    @patch('Message.models.requests.get')
    def test_douyin_share_note_redirect_uses_gallery(self, get, parse, _require_public_host):
        note_id = '7690209569083041893'
        redirected = f'https://www.iesdouyin.com/share/note/{note_id}/'
        get.side_effect = [self.response(302, location=redirected), self.response(200)]
        parse.return_value = {
            'provider': 'douyin_gallery', 'video_id': note_id, 'title': '边境小镇-室韦',
            'canonical_url': f'https://www.douyin.com/note/{note_id}',
            'cover_url': 'https://p3-pc-sign.douyinpic.com/one.jpeg',
            'images': ['https://p3-pc-sign.douyinpic.com/one.jpeg'],
        }

        result = LinkPreview.fetch_preview_data('https://v.douyin.com/DpoX9g4EaHU/')

        self.assertEqual(result['provider_data']['provider'], 'douyin_gallery')
        self.assertEqual(result['title'], '边境小镇-室韦')
        parse.assert_called_once_with(redirected)

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_xiaohongshu_short_link_uses_mobile_headers(self, get, _require_public_host):
        note_id = '68e66fef0000000004023fdb'
        note_url = f'https://www.xiaohongshu.com/discovery/item/{note_id}'
        state = {'noteData': {'data': {'noteData': {
            'noteId': note_id, 'type': 'video', 'title': 'iphone Duo',
            'video': {'media': {'stream': {'h264': [
                {'masterUrl': 'http://sns-video-v6.xhscdn.com/clip.mp4'},
            ]}}},
        }}}}
        html = ('<script>window.__INITIAL_STATE__=' + json.dumps(state) + '</script>').encode()
        get.side_effect = [self.response(302, location=note_url), self.response(200, html=html)]

        result = LinkPreview.fetch_preview_data('https://xhslink.cn/o/8YSCxhrTmhu')

        self.assertEqual(result['provider_data']['provider'], 'xiaohongshu_video')
        self.assertEqual(result['provider_data']['video_url'], 'https://sns-video-v6.xhscdn.com/clip.mp4')
        self.assertIn('iPhone', get.call_args_list[0].kwargs['headers']['User-Agent'])
        self.assertIn('iPhone', get.call_args_list[1].kwargs['headers']['User-Agent'])

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.requests.get')
    def test_xiaohongshu_login_page_is_not_cached_as_ready(self, get, _require_public_host):
        get.side_effect = [
            self.response(302, location='https://www.xiaohongshu.com/login?redirectPath=note'),
            self.response(200, html=b'<title>Login</title>'),
        ]
        with self.assertRaisesRegex(ValueError, 'xiaohongshu login redirect'):
            LinkPreview.fetch_preview_data('https://xhslink.cn/o/8YSCxhrTmhu')

    @patch.object(LinkPreview, '_require_public_host')
    @patch('Message.models.DouyinProvider.parse', return_value=None)
    @patch('Message.models.requests.get')
    def test_douyin_provider_failure_does_not_fall_back(self, get, _parse, _require_public_host):
        get.return_value = self.response(200)
        with self.assertRaisesRegex(ValueError, 'douyin provider could not resolve media'):
            LinkPreview.fetch_preview_data('https://www.douyin.com/video/7146408143612000000')


class LinkPreviewCacheRecoveryTests(TestCase):
    @patch.object(LinkPreview, '_require_public_host')
    def test_old_xiaohongshu_login_preview_retries_original_short_link(self, _require_public_host):
        url = 'https://xhslink.cn/o/8YSCxhrTmhu'
        preview = LinkPreview.objects.create(
            url='https://www.xiaohongshu.com/login?redirectPath=note',
            url_hash=LinkPreview.hash_url(url),
            status=LinkPreviewStatusChoice.READY,
            provider_data={},
            fetched_at=timezone.now(),
        )

        LinkPreview.queue_for_text(f'iphone Duo {url}')

        preview.refresh_from_db()
        self.assertEqual(preview.url, url)
        self.assertEqual(preview.status, LinkPreviewStatusChoice.PENDING)
