"""Synchronous Aliyun ASR; no Qiniu recognition fallback or payload logging."""
import os
import requests
from Message.live_transcription import provider_config
from utils.qiniu import ShortAudioTranscriptionError


def transcribe_short_audio(audio_url):
    connection = provider_config()
    if not connection:
        raise ShortAudioTranscriptionError('Aliyun transcription unavailable')
    key, websocket_url = connection
    host = websocket_url.split('/')[2]
    try:
        response = requests.post(f'https://{host}/api/v1/services/aigc/multimodal-generation/generation',
            headers={'Authorization': f'Bearer {key}'}, timeout=(5, 70),
            json={'model': os.environ.get('QWEN_FILE_MODEL', 'qwen3-asr-flash'),
                  'input': {'messages': [{'role': 'user', 'content': [{'audio': audio_url}]}]},
                  'parameters': {'asr_options': {'enable_itn': False}}})
        response.raise_for_status()
        body = response.json()
        content = body['output']['choices'][0]['message']['content']
        text = content if isinstance(content, str) else ''.join(item.get('text', '') for item in content)
        return text.strip(), str(body.get('request_id', ''))[:128]
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError):
        raise ShortAudioTranscriptionError('Aliyun transcription failed') from None
