import hashlib
from unittest.mock import patch

from django.test import SimpleTestCase

from User.models import User
from utils.qiniu import build_avatar_display_uri


class AvatarTransparencyTests(SimpleTestCase):
    @patch('utils.qiniu.sign_private_processed_url', side_effect=lambda uri, processing, **kwargs: processing)
    def test_raster_avatar_keeps_alpha_and_does_not_recrop(self, _sign):
        for extension in ('png', 'jpg', 'svg'):
            self.assertEqual(build_avatar_display_uri(f'https://cdn.example/avatar.{extension}'),
                             'imageView2/2/w/400/h/400/q/85/format/png')
        self.assertIn('/w/1024/h/1024/', build_avatar_display_uri('https://cdn.example/a.png', size=8000))

    @patch('utils.qiniu.sign_private_processed_url', side_effect=lambda uri, processing, **kwargs: processing)
    def test_existing_animated_formats_remain_native_except_oauth_raster(self, _sign):
        for extension in ('gif', 'webp'):
            uri = f'https://cdn.example/avatar.{extension}'
            self.assertNotIn('/format/', build_avatar_display_uri(uri))
            self.assertTrue(build_avatar_display_uri(uri, raster=True).endswith('/format/png'))

    def test_custom_avatar_cache_is_invalidated_without_changing_preset_keys(self):
        uri = 'https://cdn.example/avatar.png'
        custom = User(avatar_type='custom', avatar_uri=uri)
        self.assertNotEqual(custom._dictify_avatar_cache_key(), hashlib.sha256(f'custom:{uri}'.encode()).hexdigest()[:24])
        self.assertEqual(custom._dictify_avatar_cache_key(), custom._dictify_avatar_cache_key())
        preset = User(avatar_type='preset', avatar_uri=uri)
        self.assertEqual(preset._dictify_avatar_cache_key(), hashlib.sha256(f'preset:{uri}'.encode()).hexdigest()[:24])
