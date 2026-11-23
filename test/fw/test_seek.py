"""patch/seek.c: holding Previous or Next scans the playing track, faster the longer the button is held: on q2player
heard at 2, 4 and 8 times (mcl-scan) and then in jumps heard at 4; otherwise a jump then a stretch at the song's own
speed, the jumps growing (docs/internals.md#scan). A tap stays stock's skip."""
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
    def __init__(self, fw, mcl, stock_bytes, sec=60, total=240, cue=0, status=PLAYING, q2player=False):
        """q2player: the player's SCAN_FILE is there, so the song can be heard at speed."""
        self.fw, self.sec, self.total, self.status = fw, sec, total, status
        self.sent = []
        C.c_int.from_address(O['MCL_FD']).value = 7
        fw.mock('fw_access', lambda path, *_: 0 if q2player and C.string_at(path) == b'/tmp/q2scan' else -1)
        fw.mock('fw_send', lambda fd, data, n, flags, *_: self.sent.append((fd, C.string_at(data, n), flags)) or n)
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

    def scans(self):
        """The mcl-scan speeds sent, in hundredths."""
        assert all(fd == 7 and flags == 0x4040 for fd, _, flags in self.sent)  # never waits, never a SIGPIPE
        return [int(m[len(b'{mcl-scan\\'):-1]) for _, m, _ in self.sent]

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


def test_without_q2player_nothing_is_sent(scan):
    s = scan()
    s.hold(NEXT, HOLD + 3 * CYCLE)
    s.up(NEXT)
    assert s.scans() == [] and s.seeks()


@pytest.mark.parametrize('direction, paused, there, plan', [
    (1, 0, True, 1), (-1, 0, True, 3),  # forward heard at speed; back by snippets heard forward (SEEK_REWIND_SNIPPETS)
    (1, 1, True, 0), (-1, 1, True, 0), (1, 0, False, 0), (-1, 0, False, 0),  # paused, or not q2player: jumps
])
def test_the_plan_is_heard_at_speed_only_on_q2player_while_playing(fw, direction, paused, there, plan):
    fw.mock('fw_access', lambda path, *_: 0 if there and C.string_at(path) == b'/tmp/q2scan' else -1)
    assert fw.fn('seek_plan', C.c_int, C.c_int, C.c_int)(direction, paused) == plan


class Moving:
    """The player's second, moved on as a scan heard at speed would move it: the speed sent, times the time."""
    def __init__(self, s):
        self.s, self.last, self.at = s, s.clock.now, float(s.sec)
        s.fw.mock('player_playtime_and_length', self.position)

    def position(self, sec, total, *_):
        speed = (self.s.scans() or [100])[-1] / 100 or 1
        self.at = max(0.0, self.at + speed * (self.s.clock.now - self.last) / 1000)
        self.last = self.s.clock.now
        self.s.sec = int(self.at)
        return self.s.position(sec, total)


def test_on_q2player_the_hold_is_heard_at_2_4_then_8_times_then_jumps_heard_at_4(scan):
    s = scan(sec=0, total=3600, q2player=True)
    Moving(s)
    s.hold(NEXT, HOLD + 6000 - CYCLE)
    assert s.scans() == [200, 400, 800] and s.seeks() == []  # the player runs on at speed: no seek
    s.hold(NEXT, 6000)
    assert s.scans() == [200, 400, 800, 400]
    jumps = [b - a for a, b in zip(s.seeks(), s.seeks()[1:])]
    assert jumps and set(jumps) == {8, 16} and jumps == sorted(jumps)  # 16, then 32 times
    assert s.up(NEXT) == STOP and s.scans()[-1] == 0  # the release: its own speed again


def test_on_q2player_previous_steps_back_by_snippets_heard_at_twice(scan):
    s = scan(sec=200, total=3600, q2player=True)
    s.hold(PREV, HOLD + 12000)
    assert s.scans() == [200]  # each snippet heard forward at twice the speed
    steps = [a - b for a, b in zip([200] + s.seeks(), s.seeks())]
    assert set(steps) == {1, 2, 4, 8, 16} and steps == sorted(steps)  # back at 2, 4, 8, 16 then 32 times
    assert s.up(PREV) == STOP and s.scans() == [200, 0]


def test_on_q2player_previous_stops_at_the_start_and_plays_on_from_there(scan):
    s = scan(sec=5, q2player=True)
    s.hold(PREV, HOLD + 5 * CYCLE)
    assert s.seeks() == [4, 3, 2, 1, 0] and s.scans() == [200, 0]  # then forward at its own speed
    assert s.up(PREV) == STOP and s.scans() == [200, 0]


def test_paused_on_q2player_nothing_is_heard(scan):
    s = scan(status=PAUSED, q2player=True)
    s.hold(NEXT, HOLD + 2 * CYCLE)
    assert s.scans() == [] and s.seeks() == []


def test_a_lost_release_stops_the_speed(scan):
    s = scan(q2player=True)
    s.hold(NEXT, HOLD + CYCLE)
    assert s.scans() == [200]
    s.clock.now += 800  # no repeat
    s.fire_due()
    assert s.scans() == [200, 0]


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


def car(s, ms, word=1, every=500):
    """The car's Next (1) or Previous (-1) held ms: q2-iap's "scan" word at once and every `every` ms (the shim's
    seek_car), the timers fired as they come due; 0 at the end for the release unless word is None."""
    hold = s.fw.fn('seek_car', None, C.c_int)
    start = s.clock.now
    hold(word)
    while s.clock.now - start + 100 <= ms:
        s.clock.now += 100
        if (s.clock.now - start) % every == 0:
            hold(word)
        s.fire_due()


def test_the_car_s_held_next_is_the_q2_s_own_scan(scan):
    """Car Audio's held Next (iap2_hid.c, "scan 1" through the shim): the scan a held button makes, from the word on,
    at 2, 4 and 8 times on q2player with no seek; the release ("scan 0") plays on at its own speed. The first drive's
    seek every 500 ms jumped unevenly (2026-11-20)."""
    s = scan(sec=0, total=3600, q2player=True)
    Moving(s)
    car(s, 4600)
    assert s.scans() == [200, 400, 800] and s.seeks() == []
    s.fw.fn('seek_car', None, C.c_int)(0)
    assert s.scans()[-1] == 0 and not s.ui.due('seek__seek_timer')
    assert len(s.stock_downs()) == 0, 'no key reached stock: no skip'


def test_the_car_s_scan_ends_without_its_word(scan):
    """A release that never came (the cable out mid-hold): no word for SEEK_CAR_LOST_MS (2 s) ends the scan."""
    s = scan(sec=60, total=240)
    car(s, 1200)
    first = len(s.seeks())
    assert first >= 2
    for _ in range(25):  # 2.5 s with no word
        s.clock.now += 100
        s.fire_due()
    assert not s.ui.due('seek__seek_timer') and len(s.seeks()) <= first + 4
