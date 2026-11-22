"""patch/bt_vol_event.c: BlueZ's PropertiesChanged on MediaTransport1 over the system bus's wire protocol, and
bt_volume_poll (playback_power.c) reading a headset's volume change on the pass after it, with its timed reads only
a fallback. The bus is a test's: bytes in and out of fw_socket's stubs, and, where the machine has dbus-daemon and
dbus-python, a real daemon the C logs in to, subscribes on and hears from."""
import ctypes as C
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import tempfile
import time

import pytest

from conftest import O
from test_nav_loop import Btvol, Front, VolPoll, stock_bytes  # noqa: F401 (a fixture)

RULE = (b"type='signal',sender='org.bluez',interface='org.freedesktop.DBus.Properties',"
        b"member='PropertiesChanged',arg0='org.bluez.MediaTransport1'")
FALLBACK, GAP = O['BT_VOLUME_FALLBACK_MS'], O['BT_VOLUME_EVENT_GAP_MS']


def pad(b, n):
    return b + b'\0' * (-len(b) % n)


def message(kind, fields, body=b'', serial=7, endian=b'l'):
    """A D-Bus message: fields as (code, signature letter, text)."""
    order = '<' if endian == b'l' else '>'
    out = b''
    for code, sig, text in fields:
        out = pad(out, 8) + bytes([code, 1, ord(sig), 0])
        out += (bytes([len(text)]) if sig == 'g' else struct.pack(order + 'I', len(text))) + text + b'\0'
    return endian + bytes([kind, 0, 1]) + struct.pack(order + 'III', len(body), serial, len(out)) + pad(out, 8) + body


def changed(member=b'PropertiesChanged', kind=4, endian=b'l'):
    """bluetoothd's signal for a transport's Volume (a q in a{sv}); body as BlueZ sends it."""
    order = '<' if endian == b'l' else '>'
    u32 = lambda v: struct.pack(order + 'I', v)  # noqa: E731
    iface = b'org.bluez.MediaTransport1'
    entry = u32(6) + b'Volume\0' + b'\x01q\0' + struct.pack(order + 'H', 64)  # {s: v(q)}, 8-aligned, 16 bytes
    body = pad(pad(u32(len(iface)) + iface + b'\0', 4) + u32(len(entry)), 8) + entry + u32(0)
    return message(kind, [(1, 'o', b'/org/bluez/hci0/dev_AA_BB_CC_DD_EE_07/sep1/fd0'),
                          (2, 's', b'org.freedesktop.DBus.Properties'), (3, 's', member),
                          (7, 's', b':1.4'), (8, 'g', b'sa{sv}as')], body, endian=endian)


def parse(data):
    """The messages in data: (type, serial, {code: text}, body)."""
    out = []
    while data:
        body_len, serial, fields_len = struct.unpack_from('<III', data, 4)
        kind, end = data[1], 16 + fields_len
        fields, i = {}, 16
        while i < end:
            i += -i % 8
            code, sig = data[i], chr(data[i + 2])
            i += 4
            if sig == 'g':
                n, i = data[i], i + 1
            else:
                i += -i % 4
                n, i = struct.unpack_from('<I', data, i)[0], i + 4
            fields[code] = data[i:i + n]
            assert data[i + n] == 0
            i += n + 1
        start = end + (-end % 8)
        out.append((kind, serial, fields, data[start:start + body_len]))
        data = data[start + body_len:]
    return out


