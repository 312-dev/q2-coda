#!/bin/sh
# /usr/bin/q2car: Car Audio's gadget (docs/iphone-mode.md#car-audio-in-the-image). demo's USB code (patch/usb_ui.c)
# runs it at a cable in when the USB setting is Automatic: the Q2 shows the host an iPhone, and q2-iap
# (patch/q2_iap.c) decides from what the host asks whether it is a car (it stays, iAP2 runs and the car may take
# the Q2's audio over USB) or not (q2-iap ends, the gadget is removed and demo hands the cable to Transfer Files).
#
#   q2car start    make the gadget, start q2-iap on it, return at once (the rest runs detached)
#   q2car stop     q2-iap ended and the gadget removed now (the cable came out, or demo wants the controller)
#
# The gadget is the drive plan's (tools/iap_probe/q2-iap-probe.sh, phase full:iap2:uac1): Apple's 05ac:12a8, four
# configurations as an iPhone has, configuration 2 the "iPod USB Interface": USB Audio 1.0 from usb_f_uac1.ko with
# the Q2 as the source (interfaces 0 and 1), then q2-iap's FunctionFS HID (interface 2). The module is loaded here,
# the first time Car Audio starts after a boot, and left loaded; nothing loads it at boot.
#
# q2-iap writes what it decided to $STATE (key=value lines, read by demo); this script adds down=1 once the gadget is
# gone, which is when demo may start another gadget. The log is /mnt/mmc/.q2muse/logs/q2car.log (Send Logs takes
# it), the two before it kept as q2car.1.log and q2car.2.log; the kernel's last lines go to q2car.log.dmesg at the end.
# Each cable's summary line and the module's counters at its end are added to q2car-summary.log (SUMMARY), which
# is never rotated, only cut to its newer half past SUMMARY_MAX bytes, so a drive's cables are all in one file.

RUN=/tmp/q2car
STATE=/tmp/q2car.state
CFS=/sys/kernel/config
G=$CFS/usb_gadget/q2car
FFS=/dev/usb-ffs
SERIAL=/mnt/data/q2-iap.serial
MODULE=/module_driver/usb_f_uac1.ko
DAEMON=/usr/bin/q2-iap
ADBG=/usr/bin/q2adb-gadget
LOGDIR=/mnt/mmc/.q2muse/logs
mkdir -p "$RUN"
if [ -d "$LOGDIR" ] && touch "$LOGDIR/.q2car-w" 2>/dev/null; then rm -f "$LOGDIR/.q2car-w"; else LOGDIR=$RUN; fi
LOG=$LOGDIR/q2car.log
SUMMARY=$LOGDIR/q2car-summary.log
SUMMARY_MAX=262144

up() { cut -d' ' -f1 /proc/uptime; }
log() { echo "$(up) sh $*" >> "$LOG"; }
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

