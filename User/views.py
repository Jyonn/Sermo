from django.views import View
from smartdjango import analyse, OK

from Chat.models import SingleChat, GroupChat
from User import auth
from User.auth import Request
from User.models import (
    HostUser,
    GuestUser,
    NotificationPreference,
    Space,
    FriendRequest,
    Friendship,
    EmailVerificationCode,
    UserNotificationChoice,
)
from Message.models import Message
from User.validators import UserErrors, is_reserved_subdomain
from User.params import (
    HostUserParams,
    GuestUserParams,
    AuthParams,
    SubdomainParams,
    GuestListParams,
    GuestDeleteParams,
    NotificationPreferenceParams,
    SpaceParams,
    SpaceJoinParams,
    FriendParams,
    EmailVerificationParams,
)
from User.notificator_client import send as send_notification


class HostView(View):
    def get(self, request: Request):
        subdomain = auth.get_request_subdomain(request)
        if not subdomain:
            raise UserErrors.SUBDOMAIN_REQUIRED
        host = HostUser.get_by_subdomain(subdomain)
        return host.json()

    @analyse.json(HostUserParams.name, HostUserParams.password)
    def post(self, request: Request):
        host = HostUser.login(request.json.name, request.json.password)
        return auth.get_login_token(host)


class GuestView(View):
    @analyse.json(GuestUserParams.name, GuestUserParams.password)
    def post(self, request: Request):
        subdomain = auth.get_request_subdomain(request)
        if not subdomain:
            raise UserErrors.SUBDOMAIN_REQUIRED
        host = HostUser.get_by_subdomain(subdomain)
        guest = GuestUser.login(request.json.name, request.json.password, host)

        SingleChat.get_or_create(guest)
        return auth.get_login_token(guest)


class HeartbeatView(View):
    @auth.require_user
    def get(self, request: Request):
        request.user.heartbeat()
        return OK


class RefreshView(View):
    @analyse.json(AuthParams.refresh)
    def post(self, request: Request):
        return auth.refresh_login_token(request.json.refresh)


class LogoutView(View):
    @analyse.json(AuthParams.refresh)
    def post(self, request: Request):
        auth.revoke_refresh_token(request.json.refresh)
        return OK


class SubdomainView(View):
    @analyse.query(SubdomainParams.subdomain)
    def get(self, request: Request):
        subdomain = request.query.subdomain.strip().lower()
        HostUser.vldt.subdomain(subdomain)
        if is_reserved_subdomain(subdomain):
            return dict(available=False, reason='reserved')
        if HostUser.objects.filter(subdomain=subdomain).exists():
            return dict(available=False, reason='taken')
        return dict(available=True)

    @auth.require_host_user
    @analyse.json(SubdomainParams.subdomain)
    def post(self, request: Request):
        subdomain = request.json.subdomain.strip().lower()
        request.user.set_subdomain(subdomain)
        return request.user.json()


class GuestNicknameView(View):
    @analyse.query(GuestUserParams.name)
    def get(self, request: Request):
        subdomain = auth.get_request_subdomain(request)
        if not subdomain:
            raise UserErrors.SUBDOMAIN_REQUIRED
        host = HostUser.get_by_subdomain(subdomain)
        name = request.query.name
        guest = GuestUser.objects.filter(
            lower_name=name.lower(),
            host=host,
        ).first()
        if guest is None:
            return dict(available=True)
        if guest.is_deleted:
            return dict(available=False, reason='deleted')
        if guest.password:
            return dict(available=False, reason='password_required')
        return dict(available=False, reason='taken')


class HostGuestListView(View):
    @auth.require_host_user
    @analyse.query(
        GuestListParams.q,
        GuestListParams.online,
        GuestListParams.limit,
        GuestListParams.offset,
    )
    def get(self, request: Request):
        guests = GuestUser.objects.filter(host=request.user, is_deleted=False)
        if request.query.q:
            guests = guests.filter(lower_name__contains=request.query.q.lower())
        guests = list(guests.order_by('-last_heartbeat', 'id'))
        if request.query.online is not None:
            online = bool(request.query.online)
            guests = [guest for guest in guests if guest.is_alive == online]
        offset = request.query.offset
        limit = request.query.limit
        return [guest.json() for guest in guests[offset:offset + limit]]


class GuestDeleteView(View):
    @auth.require_host_user
    @analyse.query(GuestUserParams.guest_id, GuestDeleteParams.purge_group_messages)
    def delete(self, request: Request):
        guest = request.query.guest
        if guest.host != request.user:
            raise UserErrors.GUEST_FORBIDDEN

        guest.is_deleted = True
        guest.save(update_fields=['is_deleted'])

        SingleChat.objects.filter(guest=guest, is_deleted=False).update(is_deleted=True)

        group_chats = GroupChat.objects.filter(guests=guest, is_deleted=False)
        if request.query.purge_group_messages:
            Message.objects.filter(chat__in=group_chats, user=guest, is_deleted=False).update(is_deleted=True)
        for chat in group_chats:
            chat.guests.remove(guest)

        return OK


class NotificationPreferenceView(View):
    @auth.require_user
    def get(self, request: Request):
        prefs = NotificationPreference.ensure_defaults(request.user)
        return [pref.json() for pref in prefs]

    @auth.require_user
    @analyse.json(
        NotificationPreferenceParams.channel,
        NotificationPreferenceParams.enabled,
        NotificationPreferenceParams.offline_threshold_minutes,
    )
    def post(self, request: Request):
        enabled = request.json.enabled
        pref = NotificationPreference.set_preference(
            user=request.user,
            channel=request.json.channel,
            enabled=None if enabled is None else bool(enabled),
            offline_threshold_minutes=request.json.offline_threshold_minutes,
        )
        return pref.json()


