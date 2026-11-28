# USB audio: hearing the Q2 on a computer

Two ways to hear on a computer what the Q2 plays, both in `--dev` builds only, both beside adb:

- **[The tap](#the-tap)** streams exactly what the player hands the DAC, bit for bit, over adb (USB or Wi-Fi), and [`vamp audio`](#vamp-audio) writes it as WAV or plays it. For debugging: the samples, their rate and format, every take-back.
- **[USB audio](#usb-audio)** makes the Q2 a USB sound card too: the computer sees a microphone, "Q2 Output", whose recording is the Q2's output, and adb keeps working on the same cable. For listening: Windows' "Listen to this device" plays it on the computer's speakers.

| What | Where |
| --- | --- |
| The tap, in the player | `player/tap.c` (`pcm_tap_put`, `pcm_tap_back`, the FIFO's thread), its calls in `player/output.c` (`sink`, `rewind_by`, `hold`, `output_idle_end`, `output_drop`, `output_close`) |
| Its reader | `tools/q2tap.py` (`Tap`, `Wav`, `sinks`), `tools/vamp.py` (`audio`) |
| The gadget's UAC2 function | `patch/adb_gadget.sh` (`audio_function`, `audio_set`, `watch`) |
| The player's output in the gadget | `player/uac.c` |
| Tests | `test/player/run.py` `test_tap_*` and `test_uac_*`, `test/unit/test_q2tap.py`, `test/adb.py` (the gadget's audio checks) |

## The tap

The player copies every write to the device into the tap (`player/output.c` `sink`, on every way: the DAC, Bluetooth, a USB DAC, the car), and every take-back: a rewind (a click mixed into frames the device already holds, `output_click`), or a drop of what the device held unheard (a stop, a seek, a pause held for a click). `player/tap.c` hands that to whoever reads the FIFO `/tmp/q2tap.pcm`.

**It costs nothing when off.** The tap's threads exist only where `/usr/bin/q2adb-gadget` is (a `--dev` build): a release build's player looks once, at its start (`test_tap_off_without_the_gate`: no open of the FIFO in a whole run). In a `--dev` build with no reader, the FIFO's thread tries one open every 500 ms, refused (`ENXIO`) while nobody has the FIFO open to read; the playback thread's cost is a load of one word per write.

**It never holds the player.** With a reader, the playback thread copies each write into a ring of 1 MB (about 3 s of 16-bit stereo at 44.1 kHz, 0.7 s at 192 kHz and 32 bits), lock-free: one producer, one consumer, no system call. A thread of its own writes the ring to the FIFO, and waits on the reader if it must. A reader that falls behind fills the FIFO (64 kB), then the ring; what does not fit is dropped, counted, and said in the stream (`Q2TL`), never waited for (`test_tap_reader_behind_drops_and_never_holds_the_player`: a reader that stops for 3 s, the player's 5 s of real time still 5.5 s, about 800,000 frames dropped and said, the rest bit for bit; a tap made to wait fails it, 7.4 s). A reader that leaves (`EPIPE`) frees the FIFO, and the next one starts with the format (`test_tap_a_new_reader_starts_with_the_format`).

**The stream** is records: an 8-byte header (a tag and the payload's length, little-endian) and the payload.

| Tag | Payload | Meaning |
| --- | --- | --- |
| `Q2TF` | rate (u32), channels (u16), bytes a sample (u8), kind (u8: 0 integer, 1 float, 2 DoP, 3 native DSD) | the format of the frames after it; first in each reader's stream, and again at each change |
| `Q2TD` | frames | as written to the device |
| `Q2TB` | frames (u32) | taken back unheard; the frames after it replace them |
| `Q2TL` | frames (u32) | dropped here because the reader fell behind |

A reader that holds the newest second back and applies each `Q2TB` to it (`tools/q2tap.py` `Tap`) has the device's own stream: `test_tap_is_the_pcm` (two FLACs gapless, 1,764,000 frames) and `test_tap_takes_back_what_a_click_rewound` (three clicks mixed in by rewinds, 63,000 frames taken back) are the file sink's PCM bit for bit; dropped frames are silence, so a WAV keeps time.

The format is the device's: what the DAC was opened at (for a 16-bit FLAC, 16-bit at the song's rate; a 24-bit one as 32-bit), after the volume where the player applies it in software (Bluetooth with Link Volume off, a USB DAC with software volume). The DAC's own hardware volume is after the tap, so on the headphone way the tap is at full level.

## vamp audio

```sh
vamp audio --out capture.wav --seconds 30   # 30 s of the device's audio into a WAV; Ctrl-C ends it sooner
vamp audio                                  # played live through ffplay
vamp audio --out capture.wav --play         # both
vamp audio --adb adb.exe --out capture.wav  # through Google's adb: a Q2 on the cable (vamp itself speaks TCP only)
python3 tools/q2tap.py saved.tap out.wav    # a stream saved with: adb exec-out 'cat /tmp/q2tap.pcm' > saved.tap
```

On the Q2 it runs `[ -p /tmp/q2tap.pcm ] || { rm -f /tmp/q2tap.pcm; mkfifo /tmp/q2tap.pcm; }; exec cat /tmp/q2tap.pcm` (`vamp.py` `TAP_READ`), which is all a reader needs; nothing comes while nothing plays. Each format is a WAV of its own (`capture.wav`, then `capture-2.wav`...), PCM or IEEE float, said as it opens (`capture.wav: 44100 Hz, 2 ch, 16-bit integer`), and the end says the frames, the dropped and the taken back. Live play is held a second behind (the take-backs) and needs `ffplay` on the computer. Over Wi-Fi, 16-bit stereo at 44.1 kHz is 176 kB/s and 32-bit at 192 kHz is 1.5 MB/s; a link slower than the stream drops, and says so.

## USB audio

`q2adb-gadget audio on` adds a UAC2 function to adb's gadget, `q2adb`, rather than making a second gadget (this kernel refuses a second gadget directory: `EEXIST` as `android_usb/android0` is taken, `ENOMEM` as measured on 2026-11-27). The function is Shanling's `f_uac2`, built into the kernel (stock's USB DAC mode uses it the other way round, the computer playing to the Q2); `/sys/module` has no `usb_f_uac2` because it is not a module. Its `u_audio` is newer than Linux 5.10's: a function directory has `p_srate` taking a list, `fb_max`, `p_hs_bint`, terminal types and `function_name` (read on the owner's Q2, 2026-11-27).

| Attribute | Value | Why |
| --- | --- | --- |
| `p_chmask`, `c_chmask` | 3, 0 | the Q2 the source, stereo; nothing from the computer, so no feedback endpoint |
| `p_srate` | 44100,48000,88200,96000,176400,192000 | the computer picks one; the player resamples to it ([below](#the-player-s-output-in-the-gadget)) |
| `p_ssize` | 4 | 32-bit samples: a 24-bit source whole |
| `p_hs_bint` | 1 | one packet a microframe at high speed |
| `req_number` | 8 | dwc2's descriptor DMA sends an empty packet in two with one request and in three with two ([iphone-mode: Empty packets](iphone-mode.md#empty-packets)) |
| `p_mute_present`, `p_volume_present` | 0 | the computer's level is its own |
| `function_name`, `if_ctrl_name` | Q2 Output | the name Windows shows, "Microphone (Q2 Output)"; Shanling's default `function_name` is "Source/Sink" |

With audio the gadget is another device to Windows, which keys its drivers on vendor, product and `bcdDevice`: `1d6b:0104` (the Linux Foundation's Multifunction Composite Gadget), `bcdDevice` `0x0225`, device class `EF/02/01` (an interface association descriptor, which `f_uac2` has, groups the audio interfaces), so Windows' composite driver gives adb's interface to WinUSB (its Microsoft OS descriptor, as before) and the audio interfaces to `usbaudio2.sys` (Windows 10 1703 and later). Without audio it is `1d6b:0105` as ever. The controller's FIFOs fit both: adb's bulk IN needs 512 bytes, the isochronous IN at most 200 a microframe (192 kHz, 32-bit stereo), and the transmit FIFOs are 64, 64, 64, 512, 1,024, 1,024, 2,048 and 3,072 bytes (debugfs `fifo`, 2026-11-27).

```sh
q2adb-gadget audio try 120   # on for 120 s under a watchdog, not remembered
q2adb-gadget audio keep      # within the 120 s, over the adb that came back: stays on, and remembered
q2adb-gadget audio rehearse 120   # the gadget made again as it was (no audio), same watchdog; keep only stands it down
q2adb-gadget audio on        # on, remembered for every boot (/mnt/data/q2adb.audio)
q2adb-gadget audio off       # adb alone, forgotten
```

Each makes the gadget again: USB adb drops for the seconds adbd takes to come back (adb over Wi-Fi, `adbd --tcp`, is not stopped). Each returns once its work has detached: the gadget's remake ends the USB adbd, and with it the shell that ran the command, whose end sends `SIGHUP` to its session; the work ignores `SIGHUP` from before its fork and says when it is in a session of its own (`detach`). The first try on the owner's Q2 (2026-11-27) died that way, before `setsid`, and did nothing; `test/adb.py` runs `audio try` from a shell that sends its own process group `SIGHUP` as it ends, which fails on that version and passes now.

**The watchdog** of a `try` is started before the gadget is touched, as a process of its own: unless `keep` comes within S seconds, it sets adb alone again (given 30 s), and if USB adb is still not back 30 s after that (FunctionFS's `ep1` there and the gadget on the controller; the `UDC` file read in a child, since a gadget call stuck in the kernel holds every read of it), it restarts the Q2, which makes the gadget as remembered: a try is never remembered. Every step, and on each failure what each adbd and gadget call is doing (`/proc/PID/stat`'s state and `wchan`) and the kernel's last 30 lines, goes to `/mnt/data/vamp-usb-audio.log`, synced, so it is there after a restart.

**The gadget's removal** stops the USB adbd alone (its command line is `/usr/bin/adbd` with no argument), waits for it to end (3 s, then `SIGKILL`), and stops if FunctionFS cannot be unmounted or the directory is still there: a new `ffs.adb` function under an old FunctionFS mount would get no descriptors (adbd writes them to the old mount, whose function is gone), and the gadget would never bind. The removal before 2026-11-27 sent `killall adbd`, which also ended adb over Wi-Fi, and unmounted at once, before adbd had ended.

**What broke on 2026-11-27, and what is not known.** The second try (run by hand, detached) dropped USB adb, which never came back, through and after the 300 s revert; Windows saw no `1d6b:0104` device; over Wi-Fi a TCP connection was accepted but no `adb shell` was answered; the Q2 needed a restart, and no log survived (`/tmp`). What the code says: that version's removal sent `killall adbd`, ending both adbds, and unmounted FunctionFS without waiting for adbd to end; an unmount refused while adbd still held it leaves the remade gadget's `ffs.adb` without descriptors, which matches USB adb never returning and the revert, which removes and makes the gadget the same way, not saving it. It does not explain the Wi-Fi adbd: `adbd --tcp` opens no FunctionFS (`tools/adbd.patch`: with `--tcp` only `local_init` runs) and `q2adb-wifi` starts it again within 10 s; a TCP connection accepted with nothing answering is a listening socket whose process is not running its loop, which a process stuck in the kernel would be (a bind that oopsed holding the gadget's lock, as the UAC1 module's first binds did, [iphone-mode: The UAC1 module](iphone-mode.md#the-uac1-module), is one such way). Neither is shown. The next try's log says which: `rehearse` first (the remake alone), then `try`.

**At the desk (2026-11-27, V282i, the script run from `/tmp`):** `rehearse 60` made the gadget again as it was, bound in about 2.5 s, USB adb back; `try 90` made `1d6b:0104` with `ffs.adb` and `uac2.q2`, bound, USB adb back at once, the card `1 [UAC2Gadget]: UAC2_Gadget`, and Windows 11 listed "ADB Interface" (WinUSB), "USB Composite Device" and "Source/Sink" (MEDIA), as a capture device "Microphone (Source/Sink)"; the watchdog went back to adb alone at 90 s with adb unharmed. Windows opened the capture at 44.1 kHz, 16-bit stereo (its shared-mode default); a 48 kHz tone played into the card arrived at -33 dB peak where it was -12 dBFS: played at another rate than the computer's. [The desk test](#the-desk-test) plays at the computer's rate.

<a id="the-desk-test"></a>
### The desk test

`tools/uac_tone.sh` runs on the Q2 with audio on: it waits for the computer to start recording, takes the rate the computer chose from the kernel's `srate=` line (stock's root filesystem has no `amixer` to read the card's `Playback Rate` control), and plays `/tmp/NAME-RATE.wav` (NAME `tone` unless named) into the card at that rate twice, 2 s apart, sampling the PCM's status (state, `hw_ptr`, `appl_ptr`, `avail`) every 100 ms while each plays, then logs the kernel's lines since it started, all to `/mnt/data/vamp-usb-audio.log`. Tones, S32 stereo at -12 dBFS: `ffmpeg -f lavfi -i "aevalsrc=0.25*sin(2*PI*1000*t)|0.25*sin(2*PI*1000*t):s=48000:d=10" -c:a pcm_s32le tone-48000.wav` (lavfi's `sine` source is at 1/8 of full scale, so `sine` with `volume=-12dB` is -33 dBFS). A clean test is a steady 1 kHz at -12 dBFS through each play in the computer's recording. `Q2ADB_UAC_SRATE`, `Q2ADB_UAC_SSIZE` and `Q2ADB_UAC_REQ` in a try's environment change the function's rates, sample size and requests, to compare them.

**The second desk run (2026-11-27):** the computer recorded at 48 kHz (the kernel's line). The recording held the tone for its first 83 ms only (about 4,000 frames at 48 kHz: eight of `aplay`'s 512-frame periods), at the level and pitch played, then digital silence (one step of dither) to the end, and a 70-step blip as the play ended. So the computer's gain is unity and the samples are packed right (the tone's -33 dBFS arrived at -33 dB); what is wrong is that the stream stops carrying the PCM after its first periods. **The third (2026-11-27):** the card took the frames at the computer's pace through both plays (`hw_ptr` 48,200 a second, `RUNNING`, then `DRAINING` to its end; `aplay` 0 both times), the kernel logged one stream start before the first play and nothing during them, and the computer heard each play's tone for its first 187 and 137 ms only, at -12.1 dBFS as played, then digital silence. So the PCM is consumed while the packets carry zeros after the start of each open. The device reports the audio interface as "Q2 Output" (Windows' bus-reported description); "Source/Sink" is the name Windows gave the endpoint at its first install and keeps for the same device instance. The next run plays a frame counter (`count-48000.wav`: each frame's number, modulo 32,768, in the top 16 bits; `uac_tone.sh 60 count`) recorded at 48 kHz, which says which of the file's frames arrived, and compares a single rate and two requests (`Q2ADB_UAC_SRATE=48000`, `Q2ADB_UAC_REQ=2`). Windows still names the device "Microphone (Source/Sink)" with `function_name` set: either Windows keeps the name it gave the endpoint first (its device instance is the same, vendor, product and serial), or Shanling's `f_uac2` does not use the attribute; the string the device reports (Windows' bus-reported description of the audio interface) tells which.

### Listening on Windows

1. With audio on and the cable in, Windows lists a new recording device, "Q2 Output" (Settings > System > Sound > Input, or `mmsys.cpl` > Recording).
2. Its Properties > Advanced > Default Format is the rate the Q2 is asked for (48 kHz unless changed; the player resamples to it).
3. Properties > Listen > **Listen to this device**, playback through the computer's speakers or headphones. Or record it in any program that records a microphone.

macOS lists it in Audio MIDI Setup as an input, and Linux as an ALSA capture card (`arecord -l`).

<a id="the-player-s-output-in-the-gadget"></a>
## The player's output in the gadget

`player/uac.c` is the tap's second consumer. While `/tmp/q2adb.audio` is there (the gadget made with its UAC2 function), its thread opens the gadget card's playback PCM (`hw:CARD=UAC2Gadget,DEV=0`, 32-bit if it takes it, else 16-bit, stereo) at the rate the computer chose (the card's `Playback Rate` control, looked at every 500 ms; a change opens it again), and plays the tap's stream into it while the DAC plays as ever:

- **Held half a second**, as the device's own buffer is (`BUFFER_US`), so take-backs are applied before the frames go and the computer hears them about when the headphones do; with nothing more for half a second (a song's end, a pause) the held frames go at the card's pace.
- **Converted** to float stereo (a mono source to both channels, more channels than two: the first two), resampled with libswresample to the card's rate when it differs, and written as 32-bit: a 16-bit source at the card's rate arrives exact, each sample in the top 16 bits (`test_uac_sink_is_the_song`); at 48 kHz a 44.1 kHz song arrives as 48/44.1 as many frames (`test_uac_sink_resamples_to_the_computer_s_rate`). DSD is silence.
- **Two clocks.** The DAC's crystal paces what comes in, the computer's USB frames what goes out, so the card's buffer (at most 300 ms) drifts: past it the oldest goes down to 60 ms, and empty, a period of silence goes. Each is a click, counted in the session's closing line in the player's log (`usb audio: the card closed: N frames, N short periods, N frames dropped`). ponytail: a resampler that followed the buffer's level, as `q2dacloop`'s pitch control does the other way (`docs/dacloop.md` on `feat/vamp-os-base`), would never click.
- **Never the playback thread's**: the tap drops what this thread does not take, so a computer that stops recording (the PCM takes nothing for 100 ms) costs the player nothing, and what was waiting is thrown away so the next recording starts current.

On a host build the card is a file (`Q2PLAYER_UAC`, `Q2PLAYER_UAC_RATE`), paced by the clock.

## Tests

- `python3 test/player/run.py -k test_tap` and `-k test_uac`: the tap bit for bit, its take-backs, a slow reader, a second reader, off without the gate; the card given the song exactly, resampled, and nothing without the flag. The tap's first four fail on the player before it (no stream); the slow reader fails on a tap that waits for room.
- `python3 -m pytest test/unit/test_q2tap.py`: the reader on streams built as `tap.c` writes them (a record cut anywhere, take-backs inside and past the hold, drops, formats, WAVs), and `vamp audio` over the fake adbd and through Google's adb.
- `python3 test/adb.py`: `q2adb-gadget audio` on a stand-in configfs: the composite gadget's IDs, class and attributes, setup, park and unpark with audio remembered, off, the USB adbd stopped and adb over Wi-Fi not, a try through its shell's `SIGHUP`, its watchdog's revert, keep, rehearse, and the restart when USB adb does not come back.

## What is missing

- On a Q2: the sound clean at the computer's rate ([the desk test](#the-desk-test)), and whether the card has the `Playback Rate` control `player/uac.c` follows (without it the sink opens at 48 kHz). The gadget binds and Windows takes it (above). The tap has not run on a Q2.
- A setting in the UI: audio is a command (`q2adb-gadget audio`), from adb.
