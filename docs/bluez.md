# Vamp's own bluetoothd

A plan and a spike, not yet a shipped part: stock's `bluetoothd` (BlueZ 5.66 with Shanling's unpublished
changes, [bluetoothd](bluetoothd.md)) replaced by one built from upstream source, staged the way `q2player`
replaced `hciplayer` ([player-plan](player-plan.md)): ours beside stock's with a switch to fall back, then the
default once it matches. Phase 1 (2026-11-26) is this page, the build recipe, the build switch and an
off-device test; nothing on a Q2 changes unless a person starts the new daemon by hand.

Each statement says how it is known: **measured** (run here, under `qemu-mipsel`), **read** (from source or
the stock binaries), **inferred**, or **needs a Q2**.

## Why, and what it will not fix

- **Stock quirks and old code.** Stock is 22 releases behind and carries the AVRCP findings the scan names
  ([third-party](third-party.md#not-replaced)); its changes can only be altered as MIPS patches
  (`patch_bluetoothd`, `tools/bt_meta.c`). Our own build makes every change a source change with a test.
- **Pairing and reconnect.** Only part of this is bluetoothd's. `bt connect to ... ended: no link, its item is
gone` is written by `patch/bt_trace.c` about demo's own link thread in `libbtctl.so`
  ([internals](internals.md#bluetooth-connection-trace)): the thread stopped without a link. bluetoothd's part
  is the `policy` plugin's reconnection (`ReconnectAttempts`, `ReconnectIntervals`) and its pairing and key
  handling; libbtctl's thread, its 15 s wait and its one target at a time stay Shanling's whatever daemon runs
  ([bluetooth: The stack](bluetooth.md#the-stack)). Expect the new daemon to change the first and not the second
  (inferred; needs a Q2).
- **The phone app's BLE link.** It does not go through bluetoothd today ([below](#the-le-attribute-channel)),
  and an upstream bluetoothd would take it over unless told not to. This is the cutover's largest trap.

## What is on the Q2 now

How it starts (read: demo's strings, [boot](boot.md)): demo, at Bluetooth on, runs
`rtk_hciattach -n -s 115200 ttyS0 rtk_h5 &` (which loads `/firmware/rtlbt/rtl8733bs_fw` and
`rtl8733bs_config` and attaches the line discipline `hci_uart_h5` serves), then `bluetoothd &`, then
`bluealsa2` (a script: `bluealsa -c LDAC -c aptX-HD -c aptX -c AAC`, which Vamp's build edits), each with
`system("... &")` so none is demo's child. Off is `killall -9 bluealsa`, `bluetoothd`, `rtk_hciattach`. demo
also runs `hciconfig hci0 ...` itself (class, `sspmode`, `auth`, up, down, reset). `bluetoothd` is found on
`PATH` (`/usr/bin/bluetoothd`) and takes no arguments.

Configuration (read): `/etc/bluetooth/main.conf` is the only file, with `Name`, `Class = 0x20041C`,
`DiscoverableTimeout` and `[Policy] ReconnectAttempts=4`, `ReconnectIntervals=20,20,20,20`; Vamp's build sets
the name to `Vamp`, the timeout to 180 and `Experimental = true` (`BT_NAME`, `BT_DISCOVERABLE`,
`BT_EXPERIMENTAL` in `tools/build.py`). D-Bus policy is in `/etc/dbus-1/system.d/`: `bluetooth.conf`
(`org.bluez`), `bluez.conf` and `bluez2.conf` (`shanling.bluez`), `bluealsa.conf` and `bluealsa2.conf`.

The store (read, then measured): `/mnt/data/bluetooth`, not upstream's `/var/lib/bluetooth`
([The pairing store](#the-pairing-store)).

## What depends on it

| Who                                                                              | What it uses of bluetoothd                                                                                                                                                                                                                                           | Carried by                                                                                    |
| -------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| demo, through `libbtctl.so` (a copy of bluetoothctl's client)                    | `org.bluez`: `Adapter1` (power, discoverable, pairable, discovery, `RemoveDevice`), `Device1` (`Connect`, `Pair`, `Trusted`, properties), `AgentManager1` with an `Agent1` of its own (no capability named, so BlueZ's default, KeyboardDisplay), the object manager | upstream, unchanged in 5.88 (measured for the calls `test/bluez_own.py` makes)                |
| demo and libbtctl                                                                | the `shanling.bluez` service **inside** bluetoothd (`bluez_main`): `NotifyChanged`, `SetSynclink`, `GetVolumePath`, `PlayInfoChange`; and libbtctl's globals bluetoothd reads                                                                                        | [Shanling's changes](#shanlings-changes)                                                      |
| bluealsa (stock's 4.0.0, and the own one being built, [Coordination](#bluealsa)) | `Media1.RegisterEndpoint`, `MediaEndpoint1` (`SelectConfiguration`, `SetConfiguration`), `MediaTransport1` (`Acquire`, `TryAcquire`, `Release`, `State`, `Volume`), `ProfileManager1.RegisterProfile` and `Profile1`, `BatteryProvider1` (read: its strings)         | upstream; `Media1` and `ProfileManager1` are there (measured); a stream needs a Q2            |
| `patch/bt_vol_event.c`, `patch/q2dbus.h` (`q2guard`)                             | `PropertiesChanged` of `MediaTransport1` (AVRCP absolute volume, the transport's state)                                                                                                                                                                              | upstream; needs a Q2                                                                          |
| `patch/bt_name.c`, `bt_detail.c`, `bt_ui.c`, `bt_pair.c`, `bt_battery.c`         | `dbus-send` to `Adapter1.Alias`, `Device1.Alias`, `Name`, `Class`, `Adapter1.RemoveDevice`, `Battery1.Percentage`; `bt_ui.c` and `q2bt.c` also read the store's `info` files                                                                                         | upstream (measured: alias both ways, `RemoveDevice`, a battery read that has no answer)       |
| `patch/bt_pair.c`                                                                | libbtctl's pending `Agent1.RequestConfirmation`, and that BlueZ ends a request with `Agent1.Cancel` after 60 s                                                                                                                                                       | upstream (read: `src/agent.c` keeps `REQUEST_TIMEOUT`)                                        |
| `patch/q2_guest.c` (Guest Play)                                                  | an agent with `DisplayYesNo`, `Pairable`, `Discoverable`, `Alias`, `Device1.Disconnect` and `ConnectProfile`, `MediaPlayer1` and its `Track`, `GetManagedObjects`, `shanling.bluez NotifyChanged`                                                                    | upstream (measured: agent, the properties, the object list), and the `shanling.bluez` service |
| `patch/q2bip.c` (cover art)                                                      | its own L2CAP listener, and the AVRCP target record naming it, which Vamp patches into stock's binary                                                                                                                                                                | a source change in ours                                                                       |
| `tools/bt_meta.c` (in stock's binary)                                            | the control channel's GetElementAttributes and GetPlayStatus from libbtctl's record                                                                                                                                                                                  | a source change in ours                                                                       |
| `patch/q2bt*.c`, `tools/q2bt.py` (the link monitor)                              | nothing of bluetoothd: a raw HCI socket, the management socket read-only, debugfs; names from the store                                                                                                                                                              | untouched                                                                                     |
| `patch/coda_rc_ble.c`, and so the phone app over BLE                             | nothing of bluetoothd, which is the point: [The LE attribute channel](#the-le-attribute-channel)                                                                                                                                                                     | a setting or a rewrite                                                                        |
| the phone app (`net.q2coda.autoremote`)                                          | TCP, the iroh tunnel, and BLE through `coda-rc`; it connects with `TRANSPORT_LE` and never bonds (its `BleNoBondTest` holds that)                                                                                                                                    | untouched                                                                                     |

## Shanling's changes

Each is described in [bluetoothd](bluetoothd.md), read from the binary for interoperability. None of
Shanling's code, and no disassembly or decompiler output, is in the tree, a commit, an issue or a log, or will be: each is written anew against upstream's source from that
description, as a patch `tools/thirdparty.py` applies, with a test of the behavior.

| Change                                                                                                                | Without it                                                                                  | Carried as                                         |
| --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `bluez_main()` at start, `libbtctl.so` and its libraries linked                                                       | no `shanling.bluez`: demo's track and status notifications and SyncLink's switch go nowhere | a call in `main`, the link line                    |
| AVRCP target: play status and track from `g_play_status` and `g_track_meta`, `avrcp_hciplayer_event`, `g_hci_session` | a headset or car shows no track and no play state                                           | hooks in `profiles/audio/avrcp.c`, `src/adapter.c` |
| Receiver mode: `bluez_play_info_signal` from AVDTP START and SUSPEND and from the AVRCP controller                    | the receiver page never opens, shows no title                                               | hooks in `avdtp.c`, `avrcp.c`                      |
| Codec priority: `a2dp_find_eps` ordered by `g_high_priority_lcodec`                                                   | the Quality page does not decide the codec; upstream prefers the last one used              | a hook in `a2dp.c`                                 |
| The remote endpoint a headset configured is registered (`endpoint_setconf_ind`)                                       | to be judged against 5.88, which reworked this path                                         | check first, port only if still needed             |
| SyncLink's six reads of `g_synclink_mode`                                                                             | SyncLink (off by default, no Settings row) misbehaves beside A2DP                           | hooks, last                                        |
| The `shanling` plugin's SDP record (`0xcc0c`, RFCOMM 28)                                                              | Shanling's phone app cannot find SyncLink                                                   | a small plugin, last                               |
| No GATT database, no advertising manager                                                                              | see the next section                                                                        | a `main.conf` switch                               |
| The store at `/mnt/data/bluetooth`                                                                                    | pairings in RAM, gone at power off                                                          | done: `BLUEZ_STORAGE` in the recipe                |
| Vamp's own patches to stock (`bt_meta.c`, the AVRCP record's cover art entry)                                         | no track on the control channel, no cover art                                               | source changes, with `test/bluetoothd.py`'s cases  |

A port of all but the last three to 5.66 exists on the branch `feat/bt-browse` (`tools/bluez/shanling.patch`,
52 hunks, with a Unicorn suite, `test/bluez_port.py`; paused 2026-11-16, never merged, never run on a Q2).
Against 5.88, 43 of its hunks still apply and 9 do not, five of them in `avrcp.c` (measured: `patch
--dry-run`). That patch also holds AVRCP browsing for `coda-rc`, which is a feature of its own and stays out
of the cutover.

## The LE attribute channel

Stock's bluetoothd does not serve GATT ([bluetoothd](bluetoothd.md#what-the-adapter-leaves-out)), so
`coda-rc` listens on the LE attribute channel (L2CAP CID 4) itself and advertises through the kernel's
management socket, as SyncLink's `libSL.so` does. Upstream's `adapter_register` makes a GATT database for any
LE controller, and `btd_gatt_database_new` listens on CID 4 at the adapter's address (read:
`src/gatt-database.c`); when that listen fails the adapter is not registered at all. With both listening the
kernel hands a phone's connection to the listener bound to the adapter's own address before one bound to any
address, which is bluetoothd's, and the phone would find BlueZ's services and not `coda-rc`'s (inferred from
the kernel's fixed-channel lookup; needs a Q2). `test/bluez_own.py` shows the difference without a radio:
`GattManager1` and `LEAdvertisingManager1` are on `hci0` under ours and not under stock's (measured).

Two ways through, in this order:

1. **A `main.conf` switch in our build that leaves the database and the advertising manager out**, as stock
   does and as upstream itself does for a controller without LE (`adapter_register` skips both and every user
   checks for none). One condition, a few lines, and `coda-rc` and SyncLink work as they do today. This is
   what the cutover needs, as it is what matches stock.
2. Later, if wanted: `coda-rc` as a `GattManager1` application with an `LEAdvertisement1`, and the switch
   turned off. It gives BlueZ's own GATT client and server to anything else that wants LE, and costs a rewrite
   of `patch/coda_rc_ble.c`'s transport (about 400 of its 667 lines) and its simulator.

`ControllerMode = bredr` is not a way: it turns LE off in the controller, and the advertisement with it.

## The BLE pairing prompt

**Not explained; the cause is inferred, and one capture settles it.** What the evidence holds:

- The Q2's GATT server asks for nothing (read: `patch/coda_rc_ble.c`). Its socket keeps the kernel's lowest
  security level, its attribute table has no permission that needs encryption, and it never answers with an
  authentication or encryption error (its error codes are handle, read, write, PDU, request, offset, not
  found, length and group only).
- The phone app does not ask either (read: `BleLink.java` connects with `TRANSPORT_LE`; `BleNoBondTest` fails
  the app's build on `createBond`).
- The prompt on the Q2 is the pairing card (`patch/bt_pair.c`), which appears only for
  `Agent1.RequestConfirmation`: a numeric comparison. So a real pairing ran, with both sides able to show a
  code. The Q2's side is libbtctl's agent, registered with no capability, which BlueZ takes as
  KeyboardDisplay.
- Stock's bluetoothd has no attribute channel, so it never raises a link's security on its own (read).
- The Q2 advertises with its public address, the same address a phone knows it by for audio.

So the pairing was started from outside both GATT ends. In order of likelihood (inferred):

1. **The phone's own Bluetooth stack**, for a device it already knows over classic Bluetooth at that address
   (a phone that has played to the Q2 through Guest Play or the receiver, or was paired from the Bluetooth
   page): Android pairs or encrypts an LE link to a known dual-mode device without the app asking.
2. demo's link thread treating the newly connected `Device1` as a device to pair or connect.
3. The kernel, as a peripheral, asking for security: nothing sets a level above the lowest, so unlikely.

What settles it: `hcidump` or `btmon` on the Q2 (a `--dev` build), or Android's HCI snoop log, while the
prompt appears. The first Security Manager packet names the cause: a Pairing Request from the phone is 1, a
Security Request from the Q2 is 2 or 3, and a classic pairing (no SMP at all, an IO Capability Request) means
the phone came over BR/EDR. Note whether the phone lists the Q2 under its paired devices before the test.
No change is proposed until that is read.

What a new daemon changes here: nothing with the switch above. With BlueZ's own GATT server it would raise
security itself, but only for a device already bonded over LE (read: `device_attach_att`).

## Version

**5.88** (2026-11-05), the current release: the tarball is on kernel.org and its SHA-256 is in kernel.org's
signed `sha256sums.asc` (`SOURCES` in `tools/thirdparty.py`). Why the newest and not 5.66 rebuilt:

- the AVRCP fixes the scan asks for (`CVE-2023-27349`, `CVE-2023-44431`) and four years of A2DP, AVDTP and
  reconnection fixes (5.66 is of November 2022) are the reason to do this at all; 5.66 rebuilt would have
  Shanling's quirks put back and nothing gained but source;
- the D-Bus API the firmware uses did not change between them (measured for what the test calls; read for
  the rest), and the store's format only grew ([below](#the-pairing-store));
- it builds against the GLib 2.74 and D-Bus 1.12 the rootfs has (it asks for 2.36 and 1.10), and runs on a
  5.10 kernel's management interface (BlueZ supports older kernels by asking what commands exist; needs a Q2
  for the real one).

The cost is that the hooks are ported forward, not reused: about a fifth of the earlier patch.

## Build

`python3 tools/thirdparty.py build bluez`, like the other rebuilt components ([third-party](third-party.md)):
pinned by SHA-256, the Codescape toolchain, cached by recipe text. Three recipes:

- `glib` and `dbus`: **headers only**, of the versions the rootfs carries (2.74.4, 1.12.28), with
  `tools/bluez/glibconfig.h` (as GLib's meson writes it for this target) and link-time stubs made from
  `tools/bluez/stubs.tsv` (the symbols stock's two libraries export). Nothing of them is installed: bluetoothd
  uses the rootfs's own `libglib-2.0.so.0` and `libdbus-1.so.3`.
- `bluez`: `bluetoothd` alone. No readline (that is `bluetoothctl`, not built), no libudev, no libical, no
  ell, no json-c (mesh, OBEX, tools, client and udev are off). The plugins it loads are stock's less
  `wiimote` (gone upstream) and `shanling` (measured, from both daemons' logs): `hostname`, `autopair`,
  `policy`, `avrcp` (with A2DP), `gap`, `scanparam`, `deviceinfo`, `battery`; no HID, HoG, network, LE Audio
  or MIDI. Three more are compiled and left out at start with `--noplugin=hfp,rap,fastpair` (`OWN_OPTIONS`
  below): `hfp` (the hands-free unit's side) because 5.88 does not link with `--disable-hfp`, and it is an
  experimental profile where Vamp's `main.conf` turns experimental interfaces on; `rap` (ranging) and
  `fastpair`, new since 5.66, which have no configure switch. The launcher will pass the same list.
  Position independent with immediate binding, as stock's is.

Measured: 1,715,236 bytes stripped against stock's 1,313,952 (text 1,487,740 against 1,174,514); it needs
`libglib-2.0.so.0`, `libdbus-1.so.3`, `libdl`, `librt` and `libc`, and `check_abi` finds every import in the
stock rootfs. A `--dev --own-bluez` image is 56,164,352 bytes of the 75,497,472 the partition holds.

## Bases

The owner's direction of 2026-11-26: bluetoothd is one component of a Vamp OS in which every userspace piece
is our own or built from open source on a clean base (such as Buildroot), over Shanling's kernel and drivers.
So the build has to target today's stock rootfs and a newer base from one source tree and one set of patches,
with the differences in the recipe's settings, never in the code.

|                  | Today's stock rootfs                                                                                              | A newer base                                                                                                         |
| ---------------- | ----------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| C library        | glibc 2.28, the Codescape toolchain's own (`TOOLCHAIN`, `ARCH`)                                                   | the base's toolchain; BlueZ needs nothing newer than a C99 compiler and Linux headers with Bluetooth's               |
| GLib             | stock's 2.74.4, used as it is: headers and stubs from `tools/bluez/` because the build has no stock files to hand | the base's own package through its `pkg-config`; BlueZ 5.88 asks for 2.36 or later                                   |
| D-Bus            | stock's 1.12, the same way; stock's `dbus-daemon` and its policy files                                            | the base's libdbus, 1.10 or later, and a system bus with `org.bluez`'s policy file (upstream's `src/bluetooth.conf`) |
| Kernel           | Shanling's 5.10.21 on both: the management interface, L2CAP, RFCOMM, no ISO sockets (so no LE Audio on either)    | the same                                                                                                             |
| Store            | `/mnt/data/bluetooth` (`BLUEZ_STORAGE`), where stock's daemon keeps it                                            | the same path while the data partition is kept, so pairings survive the move of base too                             |
| Start            | demo's `bluetoothd &`, through the launcher                                                                       | the base's init, after `rtk_hciattach`; the same options                                                             |
| Shanling's hooks | needed while demo and `libbtctl.so` are Shanling's                                                                | not built once Vamp's own UI talks to `org.bluez` directly: they must be one patch that is left out whole            |

What that asks of phase 2:

- **Two patches, kept apart.** One for what any base wants (the `main.conf` switch for GATT, Vamp's AVRCP
  changes written against BlueZ's own `MediaPlayer1` where that can replace libbtctl's record), one for
  Shanling's hooks and the `libbtctl.so` link, applied only for the stock base. Nothing in the first may
  name libbtctl.
- **The recipe takes its base as settings**: the toolchain, where GLib and D-Bus come from (stubs or the base's
  `pkg-config`), the store. `build_glib`, `build_dbus` and `tools/bluez/` are the stock base's alone and go
  with it.
- **No version is assumed in code**: nothing may rely on GLib 2.74 or D-Bus 1.12 beyond BlueZ's own minimums.
- `test/bluez_own.py` runs against either base's libraries: it takes a root filesystem and a daemon.

The plan for the whole is `docs/vamp-os.md` (branch `docs/vamp-os`), not written when this page was.

## The build switch

`tools/build.py --own-bluez` (off by default; `python3 tools/ci/rebuild.py --dev -- --own-bluez`) adds
`/usr/lib/vamp-bluez/bluetoothd` and records it in `manifest.json` (`own_bluez`). Nothing starts it: demo's
`bluetoothd &` still finds stock's on `PATH`. It keeps the name `bluetoothd` so demo's `killall -9 bluetoothd`
ends it. A build without the flag is byte for byte what it was.

## Cutover and fallback

Phase 2 builds this; it is not in the tree.

1. `/usr/bin/bluetoothd` becomes a three-line launcher that `exec`s one of two binaries, each still named
   `bluetoothd`: stock's, moved to `/usr/lib/bluez-stock/`, or ours. It reads one key, `[Q2POD] BLUEZ` in
   `config.ini`, and a marker in `/tmp`. demo's start and stop lines do not change.
2. **Opt-in** (`--dev` builds, then a Settings row under Bluetooth): default stock.
3. **Default ours, stock the fallback**: by the row, and by a guard in `q2guard`, which already watches the
   controller ([internals](internals.md#bluetooth-controller-watchdog)): ours gone, or `org.bluez` not owned
   20 s after its start, twice in a boot, writes the marker, restarts Bluetooth on stock's and logs it to the
   unified log. Until then both daemons are in the image (1.7 MB).
4. **Stock's removed**, with `patch_bluetoothd`, `tools/bt_meta.c` and `test/bluetoothd.py`, once the
   [device plan](#on-a-q2) has passed and a week of ordinary use has shown no fallback.

Both daemons take the same `main.conf` (measured: ours parses stock's file with Vamp's edits; the groups 5.88
adds are optional) and the same D-Bus policy files.

## The pairing store

One store for both, `/mnt/data/bluetooth`, so there is no migration step, only the question whether each
reads what the other wrote.

- **Measured** (`test/bluez_own.py`, each daemon on a store the other wrote): a paired device with a link key
  is a paired `Device1` under both, its key is loaded into the kernel by both (`MGMT_OP_LOAD_LINK_KEYS`
  carries the address and the 16 bytes), an alias set under one is read by the other, as is the adapter's
  alias, and `RemoveDevice` under either removes the record.
- **Read**: every group and key 5.66 writes, 5.88 reads; 5.88 adds `[General] LastUsedBearer`,
  `PreferredBearer`, `CablePairing` and a `[ConnectionSubrate]` group, which 5.66 ignores and keeps (it loads a
  record before it rewrites it). LE keys (`LongTermKey`, `PeripheralLongTermKey`, `IdentityResolvingKey`) have
  the same names in both; the test has no LE device in its store.
- **Needs a Q2**: a real headset's record made under stock, connected under ours with no new pairing, and the
  reverse. Before the first try: `cp -a /mnt/data/bluetooth /mnt/data/bluetooth.before-own`.

## Memory and CPU

Not measurable without a Q2: under `qemu-mipsel` neither figure means anything. Read: the code is 27% larger,
the same libraries are mapped, and the daemon is idle but for D-Bus and management events while a stream
plays (audio never passes through it; bluealsa holds the A2DP socket). The device plan reads `VmRSS` and CPU
time for both.

## bluealsa

Another branch (`feat/bluealsa-own`) builds an own bluealsa from upstream bluez-alsa 5.0.0, at the same time.
The two meet at `org.bluez`'s media API only, which is upstream's on both sides, so neither cutover needs the
other: stock's bluealsa must work on our bluetoothd, and the own bluealsa on stock's. What crosses:

- **Codec priority** is decided in stock by bluetoothd's endpoint order, not by bluealsa. Until that hook is
  ported, any bluealsa on our daemon gets upstream's choice.
- Both branches add recipes to `tools/thirdparty.py` and the same `tools/bluez/glibconfig.h`; the bluealsa
  branch links stock's own libraries where this one links stubs. One of the two ways should be kept when they
  meet (theirs is the simpler, if the build has the stock rootfs to hand).
- Switch one at a time, a release apart, so a regression has one suspect; test all four pairings on a Q2
  first.

## What the spike proves, and what it cannot

`python3 test/bluez_own.py BUILD` (3 s, 49 checks) runs our daemon and stock's as MIPS under `qemu-mipsel`,
each on a D-Bus bus of its own. A preloaded library gives the daemon UNIX sockets for Bluetooth ones, and a
stand-in answers the kernel's management protocol for one dual-mode controller and records what it is sent.
Proven there, for both daemons: start on Vamp's `main.conf`, `org.bluez` owned, the name, class and
discoverable timeout from the file reaching the adapter and the controller, the interfaces the firmware's
callers use, the store read and written and exchanged, the payload's D-Bus calls. Each kind of check was seen
to fail first: a wrong key in the store, a store in another folder, another name in `main.conf`, the
interface comparison turned round.

It proves nothing about a kernel, a controller or a peer. The stand-in is written from BlueZ's protocol
document, not from Linux 5.10, and no L2CAP socket carries anything.

## On a Q2

For the main session, with a `--dev --own-bluez` build and `adb` or `q2remote` for a shell. To start ours by
hand, with Bluetooth on:
`killall -9 bluetoothd; /usr/lib/vamp-bluez/bluetoothd --noplugin=hfp,rap,fastpair -n -d >/tmp/own-bluez.log 2>&1 &`.

To go back: Bluetooth off and on in Settings (demo starts stock's), or reboot. This daemon has none of
Shanling's hooks yet, so expected to be missing: track and play state on a headset, the receiver page, the
Quality page's say, and **Car Remote over BLE** (the attribute channel, above).

| #   | Test                                                                                                                                                          | Pass                                                                                                                                                                                                                                   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0   | Before anything: copy the store aside; `grep VmRSS /proc/$(pidof bluetoothd)/status` and its CPU time from `/proc/PID/stat` after 10 minutes of AAC, on stock | figures noted                                                                                                                                                                                                                          |
| 1   | Start ours as above                                                                                                                                           | `org.bluez` owned (`dbus-send --system --print-reply --dest=org.freedesktop.DBus / org.freedesktop.DBus.NameHasOwner string:org.bluez`); the log has `Adapter /org/bluez/hci0 registered` and no `Failed`; `hciconfig hci0` up         |
| 2   | Does demo cope with no `shanling.bluez`? Open the Bluetooth page, play a song                                                                                 | no hang, no crash line in the unified log; note what demo logs                                                                                                                                                                         |
| 3   | Store, stock to ours: a headset paired under stock, switched on                                                                                               | it connects with no pairing prompt; the Bluetooth page lists the paired devices                                                                                                                                                        |
| 4   | A2DP with stock's bluealsa: play to it; AAC, then SBC, then LDAC from the Quality page                                                                        | audio on each; note which codec is chosen (the priority hook is missing); `tools/q2bt.py` shows a stream                                                                                                                               |
| 5   | AVRCP: play, pause, next from the headset; its volume keys                                                                                                    | each acts; `MediaTransport1.Volume` changes and the Q2's volume follows                                                                                                                                                                |
| 6   | Reconnect: headset off and on, three times; then out of range and back; then the Q2 rebooted with the headset on                                              | it comes back each time; compare the connection trace's lines with stock's for the same steps                                                                                                                                          |
| 7   | Pair a new headset under ours, from the Bluetooth page                                                                                                        | the pairing card or none as on stock; audio plays                                                                                                                                                                                      |
| 8   | Store, ours to stock: Bluetooth off and on (stock's again)                                                                                                    | the headset of step 7 connects with no new pairing; step 3's too                                                                                                                                                                       |
| 9   | Car Remote over BLE under ours, as it is                                                                                                                      | expected to fail: note how (`/tmp/coda-rc.log`: `no LE ATT channel`, or advertising and no service found from the phone). This is the measurement the switch in [The LE attribute channel](#the-le-attribute-channel) is designed from |
| 10  | The pairing prompt, on stock: `hcidump -X` (or `btmon`) running, phone app connects over BLE until the prompt appears                                         | the first SMP packet and its sender, per [The BLE pairing prompt](#the-ble-pairing-prompt)                                                                                                                                             |
| 11  | Memory and CPU of ours, as step 0                                                                                                                             | within a few hundred kB and no more CPU than stock                                                                                                                                                                                     |
| 12  | Fallback: `killall -9 bluetoothd` while playing under ours, then Bluetooth off and on                                                                         | stock's runs; the headset reconnects; the store is intact (compare with the copy)                                                                                                                                                      |

## Risks

- **The attribute channel**, above: a silent break of the phone app's BLE link if the switch is forgotten.
- **demo without `shanling.bluez`** (step 2) may block in a D-Bus call; that decides whether a plain upstream
  daemon can be tried on a Q2 at all before `bluez_main` is linked. It is the first thing to learn.
- **Kernel 5.10's management interface** against a 2026 daemon: BlueZ asks which commands exist, but nobody
  tests that pairing here any more.
- **Shanling's `hciconfig` calls** (`sspmode`, `auth`, `class`) go around bluetoothd; a newer daemon may
  notice and react differently.
- **Behaviors nobody wrote down**: the hooks are known from the binary's strings and code; a change Shanling
  made without a string or an import can still be there. The parity bar is the device plan, not the list.
- **Two branches in `tools/thirdparty.py`**, above.

## Effort

| Phase 2 step                                                                                                 | Estimate                       |
| ------------------------------------------------------------------------------------------------------------ | ------------------------------ |
| Device plan steps 0 to 3 and 9 to 10 on the plain daemon                                                     | half a day with the owner      |
| The `main.conf` switch for GATT, `bluez_main` linked, the launcher and Settings row                          | 2 days                         |
| Hooks ported to 5.88 with `test/bluez_port.py` brought over: track and status, receiver mode, codec priority | 4 to 6 days                    |
| Vamp's own patches as source (GetElementAttributes, GetPlayStatus, the cover art record)                     | 1 to 2 days                    |
| SyncLink's hooks and the `shanling` plugin                                                                   | 1 to 2 days, last              |
| Fallback guard, device plan in full, a week's use                                                            | 2 days of work across the week |
