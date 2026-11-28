#!/usr/bin/env python3
"""The card boot's hand-off (patch/q2vampos.sh, docs/boot.md#vampos-card-boot) under this machine's sh, with the
commands that would reach the kernel (mount, umount, chroot, killall, reboot, squashfuse_ll) and the Q2's own (q2boot,
S10mdev) as stand-ins that record their arguments: the decision (switch, key, image, hash), boot-once removed before
anything is mounted, boot-always kept, each failure ending in exit 1 (S90play's normal Vamp) with its line in the
log, a boot with no switch writing nothing, and with no loop device the FUSE mount, its supervisor's reboot when the
daemon ends under VampOS and its quiet end when the image is unmounted here.

The FUSE tests at the end run the real squashfuse_ll a --dev build ships (MIPS, under qemu-mipsel) over this
machine's /dev/fuse, in a user namespace (unshare -Urm), on an LZO image made here: the mount, its options, the
image's bytes read through it, and the supervisor's reboot when the daemon is killed mid-boot. They need /dev/fuse
(tools/mac/q2env passes it), qemu-mipsel and the binary: Q2VAMPOS_SQUASHFUSE names it, else tools/thirdparty.py
builds it (cached). Run it as python3 test/q2vampos.py (pytest, sh). Q2VAMPOS_SCRIPT names another copy of the
script, to show a broken one fails here."""
import hashlib
import os
import pathlib
import shutil
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCRIPT = pathlib.Path(os.environ.get('Q2VAMPOS_SCRIPT') or ROOT/'patch/q2vampos.sh')
IMAGE = b'a stand-in for vampos.squashfs\n'

# Each stand-in appends "NAME ARGS once=yes|no" to $R/rec: whether boot-once was still there when it ran.
STUB = '''#!/bin/sh
o=no; [ -e "$Q2VAMPOS_ROOT/mnt/mmc/.vamp/os/boot-once" ] && o=yes
echo "${0##*/} $* once=$o" >> "$Q2VAMPOS_ROOT/rec"
'''
MOUNT = STUB + '''case "$*" in
*'-o loop,'*)
    [ -e "$Q2VAMPOS_ROOT/fail-mount" ] && exit 32
    [ -e "$Q2VAMPOS_ROOT/no-loop" ] && exit 255
    [ -e "$Q2VAMPOS_ROOT/not-vamp" ] && exit 0
    eval "new=\\${$#}"
    mkdir -p "$new/etc/init.d" "$new/usr/bin" "$new/mnt/data" "$new/mnt/mmc"
    : > "$new/etc/init.d/rcS"; : > "$new/usr/bin/runsvdir" ;;
esac
'''
CHROOT = STUB + '''case "$2" in
/etc/init.d/rcS)
    if [ -e "$Q2VAMPOS_ROOT/look" ]; then
        # what VampOS's rcS would see of the image: its mount, a script, a large file's bytes, a setuid file
        { grep " $1 " /proc/mounts; cat "$1/etc/init.d/rcS"; dd if="$1/usr/bin/big" bs=65536 2>/dev/null | sha256sum
          stat -c %A "$1/bin/busybox"; } > "$Q2VAMPOS_ROOT/seen" 2>/dev/null
    fi
    if [ -e "$Q2VAMPOS_ROOT/kill-fuse" ]; then
        # the daemon killed under VampOS's rcS: the supervisor's reboot -f does not return on a Q2, so wait for it
        kill -9 "$(cat "$Q2VAMPOS_ROOT/tmp/vamp-os-fuse.pid")"
        i=0
        while [ $i -lt 100 ] && ! grep -q '^reboot' "$Q2VAMPOS_ROOT/rec"; do sleep 0.05; i=$((i + 1)); done
    fi
    exit "$(cat "$Q2VAMPOS_ROOT/rcs-status" 2>/dev/null || echo 0)" ;;
/usr/bin/runsvdir) exit 0 ;;
esac
exit 99
'''
# squashfuse_ll -f: "mounts" the image (the mount point's files and a line in $R/proc/mounts) and stays, as the
# real one serves until the image is unmounted; fuse-fail is a daemon that ends without mounting, fuse-hang one
# that neither mounts nor ends.
SQUASHFUSE = STUB.replace('once=$o"', 'once=$o oom=$(cat "$Q2VAMPOS_ROOT/proc/self/oom_score_adj")"') + '''
[ -e "$Q2VAMPOS_ROOT/fuse-fail" ] && exit 1
[ -e "$Q2VAMPOS_ROOT/fuse-hang" ] && while :; do sleep 0.05; done
eval "new=\\${$#}"
if [ ! -e "$Q2VAMPOS_ROOT/not-vamp" ]; then
    mkdir -p "$new/etc/init.d" "$new/usr/bin" "$new/mnt/data" "$new/mnt/mmc"
    : > "$new/etc/init.d/rcS"; : > "$new/usr/bin/runsvdir"
fi
echo "squashfuse_ll $new fuse.squashfuse_ll ro,nosuid 0 0" >> "$Q2VAMPOS_ROOT/proc/mounts"
while :; do sleep 0.05; done
'''
# umount: the kernel's side of a FUSE unmount, the mount gone and then the daemon's session ended
UMOUNT = STUB + '''eval "new=\\${$#}"
if grep -q " $new " "$Q2VAMPOS_ROOT/proc/mounts"; then
    grep -v " $new " "$Q2VAMPOS_ROOT/proc/mounts" > "$Q2VAMPOS_ROOT/proc/mounts.new"
    mv "$Q2VAMPOS_ROOT/proc/mounts.new" "$Q2VAMPOS_ROOT/proc/mounts"
    kill "$(cat "$Q2VAMPOS_ROOT/tmp/vamp-os-fuse.pid")"
fi
'''


