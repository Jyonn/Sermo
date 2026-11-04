from datetime import timedelta
import json
from django.test import TestCase
from django.utils import timezone
from Space.models import Space
from User.models import User
from Chat.models import Chat,ChatMember,ChatTypeChoice,ChatMemberStatusChoice
from Message.models import Message,MessageTypeChoice
from TravelMap.models import MapCheckIn,MapChatGrant,MapAccessGrant
from utils import auth
from Friendship.models import Friendship


class FootprintHistoryTests(TestCase):
    def setUp(self):
        self.space=Space.objects.create(name='Maps',slug='history-maps',email='maps@example.com')
        self.me=User.create(self.space,'Owner',language='en')
        self.peer=User.create(self.space,'Peer',language='en')
        Friendship.ensure_locked_friendship(self.me,self.peer)
        self.outsider=User.create(self.space,'Outsider')
        self.chat=Chat.objects.create(space=self.space,chat_type=ChatTypeChoice.GROUP,title='Lab',created_by=self.me)
        for user in [self.me,self.peer]:ChatMember.objects.create(chat=self.chat,user=user,status=ChatMemberStatusChoice.ACTIVE,joined_at=timezone.now())
        MapChatGrant.grant(self.chat,self.me);MapChatGrant.grant(self.chat,self.peer)

    def region(self,user,code='A',days=0):
        item=MapCheckIn.objects.create(user=user,region_code=code,region_name=code,country_code='CHN',country_name='China')
        MapCheckIn.objects.filter(id=item.id).update(checked_at=timezone.now()-timedelta(days=days))
        return item

    def history(self,**params):
        token=auth.get_login_token(self.me)['auth']
        return self.client.get('/maps/history',params,HTTP_AUTHORIZATION='Bearer '+token).json()

    def test_menu_history_is_own_only_latest_first_and_bounded(self):
        for number in range(25):self.region(self.me,str(number),number)
        self.region(self.outsider,'Secret')
        result=self.history()
        self.assertEqual(result['code'],200,result)
        rows=result['body']['items']
        self.assertEqual(len(rows),20)
        self.assertEqual([row['region_name'] for row in rows],[str(number) for number in range(20)])
        self.assertEqual({row['user_id'] for row in rows},{self.me.id})

    def test_selection_cannot_remove_self_or_reveal_unshared_members(self):
        self.region(self.me,'Mine');self.region(self.peer,'Peer');self.region(self.outsider,'Secret')
        MapChatGrant.objects.create(chat=self.chat,owner=self.outsider,active=True)
        result=self.history(chat_id=self.chat.id,selected_ids=str(self.peer.id)+','+str(self.outsider.id))
        self.assertEqual({row['user_id'] for row in result['body']['items']},{self.me.id,self.peer.id})
        result=self.history(chat_id=self.chat.id,selected_ids=str(self.me.id))
        self.assertEqual({row['user_id'] for row in result['body']['items']},{self.me.id})
        MapChatGrant.revoke(self.chat,self.peer)
        self.assertEqual({row['user_id'] for row in self.history(chat_id=self.chat.id)['body']['items']},{self.me.id})
        MapChatGrant.revoke(self.chat,self.me)
        self.assertNotEqual(self.history(chat_id=self.chat.id)['code'],200)

    def test_private_history_requires_access_and_nonmembers_cannot_read_group_history(self):
        self.region(self.peer,'Peer')
        self.assertNotEqual(self.history(user_id=self.peer.id)['code'],200)
        MapAccessGrant.grant(self.peer,self.me)
        self.assertEqual(self.history(user_id=self.peer.id)['body']['items'][0]['user_id'],self.peer.id)
        other=Chat.objects.create(space=self.space,chat_type=ChatTypeChoice.GROUP,created_by=self.peer)
        self.assertNotEqual(self.history(chat_id=other.id)['code'],200)

    def test_repeat_checkin_preserves_first_timestamp_and_one_region(self):
        args=(self.me,'CHN:Test','Test','CHN','China',30,120,20,'test')
        first=MapCheckIn.check_in(*args)
        timestamp=first.checked_at
        second=MapCheckIn.check_in(*args)
        self.assertEqual(second.id,first.id)
        self.assertEqual(second.checked_at,timestamp)
        self.assertEqual(MapCheckIn.objects.filter(user=self.me).count(),1)

    def test_system_references_are_ids_and_text_is_viewer_relative(self):
        direct=Chat.get_or_create_direct(self.me,self.peer)
        message=Message.create_system(direct,self.me,'travel_map_shared')
        self.assertEqual(message.system_message_text(self.me),'Me shared footprints with Peer')
        self.assertEqual(message.system_message_text(self.peer),'Owner shared footprints with Me')
        payload=message._payload_for_type()
        self.assertIn('<@'+str(self.me.id)+'>',payload['reference_text'])
        self.assertIn('<@'+str(self.peer.id)+'>',payload['reference_text'])
        self.assertEqual({item['user_id'] for item in payload['reference_users']},{self.me.id,self.peer.id})
        mute=Message.create_system(self.chat,self.me,'group_member_muted',member_user_id=self.peer.id,member_name=self.peer.name,duration='1h')
        self.assertIn('Me',mute.system_message_text(self.peer))
        self.assertIn('<@'+str(self.peer.id)+'>',mute._payload_for_type()['reference_text'])

    def test_history_requires_authentication(self):
        self.assertNotEqual(self.client.get('/maps/history').json()['code'],200)

    def test_official_notice_uses_actual_actor_not_delivery_account(self):
        message=Message(user_id=999999,type=MessageTypeChoice.OFFICIAL_NOTICE,content=json.dumps({'kind':'official_notice','event':'space_group_created','actor_user_id':self.me.id,'actor_name':self.me.name,'group_title':'Lab'}))
        self.assertTrue(message.system_message_text(self.me).startswith('Me '))
        self.assertTrue(message.system_message_text(self.peer).startswith('Owner '))
