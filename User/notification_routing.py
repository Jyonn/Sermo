import json


def message_category(message):
    from Chat.models import ChatPurposeChoice
    from Message.models import MessageTypeChoice
    category={ChatPurposeChoice.SUBMISSION:'submission',ChatPurposeChoice.RESEARCH:'research'}.get(message.chat.purpose,'chat')
    if message.type in (MessageTypeChoice.OFFICIAL_NOTICE,MessageTypeChoice.SUBMISSION_INVITE):
        try:
            source=json.loads(message.content)
        except (ValueError,TypeError):
            source={}
        if not isinstance(source,dict):source={}
        if message.type==MessageTypeChoice.SUBMISSION_INVITE:
            category='research' if source.get('research') else 'submission'
        elif str(source.get('event','')).startswith('submission_'):
            category='submission'
    return category
