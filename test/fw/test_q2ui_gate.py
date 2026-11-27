"""q2ui_gate.c: the screen handed to q2ui and taken back (docs/ui-rewrite.md#cutover). demo starts q2ui as its child
while /mnt/data/ui-target says q2ui and the image has /usr/bin/q2ui, paints nothing and leaves the wheel, Centre,
Return and touches to it; it takes the screen back for a window of its own and gives it again on Home; and it opens
a page of its own that q2ui asks for as it leaves. With no target or no q2ui in the image, which is every default
build, the gate never closes."""
import ctypes as C
import struct

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
    # the status file's writes (the networks' own file, /tmp/q2ui.wifi, is test_wifi_flows.py's)
    fw.mock('fw_rename', lambda a, b, *_: (C.string_at(b) == b'/tmp/q2ui.status' and writes.append(C.string_at(b).decode()))
            or rename(a, b))
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


def test_q2uis_playback_page_sets_gapless_and_smart_crossfade_through_demo(fw):
    """q2ui's Settings > Playback: Gapless as stock's own row sets it (0x4bcfa4: PLAYSET GAPLESS, mclSetGaplessMode),
    and Smart Crossfade as its row does (crossfade.c xfade_click: Q2POD XFADE, and on turns Gapless on with it)."""
    saved = []
    fw.mock('write_int_config', lambda v, sec, key, *_: saved.append((C.string_at(sec).decode(), C.string_at(key).decode(), v)) or 0)
    gapless = C.c_ubyte.from_address(O['MCL_GAPLESS'])
    gapless.value = 0
    fw.mock('mclSetGaplessMode', lambda on, *_: setattr(gapless, 'value', on))
    cc = fw.fn('q2ui_cc_set', C.c_int, C.c_char_p, C.c_int)
    assert cc(b'gapless', 1) == 0 and saved == [('PLAYSET', 'GAPLESS', 1)] and gapless.value == 1
    assert cc(b'gapless', 0) == 0 and saved[-1] == ('PLAYSET', 'GAPLESS', 0) and gapless.value == 0
    saved.clear()
    assert cc(b'xfade', 1) == 0 and saved == [('Q2POD', 'XFADE', 1), ('PLAYSET', 'GAPLESS', 1)] and gapless.value == 1
    saved.clear()
    assert cc(b'xfade', 0) == 0 and saved == [('Q2POD', 'XFADE', 0)] and gapless.value == 1
    assert cc(b'xfade', 0) == 0 and saved == [('Q2POD', 'XFADE', 0)]  # already off: nothing written again

def test_q2uis_bluetooth_page_switches_bluetooth_as_stocks_row_does(fw):
    """q2ui's Settings > Bluetooth: stock's switch row (btmenu_setting_row_on_click, 0x4c4298, row 0) runs
    switchBluetooth(g_bluetoothflag, 1), which turns it the other way and saves it, and does nothing while the stack is
    starting (bt_initstatus set) but its busy toast; demo does the same for "set bt 0|1", only when it differs, and
    refuses it while the stack starts."""
    calls = []
    fw.mock('switchBluetooth', lambda on, save, *_: calls.append((on, save)) or 0)
    cc = fw.fn('q2ui_cc_set', C.c_int, C.c_char_p, C.c_int)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    fw.var('fw_ctx_bt_initstatus', C.c_int).value = 0
    assert cc(b'bt', 1) == 0 and calls == []
    assert cc(b'bt', 0) == 0 and calls == [(1, 1)]
    fw.var('g_bluetoothflag', C.c_ubyte).value = 0
    fw.var('fw_ctx_bt_initstatus', C.c_int).value = -102
    assert cc(b'bt', 1) == -1 and calls == [(1, 1)]
    fw.var('fw_ctx_bt_initstatus', C.c_int).value = 0
    assert cc(b'bt', 1) == 0 and calls == [(1, 1), (0, 1)]


def test_a_bluetooth_flip_while_its_stack_starts_is_queued_and_done_once_it_is_up(fw):
    """Bluetooth turned on starts its stack for about 3 s (bt_initstatus 1, "bt stack status 1" in demo's log), in which
    stock's switch does nothing. A "set bt" from q2ui then is kept, the last one winning, and done by q2ui_cc_poll once
    the stack is up; demo says btbusy=1 meanwhile, so q2ui's switch keeps showing the flip."""
    calls = []
    fw.mock('switchBluetooth', lambda on, save, *_: calls.append((on, save)) or 0)
    cc = fw.fn('q2ui_cc_set', C.c_int, C.c_char_p, C.c_int)
    poll = fw.fn('q2ui_cc_poll', None)
    state = fw.fn('q2ui_cc_state', C.c_uint, C.c_char_p, C.c_uint)
    flag, init = fw.var('g_bluetoothflag', C.c_ubyte), fw.var('fw_ctx_bt_initstatus', C.c_int)

    def told():
        b = C.create_string_buffer(400)
        state(b, 400)
        return dict(line.split('=', 1) for line in b.value.decode().splitlines())
    flag.value, init.value = 1, 1
    assert cc(b'bt', 0) == 0 and calls == []
    assert cc(b'bt', 1) == 0 and cc(b'bt', 0) == 0 and calls == []  # taps in a row: the last one stands
    poll()
    assert calls == [] and told().get('btbusy') == '1'
    init.value = 0
    poll()
    assert calls == [(1, 1)]
    flag.value = 0
    poll()
    assert calls == [(1, 1)] and told().get('btbusy') == '0'
    flag.value, init.value = 0, 1
    assert cc(b'bt', 0) == 0  # already as asked: nothing queued
    init.value = 0
    poll()
    assert calls == [(1, 1)]


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


