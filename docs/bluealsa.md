# Vamp's own bluealsa

A plan and a spike, not yet a shipped part: stock's `bluealsa` (Shanling's bluez-alsa 4.0.0, which the build
patches in place) replaced by one built from upstream source, staged the way `q2player` replaced `hciplayer`
([player-plan](player-plan.md)): ours beside stock's with a switch to fall back, then the default once it
matches. Phase 1 (2026-11-26) is this page, the build recipes, the build switch and an off-device test; nothing
on a Q2 changes unless a person starts the new daemon by hand. Its sibling is `docs/bluez.md` on the branch `feat/bluez-own`, the same
move for `bluetoothd` (a link once both have landed); both are parts of the larger aim of a root file system that is all ours or built from open
source, on Shanling's kernel (`docs/vamp-os.md` on the branch `docs/vamp-os`; what this component needs of each
base is in [Bases](#bases)).

Each statement says how it is known: **measured** (run here, under `qemu-mipsel`), **read** (from source, or
from the stock binaries' strings, symbols and code), **inferred**, or **needs a Q2**. Stock was read to learn
its interfaces only. No listing of it is kept in the tree, and none may be added: describe what an interface is,
never paste what a disassembler printed.

## Verdict

**Go for phase 2.** Upstream bluez-alsa 5.0.0 builds for the Q2 with the recipes here, starts, registers with
a stand-in for bluetoothd, opens a PCM and streams all five codecs at stock's bit rates, at an encoder cost
within the noise of stock's ([What the emulator shows](#what-the-emulator-shows)). What Shanling added is small
and is an interface, not an algorithm ([Shanling's changes](#shanlings-changes)). Every Vamp patch has a home in
source or goes away ([The patches](#the-patches)). What is not known needs a radio:
[On a Q2](#on-a-q2).

## What is on the Q2 now

**Start and stop** (read: demo's strings, [boot](boot.md)). demo starts Bluetooth with three `system("... &")`
lines: `rtk_hciattach`, `bluetoothd`, and `bluealsa2`, a one-line script. Vamp's build rewrites that script
(`BT_KEEPALIVE`, `BT_GUEST_SOURCE`, `BT_COVER_START` in `tools/build.py`) to start `q2-bip` and then
`bluealsa [-p a2dp-source] --keep-alive=5 --aac-afterburner -c LDAC -c aptX-HD -c aptX -c AAC`, the profile
option only while Guest Play is on. Off is `killall -9 bluealsa` (with `bluetoothd` and `rtk_hciattach`) and
`rm -rf /var/run/bluealsa`. Nothing supervises it. `q2-guest` restarts it through `bluealsa2` when Guest Play
changes its role, and starts a second daemon, `bluealsa -B sink -p a2dp-sink -c AAC`, with `bluealsa-aplay`
playing it on.

**The binary** (read): 216,728 bytes, not position independent, linked to `libfdk-aac.so.2` (2.0.2),
`libopenaptx.so.0` (0.2.1), `libldacBT.so.2` and `libldacBT_ABR.so.2` (Sony's), `libbluetooth.so.3` (BlueZ
5.66's), GLib 2.74.4 (`gio`, `gobject`, `glib`), `libdbus-1.so.3` (1.12), and Shanling's `libbtctl.so`. SBC is
compiled in. Beside it: `bluealsa-aplay` and the two ALSA plugins, `libasound_module_pcm_bluealsa.so` and
`libasound_module_ctl_bluealsa.so`, in `/usr/lib/alsa-lib`, with `20-bluealsa.conf`.

**Codecs.** A2DP source and sink: SBC, AAC (fdk-aac), aptX and aptX HD (libopenaptx), LDAC (Sony's library; the
stock one decodes as well, which the published one does not). HFP and HSP with CVSD only: the headset's battery
level comes over HFP and is handed to BlueZ ([internals](internals.md#headset-battery)).

**Who talks to it.**

| Client                                                                      | What it uses                                                                                                                                                                                                                                                                                                         |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `q2player` (`player/output.c`), `q2video`, Rockbox, games                   | the ALSA PCM `plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp'` at the rate the transport gives; `snd_pcm_pause`, drop and drain; `-EBUSY` while another client holds it, `-ENODEV` when a codec switch replaces it ([internals](internals.md#bluetooth-pause))                                                    |
| demo's payload, through `dbus-send` (`patch/bt_ui.c`, `patch/bt_battery.c`) | `org.bluealsa.Manager1.GetPCMs`; `org.bluealsa.PCM1.GetCodecs` and `SelectCodec`; the PCM's `Codec`, `Sampling`, `CodecConfiguration` as `dbus-send` prints them                                                                                                                                                     |
| `q2guard`'s link monitor (`patch/q2bt.c`)                                   | `GetPCMs` and `GetCodecs` the same way; the daemon's pid by its name (`bluealsa`), its encoder thread by its name (`ba-a2dp-...`) for CPU; two bytes at fixed addresses in its memory, written through `/proc/PID/mem`, to step SBC's bitpool and AAC's peak down ([internals](internals.md#bluetooth-link-monitor)) |
| `q2bt-codec` (`patch/q2bt_codec.c`)                                         | `PCM1.SelectCodec` over libdbus                                                                                                                                                                                                                                                                                      |
| `q2-guest` (`patch/q2_guest.c`)                                             | `GetPCMs`, the `PCMAdded` and `PCMRemoved` signals, on `org.bluealsa` and `org.bluealsa.sink`; `pidof bluealsa`; `bluealsa-aplay`                                                                                                                                                                                    |
| stock demo and `libbtctl.so`                                                | Shanling's own service, below                                                                                                                                                                                                                                                                                        |

**Volume** ([internals](internals.md#headset-controls-and-volume)). Stock's launcher passes no
`--a2dp-volume`, so bluealsa's own volume for the PCM is software and stays at full: it scales nothing. The
headset's absolute volume is read and written by demo through bluetoothd's `MediaTransport1.Volume`
(`btctl_transport_get_volume`, and the payload's own listener for its `PropertiesChanged`), never through
bluealsa. bluealsa only reports a change of it to demo in a status event (below).

## Shanling's changes

Read from the binary against upstream 4.0.0's source; what a test here ran is marked measured.

1. **A second D-Bus service, `shanling.bluealsa`,** at `/shanling/bluealsa`, the interface of the same name. It is
   not in bluealsa itself: bluealsa calls two functions of `libbtctl.so` (one to start the service at the end
   of `main`, one to send a status), and reads two integers that library exports. The service has four methods,
   `SetLDACQuality(i)`, `SetSBCQuality(i)`, `SetUSBOutput(b)` and `StopAlsaplay()`, and two signals,
   `StatusChange(i event, ay data)` and `SinkChange(i, i)`. The two quality calls store their argument for
   the encoders (LDAC's: 21, 22 and 23 are the encoder's high, standard and mobile qualities, anything else is
   adaptive); the other two do nothing in this program; `SinkChange` is not sent by it. When the bus name is
   acquired the library creates `/tmp/bt_startup`. Measured: the stock daemon owns `shanling.bluealsa` on the
   test's bus.
2. **Status events.** `StatusChange` carries 257 or 258 and 14 bytes: a code, the device's address and, for
   258, a value. Sent when BlueZ sets a configuration, the code naming the codec (1 SBC, 2 AAC, 3 aptX, 4 LDAC,
   13 aptX HD, which the payload's `bt_ui.c` follows); 101 when a configuration is refused; and 10 with the
   level when the headset's absolute volume changes. One more call on the configuration's error path was not
   read through.
3. **Profiles with no `-p`.** Upstream refuses to start without one; stock's registers A2DP source and sink for
   all five codecs (measured: ten endpoints). No `RegisterProfile` call reached the test's stand-in from it, so
   whether it also runs the hands-free profiles unasked is open; the headset battery says it does on a Q2
   (**needs a Q2**).
4. **AAC**: VBR forced on and the peak forced to 220000 whatever the options say, and fdk-aac's VBR mode capped
   at 4 ([internals](internals.md#bluetooth-aac)).
5. **SBC**: the bitpool from a table by Shanling's quality number rather than from upstream's quality option
   ([internals](internals.md#bluetooth-link-monitor), SBC bitpool).
6. **LDAC**: an encoder thread of their own, with a queue and a writer thread, that takes the quality from the
   service and reads the socket's unsent bytes for the adaptive rate. Measured: it does not pace itself as the
   other encoders do, 20 s of audio went into a local socket in 10 to 12 s, so on a Q2 the radio's queue is its
   clock. Their `libldacBT_ABR.so.2` is not the published library's interface either: its handle constructor
   takes a library path to load, where the published one takes nothing (measured: upstream 5.0.0 linked to it
   fails to open an LDAC stream, which is why ours builds the published one in).
7. Debug prints on the Bluetooth socket's reads and writes.

Not established: that this list is complete. It comes from strings, imports and the code around them, not from
a function-by-function comparison, which phase 2 should make for the encoder loops and the transport's state
changes before the cutover.

## Version

**5.0.0** (tagged 2026-08-27), not the 4.0.0 Shanling forked.

- The patches do not carry over to either: they are MIPS words at V1.36's addresses. Rewriting them as source
  costs the same against 4.0.0 or 5.0.0, and only 5.0.0 is maintained, so only there can a fix go upstream and
  stop being ours.
- Four years of fixes sit between them, among them the races in PCM start, stop and codec switching that 4.1
  fixed, the ALSA plugin's stability fixes of 4.1 and 4.2, AAC made conformant with A2DP 1.4 in 4.2, and
  playback delay reporting.
- It builds against everything the rootfs has (it asks for BlueZ 5.51, GLib 2.58, ALSA 1.0.27 and D-Bus 1.6;
  the rootfs has 5.66, 2.74.4, 1.1.6 and 1.12), and against a newer base without change ([Bases](#bases)).
- Its command line still has every option the launcher passes.

What 5.0.0 costs, all of it at the D-Bus edge and all read from the two sources:

| Change since 4.0.0                                                                                                                                                  | Who it touches                                                                     | Answer                                                                                                                                                                           |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Manager1.GetPCMs`, `PCMAdded` and `PCMRemoved` are gone (4.1): the PCMs are listed by `org.freedesktop.DBus.ObjectManager` on `/org/bluealsa`, which 4.0.0 has too | `bt_ui.c`, `bt_battery.c`, `q2bt.c`, `q2_guest.c`                                  | move the four callers to `GetManagedObjects` and `InterfacesAdded` first: that works on stock's daemon as well, so it lands before the cutover and is tested against stock       |
| the PCM's `Sampling` is `Rate`; `Volume` is an array of bytes, not a `q`; `ChannelMap`, `ClientDelay` and `Running` are new                                         | the same readers; stock's ALSA plugins and `bluealsa-aplay`, which read `Sampling` | the readers take either name; the plugins and `bluealsa-aplay` are ours too, from the same source, and each daemon is paired with its own ([Cutover](#cutover-and-fallback))     |
| endpoints are registered with `Media1.RegisterApplication`, not `RegisterEndpoint`                                                                                  | bluetoothd                                                                         | stock's 5.66 has both (read: its strings); whether Shanling's codec priority still orders endpoints registered that way **needs a Q2**                                           |
| A2DP volume is native by default (`--a2dp-volume` is gone)                                                                                                          | the volume path                                                                    | nobody on the Q2 sets the PCM's volume, so bluealsa should scale nothing and write nothing to BlueZ, as now; **needs a Q2**                                                      |
| the daemon is `bluealsad`, its policy file `org.bluealsa.conf`                                                                                                      | demo's `killall`, `q2guard`, `q2-guest`                                            | installed under the name `bluealsa`; stock's policy file already allows `org.bluealsa`                                                                                           |
| a per-device store (volume, mute, delay) under `--localstatedir`                                                                                                    | the read-only rootfs                                                               | kept on the tmpfs, `/tmp/q2-bluealsa/lib/bluealsa`; the daemon makes only the last directory (measured: it logs an error at each save otherwise), so the launcher makes the rest |

## The patches

Every way the build changes stock's bluealsa today, and where it goes. "Source" is a change to our copy of
bluez-alsa, kept as patch files beside the recipe, each small enough to offer upstream.

| Today (`tools/build.py`, `tools/bt_silence.S`)                                                                 | Why                                                                                                                                   | In our build                                                                                                                                                                                                                                                                                                           |
| -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AAC_44K1`: the 44.1 kHz bit cleared in the AAC source capability                                              | a headset that opened the stream itself chose 44.1 kHz and stock's player still sent 48 kHz ([internals](internals.md#bluetooth-aac)) | `q2player` now asks for the transport's own rate, so the first cause is gone for it, but not for Rockbox or a game that writes 48 kHz. Source, one line in the AAC capability, kept until a Q2 shows 44.1 kHz playing clean from each client                                                                           |
| `BT_AAC_SITES`: peak 240000 and VBR mode 5                                                                     | Shanling's forced 220000 and its cap at mode 4                                                                                        | gone: `--aac-vbr --aac-bitrate=240000`, and upstream's own table gives mode 5 (measured: the same 234 kbit/s on the wire as patched stock, on the test signal)                                                                                                                                                         |
| `BT_KEEPALIVE`: `--keep-alive=5 --aac-afterburner`                                                             | one AVDTP start a session, not one a song; the better quantiser                                                                       | the same two options                                                                                                                                                                                                                                                                                                   |
| `BT_GUEST_SOURCE`, `BT_COVER_START`                                                                            | Guest Play's role; `q2-bip`                                                                                                           | the launcher's own lines, unchanged                                                                                                                                                                                                                                                                                    |
| `BT_DROP`, `BT_DROP_HANDLER`, `BT_PCM_CALLERS`: a drop flushes the FIFO in the control thread                  | stock acknowledged a drop before the encoder flushed, and the flush then ate the next song's first samples                            | 5.0.0 still flushes in the encoder thread after the client is answered (read), and tells the encoder to start afresh, which stock did not. Not ported blind: `test/bluealsa.py`'s case (a late flush after new audio) is first run against 5.0.0 in the harness; if it loses audio, source, in `ba_transport_pcm_drop` |
| `BT_SILENCE_SITES` `timeout`, `fill`, `read` and the pause entry: silence for up to 2 s after real audio stops | a skip left the stream without packets for 0.4 s and the headset's buffer ran dry                                                     | 5.0.0 pads one frame at a drain and stops (read). Source, in `io_poll_and_read_pcm`, with the budget kept per PCM (stock's patch has one for every thread: its `ponytail`). The gap is measured again first: `q2player`, not `hciplayer`, now makes the skip                                                           |
| `sbc_bp`: SBC's bitpool from a byte `q2guard` writes                                                           | the link monitor steps a weak link down without a new configuration                                                                   | source, a few lines in the SBC encoder loop, with the byte somewhere `q2guard` need not find by address: a small file both map (`/tmp/q2-bt-tune`: asked, own, in use, mode, as the four bytes are now)                                                                                                                |
| `aac_info`, `aac_rate`: AAC's peak the same way                                                                | the same, for AAC                                                                                                                     | source, in the AAC encoder loop: fdk-aac's own setter between frames, the same file                                                                                                                                                                                                                                    |
| `mtu_clamp`: the write MTU cut to 675 for every codec but LDAC                                                 | one media packet a 2-DH5 ([internals](internals.md#bluetooth-link-monitor), Packet size)                                              | source, where the transport is acquired; an option (`--a2dp-mtu-max`) rather than a constant, so another base is not tied to this radio                                                                                                                                                                                |
| `q2guard` finding the daemon and its bytes (`BT_SBC_POOL`, `BT_AAC_RATE` in `patch/q2bt.h`)                    |                                                                                                                                       | the name is unchanged; the bytes become the file above, and `q2guard` reads which daemon runs from which is there                                                                                                                                                                                                      |
| `test/bluealsa.py`, `test/ldac.py`, `tools/stock_sites.tsv`'s bluealsa rows, the manifest's `bluealsa_sha256`  | they hold the binary patches in place                                                                                                 | each moves to the harness or goes when stock's daemon goes                                                                                                                                                                                                                                                             |

And Shanling's changes, which our build must answer where something relies on them:

| Shanling's                           | In our build                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `shanling.bluealsa`                  | written new in one source file, with GDBus, from the interface above: the four methods, `StatusChange` at the same three moments with the same bytes, `/tmp/bt_startup`. On only with an option (`--shanling`), so a base without demo's `libbtctl` leaves it off. Not by linking `libbtctl.so`: that would tie our program to a closed library for 200 lines of work |
| LDAC's quality from the Quality page | `SetLDACQuality` sets what `--ldac-quality` and `--ldac-abr` set, for the next stream, and the running encoder's through the library's own call where it has one. Upstream's adaptive rate uses the same published controller as stock's (thresholds 6, 4, 2 against stock's 12, 8, 4: **needs a Q2** to compare)                                                     |
| SBC's two qualities                  | `SetSBCQuality` picks the bitpool cap from stock's table ([internals](internals.md#bluetooth-link-monitor))                                                                                                                                                                                                                                                           |
| both A2DP roles with no `-p`         | the launcher names them: `-p a2dp-source`, `a2dp-sink` where stock's receiver page needs it, and `hfp-ag` and `hsp-ag` for the battery level                                                                                                                                                                                                                          |
| LDAC as a sink                       | not offered: the published library has no decoder. Guest Play's sink is AAC; stock's own receiver page then gets SBC, AAC or aptX from a phone, never LDAC. A stated difference                                                                                                                                                                                       |

## Build

`python3 tools/thirdparty.py build bluealsa`, like the other rebuilt components ([third-party](third-party.md)):
pinned by SHA-256, the Codescape toolchain, cached by recipe text. Five recipes:

- `bluealsa`: bluez-alsa 5.0.0, with Vamp's patches (`BLUEALSA_PATCHES`, the files under `tools/bluealsa/`, one so
  far, [below](#the-first-patch)), by its own autotools build: `bluealsad`, the two ALSA plugins,
  `bluealsa-aplay` and `bluealsactl`. AAC, aptX, aptX HD and LDAC on; no oFono, UPower, MIDI, mSBC, LC3 or
  ASHA (`BLUEALSA_OPTIONS`).
- `sbc` (2.1), `freeaptx` (libfreeaptx 0.2.2) and `ldac` (Android's libldac 2.0.2.3, encoder and adaptive
  rate): static libraries, linked in. The rootfs has no libsbc; libfreeaptx is libopenaptx's fork under its
  earlier licence ([Licences](#codecs-and-licences)); stock's LDAC library has another interface (above).
- `stocksdk`: what stands for the base. The headers of GLib 2.74.4 (with GIO and GObject; the generated ones made
  by GLib's own scripts from the same archive), D-Bus 1.12.28, alsa-lib 1.1.6, BlueZ 5.66 and fdk-aac 2.0.2,
  each from its own release at the rootfs's version, and a pkg-config file apiece that links **the stock
  library itself**, from an unpacked stock rootfs (`Q2_STOCKFS`, or the cache's), refused unless its SHA-256
  is V1.36's (`BLUEALSA_STOCK_LIBS`). So the binary names the sonames the rootfs has and can import only what
  they export. `gdbus-codegen` comes from the same GLib archive.

Measured: `bluealsad` is 288,004 bytes as packaged (stock's 216,728, with SBC in both and aptX and LDAC in ours
alone, which in stock are 93 KB of libraries); it needs `libdbus-1.so.3`, `libfdk-aac.so.2`, `libasound.so.2`,
`libbluetooth.so.3`, `libgio-2.0.so.0`, `libgobject-2.0.so.0`, `libglib-2.0.so.0` and libc's, all in the
rootfs, and has stock's ELF flags. The plugins are 69 KB each and `bluealsa-aplay` 65 KB. All four are about
0.5 MB of a 75 MB partition.

### The first patch

`tools/bluealsa/alsa-1.1.6-ioplug-avail.patch`. 5.0.0's PCM plugin calls `snd_pcm_ioplug_avail`, which alsa-lib
has had since 1.1.7; the Q2 has 1.1.6. It compiled (an implicit declaration) and could not be loaded: alsa-lib
opens a plugin with `RTLD_NOW` (read: its `dlmisc.c`), and with every symbol bound at load the plugin failed
with `undefined symbol: snd_pcm_ioplug_avail` (measured: the plugin preloaded into stock's `aplay` under
`qemu-mipsel` with `LD_BIND_NOW=1`). The patch gives the plugin its own, as upstream already does for
`snd_pcm_ioplug_hw_avail` before 1.1.6; with it the same load succeeds. It is for upstream. The daemon,
`bluealsa-aplay` and the control plugin load with every symbol bound as they are.

The `bluez` branch builds against the same GLib and D-Bus headers but links stubs made from a symbol list;
this one links the stock files. One of the two ways should be kept when the branches meet. Both add
`tools/bluez/glibconfig.h`, the same bytes.

The recipes and the build switch's two functions have unit tests (`test/unit/test_thirdparty.py`,
`test/unit/test_build.py`); `test/bluealsa_own.py` is run by hand (`tools/ci/gate.py` says why: it needs the
daemon built and its path).

### Bases

The recipe for bluealsa names no stock file. It takes its base from one dependency (`BLUEALSA_BASE`), which
must give: pkg-config files for `alsa`, `bluez`, `dbus-1`, `gio-unix-2.0`, `glib-2.0` and `fdk-aac`;
`gdbus-codegen` in its `usr/bin`; and, in a file `ldflags`, whatever else its libraries need at link time.
The three static codecs depend on libc alone.

| Base                                                                                   | What stands for it                                                                                   | What it must have                                                                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Today's stock rootfs (glibc 2.28, D-Bus 1.12, BlueZ 5.66, GLib 2.74.4, alsa-lib 1.1.6) | `stocksdk`, above                                                                                    | nothing new: every library is already there                                                                                                                                                                                                                    |
| The stock rootfs with our bluetoothd (BlueZ 5.88)                                      | `stocksdk` as it is                                                                                  | nothing: bluealsa uses `libbluetooth` for address helpers and HCI ioctls alone, and 5.88 keeps `libbluetooth.so.3`; the rest is `org.bluez` on the bus, upstream's on both                                                                                     |
| A new base (Buildroot or the like: another libc, D-Bus and BlueZ)                      | that base's sysroot, wrapped as a recipe of the same shape, and its own compiler in place of `Env`'s | GLib 2.58.2 or later with GIO, D-Bus 1.6, alsa-lib 1.0.27, BlueZ 5.51 (its library and its daemon), fdk-aac 0.1.1 (or AAC off). Another libc than glibc is not tried here. A D-Bus system bus with a policy for `org.bluealsa`. No udev, systemd or PulseAudio |

No source differs between them. What differs is run-time choice, on the command line: `--shanling` (the
compatibility service, only where demo is), `--a2dp-mtu-max` (this radio), the codecs, and the store's
directory, which a base with a writable `/var/lib` leaves at upstream's default.

## The build switch

`tools/build.py --own-bluealsa` (off by default, `--dev` only; `python3 tools/ci/rebuild.py --dev --
--own-bluealsa`) adds `/usr/lib/vamp-bluealsa/` with `bluealsad`, `bluealsa-aplay.own` and the two plugins as
`libasound_module_{pcm,ctl}_bluealsa.own.so`, and records them in `manifest.json` (`own_bluealsa`). Nothing
starts or loads them: `bluealsa2` still runs stock's, and ALSA still loads stock's plugins. A build without the
flag is what it was. Measured: a
`--dev --own-bluealsa` image builds, holds the four files in a folder with `usr/lib`'s mode, and its
`/usr/bin/bluealsa` and `bluealsa2` are the ones a build without the flag has. The odd names are the spike's: the build stages every added file by its base name, where
stock's patched `bluealsa` already is. Phase 2 names the daemon `bluealsa`, for demo's `killall`, `q2guard` and
`q2-guest`.

## Cutover and fallback

Phase 2 builds this; it is not in the tree. It mirrors the bluetoothd plan's cutover (`docs/bluez.md` on `feat/bluez-own`).

1. `bluealsa2`, already a script the build rewrites, chooses: stock's `/usr/bin/bluealsa` with stock's
   plugins, or `/usr/lib/vamp-bluealsa/bluealsa` with ours. It reads one key, `[Q2POD] BLUEALSA` in
   `config.ini`, and a marker in `/tmp`. Guest Play's second daemon and `bluealsa-aplay` follow the same
   choice.
2. **The plugins follow the daemon.** A plugin reads its own daemon's properties, so each pair stays whole.
   A file the build adds to `alsa.conf.d` (as it adds `30-q2car.conf`) loads, without failing when it is absent,
   a file the launcher writes on the tmpfs that points `pcm_type.bluealsa` and `ctl_type.bluealsa` at our two libraries;
   no file, stock's. Every client keeps opening `bluealsa`.
3. **Opt-in** (`--dev` builds, then a row under Bluetooth): default stock.
4. **Default ours, stock the fallback**: by the row, and by `q2guard`, which already finds the daemon each
   second: ours gone while Bluetooth is on, or `org.bluealsa` not owned 10 s after its start, twice in a boot,
   writes the marker, restarts through `bluealsa2` on stock's and says so in the unified log.
5. **Stock's removed**, with `patch_bluealsa`, `tools/bt_silence.S`, `test/bluealsa.py`, `test/ldac.py` and
   stock's `libopenaptx` and LDAC libraries, once the [device plan](#on-a-q2) has passed and a week of ordinary
   use has shown no fallback.

**With the bluetoothd cutover.** The two meet only at `org.bluez`'s media API, upstream's on both sides, so
neither waits for the other. Switch one at a time, a release apart, and test all four pairings on a Q2 first.
One thing crosses: codec priority is bluetoothd's endpoint order in stock, so under our bluetoothd any bluealsa
gets upstream's choice until that is ported; ours can then make the choice itself with `SelectCodec`, which
the link monitor's step-down already uses.

## Codecs and licences

bluez-alsa is MIT. Our changes to it are kept under MIT too, not GPL-3.0: the daemon links fdk-aac, whose
licence the FSF lists as incompatible with the GPL, and MIT is also what upstream can take back. The firmware
as a whole stays GPL-3.0; this program is a separate work beside it, as BusyBox or OpenSSL are.

| Codec         | Library                                            | Licence                                                                               | Plainly                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| ------------- | -------------------------------------------------- | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| SBC           | libsbc 2.1, static                                 | LGPL-2.1-or-later (its tools are GPL; not built)                                      | fine: the source and the build that relinks it are published. SBC is A2DP's mandatory codec, free of royalties for that use                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| AAC           | stock's `libfdk-aac.so.2` (2.0.2), dynamic         | Fraunhofer's FDK licence: source may be distributed, **no patent licence is granted** | the image already carries this library, built by Shanling, and we do not change it. AAC's patents are Via LA's pool; whether a non-commercial firmware for a device whose maker took a licence needs its own is a legal question this page cannot answer. Anyone shipping this commercially should ask. Rebuilding fdk-aac ourselves would not change the question                                                                                                                                                                                                                                         |
| aptX, aptX HD | libfreeaptx 0.2.2, static                          | LGPL-2.1-or-later                                                                     | stock's libopenaptx 0.2.1 is GPL-3.0-or-later with a notice that tries to forbid some users; libfreeaptx is the fork of 0.2.0, the last LGPL release (29 source lines differ from 0.2.1 beyond the name; that the two encode bit for bit alike is not yet checked). aptX is Qualcomm's: the codec's original patents have expired (it dates from the 1980s), but **the names and the certification are Qualcomm's**, and this code was written from FFmpeg's, itself reverse engineered. Shanling's licence for the Q2 covers their firmware, not ours. Risk: low for a hobby firmware, real for a product |
| LDAC          | Android's libldac, static (encoder, adaptive rate) | Apache-2.0, published by Sony in AOSP                                                 | the code is plainly licensed for use and redistribution with its notice. **The LDAC name and logo need Sony's certification**; the Q2 has it as Shanling built it. No decoder is published, so ours is a source only                                                                                                                                                                                                                                                                                                                                                                                       |

Each static library's licence text is staged with the build (`LICENSE`, `COPYING.freeaptx`, `LICENSE.ldac`,
`NOTICE.ldac`); phase 2 puts them in the image and in [third-party](third-party.md)'s notices, with libsbc's.

## Memory and CPU

Budgets, to be held on a Q2:

- **Memory**: no more than stock's resident size plus 1 MB, idle and streaming. Under qemu the two idle within
  0.4 MB of each other (21.8 against 21.5 MB of the emulator's own resident memory, which is mostly qemu), which
  says only that nothing large was added.
- **CPU**: the encoder thread within a tenth of stock's for each codec, by the link monitor's `er=` and `ba=`
  ([internals](internals.md#bluetooth-link-monitor)): on a Q2 stock is about 9% of a core for aptX and aptX HD
  and 6% for AAC at mode 4 (more with mode 5 and the afterburner). The afterburner's ceiling of 15% of a core
  stands ([internals](internals.md#bluetooth-aac)).
- **Start**: `org.bluealsa` owned within a second of its start (measured here: 0.4 s under qemu, stock 0.3 s).

## What the emulator shows

`test/bluealsa_own.py` runs a daemon for the Q2 under `qemu-mipsel`, with the stock rootfs as its library
path, on a private D-Bus with a stand-in for bluetoothd, and for each codec sets a 48 kHz stereo configuration,
finds the PCM, opens it as the ALSA plugin does (`PCM1.Open`), writes a test signal through the FIFO and counts
what comes out of the transport's socket. The same script runs stock's daemon, so the numbers compare.

Measured for ours: it starts; owns `org.bluealsa`; registers ten endpoints (five codecs, source and sink) by
`RegisterApplication`; makes a PCM at 48 kHz, 16 bits for SBC, AAC and aptX, 24 for aptX HD and 32 for LDAC;
opens it; acquires the transport; streams; answers `Drop`; lets the PCM go when the configuration is cleared;
and is still running after all five. Stock's does the same by `RegisterEndpoint`, and also owns
`shanling.bluealsa`.

Encoder thread, host milliseconds of CPU for each second of audio (three runs of 10 or 20 s each, the three
daemons in turn, on a laptop busy with other work: read a spread of a quarter as noise), and what reached the
socket, RTP headers included:

| Codec, 48 kHz stereo | Ours (5.0.0)                                                    | Stock as shipped by Shanling                        | Stock as Vamp patches it                  |
| -------------------- | --------------------------------------------------------------- | --------------------------------------------------- | ----------------------------------------- |
| SBC                  | 18 to 30, at 350 kbit/s (bitpool 53)                            | 17 to 19, at 241 kbit/s (its table's lower bitpool) | 52 to 63, at 242 kbit/s                   |
| AAC, afterburner on  | 154 to 174, at 234 kbit/s (VBR mode 5, peak 240000)             | 120 to 137, at 150 kbit/s (mode 4)                  | 141 to 171, at 234 kbit/s                 |
| aptX                 | 62 to 71, at 384 kbit/s                                         | 44 to 55, at 384 kbit/s                             | 61 to 89, at 384 kbit/s                   |
| aptX HD              | 46 to 75, at 585 kbit/s                                         | 57 to 79, at 585 kbit/s                             | 80 to 113, at 587 kbit/s                  |
| LDAC                 | 201 to 208 at 1010 kbit/s (high quality); 186 at 673 (standard) | 144 to 313, at 604 to 922 kbit/s, unpaced           | 211 to 579, at 557 to 846 kbit/s, unpaced |

Read with care:

- Against what ships today (the right column) ours costs the same or less for every codec at the same bit rate.
- aptX against Shanling's own build of libopenaptx is the one place ours may cost more: 62 to 71 against 44 to
  55, about a quarter, where aptX HD from the same library shows no difference, so it may be noise. Stock's is
  the 105 M instructions a second of [internals](internals.md#bluetooth-link-monitor) (9% of a core on a Q2);
  a quarter more would be 11%. **Needs a Q2**; if it holds, libfreeaptx at another optimisation level, or
  stock's library through the same header, are the two things to try. This qemu has no plugin interface, so
  instructions were not counted here.
- The patched stock daemon costs about three times the unpatched one for SBC here, and its emulator's resident
  memory grew from 21 to 52 MB over the five codecs where the other two stayed under 30. Not looked into: it
  may be how the silence fill meets this harness's writes, and it may not happen on a Q2, whose link monitor
  has not shown it.
- Stock's LDAC took 10 s of audio in 5 to 8 s: [Shanling's changes](#shanlings-changes), item 6.
- Proof the check can fail: ours linked against stock's LDAC libraries failed it (`Open: Acquire transport:
Input/output error`, exit status 1), which is how the interface difference was found; built with the
  published library it passes.

It cannot show anything a radio does: AVDTP, the real MTU, a controller's flow control (which is LDAC's clock
in stock, and what the adaptive rate reads), a headset's buffer through a skip, absolute volume, HFP and the
battery level, or Shanling's bluetoothd and its codec priority. It does not load the ALSA plugins, so
`q2player`'s own path (`plug:bluealsa`) is tested only as the daemon's half. And its CPU numbers are the
host's time in qemu's thread for the encoder: they compare two daemons under one emulator and are not the
Q2's.

## On a Q2

Not run: the Q2 is in use. With a `--dev --own-bluealsa` build installed, over adb, and a headset that takes
each codec. Before each step note the UTC time; after it pull the unified log and the `q2-bt` log.

| #   | Step                                                                                                                                                                                                                                                                                                         | Pass                                                                                                                                                                                                                              |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Bluetooth on, headset connected, playing on stock's daemon. `ps` and `cat /proc/$(pidof bluealsa)/status`; note the `q2-bt` line's `ba=` and `er=` for a minute                                                                                                                                              | the baseline: `VmRSS`, CPU, codec                                                                                                                                                                                                 |
| 2   | Stop playback. `killall -9 bluealsa`; `mkdir -p /tmp/q2-bluealsa/lib`; `/usr/lib/vamp-bluealsa/bluealsad -p a2dp-source -p hfp-ag -p hsp-ag --keep-alive=5 --aac-afterburner --aac-vbr --aac-bitrate=240000 --ldac-abr -c LDAC -c aptX-HD -c aptX -c AAC 2>/tmp/ba.log &`                                    | it stays up; `dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa org.freedesktop.DBus.ObjectManager.GetManagedObjects` lists nothing yet and fails nothing; `/tmp/ba.log` has no error from `RegisterApplication` |
| 3   | Disconnect and connect the headset from the Q2                                                                                                                                                                                                                                                               | a PCM appears in the same call, `Codec` and `Rate` set; **which codec** stock's bluetoothd chose, against the Quality page's choice (the codec priority question)                                                                 |
| 4   | `bluealsa-aplay.own` is not the test: play a WAV with stock's `aplay` through our plugin: `printf 'pcm_type.bluealsa { lib "/usr/lib/vamp-bluealsa/libasound_module_pcm_bluealsa.own.so" }\n' > /tmp/own.conf; ALSA_CONFIG_PATH=/usr/share/alsa/alsa.conf:/tmp/own.conf aplay -D bluealsa /mnt/mmc/test.wav` | audio in the headset, clean for a minute; `VmRSS` and the encoder thread's CPU (`top -H`) against step 1                                                                                                                          |
| 5   | The same with `bluealsactl codec PCM-PATH NAME` for SBC, AAC, aptX, aptX HD and LDAC in turn (as the headset allows)                                                                                                                                                                                         | each plays; LDAC at 990, 660 and adaptive (`--ldac-quality`, `--ldac-abr`) holds without break-up at arm's length, and steps down when the Q2 is carried away                                                                     |
| 6   | The headset's volume keys while step 4 plays; then `dbus-send` a `Volume` read of BlueZ's transport                                                                                                                                                                                                          | the level moves in the headset and in BlueZ; bluealsa's log shows it following and writing nothing back; the PCM's audio is not scaled twice (full-scale tone, level by ear against step 1)                                       |
| 7   | An AirPods-like headset out of its case, so it opens the stream itself                                                                                                                                                                                                                                       | the rate it chose (`Rate`); play 44.1 kHz and 48 kHz files: clean, or the 8% frame loss of [internals](internals.md#bluetooth-aac)                                                                                                |
| 8   | A headset that reports its battery over HFP                                                                                                                                                                                                                                                                  | BlueZ's `Battery1` for the device has a level                                                                                                                                                                                     |
| 9   | Pause `aplay` (Ctrl-Z) for 30 s and resume; kill it and start it again within 5 s, and after 10 s                                                                                                                                                                                                            | the keep-alive: one AVDTP start for the first, a new one for the last (the `q2-bt` log's `idle` and `active` lines)                                                                                                               |
| 10  | `killall bluealsad`; start Bluetooth again from the Q2's page                                                                                                                                                                                                                                                | stock's daemon back, playing: the fallback is a restart                                                                                                                                                                           |

Steps 1 to 5 decide the cutover's shape; 6 to 9 are what phase 2's source changes are measured against. demo
and `q2player` are not in this plan on purpose: they need the compatibility service and the callers' move to
`GetManagedObjects`, phase 2's first two steps.

## Risks

- **Codec priority** with endpoints registered by `RegisterApplication` under Shanling's bluetoothd (step 3).
  If it is lost, ours selects the codec itself after the connection, at the cost of one reconfiguration.
- **Behaviour between 4.0.0 and 5.0.0** that no test here reaches: the busy and gone PCM errors `q2player`
  handles ([internals](internals.md#bluetooth-pause)), the delay the plugin reports, the keep-alive's timing.
  `test/player/run.py` has these cases against a fake; they need the real plugin once.
- **LDAC's pacing.** Stock's thread runs off the radio's queue; upstream's off a clock. Different under a weak
  link, and only a Q2 says which is better.
- **The list of Shanling's changes is from reading, not proof** ([above](#shanlings-changes)).
- **Two daemons in one image** for a while, and two plugin pairs: the config file that picks the pair is a new
  place for the two to disagree.
- **Licences**: nothing here is worse than stock's position, and aptX is better; none of it is cleared for a
  commercial product.

## Effort

In worker-days, phase 2 to a default-on build: callers moved to the object manager 1; the compatibility
service 2; the source patches (MTU, bitpool and peak with the shared file, silence, drop, each reproduced
first) 4; the launcher, the plugin choice, the row and `q2guard`'s fallback 2; the harness made a
gate suite, with the drop and silence cases, 1; the device plan and what it turns up 3 to 5. **13 to 15**, against the 10 to 20
[bluetooth](bluetooth.md#options) guessed before this was built.

The first three steps: (1) move `bt_ui.c`, `bt_battery.c`, `q2bt.c` and `q2_guest.c` to `GetManagedObjects`
and either property name, tested against stock's daemon; (2) the `--shanling` service in our source, checked
in the harness against what stock's daemon sends for the same configurations; (3) the device plan's steps 1 to
5 on a `--own-bluealsa` build, which settle codec priority and the plugin pairing before anything else is
built on them.
