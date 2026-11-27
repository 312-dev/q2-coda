#!/bin/sh
# q2vampos: a --dev build's hand-off to a Vamp OS image on the card (docs/vamp-os-base.md#card-boot). S90play runs it
# before demo (tools/build.py VAMPOS_HOOK). Nothing is written to flash but its log: the image is loop-mounted from
# the card and run in a chroot, on the kernel and the modules stock's boot has already loaded.
#
# It hands off only when the card's .vamp/os holds boot-once or boot-always, Play/Pause is not held (q2boot), and
# vampos.squashfs has the SHA-256 that vampos.squashfs.sha256 names. boot-once is removed before anything else is
# done, so a hang and a power cycle always come back to Vamp. Exit status: 2, Vamp OS ran and its runsvdir ended
# (S90play reboots); 1, not handed off, and S90play goes on to demo as on any boot, as it does for any status but 2.
# While Vamp OS runs it does not return.
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
mkdir -p $new
if ! mount -t squashfs -o loop,ro $img $new; then
    say "loop mount of the image failed: normal Vamp"
    exit 1
fi
if [ ! -f $new/etc/init.d/rcS ] || [ ! -e $new/usr/bin/runsvdir ]; then
    umount $new
    say "the image is not a Vamp OS root filesystem: normal Vamp"
    exit 1
fi
if ! mount --bind $R/mnt/data $new/mnt/data || ! mount --bind $R/mnt/mmc $new/mnt/mmc; then
    umount -l $new
    say "bind mounts into the image failed: normal Vamp"
    exit 1
fi
# what of stock's boot would fight Vamp OS for the screen or the device nodes: the splash and mdev's daemon
killall q2splash display_logo 2> /dev/null
$R/etc/init.d/S10mdev stop > /dev/null 2>&1
say "handing off to $got"
if ! chroot $new /etc/init.d/rcS; then
    umount -l $new
    $R/etc/init.d/S10mdev start > /dev/null 2>&1
    say "Vamp OS's rcS failed: normal Vamp"
    exit 1
fi
say "rcS done; Vamp OS's services start (runsvdir)"
chroot $new /usr/bin/runsvdir -P /etc/sv
say "runsvdir ended with $?: reboot"
exit 2
