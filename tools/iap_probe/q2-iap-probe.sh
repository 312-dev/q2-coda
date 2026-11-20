#!/bin/sh
# /tmp/q2-iap-probe.sh: route 1's experiment 1 (docs/iphone-mode.md). Runs only from /tmp on a --dev build, pushed
# there by tools/iap_probe/q2iap; never part of an update image. It makes the configfs gadget q2iap, an iPhone's
# shape (Apple's 05ac:12a8, four configurations, configuration 2 the "iPod USB Interface": UAC2 audio with the Q2
# as the source, then a FunctionFS HID interface), runs q2-iap-probe on its FunctionFS instances to log what the
# host sends, and hands the controller back to the ADB gadget when done.
#
#   q2-iap-probe.sh start [--plan desk|car|m1|m3|drive|PHASES] [--for S] [--delay S] [--sessions N] [--tone] [--carplay] [--force]
#   q2-iap-probe.sh stop        tear the gadget down now and give the controller back to adb
#   q2-iap-probe.sh status      what runs, the controller's state, the log's last lines
#
# start returns at once; the work runs detached (setsid), because parking the ADB gadget ends adb's own shell. A
# plan is phases run in order, each a gadget made, probed and removed: a phase is full or min (four configurations,
# or configuration 2 alone), with :echo (echo iAP2's detect), :noaudio (no audio function), :uac1 (USB Audio 1.0
# from tools/uac1_module's usb_f_uac1.ko, loaded from beside this script if it is not yet: at the desk only) or :tone
# (play a -20 dBFS 1 kHz tone into the gadget's sound card while it is up), :linuxid (desk only: Linux's
# FunctionFS ID 1d6b:0106 in place of Apple's, so Windows' own audio driver takes the audio function), or :iap2 (the probe speaks iAP2, milestone
# M1). desk is "full:echo:tone" until --for runs out; car is "full full:echo min:echo", m1 "full:iap2 min:iap2" and
# m3 "full:iap2:uac1 full:iap2" (USB Audio 1.0 first, as a real iPhone has; the kernel's UAC2 second), one session (a
# plug, docs/iphone-mode.md#sessions) each, then the restore; drive "full:iap2:uac1" for every plug until --for runs out
# (a drive: replugs and the car off and on at stops are sessions of the one phase).
# --for (default 900, at most 43200) is the hard limit from start: the runner stops there, and a separate watchdog
# forces the stop 60 s later if the runner has not. --delay waits that long first, with adb still up, so the cable
# can be moved from the computer to the car.
#
# Files: the log is /mnt/mmc/.q2muse/logs/q2-iap-probe.log (Send Logs takes it), the probe's lines and this
# script's together; .dmesg beside it gets the kernel log at each phase's end. With the card not mounted they go to
# /tmp/q2-iap/. State is in /tmp/q2-iap/. The iPhone serial number is made once and kept in /mnt/data/q2-iap.serial.

HERE=$(cd "$(dirname "$0")" && pwd)
PROBE=$HERE/q2-iap-probe
TONE=$HERE/q2-iap-tone.raw
RUN=/tmp/q2-iap
CFS=/sys/kernel/config
G=$CFS/usb_gadget/q2iap
FFS=/dev/usb-ffs
SERIAL=/mnt/data/q2-iap.serial
ADBG=/usr/bin/q2adb-gadget
CONFIG_INI=/mnt/data/config.ini
LOGDIR=/mnt/mmc/.q2muse/logs
mkdir -p "$RUN"
if [ -d "$LOGDIR" ] && touch "$LOGDIR/.q2-iap-w" 2>/dev/null; then rm -f "$LOGDIR/.q2-iap-w"; else LOGDIR=$RUN; fi
LOG=$LOGDIR/q2-iap-probe.log

up() { cut -d' ' -f1 /proc/uptime; }
log() { echo "$(up) sh $*" >> "$LOG"; }
alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }
pidof_file() { cat "$RUN/$1.pid" 2>/dev/null; }

