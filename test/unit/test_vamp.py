"""tools/vamp.py: its adb client against a fake adbd speaking adb's protocol as the Q2's 4.2.2 adbd does
(docs/adb.md#vamp). The fake checks what a real adbd would refuse or mishandle: a message over 4096 bytes, a
second WRTE on a stream before the OKAY for the first, a signature that is not PKCS#1 v1.5 over the token as a
SHA-1 digest. Its exec: answers from the test's handler (test/unit runs no program but the compilers); its sync:
works in a directory standing in for the Q2's root. Commands under a real sh are test/adb.py's, through the MIPS
adbd. The keys are checked against adb's own format (mincrypt's RSAPublicKey) and against openssl when
it is installed; mDNS against packets built here. The real MIPS adbd is test/adb.py's (its TCP checks)."""
import base64
import hashlib
import re
import io
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import q2remote  # noqa: E402
import vamp  # noqa: E402

_sleep = time.sleep  # conftest makes time.sleep a no-op during a test; the fake's threads wait for real
KEY = vamp.Key.generate(1024)  # adb's are 2048; 1024 keeps the tests quick and the format is the same
OTHER = vamp.Key.generate(1024)


def verify(line, token, sig):
    """mincrypt's RSA_verify, from the adbkey.pub line as adbd reads it."""
    blob = base64.b64decode(line.split()[0])
    words, n0inv = struct.unpack_from('<II', blob)
    n = int.from_bytes(blob[8:8 + 4 * words], 'little')
    e = struct.unpack_from('<I', blob, 8 + 8 * words)[0]
    k = 4 * words
    if len(sig) != k:
        return False
    em = pow(int.from_bytes(sig, 'big'), e, n).to_bytes(k, 'big')
    t = bytes.fromhex('3021300906052b0e03021a05000414') + token  # SHA-1's DigestInfo, as mincrypt's padding has it
    return em == b'\0\1' + b'\xff' * (k - len(t) - 3) + b'\0' + t


class FakeAdbd:
    """adbd 4.2.2's handshake and services, each connection a socketpair (test/unit opens no real socket):
    connect() is socket.create_connection for its port, and any other port refuses."""
    def __init__(self, root, trusted=(), maxdata=4096, banner=b'device::'):
        self.root, self.trusted, self.maxdata, self.banner = pathlib.Path(root), list(trusted), maxdata, banner
        self.handler = lambda cmd: b''  # exec:'s output for a command
        self.errors, self.offered, self.opened = [], [], []
        self.port, self.up = 5555, True

    def connect(self, address, timeout=None):
        if address != ('127.0.0.1', self.port) or not self.up:
            raise ConnectionRefusedError(111, 'Connection refused')
        ours, theirs = socket.socketpair()
        threading.Thread(target=self.session, args=(theirs,), daemon=True).start()
        return ours

    def recv(self, c, n):
        out = b''
        while len(out) < n:
            part = c.recv(n - len(out))
            if not part:
                raise EOFError
            out += part
        return out

    def read(self, c):
        cmd, a0, a1, n, check, magic = struct.unpack('<6I', self.recv(c, 24))
        if magic != cmd ^ 0xffffffff:
            self.errors.append('bad magic')
        if n > self.maxdata:
            self.errors.append(f'{vamp.NAMES.get(cmd)} with {n} bytes, over {self.maxdata}')
        data = self.recv(c, n) if n else b''
        if sum(data) & 0xffffffff != check:
            self.errors.append('bad checksum')
        return vamp.NAMES.get(cmd, '?'), a0, a1, data

    def send(self, c, name, a0, a1, data=b''):
        c.sendall(vamp.message(vamp._cmd(name), a0, a1, data))

    def session(self, c):
        lock = threading.Lock()
        streams = {}  # our id -> {'peer': id, 'in': socket or buffer handler, 'waiting': bool}
        next_id = [100]
        try:
            name, *_ = self.read(c)
            assert name == 'CNXN'
            token = os.urandom(20)
            self.send(c, 'AUTH', 1, 0, token)
            while True:
                name, a0, a1, data = self.read(c)
                if name == 'AUTH' and a0 == 2:
                    if any(verify(t, token, data) for t in self.trusted):
                        self.send(c, 'CNXN', vamp.VERSION, self.maxdata, self.banner)
                        break
                    token = os.urandom(20)
                    self.send(c, 'AUTH', 1, 0, token)
                elif name == 'AUTH' and a0 == 3:
                    self.offered.append(data)  # a TCP adbd asks no one: nothing comes back
            while True:
                name, a0, a1, data = self.read(c)
                if name == 'OPEN':
                    svc = data.rstrip(b'\0').decode()
                    self.opened.append(svc)
                    sid = next_id[0]
                    next_id[0] += 1
                    st = {'peer': a0, 'waiting': False, 'buf': b''}
                    streams[sid] = st
                    self.send(c, 'OKAY', sid, a0)
                    threading.Thread(target=self.service, args=(c, lock, sid, st, svc), daemon=True).start()
                elif name == 'WRTE':
                    st = streams.get(a1)
                    if st:
                        if st.get('unacked'):
                            self.errors.append('a WRTE before the OKAY for the last one')
                        st['buf'] += data
                        st['unacked'] = True
                        threading.Thread(target=self.ack, args=(c, lock, st, a1, a0), daemon=True).start()
                elif name == 'OKAY':
                    st = streams.get(a1)
                    if st:
                        st['waiting'] = False
                elif name == 'CLSE':
                    st = streams.pop(a1, None)
                    if st:
                        st['closed'] = True
        except (EOFError, OSError, AssertionError):
            pass
        finally:
            c.close()

    def ack(self, c, lock, st, local, remote):
        _sleep(.002)  # late enough that a client not waiting for it sends its next WRTE first
        st['unacked'] = False
        try:
            with lock:
                self.send(c, 'OKAY', local, remote)
        except OSError:
            pass

    def write(self, c, lock, sid, st, data):
        for i in range(0, len(data), self.maxdata):
            while st['waiting'] and not st.get('closed'):
                _sleep(.001)
            if st.get('closed'):
                return
            st['waiting'] = True
            with lock:
                self.send(c, 'WRTE', sid, st['peer'], data[i:i + self.maxdata])

    def take(self, st, n):
        while len(st['buf']) < n:
            if st.get('closed'):
                raise EOFError
            _sleep(.001)
        out, st['buf'] = st['buf'][:n], st['buf'][n:]
        return out

    def service(self, c, lock, sid, st, svc):
        try:
            self.serve_one(c, lock, sid, st, svc)
        except (EOFError, OSError):  # the client went first
            pass

    def serve_one(self, c, lock, sid, st, svc):
        try:
            if svc.startswith('exec:'):
                self.write(c, lock, sid, st, self.handler(svc[5:]))
            elif svc == 'sync:':
                self.sync(c, lock, sid, st)
                return
        except EOFError:
            return
        while st['waiting'] and not st.get('closed'):
            _sleep(.001)
        with lock:
            self.send(c, 'CLSE', sid, st['peer'])

    def path(self, p):
        return self.root / p.lstrip('/')

    def sync(self, c, lock, sid, st):
        while True:
            rid, n = struct.unpack('<4sI', self.take(st, 8))
            body = self.take(st, n)
            if rid == b'QUIT':
                return
            if rid == b'STAT':
                p = self.path(body.decode())
                s = p.stat() if p.exists() else None
                self.write(c, lock, sid, st, b'STAT' + struct.pack('<III', s.st_mode if s else 0, s.st_size if s else 0,
                                                                   int(s.st_mtime) if s else 0))
            elif rid == b'SEND':
                path, mode = body.decode().rsplit(',', 1)
                out = bytearray()
                while True:
                    did, dn = struct.unpack('<4sI', self.take(st, 8))
                    if did == b'DONE':
                        break
                    if dn > 65536:
                        self.errors.append('a DATA chunk over 64 KB')
                    out += self.take(st, dn)
                p = self.path(path)
                if p.parent.is_dir():
                    p.write_bytes(out)
                    os.chmod(p, int(mode) & 0o777)
                    self.write(c, lock, sid, st, b'OKAY\0\0\0\0')
                else:
                    msg = b'No such file or directory'
                    self.write(c, lock, sid, st, b'FAIL' + struct.pack('<I', len(msg)) + msg)
            elif rid == b'RECV':
                p = self.path(body.decode())
                if not p.is_file():
                    msg = b'No such file or directory'
                    self.write(c, lock, sid, st, b'FAIL' + struct.pack('<I', len(msg)) + msg)
                    continue
                data = p.read_bytes()
                for i in range(0, len(data), 65536):
                    part = data[i:i + 65536]
                    self.write(c, lock, sid, st, b'DATA' + struct.pack('<I', len(part)) + part)
                self.write(c, lock, sid, st, b'DONE\0\0\0\0')

    def close(self):
        self.up = False


