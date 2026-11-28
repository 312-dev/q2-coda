#!/usr/bin/env python3
"""VampOS's base image (tools/vampos.py, docs/vamp-os-base.md): what it holds, and how it boots under the emulator.

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
power key turning the screen off and on, and its hold starting init's shutdown; and Car Remote, nothing on TCP
7720 while off, a phone paired and browsing the library while on (docs/carrd.md).

--card-boot runs it as today's Vamp starts it from the card (patch/q2vampos.sh): rcS and then runsvdir with no init
of the image's own, beside stand-ins for today's Wi-Fi and Wi-Fi adbd, /mnt/data already mounted and /proc/modules
listing every vendor module, so rcS must keep the one and not run the module script again, and Wi-Fi and port 5555
must be left alone.

--perturb breaks one thing in the unpacked copy, to show the check that should catch it goes red: `stock-file`
(a vendor file inside the base image), `abi` (one program's NaN bit cleared), `order` (rcS says it is done
before the modules), `module` (the framebuffer's insmod dropped from the copy's module script), `adb-open` (the
Wi-Fi adbd started with no key check), `adb-off` (no Wi-Fi adbd), `playd-off` (no q2playd), `sysd-off` (no q2sysd), `ui-slow` (q2ui's finish waiting 10 s after any end), `libd-off` (no q2libd), `netd-off` (no q2netd), `netd-card` (q2netd not told of a card boot), `adb-marker` (the adb services yielding on any card boot), `rescue-late` (rcS without the rescue restore), `dac-up` (the DAC loop started at boot), `pkgd-off` (no `q2pkgd serve`), `heat-card` (q2sysd not told of a card boot), `zone-off` (no zone table: the time zone never set), `light-dark` (with --card-boot: Light Mode saved dark), `accent-blue` (with --card-boot: the accent saved Blue), `carrd-off` (no q2carrd), `carrd-card` (with --card-boot: q2carrd's run script not waiting on a card boot), `seed-off` (rcS without the random seed's mix-in), `lo-off` (rcS without the loopback interface), `lo-card` (with --card-boot: lo brought up over today's Vamp's network). --no-outer-adbd, with --card-boot: today's Vamp has no adbd running, so the image's must start on USB and Wi-Fi.
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
    # Bluetooth (docs/vamp-os-base.md#bluetooth): each package's release and tarball are thirdparty's
    for name, package, var in (('bluez', 'vamp-bluez', 'VAMP_BLUEZ'), ('bluealsa', 'vamp-bluealsa', 'VAMP_BLUEALSA'),
                               ('freeaptx', 'q2-freeaptx', 'Q2_FREEAPTX'), ('ldac', 'q2-ldac', 'Q2_LDAC')):
        version, url, digest = thirdparty.SOURCES[name]
        check(f'{var}_VERSION = {version}\n' in mk(package) and hashes(pkg/package).get(url.rsplit('/', 1)[1]) == digest,
              f'{name} {version} as tools/thirdparty.py pins it')
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
        if facts is None or rel == 'usr/lib/vamp/usb_f_uac1.ko':  # the kernel's ABI (soft float), checked by its pin below
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
    # q2ui's glyphs (tools/vampos.py ui_art), named as ui/image.c icon() opens them: a row's, Now Playing's, the status
    # row's, Control Center's
    glyphs = [f'usr/share/vamp/icons/oled_{n}.png' for n in ('shuffle_w', 'mode_radio_w', 'np_fav_d', 'np_unfav_d', 'np_more_d',
                                                             'vol_lo_d', 'status_down_d', 'cc_wifi_3', 'cc_usb')]
    missing = [g for g in glyphs if not (fs/g).read_bytes()[:8] == bytes.fromhex('89504e470d0a1a0a')] if all((fs/g).exists() for g in glyphs) else glyphs
    check(not missing and len(list((fs/'usr/share/vamp/icons').glob('oled_*.png'))) > 500,
          f"q2ui's glyphs in /usr/share/vamp/icons{': missing ' + ', '.join(missing) if missing else ''}")
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
    import uac1  # noqa: E402  (tools/, on the path as vampos is)
    ko = fs/'usr/lib/vamp/usb_f_uac1.ko'
    check(os.access(fs/'usr/bin/q2-iap', os.X_OK) and os.access(fs/'usr/libexec/vamp/q2car', os.X_OK) and os.access(fs/'usr/bin/q2car', os.X_OK)
          and ko.exists() and hashlib.sha256(ko.read_bytes()).hexdigest() == uac1.PIN[0], "Car Audio: q2-iap, q2car and tools/uac1.py's pinned usb_f_uac1.ko")
    services = sorted(p.name for p in (fs/'etc/sv').iterdir())
    check(os.access(fs/'usr/bin/q2carrd', os.X_OK) and os.access(fs/'usr/bin/coda-rc', os.X_OK), "Car Remote: q2carrd and today's coda-rc")
    import iroh  # noqa: E402
    q2_iroh = fs/'usr/bin/q2-iroh'
    check(os.access(q2_iroh, os.X_OK) and hashlib.sha256(q2_iroh.read_bytes()).hexdigest() == iroh.PIN[0],
          "Car Remote across networks: tools/iroh.py's pinned q2-iroh")
    check(services == ['adb-wifi', 'adbd', 'bluealsa', 'bluetoothd', 'bt-attach', 'dbus', 'q2-airplay', 'q2-guest', 'q2btd', 'q2carrd', 'q2dacloop', 'q2dlnad', 'q2gate', 'q2libd',
                       'q2netd', 'q2pkgd', 'q2pkgd-confirm', 'q2playd', 'q2player', 'q2scrobd', 'q2sendd', 'q2sysd', 'q2ui'] and all(os.access(fs/'etc/sv'/s/'run', os.X_OK) for s in services),
          f'services: {", ".join(services)}')
    check(os.access(fs/'usr/bin/q2sendd', os.X_OK) and os.access(fs/'usr/bin/q2send', os.X_OK) and (fs/'etc/sv/q2sendd/down').exists()
          and any(fs.glob('usr/lib/libssl.so*')), "LocalSend: q2sendd, today's q2send, OpenSSL's libssl, and its service down at boot")
    guest = fs/'usr/bin/q2-guest'
    guest_bin = guest.read_bytes() if guest.exists() else b''
    check(os.access(guest, os.X_OK) and (fs/'usr/bin/q2-hotspot').read_bytes() == (ROOT/'patch/q2_hotspot.sh').read_bytes()
          and b'bluealsa2' not in guest_bin and b'a2dp-sink' not in guest_bin
          and '--socket /run/vamp/guest.sock' in (fs/'etc/sv/q2-guest/run').read_text() and not (fs/'etc/sv/q2-guest/down').exists(),
          "Guest Play: q2-guest serving /run/vamp/guest.sock, up at boot, built for the one bluealsa (no second daemon,"
          " no restart of the main one), and patch/q2_hotspot.sh as q2-hotspot")
    check(os.readlink(fs/'etc/passwd') == '/run/vamp/passwd' and os.readlink(fs/'etc/group') == '/run/vamp/group'
          and (fs/'etc/passwd.base').read_text().startswith('root:x:0:0:') and (fs/'etc/group.base').read_text().startswith('root:x:0:'),
          "/etc/passwd and /etc/group are links into /run/vamp, the image's own users in the .base files")
    # the root is read-only: /var/run must be the tmpfs, or wpa_supplicant cannot make its control socket (on the
    # owner's Q2: "mkdir[ctrl_interface=/var/run/wpa_supplicant]: Read-only file system", Wi-Fi never up)
    check(os.path.islink(fs/'var/run') and os.readlink(fs/'var/run') == '../run', '/var/run is a link to ../run, the tmpfs')
    check(not (fs/'etc/init.d').exists() or sorted(p.name for p in (fs/'etc/init.d').iterdir()) == ['rcK', 'rcS'],
          '/etc/init.d holds rcS and rcK alone')
    zones = (fs/'usr/share/vamp/timezones').read_text().splitlines() if (fs/'usr/share/vamp/timezones').exists() else []
    missing = [z for z in zones if not (fs/'usr/share/zoneinfo'/z).is_file()]
    missing += [t for t in ('usr/sbin/ntpd', 'sbin/hwclock', 'bin/date') if not os.path.lexists(fs/t)]  # q2netd's, q2sysd's
    check(os.readlink(fs/'etc/localtime') == '/run/vamp/localtime' and zones == vamp_os.zone_names() and not missing,
          f"the clock: /etc/localtime a link into /run/vamp, q2sysd's {len(zones)} zones, each in the image's tzdata, and ntpd, hwclock and date"
          f"{': not ' + ', '.join(missing[:5]) if missing else ''}")


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


def play_check(fs, said, deadline, log, data):
    """(q2player connected, the song came back) through q2playd's Q2PLAY/1 socket in the booted image: a play of
    song 1 (/mnt/mmc/song1.wav), and q2playd's word that q2player played it out or could not (no sound card here),
    read from said, the services' log (/run/vamp/services.log)."""
    lib = data/'vamp/library.db'
    end, songs = time.monotonic() + 60, []
    while time.monotonic() < end and not songs:  # q2libd's scan of the card (a bind mount, so in /proc/mounts)
        try:
            import sqlite3
            songs = sqlite3.connect(lib).execute('select id, fileurl, time_end - time from songtable').fetchall() if lib.exists() else []
        except Exception:
            songs = []
        time.sleep(0.5)
    # the length is time_end less time, stock's CUE end and start (docs/libd.md#the-two-databases)
    check(songs == [(1, '/mnt/mmc/song1.wav', 60)], f'q2libd: the card scanned into /mnt/data/vamp/library.db through FFmpeg ({songs})')
    connected = False
    while time.monotonic() < deadline and not connected:
        connected = b'q2playd: the player connected' in said.read_bytes() if said.exists() else False
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
        return connected, False, repr(e), (({}, {}, False))
    reload = hot_reload_check(fs, log)
    end = time.monotonic() + 40
    back = False
    while time.monotonic() < end and not back:
        back = re.search(rb'q2playd: song 1 ended', said.read_bytes()) is not None if said.exists() else False
        time.sleep(0.5)
    return connected, replies == ['Q2PLAY/1 ok', 'ok'] and back, replies, reload


