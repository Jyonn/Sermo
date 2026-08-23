from django.views import View

from Activity.models import ActivityCampaign, ActivityService
from utils import auth
from utils.auth import Request


class ActiveActivityView(View):
    @auth.require_user
    def get(self, request: Request):
        return [ActivityService.payload(campaign, request.user) for campaign in ActivityCampaign.active()]


class ActivityDetailView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        return ActivityService.payload(campaign, request.user)


class ActivityContributionView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.active().get(key=key)
        ActivityService.contribute(campaign, request.user)
        return ActivityService.payload(campaign, request.user)


class ActivityClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.active().get(key=key)
        ActivityService.claim(campaign, request.user)
        return ActivityService.payload(campaign, request.user)


class ActivityPersonalRewardClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.active().get(key=key)
        ActivityService.claim_personal_reward(campaign, request.user)
        return ActivityService.payload(campaign, request.user)
