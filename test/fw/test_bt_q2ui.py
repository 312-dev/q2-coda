"""bt_ui.c's part for q2ui's own Bluetooth page (ui/page_bluetooth.c, docs/ui-rewrite.md#cutover): with stock's page not
open, a scan q2ui asks for is counted down and read as stock's page's timer would; the devices are told paired first,
a found one only once the scan heard it; a connect or disconnect is stock's row click's own steps on the device by its
MAC; a forget is Forget This Device's."""
import ctypes as C

from test_bt_ui import CAR, DEVICES, HEAD, SEEN_FILE, SPEAKER, Page


class Q2ui:
    """Stock's Bluetooth page closed (its view gone), as it is while q2ui has the screen."""
    def __init__(self, fw, devices=DEVICES, answer=True):
        self.fw, self.p = fw, Page(fw, devices, answer=answer)
        self.p.ui.destroy(self.p.win)
        self.calls = []
        fw.mock('getBtPairSize', lambda *_: sum(d[2] != 'found' for d in devices))
        fw.mock('btStartSearch', self.start)
        fw.mock('btStopSearch', lambda *_: self.calls.append('stop') or 0)
        fw.mock('getSearchResult', lambda *_: self.calls.append('read') or 0)
        self.tick = C.cast(getattr(fw.lib, 'bt_ui__q2ui_tick'), C.c_void_p).value

    def start(self, *_):
        self.calls.append('start')
        self.fw.var('bt_searchtime', C.c_ubyte).value = 50
        return 0

    def ticks(self, n=1):
        """n passes of the timers q2ui_tick added, each as the loop runs it; RET_REPEAT (8) keeps one."""
        for _ in range(n):
            for t, (f, ctx, ms) in list(self.p.ui.timers.items()):
                if f == self.tick:
                    self.p.now += ms
                    if C.CFUNCTYPE(C.c_int, C.c_void_p)(f)(ctx) != 8:
                        del self.p.ui.timers[t]

    def told(self, room=2048):
        out = C.create_string_buffer(room)
        n = self.fw.fn('bt_q2ui_devices', C.c_uint, C.c_void_p, C.c_uint)(out, room)
        return out.raw[:n].decode()


def test_q2uis_scan_is_counted_down_and_read_as_stocks_page_would(fw):
    """Started as the page starts one (btStartSearch: 50 passes of 200 ms), its results read each second, stopped and
    read at 1, then the timer goes."""
    q = Q2ui(fw)
    assert fw.fn('bt_q2ui_scan', C.c_int)() == 0
    q.ticks()
    assert q.calls[0] == 'start' and [ms for _, _, ms in q.p.ui.timers.values()] == [200]
    q.ticks(4)
    assert q.calls.count('read') == 0
    q.ticks()
    assert q.calls.count('read') == 1  # a second in: 45
    q.ticks(43)
    assert 'stop' not in q.calls
    q.ticks()  # 1: the end
    assert q.calls[-2:] == ['stop', 'read']
    fw.var('bt_searchtime', C.c_ubyte).value = 0  # btStopSearch's
    q.ticks()
    assert not q.p.ui.timers


def test_a_scan_waits_for_the_stack_and_a_connect_and_none_is_made_off(fw):
    q = Q2ui(fw)
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    fw.fn('bt_q2ui_scan', C.c_int)()
    q.ticks(3)
    assert q.calls == []
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 0
    q.ticks()
    assert q.calls[0] == 'start'
    fw.var('g_bluetoothflag', C.c_ubyte).value = 0
    q.ticks()
    assert not q.p.ui.timers and fw.fn('bt_q2ui_scan', C.c_int)() == 1


def test_the_devices_are_told_paired_first_with_their_state_and_a_found_one_once_heard(fw):
    q = Q2ui(fw, answer=False)
    q.p.links = [(HEAD, 3)]
    fw.var('bt_searchtime', C.c_ubyte).value = 0
    assert q.told() == f'scan 0\n1 2 {HEAD} WH-1000XM5\n1 0 {CAR} BMW 6R81\n'  # the speaker not heard yet
    fw.fn('bt_q2ui_scan', C.c_int)()
    q.ticks()  # started: the found device asked about
    assert SEEN_FILE in ' '.join(q.p.commands)
    ask = [c for c in q.p.commands if SEEN_FILE in c][-1]
    import re
    q.p.files.files[SEEN_FILE] = (re.search(r"echo '(#\d+)'", ask)[1] + f'\n{SPEAKER} seen\n').encode()
    q.ticks(5)
    assert q.told() == f'scan 1\n1 2 {HEAD} WH-1000XM5\n1 0 {CAR} BMW 6R81\n0 0 {SPEAKER} JBL Flip 6\n'
    fw.var('g_bluetoothflag', C.c_ubyte).value = 0
    assert q.told() == 'scan 1\n'


