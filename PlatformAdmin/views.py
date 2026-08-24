import datetime
import json
import secrets
from urllib.parse import quote

from django.db.models import Count, Q
from django.utils import timezone
from django.views import View
from smartdjango import OK

from Chat.models import Chat, ChatMember, ChatMemberStatusChoice
from Config.models import CI, Config
from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import Message
from PlatformAdmin.models import PlatformAdminEmailCode, PlatformAdminSecurity, PlatformAuditLog
from PlatformAdmin.validators import PlatformAdminErrors
from Space.models import Space
from User.models import (
    NotificationDelivery,
    NotificationDeliveryStatusChoice,
    NotificationEvent,
    NotificationPreference,
    NotificationRouteChannelChoice,
    User,
    UserNotificationChoice,
    UserRoleChoice,
    WebPushDelivery,
)
from utils import auth
from utils.notificator_integration import send_verification_mail
from utils.qiniu import avatar_uri_for_key, sign_private_download_url


def _body(request):
    value = getattr(request, 'json', None)
    if value is not None:
        return value
    try:
        return json.loads(request.body or b'{}')
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _value(data, key, default=''):
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def _boolean(value):
    if isinstance(value, bool):
        return value
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on'}


def _client_ip(request):
    forwarded = request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
    return forwarded or request.headers.get('X-Real-IP') or request.META.get('REMOTE_ADDR')


def _admin_email():
    return (Config.get_value_by_key(CI.ADMIN_EMAIL, default='') or '').strip().lower()


def _require_email(value):
    email = str(value or '').strip().lower()
    if not email or email != _admin_email():
        raise PlatformAdminErrors.ACCESS_DENIED
    return email


def _audit(request, action, target_type='', target_id=None, summary='', metadata=None):
    PlatformAuditLog.objects.create(
        action=action,
        target_type=target_type,
        target_id=target_id,
        summary=summary[:255],
        metadata=metadata or {},
        ip_address=_client_ip(request),
    )


def _space_payload(space):
    return dict(
        space_id=space.id,
        name=space.name,
        slug=space.slug,
        email=space.email,
        official_user=space.official_user.tiny_json() if space.official_user else None,
        verification_tier=space.verification_tier,
        member_limit=space.effective_member_limit,
        member_count=getattr(space, 'admin_member_count', space.active_member_count()),
        chat_enabled=space.chat_enabled,
        square_enabled=space.group_square_enabled,
        identity_submitted_at=space.identity_submitted_at.timestamp() if space.identity_submitted_at else None,
        identity_verified_at=space.identity_verified_at.timestamp() if space.identity_verified_at else None,
        created_at=space.created_at.timestamp(),
    )


class EmailCodeView(View):
    def post(self, request):
        email = _require_email(_value(_body(request), 'email'))
        if PlatformAdminSecurity.primary().mfa_enabled:
            _audit(request, 'auth.mfa_requested', summary='平台管理员进入 MFA 验证')
            return dict(mfa_required=True, masked_email=_mask_email(email))
        now = timezone.now()
        recent = PlatformAdminEmailCode.objects.filter(email=email, created_at__gte=now - datetime.timedelta(seconds=45)).exists()
        if recent:
            return dict(expires_in=600, masked_email=_mask_email(email), mfa_required=False)
        code = f'{secrets.randbelow(1_000_000):06d}'
        PlatformAdminEmailCode.objects.create(email=email, code=code, expires_at=now + datetime.timedelta(minutes=10))
        send_verification_mail(email, code, 10, 'Sermo 超级管理员登录', language='zh-CN', recipient_name='Sermo 管理员')
        _audit(request, 'auth.code_sent', summary='平台管理员验证码已发送')
        return dict(expires_in=600, masked_email=_mask_email(email), mfa_required=False)


def _mask_email(email):
    local, domain = email.split('@', 1)
    return f'{local[:2]}***@{domain}'


