"""q2ui_gate.c: the screen handed to q2ui and taken back (docs/ui-rewrite.md#cutover). demo starts q2ui as its child
while /mnt/data/ui-target says q2ui and the image has /usr/bin/q2ui, paints nothing and leaves the wheel, Centre,
Return and touches to it; it takes the screen back for a window of its own and gives it again on Home; and it opens
a page of its own that q2ui asks for as it leaves. With no target or no q2ui in the image, which is every default
build, the gate never closes."""
import ctypes as C

import pytest

from conftest import O

TARGET, BINARY, ASK = b'/mnt/data/ui-target', b'/usr/bin/q2ui', b'/tmp/q2ui.open'
CHILD, ASKED = 4242, 3 << 8  # q2ui's pid here, and waitpid's status for its exit code 3


class Gate:
    """demo's world round the gate: the target file, the image's q2ui, the child, and the window on top."""

    def __init__(self, fw, target=b'q2ui\n', binary=True):
        self.fw, self.target, self.binary = fw, target, binary
        self.top: bytes | None = b'home_page'
        self.ended, self.forks, self.repaints, self.lit = 0, 0, 0, 0
        self.shell: list[str] = []
        self.name = C.create_string_buffer(64)
        self.ask: bytes | None = None
        self.status, self.ms, self.opened = 0, 1000, []
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', self.read)
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('navigator_to', lambda name, *_: self.opened.append(C.string_at(name).decode()))
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('fw_access', lambda path, *_: 0 if binary and C.string_at(path) == BINARY else -1)
        fw.mock('fw_fork', self.fork)
        fw.mock('chapter_pipe2', lambda *_: -1)  # no settings pipe unless a test makes one (Piped)
        fw.mock('fw_waitpid', self.waitpid)
        fw.mock('fw_system', lambda cmd, *_: self.shell.append(C.string_at(cmd).decode()))
        fw.mock('window_manager', lambda *_: 0x1000)
        fw.mock('window_manager_get_top_window', lambda *_: 0x2000 if self.top is not None else 0)
        fw.mock('widget_get_prop_str', self.prop)
        fw.mock('widget_invalidate_force', self.repaint)
        fw.mock('reset_poweroptions_timer', self.light)
        self.on = fw.fn('q2ui_on', C.c_int)
        self.poll = fw.fn('q2ui_poll', None)
        self.input = fw.fn('q2ui_input', C.c_int, C.c_uint, C.c_uint)

    def open(self, path, *_):
        name = C.string_at(path)
        if name == TARGET and self.target is not None:
            return 9
        return 10 if name == ASK and self.ask is not None else -1

    def read(self, fd, out, room, *_):
        data = ((self.target if fd == 9 else self.ask) or b'')[:room]
        C.memmove(out, data, len(data))
        return len(data)

    def waitpid(self, pid, status, *_):
        if not self.ended or pid != CHILD:
            return 0
        C.c_int.from_address(status).value = self.status
        return pid

    def fork(self, *_):
        self.forks += 1
        return CHILD

    def prop(self, w, key, default, *_):
        self.name.value = self.top or b''
        return C.addressof(self.name)

    def repaint(self, *_):
        self.repaints += 1

    def light(self, *_):
        self.lit += 1


KEYS = {k: O[k] for k in ('KEY_PREV', 'KEY_NEXT', 'KEY_CENTER', 'KEY_RETURN', 'KEY_PLAY')}
DOWN, UP, TOUCH = O['EVT_KEY_DOWN'], O['EVT_KEY_UP'], O['EVT_POINTER_FIRST']


@pytest.mark.parametrize('target, binary', [(None, True), (b'demo\n', True), (b'q2u', True), (b'q2ui\n', False)])
def test_without_the_target_or_the_program_the_gate_never_closes(fw, target, binary):
    g = Gate(fw, target, binary)
    assert g.on() == 0
    for _ in range(3):
        g.poll()
    assert g.forks == 0 and g.shell == [] and g.repaints == 0
    assert [g.input(UP, k) for k in KEYS.values()] == [0] * 5 and g.input(TOUCH, 0) == 0 and g.lit == 0


