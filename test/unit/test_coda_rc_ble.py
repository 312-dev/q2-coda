"""coda-rc's own GATT server (patch/coda_rc_ble.c, docs/remote-protocol.md#bluetooth-le-primary): the daemon whole,
built for this machine as a library whose main runs on a thread, with --ble-sim. Its LE ATT channel is then a
SOCK_SEQPACKET listener, each packet one ATT PDU as the kernel's L2CAP socket gives them, and its management socket a
SOCK_SEQPACKET connection to a fake kernel here. The test is the phone's GATT client: discovery, the MTU, a write and
its notifications, a whole session with tools/coda_rc_client.py's Session over the characteristics, and the
advertisement the kernel is asked for (instance, flags, data), which stops while a phone is connected and is never
asked for while SyncLink is on."""
import ctypes
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time

import datetime
import re

import pytest

import coda_rc_client as client

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc.c', PATCH / 'coda_rc_core.c', PATCH / 'coda_rc_lib.c', PATCH / 'coda_rc_mdns.c',
           PATCH / 'coda_rc_ble.c', PATCH / 'monocypher/monocypher.c', PATCH / 'monocypher/monocypher-ed25519.c']
SQLITE = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0'
WAIT = time.sleep  # conftest makes time.sleep return at once during a test; the daemon's loop needs real time
CONNECT = socket.socket.connect  # conftest refuses connect: these are AF_UNIX sockets in a folder the test made

SERVICE = 'b67cefa7-194f-4988-8820-4974e36704c3'
CONTROL = '1666f4ad-18d6-4692-b723-b5ae83ce5ef3'
EVENT = '5645283b-18bb-419e-b338-f089cfc8dced'
INFO = 'c7f73d6b-93b1-46b5-8892-1ba59bdb7513'
H_NAME, H_CONTROL, H_EVENT, H_CCCD, H_INFO = 3, 8, 10, 11, 13
ADD_ADV, REMOVE_ADV, DISCONNECT = 0x3e, 0x3f, 0x14
LISTENING = re.compile(r'start: tcp (\d+) listening')  # the daemon's start line, with the port it got
STAMP = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ ')  # a log line's UTC stamp, ulog's


def le(uuid):
    return bytes.fromhex(uuid.replace('-', ''))[::-1]


def wait_for(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        WAIT(0.01)
    return False


@pytest.fixture(scope='module')
def daemon_so(tmp_path_factory):
    if not pathlib.Path(SQLITE).exists():
        pytest.skip('no libsqlite3.so.0 to link the daemon against')
    out = tmp_path_factory.mktemp('coda_rc_ble') / 'coda-rc.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Dmain=coda_main', '-Wall', '-Werror',
                    '-Wno-unused-parameter', '-I', str(PATCH), *map(str, SOURCES), SQLITE, '-o', str(out)], check=True)
    return out


class Kernel:
    """The management socket's far end: every command recorded and answered with status (0 succeeds)."""

    def __init__(self, sim):
        self.status, self.commands = 0, []
        self.conn: socket.socket | None = None
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.srv.bind(str(sim / 'mgmt'))
        self.srv.listen(1)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        conn, _ = self.srv.accept()
        self.conn = conn
        while True:
            try:
                m = conn.recv(1024)
            except OSError:
                return
            if not m:
                return
            op, index, n = struct.unpack('<HHH', m[:6])
            self.commands.append((op, m[6:6 + n]))
            try:
                conn.send(struct.pack('<HHHHB', 1, index, 3, op, self.status))
            except OSError:  # the daemon stopped: its last Remove Advertising has no one to read the answer
                return

    def event(self, ev, index=0):
        assert self.conn is not None
        self.conn.send(struct.pack('<HHH', ev, index, 0))

    def ops(self):
        return [op for op, _ in self.commands]

    def last_is(self, op, seconds=30.0):
        """True once the newest command the daemon sent is op. The daemon writes a command and then updates its
        own files, so a test that has seen the file change must still wait for this thread to record the command."""
        return wait_for(lambda: self.ops()[-1:] == [op], seconds)


