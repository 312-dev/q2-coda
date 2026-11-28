#!/usr/bin/env python3
"""ADB on --dev builds (docs/adb.md): the MIPS adbd under qemu-mipsel, and q2adb-gadget on a stand-in configfs.

Usage: test/adb.py [BUILD] [--qemu QEMU_MIPSEL]

adbd is BUILD's (from its rootfs), or without BUILD the one tools/thirdparty.py builds or has cached. It runs in a
bwrap sandbox whose /dev/usb-ffs/adb is a stand-in FunctionFS: ep0 a file that keeps the descriptors adbd writes,
ep1 and ep2 FIFOs this script speaks the adb protocol through, as a computer's adb would over USB. Checked: the
descriptors (FunctionFS v2, the ADB interface, Microsoft OS descriptors as the kernel parses them); that only a
key in the card's adb_keys authenticates, that its copy on /mnt/data serves while the card is away and goes when
the card's file does; shell:, exec: (byte for byte), sync: push and pull, and that the services the patch removes
are refused. A key `adb keygen` makes is checked too when adb and openssl are installed. q2adb-gadget
(patch/adb_gadget.sh) runs under the host's sh against a directory tree, with mount, umount, mountpoint and
usleep stubbed: the gadget it makes, park, unpark, and that a parked gadget is never bound. tools/uac_tone.sh, the
USB audio desk test, against a stand-in /proc, dmesg and aplay: that it plays only while the computer streams, with
the kernel's ring buffer full. adb over Wi-Fi: the
same adbd with --tcp on a loopback port, driven by tools/vamp.py's client (authorization, no UI socket, the
Smart Wi-Fi use file, exec:, q2remote's commands through vamp, push and pull, the 8-connection cap), and
q2adb-wifi (patch/adb_wifi.sh) under the host's sh with stand-ins for adbd and q2adb-mdns; with BUILD, its
q2adb-mdns too, found by vamp's browse. Needs bwrap and a
qemu-mipsel (qemu-user) for adbd; unsquashfs with BUILD.
"""
import base64, hashlib, os, pathlib, secrets, shutil, struct, subprocess, sys, tempfile, threading, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))

failures = []


def expect(name, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + name + ('' if ok else f': {detail}'))
    if not ok:
        failures.append(name)


# RSA: a key, adb's public key file format (mincrypt's RSAPublicKey), and adb's signature of a token: PKCS#1 v1.5
# over the 20-byte token as if it were a SHA-1 digest (RSA_sign(NID_sha1, token)).

def probable_prime(bits):
    while True:
        n = secrets.randbits(bits) | (3 << (bits - 2)) | 1
        if all(n % p for p in (3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)) and all(
                pow(secrets.randbelow(n - 3) + 2, n - 1, n) == 1 for _ in range(8)):
            return n


def rsa_key():
    e = 65537
    while True:
        p, q = probable_prime(1024), probable_prime(1024)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e and (p * q).bit_length() == 2048:
            return p * q, pow(e, -1, phi)


def adb_public(n, e=65537, name='test@q2'):
    """n's line in adbkey.pub's format: base64 of {len, n0inv, n[], rr[], exponent}, words little-endian."""
    words = lambda v: struct.pack('<64I', *((v >> (32 * i)) & 0xffffffff for i in range(64)))
    n0inv = (-pow(n, -1, 1 << 32)) % (1 << 32)
    blob = struct.pack('<iI', 64, n0inv) + words(n) + words(pow(1 << 2048, 2, n)) + struct.pack('<i', e)
    return base64.b64encode(blob).decode() + f' {name}\n'


SHA1_INFO = bytes.fromhex('3021300906052b0e03021a05000414')


def adb_sign(key, token):
    n, d = key
    em = b'\0\1' + b'\xff' * (256 - 3 - len(SHA1_INFO) - len(token)) + b'\0' + SHA1_INFO + token
    return pow(int.from_bytes(em, 'big'), d, n).to_bytes(256, 'big')


# The adb protocol (system/core/adb/protocol.txt), host side.
A = {name: struct.unpack('<I', name.encode())[0] for name in ('CNXN', 'AUTH', 'OPEN', 'OKAY', 'CLSE', 'WRTE')}
NAMES = {v: k for k, v in A.items()}


class Link:
    """The computer's end of the stand-in USB link: writes to ep1 (adbd's bulk out), reads ep2 (its bulk in)."""
    def __init__(self, ffs):
        self.out = os.open(ffs/'ep1', os.O_RDWR)
        self.inp = os.open(ffs/'ep2', os.O_RDWR)

    def send(self, cmd, arg0=0, arg1=0, data=b''):
        os.write(self.out, struct.pack('<6I', A[cmd], arg0, arg1, len(data), sum(data) & 0xffffffff, A[cmd] ^ 0xffffffff))
        if data:
            os.write(self.out, data)

    def read(self, n, timeout):
        out, end = b'', time.monotonic() + timeout
        os.set_blocking(self.inp, False)
        while len(out) < n:
            try:
                part = os.read(self.inp, n - len(out))
            except BlockingIOError:
                part = b''
            if not part:
                if time.monotonic() > end:
                    raise TimeoutError(f'adbd sent {len(out)} of {n} bytes')
                time.sleep(.01)
            out += part
        return out

    def recv(self, timeout=20):
        cmd, arg0, arg1, length, check, magic = struct.unpack('<6I', self.read(24, timeout))
        assert magic == cmd ^ 0xffffffff, 'bad magic'
        data = self.read(length, timeout) if length else b''
        assert sum(data) & 0xffffffff == check, 'bad checksum'
        return NAMES.get(cmd, hex(cmd)), arg0, arg1, data

    def connect(self, key):
        """Authenticate with key (or none): the device's banner, or None when it asks again."""
        self.send('CNXN', 0x01000000, 4096, b'host::\0')
        cmd, arg0, _, token = self.recv()
        assert (cmd, arg0, len(token)) == ('AUTH', 1, 20), (cmd, arg0, token)
        if key is None:
            return None
        self.send('AUTH', 2, 0, adb_sign(key, token))
        cmd, arg0, _, data = self.recv()
        if cmd == 'AUTH':
            assert arg0 == 1 and len(data) == 20
            return None
        assert cmd == 'CNXN', cmd
        return data

    def offer(self, line):
        """Send the computer's public key (adbkey.pub's line), as adb does once its signatures are refused."""
        self.send('AUTH', 3, 0, line.encode() + b'\0')  # with adbkey.pub's line end, as older adb sends it

    def quiet(self, seconds=2):
        """Whether adbd sends nothing for that long."""
        try:
            self.read(1, seconds)
            return False
        except TimeoutError:
            return True

    def service(self, name, data=b'', local=1):
        """Open a service, send data, and collect what it writes until it closes."""
        self.send('OPEN', local, 0, name.encode() + b'\0')
        cmd, remote, _, _ = self.recv()
        if cmd == 'CLSE':
            return None
        assert cmd == 'OKAY', cmd
        for i in range(0, len(data), 4096):
            self.send('WRTE', local, remote, data[i:i + 4096])
            assert self.recv()[0] == 'OKAY'
        out = b''
        while True:
            cmd, _, _, part = self.recv()
            if cmd == 'WRTE':
                out += part
                self.send('OKAY', local, remote)
            elif cmd == 'CLSE':
                self.send('CLSE', local, remote)
                return out
            elif cmd != 'OKAY':
                raise AssertionError(cmd)

    def sync(self, requests, local=2):
        """sync: with requests sent at once, then quit; what came back."""
        self.send('OPEN', local, 0, b'sync:\0')
        cmd, remote, _, _ = self.recv()
        assert cmd == 'OKAY', cmd
        data = requests + b'QUIT' + bytes(4)
        out = b''
        for i in range(0, len(data), 4096):  # adbd's MAX_PAYLOAD, which it tells the host in its CNXN
            self.send('WRTE', local, remote, data[i:i + 4096])
            while True:
                cmd, _, _, part = self.recv()
                if cmd == 'OKAY':
                    break
                assert cmd == 'WRTE', cmd
                out += part
                self.send('OKAY', local, remote)
        while True:
            cmd, _, _, part = self.recv()
            if cmd == 'WRTE':
                out += part
                self.send('OKAY', local, remote)
            elif cmd == 'CLSE':
                return out


