"""q2-bip's OBEX and BIP (patch/q2bip_core.c, docs/internals.md#bluetooth-cover-art), built for this machine and
fed the packets a controller sends. The request bytes are written out here from the values the specifications
fix (IrOBEX 1.5's opcodes, header ids and encodings; BIP 1.2's type strings, Img-Handle and Img-Descriptor
headers; AVRCP 1.6's cover art target UUID and 200 x 200 JPEG thumbnail), not captured from a car."""
import ctypes as C
import re
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PKT = 1000
UUID = bytes.fromhex('7163DD544A7E11E2B47C0050C2490048')  # AVRCP 1.6, the cover art service's OBEX target
CONNECT = bytes.fromhex('80 001a 10 00 ffff 46 0013') + UUID  # OBEX 1.0, no flags, 65535-byte packets, Target
CONNECTED = bytes.fromhex('a0 001f 10 00 03e8 cb 00000001 4a 0013') + UUID  # Success, 1000 bytes, id 1, Who
CONN_ID = bytes.fromhex('cb 00000001')

IMAGE_FN = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_char_p, C.POINTER(C.POINTER(C.c_ubyte)), C.POINTER(C.c_uint))


class Bip(C.Structure):
    _fields_ = [('image', IMAGE_FN), ('ctx', C.c_void_p), ('connected', C.c_int), ('close', C.c_int),
                ('peer_max', C.c_uint), ('kind', C.c_int), ('sending', C.c_int), ('described', C.c_int),
                ('handle', C.c_char * 8), ('body', C.c_void_p), ('body_len', C.c_uint), ('body_at', C.c_uint),
                ('xml', C.c_char * 160)]


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2bip') / 'q2bip.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-Wall', '-Wextra', '-Werror',
                    ROOT / 'patch/q2bip_core.c', '-o', out], check=True)
    L = C.CDLL(str(out))
    L.bip_feed.argtypes = [C.POINTER(Bip), C.c_char_p, C.c_uint, C.c_char_p]
    L.bip_feed.restype = C.c_uint
    L.bip_descriptor_fits.argtypes = [C.c_char_p, C.c_uint]
    L.bip_handle.argtypes = [C.c_uint, C.c_char_p]
    return L


class Car:
    """A controller: its session, the covers the Q2 has by handle, and the handles it was asked for."""

    def __init__(self, lib, covers):
        self.lib, self.covers, self.asked, self.keep = lib, covers, [], None

        def image(_ctx, handle, data, n):
            self.asked.append(handle.decode())
            jpeg = self.covers.get(handle.decode())
            if jpeg is None:
                return 0
            self.keep = (C.c_ubyte * len(jpeg)).from_buffer_copy(jpeg)
            data[0], n[0] = C.cast(self.keep, C.POINTER(C.c_ubyte)), len(jpeg)
            return 1

        self.cb = IMAGE_FN(image)
        self.bip = Bip(image=self.cb)

    def send(self, packet):
        out = C.create_string_buffer(PKT)
        n = self.lib.bip_feed(C.byref(self.bip), packet, len(packet), out)
        answer = out.raw[:n]
        assert n >= 3 and struct.unpack('>H', answer[1:3])[0] == n <= PKT  # the length field is the packet's
        return answer

    def connect(self, packet=CONNECT):
        return self.send(packet)

    def get(self, type_, handle, descriptor=None, final=True, conn=True):
        h = (CONN_ID if conn else b'') + header(0x42, type_.encode() + b'\0')
        if handle is not None:
            h += header(0x30, (handle + '\0').encode('utf-16-be'))
        if descriptor is not None:
            h += header(0x71, descriptor.encode())
        return self.send(bytes([0x83 if final else 0x03]) + struct.pack('>H', 3 + len(h)) + h)

    def more(self):
        return self.send(bytes.fromhex('83 0008') + CONN_ID)

    def pull(self, first):
        """The body of a GET from its first answer on, each Continue answered by another GET; and the packets."""
        packets, body, answer = [first], b'', first
        while True:
            headers = parse(answer)
            body += headers.get(0x48, b'') + headers.get(0x49, b'')
            if answer[0] == 0xa0:
                assert 0x49 in headers and 0x48 not in headers  # the last piece is End-of-Body
                return body, packets
            assert answer[0] == 0x90 and 0x48 in headers and 0x49 not in headers
            answer = self.more()
            packets.append(answer)


def header(hid, value):
    return bytes([hid]) + struct.pack('>H', 3 + len(value)) + value


def parse(packet):
    """A response's headers {id: value}."""
    out, at = {}, 3
    while at < len(packet):
        hid = packet[at]
        n = {2: 2, 3: 5}.get(hid >> 6) or struct.unpack('>H', packet[at + 1:at + 3])[0]
        out[hid] = packet[at + (1 if hid >> 6 >= 2 else 3):at + n]
        at += n
    assert at == len(packet)
    return out


def jpeg(n):
    return b'\xff\xd8' + bytes(i * 7 & 0xff for i in range(n - 4)) + b'\xff\xd9'


