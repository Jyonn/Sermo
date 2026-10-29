from unittest.mock import patch
from urllib.parse import urlencode
import jwt
from django.test import TestCase
from django.utils import timezone
from User import tests_chronelle_account as fixtures
from User.chronelle_account import BindingFailure
from Chat.calendar_policy import defaults
from Chat.models import ResearchCalendarPreference
from Friendship.models import Friendship
from utils import auth


class ChronelleProfileTests(TestCase):
    setUp = fixtures.ChronelleAccountTests.setUp
    bound = fixtures.ChronelleAccountTests.bound

    def profile(self, viewer=None, **params):
        query={'start':'2026-10-29T00:00:00+00:00','end':'2026-11-26T00:00:00+00:00',**params}
        return self.client.get(f'/users/{self.user.pk}/chronelle/calendar?'+urlencode(query),
            HTTP_AUTHORIZATION='Bearer '+auth.get_login_token(viewer or self.user)['auth'],HTTP_ORIGIN='https://frienden.jyonn.space')

    def provider(self, value, events=None, known=True):
        def response(base,path,**kwargs):
            if path.endswith('/introspect/'):
                return {'active':True,'grant':{'id':value['binding']['binding_id'],'version':1,'authorization_type':'account_binding',
                    'expires_at':(timezone.now()+timezone.timedelta(days=180)).isoformat()}}
            self.assertEqual(path,'/api/oauth/display/events/')
            self.claims=jwt.decode(kwargs['token'],options={'verify_signature':False})
            return {'events':events or [],'availability':'known' if known else 'unknown'}
        return response

    @patch('User.chronelle_profile.calendar',return_value=[{'id':7,'name':'Private'},{'id':8,'name':'Work'}])
    @patch('User.chronelle_profile.remote')
    def test_oauth_binding_without_legacy_share_id_restores_own_calendar(self,remote,calendar):
        _,value=self.bound();self.assertEqual(self.user.chronelle_calendar_id,'')
        remote.side_effect=self.provider(value,[{'start':'2026-10-29T10:00:00Z','end':'2026-10-29T11:00:00Z','visibility':'details','title':'Research','location':'Lab'}])
        response=self.profile()
        self.assertEqual(response['Cache-Control'],'private, no-store')
        self.assertEqual(response.json()['identifier'],'OK')
        body=response.json()['body']
        self.assertEqual(body['status'],'active');self.assertEqual(body['events'][0]['title'],'Research')
        self.assertEqual(set(self.claims['detail_group_ids']),{7,8})
        self.assertNotIn('token',body);self.assertNotIn('subject',body)

    @patch('User.chronelle_profile.calendar',return_value=[{'id':7,'name':'Private'}])
    @patch('User.chronelle_profile.remote')
    def test_unbound_viewer_gets_busy_only_without_title_or_location(self,remote,calendar):
        _,value=self.bound()
        remote.side_effect=self.provider(value,[{'start':'2026-10-29T10:00:00Z','end':'2026-10-29T11:00:00Z','visibility':'busy','title':'Secret','location':'Secret lab'}])
        body=self.profile(self.other).json()['body']
        self.assertTrue(body['events'][0]['locked']);self.assertEqual(body['events'][0]['title'],'');self.assertEqual(body['events'][0]['location'],'')
        self.assertEqual(self.claims['detail_group_ids'],[]);self.assertEqual(self.claims['busy_group_ids'],[7])

    @patch('User.chronelle_profile.calendar',return_value=[{'id':7,'name':'Private'},{'id':8,'name':'Work'}])
    @patch('User.chronelle_profile.remote')
    def test_actual_friendship_and_self_preview_use_saved_tiers(self,remote,calendar):
        _,value=self.bound();policy=defaults();policy['friends']['overrides']['7']='detail';policy['strangers']['overrides']['8']='hidden'
        ResearchCalendarPreference.objects.create(user=self.user,binding_subject=value['binding']['subject'],overrides={'version':2,'audiences':policy})
        remote.side_effect=self.provider(value,known=False)
        self.profile(self.other);self.assertEqual(self.claims['busy_group_ids'],[7]);self.assertEqual(self.claims['detail_group_ids'],[])
        Friendship.objects.create(space=self.space,user_low=self.user,user_high=self.other,status=1)
        self.profile(self.other);self.assertEqual(self.claims['detail_group_ids'],[7]);self.assertEqual(self.claims['busy_group_ids'],[8])
        self.assertEqual(self.profile(self.other,audience='friend').status_code,403)
        self.profile(audience='stranger');self.assertEqual(self.claims['detail_group_ids'],[]);self.assertEqual(self.claims['busy_group_ids'],[7])
        self.assertEqual(self.profile(audience='friend').json()['body']['availability'],'unknown')

    @patch('User.chronelle_profile.remote')
    def test_unbound_and_hidden_profiles_do_not_issue_display_credentials(self,remote):
        self.assertEqual(self.profile().json()['body']['status'],'hidden');remote.assert_not_called()
        _,value=self.bound()
        ResearchCalendarPreference.objects.create(user=self.user,binding_subject=value['binding']['subject'],default_busy=False)
        with patch('User.chronelle_profile.calendar',return_value=[{'id':7,'name':'Private'}]):
            self.assertEqual(self.profile(self.other).json()['body']['status'],'hidden')
        remote.assert_not_called()

    def test_date_bounds_and_space_isolation(self):
        from Space.models import Space
        from User.models import User
        self.assertEqual(self.profile(end='2027-01-01T00:00:00Z').status_code,400)
        self.assertEqual(self.profile(start='2026-10-29').status_code,400)
        space=Space.objects.create(slug='other-profile',name='Other',email='other@example.com')
        outsider=User.create(space,'Outsider',verified=True)
        self.assertEqual(self.profile(outsider).status_code,404)

    @patch('User.chronelle_profile.calendar',side_effect=BindingFailure('chronelle_unavailable',503))
    def test_upstream_failure_is_not_reported_as_an_empty_free_calendar(self,calendar):
        self.bound();self.assertEqual(self.profile().status_code,503)

    @patch('User.chronelle_profile.calendar',return_value=[{'id':7,'name':'Private'}])
    @patch('User.chronelle_profile.remote')
    def test_malformed_event_dates_are_not_reported_as_free(self,remote,calendar):
        _,value=self.bound()
        remote.side_effect=self.provider(value,[{'start':'invalid','end':'invalid','visibility':'busy'}])
        self.assertEqual(self.profile().status_code,503)
