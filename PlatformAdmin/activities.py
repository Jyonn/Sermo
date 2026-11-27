import json

from django.db import transaction
from django.views import View

from Activity.reruns import RerunErrors, review_requests, review_rerun
from PlatformAdmin.views import _audit
from utils import auth


class PlatformActivityView(View):
    @auth.require_platform_admin
    def get(self, request, request_id=None):
        if request_id is not None:
            raise RerunErrors.MISSING
        return review_requests()

    @auth.require_platform_admin
    @transaction.atomic
    def post(self, request, request_id=None):
        if request_id is None:
            raise RerunErrors.MISSING
        try:
            body = json.loads(request.body) if len(request.body) <= 1024 else None
        except (TypeError, ValueError):
            body = None
        if not isinstance(body, dict) or type(body.get('approved')) is not bool or not isinstance(body.get('revision'), str):
            raise RerunErrors.INVALID
        item, changed = review_rerun(request_id, body['approved'], body['revision'])
        if changed:
            previous = item.previous_run
            _audit(request, 'activity.rerun.review', 'activity', previous.campaign_id, previous.campaign.title, {
                'request_id': item.pk, 'space_id': previous.space_id, 'approved': body['approved'],
            })
        return review_requests()
