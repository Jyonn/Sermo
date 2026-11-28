from django.views import View
from smartdjango import OK, Validator, analyse

from Chat.models import Chat, ChatPurposeChoice
from Message.validators import MessageErrors
from Square.models import SquareMute, StatementComment
from User.anonymous_persona import discard_persona, issue_persona, persona_token
from User.validators import UserErrors
from utils import auth


chat_target = Validator('chat_id').to(int).null().default(None)
statement_target = Validator('statement_id').to(int).null().default(None)


class AnonymousPersonaView(View):
    @auth.require_user
    @analyse.json(chat_target, statement_target)
    def post(self, request):
        user = request.user
        chat_id = request.json.chat_id
        statement_id = request.json.statement_id
        if bool(chat_id) == bool(statement_id):
            raise UserErrors.ANONYMOUS_PERSONA_SCOPE_INVALID
        if chat_id:
            user.space.require_chat_enabled()
            chat = Chat.objects.filter(pk=chat_id, space=user.space).first()
            if not chat or not chat.group or chat.purpose != ChatPurposeChoice.NORMAL or not chat.has_active_member(user):
                raise MessageErrors.ANONYMOUS_GROUP_TEXT_ONLY
            user.space.require_group_send_allowed(user)
            chat.require_user_message_allowed(user)
            user.require_capability('chat.message.send.text')
            return issue_persona(user, 'chat', chat.id)
        user.space.require_square_enabled()
        StatementComment.statement_for_user(user, statement_id)
        SquareMute.require_can_participate(user)
        # Commenting and replying have independent permissions and are checked again at send time.
        return issue_persona(user, 'square', statement_id)

    @auth.require_user
    @analyse.json(persona_token)
    def delete(self, request):
        discard_persona(request.user, request.json.anonymous_token)
        return OK
