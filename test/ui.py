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
    return [str(binary), '--page', 'songs', '--db', d/'library.db', '--catalogue', '', '--fb', d/'fb', '--fonts', d/'fonts', '--icons', d,
            '--stats', d/'stats.json', '--state', d/'state', '--cmdq', d/'cmdq', '--playlist', d/'list', '--backlight', d/'backlight',
            '--now', d/'now', '--cover', d/'cover.jpg', '--ask', d/'ask']


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


def script(binary, work, n, lines, *options):
    """q2ui run over a script; the trace (a dict a frame), the pictures it saved by name, and the stats."""
    d = folder(work, n)
    text = '\n'.join(lines).replace('@', str(d) + '/') + '\n'
    (d/'script').write_text(text)
    done = subprocess.run(base_args(binary, d) + ['--script', d/'script', '--trace', d/'trace', '--clock', '12:00', *options],
                          timeout=120)
    if done.returncode:
        raise RuntimeError(f'q2ui --script exited {done.returncode}')
    trace = []
    for line in (d/'trace').read_text().splitlines():
        if 'sel=' not in line:
            continue
        row = dict(part.split('=') for part in line.split())
        trace.append({k: float(v) for k, v in row.items()})
    pictures = {p.stem: Picture(p) for p in d.glob('*.ppm')}
    return trace, pictures, json.loads((d/'stats.json').read_text())


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
        if not only or 'live' in only:
            good &= live(binary, work)
    print('q2ui: ' + ('all checks passed' if good else 'FAILED'))
    return 0 if good else 1


if __name__ == '__main__':
    sys.exit(main())
