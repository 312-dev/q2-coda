#!/usr/bin/env python3
"""wavcheck: a recording of the Bluetooth relay's test signal (btsignal.py) measured.
See docs/bt-relay.md#checking-the-audio.

usage: wavcheck.py CAPTURE.wav [--min-sinad DB] [--min-bandwidth HZ] [--json]

Per channel: the tone's frequency and level, and SINAD (signal over noise and distortion: the tone's power
against everything else in the band). For the file: the bits its samples really use (a 24-bit file padded from
16 uses 16), the bandwidth (the frequency above which the noise section falls 12 dB under its level at
1 to 5 kHz: the band the codec kept), and dropouts (stretches of the tone gone quiet). Exit 1 with FAIL when
the tone is missing, in the wrong channel or off pitch, SINAD is under --min-sinad (40 dB unless given), a
dropout was found, or the bandwidth is under --min-bandwidth (not checked unless given).

Standard library only, so it runs wherever the capture lands. It reads integer PCM of 16, 24 and 32 bits and
32-bit float, plain or WAVE_FORMAT_EXTENSIBLE (what Windows' loopback capture writes)."""
import array
import json
import math
import struct
import sys

LEFT_HZ, RIGHT_HZ = 1000.0, 1500.0      # btsignal's tones, and its sections' times
TONE_AT, TONE_S, NOISE_AT = 0.5, 4.0, 5.0
FFT = 16384                             # the tone's window: 0.34 s at 48 kHz, 2.9 Hz a bin
WELCH = 2048                            # the noise's: 23 Hz a bin, averaged over about 2 s
PITCH = 0.005                           # how far a tone may sit from its frequency, as a fraction
KSDATAFORMAT_PCM = bytes.fromhex('0100000000001000800000aa00389b71')


def encode(channels, rate, bits, floating=False):
    """A WAV of these channels (lists of floats in -1..1). Integer PCM gets the plain 44-byte header; float gets
    the extensible one with the float subtype, as WASAPI's mix format is written."""
    count, frames = len(channels), len(channels[0])
    inter = [0.0] * (count * frames)
    for i, ch in enumerate(channels):
        inter[i::count] = ch
    if floating:
        body = array.array('f', inter)
    else:
        top = 1 << (bits - 1)
        ints = [max(-top, min(top - 1, round(v * top))) for v in inter]
        if bits == 16:
            body = array.array('h', ints)
        elif bits == 32:
            body = array.array('i', ints)
        else:
            body = bytearray().join((v & 0xffffff).to_bytes(3, 'little') for v in ints)
    if isinstance(body, array.array) and sys.byteorder == 'big':  # pragma: no cover
        body.byteswap()
    data = bytes(body)
    align = count * bits // 8
    fmt = struct.pack('<HHIIHH', 0xfffe if floating else 1, count, rate, rate * align, align, bits)
    if floating:
        fmt += struct.pack('<HHI', 22, bits, 3) + b'\3\0' + KSDATAFORMAT_PCM[2:]
    return (b'RIFF' + struct.pack('<I', 20 + len(fmt) + len(data)) + b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt
            + b'data' + struct.pack('<I', len(data)) + data)


