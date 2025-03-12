from django.urls import path

from User.views import HostLoginView, GuestLoginView, HeartbeatView

urlpatterns = [
    path('host', HostLoginView.as_view(), name='host login'),
    path('guest', GuestLoginView.as_view(), name='guest login'),
    path('heartbeat', HeartbeatView.as_view(), name='heartbeat'),
]
