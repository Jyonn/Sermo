import json

from pywebpush import WebPushException, webpush

from utils.global_settings import Globals


class WebPushNotConfigured(Exception):
    pass


def vapid_public_key():
    return (getattr(Globals, 'WEB_PUSH_VAPID_PUBLIC_KEY', None) or '').strip()


def notification_payload(title, body, payload):
    def clip(value, limit):
        raw = str(value or '').encode('utf-8')
        return raw[:limit-3].decode('utf-8', errors='ignore') + '…' if len(raw) > limit else raw.decode('utf-8')
    # Push services have small encrypted payload limits. Do not duplicate full
    # chat content or unrelated event metadata alongside the visible preview.
    keys = ('chat_id', 'message_id', 'project_id', 'task_id', 'statement_id', 'space_slug', 'notification_event_id', 'event_type', 'icon', 'receipt_token')
    data = {key: clip(value, 512 if key in ('icon', 'receipt_token') else 128) if isinstance(value, str) else value
            for key, value in (payload or {}).items() if key in keys and isinstance(value, (str, int, float))}
    data.update(title=clip(title, 256), body=clip(body, 1800))
    encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    while len(encoded.encode('utf-8')) > 3000 and data['body']:
        data['body'] = data['body'][:len(data['body']) // 2]
        encoded = json.dumps(data, ensure_ascii=False, separators=(',', ':'))
    return encoded


def send_web_push(subscription, title: str, body: str, payload: dict):
    private_key = (getattr(Globals, 'WEB_PUSH_VAPID_PRIVATE_KEY', None) or '').strip()
    subject = (getattr(Globals, 'WEB_PUSH_VAPID_SUBJECT', None) or '').strip()
    if not private_key or not subject or not vapid_public_key():
        raise WebPushNotConfigured('web push VAPID is not configured')

    return webpush(
        subscription_info=dict(
            endpoint=subscription.endpoint,
            keys=dict(p256dh=subscription.p256dh, auth=subscription.auth),
        ),
        data=notification_payload(title, body, payload),
        vapid_private_key=private_key,
        vapid_claims=dict(sub=subject),
        timeout=8,
        ttl=300,
    )


def is_expired_subscription_error(error):
    return isinstance(error, WebPushException) and getattr(error.response, 'status_code', None) in (404, 410)
