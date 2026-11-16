#!/bin/sh
# q2-hotspot: Guest Play's hotspot (docs/internals.md#guest-play-hotspot), run by q2-guest.
#
#   q2-hotspot start SSID_HEX PASSPHRASE   an access point on wlan1 with WPA2 (CCMP), and udhcpd handing out addresses
#   q2-hotspot check                       started again when wlan0 has moved to another channel; prints the guests joined
#   q2-hotspot stop
#
# The RTL8733BS runs wlan0 and wlan1 on one radio, so the access point takes wlan0's channel (concurrent mode),
# or channel 6 (2437 MHz) when wlan0 is not connected. The rootfs's wpa_supplicant is built with its access
# point mode (CONFIG_AP), proven on a Q2 with an iPhone joining while wlan0 stayed connected on 5 GHz. The SSID
# comes as hex, so no name needs quoting; q2-guest allows no quote or backslash in the passphrase.
D=${Q2_HOTSPOT_DIR:-/tmp/q2-hotspot}
NET=192.168.43
IF=wlan1

freq() { wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^freq=//p'; }

stop() {
    for p in ap udhcpd; do [ -s $D/$p.pid ] && kill "$(cat $D/$p.pid)" 2>/dev/null; done
    ifconfig $IF down 2>/dev/null
    rm -f $D/ap.pid $D/udhcpd.pid $D/leases $D/freq
}

start() {
    stop
    mkdir -p $D
    f=$(freq)
    f=${f:-2437}
    cat > $D/ap.conf <<EOF
ctrl_interface=$D/ctrl
ap_scan=2
network={
    ssid=$1
    mode=2
    frequency=$f
    key_mgmt=WPA-PSK
    proto=RSN
    pairwise=CCMP
    group=CCMP
    psk="$2"
}
EOF
    chmod 600 $D/ap.conf
    cat > $D/udhcpd.conf <<EOF
start $NET.20
end $NET.50
interface $IF
lease_file $D/leases
pidfile $D/udhcpd.pid
opt subnet 255.255.255.0
opt lease 3600
EOF
    echo "$f" > $D/freq
    touch $D/leases
    ifconfig $IF $NET.1 netmask 255.255.255.0 up || return 1
    wpa_supplicant -B -i $IF -D nl80211 -c $D/ap.conf -P $D/ap.pid || return 1
    udhcpd $D/udhcpd.conf
}

case "$1" in
start) start "$2" "$3" ;;
stop) stop ;;
check)
    [ -s $D/ap.pid ] || exit 1
    f=$(freq)
    if [ -n "$f" ] && [ "$f" != "$(cat $D/freq 2>/dev/null)" ]; then
        ssid=$(sed -n 's/^ *ssid=//p' $D/ap.conf)
        pass=$(sed -n 's/^ *psk="\(.*\)"$/\1/p' $D/ap.conf)
        start "$ssid" "$pass"
    fi
    wpa_cli -p $D/ctrl -i $IF all_sta 2>/dev/null | grep -c -E '^([0-9a-f]{2}:){5}[0-9a-f]{2}$'
    ;;
*) echo "usage: q2-hotspot start SSID_HEX PASSPHRASE | check | stop" >&2; exit 2 ;;
esac
