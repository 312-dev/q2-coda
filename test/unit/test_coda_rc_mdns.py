"""coda-rc's mDNS responder (patch/coda_rc_mdns.c, docs/remote-protocol.md#discovery), built for this machine with
test/unit/coda_rc_mdns_host.c as the daemon around it.

The packet half is checked against packets built here with the standard library: the announcement's records read back
as a DNS-SD browser reads them (PTR, SRV, TXT, A for _q2muse._tcp.local), the queries answered and the ones that are
not, known-answer suppression, the goodbye's zero TTLs, and malformed packets (truncations at every byte, compression
loops, forward pointers, random and mutated bytes) refused. Each packet ends at the end of a page whose next page
cannot be read (`guarded`), so a read past its end kills the test run rather than passing unseen. The daemon half runs on the loopback interface, when this machine
lets a socket join 224.0.0.251 there: the announcement on start, a query answered, the goodbye on stop."""
import ctypes
import mmap
import pathlib
import random
import select
import socket
import struct
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc_mdns.c', ROOT / 'test/unit/coda_rc_mdns_host.c']
MD_PTR, MD_SRV, MD_TXT, MD_A, MD_ENUM = 1, 2, 4, 8, 16
PTR, TXT, SRV, A, AAAA, ANY = 12, 16, 33, 1, 28, 255
TTL = 120
DEVICE_ID = bytes([0x1a, 0x2b, 0x3c, 0x4d, 0x5e, 0x6f, 0x70, 0x81])
SVC = '_q2muse._tcp.local'
INST = 'Vamp._q2muse._tcp.local'
HOST = 'q2muse-1a2b3c.local'
GROUP = '224.0.0.251'


