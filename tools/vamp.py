#!/usr/bin/env python3
"""vamp: Vamp's developer CLI. It speaks adb's wire protocol itself (no Google adb), to the Q2's adbd over
Wi-Fi (docs/adb.md#adb-over-wi-fi) and finds the Q2 by name with mDNS. See docs/adb.md#vamp.

usage: vamp [-t TARGET] COMMAND [ARGS]

  find [--wait S]                       the Q2s advertising adb on this computer's networks
  connect [NAME|HOST[:PORT]]            check a Q2 answers and remember it (no argument: the one found)
  shell [COMMAND]                       COMMAND's output and exit status; none: an interactive root shell
  push LOCAL REMOTE                     a file to the Q2 (REMOTE a directory: under its name)
  pull REMOTE [LOCAL]                   a file from the Q2
  install UPDATE.TAR|BUILD [--dry-run] [--no-wait] [--min-battery N]
                                        push, check and install an update (tools/q2flash.py's flow)
  shot [--settle] OUT.png               the screen, upright
  log [LINES|-f|--clear]                demo's output
  key NAME [MS] | wheel N [--step-ms MS] | tap X Y | swipe X1 Y1 X2 Y2 [MS]
  state | wake | devices | events N [S] | reboot [--wait] | prof S [--all] [--build DIR]
  pubkey                                this computer's key, an adb_keys line
  audio [--out FILE.wav] [--play] [--seconds S] [--adb ADB]
                                        what the player hands the DAC, bit for bit: WAV, played live, or both

TARGET (or VAMP_TARGET) is a name `find` lists, HOST or HOST:PORT; without either, the Q2 `connect`
remembered. The key is adb's own, ~/.android/adbkey (made on first use), so a computer Google's adb
already trusts is trusted here too."""
import base64
import getpass
import hashlib
import io
import json
import os
import pathlib
import platform
import posixpath
import re
import secrets
import select
import shlex
import socket
import struct
import subprocess
import sys
import time
import typing

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import q2remote  # noqa: E402  the Q2's commands (shot, input, log, prof...): vamp is their transport

PORT = 5555
TIMEOUT = 30          # seconds without a byte from the Q2 before a command gives up (q2remote.TIMEOUT)
CONNECT_S = 10        # a TCP connect and the handshake
AUTH_WAIT_S = 30      # after offering the public key: a USB adbd's Allow this computer? card waits 30 s
STATE = pathlib.Path(os.environ.get('XDG_CONFIG_HOME') or pathlib.Path.home() / '.config') / 'vamp/target.json'

# ---- keys: adb's own files, PKCS#8 PEM and adbkey.pub ----

KEY = pathlib.Path(os.environ.get('ANDROID_USER_HOME') or pathlib.Path.home() / '.android') / 'adbkey'
SHA1_INFO = bytes.fromhex('3021300906052b0e03021a05000414')  # DigestInfo for SHA-1: adb signs its token as a SHA-1 hash
RSA_OID = bytes.fromhex('06092a864886f70d0101010500')        # rsaEncryption, NULL
SMALL_PRIMES = [p for p in range(3, 2000, 2) if all(p % q for q in range(3, int(p ** .5) + 1, 2))]


