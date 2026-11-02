import json
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from django.utils import timezone
from Space.models import Space
from User.models import User
from Sticker.models import StickerSeries, UserSticker, StickerAsset
from Sticker.series import StickerSeriesAssignView, StickerSeriesDetailView, series_name
from Sticker.library import sticker_library_payload


class StickerSeriesTests(TestCase):
    def setUp(self):
        space=Space.objects.create(name='Series',slug='series',email='series@example.com',admin_phone_verified_at=timezone.now())
        self.user=User.create(space,'Owner',email='owner@example.com',verified=True)
        self.other=User.create(space,'Other',email='other@example.com',verified=True)
        self.group=StickerSeries.objects.create(user=self.user,name='小哀')
        self.foreign=StickerSeries.objects.create(user=self.other,name='Private')
        asset=StickerAsset.objects.create(content_hash='f'*64,storage_key='frienden/messages/sticker/fixture.webp')
        self.row=UserSticker.objects.create(user=self.user,asset=asset,is_uploaded=True)
        self.other_row=UserSticker.objects.create(user=self.other,asset=asset)
        self.group.descriptions={str(asset.id):'开心'};self.group.save(update_fields=['descriptions'])

    def request(self,value):
        request=RequestFactory().post('/',data=json.dumps(value),content_type='application/json',HTTP_AUTHORIZATION='Bearer fixture')
        return request

    def invoke(self,view,value,**kwargs):
        with patch('utils.auth.decrypt',return_value={'user_id':self.user.id,'space_id':self.user.space_id}):
            return view.as_view()(self.request(value),**kwargs)

    def test_weighted_names_reject_overlong_and_invalid(self):
        for name in ['五个中文字','abcdefghij','小哀abcdef']:
            self.assertEqual(series_name(name),name)
        for name in ['六个中文字啊','abcdefghijk','小哀abcdefg','','../foo','😃']:
            with self.assertRaises(Exception):series_name(name)

    def test_description_validation_and_submit_requires_every_item(self):
        from Sticker.series import sticker_description, submit
        for value in ['开心','八个汉字描述上限','English label']:self.assertEqual(sticker_description(value),value)
        for value in ['',None,' ', '九个汉字描述超过限额','a'*17,'bad\nline']:
            with self.assertRaises(Exception):sticker_description(value)
        self.group.draft_assets=[self.row.asset_id];self.group.descriptions={};self.group.save()
        with self.assertRaises(Exception):submit(self.group)
        with self.assertRaises(Exception):self.invoke(StickerSeriesDetailView,{'descriptions':{'999999':'开心'}},series_id=self.group.id)
        self.invoke(StickerSeriesDetailView,{'descriptions':{str(self.row.asset_id):'开心'}},series_id=self.group.id)
        with self.captureOnCommitCallbacks(execute=True),patch('utils.notificator_integration.send_sticker_series_review_mail'):
            self.invoke(StickerSeriesDetailView,{'action':'submit'},series_id=self.group.id)
        with self.assertRaises(Exception):self.invoke(StickerSeriesDetailView,{'descriptions':{str(self.row.asset_id):'难过'}},series_id=self.group.id)
        self.group.refresh_from_db();self.assertEqual(self.group.pending_snapshot['descriptions'][str(self.row.asset_id)],'开心')

    def test_draft_description_does_not_change_published_pack(self):
        from Sticker.series import StickerPackView, StickerPackReviewView
        self.group.draft_assets=[self.row.asset_id]
        self.group.published_snapshot={'name':'小哀','asset_ids':[self.row.asset_id],'descriptions':{str(self.row.asset_id):'开心'}};self.group.save()
        self.invoke(StickerSeriesDetailView,{'descriptions':{str(self.row.asset_id):'难过'}},series_id=self.group.id)
        with patch('utils.auth.decrypt',return_value={'user_id':self.user.id}):
            response=StickerPackView.as_view()(RequestFactory().get('/',HTTP_AUTHORIZATION='Bearer fixture'),series_id=self.group.id)
        self.assertEqual(response['assets'][0]['description'],'开心')
        with self.captureOnCommitCallbacks(execute=True),patch('utils.notificator_integration.send_sticker_series_review_mail'):
            self.invoke(StickerSeriesDetailView,{'action':'submit'},series_id=self.group.id)
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.com'}):
            StickerPackReviewView.as_view()(self.request({'approved':True}),series_id=self.group.id)
        self.group.refresh_from_db();self.assertEqual(self.group.published_snapshot['descriptions'][str(self.row.asset_id)],'难过')

    def test_assign_is_atomic_and_cannot_cross_user_boundary(self):
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':self.group.id})
        self.group.refresh_from_db();self.assertEqual(self.group.draft_assets,[self.row.asset_id])
        for value in [{'sticker_ids':[self.row.id],'series_id':self.foreign.id},
                      {'sticker_ids':[self.row.id,self.other_row.id],'series_id':None}]:
            with self.assertRaises(Exception):self.invoke(StickerSeriesAssignView,value)
        self.group.refresh_from_db();self.assertEqual(self.group.draft_assets,[self.row.asset_id])

    def test_series_filters_are_applied_before_pagination(self):
        self.group.draft_assets=[self.row.asset_id];self.group.save()
        self.assertEqual(sticker_library_payload(self.user,0,1,series_id=self.group.id)['items'][0]['sticker_id'],self.row.id)
        self.assertEqual(len(sticker_library_payload(self.user,0,1,series_id=0)['items']),1)
        self.assertEqual(sticker_library_payload(self.other,0,1,series_id=self.group.id)['items'],[])

    def test_deleting_series_preserves_sticker_and_shared_asset(self):
        self.row.series=self.group;self.row.save()
        with patch('utils.auth.decrypt',return_value={'user_id':self.user.id,'space_id':self.user.space_id}):
            request=RequestFactory().delete('/',HTTP_AUTHORIZATION='Bearer fixture')
            StickerSeriesDetailView.as_view()(request,series_id=self.group.id)
        self.row.refresh_from_db();self.assertIsNone(self.row.series_id)
        self.assertTrue(StickerAsset.objects.filter(id=self.row.asset_id).exists())

    def test_collected_artwork_cannot_be_used_to_make_a_collection(self):
        self.row.is_uploaded=False;self.row.save()
        with self.assertRaises(Exception):self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':self.group.id})

    def test_upload_can_only_belong_to_one_collection_and_draft_removal_frees_it(self):
        second=StickerSeries.objects.create(user=self.user,name='Second')
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':self.group.id})
        with self.assertRaises(Exception):self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':second.id})
        self.invoke(StickerSeriesDetailView,{'remove_asset_id':self.row.asset_id},series_id=self.group.id)
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':second.id})
        self.row.refresh_from_db();self.assertEqual(self.row.series_id,second.id)

    def test_direct_upload_cannot_bypass_membership_and_batch_conflicts_are_atomic(self):
        from Sticker.series import collect_uploaded
        second=StickerSeries.objects.create(user=self.user,name='Second')
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':self.group.id})
        request=self.request({'series_id':second.id});request.user=self.user
        with self.assertRaises(Exception):collect_uploaded(request,self.row.asset)
        self.row.refresh_from_db();self.assertEqual(self.row.series_id,self.group.id)
        new_asset=StickerAsset.objects.create(content_hash='a'*64,storage_key='frienden/messages/sticker/new.webp')
        new_row=UserSticker.objects.create(user=self.user,asset=new_asset,is_uploaded=True)
        with self.assertRaises(Exception):self.invoke(StickerSeriesAssignView,{'sticker_ids':[new_row.id,self.row.id],'series_id':second.id})
        new_row.refresh_from_db();self.assertIsNone(new_row.series_id)

    def test_published_membership_stays_reserved_until_removal_review_is_approved(self):
        from Sticker.series import StickerPackReviewView
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':self.group.id})
        self.group.refresh_from_db();self.group.published_snapshot={'name':self.group.name,'asset_ids':self.group.draft_assets};self.group.save()
        second=StickerSeries.objects.create(user=self.user,name='Second')
        self.invoke(StickerSeriesDetailView,{'remove_asset_id':self.row.asset_id},series_id=self.group.id)
        with self.assertRaises(Exception):self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':second.id})
        # Deleting the approved collection is itself a reviewed operation.
        with self.captureOnCommitCallbacks(execute=True),patch('utils.notificator_integration.send_sticker_series_review_mail'):
            with patch('utils.auth.decrypt',return_value={'user_id':self.user.id}):
                StickerSeriesDetailView.as_view()(RequestFactory().delete('/',HTTP_AUTHORIZATION='Bearer fixture'),series_id=self.group.id)
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.com'}):
            StickerPackReviewView.as_view()(self.request({'approved':True}),series_id=self.group.id)
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':second.id})

    def test_submission_is_frozen_and_review_preserves_published_snapshot(self):
        from Sticker.series import StickerPackReviewView
        self.group.draft_assets=[self.row.asset_id];self.group.save()
        with self.captureOnCommitCallbacks(execute=True),patch('utils.notificator_integration.send_sticker_series_review_mail') as notify:
            self.invoke(StickerSeriesDetailView,{'action':'submit'},series_id=self.group.id)
        notify.assert_called_once()
        with self.assertRaises(Exception):self.invoke(StickerSeriesDetailView,{'name':'Changed'},series_id=self.group.id)
        review=self.request({'approved':True})
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.com'}):StickerPackReviewView.as_view()(review,series_id=self.group.id)
        self.group.refresh_from_db();self.assertEqual(self.group.published_snapshot['name'],'小哀')
        self.invoke(StickerSeriesDetailView,{'name':'NewName'},series_id=self.group.id)
        self.group.refresh_from_db();self.assertEqual(self.group.published_snapshot['name'],'小哀')
        with self.captureOnCommitCallbacks(execute=True),patch('utils.notificator_integration.send_sticker_series_review_mail'):
            with patch('utils.auth.decrypt',return_value={'user_id':self.user.id}):
                StickerSeriesDetailView.as_view()(RequestFactory().delete('/',HTTP_AUTHORIZATION='Bearer fixture'),series_id=self.group.id)
        self.group.refresh_from_db();self.assertEqual(self.group.pending_snapshot['action'],'delete')
        self.assertIsNotNone(self.group.published_snapshot)

    def test_collection_upload_does_not_add_to_mine_until_explicitly_collected(self):
        from Sticker.series import collect_uploaded
        self.row.delete()
        request=self.request({'series_id':self.group.id});request.user=self.user
        row=collect_uploaded(request,StickerAsset.objects.get(id=self.other_row.asset_id))
        self.assertFalse(row.in_home);self.assertTrue(row.is_uploaded)
        self.assertEqual(sticker_library_payload(self.user,0,30)['items'],[])
        UserSticker.collect(self.user,row.asset)
        self.assertEqual(len(sticker_library_payload(self.user,0,30)['items']),1)

    def test_unadded_collection_uploads_do_not_leak_into_global_explore(self):
        from Sticker.library import sticker_explore_payload
        asset=StickerAsset.objects.create(content_hash='b'*64,storage_key='frienden/messages/sticker/private.webp')
        UserSticker.objects.create(user=self.user,asset=asset,is_uploaded=True,in_home=False)
        visible=sticker_explore_payload(self.other,0,30)['items']
        self.assertNotIn(asset.id,[item['sticker_asset_id'] for item in visible])

    def test_rejecting_update_preserves_live_version_and_review_requires_admin(self):
        from Sticker.series import StickerPackReviewView
        self.group.published_snapshot={'name':'Original','asset_ids':[self.row.asset_id]}
        self.group.name='NewName';self.group.draft_assets=[self.row.asset_id];self.group.save()
        with self.captureOnCommitCallbacks(execute=True),patch('utils.notificator_integration.send_sticker_series_review_mail'):
            self.invoke(StickerSeriesDetailView,{'action':'submit'},series_id=self.group.id)
        self.assertNotEqual(self.client.post(f'/platform-admin/sticker-packs/{self.group.id}',data='{}',content_type='application/json').status_code,200)
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.com'}):
            StickerPackReviewView.as_view()(self.request({'approved':False,'note':'Please revise'}),series_id=self.group.id)
        self.group.refresh_from_db()
        self.assertEqual(self.group.published_snapshot['name'],'Original')
        self.assertIsNone(self.group.pending_snapshot)
        self.assertEqual(self.group.review_note,'Please revise')