@pytest.fixture
def adbd(tmp_path, monkeypatch):
    (tmp_path / 'q2').mkdir()
    fake = FakeAdbd(tmp_path / 'q2', [KEY.public_line()])
    monkeypatch.setattr(socket, 'create_connection', fake.connect)
    yield fake
    assert not fake.errors


@pytest.fixture
def dev(adbd, monkeypatch):
    monkeypatch.setattr(vamp, 'STATE', adbd.root.parent / 'target.json')
    return vamp.Device({'name': 'test', 'ip': '127.0.0.1', 'port': adbd.port, 'id': ''}, KEY)


# ---- keys ----

def test_public_blob_is_mincrypts_struct():
    blob = KEY.public_blob()
    words, n0inv = struct.unpack_from('<II', blob)
    n = int.from_bytes(blob[8:8 + 4 * words], 'little')
    rr = int.from_bytes(blob[8 + 4 * words:8 + 8 * words], 'little')
    assert words == 32 and n == KEY.n and len(blob) == 8 + 8 * words + 4
    assert n * n0inv % (1 << 32) == (1 << 32) - 1      # -1/n mod 2^32
    assert rr == pow(2, 2 * 32 * words, n)               # R^2 mod n
    assert struct.unpack_from('<I', blob, 8 + 8 * words)[0] == 65537


def test_signature_is_pkcs1_over_the_token_as_sha1():
    token = os.urandom(20)
    assert verify(KEY.public_line(), token, KEY.sign(token))
    assert not verify(OTHER.public_line(), token, KEY.sign(token))


def test_pem_round_trip_and_made_on_first_use(tmp_path, monkeypatch):
    again = vamp.Key.from_pem(KEY.pem())
    assert (again.n, again.e, again.d) == (KEY.n, KEY.e, KEY.d)
    monkeypatch.setattr(vamp.Key, 'generate', classmethod(lambda cls, bits=2048: KEY))
    path = tmp_path / '.android/adbkey'
    made = vamp.load_key(path)
    assert made is KEY and oct(path.stat().st_mode & 0o777) == '0o600'
    assert (tmp_path / '.android/adbkey.pub').read_text().split()[0] == KEY.public_line().split()[0]
    assert vamp.load_key(path).n == KEY.n  # read back, not made again


@pytest.mark.skipif(not shutil.which('openssl'), reason='no openssl')
def test_reads_an_openssl_key_as_adb_writes_them(tmp_path):
    pem = tmp_path / 'k.pem'
    subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:1024', '-out', str(pem)],
                   check=True, capture_output=True)
    key = vamp.Key.from_pem(pem.read_text())
    mod = subprocess.run(['openssl', 'rsa', '-in', str(pem), '-noout', '-modulus'], capture_output=True, text=True).stdout
    assert key.n == int(mod.split('=')[1], 16)
    der = subprocess.run(['openssl', 'pkcs8', '-topk8', '-nocrypt', '-in', str(pem), '-outform', 'DER'],
                         capture_output=True).stdout
    assert base64.b64decode(''.join(key.pem().splitlines()[1:-1])) == der  # our PKCS#8 is byte for byte openssl's


# ---- the protocol ----

def test_connects_with_a_trusted_key(adbd, dev):
    assert dev.connection().device == 'device::'
    assert adbd.offered == []


def test_untrusted_key_is_offered_then_refused_with_how_to_trust(adbd, monkeypatch):
    monkeypatch.setattr(vamp, 'CONNECT_S', 1)
    with pytest.raises(vamp.Unauthorized, match='over USB'):
        vamp.Connection(vamp.TcpTransport('127.0.0.1', adbd.port), OTHER, auth_wait=1)
    assert adbd.offered == [OTHER.public_line().encode() + b'\0']


def test_exec_out_and_a_long_output_in_4096_byte_writes(adbd, dev):
    adbd.handler = lambda cmd: {'echo hi': b'hi\n', 'big': b'x' * 100000}[cmd]
    assert dev.exec_out('echo hi') == b'hi\n'
    assert dev.exec_out('big') == b'x' * 100000  # 25 WRTEs, each OKAYed before the next


def test_q2remote_commands_go_over_vamp_with_stdin_as_a_file(adbd, dev, monkeypatch):
    (adbd.root / 'tmp').mkdir()
    seen = []

    def handler(cmd):
        seen.append(cmd)
        m = re.match(r'exec <(/tmp/vamp-in-[0-9a-f]{8}); rm -f \1\ncat$', cmd)
        return (adbd.path(m.group(1)).read_bytes() + b'done\n') if m else b''
    adbd.handler = handler
    monkeypatch.setattr(q2remote, 'TRANSPORT', dev)
    assert q2remote.shell('cat', b'from stdin\n') == b'from stdin\ndone\n'
    assert seen[-1].startswith('rm -f /tmp/vamp-in-')  # removed after, even if the command did not


def test_a_command_past_an_opens_room_goes_as_a_file(adbd, dev):
    (adbd.root / 'tmp').mkdir()
    long = 'echo ' + 'y' * 5000

    def handler(cmd):
        m = re.match(r'sh (/tmp/vamp-sh-[0-9a-f]{8}); r=\$\?; rm -f \1; exit \$r$', cmd)
        return adbd.path(m.group(1)).read_text().encode() if m else b''
    adbd.handler = handler
    assert dev.shell(long) == long.encode()
    assert all(len(s) < 4096 for s in adbd.opened)


def test_streamed_shell_keeps_the_exit_status_and_strips_its_line(adbd, dev):
    adbd.handler = lambda cmd: b'a\nb\n\0vamprc=3\n' if cmd.startswith('exec setsid sh -c') else b''
    out = io.BytesIO()
    assert vamp.run_streamed(dev, 'printf "a\\nb"; exit 3', out) == 3
    assert out.getvalue() == b'a\nb'


def test_streamed_shell_cut_short_says_so(adbd, dev):
    adbd.handler = lambda cmd: b'partial'
    out = io.BytesIO()
    with pytest.raises(ConnectionError, match='before the command finished'):
        vamp.run_streamed(dev, 'x', out)
    assert out.getvalue() == b'partial'


