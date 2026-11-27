#!/usr/bin/env python3
"""Vamp OS's base image (tools/vampos.py, docs/vamp-os-base.md): what it holds, and how it boots under the emulator.

    test/os_image.py BUILD [--zip ZIP [--no-boot] [--qemu QEMU_MIPSEL_STATIC] [--keep DIR]] [--perturb WHAT]

BUILD is a `tools/vampos.py build --out` folder. Without --zip only the image and the tree are checked:
  - the pins: os/package's versions and hashes are tools/player.py's, tools/thirdparty.py's and tools/ui.py's (at Q2UI_COMMIT);
  - the image fits mtd2, holds no /vendor and no file whose SHA-256 the vendor manifest lists (nothing of
    Shanling's), nothing that formats flash, and /module_driver and /firmware point into /vendor;
  - every ELF file is stock's ABI: MIPS32R2 little-endian o32, NaN 2008, hard float with a 64-bit FPU,
    programs on /lib/ld-linux-mipsn8.so.1;
  - init's table: rcS as the one sysinit, then runsvdir over /etc/sv, which holds q2player and q2ui.
With --zip it composes the image with the vendor set (tools/vampos.py compose), checks /vendor against the
manifest, and (unless --no-boot) boots it: BusyBox init as PID 1 of a bwrap sandbox, every program run by
QEMU_MIPSEL_STATIC (by default `tools/vampos.py qemu`'s, the pinned one for this machine), found through the
host's binfmt_misc entry for mipsel at its "-binfmt-P" interpreter path. No kernel is emulated, so the commands that would reach it (mount, mknod, mdev, insmod,
ubiattach) are stand-ins that record their arguments; the framebuffer is a file and the keys are FIFOs, as
in the emulator (~/q2-emu). The boot passes when the record shows rcS's order (file systems, mdev, the data
partition, every module of Shanling's script in its order with its parameters, the framebuffer) before
either service starts, q2ui draws a frame, q2player connects to q2playd, and a song asked for over Q2PLAY/1
reaches q2player and its end comes back; then adb over Wi-Fi's key rules, and q2sysd's battery file, a press of the
power key turning the screen off and on, and its hold starting init's shutdown.

--card-boot boots it as today's Vamp starts it from the card (patch/q2vampos.sh): /mnt/data already mounted and
/proc/modules listing every vendor module, so rcS must keep the one and not run the module script again.

--perturb breaks one thing in the unpacked copy, to show the check that should catch it goes red: `stock-file`
(a vendor file inside the base image), `abi` (one program's NaN bit cleared), `order` (rcS says it is done
before the modules), `module` (the framebuffer's insmod dropped from the copy's module script), `adb-open` (the
Wi-Fi adbd started with no key check), `adb-off` (no Wi-Fi adbd), `playd-off` (no q2playd), `sysd-off` (no q2sysd).
"""
import argparse
import hashlib
import os
import pathlib
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import player  # noqa: E402
import thirdparty  # noqa: E402
import vampos as vamp_os  # noqa: E402
import vamp  # noqa: E402