def daemon_pid(root):
    path = root/'tmp/vamp-os-fuse.pid'
    return int(path.read_text()) if path.exists() else None


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # a zombie is gone as far as the script is concerned
    stat = pathlib.Path(f'/proc/{pid}/stat')
    return not (stat.exists() and stat.read_text().rsplit(')', 1)[1].split()[0] == 'Z')


def gone(pid, timeout=5):
    """Whether pid ended within timeout seconds."""
    end = time.monotonic() + timeout
    while alive(pid):
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


@pytest.fixture
def root(tmp_path):
    """$R: a card at mnt/mmc (mounted: tmp/mmc_add), mnt/data, the kernel's files the FUSE path reads (proc's
    filesystems with fuse, mounts, self/oom_score_adj; dev/fuse, a character device), and the stand-ins, on PATH or
    at their Q2 paths. A stand-in daemon still running at the end is stopped."""
    for d in ('tmp', 'mnt/mmc/.vamp/os', 'mnt/data', 'bin', 'usr/bin', 'etc/init.d', 'proc/self', 'dev'):
        (tmp_path/d).mkdir(parents=True)
    (tmp_path/'tmp/mmc_add').write_text('')
    (tmp_path/'proc/filesystems').write_text('nodev\tproc\n\tsquashfs\nnodev\tfuse\n')
    (tmp_path/'proc/mounts').write_text('')
    (tmp_path/'proc/self/oom_score_adj').write_text('0\n')
    (tmp_path/'dev/fuse').symlink_to('/dev/null')
    stubs = {'bin/mount': MOUNT, 'bin/umount': UMOUNT, 'bin/chroot': CHROOT, 'bin/killall': STUB,
             'bin/reboot': STUB, 'bin/usleep': '#!/bin/sh\nsleep 0.05\n', 'etc/init.d/S10mdev': STUB,
             'usr/bin/q2boot': STUB + '[ -e "$Q2VAMPOS_ROOT/held" ]\n', 'usr/bin/squashfuse_ll': SQUASHFUSE}
    for path, text in stubs.items():
        (tmp_path/path).write_text(text)
        (tmp_path/path).chmod(0o755)
    (tmp_path/'rec').write_text('')
    yield tmp_path
    pid = daemon_pid(tmp_path)
    if pid and alive(pid):
        os.kill(pid, 9)


def card(root, image=IMAGE, digest=None, switch='boot-once'):
    d = root/'mnt/mmc/.vamp/os'
    if switch:
        (d/switch).write_text('')
    if image is not None:
        (d/'vampos.squashfs').write_bytes(image)
        (d/'vampos.squashfs.sha256').write_text(f'{digest or hashlib.sha256(image).hexdigest()}  vampos.squashfs\n')
    return d


