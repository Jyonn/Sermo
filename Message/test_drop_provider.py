import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from django.test import SimpleTestCase
from Message.providers.drop import DropProvider


class DropProviderTests(SimpleTestCase):
    url = 'https://drop.jyonn.space/?share='+'a'*43

    def response(self, value, status=200):
        now = datetime.now(timezone.utc)
        value = {**value, 'server_time':now.isoformat(), 'expires_at':(now+timedelta(seconds=60)).isoformat()}
        response = MagicMock(status_code=status)
        response.__enter__.return_value = response
        response.iter_content.return_value = [json.dumps(value).encode()]
        return response

    def test_stable_preview_does_not_fetch_or_store_diary_content(self):
        with patch('Message.providers.drop.requests.get') as get:
            value = DropProvider.preview(self.url)
            get.assert_not_called()
        self.assertEqual(value['provider_data'], {'provider':'drop_share','canonical_url':self.url})
        for invalid in [self.url+'&x=1', self.url+'#x', self.url.replace('drop.jyonn.space','evil.test'), None, {}, self.url+'&share='+'b'*43]:
            self.assertIsNone(DropProvider.token(invalid))

    @patch('Message.providers.drop.requests.get')
    def test_bounded_public_summary_excludes_audio_location_and_other_entries(self, get):
        get.return_value = self.response({'entries':[{'text':'日'*300,'occurred_at':'2026-11-15T00:00:00Z','city':'private-place','audio':[{'url':'temporary-signature'}]}, {'text':'second entry'}]})
        value = DropProvider.read(self.url)
        self.assertEqual({key:value[key] for key in ('status','text','truncated','occurred_at','count')}, {'status':'ready','text':'日'*240,'truncated':True,'occurred_at':'2026-11-15T00:00:00Z','count':2})
        self.assertTrue(0 < value['remaining_seconds'] <= 60)
        self.assertIn('expires_at',value)
        self.assertEqual(get.call_args.args[0], 'https://api.drop.jyonn.space/api/public/shares/'+'a'*43+'/')
        self.assertFalse(get.call_args.kwargs['allow_redirects'])

    @patch('Message.providers.drop.requests.get')
    def test_revocation_oversized_and_bad_payload_do_not_retain_body(self,get):
        get.return_value = self.response({},404)
        self.assertEqual(DropProvider.read(self.url), {'status':'gone'})
        get.return_value = self.response({'entries':[]})
        self.assertEqual(DropProvider.read(self.url), {'status':'gone'})
        get.return_value = self.response({'entries':[{'text':{}}]})
        with self.assertRaises(ValueError): DropProvider.read(self.url)

    @patch('Message.providers.drop.requests.get')
    def test_detail_projects_only_public_body_location_and_recording_references(self,get):
        get.return_value = self.response({'entries':[{'text':'full '*100,'city':'公开城市','latitude':30,'longitude':120,'voice_transcripts':['private'],'audio':[{'id':'e08e9580-3df8-4ae6-8d7b-9d1a7c25ad9a','duration_seconds':10,'playback_url':'must not pass through'}]}]})
        entry = DropProvider.read(self.url,detail=True)['entries'][0]
        self.assertEqual(entry['text'],'full '*100)
        self.assertNotIn('voice_transcripts',entry)
        self.assertEqual(entry['city'],'公开城市')
        self.assertNotIn('playback_url',entry['audio'][0])
        get.return_value = self.response({})
        get.return_value.iter_content.return_value = [b' '*(1024*1024+1)]
        with self.assertRaises(ValueError): DropProvider.read(self.url)

    @patch('Message.providers.drop.requests.get')
    def test_audio_authorization_is_fixed_path_short_lived_and_manual(self,get):
        identity='e08e9580-3df8-4ae6-8d7b-9d1a7c25ad9a'
        with self.assertRaises(ValueError): DropProvider.audio(self.url,'../bad')
        get.assert_not_called()
        response=MagicMock(status_code=200,content=b'{}')
        response.json.return_value={'url':'https://cdn.example/audio.wav?token=fixture','expires_in':60}
        get.return_value=response
        self.assertEqual(DropProvider.audio(self.url,identity)['expires_in'],60)
        self.assertTrue(get.call_args.args[0].endswith('/audio/'+identity+'/'))
        response.json.return_value={'url':'http://cdn.example/audio.wav','expires_in':60}
        with self.assertRaises(ValueError): DropProvider.audio(self.url,identity)
        response.status_code=404
        self.assertEqual(DropProvider.audio(self.url,identity),{'status':'gone'})