def test_push_and_pull_byte_for_byte(adbd, dev, tmp_path):
    (adbd.root / 'mnt/data').mkdir(parents=True)
    src = tmp_path / 'song.bin'
    src.write_bytes(os.urandom(300000))

    def handler(cmd):  # push's rename and size check, as the Q2's sh would do them
        m = re.match(r"mv -f (\S+) (\S+) && sync && wc -c <\S+$", cmd)
        if not m:
            return b''
        os.replace(adbd.path(m.group(1)), adbd.path(m.group(2)))
        return b'%d\n' % adbd.path(m.group(2)).stat().st_size
    adbd.handler = handler
    assert vamp.push(dev, src, '/mnt/data/') == '/mnt/data/song.bin'
    assert (adbd.root / 'mnt/data/song.bin').read_bytes() == src.read_bytes()
    assert not (adbd.root / 'mnt/data/song.bin.part').exists()
    vamp.pull(dev, '/mnt/data/song.bin', tmp_path / 'back.bin')
    assert (tmp_path / 'back.bin').read_bytes() == src.read_bytes()


def test_sync_failures_carry_the_q2s_reason(adbd, dev, tmp_path):
    with pytest.raises(vamp.AdbError, match='No such file'):
        vamp.pull(dev, '/nowhere', tmp_path / 'x')
    assert not (tmp_path / 'x').exists()
    with pytest.raises(vamp.AdbError, match='No such file'):
        dev.call(lambda c: c.push([b'data'], '/no/dir/f'))
    adbd.handler = lambda cmd: b'still\n'
    assert dev.exec_out('echo still') == b'still\n'  # the connection was opened again after the failure


def test_q2flash_adapter(adbd, dev, tmp_path, monkeypatch):
    monkeypatch.setattr(vamp, 'browse', lambda wait=2.0, addresses=None: [])  # gone from mDNS too
    (adbd.root / 'mnt').mkdir()
    f = tmp_path / 'u.tar'
    f.write_bytes(b'tar' * 1000)
    adb = vamp.FlashAdb(dev)
    assert adb.state() == 'device'
    assert adb.run('push', str(f), '/mnt/update.tar.part') == (0, '')
    assert (adbd.root / 'mnt/update.tar.part').read_bytes() == f.read_bytes()
    adbd.handler = lambda cmd: b'3000\n' if cmd == 'wc -c < /mnt/update.tar.part' else b''
    assert adb.sh('wc -c < /mnt/update.tar.part').strip() == '3000'
    assert adb.run('push', str(f), '/none/x')[0] == 1
    adbd.close()
    dev.drop()
    assert adb.state() == ''


def test_reconnects_by_name_when_the_address_changed(adbd, monkeypatch):
    monkeypatch.setattr(vamp, 'STATE', adbd.root.parent / 'target.json')
    old = {'name': 'Desk Q2', 'ip': '127.0.0.1', 'port': 1, 'id': 'abcd'}
    vamp.save(dict(old))
    monkeypatch.setattr(vamp, 'browse', lambda wait=2.0, addresses=None: [
        {'name': 'Desk Q2', 'ip': '127.0.0.1', 'port': adbd.port, 'id': 'abcd', 'host': 'q2muse-x.local'}])
    adbd.handler = lambda cmd: b'ok\n'
    d = vamp.Device(vamp.resolve(), KEY)
    assert d.exec_out('echo ok') == b'ok\n'
    assert vamp.saved()['port'] == adbd.port


# ---- targets and mDNS ----

@pytest.mark.parametrize('spec, want', [('10.0.0.42', ('10.0.0.42', 5555)), ('10.0.0.42:7', ('10.0.0.42', 7)),
                                        ('q2.lan:5556', ('q2.lan', 5556)), ('[fe80::1]:5555', ('fe80::1', 5555)),
                                        ('Vamp', None), ('Car Q2', None)])
def test_parse_target(spec, want):
    assert vamp.parse_target(spec) == want


def name(s):
    return b''.join(bytes([len(x)]) + x.encode() for x in s.split('.')) + b'\0'


def rec(n, t, data, ttl=120):
    return name(n) + struct.pack('>HHIH', t, 0x8001, ttl, len(data)) + data


def answer(inst='Vamp', ip=b'\x0a\0\0\x2a', port=5555, svc='_adb._tcp.local', ttl=120):
    full = f'{inst}.{svc}'
    rs = [rec(svc, 12, name(full), ttl), rec(full, 33, struct.pack('>3H', 0, 0, port) + name('q2muse-1a2b3c.local')),
          rec(full, 16, b'\x13id=1a2b3c4d5e6f7081\x03v=1'), rec('q2muse-1a2b3c.local', 1, ip)]
    return struct.pack('>6H', 0, 0x8400, 0, len(rs), 0, 0) + b''.join(rs)


def test_instances_from_an_announcement():
    assert vamp.instances(vamp.mdns_records(answer())) == [
        {'name': 'Vamp', 'ip': '10.0.0.42', 'port': 5555, 'id': '1a2b3c4d5e6f7081', 'host': 'q2muse-1a2b3c.local'}]


def test_instances_leave_out_other_services_goodbyes_and_queries():
    assert vamp.instances(vamp.mdns_records(answer(svc='_q2muse._tcp.local'))) == []
    assert vamp.instances(vamp.mdns_records(answer(ttl=0))) == []
    assert vamp.mdns_records(vamp.mdns_query(vamp.SERVICE)) == []


def test_compression_pointers_backwards_only():
    p = answer()
    i = p.index(name('_adb._tcp.local'), 12 + 1)  # the PTR's data names the service again: point it back
    looped = p[:12] + b'\xc0\x0c' + p[14:]
    with pytest.raises(ValueError):
        vamp.mdns_records(looped)
    assert i > 12


# ---- the edges: malformed keys, messages and replies ----

def test_key_files_it_cannot_use():
    with pytest.raises(ValueError, match='not a PEM'):
        vamp.Key.from_pem('nothing here')
    alg = bytes.fromhex('06072a8648ce3d020105')  # an EC key's algorithm, not rsaEncryption
    pk8 = vamp._der(0x30, vamp._der_int(0) + vamp._der(0x30, alg) + vamp._der(4, b''))
    pem = '-----BEGIN PRIVATE KEY-----\n' + base64.b64encode(pk8).decode() + '\n-----END PRIVATE KEY-----\n'
    with pytest.raises(ValueError, match='not an RSA key'):
        vamp.Key.from_pem(pem)
    short = vamp._der(0x30, vamp._der_int(0) + vamp._der_int(5))
    with pytest.raises(ValueError, match='short'):
        vamp.Key.from_pem('-----BEGIN RSA PRIVATE KEY-----\n' + base64.b64encode(short).decode() + '\n-----END')
    with pytest.raises(ValueError, match='truncated'):
        vamp._der_items(b'\x02\x05\x01')
    assert vamp._der_items(vamp._der(4, b'x' * 300)) == [(4, b'x' * 300)]  # a long form length


def test_keys_adb_cannot_take():
    with pytest.raises(ValueError, match='32-bit words'):
        vamp.Key(2 ** 1000 + 1, 65537, 1).public_blob()
    with pytest.raises(ValueError, match='too long'):
        vamp.Key(2 ** 255 + 1, 65537, 1).sign(os.urandom(20))
    assert not vamp._miller_rabin(561) and vamp._miller_rabin(2 ** 127 - 1)  # a Carmichael number, a Mersenne prime


@pytest.fixture
def peer(monkeypatch):
    """A TcpTransport whose other end the test writes as the Q2."""
    pair = []

    def connect(address, timeout=None):
        ours, theirs = socket.socketpair()
        pair.append(theirs)
        return ours
    monkeypatch.setattr(socket, 'create_connection', connect)
    t = vamp.TcpTransport('q2', 1)
    yield t, pair[0]
    pair[0].close()


