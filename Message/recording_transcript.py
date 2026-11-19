"""Bind a signed live result to its sender's audio exactly once."""
from uuid import uuid4
from django.core import signing
from django.db import transaction, IntegrityError
SALT = 'frienden.recording-transcript.v1'


def receipt(principal, text):
    return signing.dumps({'principal': principal, 'text': text[:12000], 'nonce': uuid4().hex}, salt=SALT, compress=True)


def attach(message, user, token):
    from Message.models import AudioTranscript, AudioTranscriptStatusChoice, MediaAsset, MessageTypeChoice
    if not token or message.type != MessageTypeChoice.AUDIO or not message.media_resource_id:
        return False
    try:
        data = signing.loads(token, salt=SALT, max_age=3600)
        permitted = {f'user:{user.id}'}
        if user.is_official:
            permitted.add(f'space:{user.space_id}')
        if data['principal'] not in permitted or not isinstance(data['text'], str):
            return False
        resource = message.media_resource
        if resource.owner_id != user.id:
            return False
        with transaction.atomic():
            asset = MediaAsset.objects.select_for_update().get(id=resource.asset_id)
            existing = AudioTranscript.objects.filter(asset=asset).first()
            if existing and existing.status == AudioTranscriptStatusChoice.READY:
                return existing.live_receipt_id == data['nonce']
            transcript = existing or AudioTranscript.objects.create(asset=asset)
            transcript.live_receipt_id = data['nonce']
            transcript.save(update_fields=['live_receipt_id'])
            transcript.mark_ready(data['text'], request_id='aliyun-live')
        return True
    except (signing.BadSignature, ValueError, KeyError, TypeError, IntegrityError):
        return False
