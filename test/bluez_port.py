#!/usr/bin/env python3
"""Vamp's bluetoothd's Shanling hooks (tools/bluez/shanling.patch, docs/bluez.md#shanlings-changes), each the way
stock's binary behaves (docs/bluetoothd.md), run as MIPS under Unicorn (test/bluez_emu.py) from tools/thirdparty.py's
build of BlueZ 5.88 for the stock base. Brought over from feat/bt-browse's suite for 5.66, its browsing cases left
out with browsing, its layouts 5.88's: the registered events and their transactions moved from struct avrcp to
the target's struct avrcp_data, avdtp's input buffers split in two, attributes parsed through a callback.

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
# 5.88's struct avrcp (profiles/audio/avrcp.c, 32-bit): conn, target (a struct avrcp_data), controller, supported
# events; and struct avrcp_data's registered events and the transaction per event
AVRCP_CONN, AVRCP_TARGET, AVRCP_CONTROLLER, AVRCP_SUPPORTED = 0x04, 0x0c, 0x10, 0x2c
DATA_REGISTERED, DATA_TRANSACTIONS = 0x14, 0x16
# 5.88's struct avdtp_local_sep (profiles/audio/avdtp.c) has no state or stream before its seid_info, so its user
# data is at 0x14 (5.66's 0x1c); struct avdtp keeps imtu and omtu at 0x38, then two struct in_buf of 1036 bytes
# (in_resp, in_cmd: 5.66 had one), so its buf pointer is at 0x3c + 2 * 1036
LSEP_USER_DATA = 0x14
AVDTP_MTU, AVDTP_BUF, AVDTP_SIZE = 0x38, 0x3c + 2 * 1036, 0x900


def pdu(pdu_id, params=b''):
    return b'\x00\x19\x58' + bytes([pdu_id, 0]) + struct.pack('>H', len(params)) + params + bytes(64)


FAILED = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f'  [{detail}]' if not ok else ''))
    if not ok:
        FAILED.append(name)


def session(b, supported=0, registered=0):
    s, target = b.alloc(bytes(0x60)), b.alloc(bytes(0x40))
    b.u.mem_write(s + AVRCP_SUPPORTED, struct.pack('<H', supported))
    b.u.mem_write(target + DATA_REGISTERED, struct.pack('<H', registered))
    b.word(s + AVRCP_TARGET, target)
    b.word(s + AVRCP_CONN, 0x5150)
    return s


def target(b, s):
    return b.read(s + AVRCP_TARGET)


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
    check('... the transaction kept for the change', bytes(b.u.mem_read(target(b, s) + DATA_TRANSACTIONS + 1, 1)) == b'\x09' and
          struct.unpack('<H', b.u.mem_read(target(b, s) + DATA_REGISTERED, 2))[0] & 2)
    p = b.alloc(pdu(0x31, bytes([2, 0, 0, 0, 0])))
    ctype = b.call('avrcp_handle_register_notification', s, p, 4)
    got = bytes(b.u.mem_read(p, 16))
    check('TRACK_CHANGED\'s interim UID is 0', ctype == CTYPE_INTERIM and got[7:16] == b'\x02' + bytes(8), got.hex())
    p = b.alloc(pdu(0x31, bytes([3, 0, 0, 0, 0])))
    check('an event outside Shanling\'s set is rejected', b.call('avrcp_handle_register_notification', s, p, 1) == CTYPE_REJECTED)


def test_hciplayer_event(b):
    """libbtctl's g_avrcp_event_fun: a CHANGED response on g_hci_session's registration, once."""
    sent = []
    b.answer('avctp_send_vendordep', lambda conn, transaction, code, subunit, operands, n, *_:
             sent.append((conn, transaction, code, subunit, bytes(b.u.mem_read(operands, n)))) or 0)
    s = session(b, supported=6, registered=1 << 1)
    b.u.mem_write(target(b, s) + DATA_TRANSACTIONS + 1, b'\x07')
    b.word(b.sym['g_hci_session'], s)
    status = b.alloc(struct.pack('<I', 2))
    b.call('avrcp_hciplayer_event', 1, status)
    check('a status change goes to the registered session, as a CHANGED RegisterNotification with demo\'s status',
          sent == [(0x5150, 7, CTYPE_CHANGED, 9, b'\x00\x19\x58\x31\x00\x00\x02\x01\x02')], sent)
    check('... and its registration is used up', struct.unpack('<H', b.u.mem_read(target(b, s) + DATA_REGISTERED, 2))[0] == 0)
    b.call('avrcp_hciplayer_event', 1, status)
    check('... so a second change waits for a new one', len(sent) == 1)
    b.word(b.sym['g_hci_session'], 0)
    b.u.mem_write(target(b, s) + DATA_REGISTERED, struct.pack('<H', 4))
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
        b.word(lsep + LSEP_USER_DATA, sep)  # the local endpoint's user data: its a2dp_sep
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
    """bluez_play_info_signal's calls since the last look: (event, data), the data read as each call made it (a
    caller's array on its stack is gone by the time the call has returned)."""
    if not hasattr(b, 'sent_signals'):
        b.sent_signals = []
        b.answers['bluez_play_info_signal'] = lambda event, data, n, *_: (  # libbtctl's: an import
            b.sent_signals.append((event, bytes(b.u.mem_read(data, n)))) or 0)
    out, b.sent_signals[:] = list(b.sent_signals), []
    return out


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
    signals(b)  # recording from here
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
    s = b.alloc(bytes(AVDTP_SIZE))
    b.word(s + AVDTP_BUF, b.alloc(bytes(1024)))  # session->buf
    b.u.mem_write(s + AVDTP_MTU, struct.pack('<HH', 672, 672))  # imtu, omtu
    return s


