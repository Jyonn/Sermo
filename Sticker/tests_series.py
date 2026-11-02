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
        self.row=UserSticker.objects.create(user=self.user,asset=asset)
        self.other_row=UserSticker.objects.create(user=self.other,asset=asset)

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

    def test_assign_is_atomic_and_cannot_cross_user_boundary(self):
        self.invoke(StickerSeriesAssignView,{'sticker_ids':[self.row.id],'series_id':self.group.id})
        self.row.refresh_from_db();self.assertEqual(self.row.series_id,self.group.id)
        for value in [{'sticker_ids':[self.row.id],'series_id':self.foreign.id},
                      {'sticker_ids':[self.row.id,self.other_row.id],'series_id':None}]:
            with self.assertRaises(Exception):self.invoke(StickerSeriesAssignView,value)
        self.row.refresh_from_db();self.assertEqual(self.row.series_id,self.group.id)

    def test_series_filters_are_applied_before_pagination(self):
        self.row.series=self.group;self.row.save()
        self.assertEqual(sticker_library_payload(self.user,0,1,series_id=self.group.id)['items'][0]['series_id'],self.group.id)
        self.assertEqual(sticker_library_payload(self.user,0,1,series_id=0)['items'],[])
        self.assertEqual(sticker_library_payload(self.other,0,1,series_id=self.group.id)['items'],[])

    def test_deleting_series_preserves_sticker_and_shared_asset(self):
        self.row.series=self.group;self.row.save()
        with patch('utils.auth.decrypt',return_value={'user_id':self.user.id,'space_id':self.user.space_id}):
            request=RequestFactory().delete('/',HTTP_AUTHORIZATION='Bearer fixture')
            StickerSeriesDetailView.as_view()(request,series_id=self.group.id)
        self.row.refresh_from_db();self.assertIsNone(self.row.series_id)
        self.assertTrue(StickerAsset.objects.filter(id=self.row.asset_id).exists())
