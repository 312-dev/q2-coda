#!/usr/bin/env python3
"""Boot a build on a pooled emulator and walk the UI's main paths: the test that sees a crash on screen.

    python3 tools/ci/smoke.py BUILD                 # exit 0 passed, 1 failed, 3 could not run (no emulator, or a card with no songs)
    python3 tools/ci/smoke.py BUILD --logs DIR      # where a failure's screenshot and demo.log tail go

No other suite runs the real demo with its real AWTK: test/fw links the payload's C against stand-ins for
stock's functions, and test/patch.py runs the payload under Unicorn with stock's code beside it but no window
manager. On 2026-11-17 a NULL read on every Now Playing open (Library > Songs > Center) passed both for
about 1.5 h, because the fw stand-in for widget_get_prop_str returned the default where stock returns NULL.
This drives the build as a user would, in about a minute and a half:

    boot                     Home on top; a guardian card (No Temperature Sensor) confirmed, any other dialog fails
    Library > Songs > Center Now Playing opens (playing_page); Return to Songs; back to Home
    Cover Flow > Center      its art built first (no "Preparing artwork"), then an album's tracks; Center on a
                             track: Now Playing
    Settings                 General, Playback, Display and Battery open and close
    Control Center           pulled down from the top (statusbar_dialog), Return closes it
    Search                   the letter strip types "AU" and results show; a held Return closes it

A step passes when the window it should open is on top of demo's window manager (read from demo's
memory, `windows`), by what Cover Flow's page shows (its widget tree, `coverflow_screen`) for its steps, or,
where another step stays in one window, when the screen changes. Any step fails at once
when demo's process is gone or replaced (a crash: demo.log's tail is kept). The whole run has a hard
deadline (DEADLINE, 170 s, after the emulator is leased); a lease is waited for up to --wait seconds, and
none in time is exit 3, which the merge queue does not count against the tip.

The emulator is leased from tools/ci/emu_pool.py under the name smoke-PID, never slot 2 (the user's own),
with BLUETOOTH=0 in the copy's config.ini for the run (a Bluetooth stack that is not there would only add
a scan to every boot); the file is put back as it was and the lease returned whatever happens, a signal
included. Its card is the pool's fixture (emu_pool.reset_card), its settings the data fixture's (emu_pool.reset_data); one that holds no songs is exit 3 too, said
as such ("the card has no songs"), not as a missing emulator. docs/building.md#smoke-test.
"""
import argparse
import os
import pathlib
import re
import resource
import signal
import struct
import subprocess
import sys
import time
import zipfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import emu_pool  # noqa: E402

TEST = pathlib.Path(__file__).resolve().parents[2]/'test'

DEADLINE = 170       # s from the lease to the verdict
BOOT_S = 60          # s for Home to come up after `q2emu start`
STEP_S = 8           # s for a step's window to come up
INPUT_GAP_S = .3     # s after an input before looking for its effect
TOAST_S, TOAST_MOST_S = 3, 15  # s with no toast before the first key; s at most to wait for that
# q2guard's must-confirm cards (patch/heatguard.c, docs/internals.md#overheat-guardian): a confirm card
# (GUARD_DIALOG) that demo says, in its answer file's "card" line, is one of these. q2guard decides on its
# sensor within GUARD_S of its start before demo (three empty readings 5 s apart on the emulator, which has no
# PMIC to read), and the card
# follows: boot waits for that verdict, confirms such a card with OK, and fails on any other dialog over Home.
GUARD_S = 40
GUARD_DIALOG = 'sortselect_dialog'
GUARD_CARDS = {'nosensor': 'No Temperature Sensor', 'norun': "Overheat protection isn't running"}
# demo opens a card before it writes the card's name to its answer file (heatguard.c's hg_info: hg_card, then
# hg_answer), a few ms apart in a boot loop on 2026-11-18. A GUARD_DIALOG no answer names yet is looked at again
# for up to ANSWER_S before it fails, or a look that falls in that gap fails a good boot.
ANSWER_S = 1.0
STREAM_S = 60        # s for the app's songs to reach the Library, and then for its song to play
AVOID = (2,)         # the user's own slot (docs/orchestration.md: never touched)
W, H = 320, 375      # the framebuffer's page (portrait), BGRA
CHANGED_PX = 1500    # pixels that must differ for "the screen changed"
# Boxes the screen is compared in (upright x0, y0, x1, y1): below the status row, whose clock ticks; and on
# Home, its list alone, as the art beside it slides on its own.
BELOW_BAR = (0, 40, H, W)
HOME_LIST = (0, 40, 190, W)
# Windows that come and go over the one a step opened: the status bar is always under everything, and a
# toast (msginfo_dialog: "Unplugged" at boot) sits on top for a few seconds.
TRANSIENT = ('system_bar', 'msginfo_dialog')

