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
#   audio on|off|try [S]|rehearse [S]|keep
#           USB audio beside adb (docs/usb-audio.md#usb-audio): a UAC2 function, the Q2's output as the
#           computer's microphone, in the same gadget (a second gadget directory is refused). on and off
#           remember the choice ($DATA/q2adb.audio) for every setup after; each makes the gadget again,
#           which drops USB adb for the seconds adbd takes to come back. try is on, not remembered, under
#           a watchdog: unless keep (which remembers a try's audio) comes within S seconds (90), it goes back to adb
#           alone, and if USB adb is still not back 30 s after that, the Q2 restarts. rehearse is try with
#           the gadget made again as it was, no audio: the remake alone, under the same watchdog. Each
#           returns once its work has detached (adbd's end ends the shell that ran it), and every step is
#           a line in $LOG, kept across a restart.
# The wrapped storage_config.sh and uac_device_config.sh call park and unpark around stock's copies.
# One lock serializes them, and the parked flag keeps a late bind (adbd's restart loop) off stock's gadget.
CFS=${Q2ADB_CONFIGFS:-/sys/kernel/config}
UDCS=${Q2ADB_UDC:-/sys/class/udc}
FFS=${Q2ADB_FFS:-/dev/usb-ffs/adb}
RUN=${Q2ADB_RUN:-/tmp}
G=$CFS/usb_gadget/q2adb
DATA=${Q2ADB_DATA:-/mnt/data}
PARKED=$RUN/q2adb.parked
# There while the gadget has the UAC2 function: q2player's sign to play into its card (player/uac.c)
AUDIO=$RUN/q2adb.audio
LOG=${Q2ADB_LOG:-$DATA/vamp-usb-audio.log}
ADBD=${Q2ADB_ADBD:-/usr/bin/adbd}
PROC=${Q2ADB_PROC:-/proc}
REBOOT=${Q2ADB_REBOOT:-reboot}
BACK_S=${Q2ADB_BACK_S:-30}

# A line in $LOG when an audio command runs (Q2ADB_SAY), stamped with the uptime, synced to the card:
# what the last try did is there after a restart. Past 256 KB the log starts again, the old one .1.
say() {
    [ -n "$Q2ADB_SAY" ] || return 0
    [ "$(wc -c < "$LOG" 2>/dev/null || echo 0)" -lt 262144 ] || mv -f "$LOG" "$LOG.1"
    echo "$(cut -d' ' -f1 /proc/uptime 2>/dev/null) [$$] $*" >> "$LOG"
    sync
}

