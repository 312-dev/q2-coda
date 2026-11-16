#!/bin/sh
# /usr/bin/q2adb-gadget, --dev builds only (docs/adb.md#the-usb-gadget): the USB gadget q2adb, adbd's
# FunctionFS function alone, holds the USB controller whenever stock's storage and USB DAC gadgets don't.
#   setup   at boot, before adbd: configfs, the gadget, and its FunctionFS mounted where adbd opens it
#   bind    put q2adb on the controller, once adbd has written its descriptors, unless parked
#   park    take it off the controller, stop adbd, unmount FunctionFS, remove the gadget's directory and unmount
#           configfs: stock's storage_config.sh and uac_device_config.sh refuse to start while configfs is
#           mounted and need the controller, and this kernel's configfs makes each gadget directory a device
#           named android_usb/android0, so a second gadget directory fails (EEXIST) while q2adb's exists
#   unpark  after stock's stop, which unmounts configfs: the gadget made again (setup), and bound in the
#           background once adbd, which its restart loop starts again, has written its descriptors
# The wrapped storage_config.sh and uac_device_config.sh call park and unpark around stock's copies.
# One lock serializes them, and the parked flag keeps a late bind (adbd's restart loop) off stock's gadget.
CFS=${Q2ADB_CONFIGFS:-/sys/kernel/config}
UDCS=${Q2ADB_UDC:-/sys/class/udc}
FFS=${Q2ADB_FFS:-/dev/usb-ffs/adb}
RUN=${Q2ADB_RUN:-/tmp}
G=$CFS/usb_gadget/q2adb
PARKED=$RUN/q2adb.parked

lock() { exec 9>"$RUN/q2adb.lock" && flock 9; }
gadget_make() {
    configfs || return 1
    if [ ! -d "$G" ]; then
        mkdir "$G" && cd "$G" || return 1
        # Linux Foundation's FunctionFS Gadget ID, not stock's storage one: Windows keys its driver choice,
        # and whether it asks for Microsoft OS descriptors at all, on the IDs, so they must be this
        # gadget's own. bcdDevice is unusual so no earlier device's cached answer applies.
        echo 0x1d6b > idVendor
        echo 0x0105 > idProduct
        echo 0x0223 > bcdDevice
        echo 0x0200 > bcdUSB
        echo 0x00 > bDeviceClass
        mkdir strings/0x409
        echo q2-coda > strings/0x409/manufacturer
        echo "Q2 (ADB)" > strings/0x409/product
        echo Q2CODA > strings/0x409/serialnumber
        mkdir configs/c.1 configs/c.1/strings/0x409
        echo 20 > configs/c.1/MaxPower        # stock's gadgets' values
        echo 0x80 > configs/c.1/bmAttributes
        echo adb > configs/c.1/strings/0x409/configuration
        mkdir functions/ffs.adb
        ln -s functions/ffs.adb configs/c.1/
        # Microsoft OS descriptors: adbd's function adds the WINUSB compatible ID (tools/adbd.patch)
        echo 1 > os_desc/use
        echo 0x1 > os_desc/b_vendor_code
        echo MSFT100 > os_desc/qw_sign
        ln -s configs/c.1 os_desc/
    fi
    mkdir -p "$FFS"
    mountpoint -q "$FFS" || mount -t functionfs adb "$FFS"
}
# The gadget's directory gone, configfs's way: off the controller, its links, then its directories
# innermost first. adbd holds FunctionFS open, so it is stopped first (its restart loop starts it
# again; while parked it finds no FunctionFS and ends) and FunctionFS unmounted.
gadget_remove() {
    killall adbd 2>/dev/null
    ! mountpoint -q "$FFS" || umount "$FFS"
    [ -d "$G" ] || return 0
    echo "" 2>/dev/null > "$G/UDC"
    rm -f "$G/os_desc/c.1" "$G/configs/c.1/ffs.adb"
    rmdir "$G/configs/c.1/strings/0x409" "$G/configs/c.1" "$G/functions/ffs.adb" "$G/strings/0x409" "$G"
}
configfs() { mountpoint -q "$CFS" || mount -t configfs none "$CFS"; }

bind() {
    # adbd writes its descriptors within moments of starting; an unready function can't be bound.
    # Waited for outside the lock, so park (demo's UI thread, at a cable in) never waits on it.
    i=0
    while [ ! -e "$FFS/ep1" ] && [ $i -lt 50 ]; do usleep 100000; i=$((i+1)); done
    lock
    [ -f "$PARKED" ] && return 0
    [ -e "$FFS/ep1" ] || { echo "q2adb-gadget: adbd has not set up $FFS" >&2; return 1; }
    configfs && [ -d "$G" ] || return 1
    [ -n "$(cat "$G/UDC")" ] && return 0
    ls "$UDCS" > "$G/UDC"
}

case "$1" in
setup)
    lock
    gadget_make
    ;;
bind)
    bind
    ;;
park)
    lock
    : > "$PARKED"
    gadget_remove
    ! mountpoint -q "$CFS" || umount "$CFS"
    ;;
unpark)
    (lock; rm -f "$PARKED"; gadget_make)
    bind &  # demo runs stock's stop on its UI thread: the wait for adbd is not demo's
    ;;
*)
    echo "usage: q2adb-gadget setup|bind|park|unpark" >&2
    exit 2
    ;;
esac
