from django.urls import path

from Message.views import MessageView

urlpatterns = [
    path('', MessageView.as_view(), name='message'),
]
