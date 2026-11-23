"""coda-rc's frames, protobuf subset, pairing, authentication and sealing (docs/remote-protocol.md), with no
socket: patch/coda_rc_core.c built for this machine with test/unit/coda_rc_host.c as its platform, driven
through ctypes by tools/coda_rc_client.py's Session; and tools/coda_rc_crypto.py checked against Monocypher."""
import ctypes
import hashlib
import hmac
import os
import pathlib
import struct
import subprocess

import pytest

import coda_rc_client as client
import coda_rc_crypto as cc

ROOT = pathlib.Path(__file__).resolve().parents[2]

PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc_core.c', PATCH / 'monocypher/monocypher.c', PATCH / 'monocypher/monocypher-ed25519.c',
           ROOT / 'test/unit/coda_rc_host.c']


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    so = tmp_path_factory.mktemp('coda_rc') / 'core.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-I', str(PATCH), *map(str, SOURCES), '-o', str(so)], check=True)
    L = ctypes.CDLL(str(so))
    L.session.restype = ctypes.c_void_p
    L.session.argtypes = [ctypes.c_int]
    L.crc_feed.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    L.crc_close.argtypes = [ctypes.c_void_p]
    L.crc_tick.argtypes = []
    return L


class Core:
    """A socket-like end of one core session: sendall feeds crc_feed, recv returns what it wrote."""

    def __init__(self, L, i=0):
        self.L, self.s, self.i, self.closed = L, L.session(i), i, False
        self.out = ctypes.c_size_t.in_dll(L, 'out_n')
        self.buf = (ctypes.c_uint8 * (1 << 20)).in_dll(L, 'out')
        self.out.value = 0

    def sendall(self, b):
        if self.closed:
            raise ConnectionError('closed')
        if self.L.crc_feed(self.s, bytes(b), len(b)) < 0:
            self.L.crc_close(self.s)
            self.closed = True

    def recv(self, n):
        k = self.out.value
        if not k:
            if self.closed:
                return b''
            raise TimeoutError('nothing written')
        data = bytes(self.buf[:k])
        self.out.value = 0
        return data

    def settimeout(self, t):
        pass

    def close(self):
        pass


def var(L, name, kind=ctypes.c_int):
    return kind.in_dll(L, name)


def shown(L):
    return (ctypes.c_char * 8).in_dll(L, 'code').value.decode()


@pytest.fixture
def fresh(lib):
    lib.reset()  # every session an earlier test left, and a pairing with it
    var(lib, 'peers_n').value = 0
    var(lib, 'save_fails').value = 0
    var(lib, 'now_ms', ctypes.c_uint32).value += 400000  # past any pairing lock an earlier test left
    return lib


def paired(L, name='Pixel 8'):
    s = client.Session(Core(L), name)
    info = s.hello()
    assert not info['paired'] and info['model'] == 'Shanling Q2' and info['device_id'] == '0102030405060708'
    point = s.pair_start()
    assert shown(L) and len(shown(L)) == 6 and shown(L).isdigit()
    peer, ltk = s.pair_finish(shown(L), point)
    assert var(L, 'hidden').value == 1
    return s, peer, ltk


# ---- the primitives against Monocypher

@pytest.fixture(scope='module')
def mono(tmp_path_factory):
    so = tmp_path_factory.mktemp('mono') / 'mono.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', str(PATCH / 'monocypher/monocypher.c'),
                    str(PATCH / 'monocypher/monocypher-ed25519.c'), '-o', str(so)], check=True)
    return ctypes.CDLL(str(so))


def test_primitives_match_monocypher(mono):
    B = ctypes.create_string_buffer
    rnd = __import__('random').Random(7)
    for i in range(12):
        k, u, h = (bytes(rnd.getrandbits(8) for _ in range(32)) for _ in range(3))
        o = B(32)
        mono.crypto_x25519(o, k, u)
        assert o.raw == cc.x25519(k, u)
        mono.crypto_elligator_map(o, h)
        assert o.raw == cc.elligator_map(h)
        key, nonce, ad, msg = k, h[:12], u[:6], bytes(rnd.getrandbits(8) for _ in range(i * 37))
        ctx, ct, tag = B(64), B(max(len(msg), 1)), B(16)
        mono.crypto_aead_init_ietf(ctx, key, nonce)
        mono.crypto_aead_write(ctx, ct, tag, ad, ctypes.c_size_t(6), msg, ctypes.c_size_t(len(msg)))
        assert cc.seal(key, nonce, ad, msg) == ct.raw[:len(msg)] + tag.raw
        assert cc.open_(key, nonce, ad, ct.raw[:len(msg)] + tag.raw) == msg
        assert cc.open_(key, nonce, ad, ct.raw[:len(msg)] + bytes(16)) is None
        mac = B(64)
        mono.crypto_sha512_hmac(mac, key, ctypes.c_size_t(32), msg, ctypes.c_size_t(len(msg)))
        assert mac.raw == hmac.new(key, msg, hashlib.sha512).digest()


