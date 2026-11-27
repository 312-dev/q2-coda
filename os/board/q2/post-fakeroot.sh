#!/bin/sh
# Buildroot's post-fakeroot step (BR2_ROOTFS_POST_FAKEROOT_SCRIPT), after its users are made: $1 is the folder the
# image is made from. The image is read-only, and packages get users at run time (docs/platform/packages.md#permissions),
# so /etc/passwd and /etc/group point into /run/vamp, where rcS copies the image's own (kept as the .base files) and
# q2pkgd accounts adds each package's user and the permissions' groups.
set -e
t=$1
for f in passwd group; do
    mv "$t/etc/$f" "$t/etc/$f.base"
    ln -s "/run/vamp/$f" "$t/etc/$f"
done
