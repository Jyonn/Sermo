from unittest.mock import Mock,patch
from django.test import SimpleTestCase
from Message.providers.monifa import MonifaProvider
from Message.models import LinkPreview
from django.test import TestCase, RequestFactory
from Space.models import Space
from User.models import User
from utils import auth
from Message.monifa import MonifaOtpView
import json

URL='https://monifa.jyonn.space/#/share/'+'A'*43

class MonifaTests(SimpleTestCase):
    def test_requires_exact_share_fragment_and_host(self):
        self.assertEqual(MonifaProvider.token(URL),'A'*43)
        for url in ['https://monifa.jyonn.space/',URL.replace('/#/share/','/share/'),URL.replace('.space/','.space.evil/'),URL+'/extra',URL+'?bad=1',URL.replace('https:','http:'),URL.replace('monifa.','user@monifa.')]:
            self.assertIsNone(MonifaProvider.token(url),url)

    @patch.object(LinkPreview,'_require_public_host')
    def test_normalization_preserves_only_monifa_share_fragment(self,_):
        self.assertEqual(LinkPreview.normalize_public_url(URL),URL)
        self.assertEqual(LinkPreview.normalize_public_url('https://monifa.jyonn.space/#/tokens'),'https://monifa.jyonn.space/')
        self.assertEqual(LinkPreview.extract_first_url('分享 '+URL+'，'),URL)

    @patch('Message.providers.monifa.requests.get')
    def test_preview_never_fetches_or_persists_codes(self,get):
        with patch.object(LinkPreview,'_require_public_host'):
            result=LinkPreview.fetch_preview_data(URL)
        self.assertEqual(result['provider_data']['provider'],'monifa_otp')
        self.assertNotIn('code',result['provider_data']);get.assert_not_called()

    @patch('Message.providers.monifa.requests.get')
    def test_fresh_code_exposes_only_share_fields_and_fixed_destination(self,get):
        get.return_value=Mock(status_code=200,content=b'{}',headers={'Content-Type':'application/json'})
        get.return_value.json.return_value={'issuer':'Test','accountName':'Example','code':'123456','remaining':20,'period':30,'secret':'must not escape'}
        result=MonifaProvider.read(URL)
        self.assertEqual(result['code'],'123456');self.assertNotIn('secret',result)
        self.assertEqual(get.call_args.args[0],'https://api.monifa.jyonn.space/shares/'+'A'*43)
        self.assertFalse(get.call_args.kwargs['allow_redirects'])
        get.return_value.status_code=404
        self.assertEqual(MonifaProvider.read(URL),{'status':'unavailable'})

    @patch('Message.providers.monifa.requests.get')
    def test_invalid_or_redirected_payload_is_not_a_code(self,get):
        get.return_value=Mock(status_code=302,content=b'',headers={})
        with self.assertRaises(ValueError):MonifaProvider.read(URL)
        get.return_value=Mock(status_code=200,content=b'{}',headers={'Content-Type':'application/json'})
        get.return_value.json.return_value={'code':'123456','remaining':31,'period':30}
        with self.assertRaises(ValueError):MonifaProvider.read(URL)


class MonifaEndpointTests(TestCase):
    def setUp(self):
        space=Space.objects.create(name='OTP',slug='otp',email='otp@example.com')
        self.user=User.objects.create(space=space,name='Reader')
        self.token=auth.get_login_token(self.user)['auth']

    @patch.object(MonifaProvider,'read',return_value={'status':'ready','code':'123456','remaining':20,'period':30})
    def test_authenticated_response_is_uncached_and_matches_envelope(self,read):
        request=RequestFactory().post('/messages/monifa-otp',data=json.dumps({'text':URL}),content_type='application/json',HTTP_AUTHORIZATION='Bearer '+self.token)
        response=MonifaOtpView.as_view()(request)
        self.assertEqual(json.loads(response.content)['identifier'],'OK')
        self.assertEqual(json.loads(response.content)['body']['code'],'123456')
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertEqual(response['Referrer-Policy'],'no-referrer')
        read.assert_called_once_with(URL)