def test_connect_answers_the_cover_art_target(lib):
    car = Car(lib, {})
    assert car.connect() == CONNECTED
    assert car.bip.connected == 1 and car.bip.peer_max == 0xffff


def test_connect_refuses_another_target_or_none(lib):
    pull = bytes.fromhex('8EE9B3D0460811D5841A0002A5D5C51B')  # BIP's own Image Pull target: not AVRCP's service
    assert Car(lib, {}).connect(bytes.fromhex('80 001a 10 00 ffff 46 0013') + pull) == bytes.fromhex('c6 0003')
    assert Car(lib, {}).connect(bytes.fromhex('80 0007 10 00 ffff')) == bytes.fromhex('c6 0003')
    car = Car(lib, {'1000001': jpeg(100)})
    car.connect(bytes.fromhex('80 0007 10 00 ffff'))
    assert car.get('x-bt/img-thm', '1000001') == bytes.fromhex('d3 0003')  # no session: Service Unavailable


def test_a_small_peer_packet_is_raised_to_obex_least(lib):
    car = Car(lib, {'1000001': jpeg(600)})
    car.connect(bytes.fromhex('80 001a 10 00 0040 46 0013') + UUID)
    assert car.bip.peer_max == 255
    body, packets = car.pull(car.get('x-bt/img-thm', '1000001'))
    assert body == jpeg(600) and max(map(len, packets)) == 255


def test_image_properties_xml(lib):
    car = Car(lib, {'0412345': jpeg(9876)})
    car.connect()
    body, packets = car.pull(car.get('x-bt/img-properties', '0412345'))
    assert len(packets) == 1 and 0xc3 not in parse(packets[0])
    assert body == (b'<image-properties version="1.0" handle="0412345">'
                    b'<native encoding="JPEG" pixel="200*200" size="9876"/></image-properties>')


@pytest.mark.parametrize('type_', ['x-bt/img-img', 'x-bt/img-thm'])
def test_image_in_many_packets(lib, type_):
    cover = jpeg(10000)
    car = Car(lib, {'1000001': cover})
    car.connect()
    first = car.get(type_, '1000001')
    assert first[0] == 0x90 and len(first) == PKT  # Continue, a full packet
    assert parse(first)[0xc3] == struct.pack('>I', 10000)  # the Length header, in the first alone
    body, packets = car.pull(first)
    assert body == cover
    assert [p[0] for p in packets] == [0x90] * 10 + [0xa0]  # Continue until the last, which is Success
    assert all(len(p) == PKT for p in packets[:-1]) and all(0xc3 not in parse(p) for p in packets[1:])
    assert car.asked == ['1000001']  # the cover read once, not per packet
    assert car.bip.sending == 0


def test_packets_never_pass_the_peers_size(lib):
    car = Car(lib, {'1000001': jpeg(3000)})
    car.connect(bytes.fromhex('80 001a 10 00 0200 46 0013') + UUID)  # 512
    body, packets = car.pull(car.get('x-bt/img-thm', '1000001'))
    assert body == jpeg(3000) and max(map(len, packets)) == 512


def test_a_body_that_just_fits_is_one_success(lib):
    for n, count in ((PKT - 11, 1), (PKT - 10, 2)):  # 3 + Length's 5 + End-of-Body's 3
        car = Car(lib, {'1000001': jpeg(n)})
        car.connect()
        body, packets = car.pull(car.get('x-bt/img-thm', '1000001'))
        assert (body, len(packets)) == (jpeg(n), count)


def test_request_headers_over_two_packets(lib):
    car = Car(lib, {'1000001': jpeg(50)})
    car.connect()
    assert car.get('x-bt/img-thm', None, final=False) == bytes.fromhex('90 0003')
    answer = car.send(bytes.fromhex('83') + struct.pack('>H', 3 + 5 + 19) + CONN_ID
                      + header(0x30, '1000001\0'.encode('utf-16-be')))
    assert answer[0] == 0xa0 and parse(answer)[0x49] == jpeg(50)


def test_srm_is_never_agreed_to(lib):
    car = Car(lib, {'1000001': jpeg(5000)})
    car.connect()
    h = CONN_ID + bytes.fromhex('97 01') + header(0x42, b'x-bt/img-thm\0') + header(0x30, '1000001\0'.encode('utf-16-be'))
    first = car.send(b'\x83' + struct.pack('>H', 3 + len(h)) + h)
    assert first[0] == 0x90 and 0x97 not in parse(first)  # no SRM header back: the car asks for each packet
    assert car.pull(first)[0] == jpeg(5000)


def test_handles_change_with_the_track(lib):
    car = Car(lib, {'1000001': jpeg(300)})
    car.connect()
    assert car.pull(car.get('x-bt/img-thm', '1000001'))[0] == jpeg(300)
    car.covers = {'2000002': jpeg(400)}  # the next song, another album
    assert car.get('x-bt/img-thm', '1000001') == bytes.fromhex('c4 0003')  # the old handle: Not Found
    assert car.pull(car.get('x-bt/img-thm', '2000002'))[0] == jpeg(400)
    assert car.get('x-bt/img-properties', '9999999') == bytes.fromhex('c4 0003')


