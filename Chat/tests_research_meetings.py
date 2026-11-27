import uuid
from unittest.mock import patch
from User import tests_chronelle_account as fixtures
from User.chronelle_account import BindingFailure
from User.chronelle_account import store
from Chat.models import ResearchMeeting
from User.models import User


class ResearchMeetingTests(fixtures.ChronelleAccountTests):
    def setUp(self):
        super().setUp()
        safety=patch('Chat.research_meetings.check_user_text');safety.start();self.addCleanup(safety.stop)

    def meeting(self):
        return {'command_id':str(uuid.uuid4()),'title':'Project review','description':'**Agenda**','location':'Room',
            'start':'2026-11-01T09:00:00+08:00','end':'2026-11-01T10:00:00+08:00','timezone':'Asia/Shanghai',
            'all_day':False,'members':[self.user.pk,self.other.pk],'group_id':7}

    @patch('Chat.research_calendar.calendar', return_value=[{'id': 7, 'name': 'Research'}])
    def test_only_active_project_members_with_ready_bindings_are_invitable(self, calendar):
        self.bound()
        stranger = User.create(self.space, 'Non-member', verified=True)
        self.bound(stranger)
        path = f'/research/{self.project.pk}/calendar/'
        own_only = self.call(path=path).json()['body']
        self.assertEqual(set(own_only['invitable_member_ids']), {self.user.pk})
        self.assertIn(self.other.pk, {member['id'] for member in own_only['members']})
        self.bound(self.other)
        response = self.call(path=path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.json()['body']['invitable_member_ids']), {self.user.pk, self.other.pk})
        self.assertNotIn('cbr_test-secret', response.content.decode())

    @patch('Chat.research_meetings.remote')
    def test_unlinked_invitee_returns_editable_error_without_sending(self, remote):
        self.bound()
        result = self.call(self.meeting(), f'/research/{self.project.pk}/meetings/')
        self.assertEqual(result.status_code, 400, result.content)
        self.assertIn('invitation_recipient_unavailable', result.content.decode())
        remote.assert_not_called()
        self.assertFalse(ResearchMeeting.objects.exists())

    @patch('Chat.research_meetings.remote')
    def test_real_subject_mapping_and_idempotent_local_result(self,remote):
        self.bound();_,peer=self.bound(self.other)
        remote.return_value={'id':str(uuid.uuid4())}
        body=self.meeting();path=f'/research/{self.project.pk}/meetings/'
        first=self.call(body,path)
        self.assertEqual(first.status_code,200,first.content)
        self.assertEqual(first.json()['body']['status'],'invited')
        self.assertEqual(remote.call_args.kwargs['body']['subjects'],[peer['binding']['subject']])
        self.assertNotIn('subjects',first.content.decode())
        self.assertNotIn('_authority',first.content.decode())
        repeat=self.call(body,path)
        self.assertEqual(repeat.json()['body']['invitation_id'],first.json()['body']['invitation_id'])
        self.assertEqual(remote.call_count,1)
        self.assertEqual(ResearchMeeting.objects.count(),1)
        self.assertEqual(self.call(dict(body,title='Changed'),path).status_code,409)

    @patch('Chat.research_meetings.remote')
    def test_all_day_recurring_invitation_sends_calendar_dates(self, remote):
        self.bound();self.bound(self.other)
        remote.return_value={'id':str(uuid.uuid4())}
        recurrence={'frequency':'WEEKLY','weekdays':['MO'],'ending':'count','count':3}
        body=dict(self.meeting(),all_day=True,start='2026-11-01T16:00:00Z',end='2026-11-02T16:00:00Z',category_id=4,recurrence=recurrence)
        result=self.call(body,f'/research/{self.project.pk}/meetings/')
        self.assertEqual(result.status_code,200,result.content)
        sent=remote.call_args.kwargs['body']
        self.assertEqual((sent['start'],sent['end']),('2026-11-02','2026-11-03'))
        self.assertEqual(sent['recurrence'],recurrence)
        self.assertEqual(sent['category_id'],4)

    @patch('Chat.research_meetings.remote')
    def test_unknown_delivery_retries_same_command_not_new_meeting(self,remote):
        self.bound();self.bound(self.other)
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        body=self.meeting();path=f'/research/{self.project.pk}/meetings/'
        self.assertEqual(self.call(body,path).status_code,503)
        self.assertEqual(ResearchMeeting.objects.get(pk=body['command_id']).status,'unknown')
        self.assertEqual(self.call(self.meeting(),path).status_code,409)
        key=remote.call_args.kwargs['key']
        remote.side_effect=None;remote.return_value={'id':str(uuid.uuid4())}
        self.assertEqual(self.call(body,path).status_code,200)
        self.assertEqual(remote.call_args.kwargs['key'],key)
        self.assertEqual(ResearchMeeting.objects.count(),1)

    def test_membership_and_dates_cannot_be_forged(self):
        path=f'/research/{self.project.pk}/meetings/'
        self.assertEqual(self.call(dict(self.meeting(),members=[self.user.pk,999999]),path).status_code,403)
        self.assertEqual(self.call(dict(self.meeting(),end='2026-10-01T09:00:00+08:00'),path).status_code,400)
        self.assertEqual(self.call(dict(self.meeting(),all_day=True),path).status_code,400)
        self.assertFalse(ResearchMeeting.objects.exists())

    @patch('Chat.research_meetings.remote')
    def test_uncertain_meeting_cannot_be_retried_as_a_different_calendar_account(self,remote):
        self.bound();row,peer=self.bound(self.other)
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        body=self.meeting();path=f'/research/{self.project.pk}/meetings/'
        self.assertEqual(self.call(body,path).status_code,503)
        peer['binding']['subject']=str(uuid.uuid4());store(row,peer)
        remote.reset_mock()
        self.assertEqual(self.call(body,path).status_code,409)
        remote.assert_not_called()

    @patch('Chat.research_meetings.remote')
    def test_old_protocol_unknown_delivery_is_not_resent_through_new_oauth_client(self,remote):
        self.bound();self.bound(self.other)
        remote.side_effect=BindingFailure('chronelle_unavailable',503)
        body=self.meeting();path=f'/research/{self.project.pk}/meetings/'
        self.assertEqual(self.call(body,path).status_code,503)
        row=ResearchMeeting.objects.get(pk=body['command_id'])
        row.payload.pop('_protocol');row.payload.pop('_oauth_client');row.save(update_fields=['payload'])
        remote.reset_mock()
        self.assertEqual(self.call(body,path).status_code,409)
        remote.assert_not_called()
