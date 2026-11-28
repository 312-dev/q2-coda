#!/usr/bin/env python3
"""btsnoop: an HCI capture taken on the Q2 (hcidump -w, btmon -w) read back as what the Bluetooth relay checks.
See docs/bt-relay.md#the-q2s-own-capture.

usage: btsnoop.py CAPTURE [--json] [--extract FILE] [--expect-codec NAME]

It prints the codecs each side offered over AVDTP, the configuration that was accepted (codec, rate, channel
mode, and for SBC the bitpool), the media stream's packets, bit rate and lost sequence numbers, every AVRCP
command and answer with who sent it, and the hands-free link's AT lines. --extract writes the stream as the
codec's own frames, for a decoder off the Q2. --expect-codec exits 1 unless that codec was configured.

"Sent" is the Q2's side throughout: the capture is the Q2's host talking to its controller."""
import json
import struct
import sys
from typing import Any

EPOCH_US = 0x00dcddb30f2f8000  # a btsnoop timestamp is microseconds since 0 AD; this is 1970-01-01
PSM_RFCOMM, PSM_AVCTP, PSM_AVDTP = 0x03, 0x17, 0x19
SIGNALS = {1: 'discover', 2: 'get capabilities', 3: 'set configuration', 4: 'get configuration', 5: 'reconfigure',
           6: 'open', 7: 'start', 8: 'close', 9: 'suspend', 10: 'abort', 11: 'security control',
           12: 'get all capabilities', 13: 'delay report'}
KINDS = {0: 'command', 1: 'general reject', 2: 'accept', 3: 'reject'}
VENDOR_CODECS = {(0x4f, 0x01): 'aptX', (0xd7, 0x24): 'aptX HD', (0x0a, 0x02): 'aptX LL', (0xd7, 0xad): 'aptX Adaptive',
                 (0x12d, 0xaa): 'LDAC', (0x75, 0x0103): 'Samsung Scalable', (0x53a, 0x4c32): 'LHDC',
                 (0x8a9, 0x0001): 'LC3plus'}
KEYS = {0x41: 'volume up', 0x42: 'volume down', 0x43: 'mute', 0x44: 'play', 0x45: 'stop', 0x46: 'pause',
        0x48: 'rewind', 0x49: 'fast forward', 0x4b: 'forward', 0x4c: 'backward'}
CTYPES = {0: '', 1: 'status ', 3: 'register ', 8: 'not implemented ', 9: 'accepted ', 10: 'rejected ', 12: '',
          13: 'changed ', 15: 'interim '}
EVENTS = {1: 'playback status', 2: 'track', 3: 'track reached end', 4: 'track reached start', 5: 'playback position',
          8: 'player setting', 9: 'now playing content', 10: 'available players', 11: 'addressed player',
          12: 'uids', 13: 'volume'}
PLAY_STATES = {0: 'stopped', 1: 'playing', 2: 'paused', 3: 'seeking forward', 4: 'seeking backward', 255: 'error'}
PDUS = {0x10: 'get capabilities', 0x20: 'get element attributes', 0x30: 'get play status', 0x31: 'notification',
        0x50: 'set absolute volume', 0x60: 'set addressed player'}


def records(data):
    """(seconds since 1970, sent, kind, packet) for each HCI packet; kind 1 command, 2 ACL, 3 SCO, 4 event."""
    if data[:8] != b'btsnoop\0' or len(data) < 16:
        raise ValueError('not a btsnoop capture (hcidump -w FILE, or btmon -w FILE)')
    datalink = struct.unpack_from('>I', data, 12)[0]
    if datalink not in (1001, 1002, 2001):
        raise ValueError(f'btsnoop datalink {datalink}: only 1001 (HCI), 1002 (H4) and 2001 (btmon) are read')
    at = 16
    while at + 24 <= len(data):
        _orig, size, flags, _drops, stamp = struct.unpack_from('>IIIIQ', data, at)
        packet = data[at + 24:at + 24 + size]
        at += 24 + size
        seconds = (stamp - EPOCH_US) / 1e6
        if datalink == 2001:
            kind_sent = {2: (1, True), 3: (4, False), 4: (2, True), 5: (2, False), 6: (3, True), 7: (3, False)}.get(flags >> 16)
            if kind_sent:
                yield seconds, kind_sent[1], kind_sent[0], packet
        elif datalink == 1002:
            if packet:
                yield seconds, not flags & 1, packet[0], packet[1:]
        else:
            sent = not flags & 1
            yield seconds, sent, (1 if sent else 4) if flags & 2 else 2, packet


