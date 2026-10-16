import base64
import struct
import unittest

from User.chronelle_branding import application_icon


class ChronelleBrandingTests(unittest.TestCase):
    def test_icon_meets_chronelle_data_url_limits(self):
        value = application_icon()
        prefix, encoded = value.split(',', 1)
        self.assertEqual(prefix, 'data:image/png;base64')
        raw = base64.b64decode(encoded, validate=True)
        self.assertTrue(raw.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertEqual(struct.unpack('>II', raw[16:24]), (180, 180))
        self.assertLessEqual(len(raw), 32768)
        self.assertLessEqual(len(value), 45000)
