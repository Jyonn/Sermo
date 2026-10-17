"""Lossless, request-local chat-list transport; never caches data across viewers."""
import json
import gzip


def pack_records(records):
    schemas, schema_ids, rows = [], {}, []
    for record in records:
        keys = tuple(sorted(record))
        if keys not in schema_ids:
            schema_ids[keys] = len(schemas)
            schemas.append(list(keys))
        rows.append([schema_ids[keys], [record[key] for key in keys]])
    return {'schemas': schemas, 'rows': rows}


def compact_chat_list(chats, *, accept_gzip=False):
    users, user_ids = [], {}

    def user_ref(user):
        if user is None:
            return None
        identity = json.dumps(user, sort_keys=True, ensure_ascii=False, separators=(',', ':'), default=str)
        if identity not in user_ids:
            user_ids[identity] = len(users)
            users.append(user)
        return user_ids[identity]

    def message_refs(message):
        if message is None:
            return None
        result = dict(message)
        if 'user' in result:
            result['user'] = user_ref(result['user'])
        if 'mentions' in result:
            result['mentions'] = [user_ref(user) for user in result['mentions']]
        if isinstance(result.get('reply_to'), dict):
            result['reply_to'] = message_refs(result['reply_to'])
        return result

    rows = []
    member_fields = ('joined_at', 'submission_role', 'group_chat_mute')
    for chat in chats:
        row = dict(chat)
        row['owner'] = user_ref(chat['owner'])
        row['members'] = []
        for member in chat['members']:
            core = {key: value for key, value in member.items() if key not in member_fields}
            context = {key: member[key] for key in member_fields if key in member}
            row['members'].append([user_ref(core), context])
        row['last_message'] = message_refs(chat['last_message'])
        rows.append(row)
    compact = {'format': 'compact-v1', 'users': pack_records(users), 'chats': pack_records(rows)}
    # Tiny lists may cost more with tables; the client accepts either representation.
    def size(value):
        encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str).encode()
        return len(gzip.compress(encoded, mtime=0)) if accept_gzip else len(encoded)
    return compact if size(compact) < size(chats) else chats