def run(root):
    env = dict(os.environ, Q2VAMPOS_ROOT=str(root), Q2VAMPOS_WAIT='2', PATH=f'{root/"bin"}:{os.environ["PATH"]}')
    r = subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    rec = (root/'rec').read_text().splitlines()
    log = root/'mnt/data/vamp/handoff.log'
    return r.returncode, rec, log.read_text() if log.exists() else None


def names(rec):
    return [line.split()[0] for line in rec]


def test_no_switch_is_a_normal_boot_and_writes_nothing(root):
    card(root, switch=None)
    code, rec, log = run(root)
    assert code == 1 and rec == [] and log is None


def test_boot_once_hands_off_after_removing_itself(root):
    d = card(root)
    code, rec, log = run(root)
    assert code == 2
    assert not (d/'boot-once').exists()
    # boot-once was gone before the key was read, before anything was mounted
    assert all(line.endswith('once=no') for line in rec)
    assert names(rec) == ['q2boot', 'mount', 'mount', 'mount', 'killall', 'S10mdev', 'chroot', 'chroot']
    assert '-t squashfs -o loop,ro' in rec[1] and rec[2].startswith('mount --bind') and rec[3].startswith('mount --bind')
    assert rec[5].startswith('S10mdev stop') and '/etc/init.d/rcS' in rec[6] and '/usr/bin/runsvdir -P /etc/sv' in rec[7]
    digest = hashlib.sha256(IMAGE).hexdigest()
    assert 'switch: boot-once' in log and f'handing off to {digest}' in log and 'runsvdir ended with 0: reboot' in log


def test_boot_always_is_kept(root):
    d = card(root, switch='boot-always')
    code, rec, log = run(root)
    assert code == 2 and (d/'boot-always').exists() and 'switch: boot-always' in log


def test_boot_once_wins_over_always_and_both_go_on_working(root):
    d = card(root)
    (d/'boot-always').write_text('')
    code, _, log = run(root)
    assert code == 2 and not (d/'boot-once').exists() and (d/'boot-always').exists() and 'switch: boot-once' in log


def test_play_pause_held_is_normal_vamp_and_still_spends_boot_once(root):
    d = card(root)
    (root/'held').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot'] and not (d/'boot-once').exists() and 'Play/Pause held' in log


@pytest.mark.parametrize('image, digest, why', [
    (None, None, 'no vampos.squashfs'),
    (IMAGE, '0' * 64, 'SHA-256 is'),
    (IMAGE, 'short', 'SHA-256 is'),
    (IMAGE + b'x', hashlib.sha256(IMAGE).hexdigest(), 'SHA-256 is'),
])
def test_a_missing_or_wrong_image_is_normal_vamp_with_nothing_mounted(root, image, digest, why):
    d = card(root, image=image, digest=digest)
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot'] and not (d/'boot-once').exists()
    assert why in log and log.rstrip().endswith('normal Vamp')


def test_a_missing_hash_file_is_normal_vamp(root):
    d = card(root)
    (d/'vampos.squashfs.sha256').unlink()
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot'] and 'no vampos.squashfs or vampos.squashfs.sha256' in log


def no_reboot(root, rec):
    """Whether no reboot was asked for, after the script and anything it left behind had a moment to."""
    time.sleep(0.3)
    return 'reboot' not in names((root/'rec').read_text().splitlines()) and 'reboot' not in names(rec)


def test_no_loop_and_no_fuse_is_normal_vamp(root):
    card(root)
    (root/'fail-mount').write_text('')
    (root/'proc/filesystems').write_text('nodev\tproc\n\tsquashfs\n')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount'] and 'no loop device and no FUSE: normal Vamp' in log


def test_no_fuse_device_is_normal_vamp(root):
    card(root)
    (root/'no-loop').write_text('')
    (root/'dev/fuse').unlink()
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount'] and 'no loop device and no FUSE' in log


def test_no_loop_device_mounts_the_image_by_squashfuse_and_hands_off(root):
    d = card(root)
    (root/'no-loop').write_text('')
    code, rec, log = run(root)
    assert code == 2 and not (d/'boot-once').exists()
    assert names(rec) == ['q2boot', 'mount', 'squashfuse_ll', 'mount', 'mount', 'killall', 'S10mdev', 'chroot', 'chroot']
    new = root/'tmp/vamp-os-root'
    assert rec[2] == (f'squashfuse_ll -f -o ro,allow_other,default_permissions,suid {d}/vampos.squashfs {new} '
                      'once=no oom=-1000')
    assert 'no loop device: the image is mounted by squashfuse_ll over FUSE' in log and 'runsvdir ended with 0' in log
    # the daemon serves VampOS until S90play's reboot; its end then is no reason for the supervisor's reboot -f
    pid = daemon_pid(root)
    assert alive(pid)
    os.kill(pid, 9)
    assert gone(pid) and no_reboot(root, rec)