def test_a_name_is_escaped_and_whole_lines_only_fit(fw):
    q = Q2ui(fw, devices=[(HEAD, 'Back\\slash\nNew', 'paired'), (CAR, 'BMW 6R81', 'paired')])
    assert q.told() == f'scan 0\n1 0 {HEAD} Back\\\\slash\\nNew\n1 0 {CAR} BMW 6R81\n'
    assert q.told(50) == f'scan 0\n1 0 {HEAD} Back\\\\slash\\nNew\n'


def test_q2uis_connect_is_stocks_row_click_on_the_device_by_its_mac(fw):
    """btStopSearch first, then handleBluetoothLink on its index; a disconnect (its 1) puts the output back on a jack."""
    q = Q2ui(fw)
    links = []
    fw.mock('handleBluetoothLink', lambda i, *_: q.calls.append(('link', i)) or (1 if i == 0 else 0))
    fw.mock('check_outputdevice', lambda was, *_: 1)
    fw.mock('mclGetSavePlayStatus', lambda *_: 0)
    fw.mock('config_outputchannel', lambda out, how, *_: links.append((out, how)) or 0)
    fw.var('g_headset_output', C.c_ubyte).value = 2
    assert fw.fn('bt_q2ui_link', C.c_int, C.c_char_p)(CAR.encode()) == 0
    assert q.calls == ['stop', ('link', 1), 'read'] and links == []
    assert fw.fn('bt_q2ui_link', C.c_int, C.c_char_p)(HEAD.encode()) == 0
    assert ('link', 0) in q.calls and links == [(1, 3)] and fw.var('g_headset_output', C.c_ubyte).value == 1
    assert fw.fn('bt_q2ui_link', C.c_int, C.c_char_p)(b'AA:BB:CC:DD:EE:99') == 1


def test_a_connect_running_is_stopped_by_its_own_device_and_refuses_another(fw):
    q = Q2ui(fw)
    fw.mock('handleBluetoothLink', lambda i, *_: q.calls.append(('link', i)) or 0)
    updates = []
    fw.mock('updateBtLinkStatus', lambda s, m, *_: updates.append((s, C.string_at(m).decode())) or 0)
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    q.p.links = [(CAR, 5)]
    assert fw.fn('bt_q2ui_link', C.c_int, C.c_char_p)(HEAD.encode()) == 1
    assert fw.fn('bt_q2ui_link', C.c_int, C.c_char_p)(CAR.encode()) == 0
    assert updates == [(4, CAR)] and ('link', 1) not in q.calls


def test_q2uis_forget_is_forget_this_devices(fw):
    q = Q2ui(fw)
    fw.mock('handleBluetoothPair', lambda i, *_: q.calls.append(('pair', i)) or 1)
    fw.mock('toolsWriteConfig', lambda *_: 0)
    assert fw.fn('bt_q2ui_forget', C.c_int, C.c_char_p)(CAR.encode()) == 0
    assert ('pair', 1) in q.calls and 'Device Forgotten' in q.p.ui.toasts
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    assert fw.fn('bt_q2ui_forget', C.c_int, C.c_char_p)(HEAD.encode()) == 1


def test_q2uis_bluetooth_lines_down_its_pipe_and_the_devices_file(fw):
    """q2ui_gate.c: "btscan", "btlink MAC" and "btforget MAC" reach bt_ui.c; a MAC that is not one is dropped; the
    devices go to /tmp/q2ui.bt."""
    from test_q2ui_gate import Piped
    q = Q2ui(fw)
    files = q.p.files
    fw.mock('handleBluetoothLink', lambda i, *_: q.calls.append(('link', i)) or 0)
    said = []

    def write(fd, data, n, *_):  # ulog's line on stderr: "<time> <up> <level> <src> <msg>"
        text = C.string_at(data, n).decode()
        if fd == 2 and ' q2ui ' in text:
            said.append(text.split(' q2ui ', 1)[1].strip())
        return n
    fw.mock('fw_write', write)
    g = Piped(fw)
    g.poll()
    g.sent = f'btscan\nbtlink {CAR}\nbtlink $(reboot)\nbtforget AA:BB\n'.encode()
    g.poll()
    assert ('link', 1) in q.calls and len([c for c in q.calls if isinstance(c, tuple)]) == 1
    assert [w for w in said if w.startswith('bt')] == ['btscan 0', 'btlink 0']  # the others dropped unread
    assert [ms for _, _, ms in q.p.ui.timers.values()] == [200]
    assert files.files['/tmp/q2ui.bt'].decode().startswith('scan 1\n1 0 ')
