from unittest.mock import patch

from django.test import TestCase

from Space.models import Space
from User.growth import GROWTH_THRESHOLDS, MUSIC_PROFILE_REWARDS
from User.models import User, UserResourceInventory
from User.validators import UserErrors


class MusicProfileCardTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Music Profiles', slug='music-profiles', email='music@example.com')
        self.user = User.create(space=self.space, name='Listener')

    def equip(self, theme):
        self.user.set_personalization(chat_bubble_style='default', avatar_frame_style='none', profile_card_theme=theme)

    def test_each_card_requires_its_own_level(self):
        for level, reward in MUSIC_PROFILE_REWARDS.items():
            theme = reward['asset_key']
            with self.subTest(theme=theme):
                with patch.object(User, 'effective_growth_level', return_value=level - 1):
                    with self.assertRaises(UserErrors.PERSONALIZATION_NOT_OWNED.__class__):
                        self.equip(theme)
                with patch.object(User, 'effective_growth_level', return_value=level):
                    self.equip(theme)
                self.user.refresh_from_db()
                self.assertEqual(self.user.profile_card_theme, theme)

    def test_returning_users_receive_only_earned_cards_and_keep_their_selection(self):
        with (
            patch.object(User, 'growth_level_cap', return_value=(18, '')),
            patch.object(User, 'reconcile_growth', return_value=GROWTH_THRESHOLDS[14]),
        ):
            first = self.user.json_me()
            second = self.user.json_me()
        expected = {'accusefive', 'landlords-cat', 'she'}
        for payload in (first, second):
            self.assertEqual(payload['profile_card_theme'], 'default')
            self.assertEqual({item['resource_key'] for item in payload['resource_inventory'] if item['resource_type'] == 'profile'}, expected)
        self.assertEqual(UserResourceInventory.objects.filter(user=self.user, resource_type='profile').count(), 3)
        self.equip('she')
        self.user.refresh_from_db()
        self.assertEqual(self.user.profile_card_theme, 'she')