def read(data):
    """{'rate', 'bits', 'float', 'channels': a list of floats per channel, 'ints': the same as integers or None}."""
    if data[:4] != b'RIFF' or data[8:12] != b'WAVE':
        raise ValueError('not a WAV file')
    at, fmt, body = 12, None, None
    while at + 8 <= len(data):
        name, size = data[at:at + 4], struct.unpack_from('<I', data, at + 4)[0]
        if name == b'fmt ':
            fmt = data[at + 8:at + 8 + size]
        elif name == b'data':
            body = data[at + 8:at + 8 + size]  # a capture cut short has a size past the end: take what is there
        at += 8 + size + (size & 1)
    if fmt is None or body is None or len(fmt) < 16:
        raise ValueError('not a WAV file: no fmt or data chunk')
    tag, count, rate, _byte_rate, _align, bits = struct.unpack_from('<HHIIHH', fmt)
    if tag == 0xfffe and len(fmt) >= 26:
        tag = struct.unpack_from('<H', fmt, 24)[0]
    floating = tag == 3
    if (tag, bits) not in ((1, 16), (1, 24), (1, 32), (3, 32)) or not count:
        raise ValueError(f'WAV format {tag} at {bits} bits is not read here')
    body = body[:len(body) - len(body) % (count * bits // 8)]
    ints = None
    if bits == 24:
        ints = [(b0 | b1 << 8 | b2 << 16) - ((b2 & 0x80) << 17) for b0, b1, b2 in zip(body[0::3], body[1::3], body[2::3])]
        flat = [v / 8388608 for v in ints]
    else:
        raw = array.array('f' if floating else 'h' if bits == 16 else 'i', body)
        if sys.byteorder == 'big':  # pragma: no cover
            raw.byteswap()
        if floating:
            flat = list(raw)
        else:
            ints, top = list(raw), float(1 << (bits - 1))
            flat = [v / top for v in ints]
    return {'rate': rate, 'bits': bits, 'float': floating, 'channels': [flat[i::count] for i in range(count)],
            'ints': [ints[i::count] for i in range(count)] if ints is not None else None}


def bits_used(w):
    """The sample width the file's values need: the container's bits less the low bits that are always zero.
    For float, the narrowest of 16, 24 and 32 whose integer grid holds every value."""
    if w['ints'] is not None:
        mixed = 0
        for ch in w['ints']:
            for v in ch:
                mixed |= v
        if not mixed:
            return 0
        return w['bits'] - ((mixed & -mixed).bit_length() - 1)
    if not any(v for ch in w['channels'] for v in ch):
        return 0
    for bits in (16, 24):
        scale = float(1 << (bits - 1))
        if all((v * scale).is_integer() for ch in w['channels'] for v in ch):
            return bits
    return 32


def fft_power(x):
    """|X[k]|^2 for k up to half of len(x), a power of two. An in-place radix-2 transform."""
    n = len(x)
    re, im = list(x), [0.0] * n
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            re[i], re[j] = re[j], re[i]
    size = 2
    while size <= n:
        half, step = size >> 1, 2 * math.pi / size
        cos, sin = [math.cos(step * k) for k in range(half)], [-math.sin(step * k) for k in range(half)]
        for start in range(0, n, size):
            for k in range(half):
                a, b = start + k, start + k + half
                tr = re[b] * cos[k] - im[b] * sin[k]
                ti = re[b] * sin[k] + im[b] * cos[k]
                re[b], im[b] = re[a] - tr, im[a] - ti
                re[a] += tr
                im[a] += ti
        size <<= 1
    return [re[k] * re[k] + im[k] * im[k] for k in range(n // 2)]


def _tone(ch, rate):
    """(Hz, dBFS, SINAD dB) of the strongest tone in FFT samples, through a 4-term Blackman-Harris window (its
    side lobes are 92 dB down, under a 16-bit signal's own noise)."""
    n = len(ch)
    window = [0.35875 - 0.48829 * math.cos(2 * math.pi * i / n) + 0.14128 * math.cos(4 * math.pi * i / n)
              - 0.01168 * math.cos(6 * math.pi * i / n) for i in range(n)]
    power = fft_power([v * w for v, w in zip(ch, window)])
    low = int(20 * n / rate) + 1
    peak = max(range(low, len(power)), key=power.__getitem__)
    lobe = range(max(low, peak - 8), min(len(power), peak + 9))
    tone = sum(power[k] for k in lobe)
    rest = sum(power[low:]) - tone
    if tone <= 0:  # pragma: no cover  (check() only measures a window it found sound in)
        return 0.0, -200.0, 0.0
    hz = sum(k * power[k] for k in lobe) / tone * rate / n
    amplitude = math.sqrt(4 * tone / (n * sum(w * w for w in window)))
    return hz, 20 * math.log10(amplitude), 10 * math.log10(tone / rest) if rest > 0 else 200.0


def _bandwidth(ch, rate):
    """The highest frequency at which the noise is within 12 dB of its level at 1 to 5 kHz; 0 with no noise."""
    window = [0.5 - 0.5 * math.cos(2 * math.pi * i / WELCH) for i in range(WELCH)]
    total, segments = [0.0] * (WELCH // 2), 0
    for start in range(0, len(ch) - WELCH + 1, WELCH):
        for k, p in enumerate(fft_power([v * w for v, w in zip(ch[start:start + WELCH], window)])):
            total[k] += p
        segments += 1
    if not segments:
        return 0.0
    hz = rate / WELCH
    middle = sorted(total[int(1000 / hz):int(5000 / hz)])
    reference = middle[len(middle) // 2] / segments
    if reference < 1e-9:   # under -100 dBFS a bin: the section is silence
        return 0.0
    def level(k):
        near = total[max(0, k - 4):k + 5]
        return sum(near) / len(near) / segments

    return next((k * hz for k in range(len(total) - 1, 0, -1) if level(k) >= reference / 16), 0.0)


def _rms(ch, start, size):
    block = ch[start:start + size]
    return math.sqrt(sum(v * v for v in block) / len(block)) if block else 0.0


def check(w, min_sinad=40.0, min_bandwidth=None):
    rate, chans = w['rate'], w['channels'][:2]
    r = {'rate': rate, 'bits': w['bits'], 'float': w['float'], 'seconds': len(chans[0]) / rate,
         'bits_used': bits_used(w), 'signal_at': None, 'channels': [], 'bandwidth_hz': 0.0, 'dropouts': [],
         'problems': []}
    if len(chans) < 2:
        r['problems'].append('one channel: the test signal is stereo')
        return r
    block = rate // 100
    start = next((s for s in range(0, len(chans[0]) - block, block)
                  if max(_rms(ch, s, block) for ch in chans) > 0.03), None)
    if start is None or start + rate + FFT > len(chans[0]):
        r['problems'].append('no test tone found: nothing was received, or not the test signal')
        return r
    r['signal_at'] = start / rate
    for name, ch in zip(('left', 'right'), chans):
        hz, dbfs, sinad = _tone(ch[start + rate:start + rate + FFT], rate)
        r['channels'].append({'name': name, 'tone_hz': hz, 'tone_dbfs': dbfs, 'sinad_db': sinad})
    found = [c['tone_hz'] for c in r['channels']]

    def near(hz, want):
        return abs(hz - want) <= want * PITCH

    if near(found[0], RIGHT_HZ) and near(found[1], LEFT_HZ):
        r['problems'].append('channels swapped: the right tone is in the left channel')
    else:
        r['problems'] += [f'{c["name"]} tone {c["tone_hz"]:.0f} Hz, expected {want:.0f}'
                          for c, want in zip(r['channels'], (LEFT_HZ, RIGHT_HZ)) if not near(c['tone_hz'], want)]
    r['problems'] += [f'{c["name"]} SINAD {c["sinad_db"]:.0f} dB, under {min_sinad:.0f}'
                      for c in r['channels'] if c['sinad_db'] < min_sinad]
    # dropouts: 2 ms blocks of the tone, both channels, more than 20 dB under the tone
    small = rate // 500
    floor = 10 ** (min(c['tone_dbfs'] for c in r['channels']) / 20) / math.sqrt(2) / 10
    run = None
    for s in range(start + rate // 50, min(len(chans[0]) - small, start + int((TONE_S - 0.03) * rate)), small):
        quiet = max(_rms(ch, s, small) for ch in chans) < floor
        if quiet and run is None:
            run = s
        elif not quiet and run is not None:
            r['dropouts'].append((run / rate, (s - run) / rate))
            run = None
    if run is not None:
        r['dropouts'].append((run / rate, (start + int((TONE_S - 0.03) * rate) - run) / rate))
    r['problems'] += [f'dropout of {length * 1000:.0f} ms at {at:.2f} s' for at, length in r['dropouts']]
    noise = start + int((NOISE_AT - TONE_AT + 0.3) * rate)
    r['bandwidth_hz'] = _bandwidth(chans[0][noise:noise + 2 * rate], rate)
    if min_bandwidth is not None and r['bandwidth_hz'] < min_bandwidth:
        r['problems'].append(f'bandwidth {r["bandwidth_hz"]:.0f} Hz, under {min_bandwidth:.0f}')
    return r


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {'--min-sinad': 40.0, '--min-bandwidth': None}
    as_json = '--json' in argv
    argv = [a for a in argv if a != '--json']
    while len(argv) >= 3 and argv[-2] in opts:
        opts[argv[-2]] = float(argv[-1])
        argv = argv[:-2]
    if len(argv) != 1 or argv[0].startswith('-'):
        raise ValueError(__doc__[__doc__.index('usage:'):])
    with open(argv[0], 'rb') as f:
        r = check(read(f.read()), opts['--min-sinad'], opts['--min-bandwidth'])
    if as_json:
        print(json.dumps(r, indent=1))
    else:
        kind = 'float' if r['float'] else 'integer'
        print(f'{r["seconds"]:.1f} s at {r["rate"]} Hz, {r["bits"]}-bit {kind}, {r["bits_used"]} bits used')
        for c in r['channels']:
            print(f'{c["name"]}: tone {c["tone_hz"]:.1f} Hz at {c["tone_dbfs"]:.1f} dBFS, SINAD {c["sinad_db"]:.1f} dB')
        if r['signal_at'] is not None:
            print(f'signal at {r["signal_at"]:.2f} s, bandwidth {r["bandwidth_hz"]:.0f} Hz, {len(r["dropouts"])} dropouts')
        for p in r['problems']:
            print(f'problem: {p}')
        print('FAIL' if r['problems'] else 'PASS')
    return 1 if r['problems'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as e:
        print(f'wavcheck: {e}', file=sys.stderr)
        sys.exit(2)
