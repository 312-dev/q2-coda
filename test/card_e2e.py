#!/usr/bin/env python3
"""The card boot end to end (docs/vamp-os-base.md#card-boot), as far as no kernel lets it go:

    test/card_e2e.py BUILD --zip ZIP

BUILD's image is composed with the ZIP's vendor set and laid out on a stand-in card by tools/vampos.py card (the image,
its .sha256, boot-once); today's Vamp's side is the real patch/q2vampos.sh under this machine's sh, with a stand-in
for the mount it would do: the loop mount of the card's image is an unsquashfs of it into the mount point, the bind
mounts are noted, and each chroot is a bwrap sandbox of that root with the binds, QEMU for the image's MIPS programs,
the framebuffer and input stand-ins. Inside, the image's own rcS and runsvdir run as on a Q2. Checked: q2vampos's log
(the switch, the hash, the hand-off), boot-once gone before anything ran, rcS's card-boot path (data kept mounted,
modules not loaded again), q2ui drawing, a song played over Q2PLAY/1 through q2playd and q2player; then a second boot
with boot-once spent is the usual one. A wrong .sha256 on the card is refused with nothing run. What it cannot show:
the loop device and the chroot of a real kernel, and today's Vamp's boot before the hand-off."""
import argparse
import hashlib
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
sys.path.insert(0, str(ROOT/'test'))
import vampos  # noqa: E402
import os_image  # noqa: E402

FAILS = []
SCRIPT = pathlib.Path(os.environ.get('Q2VAMPOS_SCRIPT') or ROOT/'patch/q2vampos.sh')  # another copy, to show a broken one fails


def check(ok, what):
    print(('ok    ' if ok else 'FAIL  ') + what, flush=True)
    if not ok:
        FAILS.append(what)