def test_q2ui_is_told_airplay_and_a_cable_for_its_control_center_sheet(fw):
    """q2ui's sheet lights AirPlay's circle by its receiver's flag and shows the USB row only while a cable is in:
    demo tells airplay= (g_airplayflag) and cable= (usb_ui.c usb_ui_cable), each as it changes."""
    from conftest import Files
    files = Files(fw)
    fw.mock('mclGetOutputWay', lambda *_: 0)
    air, det = fw.var('g_airplayflag', C.c_ubyte), fw.var('g_usbdet_value', C.c_ubyte)
    seen, g = [], Gate(fw)
    for a, c in ((0, 0), (1, 1)):
        air.value, det.value = a, c
        g.ms += 60000  # past the gate's pause between looks (Q2UI_STATUS_MS)
        g.poll()
        g.poll()
        said = dict(line.split('=', 1) for line in files.files['/tmp/q2ui.status'].decode().splitlines())
        seen.append((said.get('airplay'), said.get('cable')))
    assert seen == [('0', '0'), ('1', '1')]


def told_cover(fw, mcl, path, parsed, kind):
    """What demo tells q2ui of the playing song's cover (cover=), path playing and the player's cover thread last done
    with parsed, its type kind."""
    from conftest import Deques, Files
    files = Files(fw)
    g = Gate(fw)
    fw.mock('mclGetOutputWay', lambda *_: 0)
    r, s = C.create_string_buffer(O['REC_SIZE'] + 8), C.create_string_buffer(path)
    struct.pack_into('<Q', r, O['REC_PATH'], C.addressof(s))
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = Deques(fw).new([C.addressof(r)])
    mcl.pos = 0
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = parsed
    fw.var('g_playcover_type', C.c_ubyte).value = kind
    g.poll()
    g.poll()
    return dict(line.split('=', 1) for line in files.files['/tmp/q2ui.status'].decode().splitlines()).get('cover')


@pytest.mark.parametrize('kind, said', ((1, '/tmp/coverpic.jpg'), (2, '/tmp/externpic.jpg'), (4, '/tmp/externpic.jpg'),
                                        (3, ''), (0, '')))
def test_q2ui_is_told_which_file_holds_the_playing_songs_cover(fw, mcl, kind, said):
    """q2ui shows the file demo names for the playing song's cover (coverflow.c song_cover_url): the player writes an
    embedded picture to /tmp/coverpic.jpg (type 1) and a folder image or a download to /tmp/externpic.jpg (2, 4), and
    leaves the other file as the song before left it. None found (3), or not parsed yet (0), is cover= alone."""
    song = b'/mnt/mmc/Music/a.flac'
    assert told_cover(fw, mcl, song, song, kind) == said


def test_q2ui_is_told_no_cover_while_the_player_has_not_parsed_the_playing_song(fw, mcl):
    """The player's type is the song before's until its cover thread is done with this one (now_cover)."""
    assert told_cover(fw, mcl, b'/mnt/mmc/Music/b.flac', b'/mnt/mmc/Music/a.flac', 1) == ''


def test_q2ui_is_told_abouts_versions(fw, mcl):
    """q2ui's About shows the product and this build's version and the stock firmware's as demo's About does
    (vamp_version: the version without its " Muse")."""
    from conftest import Files
    files = Files(fw)
    g = Gate(fw)
    fw.mock('mclGetOutputWay', lambda *_: 0)
    g.poll()
    g.poll()
    said = dict(line.split('=', 1) for line in files.files['/tmp/q2ui.status'].decode().splitlines())
    assert said['product'] == 'Vamp' and said['firmware'] == 'V1.36' and said['version'] and 'Muse' not in said['version']


