#!/bin/sh
# Buildroot's post-build step (BR2_ROOTFS_POST_BUILD_SCRIPT): $1 is the target folder.
# /module_driver and /firmware are the paths Shanling's module scripts, the Wi-Fi driver and rtk_hciattach
# name; they point into /vendor, which is not in this image: tools/vampos.py compose appends it, from the
# user's own stock firmware (docs/vamp-os-base.md#the-vendor-set).
set -e
t=$1
test ! -e "$t/vendor"
ln -sfn /vendor/module_driver "$t/module_driver"
ln -sfn /vendor/firmware "$t/firmware"
mkdir -p "$t/mnt/data" "$t/mnt/mmc"
# /var/run a link to the /run tmpfs, as Buildroot's sysv and systemd skeletons have it (BR2_INIT_NONE's has none, so a
# package's install leaves a directory there on the read-only root): wpa_supplicant's control socket is made under it
rm -rf "$t/var/run"
ln -sfn ../run "$t/var/run"
# D-Bus's machine id (/etc/sv/dbus/run makes it each boot): /var/lib/dbus on the tmpfs, the root being read-only
if [ -x "$t/usr/bin/dbus-daemon" ]; then
    mkdir -p "$t/var/lib"
    rm -rf "$t/var/lib/dbus"
    ln -sfn /run/dbus "$t/var/lib/dbus"
fi
# runsv keeps its state in each service's supervise/, on the read-only image a link into /run (rcS makes them)
for s in "$t"/etc/sv/*; do
    ln -sfn "/run/runit/supervise.${s##*/}" "$s/supervise"
done
# The time zone (docs/sysd.md#the-clock): /etc/localtime a link to the one q2sysd sets in /run/vamp, and the zone page's
# index-to-zone table it reads, from tools/build.py's TIMEZONES
ln -sfn /run/vamp/localtime "$t/etc/localtime"
python3 "$VAMP_SRC/vamp/tools/vampos.py" zones "$t/usr/share/vamp/timezones"
printf 'VAMP_COMMIT=%s\nQ2UI_COMMIT=%s\n' "$(cat "$VAMP_SRC/vamp/COMMIT")" "$(cat "$VAMP_SRC/q2ui/COMMIT")" > "$t/etc/vamp-release"
