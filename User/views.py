from django.views import View
from django.utils import timezone
from notificator import NotificatorAPIError
from smartdjango import analyse, OK

from utils import auth, function
from utils.auth import Request
from utils.qiniu import (
    issue_avatar_upload,
    validate_avatar_key,
    avatar_uri_for_key,
    issue_chat_background_upload,
    validate_chat_background_key,
)
from utils.global_settings import notificator
from User.models import (
    NotificationPreference,
    WebPushSubscription,
    RefreshToken,
    UserGestureLockPreference,
    UserContactVerificationCode,
    UserPasswordRecoveryChallenge,
    UserNotificationChoice,
    UserWebReminderPreference,
    AccountSwitchTicket,
    UserEmojiUsage,
    PermanentVipCampaign,
)
from User.params import (
    AuthParams,
    UserParams,
    UserDeleteParams,
    UserPasswordParams,
    NotificationPreferenceParams,
    UserGestureLockPreferenceParams,
    UserWebReminderPreferenceParams,
    WebPushSubscriptionParams,
    UserContactVerificationCodeParams,
    UserGrowthEventParams,
    UserGrowthAcknowledgementParams,
    UserPrivateAccountParams,
    UserContactUnbindParams,
    UserPasswordRecoveryParams,
)
from User.validators import UserErrors
from Space.models import Space


def _require_password_enabled(user):
    if not user.has_password:
        raise UserErrors.PASSWORD_NOT_SET


class HeartbeatView(View):
    @auth.require_user
    def get(self, request: Request):
        request.user.heartbeat()
        return OK


class UserMeView(View):
    @auth.require_user
    def get(self, request: Request):
        return request.user.json_me()

    @auth.require_user
    @analyse.json(
        UserDeleteParams.password,
        UserDeleteParams.name_confirmation,
    )
    def delete(self, request: Request):
        user = request.user
        if user.has_password:
            password = request.json.password
            if not password:
                raise UserErrors.ACCOUNT_DELETE_PASSWORD_REQUIRED
            if not function.verify_password(password, user.salt, user.password):
                raise UserErrors.PASSWORD_ERROR
        else:
            name_confirmation = request.json.name_confirmation
            if not name_confirmation:
                raise UserErrors.ACCOUNT_DELETE_NAME_CONFIRMATION_REQUIRED
            if name_confirmation != user.name:
                raise UserErrors.ACCOUNT_DELETE_NAME_CONFIRMATION_MISMATCH

        user.remove()
        RefreshToken.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())
        return OK


class UserEmojiUsageView(View):
    @auth.require_user
    def get(self, request: Request):
        return UserEmojiUsage.top_for_user(request.user, limit=50)


class UserGrowthEventView(View):
    EVENTS = {
        'install_webapp': ('explore:install_webapp', 60, '安装 WebApp'),
        'plaza_friend': ('social:plaza_friend', 30, '从广场认识朋友'),
    }

    @auth.require_user
    @analyse.json(UserGrowthEventParams.event)
    def post(self, request: Request):
        key, points, title = self.EVENTS[request.json.event]
        awarded = request.user.award_growth(key, points, category='explore', title=title)
        return dict(awarded=awarded, growth=request.user.calculate_growth())


class UserGrowthAcknowledgementView(View):
    @auth.require_user
    def get(self, request: Request):
        return request.user.calculate_growth()

    @auth.require_user
    @analyse.json(UserGrowthAcknowledgementParams.level)
    def post(self, request: Request):
        return request.user.acknowledge_growth_level(request.json.level)


class PermanentVipCampaignView(View):
    @auth.require_user
    def get(self, request: Request):
        return PermanentVipCampaign.status_for(request.user)

    @auth.require_user
    def post(self, request: Request):
        return PermanentVipCampaign.claim_for(request.user)


class RefreshView(View):
    @analyse.json(AuthParams.refresh)
    def post(self, request: Request):
        return auth.refresh_login_token(request.json.refresh)


class LogoutView(View):
    @analyse.json(AuthParams.refresh)
    def post(self, request: Request):
        auth.revoke_refresh_token(request.json.refresh)
        return OK


