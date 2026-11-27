#!/usr/bin/env python3
"""q2ui, the menu UI of Vamp's own (ui/, docs/ui-rewrite.md), built for this machine and checked three ways:

    python3 test/ui.py

1. The toolkit's own checks (ui/test_engine.c): springs, smoothed edges, sub-pixel glyphs, dithered ramps, the turn
   to a 6-bit panel, and the rule that a draw changes no state.
2. Scripts (q2ui --script): input on the program's own clock, a frame every 1/60 s whatever this machine's speed, with
   the list's motion traced a line a frame and the canvas saved where the script says. They hold the motion the owner
   asked for: a tick is one row and the list glides there, a tick mid-glide carries on from where the list is, the
   platter is never cut by the pinned row or the foot's fade, and what is on the platter moves with it and further.
3. A live run against a file standing in for the panel and FIFOs standing in for the wheel and the buttons: ticks as
   the wheel's own events, the frame read back from the file, no frame and no wake at rest, Return ends it.

Without the build's inputs (stb_truetype, stb_image and SQLite's header in the third-party cache, the host's SQLite)
it prints why it is skipped and passes.
"""
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import sqlite3
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
W, H = 375, 320
BAR_H, ROW_H, ROWS = 58, 32, 7
EV_SYN, EV_KEY, EV_ABS, ABS_X, ABS_PRESSURE, BTN_TOUCH, KEY_RETURN, KEY_CENTRE = 0, 1, 3, 0, 0x18, 0x14a, 103, 116
TICK_UNITS = 28


def event(kind, code, value):
    return struct.pack('llHHi', 0, 0, kind, code, value)  # this machine's struct input_event


def folder(work, n):
    """A folder with a library of n made-up songs, the faces and an empty panel file."""
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    fixture.make(n, d/'library.db')
    (d/'fonts').mkdir()
    shutil.copy(ROOT/'assets/fonts/Inter-Regular.ttf', d/'fonts/inter.ttf')
    shutil.copy(ROOT/'assets/fonts/Inter-Medium.ttf', d/'fonts/inter-medium.ttf')
    (d/'fb').write_bytes(bytes(320 * 375 * 2 * 4))
    return d


def base_args(binary, d):
    # Songs first, as these checks were written for it (a later --page wins); Home is the program's own first page
    # 8 bits to the panel file, so its pixels are the canvas's own: the Q2's default dithers them to 6
    return [str(binary), '--page', 'songs', '--panel-bits', '8', '--db', d/'library.db', '--catalogue', '', '--fb', d/'fb', '--fonts', d/'fonts', '--icons', d,
            '--stats', d/'stats.json', '--state', d/'state', '--cmdq', d/'cmdq', '--playlist', d/'list', '--backlight', d/'backlight',
            '--now', d/'now', '--cover', d/'cover.jpg', '--ask', d/'ask', '--power', d/'power', '--wifi', d/'wifi',
            '--wireless', d/'wireless', '--told', d/'told', '--networks', d/'networks', '--bt-devices', d/'bt', '--config', d/'config.ini',
            '--library-order', d/'library-order', '--notices', d/'ui.sock', '--apps', d/'apps', '--app-card', d]


class Picture:
    """A canvas a script saved (a binary PPM)."""

    def __init__(self, path):
        data = pathlib.Path(path).read_bytes()
        head = data.split(b'\n', 3)
        assert head[0] == b'P6' and head[1] == b'%d %d' % (W, H), head[:3]
        self.rgb = head[3]

    def at(self, x, y):
        return tuple(self.rgb[(y * W + x) * 3:(y * W + x) * 3 + 3])

    def blue(self, x, y):
        """Whether (x, y) is the platter's blue, whatever its gleam."""
        r, g, b = self.at(x, y)
        return b > 150 and b - r > 60

    def ink_y(self, x0, x1, y0, y1):
        """The middle row, by weight, of the white ink (text on the platter) in a box, and its weight."""
        total = moment = 0
        for y in range(y0, y1):
            for x in range(x0, x1):
                r, g, b = self.at(x, y)
                w = max(0, min(r, g) - 150)  # white text is bright in red and green, the platter and its gleam are not
                total += w
                moment += w * (y + 0.5)
        return (moment / total if total else 0), total

    def blue_y(self, x0, x1, y0, y1):
        """The middle row of the platter's blue in a box."""
        rows = [y + 0.5 for y in range(y0, y1) for x in range(x0, x1) if self.blue(x, y)]
        return sum(rows) / len(rows) if rows else 0


# demo's file for what plays (patch/coda_rc_shim.c cr_now): state is 0 stopped, 1 playing, 2 paused (now_source.c now_status)
NOW = 'seq=3\nstate=1\npos=14000\nlen=180\nid=5\nvolume=40\nsource=library\ntitle=A Made-Up Song\nartist=Its Artist\nalbum=Its Album\n'


def script(binary, work, n, lines, *options, now=None, code=0, queue=False, ctl=False, clicks=False):
    """q2ui run over a script; the trace (a dict a frame), the pictures it saved by name, and the stats. now is what
    plays (the text of demo's file for it), code the exit code expected (None: any), and with queue the player's queue is a FIFO
    whose lines come back as a fourth value with the list file's ids. With ctl, q2ui is also given the write end of
    a pipe as demo gives it one for settings (patch/q2ui_gate.c, --ctl FD), and the lines it wrote come back fifth: its
    settings, or with clicks its key clicks ("click 0|1") alone."""
    d = folder(work, n)
    if now:
        (d/'now').write_text(now)
    fifo = None
    if queue:
        os.mkfifo(d/'cmdq')
        fifo = os.open(d/'cmdq', os.O_RDWR | os.O_NONBLOCK)
    text = '\n'.join(lines).replace('@', str(d) + '/') + '\n'
    (d/'script').write_text(text)
    pipe = os.pipe() if ctl else None
    given = ['--ctl', str(pipe[1])] if pipe else []
    try:
        done = subprocess.run(base_args(binary, d) + ['--script', d/'script', '--trace', d/'trace', '--clock', '12:00', *given,
                                                      *options], timeout=120, pass_fds=pipe[1:] if pipe else ())
    finally:
        if pipe:
            os.close(pipe[1])
    settings = os.read(pipe[0], 4096).decode().splitlines() if pipe else []
    settings = [s for s in settings if s.startswith('click ') == clicks]
    if pipe:
        os.close(pipe[0])
    if code is not None and done.returncode != code:
        raise RuntimeError(f'q2ui --script exited {done.returncode}, not {code}')
    trace = []
    for line in (d/'trace').read_text().splitlines():
        if 'sel=' not in line:
            continue
        row = dict(part.split('=') for part in line.split())
        trace.append({k: float(v) for k, v in row.items()})
    pictures = {p.stem: Picture(p) for p in d.glob('*.ppm')}
    stats = json.loads((d/'stats.json').read_text()) if (d/'stats.json').exists() else {}
    if fifo is None:
        return trace, pictures, stats
    try:
        sent = os.read(fifo, 4096).decode().splitlines()
    except BlockingIOError:
        sent = []
    os.close(fifo)
    ids = (d/'list').read_text().split() if (d/'list').exists() else []
    if ctl:
        return trace, pictures, stats, (sent, ids, d), settings
    return trace, pictures, stats, (sent, ids, d)


class Run:
    """One live q2ui over a library of n songs, with its panel file, inputs and stats in a folder of its own."""

    def __init__(self, binary, work, n, *options, at=None, fds=()):
        self.dir = at or folder(work, n)
        if not at:
            (self.dir/'input').mkdir()
            for k in (1, 3):
                os.mkfifo(self.dir/'input'/f'event{k}')
            os.mkfifo(self.dir/'cmdq')
        self.queue = os.open(self.dir/'cmdq', os.O_RDWR | os.O_NONBLOCK)  # demo's end of the player's queue
        self.proc = subprocess.Popen(base_args(binary, self.dir) + ['--input', self.dir/'input', *options], pass_fds=fds)
        self.wheel = open(self.dir/'input/event3', 'wb', buffering=0)
        self.keys = open(self.dir/'input/event1', 'wb', buffering=0)
        self.x = 100
        time.sleep(0.4)  # q2ui drops what was pressed before it had the screen: the finger comes down once it runs
        self.wheel.write(event(EV_ABS, ABS_X, self.x) + event(EV_ABS, ABS_PRESSURE, 1) + event(EV_KEY, BTN_TOUCH, 1) +
                         event(EV_SYN, 0, 0))
        time.sleep(0.1)

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
        time.sleep(1.5)
        return self.stats()

    def quiet_second(self):
        """Wakes and frames over one second clear of the minute's turn, when q2ui at rest wakes to draw the clock: a
        wake from its wait and another as the frame waits for the panel (its wait is in whole seconds, so it can come
        up to a second after :00). The second starts at :01.5 at the earliest and ends before :58."""
        at = time.time() % 60
        if at > 57 or at < 1.5:
            time.sleep((61.5 - at) % 60)
        frames, woke = self.stats()['frames'], self.wakes()
        time.sleep(1.0)
        return self.wakes() - woke, self.stats()['frames'] - frames

    def picture(self):
        return shot.page(self.dir/'fb', self.stats()['page'])

    def hold(self, code, seconds=0.9):
        """code held past q2ui's hold (KEY_HOLD_MS, 600 ms), then let go."""
        self.keys.write(event(EV_KEY, code, 1) + event(EV_SYN, 0, 0))
        time.sleep(seconds)
        self.keys.write(event(EV_KEY, code, 0) + event(EV_SYN, 0, 0))

    def key(self, code):
        self.keys.write(event(EV_KEY, code, 1) + event(EV_SYN, 0, 0) + event(EV_KEY, code, 0) + event(EV_SYN, 0, 0))

    def sent(self):
        """The commands q2ui has sent the player since the last call, a line each."""
        try:
            return os.read(self.queue, 4096).decode().splitlines()
        except BlockingIOError:
            return []

    def end(self):
        self.key(KEY_RETURN)
        code = self.proc.wait(timeout=5)
        os.close(self.queue)
        return code, self.stats()


def bar_rows(picture):
    """The list rows whose middle holds the accent, by the pixel at x 200, 3 px into the row."""
    return [k for k in range(ROWS) if shot.pixel(picture, 200, BAR_H + k * ROW_H + 3) == ACCENT]


def png(path, w, h, rgb):
    """A w x h picture of one colour, as a PNG (no Pillow here)."""
    import zlib

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    rows = b''.join(b'\0' + bytes(rgb) * w for _ in range(h))
    pathlib.Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                                   chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))


def check(what, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + what + (f': {detail}' if detail and not ok else ''))
    return ok


def rising(values):
    return all(b >= a for a, b in zip(values, values[1:]))


