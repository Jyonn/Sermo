from django.urls import path

from User.views import (
    HeartbeatView,
    UserMeView,
    RefreshView,
    LogoutView,
    NotificationPreferenceView,
    PasswordView,
    ContactVerificationCodeRequestView,
    ContactBindingConfirmView,
    WelcomeMessageView,
    UserNameView,
    AvatarPresetView,
    AvatarCustomUploadView,
    AvatarCustomView,
)

urlpatterns = [
    path('heartbeat', HeartbeatView.as_view(), name='heartbeat'),
    path('me', UserMeView.as_view(), name='user me'),
    path('refresh', RefreshView.as_view(), name='token refresh'),
    path('logout', LogoutView.as_view(), name='token revoke'),
    path('me/notification-prefs', NotificationPreferenceView.as_view(), name='notification prefs'),
    path('me/password', PasswordView.as_view(), name='password'),
    path('me/contact-code', ContactVerificationCodeRequestView.as_view(), name='contact verification code'),
    path('me/bind-contact', ContactBindingConfirmView.as_view(), name='contact bind confirm'),
    path('me/name', UserNameView.as_view(), name='user name'),
    path('me/welcome-message', WelcomeMessageView.as_view(), name='welcome message'),
    path('me/avatar/preset', AvatarPresetView.as_view(), name='avatar preset'),
    path('me/avatar/custom/upload', AvatarCustomUploadView.as_view(), name='avatar custom upload'),
    path('me/avatar/custom', AvatarCustomView.as_view(), name='avatar custom'),
]