def test_a_daemon_that_does_not_mount_is_normal_vamp_without_a_reboot(root):
    card(root)
    (root/'no-loop').write_text('')
    (root/'fuse-fail').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount', 'squashfuse_ll']
    assert log.rstrip().endswith('no loop device, and squashfuse_ll did not mount the image: normal Vamp')
    assert no_reboot(root, rec)


def test_a_daemon_that_does_not_mount_in_5_s_is_stopped_without_a_reboot(root):
    card(root)
    (root/'no-loop').write_text('')
    (root/'fuse-hang').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount', 'squashfuse_ll', 'umount']
    assert log.rstrip().endswith('squashfuse_ll did not mount the image: normal Vamp')
    assert gone(daemon_pid(root)) and no_reboot(root, rec)


def test_an_image_by_fuse_that_is_not_vamp_os_is_unmounted_without_a_reboot(root):
    card(root)
    (root/'no-loop').write_text('')
    (root/'not-vamp').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount', 'squashfuse_ll', 'umount'] and 'not a VampOS' in log
    assert gone(daemon_pid(root)) and no_reboot(root, rec) and (root/'proc/mounts').read_text() == ''


def test_a_failed_rcs_by_fuse_unmounts_without_a_reboot(root):
    card(root)
    (root/'no-loop').write_text('')
    (root/'rcs-status').write_text('3')
    code, rec, log = run(root)
    assert code == 1 and names(rec)[-3:] == ['chroot', 'umount', 'S10mdev'] and rec[-2].startswith('umount -l')
    assert "rcS failed: normal Vamp" in log and gone(daemon_pid(root)) and no_reboot(root, rec)


def test_the_daemon_killed_mid_boot_reboots(root):
    card(root)
    (root/'no-loop').write_text('')
    (root/'kill-fuse').write_text('')
    code, rec, log = run(root)
    assert 'reboot -f once=no' in rec
    assert 'squashfuse_ll ended with 137 under VampOS: reboot' in log


def test_an_image_that_is_not_vamp_os_is_unmounted(root):
    card(root)
    (root/'not-vamp').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount', 'umount'] and 'not a VampOS root filesystem' in log


def test_a_failed_rcs_unmounts_restarts_mdev_and_is_normal_vamp(root):
    card(root)
    (root/'rcs-status').write_text('3')
    code, rec, log = run(root)
    assert code == 1 and names(rec)[-3:] == ['chroot', 'umount', 'S10mdev']
    assert rec[-2].startswith('umount -l') and rec[-1].startswith('S10mdev start') and "rcS failed: normal Vamp" in log


def test_boot_once_that_cannot_be_removed_is_not_used(root):
    d = card(root)
    d.chmod(0o555)
    try:
        code, rec, log = run(root)
    finally:
        d.chmod(0o755)
    assert code == 1 and rec == [] and (d/'boot-once').exists() and 'boot-once could not be removed' in log



# The real squashfuse_ll over FUSE (the module's docstring)

def fuse_skip():
    """Why the real squashfuse_ll cannot run here, or None."""
    if not os.path.exists('/dev/fuse') or not os.path.isabs(shutil.which('qemu-mipsel') or ''):
        return 'needs /dev/fuse and qemu-mipsel'
    if not shutil.which('mksquashfs') or subprocess.run(['unshare', '-Urm', 'true'], capture_output=True).returncode:
        return 'needs mksquashfs and unshare -Urm'
    return None


def squashfuse_binary():
    """The MIPS squashfuse_ll a --dev build ships: Q2VAMPOS_SQUASHFUSE, else tools/thirdparty.py's (cached)."""
    if os.environ.get('Q2VAMPOS_SQUASHFUSE'):
        return pathlib.Path(os.environ['Q2VAMPOS_SQUASHFUSE'])
    sys.path.insert(0, str(ROOT/'tools'))
    import thirdparty
    return thirdparty.build('squashfuse')/'usr/bin/squashfuse_ll'


