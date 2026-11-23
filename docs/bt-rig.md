# The Bluetooth receiver rig

A rig for hearing what a Q2 sends over Bluetooth without anyone at the Q2: the development laptop (Windows 11 with WSL) is to act as the Q2's Bluetooth headset, record what it receives silently, drive the Q2 to play, and line the recording up with the Q2's own log. Its first use was to be Smart Crossfade on a real Q2 ([internals: Smart Crossfade](internals.md#smart-crossfade)).

**State (2026-11-23).** Driving the Q2 (coda-rc), the Q2's log over a run, the silent capture path on Windows and the analysis work, and are tested. Receiving does not: Windows will not act as an A2DP sink for a Q2 ([below](#why-windows-will-not-take-the-q2)), so `record` cannot get a stream. A run therefore gives the Q2's log alone, or q2player's own output on the laptop ([Smart Crossfade without the radio](#smart-crossfade-without-the-radio)). The way to real recordings is a second Bluetooth adapter given to WSL ([A USB adapter for WSL](#a-usb-adapter-for-wsl)).

## The pieces

| File | Runs on | Does |
| --- | --- | --- |
| `tools/bt_rig.cs` | Windows, as `btrig.exe` | Bluetooth pairing and services, AudioPlaybackConnection, the capture, the default output's guard, a stdin/stdout bridge to a TCP port |
| `tools/bt_rig.py` | WSL | builds and runs `btrig.exe`; drives the Q2 over the Windows `adb.exe` and coda-rc; `status`, `pair`, `unpair`, `record`, `play`, `ends`, `log`, `analyse`, `calibrate` |
| `tools/bt_rig_analyse.py` | anywhere (Python, ffmpeg for non-WAV files) | song changes in a recording, and the calibration cases |

`btrig.exe` is built from `tools/bt_rig.cs` by the Roslyn compiler of the installed .NET SDK against .NET Framework 4.8 and the Windows SDK's `Windows.winmd`, with nothing fetched: `python3 tools/bt_rig.py build`, or any command that needs it when the source changed. The program and its recordings live in `%LOCALAPPDATA%\q2-bt-rig` (`BTRIG_DIR`), outside the repository. Every `btrig` command prints JSON lines.

The Q2 is reached only through Windows' `adb.exe` (`Q2_ADB`, serial `Q2CODA`; [adb](adb.md)). WSL cannot reach an `adb forward`, which listens on Windows' loopback, and on this laptop has no route to the Q2's Wi-Fi address either, so coda-rc goes through `btrig pipe`: Windows makes the TCP connection to the forwarded port and WSL speaks it over the program's stdin and stdout.

## Commands

```sh
python3 tools/bt_rig.py status                    # endpoints, defaults and sources on Windows; the Q2's settings, BlueZ devices, bluealsa PCMs
python3 tools/bt_rig.py play pair                 # once: the rig as a Car Remote phone, "bt-rig" (code read from the Q2, no press)
python3 tools/bt_rig.py play state                # Now Playing, position, queue place
python3 tools/bt_rig.py play queue 20
python3 tools/bt_rig.py play seek-end 50          # to 50 s before the playing song's end
python3 tools/bt_rig.py play resume|pause|next|previous|seek S|volume V|shuffle on|repeat all|jump I SONG_ID|start NODE [SONG_ID]
python3 tools/bt_rig.py ends 3 --before 50        # three natural ends: seek near each end, let it change, keep the player's log
python3 tools/bt_rig.py log --from 2026-11-23T22:40:00Z --to 2026-11-23T22:45:00Z
python3 tools/bt_rig.py analyse take.wav --ref A.flac --ref B.flac
python3 tools/bt_rig.py calibrate /tmp/cal --codec sbc
python3 tools/bt_rig.py pair | unpair             # the laptop and the Q2, both sides
python3 tools/bt_rig.py record --seconds 120 --out take.wav   # take.json and take.q2.log beside it (needs a sink: see below)
```

`ends` uses a seek, not Next: a manual skip never crossfades, a song left to end does. A Q2 that is stopped refuses coda-rc's Resume ("demo: resume not done"), so `ends` starts the queue's own song with PlayQueueItem. Neither starts anything with no output connected: Headphones Required is held on ([internals](internals.md#no-playback-without-an-output)), and coda-rc answers `UNAVAILABLE: the Q2 did not take it`. Any plug in the 3.5 mm jack, or a Bluetooth headset, is an output.

## Pairing, and what it does to Windows

`pair` has Windows start it (`btrig pair`: WinRT custom pairing, which takes the numeric comparison in the program, with no Windows dialog). On V1UQi the Q2 asks with stock's own dialog, "Bluetooth Code:NNNNNN" with Cancel lit and OK under it, not the firmware's pairing card. Once the laptop is the one device linked to the Q2, the rig turns the wheel one row and taps OK, written as input events ([remote: Input](remote.md#input)), with a screenshot before each; Centre on that dialog did nothing.

A Q2 offers A2DP Sink as well as Source (its Bluetooth receiver), so a computer it pairs with sees a pair of speakers: as soon as the link is up, Windows makes "Speakers (Q2 Muse)" its default output for every role and sends the computer's sound to the Q2, which opens its receiver page. `pair` therefore runs `btrig guard` throughout (each role's default goes back to what it was, within 20 ms) and afterwards turns off the Q2's Audio Sink service on Windows (`btrig service MAC 0000110b-... off`; no administrator rights needed). On the Q2, stock's link thread also takes the paired computer as the saved device: `BTLINKMAC` in `config.ini` becomes the laptop's address.

## Silence

Received audio must not be heard. `btrig receive --route "Steam Streaming Speakers"` first sets this process's own output, as Settings' Volume mixer does per app (`SetPersistedDefaultAudioEndpoint`, which refuses a process with no audio session, so a never-started stream on that endpoint is opened first), to a virtual endpoint that plays nowhere, and records with a process loopback capture of itself, which takes the audio before any endpoint volume. It clears the route at exit. Each second it checks where its own audio plays and stops at once if anywhere else. `btrig selftest --route ...` proves the path with a 1 kHz tone at -6 dBFS: captured at exactly 0.5 peak, nothing on the default output. Two traps found on the way: muting or turning down the process's own session silences the capture too (any session volume, even 0.01, recorded zeros), and Windows keeps an app's session volume and mute from run to run, so `btrig` sets its sessions to full volume and unmuted each time.

That Windows plays a received stream inside the process that opened it is assumed from the API's design and not yet seen: no Q2 stream has opened.

## Why Windows will not take the Q2

AudioPlaybackConnection works on a source's `...\SNK` interface of the Bluetooth A2DP driver, and Windows enables that interface only for some sources. For the owner's Pixel (A2DP Source only) it is enabled; for the Q2 (Source and Sink) it stays disabled, so `TryCreateFromId` fails with `E_NOINTERFACE` (`btrig create ID`). What was tried, 2026-11-23: turning the Q2's Audio Sink service off on Windows and restarting its A2DP device node; the Q2 connecting A2DP to the laptop itself (`org.bluez.Device1.ConnectProfile 0000110b-...` fails with `br-connection-unknown`: Windows offers no sink endpoint until AudioPlaybackConnection has started); pairing again with the Q2's main bluealsa started with `-p a2dp-source` (it still registers the sink role, below); pairing again with the Q2's class of device set to a phone's (`hciconfig hci0 class 0x5a020c`, restored to `0x0c041c` at once). None enabled the interface. As far as these show, Windows treats a device that offers A2DP Sink as speakers and nothing else.

**bluealsa and `-p a2dp-source`.** With `bluealsa -p a2dp-source` (also bare, with no other option) the daemon still exports `/org/bluez/hci0/A2DP/<codec>/sink` beside `.../source`, and BlueZ's `Adapter1.UUIDs` still lists `0000110b`; with no bluealsa running both `0000110a` and `0000110b` go. So on the Q2 this bluealsa registers the sink role whatever `-p` says, and Guest Play's main daemon (`BT_GUEST_SOURCE` in `tools/build.py`) is not source-only.

## A USB adapter for WSL

The way to a working receiver is a second Bluetooth adapter that Windows never uses, given to WSL with usbipd-win, and BlueZ with bluez-alsa there as a plain A2DP sink: the recording is then bluealsa's PCM (`bluealsa -p a2dp-sink`, `arecord -D bluealsa:DEV=<Q2>,PROFILE=a2dp`), after the codec and with no Windows audio at all.

- **Adapter:** a Realtek RTL8761B (BW) one, USB ID `0bda:8771` (TP-Link UB500, ASUS USB-BT500 and others), Bluetooth 5.0 with BR/EDR, handled by Linux's `btusb` with `btrtl` and the `rtl_bt/rtl8761bu_fw.bin` and `_config.bin` firmware from linux-firmware. A CSR8510 A10 (`0a12:0001`) needs no firmware, but most sold are clones that misbehave.
- **Windows:** usbipd-win (an installer, administrator once), then `usbipd bind --busid X` and `usbipd attach --wsl --busid X` for that adapter only; the laptop's own radio stays with Windows.
- **WSL:** Microsoft's WSL kernel is built without Bluetooth, so a kernel of its own with `CONFIG_BT`, `CONFIG_BT_BREDR`, `CONFIG_BT_HCIBTUSB` and `CONFIG_BT_HCIBTUSB_RTL` (`.wslconfig` `kernel=`), then `bluez` and `bluez-alsa-utils`.
- **Pairing:** the Q2 pairs with that adapter as with any headset; nothing changes on Windows.

## Analysis

`bt_rig_analyse.py` (`analyse`) reads a recording and gives: the RMS envelope in 50 ms windows, every silence of 100 ms or more, clipping (samples at 0.999 of full scale or over, and over 1.0 in a float capture), and around each change time given with `--at` the dip. With the songs (`--ref`, in play order) it finds each in the recording by its onset envelope, follows each sample by sample at 8 kHz (clock drift between the Q2 and the laptop included), and fits every 50 ms window as `a*A + b*B` by least squares, so each song's own gain is known where they overlap. A song plays while its gain is within 30 dB of its gain alone (`PRESENT_BELOW`), and a window's gain is read only where the song's own level there is within 25 dB of the recording's (`TRUST_DB`): a song further under the other cannot be told from the fit's error. Per change it reports when B enters (its first 0.5 s of playing), when A was last seen, the overlap (B's entry to A's last window seen), the gap (silent windows between them), when A last read 3 dB and 20 dB under its own gain (`a_fade_from_s`, also as seconds before A's file end), how long B takes to reach its own level, both gains, and how far the fit's residual is under the recording (about 180 dB where the recording is the songs bit for bit, 20 dB through SBC).

What it cannot do: tell samples apart (a lossy codec is in the way); see the part of a fade where the outgoing song is more than 25 dB under the incoming one, so a smart fade, which overlaps the outgoing song's quiet tail by design, reads shorter than its plan by that inaudible part; or tell a crossfade from a gapless change without the songs: the envelope alone shows only dips and silences.

### Calibration

`calibrate` makes two synthetic songs and three known changes: `cut` (gapless), `fade5` (a 5 s equal-power fade, as q2player's) and `gap2` (2 s of silence), resamples each to 48 kHz on a clock 60 ppm fast with 0.37 s of lead and -3 dB of gain, passes it through SBC (328 kbps) or AAC (256 kbps) with ffmpeg, and analyses it. On 2026-11-23: SBC `cut` overlap 0.00 s and gap 0.00 s, `fade5` overlap 4.75 s, `gap2` gap 1.95 s; AAC 0.00, 4.70 and 1.95 s; gains -3.0 dB each. Equal power puts each song's first and last 2 % of a fade under the 30 dB line, hence 4.75 for 5. The check fails when the analysis is wrong: with the presence line at 3 dB instead of 30, `fade5` measured 0.00 s and failed (`test/unit/test_bt_rig_analyse.py` keeps both).

## Smart Crossfade without the radio

q2player plans a smart fade from the two files alone, so its plan for a Q2's songs can be had on this laptop: the host build (`tools/player.py build host`) driven by `test/player/fakedemo.py` as `test/player/run.py`'s `smart_scenario` and `smart_play` drive it, with the songs copied from the card, the word demo writes in `/tmp/q2xfade` and the two paths in `/tmp/q2xfade.next`. Its output, written to files instead of ALSA, is the mix bit for bit, which `analyse` measures. One trap: `smart_scenario` writes 0 to `/tmp/q2xfade` at the first song's exit, as demo writes the next change's word, and demo's word carries its count of stops above `XF_STOPS` (a Q2 that has been stopped once writes `0x19388`, not `0x9388`). Run with such a word, that 0 reads as a stop and the player drops the held tail: the first song is cut at the out point. demo itself always writes the count (`patch/crossfade.c`), so give the harness the word with no stops (`0x9388`). What it cannot show is what only the Q2 does: the analysis's time on the X2000, a song still arriving or not on the card, and the change on Bluetooth.

## What it does not prove

- Anything about the Bluetooth link itself until a stream is received: codec, latency, dropouts.
- A2DP is lossy (SBC or AAC), so nothing is compared sample for sample. The one stream seen between the two, Windows to the Q2's receiver while Windows took the Q2 for speakers, was AAC.
- The log's times are the Q2's clock, read against the laptop's to about half a second (busybox `date` has whole seconds).