def test_rfc8439_vector():
    # RFC 8439 2.8.2's AEAD test vector
    key = bytes(range(0x80, 0xa0))
    nonce = bytes.fromhex('070000004041424344454647')
    ad = bytes.fromhex('50515253c0c1c2c3c4c5c6c7')
    pt = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, "
          b"sunscreen would be it.")
    out = cc.seal(key, nonce, ad, pt)
    assert out[-16:].hex() == '1ae10b594f09e26a7e902ecbd0600691'
    assert out[:16].hex() == 'd31a8d34648e60db7b86afbc53ef7ec2'


def test_x25519_rfc7748_vector():
    k = bytes.fromhex('a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4')
    u = bytes.fromhex('e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c')
    assert cc.x25519(k, u).hex() == 'c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552'


# ---- protobuf

def test_protobuf_round_trip():
    raw = client.encode([(1, 300), (2, 'héllo'), (3, b'\0\1'), (15, True), (16, 0)])
    m = client.decode(raw)
    assert m == {1: [300], 2: ['héllo'.encode()], 3: [b'\0\1'], 15: [1], 16: [0]}
    with pytest.raises(ValueError):
        client.decode(b'\x0a\x05ab')  # a length past the end


# ---- the handshake

def test_pair_then_authenticate_seals_both_ways(fresh):
    s, peer, ltk = paired(fresh)
    assert len(peer) == 8 and var(fresh, 'peers_n').value == 1
    assert (ctypes.c_char * 32).in_dll(fresh, 'shown_name').value == b'Pixel 8'
    s.auth(ltk)
    # crc_app echoes: a sealed request comes back sealed, through both directions' counters
    for n in (0, 1, 500, 8000):
        body = os.urandom(n)
        assert s.call(0x21, body) == body
    assert fresh.session_state(0) == 4  # S_AUTHED


def test_a_later_session_authenticates_with_the_kept_key(fresh):
    _, peer, ltk = paired(fresh)
    s = client.Session(Core(fresh), 'Pixel 8')
    assert s.hello(peer)['paired']
    s.auth(ltk)
    assert s.call(0x21, b'x') == b'x'


def test_wrong_proof_is_refused_and_closes(fresh):
    _, peer, ltk = paired(fresh)
    s = client.Session(Core(fresh), 'x')
    s.hello(peer)
    with pytest.raises(client.RemoteError) as e:
        s.auth(bytes(32))
    assert e.value.name == 'UNAUTHORIZED' and s.sock.closed


def test_an_unknown_peer_is_not_paired(fresh):
    s = client.Session(Core(fresh), 'x')
    assert not s.hello(os.urandom(8))['paired']
    with pytest.raises(client.RemoteError) as e:
        s.auth(os.urandom(32))
    assert e.value.name == 'UNAUTHORIZED'


def test_nothing_but_the_handshake_before_auth(fresh):
    s = client.Session(Core(fresh), 'x')
    with pytest.raises(client.RemoteError) as e:  # before Hello
        s.call(0x30, b'')
    assert e.value.name == 'BAD_REQUEST'
    s.hello()
    for t in (0x10, 0x12, 0x20, 0x30, 0x32, 0x40, 0x42, 0x50):
        with pytest.raises(client.RemoteError) as e:
            s.call(t, b'')
        assert e.value.name == 'UNAUTHORIZED'
    assert s.call(0x7e, b'ping') == b'ping'  # Ping, plain before Auth


def test_a_ping_before_auth_is_echoed_only_when_short(fresh):
    """An unpaired peer gets CRC_PING_PLAIN bytes echoed at most (over BLE the rest would be air time taken from
    A2DP); the session goes on. Sealed, a Ping takes a whole payload."""
    s = client.Session(Core(fresh), 'x')
    s.hello()
    assert s.call(0x7e, bytes(64)) == bytes(64)
    with pytest.raises(client.RemoteError) as e:
        s.call(0x7e, bytes(65))
    assert e.value.name == 'TOO_LARGE'
    assert s.call(0x7e, b'still') == b'still'
    s, _, ltk = paired(fresh)
    s.auth(ltk)
    assert s.call(0x7e, bytes(8000)) == bytes(8000)


def test_new_codes_are_put_up_four_a_minute_at_most(fresh):
    """Each new code raises the pairing card and lights the screen: a peer asking under new names gets four a
    minute, then E_BUSY until the oldest is a minute old. The same phone asking again keeps its code and is not
    counted."""
    s = client.Session(Core(fresh), 'x')
    s.hello()
    for i in range(4):
        s.name = f'phone {i}'
        s.pair_start()
    before = shown(fresh)
    s.name = 'phone 4'
    with pytest.raises(client.RemoteError) as e:
        s.pair_start()
    assert e.value.name == 'BUSY' and shown(fresh) == before
    s.name = 'phone 3'
    assert len(s.pair_start()) == 32 and shown(fresh) == before  # the same phone again: its code, uncounted
    var(fresh, 'now_ms', ctypes.c_uint32).value += 60001
    s.name = 'phone 5'
    s.pair_start()
    assert shown(fresh) != before


