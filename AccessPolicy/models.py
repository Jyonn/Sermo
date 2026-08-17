from django.db import models


class PlatformCapabilityPolicy(models.Model):
    capability_key = models.CharField(max_length=160, unique=True, db_index=True)
    requirement = models.JSONField(default=dict, blank=True)
    denial = models.JSONField(default=dict, blank=True)
    limits = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.EmailField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['capability_key']


class SpaceCapabilityPolicy(models.Model):
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, related_name='capability_policies')
    capability_key = models.CharField(max_length=160, db_index=True)
    requirement = models.JSONField(default=dict, blank=True)
    denial = models.JSONField(default=dict, blank=True)
    limits = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['capability_key']
        constraints = [
            models.UniqueConstraint(fields=['space', 'capability_key'], name='access_policy_unique_space_capability'),
        ]


class CapabilityPolicyAudit(models.Model):
    SCOPE_PLATFORM = 'platform'
    SCOPE_SPACE = 'space'
    SCOPE_CHOICES = ((SCOPE_PLATFORM, 'Platform'), (SCOPE_SPACE, 'Space'))

    scope = models.CharField(max_length=16, choices=SCOPE_CHOICES, db_index=True)
    space = models.ForeignKey('Space.Space', on_delete=models.CASCADE, null=True, blank=True)
    capability_key = models.CharField(max_length=160, db_index=True)
    actor = models.CharField(max_length=255, blank=True, default='')
    previous = models.JSONField(default=dict, blank=True)
    current = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-id']