NO_PRELOAD = {k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'}
FAILS = []
PERTURB_ZIP = None


def check(ok, what):
    print(('ok    ' if ok else 'FAIL  ') + what, flush=True)
    if not ok:
        FAILS.append(what)
    return ok


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def hashes(mk_dir):
    """{file name: sha256} of an os/package .hash file"""
    out = {}
    for f in mk_dir.glob('*.hash'):
        for line in f.read_text().splitlines():
            if line.startswith('sha256'):
                _, digest, name = line.split()
                out[name] = digest
    return out


def check_pins():
    pkg = ROOT/'os/package'
    mk = lambda p: (pkg/p/f'{p}.mk').read_text()  # noqa: E731
    version, url, digest = player.FFMPEG
    check(f'Q2PLAYER_FFMPEG_VERSION = {version}\n' in mk('q2player-ffmpeg')
          and hashes(pkg/'q2player-ffmpeg').get(url.rsplit('/', 1)[1]) == digest, f'FFmpeg {version} as tools/player.py pins it')
    version, url, digest = thirdparty.SOURCES['mbedtls']
    check(f'Q2_MBEDTLS_VERSION = {version}\n' in mk('q2-mbedtls')
          and hashes(pkg/'q2-mbedtls').get(url.rsplit('/', 1)[1]) == digest, f'mbedTLS {version} as tools/thirdparty.py pins it')
    version, url, digest = thirdparty.SOURCES['adbd']
    got = hashes(pkg/'q2-adbd')
    deb_url, deb_digest = thirdparty.ADBD_DEBIAN
    check(f'Q2_ADBD_VERSION = {version}\n' in mk('q2-adbd') and got.get(url.rsplit('/', 1)[1]) == digest
          and got.get(deb_url.rsplit('/', 1)[1]) == deb_digest and deb_url in mk('q2-adbd')
          and all(got.get(n) == d for n, d in thirdparty.ADBD_MINCRYPT_FILES.items())
          and thirdparty.ADBD_MINCRYPT.rstrip('/') in mk('q2-adbd'), f'adbd {version} as tools/thirdparty.py pins it')
    got = hashes(pkg/'q2ui-stb')
    # tools/ui.py is on q2ui's branch: its STB and STB_IMAGE at the commit the image builds
    text = subprocess.check_output([*vamp_os.GIT, 'show', f'{vamp_os.Q2UI_COMMIT}:tools/ui.py'], text=True)
    pins = re.findall(r"^STB(?:_IMAGE)? = \('[^']*', '([^']+)',\s*'([0-9a-f]{64})'\)", text, re.M)
    check(len(pins) == 2, 'tools/ui.py names two stb headers')
    for url, digest in pins:
        commit = url.split('/')[-2]
        check(f'Q2UI_STB_VERSION = {commit}\n' in mk('q2ui-stb') and got.get(url.rsplit('/', 1)[1]) == digest,
              f'{url.rsplit("/", 1)[1]} as tools/ui.py pins it')


def unpack(image, dest):
    # -n: no progress; device nodes need root and are not checked, so their errors are ignored
    subprocess.run(['unsquashfs', '-q', '-n', '-ignore-errors', '-no-xattrs', '-d', dest, image],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return pathlib.Path(dest)


def elf_facts(path):
    """(e_flags, is_program, interpreter) of a 32-bit little-endian MIPS ELF, else None"""
    with open(path, 'rb') as f:
        head = f.read(52)
        if len(head) < 52 or head[:4] != b'\x7fELF':
            return None
        if head[4] != 1 or head[5] != 1 or struct.unpack_from('<H', head, 18)[0] != 8:
            return ('not o32 little-endian MIPS', None, None)
        e_type, = struct.unpack_from('<H', head, 16)
        e_flags, = struct.unpack_from('<I', head, 36)
        phoff, = struct.unpack_from('<I', head, 28)
        phentsize, phnum = struct.unpack_from('<HH', head, 42)
        interp = None
        for i in range(phnum):
            f.seek(phoff + i * phentsize)
            p_type, p_offset, _, _, p_filesz = struct.unpack('<IIIII', f.read(20))
            if p_type == 3:
                f.seek(p_offset)
                interp = f.read(p_filesz).rstrip(b'\0').decode()
        return (e_flags, e_type == 2 or (e_type == 3 and interp is not None), interp)


def check_image(build, fs, manifest, perturb=None):
    if perturb == 'stock-file':
        src = pathlib.Path(tempfile.mkdtemp(dir='/tmp'))
        vamp_os.compose(build/'vampos.squashfs', src/'c.squashfs', zip_path=PERTURB_ZIP)
        ko = unpack(src/'c.squashfs', src/'fs')/'vendor/module_driver/soc_fb.ko'
        (fs/'usr/lib/soc_fb.ko').write_bytes(ko.read_bytes())
    if perturb == 'abi':
        b = bytearray((fs/'usr/bin/q2ui').read_bytes())
        b[37] &= ~0x04  # e_flags (offset 36, little-endian) bit 0x400: EF_MIPS_NAN2008
        (fs/'usr/bin/q2ui').write_bytes(bytes(b))
    image = build/'vampos.squashfs'
    size = image.stat().st_size
    check(size <= vamp_os.ROOTFS_ROOM, f'image is {size:,} bytes, within mtd2\'s {vamp_os.ROOTFS_ROOM:,}')
    check(not (fs/'vendor').exists(), 'no /vendor in the base image')
    check(os.readlink(fs/'module_driver') == '/vendor/module_driver' and os.readlink(fs/'firmware') == '/vendor/firmware',
          '/module_driver and /firmware point into /vendor')
    stock = {d for d, _ in manifest.values()}
    found, elves, bad = [], 0, []
    for f in fs.rglob('*'):
        if f.is_symlink() or not f.is_file():
            continue
        rel = f.relative_to(fs).as_posix()
        if sha256(f) in stock:
            found.append(rel)
        facts = elf_facts(f)
        if facts is None:
            continue
        elves += 1
        flags, program, interp = facts
        if isinstance(flags, str):
            bad.append(f'{rel}: {flags}')
            continue
        # EF_MIPS_ARCH_32R2, EF_MIPS_NAN2008, and the o32 ABI field clear of other ABIs
        if flags & 0xf0000000 != 0x70000000 or not flags & 0x400 or flags & 0xf000 not in (0x1000, 0):
            bad.append(f'{rel}: e_flags {flags:#x}')
        if program and interp != '/lib/ld-linux-mipsn8.so.1':
            bad.append(f'{rel}: interpreter {interp}')
    check(not found, f'no file of the vendor set in the image{": " + ", ".join(found) if found else ""}')
    check(elves > 10 and not bad, f'all {elves} ELF files MIPS32R2 o32 NaN 2008 on ld-linux-mipsn8{": " + "; ".join(bad[:5]) if bad else ""}')
    for name in ('usr/bin/q2player', 'usr/bin/q2ui', 'lib/libc.so.6'):
        fp = subprocess.run(['readelf', '-A', fs/name], capture_output=True, text=True).stdout
        check('FP ABI: Hard float (32-bit CPU, 64-bit FPU)' in fp, f'{name}: hard float, 64-bit FPU')
    tools = [n for n in ('ubimkvol', 'ubiformat', 'flash_erase', 'flash_eraseall', 'ubirmvol', 'ubiupdatevol', 'nandwrite')
             if any(fs.glob(f'*/{n}')) or any(fs.glob(f'usr/*/{n}'))]
    check(not tools, f'nothing that erases or formats flash{": " + ", ".join(tools) if tools else ""}')
    check(os.path.basename(os.readlink(fs/'sbin/init')) == 'busybox' and os.path.basename(os.readlink(fs/'linuxrc')) == 'busybox',
          '/sbin/init and /linuxrc are BusyBox')
    table = [l for l in (fs/'etc/inittab').read_text().splitlines() if l and not l.startswith('#')]
    actions = [l.split(':')[2] for l in table]
    check(actions.count('sysinit') == 1 and table[0] == '::sysinit:/etc/init.d/rcS'
          and '::respawn:/usr/bin/runsvdir -P /etc/sv' in table, 'inittab: rcS, the one sysinit, then runsvdir over /etc/sv')
    services = sorted(p.name for p in (fs/'etc/sv').iterdir())
    check(services == ['adb-wifi', 'adbd', 'dhcp', 'q2playd', 'q2player', 'q2sysd', 'q2ui', 'wlan'] and all(os.access(fs/'etc/sv'/s/'run', os.X_OK) for s in services),
          f'services: {", ".join(services)}')
    check(not (fs/'etc/init.d').exists() or sorted(p.name for p in (fs/'etc/init.d').iterdir()) == ['rcK', 'rcS'],
          '/etc/init.d holds rcS and rcK alone')


def expected_modules(fs):
    """[(module file, parameters)] in the order Shanling's script loads them, read from the vendor files"""
    out = []
    md = fs/'vendor/module_driver'
    for script in re.findall(r'^sh (\S+\.sh)', (md/'driver_default_init_script.sh').read_text(), re.M):
        text = (md/script).read_text().replace('\\\n', ' ')
        for m in re.finditer(r'^\s*insmod (\S+)(.*)$', text, re.M):
            out.append((m.group(1), m.group(2).split()))
    return out


# Each stand-in's line goes into the boot log itself (bound over /run/vamp/boot.log), so the commands and rcS's
# steps are in one file in the order they happened.
def wav(path, seconds=1, rate=44100):
    """A short 16-bit stereo WAV of silence and a tone, for the player to open."""
    import struct as st
    import math
    frames = b''.join(st.pack('<hh', *(2 * [int(8000 * math.sin(2 * math.pi * 440 * i / rate))])) for i in range(seconds * rate))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'RIFF' + st.pack('<I', 36 + len(frames)) + b'WAVEfmt ' + st.pack('<IHHIIHH', 16, 1, 2, rate, rate * 4, 4, 16)
                     + b'data' + st.pack('<I', len(frames)) + frames)


def play_check(fs, console, deadline):
    """(q2player connected, the song came back) through q2playd's Q2PLAY/1 socket in the booted image: a play of
    song 1 (/mnt/mmc/song1.wav), and q2playd's word that q2player played it out or could not (no sound card here)."""
    connected = False
    while time.monotonic() < deadline and not connected:
        connected = b'q2playd: the player connected' in console.read_bytes() if console.exists() else False
        time.sleep(0.5)
    sock = fs/'run/vamp/play.sock'
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(str(sock))
        f = s.makefile('rw')
        for line in ('Q2PLAY/1', 'play 0 0 1', '1'):
            f.write(line + '\n')
        f.flush()
        replies = [f.readline().strip(), f.readline().strip()]
        s.close()
    except OSError as e:
        return connected, False, repr(e)
    end = time.monotonic() + 30
    back = False
    while time.monotonic() < end and not back:
        back = re.search(rb'q2playd: song 1 (ended|did not play)', console.read_bytes()) is not None
        time.sleep(0.5)
    return connected, replies == ['Q2PLAY/1 ok', 'ok'] and back, replies


def sysd_checks(fs, power_key, log):
    """q2sysd in the booted image (docs/sysd.md): the battery's file from the CW2015's, a press of the power key on a
    device of its own (a FIFO: q2ui leaves an unnamed one alone) turning the screen off and on, and a hold of it
    starting init's shutdown (rcK's line in the boot log). The last check: the system goes down."""
    import struct as st
    bl = fs/'sys/class/backlight/lcd/bl_power'
    text = (fs/'run/vamp/battery').read_text() if (fs/'run/vamp/battery').exists() else ''
    check('level=64' in text and 'mv=3800' in text, f'q2sysd: /run/vamp/battery from the CW2015 ({text.split() or "none"})')
    fd = os.open(power_key, os.O_RDWR | os.O_NONBLOCK)

    def key(value):  # the guest's input_event: o32 with a 64-bit time_t gets the kernel's 32-bit longs, 16 bytes
        os.write(fd, st.pack('<IIHHi', int(time.time()), 0, 1, 116, value))

    def until(pred, s=5):
        end = time.monotonic() + s
        while time.monotonic() < end and not pred():
            time.sleep(0.1)
        return pred()
    key(1), time.sleep(0.1), key(0)
    check(until(lambda: bl.read_text().strip() == '4'), 'q2sysd: a press of the power key turns the screen off (bl_power 4)')
    key(1), time.sleep(0.1), key(0)
    check(until(lambda: bl.read_text().strip() == '0'), 'q2sysd: another turns it on again')
    key(1)
    check(until(lambda: b'rcK: stopping the services' in log.read_bytes(), 10), 'q2sysd: the power key held starts init\'s shutdown')
    os.close(fd)


def adb_open(port=5555):
    """Whether something accepts on 127.0.0.1:port (the sandbox shares this machine's network)."""
    import socket as sk
    try:
        sk.create_connection(('127.0.0.1', port), 1).close()
        return True
    except OSError:
        return False


def adb_checks(fs, closed_s=15, wait_s=30):
    """adb over Wi-Fi in the booted image (docs/vamp-os-base.md#adb): no port while no computer is trusted; a key put
    in /mnt/data/adb_keys opens it within q2adb-wifi's 10 s and lets that key in (exec: reads the image's own
    /etc/vamp-release), refuses another key, and closes the port again once the file is gone."""
    keys = fs/'mnt/data/adb_keys'
    end = time.monotonic() + closed_s
    opened = False
    while time.monotonic() < end and not opened:
        opened = adb_open()
        time.sleep(1)
    check(not opened, f'adb: no port 5555 for {closed_s} s while no computer is trusted')
    key, other = vamp.Key.generate(), vamp.Key.generate()
    keys.write_text(key.public_line() + '\n')
    target = {'name': '127.0.0.1', 'ip': '127.0.0.1', 'port': 5555, 'id': ''}
    got, end = None, time.monotonic() + wait_s
    while time.monotonic() < end and got is None:
        try:
            got = vamp.Device(dict(target), key).exec_out('cat /etc/vamp-release')
        except (OSError, vamp.AdbError, TimeoutError):
            time.sleep(1)
    want = (fs/'etc/vamp-release').read_bytes()
    check(got == want, f'adb: a trusted key gets in over Wi-Fi and reads the image\'s /etc/vamp-release{"" if got == want else ": " + repr(got)}')
    try:
        vamp.Device(dict(target), other).exec_out('id')
        refused = False
    except (vamp.Unauthorized, vamp.AdbError, OSError, TimeoutError):
        refused = True
    check(refused, 'adb: an untrusted key is refused')
    keys.unlink()
    end = time.monotonic() + wait_s
    while time.monotonic() < end and adb_open():
        time.sleep(1)
    check(not adb_open(), 'adb: the port closes once no computer is trusted')


STUB = '#!/bin/sh\necho "$(cut -d" " -f1 /proc/uptime) \\$ ${0##*/} $*" >> /emu/record\n'
STUBBED = ['bin/mount', 'bin/mknod', 'sbin/mdev', 'sbin/insmod', 'usr/sbin/ubiattach', 'bin/dmesg']


def boot(composed, qemu, keep=None, wait_s=90, logs=None, perturb=None, card=False, adb=True):
    work = pathlib.Path(keep or tempfile.mkdtemp(prefix='vamp-os-boot-', dir='/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    fs = unpack(composed, work/'fs')
    emu = work/'emu'
    emu.mkdir(exist_ok=True)
    (emu/'record').write_text('')
    (fs/'run/vamp').mkdir(parents=True, exist_ok=True)
    (fs/'run/vamp/boot.log').write_text('')
    want = expected_modules(fs)  # before any perturbation: what Shanling's scripts say
    if perturb == 'order':
        rcs = (fs/'etc/init.d/rcS').read_text()
        rcs = rcs.replace('    step "modules: start"\n', '    step "modules: start"\n    step "rcS: done, starting the services"\n')
        (fs/'etc/init.d/rcS').write_text(rcs)
    if perturb == 'adb-open':  # Wi-Fi adbd with no key check: an open port
        (fs/'etc/sv/adb-wifi/run').write_text('#!/bin/sh\nexec /usr/bin/adbd --tcp 5555\n')
    if perturb == 'sysd-off':  # no q2sysd
        (fs/'etc/sv/q2sysd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'playd-off':  # no q2playd
        (fs/'etc/sv/q2playd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'adb-off':  # no Wi-Fi adbd at all
        (fs/'etc/sv/adb-wifi/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'module':
        md = fs/'vendor/module_driver/driver_default_init_script.sh'
        md.write_text(md.read_text().replace('sh soc_fb.sh\n', ''))
    for s in STUBBED:
        p = fs/s
        check(p.is_symlink() or p.exists(), f'stand-in for /{s} replaces the image\'s')
        if p.is_symlink() or p.exists():
            p.unlink()
        p.write_text(STUB)
        p.chmod(0o755)
    fb = emu/'fb0'
    fb.write_bytes(bytes(320 * 375 * 4 * 2))
    inp = emu/'input'
    inp.mkdir(exist_ok=True)
    for n in (1, 2, 3):  # the keys and the wheel, by the emulator's numbering (q2ui's input.c); 2, q2sysd's power key alone
        if not (inp/f'event{n}').exists():
            os.mkfifo(inp/f'event{n}')
    import sqlite3
    db = sqlite3.connect(fs/'mnt/data/database.db')
    db.execute('create table songtable (name text, songer text, id integer, album text, track integer, fileurl text)')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?, ?)',
                   [(f'Song {i}', 'Artist', i, 'Album', i, f'/mnt/mmc/song{i}.wav') for i in range(1, 21)])
    db.commit()
    db.close()
    wav(fs/'mnt/mmc/song1.wav')
    for rel, text in (('sys/class/backlight/lcd/bl_power', '0'), ('sys/class/backlight/lcd/brightness', '200'),
                      ('sys/class/backlight/lcd/max_brightness', '255'), ('sys/devices/i2c-0/0-0062/cw2015_capacity', '64'),
                      ('sys/devices/i2c-0/0-0062/cw2015_voltage', '3800'), ('sys/hynitron_debug/hyngescontrol', '0')):
        (fs/rel).parent.mkdir(parents=True, exist_ok=True)
        (fs/rel).write_text(text + '\n')
    console = open(work/'console.log', 'wb')
    extra = []
    if card:
        # as today's Vamp hands the Q2 over from the card (patch/q2vampos.sh): /mnt/data mounted, stock's modules loaded
        (emu/'data').mkdir(exist_ok=True)
        (emu/'data/database.db').write_bytes((fs/'mnt/data/database.db').read_bytes())
        (emu/'modules').write_text(''.join(f'{ko[:-3]} 16384 0 - Live 0x00000000\n' for ko, _ in want))
        extra = ['--bind', emu/'data', '/mnt/data', '--ro-bind', emu/'modules', '/proc/modules']
    cmd = ['bwrap', '--unshare-pid', '--unshare-uts', '--unshare-ipc', '--as-pid-1', '--die-with-parent',
           '--bind', fs, '/', '--proc', '/proc', '--dev', '/dev', '--bind', fb, '/dev/fb0', '--bind', inp, '/dev/input',
           '--ro-bind', qemu, '/usr/libexec/qemu-binfmt/mipsel-binfmt-P', '--bind', emu, '/emu',
           '--bind', emu/'record', '/run/vamp/boot.log', *extra, '/sbin/init']
    proc = subprocess.Popen([str(c) for c in cmd], stdout=console, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=NO_PRELOAD)
    drew, played = False, None
    deadline = time.monotonic() + wait_s
    try:
        while time.monotonic() < deadline and proc.poll() is None and not drew:
            time.sleep(0.5)
            drew = any(fb.read_bytes())
        if proc.poll() is None:
            played = play_check(fs, work/'console.log', deadline)
        if adb and not card and proc.poll() is None:
            adb_checks(fs)
        if not card and proc.poll() is None:
            sysd_checks(fs, inp/'event2', emu/'record')
        if card:  # the services' first lines
            time.sleep(3)
    finally:
        proc.kill()
        proc.wait()
        console.close()
    check(drew, 'q2ui drew into the framebuffer')
    check(bool(played) and played[0], 'q2player connected to q2playd' + ('' if played else ': not reached'))
    check(bool(played) and played[1], 'a song asked for over Q2PLAY/1 reaches q2player through q2playd, and its end comes back'
          + ('' if played and played[1] else f': {played}'))
    lines = (emu/'record').read_text().splitlines()
    events = [(l.split(None, 1)[1][2:], 'cmd') if l.split(None, 1)[1].startswith('$ ') else (l.split(None, 1)[1], 'log')
              for l in lines if len(l.split(None, 1)) == 2]
    timeline = ''.join(l + '\n' for l in lines)
    (work/'timeline.txt').write_text(timeline)
    if logs:  # beside the build: the sandbox's /tmp is the container's, gone when it ends
        logs.mkdir(parents=True, exist_ok=True)
        (logs/'timeline.txt').write_text(timeline)
        (logs/'console.log').write_bytes((work/'console.log').read_bytes())
    order = [s for s, _ in events]

    def at(pred):
        return next((i for i, s in enumerate(order) if pred(s)), None)
    first = lambda prefix: at(lambda s: s.startswith(prefix))  # noqa: E731
    if card:
        check(not any(s.startswith('insmod ') for s in order) and first('modules: already loaded, not run again; not loaded: none') is not None,
              'card boot: the module script is not run again over loaded modules')
        check(first('data: already mounted') is not None and not any(s.startswith(('ubiattach', 'mount -t tmpfs -o mode=0755 tmpfs /mnt/data')) for s in order),
              'card boot: /mnt/data is kept as mounted')
        check(first("adbd: left to today's Vamp (card boot)") is not None and first("adb-wifi: left to today's Vamp (card boot)") is not None
              and first('adbd: start') is None and first('adb-wifi: start') is None, "card boot: adb is left to today's Vamp's adbd")
        idx = [first('rcS: done'), first('q2player: start'), first('q2ui: start')]
        check(None not in idx and idx[0] < min(idx[1:]), 'card boot: rcS done, then the services')
        print(f'boot: {logs or work} (console.log, timeline.txt)')
        return work
    loaded = [s.split()[1:] for s in order if s.startswith('insmod ')]
    wanted = [[ko, *params] for ko, params in want]

    def same(got, said):  # a parameter the script computes (rmem_manager.sh's, from /proc/cmdline) takes any value
        return len(got) == len(said) and all(g == w or ('$' in w and g.split('=')[0] == w.split('=')[0]) for g, w in zip(got, said))
    diff = next((f': {g} where the script says {w}' for g, w in zip(loaded, wanted) if not same(g, w)), '')
    check(len(loaded) == len(wanted) and not diff, f'insmod ran {len(loaded)} times, Shanling\'s {len(want)} modules in their order with their parameters{diff}')
    marks = [('mount proc', first('mount -t proc')), ('mount /dev', first('mount -t tmpfs -o mode=0755,nosuid tmpfs /dev')),
             ('mdev -s', first('mdev -s')), ('mdev -d', first('mdev -d')), ('data', first('data: ')),
             ('first insmod', first('insmod ')), ('last insmod', max((i for i, s in enumerate(order) if s.startswith('insmod ')), default=None)),
             ('modules done', first('modules: done')), ('fb0', first('fb0: present')), ('rcS done', first('rcS: done')),
             ('q2player', first('q2player: start')), ('q2ui', first('q2ui: start'))]
    missing = [n for n, i in marks if i is None]
    check(not missing, f'every step of the boot happened{": missing " + ", ".join(missing) if missing else ""}')
    if not missing:
        idx = [i for _, i in marks]
        services = min(idx[-2:])
        check(idx[:-2] == sorted(idx[:-2]) and services > idx[-3],
              'order: proc, /dev, mdev scan and daemon, data, modules, fb0, rcS done, then the services')
    print(f'boot: {logs or work} (console.log, timeline.txt)')
    return work


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('build', type=pathlib.Path)
    ap.add_argument('--zip', type=pathlib.Path)
    ap.add_argument('--qemu', type=pathlib.Path)
    ap.add_argument('--keep', type=pathlib.Path)
    ap.add_argument('--no-boot', action='store_true')
    ap.add_argument('--perturb', choices=['stock-file', 'abi', 'order', 'module', 'adb-open', 'adb-off', 'playd-off', 'sysd-off'])
    ap.add_argument('--card-boot', action='store_true', help='boot as patch/q2vampos.sh starts it: /mnt/data mounted, modules loaded')
    a = ap.parse_args()
    manifest, _ = vamp_os.read_manifest()
    check_pins()
    with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
        fs = unpack(a.build/'vampos.squashfs', pathlib.Path(tmp)/'fs')
        global PERTURB_ZIP
        PERTURB_ZIP = a.zip
        check_image(a.build, fs, manifest, a.perturb)
    if a.zip:
        composed = a.build/'composed.squashfs'
        if composed.exists():
            composed.unlink()
        vamp_os.compose(a.build/'vampos.squashfs', composed, zip_path=a.zip)
        with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
            fs = unpack(composed, pathlib.Path(tmp)/'fs')
            bad = [n for n, (d, _) in manifest.items() if not (fs/vamp_os.place(n)).is_file() or sha256(fs/vamp_os.place(n)) != d]
            check(not bad, f'/vendor holds the {len(manifest)} files of the manifest, byte for byte{": " + ", ".join(bad[:5]) if bad else ""}')
            check(composed.stat().st_size <= vamp_os.ROOTFS_ROOM, f'composed image is {composed.stat().st_size:,} bytes, within mtd2')
        if not a.no_boot:
            boot(composed, a.qemu or vamp_os.qemu(), a.keep, logs=a.build/('boot-card' if a.card_boot else 'boot'), perturb=a.perturb, card=a.card_boot)
    print(f'{len(FAILS)} failed' if FAILS else 'all passed')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
