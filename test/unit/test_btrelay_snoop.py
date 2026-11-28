"""tools/btrelay/btsnoop.py: an HCI capture (btsnoop, as hcidump -w and btmon -w write it) read back as what the
Bluetooth relay checks (docs/bt-relay.md#the-q2s-own-capture): the codecs each side offered over AVDTP, the one
configured and its parameters, the media stream's bit rate and lost packets, AVRCP's commands, and the hands-free
link's AT lines. The captures are built here, packet by packet, from the formats' own layouts."""
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'btrelay'))
import btsnoop  # noqa: E402

HANDLE = 0x0b
EPOCH = 0x00dcddb30f2f8000  # btsnoop's microseconds at 1970-01-01


class Capture:
    """One ACL link as the Q2's host sees it. `us` is the clock, advanced by the caller."""
    def __init__(self, datalink=1002):
        self.datalink, self.records, self.us = datalink, [], 1_800_000_000_000_000

    def record(self, sent, kind, payload):
        if self.datalink == 1002:
            data, flags = bytes([kind]) + payload, 0 if sent else 1
        elif self.datalink == 1001:
            data, flags = payload, (0 if sent else 1) | (2 if kind in (1, 4) else 0)
        else:  # 2001, btmon's: the opcode in the flags' high half, index 0 in the low
            data, flags = payload, {(1, True): 2, (4, False): 3, (2, True): 4, (2, False): 5}[kind, sent] << 16
        self.records.append(struct.pack('>IIIIQ', len(data), len(data), flags, 0, EPOCH + self.us) + data)

    def acl(self, sent, cid, payload, fragment=0):
        """An L2CAP frame on the link, whole or cut after `fragment` bytes into a start and a continuation."""
        frame = struct.pack('<HH', len(payload), cid) + payload
        parts = [frame[:fragment], frame[fragment:]] if fragment else [frame]
        for i, part in enumerate(parts):
            self.record(sent, 2, struct.pack('<HH', HANDLE | (0x1000 if i else 0x2000), len(part)) + part)

    def channel(self, sent, psm, ours, theirs):
        """L2CAP's Connection Request and its answer: `sent` is who asked."""
        req, rsp = (ours, theirs) if sent else (theirs, ours)
        self.acl(sent, 1, struct.pack('<BBHHH', 2, 1, 4, psm, req))
        self.acl(not sent, 1, struct.pack('<BBHHHHH', 3, 1, 8, rsp, req, 0, 0))

    def bytes(self):
        return b'btsnoop\0' + struct.pack('>II', 1, self.datalink) + b''.join(self.records)


SBC_CAPS = bytes([0x3f, 0xff, 2, 53])        # 44.1 and 48 kHz, every mode
SBC_CONF = bytes([0x21, 0x15, 2, 53])        # 44.1 kHz joint stereo, 16 blocks, 8 subbands, loudness
AAC_CAPS = bytes([0x80, 0x01, 0x8c, 0x84, 0xe2, 0x00])
APTX_HD = struct.pack('<IH', 0xd7, 0x24) + bytes([0x12, 0, 0, 0, 0])
LDAC = struct.pack('<IH', 0x12d, 0xaa) + bytes([0x04, 0x01])


def codec(kind, info):
    return bytes([7, 2 + len(info), 0, kind]) + info


def avdtp(cap, sent, label, kind, signal, body=b''):
    """A single-packet signalling message on the first AVDTP channel (ours 0x40, theirs 0x50)."""
    cap.acl(sent, 0x50 if sent else 0x40, bytes([label << 4 | kind, signal]) + body)


def rtp(seq, payload):
    return struct.pack('>BBHII', 0x80, 96, seq, seq * 1024, 1) + payload