def scripts(binary, work):
    good = True
    # Songs' rows: 0 is Shuffle, pinned; the list opens on the first song, row 1, and six songs show, so the
    # selection reaches row 6 before the list moves
    to_foot = ['rest'] + ['tick 1', 'rest'] * 5

    # a tick is one row, and the list glides to it
    trace, pics, _ = script(binary, work, 300, to_foot + ['shot @foot.ppm', 'tick 1', 'run 600', 'shot @glided.ppm'])
    at = next((i for i, t in enumerate(trace) if t['sel'] == 7), None)
    if at is None:
        return check('the seventh tick selects the seventh song', False, str(sorted({t['sel'] for t in trace})))
    glide = [t['scroll'] for t in trace[at:]]
    moving = [s for s in glide if 0 < s < ROW_H]
    steps = [b - a for a, b in zip([0] + glide, glide)]
    good &= check('five ticks reach the sixth song with the list still', trace[at - 1]['sel'] == 6 and trace[at - 1]['scroll'] == 0,
                  str(trace[at - 1]))
    good &= check('the seventh moves the selection one row at once', trace[at]['sel'] == 7 and all(t['sel'] == 7 for t in trace[at:]))
    good &= check('and the list glides a row: 8 frames or more between, none back, none a jump',
                  len(moving) >= 8 and rising(glide) and max(steps) < 12 and glide[-1] == ROW_H,
                  f'{len(moving)} frames between, largest step {max(steps):.2f}, ends at {glide[-1]}')
    good &= check('at places between pixels', sum(1 for s in moving if abs(s - round(s)) > 0.05) >= 6, str(moving))
    good &= check('the platter stays on the last row while the list moves under it',
                  all(abs(t['bar'] - (BAR_H + 6 * ROW_H)) < 0.01 for t in trace[at:]), str([t['bar'] for t in trace[at:at + 5]]))
    good &= check('the glided list shows the next song on the platter', pics['glided'].blue(200, BAR_H + 6 * ROW_H + 3)
                  and pics['glided'].rgb != pics['foot'].rgb)

    # a tick mid-glide goes on from where the list is
    trace, _, _ = script(binary, work, 300, to_foot + ['tick 1', 'run 50', 'tick 1', 'run 700'])
    at = next(i for i, t in enumerate(trace) if t['sel'] == 8)
    glide = [t['scroll'] for t in trace]
    before, after = glide[at - 1] - glide[at - 2], glide[at + 1] - glide[at]
    good &= check('a second tick lands mid-glide', 0 < glide[at - 1] < ROW_H, f'{glide[at - 1]:.2f}')
    steps = [b - a for a, b in zip(glide, glide[1:])]
    good &= check('the list goes on from there to two rows, never back and never by a jump',
                  rising(glide) and glide[-1] == 2 * ROW_H and max(steps) < 12, f'ends at {glide[-1]}, largest step {max(steps):.2f}')
    good &= check('at the speed it had or more', after >= before * 0.8 and after > 0, f'{before:.2f} px a frame, then {after:.2f}')

    # a fast spin keeps gliding
    trace, _, stats = script(binary, work, 300, ['rest', 'tick 40', 'run 700'])
    glide = [t['scroll'] for t in trace if t['scroll'] > 0]
    between = sum(1 for s in glide[:40] if abs(s / ROW_H - round(s / ROW_H)) > 0.02)
    good &= check('a spin of 40 ticks never steps back', rising(glide))
    good &= check('and is between rows on most of its frames', between >= 30, f'{between} of the first 40')
    good &= check('and ends on a whole row', glide[-1] % ROW_H == 0, str(glide[-1]))

    # spun up into the pinned row at a pace where the wheel's acceleration moves two rows a tick and more (a tick every
    # 50 ms), from different rows, so that some last steps pass over the first song: the songs come to rest with their
    # first row under the pinned one, never a row higher with a song in Shuffle's place
    ends, jumped = [], False
    for start in range(8, 14):
        trace, _, _ = script(binary, work, 300, ['rest'] + ['tick 1', 'rest'] * (start - 1) + ['tick -1', 'run 34'] * 14 + ['rest'])
        sels = [t['sel'] for t in trace]
        ends.append((start, trace[-1]['sel'], trace[-1]['scroll']))
        jumped |= 1 not in sels[sels.index(start):]
    good &= check('some spins up reach the pinned row without stopping on the first song', jumped)
    good &= check('and every one leaves the songs at their top', all(sel == 0 and scroll == 0 for _, sel, scroll in ends),
                  str([e for e in ends if e[1:] != (0, 0)]))

    # the focus layer is above the pinned row and the foot's fade
    lines = ['rest', 'shot @rest.ppm', 'tug -1', 'run 500', 'shot @up.ppm', 'tug 0', 'rest'] + \
            ['tick 1', 'rest'] * 5 + ['tug 1', 'run 500', 'shot @down.ppm']
    trace, pics, _ = script(binary, work, 300, lines)
    good &= check('at rest the platter keeps to its row', not pics['rest'].blue(200, BAR_H + ROW_H - 3))
    good &= check('leaning up from the first song it is drawn over the pinned row', pics['up'].blue(200, BAR_H + ROW_H - 3),
                  str(pics['up'].at(200, BAR_H + ROW_H - 3)))
    good &= check('and the pinned row still shows above it', pics['up'].at(40, BAR_H + 14) != (0, 0, 0) or pics['up'].at(60, BAR_H + 14) != (0, 0, 0) or
                  any(pics['up'].at(x, BAR_H + 14) != (0, 0, 0) for x in range(26, 100)))
    foot = BAR_H + 7 * ROW_H
    good &= check('leaning down from the last row it is drawn over the fade, as bright as ever',
                  pics['down'].blue(200, foot + 1) and pics['down'].at(355, foot + 1)[2] >= 0xd0, str(pics['down'].at(355, foot + 1)))

    # what is on the platter moves with it, and further
    y1 = BAR_H + ROW_H
    rest_ink, weight = pics['rest'].ink_y(26, 200, y1 - 8, y1 + ROW_H + 8)
    up_ink, _ = pics['up'].ink_y(26, 200, y1 - 8, y1 + ROW_H + 8)
    rest_bar, up_bar = pics['rest'].blue_y(350, 358, y1 - 10, y1 + ROW_H + 10), pics['up'].blue_y(350, 358, y1 - 10, y1 + ROW_H + 10)
    good &= check('the platter leans up about 3 px', 2.2 < rest_bar - up_bar < 3.8, f'{rest_bar - up_bar:.2f}')
    good &= check('its text moves with it, further than it does', weight > 1000 and rest_ink - up_ink > (rest_bar - up_bar) * 1.2,
                  f'text {rest_ink - up_ink:.2f} px, platter {rest_bar - up_bar:.2f} px')
    # below the platter's shadow, which reaches 10 px into the next row
    other = [p.ink_y(26, 200, y1 + ROW_H + 12, y1 + 2 * ROW_H) for p in (pics['rest'], pics['up'])]
    good &= check('the row under it stays where it is', other[0] == other[1] and other[0][1] > 1000, str(other))
    lean = [t['lean'] for t in trace]
    good &= check('let go, the lean springs back to nothing', min(lean) < -2.9 and any(abs(v) > 0 for v in lean) and lean[-1] > 2.9)

    # Reduce Motion: no lean, the glide stays; --motion 0: every move is at its end at once
    trace, _, _ = script(binary, work, 300, to_foot + ['tug 1', 'run 300', 'tick 1', 'run 600'], '--reduce-motion')
    glide = [t['scroll'] for t in trace]
    good &= check('Reduce Motion leans nothing and still glides', all(t['lean'] == 0 for t in trace) and len([s for s in glide if 0 < s < ROW_H]) >= 8)
    # ... and demo's setting for it, [IPOD] REDUCEMOTION in config.ini (ui/config.c), the same
    ini = pathlib.Path(tempfile.mkdtemp(dir=work))/'config.ini'
    ini.write_text('[SYSSET]\nVOLUME=20\n[IPOD]\nACCENT=0\nREDUCEMOTION=1\n')
    trace, _, _ = script(binary, work, 300, to_foot + ['tug 1', 'run 300', 'tick 1', 'run 600'], '--config', ini)
    good &= check("demo's Reduce Motion setting leans nothing", all(t['lean'] == 0 for t in trace))
    trace, _, _ = script(binary, work, 300, to_foot + ['tick 1', 'run 100'], '--motion', '0')
    good &= check('--motion 0 is at its end at once', all(t['scroll'] in (0, ROW_H) for t in trace) and trace[-1]['scroll'] == ROW_H)

    # at rest nothing is drawn
    trace, _, _ = script(binary, work, 300, ['rest', 'tick 2', 'rest', 'run 3000'])
    tail = trace[-170:]
    good &= check('a list at rest draws no frame for three seconds', not any(t['shown'] for t in tail), f"{sum(t['shown'] for t in tail)} shown")

    # the toolkit's examples: every kind of row, and a card of layers
    lines = ['rest', 'shot @open.ppm'] + ['tick 1', 'rest'] * 3 + ['shot @on.ppm', 'key centre', 'run 60', 'shot @sliding.ppm', 'rest',
             'shot @off.ppm', 'key centre', 'rest', 'shot @back.ppm'] + ['tick 1', 'rest'] * 3
    trace, pics, _ = script(binary, work, 1, lines, '--page', 'gallery')
    good &= check('a list opens past its header, on the first row that can be selected', trace[0]['sel'] == 1, str(trace[0]['sel']))
    good &= check('the wheel passes over a greyed row and a header', [t['sel'] for t in trace][-1] == 9, str(trace[-1]['sel']))
    knob = [t['knob'] for t in trace]
    good &= check('Centre flips a switch and flips it back', pics['on'].rgb != pics['off'].rgb and pics['back'].rgb == pics['on'].rgb)
    good &= check('its knob slides there over 5 frames or more',
                  sum(1 for k in knob if 0.02 < k < 0.98) >= 10 and pics['sliding'].rgb not in (pics['on'].rgb, pics['off'].rgb),
                  f'{sum(1 for k in knob if 0.02 < k < 0.98)} frames between its ends in two flips')
    good &= check('a chevron stands at the right column', pics['on'].at(342, BAR_H + ROW_H + 16) != (0, 0, 0) and pics['open'].at(342, BAR_H + ROW_H + 16) != ACCENT)
    # an inline slider row (ROW_SLIDER): Centre grabs it and the wheel then sets it (clockwise, down a list, is more, as
    # demo's sliders: patch/inline_slider.c, KEY_NEXT), Centre or Return sets it down
    lines = ['rest', 'tick 12', 'rest', 'key centre', 'run 50', 'tick 2', 'tick -1', 'run 50', 'shot @grabbed.ppm', 'key centre', 'run 50',
             'tick -1', 'rest', 'tick 1', 'rest', 'key centre', 'run 50', 'tick 1', 'key return', 'rest', 'rows @rows.txt', 'tick -1',
             'run 50']
    trace, pics, _, (_, _, d) = script(binary, work, 1, lines, '--page', 'gallery', queue=True)
    level = [r for r in (d/'rows.txt').read_text().splitlines() if r.startswith('Level')] if (d/'rows.txt').exists() else []
    grabs = [t['grab'] for t in trace]
    good &= check('Centre grabs the slider (the wheel then sets it: clockwise two, back one) and sets it down, the wheel moving the '
                  'selection again; grabbed once more, clockwise one, Return sets it down with the page kept', level == ['Level\t42%'] and 12 in grabs and grabs[-1] == -1 and trace[-1]['sel'] == 11,
                  f'{level} {trace[-1]}')
    g = pics['grabbed']
    rows = [y for y in range(BAR_H, H) if g.at(205, y) == (255, 255, 255) and g.at(270, y) == (0x7d, 0xa2, 0xea)]
    good &= check("the grabbed row's track on the bar is #7DA2EA, filled white to 41%", len(rows) >= 2, str(rows))
    _, pics, _ = script(binary, work, 1, ['rest', 'shot @still.ppm', 'tug 1', 'run 900', 'shot @down.ppm'], '--page', 'card')

    def badge(p):  # the white disc in front of the cover
        rows = [y for y in range(60, 260) for x in range(150, 226, 4) if min(p.at(x, y)) > 235]
        return sum(rows) / len(rows) if rows else 0

    def face(p):  # the cover's top edge down the screen's middle column but one
        return next((y for y in range(40, 200) if max(p.at(130, y)) > 90), 0)
    front, card = badge(pics['down']) - badge(pics['still']), face(pics['down']) - face(pics['still'])
    good &= check('a pushed card moves, and the layer in front of it moves further', 0 <= card <= 6 and front > card + 4,
                  f'card {card} px, front layer {front:.1f} px')

    # Light Mode: paper under the same list
    _, pics, _ = script(binary, work, 30, ['rest', 'shot @light.ppm'], '--light')
    good &= check('Light Mode is black on white, the platter still blue', pics['light'].at(5, 200) == (255, 255, 255) and pics['light'].blue(200, BAR_H + ROW_H + 3))
    foot = [pics['light'].at(5, y) for y in (290, 300, 310)]
    good &= check("in Light Mode the list's foot fades to the paper, not to black", all(p == (255, 255, 255) for p in foot), str(foot))
    ini = pathlib.Path(tempfile.mkdtemp(dir=work))/'config.ini'
    ini.write_text('[IPOD]\nLIGHT=1\n')
    _, pics, _ = script(binary, work, 30, ['rest', 'shot @light.ppm'], '--config', ini)
    good &= check("demo's Light Mode setting, [IPOD] LIGHT=1 in config.ini, is Light Mode", pics['light'].at(5, 200) == (255, 255, 255))
    ini.write_text('[IPOD]\nLIGHT=0\n[OTHER]\nLIGHT=1\n')
    _, pics, _ = script(binary, work, 30, ['rest', 'shot @light.ppm'], '--config', ini)
    good &= check('another section of the same key is not it', pics['light'].at(5, 200) == (0, 0, 0))
    return good


