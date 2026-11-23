#!/usr/bin/env python3
"""Song changes in a recording of a Q2's Bluetooth output (docs/bt-rig.md#analysis). Standard library and ffmpeg.

What a Bluetooth headset hears is the song after the Q2's mix, its resampling to the link's 48 kHz and a lossy
codec (SBC or AAC) and back, so nothing here compares samples for equality. Two levels of answer:

- Without the songs (`analyse F.wav`): the RMS envelope in 50 ms windows, every silence of 100 ms or more (the
  gaps), clipping, and around each change time given (`--at`, seconds into the recording) the dip: how far and
  for how long the level fell. A crossfade and a gapless change can look alike here.
- With the songs (`--ref A --ref B ...`, the files in play order): each song is found in the recording by
  cross-correlating onset envelopes, then followed sample by sample at 8 kHz, and every 50 ms window of the
  recording is fitted as a*A + b*B by least squares. a and b are each song's gain in that window, so each
  song's own level is known even where they overlap. A song plays in a window while its gain is within
  PRESENT_BELOW dB of its steady gain (its gain while it plays alone); the overlap is the time both play, the gap
  the time neither does while the recording is silent, and the fade-out and fade-in are read off a and b.
"""
import array
import cmath
import json
import math
import pathlib
import random
import shutil
import struct
import subprocess
import sys

WINDOW_S = 0.05
SILENT_DB = -60.0        # a window this quiet or quieter is silence
GAP_MIN_S = 0.1          # silences shorter than this are not reported as gaps
PRESENT_BELOW = 30.0     # a song plays in a window while its fitted gain is within this many dB of its steady gain
TRUST_DB = 25.0          # a song's gain is read only where its own level is within this many dB of the recording's
CLIP = 0.999             # |sample| at or above this is clipped (float capture: over 1.0 is counted too)
FIT_RATE = 8000          # the rate the least-squares fit runs at
ONSET_HOP = 80           # samples at FIT_RATE per onset-envelope step (10 ms)
LAG_SEARCH = 8           # samples at FIT_RATE a song's alignment may move per window (1 ms)
MIN_CORR = 0.5           # below this normalised correlation a window does not move a song's alignment
LAG_DRIFT_MAX = 40       # samples at FIT_RATE a song's alignment may drift in a fit (5 ms: 100 ppm over 50 s)


def db(x):
    return 10 * math.log10(x) if x > 1e-20 else -200.0


# ---- reading

