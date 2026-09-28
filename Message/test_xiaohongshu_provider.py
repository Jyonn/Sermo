import json

from django.test import SimpleTestCase

from Message.providers.xiaohongshu import XiaohongshuProvider


class XiaohongshuProviderTests(SimpleTestCase):
    URL = 'https://www.xiaohongshu.com/explore/68e66fef0000000004023fdb?xsec_token=abc'

    def html(self, note):
        return '<script>window.__INITIAL_STATE__=' + json.dumps({'note': {'noteDetailMap': {'68e66fef0000000004023fdb': {'note': note}}}}) + '</script>'

    def test_gallery(self):
        result = XiaohongshuProvider.parse(self.URL, self.html({
            'type': 'normal', 'title': '周末散步', 'desc': '照片记录',
            'user': {'nickname': 'Alice'},
            'imageList': [{'urlDefault': 'https://sns-img-qc.xhscdn.com/one'}, {'urlDefault': 'https://evil.example/two'}],
        }))
        self.assertEqual(result['provider'], 'xiaohongshu_gallery')
        self.assertEqual(result['images'], ['https://sns-img-qc.xhscdn.com/one'])

    def test_video(self):
        result = XiaohongshuProvider.parse(self.URL, self.html({
            'type': 'video', 'title': '旅行', 'video': {'media': {'stream': {'h264': [
                {'masterUrl': 'https://sns-video-bd.xhscdn.com/clip.mp4', 'duration': 12000},
            ]}}},
        }))
        self.assertEqual(result['provider'], 'xiaohongshu_video')
        self.assertEqual(result['video_url'], 'https://sns-video-bd.xhscdn.com/clip.mp4')

    def test_untrusted_video_falls_back_to_images(self):
        result = XiaohongshuProvider.parse(self.URL, self.html({
            'type': 'video', 'video': {'media': {'stream': {'h264': [{'masterUrl': 'https://evil.example/clip.mp4'}]}}},
            'imageList': [{'urlDefault': 'https://sns-img-qc.xhscdn.com/one'}],
        }))
        self.assertEqual(result['provider'], 'xiaohongshu_gallery')
