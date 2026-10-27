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
