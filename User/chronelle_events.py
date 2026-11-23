from datetime import datetime

from User.chronelle_account import BindingFailure


def project_events(result, fields):
    if not isinstance(result, dict) or not isinstance(result.get('events'), list) or len(result['events']) > 10000:
        raise BindingFailure('chronelle_unavailable', 503)
    events = []
    for index, event in enumerate(result['events']):
        if (not isinstance(event, dict) or not isinstance(event.get('start'), str)
                or not isinstance(event.get('end'), str) or event.get('visibility') not in ('busy', 'details')):
            raise BindingFailure('chronelle_unavailable', 503)
        try:
            if datetime.fromisoformat(event['end']) <= datetime.fromisoformat(event['start']):
                raise ValueError()
        except (ValueError, TypeError):
            raise BindingFailure('chronelle_unavailable', 503)
        busy = event['visibility'] == 'busy'
        events.append({
            'id': str(index), 'start': event['start'], 'end': event['end'], 'all_day': bool(event.get('all_day')),
            'locked': busy, 'title': '' if busy else event.get('title', ''),
            'location': event.get('location', '') if not busy and fields['location'] else '',
            'description': event.get('description', '') if not busy and fields['description'] else '',
        })
    return {'events': events, 'availability': 'known' if result.get('availability') == 'known' else 'unknown'}
