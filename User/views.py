from django.views import View
from django.utils import timezone
from notificator import NotificatorAPIError
from smartdjango import analyse, OK

from utils import auth, function
from utils.auth import Request
from utils.qiniu import issue_avatar_upload, validate_avatar_key, avatar_uri_for_key
from utils.global_settings import notificator
from User.models import (
    NotificationPreference,
    WebPushSubscription,
    RefreshToken,
    UserGestureLockPreference,
    UserContactVerificationCode,
    UserNotificationChoice,
    UserWebReminderPreference,
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
)
from User.validators import UserErrors


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


class RefreshView(View):
    @analyse.json(AuthParams.refresh)
    def post(self, request: Request):
        return auth.refresh_login_token(request.json.refresh)


class LogoutView(View):
    @analyse.json(AuthParams.refresh)
    def post(self, request: Request):
        auth.revoke_refresh_token(request.json.refresh)
        return OK


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
        NotificationPreferenceParams.hidden_direct_message_text,
        NotificationPreferenceParams.hidden_group_message_text,
        NotificationPreferenceParams.friend_online_message_text,
        NotificationPreferenceParams.open_chat_on_tap,
    )
    def post(self, request: Request):
        _require_password_enabled(request.user)
        enabled = request.json.enabled
        hide_message_content = request.json.hide_message_content
        open_chat_on_tap = request.json.open_chat_on_tap
        pref = NotificationPreference.set_preference(
            user=request.user,
            channel=request.json.channel,
            enabled=None if enabled is None else bool(enabled),
            offline_threshold_minutes=request.json.offline_threshold_minutes,
            hide_message_content=None if hide_message_content is None else bool(hide_message_content),
            hidden_direct_message_text=request.json.hidden_direct_message_text,
            hidden_group_message_text=request.json.hidden_group_message_text,
            friend_online_message_text=request.json.friend_online_message_text,
            open_chat_on_tap=None if open_chat_on_tap is None else bool(open_chat_on_tap),
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
        UserGestureLockPreferenceParams.decoy_enabled,
        UserGestureLockPreferenceParams.decoy_pattern_hash,
        UserGestureLockPreferenceParams.decoy_salt,
        UserGestureLockPreferenceParams.lock_after_minutes,
    )
    def post(self, request: Request):
        pref = UserGestureLockPreference.ensure(request.user)
        enabled = request.json.enabled
        pattern_hash = request.json.pattern_hash
        salt = request.json.salt
        decoy_enabled = request.json.decoy_enabled
        decoy_pattern_hash = request.json.decoy_pattern_hash
        decoy_salt = request.json.decoy_salt

        if enabled is not None and bool(enabled):
            if request.user.email_verified_at is None:
                raise UserErrors.EMAIL_NOT_VERIFIED
            if not pattern_hash or not salt:
                raise UserErrors.GESTURE_LOCK_PAYLOAD_INVALID
        elif enabled is not None:
            pattern_hash = ''
            salt = ''
            decoy_enabled = False
            decoy_pattern_hash = ''
            decoy_salt = ''

        if decoy_enabled is not None and bool(decoy_enabled):
            if not pref.enabled and not (enabled is not None and bool(enabled)):
                raise UserErrors.GESTURE_LOCK_PAYLOAD_INVALID
            if not decoy_pattern_hash or not decoy_salt:
                raise UserErrors.GESTURE_LOCK_PAYLOAD_INVALID
        elif decoy_enabled is not None:
            decoy_pattern_hash = ''
            decoy_salt = ''

        pref = UserGestureLockPreference.set_preference(
            user=request.user,
            enabled=None if enabled is None else bool(enabled),
            pattern_hash=pattern_hash,
            salt=salt,
            decoy_enabled=None if decoy_enabled is None else bool(decoy_enabled),
            decoy_pattern_hash=decoy_pattern_hash,
            decoy_salt=decoy_salt,
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
        title = 'Sermo verification code'
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
        UserContactVerificationCode.verify(
            user=request.user,
            channel=channel,
            target=target,
            code=request.json.code,
        )
        request.user.bind_contact(channel=channel, target=target)
        if channel == UserNotificationChoice.EMAIL:
            NotificationPreference.set_preference(
                user=request.user,
                channel=UserNotificationChoice.EMAIL,
                enabled=True,
            )
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