def cnxn():
    return vamp.message(vamp.CNXN, vamp.VERSION, 4096, b'device::\0')


def test_messages_it_refuses(peer):
    t, q = peer
    q.sendall(cnxn())
    c = vamp.Connection(t, KEY)
    q.sendall(struct.pack('<6I', vamp.OKAY, 0, 0, 0, 0, 0))
    with pytest.raises(vamp.AdbError, match='malformed'):
        c.read(1)
    q.sendall(struct.pack('<6I', vamp.WRTE, 0, 0, 1, 99, vamp.WRTE ^ 0xffffffff) + b'x')
    with pytest.raises(vamp.AdbError, match='checksum'):
        c.read(1)


def test_a_message_cut_by_a_timeout_is_read_whole_next_time(peer):
    t, q = peer
    q.sendall(cnxn())
    c = vamp.Connection(t, KEY)
    whole = vamp.message(vamp.WRTE, 7, 1, b'hello')
    q.sendall(whole[:26])
    with pytest.raises(TimeoutError):
        c.read(.2)
    q.sendall(whole[26:])
    assert c.read(1) == (vamp.WRTE, 7, 1, b'hello')
    q.shutdown(socket.SHUT_WR)  # an end of file, not a reset
    with pytest.raises(ConnectionError, match='closed the connection'):
        c.read(1)


def test_a_q2_that_never_answers_the_handshake(peer, monkeypatch):
    monkeypatch.setattr(vamp, 'CONNECT_S', .3)
    with pytest.raises(TimeoutError, match='handshake'):
        vamp.Connection(peer[0], KEY)


def test_streams_after_the_q2_closes_them_or_starts_over(peer):
    t, q = peer
    q.sendall(cnxn())
    c = vamp.Connection(t, KEY)
    assert t.fileno() >= 0
    with pytest.raises(vamp.AdbError, match='over 4095'):
        c.open('exec:' + 'x' * 4100)
    q.sendall(vamp.message(vamp.CLSE, 0, 2))
    with pytest.raises(vamp.AdbError, match='refused exec:'):
        c.open('exec:nope')
    q.sendall(vamp.message(vamp.OKAY, 50, 3))
    s = c.open('exec:x')
    q.sendall(vamp.message(vamp.WRTE, 99, 77, b'stray'))  # for no stream of ours
    q.sendall(vamp.message(vamp.CNXN, vamp.VERSION, 4096, b'device::\0'))  # adbd started again
    assert s.read(1) == b'' and s.closed
    with pytest.raises(vamp.AdbError, match='closed the stream'):
        s.write(b'x')
    with pytest.raises(vamp.AdbError, match='closed the stream early'):
        s.read_exact(1)
    sent = q.recv(65536)
    assert vamp.message(vamp.CLSE, 77, 99) in sent  # the stray write was answered with a close


def sync_reply(peer, reply):
    """A Connection whose next sync: stream gets reply."""
    t, q = peer
    q.sendall(cnxn())
    c = vamp.Connection(t, KEY)
    # the OPEN's OKAY, the reply, then an OKAY for each write of ours: the request and the QUIT
    q.sendall(vamp.message(vamp.OKAY, 50, 1) + vamp.message(vamp.WRTE, 50, 1, reply) + vamp.message(vamp.OKAY, 50, 1) * 2)
    return c


def test_sync_replies_it_refuses(peer):
    with pytest.raises(vamp.AdbError, match='malformed STAT'):
        sync_reply(peer, b'FAIL' + b'\0' * 12).stat('/x')


def test_push_reply_it_refuses(peer):
    t, q = peer
    q.sendall(cnxn())
    c = vamp.Connection(t, KEY)
    # the OPEN's OKAY and the SEND's and DONE's, the reply, then the QUIT's OKAY
    q.sendall(vamp.message(vamp.OKAY, 50, 1) * 3 + vamp.message(vamp.WRTE, 50, 1, b'WHAT\0\0\0\0')
              + vamp.message(vamp.OKAY, 50, 1))
    with pytest.raises(vamp.AdbError, match='malformed reply to a push'):
        c.push([], '/x')


def test_pull_reply_it_refuses(peer):
    with pytest.raises(vamp.AdbError, match='malformed reply to a pull'):
        sync_reply(peer, b'DENT\0\0\0\0').pull('/x', io.BytesIO())


def test_a_sync_stream_that_cannot_quit_is_left(peer):
    t, q = peer
    q.sendall(cnxn())
    c = vamp.Connection(t, KEY)
    s = vamp.Stream(c, 9)
    s.remote = 1
    c.streams[9] = s
    q.shutdown(socket.SHUT_RDWR)
    c._sync_quit(s)  # the QUIT's write fails: nothing raised, the stream closed
    assert s.closed


# ---- mDNS's edges ----

def test_records_read_through_compression_pointers():
    p = bytearray(answer())
    svc = name('_adb._tcp.local')
    first = p.index(svc, 12)
    # the A record's name points back at the SRV's target, as a responder that compresses would write it
    host = name('q2muse-1a2b3c.local')
    last = p.rindex(host)
    p[last:last + len(host)] = b'\xc0' + bytes([p.index(host)])
    assert vamp.instances(vamp.mdns_records(bytes(p)))[0]['ip'] == '10.0.0.42'
    assert first == 12


def test_records_it_refuses_or_leaves_out():
    p = answer()
    with pytest.raises(ValueError, match='past the packet'):
        vamp.mdns_records(p[:20])
    with pytest.raises(ValueError, match='cut pointer'):
        vamp._name(b'\xc0', 0)
    with pytest.raises(ValueError, match='cut record'):
        vamp.mdns_records(p[:12 + len(name('_adb._tcp.local')) + 4])
    q = struct.pack('>6H', 0, 0x8400, 1, 1, 0, 0) + name('x.local') + b'\0\x1c\0\x01' + rec('x.local', 28, b'\0' * 16)
    assert vamp.mdns_records(q) == [('x.local', 28, b'\0' * 16, 120)]  # a question first; an AAAA kept as bytes
    no_srv = struct.pack('>6H', 0, 0x8400, 0, 1, 0, 0) + rec('_adb._tcp.local', 12, name('Lone._adb._tcp.local'))
    assert vamp.instances(vamp.mdns_records(no_srv)) == []
    full = vamp.mdns_records(answer())
    assert vamp.instances([r for r in full if r[1] != 1]) == []  # no address for its host


# ---- targets ----

def test_targets(monkeypatch, tmp_path):
    monkeypatch.setattr(vamp, 'STATE', tmp_path / 'target.json')
    with pytest.raises(ConnectionError, match='no Q2 chosen'):
        vamp.resolve()
    (tmp_path / 'target.json').write_text('{not json')
    assert vamp.saved() is None
    assert vamp.resolve('10.0.0.9:7')['port'] == 7
    q = {'name': 'Desk', 'ip': '10.0.0.5', 'port': 5555, 'id': 'aa', 'host': 'h'}
    monkeypatch.setattr(vamp, 'browse', lambda wait=2.0: [q, dict(q, id='bb', ip='10.0.0.6')])
    with pytest.raises(ConnectionError, match='2 Q2s are named'):
        vamp.resolve('Desk')
    assert vamp.resolve('bb')['ip'] == '10.0.0.6'
    with pytest.raises(ConnectionError, match="no Q2 named 'Kitchen'"):
        vamp.resolve('Kitchen')


