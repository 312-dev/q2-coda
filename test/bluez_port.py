#!/usr/bin/env python3
"""The rebuilt bluetoothd's Shanling hooks (docs/bluetoothd.md), each the way stock's binary behaves, run as MIPS
under Unicorn (test/bluez_emu.py) from tools/thirdparty.py's build of BlueZ 5.66 with tools/bluez/shanling.patch.

Usage: python3 test/bluez_port.py [BLUETOOTHD]  (by default tools/thirdparty.py's build, unstripped: the Codescape
toolchain)."""
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'test'))
sys.path.insert(0, str(ROOT / 'tools'))  # first: test/ has a thirdparty.py of its own
import thirdparty  # noqa: E402
from bluez_emu import Bluetoothd  # noqa: E402

CTYPE_STABLE, CTYPE_REJECTED, CTYPE_INTERIM, CTYPE_CHANGED = 0x0c, 0x0a, 0x0f, 0x0d
# struct avrcp's fields (profiles/audio/avrcp.c, 32-bit): conn, target, supported and registered events, the
# transaction per event
AVRCP_CONN, AVRCP_TARGET, AVRCP_SUPPORTED, AVRCP_REGISTERED, AVRCP_TRANSACTIONS = 0x04, 0x0c, 0x2c, 0x2e, 0x31


def pdu(pdu_id, params=b''):
    return b'\x00\x19\x58' + bytes([pdu_id, 0]) + struct.pack('>H', len(params)) + params + bytes(64)


FAILED = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f'  [{detail}]' if not ok else ''))
    if not ok:
        FAILED.append(name)


def session(b, supported=0, registered=0):
    s = b.alloc(bytes(0x60))
    b.u.mem_write(s + AVRCP_SUPPORTED, struct.pack('<HH', supported, registered))
    b.word(s + AVRCP_CONN, 0x5150)
    return s


def test_play_status(b):
    """GetPlayStatus: demo's status, and with no player duration unknown and position 0."""
    b.word(b.objects['g_play_status'], 2)
    s, p = session(b), b.alloc(pdu(0x30))
    ctype = b.call('avrcp_handle_get_play_status', s, p, 0)
    got = bytes(b.u.mem_read(p, 16))
    check('GetPlayStatus answers stable', ctype == CTYPE_STABLE, ctype)
    check('... duration unknown, position 0, the status demo sent', got[7:16] == b'\xff\xff\xff\xff\0\0\0\0\x02', got.hex())
    check('... read under g_notify_mutex', [c[1][0] for c in b.calls if c[0].startswith('pthread_mutex')] ==
          [b.objects['g_notify_mutex']] * 2)


def test_register_notification(b):
    b.word(b.objects['g_play_status'], 1)
    s = session(b, supported=(1 << 1) | (1 << 2))
    p = b.alloc(pdu(0x31, bytes([1, 0, 0, 0, 0])))
    ctype = b.call('avrcp_handle_register_notification', s, p, 9)
    got = bytes(b.u.mem_read(p, 9))
    check('STATUS_CHANGED registers, its interim status demo\'s', ctype == CTYPE_INTERIM and got[7:9] == b'\x01\x01', got.hex())
    check('... the transaction kept for the change', bytes(b.u.mem_read(s + AVRCP_TRANSACTIONS + 1, 1)) == b'\x09' and
          struct.unpack('<H', b.u.mem_read(s + AVRCP_REGISTERED, 2))[0] & 2)
    p = b.alloc(pdu(0x31, bytes([2, 0, 0, 0, 0])))
    ctype = b.call('avrcp_handle_register_notification', s, p, 4)
    got = bytes(b.u.mem_read(p, 16))
    check('TRACK_CHANGED\'s interim UID is 0', ctype == CTYPE_INTERIM and got[7:16] == b'\x02' + bytes(8), got.hex())
    p = b.alloc(pdu(0x31, bytes([3, 0, 0, 0, 0])))
    check('an event outside Shanling\'s set is rejected', b.call('avrcp_handle_register_notification', s, p, 1) == CTYPE_REJECTED)


