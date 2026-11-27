# q2netd

Vamp OS's network service ([roadmap: Who owns what](vamp-os.md#who-owns-what)), its first slice: Wi-Fi with no `demo` ([vamp-os-base](vamp-os-base.md)). Time (NTP), the DLNA renderer, LocalSend and Car Remote's TCP side, and a Wi-Fi page in `q2ui` are later slices.

| What | Where |
| --- | --- |
| Source | `netd/q2netd.c`, `netd/Makefile` |
| In the image | `/usr/bin/q2netd`, the `q2netd` service (`os/package/q2netd`, `/etc/sv/q2netd`), with Buildroot's `wpa_supplicant` 2.11 (nl80211, its internal TLS) and BusyBox's `udhcpc` as its children |
| Tests | `test/netd.py` (this machine, a stand-in `wpa_supplicant`), `test/os_image.py`'s boot |

## What it does

| Duty | How | From |
| --- | --- | --- |
| **The radio on** | `ifconfig wlan0 up`, then `wpa_supplicant -Dnl80211 -iwlan0 -c/mnt/data/wpa_supplicant.conf -O/var/run/wpa_supplicant`, as stock's `wifi_connect.sh` starts it, plus `-O` for the control socket q2netd speaks to (so no `ctrl_interface` line is needed in stock's file); started again at most every 10 s if it ends (no `wlan0` makes it end at once) | read: stock's `wifi_connect.sh` |
| **The radio off** | `wpa_supplicant` and `udhcpc` stopped, `ifconfig wlan0 down`: where stock's `networkStop` ends ([boot: Rockbox](boot.md#rockbox)'s `WIFI_OFF`); the module (`8733bs`) stays loaded, as it holds Bluetooth's power line | read: [boot](boot.md#rockbox), [hardware](hardware.md) |
| **DHCP** | `udhcpc -f -i wlan0 -S` once `wpa_supplicant`'s `STATUS` says `wpa_state=COMPLETED`, stopped when it does not; its default script writes `/etc/resolv.conf` (a link to `/tmp`) | Buildroot's BusyBox |
| **Smart Wi-Fi** | The radio goes off after 5 minutes with nothing holding it ([internals: Smart Wi-Fi](internals.md#smart-wi-fi)). Holds: the first 30 minutes after the start when networks are saved (`REMOTE_BOOT_MS`'s), Q2NET/1's `wake` (5 minutes) and `hold S`, and `/tmp/q2remote.used` changed (a new file renamed into place or a newer time), which the Wi-Fi adbd writes when it lets a computer in, holding it 30 minutes (`REMOTE_HOLD_MS`'s, [adb: Smart Wi-Fi](adb.md#adb-over-wi-fi)). `smart off` keeps it on. With no saved network it starts off | the payload's rule, by its docs |
| **The saved networks** | Stock's `/mnt/data/wpa_supplicant.conf`, edited by `q2netd` itself: `add` appends a `network={ ssid="..." psk="..." }` block (or `key_mgmt=NONE` for an open network), `forget` removes the block with that SSID, the rest of the file kept byte for byte, then `RECONFIGURE`. A name or passphrase the file cannot quote (a `"`, a `\`, control bytes), an SSID over 32 bytes or a passphrase outside 8 to 63, and a name saved already, are refused | stock's file format |

Decision: `q2netd` edits the file rather than `wpa_supplicant`'s `SAVE_CONFIG`, which needs `update_config=1` in a file stock wrote and rewrites the whole file in its own order; editing keeps every line stock and today's Vamp put there ([User data](vamp-os.md#user-data)).

## On a card boot

Run from the card by today's Vamp ([card boot](vamp-os-base.md#card-boot)), Wi-Fi is the developer's way to today's adbd outside the chroot, so `q2netd --card-boot` (the run script passes it when rcS has marked the boot) never takes it down: a `wpa_supplicant` already running (today's Vamp's) is left exactly as it is, with nothing started, stopped or restarted beside it and no `ifconfig` (`status state=external`); with none, the radio comes up on the saved networks and stays up. Either way Smart Wi-Fi's idle off does not apply and `off` and `smart on` answer `err card boot`.

## Q2NET/1

On `/run/vamp/net.sock`: its protocol is Vamp API 1's network page, [Q2NET/1](platform/network.md), with its commands, events, capability words and the transcripts its conformance test replays here.

Only root and a package whose user is in the `network-hold` group get past the hello; anyone else is answered `err permission` and logged ([Packages: Users and groups](platform/packages.md#users-and-groups), `test/perm.py`). The socket is mode 0660 and that group's.

## Tests

`python3 test/netd.py` (pytest) runs `q2netd` for this machine with a stand-in `wpa_supplicant` (a Python program on its control socket: `STATUS`, `SCAN`, `SCAN_RESULTS`, `RECONFIGURE`) and logging commands for `ifconfig` and `udhcpc`: the radio on with a saved network and `udhcpc` only after `COMPLETED`, off with none; Smart Wi-Fi going off after the boot hold, `wake`, `hold`, the use mark's 30 minutes and `smart off`; `add` and `forget` against a file whose other lines must survive, with UTF-8 and spaces in a name and each refusal; a scan's names encoded; another version refused. A scan as the radio comes on, its stand-in's control socket made 1.5 s late, waits for it and answers its networks; one made 8 s late is `err scan` after 5 s (`WPA_READY_MS`). Copies without the idle off, starting `udhcpc` before association, quoting anything, ignoring the use mark, scanning before the control socket is there or waiting for it with no end each fail it (`Q2NETD_SOURCE`). Two card-boot cases (today's `wpa_supplicant` running: left alone and `off` refused; none: brought up and kept up past the idle time) are in it too, and copies that ignore the running one or accept `off` fail them. `test/os_image.py`'s boot: the image's `q2netd` answers Q2NET/1, and after the boot's adb check the Wi-Fi adbd's login holds the radio on for about 30 minutes. Its card boot runs as a Q2 would, rcS and `runsvdir` in the sandbox beside stand-ins for today's Vamp's `wpa_supplicant`, `udhcpc` and Wi-Fi adbd on 5555, with networks saved: `q2netd` reports `external`, both stand-ins still run, port 5555 still answers as the outer adbd, there is one `wpa_supplicant` and no `ifconfig` was run. Without `--card-boot` (`--perturb netd-card`) it brings `wlan0` up beside them and both checks fail; `--perturb netd-off` fails the first. Its `test_conformance_*` tests replay [network.md](platform/network.md)'s transcripts against it and check that every capability word is sent by one ([conformance](platform/README.md#conformance)).

## Needs a Q2

`wlan0` associating under this `wpa_supplicant` with the saved networks, DHCP and DNS on a real network, the radio's power when `wlan0` is down (whether `ifconfig down` alone saves what stock's off saves), and how long `wpa_supplicant` takes to associate after a `wake`.