class Q2:
    """A daemon on a thread of this process, with its config.ini, its fake kernel and its ATT listener."""

    def __init__(self, so, root, config='[Q2POD]\nBTNAME=Car Q2\n[SYSSET]\nSYNCLINK=0\n', status=0):
        self.root = root
        self.sim = pathlib.Path(tempfile.mkdtemp(prefix='ble'))  # short: a socket's path is at most 107 bytes
        (root / 'tmp').mkdir()
        (root / 'mnt/data').mkdir(parents=True)
        self.config(config)
        self.kernel = Kernel(self.sim)
        self.kernel.status = status
        copy = root / 'coda-rc.so'  # a copy of its own: the daemon's statics start at zero
        shutil.copy(so, copy)
        self.lib = ctypes.CDLL(str(copy))
        # --port 0: the kernel's pick, read back from the daemon's start line. A port chosen here and closed for the
        # daemon to bind could be given to another worktree's test in between (2026-11-22): nothing listened.
        args = [b'coda-rc', b'--root', str(root).encode(), b'--port', b'0', b'--iroh-port', b'0', b'--ble-sim',
                str(self.sim).encode()]
        self.argv = (ctypes.c_char_p * len(args))(*args)
        self.loop = threading.Thread(target=self.lib.coda_main, args=(len(args), self.argv), daemon=True)
        self.loop.start()
        # Its log's start line, written once the TCP port listens: the pid file comes before the device id's fsync and
        # the listen, and a test that connected on the pid file alone was refused when the disk was slow (a loaded
        # machine, 2026-11-22: test_unauthenticated_tcp_sessions_give_way_and_time_out, ECONNREFUSED)
        assert wait_for(lambda: any(LISTENING.match(line) for line in self.log()))
        self.port = next(int(m[1]) for m in map(LISTENING.match, self.log()) if m)

    def config(self, text):
        (self.root / 'mnt/data/config.ini').write_text(text)

    def att(self):
        return self.sim / 'att'

    def advertising(self):
        return (self.root / 'tmp/coda-rc.ble').exists()

    def stamped(self):
        p = self.root / 'tmp/coda-rc.log'
        return p.read_text().splitlines() if p.exists() else []

    def log(self):
        """The lines without their UTC stamp."""
        return [STAMP.sub('', line, count=1) for line in self.stamped()]

    def stop(self):
        self.lib.crc_host_quit()
        self.loop.join(5)
        shutil.rmtree(self.sim, ignore_errors=True)


@pytest.fixture
def q2(daemon_so, tmp_path):
    d = Q2(daemon_so, tmp_path)
    yield d
    d.stop()


class Central:
    """The phone's GATT client over one ATT channel. Notifications on event collect into a byte stream that
    recv hands out, and sendall writes the stream to control, so coda_rc_client.Session runs over it."""

    def __init__(self, path):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        CONNECT(self.s, str(path))
        self.s.settimeout(5)
        self.mtu, self.stream, self.notes = 23, b'', 0

    def settimeout(self, t):
        self.s.settimeout(t)

    def take(self):
        """One PDU; a notification's bytes go to the stream and None is returned for it."""
        p = self.s.recv(1024)
        if not p:
            raise ConnectionError('closed')
        if p[0] != 0x1b:
            return p
        assert struct.unpack('<H', p[1:3])[0] == H_EVENT and len(p) <= self.mtu
        self.stream += p[3:]
        self.notes += 1
        return None

    def pdu(self):
        """The next PDU that is not a notification; notifications on the way are taken into the stream."""
        while (p := self.take()) is None:
            pass
        return p

    def req(self, p):
        self.s.send(p)
        return self.pdu()

    def exchange(self, mtu):
        r = self.req(struct.pack('<BH', 0x02, mtu))
        assert r[0] == 0x03
        self.mtu = min(mtu, struct.unpack('<H', r[1:3])[0])
        return struct.unpack('<H', r[1:3])[0]

    def read(self, h):
        return self.req(struct.pack('<BH', 0x0a, h))

    def write(self, h, value, cmd=False):
        p = struct.pack('<BH', 0x52 if cmd else 0x12, h) + value
        if cmd:
            self.s.send(p)
            return None
        return self.req(p)

    def notify(self, on=True):
        assert self.write(H_CCCD, struct.pack('<H', int(on))) == b'\x13'

    def sendall(self, data):
        for i in range(0, len(data), self.mtu - 3):
            self.write(H_CONTROL, data[i:i + self.mtu - 3], cmd=True)

    def recv(self, n):
        while not self.stream:
            assert self.take() is None  # only notifications come unasked
        out, self.stream = self.stream[:n], self.stream[n:]
        return out

    def close(self):
        self.s.close()


