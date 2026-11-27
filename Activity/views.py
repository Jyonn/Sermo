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
        from utils.middleware import APIPacker
        payload = BgmService.current_payload(request.user)
        if payload is None:
            response = APIPacker.pack(None)
            response['Cache-Control'] = 'no-store, private'
            return response
        if 'chat_id' in request.GET:
            try:
                chat_id = int(request.GET['chat_id'])
            except (ValueError, TypeError):
                raise BgmErrors.INVALID
            BgmService.entrance_chat(request.user, chat_id)
            payload['bgm']['entrance'] = BgmService.entrance_status(request.user, chat_id)
        response = APIPacker.pack(payload)
        response['Cache-Control'] = 'no-store, private'
        return response


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
        from Activity.participation import activity_participation
        runs = ActivityService.active_campaigns_for_space(request.user.space)
        payloads = [
            ActivityService.payload(space_activity.campaign, request.user, space_activity)
            for space_activity in runs
        ]
        participants = activity_participation(runs, request.user.space_id)
        return [{**payload, 'participation': participants[run.pk]} for payload, run in zip(payloads, runs)]


class NumberDuelView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        from Activity.number_duel import NumberDuelService
        return NumberDuelService.lobby(key, request.user, request.GET.get('match_id'), request.GET.get('room_code'))

    @auth.require_user
    def post(self, request: Request, key: str):
        import json
        from Activity.number_duel import DuelErrors, NumberDuelService
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise DuelErrors.INVALID
        return NumberDuelService.act(key, request.user, data)


class LandlordView(View):
    @staticmethod
    def _response(payload):
        from utils.middleware import APIPacker
        response = APIPacker.pack(payload)
        response['Cache-Control'] = 'private, no-store'
        return response

    @auth.require_user
    def get(self, request: Request, key: str):
        from Activity.landlord import LandlordService
        return self._response(LandlordService.lobby(key, request.user, request.GET.get('code')))

    @auth.require_user
    def post(self, request: Request, key: str):
        import json
        from Activity.landlord import LandlordErrors, LandlordService
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise LandlordErrors.INVALID
        return self._response(LandlordService.act(key, request.user, data))


class LandlordLeaderboardView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        from Activity.landlord import LandlordService
        return LandlordView._response(LandlordService.leaderboard(
            key, request.user, request.GET.get('year'), request.GET.get('metric', 'points'),
        ))


class GameLeaderboardView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        from Activity.game_leaderboard import game_leaderboard
        return LandlordView._response(game_leaderboard(
            key, request.user, request.GET.get('year'), request.GET.get('metric', 'points'),
        ))


class FourPlayerView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        from Activity.four_player import lobby
        return LandlordView._response(lobby(key, request.user, request.GET.get('code')))

    @auth.require_user
    def post(self, request: Request, key: str):
        import json
        from Activity.four_player import FourPlayerErrors, act
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise FourPlayerErrors.INVALID
        return LandlordView._response(act(key, request.user, data))


class ActivityDetailView(View):
    @auth.require_user
    def get(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        ActivityService.ensure_automatic_for_space(request.user.space)
        if campaign.config.get('mode') == 'landlord':
            from Activity.landlord import LandlordService
            space_activity = LandlordService.run(key, request.user)
        else:
            from Activity.table_activity import FOUR_PLAYER_MODES, activity_run
            if campaign.config.get('mode') in FOUR_PLAYER_MODES:
                from Activity.four_player import FourPlayerErrors
                space_activity = activity_run(key, request.user, FOUR_PLAYER_MODES)
                if not space_activity:
                    raise FourPlayerErrors.CLOSED
            else:
                space_activity = ActivityService.space_activity_for(campaign, request.user.space)
        from Activity.participation import activity_participation
        payload = ActivityService.payload(campaign, request.user, space_activity)
        payload['participation'] = activity_participation([space_activity], request.user.space_id)[space_activity.pk]
        return payload


class ExplorerRewardClaimView(View):
    @auth.require_user
    def post(self, request: Request, key: str, tier: int):
        from Activity.explorer import ExplorerErrors, ExplorerService
        campaign = ActivityCampaign.objects.filter(key=key, enabled=True, config__mode=ExplorerService.MODE).first()
        if campaign is None:
            raise ExplorerErrors.INVALID
        ActivityService.ensure_automatic_for_space(request.user.space)
        run = campaign.space_runs.filter(space=request.user.space).first()
        if run is None:
            raise ExplorerErrors.INVALID
        return ExplorerService.claim(campaign, request.user, run, tier)


class ActivitySeenView(View):
    @auth.require_user
    def post(self, request: Request, key: str):
        campaign = ActivityCampaign.objects.get(key=key, enabled=True)
        space_activity = ActivityService.space_activity_for(campaign, request.user.space)
        ActivityService.mark_seen(space_activity, request.user)
        return ActivityService.payload(campaign, request.user, space_activity)


class RecordShopView(View):
    @auth.require_user
    def post(self, request: Request, key: str, action: str):
        import json
        from Activity.record_shop import RecordShopErrors, claim_singer, finish_listen, start_listen

        if len(request.body) > 8192:
            raise RecordShopErrors.INVALID
        try:
            data = json.loads(request.body)
        except (ValueError, UnicodeDecodeError):
            raise RecordShopErrors.INVALID
        if not isinstance(data, dict):
            raise RecordShopErrors.INVALID
        if action == 'listen-start':
            return start_listen(key, request.user, data)
        if action == 'listen-finish':
            return finish_listen(key, request.user, data)
        if action in {'claim', 'singer'}:
            return claim_singer(key, request.user, data.get('singer'))
        raise RecordShopErrors.INVALID


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