def test_handle_from_cover_flows_key(lib):
    out = C.create_string_buffer(8)
    for key, want in ((0, b'0000000'), (0x811c9dc5, b'6136261'), (0xffffffff, b'4967295'), (12345678, b'2345678')):
        lib.bip_handle(key, out)
        assert out.value == want == b'%07d' % (key % 10**7)


@pytest.mark.parametrize('packet, code', [
    (bytes.fromhex('83 0003'), 0xc0),  # a GET of nothing
    (bytes.fromhex('83 0008') + CONN_ID, 0xc0),  # no type, no handle
    (bytes.fromhex('83 0009') + CONN_ID + b'\x42', 0xc0),  # a header cut short
    (bytes.fromhex('83 000b') + CONN_ID + bytes.fromhex('42 0002'), 0xc0),  # a header shorter than itself
    (bytes.fromhex('83 0010') + CONN_ID, 0xc0),  # a length that is not the packet's
    (bytes.fromhex('83 0008 cb 00000002'), 0xd3),  # another connection's id
    (bytes.fromhex('82 0003'), 0xd1),  # PUT
    (bytes.fromhex('85 0005 0000'), 0xd1),  # SETPATH
    (bytes.fromhex('ff 0003'), 0xa0),  # ABORT
])
def test_malformed_and_unserved_requests(lib, packet, code):
    car = Car(lib, {'1000001': jpeg(5000)})
    car.connect()
    assert car.send(packet) == bytes([code, 0, 3])
    assert car.pull(car.get('x-bt/img-thm', '1000001'))[0] == jpeg(5000)  # and the session goes on


def test_bad_handles_and_types(lib):
    car = Car(lib, {'1000001': jpeg(50)})
    car.connect()
    assert car.get('x-bt/img-thm', '100000') == bytes.fromhex('c0 0003')  # six digits
    assert car.get('x-bt/img-thm', '10000x1') == bytes.fromhex('c0 0003')
    assert car.get('x-bt/img-listing', '1000001') == bytes.fromhex('c0 0003')
    assert car.get('x-bt/img-thm', None) == bytes.fromhex('c0 0003')
    assert car.asked == []


def test_abort_ends_a_transfer(lib):
    car = Car(lib, {'1000001': jpeg(5000)})
    car.connect()
    assert car.get('x-bt/img-img', '1000001')[0] == 0x90
    assert car.send(bytes.fromhex('ff 0003')) == bytes.fromhex('a0 0003')
    assert car.more() == bytes.fromhex('c0 0003')  # nothing is being sent any more
    assert car.pull(car.get('x-bt/img-img', '1000001'))[0] == jpeg(5000)


def test_disconnect_closes(lib):
    car = Car(lib, {'1000001': jpeg(50)})
    car.connect()
    assert car.send(bytes.fromhex('81 0008') + CONN_ID) == bytes.fromhex('a0 0003')
    assert (car.bip.connected, car.bip.close) == (0, 1)
    assert car.get('x-bt/img-thm', '1000001') == bytes.fromhex('d3 0003')


DESC = '<image-descriptor version="1.0"><image encoding="%s" pixel="%s"/></image-descriptor>'


@pytest.mark.parametrize('xml, fits', [
    ('', 1), ('<image-descriptor version="1.0"/>', 1), (DESC % ('JPEG', '200*200'), 1), (DESC % ('', ''), 1),
    (DESC % ('JPEG', '0*0-65535*65535'), 1), (DESC % ('JPEG', '100**-400*400'), 1),
    (DESC % ('JPEG', '200*200-200*200'), 1),
    (DESC % ('PNG', '200*200'), 0), (DESC % ('JPEG', '640*480'), 0), (DESC % ('JPEG', '300*300-600*600'), 0),
    (DESC % ('JPEG', '10*10-199*199'), 0), (DESC % ('JPEG', '100**-400*300'), 0), (DESC % ('JPEG', '200'), 0),
    (DESC % ('JPEG', '200*200-'), 0), (DESC % ('JPEG', '200*x'), 0),
    ('<image-descriptor version="1.0"><image encoding="JPEG" pixel="200*200', 1),  # cut off: no attribute read
])
def test_descriptor(lib, xml, fits):
    assert lib.bip_descriptor_fits(xml.encode(), len(xml)) == fits


def test_get_image_with_a_descriptor(lib):
    car = Car(lib, {'1000001': jpeg(50)})
    car.connect()
    assert car.get('x-bt/img-img', '1000001', DESC % ('PNG', '200*200')) == bytes.fromhex('c6 0003')
    assert car.get('x-bt/img-img', '1000001', DESC % ('JPEG', '200*200'))[0] == 0xa0
    assert car.get('x-bt/img-img', '1000001', '')[0] == 0xa0  # an empty descriptor: the native version


def test_no_constant_drift_from_the_header():
    h = (ROOT / 'patch/q2bip.h').read_text()
    assert int(re.search(r'#define BIP_PKT (\d+)', h)[1]) == PKT
    assert re.search(r'#define BIP_PSM (0x[0-9a-f]+)', h)[1] == '0x1025'
