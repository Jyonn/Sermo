import uuid
from unittest.mock import patch
from User import tests_chronelle_account as fixtures
from User.chronelle_account import BindingFailure
from Chat.models import ResearchMeeting


class ResearchMeetingTests(fixtures.ChronelleAccountTests):
    def setUp(self):
        super().setUp()
        safety=patch('Chat.research_meetings.check_user_text');safety.start();self.addCleanup(safety.stop)

    def meeting(self):
        return {'command_id':str(uuid.uuid4()),'title':'Project review','description':'**Agenda**','location':'Room',
            'start':'2026-11-01T09:00:00+08:00','end':'2026-11-01T10:00:00+08:00','timezone':'Asia/Shanghai',
            'all_day':False,'members':[self.user.pk,self.other.pk],'group_id':7}

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
        repeat=self.call(body,path)
        self.assertEqual(repeat.json()['body']['invitation_id'],first.json()['body']['invitation_id'])
        self.assertEqual(remote.call_count,1)
        self.assertEqual(ResearchMeeting.objects.count(),1)
        self.assertEqual(self.call(dict(body,title='Changed'),path).status_code,409)

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
