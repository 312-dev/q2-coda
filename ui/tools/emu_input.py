#!/usr/bin/env python3
"""Send the wheel or a key to demo and to q2ui at once in a q2-emu copy, for the hand-over
(docs/ui-rewrite.md#cutover).

    python3 ui/tools/emu_input.py EMU_DIR wheel N        # N ticks down, negative up
    python3 ui/tools/emu_input.py EMU_DIR key NAME [MS]  # centre, return, play, next, prev

On a Q2 each program that has an input device open is handed every event. The emulator's devices are
FIFOs, where an event goes to whichever reader takes it first, so with both programs running `q2emu wheel`
reaches one of them by chance. This writes each batch twice: to the copy's own FIFOs, which demo reads, and
to a second set in state/data/q2ui-input, which q2ui reads when /mnt/data/q2ui.args says
`--input /mnt/data/q2ui-input`. It makes that folder and its FIFOs when they are not there.
"""
import os
import pathlib
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent/'tools'))
import q2remote as r  # noqa: E402

DEV = {'key': 1, 'wheel': 3}
SAMPLE_S = 0.010  # the viewer's pace for the wheel's samples


def fifos(emu, kind):
    """Both readers' FIFOs for kind, open for writing."""
    ours = emu/'state/data/q2ui-input'
    ours.mkdir(exist_ok=True)
    for n in DEV.values():
        if not (ours/f'event{n}').exists():
            os.mkfifo(ours/f'event{n}')
    return [os.open(d/f'event{DEV[kind]}', os.O_RDWR | os.O_NONBLOCK) for d in (emu/'state/input', ours)]


def stamp(batch):
    """Each 16-byte event given the time of day, as the kernel's evdev does."""
    now = time.time()
    t = struct.pack('<II', int(now), int(now % 1 * 1e6))
    return b''.join(t + batch[i + 8:i + 16] for i in range(0, len(batch), 16))


def send(emu, kind, batches, delay):
    fds = fifos(emu, kind)
    for i, b in enumerate(batches):
        if i:
            time.sleep(delay)
        data = stamp(b)
        for fd in fds:
            os.write(fd, data)
    for fd in fds:
        os.close(fd)


def wheel_batches(ticks):
    ev = r.event
    down = ev(r.EV_ABS, r.ABS_X, 100) + ev(r.EV_ABS, r.ABS_Y, 100) + ev(r.EV_ABS, r.ABS_PRESSURE, 1) + ev(r.EV_KEY, r.BTN_TOUCH, 1) + ev(r.EV_SYN, 0, 0)
    moves = [ev(r.EV_ABS, r.ABS_X, x) + ev(r.EV_ABS, r.ABS_Y, 100 + i % 2) + ev(r.EV_SYN, 0, 0) for i, x in enumerate(r.wheel_positions(ticks))]
    up = ev(r.EV_ABS, r.ABS_PRESSURE, 0) + ev(r.EV_KEY, r.BTN_TOUCH, 0) + ev(r.EV_SYN, 0, 0)
    return [down, *moves, up]


def main():
    if len(sys.argv) < 4 or sys.argv[2] not in ('wheel', 'key'):
        sys.exit(__doc__)
    emu = pathlib.Path(sys.argv[1])
    if sys.argv[2] == 'wheel':
        send(emu, 'wheel', wheel_batches(int(sys.argv[3])), SAMPLE_S)
    else:
        if sys.argv[3] not in r.KEYS:
            sys.exit(f'emu_input: no key {sys.argv[3]}; one of {", ".join(sorted(r.KEYS))}')
        send(emu, 'key', r.key_events(r.KEYS[sys.argv[3]]), (int(sys.argv[4]) if len(sys.argv) > 4 else 60) / 1000)


if __name__ == '__main__':
    main()
