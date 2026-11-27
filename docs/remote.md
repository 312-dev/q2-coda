# q2remote: drive a development Q2 over Wi-Fi

`q2remote` gives a developer, or an AI agent working on the project, a root shell, screenshots and input on a Q2 over the local network, so a change can be tried and checked without touching the device. It ships **only in `--dev` builds** ([building.md](building.md)): `tools/build.py --dev` compiles `patch/remote.c` into `/usr/bin/q2remote` and starts it from `S90play` after dbus; release builds don't contain it, and `test/build.py` checks that.

**Retired in favour of adb.** `--dev` builds run `adbd` over the cable and over Wi-Fi, with RSA keys and discovery by name, and [`vamp`](adb.md#vamp) runs every command below over it: `vamp shot`, `vamp key`, `vamp log` and the rest are this tool's own commands with vamp as their transport (`q2remote.TRANSPORT`). Port 2302 keeps working until the [device test plan](adb.md#device-test-plan) has passed on a Q2; then S90play stops starting its listener and this page's client side goes, while the Q2-side helper (`q2remote --shot`, `--prof`) stays for vamp.

It listens on TCP port 2302 and sends the key and every command **unencrypted**. Use it only on a network you trust, such as your own phone's hotspot, and never leave a key on a card you lend or sell.

## Setup

1. Install a `--dev` update on the Q2 and join it to the same Wi-Fi network as your PC.
2. Put a key on the card: 16-126 characters from `A-Z a-z 0-9 _ -`, one line. With the Q2 connected over USB storage:

   ```sh
   openssl rand -hex 16 > /run/media/$USER/<card>/q2remote.key
   ```

3. **Eject, then unplug the cable or restart the Q2.** While the card is shared over USB storage the Q2 unmounts it, so a new key is read only once the cable is out, or at the next boot, before USB storage takes the card. q2remote keeps a copy in `/mnt/data/q2remote.key` and uses it while the card is away, so a cable in, even from boot, doesn't lock you out.
4. Find the Q2's address: `python3 tools/q2remote.py find` tries every address of each /24 your PC is on (on Linux every interface's, so a Q2 on a hotspot is found while Ethernet stays the default route; elsewhere the default route's), without sending the key, and lists those where q2remote answers, or use the hotspot's or router's client list.
5. Load the key into the environment without echoing it:

   ```sh
   export Q2_HOST=10.0.0.42
   export Q2_KEY="$(cat path/to/q2remote.key)"
   python3 tools/q2remote.py sh id        # uid=0(root) gid=0(root)
   ```

   If you keep a copy of the key on the workstation, a temporary location such as
   `$XDG_RUNTIME_DIR/q2remote.key` is a good fit: it may be cleared at logout or reboot.
   Instead of `Q2_KEY`, `Q2_KEY_FILE` can name the file, and without either the client reads
   `q2remote.key` from the repository root, which git ignores.

Each connection rereads `/mnt/mmc/q2remote.key` while the card is mounted, so replacing the file rotates the key and deleting it revokes access, the internal copy included. A change made over USB storage applies once the cable is out.

## Smart Wi-Fi

With **Smart Wi-Fi** on, the Q2 turns its radio off once nothing has needed the network for 5 minutes, and while it is off q2remote cannot be reached. In a `--dev` build q2remote counts as needing it: Wi-Fi stays on for 30 minutes after each request (`REMOTE_HOLD_MS`; any command that passes the key, `find` not included), and for the first 30 minutes after demo starts (`REMOTE_BOOT_MS`), so a Q2 just updated or restarted can be found. Past both, once Smart Wi-Fi has the radio off, no request can arrive to bring it back: wake it on the Q2 (open Streaming or the Wi-Fi page), plug in the cable, or turn Smart Wi-Fi off. Each hold's start and end is a line in the [unified log](internals.md#unified-log) (`q2remote: ...`, source `wifi`). The Wi-Fi adbd holds the radio the same way, through the same file ([adb: ADB over Wi-Fi](adb.md#adb-over-wi-fi)). How it works: [internals: Smart Wi-Fi](internals.md#smart-wi-fi).

## Commands

| Command                        | Does                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sh [--timeout S] 'COMMAND'`   | Runs `COMMAND` with busybox `sh` as root, printing stdout and stderr together, and exits with its status. After `S` seconds without output (default 30) the client gives up and names the process group still running it; Ctrl-C kills it.                                                                                                                                                                                                                                                                                                   |
| `shot [--settle] OUT.png`      | Saves the displayed framebuffer as an upright 375×320 PNG, and warns if the screen is off. `--settle` first waits, up to 5 s, until the screen stops changing.                                                                                                                                                                                                                                                                                                                                                                               |
| `state`                        | Prints demo's pid, the uptime, and from demo's memory the backlight, USB, key lock, lock screen and volume bytes.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `wake`                         | Lights the screen with Center (twice with **Wake: Double Press**) only if it is off, and checks it came on.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `key NAME [MS]`                | Presses a button for `MS` ms (default 80).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `wheel N [--step-ms MS]`       | Turns the wheel `N` ticks: positive is down/next, negative up/back. A sample every `MS` ms (default 20); 0 sends the whole turn in one write.                                                                                                                                                                                                                                                                                                                                                                                                |
| `tap X Y`                      | Taps the screen at upright screenshot coordinates (0-374, 0-319).                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `swipe X1 Y1 X2 Y2 [MS]`       | Drags from one point to the other over `MS` ms (default 300).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `push LOCAL REMOTE`            | Copies a file to the Q2 through `REMOTE.part`, synced, and checks its MD5. Refuses `/mnt/mmc` while USB storage has the card.                                                                                                                                                                                                                                                                                                                                                                                                                |
| `pull REMOTE LOCAL`            | Copies a file from the Q2.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `install UPDATE.TAR`           | Replaces the card's `update.tar`, checks it as read back from the card, and names the menu that flashes it.                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `log [LINES\|-f\|--clear]`     | Prints the last `LINES` (default 100) of demo's output, follows it, or empties it.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `events N [S]`                 | Prints raw events from `/dev/input/eventN` for `S` seconds (default 5).                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `devices`                      | Lists the input devices and their key and axis codes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `find`                         | Lists the addresses on this network where q2remote answers.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `prof S [--all] [--build DIR]` | Samples demo's main thread (`--all`: every thread, at 20 Hz) at 100 Hz for `S` seconds through `q2remote --prof`, which stops each thread for an instant with ptrace, and prints the most frequent stacks by state (running, blocked in D, sleeping). `--build` (or `Q2_BUILD`) names the installed build's directory for demo's and the payload's symbols; libraries are pulled once into `~/.cache/q2-prof`. Callers past `$ra` come from a scan of the stack and may include a stale word. `Q2_PROF_BIN` runs another copy of the helper. |
| `reboot [--wait]`              | Syncs and reboots into the saved boot target; q2remote answers again once the Q2 rejoins Wi-Fi, which can take a few minutes. `--wait` returns once it does.                                                                                                                                                                                                                                                                                                                                                                                 |

## Input

| Control        | How                                                                | Device                                                    |
| -------------- | ------------------------------------------------------------------ | --------------------------------------------------------- |
| Center         | `key centre`; hold 5 s (`key centre 5000`) for the shutdown prompt | `md-gpio-keys`, `KEY_POWER` 116                           |
| Return         | `key return`                                                       | `md-gpio-keys`, `KEY_UP` 103                              |
| Play/Pause     | `key play`                                                         | `md-gpio-keys`, `KEY_DOWN` 108                            |
| Next, Previous | `key next`, `key prev`                                             | `md-gpio-keys`, 163, 165                                  |
| Wheel          | `wheel N`                                                          | `hyn_sliderbar`: x 1-200 a turn, read through tslib       |
| Touch          | `tap X Y`, `swipe X1 Y1 X2 Y2`                                     | `hyn_ts`: the portrait panel's axes, which `tap` converts |

All of it is injected by writing input events to `/dev/input/eventN`, so the firmware sees exactly what the hardware would send and `demo` needs no change. `wheel` reads the saved **Wheel Sensitivity** from `/mnt/data/config.ini`, so one tick is one tick of the firmware at any setting ([internals.md](internals.md), the wheel). The PMIC's 10-15 s Center reset is wired in hardware and can't be injected; use `reboot`.

## Working with it

- Take a `shot --settle` after every action and look at it before the next step.
- If input seems to do nothing, the screen may be off: `shot` warns then, and `wake` lights it. While it's dark the wheel does nothing (`wheel N` changes nothing and does not wake it). Don't wake it with `key centre`: on a lit screen that clicks the selected row.
- Quick Settings opens with `swipe 187 5 187 220` and closes with a swipe up from the bottom edge, `swipe 187 310 187 0`.
- With a cable in, the Q2 shows **USB Connected** and the wheel changes the volume. `key return` ends USB storage and the Q2 mounts its card again; eject it on the PC first.
- With **Wake: Double Press** on, a dark Q2 needs two Center presses to wake.
- A tap hides the wheel's selection, and the next tick only shows it again without moving.
- Busybox has no `timeout`, `pkill` or `killall -r`. Background a command and `kill $!` it. Redirect a job you leave running in the background (`>/dev/null 2>&1 &`): its output has nowhere to go once `sh` returns.
- Settings: `/mnt/data/config.ini`; the card is at `/mnt/mmc`. A changed demo pid in `state` means demo restarted.

## Log

`--dev` builds start demo with its output, and that of the hciplayer it starts, appended to `/tmp/q2-demo.log` instead of the serial console; release builds keep its stderr alone, in `/tmp/q2-demo.err`, rolled at 256 KB ([boot.md](boot.md#rockbox-from-home)). The file is in RAM: once a minute, past 2 MB, it is copied to `/tmp/q2-demo.log.1` and emptied, and both are gone after a reboot; `log` reads the two together, and `q2guard` copies every line into the [unified log](internals.md#unified-log) on the card. demo's stdout is block-buffered once it goes to a file, so its lines arrive in batches rather than as they are printed; stderr arrives at once.

## Troubleshooting

- **`authentication rejected`:** the key differs from the card's file, the file is malformed, or the key is new and the USB cable is still connected (setup step 3).
- **`cannot reach Q2` / `No route to host`:** the Q2 isn't on the network yet (after a boot or a reboot it can take a few minutes), Smart Wi-Fi has its radio off ([Smart Wi-Fi](#smart-wi-fi)), or it is in Rockbox, which has no q2remote. Make sure the PC is on the same network and not still on wired Ethernet.
- **`no md-gpio-keys input device`:** run `devices`; the firmware may name its devices differently.

## Protocol

The client sends the key, a newline, the command, a newline, then any stdin. `sh` sends its script as that stdin and runs it under `setsid`, recording its pid in `/tmp/q2remote-*.pid` so Ctrl-C can kill its process group, and ends the output with `\n\0q2rc=STATUS\n`, where the client stops reading: a job left in the background doesn't hold the reply. The script isn't exported, so it may be any size. The helper answers `OK\n` and then streams the command's output until it exits, or answers `ERR\n` and closes. A handshake has to arrive within 10 seconds. `q2remote --shot` writes `Q2FB\0`, then the width, height, stride and size as little-endian 32-bit integers, then the displayed page; the client has it gzipped on the Q2, then rotates and encodes it. `state` reads demo's byte globals with `dd` from `/proc/<pid>/mem` at stock V1.36's addresses (`GLOBALS` in `tools/q2remote.py`). Each request that passes the key first renames a new `/tmp/q2remote.used`, holding the serving process's pid, into place; a `--dev` demo takes its new inode as a use ([Smart Wi-Fi](#smart-wi-fi)). `python3 test/remote.py` builds the helper for the host and checks authentication, rotation, revocation, the internal copy, that use file, framing and the input encoders.

## Car Remote across networks

The Vamp app reaches the Q2's coda-rc ([remote-protocol](remote-protocol.md)) when the phone and the Q2 are on different networks: the phone on cellular in the car, the Q2 on the car's or a home Wi-Fi. `q2-iroh` (`remote/iroh-sidecar`) is a small Rust program in every image that uses [iroh](https://github.com/n0-computer/iroh) 1.5: QUIC between the two ends, found by the Q2's endpoint id (a public key), starting through a relay and moving to a direct, hole-punched path when one opens. It runs beside coda-rc as a sidecar: each incoming stream becomes a local TCP connection to `127.0.0.1:7720`, so coda-rc is unchanged, and its own pairing, authentication and sealing still decide who may control the Q2. Tailscale, the other way to do this, was dropped for its memory.

### What the user does

1. On the Q2: Settings > Car Remote on, then Pair a Phone. It shows the pairing code, and above it a QR code once `q2-iroh` can be reached: from any network with a relay, from the same Wi-Fi without one ([remote-protocol: Pairing by QR code](remote-protocol.md#pairing-by-qr-code)).
2. On the phone: the Vamp app > Pair a Vamp, then **Scan QR code** and point it at the Q2, or tap the Vamp it lists (found by Bluetooth or on the Wi-Fi) and type the code. With a relay the phone can be on any network. Scanned, it dials the Q2's Wi-Fi address and its iroh endpoint at once, keeps the first that answers, and pairs on the code it read; the Q2 shows the pairing card for a moment, then "Pixel 10 Pro paired".
3. From then on the app connects the same way wherever the phone is: on the Q2's Wi-Fi straight to its address, elsewhere through the relay (or a direct path once iroh finds one). A phone paired the old way (Wi-Fi address or Bluetooth) learns the endpoint at its next session on a Q2 with `q2-iroh` (`GetRemote`), registers its own iroh key there (`RegisterRemote`), and does the same.

The Q2 needs Wi-Fi with internet for a phone elsewhere to reach it. Under Smart Wi-Fi, Car Remote keeps a joined Wi-Fi on for 10 minutes after its last use (a phone connected by any way, a pairing, Car Remote turned on); after that Smart Wi-Fi turns the radio off as for anything else, and a phone elsewhere cannot reach the Q2 until something brings Wi-Fi back ([internals: Smart Wi-Fi](internals.md#smart-wi-fi)).

### On the Q2

demo's Car Remote shim (`patch/coda_rc_shim.c`) runs `q2-iroh` beside the daemon while Wi-Fi has a link, stops it a minute after the link goes, and stops it with the daemon, so nothing listens while Car Remote is off and no endpoint retries its relay with no network ([internals: Across networks](internals.md#car-remote-iroh)). It lets in only the iroh keys of paired phones, which the daemon lists in `/tmp/coda-rc.iroh-allow`, and anyone else only while Pair a Phone shows its code ([remote-protocol: Across networks: only paired phones](remote-protocol.md#across-networks-only-paired-phones)); each stream goes to the daemon's loopback port 7721, the phone's key first. Its key is `/mnt/data/coda-rc/iroh.key` (0600, made on first start, never on the card), so the Q2's endpoint id survives restarts and updates; `/tmp/coda-rc.iroh` holds the id and the home relay for the daemon and the Pair a Phone page; `/tmp/q2-iroh.log` its log, kept to 64 KB. The relay is n0's public one (`*.relay.n0.iroh.link`) unless `/mnt/data/coda-rc/relay` names others, one URL a line (an own `iroh-relay` on the Hetzner box, later): write the file and turn Car Remote off and on.

```sh
q2-iroh serve [--key FILE] [--forward 127.0.0.1:7720 | --forward-named 127.0.0.1:7721] [--allow ID]... [--allow-file FILE]
              [--open-while FILE]... [--relay URL]... [--relay-file FILE] [--info FILE] [--pid FILE] [--nice N] [--log FILE]
q2-iroh dial ID [--listen 127.0.0.1:7720] [--relay URL] [--addr IP:PORT]... [--relay-only]   # a laptop's local port to the Q2's coda-rc
q2-iroh bench ID [--count N] [--size BYTES] [--relay URL] [--relay-only]                     # times echoed messages down one stream
```

### Building it

A firmware build needs no Rust: `tools/build.py` installs the binary `tools/iroh.py` pins (`PIN`: its SHA-256, size and the crate's hash), taken from `~/.cache/q2-thirdparty/q2-iroh/<sha256>/`. A change to the crate stops the build (and `test/unit/test_iroh.py`) until it is built and pinned again:

`q2/build.sh` builds the Q2's binary from a copy of the crate, with iroh 1.5.0's source patched by `q2/iroh-restun.patch`: iroh's periodic net report, QUIC probes to each of the relay regions every 20 to 26 s, cost the Q2 about 390 ms of CPU each (nearly all of q2-iroh's idle 2% of a core, measured 2026-11-20), so it runs every 300 to 330 s; a link change still sets one off at once. The patch's header has the trade-off. Measured on a Q2 (2026-11-20, 300 s idle): 490 ticks before, 56 after; reachable again 22 to 27 s after a 10 s Wi-Fi drop either way, the link change's report at once. The binary pinned with it, built with nightly-2026-11-18, also keeps 1.5 MB of heap where the one before kept 17 MB (RssAnon; that build's toolchain wasn't recorded). The laptop's side (`cargo build` below) is iroh as published.

```sh
python3 tools/iroh.py           # build from remote/iroh-sidecar, keep it in the cache, print the new PIN
python3 tools/iroh.py --check   # the pinned one is cached and the crate unchanged
python3 tools/build.py ZIP --out DIR --dev --build-iroh   # build it inside a firmware build, pinned or not
cargo build --release --manifest-path remote/iroh-sidecar/Cargo.toml   # the laptop's side
```

Building needs Rust nightly (the pinned binary's: nightly-2026-11-18) and `rust-src` (MIPS is a tier-3 target, so std is built from source), clang, lld, llvm-ar and llvm-strip, with the Codescape toolchain and the unpacked stock rootfs that `tools/thirdparty.py` keeps in its cache (or `Q2_STOCKFS`), as `tools/player.py` links against. `q2/build.sh` links against the Q2's own glibc 2.28 exactly as [librespot](librespot.md) is built (hard float, FP64, NaN-2008, `ld-linux-mipsn8.so.1`), with q2-librespot's target spec, linker wrapper and start files; `STOCK_LIB` and `GLIBC_INCLUDE` name another rootfs `/lib` or header tree. MIPS32 has no 64-bit atomics; the target spec's `max-atomic-width` of 32 is enough, as tokio and iroh's dependencies fall back on their own (`portable-atomic` among them) with no patch.

### Measured

On 2026-11-18 under `qemu-mipsel` on this laptop, against the stock rootfs's libraries, with a TCP echo server standing in for coda-rc. Memory is the guest's own resident pages (the host mappings inside the guest's address space, not qemu's); CPU is qemu's host time, an emulator's figure, not the X2000's.

| | |
|---|---|
| Binary, stripped | 5.33 MB (pinned build, 5,325,056 bytes); 2.6 MB of the compressed rootfs |
| Resident, idle and registered with the relay | 8.2 MB, steady over a minute |
| Resident, after 4,000 echoed messages on both paths | 8.6 MB |
| CPU idle (relay keep-alives) | about 0.6% of one host core under qemu |
| Connect by id alone | about 0.4 s, through the relay |
| Relay path (n0's `use1-1`) | p50 48 ms per 64-byte round trip; about 2.6 ms of CPU each under qemu |
| Direct path, hole-punched within the 3 s the bench waits | p50 0.5 ms (same host); about 0.44 ms of CPU each under qemu |

ponytail: an endpoint registered all the time Car Remote is on costs a little radio and battery that has not been measured on the device; and n0's public relay is a third party's service, with no promise of staying up (`/mnt/data/coda-rc/relay` moves it to an own one).
