from django.urls import path

from Activity.views import ActiveActivityView, ActivityClaimView, ActivityContributionView, ActivityDetailView, ActivityPersonalRewardClaimView, ActivitySpaceRewardClaimView


urlpatterns = [
    path('active', ActiveActivityView.as_view(), name='active activities'),
    path('<slug:key>', ActivityDetailView.as_view(), name='activity detail'),
    path('<slug:key>/claim', ActivityClaimView.as_view(), name='activity claim'),
    path('<slug:key>/personal-reward/claim', ActivityPersonalRewardClaimView.as_view(), name='activity personal reward claim'),
    path('<slug:key>/space-reward/claim', ActivitySpaceRewardClaimView.as_view(), name='activity space reward claim'),
    path('<slug:key>/contribute', ActivityContributionView.as_view(), name='activity contribution'),
]
