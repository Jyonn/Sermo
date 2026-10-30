"""Check app credentials and exact callbacks without authorizing any user."""
import hashlib,base64,secrets
from uuid import UUID
import requests
from django.core.management.base import BaseCommand,CommandError
from User.chronelle_account import config,remote,BindingFailure
from User.chronelle_origins import BASE,MIRROR_BASE,APP_ORIGINS


class Command(BaseCommand):
    help='Validate Chronelle application registration without accessing user calendars'

    def handle(self,*args,**options):
        try:
            client,_,_=config()
            for base in (BASE,MIRROR_BASE):
                result=remote(base,'/api/oauth/introspect/',body={'subject':str(UUID(int=0))})
                if result.get('active') is not False:
                    raise CommandError('Unexpected introspection result')
                embed=requests.get(base+'/oauth/embed/'+client+'/',allow_redirects=False,timeout=15)
                if embed.status_code!=200 or any(origin not in embed.headers.get('Content-Security-Policy','') for origin in APP_ORIGINS):
                    raise CommandError('Chronelle embed or registered display origins are unavailable')
            for origin in sorted(APP_ORIGINS):
                base=MIRROR_BASE if origin.endswith('.6-79.cn') else BASE
                challenge=base64.urlsafe_b64encode(hashlib.sha256(secrets.token_urlsafe(48).encode()).digest()).rstrip(b'=').decode()
                response=requests.get(base+'/oauth/authorize/',params={'client_id':client,
                    'redirect_uri':origin+'/app/research','response_type':'code','authorization_type':'account_binding',
                    'state':secrets.token_urlsafe(32),'code_challenge':challenge,'code_challenge_method':'S256'},
                    allow_redirects=False,timeout=15)
                if response.status_code!=302 or not response.headers.get('Location','').startswith('/#/oauth/authorize/'):
                    raise CommandError('Chronelle rejected a registered callback; no user authorization performed')
        except (BindingFailure,requests.RequestException):
            raise CommandError('Chronelle application validation failed; credential values are hidden')
        self.stdout.write('Chronelle credentials, embed origins and all four callbacks verified. No user data accessed.')
