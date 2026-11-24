"""Export short, level-matched audio files from record-shop source recordings."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def source_rows(manifest):
    rows = json.loads(manifest.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('expected a JSON list of album clip specifications')
    ids = set()
    missing = [str(row.get('album_id', '?')) for row in rows
               if not row.get('file') or row.get('start_ms') is None or row.get('end_ms') is None]
    if missing:
        raise ValueError(f'{len(missing)} clips need a local recording and cue: {", ".join(missing)}')
    for row in rows:
        album_id = row['album_id']
        if not isinstance(album_id, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', album_id) or album_id in ids:
            raise ValueError(f'invalid or repeated album ID: {album_id!r}')
        ids.add(album_id)
        start, end = row['start_ms'], row['end_ms']
        if type(start) is not int or type(end) is not int or start < 0 or not 0 < end - start <= 30_000:
            raise ValueError(f'{album_id}: expected millisecond offsets for a clip of up to 30 seconds')
        source = (manifest.parent / row['file']).resolve(strict=True)
        if not source.is_file():
            raise ValueError(f'{album_id}: expected a local audio recording')
        yield album_id, source, start, end


def run_ffmpeg(ffmpeg, arguments):
    return subprocess.run(
        [ffmpeg, '-nostdin', '-hide_banner', *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=150,
        check=True,
    ).stderr.decode(errors='replace')


def media_duration_ms(ffmpeg, path):
    output = run_ffmpeg(ffmpeg, ['-i', str(path), '-map', '0:a:0', '-c', 'copy', '-t', '0.03', '-f', 'null', '-'])
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', output)
    if not match:
        raise ValueError(f'{path.name}: audio duration could not be verified')
    hours, minutes, seconds = map(float, match.groups())
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def prepare_audio(manifest, output, ffmpeg):
    rows = list(source_rows(manifest))
    if not rows:
        raise ValueError('the clip manifest is empty')
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f'choose a new output directory: {output}')

    with tempfile.TemporaryDirectory(dir=output.parent, prefix='.record-shop-') as staging:
        directory = Path(staging) / 'clips'
        directory.mkdir()
        index = []
        for album_id, source, start, end in rows:
            if end > media_duration_ms(ffmpeg, source) + 20:
                raise ValueError(f'{album_id}: end offset is past the media duration')
            length = (end - start) / 1000
            fade = min(.06, length / 4)
            clip = directory / f'{album_id}.m4a'
            run_ffmpeg(ffmpeg, [
                '-loglevel', 'error', '-ss', f'{start / 1000:.3f}', '-i', str(source),
                '-t', f'{length:.3f}', '-map', '0:a:0', '-vn', '-sn', '-dn',
                '-map_metadata', '-1', '-map_chapters', '-1',
                '-af', f'loudnorm=I=-16:TP=-1.5:LRA=7,afade=t=in:d={fade:.3f},afade=t=out:st={length - fade:.3f}:d={fade:.3f}',
                '-ac', '2', '-ar', '44100', '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(clip),
            ])
            duration = media_duration_ms(ffmpeg, clip)
            if abs(duration - (end - start)) > 50:
                raise ValueError(f'{album_id}: clip duration verification failed')
            index.append(dict(album_id=album_id, file=clip.name, duration_ms=duration, sha256=hashlib.sha256(clip.read_bytes()).hexdigest()))
        (directory / 'index.json').write_text(json.dumps(index, indent=2) + '\n', encoding='utf-8')
        directory.rename(output)
    return index


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='JSON rows with album_id, file, start_ms and end_ms')
    parser.add_argument('output', type=Path)
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    args = parser.parse_args()
    if not args.ffmpeg:
        parser.error('ffmpeg is required')
    try:
        clips = prepare_audio(args.manifest.resolve(), args.output, args.ffmpeg)
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(1, f'{error}\n')
    except subprocess.CalledProcessError as error:
        parser.exit(1, f'FFmpeg failed: {error.stderr.decode(errors="replace")[-800:].strip()}\n')
    except subprocess.TimeoutExpired:
        parser.exit(1, 'FFmpeg did not finish processing the recording.\n')
    print(f'Prepared {len(clips)} clips in {args.output}')


if __name__ == '__main__':
    main()
