from unittest.mock import Mock

from django.test import SimpleTestCase

from Message.providers.douyin import DouyinProvider


class DouyinProviderTests(SimpleTestCase):
    def setUp(self):
        self.session = Mock()
        self.provider = DouyinProvider(session=self.session)

    def test_parse_selects_highest_bitrate_for_each_resolution(self):
        video_id = '7688192164567824886'
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            'aweme_id': video_id,
            'title': '千亿美元目标，阿里要提前交卷',
            'author': '口罩哥研报60秒',
            'cover': 'https://p26-sign.douyinpic.com/cover.jpeg',
            'duration': 106434,
            'qualities': [
                {'label': '720p', 'height': 720, 'width': 1280, 'bitrate': 500000, 'url': 'https://v3-dy-o.zjcdn.com/low-720.mp4'},
                {'label': '480p', 'height': 480, 'width': 854, 'bitrate': 300000, 'url': 'https://v3-dy-o.zjcdn.com/high-480.mp4'},
                {'label': '720p', 'height': 720, 'width': 1282, 'bitrate': 755406, 'url': 'https://v3-dy-o.zjcdn.com/high-720.mp4'},
                {'label': '480p', 'height': 480, 'width': 854, 'bitrate': 200000, 'url': 'https://v3-dy-o.zjcdn.com/low-480.mp4'},
            ],
        }
        self.session.post.return_value = response

        result = self.provider.parse(f'https://www.douyin.com/video/{video_id}')

        self.assertEqual(result['video_url'], 'https://v3-dy-o.zjcdn.com/high-720.mp4')
        self.assertEqual(result['width'], 1282)
        self.assertEqual(result['height'], 720)
        self.assertEqual(result['duration_ms'], 106434)
        self.assertEqual(result['author'], '口罩哥研报60秒')
        self.assertEqual(len(result['qualities']), 2)
        self.assertEqual(result['qualities'][1]['url'], 'https://v3-dy-o.zjcdn.com/high-480.mp4')
        self.session.post.assert_called_once_with(
            DouyinProvider.API_URL,
            json={'url': f'https://www.douyin.com/video/{video_id}'},
            headers={'Accept': 'application/json', 'Content-Type': 'application/json'},
            timeout=(3, 20),
        )

    def test_untrusted_media_hosts_are_rejected(self):
        qualities = DouyinProvider._qualities([
            {'label': '720p', 'height': 720, 'width': 1280, 'bitrate': 800000, 'url': 'https://evil.example/video.mp4'},
        ])
        self.assertEqual(qualities, [])

    def test_extracts_video_id_from_modal_url(self):
        url = 'https://www.douyin.com/?modal_id=7146408143612000000'
        self.assertEqual(DouyinProvider.video_id_from_url(url), '7146408143612000000')

    def test_api_failure_returns_none(self):
        self.session.post.side_effect = ValueError('invalid json')
        self.assertIsNone(self.provider.parse('https://v.douyin.com/AbCdEf/'))