def sbc_stream(datalink=1002, lose=(), fragment=0):
    """The Q2 connects, reads a sink's two endpoints (SBC, and one of AAC, aptX HD and LDAC in turn is enough for
    the names), configures SBC and sends 100 media packets of 5 frames of 119 bytes, 14.5 ms apart."""
    cap = Capture(datalink)
    cap.channel(True, 0x19, 0x40, 0x50)
    avdtp(cap, True, 1, 0, 1)
    avdtp(cap, False, 1, 2, 1, bytes([1 << 2, 0x08, 2 << 2, 0x08, 3 << 2, 0x08, 4 << 2, 0x08]))
    for seid, info in ((1, codec(0, SBC_CAPS)), (2, codec(2, AAC_CAPS)), (3, codec(0xff, APTX_HD)), (4, codec(0xff, LDAC))):
        avdtp(cap, True, 2, 0, 2, bytes([seid << 2]))
        avdtp(cap, False, 2, 2, 2, bytes([1, 0]) + info)
    avdtp(cap, True, 3, 0, 3, bytes([1 << 2, 5 << 2, 1, 0]) + codec(0, SBC_CONF))
    avdtp(cap, False, 3, 2, 3)
    avdtp(cap, True, 4, 0, 6, bytes([1 << 2]))
    avdtp(cap, False, 4, 2, 6)
    cap.channel(True, 0x19, 0x41, 0x51)
    avdtp(cap, True, 5, 0, 7, bytes([1 << 2]))
    avdtp(cap, False, 5, 2, 7)
    frame = bytes([0x9c, 0xbd, 53, 0]) + bytes(115)
    for seq in range(100):
        cap.us += 14_500
        if seq not in lose:
            cap.acl(True, 0x51, rtp(seq, bytes([5]) + frame * 5), fragment)
    return cap


def test_the_codecs_a_sink_offers_and_the_one_configured():
    r = btsnoop.report(sbc_stream().bytes())
    assert r['remote_codecs'] == ['SBC', 'AAC', 'aptX HD', 'LDAC']
    assert r['local_codecs'] == []
    assert r['configured'] == {'codec': 'SBC', 'rate': 44100, 'channels': 'joint stereo', 'blocks': 16, 'subbands': 8,
                               'allocation': 'loudness', 'bitpool': [2, 53], 'by': 'q2'}
    assert [(s['signal'], s['kind'], s['sent']) for s in r['avdtp']][-4:] == [
        ('open', 'command', True), ('open', 'accept', False), ('start', 'command', True), ('start', 'accept', False)]


def test_the_media_streams_rate_and_its_lost_packets():
    m = btsnoop.report(sbc_stream().bytes())['media']
    assert (m['packets'], m['bytes'], m['lost'], m['sent']) == (100, 100 * 5 * 119, 0, True)
    assert m['seconds'] == pytest.approx(99 * 0.0145)
    assert m['kbps'] == pytest.approx(100 * 5 * 119 * 8 / (100 * 0.0145) / 1000, rel=0.02)  # SBC at bitpool 53: 328
    lossy = btsnoop.report(sbc_stream(lose={10, 11, 50}).bytes())['media']
    assert (lossy['packets'], lossy['lost']) == (97, 3)


@pytest.mark.parametrize('datalink', [1001, 1002, 2001])
def test_each_capture_format_and_a_frame_cut_across_acl_packets(datalink):
    r = btsnoop.report(sbc_stream(datalink, fragment=300).bytes())
    assert r['configured']['codec'] == 'SBC' and r['media']['packets'] == 100


def test_the_stream_as_a_file_ffmpeg_decodes():
    es = btsnoop.extract(sbc_stream().bytes())
    assert len(es) == 100 * 5 * 119 and es[:3] == bytes([0x9c, 0xbd, 53])


def test_a_capture_begun_after_the_channels_opened_still_finds_the_media():
    cap = Capture()
    for seq in range(20):
        cap.us += 14_500
        cap.acl(True, 0x51, rtp(seq, bytes([1, 0x9c, 0xbd, 53]) + bytes(116)))
    r = btsnoop.report(cap.bytes())
    assert r['media']['packets'] == 20 and r['configured'] is None


def configured(info, kind=0xff, sent=False):
    cap = Capture()
    cap.channel(sent, 0x19, 0x40, 0x50)
    avdtp(cap, sent, 1, 0, 3, bytes([1 << 2, 5 << 2, 1, 0]) + codec(kind, info))
    avdtp(cap, not sent, 1, 2, 3)
    return btsnoop.report(cap.bytes())['configured']