def connect(q2):
    assert wait_for(q2.att().exists)
    return Central(q2.att())


def err(p):
    """An Error Response's (request, handle, code)."""
    assert p[0] == 0x01
    return p[1], struct.unpack('<H', p[2:4])[0], p[4]


def authed(q2, c, name='Pixel 10'):
    """A Session over c that has paired and authenticated: what a phone has before anything but the handshake."""
    s = client.Session(c, name)
    s.hello()
    point = s.pair_start()
    assert wait_for((q2.root / 'tmp/coda-rc.pair').exists)
    code = (q2.root / 'tmp/coda-rc.pair').read_text().split('\n')[0]
    s.auth(s.pair_finish(code, point)[1])
    return s


def test_discovery_finds_gap_and_the_coda_rc_service(q2):
    c = connect(q2)
    groups = []
    start = 1
    while True:
        r = c.req(struct.pack('<BHHH', 0x10, start, 0xffff, 0x2800))
        if r[0] == 0x01:
            assert err(r) == (0x10, start, 0x0a)  # Attribute Not Found: the end
            break
        size = r[1]
        for i in range(2, len(r), size):
            s, e = struct.unpack('<HH', r[i:i + 4])
            groups.append((s, e, r[i + 4:i + size]))
            start = e + 1
    assert groups == [(1, 5, struct.pack('<H', 0x1800)), (6, 13, le(SERVICE))]
    chars = []
    start = 6
    while True:
        r = c.req(struct.pack('<BHHH', 0x08, start, 13, 0x2803))
        if r[0] == 0x01:
            break
        size = r[1]
        for i in range(2, len(r), size):
            decl, props, value = struct.unpack('<HBH', r[i:i + 5])
            chars.append((props, value, r[i + 5:i + size]))
            start = decl + 1
    assert chars == [(0x0c, H_CONTROL, le(CONTROL)), (0x10, H_EVENT, le(EVENT)), (0x02, H_INFO, le(INFO))]
    r = c.req(struct.pack('<BHH', 0x04, 11, 11))  # the event characteristic's descriptors
    assert r == bytes([0x05, 1]) + struct.pack('<HH', H_CCCD, 0x2902)
    assert c.read(H_INFO) == b'\x0bcoda-rc 1'
    assert c.read(H_NAME) == b'\x0bCar Q2'  # the Bluetooth name, config.ini's BTNAME
    r = c.req(struct.pack('<BHHH', 0x06, 1, 0xffff, 0x2800) + le(SERVICE))  # Find By Type Value
    assert r == b'\x07' + struct.pack('<HH', 6, 13)