# ---- the gadget ----

# gadget_up PHASE: q2iap made and bound for one phase; 1 if the controller would not take it.
gadget_up() {
    configs=full audio=uac2 echo= tone= iap2= vid=0x05ac pid=0x12a8 product=iPhone
    for w in $(echo "$1" | tr ':' ' '); do
        case $w in
        full | min) configs=$w ;;
        echo) echo=--echo ;;
        iap2) iap2="--iap2 --uuid $(cat $SERIAL 2>/dev/null)" ;;
        noaudio) audio=none ;;
        uac1) audio=uac1 ;;
        tone) tone=1 ;;
        linuxid) vid=0x1d6b pid=0x0106 product="Q2 car audio test" ;;
        esac
    done
    cfgs="2"
    [ "$configs" = full ] && cfgs="1 2 3 4"
    mountpoint -q $CFS || mount -t configfs none $CFS || { log "configfs would not mount"; return 1; }
    for d in $CFS/usb_gadget/*; do
        [ -d "$d" ] && { log "another gadget is there ($d): not starting"; return 1; }
    done
    mkdir $G || { log "mkdir $G failed"; return 1; }
    echo $vid > $G/idVendor
    echo $pid > $G/idProduct
    echo 0x1001 > $G/bcdDevice
    echo 0x0200 > $G/bcdUSB
    echo 0x00 > $G/bDeviceClass
    echo 0x00 > $G/bDeviceSubClass
    echo 0x00 > $G/bDeviceProtocol
    mkdir $G/strings/0x409
    echo "Apple Inc." > $G/strings/0x409/manufacturer
    echo "$product" > $G/strings/0x409/product
    [ -s $SERIAL ] || head -c 20 /dev/urandom | hexdump -v -e '1/1 "%02x"' > $SERIAL
    cat $SERIAL > $G/strings/0x409/serialnumber
    for n in $cfgs; do
        mkdir $G/configs/c.$n $G/configs/c.$n/strings/0x409
        echo 0xc0 > $G/configs/c.$n/bmAttributes
        echo 500 > $G/configs/c.$n/MaxPower
        mkdir $G/functions/ffs.c$n
    done
    if [ "$configs" = full ]; then
        echo "PTP" > $G/configs/c.1/strings/0x409/configuration
        echo "PTP + Apple Mobile Device" > $G/configs/c.3/strings/0x409/configuration
        echo "PTP + Apple Mobile Device + Apple USB Ethernet" > $G/configs/c.4/strings/0x409/configuration
    fi
    echo "iPod USB Interface" > $G/configs/c.2/strings/0x409/configuration
    if [ $audio = uac1 ] && [ ! -d /sys/module/usb_f_uac1 ]; then
        # tools/uac1_module's build, pushed beside this script; loaded once, left loaded (a restart clears it)
        if [ -f $HERE/usb_f_uac1.ko ] && insmod $HERE/usb_f_uac1.ko 2>>"$LOG"; then
            log "usb_f_uac1.ko loaded: $(dmesg | tail -n 5 | tr '\n' '|')"
        else
            log "usb_f_uac1.ko would not load: $(dmesg | tail -n 8 | tr '\n' '|')"
            return 1
        fi
    fi
    if [ $audio = uac2 ] || [ $audio = uac1 ]; then
        # The Q2 is the source here: the reverse of stock's USB DAC gadget (uac_device_config.sh). Shanling's
        # f_uac2 is not upstream's, so every attribute is written only if it exists, and all are logged.
        U=$G/functions/$audio.car
        if mkdir $U; then
            for kv in c_chmask=0 p_chmask=3 p_ssize=2 p_srate=44100 \
                p_mute_present=0 p_volume_present=0 c_mute_present=0 c_volume_present=0; do
                k=${kv%%=*} v=${kv#*=}
                [ -e $U/$k ] || { log "$audio: no attribute $k"; continue; }
                echo $v > $U/$k 2>/dev/null || log "$audio: $k refused $v"
            done
            log "$audio attributes: $(for f in $U/*; do printf '%s=%s ' "${f##*/}" "$(tr '\n' ' ' < $f 2>/dev/null)"; done)"
        else
            log "$audio: mkdir $U failed (no $audio function?)"
            audio=none
        fi
    fi
    # Link order is interface order: audio first (interfaces 0 and 1), then the HID (interface 2).
    [ "$configs" = full ] && ln -s $G/functions/ffs.c1 $G/configs/c.1/
    [ $audio != none ] && ln -s $G/functions/$audio.car $G/configs/c.2/
    ln -s $G/functions/ffs.c2 $G/configs/c.2/
    [ "$configs" = full ] && ln -s $G/functions/ffs.c3 $G/configs/c.3/ && ln -s $G/functions/ffs.c4 $G/configs/c.4/
    args=
    for n in $cfgs; do
        mkdir -p $FFS/iap$n
        mount -t functionfs c$n $FFS/iap$n || { log "functionfs c$n would not mount"; return 1; }
        case $n in 1) k=ptp ;; 2) k=hid ;; 3) k=ptpmux ;; 4) k=ptpmuxeth ;; esac
        args="$args $FFS/iap$n:$k"
    done
    left=$((DEADLINE - $(date +%s)))
    # the gadget's card as the player opens it (player/output.c output_car): ALSA's id is the function's card name
    # without its underscore (u_audio: "UAC1_Gadget", "UAC2_Gadget"; seen as [UAC2Gadget] in the car)
    case $audio in uac1) pcm=hw:CARD=UAC1Gadget,0 ;; uac2) pcm=hw:CARD=UAC2Gadget,0 ;; *) pcm=null ;; esac
    $PROBE --phase "$1" --for $left --sessions $SESSIONS $echo $iap2 --car-pcm $pcm $ECHO_FROM $CARPLAY "$LOG" $args </dev/null >/dev/null 2>>"$LOG" &
    echo $! > $RUN/probe.pid
    # every instance's endpoints exist once the probe has written its descriptors
    i=0
    for n in $cfgs; do
        while [ ! -e $FFS/iap$n/ep1 ] && [ $i -lt 50 ]; do usleep 100000; i=$((i + 1)); done
        [ -e $FFS/iap$n/ep1 ] || { log "c$n: the probe did not set up its endpoints"; return 1; }
    done
    cards0=$(cat /proc/asound/cards 2>/dev/null)
    udc=$(ls /sys/class/udc | head -n 1)
    if ! echo "$udc" > $G/UDC 2>>"$LOG"; then
        log "bind to $udc failed; the kernel says: $(dmesg | tail -n 8 | tr '\n' '|')"
        return 1
    fi
    log "bound to $udc: phase $1, configurations $cfgs, audio $audio, max speed $(cat /sys/class/udc/$udc/maximum_speed 2>/dev/null)"
    usleep 300000
    CARD=
    if [ $audio != none ]; then
        log "sound cards now: $(tr '\n' '|' < /proc/asound/cards)"
        CARD=$(cat /proc/asound/cards | grep -E '^ *[0-9]+ \[' | while read -r line; do
            echo "$cards0" | grep -qF "$line" || echo "$line"
        done | sed -n 's/^ *\([0-9]*\) .*/\1/p' | head -n 1)
        log "the gadget's sound card: ${CARD:-not found}"
    fi
    watch_state "$udc" &
    echo $! > $RUN/watch.pid
    if [ -n "$tone" ] && [ -n "$CARD" ] && [ -s "$TONE" ]; then
        play_tone "$CARD" &
        echo $! > $RUN/tone.pid
    fi
    return 0
}

