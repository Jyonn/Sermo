from django.views import View
from notificator import NotificatorAPIError
from smartdjango import analyse, OK

from utils import auth
from utils.auth import Request
from utils.global_settings import notificator
from User.models import (
    NotificationPreference,
    EmailVerificationCode,
    UserContactVerificationCode,
    UserNotificationChoice,
)
from User.params import (
    AuthParams,
    UserParams,
    NotificationPreferenceParams,
    EmailVerificationCodeParams,
    UserContactVerificationCodeParams,
)
from User.validators import UserErrors


class HeartbeatView(View):
    @auth.require_user
    def get(self, request: Request):
        request.user.heartbeat()
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
        prefs = NotificationPreference.ensure_defaults(request.user)
        return [pref.json() for pref in prefs]

    @auth.require_user
    @analyse.json(
        NotificationPreferenceParams.channel,
        NotificationPreferenceParams.enabled,
        NotificationPreferenceParams.offline_threshold_minutes,
    )
    def post(self, request: Request):
        enabled = request.json.enabled
        pref = NotificationPreference.set_preference(
            user=request.user,
            channel=request.json.channel,
            enabled=None if enabled is None else bool(enabled),
            offline_threshold_minutes=request.json.offline_threshold_minutes,
        )
        return pref.json()


class EmailVerificationCodeRequestView(View):
    @auth.require_user
    @analyse.json(EmailVerificationCodeParams.email)
    def post(self, request: Request):
        verify_code = EmailVerificationCode.issue(request.user, request.json.email)
        title = 'Sermo verification code'
        body = f'Your verification code is {verify_code.code}. It expires in 10 minutes.'
        try:
            notificator.mail(
                mail=verify_code.email,
                title=title,
                body=body,
                recipient_name=request.user.name,
            )
        except NotificatorAPIError as e:
            raise UserErrors.EMAIL_SEND_FAILED(details=e)
        return dict(expires_in=EmailVerificationCode.EXPIRE_SECONDS)


class EmailVerificationConfirmView(View):
    @auth.require_user
    @analyse.json(
        EmailVerificationCodeParams.email,
        EmailVerificationCodeParams.code,
        EmailVerificationCodeParams.password,
    )
    def post(self, request: Request):
        email = request.json.email
        EmailVerificationCode.verify(
            user=request.user,
            email=email,
            code=request.json.code,
        )
        request.user.verify_email_and_upgrade(
            email=email,
            password=request.json.password,
        )
        NotificationPreference.set_preference(
            user=request.user,
            channel=UserNotificationChoice.EMAIL,
            enabled=True,
        )
        return request.user.json()


class ContactVerificationCodeRequestView(View):
    @auth.require_user
    @analyse.json(
        UserContactVerificationCodeParams.channel,
        UserContactVerificationCodeParams.target,
    )
    def post(self, request: Request):
        channel = request.json.channel
        code_obj = UserContactVerificationCode.issue(
            user=request.user,
            channel=channel,
            target=request.json.target,
        )
        title = 'Sermo verification code'
        body = f'Your verification code is {code_obj.code}. It expires in 10 minutes.'
        try:
            if channel == UserNotificationChoice.EMAIL:
                notificator.mail(
                    mail=code_obj.target,
                    title=title,
                    body=body,
                    recipient_name=request.user.name,
                )
            elif channel == UserNotificationChoice.SMS:
                notificator.sms(
                    target=code_obj.target,
                    title=title,
                    body=body,
                )
            elif channel == UserNotificationChoice.BARK:
                notificator.bark(
                    target=code_obj.target,
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
        return request.user.json()


class WelcomeMessageView(View):
    @auth.require_user
    def get(self, request: Request):
        return dict(welcome_message=request.user.welcome_message)

    @auth.require_user
    @analyse.json(UserParams.welcome_message)
    def post(self, request: Request):
        request.user.set_welcome_message(request.json.welcome_message)
        return dict(welcome_message=request.user.welcome_message)
