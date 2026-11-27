from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from User import tests_chronelle_account as fixtures
from Chat.calendar_policy import defaults, initialize, projection, review_required
from Chat.models import ResearchCalendarPreference
from Friendship.models import Friendship, FriendshipStatusChoice
from User.chronelle_account import BindingFailure
from User.models import User
from utils import auth


class ChronellePermissionsTests(TestCase):
    setUp = fixtures.ChronelleAccountTests.setUp
    bound = fixtures.ChronelleAccountTests.bound
    call = fixtures.ChronelleAccountTests.call
    path = '/users/me/chronelle/permissions'

    def permissions(self, policy=None):
        return self.call(None if policy is None else {'audiences': policy}, self.path)

    def friend_settings(self, policy=None, friend=None):
        return self.call(None if policy is None else {'policy': policy},
                         f'{self.path}/friends/{(friend or self.other).pk}')

    @patch('User.chronelle_permissions.calendar', return_value=[{'id': 7, 'name': 'Work'}, {'id': 8, 'name': 'Home'}])
    def test_individual_settings_are_directional_inherited_and_resettable(self, _):
        _, binding = self.bound()
        another = User.create(self.space, 'Another friend', verified=True)
        for friend in (self.other, another):
            Friendship.objects.create(space=self.space, user_low=self.user, user_high=friend, status=FriendshipStatusChoice.ACCEPTED)
        audiences = defaults()
        audiences['friends']['overrides']['8'] = 'detail'
        self.assertEqual(self.permissions(audiences).status_code, 200)
        self.assertEqual(self.friend_settings().json()['body']['policy'], {'default': None, 'overrides': {}})
        option = {'default': 'hidden', 'overrides': {'7': 'detail'}}
        result = self.friend_settings(option)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(set(result['Cache-Control'].split(', ')), {'private', 'no-store'})
        self.assertEqual(projection(self.user, self.other, [7, 8, 9]), {'detail': [7], 'busy': []})
        self.assertEqual(projection(self.user, another, [7, 8, 9]), {'detail': [8], 'busy': [7, 9]})
        self.assertEqual(projection(self.user, self.user, [7, 8, 9], preview_tier='friends'), {'detail': [8], 'busy': [7, 9]})
        self.assertEqual(projection(self.user, self.other, [7], expected_subject='other-binding'), {'detail': [], 'busy': []})

        self.permissions(defaults('detail'))
        initialize(self.user, binding['binding']['subject'])
        snapshot = self.call(path=f'{self.path}/friends')
        self.assertEqual(set(snapshot['Cache-Control'].split(', ')), {'private', 'no-store'})
        rows = snapshot.json()['body']['friends']
        self.assertEqual([(row['friend']['user_id'], row['policy']) for row in rows], [(self.other.pk, option)])
        original_headers = self.headers.copy()
        self.headers['HTTP_AUTHORIZATION'] = 'Bearer ' + auth.get_login_token(self.other)['auth']
        self.assertEqual(self.call(path=f'{self.path}/friends').json()['body']['friends'], [])
        self.headers = original_headers
        self.assertEqual(self.friend_settings({'default': None, 'overrides': {}}).status_code, 200)
        self.assertEqual(self.call(path=f'{self.path}/friends').json()['body']['friends'], [])
        self.assertEqual(projection(self.user, self.other, [7, 8]), {'detail': [7, 8], 'busy': []})

    @patch('User.chronelle_permissions.calendar', return_value=[{'id': 7, 'name': 'Work'}])
    def test_friend_endpoint_rejects_nonfriends_deleted_friends_and_unknown_groups(self, _):
        self.bound()
        policy = {'default': 'detail', 'overrides': {}}
        self.assertEqual(self.friend_settings(policy).status_code, 404)
        self.assertEqual(self.friend_settings(friend=self.user).status_code, 404)
        row = Friendship.objects.create(space=self.space, user_low=self.user, user_high=self.other, status=FriendshipStatusChoice.PENDING)
        self.assertEqual(self.friend_settings(policy).status_code, 404)
        row.status = FriendshipStatusChoice.ACCEPTED
        row.save(update_fields=['status'])
        for invalid in [{'default': True, 'overrides': {}}, {'default': None, 'overrides': {'200': 'detail'}}]:
            self.assertEqual(self.friend_settings(invalid).status_code, 400)
        self.assertEqual(self.friend_settings(policy).status_code, 200)
        self.headers['HTTP_ORIGIN'] = 'https://evil.example'
        self.assertEqual(self.friend_settings(policy).status_code, 403)
        self.headers['HTTP_ORIGIN'] = 'https://frienden.jyonn.space'
        row.remove(self.user)
        self.assertEqual(self.call(path=f'{self.path}/friends').json()['body']['friends'], [])
        row.status = FriendshipStatusChoice.ACCEPTED
        row.save(update_fields=['status'])
        self.assertEqual(self.friend_settings().json()['body']['policy'], {'default': None, 'overrides': {}})

    @patch('User.chronelle_permissions.calendar', return_value=[{'id': 7, 'name': 'Work'}])
    @patch('Chat.research_calendar.remote')
    def test_reducing_privacy_only_revokes_when_target_has_live_calendar_access(self, remote, _):
        self.bound()
        another = User.create(self.space, 'Another friend', verified=True)
        Friendship.objects.create(space=self.space, user_low=self.user, user_high=self.other, status=FriendshipStatusChoice.ACCEPTED)
        until = timezone.now().timestamp() + 300
        self.project.calendar_displays = [
            {'owner': self.user.pk, 'viewer': another.pk, 'until': until, 'protocol': 'oauth-v1'},
        ]
        self.project.save(update_fields=['calendar_displays'])
        hidden = {'default': 'hidden', 'overrides': {}}
        self.assertFalse(self.friend_settings(hidden).json()['body']['rebind_required'])
        remote.assert_not_called()
        self.friend_settings({'default': 'detail', 'overrides': {}})
        self.project.calendar_displays.append(
            {'owner': self.user.pk, 'viewer': self.other.pk, 'until': until, 'protocol': 'oauth-v1',
             'base': 'https://chronelle.6-79.cn'})
        self.project.save(update_fields=['calendar_displays'])
        remote.side_effect = BindingFailure('chronelle_unavailable', 503)
        self.assertEqual(self.friend_settings(hidden).status_code, 503)
        self.assertEqual(self.friend_settings().json()['body']['policy']['default'], 'detail')
        remote.side_effect = None
        self.assertTrue(self.friend_settings(hidden).json()['body']['rebind_required'])
        self.assertEqual(remote.call_args.args[0], 'https://chronelle.6-79.cn')
        self.project.refresh_from_db()
        self.assertEqual([item['viewer'] for item in self.project.calendar_displays], [another.pk])

    def test_busy_default_for_friends_and_strangers_including_new_groups(self):
        _, value = self.bound()
        initialize(self.user, value['binding']['subject'])
        self.assertTrue(review_required(self.user, value['binding']['subject']))
        self.assertEqual(projection(self.user, self.other, [7, 8]), {'detail': [], 'busy': [7, 8]})
        Friendship.objects.create(space=self.space,user_low=self.user,user_high=self.other,status=1)
        self.assertEqual(projection(self.user, self.other, [7, 8]), {'detail': [], 'busy': [7, 8]})

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    def test_global_api_works_without_a_project_or_research_enabled(self, calendar):
        self.bound();self.space.research_enabled=False;self.space.save()
        body=self.permissions().json()['body']
        self.assertEqual(body['audiences'], defaults())
        self.assertEqual(body['groups'], [{'id':7,'name':'Private'}])
        self.assertEqual(self.permissions(defaults()).status_code,200)
        self.assertFalse(self.permissions().json()['body']['review_required'])

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    def test_detail_defaults_and_legacy_saves_preserve_disabled_fields(self, calendar):
        _, value = self.bound()
        self.assertEqual(self.permissions().json()['body']['detail_fields'], {'location': True, 'description': True})
        for invalid in [None, {'location': False}, {'location': 'false', 'description': True}]:
            self.assertEqual(self.call({'audiences': defaults(), 'detail_fields': invalid}, self.path).status_code, 400)
        fields = {'location': False, 'description': True}
        self.assertEqual(self.call({'audiences': defaults(), 'detail_fields': fields}, self.path).status_code, 200)
        self.assertEqual(self.permissions(defaults('detail')).status_code, 200)
        initialize(self.user, value['binding']['subject'])
        self.assertEqual(self.permissions().json()['body']['detail_fields'], fields)

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    @patch('Chat.research_calendar.remote')
    def test_hiding_details_revokes_previous_browser_grants(self, remote, calendar):
        self.bound()
        self.project.calendar_displays = [{'protocol': 'oauth-v1', 'id': 'old', 'owner': self.user.pk,
            'viewer': self.other.pk, 'until': timezone.now().timestamp() + 300}]
        self.project.save()
        result = self.call({'audiences': defaults(), 'detail_fields': {'location': False, 'description': True}}, self.path)
        self.assertTrue(result.json()['body']['rebind_required'])
        remote.assert_called_once()

    @patch('User.chronelle_account.remote', return_value=[{'id':7,'name':'Private'}])
    @patch('Chat.research_calendar.remote')
    def test_research_reads_filtered_agenda_without_a_browser_token(self, remote, groups):
        self.bound()
        _, owner = self.bound(self.other)
        ResearchCalendarPreference.objects.create(user=self.other, binding_subject=owner['binding']['subject'],
            overrides={'version': 2, 'audiences': defaults('detail'), 'detail_fields': {'location': True, 'description': False}})
        path = f'/research/{self.project.pk}/calendar/'
        result = self.call({'action': 'display', 'members': [self.other.pk]}, path).json()['body']
        self.assertEqual(result, {'displays': [], 'filtered': True})
        remote.assert_not_called()
        remote.side_effect = [
            {'active': True, 'grant': {'id': owner['binding']['binding_id'], 'authorization_type': 'account_binding',
                'version': 1, 'expires_at': (timezone.now() + timezone.timedelta(days=180)).isoformat()}},
            {'events': [{'start': '2026-11-01T12:00:00Z', 'end': '2026-11-01T13:00:00Z', 'visibility': 'details',
                'title': 'Meeting', 'location': 'Room', 'description': 'Private note'}], 'availability': 'known'},
        ]
        response = self.call({'action': 'agenda', 'members': [self.other.pk],
            'start': '2026-11-01T00:00:00Z', 'end': '2026-11-02T00:00:00Z'}, path)
        self.assertEqual(response.status_code, 200)
        event = response.json()['body']['events'][0]
        self.assertEqual((event['title'], event['location'], event['description']), ('Meeting', 'Room', ''))
        self.assertNotIn('token', response.json()['body'])
        self.assertNotIn('Private note', response.content.decode())

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'},{'id':8,'name':'Work'}])
    def test_actual_friendship_selects_tier_and_forged_tier_is_ignored(self, calendar):
        self.bound()
        policy=defaults();policy['friends']['overrides']['7']='detail';policy['strangers']['overrides']['8']='hidden'
        self.assertEqual(self.permissions(policy).status_code,200)
        self.assertEqual(projection(self.user,self.other,[7,8]),{'detail':[],'busy':[7]})
        Friendship.objects.create(space=self.space,user_low=self.user,user_high=self.other,status=1)
        self.assertEqual(projection(self.user,self.other,[7,8]),{'detail':[7],'busy':[8]})
        self.assertEqual(projection(self.user,None,[7,8]),{'detail':[],'busy':[7]})

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    def test_api_rejects_lower_friend_access_foreign_groups_and_invalid_modes(self, calendar):
        self.bound()
        for mutate in [lambda p:p['friends'].update(default='hidden'),lambda p:p['friends']['overrides'].update({'7':'hidden'}),lambda p:p['strangers']['overrides'].update({'99':'detail'}),lambda p:p['friends'].update(default=[]),lambda p:p['friends']['overrides'].update({'7':{}})]:
            policy=defaults();mutate(policy)
            self.assertEqual(self.permissions(policy).status_code,400)
        self.headers['HTTP_ORIGIN']='https://evil.example'
        self.assertEqual(self.permissions(defaults()).status_code,403)

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    def test_legacy_policy_is_preserved_and_deleted_groups_are_pruned(self, calendar):
        _,value=self.bound()
        ResearchCalendarPreference.objects.create(user=self.user,binding_subject=value['binding']['subject'],default_busy=False,overrides={'7':'detail','99':'busy'})
        body=self.permissions().json()['body']
        for policy in body['audiences'].values():
            self.assertEqual(policy,{'default':'hidden','overrides':{'7':'detail'}})
        self.assertEqual(self.permissions(body['audiences']).status_code,200)
        self.assertEqual(self.call({'action':'policy','default_busy':True,'detail':[],'busy':[7]},f'/research/{self.project.pk}/calendar/').status_code,400)

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    @patch('Chat.research_calendar.remote')
    def test_reduction_revokes_shared_displays_but_self_only_display_does_not_revoke(self, remote, calendar):
        self.bound()
        self.project.calendar_displays=[{'protocol':'oauth-v1','id':'self','owner':self.user.pk,'viewer':self.user.pk,'until':timezone.now().timestamp()+300}];self.project.save()
        self.assertFalse(self.permissions(defaults('hidden')).json()['body']['rebind_required']);remote.assert_not_called()
        self.permissions(defaults())
        self.project.calendar_displays.append({'protocol':'oauth-v1','id':'shared','owner':self.user.pk,'viewer':self.other.pk,'until':timezone.now().timestamp()+300});self.project.save()
        self.assertTrue(self.permissions(defaults('hidden')).json()['body']['rebind_required']);remote.assert_called_once()
        self.project.refresh_from_db();self.assertEqual([d['id'] for d in self.project.calendar_displays],['self'])

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    @patch('Chat.research_calendar.remote', side_effect=BindingFailure('chronelle_unavailable'))
    def test_failed_revocation_does_not_report_saved_or_reduce_policy(self, remote, calendar):
        self.bound();self.permissions(defaults())
        self.project.calendar_displays=[{'protocol':'oauth-v1','id':'shared','owner':self.user.pk,'viewer':self.other.pk,'until':timezone.now().timestamp()+300}];self.project.save()
        self.assertNotEqual(self.permissions(defaults('hidden')).status_code,200)
        self.assertEqual(self.permissions().json()['body']['audiences'],defaults())

    @patch('User.chronelle_permissions.calendar', return_value=[{'id':7,'name':'Private'}])
    @patch('Chat.research_calendar.remote')
    def test_friend_removal_revokes_stronger_outstanding_friend_display(self, remote, calendar):
        self.bound();policy=defaults();policy['friends']['default']='detail';self.permissions(policy)
        friend=Friendship.objects.create(space=self.space,user_low=self.user,user_high=self.other,status=1)
        self.project.calendar_displays=[{'protocol':'oauth-v1','id':'friend','owner':self.user.pk,'viewer':self.other.pk,'until':timezone.now().timestamp()+300,'base':'https://chronelle.jyonn.space'}];self.project.save()
        friend.remove(self.user);remote.assert_called_once()
        self.assertEqual(Friendship.objects.get(pk=friend.pk).status,FriendshipStatusChoice.DELETED)
        self.assertEqual(projection(self.user,self.other,[7]),{'detail':[],'busy':[7]})

    def test_rebind_same_subject_preserves_restrictions_but_new_subject_does_not_copy_group_details(self):
        _,value=self.bound();subject=value['binding']['subject']
        ResearchCalendarPreference.objects.create(user=self.user,binding_subject=subject,default_busy=False,overrides={'7':'detail'})
        initialize(self.user,subject)
        self.assertEqual(ResearchCalendarPreference.objects.get(user=self.user).overrides['audiences']['strangers']['default'],'hidden')
        initialize(self.user,'new-subject')
        self.assertEqual(ResearchCalendarPreference.objects.get(user=self.user).overrides['audiences'],defaults())

    @patch('User.chronelle_account.remote', return_value=[{'id':7,'name':'Private'}])
    @patch('Chat.research_calendar.remote')
    def test_signed_peer_display_uses_server_relationship_not_a_payload_tier(self, remote, groups):
        import jwt
        _,viewer=self.bound();_,owner=self.bound(self.other)
        initialize(self.other,owner['binding']['subject'])
        pref=ResearchCalendarPreference.objects.get(user=self.other)
        pref.overrides['audiences']['friends']['default']='detail';pref.save()
        def introspect(base,path,**kwargs):
            binding=viewer['binding'] if kwargs['body']['subject']==viewer['binding']['subject'] else owner['binding']
            return {'active':True,'grant':{'id':binding['binding_id'],'authorization_type':'account_binding','version':1,'expires_at':(timezone.now()+timezone.timedelta(days=180)).isoformat()}}
        remote.side_effect=introspect
        path=f'/research/{self.project.pk}/calendar/'
        response=self.call({'action':'display','members':[self.other.pk],'tier':'friends'},path)
        token=response.json()['body']['displays'][0]['token']
        claims=jwt.decode(token,options={'verify_signature':False})
        self.assertEqual(claims['detail_group_ids'],[]);self.assertEqual(claims['busy_group_ids'],[7])
        Friendship.objects.create(space=self.space,user_low=self.user,user_high=self.other,status=1)
        response=self.call({'action':'display','members':[self.other.pk]},path)
        claims=jwt.decode(response.json()['body']['displays'][0]['token'],options={'verify_signature':False})
        self.assertEqual(claims['detail_group_ids'],[7]);self.assertEqual(claims['busy_group_ids'],[])
        pref.overrides['friend_overrides'] = {str(self.user.pk): {'default': 'hidden', 'overrides': {}}}
        pref.save(update_fields=['overrides'])
        response = self.call({'action': 'display', 'members': [self.other.pk]}, path)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('displays', response.json().get('body') or {})
