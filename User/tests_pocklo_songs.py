import json
from unittest.mock import patch, MagicMock
from django.test import TestCase
from Message.providers import pocklo

TOKEN='11111111-1111-4111-8111-111111111111'
SONG={'schema':'pocklo.song.v1','type':'song','song_id':TOKEN,'version':2,'available':True,'title':'Silent','artists':[], 'album':'','duration_ms':20000,'cover':None,'lyrics':{'type':'text','text':'<script>not HTML</script>','cues':[]}}

class PockloSongTests(TestCase):
    def test_song_reference_round_trips_without_transient_fields(self):
        from Message.models import LinkPreview, LinkPreviewStatusChoice
        with patch.object(pocklo,'public_body',side_effect=[{'type':'song'},SONG]):
            data=pocklo.preview(TOKEN)
        row=LinkPreview.objects.create(status=LinkPreviewStatusChoice.READY, **data)
        reopened=LinkPreview.objects.get(pk=row.pk).jsonl()
        self.assertEqual(reopened['provider_data']['song_id'],TOKEN)
        self.assertEqual(reopened['url'],f'https://pocklo.jyonn.space/share/{TOKEN}')
        self.assertNotIn('expires_at',json.dumps(reopened))

    def test_song_share_preserves_idempotency_and_legacy_file(self):
        from Space.models import Space
        from User.models import User
        from utils import auth
        space=Space.objects.create(slug='songs',name='Songs',email='songs@example.com')
        user=User.create(space,'Listener',verified=True)
        headers={'HTTP_AUTHORIZATION':'Bearer '+auth.get_login_token(user)['auth']}
        from User.pocklo import binding
        for kind in ('song','file','folder'):
            with patch('User.pocklo.access_for',return_value='access'), patch.object(binding,'remote',return_value={'url':f'https://pocklo.jyonn.space/share/{TOKEN}','type':kind}) as remote, patch.object(pocklo,'public_body',return_value=SONG):
                response=self.client.post('/users/me/pocklo/shares',data=json.dumps({'resource_id':'file-1','operation_id':TOKEN}),content_type='application/json',**headers)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['type'],kind)
            remote.assert_called_once_with('shares/',data={'resource_id':'file-1'},access='access',idempotency_key=f'{user.pk}:{TOKEN}')
            if kind=='song':self.assertEqual(response.json()['song']['song_id'],TOKEN)

    def test_persistent_preview_contains_no_signed_resources_or_lyrics(self):
        with patch.object(pocklo,'public_body',side_effect=[{'type':'song'},dict(SONG,audio_url='secret',cover_url='secret')]):
            result=pocklo.preview(TOKEN)
        self.assertEqual(result['provider_data']['provider'],'pocklo_music')
        self.assertEqual(result['provider_data']['song_id'],TOKEN)
        self.assertNotIn('secret',json.dumps(result))
        self.assertNotIn('lyrics',result['provider_data'])

    def test_public_metadata_is_no_store_and_preserves_text(self):
        with patch.object(pocklo,'public_body',return_value=SONG):
            response=self.client.get(f'/users/pocklo/shares/{TOKEN}/song')
        self.assertEqual(response.status_code,200)
        self.assertIn('no-store',response['Cache-Control'])
        self.assertEqual(response.json()['lyrics']['text'],SONG['lyrics']['text'])

    def test_http_errors_override_success_envelope(self):
        for code in (404,410):
            response=MagicMock(status_code=code,headers={'Content-Type':'application/json'})
            response.__enter__.return_value=response
            with patch.object(pocklo.requests,'get',return_value=response):
                result=self.client.get(f'/users/pocklo/shares/{TOKEN}/playback')
            self.assertEqual(result.status_code,code)
            self.assertIn('no-store',result['Cache-Control'])

    def test_playback_rejects_foreign_cdn_without_proxying_audio(self):
        data={'schema':'pocklo.song.v1','song_id':TOKEN,'version':2,'expires_at':'2026-11-14T00:00:00Z','audio':{'url':'https://evil.example/file','mime':'audio/mpeg'},'cover':None}
        with patch.object(pocklo,'public_body',return_value=data):
            self.assertEqual(self.client.get(f'/users/pocklo/shares/{TOKEN}/playback').status_code,503)
        data['audio']['url']='https://res.6-79.cn/test?token=short'
        with patch.object(pocklo,'public_body',return_value=data):
            result=self.client.get(f'/users/pocklo/shares/{TOKEN}/playback')
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json()['audio']['url'],data['audio']['url'])
