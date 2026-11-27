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
# runsv keeps its state in each service's supervise/, on the read-only image a link into /run (rcS makes them)
for s in "$t"/etc/sv/*; do
    ln -sfn "/run/runit/supervise.${s##*/}" "$s/supervise"
done
printf 'VAMP_COMMIT=%s\nQ2UI_COMMIT=%s\n' "$(cat "$VAMP_SRC/vamp/COMMIT")" "$(cat "$VAMP_SRC/q2ui/COMMIT")" > "$t/etc/vamp-release"
