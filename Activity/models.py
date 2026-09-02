import random
from datetime import timedelta

from django.db import models, transaction
from django.utils import timezone


class ActivityCampaign(models.Model):
    class AssignmentMode(models.TextChoices):
        AUTOMATIC = 'automatic', 'Automatic'
        MANUAL = 'manual', 'Manual'

    key = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=80)
    title_en = models.CharField(max_length=120, blank=True, default='')
    summary = models.CharField(max_length=200, blank=True, default='')
    summary_en = models.CharField(max_length=240, blank=True, default='')
    assignment_mode = models.CharField(
        max_length=16,
        choices=AssignmentMode.choices,
        default=AssignmentMode.MANUAL,
        db_index=True,
    )
    duration_seconds = models.PositiveIntegerField(null=True, blank=True)
    event_key = models.CharField(max_length=80, default='square.statement.publish')
    daily_user_limit = models.PositiveSmallIntegerField(default=1)
    config = models.JSONField(default=dict, blank=True)
    enabled = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

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
    claimed_at = models.DateTimeField(default=timezone.now, db_index=True)
    ends_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['campaign', 'space'], name='activity_campaign_space_unique'),
        ]

    def is_active(self, now=None):
        now = now or timezone.now()
        return self.claimed_at <= now and (self.ends_at is None or self.ends_at > now)


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
    claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)
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


class ActivityAwakening(models.Model):
    space_activity = models.ForeignKey(SpaceActivity, on_delete=models.CASCADE, related_name='awakenings')
    step = models.PositiveSmallIntegerField()
    threshold = models.PositiveIntegerField()
    user = models.ForeignKey('User.User', on_delete=models.SET_NULL, null=True, related_name='activity_awakenings')
    unlocked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['step']
        constraints = [
            models.UniqueConstraint(fields=['space_activity', 'step'], name='activity_space_awakening_unique'),
        ]


class UserActivityReward(models.Model):
    progress = models.OneToOneField(UserActivityProgress, on_delete=models.CASCADE, related_name='personal_reward')
    resource_type = models.CharField(max_length=24)
    reward_id = models.CharField(max_length=80)
    resource_key = models.CharField(max_length=80)
    unlocked_at = models.DateTimeField(auto_now_add=True)
    claimed_at = models.DateTimeField(null=True, blank=True, db_index=True)


