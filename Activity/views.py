from django.views import View

from Activity.models import ActivityCampaign, ActivityService
from utils import auth
from utils.auth import Request


class ActiveActivityView(View):
    @auth.require_user
    def get(self, request: Request):
        return [
            ActivityService.payload(space_activity.campaign, request.user, space_activity)
            for space_activity in ActivityService.active_campaigns_for_space(request.user.space)
        ]


class ActivityDetailView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.ensure_automatic_for_space(request.user.space)
        space_activity = ActivityService.space_activity_for(campaign, request.user.space)
        return ActivityService.payload(campaign, request.user, space_activity)


class ActivityContributionView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.space_activity_for(campaign, request.user.space, active_only=True)
        ActivityService.contribute(campaign, request.user)
        return ActivityService.payload(campaign, request.user)


class ActivityClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.space_activity_for(campaign, request.user.space, active_only=True)
        ActivityService.claim(campaign, request.user)
        return ActivityService.payload(campaign, request.user)


class ActivityPersonalRewardClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.space_activity_for(campaign, request.user.space, active_only=True)
        ActivityService.claim_personal_reward(campaign, request.user)
        return ActivityService.payload(campaign, request.user)


class ActivitySpaceRewardClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.space_activity_for(campaign, request.user.space, active_only=True)
        ActivityService.claim_space_reward(campaign, request.user)
        return ActivityService.payload(campaign, request.user)


class AdminActivityListView(View):
    @auth.require_space
    def get(self, request: Request):
        return ActivityService.admin_payloads(request.space)


class AdminActivityClaimView(View):
    @auth.require_space
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.claim_for_space(campaign, request.space)
        return ActivityService.admin_payloads(request.space)
