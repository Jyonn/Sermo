"""Each edition keeps its own space claims, progress and reward receipts."""
from datetime import timedelta
from hashlib import sha256

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.models import ActivityCampaign, ActivityMilestone, ActivityRewardReceipt, SpaceActivity


@Error.register
class RerunErrors:
    UNAVAILABLE = Error(_('This activity cannot be brought back right now'), code=Code.Forbidden)
    SUPERSEDED = Error(_('A later edition is available. Refresh the activity list.'), code=Code.Conflict)
    MISSING = Error(_('Activity not found'), code=Code.NotFound)
    SPACE_NOT_JOINED = Error(_('The space administrator has not activated this edition'), code=Code.Forbidden)


def edition_family(campaign):
    original_id = campaign.original_id or campaign.pk
    return ActivityCampaign.objects.filter(Q(pk=original_id) | Q(original_id=original_id))


def lock_original(campaign):
    return ActivityCampaign.objects.select_for_update().get(pk=campaign.original_id or campaign.pk)


def edition_fields(campaign):
    return {
        'edition': campaign.edition,
        'original_key': campaign.original.key if campaign.original_id else campaign.key,
    }


def rerun_status(campaign, run_count, running_count):
    if campaign.config.get('mode') == 'annual_explorer':
        return 'one_time_tasks'
    if not campaign.duration_seconds:
        return 'permanent'
    if running_count:
        return 'still_running'
    if not run_count:
        return 'awaiting_spaces' if campaign.edition > 1 else 'never_started'
    return 'available'


def platform_campaigns():
    from django.db.models import Count, Max
    now = timezone.now()
    rows = ActivityCampaign.objects.select_related('original').annotate(
        spaces=Count('space_runs'),
        running_spaces=Count('space_runs', filter=Q(space_runs__isnull=False) &
                             (Q(space_runs__ends_at__isnull=True) | Q(space_runs__ends_at__gt=now))),
        last_end=Max('space_runs__ends_at'),
    ).order_by('-created_at', '-pk')
    latest = {}
    for campaign in rows:
        latest.setdefault(campaign.original_id or campaign.pk, []).append(campaign)
    return [
        dict(
            key=campaign.key, title=campaign.title, title_en=campaign.title_en,
            summary=campaign.summary, summary_en=campaign.summary_en,
            theme=campaign.config.get('theme', ''),
            duration_seconds=campaign.duration_seconds,
            joined_spaces=campaign.spaces, active_spaces=campaign.running_spaces,
            last_end=campaign.last_end.timestamp() if campaign.last_end else None,
            status=rerun_status(campaign, campaign.spaces, campaign.running_spaces),
            previous_editions=[dict(key=prior.key, edition=prior.edition, joined_spaces=prior.spaces,
                                    last_end=prior.last_end.timestamp() if prior.last_end else None)
                               for prior in previous],
            **edition_fields(campaign),
        )
        for campaign, *previous in latest.values()
    ]


@transaction.atomic
def publish_rerun(key):
    source = ActivityCampaign.objects.filter(key=key).first()
    if source is None:
        raise RerunErrors.MISSING
    original = lock_original(source)
    family = edition_family(original)
    latest = family.order_by('-edition').first()
    if source.pk != latest.pk:
        raise RerunErrors.SUPERSEDED
    now = timezone.now()
    runs = SpaceActivity.objects.filter(campaign=latest)
    running = SpaceActivity.objects.filter(campaign_id__in=family.values('pk')).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    ).exists()
    if rerun_status(latest, runs.exists(), running) != 'available':
        raise RerunErrors.UNAVAILABLE
    edition = latest.edition + 1
    new = ActivityCampaign.objects.create(
        key=f'{original.key[:55]}-edition-{edition}-{original.pk}',
        original=original, edition=edition, title=latest.title, title_en=latest.title_en,
        summary=latest.summary, summary_en=latest.summary_en,
        assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
        duration_seconds=latest.duration_seconds, event_key=latest.event_key,
        daily_user_limit=latest.daily_user_limit, config=latest.config, enabled=True,
    )
    ActivityMilestone.objects.bulk_create([
        ActivityMilestone(campaign=new, threshold=row.threshold, resource_type=row.resource_type,
                          reward_pool=row.reward_pool, reward_label=row.reward_label)
        for row in latest.milestones.all()
    ])
    return new


def claimed_rewards(campaign, user):
    if campaign.edition == 1:
        return set(user.resource_inventory.values_list('reward_id', flat=True))
    return set(ActivityRewardReceipt.objects.filter(campaign=campaign, user=user).values_list('reward_id', flat=True))


def reward_growth(campaign, user):
    if campaign.edition == 1:
        return 0
    return ActivityRewardReceipt.objects.filter(campaign=campaign, user=user).aggregate(points=Sum('growth_points'))['points'] or 0


def record_reward(campaign, user, reward_id, already_owned):
    """Called within the claim transaction while holding the user's row lock."""
    receipt, created = ActivityRewardReceipt.objects.get_or_create(campaign=campaign, user=user, reward_id=reward_id)
    if created and already_owned:
        item_key = sha256(reward_id.encode()).hexdigest()[:32]
        receipt.growth_points = user.award_growth(f'activity:rerun:{campaign.pk}:{item_key}', notify=False)
        receipt.save(update_fields=['growth_points'])
    return receipt


@transaction.atomic
def grant_resource(campaign, user, resource_type, reward_id, resource_key, metadata=None):
    from User.models import User, UserResourceInventory
    if campaign.edition == 1:
        item, _ = UserResourceInventory.grant_activity_resource(
            user, resource_type, reward_id, resource_key, campaign.key, metadata=metadata,
        )
        return item
    User.objects.select_for_update().get(pk=user.pk)
    lookup = Q(reward_id=reward_id)
    if resource_key:
        lookup |= Q(resource_type=resource_type, resource_key=resource_key)
    owned = user.resource_inventory.filter(lookup).first()
    if ActivityRewardReceipt.objects.filter(campaign=campaign, user=user, reward_id=reward_id).exists():
        return owned
    if owned is None:
        item, created = UserResourceInventory.grant_activity_resource(
            user, resource_type, reward_id, resource_key, campaign.key, metadata=metadata,
        )
    else:
        item, created = owned, False
    record_reward(campaign, user, reward_id, already_owned=not created)
    return item
