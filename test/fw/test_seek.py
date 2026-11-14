"""patch/seek.c: holding Previous or Next scans the playing track, a jump then a stretch at the song's own speed, the
jumps growing the longer the button is held (docs/internals.md#scan); a tap stays stock's skip."""
import ctypes as C

import pytest

from conftest import O
from test_nav_loop import DOWN, UP, Clock, Ui, key_event, stock_bytes  # noqa: F401  stock_bytes is a fixture
from test_playback import Player

NEXT, PREV, STOP = O['KEY_FWD_BTN'], O['KEY_BACK_BTN'], 11
HOLD, CYCLE = 500, 500  # SEEK_HOLD_MS, SEEK_CYCLE_MS
PLAYING, PAUSED = 2, 3


class Scan:
    """A Q2 playing a 240 s local track at sec, its buttons pressed through ringnav_input and released through ringnav,
    the key-up hook, with the clock the test moves and the timers Ui keeps."""
    def __init__(self, fw, mcl, stock_bytes, sec=60, total=240, cue=0, status=PLAYING):
        self.fw, self.sec, self.total, self.status = fw, sec, total, status
        self.ui = Ui(fw)
        self.ui.window('playing_page')
        self.clock = Clock(fw, 100000)
        for at in (O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']):
            stock_bytes(at, 1)
        stock_bytes(O['SCAN_DONE'])
        self.held = stock_bytes(O['NP_KEY_HELD'], 2)
        self.player = Player(fw, mcl)
        self.player.load([self.player.song(b'/m/%d.flac' % i, cue=(cue, 0)) for i in range(3)], 1)
        mcl.type = 1  # a folder's queue
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_playtime_and_length', self.position)
        self.due_at = {}
        fw.mock('timer_add', self.timer_add)
        self.input = fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)
        self.keyup = fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)

    def position(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total
        return 0

    def down(self, key, after=0):
        self.clock.now += after
        return self.input(self.ui.wm.p, key_event(key, DOWN))

    def up(self, key, after=0):
        """The release as stock delivers it: the input hook, then the key-up hook (STOP: no skip)."""
        self.clock.now += after
        self.input(self.ui.wm.p, key_event(key, UP))
        return self.keyup(None, key_event(key, UP))

    def hold(self, key, ms, period=100):
        """Press, the key-down repeated every period ms while held, the timers fired as they come due."""
        self.down(key)
        start = self.clock.now
        while self.clock.now - start + period <= ms:
            self.clock.now += period
            self.down(key)
            self.fire_due()

    def timer_add(self, fn, ctx, ms, *_):
        t = self.ui.timer_add(fn, ctx, ms)
        self.due_at[t] = self.clock.now + ms
        return t

    def fire_due(self):
        """Run each of seek.c's timers that has come due, as the UI loop would; a repeat comes round again."""
        for t, at in sorted(self.due_at.items(), key=lambda kv: kv[1]):
            if t in self.ui.timers and self.clock.now >= at:
                self.due_at[t] = at + self.ui.timers[t][2]
                self.ui.fire(timer=t)

    def seeks(self):
        return [c[1][0] for c in self.fw.calls('mclSetSeek')]

    def stock_downs(self):
        return [c for c in self.fw.calls('stock_input_trampoline')]


@pytest.fixture
def scan(fw, mcl, stock_bytes):
    def make(**kw):
        return Scan(fw, mcl, stock_bytes, **kw)
    return make


def test_the_jump_grows_the_longer_the_button_is_held(fw):
    step = fw.fn('seek_step', C.c_int, C.c_uint)
    assert [step(ms) for ms in (0, 2999, 3000, 5999, 6000, 9999, 10000, 60000)] == [2, 2, 4, 4, 8, 8, 16, 16]


@pytest.mark.parametrize('to, direction, step, total, want', [
    (60, 1, 2, 240, 62), (238, 1, 4, 240, 239), (239, 1, 2, 240, 239), (3, -1, 4, 240, 0), (60, -1, 8, 240, 52),
    (60, 1, 8, 0, 68),  # no length known: no end to stop at
])
def test_a_target_stays_within_the_track(fw, to, direction, step, total, want):
    assert fw.fn('seek_target', C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)(to, direction, step, total) == want


def test_a_tap_is_stocks_skip(scan):
    s = scan()
    assert s.down(NEXT) == 0 and len(s.stock_downs()) == 1  # the press reaches stock
    s.clock.now += HOLD - 1
    assert s.up(NEXT) != STOP and not s.ui.due('seek__seek_hold') and s.seeks() == []


def test_a_hold_scans_forward_and_the_release_is_no_skip(scan):
    s = scan()
    s.hold(NEXT, HOLD + 3 * CYCLE)
    assert len(s.stock_downs()) == 1  # the repeats went no further: Now Playing's slider would step on each
    assert s.seeks() == [62, 64, 66, 68]  # at the hold, then each cycle: 2 s a jump, 4 times the song's speed
    C.c_ubyte.from_address(O['NP_KEY_HELD']).value = 1  # Now Playing's long press marked it
    assert s.up(NEXT, 50) == STOP and C.c_ubyte.from_address(O['NP_KEY_HELD']).value == 0
    assert not s.ui.due('seek__seek_timer') and len(s.seeks()) == 4  # no seek after the release while playing


def test_the_jumps_speed_up(scan):
    s = scan(sec=0, total=3600)
    s.hold(NEXT, HOLD + 12000)
    jumps = [b - a for a, b in zip(s.seeks(), s.seeks()[1:])]
    assert jumps[:5] == [2] * 5 and 4 in jumps and 8 in jumps and jumps[-1] == 16
    assert jumps == sorted(jumps)


def test_a_hold_rewinds_to_the_start_and_plays_on_from_there(scan):
    s = scan(sec=5)
    s.hold(PREV, HOLD + 5 * CYCLE)
    assert s.seeks() == [3, 1, 0]  # then it waits at the start
    assert s.up(PREV) == STOP


def test_forward_stops_short_of_the_end_and_goes_on_in_the_next_track(scan, mcl):
    s = scan(sec=236)
    s.hold(NEXT, HOLD + CYCLE)
    assert s.seeks() == [238, 239]
    s.clock.now += CYCLE
    s.down(NEXT)
    s.fire_due()
    assert s.seeks() == [238, 239]  # waits for the track to run out
    mcl.pos, s.sec = 2, 0  # the player went on to the next
    s.clock.now += CYCLE
    s.down(NEXT)
    s.fire_due()
    assert s.seeks() == [238, 239, 2]


def test_paused_only_the_bar_moves_and_the_release_seeks_once(scan):
    s = scan(status=PAUSED, cue=1000)
    s.hold(NEXT, HOLD + 2 * CYCLE)
    assert s.seeks() == []
    assert s.up(NEXT) == STOP and s.seeks() == [1066]  # the record's CUE start added, as player_seek_time does


def test_a_lost_release_ends_the_scan(scan):
    s = scan()
    s.hold(NEXT, HOLD)
    n = len(s.seeks())
    s.clock.now += 800  # no repeat
    s.fire_due()
    assert not s.ui.due('seek__seek_timer') and len(s.seeks()) == n


@pytest.mark.parametrize('page', ['games_page', 'playlistedit_page'])
def test_a_page_that_owns_the_buttons_keeps_them(scan, page):
    s = scan()
    s.ui.window(page)
    s.hold(NEXT, HOLD + CYCLE)
    assert s.seeks() == [] and s.up(NEXT) != STOP


def test_a_stream_keeps_its_buttons(scan, mcl):
    s = scan()
    mcl.type = 0xf1  # Tidal's online queue
    s.hold(NEXT, HOLD + CYCLE)
    assert s.seeks() == [] and s.up(NEXT) != STOP


def test_the_other_button_while_scanning_is_dropped(scan):
    s = scan()
    s.hold(NEXT, HOLD)
    downs = len(s.stock_downs())
    assert s.down(PREV) == 0 and len(s.stock_downs()) == downs  # dropped, not passed