def q2play_now(fs):
    """q2playd's now line over Q2PLAY/1, as {field: value}"""
    import urllib.parse
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(str(fs/'run/vamp/play.sock'))
    f = s.makefile('rw')
    f.write('Q2PLAY/1\nnow\n')
    f.flush()
    f.readline()
    line = f.readline().split()
    s.close()
    return {k: urllib.parse.unquote(v) for k, v in (w.split('=', 1) for w in line[1:])}


def q2play(fs, *lines):
    """Q2PLAY/1 lines sent to q2playd in the booted image; the answer to each"""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect(str(fs/'run/vamp/play.sock'))
    f = s.makefile('rw')
    f.write('Q2PLAY/1\n' + ''.join(l + '\n' for l in lines))
    f.flush()
    f.readline()
    out = [f.readline().strip() for _ in lines]
    s.close()
    return out


def hot_reload_check(fs, log):
    """q2ui ended from outside (SIGTERM to its process) while a song is paused: runsv starts it again within 5 s, and
    q2playd keeps the song and q2player through it (still paused, the player still connected: no "went away"); then
    resumed, the song plays out. alsa-lib's null card takes audio as fast as it comes, so the song is paused at once
    for there to be a song across the restart at all. (before, after, restarted)"""
    import signal
    q2play(fs, 'pause')
    before = q2play_now(fs)
    pids = [int(p.name) for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()
            and b'/usr/bin/q2ui' in (p/'cmdline').read_bytes() if (p/'cmdline').exists()]
    starts = log.read_bytes().count(b'q2ui: start')
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    end, restarted = time.monotonic() + 5, False
    while time.monotonic() < end and not restarted:
        restarted = log.read_bytes().count(b'q2ui: start') > starts
        time.sleep(0.2)
    time.sleep(1)
    after = q2play_now(fs)
    q2play(fs, 'resume')
    return before, after, bool(pids) and restarted