def test_wrong_code_fails_once_then_locks_after_three(fresh):
    for attempt in range(3):
        s = client.Session(Core(fresh), 'x')
        s.hello()
        point = s.pair_start()
        wrong = '%06d' % ((int(shown(fresh)) + 1) % 1000000)
        with pytest.raises(client.RemoteError) as e:
            s.pair_finish(wrong, point)
        assert e.value.name == 'PAIRING_FAILED' and var(fresh, 'hidden').value == 0
        assert not shown(fresh)  # the code is gone with the try
        with pytest.raises(ConnectionError):  # a second try out of turn ends the session
            s.pair_finish(wrong, point)
    s = client.Session(Core(fresh), 'x')
    s.hello()
    with pytest.raises(client.RemoteError) as e:
        s.pair_start()
    assert e.value.name == 'PAIRING_LOCKED'
    var(fresh, 'now_ms', ctypes.c_uint32).value += 300001
    s = client.Session(Core(fresh), 'x')
    s.hello()
    assert len(s.pair_start()) == 32


def qr(L, code):
    """Pair a Phone's QR code shows code (coda_rc.c crc_code_shown); '' for none."""
    (ctypes.c_char * 8).in_dll(L, 'qr_code').value = code.encode()


def test_a_phone_that_scanned_the_qr_code_pairs_on_its_code(fresh):
    """docs/remote-protocol.md#pairing-by-qr-code: the code under the QR code is the pairing's, not a new one, and it
    pairs once: the page then shows another."""
    qr(fresh, '042917')
    spent = var(fresh, 'spent').value
    s = client.Session(Core(fresh), 'Pixel 10 Pro')
    s.hello()
    point = s.pair_start()
    assert shown(fresh) == '042917'
    peer, ltk = s.pair_finish('042917', point)
    assert len(peer) == 8 and var(fresh, 'hidden').value == 1
    assert var(fresh, 'spent').value == spent + 1 and not (ctypes.c_char * 8).in_dll(fresh, 'qr_code').value
    qr(fresh, '')


def test_a_wrong_guess_spends_the_qr_code(fresh):
    """Someone who reached the Q2 without seeing its screen gets one guess at the code shown, as with the card's."""
    qr(fresh, '042917')
    spent = var(fresh, 'spent').value
    s = client.Session(Core(fresh), 'x')
    s.hello()
    point = s.pair_start()
    with pytest.raises(client.RemoteError) as e:
        s.pair_finish('042918', point)
    assert e.value.name == 'PAIRING_FAILED' and var(fresh, 'spent').value == spent + 1
    s = client.Session(Core(fresh), 'x')
    s.hello()
    s.pair_start()
    assert shown(fresh) != '042917' and len(shown(fresh)) == 6  # none shown now: a code of the card's own
    assert var(fresh, 'spent').value == spent + 1


def test_a_code_made_for_the_card_is_not_spent_as_the_qr_codes(fresh):
    spent = var(fresh, 'spent').value
    s, _, _ = paired(fresh)
    assert var(fresh, 'spent').value == spent


def test_one_pairing_at_a_time(fresh):
    a = client.Session(Core(fresh, 0), 'a')
    a.hello()
    a.pair_start()
    b = client.Session(Core(fresh, 1), 'b')
    b.hello()
    with pytest.raises(client.RemoteError) as e:
        b.pair_start()
    assert e.value.name == 'BUSY'
    fresh.crc_close(a.sock.s)  # the first phone goes: its code stays up for its return, but no longer holds the Q2
    assert var(fresh, 'hidden').value == -9
    assert len(b.pair_start()) == 32 and (ctypes.c_char * 32).in_dll(fresh, 'shown_name').value == b'b'


def test_a_phone_that_reconnects_keeps_its_code(fresh):
    """The Q2's log of a pairing that failed (2026-11-16): shown, ended, shown, wrong code. The phone's
    connection dropped and came back while the user typed; the code on screen must stay the one that works."""
    a = client.Session(Core(fresh, 0), 'Pixel 10 Pro')
    a.hello()
    a.pair_start()
    code = shown(fresh)
    fresh.crc_close(a.sock.s)
    assert var(fresh, 'hidden').value == -9 and shown(fresh) == code  # the Dialog stays up, its code too
    b = client.Session(Core(fresh, 1), 'Pixel 10 Pro')
    b.hello()
    point = b.pair_start()
    assert shown(fresh) == code
    peer, ltk = b.pair_finish(code, point)
    assert var(fresh, 'hidden').value == 1 and len(peer) == 8


def test_a_kept_code_still_lives_120_s_and_takes_one_guess(fresh):
    a = client.Session(Core(fresh, 0), 'Pixel 8')
    a.hello()
    a.pair_start()
    code = shown(fresh)
    fresh.crc_close(a.sock.s)
    var(fresh, 'now_ms', ctypes.c_uint32).value += 60000
    fresh.crc_tick()
    b = client.Session(Core(fresh, 1), 'Pixel 8')
    b.hello()
    point = b.pair_start()
    assert shown(fresh) == code
    wrong = '%06d' % ((int(code) + 1) % 1000000)
    with pytest.raises(client.RemoteError) as e:
        b.pair_finish(wrong, point)
    assert e.value.name == 'PAIRING_FAILED' and not shown(fresh)  # a wrong guess still ends the code
    c = client.Session(Core(fresh, 2), 'Pixel 8')
    c.hello()
    c.pair_start()
    code = shown(fresh)
    fresh.crc_close(c.sock.s)
    var(fresh, 'now_ms', ctypes.c_uint32).value += 120001  # 120 s from when it was first shown, whoever holds it
    fresh.crc_tick()
    assert var(fresh, 'hidden').value == -1 and not shown(fresh)
    d = client.Session(Core(fresh, 3), 'Pixel 8')
    d.hello()
    d.pair_start()
    assert shown(fresh) and shown(fresh) != code


