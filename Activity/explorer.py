"""Annual, space-scoped exploration. All evidence and reward ordering are server-owned."""
from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.models import ActivityEvent, SpaceActivity, UserActivityProgress, UserActivityReward
from Activity.presentation import presentation
from User.models import User, UserResourceInventory


@Error.register
class ExplorerErrors:
    CLOSED = Error(_('This exploration activity has ended or has not started'), code=Code.Forbidden)
    NOT_READY = Error(_('Complete another theme and claim earlier rewards first'), code=Code.Forbidden)
    INVALID = Error(_('Invalid exploration reward'), code=Code.BadRequest)


class ExplorerService:
    MODE = 'annual_explorer'
    YEAR = 2026
    START = datetime(2026, 10, 30, tzinfo=ZoneInfo('Asia/Shanghai'))
    END = datetime(2027, 1, 1, tzinfo=ZoneInfo('Asia/Shanghai'))
    THEMES = ('basic', 'security', 'apps', 'square', 'activity')
    REWARDS = (
        ('frame', 'activity.explorer.2026.frame', 'explorer-conan', 'uncommon'),
        ('bubble', 'activity.explorer.2026.bubble', 'explorer-ai', 'rare'),
        ('background', 'activity.explorer.2026.background', 'explorer-duality', 'epic'),
        ('profile', 'activity.explorer.2026.profile', 'explorer-romance', 'legendary'),
        ('identity', 'identity.explorer.2026', 'explorer-2026', 'legendary'),
    )

    @staticmethod
    def chronelle_bound(user):
        # Inspect only the locally verified native binding; never refresh tokens
        # or call the provider from activity banners/status polling.
        from User.chronelle_account import BindingFailure, config, unpack, require_ready
        from User.models import ChronelleAccount, ChronelleCredential
        import hashlib
        row = ChronelleAccount.objects.filter(user=user).first()
        if row is None:
            return False
        try:
            value = unpack(row)
            binding = value.get('binding') or {}
            if value.get('protocol') != 'oauth-v1' or not binding.get('subject') or binding.get('client_id') != config()[0]:
                return False
            require_ready(value)
            key = hashlib.sha256((binding['client_id']+'\0'+binding['subject']).encode()).hexdigest()
            shared = ChronelleCredential.objects.filter(pk=key).first()
            if shared is not None:
                require_ready(unpack(shared))
            return True
        except (BindingFailure, KeyError, TypeError):
            return False

    @classmethod
    def completed_other_activity(cls, user, campaign):
        from User.growth import PERMANENT_VIP_RESOURCES
        owned = set(user.resource_inventory.values_list('reward_id', flat=True))
        for run in SpaceActivity.objects.filter(space_id=user.space_id).exclude(campaign=campaign).select_related('campaign').prefetch_related('campaign__milestones', 'rewards'):
            other = run.campaign
            config = other.config or {}
            mode = config.get('mode')
            if config.get('theme') == 'permanent-vip':
                if {item[1] for item in PERMANENT_VIP_RESOURCES}.issubset(owned):
                    return True
                continue
            rewards = config.get('user_rewards') or ([config['reward']] if config.get('reward') else [])
            if rewards:
                required = {reward.get('reward_id') for reward in rewards}
                if required and None not in required and required.issubset(owned):
                    return True
                continue
            if mode == 'bgm_collection':
                from Activity.bgm import BgmService
                progress = UserActivityProgress.objects.filter(user=user, space_activity=run).first()
                if progress and hasattr(progress, 'bgm_participation') and len(BgmService.catalog(other)) == 12:
                    points, days, _ = BgmService.stats(progress)
                    if points >= BgmService.GATES[-1][1] and days >= BgmService.GATES[-1][0]:
                        return True
                continue
            # Rewardless games/previews do not count. Pool alternatives are not
            # separate tiers: require each actual milestone and personal reward.
            milestones = list(other.milestones.all())
            if not milestones:
                continue
            space_rewards = list(run.rewards.all())
            if {item.id for item in milestones} != {item.milestone_id for item in space_rewards}:
                continue
            if not {item.reward_id for item in space_rewards}.issubset(owned):
                continue
            personal_required = bool(config.get('personal_reward_pool'))
            personal = UserActivityReward.objects.filter(progress__user=user, progress__space_activity=run, claimed_at__isnull=False).first()
            if not personal_required or personal and personal.reward_id in owned:
                return True
        return False

    @classmethod
    def evidence(cls, user, campaign):
        from Chat.models import Submission
        from Square.models import Statement
        from Message.models import WelcomeMessageTemplate
        return {
            'basic': [('welcome', WelcomeMessageTemplate.has_custom_for(user)), ('signature', bool((user.signature or '').strip()))],
            'security': [('phone', bool(user.phone and user.phone_verified_at)), ('email', bool(user.email and user.email_verified_at))],
            'apps': [('chronelle', cls.chronelle_bound(user)), ('checkin', user.map_checkins.exists())],
            'square': [('publish', Statement.objects.filter(user=user, space_id=user.space_id, is_deleted=False, created_at__gte=cls.START, created_at__lt=cls.END).exists() or Submission.objects.filter(author=user, chat__space_id=user.space_id, submitted_at__gte=cls.START, submitted_at__lt=cls.END).exists())],
            'activity': [('complete_activity', cls.completed_other_activity(user, campaign))],
        }

    @classmethod
    def refresh(cls, campaign, user, run):
        if run.space_id != user.space_id or run.campaign_id != campaign.id or user.is_deleted:
            raise ExplorerErrors.INVALID
        progress, _ = UserActivityProgress.objects.get_or_create(campaign=campaign, user=user, defaults={'space_activity': run})
        completed = set(progress.events.filter(event_key='explorer.theme').values_list('event_reference', flat=True))
        checks = cls.evidence(user, campaign)
        if campaign.enabled and run.is_active() and cls.START <= timezone.now() < cls.END:
            for theme, items in checks.items():
                if theme not in completed and all(done for _, done in items):
                    ActivityEvent.objects.get_or_create(campaign=campaign, progress=progress, event_key='explorer.theme', event_date=cls.START.date(), event_reference=theme, defaults={'points': 0, 'claimed_at': timezone.now()})
                    completed.add(theme)
            count = len(completed.intersection(cls.THEMES))
            if progress.earned_points != count:
                progress.earned_points = count
                progress.save(update_fields=['earned_points', 'updated_at'])
        return completed, checks

    @classmethod
    def payload(cls, campaign, user, run):
        completed, checks = cls.refresh(campaign, user, run)
        owned = set(user.resource_inventory.values_list('reward_id', flat=True))
        count = len(completed.intersection(cls.THEMES))
        active = campaign.enabled and run.is_active() and cls.START <= timezone.now() < cls.END
        rewards = [dict(tier=index, resource_type=kind, reward_id=reward_id, resource_key=key, rarity=rarity, claimed=reward_id in owned,
                        unlocked=count >= index, claimable=active and count >= index and reward_id not in owned and all(previous[1] in owned for previous in cls.REWARDS[:index-1]))
                   for index, (kind, reward_id, key, rarity) in enumerate(cls.REWARDS, 1)]
        return dict(key=campaign.key, title=campaign.title, title_en=campaign.title_en, summary=campaign.summary, summary_en=campaign.summary_en,
                    starts_at=cls.START.timestamp(), ends_at=cls.END.timestamp(), active=active, assignment_mode=campaign.assignment_mode,
                    duration_seconds=None, theme='explorer', presentation=presentation(campaign.config), newly_claimed=False,
                    verified=user.verified, today_earned=False, claimable_points=0, available_points=0, contributed_points=0,
                    personal_event_count=count, personal_event_target=5, personal_reward_claimable=False, personal_reward=None,
                    space_reward_claimable=None, official_user=None, space_total=0, target=5, milestones=[], awakenings=[],
                    explorer=dict(year=cls.YEAR, completed_count=count, rewards=rewards,
                                  themes=[dict(key=theme, completed=theme in completed, checks=[dict(key=key, completed=theme in completed or done) for key, done in checks[theme]]) for theme in cls.THEMES]))

    @classmethod
    def claim(cls, campaign, user, run, tier):
        if type(tier) is not int or not 1 <= tier <= len(cls.REWARDS):
            raise ExplorerErrors.INVALID
        with transaction.atomic():
            current = User.objects.select_for_update().get(pk=user.pk)
            completed, _ = cls.refresh(campaign, current, run)
            kind, reward_id, key, rarity = cls.REWARDS[tier-1]
            owned = set(current.resource_inventory.values_list('reward_id', flat=True))
            if reward_id in owned:
                return cls.payload(campaign, current, run)
            if not campaign.enabled or not run.is_active() or not cls.START <= timezone.now() < cls.END:
                raise ExplorerErrors.CLOSED
            if len(completed.intersection(cls.THEMES)) < tier or not all(previous[1] in owned for previous in cls.REWARDS[:tier-1]):
                raise ExplorerErrors.NOT_READY
            UserResourceInventory.grant_activity_resource(current, kind, reward_id, key, campaign.key, metadata={'year': cls.YEAR, 'tier': tier, 'rarity': rarity})
            return cls.payload(campaign, current, run)
