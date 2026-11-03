import json
from django.test import TestCase
from django.utils import timezone
from Square.models import Statement, StatementLike, StatementComment, StatementVisibilityChoice
from Space.models import Space
from User.models import User
from utils import auth
from Friendship.models import Friendship


class StatementSyncTests(TestCase):
    def setUp(self):
        self.space=Space.objects.create(name='Sync',slug='sync',email='sync@example.com',group_square_enabled=True,admin_phone_verified_at=timezone.now())
        self.viewer=User.create(space=self.space,name='Viewer')
        self.author=User.create(space=self.space,name='Author')
        self.public=Statement.objects.create(space=self.space,user=self.author,text='Public')

    def sync(self, ids, user=None):
        token=auth.get_login_token(user or self.viewer)['auth']
        return self.client.post('/square/statements/sync',data=json.dumps({'statement_ids':ids}),content_type='application/json',HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_sync_reports_deletion_permission_loss_and_other_spaces_without_leaking_content(self):
        private=Statement.objects.create(space=self.space,user=self.author,text='Private secret',visibility=StatementVisibilityChoice.FRIENDS)
        deleted=Statement.objects.create(space=self.space,user=self.author,text='Deleted secret',is_deleted=True)
        other=Space.objects.create(name='Other',slug='other',email='other@example.com')
        foreign=Statement.objects.create(space=other,user=User.create(space=other,name='Other'),text='Foreign secret')
        ids=[self.public.id,private.id,deleted.id,foreign.id,999999]
        response=self.sync(ids).json()
        self.assertEqual(response['code'],200,response)
        data=response['body']
        self.assertEqual([item['statement_id'] for item in data['items']],[self.public.id])
        self.assertEqual(data['removed_ids'],ids[1:])
        self.assertNotIn('secret',json.dumps(data))

    def test_counts_and_liked_state_are_viewer_specific_and_ignore_deleted_comments(self):
        StatementLike.objects.create(statement=self.public,user=self.viewer)
        StatementComment.objects.create(statement=self.public,user=self.author,text='Comment')
        StatementComment.objects.create(statement=self.public,user=self.author,text='Deleted',is_deleted=True)
        item=self.sync([self.public.id]).json()['body']['items'][0]
        self.assertEqual((item['like_count'],item['comment_count'],item['liked']),(1,1,True))
        self.assertFalse(self.sync([self.public.id],self.author).json()['body']['items'][0]['liked'])

    def test_invalid_batches_are_rejected_and_empty_batches_are_safe(self):
        self.assertEqual(self.sync([]).json()['body'],{'items':[],'removed_ids':[]})
        for ids in [[True],[0],[-1],['1'],[1.5],list(range(1,102))]:
            self.assertNotEqual(self.sync(ids).json()['code'],200,ids)

    def test_sync_requires_authentication(self):
        response=self.client.post('/square/statements/sync',data=json.dumps({'statement_ids':[self.public.id]}),content_type='application/json')
        self.assertNotEqual(response.json()['code'],200)

    def test_friendship_loss_is_reported_as_unavailable(self):
        private=Statement.objects.create(space=self.space,user=self.author,text='Private',visibility=StatementVisibilityChoice.FRIENDS)
        Friendship.ensure_locked_friendship(self.author,self.viewer)
        self.assertEqual(self.sync([private.id]).json()['body']['removed_ids'],[])
        Friendship.objects.all().delete()
        self.assertEqual(self.sync([private.id]).json()['body']['removed_ids'],[private.id])