def pages(binary, work):
    """Home, the Library's lists and Now Playing (docs/ui-rewrite.md#parity), by script."""
    good = True
    home = ('--page', 'home')
    # Home: the list at the left, the pane at the right; its first row is Playing while something is
    trace, pics, _ = script(binary, work, 60, ['rest', 'shot @idle.ppm', 'tick 9', 'rest'], *home)
    idle_rows = trace[-1]['sel'] + 1
    trace, pics2, _ = script(binary, work, 60, ['rest', 'shot @playing.ppm', 'tick 9', 'rest'], *home, now=NOW)
    good &= check('Home has one row more while something plays', (idle_rows, trace[-1]['sel'] + 1) == (8, 9), f"{idle_rows} and {trace[-1]['sel'] + 1}")
    good &= check("Home's platter keeps to the list's side and the pane is drawn beside it",
                  pics['idle'].blue(100, 40 + 3) and not pics['idle'].blue(250, 40 + 3) and pics['idle'].at(280, 160) != (0, 0, 0))
    p = pics['idle']
    blue = [x for x in range(W) if p.blue(x, 56)]
    good &= check("Home's rows run to x 204 as demo's (the bar 16 to 188 at rest, raised 2%) and the art from x 196 "
                  '(docs/oled-design.md, Screens: Home)', blue and 13 <= blue[0] <= 16 and 187 <= blue[-1] <= 190 and
                  p.at(194, 160) == (0, 0, 0) and p.at(198, 160) != (0, 0, 0), f'{blue[:1]} {blue[-1:]} {p.at(194, 160)} {p.at(198, 160)}')
    good &= check('the two Homes differ where the first row is', pics['idle'].rgb != pics2['playing'].rgb)
    _, paused, _ = script(binary, work, 60, ['rest', 'shot @paused.ppm', 'tick 9', 'rest'], *home, now=NOW.replace('state=1', 'state=2'))
    _, stopped, _ = script(binary, work, 60, ['rest', 'shot @stopped.ppm'], *home, now=NOW.replace('state=1', 'state=0'))
    good &= check('a paused song keeps the row, reading Paused, and a stopped one has none',
                  paused['paused'].rgb not in (pics['idle'].rgb, pics2['playing'].rgb) and stopped['stopped'].rgb == pics['idle'].rgb)
    # Centre on Playing is Now Playing: the bar filled to the song's place, the times beside it
    _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'rest', 'shot @now.ppm'], *home, now=NOW)
    fill, track = pics['now'].at(36, 231), pics['now'].at(200, 231)
    good &= check("Now Playing's bar is filled to 14 s of 180", fill == ACCENT and track == (0x33, 0x33, 0x33), f'{fill} {track}')
    _, later, _ = script(binary, work, 60, ['rest', 'key centre', 'rest', 'shot @now.ppm'], *home, now=NOW.replace('pos=14000', 'pos=120000'))
    good &= check('and further on later in the song', later['now'].at(200, 231) == ACCENT and later['now'].rgb != pics['now'].rgb)
    _, _, _, (sent, _, _) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick 1', 'run 100'], *home, now=NOW, queue=True)
    good &= check("there a tick clockwise (down a list) is one step louder, as demo's wheel "
                  '(patch/playback.c: x falling is KEY_NEXT; patch/volume.c vol_next: KEY_NEXT is up)',
                  [line.split(' ', 1)[1] for line in sent] == ['volume 41'], str(sent))
    good &= now_cycle(binary, work, home)
    # a title too long for its column rests, slides to its end, and comes back
    # a picture every quarter second for ten: at rest 2.75 s, sliding, held 1.5 s at its end, then back at rest
    long = NOW.replace('title=A Made-Up Song', 'title=A Made-Up Song Again X').replace('state=1', 'state=2')
    lines = ['rest', 'key centre', 'run 500']  # no rest after: a title that slides never does
    for k in range(40):
        lines += [f'shot @m{k:02d}.ppm', 'run 250']
    _, pics, _ = script(binary, work, 60, lines, *home, now=long)
    title = lambda p: bytes(v for y in range(64, 92) for x in range(192, 352) for v in p.at(x, y))  # noqa: E731
    edge = lambda p: max(max(p.at(x, y)) for y in range(66, 90) for x in range(192, 196))  # noqa: E731
    seen = [title(pics[f'm{k:02d}']) for k in range(40)]
    still = next((k for k in range(40) if seen[k] != seen[0]), 40)       # the first picture that has moved
    held = next((k for k in range(still, 37) if seen[k] == seen[k + 1] == seen[k + 2]), None)  # three alike after it
    back = held is not None and any(seen[k] == seen[0] for k in range(held, 40))
    good &= check('a long title is still for its first two seconds and more, slides, holds at its end, and comes back',
                  7 <= still <= 10 and held is not None and seen[held] != seen[0] and back, f'moved at shot {still}, held at {held}, back {back}')
    good &= check("at rest its start is whole, and at its end it fades out at the column's left",
                  held is not None and edge(pics['m00']) > 200 and edge(pics[f'm{held:02d}']) < 120,
                  f"{edge(pics['m00'])} {edge(pics[f'm{held:02d}']) if held is not None else None}")
    # the modes at the foot are marks of what is set, in the theme's own glyphs: made-up ones here, a colour each
    def glyphs(d):
        for name, rgb in (('mode_shuffle_d', (90, 90, 90)), ('mode_shuffle_on', (0, 0, 250)), ('mode_repeat_d', (90, 90, 90)),
                          ('mode_repeat_on', (0, 0, 250)), ('mode_repeat1_on', (0, 250, 0)), ('mode_radio_d', (90, 90, 90)),
                          ('mode_queue_d', (90, 90, 90)), ('np_fav_d', (250, 0, 0)), ('np_unfav_d', (90, 90, 90)),
                          ('np_more_d', (0, 250, 250)), ('vol_lo_d', (250, 250, 0)), ('vol_hi_d', (250, 0, 250))):
            png(d/f'oled_{name}.png', 16, 16, rgb)
    icons = pathlib.Path(tempfile.mkdtemp(dir=work))
    glyphs(icons)
    marks, dots = {}, {}
    # demo's file says repeat 0 off, 1 one, 2 all (patch/coda_rc_shim.c cr_modes)
    for what, text in (('off', NOW.replace('id=5', 'id=5\nhearts=5\nliked=0')),
                       ('one', NOW.replace('id=5', 'id=5\nshuffle=1\nrepeat=1\nliked=1')),
                       ('all', NOW.replace('id=5', 'id=5\nrepeat=2'))):
        _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'run 600', 'shot @np.ppm'], *home, '--icons', icons, now=text)
        marks[what] = [pics['np'].at(64, 286), pics['np'].at(228, 286), pics['np'].at(200, 160)]
        # the on dot: 4 px, its centre 15 px under the glyph's (patch/np_bar.c NP_DOT, NP_DOT_DY), in the accent
        dots[what] = [pics['np'].at(x, y) for x in (64, 228) for y in (300, 301)] + [pics['np'].at(x, 304) for x in (64, 228)]
    good &= check("Now Playing's shuffle, repeat and heart are drawn by what is set: Repeat One its own glyph, All the lit one, "
                  "the heart full by the song's like (liked=1) and empty for five stars without it",
                  marks['off'] == [(90, 90, 90)] * 3 and marks['one'] == [(0, 0, 250), (0, 250, 0), (250, 0, 0)]
                  and marks['all'][1] == (0, 0, 250), str(marks))
    good &= check('a lit shuffle or repeat has the accent dot under it, an unlit one none',
                  dots['off'] == [(0, 0, 0)] * 6 and dots['one'] == [ACCENT] * 4 + [(0, 0, 0)] * 2
                  and dots['all'] == [(0, 0, 0)] * 2 + [ACCENT] * 2 + [(0, 0, 0)] * 2, str(dots))
    # set from elsewhere while the page is open (the car, the phone, demo's own buttons): the row follows
    (icons/'lit').write_text(NOW.replace('id=5', 'id=5\nshuffle=1\nrepeat=2'))
    _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'run 600', f'plays {icons}/lit', 'run 300', 'shot @np.ppm'],
                        *home, '--icons', icons, now=NOW)
    later = [pics['np'].at(x, y) for y in (286, 301) for x in (64, 228)]
    good &= check('shuffle and repeat turned on while Now Playing is open light there, each with its dot',
                  later == [(0, 0, 250)] * 2 + [ACCENT] * 2, str(later))
    # More (demo's dots at the right of the art's foot, tools/ipod.py img_more): Centre held opens demo's More page
    _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'run 600', 'shot @np.ppm'], *home, '--icons', icons, now=NOW)
    good &= check("Now Playing's More is drawn at the right of the art's foot", pics['np'].at(340, 203) == (0, 250, 250),
                  str(pics['np'].at(340, 203)))
    _, _, _, (sent, _, d) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'hold centre', 'run 100'], *home, now=NOW, code=None, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check("Centre held on Now Playing opens demo's More, and plays or pauses nothing",
                  asked == 'playermore_page\n' and sent == [], f'{asked!r} {sent}')
    # a tick shows the volume's level in the bar's place, white, and it gives the bar back
    _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick -1', 'run 200', 'shot @volume.ppm', 'run 2000', 'shot @after.ppm'], *home, now=NOW)
    level, bar = pics['volume'].at(80, 231), pics['after'].at(36, 231)
    good &= check('a tick on Now Playing shows the volume in the bar, then the bar comes back',
                  level == (255, 255, 255) and pics['volume'].at(36, 231) != ACCENT and bar == ACCENT, f'{level} {bar}')
    # demo's speakers at the bar's ends while it shows the volume (patch/volume.c vol_paint): 16 px, centred 8 px in from
    # each end of the progress bar (VOL_SPK_X), the track VOL_SPK_INSET (24 px) in from each end between them
    _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick 1', 'run 200', 'shot @volume.ppm'], *home,
                        '--icons', icons, now=NOW)
    v = pics['volume']
    ends = [v.at(x, 231) for x in (25, 32, 39, 336, 343, 350)]
    gaps = [v.at(x, 231) for x in (41, 47, 328, 334)]
    good &= check("the volume in the bar's place has demo's quiet and loud speakers at its ends, the track 24 px in",
                  ends == [(250, 250, 0)] * 3 + [(250, 0, 250)] * 3 and gaps == [(0, 0, 0)] * 4
                  and v.at(48, 231) != (0, 0, 0) and v.at(326, 231) != (0, 0, 0), f'{ends} {gaps}')
    # Library > Artists > the first artist's songs > Centre on the second: that artist's songs are the queue
    walk = ['rest', 'key centre', 'rest', 'tick 2', 'rest', 'key centre', 'rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'run 200']
    _, _, _, (sent, ids, d) = script(binary, work, 60, walk, *home, queue=True)
    db = sqlite3.connect(d/'library.db')
    artist = db.execute('select songer from songtable order by songer collate nocase limit 1').fetchone()[0]
    want = [str(r[0]) for r in db.execute('select id from songtable where songer = ? order by name collate nocase, id', (artist,))]
    good &= check("Centre on an artist's second song plays that artist's songs from it",
                  len(sent) == 1 and sent[0].split()[1:] == ['play', '1', '0', str(len(want))] and ids == want and len(want) > 1, f'{sent} {len(ids)} ids for {len(want)}')
    # Library > Albums > an album whose tracks are not in their titles' order: its tracks in track order
    probe = folder(work, 60)  # the same made-up library every time
    db = sqlite3.connect(probe/'library.db')
    albums = [r[0] for r in db.execute('select distinct album from songtable order by album collate nocase')]
    by = {o: {a: [str(r[0]) for r in db.execute(f'select id from songtable where album = ? order by {o}', (a,))] for a in albums}
          for o in ('track, name collate nocase, id', 'name collate nocase, id')}
    k = next((i for i, a in enumerate(albums) if by['track, name collate nocase, id'][a] != by['name collate nocase, id'][a]), None)
    if k is None:
        return check("the made-up library has an album whose tracks are not in title order", False)
    walk = ['rest', 'key centre', 'rest', 'tick 3', 'rest', 'key centre', 'rest'] + ['tick 1', 'rest'] * k + ['key centre', 'rest', 'key centre', 'run 200']
    _, _, _, (sent, ids, d) = script(binary, work, 60, walk, *home, queue=True)
    want = by['track, name collate nocase, id'][albums[k]]
    good &= check("an album's songs are its tracks in order", len(sent) == 1 and ids == want, f'{ids} for {want}')
    # Library > Albums: two-line rows, each led by its cover from Cover Flow's thumbnails, else the placeholder
    good &= album_covers(binary, work, home)
    good &= now_covers(binary, work, home)
    good &= playlists(binary, work, home)
    good &= control_center(binary, work, home)
    # Centre on a song opens Now Playing over the list, as demo's playing_page comes up when a song is chosen
    tops = []
    for walk in (['rest', 'tick 2', 'rest', 'key centre', 'rest'],  # Songs (the page these scripts open on)
                 ['rest', 'key centre', 'rest', 'tick 2', 'rest', 'key centre', 'rest', 'key centre', 'rest', 'key centre', 'rest']):
        _, _, stats, (sent, _, _) = script(binary, work, 60, walk, *(() if len(walk) == 5 else home), queue=True)
        tops.append((stats['top'], len(sent)))
    good &= check("Centre on a song in Songs and in an album plays it and opens Now Playing over the list",
                  tops == [('now', 1), ('now', 1)], str(tops))
    # and Return from it gives the list back, where it was
    trace, _, stats, _ = script(binary, work, 60, ['rest', 'tick 2', 'rest', 'key centre', 'rest', 'key return', 'rest'], queue=True)
    good &= check('Return from that Now Playing is the list again on the same row', stats['top'] != 'now' and trace[-1]['sel'] == 3,
                  f"{stats['top']} {trace[-1]['sel']}")
    good &= control_center_sheet(binary, work, home)
    # a row whose page is demo's: the page is named for demo and q2ui ends with the code that says so
    _, _, _, (sent, _, d) = script(binary, work, 60, ['rest', 'tick 1', 'rest', 'key centre', 'run 100'], *home, code=3, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check('Centre on Radio asks demo for its page and ends with code 3', asked == 'tuner_page\n' and sent == [], repr(asked))
    _, _, _, (sent, _, d) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick 5', 'rest', 'key centre', 'run 100'], *home, code=3, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check("and Ratings, in the Library, for demo's Ratings", asked == 'ratings_page\n', repr(asked))
    _, _, _, (_, _, d) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'rows @rows.txt'], *home, queue=True)
    rows = (d/'rows.txt').read_text().split() if (d/'rows.txt').exists() else []
    good &= check("the Library's rows in demo's default order (patch/library_edit.c lr_default), for the ones q2ui has",
                  rows == ['Songs', 'Playlists', 'Artists', 'Albums', 'Years', 'Ratings', 'Most', 'Played'], str(rows))
    # the user's own order and hidden rows from demo's Edit Library (patch/library_edit.c LR_FILE: the rows' letters in
    # order, each led by + or - once shown or hidden; a row it does not name goes in after the one before it by default).
    # Most Played moved first, Playlists hidden, Songs moved after Albums: Years and Ratings follow Albums, Songs last
    order = pathlib.Path(tempfile.mkdtemp(dir=work))/'ringnav-library-order'
    order.write_bytes(b'm-pals'.ljust(64, b'\0'))
    _, _, _, (_, _, d) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'rows @rows.txt'], *home, '--library-order', order,
                                queue=True)
    rows = (d/'rows.txt').read_text().split() if (d/'rows.txt').exists() else []
    good &= check("the Library's rows in the user's own order from Edit Library, a row they hid left out",
                  rows == ['Most', 'Played', 'Artists', 'Albums', 'Years', 'Ratings', 'Songs'], str(rows))
    _, _, _, (_, _, d) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick 5', 'rest', 'key centre', 'rest',
                                                   'rows @songs.txt'], *home, '--library-order', order, queue=True)
    rows = (d/'songs.txt').read_text().split() if (d/'songs.txt').exists() else []
    good &= check('and each row opens its own page there: the sixth, Songs, opens Songs (its Shuffle row first)',
                  rows[:1] == ['Shuffle'], str(rows[:4]))
    # the last row leaves q2ui for demo's menus: the program ends, with the code of a plain end
    trace, _, _ = script(binary, work, 60, ['rest', 'tick 9', 'rest', 'key centre', 'run 100', 'tick -1', 'run 100'], *home, code=0)
    good &= check('Centre on Old Menus ends q2ui', trace[-1]['sel'] == 7, str(trace[-1]['sel']))
    # Settings is q2ui's list, in demo's order; its rows ask for demo's pages, a stock one by its path
    _, _, _, (sent, _, d) = script(binary, work, 60, ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 5', 'rest', 'key centre', 'run 100'],
                                   *home, code=3, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check("Home > Settings > Car Remote asks demo for its page by the payload's opener", asked == 'carremote_page\n', repr(asked))
    good &= display(binary, work, home)
    good &= playback(binary, work, home)
    good &= wireless(binary, work, home)
    good &= wifi_networks(binary, work, home)
    good &= bt_devices(binary, work, home)
    good &= clicks(binary, work)
    good &= comebacks(binary, work, home)
    good &= general(binary, work, home)
    return good


def now_cycle(binary, work, home):
    """Now Playing's Centre is demo's iPod Centre cycle (patch/nav/now_playing.c np_centre): a press scrubs, the wheel
    moving the place 5 s a tick (more on a fast spin) with a playing song following it at most every 200 ms; the next
    press seeks there and rings Shuffle; the wheel walks Shuffle, Repeat and the heart, staying at the ends; Centre
    steps the ringed one down the queue ("mode"); 3 s with no key or Return ends the cycle; a radio leaves only the
    heart. Centre never plays or pauses: that is the Play/Pause key's. A second press within 250 ms of the one that
    started the scrub is the double that turns the screen off, so presses meant as two are 300 ms apart."""
    good = True
    words = lambda sent: [line.split(' ', 1)[1] for line in sent]  # noqa: E731
    to_now = ['rest', 'key centre', 'rest']
    # slow ticks (300 ms apart, each heard), then a fast spin of 6: 1, 1, 1 steps until 100 ms of spin, then 2 and more
    lines = to_now + ['key centre', 'run 50', 'tick 1', 'run 300', 'tick 1', 'run 300', 'tick -1', 'run 300']
    trace, _, _, (sent, _, _) = script(binary, work, 60, lines, *home, now=NOW, queue=True)
    good &= check('Centre starts the scrub at the song\'s place, each slow tick moves it 5 s and a playing song follows',
                  trace[-1]['scrub'] == 1 and trace[-1]['to'] == 19 and words(sent) == ['seek 19', 'seek 24', 'seek 19'],
                  f"{trace[-1]} {sent}")
    trace, _, _, (sent, _, _) = script(binary, work, 60, to_now + ['key centre', 'run 50'] + ['tick 1', 'run 30'] * 8,
                                       *home, now=NOW, queue=True)
    good &= check('a fast spin steps further each tick (patch/navigation.c ramp: 1 until 100 ms, then 2 and up)',
                  trace[-1]['to'] > 14 + 8 * 5, str(trace[-1]))
    lines = to_now + ['key centre', 'run 50', 'tick 2', 'run 50', 'key centre', 'run 50', 'tick 1', 'run 50', 'tick 1',
                      'run 50', 'tick 1', 'run 50', 'tick -1', 'run 50', 'key centre', 'run 50', 'key return', 'run 50']
    trace, _, _, (sent, _, _) = script(binary, work, 60, lines, *home, now=NOW, queue=True)
    stages = [int(t['stage']) for t in trace if 'stage' in t]  # Home's lines first
    good &= check("the next Centre seeks there and rings Shuffle; ticks walk Repeat, the heart and stay; Centre steps "
                  "Repeat Off to All; Return ends the ring and the page stays",
                  words(sent)[-2:] == ['seek 24', 'mode -1 2'] and 'toggle' not in words(sent) and 1 in stages
                  and stages[-1] == 0 and 3 in stages and trace[-1]['scrub'] == 0, f'{sent} {sorted(set(stages))}')
    # repeat All steps to One, then Off; shuffle on goes off
    for now, ticks, want in ((NOW.replace('id=5', 'id=5\nrepeat=2'), 1, 'mode -1 1'), (NOW.replace('id=5', 'id=5\nrepeat=1'), 1, 'mode -1 0'),
                             (NOW.replace('id=5', 'id=5\nshuffle=1'), 0, 'mode 0 -1'), (NOW.replace('id=5', 'id=5\nliked=0'), 2, 'like 5'),
                             (NOW.replace('id=5', 'id=5\nliked=1'), 2, 'like 5')):
        _, _, _, (sent, _, _) = script(binary, work, 60, to_now + ['key centre', 'run 300', 'key centre', 'run 50'] +
                                       ['tick 1', 'run 50'] * ticks + ['key centre', 'run 50'], *home, now=now, queue=True)
        good &= check(f'Centre on the ring steps it: {want}', words(sent)[-1:] == [want], str(sent))
    trace, _, _, (sent, _, _) = script(binary, work, 60, to_now + ['key centre', 'run 3100'], *home, now=NOW, queue=True)
    good &= check('3 s with no key ends a scrub the wheel never moved, seeking nothing',
                  trace[-1]['scrub'] == 0 and trace[-1]['stage'] == 0 and sent == [], f'{trace[-1]} {sent}')
    trace, _, _, _ = script(binary, work, 60, to_now + ['key centre', 'run 300', 'key centre', 'run 50', 'run 3100'], *home,
                            now=NOW, queue=True)
    good &= check('and a ring with no key goes after 3 s too', trace[-1]['stage'] == 0, str(trace[-1]))
    trace, _, _, (sent, _, _) = script(binary, work, 60, to_now + ['key centre', 'run 300', 'key centre', 'run 50', 'tick -1',
                                                                   'run 50'], *home, now=NOW + 'radio=1\n', queue=True)
    good &= check('while a radio plays the ring stands on the heart alone', trace[-1]['stage'] == 3, str(trace[-1]))
    # the screen off as demo's (np_centre): Centre held 400 ms (short of More's 600) with no scrub, or a second press within
    # 250 ms of the release that started a scrub with the wheel still; neither seeks, both end the cycle
    for name, keys, off, scrub in (('held 450 ms', ['down centre', 'run 450', 'up centre'], True, 0),
                                   ('held 300 ms', ['down centre', 'run 300', 'up centre'], False, 1),
                                   ('pressed twice 100 ms apart', ['key centre', 'run 100', 'key centre'], True, 0),
                                   ('pressed twice 300 ms apart', ['key centre', 'run 300', 'key centre'], False, 0),
                                   ('pressed twice, the wheel turned between', ['key centre', 'run 30', 'tick 1', 'key centre'], False, 0),
                                   ('held 450 ms while scrubbing', ['key centre', 'run 300', 'tick 1', 'down centre', 'run 450', 'up centre'],
                                    False, 0)):
        trace, _, _, (sent, _, _), said = script(binary, work, 60, to_now + keys + ['run 50'], *home, now=NOW, queue=True, ctl=True)
        rang = trace[-1]['stage'] != 0
        good &= check(f"Now Playing's Centre {name}: " + ('the screen off, unseeked, the cycle ended' if off else 'no screen off'),
                      (said == ['screenoff']) == off and (not off or ('seek' not in ' '.join(sent) and not rang)) and
                      trace[-1]['scrub'] == scrub, f'{said} {sent} {trace[-1]}')
    _, pics, _ = script(binary, work, 60, to_now + ['key centre', 'run 50', 'tick 2', 'run 50', 'shot @scrub.ppm'], *home,
                        now=NOW, queue=True)[:3]
    # a radio plays: demo's pill, 92 x 34 in the accent between x 112 and 204 (patch/np_bar.c np_modes), grown in 200 ms
    _, radio, _ = script(binary, work, 60, to_now + ['run 300', 'shot @r.ppm'], *home, now=NOW + 'radio=1\n')
    _, plain, _ = script(binary, work, 60, to_now + ['run 300', 'shot @r.ppm'], *home, now=NOW)
    pill = [radio['r'].at(x, 286) for x in (116, 200)] + [radio['r'].at(158, 271)]
    good &= check("while a radio plays its button is demo's pill in the accent, 112 to 204 across, 34 high",
                  pill == [ACCENT] * 3 and radio['r'].at(110, 286) != ACCENT and plain['r'].at(116, 286) != ACCENT, str(pill))
    # 24 s of 180 on the bar from x 24, 327 px long: the knob's 14 px white disc centred on the fill's end
    kx = round(24 + 4 + (327 - 4) * 24 / 180)
    good &= check("the scrub shows demo's white knob on the fill's end", pics['scrub'].at(kx, 231) == (255, 255, 255)
                  and pics['scrub'].at(kx + 9, 231) != (255, 255, 255), str(pics['scrub'].at(kx, 231)))
    return good


def art_key(artist, album):
    """patch/coverflow.c tags_key: the name of an album's thumbnail in Cover Flow's folder."""
    def fnv(h, text):
        for b in text.encode():
            h = (h ^ b) * 16777619 & 0xffffffff
        return h * 16777619 & 0xffffffff
    return fnv(fnv(2166136261, artist), album)


def playlists(binary, work, home):
    """Library > Playlists (ui/page_library.c): stock's table playlist, On-The-Go first while it has songs, then the
    lists in the order made; a list's songs in its own order, and Centre plays them from the row."""
    def made(d):
        db = sqlite3.connect(d/'library.db')
        songs = db.execute('select id, fileurl from songtable order by name collate nocase desc limit 3').fetchall()
        db.execute("insert into playlist(id, filename) values (1, 'Road')")
        for k, (_, url) in enumerate(songs):  # the songs in reverse title order: not the library's
            db.execute('insert into playlist(id, cd_num, fileurl) values (?, 1, ?)', (101 + k, url))
        db.execute('insert into playlist(id, cd_num, fileurl) values (110, 100, ?)', (songs[0][1],))
        db.commit()
        return [str(i) for i, _ in songs]
    # script() makes its own folder: its library is made-up the same every time, so the ids are known from a probe
    want = made(folder(work, 60))
    lines = ['rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'rest',
             'tick 1', 'rest', 'key centre', 'run 200']
    d = folder(work, 60)
    made(d)
    _, _, _, (sent, ids, _) = script(binary, work, 60, lines, *home, '--db', d/'library.db', queue=True)
    return check("Playlists lists On-The-Go and then Road; Centre on Road's second song plays Road's songs in its order from it",
                 len(sent) == 1 and sent[0].split()[1:3] == ['play', '1'] and ids == want, f'{sent} {ids} for {want}')


def display(binary, work, home):
    """Settings > Display, q2ui's own (ui/page_display.c): Light Mode and Reduce Motion as config.ini has them, set
    through demo, and applied when demo writes the file; the rest of demo's Display page by its path."""
    good = True
    ini = pathlib.Path(tempfile.mkdtemp(dir=work))/'config.ini'
    ini.write_text('[IPOD]\nLIGHT=0\nREDUCEMOTION=0\n')
    to = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 6', 'rest', 'key centre', 'rest']  # Home > Settings > Display
    lines = to + ['key centre', 'run 100', 'key centre', 'run 100', 'tick 1', 'rest', 'key centre', 'run 100']
    _, _, _, (sent, _, _), said = script(binary, work, 60, lines, *home, '--config', ini, queue=True, ctl=True)
    good &= check('Light Mode goes Off to On, then on to Auto before demo has written it; Reduce Motion goes on, each '
                  "down q2ui's own pipe and none down the queue", said == ['set light 1', 'set light 2', 'set motion 1'] and sent == [],
                  f'{said} {sent}')
    _, _, _, (sent, _, d) = script(binary, work, 60, to + ['tick 2', 'rest', 'key centre', 'run 100'], *home, '--config', ini, code=3, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check("its last row opens the rest of demo's Display page", asked == 'systemset/display_page\n', repr(asked))
    run = Run(binary, work, 60, '--config', ini, '--clock', '12:00')
    try:
        run.rest()
        dark = shot.pixel(run.picture(), 5, 200)
        (ini.parent/'config.tmp').write_text('[IPOD]\nLIGHT=1\n')
        (ini.parent/'config.tmp').rename(ini)
        run.rest()
        light = shot.pixel(run.picture(), 5, 200)
        good &= check('demo writing Light Mode on turns q2ui light as the file lands', dark == (0, 0, 0) and light == (255, 255, 255), f'{dark} {light}')
    finally:
        run.end()
    return good


def playback(binary, work, home):
    """Settings > Playback, q2ui's own (ui/page_playback.c): EQ first, as demo's Playback has it, opening demo's EQ
    page; Gapless and Smart Crossfade as config.ini has them, each set through demo (Smart
    Crossfade on turns Gapless on, as demo's row does); the Sleep Timer and the rest of demo's Playback page by their
    paths."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'config.ini').write_text('[PLAYSET]\nGAPLESS=1\n[Q2POD]\nXFADE=0\n')
    (d/'told').write_text('eq=1\n')
    to = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 3', 'rest', 'key centre', 'rest']  # Home > Settings > Playback
    lines = to + ['rows @playback.txt', 'tick 1', 'rest', 'key centre', 'run 100', 'tick 1', 'rest', 'key centre', 'run 100',
                  'tick -1', 'rest', 'key centre', 'run 100']
    opts = (*home, '--config', d/'config.ini', '--told', d/'told')
    _, _, _, (sent, _, out), said = script(binary, work, 60, lines, *opts, queue=True, ctl=True)
    rows = (out/'playback.txt').read_text().splitlines() if (out/'playback.txt').exists() else []
    good &= check("Playback's rows: EQ, the two switches, the Sleep Timer, the rest of demo's page",
                  rows == ['EQ', 'Gapless', 'Smart Crossfade', 'Sleep Timer', 'More Playback Settings'], str(rows))
    good &= check('Gapless (on in the file) goes off, Smart Crossfade on, and Gapless shows on with it again, each '
                  "down q2ui's own pipe and none down the queue",
                  said == ['set gapless 0', 'set xfade 1', 'set gapless 0'] and sent == [], f'{said} {sent}')
    for ticks, page in ((0, 'playset/equalizer_page'), (3, 'systemset/sleepshutdown_page'), (4, 'playset/playset_page')):
        _, _, _, (sent, _, out) = script(binary, work, 60, to + [f'tick {ticks}', 'rest', 'key centre', 'run 100'], *opts,
                                         code=3, queue=True)
        asked = (out/'ask').read_text() if (out/'ask').exists() else ''
        good &= check(f"its row {ticks + 1} opens demo's {page}", asked == page + '\n', repr(asked))
    return good


def clicks(binary, work):
    """The key click: q2ui reads the wheel and keys itself, so demo's click for them (Key Tone, the Clicker's route:
    patch/q2ui_gate.c ctl) is asked for down q2ui's pipe, "click 0" for a tick and "click 1" for a press, as demo would
    click: a list's tick that moves the selection (none at an end, where it stays), every volume tick on Now Playing,
    every Centre and Return as it goes down."""
    ticks = (1, -1, -1, -1, -1, 1)
    lines = ['rest'] + [w for d in ticks for w in (f'tick {d}', 'rest')] + ['key centre', 'run 300', 'tick 1', 'run 100', 'key return', 'run 300']
    trace, _, _, _, said = script(binary, work, 60, lines, now=NOW, queue=True, ctl=True, clicks=True)
    sel, moves = int(trace[0]['sel']) if trace else 0, 0
    for d in ticks:  # the list's, from the row it opened on (Shuffle, row 0, is the top)
        moves += max(0, sel + d) != sel
        sel = max(0, sel + d)
    want = ['click 0'] * moves + ['click 1', 'click 0', 'click 1']
    return check('a tick that moves the selection clicks and one at the top does not; Centre, a volume tick on Now Playing '
                 'and Return each click', said == want and 0 < moves < len(ticks), f'{said} {moves}')


def wireless(binary, work, home):
    """Settings > Wi-Fi and Settings > Bluetooth, q2ui's own (ui/page_wifi.c, ui/page_bluetooth.c): each page's switch
    stands as demo says (wifi=, bton= in its status file) and is set through demo ("set wifi|bt 0|1"); off, the switch is
    the page, as on demo's pages; on, the rows under it open demo's pages for the networks, the devices and their
    settings."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    told = d/'told'
    wifi = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'key centre', 'rest']  # Home > Settings > Wi-Fi
    bt = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'rest']  # Home > Settings > Bluetooth
    opts = (*home, '--told', told)
    for name, to, key, on_rows in (
            ('Wi-Fi', wifi, 'wifi', ['Wi-Fi', 'Networks', 'No Networks', 'Search Again', 'More Wi-Fi Settings']),
            ('Bluetooth', bt, 'bt', ['Bluetooth', 'My Devices', 'Other Devices', 'No Devices', 'Quality', 'Bluetooth Headset Volume Control', 'More Bluetooth Settings'])):
        flag = 'wifi' if key == 'wifi' else 'bton'
        told.write_text(f'{flag}=0\n')
        _, _, _, (sent, _, out), said = script(binary, work, 60, to + ['rows @off.txt', 'key centre', 'run 100', 'rest',
                                                                       'rows @on.txt'], *opts, queue=True, ctl=True)
        off = (out/'off.txt').read_text().splitlines() if (out/'off.txt').exists() else []
        on = (out/'on.txt').read_text().splitlines() if (out/'on.txt').exists() else []
        searched = ['scan'] if key == 'wifi' else ['btscan']  # coming on asks demo for a search (wifi_networks, bt_devices)
        good &= check(f"{name} off is its switch alone; Centre turns it on down q2ui's own pipe and its rows come at once",
                      off == [name] and said == [f'set {key} 1'] + searched and sent == [] and on == on_rows,
                      f'{off} {said} {sent} {on}')
        told.write_text(f'{flag}=1\n')
        _, _, _, (_, _, out), said = script(binary, work, 60, to + ['key centre', 'run 100', 'rest', 'rows @off.txt'], *opts,
                                            queue=True, ctl=True)
        off = (out/'off.txt').read_text().splitlines() if (out/'off.txt').exists() else []
        good &= check(f'{name} on by demo goes off, its rows going with it',
                      said == searched + [f'set {key} 0'] and off == [name], f'{said} {off}')
    # past the headers and the grey "No Networks" or "No Devices" row (and Wi-Fi's Search Again)
    pages = (('Wi-Fi', wifi, 2, 'systemset/wifi_page'), ('Bluetooth', bt, 1, 'systemset/btquality_page'),
             ('Bluetooth', bt, 2, 'systemset/btadjustvol_page'), ('Bluetooth', bt, 3, 'systemset/bluetooth_page'))
    told.write_text('wifi=1\nbton=1\n')
    for name, to, ticks, page in pages:
        _, _, _, (_, _, out) = script(binary, work, 60, to + [f'tick {ticks}', 'rest', 'key centre', 'run 100'], *opts,
                                      code=3, queue=True)
        asked = (out/'ask').read_text() if (out/'ask').exists() else ''
        good &= check(f"{name}'s row {ticks} down opens demo's {page}", asked == page + '\n', repr(asked))
    return good