def _der(tag, body):
    n = len(body)
    size = bytes([n]) if n < 128 else bytes([0x80 | (n.bit_length() + 7) // 8]) + n.to_bytes((n.bit_length() + 7) // 8, 'big')
    return bytes([tag]) + size + body


def _der_int(v):
    b = v.to_bytes(v.bit_length() // 8 + 1, 'big')  # a leading zero byte keeps it positive
    return _der(2, b)


def _der_items(b):
    """The (tag, body) of each element of a DER sequence's body."""
    out, i = [], 0
    while i < len(b):
        tag, n = b[i], b[i + 1]
        i += 2
        if n & 0x80:
            k = n & 0x7f
            n, i = int.from_bytes(b[i:i + k], 'big'), i + k
        if i + n > len(b):
            raise ValueError('a truncated DER element')
        out.append((tag, b[i:i + n]))
        i += n
    return out


class Key:
    """An RSA key: n, e, d, and the primes for writing it out."""
    def __init__(self, n, e, d, p=0, q=0):
        self.n, self.e, self.d, self.p, self.q = n, e, d, p, q

    @classmethod
    def generate(cls, bits=2048):
        def prime(b):
            while True:
                c = secrets.randbits(b) | (3 << (b - 2)) | 1
                if any(c % p == 0 for p in SMALL_PRIMES):
                    continue
                if all(pow(a, c - 1, c) == 1 for a in (2, 3, 5, 7)) and _miller_rabin(c):
                    return c
        e = 65537
        while True:
            p, q = prime(bits // 2), prime(bits // 2)
            phi = (p - 1) * (q - 1)
            if p != q and (p * q).bit_length() == bits and phi % e:  # pragma: no branch  (a retry is rare: p == q, or a product a bit short)
                return cls(p * q, e, pow(e, -1, phi), p, q)

    @classmethod
    def from_pem(cls, text):
        m = re.search(r'-----BEGIN (RSA )?PRIVATE KEY-----(.*?)-----END', text, re.S)
        if not m:
            raise ValueError('not a PEM RSA private key')
        der = base64.b64decode(''.join(m.group(2).split()))
        (tag, body), = _der_items(der)
        items = _der_items(body)
        if not m.group(1):  # PKCS#8: version, the algorithm, then PKCS#1 in an OCTET STRING
            if len(items) < 3 or items[1][1] != RSA_OID or items[2][0] != 4:
                raise ValueError('not an RSA key')
            (tag, body), = _der_items(items[2][1])
            items = _der_items(body)
        v = [int.from_bytes(b, 'big') for t, b in items if t == 2]
        if len(v) < 6:
            raise ValueError('a short RSA key')
        return cls(v[1], v[2], v[3], v[4], v[5])

    def pem(self):
        d, p, q = self.d, self.p, self.q
        rsa = _der(0x30, b''.join(_der_int(x) for x in (0, self.n, self.e, d, p, q, d % (p - 1), d % (q - 1), pow(q, -1, p))))
        pk8 = _der(0x30, _der_int(0) + _der(0x30, RSA_OID) + _der(4, rsa))
        b64 = base64.b64encode(pk8).decode()
        return '-----BEGIN PRIVATE KEY-----\n' + '\n'.join(b64[i:i + 64] for i in range(0, len(b64), 64)) + '\n-----END PRIVATE KEY-----\n'

    def public_blob(self):
        """adb's RSAPublicKey (mincrypt's struct): the word count, -1/n mod 2^32, n, R^2 mod n, e; little-endian."""
        words = self.n.bit_length() // 32
        if words * 32 != self.n.bit_length():
            raise ValueError('adb keys are a whole number of 32-bit words')
        n0inv = (-pow(self.n, -1, 1 << 32)) % (1 << 32)
        rr = pow(2, 2 * 32 * words, self.n)
        return struct.pack('<II', words, n0inv) + self.n.to_bytes(4 * words, 'little') + rr.to_bytes(4 * words, 'little') + struct.pack('<I', self.e)

    def public_line(self, comment=None):
        comment = comment or f'{getpass.getuser()}@{platform.node().split(".")[0] or "vamp"}'
        return base64.b64encode(self.public_blob()).decode() + ' ' + comment

    def sign(self, token):
        """PKCS#1 v1.5 over the token as a SHA-1 digest, as adb's RSA_sign(NID_sha1) gives it."""
        k = (self.n.bit_length() + 7) // 8
        t = SHA1_INFO + token
        if len(t) + 11 > k:
            raise ValueError('a token too long for the key')
        em = b'\0\1' + b'\xff' * (k - len(t) - 3) + b'\0' + t
        return pow(int.from_bytes(em, 'big'), self.d, self.n).to_bytes(k, 'big')


def _miller_rabin(n, rounds=24):
    d, r = n - 1, 0
    while d % 2 == 0:
        d, r = d // 2, r + 1
    for _ in range(rounds):
        x = pow(secrets.randbelow(n - 3) + 2, d, n)
        if x in (1, n - 1):
            continue
        for _ in range(r - 1):
            x = pow(x, 2, n)
            if x == n - 1:
                break
        else:
            return False
    return True


def load_key(path=None):
    """The computer's key: adb's adbkey, made (with adbkey.pub beside it) when there is none."""
    path = pathlib.Path(path or os.environ.get('VAMP_KEY') or KEY)
    try:
        return Key.from_pem(path.read_text())
    except FileNotFoundError:
        pass
    key = Key.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(key.pem())
    (path.parent / (path.name + '.pub')).write_text(key.public_line() + '\n')
    print(f'vamp: made a key, {path}', file=sys.stderr)
    return key


# ---- the adb protocol (system/core/adb/protocol.txt): 24-byte messages, then their data ----

def _cmd(name):
    return struct.unpack('<I', name.encode())[0]


SYNC, CNXN, AUTH, OPEN, OKAY, CLSE, WRTE = map(_cmd, ('SYNC', 'CNXN', 'AUTH', 'OPEN', 'OKAY', 'CLSE', 'WRTE'))
NAMES = {v: k for k, v in zip(('SYNC', 'CNXN', 'AUTH', 'OPEN', 'OKAY', 'CLSE', 'WRTE'), (SYNC, CNXN, AUTH, OPEN, OKAY, CLSE, WRTE))}
VERSION = 0x01000000
MAXDATA = 4096  # 4.2.2's MAX_PAYLOAD, the Q2's adbd: a message with more data is dropped as malformed
AUTH_TOKEN, AUTH_SIGNATURE, AUTH_RSAPUBLICKEY = 1, 2, 3
SYNC_DATA = 65536  # a sync DATA chunk at most, as adb's


def message(cmd, arg0, arg1, data=b''):
    return struct.pack('<6I', cmd, arg0, arg1, len(data), sum(data) & 0xffffffff, cmd ^ 0xffffffff) + data


class AdbError(Exception):
    pass


class Unauthorized(AdbError, PermissionError):
    pass


class TcpTransport:
    def __init__(self, host, port=PORT, timeout=CONNECT_S):
        self.where = f'{host}:{port}'
        try:
            self.sock = socket.create_connection((host, port), timeout)
        except OSError as e:
            raise ConnectionError(f'cannot reach the Q2 at {self.where}: {e}') from e
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # one small message, one packet
        except OSError:
            pass  # not TCP (test/unit/test_vamp.py's socketpair)
        self.buf = bytearray()

    def send(self, data):
        self.sock.sendall(data)

    def recv(self, n, timeout):
        """n bytes; what a timeout cut short is kept for the next call, so a message is never split."""
        self.sock.settimeout(timeout)
        while len(self.buf) < n:
            part = self.sock.recv(max(n - len(self.buf), 65536))
            if not part:
                raise ConnectionError(f'the Q2 at {self.where} closed the connection')
            self.buf += part
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        self.sock.close()


class Stream:
    """One service on the connection: what it has sent and not yet read, and whether the Q2 may take a write."""
    def __init__(self, conn, local):
        self.conn, self.local, self.remote = conn, local, None
        self.inbox = bytearray()
        self.ready = False   # the Q2's OKAY for our last WRTE (or for the OPEN)
        self.closed = False

    def write(self, data):
        view = memoryview(data)
        for i in range(0, len(view), self.conn.max):
            if self.closed:
                raise AdbError('the Q2 closed the stream')
            self.ready = False
            self.conn.send(WRTE, self.local, self.remote, bytes(view[i:i + self.conn.max]))
            while not self.ready and not self.closed:  # one WRTE in flight, as adb's protocol requires
                self.conn.pump(TIMEOUT)

    def read(self, timeout=TIMEOUT):
        """What has arrived, waiting up to timeout for something; b'' once the Q2 has closed it."""
        while not self.inbox and not self.closed:
            self.conn.pump(timeout)
        out = bytes(self.inbox)
        self.inbox.clear()
        return out

    def read_exact(self, n, timeout=TIMEOUT):
        while len(self.inbox) < n:
            if self.closed:
                raise AdbError('the Q2 closed the stream early')
            self.conn.pump(timeout)
        out = bytes(self.inbox[:n])
        del self.inbox[:n]
        return out

    def close(self):
        if not self.closed:
            self.closed = True
            self.conn.streams.pop(self.local, None)
            try:
                self.conn.send(CLSE, self.local, self.remote or 0)
            except OSError:
                pass  # the connection is gone, and the stream with it


class Connection:
    """A connected, authorized adb session: CNXN, then AUTH with key, then streams."""
    def __init__(self, transport, key, banner=b'host::vamp\0', auth_wait=AUTH_WAIT_S):
        self.t, self.key = transport, key
        self.max = MAXDATA
        self.streams = {}
        self.next_id = 1
        self.device = ''
        self._handshake(banner, auth_wait)

    def send(self, cmd, arg0, arg1, data=b''):
        self.t.send(message(cmd, arg0, arg1, data))

    def read(self, timeout):
        try:
            head = self.t.recv(24, timeout)
        except socket.timeout:
            raise TimeoutError(f'no answer from the Q2 for {timeout:g} s') from None
        cmd, a0, a1, n, check, magic = struct.unpack('<6I', head)
        if magic != cmd ^ 0xffffffff or n > max(self.max, MAXDATA) * 64:
            raise AdbError('a malformed message from the Q2')
        try:
            data = self.t.recv(n, timeout) if n else b''
        except socket.timeout:
            self.t.buf[:0] = head  # read again whole next time
            raise TimeoutError(f'no answer from the Q2 for {timeout:g} s') from None
        if check and sum(data) & 0xffffffff != check:  # 4.2.2 sends the sum; adb from 7.0 may send 0
            raise AdbError('a message with a wrong checksum')
        return cmd, a0, a1, data

    def _handshake(self, banner, auth_wait):
        self.send(CNXN, VERSION, MAXDATA, banner)
        signed = offered = False
        deadline = time.monotonic() + CONNECT_S
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                if offered:
                    raise Unauthorized(
                        'the Q2 did not take this computer\'s key. Over Wi-Fi only a key it already trusts gets in: '
                        'allow this computer once over USB (Always allow), or put `vamp pubkey` in the card\'s '
                        'adb_keys (docs/adb.md#adb-over-wi-fi)')
                raise TimeoutError('the Q2 did not finish the handshake')
            try:
                cmd, a0, a1, data = self.read(left)
            except TimeoutError:
                continue
            if cmd == CNXN:
                self.max = min(MAXDATA, a1) if a1 else MAXDATA
                self.device = data.rstrip(b'\0').decode(errors='replace')
                return
            if cmd == AUTH and a0 == AUTH_TOKEN:
                if not signed:
                    signed = True
                    self.send(AUTH, AUTH_SIGNATURE, 0, self.key.sign(data))
                elif not offered:  # our signature was refused: offer the key, which a USB adbd asks the Q2's user about
                    offered = True
                    self.send(AUTH, AUTH_RSAPUBLICKEY, 0, self.key.public_line().encode() + b'\0')
                    deadline = time.monotonic() + auth_wait

    def pump(self, timeout):
        """Read one message and hand it to its stream."""
        cmd, a0, a1, data = self.read(timeout)
        s = self.streams.get(a1)
        if cmd == OKAY and s:
            if s.remote is None:
                s.remote = a0
            s.ready = True
        elif cmd == WRTE:
            if s:
                s.inbox += data
                self.send(OKAY, a1, a0)
            else:
                self.send(CLSE, a1, a0)
        elif cmd == CLSE and s:
            s.closed = True
            self.streams.pop(a1, None)
        elif cmd == CNXN:  # the Q2's adbd started over: every stream is gone
            for s in list(self.streams.values()):
                s.closed = True
            self.streams.clear()

    def open(self, service, timeout=CONNECT_S):
        s = Stream(self, self.next_id)
        self.next_id += 1
        self.streams[s.local] = s
        if len(service) + 1 > self.max:
            raise AdbError(f'a service name over {self.max - 1} bytes')
        self.send(OPEN, s.local, 0, service.encode() + b'\0')
        while s.remote is None and not s.closed:
            self.pump(timeout)
        if s.remote is None:
            raise AdbError(f'the Q2 refused {service.split(":")[0]}:')
        return s

    def close(self):
        self.t.close()

    # ---- exec: and shell: ----

    def exec_out(self, command, timeout=TIMEOUT, sink=None, done=None):
        """command's output (stdout and stderr, no terminal); streamed to sink when given. done(output so far)
        ends the read early. timeout: seconds without a byte; None waits forever."""
        s = self.open('exec:' + command)
        out = bytearray()
        try:
            while True:
                part = s.read(timeout if timeout is not None else 3600 * 24)
                if not part:
                    break
                if sink:
                    sink.write(part)
                    sink.flush()
                else:
                    out += part
                if done and done(out):
                    break
        finally:
            s.close()
        return bytes(out)

    # ---- sync: (file_sync_service.c) ----

    def _sync_request(self, s, rid, body):
        s.write(rid.encode() + struct.pack('<I', len(body)) + body)

    def _sync_fail(self, s, what):
        n = struct.unpack('<I', s.read_exact(4))[0]
        return AdbError(f'{what}: {s.read_exact(n).decode(errors="replace")}')

    def stat(self, remote):
        """(mode, size, mtime) of remote; mode 0 when there is nothing there."""
        s = self.open('sync:')
        try:
            self._sync_request(s, 'STAT', remote.encode())
            head = s.read_exact(16)
            if head[:4] != b'STAT':
                raise AdbError('a malformed STAT reply')
            return struct.unpack('<III', head[4:])
        finally:
            self._sync_quit(s)

    def _sync_quit(self, s):
        if not s.closed:
            try:
                self._sync_request(s, 'QUIT', b'')
            except (AdbError, OSError):
                pass
            s.close()

    def push(self, data_iter, remote, mode=0o644, mtime=None, progress=None):
        """Each chunk of data_iter to remote, then DONE; refused with the Q2's own reason."""
        s = self.open('sync:')
        try:
            self._sync_request(s, 'SEND', f'{remote},{0o100000 | mode}'.encode())
            sent = 0
            for chunk in data_iter:
                for i in range(0, len(chunk), SYNC_DATA):
                    part = chunk[i:i + SYNC_DATA]
                    s.write(b'DATA' + struct.pack('<I', len(part)) + part)
                    sent += len(part)
                    if progress:
                        progress(sent)
            s.write(b'DONE' + struct.pack('<I', int(time.time() if mtime is None else mtime)))
            rid = s.read_exact(4, timeout=120)  # the Q2 closes the file, which on the card can take a while
            if rid == b'FAIL':
                raise self._sync_fail(s, f'push to {remote} failed')
            if rid != b'OKAY':
                raise AdbError('a malformed reply to a push')
            s.read_exact(4)
            return sent
        finally:
            self._sync_quit(s)

    def pull(self, remote, sink):
        s = self.open('sync:')
        got = 0
        try:
            self._sync_request(s, 'RECV', remote.encode())
            while True:
                rid = s.read_exact(4)
                if rid == b'FAIL':
                    raise self._sync_fail(s, f'pull of {remote} failed')
                n = struct.unpack('<I', s.read_exact(4))[0]
                if rid == b'DONE':
                    return got
                if rid != b'DATA' or n > SYNC_DATA:
                    raise AdbError('a malformed reply to a pull')
                sink.write(s.read_exact(n))
                got += n
        finally:
            self._sync_quit(s)


# ---- finding the Q2: mDNS (RFC 6762), the records patch/adb_mdns.c answers ----

MDNS_GROUP, MDNS_PORT = '224.0.0.251', 5353
SERVICE = '_adb._tcp.local'
PTR, TXT, SRV, A = 12, 16, 33, 1


def mdns_query(name, qtype=PTR):
    q = b''.join(bytes([len(label)]) + label.encode() for label in name.split('.')) + b'\0'
    return struct.pack('>6H', 0, 0, 1, 0, 0, 0) + q + struct.pack('>HH', qtype, 1)


def _name(p, i):
    """The name at p[i] and the offset past it; compression pointers followed, only backwards."""
    labels, end, hops = [], None, 0
    while True:
        if i >= len(p):
            raise ValueError('a name past the packet')
        n = p[i]
        if n & 0xc0 == 0xc0:
            if i + 1 >= len(p):
                raise ValueError('a cut pointer')
            ptr = (n & 0x3f) << 8 | p[i + 1]
            if ptr >= i or hops > 32:
                raise ValueError('a pointer forward')
            end, i, hops = end or i + 2, ptr, hops + 1
            continue
        if n == 0:
            return '.'.join(labels), end or i + 1
        labels.append(p[i + 1:i + 1 + n].decode('utf-8', 'replace'))
        i += 1 + n


def mdns_records(p):
    """(name, type, data) of each answer and additional record of a response; [] for a query."""
    if len(p) < 12 or not p[2] & 0x80:
        return []
    qd, an, ns, ar = struct.unpack('>4H', p[4:12])
    i, out = 12, []
    for _ in range(qd):
        _, i = _name(p, i)
        i += 4
    for _ in range(an + ns + ar):
        name, i = _name(p, i)
        if i + 10 > len(p):
            raise ValueError('a cut record')
        rtype, _, ttl, rdlen = struct.unpack('>HHIH', p[i:i + 10])
        i += 10
        rd = p[i:i + rdlen]
        if rtype == PTR:
            data = _name(p, i)[0]
        elif rtype == SRV:
            data = struct.unpack('>3H', rd[:6]) + (_name(p, i + 6)[0],)
        elif rtype == TXT:
            data, j = [], 0
            while j < len(rd):
                data.append(rd[j + 1:j + 1 + rd[j]].decode('utf-8', 'replace'))
                j += 1 + rd[j]
        elif rtype == A and rdlen == 4:
            data = socket.inet_ntoa(rd)
        else:
            data = rd
        out.append((name, rtype, data, ttl))
        i += rdlen
    return out


def local_addresses():  # pragma: no mutate block
    """This computer's IPv4 addresses, loopback left out: `ip` on Linux, `ifconfig` on macOS, else the
    default route's."""
    for cmd in (['ip', '-4', '-o', 'addr', 'show'], ['ifconfig']):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            continue
        addrs = [a for a in re.findall(r'inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)', out) if not a.startswith('127.')]
        if addrs:
            return sorted(set(addrs))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('192.0.2.1', 9))  # picks the route's local address; sends nothing
            return [s.getsockname()[0]]
    except OSError:
        return []


def browse(wait=2.0, addresses=None):  # pragma: no mutate block
    """The Q2s answering for _adb._tcp on each of addresses' networks: [{name, ip, port, id, host}], by name.

    The query leaves from port 5353, as a DNS-SD browser's does, so the answers are multicast and reach
    every socket in the group (adb_mdns.c answers no query from another port)."""
    addresses = local_addresses() if addresses is None else addresses
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)  # macOS's mDNSResponder holds 5353 too
    s.bind(('', MDNS_PORT))
    joined = []
    for a in addresses:
        try:
            s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MDNS_GROUP) + socket.inet_aton(a))
            joined.append(a)
        except OSError:
            pass
    if not joined:
        s.close()
        raise ConnectionError('no network to look on (no IPv4 address that can join mDNS)')
    records = []
    try:
        for when in (0, wait / 2):
            deadline = time.monotonic() + (wait / 2 if when else wait / 2)
            for a in joined:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(a))
                s.sendto(mdns_query(SERVICE), (MDNS_GROUP, MDNS_PORT))
            while (left := deadline - time.monotonic()) > 0:
                if not select.select([s], [], [], left)[0]:
                    break
                try:
                    records += mdns_records(s.recv(9000))
                except ValueError:
                    pass
    finally:
        s.close()
    return instances(records)


