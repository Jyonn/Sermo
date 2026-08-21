import datetime
import logging
import threading

from django.views import View
from django.utils import timezone, translation
from django.db import transaction
from django.db.models import Count, Q
from notificator import NotificatorAPIError
from smartdjango import analyse

from Space.models import Space
from Space.models import SpaceEmailVerificationCode, SpaceEmailCodePurposeChoice, SpacePhoneVerificationCode
from Space.params import (
    SpaceParams,
    SpaceEmailVerificationCodeParams,
    SpaceLookupParams,
    SpaceAdminBroadcastParams,
    SpaceOfficialLoginTicketParams,
    SpaceUserListParams,
    SpacePhoneVerificationParams,
    SpaceIdentityParams,
)
from Space.validators import SpaceErrors
from utils import auth
from utils.auth import Request
from Chat.models import Chat
from Friendship.models import Friendship, FriendshipStatusChoice
from Message.models import Message
from Message.params import MessageParams
from User.models import (
    NotificationEvent,
    NotificationPreference,
    OfficialLoginTicket,
    User,
    UserNotificationChoice,
    UserRoleChoice,
)
from User.params import UserParams
from User.validators import UserErrors
from utils.notificator_integration import (
    send_verification_mail,
    send_verification_sms,
    send_space_identity_review_mail,
    space_administrator_name,
    verification_title,
)
from utils.qiniu import issue_message_upload, issue_space_identity_upload, validate_space_identity_key


logger = logging.getLogger(__name__)


def _send_identity_review_safely(space_id):
    try:
        send_space_identity_review_mail(Space.index(space_id))
    except Exception:
        logger.exception('Failed to send identity review notification for space %s', space_id)


def _extract_client_ip(request: Request):
    x_forwarded_for = request.headers.get('X-Forwarded-For', '')
    if x_forwarded_for:
        first_ip = x_forwarded_for.split(',')[0].strip()
        if first_ip:
            return first_ip

    x_real_ip = request.headers.get('X-Real-IP', '').strip()
    if x_real_ip:
        return x_real_ip

    meta = getattr(request, 'META', {}) or {}
    remote_addr = (meta.get('REMOTE_ADDR') or '').strip()
    return remote_addr or None


def _mask_email(email: str) -> str:
    email = (email or '').strip().lower()
    if '@' not in email:
        return '***'

    local, domain = email.split('@', 1)
    domain_name, dot, domain_suffix = domain.partition('.')

    def mask_part(value: str, keep: int = 1) -> str:
        if not value:
            return '***'
        if len(value) <= keep:
            return value[0] + '***'
        return value[:keep] + '***'

    masked_local = mask_part(local, keep=1)
    masked_domain = mask_part(domain_name, keep=1)
    return f'{masked_local}@{masked_domain}{dot}{domain_suffix}' if dot else f'{masked_local}@{masked_domain}'


def _is_notificator_timeout(error: Exception):
    message = str(error).lower()
    return 'timed out' in message or 'timeout' in message or 'read timed out' in message


class SpaceEmailCodeRequestView(View):
    @analyse.json(
        SpaceEmailVerificationCodeParams.slug,
        SpaceEmailVerificationCodeParams.email,
    )
    def post(self, request: Request):
        slug = request.json.slug
        email = (request.json.email or '').strip().lower()

        if slug:
            space = Space.get_by_slug(slug)
            if email and space.email != email:
                raise SpaceErrors.EMAIL_MISMATCH
            email = space.email
            purpose = SpaceEmailCodePurposeChoice.LOGIN
        else:
            space = None
            if not email:
                raise SpaceErrors.EMAIL_REQUIRED
            Space.require_email_creation_available(email)
            purpose = SpaceEmailCodePurposeChoice.REGISTER

        verify_code = SpaceEmailVerificationCode.issue(
            email=email,
            purpose=purpose,
            space=space,
        )
        language = (
            space.official_user.language
            if space is not None and space.official_user_id
            else translation.get_language()
        )
        title = verification_title('space', language)
        try:
            send_verification_mail(
                target=verify_code.email,
                code=verify_code.code,
                time=SpaceEmailVerificationCode.EXPIRE_SECONDS // 60,
                title=title,
                language=language,
                recipient_name=space_administrator_name(language),
            )
        except NotificatorAPIError as e:
            if _is_notificator_timeout(e):
                logger.warning('Space email code delivery timed out after code creation: %s', e)
                return dict(
                    expires_in=SpaceEmailVerificationCode.EXPIRE_SECONDS,
                    masked_email=_mask_email(email),
                    delivery_uncertain=True,
                )
            raise SpaceErrors.NOTIFICATOR_FAILED(details=e)
        return dict(
            expires_in=SpaceEmailVerificationCode.EXPIRE_SECONDS,
            masked_email=_mask_email(email),
        )


