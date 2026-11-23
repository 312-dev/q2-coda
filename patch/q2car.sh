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
# it); the kernel's last lines go to q2car.log.dmesg at the end. At the next cable a car's log is kept as
# q2car.1.log, the older ones moving to .2, .3 and on, the oldest going once they pass CARLOG_MAX bytes together
# (the newest CARLOG_KEEP whatever their size) or CARLOG_DAYS days (rotate); a computer's or a charger's is kept only until the next, as q2car.other.log, so a
# computer plugged in to read the logs never pushes a drive's out.
# Each cable's summary line and the module's counters at its end are added to q2car-summary.log (SUMMARY), which
# is never rotated, only cut to its newer half past SUMMARY_MAX bytes, so a drive's cables are all in one file.

RUN=${Q2CAR_RUN:-/tmp/q2car}
STATE=/tmp/q2car.state
RELINK=/tmp/q2car.relink # q2-iap's note of a relink under way (patch/usb_car.c holds its pause for it)
CFS=/sys/kernel/config
G=$CFS/usb_gadget/q2car
FFS=${Q2CAR_FFS:-/dev/usb-ffs}
SERIAL=/mnt/data/q2-iap.serial
MODULE=/module_driver/usb_f_uac1.ko
DAEMON=/usr/bin/q2-iap
ADBG=/usr/bin/q2adb-gadget
LOGDIR=${Q2CAR_LOGDIR:-/mnt/mmc/.q2muse/logs} # Q2CAR_RUN, _LOGDIR, _PCM, _PLAYER, _FFS: test/iap_probe.py
PCM=${Q2CAR_PCM:-/proc/asound/UAC1Gadget/pcm0p/sub0/status} # "closed" once nothing has the car's PCM open
PLAYER=${Q2CAR_PLAYER:-hciplayer}                           # q2player, by stock's player's name
mkdir -p "$RUN"
if [ -d "$LOGDIR" ] && touch "$LOGDIR/.q2car-w" 2>/dev/null; then rm -f "$LOGDIR/.q2car-w"; else LOGDIR=$RUN; fi
LOG=$LOGDIR/q2car.log
SUMMARY=$LOGDIR/q2car-summary.log
SUMMARY_MAX=262144
RELINKS=3
CARLOG_MAX=${Q2CAR_CARLOG_MAX:-41943040} # the cars' logs together, bytes: three of q2-iap's longest (its --log-cap, 12 MB) and more
CARLOG_KEEP=2       # the newest cars' logs kept whatever their size: the last drive's beside this one's
CARLOG_DAYS=7

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
    # The Q2 is the source (the reverse of stock's USB DAC), 44.1 kHz 16-bit, four requests in flight: the Q2's dwc2
    # runs isochronous IN in descriptor DMA, whose chain runs dry with one or two (every other packet, or every third,
    # sent empty: the car's stutter of 2026-11-21) and holds with three or more (docs/iphone-mode.md#empty-packets)
    U=$G/functions/uac1.car
    mkdir $U || { log "mkdir $U failed"; return 1; }
    for kv in c_chmask=0 p_chmask=3 p_ssize=2 p_srate=44100 req_number=4; do
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

# The car's PCM let go before the function is unbound: the unbind ends in the kernel's snd_card_free, which waits for
# the card's last open file, and a paused song keeps it open (player/engine.c), so the unbind and the gadget's lock
# waited for the player's next command (2026-11-22: 6.4 s in snd_card_free with the PCM held open, every read of UDC
# waiting behind it, until it closed). The
# player's SIGUSR2 releases it (docs/player.md); 2 s at most, then the unbind goes ahead and waits as before.
pcm_open() { [ -e "$PCM" ] && [ "$(head -c 6 "$PCM")" != closed ]; }
release() {
    pcm_open || return 0
    p=$(pidof $PLAYER)
    [ -n "$p" ] && kill -USR2 $p
    i=0
    while pcm_open && [ $i -lt 20 ]; do usleep 100000; i=$((i + 1)); done
    if pcm_open; then log "the car's PCM still open after 2 s: the gadget removed anyway"; else log "the car's PCM released in ${i}00 ms"; fi
}

# The processes with a file open under $1, by pid.
holders() {
    for p in /proc/[0-9]*; do
        ls -l $p/fd 2>/dev/null | grep -q " $1/" && printf '%s ' "${p#/proc/}"
    done
}

# The four FunctionFS mounts gone; one that will not go says so, with who holds it (a relink cannot start over it).
ffs_unmount() {
    for n in 1 2 3 4; do
        mountpoint -q $FFS/iap$n || continue
        umount $FFS/iap$n 2>>"$LOG" && continue
        log "could not unmount $FFS/iap$n: busy (held by: $(holders $FFS/iap$n))"
    done
}