class EmailVerificationCodeRequestView(View):
    @auth.require_user
    @analyse.json(EmailVerificationParams.email)
    def post(self, request: Request):
        email = request.json.email.strip().lower()
        verify_code = EmailVerificationCode.issue(request.user, email)
        title = 'Sermo verification code'
        body = f'Your verification code is {verify_code.code}. It expires in 10 minutes.'
        ok, detail = send_notification(
            channel=UserNotificationChoice.EMAIL,
            target=email,
            title=title,
            body=body,
            recipient_name=request.user.specify().name,
        )
        if not ok:
            raise UserErrors.EMAIL_SEND_FAILED(details=detail)
        return dict(expires_in=EmailVerificationCode.EXPIRE_SECONDS)


class EmailVerificationConfirmView(View):
    @auth.require_guest_user
    @analyse.json(
        EmailVerificationParams.email,
        EmailVerificationParams.code,
        EmailVerificationParams.password,
    )
    def post(self, request: Request):
        email = request.json.email.strip().lower()
        EmailVerificationCode.verify(
            user=request.user,
            email=email,
            code=request.json.code,
        )
        guest: GuestUser = request.user
        guest.verify_email_and_upgrade(
            email=email,
            password=request.json.password,
        )
        NotificationPreference.set_preference(
            user=guest,
            channel=UserNotificationChoice.EMAIL,
            enabled=True,
        )
        return guest.json()


class SpaceView(View):
    @analyse.json(
        SpaceParams.name,
        SpaceParams.slug,
        SpaceParams.official_name,
        SpaceParams.password,
    )
    def post(self, request: Request):
        official_name = request.json.official_name
        if official_name is None:
            official_name = request.json.slug
        space = Space.create(
            name=request.json.name.strip(),
            slug=request.json.slug.strip(),
            official_name=official_name.strip(),
            password=request.json.password,
        )
        return dict(
            space=space.json(),
            auth=auth.get_login_token(space.official_user),
        )


class SpaceJoinView(View):
    @analyse.json(
        SpaceJoinParams.slug,
        SpaceJoinParams.name,
        SpaceJoinParams.password,
    )
    def post(self, request: Request):
        space = Space.get_by_slug(request.json.slug.strip().lower())
        guest = GuestUser.login(
            name=request.json.name,
            password=request.json.password,
            host=space.official_user,
        )
        SingleChat.get_or_create(guest)
        return dict(
            space=space.json(),
            auth=auth.get_login_token(guest),
        )


class SpaceMeView(View):
    @auth.require_user
    def get(self, request: Request):
        return request.user.space.json()


class SpaceUserListView(View):
    @auth.require_user
    @analyse.query(
        GuestListParams.q,
        GuestListParams.online,
        GuestListParams.limit,
        GuestListParams.offset,
    )
    def get(self, request: Request):
        space = request.user.space
        host = space.official_user
        guests = list(GuestUser.objects.filter(host=host, is_deleted=False))
        users = [host] + guests

        if request.query.q:
            keyword = request.query.q.lower()
            users = [user for user in users if keyword in user.name.lower()]
        if request.query.online is not None:
            users = [user for user in users if user.is_alive == bool(request.query.online)]

        users.sort(key=lambda x: (x.id != host.id, -x.last_heartbeat.timestamp(), x.id))
        offset = request.query.offset
        limit = request.query.limit

        data = []
        for user in users[offset:offset + limit]:
            item = user.json()
            item['official'] = user.id == host.id
            data.append(item)
        return data


class SpaceOnlineUserListView(View):
    @auth.require_user
    @analyse.query(
        GuestListParams.q,
        GuestListParams.limit,
        GuestListParams.offset,
    )
    def get(self, request: Request):
        space = request.user.space
        host = space.official_user
        guests = list(GuestUser.objects.filter(host=host, is_deleted=False))
        users = [host] + guests

        if request.query.q:
            keyword = request.query.q.lower()
            users = [user for user in users if keyword in user.name.lower()]
        users = [user for user in users if user.is_alive]
        users.sort(key=lambda x: (x.id != host.id, -x.last_heartbeat.timestamp(), x.id))

        offset = request.query.offset
        limit = request.query.limit

        data = []
        for user in users[offset:offset + limit]:
            item = user.json()
            item['official'] = user.id == host.id
            data.append(item)
        return data


class FriendListView(View):
    @auth.require_user
    def get(self, request: Request):
        friends = Friendship.friends_of(request.user)
        return [friend.json() for friend in friends]


class FriendRequestView(View):
    @auth.require_user
    @analyse.json(FriendParams.to_user_id)
    def post(self, request: Request):
        request_obj = FriendRequest.create_request(
            from_user=request.user,
            to_user=request.json.to_user,
        )
        return request_obj.json()

    @auth.require_user
    def get(self, request: Request):
        user = request.user.specify()
        incoming = FriendRequest.objects.filter(to_user=user).order_by('-created_at')[:100]
        outgoing = FriendRequest.objects.filter(from_user=user).order_by('-created_at')[:100]
        return dict(
            incoming=[obj.json() for obj in incoming],
            outgoing=[obj.json() for obj in outgoing],
        )


class FriendRequestRespondView(View):
    @auth.require_user
    @analyse.query(FriendParams.request_id)
    @analyse.json(FriendParams.accept)
    def post(self, request: Request):
        request_obj = request.query.friend_request
        if request.json.accept:
            request_obj.accept(request.user)
        else:
            request_obj.reject(request.user)
        return request_obj.json()
