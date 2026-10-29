from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch
from django.apps import apps
from django.test import TestCase
from Chat import tests_research as fixtures
from Chat.models import ChatUserPreference
from User.models import NotificationEvent, NotificationTopicPreference as Pref, NotificationDelivery, NotificationPreference


class NotificationCategoryTests(TestCase):
    setUp=fixtures.ResearchTests.setUp
    call=fixtures.ResearchTests.call
    create=fixtures.ResearchTests.create
    member=fixtures.ResearchTests.member
    update=fixtures.ResearchTests.update

    def event(self,kind=None,category=None):
        return NotificationEvent.objects.create(user=self.student,actor=self.owner,space=self.space,event_type=4,payload={'kind':kind,'notification_category':category})

    def test_square_migration_uses_friend_settings_and_keeps_new_categories_independent(self):
        Pref.objects.create(user=self.student,channel=1,topic=2,audience=1,enabled=False)
        Pref.objects.create(user=self.student,channel=1,topic=2,audience=2,enabled=True)
        Pref.objects.create(user=self.student,channel=1,topic=1,audience=0,enabled=False)
        import_module('User.migrations.0091_notification_categories').unify(apps,None)
        self.assertFalse(Pref.effective_enabled(self.student,1,2))
        self.assertFalse(Pref.effective_enabled(self.student,1,7))
        Pref.set_enabled(self.student,1,1,0,True)
        self.assertFalse(Pref.effective_enabled(self.student,1,7))
        self.assertTrue(Pref.effective_enabled(self.student,1,1))
        Pref.set_enabled(self.student,1,2,2,True)
        self.assertTrue(Pref.effective_enabled(self.student,1,2))
        with self.assertNumQueries(1):matrix=Pref.matrix(self.student)
        self.assertEqual(len(matrix),44);self.assertTrue(all(row['audience']==0 for row in matrix))

    def test_every_channel_uses_the_same_topic_policy(self):
        for kind,category,topic in [('research_task','research',8),('research_progress','research',9),('research_update','research',10),(None,'submission',7),('friend_statement',None,11),('peer_online',None,6)]:
            event=self.event(kind,category);self.assertEqual(event.topic(),topic)
            for channel in (0,1,3):
                Pref.set_enabled(self.student,channel,topic,0,False)
                self.assertFalse(Pref.is_enabled_for_event(event,channel))
                Pref.set_enabled(self.student,channel,topic,0,True)
                self.assertEqual(Pref.is_enabled_for_event(event,channel),topic!=6 or channel==3)

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_research_assignment_progress_and_conflict_do_not_duplicate_events(self,enqueue):
        p=self.create();self.member(p,self.student)
        p=self.update(p,{'action':'task','title':'Task','body':'Details','start':'2026-10-29','assignees':[self.student.pk]}).json()['body']
        event=NotificationEvent.objects.get(user=self.student,payload__kind='research_task')
        self.assertEqual(event.topic(),8);self.assertEqual(event.payload['project_id'],p['id'])
        self.assertNotIn('Details',str(event.payload))
        body={'action':'progress','task_id':p['tasks'][0]['id'],'body':'Private progress text'}
        self.assertEqual(self.update(p,body,self.student).status_code,200)
        progress=NotificationEvent.objects.get(user=self.owner,payload__kind='research_progress')
        self.assertEqual(progress.topic(),9)
        self.assertNotIn('Private progress text',str(progress.payload))
        self.assertEqual(self.update(p,body,self.student).status_code,409)
        self.assertEqual(NotificationEvent.objects.filter(payload__kind__in=['research_task','research_progress']).count(),2)
        delivery=NotificationDelivery(event=progress)
        self.assertIn('/app/research?project=',delivery._email_event_action_url())
        self.assertIn('&task=',delivery._email_event_action_url())

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_muted_project_and_self_assignments_do_not_notify(self,enqueue):
        p=self.create();self.member(p,self.student)
        ChatUserPreference.objects.create(user=self.student,chat_id=p['chat_id'],notifications_muted=True)
        self.update(p,{'action':'task','title':'Task','body':'Details','start':'2026-10-29','assignees':[self.owner.pk,self.student.pk]})
        self.assertFalse(NotificationEvent.objects.filter(payload__kind='research_task').exists())

    def test_submission_notices_and_research_invites_are_not_classified_as_regular_chat(self):
        from Message.models import MessageTypeChoice
        from Chat.models import Chat
        from Friendship.models import Friendship
        Friendship.ensure_locked_friendship(self.owner,self.student)
        chat=Chat.get_or_create_direct(self.owner,self.student)
        for kind,content,topic in [(MessageTypeChoice.OFFICIAL_NOTICE,'{"event":"submission_ready"}',7),(MessageTypeChoice.SUBMISSION_INVITE,'{"research":true}',10),(MessageTypeChoice.SUBMISSION_INVITE,'{}',7)]:
            message=SimpleNamespace(type=kind,chat=chat,chat_id=chat.pk,id=1,content=content,preview_text=lambda:'Preview')
            events=NotificationEvent.emit_message_notifications(message,self.owner,enqueue=False)
            self.assertEqual(events[0].topic(),topic)

    def test_old_queued_submission_notices_follow_the_new_submission_switch(self):
        from Message.models import Message,MessageTypeChoice
        from Chat.models import Chat
        chat=Chat.objects.create(space=self.space,created_by=self.owner,chat_type=0)
        message=Message.objects.create(chat=chat,user=self.owner,type=MessageTypeChoice.OFFICIAL_NOTICE,content='{"event":"submission_ready"}')
        event=NotificationEvent.objects.create(user=self.student,actor=self.owner,space=self.space,event_type=1,payload={'message_id':message.pk})
        Pref.set_enabled(self.student,1,7,0,False)
        self.assertEqual(event.topic(),7)
        self.assertFalse(Pref.is_enabled_for_event(event,1))
