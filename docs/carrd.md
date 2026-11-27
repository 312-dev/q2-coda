# q2carrd: Car Remote on Vamp OS

Car Remote ([remote-protocol](remote-protocol.md), [internals: Car Remote](internals.md#car-remote)) on Vamp OS: today's `coda-rc` daemon, the protocol, unchanged but for where it reads the library, run by `q2carrd`, which does what `demo`'s shim (`patch/coda_rc_shim.c`) does for it on today's Vamp. The shim's other half, Now Playing, the queue and the commands, is `q2playd`'s ([q2playd: What it talks to](playd.md#what-it-talks-to)).

| What | Where |
| --- | --- |
| Source | `carrd/q2carrd.c`, `carrd/Makefile`; `coda-rc` is `patch/coda_rc*.c` and monocypher (`tools/build.py` `CODA_RC_SOURCES`) built with this libc's headers (`coda_rc_sys.h`'s `CODA_LIBC`) |
| In the image | `/usr/bin/q2carrd`, `/usr/bin/coda-rc`, the `q2carrd` service (`os/package/q2carrd`, `/etc/sv/q2carrd`) |
| Tests | `test/carrd.py` (this machine: `q2carrd`, `coda-rc`, `q2playd` and a phone), `test/unit/test_coda_rc_lib.py` (the library), `test/os_image.py`'s boot |

## What it does

| Duty | How | Stock's or the shim's |
| --- | --- | --- |
| **The switch** | `coda-rc` runs while `[Q2POD] CARREMOTE` is 1 in `config.ini` (anything else, or none, is off), looked at once a second by its time and size, and is stopped at once when it goes off (a TERM, a KILL after 5 s); one that ends is started again at most every 30 s | `coda_rc_enabled`, `cr_start`, `cr_stop`, `CR_RESTART_MS` |
| **Told** | `coda-rc` is given `--tell-fd`: a byte down the pipe when it has written a file below, so it is read at once; else each second | `cr_tell` |
| **The pairing code** | While `/tmp/coda-rc.pair` (`CODE\nNAME\n`) stands, a Q2UI/1 card ([notices](platform/notices.md)): `ask pair-N NAME "123 456. Enter this code in the Vamp app" Cancel`. Cancel, or Return (answer 0 before Q2UI/1's own 60 s timeout), writes `/tmp/coda-rc.pair-cancel` and the code is not asked again; Q2UI/1's timeout is asked again while the file stands, so the card is up for the pairing's 120 s as stock's is; `gone` (q2ui restarted, as `q2libd` does after a scan) is asked again once it is back. The file gone, the card is withdrawn and `/tmp/coda-rc.pair-end` toasted: `NAME paired` (1), `Pairing failed` (0), nothing (-1, ended) | `cr_pairing`, `pair_show` (`pair_prompt.c`), `cr_toast` |
| **A car's cover** | The id in `/tmp/coda-rc.wake` started over Q2PKG/1 (`start ID`) when its `/mnt/mmc/.<id>/q2app` says `covers=1` | `cr_wake`, `addon_cover_wake` |
| **The radio** | Q2NET/1 `hold 60`, renewed every 30 s, while Wi-Fi is connected (`status`) and Car Remote was in use in the last 10 minutes: its start, a phone online (`/tmp/coda-rc.online` not empty) and the pairing card up are each a use | `coda_rc_wants_wifi`, `CR_NET_IDLE_MS` |
| **A card boot** | Not run: today's Vamp's own `coda-rc`, outside the image, has TCP 7720 and the LE channel; the run script waits and looks again every minute | |

## The library

`coda-rc --library vampos` (`coda_rc_lib.c` `crc_vampos`, which `q2carrd` passes) reads `q2libd`'s `/mnt/data/vamp/library.db` ([libd](libd.md)) for stock's `database.db`: its `songtable` (each song's length from its `time` column, in seconds, for today's durations file) and stock's `playlist`. `q2libd` keeps no `myfav`, so Favorites is an empty list. Neither stock's `database.db` nor today's Vamp's `/mnt/mmc/.q2pod/catalogue.db` is read, as Vamp OS leaves both alone ([libd: The two databases](libd.md#the-two-databases)); a play's ids are `library.db`'s, which `q2playd` plays.

## Not here yet

- **The servers' songs** on the phone: `q2libd`'s catalogue (`/mnt/mmc/.vamp/catalogue.db`) has `servermeta` by id, not by path, and `serverpl` and `serveritem` for today's `serverlists`, so `--library vampos` offers card songs alone.
- **Commands `q2playd` does not have yet**: SetMode (`mode`), Rate (`rate`), Car Audio's held Next (`scan`), Start Radio (`radio`) and Find My Vamp (`find`) are refused, until `q2playd` has repeat, ratings, scan, song radio and Find My Vamp ([q2playd: What is missing](playd.md#what-is-missing)).
- **The car's toasts**: the shim puts the Vamp's last toast in Now Playing for the car (`coda_rc_note`, `note=`); `q2ui`'s toasts do not reach `q2playd`'s now file.
- **Settings > Car Remote** (the switch, the paired phones, Pair a Phone's QR code in `/tmp/coda-rc.qr`, `carremote_page.c`) is `q2ui`'s to draw.
- **Across networks**: `q2-iroh` (`remote/iroh-sidecar`) is Rust and waits on the owner's decision about a Rust toolchain in the image; Car Remote works on the LAN and over Bluetooth LE without it.

## Tests

`python3 test/carrd.py` (pytest) builds `q2carrd`, `coda-rc` (`CODA_HOST`: loopback only, no mDNS) and `q2playd` for this machine and runs them with a stand-in for `q2player` and for Q2UI/1, Q2NET/1 and Q2PKG/1: a phone (`tools/coda_rc_client.py`) pairs with the code read off the card's body, the card titled with its name and a `NAME paired` toast after; it browses `library.db`'s songs with their lengths, plays one (`q2player` sent its file), and sees Now Playing, the queue and a Next and a jump follow `q2playd`. Besides: the switch starting and stopping `coda-rc`; Cancel ending a pairing (the code no longer pairs); Q2UI/1's timeout asked again and Return cancelling; the card withdrawn and `Pairing failed` toasted; a cover's app started only with `covers=1`; the radio held only while connected; SIGTERM stopping both. `Q2CARRD_SOURCE` builds another copy of `q2carrd.c`. `test/os_image.py`'s boot: nothing on 7720 by default; with `CARREMOTE=1`, a phone pairs and browses `song1.wav` with its 60 s; off again, the port closes; on a card boot nothing of the image's on 7720 with the switch on (`--perturb carrd-off`, `carrd-card`).

## Needs a Q2

`coda-rc`'s Bluetooth LE GATT server on the kernel's sockets with BlueZ 5.88's `bluetoothd` running beside it (no GATT plugin, `main.conf`), and its LE advertising; mDNS on `wlan0` for the Vamp app's Pair a Vamp list; a phone pairing off the card as `q2ui` draws it. Steps, from a card boot with Car Remote off in today's Vamp (so 7720 is free) or a flash boot:

1. Over adb: `grep -A3 Q2POD /mnt/data/config.ini`; set `CARREMOTE=1` (or the switch, once `q2ui` has it); expect `pgrep -l coda-rc` within 2 s and `/tmp/coda-rc.log`'s `start: tcp 7720 listening`.
2. On the phone, the Vamp app's Pair a Vamp: the Q2 listed (mDNS), the code card on the Q2's screen titled with the phone's name; type it: a `paired` toast; browse Songs and play one.
3. Wi-Fi off on the phone, Bluetooth on: the Q2 found over LE (`/tmp/coda-rc.ble` while advertising), the same browse.
4. `CARREMOTE=0`: `coda-rc` gone within 2 s, nothing on 7720 (`netstat -ltn`).
