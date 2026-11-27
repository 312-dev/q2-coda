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
import shutil
import signal
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
            '--wireless', d/'wireless']


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


def script(binary, work, n, lines, *options, now=None, code=0, queue=False):
    """q2ui run over a script; the trace (a dict a frame), the pictures it saved by name, and the stats. now is what
    plays (the text of demo's file for it), code the exit code expected, and with queue the player's queue is a FIFO
    whose lines come back as a fourth value with the list file's ids."""
    d = folder(work, n)
    if now:
        (d/'now').write_text(now)
    fifo = None
    if queue:
        os.mkfifo(d/'cmdq')
        fifo = os.open(d/'cmdq', os.O_RDWR | os.O_NONBLOCK)
    text = '\n'.join(lines).replace('@', str(d) + '/') + '\n'
    (d/'script').write_text(text)
    done = subprocess.run(base_args(binary, d) + ['--script', d/'script', '--trace', d/'trace', '--clock', '12:00', *options],
                          timeout=120)
    if done.returncode != code:
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
    return trace, pictures, stats, (sent, ids, d)


class Run:
    """One live q2ui over a library of n songs, with its panel file, inputs and stats in a folder of its own."""

    def __init__(self, binary, work, n, *options, at=None):
        self.dir = at or folder(work, n)
        if not at:
            (self.dir/'input').mkdir()
            for k in (1, 3):
                os.mkfifo(self.dir/'input'/f'event{k}')
            os.mkfifo(self.dir/'cmdq')
        self.queue = os.open(self.dir/'cmdq', os.O_RDWR | os.O_NONBLOCK)  # demo's end of the player's queue
        self.proc = subprocess.Popen(base_args(binary, self.dir) + ['--input', self.dir/'input', *options])
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

    def picture(self):
        return shot.page(self.dir/'fb', self.stats()['page'])

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
    return good