# The controller's state and the cable, a line at each change (the probe sees only what reaches its functions:
# the kernel answers the host's descriptor requests itself).
watch_state() {
    last=
    while [ -d $G ]; do
        s="udc=$(cat /sys/class/udc/$1/state 2>/dev/null) speed=$(cat /sys/class/udc/$1/current_speed 2>/dev/null) usb_online=$(cat /sys/class/power_supply/usb/online 2>/dev/null) battery=$(cat /sys/class/power_supply/battery/status 2>/dev/null)"
        [ "$s" != "$last" ] && log "state $s" && last=$s
        usleep 200000
    done
}

# The test tone into the gadget's playback device, again and again; the first failures logged.
play_tone() {
    n=0
    while [ -d $G ]; do
        if ! err=$(aplay -q -D hw:$1,0 -t raw -f S16_LE -r 44100 -c 2 "$TONE" 2>&1); then
            [ $n -lt 5 ] && log "tone: aplay on hw:$1,0: $(echo "$err" | tr '\n' ' ')"
            n=$((n + 1))
            sleep 1
        fi
    done
}

stop_pid() {
    p=$(pidof_file "$1")
    alive "$p" && kill "$p" 2>/dev/null
    rm -f "$RUN/$1.pid"
}

# The gadget gone, configfs's way: off the controller, the probe ended, FunctionFS unmounted, links, directories
# innermost first. Safe to run with nothing there.
gadget_down() {
    # the USB picker's Charge Only parks q2adb, which unmounts configfs: the gadget is still there, only unseen
    mountpoint -q $CFS || mount -t configfs none $CFS
    if [ -d $G ]; then
        echo "" > $G/UDC 2>/dev/null
    fi
    stop_pid tone
    p=$(pidof_file probe)
    if alive "$p"; then
        kill "$p"
        i=0
        while alive "$p" && [ $i -lt 20 ]; do usleep 100000; i=$((i + 1)); done
        alive "$p" && kill -9 "$p"
    fi
    rm -f $RUN/probe.pid
    killall aplay 2>/dev/null
    for n in 1 2 3 4; do mountpoint -q $FFS/iap$n && umount $FFS/iap$n; done
    if [ -d $G ]; then
        rm -f $G/configs/c.*/ffs.c* $G/configs/c.*/uac2.car $G/configs/c.*/uac1.car
        rmdir $G/configs/c.*/strings/0x409 $G/configs/c.* 2>/dev/null
        rmdir $G/functions/* $G/strings/0x409 2>/dev/null
        rmdir $G 2>/dev/null || log "could not remove $G: $(ls -R $G 2>&1 | tr '\n' ' ')"
    fi
    stop_pid watch
    { echo "==== $(up) $1"; dmesg | tail -n 300; } >> "$LOG.dmesg"
}

# The controller back to adb (a --dev build's q2adb gadget), or simply configfs unmounted on a build without it.
restore() {
    mountpoint -q $CFS && [ ! -d $G ] && umount $CFS 2>/dev/null
    if [ -x $ADBG ]; then
        $ADBG unpark >/dev/null 2>&1
        log "restored: q2adb unparked"
    else
        log "restored: no q2adb-gadget on this build"
    fi
    rm -f $RUN/runner.pid $RUN/phase
}

# ---- the runner (detached) ----

runner() {
    echo $$ > $RUN/runner.pid
    STOP=
    trap 'STOP=1; p=$(pidof_file probe); alive "$p" && kill "$p"' TERM INT HUP
    log "runner: plan '$PLAN', until $(date -d @$DEADLINE 2>/dev/null || echo $DEADLINE), delay $DELAY s, sessions $SESSIONS"
    # at least 2 s, so start's own lines reach the computer before parking ends adb's shell
    while [ -z "$STOP" ] && [ $(($(date +%s) - START)) -lt $((DELAY < 2 ? 2 : DELAY)) ]; do sleep 1; done
    if [ -z "$STOP" ]; then
        pick=$(grep -o 'USBPICK=[0-9]*' $CONFIG_INI 2>/dev/null | head -n 1 | cut -d= -f2)
        [ "$pick" = 1 ] || log "warning: the USB picker is not Charge Only (USBPICK=${pick:-unset}): a cable in may start demo's own gadget"
        $ADBG park >/dev/null 2>&1
        log "q2adb parked"
    fi
    for ph in $PLAN; do
        [ -n "$STOP" ] && break
        [ "$(date +%s)" -ge $DEADLINE ] && log "time is up before phase $ph" && break
        echo "$ph" > $RUN/phase
        log "phase $ph begins"
        if gadget_up "$ph"; then
            wait "$(pidof_file probe)"
            rc=$?
            [ -n "$STOP" ] && { p=$(pidof_file probe); alive "$p" && wait "$p"; }
            log "phase $ph ends (probe exit $rc)"
        else
            log "phase $ph could not start"
        fi
        gadget_down "phase $ph"
    done
    restore
    log "runner done"
}

# The watchdog (detached): at the deadline plus 60 s, if the runner or the gadget is still there, the forced stop.
watchdog() {
    echo $$ > $RUN/watchdog.pid
    until [ "$(date +%s)" -ge $(($1 + 60)) ]; do
        sleep 5
        alive "$(pidof_file runner)" || [ -d $G ] || [ -f /tmp/q2adb.parked ] || { rm -f $RUN/watchdog.pid; exit 0; }
    done
    log "watchdog: past the deadline, forcing the stop"
    rm -f $RUN/watchdog.pid
    stop
}

# ---- commands ----

start() {
    PLAN=desk FOR=900 DELAY=0 SESSIONS= FORCE= TONEALL= CARPLAY= ECHO_FROM=
    while [ $# -gt 0 ]; do
        case $1 in
        --plan) PLAN=$2; shift ;;
        --for) FOR=$2; shift ;;
        --delay) DELAY=$2; shift ;;
        --sessions) SESSIONS=$2; shift ;;
        --tone) TONEALL=:tone ;;
        --carplay) CARPLAY=--carplay ;;
        --force) FORCE=1 ;;
        *) echo "q2-iap-probe.sh: unknown option $1" >&2; exit 2 ;;
        esac
        shift
    done
    case $PLAN in
    desk) PLAN="full:echo:tone" SESSIONS=${SESSIONS:-0} ;;
    car) PLAN="full full:echo min:echo" SESSIONS=${SESSIONS:-1} ;;
    m1) PLAN="full:iap2 min:iap2" SESSIONS=${SESSIONS:-1} ;;
    m3) PLAN="full:iap2:uac1 full:iap2" SESSIONS=${SESSIONS:-1} ;;
    drive) PLAN="full:iap2:uac1" SESSIONS=${SESSIONS:-0} ;;
    *) SESSIONS=${SESSIONS:-0} ;;
    esac
    [ -n "$TONEALL" ] && PLAN=$(for p in $PLAN; do printf '%s%s ' "$p" "$TONEALL"; done)
    case $FOR in '' | *[!0-9]*) echo "--for takes seconds" >&2; exit 2 ;; esac
    case $DELAY in '' | *[!0-9]*) echo "--delay takes seconds" >&2; exit 2 ;; esac
    [ "$FOR" -gt 43200 ] && FOR=43200
    [ "$FOR" -le "$DELAY" ] && { echo "--for must be longer than --delay" >&2; exit 2; }
    [ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
    [ -x "$PROBE" ] || { echo "no $PROBE: push it first (tools/iap_probe/q2iap push)" >&2; exit 1; }
    alive "$(pidof_file runner)" && { echo "already running (q2-iap-probe.sh stop first)" >&2; exit 1; }
    [ -d $G ] && { echo "$G is there from an earlier run: q2-iap-probe.sh stop first" >&2; exit 1; }
    pick=$(grep -o 'USBPICK=[0-9]*' $CONFIG_INI 2>/dev/null | head -n 1 | cut -d= -f2)
    # demo starts stock's storage gadget at a cable in unless Charge Only is remembered (patch/usb_ui.c). USB
    # Debugging (4) is taken too: arming needs adb, and Charge Only picked with the cable in parks it, so the picker is
    # switched to Charge Only after this, within --delay; the runner checks it again then.
    if [ "$pick" != 1 ] && [ "$pick" != 4 ] && [ -z "$FORCE" ]; then
        echo "Settings > General > USB is neither Charge Only nor USB Debugging (USBPICK=${pick:-unset}): set it, or pass --force" >&2
        exit 1
    fi
    [ "$pick" = 4 ] && echo "USB Debugging is on: switch Settings > General > USB to Charge Only before the delay ends." 
    [ -f "$LOG" ] && mv -f "$LOG" "${LOG%.log}.prev.log" && mv -f "$LOG.dmesg" "${LOG%.log}.prev.log.dmesg" 2>/dev/null
    START=$(date +%s)
    DEADLINE=$((START + FOR))
    log "start: plan '$PLAN', for $FOR s, delay $DELAY s, sessions $SESSIONS, kernel $(uname -r), $(cat /proc/version | cut -c1-120)"
    export PLAN DEADLINE DELAY SESSIONS START CARPLAY ECHO_FROM
    # adb's shell ends as soon as this returns, and its hangup reached the children before setsid had moved them
    # to their own session (seen on the Q2: the runner never logged). Ignored here, it is ignored in them too.
    trap '' HUP
    setsid sh "$0" _runner </dev/null >/dev/null 2>>"$LOG" &
    setsid sh "$0" _watchdog $DEADLINE </dev/null >/dev/null 2>&1 &
    i=0
    while ! alive "$(pidof_file runner)" && [ $i -lt 30 ]; do usleep 100000; i=$((i + 1)); done
    alive "$(pidof_file runner)" || { echo "q2-iap-probe: the runner did not start; see $LOG" >&2; exit 1; }
    echo "q2-iap-probe: started, plan '$PLAN', restores by itself within $FOR s (watchdog at +60 s)."
    [ "$DELAY" -gt 0 ] && echo "adb stays up for $DELAY s; then the Q2 becomes an iPhone on its USB port." ||
        echo "adb goes away now; it comes back when the run ends."
    echo "log: $LOG"
}

stop() {
    r=$(pidof_file runner)
    if alive "$r"; then
        kill "$r"
        i=0
        while alive "$r" && [ $i -lt 50 ]; do usleep 200000; i=$((i + 1)); done
    fi
    if alive "$r" || [ -d $G ] || [ -f /tmp/q2adb.parked ]; then
        alive "$r" && kill -9 "$r"
        log "stop: forced"
        gadget_down "stop"
        restore
    fi
    w=$(pidof_file watchdog)
    [ "$w" != "$$" ] && alive "$w" && kill "$w" 2>/dev/null
    rm -f $RUN/watchdog.pid
    sync
    echo "q2-iap-probe: stopped; log $LOG"
}

status() {
    r=$(pidof_file runner)
    if alive "$r"; then echo "running: phase $(cat $RUN/phase 2>/dev/null), probe $(pidof_file probe)"; else echo "not running"; fi
    [ -d $G ] && echo "gadget q2iap is there, UDC=$(cat $G/UDC 2>/dev/null)"
    [ -f /tmp/q2adb.parked ] && echo "q2adb is parked"
    for u in /sys/class/udc/*; do echo "${u##*/}: $(cat $u/state 2>/dev/null)"; done
    echo "log: $LOG"
    tail -n 15 "$LOG" 2>/dev/null
}

case "$1" in
start) shift; start "$@" ;;
stop) stop ;;
status) status ;;
_runner) runner ;;
_watchdog) watchdog "$2" ;;
*) echo "usage: $0 start [--plan desk|car|m1|m3|PHASES] [--for S] [--delay S] [--sessions N] [--tone] [--carplay] [--force] | stop | status" >&2; exit 2 ;;
esac
