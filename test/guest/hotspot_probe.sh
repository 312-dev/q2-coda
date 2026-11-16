#!/bin/sh
# Guest Play's hotspot, a feasibility probe for a real Q2 (docs/internals.md#guest-play-hotspot). Run as root on the
# Q2 (adb push, then `adb shell sh /tmp/hotspot_probe.sh`; or through q2remote) with Wi-Fi on and connected. It changes
# nothing that survives a reboot: everything is under /tmp, and it stops what it started at the end.
#
#   sh hotspot_probe.sh            what the driver offers, then an access point on wlan1 for 120 s for a phone to join
#   sh hotspot_probe.sh --hostapd  the same with /tmp/hostapd (pushed by hand) in place of wpa_supplicant's AP mode
#   sh hotspot_probe.sh --reload   if there is no wlan1: reload the Wi-Fi driver with a second interface first
#                                  (Wi-Fi drops for a few seconds; Bluetooth is a separate UART device)
#
# Everything it finds goes to /tmp/hotspot-probe.log; pull that file back.
LOG=/tmp/hotspot-probe.log
SSID=Q2-probe
PASS=q2probe-1234
NET=192.168.43
WAIT=120
mode=wpa
[ "$1" = --hostapd ] && mode=hostapd
: > $LOG
say() { echo "$*" | tee -a $LOG; }
run() { say "\$ $*"; "$@" >> $LOG 2>&1; say "(exit $?)"; }
mem() { say "MemAvailable: $(grep MemAvailable /proc/meminfo)"; }
rss() { [ -n "$1" ] && [ -r /proc/$1/status ] && say "$2 RSS: $(grep VmRSS /proc/$1/status)"; }

say "== the driver"
run uname -a
run cat /proc/modules
for p in /sys/module/rtl8733bs/parameters/*; do [ -r "$p" ] && say "$(basename $p)=$(cat $p 2>/dev/null)"; done
run ls /sys/class/net
run cat /proc/net/wireless
say "== wlan0"
run wpa_cli -i wlan0 status
FREQ=$(wpa_cli -i wlan0 status 2>/dev/null | sed -n 's/^freq=//p')
say "wlan0 frequency: ${FREQ:-none (not connected)}"
mem

if [ ! -d /sys/class/net/wlan1 ] && { [ "$1" = --reload ] || [ "$2" = --reload ]; }; then
    say "== reloading the driver with a second interface"
    run killall wpa_supplicant
    run rmmod rtl8733bs
    cd /module_driver 2>/dev/null || cd /
    run insmod /module_driver/rtl8733bs.ko wlan_mmc_num=1 gpio_wl_dis_n=PD01 gpio_bt_dis_n=PD20 \
        gpio_bt_wifi_power_en=-1 bt_wifi_power_valid_level=0 ifname=wlan%d if2name=wlan%d
    sleep 3
    run ls /sys/class/net
    say "(Wi-Fi: turn it off and on in Settings to reconnect wlan0)"
fi
if [ ! -d /sys/class/net/wlan1 ]; then
    say "RESULT: no wlan1. Run again with --reload, or the driver needs another parameter (see the list above)."
    exit 1
fi

say "== an access point on wlan1 ($mode), $SSID / $PASS"
run ifconfig wlan1 $NET.1 netmask 255.255.255.0 up
CH_FREQ=${FREQ:-2437}
if [ $mode = wpa ]; then
    cat > /tmp/probe-ap.conf <<EOF
ctrl_interface=/tmp/probe-ap
ap_scan=2
network={
    ssid="$SSID"
    mode=2
    frequency=$CH_FREQ
    key_mgmt=WPA-PSK
    proto=RSN
    pairwise=CCMP
    group=CCMP
    psk="$PASS"
}
EOF
    wpa_supplicant -i wlan1 -D nl80211 -c /tmp/probe-ap.conf -dd > /tmp/probe-ap.out 2>&1 &
    AP=$!
else
    [ -x /tmp/hostapd ] || { say "RESULT: --hostapd needs /tmp/hostapd"; exit 1; }
    if [ "$CH_FREQ" -gt 4000 ]; then HW=a; CH=$(( (CH_FREQ - 5000) / 5 )); else HW=g; CH=$(( (CH_FREQ - 2407) / 5 )); fi
    cat > /tmp/probe-ap.conf <<EOF
interface=wlan1
driver=nl80211
ctrl_interface=/tmp/probe-ap
ssid=$SSID
hw_mode=$HW
channel=$CH
ieee80211n=1
wpa=2
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
wpa_passphrase=$PASS
EOF
    /tmp/hostapd -dd /tmp/probe-ap.conf > /tmp/probe-ap.out 2>&1 &
    AP=$!
fi
cat > /tmp/probe-udhcpd.conf <<EOF
start $NET.20
end $NET.50
interface wlan1
lease_file /tmp/probe-udhcpd.leases
opt subnet 255.255.255.0
opt lease 3600
EOF
touch /tmp/probe-udhcpd.leases
udhcpd -f /tmp/probe-udhcpd.conf > /tmp/probe-udhcpd.out 2>&1 &
DHCP=$!
sleep 5
rss $AP "access point"
rss $DHCP udhcpd
mem
say "Join \"$SSID\" (password $PASS) from a phone now: waiting up to $WAIT s."
joined=
i=0
while [ $i -lt $WAIT ]; do
    # a phone that joined: wpa_supplicant's AP-STA-CONNECTED, or a lease (its own startup prints CONNECTED lines too)
    if [ -s /tmp/probe-udhcpd.leases ] || grep -q 'AP-STA-CONNECTED' /tmp/probe-ap.out 2>/dev/null; then joined=1; break; fi
    sleep 2; i=$((i + 2))
done
sleep 5
say "== after $i s"
run cat /proc/net/arp
run wpa_cli -p /tmp/probe-ap -i wlan1 all_sta
run wpa_cli -i wlan0 status
rss $AP "access point"
mem
say "== listeners (mDNS is 5353: AirPlay, Spotify Connect and DLNA announce over it)"
run netstat -lnu
run netstat -lnt
say "Now check on the phone: does it show the Q2 under AirPlay (shairport) or Spotify Connect while on $SSID?"
say "Waiting 60 s for that (stdin may be /dev/null under q2remote, so no prompt)."
sleep 60
say "== the access point's log (last 60 lines)"
tail -60 /tmp/probe-ap.out >> $LOG
say "RESULT: joined=${joined:-no} mode=$mode wlan0-freq=${FREQ:-none}"

[ "$KEEP" = 1 ] && { say "(KEEP=1: left running)"; exit 0; }
kill $AP $DHCP 2>/dev/null
ifconfig wlan1 down
rm -f /tmp/probe-ap.conf /tmp/probe-udhcpd.conf /tmp/probe-udhcpd.leases
rm -rf /tmp/probe-ap
say "stopped; the log is $LOG"