class AccountSwitchListView(View):
    @auth.require_user
    def get(self, request: Request):
        return [
            dict(
                user=target.tiny_json(),
                space=target.space.json(),
            )
            for target in AccountSwitchTicket.available_targets(request.user)
        ]


class AccountSwitchTicketView(View):
    @auth.require_user
    @analyse.json(AuthParams.account_user_id)
    def post(self, request: Request):
        ticket = AccountSwitchTicket.issue(request.user, request.json.user_id)
        return dict(
            token=ticket.token,
            expires_in=AccountSwitchTicket.EXPIRE_SECONDS,
            space=ticket.target_user.space.json(),
        )


class AccountSwitchExchangeView(View):
    @analyse.json(AuthParams.switch_ticket)
    def post(self, request: Request):
        user = AccountSwitchTicket.exchange(request.json.ticket)
        user.log_login()
        return dict(space=user.space.json(), auth=auth.get_login_token(user))


class PrivateAccountView(View):
    @auth.require_user
    @analyse.json(UserPrivateAccountParams.enabled)
    def post(self, request: Request):
        request.user.set_private_account(bool(request.json.enabled))
        return request.user.json_me()


class NotificationPreferenceView(View):
    @auth.require_user
    def get(self, request: Request):
        _require_password_enabled(request.user)
        prefs = NotificationPreference.ensure_defaults(request.user)
        return [pref.json() for pref in prefs]

    @auth.require_user
    @analyse.json(
        NotificationPreferenceParams.channel,
        NotificationPreferenceParams.enabled,
        NotificationPreferenceParams.offline_threshold_minutes,
        NotificationPreferenceParams.hide_message_content,
        NotificationPreferenceParams.hidden_direct_message_title,
        NotificationPreferenceParams.hidden_direct_message_text,
        NotificationPreferenceParams.hidden_group_message_title,
        NotificationPreferenceParams.hidden_group_message_text,
        NotificationPreferenceParams.friend_online_message_title,
        NotificationPreferenceParams.friend_online_message_text,
        NotificationPreferenceParams.open_chat_on_tap,
        NotificationPreferenceParams.bark_icon_mode,
    )
    def post(self, request: Request):
        _require_password_enabled(request.user)
        enabled = request.json.enabled
        hide_message_content = request.json.hide_message_content
        open_chat_on_tap = request.json.open_chat_on_tap
        custom_message_values = (
            request.json.hidden_direct_message_title,
            request.json.hidden_direct_message_text,
            request.json.hidden_group_message_title,
            request.json.hidden_group_message_text,
            request.json.friend_online_message_title,
            request.json.friend_online_message_text,
        )
        if any(value is not None for value in custom_message_values):
            request.user.require_growth_capability('custom_notification_message')
        pref = NotificationPreference.set_preference(
            user=request.user,
            channel=request.json.channel,
            enabled=None if enabled is None else bool(enabled),
            offline_threshold_minutes=request.json.offline_threshold_minutes,
            hide_message_content=None if hide_message_content is None else bool(hide_message_content),
            hidden_direct_message_title=request.json.hidden_direct_message_title,
            hidden_direct_message_text=request.json.hidden_direct_message_text,
            hidden_group_message_title=request.json.hidden_group_message_title,
            hidden_group_message_text=request.json.hidden_group_message_text,
            friend_online_message_title=request.json.friend_online_message_title,
            friend_online_message_text=request.json.friend_online_message_text,
            open_chat_on_tap=None if open_chat_on_tap is None else bool(open_chat_on_tap),
            bark_icon_mode=request.json.bark_icon_mode,
        )
        return pref.json()


class UserWebReminderPreferenceView(View):
    @auth.require_user
    def get(self, request: Request):
        return UserWebReminderPreference.ensure(request.user).json()

    @auth.require_user
    @analyse.json(
        UserWebReminderPreferenceParams.sound_enabled,
        UserWebReminderPreferenceParams.title_enabled,
    )
    def post(self, request: Request):
        pref = UserWebReminderPreference.set_preference(
            user=request.user,
            sound_enabled=None if request.json.sound_enabled is None else bool(request.json.sound_enabled),
            title_enabled=None if request.json.title_enabled is None else bool(request.json.title_enabled),
        )
        return pref.json()