BIG = os.urandom(3 << 20)


@pytest.fixture
def fuse_root(root, tmp_path_factory):
    """root, with no loop device and the kernel's own FUSE: squashfuse_ll is the real one under qemu-mipsel (recorded
    as the stand-ins are, then exec'd: its PID is the script's $!), proc's mounts and filesystems and /dev/fuse are
    this machine's, umount is the real one, and the card holds a real image, LZO with 128 KB blocks as VampOS's is,
    with a setuid busybox."""
    why = fuse_skip()
    if why:
        pytest.skip(why)
    binary = squashfuse_binary()
    (root/'usr/bin/squashfuse_ll').write_text(STUB + f'exec qemu-mipsel {binary} "$@"\n')
    for name, target in (('proc/mounts', '/proc/self/mounts'), ('proc/filesystems', '/proc/filesystems'),
                         ('dev/fuse', '/dev/fuse')):
        (root/name).unlink()
        (root/name).symlink_to(target)
    (root/'bin/umount').unlink()
    tree = tmp_path_factory.mktemp('image')/'tree'
    for d in ('etc/init.d', 'usr/bin', 'bin', 'mnt/data', 'mnt/mmc'):
        (tree/d).mkdir(parents=True)
    (tree/'etc/init.d/rcS').write_text('#!/bin/sh\necho rcS\n')
    (tree/'usr/bin/runsvdir').write_text('')
    (tree/'usr/bin/big').write_bytes(BIG)
    (tree/'bin/busybox').write_text('')
    (tree/'bin/busybox').chmod(0o4755)
    image = tree.parent/'vampos.squashfs'
    subprocess.run(['mksquashfs', tree, image, '-comp', 'lzo', '-b', '131072', '-noappend', '-quiet', '-all-root',
                    '-no-progress'], check=True, capture_output=True)
    card(root, image=image.read_bytes())
    (root/'no-loop').write_text('')
    (root/'look').write_text('')
    return root


def run_fuse(root):
    """run, inside a user and mount namespace of its own, where FUSE may mount."""
    env = dict(os.environ, Q2VAMPOS_ROOT=str(root), Q2VAMPOS_WAIT='2', PATH=f'{root/"bin"}:{os.environ["PATH"]}')
    r = subprocess.run(['unshare', '-Urm', 'sh', str(SCRIPT)], env=env, capture_output=True, text=True, timeout=120)
    log = root/'mnt/data/vamp/handoff.log'
    return r.returncode, (root/'rec').read_text().splitlines(), log.read_text() if log.exists() else None


def test_real_squashfuse_mounts_the_image_and_vamp_os_reads_it(fuse_root):
    code, rec, log = run_fuse(fuse_root)
    assert code == 2, log
    assert 'no loop device: the image is mounted by squashfuse_ll over FUSE' in log
    mount, rcs, big, busybox = _seen(fuse_root)
    options = mount.split()[3].split(',')
    assert mount.split()[2] == 'fuse.squashfuse_ll' and {'ro', 'allow_other', 'default_permissions'} <= set(options)
    assert 'nosuid' not in options and busybox == '-rwsr-xr-x'
    assert rcs == 'echo rcS' and big.split()[0] == hashlib.sha256(BIG).hexdigest()
    pid = daemon_pid(fuse_root)
    assert alive(pid)
    os.kill(pid, 9)
    assert gone(pid) and no_reboot(fuse_root, rec)


def _seen(root):
    lines = (root/'seen').read_text().splitlines()
    # the mount's line, rcS's two lines (the second is what it runs), the big file's digest, busybox's mode
    return lines[0], lines[2], lines[3], lines[4]


def test_real_squashfuse_ends_with_the_unmount_without_a_reboot(fuse_root):
    (fuse_root/'rcs-status').write_text('3')
    code, rec, log = run_fuse(fuse_root)
    assert code == 1 and "rcS failed: normal Vamp" in log
    assert gone(daemon_pid(fuse_root)) and no_reboot(fuse_root, rec)


def test_real_squashfuse_killed_mid_boot_reboots(fuse_root):
    (fuse_root/'kill-fuse').write_text('')
    code, rec, log = run_fuse(fuse_root)
    assert 'reboot -f once=no' in rec and 'squashfuse_ll ended with 137 under VampOS: reboot' in log


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