def sys_heat(fs, ask='heat'):
    """Q2SYS/1's heat answer (or another command's) from the booted image's q2sysd (docs/sysd.md#heat), or what went wrong"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(10)
        s.connect(str(fs/'run/vamp/sys.sock'))
        f = s.makefile('rw')
        out = []
        for line in ('Q2SYS/1', ask):
            f.write(line + '\n')
            f.flush()
            out.append(f.readline().strip())
        s.close()
        return out[-1]
    except OSError as e:
        return repr(e)


def day_zone(fs):
    """(index, name) of a zone in q2sysd's table whose clock reads between 10:00 and 15:59 now, by the image's tzdata"""
    import datetime
    import zoneinfo
    for i, name in enumerate((fs/'usr/share/vamp/timezones').read_text().splitlines()):
        with open(fs/'usr/share/zoneinfo'/name, 'rb') as f:
            if 10 <= datetime.datetime.now(zoneinfo.ZoneInfo.from_file(f)).hour < 16:
                return i, name
    raise SystemExit('no zone in the table is at midday now')


def light_check(fb):
    """q2ui's Light Mode with no [IPOD] LIGHT saved is Auto (the owner's ruling, 2026-11-27; ui/config.c): at midday by the
    zone q2sysd set, the screen is light (q2ui before 9af5a6f5 draws it dark whatever the hour). The pixel at (5, 50) of
    the framebuffer (rotated: the home list's margin, above the cover's pane) is the list's background, near white when
    light, near black when dark; both of its pages."""
    data = fb.read_bytes()
    px = [data[o:o + 3] for o in ((50 * 320 + 5) * 4, 320 * 375 * 4 + (50 * 320 + 5) * 4)]
    check(all(min(p) >= 0xe0 for p in px), f"q2ui: Light Mode never chosen is Auto, light at midday (pixel (5, 50): {[p.hex() for p in px]})")


# Arterial, #B8141C, and Blue, #2160D8, at the panel's 6 bits a channel (#2060D8), as q2ui's frame stores them
# (0xffRRGGBB, little-endian)
ARTERIAL, BLUE = b'\x1c\x14\xb8', b'\xd8\x60\x20'


def accent_check(fb):
    """q2ui's accent with no [IPOD] OLED_ACCENT saved is Arterial, #B8141C on the 6-bit panel (the owner's #B8121B;
    feat/render-engine 350316ac, ui/theme.c): the home list's selected row is filled with it. q2ui before 62f1468b fills
    it Blue (#2060D8 on the panel) whatever is saved. Counted over both pages of the framebuffer, as the row's place turns with it."""
    data = fb.read_bytes()
    px = [data[o:o + 3] for o in range(0, len(data) - 3, 4)]
    red, blue = px.count(ARTERIAL), px.count(BLUE)
    check(red >= 1000 and blue < 100, f"q2ui: the accent never chosen is Arterial (#B8141C: {red} px, Blue #2060D8: {blue} px)")


def sysd_checks(fs, power_key, log):
    """q2sysd in the booted image (docs/sysd.md): the battery's file from the CW2015's, a press of the power key on a
    device of its own (a FIFO: q2ui leaves an unnamed one alone) lighting a dark screen and leaving a lit one, and a hold of it
    starting init's shutdown (rcK's line in the boot log). The last check: the system goes down."""
    import struct as st
    bl = fs/'sys/class/backlight/lcd/bl_power'
    text = (fs/'run/vamp/battery').read_text() if (fs/'run/vamp/battery').exists() else ''
    check('level=64' in text and 'mv=3800' in text, f'q2sysd: /run/vamp/battery from the CW2015 ({text.split() or "none"})')
    got = sys_heat(fs)
    check(got in ('heat pending', 'heat ok', 'heat nosensor'), f"q2sysd: heat watches, with no PMIC to read here ({got})")
    fd = os.open(power_key, os.O_RDWR | os.O_NONBLOCK)

    def key(value):  # the guest's input_event: o32 with a 64-bit time_t gets the kernel's 32-bit longs, 16 bytes
        os.write(fd, st.pack('<IIHHi', int(time.time()), 0, 1, 116, value))

    def until(pred, s=5):
        end = time.monotonic() + s
        while time.monotonic() < end and not pred():
            time.sleep(0.1)
        return pred()
    # Centre is KEY_POWER on the Q2: lit, a short press is q2ui's select and leaves the light; dark, it lights it
    got = sys_heat(fs, 'screen off')
    check(got == 'ok' and until(lambda: bl.read_text().strip() == '4'), f'q2sysd: Q2SYS/1 screen off (bl_power 4) ({got})')
    key(1), time.sleep(0.1), key(0)
    check(until(lambda: bl.read_text().strip() == '0'), 'q2sysd: a press of the power key lights a dark screen')
    key(1), time.sleep(0.1), key(0)
    time.sleep(1)
    check(bl.read_text().strip() == '0', 'q2sysd: a press while lit leaves the screen lit (Centre\'s select is q2ui\'s)')
    key(1)
    check(until(lambda: b'rcK: stopping the services' in log.read_bytes(), 10), 'q2sysd: the power key held starts init\'s shutdown')
    os.close(fd)


def outer_vamp(work, adbd=True):
    """Stand-ins for what today's Vamp has running when it hands over from the card: wpa_supplicant and udhcpc (a sleep
    under each name: Wi-Fi associated) and its Wi-Fi adbd on 5555 (a listener that greets with "outer-adbd")."""
    import threading
    procs = []
    for name in ('wpa_supplicant', 'udhcpc') + (('adbd',) if adbd else ()):
        exe = work/'outer'/name
        exe.parent.mkdir(exist_ok=True)
        exe.write_bytes(pathlib.Path('/bin/sleep').read_bytes())
        exe.chmod(0o755)
        procs.append(subprocess.Popen([str(exe), '3600']))
    if not adbd:
        return procs
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('0.0.0.0', 5555))
    srv.listen(8)

    def serve():
        while True:
            try:
                c, _ = srv.accept()
                c.sendall(b'outer-adbd')
                c.close()
            except OSError:
                return
    threading.Thread(target=serve, daemon=True).start()
    return procs + [srv]