class SpaceView(View):
    @analyse.json(
        SpaceParams.name,
        SpaceParams.slug,
        SpaceParams.email,
        SpaceParams.language,
        SpaceEmailVerificationCodeParams.code,
    )
    def post(self, request: Request):
        space = Space.create(
            name=request.json.name,
            slug=request.json.slug,
            email=request.json.email,
            language=request.json.language,
            code=request.json.code,
        )
        return dict(
            space=space.json_private(),
            auth=auth.get_space_login_token(space),
        )


class SpaceLoginView(View):
    @analyse.json(
        SpaceParams.slug,
        SpaceEmailVerificationCodeParams.email,
        SpaceEmailVerificationCodeParams.code,
    )
    def post(self, request: Request):
        space = Space.login_by_email_code(
            slug=request.json.slug,
            email=request.json.email,
            code=request.json.code,
        )
        return dict(
            space=space.json_private(),
            auth=auth.get_space_login_token(space),
        )


class SpaceJoinView(View):
    @analyse.json(
        SpaceParams.slug,
        SpaceParams.name,
        SpaceParams.password,
        SpaceParams.language,
    )
    def post(self, request: Request):
        space = Space.get_by_slug(request.json.slug)
        user = User.login(
            space=space,
            name=request.json.name,
            password=request.json.password,
            language=request.json.language,
        )
        user.log_login(ip=_extract_client_ip(request))
        return dict(
            space=space.json(),
            auth=auth.get_login_token(user),
        )


class SpaceMeView(View):
    @auth.require_user
    def get(self, request: Request):
        return request.user.space.json()


class SpaceAdminSettingsView(View):
    @auth.require_space
    @analyse.json(
        SpaceParams.name,
        SpaceParams.group_square_enabled,
        SpaceParams.chat_enabled,
        SpaceParams.square_explore_enabled,
        SpaceParams.unverified_group_policy,
        SpaceParams.member_limit,
        SpaceParams.level_names,
    )
    def post(self, request: Request):
        space = request.space.set_admin_settings(
            name=request.json.name,
            group_square_enabled=request.json.group_square_enabled,
            chat_enabled=request.json.chat_enabled,
            square_explore_enabled=request.json.square_explore_enabled,
            unverified_group_policy=request.json.unverified_group_policy,
            member_limit=request.json.member_limit,
            level_names=request.json.level_names() if request.json.level_names is not None else None,
        )
        return space.json_private()


class SpaceAdminOfficialLoginTicketView(View):
    @auth.require_space
    def post(self, request: Request):
        ticket = OfficialLoginTicket.issue(request.space)
        return dict(
            token=ticket.token,
            expires_in=OfficialLoginTicket.EXPIRE_SECONDS,
        )


class SpaceAdminSessionView(View):
    @auth.require_user
    def post(self, request: Request):
        space = request.user.space
        if not request.user.is_official or space.official_user_id != request.user.id:
            raise SpaceErrors.ADMIN_ACCESS_FORBIDDEN
        return dict(
            space=space.json_private(),
            auth=auth.get_space_login_token(space),
        )


class SpaceAdminDashboardView(View):
    @auth.require_space
    def get(self, request: Request):
        space = request.space
        users = User.objects.filter(
            space=space,
            is_deleted=False,
            role=UserRoleChoice.MEMBER,
        )
        threshold = timezone.now() - datetime.timedelta(minutes=User.vldt.OFFLINE_MIN_INTERVAL)
        return dict(
            space=space.json_private(),
            stats=dict(
                members_count=users.count(),
                online_count=users.filter(last_heartbeat__gt=threshold).count(),
            ),
        )


class SpaceAdminPhoneCodeView(View):
    @auth.require_space
    @analyse.json(SpacePhoneVerificationParams.phone)
    def post(self, request: Request):
        code = SpacePhoneVerificationCode.issue(request.space, request.json.phone)
        try:
            send_verification_sms(
                code.phone,
                code.code,
                SpacePhoneVerificationCode.EXPIRE_SECONDS // 60,
                'Sermo 空间管理员认证',
                language='zh-CN',
            )
        except NotificatorAPIError as error:
            raise SpaceErrors.NOTIFICATOR_FAILED(details=error)
        return dict(expires_in=SpacePhoneVerificationCode.EXPIRE_SECONDS)


class SpaceAdminPhoneVerifyView(View):
    @auth.require_space
    @analyse.json(SpacePhoneVerificationParams.phone, SpacePhoneVerificationParams.code)
    def post(self, request: Request):
        return SpacePhoneVerificationCode.verify(
            request.space, request.json.phone, request.json.code,
        ).json_private()


