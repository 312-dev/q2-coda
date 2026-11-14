#!/usr/bin/env python3
"""The test songs for test/player, made with ffmpeg and lame: `python3 test/player/music.py DIR`.

a1.flac + a2.flac and b1.mp3 + b2.mp3 are one 40 s signal cut in two at 20 s, so a gapless change is
exact when the two decode to the signal with nothing added or lost at the cut. The signal is a 441 Hz
tone under a slow sweep: periodic enough to compress, and any missing or repeated sample shows.

The same cut makes a pair for each lossy encoder whose delay and padding a player must remove
(PAIRS): FFmpeg's AAC in M4A (an edit list), iTunes' shape of M4A (whole frames, no edit list, the
iTunSMPB tag), MP3 from FFmpeg's LAME, Ogg Vorbis, and Opus (from a 48 kHz copy, Opus's own rate).
FORMATS is one short file of each other format demo lists, at each width a format has.
"""
import os, pathlib, struct, subprocess, sys

# Bumped when a file is added or changed, so a directory made by an older recipe is made again.
RECIPE = '2'

# (name, ffmpeg output arguments): 6 s of the noise source, in each format and width. APE, TAK and WMA Lossless
# have no encoder in ffmpeg or on this machine, so none is made.
FORMATS = [
    ('w16.wav', ['-c:a', 'pcm_s16le']), ('w24.wav', ['-c:a', 'pcm_s24le']), ('w32.wav', ['-c:a', 'pcm_s32le']),
    ('wf.wav', ['-c:a', 'pcm_f32le']), ('i16.aiff', ['-c:a', 'pcm_s16be']), ('i24.aiff', ['-c:a', 'pcm_s24be']),
    ('i32.aiff', ['-c:a', 'pcm_s32be']), ('l16.m4a', ['-c:a', 'alac', '-sample_fmt', 's16p']),
    ('l24.m4a', ['-c:a', 'alac', '-sample_fmt', 's32p', '-bits_per_raw_sample', '24']),
    ('k16.wv', ['-c:a', 'wavpack', '-sample_fmt', 's16p']), ('k24.wv', ['-c:a', 'wavpack', '-sample_fmt', 's32p', '-bits_per_raw_sample', '24']),
    ('k32.wv', ['-c:a', 'wavpack', '-sample_fmt', 's32p']), ('f24.flac', ['-c:a', 'flac', '-sample_fmt', 's32', '-bits_per_raw_sample', '24']),
    ('m.mp2', ['-c:a', 'mp2', '-b:a', '192k']), ('x.aac', ['-c:a', 'aac', '-b:a', '128k', '-f', 'adts']),
    ('o.opus', ['-c:a', 'libopus', '-b:a', '96k']), ('x2.wma', ['-c:a', 'wmav2', '-b:a', '128k']),
    ('x1.wma', ['-c:a', 'wmav1', '-b:a', '128k']), ('z.ac3', ['-c:a', 'ac3', '-b:a', '192k']),
    ('z.dts', ['-strict', '-2', '-c:a', 'dca', '-b:a', '768k']),
]

# The lossy pairs, each two files cut from whole.wav (whole48.wav for Opus) at 20 s.
PAIRS = {'m4a': 'm4a', 'itunes': 'm4a', 'lamef': 'mp3', 'ogg': 'ogg', 'opus': 'opus'}


def ff(*a):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-bitexact', *map(str, a)], check=True)


def _box(name, body): return struct.pack('>I', 8 + len(body)) + name + body


def itunes(path, frames):
    """FFmpeg's M4A made into iTunes' shape: every stts frame a whole 1024 and an iTunSMPB tag of 1024 priming and
    the rest padding, as iTunes and qaac write (test/player_seam.py's add_tag and whole_frames)."""
    b = bytearray(pathlib.Path(path).read_bytes())
    moov = b.index(b'moov') - 4
    at = b.index(b'stts', moov) + 8
    n, = struct.unpack_from('>I', b, at)
    total = sum(struct.unpack_from('>I', b, at + 4 + 8 * i)[0] for i in range(n)) * 1024
    struct.pack_into('>II', b, at + 4 + 8 * (n - 1), struct.unpack_from('>I', b, at + 4 + 8 * (n - 1))[0], 1024)
    smpb = ' 00000000 %08X %08X %016X 00000000 00000000' % (1024, total - 1024 - frames, frames)
    tag = _box(b'----', _box(b'mean', bytes(4) + b'com.apple.iTunes') + _box(b'name', bytes(4) + b'iTunSMPB')
               + _box(b'data', struct.pack('>II', 1, 0) + smpb.encode()))
    # moov/udta/meta/ilst, which FFmpeg writes for the title tag; moov is after mdat, so no chunk offset moves
    boxes, start, end = [], moov, moov + struct.unpack_from('>I', b, moov)[0]
    for name, head in (b'udta', 8), (b'meta', 8), (b'ilst', 12):
        boxes.append(start)
        p = start + head
        while b[p + 4:p + 8] != name:
            p += struct.unpack_from('>I', b, p)[0]
            assert p < end, f'{path}: no {name.decode()}'
        start, end = p, p + struct.unpack_from('>I', b, p)[0]
    b[end:end] = tag
    for q in boxes + [start]: struct.pack_into('>I', b, q, struct.unpack_from('>I', b, q)[0] + len(tag))
    pathlib.Path(path).write_bytes(b)


