from django.views import View

from AccessPolicy.engine import evaluate_expression
from Chat import voice
from Message import social_quota
from Message.models import MediaResource
from Square.models import SquareMute
from Square.quota import quota_for_user
from utils import auth


CHAT_SEND = ('image', 'audio', 'video', 'file', 'location')
CHAT_GROUP = ('create', 'join', 'send', 'invite', 'rename')
SQUARE_MEDIA = ('text', 'image', 'audio', 'video')


def permission_status(reason=None, **details):
    return dict(allowed=reason is None, reason=reason, **details)


def unmet_requirements(expression, context):
    if evaluate_expression(expression, context):
        return
    if 'all' in expression:
        for child in expression['all']:
            yield from unmet_requirements(child, context)
    else:
        yield expression


def capability_status(user, key, reason=None, **details):
    if reason:
        return permission_status(reason, **details)
    decision = user.capability_decision(key)
    if decision.allowed:
        return permission_status()
    if decision.denied_by:
        return permission_status('restricted')

    requirements = [
        atom for failure in decision.failed
        for atom in unmet_requirements(failure['expression'], decision.context)
    ]
    for field, reason in (('verified', 'verification_required'), ('phone_verified', 'phone_required'),
                          ('space_phone_verified', 'space_verification_required')):
        if dict(field=field, op='eq', value=True) in requirements:
            return permission_status(reason)
    levels = [atom['value'] for atom in requirements
              if atom.get('field') == 'growth_level' and atom.get('op') == 'gte'
              and type(atom.get('value')) is int and 1 <= atom['value'] <= 18]
    if levels:
        return permission_status('level_required', required_level=max(levels))
    return permission_status('restricted')


def personal_permissions(user):
    space = user.space
    chat_reason = None if space.chat_enabled else 'space_disabled'
    chat_mute = user.chat_mute_payload()
    chat_send_reason = chat_reason or ('muted' if chat_mute['active'] else None)
    chat_send = {
        kind: capability_status(user, f'chat.message.send.{kind}', chat_send_reason,
                                **({'until': chat_mute['muted_until']} if chat_send_reason == 'muted' else {}))
        for kind in CHAT_SEND
    }
    chat_group = {
        action: capability_status(user, f'chat.group.{action}', chat_send_reason if action == 'send' else chat_reason)
        for action in CHAT_GROUP
    }

    square_enabled = space.group_square_enabled and space.verification_tier != 'email'
    square_reason = None if square_enabled else 'space_disabled'
    square_mute = SquareMute.active_for(user) if square_enabled else None
    square_participation_reason = square_reason or ('muted' if square_mute else None)
    mute_detail = {'until': square_mute.muted_until.timestamp() if square_mute.muted_until else None} if square_mute else {}
    publish_reason = square_participation_reason or (None if space.square_free_post_enabled else 'publishing_disabled')
    square_quota = quota_for_user(user) if square_enabled else None

    return dict(
        account=dict(user_id=user.id, space_id=space.id, space_name=space.name,
                     level=user.effective_growth_level(), vip=bool(user.is_permanent_vip)),
        chat=dict(enabled=bool(space.chat_enabled), voice=voice.personal_quota(user), storage=MediaResource.quota_for(user),
                  send=chat_send, group=chat_group,
                  download_voice=capability_status(user, 'chat.message.download.audio', chat_reason)),
        square=dict(enabled=bool(square_enabled), quota=square_quota,
                    publish=capability_status(user, 'square.statement.publish', publish_reason, **mute_detail),
                    media={kind: capability_status(user, f'square.statement.publish.{kind}', publish_reason, **mute_detail)
                           for kind in SQUARE_MEDIA},
                    comment=capability_status(user, 'square.interaction.comment', square_participation_reason, **mute_detail),
                    reply=capability_status(user, 'square.interaction.reply', square_participation_reason, **mute_detail),
                    like=capability_status(user, 'square.interaction.like', square_reason)),
        social=social_quota.quota_for(user),
        research=dict(enabled=bool(space.research_enabled), create=capability_status(
            user, 'chat.group.create', None if space.research_enabled else 'space_disabled')),
    )


class PersonalPermissionQuotaView(View):
    @auth.require_user
    def get(self, request):
        return personal_permissions(request.user)