# The gadget made and bound, q2-iap on it; 1 if anything refused.
gadget_up() {
    mountpoint -q $CFS || mount -t configfs none $CFS || { log "configfs would not mount"; return 1; }
    for d in $CFS/usb_gadget/*; do
        [ -d "$d" ] && { log "another gadget is there ($d): not starting"; return 1; }
    done
    if [ ! -d /sys/module/usb_f_uac1 ]; then
        insmod $MODULE 2>>"$LOG" || { log "usb_f_uac1.ko would not load: $(dmesg | tail -n 8 | tr '\n' '|')"; return 1; }
        log "usb_f_uac1.ko loaded"
    fi
    mkdir $G || { log "mkdir $G failed"; return 1; }
    echo 0x05ac > $G/idVendor
    echo 0x12a8 > $G/idProduct
    echo 0x1001 > $G/bcdDevice
    echo 0x0200 > $G/bcdUSB
    echo 0x00 > $G/bDeviceClass
    echo 0x00 > $G/bDeviceSubClass
    echo 0x00 > $G/bDeviceProtocol
    mkdir $G/strings/0x409
    echo "Apple Inc." > $G/strings/0x409/manufacturer
    echo "iPhone" > $G/strings/0x409/product
    [ -s $SERIAL ] || head -c 20 /dev/urandom | hexdump -v -e '1/1 "%02x"' > $SERIAL
    cat $SERIAL > $G/strings/0x409/serialnumber
    for n in 1 2 3 4; do
        mkdir $G/configs/c.$n $G/configs/c.$n/strings/0x409
        echo 0xc0 > $G/configs/c.$n/bmAttributes
        echo 500 > $G/configs/c.$n/MaxPower
        mkdir $G/functions/ffs.c$n
    done
    echo "PTP" > $G/configs/c.1/strings/0x409/configuration
    echo "iPod USB Interface" > $G/configs/c.2/strings/0x409/configuration
    echo "PTP + Apple Mobile Device" > $G/configs/c.3/strings/0x409/configuration
    echo "PTP + Apple Mobile Device + Apple USB Ethernet" > $G/configs/c.4/strings/0x409/configuration
    # The Q2 is the source (the reverse of stock's USB DAC), 44.1 kHz 16-bit, one request in flight so a packet the
    # controller gives back unsent goes out again in order (docs/iphone-mode.md#missed-isochronous-packets)
    U=$G/functions/uac1.car
    mkdir $U || { log "mkdir $U failed"; return 1; }
    for kv in c_chmask=0 p_chmask=3 p_ssize=2 p_srate=44100 req_number=1; do
        echo ${kv#*=} > $U/${kv%%=*} 2>/dev/null || log "uac1: ${kv%%=*} refused ${kv#*=}"
    done
    # Link order is interface order: the audio function (0 and 1), then the HID (2), where an iPhone has it
    ln -s $G/functions/ffs.c1 $G/configs/c.1/
    ln -s $U $G/configs/c.2/
    ln -s $G/functions/ffs.c2 $G/configs/c.2/
    ln -s $G/functions/ffs.c3 $G/configs/c.3/
    ln -s $G/functions/ffs.c4 $G/configs/c.4/
    args=
    for n in 1 2 3 4; do
        mkdir -p $FFS/iap$n
        mount -t functionfs c$n $FFS/iap$n || { log "functionfs c$n would not mount"; return 1; }
        case $n in 1) k=ptp ;; 2) k=hid ;; 3) k=ptpmux ;; 4) k=ptpmuxeth ;; esac
        args="$args $FFS/iap$n:$k"
    done
    udc=$(ls /sys/class/udc | head -n 1)
    rm -f $STATE
    $DAEMON --iap2 --uuid "$(cat $SERIAL 2>/dev/null)" --car-pcm hw:CARD=UAC1Gadget,0 --state $STATE \
        --udc /sys/class/udc/$udc/state --counters /sys/module/usb_f_uac1/parameters "$LOG" $args \
        </dev/null >/dev/null 2>>"$LOG" &
    echo $! > $RUN/daemon.pid
    i=0
    for n in 1 2 3 4; do
        while [ ! -e $FFS/iap$n/ep1 ] && [ $i -lt 50 ]; do usleep 100000; i=$((i + 1)); done
        [ -e $FFS/iap$n/ep1 ] || { log "c$n: q2-iap did not set up its endpoints"; return 1; }
    done
    echo "$udc" > $G/UDC 2>>"$LOG" || { log "bind to $udc failed: $(dmesg | tail -n 8 | tr '\n' '|')"; return 1; }
    log "bound to $udc"
    return 0
}

# The gadget gone, configfs's way, and configfs unmounted, so stock's storage and DAC scripts can start theirs. Safe
# with nothing there.
gadget_down() {
    mountpoint -q $CFS || mount -t configfs none $CFS
    [ -d $G ] && echo "" > $G/UDC 2>/dev/null
    p=$(cat $RUN/daemon.pid 2>/dev/null)
    if alive "$p"; then
        kill "$p"
        i=0
        while alive "$p" && [ $i -lt 20 ]; do usleep 100000; i=$((i + 1)); done
        alive "$p" && kill -9 "$p"
    fi
    rm -f $RUN/daemon.pid /tmp/q2-iap.audio
    for n in 1 2 3 4; do mountpoint -q $FFS/iap$n && umount $FFS/iap$n; done
    if [ -d $G ]; then
        rm -f $G/configs/c.*/ffs.c* $G/configs/c.*/uac1.car
        rmdir $G/configs/c.*/strings/0x409 $G/configs/c.* 2>/dev/null
        rmdir $G/functions/* $G/strings/0x409 2>/dev/null
        rmdir $G 2>/dev/null || log "could not remove $G"
    fi
    mountpoint -q $CFS && [ -z "$(ls $CFS/usb_gadget 2>/dev/null)" ] && umount $CFS 2>/dev/null
    { echo "==== $(up) $1"; dmesg | tail -n 120; } >> "$LOG.dmesg"
    keep
}

# The cable's summary (q2-iap's last line) and the module's counters, once a cable, into SUMMARY.
keep() {
    [ -f $RUN/kept ] && return
    : > $RUN/kept
    P=/sys/module/usb_f_uac1/parameters
    m=
    for f in in_sent in_missed p_rate p_srates rate_sets rate_asked resend_missed; do
        [ -e $P/$f ] && m="$m $f=$(cat $P/$f)"
    done
    { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $(up) $(grep ' car summary:' "$LOG" | tail -n 1 | cut -d' ' -f3-)"
      echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $(up) module:$m"; } >> "$SUMMARY"
    if [ "$(wc -c < "$SUMMARY")" -gt $SUMMARY_MAX ]; then
        tail -c $((SUMMARY_MAX / 2)) "$SUMMARY" > "$SUMMARY.new" && mv -f "$SUMMARY.new" "$SUMMARY"
    fi
}

runner() {
    # A stop still removing the last cable's gadget (a cable out and in again within a second or two) goes first: it
    # holds the lock for its whole run (its waits are bounded, a few seconds at most), and this runner takes it only
    # to start after it
    exec 8>$RUN/lock
    flock 8
    flock -u 8
    echo $$ > $RUN/runner.pid
    trap 'p=$(cat $RUN/daemon.pid 2>/dev/null); alive "$p" && kill "$p"' TERM INT HUP
    # A --dev build's ADB gadget holds the controller between stock's gadgets: off it while the iPhone is shown
    [ -x $ADBG ] && $ADBG park >/dev/null 2>&1
    if gadget_up; then
        wait "$(cat $RUN/daemon.pid)"
        rc=$?
        p=$(cat $RUN/daemon.pid 2>/dev/null)
        alive "$p" && wait "$p"
        log "q2-iap ended (exit $rc)"
    else
        log "the gadget could not start"
        grep -q '^verdict=' $STATE 2>/dev/null || echo "verdict=failed" >> $STATE
    fi
    gadget_down "end"
    echo "down=1" >> $STATE
    rm -f $RUN/runner.pid
}

start() {
    # the last three cables' logs: q2car.log, q2car.1.log, q2car.2.log (and their .dmesg)
    for k in 1 0; do
        from=$LOG
        [ $k = 1 ] && from=${LOG%.log}.1.log
        [ -f "$from" ] && mv -f "$from" "${LOG%.log}.$((k + 1)).log" && mv -f "$from.dmesg" "${LOG%.log}.$((k + 1)).log.dmesg" 2>/dev/null
    done
    rm -f $STATE $RUN/kept
    log "start: kernel $(uname -r)"
    setsid sh "$0" _runner </dev/null >/dev/null 2>>"$LOG" &
}

stop() {
    exec 8>$RUN/lock
    flock 8
    r=$(cat $RUN/runner.pid 2>/dev/null)
    if alive "$r"; then
        kill "$r"
        i=0
        while alive "$r" && [ $i -lt 30 ]; do usleep 100000; i=$((i + 1)); done
        alive "$r" && kill -9 "$r"
    fi
    if [ -d $G ] || [ -f $RUN/daemon.pid ]; then
        gadget_down "stop"
        echo "down=1" >> $STATE
    fi
    rm -f $RUN/runner.pid
    log "stopped"
}

case "$1" in
start) start ;;
stop) stop ;;
_runner) runner ;;
*) echo "usage: $0 start | stop" >&2; exit 2 ;;
esac