def test_another_phone_gets_a_code_of_its_own(fresh):
    a = client.Session(Core(fresh, 0), 'Pixel 8')
    a.hello()
    a.pair_start()
    code = shown(fresh)
    b = client.Session(Core(fresh, 1), 'Car tablet')
    b.hello()
    with pytest.raises(client.RemoteError) as e:  # while the first is connected, one pairing at a time
        b.pair_start()
    assert e.value.name == 'BUSY'
    fresh.crc_close(a.sock.s)
    point = b.pair_start()
    assert (ctypes.c_char * 32).in_dll(fresh, 'shown_name').value == b'Car tablet' and shown(fresh) != code
    b.pair_finish(shown(fresh), point)


def test_code_expires_and_cancel_ends_it(fresh):
    s = client.Session(Core(fresh), 'x')
    s.hello()
    point = s.pair_start()
    code = shown(fresh)
    var(fresh, 'now_ms', ctypes.c_uint32).value += 120001
    fresh.crc_tick()
    assert var(fresh, 'hidden').value == -1
    with pytest.raises(client.RemoteError) as e:  # the push that ended it
        s.wait_push(1)
    assert e.value.name == 'PAIRING_FAILED'
    with pytest.raises(ConnectionError):
        s.pair_finish(code, point)
    s = client.Session(Core(fresh), 'x')
    s.hello()
    s.pair_start()
    var(fresh, 'cancelled').value = 1
    fresh.crc_tick()
    with pytest.raises(client.RemoteError) as e:
        s.wait_push(1)
    assert e.value.name == 'PAIRING_CANCELLED'


def test_full_device_refuses_a_ninth(fresh):
    for _ in range(8):
        paired(fresh)
        var(fresh, 'now_ms', ctypes.c_uint32).value += 15001  # four new codes a minute (CRC_PAIR_SHOWS)
    s = client.Session(Core(fresh), 'x')
    s.hello()
    with pytest.raises(client.RemoteError) as e:
        s.pair_start()
    assert e.value.name == 'PAIRING_FULL'


def test_low_order_point_fails(fresh):
    s = client.Session(Core(fresh), 'x')
    s.hello()
    s.pair_start()
    with pytest.raises(client.RemoteError) as e:  # u = 0 is of order 4: an all-zero shared secret
        s.call(0x05, client.encode([(1, bytes(32)), (2, bytes(32))]), 0x06)
    assert e.value.name == 'PAIRING_FAILED'


# ---- sealed frames that must not open

def authed(L):
    s, peer, ltk = paired(L)
    s.auth(ltk)
    return s


@pytest.mark.parametrize('tamper', ['payload', 'header', 'replay', 'plain', 'flags'])
def test_a_bad_sealed_frame_closes_the_session(fresh, tamper):
    s = authed(fresh)
    hdr = struct.pack('>HBBH', 4 + 3 + 16, 0x21, 1, 9)
    good = hdr + cc.seal(s.kc, cc.nonce(s.tx), hdr, b'abc')
    if tamper == 'payload':
        frame = good[:7] + bytes([good[7] ^ 1]) + good[8:]
    elif tamper == 'header':
        frame = good[:5] + b'\x0a' + good[6:]  # another req_id: the header is authenticated
    elif tamper == 'replay':
        s.sock.sendall(good)
        s.frame()
        frame = good
    elif tamper == 'plain':
        frame = struct.pack('>HBBH', 4 + 3, 0x21, 0, 9) + b'abc'
    else:
        frame = good[:3] + b'\x03' + good[4:]
    s.sock.sendall(frame)
    assert s.sock.closed


def test_lengths_out_of_range_close(fresh):
    for frame in (b'\x00\x03\x01\x00\x00', b'\x20\x15'):  # below 4, above 2 + 8212
        c = Core(fresh)
        c.sendall(frame)
        assert c.closed


def test_frames_split_anywhere_reassemble(fresh):
    """BLE's ATT PDUs cut the stream at any byte: one byte at a time still works."""
    class Trickle(Core):
        def sendall(self, b):
            for i in range(len(b)):
                super().sendall(b[i:i + 1])
    s = client.Session(Trickle(fresh), 'x')
    s.hello()
    point = s.pair_start()
    peer, ltk = s.pair_finish(shown(fresh), point)
    s.auth(ltk)
    assert s.call(0x21, b'hello') == b'hello'


def test_app_error_closes(fresh):
    s = authed(fresh)
    s.send(0x70)
    assert s.sock.closed


def test_save_failure_is_internal_and_keeps_nothing(fresh):
    var(fresh, 'save_fails').value = 1
    s = client.Session(Core(fresh), 'x')
    s.hello()
    point = s.pair_start()
    with pytest.raises(client.RemoteError) as e:
        s.pair_finish(shown(fresh), point)
    assert e.value.name == 'INTERNAL' and var(fresh, 'peers_n').value == 0