class LoginView(View):
    def post(self, request):
        data = _body(request)
        email = _require_email(_value(data, 'email'))
        now = timezone.now()
        security = PlatformAdminSecurity.primary()
        mfa_code = str(_value(data, 'mfa_code')).strip()
        if security.mfa_enabled:
            if not mfa_code:
                raise PlatformAdminErrors.MFA_REQUIRED
            if not security.verify_totp(security.totp_secret, mfa_code):
                recovery_hash = security.hash_recovery_code(mfa_code)
                if recovery_hash not in security.recovery_code_hashes:
                    _audit(request, 'auth.login_failed', summary='MFA 验证失败')
                    raise PlatformAdminErrors.MFA_INVALID
                security.recovery_code_hashes = [item for item in security.recovery_code_hashes if item != recovery_hash]
                security.save(update_fields=['recovery_code_hashes', 'updated_at'])
        else:
            code = PlatformAdminEmailCode.objects.filter(
                email=email, code=str(_value(data, 'code')).strip(), consumed_at__isnull=True, expires_at__gt=now,
            ).order_by('-id').first()
            if code is None:
                _audit(request, 'auth.login_failed', summary='验证码错误')
                raise PlatformAdminErrors.CODE_INVALID
            code.consumed_at = now
            code.save(update_fields=['consumed_at'])
        _audit(request, 'auth.login', summary='平台管理员登录')
        return dict(**auth.get_platform_admin_token(email), mfa_enabled=security.mfa_enabled)


class DashboardView(View):
    @auth.require_platform_admin
    def get(self, request):
        pending = Space.objects.filter(identity_submitted_at__isnull=False, identity_verified_at__isnull=True).count()
        return dict(
            spaces=Space.objects.count(),
            members=User.objects.filter(role=UserRoleChoice.MEMBER, is_deleted=False).count(),
            pending_identity_reviews=pending,
            mfa_enabled=PlatformAdminSecurity.primary().mfa_enabled,
            recent_audit=[_audit_payload(item) for item in PlatformAuditLog.objects.all()[:8]],
        )


class SpaceListView(View):
    @auth.require_platform_admin
    def get(self, request):
        query = request.GET.get('q', '').strip()
        spaces = Space.objects.select_related('official_user').annotate(
            admin_member_count=Count('users', filter=Q(users__role=UserRoleChoice.MEMBER, users__is_deleted=False), distinct=True),
        )
        if query:
            spaces = spaces.filter(Q(name__icontains=query) | Q(slug__icontains=query) | Q(email__icontains=query))
        return [_space_payload(space) for space in spaces.order_by('-created_at', '-id')[:100]]


class MemberListView(View):
    @auth.require_platform_admin
    def get(self, request, space_id):
        space = Space.index(space_id)
        _audit(request, 'space.members_viewed', 'space', space.id, f'查看空间 {space.slug} 的成员')
        users = User.objects.filter(space=space, is_deleted=False).order_by('role', 'name_pinyin', 'id')
        payload = []
        for user in users:
            item = user.json_admin()
            item['friend_count'] = Friendship.objects.filter(space=space, status=FriendshipStatusChoice.ACCEPTED).filter(Q(user_low=user) | Q(user_high=user)).count()
            item['chat_count'] = ChatMember.objects.filter(user=user, status=ChatMemberStatusChoice.ACTIVE, chat__is_deleted=False).count()
            item['statement_count'] = user.statements.filter(is_deleted=False).count()
            item['contacts'] = dict(
                email=bool(user.email_verified_at),
                phone=bool(user.phone_verified_at),
                bark=user.instant_notification_endpoints.filter(verified_at__isnull=False).exists(),
            )
            item['notifications_enabled'] = NotificationPreference.objects.filter(user=user, enabled=True).count()
            payload.append(item)
        return payload


class ChatListView(View):
    @auth.require_platform_admin
    def get(self, request, user_id):
        user = User.index(user_id)
        _audit(request, 'member.chats_viewed', 'user', user.id, f'查看 {user.name} 的会话列表')
        chats = Chat.objects.filter(chat_members__user=user, chat_members__status=ChatMemberStatusChoice.ACTIVE, is_deleted=False).distinct().order_by('-last_chat_at')
        return [chat.jsonl() for chat in chats]