def outer_check(outer, log, adbd=True):
    """The card boot left today's Vamp's Wi-Fi and its adbd alone: the processes still running, port 5555 still the
    outer adbd's, past Smart Wi-Fi's idle time; with no outer adbd, the image's own adbd over USB and over Wi-Fi."""
    time.sleep(8)
    text = log.read_text()
    if adbd:
        check('adbd: an outer adbd serves USB, left to it' in text and 'adb-wifi: an outer adbd listens on 5555, left to it' in text
              and 'adbd: start' not in text and 'adb-wifi: start' not in text, "card boot: the image's adb stays off beside an outer adbd")
    else:
        # USB's adbd tries: its gadget is refused here (no configfs), which is its own line, not a yield
        check('left to it' not in text and 'adb-wifi: start' in text and ('adbd: start' in text or 'adbd: no USB gadget' in text),
              "card boot, no outer adbd: the image's adbd starts on USB and Wi-Fi")
        return
    alive = all(o.poll() is None for o in outer if hasattr(o, 'poll'))
    try:
        c = socket.create_connection(('127.0.0.1', 5555), 3)
        greet = c.recv(32)
        c.close()
    except OSError as e:
        greet = repr(e).encode()
    wpas = sum(1 for p in pathlib.Path('/proc').iterdir() if p.name.isdigit() and (p/'comm').exists()
               and (p/'comm').read_text().strip() == 'wpa_supplicant')
    touched = [l for l in log.read_text().splitlines() if '$ ifconfig' in l]
    check(alive and greet == b'outer-adbd' and wpas == 1 and not touched,
          f"card boot: today's Vamp's wpa_supplicant, udhcpc and its adbd on 5555 left alone, no second wpa_supplicant, "
          f"no ifconfig ({alive}, {greet}, {wpas}, {touched})")


NAVI_RUN = r"""#!/bin/sh
pid=/tmp/q2-navi
[ -f $pid ] && kill -0 "$(cat $pid)" 2>/dev/null && exit 0
( cat /tmp/q2-addon.caps > /mnt/mmc/.navi/caps
  sleep 1000 &
  s=$!
  trap 'kill $s; rm -f $pid; exit 0' TERM
  wait $s ) </dev/null >/dev/null 2>&1 &
echo $! > $pid
"""


def launch_check(fs):
    """q2pkgd's launcher in the booted image (docs/platform/packages.md#starting-and-stopping-q2pkg1): Q2PKG/1 starts
    the card's API 0 app under its own runsv, its caps line the image's words, and stops it"""
    def say(*lines):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect(str(fs/'run/vamp/pkg.sock'))
        f = s.makefile('rw')
        out = []
        for line in ('Q2PKG/1',) + lines:
            f.write(line + '\n')
            f.flush()
            out.append(f.readline().strip())
        s.close()
        return out
    try:
        first = say('status navi', 'start navi')
        end = time.monotonic() + 15
        caps = fs/'mnt/mmc/.navi/caps'
        while time.monotonic() < end and not (caps.exists() and caps.read_text()):
            time.sleep(0.2)
        mid = say('status navi', 'stop navi', 'status navi')
    except OSError as e:
        return check(False, f'q2pkgd: Q2PKG/1 on /run/vamp/pkg.sock ({e!r})')
    check(first == ['Q2PKG/1 ok', 'status navi stopped', 'ok'] and mid == ['Q2PKG/1 ok', 'status navi running', 'ok', 'status navi stopped'],
          f'q2pkgd: an API 0 app started and stopped over Q2PKG/1 ({first + mid[1:]})')
    got = caps.read_text() if caps.exists() else None
    check(got == 'catalogue playlist ahead ratings likes grow\n', f"q2pkgd: the app's caps line is the image's words ({got!r})")
    check((fs/'run/vamp/sv/navi/supervise/stat').exists(), 'q2pkgd: the app ran under a runsv of its own, /run/vamp/sv/navi')