def test_spec_frame_types_match_the_header():
    """remote/coda-rc.proto's table, coda_rc.h's enum and the client's T name the same numbers."""
    proto = (ROOT / 'remote/coda-rc.proto').read_text()
    header = (PATCH / 'coda_rc.h').read_text()
    import re
    for name, v in client.T.items():
        assert re.search(rf'0x{v:02x} [A-Z]', proto), name
    enum = header[header.index('enum {\n    T_HELLO'):]
    enum = enum[:enum.index('};')]
    n, seen = 0, {}
    for tok in re.findall(r'T_(\w+)(?: = (0x[0-9a-f]+))?', enum):
        n = int(tok[1], 16) if tok[1] else n + 1
        seen[tok[0]] = n
    assert seen == client.T


# ---- the client against a Q2 written here: its handshake from tools/coda_rc_crypto.py, canned answers

class FakeQ2:
    """A socket-like Q2: Hello, pairing (the code is CODE) and Auth as coda_rc_core.c answers them, then each
    request's answer from answers[type], a function of the request's fields giving [(type, req_id, payload)]."""
    CODE, DEVICE_ID = '135790', bytes(range(8))

    def __init__(self, answers=None, keys=None):
        self.answers, self.keys = answers or {}, keys if keys is not None else {}
        self.inbox, self.outbox, self.kc = b'', b'', None
        self.tx = self.rx = 0
        self.requests, self.timeouts, self.closed = [], [], False

    def settimeout(self, t):
        self.timeouts.append(t)

    def close(self):
        self.closed = True

    def recv(self, n):
        out, self.outbox = self.outbox[:n], self.outbox[n:]
        return out

    def reply(self, type_, req, payload=b''):
        sealed = self.kc is not None
        hdr = struct.pack('>HBBH', len(payload) + 4 + 16 * sealed, type_, int(sealed), req)
        if sealed:
            payload = cc.seal(self.kd, cc.nonce(self.tx), hdr, payload)
            self.tx += 1
        self.outbox += hdr + payload

    def sendall(self, b):
        self.inbox += b
        while len(self.inbox) >= 2 and len(self.inbox) >= 2 + struct.unpack('>H', self.inbox[:2])[0]:
            n = 2 + struct.unpack('>H', self.inbox[:2])[0]
            raw, self.inbox = self.inbox[:n], self.inbox[n:]
            _, t, flags, req = struct.unpack('>HBBH', raw[:6])
            p = raw[6:]
            if flags & 1:
                p = cc.open_(self.kc, cc.nonce(self.rx), raw[:6], p)
                self.rx += 1
            self.handle(t, req, client.decode(p))

    def handle(self, t, req, m):
        T = client.T
        if t in self.answers:
            self.requests.append((t, m))
            for answer in self.answers[t](m):
                self.reply(answer[0], req if answer[1] is None else answer[1], answer[2])
        elif t == T['HELLO']:
            self.sid = client.one(m, 3) + bytes(16)
            peer = client.one(m, 4)
            self.reply(T['HELLO_RESP'], req, client.encode([(1, 1), (3, bytes(16)), (4, peer in self.keys), (5, 3),
                                                            (6, 'Shanling Q2'), (7, self.DEVICE_ID)]))
            self.peer = peer
        elif t == T['PAIR_START']:
            self.b = bytes([9] * 32)
            self.point = cc.x25519(self.b, cc.pake_generator(self.CODE, self.sid))
            self.reply(T['PAIR_CHALLENGE'], req, client.encode([(1, self.point)]))
        elif t == T['PAIR_FINISH']:
            cp = client.one(m, 1)
            prk = cc.pair_prk(cc.x25519(self.b, cp), self.sid, cp, self.point)
            if client.one(m, 2) != cc.client_confirm(prk):
                return self.reply(T['ERROR'], req, client.encode([(1, 6), (2, 'wrong code')]))
            self.peer = bytes([7] * 8)
            self.keys[self.peer] = cc.long_term_key(prk)
            self.reply(T['PAIR_RESULT'], req, client.encode([(1, self.peer), (2, cc.device_confirm(prk, self.peer))]))
        elif t == T['AUTH']:
            ltk = self.keys[self.peer]
            proof_c, proof_d = cc.auth_proofs(ltk, self.sid)
            assert client.one(m, 1) == proof_c
            self.reply(T['AUTH_RESP'], req, client.encode([(1, proof_d)]))
            self.kc, self.kd = cc.session_keys(ltk, self.sid)
        else:
            raise AssertionError(f'no answer for {t:#x}')


def item(id_, title, **kw):
    return client.encode([(1, id_), (2, title), (3, kw.get('subtitle')), (4, kw.get('art')), (5, kw.get('icon')),
                          (6, kw.get('browsable')), (7, kw.get('playable')), (8, kw.get('group')),
                          (9, kw.get('duration')), (10, kw.get('number'))])


NOW = client.encode([(1, client.encode([(1, 's:12'), (2, 'Song'), (3, 'Artist'), (4, 'Album'), (5, 200000),
                                       (6, 'a:3'), (7, 1), (8, 1), (9, 2), (10, 40), (11, 3), (12, 1)])),
                     (2, client.encode([(1, 30000), (2, 99)])), (3, client.encode([(1, 4), (2, 2), (3, 9)]))])