def frames(data):
    """(seconds, sent, handle, cid, payload) for each whole L2CAP frame, put together from its ACL packets."""
    partial = {}
    for seconds, sent, kind, packet in records(data):
        if kind != 2 or len(packet) < 4:
            continue
        word, _size = struct.unpack_from('<HH', packet)
        handle, boundary, key = word & 0xfff, word >> 12 & 3, (word & 0xfff, sent)
        if boundary == 1:  # a continuation
            if key not in partial:
                continue
            partial[key][1] += packet[4:]
        else:
            partial[key] = [seconds, bytearray(packet[4:])]
        seconds, buf = partial[key]
        if len(buf) >= 4 and len(buf) >= 4 + struct.unpack_from('<H', buf)[0]:
            size, cid = struct.unpack_from('<HH', buf)
            del partial[key]
            yield seconds, sent, handle, cid, bytes(buf[4:4 + size])


def _one(bits, table):
    """The names of the bits set, highest first: one for a configuration, several for capabilities."""
    return [name for bit, name in table if bits & bit]


def _pick(names):
    return names[0] if len(names) == 1 else names


RATES_HIGH = ((0x20, 44100), (0x10, 48000))
SBC_MODES = ((8, 'mono'), (4, 'dual channel'), (2, 'stereo'), (1, 'joint stereo'))


def codec(kind, info):
    """A media codec capability's fields. For a configuration each field is one value; for capabilities, a list."""
    if kind == 0 and len(info) >= 4:
        return {'codec': 'SBC',
                'rate': _pick(_one(info[0], ((0x80, 16000), (0x40, 32000), *RATES_HIGH))),
                'channels': _pick(_one(info[0], SBC_MODES)),
                'blocks': _pick(_one(info[1], ((0x80, 4), (0x40, 8), (0x20, 12), (0x10, 16)))),
                'subbands': _pick(_one(info[1], ((8, 4), (4, 8)))),
                'allocation': _pick(_one(info[1], ((2, 'SNR'), (1, 'loudness')))),
                'bitpool': [info[2], info[3]]}
    if kind == 2 and len(info) >= 6:
        rates = _one(info[1], ((0x80, 8000), (0x40, 11025), (0x20, 12000), (0x10, 16000), (8, 22050), (4, 24000),
                               (2, 32000), (1, 44100)))
        rates += _one(info[2], ((0x80, 48000), (0x40, 64000), (0x20, 88200), (0x10, 96000)))
        return {'codec': 'AAC',
                'object': _pick(_one(info[0], ((0x80, 'MPEG-2 LC'), (0x40, 'MPEG-4 LC'), (0x20, 'MPEG-4 LTP'),
                                               (0x10, 'MPEG-4 scalable')))),
                'rate': _pick(rates), 'channels': _pick(_one(info[2], ((8, 'mono'), (4, 'stereo')))),
                'vbr': bool(info[3] & 0x80), 'bitrate': (info[3] & 0x7f) << 16 | info[4] << 8 | info[5]}
    if kind == 0xff and len(info) >= 6:
        vendor, ident = struct.unpack_from('<IH', info)
        name, rest = VENDOR_CODECS.get((vendor, ident), f'vendor 0x{vendor:08x}:0x{ident:04x}'), info[6:]
        if name in ('aptX', 'aptX HD', 'aptX LL') and rest:
            return {'codec': name, 'rate': _pick(_one(rest[0], RATES_HIGH)),
                    'channels': _pick(_one(rest[0], ((1, 'mono'), (2, 'stereo'))))}
        if name == 'LDAC' and len(rest) >= 2:
            return {'codec': name,
                    'rate': _pick(_one(rest[0], (*RATES_HIGH, (8, 88200), (4, 96000), (2, 176400), (1, 192000)))),
                    'channels': _pick(_one(rest[1], ((4, 'mono'), (2, 'dual channel'), (1, 'stereo'))))}
        return {'codec': name}
    return {'codec': {1: 'MPEG-1,2 audio', 4: 'ATRAC'}.get(kind, f'codec type {kind}')}


def capabilities(body):
    """The media codec capabilities in a run of AVDTP service capabilities."""
    found, at = [], 0
    while at + 2 <= len(body):
        category, size = body[at], body[at + 1]
        if category == 7 and size >= 2:
            found.append(codec(body[at + 3], body[at + 4:at + 2 + size]))
        at += 2 + size
    return found