def read_wav(path):
    """(rate, channels, samples as array('f') interleaved, in -1..1) from a PCM16, PCM32 or float32 WAV."""
    with open(path, "rb") as f:
        b = f.read()
    if b[:4] != b"RIFF" or b[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a WAV file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(b):
        cid, size = b[pos:pos + 4], struct.unpack_from("<I", b, pos + 4)[0]
        body = b[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", body)
            if fmt[0] == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: the subformat's first word is the tag
                fmt = (struct.unpack_from("<H", body, 24)[0],) + fmt[1:]
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    if not fmt or data is None:
        raise ValueError(f"{path}: no fmt or data chunk")
    tag, ch, rate, _, _, bits = fmt
    if tag == 3 and bits == 32:
        s = array.array("f")
        s.frombytes(data[:len(data) // 4 * 4])
    elif tag == 1 and bits == 16:
        i = array.array("h")
        i.frombytes(data[:len(data) // 2 * 2])
        s = array.array("f", (v / 32768.0 for v in i))
    elif tag == 1 and bits == 32:
        i = array.array("i")
        i.frombytes(data[:len(data) // 4 * 4])
        s = array.array("f", (v / 2147483648.0 for v in i))
    else:
        raise ValueError(f"{path}: format {tag} at {bits} bits is not read here")
    if sys.byteorder != "little":
        s.byteswap()
    return rate, ch, s


def decode_mono(path, rate=FIT_RATE):
    """A song or recording as mono float at rate (the channels' mean), by ffmpeg when there is one, so the songs and
    the recording go through the same resampler (two different ones leave their difference in every fitted window);
    without ffmpeg a WAV is read here (resample)."""
    if not shutil.which("ffmpeg") and str(path).lower().endswith(".wav"):
        r, ch, s = read_wav(path)
        mono = array.array("f", (math.fsum(s[i:i + ch]) / ch for i in range(0, len(s) - ch + 1, ch))) if ch > 1 else s
        return resample(mono, r, rate)
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate),
                        "-f", "f32le", "-"], capture_output=True, check=True)
    s = array.array("f")
    s.frombytes(r.stdout)
    return s


def resample(x, rate, to):
    """x from rate to `to`: each output sample the mean of the input over its own period (a box low-pass), read
    by linear interpolation between those means."""
    if rate == to:
        return array.array("f", x)
    step = rate / to
    k = max(1, int(round(step)))
    # running sums: the mean of x[j:j+k] in O(1) a sample
    acc = [0.0]
    total = 0.0
    for v in x:
        total += v
        acc.append(total)
    n_means = len(x) - k + 1
    if n_means <= 0:
        return array.array("f")
    out = array.array("f")
    pos = 0.0
    while pos <= n_means - 1:
        j = int(pos)
        f = pos - j
        m0 = (acc[j + k] - acc[j]) / k
        m1 = (acc[j + 1 + k] - acc[j + 1]) / k if j + 1 < n_means else m0
        out.append(m0 * (1 - f) + m1 * f)
        pos += step
    return out


# ---- envelope, silence, clipping

def envelope(samples, rate, ch, window_s=WINDOW_S):
    """The RMS over every channel of each window_s window, in dBFS."""
    n = int(rate * window_s) * ch
    out = []
    for i in range(0, len(samples) - n + 1, n):
        w = samples[i:i + n]
        out.append(db(math.fsum(v * v for v in w) / n))
    return out


def silences(env, window_s=WINDOW_S, floor=SILENT_DB, min_s=GAP_MIN_S):
    """[(start s, length s)] of each run of windows at or below floor lasting min_s or more."""
    runs, start = [], None
    for i, v in enumerate(env + [0.0]):
        if v <= floor and start is None:
            start = i
        elif v > floor and start is not None:
            if (i - start) * window_s >= min_s - 1e-9:
                runs.append((round(start * window_s, 3), round((i - start) * window_s, 3)))
            start = None
    return runs


def clipping(samples, rate, ch, clip=CLIP):
    """Count of samples at or over clip, over 1.0, the peak, and the times (s) of the first few clipped."""
    n_clip = n_over = 0
    peak = 0.0
    first = []
    for i, v in enumerate(samples):
        a = v if v >= 0 else -v
        if a > peak:
            peak = a
        if a >= clip:
            n_clip += 1
            if a > 1.0:
                n_over += 1
            if len(first) < 5:
                first.append(round(i / ch / rate, 3))
    return {"clipped": n_clip, "over_full_scale": n_over, "peak_dbfs": round(20 * math.log10(peak), 2) if peak else -200.0,
            "first_clipped_s": first}


def dip(env, at_s, window_s=WINDOW_S, around_s=10.0):
    """Around a change at at_s: the level before and after (medians over around_s), the lowest window within 2 s,
    how far it is under the quieter side, and how long the level stays 6 dB or more under the quieter side."""
    i = int(at_s / window_s)
    k = int(around_s / window_s)
    before = sorted(env[max(0, i - k):max(0, i - int(2 / window_s))]) or [-200.0]
    after = sorted(env[i + int(2 / window_s):i + k]) or [-200.0]
    lb, la = before[len(before) // 2], after[len(after) // 2]
    near = env[max(0, i - int(2 / window_s)):i + int(2 / window_s)] or [-200.0]
    low = min(near)
    ref = min(lb, la)
    below = 0
    for v in near:
        below += v <= ref - 6
    return {"at_s": at_s, "before_db": round(lb, 1), "after_db": round(la, 1), "lowest_db": round(low, 1),
            "dip_db": round(ref - low, 1), "dip_s": round(below * window_s, 2)}


# ---- alignment

def fft(a, inverse=False):
    """In-place iterative radix-2 FFT of a list of complex numbers (len a power of two)."""
    n = len(a)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            a[i], a[j] = a[j], a[i]
    size = 2
    sign = 1 if inverse else -1
    while size <= n:
        w = cmath.exp(sign * 2j * math.pi / size)
        half = size // 2
        for start in range(0, n, size):
            wk = 1
            for k in range(start, start + half):
                t = a[k + half] * wk
                a[k + half] = a[k] - t
                a[k] += t
                wk *= w
        size *= 2
    if inverse:
        for i in range(n):
            a[i] /= n
    return a


def onsets(x, hop=ONSET_HOP):
    """The rise of the log envelope, hop samples a step, mean removed: what a song looks like whatever its gain."""
    env = []
    for i in range(0, len(x) - hop + 1, hop):
        env.append(db(math.fsum(v * v for v in x[i:i + hop]) / hop + 1e-10))
    d = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    m = sum(d) / len(d) if d else 0.0
    return [v - m for v in d]


def locate(rec_on, ref_on, hop=ONSET_HOP):
    """Where ref starts in the recording (samples at rate, may be negative) and the peak's sharpness: its height
    over the cross-correlation's own spread (the songs of a recording stood 60 or more out, 2026-11-23)."""
    n = 1
    while n < len(rec_on) + len(ref_on):
        n *= 2
    A = fft([complex(v) for v in rec_on] + [0j] * (n - len(rec_on)))
    B = fft([complex(v) for v in ref_on] + [0j] * (n - len(ref_on)))
    c = fft([x * y.conjugate() for x, y in zip(A, B)], inverse=True)
    vals = [v.real for v in c]
    best = max(range(n), key=lambda i: vals[i])
    lag = best if best < n // 2 else best - n
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) or 1.0
    return lag * hop, (vals[best] - mean) / sd


def corr(x, xo, y, yo, n):
    """Normalised correlation of x[xo:xo+n] and y[yo:yo+n] (0 where either is out of range or silent)."""
    if xo < 0 or yo < 0 or xo + n > len(x) or yo + n > len(y):
        return 0.0
    sxy = sxx = syy = 0.0
    for i in range(n):
        a, b = x[xo + i], y[yo + i]
        sxy += a * b
        sxx += a * a
        syy += b * b
    return sxy / math.sqrt(sxx * syy) if sxx > 0 and syy > 0 else 0.0


def refine(rec, ref, off, at_s, rate=FIT_RATE, block_s=0.5, search=2 * ONSET_HOP):
    """off moved within search samples to where ref[i - off] best matches the recording over block_s from at_s."""
    n, r0 = int(block_s * rate), int(at_s * rate)
    best, bc = off, -1.0
    for d in range(-search, search + 1):
        c = corr(rec, r0, ref, r0 - (off + d), n)
        if c > bc:
            best, bc = off + d, c
    return best


def segment(y, at, n):
    return [y[at + i] if 0 <= at + i < len(y) else 0.0 for i in range(n)]


def fit_change(rec, a_ref, a_off, b_ref, b_off, t0, t1, rate=FIT_RATE, window_s=WINDOW_S):
    """Every window from t0 to t1 s of rec fitted as a*A + b*B, each song's alignment (rec index - ref index)
    followed within LAG_SEARCH a window. A row per window: t (s), the recording's dBFS, how far the fit's residual
    is under it (dB), then for A and for B: its own window's dBFS in its file (None outside the file), its gain
    (dB; None where its file is silent there) and its contribution (dBFS)."""
    n = int(rate * window_s)
    rows = []
    offs = [a_off, b_off]
    start = [a_off, b_off]
    refs = [a_ref, b_ref]
    for w in range(round(t0 / window_s), round(t1 / window_s)):
        r0 = w * n
        if r0 + n > len(rec):
            break
        x = rec[r0:r0 + n]
        for k in (0, 1):  # follow each song's alignment where it is clearly there
            best, bc = offs[k], 0.0
            for d in range(-LAG_SEARCH, LAG_SEARCH + 1):
                c = abs(corr(rec, r0, refs[k], r0 - (offs[k] + d), n))
                if c > bc:
                    best, bc = offs[k] + d, c
            if bc >= MIN_CORR and abs(best - start[k]) <= LAG_DRIFT_MAX:
                offs[k] = best
        segs = [segment(refs[k], r0 - offs[k], n) for k in (0, 1)]
        inside = [0 <= r0 - offs[k] and r0 - offs[k] + n <= len(refs[k]) for k in (0, 1)]
        a, b = segs
        saa = math.fsum(v * v for v in a)
        sbb = math.fsum(v * v for v in b)
        sab = math.fsum(p * q for p, q in zip(a, b))
        sxa = math.fsum(p * q for p, q in zip(x, a))
        sxb = math.fsum(p * q for p, q in zip(x, b))
        det = saa * sbb - sab * sab
        if saa > 1e-9 and sbb > 1e-9 and abs(det) > 1e-12 * saa * sbb:
            g = [(sxa * sbb - sxb * sab) / det, (sxb * saa - sxa * sab) / det]
        else:
            g = [sxa / saa if saa > 1e-9 else 0.0, sxb / sbb if sbb > 1e-9 else 0.0]
        ex = math.fsum(v * v for v in x) / n
        res = math.fsum((x[i] - g[0] * a[i] - g[1] * b[i]) ** 2 for i in range(n)) / n
        row = [round(w * window_s, 3), round(db(ex), 1), round(db(ex) - db(res), 1)]
        for k, e in ((0, saa / n), (1, sbb / n)):
            own = db(e)
            loud = inside[k] and own > SILENT_DB
            row += [round(own, 1) if inside[k] else None, round(db(g[k] * g[k]), 1) if loud else None,
                    round(db(g[k] * g[k] * e), 1)]
        rows.append(tuple(row))
    return rows


T, REC, FIT, A_OWN, A_GAIN, A_LEVEL, B_OWN, B_GAIN, B_LEVEL = range(9)


def presence(rows, own, gain, steady, below=PRESENT_BELOW, trust=TRUST_DB):
    """Per row, whether the song plays (its gain within `below` dB of its steady gain) and whether that was seen
    there. A window's gain is read only where the song's own window in its file is not silent and is within `trust`
    dB of the recording's level: a song far under the other cannot be told apart from the fit's error. Elsewhere in
    its file the previous answer holds, unseen; outside its file, no."""
    plays, seen, last = [], [], False
    for r in rows:
        looked = False
        if r[own] is None:
            last = False
        elif r[gain] is not None and r[own] >= r[REC] - trust:
            last, looked = r[gain] >= steady - below, True
        plays.append(last)
        seen.append(last and looked)
    return plays, seen


def median(v, default=None):
    v = sorted(x for x in v if x is not None)
    return v[len(v) // 2] if v else default


def change_from_rows(rows, b_at_s, window_s=WINDOW_S):
    """The change in a fit: A's last window playing and B's first, the overlap (both playing) and the gap (neither
    playing, the recording silent), A's fade-out and B's fade-in. b_at_s, B's start in the recording as located,
    splits the steady parts: A's steady gain is its median more than 2 s before that, B's more than 16 s after."""
    sa = median(r[A_GAIN] for r in rows if r[T] < b_at_s - 2 - 15)
    sa = median(r[A_GAIN] for r in rows if r[T] < b_at_s - 2) if sa is None else sa
    sb = median(r[B_GAIN] for r in rows if r[T] > b_at_s + 16)
    sb = median(r[B_GAIN] for r in rows if r[T] > b_at_s + 2) if sb is None else sb
    if sa is None or sb is None:
        return {"error": "a song never plays alone in the fitted span"}
    pa, seen_a = presence(rows, A_OWN, A_GAIN, sa)
    pb, _ = presence(rows, B_OWN, B_GAIN, sb)
    run = int(0.5 / window_s)  # B's entry: the first window of its first 0.5 s run
    first_b = next((i for i in range(len(rows) - run + 1) if all(pb[i:i + run])), None)
    if first_b is None or not any(seen_a[:first_b + 1]):
        return {"error": "B never plays for 0.5 s, or A not before it"}
    last_a = max(i for i in range(len(rows)) if seen_a[i])  # A's last window seen playing
    overlap = max(0, last_a - first_b + 1)
    gap = sum(1 for i in range(last_a + 1, first_b) if rows[i][REC] <= SILENT_DB)

    def a_last_at_least(level):  # the end of A's last window read at level or over, up to A's end
        idx = [i for i in range(last_a + 1) if rows[i][A_GAIN] is not None and rows[i][A_OWN] >= rows[i][REC] - TRUST_DB
               and rows[i][A_GAIN] >= level]
        return round(rows[idx[-1]][T] + window_s, 3) if idx else None

    full_b = next((rows[i][T] for i in range(first_b, len(rows)) if rows[i][B_GAIN] is not None and rows[i][B_GAIN] >= sb - 3), None)
    fit = median((r[FIT] for r in rows if r[REC] > SILENT_DB), 0.0)
    return {"b_enters_s": rows[first_b][T], "a_ends_s": round(rows[last_a][T] + window_s, 3),
            "overlap_s": round(overlap * window_s, 2), "gap_s": round(gap * window_s, 2),
            "a_gain_db": sa, "b_gain_db": sb,
            "a_fade_from_s": a_last_at_least(sa - 3), "a_fade_20db_s": a_last_at_least(sa - 20),
            "b_full_s": full_b, "b_fade_in_s": round(full_b - rows[first_b][T], 2) if full_b is not None else None,
            "fit_db_median": fit}


# ---- the whole analysis

def analyse(wav, refs=(), at=(), want_rows=False):
    rate, ch, s = read_wav(wav)
    env = envelope(s, rate, ch)
    out = {"file": str(wav), "rate": rate, "channels": ch, "seconds": round(len(s) / ch / rate, 3),
           "window_s": WINDOW_S, "clipping": clipping(s, rate, ch), "silences": silences(env), "changes": []}
    for t in at:
        out["changes"].append({"dip": dip(env, t)})
    if len(refs) >= 2:
        rec = decode_mono(wav)
        rec_on = onsets(rec)
        placed = []
        for r in refs:
            ref = decode_mono(r)
            off, sharp = locate(rec_on, onsets(ref))
            placed.append({"ref": str(r), "offset_s": round(off / FIT_RATE, 3), "seconds": round(len(ref) / FIT_RATE, 3),
                           "match": round(sharp, 1), "_ref": ref, "_off": off})
        out["songs"] = [{k: v for k, v in p.items() if not k.startswith("_")} for p in placed]
        for a, b in zip(placed, placed[1:]):
            t_b = b["_off"] / FIT_RATE
            t0 = max(0.0, max(a["_off"] / FIT_RATE, t_b - 25.0))
            t1 = min(out["seconds"], t_b + 20.0)
            # each song's alignment to the sample where it plays alone: A at the fit's start, B 6 s after it enters
            a_off = refine(rec, a["_ref"], a["_off"], t0 + 0.5)
            b_off = refine(rec, b["_ref"], b["_off"], min(t1 - 1.0, t_b + 6.0))
            rows = fit_change(rec, a["_ref"], a_off, b["_ref"], b_off, t0, t1)
            ch_ = {"a": a["ref"], "b": b["ref"], "fit_span_s": [round(t0, 2), round(t1, 2)]}
            ch_.update(change_from_rows(rows, t_b))
            if "b_enters_s" in ch_:
                ch_["dip"] = dip(env, ch_["b_enters_s"])
                # where A stopped, in A's own time: how far before its file's end it went quiet
                ch_["a_ends_before_file_end_s"] = round(a["_off"] / FIT_RATE + a["seconds"] - ch_["a_ends_s"], 2)
                if ch_["a_fade_from_s"] is not None:
                    ch_["a_fade_from_before_file_end_s"] = round(a["_off"] / FIT_RATE + a["seconds"] - ch_["a_fade_from_s"], 2)
            if want_rows:
                ch_["rows"] = rows
            out["changes"].append(ch_)
    return out


# ---- calibration: known song changes made from two synthetic songs (docs/bt-rig.md#calibration)
#
# Each case is song A then song B as one recording: `cut` (B straight after A, gapless), `fade5` (the last 5 s of A
# under the first 5 s of B, equal power: cos and sin, as q2player's fade) and `gap2` (2 s of digital silence
# between). The recording is then made what the rig records: resampled to 48 kHz with a clock 60 ppm fast (a Q2's
# against the laptop's), 0.37 s of silence in front, a gain of -3 dB, and through a lossy codec and back with ffmpeg
# (SBC by default). A and B are kept as FLAC, as the card holds songs.

SYN_RATE = 22050          # the synthetic songs' rate; the rig's is 48 kHz, so the resampling is exercised too
SONG_S = 30.0
FADE_S = 5.0
GAP_S = 2.0
LEAD_S = 0.37
DRIFT = 60e-6
GAIN = 10 ** (-3 / 20)


def song(seed, seconds=SONG_S, rate=SYN_RATE):
    """A mono song-like signal: chords of four harmonic notes changing every 0.2-0.6 s with a quick attack and
    decay, and a noise tick every beat, near -14 dBFS RMS. Two seeds give two songs that do not correlate."""
    r = random.Random(seed)
    n = int(seconds * rate)
    out = array.array("f", bytes(4 * n))
    t = 0
    beat = int(rate * r.uniform(0.4, 0.55))
    while t < n:
        dur = int(rate * r.uniform(0.2, 0.6))
        root = 110 * 2 ** (r.randrange(0, 24) / 12)
        notes = [root * 2 ** (k / 12) for k in (0, r.choice((3, 4)), 7, 12)]
        phases = [r.uniform(0, 2 * math.pi) for _ in notes]
        for i in range(min(dur, n - t)):
            env = min(1.0, i / (0.01 * rate)) * math.exp(-i / (0.35 * rate))
            v = 0.0
            for f, ph in zip(notes, phases):
                w = 2 * math.pi * f * i / rate + ph
                v += math.sin(w) + 0.35 * math.sin(2 * w) + 0.15 * math.sin(3 * w)
            out[t + i] += 0.09 * env * v
        t += dur
    for b in range(0, n, beat):
        for i in range(min(int(0.03 * rate), n - b)):
            out[b + i] += 0.25 * r.uniform(-1, 1) * math.exp(-i / (0.006 * rate))
    return out


def mix(a, b, case, rate=SYN_RATE):
    """A then B as the case says."""
    out = array.array("f", a)
    if case == "cut":
        out.extend(b)
    elif case == "gap2":
        out.extend(array.array("f", bytes(4 * int(GAP_S * rate))))
        out.extend(b)
    elif case == "fade5":
        k = int(FADE_S * rate)
        start = len(a) - k
        for i in range(k):
            x = (i + 0.5) / k * math.pi / 2
            out[start + i] = a[start + i] * math.cos(x) + b[i] * math.sin(x)
        out.extend(b[k:])
    else:
        raise ValueError(case)
    return out


def headset(x, rate=SYN_RATE, out_rate=48000, drift=DRIFT, lead=LEAD_S, gain=GAIN):
    """x at out_rate with the clock drift (linear interpolation), lead seconds of silence in front, and the gain."""
    step = rate / out_rate * (1 + drift)
    n = int((len(x) - 1) / step)
    out = array.array("f", bytes(4 * int(lead * out_rate)))
    for i in range(n):
        p = i * step
        j = int(p)
        f = p - j
        out.append(gain * (x[j] * (1 - f) + x[j + 1] * f))
    return out


def write_wav(path, mono, rate, channels=2):
    """Float WAV, the mono signal on every channel."""
    data = array.array("f")
    for v in mono:
        data.extend([v] * channels)
    raw = data.tobytes()
    with open(path, "wb") as f:
        f.write(b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 3, channels, rate, rate * channels * 4, channels * 4, 32) +
                b"data" + struct.pack("<I", len(raw)) + raw)


def codec_round_trip(path, codec):
    """path through the codec and back, in place (float WAV, 48 kHz stereo)."""
    if codec == "none":
        return
    enc = {"sbc": ["-c:a", "sbc", "-b:a", "328k", "-f", "sbc"], "aac": ["-c:a", "aac", "-b:a", "256k", "-f", "adts"]}[codec]
    tmp = path.with_suffix("." + codec)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), *enc, str(tmp)], check=True)
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(tmp), "-c:a", "pcm_f32le", "-ar", "48000",
                    str(path)], check=True)
    tmp.unlink()


