from unittest.mock import patch

from django.test import TestCase

from AccessPolicy.models import PlatformCapabilityPolicy
from Space.models import Space
from User.growth import GROWTH_THRESHOLDS, SINGER_AVATAR_FRAMES
from User.models import User, UserFeatureDiscovery, UserResourceInventory
from utils.auth import get_login_token


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

    def test_discovery_marks_only_unlocked_appearances_and_persists_per_account(self):
        url = '/users/me/feature-discoveries'
        other = User.create(space=self.user.space, name='Another listener')
        headers = {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(self.user)['auth']}
        other_headers = {'HTTP_AUTHORIZATION': 'Bearer ' + get_login_token(other)['auth']}

        def styles(account):
            response = self.client.get(url, **account)
            self.assertEqual(response.status_code, 200)
            return {item['reward_id']: item['is_new'] for item in response.json()['body']['features']
                    if item['capability_key'].startswith('appearance.')}

        unlocked = {'frame.joker-xue', 'frame.khalil-fong', 'profile.accusefive',
                    'profile.landlords-cat', 'profile.she', 'profile.mayday'}
        with patch.object(User, 'effective_growth_level', return_value=16):
            self.assertEqual(styles(headers), dict.fromkeys(unlocked, True))
            locked = self.client.post(url, {'reward_id': 'frame.gem'}, content_type='application/json', **headers)
            self.assertEqual(locked.status_code, 400)
            for reward_id in ('frame.joker-xue', 'profile.mayday', 'frame.joker-xue'):
                response = self.client.post(url, {'reward_id': reward_id}, content_type='application/json', **headers)
                self.assertEqual(response.status_code, 200)
            self.assertEqual(styles(headers), dict.fromkeys(unlocked, True) | {
                'frame.joker-xue': False, 'profile.mayday': False,
            })
            self.assertEqual(styles(other_headers), dict.fromkeys(unlocked, True))
        with patch.object(User, 'effective_growth_level', return_value=17):
            self.assertTrue(styles(headers)['frame.gem'])
        self.assertEqual(UserFeatureDiscovery.objects.filter(user=self.user).count(), 2)