def test_the_target_closes_the_gate_before_the_first_paint_and_the_first_pass_starts_q2ui(fw):
    g = Gate(fw)
    assert g.on() == -1 and g.forks == 0  # the screen's holder already: no frame of demo's shows first
    g.poll()
    assert g.forks == 1 and g.on() == CHILD
    assert g.shell == ['killall q2splash 2>/dev/null']  # the boot animation would draw over it
    g.poll()
    assert g.forks == 1 and g.repaints == 0  # running: nothing more


def test_the_wheel_centre_return_and_touches_are_q2uis_and_the_other_keys_stay_demos(fw):
    g = Gate(fw)
    g.poll()
    for key in ('KEY_PREV', 'KEY_NEXT', 'KEY_CENTER', 'KEY_RETURN'):
        assert g.input(DOWN, KEYS[key]) == 1 and g.input(UP, KEYS[key]) == 1, key
    assert g.input(TOUCH, 0) == 1 and g.input(O['EVT_POINTER_ABORT'], 0) == 1
    assert g.lit == 10  # each one keeps the screen lit, as a press demo acted on would
    assert g.input(UP, KEYS['KEY_PLAY']) == 0 and g.input(DOWN, KEYS['KEY_PLAY']) == 0 and g.lit == 10


def test_q2ui_ending_gives_demo_the_screen_whole_and_it_is_not_started_again(fw):
    g = Gate(fw)
    g.poll()
    g.ended = 1  # Return on its first page, or a crash
    g.poll()
    assert g.on() == 0 and g.repaints == 1
    for _ in range(3):
        g.poll()
    assert g.forks == 1 and g.input(UP, KEYS['KEY_RETURN']) == 0


def test_a_window_of_demos_ends_q2ui_and_home_starts_it_again(fw):
    g = Gate(fw)
    g.poll()
    g.top = b'usbmode_page'  # it opened by itself: the user must see it
    g.poll()
    assert g.shell[-1] == f'kill {CHILD}' and g.on() == CHILD
    g.poll()
    assert g.shell.count(f'kill {CHILD}') == 1  # asked once
    g.ended = 1
    g.poll()
    assert g.on() == 0 and g.repaints == 1 and g.forks == 1
    g.poll()
    assert g.forks == 1  # not while that window is up
    g.top, g.ended = b'home_page', 0
    g.poll()  # Home is back on top: started on that pass
    assert g.forks == 2 and g.on() == CHILD and g.shell.count('killall q2splash 2>/dev/null') == 1


def test_with_no_window_yet_demo_is_still_starting_and_q2ui_is_left_alone(fw):
    g = Gate(fw)
    g.top = None
    g.poll()
    g.poll()
    assert g.forks == 1 and not any(c.startswith('kill ') for c in g.shell)


@pytest.mark.parametrize('top', [b'playing_page', b'volume_dialog', b'usbdebug_page'])
def test_now_playing_the_volumes_display_and_the_usb_debugging_page_are_not_windows_to_end_q2ui_for(fw, top):
    g = Gate(fw)
    g.poll()
    # stock opened Now Playing for the song q2ui chose; the volume's display comes up at every step; the USB Debugging
    # page stands on top from boot while a developer's cable is in (a Q2 so cabled never reached q2ui, 2026-11-27)
    g.top = top
    for _ in range(3):
        g.poll()
    assert not any(c.startswith('kill ') for c in g.shell) and g.on() == CHILD


def test_a_page_q2ui_asks_for_is_opened_and_q2ui_comes_back_when_it_is_left(fw):
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = b'folder_page\n', ASKED, 1
    g.poll()
    assert g.opened == ['folder_page'] and g.on() == 0 and g.repaints == 1
    g.ended = 0
    g.poll()
    assert g.forks == 1  # Home is still on top: the page is on its way up, not left
    g.top = b'folder_page'
    g.poll()
    g.poll()
    assert g.forks == 1
    g.top = b'home_page'  # Return from it
    g.poll()
    assert g.forks == 2 and g.on() == CHILD


@pytest.mark.parametrize('ask', [b'systemset/wifi_page', b'playset/playset_page'])
def test_a_stock_page_in_a_folder_is_opened_by_its_path(fw, ask):
    """q2ui's Settings rows name stock's settings pages as stock's own rows open them: their folder, a slash, the page."""
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = ask, ASKED, 1
    g.poll()
    assert g.opened == [ask.decode()] and g.on() == 0


