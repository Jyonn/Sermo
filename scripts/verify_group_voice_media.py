"""Verify three local accounts with real LiveKit audio and independent billing."""
import argparse
import asyncio
import json
import uuid
from pathlib import Path

import numpy as np
import requests
from livekit import rtc


async def verify(origin, fixtures):
    devices = [str(uuid.uuid4()) for _ in range(3)]
    frequencies = [440, 660, 880]
    chat_id = fixtures['chat_id']
    rooms = [rtc.Room() for _ in devices]
    frames = [{} for _ in devices]
    tasks = []
    call_id = None
    joined = set()

    async def api(index, path, body=None):
        result = await asyncio.to_thread(requests.request, 'GET' if body is None else 'POST',
            f'{origin}/chats/voice/{path}', json=body, timeout=10,
            headers={'Authorization': 'Bearer ' + fixtures['users'][index]['auth']})
        if result.status_code != 200:
            raise AssertionError((path, result.status_code))
        return result.json()['body']

    async def action(index, name):
        return await api(index, f'calls/{call_id}/{name}', {'device_id': devices[index]})

    async def heartbeat():
        while True:
            for index in sorted(joined):
                await action(index, 'heartbeat')
            await asyncio.sleep(1)

    async def receive(index, identity, track):
        frames[index][identity] = []
        async for packet in rtc.AudioStream(track, sample_rate=48000, num_channels=1):
            frames[index][identity].append(np.frombuffer(packet.frame.data, dtype=np.int16).copy())

    async def send(source, frequency):
        frame = (np.sin(2 * np.pi * frequency * np.arange(4800) / 48000) * 8000).astype(np.int16)
        while True:
            await source.capture_frame(rtc.AudioFrame(frame.tobytes(), 48000, 1, 4800))

    def bind_receiver(index):
        @rooms[index].on('track_subscribed')
        def subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                tasks.append(asyncio.create_task(receive(index, participant.identity, track)))

    async def expect_audio(indices):
        for index in indices:
            for remote in indices:
                if remote == index:
                    continue
                identity = rooms[remote].local_participant.identity
                for _ in range(60):
                    received = frames[index].get(identity, [])
                    if len(received) >= 24:
                        break
                    await asyncio.sleep(.2)
                assert len(received) >= 24, (index, remote, 'no audio')
                data = np.concatenate(received[-24:]).astype(float)
                peak = np.fft.rfftfreq(len(data), 1 / 48000)[np.argmax(np.abs(np.fft.rfft(data)))]
                assert np.sqrt(np.mean(data ** 2)) > 500 and abs(peak - frequencies[remote]) < 15

    capability_path = f'capability?chat_id={chat_id}'
    before = [await api(index, capability_path) for index in range(3)]
    assert all(cap['can_call'] and cap['remaining_seconds'] == 1800 for cap in before)
    tasks.append(asyncio.create_task(heartbeat()))
    credentials = []
    try:
        for index, room in enumerate(rooms):
            bind_receiver(index)
            call = await api(index, 'calls', dict(chat_id=chat_id, client_key=str(uuid.uuid4()),
                                                   device_id=devices[index]))
            call_id = call_id or call['call_id']
            assert call['group'] and call['call_id'] == call_id
            joined.add(index)
            media = await action(index, 'media')
            credentials.append(media)
            await asyncio.wait_for(room.connect(media['url'], media['token'],
                rtc.RoomOptions(single_peer_connection=False)), timeout=15)
            source = rtc.AudioSource(48000, 1)
            track = rtc.LocalAudioTrack.create_audio_track('microphone', source)
            await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
            tasks.append(asyncio.create_task(send(source, frequencies[index])))
        await expect_audio(range(3))
        print('All three accounts decoded both other microphones.', flush=True)
        durations = []
        for index in range(3):
            joined.remove(index)
            ended = await action(index, 'end')
            assert ended['state'] == 'ended' and ended['duration_seconds'] > 0
            durations.append(ended['duration_seconds'])
            await rooms[index].disconnect()
            denied = await asyncio.to_thread(requests.get, f'{origin}/chats/voice/media-admission',
                headers={'X-Voice-Token': credentials[index]['token']}, timeout=10)
            assert denied.status_code == 403
            if index == 0:
                for other in (1, 2):
                    frames[other][rooms[3-other].local_participant.identity].clear()
                await expect_audio([1, 2])
                assert all([(await action(other, 'heartbeat'))['state'] == 'active' for other in (1, 2)])
                print('The two remaining accounts kept exchanging audio after the host left.', flush=True)
            await asyncio.sleep(1.1)
        for index in range(3):
            after = await api(index, capability_path)
            assert not after['ongoing'] and after['remaining_seconds'] == 1800 - durations[index]
        assert len(set(durations)) > 1, durations
        print(f'Final leave ended the room. Independent charges in seconds: {durations}.', flush=True)
    finally:
        for index in sorted(joined):
            try:
                await action(index, 'end')
            except (AssertionError, requests.RequestException):
                pass
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(room.disconnect() for room in rooms), return_exceptions=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixtures', required=True, type=Path)
    parser.add_argument('--api', default='http://127.0.0.1:8901',
                        choices=['http://127.0.0.1:8901'])
    args = parser.parse_args()
    try:
        asyncio.run(verify(args.api, json.loads(args.fixtures.read_text())))
    except Exception as exc:
        # Transport errors may contain signed media URLs.
        raise SystemExit(f'Group voice smoke test failed: {type(exc).__name__}') from None
