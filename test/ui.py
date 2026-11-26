#!/usr/bin/env python3
"""q2ui, the menu UI spike (ui/, docs/ui-rewrite.md), built for this machine and run against a file standing in
for the panel and FIFOs standing in for the wheel and the buttons: a made-up library, ticks fed as the wheel's
own events, and the frame read back from the file.

    python3 test/ui.py

What it holds: the selection follows the ticks one row each, the bar is drawn on the selected row and nowhere
else, a scroll drawn by copying rows leaves the same picture as one drawn whole, a list at rest draws no frame and
is not woken,
and Return ends the program. Without the build's inputs (stb_truetype and SQLite's header in the third-party
cache, the host's SQLite) it prints why it is skipped and passes.
"""
import json
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT/'tools'))
sys.path.insert(0, str(ROOT/'ui/tools'))
import fixture  # noqa: E402
import shot  # noqa: E402
import ui  # noqa: E402

ACCENT = (0x21, 0x60, 0xd8)
BAR_H, ROW_H, ROWS = 58, 32, 7
EV_SYN, EV_KEY, EV_ABS, ABS_X, ABS_PRESSURE, BTN_TOUCH, KEY_RETURN = 0, 1, 3, 0, 0x18, 0x14a, 103
TICK_UNITS = 28


def event(kind, code, value):
    return struct.pack('llHHi', 0, 0, kind, code, value)  # this machine's struct input_event


class Run:
    """One q2ui over a library of n songs, with its panel file, inputs and stats in a folder of its own."""

    def __init__(self, binary, work, n, *options):
        self.dir = pathlib.Path(tempfile.mkdtemp(dir=work))
        fixture.make(n, self.dir/'library.db')
        fonts = self.dir/'fonts'
        fonts.mkdir()
        shutil.copy(ROOT/'assets/fonts/Inter-Regular.ttf', fonts/'inter.ttf')
        shutil.copy(ROOT/'assets/fonts/Inter-Medium.ttf', fonts/'inter-medium.ttf')
        (self.dir/'input').mkdir()
        for k in (1, 3):
            os.mkfifo(self.dir/'input'/f'event{k}')
        (self.dir/'fb').write_bytes(bytes(320 * 375 * 2 * 4))
        self.proc = subprocess.Popen([str(binary), '--db', self.dir/'library.db', '--fb', self.dir/'fb', '--input',
                                      self.dir/'input', '--fonts', fonts, '--stats', self.dir/'stats.json', *options])
        self.wheel = open(self.dir/'input/event3', 'wb', buffering=0)
        self.keys = open(self.dir/'input/event1', 'wb', buffering=0)
        self.x = 100
        self.wheel.write(event(EV_ABS, ABS_X, self.x) + event(EV_ABS, ABS_PRESSURE, 1) + event(EV_KEY, BTN_TOUCH, 1) +
                         event(EV_SYN, 0, 0))
        time.sleep(0.3)

    def ticks(self, n, gap=0.16):
        """n ticks down (up when negative), far enough apart that none accelerates (patch/wheel_accel.h WA_RUN_MS)."""
        for _ in range(abs(n)):
            self.x = (self.x - (TICK_UNITS if n > 0 else -TICK_UNITS) - 1) % 200 + 1
            self.wheel.write(event(EV_ABS, ABS_X, self.x) + event(EV_SYN, 0, 0))
            time.sleep(gap)

    def stats(self):
        return json.loads((self.dir/'stats.json').read_text())

    def wakes(self):
        """How often the program has been woken from a wait (its voluntary context switches)."""
        for line in pathlib.Path(f'/proc/{self.proc.pid}/status').read_text().splitlines():
            if line.startswith('voluntary_ctxt_switches'):
                return int(line.split()[1])
        raise RuntimeError('no voluntary_ctxt_switches')

    def rest(self):
        """Wait out the motion (the indicator's second) and read the stats q2ui writes once it rests."""
        time.sleep(1.4)
        return self.stats()

    def picture(self):
        return shot.upright(self.dir/'fb', self.stats()['page'])

    def end(self):
        self.keys.write(event(EV_KEY, KEY_RETURN, 1) + event(EV_SYN, 0, 0) + event(EV_KEY, KEY_RETURN, 0) + event(EV_SYN, 0, 0))
        code = self.proc.wait(timeout=5)
        return code, self.stats()


def bar_rows(picture):
    """The list rows whose middle holds the accent, by the pixel at x 200, 3 px into the row."""
    px = picture.load()
    return [k for k in range(ROWS) if px[200, BAR_H + k * ROW_H + 3] == ACCENT]


def check(what, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + what + (f': {detail}' if detail and not ok else ''))
    return ok


def main():
    try:  # the build's inputs; a compile that fails with them all there is a failure, not a skip
        ui.stb(), ui.sqlite_include()
        if not ui.HOST_SQLITE.exists():
            raise SystemExit(f'no {ui.HOST_SQLITE}')
    except (SystemExit, OSError) as e:
        print(f'skipped: q2ui cannot be built here ({e})')
        return 0
    binary = ui.build('host')
    good = True
    with tempfile.TemporaryDirectory() as work:
        # the first frame, three ticks, a rest
        r = Run(binary, work, 300, '--motion', '1')
        first = r.rest()
        good &= check('the first frame shows the bar on the Shuffle row', bar_rows(r.picture()) == [0], str(bar_rows(r.picture())))
        good &= check('300 songs were read', first['rows'] == 300, str(first['rows']))
        r.ticks(3)
        moved = r.rest()
        good &= check('three ticks are three rows', moved['ticks'] == 3 and bar_rows(r.picture()) == [3],
                      f"{moved['ticks']} ticks, bar on {bar_rows(r.picture())}")
        woke = r.wakes()
        time.sleep(1.0)
        good &= check('a list at rest draws no frame', r.stats()['frames'] == moved['frames'],
                      f"{moved['frames']} then {r.stats()['frames']}")
        # asleep in poll until input or the clock's next minute: one wake at most in this second
        good &= check('a list at rest does not wake', r.wakes() - woke <= 1, f'{r.wakes() - woke} wakes in 1 s')
        r.ticks(-2)
        back = r.rest()
        good &= check('two ticks back are two rows up', bar_rows(r.picture()) == [1], str(bar_rows(r.picture())))
        good &= check('a frame turns only what changed', back['px_turned_per_frame'] < 375 * 320 // 2, str(back['px_turned_per_frame']))
        code, last = r.end()
        good &= check('Return ends it cleanly', code == 0 and last['ticks'] == 5, f'exit {code}, {last["ticks"]} ticks')

        # a scroll drawn whole and one drawn by copying rows: the same picture
        pictures = []
        for options in (('--motion', '0'), ('--motion', '0', '--copy')):
            r = Run(binary, work, 300, *options)
            r.ticks(19)
            r.ticks(-4)
            s = r.rest()
            pictures.append((r.picture(), s))
            r.end()
        (whole, ws), (copied, cs) = pictures
        good &= check('both runs scrolled to the same row', ws['ticks'] == cs['ticks'] == 23 and bar_rows(whole) == bar_rows(copied) == [2],
                      f"{ws['ticks']} and {cs['ticks']} ticks, bars {bar_rows(whole)} {bar_rows(copied)}")
        good &= check('a copied scroll leaves the picture a whole redraw does', whole.tobytes() == copied.tobytes())
    print('q2ui: ' + ('all checks passed' if good else 'FAILED'))
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
