import random

from django.db import models, transaction
from django.utils import timezone


class ActivityCampaign(models.Model):
    key = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=80)
    title_en = models.CharField(max_length=120, blank=True, default='')
    summary = models.CharField(max_length=200, blank=True, default='')
    summary_en = models.CharField(max_length=240, blank=True, default='')
    starts_at = models.DateTimeField(db_index=True)
    ends_at = models.DateTimeField(db_index=True)
    event_key = models.CharField(max_length=80, default='square.statement.publish')
    daily_user_limit = models.PositiveSmallIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def active(cls, now=None):
        now = now or timezone.now()
        return cls.objects.filter(enabled=True, starts_at__lte=now, ends_at__gt=now)


class ActivityMilestone(models.Model):
    campaign = models.ForeignKey(ActivityCampaign, on_delete=models.CASCADE, related_name='milestones')
    threshold = models.PositiveIntegerField()
    resource_type = models.CharField(max_length=24)
    reward_pool = models.JSONField(default=list)
    reward_label = models.CharField(max_length=80, blank=True, default='')

    class Meta:
        ordering = ['threshold']
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'threshold'], name='activity_campaign_threshold_unique'),
        ]


class SpaceActivity(models.Model):
    campaign = models.ForeignKey(ActivityCampaign, on_delete=models.CASCADE, related_name='space_runs')
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='activities')
    total_points = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'space'], name='activity_campaign_space_unique'),
        ]


class UserActivityProgress(models.Model):
    campaign = models.ForeignKey(ActivityCampaign, on_delete=models.CASCADE, related_name='user_progress')
    space_activity = models.ForeignKey(SpaceActivity, on_delete=models.CASCADE, related_name='user_progress')
    user = models.ForeignKey('User.User', on_delete=models.CASCADE, related_name='activity_progress')
    earned_points = models.PositiveIntegerField(default=0)
    available_points = models.PositiveIntegerField(default=0)
    contributed_points = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'user'], name='activity_campaign_user_unique'),
        ]


class ActivityEvent(models.Model):
    campaign = models.ForeignKey(ActivityCampaign, on_delete=models.CASCADE, related_name='events')
    progress = models.ForeignKey(UserActivityProgress, on_delete=models.CASCADE, related_name='events')
    event_key = models.CharField(max_length=80)
    event_reference = models.CharField(max_length=80, blank=True, default='')
    event_date = models.DateField(db_index=True)
    points = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['campaign', 'progress', 'event_key', 'event_date', 'event_reference'],
                name='activity_event_reference_unique',
            ),
        ]


class SpaceActivityReward(models.Model):
    space_activity = models.ForeignKey(SpaceActivity, on_delete=models.CASCADE, related_name='rewards')
    milestone = models.ForeignKey(ActivityMilestone, on_delete=models.CASCADE, related_name='space_rewards')
    resource_type = models.CharField(max_length=24)
    reward_id = models.CharField(max_length=80)
    resource_key = models.CharField(max_length=80)
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['space_activity', 'milestone'], name='activity_space_milestone_unique'),
        ]


class ActivityService:
    @staticmethod
    def _progress(campaign, user):
        space_activity, _ = SpaceActivity.objects.get_or_create(campaign=campaign, space=user.space)
        progress, _ = UserActivityProgress.objects.get_or_create(
            campaign=campaign,
            user=user,
            defaults={'space_activity': space_activity},
        )
        return space_activity, progress

    @classmethod
    def record_event(cls, user, event_key, event_reference=''):
        if not user.verified or user.is_deleted:
            return []
        awarded = []
        for campaign in ActivityCampaign.active().filter(event_key=event_key):
            with transaction.atomic():
                space_activity, progress = cls._progress(campaign, user)
                progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
                today_events = ActivityEvent.objects.filter(
                    campaign=campaign,
                    progress=progress,
                    event_key=event_key,
                    event_date=timezone.localdate(),
                )
                if today_events.count() >= campaign.daily_user_limit:
                    continue
                event, created = ActivityEvent.objects.get_or_create(
                    campaign=campaign,
                    progress=progress,
                    event_key=event_key,
                    event_date=timezone.localdate(),
                    event_reference=str(event_reference),
                    defaults={'points': 1},
                )
                if created:
                    progress.earned_points += event.points
                    progress.available_points += event.points
                    progress.save(update_fields=['earned_points', 'available_points', 'updated_at'])
                    awarded.append(campaign.key)
        return awarded

    @staticmethod
    def _grant_reward_to_members(reward):
        from User.models import User, UserResourceInventory

        users = User.objects.filter(space=reward.space_activity.space, is_deleted=False)
        for user in users.iterator():
            UserResourceInventory.grant_activity_resource(
                user,
                reward.resource_type,
                reward.reward_id,
                reward.resource_key,
                reward.space_activity.campaign.key,
                metadata={'threshold': reward.milestone.threshold},
            )

    @classmethod
    def _unlock_crossed_milestones(cls, space_activity):
        used_keys = set(space_activity.rewards.values_list('resource_key', flat=True))
        unlocked = []
        for milestone in space_activity.campaign.milestones.filter(threshold__lte=space_activity.total_points):
            if space_activity.rewards.filter(milestone=milestone).exists():
                continue
            available = [item for item in milestone.reward_pool if item.get('resource_key') not in used_keys]
            if not available:
                continue
            choice = random.SystemRandom().choice(available)
            reward = SpaceActivityReward.objects.create(
                space_activity=space_activity,
                milestone=milestone,
                resource_type=milestone.resource_type,
                reward_id=choice['reward_id'],
                resource_key=choice['resource_key'],
            )
            used_keys.add(reward.resource_key)
            cls._grant_reward_to_members(reward)
            unlocked.append(reward)
        return unlocked

    @classmethod
    def contribute(cls, campaign, user):
        with transaction.atomic():
            space_activity, progress = cls._progress(campaign, user)
            space_activity = SpaceActivity.objects.select_for_update().get(id=space_activity.id)
            progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
            amount = progress.available_points
            if amount:
                progress.available_points = 0
                progress.contributed_points += amount
                progress.save(update_fields=['available_points', 'contributed_points', 'updated_at'])
                space_activity.total_points += amount
                space_activity.save(update_fields=['total_points', 'updated_at'])
                cls._unlock_crossed_milestones(space_activity)
        return amount

    @classmethod
    def payload(cls, campaign, user):
        space_activity, progress = cls._progress(campaign, user)
        rewards = {item.milestone_id: item for item in space_activity.rewards.select_related('milestone')}
        for reward in rewards.values():
            cls._grant_reward_to_members(reward)
        milestones = []
        for item in campaign.milestones.all():
            reward = rewards.get(item.id)
            milestones.append(dict(
                threshold=item.threshold,
                unlocked=reward is not None,
                resource_key=reward.resource_key if reward else '',
                reward_id=reward.reward_id if reward else '',
                reward_label=item.reward_label,
            ))
        now = timezone.now()
        return dict(
            key=campaign.key,
            title=campaign.title,
            title_en=campaign.title_en,
            summary=campaign.summary,
            summary_en=campaign.summary_en,
            starts_at=campaign.starts_at.timestamp(),
            ends_at=campaign.ends_at.timestamp(),
            active=campaign.starts_at <= now < campaign.ends_at and campaign.enabled,
            verified=bool(user.verified),
            today_earned=progress.events.filter(event_date=timezone.localdate()).exists(),
            available_points=progress.available_points,
            contributed_points=progress.contributed_points,
            space_total=space_activity.total_points,
            target=max([item['threshold'] for item in milestones], default=0),
            milestones=milestones,
        )