def test_q2uis_clicks_down_its_pipe_are_demos_key_tone_one_a_pass(fw):
    """q2ui reads the wheel and the keys itself, so demo's own click (buzzeer_switch, hooked as ringnav_buzzer: Key
    Tone, the Clicker's route) never runs for them: q2ui sends "click 0" for a wheel tick and "click 1" for a press,
    and demo clicks as it would have, a press as a button's sound (clicker_kind). A fast spin's clicks that reach one
    pass are one click, as the buzzer is busy for them anyway."""
    kind = fw.var('clicker_kind', C.c_int)
    clicks = []
    fw.mock('buzzeer_switch', lambda on, *_: clicks.append((on, kind.value)) or 0)
    g = Piped(fw)
    g.poll()
    g.sent = b'click 0\nclick 0\nclick 0\n'
    g.poll()
    assert clicks == [(1, 0)]
    g.sent = b'click 1\n'
    g.poll()
    assert clicks == [(1, 0), (1, 2)] and kind.value == 0
    g.sent = b'click 0\nclick 1\n'  # both in one pass: one of each
    g.poll()
    assert clicks[2:] == [(1, 0), (1, 2)]
    g.poll()
    assert len(clicks) == 4


def test_q2uis_wake_lights_the_dark_screen_as_a_press_would(fw):
    """A question on q2ui's notice socket asked with the screen dark (ui/notice.c) sends "wake": demo lights the screen
    as a press would (awake_screen, as findmy.c does), and leaves a lit one alone; "wake" with more on its line is
    another verb, dropped."""
    lit = fw.var('g_backlight_status', C.c_ubyte)
    woke = []
    fw.mock('awake_screen', lambda *_: woke.append(lit.value) or 0)
    g = Piped(fw)
    g.poll()
    assert g.on() == CHILD
    lit.value = 0
    g.sent = b'wake\n'
    g.poll()
    assert woke == [0]
    lit.value = 1
    g.sent = b'wake\n'
    g.poll()
    lit.value = 0
    g.sent = b'wake up\nwak\n'
    g.poll()
    assert woke == [0]


def test_q2uis_screenoff_turns_the_lit_screen_off_as_now_playings_short_hold_does(fw):
    """q2ui's Now Playing reads Centre itself, so its short hold or double press sends "screenoff": demo replays a
    Centre release to stock's on_wm_keyup_fun as its own Now Playing does (nav/now_playing.c np_single), only while
    the screen is lit."""
    lit = fw.var('g_backlight_status', C.c_ubyte)
    g = Piped(fw)
    g.poll()
    lit.value = 1
    g.sent = b'screenoff\n'
    g.poll()
    keys = [C.c_int.from_address(c[1][1] + O['EVENT_KEY']).value for c in fw.calls('on_wm_keyup_fun')]
    assert keys == [O['KEY_CENTER']]  # the one key on_wm_keyup_fun reads
    lit.value = 0
    g.sent = b'screenoff\nscreenoff now\n'
    g.poll()
    assert len(fw.calls('on_wm_keyup_fun')) == 1


@pytest.mark.parametrize('back, then', [(b'playing_page', [b'--then', b'now']), (b'home_page', [])])
def test_q2ui_left_for_a_page_that_ends_on_demos_now_playing_comes_back_on_its_own(fw, back, then):
    """Search's strip is demo's (searchstrip_page): a result chosen there plays and demo opens its Now Playing, which
    counts as Home for the gate. q2ui started again then opens its own Now Playing over the pages it had ("--then
    now"), as demo's pick lands on Now Playing; left for Home, it comes back on its pages alone."""
    ran = []
    fw.mock('fw_execl', lambda *a: ran.append([C.string_at(p) if p else 0 for p in a[:6]]) or -1)
    g = Gate(fw)
    g.poll()
    g.ask, g.status, g.ended = b'searchstrip_page\n', ASKED, 1
    g.poll()
    g.ended = 0
    g.top = b'searchstrip_page'
    g.poll()
    g.top = back
    fw.mock('fw_fork', lambda *_: 0)  # the child's side this time: what it runs
    g.poll()
    assert ran and ran[-1][:2] == [BINARY, BINARY] and ran[-1][2:2 + len(then) + 1] == then + [0]


def test_play_pause_is_q2uis_only_while_its_row_editor_asks_for_it(fw):
    """q2ui's Edit Library shows and hides a row by Play/Pause (docs/oled-design.md#edit-library), which is otherwise
    demo's (the music's key): "playkey 1" down q2ui's pipe makes its presses q2ui's until "playkey 0", and a q2ui
    started again begins with them demo's, so one that ended inside its editor cannot keep the key."""
    g = Piped(fw)
    g.poll()
    assert g.input(UP, KEYS['KEY_PLAY']) == 0
    g.sent = b'playkey 1\n'
    g.poll()
    assert g.input(DOWN, KEYS['KEY_PLAY']) == 1 and g.input(UP, KEYS['KEY_PLAY']) == 1
    assert g.input(UP, KEYS['KEY_PREV']) == 1  # the wheel's stay q2ui's
    g.sent = b'playkey 0\n'
    g.poll()
    assert g.input(UP, KEYS['KEY_PLAY']) == 0
    g.sent = b'playkey 1\n'
    g.poll()
    g.ms += 60000
    g.status, g.ended = 15, 1  # ended from outside, a minute in: started again
    g.poll()
    g.ended = 0
    g.poll()
    assert g.forks == 2 and g.on() == CHILD and g.input(UP, KEYS['KEY_PLAY']) == 0
