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

## How it works

### adbd

`adbd` is Android 4.2.2's, as Debian packaged it (`android-tools` 4.2.2+git20130218) and Buildroot builds it for embedded Linux, with `tools/adbd.patch` on top ([third-party.md](third-party.md#adbd)). `tools/thirdparty.py` builds it (`/usr/bin/adbd`, about 64 KB) against the toolchain's glibc, the rootfs's own build, so it needs only `libc.so.6` and `libpthread.so.0`. It listens on USB alone, through the kernel's FunctionFS at `/dev/usb-ffs/adb`: no TCP port, no local socket a program could use instead (only the UI's, below), and it runs as root, so `adb shell` is a root shell once a computer is authorized. It keeps FunctionFS's `ep0` open for as long as it runs: closing it would make the kernel unbind the whole gadget. Its function's descriptors are an ADB interface (class `ff`, subclass `42`, protocol `01`) with Microsoft OS descriptors: compatible ID `WINUSB`, and the `DeviceInterfaceGUID` `{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}` that `adb.exe` looks for, so Windows binds its WinUSB driver and `adb.exe` finds the Q2 with no INF installed, as with an Android phone.

S90play makes the gadget (`q2adb-gadget setup`) and starts adbd before demo, and starts adbd again two seconds after it ever ends.

### The USB gadget

The Q2's USB modes are configfs gadgets that stock's scripts make and demo runs: `storage_config.sh` (USB storage, the gadget `storage_demo`) as soon as a cable is in, or `uac_device_config.sh` (the USB DAC) under **Audio and DAC**, each stopped when the cable comes out or the USB page closes (stock's `check_usb_status`, [internals.md](internals.md), "USB DAC on a host that never reads the card"). The USB controller takes one gadget at a time. adb gets a gadget of its own, `q2adb` (`patch/adb_gadget.sh`, installed as `/usr/bin/q2adb-gadget`), which has the controller whenever stock's gadgets don't:

- `setup` (boot): the gadget with ID `1d6b:0105` (Linux's FunctionFS gadget), `bcdDevice` `0x0223`, serial `Q2CODA`, adbd's function `ffs.adb`, and Microsoft OS descriptors on (`os_desc`, vendor code 1, `MSFT100`); FunctionFS mounted at `/dev/usb-ffs/adb`.
- `bind`: once adbd has written its descriptors (`ep1` exists), the gadget goes on the controller, unless parked.
- `park` / `unpark`: in a `--dev` build, `/usr/bin/storage_config.sh` and `uac_device_config.sh` are wrappers (`adb_wrapper` in `tools/build.py`) that call `park` before stock's start and `unpark` after its stop, stock's scripts kept unchanged beside them as `storage_config_stock.sh` and `uac_device_config_stock.sh`. `park` takes `q2adb` off the controller and unmounts configfs, which stock's start refuses to find mounted; `unpark` binds it again, in the background, once stock's stop has unmounted it (demo runs stock's stop on its UI thread, which must not wait out adbd). A flag file keeps adbd's restart loop from binding over stock's gadget, and a lock serializes the calls.

So with a cable in, the Q2 is a USB drive (or the DAC) exactly as stock makes it, and an adb device once the USB page is left with Return, until the cable comes out. Under Audio and DAC the DAC keeps the controller while the cable is in, so adb needs USB Mode on storage.

Why not one composite storage + adb gadget: it would change the gadget stock's USB storage and its eject handling (`usb_poll`) rely on, adb would come and go with the drive, and while the card is shared the Q2 has unmounted it and its UI sits on USB Connected, which is little use for development. Its own gadget leaves storage and the DAC untouched. Its own USB IDs matter on Windows, which keys its driver choice, and whether it asks for Microsoft OS descriptors at all (the answer is cached), on the vendor, product and `bcdDevice`.

The USB chooser on plug-in ([internals: USB pages](internals.md#usb-pages)) makes **USB Debugging** the way to this gadget: its probe's stop hands the controller back to `q2adb`, and its page's Stop parks it until the cable comes out.

### Authorization

adb's own RSA key authentication is always on: a computer gets nothing, not even a shell prompt, until it signs adbd's random token with a key the Q2 accepts. adbd reads, for every connection:

- `/mnt/data/adb_keys`: computers remembered with **Always Allow This Computer**;
- `/mnt/mmc/adb_keys` while the card is mounted: a developer's own, copied there by hand (one `adbkey.pub` line per computer).

A key in neither is offered to the UI as Android's adbd offers it to Android's framework: adbd listens on the Unix socket `/tmp/q2adb-ui` (mode 0600, root's), one client at a time, and writes `PK` followed by the computer's `adbkey.pub` line (base64 key, a space, `user@host`, no line end). The client answers `OK` to let that computer in this once, after appending the line to `/mnt/data/adb_keys` for Always Allow, or `NO`. With no client connected the key is refused at once, and adb shows the device as `unauthorized`. A computer that disconnects before the answer is forgotten; a late `OK` lets nothing in.

The Q2's side of the prompt, the **Allow USB Debugging?** dialog in demo, is not built yet. Its design: the question in the status row, "This computer's key:" over the key's fingerprint (Android's: the MD5 of the base64-decoded key as colon-separated hex pairs), the rows **Cancel** (selected) and **Allow**, the switch **Always Allow This Computer**, and the note "Gives full control of the Q2." Cancel, Return or a timeout answers `NO`. Until it is, the card's `adb_keys` is the way in.

### Tests

`python3 test/adb.py [BUILD]` runs the MIPS adbd (BUILD's with BUILD's `/lib`, or the one `tools/thirdparty.py` caches with the toolchain's glibc) under qemu-mipsel in a bwrap sandbox, with a stand-in FunctionFS whose `ep1` and `ep2` are FIFOs the test speaks adb's protocol through: the descriptors as the kernel parses them, refusal without a key, the prompt's socket (`PK`, `NO`, `OK`, Always Allow), the card's file, keys read again per connection, a key from `adb keygen` when adb and openssl are installed, `shell:`, `exec:` byte for byte, push and pull, and the refused services. It also runs `q2adb-gadget` and the wrapper under the host's sh against a directory tree standing in for configfs. The gadget on real hardware, Windows' driver binding and the controller hand-over are only tested on a Q2.

## Troubleshooting

- **`adb devices` lists nothing:** the Q2 is still on **USB Connected** (press Return), or USB Mode is **Audio and DAC**. In Device Manager the Q2 should be under Universal Serial Bus devices as "Q2 (ADB)". If it shows with a warning, Windows cached an earlier answer for these IDs: uninstall the device there (with its driver) and plug in again.
- **`unauthorized`:** the key isn't in either `adb_keys`, or the card's file was copied while USB storage had the card and the Q2 hasn't mounted it since (Return does). `adb kill-server` and `adb devices` retries.
- **adb stops answering until a restart:** adbd ended while its gadget was on the controller, which unbinds the gadget; unplug and plug in again, or restart the Q2.
- **Rolling back:** install a release update (or an earlier `--dev` one) from the card as usual; adb leaves nothing behind but `/mnt/data/adb_keys`.
