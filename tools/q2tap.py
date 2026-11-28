"""The output tap's reader (docs/usb-audio.md#the-tap): q2player's tap stream (player/tap.c) back into PCM, as WAV
files or played live, for `vamp audio`.

The stream is records: an 8-byte header, a tag and the payload's length (little-endian 32 bits), then the payload.
Q2TF is the format of the frames after it (rate u32, channels u16, bytes a sample u8, kind u8: 0 integer, 1 float,
2 DoP, 3 native DSD); Q2TD frames as the device was handed them; Q2TB frames (u32) taken back unheard, which the
frames after it replace; Q2TL frames (u32) the player dropped because the reader fell behind. Anything before the
first Q2TF is an earlier reader's and is skipped.

Tap holds the newest HOLD_S of frames back from its sink, so a take-back (a click mixed into what the device held,
up to the device's half-second buffer: player/output.c BUFFER_US) is applied before they go: the WAV is what the
device played, bit for bit, while nothing was dropped. A take-back larger than what is held is counted (`unheld`).
Dropped frames go out as silence, so the WAV keeps time, and are counted (`lost`)."""
import struct
import subprocess
import sys

HOLD_S = 1.0  # more than the device's buffer (BUFFER_US, 0.5 s): the most a take-back reaches
KINDS = ('integer', 'float', 'DoP', 'native DSD')
HEAD = struct.Struct('<4sI')


class Format(tuple):
    """(rate, channels, bytes a sample, kind)"""
    __slots__ = ()

    def __new__(cls, rate, channels, width, kind):
        return tuple.__new__(cls, (rate, channels, width, kind))

    rate = property(lambda f: f[0])
    channels = property(lambda f: f[1])
    width = property(lambda f: f[2])
    kind = property(lambda f: f[3])
    frame = property(lambda f: f[1] * f[2])

    def ffmpeg(self):
        """FFmpeg's name of the raw sample format."""
        return 'f32le' if self.kind == 1 else f's{8 * self.width}le'

    def __str__(self):
        bits = f'{8 * self.width}-bit {KINDS[self.kind] if self.kind < len(KINDS) else "kind %d" % self.kind}'
        return f'{self.rate} Hz, {self.channels} ch, {bits}'