def test_hciplayer_event(b):
    """libbtctl's g_avrcp_event_fun: a CHANGED response on g_hci_session's registration, once."""
    sent = []
    b.answer('avctp_send_vendordep', lambda conn, transaction, code, subunit, operands, n:
             sent.append((conn, transaction, code, subunit, bytes(b.u.mem_read(operands, n)))) or 0)
    s = session(b, supported=6, registered=1 << 1)
    b.u.mem_write(s + AVRCP_TRANSACTIONS + 1, b'\x07')
    b.word(b.sym['g_hci_session'], s)
    status = b.alloc(struct.pack('<I', 2))
    b.call('avrcp_hciplayer_event', 1, status)
    check('a status change goes to the registered session, as a CHANGED RegisterNotification with demo\'s status',
          sent == [(0x5150, 7, CTYPE_CHANGED, 9, b'\x00\x19\x58\x31\x00\x00\x02\x01\x02')], sent)
    check('... and its registration is used up', struct.unpack('<H', b.u.mem_read(s + AVRCP_REGISTERED, 2))[0] == 0)
    b.call('avrcp_hciplayer_event', 1, status)
    check('... so a second change waits for a new one', len(sent) == 1)
    b.word(b.sym['g_hci_session'], 0)
    b.u.mem_write(s + AVRCP_REGISTERED, struct.pack('<H', 4))
    b.call('avrcp_hciplayer_event', 2, b.alloc(bytes(8)))
    check('no session (a disconnection cleared it): nothing sent', len(sent) == 1)


def test_init_sets_the_event_function(b):
    b.answer('btd_profile_register', lambda *_: 0)
    b.word(b.objects['g_avrcp_event_fun'], 0)
    b.call('avrcp_init')
    check('avrcp_init sets g_avrcp_event_fun to avrcp_hciplayer_event',
          b.read(b.objects['g_avrcp_event_fun']) == b.sym['avrcp_hciplayer_event'], hex(b.read(b.objects['g_avrcp_event_fun'])))


def queue_items(b, q):
    """A shared/queue.c queue's items: {ref_count, head, tail, entries}, entries {data, next}."""
    out, e = [], b.read(q + 4)
    while e:
        out.append(b.read(e))
        e = b.read(e + 4)
    return out


def test_codec_priority(b):
    """a2dp_find_eps: the Quality page's codec first, then LDAC, aptX HD, aptX, AAC, SBC; others left out."""
    b.answer('avdtp_find_remote_sep', lambda *_: 1)
    caps = {}
    b.answers['get_capabilities'] = lambda sep, out, *_: (b.word(out, caps[sep]), 4)[1]
    endpoint = b.alloc(struct.pack('<3I', 0, 0, b._stub('get_capabilities')))
    seps, node = {}, 0
    for name, codec, vendor in (('SBC', 0, None), ('AAC', 2, None), ('LDAC', 0xff, 0x12d), ('aptX', 0xff, 0x4f),
                                ('MPEG', 1, None)):
        lsep = b.alloc(bytes(0x20))
        sep = b.alloc(struct.pack('<2IBB2xI', 0, endpoint, 0, codec, lsep) + bytes(0x30))
        b.word(lsep + 0x1c, sep)  # the local endpoint's user data: its a2dp_sep
        if vendor is not None:
            caps[sep] = b.alloc(struct.pack('<IH', vendor, 0))
        seps[sep] = name
    for sep in reversed(list(seps)):
        node = b.alloc(struct.pack('<2I', sep, node))  # a GSList
    for priority, want in ((2, ['AAC', 'LDAC', 'aptX', 'SBC']), (0x13, ['LDAC', 'aptX', 'AAC', 'SBC']),
                           (0x11, ['aptX', 'LDAC', 'AAC', 'SBC']), (0, ['SBC', 'LDAC', 'aptX', 'AAC'])):
        b.byte(b.objects['g_high_priority_lcodec'], priority)
        q = b.call('a2dp_find_eps', b.alloc(bytes(0x80)), node, 0)
        got = [seps[s] for s in queue_items(b, q)]
        check(f'codec priority {priority:#x}: {", ".join(want)}', got == want, got)


def signals(b):
    """bluez_play_info_signal's calls: (event, data)."""
    return [(a[0], bytes(b.u.mem_read(a[1], a[2]))) for n, a in b.calls if n == 'bluez_play_info_signal']


