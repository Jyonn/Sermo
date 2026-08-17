from django.urls import path

from AccessPolicy.views import UserCapabilityView


urlpatterns = [
    path('me', UserCapabilityView.as_view()),
]