def test_a_page_that_never_comes_up_does_not_keep_q2ui_away(fw):
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = b'folder_page', ASKED, 1
    g.poll()
    g.ended = 0
    g.ms += 1400
    g.poll()
    assert g.forks == 1
    g.ms += 200  # a second and a half with Home still on top
    g.poll()
    g.poll()
    assert g.forks == 2


@pytest.mark.parametrize('ask', [b'../etc/passwd', b'games', b'Games_page', b'games_page; reboot', b'_page', None,
                                 b'/wifi_page', b'a/b/wifi_page', b'system_set/wifi_page', b'systemset/_page',
                                 b'Systemset/wifi_page', b'systemset/../wifi_page'])
def test_only_a_stock_pages_name_is_opened(fw, ask):
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = ask, ASKED, 1
    g.poll()
    g.ended = 0
    for _ in range(3):
        g.poll()
    assert g.opened == [] and g.forks == 1 and g.on() == 0  # demo keeps the screen, as after a plain end


def test_a_plain_end_reads_no_request(fw):
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = b'games_page', 0, 1  # a request left from an earlier run
    g.poll()
    assert g.opened == [] and g.on() == 0


def test_a_q2ui_that_ends_to_be_reloaded_is_started_again_with_no_frame_of_demos(fw):
    g = Gate(fw)
    g.poll()
    g.status, g.ended = 4 << 8, 1  # kill -HUP: q2ui ends with its reload code
    g.poll()
    assert g.on() == -1  # still the screen's holder: demo paints nothing meanwhile
    g.ended = 0
    g.poll()
    assert g.forks == 2 and g.on() == CHILD and g.opened == []


def test_a_q2ui_killed_from_outside_is_started_again_and_one_that_dies_at_once_is_not(fw):
    g = Gate(fw)
    g.poll()
    g.ms += 60000
    g.status, g.ended = 15, 1  # SIGTERM from a shell, a minute in
    g.poll()
    assert g.on() == -1
    g.ended = 0
    g.poll()
    assert g.forks == 2 and g.on() == CHILD
    g.ms += 2000
    g.status, g.ended = 11, 1  # a fault two seconds after that start: it cannot run
    g.poll()
    g.ended = 0
    for _ in range(3):
        g.poll()
    assert g.forks == 2 and g.on() == 0 and g.repaints >= 1  # demo keeps the screen rather than loop


def test_every_start_and_end_is_a_line_in_demos_log(fw):
    lines = []

    def write(fd, data, n, *_):  # ulog's line on stderr: "<time> <up> <level> <src> <msg>"
        text = C.string_at(data, n).decode()
        if fd == 2 and ' q2ui ' in text:
            lines.append(text.split(' q2ui ', 1)[1].strip())
        return n
    fw.mock('fw_write', write)
    g = Gate(fw)
    g.poll()
    g.top = b'usbmode_page'
    g.poll()
    g.status, g.ended = 15, 1
    g.poll()
    assert lines[0] == f'started, pid {CHILD}' and lines[1] == 'usbmode_page' and lines[2].startswith("ended for a window of demo's, status")


@pytest.mark.parametrize('name', ['tuner_page', 'games_page', 'era_page', 'ratings_page', 'mostplayed_page', 'searchstrip_page',
                                  'coverflow_page', 'carremote_page'])
def test_a_page_the_payload_builds_is_not_opened_by_its_name(fw, name):
    """Those are built by openers of the payload's own (the click of the row that leads to each), which the gate
    calls; a stock page's name would open nothing for them. That each opener then puts its page up is the emulator's
    to show (docs/ui-rewrite.md#cutover): here the openers are the real ones, with no window system under them."""
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = name.encode(), ASKED, 1
    g.poll()
    assert g.opened == [] and g.on() == 0



