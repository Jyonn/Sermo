import json
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from Chat.models import Chat, ChatMember, ChatMemberRoleChoice, ChatMemberStatusChoice, ChatTypeChoice
from Config.models import Config, ConfigInstance
from Message.models import Message, MessageEvent, MessageTypeChoice, MessageUserState, PinnedMessage
from Space.models import Space, SpacePhoneVerificationCode
from Square.models import Statement, StatementComment, StatementCommentLike, StatementLike
from User.models import GrowthEvent, NotificationPreference, User, UserEmojiUsage, UserNotificationChoice
from User.growth import GROWTH_THRESHOLDS
from utils import auth


class SpaceSlugValidationTests(SimpleTestCase):
    def test_frontend_root_routes_are_reserved(self):
        for slug in (
            'entry', 'space', 'app', 'friend-invite', 'official-login',
            'account-switch', 'pwa', 'assets', 'icons', 'labs',
        ):
            with self.subTest(slug=slug):
                self.assertTrue(Space.vldt.reserved_slug(slug))

    def test_regular_space_slug_is_available(self):
        self.assertFalse(Space.vldt.reserved_slug('yuanmeng'))


class SpaceAdminApiTests(TestCase):
    def setUp(self):
        self.space = Space.objects.create(
            name='Test Space',
            slug='test-space',
            email='admin@example.com',
        )
        self.space.admin_phone = '+8613800000000'
        self.space.admin_phone_verified_at = timezone.now()
        self.space.save(update_fields=['admin_phone', 'admin_phone_verified_at'])
        self.official = self.space.ensure_official_user()
        self.member = User.create(
            space=self.space,
            name='Member',
            email='member@example.com',
            verified=True,
        )
        Config.objects.create(
            key=ConfigInstance.QINIU_DOMAIN,
            value='https://resource.example.com',
        )
        self.token = auth.get_space_login_token(self.space)['auth']

    def authorization(self):
        return dict(HTTP_AUTHORIZATION=f'Bearer {self.token}')

    def user_authorization(self, user):
        token = auth.get_login_token(user)['auth']
        return dict(HTTP_AUTHORIZATION=f'Bearer {token}')

    def grant_growth_level(self, user, level):
        target = GROWTH_THRESHOLDS[level - 1]
        for index in range((target + 59) // 60):
            GrowthEvent.objects.get_or_create(
                user=user,
                event_key=f'social:active_4_weeks:{2000 + index // 99}-W{index % 99:02d}',
                defaults=dict(category='social', title='连续活跃 4 周', points=60),
            )
        user.reconcile_growth()

    def test_email_tier_has_five_member_limit_and_restricted_square(self):
        trial = Space.objects.create(name='Trial', slug='trial-space', email='trial@example.com')
        self.assertEqual(trial.tier_member_limit, 5)
        self.assertFalse(trial.json()['group_square_enabled'])
        with self.assertRaises(Exception):
            trial.set_admin_settings('Trial', True, True, True, 2, None)

    def test_email_can_create_again_only_after_existing_space_phone_verification(self):
        trial = Space.objects.create(name='Trial', slug='trial-email', email='shared@example.com')
        with self.assertRaises(Exception):
            Space.require_email_creation_available('shared@example.com')
        trial.admin_phone_verified_at = timezone.now()
        trial.save(update_fields=['admin_phone_verified_at'])
        self.assertEqual(Space.require_email_creation_available('shared@example.com'), 'shared@example.com')

    def test_phone_verification_upgrades_space_to_one_hundred_members(self):
        trial = Space.objects.create(name='Trial', slug='trial-phone', email='phone@example.com')
        code = SpacePhoneVerificationCode.issue(trial, '+8613900000000')
        SpacePhoneVerificationCode.verify(trial, code.phone, code.code)
        trial.refresh_from_db()
        self.assertEqual(trial.verification_tier, 'phone')
        self.assertEqual(trial.tier_member_limit, 100)

    def test_online_square_always_includes_official_user(self):
        self.official.last_heartbeat = timezone.now() - timedelta(days=30)
        self.official.save(update_fields=['last_heartbeat'])

        response = self.client.get(
            '/spaces/users/online?limit=80&offset=0',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        user_ids = [item['user_id'] for item in response.json()['body']]
        self.assertIn(self.official.id, user_ids)

    def test_official_user_can_exchange_admin_session(self):
        response = self.client.post(
            '/spaces/admin/session',
            **self.user_authorization(self.official),
        )

        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()['body']
        self.assertEqual(payload['space']['space_id'], self.space.id)
        self.assertIn('auth', payload['auth'])

    def test_member_cannot_exchange_admin_session(self):
        response = self.client.post(
            '/spaces/admin/session',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 403, response.content)

    def test_removing_member_hides_square_content_and_clears_reactions(self):
        other = User.create(self.space, 'Other member', verified=True)
        statement = Statement.create_statement(self.member, '即将被清理的发言', 'public', [])
        other_statement = Statement.create_statement(other, '保留的发言', 'public', [])
        comment = StatementComment.create_comment(self.member, other_statement.id, '即将被清理的评论')
        StatementLike.objects.create(statement=other_statement, user=self.member)
        StatementCommentLike.objects.create(comment=comment, user=self.member)

        response = self.client.delete(
            f'/spaces/admin/users/remove?user_id={self.member.id}',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        statement.refresh_from_db()
        comment.refresh_from_db()
        self.assertTrue(statement.is_deleted)
        self.assertTrue(comment.is_deleted)
        self.assertFalse(StatementLike.objects.filter(user=self.member).exists())
        self.assertFalse(StatementCommentLike.objects.filter(user=self.member).exists())
        self.assertFalse(Statement.visible_for(other).filter(id=statement.id).exists())

    def test_deleted_member_with_square_residue_can_be_removed_again(self):
        statement = Statement.create_statement(self.member, '历史遗留发言', 'public', [])
        self.member.is_deleted = True
        self.member.save(update_fields=['is_deleted'])

        self.assertTrue(self.member.has_removal_residue())
        self.member.remove()

        statement.refresh_from_db()
        self.assertTrue(statement.is_deleted)
        self.assertFalse(self.member.has_removal_residue())

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_broadcast_is_idempotent(self, enqueue):
        payload = dict(content='Hello everyone', type=0, broadcast_id='broadcast:test')

        first = self.client.post(
            '/spaces/admin/broadcast',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )
        second = self.client.post(
            '/spaces/admin/broadcast',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        chat = Chat.get_or_create_direct(self.official, self.member)
        self.assertEqual(
            Message.objects.filter(
                chat=chat,
                user=self.official,
                client_message_id=payload['broadcast_id'],
            ).count(),
            1,
        )
        self.assertEqual(second.json()['body']['duplicate_count'], 1)
        enqueue.assert_called()

    @patch('User.models.NotificationEvent._enqueue_deliveries_after_commit')
    def test_broadcast_supports_media_messages(self, enqueue):
        payload = dict(
            content=json.dumps({
                'key': 'sermo/messages/image/test.jpg',
                'mime_type': 'image/jpeg',
            }),
            type=1,
            broadcast_id='broadcast:image:test',
        )

        response = self.client.post(
            '/spaces/admin/broadcast',
            data=json.dumps(payload),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        chat = Chat.get_or_create_direct(self.official, self.member)
        message = Message.objects.get(
            chat=chat,
            user=self.official,
            client_message_id=payload['broadcast_id'],
        )
        self.assertEqual(message.type, 1)
        self.assertIsNotNone(message.media_asset)
        self.assertTrue(message.media_asset.blob_slug)
        self.assertEqual(message.preview_text(), '[图片]')
        enqueue.assert_called()

    def test_member_list_only_exposes_contact_status(self):
        NotificationPreference.set_preference(
            self.member,
            UserNotificationChoice.EMAIL,
            enabled=1,
            offline_threshold_minutes=12,
        )

        response = self.client.get('/spaces/admin/users', **self.authorization())

        self.assertEqual(response.status_code, 200)
        member = response.json()['body'][0]
        self.assertNotIn('email', member)
        self.assertTrue(member['contacts']['email']['bound'])
        self.assertTrue(member['contacts']['email']['verified'])
        email_pref = next(
            item for item in member['notification_preferences']
            if item['channel'] == UserNotificationChoice.EMAIL
        )
        self.assertTrue(email_pref['enabled'])
        self.assertEqual(email_pref['offline_threshold_minutes'], 12)

    def test_admin_can_name_space_growth_levels(self):
        level_names = [f'阶段{index}' for index in range(1, 19)]
        response = self.client.post(
            '/spaces/admin/settings',
            data=json.dumps({
                'name': self.space.name,
                'group_square_enabled': 1,
                'member_limit': 100,
                'level_names': level_names,
            }),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.space.refresh_from_db()
        self.assertEqual(self.space.level_names, level_names)
        self.assertEqual(response.json()['body']['level_names'], level_names)

    def test_admin_feature_settings_preserve_valid_module_combination(self):
        response = self.client.post(
            '/spaces/admin/settings',
            data=json.dumps({
                'name': self.space.name,
                'group_square_enabled': 1,
                'chat_enabled': 0,
                'square_explore_enabled': 0,
                'unverified_group_policy': 1,
                'member_limit': 100,
                'level_names': self.space.level_names,
            }),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.space.refresh_from_db()
        self.assertFalse(self.space.chat_enabled)
        self.assertTrue(self.space.group_square_enabled)
        self.assertFalse(self.space.square_explore_enabled)
        self.assertEqual(self.space.unverified_group_policy, 1)

    def test_admin_cannot_disable_chat_and_square_together(self):
        response = self.client.post(
            '/spaces/admin/settings',
            data=json.dumps({
                'name': self.space.name,
                'group_square_enabled': 0,
                'chat_enabled': 0,
                'square_explore_enabled': 0,
                'unverified_group_policy': 2,
                'member_limit': 100,
                'level_names': self.space.level_names,
            }),
            content_type='application/json',
            **self.authorization(),
        )

        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()['identifier'], 'SPACE@MODULES_REQUIRED')

    def test_official_account_has_highest_growth_level(self):
        self.space.level_names = [f'阶段{index}' for index in range(1, 19)]
        self.space.save(update_fields=['level_names'])
        self.official.phone = '13800000000'
        self.official.phone_verified_at = timezone.now()
        self.official.save(update_fields=['phone', 'phone_verified_at'])

        growth = self.official.calculate_growth()

        self.assertEqual(growth['score'], 5300)
        self.assertEqual(growth['level'], 18)
        self.assertEqual(growth['name'], '阶段18')
        self.assertIn('自定义消息通知', growth['privileges'])
        self.assertEqual(len(growth['levels']), 18)
        self.assertTrue(any(reward['id'] == 'capability.notification' for reward in growth['levels'][9]['rewards']))

    def test_growth_level_acknowledgements_are_persisted_in_order(self):
        self.grant_growth_level(self.member, 2)
        growth = self.member.calculate_growth()
        self.assertGreaterEqual(growth['level'], 2)
        self.assertEqual(growth['acknowledged_level'], 0)
        self.assertEqual(growth['pending_level'], 1)

        skipped = self.client.post(
            '/users/me/growth',
            data=json.dumps({'level': 2}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(skipped.status_code, 400)

        acknowledged = self.client.post(
            '/users/me/growth',
            data=json.dumps({'level': 1}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(acknowledged.status_code, 200, acknowledged.content)
        self.assertEqual(acknowledged.json()['body']['acknowledged_level'], 1)
        self.assertEqual(acknowledged.json()['body']['pending_level'], 2)

        self.member.refresh_from_db()
        self.assertEqual(self.member.growth_acknowledged_level, 1)

    def test_permanent_vip_requires_dual_verification_and_level_six(self):
        ineligible = self.client.post(
            '/users/me/permanent-vip',
            **self.user_authorization(self.member),
        )
        self.assertEqual(ineligible.status_code, 403)

        self.member.set_password('safe-password')
        self.member.phone = '13800000006'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['phone', 'phone_verified_at'])
        self.grant_growth_level(self.member, 6)
        self.assertGreaterEqual(self.member.calculate_growth()['level'], 6)

        claimed = self.client.post(
            '/users/me/permanent-vip',
            **self.user_authorization(self.member),
        )
        self.assertEqual(claimed.status_code, 200, claimed.content)
        self.assertEqual(claimed.json()['body']['slot'], 1)
        self.assertTrue(claimed.json()['body']['claimed_by_user'])

        self.member.refresh_from_db()
        self.assertTrue(self.member.is_permanent_vip)
        vip_event = self.member.growth_events.get(event_key='vip:permanent')
        self.assertEqual(vip_event.points, 500)
        score_after_claim = self.member.growth_score

        repeated = self.client.post(
            '/users/me/permanent-vip',
            **self.user_authorization(self.member),
        )
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.assertEqual(repeated.json()['body']['slot'], 1)
        self.member.refresh_from_db()
        self.assertEqual(self.member.growth_score, score_after_claim)
        self.assertEqual(self.member.growth_events.filter(event_key='vip:permanent').count(), 1)

    def test_user_personalization_is_persisted_and_serialized(self):
        self.grant_growth_level(self.member, 2)
        payload = {
            'chat_bubble_style': 'comic',
            'avatar_frame_style': 'orbit',
        }
        response = self.client.post(
            '/users/me/personalization',
            data=json.dumps(payload),
            content_type='application/json',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.member.refresh_from_db()
        for field, value in payload.items():
            self.assertEqual(getattr(self.member, field), value)
            self.assertEqual(response.json()['body'][field], value)
            self.assertEqual(self.member.tiny_json()[field], value)

    def test_cultural_bubble_styles_are_persisted_and_serialized(self):
        self.member.set_password('safe-password')
        self.member.phone = '13800000008'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['password', 'phone', 'phone_verified_at'])
        self.grant_growth_level(self.member, 13)
        self.assertEqual(self.member.effective_growth_level(), 13)
        for bubble_style in ('zen', 'hero', 'dragon', 'bauhaus', 'mosaic'):
            payload = {
                'chat_bubble_style': bubble_style,
                'avatar_frame_style': 'none',
            }
            response = self.client.post(
                '/users/me/personalization',
                data=json.dumps(payload),
                content_type='application/json',
                **self.user_authorization(self.member),
            )

            self.assertEqual(response.status_code, 200, response.content)
            self.member.refresh_from_db()
            self.assertEqual(self.member.chat_bubble_style, bubble_style)
            self.assertEqual(response.json()['body']['chat_bubble_style'], bubble_style)
            self.assertEqual(self.member.tiny_json()['chat_bubble_style'], bubble_style)

    def test_vip_bubble_requires_permanent_vip(self):
        payload = {
            'chat_bubble_style': 'vip',
            'avatar_frame_style': 'vip',
        }
        denied = self.client.post(
            '/users/me/personalization',
            data=json.dumps(payload),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(denied.status_code, 403, denied.content)

        self.member.is_permanent_vip = True
        self.member.save(update_fields=['is_permanent_vip'])
        accepted = self.client.post(
            '/users/me/personalization',
            data=json.dumps(payload),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.member.refresh_from_db()
        self.assertEqual(self.member.chat_bubble_style, 'vip')
        self.assertEqual(self.member.avatar_frame_style, 'vip')

    def test_ip_styles_require_their_level_or_permanent_vip(self):
        payload = {
            'chat_bubble_style': 'niko',
            'avatar_frame_style': 'niko-run',
        }
        denied = self.client.post(
            '/users/me/personalization',
            data=json.dumps(payload),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(denied.json()['identifier'], 'USER@GROWTH_LEVEL_REQUIRED')

        self.member.is_permanent_vip = True
        self.member.save(update_fields=['is_permanent_vip'])
        accepted = self.client.post(
            '/users/me/personalization',
            data=json.dumps(payload),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.member.refresh_from_db()
        self.assertEqual(self.member.chat_bubble_style, 'niko')
        self.assertEqual(self.member.avatar_frame_style, 'niko-run')

        self.member.chat_bubble_style = 'default'
        self.member.avatar_frame_style = 'none'
        self.member.is_permanent_vip = False
        self.member.set_password('safe-password')
        self.member.phone = '13800000016'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=[
            'chat_bubble_style', 'avatar_frame_style', 'is_permanent_vip',
            'password', 'phone', 'phone_verified_at',
        ])
        self.grant_growth_level(self.member, 18)
        self.assertEqual(self.member.effective_growth_level(), 18)
        level_eighteen = self.client.post(
            '/users/me/personalization',
            data=json.dumps(payload),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(level_eighteen.status_code, 200, level_eighteen.content)

    def test_discontinued_xiaobai_styles_cannot_be_newly_enabled(self):
        denied = self.client.post(
            '/users/me/personalization',
            data=json.dumps({
                'chat_bubble_style': 'xiaobai',
                'avatar_frame_style': 'xiaobai-run',
            }),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(denied.status_code, 403, denied.content)
        self.assertEqual(denied.json()['identifier'], 'USER@PERSONALIZATION_UNAVAILABLE')

    def test_message_search_filters_keyword_type_and_hidden_records(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        visible_text = Message.create(chat, self.official, MessageTypeChoice.TEXT, '周末去海边散步')
        hidden_text = Message.create(chat, self.official, MessageTypeChoice.TEXT, '海边旧计划')
        image = Message.create(chat, self.official, MessageTypeChoice.IMAGE, json.dumps({
            'key': 'sermo/messages/image/search-test.jpg',
            'mime_type': 'image/jpeg',
        }))
        hidden_text.hide_for(self.member)

        keyword_response = self.client.get(
            '/messages/search',
            {'chat_id': chat.id, 'keyword': '海边', 'limit': 20},
            **self.user_authorization(self.member),
        )
        self.assertEqual(keyword_response.status_code, 200, keyword_response.content)
        keyword_ids = [row['message_id'] for row in keyword_response.json()['body']['items']]
        self.assertEqual(keyword_ids, [visible_text.id])

        image_response = self.client.get(
            '/messages/search',
            {'chat_id': chat.id, 'type': MessageTypeChoice.IMAGE, 'limit': 20},
            **self.user_authorization(self.member),
        )
        self.assertEqual(image_response.status_code, 200, image_response.content)
        self.assertEqual(
            [row['message_id'] for row in image_response.json()['body']['items']],
            [image.id],
        )

    def test_batch_delete_hides_messages_from_any_sender_for_actor(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        first = Message.create(chat, self.member, MessageTypeChoice.TEXT, 'First')
        second = Message.create(chat, self.member, MessageTypeChoice.TEXT, 'Second')
        other = Message.create(chat, self.official, MessageTypeChoice.TEXT, 'Other')

        accepted = self.client.delete(
            '/messages/batch',
            data=json.dumps({'message_ids': [first.id, other.id]}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(accepted.json()['body']['deleted_message_ids'], [first.id, other.id])
        self.assertFalse(Message.objects.get(id=first.id).is_deleted)
        self.assertFalse(Message.objects.get(id=other.id).is_deleted)
        self.assertFalse(Message.objects.get(id=second.id).is_deleted)
        self.assertTrue(MessageUserState.objects.filter(message=first, user=self.member).exists())
        self.assertTrue(MessageUserState.objects.filter(message=other, user=self.member).exists())

        member_rows = self.client.get(
            f'/messages/?chat_id={chat.id}&limit=30',
            **self.user_authorization(self.member),
        ).json()['body']
        official_rows = self.client.get(
            f'/messages/?chat_id={chat.id}&limit=30',
            **self.user_authorization(self.official),
        ).json()['body']
        self.assertNotIn(first.id, [row['message_id'] for row in member_rows])
        self.assertNotIn(other.id, [row['message_id'] for row in member_rows])
        self.assertIn(first.id, [row['message_id'] for row in official_rows])
        self.assertIn(other.id, [row['message_id'] for row in official_rows])

    def test_clear_chat_history_only_hides_messages_for_actor(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        first = Message.create(chat, self.member, MessageTypeChoice.TEXT, 'Mine')
        second = Message.create(chat, self.official, MessageTypeChoice.TEXT, 'Theirs')
        system = Message.create_system(chat, self.official, 'group_renamed', new_title='History')
        visible_message_ids = set(Message.visible_for_user(chat, self.member).values_list('id', flat=True))
        self.assertTrue({first.id, second.id, system.id}.issubset(visible_message_ids))

        response = self.client.delete(
            '/messages/clear',
            data=json.dumps({'chat_id': chat.id}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body']['deleted_count'], len(visible_message_ids))
        self.assertFalse(Message.visible_for_user(chat, self.member).exists())
        self.assertEqual(
            set(Message.visible_for_user(chat, self.official).values_list('id', flat=True)),
            visible_message_ids,
        )
        chat_payload = self.client.get('/chats/', **self.user_authorization(self.member)).json()['body'][0]
        self.assertIsNone(chat_payload['last_message'])

    def test_message_can_be_hidden_for_one_member_without_recalling_it(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        message = Message.create(chat, self.official, MessageTypeChoice.TEXT, 'Only hide this copy')

        hidden = self.client.delete(
            f'/messages/?message_id={message.id}&scope=me',
            **self.user_authorization(self.member),
        )
        self.assertEqual(hidden.status_code, 200, hidden.content)
        self.assertTrue(MessageUserState.objects.filter(message=message, user=self.member).exists())
        self.assertFalse(Message.objects.get(id=message.id).is_deleted)

        member_rows = self.client.get(
            f'/messages/?chat_id={chat.id}&limit=30',
            **self.user_authorization(self.member),
        ).json()['body']
        official_rows = self.client.get(
            f'/messages/?chat_id={chat.id}&limit=30',
            **self.user_authorization(self.official),
        ).json()['body']
        self.assertNotIn(message.id, [row['message_id'] for row in member_rows])
        self.assertIn(message.id, [row['message_id'] for row in official_rows])

        reconciled = self.client.post(
            '/messages/reconcile',
            data=json.dumps({'chat_id': chat.id, 'message_ids': [message.id]}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(reconciled.status_code, 200, reconciled.content)
        self.assertEqual(reconciled.json()['body']['deleted_message_ids'], [message.id])

    def test_regular_user_cannot_recall_message_after_two_minutes(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        message = Message.create(chat, self.member, MessageTypeChoice.TEXT, 'Too late')
        Message.objects.filter(id=message.id).update(created_at=timezone.now() - timedelta(minutes=3))

        response = self.client.delete(
            f'/messages/?message_id={message.id}&scope=everyone',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 403, response.content)
        self.assertFalse(Message.objects.get(id=message.id).is_deleted)

    def test_vip_user_can_recall_message_within_seven_days(self):
        self.member.is_permanent_vip = True
        self.member.save(update_fields=['is_permanent_vip'])
        chat = Chat.get_or_create_direct(self.official, self.member)
        message = Message.create(chat, self.member, MessageTypeChoice.TEXT, 'VIP recall')
        Message.objects.filter(id=message.id).update(created_at=timezone.now() - timedelta(days=6))

        response = self.client.delete(
            f'/messages/?message_id={message.id}&scope=everyone',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(Message.objects.get(id=message.id).is_deleted)

    def test_message_event_sync_scopes_hidden_and_recalled_events(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        cursor = MessageEvent.objects.order_by('-id').values_list('id', flat=True).first() or 0
        message = Message.create(chat, self.official, MessageTypeChoice.TEXT, 'Sync this')
        message.hide_for(self.member)

        member_events = self.client.get(
            f'/messages/sync-v2?after={cursor}&limit=50',
            **self.user_authorization(self.member),
        ).json()['body']['events']
        official_events = self.client.get(
            f'/messages/sync-v2?after={cursor}&limit=50',
            **self.user_authorization(self.official),
        ).json()['body']['events']
        self.assertEqual([event['type'] for event in member_events], ['message.created', 'message.hidden'])
        self.assertEqual([event['type'] for event in official_events], ['message.created'])

        message.remove()
        recalled_events = self.client.get(
            f'/messages/sync-v2?after={official_events[-1]["event_id"]}&limit=50',
            **self.user_authorization(self.official),
        ).json()['body']['events']
        self.assertEqual(recalled_events[-1]['type'], 'message.recalled')
        self.assertEqual(recalled_events[-1]['message_id'], message.id)
        self.assertGreaterEqual(MessageEvent.objects.count(), 3)

    def test_daily_chat_growth_is_capped(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        for index in range(20):
            Message.create(
                chat=chat,
                user=self.member,
                message_type=MessageTypeChoice.TEXT,
                content=f'message {index}',
                client_message_id=f'growth-{index}',
            )

        self.member.refresh_from_db()
        growth = self.member.calculate_growth()
        self.assertEqual(growth['daily']['earned'], 20)
        self.assertEqual(self.member.growth_score, 20)
        self.assertFalse(next(item for item in growth['milestones'] if item['key'] == 'security:email')['earned'])

    def test_one_time_growth_is_idempotent_and_repairs_inflated_points(self):
        self.member.award_growth('security:email')
        self.member.calculate_growth()
        email_event = GrowthEvent.objects.get(user=self.member, event_key='security:email')
        self.assertEqual(email_event.points, 30)

        email_event.points = 450
        email_event.save(update_fields=['points'])
        self.member.growth_score = 450
        self.member.save(update_fields=['growth_score'])

        growth = self.member.calculate_growth()
        email_event.refresh_from_db()
        self.member.refresh_from_db()
        self.assertEqual(email_event.points, 30)
        self.assertEqual(growth['score'], 30)
        self.assertEqual(self.member.growth_score, 30)

    def test_growth_level_is_capped_by_security_setup(self):
        self.grant_growth_level(self.member, 18)

        growth = self.member.calculate_growth()
        self.assertEqual(growth['level'], 3)
        self.assertEqual(growth['score_level'], 18)
        self.assertEqual(growth['effective_level'], 3)

        self.member.set_password('safe-password')
        self.member.email = None
        self.member.email_verified_at = None
        self.member.save(update_fields=['email', 'email_verified_at'])
        self.assertEqual(self.member.calculate_growth()['level'], 6)

        self.member.email = 'member@example.com'
        self.member.email_verified_at = timezone.now()
        self.member.save(update_fields=['email', 'email_verified_at'])
        self.assertEqual(self.member.calculate_growth()['level'], 9)

        self.member.phone = '13800000001'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['phone', 'phone_verified_at'])
        growth = self.member.calculate_growth()
        self.assertEqual(growth['level'], 18)
        self.assertEqual(growth['level_cap'], 18)

    def test_nickname_cooldown_improves_at_levels_eight_and_twelve(self):
        self.member.set_password('safe-password')
        self.grant_growth_level(self.member, 5)
        self.assertEqual(self.member.nickname_change_interval_days(), 365)
        self.grant_growth_level(self.member, 7)
        self.assertEqual(self.member.nickname_change_interval_days(), 365)
        self.grant_growth_level(self.member, 8)
        self.assertEqual(self.member.nickname_change_interval_days(), 30)
        self.member.email_verified_at = timezone.now()
        self.member.phone = '13800000009'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['email_verified_at', 'phone', 'phone_verified_at'])
        self.grant_growth_level(self.member, 12)
        self.assertEqual(self.member.nickname_change_interval_days(), 7)

    def test_private_account_only_requires_verified_phone(self):
        self.member.email = None
        self.member.email_verified_at = None
        self.member.phone = '13800000002'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['email', 'email_verified_at', 'phone', 'phone_verified_at'])

        self.member.set_private_account(True)

        self.member.refresh_from_db()
        self.assertTrue(self.member.is_private_account)

    def test_account_switch_matches_mainland_phone_with_or_without_country_code(self):
        other_space = Space.objects.create(
            name='Other Space',
            slug='other-space',
            email='other-admin@example.com',
        )
        target = User.create(
            space=other_space,
            name='Other Member',
            verified=False,
        )
        self.member.phone = '13800000004'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['phone', 'phone_verified_at'])
        target.phone = '+8613800000004'
        target.phone_verified_at = timezone.now()
        target.save(update_fields=['phone', 'phone_verified_at'])

        response = self.client.get(
            '/users/me/switch-accounts',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        targets = response.json()['body']
        self.assertEqual([item['user']['user_id'] for item in targets], [target.id])

    def test_account_switch_matches_official_account_by_verified_phone(self):
        self.member.phone = '13800000005'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['phone', 'phone_verified_at'])
        self.official.phone = '+8613800000005'
        self.official.phone_verified_at = timezone.now()
        self.official.is_private_account = False
        self.official.save(update_fields=['phone', 'phone_verified_at', 'is_private_account'])

        response = self.client.get(
            '/users/me/switch-accounts',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        targets = response.json()['body']
        self.assertEqual([item['user']['user_id'] for item in targets], [self.official.id])

    def test_text_message_emoji_usage_is_idempotent(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        for _index in range(2):
            Message.create(
                chat=chat,
                user=self.member,
                message_type=MessageTypeChoice.TEXT,
                content='收到 👍👍',
                client_message_id='emoji-idempotent',
            )

        usage = UserEmojiUsage.objects.get(user=self.member, emoji='👍')
        self.assertEqual(usage.use_count, 2)
        response = self.client.get(
            '/users/me/emoji-usage',
            **self.user_authorization(self.member),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body'][0]['emoji'], '👍')
        self.assertEqual(response.json()['body'][0]['use_count'], 2)

    def test_direct_chat_members_can_share_pinned_messages(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        message = Message.create(chat, self.member, MessageTypeChoice.TEXT, 'Important')

        response = self.client.post(
            f'/messages/pins?message_id={message.id}',
            **self.user_authorization(self.official),
        )
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.get(
            f'/messages/pins?chat_id={chat.id}',
            **self.user_authorization(self.member),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body'][0]['message']['message_id'], message.id)
        self.assertEqual(
            [item['user_id'] for item in response.json()['body'][0]['pinned_by_users']],
            [self.official.id],
        )

        response = self.client.post(
            f'/messages/pins?message_id={message.id}',
            **self.user_authorization(self.member),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(PinnedMessage.objects.filter(message=message).count(), 2)
        self.assertEqual(len(response.json()['body']['pinned_by_users']), 2)

        response = self.client.delete(
            f'/messages/pins?message_id={message.id}',
            **self.user_authorization(self.member),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(PinnedMessage.objects.filter(message=message, pinned_by=self.official).exists())
        self.assertFalse(PinnedMessage.objects.filter(message=message, pinned_by=self.member).exists())

        response = self.client.delete(
            f'/messages/pins?message_id={message.id}',
            **self.user_authorization(self.official),
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(PinnedMessage.objects.filter(message=message).exists())

    def test_group_member_cannot_manage_pinned_messages(self):
        chat = Chat.objects.create(
            space=self.space,
            chat_type=ChatTypeChoice.GROUP,
            title='Group',
            created_by=self.official,
        )
        ChatMember.objects.create(
            chat=chat,
            user=self.official,
            role=ChatMemberRoleChoice.OWNER,
            status=ChatMemberStatusChoice.ACTIVE,
        )
        ChatMember.objects.create(
            chat=chat,
            user=self.member,
            role=ChatMemberRoleChoice.MEMBER,
            status=ChatMemberStatusChoice.ACTIVE,
        )
        message = Message.create(chat, self.official, MessageTypeChoice.TEXT, 'Owner note')

        response = self.client.post(
            f'/messages/pins?message_id={message.id}',
            **self.user_authorization(self.member),
        )
        self.assertEqual(response.status_code, 403, response.content)

    def test_space_admin_email_does_not_bypass_contact_matching(self):
        self.member.email = self.space.email
        self.member.email_verified_at = timezone.now()
        self.member.save(update_fields=['email', 'email_verified_at'])
        self.official.email = 'official-other@example.com'
        self.official.email_verified_at = timezone.now()
        self.official.save(update_fields=['email', 'email_verified_at'])

        response = self.client.get(
            '/users/me/switch-accounts',
            **self.user_authorization(self.member),
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['body'], [])

    def test_message_and_profile_features_follow_growth_route(self):
        chat = Chat.get_or_create_direct(self.official, self.member)
        image_payload = json.dumps({
            'key': 'sermo/messages/image/locked.jpg',
            'mime_type': 'image/jpeg',
        })
        locked_image = self.client.post(
            f'/messages/?chat_id={chat.id}',
            data=json.dumps({'type': MessageTypeChoice.IMAGE, 'content': image_payload}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(locked_image.status_code, 403)
        self.assertEqual(locked_image.json()['identifier'], 'USER@GROWTH_LEVEL_REQUIRED')

        self.member.set_password('safe-password')
        self.grant_growth_level(self.member, 4)
        self.assertEqual(self.member.effective_growth_level(), 4)
        image_response = self.client.post(
            f'/messages/?chat_id={chat.id}',
            data=json.dumps({'type': MessageTypeChoice.IMAGE, 'content': image_payload}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(image_response.status_code, 200, image_response.content)

        nickname_locked = self.client.post(
            '/users/me/name',
            data=json.dumps({'name': 'NextMember'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(nickname_locked.status_code, 403)

        self.grant_growth_level(self.member, 5)
        nickname_response = self.client.post(
            '/users/me/name',
            data=json.dumps({'name': 'NextMember'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(nickname_response.status_code, 200, nickname_response.content)
        nickname_cooldown = self.client.post(
            '/users/me/name',
            data=json.dumps({'name': 'ThirdMember'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(nickname_cooldown.status_code, 400)
        self.assertEqual(nickname_cooldown.json()['identifier'], 'USER@NICKNAME_CHANGE_COOLDOWN')

    def test_chat_background_has_free_and_levelled_presets(self):
        self.grant_growth_level(self.member, 2)
        free = self.client.post(
            '/users/me/chat-background',
            data=json.dumps({'theme': 'paper'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(free.status_code, 200, free.content)

        locked = self.client.post(
            '/users/me/chat-background',
            data=json.dumps({'theme': 'comic'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(locked.status_code, 403, locked.content)
        self.assertEqual(locked.json()['identifier'], 'USER@GROWTH_LEVEL_REQUIRED')

        self.member.set_password('safe-password')
        self.grant_growth_level(self.member, 5)
        self.assertEqual(self.member.effective_growth_level(), 5)

        updated = self.client.post(
            '/users/me/chat-background',
            data=json.dumps({'theme': 'comic'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.member.refresh_from_db()
        self.assertEqual(self.member.chat_background_theme, 'comic')
        self.assertEqual(self.member.chat_background_uri, '')

        higher_locked = self.client.post(
            '/users/me/chat-background',
            data=json.dumps({'theme': 'hero'}),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(higher_locked.status_code, 403, higher_locked.content)

    def test_custom_notification_messages_require_level_ten(self):
        self.member.set_password('safe-password')

        basic_update = self.client.post(
            '/users/me/notification-prefs',
            data=json.dumps({
                'channel': UserNotificationChoice.EMAIL,
                'enabled': 1,
                'hide_message_content': 1,
            }),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(basic_update.status_code, 200, basic_update.content)

        locked = self.client.post(
            '/users/me/notification-prefs',
            data=json.dumps({
                'channel': UserNotificationChoice.EMAIL,
                'hidden_direct_message_text': '有人找你',
            }),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(locked.status_code, 403, locked.content)
        self.assertEqual(locked.json()['identifier'], 'USER@GROWTH_LEVEL_REQUIRED')

        self.member.phone = '13800000003'
        self.member.phone_verified_at = timezone.now()
        self.member.save(update_fields=['phone', 'phone_verified_at'])
        self.grant_growth_level(self.member, 10)
        self.assertEqual(self.member.effective_growth_level(), 10)

        updated = self.client.post(
            '/users/me/notification-prefs',
            data=json.dumps({
                'channel': UserNotificationChoice.EMAIL,
                'hidden_direct_message_text': '有人找你',
            }),
            content_type='application/json',
            **self.user_authorization(self.member),
        )
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()['body']['hidden_direct_message_text'], '有人找你')

    def test_permanent_vip_can_customize_notification_messages_below_level_ten(self):
        self.member.set_password('safe-password')
        self.member.is_permanent_vip = True
        self.member.save(update_fields=['is_permanent_vip'])

        updated = self.client.post(
            '/users/me/notification-prefs',
            data=json.dumps({
                'channel': UserNotificationChoice.EMAIL,
                'hidden_direct_message_text': 'VIP 自定义提醒',
            }),
            content_type='application/json',
            **self.user_authorization(self.member),
        )

        self.assertLess(self.member.effective_growth_level(), 10)
        self.assertEqual(updated.status_code, 200, updated.content)
        self.assertEqual(updated.json()['body']['hidden_direct_message_text'], 'VIP 自定义提醒')
        self.assertTrue(self.member.calculate_growth()['capabilities']['custom_notification_message']['available'])

    @patch('User.models.delete_chat_background_by_uri')
    def test_replacing_chat_background_removes_previous_image(self, delete_background):
        self.member.set_password('safe-password')
        self.grant_growth_level(self.member, 8)
        self.member.chat_background_theme = 'custom'
        self.member.chat_background_uri = 'https://resource.example.com/sermo/chat-background/old.jpg'
        self.member.save(update_fields=['chat_background_theme', 'chat_background_uri'])

        self.member.set_chat_background('mint')

        delete_background.assert_called_once_with(
            'https://resource.example.com/sermo/chat-background/old.jpg'
        )
        self.assertEqual(self.member.chat_background_theme, 'mint')
        self.assertEqual(self.member.chat_background_uri, '')