def instances(records):
    """The Q2s that records describe: each PTR for SERVICE with its SRV, TXT and the A of the SRV's host."""
    # names compare in any case (RFC 6762 section 16); the instance keeps the case it was announced in
    ptr = {d for n, t, d, ttl in records if t == PTR and n.lower() == SERVICE and ttl}
    srv = {n.lower(): d for n, t, d, ttl in records if t == SRV}
    txt = {n.lower(): d for n, t, d, ttl in records if t == TXT}
    addr = {n.lower(): d for n, t, d, ttl in records if t == A}
    found = {}
    for inst in ptr:
        if inst.lower() not in srv:
            continue
        _, _, port, host = srv[inst.lower()]
        ip = addr.get(host.lower())
        if not ip:
            continue
        fields = dict(f.split('=', 1) for f in txt.get(inst.lower(), []) if '=' in f)
        name = inst[:-len(SERVICE) - 1]
        found[fields.get('id') or name] = {'name': name, 'ip': ip, 'port': port, 'id': fields.get('id', ''),
                                           'host': host}
    return sorted(found.values(), key=lambda q: (q['name'], q['ip']))


# ---- the Q2 to use ----

def parse_target(spec):
    """HOST, HOST:PORT or [V6]:PORT as (host, port); None for a name to look up with mDNS."""
    m = re.fullmatch(r'\[([0-9a-fA-F:]+)\](?::(\d+))?|([0-9.]+|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)(?::(\d+))?', spec)
    if not m:
        return None
    host, port = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return host, int(port or PORT)