def test_receiver_stream(b):
    """A phone's A2DP START and SUSPEND to demo: 0x103 {-1, -1, 0x11 or 0x12}, before the command itself."""
    for name in ('ba2str', 'avdtp_send'):  # the command itself: answered as nothing (no stream here)
        b.answer(name, lambda *_: 1)
    signals(b)  # recording from here
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


def control_pdu(pdu_id, params):
    """an AVRCP vendor-dependent PDU: company id, PDU id, packet type, length, parameters, and room after"""
    return b'\x00\x19\x58' + bytes([pdu_id, 0]) + struct.pack('>H', len(params)) + params + bytes(600)


def element_attributes(b, asked=()):
    p = b.alloc(control_pdu(0x20, bytes(8) + bytes([len(asked)]) + b''.join(struct.pack('>I', a) for a in asked)))
    ctype = b.call('shanling_get_element_attributes', 0, p, 0)
    n = struct.unpack('>H', b.u.mem_read(p + 5, 2))[0]
    return ctype, bytes(b.u.mem_read(p + 7, n))


def attrs_of(params):
    out, at = {}, 1
    for _ in range(params[0]):
        aid, cs, n = struct.unpack_from('>IHH', params, at)
        out[aid] = params[at + 8:at + 8 + n].decode(errors='replace')  # a cut character reads as U+FFFD
        at += 8 + n
    return out


def test_element_attributes(b):
    """The control channel's GetElementAttributes from g_track_meta (tools/bt_meta.c's, in C)"""
    meta = b.alloc(struct.pack('<3i4I', 3, 12, 245000, *(b.alloc(t + b'\0') for t in
                                                          (b'Lovely Day', b'Bill Withers', b'Menagerie', b'Soul'))))
    b.word(b.objects['g_track_meta'], meta)
    ctype, params = element_attributes(b)
    check('GetElementAttributes answers the playing track, all seven', ctype == CTYPE_STABLE and attrs_of(params) == {
        1: 'Lovely Day', 2: 'Bill Withers', 3: 'Menagerie', 4: '3', 5: '12', 6: 'Soul', 7: '245000'}, attrs_of(params))
    check('... under g_notify_mutex', [c[1][0] for c in b.calls if c[0].startswith('pthread_mutex')] ==
          [b.objects['g_notify_mutex']] * 2)
    ctype, params = element_attributes(b, (7, 1, 7, 99))
    check('... only those asked, once each, in order', list(attrs_of(params).items()) == [(7, '245000'), (1, 'Lovely Day')])
    long = b'\xc3\xa9' * 300
    b.word(meta + 12, b.alloc(long + b'\0'))
    ctype, params = element_attributes(b, (1,))
    title = attrs_of(params)[1]
    check('... a title past one packet cut at a character', 7 + len(params) <= 509 and set(title) == {'\xe9'}, len(params))
    b.word(b.objects['g_track_meta'], 0)
    ctype, params = element_attributes(b)
    check('no track: a title of one space', attrs_of(params) == {1: ' '}, params.hex())
    p = b.alloc(control_pdu(0x20, bytes(8)))
    check('a request short of its count is rejected', b.call('shanling_get_element_attributes', 0, p, 0) == CTYPE_REJECTED)



def main():
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else thirdparty.build('bluez') / 'usr/bin/bluetoothd'
    for test in (test_play_status, test_register_notification, test_hciplayer_event, test_init_sets_the_event_function,
                 test_codec_priority, test_receiver_track, test_receiver_stream, test_synclink_discover,
                 test_disconnect_clears_session, test_element_attributes):
        try:
            test(Bluetoothd(path))
        except Exception as e:  # a hook missing or a layout moved: that test fails, the rest still run
            check(f'{test.__name__} ran', False, f'{type(e).__name__}: {e}')
    print(f'{len(FAILED)} failed' if FAILED else 'all passed')
    if FAILED:
        sys.exit(1)


if __name__ == '__main__':
    main()
