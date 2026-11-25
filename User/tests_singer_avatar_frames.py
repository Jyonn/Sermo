from unittest.mock import patch

from django.test import TestCase

from AccessPolicy.models import PlatformCapabilityPolicy
from Space.models import Space
from User.growth import GROWTH_THRESHOLDS, SINGER_AVATAR_FRAMES
from User.models import User, UserResourceInventory


class SingerAvatarFrameTests(TestCase):
    def setUp(self):
        space = Space.objects.create(name='Singer Frames', slug='singer-frames', email='frames@example.com')
        self.user = User.create(space=space, name='Listener')

    def equip(self, style):
        self.user.set_personalization(chat_bubble_style='default', avatar_frame_style=style)

    def test_level_gate_and_equipment(self):
        for style, frame in SINGER_AVATAR_FRAMES.items():
            key = f'menu.personalization.frame.use.{style}'
            with self.subTest(frame=style):
                self.assertFalse(self.user.has_capability(key, {
                    'growth_level': frame.level - 1, 'permanent_vip': True,
                }))
                self.assertTrue(self.user.has_capability(key, {'growth_level': frame.level}))
                with patch.object(User, 'effective_growth_level', return_value=frame.level):
                    self.equip(style)
                self.user.refresh_from_db()
                self.assertEqual(self.user.avatar_frame_style, style)

    def test_existing_member_unlocks_earned_frames_on_refresh(self):
        with (
            patch.object(User, 'growth_level_cap', return_value=(18, '')),
            patch.object(User, 'reconcile_growth', return_value=GROWTH_THRESHOLDS[16]),
        ):
            first = self.user.json_me()
            second = self.user.json_me()
        expected = {'joker-xue', 'khalil-fong', 'gem'}
        for payload in (first, second):
            self.assertEqual(payload['avatar_frame_style'], 'none')
            self.assertEqual({item['resource_key'] for item in payload['resource_inventory'] if item['resource_key'] in SINGER_AVATAR_FRAMES}, expected)
        self.assertEqual(UserResourceInventory.objects.filter(user=self.user, resource_key__in=expected).count(), 3)

    def test_platform_can_close_a_singer_frame(self):
        key = 'menu.personalization.frame.use.gem'
        PlatformCapabilityPolicy.objects.create(capability_key=key, enabled=False)
        self.assertFalse(self.user.has_capability(key, {'growth_level': 18}))