def _avrcp(payload):
    """One AVCTP frame's AV/C command or answer in words, or None for a frame that carries none."""
    if len(payload) < 6 or payload[0] & 0x0c or payload[1:3] != b'\x11\x0e':  # single packets of the AVRCP profile
        return None
    ctype, opcode, rest = payload[3] & 0x0f, payload[5], payload[6:]
    lead = CTYPES.get(ctype, f'ctype {ctype} ')
    if opcode == 0x7c and rest:
        key = KEYS.get(rest[0] & 0x7f, f'0x{rest[0] & 0x7f:02x}')
        return f'{lead}key {key} {"released" if rest[0] & 0x80 else "pressed"}'
    if opcode == 0 and len(rest) >= 7 and rest[:3] == b'\x00\x19\x58':
        pdu, params = rest[3], rest[7:]
        if pdu == 0x50 and params:
            return f'{lead}set absolute volume {params[0] & 0x7f}'
        if pdu == 0x31 and params:
            what = lead + EVENTS.get(params[0], f'event {params[0]}')
            if ctype in (13, 15) and len(params) > 1:
                if params[0] == 1:
                    what += ' ' + PLAY_STATES.get(params[1], str(params[1]))
                elif params[0] == 13:
                    what += f' {params[1] & 0x7f}'
            return what
        return lead + PDUS.get(pdu, f'pdu 0x{pdu:02x}')
    return None


def _rfcomm_text(payload):
    """A UIH frame's bytes, the credit octet skipped: the AT lines' carrier."""
    if len(payload) < 4 or payload[1] & 0xef != 0xef:
        return b''
    size, at = payload[2] >> 1, 3
    if not payload[2] & 1 and len(payload) > 3:
        size, at = size | payload[3] << 7, 4
    if payload[1] & 0x10 and len(payload) - at - 1 > size:
        at += 1
    return payload[at:at + size]


def _trace(data):
    """Every frame followed through its L2CAP channel. Returns the report and the media stream's payloads."""
    pending, channels = {}, {}        # (handle, requester's cid, asked by us) -> psm; (handle, sent, cid) -> psm
    avdtp_seen, labels, asked = {}, {}, {}
    offered = {True: [], False: []}   # by who sent the capabilities
    r: dict[str, Any] = {'remote_codecs': [], 'local_codecs': [], 'configured': None, 'avdtp': [], 'media': None,
                         'avrcp': [], 'hfp': []}
    proposed, media, at_text = None, [], {True: b'', False: b''}
    for seconds, sent, handle, cid, payload in frames(data):
        if cid == 1:
            if len(payload) >= 8 and payload[0] == 2:
                psm, scid = struct.unpack_from('<HH', payload, 4)
                pending[handle, scid, sent] = psm
            elif len(payload) >= 12 and payload[0] == 3:
                dcid, scid, result, _status = struct.unpack_from('<HHHH', payload, 4)
                psm = pending.get((handle, scid, not sent))
                if psm is not None and result == 0:
                    del pending[handle, scid, not sent]
                    role = 'media' if psm == PSM_AVDTP and avdtp_seen.get(handle) else psm
                    avdtp_seen[handle] = avdtp_seen.get(handle) or psm == PSM_AVDTP
                    # the answerer receives on dcid and the requester on scid: each side sends to the other's
                    channels[handle, sent, scid] = channels[handle, not sent, dcid] = role
            continue
        role = channels.get((handle, sent, cid))
        if role is None and len(payload) > 20 and payload[0] == 0x80 and payload[1] & 0x7f >= 96:
            role = channels[handle, sent, cid] = 'media'  # the capture began after the channel opened
        if role == 'media':
            media.append((seconds, sent, payload))
        elif role == PSM_AVDTP and len(payload) >= 2 and not payload[0] & 0x0c:
            label, kind, signal, body = payload[0] >> 4, payload[0] & 3, payload[1] & 0x3f, payload[2:]
            r['avdtp'].append({'t': seconds, 'sent': sent, 'kind': KINDS[kind], 'signal': SIGNALS.get(signal, str(signal))})
            if kind == 0:
                labels[sent, label] = signal
                if signal in (3, 5):
                    found = capabilities(body[2 if signal == 3 else 1:])
                    asked[sent, label] = dict(found[0], by='q2' if sent else 'peer') if found else None
            elif kind == 2 and labels.get((not sent, label)) == signal:
                if signal in (2, 12):
                    offered[sent] += [c['codec'] for c in capabilities(body)]
                elif signal in (3, 5) and asked.get((not sent, label)):
                    proposed = asked[not sent, label]
        elif role == PSM_AVCTP:
            what = _avrcp(payload)
            if what:
                r['avrcp'].append({'t': seconds, 'sent': sent, 'what': what})
        elif role == PSM_RFCOMM:
            at_text[sent] += _rfcomm_text(payload)
            *lines, at_text[sent] = at_text[sent].replace(b'\n', b'\r').split(b'\r')
            r['hfp'] += [{'sent': sent, 'line': line.decode('ascii', 'replace')} for line in lines if line.strip()]
    r['remote_codecs'], r['local_codecs'], r['configured'] = offered[False], offered[True], proposed
    name = proposed['codec'] if proposed else None
    stream = []
    if media:
        sent = media[0][1]
        packets = [(t, p) for t, s, p in media if s == sent]
        seqs = [struct.unpack_from('>H', p, 2)[0] for _t, p in packets] if name != 'aptX' else []
        lost = sum((b - a - 1) & 0xffff for a, b in zip(seqs, seqs[1:]) if (b - a) & 0xffff < 0x8000)
        stream = [_payload(name, p) for _t, p in packets]
        seconds = packets[-1][0] - packets[0][0]
        size = sum(map(len, stream))
        r['media'] = {'packets': len(packets), 'bytes': size, 'seconds': seconds, 'lost': lost, 'sent': sent,
                      'kbps': (size - len(stream[0])) * 8 / seconds / 1000 if seconds > 0 else 0.0}
    return r, stream