# the mount and chroot of today's Vamp's q2vampos, as this machine can do them (see the docstring); STUBS are the image's
# own commands that would reach the kernel (test/os_image.py's)
MOUNT = r'''#!/bin/sh
echo "mount $*" >> "$E/q2vampos.record"
case "$*" in
*loop*) eval "new=\${$#}"; eval "img=\${$(($# - 1))}"
    unsquashfs -q -n -f -ignore-errors -no-xattrs -d "$new" "$img" > /dev/null 2>&1
    for s in $STUBBED; do rm -f "$new/$s"; printf '%s' "$STUB" > "$new/$s"; chmod 755 "$new/$s"; done
    mkdir -p "$new/run/vamp"; : > "$new/run/vamp/boot.log"; exit 0 ;;
*--bind*) echo "$2 $3" >> "$E/binds"; exit 0 ;;
esac
exit 0
'''
CHROOT = r'''#!/bin/sh
echo "chroot $*" >> "$E/q2vampos.record"
new=$1; shift
binds=; while read -r a b; do binds="$binds --bind $a ${b#$new}"; done < "$E/binds"
# uid 0 in a user namespace, as root is on a Q2: the services take root's connections (perm/q2perm.h)
exec bwrap --unshare-user --uid 0 --gid 0 --bind "$new" / --bind /proc /proc --dev /dev --bind "$E/fb0" /dev/fb0 --bind "$E/input" /dev/input \
    --ro-bind "$QEMU" /usr/libexec/qemu-binfmt/mipsel-binfmt-P --bind "$E/record" /run/vamp/boot.log --bind "$E" /emu $binds \
    --ro-bind "$E/modules" /proc/modules "$@"
'''
REC = '#!/bin/sh\necho "$(basename "$0") $*" >> "$E/q2vampos.record"\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('build', type=pathlib.Path)
    ap.add_argument('--zip', type=pathlib.Path, required=True)
    a = ap.parse_args()
    qemu = vampos.qemu()
    with tempfile.TemporaryDirectory(dir='/tmp') as tmp:
        w = pathlib.Path(tmp)
        composed = w/'composed.squashfs'
        vampos.compose(a.build/'vampos.squashfs', composed, zip_path=a.zip)
        R, E = w/'r', w/'e'  # today's Vamp's root, and the stand-ins' folder
        for d in ('mnt/mmc', 'mnt/data', 'tmp', 'usr/bin', 'etc/init.d', 'bin', 'e'):
            (R/d).mkdir(parents=True, exist_ok=True)
        E.mkdir()
        (E/'input').mkdir()
        for n in (1, 2, 3):
            os.mkfifo(E/'input'/f'event{n}')
        (E/'fb0').write_bytes(bytes(320 * 375 * 4 * 2))
        (E/'record').write_text('')
        (E/'binds').write_text('')
        # stock's boot loaded the modules before the hand-off: /proc/modules as it then reads
        import re
        md = ROOT/'os/vendor/V1.36.tsv'
        kos = sorted({re.sub(r'\.ko$', '', l.split('\t')[0].rsplit('/', 1)[-1]) for l in md.read_text().splitlines() if l.endswith('\t0744') is False and '.ko\t' in l})
        (E/'modules').write_text(''.join(f'{k} 16384 0 - Live 0x00000000\n' for k in kos))
        (R/'tmp/mmc_add').write_text('')
        (R/'etc/asound.conf').write_text('')
        os_image.wav(R/'mnt/mmc/song1.wav', seconds=60)
        for p, text in (('usr/bin/q2boot', REC + 'exit 1\n'), ('etc/init.d/S10mdev', REC), ('bin/mount', MOUNT), ('bin/umount', REC),
                        ('bin/chroot', CHROOT), ('bin/killall', REC)):
            (R/p).write_text(text)
            (R/p).chmod(0o755)
        vampos.card(composed, R/'mnt/mmc')
        env = dict(os.environ, Q2VAMPOS_ROOT=str(R), Q2VAMPOS_WAIT='1', E=str(E), QEMU=str(qemu), PATH=f'{R}/bin:{os.environ["PATH"]}',
                   STUBBED=' '.join(os_image.STUBBED), STUB=os_image.STUB.replace('/emu/record', '/emu/record'))
        # alsa-lib's null card inside, as the boot test gives it: the image's /etc is the unpacked root's
        out = open(E/'handoff.out', 'wb')
        handoff = subprocess.Popen(['sh', str(SCRIPT)], env=env, stdout=out, stderr=subprocess.STDOUT)
        new = R/'tmp/vamp-os-root'
        try:
            end = time.monotonic() + 30
            while time.monotonic() < end and not (new/'etc/asound.conf').exists() and not (new/'usr/bin').exists():
                time.sleep(0.2)
            time.sleep(1)
            if new.exists():
                (new/'etc/asound.conf').write_text('pcm.!default { type null }\n')
            # q2libd scans the card; then a song over Q2PLAY/1, as the boot test plays one
            lib = R/'mnt/data/vamp/library.db'
            end = time.monotonic() + 90
            while time.monotonic() < end and not lib.exists():
                time.sleep(0.5)
            time.sleep(5)
            drew = any((E/'fb0').read_bytes())
            replies = []
            try:
                end = time.monotonic() + 30
                while time.monotonic() < end and not (new/'run/vamp/play.sock').exists():
                    time.sleep(0.5)
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.settimeout(10)
                s.connect(str(new/'run/vamp/play.sock'))
                f = s.makefile('rw')
                f.write('Q2PLAY/1\nplay 0 0 1\n1\nnow\n')
                f.flush()
                replies = [f.readline().strip() for _ in range(3)]
                s.close()
            except OSError as e:
                replies = [repr(e)]
            log = (R/'mnt/data/vamp/handoff.log').read_text() if (R/'mnt/data/vamp/handoff.log').exists() else ''
            print('--- hand-off output (tail)\n' + (E/'handoff.out').read_text(errors='replace')[-1500:] + '\n---')
            boot = (E/'record').read_text()
            want = vampos.sha256(composed)
            check('switch: boot-once' in log and f'handing off to {want}' in log, f'q2vampos took the card\'s switch and image ({log.strip().splitlines()[-1:] if log else "no log"})')
            check(not (R/'mnt/mmc/.vamp/os/boot-once').exists(), 'boot-once is gone')
            check('data: already mounted' in boot and 'modules: already loaded, not run again; not loaded: none' in boot and 'rcS: done' in boot,
                  "the image's rcS took the card boot's path: data kept, modules not loaded again"
                  + ('' if 'data: already mounted' in boot else f" ({[l for l in boot.splitlines() if 'data:' in l or 'modules:' in l]})"))
            check(drew, "the image's q2ui drew into the framebuffer")
            check(replies[:2] == ['Q2PLAY/1 ok', 'ok'] and len(replies) > 2 and ' state=1 ' in replies[2] and replies[2].endswith(' id=1'),
                  f"a song played over the image's Q2PLAY/1 from the card's library ({replies})")
        finally:
            handoff.kill()
            subprocess.run(['pkill', '-KILL', '-P', str(handoff.pid)], capture_output=True)
            handoff.wait()
        # the next boot: boot-once spent, so the usual one, nothing mounted
        (E/'q2vampos.record').write_text('')
        r = subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        check(r.returncode == 1 and 'mount' not in (E/'q2vampos.record').read_text(), 'the next boot, boot-once spent, is the usual one')
        # a wrong hash on the card: refused, nothing run
        (R/'mnt/mmc/.vamp/os/boot-once').write_text('')
        (R/'mnt/mmc/.vamp/os/vampos.squashfs.sha256').write_text('0' * 64 + '  vampos.squashfs\n')
        try:
            r = subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
        except subprocess.TimeoutExpired:  # it handed off: Vamp OS ran from a card whose hash is wrong
            r = subprocess.CompletedProcess([], None)
        log = (R/'mnt/data/vamp/handoff.log').read_text()
        check(r.returncode == 1 and 'mount' not in (E/'q2vampos.record').read_text() and 'SHA-256 is' in log.splitlines()[-1],
              'a card whose .sha256 is wrong: refused, nothing mounted')
    print(f'{len(FAILS)} failed' if FAILS else 'all passed')
    return 1 if FAILS else 0


if __name__ == '__main__':
    sys.exit(main())
