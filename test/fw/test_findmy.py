"""Find My Vamp, patch/findmy.c and patch/findmy_tune.h (docs/internals.md#find-my-vamp): the jingle's ioctls on a fake
/dev/jz_pwm, recorded in order; the worker run on the test's thread with a fake clock that its waits move, so a press,
the minute's end and a refused CONFIG land at a known point of the phrase."""
import ctypes as C
import struct

from conftest import O

RELEASE, REQUEST, CONFIG, LEVEL = 0x80045016, 0x8001500b, 0x80185021, 0xc004502c
FD, CH = 7, 1
TUNE = [(2349, 110), (0, 30), (2960, 110), (0, 30), (2637, 110), (0, 30), (2960, 110), (0, 30), (3136, 260), (0, 900)]
EVT_KEY_DOWN, EVT_KEY_UP, EVT_POINTER_DOWN, EVT_POINTER_UP = 0x110, 0x114, 0xff, 0x103
KEY_CENTER = 0xab


class Pwm:
    """/dev/jz_pwm: each ioctl as (name, value), CONFIG as its frequency, LEVEL as (channel, duty). The clock moves only
    in usleep; during(t, fn) runs fn once, at the first wait that reaches t ms after the start."""
    def __init__(self, fw, opens=True):
        self.fw, self.ms, self.log, self.fail_configs, self.hooks = fw, 1_000_000, [], 0, []
        self.start = self.ms
        fw.mock('time_now_ms', lambda *_: self.ms & 0xffffffff)
        fw.mock('fw_open', lambda path, *_: FD if opens and C.string_at(path) == b'/dev/jz_pwm' else -1)
        fw.mock('fw_ioctl', self.ioctl)
        fw.mock('fw_usleep', self.usleep)
        fw.mock('fw_close', lambda fd, *_: self.log.append(('close', fd)))  # fw.calls keeps only the first 8192
        self.threads = []
        def create(t, attr, fn, arg, *_):  # the worker (and a click's) run to their end on this thread
            self.threads.append(fn)
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(fn)(arg)
            return 0
        fw.mock('fw_pthread_create', create)
        self.start_fn = fw.fn('findmy_start', C.c_int)
        self.on = fw.fn('findmy_on', C.c_int)
        self.gate = fw.fn('findmy_gate', C.c_int, C.c_void_p)

    def ioctl(self, fd, req, arg, *_):
        assert fd == FD
        req &= 0xffffffff
        if req == RELEASE:
            self.log.append(('release', arg & 0xffffffff))
            return 0
        if req == REQUEST:
            self.log.append(('request', C.string_at(arg)))
            return CH
        if req == CONFIG:
            words = struct.unpack('<6I', C.string_at(arg, 24))
            assert words[:3] == (1, 0, 0) and words[4:] == (100, CH)
            if self.fail_configs:
                self.fail_configs -= 1
                self.log.append(('config refused', words[3]))
                return -1
            self.log.append(('config', words[3]))
            return 0
        if req == LEVEL:
            self.log.append(('level', struct.unpack('<2I', C.string_at(arg, 8))))
            return 0
        raise AssertionError(hex(req))

    def usleep(self, us, *_):
        self.ms += (us & 0xffffffff) // 1000
        for hook in list(self.hooks):
            if self.ms - self.start >= hook[0]:
                self.hooks.remove(hook)
                hook[1]()
        return 0

    def during(self, at_ms, fn):
        self.hooks.append((at_ms, fn))

    def play(self):
        self.start = self.ms
        return self.start_fn()


def event(fw, type_, key=KEY_CENTER):
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, O['EVENT_TYPE'], type_)
    struct.pack_into('<i', e, O['EVENT_KEY'], key)
    return e


def notes(log):
    return [v for k, v in log if k == 'config']


def test_the_phrase_plays_on_the_buzzer_for_a_minute_then_leaves_it_low_and_released(fw):
    pwm = Pwm(fw)
    assert pwm.play() == 1
    log = pwm.log
    assert log[:2] == [('release', CH), ('request', b'PC01')]  # a stale "working" flag cleared first
    for i, (k, _) in enumerate(log):
        if k == 'config':
            assert log[i - 1] == ('level', (CH, 0))  # CONFIG is refused while the channel runs
            assert log[i + 1] == ('level', (CH, 50))
    phrase = [hz for hz, _ in TUNE if hz]
    assert notes(log)[:10] == phrase * 2
    assert log[-3:] == [('level', (CH, 0)), ('release', CH), ('close', FD)]  # low, released, the driver closed
    assert 60_000 <= pwm.ms - pwm.start <= 60_010  # the minute, then it stops by itself
    passes = 60_000 / sum(ms for _, ms in TUNE)
    assert int(passes) * 5 <= len(notes(log)) <= (int(passes) + 1) * 5
    assert pwm.on() == 0


