"""bt_trace.c (docs/internals.md#bluetooth-connection-trace): each change of stock's Bluetooth connection state, as a
bt line in the unified log through demo's stderr, every 500 ms from the UI loop; each connect the link thread starts,
with its target and why (auto, usb, tap, other); the page opening and the found devices it shows."""
import ctypes as C

import pytest

BUDS, CAR = '04:00:6E:D3:96:6D', '60:E6:F0:A1:1F:09'
BTLINKMAC = 0xa4c0d8


class Rig:
    """Stock's Bluetooth globals and link list, the clock, and demo's stderr as lines (level, source, message)."""
    def __init__(self, fw):
        self.fw, self.now, self.links, self.lines, self.acl = fw, 1000, [], [], []
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_write', self.write)
        fw.mock('getBtLinkSize', lambda *_: len(self.links))
        fw.mock('getBtLinkItem', self.item)
        fw.mock('fw_socket', lambda *_: 7)
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('fw_ioctl', self.ioctl)
        self.set(on=1, saved=CAR)

    def set(self, on=None, init=None, conn=None, scan=None, direction=None, saved=None):
        fw = self.fw
        if on is not None:
            fw.var('g_bluetoothflag', C.c_ubyte).value = on
        if init is not None:
            fw.var('fw_ctx_bt_initstatus', C.c_int).value = init
        if conn is not None:
            fw.var('fw_ctx_bt_connectstatus', C.c_int).value = conn
        if scan is not None:
            fw.var('bt_searchtime', C.c_ubyte).value = scan
        if direction is not None:
            fw.var('fw_ctx_bt_a2dp_direction', C.c_int).value = direction
        if saved is not None:
            C.memmove(BTLINKMAC, saved.encode() + b'\0', len(saved) + 1)

    def item(self, i, status, mac, *_):
        m, s = self.links[i]
        C.c_int.from_address(status).value = s
        C.memmove(mac, m.encode() + b'\0', len(m) + 1)
        return 0

    def ioctl(self, fd, request, req, *_):
        if request & 0xffffffff != 0x400448d4:
            return -1
        C.c_uint16.from_address(req + 2).value = len(self.acl)
        for i, mac in enumerate(self.acl):
            c = req + 4 + 16 * i
            C.memmove(c + 2, bytes(reversed(bytes.fromhex(mac.replace(':', '')))), 6)
            C.c_uint8.from_address(c + 8).value = 1
            C.c_uint16.from_address(c + 10).value = 1
        return 0

    def write(self, fd, data, n, *_):
        if fd == 2:
            words = C.string_at(data, n).decode().rstrip('\n').split(' ', 4)
            self.lines.append((words[2], words[3], words[4]))
        return n

    def poll(self, ms=500):
        self.now += ms
        self.fw.fn('bt_trace_poll', None)()

    def said(self):
        out, self.lines = [(lv, msg) for lv, src, msg in self.lines if src == 'bt'], []
        return out


def test_the_first_poll_logs_the_state_once_then_only_changes(fw):
    r = Rig(fw)
    r.set(init=0)
    r.poll()
    assert r.said() == [('I', 'switch on'), ('I', 'stack status 0'), ('I', f'saved device {CAR}'),
                        ('I', 'link setup none'), ('I', 'link list 0'), ('I', 'link thread idle (0)')]
    r.poll()
    r.poll()
    assert r.said() == []
    r.set(scan=10)
    r.poll()
    r.set(scan=0)
    r.poll()
    r.set(init=-102)
    r.poll()
    assert r.said() == [('I', 'scan started'), ('I', 'scan ended'), ('W', 'stack status -102')]


def test_polls_closer_than_500_ms_read_nothing(fw):
    r = Rig(fw)
    r.poll()
    r.said()
    r.set(scan=10)
    r.poll(100)
    assert r.said() == []
    r.poll(400)
    assert r.said() == [('I', 'scan started')]


@pytest.mark.parametrize('why, mac, said', [('auto', CAR, 'auto'), ('tap', CAR, 'tap'), (None, None, 'other'),
                                            ('tap', BUDS, 'other')])  # a tap on another device is not this one
def test_each_connect_the_link_thread_starts_says_its_target_and_why(fw, why, mac, said):
    """At boot and after each stack relaunch the link thread pages BTLINKMAC (auto, through headset.c's autolink); a
    row tap or Connect goes through bt_stock_click (tap); anything else stock started is other."""
    r = Rig(fw)
    r.poll()
    r.said()
    if why:
        fw.fn('bt_trace_why', None, C.c_char_p, C.c_char_p)(why.encode(), mac.encode())
    r.links = [(CAR, 1)]
    r.set(conn=1)
    r.poll()
    assert r.said() == [('I', f'link list 1, last {CAR} status=1 queued'), ('I', 'link thread running (1)'),
                        ('I', f'connect starts to {CAR} why={said}')]
    r.links = [(CAR, 4)]
    r.set(conn=2)
    r.poll()
    assert r.said() == [('I', f'link list 1, last {CAR} status=4 failed'), ('I', 'link thread ended (2)'),
                        ('W', f'connect to {CAR} ended: failed after 500 ms')]


def started(fw, r, mac=CAR):
    """A connect to mac the link thread has started, its lines read."""
    r.poll()
    r.links = [(mac, 1)]
    r.set(conn=1, init=0)
    r.poll()
    r.said()


