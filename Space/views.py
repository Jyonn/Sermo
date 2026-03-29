import datetime

from django.views import View
from django.utils import timezone
from notificator import NotificatorAPIError
from smartdjango import analyse

from Space.models import Space
from Space.models import SpaceEmailVerificationCode, SpaceEmailCodePurposeChoice
from Space.params import (
    SpaceParams,
    SpaceEmailVerificationCodeParams,
    SpaceLookupParams,
    SpaceOfficialLoginTicketParams,
    SpaceUserListParams,
)
from Space.validators import SpaceErrors
from utils import auth
from utils.auth import Request
from User.models import OfficialLoginTicket, User, UserRoleChoice
from User.params import UserParams
from User.validators import UserErrors
from utils.global_settings import notificator


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
            purpose = SpaceEmailCodePurposeChoice.REGISTER

        verify_code = SpaceEmailVerificationCode.issue(
            email=email,
            purpose=purpose,
            space=space,
        )
        title = 'Sermo space verification code'
        body = f'Your verification code is {verify_code.code}. It expires in {SpaceEmailVerificationCode.EXPIRE_SECONDS // 60} minutes.'
        try:
            notificator.mail(
                mail=verify_code.email,
                title=title,
                body=body,
                recipient_name='Space Admin',
            )
        except NotificatorAPIError as e:
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
        SpaceParams.member_limit,
    )
    def post(self, request: Request):
        space = request.space.set_admin_settings(
            name=request.json.name,
            group_square_enabled=request.json.group_square_enabled,
            member_limit=request.json.member_limit,
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
                users = users.filter(last_heartbeat__gt=threshold)
            else:
                users = users.filter(last_heartbeat__lte=threshold)

        offset = request.query.offset
        limit = request.query.limit
        rows = users.order_by('name_pinyin', 'lower_name', 'id')[offset:offset + limit]
        return [user.jsonl() for user in rows]


class SpaceAdminUserListView(SpaceUserListView):
    @auth.require_space
    @analyse.query(
        SpaceUserListParams.q,
        SpaceUserListParams.online,
        SpaceUserListParams.limit,
        SpaceUserListParams.offset,
    )
    def get(self, request: Request):
        users = User.objects.filter(
            space=request.space,
            is_deleted=False,
            role=UserRoleChoice.MEMBER,
        )

        if request.query.q:
            users = users.filter(lower_name__contains=request.query.q.lower())

        online = request.query.online
        if self.force_online is not None:
            online = 1 if self.force_online else 0
        if online is not None:
            threshold = timezone.now() - datetime.timedelta(minutes=User.vldt.OFFLINE_MIN_INTERVAL)
            if bool(online):
                users = users.filter(last_heartbeat__gt=threshold)
            else:
                users = users.filter(last_heartbeat__lte=threshold)

        offset = request.query.offset
        limit = request.query.limit
        rows = users.order_by('name_pinyin', 'lower_name', 'id')[offset:offset + limit]
        return [user.jsonl() for user in rows]


class SpaceAdminUserRemoveView(View):
    @auth.require_space
    @analyse.query(
        UserParams.user_id,
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
