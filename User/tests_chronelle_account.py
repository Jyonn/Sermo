import json
import os
import uuid
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.test import TestCase
from django.utils import timezone
from Space.models import Space
from User.models import User, ChronelleAccount
from User.chronelle_account import credentials, store, unpack, BindingFailure
from Chat.models import Chat, ChatMember, ResearchProject
from utils import auth


class ChronelleAccountTests(TestCase):
    def setUp(self):
        self.client_id=str(uuid.uuid4())
        self.env=patch.dict(os.environ,CHRONELLE_BINDING_CLIENT_ID=self.client_id,CHRONELLE_BINDING_CLIENT_SECRET='test-confidential-secret',CHRONELLE_BINDING_STORAGE_KEY=Fernet.generate_key().decode())
        self.env.start();self.addCleanup(self.env.stop)
        network=patch('User.chronelle_account.requests.request',side_effect=AssertionError('Unexpected network in unit tests'))
        network.start();self.addCleanup(network.stop)
        self.space=Space.objects.create(slug='cal-tests',name='Calendar',email='admin@example.com',research_enabled=True)
        self.user=User.create(self.space,'Viewer',verified=True)
        self.other=User.create(self.space,'Other',verified=True)
        self.chat=Chat.objects.create(space=self.space,created_by=self.user,chat_type=1,purpose=2,title='Project')
        for user,role in [(self.user,1),(self.other,0)]:
            ChatMember.objects.create(chat=self.chat,user=user,role=role,status=1)
        self.project=ResearchProject.objects.create(chat=self.chat,start='2026-10-27')
        self.headers={'HTTP_AUTHORIZATION':'Bearer '+auth.get_login_token(self.user)['auth'],'HTTP_ORIGIN':'https://frienden.jyonn.space'}

    def call(self,body=None,path='/users/me/chronelle/account'):
        return self.client.get(path,**self.headers) if body is None else self.client.post(path,json.dumps(body),content_type='application/json',**self.headers)

    def bound(self,user=None,expired=False):
        row=ChronelleAccount.objects.create(user=user or self.user)
        value={'binding':{'subject':str(uuid.uuid4()),'binding_id':str(uuid.uuid4()),'client_id':self.client_id,'binding_version':1},'access_token':'cba_test-secret','refresh_token':'cbr_test-secret','access_until':timezone.now().timestamp()+(-1 if expired else 900)}
        store(row,value)
        return row,value

    def test_unconfigured_and_unbound_status_are_explicit(self):
        self.assertEqual(self.call().json()['body']['status'],'binding_required')
        with patch.dict(os.environ,CHRONELLE_BINDING_CLIENT_SECRET=''):
            self.assertEqual(self.call().json()['body']['status'],'integration_not_configured')

    @patch('User.chronelle_account.remote')
    def test_authorize_requires_confirmation_and_seals_pkce(self,remote):
        self.assertEqual(self.call({'action':'authorize'}).status_code,400)
        remote.return_value={'request_id':str(uuid.uuid4()),'expires_in':600}
        response=self.call({'action':'authorize','confirmed':True})
        self.assertEqual(response.status_code,200,response.content)
        row=ChronelleAccount.objects.get(user=self.user)
        pending=unpack(row)['pending']
        self.assertNotIn(pending['verifier'],row.sealed)
        self.assertNotIn(pending['verifier'],response.content.decode())
        self.assertEqual(remote.call_args.kwargs['body']['redirect_uri'],'https://frienden.jyonn.space/app/research')
        self.assertIn('/#/bind-calendar/',response.json()['body']['authorize_url'])

    @patch('User.chronelle_account.remote')
    def test_lost_claim_is_not_replayed(self,remote):
        remote.return_value={'request_id':str(uuid.uuid4()),'expires_in':600}
        self.call({'action':'authorize','confirmed':True})
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        self.assertEqual(self.call({'action':'claim'}).status_code,503)
        remote.reset_mock()
        self.assertEqual(self.call({'action':'claim'}).json()['body']['status'],'expired')
        remote.assert_not_called()

    @patch('User.chronelle_account.remote')
    def test_explicit_reauthorization_can_replace_unreadable_ciphertext(self,remote):
        ChronelleAccount.objects.create(user=self.user,sealed='unreadable-old-key-material')
        self.assertEqual(self.call().json()['body']['status'],'binding_required')
        remote.return_value={'request_id':str(uuid.uuid4()),'expires_in':600}
        self.assertEqual(self.call({'action':'authorize','confirmed':True}).status_code,200)
        self.assertIn('pending',unpack(ChronelleAccount.objects.get(user=self.user)))

    @patch('User.chronelle_account.remote')
    def test_exchange_keeps_tokens_only_in_encrypted_storage(self,remote):
        remote.return_value={'request_id':str(uuid.uuid4()),'expires_in':600}
        self.call({'action':'authorize','confirmed':True})
        state=unpack(ChronelleAccount.objects.get(user=self.user))['pending']['state']
        remote.side_effect=[{'state':state,'authorization_code':'cbcode_secret'},
            {'binding':{'subject':str(uuid.uuid4()),'binding_id':str(uuid.uuid4()),'client_id':self.client_id,'binding_version':1},'access_token':'cba_secret','refresh_token':'cbr_secret','expires_in':900}]
        response=self.call({'action':'claim'})
        self.assertEqual(response.json()['body'],{'status':'active'})
        row=ChronelleAccount.objects.get(user=self.user)
        self.assertNotIn('cba_secret',row.sealed)
        self.assertNotIn('cbr_secret',response.content.decode())
        self.assertEqual(unpack(row)['access_token'],'cba_secret')
        remote.side_effect=None;remote.return_value={'active':True}
        self.assertEqual(self.call({'action':'claim'}).json()['body']['status'],'active')
        self.assertTrue(remote.call_args.args[1].endswith('/introspect/'))

    @patch('User.chronelle_account.remote')
    def test_uncertain_refresh_cannot_reuse_old_token(self,remote):
        row,_=self.bound(expired=True)
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')
        row.refresh_from_db();self.assertTrue(unpack(row)['renewal_uncertain'])
        remote.reset_mock()
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')
        remote.assert_not_called()

    @patch('User.chronelle_account.remote')
    def test_member_calendar_hidden_by_default_and_nonmembers_denied(self,remote):
        self.bound();self.bound(self.other)
        remote.return_value=[{'id':7,'name':'Private'}]
        path=f'/research/{self.project.pk}/calendar/'
        self.assertEqual(self.call({'action':'display','members':[self.user.pk,self.other.pk]},path).json()['message'],'member_calendar_not_shared')
        self.assertEqual(self.call({'action':'display','members':[999999]},path).status_code,403)
        self.project.refresh_from_db();self.assertEqual(self.project.calendar_displays,[])

    @patch('User.chronelle_account.remote')
    @patch('Chat.research_calendar.remote')
    def test_own_display_uses_verified_subject_and_current_groups(self,display_remote,remote):
        _,value=self.bound()
        display=str(uuid.uuid4())
        remote.return_value=[{'id':7,'name':'Private'}]
        display_remote.return_value={'display_id':display}
        response=self.call({'action':'display','members':[self.user.pk]},f'/research/{self.project.pk}/calendar/')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response.json()['body']['displays'][0]['url'],f'https://chronelle.jyonn.space/binding-embed/{display}/')
        body=display_remote.call_args.kwargs['body']
        self.assertEqual(body['viewer_subject'],value['binding']['subject'])
        self.assertEqual(body['detail_group_ids'],[7])
        self.assertEqual(body['origins'],['https://frienden.jyonn.space'])
        self.assertNotIn('cba_',response.content.decode())

    @patch('User.chronelle_account.remote')
    def test_policy_cannot_grant_foreign_or_overlapping_groups(self,remote):
        self.bound();remote.return_value=[{'id':7,'name':'Private'}]
        path=f'/research/{self.project.pk}/calendar/'
        for body in [{'detail':[999],'busy':[]},{'detail':[7],'busy':[7]}]:
            self.assertEqual(self.call(dict(body,action='policy'),path).status_code,400)
        self.assertEqual(self.call({'action':'policy','detail':[],'busy':[7]},path).status_code,200)
        self.project.refresh_from_db()
        self.assertEqual(self.project.calendar_policies,{str(self.user.pk):{'detail':[],'busy':[7]}})
