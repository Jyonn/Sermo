import json
from unittest.mock import Mock

from django.test import SimpleTestCase

from Message.providers.douyin import DouyinProvider


class DouyinProviderTests(SimpleTestCase):
    VIDEO_ID = '7689675824143879462'
    NOTE_ID = '7674182055039082484'

    def setUp(self):
        self.session = Mock()
        self.provider = DouyinProvider(session=self.session)

    @staticmethod
    def response(*, payload=None, html=None, status=200, location=None):
        response = Mock(status_code=status)
        response.headers = {'Content-Type': 'text/html' if html is not None else 'application/json'}
        if location:
            response.headers['Location'] = location
        response.json.return_value = payload
        response.iter_content.return_value = [html.encode()] if html is not None else []
        return response

    def test_short_video_uses_douyin_feed_and_selects_exact_h264_work(self):
        short = self.response(status=302, location=f'https://www.iesdouyin.com/share/video/{self.VIDEO_ID}/')
        feed = self.response(payload={'aweme_list': [
            {'aweme_id': '7690407742012304996', 'video': {'play_addr_h264': {
                'url_list': ['https://v5.douyinvod.com/wrong.mp4']}}},
            {'aweme_id': self.VIDEO_ID, 'desc': 'AI怎么让NS方程爆炸',
             'author': {'nickname': '漫士沉思录'}, 'video': {
                 'duration': 2459254, 'width': 1280, 'height': 720,
                 'cover': {'url_list': ['https://p26-sign.douyinpic.com/cover.jpeg']},
                 'play_addr_h264': {'width': 1024, 'height': 576, 'url_list': [
                     'https://v5-coldb.douyinvod.com/h264.mp4']},
                 'play_addr_265': {'url_list': ['https://v5-coldb.douyinvod.com/h265.mp4']},
             }},
        ]})
        self.session.get.side_effect = [short, feed]

        result = self.provider.parse('https://v.douyin.com/cIDUOEui3qA/')

        self.assertEqual(result['provider'], 'douyin_video')
        self.assertEqual(result['video_id'], self.VIDEO_ID)
        self.assertEqual(result['video_url'], 'https://v5-coldb.douyinvod.com/h264.mp4')
        self.assertEqual(result['duration_ms'], 2459254)
        self.assertEqual(result['height'], 576)
        self.assertEqual(result['author'], '漫士沉思录')
        self.assertEqual(self.session.get.call_args_list[1].args[0], DouyinProvider.FEED_URL)
        self.session.post.assert_not_called()

    def test_gallery_reads_public_note_json_ld_and_filters_media_hosts(self):
        images = [f'https://p3-pc-sign.douyinpic.com/{index}.jpeg' for index in range(105)]
        html = '<script data-rh="true" type="application/ld+json">' + json.dumps({
            '@type': 'article', 'headline': '用100张胶片图打开内蒙古的秋天',
            'author': {'name': '一屋桉园'}, 'image': images + ['https://evil.example/other.jpeg'],
        }) + '</script>'
        self.session.get.return_value = self.response(html=html)

        result = self.provider.parse(f'https://www.douyin.com/note/{self.NOTE_ID}')

        self.assertEqual(result['provider'], 'douyin_gallery')
        self.assertEqual(result['author'], '一屋桉园')
        self.assertEqual(result['images'], images[:100])
        self.assertEqual(result['canonical_url'], f'https://www.douyin.com/note/{self.NOTE_ID}')
        self.session.post.assert_not_called()

    def test_short_gallery_redirects_to_public_note(self):
        short = self.response(status=302, location=f'https://www.iesdouyin.com/share/note/{self.NOTE_ID}/')
        note = self.response(html='<script type="application/ld+json">' + json.dumps({
            '@type': 'article', 'headline': '图文',
            'image': ['https://p3-pc-sign.douyinpic.com/one.jpeg'],
        }) + '</script>')
        self.session.get.side_effect = [short, note]

        result = self.provider.parse('https://v.douyin.com/Ruk0ENzuOGE/')

        self.assertEqual(result['provider'], 'douyin_gallery')
        self.assertEqual(self.session.get.call_args_list[1].args[0], f'https://www.douyin.com/note/{self.NOTE_ID}')

    def test_rejects_untrusted_short_link_redirect(self):
        self.session.get.return_value = self.response(status=302, location='https://evil.example/video/7689675824143879462')
        self.assertIsNone(self.provider.parse('https://v.douyin.com/cIDUOEui3qA/'))
        self.assertEqual(self.session.get.call_count, 1)

    def test_does_not_return_recommended_video_when_target_missing(self):
        self.session.get.side_effect = [
            self.response(payload={'aweme_list': [{'aweme_id': '7690407742012304996'}]}),
            self.response(html='<html></html>'),
        ]
        self.assertIsNone(self.provider.parse(f'https://www.douyin.com/video/{self.VIDEO_ID}'))

    def test_does_not_select_h265_stream_for_browser_playback(self):
        self.session.get.side_effect = [
            self.response(payload={'aweme_list': [{'aweme_id': self.VIDEO_ID, 'video': {
                'is_h265': 1, 'play_addr': {'url_list': ['https://v5.douyinvod.com/h265.mp4']},
            }}]}),
            self.response(html='<html></html>'),
        ]
        self.assertIsNone(self.provider.parse(f'https://www.douyin.com/video/{self.VIDEO_ID}'))

    def test_extracts_id_from_modal_and_slides_urls(self):
        self.assertEqual(DouyinProvider.video_id_from_url(
            f'https://www.douyin.com/?modal_id={self.VIDEO_ID}'), self.VIDEO_ID)
        self.assertEqual(DouyinProvider.video_id_from_url(
            f'https://www.iesdouyin.com/share/slides/{self.NOTE_ID}/'), self.NOTE_ID)

    def test_all_douyin_requests_use_browser_identity(self):
        short = self.response(status=302, location=f'https://www.iesdouyin.com/share/slides/{self.NOTE_ID}/')
        note = self.response(html='<html></html>')
        self.session.get.side_effect = [short, note]

        self.provider.parse('https://v.douyin.com/Ruk0ENzuOGE/')

        for call in self.session.get.call_args_list:
            headers = call.kwargs['headers']
            self.assertIn('Mozilla/5.0', headers['User-Agent'])
            self.assertIn('Chrome/', headers['User-Agent'])
            self.assertNotIn('bot', headers['User-Agent'].lower())

        self.session.get.reset_mock()
        self.session.get.side_effect = [self.response(payload={'aweme_list': []}), note]
        self.provider.parse(f'https://www.douyin.com/video/{self.VIDEO_ID}')
        self.assertIn('Chrome/', self.session.get.call_args_list[0].kwargs['headers']['User-Agent'])