@pytest.mark.parametrize('change, want', [
    (dict(links=[(CAR, 3)]), ('I', f'connect to {CAR} ended: connected after 1000 ms')),
    (dict(links=[(CAR, 4)], conn=0), ('W', f'connect to {CAR} ended: failed after 1000 ms')),
    (dict(links=[(CAR, 5)], conn=0), ('W', f'connect to {CAR} ended: no link, the link list says connecting')),
    (dict(links=[], conn=0), ('W', f'connect to {CAR} ended: no link, its item is gone')),
    (dict(on=0), ('W', f'connect to {CAR} abandoned: Bluetooth switched off')),
    (dict(init=1), ('W', f'connect to {CAR} abandoned: the stack restarted')),
])
def test_every_connect_that_starts_has_an_outcome_line(fw, change, want):
    """Each connect starts line is followed by one of how it ended: connected, failed (the kernel's reason is in
    q2guard's own connect failed line beside it), no link when the thread stops without one, or abandoned when
    Bluetooth goes off or the stack restarts under it (a BT RESET relaunch, 2026-11-22 06:07:17Z)."""
    r = Rig(fw)
    started(fw, r)
    r.poll()  # half a second more: still connecting, nothing to say
    assert r.said() == []
    if 'links' in change:
        r.links = change.pop('links')
    r.set(**change)
    r.poll()
    got = r.said()
    assert want in got and sum(m.startswith(f'connect to {CAR} ') for _, m in got) == 1
    r.poll()
    r.poll()
    assert not any(m.startswith('connect to') for _, m in r.said())  # said once


def test_a_connect_to_another_device_ends_the_one_before(fw):
    r = Rig(fw)
    started(fw, r)
    r.links = [(CAR, 4), (BUDS, 1)]
    r.set(conn=2)
    r.poll()
    assert ('W', f'connect to {CAR} ended: failed after 500 ms') in r.said()
    r.set(conn=1)
    r.poll()
    assert ('I', f'connect starts to {BUDS} why=other') in r.said()


def test_a_reason_three_seconds_old_is_no_longer_the_reason(fw):
    r = Rig(fw)
    r.poll()
    fw.fn('bt_trace_why', None, C.c_char_p, C.c_char_p)(b'auto', CAR.encode())
    r.poll(3000)
    r.said()
    r.links = [(CAR, 1)]
    r.set(conn=1)
    r.poll()
    assert ('I', f'connect starts to {CAR} why=other') in r.said()


def test_stock_behind_the_kernel_s_link_warns_once(fw):
    """2026-11-19 (V1JBi): the Pixel Buds' link up and streaming while stock's item stayed at 5 and the page said
    Connecting: the log says so, once while it lasts, and again if it comes back."""
    r = Rig(fw)
    r.links = [(BUDS, 5)]
    r.set(conn=1)
    r.poll()
    r.said()
    r.acl = [BUDS]
    r.poll()
    r.poll()
    assert r.said() == [('W', f'link list says connecting for {BUDS}, the kernel holds its link: shown Connected')]
    r.links = [(BUDS, 3)]
    r.poll()
    assert r.said() == [('I', f'link list 1, last {BUDS} status=3 connected')]
    r.links = [(BUDS, 5)]
    r.poll()
    assert r.said()[-1][0] == 'W'


def test_the_page_opening_and_each_found_device_it_shows(fw):
    """The pairing page's side of discovery: q2guard logs when the radio heard a device ("found"); this says when
    the page showed it, once each time the page is open."""
    r = Rig(fw)
    page, shown = fw.fn('bt_trace_page', None), fw.fn('bt_trace_shown', None, C.c_char_p)
    page()
    shown(BUDS.encode())
    shown(BUDS.lower().encode())
    shown(CAR.encode())
    assert r.said() == [('I', 'page opened'), ('I', f'page shows found {BUDS}'), ('I', f'page shows found {CAR}')]
    page()
    shown(BUDS.encode())
    assert r.said() == [('I', 'page opened'), ('I', f'page shows found {BUDS}')]


@pytest.mark.parametrize('links, paired, target, want', [
    ([], 1, CAR, ('I', f'auto-connect (auto) picks {CAR}: the saved device, paired, nothing connected')),
    ([], 0, CAR, ('W', f'auto-connect (auto) skips {CAR}: the saved device, not paired')),
    ([(BUDS, 3)], 1, CAR, ('I', f'auto-connect (auto) skips {CAR}: {BUDS} is connected')),
    ([], 1, BUDS, ('I', f'auto-connect (auto) picks {BUDS}: not the saved device, paired, nothing connected')),
])
def test_each_auto_connect_says_which_device_it_pages_and_why(fw, links, paired, target, want):
    """The boot of 2026-11-19 paged the car (====auto bt[60:E6:F0:A1:1F:09]==) and never the Buds, and a skip said
    nothing: each decision of headset_bt_autolink is a bt line, its target, whether it is the saved device
    (BTLINKMAC), and what stopped it."""
    r = Rig(fw)
    r.links = links
    fw.mock('checkPairDev', lambda *_: paired)
    go = fw.fn('headset_bt_autolink', C.c_int, C.c_char_p)(target.encode())
    assert go == (want[1].split()[2] == 'picks')
    assert r.said() == [want]