def test_while_q2ui_runs_demo_tells_it_the_status_rows_states_when_they_change(fw):
    """What only demo knows for q2ui's status row (status_row_state: where the sound goes, Bluetooth, the lock, a
    card app's sync and server) is written to /tmp/q2ui.status, looked at every 500 ms and written only on a change,
    so at rest nothing is written and q2ui's watch of /tmp never wakes it."""
    from conftest import Files
    files = Files(fw)
    writes = []
    rename = files.rename
    fw.mock('fw_rename', lambda a, b, *_: writes.append(C.string_at(b).decode()) or rename(a, b))
    g = Gate(fw)
    fw.var('g_po_status', C.c_ubyte).value = 0
    fw.mock('mclGetOutputWay', lambda *_: 0)
    assert writes == []  # nothing before q2ui runs
    g.poll()  # started
    g.poll()
    said = files.files.get('/tmp/q2ui.status', b'').decode()
    assert writes == ['/tmp/q2ui.status'] and 'out=none\n' in said and 'lock=0\n' in said
    for _ in range(3):
        g.ms += 600
        g.poll()
    assert len(writes) == 1  # the same states: no write
    fw.var('g_po_status', C.c_ubyte).value = 1  # a jack goes in
    g.ms += 100
    g.poll()
    assert len(writes) == 1  # looked at again only 500 ms on
    g.ms += 500
    g.poll()
    assert len(writes) == 2 and 'out=wire\n' in files.files['/tmp/q2ui.status'].decode()
    g.ended = 1
    g.poll()  # q2ui ended: demo has the screen and its own row
    fw.var('g_po_status', C.c_ubyte).value = 0
    g.ms += 1000
    g.poll()
    assert len(writes) == 2


def test_controls_is_opened_as_homes_controls_row_opens_it(fw):
    """Settings > Controls is the Display page opened on its Controls part (settings_display.c display_open), not a
    page of its own name."""
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = b'controls_page', ASKED, 1
    g.poll()
    assert g.opened == ['systemset/display_page'] and g.on() == 0


def test_q2ui_is_told_its_control_centers_switches(fw):
    """q2ui's Control Center shows demo's settings as demo has them (control_center.c q2ui_cc_state)."""
    from conftest import Files
    files = Files(fw)
    g = Gate(fw)
    fw.var('g_wififlag', C.c_ubyte).value = 1
    g.poll()
    g.poll()
    said = dict(line.split('=', 1) for line in files.files['/tmp/q2ui.status'].decode().splitlines())
    assert said['wifi'] == '1' and said['bton'] == '0' and said['car'] == '0' and said['smart'] == '0' and 'usb' in said
    fw.var('g_wififlag', C.c_ubyte).value = 0
    g.ms += 600
    g.poll()
    assert 'wifi=0\n' in files.files['/tmp/q2ui.status'].decode()


def test_q2uis_switches_flip_only_what_differs(fw):
    """control_center.c q2ui_cc_set, which "set NAME 0|1" from q2ui's queue runs: Wi-Fi through stock's own switch,
    only when it is not already so; Smart Wi-Fi saved; a name it has not is refused."""
    flips, saved = [], []
    fw.mock('wifi_switch', lambda v, *_: flips.append(v) or 0)
    fw.mock('write_int_config', lambda v, sec, key, *_: saved.append((C.string_at(key).decode(), v)) or 0)
    cc = fw.fn('q2ui_cc_set', C.c_int, C.c_char_p, C.c_int)
    fw.var('g_wififlag', C.c_ubyte).value = 1
    assert cc(b'wifi', 1) == 0 and flips == []
    assert cc(b'wifi', 0) == 0 and flips == [1]
    assert cc(b'smart', 1) == 0 and saved[-1] == ('SMARTWIFI', 1)
    assert cc(b'reboot', 1) == -1


def test_q2uis_display_page_sets_light_mode_and_reduce_motion_through_demo(fw):
    """q2ui's Settings > Display: demo writes [IPOD] LIGHT (0 to 2 only) and REDUCEMOTION, which q2ui reads back."""
    saved = []
    fw.mock('write_int_config', lambda v, sec, key, *_: saved.append((C.string_at(sec).decode(), C.string_at(key).decode(), v)) or 0)
    cc = fw.fn('q2ui_cc_set', C.c_int, C.c_char_p, C.c_int)
    assert cc(b'light', 2) == 0 and saved[-1] == ('IPOD', 'LIGHT', 2)
    assert cc(b'light', 3) == -1 and len(saved) == 1
    assert cc(b'motion', 1) == 0 and saved[-1] == ('IPOD', 'REDUCEMOTION', 1)