class Bus:
    """fw_socket and the calls after it, against bytes: what the C sends, and what recv hands it, a chunk a call."""
    def __init__(self, fw, uid=0, reply=b'OK 1234deadbeef\r\n', fd=9):
        self.fw, self.sent, self.chunks, self.fd = fw, b'', [reply], fd
        fw.mock('fw_getuid', lambda *_: uid)
        fw.mock('fw_socket', lambda family, kind, *_: self.fd if (family, kind) == (1, 2) else -1)
        fw.mock('fw_connect', self.connect)
        fw.mock('fw_send', self.send)
        fw.mock('fw_recv', self.recv)
        self.path = None

    def connect(self, fd, addr, size, *_):
        assert fd == self.fd and struct.unpack_from('<H', C.string_at(addr, 2))[0] == 1
        self.path = C.string_at(addr + 2)
        return 0

    def send(self, fd, data, n, flags, *_):
        assert fd == self.fd and flags == 0x4000  # MSG_NOSIGNAL: a bus gone is an error, not SIGPIPE
        self.sent += C.string_at(data, n)
        return n

    def recv(self, fd, out, n, *_):
        if not self.chunks:
            return 0
        chunk = self.chunks.pop(0)
        assert len(chunk) <= n
        C.memmove(out, chunk, len(chunk))
        return len(chunk)


def connect(fw, **kw):
    bus = Bus(fw, **kw)
    return bus, fw.fn('bt_vol_event_connect', C.c_int)()


@pytest.mark.parametrize('uid, hexuid', [(0, b'30'), (1000, b'31303030')])
def test_it_logs_in_says_hello_and_subscribes_to_the_transports_signal(fw, uid, hexuid):
    bus, ok = connect(fw, uid=uid)
    assert ok == 1 and fw.fn('bt_vol_event_live', C.c_int)() == 1
    assert bus.path == b'/var/run/dbus/system_bus_socket'
    login = b'\0AUTH EXTERNAL ' + hexuid + b'\r\nBEGIN\r\n'
    assert bus.sent.startswith(login)
    hello, match = parse(bus.sent[len(login):])
    bus_fields = {1: b'/org/freedesktop/DBus', 6: b'org.freedesktop.DBus', 2: b'org.freedesktop.DBus'}
    assert hello == (1, 1, {**bus_fields, 3: b'Hello'}, b'')
    assert match == (1, 2, {**bus_fields, 3: b'AddMatch', 8: b's'}, struct.pack('<I', len(RULE)) + RULE + b'\0')


@pytest.mark.parametrize('reply', [b'REJECTED EXTERNAL\r\n', b'', b'O'])
def test_a_refused_or_silent_login_leaves_the_timed_reads(fw, reply):
    bus, ok = connect(fw, reply=reply)
    assert ok == 0 and fw.fn('bt_vol_event_live', C.c_int)() == 0
    assert fw.calls('fw_close')[-1][1][0] == bus.fd


def test_no_bus_leaves_the_timed_reads(fw):
    fw.mock('fw_socket', lambda *_: -1)
    assert fw.fn('bt_vol_event_connect', C.c_int)() == 0 and fw.fn('bt_vol_event_live', C.c_int)() == 0


class Events:
    """A logged-in connection; feed() hands the bus's bytes to one wait each."""
    def __init__(self, fw):
        self.bus, ok = connect(fw)
        assert ok
        self.wait, self.take = fw.fn('bt_vol_event_wait', C.c_int), fw.fn('bt_vol_event_take', C.c_int)

    def feed(self, *chunks):
        self.bus.chunks = list(chunks)
        return [self.wait() for _ in chunks]


def test_each_properties_changed_counts_and_the_replies_and_other_signals_do_not(fw):
    ev = Events(fw)
    replies = message(2, [(7, 's', b'org.freedesktop.DBus')], serial=1)  # Hello's reply, its own fields aside
    acquired = message(4, [(1, 'o', b'/org/freedesktop/DBus'), (2, 's', b'org.freedesktop.DBus'),
                           (3, 's', b'NameAcquired'), (8, 'g', b's')], struct.pack('<I', 5) + b':1.9\0\0')
    assert ev.feed(replies + acquired) == [1] and ev.take() == 0
    assert ev.feed(changed()) == [1] and ev.take() == 1 and ev.take() == 0
    assert ev.feed(changed(endian=b'B')) == [1] and ev.take() == 1  # a big-endian sender's, as relayed


