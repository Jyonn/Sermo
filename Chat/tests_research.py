import json
from unittest.mock import patch
from django.test import TestCase
from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ResearchProject
from Space.models import Space
from User.models import User
from utils import auth


class ResearchTests(TestCase):
    def setUp(self):
        self.space=Space.objects.create(slug='research', name='Research', email='admin@example.com', research_enabled=True)
        self.owner=User.create(self.space,'Owner',verified=True)
        self.student=User.create(self.space,'Student',verified=True)
        self.other=User.create(self.space,'Other',verified=True)
        self.capability=patch.object(User,'require_capability').start()
        patch('Chat.research.check_user_text').start()
        self.addCleanup(patch.stopall)

    def call(self,user,body=None,id=None):
        path='/research/'+(str(id)+'/' if id else '')
        headers={'HTTP_AUTHORIZATION':'Bearer '+auth.get_login_token(user)['auth']}
        return self.client.get(path,**headers) if body is None else self.client.post(path,json.dumps(body),content_type='application/json',**headers)

    def create(self):
        response=self.call(self.owner,dict(title='Real project',description='Research',start='2026-10-27',end=None))
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['body']

    def member(self,p,user):
        ChatMember.objects.create(chat_id=p['chat_id'],user=user,role=ChatMemberRoleChoice.MEMBER,status=ChatMemberStatusChoice.ACTIVE,invited_by=self.owner)

    def update(self,p,body,user=None):
        return self.call(user or self.owner,dict(body,revision=p['revision']),p['id'])

    def test_default_off_and_gate_all_endpoints(self):
        self.assertFalse(Space._meta.get_field('research_enabled').default)
        p=self.create()
        self.space.research_enabled=False;self.space.save()
        for response in [self.call(self.owner),self.call(self.owner,id=p['id']),self.update(p,{'action':'settings','status':'active'})]:
            self.assertEqual(response.status_code,403)

    def test_two_state_lifecycle_and_legacy_clients(self):
        p=self.create()
        self.assertEqual(p['status'],'active')
        p=self.update(p,dict(action='task',title='Task',body='Body',start='2026-10-28',assignees=[self.owner.pk])).json()['body']
        task=p['tasks'][0]
        self.assertEqual(task['status'],'active')
        p=self.update(p,dict(action='task_status',task_id=task['id'],status='done')).json()['body']
        self.assertEqual(p['tasks'][0]['status'],'archived')
        self.assertEqual(self.update(p,dict(action='progress',task_id=task['id'],body='Blocked')).status_code,403)
        p=self.update(p,dict(action='task_status',task_id=task['id'],status='active')).json()['body']
        p=self.update(p,dict(action='progress',task_id=task['id'],body='Preserved')).json()['body']
        p=self.update(p,dict(action='settings',status='completed')).json()['body']
        self.assertEqual(p['status'],'archived')
        self.assertEqual(self.call(self.owner).json()['body'][0]['status'],'archived')
        self.assertEqual(self.update(p,dict(action='progress',task_id=task['id'],body='Blocked')).status_code,403)
        p=self.update(p,dict(action='settings',status='active')).json()['body']
        self.assertEqual(p['tasks'][0]['progress'][0]['body'],'Preserved')
        self.assertEqual(self.update(p,dict(action='settings',status='unknown')).status_code,400)

    def test_markdown_description_is_owner_only_versioned_and_safety_checked(self):
        p=self.create();self.member(p,self.student)
        body='## Research goals\n\n- **Evidence** first\n- [Resources](https://example.com)'
        self.assertEqual(self.update(p,dict(action='settings',description=body),self.student).status_code,403)
        with patch('Chat.research.check_user_text') as check:
            result=self.update(p,dict(action='settings',description=body))
            self.assertEqual(result.status_code,200,result.content)
            check.assert_called_once()
        updated=result.json()['body']
        self.assertEqual(updated['description'],body)
        self.assertEqual(updated['status'],'active')
        self.assertEqual(self.call(self.student,id=p['id']).json()['body']['description'],body)
        self.assertEqual(self.update(p,dict(action='settings',description='stale')).status_code,409)
        self.assertEqual(self.update(updated,dict(action='settings',description='x'*20001)).status_code,400)
        self.assertEqual(self.update(updated,dict(action='settings',description='')).json()['body']['description'],'')

    def test_status_migration_preserves_records_and_invalidates_old_revisions(self):
        from importlib import import_module
        from django.apps import apps
        from types import SimpleNamespace
        p=self.create()
        ResearchProject.objects.filter(pk=p['id']).update(status='completed',tasks=[dict(id='old',status='done',progress=[dict(id='record',body='Keep')]),dict(id='pending',status='todo',progress=[])])
        import_module('Chat.migrations.0024_research_active_archived').simplify_statuses(apps,SimpleNamespace(connection=SimpleNamespace(alias='default')))
        project=ResearchProject.objects.get(pk=p['id'])
        self.assertEqual(project.status,'archived')
        self.assertEqual([task['status'] for task in project.tasks],['archived','active'])
        self.assertEqual(project.tasks[0]['progress'][0]['body'],'Keep')
        self.assertEqual(project.revision,p['revision']+1)

    @patch.object(Chat, '_require_friendship')
    @patch.object(Chat, '_require_friend_of')
    def test_invitation_card_requires_recipient_consent_and_current_owner(self,*unused):
        from Chat.models import SubmissionInvite
        from Message.models import Message, MessageTypeChoice
        p=self.create()
        sent=self.update(p,{'action':'invite','user_id':self.student.pk})
        self.assertEqual(sent.status_code,200,sent.content)
        self.assertFalse(ChatMember.objects.filter(chat_id=p['chat_id'],user=self.student,status=1).exists())
        self.assertEqual(self.call(self.student,id=p['id']).status_code,404)
        invite=SubmissionInvite.objects.get(chat_id=p['chat_id'])
        self.assertEqual(Message.objects.filter(type=MessageTypeChoice.SUBMISSION_INVITE).count(),1)
        card=invite.jsonl(self.student)
        self.assertTrue(card['can_accept'])
        self.assertEqual(card['project']['id'],p['id'])
        self.assertFalse(invite.jsonl(self.other)['can_accept'])
        invite.accept(self.student)
        self.assertEqual(self.call(self.student,id=p['id']).status_code,200)
        self.assertIsNone(ChatMember.objects.get(chat_id=p['chat_id'],user=self.student).submission_role)
        self.assertEqual(SubmissionInvite.objects.get(pk=invite.pk).effective_status,'accepted')

    @patch.object(Chat, '_require_friendship')
    @patch.object(Chat, '_require_friend_of')
    def test_pending_invites_expire_on_owner_transfer(self,*unused):
        from Chat.models import SubmissionInvite
        p=self.create();self.member(p,self.student)
        p=self.update(p,{'action':'invite','user_id':self.other.pk}).json()['body']
        self.assertIn(self.other.pk,p['pending_invitee_ids'])
        response=self.update(p,{'action':'transfer','user_id':self.student.pk})
        self.assertEqual(response.status_code,200,response.content)
        invite=SubmissionInvite.objects.get(chat_id=p['chat_id'])
        self.assertFalse(invite.jsonl(self.other)['can_accept'])
        with self.assertRaises(Exception):
            invite.accept(self.other)
        self.assertFalse(ChatMember.objects.filter(chat_id=p['chat_id'],user=self.other,status=1).exists())

    def test_real_group_and_member_read_isolation(self):
        p=self.create()
        self.assertEqual(Chat.objects.get(pk=p['chat_id']).purpose,2)
        self.assertEqual(self.call(self.other,id=p['id']).status_code,404)
        self.member(p,self.student)
        self.assertEqual(self.call(self.student,id=p['id']).status_code,200)
        self.assertEqual(self.update(p,{'action':'task','title':'x'},self.student).status_code,403)
        foreign_space=Space.objects.create(slug='foreign-research',name='Foreign',email='foreign@example.com',research_enabled=True)
        foreign=User.create(foreign_space,'Foreign',verified=True)
        self.assertEqual(self.call(foreign,id=p['id']).status_code,404)
        self.assertEqual(self.call(foreign).json()['body'],[])

    def test_member_avatars_use_shared_public_serialization(self):
        self.owner.avatar_frame_style='orbit'
        self.owner.save(update_fields=['avatar_frame_style'])
        p=self.create()
        member=p['members'][0]
        expected=self.owner.dictify('avatar_uri','avatar_cache_key','avatar_frame_style')
        for key,value in expected.items():
            self.assertEqual(member[key],value)
        self.assertEqual(set(member),{'id','name','owner','avatar_uri','avatar_cache_key','avatar_frame_style'})
        self.assertEqual(self.call(self.other,id=p['id']).status_code,404)

    def test_chat_list_and_membership_respect_space_gate(self):
        p=self.create()
        self.assertIn(p['chat_id'],[chat.pk for chat in Chat.get_user_chats(self.owner)])
        self.space.research_enabled=False;self.space.save()
        self.assertNotIn(p['chat_id'],[chat.pk for chat in Chat.get_user_chats(self.owner)])
        self.assertFalse(Chat.objects.get(pk=p['chat_id']).has_active_member(self.owner))

    def test_admin_settings_preserve_and_publish_flag(self):
        self.space.set_admin_settings(name=self.space.name,group_square_enabled=False,chat_enabled=True,square_explore_enabled=False,unverified_group_policy=2,member_limit=None,research_enabled=False)
        self.space.refresh_from_db()
        self.assertFalse(self.space.research_enabled)
        self.space.set_admin_settings(name=self.space.name,group_square_enabled=False,chat_enabled=True,square_explore_enabled=False,unverified_group_policy=2,member_limit=None,research_enabled=True)
        self.space.refresh_from_db()
        self.assertTrue(self.space.json()['research_enabled'])
        self.space.set_admin_settings(name=self.space.name,group_square_enabled=False,chat_enabled=True,square_explore_enabled=False,unverified_group_policy=2,member_limit=None)
        self.space.refresh_from_db()
        self.assertTrue(self.space.research_enabled)

    def test_task_progress_reply_persist_and_version_conflicts(self):
        p=self.create();self.member(p,self.student)
        response=self.update(p,dict(action='task',title='Baseline',body='**Markdown**',start='2026-10-27',end=None,assignees=[self.student.pk]))
        p=response.json()['body'];task=p['tasks'][0]
        self.assertEqual(self.update(dict(p,revision=1),{'action':'settings','status':'active'}).status_code,409)
        p=self.update(p,dict(action='progress',task_id=task['id'],body='Done'),self.student).json()['body']
        parent=p['tasks'][0]['progress'][0]['id']
        p=self.update(p,dict(action='progress',task_id=task['id'],body='Reviewed',reply_to=parent)).json()['body']
        self.assertEqual(self.call(self.student,id=p['id']).json()['body']['tasks'][0]['progress'][0]['reply_to'],parent)
        self.assertEqual(len(ResearchProject.objects.get(pk=p['id']).tasks[0]['progress']),2)

    def test_foreign_assignees_nonfriend_and_invalid_dates(self):
        p=self.create()
        self.assertNotEqual(self.update(p,dict(action='invite',user_id=self.other.pk)).json().get('code'),0)
        self.assertEqual(self.update(p,dict(action='task',title='x',body='x',start='2026-10-28',end='2026-10-27',assignees=[self.owner.pk])).status_code,400)
        self.assertEqual(self.update(p,dict(action='task',title='x',body='x',start='2026-10-28',assignees=[self.other.pk])).status_code,400)

    def test_group_sensitive_operations_cannot_bypass_project(self):
        p=self.create();chat=Chat.objects.get(pk=p['chat_id'])
        for action in [lambda:chat.remove(),lambda:chat.rename(self.owner,'bypass'),lambda:chat.leave(self.owner),lambda:chat.transfer_ownership(self.owner,self.student),lambda:chat.invite_member(self.owner,self.student)]:
            with self.assertRaises(Exception):action()
        self.assertFalse(Chat.objects.get(pk=chat.pk).is_deleted)

    def test_transfer_leave_and_dissolve_follow_shared_membership(self):
        p=self.create();self.member(p,self.student)
        self.assertEqual(self.update(p,dict(action='leave')).status_code,400)
        p=self.update(p,dict(action='transfer',user_id=self.student.pk)).json()['body']
        self.assertEqual(self.update(p,dict(action='dissolve')).status_code,403)
        p=self.update(p,dict(action='leave')).json()['body']
        self.assertEqual(self.call(self.owner,id=p['id']).status_code,404)
        self.assertEqual(self.update(p,dict(action='dissolve'),self.student).status_code,200)
        self.assertEqual(self.call(self.student,id=p['id']).status_code,404)
