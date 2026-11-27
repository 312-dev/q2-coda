#!/bin/sh
# Vamp's Bluetooth launcher (docs/bluez.md#the-launcher). An --own-bluez build installs this file at stock's
# /usr/bin/bluetoothd, and an --own-bluealsa build at /usr/bin/bluealsa and /usr/bin/bluealsa-aplay, the names
# demo, bluealsa2 and q2-guest start them by; stock's programs move to /usr/lib/bluez-stock and
# /usr/lib/bluealsa-stock. Each run picks one daemon:
#
#   [Q2POD] BLUEZ=1, BLUEALSA=1 in config.ini   Vamp's own (/usr/lib/vamp-bluez, /usr/lib/vamp-bluealsa), watched
#   anything else, or a fallback this boot       stock's, exec'd with the arguments it was given, nothing watching
#
# Vamp's daemon runs as the child of this script exec'd again as bt-watch, a name of its own, so that demo's
# `killall -9 bluetoothd` and `killall -9 bluealsa`, q2guard's search by name and q2-guest's pidof find the
# daemon alone. The watcher:
#
# - falls back to stock's for the rest of the boot (a marker in $D) when the daemon has not taken its bus name
#   (org.bluez, org.bluealsa, or org.bluealsa.NAME for -B NAME) within UP seconds of its start;
# - starts it again when it ends other than by SIGKILL, and falls back once it has ended CRASHES times within
#   WINDOW seconds. SIGKILL is demo's Bluetooth off and q2-guest's restart, so the watcher then ends with it;
# - writes each start, crash and fallback as a unified log line (docs/internals.md#unified-log) on its stderr,
#   which is demo's: q2guard copies it to the card.
#
# bluealsa-aplay runs the one that speaks to the bluealsa daemon last started ($D/bluealsa.which), and the ALSA
# plugins follow it too: /usr/share/alsa/alsa.conf.d/25-vamp-bluealsa.conf names the plugin libraries as links
# in $D, which each start of bluealsa points at stock's or Vamp's pair (docs/bluealsa.md#cutover-and-fallback).
# Arguments hold no spaces (every caller's are single words), so they are passed on as words.

D=/tmp/q2-bt
CONFIG=/mnt/data/config.ini
UP=20
CRASHES=3
WINDOW=120
# Vamp's own daemons' options beside the caller's: bluetoothd's plugins that stock has no like of
# (docs/bluez.md#build); bluealsa's AAC as BT_AAC_SITES sets stock's (docs/bluealsa.md#the-patches)
BLUEZ_OPTIONS=--noplugin=hfp,rap,fastpair
BLUEALSA_OPTIONS='--aac-vbr --aac-bitrate=240000'
# ... with no -p given: stock's two A2DP roles and the hands-free gateways the headset's battery level comes over;
# with no -B, the main daemon: shanling.bluealsa for demo, at stock's starting qualities
BLUEALSA_PROFILES='-p a2dp-source -p a2dp-sink -p hfp-ag -p hsp-ag'
BLUEALSA_MAIN='--shanling --sbc-quality=medium --ldac-quality=standard'

log() { # LEVEL MESSAGE: one unified log line, source bt
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) $(awk '{ printf "%d", $1 * 1000 }' /proc/uptime) $1 bt $2" >&2
}
up_s() { awk '{ printf "%d", $1 }' /proc/uptime; }
setting() { # KEY: its value in config.ini's [Q2POD], the first line of it as stock reads it
    awk -F= -v k="$1" '{ sub(/\r$/, "") } /^\[/ { s = $0 == "[Q2POD]" } s && $1 == k { print $2; exit }' \
        "$CONFIG" 2>/dev/null
}
alive() { # PID: running, not a zombie waiting to be reaped (kill -0 answers for one)
    st=$(awk '{ print $3 }' /proc/"$1"/stat 2>/dev/null)
    [ "$st" ] && [ "$st" != Z ]
}
owner() { # NAME: the pid owning that bus name, or nothing
    dbus-send --system --print-reply --dest=org.freedesktop.DBus / \
        org.freedesktop.DBus.GetConnectionUnixProcessID string:"$1" 2>/dev/null | awk '/uint32/ { print $2 }'
}