def test_a_signal_split_across_reads_and_two_in_one_count_once_each_whole(fw):
    ev = Events(fw)
    one = changed()
    assert ev.feed(one[:5], one[5:40]) == [1, 1] and ev.take() == 0
    assert ev.feed(one[40:]) == [1] and ev.take() == 1
    two = fw.var('bt_vol_event__bve', C.c_char)  # the count itself: two signals in one read
    before = C.c_uint.from_address(C.addressof(two) + 8).value
    ev.feed(one + one)
    assert C.c_uint.from_address(C.addressof(two) + 8).value == before + 2


def test_a_message_too_big_for_the_buffer_is_dropped_and_the_next_one_counts(fw):
    ev = Events(fw)
    big = message(4, [(3, 's', b'PropertiesChanged')], b'\0' * 5000)
    ev.feed(big[:1000], big[1000:3000], big[3000:4500], big[4500:] + changed())
    assert ev.take() == 1


def test_the_bus_gone_ends_the_wait(fw):
    ev = Events(fw)
    assert ev.feed() == [] and ev.wait() == 0


def test_the_thread_starts_once_and_is_live_only_after_its_login(fw):
    started = []
    fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: started.append(fn) or 0)
    start, live = fw.fn('bt_vol_event_start', None), fw.fn('bt_vol_event_live', C.c_int)
    start(), start()
    assert len(started) == 1 and live() == 0


# bt_volume_poll with the signal subscribed.
def subscribed(fw, **kw):
    vol = VolPoll(fw, **kw)
    ev = Events(fw)
    vol.poll()  # a first read
    return vol, ev