def lib_check(fs):
    """q2libd's Q2LIB/1 in the booted image (docs/platform/library.md#q2lib1-the-library-socket): its caps, and the
    card's one song (song1.wav, scanned for play_check) asked by id, as a client of the socket"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(15)
        s.connect(str(fs/'run/vamp/lib.sock'))
        f = s.makefile('rw')
        out = []
        for line in ('Q2LIB/1', 'caps', 'song 1'):
            f.write(line + '\n')
            f.flush()
            out.append(f.readline().strip())
        s.close()
    except OSError as e:
        return check(False, f'q2libd: Q2LIB/1 on /run/vamp/lib.sock ({e!r})')
    check(out[:2] == ['Q2LIB/1 ok', 'caps songs song albums artists genres playlists playlist search art watch lyrics'] and
          out[2].startswith('song id=1 title=song1 ') and out[2].endswith(' time=60 source=card'), f'q2libd: Q2LIB/1 answers on the image ({out})')


def set_carremote(data, on):
    """[Q2POD] CARREMOTE in config.ini, the rest of the file kept (q2carrd looks at it once a second)"""
    ini = data/'config.ini'
    text = re.sub(r'\n?\[Q2POD\]\nCARREMOTE=\d\n', '\n', ini.read_text() if ini.exists() else '')
    ini.write_text(text.rstrip('\n') + f'\n[Q2POD]\nCARREMOTE={1 if on else 0}\n')


def carrd_check(fs, data):
    """Car Remote in the booted image (docs/carrd.md): nothing on TCP 7720 while [Q2POD] CARREMOTE is off; on, q2carrd
    starts coda-rc, a phone pairs with the code coda-rc shows (read from /tmp/coda-rc.pair: q2ui draws it on a card)
    and browses q2libd's library, song1.wav with its 60 s; off again, the port closes"""
    import coda_rc_client as client  # noqa: E402  (tools/, on the path as vampos is)
    check(not adb_open(7720), 'Car Remote: nothing listens on 7720 while it is off (its default)')
    set_carremote(data, True)
    end = time.monotonic() + 60
    while time.monotonic() < end and not adb_open(7720):
        time.sleep(0.5)
    if not adb_open(7720):
        return check(False, 'Car Remote: on, coda-rc listens on 7720')

    def code(_prompt):
        pair = fs/'tmp/coda-rc.pair'
        until = time.monotonic() + 30
        while time.monotonic() < until and not pair.exists():
            time.sleep(0.2)
        return pair.read_text().split('\n')[0] if pair.exists() else '000000'
    try:
        with tempfile.TemporaryDirectory() as keys:
            phone = client.connect('127.0.0.1', 7720, pathlib.Path(keys), name='os_image', ask=code)
            items = phone.browse('tracks')['items']
            phone.sock.close()
        got = [(i['title'], i.get('duration_ms')) for i in items]
    except Exception as e:  # noqa: BLE001  (the phone's side: any failure is the check's)
        got = repr(e)
    check(got == [('song1', 60000)], f"Car Remote: a phone pairs and browses q2libd's library over coda-rc ({got})")
    set_carremote(data, False)
    end = time.monotonic() + 15
    while time.monotonic() < end and adb_open(7720):
        time.sleep(0.5)
    check(not adb_open(7720), 'Car Remote: off again, coda-rc stopped')


