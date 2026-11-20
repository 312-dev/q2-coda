#!/bin/sh
# /tmp/uac1-steps.sh: the UAC1 module's desk test in steps (docs/iphone-mode.md#the-uac1-module). Desk only, from /tmp,
# never in an image. Its first try (2026-11-20) hung in the gadget's bind: the write of the controller's name to UDC
# never returned and q2guard's log stopped with it. Here each step is a line on the card and a sync before the next,
# the module's function bound alone (no FunctionFS, no iAP2, Linux's 1d6b:0106 so Windows' audio driver takes it),
# and a bind that has not returned in BIND_WAIT seconds has its kernel stack, every blocked task's and the kernel log
# written to the card before anything else is tried. A hung bind leaves the Q2 needing a restart (hold Power): the log
# says so.
#
#   uac1-steps.sh run [SECONDS]   load, make, bind, play the tone for SECONDS (default 40), unbind, remove
#   uac1-steps.sh log             the last run's log
HERE=$(cd "$(dirname "$0")" && pwd)
KO=$HERE/usb_f_uac1.ko
TONE=$HERE/q2-iap-tone.raw
CFS=/sys/kernel/config
G=$CFS/usb_gadget/q2uac1
ADBG=/usr/bin/q2adb-gadget
LOGDIR=/mnt/mmc/.q2muse/logs
[ -d "$LOGDIR" ] || LOGDIR=/tmp
LOG=$LOGDIR/q2-uac1-steps.log
BIND_WAIT=10

step() { echo "$(cut -d' ' -f1 /proc/uptime) $*" >> "$LOG"; sync; }

# Every task in D state (waiting, uninterruptible) with its kernel stack, then sysrq's blocked-task dump in dmesg.
dump_blocked() {
    for d in /proc/[0-9]*; do
        s=$(cut -d' ' -f3 "$d/stat" 2>/dev/null)
        [ "$s" = D ] || continue
        { echo "---- pid ${d#/proc/} $(tr '\0' ' ' < "$d/cmdline" 2>/dev/null) wchan $(cat "$d/wchan" 2>/dev/null)"
          cat "$d/stack" 2>/dev/null; } >> "$LOG"
    done
    echo w > /proc/sysrq-trigger 2>/dev/null
    { echo "---- dmesg"; dmesg | tail -n 200; } >> "$LOG"
    sync
}