def test_wrong_requests_get_att_errors(q2):
    c = connect(q2)
    assert err(c.read(H_CONTROL)) == (0x0a, H_CONTROL, 0x02)  # Read Not Permitted
    assert err(c.read(99)) == (0x0a, 99, 0x01)  # Invalid Handle
    assert err(c.write(H_INFO, b'x')) == (0x12, H_INFO, 0x03)  # Write Not Permitted
    assert err(c.write(H_CCCD, b'\x01')) == (0x12, H_CCCD, 0x0d)  # a CCCD is two bytes
    assert err(c.req(struct.pack('<BHH', 0x0c, H_INFO, 10))) == (0x0c, H_INFO, 0x07)  # Invalid Offset
    assert c.req(struct.pack('<BHH', 0x0c, H_INFO, 8)) == b'\x0d1'  # Read Blob from 8
    assert err(c.req(b'\x16\x08\x00\x00\x00')) == (0x16, 0, 0x06)  # Prepare Write: Request Not Supported
    assert err(c.req(struct.pack('<BHHH', 0x10, 1, 0xffff, 0x2803))) == (0x10, 1, 0x10)  # Unsupported Group Type
    assert err(c.req(struct.pack('<BHHH', 0x08, 5, 2, 0x2803))) == (0x08, 5, 0x01)  # start after end
    assert err(c.req(b'\x0a')) == (0x0a, 0, 0x04)  # Invalid PDU
    c.s.send(b'\x7f\x00')  # an unknown command: ignored
    assert c.read(H_INFO) == b'\x0bcoda-rc 1'