def test_an_address_that_does_not_answer_is_not_looked_up(adbd, monkeypatch):
    monkeypatch.setattr(vamp, 'browse', lambda *a, **k: pytest.fail('looked up an address'))
    with pytest.raises(ConnectionError, match='cannot reach'):
        vamp.Device({'name': '127.0.0.1:1', 'ip': '127.0.0.1', 'port': 1, 'id': ''}, KEY).connection()


def test_drop_survives_a_close_that_fails(dev):
    class Bad:
        def close(self):
            raise OSError('gone')
    dev.conn = Bad()
    dev.drop()
    assert dev.conn is None


def test_a_refused_stdin_file_is_a_connection_error_and_cleanup_tolerates_failure(adbd, dev, monkeypatch):
    with pytest.raises(ConnectionError, match='No such file'):  # no /tmp on this fake Q2
        dev.shell('cat', b'x')
    calls = []

    def exec_out(cmd, timeout=30, sink=None, done=None):
        calls.append(cmd)
        if cmd.startswith('rm -f'):
            raise OSError('gone')
        return b'ok'
    (adbd.root / 'tmp').mkdir()
    dev.connection()
    monkeypatch.setattr(dev, 'exec_out', exec_out)
    assert dev.shell('cat', b'x') == b'ok' and calls[-1].startswith('rm -f')


def test_streamed_shell_long_output_and_ctrl_c(adbd, dev, monkeypatch):
    adbd.handler = lambda cmd: b'z' * 50000 + b'\n\0vamprc=0\n'
    out = io.BytesIO()
    assert vamp.run_streamed(dev, 'x', out) == 0 and out.getvalue() == b'z' * 50000
    killed = []

    def shell(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(dev, 'shell', shell)
    monkeypatch.setattr(dev, 'exec_out', lambda cmd, timeout=30: killed.append(cmd))
    with pytest.raises(KeyboardInterrupt):
        vamp.run_streamed(dev, 'sleep 100', io.BytesIO())
    assert killed and killed[0].startswith('p=$(cat /tmp/vamp-') and 'kill -- -$p' in killed[0]


def test_push_corners(adbd, dev, tmp_path, monkeypatch):
    (adbd.root / 'mnt/mmc').mkdir(parents=True)
    src = tmp_path / 'a.flac'
    src.write_bytes(b'f' * 5000)
    checked = []
    monkeypatch.setattr(q2remote, 'card_mounted', lambda: checked.append(1))
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: True)
    adbd.handler = lambda cmd: b'12\n' if cmd.startswith('mv -f') else b''
    with pytest.raises(vamp.AdbError, match='holds 12, not 5000 bytes'):
        vamp.push(dev, src, '/mnt/mmc/a.flac')
    assert checked == [1]  # the card checked before anything was sent
    (adbd.root / 'mnt/mmc/b.flac').write_bytes(b'pulled')
    (tmp_path / 'into').mkdir()
    vamp.pull(dev, '/mnt/mmc/b.flac', tmp_path / 'into')
    assert (tmp_path / 'into/b.flac').read_bytes() == b'pulled'
    with pytest.raises(ValueError, match='asked vamp for adb shell'):
        vamp.FlashAdb(dev).run('shell', 'x')


def test_handshake_skips_what_it_does_not_ask_for(peer):
    t, q = peer
    tok = vamp.message(vamp.AUTH, 1, 0, os.urandom(20))
    # an OKAY and an AUTH that is no token, then three tokens (signed, offered, then left), then in
    q.sendall(vamp.message(vamp.OKAY, 1, 1) + vamp.message(vamp.AUTH, 2, 0, b'x') + tok * 3 + cnxn())
    c = vamp.Connection(t, KEY)
    assert c.device == 'device::'
    q.sendall(vamp.message(vamp.SYNC, 1, 0))  # nothing to hand to a stream
    c.pump(1)
    s = vamp.Stream(c, 3)
    s.closed = True
    c._sync_quit(s)  # already closed: nothing sent


def test_reconnect_leaves_another_saved_q2_alone(adbd, monkeypatch):
    monkeypatch.setattr(vamp, 'STATE', adbd.root.parent / 'target.json')
    vamp.save({'name': 'Other', 'ip': '10.0.0.1', 'port': 5555, 'id': 'ffff'})
    monkeypatch.setattr(vamp, 'browse', lambda wait=2.0, addresses=None: [
        {'name': 'Desk', 'ip': '127.0.0.1', 'port': adbd.port, 'id': 'abcd', 'host': 'h'}])
    adbd.handler = lambda cmd: b'ok'
    d = vamp.Device({'name': 'Desk', 'ip': '127.0.0.1', 'port': 1, 'id': 'abcd'}, KEY)
    assert d.exec_out('x') == b'ok' and vamp.saved()['id'] == 'ffff'


def test_ctrl_c_when_the_kill_cannot_reach_the_q2(dev, monkeypatch):
    monkeypatch.setattr(dev, 'shell', lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt))

    def gone(cmd, timeout=30):
        raise OSError('gone')
    monkeypatch.setattr(dev, 'exec_out', gone)
    with pytest.raises(KeyboardInterrupt):
        vamp.run_streamed(dev, 'x', io.BytesIO())


def test_push_on_a_terminal_shows_its_progress(adbd, dev, tmp_path, monkeypatch, capsys):
    (adbd.root / 'mnt').mkdir()
    src = tmp_path / 'a.bin'
    src.write_bytes(b'q' * 2000)
    monkeypatch.setattr(sys.stderr, 'isatty', lambda: True)
    adbd.handler = lambda cmd: b'2000\n' if cmd.startswith('mv -f') else b''
    assert vamp.push(dev, src, '/mnt/a.bin') == '/mnt/a.bin'
    assert '2000 bytes in' in capsys.readouterr().out


# ---- ui-push ----

MIPS = b'\x7fELF\x01\x01\x01' + b'\0' * 11 + b'\x08\x00'  # e_machine 8 at offset 18


class FakeUi:
    """The Q2's side of ui-push: /usr/bin/q2ui, a running q2ui and how it restarts on SIGHUP ('path': from the
    file now at /usr/bin/q2ui; 'self': from its own file, as 817b5fc2's /proc/self/exe; 'gone': it ends)."""
    def __init__(self, adbd, restart='path', mark=True, has_ui=True, running=True, mount_ok=True):
        self.adbd, self.restart, self.mark, self.has_ui, self.mount_ok = adbd, restart, mark, has_ui, mount_ok
        self.old = hashlib.sha256(b'old q2ui').hexdigest()
        self.at_path, self.exe, self.pid = self.old, (self.old if running else None), (7 if running else None)
        self.seen = []
        (adbd.root / 'mnt/data').mkdir(parents=True, exist_ok=True)
        adbd.handler = self

    def __call__(self, cmd):
        self.seen.append(cmd)
        if cmd == "[ -e /usr/bin/q2ui ] && echo yes":
            return b'yes\n' if self.has_ui else b''
        if cmd == 'pidof q2ui':
            return b'%d\n' % self.pid if self.pid else b''
        if cmd.startswith('grep -c /proc/self/exe '):
            return b'1\n' if self.mark else b'0\n'
        if m := re.match(r"mv -f (\S+) (\S+) && sync && wc -c <\S+$", cmd):
            os.replace(self.adbd.path(m.group(1)), self.adbd.path(m.group(2)))
            return b'%d\n' % self.adbd.path(m.group(2)).stat().st_size
        if cmd.startswith('chmod 755 /mnt/data/q2ui.vamp; umount /usr/bin/q2ui 2>/dev/null; mount --bind'):
            if not self.mount_ok:
                return b'mount: permission denied\n'
            self.at_path = hashlib.sha256(self.adbd.path('/mnt/data/q2ui.vamp').read_bytes()).hexdigest()
            return f'{self.at_path}  /usr/bin/q2ui\n'.encode()
        if cmd == f'kill -HUP {self.pid}':
            self.exe = {'path': self.at_path, 'self': self.exe, 'gone': None}[self.restart]
            self.pid = None if self.restart == 'gone' else self.pid
            return b''
        if cmd.startswith('p=$(pidof q2ui) && sha256sum /proc/${p%% *}/exe'):
            return f'{self.exe}  /proc/{self.pid}/exe\n{self.pid}\n'.encode() if self.pid else b'\n'
        return b''