class Self(ctypes.Structure):
    _fields_ = [('ip', ctypes.c_uint8 * 4), ('id', ctypes.c_uint8 * 8), ('port', ctypes.c_uint16),
                ('name', ctypes.c_char * 64), ('host', ctypes.c_char * 64)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    so = tmp_path_factory.mktemp('coda_rc_mdns') / 'mdns.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-I', str(PATCH), *map(str, SOURCES), '-o', str(so)], check=True)
    L = ctypes.CDLL(str(so))
    L.mdns_query.argtypes = [ctypes.POINTER(Self), ctypes.c_void_p, ctypes.c_size_t]
    L.mdns_reply.argtypes = [ctypes.POINTER(Self), ctypes.c_uint, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
    L.mdns_reply.restype = ctypes.c_size_t
    L.mdns_start.argtypes = [ctypes.c_char_p]
    L.mdns_poll.argtypes = [ctypes.c_uint32]
    return L


def me(name=b'Vamp', ip=(192, 168, 1, 23)):
    s = Self()
    s.ip[:] = ip
    s.id[:] = DEVICE_ID
    s.port = 7720
    s.name = name
    s.host = b'q2muse-1a2b3c'
    return s


# ---- DNS in Python: enough to build queries and read answers ----

def wire(name):
    return b''.join(bytes([len(label)]) + label.encode() for label in name.split('.')) + b'\0'


def query(*questions, answers=(), flags=0, qid=0):
    """A query: questions (name, type[, class]); answers (name, type, ttl, rdata) as known answers."""
    out = struct.pack('>HHHHHH', qid, flags, len(questions), len(answers), 0, 0)
    for q in questions:
        out += wire(q[0]) + struct.pack('>HH', q[1], q[2] if len(q) > 2 else 1)
    for name, t, ttl, rd in answers:
        out += wire(name) + struct.pack('>HHIH', t, 1, ttl, len(rd)) + rd
    return out


def read_name(p, at):
    labels, jumped, end = [], False, at
    while True:
        n = p[at]
        if n & 0xc0 == 0xc0:
            if not jumped:
                end = at + 2
            at, jumped = (n & 0x3f) << 8 | p[at + 1], True
            continue
        if n == 0:
            return '.'.join(labels), (end if jumped else at + 1)
        labels.append(p[at + 1:at + 1 + n].decode())
        at += 1 + n


def parse(p):
    """A response's flags and its records, answers then additionals: (section, name, type, class, ttl, value)."""
    _, flags, qd, an, ns, ar = struct.unpack_from('>HHHHHH', p)
    assert qd == 0 and ns == 0
    at, out = 12, []
    for i in range(an + ar):
        name, at = read_name(p, at)
        t, cls, ttl, rdlen = struct.unpack_from('>HHIH', p, at)
        at += 10
        rd = p[at:at + rdlen]
        if t == PTR:
            value = read_name(p, at)[0]
        elif t == SRV:
            value = struct.unpack_from('>HHH', rd) + (read_name(p, at + 6)[0],)
        elif t == TXT:
            value, k = [], 0
            while k < len(rd):
                value.append(rd[k + 1:k + 1 + rd[k]].decode())
                k += 1 + rd[k]
        else:
            value = socket.inet_ntoa(rd)
        out.append(('an' if i < an else 'ar', name, t, cls, ttl, value))
        at += rdlen
    assert at == len(p)
    return flags, out


PAGE = mmap.PAGESIZE
_guard = mmap.mmap(-1, 2 * PAGE)
_GUARD_AT = ctypes.addressof(ctypes.c_char.from_buffer(_guard))
_libc = ctypes.CDLL(None, use_errno=True)
_libc.mprotect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
assert _libc.mprotect(_GUARD_AT + PAGE, PAGE, 0) == 0  # PROT_NONE: a read there is SIGSEGV


def guarded(packet):
    """packet's bytes ending where the unreadable page starts"""
    assert len(packet) <= PAGE
    at = _GUARD_AT + PAGE - len(packet)
    ctypes.memmove(at, packet, len(packet))
    return at


def ask(lib, packet, s=None):
    return lib.mdns_query(ctypes.byref(s or me()), ctypes.c_void_p(guarded(packet)), len(packet))


def reply(lib, answers, ttl=TTL, s=None, cap=1500):
    buf = ctypes.create_string_buffer(cap)
    n = lib.mdns_reply(ctypes.byref(s or me()), answers, ttl, buf, cap)
    return buf.raw[:n]


# ---- the packet half ----

def test_announcement_is_what_a_browser_needs(lib):
    flags, recs = parse(reply(lib, MD_PTR | MD_SRV | MD_TXT | MD_A))
    assert flags == 0x8400  # a response, authoritative
    assert recs == [
        ('an', SVC, PTR, 1, TTL, INST),
        ('an', INST, SRV, 0x8001, TTL, (0, 0, 7720, HOST)),
        ('an', INST, TXT, 0x8001, TTL, ['id=1a2b3c4d5e6f7081', 'v=1']),
        ('an', HOST, A, 0x8001, TTL, '192.168.1.23'),
    ]


def test_goodbye_has_ttl_zero(lib):
    _, recs = parse(reply(lib, MD_PTR | MD_SRV | MD_TXT | MD_A, ttl=0))
    assert len(recs) == 4 and all(r[4] == 0 for r in recs)


def test_ptr_answer_carries_srv_txt_a(lib):
    _, recs = parse(reply(lib, MD_PTR))
    assert [(r[0], r[2]) for r in recs] == [('an', PTR), ('ar', SRV), ('ar', TXT), ('ar', A)]
    _, recs = parse(reply(lib, MD_SRV))
    assert [(r[0], r[2]) for r in recs] == [('an', SRV), ('ar', A)]
    _, recs = parse(reply(lib, MD_ENUM))
    assert recs == [('an', '_services._dns-sd._udp.local', PTR, 1, TTL, SVC)]


def test_renamed_q2_answers_under_its_name(lib):
    s = me(name=b'Car Q2')
    _, recs = parse(reply(lib, MD_PTR, s=s))
    assert recs[0][5] == 'Car Q2._q2muse._tcp.local'
    assert ask(lib, query(('Car Q2._q2muse._tcp.local', SRV)), s) == MD_SRV
    assert ask(lib, query((INST, SRV)), s) == 0


@pytest.mark.parametrize('config, name', [
    (None, 'Vamp'),  # no name chosen: the default
    ('[Q2POD]\nBTNAME=Vamp\n', 'Vamp'),  # the name builds before Vamp gave it, chosen
])
def test_any_app_finds_it_by_the_service_type_whatever_its_name(lib, tmp_path, config, name):
    """The phone app browses _q2muse._tcp (WifiFinder.SERVICE, the same in every release of the app), keys a Q2 by
    its TXT id and lists the instance name without matching it, so an app from before the name Vamp finds a Q2 that
    says Vamp and the Vamp app finds one that says Vamp. The name is config.ini's BTNAME, else the default."""
    (tmp_path / 'mnt/data').mkdir(parents=True)
    if config:
        (tmp_path / 'mnt/data/config.ini').write_text(config)
    ctypes.memmove(ctypes.addressof(ctypes.c_char.in_dll(lib, 'root')), str(tmp_path).encode() + b'\0',
                   len(str(tmp_path)) + 1)
    out = ctypes.create_string_buffer(64)
    lib.crc_bt_name(out)
    assert out.value == name.encode()
    _, recs = parse(reply(lib, MD_PTR, s=me(name=out.value)))
    inst = f'{name}._q2muse._tcp.local'
    assert recs[0] == ('an', '_q2muse._tcp.local', PTR, 1, TTL, inst)
    assert recs[2][1:3] == (inst, TXT) and recs[2][5] == ['id=1a2b3c4d5e6f7081', 'v=1']


@pytest.mark.parametrize('q, want', [
    ((SVC, PTR), MD_PTR),
    (('_Q2MUSE._TCP.LOCAL', PTR), MD_PTR),  # names in any case
    ((SVC, ANY), MD_PTR),
    ((SVC, PTR, 0x8001), MD_PTR),  # the unicast-response bit: answered (by multicast)
    ((INST, SRV), MD_SRV),
    ((INST, TXT), MD_TXT),
    ((INST, ANY), MD_SRV | MD_TXT),
    ((HOST, A), MD_A),
    ((HOST, AAAA), 0),  # no IPv6
    (('_services._dns-sd._udp.local', PTR), MD_ENUM),
    (('_airplay._tcp.local', PTR), 0),  # only our service
    (('_raop._tcp.local', PTR), 0),
    (('ingenic.local', A), 0),
    ((SVC, PTR, 3), 0),  # class CH
    ((SVC, SRV), 0),
])
def test_queries(lib, q, want):
    assert ask(lib, query(q)) == want


def test_several_questions(lib):
    assert ask(lib, query((SVC, PTR), (HOST, A), ('_x._tcp.local', PTR))) == MD_PTR | MD_A


def test_responses_and_other_opcodes_are_not_answered(lib):
    assert ask(lib, query((SVC, PTR), flags=0x8400)) == 0
    assert ask(lib, query((SVC, PTR), flags=0x2800)) == 0  # UPDATE


def test_known_answer_suppression(lib):
    held = (SVC, PTR, TTL, wire(INST))
    assert ask(lib, query((SVC, PTR), answers=[held])) == 0
    assert ask(lib, query((SVC, PTR), answers=[(SVC, PTR, TTL // 2 - 1, wire(INST))])) == MD_PTR  # nearly expired
    assert ask(lib, query((SVC, PTR), answers=[(SVC, PTR, TTL, wire('Other._q2muse._tcp.local'))])) == MD_PTR
    assert ask(lib, query((SVC, PTR), (HOST, A), answers=[held])) == MD_A


def test_compressed_names(lib):
    # the question names the service type, the second one points back into it
    p = struct.pack('>HHHHHH', 0, 0, 2, 0, 0, 0) + wire(SVC) + struct.pack('>HH', PTR, 1)
    p += b'\x04Vamp\xc0\x0c' + struct.pack('>HH', SRV, 1)
    assert ask(lib, p) == MD_PTR | MD_SRV


def test_pointer_loops_and_forward_pointers_are_malformed(lib):
    head = struct.pack('>HHHHHH', 0, 0, 1, 0, 0, 0)
    assert ask(lib, head + b'\xc0\x0c' + struct.pack('>HH', PTR, 1)) == -1  # points at itself
    assert ask(lib, head + b'\xc0\x20' + struct.pack('>HH', PTR, 1) + b'\0' * 32) == -1  # forward
    assert ask(lib, head + b'\x03abc\xc0\x0c' + struct.pack('>HH', PTR, 1)) == -1  # back to its own start: a loop
    assert ask(lib, head + b'\x40abc\0' + struct.pack('>HH', PTR, 1)) == -1  # reserved label type
    long_name = b''.join(b'\x3f' + b'a' * 63 for _ in range(5)) + b'\0'  # 321 bytes: over 255
    assert ask(lib, head + long_name + struct.pack('>HH', PTR, 1)) == -1


def test_truncated_at_every_byte(lib):
    full = query((SVC, PTR), (HOST, A), answers=[(SVC, PTR, TTL, wire(INST))])
    assert ask(lib, full) == MD_A
    for n in range(len(full)):
        assert ask(lib, full[:n]) in (-1, 0), n


def test_counts_past_the_packet(lib):
    p = bytearray(query((SVC, PTR)))
    p[4:6] = b'\xff\xff'
    assert ask(lib, bytes(p)) == -1
    p = bytearray(query((SVC, PTR)))
    p[6:8] = b'\x00\x05'
    assert ask(lib, bytes(p)) == -1
    big_rdlen = query((SVC, PTR), answers=[(SVC, PTR, TTL, wire(INST))])[:-len(wire(INST)) - 2] + b'\xff\xff'
    assert ask(lib, big_rdlen) == -1


def test_random_and_mutated_packets(lib):
    rng = random.Random(7720)
    seeds = [query((SVC, PTR)), query((INST, ANY), (HOST, A), answers=[(SVC, PTR, TTL, wire(INST))]),
             reply(lib, MD_PTR | MD_SRV | MD_TXT | MD_A)]
    for i in range(20000):
        if i % 4 == 0:
            p = bytes(rng.randrange(256) for _ in range(rng.randrange(64)))
        else:
            p = bytearray(rng.choice(seeds))
            for _ in range(rng.randrange(1, 6)):
                p[rng.randrange(len(p))] = rng.randrange(256)
            p = bytes(p[:rng.randrange(len(p) + 1)] if rng.random() < 0.3 else p)
        assert ask(lib, p) in range(-1, 32)


def test_reply_too_big_for_its_buffer(lib):
    assert reply(lib, MD_PTR | MD_SRV | MD_TXT | MD_A, cap=40) == b''
    assert reply(lib, 0) == b''


def test_name_too_long_for_a_label_answers_nothing(lib):
    s = me(name=b'x' * 63 + b'y')
    assert reply(lib, MD_PTR, s=s) == b''
    assert ask(lib, query((SVC, PTR)), s) == 0


# ---- the daemon half, on loopback ----

def listener():
    """A socket in 224.0.0.251:5353 on loopback, as a browser on the same network would be; None when this machine
    cannot do that."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('', 5353))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(GROUP) + socket.inet_aton('127.0.0.1'))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton('127.0.0.1'))
    except OSError:
        s.close()
        return None
    return s


def received(lib, s, wait=0.5):
    """The responses on s (the daemon's packets handled as they come), each parsed."""
    out = []
    while select.select([s], [], [], wait)[0]:
        p = s.recv(1500)
        if p[2] & 0x80:
            out.append(parse(p)[1])
        else:  # a query: the daemon's poll hands it over, with whatever its socket holds before it
            while lib.mdns_fd() >= 0 and select.select([lib.mdns_fd()], [], [], 0)[0]:
                lib.mdns_handle()
    return out


def test_daemon_announces_answers_and_says_goodbye(lib, tmp_path):
    s = listener()
    if s is None:
        pytest.skip('no multicast on loopback here')
    with s:
        (tmp_path / 'mnt/data').mkdir(parents=True)
        (tmp_path / 'mnt/data/config.ini').write_text('[OTHER]\nBTNAME=Not this\n[Q2POD]\nCARREMOTE=1\nBTNAME=Car Q2\n')
        ctypes.memmove(ctypes.addressof(ctypes.c_char.in_dll(lib, 'root')), str(tmp_path).encode() + b'\0',
                       len(str(tmp_path)) + 1)
        now = ctypes.c_uint32.in_dll(lib, 'now_ms')
        lib.mdns_start(b'lo')
        lib.mdns_poll(now.value)
        assert lib.mdns_fd() >= 0
        first = received(lib, s)
        assert len(first) == 1
        inst = 'Car Q2._q2muse._tcp.local'
        assert first[0] == [('an', SVC, PTR, 1, TTL, inst), ('an', inst, SRV, 0x8001, TTL, (0, 0, 7720, HOST)),
                            ('an', inst, TXT, 0x8001, TTL, ['id=1a2b3c4d5e6f7081', 'v=1']),
                            ('an', HOST, A, 0x8001, TTL, '127.0.0.1')]
        lib.mdns_poll(now.value + 500)
        assert received(lib, s, 0.2) == []  # the second announcement waits a second
        now.value += 1000
        lib.mdns_poll(now.value)
        assert len(received(lib, s)) == 1
        lib.mdns_poll(now.value + 1000)
        assert received(lib, s, 0.2) == []  # two, then nothing on its own
        now.value += 5000
        s.sendto(query((SVC, PTR)), (GROUP, 5353))
        assert [[(r[0], r[2]) for r in recs] for recs in received(lib, s)] == [
            [('an', PTR), ('ar', SRV), ('ar', TXT), ('ar', A)]]
        s.sendto(query((SVC, PTR)), (GROUP, 5353))
        assert received(lib, s) == []  # the same records within a second: not sent again
        now.value += 1000
        s.sendto(query(('_raop._tcp.local', PTR)), (GROUP, 5353))
        assert received(lib, s) == []
        s.sendto(b'\x00' * 5, (GROUP, 5353))
        assert received(lib, s) == []
        lib.mdns_stop()
        assert lib.mdns_fd() == -1
        bye = received(lib, s)
        assert len(bye) == 1 and len(bye[0]) == 4 and all(r[4] == 0 for r in bye[0])
