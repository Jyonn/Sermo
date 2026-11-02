import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from django.test import TestCase
from django.core.management import call_command
from Activity.models import ActivityCampaign
from Sticker.models import ManagedResource, ResourceCollection
from Sticker.resources import resource_directory


class ActivityResourceImportTests(TestCase):
    def test_import_keeps_exact_bytes_and_live_configuration_and_ids(self):
        campaign=ActivityCampaign.objects.create(key='fixture',title='Fixture',config={'presentation':{'artwork_url':'/activity/fixture/title.svg'},'reward':{'unchanged':True}})
        content=b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'title.svg').write_bytes(content)
            (root/'manifest.json').write_text(json.dumps([{'file':'title.svg','alias':'/activity/fixture/title.svg','activity':'fixture'}]))
            with patch('Sticker.management.commands.import_activity_resources.upload_object') as upload:
                for _ in range(2):call_command('import_activity_resources',str(root/'manifest.json'),output=str(root/'output.json'),update_references=True,verbosity=0)
                key,data,mime=upload.call_args.args
                self.assertTrue(key.startswith('frienden/assets/activities/fixture/'))
                self.assertEqual(data,content);self.assertEqual(mime,'image/svg+xml')
            campaign.refresh_from_db()
            self.assertEqual(ManagedResource.objects.count(),1)
            self.assertEqual(campaign.config['reward'],{'unchanged':True})
            self.assertIn('/resources/assets/',campaign.config['presentation']['artwork_url'])

    def test_directory_cannot_escape_shared_bucket_namespace(self):
        row=ResourceCollection(slug='fixture',category='activity',directory='../other-app')
        with self.assertRaises(Exception):resource_directory(row)