def saved():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def save(target):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(target, indent=1) + '\n')


def resolve(spec=None, wait=2.0):
    """The target as {name, ip, port, id}: spec (a name, an id or an address), else the one `connect` saved."""
    if spec:
        if addr := parse_target(spec):
            return {'name': spec, 'ip': addr[0], 'port': addr[1], 'id': ''}
        found = [q for q in browse(wait) if spec.lower() in (q['name'].lower(), q['id'].lower())]
        if not found:
            raise ConnectionError(f'no Q2 named {spec!r} answers mDNS here (`vamp find` lists those that do)')
        if len(found) > 1:
            raise ConnectionError(f'{len(found)} Q2s are named {spec!r}: use the id or the address '
                                  f'({", ".join(q["id"] + " " + q["ip"] for q in found)})')
        return found[0]
    if target := saved():
        return target
    raise ConnectionError('no Q2 chosen: `vamp connect NAME` (or -t / VAMP_TARGET); `vamp find` lists them')


class Device:
    """The chosen Q2: one connection, opened when first needed and again after it drops. A Q2 whose
    address changed (a new network, a new lease after sleep) is found again by its id with mDNS."""
    def __init__(self, target, key=None):
        self.target, self.key, self.conn = target, key, None

    def connection(self):
        if self.conn:
            return self.conn
        self.key = self.key or load_key()
        try:
            self.conn = Connection(TcpTransport(self.target['ip'], self.target['port']), self.key)
        except (ConnectionError, TimeoutError) as first:
            ident = self.target.get('id') or (self.target['name'] if not parse_target(self.target['name']) else '')
            if not ident:
                raise
            found = [q for q in browse() if ident.lower() in (q['id'].lower(), q['name'].lower())]
            if len(found) != 1 or (found[0]['ip'], found[0]['port']) == (self.target['ip'], self.target['port']):
                raise first
            self.target.update(found[0])
            if saved() and saved().get('id') == self.target.get('id'):
                save(self.target)
            self.conn = Connection(TcpTransport(self.target['ip'], self.target['port']), self.key)
        return self.conn

    def drop(self):
        if self.conn:
            try:
                self.conn.close()
            except OSError:
                pass
        self.conn = None

    def call(self, fn):
        """fn(connection); a connection that failed under it is dropped, so the next call opens another."""
        try:
            return fn(self.connection())
        except BaseException:
            self.drop()
            raise

    def exec_out(self, command, timeout=TIMEOUT, sink=None, done=None):
        return self.call(lambda c: c.exec_out(command, timeout, sink, done))

    def put(self, data, remote, mode=0o600):
        return self.call(lambda c: c.push([data], remote, mode))

    def shell(self, cmd, data=b'', timeout=TIMEOUT, host=None, sink=None, done=None):
        """q2remote.shell's contract over adb: cmd under sh with data as its stdin, and the output. adb's
        streams cannot be half-closed, so data goes to a file first, which cmd's shell reads and removes."""
        tmp = []
        try:
            if data:
                tmp.append(f'/tmp/vamp-in-{secrets.token_hex(4)}')
                self.put(data, tmp[-1])
                cmd = f'exec <{tmp[-1]}; rm -f {tmp[-1]}\n{cmd}'
            if len(cmd) > MAXDATA - 200:  # past an OPEN's room: the command itself goes as a file
                tmp.append(f'/tmp/vamp-sh-{secrets.token_hex(4)}')
                self.put(cmd.encode(), tmp[-1])
                cmd = f'sh {tmp[-1]}; r=$?; rm -f {tmp[-1]}; exit $r'
            return self.exec_out(cmd, timeout, sink, done)
        except AdbError as e:
            raise ConnectionError(str(e)) from e
        finally:
            if tmp and self.conn:
                try:
                    self.exec_out('rm -f ' + ' '.join(tmp), 10)
                except (OSError, AdbError):
                    pass


