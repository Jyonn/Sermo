from unittest.mock import Mock

from django.test import SimpleTestCase

from Message.providers.music import MusicProvider


class MusicProviderTests(SimpleTestCase):
    @staticmethod
    def response(payload):
        response = Mock()
        response.json.return_value = payload
        return response

    def test_qq_song_resolves_metadata_audio_and_lyrics(self):
        session = Mock()
        session.get.side_effect = [
            self.response({'data': [{'name': '测试歌曲', 'interval': 180, 'singer': [{'name': '歌手'}], 'album': {'name': '专辑', 'mid': 'ALBUM'}, 'file': {'media_mid': 'MEDIA'}}]}),
            self.response({'data': {'items': [{'filename': 'C400MEDIA.m4a', 'vkey': 'signed'}]}}),
            self.response({'lyric': '[00:00.00]歌词', 'trans': ''}),
        ]

        result = MusicProvider.parse('https://y.qq.com/n/ryqq/songDetail/0014kBdU3LPmCA', session=session)

        self.assertEqual(result['provider'], 'qq_music')
        self.assertEqual(result['artists'], ['歌手'])
        self.assertIn('vkey=signed', result['audio_url'])
        self.assertEqual(result['lyrics']['original'], '[00:00.00]歌词')

    def test_kugou_song_resolves_from_page_hash(self):
        session = Mock()
        session.get.return_value = self.response({'data': {
            'song_name': '测试歌曲', 'author_name': '歌手', 'album_name': '专辑',
            'img': 'http://img.kugou.com/cover.jpg', 'timelength': 190000,
            'play_url': 'http://fs.kugou.com/song.mp3', 'lyrics': '[00:00.00]歌词',
        }})

        result = MusicProvider.parse(
            'https://www.kugou.com/song/',
            '<script>var data={"hash":"D1D7835F9BED257E613A2C854333667B","album_id":"966846"}</script>',
            session=session,
        )

        self.assertEqual(result['provider'], 'kugou_music')
        self.assertEqual(result['audio_url'], 'https://fs.kugou.com/song.mp3')
        self.assertEqual(result['cover_url'], 'https://img.kugou.com/cover.jpg')

    def test_kugou_short_link_landing_page_resolves_from_query(self):
        session = Mock()
        session.get.return_value = self.response({'data': {
            'song_name': '测试歌曲', 'author_name': '歌手',
            'play_url': 'https://fs.kugou.com/song.mp3',
        }})
        url = (
            'https://h5.kugou.com/v2/index.html?'
            'hash=46f6a43d131a412c7a06317ec9f9e4cc&album_id=75207287&album_audio_id=526165282'
        )

        result = MusicProvider.parse(url, session=session)

        self.assertTrue(MusicProvider.supports('https://t1.kugou.com/5MbFp65G5V2'))
        self.assertEqual(result['song_id'], '46F6A43D131A412C7A06317EC9F9E4CC')
        self.assertEqual(result['title'], '测试歌曲')

    def test_qishui_share_page_resolves_router_data(self):
        html = '''<script>_ROUTER_DATA = {"loaderData":{"track_page":{
          "track_id":"7123456789012345678","audioWithLyricsOption":{
            "track_id":"7123456789012345678","trackName":"汽水歌曲","artistName":"歌手",
            "albumName":"专辑","coverURL":"https://example.com/cover.jpg",
            "url":"https://example.com/song.m4a","duration":183000}}}}};</script>'''

        result = MusicProvider.parse(
            'https://music.douyin.com/qishui/share/track?track_id=7123456789012345678', html,
        )

        self.assertEqual(result['provider'], 'qishui_music')
        self.assertEqual(result['title'], '汽水歌曲')
        self.assertEqual(result['audio_url'], 'https://example.com/song.m4a')

    def test_apple_music_song_uses_catalog_preview(self):
        session = Mock()
        session.get.return_value = self.response({'results': [{
            'trackId': 1616728075, 'trackName': 'Power Of A Woman', 'artistName': 'Ella Mai',
            'collectionName': 'Heart On My Sleeve', 'trackTimeMillis': 203000,
            'artworkUrl100': 'https://example.com/100x100bb.jpg',
            'previewUrl': 'https://audio.example.com/preview.m4a',
            'trackViewUrl': 'https://music.apple.com/us/album/example/1616728060?i=1616728075&uo=4',
        }]})

        result = MusicProvider.parse(
            'https://music.apple.com/us/album/example/1616728060?i=1616728075', session=session,
        )

        self.assertEqual(result['provider'], 'apple_music')
        self.assertEqual(result['artists'], ['Ella Mai'])
        self.assertEqual(result['cover_url'], 'https://example.com/600x600bb.jpg')
        self.assertEqual(result['audio_url'], 'https://audio.example.com/preview.m4a')

    def test_kuwo_song_combines_page_metadata_and_public_audio(self):
        session = Mock()
        session.get.side_effect = [
            self.response({'data': None, 'status': 301}),
            self.response({'code': 200, 'url': 'https://kw.example.com/song.mp3'}),
        ]
        html = '<title>晴天_周杰伦_单曲在线试听_酷我音乐</title><script>var song={name:"晴天",artist:"周杰伦",album:"叶惠美",pic120:"https:\\/\\/img.example.com\\/cover.jpg"}</script>'

        result = MusicProvider.parse('https://www.kuwo.cn/play_detail/228908', html, session=session)

        self.assertEqual(result['provider'], 'kuwo_music')
        self.assertEqual(result['title'], '晴天')
        self.assertEqual(result['artists'], ['周杰伦'])
        self.assertEqual(result['cover_url'], 'https://img.example.com/cover.jpg')
        self.assertEqual(result['audio_url'], 'https://kw.example.com/song.mp3')

    def test_rejects_unrecognized_music_link(self):
        self.assertIsNone(MusicProvider.parse('https://example.com/song'))
