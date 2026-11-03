"""Narrow, signed delivery receipts; no login tokens are exposed to workers."""
import json
from django.core import signing
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views import View
from User.models import NotificationDelivery

SALT = 'frienden.web-push.delivery-receipt.v1'
MAX_AGE = 7 * 24 * 60 * 60
ERRORS = {'NotAllowedError', 'InvalidStateError', 'TypeError', 'AbortError', 'UnknownError'}


def receipt_token(delivery):
    return signing.dumps({'delivery': delivery.pk, 'endpoint': delivery.web_subscription.endpoint_digest}, salt=SALT, compress=True)


@transaction.atomic
def record_receipt(token, phase, error=''):
    if phase not in {'received', 'submitted', 'failed'}:
        raise ValueError('Invalid receipt phase')
    data = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    if not isinstance(data, dict) or not isinstance(data.get('delivery'), int):
        raise ValueError('Invalid receipt token')
    delivery = NotificationDelivery.objects.select_for_update().select_related('web_subscription').get(pk=data['delivery'])
    subscription = delivery.web_subscription
    if not subscription or subscription.endpoint_digest != data.get('endpoint'):
        raise ValueError('Invalid receipt binding')
    fields = []
    if delivery.client_received_at is None:
        delivery.client_received_at = timezone.now(); fields.append('client_received_at')
    if phase == 'submitted' and delivery.notification_submitted_at is None:
        delivery.notification_submitted_at = timezone.now(); fields.append('notification_submitted_at')
        delivery.client_error = ''; fields.append('client_error')
    elif phase == 'failed' and delivery.notification_submitted_at is None:
        code = error if error in ERRORS else 'UnknownError'
        if delivery.client_error != code:
            delivery.client_error = code; fields.append('client_error')
    if fields:
        delivery.save(update_fields=fields)


class WebPushReceiptView(View):
    def post(self, request):
        try:
            if len(request.body) > 2048:
                raise ValueError('Receipt too large')
            data = json.loads(request.body)
            if not isinstance(data, dict) or not isinstance(data.get('token'), str) or not isinstance(data.get('phase'), str) or not isinstance(data.get('error', ''), str):
                raise ValueError('Invalid receipt')
            record_receipt(data['token'], data['phase'], data.get('error', ''))
        except (ValueError, TypeError, KeyError, signing.BadSignature, NotificationDelivery.DoesNotExist):
            return JsonResponse({'message': 'Invalid delivery receipt'}, status=400)
        return JsonResponse({'message': 'OK'})
