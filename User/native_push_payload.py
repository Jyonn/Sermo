"""Versioned tap identity, without copying arbitrary event payload fields."""
import json


def native_push_payload(event):
    from User.models import NotificationDelivery

    if (type(event.pk) is not int or event.pk <= 0 or type(event.user_id) is not int or event.user_id <= 0
            or event.user.is_deleted or event.space_id != event.user.space_id):
        raise ValueError('invalid notification recipient')
    slug = event.space.slug
    if not isinstance(slug, str) or not slug or len(slug) > 256:
        raise ValueError('invalid notification space')
    title, body = NotificationDelivery(event=event)._web_notification_text()
    title, body = str(title)[:128], str(body)[:512]
    payload = dict(aps={'alert': {'title': title, 'body': body}, 'sound': 'default',
                        'thread-id': f'frienden:{event.space_id}:{event.user_id}'},
                   frienden={'version': 1, 'account_id': event.user_id,
                             'space_slug': slug, 'notification_event_id': event.pk})
    # Measure actual UTF-8 JSON bytes, including escaped control characters.
    while len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 4096:
        alert = payload['aps']['alert']
        field = 'body' if len(alert['body']) >= len(alert['title']) else 'title'
        if not alert[field]:
            raise ValueError('notification metadata too large')
        alert[field] = alert[field][:len(alert[field]) // 2]
    return payload
