import json
from unittest.mock import patch
from django.test import TestCase
from Chat.tests_research import ResearchTests
from Chat.models import ResearchResource, ResearchProject
from Message.models import MediaAsset, MediaResource
from User.models import User
from utils import auth


class ResourceTests(TestCase):
    setUp = ResearchTests.setUp
    create = ResearchTests.create
    call = ResearchTests.call
    member = ResearchTests.member
    update = ResearchTests.update

    def request(self, project, body=None, user=None, task=''):
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + auth.get_login_token(user or self.owner)['auth']}
        path = f"/research/{project['id']}/resources/"
        if body is None:
            return self.client.get(path, {'task_id': task}, **headers)
        with patch('Chat.research_resources.check_user_text'):
            return self.client.post(path, json.dumps(dict(body, task_id=task)), content_type='application/json', **headers)

    def add(self, project, kind='folder', **values):
        return self.request(project, dict(action='add', kind=kind, name='Resource', **values))

    def task(self, project):
        return self.update(project, dict(action='task', title='Task', body='Body', start='2026-10-29', assignees=[self.student.pk])).json()['body']

    def test_two_folder_levels_and_flat_task_scope(self):
        p = self.create(); self.member(p, self.student)
        p = self.task(p); task = p['tasks'][0]['id']
        first = self.add(p).json()['body']['id']
        self.assertEqual(self.add(p).status_code, 409)
        second = self.add(p, parent_id=first).json()['body']['id']
        self.assertEqual(self.add(p, parent_id=second).status_code, 400)
        self.assertEqual(self.add(p, kind='link', parent_id=second, url='https://example.com/paper').status_code, 200)
        self.assertEqual(self.request(p, dict(action='add', kind='folder', name='No'), task=task).status_code, 400)
        self.assertEqual(self.request(p, dict(action='add', kind='link', name='No', parent_id=first, url='https://example.com'), task=task).status_code, 400)
        self.assertEqual(self.request(p, dict(action='add', kind='link', name='Paper', url='https://example.com'), task=task).status_code, 200)
        self.assertEqual(len(self.request(p, task=task).json()['body']['items']), 1)
        self.assertEqual(len(self.request(p).json()['body']['items']), 3)
        self.assertEqual(self.request(p, task='missing').status_code, 400)
        self.assertEqual(self.request(p, dict(action='delete', id=first)).status_code, 409)

    def test_membership_scope_delete_and_archive_enforced(self):
        p = self.create(); self.member(p, self.student)
        row = self.add(p).json()['body']['id']
        self.assertEqual(self.request(p, user=self.other).status_code, 404)
        self.assertEqual(self.request(p, dict(action='delete', id=row), user=self.student).status_code, 403)
        second = self.create()
        self.assertEqual(self.add(second, parent_id=row).status_code, 400)
        self.assertEqual(self.request(second, dict(action='delete', id=row)).status_code, 404)
        student_link = self.request(p, dict(action='add', kind='link', name='Paper', url='https://example.com'), user=self.student).json()['body']
        self.assertEqual(self.request(p, dict(action='delete', id=student_link['id']), user=self.owner).status_code, 200)
        self.update(p, dict(action='settings', status='archived'))
        self.assertFalse(self.request(p).json()['body']['writable'])
        self.assertEqual(self.add(p).status_code, 403)
        self.assertEqual(self.request(p, dict(action='delete', id=row)).status_code, 403)
        self.space.research_enabled = False; self.space.save()
        self.assertEqual(self.request(p).status_code, 404)

    def test_link_validation_and_task_archive(self):
        p = self.create(); self.member(p, self.student)
        for url in ('javascript:alert(1)', 'data:text/html,test', 'https://a:b@example.com', 'https://'):
            self.assertEqual(self.add(p, kind='link', url=url).status_code, 400)
        p = self.task(p); task = p['tasks'][0]['id']
        row = self.request(p, dict(action='add', kind='link', name='Paper', url='https://example.com'), task=task).json()['body']
        self.assertEqual(self.request(p, dict(action='open', id=row['id'])).status_code, 404)
        self.update(p, dict(action='task_status', task_id=task, status='archived'))
        self.assertFalse(self.request(p, task=task).json()['body']['writable'])
        self.assertEqual(self.request(p, dict(action='add', kind='link', name='Paper', url='https://example.com'), task=task).status_code, 403)
        self.assertEqual(self.request(p, dict(action='open', id=row['id']), task=task).json()['body']['url'], 'https://example.com')

    def test_project_cleanup_can_collect_nested_directories(self):
        p = self.create()
        first = self.add(p).json()['body']['id']
        self.add(p, parent_id=first)
        ResearchProject.objects.get(pk=p['id']).delete()
        self.assertFalse(ResearchResource.objects.filter(project_id=p['id']).exists())

    def test_file_ownership_private_download_and_reference_retention(self):
        p = self.create(); self.member(p, self.student)
        asset = MediaAsset.objects.create(source_key='sermo/messages/file/test.pdf', source_uri='https://example.com/test.pdf', kind=MediaAsset.KIND_FILE, status=MediaAsset.STATUS_READY, file_size=1024)
        media = MediaResource.acquire(self.owner, asset, MediaAsset.KIND_FILE, 'test.pdf')
        body = dict(action='add', kind='file', name='Test', resource_id=media.pk)
        self.assertEqual(self.request(p, body, user=self.student).status_code, 400)
        row = self.request(p, body).json()['body']
        self.assertEqual(row['size'], 1024)
        self.assertIsNone(row['url'])
        self.assertEqual(media.recalculate_reference_count(), 1)
        media.library_active = False; media.save()
        with patch('Chat.research_resources.sign_private_download_url', return_value='https://signed.example/file'):
            self.assertEqual(self.request(p, dict(action='open', id=row['id']), user=self.student).json()['body']['url'], 'https://signed.example/file')
            self.assertEqual(self.request(p, dict(action='open', id=row['id']), user=self.other).status_code, 404)
        self.assertEqual(self.request(p, dict(action='delete', id=row['id'])).status_code, 200)
        self.assertEqual(media.recalculate_reference_count(), 0)
        self.assertTrue(MediaResource.objects.filter(pk=media.pk).exists())
        media.refresh_from_db()
        self.assertFalse(media.library_active)