def _payload(name, packet):
    """A media packet's codec frames: what follows the RTP header and the codec's own header octet. aptX has
    neither; AAC's LATM gets the LOAS sync and length a file decoder needs."""
    if name == 'aptX':
        return packet
    body = packet[12 + 4 * (packet[0] & 0x0f):]
    if name in ('SBC', 'LDAC', None):
        return body[1:]
    if name == 'AAC':
        return bytes([0x56, 0xe0 | len(body) >> 8 & 0x1f, len(body) & 0xff]) + body
    return body


DECODE = {'SBC': 'ffmpeg -f sbc -i {0} out.wav', 'AAC': 'ffmpeg -f loas -i {0} out.wav',
          'aptX': 'ffmpeg -f aptx -sample_rate RATE -i {0} out.wav',
          'aptX HD': 'ffmpeg -f aptx_hd -sample_rate RATE -i {0} out.wav',
          'LDAC': 'ffmpeg has no LDAC decoder: {0} holds the frames for libldacdec'}


def report(data):
    return _trace(data)[0]


def extract(data):
    return b''.join(_trace(data)[1])


def describe(c):
    if not c:
        return 'none seen (begin the capture before the stream is configured)'
    text = f'{c["codec"]} {c.get("rate", "?")} Hz {c.get("channels", "")}'.rstrip()
    if c['codec'] == 'SBC':
        text += f', {c["blocks"]} blocks, {c["subbands"]} subbands, {c["allocation"]}, bitpool {c["bitpool"][0]}-{c["bitpool"][1]}'
    if c['codec'] == 'AAC':
        text += f', {c["object"]}, {"VBR" if c["vbr"] else "CBR"} up to {c["bitrate"]} bit/s'
    return f'{text} (set by the {"Q2" if c["by"] == "q2" else "peer"})'


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    flags: dict[str, Any] = {'--json': False, '--extract': None, '--expect-codec': None}
    paths, i = [], 0
    while i < len(argv):
        if argv[i] == '--json':
            flags['--json'] = True
        elif argv[i] in flags and i + 1 < len(argv):
            flags[argv[i]] = argv[i + 1]
            i += 1
        else:
            paths.append(argv[i])
        i += 1
    if len(paths) != 1 or paths[0].startswith('-'):
        raise ValueError(__doc__[__doc__.index('usage:'):])
    with open(paths[0], 'rb') as f:
        r, stream = _trace(f.read())
    c, m = r['configured'], r['media']
    if flags['--json']:
        print(json.dumps(r, indent=1))
    else:
        print(f'peer offers: {" ".join(r["remote_codecs"]) or "nothing seen"}')
        print(f'Q2 offers: {" ".join(r["local_codecs"]) or "nothing seen"}')
        print(f'configured: {describe(c)}')
        if m:
            print(f'media: {m["packets"]} packets {"sent" if m["sent"] else "received"} in {m["seconds"]:.1f} s, '
                  f'{m["kbps"]:.0f} kbit/s, {m["lost"]} lost')
        else:
            print('media: none')
        t0 = min([e['t'] for e in r['avdtp'] + r['avrcp']], default=0)
        for e in r['avdtp']:
            print(f'avdtp {e["t"] - t0:8.3f} {"Q2  " if e["sent"] else "peer"} {e["signal"]} {e["kind"]}')
        for e in r['avrcp']:
            print(f'avrcp {e["t"] - t0:8.3f} {"Q2  " if e["sent"] else "peer"} {e["what"]}')
        for e in r['hfp']:
            print(f'hfp   {"Q2  " if e["sent"] else "peer"} {e["line"]}')
    if flags['--extract']:
        with open(flags['--extract'], 'wb') as f:
            f.write(b''.join(stream))
        name = c['codec'] if c else 'SBC'
        print(f'wrote {flags["--extract"]}: {DECODE.get(name, "{0}: no decoder known here").format(flags["--extract"])}')
    want = flags['--expect-codec']
    if want and (not c or c['codec'].lower().replace('-', ' ') != want.lower().replace('-', ' ')):
        print(f'FAIL: expected {want}, configured {c["codec"] if c else "nothing"}')
        return 1
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as e:
        print(f'btsnoop: {e}', file=sys.stderr)
        sys.exit(2)