def netd_check(fs, card):
    """q2netd in the booted image: Q2NET/1 answers; with no saved network the radio is off, unless the Wi-Fi adbd let a
    computer in (adb_checks, before this), whose use mark holds it on for 30 minutes (Smart Wi-Fi's rule)"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect(str(fs/'run/vamp/net.sock'))
        f = s.makefile('rw')
        f.write('Q2NET/1\nstatus\n')
        f.flush()
        got = [f.readline().strip(), f.readline().strip()]
        s.close()
    except OSError as e:
        got = [repr(e), '']
    st = dict(w.split('=', 1) for w in got[1].split()[1:]) if got[1].startswith('status ') else {}
    if card:
        ok = st.get('state') == 'external'
    else:
        ok = st.get('state') != 'off' and int(st.get('held', 0)) > 1000
    check(got[0] == 'Q2NET/1 ok' and ok, 'q2netd: Q2NET/1 answers; ' + ("today's Wi-Fi left as it is (external)" if card else
          'the Wi-Fi adbd\'s login holds the radio on (Smart Wi-Fi)') + f' ({got})')


def adb_open(port=5555):
    """Whether something accepts on 127.0.0.1:port (the sandbox shares this machine's network)."""
    import socket as sk
    try:
        sk.create_connection(('127.0.0.1', port), 1).close()
        return True
    except OSError:
        return False


def adb_checks(fs, keys=None, closed_s=15, wait_s=30):
    """adb over Wi-Fi in the booted image (docs/vamp-os-base.md#adb): no port while no computer is trusted; a key put
    in /mnt/data/adb_keys opens it within q2adb-wifi's 10 s and lets that key in (exec: reads the image's own
    /etc/vamp-release), refuses another key, and closes the port again once the file is gone."""
    keys = keys or fs/'mnt/data/adb_keys'
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
        ask = vamp.Device(dict(target), key).exec_out('q2ask /run/vamp/sys.sock Q2SYS/1 battery; echo rc=$?').decode(errors='replace')
    except (OSError, vamp.AdbError, TimeoutError) as e:
        ask = repr(e)
    check(ask.startswith('Q2SYS/1 ok\nbattery level=') and ask.rstrip().endswith('rc=0'),
          f'q2ask: a shell on the Q2 asks q2sysd for the battery (docs/vamp-os-base.md#first-card-boot) ({ask!r})')
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
STUBBED = ['bin/mount', 'bin/mknod', 'sbin/mdev', 'sbin/insmod', 'usr/sbin/ubiattach', 'bin/dmesg', 'sbin/ifconfig']


def boot(composed, qemu, keep=None, wait_s=90, logs=None, perturb=None, card=False, adb=True, outer_adbd=True):
    work = pathlib.Path(keep or tempfile.mkdtemp(prefix='vamp-os-boot-', dir='/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    fs = unpack(composed, work/'fs')
    emu = work/'emu'
    emu.mkdir(exist_ok=True)
    (emu/'record').write_text('')
    (fs/'run/vamp').mkdir(parents=True, exist_ok=True)
    (fs/'run/vamp/boot.log').write_text('')
    want = expected_modules(fs)  # before any perturbation: what Shanling's scripts say
    if perturb == 'dac-up':  # USB DAC mode's loop started at boot: the DAC taken from the player
        (fs/'etc/sv/q2dacloop/down').unlink()
    if perturb == 'rescue-late':  # rcS without its restore: the 60 s confirm alone would put it back
        rcs = (fs/'etc/init.d/rcS').read_text().replace('if [ -e /mnt/mmc/.vamp/rescue/pending ]; then', 'if false; then')
        (fs/'etc/init.d/rcS').write_text(rcs)
    if perturb in ('seed-off', 'lo-off', 'lo-card'):  # rcS without the seed's mix-in, without lo, or lo on a card boot too
        rcs = (fs/'etc/init.d/rcS').read_text()
        old, new = {'seed-off': ('if [ -s $seed ] && dd', 'if false && dd'),
                    'lo-off': ('    if ifconfig lo ', '    if false && ifconfig lo '),
                    'lo-card': ('if [ ! -e /run/vamp/card-boot ]; then\n    if ifconfig lo', 'if true; then\n    if ifconfig lo')}[perturb]
        check(old in rcs, f'--perturb {perturb}: the line it breaks is in rcS')
        (fs/'etc/init.d/rcS').write_text(rcs.replace(old, new))
    if perturb == 'order':
        rcs = (fs/'etc/init.d/rcS').read_text()
        rcs = rcs.replace('    step "modules: start"\n', '    step "modules: start"\n    step "rcS: done, starting the services"\n')
        (fs/'etc/init.d/rcS').write_text(rcs)
    if perturb == 'adb-open':  # Wi-Fi adbd with no key check: an open port
        (fs/'etc/sv/adb-wifi/run').write_text('#!/bin/sh\nexec /usr/bin/adbd --tcp 5555\n')
    if perturb == 'adb-marker':  # the adb services yield on a card boot whatever runs (the rule before this one)
        for svc in ('adbd', 'adb-wifi'):
            run = (fs/'etc/sv'/svc/'run').read_text()
            run = run.replace('if outer_usb_adbd; then', 'if [ -e /run/vamp/card-boot ]; then').replace('if outer_tcp_adbd 5555; then', 'if [ -e /run/vamp/card-boot ]; then')
            (fs/'etc/sv'/svc/'run').write_text(run)
    if perturb == 'netd-card':  # q2netd not told it is a card boot
        run = (fs/'etc/sv/q2netd/run').read_text().replace('card=--card-boot', 'card=')
        (fs/'etc/sv/q2netd/run').write_text(run)
    if perturb == 'netd-off':  # no q2netd
        (fs/'etc/sv/q2netd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'libd-off':  # no q2libd
        (fs/'etc/sv/q2libd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'ui-slow':  # q2ui's finish waits 10 s whatever the end, as before hot reload
        (fs/'etc/sv/q2ui/finish').write_text('#!/bin/sh\nsleep 10\n')
    if perturb == 'player-bounce':  # q2player stopped as q2ui restarts: the hot reload check's player gone away
        (fs/'etc/sv/q2ui/finish').write_text('#!/bin/sh\nsv restart /etc/sv/q2player\n')
    if perturb == 'heat-card':  # q2sysd not told it is a card boot: its heat beside today's q2guard
        r = fs/'etc/sv/q2sysd/run'
        r.write_text(r.read_text().replace('&& card=--card-boot', '&& card='))
    if perturb == 'zone-off':  # no zone table: rcS's q2sysd zone and the service set none
        (fs/'usr/share/vamp/timezones').unlink()
    if perturb == 'sysd-off':  # no q2sysd
        (fs/'etc/sv/q2sysd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'playd-off':  # no q2playd
        (fs/'etc/sv/q2playd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'pkgd-off':  # no q2pkgd serve: nothing starts an app
        (fs/'etc/sv/q2pkgd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'carrd-off':  # no q2carrd: Car Remote never starts
        (fs/'etc/sv/q2carrd/run').write_text('#!/bin/sh\nexec sleep 3600\n')
    if perturb == 'carrd-card':  # q2carrd's run script not waiting on a card boot: two coda-rcs on one Q2
        r = fs/'etc/sv/q2carrd/run'
        r.write_text(r.read_text().replace('if [ -e /run/vamp/card-boot ]; then', 'if false; then'))
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
    # q2ui's keys, touch and wheel by the emulator's numbering (ui/input.c input_open: 1, 2, 3), and 4, a power key of
    # q2sysd's alone: a FIFO has one reader's bytes, where a real device gives each reader every event
    for n in (1, 2, 3, 4):
        if not (inp/f'event{n}').exists():
            os.mkfifo(inp/f'event{n}')
    import sqlite3
    db = sqlite3.connect(fs/'mnt/data/database.db')
    db.execute('create table songtable (name text, songer text, id integer, album text, track integer, fileurl text)')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?, ?)',
                   [(f'Song {i}', 'Artist', i, 'Album', i, f'/mnt/mmc/song{i}.wav') for i in range(1, 21)])
    db.commit()
    db.close()
    wav(fs/'mnt/mmc/song1.wav', seconds=60)
    # an API 0 card app for the launcher (launch_check): its run script starts its loop in the background, as
    # q2-subsonic's does, and the loop notes the caps line it found
    navi = fs/'mnt/mmc/.navi'
    navi.mkdir(parents=True, exist_ok=True)
    (navi/'q2app').write_text('name=Navi\ncatalogue=1\n')
    (navi/'run').write_text(NAVI_RUN)
    # an install pending and recovery-update/ gone, as after Shanling's recovery has flashed it: rcS must put it back
    rescue = {'xImage': b'stock kernel' * 100, 'rootfs.squashfs': b'stock rootfs' * 100}
    (fs/'mnt/mmc/.vamp/rescue').mkdir(parents=True, exist_ok=True)
    for n, b in rescue.items():
        (fs/'mnt/mmc/.vamp/rescue'/n).write_bytes(b)
    (fs/'mnt/mmc/.vamp/rescue/pending').write_text('')
    # a sound card that takes anything (alsa-lib's null plugin): the emulator has none, and a song should play out
    (fs/'etc/asound.conf').write_text('pcm.!default { type null }\n')
    for rel, text in (('sys/class/backlight/lcd/bl_power', '0'), ('sys/class/backlight/lcd/brightness', '200'),
                      ('sys/class/backlight/lcd/max_brightness', '255'), ('sys/devices/i2c-0/0-0062/cw2015_capacity', '64'),
                      ('sys/devices/i2c-0/0-0062/cw2015_voltage', '3800'), ('sys/hynitron_debug/hyngescontrol', '0')):
        (fs/rel).parent.mkdir(parents=True, exist_ok=True)
        (fs/rel).write_text(text + '\n')
    # the last boot's random seed on a boot from flash; a card boot's /mnt/data (emu/data) has none, as a first boot
    old_seed = bytes(range(256)) * 2
    if not card:
        (fs/'mnt/data/vamp').mkdir(parents=True, exist_ok=True)
        (fs/'mnt/data/vamp/random-seed').write_bytes(old_seed)
    console = open(work/'console.log', 'wb')
    extra = []
    zone_want = 'UTC'  # a fresh /mnt/data (a tmpfs here) has no zone saved
    if card:
        # as today's Vamp hands the Q2 over from the card (patch/q2vampos.sh): /mnt/data mounted, stock's modules loaded
        (emu/'data').mkdir(exist_ok=True)
        (emu/'data/database.db').write_bytes((fs/'mnt/data/database.db').read_bytes())
        (emu/'data/wpa_supplicant.conf').write_text('network={\n\tssid="Home"\n\tpsk="secret123"\n}\n')  # networks saved
        # a zone chosen, Light Mode and the accent never: the zone's clock at midday, so Auto is light (light_check), and
        # the accent the default (accent_check)
        zone_index, zone_want = day_zone(fs)
        ipod = ('LIGHT=0\n' if perturb == 'light-dark' else '') + ('OLED_ACCENT=10\n' if perturb == 'accent-blue' else '')
        (emu/'data/config.ini').write_text(f'[SYSSET]\nTIMEZONE={zone_index}\n' + ('[IPOD]\n' + ipod if ipod else '')
                                           + '[Q2POD]\nCARREMOTE=1\n')  # on: the image's own must still not start
        (emu/'modules').write_text(''.join(f'{ko[:-3]} 16384 0 - Live 0x00000000\n' for ko, _ in want))
        extra = ['--bind', emu/'data', '/mnt/data', '--ro-bind', emu/'modules', '/proc/modules']
    outer = []
    if card:
        # as on a Q2: today's Vamp's init stays PID 1 and q2vampos runs VampOS's rcS and runsvdir in a chroot, beside
        # what today's boot started: its Wi-Fi (wpa_supplicant, udhcpc) and its Wi-Fi adbd on 5555, stand-ins here
        outer = outer_vamp(work, outer_adbd)
        ns, proc_args = [], ['--bind', '/proc', '/proc']
        first = ['/bin/sh', '-c', '/etc/init.d/rcS && exec /usr/bin/runsvdir -P /etc/sv']
    else:
        ns, proc_args = ['--unshare-pid', '--as-pid-1'], ['--proc', '/proc']
        first = ['/sbin/init']
    # uid 0 in a user namespace of its own: on a Q2 the services and q2ui are root, and the services take root's
    # connections without a package's groups (perm/q2perm.h)
    cmd = ['bwrap', *ns, '--unshare-user', '--uid', '0', '--gid', '0', '--unshare-uts', '--unshare-ipc', '--die-with-parent',
           '--bind', fs, '/', *proc_args, '--dev', '/dev', '--bind', fb, '/dev/fb0', '--bind', inp, '/dev/input',
           '--ro-bind', qemu, '/usr/libexec/qemu-binfmt/mipsel-binfmt-P', '--bind', emu, '/emu',
           '--bind', emu/'record', '/run/vamp/boot.log', '--bind', fs/'mnt/mmc', '/mnt/mmc', *extra, *first]
    proc = subprocess.Popen([str(c) for c in cmd], stdout=console, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=NO_PRELOAD)
    drew, played = False, None
    deadline = time.monotonic() + wait_s
    try:
        while time.monotonic() < deadline and proc.poll() is None and not drew:
            time.sleep(0.5)
            drew = any(fb.read_bytes())
        if proc.poll() is None:
            played = play_check(fs, fs/'run/vamp/services.log', deadline, emu/'record', emu/'data' if card else fs/'mnt/data')
        if adb and not card and proc.poll() is None:
            adb_checks(fs)
        if proc.poll() is None:
            lib_check(fs)
        if proc.poll() is None:
            netd_check(fs, card)
        if not card and proc.poll() is None:
            launch_check(fs)
        if not card and proc.poll() is None:
            carrd_check(fs, fs/'mnt/data')
        if card and proc.poll() is None:
            outer_check(outer, emu/'record', outer_adbd)
            got = sys_heat(fs)
            check(got == 'heat off', f"q2sysd: no heat of its own on a card boot, today's Vamp's q2guard's ({got})")
            light_check(fb)
            accent_check(fb)
            time.sleep(3)  # q2carrd's run script, had it not waited, would have started coda-rc by now (config.ini: on)
            check(not adb_open(7720), "card boot: Car Remote is today's Vamp's (nothing of the image's on 7720)")
            if not outer_adbd:
                adb_checks(fs, emu/'data/adb_keys')
        if proc.poll() is None:
            got = sys_heat(fs, 'time')
            check(got == f'time zone={zone_want}', f"q2sysd: the zone config.ini saves, {zone_want} (docs/sysd.md#the-clock) ({got})")
        if not card and proc.poll() is None:
            sysd_checks(fs, inp/'event4', emu/'record')
        if card:  # the services' first lines
            time.sleep(3)
    finally:
        proc.kill()
        proc.wait()
        console.close()
        for o in outer:
            if hasattr(o, 'kill'):
                o.kill()
            else:
                o.close()
    # each service's stdout and stderr in /run/vamp/services.log (usr/libexec/vamp/log), not the console: on a card
    # boot that is the serial port, and the first card boot on the owner's Q2 lost every service's word to it
    said = (fs/'run/vamp/services.log').read_bytes() if (fs/'run/vamp/services.log').exists() else b''
    check(b'q2sysd: ' in said and b'input device(s)' in said and b'q2sysd: ' not in (work/'console.log').read_bytes(),
          "the services' words in /run/vamp/services.log, not on the console")
    check(drew, 'q2ui drew into the framebuffer')
    check(bool(played) and played[0], 'q2player connected to q2playd' + ('' if played else ': not reached'))
    check(bool(played) and played[1], 'a song asked for over Q2PLAY/1 plays out through q2playd and q2player (alsa-lib\'s null card)'
          + ('' if played and played[1] else f': {played}'))
    if played and len(played) > 3:
        before, after, restarted = played[3]
        check(restarted, 'hot reload: q2ui ended from outside is started again within 5 s')
        check(before.get('state') == '2' and after.get('state') == '2' and before.get('id') == after.get('id') == '1'
              and b'q2playd: the player went away' not in said.split(b'powering off')[0],  # before the hold's shutdown, which stops both
              f'hot reload: q2playd keeps the song and q2player across q2ui\'s restart (state {before.get("state")} then '
              f'{after.get("state")}), and it plays out after')
    lines = (emu/'record').read_text().splitlines()
    events = [(l.split(None, 1)[1][2:], 'cmd') if l.split(None, 1)[1].startswith('$ ') else (l.split(None, 1)[1], 'log')
              for l in lines if len(l.split(None, 1)) == 2]
    timeline = ''.join(l + '\n' for l in lines)
    (work/'timeline.txt').write_text(timeline)
    if logs:  # beside the build: the sandbox's /tmp is the container's, gone when it ends
        logs.mkdir(parents=True, exist_ok=True)
        (logs/'timeline.txt').write_text(timeline)
        (logs/'console.log').write_bytes((work/'console.log').read_bytes())
        (logs/'services.log').write_bytes(said)
    order = [s for s, _ in events]

    def at(pred):
        return next((i for i, s in enumerate(order) if pred(s)), None)
    first = lambda prefix: at(lambda s: s.startswith(prefix))  # noqa: E731
    check(first('accounts: 0 package users') is not None and (first('data: ') or 0) < first('accounts: '),
          'rcS composes the accounts (q2pkgd accounts) once /mnt/data is there')
    zone_at, svc_at = first(f'clock: zone /usr/share/zoneinfo/{zone_want}, '), first('q2player: start')
    check(zone_at is not None and (svc_at is None or zone_at < svc_at), f"rcS sets the time zone (q2sysd zone, {zone_want}) before the services")
    # the random seed (stock's S20urandom): the saved one mixed in before the services, a new one saved at once
    seed = (emu/'data' if card else fs/'mnt/data')/'vamp/random-seed'
    mixed, done_at = first('seed: none saved yet' if card else 'seed: 512 bytes mixed into the pool'), first('rcS: done')
    new_seed = seed.read_bytes() if seed.exists() else b''
    check(mixed is not None and done_at is not None and mixed < done_at and first('seed: a new one saved') is not None
          and len(new_seed) == 512 and new_seed != old_seed and seed.stat().st_mode & 0o077 == 0,
          'rcS mixes the saved random seed into the pool before the services and saves a new one, 512 bytes, owner-only'
          + ('' if card else ' (a boot from flash, a seed saved)'))
    if not card:
        lo, lo_said = first('ifconfig lo 127.0.0.1 netmask 255.0.0.0 up'), first('net: lo up')
        check(lo is not None and lo_said is not None and done_at is not None and lo < done_at,
              'rcS brings up the loopback interface on a boot from flash, before the services')
        if first('rcK: stopping the services') is not None:  # the power key's hold (sysd_checks) ran init's shutdown
            check(first('rcK: seed saved') is not None, 'rcK saves a random seed at shutdown')
    if card:
        check(not any(s.startswith('insmod ') for s in order) and first('modules: already loaded, not run again; not loaded: none') is not None,
              'card boot: the module script is not run again over loaded modules')
        check(first('data: already mounted') is not None and not any(s.startswith(('ubiattach', 'mount -t tmpfs -o mode=0755 tmpfs /mnt/data')) for s in order),
              'card boot: /mnt/data is kept as mounted')
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
    rec = fs/'mnt/mmc/recovery-update'
    got = {n: (rec/n).read_bytes() for n in rescue if (rec/n).exists()}
    at_r, at_fb = first('rescue: recovery-update/ restored'), first('fb0: present')
    check(got == rescue and at_r is not None and at_fb is not None and at_r < at_fb and at_r < (first('q2player: start') or 10**9),
          'rcS puts the rescue copy back in recovery-update/ before the framebuffer and the services')
    check(first('q2dacloop: start') is None and first('q2playd: start') is not None,
          "USB DAC mode's loop stays down at boot (it waits for q2sysd's usb dac)")
    check(first('q2sendd: start') is None and first('q2playd: start') is not None,
          'LocalSend stays down at boot (it listens only while its page is open)')
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
    ap.add_argument('--perturb', choices=['stock-file', 'abi', 'order', 'module', 'adb-open', 'adb-off', 'playd-off', 'sysd-off', 'ui-slow', 'libd-off', 'netd-off', 'netd-card', 'adb-marker', 'rescue-late', 'dac-up', 'pkgd-off', 'player-bounce', 'heat-card', 'zone-off', 'light-dark', 'accent-blue', 'carrd-off', 'carrd-card', 'seed-off', 'lo-off', 'lo-card'])
    ap.add_argument('--card-boot', action='store_true', help='boot as patch/q2vampos.sh starts it: /mnt/data mounted, modules loaded')
    ap.add_argument('--no-outer-adbd', action='store_true', help='with --card-boot: today\'s Vamp has no adbd running')
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
            boot(composed, a.qemu or vamp_os.qemu(), a.keep, logs=a.build/(('boot-card-noadb' if a.no_outer_adbd else 'boot-card') if a.card_boot else 'boot'), perturb=a.perturb, card=a.card_boot, outer_adbd=not a.no_outer_adbd)
    print(f'{len(FAILS)} failed' if FAILS else 'all passed')
    sys.exit(1 if FAILS else 0)


if __name__ == '__main__':
    main()
