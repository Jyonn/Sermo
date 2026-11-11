"""Opt-in two-participant media smoke test; use dedicated fixtures only.

Optional test dependencies: livekit, numpy, requests. Credentials stay in the
fixture JSON file; never pass access tokens on the command line.
"""
import argparse
import asyncio, json, uuid, math, array, pathlib, sys

import requests
import numpy as np
from livekit import rtc

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--fixtures', required=True, type=pathlib.Path)
parser.add_argument('--api', required=True, help='API origin, including any path prefix')
parser.add_argument('--media-test-url', help='Optional isolated test proxy; not public-network acceptance')
parser.add_argument('--wait-for-quota', action='store_true', help='Require a fixture with at most 20 seconds available')
args=parser.parse_args()
fixtures=json.loads(args.fixtures.read_text())
base=args.api.rstrip('/')
devices=[str(uuid.uuid4()),str(uuid.uuid4())]
sessions=[requests.Session(),requests.Session()]
def api(i,path,body=None):
    r=sessions[i].request('GET' if body is None else 'POST',base+'/chats/voice/'+path,
        json=body,headers={'Authorization':'Bearer '+fixtures['users'][i]['auth']},timeout=12)
    result=r.json()
    if r.status_code!=200 or result['identifier']!='OK':
        raise RuntimeError((r.status_code,result.get('identifier'),result.get('message')))
    return result['body']

async def main():
    cap=await asyncio.to_thread(api,0,'capability?chat_id='+str(fixtures['chat_id']))
    peer_cap=await asyncio.to_thread(api,1,'capability?chat_id='+str(fixtures['chat_id']))
    assert cap['can_call'], cap['reason']
    if args.wait_for_quota: assert 0 < cap['max_seconds'] <= 20
    print('preflight',cap['can_call'],cap['remaining_seconds'],flush=True)
    call=await asyncio.to_thread(api,0,'calls',dict(chat_id=fixtures['chat_id'],client_key=str(uuid.uuid4()),device_id=devices[0]))
    call_id=call['call_id']
    tasks=[]; rooms=[]; sources=[]; frames=[[],[]]; stopped=False
    async def pulse():
        while not stopped:
            for i in range(2):
                try: await asyncio.to_thread(api,i,f'calls/{call_id}/heartbeat',dict(device_id=devices[i]))
                except Exception as e: print('heartbeat error',type(e).__name__,flush=True)
            await asyncio.sleep(.7)
    try:
        await asyncio.to_thread(api,1,f'calls/{call_id}/accept',dict(device_id=devices[1]))
        tasks.append(asyncio.create_task(pulse()))
        credentials=[await asyncio.to_thread(api,i,f'calls/{call_id}/media',dict(device_id=devices[i])) for i in range(2)]
        async def read(i,track):
            async for packet in rtc.AudioStream(track,sample_rate=48000,num_channels=1):
                frames[i].append(np.frombuffer(packet.frame.data,dtype=np.int16).copy())
        for i in range(2):
            room=rtc.Room(); rooms.append(room)
            def subscribed(track,pub,participant,index=i):
                if track.kind==rtc.TrackKind.KIND_AUDIO:
                    tasks.append(asyncio.create_task(read(index,track)))
            room.on('track_subscribed',subscribed)
            await asyncio.wait_for(room.connect(args.media_test_url or credentials[i]['url'],credentials[i]['token'],rtc.RoomOptions(single_peer_connection=False)),20)
            source=rtc.AudioSource(48000,1); sources.append(source)
            track=rtc.LocalAudioTrack.create_audio_track('microphone',source)
            options=rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            await room.local_participant.publish_track(track,options)
            print('connected/published',i,flush=True)
        async def send(i):
            freq=[440,660][i]; offset=0
            while not stopped:
                samples=array.array('h',(int(8000*math.sin(2*math.pi*freq*(offset+n)/48000)) for n in range(480)))
                await sources[i].capture_frame(rtc.AudioFrame(samples.tobytes(),48000,1,480))
                offset+=480
        tasks.extend(asyncio.create_task(send(i)) for i in range(2))
        for _ in range(16):
            await asyncio.sleep(.5)
            current=await asyncio.to_thread(api,0,'current?device_id='+devices[0])
            if current['call'] and current['call']['state']=='active' and all(len(x)>100 for x in frames): break
        current=await asyncio.to_thread(api,0,'current?device_id='+devices[0])
        assert current['call']['state']=='active',current
        for i in range(2):
            assert len(frames[i])>50,(i,len(frames[i]))
            data=np.concatenate(frames[i][-50:]).astype(float)
            rms=float(np.sqrt(np.mean(data**2)))
            spectrum=np.abs(np.fft.rfft(data))
            dominant=float(np.fft.rfftfreq(len(data),1/48000)[np.argmax(spectrum)])
            assert rms>500 and abs(dominant-[660,440][i])<15,(i,rms,dominant)
            print('received_audio',dict(endpoint=i,frames=len(frames[i]),rms=round(rms),frequency=round(dominant)),flush=True)
        if args.wait_for_quota:
            for _ in range(44):
                ended=await asyncio.to_thread(api,0,f'calls/{call_id}/heartbeat',dict(device_id=devices[0]))
                if ended['state']=='ended': break
                await asyncio.sleep(.5)
            assert ended['state']=='ended' and ended['reason']=='quota',ended
            await asyncio.sleep(1.5)
            assert all(room.connection_state==rtc.ConnectionState.CONN_DISCONNECTED for room in rooms)
            print('quota_auto_disconnect','both endpoints disconnected','PASS',flush=True)
        else:
            ended=await asyncio.to_thread(api,0,f'calls/{call_id}/end',dict(device_id=devices[0]))
        print('settled',dict(state=ended['state'],duration=ended['duration_seconds']),flush=True)
        await asyncio.to_thread(api,1,f'calls/{call_id}/end',dict(device_id=devices[1]))
        cap2=await asyncio.to_thread(api,0,'capability?chat_id='+str(fixtures['chat_id']))
        assert cap['remaining_seconds']-cap2['remaining_seconds']==ended['duration_seconds']
        peer_after=await asyncio.to_thread(api,1,'capability?chat_id='+str(fixtures['chat_id']))
        assert peer_cap['remaining_seconds']-peer_after['remaining_seconds']==ended['duration_seconds']
        from urllib.parse import quote
        invalid=await asyncio.to_thread(requests.get,credentials[0]['url'].replace('wss://','https://')+'/rtc/validate?access_token='+quote(credentials[0]['token']),timeout=10)
        assert invalid.status_code==403,invalid.status_code
        print('old_token_reentry',invalid.status_code,'PASS',flush=True)
    finally:
        stopped=True
        try: await asyncio.to_thread(api,0,f'calls/{call_id}/end',dict(device_id=devices[0]))
        except Exception: pass
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.gather(*(room.disconnect() for room in rooms),return_exceptions=True)

try:
    asyncio.run(main())
except Exception as exc:
    # Network exceptions can include signed URLs. Do not print their raw text.
    print('Media smoke test failed:',type(exc).__name__,file=sys.stderr)
    sys.exit(1)