@pytest.mark.parametrize('lit', [1, 0])
def test_subscribed_the_level_is_read_every_fallback_period_lit_or_dark(fw, lit):
    vol, _ = subscribed(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    reads = vol.reads()
    vol.clock.now += FALLBACK - 1
    vol.poll()
    assert vol.reads() == reads
    vol.clock.now += 1
    vol.poll()
    assert vol.reads() == reads + 1


def test_a_headsets_change_shows_on_the_lit_screen_on_the_pass_after_its_signal(fw, stock_bytes):
    """The latency: the overlay opens on the first UI pass after BlueZ's signal, the clock not moved, where the
    timed read alone would wait the fallback period."""
    stock_bytes(0xa4cbb5, 1)
    stock_bytes(O['VOL_DIALOG_COUNT'], 1)
    front = Front(fw, b'playing_page')
    vol, ev = subscribed(fw, volume=40)
    vol.clock.now += 1000
    vol.headset.level = 127  # the headset's own key
    vol.poll()
    assert front.opened == [] and fw.var('g_volume', C.c_ubyte).value == 40  # no signal yet: nothing read
    ev.feed(changed())
    vol.poll()  # the next pass, the same millisecond
    assert front.opened == [b'dialog/volume_dialog'] and fw.var('g_volume', C.c_ubyte).value == 100


def test_a_burst_of_signals_reads_at_most_once_a_gap(fw):
    vol, ev = subscribed(fw)
    vol.clock.now += 1000
    ev.feed(changed())
    vol.poll()
    reads = vol.reads()
    vol.clock.now += 100
    ev.feed(changed())  # our own write echoed, or the headset's key held
    vol.poll()
    assert vol.reads() == reads
    vol.clock.now += GAP - 100
    vol.poll()
    assert vol.reads() == reads + 1  # the pending signal, GAP after the last read
    vol.clock.now += GAP
    vol.poll()
    assert vol.reads() == reads + 1  # nothing pending: back to the fallback


def test_a_headset_that_refused_keeps_the_slow_pace_signal_or_not(fw):
    vol, ev = subscribed(fw)
    fw.var('navigation__btvol', Btvol).failed = 1
    vol.clock.now += 1000
    ev.feed(changed())
    vol.poll()
    reads = vol.reads()
    vol.clock.now += O['BT_VOLUME_SLOW_MS'] - 1000
    vol.poll()
    assert vol.reads() == reads + 1


def test_the_first_ready_pass_starts_the_subscription(fw):
    started = []
    fw.mock('fw_pthread_create', lambda *_: started.append(1) or 0)
    vol = VolPoll(fw)
    vol.poll()
    assert started == [1]


# A real dbus-daemon: the C's login, Hello and AddMatch accepted, and bluetoothd's signal (a process owning org.bluez)
# delivered to it while another interface's is not.
EMIT = r'''
import sys, dbus, dbus.service
bus = dbus.bus.BusConnection(sys.argv[1])
bus.request_name('org.bluez')
props = 'org.freedesktop.DBus.Properties'
def emit(iface):
    m = dbus.lowlevel.SignalMessage('/org/bluez/hci0/dev_AA/sep1/fd0', props, 'PropertiesChanged')
    m.append(iface, dbus.Dictionary({'Volume': dbus.UInt16(64)}, signature='sv'), dbus.Array([], signature='s'))
    bus.send_message(m)
emit('org.bluez.Device1'); emit('org.bluez.MediaTransport1'); bus.flush()
'''


def real_bus() -> bool:
    """dbus-daemon here, and a system python with dbus-python to own org.bluez on it."""
    if not shutil.which('dbus-daemon') or not os.path.exists('/usr/bin/python3'):
        return False
    return subprocess.run(['/usr/bin/python3', '-c', 'import dbus'], capture_output=True).returncode == 0


@pytest.mark.skipif(not real_bus(), reason='needs dbus-daemon and dbus-python')
def test_a_real_bus_takes_the_login_and_the_match_and_delivers_the_signal(fw):
    # The bus's socket in a short folder of its own: dbus-daemon refuses a unix:path= longer than 99 bytes
    # (_DBUS_MAX_SUN_PATH_LENGTH), and tmp_path under a worktree with a long name (fw.py's basetemp is named after
    # it, then popen-gwN and the test's own folder) passes that, so the daemon never listened.
    folder = tempfile.mkdtemp(prefix='q2bus')
    path = pathlib.Path(folder)/'bus'
    daemon = subprocess.Popen(['dbus-daemon', '--session', '--nofork', '--nopidfile', f'--address=unix:path={path}'],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if path.exists():
                break
            time.sleep(0.05)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(5)  # a wait that hangs fails instead
        fw.mock('fw_getuid', lambda *_: os.getuid())
        fw.mock('fw_socket', lambda *_: sock.fileno())
        fw.mock('fw_connect', lambda *_: sock.connect(str(path)) or 0)
        fw.mock('fw_send', lambda fd, data, n, *_: sock.send(C.string_at(data, n)))

        def recv(fd, out, n, *_):
            try:
                data = sock.recv(n)
            except OSError:
                return -1
            C.memmove(out, data, len(data))
            return len(data)
        fw.mock('fw_recv', recv)
        assert fw.fn('bt_vol_event_connect', C.c_int)() == 1
        wait, take = fw.fn('bt_vol_event_wait', C.c_int), fw.fn('bt_vol_event_take', C.c_int)
        assert wait() == 1 and take() == 0  # Hello's and AddMatch's replies, NameAcquired
        emitter = subprocess.run(['/usr/bin/python3', '-c', EMIT, f'unix:path={path}'], capture_output=True, timeout=20)
        assert emitter.returncode == 0, emitter.stderr
        deadline = time.monotonic() + 5
        while not take():
            assert time.monotonic() < deadline and wait() == 1
        sock.settimeout(0.5)
        assert wait() == 0 or take() == 0  # Device1's was filtered out by the daemon: one count only
        sock.close()
    finally:
        daemon.terminate()
        daemon.wait()
        shutil.rmtree(folder, ignore_errors=True)