class Adbd:
    """adbd under qemu-mipsel in a bwrap sandbox: the stand-in FunctionFS, the card at /mnt/mmc (or no card),
    /mnt/data from data, and /tmp from work/tmp, where adbd's UI socket is."""
    def __init__(self, qemu, adbd, work, card, data):
        self.ffs = work/'ffs'
        shutil.rmtree(self.ffs, ignore_errors=True)
        self.ffs.mkdir()
        (self.ffs/'ep0').write_bytes(b'')
        for ep in ('ep1', 'ep2'):
            os.mkfifo(self.ffs/ep)
        mmc = ['--bind', str(card), '/mnt/mmc'] if card else ['--dir', '/mnt/mmc']
        # q2-heavy preloads nodump.so, which makes a process non-dumpable, and a non-dumpable bwrap may not
        # write its own uid_map ("setting up uid map: Permission denied"). So bwrap starts without the
        # preload, qemu gets it back inside the sandbox (no core from it either), and -U keeps it from adbd,
        # whose MIPS loader cannot load a host library.
        env = dict(os.environ)
        preload = env.pop('LD_PRELOAD', None)
        keep = ['--setenv', 'LD_PRELOAD', preload] if preload else []
        self.p = subprocess.Popen(['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                                   '--tmpfs', '/mnt', *mmc, '--bind', str(data), '/mnt/data', '--dir', '/dev/usb-ffs',
                                   '--bind', str(self.ffs), '/dev/usb-ffs/adb', '--bind', str(work/'tmp'), '/tmp',
                                   *keep, '--die-with-parent', qemu[0], '-U', 'LD_PRELOAD', *qemu[1:], str(adbd)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.monotonic() + 20
        while (self.ffs/'ep0').stat().st_size == 0 and time.monotonic() < end and self.p.poll() is None:
            time.sleep(.05)
        if self.p.poll() is not None:
            raise RuntimeError(f'adbd ended at start ({self.p.returncode})')
        self.link = Link(self.ffs)

    def stop(self):
        self.p.kill()
        self.p.wait()


def check_descriptors(blob):
    """FunctionFS v2 descriptors as the kernel reads them (f_fs.c __ffs_data_got_descs, __ffs_data_do_os_desc)."""
    magic, length, flags, fs, hs, os_count = struct.unpack_from('<6I', blob)
    expect('ep0: FunctionFS v2 descriptors, full and high speed and Microsoft OS descriptors',
           (magic, flags, fs, hs, os_count) == (3, 1 | 2 | 8, 3, 3, 2), (magic, flags, fs, hs, os_count))
    at = 24
    for speed, packet in (('full', 64), ('high', 512)):
        intf = blob[at:at + 9]
        eps = [struct.unpack_from('<BBBBH', blob, at + 9 + 7 * i) for i in range(2)]
        expect(f'{speed} speed: one interface, class ff/42/01, two bulk endpoints of {packet} bytes',
               intf[:2] == b'\x09\x04' and intf[4:8] == bytes((2, 0xff, 0x42, 0x01)) and
               [(e[0], e[1], e[2], e[3], e[4]) for e in eps] == [(7, 5, 0x01, 2, packet), (7, 5, 0x82, 2, packet)], (intf, eps))
        at += 9 + 14
    iface, dwlen, bcd, windex, count = struct.unpack_from('<BIHHB', blob, at)
    compat = blob[at + 11:at + 35]  # the header is 11 bytes, packed
    expect('OS descriptor 4: compatible ID WINUSB for interface 0, Reserved1 1',
           (iface, dwlen, bcd, windex, count) == (0, 35, 1, 4, 1) and compat[:2] == b'\0\1' and compat[2:10] == b'WINUSB\0\0'
           and compat[10:] == bytes(14), (iface, dwlen, bcd, windex, count, compat))
    at += 35
    iface, dwlen, bcd, windex, wcount = struct.unpack_from('<BIHHH', blob, at)
    size, kind, pnl = struct.unpack_from('<IIH', blob, at + 11)
    name = blob[at + 21:at + 21 + pnl]
    pdl, = struct.unpack_from('<I', blob, at + 21 + pnl)
    value = blob[at + 25 + pnl:at + 25 + pnl + pdl]
    expect('OS descriptor 5: DeviceInterfaceGUID, adb\'s, a REG_SZ whose sizes add up (dwSize = 14 + names)',
           (iface, bcd, windex, wcount, kind) == (0, 1, 5, 1, 1) and size == 14 + pnl + pdl and dwlen == 11 + size and
           name == b'DeviceInterfaceGUID\0' and value == b'{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}\0',
           (iface, dwlen, bcd, windex, wcount, size, kind, name, value))
    at += 11 + size
    expect('descriptors: the header\'s length is what was written', length == at, (length, at))
    magic, slen, nstr, nlang = struct.unpack_from('<4I', blob, at)
    expect('strings: one language, "ADB Interface"', (magic, nstr, nlang) == (2, 1, 1) and
           blob[at + 16:at + slen] == b'\x09\x04ADB Interface\0' and at + slen == len(blob), blob[at:])


def ui(work):
    """The Q2's side of the prompt, as patch/ will be: a client of adbd's UI socket."""
    import socket
    c = socket.socket(socket.AF_UNIX)
    end = time.monotonic() + 10
    while True:
        try:
            c.connect(str(work/'tmp/q2adb-ui'))
            break
        except OSError:
            if time.monotonic() > end:
                raise
            time.sleep(.05)
    c.settimeout(10)
    return c


def adbd_checks(qemu, adbd):
    print('generating test keys...')
    good, other = rsa_key(), rsa_key()
    good_line, other_line = adb_public(good[0], name='dev@laptop'), adb_public(other[0], name='other')
    with tempfile.TemporaryDirectory(prefix='q2-adb-') as d:
        work = pathlib.Path(d)
        card, data, files = work/'card', work/'data', work/'tmp/files'
        for p in (card, data, files):
            p.mkdir(parents=True)

        def services(link):
            out = link.service('shell:echo q2; id -u')
            expect('shell: runs sh (through a pty: CR LF)', out is not None and out.replace(b'\r\n', b'\n') == b'q2\n'
                   + str(os.getuid()).encode() + b'\n', out)
            raw = bytes(range(256))
            (files/'raw').write_bytes(raw)
            out = link.service('exec:cat /tmp/files/raw', local=3)
            expect('exec: gives the command\'s bytes unchanged (adb exec-out)', out == raw, out[:40] if out else out)
            body = secrets.token_bytes(70000)
            path = b'/tmp/files/pushed'
            send = (b'SEND' + struct.pack('<I', len(path) + 4) + path + b',420' +
                    b''.join(b'DATA' + struct.pack('<I', len(body[i:i + 4096])) + body[i:i + 4096] for i in range(0, len(body), 4096)) +
                    b'DONE' + struct.pack('<I', 1700000000))
            reply = link.sync(send)
            expect('sync: push writes the file, its mode and time', reply[:4] == b'OKAY' and (files/'pushed').read_bytes() == body
                   and (files/'pushed').stat().st_mode & 0o777 == 0o644 and int((files/'pushed').stat().st_mtime) == 1700000000,
                   reply[:40])
            reply = link.sync(b'RECV' + struct.pack('<I', len(path)) + path, local=5)
            got, at = b'', 0
            while reply[at:at + 4] == b'DATA':
                n, = struct.unpack_from('<I', reply, at + 4)
                got += reply[at + 8:at + 8 + n]
                at += 8 + n
            expect('sync: pull reads it back', got == body and reply[at:at + 4] == b'DONE', reply[at:at + 8])
            for i, name in enumerate(('framebuffer:', 'tcpip:5555', 'root:', 'remount:', 'jdwp:1', 'dev:/dev/null',
                                      'backup:all', 'log:main', 'usb:')):
                expect(f'{name} is refused', link.service(name, local=10 + i) is None)

        def session(card_in, check):
            dev = Adbd(qemu, adbd, work, card if card_in else None, data)
            try:
                return check(dev, dev.link)
            finally:
                expect('adbd is still running at the end of the session', dev.p.poll() is None)
                dev.stop()

        def no_ui(dev, link):
            check_descriptors((dev.ffs/'ep0').read_bytes())
            expect('no authorized key: a signature is refused, the token sent again', link.connect(good) is None)
            link.offer(good_line)
            expect('no UI to ask: the offered key is refused, nothing sent', link.quiet())
            expect('the UI socket is root\'s alone (0600)', (work/'tmp/q2adb-ui').stat().st_mode & 0o777 == 0o600)
        session(True, no_ui)

        def prompt(dev, link):
            q = ui(work)
            expect('unknown key: refused', link.connect(good) is None)
            link.offer(good_line)
            asked = q.recv(4096)
            expect('the UI is asked "PK" and the computer\'s adbkey.pub line', asked == b'PK' + good_line.strip().encode(), asked[:60])
            q.sendall(b'NO')
            expect('Cancel: the computer stays out', link.quiet())
            # adb asks again with a new connection; this time Allow, once
            expect('again: refused', link.connect(good) is None)
            link.offer(good_line)
            expect('asked again', q.recv(4096).startswith(b'PK'))
            q.sendall(b'OK')
            cmd, _, _, banner = link.recv()
            expect('Allow: the computer is in (CNXN device::)', cmd == 'CNXN' and banner.startswith(b'device::'), (cmd, banner))
            services(link)
            expect('Allow once remembers nothing', not (data/'adb_keys').exists())
        session(False, prompt)

        def always(dev, link):
            q = ui(work)
            link.connect(other)
            link.offer(other_line)
            q.recv(4096)
            (data/'adb_keys').write_text(other_line)  # what the UI does for Always Allow This Computer
            q.sendall(b'OK')
            expect('Always Allow: in', link.recv()[0] == 'CNXN')
        session(False, always)
        expect('a remembered key connects with no prompt (no UI running)', session(False, lambda dev, link: link.connect(other) is not None))
        expect('another key is still refused', session(False, lambda dev, link: link.connect(good) is None))

        (card/'adb_keys').write_text(good_line)
        expect('the card\'s adb_keys authorizes while the card is in', session(True, lambda dev, link: link.connect(good) is not None))
        expect('...and not with no card', session(False, lambda dev, link: link.connect(good) is None))

        def reread(dev, link):
            expect('before: refused', link.connect(good) is None)
            (data/'adb_keys').write_text(good_line)
            expect('a key added while adbd runs counts at the next connection', link.connect(good) is not None)
        (data/'adb_keys').unlink()
        session(False, reread)

        # A key made by adb itself, signed by openssl as adb signs: the file format and signature are adb's.
        if shutil.which('adb') and shutil.which('openssl'):
            subprocess.run(['adb', 'keygen', str(work/'adbkey')], check=True, capture_output=True)
            modulus = subprocess.run(['openssl', 'rsa', '-in', str(work/'adbkey'), '-noout', '-modulus'], check=True,
                                     capture_output=True, text=True).stdout.strip().split('=')[1]
            mine = adb_public(int(modulus, 16)).split()[0]
            expect('adb keygen\'s adbkey.pub is the format this test writes', (work/'adbkey.pub').read_text().split()[0] == mine)
            shutil.copy(work/'adbkey.pub', data/'adb_keys')
            def keygen(dev, link):
                link.send('CNXN', 0x01000000, 4096, b'host::\0')
                _, _, _, token = link.recv()
                (work/'token').write_bytes(token)
                sig = subprocess.run(['openssl', 'pkeyutl', '-sign', '-inkey', str(work/'adbkey'), '-pkeyopt', 'digest:sha1',
                                      '-in', str(work/'token')], check=True, capture_output=True).stdout
                link.send('AUTH', 2, 0, sig)
                expect('a key from adb keygen authenticates', link.recv()[0] == 'CNXN')
            session(False, keygen)
        else:
            print('skip adb keygen cross-check (needs adb and openssl)')


GADGET_STUBS = {
    # mount -t TYPE DEV DIR, umount DIR, mountpoint -q DIR: a file listing what is mounted
    'mount': 'echo "$4" >> "$Q2ADB_RUN/mounts"; echo "mount $2 $4" >> "$Q2ADB_RUN/log"',
    'umount': 'grep -vx "$1" "$Q2ADB_RUN/mounts" > "$Q2ADB_RUN/m"; mv "$Q2ADB_RUN/m" "$Q2ADB_RUN/mounts"; echo "umount $1" >> "$Q2ADB_RUN/log"',
    'mountpoint': 'grep -qx "$2" "$Q2ADB_RUN/mounts"',
    'usleep': 'sleep 0.01',
    # configfs fills a new gadget's directory itself; the stand-in tree gets the parts setup writes into. This
    # kernel's configfs makes each gadget directory the device android_usb/android0, so a second one fails (EEXIST)
    'mkdir': 'p=; for a; do case "$a" in -p) p=-p; continue;; */usb_gadget/*/*) ;; */usb_gadget/*) for o in "${a%/*}"/*; '
             'do [ -d "$o" ] && { echo "mkdir: $a: File exists" >&2; exit 1; }; done;; esac; /bin/mkdir $p "$a" || exit; '
             'case "$a" in */configs/*/*|configs/*/*) ;; */configs/*|configs/*) /bin/mkdir -p "$a/strings";; '
             '*/usb_gadget/*/*) ;; */usb_gadget/*) /bin/mkdir -p "$a/os_desc" "$a/strings" "$a/configs" "$a/functions"; '
             ': > "$a/UDC";; esac; done',
    # configfs's rmdir takes a directory's attributes and its own default groups (strings, configs...) with it, but
    # not a directory or link made in it
    'rmdir': 'for a; do bad=; for c in "$a"/* "$a"/*/*; do [ -L "$c" ] && bad=1; if [ -d "$c" ] && [ ! -L "$c" ]; then '
             'case "${c#$a/}" in strings|os_desc|configs|functions) ;; *) bad=1;; esac; fi; done; '
             '[ -n "$bad" ] && { echo "rmdir: $a: Directory not empty" >&2; exit 1; }; rm -rf "$a"; done',
    'reboot': 'echo reboot >> "$Q2ADB_RUN/log"',
}


def fake_adbds(proc, *cmdlines):
    """A process for each command line (a sleep), listed in a stand-in /proc under its pid with that command line,
    as q2adb-gadget looks for the USB adbd."""
    procs = []
    for line in cmdlines:
        p = subprocess.Popen(['sleep', '1000'])
        (proc/str(p.pid)).mkdir(parents=True)
        (proc/str(p.pid)/'cmdline').write_bytes(line.replace(' ', '\0').encode() + b'\0')
        procs.append(p)
    return procs


def gadget_checks():
    script = ROOT/'patch/adb_gadget.sh'
    with tempfile.TemporaryDirectory(prefix='q2-adb-gadget-') as d:
        d = pathlib.Path(d)
        cfs, udc, ffs, run, bin = d/'config', d/'udc', d/'ffs', d/'run', d/'bin'
        for p in (cfs, udc, run, bin):
            p.mkdir()
        (udc/'13500000.otg').mkdir()
        (run/'mounts').write_text('')
        for name, body in GADGET_STUBS.items():
            (bin/name).write_text('#!/bin/sh\n' + body + '\n')
            (bin/name).chmod(0o755)
        proc = d/'proc'
        proc.mkdir()
        env = dict(os.environ, PATH=f'{bin}:{os.environ["PATH"]}', Q2ADB_CONFIGFS=str(cfs), Q2ADB_UDC=str(udc),
                   Q2ADB_FFS=str(ffs), Q2ADB_RUN=str(run), Q2ADB_PROC=str(proc), Q2ADB_LOG=str(d/'audio.log'),
                   Q2ADB_REBOOT='reboot', Q2ADB_DATA=str(d/'data'))
        (d/'data').mkdir()
        def gadget(*args):
            return subprocess.run(['sh', str(script), *args], env=env, capture_output=True, text=True, timeout=30)
        g = cfs/'usb_gadget/q2adb'
        (cfs/'usb_gadget').mkdir()
        # setup expects configfs mounted (stock's way to it) or mounts it
        r = gadget('setup')
        expect('setup: exits 0', r.returncode == 0, r.stderr)
        read = lambda p: (g/p).read_text().strip()
        ok = g.is_dir() and read('idVendor') == '0x1d6b' and read('idProduct') == '0x0105' and read('bcdDevice') == '0x0223' and \
            read('strings/0x409/serialnumber') == 'Q2CODA' and os.readlink(g/'configs/c.1/ffs.adb') == 'functions/ffs.adb' and \
            read('os_desc/use') == '1' and read('os_desc/qw_sign') == 'MSFT100' and read('os_desc/b_vendor_code') == '0x1' and \
            os.readlink(g/'os_desc/c.1') == 'configs/c.1' and (g/'functions/ffs.adb').is_dir()
        expect('setup: the q2adb gadget, its own IDs, the FunctionFS function and the OS descriptors', ok)
        log = (run/'log').read_text().split('\n')
        expect('setup: configfs mounted, then FunctionFS at the path adbd opens', log[:2] == [f'mount configfs {cfs}', f'mount functionfs {ffs}'], log)
        r = gadget('setup')
        expect('setup again changes nothing and mounts nothing more', r.returncode == 0 and (run/'log').read_text().count('mount ') == 2, r.stderr)

        start = time.monotonic()
        r = gadget('bind')
        expect('bind before adbd is ready: waits about 5 s, fails, leaves the controller free',
               r.returncode != 0 and 'adbd has not set up' in r.stderr and read('UDC') == '' and time.monotonic() - start > 0.4, r.stderr)
        (ffs/'ep1').write_text('')
        r = gadget('bind')
        expect('bind once adbd wrote its descriptors: q2adb on the controller', r.returncode == 0 and read('UDC') == '13500000.otg', r.stderr)

        stock = cfs/'usb_gadget/storage_demo'
        r = subprocess.run(['sh', '-c', f'mkdir {stock}'], env=env, capture_output=True, text=True)
        expect('the stand-in refuses a second gadget directory, as the kernel does (EEXIST)', r.returncode != 0 and not stock.exists())
        usb, tcp = fake_adbds(proc, '/usr/bin/adbd', '/usr/bin/adbd --tcp 5555')
        r = gadget('park')
        mounts = (run/'mounts').read_text().split()
        expect('park: the USB adbd stopped, FunctionFS and configfs unmounted, the gadget\'s directory gone, parked',
               r.returncode == 0 and not g.exists() and str(cfs) not in mounts and str(ffs) not in mounts
               and usb.poll() is not None and (run/'q2adb.parked').exists(), r.stderr)
        expect('park leaves adb over Wi-Fi (adbd --tcp) running', tcp.poll() is None)
        tcp.kill(); tcp.wait()
        r = subprocess.run(['sh', '-c', f'mkdir {stock} && rmdir {stock}'], env=env, capture_output=True, text=True)
        expect('parked, stock\'s storage gadget can be made, and taken down again at its stop', r.returncode == 0, r.stderr)
        r = gadget('bind')
        expect('bind while parked (adbd restarting under stock\'s gadget) binds nothing and mounts nothing',
               r.returncode == 0 and not g.exists() and str(cfs) not in (run/'mounts').read_text().split(), r.stderr)
        r = gadget('unpark')
        end = time.monotonic() + 10  # it binds in the background
        while read('UDC') != '13500000.otg' and time.monotonic() < end:
            time.sleep(.05)
        mounts = (run/'mounts').read_text().split()
        expect('unpark: the gadget made again, configfs and FunctionFS mounted, q2adb on the controller',
               r.returncode == 0 and read('UDC') == '13500000.otg' and str(cfs) in mounts and str(ffs) in mounts
               and read('idVendor') == '0x1d6b' and os.readlink(g/'configs/c.1/ffs.adb') == 'functions/ffs.adb'
               and not (run/'q2adb.parked').exists(), r.stderr)
        expect('a bound q2adb is left as it is', gadget('bind').returncode == 0 and read('UDC') == '13500000.otg')
        expect('an unknown command is refused', gadget('frob').returncode == 2)

        # USB audio beside adb (docs/usb-audio.md#usb-audio)
        data, log = d/'data', d/'audio.log'
        def rd(p):  # the gadget is made again in the background: a file not there yet reads as nothing
            try: return (g/p).read_text().strip()
            except OSError: return ''
        def bound(want=True):
            end = time.monotonic() + 10  # bound in the background
            while (g.is_dir() and rd('UDC') == '13500000.otg') != want and time.monotonic() < end:
                time.sleep(.05)
            return g.is_dir() and rd('UDC') == '13500000.otg'
        def audio_on():
            f = g/'functions/uac2.q2'
            return g.is_dir() and rd('idProduct') == '0x0104' and rd('bcdDevice') == '0x0225' and \
                rd('bDeviceClass') == '0xef' and rd('bDeviceSubClass') == '0x02' and rd('bDeviceProtocol') == '0x01' and \
                os.path.islink(g/'configs/c.1/uac2.q2') and os.readlink(g/'configs/c.1/uac2.q2') == 'functions/uac2.q2' and \
                os.readlink(g/'configs/c.1/ffs.adb') == 'functions/ffs.adb' and \
                [rd(f'functions/uac2.q2/{a}') for a in ('p_chmask', 'c_chmask', 'p_srate', 'p_ssize', 'req_number', 'p_volume_present')] == \
                ['3', '0', '44100,48000,88200,96000,176400,192000', '4', '8', '0'] and \
                rd('functions/uac2.q2/c_terminal_type') == '1539' and \
                rd('functions/uac2.q2/function_name') == 'Q2 Output' and (run/'q2adb.audio').exists()
        def audio_off():
            return g.is_dir() and rd('idProduct') == '0x0105' and rd('bDeviceClass') == '0x00' and \
                not (g/'functions/uac2.q2').exists() and not (run/'q2adb.audio').exists()
        def until(f, s=10):
            end = time.monotonic() + s
            while not f() and time.monotonic() < end:
                time.sleep(.05)
            return f()
        def said(text): return text in (log.read_text() if log.exists() else '')
        r = gadget('audio', 'on')
        expect('audio on: returns once its work has detached', r.returncode == 0, r.stderr)
        expect('audio on: the composite gadget (0x0104, class ef/02/01), adb and the UAC2 source as a line input (1539), remembered, bound',
               until(audio_on) and (data/'q2adb.audio').exists() and bound(), log.read_text() if log.exists() else '')
        expect('audio on: each step in the log on the card', until(lambda: said('bind returned 0')) and said('gadget removed'))
        r = gadget('setup')
        expect('setup with audio remembered keeps the gadget as it is', r.returncode == 0 and audio_on(), r.stderr)
        r = gadget('park')
        expect('park with audio: the UAC2 function and its link gone with the rest', r.returncode == 0 and not g.exists()
               and not (run/'q2adb.audio').exists(), r.stderr)
        r = gadget('unpark')
        expect('unpark with audio remembered: the composite gadget again', r.returncode == 0 and bound() and audio_on(), r.stderr)
        usb, tcp = fake_adbds(proc, '/usr/bin/adbd', '/usr/bin/adbd --tcp 5555')
        (proc/'99999').mkdir()  # a process gone between the scan's glob and its read: its cmdline found, then not there
        os.symlink(d/'gone', proc/'99999/cmdline')
        r = gadget('audio', 'off')
        expect('audio off: adb alone again, forgotten, bound', r.returncode == 0 and until(audio_off) and bound()
               and not (data/'q2adb.audio').exists(), r.stderr)
        expect('audio off: the USB adbd stopped and waited for, adb over Wi-Fi left running',
               usb.poll() is not None and tcp.poll() is None and said('stopped'))
        expect('a process gone mid-scan says nothing in the log', 'No such file' not in log.read_text(), log.read_text()[-500:])
        tcp.kill(); tcp.wait()
        # the shell that ran it gets SIGHUP as it ends (adb's pty): the work goes on
        log.write_text('')
        r = subprocess.run(['sh', '-c', f'sh {script} audio try 1; kill -HUP 0'], env=env, capture_output=True, text=True,
                           timeout=30, start_new_session=True)
        on = until(audio_on) and bound() and not (data/'q2adb.audio').exists()
        expect('audio try 1: on, not remembered, through the SIGHUP of the shell that ran it', on, r.stderr + log.read_text())
        expect('audio try 1: no keep, so off by its watchdog and USB adb back', until(lambda: said('USB adb back'))
               and audio_off() and bound(), log.read_text())
        r = gadget('audio', 'try', '1')
        k = gadget('audio', 'keep')
        expect('audio try then keep: still on after the try\'s second, kept and remembered',
               r.returncode == 0 and k.returncode == 0 and until(lambda: said('kept')) and audio_on() and (data/'q2adb.audio').exists())
        gadget('audio', 'off'); until(audio_off); until(lambda: said('bind returned'))
        # a desk test's other device: the terminal type the computer records from, another product and release
        knobs = dict(env, Q2ADB_UAC_CTERM='513', Q2ADB_UAC_PID='0x0106', Q2ADB_UAC_BCD='0x0226')
        # on, not try: a try's watchdog would outlive this check and go back to adb alone inside a later one
        r = subprocess.run(['sh', str(script), 'audio', 'on'], env=knobs, capture_output=True, text=True, timeout=30)
        def knobbed():
            return g.is_dir() and rd('idProduct') == '0x0106' and rd('bcdDevice') == '0x0226' and \
                rd('functions/uac2.q2/c_terminal_type') == '513' and rd('functions/uac2.q2/function_name') == 'Q2 Output'
        expect('audio with Q2ADB_UAC_CTERM, _PID and _BCD: that terminal type, product and release',
               r.returncode == 0 and until(knobbed) and bound(), r.stderr + log.read_text()[-800:])
        expect('the log names the terminal type set', until(lambda: said('c_terminal_type 513')), log.read_text()[-800:])
        gadget('audio', 'off'); until(audio_off); until(lambda: said('bind returned'))
        r = gadget('audio', 'on')
        expect('audio on without the knobs: 0x0104, 0x0225, a line input', until(audio_on), log.read_text()[-800:])
        gadget('audio', 'off'); until(audio_off); until(lambda: said('bind returned'))
        # a try's watchdog stands down for a command after it once USB adb is back: an on after a try stays on
        log.write_text('')
        gadget('audio', 'try', '3'); until(lambda: said('bind returned'))
        r = gadget('audio', 'on')
        expect('audio on after a try: that try\'s watchdog told to stand down once USB adb is back',
               r.returncode == 0 and until(lambda: said('told to stand down')), log.read_text()[-1500:])
        expect('the try\'s watchdog then stands down at its time, and the on stays on and remembered',
               until(lambda: said('stands down: an audio command'), 10) and audio_on() and (data/'q2adb.audio').exists()
               and not said('no keep in 3 s'), log.read_text()[-1500:])
        gadget('audio', 'off'); until(audio_off); until(lambda: said('bind returned'))
        # a later try: the earlier try's watchdog leaves the gadget to the later one's
        log.write_text('')
        gadget('audio', 'try', '2')
        gadget('audio', 'try', '6')
        expect('two tries: the first one\'s watchdog stands down, the second try stays on',
               until(lambda: said('stands down'), 10) and not said('no keep in 2 s') and audio_on(), log.read_text()[-1500:])
        expect('two tries: the second one\'s watchdog goes back to adb alone at its time',
               until(lambda: said('no keep in 6 s'), 15) and until(audio_off) and until(lambda: said('USB adb back')),
               log.read_text()[-1500:])
        log.write_text('')
        r = gadget('audio', 'rehearse', '1')
        expect('audio rehearse: the gadget made again as it was, no audio, and back', r.returncode == 0
               and until(lambda: said('USB adb back')) and audio_off() and bound(), log.read_text())
        r = gadget('audio', 'rehearse', '1')
        k = gadget('audio', 'keep')
        expect('audio rehearse then keep: the watchdog stands down and audio stays off, not remembered', k.returncode == 0
               and until(lambda: said('kept')) and until(audio_off) and not (data/'q2adb.audio').exists())
        # adbd never writing its descriptors again: no USB adb after the try, so the watchdog restarts the Q2
        (ffs/'ep1').unlink()
        (run/'log').write_text('')
        env['Q2ADB_BACK_S'] = '1'
        r = gadget('audio', 'try', '1')
        expect('audio try with USB adb never back: the watchdog restarts the Q2', r.returncode == 0
               and until(lambda: 'reboot' in (run/'log').read_text(), 20) and said('RESTARTING THE Q2')
               and said('ep1 no'), log.read_text()[-2000:])
        # the boot after it: /tmp and configfs empty, setup from S90play: adb alone, so no second restart
        shutil.rmtree(g)
        for f in run.iterdir():
            if f.name not in ('mounts', 'log'): f.unlink()
        (run/'log').write_text('')
        r = gadget('setup')
        expect('the boot after the watchdog\'s restart: adb alone, nothing of the try left', r.returncode == 0 and audio_off()
               and not (data/'q2adb.audio').exists())
        # parked (stock's storage has the controller) when the try's time is up: no restart
        log.write_text('')
        (run/'q2adb.parked').write_text('')
        r = gadget('audio', 'try', '1')
        expect('audio try while parked: only remembered for the unpark, and the watchdog restarts nothing', r.returncode == 0
               and until(lambda: said('no restart'), 20) and 'reboot' not in (run/'log').read_text()
               and said('parked: only remembered'), log.read_text()[-1500:])
        (run/'q2adb.parked').unlink()
        (ffs/'ep1').write_text('')
        expect('audio with no word, or try with no number, is refused', gadget('audio').returncode == 2
               and gadget('audio', 'try', 'x').returncode == 2)


def uac_tone_checks():
    """tools/uac_tone.sh, the USB audio desk test (docs/usb-audio.md#the-desk-test), under the host's sh: a stand-in
    /proc (the gadget's card and PCM, a fixed uptime of 100 s), a dmesg that prints a file, an aplay that notes its
    calls. On a Q2 up a while the kernel's ring buffer is full, so a new line pushes the oldest out and the count
    of lines stays the same; device run 5 missed the computer's stream START so, and took a STOP line's rate for
    a recording. $UAC_TONE runs another copy of the script (a broken one must fail these)."""
    script = os.environ.get('UAC_TONE', str(ROOT/'tools/uac_tone.sh'))
    old = [f'[ {i:10.6f}] hyn: touch {i}' for i in range(40)]
    def line(t, text):
        return f'[ {t:10.6f}] {text}'
    start = line(101, 'UAC2: playback stream START, srate=48000')
    stop = line(105, 'UAC2: playback stream STOP, srate=48000')
    def run(wait, lines=(), after=0.0, stop_on_play=False, grow=False):
        with tempfile.TemporaryDirectory(prefix='q2-uac-tone-') as d:
            d = pathlib.Path(d)
            proc, tmp, bin = d/'proc', d/'tmp', d/'bin'
            sub = proc/'asound/card1/pcm0p/sub0'
            for p in (sub, tmp, bin):
                p.mkdir(parents=True)
            (proc/'uptime').write_text('100.00 50.00\n')
            (proc/'asound/cards').write_text(' 0 [ecodecsoundcard]: x\n 1 [UAC2Gadget     ]: UAC2_Gadget - UAC2_Gadget\n')
            (sub/'status').write_text('state: RUNNING\nhw_ptr : 0\nappl_ptr : 0\navail : 0\n')
            (sub/'hw_params').write_text('rate: 48000 (48000/1)\n')
            (tmp/'tone-48000.wav').write_bytes(b'RIFF')
            ring, calls = d/'dmesg.txt', d/'aplay.calls'
            ring.write_text('\n'.join(old) + '\n')
            def push(new):  # each new line pushes the oldest out, unless the ring is still filling (grow)
                cur = ring.read_text().splitlines()
                (d/'ring.n').write_text('\n'.join(cur[0 if grow else len(new):] + list(new)) + '\n')
                os.replace(d/'ring.n', ring)
            stubs = {'dmesg': f'cat "{ring}"', 'usleep': 'sleep 0.1',
                     'aplay': f'echo "$*" >> "{calls}"\n' + (f'tail -n +2 "{ring}" > "{d}/r.n"; echo "{stop}" >> "{d}/r.n"; '
                                                             f'mv "{d}/r.n" "{ring}"\n' if stop_on_play else '') +
                              'sleep 1.2'}  # past the script's 500 ms check of the stream
            for name, body in stubs.items():
                (bin/name).write_text('#!/bin/sh\n' + body + '\n')
                (bin/name).chmod(0o755)
            timer = threading.Timer(after, push, (lines,))
            if lines:
                timer.start()
            env = dict(os.environ, PATH=f'{bin}:{os.environ["PATH"]}', UAC_PROC=str(proc), UAC_TMP=str(tmp),
                       UAC_LOG=str(d/'log.txt'))
            r = subprocess.run(['sh', script, str(wait), 'tone'], env=env, capture_output=True, text=True, timeout=60)
            timer.cancel()
            return r, calls.read_text().splitlines() if calls.exists() else []
    r, played = run(5, (line(101, 'UAC2: u_audio_set_playback_srate: host set srate 48000'), start), after=1.5)
    expect('uac_tone: a START in a full ring buffer starts the plays, at its rate',
           'the computer records at 48000 Hz' in r.stdout and len(played) == 2
           and all(c.endswith('tone-48000.wav') for c in played), r.stdout + str(played))
    expect('uac_tone: the count of kernel lines is logged', f'kernel lines {len(old)}' in r.stdout, r.stdout)
    r, played = run(3, (line(101, 'UAC2: playback stream STOP, srate=48000'),), after=1.0)
    expect('uac_tone: a STOP alone is not a recording', r.returncode == 1 and not played
           and 'did not start recording within 3 s (last stream line: STOP 48000)' in r.stdout, r.stdout + str(played))
    r, played = run(3, (line(101, 'UAC2: playback stream STOP, srate=48000'),), after=1.0, grow=True)
    expect('uac_tone: a STOP alone is not a recording while the ring buffer fills', r.returncode == 1 and not played,
           r.stdout + str(played))
    r, played = run(2, (line(99, 'UAC2: playback stream START, srate=48000'),), after=0.2)
    expect('uac_tone: a START stamped before it began does not count', r.returncode == 1 and not played,
           r.stdout + str(played))
    r, played = run(2, (start,), after=0.5, stop_on_play=True)
    expect('uac_tone: a STOP while playing is logged and no play follows it', len(played) == 1
           and 'stream stopped by 100.00: STOP 48000' in r.stdout
           and 'play 2: the stream is not running (last stream line: STOP 48000)' in r.stdout, r.stdout + str(played))


def wrapper_checks():
    from build import adb_wrapper
    with tempfile.TemporaryDirectory(prefix='q2-adb-wrap-') as d:
        d = pathlib.Path(d)
        log = d/'log'
        for name, rc in (('stock', 3), ('gadget', 0)):
            (d/name).write_text(f'#!/bin/sh\necho "{name} $*" >> {log}\nexit {rc}\n')
            (d/name).chmod(0o755)
        text = adb_wrapper('storage_config.sh').decode().replace('/usr/bin/storage_config_stock.sh', str(d/'stock'))
        text = text.replace('/usr/bin/q2adb-gadget', str(d/'gadget'))
        (d/'wrap').write_text(text)
        for args, calls in ((['start', '/dev/mmcblk0p1'], ['gadget park', 'stock start /dev/mmcblk0p1']),
                            (['stop'], ['stock stop', 'gadget unpark'])):
            log.write_text('')
            r = subprocess.run(['sh', str(d/'wrap'), *args])
            got = log.read_text().splitlines()
            expect(f'storage_config.sh {args[0]}: {", then ".join(calls)}; stock\'s status kept', got == calls and r.returncode == 3, got)


class TcpAdbd:
    """`adbd --tcp PORT` under qemu-mipsel in bwrap, on the network this machine has (no USB stand-in), with
    the card at /mnt/mmc, /mnt/data from data and /tmp from work/tmp."""
    def __init__(self, qemu, adbd, work, card, data, port):
        env = dict(os.environ)
        preload = env.pop('LD_PRELOAD', None)
        keep = ['--setenv', 'LD_PRELOAD', preload] if preload else []
        mmc = ['--bind', str(card), '/mnt/mmc'] if card else ['--dir', '/mnt/mmc']
        self.p = subprocess.Popen(['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc', '--tmpfs', '/mnt',
                                   *mmc, '--bind', str(data), '/mnt/data', '--bind', str(work/'tmp'), '/tmp', *keep,
                                   '--die-with-parent', qemu[0], '-U', 'LD_PRELOAD', *qemu[1:], str(adbd), '--tcp', str(port)],
                                  env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import socket
        end = time.monotonic() + 20
        while time.monotonic() < end and self.p.poll() is None:
            try:
                socket.create_connection(('127.0.0.1', port), 1).close()
                break
            except OSError:
                time.sleep(.1)
        if self.p.poll() is not None:
            raise RuntimeError(f'adbd --tcp ended at start ({self.p.returncode})')

    def stop(self):
        self.p.kill()
        self.p.wait()


def free_port():
    import socket
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def tcp_checks(qemu, adbd):
    """adb over Wi-Fi (docs/adb.md#adb-over-wi-fi): the MIPS adbd's TCP mode, driven by tools/vamp.py's client."""
    import socket
    import io
    import vamp, q2remote
    print('adbd --tcp, through vamp...')
    for bad in ('0', '70000', 'x'):
        r = subprocess.run([*qemu, str(adbd), '--tcp', bad], capture_output=True, timeout=30)
        expect(f'adbd --tcp {bad} is refused (exit 2)', r.returncode == 2, r.returncode)
    key, other = vamp.Key.generate(), vamp.Key.generate()
    with tempfile.TemporaryDirectory(prefix='q2-adb-tcp-') as d:
        work = pathlib.Path(d)
        card, data = work/'card', work/'data'
        for p in (card, data, work/'tmp/files'):
            p.mkdir(parents=True)
        (data/'adb_keys').write_text(key.public_line() + '\n')
        port = free_port()
        dev = TcpAdbd(qemu, adbd, work, card, data, port)
        try:
            target = {'name': '127.0.0.1', 'ip': '127.0.0.1', 'port': port, 'id': ''}
            used = work/'tmp/q2remote.used'
            expect('nothing marks a use before a computer is in', not used.exists())
            q = vamp.Device(dict(target), key)
            conn = q.connection()
            expect('a trusted key connects over TCP (CNXN device::)', conn.device.startswith('device'), conn.device)
            expect('the TCP adbd keeps 4096-byte messages', conn.max == 4096, conn.max)
            expect('it opens no UI socket: the USB adbd\'s stays its own', not (work/'tmp/q2adb-ui').exists())
            first = used.stat().st_ino if used.exists() else None
            expect('a computer let in renames /tmp/q2remote.used into place (Smart Wi-Fi\'s hold)', first is not None
                   and used.read_text().strip().isdigit())
            expect('exec: over TCP', q.exec_out('echo q2') == b'q2\n')
            expect('each service opened renames it again', used.exists() and used.stat().st_ino != first)
            q2remote.TRANSPORT = q
            try:
                expect('q2remote.run over vamp: output and exit status', q2remote.run('echo out; exit 7') == (b'out\n', 7))
                expect('q2remote.shell over vamp: stdin through a file', q2remote.shell('cat; echo .', b'from stdin\n') == b'from stdin\n.\n')
                long = 'echo ' + 'y' * 6000
                expect('a command past an OPEN\'s 4096 bytes runs whole', q.shell(long) == b'y' * 6000 + b'\n')
                expect('the temporary files are gone', not list((work/'tmp').glob('vamp-*')))
                out = io.BytesIO()
                rc = vamp.run_streamed(q, 'printf "a\\nb"; exit 3', out)
                expect('vamp shell COMMAND: streamed, its status kept, its status line gone', (rc, out.getvalue()) == (3, b'a\nb'),
                       (rc, out.getvalue()))
            finally:
                q2remote.TRANSPORT = None
            body = secrets.token_bytes(300000)
            src = work/'src.bin'
            src.write_bytes(body)
            start = time.monotonic()
            path = vamp.push(q, src, '/tmp/files/')
            took = time.monotonic() - start
            expect('vamp push: the file whole, renamed from .part', path == '/tmp/files/src.bin'
                   and (work/'tmp/files/src.bin').read_bytes() == body and not (work/'tmp/files/src.bin.part').exists())
            print(f'     (300 KB pushed in {took:.2f} s under qemu)')
            vamp.pull(q, '/tmp/files/src.bin', work/'back.bin')
            expect('vamp pull: byte for byte', (work/'back.bin').read_bytes() == body)
            try:
                q.call(lambda c: c.push([b'x'], '/proc/nope/x'))
                failed = None
            except vamp.AdbError as e:
                failed = str(e)
            expect('a refused push carries adbd\'s reason', failed is not None and 'failed' in failed, failed)
            q.drop()
            try:
                vamp.Connection(vamp.TcpTransport('127.0.0.1', port), other, auth_wait=3)
                refused = None
            except vamp.Unauthorized as e:
                refused = str(e)
            expect('an unknown key is refused over TCP, with how to trust it', refused is not None and 'USB' in refused, refused)
            expect('...and nothing on the Q2 was asked', not (work/'tmp/q2adb-ui').exists())
            (card/'adb_keys').write_text(other.public_line() + '\n')
            expect('the card\'s adb_keys lets a computer in over TCP too',
                   vamp.Connection(vamp.TcpTransport('127.0.0.1', port), other).device.startswith('device'))
            held = [socket.create_connection(('127.0.0.1', port), 5) for _ in range(8)]
            time.sleep(1)
            ninth = socket.create_connection(('127.0.0.1', port), 5)
            ninth.settimeout(5)
            try:
                closed = ninth.recv(1) == b''
            except OSError:
                closed = True
            expect('a ninth connection is closed at once (8 at most)', closed)
            for s in held + [ninth]:
                s.close()
            ok, last = False, None
            for _ in range(20):
                time.sleep(.5)
                try:
                    ok = vamp.Connection(vamp.TcpTransport('127.0.0.1', port), key).device.startswith('device')
                    break
                except (OSError, vamp.AdbError) as e:
                    last = e
            expect('once they go, a computer gets in again (the count comes down)', ok, repr(last))
            expect('adbd --tcp is still running at the end', dev.p.poll() is None)
        finally:
            dev.stop()


def wifi_checks():
    """q2adb-wifi (patch/adb_wifi.sh) under this machine's sh, with adbd and q2adb-mdns stand-ins that record
    their arguments and run until killed."""
    script = ROOT/'patch/adb_wifi.sh'
    print('q2adb-wifi...')
    with tempfile.TemporaryDirectory(prefix='q2-adb-wifi-') as d:
        d = pathlib.Path(d)
        for name in ('adbd', 'mdns'):
            (d/name).write_text(f'#!/bin/sh\necho "$$ $*" >> {d}/{name}.log\nexec sleep 1000\n')
            (d/name).chmod(0o755)
        env = dict(os.environ, Q2ADB_KEYS=str(d/'adb_keys'), Q2ADB_CARD_KEYS=str(d/'card_keys'),
                   Q2ADB_CONFIG=str(d/'config.ini'), Q2ADB_EVERY='0.2', Q2ADB_ADBD=str(d/'adbd'), Q2ADB_MDNS=str(d/'mdns'))
        p = subprocess.Popen(['sh', str(script)], env=env)

        def running(name):
            log = d/f'{name}.log'
            if not log.exists():
                return []
            return [line.split()[0] for line in log.read_text().splitlines()
                    if os.path.exists(f'/proc/{line.split()[0]}') and
                    open(f'/proc/{line.split()[0]}/cmdline', 'rb').read().startswith(b'sleep')]

        def settle():
            time.sleep(1)
        try:
            settle()
            expect('no trusted key: no port open, nothing advertised', running('adbd') == [] and running('mdns') == [])
            (d/'adb_keys').write_text('')
            settle()
            expect('an empty adb_keys trusts no one: still nothing', running('adbd') == [])
            (d/'adb_keys').write_text('AAAA test@laptop\n')
            settle()
            args = (d/'adbd.log').read_text().split() if (d/'adbd.log').exists() else []
            expect('a key trusted: adbd --tcp 5555 and q2adb-mdns --port 5555 start', len(running('adbd')) == 1 and
                   len(running('mdns')) == 1 and args[1:] == ['--tcp', '5555'] and
                   (d/'mdns.log').read_text().split()[1:] == ['--port', '5555'], args)
            first = running('adbd')[0]
            os.kill(int(first), 9)
            settle()
            expect('an adbd that ends is started again', len(running('adbd')) == 1 and running('adbd')[0] != first)
            (d/'config.ini').write_text('[Q2POD]\nADBWIFI=0\n')
            settle()
            expect('ADBWIFI=0 (Developer access off): both stopped', running('adbd') == [] and running('mdns') == [])
            (d/'config.ini').write_text('[Q2POD]\nADBWIFI=1\n')
            (d/'adb_keys').unlink()
            (d/'card_keys').write_text('AAAA test@laptop\n')
            settle()
            expect('the card\'s adb_keys alone counts too', len(running('adbd')) == 1)
            p.terminate()
            p.wait(5)
            time.sleep(.3)
            expect('SIGTERM stops q2adb-wifi and both it started', running('adbd') == [] and running('mdns') == [])
        finally:
            if p.poll() is None:
                p.kill()
            for name in ('adbd', 'mdns'):
                for pid in running(name):
                    os.kill(int(pid), 9)


def mdns_checks(qemu, build):
    """BUILD's q2adb-mdns under qemu-mipsel, found by vamp's browse by its Bluetooth name. On this machine's
    first IPv4 interface, not loopback: qemu-user refuses IP_MULTICAST_IF, so its answers leave by the route."""
    import vamp
    out = subprocess.run(['ip', '-4', '-o', 'addr', 'show', 'scope', 'global'], capture_output=True, text=True).stdout.split()
    if 'inet' not in out:
        print('skip q2adb-mdns: no IPv4 interface but loopback')
        return
    ifname, addr = out[1], out[out.index('inet') + 1].split('/')[0]
    with tempfile.TemporaryDirectory(prefix='q2-adb-mdns-') as d:
        d = pathlib.Path(d)
        (d/'mnt/data').mkdir(parents=True)
        (d/'mnt/data/config.ini').write_text('[Q2POD]\nBTNAME=Desk Q2\n')
        m = d/'q2adb-mdns'
        m.write_bytes(subprocess.run(['unsquashfs', '-cat', str(build/'rootfs.squashfs'), 'usr/bin/q2adb-mdns'],
                                     capture_output=True, check=True).stdout)
        m.chmod(0o755)
        p = subprocess.Popen([*qemu, str(m), '--if', ifname, '--root', str(d)], stderr=subprocess.DEVNULL)
        try:
            time.sleep(2)
            found = vamp.browse(3.0, [addr])
            ident = (d/'mnt/data/coda-rc/id').read_bytes().hex() if (d/'mnt/data/coda-rc/id').exists() else None
            expect('q2adb-mdns: vamp finds the Q2 by its Bluetooth name, port 5555, its id Car Remote\'s',
                   [(q['name'], q['ip'], q['port'], q['id']) for q in found] == [('Desk Q2', addr, 5555, ident)], found)
        finally:
            p.terminate()
            p.wait(10)
        expect('q2adb-mdns ends on SIGTERM (after its goodbye)', p.returncode == 0, p.returncode)


def main(argv):
    args = list(argv)
    qemu = shutil.which('qemu-mipsel')
    if '--qemu' in args:
        i = args.index('--qemu'); qemu = args[i + 1]; del args[i:i + 2]
    gadget_checks()
    uac_tone_checks()
    wrapper_checks()
    wifi_checks()
    if args:  # BUILD's adbd, with BUILD's libraries
        build = pathlib.Path(args[0])
        image = build/'rootfs.squashfs'
        adbd = build/'adbd'
        adbd.write_bytes(subprocess.run(['unsquashfs', '-cat', str(image), 'usr/bin/adbd'], capture_output=True, check=True).stdout)
        adbd.chmod(0o755)  # qemu-user's loader refuses a file without an execute bit, and says nothing
        libs = pathlib.Path.home()/'.cache/q2-adb-test'/hashlib.sha256(image.read_bytes()).hexdigest()[:16]
        if not (libs/'lib').is_dir():
            shutil.rmtree(libs, ignore_errors=True)
            libs.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['unsquashfs', '-no-xattrs', '-d', str(libs), str(image), 'lib'], check=True, capture_output=True)
    else:  # the cached one, with the toolchain's glibc (the rootfs's own build)
        import thirdparty
        adbd = thirdparty.build('adbd')/'usr/bin/adbd'
        libs = thirdparty.toolchain().parent/'sysroot/mipsel-r2-hard-nan2008'
    if not qemu:
        print('no qemu-mipsel: adbd not run')
        failures.append('qemu-mipsel')
    else:
        adbd_checks([qemu, '-L', str(libs)], adbd)
        tcp_checks([qemu, '-L', str(libs)], adbd)
        if args:
            mdns_checks([qemu, '-L', str(libs)], build)
    print(f'{len(failures)} failed' if failures else 'all passed')
    return 1 if failures else 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
