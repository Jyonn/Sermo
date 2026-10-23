"""Request-local, lossless schema tables and object deduplication for JSON payloads."""
import gzip
import json


def compact_payload(payload, kind, *, accept_gzip=False):
    schemas, schema_ids, records, record_ids = [], {}, [], {}

    def encode(value):
        if isinstance(value, (list, tuple)):
            return [-1, *[encode(item) for item in value]]
        if not isinstance(value, dict):
            return value
        keys = tuple(sorted(value))
        if keys not in schema_ids:
            schema_ids[keys] = len(schemas)
            schemas.append(list(keys))
        row = [schema_ids[keys], *[encode(value[key]) for key in keys]]
        identity = json.dumps(row, ensure_ascii=False, separators=(',', ':'), default=str)
        if identity not in record_ids:
            record_ids[identity] = len(records)
            records.append(row)
        return [-2, record_ids[identity]]

    root = encode(payload)
    compact = dict(format='compact-v1', kind=kind, schemas=schemas, records=records, root=root)

    def size(value):
        # Match APIPacker's actual JSON spacing when deciding whether to fall back.
        data = json.dumps(value, ensure_ascii=False, default=str).encode()
        return len(gzip.compress(data, mtime=0)) if accept_gzip else len(data)

    return compact if size(compact) < size(payload) else payload


def compact_response(request, payload, kind):
    if request.GET.get('format') != 'compact-v1':
        return payload
    return compact_payload(payload, kind, accept_gzip='gzip' in request.headers.get('Accept-Encoding', '').lower())
