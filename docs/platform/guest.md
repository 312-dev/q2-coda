# Q2GUEST/1: Guest Play

`q2-guest` (`patch/q2_guest.c`, [internals: Guest Play](../internals.md#guest-play)) decides who may play to the Q2 from their phone: it asks the owner, keeps the Trusted Guests, opens Let a Guest Connect, runs the relay to the car, headset or jack, runs the hotspot, and passes the car's keys to the guest's phone. In today's Vamp the payload talks to it through a FIFO of commands (`/tmp/q2-guest.cmd`) and a state file it polls (`/tmp/q2-guest.state`). Started with `--socket /run/vamp/guest.sock`, as VampOS would start it, it serves the same commands and the same fields on that socket, so `q2ui`'s Guest Play pages need no file of the daemon's; the FIFO and the state file stay beside it, unchanged.

**Status: in the image, never run on the Q2.** `os/package/q2-guest` builds the daemon with `CODA_LIBC` and installs it with `q2-hotspot`; `etc/sv/q2-guest/run` starts it with `--socket /run/vamp/guest.sock` once the system bus is up, and it does nothing while its own switch is off. The image's one bluealsa (`etc/sv/bluealsa/run`) already has the sink role, so this build starts no second daemon and never restarts the main one (`Q2_GUEST_DEFINES`: `QG_SINKD`, `QG_MAIN_SOURCE`, `QG_MAIN_STOCK`); a guest's stream on the main daemon is relayed without `-B` (`test/q2_guest.py`'s `test_vampos`). The car's track notice goes nowhere on VampOS: it is a call to stock bluetoothd's `shanling.bluez`, which the image's BlueZ does not have ([bluez](../bluez.md)). There is no Guest Play page in `q2ui` yet ([vamp-os-services: Guest Play](../vamp-os-services.md#guest-play)).

## The socket

`/run/vamp/guest.sock`, in [the common shape](README.md#the-common-shape): the client's first line `Q2GUEST/1`, answered `Q2GUEST/1 ok`; percent-encoded free text both ways (a name, a hotspot password). Mode 0600: only `q2ui`, which runs as root, speaks it; no package permission reaches it.

| Command | Answer |
| --- | --- |
| `caps` | `caps status trusted watch on off open close answer stop key name allow leave hotspot forget ask rename` |
| `status` | `status` and every field below, in that order |
| `trusted` | `guest mac=MAC ask=0\|1 last=EPOCH name=NAME` a line for each Trusted Guest (16 at most), then `ok`; `ask` 1 for one asked before playing even so, `last` when they last played (Unix seconds) |
| `watch` | `ok`; then `status ...` whenever a field changes (once a second while a guest plays or a countdown runs), `event what=W name=NAME` for each thing that happened once, and `trusted changed` when the Trusted Guests change |
| `on`, `off` | `ok`: Guest Play's switch, saved |
| `open`, `close` | `ok`: Let a Guest Connect for two minutes (`open` again starts the two minutes over), or ended |
| `answer once`, `answer always`, `answer decline` | `ok`: the question asked (`state=asking`) answered Allow Once, Always Allow (the guest made trusted) or Decline; `err answer` with none asked |
| `answer keep`, `answer replace` | `ok`: the second guest's question (`state=replace`) answered; `err answer` with none asked |
| `stop` | `ok`: the guest's session ended (`event what=stopped`) |
| `key play\|pause\|toggle\|next\|prev` | `ok`: the key sent on to the guest's phone (the car's keys go the same way); `err argument` for another word |
| `name TEXT` | `ok`: the Guest Play name (empty for the Bluetooth name), saved; `err argument` for one of 64 bytes or more or with a control byte |
| `allow ask`, `allow trusted` | `ok`: Ask Every Time, or Trusted Guests Only |
| `leave resume`, `leave stay` | `ok`: what the owner's music does when a guest leaves |
| `hotspot on`, `hotspot off` | `ok`: Guest Play's hotspot, saved (it runs only while Guest Play is on) |
| `hotspot pass TEXT` | `ok`: its password, saved; `err pass` for one not 8 to 63 printable bytes or holding a quote, backslash or backtick |
| `forget MAC` | `ok`: the Trusted Guest gone and its Bluetooth pairing removed (its session ended first when it plays); `err none` for a MAC not trusted |
| `ask MAC 0\|1` | `ok`: that Trusted Guest's Ask Before Playing; `err none` as above |
| `rename MAC TEXT` | `ok`: that Trusted Guest's name; `err none` as above |

Any other command is `err command`; a command with arguments it does not take, `err argument`.

**The fields of `status`**, the state file's ([internals: Guest Play](../internals.md#guest-play)) without its `seq` and without the one-shot ones, which are `event` lines here:

| Field | Value |
| --- | --- |
| `state` | `off`, `idle`, `open` (Let a Guest Connect), `asking` (a guest waits for the owner's answer), `replace` (a second guest while one plays), `playing`, `paused` |
| `enabled`, `name`, `allow`, `leave` | the settings: 0 or 1, the Guest Play name, `ask` or `trusted`, `resume` or `stay` |
| `window` | Let a Guest Connect's seconds left, 0 when shut |
| `guest`, `mac`, `trusted` | who asks or plays, its Bluetooth address (`KIND:ID` for a network receiver's guest), 1 when trusted |
| `code` | while asking for a first pairing, the six digits to check on the guest's phone; empty otherwise |
| `countdown` | the question's seconds left: unanswered, `asking` is declined and `replace` kept after 30 s |
| `new`, `newmac` | the second guest of `replace` |
| `in`, `out`, `outmac`, `outcodec` | the relay's legs: the guest's codec line (`CODEC RATE KBPS`), where it plays (a device's name, or `jack`), that device's address and codec line |
| `title`, `artist`, `album`, `genre`, `track`, `tracks`, `duration`, `position`, `status` | the guest's song as the phone sends it: `duration` and `position` in ms (the position in whole seconds), `status` the phone's play status (`playing`, `paused`, ...) |
| `hotspot`, `hotspot_up`, `hotspot_name`, `hotspot_pass`, `hotspot_ip`, `hotspot_clients`, `hotspot_qr` | the hotspot's switch, whether it runs, its network name and password, the Q2's address on it (192.168.43.1), how many joined, and the text of its QR code (`WIFI:T:WPA;S:...;P:...;;`, the standard Wi-Fi QR form) |
| `source`, `newsource` | empty for a Bluetooth phone, or the network receiver a guest came through |

**Events** (`event what=W name=NAME`, NAME the guest's): `left` (the guest disconnected; the owner's music resumes unless `leave=stay`), `lost` (the link dropped), `declined`, `timeout` (not answered in 30 s), `refused` (not trusted, with Trusted Guests Only), `stopped`, `gaveup` (paused on the phone for 5 minutes), `kept`, `replaced`, `failed` (a pairing that did not complete: the UI's "Couldn't Connect" page, whose Try Again is `open`). Today's toasts are worded from these ([internals: Guest Play screens](../internals.md#guest-play-screens)).

**What the UI does**, as today's payload does it (`patch/guest.c`, `patch/guest_ui.c`): the question as the confirmation card (Allow Once focused, Return declining) with the code large when there is one and the countdown's bar, closed by itself when a `status` shows the question settled; the guest's Now Playing (title, artist, "Playing from ...", the legs) with Centre `stop` and Play, Next and Previous as `key`; a guest glyph in the status row and Home's first row while one plays. **The owner's music** is the UI's too: on the edge to `playing` from a Bluetooth phone it pauses the Library through [Q2PLAY/1](playback.md) (`pause`), and on `left`, `stopped`, `lost` or `gaveup` it resumes (`resume`) when the music was playing and `leave=resume`. Q2UI/1's `ask` ([notices](notices.md)) does not yet carry a code or a 30 s countdown, so Guest Play's question is drawn by `q2ui` from these fields, not through `ask`.

**Not on this socket:** demo's `net KIND ID NAME` and `net-end KIND`, which report a Spotify, AirPlay or DLNA receiver's session to be asked about, and the `netdeny` fields that answer them. Of those receivers VampOS keeps DLNA (`q2dlnad`); when it asks before playing, these become commands for it.

## Conformance

`test/q2_guest.py` (`test_conformance`, run by `python3 test/api.py` too) starts `q2-guest --socket` afresh for each block, on a private bus beside the stand-in BlueZ with no device, from the settings `enabled=1`, the name `Test Q2`, `allow=ask`, `leave=resume`, the hotspot off with the password `tulip-river-42`, and one Trusted Guest, `Sam's iPhone` at `AA:BB:CC:DD:EE:03`.

```conformance
> Q2GUEST/1
< Q2GUEST/1 ok
> caps
< caps status trusted watch on off open close answer stop key name allow leave hotspot forget ask rename
> status
< status state=idle enabled=1 name=Test%20Q2 allow=ask leave=resume window=0 guest= mac= trusted=0 code= countdown=0 new= newmac= in= out=jack outmac= outcodec= title= artist= album= genre= track=0 tracks=0 duration=0 position=0 status= hotspot=0 hotspot_up=0 hotspot_name=Test%20Q2 hotspot_pass=tulip-river-42 hotspot_ip= hotspot_clients=0 hotspot_qr=WIFI:T:WPA;S:Test%20Q2;P:tulip-river-42;; source= newsource=
> trusted
< guest mac=AA:BB:CC:DD:EE:03 ask=0 last=1700000000 name=Sam's%20iPhone
< ok
> watch
< ok
> allow trusted
< ok
< ~ status state=idle .* allow=trusted .*
> name Den%20Q2
< ok
< ~ status state=idle enabled=1 name=Den%20Q2 .* hotspot_name=Den%20Q2 .*
> ask AA:BB:CC:DD:EE:03 1
< ok
< trusted changed
> trusted
< guest mac=AA:BB:CC:DD:EE:03 ask=1 last=1700000000 name=Sam's%20iPhone
< ok
> rename AA:BB:CC:DD:EE:03 Sam
< ok
< trusted changed
> rename AA:BB:CC:DD:EE:09 Nobody
< err none
> answer once
< err answer
> answer maybe
< err argument
> leave sideways
< err argument
> leave stay
< ok
< ~ status .* leave=stay .*
> key sideways
< err argument
> key next
< ok
> stop
< ok
> frobnicate
< err command
```

A wrong hello is refused:

```conformance
> Q2GUEST/2
< err version
```

The switch, Let a Guest Connect, a password and the hotspot, and forgetting:

```conformance
> Q2GUEST/1
< Q2GUEST/1 ok
> watch
< ok
> off
< ok
< ~ status state=off enabled=0 .*
> on
< ok
< ~ status state=idle enabled=1 .*
> open
< ok
< ~ status state=open .* window=1[12][0-9] .*
> close
<* ~ status state=open .*
< ok
< ~ status state=idle .* window=0 .*
> hotspot pass short
< err pass
> hotspot pass maple%22cloud-17
< err pass
> hotspot pass maple-cloud-17
< ok
< ~ status .* hotspot_pass=maple-cloud-17 .*
> forget AA:BB:CC:DD:EE:03
< ok
< trusted changed
> trusted
< ok
> hotspot on
< ok
< ~ status .* hotspot=1 .*
```
