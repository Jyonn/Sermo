import unicodedata

from django.db import transaction
from django.utils import timezone

from Chat.models import ChatBlockedWord, ChatBlockedWordRequest, ChatMember, ChatMemberStatusChoice
from Chat.validators import ChatErrors
from Message.models import Message, MessageEvent


def normalize_word(value):
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ''.join(char for char in text if not char.isspace() and unicodedata.category(char) != 'Cf')


def validated_word(value):
    word = str(value or '').strip()
    normalized = normalize_word(word)
    if not 2 <= len(normalized) <= 20 or len(word) > 80:
        raise ChatErrors.BLOCKED_WORD_INVALID
    return word, normalized


def check_text(chat, sender, text):
    normalized = normalize_word(text)
    if not normalized:
        return
    if chat.group:
        rules = ChatBlockedWord.objects.filter(chat=chat, owner__isnull=True)
    else:
        member_ids = ChatMember.objects.filter(chat=chat, status=ChatMemberStatusChoice.ACTIVE).values_list('user_id', flat=True)
        rules = ChatBlockedWord.objects.filter(chat=chat, owner_id__in=member_ids)
    if any(rule in normalized for rule in rules.values_list('normalized', flat=True)):
        raise ChatErrors.BLOCKED_WORD_MATCHED


def rules_for(chat, user):
    rules = ChatBlockedWord.objects.filter(chat=chat, owner=None if chat.group else user).order_by('id')
    requests = []
    if chat.group:
        base = ChatBlockedWordRequest.objects.filter(chat=chat).select_related('applicant')
        requests = list(base.filter(status='pending').order_by('-id')) + list(base.exclude(status='pending').order_by('-id')[:50])
    return {
        'words': [dict(id=item.id, word=item.word) for item in rules],
        'requests': [dict(id=item.id, word=item.word, status=item.status, applicant_id=item.applicant_id,
                          applicant_name=item.applicant.name, message_id=item.message_id) for item in requests],
        'is_owner': chat.is_owner(user) if chat.group else False,
        'group': chat.group,
    }


@transaction.atomic
def change_rules(chat, user, action, word=None, item_id=None):
    # Serialize capacity and duplicate checks for all changes in this chat.
    type(chat).objects.select_for_update().get(id=chat.id)
    if chat.submission:
        raise ChatErrors.FORBIDDEN
    is_owner = chat.is_owner(user) if chat.group else False
    if action in ('add', 'request'):
        word, normalized = validated_word(word)
        scope = ChatBlockedWord.objects.filter(chat=chat, owner=None if chat.group else user)
        if scope.filter(normalized=normalized).exists():
            raise ChatErrors.BLOCKED_WORD_DUPLICATE
        if chat.group and ChatBlockedWordRequest.objects.filter(chat=chat, status='pending', normalized=normalized).exists():
            raise ChatErrors.BLOCKED_WORD_DUPLICATE
        limit = 50 if chat.group else 25
        if action == 'add':
            if chat.group and not is_owner:
                raise ChatErrors.FORBIDDEN
            if scope.count() >= limit:
                raise ChatErrors.BLOCKED_WORD_LIMIT
            ChatBlockedWord.objects.create(chat=chat, owner=None if chat.group else user, word=word, normalized=normalized)
        else:
            if not chat.group or is_owner:
                raise ChatErrors.FORBIDDEN
            pending = ChatBlockedWordRequest.objects.filter(chat=chat, status='pending')
            if pending.filter(applicant=user).count() >= 3:
                raise ChatErrors.BLOCKED_WORD_PENDING_LIMIT
            item = ChatBlockedWordRequest.objects.create(chat=chat, applicant=user, word=word, normalized=normalized)
            message = Message.create_system(chat, user, 'blocked_word_request', request_id=item.id)
            item.message = message
            item.save(update_fields=['message'])
    elif action == 'remove':
        scope = ChatBlockedWord.objects.filter(chat=chat, owner=None if chat.group else user)
        if chat.group and not is_owner:
            raise ChatErrors.FORBIDDEN
        if not scope.filter(id=item_id).exists():
            raise ChatErrors.BLOCKED_WORD_NOT_FOUND
        scope.filter(id=item_id).delete()
    elif action in ('approve', 'reject', 'withdraw'):
        if not chat.group:
            raise ChatErrors.FORBIDDEN
        item = ChatBlockedWordRequest.objects.select_for_update().filter(chat=chat, id=item_id, status='pending').first()
        if item is None:
            raise ChatErrors.BLOCKED_WORD_NOT_FOUND
        if action == 'withdraw':
            if item.applicant_id != user.id:
                raise ChatErrors.FORBIDDEN
            item.status = 'withdrawn'
        else:
            if not is_owner:
                raise ChatErrors.FORBIDDEN
            item.status = 'approved' if action == 'approve' else 'rejected'
            if action == 'approve':
                scope = ChatBlockedWord.objects.filter(chat=chat, owner__isnull=True)
                if scope.count() >= 50:
                    raise ChatErrors.BLOCKED_WORD_LIMIT
                if scope.filter(normalized=item.normalized).exists():
                    raise ChatErrors.BLOCKED_WORD_DUPLICATE
                ChatBlockedWord.objects.create(chat=chat, word=item.word, normalized=item.normalized)
        item.resolved_at = timezone.now()
        item.save(update_fields=['status', 'resolved_at'])
        if item.message_id:
            MessageEvent.record_created(item.message)
    else:
        raise ChatErrors.FORBIDDEN
    return rules_for(chat, user)
