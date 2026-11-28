#!/bin/sh
# q2vampos: a --dev build's hand-off to a VampOS image on the card (docs/boot.md#vampos-card-boot). S90play runs it
# before demo (tools/build.py VAMPOS_HOOK). Nothing is written to flash but its log: the image is mounted read-only
# from the card, by a loop device or, on a kernel with none, by squashfuse_ll over FUSE, and run in a chroot, on the
# kernel and the modules stock's boot has already loaded.
#
# It hands off only when the card's .vamp/os holds boot-once or boot-always, Play/Pause is not held (q2boot), and
# vampos.squashfs has the SHA-256 that vampos.squashfs.sha256 names. boot-once is removed before anything else is
# done, so a hang and a power cycle always come back to Vamp. Exit status: 2, VampOS ran and its runsvdir ended
# (S90play reboots); 1, not handed off, and S90play goes on to demo as on any boot, as it does for any status but 2.
# While VampOS runs it does not return.
# Each decision and outcome is a line in /mnt/data/vamp/handoff.log; a boot without a switch writes nothing.
# Q2VAMPOS_ROOT prefixes every path, and Q2VAMPOS_WAIT is how many 200 ms waits for the card, for test/q2vampos.py.
R=${Q2VAMPOS_ROOT:-}
d=$R/mnt/mmc/.vamp/os
img=$d/vampos.squashfs
new=$R/tmp/vamp-os-root
log=$R/mnt/data/vamp/handoff.log

say() {
    mkdir -p "${log%/*}" && echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$log"
    sync
}

# the card's mount (stock's add_removesd.sh touches /tmp/mmc_add), as S90play waits for Rockbox's
i=0
while [ $i -lt "${Q2VAMPOS_WAIT:-15}" ] && [ ! -e $R/tmp/mmc_add ]; do usleep 200000; i=$((i + 1)); done

mode=
[ -f $d/boot-always ] && mode=always
[ -f $d/boot-once ] && mode=once
[ "$mode" ] || exit 1
if [ $mode = once ]; then
    rm -f $d/boot-once
    sync
    if [ -e $d/boot-once ]; then
        say "boot-once could not be removed: normal Vamp"
        exit 1
    fi
fi
say "switch: boot-$mode"
if $R/usr/bin/q2boot; then
    say "Play/Pause held: normal Vamp"
    exit 1
fi
if [ ! -f $img ] || [ ! -f $img.sha256 ]; then
    say "no vampos.squashfs or vampos.squashfs.sha256 in .vamp/os: normal Vamp"
    exit 1
fi
want=$(cut -d ' ' -f 1 $img.sha256)
got=$(sha256sum $img | cut -d ' ' -f 1)
if [ ${#want} -ne 64 ] || [ "$want" != "$got" ]; then
    say "image's SHA-256 is $got, not $want: normal Vamp"
    exit 1
fi
# The image's FUSE daemon, when it is mounted by one: squashfuse_ll in the foreground (its PID in
# /tmp/vamp-os-fuse.pid), started by a supervisor that waits on it. Every page of VampOS's programs is read through
# it, so it is out of the OOM killer's reach (its oom_score_adj, inherited from the subshell that execs it) and of
# every killall here and in the image (they name their programs). If it ends while the image is still mounted, the
# supervisor logs it (say syncs) and reboots at once (reboot -f: VampOS's programs can no longer be read to stop
# them), and with boot-once spent that is today's Vamp; a daemon that ends unmounted (it never mounted, or the
# image was unmounted here) ends the supervisor too. Mounted suid, as a loop mount is: the image's busybox is setuid
# root. stdin and stdout go nowhere, its messages to the log.
fuse_mount() {
    [ -c $R/dev/fuse ] && grep -qw fuse $R/proc/filesystems || return 3
    (
        (echo -1000 > $R/proc/self/oom_score_adj && exec $R/usr/bin/squashfuse_ll -f \
            -o ro,allow_other,default_permissions,suid $img $new 2>> "$log") &
        echo $! > $R/tmp/vamp-os-fuse.pid
        wait $!
        status=$?
        grep -q " $new " $R/proc/mounts || exit 0
        say "squashfuse_ll ended with $status under VampOS: reboot"
        reboot -f
    ) < /dev/null > /dev/null 2>&1 &
    sup=$!
    i=0
    while ! grep -q " $new " $R/proc/mounts; do
        i=$((i + 1))
        if ! kill -0 $sup 2> /dev/null; then
            return 1
        fi
        if [ $i -gt 50 ]; then
            # 5 s and not mounted: stopped, unmounted first in case it mounted just now
            umount $new 2> /dev/null
            kill "$(cat $R/tmp/vamp-os-fuse.pid)" 2> /dev/null
            return 1
        fi
        usleep 100000
    done
}
sup=
mkdir -p $new
if ! mount -t squashfs -o loop,ro $img $new 2> /dev/null; then
    fuse_mount
    case $? in
    0) say "no loop device: the image is mounted by squashfuse_ll over FUSE" ;;
    3)
        say "no loop device and no FUSE: normal Vamp"
        exit 1
        ;;
    *)
        say "no loop device, and squashfuse_ll did not mount the image: normal Vamp"
        exit 1
        ;;
    esac
fi
if [ ! -f $new/etc/init.d/rcS ] || [ ! -e $new/usr/bin/runsvdir ]; then
    umount $new
    say "the image is not a VampOS root filesystem: normal Vamp"
    exit 1
fi
if ! mount --bind $R/mnt/data $new/mnt/data || ! mount --bind $R/mnt/mmc $new/mnt/mmc; then
    umount -l $new
    say "bind mounts into the image failed: normal Vamp"
    exit 1
fi
# what of stock's boot would fight VampOS for the screen or the device nodes: the splash and mdev's daemon
killall q2splash display_logo 2> /dev/null
$R/etc/init.d/S10mdev stop > /dev/null 2>&1
say "handing off to $got"
if ! chroot $new /etc/init.d/rcS; then
    umount -l $new
    $R/etc/init.d/S10mdev start > /dev/null 2>&1
    say "VampOS's rcS failed: normal Vamp"
    exit 1
fi
say "rcS done; VampOS's services start (runsvdir)"
chroot $new /usr/bin/runsvdir -P /etc/sv
say "runsvdir ended with $?: reboot"
# S90play's reboot stops the daemon with everything else; the supervisor must not turn that into a reboot -f
[ "$sup" ] && kill $sup 2> /dev/null
exit 2
