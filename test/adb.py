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
usleep stubbed: the gadget it makes, park, unpark, and that a parked gadget is never bound. Needs bwrap and a
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
        self.p = subprocess.Popen(['bwrap', '--ro-bind', '/', '/', '--dev', '/dev', '--proc', '/proc',
                                   '--tmpfs', '/mnt', *mmc, '--bind', str(data), '/mnt/data', '--dir', '/dev/usb-ffs',
                                   '--bind', str(self.ffs), '/dev/usb-ffs/adb', '--bind', str(work/'tmp'), '/tmp',
                                   '--die-with-parent', qemu, str(adbd)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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
    # configfs fills a new gadget's directory itself; the stand-in tree gets the parts setup writes into
    'mkdir': '/bin/mkdir "$@" || exit; for a; do case "$a" in */usb_gadget/q2adb) /bin/mkdir -p "$a/os_desc" "$a/strings" "$a/configs" "$a/functions"; : > "$a/UDC";; esac; done',
}


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
        env = dict(os.environ, PATH=f'{bin}:{os.environ["PATH"]}', Q2ADB_CONFIGFS=str(cfs), Q2ADB_UDC=str(udc),
                   Q2ADB_FFS=str(ffs), Q2ADB_RUN=str(run))
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

        r = gadget('park')
        expect('park: off the controller, configfs unmounted, parked',
               r.returncode == 0 and read('UDC') == '' and str(cfs) not in (run/'mounts').read_text().split() and (run/'q2adb.parked').exists(), r.stderr)
        r = gadget('bind')
        expect('bind while parked (adbd restarting under stock\'s gadget) binds nothing and mounts nothing',
               r.returncode == 0 and read('UDC') == '' and str(cfs) not in (run/'mounts').read_text().split(), r.stderr)
        r = gadget('unpark')
        expect('unpark: configfs mounted again, q2adb on the controller',
               r.returncode == 0 and read('UDC') == '13500000.otg' and str(cfs) in (run/'mounts').read_text().split()
               and not (run/'q2adb.parked').exists(), r.stderr)
        expect('a bound q2adb is left as it is', gadget('bind').returncode == 0 and read('UDC') == '13500000.otg')
        expect('an unknown command is refused', gadget('frob').returncode == 2)


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


def main(argv):
    args = list(argv)
    qemu = shutil.which('qemu-mipsel')
    if '--qemu' in args:
        i = args.index('--qemu'); qemu = args[i + 1]; del args[i:i + 2]
    gadget_checks()
    wrapper_checks()
    if args:
        build = pathlib.Path(args[0])
        adbd = build/'adbd'
        out = subprocess.run(['unsquashfs', '-cat', str(build/'rootfs.squashfs'), 'usr/bin/adbd'], capture_output=True, check=True).stdout
        adbd.write_bytes(out)
    else:
        import thirdparty
        adbd = thirdparty.build('adbd')/'usr/bin/adbd'
    if not qemu:
        print('no qemu-mipsel: adbd not run')
        failures.append('qemu-mipsel')
    else:
        adbd_checks(qemu, adbd)
    print(f'{len(failures)} failed' if failures else 'all passed')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
