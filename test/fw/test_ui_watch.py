"""ui_watch.c: the UI pass watchdog (docs/internals.md#ui-pass-watchdog), a W line for each UI loop pass whose work
took UI_LATE_MS or more while the screen was lit, with the step its caller marked last."""
import ctypes as C


class Loop:
    """ringnav_sleep's three marks around a pass, a clock in ms, the screen's state and ulog's lines on stderr."""
    def __init__(self, fw):
        self.fw, self.ms, self.lines = fw, 5000, []
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        fw.mock('fw_write', self.write)
        self.enter, self.polled, self.woke = (fw.fn(n, None) for n in ('ui_watch_enter', 'ui_watch_polled', 'ui_watch_woke'))
        self.step = fw.fn('ui_watch_step', None, C.c_char_p)

    def write(self, fd, data, n, *_):
        if fd == 2:  # "<time> <up> <level> <src> <msg>"
            self.lines.append(C.string_at(data, n).decode().rstrip('\n').split(' ', 4)[2:])
        return n

    def pass_(self, work, polls=0, sleep=16, step=None):
        """One pass: work ms of events, timers and paint since the last sleep, then ringnav_sleep's polls, then its
        sleep."""
        self.ms += work
        if step:
            self.step(step)
        self.enter()
        self.ms += polls
        self.polled()
        self.ms += sleep
        self.woke()


def test_a_long_pass_is_logged_with_its_step(fw):
    loop = Loop(fw)
    loop.pass_(5)  # the first: no sleep before it to count from
    loop.pass_(5, sleep=4000)  # an idle wait, however long, is no work
    assert loop.lines == []
    loop.pass_(340, step=b'stream play')
    assert loop.lines == [['W', 'ui', 'ui pass 340 ms, step stream play, 0 more late since']]


def test_the_polls_count_with_the_work_and_a_pass_just_under_is_quiet(fw):
    loop = Loop(fw)
    loop.pass_(5)
    loop.pass_(100, polls=49)
    assert loop.lines == []
    loop.pass_(100, polls=50)
    assert loop.lines == [['W', 'ui', 'ui pass 150 ms, step -, 0 more late since']]


def test_a_step_names_only_the_pass_it_was_marked_in(fw):
    loop = Loop(fw)
    loop.pass_(5)
    loop.pass_(5, step=b'stream list')
    loop.pass_(200, sleep=2000)
    assert loop.lines[-1][2].startswith('ui pass 200 ms, step -,')


def test_lines_are_a_second_apart_the_rest_counted(fw):
    loop = Loop(fw)
    loop.pass_(5)
    for _ in range(4):
        loop.pass_(200)  # 4 passes in about 0.9 s
    assert len(loop.lines) == 1
    loop.pass_(200, sleep=300)
    loop.pass_(200)
    assert [l[2] for l in loop.lines] == ['ui pass 200 ms, step -, 0 more late since', 'ui pass 200 ms, step -, 4 more late since']


def test_nothing_is_logged_while_the_screen_is_dark(fw):
    loop = Loop(fw)
    loop.pass_(5)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    loop.pass_(900)
    assert loop.lines == []