NETWORKS = 'scan 0\n4 1 2 Home\n3 0 0 Cafe\n1 1 4 Back\\\\sl\\nash\n2 0 3 Far\n'


def wifi_networks(binary, work, home):
    """Settings > Wi-Fi's networks (ui/page_wifi.c), as demo lists them (--networks, patch/wifi_flows.c
    wifi_flows_networks): each with its signal and lock at the right column and its join's state after its name, in
    demo's order; the page asks for a search as it opens; Centre on a network joins it by its name, escaped as demo
    reads it; a new list keeps the selection on its network; Search Again searches again."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'told').write_text('wifi=1\n')
    (d/'nets').write_text(NETWORKS)
    (d/'later').write_text('scan 1\n3 0 1 Cafe\n4 1 0 Home\n')  # Far and the other gone, Cafe joining first
    to = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'key centre', 'rest']  # Home > Settings > Wi-Fi
    lines = to + ['rows @rows.txt', 'tick 3', 'rest', 'key centre', 'rest', 'tick -1', 'rest', 'key centre', 'rest',
                  f'networks {d/"later"}', 'rest', 'rows @later.txt', 'key centre', 'rest', 'tick 2', 'rest',
                  'key centre', 'rest']
    _, _, _, (_, _, out), said = script(binary, work, 60, lines, *home, '--told', d/'told', '--networks', d/'nets',
                                        queue=True, ctl=True)
    got = (out/'rows.txt').read_text().splitlines() if (out/'rows.txt').exists() else []
    rows = ['Wi-Fi', 'Networks', 'Home\t\tConnected\twifi_3 lock', 'Cafe\t\t\twifi_2',
            'Back\\sl', 'ash\t\tWrong password\twifi_1 lock', "Far\t\tCouldn't connect\twifi_1",
            'Search Again', 'More Wi-Fi Settings']  # a name's newline splits its line here
    good &= check("Wi-Fi's networks as demo lists them, each with its signal, its lock and its join's state",
                  got == rows, repr(got))
    good &= check('opening the page asks for a search; Centre joins a network by its name, escaped',
                  said[:3] == ['scan', 'join Back\\\\sl\\nash', 'join Cafe'], repr(said))
    later = (out/'later.txt').read_text().splitlines() if (out/'later.txt').exists() else []
    good &= check("a new list from demo is shown and keeps the selection on its network (Cafe, now first); Search "
                  "Again after the networks asks for a search", later[2:4] == ['Cafe\t\tConnecting\u2026\twifi_2',
                  'Home\t\t\twifi_3 lock'] and said[3:] == ['join Cafe', 'scan'], f'{later} {said}')
    return good


DEVICES = ('scan 1\n1 2 AA:BB:CC:DD:EE:01 Fixture Phones\n1 0 AA:BB:CC:DD:EE:02 Fixture Car\n'
           '0 0 AA:BB:CC:DD:EE:03 Fixture Back\\\\sl\\nash\n')


def bt_devices(binary, work, home):
    """Settings > Bluetooth's devices (ui/page_bluetooth.c), as demo lists them (--bt-devices, patch/bt_ui.c
    bt_q2ui_devices): My Devices with each one's state, Other Devices the ones a scan heard; the page asks for a scan as
    it opens; a paired device opens its own page, whose Disconnect or Connect and Forget go down the pipe by its MAC;
    Centre on a found one connects it; a new list keeps the selection on its device."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'told').write_text('bton=1\n')
    (d/'devs').write_text(DEVICES)
    # the car forgotten, the found one paired and connected: it moves up into My Devices
    (d/'later').write_text('scan 0\n1 0 AA:BB:CC:DD:EE:01 Fixture Phones\n1 2 AA:BB:CC:DD:EE:03 Fixture Back\\\\sl\\nash\n')
    to = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'rest']  # Settings > Bluetooth
    # Phones' page: Disconnect; the found one; Car's page: Forget; then the found one selected as demo's new list comes
    lines = to + ['rows @rows.txt', 'tick 1', 'rest', 'key centre', 'rest', 'rows @phones.txt', 'key centre', 'rest',
                  'tick 2', 'rest', 'key centre', 'rest', 'tick -1', 'rest', 'key centre', 'rest', 'rows @car.txt',
                  'tick 1', 'rest', 'key centre', 'rest', 'tick 1', 'rest',
                  f'btdevices {d/"later"}', 'rest', 'rows @later.txt', 'key centre', 'rest', 'rows @again.txt']
    _, _, _, (_, _, out), said = script(binary, work, 60, lines, *home, '--told', d/'told', '--bt-devices', d/'devs',
                                        queue=True, ctl=True)

    def rows(name):
        return (out/name).read_text().splitlines() if (out/name).exists() else []
    want = ['Bluetooth', 'My Devices', 'Fixture Phones\tConnected', 'Fixture Car\tNot Connected', 'Other Devices',
            'Fixture Back\\sl', 'ash', 'Quality', 'Bluetooth Headset Volume Control', 'More Bluetooth Settings']
    good &= check("Bluetooth's devices as demo lists them: paired with their state, then the ones a scan heard",
                  rows('rows.txt') == want, repr(rows('rows.txt')))
    good &= check("a connected device's page offers Disconnect and Forget, a paired one's Connect",
                  rows('phones.txt') == ['Disconnect', 'Forget This Device'] and
                  rows('car.txt') == ['Connect', 'Forget This Device'], f"{rows('phones.txt')} {rows('car.txt')}")
    good &= check('the page scans as it opens; Disconnect, a found device and Forget go down the pipe by MAC',
                  said[:4] == ['btscan', 'btlink AA:BB:CC:DD:EE:01', 'btlink AA:BB:CC:DD:EE:03',
                               'btforget AA:BB:CC:DD:EE:02'], repr(said))
    good &= check('a new list from demo is shown and keeps the selection on its device (moved up into My Devices)',
                  rows('later.txt')[:4] == ['Bluetooth', 'My Devices', 'Fixture Phones\tNot Connected', 'Fixture Back\\sl']
                  and rows('again.txt') == ['Disconnect', 'Forget This Device'], f"{rows('later.txt')} {rows('again.txt')}")
    return good


def az_index(binary, work):
    """The A-Z index (docs/oled-design.md#a-z-index): on a list sorted by title of more than 50 rows, a fast spin shows
    the folded column at x 362 with the selected row's letter lit on it and the bubble beside it, the right column 4 px
    in; a second after the last fast tick it is gone. Slow ticks, or a list of 50 rows or fewer, show none."""
    good = True
    fast = ['rest', 'tick 30', 'shot @spin.ppm']
    trace, pics, _ = script(binary, work, 300, fast + ['run 400', 'shot @still.ppm', 'run 700', 'shot @gone.ppm'])
    spun = [t for t in trace if t.get('az', -1) >= 0]
    last = trace[-1]
    sel_letter = int(spun[-1]['az']) if spun else -1
    good &= check('a fast spin on 300 songs shows the index, the right column 4 px in, and it goes a second after',
                  bool(spun) and spun[-1]['azin'] == 4 and last['az'] == -1 and last['azin'] == 0 and 0 <= sel_letter <= 26,
                  f'{len(spun)} frames with it, last {last}')
    col = [pics['spin'].at(362, y) for y in range(78, 83)]  # '#' at the column's top
    lit_y = round(80 + sel_letter * 160 / 26)
    disc = pics['spin'].at(362 - 6, lit_y)  # the lit letter's white disc, beside its black letter
    bubble = pics['spin'].at(362 - 8 - 18 - 26, lit_y)  # the bubble's middle while it spins
    good &= check('the column is drawn, the lit letter a white disc at its place, the bubble beside it while it spins, and '
                  'the bubble gone once the spin has stopped', any(c[0] > 150 for c in col) and disc == (255, 255, 255) and
                  bubble == (0x2c, 0x2c, 0x2e) and pics['still'].at(362 - 8 - 18 - 26, lit_y) != (0x2c, 0x2c, 0x2e) and
                  all(pics['gone'].at(362, y)[0] < 40 for y in range(78, 83)),
                  f"col {col} disc {disc} bubble {bubble} still {pics['still'].at(362 - 8 - 18 - 26, lit_y)}")
    slow, _, _ = script(binary, work, 300, ['rest'] + ['tick 1', 'run 300'] * 6)
    few, _, _ = script(binary, work, 50, ['rest', 'tick 30'])
    good &= check('slow ticks, or a list of 50 rows, show no index', all(t.get('az', -1) == -1 for t in slow + few))
    return good


