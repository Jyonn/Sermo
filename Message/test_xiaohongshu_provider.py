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

    def test_mobile_share_video_with_http_cdn_urls(self):
        html = '<script>window.__INITIAL_STATE__=' + json.dumps({'noteData': {'data': {'noteData': {
            'noteId': '68e66fef0000000004023fdb', 'type': 'video', 'title': 'iphone Duo',
            'user': {'nickName': '遂宁叮当通讯'},
            'imageList': [{'url': 'http://sns-webpic-qc.xhscdn.com/cover'}],
            'video': {'media': {'stream': {'h264': [
                {'masterUrl': 'http://sns-video-v6.xhscdn.com/clip.mp4', 'duration': 23000},
            ]}}},
        }}}}) + '</script>'
        url = 'https://www.xiaohongshu.com/discovery/item/68e66fef0000000004023fdb'

        result = XiaohongshuProvider.parse(url, html)

        self.assertEqual(result['provider'], 'xiaohongshu_video')
        self.assertEqual(result['author'], '遂宁叮当通讯')
        self.assertEqual(result['cover_url'], 'https://sns-webpic-qc.xhscdn.com/cover')
        self.assertEqual(result['video_url'], 'https://sns-video-v6.xhscdn.com/clip.mp4')
        self.assertTrue(XiaohongshuProvider.supports('https://xhslink.cn/o/8YSCxhrTmhu'))

    def test_mobile_share_gallery_preserves_all_images(self):
        note_id = '6aba6cdf0000000014032ea4'
        images = [f'http://sns-webpic-qc.xhscdn.com/photo-{index}.jpg' for index in range(13)]
        html = '<script>window.__INITIAL_STATE__=' + json.dumps({'noteData': {'data': {'noteData': {
            'noteId': note_id, 'type': 'normal',
            'title': '大兴安岭追秋day4，额尔古纳→根河→满归',
            'user': {'nickName': 'Ocean'},
            'imageList': [{'url': image} for image in images],
        }}}}) + '</script>'

        result = XiaohongshuProvider.parse(f'https://www.xiaohongshu.com/discovery/item/{note_id}', html)

        self.assertEqual(result['provider'], 'xiaohongshu_gallery')
        self.assertEqual(result['author'], 'Ocean')
        self.assertEqual(result['images'], [image.replace('http:', 'https:', 1) for image in images])

    def test_untrusted_video_falls_back_to_images(self):
        result = XiaohongshuProvider.parse(self.URL, self.html({
            'type': 'video', 'video': {'media': {'stream': {'h264': [{'masterUrl': 'https://evil.example/clip.mp4'}]}}},
            'imageList': [{'urlDefault': 'https://sns-img-qc.xhscdn.com/one'}],
        }))
        self.assertEqual(result['provider'], 'xiaohongshu_gallery')
