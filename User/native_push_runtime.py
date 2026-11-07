"""Opt-in event queueing and explicitly requested, bounded native delivery."""
import logging
import os

from django.conf import settings

from User.native_push_delivery import NativePushDelivery as Delivery
from User.native_push_dispatch import dispatch_native_delivery
from utils.apns import APNsConfiguration, APNsTransport, APNsUnavailable

logger = logging.getLogger(__name__)


def queue_environments():
    if getattr(settings, 'NATIVE_PUSH_QUEUE_ENABLED', False) is not True:
        return ()
    try:
        values = Delivery._environments(getattr(settings, 'NATIVE_PUSH_ENVIRONMENTS', ()))
    except ValueError:
        return ()
    return tuple(sorted(values))


def enqueue_native_event(event):
    environments = queue_environments()
    if not environments:
        return []
    try:
        return Delivery.enqueue(event, environments=environments)
    except Exception:
        # Queue failure must not block browser/email routes or expose provider data.
        logger.warning('Native notification queue failed for event %s', event.pk)
        return []


def make_transport(environments):
    configuration = APNsConfiguration.from_environment(os.environ)
    if set(configuration.environments) != set(environments):
        raise APNsUnavailable('environment_mismatch')
    return APNsTransport(configuration)


def process_native_queue(*, limit=50, send=False):
    if type(limit) is not int or not 1 <= limit <= 500 or type(send) is not bool:
        raise ValueError('invalid native queue request')
    summary = dict(status='disabled', candidates=0, processed=0, sent=0,
                   failed=0, skipped=0, unknown=0, unclaimed=0, expired_claims=0)
    environments = queue_environments()
    if not environments or getattr(settings, 'NATIVE_PUSH_WORKER_ENABLED', False) is not True:
        return summary
    ids = list(Delivery.objects.filter(state='pending', binding__environment__in=environments)
               .order_by('pk').values_list('pk', flat=True)[:limit])
    summary['candidates'] = len(ids)
    if not send:
        summary['status'] = 'dry_run'
        return summary
    try:
        transport = make_transport(environments)
    except Exception:
        summary['status'] = 'configuration_unavailable'
        return summary
    try:
        summary['expired_claims'] = Delivery.expire_claims(environments=environments)
        summary['status'] = 'processed'
        for delivery_id in ids:
            if (queue_environments() != environments
                    or getattr(settings, 'NATIVE_PUSH_WORKER_ENABLED', False) is not True):
                summary['status'] = 'stopped'
                break
            outcome = dispatch_native_delivery(delivery_id, transport)
            summary['processed'] += 1
            summary[outcome if outcome in ('sent', 'failed', 'skipped', 'unknown') else 'unclaimed'] += 1
    finally:
        try:
            transport.close()
        except Exception:
            logger.warning('Native notification transport close failed')
    return summary
