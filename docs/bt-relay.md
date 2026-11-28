# The Bluetooth relay

A test rig for the Q2's Bluetooth audio output that needs no headset: the development laptop is the receiver.
The Q2 plays a known signal over the air, the laptop records what arrives, and a capture taken on the Q2 says
what was negotiated and sent. It is for the work in [bluez](bluez.md), [bluealsa](bluealsa.md) and
[bt-own-plan](bt-own-plan.md), whose steps otherwise need a person and a pair of headphones each time.

Each statement says how it is known: **measured** (run on the laptop or in the unit tests, 2026-11-27, or on a
Q2 running V288i, 2026-11-28), **read** (from documentation or source), or **needs a Q2** (not yet run over the
air: the first run paired and then stopped, see [The first run](#the-first-run)).

## Design

Three receivers, in the order they cost something:

| Receiver                                                               | Needs                                        | Covers                                                                                                                                                                                      |
| ---------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Windows as the A2DP sink** (`tools/btrelay/btrelay.sh`)              | nothing: the laptop's own adapter            | A2DP with the codecs Windows' sink offers (SBC for certain; whatever else it lists shows in the first capture), AVRCP, absolute volume, HFP if Windows takes the hands-free role for the Q2 |
| **The Q2's own capture** (`vamp bt snoop`, `tools/btrelay/btsnoop.py`) | nothing: stock's `hcidump`, pushed to `/tmp` | every codec, with any sink: what each side offered, what was configured, the bit rate, lost packets, every AVRCP and HFP frame, and the encoded stream itself for a decoder off the Q2      |
| **A USB dongle in WSL** (not built)                                    | one USB Bluetooth adapter                    | a sink that offers AAC, aptX, aptX HD and LDAC, and a recording of the decoder's own output                                                                                                 |

**The laptop's adapter** (measured): Intel's "Wireless Dual Bluetooth", a PCI device (`VEN_8086&DEV_E476`), driver
24.70.0.4, on Windows 11 build 26300. `AudioPlaybackConnection`, Windows' A2DP sink (the receiving end of
A2DP, where a headset usually sits), is there: its device selector returns a paired phone as a source, and
Windows already keeps an "A2DP SNK" device for that phone. Because the adapter is PCI and not USB, `usbipd-win`
cannot hand it to WSL: Linux on this laptop gets a radio only from a dongle.

**Codecs** (read): Windows' A2DP sink is reported to offer SBC alone. Whether this build offers more is settled by
the first capture, whose `peer offers:` line is Windows' answer to the Q2's AVDTP Get Capabilities (needs a Q2).
The other direction is measured: Windows as an A2DP source to the Q2's sink role negotiated AAC at 48 kHz.
A codec the sink does not offer cannot be streamed to it, so AAC, aptX, aptX HD and LDAC over the air need the
dongle, or a headset with the Q2's own capture running.

**The dongle tier** (read, not built). WSL's kernel here (6.18.58.1) has `CONFIG_BT=m`, `btusb` with Realtek
and Broadcom support (not MediaTek) and `vhci_hcd` (measured: `/proc/config.gz`, `modinfo btusb`), so a dongle
passed in with `usbipd-win` is a real `hci0` to BlueZ. What the owner would need: one Realtek RTL8761B dongle
(TP-Link UB500 is one; about 15 dollars), `winget install usbipd`, and in WSL `bluez`, `linux-firmware` for
the dongle's patch file, and bluez-alsa built with AAC (fdk-aac), aptX and aptX HD (libfreeaptx) and LDAC
decoding (libldacdec, the open decoder; Sony's library only encodes). `arecord -D bluealsa` then gives the
decoder's output sample for sample. Build this when a codec has to be heard decoded by something other than
ffmpeg.

**What is checked, and where:**

| What                                    | Selected by                                                                                                                 | Checked by                                                                                                                                  |
| --------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| A2DP codec                              | `vamp bt codec NAME` (bluealsa's `SelectCodec`, through `q2bt-codec`)                                                       | `btsnoop.py --expect-codec NAME`: the configuration the sink accepted                                                                       |
| SBC bitpool, AAC bit rate, LDAC quality | the Q2's Quality page, or the daemon's options in `patch/bt_launch.sh` (`--sbc-quality`, `--aac-bitrate`, `--ldac-quality`) | `btsnoop.py`: the bitpool or bit rate configured, and the stream's measured kbit/s                                                          |
| The audio                               | `vamp bt play`: the test signal at the stream's rate                                                                        | `wavcheck.py` on the laptop's recording ([Checking the audio](#checking-the-audio))                                                         |
| Lost packets                            |                                                                                                                             | `btsnoop.py`: gaps in the RTP sequence numbers; `wavcheck.py`: dropouts                                                                     |
| AVRCP from the sink                     | `btrelay.sh sink --send pause@5,play@8`                                                                                     | `btsnoop.py`: `peer key pause pressed`, and the Q2's answer                                                                                 |
| AVRCP from the Q2, absolute volume      | the Q2's keys and wheel (`vamp key`, `vamp wheel`)                                                                          | `btsnoop.py`: `Q2 set absolute volume N`, `changed playback status paused`; `btrelay.sh sink` prints Windows' media session if it makes one |
| HFP and HSP                             | they connect with the device when the other side takes the role                                                             | `btsnoop.py`: the AT lines (`AT+BRSF`, `+CIEV`, `AT+IPHONEACCEV` for a battery level)                                                       |

**Owner-side requirements.** None to buy for the first two receivers. One pairing between the laptop and the Q2,
which `btrelay.sh pair` and `btrelay.sh unpair` make and remove; no existing device is touched
([What it changes](#what-it-changes)). While a sink is open the Q2's audio comes out of the laptop's speakers or
headphones, as from any app.

## On the laptop

`tools/btrelay/btrelay.sh VERB` runs from WSL. It copies `tools/btrelay/win/` to `C:\Users\Public\vamp-btrelay`
(`BTRELAY_DIR` for another folder), builds `btrelay.exe` there when the source changed, and runs it. The build
uses the C# compiler and the WinRT metadata that ship in Windows, so nothing is installed (measured).

```sh
tools/btrelay/btrelay.sh probe                      # adapter and its address, sources, output format: reads only
tools/btrelay/btrelay.sh pair AA:BB:CC:DD:EE:FF     # the Q2's address; the Q2 must be discoverable
tools/btrelay/btrelay.sh sink AA:BB:CC:DD:EE:FF --seconds 40 --wav run.wav --send pause@30
tools/btrelay/btrelay.sh capture --seconds 5 --wav room.wav   # the recording alone
tools/btrelay/btrelay.sh unpair AA:BB:CC:DD:EE:FF
```

- **`probe`** changes nothing (measured): the adapter, each paired device that can play to this computer, the
  default output's mix format (here 48 kHz, 2 channels, 32-bit float) and Windows' media sessions.
- **`pair`** watches for the unpaired Q2 (which runs an inquiry, the scan for nearby devices), pairs with it
  and accepts the confirmation itself, printing the code. It notes the address in `paired.txt` beside the exe
  (measured: Paired 37 s after the start, 30 s of it the inquiry; Windows asked ConfirmPinMatch and the Q2
  accepted with no card on its screen). Windows then connects to the Q2 as an A2DP source at once, because the
  Q2 also offers an A2DP sink ([The first run](#the-first-run)).
- **`sink`** opens `AudioPlaybackConnection` for that device: `StartAsync` lets the Q2 connect, `OpenAsync`
  connects to it. For `--seconds` it prints the sink's state changes and any media session that appears, sends
  `--send`'s keys at their times, and with `--wav` records. Closing the sink ends the stream (needs a Q2).
- **The recording** is WASAPI loopback (Windows' tap on what an output device is playing) of the default
  output, in the output's own mix format, written from start to stop with silence where nothing played
  (measured with nothing playing: a 3 s run gives a 2.7 s file of silence; with sound, needs a Q2). It holds
  everything Windows plays in that time, so keep other sound off during a run. It is taken before the
  endpoint's mute and volume (measured: the test signal played on a muted output at volume 24 recorded at
  -6.0 dBFS and passed `wavcheck.py`), so a run can keep the laptop silent: mute the output first.
- **`--send`** goes only to a media session that appeared after the sink opened. If Windows makes none for a
  Bluetooth source, nothing is sent: a media key would reach whatever the owner is playing instead.
- **`unpair`** removes only an address in `paired.txt`; any other is refused (measured).

### What it changes

| Command                            | Changes                                                                                                                                                                                             | Undo                                                                                                                                                                                                                             |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `btrelay.sh pair ADDR`             | adds the Q2 to Windows' paired devices; the Q2 gains the laptop in its own list; Windows makes a speaker for the Q2 and makes it the default output; the Q2's `[SYSSET] BTLINKMAC` names the laptop | `btrelay.sh unpair ADDR`, and `vamp bt forget LAPTOP-ADDR` on the Q2 (the speaker goes and the old default output returns); `BTLINKMAC` put back by hand, in `config.ini` and in demo's memory ([The first run](#the-first-run)) |
| `btrelay.sh sink`                  | a Bluetooth audio link for its run; sound on the default output                                                                                                                                     | it closes the link when it ends                                                                                                                                                                                                  |
| `vamp bt discoverable on`          | the Q2 answers inquiries for the time given                                                                                                                                                         | `vamp bt discoverable off`, or the time runs out                                                                                                                                                                                 |
| `vamp bt connect`, `codec`, `play` | the Q2's link to the laptop and its codec; nothing stored                                                                                                                                           | `vamp bt disconnect ADDR`; the next connection negotiates afresh                                                                                                                                                                 |
| `vamp bt snoop install`, `start`   | files in the Q2's `/tmp` (RAM)                                                                                                                                                                      | gone at the next start; `vamp bt snoop stop` ends the capture                                                                                                                                                                    |

`probe`, `capture`, `vamp bt status` and a bare `vamp bt codec` change nothing.

## On the Q2

`vamp bt` ([adb: vamp](adb.md#vamp)) is `tools/btrelay/q2side.py`: each command is shell text run over adb,
using what the image has (`dbus-send`, `hciconfig`, `hcitool`, `aplay`, `q2bt-codec`) and stock's `hcidump`.

```sh
vamp bt status                           # adapter, daemons (ours or stock's), links, bluealsa's PCMs
vamp bt discoverable on 120              # so the laptop's pair finds it; off to end early
vamp bt connect AA:BB:CC:DD:EE:FF        # the laptop's address (btrelay.sh probe prints it); disconnect, forget
vamp bt codec                            # the stream's codec and rate, and the codecs the sink offers
vamp bt codec aptX-HD                    # select one: SBC, AAC, aptX, aptX-HD, LDAC
vamp bt play                             # the test signal through bluealsa; or: vamp bt play song.wav
vamp bt snoop install "$B"               # stock's hcidump from a build's stock.squashfs to /tmp/hcidump
vamp bt snoop start                      # capture to /tmp/btrelay.snoop
vamp bt snoop stop run.snoop             # end it and pull it
```

- **`codec NAME`** asks bluealsa for `PCM1.SelectCodec` on the A2DP source PCM with `q2bt-codec`, the program
  the link monitor's step-down uses ([internals](internals.md#bluetooth-link-monitor)), so it works with
  stock's daemon and ours. A codec the sink does not offer is refused, with bluealsa's words.
- **`play`** pushes a WAV to `/tmp/btrelay.wav` and plays it with `aplay` through the ALSA PCM q2player uses
  ([bluealsa: The callers](bluealsa.md#the-callers)). bluealsa gives that PCM to one client, so pause the Q2
  first; `aplay` says "busy" otherwise. The signal is made at the rate the stream has.
- **`snoop`** needs `hcidump`, which every build leaves out ([building](building.md)): `install` takes it from
  a build directory's `stock.squashfs` with `unsquashfs` and pushes it. The capture is in RAM, about 3 MB a
  minute at SBC's highest rate, so stop it within a few minutes.
- **`connect`**, **`disconnect`** and **`forget`** are bluetoothd's `Device1.Connect` and `Disconnect` and
  `Adapter1.RemoveDevice` for that one address.

None of it has run on a Q2 (needs a Q2); the unit tests check the text each command sends.

## The Q2's own capture

`tools/btrelay/btsnoop.py CAPTURE` reads a btsnoop file (`hcidump -w`, or `btmon -w`: datalinks 1001, 1002 and 2001) and follows each L2CAP channel from its Connection Request: AVDTP's signalling and media, AVCTP, RFCOMM.
"Sent" is always the Q2.

```
peer offers: SBC
Q2 offers: nothing seen
configured: SBC 44100 Hz joint stereo, 16 blocks, 8 subbands, loudness, bitpool 2-53 (set by the Q2)
media: 2068 packets sent in 30.0 s, 328 kbit/s, 0 lost
avrcp    4.512 peer key pause pressed
avrcp    4.513 Q2   accepted key pause pressed
```

- `--expect-codec NAME` exits 1 unless that codec was configured and accepted: the check a script uses.
- `--extract FILE` writes the stream's codec frames and names the decoder: SBC as `ffmpeg -f sbc` reads it,
  AAC with a LOAS header on each LATM packet (`-f loas`), aptX and aptX HD raw (`-f aptx`, `-f aptx_hd`), LDAC's
  frames for libldacdec. Decoding what the Q2 sent, off the Q2, is the codec-level check that needs no second
  radio: it shows the encoder's output whatever the sink did with it.
- `--json` prints everything, for a script.
- A capture begun after the stream started has no configuration; the media is still found by its RTP header.
- Not read: AVDTP signalling split across packets (a sink with very long capabilities), and AVRCP's browsing
  channel.

The parsers are checked against captures the tests build from the formats' layouts (measured); against a real
capture, needs a Q2. Extraction is checked for SBC's framing only; the other codecs' is from their
specifications and unrun.

## Checking the audio

`tools/btrelay/btsignal.py OUT.wav` writes the test signal (8.5 s, stereo): 4 s of a 1000 Hz tone on the left
and 1500 Hz on the right at -6 dBFS, then 3 s of white noise. `tools/btrelay/wavcheck.py CAPTURE.wav` finds it
in a recording and measures:

| Measure                                  | Meaning                                                                               | Fails when                                    |
| ---------------------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------- |
| tone frequency, per channel              | the right signal, the right channel, the right speed                                  | off by over 0.5%, or the channels are swapped |
| SINAD (signal over noise and distortion) | the tone's power against everything else in the band, through a 16384-point transform | under `--min-sinad` (40 dB unless given)      |
| bandwidth                                | where the noise falls 12 dB under its 1 to 5 kHz level: the band the codec kept       | under `--min-bandwidth`, when given           |
| dropouts                                 | stretches of the tone more than 20 dB down, in 2 ms steps                             | any                                           |
| bits used                                | the container's bits less the low bits that are always zero                           | reported, never a failure                     |

The bit-depth figure is honest only for a recording nothing has resampled. Windows' loopback is the mixer's
output (float, at the output's rate), so there it reads 32; the place to see 16 against 24 bits is a decode of
the Q2's own capture, or the dongle tier's `arecord`. SBC and AAC decode to 16 bits whatever went in.

What a pass looks like per codec (the numbers to expect, to be replaced by measured ones): SBC at bitpool 53
keeps about 19 to 20 kHz and at bitpool 35 about 16 kHz; AAC at 240 kbit/s about 17 to 20 kHz; aptX 20 kHz;
LDAC at 990 kbit/s the full band of a 96 kHz stream.

Both are standard library Python, so they run on the laptop, the MacBook or a CI runner.

## Procedure

Run from a checkout: the Q2's commands on the computer that has adb to it (`vamp` below is
`tools/mac/q2env python3 tools/vamp.py`), the laptop's in WSL on the laptop. `Q2` and `PC` are the two
addresses (`vamp bt status`, `btrelay.sh probe`). Tell the owner before step 2: it is the one step that adds
to their paired devices.

| #   | Where      | Step                                                                                                                                       | Pass                                                                                                                                                      |
| --- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | laptop     | `btrelay.sh probe`                                                                                                                         | an adapter with classic support; a loopback format                                                                                                        |
| 2   | Q2, laptop | `vamp bt discoverable on 120`, then `btrelay.sh pair Q2`                                                                                   | `pairing: Paired` (no card on the Q2, measured); then Windows connects to the Q2 as a speaker: see [The first run](#the-first-run)                        |
| 3   | Q2         | `vamp bt snoop install "$B"`, `vamp bt snoop start`                                                                                        | `capturing to /tmp/btrelay.snoop`                                                                                                                         |
| 4   | laptop     | `btrelay.sh sink Q2 --seconds 40 --wav run.wav --send pause@30` (in the background)                                                        | `sink open: Success`                                                                                                                                      |
| 5   | Q2         | `vamp bt status`; if no PCM, `vamp bt connect PC`                                                                                          | a `pcm A2DP-source sink` line                                                                                                                             |
| 6   | Q2         | `vamp bt codec SBC`, then `vamp bt play`                                                                                                   | the signal plays; it is heard on the laptop                                                                                                               |
| 7   | Q2         | once step 4 has ended: `vamp bt snoop stop run.snoop`                                                                                      | a file                                                                                                                                                    |
| 8   | anywhere   | `python3 tools/btrelay/btsnoop.py run.snoop --expect-codec SBC --extract run.sbc`                                                          | `configured: SBC ...`, 0 lost, `peer offers:` (write Windows' list into [Design](#design)), and the `peer key pause` line if step 4 found a media session |
| 9   | laptop     | `python3 tools/btrelay/wavcheck.py /mnt/c/Users/Public/vamp-btrelay/run.wav`                                                               | `PASS`                                                                                                                                                    |
| 10  | anywhere   | `ffmpeg -f sbc -i run.sbc sent.wav && python3 tools/btrelay/wavcheck.py sent.wav`                                                          | `PASS`: what the Q2 encoded, apart from the air and Windows                                                                                               |
| 11  |            | steps 3 to 10 again for each codec `peer offers:` lists, each Quality setting, and `BLUEZ=1`, `BLUEALSA=1` ([bt-own-plan](bt-own-plan.md)) | the codec and rate asked for, each time                                                                                                                   |
| 12  | both       | when the rig is no longer wanted: `btrelay.sh unpair Q2`, `vamp bt forget PC`                                                              | `unpairing: Unpaired`                                                                                                                                     |

Still to settle over the air (needs a Q2): which codecs Windows' sink offers; whether Windows makes a media
session for a Bluetooth source (without one, AVRCP from the sink cannot be driven from the laptop, and the
Q2's side of AVRCP is still read from the capture); and the Q2's own `vamp bt` commands beyond `status`,
`discoverable` and `forget`.

### The first run

2026-11-28, a Q2 running V288i (stock's bluetoothd and bluealsa), driven over the laptop's `adb.exe`. Step 2
ran and the run stopped there, because the pairing changed the owner's settings on both sides (measured):

- **Pairing.** Windows-led pairing raises no card on the Q2: Windows asked ConfirmPinMatch, `btrelay pair`
  accepted it, and the Q2 accepted by itself. Paired 37 s after the start.
- **Windows takes the Q2 as a speaker.** The Q2 offers an A2DP sink (its "Playing from Phone" mode), and
  within seconds of pairing Windows connected as the A2DP source: `vamp bt status` showed `pcm A2DP-sink
source: AAC 48000 Hz` and the Q2's screen "Playing from Phone, AAC". Windows made a render endpoint for the
  Q2 and made it the default output for all three roles (console, multimedia, communications), unmuted at
  volume 100. So the laptop's own sound would go to the Q2, and a loopback recording would record the Q2's
  endpoint, not the laptop's.
- **The Q2's preferred device changed.** Stock wrote the laptop into `[SYSSET] BTLINKMAC` in
  `/mnt/data/config.ini` and into demo's copy (`0xa4c0d8`), so boot and Bluetooth on would page the laptop
  instead of the last headset ([internals](internals.md#bluetooth-boot-reconnect)). `vamp bt forget` does not
  clear it (only the Q2's own Forget does, and only while the device is listed), so the undo is to write the
  old value back to both.
- **No hands-free.** Windows added the Q2's AVRCP target and controller services and no HFP or HSP entry.
- After `btrelay.sh unpair` and `vamp bt forget`, and `BTLINKMAC` written back, the Q2's paired list,
  pairing store, `bta2dpfile`, `BTLINKMAC`, the laptop's Bluetooth devices and its audio endpoints hashed
  equal to before the run.

The relay direction (Q2 as source, Windows as sink) therefore needs the Q2's sink role kept out of the
way first, an owner's choice: start bluealsa for the run with the A2DP source profile only (a restart of
bluealsa), turn off the Q2's audio sink service in Windows' device properties after pairing (whether that
needs admin is not known), or let the default output move for the run, then put it and `BTLINKMAC` back.

## Tests

`test/unit/test_btrelay_snoop.py` (captures built packet by packet: three capture formats, frames split across
ACL packets, each codec's configuration, a refused configuration, AVRCP, HFP, extraction),
`test/unit/test_btrelay_wav.py` (the signal through quantizing, a low-pass, swapped channels, clipping, a cut,
a wrong rate, silence) and `test/unit/test_btrelay_q2.py` (the shell text of every `vamp bt` command against a
recording stand-in, with bluealsa's answers from `test/bt-fixtures/`). `btrelay.exe` has no automated test: it
is checked by hand with `probe`, `capture`, and the refusals of `unpair` and `sink` for an unknown address.
