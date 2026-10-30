import json
import os
import uuid
from unittest.mock import patch
from cryptography.fernet import Fernet
from django.test import TestCase
from django.utils import timezone
from Space.models import Space
from User.models import User, ChronelleAccount
from User.chronelle_account import authority, credentials, store, unpack, BindingFailure
from Chat.models import Chat, ChatMember, ResearchProject
from utils import auth


class ChronelleAccountTests(TestCase):
    def setUp(self):
        self.client_id='coapp_test_application_123456789'
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
        value={'protocol':'oauth-v1','binding':{'subject':str(uuid.uuid4()),'binding_id':str(uuid.uuid4()),'client_id':self.client_id,'binding_version':1,'expires_at':(timezone.now()+timezone.timedelta(days=180)).timestamp()},'access_token':'cba_test-secret','refresh_token':'cbr_test-secret','access_until':timezone.now().timestamp()+(-1 if expired else 900)}
        store(row,value)
        return row,value

    def test_unconfigured_and_unbound_status_are_explicit(self):
        self.assertEqual(self.call().json()['body']['status'],'binding_required')
        with patch.dict(os.environ,CHRONELLE_BINDING_CLIENT_SECRET=''):
            self.assertEqual(self.call().json()['body']['status'],'integration_not_configured')

    def test_legacy_authority_requires_rebinding_without_network(self):
        row,value=self.bound();value.pop('protocol');store(row,value)
        response=self.call().json()['body']
        self.assertEqual(response,{'status':'binding_required','rebind_required':True})
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')

    def test_retired_display_metadata_does_not_require_an_unbound_owner_to_reconnect(self):
        from User.chronelle_account import revocation_credentials
        from Chat.research_calendar import revoke_displays
        self.assertEqual(revocation_credentials(self.other,'https://chronelle.jyonn.space'),{'revoked':True})
        self.project.calendar_displays=[{'id':str(uuid.uuid4()),'owner':self.other.pk,'viewer':self.user.pk,'until':timezone.now().timestamp()+300}]
        revoke_displays(self.project,'https://chronelle.jyonn.space',{})
        self.assertEqual(self.project.calendar_displays,[])

    def test_all_four_callback_origins_and_unknown_origin_rejected(self):
        from User.chronelle_origins import APP_ORIGINS
        for origin in APP_ORIGINS:
            self.headers['HTTP_ORIGIN']=origin
            self.call({'action':'authorize','confirmed':True})
            pending=unpack(ChronelleAccount.objects.get(user=self.user))['pending']
            self.assertEqual(pending['redirect'],origin+'/app/research')
            self.assertEqual(pending['base'],'https://chronelle.6-79.cn' if origin.endswith('.6-79.cn') else 'https://chronelle.jyonn.space')
        self.headers['HTTP_ORIGIN']='https://evil.example'
        self.assertEqual(self.call({'action':'authorize','confirmed':True}).status_code,403)

    @patch('User.chronelle_account.remote')
    def test_invalid_state_does_not_consume_callback_or_exchange(self,remote):
        self.call({'action':'authorize','confirmed':True})
        self.assertEqual(self.call({'action':'claim','code':'coc_test','state':'wrong-state'}).status_code,403)
        remote.assert_not_called()
        self.assertIn('pending',unpack(ChronelleAccount.objects.get(user=self.user)))

    def test_explicit_unbind_does_not_trigger_migration_prompt(self):
        row,value=self.bound();value.pop('protocol');store(row,value)
        self.assertEqual(self.call({'action':'revoke','confirmed':True}).status_code,200)
        self.assertFalse(self.call().json()['body']['rebind_required'])

    def test_config_model_is_server_only_and_preferred(self):
        from Config.models import Config,CI
        from User.chronelle_account import config
        Config.update_value(CI.CHRONELLE_CLIENT_ID,'coapp_config_application_123456')
        Config.update_value(CI.CHRONELLE_CLIENT_SECRET,'server-only-test-secret-12345678901234')
        Config.update_value(CI.CHRONELLE_STORAGE_KEY,Fernet.generate_key().decode())
        self.assertEqual(config()[0],'coapp_config_application_123456')
        self.assertNotIn('server-only-test-secret',self.call().content.decode())

    def test_upstream_auth_failure_never_becomes_a_frienden_login_failure(self):
        from User.chronelle_account import remote
        with patch('User.chronelle_account.requests.request') as send:
            send.return_value.status_code=401
            send.return_value.content=b'error'
            send.return_value.json.return_value={'code':'authorization_expired','detail':'never relay upstream prose'}
            with self.assertRaises(BindingFailure) as caught:
                remote('https://chronelle.jyonn.space','/api/oauth/introspect/',body={'subject':str(uuid.uuid4())})
            self.assertEqual(caught.exception.code,'binding_expired')
            self.assertEqual(caught.exception.status,409)

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
        self.assertEqual(pending['redirect'],'https://frienden.jyonn.space/app/research')
        self.assertIn('/oauth/authorize/',response.json()['body']['authorize_url'])
        remote.assert_not_called()

    @patch('User.chronelle_account.remote')
    def test_lost_claim_is_not_replayed(self,remote):
        remote.return_value={'request_id':str(uuid.uuid4()),'expires_in':600}
        self.call({'action':'authorize','confirmed':True})
        state=unpack(ChronelleAccount.objects.get(user=self.user))['pending']['state']
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        self.assertEqual(self.call({'action':'claim','code':'coc_test','state':state}).status_code,503)
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
        remote.return_value={'authorization_type':'account_binding','subject':str(uuid.uuid4()),'grant_id':str(uuid.uuid4()),'grant_version':1,'grant_expires_at':(timezone.now()+timezone.timedelta(days=180)).isoformat(),'access_token':'cba_secret','refresh_token':'cbr_secret','expires_in':900}
        response=self.call({'action':'claim','state':state,'code':'coc_test'})
        self.assertEqual(response.json()['body'],{'status':'active'})
        row=ChronelleAccount.objects.get(user=self.user)
        self.assertNotIn('cba_secret',row.sealed)
        self.assertNotIn('cbr_secret',response.content.decode())
        self.assertEqual(unpack(row)['access_token'],'cba_secret')
        remote.side_effect=None;remote.return_value={'active':True}
        self.assertEqual(self.call().json()['body']['status'],'active')
        self.assertTrue(remote.call_args.args[1].endswith('/introspect/'))

    @patch('User.chronelle_account.remote')
    def test_uncertain_refresh_cannot_reuse_old_token(self,remote):
        row,_=self.bound(expired=True)
        remote.side_effect=[{'active':True},BindingFailure('chronelle_unavailable',503)]
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')
        self.assertTrue(unpack(authority(unpack(row)))['renewal_uncertain'])
        remote.reset_mock()
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')
        remote.assert_not_called()

    @patch('User.chronelle_account.remote')
    def test_same_subject_across_spaces_shares_latest_credentials_and_one_refresh(self,remote):
        row,old=self.bound(expired=True)
        other_space=Space.objects.create(slug='other-cal-space',name='Other',email='other@example.com')
        other=User.create(other_space,'Same person',verified=True)
        latest=dict(old,binding=dict(old['binding'],binding_version=2),access_token='new-access',refresh_token='new-refresh')
        store(ChronelleAccount.objects.create(user=other),latest)
        fresh={'authorization_type':'account_binding','subject':old['binding']['subject'],'grant_id':old['binding']['binding_id'],
               'grant_version':2,'grant_expires_at':(timezone.now()+timezone.timedelta(days=180)).isoformat(),
               'access_token':'rotated-access','refresh_token':'rotated-refresh','expires_in':900}
        def rotate(base,path,**kwargs):
            if path.endswith('/introspect/'):
                self.assertEqual(kwargs['body'],{'token':'new-refresh'})
                return {'active':True}
            self.assertEqual(kwargs['body']['refresh_token'],'new-refresh')
            # A request racing with rotation must not tell the user to rebind.
            status=self.call().json()['body']
            self.assertEqual(status,{'status':'chronelle_unavailable','rebind_required':False})
            with self.assertRaises(BindingFailure) as concurrent:
                credentials(other,base)
            self.assertEqual(concurrent.exception.code,'chronelle_unavailable')
            return fresh
        remote.side_effect=rotate
        self.assertEqual(credentials(self.user,'https://chronelle.jyonn.space')['access_token'],'rotated-access')
        self.assertEqual(credentials(other,'https://chronelle.jyonn.space')['access_token'],'rotated-access')
        self.assertEqual(remote.call_count,2)
        self.assertEqual(unpack(authority(old))['refresh_token'],'rotated-refresh')

    @patch('User.chronelle_account.remote')
    def test_malformed_introspection_is_not_treated_as_revoked(self,remote):
        self.bound();remote.return_value={}
        self.assertEqual(self.call().json()['body'],{'status':'chronelle_unavailable','rebind_required':False})

    @patch('User.chronelle_account.remote')
    def test_different_subjects_do_not_share_credentials(self,remote):
        _,left=self.bound();_,right=self.bound(self.other)
        self.assertNotEqual(authority(left).pk,authority(right).pk)
        self.assertEqual(credentials(self.user,'https://chronelle.jyonn.space')['binding']['subject'],left['binding']['subject'])
        remote.assert_not_called()

    @patch('User.chronelle_account.remote')
    def test_stale_refresh_is_never_submitted_to_rotation(self,remote):
        self.bound(expired=True);remote.return_value={'active':False}
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')
        remote.assert_called_once()
        self.assertTrue(remote.call_args.args[1].endswith('/introspect/'))

    @patch('User.chronelle_account.remote')
    def test_preflight_network_failure_preserves_usable_credentials(self,remote):
        row,value=self.bound(expired=True)
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        with self.assertRaises(BindingFailure):credentials(self.user,'https://chronelle.jyonn.space')
        self.assertFalse(unpack(authority(value)).get('renewal_uncertain',False))

    @patch('User.chronelle_account.remote')
    def test_rebinding_repairs_shared_authority_without_changing_other_accounts_policy(self,remote):
        from Chat.models import ResearchCalendarPreference
        row,value=self.bound(expired=True)
        store(ChronelleAccount.objects.create(user=self.other),value)
        shared=authority(value)
        store(shared,dict(value,renewal_uncertain=True))
        pref=ResearchCalendarPreference.objects.create(user=self.other,binding_subject=value['binding']['subject'],default_busy=False)
        self.call({'action':'authorize','confirmed':True})
        state=unpack(ChronelleAccount.objects.get(user=self.user))['pending']['state']
        remote.return_value={'authorization_type':'account_binding','subject':value['binding']['subject'],'grant_id':value['binding']['binding_id'],
            'grant_version':2,'grant_expires_at':(timezone.now()+timezone.timedelta(days=180)).isoformat(),
            'access_token':'rebound-access','refresh_token':'rebound-refresh','expires_in':900}
        self.assertEqual(self.call({'action':'claim','code':'coc_test','state':state}).json()['body']['status'],'active')
        self.assertEqual(credentials(self.other,'https://chronelle.jyonn.space')['access_token'],'rebound-access')
        pref.refresh_from_db();self.assertFalse(pref.default_busy)
        self.assertFalse(unpack(authority(value)).get('renewal_uncertain',False))

    @patch('User.chronelle_account.remote')
    def test_explicitly_hidden_member_calendar_and_nonmembers_denied(self,remote):
        from Chat.models import ResearchCalendarPreference
        self.bound();_,other=self.bound(self.other)
        ResearchCalendarPreference.objects.create(user=self.other,binding_subject=other['binding']['subject'],default_busy=False)
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
        display_remote.return_value={'active':True,'grant':{'id':value['binding']['binding_id'],'authorization_type':'account_binding','version':1,'expires_at':(timezone.now()+timezone.timedelta(days=180)).isoformat()}}
        response=self.call({'action':'display','members':[self.user.pk]},f'/research/{self.project.pk}/calendar/')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(response['Cache-Control'],'no-store, private')
        self.assertEqual(response.json()['body']['displays'][0]['url'],f'https://chronelle.jyonn.space/oauth/embed/{self.client_id}/')
        import jwt
        body=jwt.decode(response.json()['body']['displays'][0]['token'],'test-confidential-secret',algorithms=['HS256'],audience='chronelle-display-api')
        self.assertEqual(body['viewer_ref'],'frienden:'+str(self.user.pk))
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
        from Chat.models import ResearchCalendarPreference
        self.assertFalse(ResearchCalendarPreference.objects.filter(user=self.user).exists())
        self.assertEqual(self.project.calendar_policies,{str(self.user.pk):{'detail':[],'busy':[7]}})

    @patch('User.chronelle_account.remote')
    def test_global_busy_default_applies_before_review_and_to_new_groups(self,remote):
        from Chat.models import ResearchCalendarPreference
        from Chat.research_calendar import shared_policy
        self.bound();remote.return_value=[{'id':7,'name':'Private'}]
        path=f'/research/{self.project.pk}/calendar/'
        setup=self.call(path=path).json()['body']
        self.assertFalse(setup['policy_configured'])
        self.assertEqual(setup['policy'],{'detail':[],'busy':[7]})
        self.assertFalse(ResearchCalendarPreference.objects.filter(user=self.user).exists())
        self.assertEqual(shared_policy(self.user,self.project,[7]),{'detail':[],'busy':[7]})
        response=self.call({'action':'policy','default_busy':True,'detail':[],'busy':[7]},path)
        self.assertEqual(response.status_code,200,response.content)
        other_project=ResearchProject.objects.create(chat=Chat.objects.create(space=self.space,created_by=self.user,chat_type=1,purpose=2),start='2026-10-28')
        self.assertEqual(shared_policy(self.user,other_project,[7,8]),{'detail':[],'busy':[7,8]})
        self.assertEqual(self.call({'action':'policy','detail':[7],'busy':[]},path).status_code,400)
        self.assertEqual(self.call({'action':'policy','default_busy':True,'detail':[],'busy':[]},path).status_code,200)
        self.assertEqual(shared_policy(self.user,other_project,[7,8]),{'detail':[],'busy':[8]})
        row=ChronelleAccount.objects.get(user=self.user)
        value=unpack(row);value['binding']['subject']=str(uuid.uuid4());store(row,value)
        self.assertEqual(shared_policy(self.user,other_project,[7,8]),{'detail':[],'busy':[]})

    @patch('Chat.research_calendar.remote')
    @patch('User.chronelle_account.remote')
    def test_global_privacy_save_revokes_owned_displays_across_projects(self,groups,remote):
        self.bound();groups.return_value=[{'id':7,'name':'Private'}]
        second=ResearchProject.objects.create(chat=Chat.objects.create(space=self.space,created_by=self.user,chat_type=1,purpose=2),start='2026-10-28')
        ChatMember.objects.create(chat=second.chat,user=self.user,role=1,status=1)
        for project in [self.project,second]:
            project.calendar_displays=[{'protocol':'oauth-v1','id':str(uuid.uuid4()),'owner':self.user.pk,'viewer':self.other.pk,'until':timezone.now().timestamp()+300}]
            project.save()
        response=self.call({'action':'policy','default_busy':False,'detail':[],'busy':[]},f'/research/{self.project.pk}/calendar/')
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(remote.call_count,2)
        for project in [self.project,second]:
            project.refresh_from_db();self.assertEqual(project.calendar_displays,[])