# The gadget gone, configfs's way, and configfs unmounted, so stock's storage and DAC scripts can start theirs. Safe
# with nothing there.
gadget_down() {
    mountpoint -q $CFS || mount -t configfs none $CFS
    release
    [ -d $G ] && echo "" > $G/UDC 2>/dev/null
    p=$(cat $RUN/daemon.pid 2>/dev/null)
    if alive "$p"; then
        kill "$p"
        i=0
        while alive "$p" && [ $i -lt 20 ]; do usleep 100000; i=$((i + 1)); done
        alive "$p" && kill -9 "$p"
    fi
    rm -f $RUN/daemon.pid /tmp/q2-iap.audio
    ffs_unmount
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
    for f in in_sent in_missed in_enodata in_exdev in_eshutdown in_other in_resent in_bytes in_zero in_gaps2 \
        in_alt_on in_alt_off in_alt_ms p_rate p_srates rate_sets rate_asked resend_missed; do
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
    # A lost iAP2 link (q2-iap's exit 22) is a fresh iPhone for the car: the gadget off the controller and back, at
    # most RELINKS times a cable (docs/iphone-mode.md#the-link-s-pace)
    relinks=0
    while :; do
        if ! gadget_up; then
            log "the gadget could not start"
            grep -q '^verdict=' $STATE 2>/dev/null || echo "verdict=failed" >> $STATE
            break
        fi
        wait "$(cat $RUN/daemon.pid)"
        rc=$?
        p=$(cat $RUN/daemon.pid 2>/dev/null)
        alive "$p" && wait "$p"
        log "q2-iap ended (exit $rc)"
        [ "$rc" = 22 ] && [ $relinks -lt $RELINKS ] || break
        relinks=$((relinks + 1))
        log "the link was lost: the iPhone shown again ($relinks of $RELINKS)"
        gadget_down "relink"
        [ -d $G ] && log "the relink cannot start: $G is still there (FunctionFS busy)"
        rm -f $RUN/kept # the next session's summary is kept too
        sleep 1
    done
    rm -f $RELINK # no relink follows
    gadget_down "end"
    echo "down=1" >> $STATE
    rm -f $RUN/runner.pid
}

# The cable before's log made room for this one's: a car's (q2-iap's "car host: car" verdict) shifted to
# q2car.1.log, the others up one, then the oldest dropped past CARLOG_MAX bytes (never the newest CARLOG_KEEP) or
# CARLOG_DAYS days (never q2car.1.log); any other host's, or a cable no host spoke on, to q2car.other.log in place
# of the one before.
rotate() {
    b=${LOG%.log}
    [ -f "$LOG" ] || return 0
    if ! grep -q '^[0-9.]* car host: car ' "$LOG"; then
        mv -f "$LOG" "$b.other.log"
        rm -f "$b.other.log.dmesg"
        [ -f "$LOG.dmesg" ] && mv -f "$LOG.dmesg" "$b.other.log.dmesg"
        return 0
    fi
    n=1
    while [ -f "$b.$n.log" ]; do n=$((n + 1)); done
    while [ $n -gt 1 ]; do
        mv -f "$b.$((n - 1)).log" "$b.$n.log"
        [ -f "$b.$((n - 1)).log.dmesg" ] && mv -f "$b.$((n - 1)).log.dmesg" "$b.$n.log.dmesg"
        n=$((n - 1))
    done
    mv -f "$LOG" "$b.1.log"
    [ -f "$LOG.dmesg" ] && mv -f "$LOG.dmesg" "$b.1.log.dmesg"
    total=0 k=1
    while [ -f "$b.$k.log" ]; do
        total=$((total + $(cat "$b.$k.log" "$b.$k.log.dmesg" 2>/dev/null | wc -c)))
        if { [ $k -gt $CARLOG_KEEP ] && [ $total -gt $CARLOG_MAX ]; } ||
            { [ $k -gt 1 ] && [ -n "$(find "$b.$k.log" -mtime +$CARLOG_DAYS)" ]; }; then
            while [ -f "$b.$k.log" ]; do rm -f "$b.$k.log" "$b.$k.log.dmesg"; k=$((k + 1)); done
            break
        fi
        k=$((k + 1))
    done
}

start() {
    rotate
    rm -f $STATE $RUN/kept $RELINK
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
    rm -f $RELINK
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
_rotate) rotate ;;   # test/iap_probe.py
_release) release ;; # test/iap_probe.py
_unmount) ffs_unmount ;; # test/iap_probe.py
*) echo "usage: $0 start | stop" >&2; exit 2 ;;
esac