def pages(binary, work):
    """Home, the Library's lists and Now Playing (docs/ui-rewrite.md#parity), by script."""
    good = True
    home = ('--page', 'home')
    # Home: the list at the left, the pane at the right; its first row is Playing while something is
    trace, pics, _ = script(binary, work, 60, ['rest', 'shot @idle.ppm', 'tick 9', 'rest'], *home)
    idle_rows = trace[-1]['sel'] + 1
    trace, pics2, _ = script(binary, work, 60, ['rest', 'shot @playing.ppm', 'tick 9', 'rest'], *home, now=NOW)
    good &= check('Home has one row more while something plays', (idle_rows, trace[-1]['sel'] + 1) == (7, 8), f"{idle_rows} and {trace[-1]['sel'] + 1}")
    good &= check("Home's platter keeps to the list's side and the pane is drawn beside it",
                  pics['idle'].blue(100, 40 + 3) and not pics['idle'].blue(250, 40 + 3) and pics['idle'].at(280, 160) != (0, 0, 0))
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
    _, _, _, (sent, _, _) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'key centre', 'run 100', 'tick 1', 'run 100'], *home, now=NOW, queue=True)
    good &= check('there Centre is play and pause, and a tick down the wheel is one step quieter',
                  [line.split(' ', 1)[1] for line in sent] == ['toggle', 'volume 39'], str(sent))
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
                          ('mode_queue_d', (90, 90, 90)), ('np_fav_d', (250, 0, 0)), ('np_unfav_d', (90, 90, 90))):
            png(d/f'oled_{name}.png', 16, 16, rgb)
    icons = pathlib.Path(tempfile.mkdtemp(dir=work))
    glyphs(icons)
    marks = {}
    for what, text in (('off', NOW), ('on', NOW.replace('id=5', 'id=5\nshuffle=1\nrepeat=2\nhearts=1'))):
        _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'run 600', 'shot @np.ppm'], *home, '--icons', icons, now=text)
        marks[what] = [pics['np'].at(64, 286), pics['np'].at(228, 286), pics['np'].at(200, 160)]
    good &= check("Now Playing's shuffle, repeat and heart are drawn by what is set",
                  marks['off'] == [(90, 90, 90)] * 3 and marks['on'] == [(0, 0, 250), (0, 250, 0), (250, 0, 0)], str(marks))
    # a tick shows the volume's level in the bar's place, white, and it gives the bar back
    _, pics, _ = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick -1', 'run 200', 'shot @volume.ppm', 'run 2000', 'shot @after.ppm'], *home, now=NOW)
    level, bar = pics['volume'].at(80, 231), pics['after'].at(36, 231)
    good &= check('a tick on Now Playing shows the volume in the bar, then the bar comes back',
                  level == (255, 255, 255) and pics['volume'].at(36, 231) != ACCENT and bar == ACCENT, f'{level} {bar}')
    # Library > Artists > the first artist's songs > Centre on the second: that artist's songs are the queue
    walk = ['rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'rest', 'key centre', 'rest', 'tick 1', 'rest', 'key centre', 'run 200']
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
    walk = ['rest', 'key centre', 'rest', 'tick 2', 'rest', 'key centre', 'rest'] + ['tick 1', 'rest'] * k + ['key centre', 'rest', 'key centre', 'run 200']
    _, _, _, (sent, ids, d) = script(binary, work, 60, walk, *home, queue=True)
    want = by['track, name collate nocase, id'][albums[k]]
    good &= check("an album's songs are its tracks in order", len(sent) == 1 and ids == want, f'{ids} for {want}')
    # a row whose page is demo's: the page is named for demo and q2ui ends with the code that says so
    _, _, _, (sent, _, d) = script(binary, work, 60, ['rest', 'tick 1', 'rest', 'key centre', 'run 100'], *home, code=3, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check('Centre on Radio asks demo for its page and ends with code 3', asked == 'tuner_page\n' and sent == [], repr(asked))
    _, _, _, (sent, _, d) = script(binary, work, 60, ['rest', 'key centre', 'rest', 'tick 4', 'rest', 'key centre', 'run 100'], *home, code=3, queue=True)
    asked = (d/'ask').read_text() if (d/'ask').exists() else ''
    good &= check("and Ratings, in the Library, for demo's Ratings", asked == 'ratings_page\n', repr(asked))
    # the last row leaves q2ui for demo's menus: the program ends, with the code of a plain end
    trace, _, _ = script(binary, work, 60, ['rest', 'tick 9', 'rest', 'key centre', 'run 100', 'tick -1', 'run 100'], *home, code=0)
    good &= check('Centre on Old Menus ends q2ui', trace[-1]['sel'] == 6, str(trace[-1]['sel']))
    return good


def routes(binary, work):
    """Ended and started again, q2ui opens the pages it had open, each on its row."""
    good = True
    r = Run(binary, work, 60, '--page', 'home')
    r.rest()
    r.key(KEY_CENTRE)   # Library
    time.sleep(0.6)
    r.ticks(1)          # Artists
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
    good &= check('started again, it is on the page and the row it was ended on', same and before['ticks'] == 4, f"{before['ticks']} ticks")
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
    woke = r.wakes()
    time.sleep(1.0)
    good &= check('and Home is then at rest again', r.wakes() - woke <= 1, f'{r.wakes() - woke} wakes in 1 s')
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
    """kill -HUP: q2ui starts itself again in place, the same process, on the pages it had open."""
    r = Run(binary, work, 20, '--page', 'home')
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
    r.proc.terminate()
    r.proc.wait(timeout=5)
    os.close(r.queue)
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
    woke = r.wakes()
    time.sleep(1.0)
    good &= check('a list at rest draws no frame', r.stats()['frames'] == moved['frames'],
                  f"{moved['frames']} then {r.stats()['frames']}")
    # asleep in poll until input or the clock's next minute: one wake at most in this second
    good &= check('a list at rest does not wake', r.wakes() - woke <= 1, f'{r.wakes() - woke} wakes in 1 s')
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
    r.ticks(-3)
    r.key(KEY_CENTRE)
    time.sleep(0.4)
    lines = r.sent()
    good &= check('Centre on Shuffle sends the same list shuffled', len(lines) == 1 and lines[0].split()[1:] == ['play', '0', '1', '40'], str(lines))
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
    print('q2ui: ' + ('all checks passed' if good else 'FAILED'))
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
