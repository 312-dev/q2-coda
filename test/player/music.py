#!/usr/bin/env python3
"""The test songs for test/player, made with ffmpeg and lame: `python3 test/player/music.py DIR`.

a1.flac + a2.flac and b1.mp3 + b2.mp3 are one 40 s signal cut in two at 20 s, so a gapless change is
exact when the two decode to the signal with nothing added or lost at the cut. The signal is a 441 Hz
tone under a slow sweep: periodic enough to compress, and any missing or repeated sample shows.

The same cut makes a pair for each lossy encoder whose delay and padding a player must remove
(PAIRS): FFmpeg's AAC in M4A (an edit list), iTunes' shape of M4A (whole frames, no edit list, the
iTunSMPB tag), MP3 from FFmpeg's LAME, Ogg Vorbis, and Opus (from a 48 kHz copy, Opus's own rate).
FORMATS is one short file of each other format demo lists, at each width a format has.

REPLAYGAIN is a 6 s file for each form a ReplayGain tag takes (Vorbis comments in FLAC and Ogg, ID3v2.3 and 2.4
TXXX frames in MP3, an iTunes freeform atom in M4A) and for each rule of the gain (the peak's limit, no peak, an
album gain alone), and rglame.mp3, whose only gain is the one LAME puts in its header. silence.wav and full.wav are 6 s of digital silence and of a full-scale square, for the click, and with tone20.wav, a 1 kHz sine at -20 dBFS, for the level meter.
DSD (DSD): a DSD64 and a DSD128 DSF and a DSD64 DFF of the same 2205 Hz tone, made by a sigma-delta modulator here
(nothing on this machine encodes DSD). h1.flac + h2.flac are h96.flac, 16-bit 96 kHz, cut in two at 10 s, for Bluetooth's
resampler across a gapless change. qq.flac is a1.flac encrypted for test/player/qmstub.c's stand-in for QQ Music's
library, with the key QQ_KEY.
"""
import os, pathlib, struct, subprocess, sys, wave

# Bumped when a file is added or changed, so a directory made by an older recipe is made again.
RECIPE = '9'
POS_BASE, POS_STEP = 1000, 20  # pos.flac's second n: a sine of POS_BASE + POS_STEP n Hz

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

# (name, codec arguments, tags): the ReplayGain files, from 6 s of pink noise at 16 bits. rg23.mp3 and rg24.mp3 are
# MP3 with the gains in TXXX frames, Latin-1 under ID3v2.3 and UTF-8 under 2.4, as FFmpeg writes each.
RG = {'REPLAYGAIN_TRACK_GAIN': '-6.00 dB', 'REPLAYGAIN_TRACK_PEAK': '0.500000', 'REPLAYGAIN_ALBUM_GAIN': '-3.50 dB',
      'REPLAYGAIN_ALBUM_PEAK': '0.600000'}
REPLAYGAIN = [
    ('rg.flac', ['-c:a', 'flac'], RG),
    ('rg.ogg', ['-c:a', 'libvorbis'], RG),
    ('rg23.mp3', ['-c:a', 'libmp3lame', '-q:a', '2', '-id3v2_version', '3'], RG),
    ('rg24.mp3', ['-c:a', 'libmp3lame', '-q:a', '2', '-id3v2_version', '4'], RG),
    ('rg.m4a', ['-c:a', 'alac', '-sample_fmt', 's16p'], {}),  # its tags are freeform atoms, added by freeform()
    ('rgpeak.flac', ['-c:a', 'flac'], {'REPLAYGAIN_TRACK_GAIN': '+6.00 dB', 'REPLAYGAIN_TRACK_PEAK': '0.800000'}),
    ('rgnopeak.flac', ['-c:a', 'flac'], {'REPLAYGAIN_TRACK_GAIN': '+6.00 dB'}),
    ('rgalbum.flac', ['-c:a', 'flac'], {'REPLAYGAIN_ALBUM_GAIN': '-9.00 dB', 'REPLAYGAIN_ALBUM_PEAK': '0.500000'}),
]