class ChatMessageView(View):
    @auth.require_platform_admin
    def get(self, request, chat_id):
        chat = Chat.index(chat_id)
        if not request.GET.get('reason', '').strip():
            raise PlatformAdminErrors.ACCESS_DENIED
        before = request.GET.get('before')
        perspective_user_id = request.GET.get('perspective_user_id')
        if perspective_user_id and not chat.chat_members.filter(user_id=int(perspective_user_id)).exists():
            raise PlatformAdminErrors.ACCESS_DENIED
        limit = min(100, max(1, int(request.GET.get('limit', 50))))
        _audit(request, 'chat.messages_viewed', 'chat', chat.id, request.GET.get('reason', '')[:255])
        queryset = Message.objects.filter(chat=chat).select_related(
            'user', 'reply_to', 'reply_to__user', 'media_resource', 'media_resource__asset',
        ).prefetch_related('chat_mentions__user')
        if before:
            queryset = queryset.filter(id__lt=int(before))
        messages = list(queryset.order_by('-id')[:limit + 1])
        has_more = len(messages) > limit
        messages = messages[:limit]
        return dict(
            chat=chat.jsonl(),
            messages=[item.jsonl(request=request, include_deleted=True) for item in messages],
            has_more=has_more,
            next_before=messages[-1].id if has_more and messages else None,
            first_person_user_id=int(perspective_user_id) if perspective_user_id else None,
        )


def _delivery_status(value):
    return {
        NotificationDeliveryStatusChoice.PENDING: 'pending',
        NotificationDeliveryStatusChoice.SENT: 'sent',
        NotificationDeliveryStatusChoice.FAILED: 'failed',
        NotificationDeliveryStatusChoice.SKIPPED: 'skipped',
    }.get(value, 'unknown')


def _delivery_channel(value, delivery=None):
    if value == UserNotificationChoice.BARK and delivery is not None and delivery.instant_endpoint_id:
        return delivery.instant_endpoint.provider
    return {
        UserNotificationChoice.EMAIL: 'email',
        UserNotificationChoice.SMS: 'sms',
        UserNotificationChoice.BARK: 'instant',
    }.get(value, 'unknown')


def _delivery_payload(delivery, channel):
    return dict(
        delivery_id=delivery.id,
        channel=channel,
        status=_delivery_status(delivery.status),
        detail=delivery.detail or '',
        created_at=delivery.created_at.timestamp(),
        attempted_at=delivery.attempted_at.timestamp() if delivery.attempted_at else None,
    )


_NON_DELIVERY_DETAILS = {
    'channel_disabled',
    'channel_unavailable',
    'preference_missing',
    'topic_disabled',
}


class MessageDeliveryView(View):
    @auth.require_platform_admin
    def get(self, request, message_id):
        message = Message.objects.select_related('chat', 'user').get(id=message_id)
        reason = request.GET.get('reason', '').strip()
        if not reason:
            raise PlatformAdminErrors.ACCESS_DENIED
        _audit(
            request,
            'message.deliveries_viewed',
            'message',
            message.id,
            reason[:255],
            metadata={'chat_id': message.chat_id},
        )
        events = list(
            NotificationEvent.objects.filter(
                space=message.chat.space,
                payload__message_id=message.id,
            ).select_related('user').prefetch_related(
                'deliveries__instant_endpoint',
                'web_push_deliveries__subscription',
            ).order_by('created_at', 'id')
        )
        recipients = []
        totals = dict(sent=0, pending=0, failed=0, skipped=0)
        for event in events:
            deliveries = [
                _delivery_payload(delivery, _delivery_channel(delivery.channel, delivery))
                for delivery in event.deliveries.all().order_by('created_at', 'id')
                if delivery.detail not in _NON_DELIVERY_DETAILS
            ]
            for delivery in event.web_push_deliveries.all().order_by('created_at', 'id'):
                item = _delivery_payload(delivery, 'web')
                subscription = delivery.subscription
                item['subscription'] = dict(
                    digest=subscription.endpoint_digest[:12],
                    origin=subscription.origin,
                    user_agent=subscription.user_agent,
                    enabled=subscription.enabled,
                    last_seen_at=subscription.last_seen_at.timestamp(),
                )
                deliveries.append(item)
            for delivery in deliveries:
                if delivery['status'] in totals:
                    totals[delivery['status']] += 1
            recipients.append(dict(
                event_id=event.id,
                user=event.user.tiny_json(),
                event_created_at=event.created_at.timestamp(),
                deliveries=deliveries,
            ))
        return dict(
            message=dict(
                message_id=message.id,
                chat_id=message.chat_id,
                sender=message.user.tiny_json(),
                created_at=message.created_at.timestamp(),
                type=message.type,
                preview=message.preview_text(),
            ),
            recipients=recipients,
            totals=dict(recipients=len(recipients), deliveries=sum(totals.values()), **totals),
        )


