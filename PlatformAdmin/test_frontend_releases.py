import json
from unittest.mock import patch
from django.test import TestCase, RequestFactory
from PlatformAdmin.frontend_releases import publish_frontend_release, release_catalog, FrontendReleasePublishView
from PlatformAdmin.models import FrontendRelease, FrontendDeployment, PlatformAuditLog


def notes(version='2026.11.03.1'):
    return dict(id=version,publishedAt='2026-11-03',locales={'zh-CN':{'title':'更新','items':['改进体验']},'en':{'title':'Update','items':['Improved experience']}})


class FrontendReleaseTests(TestCase):
    origin='https://frienden.jyonn.space'

    def test_public_get_is_frontend_only_and_does_not_probe_sites(self):
        publish_frontend_release(self.origin,notes(),'fixture')
        with patch('requests.get',side_effect=AssertionError('No site probes')):
            response=self.client.get('/frontend/releases')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response['Cache-Control'],'no-store')
        body=response.json()['body'];self.assertEqual(body['kind'],'frontend');self.assertEqual(len(body['sources']),4)
        self.assertEqual(body['sources'][0]['releaseId'],'2026.11.03.1')
        self.assertEqual(body['sources'][1]['status'],'error')

    def test_publish_requires_platform_admin_and_frontend_payload(self):
        value={'kind':'frontend','origin':self.origin,'release':notes()}
        self.assertNotEqual(self.client.put('/platform-admin/frontend/releases',data=json.dumps(value),content_type='application/json').status_code,200)
        request=RequestFactory().put('/',data=json.dumps(value),content_type='application/json',HTTP_AUTHORIZATION='Bearer fixture')
        with patch('utils.auth.decrypt',return_value={'email':'admin@example.com'}):
            result=FrontendReleasePublishView.as_view()(request)
        self.assertEqual(result['id'],'2026.11.03.1')
        for value in [{'kind':'backend','origin':self.origin,'release':notes()},{'kind':'frontend','origin':'https://evil.test','release':notes()}]:
            request=RequestFactory().put('/',data=json.dumps(value),content_type='application/json',HTTP_AUTHORIZATION='Bearer fixture')
            with patch('utils.auth.decrypt',return_value={'email':'admin@example.com'}):self.assertEqual(FrontendReleasePublishView.as_view()(request).status_code,400)
        self.assertEqual(FrontendRelease.objects.count(),1)

    def test_idempotent_immutable_notes_and_no_site_downgrade(self):
        publish_frontend_release(self.origin,notes(),'fixture')
        publish_frontend_release(self.origin,notes(),'fixture')
        self.assertEqual(PlatformAuditLog.objects.filter(action='frontend.release.publish').count(),1)
        changed=notes();changed['locales']['en']['title']='Changed'
        with self.assertRaises(ValueError):publish_frontend_release(self.origin,changed,'fixture')
        publish_frontend_release(self.origin,notes('2026.11.03.2'),'fixture')
        with self.assertRaises(ValueError):publish_frontend_release(self.origin,notes(),'fixture')
        self.assertEqual(FrontendDeployment.objects.get(origin=self.origin).release.version,'2026.11.03.2')
        publish_frontend_release('https://sermo.6-79.cn',notes(),'fixture')
        self.assertEqual(release_catalog()['sources'][-1]['releaseId'],'2026.11.03.1')

    def test_invalid_dates_notes_and_backend_metadata_are_rejected(self):
        invalid=[]
        bad=notes();bad['backend_commit']='abc';invalid.append(bad)
        bad=notes();bad['publishedAt']='2026-11-04';invalid.append(bad)
        bad=notes();bad['locales'].pop('en');invalid.append(bad)
        bad=notes();bad['locales']['en']['items']=[];invalid.append(bad)
        bad=notes();bad['id']='2026.99.03.1';invalid.append(bad)
        for value in invalid:
            with self.assertRaises((ValueError,TypeError)):publish_frontend_release(self.origin,value,'fixture')
        self.assertEqual(FrontendRelease.objects.count(),0)