ACK = lambda m: [(0x21, None, b'')]  # noqa: E731
COVER = b'\xff\xd8' + bytes(range(256)) * 20 + b'\xff\xd9'


def cover_answers():
    """A cover in 4096-byte parts whose etag changes after the first, so the fetch starts over once."""
    etags = iter([1] + [2] * 9)

    def answer(m):
        at = client.one(m, 2, 0)
        return [(0x51, None, client.encode([(1, len(COVER)), (2, at), (3, COVER[at:at + 4096]), (4, 'image/jpeg'),
                                            (5, next(etags))]))]
    return answer


ANSWERS = {
    0x10: ACK, 0x20: ACK, 0x22: ACK, 0x24: ACK, 0x40: ACK, 0x44: ACK, 0x46: ACK,
    0x60: lambda m: [(0x61, None, client.encode([(1, 'ab' * 32), (2, 'https://use1-1.relay.n0.iroh.link./')]))],
    0x12: lambda m: [(0x7F, 0, b''), (0x11, None, NOW)],  # a push first: kept, then the answer
    0x30: lambda m: [(0x31, None, client.encode([(1, client.text(m, 1)), (2, 'Albums'), (3, 2), (4, 0),
                                                 (5, item('al:1', 'One', subtitle='A', art='a:1', browsable=1,
                                                          playable=1, group='O')),
                                                 (5, item('s:1', 'Two', icon=2, duration=1000, number=3, playable=1)),
                                                 (6, 'name'), (7, client.encode([(1, 'name'), (2, 'Name')])),
                                                 (8, 1), (8, 2), (8, 3), (8, 5)]))],
    0x32: lambda m: [(0x33, None, client.encode([(1, client.encode([(1, 'Songs'), (2, item('s:1', 'Two')), (3, 1)]))]))],
    0x42: lambda m: [(0x43, None, client.encode([(1, 4), (2, 2), (3, 9), (4, client.one(m, 1, 0)),
                                                 (5, item('s:5', 'Five'))]))],
    0x50: None,
}


@pytest.fixture
def q2(monkeypatch, tmp_path):
    """create_connection answers FakeQ2s sharing one key store; the client's keys go to tmp_path."""
    made, keys = [], {}

    def connect(addr, timeout=None):
        assert addr == ('127.0.0.1', 7720) and timeout == 10
        made.append(FakeQ2({**ANSWERS, 0x50: cover_answers()}, keys))
        return made[-1]
    monkeypatch.setattr(client.socket, 'create_connection', connect)
    monkeypatch.setenv('CODA_RC_KEYS', str(tmp_path / 'keys'))
    return made


def run(capsys, *argv):
    assert client.main(['--code', FakeQ2.CODE, *argv]) == 0
    out = capsys.readouterr().out
    return __import__('json').loads(out) if out.startswith(('{', '[')) else out


def test_client_pairs_once_then_authenticates_with_the_kept_key(q2, capsys, tmp_path):
    out = run(capsys, 'pair')
    assert out['paired'] and out['model'] == 'Shanling Q2' and out['device_id'] == '0001020304050607'
    kept = tmp_path / 'keys' / '0001020304050607.json'
    assert kept.stat().st_mode & 0o777 == 0o600 and (tmp_path / 'keys').stat().st_mode & 0o777 == 0o700
    assert len(q2) == 1
    assert run(capsys, 'state')['now_playing']['title'] == 'Song'
    assert len(q2) == 3 and q2[1].closed and q2[2].requests[0][0] == 0x12  # Hello, then again with the key


def test_client_pairs_again_when_the_q2_forgot_it(q2, capsys, tmp_path):
    run(capsys, 'pair')
    q2[0].keys.clear()
    assert run(capsys, 'queue', '--offset', '3')['items'][0]['title'] == 'Five'
    assert len(q2) == 3 and q2[2].keys  # the second connection paired


def test_client_asks_for_the_code(q2):
    asked = []
    s = client.connect('127.0.0.1', 7720, client.key_dir(None), ask=lambda prompt: asked.append(prompt) or '135 790')
    assert asked == ['Code shown on the Q2: '] and s.kc is not None


