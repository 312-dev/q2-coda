# q2btd

Vamp OS's Bluetooth service ([roadmap: Who owns what](vamp-os.md#who-owns-what)), its first slice: the client of the image's own `bluetoothd` and `bluealsa` ([vamp-os-base: Bluetooth](vamp-os-base.md#bluetooth)), doing what `demo` and `libbtctl.so` do on today's Vamp for a headset: the radio on and off, scanning, pairing, connecting, the last device again at the start and after a drop, the codec, the battery, where `q2playd`'s audio goes, absolute volume, and the headset's buttons. Receiver mode (a phone playing to the Q2), Guest Play, LE devices, a Bluetooth page in `q2ui` and the pairing notice are later slices ([What is missing](#what-is-missing)).

| What | Where |
| --- | --- |
| Source | `btd/q2btd.c` (one file, one thread, one `poll` loop, libdbus with no main loop library), `btd/Makefile` |
| In the image | `/usr/bin/q2btd`, the `q2btd` service (`os/package/q2btd`, `/etc/sv/q2btd`) |
| Its socket | `/run/vamp/bt.sock`, [Q2BT/1](platform/bt.md) |
| Its state | `/mnt/data/q2btd.state`: `power=`, `last=` (the last device), `codec=` (the codec chosen), a line each, written whole (a `.tmp`, then renamed) |
| Tests | `test/btd.py` (this machine, `test/btd_standin.c` standing in for both daemons), `test/os_bt.py` (the image's own daemons under QEMU) |

## What it talks to

| Side | How |
| --- | --- |
| `bluetoothd` (`org.bluez`) | Everything under `/` from `ObjectManager.GetManagedObjects`, then `InterfacesAdded`, `InterfacesRemoved` and `PropertiesChanged`: `Adapter1` (Powered, Discovering), `Device1` (Address, Alias, Class, Paired, Connected, Trusted), `Battery1` (Percentage). Calls: `Properties.Set` of Powered, Discoverable and Trusted, `StartDiscovery`, `StopDiscovery`, `RemoveDevice`, `Device1.Pair`, `Connect`, `Disconnect`, `AgentManager1.RegisterAgent` and `RequestDefaultAgent`, `Media1.RegisterPlayer` |
| `bluealsa` (`org.bluealsa`) | Its `PCM1` objects under `/org/bluealsa`, the same way: Device, Transport (`A2DP-source` is the Q2 playing to a headset), Codec, Rate, Volume. Calls: `GetCodecs`, `SelectCodec`, `Properties.Set` of Volume |
| `q2playd` | A client of [Q2PLAY/1](platform/playback.md): `watch` for the `now` lines (the volume, the song for the player), and `output ...`, `volume V`, `pause`, `resume`, `toggle`, `next`, `previous` |
| The bus itself | `NameOwnerChanged` for both names: a daemon that ends is forgotten (its devices or PCMs, so the output goes back to the DAC), and when it comes back everything is read again and the agent and the player registered again; a new `bluetoothd` starts unpowered, and is powered again if the state says so |

Every call is sent with a reply handler and the loop goes on (libdbus's pending calls; their timeouts are run by the loop), so a `Connect` that pages for seconds or a `Pair` waiting on its agent holds nothing up; a Q2BT/1 command that needs one is answered when it comes back. No call waits more than 25 s (`CALL_MS`) but `Pair`, 65 s (`PAIR_CALL_MS`, past BlueZ's own 60 s for an agent's answer).

## The radio

`power on` and `power off` set `Adapter1.Powered` and keep the choice in the state. At the start, and whenever an adapter appears, the adapter is made what the state says; a unit with no state has the radio on. `scan` runs discovery for 60 s (`SCAN_MS`) and sets `Discoverable` for as long; each device heard that is not paired is a `found` line to watchers. A Bluetooth radio left off stays off at the next boot.

## The agent

The pairing questions BlueZ asks (`org.bluez.Agent1`, registered as `DisplayYesNo` and made the default agent) would be the user's, through `q2ui`'s notices. That design (`docs/platform/notices.md`) is not served yet, so the agent answers by itself, a **just-works fallback** with a rule:

| Question | Answer |
| --- | --- |
| `RequestConfirmation` (numeric comparison), `RequestAuthorization` (just works) | yes for the device `pair` is pairing, and for any device while a `scan` has the Q2 discoverable; else `Rejected` |
| `RequestPinCode` (a device older than Secure Simple Pairing) | `0000`, under the same rule (stock's PIN) |
| `RequestPasskey` | `Rejected`: the Q2 has no keyboard (as stock) |
| `AuthorizeService` | yes for a paired device, or one the rule lets pair |
| `DisplayPasskey`, `DisplayPinCode`, `Cancel`, `Release` | answered, nothing shown |

So a phone that tries to pair with a Q2 nobody is pairing is refused, and a headset paired from the Q2 needs nothing pressed. Each answer is a `pair-request` line to watchers with the six digits, which is what a notice would show. A pairing that succeeds is made Trusted (so its profiles connect without asking) and connected to.

## Connecting, and the auto-reconnect

`connect` and `disconnect` are `Device1`'s. The device that connects last (by the Q2 or by itself) is saved as `last=`. **Auto-reconnect** pages it in rounds, at 0, 3, 8, 15, 30, 60 and 90 s after the round starts (`RECONNECT_AT`), for at most two minutes (`RECONNECT_FOR_MS`): a round starts when the service starts, when the radio comes on, and when the last device drops a link the Q2 did not end. A `disconnect` or `forget` asked over Q2BT/1 starts none, a device connecting ends the round, and only one `Connect` is out at a time. BlueZ's own reconnect for a link lost to a timeout (`main.conf`'s policy) still runs beside it. Today's Vamp pages the last device at boot, at Bluetooth on and at a cable in ([internals: Bluetooth boot reconnect](internals.md#bluetooth-boot-reconnect)); the round after a drop is new here. A unit with no state takes the last device from stock's `[SYSSET] BTLINKMAC` in `/mnt/data/config.ini` when it holds an address, so the headset paired under stock is paged at the first boot; the file is never written.

## Where the music goes

While a connected device has an A2DP PCM with the Q2 as its source, `q2btd` asks `q2playd` for `output bluetooth` (`output bluetooth soft` for a device that is not headphones or a headset); when the PCM goes, `output dac`, and a `pause` before it if a song plays, so a headset that drops does not move the music to the jack still playing. `q2playd` tells `q2player` the way and starts the song again where it is heard ([q2playd: The output](playd.md#the-output)). `q2player` plays to bluealsa's PCM for the device connected last (`bluealsa:DEV=00:00:00:00:00:00`, [player: Output](player.md#output)), so no `asound.conf` is written.

**Absolute volume.** For headphones and headsets (Class of Device major 4, minor 1, 2 or 6: today's Link Volume default, [internals: Headset controls and volume](internals.md#headset-controls-and-volume)), the player plays at unity and the level is the headset's: `q2playd`'s volume is written to the PCM's `Volume` on the Even curve, 127 v / 100 rounded and at least 1 above 0, unless the headset's level already reads as it; a level the headset sets (its own keys) comes back as `PropertiesChanged` and is sent to `q2playd` as `volume V`, 100 L / 127 rounded, so the Q2's volume follows. The Q2's own writes, which come back the same way, are told apart by the level last written. A speaker, a car, or a device whose Class is not known keeps its own volume, never written, and the player's gain sets the level. `q2playd` keeps Bluetooth's level apart from the DAC's.

**Codecs.** `codec` names the PCM's codec, its rate, the codecs the headset offers (`GetCodecs`) and the choice. `auto` leaves it to bluealsa, which takes the best both ends have; a codec chosen is asked for (`SelectCodec`) at once and at each connection of a headset that offers it, and kept in the state. Today's Quality page also chooses LDAC's bitrate and the SBC bitpool ([internals: Bluetooth LDAC quality](internals.md#bluetooth-ldac-quality)); those are not here.

## The headset's buttons, and what it shows

`q2btd` registers a player with BlueZ (`Media1.RegisterPlayer`, an `org.mpris.MediaPlayer2.Player` at `/vamp/q2btd/player`). BlueZ's AVRCP target then calls it for a headset's Play, Pause, Play/Pause, Stop, Next and Previous, which become `q2playd`'s `resume`, `pause`, `toggle`, `pause`, `next` and `previous`; and it answers a headset's or a car's GetElementAttributes and its play status from the player's properties, `PlaybackStatus` and `Metadata` (`xesam:title`, `xesam:artist`, `xesam:album`, `mpris:length`) from `q2playd`'s `now`, announced with `PropertiesChanged` at each change of song or play state. Today's Vamp reads the keys from the kernel's AVRCP input device and the track from `libbtctl`'s record ([internals: Track metadata](internals.md#track-metadata)); neither exists here.

**Battery**: `Battery1.Percentage` on the connected device, which BlueZ fills from the LE Battery Service or from bluealsa's HFP reading of `+IPHONEACCEV` and `+XAPL` ([internals: Headset battery](internals.md#headset-battery)); `main.conf` has `Experimental` on, which BlueZ needs for bluealsa's battery provider.

## Tests

`python3 test/btd.py` (pytest) builds `q2btd` and `test/btd_standin.c` for this machine and runs them on a `dbus-daemon` of its own, with a stand-in `q2playd` on the Q2PLAY/1 socket. The stand-in owns `org.bluez` and `org.bluealsa` (one connection each), holds one adapter and made-up devices (headphones `AA:BB:CC:DD:EE:01`, a speaker `AA:BB:CC:DD:EE:02`), answers the calls above in BlueZ's and bluez-alsa's shapes, calls the agent for a `Pair` and for a pairing from the device's side, and takes lines from the test for what a radio and a headset do: a link lost, a headset connecting itself, out of range, its own volume, its battery, a button. Checked: the radio powered at the start and its off kept across a restart; the last device paged at the start (also when named only by stock's `BTLINKMAC`), after a drop with a pause before `output dac`, and on until an absent headset is in range; no page after a disconnect asked for; scan, `found`, pair, trust and connect; the agent refusing a pairing nobody asked for and taking it during a scan; a speaker at the player's gain with its volume never written; the codec asked for, kept and asked for again at the next connection; the battery and its change; absolute volume both ways and no echo; the four buttons and the track display; forget; both daemons gone and back; and [Q2BT/1](platform/bt.md)'s transcripts. Each was seen to fail: 16 copies of `q2btd.c`, each with one behaviour broken (no round after a drop, a round after a disconnect asked, an agent that says yes to all, no pause, absolute volume for every device, the curve unrounded, the headset's level ignored, a button mapped wrong, no track change sent, power not saved, the codec not asked again, the battery ignored, no power after `bluetoothd` came back, `BTLINKMAC` not read, forget keeping the last device, a `caps` word missing), each fail the test that names it (`Q2BTD_SOURCE`). One of them was a real bug before it was a perturbation: a `bluetoothd` started again was never powered, as the first run of the daemons-back test showed.

`test/os_bt.py IMAGE` runs `q2btd` in the image beside the image's own `dbus`, `bluetoothd` and `bluealsa` under QEMU ([vamp-os-base: Bluetooth](vamp-os-base.md#bluetooth)), its socket reached from this machine: see there for what it shows.

## What is missing

- **The pairing notice**: the agent's questions to the user through `q2ui` (`docs/platform/notices.md` is not written); until then the rule above.
- **A Bluetooth page in `q2ui`**: Q2BT/1 is served, nothing in the image's UI uses it yet.
- Receiver mode (bluealsa's A2DP sink and `bluealsa-aplay`), Guest Play, the car's cable beside Bluetooth, LE devices and Car Remote's BLE channel.
- Today's per-device settings: Volume Limit, the Volume Curve, Link Volume chosen by hand, Resume on Connect (here a headset connecting never starts the music, which is today's default, Off), the LDAC and SBC quality rows, the link monitor's step-down.
- Ear detection and the AirPods handshake (`patch/headset.c`).
- The permission check: Q2BT/1's socket is root's 0600 like every API 1 socket ([Packages: Permissions](platform/packages.md#permissions)).

## Needs a Q2

Everything above has run against stand-ins and, under QEMU, the image's own daemons with no radio. Only a Q2 shows: the controller powered by `Powered` with this `rtk_hciattach`; discovery hearing devices; a pairing with a real headset under this agent (numeric comparison and just works) and a phone refused; `Connect` to a headset paired under stock (the store read by 5.88); A2DP audio through `output bluetooth` with each of SBC, aptX, aptX HD and LDAC, and `SelectCodec` on a live link; absolute volume on a real headset both ways, and whether a headset that takes the level as a plain gain wants the Gentle curve; the buttons and a car's track display through the registered player; the battery from a headset that reports it; the auto-reconnect's timing against a headset switched off and on, out of range and back, and a car that ends the link at its switch-off.