def captions(binary, work, home):
    """A captioned row (docs/oled-design.md#list-row): its caption shows only while it is selected, the row growing over
    150 ms to hold it in the bar, Inter 13 in Caption On Accent from the title's column, 17 px a line and 5 px under the
    last, the rows below moved down by as much; it shrinks back as the selection leaves. Under Reduce Motion it is at
    its end at once. A grown row near a list's end scrolls the list by whole rows to keep its foot on the list end."""
    good = True
    to = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 6', 'rest', 'key centre', 'rest']  # Home > Settings > Display
    lines = to + ['shot @before.ppm', 'tick 1', 'rest', 'shot @grown.ppm', 'tick 1', 'rest', 'shot @shut.ppm']
    trace, pics, _ = script(binary, work, 60, lines, *home)
    caps = [t.get('cap', -1) for t in trace]
    good &= check('Reduce Motion grows by one line (22 px) when selected, over frames between, and back to 0 as the selection '
                  'leaves', max(caps) == 22 and caps[-1] == 0 and sum(1 for c in caps if 0 < c < 22) >= 3, str(sorted(set(caps))))
    ink = sum(1 for y in range(124, 140) for x in range(24, 340) if (lambda c: c[0] > 150 and c[2] - c[0] > 20)(pics['grown'].at(x, y)))
    gone = sum(1 for y in range(124, 140) for x in range(24, 340) if pics['before'].blue(x, y))
    good &= check("its caption is drawn in the bar under the title, the bar grown to hold it",
                  ink > 60 and pics['grown'].blue(30, 141) and gone == 0, f'{ink} ink px, blue at 141 {pics["grown"].blue(30, 141)}')
    below = [pics['before'].ink_y(24, 300, 120, 156)[0], pics['grown'].ink_y(24, 300, 142, 178)[0]]
    good &= check('the row below moves down by the caption', 20.5 < below[1] - below[0] < 23.5, str(below))
    back = pics['shut'].ink_y(24, 300, 116, 164)[0]
    good &= check('the selection moved on to it, the row below is back in its place', abs(back - below[0]) < 1.5, f'{back} {below[0]}')
    trace, _, _ = script(binary, work, 60, to + ['tick 1', 'rest'], *home, '--reduce-motion')
    caps = [t.get('cap', -1) for t in trace]
    good &= check('under Reduce Motion the row is grown at once', max(caps) == 22 and not any(0 < c < 22 for c in caps), str(sorted(set(caps))))
    # the gallery's last row has a caption of two lines: selected, the list scrolls two rows more than for a plain row
    trace, _, _ = script(binary, work, 1, ['rest', 'tick 14', 'rest'], '--page', 'gallery')
    good &= check("a two-line caption on a list's last row: 39 px grown, the list scrolled to keep its foot on the list end",
                  trace[-1]['sel'] == 13 and trace[-1]['cap'] == 39 and trace[-1]['scroll'] == 9 * ROW_H, str(trace[-1]))
    return good


def comebacks(binary, work, home):
    """A page of demo's opened from a page of q2ui's: q2ui, started again when demo's page is left, is back on the page
    that opened it, on its row, under the pages it had (main.c state_restore), for every page that opens one."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'told').write_text('wifi=1\nbton=1\n')
    settings = ['rest', 'tick 6', 'rest', 'key centre', 'rest']
    for name, to, rows in (
            ('Wi-Fi', settings + ['key centre', 'rest', 'tick 2'], ['Wi-Fi', 'Networks', 'No Networks', 'Search Again',
                                                                   'More Wi-Fi Settings']),
            ('Bluetooth', settings + ['tick 1', 'rest', 'key centre', 'rest', 'tick 2'], ['Bluetooth', 'My Devices', 'Other Devices', 'No Devices', 'Quality', 'Bluetooth Headset Volume Control', 'More Bluetooth Settings']),
            ('General', settings + ['tick 2', 'rest', 'key centre', 'rest', 'tick 1'], ['About', 'More General Settings']),
            ('Playback', settings + ['tick 3', 'rest', 'key centre', 'rest'],
             ['EQ', 'Gapless', 'Smart Crossfade', 'Sleep Timer', 'More Playback Settings'])):
        state = d/f'state-{len(name)}'
        opts = (*home, '--told', d/'told', '--state', state)
        script(binary, work, 60, to + ['rest', 'key centre', 'run 100'], *opts, code=3, queue=True)
        trace, _, _, (_, _, out) = script(binary, work, 60, ['rest', 'rows @top.txt', 'rest'], *opts, queue=True)
        got = (out/'top.txt').read_text().splitlines() if (out/'top.txt').exists() else []
        sel = int(trace[-1]['sel']) if trace else -1
        want_sel = int(to[-1].split()[1]) if to[-1].startswith('tick') else 0
        if name == 'Wi-Fi':
            want_sel = 4  # two ticks pass over the header and the grey "No Networks" row
        if name == 'Bluetooth':
            want_sel = 5  # past both headers and the grey "No Devices" row
        ok = got == rows
        good &= check(f"back from the page of demo's that {name} opened, q2ui is on {name} on the row it was on",
                      ok and sel == want_sel, f'{got} sel {sel}')
    # left for demo's Search strip with nothing playing, whose result then played: demo's Now Playing is up as q2ui
    # starts again (--then now), and Home has gained its Playing row since the row was kept
    state = d/'state-search'
    opts = (*home, '--state', state)
    script(binary, work, 20, ['rest', 'tick 5', 'rest', 'key centre', 'run 100'], *opts, code=3, queue=True)
    tops = []
    for lines in (['rest'], ['rest', 'key return', 'rest', 'rows @top.txt']):
        trace, _, stats, (_, _, out) = script(binary, work, 20, lines, *opts, '--then', 'now', now=NOW, queue=True)
        tops.append(stats['top'])
    rows = (out/'top.txt').read_text().splitlines() if (out/'top.txt').exists() else []
    sel = int(trace[-1]['sel']) if trace else -1
    on = rows[sel].split('\t')[0] if 0 <= sel < len(rows) else None
    good &= check("left for demo's Search strip whose result played, q2ui comes back on its own Now Playing (--then now), "
                  "and Return goes back to Home on the Search row, Home's Playing row come since",
                  tops == ['now', 'home'] and on == 'Search', f'{tops} on {on!r} ({sel} of {rows})')
    return good


def general(binary, work, home):
    """Settings > General, q2ui's own (ui/page_general.c): About first, then the rest of demo's General page by its
    path. About lists the product and its version and the stock firmware's as demo says them (its status file), the
    library's songs, the card's capacity and room left, and Wi-Fi's address."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'card').mkdir()
    (d/'told').write_text('product=Vamp\nversion=9.8.7 dev test\nfirmware=V1.36\n')
    to = ['rest', 'tick 6', 'rest', 'key centre', 'rest', 'tick 2', 'rest', 'key centre', 'rest']  # Home > Settings > General
    opts = (*home, '--told', d/'told', '--card', d/'card')
    _, _, _, (_, _, out) = script(binary, work, 60, to + ['rows @general.txt', 'key centre', 'rest', 'rows @about.txt'], *opts,
                                  queue=True)
    rows = (out/'general.txt').read_text().splitlines() if (out/'general.txt').exists() else []
    good &= check("General's rows are About, then the rest of demo's page", rows == ['About', 'More General Settings'], str(rows))
    about = [r.split('\t') for r in ((out/'about.txt').read_text().splitlines() if (out/'about.txt').exists() else [])]
    want = [['Vamp', '9.8.7 dev test'], ['Firmware', 'V1.36'], ['Songs', '60']]
    sized = [r for r in about[3:5] if len(r) == 2 and re.fullmatch(r'\d+\.\d\d GB', r[1])]
    good &= check('About: the product and both versions as demo says them, the songs, the card in GB and Wi-Fi off',
                  about[:3] == want and [r[0] for r in sized] == ['Capacity', 'Available'] and
                  about[5:] == [['Wi-Fi Address', 'Not connected']], str(about))
    _, _, _, (_, _, out) = script(binary, work, 60, to + ['tick 1', 'rest', 'key centre', 'run 100'], *opts, code=3, queue=True)
    asked = (out/'ask').read_text() if (out/'ask').exists() else ''
    good &= check("its last row opens demo's General page", asked == 'systemset/sysset_page\n', repr(asked))
    return good


RAISED, OFF, FILL, GRAB = (0x1c, 0x1c, 0x1e), (0x3a, 0x3a, 0x3c), (0xe5, 0xe5, 0xea), (0x5a, 0x5a, 0x5e)
# the toggles' circles' centres (docs/oled-design.md#control-center): Wi-Fi, Bluetooth / AirPlay, Car / Smart Wi-Fi, EQ
CIRCLES = [(66, 68), (132, 68), (66, 134), (132, 134), (66, 200), (132, 200)]
CC_TOLD = 'out=other\nwifi=1\nbton=0\nairplay=1\ncar=0\nsmart=0\neq=1\nusb=Automatic\nbright=40\nvol=30\nvolmax=33\ncable=0\n'


def cc_told(work, text=CC_TOLD):
    told = pathlib.Path(tempfile.mkdtemp(dir=work))/'told'
    told.write_text(text)
    return told


def control_center(binary, work, home):
    """Control Center (ui/page_control.c) as demo's two-column sheet (docs/oled-design.md#control-center): six circles lit
    as demo says its switches stand (the told file, patch/control_center.c q2ui_cc_state), Now Playing, two pill sliders,
    the USB row while a cable is in, the grab bar. The wheel's ring is not there on opening; the first tick shows it on
    Wi-Fi without moving, and it goes 5 s after the last input. Centre flips a switch down q2ui's own pipe, at once."""
    good = True
    told = cc_told(work)
    lines = ['rest', 'tick 2', 'rest', 'hold return', 'rest', 'shot @open.ppm', 'tick 1', 'run 100', 'shot @ring.ppm', 'key centre',
             'run 100', 'shot @flipped.ppm', 'tick 1', 'run 100', 'key centre', 'run 5200', 'shot @gone.ppm']
    # any exit: a fault that has Centre land on a page of demo's is these checks' to report
    trace, pics, stats, (sent, _, _), said = script(binary, work, 60, lines, *home, '--told', told, code=None, queue=True, ctl=True)
    p = pics['open']
    lit = [p.at(x + 15, y) for x, y in CIRCLES]
    good &= check('Return held opens the sheet: the toggles block, Wi-Fi, AirPlay and EQ lit as demo says, Bluetooth, Car '
                  'and Smart Wi-Fi off', p.at(25, 100) == RAISED and lit == [ACCENT, OFF, ACCENT, OFF, OFF, ACCENT], f'{p.at(25, 100)} {lit}')
    good &= check('Now Playing beside it, a grey square for no cover, and no ring anywhere on opening',
                  p.at(300, 45) == RAISED and p.at(226, 76) == OFF and p.at(87, 68) == RAISED, f'{p.at(300, 45)} {p.at(226, 76)} {p.at(87, 68)}')
    good &= check("the sliders fill #E5E5EA to demo's levels, Brightness 40, Volume 30, on #3A3A3C",
                  [p.at(230, 181), p.at(262, 181), p.at(225, 229), p.at(245, 229)] == [FILL, OFF, FILL, OFF],
                  str([p.at(230, 181), p.at(262, 181), p.at(225, 229), p.at(245, 229)]))
    good &= check('no cable, no USB row; the grab bar on the foot', p.at(100, 277) == (0, 0, 0) and p.at(187, 311) == GRAB,
                  f'{p.at(100, 277)} {p.at(187, 311)}')
    ring = pics['ring'].at(87, 68)
    good &= check("the first tick shows the white ring on Wi-Fi and does not move", ring == (255, 255, 255) and
                  trace[-1]['sel'] == 1 and any(t['sel'] == 0 and t.get('ring') == 1 for t in trace), str(ring))
    good &= check("Centre on Wi-Fi turns its circle off at once, before demo says so", pics['flipped'].at(81, 68) == OFF,
                  str(pics['flipped'].at(81, 68)))
    good &= check("Wi-Fi goes off, then the next stop, Bluetooth, on, each down q2ui's own pipe and none down the queue",
                  said == ['set wifi 0', 'set bt 1'] and sent == [], f'{said} {sent}')
    good &= check('the ring goes 5 s after the last input', trace[-1]['ring'] == 0 and pics['gone'].at(153, 68) == RAISED,
                  f"{trace[-1]['ring']} {pics['gone'].at(153, 68)}")
    trace, _, _, (sent, _, _) = script(binary, work, 60, lines, *home, '--told', told, queue=True)
    good &= check('with no pipe (q2ui not started by demo) a switch sends nothing', sent == [], str(sent))
    # Bluetooth flipped while its stack is still starting (about 3 s on a Q2): demo queues the flip and says btbusy=1
    # until the stack is up and the flip done (patch/control_center.c q2ui_cc_set); a demo that slow stands in here, its
    # word unchanged for 3 s. The circle shows the flip all that time, and lets go after WANT_HOLD_US only once demo is
    # not busy and still says otherwise.
    walk = ['rest', 'hold return', 'rest', 'tick 1', 'run 50', 'tick 1', 'run 50', 'key centre', 'run 3000', 'shot @late.ppm']
    bt = (CIRCLES[1][0] + 15, CIRCLES[1][1])
    for busy, want in ((1, OFF), (0, ACCENT)):
        slow = cc_told(work, CC_TOLD.replace('bton=0', 'bton=1') + ('btbusy=1\n' if busy else ''))
        _, pics, _, (sent, _, _), said = script(binary, work, 60, walk, *home, '--told', slow, queue=True, ctl=True)
        good &= check('Bluetooth flipped off while demo is busy starting its stack stays off on the sheet 3 s on' if busy else
                      'not busy, a flip demo never takes is let go after 2 s', said == ['set bt 0'] and pics['late'].at(*bt) == want,
                      f"{said} {pics['late'].at(*bt)}")
    # the stops that open a page: AirPlay by Centre, the USB row (only with a cable in) by Centre, and the toggles held
    cable = cc_told(work, CC_TOLD.replace('cable=0', 'cable=1'))
    asks = []
    for ticks, key, told_file in ((2, 'key centre', told), (9, 'key centre', cable), (5, 'hold centre', told), (3, 'hold centre', told)):
        walk = ['rest', 'hold return', 'rest', 'tick 1', 'run 50', f'tick {ticks}', 'run 50', 'shot @at.ppm', key, 'run 100']
        _, pics, _, (sent, _, d) = script(binary, work, 60, walk, *home, '--told', told_file, code=3, queue=True)
        asks.append(((d/'ask').read_text().strip() if (d/'ask').exists() else '', sent))
        if ticks == 9:
            u = pics['at']
            good &= check('with a cable in, the USB row under the blocks, ringed white 2 px wide 5 px out when the wheel is on it',
                          u.at(100, 277) == RAISED and u.at(16, 277) == (255, 255, 255), f'{u.at(100, 277)} {u.at(16, 277)}')
    good &= check("AirPlay's Centre, the USB row's Centre, EQ held and Car held each open demo's page for it",
                  asks == [('systemset/netservice_page', []), ('usbpick_page', []), ('playset/equalizer_page', []), ('carremote_page', [])], str(asks))
    rows = {}
    for name, ticks in (('wifi', 0), ('smart', 4), ('bt', 1)):
        walk = ['rest', 'hold return', 'rest', 'tick 1', 'run 50', f'tick {ticks}', 'run 50', 'hold centre', 'rest', 'rows @rows.txt']
        _, _, stats, (sent, _, d) = script(binary, work, 60, walk, *home, '--told', told, queue=True)
        rows[name] = (d/'rows.txt').read_text().splitlines()[:1] if (d/'rows.txt').exists() else []
    good &= check("Wi-Fi and Smart Wi-Fi held open q2ui's Wi-Fi page, Bluetooth held its Bluetooth page",
                  rows == {'wifi': ['Wi-Fi'], 'smart': ['Wi-Fi'], 'bt': ['Bluetooth']}, str(rows))
    return good


