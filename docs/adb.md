# ADB over USB

A `--dev` build ([building.md](building.md)) runs `adbd`, Android's debug daemon, so a computer with Android's `adb` gets a root shell, file copies and screenshots over the USB cable: no Wi-Fi, no key to type. Release builds contain none of it, and `test/build.py --build` checks that. [q2remote](remote.md) over Wi-Fi stays as it was; adb is the same idea on a cable.

## Setup on Windows

1. Install a `--dev` update on the Q2.
2. On the PC, unpack Google's [SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) (it holds `adb.exe`) and open a terminal in that folder. No driver: the Q2 tells Windows to use its own WinUSB driver.
3. Run `adb devices` once: it makes the PC's key, `%USERPROFILE%\.android\adbkey.pub` (`~/.android/adbkey.pub` elsewhere).
4. Authorize the PC: plug the Q2 in (it shows **USB Connected**, the card on the PC as a drive), copy `adbkey.pub` to the card's root as `adb_keys` (more than one computer: one line each), and eject the card on the PC. The Q2's own **Allow USB Debugging?** prompt, which will make this step a press on the Q2, is not built yet ([Authorization](#authorization)).
5. Press **Return** on the Q2. Its card is mounted again, the UI is usable, and the PC now sees "Q2 (ADB)".
6. `adb devices` lists `Q2CODA  device`, and `adb shell id` prints `uid=0(root) gid=0(root)`.

macOS and Linux need no driver either; on Linux, a udev rule for `1d6b:0105` may be needed to use it without root.

## Use

```sh
adb shell                                  # busybox sh as root
adb shell 'cat /proc/meminfo'              # one command (through a terminal: line ends come back as CR LF)
adb exec-out 'cat /mnt/data/config.ini'    # bytes exactly as written, no terminal; stderr comes with them
adb push song.flac /mnt/mmc/Music/         # and adb pull
adb reboot
python3 tools/q2remote.py shot --adb out.png   # the screen as an upright PNG, over adb exec-out
```

`q2remote.py shot --adb` runs `q2remote --shot` on the Q2 through `adb exec-out` and turns the framebuffer the same way `shot` does over Wi-Fi; `--settle` works too. By hand: `adb exec-out "q2remote --shot 2>/dev/null | gzip -1" > shot.gz` gives the raw page (`Q2FB` header, then 320 × 375 BGRA rows, [remote.md](remote.md#protocol)).

`adb forward tcp:N tcp:M` works (the Q2 side connects to its own `127.0.0.1:M`). `adb root`, `adb remount`, `adb tcpip`, `adb logcat` and `adb install` don't: there is no Android behind them. `adb shell` gets the old, terminal-only protocol, so a command's exit status is not passed back.

## q2eq

`--dev` builds also install `q2eq` (`patch/q2eq.c`), which drives the EQ page's editor from a computer: list, load and adjust EQ live without touching the Q2, and without the app falling out of step. It never writes the EQ state itself: every command is a request that demo carries out through the editor's own actions, so the EQ page (it renders again if open), **EQ per Output** and the player follow exactly as they do for the wheel. Each command prints the resulting state as JSON on stdout and exits 0, or 1 when demo refused the change (`"ok": false` and the editor's own message, the text of the toast it would have shown), or 2 for a usage error or no answer.

```sh
adb shell q2eq current                       # the whole state as JSON
adb shell q2eq list                          # presets on the card
adb shell q2eq load 'Sennheiser HD 600'      # into the draft, as Presets does
adb shell q2eq band 3 gain 2.5               # an edit to the draft
adb shell q2eq apply                         # Apply changes: it plays
adb shell q2eq --now band 3 freq 1200        # the edit applied at once
adb push hd600.txt /tmp/ && adb shell q2eq import /tmp/hd600.txt
adb exec-out q2eq export > draft.txt         # the draft as Equalizer APO text
```

Edits go to the editor's draft, as on the Q2: the Advanced tab's **Apply changes** (`apply`) plays them, and `--now` applies after each change. A draft made while the page is closed is the one the page opens with; edits left unapplied on the page go when it closes, as before. Values are checked by the editor itself: a gain, preamp or balance must be one of its picker's rows (0.5 dB steps: gain -24 to 24, preamp -24 to 12 or `auto`, balance -12 to 12, above 0 the left quieter), frequency and Q are read by the editor's number reader and clamped as a typed value is (20 to 20,000 Hz; Q 0.1 to 10, a notch's to 30). `q2eq --help` lists every command.

### What each control is

| On the Q2                                                | q2eq                                                                                    |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Simple: Enabled                                          | `simple on`, `simple off`                                                               |
| Simple: Low, Mid, High sliders                           | `simple low DB`, `simple mid DB`, `simple high DB`                                      |
| Simple: Reset (R)                                        | `simple reset`                                                                          |
| Simple: Presets (a stock preset, playing at once)        | `simple preset NAME`                                                                    |
| Advanced: Apply changes                                  | `apply` (or `--now` on any edit)                                                        |
| Advanced: Reset to defaults                              | `reset`                                                                                 |
| Advanced: Enabled                                        | `on`, `off` (`off` turns Simple's 3EQ off too, as Control Center's EQ tile does)        |
| Advanced: EQ per Output                                  | `per-output on`, `per-output off`                                                       |
| Advanced: Balance                                        | `balance DB`                                                                            |
| Advanced: Preamp, and its Auto row                       | `preamp DB`, `preamp auto` (Auto's cut is `auto_preamp`)                                |
| Advanced: a band's row, the spare one past the last used | `band N ...` (N 1-30; past 10, at most one past the last used)                          |
| Band: Band switch                                        | `band N on`, `band N off`                                                               |
| Band: Channels                                           | `band N channel both\|left\|right`                                                      |
| Band: Type                                               | `band N type peaking\|lowshelf\|highshelf\|lowpass\|highpass\|bandpass\|notch\|allpass` |
| Band: Frequency (typed, or Raise, Lower and Step)        | `band N freq HZ`                                                                        |
| Band: Q (typed, or Raise and Lower)                      | `band N q Q`                                                                            |
| Band: Gain                                               | `band N gain DB`                                                                        |
| Presets: a saved preset                                  | `load NAME`; `list` lists them                                                          |
| Presets: Stock presets (into the draft)                  | `stock NAME` (Pop, Rock, Dance, Blues, Metal, Vocal, Classical, Jazz)                   |
| Presets: Save editor preset (Manual 01-10), Replace      | `save 1-10`, `save 1-10 --replace`                                                      |
| Presets: Rename a preset                                 | `rename OLD NEW`                                                                        |
| Presets: Delete a preset                                 | `delete NAME`                                                                           |
| Leaving the page with edits unapplied                    | `discard`                                                                               |

And beyond the page: `current` (the saved state, the draft, its name, Auto's figure, whether the page is open and which output's EQ it is), `outputs` (the outputs with an EQ remembered), `--output KEY` (`3.5mm`, `4.4mm`, `usb` or a Bluetooth MAC: a change is refused unless that is the output playing; `current --output KEY` reads another output's remembered EQ), `save-as NAME` (the draft saved under any name the rename rules allow), `import FILE` or `import - --name NAME` (Equalizer APO or AutoEQ text saved to the card's `EQ/`, then loaded as a preset; refused, and nothing written, when nothing in it can be used) and `export [NAME]` (the draft as Equalizer APO text, or a saved preset's file as it is).

What it has no command for, and why:

- **Removing a band.** The editor has no such control: a band's row stays once used, and a band is switched off instead (`band N off`).
- **Changing another output's EQ.** The editor changes only the output playing, whose EQ demo files away when another takes over; writing a remembered one would go around demo. `current --output KEY` reads it.
- **The Raise, Lower and Step rows** set a value in steps; `freq` and `q` set it outright, through the same clamp.

A request waits for demo's next look, every 500 ms, and is refused while a keyboard is up on the EQ page (a Frequency, Q or name edit). One request is taken at a time: two `q2eq` commands run at once can lose one, which then says demo did not answer.

## How it works

### adbd

`adbd` is Android 4.2.2's, as Debian packaged it (`android-tools` 4.2.2+git20130218) and Buildroot builds it for embedded Linux, with `tools/adbd.patch` on top ([third-party.md](third-party.md#adbd)). `tools/thirdparty.py` builds it (`/usr/bin/adbd`, about 64 KB) against the toolchain's glibc, the rootfs's own build, so it needs only `libc.so.6` and `libpthread.so.0`. It listens on USB alone, through the kernel's FunctionFS at `/dev/usb-ffs/adb`: no TCP port, no local socket a program could use instead (only the UI's, below), and it runs as root, so `adb shell` is a root shell once a computer is authorized. It keeps FunctionFS's `ep0` open for as long as it runs: closing it would make the kernel unbind the whole gadget. Its function's descriptors are an ADB interface (class `ff`, subclass `42`, protocol `01`) with Microsoft OS descriptors: compatible ID `WINUSB`, and the `DeviceInterfaceGUID` `{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}` that `adb.exe` looks for, so Windows binds its WinUSB driver and `adb.exe` finds the Q2 with no INF installed, as with an Android phone.

S90play makes the gadget (`q2adb-gadget setup`) and starts adbd before demo, and starts adbd again two seconds after it ever ends.

### The USB gadget

The Q2's USB modes are configfs gadgets that stock's scripts make and demo runs: `storage_config.sh` (USB storage, the gadget `storage_demo`) as soon as a cable is in, or `uac_device_config.sh` (the USB DAC) under **Audio and DAC**, each stopped when the cable comes out or the USB page closes (stock's `check_usb_status`, [internals.md](internals.md), "USB DAC on a host that never reads the card"). The USB controller takes one gadget at a time. adb gets a gadget of its own, `q2adb` (`patch/adb_gadget.sh`, installed as `/usr/bin/q2adb-gadget`), which has the controller whenever stock's gadgets don't:

- `setup` (boot): the gadget with ID `1d6b:0105` (Linux's FunctionFS gadget), `bcdDevice` `0x0223`, serial `Q2CODA`, adbd's function `ffs.adb`, and Microsoft OS descriptors on (`os_desc`, vendor code 1, `MSFT100`); FunctionFS mounted at `/dev/usb-ffs/adb`.
- `bind`: once adbd has written its descriptors (`ep1` exists), the gadget goes on the controller, unless parked.
- `park` / `unpark`: in a `--dev` build, `/usr/bin/storage_config.sh` and `uac_device_config.sh` are wrappers (`adb_wrapper` in `tools/build.py`) that call `park` before stock's start and `unpark` after its stop, stock's scripts kept unchanged beside them as `storage_config_stock.sh` and `uac_device_config_stock.sh`. `park` takes `q2adb` off the controller, stops adbd (its restart loop starts it again; parked, it finds no FunctionFS), unmounts FunctionFS, removes the gadget's directory (its links, then its directories innermost first) and unmounts configfs, which stock's start refuses to find mounted. The directory must go, not only the controller: this kernel's configfs makes every gadget directory a device named `android_usb/android0`, so with `q2adb`'s still there stock's `storage_demo` fails to be made (`sysfs: cannot create duplicate filename '/devices/virtual/android_usb/android0'`, `-EEXIST`), and the computer sees no storage and no USB Connected prompt follows (seen on a Q2 on 2026-11-15, every other plug-in). `unpark` makes the gadget again (`setup`'s steps) and binds it in the background once stock's stop has unmounted configfs and adbd has written its descriptors again (demo runs stock's stop on its UI thread, which must not wait out adbd). `test/adb.py`'s stand-in configfs refuses a second gadget directory, as the kernel does. A flag file keeps adbd's restart loop from binding over stock's gadget, and a lock serializes the calls.

So with a cable in, the Q2 is a USB drive (or the DAC) exactly as stock makes it, and an adb device once the USB page is left with Return, until the cable comes out. Under Audio and DAC the DAC keeps the controller while the cable is in, so adb needs USB Mode on storage.

Why not one composite storage + adb gadget: it would change the gadget stock's USB storage and its eject handling (`usb_poll`) rely on, adb would come and go with the drive, and while the card is shared the Q2 has unmounted it and its UI sits on USB Connected, which is little use for development. Its own gadget leaves storage and the DAC untouched. Its own USB IDs matter on Windows, which keys its driver choice, and whether it asks for Microsoft OS descriptors at all (the answer is cached), on the vendor, product and `bcdDevice`.

The USB chooser on plug-in ([internals: USB pages](internals.md#usb-pages)) makes **USB Debugging** the way to this gadget. With `q2adb` on the controller when the cable comes, it is the chooser's probe ([internals: USB cable events](internals.md#usb-cable-events)): the computer sees one device, USB Debugging and Charge Only keep it, and only Transfer Files or USB Audio park it for stock's gadget. When it is not back yet (within about two seconds of a cable out after Transfer Files, while adbd restarts), stock's storage gadget probes instead, and its stop hands the controller back to `q2adb`. USB Debugging's page's Stop parks it until the cable comes out; the cable out stops a probe's storage gadget even when the chooser was never answered.

### Authorization

adb's own RSA key authentication is always on: a computer gets nothing, not even a shell prompt, until it signs adbd's random token with a key the Q2 accepts. adbd reads, for every connection:

- `/mnt/data/adb_keys`: computers remembered with **Always Allow**;
- `/mnt/mmc/adb_keys` while the card is mounted: a developer's own, copied there by hand (one `adbkey.pub` line per computer).

A key in neither is offered to the UI as Android's adbd offers it to Android's framework: adbd listens on the Unix socket `/tmp/q2adb-ui` (mode 0600, root's), one client at a time, and writes `PK` followed by the computer's `adbkey.pub` line (base64 key, a space, `user@host`, no line end). The client answers `OK` to let that computer in this once, after appending the line to `/mnt/data/adb_keys` for Always Allow, or `NO`. With no client connected the key is refused at once, and adb shows the device as `unauthorized`. A computer that disconnects before the answer is forgotten; a late `OK` lets nothing in.

The Q2's side of the prompt (`patch/usb_ui.c` `auth_open`, a `--dev` build's) is **Allow USB Debugging?** on the [confirmation card](oled-design.md#confirmation-card): its body the key's fingerprint ("Key " and the first eight bytes of the MD5 of the base64-decoded key as colon-separated hex pairs, Android's) over "It gets full control of the Q2.", the switch row **Always Allow**, then **Cancel** (selected) and **Allow**. Allow answers `OK`, first appending the key's line to `/mnt/data/adb_keys` when Always Allow is on; Cancel or Return answers `NO`, and so does a question that arrives while another card is up (the computer asks again). The card's `adb_keys` is the other way in.

### q2eq's requests

`q2eq` writes a request (`q2eq_request`, `patch/q2eq.h`: the change, a band, a value as text, a name, flags for `--now` and `--replace`, and a random number to match its answer) to `/tmp/q2eq.req` through a temporary name, mode 0600. demo's `q2eq_poll` (`patch/peq_ui.c`, compiled into `--dev` builds alone) looks for it every 500 ms from `headset_eq_poll`, after EQ per Output's swap, takes only a regular file owned by root that no one else can read or write, removes it, carries it out, and answers in `/tmp/q2eq.rep` (`Q2EQR01`, then `q2eq_reply`: the message, the saved state, the draft and its name). How it maps onto the editor is in [internals: q2eq requests](internals.md#q2eq-requests). `q2eq` gives up after 10 s and removes its request, so a demo that comes back later does not act on it.

### Tests

`python3 test/q2eq.py` builds `q2eq` for the host and answers it with a fake demo: each command's request, usage, the JSON, the wait and its answer's match. `test/fw/test_q2eq.py` runs demo's side in a `--dev` build of the payload's C. `python3 test/adb.py [BUILD]` runs the MIPS adbd (BUILD's with BUILD's `/lib`, or the one `tools/thirdparty.py` caches with the toolchain's glibc) under qemu-mipsel in a bwrap sandbox, with a stand-in FunctionFS whose `ep1` and `ep2` are FIFOs the test speaks adb's protocol through: the descriptors as the kernel parses them, refusal without a key, the prompt's socket (`PK`, `NO`, `OK`, Always Allow), the card's file, keys read again per connection, a key from `adb keygen` when adb and openssl are installed, `shell:`, `exec:` byte for byte, push and pull, and the refused services. It also runs `q2adb-gadget` and the wrapper under the host's sh against a directory tree standing in for configfs. The gadget on real hardware, Windows' driver binding and the controller hand-over are only tested on a Q2.

## Troubleshooting

- **`adb devices` lists nothing:** the Q2 is still on **USB Connected** (press Return), or USB Mode is **Audio and DAC**. In Device Manager the Q2 should be under Universal Serial Bus devices as "Q2 (ADB)". If it shows with a warning, Windows cached an earlier answer for these IDs: uninstall the device there (with its driver) and plug in again.
- **`unauthorized`:** the key isn't in either `adb_keys`, or the card's file was copied while USB storage had the card and the Q2 hasn't mounted it since (Return does). `adb kill-server` and `adb devices` retries.
- **adb stops answering until a restart:** adbd ended while its gadget was on the controller, which unbinds the gadget; unplug and plug in again, or restart the Q2.
- **Rolling back:** install a release update (or an earlier `--dev` one) from the card as usual; adb leaves nothing behind but `/mnt/data/adb_keys`.
