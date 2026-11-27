from django.db import transaction
from django.views import View

from Activity.reruns import RerunErrors, platform_campaigns, publish_rerun
from PlatformAdmin.views import _audit
from utils import auth


class PlatformActivityView(View):
    @auth.require_platform_admin
    def get(self, request, key=None):
        if key is not None:
            raise RerunErrors.MISSING
        return platform_campaigns()

    @auth.require_platform_admin
    @transaction.atomic
    def post(self, request, key=None):
        if key is None:
            raise RerunErrors.MISSING
        campaign = publish_rerun(key)
        _audit(request, 'activity.rerun', 'activity', campaign.pk, campaign.title, {
            'activity_key': campaign.key, 'previous_key': key, 'edition': campaign.edition,
        })
        return platform_campaigns()
