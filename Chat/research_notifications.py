"""Research activity uses the shared notification routing and privacy controls."""
from User.models import NotificationEvent
from Chat.models import ChatUserPreference, ChatMemberRoleChoice
from Chat.research import members


def notify_research(project,actor,action,body):
    if action not in ('task','task_dates','task_status','progress','settings','transfer','leave','remove','join'):
        return
    rows=list(members(project))
    task=project.tasks[-1] if action=='task' else next((t for t in project.tasks if t['id']==body.get('task_id')),None)
    recipients={row.user_id for row in rows}
    kind='research_update'
    if task:
        kind='research_progress' if action=='progress' else 'research_task'
        recipients=set(task['assignees'])
        recipients.update(row.user_id for row in rows if row.role==ChatMemberRoleChoice.OWNER)
        if body.get('reply_to'):
            recipients.update(p['user_id'] for p in task['progress'] if p['id']==body['reply_to'])
    muted=set(ChatUserPreference.objects.filter(chat=project.chat,notifications_muted=True).values_list('user_id',flat=True))
    for row in rows:
        if row.user_id==actor.pk or row.user_id not in recipients or row.user_id in muted:
            continue
        NotificationEvent.emit_system_event(row.user,actor,{'kind':kind,'notification_category':'research','project_id':project.pk,
            'project_title':project.chat.title,'task_id':task['id'] if task else None,'task_title':task['title'] if task else '',
            'action':action,'revision':project.revision})
