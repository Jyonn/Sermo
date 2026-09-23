from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from Message.providers.douyin import DouyinProvider


class DouyinProviderTests(SimpleTestCase):
    def setUp(self):
        self.session = Mock()
        self.provider = DouyinProvider(cookie='sessionid=test', session=self.session)

    def test_parse_returns_highest_quality_video(self):
        video_id = '7146408143612000000'
        media_url = 'https://v3-web.douyinvod.com/video/high.mp4'
        response = Mock(status_code=200, content=b'{}')
        response.json.return_value = {
            'status_code': 0,
            'aweme_detail': {
                'aweme_id': video_id,
                'desc': '测试视频',
                'video': {
                    'width': 1080,
                    'height': 1920,
                    'bit_rate': [{'play_addr': {'url_list': [media_url]}}],
                    'play_addr': {'url_list': ['https://v3-web.douyinvod.com/video/low.mp4']},
                    'cover': {'url_list': ['https://p3.douyinpic.com/cover.jpg']},
                },
            },
        }
        self.session.get.return_value = response

        result = self.provider.parse(f'https://www.douyin.com/video/{video_id}')

        self.assertEqual(result['video_url'], media_url)
        self.assertEqual(result['title'], '测试视频')
        self.assertEqual(result['width'], 1080)
        self.assertEqual(self.session.get.call_args.kwargs['headers']['Cookie'], 'sessionid=test')

    def test_parse_uses_xbogus_after_abogus_failure(self):
        video_id = '7146408143612000000'
        failed = Mock(status_code=403, content=b'')
        success = Mock(status_code=200, content=b'{}')
        success.json.return_value = {
            'status_code': 0,
            'aweme_detail': {'aweme_id': video_id, 'video': {'play_addr': {'uri': 'video-uri'}}},
        }
        self.session.get.side_effect = [failed, success]

        result = self.provider.parse(f'https://www.douyin.com/video/{video_id}')

        self.assertIn('aweme.snssdk.com/aweme/v1/play/', result['video_url'])
        self.assertIn('X-Bogus=', self.session.get.call_args.args[0])

    def test_untrusted_media_host_is_rejected(self):
        video = {'play_addr': {'url_list': ['https://evil.example/video.mp4']}}
        self.assertEqual(DouyinProvider._media_url(video), '')

    def test_extracts_video_id_from_modal_url(self):
        url = 'https://www.douyin.com/?modal_id=7146408143612000000'
        self.assertEqual(DouyinProvider.video_id_from_url(url), '7146408143612000000')

    @patch.dict('os.environ', {'DOUYIN_COOKIE': 'sessionid=from-environment'})
    def test_reads_cookie_from_environment(self):
        provider = DouyinProvider(session=self.session)
        self.assertEqual(provider.cookie, 'sessionid=from-environment')