@pytest.fixture
def q2ui(tmp_path):
    f = tmp_path / 'q2ui'
    f.write_bytes(MIPS + b'new q2ui')
    return f


def test_ui_push_runs_the_new_q2ui(adbd, dev, q2ui, capsys):
    ui = FakeUi(adbd)
    vamp.ui_push(dev, q2ui)
    assert ui.exe == hashlib.sha256(q2ui.read_bytes()).hexdigest()
    assert 'kill -HUP 7' in ui.seen and 'running (pid 7)' in capsys.readouterr().out


def test_ui_push_says_when_q2ui_restarted_its_old_file(adbd, dev, q2ui):
    FakeUi(adbd, restart='self')
    with pytest.raises(vamp.AdbError, match='still runs .* /proc/self/exe'):
        vamp.ui_push(dev, q2ui, wait=.2)


def test_ui_push_says_when_no_q2ui_came_back(adbd, dev, q2ui):
    FakeUi(adbd, restart='gone')
    with pytest.raises(vamp.AdbError, match='no q2ui running'):
        vamp.ui_push(dev, q2ui, wait=.2)


def test_ui_push_stops_on_an_older_q2ui_before_pushing(adbd, dev, q2ui):
    ui = FakeUi(adbd, mark=False)
    with pytest.raises(vamp.AdbError, match='predates feat/render-engine 817b5fc2'):
        vamp.ui_push(dev, q2ui)
    assert not (adbd.root / 'mnt/data/q2ui.vamp').exists() and not any('mount' in c for c in ui.seen)


def test_ui_push_refusals(adbd, dev, q2ui, tmp_path, capsys):
    x86 = tmp_path / 'x86'
    x86.write_bytes(b'\x7fELF' + b'\0' * 14 + b'\x3e\x00')
    with pytest.raises(ValueError, match='not a MIPS program'):
        vamp.ui_push(dev, x86)
    FakeUi(adbd, has_ui=False)
    with pytest.raises(vamp.AdbError, match='no /usr/bin/q2ui'):
        vamp.ui_push(dev, q2ui)
    FakeUi(adbd, mount_ok=False)
    with pytest.raises(vamp.AdbError, match='bind mount over /usr/bin/q2ui failed'):
        vamp.ui_push(dev, q2ui)
    ui = FakeUi(adbd, running=False)
    vamp.ui_push(dev, q2ui)
    assert 'none is running' in capsys.readouterr().out and not any('kill' in c for c in ui.seen)


# ---- connect: -t, the remembered address, retries ----

def test_a_q2_mdns_cannot_find_is_retried_at_its_address(adbd, monkeypatch):
    def no_network(*a, **k):
        raise ConnectionError('no network to look on')
    monkeypatch.setattr(vamp, 'browse', no_network)
    adbd.handler = lambda cmd: b'ok'
    adbd.up = False
    tries = []

    def back_on(*a):  # the Q2's Wi-Fi comes back between the first try and the retry
        tries.append(a)
        adbd.up = True
    monkeypatch.setattr(vamp.time, 'sleep', back_on)
    d = vamp.Device({'name': 'Desk', 'ip': '127.0.0.1', 'port': adbd.port, 'id': 'abcd'}, KEY)
    assert d.exec_out('x') == b'ok' and len(tries) == 1
    adbd.up = False
    d.drop()
    monkeypatch.setattr(vamp.time, 'sleep', lambda s: None)
    with pytest.raises(ConnectionError, match='cannot reach'):
        d.connection()


def test_connect_takes_t_and_falls_back_to_the_last_q2(adbd, monkeypatch, capsys):
    monkeypatch.setattr(vamp, 'STATE', adbd.root.parent / 'target.json')
    monkeypatch.setattr(vamp, 'load_key', lambda: KEY)
    monkeypatch.setattr(vamp, 'browse', lambda *a, **k: pytest.fail('looked on mDNS'))
    adbd.handler = lambda cmd: b'1 version V0JZi -\n'  # NONCE WORD INSTALLED UPDATE
    assert vamp.main(['-t', f'127.0.0.1:{adbd.port}', 'connect']) == 0
    assert vamp.saved()['ip'] == '127.0.0.1' and 'running V0JZi' in capsys.readouterr().out
    assert vamp.main(['connect']) == 0  # no name, no -t: the one it remembered, mDNS not asked


# ---- USB ----

import ctypes  # noqa: E402


def config(ifaces):
    """A libusb_config_descriptor holding ifaces: [(number, (class, sub, proto), [(address, attributes)])]."""
    keep = []
    arr = (vamp.Interface * len(ifaces))()
    for i, (num, cls, eps) in enumerate(ifaces):
        e = (vamp.EndpointDesc * max(len(eps), 1))()
        for j, (addr, attr) in enumerate(eps):
            e[j].bEndpointAddress, e[j].bmAttributes = addr, attr
        d = (vamp.InterfaceDesc * 1)()
        d[0].bInterfaceNumber, d[0].bNumEndpoints = num, len(eps)
        d[0].bInterfaceClass, d[0].bInterfaceSubClass, d[0].bInterfaceProtocol = cls
        d[0].endpoint = ctypes.cast(e, ctypes.POINTER(vamp.EndpointDesc))
        arr[i].altsetting, arr[i].num_altsetting = ctypes.cast(d, ctypes.POINTER(vamp.InterfaceDesc)), 1
        keep += [e, d]
    c = vamp.ConfigDesc()
    c.bNumInterfaces, c.interface = len(ifaces), ctypes.cast(arr, ctypes.POINTER(vamp.Interface))
    c._keep = keep + [arr]
    return c


ADB_IFACE = (1, (0xff, 0x42, 0x01), [(0x81, 2), (0x02, 2)])


def put_pointer(arg, address):
    """What libusb does with an out-parameter pointer: the pointer arg refers to now holds address."""
    ctypes.c_void_p.from_address(ctypes.addressof(arg._obj)).value = address