# ---- commands ----

# The output tap's FIFO (player/tap.c TAP_FIFO), made by its reader: the player writes to it only while one is open
TAP_FIFO = '/tmp/q2tap.pcm'
TAP_READ = f'[ -p {TAP_FIFO} ] || {{ rm -f {TAP_FIFO}; mkfifo {TAP_FIFO}; }}; exec cat {TAP_FIFO}'
AUDIO_USAGE = 'usage: vamp audio [--out FILE.wav] [--play] [--seconds S] [--adb ADB]'


def audio_args(a):
    """vamp audio's flags: (out, play, seconds, adb). No --out: --play."""
    out, play, seconds, adb, a = None, False, None, None, list(a)
    while a:
        flag = a.pop(0)
        if flag == '--play':
            play = True
        elif flag in ('--out', '--seconds', '--adb') and a:
            v = a.pop(0)
            if flag == '--out':
                out = v
            elif flag == '--seconds':
                seconds = float(v)
            else:
                adb = v
        else:
            raise ValueError(AUDIO_USAGE)
    return out, play or not out, seconds, adb


def audio(dev, a, say=None, player=None):
    """vamp audio (docs/usb-audio.md#vamp-audio): the tap's stream from the Q2 (over vamp's connection, or Google's
    adb with --adb, which reaches a Q2 on the cable) through q2tap into WAV files and/or ffplay, until --seconds of
    the device's audio or Ctrl-C. Its summary line."""
    import q2tap
    out, play, seconds, adb = audio_args(a)
    say = say or (lambda t: print(t, file=sys.stderr))
    tap = q2tap.Tap(q2tap.sinks(out, play, say, **({'player': player} if player else {})))

    class Sink:
        def write(self, part):
            tap.feed(part)

        def flush(self):
            pass

    def done(_):
        return seconds is not None and tap.seconds() >= seconds
    say(f'vamp audio: listening on the Q2\'s tap (nothing comes while nothing plays)')
    try:
        if adb:
            p = subprocess.Popen([adb, 'exec-out', TAP_READ], stdout=subprocess.PIPE)
            out = typing.cast(io.BufferedReader, p.stdout)  # stdout=PIPE: buffered, so read1 takes what has come
            try:
                while not done(None):
                    part = out.read1(1 << 16)
                    if not part:
                        break
                    tap.feed(part)
            finally:
                p.kill()
                p.wait()
        else:
            dev.exec_out(TAP_READ, None, Sink(), done)
    except KeyboardInterrupt:
        if dev:
            dev.drop()
    finally:
        tap.close()
    line = q2tap.summary(tap)
    say(line)
    return line

