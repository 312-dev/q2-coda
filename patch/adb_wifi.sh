#!/bin/sh
# q2adb-wifi: adb over Wi-Fi in a --dev build (docs/adb.md#adb-over-wi-fi). S90play starts it once; it
# keeps a second adbd, `adbd --tcp 5555`, and q2adb-mdns (patch/adb_mdns.c, the _adb._tcp advertisement)
# running while both of these hold, and stops them as soon as either does not:
#
#   - Developer access is on: config.ini has no ADBWIFI=0 (the [Q2POD] key a release build's Developer
#     access row would write; a --dev build has it on unless the line is there);
#   - the Q2 trusts at least one computer: /mnt/data/adb_keys (Always allow over USB) or the card's
#     adb_keys is not empty. So no port is open on the network until a key is trusted, and that first key
#     comes over the cable or the card, never over Wi-Fi (the TCP adbd asks no one).
#
# It looks every Q2ADB_EVERY seconds (10): a test of two files and one grep, then a sleep. Either program
# that ends is started again at the next look. The Q2ADB_* variables move every path and program, for
# test/adb.py.
port=${Q2ADB_PORT:-5555}
keys=${Q2ADB_KEYS:-/mnt/data/adb_keys}
card_keys=${Q2ADB_CARD_KEYS:-/mnt/mmc/adb_keys}
config=${Q2ADB_CONFIG:-/mnt/data/config.ini}
every=${Q2ADB_EVERY:-10}
adbd=${Q2ADB_ADBD:-/usr/bin/adbd}
mdns=${Q2ADB_MDNS:-/usr/bin/q2adb-mdns}
a= m=

wanted() {
    grep -q '^ADBWIFI=0' "$config" 2>/dev/null && return 1
    [ -s "$keys" ] || [ -s "$card_keys" ]
}

alive() { [ -n "$1" ] && kill -0 "$1" 2>/dev/null; }

stop() {
    alive "$a" && kill "$a"
    alive "$m" && kill "$m"
    [ -n "$a$m" ] && wait $a $m 2>/dev/null
    a= m=
}

trap 'stop; exit 0' TERM INT HUP
while :; do
    if wanted; then
        alive "$a" || { "$adbd" --tcp "$port" </dev/null >/dev/null 2>&1 & a=$!; }
        alive "$m" || { "$mdns" --port "$port" </dev/null >/dev/null 2>&1 & m=$!; }
    else
        stop
    fi
    sleep "$every" &
    wait $!
done
