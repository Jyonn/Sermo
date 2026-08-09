from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from Space.models import Space
from User.models import (
    NotificationEvent,
    NotificationEventTypeChoice,
    User,
    account_switch_phone_variants,
    extract_emojis,
    normalize_bark_endpoint,
)
from TravelMap.models import MapCheckIn
from User.validators import UserErrors


class UserPresentationTests(SimpleTestCase):
    def test_preset_avatar_uses_webapp_png_collection(self):
        self.assertEqual(
            User.build_preset_avatar_uri(36),
            'https://sermo.jyonn.space/assets/avatars/v2/36.png',
        )

    def test_plaza_greeting_has_language_aware_default(self):
        self.assertEqual(User(language='zh-CN').display_plaza_greeting(), '嗨，认识一下？')
        self.assertEqual(User(language='en').display_plaza_greeting(), 'Hi, nice to meet you.')


class CityBubbleUnlockTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='City Collection', slug='city-collection', email='city@example.com')
        self.user = User.create(space=self.space, name='Traveler')

    def test_city_bubble_requires_matching_checkin(self):
        with self.assertRaises(UserErrors.CITY_BUBBLE_CHECKIN_REQUIRED.__class__):
            self.user.set_personalization(chat_bubble_style='city-jdz', avatar_frame_style='none')

    def test_jiangxi_checkin_unlocks_jingdezhen_bubble(self):
        MapCheckIn.objects.create(
            user=self.user,
            region_code='CN-JX',
            region_name='Jiangxi Province',
            country_code='CHN',
            country_name='China',
        )
        self.user.set_personalization(chat_bubble_style='city-jdz', avatar_frame_style='none')
        self.user.refresh_from_db()
        self.assertEqual(self.user.chat_bubble_style, 'city-jdz')
        self.assertIn('city-jdz', self.user.json_me()['city_bubble_styles'])

    def test_custom_plaza_greeting_takes_precedence(self):
        self.assertEqual(
            User(language='zh-CN', plaza_greeting='  今天也要尽兴  ').display_plaza_greeting(),
            '今天也要尽兴',
        )

    def test_explicit_language_preference_is_not_overwritten_by_login_language(self):
        user = User(language='zh-CN', language_preference='zh-CN')
        user.set_language('en', save=False)
        self.assertEqual(user.language, 'zh-CN')

    def test_system_language_preference_tracks_device_language(self):
        user = User(language='en', language_preference='system')
        user.set_language('zh-CN', save=False)
        self.assertEqual(user.language, 'zh-CN')


class UserHeartbeatTests(TestCase):
    def test_regular_model_save_does_not_refresh_heartbeat(self):
        space = Space.objects.create(name='Presence Space', slug='presence-space', email='presence@example.com')
        user = User.create(space=space, name='Presence Test')
        previous = timezone.now() - timedelta(minutes=10)
        User.objects.filter(id=user.id).update(last_heartbeat=previous)
        user.refresh_from_db()

        user.welcome_message = 'hello'
        user.save()
        user.refresh_from_db()

        self.assertEqual(user.last_heartbeat, previous)

class AccountSwitchPhoneNormalizationTests(SimpleTestCase):
    def test_mainland_phone_variants_include_country_code(self):
        self.assertEqual(
            account_switch_phone_variants('13800000000'),
            {'13800000000', '+8613800000000'},
        )
        self.assertEqual(
            account_switch_phone_variants('+8613800000000'),
            {'13800000000', '+8613800000000'},
        )

    def test_other_international_numbers_are_not_rewritten(self):
        self.assertEqual(account_switch_phone_variants('+6591234567'), {'+6591234567'})


class EmojiExtractionTests(SimpleTestCase):
    def test_extracts_compound_and_repeated_emoji(self):
        self.assertEqual(
            extract_emojis('好耶 👍👍🏽 家庭 👨‍👩‍👧‍👦'),
            ['👍', '👍🏽', '👨‍👩‍👧‍👦'],
        )


class BarkEndpointNormalizationTests(SimpleTestCase):
    def test_copied_push_url_discards_sample_message(self):
        self.assertEqual(
            normalize_bark_endpoint('https://api.day.app/p7eciGfLv6oNuwQktkLE5Q/这里改成你自己的推送内容'),
            'https://api.day.app/p7eciGfLv6oNuwQktkLE5Q',
        )

    def test_endpoint_without_message_is_unchanged(self):
        self.assertEqual(
            normalize_bark_endpoint('https://api.day.app/p7eciGfLv6oNuwQktkLE5Q'),
            'https://api.day.app/p7eciGfLv6oNuwQktkLE5Q',
        )


class NotificationEventDeliveryTests(SimpleTestCase):
    def test_delivery_uses_recipient_language(self):
        event = NotificationEvent(
            user=User(id=1, language='zh-CN'),
            event_type=NotificationEventTypeChoice.DIRECT_MESSAGE,
            payload={},
        )

        title, body = event.render_delivery_message()

        self.assertEqual(title, '新私聊消息')
        self.assertEqual(body, '你收到了一条新的私聊消息。')

    def test_hidden_message_uses_custom_title_and_body(self):
        event = NotificationEvent(
            event_type=NotificationEventTypeChoice.DIRECT_MESSAGE,
            payload={'content': 'secret'},
        )

        title, body = event.render_delivery_message(
            hide_message_content=True,
            hidden_direct_message_title='自定义标题',
            hidden_direct_message_text='自定义内容',
        )

        self.assertEqual(title, '自定义标题')
        self.assertEqual(body, '自定义内容')

    def test_online_message_uses_custom_title_and_body(self):
        event = NotificationEvent(
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload={'kind': 'peer_online'},
        )

        title, body = event.render_delivery_message(
            friend_online_message_title='好友来了',
            friend_online_message_text='快去聊天',
        )

        self.assertEqual(title, '好友来了')
        self.assertEqual(body, '快去聊天')

    @patch('User.models.threading.Thread')
    @patch('User.models.transaction.on_commit')
    def test_delivery_thread_starts_only_after_commit(self, on_commit, thread):
        NotificationEvent._enqueue_deliveries_after_commit([12, 34])

        thread.assert_not_called()
        callback = on_commit.call_args.args[0]
        callback()

        thread.assert_called_once_with(
            target=NotificationEvent._enqueue_deliveries,
            args=((12, 34),),
            daemon=True,
            name='notification-delivery',
        )
        thread.return_value.start.assert_called_once_with()