RC = re.compile(rb'\n\0vamprc=(\d+)\n\Z')


def run_streamed(dev, command, out):
    """command under sh in its own process group, its output to out as it comes; its exit status. Ctrl-C
    kills the group (q2remote.run's way: the group's pid in a file)."""
    pid = f'/tmp/vamp-{secrets.token_hex(4)}.pid'
    inner = f'echo $$ >{pid}; ({command}) </dev/null; r=$?; rm -f {pid}; printf "\\n\\0vamprc=%d\\n" $r'
    held = bytearray()  # the last bytes, until it is clear they are not the status line

    class Sink:
        def write(self, part):
            held.extend(part)
            if len(held) > 16:
                out.write(bytes(held[:-16]))
                del held[:-16]

        def flush(self):
            out.flush()
    try:
        dev.shell(f'exec setsid sh -c {shlex.quote(inner)}', timeout=None, sink=Sink(),
                  done=lambda _: RC.search(bytes(held)))
    except KeyboardInterrupt:
        dev.drop()
        try:
            dev.exec_out(f'p=$(cat {pid} 2>/dev/null) && kill -- -$p; rm -f {pid}', 5)
        except (OSError, AdbError):
            pass
        raise
    m = RC.search(bytes(held))
    if not m:
        out.write(bytes(held))
        raise ConnectionError('the Q2 closed the connection before the command finished')
    out.write(bytes(held[:m.start()]))
    out.flush()
    return int(m.group(1))