# The lossy pairs, each two files cut from whole.wav (whole48.wav for Opus) at 20 s.
PAIRS = {'m4a': 'm4a', 'itunes': 'm4a', 'lamef': 'mp3', 'ogg': 'ogg', 'opus': 'opus'}


# (name, DSD rate, container): a 2205 Hz tone, 4 s, stereo (the right channel half a period behind). 2205 Hz is a
# whole number of bytes a period at DSD64 (160) and DSD128 (320), so one period is modulated and repeated.
DSD = [('d64.dsf', 2822400, 'dsf'), ('d128.dsf', 5644800, 'dsf'), ('d64.dff', 2822400, 'dff')]
DSD_SECONDS = 4


def dsd_period(rate, phase):
    """One period of a 2205 Hz sine at -6 dB through a second-order sigma-delta modulator, as bits, first in time
    first; the modulator runs ten periods first so that the one kept starts from its settled state."""
    import math
    n = rate // 2205
    i1 = i2 = 0.0
    bits = []
    for k in range(11 * n):
        x = 0.5 * math.sin(2 * math.pi * ((k % n) / n + phase))
        y = 1.0 if i2 >= 0 else -1.0
        i1 += x - y
        i2 += i1 - y
        if k >= 10 * n: bits.append(y > 0)
    return bits


def dsd(path, rate, kind):
    """A DSF (LSB first, 4096-byte blocks a channel) or a DSDIFF file (MSB first, a byte a channel at a time)."""
    import itertools
    frames = rate * DSD_SECONDS // 8  # bytes a channel
    chans = []
    for phase in (0.0, 0.5):
        bits = dsd_period(rate, phase)
        per = [sum(b << (i if kind == 'dsf' else 7 - i) for i, b in enumerate(bits[j:j + 8])) for j in range(0, len(bits), 8)]
        chans.append(bytes(itertools.islice(itertools.cycle(per), frames)))
    if kind == 'dsf':
        block = 4096
        data = b''.join(c[j:j + block].ljust(block, b'\0') for j in range(0, frames, block) for c in chans)
        fmt = struct.pack('<4sQIIIIIIQI4x', b'fmt ', 52, 1, 0, 2, 2, rate, 1, frames * 8, block)
        body = fmt + struct.pack('<4sQ', b'data', 12 + len(data)) + data
        head = struct.pack('<4sQQQ', b'DSD ', 28, 28 + len(body), 0)
        pathlib.Path(path).write_bytes(head + body)
        return
    data = bytes(b for pair in zip(*chans) for b in pair)
    ck = lambda name, body: name + struct.pack('>Q', len(body)) + body + (b'\0' if len(body) % 2 else b'')
    prop = b'SND ' + ck(b'FS  ', struct.pack('>I', rate)) + ck(b'CHNL', struct.pack('>H', 2) + b'SLFTSRGT') \
        + ck(b'CMPR', b'DSD ' + bytes([14]) + b'not compressed' + b'\0')
    body = b'DSD ' + ck(b'FVER', struct.pack('>I', 0x01050000)) + ck(b'PROP', prop) + ck(b'DSD ', data)
    pathlib.Path(path).write_bytes(ck(b'FRM8', body))


SACD_TRACKS = (2, 2)  # sacd.iso's two tracks, in seconds, of the DSD64 tone, which then ends