class FakeLibusb:
    """libusb 1.0's calls as usb_devices and UsbEndpoints make them, for devices given as dicts (config, serial,
    open, claim); a claimed device's bulk transfers go to the fake adbd, and each OUT transfer's size is kept."""
    def __init__(self, adbd, devices, init=0):
        self.adbd, self.devs, self.init, self.out_sizes, self.closed = adbd, devices, init, [], []
        self.arr = (ctypes.c_void_p * (len(devices) + 1))(*range(1, len(devices) + 1))

    def libusb_init(self, ctx):
        return self.init

    def libusb_get_device_list(self, ctx, devs):
        put_pointer(devs, ctypes.addressof(self.arr))
        return len(self.devs)

    def libusb_free_device_list(self, devs, unref):
        pass

    def dev(self, d):
        return self.devs[(d.value if hasattr(d, 'value') else d) - 1]

    def libusb_get_active_config_descriptor(self, d, cfg):
        dev = self.dev(d)
        if dev.get('config') is None:
            return -5
        put_pointer(cfg, ctypes.addressof(dev['config']))
        return 0

    def libusb_free_config_descriptor(self, cfg):
        pass

    def libusb_get_device_descriptor(self, d, desc):
        desc._obj.iSerialNumber = 3 if self.dev(d).get('serial') else 0
        desc._obj.idVendor, desc._obj.idProduct = 0x1d6b, 0x0105

    def libusb_open(self, d, h):
        dev = self.dev(d)
        if not dev.get('open', True):
            return -3
        put_pointer(h, 100 + self.devs.index(dev))
        return 0

    def handle(self, h):
        return self.devs[(h.value if hasattr(h, 'value') else h) - 100]

    def libusb_close(self, h):
        self.closed.append(h)

    def libusb_get_string_descriptor_ascii(self, h, idx, buf, n):
        s = self.handle(h)['serial']
        ctypes.memmove(buf, s, len(s))
        return len(s)

    def libusb_set_auto_detach_kernel_driver(self, h, on):
        return 0

    def libusb_claim_interface(self, h, iface):
        dev = self.handle(h)
        if not dev.get('claim', True):
            return vamp.LIBUSB_ERROR_BUSY
        dev['sock'] = self.adbd.connect(('127.0.0.1', self.adbd.port))
        return 0

    def libusb_release_interface(self, h, iface):
        self.handle(h)['sock'].close()

    def libusb_bulk_transfer(self, h, ep, buf, n, done, timeout_ms):
        dev = self.handle(h)
        if dev.get('fail'):
            return -4
        s = dev['sock']
        if not ep & 0x80:
            data = bytes(buf[:n])[:dev.get('short', n)]
            self.out_sizes.append(len(data))
            s.sendall(data)
            done._obj.value = len(data)
            return 0
        s.settimeout(timeout_ms / 1000)
        got = b''
        try:
            while len(got) < n:
                part = s.recv(n - len(got))
                if not part:
                    break
                got += part
        except socket.timeout:
            done._obj.value = len(got)
            return vamp.LIBUSB_ERROR_TIMEOUT
        ctypes.memmove(buf, got, len(got))
        done._obj.value = len(got)
        return 0


def test_adb_interfaces_picks_ff_42_01_with_bulk_in_and_out():
    c = config([(0, (8, 6, 0x50), [(0x81, 2), (0x02, 2)]),        # a drive
                (2, (0xff, 0x42, 0x01), [(0x83, 3), (0x04, 2)]),  # ADB, but its IN is interrupt
                ADB_IFACE])
    assert vamp.adb_interfaces(c) == [(1, 0x81, 0x02)]


def test_usb_lists_adb_devices_and_connects_through_one(adbd, dev, capsys, monkeypatch):
    lib = FakeLibusb(adbd, [{'config': None}, {'config': config([(0, (8, 6, 0x50), [])])},
                            {'config': config([ADB_IFACE]), 'open': False},
                            {'config': config([ADB_IFACE]), 'serial': b'Q2CODA'},
                            {'config': config([ADB_IFACE])}])
    found = vamp.usb_devices(lib)
    assert [s for s, _ in found] == ['Q2CODA', '1d6b:0105']  # a device that cannot be opened is left out
    adbd.handler = lambda cmd: b'x' * 9000
    d = vamp.Device({'name': 'usb:Q2CODA', 'ip': 'usb', 'port': 'Q2CODA', 'id': ''}, KEY)
    real = vamp.Device.usb_transport
    monkeypatch.setattr(vamp.Device, 'usb_transport', lambda self: real(self, lambda: found))
    assert d.exec_out('big') == b'x' * 9000
    sizes = lib.out_sizes
    assert sizes[0] == 24 and all(s <= 4096 for s in sizes)  # the CNXN's header alone, then its data
    assert sizes[:2] == [24, len(b'host::vamp\0')]
    d.drop()


def test_usb_picks_by_serial_and_says_what_it_found():
    d = vamp.Device({'name': 'usb', 'ip': 'usb', 'port': '', 'id': ''}, KEY)
    with pytest.raises(ConnectionError, match=r'0 ADB devices on USB match \(none\)'):
        d.usb_transport(lambda: [])
    two = [('A', lambda: 'a'), ('B', lambda: 'b')]
    with pytest.raises(ConnectionError, match='2 ADB devices .*A, B'):
        d.usb_transport(lambda: two)
    d.target['port'] = 'B'
    assert d.usb_transport(lambda: two) == 'b'


def test_usb_open_and_transfer_failures(adbd):
    lib = FakeLibusb(adbd, [{'config': config([ADB_IFACE]), 'serial': b'S', 'claim': False}])
    (_, opener), = vamp.usb_devices(lib)
    with pytest.raises(ConnectionError, match='busy'):
        opener()
    lib.devs[0]['claim'] = True
    lib.devs[0]['open'] = False
    with pytest.raises(ConnectionError, match='could not be opened'):
        opener()
    lib.devs[0]['open'] = True
    t = opener()
    with pytest.raises(socket.timeout):
        t.recv(24, .1)  # the fake adbd says nothing until it hears a CNXN
    lib.devs[0]['short'] = 10
    t.ep.write(cnxn())  # a transfer cut short is finished with another: 33 bytes as 10, 10, 10, 3
    assert lib.out_sizes[-4:] == [10, 10, 10, 3]
    lib.devs[0]['fail'] = True
    with pytest.raises(ConnectionError, match='unplugged'):
        t.send(b'a' * 30)
    t.ep.read = lambda n, timeout: b''
    with pytest.raises(socket.timeout):
        t.recv(4, 1)
    lib.devs[0]['fail'] = False
    t.close()
    with pytest.raises(ConnectionError, match='libusb would not start'):
        vamp.usb_devices(FakeLibusb(adbd, [], init=-1))
    class Broken(FakeLibusb):
        def libusb_get_device_list(self, ctx, devs):
            return -1  # LIBUSB_ERROR_IO: nothing listed, nothing to free

        def libusb_free_device_list(self, devs, unref):
            pytest.fail('freed a list libusb never gave')
    assert vamp.usb_devices(Broken(adbd, [])) == []


def test_first_pairing_over_usb_says_to_press_allow(adbd, capsys, monkeypatch):
    adbd.trusted = []  # an unknown key: offered, and the fake (like a TCP adbd) never answers
    monkeypatch.setattr(vamp, 'AUTH_WAIT_S', .2)
    monkeypatch.setattr(vamp.Device, 'usb_transport', lambda self: vamp.TcpTransport('127.0.0.1', adbd.port))
    with pytest.raises(vamp.Unauthorized):
        vamp.Device({'name': 'usb', 'ip': 'usb', 'port': '', 'id': ''}, OTHER).connection()
    assert 'press Allow' in capsys.readouterr().err


