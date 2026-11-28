# ADB: USB, Wi-Fi and vamp

A `--dev` build ([building.md](building.md)) runs `adbd`, Android's debug daemon, so a computer gets a root shell, file copies and screenshots over the USB cable, and over Wi-Fi once the computer is trusted ([ADB over Wi-Fi](#adb-over-wi-fi)). [`vamp`](#vamp), the project's own client, drives it without Google's `adb`; Google's `adb` works too. Release builds contain none of it, and `test/build.py --build` checks that. [q2remote](remote.md)'s Wi-Fi channel is retired in favour of adb ([vamp: q2remote](#vamp)).

## Setup on Windows

1. Install a `--dev` update on the Q2.
2. On the PC, unpack Google's [SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools) (it holds `adb.exe`) and open a terminal in that folder. No driver: the Q2 tells Windows to use its own WinUSB driver.
3. Run `adb devices` once: it makes the PC's key, `%USERPROFILE%\.android\adbkey.pub` (`~/.android/adbkey.pub` elsewhere).
4. Authorize the PC: plug the Q2 in (it shows **USB Connected**, the card on the PC as a drive), copy `adbkey.pub` to the card's root as `adb_keys` (more than one computer: one line each), and eject the card on the PC. Or skip the file: with the PC's adb running, the Q2 asks **Allow this computer?** and a press on **Allow** lets that computer in ([Authorization](#authorization)).
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
adb exec-out q2guard --dump > q2.log                                                  # the unified log, in order, RAM's lines too
python3 tools/q2log.py --adb                                                         # it, the heat and Bluetooth logs as one timeline
python3 tools/q2log.py --adb --btev                                                  # the Bluetooth event log alone (q2guard --dump-bt), days of it
adb exec-out 'cd /mnt/mmc/.q2muse/logs; cat q2-heat.3 q2-heat.2 q2-heat.1 q2-heat.0 2>/dev/null' | python3 tools/q2heat.py -   # the heat log, oldest file first
adb exec-out 'cd /mnt/mmc/.q2muse/logs; cat q2-bt.3 q2-bt.2 q2-bt.1 q2-bt.0 2>/dev/null' | python3 tools/q2bt.py -   # the Bluetooth link log
adb exec-out 'cd /mnt/mmc/.q2muse/logs; cat q2-wifi.3 q2-wifi.2 q2-wifi.1 q2-wifi.0 2>/dev/null' | python3 tools/q2wifi.py -   # the Wi-Fi fault log
python3 tools/q2remote.py shot --adb out.png   # the screen as an upright PNG, over adb exec-out
```

The unified log ([internals](internals.md#unified-log)) is the first line of that block: `q2guard --dump` has the running `q2guard` write out the lines it still holds in RAM (a few seconds at most), then prints `q2-log.3` to `.0`, oldest first, each line `TIME UPTIME_MS LEVEL SOURCE message`. `tools/q2log.py --adb` takes it, the heat log and the Bluetooth log over adb and merges them by time into one timeline; `--level W` keeps the warnings and errors, `--src bt,usb` some sources; given files instead of `--adb`, it reads saved ones (`q2-log.3 q2-log.2 q2-log.1 q2-log.0 --heat q2-heat.1 q2-heat.0 --bt q2-bt.1 q2-bt.0`). `--adb --btev` reads the Bluetooth event log instead (`q2guard --dump-bt`: the same flush, then `q2-btev.3` to `.0`), the unified log's Bluetooth lines kept for days ([internals](internals.md#bluetooth-event-log)).

The heat log (`q2guard`, [internals](internals.md#overheat-guardian)) is read back with the last line of that block: `.1` then `.0` so the lines come out in order, `2>/dev/null` inside the quotes because `exec-out` passes the Q2's stderr through (a Q2 whose first log hasn't filled has no `.1`), and `tools/q2heat.py` turns them into a timeline with the fastest rise and the processes using the CPU then. The Bluetooth link log beside it is read the same way with `tools/q2bt.py` ([internals](internals.md#bluetooth-link-monitor), which has the A/B walk's whole procedure). The card has to be mounted on the Q2: with it lent to a computer (USB Transfer Files) the files are not there to read, and the lines wait in RAM.

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
| Advanced: Enabled                                        | `on`, `off` (`off` turns Simple's 3EQ off too, as Control Center's EQ circle does)        |
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

## Installing an update over adb

`tools/q2flash.py` (also `q2 flash`) installs a build on a Q2 over the cable with no one at its screen. It copies `update.tar` to the card, checks the copy, and asks the Q2 to run its own **Settings > General > Software Update** path on it, so the Q2 checks the file again, shows Restarting to Install with its bar, and stock's updater flashes it as from the card. Nothing on the computer touches the flash itself.

```sh
python3 tools/q2flash.py /tmp/q2-dev --dry-run   # everything but the install: push, check the copy, the Q2's own check
python3 tools/q2flash.py /tmp/q2-dev             # then install: the Q2 restarts, the recovery flashes, it comes back
python3 tools/q2flash.py update.tar -s Q2CODA    # a release's update.tar (its update.tar.sig beside it); one Q2 of several
```

In order, it:

1. Refuses a file that is not one of ours, before adb is used: `firmware_v20.info` first in the tar, `Shanling Q2`, a version of ours (a release's `V1820`, a `--dev` build's `V0JZi`; stock's `V1.36` is refused), both images' MD5s right, and either a `manifest.json` beside it whose `update_sha256` and `version` are this file's (a build directory) or an `update.tar.sig` the release key signed ([releasing](releasing.md#signing-a-release)). This guards against flashing the wrong file, not against someone set on it: a manifest is easily written.
2. Asks the Q2 its build (`q2update version`) and refuses one that already runs this tag.
3. Refuses a battery under 30% unless it is charging (`cw2015_capacity`, and `Charging` or `Full` in `power_supply/battery/status`; `--min-battery` changes the 30). Stock's own check, under 16% with no cable, still holds on the Q2.
4. Refuses a card that is not mounted on the Q2 (lent to the computer) or has no room for the file and the 100 MB stock's updater unpacks beside it.
5. Pushes to `/mnt/mmc/update.tar.part`, compares `sha256sum` on the Q2 with the local file's (a copy that differs is removed), and only then renames it over `/mnt/mmc/update.tar`, so a cut push never leaves a broken `update.tar`. adb drops the exit status of a command on the Q2, so the renamed file's `sha256sum` is compared too: a rename that failed (the card remounted read-only, or lent to a computer between the two steps) leaves an older `update.tar` there, and that is refused before anything is installed.
6. Runs `q2update check` (the Q2's check on screen, then "Update checked: ready to install"), whose answer must name this build's tag as the card's file; `--dry-run` stops there, with nothing installed. Then `q2update install` (the same check, then Restarting to Install), whose answer must name the tag again, then waits for the Q2 to leave adb, come back (up to 15 minutes) and answer `q2update version` with the new tag. `--no-wait` returns once the install has started.

Exit status 0 is installed (or, dry run, would install); 1 is refused or failed, with the reason; 3 is a Q2 whose build has no `q2update` (one from before it): the file is on the card and checked, and **Software Update** has to be pressed on the Q2 that once.

### q2update

`q2update` (`patch/q2update.sh`, a `--dev` build's `/usr/bin/q2update`) is the Q2's side, and works by hand too:

```sh
adb exec-out q2update version    # this build's tag, nothing opened
adb exec-out q2update check      # the card's update.tar checked as Software Update checks it; nothing installed
adb exec-out q2update install    # the same, then stock's updater, with no Install Update? card to answer
```

It prints demo's answer, `NONCE WORD INSTALLED UPDATE`: WORD is `version`, `ok` (the check passed), `installing`, or why not: `busy` (Software Update or a confirmation card is already up, such as the guardian's at boot), `low`, `nocard`, `nofile`, `full`, `bad` (damaged, another device's), `latest` (this build's own tag), `cancelled` (Return pressed on the page), `failed` (stock's updater refused after all), `unknown`. Its exit status is 0, 1 for those, 2 for no answer, but `adb shell` and `exec-out` drop it: read the word.

It writes `NONCE OP` to `/tmp/q2update.req` through a temporary name, mode 0600, as `q2guard` writes its state file, and waits for `/tmp/q2update.ack`. demo's `q2update_poll` (`patch/sw_update.c`, compiled into `--dev` builds alone, every 500 ms from `headset_eq_poll` beside `q2eq_poll`) takes only a regular file owned by root that no one else can read or write, removes it, answers `taken`, and runs `ringnav_fwupdate_tf`, the SD Card Update path the download ends in ([internals: SD Card Update](internals.md#sd-card-update)): the same page, stock's checks, the tar's check on a thread. Where the user would be asked Install Update?, `install` closes the page and runs stock's `checkfw_dialog` as Install and Restart does (`sw_answer`), and `check` closes it with the toast. Any other answer leaves its page on screen, as for a press. Each answer is written whole (`.tmp`, then renamed). `q2update` gives up after 5 s with no `taken` (demo is not running) or 180 s with no answer (`Q2UPDATE_WAIT`), and removes its request.

`test/fw/test_q2update.py` runs demo's side in a `--dev` build of the payload's C, `test/q2update.py` the helper under the host's `sh` against a fake demo, and `test/unit/test_q2flash.py` the host tool against a fake adb and Q2. In the emulator (2026-11-19) `q2update` reached demo through the sandbox's namespaces, `q2flash.py --dry-run` ran whole and the toast showed, and `install` reached stock's updater, which stopped at its `tar` (the emulator runs demo in `/release/bin`, not `/`) and answered `failed` with Update Can't Be Used on screen; the flash itself is only tested on a Q2.

## ADB over Wi-Fi

A `--dev` build also runs a second adbd on TCP port 5555, `adbd --tcp 5555`, so a computer reaches the Q2 over Wi-Fi with the same RSA key authentication as on the cable, and advertises it by mDNS, so it is found by name on whatever network it has joined. [`vamp`](#vamp) is the client; Google's `adb connect HOST:5555` speaks to the same adbd.

**Trusting the first key.** The Wi-Fi adbd lets in only a computer whose key the Q2 already trusts, and it never asks: nothing on a network can put Allow this computer? on the Q2's screen, and an unknown key is refused at once (`vamp` says so, and how to fix it). So the first key comes one of the two ways a key comes on the cable ([Authorization](#authorization)):

1. **Over USB, once:** plug in, pick USB Debugging, and connect with any adb client using this computer's key; vamp's key is adb's own file, `~/.android/adbkey`, so `adb devices` from Google's platform-tools does it. On **Allow this computer?** leave **Always allow this computer** on and press Allow: the key goes into `/mnt/data/adb_keys`, and from then on the Q2 takes it over Wi-Fi too.
2. **Or the card:** `vamp pubkey` prints the key's `adb_keys` line; put it in the card's `adb_keys` (one line a computer) with the card on the computer as a drive, and eject.

**No open port without a trusted key.** `q2adb-wifi` (`patch/adb_wifi.sh`, started by S90play) runs the Wi-Fi adbd and `q2adb-mdns` only while `/mnt/data/adb_keys` or the card's `adb_keys` holds a line and Developer access is on, and stops both as soon as either is not; it looks every 10 s. A Q2 nobody has trusted listens on nothing. A key removed under **Developer Options > Trusted Computers** is refused at the next connection, as on the cable; removing the last one closes the port within 10 s.

**Developer access.** In a `--dev` build it is on unless `config.ini` has `ADBWIFI=0` (in `[Q2POD]`). The design for release builds: the same three programs ship there too, off by default, and a **Developer access** switch in Settings > General writes `ADBWIFI=1` (on) or `0`; turning it on with no trusted computer says to allow one over USB first. That row, and shipping adbd in a release, are not built yet; today a release has none of it (`test/build.py --build`).

**Found by name.** `q2adb-mdns` (`patch/adb_mdns.c`) is coda-rc's mDNS responder ([remote-protocol: Discovery](remote-protocol.md#discovery)) for the service type `_adb._tcp`, the one modern adb's own discovery lists (`adb mdns services`); 4.2.2's adbd has no TLS, so no `_adb-tls-connect._tcp`. It answers for `<name>._adb._tcp.local` (the Bluetooth name, Vamp unless renamed), whose SRV names port 5555 on `q2muse-<6 hex>.local`, the host name Car Remote's responder uses, with the same device id in the TXT record (`id=`, from `/mnt/data/coda-rc/id`), so a Q2 is the same Q2 to both. It sends two announcements when wlan0 gets an address (a new network, a new lease, Wi-Fi back on), a goodbye when the address goes or it is stopped, and otherwise only answers.

**Smart Wi-Fi.** A Q2 whose radio is off can't be reached, and Smart Wi-Fi turns it off after 5 idle minutes ([internals: Smart Wi-Fi](internals.md#smart-wi-fi)). The Wi-Fi adbd counts as needing the network exactly as q2remote does: each computer let in and each service it opens renames a new `/tmp/q2remote.used` into place, which a `--dev` demo takes as a request and holds the radio for 30 minutes after (`REMOTE_HOLD_MS`), and for demo's first 30 minutes after boot (`REMOTE_BOOT_MS`), so a Q2 just installed or restarted is reachable. A session left open with nothing run lets the hold run out; past it, wake the radio on the Q2 (the Wi-Fi page or Streaming), plug in the cable, or turn Smart Wi-Fi off. Keep-alive probes end a connection whose computer slept or left within about 90 s.

**What it costs.** adbd waits in `accept` and wakes only for a connection. `q2adb-mdns` wakes every 5 s to read wlan0's address (one ioctl) and for each mDNS packet on the network, as coda-rc's responder does while Car Remote is on. `q2adb-wifi` wakes every 10 s for a `grep` and a `sleep`. Not yet measured on a Q2 ([device test plan](#device-test-plan)). ponytail: a netlink socket in `q2adb-mdns` and an inotify watch in `q2adb-wifi` would remove both clocks.

**Limits.** adb's traffic is not encrypted (4.2.2 has no TLS): a key keeps others out, but a shell's output and pushed files cross the network as they are, so use it on a network you trust. The Wi-Fi adbd keeps at most 8 connections (`TCP_MAX`, `tools/adbd.patch`); ponytail: a host on the network can hold all 8 unauthorized and lock out the rest until it lets go, which a handshake deadline would end. Messages carry 4096 bytes at most (4.2.2's `MAX_PAYLOAD`) and each waits for its OKAY, so a push moves 4 KB per round trip: on a good Wi-Fi link (a few ms) about 1 MB/s.

## vamp

`vamp` (`tools/vamp.py`) is the project's own client for the Q2's adbd, so development and testing need neither Google's `adb` binary nor stock's tools. It speaks adb's protocol itself: CNXN, AUTH with this computer's RSA key (PKCS#1 v1.5 over the token as adb's `RSA_sign` gives it, the key offered when the signature is refused), OPEN, WRTE, OKAY and CLSE with one write in flight per stream, `exec:` and `shell:`, and `sync:` for push and pull. It finds the Q2 with mDNS, remembers it (`~/.config/vamp/target.json`), and when the remembered address stops answering (a new network, a new lease after sleep) finds it again by its id.

```sh
ln -s "$PWD/tools/vamp.py" ~/.local/bin/vamp     # or: alias vamp="python3 $PWD/tools/vamp.py"
vamp find                                        # Desk Q2   192.168.1.23:5555   id=1a2b3c4d5e6f7081
vamp connect 'Desk Q2'                           # check it answers, and remember it; or an address, HOST[:PORT]
vamp shell 'cat /proc/meminfo'                   # output as it comes, and the command's exit status
vamp shell                                       # an interactive root shell (busybox sh on adbd's pty)
vamp push song.flac /mnt/mmc/Music/              # through .part, renamed once whole, its size checked
vamp pull /mnt/data/config.ini
vamp install /tmp/q2-dev                         # update.tar or a build directory: q2flash.py's flow, over Wi-Fi
vamp shot --settle out.png                       # the screen, upright 375x320
vamp log -f                                      # demo's output; log 200, log --clear
vamp key centre; vamp wheel 3; vamp tap 187 160  # input: key, wheel, tap, swipe, as q2remote's
vamp prof 5 --build /tmp/q2-dev                  # demo's threads sampled, symbolized
vamp state; vamp wake; vamp devices; vamp events 2 5; vamp reboot --wait
vamp -t 192.168.1.40 shell id                    # another Q2 for one command (or VAMP_TARGET)
vamp pubkey                                      # this computer's adb_keys line
vamp audio --out capture.wav --seconds 30        # what the player hands the DAC, bit for bit (docs/usb-audio.md)
```

- **The key** is adb's own `~/.android/adbkey` (`ANDROID_USER_HOME`, or `VAMP_KEY` for another file), made on first use as adb makes it (2048-bit RSA, PKCS#8 PEM, `adbkey.pub` beside it), so a computer trusted for Google's adb is trusted for vamp and the other way round.
- **`shell COMMAND`** runs it as q2remote's `sh` does: under `setsid sh -c` with its stdin `/dev/null`, its pid in `/tmp/vamp-*.pid` so Ctrl-C kills its process group, and a last line `\n\0vamprc=STATUS\n` that vamp strips and exits with. adb's own `shell:` drops the status on this adbd.
- **stdin.** adb's streams cannot be half-closed, so where a command needs its stdin (q2remote's input writers), vamp pushes it to `/tmp/vamp-in-*` first and the command's shell reads and removes it; a command longer than an OPEN's 4096 bytes goes the same way as a script.
- **`install`** is `tools/q2flash.py`'s `flash` with vamp in adb's place ([Installing an update over adb](#installing-an-update-over-adb)): the file checked as ours, the Q2's build, battery and card, the push to `/mnt/mmc/update.tar.part`, its SHA-256 on the Q2, the rename and its SHA-256 again, `q2update check` naming the tag, `q2update install`, then the wait for the Q2 to leave and come back (a fresh connection each look, found again by id if its address changed) and answer `q2update version` with the new tag. `--dry-run`, `--no-wait` and `--min-battery N` as there. The Q2 has to be on Wi-Fi when it comes back: within the 30 minutes after boot it is.

**q2remote.** The commands vamp shares with [q2remote](remote.md) (shot, log, key, wheel, tap, swipe, state, wake, devices, events, reboot, prof) are q2remote.py's own: vamp sets `q2remote.TRANSPORT` to its connection, and each goes over adb with no change to what it runs on the Q2, so the device-side knowledge (input encodings, the wheel's sensitivity, the framebuffer's rotation, demo's globals, the profiler's symbols) stays in one place. q2remote's port 2302 channel is retired in favour of adb: it sends its key in the clear and has no discovery. It keeps working for now; once the [device test plan](#device-test-plan) has passed on a Q2, S90play stops starting its listener, `q2remote.py`'s socket client and `find` are removed, and the module stays as the device commands vamp drives. The Q2-side helper, `q2remote --shot` and `--prof`, stays: vamp's `shot` and `prof` run it.

**Not yet:** USB. vamp speaks only TCP; a USB transport through libusb (the interface is class `ff`, subclass `42`, protocol `01`, two bulk endpoints) is the next step and would let vamp do the first pairing itself. Until then the first key is trusted with Google's adb once, or with the card's file.

### Tests

`test/unit/test_vamp.py` runs vamp against a fake adbd (over socket pairs: test/unit opens no real socket) that refuses what the Q2's would: a message over 4096 bytes, a WRTE before the OKAY for the last one, a signature that is not PKCS#1 v1.5 over SHA-1's DigestInfo, checked with the public key as mincrypt reads `adbkey.pub`. It checks the key files against openssl's PKCS#8 byte for byte, the public key's layout (`n0inv`, `R^2 mod n`), push, pull and their refusals, the streamed shell's status line, stdin through a file, the reconnect by id, mDNS records, and vamp's coverage is 100%. Mutants of the flow control, the message size, the DigestInfo, the status line, the stdin file and `n0inv` each fail it. `test/unit/test_adb_mdns.py` builds coda-rc's responder for this machine and checks that vamp reads the Q2's `_adb._tcp` announcement and that the responder answers vamp's query. `python3 test/adb.py` runs the MIPS adbd with `--tcp` under qemu-mipsel, driven by vamp, and `q2adb-wifi` under the host's sh (see [Tests](#tests)).

## Device test plan

The main session runs these on a Q2 with a `--dev` build of this branch, the Mac on the same Wi-Fi. Each step says what passes.

1. **The first key, over USB.** Remove the Mac from Developer Options > Trusted Computers if it is there. Before: `vamp find` lists nothing (no port open without a key; `nc -z Q2IP 5555` refused). Plug in, USB Debugging, `adb devices` (Google's, the shared key), Allow with Always allow on. Within 10 s `vamp find` lists the Q2 by its Bluetooth name. Unplug.
2. **An unknown computer.** From a second computer (or `VAMP_KEY=/tmp/other vamp connect IP`): refused with the "allow over USB" message, and nothing shows on the Q2's screen.
3. **Connect and shell.** `vamp connect NAME`: prints the build's tag. `vamp shell id` prints `uid=0(root)` and exits 0; `vamp shell 'exit 3'; echo $?` prints 3; `vamp shell` gives a prompt, `ls /mnt/mmc` works, Ctrl-D leaves. `vamp shell 'sleep 100'` then Ctrl-C: `vamp shell 'ps | grep "sleep 100"'` shows none.
4. **Push and pull.** `vamp push` a 30 MB file to `/mnt/mmc/`: note the MB/s; `vamp pull` it back and `cmp`. With the card lent to the Mac as a drive, `vamp push x /mnt/mmc/` is refused, and works again after Return.
5. **Screen and input.** `vamp shot --settle a.png` is upright and current; `vamp wheel 3`, `vamp key centre`, `vamp tap 187 160` act as on the cable.
6. **Install.** `vamp install BUILD --dry-run` passes; then `vamp install BUILD` (a newer `--dev` build): the Q2 restarts, flashes, and vamp reports the new tag with no cable in.
7. **A new network.** Join the Q2 and the Mac to another network (a phone's hotspot). `vamp find` lists the Q2 there by name; `vamp shell id` works with no `connect` again (found by its id at the new address, `~/.config/vamp/target.json` updated).
8. **Reconnect after sleep.** Screen off, wait 10 minutes (Smart Wi-Fi on): `vamp shell id` still works (the hold). Then with no command for 35 minutes, Smart Wi-Fi off the radio: vamp fails with "cannot reach"; open the Wi-Fi page on the Q2 and it works again within about 20 s, at a new address if the lease changed. The unified log has the hold's `q2remote:` lines.
9. **Developer access off.** `vamp shell 'echo ADBWIFI=0 >>/mnt/data/config.ini'` (q2adb-wifi takes the line in any section): within 10 s the port closes and `vamp find` drops the Q2 (its goodbye). Remove the line over USB adb; it comes back.
10. **Battery cost of the listener.** Two nights, screen off, music stopped, Wi-Fi joined and Smart Wi-Fi off: one with `ADBWIFI=0`, one with it on and a key trusted. Compare the gauge's drop (`cw2015_capacity`) and the wake-ups (`cat /proc/interrupts` and `/proc/PID/stat` for adbd, `q2adb-mdns` and `q2adb-wifi`, before and after). Write the figures here.
11. **The cable still works.** With Wi-Fi adb connected, plug in and pick Transfer Files: the Mac sees the card, and the Wi-Fi session stays up (its own process). Return: `adb devices` sees the Q2 again on USB.

## How it works

### adbd

`adbd` is Android 4.2.2's, as Debian packaged it (`android-tools` 4.2.2+git20130218) and Buildroot builds it for embedded Linux, with `tools/adbd.patch` on top ([third-party.md](third-party.md#adbd)). `tools/thirdparty.py` builds it (`/usr/bin/adbd`, about 64 KB) against the toolchain's glibc, the rootfs's own build, so it needs only `libc.so.6` and `libpthread.so.0`. It listens on USB alone, through the kernel's FunctionFS at `/dev/usb-ffs/adb`, with no local socket a program could use instead (only the UI's, below); a second process, `adbd --tcp 5555`, is the Wi-Fi one ([ADB over Wi-Fi](#adb-over-wi-fi)), which opens no UI socket, so stock's gadgets taking the controller (which stops the USB adbd) leave a Wi-Fi session alone. Each runs as root, so `adb shell` is a root shell once a computer is authorized. It keeps FunctionFS's `ep0` open for as long as it runs: closing it would make the kernel unbind the whole gadget. Its function's descriptors are an ADB interface (class `ff`, subclass `42`, protocol `01`) with Microsoft OS descriptors: compatible ID `WINUSB`, and the `DeviceInterfaceGUID` `{F72FE0D4-CBCB-407D-8814-9ED673D0DD6B}` that `adb.exe` looks for, so Windows binds its WinUSB driver and `adb.exe` finds the Q2 with no INF installed, as with an Android phone.

S90play makes the gadget (`q2adb-gadget setup`) and starts adbd before demo, and starts adbd again two seconds after it ever ends.

### The USB gadget

The Q2's USB modes are configfs gadgets that stock's scripts make and demo runs: `storage_config.sh` (USB storage, the gadget `storage_demo`) as soon as a cable is in, or `uac_device_config.sh` (the USB DAC) under **Audio and DAC**, each stopped when the cable comes out or the USB page closes (stock's `check_usb_status`, [internals.md](internals.md), "USB DAC on a host that never reads the card"). The USB controller takes one gadget at a time. adb gets a gadget of its own, `q2adb` (`patch/adb_gadget.sh`, installed as `/usr/bin/q2adb-gadget`), which has the controller whenever stock's gadgets don't:

- `setup` (boot): the gadget with ID `1d6b:0105` (Linux's FunctionFS gadget), `bcdDevice` `0x0223`, serial `Q2CODA`, adbd's function `ffs.adb`, and Microsoft OS descriptors on (`os_desc`, vendor code 1, `MSFT100`); FunctionFS mounted at `/dev/usb-ffs/adb`.
- `bind`: once adbd has written its descriptors (`ep1` exists), the gadget goes on the controller, unless parked.
- `audio on|off|try|rehearse|keep`: a UAC2 function beside adbd's, the Q2's output as the computer's microphone ([usb-audio](usb-audio.md#usb-audio)).
- `park` / `unpark`: in a `--dev` build, `/usr/bin/storage_config.sh` and `uac_device_config.sh` are wrappers (`adb_wrapper` in `tools/build.py`) that call `park` before stock's start and `unpark` after its stop, stock's scripts kept unchanged beside them as `storage_config_stock.sh` and `uac_device_config_stock.sh`. `park` takes `q2adb` off the controller, stops the USB adbd and waits for it to end (its restart loop starts it again; parked, it finds no FunctionFS; adb over Wi-Fi, `adbd --tcp`, is left running), unmounts FunctionFS, removes the gadget's directory (its links, then its directories innermost first) and unmounts configfs, which stock's start refuses to find mounted. The directory must go, not only the controller: this kernel's configfs makes every gadget directory a device named `android_usb/android0`, so with `q2adb`'s still there stock's `storage_demo` fails to be made (`sysfs: cannot create duplicate filename '/devices/virtual/android_usb/android0'`, `-EEXIST`), and the computer sees no storage and no USB Connected prompt follows (seen on a Q2 on 2026-11-15, every other plug-in). `unpark` makes the gadget again (`setup`'s steps) and binds it in the background once stock's stop has unmounted configfs and adbd has written its descriptors again (demo runs stock's stop on its UI thread, which must not wait out adbd). `test/adb.py`'s stand-in configfs refuses a second gadget directory, as the kernel does. A flag file keeps adbd's restart loop from binding over stock's gadget, and a lock serializes the calls.

So with a cable in, the Q2 is a USB drive (or the DAC) exactly as stock makes it, and an adb device once the USB page is left with Return, until the cable comes out. Under Audio and DAC the DAC keeps the controller while the cable is in, so adb needs USB Mode on storage.

Why not one composite storage + adb gadget: it would change the gadget stock's USB storage and its eject handling (`usb_poll`) rely on, adb would come and go with the drive, and while the card is shared the Q2 has unmounted it and its UI sits on USB Connected, which is little use for development. Its own gadget leaves storage and the DAC untouched. Its own USB IDs matter on Windows, which keys its driver choice, and whether it asks for Microsoft OS descriptors at all (the answer is cached), on the vendor, product and `bcdDevice`.

The USB chooser on plug-in ([internals: USB pages](internals.md#usb-pages)) makes **USB Debugging** the way to this gadget. With `q2adb` on the controller when the cable comes, it is the chooser's probe ([internals: USB cable events](internals.md#usb-cable-events)): the computer sees one device, USB Debugging and Charge Only keep it, and only Transfer Files or USB Audio park it for stock's gadget. When it is not back yet (within about two seconds of a cable out after Transfer Files, while adbd restarts), stock's storage gadget probes instead, and its stop hands the controller back to `q2adb`. USB Debugging's page's Stop parks it until the cable comes out; the cable out stops a probe's storage gadget even when the chooser was never answered.

### Authorization

adb's own RSA key authentication is always on: a computer gets nothing, not even a shell prompt, until it signs adbd's random token with a key the Q2 accepts. adbd reads, for every connection:

- `/mnt/data/adb_keys`: computers remembered with **Always Allow**;
- `/mnt/mmc/adb_keys` while the card is mounted: a developer's own, copied there by hand (one `adbkey.pub` line per computer).

A key in neither is offered to the UI as Android's adbd offers it to Android's framework: adbd listens on the Unix socket `/tmp/q2adb-ui` (mode 0600, root's), one client at a time, and writes `PK` followed by the computer's `adbkey.pub` line (base64 key, a space, `user@host`, no line end). The client answers `OK` to let that computer in this once, after appending the line to `/mnt/data/adb_keys` for Always Allow, or `NO`. With no client connected the key is refused at once, and adb shows the device as `unauthorized`. A computer that disconnects before the answer is forgotten; a late `OK` lets nothing in.

The Q2's side of the prompt (`patch/adb_trust.c`, a `--dev` build's; `patch/usb_ui.c` `auth_poll` keeps the socket) is **Allow this computer?** on the [confirmation card](oled-design.md#confirmation-card)'s wide form, over whatever is on screen ([oled-design: adb](oled-design.md#adb)): the laptop glyph and the title, "grayson@MacBook-Pro wants developer access over USB." (the key's comment, as `adb` writes it into `adbkey.pub`), the key's short fingerprint ("Key 3F:9A:1C:E0 … 7B:C2": the first four and the last two bytes of the MD5 of the base64-decoded key, the hash Android's fingerprint uses), the switch **Always allow this computer** (on), then **Deny** and **Allow** side by side, Allow lit. The wheel moves, Centre answers the lit one (or flips the switch), a tap answers either. **Allow** answers `OK`; with the switch on it first appends the key's line to `/mnt/data/adb_keys` and the day to `/mnt/data/adb_keys.added` (12 bytes a computer: the first eight bytes of that MD5, then the time, little-endian), and with it off the computer gets this connection only. The status row then shows "MacBook-Pro allowed" for 2 s (the host after the comment's `@`). **Deny**, Return, or 30 s with no answer answer `NO`, so nothing lets a computer in but a press on the Q2; a question that arrives while another card is up is answered `NO` too (the computer asks again), and adbd going away closes the card unanswered. The card's `adb_keys` is the other way in.

**Settings > General > Developer Options** (a `--dev` build's last row before Restart) holds **Trusted Computers** and how many. That page lists `/mnt/data/adb_keys`'s computers, each by its comment with the day it was added (Today, Yesterday, else "Nov 13"; none for a key added by hand), then Remove All in red. Centre on a computer asks "Remove grayson@MacBook-Pro?" and drops its line and its day; Remove All asks, then deletes both files. A removed computer keeps the connection it has until the cable comes out: adbd reads the keys again at each connection, so the next one asks. The card's own `adb_keys` is the developer's file and is not listed.

### q2eq's requests

`q2eq` writes a request (`q2eq_request`, `patch/q2eq.h`: the change, a band, a value as text, a name, flags for `--now` and `--replace`, and a random number to match its answer) to `/tmp/q2eq.req` through a temporary name, mode 0600. demo's `q2eq_poll` (`patch/peq_ui.c`, compiled into `--dev` builds alone) looks for it every 500 ms from `headset_eq_poll`, after EQ per Output's swap, takes only a regular file owned by root that no one else can read or write, removes it, carries it out, and answers in `/tmp/q2eq.rep` (`Q2EQR01`, then `q2eq_reply`: the message, the saved state, the draft and its name). How it maps onto the editor is in [internals: q2eq requests](internals.md#q2eq-requests). `q2eq` gives up after 10 s and removes its request, so a demo that comes back later does not act on it.

### Tests

`python3 test/q2eq.py` builds `q2eq` for the host and answers it with a fake demo: each command's request, usage, the JSON, the wait and its answer's match. `test/fw/test_q2eq.py` runs demo's side in a `--dev` build of the payload's C. `test/fw/test_adb_trust.py` runs the card, its answers and Trusted Computers in the payload's C. `python3 test/adb.py [BUILD]` runs the MIPS adbd (BUILD's with BUILD's `/lib`, or the one `tools/thirdparty.py` caches with the toolchain's glibc) under qemu-mipsel in a bwrap sandbox, with a stand-in FunctionFS whose `ep1` and `ep2` are FIFOs the test speaks adb's protocol through: the descriptors as the kernel parses them, refusal without a key, the prompt's socket (`PK`, `NO`, `OK`, Always Allow), the card's file, keys read again per connection, a key from `adb keygen` when adb and openssl are installed, `shell:`, `exec:` byte for byte, push and pull, and the refused services. It also runs `q2adb-gadget` and the wrapper under the host's sh against a directory tree standing in for configfs. The gadget on real hardware, Windows' driver binding and the controller hand-over are only tested on a Q2.

## Troubleshooting

- **`adb devices` lists nothing:** the Q2 is still on **USB Connected** (press Return), or USB Mode is **Audio and DAC**. In Device Manager the Q2 should be under Universal Serial Bus devices as "Q2 (ADB)". If it shows with a warning, Windows cached an earlier answer for these IDs: uninstall the device there (with its driver) and plug in again.
- **`unauthorized`:** the key isn't in either `adb_keys`, or the card's file was copied while USB storage had the card and the Q2 hasn't mounted it since (Return does). `adb kill-server` and `adb devices` retries.
- **adb stops answering until a restart:** adbd ended while its gadget was on the controller, which unbinds the gadget; unplug and plug in again, or restart the Q2.
- **Rolling back:** install a release update (or an earlier `--dev` one) from the card as usual; adb leaves nothing behind but `/mnt/data/adb_keys`.
