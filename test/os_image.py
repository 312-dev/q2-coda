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
either service starts, q2player connects to /tmp/sock_play (served by this test, standing in for
q2playd) and q2ui draws a frame.

--card-boot boots it as today's Vamp starts it from the card (patch/q2vampos.sh): /mnt/data already mounted and
/proc/modules listing every vendor module, so rcS must keep the one and not run the module script again.

--perturb breaks one thing in the unpacked copy, to show the check that should catch it goes red: `stock-file`
(a vendor file inside the base image), `abi` (one program's NaN bit cleared), `order` (rcS says it is done
before the modules), `module` (the framebuffer's insmod dropped from the copy's module script).
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
    check(services == ['q2player', 'q2ui'] and all(os.access(fs/'etc/sv'/s/'run', os.X_OK) for s in services),
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
STUB = '#!/bin/sh\necho "$(cut -d" " -f1 /proc/uptime) \\$ ${0##*/} $*" >> /emu/record\n'
STUBBED = ['bin/mount', 'bin/mknod', 'sbin/mdev', 'sbin/insmod', 'usr/sbin/ubiattach', 'bin/dmesg']


def boot(composed, qemu, keep=None, wait_s=90, logs=None, perturb=None, card=False):
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
    for n in (1, 3):  # the keys and the wheel, by the emulator's numbering (q2ui's input.c)
        if not (inp/f'event{n}').exists():
            os.mkfifo(inp/f'event{n}')
    import sqlite3
    db = sqlite3.connect(fs/'mnt/data/database.db')
    db.execute('create table songtable (name text, songer text, id integer, album text, track integer)')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?)',
                   [(f'Song {i}', 'Artist', i, 'Album', i) for i in range(1, 21)])
    db.commit()
    db.close()
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(fs/'tmp/sock_play'))
    srv.listen(1)
    srv.settimeout(1)
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
    conn, drew = None, False
    deadline = time.monotonic() + wait_s
    try:
        while time.monotonic() < deadline and proc.poll() is None and not (conn and drew):
            if conn is None:
                try:
                    conn, _ = srv.accept()
                except socket.timeout:
                    pass
            else:
                time.sleep(0.5)
            drew = drew or any(fb.read_bytes())
    finally:
        proc.kill()
        proc.wait()
        console.close()
    check(conn is not None, 'q2player connected to /tmp/sock_play')
    check(drew, 'q2ui drew into the framebuffer')
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
    ap.add_argument('--perturb', choices=['stock-file', 'abi', 'order', 'module'])
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