def control_center_sheet(binary, work, home):
    """The sheet's Wi-Fi circle is the link as the status row reads it, its landing springs, Light Mode inks it, and the
    Now Playing block opens Now Playing."""
    good = True
    icons = pathlib.Path(tempfile.mkdtemp(dir=work))
    inks = {'wifi_slash': (200, 0, 0), 'wifi_0': (0, 200, 0), 'wifi_3': (0, 0, 200), 'wifi_x': (200, 200, 0)}
    for name, rgb in inks.items():
        png(icons/f'oled_cc_{name}.png', 22, 22, rgb)
    seen = {}
    for case, text, link in (('off', 'wifi=0\n', None), ('on', 'wifi=1\n', None), ('joined', 'wifi=1\n', 60),
                             ('joined, no internet', 'wifi=1\nnet=0\n', 60), ('joined, switch off', 'wifi=0\n', 60)):
        d = pathlib.Path(tempfile.mkdtemp(dir=work))
        (d/'told').write_text(text)
        opts = ['--told', d/'told', '--icons', icons]
        if link:
            (d/'wifi').write_text('up\n')
            (d/'wireless').write_text(f'Inter-| sta\n face | tus\n wlan0: 0000   {link}.  -40.  -256\n')
            opts += ['--wifi', d/'wifi', '--wireless', d/'wireless']
        _, pics, _ = script(binary, work, 60, ['rest', 'hold return', 'rest', 'shot @cc.ppm'], *home, *opts)
        seen[case] = (pics['cc'].at(66, 68), pics['cc'].at(81, 68))
    want = {'off': (inks['wifi_slash'], OFF), 'on': (inks['wifi_0'], ACCENT), 'joined': (inks['wifi_3'], ACCENT),
            'joined, no internet': (inks['wifi_x'], ACCENT), 'joined, switch off': (inks['wifi_3'], ACCENT)}
    good &= check("the Wi-Fi circle's glyph is the link's (slash off, none on, the fan joined, x with no internet), lit "
                  'whenever joined', seen == want, str(seen))
    told = cc_told(work)
    trace, _, _ = script(binary, work, 60, ['rest', 'hold return', 'rest'], *home, '--told', told)
    land = [t['land'] for t in trace if 'land' in t]
    good &= check('landing open, the blocks overshoot 8 px down, spring back to 2 px short and are still',
                  land and 7.5 <= max(land) <= 8.5 and -2.5 <= min(land) <= -1.5 and land[-1] == 0, f'{max(land, default=0)} {min(land, default=0)}')
    trace, _, _ = script(binary, work, 60, ['rest', 'hold return', 'rest'], *home, '--told', told, '--reduce-motion')
    good &= check('under Reduce Motion they land still', all(t.get('land', 0) == 0 for t in trace), '')
    _, pics, _ = script(binary, work, 60, ['rest', 'hold return', 'rest', 'shot @light.ppm'], *home, '--told', told, '--light')
    p = pics['light']
    good &= check("Light Mode: the block in Raised's light half and the sliders' fill dark, as demo's flip makes them",
                  p.at(25, 100) == (0xe3, 0xe3, 0xe1) and p.at(230, 181) == (0x1a, 0x1a, 0x15), f'{p.at(25, 100)} {p.at(230, 181)}')
    # the sliders: Centre on one gives it the wheel (its ring the accent), clockwise is more (demo's cc_wheel), Centre or
    # Return gives it back
    lines = ['rest', 'hold return', 'rest', 'tick 1', 'run 50', 'tick 6', 'run 50', 'shot @on.ppm', 'key centre', 'run 50', 'shot @grabbed.ppm',
             'tick 2', 'tick -1', 'key centre', 'run 50', 'tick 1', 'run 50', 'key centre', 'run 50', 'tick 5', 'key return', 'run 300',
             'shot @set.ppm', 'tick 1', 'run 50']
    trace, pics, stats, (sent, _, _), said = script(binary, work, 60, lines, *home, '--told', told, queue=True, ctl=True)
    verbs = [line.split(' ', 1)[1] for line in sent]
    good &= check("Brightness, grabbed, goes up two clockwise and down one back down the pipe; Volume, grabbed, clockwise up to demo's top (33) down the "
                  'queue and no further', said == ['set bright 41', 'set bright 42', 'set bright 41'] and
                  verbs == ['volume 31', 'volume 32', 'volume 33'], f'{said} {verbs}')
    good &= check('Return gave Volume back and the sheet stayed; with no cable the next tick stays on Volume, the last stop',
                  trace[-1]['grab'] == 0 and trace[-1]['sel'] == 8 and stats['top'] == 'control', f"{trace[-1]} {stats['top']}")
    rings = (pics['on'].at(186, 181), pics['grabbed'].at(186, 181))
    good &= check('a slider the wheel is on is ringed white, and in the accent once it has the wheel', rings == ((255, 255, 255), ACCENT), str(rings))
    q = pics['set']
    fills = [q.at(250, 181), q.at(262, 181), q.at(240, 229), q.at(250, 229)]
    good &= check('each fill shows the level set at once: Brightness 41, Volume 33', fills == [FILL, OFF, FILL, OFF], str(fills))
    walk = ['rest', 'hold return', 'rest', 'shot @np.ppm', 'tick 1', 'run 50', 'tick 6', 'run 50', 'key centre', 'rest']
    trace, pics, stats = script(binary, work, 60, walk, *home, '--told', told, now=NOW)
    _, ink = pics['np'].ink_y(250, 350, 60, 82)
    good &= check("while something plays, the Now Playing block shows its title and is the stop after EQ; Centre on it "
                  'opens Now Playing', ink > 0 and stats['top'] == 'now', f"{ink} {stats['top']}")
    return good


def album_covers(binary, work, home):
    """Albums' rows lead with their covers (ui/art.c), found as Cover Flow names them; one it has not made shows the
    placeholder. Read in place by a script, and by the worker in a live run."""
    good = True
    probe = folder(work, 60)
    db = sqlite3.connect(probe/'library.db')
    album = db.execute('select album from songtable order by album collate nocase limit 1').fetchone()[0]
    artist = db.execute('select songer from songtable where album = ? order by track, name collate nocase, id', (album,)).fetchone()[0]
    art = pathlib.Path(tempfile.mkdtemp(dir=work))
    png(art/f'{art_key(artist, album):08x}.jpg', 160, 160, (200, 40, 40))  # stb reads it by its bytes
    walk = ['rest', 'key centre', 'rest', 'tick 3', 'rest', 'key centre', 'rest', 'shot @albums.ppm']
    _, pics, _ = script(binary, work, 60, walk, *home, '--art', art)
    p = pics['albums']
    good &= check("the first album's row is led by its cover and the second, with none made, by the placeholder",
                  p.at(44, 84) == (200, 40, 40) and p.at(44, 136) == (0x2c, 0x2c, 0x2e), f'{p.at(44, 84)} {p.at(44, 136)}')
    run = Run(binary, work, 60, *home, '--art', art, '--clock', '12:00')
    try:
        run.rest()
        run.key(KEY_CENTRE)
        run.rest()
        run.ticks(3)
        run.rest()
        run.key(KEY_CENTRE)
        run.rest()
        got = shot.pixel(run.picture(), 44, 84)
        good &= check('live, the worker reads it and the row is drawn again with it', got == (200, 40, 40), str(got))
    finally:  # Home's Return opens no way out: the program is ended
        run.proc.kill()
        run.proc.wait(timeout=5)
        os.close(run.queue)
    # Cover Flow makes its thumbnails after q2ui has looked (a fresh card: demo builds them once its Cover Flow page
    # opens, the emulator's m29 walk): the rows on screen take the cover once it is written, with no key pressed
    late = pathlib.Path(tempfile.mkdtemp(dir=work))
    run = Run(binary, work, 60, *home, '--art', late, '--clock', '12:00')
    try:
        run.rest()
        run.key(KEY_CENTRE)
        run.rest()
        run.ticks(3)
        run.rest()
        run.key(KEY_CENTRE)
        run.rest()
        before = shot.pixel(run.picture(), 44, 84)
        png(late/f'{art_key(artist, album):08x}.jpg', 160, 160, (200, 40, 40))
        time.sleep(4)
        after = shot.pixel(run.picture(), 44, 84)
        good &= check('a cover Cover Flow writes after the row was drawn with the placeholder is drawn within seconds',
                      before == (0x2c, 0x2c, 0x2e) and after == (200, 40, 40), f'{before} then {after}')
    finally:
        run.proc.kill()
        run.proc.wait(timeout=5)
        os.close(run.queue)
    return good


def now_covers(binary, work, home):
    """Now Playing's art is the file demo names for the playing song (cover= in its status file, patch/q2ui_gate.c
    tell, from coverflow.c song_cover_url): the player's embedded picture (/tmp/coverpic.jpg), a folder image or a
    download (/tmp/externpic.jpg), or a card app's cover of a server song's album. None named is the placeholder,
    whatever file the player left from the song before; demo saying nothing leaves --cover's file."""
    good = True
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    embedded, folder_image, stale = d/'coverpic.png', d/'externpic.png', d/'stale.png'
    png(embedded, 300, 300, (200, 40, 40))  # stb reads them by their bytes, whatever the name
    png(folder_image, 300, 300, (40, 160, 40))
    png(stale, 300, 300, (40, 40, 200))

    def art(told):
        (d/'told').write_text(told)
        _, pics, _ = script(binary, work, 5, ['rest', 'shot @now.ppm'], '--page', 'now', '--cover', stale,
                            '--told', d/'told', now=NOW)
        return pics['now'].at(99, 133)  # the middle of the art's card (ui/page_now.c ART)

    def near(got, rgb):
        return all(abs(a - b) <= 8 for a, b in zip(got, rgb))
    got = art(f'out=other\ncover={embedded}\n')
    good &= check("Now Playing shows the embedded picture demo names, not the player's other file", near(got, (200, 40, 40)), str(got))
    got = art(f'out=other\ncover={folder_image}\n')
    good &= check('... and a folder image or a download the same way', near(got, (40, 160, 40)), str(got))
    got = art('out=other\ncover=\n')
    good &= check("no cover named is the placeholder, not the file a song before left", not near(got, (40, 40, 200)) and
                  not near(got, (200, 40, 40)), str(got))
    got = art('out=other\n')
    good &= check("demo naming none leaves --cover's file", near(got, (40, 40, 200)), str(got))
    # live: the next song's cover named while Now Playing is up is drawn as the file lands
    (d/'told').write_text(f'out=other\ncover={embedded}\n')
    (d/'now').write_text(NOW)
    run = Run(binary, work, 5, *home, '--now', d/'now', '--cover', stale, '--told', d/'told', '--clock', '12:00')
    try:
        run.rest()
        run.key(KEY_CENTRE)  # Home's Playing row
        run.rest()
        before = shot.pixel(run.picture(), 99, 133)
        (d/'told.tmp').write_text(f'out=other\ncover={folder_image}\n')
        (d/'told.tmp').rename(d/'told')
        run.rest()
        after = shot.pixel(run.picture(), 99, 133)
        good &= check("live, demo's new cover is drawn as its file lands", near(before, (200, 40, 40)) and near(after, (40, 160, 40)),
                      f'{before} {after}')
    finally:  # Home's Return opens no way out: the program is ended
        run.proc.kill()
        run.proc.wait(timeout=5)
        os.close(run.queue)
    return good


def routes(binary, work):
    """Ended and started again, q2ui opens the pages it had open, each on its row."""
    good = True
    r = Run(binary, work, 60, '--page', 'home')
    r.rest()
    r.key(KEY_CENTRE)   # Library
    time.sleep(0.6)
    r.ticks(2)          # Artists
    r.key(KEY_CENTRE)
    time.sleep(0.6)
    r.ticks(3)          # the fourth artist
    before = r.rest()
    was = r.picture()
    r.proc.terminate()
    r.proc.wait(timeout=5)
    os.close(r.queue)
    again = Run(binary, work, 60, '--page', 'home', at=r.dir)
    again.rest()
    now = again.picture()
    # below the status row, whose clock may have turned a minute between the two
    same = all(shot.pixel(was, x, y) == shot.pixel(now, x, y) for y in range(BAR_H, 320, 3) for x in range(0, 375, 5))
    good &= check('started again, it is on the page and the row it was ended on', same and before['ticks'] == 5, f"{before['ticks']} ticks")
    again.key(KEY_RETURN)
    time.sleep(0.6)
    again.key(KEY_RETURN)
    time.sleep(0.6)
    home = again.rest()
    good &= check('and Return goes back through the pages under it to Home, which Return does not leave',
                  again.proc.poll() is None and home['frames'] > 0)
    again.proc.terminate()
    again.proc.wait(timeout=5)
    os.close(again.queue)

    # what plays changes while q2ui runs: demo writes its file beside it and renames it into place, and Home shows it
    r = Run(binary, work, 60, '--page', 'home')
    idle = r.rest()
    first = [shot.pixel(r.picture(), x, 40 + 16) for x in range(20, 120)]
    (r.dir/'now.tmp').write_text(NOW)
    os.rename(r.dir/'now.tmp', r.dir/'now')
    time.sleep(0.5)
    playing = r.rest()
    row = [shot.pixel(r.picture(), x, 40 + 16) for x in range(20, 120)]
    good &= check('a song starting while Home is shown puts Playing on its first row, with no key pressed',
                  playing['frames'] > idle['frames'] and row != first, f"{idle['frames']} frames, then {playing['frames']}")
    woke, drew = r.quiet_second()
    good &= check('and Home is then at rest again', woke == 0 and drew == 0, f'{woke} wakes, {drew} frames in 1 s')
    r.proc.terminate()
    r.proc.wait(timeout=5)
    os.close(r.queue)
    return good


def status(binary, work):
    """The status row's glyphs (ui/status.c): the battery by its level and its cable, the play state, Wi-Fi's fan."""
    good = True

    def row(level=None, state='Discharging', wifi=None, now=None):
        d = pathlib.Path(tempfile.mkdtemp(dir=work))
        if level is not None:
            (d/'battery').mkdir()
            (d/'battery/capacity').write_text(f'{level}\n')
            (d/'battery/status').write_text(state + '\n')
        if wifi is not None:
            (d/'operstate').write_text('up\n')
            (d/'wireless').write_text(f'Inter-| sta-|\n face | tus |\n wlan0: 0000   {wifi}.  -50.  -256\n')
        _, pics, _ = script(binary, work, 5, ['rest', 'shot @row.ppm'], '--power', d, '--wifi', d/'operstate', '--wireless', d/'wireless', now=now)
        return pics['row']
    WHITE, GREEN, RED = (255, 255, 255), (0x34, 0xc7, 0x59), (0xff, 0x45, 0x3a)
    none, half = row(), row(50)
    lit = [x for x in range(280, 313) if half.at(x, 22) == WHITE]
    good &= check('with no battery to read, nothing stands at the right of the row', all(none.at(x, 22) == (0, 0, 0) for x in range(230, 320)))
    good &= check("half a charge fills the battery's left half in the text's ink", lit and min(lit) < 292 and 296 < max(lit) < 302, str(lit))
    good &= check('under a fifth it is red, and on a cable green',
                  any(row(12).at(x, 22) == RED for x in range(284, 292)) and any(row(60, 'Charging').at(x, 22) == GREEN for x in range(284, 296)))
    playing, paused = row(50, now=NOW), row(50, now=NOW.replace('state=1', 'state=2'))
    box = lambda p: [(x, y) for y in range(16, 29) for x in range(266, 281) if p.at(x, y) == WHITE]  # noqa: E731
    good &= check('a song playing puts three bars left of the battery, a paused one two wider bars, nothing stopped',
                  len({x for x, _ in box(playing)}) == 6 and len({x for x, _ in box(paused)}) == 6 and box(playing) != box(paused) and not box(half),
                  f'{sorted({x for x, _ in box(playing)})} {sorted({x for x, _ in box(paused)})}')
    # the volume changed from elsewhere (a side button): its level shows in the row for a moment, on a list
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'louder').write_text(NOW.replace('volume=40', 'volume=80'))
    _, pics, _ = script(binary, work, 5, ['rest', 'shot @before.ppm', f'plays {d}/louder', 'run 200', 'shot @shown.ppm', 'run 2000', 'shot @after.ppm'], now=NOW)
    bar = lambda p: [x for x in range(150, 226) if p.at(x, 23) == WHITE]  # noqa: E731
    good &= check('a volume change shows its level in the status row, four fifths of the track, and then goes',
                  not bar(pics['before']) and 55 <= len(bar(pics['shown'])) <= 64 and not bar(pics['after']),
                  f"{len(bar(pics['before']))} {len(bar(pics['shown']))} {len(bar(pics['after']))}")
    strong, weak = row(50, wifi=60), row(50, wifi=10)
    fan = lambda p: sum(1 for y in range(16, 29) for x in range(250, 281) if p.at(x, y) == WHITE)  # noqa: E731
    good &= check("Wi-Fi's fan is lit by its signal: all of it strong, its dot alone weak, none when not connected",
                  fan(strong) > fan(weak) * 2 > 0 and fan(half) == 0, f'{fan(strong)} {fan(weak)} {fan(half)}')
    return good & told(binary, work)