def test_receiver_track(b):
    """The controller side's attributes (a phone playing to the Q2) to demo: 0x104, title, artist, album, cut at C0 80."""
    for name in ('media_player_clear_metadata', 'media_player_set_playlist_item', 'media_player_set_metadata',
                 'media_player_metadata_changed', 'avrcp_get_play_status'):
        b.answer(name, lambda *_: 0)
    b.answer('btd_profile_register', lambda *_: 0)
    b.call('avrcp_init')  # recv_meta's strings
    player = b.alloc(bytes(0x40))
    controller = b.alloc(struct.pack('<I', player) + bytes(0x40))  # struct avrcp_data: player first
    s = b.alloc(bytes(0x60))
    b.word(s + 0x10, controller)  # session->controller
    def attr(i, text):
        return struct.pack('>IHH', i, 106, len(text)) + text
    attrs = attr(1, b'Lovely Day\xc0\x80junk') + attr(2, b'Bill Withers') + attr(3, b'Menagerie') + attr(7, b'245000')
    rsp = b.alloc(pdu(0x20, bytes([4]) + attrs))
    b.call('avrcp_get_element_attributes_rsp', 0, CTYPE_STABLE, 9, 0, rsp, 7 + 1 + len(attrs), s)
    want = struct.pack('<II', 245000, 3) + b''.join(struct.pack('<HH', i, len(t) + 1) + t + b'\0'
                                                     for i, t in ((1, b'Lovely Day'), (2, b'Bill Withers'), (3, b'Menagerie')))
    check('a phone\'s track goes to demo as PlayInfoChange 0x104', signals(b) == [(0x104, want)], signals(b))
    b.call('avrcp_get_element_attributes_rsp', 0, CTYPE_STABLE, 9, 0, rsp, 7 + 1 + len(attrs), s)
    check('... once: the same again sends nothing', signals(b) == [])


def avdtp_receive(b, session, packet):
    """avdtp.c's session_cb reading packet from the channel."""
    b.answers['g_io_channel_unix_get_fd'] = lambda *_: 3
    b.answers['read'] = lambda fd, buf, n, *_: (b.u.mem_write(buf, packet), len(packet))[1]
    address = [a for n, a, size in b.functions('session_cb') if size > 4000][0]  # avdtp's, not avctp's
    return b.call_at(address, 0, 1, session)  # G_IO_IN


def avdtp_session(b):
    s = b.alloc(bytes(0x480))
    b.word(s + 0x448, b.alloc(bytes(1024)))  # session->buf
    b.u.mem_write(s + 0x38, struct.pack('<HH', 672, 672))  # imtu, omtu
    return s


def test_receiver_stream(b):
    """A phone's A2DP START and SUSPEND to demo: 0x103 {-1, -1, 0x11 or 0x12}, before the command itself."""
    for name in ('ba2str', 'avdtp_send'):  # the command itself: answered as nothing (no stream here)
        b.answer(name, lambda *_: 1)
    for signal, state in ((7, 0x11), (9, 0x12)):
        avdtp_receive(b, avdtp_session(b), bytes([0x10, signal, 1 << 2]))
        check(f'a phone\'s {"START" if signal == 7 else "SUSPEND"} signals receiver mode {state:#x}',
              signals(b) == [(0x103, struct.pack('<3i', -1, -1, state))], signals(b))


def test_synclink_discover(b):
    """SyncLink on: a DISCOVER is rejected as if no endpoint existed (message type 3), off it is accepted (2)."""
    sent = []
    b.answer('avdtp_send', lambda *a: sent.append(a) or 1)
    b.answer('queue_length', lambda *_: 1)
    b.answer('queue_foreach', lambda *_: 0)
    for mode, want in ((1, 3), (0, 2)):
        sent.clear()
        b.byte(b.objects['g_synclink_mode'], mode)
        avdtp_receive(b, avdtp_session(b), bytes([0x20, 1]))
        check(f'SyncLink {"on: DISCOVER rejected" if mode else "off: DISCOVER accepted"}', sent and sent[0][2] == want, sent)


def test_disconnect_clears_session(b):
    for name in ('btd_adapter_find_device', 'bonding_attempt_complete'):
        b.answer(name, lambda *_: 0)
    b.word(b.sym['g_hci_session'], 0x1234)
    b.call('dev_disconnected', b.alloc(bytes(0x100)), b.alloc(bytes(8)), 0x13)
    check('a disconnection clears g_hci_session', b.read(b.sym['g_hci_session']) == 0)


def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else thirdparty.build('bluez') / 'usr/bin/bluetoothd'
    for test in (test_play_status, test_register_notification, test_hciplayer_event, test_init_sets_the_event_function, test_codec_priority,
                 test_receiver_track, test_receiver_stream, test_synclink_discover, test_disconnect_clears_session):
        test(Bluetoothd(path))
    if FAILED:
        sys.exit(f'{len(FAILED)} failed')


if __name__ == '__main__':
    main()
