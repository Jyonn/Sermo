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

    def test_rejects_unrecognized_music_link(self):
        self.assertIsNone(MusicProvider.parse('https://example.com/song'))