# the status glyphs demo's word draws (tools/art.py status_images), made up here as a colour each, at their boxes' sizes
TOLD_ART = {'status_wired_d': (14, (0, 200, 0)), 'status_noaudio_d': (10, (200, 0, 0)), 'status_bt_d': (9, (0, 0, 200)),
            'status_btdim_d': (9, (0, 0, 100)), 'status_car_d': (19, (100, 0, 100)), 'status_headphones_d': (14, (200, 200, 0)),
            'keylock_d': (14, (0, 200, 200)), 'status_offline_d': (14, (200, 0, 200)), 'status_wifi_x_d': (16, (100, 100, 0))}


def told(binary, work):
    """What only demo knows for the status row (patch/status_row.c status_row_state, written by q2ui_gate.c to
    /tmp/q2ui.status): where the sound goes, Bluetooth, the lock, a card app's sync and server, Wi-Fi's internet."""
    good = True
    icons = pathlib.Path(tempfile.mkdtemp(dir=work))
    for name, (w, rgb) in TOLD_ART.items():
        png(icons/f'oled_{name}.png', w, 14, rgb)
    for f in range(30):  # the sync arrows' frames, a colour each
        png(icons/f'oled_status_sync_{f}_d.png', 14, 14, (10 + f * 8, 50, 50))

    def row(said, wifi=False):
        d = pathlib.Path(tempfile.mkdtemp(dir=work))
        (d/'battery').mkdir()
        (d/'battery/capacity').write_text('50\n')
        (d/'battery/status').write_text('Discharging\n')
        (d/'told').write_text(''.join(f'{k}={v}\n' for k, v in said.items()))
        if wifi:
            (d/'operstate').write_text('up\n')
            (d/'wireless').write_text(' wlan0: 0000   60.  -50.  -256\n')
        _, pics, _ = script(binary, work, 5, ['rest', 'shot @row.ppm'], '--power', d, '--told', d/'told', '--icons', icons,
                            '--wifi', d/'operstate', '--wireless', d/'wireless')
        return pics['row']
    colour = lambda name: TOLD_ART[name][1]  # noqa: E731
    # the battery's body starts at 285.5, so the slots end at 279, GAP (6) left of it
    wire, ear, other = row({'out': 'wire'}), row({'out': 'none'}), row({'out': 'other'})
    good &= check('wired headphones, the deaf ear with no output, nothing for any other output: each in its box by the battery',
                  wire.at(272, 22) == colour('status_wired_d') and wire.at(264, 22) == (0, 0, 0) and
                  ear.at(274, 22) == colour('status_noaudio_d') and ear.at(268, 22) == (0, 0, 0) and
                  all(other.at(x, 22) == (0, 0, 0) for x in range(200, 280)),
                  f"{wire.at(272, 22)} {ear.at(274, 22)} {[x for x in range(200, 280) if other.at(x, 22) != (0, 0, 0)]}")
    rune, dim = row({'out': 'none', 'bt': 'rune'}), row({'out': 'none', 'bt': 'connecting'})
    good &= check('the Bluetooth rune stands left of the ear, GAP between, and dim while a device connects',
                  rune.at(274, 22) == colour('status_noaudio_d') and rune.at(258, 22) == colour('status_bt_d') and
                  dim.at(258, 22) == colour('status_btdim_d'), f'{rune.at(258, 22)} {dim.at(258, 22)}')
    headset = row({'out': 'other', 'bt': 'headset', 'btbatt': 19})
    good &= check("a headset that says its level: headphones, then its own battery, red under a fifth",
                  headset.at(253, 22) == colour('status_headphones_d') and headset.at(265, 23) == (0xff, 0x45, 0x3a),
                  f'{headset.at(253, 22)} {headset.at(265, 23)}')
    many = row({'out': 'other', 'sync': 2, 'syncpm': 500, 'lock': 1, 'offline': 1})
    good &= check("a download's ring half filled clockwise from 12 o'clock, then the padlock, then the cloud",
                  # the ring's 1.6 px line is mostly edge: its hue, not its whole colour
                  many.at(278, 23)[2] > 120 > many.at(278, 23)[0] and len(set(many.at(264, 23))) == 1 and many.at(264, 23)[0] > 20 and
                  many.at(250, 22) == colour('keylock_d') and many.at(231, 22) == colour('status_offline_d'),
                  f'{many.at(278, 23)} {many.at(264, 23)} {many.at(250, 22)} {many.at(231, 22)}')
    crossed = row({'out': 'other', 'net': 0}, wifi=True)
    good &= check('Wi-Fi linked with no internet is the fan crossed out', crossed.at(271, 22) == colour('status_wifi_x_d'), str(crossed.at(271, 22)))
    # the arrows turn while a card app checks its server: a frame of theirs every 33 ms
    d = pathlib.Path(tempfile.mkdtemp(dir=work))
    (d/'told').write_text('out=other\nsync=1\n')
    lines = ['run 300'] + [f'shot @s{k}.ppm\nrun 70' for k in range(6)]  # no rest: the arrows never stop
    _, pics, _ = script(binary, work, 5, lines, '--told', d/'told', '--icons', icons)
    frames = {pics[f's{k}'].at(306, 23) for k in range(6)}  # no battery here: the arrows end at the row's right, 313
    good &= check('the sync arrows turn, a frame of theirs every 33 ms', len(frames) >= 5, str(frames))
    # and live, where only the loop's own wake-up moves them: both panel pages read, as a page is never at rest
    run = Run(binary, work, 5, '--told', d/'told', '--icons', icons, '--clock', '12:00')
    try:
        seen = set()
        for _ in range(8):
            time.sleep(0.07)
            seen |= {shot.pixel(shot.page(run.dir/'fb', k), 306, 23) for k in (0, 1)}
        good &= check('live, the arrows turn by themselves', len(seen) >= 5, str(len(seen)))
    finally:
        run.end()
    # demo writing the file anew is drawn at once, by the watch of its folder: a live run
    (d/'told').write_text('out=other\n')
    run = Run(binary, work, 5, '--told', d/'told', '--icons', icons, '--clock', '12:00')
    try:
        run.rest()
        before = shot.pixel(run.picture(), 306, 22)
        (d/'told.tmp').write_text('out=wire\n')
        (d/'told.tmp').rename(d/'told')
        after = shot.pixel(run.picture() if run.rest() else b'', 306, 22)
        good &= check("demo's new file is drawn as it lands", before == (0, 0, 0) and after == colour('status_wired_d'), f'{before} {after}')
    finally:
        run.end()
    return good


def panel(binary, work):
    """The panel told to take 24 bits a pixel (ui/panel.c), against a file standing in for the DSI host's registers:
    sent only to a link that reads as the Q2's, only when its FIFOs have room, and never with --panel-mode keep."""
    good = True
    CODING, PKT, HDR, PLD, STATUS = 0x10, 0x3c, 0x6c, 0x70, 0x74

    def run(words, *options):
        d = pathlib.Path(tempfile.mkdtemp(dir=work))
        regs = bytearray(4096)
        for at, value in words.items():
            struct.pack_into('<I', regs, at, value)
        (d/'dsi').write_bytes(regs)
        script(binary, work, 1, ['rest'], '--panel-mem', d/'dsi', *options)
        after = (d/'dsi').read_bytes()
        changed = {at for at in range(0, 4096, 4) if after[at:at + 4] != bytes(regs[at:at + 4])}
        return struct.unpack_from('<I', after, PLD)[0], struct.unpack_from('<I', after, HDR)[0], changed
    q2 = {CODING: 5, PKT: 0x140}
    pld, hdr, changed = run(q2)
    good &= check('the panel is sent COLMOD 0x77 as one DCS long write, and no other register is written',
                  (pld, hdr) == (0x773a, 0x239) and changed == {PLD, HDR}, f'{pld:#x} {hdr:#x} {sorted(changed)}')
    for what, words, options in (('a link that is not the Q2\'s', {CODING: 0, PKT: 0x140}, ()),
                                 ('a line of another length', {CODING: 5, PKT: 0x1e0}, ()),
                                 ('FIFOs that stay full', {**q2, STATUS: 0x0a}, ()),
                                 ('--panel-mode keep', q2, ('--panel-mode', 'keep'))):
        _, _, changed = run(words, *options)
        good &= check(f'with {what} nothing is written', not changed, str(sorted(changed)))
    return good


def reload(binary, work):
    """kill -HUP: q2ui starts itself again in place, the same process, on the pages it had open, with demo's settings
    pipe (--ctl) still open: it has no other way back."""
    pipe = os.pipe()
    r = Run(binary, work, 20, '--page', 'home', '--ctl', str(pipe[1]), fds=pipe[1:])
    os.close(pipe[1])
    end = f'/proc/{r.proc.pid}/fd/{pipe[1]}'
    held = os.path.exists(end)
    r.rest()
    r.key(KEY_CENTRE)  # Library
    time.sleep(0.6)
    r.ticks(1)
    before = r.rest()
    was = r.picture()
    r.proc.send_signal(signal.SIGHUP)
    time.sleep(1.0)
    after = r.rest()
    now = r.picture()
    same = all(shot.pixel(was, x, y) == shot.pixel(now, x, y) for y in range(BAR_H, 320, 3) for x in range(0, 375, 5))
    good = check('SIGHUP starts q2ui again in place: the same process, counting from nothing, on the page and row it had',
                 r.proc.poll() is None and before['ticks'] == 1 and after['ticks'] == 0 and same,
                 f"exit {r.proc.poll()}, ticks {before['ticks']} then {after['ticks']}, same picture {same}")
    kept = os.path.exists(end) and os.readlink(end) == f'pipe:[{os.fstat(pipe[0]).st_ino}]'
    good &= check("demo's settings pipe is open in q2ui before SIGHUP and still after", held and kept, f'{held} {kept}')
    r.hold(KEY_RETURN)  # Control Center, in the program started again
    r.rest()
    r.key(KEY_CENTRE)   # the wheel's ring, on Wi-Fi
    r.rest()
    r.key(KEY_CENTRE)   # its Wi-Fi switch, off as no told file says otherwise
    r.rest()
    os.set_blocking(pipe[0], False)
    try:
        said = [s for s in os.read(pipe[0], 4096).decode().splitlines() if not s.startswith('click ')]  # its settings
    except BlockingIOError:
        said = []
    good &= check("after SIGHUP a Control Center switch still reaches demo down that pipe", said == ['set wifi 1'], str(said))
    r.proc.terminate()
    r.proc.wait(timeout=5)
    os.close(r.queue)
    os.close(pipe[0])
    return good


def swap(binary, work):
    """A newer program put at q2ui's path is what runs after SIGHUP: the path is run again, not the file the process
    was started from (a `vamp ui-push` mounts the new one there)."""
    d = folder(work, 5)
    path = d/'q2ui'
    shutil.copy(binary, path)
    (d/'input').mkdir()
    for k in (1, 3):
        os.mkfifo(d/'input'/f'event{k}')
    proc = subprocess.Popen(base_args(path, d) + ['--input', d/'input'])
    for _ in range(50):  # until it has drawn and come to rest: its loop is where the signal is acted on
        if (d/'stats.json').exists():
            break
        time.sleep(0.1)
    time.sleep(0.3)
    stand_in = d/'q2ui.new'  # stands in for the newer q2ui: it says it ran, with what it was given, and stays
    stand_in.write_text(f'#!/bin/sh\necho "$@" > {d}/ran\nexec sleep 20\n')
    stand_in.chmod(0o755)
    os.rename(stand_in, path)
    proc.send_signal(signal.SIGHUP)
    for _ in range(50):  # up to five seconds: this machine may be busy
        if (d/'ran').exists() and (d/'ran').read_text():
            break
        time.sleep(0.1)
    ran = (d/'ran').read_text() if (d/'ran').exists() else None
    good = check('after SIGHUP the program now at the path runs, in the same process, with the same arguments',
                 ran is not None and '--input' in ran and proc.poll() is None, f'ran: {ran!r}, exit {proc.poll()}')
    proc.kill()
    proc.wait(timeout=5)
    return good