class UserGestureLockPreferenceView(View):
    @auth.require_user
    def get(self, request: Request):
        return UserGestureLockPreference.ensure(request.user).json()

    @auth.require_user
    @analyse.json(
        UserGestureLockPreferenceParams.enabled,
        UserGestureLockPreferenceParams.pattern_hash,
        UserGestureLockPreferenceParams.salt,
        UserGestureLockPreferenceParams.lock_after_minutes,
    )
    def post(self, request: Request):
        pref = UserGestureLockPreference.ensure(request.user)
        enabled = request.json.enabled
        pattern_hash = request.json.pattern_hash
        salt = request.json.salt
        if enabled is not None and bool(enabled):
            if request.user.email_verified_at is None:
                raise UserErrors.EMAIL_NOT_VERIFIED
            if not pattern_hash or not salt:
                raise UserErrors.GESTURE_LOCK_PAYLOAD_INVALID
        elif enabled is not None:
            pattern_hash = ''
            salt = ''

        pref = UserGestureLockPreference.set_preference(
            user=request.user,
            enabled=None if enabled is None else bool(enabled),
            pattern_hash=pattern_hash,
            salt=salt,
            lock_after_minutes=request.json.lock_after_minutes,
        )
        return pref.json()


class WebPushSubscriptionView(View):
    @auth.require_user
    def get(self, request: Request):
        from utils.webpush import vapid_public_key

        return dict(
            public_key=vapid_public_key(),
            subscriptions=[item.json() for item in WebPushSubscription.active_for_user(request.user)],
        )

    @auth.require_user
    @analyse.json(
        WebPushSubscriptionParams.endpoint,
        WebPushSubscriptionParams.p256dh,
        WebPushSubscriptionParams.auth,
        WebPushSubscriptionParams.origin,
    )
    def post(self, request: Request):
        subscription = WebPushSubscription.register(
            user=request.user,
            endpoint=request.json.endpoint,
            p256dh=request.json.p256dh,
            auth=request.json.auth,
            origin=request.json.origin,
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
        return subscription.json()

    @auth.require_user
    @analyse.json(WebPushSubscriptionParams.endpoint)
    def delete(self, request: Request):
        WebPushSubscription.objects.filter(user=request.user, endpoint=request.json.endpoint).delete()
        return OK


class PasswordView(View):
    @auth.require_user
    @analyse.json(
        UserPasswordParams.old_password,
        UserPasswordParams.new_password,
    )
    def post(self, request: Request):
        user = request.user
        if user.has_password:
            old_password = request.json.old_password
            if not old_password:
                raise UserErrors.OLD_PASSWORD_REQUIRED
            if not function.verify_password(old_password, user.salt, user.password):
                raise UserErrors.PASSWORD_ERROR
        user.set_password(request.json.new_password)
        return dict(has_password=user.has_password)


class PasswordRecoveryLookupView(View):
    @analyse.json(
        UserPasswordRecoveryParams.slug,
        UserPasswordRecoveryParams.name,
    )
    def post(self, request: Request):
        space = Space.get_by_slug(request.json.slug)
        user = UserPasswordRecoveryChallenge.find_user(space, request.json.name)
        return dict(channels=UserPasswordRecoveryChallenge.recovery_channels(user))


class PasswordRecoveryCodeView(View):
    @analyse.json(
        UserPasswordRecoveryParams.slug,
        UserPasswordRecoveryParams.name,
        UserPasswordRecoveryParams.channel,
    )
    def post(self, request: Request):
        space = Space.get_by_slug(request.json.slug)
        user = UserPasswordRecoveryChallenge.find_user(space, request.json.name)
        challenge = UserPasswordRecoveryChallenge.issue(user, request.json.channel)
        expire_minutes = UserPasswordRecoveryChallenge.CODE_EXPIRE_SECONDS // 60
        title = 'Sermo 言浪密码找回'
        body = f'Your verification code is {challenge.code}. It expires in {expire_minutes} minutes.'
        try:
            if challenge.channel == UserNotificationChoice.EMAIL:
                notificator.mail(
                    challenge.target,
                    title=title,
                    body=body,
                    recipient_name=user.name,
                )
            elif challenge.channel == UserNotificationChoice.SMS:
                notificator.sms(
                    challenge.target,
                    title=title,
                    body=dict(code=challenge.code, time=expire_minutes),
                )
            else:
                raise UserErrors.PASSWORD_RECOVERY_CHANNEL_INVALID
        except NotificatorAPIError as error:
            challenge.used_at = timezone.now()
            challenge.save(update_fields=['used_at'])
            raise UserErrors.CONTACT_SEND_FAILED(details=error)
        return dict(
            challenge_id=challenge.id,
            expires_in=UserPasswordRecoveryChallenge.CODE_EXPIRE_SECONDS,
        )


class PasswordRecoveryVerifyView(View):
    @analyse.json(
        UserPasswordRecoveryParams.challenge_id,
        UserPasswordRecoveryParams.code,
    )
    def post(self, request: Request):
        challenge = UserPasswordRecoveryChallenge.verify_code(
            request.json.challenge_id,
            request.json.code,
        )
        return dict(
            reset_token=challenge.reset_token,
            expires_in=UserPasswordRecoveryChallenge.RESET_EXPIRE_SECONDS,
        )


class PasswordRecoveryResetView(View):
    @analyse.json(
        UserPasswordRecoveryParams.reset_token,
        UserPasswordRecoveryParams.new_password,
    )
    def post(self, request: Request):
        UserPasswordRecoveryChallenge.reset_password(
            request.json.reset_token,
            request.json.new_password,
        )
        return OK


class ContactVerificationCodeRequestView(View):
    @auth.require_user
    @analyse.json(
        UserContactVerificationCodeParams.channel,
        UserContactVerificationCodeParams.target,
    )
    def post(self, request: Request):
        _require_password_enabled(request.user)
        channel = request.json.channel
        code_obj = UserContactVerificationCode.issue(
            user=request.user,
            channel=channel,
            target=request.json.target,
        )
        title = 'Sermo 言浪验证码'
        expire_minutes = UserContactVerificationCode.EXPIRE_SECONDS // 60
        body = f'Your verification code is {code_obj.code}. It expires in {expire_minutes} minutes.'
        try:
            if channel == UserNotificationChoice.EMAIL:
                notificator.mail(
                    code_obj.target,
                    title=title,
                    body=body,
                    recipient_name=request.user.name,
                )
            elif channel == UserNotificationChoice.SMS:
                notificator.sms(
                    code_obj.target,
                    title=title,
                    body=dict(
                        code=code_obj.code,
                        time=expire_minutes,
                    )
                )
            elif channel == UserNotificationChoice.BARK:
                notificator.bark(
                    code_obj.target,
                    title=title,
                    body=body,
                )
            else:
                raise UserErrors.CONTACT_CHANNEL_INVALID
        except NotificatorAPIError as e:
            raise UserErrors.CONTACT_SEND_FAILED(details=e)
        return dict(expires_in=UserContactVerificationCode.EXPIRE_SECONDS)


class ContactBindingConfirmView(View):
    @auth.require_user
    @analyse.json(
        UserContactVerificationCodeParams.channel,
        UserContactVerificationCodeParams.target,
        UserContactVerificationCodeParams.code,
    )
    def post(self, request: Request):
        _require_password_enabled(request.user)
        channel = request.json.channel
        target = request.json.target
        verification = UserContactVerificationCode.verify(
            user=request.user,
            channel=channel,
            target=target,
            code=request.json.code,
        )
        request.user.bind_contact(channel=channel, target=verification.target)
        if channel == UserNotificationChoice.EMAIL:
            NotificationPreference.set_preference(
                user=request.user,
                channel=UserNotificationChoice.EMAIL,
                enabled=True,
            )
        return request.user.json_me()


class ContactUnbindView(View):
    @auth.require_user
    @analyse.json(
        UserContactUnbindParams.channel,
        UserContactUnbindParams.code,
    )
    def delete(self, request: Request):
        channel = request.json.channel
        verification = None
        if channel in (UserNotificationChoice.EMAIL, UserNotificationChoice.SMS):
            target = request.user.email if channel == UserNotificationChoice.EMAIL else request.user.phone
            if not target:
                raise UserErrors.CONTACT_NOT_BOUND
            verification = UserContactVerificationCode.verify(
                user=request.user,
                channel=channel,
                target=target,
                code=request.json.code,
            )
        request.user.unbind_contact(channel, verification=verification)
        return request.user.json_me()


class WelcomeMessageView(View):
    @auth.require_user
    def get(self, request: Request):
        return dict(welcome_message=request.user.welcome_message)

    @auth.require_user
    @analyse.json(UserParams.welcome_message)
    def post(self, request: Request):
        _require_password_enabled(request.user)
        request.user.set_welcome_message(request.json.welcome_message)
        return dict(welcome_message=request.user.welcome_message)


class PlazaGreetingView(View):
    @auth.require_user
    def get(self, request: Request):
        return dict(plaza_greeting=request.user.plaza_greeting)

    @auth.require_user
    @analyse.json(UserParams.plaza_greeting)
    def post(self, request: Request):
        _require_password_enabled(request.user)
        request.user.set_plaza_greeting(request.json.plaza_greeting)
        return dict(plaza_greeting=request.user.plaza_greeting)


class UserNameView(View):
    @auth.require_user
    @analyse.json(UserParams.name)
    def post(self, request: Request):
        _require_password_enabled(request.user)
        request.user.set_name(request.json.name)
        return request.user.json_me()


class AvatarPresetView(View):
    @auth.require_user
    @analyse.json(UserParams.avatar_preset_id)
    def post(self, request: Request):
        request.user.set_preset_avatar(request.json.avatar_preset_id)
        return request.user.dictify('avatar_type', 'avatar_uri')


class AvatarCustomUploadView(View):
    @auth.require_user
    @analyse.json(
        UserParams.avatar_file_name,
        UserParams.avatar_content_type,
    )
    def post(self, request: Request):
        _require_password_enabled(request.user)
        request.user.require_growth_capability('custom_avatar')
        return issue_avatar_upload(
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class AvatarCustomView(View):
    @auth.require_user
    @analyse.json(UserParams.avatar_key)
    def post(self, request: Request):
        _require_password_enabled(request.user)
        key = validate_avatar_key(request.json.key)
        request.user.set_custom_avatar(avatar_uri_for_key(key))
        return request.user.dictify('avatar_type', 'avatar_uri')


class ChatBackgroundUploadView(View):
    @auth.require_user
    @analyse.json(
        UserParams.avatar_file_name,
        UserParams.avatar_content_type,
    )
    def post(self, request: Request):
        request.user.require_growth_capability('chat_background')
        return issue_chat_background_upload(
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class ChatBackgroundView(View):
    @auth.require_user
    @analyse.json(
        UserParams.chat_background_theme,
        UserParams.chat_background_key,
    )
    def post(self, request: Request):
        theme = request.json.chat_background_theme
        uri = ''
        if theme == 'custom':
            key = validate_chat_background_key(request.json.chat_background_key)
            uri = avatar_uri_for_key(key)
        request.user.set_chat_background(theme, uri)
        return request.user.json_me()


class UserPersonalizationView(View):
    @auth.require_user
    @analyse.json(
        UserParams.chat_bubble_style,
        UserParams.avatar_frame_style,
        UserParams.square_outfit_style,
        UserParams.square_prop_style,
        UserParams.square_motion_style,
        UserParams.square_limb_style,
    )
    def post(self, request: Request):
        request.user.set_personalization(
            chat_bubble_style=request.json.chat_bubble_style,
            avatar_frame_style=request.json.avatar_frame_style,
            square_outfit_style=request.json.square_outfit_style,
            square_prop_style=request.json.square_prop_style,
            square_motion_style=request.json.square_motion_style,
            square_limb_style=request.json.square_limb_style,
        )
        return request.user.json_me()
