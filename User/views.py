from django.views import View
from notificator import NotificatorAPIError
from smartdjango import analyse, OK

from utils import auth, function
from utils.auth import Request
from utils.qiniu import issue_avatar_upload, validate_avatar_key, avatar_uri_for_key
from utils.global_settings import notificator
from User.models import (
    NotificationPreference,
    UserContactVerificationCode,
    UserNotificationChoice,
)
from User.params import (
    AuthParams,
    UserParams,
    UserPasswordParams,
    NotificationPreferenceParams,
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
    )
    def post(self, request: Request):
        _require_password_enabled(request.user)
        enabled = request.json.enabled
        pref = NotificationPreference.set_preference(
            user=request.user,
            channel=request.json.channel,
            enabled=None if enabled is None else bool(enabled),
            offline_threshold_minutes=request.json.offline_threshold_minutes,
        )
        return pref.json()


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
        request.user.set_welcome_message(request.json.welcome_message)
        return dict(welcome_message=request.user.welcome_message)


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
        return issue_avatar_upload(
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class AvatarCustomView(View):
    @auth.require_user
    @analyse.json(UserParams.avatar_key)
    def post(self, request: Request):
        key = validate_avatar_key(request.json.key)
        request.user.set_custom_avatar(avatar_uri_for_key(key))
        return request.user.dictify('avatar_type', 'avatar_uri')