class Piped(Gate):
    """The gate with q2ui's settings pipe: pipe2 answers READ and WRITE, and what q2ui wrote is read from READ."""
    READ, WRITE = 20, 21

    def __init__(self, fw):
        super().__init__(fw)
        self.sent, self.made = b'', 0
        fw.mock('chapter_pipe2', self.pipe2)

    def pipe2(self, fds, flags, *_):
        self.made += 1
        self.flags = flags
        C.c_int.from_address(fds).value, C.c_int.from_address(fds + 4).value = self.READ, self.WRITE
        return 0

    def read(self, fd, out, room, *_):
        if fd != self.READ:
            return super().read(fd, out, room)
        data, self.sent = self.sent[:room], self.sent[room:]
        C.memmove(out, data, len(data))
        return len(data) if data else -1  # EAGAIN: nothing waiting


def test_q2ui_alone_is_given_the_settings_pipe_open_and_its_number(fw):
    """The write end stays open across q2ui's exec (FD_CLOEXEC cleared in the child alone) and its number follows
    --ctl; both ends close in any other child demo starts, and demo's end never waits."""
    g = Piped(fw)
    fw.mock('fw_fork', lambda *_: 0)
    ran = []  # the arguments as execl has them: the number is in the starter's frame, gone once it returns
    fw.mock('fw_execl', lambda *a: ran.append([C.string_at(p) if p else 0 for p in a[:5]]) or -1)
    g.poll()
    calls = fw.calls()
    order = [n for n, _ in calls]
    execl = order.index('fw_execl')
    assert ran[0] == [BINARY, BINARY, b'--ctl', b'21', 0]
    assert (g.flags & 0x80) and (g.flags & 0x80000) and g.made == 1
    assert ('fw_fcntl', (21, 2, 0)) in [(n, tuple(a[:3])) for n, a in calls[:execl]]
    g.poll()  # started again (a fork answering 0 here): the same pipe
    assert g.made == 1


def test_a_settings_line_down_q2uis_pipe_is_acted_on(fw):
    """control_center.c q2ui_cc_set for each "set NAME VALUE" line, in order, also one split across two reads; a
    line of another verb, or a setting it has not, does nothing."""
    saved = []
    fw.mock('write_int_config', lambda v, sec, key, *_: saved.append((C.string_at(key).decode(), v)) or 0)
    g = Piped(fw)
    g.poll()
    assert g.on() == CHILD
    g.sent = b'set smart 1\nreboot\nset reboot 1\nset motion 1\nset sm'
    g.poll()
    assert saved == [('SMARTWIFI', 1), ('REDUCEMOTION', 1)]
    g.sent = b'art 0\n'
    g.poll()
    assert saved[-1] == ('SMARTWIFI', 0) and len(saved) == 3


def test_without_a_pipe_q2ui_is_started_with_no_ctl(fw):
    """pipe2 refused: q2ui runs as before, its settings rows staying as demo has them."""
    g = Gate(fw)
    fw.mock('chapter_pipe2', lambda *_: -1)
    fw.mock('fw_fork', lambda *_: 0)
    g.poll()
    args = [a for n, a in fw.calls() if n == 'fw_execl'][0]
    assert [C.string_at(p) for p in args[:2]] == [BINARY, BINARY] and args[2] == 0


def test_q2uis_brightness_slider_sets_the_backlight_and_is_told_it_with_the_volume(fw):
    """Control Center's sliders in q2ui: demo tells bright, vol and volmax (vol_cap: how high a step may take the volume);
    "set bright N" is stock's config_lightness for 0 to 100 only."""
    from conftest import Files
    files = Files(fw)
    g = Gate(fw)
    fw.var('g_lightness', C.c_ubyte).value = 55
    fw.var('g_volume', C.c_ubyte).value = 30
    fw.var('g_maxvolume', C.c_ubyte).value = 70
    fw.mock('mclGetOutputWay', lambda *_: 0)
    g.poll()
    g.poll()
    said = dict(line.split('=', 1) for line in files.files['/tmp/q2ui.status'].decode().splitlines())
    assert said['bright'] == '55' and said['vol'] == '30' and said['volmax'] == '70'
    lit = []
    fw.mock('config_lightness', lambda v, *_: lit.append(v) or 0)
    cc = fw.fn('q2ui_cc_set', C.c_int, C.c_char_p, C.c_int)
    assert cc(b'bright', 80) == 0 and lit == [80]
    assert cc(b'bright', 101) == -1 and cc(b'bright', -1) == -1 and lit == [80]