# Stock V1.36's window manager, found from window_manager() at 0x6801f8 (lui/addiu/addu set $gp to 0xa3aae0,
# then `lw v0,-0x7f44(gp); lw v0,0x1420(v0)`): the GOT slot WM_GOT holds a data block whose word at WM_OFF
# is the manager. Its children (window_manager_get_top_window, 0x680308) are a darray_t at +0x5c (size at
# +0, elms at +8), bottom first. A widget's name is at +0x10 and its vtable at +0x74, the vtable's type
# name at +4 (widget_set_name 0x66a7cc, widget_is_normal_window 0x670eec). qemu-user maps the guest at its
# own addresses, so /proc/PID/mem reads them as they are (the viewer reads g_backlight_status the same way).
WM_GOT, WM_OFF, WM_CHILDREN, W_NAME, W_VT, VT_TYPE = 0xa3aae0 - 0x7f44, 0x1420, 0x5c, 0x10, 0x74, 0x4
# A widget's flags word and its visible bit (widget_get_visible 0x67795c: lw 0x24($a0); ext 2, 1).
W_FLAGS, W_VISIBLE = 0x24, 2
# s for Cover Flow's first open to build its art ("Preparing artwork n/N"): every lease starts with no art
# cached, and the fixture's 16-bit PNG covers each take an ffmpeg run under qemu (1 to 3 s for its five)
ART_S = 60


class Failed(Exception):
    pass


class Unavailable(Exception):
    pass


class Deadline(BaseException):
    pass