run() {
    secs=${1:-40}
    : > "$LOG"
    step "start: $(cat /proc/version | cut -c1-80)"
    [ -f "$KO" ] || { step "no $KO"; exit 1; }
    step "1 park adb"
    $ADBG park >/dev/null 2>&1
    step "2 insmod"
    if [ -d /sys/module/usb_f_uac1 ]; then step "2 already loaded"
    elif insmod "$KO" 2>>"$LOG"; then step "2 loaded: $(dmesg | tail -n 3 | tr '\n' '|')"
    else step "2 insmod failed: $(dmesg | tail -n 8 | tr '\n' '|')"; $ADBG unpark; exit 1; fi
    step "3 configfs"
    mountpoint -q $CFS || mount -t configfs none $CFS
    mkdir $G && cd $G || { step "3 mkdir failed"; $ADBG unpark; exit 1; }
    echo 0x1d6b > idVendor; echo 0x0106 > idProduct; echo 0x0100 > bcdDevice; echo 0x0200 > bcdUSB
    mkdir strings/0x409; echo q2-coda > strings/0x409/manufacturer; echo "Q2 UAC1 test" > strings/0x409/product
    echo Q2UAC1 > strings/0x409/serialnumber
    mkdir configs/c.1 configs/c.1/strings/0x409; echo uac1 > configs/c.1/strings/0x409/configuration
    echo 0x80 > configs/c.1/bmAttributes; echo 100 > configs/c.1/MaxPower
    step "4 mkdir functions/uac1.t"
    mkdir functions/uac1.t || { step "4 failed"; exit 1; }
    for kv in c_chmask=0 p_chmask=3 p_srate=44100 p_ssize=2; do
        echo ${kv#*=} > functions/uac1.t/${kv%%=*} 2>>"$LOG" || step "4 ${kv%%=*} refused"
    done
    step "4 attributes: $(for f in functions/uac1.t/*; do printf '%s=%s ' "${f##*/}" "$(cat $f)"; done)"
    ln -s functions/uac1.t configs/c.1/
    step "5 bind (the step that hung): $(ls /sys/class/udc)"
    ( ls /sys/class/udc > UDC; echo "$(cut -d' ' -f1 /proc/uptime) 5 bind returned $?" >> "$LOG"; sync ) &
    bp=$!
    i=0
    while kill -0 $bp 2>/dev/null && [ $i -lt $((BIND_WAIT * 10)) ]; do usleep 100000; i=$((i + 1)); done
    if kill -0 $bp 2>/dev/null; then
        step "5 bind still blocked after ${BIND_WAIT} s: the stacks follow; the Q2 needs a restart (hold Power)"
        { echo "---- the bind's shell, pid $bp, wchan $(cat /proc/$bp/wchan 2>/dev/null)"; cat /proc/$bp/stack 2>/dev/null
          for c in $(cat /proc/$bp/task/*/children 2>/dev/null); do echo "---- child $c wchan $(cat /proc/$c/wchan)"; cat /proc/$c/stack; done
        } >> "$LOG" 2>&1
        dump_blocked
        step "5 stopped here; nothing more is tried"
        exit 3
    fi
    step "6 bound: $(cat /sys/class/udc/*/state); cards: $(tr '\n' '|' < /proc/asound/cards)"
    card=$(grep -E '^ *[0-9]+ \[UAC1Gadget' /proc/asound/cards | sed -n 's/^ *\([0-9]*\) .*/\1/p')
    step "7 tone into card ${card:-none} for $secs s (the computer records it)"
    end=$(($(date +%s) + secs))
    while [ -n "$card" ] && [ "$(date +%s)" -lt $end ]; do
        aplay -q -D hw:$card,0 -t raw -f S16_LE -r 44100 -c 2 "$TONE" 2>>"$LOG" || { step "7 aplay failed"; sleep 1; }
    done
    step "8 unbind"
    ( echo "" > UDC; echo "$(cut -d' ' -f1 /proc/uptime) 8 unbind returned $?" >> "$LOG"; sync ) &
    up=$!
    i=0
    while kill -0 $up 2>/dev/null && [ $i -lt $((BIND_WAIT * 10)) ]; do usleep 100000; i=$((i + 1)); done
    if kill -0 $up 2>/dev/null; then
        step "8 unbind still blocked: the stacks follow; the Q2 needs a restart (hold Power)"
        cat /proc/$up/stack >> "$LOG" 2>&1
        dump_blocked
        exit 3
    fi
    step "9 remove"
    rm -f configs/c.1/uac1.t
    rmdir configs/c.1/strings/0x409 configs/c.1 functions/uac1.t strings/0x409
    cd / && rmdir $G
    umount $CFS 2>/dev/null
    step "10 adb back"
    $ADBG unpark >/dev/null 2>&1
    { echo "---- dmesg"; dmesg | tail -n 60; } >> "$LOG"
    step "done: the module stays loaded until a restart"
}

case "$1" in
run)
    # detached: parking adb ends adb's shell (q2-iap-probe.sh start's trap, for the same hangup)
    trap '' HUP
    setsid sh "$0" _run "${2:-40}" </dev/null >/dev/null 2>&1 &
    echo "uac1-steps: started; log $LOG; adb back in about ${2:-40} s plus a few if all goes well"
    ;;
_run) run "$2" ;;
log) cat "$LOG" ;;
*) echo "usage: $0 run [SECONDS] | log" >&2; exit 2 ;;
esac
