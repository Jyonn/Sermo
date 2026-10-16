import base64
import struct
import unittest

from User.chronelle_branding import APPLICATION_NAME, AUTHORIZATION_PURPOSES, application_icon, authorization_purpose


class ChronelleBrandingTests(unittest.TestCase):
    def test_current_application_name(self):
        self.assertEqual(APPLICATION_NAME, 'Frienden 友间')

    def test_purpose_matches_all_supported_languages(self):
        self.assertEqual(authorization_purpose('zh-CN'), '把你的公开日程展示在个人名片中')
        self.assertEqual(authorization_purpose('zh_TW'), '在個人名片中展示你的公開行程')
        self.assertEqual(authorization_purpose('en'), 'Show your public schedule on your profile card')
        for language in ('zh-CN', 'zh-TW', 'en', 'ja', 'ko', 'es'):
            with self.subTest(language=language):
                purpose = authorization_purpose(language)
                self.assertEqual(purpose, AUTHORIZATION_PURPOSES[language.lower()])
                self.assertLess(len(purpose), 100)
        self.assertEqual(authorization_purpose(None), authorization_purpose('en'))
        self.assertEqual(authorization_purpose('unknown'), authorization_purpose('en'))

    def test_icon_meets_chronelle_data_url_limits(self):
        value = application_icon()
        prefix, encoded = value.split(',', 1)
        self.assertEqual(prefix, 'data:image/png;base64')
        raw = base64.b64decode(encoded, validate=True)
        self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II', raw[16:24]), (180, 180))
        self.assertLessEqual(len(raw), 32768)
        self.assertLessEqual(len(value), 45000)
