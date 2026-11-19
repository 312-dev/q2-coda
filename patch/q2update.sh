#!/bin/sh
# q2update (--dev builds, /usr/bin/q2update; docs/adb.md#installing-an-update-over-adb): asks demo to run
# Software Update's own SD card path on /mnt/mmc/update.tar, as if its row were pressed, with no
# Install Update? card to answer. demo's q2update_poll (patch/sw_update.c) takes the request from
# /tmp/q2update.req within 500 ms and answers each step in /tmp/q2update.ack:
#   q2update version   this build's tag, nothing opened
#   q2update check     the dry run: stock's checks and the tar's, on screen; nothing installed
#   q2update install   the same, then stock's updater: Restarting to Install, and the Q2 restarts
# Prints demo's last answer, "NONCE WORD INSTALLED UPDATE"; exits 0 for version, ok and installing,
# 1 for a refusal (busy, low, nocard, nofile, full, bad, latest, cancelled, failed, unknown), 2 for a
# usage error or no answer. Q2UPDATE_ROOT puts /tmp elsewhere (test/q2update.py).
R=${Q2UPDATE_ROOT:-}
REQ=$R/tmp/q2update.req
ACK=$R/tmp/q2update.ack
case "$1" in
version) wait=5 ;;
check | install) wait=${Q2UPDATE_WAIT:-180} ;; # the tar's MD5s: about 55 MB read from the card
*)
    echo "usage: q2update version|check|install" >&2
    exit 2
    ;;
esac
nonce=$(($(date +%s) % 100000 * 1000 + $$ % 1000))
umask 077
rm -f "$ACK"
printf '%s %s\n' "$nonce" "$1" >"$REQ.tmp" && mv -f "$REQ.tmp" "$REQ" || exit 2
# demo looks every 500 ms; it says "taken" first, so a demo that is not running is told apart from a
# slow check within a few seconds
ticks=0 taken=0 last=
while :; do
    if [ -f "$ACK" ]; then
        set -- $(cat "$ACK" 2>/dev/null)
        if [ "$1" = "$nonce" ]; then
            last="$*"
            case "$2" in
            taken) taken=1 ;;
            version | ok | installing)
                echo "$last"
                exit 0
                ;;
            *)
                echo "$last"
                exit 1
                ;;
            esac
        fi
    fi
    ticks=$((ticks + 1))
    if [ "$taken" = 0 ] && [ "$ticks" -ge 25 ] || [ "$ticks" -ge $((wait * 5)) ]; then
        rm -f "$REQ"
        echo "${last:-$nonce none} (no answer from demo)" >&2
        exit 2
    fi
    usleep 200000 # busybox: its sleep may not take a fraction
done