lock() { exec 9>"$RUN/q2adb.lock" && flock 9; }
# remembered on, or on for a try (until the next boot at most)
want_audio() { [ -f "$DATA/q2adb.audio" ] || [ -f "$RUN/q2adb.audio.try" ]; }
gadget_make() {
    configfs || return 1
    if [ ! -d "$G" ]; then
        mkdir "$G" && cd "$G" || return 1
        # Linux Foundation's FunctionFS Gadget ID, not stock's storage one: Windows keys its driver choice,
        # and whether it asks for Microsoft OS descriptors at all, on the IDs, so they must be this
        # gadget's own. bcdDevice is unusual so no earlier device's cached answer applies. With audio the
        # gadget is another device to Windows: the Multifunction Composite Gadget's ID, and the class
        # that says an interface association descriptor (f_uac2 has one) groups its interfaces, so
        # Windows' composite driver gives adb's interface to WinUSB and the audio ones to usbaudio2.
        echo 0x1d6b > idVendor
        echo 0x0200 > bcdUSB
        if want_audio; then
            echo 0x0104 > idProduct
            echo 0x0225 > bcdDevice # 0x0224 enumerated as "Source/Sink" before function_name: a new one so Windows reads the name afresh
            echo 0xef > bDeviceClass
            echo 0x02 > bDeviceSubClass
            echo 0x01 > bDeviceProtocol
        else
            echo 0x0105 > idProduct
            echo 0x0223 > bcdDevice
            echo 0x00 > bDeviceClass
        fi
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
        want_audio && audio_function
        # Microsoft OS descriptors: adbd's function adds the WINUSB compatible ID (tools/adbd.patch)
        echo 1 > os_desc/use
        echo 0x1 > os_desc/b_vendor_code
        echo MSFT100 > os_desc/qw_sign
        ln -s configs/c.1 os_desc/
    fi
    mkdir -p "$FFS"
    mountpoint -q "$FFS" || mount -t functionfs adb "$FFS"
}
# Shanling's f_uac2, built into the kernel (stock's USB DAC mode uses it the other way round): playback
# alone, the Q2 the source and the computer the sink, so the computer sees a microphone. Its rates are a
# list the computer picks from (Shanling's u_audio takes one, as upstream 6.x's does), 32-bit samples,
# one packet a microframe, no mute or volume control (the computer's own level is its own). Eight
# requests in flight: dwc2's descriptor DMA sends an empty packet in two with one and in three with two
# (docs/iphone-mode.md#empty-packets).
# Q2ADB_UAC_SRATE, _SSIZE and _REQ stand for the rates, the sample size and the requests, for a desk
# test that compares them (docs/usb-audio.md#the-desk-test); a try's environment reaches its work.
audio_function() {
    mkdir functions/uac2.q2 || return 1
    echo 3 > functions/uac2.q2/p_chmask
    echo 0 > functions/uac2.q2/c_chmask
    echo "${Q2ADB_UAC_SRATE:-44100,48000,88200,96000,176400,192000}" > functions/uac2.q2/p_srate
    echo "${Q2ADB_UAC_SSIZE:-4}" > functions/uac2.q2/p_ssize
    echo 1 > functions/uac2.q2/p_hs_bint
    echo "${Q2ADB_UAC_REQ:-8}" > functions/uac2.q2/req_number
    echo 0 > functions/uac2.q2/p_mute_present
    echo 0 > functions/uac2.q2/p_volume_present
    # the names a computer shows: Windows lists the function's (its interface association) as the
    # recording device, "Microphone (Q2 Output)"; Shanling's default is "Source/Sink"
    echo "Q2 Output" > functions/uac2.q2/function_name
    echo "Q2 Output" > functions/uac2.q2/if_ctrl_name
    ln -s functions/uac2.q2 configs/c.1/
    : > "$AUDIO"
}
# The USB adbd's pids: its command line is the program alone. adbd --tcp, adb over Wi-Fi, is
# q2adb-wifi's and holds no FunctionFS, so it is left running.
usb_adbd() {
    for c in "$PROC"/[0-9]*/cmdline; do
        [ "$( { tr '\0' ' ' < "$c"; } 2>/dev/null)" = "$ADBD " ] || continue  # a process may end mid-scan
        c=${c%/cmdline}
        echo "${c##*/}"
    done
}
# The USB adbd stopped, and waited for (3 s, then SIGKILL), so it no longer holds FunctionFS open when
# FunctionFS is unmounted (its restart loop starts it again; while parked it finds no FunctionFS and ends).
usb_adbd_stop() {
    pids=$(usb_adbd | tr '\n' ' ')
    [ -n "$pids" ] || return 0
    kill $pids 2>/dev/null
    i=0
    while [ $i -lt 30 ]; do
        left=
        for p in $pids; do kill -0 "$p" 2>/dev/null && left="$left $p"; done
        [ -z "$left" ] && { say "adbd $pids stopped"; return 0; }
        usleep 100000
        i=$((i+1))
    done
    say "adbd$left still there after 3 s: SIGKILL"
    kill -9 $left 2>/dev/null
}
# The gadget's directory gone, configfs's way: off the controller, its links, then its directories
# innermost first, adbd stopped and FunctionFS unmounted before. 1 when FunctionFS is still mounted
# (something holds it) or the directory is still there: making the gadget again then would give a new
# FunctionFS function to an old mount adbd writes its descriptors to, and the gadget would never bind.
gadget_remove() {
    usb_adbd_stop
    if mountpoint -q "$FFS" && ! umount "$FFS"; then
        say "umount $FFS failed"
        return 1
    fi
    [ -d "$G" ] || return 0
    echo "" 2>/dev/null > "$G/UDC"
    rm -f "$G/os_desc/c.1" "$G/configs/c.1/ffs.adb" "$G/configs/c.1/uac2.q2" "$AUDIO"
    [ ! -d "$G/functions/uac2.q2" ] || rmdir "$G/functions/uac2.q2"
    rmdir "$G/configs/c.1/strings/0x409" "$G/configs/c.1" "$G/functions/ffs.adb" "$G/strings/0x409" "$G"
    [ ! -d "$G" ] || { say "$G still there"; return 1; }
    say "gadget removed"
}
# The gadget made again with or without audio (as), and bound as unpark does. Parked (stock's storage or
# DAC has the controller) only the choice changes, for the unpark. A remove that fails leaves the gadget
# as it is, unmade: the watchdog of a try restarts the Q2 if adb does not come back.
audio_set() {
    (
        lock
        case "$1" in
        on) : > "$DATA/q2adb.audio" ;;
        try) : > "$RUN/q2adb.audio.try" ;;
        off) rm -f "$DATA/q2adb.audio" "$RUN/q2adb.audio.try" ;;
        esac
        [ -f "$PARKED" ] && { say "parked: only remembered"; exit 0; }
        say "audio $1: the gadget made again"
        gadget_remove || exit 1
        gadget_make
        say "made: $(cat "$G/idProduct" 2>/dev/null), functions $(ls "$G/functions" 2>/dev/null | tr '\n' ' ')$(
            [ -d "$G/functions/uac2.q2" ] && for a in p_srate p_ssize req_number function_name; do
                printf ', %s %s' $a "$(cat "$G/functions/uac2.q2/$a" 2>/dev/null)"; done)"
    ) || return 1
    bind
    say "bind returned $?"
}
# Detached from the shell that runs it: SIGHUP ignored before the fork (adb's pty sends its session one
# when the command's shell ends, and the child would die before setsid), and waited for until the child,
# in a session of its own, has said so ($RUN/q2adb-audio.TAG).
detach() {
    tag=$1
    shift
    rm -f "$RUN/q2adb-audio.$tag"
    trap '' HUP
    Q2ADB_DETACHED=$tag Q2ADB_SAY=1 setsid sh "$0" "$@" </dev/null >>"$LOG" 2>&1 &
    i=0
    while [ ! -f "$RUN/q2adb-audio.$tag" ] && [ $i -lt 50 ]; do usleep 100000; i=$((i+1)); done
    [ -f "$RUN/q2adb-audio.$tag" ] || { echo "q2adb-gadget: $* did not start" >&2; return 1; }
}
# USB adb is back: adbd has written its descriptors and the gadget is on the controller. UDC is read in
# a child: a gadget call stuck in the kernel holds every read of it.
usb_back() {
    [ -e "$FFS/ep1" ] || return 1
    (cat "$G/UDC" > "$RUN/q2adb.udc" 2>/dev/null) &
    r=$!
    i=0
    while kill -0 $r 2>/dev/null && [ $i -lt 20 ]; do usleep 100000; i=$((i+1)); done
    kill -0 $r 2>/dev/null && { say "reading UDC is stuck"; return 1; }
    [ -n "$(cat "$RUN/q2adb.udc" 2>/dev/null)" ]
}
# What each adbd and gadget call is doing, and the kernel's last lines, into the log.
snapshot() {
    for c in "$PROC"/[0-9]*/cmdline; do
        line=$( { tr '\0' ' ' < "$c"; } 2>/dev/null)
        case "$line" in *adbd*|*q2adb-gadget*|*storage_config*|*uac_device*) ;; *) continue ;; esac
        d=${c%/cmdline}
        say "pid ${d##*/} [$line] state $(cut -d' ' -f3 "$d/stat" 2>/dev/null) wchan $(cat "$d/wchan" 2>/dev/null)"
    done
    say "ep1 $([ -e "$FFS/ep1" ] && echo yes || echo no), mounts: $(grep -E 'configfs|functionfs' /proc/mounts 2>/dev/null | tr '\n' ';')"
    dmesg 2>/dev/null | tail -30 | while read -r l; do say "dmesg $l"; done
}
# The watchdog of a try: unless keep came within S seconds, adb alone again (in a child, given 30 s); and
# unless USB adb is then back within BACK_S, the Q2 restarts, which makes the gadget as remembered (a try
# never is, so the restart cannot come again). Never while parked: stock's gadget has the controller.
watch() {
    sleep "$1"
    [ -f "$RUN/q2adb.audio.keep" ] && { say "kept"; return 0; }
    say "no keep in $1 s: back to adb alone"
    snapshot
    (audio_set off) &
    w=$!
    i=0
    while kill -0 $w 2>/dev/null && [ $i -lt 300 ]; do usleep 100000; i=$((i+1)); done
    kill -0 $w 2>/dev/null && { say "audio off still running after 30 s"; snapshot; }
    i=0
    while [ $i -lt "$BACK_S" ]; do
        usb_back && { say "USB adb back"; return 0; }
        # stock's storage or USB DAC has the controller: adb is meant to be away, and a restart would take
        # the computer's drive or DAC from it
        [ -f "$PARKED" ] && { say "parked for stock's gadget: no restart"; return 0; }
        sleep 1
        i=$((i+1))
    done
    snapshot
    say "USB adb not back $BACK_S s after adb alone: RESTARTING THE Q2 (a try is not remembered: it comes up on adb alone)"
    sync
    $REBOOT
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
audio)
    if [ -n "$Q2ADB_DETACHED" ]; then # the detached child: say so, then the work
        : > "$RUN/q2adb-audio.$Q2ADB_DETACHED"
        say "audio $2 $3: pid $$, session $(cut -d' ' -f6 /proc/$$/stat 2>/dev/null)"
        case "$2" in
        watch) watch "${3:-90}" ;;
        on|off|try|rehearse) audio_set "$2" ;;
        esac
        exit
    fi
    case "$2" in
    keep)
        # the watchdog stands down; a try's audio is remembered from now on, a rehearsal changes nothing
        : > "$RUN/q2adb.audio.keep"
        [ -f "$RUN/q2adb.audio.try" ] && { : > "$DATA/q2adb.audio"; rm -f "$RUN/q2adb.audio.try"; }
        Q2ADB_SAY=1 say "keep"
        ;;
    on|off) detach work audio "$2" ;;
    try|rehearse)
        case "${3:-90}" in *[!0-9]*) echo "q2adb-gadget: audio $2 S, S in seconds" >&2; exit 2 ;; esac
        rm -f "$RUN/q2adb.audio.keep"
        detach watch audio watch "${3:-90}" && detach work audio "$2" &&
            echo "audio $2: USB adb drops for a few seconds; within ${3:-90} s run: q2adb-gadget audio keep"
        ;;
    *) echo "usage: q2adb-gadget audio on|off|try [S]|rehearse [S]|keep" >&2; exit 2 ;;
    esac
    ;;
*)
    echo "usage: q2adb-gadget setup|bind|park|unpark|audio on|off|try [S]|rehearse [S]|keep" >&2
    exit 2
    ;;
esac