def test_client_commands(q2, capsys, tmp_path):
    run(capsys, 'pair')
    st = run(capsys, 'state')
    assert st == {'now_playing': {'track_id': 's:12', 'title': 'Song', 'artist': 'Artist', 'album': 'Album',
                                  'duration_ms': 200000, 'art': 'a:3', 'play_state': 1, 'shuffle': 1, 'repeat': 2,
                                  'volume': 40, 'hearts': 3, 'rateable': True, 'buffering': False, 'buffered_bytes': 0,
                                  'total_bytes': 0, 'buffered_ms': 0, 'radio': False, 'notice': '', 'notice_id': 0,
                                  'album_id': '', 'artist_id': ''},
                  'position': {'position_ms': 30000, 'at_ms': 99}, 'queue': {'version': 4, 'index': 2, 'total': 9}}
    b = run(capsys, 'browse', 'albums', '--sort', 'name', '--limit', '2')
    assert b['items'][0] == {'id': 'al:1', 'title': 'One', 'subtitle': 'A', 'art': 'a:1', 'group': 'O',
                             'browsable': True, 'playable': True}
    assert b['items'][1] == {'id': 's:1', 'title': 'Two', 'icon': 2, 'duration_ms': 1000, 'number': 3,
                             'browsable': False, 'playable': True}
    assert b['sorts'] == [{'key': 'name', 'label': 'Name'}] and b['actions'] == ['play', 'shuffle', 'resume', 5]
    assert q2[-1].requests[0][1] == {1: [b'albums'], 2: [b'name'], 3: [0], 4: [2]}
    assert run(capsys, 'browse')['node'] == 'root'
    assert run(capsys, 'search', 'two', 'words')[0]['items'][0]['id'] == 's:1'
    assert client.text(q2[-1].requests[0][1], 1) == 'two words'
    assert run(capsys, 'play', 'al:1', '--start', 's:1', '--shuffle') == {'ok': True}
    assert q2[-1].requests[0][1] == {1: [b'al:1'], 3: [b's:1'], 4: [1]}
    assert run(capsys, 'play', 'pl:1', '--sort', 'order', '--resume') == {'ok': True}
    assert q2[-1].requests[0][1] == {1: [b'pl:1'], 2: [b'order'], 4: [0], 5: [1]}
    for argv, fields in ((('key', 'next'), {1: [4]}), (('seek', '75.5'), {1: [6], 2: [75500]}),
                         (('volume', '30'), {1: [9], 3: [30]}), (('shuffle', 'off'), {1: [1], 2: [0]}),
                         (('repeat', 'all'), {3: [1], 4: [2]}), (('rate', '5', 's:12'), {1: [b's:12'], 2: [5]}),
                         (('jump', '3', 's:5'), {1: [3], 2: [b's:5']})):
        assert run(capsys, *argv) == {'ok': True} and q2[-1].requests[0][1] == fields
    out = tmp_path / 'cover.jpg'
    assert run(capsys, 'cover', 'a:3', str(out), '--px', '80') == {'bytes': len(COVER), 'mime': 'image/jpeg'}
    assert out.read_bytes() == COVER
    assert [client.one(m, 2) for _, m in q2[-1].requests] == [0, 4096, 0, 4096]  # the etag changed: again
    with pytest.raises(SystemExit):
        run(capsys, 'nonsense')
    with pytest.raises(SystemExit):
        run(capsys, 'repeat', 'sometimes')


def test_client_radio_and_remote(q2, capsys):
    """StartRadio from the playing song, from a song and stopped (CAP_RADIO); GetRemote's endpoint (CAP_REMOTE)."""
    run(capsys, 'pair')
    for argv, fields in ((('radio',), {}), (('radio', 'song:t12'), {1: [b'song:t12']}), (('radio', 'stop'), {2: [1]})):
        assert run(capsys, *argv) == {'ok': True} and q2[-1].requests[0] == (0x46, fields)
    assert run(capsys, 'remote') == {'endpoint_id': 'ab' * 32, 'relay_url': 'https://use1-1.relay.n0.iroh.link./'}


def test_client_watch_prints_pushes_until_its_time(q2, capsys, monkeypatch):
    run(capsys, 'pair')
    clock = iter([0, 0, 5, 5, 11, 11, 99])
    monkeypatch.setattr(client.time, 'monotonic', lambda: next(clock))
    pushes = iter([{'queue': {}}, TimeoutError()])

    def wait_push(self, timeout):
        p = next(pushes)
        if isinstance(p, Exception):
            raise p
        return p
    monkeypatch.setattr(client.Session, 'wait_push', wait_push)
    assert client.main(['watch', '10']) == 0
    assert capsys.readouterr().out == '{"queue": {}}\n'
    assert q2[-1].requests == [(0x10, {1: [1]})]


def test_client_frame_prints_a_hello():
    import io
    import contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        client.main(['frame', 'hello'])
    raw = bytes.fromhex(out.getvalue())
    assert raw[2:6] == b'\x01\x00\x00\x01' and struct.unpack('>H', raw[:2])[0] == len(raw) - 2
    assert client.text(client.decode(raw[6:]), 2) == 'coda_rc_client.py'


def plain(answers):
    """A Session on an unauthenticated FakeQ2 that answers anything through answers."""
    return client.Session(FakeQ2(answers))


def test_session_keeps_pushes_skips_strays_and_raises_errors():
    s = plain({0x60: lambda m: [(0x11, 0, NOW), (0x21, 999, b''), (0x21, None, b'ok')],
               0x61: lambda m: [(0x0F, 0, client.encode([(1, 12), (2, 'cancelled')]))],
               0x62: lambda m: [(0x0F, None, client.encode([(1, 4), (2, 'no such node')]))],
               0x63: lambda m: [(0x31, None, b'')],
               0x64: lambda m: [(0x7F, 0, b''), (0x11, 0, NOW), (0x0F, 0, client.encode([(1, 99)]))]})
    assert s.call(0x60, b'', 0x21) == b'ok'
    assert s.wait_push()['queue']['total'] == 9  # kept from the call
    with pytest.raises(client.RemoteError) as e:
        s.call(0x61)
    assert e.value.name == 'PAIRING_CANCELLED' and str(e.value) == 'PAIRING_CANCELLED: cancelled'
    with pytest.raises(client.RemoteError) as e:
        s.call(0x62)
    assert e.value.code == 4
    with pytest.raises(ValueError, match='answer type'):
        s.call(0x63, want=0x21)
    s.req = 65535
    assert s.send(0x64) == 1  # request ids wrap past 65535, never 0
    assert s.wait_push(5)['position']['at_ms'] == 99  # the Pong push before it passed over
    with pytest.raises(client.RemoteError) as e:
        s.wait_push(5)
    assert e.value.name == '99'
    s.pushes.append((0x7F, b''))
    assert s.wait_push() == {'type': 0x7F}


