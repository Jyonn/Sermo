from django.urls import path

from PlatformAdmin.views import (
    AuditLogView, ChatListView, ChatMessageView, DashboardView, EmailCodeView,
    IdentityDocumentView, IdentityReviewView, LoginView, MemberListView,
    MfaDisableView, MfaSetupView, MfaVerifyView, SpaceListView,
)

urlpatterns = [
    path('email-code', EmailCodeView.as_view()),
    path('login', LoginView.as_view()),
    path('dashboard', DashboardView.as_view()),
    path('spaces', SpaceListView.as_view()),
    path('spaces/<int:space_id>/members', MemberListView.as_view()),
    path('members/<int:user_id>/chats', ChatListView.as_view()),
    path('chats/<int:chat_id>/messages', ChatMessageView.as_view()),
    path('identity/<int:space_id>/document', IdentityDocumentView.as_view()),
    path('identity/<int:space_id>/review', IdentityReviewView.as_view()),
    path('mfa/setup', MfaSetupView.as_view()),
    path('mfa/verify', MfaVerifyView.as_view()),
    path('mfa/disable', MfaDisableView.as_view()),
    path('audit', AuditLogView.as_view()),
]