def make(d):
    d = pathlib.Path(d); d.mkdir(parents=True, exist_ok=True)
    if not (d/'slow.flac').exists(): os.mkfifo(d/'slow.flac')  # a preload's open that blocks until a test writes it
    if (d/'.made').exists() and (d/'.made').read_text() == RECIPE: return d
    w = d/'whole.wav'
    ff('-f', 'lavfi', '-i', 'sine=f=441:r=44100:d=40', '-f', 'lavfi', '-i', 'aevalsrc=0.2*sin(2*PI*(200+20*t)*t):s=44100:d=40',
       '-filter_complex', 'amix=inputs=2:normalize=0,aformat=channel_layouts=stereo', '-c:a', 'pcm_s16le', w)
    tags = lambda title, album, n: ['-metadata', f'title={title}', '-metadata', 'artist=Tester', '-metadata', f'album={album}', '-metadata', f'track={n}']
    ff('-i', w, '-t', '20', '-c:a', 'flac', *tags('A One', 'Gapless FLAC', 1), d/'a1.flac')
    ff('-ss', '20', '-i', w, '-c:a', 'flac', *tags('A Two', 'Gapless FLAC', 2), d/'a2.flac')
    ff('-i', w, '-t', '20', '-c:a', 'pcm_s16le', d/'p1.wav')
    ff('-ss', '20', '-i', w, '-c:a', 'pcm_s16le', d/'p2.wav')
    for n in '12':
        subprocess.run(['lame', '--quiet', '-V2', '--tt', f'B {n}', '--ta', 'Tester', '--tl', 'Gapless MP3', '--tn', n, d/f'p{n}.wav', d/f'b{n}.mp3'], check=True)
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=15:c=pink:r=96000:a=0.3:seed=7', '-ac', '2', '-c:a', 'flac', '-sample_fmt', 's32',
       '-bits_per_raw_sample', '24', *tags('Hi Res', 'HiRes', 1), d/'c96.flac')
    ff('-i', d/'p1.wav', '-c:a', 'aac', '-b:a', '128k', *tags('M4A', 'AAC', 1), d/'d.m4a')
    ff('-i', d/'p1.wav', '-c:a', 'libvorbis', *tags('Ogg', 'Vorbis', 1), d/'e.ogg')
    (d/'junk.flac').write_bytes(bytes((i * 7919 + (i >> 3)) & 255 for i in range(200000)))  # not audio
    # an MP3 with 3 MB of the same kind of bytes after its last frame: they must not play
    (d/'tail.mp3').write_bytes((d/'b1.mp3').read_bytes() + bytes((i * 7919 + (i >> 3)) & 255 for i in range(3 << 20)))
    # a FLAC's metadata and no frames: it opens, has a rate, and ends at once
    flac = (d/'a1.flac').read_bytes()
    at = 4
    while True:
        last, size = flac[at] & 0x80, int.from_bytes(flac[at + 1:at + 4], 'big')
        at += 4 + size
        if last: break
    (d/'empty.flac').write_bytes(flac[:at])
    (d/'a}1.flac').write_bytes(flac)  # a } in the name, which demo sends as %0x7d
    # every other format, from 6 s of noise at 44.1 kHz in 32-bit float, so each width's low bits are busy
    src = d/'noise.wav'
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=6:c=pink:r=44100:a=0.4:seed=3', '-ac', '2', '-c:a', 'pcm_f32le', src)
    for name, args in FORMATS:
        ff('-i', src, *args, *tags(name, 'Formats', 1), d/name)
    # the lossy pairs
    ff('-i', w, '-ar', '48000', '-c:a', 'pcm_s16le', d/'whole48.wav')
    for name, ext in PAIRS.items():
        for n, cut in ('1', ['-t', '20']), ('2', ['-ss', '20']):
            out = d/f'{name}{n}.{ext}'
            src = d/('whole48.wav' if name == 'opus' else 'whole.wav')
            enc = {'m4a': ['-c:a', 'aac', '-b:a', '128k'], 'itunes': ['-c:a', 'aac', '-b:a', '128k', '-use_editlist', '0'],
                   'lamef': ['-c:a', 'libmp3lame', '-q:a', '2'], 'ogg': ['-c:a', 'libvorbis'], 'opus': ['-c:a', 'libopus', '-b:a', '128k']}[name]
            ff(*cut if n == '2' else [], '-i', src, *cut if n == '1' else [], *enc, *tags(f'{name} {n}', name, n), out)
            if name == 'itunes': itunes(out, 20 * 44100)
    (d/'.made').write_text(RECIPE)
    return d


if __name__ == '__main__':
    print(make(sys.argv[1]))