class SpaceAdminIdentityUploadView(View):
    @auth.require_space
    @analyse.json(SpaceIdentityParams.file_name, SpaceIdentityParams.content_type)
    def post(self, request: Request):
        if request.space.admin_phone_verified_at is None:
            raise SpaceErrors.TIER_FEATURE_RESTRICTED
        return issue_space_identity_upload(
            request.space.id, request.json.file_name, request.json.content_type,
        )


class SpaceAdminIdentitySubmitView(View):
    @auth.require_space
    @analyse.json(SpaceIdentityParams.key)
    def post(self, request: Request):
        space = request.space
        if space.admin_phone_verified_at is None:
            raise SpaceErrors.TIER_FEATURE_RESTRICTED
        if space.identity_submitted_at is not None and space.identity_verified_at is None:
            raise SpaceErrors.IDENTITY_ALREADY_SUBMITTED
        space.identity_document_key = validate_space_identity_key(space.id, request.json.key)
        space.identity_submitted_at = timezone.now()
        space.identity_verified_at = None
        space.save(update_fields=['identity_document_key', 'identity_submitted_at', 'identity_verified_at'])
        transaction.on_commit(lambda: threading.Thread(
            target=_send_identity_review_safely,
            args=(space.id,),
            daemon=True,
        ).start())
        return space.json_private()


class SpaceLookupView(View):
    @analyse.query(
        SpaceLookupParams.slug,
    )
    def get(self, request: Request):
        space = Space.get_by_slug(request.query.slug)
        return space.json()


class SpaceUserListView(View):
    force_online = None

    @auth.require_user
    @analyse.query(
        SpaceUserListParams.q,
        SpaceUserListParams.online,
        SpaceUserListParams.limit,
        SpaceUserListParams.offset,
    )
    def get(self, request: Request):
        users = User.objects.filter(space=request.user.space, is_deleted=False)

        if request.query.q:
            users = users.filter(lower_name__contains=request.query.q.lower())

        online = request.query.online
        if self.force_online is not None:
            online = 1 if self.force_online else 0
        if online is not None:
            threshold = timezone.now() - datetime.timedelta(minutes=User.vldt.OFFLINE_MIN_INTERVAL)
            if bool(online):
                users = users.filter(Q(last_heartbeat__gt=threshold) | Q(role=UserRoleChoice.OFFICIAL))
            else:
                users = users.filter(last_heartbeat__lte=threshold)

        offset = request.query.offset
        limit = request.query.limit
        rows = users.order_by('name_pinyin', 'lower_name', 'id')[offset:offset + limit]
        level_names = request.user.space.level_names or []
        payload = []
        for user in rows:
            item = user.jsonl()
            level_index = max(0, min(len(level_names) - 1, user.growth_level - 1))
            item['growth_level_name'] = level_names[level_index] if level_names else ''
            payload.append(item)
        return payload