def test_each_codecs_configuration():
    assert configured(bytes([0x80, 0x01, 0x04, 0x83, 0xe8, 0x00]), 2) == {
        'codec': 'AAC', 'object': 'MPEG-2 LC', 'rate': 44100, 'channels': 'stereo', 'vbr': True, 'bitrate': 256000,
        'by': 'peer'}
    assert configured(struct.pack('<IH', 0x4f, 1) + bytes([0x12])) == {
        'codec': 'aptX', 'rate': 48000, 'channels': 'stereo', 'by': 'peer'}
    assert configured(APTX_HD, sent=True) == {'codec': 'aptX HD', 'rate': 48000, 'channels': 'stereo', 'by': 'q2'}
    assert configured(struct.pack('<IH', 0x12d, 0xaa) + bytes([0x04, 0x01])) == {
        'codec': 'LDAC', 'rate': 96000, 'channels': 'stereo', 'by': 'peer'}
    assert configured(struct.pack('<IH', 0x1234, 0x5678) + b'\1')['codec'] == 'vendor 0x00001234:0x5678'


def test_a_refused_configuration_is_not_the_configuration():
    cap = Capture()
    cap.channel(True, 0x19, 0x40, 0x50)
    avdtp(cap, True, 1, 0, 3, bytes([1 << 2, 5 << 2, 1, 0]) + codec(0, SBC_CONF))
    avdtp(cap, False, 1, 3, 3, bytes([7, 0x29]))
    r = btsnoop.report(cap.bytes())
    assert r['configured'] is None and r['avdtp'][-1]['kind'] == 'reject'


def avc(cap, sent, ctype, body, label=0, response=False):
    cap.acl(sent, 0x52 if sent else 0x42, bytes([label << 4 | (2 if response else 0), 0x11, 0x0e, ctype]) + body)


def vendor(pdu, params):
    return bytes([0x48, 0x00, 0x00, 0x19, 0x58, pdu, 0]) + struct.pack('>H', len(params)) + params


def test_avrcps_keys_volume_and_notifications():
    cap = Capture()
    cap.channel(False, 0x17, 0x42, 0x52)
    avc(cap, False, 0, bytes([0x48, 0x7c, 0x46, 0]))                    # the sink's pause key, pressed
    avc(cap, True, 9, bytes([0x48, 0x7c, 0x46, 0]), response=True)
    avc(cap, False, 0, bytes([0x48, 0x7c, 0x46 | 0x80, 0]))             # and released
    avc(cap, True, 0, vendor(0x50, bytes([64])))                        # the Q2 sets the sink's volume
    avc(cap, False, 9, vendor(0x50, bytes([64])), response=True)
    avc(cap, False, 3, vendor(0x31, bytes([0x01, 0, 0, 0, 0])))         # the sink asks for play status changes
    avc(cap, True, 0xd, vendor(0x31, bytes([0x01, 0x02])), response=True)
    events = btsnoop.report(cap.bytes())['avrcp']
    assert [(e['sent'], e['what']) for e in events] == [
        (False, 'key pause pressed'), (True, 'accepted key pause pressed'), (False, 'key pause released'),
        (True, 'set absolute volume 64'), (False, 'accepted set absolute volume 64'),
        (False, 'register playback status'), (True, 'changed playback status paused')]


def test_the_hands_free_links_at_lines():
    cap = Capture()
    cap.channel(False, 0x03, 0x43, 0x53)

    def uih(sent, text):
        body = text.encode()
        cap.acl(sent, 0x53 if sent else 0x43, bytes([0x0b, 0xef, len(body) << 1 | 1]) + body + b'\x9a')

    uih(False, 'AT+BRSF=959\r')
    uih(True, '\r\n+BRSF: 1024\r\n')
    uih(True, '\r\nOK\r\n')
    assert btsnoop.report(cap.bytes())['hfp'] == [
        {'sent': False, 'line': 'AT+BRSF=959'}, {'sent': True, 'line': '+BRSF: 1024'}, {'sent': True, 'line': 'OK'}]


def test_not_a_capture():
    with pytest.raises(ValueError, match='not a btsnoop'):
        btsnoop.report(b'RIFF' + bytes(40))


def test_the_command_line_checks_the_codec(tmp_path, capsys):
    path = tmp_path / 'c.snoop'
    path.write_bytes(sbc_stream().bytes())
    out = tmp_path / 'c.sbc'
    assert btsnoop.main([str(path), '--expect-codec', 'sbc', '--extract', str(out)]) == 0
    text = capsys.readouterr().out
    assert 'configured: SBC 44100 Hz joint stereo' in text and '328' in text and 'ffmpeg -f sbc' in text
    assert out.stat().st_size == 100 * 5 * 119
    assert btsnoop.main([str(path), '--expect-codec', 'LDAC']) == 1
    assert 'expected LDAC' in capsys.readouterr().out