def test_mtu_exchange_sizes_the_notifications(q2):
    c = connect(q2)
    assert c.exchange(247) == 247
    c.notify()
    s = authed(q2, c)
    payload = bytes(range(256)) * 20
    notes = c.notes
    assert s.call(client.T['PING'], payload, client.T['PONG']) == payload
    assert c.notes - notes == -(-(len(payload) + 6 + 16) // 244)  # every notification full but the last


def test_write_and_notify_round_trip_at_the_least_mtu(q2):
    """No MTU exchange: 20 bytes a notification. Write Requests (with a response) carry the stream as well as
    commands do."""
    c = connect(q2)
    c.notify()
    p = client.encode([(1, client.VERSION), (2, 'nRF Connect'), (3, bytes(16))])
    hello = struct.pack('>HBBH', len(p) + 4, client.T['HELLO'], 0, 1) + p  # as coda_rc_client.py frame hello prints it
    for i in range(0, len(hello), 20):
        assert c.write(H_CONTROL, hello[i:i + 20]) == b'\x13'
    s = client.Session(c)
    t, req, _ = s.frame(5)
    assert t == client.T['HELLO_RESP']


def test_no_notifications_before_the_cccd(q2):
    """The daemon sends nothing until the phone enables event's notifications; a Hello before then ends the
    session (its answer has nowhere to go), and the link with it."""
    c = connect(q2)
    s = client.Session(c)
    with pytest.raises((ConnectionError, OSError)):
        s.hello()
        c.pdu()


def test_a_whole_session_pairs_and_authenticates_over_ble(q2):
    c = connect(q2)
    c.exchange(247)
    c.notify()
    s = client.Session(c, 'Pixel 10')
    info = s.hello()
    assert not info['paired']
    point = s.pair_start()
    assert wait_for((q2.root / 'tmp/coda-rc.pair').exists)
    code = (q2.root / 'tmp/coda-rc.pair').read_text().split('\n')[0]
    peer, ltk = s.pair_finish(code, point)
    s.auth(ltk)
    assert s.call(client.T['PING'], b'sealed', client.T['PONG']) == b'sealed'
    assert wait_for(lambda: (q2.root / 'tmp/coda-rc.online').read_bytes() == peer + b'\x01')  # 1: over Bluetooth
    s.subscribe(position=False)


def test_pipelined_requests_are_answered_in_order_one_at_a_time(q2):
    """Four 3 KB Pings in one go at MTU 23, sealed (before Auth a Ping is echoed only up to CRC_PING_PLAIN): each
    answer whole and in order (the daemon takes the next once the one before has left, coda_rc_ble.c feed)."""
    c = connect(q2)
    c.notify()
    s = authed(q2, c)
    payloads = [bytes([i]) * 3000 for i in range(4)]
    reqs = [s.send(client.T['PING'], p) for p in payloads]
    for req, p in zip(reqs, payloads):
        t, r, got = s.frame(20)
        assert (t, r, got) == (client.T['PONG'], req, p)


def test_a_phone_that_writes_without_reading_answers_is_let_go(q2):
    """More than IN_CAP (two whole frames) waiting to be framed: the link is dropped rather than the bytes."""
    c = connect(q2)
    c.notify()
    s = authed(q2, c)
    with pytest.raises((ConnectionError, OSError)):
        for _ in range(4):
            s.send(client.T['PING'], bytes(8000))
        c.read(H_INFO)
    assert wait_for(q2.advertising)


def static_bytes(so, lib, symbol, offset, n):
    """n bytes of the loaded daemon's memory at a static's address plus offset: the static found by name in the
    library's symbol table, the load address from coda_main's."""
    syms = {}
    for line in subprocess.run(['llvm-nm', '--defined-only', str(so)], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        addr, _, name = line.split()
        syms[name] = int(addr, 16)
    main = ctypes.cast(lib.coda_main, ctypes.c_void_p).value
    assert main is not None
    base = main - syms['coda_main']
    return ctypes.string_at(base + syms[symbol] + offset, n)


def test_a_bad_length_with_more_than_a_frame_held_closes_without_overrunning(daemon_so, q2):
    """A length out of range at the head of more than CRC_MAX_FRAME held bytes (they pile up while an answer
    larger than the link's send buffer waits for the phone to take it): the link is closed and nothing is copied
    past the frame buffer feed hands the session (coda_rc_ble.c feed, its static f)."""
    max_frame = 6 + 8192 + 16
    c = connect(q2)
    c.exchange(247)
    c.notify()
    s = authed(q2, c)
    canary = static_bytes(daemon_so, q2.lib, 'feed.f', max_frame, 400)
    s.send(client.T['PING'], bytes(8000))  # 8022 bytes back, more than the send buffer holds: they wait in out
    c.s.recv(1, socket.MSG_PEEK)  # the answer has begun, so the Ping has left the held bytes
    held = b'\xff\xff' + b'\xa5' * (max_frame + 300 - 2)
    for i in range(0, len(held), 244):
        c.write(H_CONTROL, held[i:i + 244], cmd=True)
    c.s.send(struct.pack('<BH', 0x0a, H_INFO))  # a Read after them: its answer comes once all were taken in
    got_info = False
    with pytest.raises((ConnectionError, OSError)):
        while True:
            p = c.take()
            got_info = got_info or p == b'\x0bcoda-rc 1'
    assert got_info and len(c.stream) >= 8022
    assert static_bytes(daemon_so, q2.lib, 'feed.f', max_frame, 400) == canary
    assert wait_for(q2.advertising)


def tcp(q2):
    s = socket.socket()
    CONNECT(s, ('127.0.0.1', q2.port))
    s.settimeout(5)
    return s


def closed(sock):
    """True once the daemon has closed sock (its read gives end of file)."""
    sock.settimeout(0.05)
    def gone():
        try:
            return sock.recv(1) == b''
        except (TimeoutError, BlockingIOError):
            return False
        except OSError:
            return True
    return wait_for(gone, 5)


def test_unauthenticated_tcp_sessions_give_way_and_time_out(q2):
    """Four peers that say Hello and wait hold every TCP slot: a fifth connection still gets a session, the oldest
    making room, and a session that stays short of Auth outside a pairing is closed after HANDSHAKE_MS (15 s). The
    one pairing (a person typing the code) keeps AUTH_MS."""
    skew = ctypes.c_uint32.in_dll(q2.lib, 'crc_host_skew_ms')
    socks = []
    for i in range(4):
        sk = tcp(q2)
        client.Session(sk, f'idle {i}').hello()
        socks.append(sk)
    pairing = tcp(q2)
    p = client.Session(pairing, 'Pixel 10')
    p.hello()  # a session of its own: the oldest idle one made room for it
    assert closed(socks[0])
    p.pair_start()
    WAIT(1.2)  # a state is timed from the loop's once-a-second look that first sees it (timeouts)
    skew.value += 15001
    assert all(closed(sk) for sk in socks[1:])
    skew.value += 100000  # 115 s in: the pairing's session is still up
    assert p.call(client.T['PING'], b'up', client.T['PONG']) == b'up'
    skew.value += 40000
    assert closed(pairing)


def test_advertising_follows_the_phone(q2):
    k = q2.kernel
    assert wait_for(q2.advertising)
    op, p = k.commands[0]
    assert op == ADD_ADV
    instance, flags, duration, timeout, adv_n, scan_n = struct.unpack('<BIHHBB', p[:11])
    assert (instance, flags, duration, timeout) == (2, 3, 0, 0)  # connectable, discoverable; libSL's is 1
    assert p[11:11 + adv_n] == bytes([17, 0x07]) + le(SERVICE)
    assert p[11 + adv_n:11 + adv_n + scan_n] == bytes([7, 0x09]) + b'Car Q2'
    c = connect(q2)
    c.notify()
    assert wait_for(lambda: not q2.advertising())
    assert k.last_is(REMOVE_ADV)
    other = Central(q2.att())  # one phone at a time
    with pytest.raises((ConnectionError, OSError)):
        other.read(H_INFO)
    c.close()
    assert wait_for(q2.advertising)
    assert k.last_is(ADD_ADV)
    assert wait_for(lambda: 'ble: connected' in q2.log() and 'ble: disconnected' in q2.log())


@pytest.mark.parametrize('config, name', [
    ('[SYSSET]\nSYNCLINK=0\n', b'Vamp'),  # no name chosen: the default
    ('[Q2POD]\nBTNAME=Q2 Muse\n[SYSSET]\nSYNCLINK=0\n', b'Q2 Muse'),  # the name builds before Vamp gave it, chosen
])
def test_any_app_finds_it_by_the_service_whatever_its_name(daemon_so, tmp_path, config, name):
    """The phone app's scan filters on the coda-rc service UUID (BleLink.SERVICE, SERVICE here, the same in every
    release of the app) and lists the scan response's name without matching it, so an app from before the name Vamp
    finds a Q2 that says Vamp and the Vamp app finds one that says Q2 Muse."""
    q2 = Q2(daemon_so, tmp_path, config=config)
    try:
        assert wait_for(q2.advertising)
        op, p = q2.kernel.commands[0]
        adv_n, scan_n = p[9], p[10]
        assert op == ADD_ADV and p[11:11 + adv_n] == bytes([17, 0x07]) + le(SERVICE)
        assert p[11 + adv_n:11 + adv_n + scan_n] == bytes([1 + len(name), 0x09]) + name
    finally:
        q2.stop()


def test_a_session_the_daemon_ends_disconnects_the_phone(q2):
    """A malformed frame (len 2) closes the session; the link goes with it and advertising comes back."""
    c = connect(q2)
    c.notify()
    c.sendall(b'\x00\x02\x01\x00')
    with pytest.raises((ConnectionError, OSError)):
        c.pdu()  # nothing was asked: the next thing is the link's end
    assert wait_for(q2.advertising)


def test_stop_removes_the_advertisement(daemon_so, tmp_path):
    q2 = Q2(daemon_so, tmp_path)
    assert wait_for(q2.advertising)
    q2.stop()
    assert q2.kernel.ops()[-1] == REMOVE_ADV
    assert not q2.advertising()


def test_bluetooth_off_waits_for_hci0(daemon_so, tmp_path):
    """No hci0 (Invalid Index): logged once, nothing asked again until the kernel's Index Added, then 2 s on."""
    q2 = Q2(daemon_so, tmp_path, status=0x11)
    try:
        assert wait_for(lambda: q2.kernel.ops() == [ADD_ADV])
        WAIT(1.5)
        assert q2.kernel.ops() == [ADD_ADV] and not q2.advertising()
        q2.kernel.status = 0
        q2.kernel.event(4)  # Index Added
        WAIT(1)
        assert q2.kernel.ops() == [ADD_ADV]  # bluetoothd is given 2 s to power it
        assert wait_for(q2.advertising, 3)
        q2.kernel.event(5)  # Index Removed: Bluetooth off again
        assert wait_for(lambda: not q2.advertising())
        assert q2.log().count('ble: Bluetooth is off; waiting for it') == 2
    finally:
        q2.stop()


def test_refused_while_synclink_is_on(daemon_so, tmp_path):
    q2 = Q2(daemon_so, tmp_path, config='[SYSSET]\nSYNCLINK=1\n')
    try:
        assert wait_for(lambda: 'ble: off while SyncLink is on' in q2.log())  # logged as the daemon starts
        assert not q2.att().exists() and not q2.kernel.commands and not q2.advertising()
        q2.config('[SYSSET]\nSYNCLINK=0\n')
        assert wait_for(q2.advertising, 30)  # config.ini is read every 5 s; the deadline is for a loaded machine
        assert q2.att().exists()
        q2.config('[SYSSET]\nSYNCLINK=2\n')
        assert wait_for(lambda: not q2.advertising(), 30)
        assert q2.kernel.last_is(REMOVE_ADV)
    finally:
        q2.stop()


def test_no_ble_opens_nothing(daemon_so, tmp_path):
    root = tmp_path
    sim = pathlib.Path(tempfile.mkdtemp(prefix='ble'))
    (root / 'tmp').mkdir()
    shutil.copy(daemon_so, root / 'c.so')
    lib = ctypes.CDLL(str(root / 'c.so'))
    args = [b'coda-rc', b'--root', str(root).encode(), b'--port', b'0', b'--iroh-port', b'0', b'--no-ble', b'--ble-sim',
            str(sim).encode()]
    argv = (ctypes.c_char_p * len(args))(*args)
    t = threading.Thread(target=lib.coda_main, args=(len(args), argv), daemon=True)
    t.start()
    assert wait_for(lambda: (root / 'tmp/coda-rc.pid').exists())
    WAIT(0.3)
    assert not os.listdir(sim)
    lib.crc_host_quit()
    t.join(5)
    shutil.rmtree(sim)


@pytest.mark.parametrize('which', ['tcp', 'iroh'])
def test_a_taken_port_ends_the_daemon_and_says_so(daemon_so, tmp_path, which):
    """A port another process holds: the daemon does not run with no listener (the shim would take it for alive and
    never start it again, docs/internals.md#car-remote), logs why and leaves no pid file; the shim starts it again
    CR_RESTART_MS later."""
    root = tmp_path
    (root / 'tmp').mkdir()
    shutil.copy(daemon_so, root / 'c.so')
    lib = ctypes.CDLL(str(root / 'c.so'))
    holder = socket.socket()
    holder.bind(('127.0.0.1', 0))  # bound and not listening, as another program's
    taken = str(holder.getsockname()[1]).encode()
    ports = [b'--port', taken, b'--iroh-port', b'0'] if which == 'tcp' else [b'--port', b'0', b'--iroh-port', taken]
    args = [b'coda-rc', b'--root', str(root).encode(), *ports, b'--no-ble']
    argv = (ctypes.c_char_p * len(args))(*args)
    try:
        assert lib.coda_main(len(args), argv) == 1
    finally:
        holder.close()
    log = (root / 'tmp/coda-rc.log').read_text()
    assert 'start failed: tcp' in log and 'not running' in log and 'start: tcp' not in log
    assert not (root / 'tmp/coda-rc.pid').exists()


def test_each_log_line_starts_with_its_utc_time(daemon_so, tmp_path):
    """The drive of 2026-11-19: coda-rc.log's lines ("demo: play 0 1 9199 done", twice) had no time, so they could not be
    set beside the unified log's. Each line starts with its UTC time as the unified log writes it."""
    q2 = Q2(daemon_so, tmp_path, config='[SYSSET]\nSYNCLINK=1\n')
    try:
        assert wait_for(lambda: 'ble: off while SyncLink is on' in q2.log())
        now = datetime.datetime.now(datetime.timezone.utc)
        for line in q2.stamped():
            assert STAMP.match(line), f'no UTC stamp: {line!r}'
            at = datetime.datetime.strptime(line[:20], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
            assert abs((at - now).total_seconds()) < 60, f'{line!r} is not now ({now:%Y-%m-%dT%H:%M:%SZ})'
    finally:
        q2.stop()