class SpaceAdminUserListView(SpaceUserListView):
    @staticmethod
    def _contact_status(user):
        return dict(
            email=dict(
                bound=bool(user.email),
                verified=user.email_verified_at is not None,
            ),
            sms=dict(
                bound=bool(user.phone),
                verified=user.phone_verified_at is not None,
            ),
            bark=dict(
                bound=user.instant_notification_endpoints.exists(),
                verified=user.instant_notification_endpoints.filter(verified_at__isnull=False).exists(),
            ),
        )

    @staticmethod
    def _notification_status(user, preferences):
        rows = []
        for channel in NotificationPreference.supported_channels():
            pref = preferences.get((user.id, channel))
            rows.append(dict(
                channel=channel,
                enabled=pref.enabled if pref else NotificationPreference._default_enabled(user, channel),
                offline_threshold_minutes=(
                    None if channel == UserNotificationChoice.BARK else (
                        pref.offline_threshold_minutes
                        if pref
                        else NotificationPreference._default_threshold(channel)
                    )
                ),
            ))
        return rows

    @auth.require_space
    @analyse.query(
        SpaceUserListParams.q,
        SpaceUserListParams.online,
        SpaceUserListParams.limit,
        SpaceUserListParams.offset,
    )
    def get(self, request: Request):
        active_users = User.objects.filter(
            space=request.space,
            is_deleted=False,
            role=UserRoleChoice.MEMBER,
        ).annotate(
            admin_statement_count=Count('statements', filter=Q(statements__is_deleted=False), distinct=True),
        )

        if request.query.q:
            keyword = request.query.q.strip()
            active_users = active_users.filter(
                Q(name__icontains=keyword) |
                Q(lower_name__contains=keyword.lower())
            )

        online = request.query.online
        if self.force_online is not None:
            online = 1 if self.force_online else 0
        if online is not None:
            threshold = timezone.now() - datetime.timedelta(minutes=User.vldt.OFFLINE_MIN_INTERVAL)
            if bool(online):
                active_users = active_users.filter(last_heartbeat__gt=threshold)
            else:
                active_users = active_users.filter(last_heartbeat__lte=threshold)

        offset = request.query.offset
        limit = request.query.limit
        active_rows = list(active_users.order_by('name_pinyin', 'lower_name', 'id'))

        deleted_rows = []
        if online is None or not bool(online):
            deleted_users = User.objects.filter(
                space=request.space,
                is_deleted=True,
                role=UserRoleChoice.MEMBER,
            ).order_by('name_pinyin', 'name', 'id')
            if request.query.q:
                keyword = request.query.q.strip()
                deleted_users = deleted_users.filter(name__icontains=keyword)
            deleted_rows = [user for user in deleted_users if user.has_removal_residue()]

        rows = active_rows + deleted_rows
        paged_rows = rows[offset:offset + limit]
        user_ids = [user.id for user in paged_rows]
        preferences = {
            (pref.user_id, pref.channel): pref
            for pref in NotificationPreference.objects.filter(user_id__in=user_ids)
        }
        payload = []
        for user in paged_rows:
            item = user.json_admin()
            level_names = request.space.level_names or []
            level_index = max(0, min(len(level_names) - 1, user.growth_level - 1))
            item['growth_level_name'] = level_names[level_index] if level_names else ''
            item['contacts'] = self._contact_status(user)
            item['notification_preferences'] = self._notification_status(user, preferences)
            item['friend_count'] = Friendship.objects.filter(
                space=request.space,
                status=FriendshipStatusChoice.ACCEPTED,
            ).filter(Q(user_low=user) | Q(user_high=user)).count()
            item['statement_count'] = getattr(user, 'admin_statement_count', 0)
            payload.append(item)
        return payload


class SpaceAdminBroadcastView(View):
    @auth.require_space
    @analyse.json(
        SpaceAdminBroadcastParams.content,
        SpaceAdminBroadcastParams.type,
        SpaceAdminBroadcastParams.broadcast_id,
    )
    def post(self, request: Request):
        official = request.space.ensure_official_user()
        recipients = list(User.objects.filter(
            space=request.space,
            is_deleted=False,
            role=UserRoleChoice.MEMBER,
        ).order_by('id'))
        created_count = 0
        notification_event_ids = []

        with transaction.atomic():
            for recipient in recipients:
                Friendship.ensure_locked_friendship(official, recipient)
                chat = Chat.get_or_create_direct(official, recipient)
                message = Message.create(
                    chat=chat,
                    user=official,
                    message_type=request.json.type,
                    content=request.json.content,
                    client_message_id=request.json.broadcast_id,
                )
                if not getattr(message, '_was_created', True):
                    continue
                created_count += 1
                events = NotificationEvent.emit_message_notifications(
                    message,
                    actor=official,
                    enqueue=False,
                )
                notification_event_ids.extend(event.id for event in events)

            NotificationEvent._enqueue_deliveries_after_commit(notification_event_ids)

        return dict(
            recipients_count=len(recipients),
            sent_count=created_count,
            duplicate_count=len(recipients) - created_count,
        )


class SpaceAdminBroadcastUploadView(View):
    @auth.require_space
    @analyse.json(
        MessageParams.kind,
        MessageParams.file_name,
        MessageParams.content_type,
    )
    def post(self, request: Request):
        if request.json.kind in {'video', 'file'} and request.space.verification_tier == 'email':
            raise SpaceErrors.TIER_FEATURE_RESTRICTED
        return issue_message_upload(
            kind=request.json.kind,
            file_name=request.json.file_name,
            content_type=request.json.content_type,
        )


class SpaceAdminUserRemoveView(View):
    @auth.require_space
    @analyse.query(
        UserParams.admin_user_id,
    )
    def delete(self, request: Request):
        user = request.query.user
        if user.space_id != request.space.id:
            raise UserErrors.USER_FORBIDDEN
        user.remove()
        return {}


class SpaceOfficialLoginExchangeView(View):
    @analyse.json(
        SpaceOfficialLoginTicketParams.token,
    )
    def post(self, request: Request):
        user = OfficialLoginTicket.exchange(request.json.token)
        user.log_login(ip=_extract_client_ip(request))
        return dict(
            space=user.space.json(),
            auth=auth.get_login_token(user),
        )