def demo_pid(copy):
    """The pid of copy's demo (its qemu process), or None: as the viewer finds it, by a parent's bwrap. q2emu
    resolves its own folder (readlink -f) before run.sh binds its rootfs, so the bwrap names the real path, not
    ~/q2-emu-poolK when that is a symlink onto another disk: the copy is resolved the same way here."""
    root = f'--bind {pathlib.Path(copy).resolve()}/rootfs / '
    out = subprocess.run(['pgrep', '-f', '[m]ipsel-binfmt-P /release/bin/demo'], capture_output=True, text=True).stdout
    for pid in map(int, out.split()):
        p = pid
        while p > 1:
            try:
                if root in pathlib.Path(f'/proc/{p}/cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace'):
                    return pid
                p = int(pathlib.Path(f'/proc/{p}/stat').read_text().rsplit(')', 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
    return None


def windows(pid):
    """demo's windows, bottom first, as (type, name); None when its memory cannot be read (it has gone)."""
    try:
        with open(f'/proc/{pid}/mem', 'rb') as m:
            def u32(a):
                m.seek(a)
                return struct.unpack('<I', m.read(4))[0]

            def text(a):
                if not a:
                    return ''
                m.seek(a)
                return m.read(64).split(b'\0')[0].decode(errors='replace')
            wm = u32(u32(WM_GOT) + WM_OFF)
            arr = u32(wm + WM_CHILDREN) if wm else 0
            n = u32(arr) if arr else 0
            if not 0 < n < 64:
                return []
            elms = u32(arr + 8)
            out = []
            for i in range(n):
                w = u32(elms + 4 * i)
                vt = u32(w + W_VT) if w else 0
                out.append((text(u32(vt + VT_TYPE)) if vt else '', text(u32(w + W_NAME)) if w else ''))
            return out
    except (OSError, struct.error):
        return None


def window_tree(pid, name):
    """The topmost window called name as a tree of (type, visible, children), read as windows() reads the window
    manager (a widget's children are the same darray at WM_CHILDREN; visible is bit W_VISIBLE of the word at
    W_FLAGS, as stock's widget_get_visible at 0x67795c reads it); None when there is none or it cannot be read."""
    try:
        with open(f'/proc/{pid}/mem', 'rb') as m:
            def u32(a):
                m.seek(a)
                return struct.unpack('<I', m.read(4))[0]

            def kids(w):
                arr = u32(w + WM_CHILDREN)
                n = u32(arr) if arr else 0
                return [u32(u32(arr + 8) + 4 * i) for i in range(n)] if 0 < n < 512 else []

            def text(a):
                m.seek(a)
                return m.read(64).split(b'\0')[0].decode(errors='replace') if a else ''

            def tree(w, depth=0):
                vt = u32(w + W_VT)
                return (text(u32(vt + VT_TYPE)) if vt else '', bool(u32(w + W_FLAGS) >> W_VISIBLE & 1),
                        [tree(c, depth + 1) for c in kids(w) if c] if depth < 16 else [])
            wm = u32(u32(WM_GOT) + WM_OFF)
            found = [w for w in (kids(wm) if wm else []) if w and text(u32(w + W_NAME)) == name]
            return tree(found[-1]) if found else None
    except (OSError, struct.error):
        return None


def coverflow_screen(tree):
    """What Cover Flow's page shows, from its widget tree (window_tree): 'covers' (the carousel, its slide_menu
    shown), 'tracks' (the carousel built and hidden under an album's text list) or 'building' (no carousel yet:
    "Preparing artwork n/N" with its Cancel row, or the albums still loading). A Center on 'building' presses
    Cancel (patch/coverflow.c art_build, cancel_row), which shows the covers: a change of screen, and not the
    album's tracks the walk wanted. None for no page."""
    if not tree:
        return None

    def menus(node, shown):
        kind, visible, children = node
        shown = shown and visible
        return [shown] if kind == 'slide_menu' else [s for c in children for s in menus(c, shown)]
    found = menus(tree, True)
    return 'covers' if any(found) else 'tracks' if found else 'building'


def top(stack):
    """The window a step looks at: the topmost that is not TRANSIENT."""
    names = [name for _, name in stack or [] if name not in TRANSIENT]
    return names[-1] if names else None


def boot_rule(stack, card):
    """Over Home at boot: None when Home is on top, 'confirm' for a guardian card demo vouches for (card, from
    its answer file), 'fail' for any other window."""
    t = top(stack)
    if t == 'home_page':
        return None
    return 'confirm' if t == GUARD_DIALOG and card in GUARD_CARDS else 'fail'


def guard_files(pid) -> 'tuple[dict[str, str], dict[str, str]] | None':
    """q2guard's state and demo's answers as dicts (key: value), from the sandbox's /tmp through demo's root;
    None when the build has no q2guard."""
    root = pathlib.Path(f'/proc/{pid}/root')
    if not (root/'usr/bin/q2guard').exists():
        return None
    state: dict[str, str] = {}
    answers: dict[str, str] = {}
    for path, out in ((root/'tmp/q2guard.state', state), (root/'tmp/q2guard.ack', answers)):
        try:
            text = path.read_text()
        except OSError:
            continue
        for word in text.replace('\n', ' ').split(' '):
            if '=' in word:
                out.setdefault(word.split('=', 1)[0], word.split('=', 1)[1])
        for line in text.splitlines():
            if ' ' in line and '=' not in line:
                out[line.split(' ', 1)[0]] = line.split(' ', 1)[1]
    return state, answers


class Emulator:
    """A leased pool copy running the build under test, driven as a user would."""

    def __init__(self, slot, log=print):
        self.slot, self.copy, self.log = slot, emu_pool.copy_dir(slot), log
        self.env = {**os.environ, 'Q2EMU_PORT': str(emu_pool.port(slot))}
        self.pid = None
        self.log_at = 0

    def q2emu(self, *args):
        subprocess.run([str(self.copy/'q2emu'), *map(str, args)], env=self.env, capture_output=True, timeout=30)

    def page(self):
        """The page on screen, BGRA."""
        try:
            off = int((self.copy/'state/fb0.state').read_text().split()[0])
        except (OSError, ValueError, IndexError):
            off = 0
        with open(self.copy/'state/fb0', 'rb') as f:
            f.seek(off * W * 4)
            return f.read(W * H * 4)

    def alive(self):
        """Fail when demo has gone or been replaced since boot."""
        now = demo_pid(self.copy)
        if now != self.pid:
            raise Failed(f'demo {"crashed" if now is None else f"restarted (pid {self.pid} is now {now})"}')

    def stack(self):
        self.alive()
        s = windows(self.pid)
        if s is None:
            self.alive()
            raise Failed('demo\'s memory could not be read')
        return s

    def boot(self):
        end = time.monotonic() + BOOT_S
        while time.monotonic() < end:
            self.pid = demo_pid(self.copy)
            if self.pid and top(windows(self.pid)) == 'home_page':
                # A toast ("Unplugged") comes up about 1.5 s after Home and takes the keys while it shows:
                # go on once none has shown for TOAST_S, or after TOAST_MOST_S whatever shows.
                calm, most = time.monotonic(), time.monotonic() + TOAST_MOST_S
                while time.monotonic() < most and time.monotonic() - calm < TOAST_S:
                    if (windows(self.pid) or [('', '')])[-1][1] in TRANSIENT[1:]:
                        calm = time.monotonic()
                    time.sleep(.2)
                self.guard()
                return
            time.sleep(.5)
        raise Failed(f'Home did not come up in {BOOT_S} s (windows: {self.pid and windows(self.pid)})')

    def guard_card(self):
        """The must-confirm card demo says is up ("-" none), or None."""
        files = guard_files(self.pid)
        return files[1].get('card') if files else None

    def guard(self):
        """Wait for q2guard's verdict on its sensor and confirm the card it brings (GUARD_CARDS); any other
        dialog over Home fails."""
        end, confirmed, unnamed = time.monotonic() + GUARD_S, set(), None
        while True:
            files = guard_files(self.pid)
            if files is None:
                return  # a build without the guardian
            state, answers = files
            card = answers.get('card')
            stack = self.stack()
            rule = boot_rule(stack, card)
            if rule == 'fail' and top(stack) == GUARD_DIALOG:  # a card demo may not have named yet (ANSWER_S)
                unnamed = unnamed or time.monotonic() + ANSWER_S
                if time.monotonic() < unnamed:
                    time.sleep(.05)
                    continue
            else:
                unnamed = None
            if rule == 'fail':
                raise Failed(f'boot: a dialog over Home: windows are {[n for _, n in self.stack()]}, card {card}')
            if rule == 'confirm':
                self.log(f'boot: confirmed {GUARD_CARDS.get(card or "", "")}')
                confirmed.add(state.get('seq'))
                self.q2emu('key', 'centre')
                gone = time.monotonic() + STEP_S  # one press: wait for demo to say the card has closed
                while self.guard_card() == card:
                    if time.monotonic() > gone:
                        raise Failed(f'boot: {GUARD_CARDS.get(card or "", "")} did not close on OK')
                    time.sleep(.2)
                continue
            waiting = state.get('state', 'pending') == 'pending' or (
                state.get('card') == 'nosensor' and state.get('seq') not in confirmed)
            if not waiting:
                self.settle(HOME_LIST)
                return
            if time.monotonic() > end:
                raise Failed(f'boot: q2guard gave no verdict in {GUARD_S} s (state {state}, answers {answers})')
            time.sleep(.5)

    def expect(self, name, why):
        """Wait for window name on top, then for it to finish opening: a key sent mid-slide is dropped."""
        end = time.monotonic() + STEP_S
        while True:
            s = self.stack()
            if top(s) == name:
                self.settle(HOME_LIST if name == 'home_page' else None)
                return
            if time.monotonic() > end:
                raise Failed(f'{why}: expected {name} on top, windows are {[n for _, n in s]}')
            time.sleep(.2)

    def coverflow_shows(self, screen, why, wait):
        """Wait up to wait s for Cover Flow on top to show screen (coverflow_screen), then for it to finish
        opening."""
        end = time.monotonic() + wait
        while True:
            self.alive()
            now = coverflow_screen(window_tree(self.pid, 'coverflow_page')) if top(self.stack()) == 'coverflow_page' \
                else None
            if now == screen:
                self.settle()
                return
            if time.monotonic() > end:
                raise Failed(f'{why}: expected Cover Flow\'s {screen}, it shows {now or "no Cover Flow"} '
                             f'(windows {[n for _, n in self.stack()]})')
            time.sleep(.2)

    def settle(self, box=None, quiet_s=.4, most_s=3.0):
        """Wait for the screen in box to stop moving (an animation, a cover's spring); its last page."""
        end, last = time.monotonic() + most_s, self.page()
        while time.monotonic() < end:
            time.sleep(quiet_s)
            now = self.page()
            if differ(last, now, box) < CHANGED_PX // 5:
                return now
            last = now
        return last

    def changes(self, before, why, box=None):
        end = time.monotonic() + STEP_S
        while True:
            self.alive()
            n = differ(before, self.page(), box)
            if n >= CHANGED_PX:
                return
            if time.monotonic() > end:
                raise Failed(f'{why}: the screen did not change ({n} pixels differ)')
            time.sleep(.2)

    def do(self, *args):
        """One input, then the screen at rest. Each device is its own FIFO and demo reads them as they come, so a
        key sent straight after a turn can land before the turn's last samples (Center on the row the
        selection was passing): the next input waits for the last one's effect to end."""
        self.alive()
        self.q2emu(*args)
        time.sleep(INPUT_GAP_S)
        self.settle(HOME_LIST if top(self.stack()) == 'home_page' else None, most_s=2)

    def home(self):
        """Return until Home is on top, looking first each time: Return on Home itself opens Now Playing."""
        for _ in range(6):
            if top(self.stack()) == 'home_page':
                break
            self.do('key', 'return')
            time.sleep(1)
        self.expect('home_page', 'back to Home')  # and its slide in done: a turn during it goes astray

    def row(self, n, from_end=False):
        """Put the selection on row n of the list in focus: from its first row, or its last with from_end."""
        self.do('wheel', 30 if from_end else -30)
        time.sleep(.6)  # a turn the other way straight after the first loses its first tick
        if n:
            self.do('wheel', -n if from_end else n)
        time.sleep(.6)

    def demo_log(self):
        try:
            with open(self.copy/'state/log/demo.log', 'rb') as f:
                f.seek(self.log_at if os.fstat(f.fileno()).st_size >= self.log_at else 0)
                return f.read()[-4000:].decode(errors='replace')
        except OSError:
            return ''


def differ(a, b, box=None):
    """How many pixels differ between two pages inside box (x0, y0, x1, y1), upright coordinates as q2emu shot's
    375x320 (BELOW_BAR when None). Upright (x, y) is the page's row x, column W - 1 - y (q2remote.framebuffer_png)."""
    if len(a) != len(b):
        return W * H
    x0, y0, x1, y1 = box or BELOW_BAR
    lo, hi = (W - y1) * 4, (W - y0) * 4
    n = 0
    for x in range(x0, x1):
        ra, rb = a[x * W * 4 + lo:x * W * 4 + hi], b[x * W * 4 + lo:x * W * 4 + hi]
        if ra != rb:
            n += sum(1 for i in range(0, len(ra), 4) if ra[i:i + 3] != rb[i:i + 3])
    return n


# Home's rows counted from its last (Settings), as Now Playing's row comes and goes at the top: tools/ipod.py's
# HOME_ROWS read from its end (test_ci.py holds the two together; Radio Tuner's row under the Library moved it).
HOME_FROM_END = {'settings': 0, 'search': 1, 'cover flow': 2, 'library': 6}
# Settings' rows from its first, tools/ipod.py's HOME_SETS (Car Remote's row before Display moved the last two).
SETTINGS = (('General', 2, 'sysset_page'), ('Playback', 3, 'playset_page'), ('Display', 6, 'display_page'),
            ('Battery', 8, 'powermanager_page'))


def private_ipv4():
    """This machine's first RFC 1918 address, the only kind the app takes http:// to; None without one."""
    out = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'], capture_output=True, text=True).stdout
    for addr in re.findall(r'inet (\d+\.\d+\.\d+\.\d+)/', out):
        a, b = map(int, addr.split('.')[:2])
        if a == 10 or (a == 172 and 16 <= b <= 31) or (a == 192 and b == 168):
            return addr
    return None


def car_remote_on(ini):
    """config.ini's text with Car Remote on (CARREMOTE=1 under [Q2POD]): its daemon's shim writes what Now Playing
    plays, its position included, to /tmp/coda-rc.now, which the streaming step reads."""
    text, n = re.subn(r'(?m)^CARREMOTE=.*$', 'CARREMOTE=1', ini)
    if n:
        return text
    text, n = re.subn(r'(?m)^\[Q2POD\]\s*$', '[Q2POD]\nCARREMOTE=1', ini, count=1)
    return text if n else ini.rstrip('\n') + '\n[Q2POD]\nCARREMOTE=1\n'


class Stream:
    """The streaming step's server and app: q2-fakesonic (test/subsonic_seam.py's pin, built from ~/repos/q2-subsonic)
    on this machine's private address at a port the kernel picks, which the emulator reaches as it shares this
    machine's network, and the pinned app's card files (BUNDLED_APPS' zip, its MIPS program) on the copy's card,
    signed in to it with a test account (the fake takes only that one; no real credential). Its three songs are
    titled "0 Seam s1"... so they sort first in Library > Songs, and each is the card fixture's first FLAC (-audio),
    which the emulator's player decodes. why says what is missing when it cannot run."""
    def __init__(self, log):
        self.proc, self.why, self.files = None, None, {}
        sys.path.insert(0, str(TEST))
        try:
            import subsonic_seam as seam  # type: ignore[import-not-found]  # test/, not on mypy's path
        except Exception as e:  # noqa: BLE001  a tree without the seam harness
            self.why = f'no test/subsonic_seam.py ({e})'
            return
        zip_path, commit = seam.pinned_zip()
        ip = private_ipv4()
        if zip_path is None or not (seam.REPO/'.git').exists() or not ip:
            self.why = commit if zip_path is None else f'no q2-subsonic repository at {seam.REPO}' if ip else \
                'this machine has no RFC 1918 address for the app to reach over http'
            return
        try:
            fake, _ = seam.at_commit(seam.REPO, seam.FAKESONIC, './cmd/q2-fakesonic', 'q2-fakesonic')
        except (OSError, subprocess.CalledProcessError) as e:
            self.why = f'q2-fakesonic did not build: {e}'
            return
        audio = next(iter(sorted(emu_pool.fixture_dir().rglob('*.flac'))), None)
        if audio is None:
            self.why = f'no FLAC on the pool\'s card fixture ({emu_pool.fixture_dir()}) for the fake server to serve'
            return
        with zipfile.ZipFile(zip_path) as z:
            self.files = {n.split('/', 1)[1]: z.read(n) for n in z.namelist() if n.startswith('.subsonic/') and n[-1] != '/'}
        self.proc = subprocess.Popen([str(fake), '-listen', f'{ip}:0', '-songs', '3', '-album', '3', '-audio', str(audio),
                                      '-title', '0 Seam '], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        line = self.proc.stdout.readline().split() if self.proc.stdout else []
        self.url = line[1] if len(line) == 2 and line[0] == 'listening' else None
        if not self.url:
            self.stop()
            self.why = 'q2-fakesonic did not start'
            return
        log(f'streaming: q2-fakesonic at {self.url}, {zip_path.name} on the card')

    def card(self, copy):
        """The app on the copy's card (its lease reset the card just before), signed in to the fake."""
        d = copy/'state/mmc/.subsonic'
        d.mkdir(parents=True, exist_ok=True)
        for name, data in self.files.items():
            (d/name).write_bytes(data)
            (d/name).chmod(0o755 if name in ('q2-subsonic', 'run') else 0o644)
        (d/'credentials').write_text(f'{{"Server": "{self.url}", "User": "seam", "Password": "seam"}}')

    def stop(self):
        if self.proc:
            self.proc.kill()
            self.proc.wait(timeout=5)
            self.proc = None


def guest_file(e, name):
    """A file in demo's /tmp (a tmpfs of its sandbox), through /proc; None when it is not there."""
    try:
        return pathlib.Path(f'/proc/{e.pid}/root/tmp/{name}').read_text(errors='replace')
    except OSError:
        return None


def now_playing(e):
    """/tmp/coda-rc.now's keys (patch/coda_rc_shim.c): state, pos, path, buf..."""
    return dict(l.split('=', 1) for l in (guest_file(e, 'coda-rc.now') or '').splitlines() if '=' in l)


def until_true(e, what, ok, wait):
    end = time.monotonic() + wait
    while not (got := ok()):
        e.alive()
        if time.monotonic() > end:
            raise Failed(f'{what}: not within {wait} s')
        time.sleep(.5)
    return got


def streamed(e):
    """The song Now Playing opened is the app's, streamed, and its position moves on."""
    first = until_true(e, 'a streamed song playing (coda-rc.now: path under /mnt/mmc/.subsonic/, state 1, not loading)',
                       lambda: (n := now_playing(e)).get('path', '').startswith('/mnt/mmc/.subsonic/') and
                       n.get('state') == '1' and n.get('buf') == '0' and n, STREAM_S)
    pos = int(first.get('pos', '0'))
    later = until_true(e, f'the position moving on from {pos}', lambda: int(now_playing(e).get('pos', '0')) > pos and
                       now_playing(e), 15)
    e.log(f'  {first["path"]}: position {pos} then {later.get("pos")}')


def steps(e, say, stream=None):
    """The walk; each step says what it did before it is checked."""
    say('boot: Home')
    e.boot()
    if stream:
        say('the app\'s songs in the Library')
        until_true(e, 'the app\'s catalogue imported (/tmp/q2-subsonic.catalogue.done ok)',
                   lambda: ' ok ' in (guest_file(e, 'q2-subsonic.catalogue.done') or ''), STREAM_S)

    say('Home > Library')
    e.row(HOME_FROM_END['library'], from_end=True)
    e.do('key', 'centre')
    e.expect('localmusic_page', 'Library')
    say('Library > Songs')
    e.row(0)
    e.do('key', 'centre')
    e.expect('allmusic_page', 'Songs')
    say('Songs > Center: Now Playing' + (', a streamed song playing on' if stream else ''))
    e.do('key', 'centre')
    e.expect('playing_page', 'Center on a song')
    if stream:
        streamed(e)
    say('Now Playing > Return: Songs')
    e.do('key', 'return')
    e.expect('allmusic_page', 'Return from Now Playing')
    e.home()

    say('Home > Cover Flow')
    e.row(HOME_FROM_END['cover flow'], from_end=True)
    e.do('key', 'centre')
    e.expect('coverflow_page', 'Cover Flow')
    e.coverflow_shows('covers', 'Cover Flow\'s art built', ART_S)
    say('Cover Flow > Center: the album\'s tracks')
    e.do('key', 'centre')
    e.coverflow_shows('tracks', 'Center on an album', STEP_S)
    say('tracks > Center: Now Playing')
    e.settle()
    e.do('key', 'centre')
    e.expect('playing_page', 'Center on a track')
    e.home()

    say('Home > Settings')
    e.row(HOME_FROM_END['settings'], from_end=True)
    before = e.settle(HOME_LIST)
    e.do('key', 'centre')
    e.changes(before, 'Settings', HOME_LIST)
    for label, n, page in SETTINGS:
        say(f'Settings > {label}')
        e.row(n)
        e.do('key', 'centre')
        e.expect(page, f'Settings > {label}')
        e.do('key', 'return')
        e.expect('home_page', f'Return from {label}')
    before = e.settle(HOME_LIST)
    e.do('key', 'return')  # Settings' list back to Home's
    e.changes(before, 'Return from Settings', HOME_LIST)

    say('Control Center: pull down, Return')
    e.do('swipe', 187, 2, 187, 250, 300)
    e.expect('statusbar_dialog', 'Control Center')
    e.settle()
    e.do('key', 'return')
    e.expect('home_page', 'Return from Control Center')

    say('Home > Search: type AU')
    e.row(HOME_FROM_END['search'], from_end=True)
    e.do('key', 'centre')
    e.expect('searchstrip_page', 'Search')
    before = e.settle()
    e.do('key', 'centre')   # A: the strip opens on it
    e.do('wheel', 20)       # A to U in one turn
    e.do('key', 'centre')
    e.changes(before, 'typing AU')
    e.settle()
    say('Search > held Return: Home')
    e.do('key', 'return', 900)
    e.expect('home_page', 'held Return from Search')
    e.alive()


def bluetooth_off(ini):
    """config.ini's text with Bluetooth off: stock's BLUETOOTH key, under [SYSSET]."""
    text, n = re.subn(r'(?m)^BLUETOOTH=.*$', 'BLUETOOTH=0', ini)
    if n:
        return text
    text, n = re.subn(r'(?m)^\[SYSSET\]\s*$', '[SYSSET]\nBLUETOOTH=0', ini, count=1)
    return text if n else ini.rstrip('\n') + '\n[SYSSET]\nBLUETOOTH=0\n'


def lease(build, wait_s, prepare, log):
    """A slot running build, waiting up to wait_s for one to come free."""
    end = time.monotonic() + wait_s
    said = False
    while True:
        try:
            return emu_pool.get(build, f'smoke-{os.getpid()}', 0.25, False, avoid=AVOID, prepare=prepare)
        except SystemExit as e:
            if 'leased' not in str(e):
                raise Failed(f'the emulator did not start: {e}') from None
            if time.monotonic() > end:
                raise Unavailable(f'no pool emulator came free in {wait_s} s') from None
            if not said:
                log(f'every pool emulator is leased; waiting up to {wait_s} s')
                said = True
            time.sleep(10)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('build', type=pathlib.Path)
    ap.add_argument('--logs', type=pathlib.Path, help='default: ~/.cache/q2-smoke/<time>/')
    ap.add_argument('--shots', type=pathlib.Path, help='a screenshot here as each step starts')
    ap.add_argument('--wait', type=float, default=600, help='seconds to wait for a free emulator (600)')
    ap.add_argument('--no-stream', action='store_true', help='leave out the streamed song (Library > Songs plays a card one)')
    args = ap.parse_args(argv)
    if not (args.build/'rootfs.squashfs').exists():
        ap.error(f'{args.build} is not a build (no rootfs.squashfs)')
    logs = args.logs or pathlib.Path.home()/'.cache/q2-smoke'/time.strftime('%Y%m%d-%H%M%S')
    t0 = time.monotonic()

    def log(msg):
        print(f'{time.monotonic() - t0:6.1f}  {msg}', flush=True)

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # a crashing demo writes no core
    os.environ.pop('LD_PRELOAD', None)  # q2-heavy's host preload means nothing to the guest's loader
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # the finally below still gives the slot back

    saved, log_at = {}, {}
    stream = None if args.no_stream else Stream(log)
    if stream and stream.why:
        log(f'smoke: streaming skipped: {stream.why}')
        stream = None

    def prepare(k):
        demo_log = emu_pool.copy_dir(k)/'state/log/demo.log'
        log_at[k] = demo_log.stat().st_size if demo_log.exists() else 0  # this run's part of it starts here
        ini = emu_pool.copy_dir(k)/'state/data/config.ini'
        if ini.exists():
            saved[k] = ini.read_bytes()
            text = bluetooth_off(saved[k].decode('utf-8', 'replace'))
            ini.write_text(car_remote_on(text) if stream else text)
        if stream:
            stream.card(emu_pool.copy_dir(k))

    slot, e, step = None, None, ['lease']

    def say(what):
        step[0] = what
        log(what)
        if args.shots and e is not None:  # the screen each step starts from
            args.shots.mkdir(parents=True, exist_ok=True)
            n = len(list(args.shots.glob('*.png')))
            e.q2emu('shot', args.shots/f'{n:02d}-{re.sub(r"[^A-Za-z0-9]+", "-", what).strip("-")}.png')

    try:
        try:
            slot = lease(args.build, args.wait, prepare, log)
        except Unavailable as why:
            log(f'smoke: could not run: {why}')
            return 3
        if not emu_pool.card_songs(slot):
            log(f'smoke: could not run: the card on slot {slot} has no songs ({emu_pool.copy_dir(slot)/"state/mmc"}; '
                f'the pool resets it to {emu_pool.fixture_dir()} at each lease), so Library > Songs has nothing to open')
            return 3
        e = Emulator(slot, log)
        e.log_at = log_at.get(slot, 0)
        log(f'slot {slot}: {e.copy}, http://127.0.0.1:{emu_pool.port(slot)}/')

        def overdue(*_):
            raise Deadline()
        signal.signal(signal.SIGALRM, overdue)
        signal.alarm(DEADLINE + (2 * STREAM_S if stream else 0))
        steps(e, say, stream)
        signal.alarm(0)
        log(f'smoke: passed in {time.monotonic() - t0:.0f} s')
        return 0
    except (Failed, Deadline) as why:
        signal.alarm(0)
        reason = str(why) if isinstance(why, Failed) else f'the deadline ({DEADLINE} s) passed'
        log(f'smoke: FAILED at "{step[0]}": {reason}')
        if e is not None:
            logs.mkdir(parents=True, exist_ok=True)
            try:
                e.q2emu('shot', logs/'failed.png')
                log(f'  the screen: {logs/"failed.png"}')
            except (OSError, subprocess.SubprocessError):
                pass
            tail = e.demo_log()
            (logs/'demo.log').write_text(tail)
            crash = [ln for ln in tail.splitlines() if re.search(r'signal|Segmentation|Aborted|core dumped', ln)]
            log(f'  demo.log tail: {logs/"demo.log"}' + (f'; {crash[-1].strip()}' if crash else ''))
        return 1
    finally:
        if stream:
            stream.stop()
        if slot is not None:
            emu_pool.put(slot)
            if slot in saved:
                (emu_pool.copy_dir(slot)/'state/data/config.ini').write_bytes(saved[slot])


if __name__ == '__main__':
    sys.exit(main())