def live(binary, work):
    good = True
    # the first frame, three ticks, a rest
    r = Run(binary, work, 300)
    first = r.rest()
    good &= check('the first frame shows the platter on the first song', bar_rows(r.picture()) == [1], str(bar_rows(r.picture())))
    good &= check('300 songs were read', first['rows'] == 300, str(first['rows']))
    r.ticks(3)
    moved = r.rest()
    good &= check('three ticks are three rows', moved['ticks'] == 3 and bar_rows(r.picture()) == [4],
                  f"{moved['ticks']} ticks, platter on {bar_rows(r.picture())}")
    # asleep in poll until input or the clock's next minute, which this second is clear of
    woke, drew = r.quiet_second()
    good &= check('a list at rest draws no frame', drew == 0 and r.stats()['frames'] == moved['frames'],
                  f"{moved['frames']} then {r.stats()['frames']}")
    good &= check('a list at rest does not wake', woke == 0, f'{woke} wakes in 1 s')
    r.ticks(-2)
    back = r.rest()
    good &= check('two ticks back are two rows up', bar_rows(r.picture()) == [2], str(bar_rows(r.picture())))
    good &= check('a frame turns only what changed', back['px_turned_per_frame'] < 375 * 320 // 2, str(back['px_turned_per_frame']))
    r.ticks(12)
    paced = r.rest()
    # by CPU time: this machine's other work can hold a frame up, which says nothing of the frame
    good &= check('no frame of twelve glides took a refresh of CPU', paced['cpu_over_budget'] == 0 and paced['frames'] - back['frames'] >= 40,
                  f"{paced['cpu_over_budget']} over, {paced['frames'] - back['frames']} frames, worst draw {paced['draw_us_max']} us")
    code, last = r.end()
    good &= check('Return ends it cleanly', code == 0 and last['ticks'] == 17, f'exit {code}, {last["ticks"]} ticks')

    # Centre plays the row through the player's queue: the list is the queue, from the song chosen
    r = Run(binary, work, 40)
    r.rest()
    r.ticks(2)
    r.key(KEY_CENTRE)
    time.sleep(0.4)
    lines, ids = r.sent(), (r.dir/'list').read_text().split() if (r.dir/'list').exists() else []
    want = [row[0] for row in sqlite3.connect(r.dir/'library.db').execute('select id from songtable order by name collate nocase, id')]
    good &= check('Centre on the third song sends one play, from that song, unshuffled',
                  len(lines) == 1 and lines[0].split()[1:] == ['play', '2', '0', '40'], str(lines))
    good &= check("the list it names is every song's id in the list's order", ids == [str(i) for i in want], f'{len(ids)} ids')
    r.key(KEY_RETURN)  # Now Playing, which the play opened, gives the list back
    time.sleep(0.6)
    r.ticks(-3)
    r.key(KEY_CENTRE)
    time.sleep(0.4)
    lines = r.sent()
    good &= check('Centre on Shuffle sends the same list shuffled', len(lines) == 1 and lines[0].split()[1:] == ['play', '0', '1', '40'], str(lines))
    r.key(KEY_RETURN)
    time.sleep(0.6)
    # ended from outside, as demo ends it for a window of its own: the next start opens on the row it rested on
    r.ticks(5)
    r.rest()
    r.proc.terminate()
    r.proc.wait(timeout=5)
    os.close(r.queue)
    again = Run(binary, work, 40, at=r.dir)
    again.rest()
    good &= check('started again, it opens on the row it was ended on', bar_rows(again.picture()) == [5], str(bar_rows(again.picture())))
    # a dark screen: the press that wakes it moves nothing
    light = again.dir/'backlight/panel'
    light.mkdir(parents=True)
    (light/'bl_power').write_text('4\n')
    (light/'brightness').write_text('80\n')
    again.proc.terminate()
    again.proc.wait(timeout=5)
    os.close(again.queue)
    dark = Run(binary, work, 40, at=again.dir)
    dark.rest()
    dark.ticks(2)
    quiet = dark.rest()['ticks']
    (light/'bl_power').write_text('0\n')
    time.sleep(0.6)
    dark.ticks(2)
    lit = dark.rest()['ticks']
    good &= check('with the backlight off the wheel moves nothing; lit again, it does', quiet == 0 and lit == 2, f'{quiet} then {lit} ticks')
    dark.end()

    # a server library's catalogue (docs/server-library.md): its songs are listed with the card's
    cat = pathlib.Path(work)/'catalogue.db'
    db = sqlite3.connect(cat)
    db.execute('create table servertable(id integer, name, songer, album, track integer)')  # songtable's columns that are read
    db.executemany('insert into servertable values (?, ?, ?, ?, ?)',
                   [(0x40000000 + i, f'Server {i}', 'Artist', 'Album', i + 1) for i in range(50)])
    db.commit()
    db.close()
    r = Run(binary, work, 300, '--catalogue', cat)
    rows = r.rest()['rows']
    good &= check("a catalogue's 50 songs are listed with the card's 300", rows == 350, str(rows))
    r.end()
    return good


class Client:
    """A connection to q2ui's Q2UI/1 socket (ui/notice.c), its lines read with a time limit."""

    def __init__(self, path, hello=True):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(str(path))
        self.buf = b''
        if hello:
            self.say('Q2UI/1')
            assert self.line() == 'Q2UI/1 ok'

    def say(self, line):
        self.s.sendall(line.encode() + b'\n')

    def line(self, timeout=3.0):
        """The next line; None when none came in timeout, '' when the connection ended."""
        self.s.settimeout(timeout)
        while b'\n' not in self.buf:
            try:
                got = self.s.recv(4096)
            except socket.timeout:
                return None
            if not got:
                return ''
            self.buf += got
        line, self.buf = self.buf.split(b'\n', 1)
        return line.decode()

    def ask(self, line):
        self.say(line)
        return self.line()

    def close(self):
        self.s.close()


def transcripts(text):
    """A page's conformance blocks (```conformance, or the protocol page's unnamed block under its Conformance
    heading), each a list of (kind, text): '>' sent, '<' the next answer exactly, '<~' the next answer matching."""
    out, cur, under = [], None, False
    for line in text.splitlines():
        if line.startswith('## '):
            under = line.startswith('## Conformance')
        if cur is None and (line.strip() == '```conformance' or (under and line.strip() == '```')):
            cur = []
        elif cur is not None and line.strip() == '```':
            out.append(cur)
            cur = None
        elif cur is not None and line:
            kind, _, rest = line.partition(' ')
            if kind == '<' and rest.startswith('~ '):
                kind, rest = '<~', rest[2:]
            assert kind in ('>', '<', '<~'), line
            cur.append((kind, rest))
    return out


def replay(path, block):
    """One transcript against the socket at path; '' when it held, else the step that differed."""
    c = Client(path, hello=False)
    try:
        for n, (kind, text) in enumerate(block, 1):
            if kind == '>':
                c.say(text)
                continue
            got = c.line()
            if (got != text) if kind == '<' else (got is None or not re.fullmatch(text, got)):
                return f'step {n}: expected {text!r}, said {got!r}'
        return ''
    finally:
        c.close()


def lit_folder(work, n):
    """A live run's folder made ahead (Run at=), so the backlight it reads is there from its start, lit."""
    d = folder(work, n)
    (d/'input').mkdir()
    for k in (1, 3):
        os.mkfifo(d/'input'/f'event{k}')
    os.mkfifo(d/'cmdq')
    (d/'backlight/panel').mkdir(parents=True)
    (d/'backlight/panel/bl_power').write_text('0\n')
    (d/'backlight/panel/brightness').write_text('80\n')
    return d


def notice_lines(trace_text):
    return [line.split(' ', 1)[1] for line in trace_text.splitlines() if ' said ' in line or ' notice ' in line]


def notices(binary, work):
    """Q2UI/1 (ui/notice.c, docs/ui-rewrite.md#notices): the protocol's transcripts against a live q2ui, then the
    card and the toast as the owner sees and answers them."""
    good = True
    d = lit_folder(work, 20)
    pipe = os.pipe()
    os.set_blocking(pipe[0], False)
    r = Run(binary, work, 20, '--ctl', str(pipe[1]), at=d, fds=pipe[1:])
    os.close(pipe[1])
    sock = d/'ui.sock'

    def wakes():
        try:
            return [x for x in os.read(pipe[0], 4096).decode().splitlines() if x == 'wake']
        except BlockingIOError:
            return []
    r.rest()
    blocks = transcripts((ROOT/'docs/ui-rewrite.md').read_text())
    spec = subprocess.run(['git', '-C', str(ROOT), 'show', 'feat/vamp-os-base:docs/platform/notices.md'], capture_output=True, text=True)
    if spec.returncode == 0:
        blocks += transcripts(spec.stdout)
    failed = [f'block {k}: {why}' for k, b in enumerate(blocks) if (why := replay(sock, b))]
    good &= check(f'the conformance transcripts hold against q2ui ({len(blocks)} blocks, the protocol page\'s '
                  f'{"among them" if spec.returncode == 0 else "not in this repository"})', len(blocks) >= 2 and not failed, '; '.join(failed))
    for _ in range(80):  # the transcripts' toasts gone, one after another
        time.sleep(0.1)
        if not r.stats()['toast'] and not r.stats()['toasts_waiting']:
            break
    r.rest()
    wakes()
    before = r.picture()
    a = Client(sock)
    said = a.ask('ask q-1 Receive%20files? Pixel%208%20sends%203%20files Accept Decline')
    r.rest()
    st, card = r.stats(), r.picture()
    scrim = shot.pixel(before, 30, 93), shot.pixel(card, 30, 93)
    halved = all(abs(b // 2 - c) <= 2 for b, c in zip(*scrim))
    good &= check('an ask is answered ok and its card comes up: the page under black at half, the card, the first choice lit',
                  said == 'ok' and st['card'] == 'q-1' and halved and shot.pixel(card, 46, 168) == (0x1c, 0x1c, 0x1e) and
                  shot.pixel(card, 55, 196) == ACCENT and shot.pixel(card, 55, 228) == (0x1c, 0x1c, 0x1e),
                  f"said {said!r}, card {st['card']!r}, platter {scrim}, card {shot.pixel(card, 46, 168)}, "
                  f"first {shot.pixel(card, 55, 196)}, second {shot.pixel(card, 55, 228)}")
    good &= check('a question asked with the screen lit sends no wake', wakes() == [])
    r.ticks(1)
    r.rest()
    moved = shot.pixel(r.picture(), 55, 228) == ACCENT and shot.pixel(r.picture(), 55, 196) == (0x1c, 0x1c, 0x1e)
    r.key(KEY_CENTRE)
    got = a.line()
    r.rest()
    after = r.picture()
    same = all(shot.pixel(before, x, y) == shot.pixel(after, x, y) for y in range(BAR_H, 320, 7) for x in range(0, 375, 7))
    if not same and os.environ.get('Q2UI_DEBUG'):
        print([(x, y, shot.pixel(before, x, y), shot.pixel(after, x, y)) for y in range(BAR_H, 320, 7) for x in range(0, 375, 7)
               if shot.pixel(before, x, y) != shot.pixel(after, x, y)][:20])
    good &= check('the wheel moves the bar to the second choice and Centre answers it, from 1; the card goes and the page is '
                  'as it was, its list not moved', moved and got == 'answer q-1 2' and r.stats()['card'] == '' and same,
                  f'moved {moved}, said {got!r}, page back {same}')
    a.ask('ask q-2 Title Body Yes No')
    r.rest()
    r.key(KEY_RETURN)
    got = a.line()
    r.rest()
    good &= check('Return dismisses the card: answer 0, and Return does not end q2ui under it', got == 'answer q-2 0' and r.proc.poll() is None,
                  f'{got!r}, exit {r.proc.poll()}')
    a.ask('ask q-3 Title Body Yes No')
    r.rest()
    asking = r.picture()
    r.hold(KEY_RETURN)
    r.rest()
    held = r.picture()
    got = a.line(timeout=0.5)
    if got is None:
        r.key(KEY_RETURN)
        got = a.line()
    r.rest()
    under = all(shot.pixel(asking, x, y) == shot.pixel(held, x, y) for y in range(0, 320, 5) for x in range(0, 40, 3))
    good &= check('a held Return opens no Control Center while the card asks: the page under the scrim stays', under and
                  got == 'answer q-3 0', f'page under kept {under}, said {got!r}')
    # one card at a time, each answer to its own connection; a connection that ends takes its question
    b = Client(sock)
    first, second = a.ask('ask a-1 First Body One'), b.ask('ask b-1 Second Body Two')
    r.rest()
    on1 = r.stats()['card']
    r.key(KEY_CENTRE)
    got_a, got_b = a.line(), b.line(timeout=0.5)
    r.rest()
    on2 = r.stats()['card']
    good &= check('two questions are one card at a time in the order asked, and each answer reaches only its asker',
                  (first, second, on1, got_a, got_b, on2) == ('ok', 'ok', 'a-1', 'answer a-1 1', None, 'b-1'),
                  str((first, second, on1, got_a, got_b, on2)))
    b.close()
    time.sleep(0.3)
    r.rest()
    good &= check('a connection that ends takes its question off the card', r.stats()['card'] == '', r.stats()['card'])
    # the answers that refuse
    asked = [a.ask(f'ask w-{k} T B Yes') for k in range(5)]
    withdrawn = [(a.ask(f'withdraw w-{k}'), a.line()) for k in range(4)]
    good &= check('four questions wait at most: the fifth is err busy; each withdrawn is ok, then answer gone',
                  asked == ['ok'] * 4 + ['err busy'] and withdrawn == [('ok', f'answer w-{k} gone') for k in range(4)],
                  f'{asked} {withdrawn}')
    refused = [a.ask('ask x-1 ' + 'T' * 41 + ' B Yes'), a.ask('ask x-1 T ' + 'B' * 161 + ' Yes'), a.ask('ask x-1 T B ' + 'Y' * 17),
               a.ask('ask x-1 T B A B C D'), a.ask('ask Bad_id T B Yes'), a.ask('ask x-1 T%2 B Yes'), a.ask('toast')]
    fits = a.ask('ask x-2 ' + 'T' * 40 + ' ' + 'B' * 160 + ' ' + 'Y' * 16)
    a.ask('withdraw x-2'), a.line()
    good &= check('a title over 40 bytes, a body over 160, a choice over 16 are err text; five choices err ask; a bad id err id; '
                  'a broken % err text; and the limits themselves fit',
                  refused == ['err text', 'err text', 'err text', 'err ask', 'err id', 'err text', 'err toast'] and fits == 'ok',
                  f'{refused} {fits}')
    c = Client(sock, hello=False)
    c.say('x' * 600)
    long_line = c.line()
    c.say('Q2UI/1')
    hello_after = c.line()
    c.close()
    good &= check('a line over 512 bytes is err line and read past; the next line is taken', long_line == 'err line' and hello_after == 'Q2UI/1 ok',
                  f'{long_line!r} {hello_after!r}')
    # the screen
    light = d/'backlight/panel/bl_power'
    light.write_text('4\n')
    time.sleep(0.2)
    a.ask('toast Dark')
    time.sleep(0.5)
    toast_woke = wakes()
    a.ask('ask s-1 T B Yes')
    time.sleep(1.5)
    ask_woke = wakes()
    held_dark = r.stats()['card'] == 's-1'
    light.write_text('0\n')
    time.sleep(1.5)
    light.write_text('4\n')
    got = a.line(timeout=3)
    good &= check('a question asked with the screen dark lights it (wake down demo\'s pipe) and a toast does not; it waits while '
                  'dark until seen lit, then the screen going off answers 0',
                  toast_woke == [] and ask_woke == ['wake'] and held_dark and got == 'answer s-1 0',
                  f'toast {toast_woke}, ask {ask_woke}, held while dark {held_dark}, then {got!r}')
    light.write_text('0\n')
    time.sleep(3)
    # who asks: an app by its package's name, the system's uids as Vamp
    uid = os.getuid()
    names = []
    for apps in ('', f'radio {uid} notices,ask\n', f'radio {uid} -\n'):
        (d/'apps').write_text(apps)
        (d/'.radio').mkdir(exist_ok=True)
        (d/'.radio/q2app').write_text('name=Radio\napi=1\n')
        e = Client(sock)
        e.ask('ask n-1 T B Yes')
        time.sleep(0.4)
        names.append(r.stats()['card_from'])
        e.close()
        time.sleep(0.4)
    system = 'Vamp' if uid < 10000 else 'Unknown app'
    good &= check('the card names who asks: the app its uid is the package user of, by its q2app name=; else the system',
                  names == [system, 'Radio', system], str(names))
    # watch: the page in front, now and as it changes
    w = Client(sock)
    w.say('watch')
    now = w.line(), w.line()
    r.key(KEY_RETURN)  # Songs is the first page here: its Return ends q2ui, so the change is watched on its way out
    later = w.line()
    good &= check('watch: ok, the page in front at once, and the connection ends with q2ui', now == ('ok', 'front songs') and later == '',
                  f'{now} {later!r}')
    r.proc.wait(timeout=5)
    good &= check('q2ui ending takes its socket with it', not sock.exists())
    os.close(r.queue)
    os.close(pipe[0])

    # a watched page change, Home to the Library
    d2 = lit_folder(work, 20)
    r = Run(binary, work, 20, '--page', 'home', at=d2)
    r.rest()
    w = Client(d2/'ui.sock')
    w.say('watch')
    first = w.line(), w.line()
    r.key(KEY_CENTRE)
    r.rest()
    nxt = w.line()
    good &= check('watch tells the page in front as it changes, by its route name', first == ('ok', 'front home') and nxt == 'front library',
                  f'{first} {nxt!r}')
    r.proc.terminate()
    r.proc.wait(timeout=5)
    os.close(r.queue)

    # on the program's own clock: the toast's time and queue, the timeout, a press begun before the card
    trace_lines = []
    for lines in (['notice toast One', 'notice toast Two', 'run 2400', 'run 400', 'run 2300', 'run 500'],
                  ['notice ask t-1 T B Yes', 'run 59000', 'run 2000'],
                  ['notice ask p-1 T B Yes', 'notice toast Later', 'run 500', 'key return', 'run 600'],
                  ['down centre', 'notice ask d-1 T B Yes', 'run 300', 'up centre', 'run 300', 'key return', 'run 300']):
        dd = folder(work, 20)
        text = '\n'.join(lines) + '\n'
        (dd/'script').write_text(text)
        subprocess.run(base_args(binary, dd) + ['--script', dd/'script', '--trace', dd/'trace', '--clock', '12:00'], timeout=120)
        trace_lines.append((dd/'trace').read_text())
    tl = trace_lines[0].splitlines()

    shown = [(int(x.split()[0][2:]) - 1016667, x.split(' toast=')[1]) for x in tl if ' notice ' in x]

    def toast_at(ms):
        rows = [v for t, v in shown if t <= ms * 1000]
        return rows[-1] if rows else '-'
    gone = shown[-1][0] / 1000 if shown else 0
    good &= check('a toast lasts 2.5 s and the next waits behind it, shown as the first has gone (150 ms): both gone by 5.3 s',
                  toast_at(2450) == 'on waiting=1' and toast_at(2550) == 'leaving waiting=1' and toast_at(2700) == 'on waiting=0' and
                  toast_at(5100) == 'on waiting=0' and 5250 <= gone <= 5300,
                  f'{toast_at(2450)} | {toast_at(2550)} | {toast_at(2700)} | {toast_at(5100)} | last at {gone} ms')
    t2 = notice_lines(trace_lines[1])
    good &= check('an unanswered question answers 0 after 60 s, not before',
                  [x for x in t2 if 'answer' in x] == ['said answer t-1 0'] and
                  int(next(x for x in trace_lines[1].splitlines() if 'answer' in x).split()[0][2:]) >= 1000000 + 60000000,
                  str([x for x in trace_lines[1].splitlines() if 'said' in x]))
    t3 = trace_lines[2].splitlines()
    waited = all('toast=-' in x for x in t3 if ' card=p-1 ' in x and 'level=1.000' in x)
    shown_after = any('toast=on' in x and ' card=- ' in x for x in t3)
    good &= check('a toast sent while a question is on the card waits for the card to go', waited and shown_after,
                  f'waited {waited}, shown after {shown_after}')
    t4 = [x for x in notice_lines(trace_lines[3]) if 'answer' in x]
    good &= check('the release of a Centre pressed before the card came answers nothing; Return then answers 0',
                  t4 == ['said answer d-1 0'], str(t4))
    return good


def main():
    try:  # the build's inputs; a compile that fails with them all there is a failure, not a skip
        ui.stb(), ui.stb_image(), ui.sqlite_include()
        if not ui.HOST_SQLITE.exists():
            raise SystemExit(f'no {ui.HOST_SQLITE}')
    except (SystemExit, OSError) as e:
        print(f'skipped: q2ui cannot be built here ({e})')
        return 0
    binary, engine = ui.build('host'), ui.build('host', 'q2ui-test')
    only = sys.argv[1:]
    good = True
    with tempfile.TemporaryDirectory() as work:
        if not only or 'engine' in only:
            d = folder(work, 1)
            good &= subprocess.run([str(engine), d/'fb', d/'fonts'], timeout=60).returncode == 0
        if not only or 'scripts' in only:
            good &= scripts(binary, work)
        if not only or 'pages' in only:
            good &= pages(binary, work)
        if not only or 'live' in only:
            good &= live(binary, work)
            good &= routes(binary, work)
            good &= reload(binary, work)
            good &= swap(binary, work)
        if not only or 'panel' in only:
            good &= panel(binary, work)
        if not only or 'status' in only:
            good &= status(binary, work)
        if not only or 'az' in only:
            good &= az_index(binary, work)
        if not only or 'captions' in only:
            good &= captions(binary, work, ('--page', 'home'))
        if not only or 'notices' in only:
            good &= notices(binary, work)
    print('q2ui: ' + ('all checks passed' if good else 'FAILED'))
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
