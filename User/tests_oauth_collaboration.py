from unittest.mock import patch
from urllib.parse import parse_qs,urlparse

from django.test import TestCase,override_settings
from django.utils import timezone

from Chat.models import Chat,ChatMember,ChatMemberStatusChoice
from Friendship.models import Friendship,FriendshipStatusChoice
from Space.models import Space
from User.models import User
from User.oauth import exchange,userinfo
from User.oauth_registry import approved
from User.oauth_requests import create
from User.oauth_scopes import COLLABORATION_SCOPES
from User import tests_oauth_actions as fixture
from utils.auth import get_login_token


@override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=True)
class CollaborationTests(TestCase):
    def setUp(self):
        fixture.FiveDomainTests.setUp(self)
        revision=approved('stillfolio')
        revision.scopes=sorted(set(revision.scopes)|{'email'})
        revision.save(update_fields=['scopes'])
        for index,user in enumerate((self.peer,self.official)):
            User.objects.filter(pk=user.pk).update(email=f'fixture{index}@example.test',email_verified_at=timezone.now(),
                phone=f'+861380013800{index+1}',phone_verified_at=timezone.now(),is_permanent_vip=True)
            user.refresh_from_db()
        for user in (self.user,self.peer):
            user.set_password('synthetic-fixture-password')
            User.objects.filter(pk=user.pk).update(growth_score=5300)
            user.refresh_from_db()
        patch('User.oauth_collaboration.check_user_text').start()
        patch('User.oauth_chat.check_user_text').start()

    token=fixture.FiveDomainTests.token
    headers=fixture.FiveDomainTests.headers
    call=fixture.FiveDomainTests.call

    def friend(self,other):
        # Fixture setup only; production creation always goes through requests.
        Chat.ensure_direct_friendship(self.user,other)

    def group(self,ids=None):
        response=self.call('post','chat/groups','chat.write',{'user_ids':ids or [],'title':'fixture research group'})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()['chat']['chat_id']

    def manage(self,chat_id,action,**body):
        return self.call('post',f'chat/chats/{chat_id}/manage','chat.modify',{'action':action,**body})

    def test_private_chat_requires_friendship_then_sends_and_reads(self):
        self.assertEqual(self.call('post','chat/direct','chat.write',{'peer_user_id':self.peer.id}).status_code,403)
        pending=self.call('post','contacts','contacts.write',{'peer_user_id':self.peer.id})
        self.assertEqual(pending.status_code,200,pending.content)
        request_id=pending.json()['request']['request_id']
        author=self.user;self.user=self.peer
        self.assertEqual(self.call('post',f'contacts/requests/{request_id}/respond','contacts.modify',{'accept':True}).status_code,200)
        self.user=author
        first=self.call('post','chat/direct','chat.write',{'peer_user_id':self.peer.id})
        self.assertEqual(first.status_code,200,first.content)
        chat_id=first.json()['chat']['chat_id']
        second=self.call('post','chat/direct','chat.write',{'peer_user_id':self.peer.id})
        self.assertEqual(second.json()['chat']['chat_id'],chat_id)
        sent=self.call('post',f'chat/chats/{chat_id}/messages','chat.write',{'content':'private fixture','client_message_id':'fixture-private'})
        self.assertEqual(sent.status_code,200,sent.content)
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}/messages','chat.read').json()['messages'][0]['content'],'private fixture')
        Friendship.objects.filter(pk=request_id).update(status=FriendshipStatusChoice.DELETED)
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}','chat.read').status_code,403)

    def test_reported_creation_policy_matches_enforcement_and_old_identity_grant(self):
        self.assertTrue(self.call('get','chat/capabilities','chat.read').json()['create_group'])
        User.objects.filter(pk=self.user.pk).update(growth_score=0)
        self.assertFalse(self.call('get','chat/capabilities','chat.read').json()['create_group'])
        self.assertEqual(self.call('post','chat/groups','chat.write',{'user_ids':[]}).status_code,403)
        self.assertEqual(self.call('post','chat/groups','profile email',{'user_ids':[]}).status_code,403)

    def test_group_creation_management_owner_transfer_and_live_membership(self):
        chat_id=self.group()
        self.assertEqual(self.manage(chat_id,'invite',user_ids=[self.peer.id]).status_code,403)
        self.friend(self.peer)
        invited=self.manage(chat_id,'invite',user_ids=[self.peer.id])
        self.assertEqual(invited.status_code,200,invited.content)
        self.assertTrue(ChatMember.objects.filter(chat_id=chat_id,user=self.peer,status=ChatMemberStatusChoice.ACTIVE).exists())
        owner=self.user;self.user=self.peer
        self.assertEqual(self.manage(chat_id,'remove',user_ids=[owner.id]).status_code,403)
        self.assertEqual(self.manage(chat_id,'background',background_theme='').status_code,403)
        self.assertEqual(self.manage(chat_id,'transfer_owner',peer_user_id=owner.id).status_code,403)
        self.user=owner
        self.assertEqual(self.manage(chat_id,'rename',title='fixture renamed').status_code,200)
        self.assertEqual(self.manage(chat_id,'background',background_theme='').status_code,200)
        self.assertEqual(self.manage(chat_id,'remove',user_ids=[owner.id]).status_code,403)
        self.assertEqual(self.manage(chat_id,'transfer_owner',peer_user_id=self.peer.id).status_code,200)
        self.assertEqual(self.manage(chat_id,'remove',user_ids=[self.peer.id]).status_code,403)
        former=self.user;self.user=self.peer
        self.assertEqual(self.manage(chat_id,'leave').status_code,403)
        self.assertEqual(self.manage(chat_id,'transfer_owner',peer_user_id=former.id).status_code,200)
        self.user=former
        self.assertEqual(self.manage(chat_id,'remove',user_ids=[self.peer.id]).status_code,200)
        self.user=self.peer
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}','chat.read').status_code,403)

    def test_group_mutes_and_only_finite_group_actions(self):
        self.friend(self.peer)
        chat_id=self.group([self.peer.id])
        self.assertEqual(self.manage(chat_id,'mute',peer_user_id=self.peer.id,duration='permanent').status_code,400)
        self.assertEqual(self.manage(chat_id,'mute',peer_user_id=self.peer.id,duration='2m').status_code,200)
        owner=self.user;self.user=self.peer
        self.assertEqual(self.call('post',f'chat/chats/{chat_id}/messages','chat.write',{'content':'muted fixture','client_message_id':'muted-fixture'}).status_code,403)
        self.user=owner
        self.assertEqual(self.manage(chat_id,'unmute',peer_user_id=self.peer.id).status_code,200)
        for action in ('delete','set_admin','global_mute','notifications'):
            self.assertEqual(self.manage(chat_id,action).status_code,400)
        self.assertEqual(self.manage(chat_id,'rename',title='x',notifications_muted=True).status_code,400)
        self.assertEqual(self.call('post',f'chat/chats/{chat_id}/read','chat.modify').status_code,200)

    def test_batch_invitation_rollback_and_cross_space_rejection(self):
        self.friend(self.peer)
        chat_id=self.group()
        outsider=User.create(self.space,'notfriend',verified=True)
        response=self.manage(chat_id,'invite',user_ids=[self.peer.id,outsider.id])
        self.assertEqual(response.status_code,403)
        self.assertFalse(ChatMember.objects.filter(chat_id=chat_id,user=self.peer).exists())
        other=Space.objects.create(name='other',slug='collab-other',email='other@example.test')
        remote=User.create(other,'remote')
        self.assertEqual(self.manage(chat_id,'invite',user_ids=[remote.id]).status_code,403)
        self.assertEqual(self.call('post','chat/direct','chat.write',{'peer_user_id':remote.id}).status_code,403)
        self.assertEqual(self.call('post','chat/groups','chat.read',{'user_ids':[]}).status_code,403)
        self.assertEqual(self.call('post','chat/groups','profile email',{'user_ids':[]}).status_code,403)

    def test_pending_invitation_and_exact_search_do_not_bypass_membership(self):
        chat_id=self.group()
        ChatMember.objects.create(chat_id=chat_id,user=self.peer,status=ChatMemberStatusChoice.PENDING,invited_by=self.user)
        self.user=self.peer
        invitations=self.call('get','chat/invitations','chat.read').json()['invitations']
        self.assertEqual(invitations[0]['chat_id'],chat_id)
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}','chat.read').status_code,403)
        response=self.call('post',f'chat/chats/{chat_id}/invitation','chat.modify',{'accept':True})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}','chat.read').status_code,200)
        self.assertEqual(self.manage(chat_id,'leave').status_code,200)
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}','chat.read').status_code,403)
        found=self.call('get','contacts/search?name=Photographer','contacts.read')
        self.assertEqual(found.status_code,200,found.content)
        self.assertNotIn('email',str(found.json()))

    def test_teacher_bootstrap_combines_explicit_collaboration_and_rechecks_role(self):
        app=approved('stillfolio').application
        app.space_admin_proof_enabled=True;app.save(update_fields=['space_admin_proof_enabled'])
        params=dict(client_id='stillfolio',redirect_uri=approved('stillfolio').redirect_uris[0],scope='profile email '+' '.join(sorted(COLLABORATION_SCOPES)),
            state='s'*32,code_challenge=self.challenge,code_challenge_method='S256',response_type='code',purpose='space_admin')
        flow=create(params)
        metadata=self.client.get('/users/oauth/metadata',{'request_id':flow['request_id']})
        self.assertEqual(metadata.status_code,200,metadata.content)
        self.assertIsNone(metadata.json()['target_space'])
        self.assertEqual(set(metadata.json()['scopes']),COLLABORATION_SCOPES|{'profile','email'})
        denied=self.client.post('/users/oauth/authorize',{'request_id':flow['request_id']},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+get_login_token(self.user)['auth'])
        self.assertEqual(denied.json()['error'],'admin_required')
        consent=self.client.post('/users/oauth/authorize',{'request_id':flow['request_id']},content_type='application/json',HTTP_AUTHORIZATION='Bearer '+get_login_token(self.official)['auth'])
        self.assertEqual(consent.status_code,200,consent.content)
        code=parse_qs(urlparse(consent.json()['redirect_uri']).query)['code'][0]
        access=exchange(client_id='stillfolio',redirect_uri=params['redirect_uri'],code=code,code_verifier=self.verifier)['access_token']
        identity=userinfo(access)
        self.assertTrue(identity['space_admin'])
        self.assertEqual(identity['space_id'],self.space.id)
        created=self.client.post('/users/oauth/chat/groups',{'user_ids':[],'title':'teacher research fixture'},content_type='application/json',**self.headers(access))
        self.assertEqual(created.status_code,200,created.content)
        self.assertTrue(created.json()['chat']['is_owner'])
        Space.objects.filter(pk=self.space.id).update(official_user=None)
        self.assertNotEqual(self.client.get('/users/oauth/chat/chats',**self.headers(access)).status_code,200)