def sacd_dsd():
    """d64.dff's tone, 4 s of DSD64 a byte a channel at a time, MSB first, as SACD's plain DSD frames carry it."""
    import itertools
    rate, chans = 2822400, []
    for phase in (0.0, 0.5):
        bits = dsd_period(rate, phase)
        per = [sum(b << (7 - i) for i, b in enumerate(bits[j:j + 8])) for j in range(0, len(bits), 8)]
        chans.append(bytes(itertools.islice(itertools.cycle(per), rate * DSD_SECONDS // 8)))
    return bytes(b for pair in zip(*chans) for b in pair)


def dst_frames(dff):
    """The DST frames of a DSDIFF file's DST chunk, each DSTF chunk's data."""
    data, out, at = pathlib.Path(dff).read_bytes(), [], 0
    while (at := data.find(b'DSTF', at)) >= 0:
        n = struct.unpack('>Q', data[at + 4:at + 12])[0]
        out.append(data[at + 12:at + 12 + n]); at += 12 + n + (n & 1)
    return out


def sacd_iso(path, frames=None, tracks=SACD_TRACKS, dst=False):
    """A stereo SACD image in the Scarletbook layout sacd-ripper's libsacdread reads (scarletbook.h): the master TOC at
    sector 510 with its eight text sectors and the manufacturer's, one stereo DSD64 area TOC with its two track lists,
    and the area's frames of 1/75 s from sector 600 (by default sacd_dsd() cut in 9,408 bytes; tracks in seconds).
    Plain DSD is frame format 3, three frames in each 16 sectors; DST (dst) frame format 0, the frames one after another.
    An audio sector is a header byte (packets << 5 | frame starts << 2 | DST), two bytes a packet (frame start << 7 |
    data type 2 << 3 | length, at most 2045), and for each frame that starts in it its time code (minutes, seconds,
    frames) and, for DST, a byte with its packets' count << 2; then the packets' bytes."""
    S, rate = 2048, 2822400
    if frames is None:
        data = sacd_dsd()
        frames = [data[i:i + 9408] for i in range(0, len(data), 9408)]
    sectors, f, at, start_lsn = [], 0, 0, {}
    while f < len(frames):
        if not dst and f % 3 == 0 and at == 0: block = len(sectors)
        infos, times, body, room, counts = [], [], b'', S - 1, []
        while f < len(frames) and len(infos) < 7 and room > 6:
            first = at == 0
            if not dst and first and f % 3 == 0 and len(sectors) != block: break
            n = min(len(frames[f]) - at, room - 2 - (3 + dst if first else 0), 2045)
            if n <= 0: break
            if first: start_lsn[f] = len(sectors); times.append([f // 75 // 60, f // 75 % 60, f % 75] + ([0] if dst else []))
            infos.append([first, n]); body += frames[f][at:at + n]
            room -= 2 + (3 + dst if first else 0) + n; at += n
            if at == len(frames[f]): f, at = f + 1, 0
        sectors.append([infos, times, body])
        if not dst and (f % 3 == 0 and at == 0 or f == len(frames)):
            sectors += [[[], [], b'']] * (block + 16 - len(sectors))
    if dst:  # each frame's packet count, in the time code's fourth byte of the sector it starts in
        owner = []
        for k, (infos, times, body) in enumerate(sectors):
            for first, n in infos:
                if first: owner.append([k, len([t for t in owner if t[0] == k]), 0])
                owner[-1][2] += 1
        for k, i, count in owner: sectors[k][1][i][3] = count << 2
    audio = b''.join((bytes([len(i) << 5 | len(t) << 2 | dst]) + b''.join(struct.pack('>H', a << 15 | 2 << 11 | n) for a, n in i)
                      + b''.join(bytes(x) for x in t) + body).ljust(S, b'\0') for i, t, body in sectors)
    first_lsn, out, sec = 600, [], 0
    for length in tracks:
        a, b = sec * 75, min(sec * 75 + length * 75, len(frames))
        lsn = start_lsn[a]; end = start_lsn[b] if b < len(frames) else len(sectors)
        out.append((first_lsn + lsn, end - lsn, a, b - a)); sec += length
    mtoc = bytearray(S)
    mtoc[0:10] = b'SACDMTOC\x01\x14'
    struct.pack_into('>HH', mtoc, 16, 1, 1)
    struct.pack_into('>IIII', mtoc, 64, 540, 550, 0, 0)
    struct.pack_into('>HH', mtoc, 84, 3, 0)
    mtoc[128], mtoc[136:140] = 1, b'en\x02\x00'
    atoc = bytearray(S)
    atoc[0:10] = b'TWOCHTOC\x01\x14'
    struct.pack_into('>H', atoc, 10, 3)
    struct.pack_into('>I', atoc, 16, 2 * rate // 8)
    atoc[20], atoc[21], atoc[32], atoc[34] = 4, 0 if dst else 3, 2, 2
    total = len(frames)
    atoc[64:67] = bytes((total // 75 // 60, total // 75 % 60, total % 75))
    atoc[69] = len(out)
    struct.pack_into('>II', atoc, 72, first_lsn, first_lsn + len(sectors) - 1)
    atoc[80], atoc[88:92] = 1, b'en\x02\x00'
    trl1, trl2 = bytearray(S), bytearray(S)
    trl1[0:8], trl2[0:8] = b'SACDTRL1', b'SACDTRL2'
    tc = lambda f: bytes((f // 75 // 60, f // 75 % 60, f % 75, 0))
    for i, (lsn, n, st, du) in enumerate(out):
        struct.pack_into('>II', trl1, 8 + 4 * i, lsn, 0); struct.pack_into('>I', trl1, 8 + 4 * 255 + 4 * i, n)
        trl2[8 + 4 * i:12 + 4 * i], trl2[8 + 4 * 255 + 4 * i:12 + 4 * 255 + 4 * i] = tc(st), tc(du)
    area = bytes(atoc) + bytes(trl1) + bytes(trl2)
    img = bytearray(S * 510) + bytes(mtoc) + b'SACDText'.ljust(S, b'\0') * 8 + b'SACD_Man'.ljust(S, b'\0')
    img += bytes(S * (540 - len(img) // S)) + area + bytes(S * 7) + area
    img += bytes(S * (first_lsn - len(img) // S)) + audio
    pathlib.Path(path).write_bytes(bytes(img))


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


def freeform(path, tags):
    """tags as iTunes and foobar2000 write them into an M4A: a ---- atom each (mean com.apple.iTunes, name the
    tag's), under moov/udta/meta/ilst, which FFmpeg wrote for the title."""
    b = bytearray(pathlib.Path(path).read_bytes())
    moov = b.index(b'moov') - 4
    atoms = b''.join(_box(b'----', _box(b'mean', bytes(4) + b'com.apple.iTunes') + _box(b'name', bytes(4) + k.lower().encode())
                          + _box(b'data', struct.pack('>II', 1, 0) + v.encode())) for k, v in tags.items())
    boxes, start, end = [], moov, moov + struct.unpack_from('>I', b, moov)[0]
    for name, head in (b'udta', 8), (b'meta', 8), (b'ilst', 12):
        boxes.append(start)
        p = start + head
        while b[p + 4:p + 8] != name:
            p += struct.unpack_from('>I', b, p)[0]
            assert p < end, f'{path}: no {name.decode()}'
        start, end = p, p + struct.unpack_from('>I', b, p)[0]
    b[end:end] = atoms
    for q in boxes + [start]: struct.pack_into('>I', b, q, struct.unpack_from('>I', b, q)[0] + len(atoms))
    pathlib.Path(path).write_bytes(b)


# QQ Music (qq.flac): a1.flac encrypted as test/player/qmstub.c's Decrypt undoes it for this key
QQ_KEY = 'k3y'


def qq_mask(key0, offset):
    """qmstub.c's QM_MASK: the byte a file's byte at offset is XORed with."""
    return (key0 ^ (offset * 7 + (offset >> 8))) & 0xff


# FFmpeg's own DST test file (its FATE suite), for a DST image: nothing here encodes DST. Fetched once, by its hash.
DST_SAMPLE = ('https://fate-suite.ffmpeg.org/dst/dst-64fs44-2ch.dff',
              '29d305bd19731ce079324ed14ac17254f254115d7787b5646a943e367b238ed0')


def sacd_dst(d):
    """sacd-dst.iso: DST_SAMPLE's ten frames thirty times over, two tracks of 2 s; None when it cannot be fetched."""
    import hashlib, urllib.request
    url, digest = DST_SAMPLE
    cached = d/'dst-64fs44-2ch.dff'
    if not cached.exists():
        try:
            data = urllib.request.urlopen(url, timeout=60).read()
        except OSError:
            return None
        if hashlib.sha256(data).hexdigest() != digest: return None
        cached.write_bytes(data)
    if not (d/'sacd-dst.iso').exists(): sacd_iso(d/'sacd-dst.iso', dst_frames(cached) * 30, dst=True)
    return d/'sacd-dst.iso'


def qq(d):
    if not (d/'sacd.iso').exists(): sacd_iso(d/'sacd.iso')
    sacd_dst(d)
    if (d/'qq.flac').exists() or not (d/'a1.flac').exists(): return
    k = ord(QQ_KEY[0])
    (d/'qq.flac').write_bytes(bytes(b ^ qq_mask(k, i) for i, b in enumerate((d/'a1.flac').read_bytes())))


def make(d):
    d = pathlib.Path(d); d.mkdir(parents=True, exist_ok=True)
    if not (d/'slow.flac').exists(): os.mkfifo(d/'slow.flac')  # a preload's open that blocks until a test writes it
    if not (d/'pos.flac').exists():  # the scan's: second n a sine of POS_HZ(n), so what is heard says where it is from
        ff('-f', 'lavfi', '-i', f'aevalsrc=0.5*sin(2*PI*({POS_BASE}+{POS_STEP}*floor(t))*t):s=44100:d=60',
           '-af', 'aformat=channel_layouts=stereo', '-c:a', 'flac', '-sample_fmt', 's16', d/'pos.flac')
    if (d/'.made').exists() and (d/'.made').read_text() == RECIPE: return qq(d) or d
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
    # ReplayGain's tag forms; the moov is after mdat in FFmpeg's M4A, so the atoms move no chunk offset
    pink = d/'pink.wav'
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=6:c=pink:r=44100:a=0.25:seed=5', '-ac', '2', '-c:a', 'pcm_s16le', pink)
    for name, args, rg in REPLAYGAIN:
        ff('-i', pink, *args, *tags(name, 'ReplayGain', 1), *[x for k, v in rg.items() for x in ('-metadata', f'{k}={v}')], d/name)
    freeform(d/'rg.m4a', RG)
    # a gain in its LAME header alone: white noise at full scale, so the gain LAME works out is a cut, -8.9 dB (a boost
    # would read 1 anyway, as LAME's peak is 0)
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=6:c=white:r=44100:a=1.0:seed=5', '-ac', '2', '-c:a', 'pcm_s16le', d/'loud.wav')
    subprocess.run(['lame', '--quiet', '-V2', d/'loud.wav', d/'rglame.mp3'], check=True)
    ff('-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo:d=6', '-c:a', 'pcm_s16le', d/'silence.wav')
    ff('-f', 'lavfi', '-i', 'sine=f=1000:r=44100:d=6', '-af', 'volume=0.8,aformat=channel_layouts=stereo', '-c:a', 'pcm_s16le', d/'tone20.wav')
    with wave.open(str(d/'full.wav'), 'wb') as f:  # a 100 Hz square at the very edges of 16 bits, both channels
        f.setnchannels(2), f.setsampwidth(2), f.setframerate(44100)
        f.writeframes(b''.join(struct.pack('<hh', v, v) for v in (32767 if i % 441 < 220 else -32768 for i in range(6 * 44100))))
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
    # DSD, and a 96 kHz tone cut in two for Bluetooth's resampler
    for name, rate, kind in DSD:
        dsd(d/name, rate, kind)
    ff('-f', 'lavfi', '-i', 'sine=f=997:r=96000:d=20', '-af', 'volume=0.5,aformat=channel_layouts=stereo', '-c:a', 'flac', '-sample_fmt', 's16', d/'h96.flac')
    ff('-i', d/'h96.flac', '-t', '10', '-c:a', 'flac', d/'h1.flac')
    ff('-ss', '10', '-i', d/'h96.flac', '-c:a', 'flac', d/'h2.flac')
    qq(d)
    (d/'.made').write_text(RECIPE)
    return d


if __name__ == '__main__':
    print(make(sys.argv[1]))
