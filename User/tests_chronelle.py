import unittest

from User.chronelle import calendar_id_from_url


class ChronelleLinkTests(unittest.TestCase):
    calendar = '35c6deab-103d-4f60-9cf9-9f42c812cc5a'

    def test_public_share_and_embed(self):
        for path in ('/', '/embed/'):
            self.assertEqual(calendar_id_from_url(f'https://chronelle.jyonn.space{path}#/share/{self.calendar}'), self.calendar)

    def test_rejects_keys_foreign_hosts_and_invalid_ids(self):
        for url in (
            f'https://chronelle.jyonn.space/#/share/{self.calendar}?key=secret',
            f'https://chronelle.jyonn.space.evil.test/#/share/{self.calendar}',
            f'http://chronelle.jyonn.space/#/share/{self.calendar}',
            f'https://user@chronelle.jyonn.space/#/share/{self.calendar}',
            'https://chronelle.jyonn.space/#/share/not-a-calendar',
            None,
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                calendar_id_from_url(url)
