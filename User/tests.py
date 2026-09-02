from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from Space.models import Space
from User.models import (
    NotificationEvent,
    NotificationEventTypeChoice,
    NotificationDelivery,
    WebPushSubscription,
    User,
    UserResourceInventory,
    UserContactVerificationCode,
    UserNotificationChoice,
    UserStateEvent,
    UserStateEventKindChoice,
    account_switch_phone_variants,
    extract_emojis,
    normalize_bark_endpoint,
)
from TravelMap.models import MapCheckIn
from User.validators import UserErrors
from utils.notificator_integration import send_verification_mail
from utils import auth


class UserPresentationTests(SimpleTestCase):
    def test_preset_avatar_uses_webapp_png_collection(self):
        self.assertEqual(
            User.build_preset_avatar_uri(36),
            'https://sermo.jyonn.space/assets/avatars/v2/36.png',
        )

    def test_plaza_greeting_has_language_aware_default(self):
        self.assertEqual(User(language='zh-CN').display_plaza_greeting(), '嗨，认识一下？')
        self.assertEqual(User(language='en').display_plaza_greeting(), 'Hi, nice to meet you.')

    def test_avatar_cache_key_is_stable_and_changes_with_avatar(self):
        user = User(avatar_type='custom', avatar_uri='https://cdn.example.com/avatar/a.png')
        first = user._dictify_avatar_cache_key()
        self.assertEqual(first, user._dictify_avatar_cache_key())

        user.avatar_uri = 'https://cdn.example.com/avatar/b.png'
        self.assertNotEqual(first, user._dictify_avatar_cache_key())


class UserStateEventSyncTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='State Events', slug='state-events', email='state@example.com')
        self.user = User.create(space=self.space, name='State User', verified=True)
        self.other = User.create(space=self.space, name='Other User', verified=True)

    def authorization(self, user):
        return {'HTTP_AUTHORIZATION': f"Bearer {auth.get_login_token(user)['auth']}"}

    def test_sync_is_user_scoped_and_cursor_based(self):
        first = UserStateEvent.emit(self.user, UserStateEventKindChoice.CHATS_CHANGED, 12)
        second = UserStateEvent.emit(self.user, UserStateEventKindChoice.FRIENDS_CHANGED, 34)
        UserStateEvent.emit(self.other, UserStateEventKindChoice.FRIEND_REQUESTS_CHANGED, 56)

        response = self.client.get(
            f'/users/me/state-events?after={first.id}&limit=10',
            **self.authorization(self.user),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()['body']
        self.assertEqual([event['event_id'] for event in payload['events']], [second.id])
        self.assertEqual(payload['next_after'], second.id)
        self.assertFalse(payload['has_more'])

    def test_baseline_starts_fresh_device_after_existing_events(self):
        event = UserStateEvent.emit(self.user, UserStateEventKindChoice.CHATS_CHANGED, 12)
        UserStateEvent.emit(self.other, UserStateEventKindChoice.CHATS_CHANGED, 13)

        response = self.client.get(
            '/users/me/state-events/baseline',
            **self.authorization(self.user),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['next_after'], event.id)


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

    def test_baxian_bubble_requires_activity_inventory(self):
        with self.assertRaises(UserErrors.PERSONALIZATION_NOT_OWNED.__class__):
            self.user.set_personalization(chat_bubble_style='baxian-lv', avatar_frame_style='none')

        UserResourceInventory.grant_activity_resource(
            self.user,
            resource_type='bubble',
            reward_id='activity.baxian.lv',
            resource_key='baxian-lv',
            activity_key='baxian-2026',
        )
        self.user.set_personalization(chat_bubble_style='baxian-lv', avatar_frame_style='none')
        self.user.refresh_from_db()
        self.assertEqual(self.user.chat_bubble_style, 'baxian-lv')

    def test_self_avatar_preference_is_persisted_in_private_profile(self):
        self.user.set_personalization(
            chat_bubble_style='default',
            avatar_frame_style='none',
            show_self_avatar=True,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.show_self_avatar)
        self.assertTrue(self.user.json_me()['show_self_avatar'])
        self.assertNotIn('show_self_avatar', self.user.tiny_json())

    def test_profile_card_theme_requires_level_or_vip(self):
        with self.assertRaises(UserErrors.PERSONALIZATION_NOT_OWNED.__class__):
            self.user.set_personalization(
                chat_bubble_style='default', avatar_frame_style='none', profile_card_theme='level-12',
            )
        with patch.object(User, 'effective_growth_level', return_value=12):
            self.user.set_personalization(
                chat_bubble_style='default', avatar_frame_style='none', profile_card_theme='level-12',
            )
        self.assertEqual(self.user.profile_card_theme, 'level-12')

        with self.assertRaises(UserErrors.PERSONALIZATION_NOT_OWNED.__class__):
            self.user.set_personalization(
                chat_bubble_style='default', avatar_frame_style='none', profile_card_theme='vip',
            )
        self.user.is_permanent_vip = True
        self.user.save(update_fields=['is_permanent_vip'])
        self.user.set_personalization(
            chat_bubble_style='default', avatar_frame_style='none', profile_card_theme='vip',
        )
        self.assertEqual(self.user.profile_card_theme, 'vip')

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


class SquareNotificationReadTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Notification Space', slug='notification-space', email='notify@example.com')
        self.user = User.create(space=self.space, name='Reader')
        self.actor = User.create(space=self.space, name='Actor')

    def test_statement_payload_can_scope_all_related_unread_events(self):
        from User.models import NotificationEvent

        for event_type in (
            NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE,
            NotificationEventTypeChoice.SQUARE_STATEMENT_COMMENT,
            NotificationEventTypeChoice.SQUARE_COMMENT_LIKE,
            NotificationEventTypeChoice.SQUARE_COMMENT_REPLY,
        ):
            NotificationEvent.objects.create(
                space=self.space,
                user=self.user,
                actor=self.actor,
                event_type=event_type,
                payload={'statement_id': 42},
            )
        NotificationEvent.objects.create(
            space=self.space,
            user=self.user,
            actor=self.actor,
            event_type=NotificationEventTypeChoice.SQUARE_STATEMENT_LIKE,
            payload={'statement_id': 43},
        )

        updated, unread_count = NotificationEvent.mark_square_events_read(self.user, 42)

        self.assertEqual(updated, 4)
        self.assertEqual(unread_count, 1)


class WebPushRegistrationTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Push Space', slug='push-space', email='push@example.com')
        self.first_user = User.create(space=self.space, name='First Device User')
        self.second_user = User.create(space=self.space, name='Second Device User')

    def register(self, user):
        return WebPushSubscription.register(
            user=user,
            endpoint='https://push.example.test/shared-endpoint',
            p256dh='p256dh-key',
            auth='auth-key',
            origin='https://sermo.jyonn.space',
        )

    def test_same_browser_endpoint_can_serve_multiple_accounts(self):
        self.register(self.first_user)
        self.register(self.second_user)

        self.assertEqual(WebPushSubscription.objects.count(), 2)
        self.assertSetEqual(
            set(WebPushSubscription.objects.values_list('user_id', flat=True)),
            {self.first_user.id, self.second_user.id},
        )

    def test_registering_again_refreshes_existing_user_subscription(self):
        first = self.register(self.first_user)
        second = self.register(self.first_user)

        self.assertEqual(first.id, second.id)
        self.assertEqual(WebPushSubscription.objects.count(), 1)

    def test_mirror_origin_is_enabled(self):
        subscription = WebPushSubscription.register(
            user=self.first_user,
            endpoint='https://push.example.test/mirror-endpoint',
            p256dh='p256dh-key',
            auth='auth-key',
            origin='https://sermo.6-79.cn',
        )

        self.assertTrue(subscription.enabled)

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


class ContactAvailabilityTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(name='Contact Space', slug='contact-space', email='owner@example.com')
        self.bound_user = User.create(space=self.space, name='Bound User')
        self.new_user = User.create(space=self.space, name='New User')

    def test_email_conflict_is_rejected_before_code_is_issued(self):
        self.bound_user.bind_contact(UserNotificationChoice.EMAIL, 'Taken@Example.com')

        with self.assertRaises(UserErrors.CONTACT_ALREADY_BOUND.__class__):
            UserContactVerificationCode.issue(
                self.new_user,
                UserNotificationChoice.EMAIL,
                'taken@example.com',
            )

        self.assertFalse(UserContactVerificationCode.objects.filter(user=self.new_user).exists())

    def test_phone_conflict_is_rejected_before_code_is_issued(self):
        self.bound_user.bind_contact(UserNotificationChoice.SMS, '13800000000')

        with self.assertRaises(UserErrors.CONTACT_ALREADY_BOUND.__class__):
            UserContactVerificationCode.issue(
                self.new_user,
                UserNotificationChoice.SMS,
                '13800000000',
            )

        self.assertFalse(UserContactVerificationCode.objects.filter(user=self.new_user).exists())

    def test_same_user_can_request_code_for_current_contact(self):
        self.new_user.bind_contact(UserNotificationChoice.EMAIL, 'self@example.com')

        verification = UserContactVerificationCode.issue(
            self.new_user,
            UserNotificationChoice.EMAIL,
            'SELF@example.com',
        )

        self.assertEqual(verification.target, 'self@example.com')


class NotificatorIntegrationTests(SimpleTestCase):
    @patch('utils.notificator_integration.notificator')
    def test_verification_mail_uses_structured_format_and_locale(self, client):
        send_verification_mail(
            'reader@example.com',
            code='223806',
            time=10,
            title='Sermo 言浪验证码',
            language='zh-CN',
            recipient_name='读者',
        )

        client.mail.assert_called_once_with(
            'reader@example.com',
            format='verification',
            title='Sermo 言浪验证码',
            locale='zh-CN',
            body={'code': '223806', 'time': 10},
            recipient_name='读者',
        )

    @patch('User.models.NotificationDelivery._message_digest_groups')
    def test_message_email_is_grouped_markdown_without_content_leak(self, digest_groups):
        actor = SimpleNamespace(id=8, name='Fly')
        event = SimpleNamespace(
            actor=actor,
            actor_id=actor.id,
            id=21,
            payload={'chat_id': 7, 'content': 'secret'},
            render_delivery_message=lambda **_kwargs: ('New message', 'secret'),
        )
        deliveries = [SimpleNamespace(event=event, event_id=event.id)] * 2
        preference = SimpleNamespace(
            hide_message_content=True,
        )
        digest_groups.return_value = ([dict(
            name='Fly',
            chat=SimpleNamespace(group=False),
            deliveries=deliveries,
            selected=deliveries,
            total_count=2,
        )], 0)

        body = NotificationDelivery._render_email_batch_body(deliveries, preference)

        self.assertEqual(body, 'You received 2 messages from 1 conversations.')
        self.assertNotIn('Fly', body)
        self.assertNotIn('secret', body)


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

    def test_hidden_message_uses_anonymous_title_and_body(self):
        event = NotificationEvent(
            event_type=NotificationEventTypeChoice.DIRECT_MESSAGE,
            payload={'content': 'secret'},
        )

        title, body = event.render_delivery_message(hide_message_content=True)

        self.assertEqual(title, 'New message')
        self.assertEqual(body, 'You received a message.')

    def test_online_message_uses_default_title_and_body(self):
        event = NotificationEvent(
            event_type=NotificationEventTypeChoice.SYSTEM,
            payload={'kind': 'peer_online'},
        )

        title, body = event.render_delivery_message()

        self.assertEqual(title, 'Friend online')
        self.assertEqual(body, 'Your friend is online now.')

    def test_web_push_direct_message_uses_friend_name_and_natural_media_text(self):
        recipient = User(id=1, language='zh-CN')
        actor = User(id=2, name='Fly')
        event = NotificationEvent(
            user=recipient,
            actor=actor,
            event_type=NotificationEventTypeChoice.DIRECT_MESSAGE,
            payload={'message_type': 1, 'content': '[图片]'},
        )

        title, body = NotificationDelivery(event=event)._web_notification_text()

        self.assertEqual(title, 'Fly')
        self.assertEqual(body, '发送了一张图片。')

    def test_web_push_group_message_uses_group_name_and_sender_prefix(self):
        recipient = User(id=1, language='zh-CN')
        actor = User(id=2, name='Fly')
        event = NotificationEvent(
            user=recipient,
            actor=actor,
            event_type=NotificationEventTypeChoice.GROUP_MESSAGE,
            payload={
                'chat_name': '一百二十五星俱乐部',
                'message_type': 0,
                'content': '晚上集合',
            },
        )

        title, body = NotificationDelivery(event=event)._web_notification_text()

        self.assertEqual(title, '一百二十五星俱乐部')
        self.assertEqual(body, 'Fly：晚上集合')

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

    @patch('User.models.NotificationPreference.ensure_defaults', return_value=[])
    @patch('User.models.NotificationDelivery.enqueue_web_for_event', return_value=['web'])
    def test_web_push_is_enqueued_before_slower_channels(self, web_push, ensure_defaults):
        event = NotificationEvent(user=User(id=1))

        deliveries = NotificationDelivery.enqueue_for_event(event)

        web_push.assert_called_once_with(event)
        ensure_defaults.assert_called_once_with(event.user)
        self.assertEqual(deliveries, ['web'])