def test_the_notes_sit_near_the_buzzer_s_resonance():
    """Loud: the buzzer has no volume, so every note stays in 1.8 to 3.3 kHz around its 2 to 3 kHz resonance, with
    rests that part them; a phrase under 1.5 s (0.82 s, the one auditioned) and a pause of about a second. The table
    is findmy_tune.h's; this list is its copy."""
    import re
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2]/'patch/findmy_tune.h').read_text()
    table = [tuple(map(int, t)) for t in re.findall(r'\{ *(\d+), *(\d+) *\}', src)]
    assert table == TUNE
    assert all(1800 <= hz <= 3300 for hz, _ in table if hz) and 4 <= sum(1 for hz, _ in table if hz) <= 8
    assert 700 <= sum(ms for _, ms in table[:-1]) <= 1500 and table[-1][0] == 0 and 800 <= table[-1][1] <= 1200
    assert all(hz == 0 for hz, _ in table[1::2])  # a rest after every note


def test_a_key_stops_it_within_one_note_and_its_release_goes_nowhere(fw):
    pwm = Pwm(fw)
    down, up = event(fw, EVT_KEY_DOWN), event(fw, EVT_KEY_UP)
    stopped = []
    pwm.during(1_000, lambda: stopped.append((pwm.gate(down), pwm.ms, len(pwm.log))))
    pwm.play()
    (taken, at, n), = stopped
    assert taken == 1
    assert pwm.ms - at <= 10  # its next wait's slice, at most
    assert notes(pwm.log[n:]) == [] and pwm.log[-3:-1] == [('level', (CH, 0)), ('release', CH)]
    assert pwm.on() == 0
    assert pwm.gate(up) == 1  # the stopping press's release is eaten too
    assert pwm.gate(down) == 0 and pwm.gate(up) == 0  # then keys are the page's again


def test_a_touch_stops_it(fw):
    pwm = Pwm(fw)
    pwm.during(500, lambda: pwm.gate(event(fw, EVT_POINTER_DOWN)))
    pwm.play()
    assert pwm.ms - pwm.start < 600
    assert pwm.gate(event(fw, EVT_POINTER_UP)) == 1 and pwm.gate(event(fw, EVT_POINTER_DOWN)) == 0


def test_the_clicker_leaves_the_buzzer_alone_while_it_plays(fw):
    pwm = Pwm(fw)
    fw.var('g_keytone_flag', C.c_ubyte).value = 1
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    click, piezo = fw.fn('ringnav_buzzer', C.c_int, C.c_int), fw.fn('ringnav_piezo', C.c_int)
    clicks = lambda: len(fw.calls('stock_buzzer_trampoline'))  # noqa: E731
    seen = []
    pwm.during(300, lambda: seen.append((pwm.on(), click(1), piezo(), clicks())))
    pwm.during(400, lambda: pwm.fw.fn('findmy_stop', None)())
    pwm.play()
    assert seen == [(1, 0, 0, 0)]
    click(1)  # the control: once it is over the click sounds again
    assert clicks() == 1


def test_a_refused_config_takes_the_channel_again_and_plays_on(fw):
    """The MCU's click took channel 1 mid-tone: CONFIG fails ("Cannot configure at working"); the channel is released
    and requested again, and the note sounds."""
    pwm = Pwm(fw)
    pwm.fail_configs = 1
    pwm.during(200, lambda: pwm.fw.fn('findmy_stop', None)())
    pwm.play()
    log = pwm.log
    i = log.index(('config refused', 2349))
    assert log[i + 1:i + 6] == [('release', CH), ('release', CH), ('request', b'PC01'), ('level', (CH, 0)),
                                ('config', 2349)]
    assert log[i + 6] == ('level', (CH, 50))


def test_no_buzzer_driver_starts_nothing(fw):
    pwm = Pwm(fw, opens=False)
    assert pwm.play() == 0 and pwm.on() == 0 and pwm.threads == []


def test_find_again_while_it_plays_starts_its_minute_again(fw):
    pwm = Pwm(fw)
    fw.mock('fw_pthread_create', lambda *_: 0)  # the worker started, left running
    assert pwm.play() == 1 and pwm.on() == 1
    pwm.ms += 30_000
    assert pwm.start_fn() == 1
    assert len(fw.calls('fw_open')) == 1  # one worker, one open


def test_the_toast_shows_while_it_plays_and_goes_with_it(fw):
    """The screen lit (awake_screen) and the toast "Find My Vamp" opened from a timer, for what is left of the minute;
    the stop takes it down (toast_end), and the UI's tick ends once the worker is done."""
    pwm = Pwm(fw)
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append((fn, ms)) or len(timers))
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert pwm.play() == 1
    assert len(fw.calls('awake_screen')) == 1
    show = C.cast(fw.lib.findmy__fm_show, C.c_void_p).value or 0
    tick = C.cast(fw.lib.findmy__fm_tick, C.c_void_p).value or 0
    assert [(fn, ms) for fn, ms in timers] == [(show, 0), (tick, 250)]
    opened = []
    fw.mock('navigator_to_with_context', lambda name, info, *_: opened.append(
        (C.string_at(name), struct.unpack('<2i', C.string_at(info, 8)), C.string_at(info + 8))))
    pwm.ms += 5_000
    C.CFUNCTYPE(C.c_int, C.c_void_p)(show)(0)
    assert opened == [(b'dialog/msginfo_dialog', (1, 55_000), b'Find My Vamp')]
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert C.CFUNCTYPE(C.c_int, C.c_void_p)(tick)(0) == 8  # RET_REPEAT while it plays
    assert len(fw.calls('reset_poweroptions_timer')) == 1  # kept lit
