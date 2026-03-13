from django.urls import path

from User.views import (
    HeartbeatView,
    RefreshView,
    LogoutView,
    NotificationPreferenceView,
    EmailVerificationCodeRequestView,
    EmailVerificationConfirmView,
    ContactVerificationCodeRequestView,
    ContactBindingConfirmView,
)

urlpatterns = [
    path('heartbeat', HeartbeatView.as_view(), name='heartbeat'),
    path('refresh', RefreshView.as_view(), name='token refresh'),
    path('logout', LogoutView.as_view(), name='token revoke'),
    path('me/notification-prefs', NotificationPreferenceView.as_view(), name='notification prefs'),
    path('me/email-code', EmailVerificationCodeRequestView.as_view(), name='email verification code'),
    path('me/verify-email', EmailVerificationConfirmView.as_view(), name='email verification confirm'),
    path('me/contact-code', ContactVerificationCodeRequestView.as_view(), name='contact verification code'),
    path('me/bind-contact', ContactBindingConfirmView.as_view(), name='contact bind confirm'),
]