def interactive(dev):  # pragma: no mutate block
    """shell: on a terminal of the Q2's own (adbd's pty), this terminal raw until it ends."""
    conn = dev.connection()
    s = conn.open('shell:')
    tty = sys.stdin.isatty()
    if tty:
        import termios, tty as ttymod
        saved_mode = termios.tcgetattr(sys.stdin)
        ttymod.setraw(sys.stdin)
    try:
        while not s.closed:
            if s.inbox:
                os.write(1, bytes(s.inbox))
                s.inbox.clear()
                continue
            ready = select.select([sys.stdin, conn.t], [], [], 1)[0]
            if sys.stdin in ready:
                data = os.read(0, 4096)
                if not data:
                    break
                s.write(data)
            if conn.t in ready or conn.t.buf:
                conn.pump(TIMEOUT)
    finally:
        if tty:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, saved_mode)
        s.close()
    return 0


def push(dev, local, remote):
    """local to remote (a directory: under local's name) through remote.part, renamed once whole; its size
    on the Q2 checked. /mnt/mmc refused while USB storage has the card."""
    local = pathlib.Path(local)
    size = local.stat().st_size
    conn = dev.connection()
    mode, _, _ = conn.stat(remote)
    if remote.endswith('/') or mode & 0o170000 == 0o040000:
        remote = posixpath.join(remote, local.name)
    remote = posixpath.normpath(remote)
    if (remote + '/').startswith(q2remote.CARD + '/'):
        q2remote.card_mounted()
    part = remote + '.part'
    start, shown = time.monotonic(), [0.0]

    def progress(sent):
        if sys.stderr.isatty() and time.monotonic() - shown[0] > .5:
            shown[0] = time.monotonic()
            print(f'\r{sent * 100 // max(size, 1)}% of {size / 1e6:.1f} MB', end='', file=sys.stderr, flush=True)

    def chunks():
        with open(local, 'rb') as f:
            while chunk := f.read(1 << 20):
                yield chunk
    dev.call(lambda c: c.push(chunks(), part, local.stat().st_mode & 0o777, local.stat().st_mtime, progress))
    out = dev.exec_out(f'mv -f {shlex.quote(part)} {shlex.quote(remote)} && sync && wc -c <{shlex.quote(remote)}', 120)
    if out.strip() != str(size).encode():
        dev.exec_out(f'rm -f {shlex.quote(part)}', 10)
        raise AdbError(f'push to {remote} failed: the Q2 holds {out.decode(errors="replace").strip() or "nothing"}, '
                       f'not {size} bytes')
    took = time.monotonic() - start
    if sys.stderr.isatty():
        print('\r', end='', file=sys.stderr)
    print(f'{remote}: {size} bytes in {took:.1f} s ({size / max(took, 1e-3) / 1e6:.2f} MB/s)')
    return remote


