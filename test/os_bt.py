#!/usr/bin/env python3
"""Vamp OS's Bluetooth services (docs/vamp-os-base.md#bluetooth) under the emulator: the image's dbus, bt-attach,
bluetoothd and bluealsa services run by its own runsvdir, every program MIPS under qemu-mipsel through binfmt in a
bwrap chroot of the image, with test/bluez_own.py's stand-in for the kernel's management socket.

    python3 test/os_bt.py IMAGE [--keep DIR]

IMAGE is a vampos.squashfs with the Bluetooth packages, or a directory holding one unpacked. Only the four
Bluetooth services run (from a copy of /etc/sv holding them alone): the rest of the boot is test/os_image.py's.
Two things are changed in the test's copy only: D-Bus's system.conf loses its <user> (the sandbox maps one user,
so the bus cannot drop to dbus), and bluetoothd's Bluetooth sockets reach the stand-in through bluez_own.py's
preloaded shim. Checked: the bus runs; bluetoothd owns org.bluez and gives the controller main.conf's name and
class, with no GattManager1 or LEAdvertisingManager1 on hci0 ([GATT] Server = false); bluealsa owns org.bluealsa
with its roles; bt-attach, with no /dev/ttyS0 and no vendor rtk_hciattach here, says so and waits 10 s between
tries rather than spinning; and the store is /mnt/data/bluetooth. What it cannot show: rtk_hciattach, the
radio, a kernel's Bluetooth.
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bluez_own  # noqa: E402

SERVICES = ['dbus', 'bt-attach', 'bluetoothd', 'bluealsa']
RESULTS = []


def check(ok, what, detail=''):
    print(('ok   ' if ok else 'FAIL ') + what + (f': {detail}' if detail and not ok else ''), flush=True)
    RESULTS.append(bool(ok))


DRIVER = r'''
mkdir -p /run/vamp /run/runit /emu-out
for s in /etc/sv-bt/*; do mkdir -p "/run/runit/supervise.${s##*/}"; rm -f "$s/supervise"; ln -s "/run/runit/supervise.${s##*/}" "$s/supervise"; done
: > /run/vamp/boot.log
runsvdir -P /etc/sv-bt > /emu-out/runsvdir.log 2>&1 &
owner() { dbus-send --system --print-reply --dest=org.freedesktop.DBus / \
    org.freedesktop.DBus.GetConnectionUnixProcessID string:$1 2>/dev/null | awk '/uint32/ { print $2 }'; }
i=0; while [ $i -lt 90 ]; do [ "$(owner org.bluez)" ] && [ "$(owner org.bluealsa)" ] && break; sleep 1; i=$((i+1)); done
sleep 25
for n in org.freedesktop.DBus org.bluez org.bluealsa; do echo "$n $(owner $n)"; done > /emu-out/owners
for p in /proc/[0-9]*; do c=$(tr '\0' ' ' < $p/cmdline 2>/dev/null) || continue; echo "${p#/proc/} $c"; done > /emu-out/ps
dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0 org.freedesktop.DBus.Introspectable.Introspect \
    > /emu-out/hci0.xml 2>&1
cp /run/vamp/boot.log /emu-out/boot.log
'''


def main(argv):
    keep = argv[argv.index('--keep') + 1] if '--keep' in argv else None
    args = [a for a in argv if not a.startswith('--') and a != keep]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    for tool in ('qemu-mipsel-static', 'bwrap', 'unsquashfs'):
        if not shutil.which(tool):
            print(f'skip: no {tool}')
            return 0
    image = pathlib.Path(args[0]).resolve()
    work = pathlib.Path(keep or tempfile.mkdtemp(prefix='vamp-os-bt-', dir='/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    fs = work / 'fs'
    if image.is_dir():
        fs = image
    else:
        shutil.rmtree(fs, ignore_errors=True)
        subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', fs, image], check=True, stdout=subprocess.DEVNULL)
    missing = [p for p in ('usr/libexec/bluetooth/bluetoothd', 'usr/bin/bluealsa', 'usr/bin/dbus-daemon',
                           *(f'etc/sv/{s}/run' for s in SERVICES)) if not (fs / p).exists()]
    if missing:
        print(f'{image}: no Bluetooth in this image ({", ".join(missing)})')
        return 1
    sv = fs / 'etc/sv-bt'
    shutil.rmtree(sv, ignore_errors=True)
    sv.mkdir()
    for s in SERVICES:
        shutil.copytree(fs / 'etc/sv' / s, sv / s, symlinks=True, ignore=shutil.ignore_patterns('supervise'))
    conf = fs / 'usr/share/dbus-1/system.conf'
    conf.write_text(re.sub(r'\s*<user>[^<]*</user>', '', conf.read_text()))
    for d in ('run', 'tmp', 'proc', 'dev', 'mnt/data', 'usr/libexec/qemu-binfmt', 'emu-out'):
        (fs / d).mkdir(parents=True, exist_ok=True)
    (fs / 'usr/libexec/qemu-binfmt/mipsel-binfmt-P').touch()
    out, data, tmp = work / 'out', work / 'data', work / 'tmp'
    for d in (out, data, tmp):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    env = bluez_own.thirdparty.Env({}, 0)
    (work / 'shim.c').write_text(bluez_own.SHIM)
    shim = work / 'shim.so'
    subprocess.run([*env.cc.split(), '-shared', '-fPIC', '-O1', '-o', str(shim), str(work / 'shim.c'), '-ldl'], check=True)
    (fs / 'lib/libq2btshim.so').touch()
    kernel = bluez_own.Kernel(tmp / 'mgmt')
    cmd = ['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-user', '--uid', '0', '--gid', '0',
           '--bind', fs, '/', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/run', '--bind', tmp, '/tmp',
           '--bind', data, '/mnt/data', '--bind', out, '/emu-out',
           '--ro-bind', shutil.which('qemu-mipsel-static'), '/usr/libexec/qemu-binfmt/mipsel-binfmt-P',
           '--ro-bind', shim, '/lib/libq2btshim.so',
           '--setenv', 'PATH', '/bin:/sbin:/usr/bin:/usr/sbin', '--setenv', 'LD_PRELOAD', '/lib/libq2btshim.so',
           '--setenv', 'Q2_FAKE_MGMT', '/tmp/mgmt', '/bin/sh', '-c', DRIVER]
    started = time.monotonic()
    try:
        r = subprocess.run([str(c) for c in cmd], env={k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'},
                           capture_output=True, text=True, timeout=300)
        if r.returncode:
            print(f'the chroot ended {r.returncode}: {r.stderr[-400:]}')
    finally:
        kernel.close()
    read = lambda n: (out / n).read_text(errors='replace') if (out / n).exists() else ''  # noqa: E731
    owners = dict(line.split(' ', 1) for line in read('owners').splitlines() if ' ' in line)
    ps = dict(line.split(' ', 1) for line in read('ps').splitlines() if ' ' in line)
    log = read('boot.log')
    print(f'{time.monotonic() - started:.0f} s; boot log:\n' + ''.join('    ' + line + '\n' for line in log.splitlines()[-30:]))
    check(owners.get('org.freedesktop.DBus', '').strip(), 'the system bus runs (dbus service)')
    bz = owners.get('org.bluez', '').strip()
    check(bz and '/usr/libexec/bluetooth/bluetoothd' in ps.get(bz, '') and '--noplugin=hfp,rap,fastpair' in ps.get(bz, ''),
          'bluetoothd owns org.bluez, with stock\'s like of plugins', ps.get(bz, bz))
    names = [p for p in kernel.sent(bluez_own.SET_LOCAL_NAME)]
    check(any(p.split(b'\0')[0] == b'Vamp' for p in names), 'main.conf\'s name reaches the controller', str(names[:1]))
    classes = kernel.sent(bluez_own.SET_DEV_CLASS)
    check(b'\x04\x1c' in classes or any(c[:2] == b'\x04\x1c' for c in classes), 'main.conf\'s class 0x20041C reaches the controller',
          str(classes[:2]))
    xml = read('hci0.xml')
    check('org.bluez.Adapter1' in xml and 'GattManager1' not in xml and 'LEAdvertisingManager1' not in xml,
          'hci0 has Adapter1 and no GATT server or advertising manager ([GATT] Server = false)', xml[-300:])
    ba = owners.get('org.bluealsa', '').strip()
    check(ba and '/usr/bin/bluealsa' in ps.get(ba, '') and '-p a2dp-source' in ps.get(ba, '') and '--shanling' not in ps.get(ba, ''),
          'bluealsa owns org.bluealsa with its roles and no compatibility service', ps.get(ba, ba))
    tries = log.count('bt-attach: no /vendor/bin/rtk_hciattach or no /dev/ttyS0')
    check(1 <= tries <= 6, f'bt-attach says what it lacks and tries every 10 s, not spinning ({tries} tries)')
    check((data / 'bluetooth').is_dir(), 'the store is /mnt/data/bluetooth')
    if not keep:
        shutil.rmtree(work, ignore_errors=True)
    print(f'{sum(RESULTS)} of {len(RESULTS)} checks passed')
    return 0 if all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
