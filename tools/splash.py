#!/usr/bin/env python3
"""Pack rendered frames into the boot animation q2splash plays (docs/boot.md#boot-animation).

    python3 tools/splash.py FRAMES_DIR assets/boot-anim.q2a [--fps 30]

FRAMES_DIR holds the animation's frames as upright images, 375x320 or a whole multiple of it,
named so that sorting them gives their order. The file is the frames as the framebuffer stores
them (320x375, turned 90 degrees clockwise) in 16 grey levels, each frame as its difference from
the one before:

    header   'Q2AN', then little-endian u16 fps, frames, width, height
    frame    u32 byte count, then runs until the count is used up:
             varint pixels to skip, varint pixels that follow, those pixels' levels two to a
             byte (low nibble first)

Pixels run left to right, top to bottom, and a frame starts from the one before it (the first
from black). Identical frames at the end are dropped: the last frame stays on the screen.
"""
import argparse, pathlib, struct

MAGIC = b'Q2AN'
W, H = 320, 375   # /dev/fb0
LEVELS = 16
GAP = 4           # unchanged pixels between two changes that cost less inside a run than a new run

def varint(n):
    out = bytearray()
    while True:
        out.append(n & 0x7f | (0x80 if n > 0x7f else 0))
        n >>= 7
        if not n: return bytes(out)

def encode_frame(prev, cur):
    """The runs that turn prev into cur, both bytes of W*H levels."""
    out, at, i, n = bytearray(), 0, 0, len(cur)
    while i < n:
        if cur[i] == prev[i]:
            i += 1
            continue
        end = j = i + 1
        while j < n and j - end < GAP:
            if cur[j] != prev[j]: end = j + 1
            j += 1
        run = cur[i:end]
        out += varint(i - at) + varint(len(run))
        padded = run + bytes(len(run) & 1)
        out += bytes(padded[k] | padded[k + 1] << 4 for k in range(0, len(padded), 2))
        at = i = end
    return bytes(out)

def decode_frame(prev, data):
    """encode_frame's inverse, as patch/splash.c applies a frame."""
    cur, at, i = bytearray(prev), 0, 0
    def read():
        nonlocal i
        v = shift = 0
        while True:
            b = data[i]; i += 1
            v |= (b & 0x7f) << shift; shift += 7
            if b < 0x80: return v
    while i < len(data):
        at += read()
        n = read()
        for k in range(n):
            cur[at + k] = data[i + k // 2] >> 4 * (k & 1) & 15
        at += n; i += (n + 1) // 2
    return bytes(cur)

def pack(frames, fps):
    """The file for frames, each bytes of W*H levels."""
    frames = list(frames)
    while len(frames) > 1 and frames[-1] == frames[-2]: frames.pop()
    out, prev = bytearray(MAGIC + struct.pack('<4H', fps, len(frames), W, H)), bytes(W * H)
    for cur in frames:
        data = encode_frame(prev, cur)
        out += struct.pack('<I', len(data)) + data
        prev = cur
    return bytes(out)

def unpack(blob):
    """(fps, frames) of a packed file."""
    assert blob[:4] == MAGIC, 'Not a boot animation'
    fps, count, w, h = struct.unpack_from('<4H', blob, 4)
    assert (w, h) == (W, H), 'Boot animation must be 320x375'
    frames, prev, at = [], bytes(W * H), 12
    for _ in range(count):
        n, = struct.unpack_from('<I', blob, at)
        prev = decode_frame(prev, blob[at + 4:at + 4 + n])
        frames.append(prev)
        at += 4 + n
    assert at == len(blob), 'Boot animation has trailing bytes'
    return fps, frames

def levels(path):
    """An upright frame as the framebuffer's W*H levels: black 0, the brightest pixel a render uses 15."""
    from PIL import Image
    im = Image.open(path).convert('L')
    assert im.width % H == 0 and im.height * H == im.width * W, f'{path}: not 375x320 or a multiple'
    im = im.resize((H, W), Image.BOX).transpose(Image.ROTATE_270)
    return im.tobytes()

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('frames', type=pathlib.Path)
    ap.add_argument('out', type=pathlib.Path)
    ap.add_argument('--fps', type=int, default=30)
    a = ap.parse_args()
    grey = [levels(p) for p in sorted(a.frames.iterdir())]
    peak = max(max(g) for g in grey)   # the line colour is a warm off-white, not 255
    frames = [bytes(min(LEVELS - 1, (v * (LEVELS - 1) * 2 + peak) // (2 * peak)) for v in g) for g in grey]
    blob = pack(frames, a.fps)
    a.out.write_bytes(blob)
    print(f'{a.out}: {struct.unpack_from("<H", blob, 6)[0]} frames at {a.fps} fps, {len(blob)} bytes')

if __name__ == '__main__':
    main()
