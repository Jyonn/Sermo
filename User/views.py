from django.views import View
from smartdjango import analyse, OK

from Chat.models import SingleChat, GroupChat
from User import auth
from User.auth import Request
from User.models import HostUser, GuestUser
from Message.models import Message
from User.validators import UserErrors, is_reserved_subdomain
from User.params import (
    HostUserParams,
    GuestUserParams,
    AuthParams,
    SubdomainParams,
    GuestListParams,
    GuestDeleteParams,
)


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