kind() { # KIND: the daemon's paths and setting
    kind=$1
    case $1 in
    bluez) key=BLUEZ own=/usr/lib/vamp-bluez/bluetoothd stock=/usr/lib/bluez-stock/bluetoothd ;;
    bluealsa) key=BLUEALSA own=/usr/lib/vamp-bluealsa/bluealsa stock=/usr/lib/bluealsa-stock/bluealsa ;;
    esac
}
plugins() { # DIR: the ALSA plugins' links in $D at DIR's pair, and bluealsa-aplay's choice
    for t in pcm ctl; do
        ln -s "$1/libasound_module_${t}_bluealsa.so" "$D/.$t.$$" && mv -f "$D/.$t.$$" "$D/libasound_module_${t}_bluealsa.so"
    done
}
choose() { # KIND ARGS...: exec stock's, or Vamp's under bt-watch
    kind "$1"
    shift
    mkdir -p $D
    v=$(setting $key)
    if [ "$v" = 1 ] && [ ! -x "$own" ]; then
        log W "$key=1, but this build has no $own: stock's"
        v=
    fi
    [ -e "$D/$kind.fallback" ] && v=
    if [ "$v" != 1 ]; then
        if [ $kind = bluealsa ]; then
            plugins /usr/lib/alsa-lib
            echo stock >$D/bluealsa.which
        fi
        exec "$stock" "$@"
    fi
    exec /usr/lib/vamp-bt/bt-watch "$kind" "$@"
}

fallback() { # WHY ARGS...: stock's daemon in this process, for the rest of the boot
    log E "$kind: $1; stock's for the rest of this boot"
    shift
    : >"$D/$kind.fallback"
    if [ $kind = bluealsa ]; then
        plugins /usr/lib/alsa-lib
        echo stock >$D/bluealsa.which
    fi
    exec "$stock" "$@"
}

watch() { # KIND ARGS...: Vamp's daemon, as the header says
    kind "$1"
    shift
    name=org.bluez extra=$BLUEZ_OPTIONS
    if [ $kind = bluealsa ]; then
        name=org.bluealsa extra=$BLUEALSA_OPTIONS profiles=$BLUEALSA_PROFILES main=$BLUEALSA_MAIN
        prev=
        for a in "$@"; do
            case $a in
            -p* | --profile*) profiles= ;;
            -B* | --dbus*) main= ;;
            esac
            case $prev in -B | --dbus) name=org.bluealsa.$a ;; esac
            case $a in -B?*) name=org.bluealsa.${a#-B} ;; --dbus=*) name=org.bluealsa.${a#--dbus=} ;; esac
            prev=$a
        done
        extra="$profiles $main $extra"
        mkdir -p /tmp/q2-bluealsa/lib # its per-device store's parent (docs/bluealsa.md#version)
        plugins /usr/lib/vamp-bluealsa
        echo own >$D/bluealsa.which
    fi
    ends=
    while :; do
        # shellcheck disable=SC2086 # extra is words
        "$own" $extra "$@" &
        pid=$!
        log I "$kind: Vamp's $own, pid $pid"
        t=0 up=
        while [ $t -lt $UP ] && alive $pid; do
            sleep 1
            t=$((t + 1))
            [ "$(owner $name)" = $pid ] && up=1 && break
        done
        if [ "$up" ]; then
            log I "$kind: $name up after ${t}s"
        elif alive $pid; then
            kill -9 $pid
            wait $pid
            fallback "$name not taken ${UP}s after its start" "$@"
        fi
        wait $pid
        status=$?
        if [ $status = 137 ]; then
            exit 0 # SIGKILL: Bluetooth off, or q2-guest's restart
        fi
        now=$(up_s) n=1 kept=$now
        for e in $ends; do
            [ $((now - e)) -lt $WINDOW ] && n=$((n + 1)) && kept="$kept $e"
        done
        ends=$kept
        [ $n -ge $CRASHES ] && fallback "Vamp's daemon ended $n times in ${WINDOW}s (status $status)" "$@"
        log W "$kind: Vamp's daemon ended (status $status), started again"
    done
}

me=${0##*/}
case $me in
bluetoothd) choose bluez "$@" ;;
bluealsa) choose bluealsa "$@" ;;
bluealsa-aplay)
    d=/usr/lib/bluealsa-stock
    [ "$(cat $D/bluealsa.which 2>/dev/null)" = own ] && d=/usr/lib/vamp-bluealsa
    exec $d/bluealsa-aplay "$@"
    ;;
bt-watch) watch "$@" ;;
*)
    echo "$me: run as bluetoothd, bluealsa or bluealsa-aplay" >&2
    exit 2
    ;;
esac
