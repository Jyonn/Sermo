from django.test import SimpleTestCase, TestCase
from django.core.management import call_command, CommandError
from io import StringIO
from Activity.models import ActivityCampaign
from Activity.presentation import presentation


class PresentationTests(SimpleTestCase):
    def test_optional(self):
        self.assertIsNone(presentation({}))

    def test_public_metadata_only(self):
        self.assertEqual(presentation({'presentation': {'palette': 'rose', 'artwork_url': '/activity/a.png', 'secret': 'hidden'}}), {'palette': 'rose', 'artwork_url': '/activity/a.png'})

    def test_reject_private_or_unsafe_urls(self):
        for value in ('javascript:alert(1)', '//evil.test/a', 'https://user:pass@example.test/a', 'http://example.test/a', '/\\evil.test/a'):
            self.assertIsNone(presentation({'presentation': {'artwork_url': value}}))


class ConfigurePresentationTests(TestCase):
    def test_preserves_campaign_rules(self):
        campaign = ActivityCampaign.objects.create(key='custom-test', title='Test', config={'mode': 'bgm_collection', 'tracks': [{'key': 'keep'}]}, duration_seconds=2592000)
        call_command('configure_activity_presentation', campaign.key, palette='sage', artwork_url='/a.png', title_art_url='/b.png', artwork_layout='image', stdout=StringIO())
        campaign.refresh_from_db()
        self.assertEqual(campaign.config['tracks'], [{'key': 'keep'}])
        self.assertEqual(campaign.config['mode'], 'bgm_collection')
        self.assertEqual(campaign.duration_seconds, 2592000)
        self.assertEqual(campaign.config['presentation']['artwork_url'], '/a.png')

    def test_rejects_unsafe_before_writing(self):
        with self.assertRaises(CommandError):
            call_command('configure_activity_presentation', 'unused', palette='sage', artwork_url='javascript:bad', title_art_url='/b.png', artwork_layout='image')
