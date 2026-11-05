import re
from urllib.parse import urlparse
import requests


class MonifaProvider:
    HOST = 'monifa.jyonn.space'
    SHARE = re.compile(r'^/share/([A-Za-z0-9_-]{32,128})$')

    @classmethod
    def token(cls, url):
        try:
            parsed = urlparse(url or '')
            if parsed.scheme != 'https' or parsed.hostname != cls.HOST or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.path != '/' or parsed.query:
                return None
            match = cls.SHARE.fullmatch(parsed.fragment)
            return match.group(1) if match else None
        except ValueError:
            return None

    @classmethod
    def preview(cls, url):
        token=cls.token(url)
        if not token: return None
        canonical=f'https://{cls.HOST}/#/share/{token}'
        return dict(url=canonical,title='Monifa OTP',description='',image_url='',site_name='Monifa',favicon_url='',provider_data={'provider':'monifa_otp','canonical_url':canonical})

    @classmethod
    def read(cls, url):
        token=cls.token(url)
        if not token: raise ValueError('invalid share')
        response=requests.get(f'https://api.monifa.jyonn.space/shares/{token}',headers={'Accept':'application/json'},timeout=(3,7),allow_redirects=False)
        if response.status_code==404: return {'status':'unavailable'}
        if response.status_code!=200 or len(response.content)>8192 or 'application/json' not in response.headers.get('Content-Type',''):
            raise ValueError('share unavailable')
        data=response.json()
        if not isinstance(data,dict) or not isinstance(data.get('code'),str) or not re.fullmatch(r'\d{6}|\d{8}',data['code']): raise ValueError('invalid otp')
        period=data.get('period');remaining=data.get('remaining')
        if type(period)!=int or type(remaining)!=int or not 1<=period<=300 or not 0<=remaining<=period: raise ValueError('invalid validity')
        return {'status':'ready','code':data['code'],'period':period,'remaining':remaining,'issuer':str(data.get('issuer',''))[:100],'accountName':str(data.get('accountName',''))[:150]}
