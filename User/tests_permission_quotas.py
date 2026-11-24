from datetime import datetime, timezone as utc
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from AccessPolicy.models import SpaceCapabilityPolicy
from Chat import voice
from Chat.voice_models import VoiceIdentity, VoiceWeeklyQuota
from Message import social_quota
from Message.models import MediaResource, SocialParseUsage
from Space.models import Space
from Square.models import SquareMute
from User.models import User
from utils import auth


URL = '/users/me/permissions-quotas'


@override_settings(VOICE_CALLS_ENABLED=True, VOICE_LIVEKIT_URL='test', VOICE_LIVEKIT_API_KEY='test',
                   VOICE_LIVEKIT_API_SECRET='test')
@patch.dict('os.environ', {'JUSTONEAPI_TOKEN': 'fixture-only'})
class PersonalPermissionQuotaTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Quota space', slug='quota-space', email='quota@example.test',
            admin_phone_verified_at=timezone.now(), chat_enabled=True, group_square_enabled=True,
            square_free_post_enabled=True, square_explore_enabled=True, research_enabled=True,
        )
        self.user = User.create(self.space, 'Viewer', verified=True)
        self.user.is_permanent_vip = True
        self.user.phone = '13800000000'
        self.user.phone_verified_at = timezone.now()
        self.user.save()

    def read(self, user=None, path=URL):
        token = auth.get_login_token(user or self.user)['auth']
        response = self.client.get(path, HTTP_AUTHORIZATION=f'Bearer {token}')
        self.assertEqual(response.status_code, 200, response.json())
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        return response.json()['body']

    def test_authenticated_read_reports_current_user_without_consuming_quota(self):
        self.assertNotEqual(self.client.get(URL).status_code, 200)
        other = User.create(self.space, 'Other', verified=True)
        identity = VoiceIdentity.objects.create(digest=voice.identity_key(self.user))
        VoiceWeeklyQuota.objects.create(identity=identity, week=voice.week_at(timezone.now()),
                                         used_seconds=92, reserved_seconds=80)
        SocialParseUsage.objects.create(user=self.user, week=social_quota.week_start(), count=3)

        result = self.read(path=f'{URL}?user_id={other.id}')
        self.assertEqual(result['account']['user_id'], self.user.id)
        self.assertEqual(result['account']['space_id'], self.space.id)
        self.assertEqual(result['chat']['voice']['limit'], 300)
        self.assertEqual(result['chat']['voice']['usage'], dict(used=92, reserved=80, remaining=128))
        self.assertEqual(result['social']['remaining'], 4)
        self.assertEqual(result['chat']['storage'], MediaResource.quota_for(self.user))
        self.assertEqual(SocialParseUsage.objects.get(user=self.user).count, 3)
        self.assertEqual(VoiceWeeklyQuota.objects.get(identity=identity).used_seconds, 92)
        other_result = self.read(other)
        self.assertIsNone(other_result['chat']['voice']['usage'])
        self.assertEqual(other_result['social']['reason'], 'vip_required')
        self.assertEqual(other_result['social']['remaining'], 0)

    def test_space_and_account_policies_are_reported(self):
        self.space.square_free_post_enabled = False
        self.space.save(update_fields=['square_free_post_enabled'])
        self.user.phone_verified_at = None
        self.user.save(update_fields=['phone_verified_at'])
        SpaceCapabilityPolicy.objects.create(space=self.space, capability_key='chat.message.send.file',
                                             denial={'field': 'verified', 'op': 'eq', 'value': True})
        result = self.read()
        self.assertEqual(result['chat']['voice']['reason'], 'phone_required')
        self.assertEqual(result['chat']['send']['file']['reason'], 'restricted')
        self.assertEqual(result['square']['publish']['reason'], 'publishing_disabled')
        self.assertTrue(result['square']['comment']['allowed'])
        SquareMute.objects.create(space=self.space, user=self.user, active=True)
        self.assertEqual(self.read()['square']['reply']['reason'], 'muted')

    def test_beijing_week_resets_without_carrying_over_usage(self):
        sunday = datetime(2026, 11, 22, 15, 59, tzinfo=utc.utc)
        monday = datetime(2026, 11, 22, 16, tzinfo=utc.utc)
        with patch('django.utils.timezone.now', return_value=sunday):
            SocialParseUsage.objects.create(user=self.user, week=social_quota.week_start(), count=7)
            before = social_quota.quota_for(self.user)
        with patch('django.utils.timezone.now', return_value=monday):
            after = social_quota.quota_for(self.user)
        self.assertEqual((before['reason'], before['remaining'], before['reset_at']), ('exhausted', 0, monday.timestamp()))
        self.assertEqual((after['reason'], after['remaining']), (None, 7))