def pull(dev, remote, local=None):
    local = pathlib.Path(local or posixpath.basename(remote))
    if local.is_dir():
        local = local / posixpath.basename(remote)
    part = local.with_name(local.name + '.part')
    with open(part, 'wb') as f:
        n = dev.call(lambda c: c.pull(remote, f))
    os.replace(part, local)
    print(f'{local}: {n} bytes')


class FlashAdb:
    """tools/q2flash.py's adb, over vamp: run('push', LOCAL, REMOTE), sh(COMMAND) and state()."""
    def __init__(self, dev):
        self.dev = dev

    def run(self, *args, timeout=60):
        if args[:1] != ('push',) or len(args) != 3:
            raise ValueError(f'q2flash asked vamp for adb {" ".join(args)}')
        try:
            self.dev.call(lambda c: c.push(_file_chunks(args[1]), args[2]))
            return 0, ''
        except (AdbError, OSError) as e:
            return 1, str(e)

    def sh(self, command, timeout=60):
        return self.dev.exec_out(command, timeout).decode(errors='replace')

    def state(self):
        """'device' when the Q2 answers now (a fresh connection: the install restarts it)."""
        self.dev.drop()
        try:
            self.dev.connection()
            return 'device'
        except (OSError, AdbError):
            return ''


def _file_chunks(path):
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            yield chunk


Q2REMOTE_COMMANDS = {'shot', 'log', 'key', 'wheel', 'tap', 'swipe', 'state', 'wake', 'devices', 'events', 'reboot', 'prof'}


def main(argv=None):  # pragma: no mutate block
    argv = list(sys.argv[1:] if argv is None else argv)
    spec = os.environ.get('VAMP_TARGET')
    if argv[:1] in (['-t'], ['--target']) and len(argv) > 1:
        spec, argv = argv[1], argv[2:]
    if not argv or argv[0] in ('-h', '--help', 'help'):
        print(__doc__)
        return 0
    cmd, a = argv[0], argv[1:]
    if cmd == 'pubkey' and not a:
        print(load_key().public_line())
        return 0
    if cmd == 'find' and a in ([], ) or (cmd == 'find' and len(a) == 2 and a[0] == '--wait'):
        found = browse(float(a[1]) if a else 2.0)
        for q in found:
            print(f'{q["name"]}\t{q["ip"]}:{q["port"]}\tid={q["id"] or "-"}')
        if not found:
            print('vamp: no Q2 answered (Wi-Fi on, a --dev build, a trusted key on it: docs/adb.md#adb-over-wi-fi)',
                  file=sys.stderr)
        return 0 if found else 1
    if cmd == 'connect' and len(a) <= 1:
        if a:
            target = resolve(a[0])
        else:
            found = browse()
            if len(found) != 1:
                raise ConnectionError(f'{len(found)} Q2s answered: name one (`vamp find`)')
            target = found[0]
        dev = Device(target)
        tag = dev.exec_out('q2update version 2>/dev/null', 30).split()
        save(dev.target)
        print(f'connected to {dev.target["name"]} at {dev.target["ip"]}:{dev.target["port"]}'
              + (f', running {tag[2].decode()}' if len(tag) > 2 else ''))
        return 0
    if cmd == 'audio' and '--adb' in a:
        audio(None, a)
        return 0
    dev = Device(resolve(spec))
    q2remote.TRANSPORT = dev
    if cmd == 'audio':
        audio(dev, a)
        return 0
    if cmd == 'shell':
        if not a:
            return interactive(dev)
        return run_streamed(dev, ' '.join(a) if len(a) > 1 else a[0], sys.stdout.buffer)
    if cmd == 'push' and len(a) == 2:
        push(dev, *a)
        return 0
    if cmd == 'pull' and len(a) in (1, 2):
        pull(dev, *a)
        return 0
    if cmd == 'install' and a:
        import q2flash
        flags = set(a[1:])
        minimum = q2flash.MIN_BATTERY
        if '--min-battery' in a:
            i = a.index('--min-battery')
            minimum = int(a[i + 1])
            flags -= {'--min-battery', a[i + 1]}
        if flags - {'--dry-run', '--no-wait'}:
            raise ValueError('usage: vamp install UPDATE.TAR|BUILD [--dry-run] [--no-wait] [--min-battery N]')
        try:
            return q2flash.flash(a[0], FlashAdb(dev), '--dry-run' in flags, minimum, '--no-wait' not in flags)
        except q2flash.Refused as e:
            print(f'vamp: install refused: {e}', file=sys.stderr)
            return 1
    if cmd in Q2REMOTE_COMMANDS:
        if cmd == 'shot' and '--adb' in a:
            raise ValueError('vamp shot always goes over adb: leave out --adb')
        return q2remote.main([cmd, *a])
    raise ValueError(__doc__[__doc__.index('usage:'):])


def entry():  # pragma: no mutate block
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except (ValueError, OSError, AdbError, RuntimeError) as e:
        print(f'vamp: {e}', file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    entry()
