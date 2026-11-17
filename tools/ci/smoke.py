#!/usr/bin/env python3
"""Boot a build on a pooled emulator and walk the UI's main paths: the test that sees a crash on screen.

    python3 tools/ci/smoke.py BUILD                 # exit 0 passed, 1 failed, 3 could not run (no emulator)
    python3 tools/ci/smoke.py BUILD --logs DIR      # where a failure's screenshot and demo.log tail go

No other suite runs the real demo with its real AWTK: test/fw links the payload's C against stand-ins for
stock's functions, and test/patch.py runs the payload under Unicorn with stock's code beside it but no window
manager. On 2026-11-17 a NULL read on every Now Playing open (Library > Songs > Center) passed both for
about 1.5 h, because the fw stand-in for widget_get_prop_str returned the default where stock returns NULL.
This drives the build as a user would, in about a minute and a half:

    boot                     Home on top
    Library > Songs > Center Now Playing opens (playing_page); Return to Songs; back to Home
    Cover Flow > Center      an album's tracks (the screen changes); Center on a track: Now Playing
    Settings                 General, Playback, Display and Battery open and close
    Control Center           pulled down from the top (statusbar_dialog), Return closes it
    Search                   the letter strip types "AU" and results show; a held Return closes it

A step passes when the window it should open is on top of demo's window manager (read from demo's
memory, `windows`) or, where the step stays in one window, when the screen changes. Any step fails at once
when demo's process is gone or replaced (a crash: demo.log's tail is kept). The whole run has a hard
deadline (DEADLINE, 170 s, after the emulator is leased); a lease is waited for up to --wait seconds, and
none in time is exit 3, which the merge queue does not count against the tip.

The emulator is leased from tools/ci/emu_pool.py under the name smoke-PID, never slot 2 (the user's own),
with BLUETOOTH=0 in the copy's config.ini for the run (a Bluetooth stack that is not there would only add
a scan to every boot); the file is put back as it was and the lease returned whatever happens, a signal
included. docs/building.md#smoke-test.
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import emu_pool  # noqa: E402

DEADLINE = 170       # s from the lease to the verdict
BOOT_S = 60          # s for Home to come up after `q2emu start`
STEP_S = 8           # s for a step's window to come up
INPUT_GAP_S = .3     # s after an input before looking for its effect
TOAST_S, TOAST_MOST_S = 3, 15  # s with no toast before the first key; s at most to wait for that
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


class Failed(Exception):
    pass


class Unavailable(Exception):
    pass


class Deadline(BaseException):
    pass


def demo_pid(copy):
    """The pid of copy's demo (its qemu process), or None: as the viewer finds it, by a parent's bwrap."""
    root = f'--bind {copy}/rootfs / '
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


def top(stack):
    """The window a step looks at: the topmost that is not TRANSIENT."""
    names = [name for _, name in stack or [] if name not in TRANSIENT]
    return names[-1] if names else None


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
                return
            time.sleep(.5)
        raise Failed(f'Home did not come up in {BOOT_S} s (windows: {self.pid and windows(self.pid)})')

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


# Home's rows counted from its last (Settings), as Now Playing's row comes and goes at the top.
HOME_FROM_END = {'settings': 0, 'search': 1, 'cover flow': 2, 'library': 5}
SETTINGS = (('General', 2, 'sysset_page'), ('Playback', 3, 'playset_page'), ('Display', 5, 'display_page'),
            ('Battery', 7, 'powermanager_page'))


def steps(e, say):
    """The walk; each step says what it did before it is checked."""
    say('boot: Home')
    e.boot()

    say('Home > Library')
    e.row(HOME_FROM_END['library'], from_end=True)
    e.do('key', 'centre')
    e.expect('localmusic_page', 'Library')
    say('Library > Songs')
    e.row(0)
    e.do('key', 'centre')
    e.expect('allmusic_page', 'Songs')
    say('Songs > Center: Now Playing')
    e.do('key', 'centre')
    e.expect('playing_page', 'Center on a song')
    say('Now Playing > Return: Songs')
    e.do('key', 'return')
    e.expect('allmusic_page', 'Return from Now Playing')
    e.home()

    say('Home > Cover Flow')
    e.row(HOME_FROM_END['cover flow'], from_end=True)
    e.do('key', 'centre')
    e.expect('coverflow_page', 'Cover Flow')
    say('Cover Flow > Center: the album\'s tracks')
    before = e.settle()
    e.do('key', 'centre')
    e.changes(before, 'Center on an album')
    e.expect('coverflow_page', 'the album\'s tracks')
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

    def prepare(k):
        demo_log = emu_pool.copy_dir(k)/'state/log/demo.log'
        log_at[k] = demo_log.stat().st_size if demo_log.exists() else 0  # this run's part of it starts here
        ini = emu_pool.copy_dir(k)/'state/data/config.ini'
        if ini.exists():
            saved[k] = ini.read_bytes()
            ini.write_text(bluetooth_off(saved[k].decode('utf-8', 'replace')))

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
        e = Emulator(slot, log)
        e.log_at = log_at.get(slot, 0)
        log(f'slot {slot}: {e.copy}, http://127.0.0.1:{emu_pool.port(slot)}/')

        def overdue(*_):
            raise Deadline()
        signal.signal(signal.SIGALRM, overdue)
        signal.alarm(DEADLINE)
        steps(e, say)
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
        if slot is not None:
            emu_pool.put(slot)
            if slot in saved:
                (emu_pool.copy_dir(slot)/'state/data/config.ini').write_bytes(saved[slot])


if __name__ == '__main__':
    sys.exit(main())
