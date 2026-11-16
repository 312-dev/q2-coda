#!/usr/bin/env python3
"""coda-rc --avrcp from a build, as MIPS under qemu-mipsel with the build's rootfs (docs/bluetoothd.md#browsing):
the socket the rebuilt bluetoothd talks to, its framing, and a car's walk from the root into an album, against
test/unit/test_coda_rc_avrcp.py's library. test/unit/test_coda_rc_avrcp.py covers each PDU on this machine;
this proves the daemon's own loop, its stock libc and SQLite, and the frames bluetoothd's shanling_browse sends.

usage: coda_rc_avrcp.py BUILD   (a tools/build.py --out directory: its rootfs.squashfs)"""
import pathlib
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'test/unit'))
from test_coda_rc_avrcp import ALBUMS, library  # noqa: E402

FAILED = []


def check(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + (f'  [{detail}]' if not ok else ''))
    if not ok:
        FAILED.append(name)


def ask(s, pdu, params, tag=9, room=1000):
    body = bytes([pdu]) + struct.pack('>IH', tag, room) + params
    s.sendall(struct.pack('>H', len(body)) + body)
    n = struct.unpack('>H', s.recv(2))[0]
    r = b''
    while len(r) < n:
        r += s.recv(n - len(r))
    return r


def names(reply):
    """GetFolderItems' item names, folders or songs"""
    out, at = [], 6
    for _ in range(struct.unpack_from('>H', reply, 4)[0]):
        kind, n = reply[at], struct.unpack_from('>H', reply, at + 1)[0]
        item = reply[at + 3:at + 3 + n]
        k = 12 if kind == 2 else 11
        out.append(item[k + 2:k + 2 + struct.unpack_from('>H', item, k)[0]].decode())
        at += 3 + n
    return out


def main():
    build = pathlib.Path(sys.argv[1])
    with tempfile.TemporaryDirectory(dir=pathlib.Path.home() / '.cache') as t:
        rootfs, q2 = pathlib.Path(t) / 'rootfs', pathlib.Path(t) / 'q2'
        subprocess.run(['unsquashfs', '-q', '-n', '-d', str(rootfs), str(build / 'rootfs.squashfs'), 'lib', 'usr/lib',
                        'usr/bin/coda-rc'], check=True, stdout=subprocess.DEVNULL)
        library(q2)
        daemon = subprocess.Popen(['qemu-mipsel', '-L', str(rootfs), str(rootfs / 'usr/bin/coda-rc'), '--root', str(q2),
                                   '--avrcp'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            path = q2 / 'tmp/coda-rc.avrcp'
            for _ in range(100):
                if path.exists():
                    break
                time.sleep(0.1)
            check('the daemon listens on its socket, root\'s alone', path.exists() and path.stat().st_mode & 0o777 == 0o600)
            check('... and says it runs', (q2 / 'tmp/coda-rc-avrcp.pid').read_text().strip() == str(daemon.pid))
            s = socket.socket(socket.AF_UNIX)
            s.settimeout(20)
            s.connect(str(path))
            r = ask(s, 0x70, b'\0\1')
            check('SetBrowsedPlayer 1: the root, seven lists', r == bytes([0x70, 4, 0, 0]) + struct.pack('>IHB', 7, 0x6a, 0), r.hex())
            r = ask(s, 0x71, b'\1' + struct.pack('>II', 0, 9) + b'\0')
            check('GetFolderItems lists them', names(r)[:4] == ['Tracks', 'Playlists', 'All artists', 'Albums'], names(r))
            r = ask(s, 0x72, struct.pack('>HBQ', 0, 1, 4))
            check('ChangePath into Albums', r == bytes([0x72, 4]) + struct.pack('>I', 3), r.hex())
            r = ask(s, 0x72, struct.pack('>HBQ', 0, 1, 3))
            r = ask(s, 0x71, b'\1' + struct.pack('>II', 0, 9) + b'\0')
            check('... then Menagerie\'s songs', names(r) == ALBUMS[0][3], names(r))
            s.sendall(b'\0\5\xff\0\0\0\x09')  # bluetoothd forgets the car
            r = ask(s, 0x75, b'\1')
            check('a forgotten car starts again at the root', r == bytes([0x75, 4, 0, 0]) + struct.pack('>I', 7), r.hex())
            r = ask(s, 0x70, b'\0\1', room=6)
            check('a reply past the room is an internal error', r == bytes([0x70, 3]), r.hex())
            s.close()
        finally:
            daemon.terminate()
            daemon.wait(10)
        check('stopped, its socket and pid file are gone',
              not (q2 / 'tmp/coda-rc.avrcp').exists() and not (q2 / 'tmp/coda-rc-avrcp.pid').exists())
    if FAILED:
        sys.exit(f'{len(FAILED)} failed')


if __name__ == '__main__':
    main()
