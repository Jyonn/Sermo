import json

from pywebpush import WebPushException, webpush

from utils.global_settings import Globals


class WebPushNotConfigured(Exception):
    pass


def vapid_public_key():
    return (getattr(Globals, 'WEB_PUSH_VAPID_PUBLIC_KEY', None) or '').strip()


def send_web_push(subscription, title: str, body: str, payload: dict):
    private_key = (getattr(Globals, 'WEB_PUSH_VAPID_PRIVATE_KEY', None) or '').strip()
    subject = (getattr(Globals, 'WEB_PUSH_VAPID_SUBJECT', None) or '').strip()
    if not private_key or not subject or not vapid_public_key():
        raise WebPushNotConfigured('web push VAPID is not configured')

    data = dict(payload or {})
    data.update(title=title, body=body)
    return webpush(
        subscription_info=dict(
            endpoint=subscription.endpoint,
            keys=dict(p256dh=subscription.p256dh, auth=subscription.auth),
        ),
        data=json.dumps(data, ensure_ascii=False),
        vapid_private_key=private_key,
        vapid_claims=dict(sub=subject),
        ttl=300,
    )


def is_expired_subscription_error(error):
    return isinstance(error, WebPushException) and getattr(error.response, 'status_code', None) in (404, 410)
