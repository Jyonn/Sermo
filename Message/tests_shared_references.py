import datetime
import json
from types import SimpleNamespace
from unittest.mock import patch
from django.test import TestCase
from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatMemberRoleChoice, ChatTypeChoice, ResearchProject
from Message.models import Message, ForwardBundle
from Message.shared_references import normalize, payload
from Message.validators import MessageErrors
from Space.models import Space
from User.models import User
from utils import auth


class SharedReferenceTests(TestCase):
    def setUp(self):
        self.space=Space.objects.create(slug='refs',name='References',email='test@example.com',research_enabled=True)
        self.alice=User.create(self.space,'Alice',verified=True)
        self.bob=User.create(self.space,'Bob',verified=True)
        self.outsider=User.create(self.space,'Outside',verified=True)
        self.foreign=User.create(Space.objects.create(slug='elsewhere',name='Elsewhere',email='other@example.com',research_enabled=True),'Foreign',verified=True)
        self.chat=self.group([self.alice,self.bob,self.outsider])
        self.project=ResearchProject.objects.create(chat=self.group([self.alice,self.bob]),start=datetime.date(2026,11,19),tasks=[dict(
            id='task1',title='Private task',body='private task body',status='active',start='2026-11-19',end=None,assignees=[self.bob.pk],progress=[
                dict(id='comment1',user_id=self.alice.pk,body='private comment',created_at='2026-11-19T12:00:00Z'),
                dict(id='reply1',user_id=self.bob.pk,body='private reply',created_at='2026-11-19T12:01:00Z',reply_to='comment1'),
            ])])
        patch.object(User,'require_capability').start()
        self.addCleanup(patch.stopall)

    def group(self,users):
        chat=Chat.objects.create(space=self.space,chat_type=ChatTypeChoice.GROUP,title='Research',created_by=users[0])
        for index,user in enumerate(users):
            ChatMember.objects.create(chat=chat,user=user,role=ChatMemberRoleChoice.OWNER if index==0 else ChatMemberRoleChoice.MEMBER,status=ChatMemberStatusChoice.ACTIVE)
        return chat

    def reference(self,kind):
        if kind==15:return dict(profile_user_id=self.bob.pk)
        return dict(project_id=self.project.pk,task_id='task1',**({'progress_id':'comment1' if kind==17 else 'reply1'} if kind!=16 else {}))

    def send(self,kind,user=None,reference=None,client_id=None):
        return Message.create(self.chat,user or self.alice,kind,json.dumps(reference or self.reference(kind)),client_message_id=client_id)

    def test_all_four_types_send_round_trip_and_ignore_forged_snapshots(self):
        for kind in (15,16,17,18):
            message=self.send(kind,reference=dict(self.reference(kind),text='FORGED',profile={'name':'FORGED'}))
            self.assertNotIn('FORGED',message.content)
            data=message.jsonl(SimpleNamespace(user=self.bob))
            self.assertEqual(data['min_client_version'],'2026.11.19.6')
            self.assertFalse(data['payload'].get('unavailable',False))
            if kind==15:self.assertEqual(data['payload']['profile']['name'],'Bob')
            else:self.assertIn('private',data['payload']['text'])

    def test_research_permissions_apply_on_every_read_and_never_leak_to_outsiders(self):
        for kind in (16,17,18):
            message=self.send(kind)
            for viewer in (self.outsider,self.foreign,None):
                result=message._payload_for_type(SimpleNamespace(user=viewer))
                self.assertTrue(result['unavailable'])
                self.assertNotIn('private',json.dumps(result).lower())
                self.assertNotIn('project_title',result)
            membership=ChatMember.objects.get(chat=self.project.chat,user=self.bob)
            membership.status=ChatMemberStatusChoice.LEFT;membership.save()
            self.assertTrue(message._payload_for_type(SimpleNamespace(user=self.bob))['unavailable'])
            membership.status=ChatMemberStatusChoice.ACTIVE;membership.save()

    def test_deleted_and_disabled_content_cannot_be_read_or_sent(self):
        message=self.send(16)
        self.space.research_enabled=False;self.space.save()
        self.assertTrue(message._payload_for_type(SimpleNamespace(user=self.alice))['unavailable'])
        with self.assertRaises(Exception):self.send(16)
        self.space.research_enabled=True;self.space.save()
        self.project.tasks=[];self.project.save()
        self.assertTrue(message._payload_for_type(SimpleNamespace(user=self.alice))['unavailable'])

    def test_cross_space_profiles_invalid_ids_and_mismatched_reply_types_are_rejected(self):
        for kind,ref in [(15,{'profile_user_id':self.foreign.pk}),(17,dict(self.reference(17),progress_id='reply1')),(18,dict(self.reference(18),progress_id='comment1')),(16,dict(self.reference(16),task_id='missing'))]:
            with self.assertRaises(Exception):self.send(kind,reference=ref)
        for value in ['[]','{}','{"profile_user_id":true}']:
            with self.assertRaises(Exception):normalize(15,value)
        for value in (True,-1,'1'):
            with self.assertRaises(Exception):normalize(16,json.dumps(dict(self.reference(16),project_id=value)))

    def test_forward_bundles_store_only_references_and_recheck_the_reader(self):
        messages=[self.send(kind) for kind in (15,16,17,18)]
        bundle=ForwardBundle.create_from_messages(messages,self.alice,SimpleNamespace(user=self.alice))
        for item in bundle.items.all():
            self.assertNotIn('private',json.dumps(item.payload).lower())
            self.assertNotIn('text',item.payload)
        outside=bundle.jsonl(SimpleNamespace(user=self.outsider))
        self.assertFalse(outside['items'][0]['payload'].get('unavailable',False))
        for item in outside['items'][1:]:self.assertTrue(item['payload']['unavailable'])
        inside=bundle.jsonl(SimpleNamespace(user=self.bob))
        self.assertEqual(inside['items'][2]['payload']['text'],'private comment')
        forwarded=Message.forward_individual(messages[1],self.chat,self.bob)
        self.assertEqual(forwarded.content,messages[1].content)
        with self.assertRaises(Exception):Message.forward_individual(messages[1],self.chat,self.outsider)

    def test_api_accepts_reference_types_and_idempotency(self):
        headers={'HTTP_AUTHORIZATION':'Bearer '+auth.get_login_token(self.alice)['auth']}
        for kind in (15,16,17,18):
            body=dict(type=kind,content=json.dumps(self.reference(kind)),client_message_id=f'reference-{kind}')
            responses=[self.client.post(f'/messages/?chat_id={self.chat.pk}',json.dumps(body),content_type='application/json',**headers) for _ in range(2)]
            self.assertEqual(responses[0].status_code,200,responses[0].content)
            self.assertEqual(responses[0].json()['body']['message_id'],responses[1].json()['body']['message_id'])

    def test_event_sync_and_bundle_forward_enforce_viewer_permissions(self):
        from Message.models import MessageEvent
        message=self.send(17)
        synced=MessageEvent.sync_for_user(self.outsider,0,100,SimpleNamespace(user=self.outsider))
        self.assertNotIn('private',json.dumps(synced).lower())
        headers={'HTTP_AUTHORIZATION':'Bearer '+auth.get_login_token(self.alice)['auth']}
        response=self.client.post('/messages/forward',json.dumps(dict(message_ids=[message.pk],target_chat_ids=[self.chat.pk],mode='bundle')),content_type='application/json',**headers)
        self.assertEqual(response.status_code,200,response.content)
