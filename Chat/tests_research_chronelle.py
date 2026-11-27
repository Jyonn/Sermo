import uuid
from unittest.mock import patch

from django.test import TestCase

from Chat.models import Chat, ChatMember, ResearchProject
from Space.models import Space
from User.chronelle_account import BindingFailure
from User.models import User
from utils.auth import get_login_token


class ResearchChronelleTests(TestCase):
    def setUp(self):
        space = Space.objects.create(slug='calendar-workbench-test', name='Calendar test',
                                     email='calendar-workbench@example.test', research_enabled=True)
        self.user = User.create(space, 'Member', verified=True)
        self.stranger = User.create(space, 'Outsider', verified=True)
        chat = Chat.objects.create(space=space, created_by=self.user, chat_type=1, purpose=2, title='Research')
        ChatMember.objects.create(chat=chat, user=self.user, role=1, status=1)
        self.project = ResearchProject.objects.create(chat=chat, start='2026-11-01')
        self.prefix = f'/research/{self.project.pk}/chronelle/'
        self.headers = self.headers_for(self.user)
        credential = patch('Chat.research_chronelle.credentials', return_value={'access_token': 'server-fixture'})
        self.credentials = credential.start()
        self.addCleanup(credential.stop)

    @staticmethod
    def headers_for(user):
        return {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(user)['auth'],
                'HTTP_ORIGIN': 'https://frienden.6-79.cn'}

    @patch('Chat.research_chronelle.remote', return_value=[])
    def test_own_events_are_private_and_use_the_correct_origin(self, remote):
        query = {'start': '2026-11-01T00:00:00+08:00', 'end': '2026-11-07T00:00:00+08:00'}
        result = self.client.get(self.prefix + 'events/', query, **self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['body'], [])
        self.assertIn('no-store', result['Cache-Control'])
        remote.assert_called_once_with('https://chronelle.6-79.cn', '/api/oauth/calendar/events/',
                                       method='GET', params=query, body=None, key=None, token='server-fixture')
        self.assertEqual(self.credentials.call_args.args[0].pk, self.user.pk)
        self.assertEqual(self.client.get(self.prefix + 'events/', query, **self.headers_for(self.stranger)).status_code, 404)
        self.assertEqual(self.client.get(self.prefix + 'events/', {**query, 'subject': 'any'}, **self.headers).status_code, 400)
        self.assertEqual(self.client.get(self.prefix + 'grants/', **self.headers).status_code, 404)
        self.assertEqual(remote.call_count, 1)

    @patch('Chat.research_chronelle.remote', return_value={})
    def test_recurring_update_keeps_revision_and_idempotency(self, remote):
        uid, key = str(uuid.uuid4()), str(uuid.uuid4())
        data = {'scope': 'future', 'occurrence_start': '2026-11-05', 'revision': 'returned-by-chronelle',
                'recurrence': {'frequency': 'WEEKLY', 'weekdays': ['TH'], 'ending': 'count', 'count': 4}}
        payload = {'request_key': key, 'data': data}
        result = self.client.patch(self.prefix + f'event/{uid}/', payload, content_type='application/json', **self.headers)
        self.assertEqual(result.status_code, 200, result.content)
        self.assertEqual(remote.call_args.args[1], f'/api/oauth/calendar/event/{uid}/')
        self.assertEqual(remote.call_args.kwargs['key'], key)
        self.assertEqual(remote.call_args.kwargs['body'], data)
        self.project.status = 'archived'
        self.project.save(update_fields=['status'])
        result = self.client.delete(self.prefix + f'event/{uid}/', payload, content_type='application/json', **self.headers)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(remote.call_count, 1)

    @patch('Chat.research_chronelle.remote')
    def test_inbox_conflicts_and_private_share_keys(self, remote):
        uid = str(uuid.uuid4())
        remote.return_value = {'items': [{'id': uid, 'participants': [{'name': 'Member', 'share': 'private-key'}]}]}
        result = self.client.get(self.prefix + 'invitations/', **self.headers)
        self.assertEqual(result.json()['body']['items'][0]['participants'], [{'name': 'Member'}])
        self.assertNotIn('server-fixture', result.content.decode())
        path = self.prefix + f'respond/{uid}/'
        remote.side_effect = BindingFailure('calendar_revision_conflict', 409)
        result = self.client.post(path, {'request_key': 'respond-test', 'data': {'action': 'decline', 'proposal_version': 2}},
                                  content_type='application/json', **self.headers)
        self.assertEqual(result.status_code, 409)
        self.assertEqual(result.json()['message'], 'calendar_revision_conflict')
        invalid = self.client.post(path, {'request_key': 'invalid key', 'data': {}}, content_type='application/json', **self.headers)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(remote.call_count, 2)