class ActivityService:
    AWAKENING_COUNT = 8
    FRIENDLY_NEIGHBOR_MODE = 'friendly_neighbor'

    @staticmethod
    def claim_for_space(campaign, space, claimed_at=None):
        claimed_at = claimed_at or timezone.now()
        ends_at = (
            claimed_at + timedelta(seconds=campaign.duration_seconds)
            if campaign.duration_seconds else None
        )
        space_activity, _ = SpaceActivity.objects.get_or_create(
            campaign=campaign,
            space=space,
            defaults={'claimed_at': claimed_at, 'ends_at': ends_at},
        )
        return space_activity

    @classmethod
    def ensure_automatic_for_space(cls, space):
        for campaign in ActivityCampaign.objects.filter(
            enabled=True,
            assignment_mode=ActivityCampaign.AssignmentMode.AUTOMATIC,
        ):
            cls.claim_for_space(campaign, space)

    @staticmethod
    def space_activities(space, active_only=False):
        now = timezone.now()
        queryset = SpaceActivity.objects.filter(
            space=space,
            campaign__enabled=True,
            claimed_at__lte=now,
        ).select_related('campaign')
        if active_only:
            queryset = queryset.filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
        return queryset.order_by('claimed_at', 'id')

    @classmethod
    def active_campaigns_for_space(cls, space, include_managed=False):
        cls.ensure_automatic_for_space(space)
        runs = cls.space_activities(space, active_only=True)
        if include_managed:
            return list(runs)
        return [run for run in runs if run.campaign.config.get('theme') != 'permanent-vip']

    @classmethod
    def space_activity_for(cls, campaign, space, active_only=False):
        queryset = cls.space_activities(space, active_only=active_only)
        return queryset.get(campaign=campaign)

    @classmethod
    def admin_payloads(cls, space):
        cls.ensure_automatic_for_space(space)
        runs = {item.campaign_id: item for item in cls.space_activities(space)}
        payloads = []
        for campaign in ActivityCampaign.objects.filter(enabled=True).order_by('created_at', 'id'):
            run = runs.get(campaign.id)
            status = 'unclaimed'
            if run is not None:
                status = 'active' if run.is_active() else 'ended'
            payloads.append(dict(
                key=campaign.key,
                title=campaign.title,
                title_en=campaign.title_en,
                summary=campaign.summary,
                summary_en=campaign.summary_en,
                theme=campaign.config.get('theme', ''),
                assignment_mode=campaign.assignment_mode,
                mandatory=campaign.assignment_mode == ActivityCampaign.AssignmentMode.AUTOMATIC,
                duration_seconds=campaign.duration_seconds,
                status=status,
                claimed=run is not None,
                claimed_at=run.claimed_at.timestamp() if run else None,
                ends_at=run.ends_at.timestamp() if run and run.ends_at else None,
            ))
        return payloads

    @staticmethod
    def _progress(campaign, user, space_activity=None):
        space_activity = space_activity or SpaceActivity.objects.get(campaign=campaign, space=user.space)
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
        cls.ensure_automatic_for_space(user.space)
        for space_activity in cls.space_activities(user.space, active_only=True).filter(campaign__event_key=event_key):
            campaign = space_activity.campaign
            with transaction.atomic():
                space_activity, progress = cls._progress(campaign, user, space_activity)
                progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
                today_events = ActivityEvent.objects.filter(
                    campaign=campaign,
                    progress=progress,
                    event_key=event_key,
                    event_date=timezone.localdate(),
                )
                earns_force = today_events.filter(points__gt=0).count() < campaign.daily_user_limit
                event, created = ActivityEvent.objects.get_or_create(
                    campaign=campaign,
                    progress=progress,
                    event_key=event_key,
                    event_date=timezone.localdate(),
                    event_reference=str(event_reference),
                    defaults={'points': 1 if earns_force else 0},
                )
                if created:
                    cls._ensure_personal_reward(progress)
                    awarded.append(campaign.key)
        return awarded

    @classmethod
    def record_friendly_neighbor_reply(cls, user, event_reference):
        """Award daily web points while retaining zero-point events as anti-abuse history."""
        if not user.verified or user.is_deleted:
            return 0
        awarded = 0
        for space_activity in cls.space_activities(user.space, active_only=True).filter(
                campaign__event_key='square.statement.comment'):
            campaign = space_activity.campaign
            if campaign.config.get('mode') != cls.FRIENDLY_NEIGHBOR_MODE:
                continue
            with transaction.atomic():
                _, progress = cls._progress(campaign, user, space_activity)
                progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
                today = ActivityEvent.objects.filter(
                    campaign=campaign,
                    progress=progress,
                    event_key=campaign.event_key,
                    event_date=timezone.localdate(),
                )
                reply_index = today.count()
                daily_limit = int(campaign.config.get('daily_points_limit', 25))
                daily_points = sum(today.values_list('points', flat=True))
                schedule = campaign.config.get('reply_points') or [20, 10, 5]
                nominal = int(schedule[min(reply_index, len(schedule) - 1)])
                points = max(0, min(nominal, daily_limit - daily_points))
                event, created = ActivityEvent.objects.get_or_create(
                    campaign=campaign,
                    progress=progress,
                    event_key=campaign.event_key,
                    event_date=timezone.localdate(),
                    event_reference=str(event_reference),
                    defaults={'points': points, 'claimed_at': timezone.now()},
                )
                if created and event.points:
                    progress.earned_points += event.points
                    progress.save(update_fields=['earned_points', 'updated_at'])
                    awarded += event.points
        return awarded

    @classmethod
    def claim_milestone_reward(cls, campaign, user, reward_key):
        from User.models import UserResourceInventory

        reward = next((item for item in campaign.config.get('user_rewards', []) if item.get('key') == reward_key), None)
        if reward is None:
            raise ValueError('activity reward does not exist')
        with transaction.atomic():
            _, progress = cls._progress(campaign, user)
            progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
            if progress.earned_points < int(reward['threshold']):
                raise ValueError('activity reward is not ready')
            item, _ = UserResourceInventory.grant_activity_resource(
                user,
                reward['resource_type'],
                reward['reward_id'],
                reward['resource_key'],
                campaign.key,
                metadata={'kind': 'user_milestone', 'threshold': int(reward['threshold'])},
            )
        return item

    @classmethod
    def claim(cls, campaign, user):
        with transaction.atomic():
            _, progress = cls._progress(campaign, user)
            progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
            events = ActivityEvent.objects.select_for_update().filter(
                campaign=campaign,
                progress=progress,
                points__gt=0,
                claimed_at__isnull=True,
            )
            amount = sum(events.values_list('points', flat=True))
            if amount:
                claimed_at = timezone.now()
                events.update(claimed_at=claimed_at)
                progress.earned_points += amount
                progress.available_points += amount
                progress.save(update_fields=['earned_points', 'available_points', 'updated_at'])
        return amount

    @classmethod
    def _ensure_personal_reward(cls, progress):
        target = int(progress.campaign.config.get('personal_event_target', 0))
        pool = progress.campaign.config.get('personal_reward_pool') or []
        if not target or not pool or progress.events.count() < target:
            return None
        existing = UserActivityReward.objects.filter(progress=progress).first()
        if existing:
            return existing
        owned = set(progress.user.resource_inventory.values_list('reward_id', flat=True))
        available = [item for item in pool if item.get('reward_id') not in owned] or pool
        choice = random.SystemRandom().choice(available)
        reward = UserActivityReward.objects.create(
            progress=progress,
            resource_type=choice.get('resource_type', 'bubble'),
            reward_id=choice['reward_id'],
            resource_key=choice['resource_key'],
        )
        return reward

    @classmethod
    def claim_personal_reward(cls, campaign, user):
        from User.models import UserResourceInventory

        with transaction.atomic():
            _, progress = cls._progress(campaign, user)
            progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
            reward = cls._ensure_personal_reward(progress)
            if reward is None:
                raise ValueError('personal reward is not ready')
            reward = UserActivityReward.objects.select_for_update().get(id=reward.id)
            if reward.claimed_at is None:
                target = int(campaign.config.get('personal_event_target', 0))
                UserResourceInventory.grant_activity_resource(
                    user, reward.resource_type, reward.reward_id, reward.resource_key,
                    campaign.key, metadata={'kind': 'personal_task', 'target': target},
                )
                reward.claimed_at = timezone.now()
                reward.save(update_fields=['claimed_at'])
        return reward

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
    def claim_space_reward(cls, campaign, user):
        """Claim the next reached space milestone without auto-granting on contribution."""
        with transaction.atomic():
            space_activity, _ = cls._progress(campaign, user)
            space_activity = SpaceActivity.objects.select_for_update().get(id=space_activity.id)
            claimed_milestone_ids = space_activity.rewards.values_list('milestone_id', flat=True)
            milestone = (
                campaign.milestones.filter(threshold__lte=space_activity.total_points)
                .exclude(id__in=claimed_milestone_ids)
                .order_by('threshold', 'id')
                .first()
            )
            if milestone is None:
                raise ValueError('space reward is not ready')

            used_keys = set(space_activity.rewards.values_list('resource_key', flat=True))
            available = [item for item in milestone.reward_pool if item.get('resource_key') not in used_keys]
            if not available:
                raise ValueError('space reward pool is exhausted')
            choice = random.SystemRandom().choice(available)
            reward = SpaceActivityReward.objects.create(
                space_activity=space_activity,
                milestone=milestone,
                resource_type=milestone.resource_type,
                reward_id=choice['reward_id'],
                resource_key=choice['resource_key'],
            )
            cls._grant_reward_to_members(reward)
        return reward

    @classmethod
    def _claimable_space_milestone(cls, space_activity):
        claimed_milestone_ids = space_activity.rewards.values_list('milestone_id', flat=True)
        return (
            space_activity.campaign.milestones.filter(threshold__lte=space_activity.total_points)
            .exclude(id__in=claimed_milestone_ids)
            .order_by('threshold', 'id')
            .first()
        )

    @classmethod
    def contribute(cls, campaign, user):
        with transaction.atomic():
            space_activity, progress = cls._progress(campaign, user)
            space_activity = SpaceActivity.objects.select_for_update().get(id=space_activity.id)
            progress = UserActivityProgress.objects.select_for_update().get(id=progress.id)
            amount = progress.available_points
            if amount:
                previous_total = space_activity.total_points
                progress.available_points = 0
                progress.contributed_points += amount
                progress.save(update_fields=['available_points', 'contributed_points', 'updated_at'])
                space_activity.total_points += amount
                space_activity.save(update_fields=['total_points', 'updated_at'])
                cls._record_crossed_awakenings(space_activity, user, previous_total)
        return amount

    @classmethod
    def _record_crossed_awakenings(cls, space_activity, user, previous_total=0):
        target = max(space_activity.campaign.milestones.values_list('threshold', flat=True), default=24)
        count = int(space_activity.campaign.config.get('awakening_count', cls.AWAKENING_COUNT))
        for step in range(1, count + 1):
            threshold = max(1, round(target * step / count))
            if previous_total < threshold <= space_activity.total_points:
                ActivityAwakening.objects.get_or_create(
                    space_activity=space_activity,
                    step=step,
                    defaults={'threshold': threshold, 'user': user},
                )

    @classmethod
    def _ensure_legacy_awakenings(cls, space_activity):
        if space_activity.total_points <= 0:
            return
        contributors = list(
            space_activity.user_progress.filter(contributed_points__gt=0)
            .select_related('user').order_by('updated_at', 'id')
        )
        if not contributors:
            return
        target = max(space_activity.campaign.milestones.values_list('threshold', flat=True), default=24)
        count = int(space_activity.campaign.config.get('awakening_count', cls.AWAKENING_COUNT))
        contribution_owners = [
            contributor.user
            for contributor in contributors
            for _ in range(contributor.contributed_points)
        ]
        for step in range(1, count + 1):
            threshold = max(1, round(target * step / count))
            if threshold > space_activity.total_points:
                break
            contributor = contribution_owners[min(threshold - 1, len(contribution_owners) - 1)]
            ActivityAwakening.objects.get_or_create(
                space_activity=space_activity,
                step=step,
                defaults={'threshold': threshold, 'user': contributor},
            )

    @classmethod
    def payload(cls, campaign, user, space_activity=None):
        space_activity, progress = cls._progress(campaign, user, space_activity)
        personal_reward = cls._ensure_personal_reward(progress)
        cls._ensure_legacy_awakenings(space_activity)
        rewards = {item.milestone_id: item for item in space_activity.rewards.select_related('milestone')}
        for reward in rewards.values():
            cls._grant_reward_to_members(reward)
        claimable_space_milestone = cls._claimable_space_milestone(space_activity)
        milestones = []
        for item in campaign.milestones.all():
            reward = rewards.get(item.id)
            milestones.append(dict(
                threshold=item.threshold,
                unlocked=reward is not None,
                claimable=claimable_space_milestone is not None and item.id == claimable_space_milestone.id,
                resource_key=reward.resource_key if reward else '',
                reward_id=reward.reward_id if reward else '',
                reward_label=item.reward_label,
            ))
        claimable_points = sum(progress.events.filter(points__gt=0, claimed_at__isnull=True).values_list('points', flat=True))
        payload = dict(
            key=campaign.key,
            title=campaign.title,
            title_en=campaign.title_en,
            summary=campaign.summary,
            summary_en=campaign.summary_en,
            starts_at=space_activity.claimed_at.timestamp(),
            ends_at=space_activity.ends_at.timestamp() if space_activity.ends_at else None,
            active=campaign.enabled and space_activity.is_active(),
            assignment_mode=campaign.assignment_mode,
            duration_seconds=campaign.duration_seconds,
            theme=campaign.config.get('theme', ''),
            verified=bool(user.verified),
            today_earned=progress.events.filter(event_date=timezone.localdate()).exists(),
            claimable_points=claimable_points,
            available_points=progress.available_points,
            contributed_points=progress.contributed_points,
            personal_event_count=progress.events.count(),
            personal_event_target=int(campaign.config.get('personal_event_target', 0)),
            personal_reward_claimable=bool(personal_reward and personal_reward.claimed_at is None),
            personal_reward=dict(
                resource_key=personal_reward.resource_key,
                reward_id=personal_reward.reward_id,
            ) if personal_reward and personal_reward.claimed_at else None,
            space_reward_claimable=dict(
                threshold=claimable_space_milestone.threshold,
                reward_label=claimable_space_milestone.reward_label,
            ) if claimable_space_milestone else None,
            official_user=user.space.official_user.tiny_json() if user.space.official_user_id else None,
            space_total=space_activity.total_points,
            target=max([item['threshold'] for item in milestones], default=0),
            milestones=milestones,
            awakenings=[dict(
                step=item.step,
                threshold=item.threshold,
                user=item.user.tiny_json() if item.user else None,
            ) for item in space_activity.awakenings.select_related('user')],
        )
        if campaign.config.get('mode') == cls.FRIENDLY_NEIGHBOR_MODE:
            today_events = progress.events.filter(event_date=timezone.localdate())
            today_points = sum(today_events.values_list('points', flat=True))
            reply_count = today_events.count()
            daily_limit = int(campaign.config.get('daily_points_limit', 25))
            schedule = campaign.config.get('reply_points') or [20, 10, 5]
            next_nominal = int(schedule[min(reply_count, len(schedule) - 1)])
            owned = set(user.resource_inventory.values_list('reward_id', flat=True))
            payload['friendly_neighbor'] = dict(
                web_points=progress.earned_points,
                today_points=today_points,
                today_reply_count=reply_count,
                daily_limit=daily_limit,
                next_reply_points=max(0, min(next_nominal, daily_limit - today_points)),
                rewards=[dict(
                    key=item['key'],
                    threshold=int(item['threshold']),
                    resource_type=item['resource_type'],
                    resource_key=item['resource_key'],
                    claimed=item['reward_id'] in owned,
                    claimable=progress.earned_points >= int(item['threshold']) and item['reward_id'] not in owned,
                ) for item in campaign.config.get('user_rewards', [])],
            )
        return payload
