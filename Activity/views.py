from django.views import View

from Activity.models import ActivityCampaign, ActivityService
from utils import auth
from utils.auth import Request


class BgmSettingsView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        import json
        from Activity.bgm import BgmErrors, BgmService
        campaign = ActivityCampaign.objects.get(key=key, config__mode=BgmService.MODE)
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise BgmErrors.INVALID
        return BgmService.configure(campaign, request.user, data)


class BgmMeView(View):
    @auth.require_user
    def get(self, request: Request):
        from Activity.bgm import BgmErrors, BgmService
        from Activity.models import BgmParticipation
        participation = BgmParticipation.objects.filter(
            progress__user=request.user, progress__campaign__enabled=True,
            progress__space_activity__space=request.user.space,
        ).select_related('progress__campaign', 'progress__space_activity').order_by('-joined_at').first()
        if participation is None:
            return None
        payload = BgmService.payload(participation.progress.campaign, request.user, participation.progress.space_activity)
        if 'chat_id' in request.GET:
            try:
                chat_id = int(request.GET['chat_id'])
            except (ValueError, TypeError):
                raise BgmErrors.INVALID
            BgmService.entrance_chat(request.user, chat_id)
            payload['bgm']['entrance'] = BgmService.entrance_status(request.user, chat_id)
        return payload


class BgmEntranceView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        import json
        from Activity.bgm import BgmErrors, BgmService
        campaign = ActivityCampaign.objects.get(key=key, enabled=True, config__mode=BgmService.MODE)
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise BgmErrors.INVALID
        return BgmService.enter(campaign, request.user, data)


class ActiveActivityView(View):
    @auth.require_user
    def get(self, request: Request):
        return [
            ActivityService.payload(space_activity.campaign, request.user, space_activity)
            for space_activity in ActivityService.active_campaigns_for_space(request.user.space)
        ]


class NumberDuelView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        from Activity.number_duel import NumberDuelService
        return NumberDuelService.lobby(key, request.user, request.GET.get('match_id'))

    @auth.require_user
    def post(self, request: Request, key: str):
        import json
        from Activity.number_duel import DuelErrors, NumberDuelService
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise DuelErrors.INVALID
        return NumberDuelService.act(key, request.user, data)


class ActivityDetailView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.ensure_automatic_for_space(request.user.space)
        space_activity = ActivityService.space_activity_for(campaign, request.user.space)
        return ActivityService.payload(campaign, request.user, space_activity)


class ActivitySeenView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        space_activity = ActivityService.space_activity_for(campaign, request.user.space)
        ActivityService.mark_seen(space_activity, request.user)
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


class ActivityMilestoneRewardClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str, reward_key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.space_activity_for(campaign, request.user.space, active_only=True)
        ActivityService.claim_milestone_reward(campaign, request.user, reward_key)
        return ActivityService.payload(campaign, request.user)


class ActivityStarryNightRewardClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.space_activity_for(campaign, request.user.space, active_only=True)
        ActivityService.claim_starry_night_reward(campaign, request.user)
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
