"""Canonical research lifecycle, including compatibility with older clients."""

def research_status(value):
    if value in ('active', 'planning', 'todo', 'doing'):
        return 'active'
    if value in ('archived', 'completed', 'done'):
        return 'archived'
    raise ValueError('Invalid research status')


def normalize_tasks(tasks):
    return [dict(task, status=research_status(task.get('status', 'active'))) for task in tasks]
