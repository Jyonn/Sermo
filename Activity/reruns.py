"""Each edition keeps its own space claims, progress and reward receipts."""
from calendar import monthrange
from datetime import datetime, time
from hashlib import sha256
from zoneinfo import ZoneInfo

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from smartdjango import Code, Error

from Activity.models import ActivityCampaign, ActivityMilestone, ActivityRerunRequest, ActivityRewardReceipt, ActivityService, SpaceActivity


SHANGHAI = ZoneInfo('Asia/Shanghai')


@Error.register
class RerunErrors:
    UNAVAILABLE = Error(_('This activity cannot be brought back right now'), code=Code.Forbidden)
    SUPERSEDED = Error(_('This application is no longer pending. Refresh the list.'), code=Code.Conflict)
    MISSING = Error(_('Activity not found'), code=Code.NotFound)
    INVALID = Error(_('Invalid activity rerun request'), code=Code.BadRequest)
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


def anniversary(end, months):
    day = end.astimezone(SHANGHAI).date()
    year, month_index = divmod(day.year * 12 + day.month - 1 + months, 12)
    month = month_index + 1
    number = min(day.day, monthrange(year, month)[1])
    return datetime.combine(day.replace(year=year, month=month, day=number), time.min, tzinfo=SHANGHAI)


def rerun_info(run, now=None):
    campaign = run.campaign
    now = now or timezone.now()
    if not campaign.enabled or not campaign.duration_seconds or not run.ends_at or run.ends_at > now:
        return None
    if campaign.config.get('mode') == 'annual_explorer':
        return None
    can_apply = anniversary(run.ends_at, 3)
    automatic = anniversary(run.ends_at, 6)
    request = getattr(run, 'rerun_request', None)
    if now >= automatic:
        status = 'ready'
    elif request and request.status == ActivityRerunRequest.Status.APPROVED:
        status = 'ready'
    elif now < can_apply:
        status = 'waiting'
    else:
        status = request.status if request else 'eligible'
    return dict(status=status, next_edition=campaign.edition + 1,
                apply_at=can_apply.timestamp(), auto_at=automatic.timestamp())


def _previous_for_update(key, space):
    source = ActivityCampaign.objects.filter(key=key, enabled=True).first()
    if source is None:
        raise RerunErrors.MISSING
    original = lock_original(source)
    runs = SpaceActivity.objects.filter(space=space, campaign_id__in=edition_family(original).values('pk'))
    previous = runs.select_for_update().order_by('-campaign__edition').first()
    if previous is None:
        raise RerunErrors.UNAVAILABLE
    return original, previous


@transaction.atomic
def apply_for_rerun(key, space):
    from Activity.eligibility import ActivityEligibilityErrors, missing_activity_requirements
    _, previous = _previous_for_update(key, space)
    if previous.campaign.key != key:
        raise RerunErrors.SUPERSEDED
    info = rerun_info(previous)
    if info is None or info['status'] not in ('eligible', 'pending', 'rejected'):
        raise RerunErrors.UNAVAILABLE
    if missing_activity_requirements(previous.campaign, space):
        raise ActivityEligibilityErrors.UNAVAILABLE
    if info['status'] != 'pending':
        ActivityRerunRequest.objects.update_or_create(previous_run=previous, defaults={
            'status': ActivityRerunRequest.Status.PENDING, 'submitted_at': timezone.now(), 'reviewed_at': None,
        })


def _edition_for(original, source):
    edition = source.edition + 1
    existing = original.reruns.filter(edition=edition).first()
    if existing:
        return existing
    new = ActivityCampaign.objects.create(
        key=f'{original.key[:55]}-edition-{edition}-{original.pk}',
        original=original, edition=edition, title=source.title, title_en=source.title_en,
        summary=source.summary, summary_en=source.summary_en,
        assignment_mode=ActivityCampaign.AssignmentMode.MANUAL,
        duration_seconds=source.duration_seconds, event_key=source.event_key,
        daily_user_limit=source.daily_user_limit, config=source.config, enabled=True,
    )
    ActivityMilestone.objects.bulk_create([
        ActivityMilestone(campaign=new, threshold=row.threshold, resource_type=row.resource_type,
                          reward_pool=row.reward_pool, reward_label=row.reward_label)
        for row in source.milestones.all()
    ])
    return new


@transaction.atomic
def activate_rerun(key, space):
    original, previous = _previous_for_update(key, space)
    if previous.campaign.key != key:
        source = edition_family(original).get(key=key)
        if previous.campaign.edition == source.edition + 1:
            return previous  # A retried activation must preserve the original start.
        raise RerunErrors.SUPERSEDED
    info = rerun_info(previous)
    if info is None or info['status'] != 'ready':
        raise RerunErrors.UNAVAILABLE
    return ActivityService.claim_for_space(_edition_for(original, previous.campaign), space, rerun_authorized=True)


def request_payload(item, now=None):
    previous = item.previous_run
    campaign = previous.campaign
    info = rerun_info(previous, now)
    effective_status = item.status
    if info is None or (item.status == ActivityRerunRequest.Status.PENDING and info['status'] == 'ready'):
        effective_status = 'closed'
    return dict(id=item.pk, status=effective_status, space_name=previous.space.name,
                title=campaign.title, title_en=campaign.title_en,
                next_edition=campaign.edition + 1, ended_at=previous.ends_at.timestamp() if previous.ends_at else None,
                submitted_at=item.submitted_at.timestamp(), reviewed_at=item.reviewed_at.timestamp() if item.reviewed_at else None,
                auto_at=info['auto_at'] if info else None, revision=item.submitted_at.isoformat())


def review_requests():
    now = timezone.now()
    rows = ActivityRerunRequest.objects.select_related('previous_run__space', 'previous_run__campaign').order_by('-submitted_at', '-pk')
    return [request_payload(item, now) for item in rows]


@transaction.atomic
def review_rerun(identifier, approved, revision):
    item = ActivityRerunRequest.objects.select_for_update().filter(pk=identifier).first()
    if item is None:
        raise RerunErrors.MISSING
    if item.submitted_at.isoformat() != revision:
        raise RerunErrors.SUPERSEDED
    status = ActivityRerunRequest.Status.APPROVED if approved else ActivityRerunRequest.Status.REJECTED
    if item.status == status:
        return item, False
    if request_payload(item)['status'] != ActivityRerunRequest.Status.PENDING:
        raise RerunErrors.SUPERSEDED
    item.status = status
    item.reviewed_at = timezone.now()
    item.save(update_fields=['status', 'reviewed_at'])
    return item, True


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
