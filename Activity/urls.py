from django.urls import path

from Activity.views import AdminActivityClaimView, AdminActivityListView, ActiveActivityView, ActivityClaimView, ActivityContributionView, ActivityDetailView, ActivityMilestoneRewardClaimView, ActivityPersonalRewardClaimView, ActivitySpaceRewardClaimView


urlpatterns = [
    path('active', ActiveActivityView.as_view(), name='active activities'),
    path('admin', AdminActivityListView.as_view(), name='admin activities'),
    path('admin/<slug:key>/claim', AdminActivityClaimView.as_view(), name='admin activity claim'),
    path('<slug:key>', ActivityDetailView.as_view(), name='activity detail'),
    path('<slug:key>/claim', ActivityClaimView.as_view(), name='activity claim'),
    path('<slug:key>/personal-reward/claim', ActivityPersonalRewardClaimView.as_view(), name='activity personal reward claim'),
    path('<slug:key>/rewards/<slug:reward_key>/claim', ActivityMilestoneRewardClaimView.as_view(), name='activity milestone reward claim'),
    path('<slug:key>/space-reward/claim', ActivitySpaceRewardClaimView.as_view(), name='activity space reward claim'),
    path('<slug:key>/contribute', ActivityContributionView.as_view(), name='activity contribution'),
]
