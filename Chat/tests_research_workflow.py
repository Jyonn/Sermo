from datetime import date
from unittest.mock import patch
from django.test import TestCase
from Chat.tests_research import ResearchTests
from Chat.models import ResearchProject, ResearchEvidence
from Message.models import MediaAsset, MediaResource


class WorkflowTests(TestCase):
    setUp = ResearchTests.setUp
    create = ResearchTests.create
    call = ResearchTests.call
    member = ResearchTests.member
    update = ResearchTests.update

    def project(self):
        p = self.create()
        self.member(p, self.student)
        self.member(p, self.other)
        return self.update(p, dict(action='task', title='Research task', body='Evidence', start='2026-10-01', assignees=[self.student.pk], reporting={k:dict(amount=2, reward_type='silver') for k in ('daily', 'weekly', 'monthly')})).json()['body']

    def report(self, p, kind='daily', **fields):
        return self.update(p, dict(action='progress', task_id=p['tasks'][0]['id'], body='Report', report_kind=kind, **fields), self.student)

    @patch('Chat.research_workflow.today', return_value=date(2026, 11, 5))
    def test_required_reports_rewards_duplicate_and_waiver(self, _):
        p = self.project()
        snapshot = self.call(self.student, id=p['id']).json()['body']
        self.assertEqual({r['kind'] for r in snapshot['workflow']['todos']}, {'daily', 'weekly', 'monthly'})
        p = self.report(p, ai_slots=3, thinking_slots=5).json()['body']
        self.assertEqual(p['tasks'][0]['progress'][0]['thinking_slots'], 5)
        self.assertEqual(len(p['workflow']['todos']), 2)
        self.assertEqual(p['workflow']['ledger'][0]['amount'], 2)
        self.assertEqual(self.report(p).status_code, 400)
        self.assertEqual(len(ResearchProject.objects.get(pk=p['id']).workflow['ledger']), 1)
        p = self.report(p, kind='weekly', waive_reward=True).json()['body']
        self.assertEqual(len(p['workflow']['ledger']), 1)
        self.assertTrue(p['tasks'][0]['progress'][0]['waive_reward'])

    @patch('Chat.research_workflow.today', return_value=date(2026, 11, 5))
    def test_report_permissions_time_and_period_rollover(self, day):
        p = self.project()
        for fields in [dict(ai_slots=1.5), dict(ai_slots=-1), dict(ai_slots=30, thinking_slots=30), dict(waive_reward='false')]:
            self.assertEqual(self.report(p, **fields).status_code, 400)
        body=dict(action='progress',task_id=p['tasks'][0]['id'],body='Report',report_kind='daily')
        self.assertEqual(self.update(p,body,self.other).status_code,400)
        self.assertEqual(self.update(p,body).status_code,400)
        p = self.report(p).json()['body']
        day.return_value = date(2026,11,6)
        self.assertIn('daily', {r['kind'] for r in self.call(self.student,id=p['id']).json()['body']['workflow']['todos']})
        p = self.report(p).json()['body']
        self.assertEqual(len(p['workflow']['ledger']), 2)

    def test_flag_targets_resolution_and_owner_only_configuration(self):
        p=self.project(); task_id=p['tasks'][0]['id']
        p=self.update(p,dict(action='progress',task_id=task_id,body='Question',response_to=self.owner.pk),self.student).json()['body']
        question=p['tasks'][0]['progress'][0]['id']
        self.assertEqual(self.call(self.owner,id=p['id']).json()['body']['workflow']['todos'][0]['id'],question)
        self.assertEqual(self.update(p,dict(action='resolve_request',id=question),self.other).status_code,400)
        p=self.update(p,dict(action='progress',task_id=task_id,body='Answer',reply_to=question)).json()['body']
        self.assertEqual(self.call(self.owner,id=p['id']).json()['body']['workflow']['todos'],[])
        self.assertEqual(self.update(p,dict(action='award',user_id=self.student.pk,reward_type='gold',amount=2,reason='Review'),self.student).status_code,403)
        p=self.update(p,dict(action='award',user_id=self.student.pk,reward_type='gold',amount=2,reason='Helpful comment')).json()['body']
        self.assertEqual(p['workflow']['ledger'][0]['reward_type'],'gold')
        self.assertEqual(self.call(self.other,id=p['id']).json()['body']['workflow']['ledger'],[])

    def test_task_edit_and_milestone_bounds(self):
        p=self.project();task=p['tasks'][0]
        body=dict(action='task_edit',task_id=task['id'],title='Revised',body='Revised instructions',start='2026-11-01',end='2026-12-01',assignees=[self.other.pk],reporting={})
        self.assertEqual(self.update(p,body,self.student).status_code,403)
        p=self.update(p,body).json()['body']
        self.assertEqual(p['tasks'][0]['title'],'Revised')
        self.assertEqual(p['tasks'][0]['assignees'],[self.other.pk])
        self.assertEqual(self.update(p,dict(action='milestone',date='2020-01-01',title='No')).status_code,400)
        p=self.update(p,dict(action='milestone',date='2026-12-01',title='Paper deadline')).json()['body']
        self.assertEqual(len(p['workflow']['milestones']),1)
        self.assertEqual(self.update(p,dict(action='milestone',date='2026-12-01',title='No'),self.student).status_code,403)
        p=self.update(p,dict(action='settings',title='New title')).json()['body']
        self.assertEqual(p['title'],'New title')

    def test_period_boundaries_and_invalid_reward_rules(self):
        from Chat.research_workflow import period_start
        self.assertEqual(period_start('weekly',date(2026,11,1)),date(2026,10,26))
        self.assertEqual(period_start('monthly',date(2026,11,30)),date(2026,11,1))
        p=self.project();task=p['tasks'][0]
        body=dict(action='task_edit',task_id=task['id'],title=task['title'],body=task['body'],start=task['start'],assignees=task['assignees'])
        for reporting in ({'yearly':{}},{'daily':{'amount':-1}},{'daily':{'amount':1.5}},{'daily':{'amount':1,'reward_type':'missing'}}):
            self.assertEqual(self.update(p,dict(body,reporting=reporting)).status_code,400)

    def test_comment_bonus_edit_audit_and_student_source_visibility(self):
        p=self.project();task_id=p['tasks'][0]['id']
        p=self.update(p,dict(action='progress',task_id=task_id,body='Useful observation'),self.student).json()['body']
        source=p['tasks'][0]['progress'][0]['id']
        p=self.update(p,dict(action='award',user_id=self.student.pk,source_id=source,reward_type='silver',amount=2,reason='Good evidence')).json()['body']
        award=p['workflow']['ledger'][0]
        edit=dict(action='award_edit',id=award['id'],reward_type='gold',amount=3,reason='Revised award')
        self.assertEqual(self.update(p,edit,self.student).status_code,403)
        self.assertEqual(self.update(p,dict(edit,amount=-1)).status_code,400)
        p=self.update(p,edit).json()['body']
        self.assertEqual(len(p['workflow']['ledger']),1)
        self.assertEqual(p['workflow']['ledger'][0]['amount'],3)
        self.assertEqual(len(ResearchProject.objects.get(pk=p['id']).workflow['ledger'][0]['history']),1)
        student=self.call(self.student,id=p['id']).json()['body']['workflow']
        self.assertEqual(student['ledger'][0]['source_id'],source)
        self.assertEqual(student['ledger'][0]['task_id'],task_id)
        self.assertFalse(student['ledger'][0]['editable'])
        other=self.call(self.other,id=p['id']).json()['body']['workflow']
        self.assertEqual(other['ledger'],[])
        self.assertEqual(other['comment_awards'][0]['amount'],3)
        self.assertNotIn('history',other['comment_awards'][0])

    @patch('Chat.research_workflow.today', return_value=date(2026,11,5))
    def test_report_rewards_cannot_be_manually_edited(self,_):
        p=self.project();p=self.report(p).json()['body']
        award=p['workflow']['ledger'][0]
        self.assertEqual(self.update(p,dict(action='award_edit',id=award['id'],reward_type='silver',amount=9,reason='No')).status_code,400)

    def evidence(self, owner, key):
        asset=MediaAsset.objects.create(source_key='research-test/'+key,kind=MediaAsset.KIND_FILE,source_uri='https://example.com/'+key,file_size=500,status=MediaAsset.STATUS_READY)
        return MediaResource.acquire(owner,asset,MediaAsset.KIND_FILE,key)

    def test_reimbursement_required_documents_private_access_and_retention(self):
        p=self.project()
        payment=self.evidence(self.student,'payment.pdf');invoice=self.evidence(self.student,'invoice.pdf')
        body=dict(action='reimbursement',title='Conference',amount='100.25',evidence=[payment.pk,invoice.pk])
        self.assertEqual(self.update(p,dict(body,evidence=[payment.pk]),self.student).status_code,400)
        self.assertEqual(self.update(p,body,self.other).status_code,400)
        p=self.update(p,body,self.student).json()['body']
        row=p['workflow']['reimbursements'][0]
        self.assertEqual(self.call(self.other,id=p['id']).json()['body']['workflow']['reimbursements'],[])
        self.assertEqual(ResearchEvidence.objects.filter(project_id=p['id']).count(),2)
        self.assertEqual(payment.recalculate_reference_count(),1)
        open_body=dict(action='reimbursement_open',id=row['id'],resource_id=payment.pk)
        self.assertEqual(self.update(p,open_body,self.other).status_code,400)
        with patch('utils.qiniu.sign_private_download_url',return_value='https://signed.example/document'):
            self.assertEqual(self.update(p,open_body,self.student).json()['body']['url'],'https://signed.example/document')
        self.assertEqual(self.update(p,dict(action='reimbursement_review',id=row['id'],status='reviewed'),self.student).status_code,403)
        p=self.update(p,dict(action='reimbursement_review',id=row['id'],status='needs_info',notes='Please explain the amount')).json()['body']
        self.assertTrue(any(item['kind']=='reimbursement' for item in self.call(self.student,id=p['id']).json()['body']['workflow']['todos']))
        self.assertEqual(self.update(p,dict(action='reimbursement_resubmit',id=row['id'],notes='No access'),self.other).status_code,400)
        p=self.update(p,dict(action='reimbursement_resubmit',id=row['id'],notes='Includes registration fee'),self.student).json()['body']
        self.assertEqual(p['workflow']['reimbursements'][0]['status'],'submitted')
        p=self.update(p,dict(action='reimbursement_review',id=row['id'],status='reviewed',notes='Checked')).json()['body']
        self.assertEqual(p['workflow']['reimbursements'][0]['status'],'reviewed')
        p=self.update(p,dict(action='settings',status='archived')).json()['body']
        with patch('utils.qiniu.sign_private_download_url',return_value='https://signed.example/document'):
            self.assertEqual(self.update(p,open_body,self.student).status_code,200)
