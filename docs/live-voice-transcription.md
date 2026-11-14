# Live voice transcription

The shared hold-to-talk composer records its ordinary compressed voice message
while an AudioWorklet sends 16 kHz mono signed little-endian PCM to a dedicated
ASGI service. Audio and transcripts are transient; cancelling or sending voice
closes transcription. Converting to text waits for the final sentence and lets
the user review a draft. Provider failure must never prevent sending voice.

## Deployment

- Install `requirements-transcription.txt` in the backend virtual environment.
- Provide `DASHSCOPE_API_KEY` and `DASHSCOPE_REGION` (`cn-beijing` or
  `ap-southeast-1`) to both the API and the sidecar through a private server
  environment file. Optional: `DASHSCOPE_WORKSPACE_ID`, `QWEN_STREAM_MODEL`.
  The default model is `qwen-audio-3.1-asr-flash-message`.
- Run `uvicorn Sermo.transcription_asgi:application --host 127.0.0.1 --port 6973
  --workers 1 --lifespan off --no-access-log --ws-max-size 65536` from the same
  release directory as the API. It needs the same Django/database configuration.
- Proxy only `/ws/voice-transcription` to this port, with HTTP/1.1 Upgrade,
  buffering off, and read/send timeouts of 100 seconds. Disable access logging
  on this route. Do not route ordinary API requests to the sidecar.

**One worker is required.** One-use tickets, active-session admission and rate
limits are in memory. Scaling out requires shared atomic admission first.

## Protocol and limits

1. Authenticated POST `/messages/voice-stream-ticket`, or
   `/spaces/admin/voice-stream-ticket` for the existing space-admin composer.
   Returns a signed, one-use, 60-second ticket; response is `private, no-store`.
2. Connect to `/ws/voice-transcription` from one of the four frontend origins.
   Send `{ "ticket": "…" }` as the first frame, never in the URL.
3. Wait for `{ "type": "ready" }`, then send PCM binary frames.
4. Receive `transcript` with `text`, `final_text` and `partial_text`. Partial
   updates replace the matching sentence instead of appending duplicates.
5. Flush PCM then send `{ "type": "finish" }`; `done` includes final `text`.
   `error` means transcription is unavailable; retain the recording.

Maximum 60 seconds of PCM, 90-second session deadline, 12-second idle deadline,
8-second finalization, one simultaneous session per account, 32 global sessions,
12 starts/minute and 120 starts/hour per account. No transcript/audio storage or
provider payload logging. Only exact allowed origins are accepted (localhost
origins additionally allowed with Django DEBUG for development).

## Checks

`python manage.py test Message.tests_live_transcription
--settings=Sermo.isolated_test_settings --noinput` covers authentication, ticket
replay/expiry, origin checks, sentence replacement and relay cleanup. Frontend
tests additionally cover sample-rate conversion, gesture decisions, cancellation
and asynchronous finalization. Use a public sample for real provider checks;
do not replay private chat recordings as test fixtures.