class IdentityDocumentView(View):
    @auth.require_platform_admin
    def get(self, request, space_id):
        space = Space.index(space_id)
        if not space.identity_document_key:
            raise PlatformAdminErrors.IDENTITY_NOT_PENDING
        _audit(request, 'identity.document_opened', 'space', space.id, f'打开 {space.slug} 身份材料')
        return dict(uri=sign_private_download_url(avatar_uri_for_key(space.identity_document_key), expire_seconds=10 * 60))


class IdentityReviewView(View):
    @auth.require_platform_admin
    def post(self, request, space_id):
        space = Space.index(space_id)
        if not space.identity_submitted_at or space.identity_verified_at:
            raise PlatformAdminErrors.IDENTITY_NOT_PENDING
        data = _body(request)
        approved = _boolean(_value(data, 'approved', False))
        note = str(_value(data, 'note')).strip()
        if approved:
            space.identity_verified_at = timezone.now()
            space.save(update_fields=['identity_verified_at'])
        else:
            space.identity_document_key = ''
            space.identity_submitted_at = None
            space.save(update_fields=['identity_document_key', 'identity_submitted_at'])
        _audit(request, 'identity.approved' if approved else 'identity.rejected', 'space', space.id, note or ('审核通过' if approved else '审核驳回'))
        return _space_payload(space)


class MfaSetupView(View):
    @auth.require_platform_admin
    def post(self, request):
        secret = PlatformAdminSecurity.new_secret()
        Config.update_value(CI.PLATFORM_ADMIN_MFA_PENDING_SECRET, secret)
        email = _admin_email()
        uri = f'otpauth://totp/{quote("Sermo:" + email)}?secret={secret}&issuer=Sermo&algorithm=SHA1&digits=6&period=30'
        _audit(request, 'mfa.setup_started', summary='开始配置 MFA')
        return dict(secret=secret, otpauth_uri=uri)


class MfaVerifyView(View):
    @auth.require_platform_admin
    def post(self, request):
        secret = Config.get_value_by_key(CI.PLATFORM_ADMIN_MFA_PENDING_SECRET, default='')
        if not secret:
            raise PlatformAdminErrors.MFA_NOT_PENDING
        if not PlatformAdminSecurity.verify_totp(secret, _value(_body(request), 'code')):
            raise PlatformAdminErrors.MFA_INVALID
        recovery_codes = [secrets.token_hex(4).upper() for _ in range(8)]
        security = PlatformAdminSecurity.primary()
        security.totp_secret = secret
        security.mfa_enabled = True
        security.recovery_code_hashes = [security.hash_recovery_code(item) for item in recovery_codes]
        security.save()
        Config.update_value(CI.PLATFORM_ADMIN_MFA_PENDING_SECRET, '')
        _audit(request, 'mfa.enabled', summary='启用 MFA')
        return dict(recovery_codes=recovery_codes)


class MfaDisableView(View):
    @auth.require_platform_admin
    def post(self, request):
        security = PlatformAdminSecurity.primary()
        if not security.verify_totp(security.totp_secret, _value(_body(request), 'code')):
            raise PlatformAdminErrors.MFA_INVALID
        security.totp_secret = ''
        security.mfa_enabled = False
        security.recovery_code_hashes = []
        security.save()
        _audit(request, 'mfa.disabled', summary='关闭 MFA')
        return OK


def _audit_payload(item):
    return dict(audit_id=item.id, action=item.action, target_type=item.target_type, target_id=item.target_id, summary=item.summary, metadata=item.metadata, ip_address=item.ip_address, created_at=item.created_at.timestamp())


class AuditLogView(View):
    @auth.require_platform_admin
    def get(self, request):
        queryset = PlatformAuditLog.objects.all()
        action = request.GET.get('action', '').strip()
        if action:
            queryset = queryset.filter(action__startswith=action)
        return [_audit_payload(item) for item in queryset[:100]]