class Tap:
    """feed() the stream as it comes; close() at its end. open_sink(fmt) gives an object with write(bytes) and
    close() for each run of one format."""

    def __init__(self, open_sink, hold_s=HOLD_S):
        self.open_sink, self.hold_s = open_sink, hold_s
        self.buf = bytearray()
        self.fmt = self.sink = None
        self.pending = bytearray()
        self.frames = self.lost = self.back = self.unheld = 0
        self.formats = []

    def feed(self, data):
        self.buf += data
        at = 0
        while len(self.buf) - at >= HEAD.size:
            tag, n = HEAD.unpack_from(self.buf, at)
            if len(self.buf) - at - HEAD.size < n:
                break
            body = bytes(self.buf[at + HEAD.size:at + HEAD.size + n])
            at += HEAD.size + n
            self.record(tag, body)
        del self.buf[:at]

    def record(self, tag, body):
        if tag == b'Q2TF':
            f = Format(*struct.unpack('<IHBB', body))
            if f != self.fmt:
                self.flush()
                if self.sink:
                    self.sink.close()
                self.fmt, self.sink = f, self.open_sink(f)
                self.formats.append(f)
            return
        if self.fmt is None:
            return  # an earlier reader's, before this one's first format
        fb = self.fmt.frame
        if tag == b'Q2TD':
            self.pending += body
            self.frames += len(body) // fb
        elif tag == b'Q2TB':
            (n,) = struct.unpack('<I', body)
            cut = min(n, len(self.pending) // fb)
            del self.pending[len(self.pending) - cut * fb:]
            self.frames -= cut
            self.back += cut
            self.unheld += n - cut
        elif tag == b'Q2TL':
            (n,) = struct.unpack('<I', body)
            self.pending += bytes(n * fb)
            self.frames += n
            self.lost += n
        hold = int(self.fmt.rate * self.hold_s) * fb
        if len(self.pending) > hold:
            assert self.sink is not None  # frames come after a format, which opened the sink
            self.sink.write(bytes(self.pending[:len(self.pending) - hold]))
            del self.pending[:len(self.pending) - hold]

    def flush(self):
        if self.sink and self.pending:
            self.sink.write(bytes(self.pending))
        self.pending.clear()

    def close(self):
        self.flush()
        if self.sink:
            self.sink.close()
        self.sink = None

    def seconds(self):
        return self.frames / self.fmt.rate if self.fmt else 0.0


class Wav:
    """A WAV file of one format: PCM (tag 1), or IEEE float (tag 3); sizes written at close."""

    def __init__(self, path, fmt):
        self.f, self.fmt, self.n = open(path, 'wb'), fmt, 0
        self.header()

    def header(self):
        f = self.fmt
        tag = 3 if f.kind == 1 else 1
        self.f.write(b'RIFF' + struct.pack('<I', 36 + self.n) + b'WAVEfmt ' +
                     struct.pack('<IHHIIHH', 16, tag, f.channels, f.rate, f.rate * f.frame, f.frame, 8 * f.width) +
                     b'data' + struct.pack('<I', self.n))

    def write(self, data):
        self.f.write(data)
        self.n += len(data)

    def close(self):
        self.f.seek(0)
        self.header()
        self.f.close()


def wav_paths(base):
    """out.wav, then out-2.wav, out-3.wav... for each later format."""
    stem = base[:-4] if base.lower().endswith('.wav') else base
    n = 0
    while True:
        n += 1
        yield f'{stem}.wav' if n == 1 else f'{stem}-{n}.wav'


class Player:  # pragma: no mutate block
    """ffplay reading the raw frames on its stdin (one per format)."""

    def __init__(self, fmt, ffplay='ffplay'):
        layout = {1: 'mono', 2: 'stereo'}.get(fmt.channels, f'{fmt.channels}c')
        cmd = [ffplay, '-v', 'error', '-nodisp', '-autoexit', '-f', fmt.ffmpeg(), '-ar', str(fmt.rate),
               '-ch_layout', layout, '-i', '-']
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        stdin = self.p.stdin
        assert stdin is not None  # stdin=PIPE
        self.stdin = stdin

    def write(self, data):
        try:
            self.stdin.write(data)
        except BrokenPipeError:
            pass

    def close(self):
        try:
            self.stdin.close()
        except BrokenPipeError:
            pass
        self.p.wait()


class Both:
    def __init__(self, sinks):
        self.sinks = sinks

    def write(self, data):
        for s in self.sinks:
            s.write(data)

    def close(self):
        for s in self.sinks:
            s.close()


def sinks(out=None, play=False, say=None, player=Player):
    """open_sink for Tap: WAVs at out's names, ffplay when play; each format said through say."""
    names = wav_paths(out) if out else None

    def open_sink(fmt):
        parts = []
        if names:
            path = next(names)
            parts.append(Wav(path, fmt))
            what = path
        else:
            what = 'playing'
        if play:
            parts.append(player(fmt))
        if say:
            say(f'{what}: {fmt}')
        return Both(parts)
    return open_sink


def summary(tap):
    s = f'{tap.frames} frames ({tap.seconds():.1f} s)'
    if tap.lost:
        s += f', {tap.lost} dropped by the player (the reader behind; silence in their place)'
    if tap.back:
        s += f', {tap.back} taken back'
    if tap.unheld:
        s += f', {tap.unheld} taken back past what was held'
    return s


def main(argv=None):  # pragma: no mutate block
    """q2tap.py IN OUT.wav: a saved stream (adb exec-out 'cat /tmp/q2tap.pcm' > IN) into WAV."""
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 2:
        sys.exit(main.__doc__)
    tap = Tap(sinks(a[1], say=lambda t: print(t, file=sys.stderr)))
    with open(a[0], 'rb') as f:
        while True:
            part = f.read(1 << 16)
            if not part:
                break
            tap.feed(part)
    tap.close()
    print(summary(tap), file=sys.stderr)
    return 0


if __name__ == '__main__':
    main()
