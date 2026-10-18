from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice, ChatTypeChoice, Submission, SubmissionStatusChoice
from Friendship.models import Friendship
from Message.models import Message, MessageTypeChoice
from Space.models import Space
from Square.models import Statement, StatementVisibilityChoice, SquareMute
from User.models import OAuthGrant, User
from User.oauth import OAuthError
from User.oauth_requests import create
from User.oauth_registry import DELEGATED_SCOPES, RegistryError, approved, configuration
from User.oauth_scopes import available_scopes, catalog, permission_groups
from User import tests_oauth as base
from User import tests_oauth_chat as chat_fixture


@override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=True)
class FiveDomainTests(TestCase):
    def setUp(self):
        base.OAuthTests.setUp(self)
        self.user.is_permanent_vip=True
        self.user.save(update_fields=['is_permanent_vip'])
        revision=approved('stillfolio')
        revision.scopes=sorted(set(revision.scopes)|DELEGATED_SCOPES)
        revision.save(update_fields=['scopes'])
        self.space.admin_phone_verified_at=timezone.now()
        self.space.group_square_enabled=True
        self.space.submission_enabled=True
        self.space.save(update_fields=['admin_phone_verified_at','group_square_enabled','submission_enabled'])
        self.official=self.space.ensure_official_user()
        self.peer=User.create(self.space,'peer',verified=True)
        self.content_check=patch('User.oauth_actions.check_user_text').start()
        self.addCleanup(patch.stopall)

    token=chat_fixture.DelegatedChatTests.token
    headers=chat_fixture.DelegatedChatTests.headers

    def call(self,method,path,scopes,body=None):
        header=self.headers(self.token(scopes))
        return getattr(self.client,method)('/users/oauth/'+path,body or {},content_type='application/json',**header) if method=='post' else getattr(self.client,method)('/users/oauth/'+path,**header)

    def test_catalog_is_five_domains_and_rejects_unimplemented_or_excluded_permissions(self):
        rows=catalog()
        delegated=[row for row in rows if '.' in row['scope']]
        self.assertEqual(len(delegated),15)
        self.assertEqual({row['domain'] for row in delegated},{'chat','square','submission','contacts','personal'})
        self.assertEqual(len([row for row in delegated if row['available']]),13)
        for scope in ('square.modify','personal.write','plugins.read','security.modify','notifications.read','chat.events','chat.send'):
            with self.assertRaises(OAuthError): self.token(scope)
        revision=approved('stillfolio')
        config=dict(name=revision.name,redirect_uris=revision.redirect_uris,scopes=['chat.read'])
        with override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=False):
            self.assertEqual(available_scopes(),{'profile','email','phone','vip'})
            with self.assertRaises(RegistryError): configuration(config)
            self.assertFalse(any(row['available'] for row in catalog() if '.' in row['scope']))
        self.assertEqual(permission_groups(['chat.read','email']),[{'key':'chat','scopes':['chat.read']},{'key':'personal','scopes':['email']}])

    def test_metadata_describes_every_scope_and_admin_proof_cannot_delegate_actions(self):
        params=dict(client_id='stillfolio',redirect_uri=approved('stillfolio').redirect_uris[0],
                    scope=' '.join(sorted(DELEGATED_SCOPES)),state='s'*32,
                    code_challenge=self.challenge,code_challenge_method='S256',response_type='code')
        unbound=self.client.get('/users/oauth/metadata',params)
        self.assertEqual(unbound.status_code,400)
        flow=create({**params,'space_id':self.space.id})
        response=self.client.get('/users/oauth/metadata',{'request_id':flow['request_id']})
        self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(set(scope for group in response.json()['permission_groups'] for scope in group['scopes']),DELEGATED_SCOPES)
        app=approved('stillfolio').application
        app.space_admin_proof_enabled=True
        app.save(update_fields=['space_admin_proof_enabled'])
        with self.assertRaises(OAuthError): create({**params,'purpose':'space_admin'})

    def test_shared_policy_loss_of_verification_and_rollout_apply_to_every_domain(self):
        paths={'chat':'chat/chats','square':'square/statements','submission':'submissions','contacts':'contacts','personal':'personal'}
        headers={domain:self.headers(self.token(domain+'.read')) for domain in paths}
        User.objects.filter(pk=self.user.pk).update(phone_verified_at=None)
        for domain,path in paths.items():
            self.assertEqual(self.client.get('/users/oauth/'+path,**headers[domain]).status_code,403)
        User.objects.filter(pk=self.user.pk).update(phone_verified_at=timezone.now())
        with override_settings(OAUTH_DELEGATED_ACTIONS_ENABLED=False):
            for domain,path in paths.items():
                self.assertEqual(self.client.get('/users/oauth/'+path,**headers[domain]).status_code,401)

    def test_cross_domain_scope_and_old_grant_never_gain_actions(self):
        token=self.token('profile')
        old_grant=OAuthGrant.objects.get(access_token_hash__isnull=False)
        revision=approved('stillfolio')
        self.assertIn('personal.read',revision.scopes)
        for path in ('personal','square/statements','contacts','submissions'):
            self.assertEqual(self.client.get('/users/oauth/'+path,**self.headers(token)).status_code,403)
        for path in ('personal','contacts','submissions'):
            self.assertEqual(self.call('get',path,'square.read').status_code,403)
        old_grant.refresh_from_db()
        self.assertEqual(old_grant.scopes,['profile'])

    def test_personal_allowlist_language_and_atomic_style_update(self):
        response=self.call('get','personal','personal.read')
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn('email',response.json())
        self.assertNotIn('phone',response.json())
        self.assertNotIn('password',response.json())
        result=self.call('post','personal','personal.modify',{'language_preference':'en'})
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['language'],'en')
        for body in ({'password':'new'},{'email':'x@example.test'},{'notifications_muted':True},{'name':'x','show_self_avatar':'yes'}):
            self.assertEqual(self.call('post','personal','personal.modify',body).status_code,400)
        self.assertEqual(self.call('post','personal','personal.read',{'language_preference':'en'}).status_code,403)
        response=self.call('post','personal','personal.modify',{'language_preference':'zh-CN','chat_bubble_style':'not-owned-or-valid'})
        self.assertEqual(response.status_code,403)
        self.user.refresh_from_db()
        self.assertEqual(self.user.language,'en')

    def test_square_text_visibility_and_anonymous_identity_redaction(self):
        own=Statement.objects.create(space=self.space,user=self.user,text='mine',visibility=StatementVisibilityChoice.FRIENDS)
        private=Statement.objects.create(space=self.space,user=self.peer,text='not your friend',visibility=StatementVisibilityChoice.FRIENDS)
        anonymous=Statement.objects.create(space=self.space,user=self.peer,text='public anonymous',is_anonymous=True)
        data=self.call('get','square/statements?scope=all','square.read').json()
        self.assertIn(own.id,[item['statement_id'] for item in data['statements']])
        self.assertNotIn(private.id,[item['statement_id'] for item in data['statements']])
        self.assertIsNone(next(item for item in data['statements'] if item['statement_id']==anonymous.id)['user'])
        self.assertNotIn('media',data['statements'][0])
        self.assertEqual(self.call('get',f'square/statements/{private.id}/comments','square.read').status_code,403)
        self.assertEqual(self.call('post','square/statements','square.read',{'text':'denied','visibility':'public'}).status_code,403)
        self.assertEqual(self.call('post','square/statements','square.write',{'text':'denied','visibility':'public','pin':True}).status_code,400)
        result=self.call('post','square/statements','square.write',{'text':'fixture published','visibility':'friends'})
        self.assertEqual(result.status_code,200,result.content)
        comment=self.call('post',f'square/statements/{own.id}/comments','square.write',{'text':'fixture comment'})
        self.assertEqual(comment.status_code,200,comment.content)
        self.assertEqual(self.call('get',f"square/statements/{own.id}/comments",'square.read').json()['comments'][0]['text'],'fixture comment')
        self.space.square_free_post_enabled=False
        self.space.save(update_fields=['square_free_post_enabled'])
        self.assertEqual(self.call('post','square/statements','square.write',{'text':'no','visibility':'public'}).status_code,403)
        self.space.square_free_post_enabled=True
        self.space.save(update_fields=['square_free_post_enabled'])
        SquareMute.objects.create(space=self.space,user=self.user,reason='fixture mute')
        self.assertEqual(self.call('post',f'square/statements/{own.id}/comments','square.write',{'text':'muted'}).status_code,403)

    def test_contacts_only_own_requests_and_same_space(self):
        result=self.call('post','contacts','contacts.write',{'peer_user_id':self.peer.id})
        self.assertEqual(result.status_code,200,result.content)
        request_id=result.json()['request']['request_id']
        self.assertEqual(self.call('post',f'contacts/requests/{request_id}/respond','contacts.modify',{'accept':True}).status_code,403)
        data=self.call('get','contacts','contacts.read').json()
        self.assertTrue(data['requests'])
        self.assertNotIn('email',str(data))
        requester=User.create(self.space,'requester',verified=True)
        incoming=Friendship.create(requester,self.user)
        accepted=self.call('post',f'contacts/requests/{incoming.id}/respond','contacts.modify',{'accept':True})
        self.assertEqual(accepted.status_code,200,accepted.content)
        self.assertTrue(self.call('get','contacts','contacts.read').json()['friends'])
        other=Space.objects.create(name='other',slug='action-other',email='other@example.test')
        outsider=User.create(other,'outside')
        self.assertEqual(self.call('post','contacts','contacts.write',{'peer_user_id':outsider.id}).status_code,403)

    def test_submission_author_reviewer_state_and_visibility_enforced(self):
        body=dict(peer_user_id=self.official.id,title='fixture draft',client_draft_id='fixture-draft',content='draft private',client_message_id='draft-1')
        created=self.call('post','submissions','submission.write',body)
        self.assertEqual(created.status_code,200,created.content)
        chat_id=created.json()['submission']['chat_id']
        self.assertEqual(self.call('get',f'submissions/{chat_id}/messages','submission.read').json()['messages'][0]['content'],'draft private')
        self.assertEqual(self.call('get',f'chat/chats/{chat_id}/messages','chat.read').status_code,403)
        self.assertEqual(self.call('post',f'submissions/{chat_id}/review','submission.modify',{'action':'ready'}).status_code,403)
        submitted=self.call('post',f'submissions/{chat_id}/submit','submission.write')
        self.assertEqual(submitted.status_code,200,submitted.content)
        self.assertEqual(submitted.json()['submission']['status'],'review')
        self.assertEqual(self.call('post',f'submissions/{chat_id}/messages','submission.write',{'content':'no','client_message_id':'no'}).status_code,403)
        self.assertEqual(self.call('post',f'submissions/{chat_id}/review','submission.modify',{'action':'terminate'}).status_code,400)
        # Reviewer fixture receives only the released round; author-only draft stays hidden.
        User.objects.filter(pk=self.official.pk).update(email='official@example.test',email_verified_at=timezone.now(),phone='+8613800138001',phone_verified_at=timezone.now())
        author=self.user
        self.user=self.official
        reviewed=self.call('post',f'submissions/{chat_id}/review','submission.modify',{'action':'revision'})
        self.assertEqual(reviewed.status_code,200,reviewed.content)
        self.user=author
        self.assertEqual(self.call('post',f'submissions/{chat_id}/messages','submission.write',{'content':'new private round','client_message_id':'round-2'}).status_code,200)
        self.user=self.official
        messages=self.call('get',f'submissions/{chat_id}/messages','submission.read').json()['messages']
        self.assertNotIn('new private round',[item['content'] for item in messages])
        self.user=self.peer
        User.objects.filter(pk=self.peer.pk).update(email='peer@example.test',email_verified_at=timezone.now(),phone='+8613800138002',phone_verified_at=timezone.now())
        self.assertEqual(self.call('get',f'submissions/{chat_id}/messages','submission.read').status_code,403)