def test_wait_push_times_out(monkeypatch):
    s = plain({})
    clock = iter([0, 11])
    monkeypatch.setattr(client.time, 'monotonic', lambda: next(clock))
    with pytest.raises(TimeoutError):
        s.wait_push(10)


@pytest.mark.parametrize('case', ['sealed before auth', 'plain after auth', 'does not open', 'closed'])
def test_frames_the_client_refuses(case):
    s = plain({})
    hdr = struct.pack('>HBBH', 4 + 16, 0x11, 1, 0)
    if case == 'sealed before auth':
        s.sock.outbox = hdr + bytes(16)
    elif case == 'plain after auth':
        s.kc = s.kd = bytes(32)
        s.sock.outbox = struct.pack('>HBBH', 4, 0x11, 0, 0)
    elif case == 'does not open':
        s.kc = s.kd = bytes(32)
        s.sock.outbox = hdr + bytes(16)
    with pytest.raises(ConnectionError if case == 'closed' else ValueError):
        s.frame()


def test_pairing_checks_the_q2():
    s = plain({})
    s.hello()
    s.pair_start()
    with pytest.raises(ValueError, match='low-order'):
        s.pair_finish('000000', bytes(32))
    # a Q2 that does not know the code: its confirmation is not the code's
    s.sock.answers[0x05] = lambda m: [(0x06, None, client.encode([(1, bytes(8)), (2, bytes(32))]))]
    with pytest.raises(ValueError, match='confirmation'):
        s.pair_finish('000000', cc.x25519(bytes([5] * 32), cc.pake_generator('000000', s.sid)))
    s.sock.answers[0x07] = lambda m: [(0x08, None, client.encode([(1, bytes(32))]))]
    with pytest.raises(ValueError, match='proof'):
        s.auth(bytes(32))


def test_decode_refuses_and_reads_fixed_widths():
    assert client.decode(b'\x09' + bytes(8) + b'\x15' + bytes(4)) == {1: [bytes(8)], 2: [bytes(4)]}
    for bad in (b'\x0b', b'\x08\x80', b'\x08' + b'\xff' * 10 + b'\x01'):
        with pytest.raises(ValueError):
            client.decode(bad)
    assert client.text({1: [5]}, 1) == '' and client.encode([(1, None)]) == b''


def test_load_key_without_one(tmp_path):
    assert client.load_key(tmp_path, 'x') is None
    (tmp_path / 'x.json').write_text('{}')
    assert client.load_key(tmp_path, 'x') is None
    assert client.key_dir('/k') == pathlib.Path('/k')


def test_open_refuses_a_short_frame():
    assert cc.open_(bytes(32), cc.nonce(0), b'', bytes(15)) is None


def test_frames_arriving_a_byte_at_a_time_and_a_bare_state():
    class Trickle(FakeQ2):
        def recv(self, n):
            return super().recv(1)
    s = client.Session(Trickle({0x12: lambda m: [(0x11, None, b'')]}))
    assert s.send(0x12, req_id=40) == 40 and s.frame() == (0x11, 40, b'')
    assert s.req == 0  # an explicit id leaves the counter alone
    assert client.state(b'') == {}


@pytest.mark.parametrize('have,total,ms,want', [
    (7_000_000, 20_000_000, 780_000, 273_000),     # 7.0 of 20 MB of a 13-minute song
    (0, 20_000_000, 780_000, 0), (20_000_000, 20_000_000, 780_000, 780_000),
    (4_000_000_000, 4_294_967_295, 36_000_000, 33_528_880),  # past 32 bits in the product: no overflow
    (5, 0, 1000, 0), (5, 10, 0, 0), (30, 10, 1000, 1000),    # no total or length: 0; never past the length
])
def test_buffered_ms_is_the_bytes_share_of_the_length(lib, have, total, ms, want):
    f = lib.crc_buffered_ms
    f.restype, f.argtypes = ctypes.c_uint32, [ctypes.c_uint32] * 3
    assert abs(f(have, total, ms) - want) <= max(2, want // 10000)


def test_client_reads_the_buffering_fields():
    raw = client.encode([(1, client.encode([(2, 'Song'), (13, 1), (14, 7_000_000), (15, 20_000_000), (16, 273_000)]))])
    np = client.state(raw)['now_playing']
    assert (np['buffering'], np['buffered_bytes'], np['total_bytes'], np['buffered_ms']) == (True, 7_000_000, 20_000_000, 273_000)
    assert client.state(client.encode([(1, client.encode([(2, 'Song')]))]))['now_playing']['buffering'] is False