def make(outdir, codec="sbc", cases=("cut", "fade5", "gap2"), seconds=SONG_S, flac=True):
    """A and B (FLAC, or float WAV with flac False) and each case's recording (a float WAV) in outdir."""
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    a, b = song(1, seconds), song(2, seconds)
    refs = []
    for name, x in (("A", a), ("B", b)):
        w = outdir / f"{name}.wav"
        write_wav(w, x, SYN_RATE)
        if flac:
            subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(w), "-c:a", "flac",
                            str(outdir / f"{name}.flac")], check=True)
            w.unlink()
            w = outdir / f"{name}.flac"
        refs.append(w)
    made = {}
    for case in cases:
        p = outdir / f"{case}.wav"
        write_wav(p, headset(mix(a, b, case)), 48000)
        codec_round_trip(p, codec)
        made[case] = p
    return refs[0], refs[1], made


# What each case must measure: (overlap's range, gap's range) in seconds. The fade's overlap is a little under
# FADE_S because equal power puts each song's first and last 2 % of the fade under PRESENT_BELOW.
EXPECT = {"cut": ((0.0, 0.1), (0.0, 0.1)), "fade5": ((4.5, 5.0), (0.0, 0.1)), "gap2": ((0.0, 0.1), (1.8, 2.1))}


def calibrate(outdir, codec="sbc", seconds=SONG_S, flac=True):
    """Each case made and analysed: [(case, overlap, gap, passed)]."""
    a, b, made = make(outdir, codec, tuple(EXPECT), seconds, flac)
    out = []
    for case, wav in made.items():
        c = analyse(wav, [a, b])["changes"][0]
        (olo, ohi), (glo, ghi) = EXPECT[case]
        ok = "overlap_s" in c and olo <= c["overlap_s"] <= ohi and glo <= c["gap_s"] <= ghi
        out.append((case, c.get("overlap_s"), c.get("gap_s"), ok))
    return out



def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="bt_rig.py analyse", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("wav")
    p.add_argument("--ref", action="append", default=[], help="a song in the recording, in play order")
    p.add_argument("--at", action="append", type=float, default=[], help="a change's time (s into the recording)")
    p.add_argument("--rows", action="store_true", help="include every fitted window")
    a = p.parse_args(argv)
    json.dump(analyse(a.wav, a.ref, a.at, a.rows), sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
