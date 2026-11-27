#!/usr/bin/env python3
"""The card boot's hand-off (patch/q2vampos.sh, docs/vamp-os-base.md#card-boot) under this machine's sh, with the
commands that would reach the kernel (mount, umount, chroot, killall) and the Q2's own (q2boot, S10mdev) as
stand-ins that record their arguments: the decision (switch, key, image, hash), boot-once removed before anything
is mounted, boot-always kept, each failure ending in exit 1 (S90play's normal Vamp) with its line in the log, and a
boot with no switch writing nothing. Run it as python3 test/q2vampos.py (pytest, sh). Q2VAMPOS_SCRIPT names another
copy of the script, to show a broken one fails here."""
import hashlib
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(os.environ.get('Q2VAMPOS_SCRIPT') or pathlib.Path(__file__).resolve().parents[1]/'patch/q2vampos.sh')
IMAGE = b'a stand-in for vampos.squashfs\n'

# Each stand-in appends "NAME ARGS once=yes|no" to $R/rec: whether boot-once was still there when it ran.
STUB = '''#!/bin/sh
o=no; [ -e "$Q2VAMPOS_ROOT/mnt/mmc/.vamp/os/boot-once" ] && o=yes
echo "${0##*/} $* once=$o" >> "$Q2VAMPOS_ROOT/rec"
'''
MOUNT = STUB + '''case "$*" in
*loop*)
    [ -e "$Q2VAMPOS_ROOT/fail-mount" ] && exit 32
    [ -e "$Q2VAMPOS_ROOT/not-vamp" ] && exit 0
    eval "new=\\${$#}"
    mkdir -p "$new/etc/init.d" "$new/usr/bin" "$new/mnt/data" "$new/mnt/mmc"
    : > "$new/etc/init.d/rcS"; : > "$new/usr/bin/runsvdir" ;;
esac
'''
CHROOT = STUB + '''case "$2" in
/etc/init.d/rcS) exit "$(cat "$Q2VAMPOS_ROOT/rcs-status" 2>/dev/null || echo 0)" ;;
/usr/bin/runsvdir) exit 0 ;;
esac
exit 99
'''


@pytest.fixture
def root(tmp_path):
    """$R: a card at mnt/mmc (mounted: tmp/mmc_add), mnt/data, and the stand-ins, on PATH or at their Q2 paths."""
    for d in ('tmp', 'mnt/mmc/.vamp/os', 'mnt/data', 'bin', 'usr/bin', 'etc/init.d'):
        (tmp_path/d).mkdir(parents=True)
    (tmp_path/'tmp/mmc_add').write_text('')
    stubs = {'bin/mount': MOUNT, 'bin/umount': STUB, 'bin/chroot': CHROOT, 'bin/killall': STUB,
             'bin/usleep': '#!/bin/sh\nexit 0\n', 'etc/init.d/S10mdev': STUB,
             'usr/bin/q2boot': STUB + '[ -e "$Q2VAMPOS_ROOT/held" ]\n'}
    for path, text in stubs.items():
        (tmp_path/path).write_text(text)
        (tmp_path/path).chmod(0o755)
    (tmp_path/'rec').write_text('')
    return tmp_path


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


def test_a_failed_loop_mount_is_normal_vamp(root):
    card(root)
    (root/'fail-mount').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount'] and 'loop mount of the image failed' in log


def test_an_image_that_is_not_vamp_os_is_unmounted(root):
    card(root)
    (root/'not-vamp').write_text('')
    code, rec, log = run(root)
    assert code == 1 and names(rec) == ['q2boot', 'mount', 'umount'] and 'not a Vamp OS root filesystem' in log


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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
