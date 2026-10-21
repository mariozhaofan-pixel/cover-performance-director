"""Budget all uploaded audio, fit a selected song excerpt, and map candidate cues.

Only stdlib + ffmpeg are required. A companion stem is backup, not an upload.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import wave

RATE = 44100


def finite(value, name, positive=False):
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be finite and {'positive' if positive else 'nonnegative'}")
    return value


def ffmpeg():
    found = shutil.which('ffmpeg')
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError('ffmpeg required; use the cover-audio runtime or install ffmpeg') from exc


def duration(path):
    path = Path(path).expanduser().resolve(strict=True)
    probe = shutil.which('ffprobe')
    if probe:
        result = subprocess.check_output([probe, '-v', 'error', '-select_streams', 'a:0',
            '-show_entries', 'format=duration:stream=duration', '-of', 'json', str(path)], text=True, encoding='utf-8')
        data = json.loads(result)
        if not data.get('streams'):
            raise ValueError(f'no audio stream: {path}')
        values = []
        for record in [data.get('format', {}), *data['streams']]:
            try:
                v = float(record['duration'])
                if math.isfinite(v) and v > 0:
                    values.append(v)
            except (KeyError, ValueError, TypeError):
                pass
        if values:
            return max(values)  # Keep MP3/container padding in the upload budget.
    with tempfile.TemporaryDirectory(prefix='cover-probe-') as temp:
        decoded = Path(temp) / 'decoded.wav'
        subprocess.run([ffmpeg(), '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:a:0',
            '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(decoded)], check=True)
        with wave.open(str(decoded), 'rb') as w:
            return finite(w.getnframes() / w.getframerate(), 'decoded duration', True)


def allocate_budget(total=30., reserve=4., voice_durations=(), other_durations=(), max_song=30.):
    finite(total, 'reference audio budget', True)
    finite(reserve, 'voice reserve')
    finite(max_song, 'max song duration', True)
    voice = sum(finite(v, 'voice duration', True) for v in voice_durations)
    other = sum(finite(v, 'other audio duration', True) for v in other_durations)
    held = max(reserve, voice)
    available = min(max_song, total - held - other)
    if available <= 0:
        raise ValueError('voice and other uploaded audio exhaust the reference audio budget')
    # Floor to a sample, never round an upload above the budget.
    available = math.floor(available * RATE + 1e-7) / RATE
    if available <= 0:
        raise ValueError('less than one sample remains in the reference audio budget')
    return {'total_seconds': total, 'voice_reserve_seconds': reserve,
        'voice_actual_seconds': voice, 'voice_budgeted_seconds': held,
        'other_audio_seconds': other, 'song_limit_seconds': available,
        'video_duration_is_independent': True}


def probe_budget(total=30., reserve=4., voices=(), others=(), max_song=30.):
    records = []
    values = {}
    for role, paths in [('voice', voices), ('other', others)]:
        values[role] = []
        for path in paths:
            p = Path(path).expanduser().resolve(strict=True)
            seconds = duration(p)
            values[role].append(seconds)
            records.append({'role': role, 'path': str(p), 'duration': seconds})
    result = allocate_budget(total, reserve, values['voice'], values['other'], max_song)
    result['attachments'] = records
    return result


def fit_rate(selected_duration, capacity, max_speed=1.12):
    finite(selected_duration, 'selected duration', True)
    finite(capacity, 'song capacity', True)
    finite(max_speed, 'maximum speed', True)
    if max_speed < 1:
        raise ValueError('maximum speed must be at least 1')
    speed = max(1., selected_duration / capacity)
    if speed > max_speed + 1e-9:
        raise ValueError('required speed exceeds maximum; select a shorter complete phrase or split the song')
    return speed


def map_lyrics(data, start, end, speed):
    output = []
    for p in data['phrases']:
        if p['start'] >= end or p['end'] <= start:
            continue
        complete = p['start'] >= start - 1e-6 and p['end'] <= end + 1e-6
        row = {'id': p['id'], 'text': p['text'] if complete else None,
            'full_phrase_context': p['text'], 'source_start': max(start, p['start']),
            'source_end': min(end, p['end']), 'start': (max(start, p['start'])-start)/speed,
            'end': (min(end, p['end'])-start)/speed, 'evidence': 'transformed ASR candidate',
            'partial_phrase': not complete, 'flags': p.get('flags', []), 'words': []}
        for w in p.get('words', []):
            if w['start'] < end and w['end'] > start:
                row['words'].append({**w, 'source_start': w['start'], 'source_end': w['end'],
                    'start': (max(start,w['start'])-start)/speed,
                    'end': (min(end,w['end'])-start)/speed,
                    'partial_word': w['start'] < start or w['end'] > end})
        output.append(row)
    return {'status':'transformed ASR candidates; not listening-verified', 'time_formula':'local=(source-start)/speed',
        'source_start':start, 'source_end':end, 'tempo_factor':speed, 'phrases':output}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024), b''):
            h.update(b)
    return h.hexdigest()


def render(source, target, start, end, speed, target_frames):
    # atempo changes tempo while preserving pitch; never use asetrate.
    with tempfile.TemporaryDirectory(prefix='cover-fit-') as temp:
        raw = Path(temp)/'tempo.wav'
        filters = f'atrim=start={start:.12f}:end={end:.12f},asetpts=PTS-STARTPTS,atempo={speed:.12f}'
        subprocess.run([ffmpeg(), '-nostdin', '-v', 'error', '-i', str(source), '-map', '0:a:0',
            '-vn', '-af', filters, '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_s24le', str(raw)], check=True)
        with wave.open(str(raw),'rb') as w:
            count = w.getnframes()
            data = w.readframes(count)
        delta = target_frames-count
        if abs(delta)/RATE > .12:
            raise ValueError('tempo output duration differs by over 120ms; review instead of truncating content')
        # Only correct the bounded atempo sample discrepancy, never pad to video duration.
        data = data[:target_frames*6] if delta < 0 else data + bytes(delta*6)
        # A 10ms ending fade prevents clicks at the selected phrase boundary.
        fade = min(round(.01*RATE), target_frames)
        content = bytearray(data)
        for i in range(fade):
            gain = (fade-1-i)/max(1,fade-1)
            for channel in range(2):
                pos = ((target_frames-fade+i)*2+channel)*3
                sample = int.from_bytes(content[pos:pos+3], 'little', signed=True)
                content[pos:pos+3] = round(sample*gain).to_bytes(3,'little',signed=True)
        with wave.open(str(target),'wb') as w:
            w.setnchannels(2); w.setsampwidth(3); w.setframerate(RATE); w.writeframes(content)
    return {'file':target.name, 'frames':target_frames, 'sample_rate':RATE,
        'duration':target_frames/RATE, 'atempo_frames_before_correction':count,
        'sample_correction':delta, 'end_fade_seconds':fade/RATE, 'sha256':sha(target)}


def run(args):
    source = args.audio.expanduser().resolve(strict=True)
    start = finite(args.start, 'start')
    end = duration(source) if args.end is None else finite(args.end,'end',True)
    if end <= start or end > duration(source)+1/RATE:
        raise ValueError('selected range is outside the source audio')
    budget = probe_budget(args.reference_audio_budget,args.voice_reserve_seconds,args.voice_reference,
        args.other_reference_audio,args.max_seconds)
    speed = fit_rate(end-start,budget['song_limit_seconds'],args.max_speed)
    frames = math.floor((end-start)/speed*RATE+1e-7)
    inputs = [source, *args.voice_reference, *args.other_reference_audio]
    if args.companion:
        inputs.append(args.companion)
        if duration(args.companion)+1/RATE < end:
            raise ValueError('companion stem does not cover selected range')
    hashes = {str(Path(p).resolve()):sha(p) for p in inputs}
    root = args.output.expanduser().resolve()
    root.mkdir(parents=True,exist_ok=False)
    outputs = [render(source,root/'song-reference.wav',start,end,speed,frames)]
    if args.companion:
        outputs.append(render(args.companion,root/'instrumental-backup.wav',start,end,speed,frames))
    if args.lyrics:
        mapped = map_lyrics(json.loads(args.lyrics.read_text(encoding='utf-8-sig')),start,end,speed)
        (root/'mapped-lyrics.json').write_text(json.dumps(mapped,ensure_ascii=False,indent=2),encoding='utf-8')
    actual_total = duration(root/'song-reference.wav')+budget['voice_actual_seconds']+budget['other_audio_seconds']
    reserved_total = outputs[0]['duration']+budget['voice_budgeted_seconds']+budget['other_audio_seconds']
    if max(actual_total,reserved_total) > budget['total_seconds']+1e-6:
        raise ValueError('rendered reference audio exceeds aggregate upload budget')
    for path, checksum in hashes.items():
        if sha(path) != checksum:
            raise RuntimeError('input changed during fitting')
    result = {'budget':budget,'source_range':[start,end],'tempo_factor':speed,
        'pitch_preserving_filter':'ffmpeg atempo', 'actual_uploaded_total_seconds':actual_total,
        'budgeted_uploaded_total_seconds':reserved_total,'outputs':outputs,'input_sha256':hashes,
        'upload_set':['song-reference.wav',*[str(p) for p in args.voice_reference],*[str(p) for p in args.other_reference_audio]],
        'backup_not_in_upload_set':bool(args.companion),'final_character_voice_generated':False,
        'timing_evidence':'mapped candidate; verify final singing waveform before exact lip sync'}
    (root/'fit-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('audio',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--start',type=float,default=0.)
    parser.add_argument('--end',type=float)
    parser.add_argument('--reference-audio-budget',type=float,default=30.)
    parser.add_argument('--voice-reserve-seconds',type=float,default=4.)
    parser.add_argument('--voice-reference',type=Path,action='append',default=[])
    parser.add_argument('--other-reference-audio',type=Path,action='append',default=[])
    parser.add_argument('--max-seconds',type=float,default=30.)
    parser.add_argument('--max-speed',type=float,default=1.12)
    parser.add_argument('--companion',type=Path)
    parser.add_argument('--lyrics',type=Path)
    args=parser.parse_args()
    try:
        print(json.dumps(run(args),ensure_ascii=False,indent=2))
    except (ValueError,OSError,subprocess.CalledProcessError) as exc:
        parser.exit(1,str(exc)+'\n')


if __name__=='__main__':
    main()