def test_connect_over_usb_remembers_the_wifi_address(adbd, dev, monkeypatch, capsys):
    monkeypatch.setattr(vamp, 'STATE', adbd.root.parent / 'target.json')
    def handler(cmd):
        if cmd.startswith('ifconfig wlan0'):
            return (b'wlan0  Link encap:Ethernet\n  inet addr:192.168.1.23  Bcast:192.168.1.255\nvamp-id\n'
                    b' 1a 2b 3c 4d 5e 6f 70 81\n')
        return b'1 version V0JZi -\n'
    adbd.handler = handler
    assert vamp.over_wifi(dev) == {'name': '192.168.1.23', 'ip': '192.168.1.23', 'port': 5555, 'id': '1a2b3c4d5e6f7081'}
    adbd.handler = lambda cmd: b'wlan0 no address\nvamp-id\n' if cmd.startswith('ifconfig') else b''
    assert vamp.over_wifi(dev) is None and 'no Wi-Fi address' in capsys.readouterr().err
    adbd.handler = handler
    monkeypatch.setattr(vamp, 'load_key', lambda: KEY)
    monkeypatch.setattr(vamp.Device, 'usb_transport', lambda self: vamp.TcpTransport('127.0.0.1', adbd.port))
    assert vamp.main(['-t', 'usb', 'connect']) == 0
    assert vamp.saved()['ip'] == '192.168.1.23' and vamp.saved()['id'] == '1a2b3c4d5e6f7081'
    adbd.handler = lambda cmd: b'' if cmd.startswith('ifconfig') else b'1 version V0JZi -\n'
    assert vamp.main(['-t', 'usb:Q2CODA', 'connect']) == 0
    assert vamp.saved()['ip'] == 'usb' and vamp.saved()['port'] == 'Q2CODA'


def test_find_lists_usb_and_says_when_it_cannot_look(monkeypatch, capsys):
    monkeypatch.setattr(vamp, 'browse', lambda wait=2.0: [])
    monkeypatch.setattr(vamp, 'usb_devices', lambda: [('Q2CODA', None)])
    assert vamp.main(['find']) == 0 and 'usb:Q2CODA' in capsys.readouterr().out

    def no_libusb():
        raise ConnectionError('no libusb 1.0')
    monkeypatch.setattr(vamp, 'usb_devices', no_libusb)
    assert vamp.main(['find']) == 1 and 'USB not looked at: no libusb' in capsys.readouterr().err


# ---- recovery-stage ----

class FakeCard:
    """The Q2's shell for recovery-stage: the card mounted or not (q2remote.card_mounted's mountpoint through
    q2remote.run's wrapper, its script a stdin file), push's rename and size, sha256sum of what landed."""
    def __init__(self, adbd, mounted=True, corrupt=None):
        self.adbd, self.mounted, self.corrupt, self.seen = adbd, mounted, corrupt, []
        for d in ('tmp', 'mnt/mmc/recovery-update'):
            (adbd.root / d).mkdir(parents=True, exist_ok=True)
        adbd.handler = self

    def __call__(self, cmd):
        self.seen.append(cmd)
        if m := re.match(r'exec <(/tmp/vamp-in-[0-9a-f]{8}); rm -f \1\n', cmd):
            script = self.adbd.path(m.group(1)).read_text()
            return b'\n\0q2rc=%d\n' % (0 if self.mounted or 'mountpoint' not in script else 1)
        if m := re.match(r"mv -f (\S+) (\S+) && sync && wc -c <\S+$", cmd):
            os.replace(self.adbd.path(m.group(1)), self.adbd.path(m.group(2)))
            return b'%d\n' % self.adbd.path(m.group(2)).stat().st_size
        if m := re.match(r'sha256sum (\S+)$', cmd):
            data = self.adbd.path(m.group(1)).read_bytes()
            if self.corrupt and m.group(1).endswith(self.corrupt):
                data += b'!'
            return f'{hashlib.sha256(data).hexdigest()}  {m.group(1)}\n'.encode()
        return b''


@pytest.fixture
def stock(tmp_path):
    d = tmp_path / 'recovery-update'
    d.mkdir()
    (d / 'xImage').write_bytes(b'stock kernel' * 100)
    (d / 'rootfs.squashfs').write_bytes(b'stock rootfs' * 1000)
    return d


def test_recovery_stage_puts_the_stock_folder_back(adbd, dev, stock, monkeypatch):
    FakeCard(adbd)
    monkeypatch.setattr(q2remote, 'TRANSPORT', dev)
    vamp.recovery_stage(dev, stock)
    for n in ('xImage', 'rootfs.squashfs'):
        assert (adbd.root / 'mnt/mmc/recovery-update' / n).read_bytes() == (stock / n).read_bytes()


def test_recovery_stage_refusals(adbd, dev, stock, tmp_path, monkeypatch):
    monkeypatch.setattr(q2remote, 'TRANSPORT', dev)
    (tmp_path / 'half').mkdir()
    (tmp_path / 'half/xImage').write_bytes(b'k')
    with pytest.raises(ValueError, match='no rootfs.squashfs'):
        vamp.recovery_stage(dev, tmp_path / 'half')
    card = FakeCard(adbd, mounted=False)
    with pytest.raises(ValueError, match='USB storage has the card'):
        vamp.recovery_stage(dev, stock)
    assert not any(c.startswith('mv -f') for c in card.seen)  # nothing copied
    FakeCard(adbd, corrupt='rootfs.squashfs')
    with pytest.raises(vamp.AdbError, match='rootfs.squashfs on the Q2 is not'):
        vamp.recovery_stage(dev, stock)


def test_restage_after_install(adbd, dev, stock, monkeypatch, capsys):
    monkeypatch.setattr(q2remote, 'TRANSPORT', dev)
    monkeypatch.setattr(vamp, 'recovery_stage', lambda d, keep: pytest.fail('staged'))
    assert vamp.restage(dev, None, 0, set()) == 0           # nothing kept
    assert vamp.restage(dev, str(stock), 1, set()) == 1     # the install failed: leave the card alone
    assert vamp.restage(dev, str(stock), 0, {'--dry-run'}) == 0  # a dry run unpacks nothing
    assert vamp.restage(dev, str(stock), 0, {'--no-wait'}) == 0
    assert f'vamp recovery-stage {stock}' in capsys.readouterr().err
    staged = []
    monkeypatch.setattr(vamp, 'recovery_stage', lambda d, keep: staged.append(keep))
    assert vamp.restage(dev, str(stock), 0, set()) == 0 and staged == [str(stock)]

    def fails(d, keep):
        raise vamp.AdbError('card full')
    monkeypatch.setattr(vamp, 'recovery_stage', fails)
    assert vamp.restage(dev, str(stock), 0, set()) == 1 and 'could not be put back (card full)' in capsys.readouterr().err


def test_recovery_stage_command_remembers_and_install_uses_it(adbd, stock, monkeypatch, tmp_path, capsys):
    import q2flash
    monkeypatch.setattr(vamp, 'STATE', tmp_path / 'cfg/target.json')
    monkeypatch.setattr(vamp, 'CONFIG', tmp_path / 'cfg/config.json')
    monkeypatch.setattr(vamp, 'load_key', lambda: KEY)
    FakeCard(adbd)
    t = f'127.0.0.1:{adbd.port}'
    assert vamp.main(['-t', t, 'recovery-stage', str(stock)]) == 0
    assert vamp.config_get('recovery') == str(stock.resolve())
    calls = []
    monkeypatch.setattr(q2flash, 'flash', lambda *a: calls.append(a) or 0)
    staged = []
    monkeypatch.setattr(vamp, 'recovery_stage', lambda d, keep: staged.append(keep))
    assert vamp.main(['-t', t, 'install', 'u.tar']) == 0 and staged == [str(stock.resolve())]
    (stock / 'xImage').unlink()
    with pytest.raises(ValueError, match='no xImage'):  # said before anything is installed
        vamp.main(['-t', t, 'install', 'u.tar'])
    assert len(calls) == 1
    assert vamp.main(['-t', t, 'recovery-stage', '--forget']) == 0 and vamp.config_get('recovery') is None
    (tmp_path / 'cfg/config.json').write_text('{broken')
    assert vamp.config_get('recovery') is None
    vamp.config_set('recovery', '/x')
    assert vamp.config_get('recovery') == '/x'
