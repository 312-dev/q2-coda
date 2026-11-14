# The new player: design and plan

`q2player` replaces stock's `hciplayer`: built from source in `player/`, on current libraries, speaking the socket protocol `demo` already uses, with Q2 Pod's player fixes written as code where they are binary patches today. [player.md](player.md) describes the program it replaces and is the specification for everything below; read it first.

**The rule of scope: parity.** Whatever stock's player does that demo or a user relies on, the new one does: every format, DSD in all three modes, SACD ISO, QQ Music, CUE, gapless, crossfade, the EQs, ReplayGain, every output, every stream source, and the socket byte for byte as far as demo reads it. demo is not changed. A difference from stock is allowed only when it is in [Deviations](#deviations), and the plan is finished when every row of the [Parity checklist](#parity-checklist) is verified.

## Library stack

| Part | Choice | Size | Replaces |
| --- | --- | --- | --- |
| Demuxing and decoding | FFmpeg 7.1.5 (`libavformat`, `libavcodec`, `libavutil`), static, only the components in `tools/player.py` (`DEMUXERS`, `DECODERS`, `PARSERS`), compiled as C alone | the whole `q2player` is 2,794,792 bytes stripped (phase 2), 1,638,400 in the root filesystem's squashfs (LZO, 128 KB blocks) at phase 1's 2,788,456 | hciplayer's static FFmpeg 3.0, libmad, MPlayer's demuxers: 7,708,808 bytes patched, 4,251,648 in squashfs |
| Rate conversion | `libswresample`, in the same build; only Bluetooth uses it | 124,946 bytes of archive, inside the figure above | MPlayer's `lavcresample` |
| Output | stock's own `libasound.so.2` (alsa-lib 1.1.6), linked dynamically | 0 | the same library |
| TLS for `https` | mbedTLS 3.6.7 as `tools/thirdparty.py` (branch `sec/libs`) builds it, static, through FFmpeg's `tls` protocol | adds about 416,000 bytes (measured on a build with `Q2_PLAYER_TLS=1`: 3,203,560 against 2,787,512 without), 221,184 in squashfs | hciplayer's static OpenSSL 1.0.1 |
| QQ Music | stock's `libQMDecryption.so`, loaded with `dlopen` when a URL needs it | 0 | the same library |
| SACD ISO | stock's `libsacd.so` (sacd-ripper's libsacdread and DST decoder, which demo needs too), loaded with `dlopen` | 0 | the copy of the same code compiled into hciplayer |

The root filesystem holds 50,688,000 bytes and this branch's `--dev` build uses 48,836,608, so 1,851,392 are free. While both players ship (phases 2 to 6) the build grows by `q2player`'s 1,638,400: phase 2's `--dev --new-player` build measured 50,479,104 bytes, 208,896 to spare, and the build stops with the overshoot named past 50,688,000. With TLS it is 1,859,584 and does not fit, by 8,192 bytes: phase 5 builds with TLS for its tests, and a `--dev` image carries it only once something else has made room or phase 7 has removed hciplayer. Once hciplayer goes (phase 7) the build is about 2.6 MB smaller than today, and `libmad.so.0.2.1` (95,152 bytes; only hciplayer needs it) goes too.

**Why FFmpeg and not one decoder library per format.** The list of formats is long (about twenty codecs and a dozen containers) and stock already answers it with FFmpeg, so FFmpeg alone gives parity in one dependency, one licence (LGPL 2.1 or later for everything chosen; GPL 3 takes it) and one place to follow security releases. The price is paid once: FFmpeg's decoders are not the fastest for FLAC or MP3 on a CPU without assembly, and its float decoders need care to be exact (below). Dedicated libraries (libFLAC, minimp3, libopus) would cost a demuxer and a build recipe each, and still leave APE, WMA, ALAC, DTS, TAK and DSD to FFmpeg.

**MPEG audio uses FFmpeg's integer decoder** (`mp3`, `mp2`, `mp1`), not its float one: it puts out 16 bits, as stock's libmad does, so MP3 keeps the output format stock opens for it, and its arithmetic is integers alone.

**Why 7.1.** It is a maintained release branch (7.1.5 is its newest release, pinned by SHA-256 in `tools/player.py`; check the release's signature against the FFmpeg key in `docs/third-party.md` when `sec/libs` merges) and the Q2's toolchain, GCC 9.3, builds it without a patch. 8.x and 9.x were not tried with that compiler; a later phase can, and moving is a change to one tuple and a rerun of the exactness tests.

**Compiler flags.** The toolchain and flags are `tools/thirdparty.py`'s, unchanged: Codescape GNU Tools 2020.06-01, `-EL -march=mips32r2 -mabi=32 -mhard-float -mfp64 -mnan=2008 -mabs=2008`, which gives stock's ELF flags (`0x70001405`, checked on the built binary) and loads against stock's glibc 2.28. The X2000 has two XBurst II cores at 1.2 GHz with an FPU; the kernel is built with MSA support, which does not say the CPU has it ([player.md: Open questions](player.md#open-questions)). The plan does not depend on MSA: FFmpeg is configured `--disable-asm`, so the same C runs on the Q2 and on the machine that makes the reference decodes, and integer and IEEE float arithmetic give the same samples on both. That is what makes "bit-exact" testable without a device. If phase 6 measures too little headroom, FFmpeg's MIPS FPU and MSA code can be turned on per decoder, each one checked against the C output first.

**CPU.** No Q2 was available, so the only measurement is relative: both players under the same `qemu-mipsel` user-mode emulator, playing the same files in real time, by the CPU time the emulator used (`# cpu` in each transcript).

| Played in real time | stock hciplayer | Q2 Pod's hciplayer | q2player |
| --- | --- | --- | --- |
| 16-bit 44.1 kHz FLAC, two files, 40 s | 0.45 s | 0.21 s | 0.33 s |
| 24-bit 96 kHz FLAC, 15 s | 0.43 s | 0.18 s | 0.25 s |
| MP3 (LAME V2), two files, 40 s | 0.50 s | 0.36 s | 0.72 s |
| nothing, for 4 s (start-up alone) | not measured | not measured | 0.02 s |

Emulated time is not device time, the tick is 10 ms, and each figure is under 2% of one emulated core, so this ranks nothing finely. What it shows: FLAC costs the new player less than stock and more than Q2 Pod's player (which sleeps where stock polls); MP3 costs it more than either, 1.4 times stock's libmad, which is the one decoder to look at first on a device; and none of the three cases is near a limit with no assembly at all. Device figures are phase 6's first task; the budget to meet there is stock's own load at each rate plus no more than a fifth, and under half of one core at 384 kHz.

**RAM.** 128 MB, no swap. The player's own buffers are small: FFmpeg's probe and packet buffers (about 1 MB), one decoded block, a preloaded second file's context, and the ALSA ring (500 ms; 384 kHz stereo S32 is 1.5 MB). Crossfade's hold is the large one, 6 MB today, and stays that. Streams take the cache demo asks for (512 KB to 4 MB). Phase 6 measures resident size against hciplayer's.

## Architecture

```
demo --- /tmp/sock_play ---> command thread (ipc.c) ---> ctl (requests, settings, status; one mutex)
            ^ answers, events                                  | wake
            |                                                  v
            +------------------------------------- playback thread (engine.c)
                                                    source -> DSP chain -> output
                                   preload thread: opens the next source while the current one plays
```

- **One process, three threads**, as stock: commands, playback, and a preload thread started per `set-gapless`. The command thread never blocks on the playback thread: queries are answered from `ctl` under its mutex, and everything else sets a request and wakes the playback thread. That is also why a query between two songs cannot fault ([internals: Music info between songs](internals.md#music-info-between-songs)).
- **Idle and paused, the playback thread sleeps on a condition variable.** No polling, so the player costs nothing while stopped ([internals: Battery](internals.md#battery)).
- **`source` (source.c)** is one file or stream: `libavformat` demuxes, `libavcodec` decodes, and the result is interleaved PCM in the width the output is opened with. Encoder delay and padding are cut by FFmpeg from the container's own data. A seek goes to the packet at or before the target and drops samples up to it, so it lands on the sample asked for.
- **Custom I/O** sits under `libavformat` for the two vendor formats: QQ Music is an `AVIOContext` whose read decrypts each block by its offset through `libQMDecryption.so`'s three calls; an SACD image is opened through `libsacd.so` and its tracks presented as DSD packets. Both libraries are loaded with `dlopen` on first use, so a root filesystem without them still plays everything else.
- **DSP chain**, in this order, each stage absent when its setting is off: ReplayGain, PEQ or 3EQ (`patch/peq.c`, Q2 Pod's own engine, compiled into the player as it is into the payload), crossfade (hold and equal-power mix), Mono Audio, the visualizer's tap, software volume (Bluetooth, and USB with `mcl-softvolflag`), rate conversion (Bluetooth). With every stage off the decoder's integers go to the output untouched: that path has no float in it and is the bit-exact one. With any stage on, samples are float from the first stage to a single conversion at the end, which saturates. DSD in DoP or Native mode bypasses the chain, as stock.
- **`output` (output.c)** opens ALSA as stock does: the device by output way and hardware type, the file's rate with ALSA's resampling off, 500 ms in 16 periods. It stays open across a change when rate, channels and width are the same, on every output. It owns the DAC's side too (`/dev/shanling_dac`: PCM or DSD mode per song, mute before a drop or reopen, unmute and the 90-100 ms wait before sound) so the waits Q2 Pod patches in are simply the order of calls.
- **State on disk** is what the patched player reads today, in the same files and formats, so the payload's UI needs no change: `/mnt/data/peq-active` (`Q2PEQ03`), `/tmp/q2xfade`, the visualizer's shared tap, `/tmp/q2mute`.

**The fallback.** While both players ship (`tools/build.py --dev --new-player`), `/usr/bin/hciplayer` is `q2player` and Q2 Pod's patched hciplayer is `/usr/lib/hciplayer-stock/hciplayer`. `q2player`'s first act (`player/main.c`) is to look for `/mnt/data/q2player`: without that file it `execv`s that hciplayer with its own arguments, so `argv[0]` stays `hciplayer`, and since the file is named `hciplayer` too, so does the kernel's process name (`comm`). demo's `nice … hciplayer -ao alsa &` and `killall -9 hciplayer` work on either. Off by default until phase 7; `touch /mnt/data/q2player` over `q2remote` and a restart of the player turns it on, removing the file turns it off. If the hciplayer cannot be run, `q2player` plays. Phase 7 inverts the test and then removes it. The directory, not a name like `/usr/bin/hciplayer-stock`, is so that `killall` finds the process by `comm` alone: BusyBox 1.31's `killall` also matches `argv[0]`, which would do on a Q2, but under qemu-user every process's command line starts with the emulator's path, so only `comm` can be checked there, and it is the one that does not depend on how `execv` was called.

## Phases

Each is sized for one agent. "Emulator" means `fakedemo.py --emu` with the MIPS binary in the sandbox described in [player.md: Capturing](player.md#capturing); "host" means `python3 test/player/run.py`.

### 1. Prototype (done)

`player/` (`ipc.c`, `engine.c`, `source.c`, `output.c`), `tools/player.py`, `test/player/`. Speaks the protocol for play, pause, seek, skip, stop, preload and every query; decodes through FFmpeg; writes ALSA. Verified: [Phase 1 results](#phase-1-results).

### 2. Local files on the DAC, behind the switch (done)

Verified: [Phase 2 results](#phase-2-results), with what is left in the checklist's rows 15, 20, 21 and 23.


- **Goal:** everything a Q2 does with files on the card through the headphone outputs, at parity, selectable on a device.
- **Work:** the preload thread (open off the playback thread; a preload cancelled or outrun is closed, never played); `ss=` starts and CUE tracks; every format in [player.md: Formats](player.md#formats) with its `CODEC` name and `BITS`; `LENGTH` and `BITRATE` as stock computes them; the `ready` period after a start and after a preloaded change; no two `exit`s within 300 ms; errors (`/tmp/notfile`, `/tmp/notsupport`, a bad next file does not stop the song playing); an MP3's frame count ends it and trailing tags are not decoded; `/dev/shanling_dac` mode, mute and unmute sequencing in `output.c`; `mcl-orderstop`; the fallback above; `tools/build.py` installs `q2player` and, beside it, the patched hciplayer in a `--dev` build (flag `--new-player`), the build fails with a clear message when the image would pass 50,688,000 bytes.
- **Files:** `player/*.c`, `tools/player.py`, `tools/build.py` (one install step, one flag), `test/player/`.
- **Host tests:** one exactness test per format against the reference decode, with a gapless pair for each lossy format (FFmpeg-made and iTunes-style M4A, LAME and FFmpeg MP3, Opus, Vorbis: the pairs `test/player_seam.py` generates); a seek test (the PCM after a seek to second N is the reference from frame N × rate); protocol fixtures for every scenario in `fakedemo.py`.
- **Emulator:** every fixture scenario with the MIPS binary; the fallback (with and without the switch file, `killall -9 hciplayer` ends either); real demo driving it, once the emulator's library scan works ([Risks](#risks)).
- **Needs a device:** nothing to finish; a first listen is welcome.
- **Exit:** checklist rows 1-24.

### 3. DSP

- **Goal:** the EQs, crossfade, ReplayGain, Mono Audio, the visualizer tap and the meters.
- **Work:** build `patch/peq.c` into the player (it already has a host build, `PEQ_HOST`); port the filter logic of `patch/peq_player.c` (carry across a gapless change, crossfade's hold and mix, mono, the tap) from MPlayer filter callbacks to chain stages, keeping its constants (`XF_LEAD_S`, `XF_COLD_NS`, `XF_STALE_NS`, `CARRY_STALE_NS`); ReplayGain from FFmpeg's `AV_PKT_DATA_REPLAYGAIN` side data, all four modes of `mcl-replaygain`; `mcl-seteq`'s stock ten-band path is not kept (see [Deviations](#deviations)); `mcl-getuvmeter` and `mcl-getspectrum`.
- **Files:** `player/dsp_*.c`, `patch/peq.c` and `peq.h` (shared, untouched if possible), `test/player/`.
- **Host tests:** `test/peq.py`'s checks run against the player's chain (carry, hold, mix length, equal power, seek while holding, stop drops the tail); bypass is bit-exact; ReplayGain's gain per tag form (ID3v2.3, 2.4 UTF-8, Vorbis comment, MP4).
- **Emulator:** crossfade and EQ scenarios with `/mnt/data/peq-active` and `/tmp/q2xfade` written as the payload writes them.
- **Needs a device:** the meters' scale against stock's (an open question).
- **Exit:** rows 25-34.

### 4. Outputs and DSD

- **Goal:** USB DAC, Bluetooth, and DSD in D2P, DoP and Native.
- **Work:** `hw:2,0` with the 500 ms buffer and software volume; `plug:bluealsa` with rate conversion to the transport's rate through `libswresample`, its state kept across a gapless change; the PCM kept open across a change on both; an output-way change while playing; DSF and DFF demuxed by FFmpeg, D2P through FFmpeg's DSD decoders, DoP packed in the player, Native written in ALSA's DSD formats; DSD's `RATE` in `media-info`.
- **Files:** `player/output.c`, `player/dsd.c`, `test/player/`.
- **Host tests:** DoP frames carry the file's DSD bits exactly (unpack and compare); a tone split across two files resampled 44.1 to 48 and 96 to 48 kHz equals one file resampled (`test/bt_resample.py`'s check); volume scaling.
- **Emulator:** the shim logs the device, format and rate each scenario opens; compare with stock's log for the same scenario.
- **Needs a device:** a USB DAC and a Bluetooth headset for every row; DSD formats per output.
- **Exit:** rows 35-46.

### 5. Streams, QQ Music, SACD

- **Goal:** every URL source and the two vendor formats.
- **Work:** FFmpeg's `http` with reconnects, and `https` through mbedTLS with the server's name and chain checked against the CA file the payload already ships for scrobbling (`/etc/scrobble-ca.pem`, or a wider bundle if Tidal's or a DLNA server's root is missing: measure first); the cache (`C` KB, start at `P` percent, `get_cache_buffer`'s fill); a stream that stalls or ends early; QQ Music's `AVIOContext` over `libQMDecryption.so`; SACD through `libsacd.so`; DLNA and Tidal queues' cold changes.
- **Files:** `player/stream.c`, `player/qq.c`, `player/sacd.c`, `tools/player.py` (TLS on), `test/player/`.
- **Host tests:** a local HTTP server: range requests, a stall, a dropped connection, a redirect; TLS against a local server with a good chain, a wrong name and a self-signed certificate (the last two must fail); QQ's reader against a stub library with the same three exports and a known XOR; SACD against a stub.
- **Emulator:** the QQ reader with stock's real library and a key file (it loads and decrypts or reports a bad key); an ISO if one is at hand.
- **Needs a device:** Tidal, a DLNA server, QQ Music, a real SACD image.
- **Exit:** rows 47-56.

### 6. On a Q2

- **Goal:** the answers the emulator cannot give, and the timing behaviours.
- **Work:** every row of [player.md: Open questions](player.md#open-questions); CPU and resident memory against hciplayer at 44.1, 96, 192 and 384 kHz and for each lossy codec; the wired start (no cut first fraction of a second after a press, a seek, a resume), the unpause (nothing skipped), a reopen's drain, the mute line's timing, all by recording the headphone output; a day of ordinary use on the switch.
- **Needs a device:** all of it. Brief the tester with `tools/q2remote.py` commands; the agent reads logs and recordings.
- **Exit:** rows 57-64, and no checklist row above still marked "device".

### 7. Switch over

- **Goal:** `q2player` is the player; hciplayer and its patches are gone.
- **Work:** invert then remove the fallback; stop installing hciplayer; delete `patch_player` and the player lists from `tools/peq.py`, `patch/peq_player.c`, `mp3_gapless.c`, `lavf_gapless.c`, `flac_*.c`, `tools/flac.py` and the Unicorn player suites (`test/hci_*.py`, `player_seam.py`, `xfade_player.py`, `mp3_gapless_player.py`), keeping `patch/peq.c`; drop `libmad`; update `docs/architecture.md`, `source-map.md`, `third-party.md`, `rebasing.md` (the player's sites leave `stock_sites.tsv`), `internals.md`'s player sections (what the new player does, not what was patched), `changelog.md`.
- **Exit:** the whole checklist verified, `tools/scan.py` shows hciplayer's FFmpeg 3.0 and OpenSSL 1.0.1 findings gone, the root filesystem is smaller than before phase 2.

## Testing

- **Exactness (host).** The reference for a file is the system `ffmpeg` with its assembly off (`-cpuflags 0`) writing raw PCM: an independent build of the same C arithmetic. The player's output must be those bytes: for lossless formats that is the audio itself; for MP3 it is the integer decoder's 16 bits; for float decoders it is the decoder's float output, unconverted. A pair of files cut from one signal, played through a preloaded change, must be the two references joined, in one PCM open. `test/player/run.py` has every format `music.py` can make, and a pair for each lossy encoder ([Phase 2 results](#phase-2-results)). For a lossy file the reference is cut to its source's length where ffmpeg leaves an encoder's padding in (M4A).
- **Gapless counts.** The same tests fail on one frame more or less: a 20 s file is 882,000 frames, a pair 1,764,000.
- **Protocol (host).** `test/player/fixtures/` holds transcripts of stock's and Q2 Pod's hciplayer answering `fakedemo.py`'s scenarios under qemu. For each, `q2player` is sent the same commands at the same times and must give the same events and query answers in the same order, the same `media-info` fields (bitrate within 2%), and positions within 2 s at each second sampled. A fixture the new player is meant to differ from is listed in `KNOWN` with its deviation.
- **Emulator.** The MIPS binary in the stock root filesystem under qemu, against stock's ALSA library, driven by the same scenarios; its PCM, captured at `snd_pcm_writei`, is compared with the host reference.
- **None of it needs the stock ZIP**, except the emulator runs, which need an unpacked root filesystem.

### Phase 1 results

Measured on 2026-11-13 with the tree as it is at this document's commit.

**On this machine** (`python3 test/player/run.py`, x86-64, the player writing files in place of ALSA):

- Two 20 s 16-bit 44.1 kHz FLACs through a preloaded change: 1,764,000 frames in one PCM open, byte for byte the two reference decodes joined; and those two references joined are the WAV the files were cut from.
- Two LAME V2 MP3s the same way: 1,764,000 frames in one PCM open (882,000 a file: LAME's delay and padding are gone), byte for byte the reference decodes joined.
- A 15 s 24-bit 96 kHz FLAC: 1,440,000 S32 frames, byte for byte the reference.
- A 24-bit FLAC, an M4A and an Ogg Vorbis file open the output as S32, float and float, which is what stock asks ALSA for with the same files.
- Protocol: 20 of the 22 fixtures (11 scenarios, each answered by stock's hciplayer and by Q2 Pod's) give the same events and query answers in the same order, the same `media-info`, and positions within 2 s. The other two are stock ending a FLAC after a seek, which Q2 Pod fixes (`KNOWN` in `run.py`).
- Each kind of check was seen to fail first: output scaled by 32767 in place of 32768 failed the MP3 comparison at frame 24 (with the float decoder then in use); `player-nextstatus` sent as `player-stopstatus` failed `pod-skip`; and the cut test drops five frames from a copy on every run and requires that to show.

**In the emulator** (the MIPS binary, 2,788,456 bytes with stock's ELF flags `0x70001405`, under `qemu-mipsel` in the stock root filesystem, linked to stock's `libasound.so.2`; PCM captured at `snd_pcm_writei`):

- The FLAC pair: 1,764,000 frames, one PCM open, no difference from the reference made on this machine.
- The MP3 pair: 1,764,000 frames, one PCM open, no difference.
- The 24-bit 96 kHz FLAC: 1,440,000 frames as `S32_LE`, no difference.
- It asks ALSA for what stock asks, by the shim's log of both: `default`; `S32_LE` at 96,000 Hz, then `FLOAT_LE` at 44,100 Hz twice in the formats scenario; `S16_LE` at 44,100 Hz for 16-bit FLAC and MP3.
- Its transcripts for all 11 scenarios have the fixtures' events and `media-info` (20 of 20 compared).
- demo itself, the `--dev` build's, started it with its own `nice -n -5 hciplayer -ao alsa &` (the binary bound over `/usr/bin/hciplayer`); it connected to demo's socket and received demo's boot commands, `{mcl-dsdmode\0}` and `{mcl-replaygain\0}`.

**What phase 1 leaves out**, all of it phase 2 or later: the preload on its own thread; `ss=` starts; exactness for any format but FLAC and MP3 (AAC and Vorbis play and report as stock does, unchecked sample for sample); every DSP stage, so `mcl-seteq`, `mcl-replaygain` and `mcl-setvol` are stored and change nothing; USB, Bluetooth and DSD; URLs, QQ Music and SACD; the DAC's ioctls; the meters' scale (`mcl-getuvmeter` answers a root mean square, `mcl-getspectrum` zeros); the fallback switch and the build's install step.

### Phase 2 results

Measured on 2026-11-13 with the tree as it is at this document's commit.

**On this machine** (`python3 test/player/run.py`: 24 tests and 31 fixtures pass; 9 fixtures are `KNOWN`, each with its reason):

- Every lossless format bit for bit, played whole one after another through preloads: WAV at 16, 24 and 32 bits and float, AIFF at 16, 24 and 32, ALAC at 16 and 24, WavPack at 16, 24 and 32, FLAC at 24: 13 files, 3,439,800 frames, 9 PCM opens (one per run of the same rate and width).
- Every lossy format the same way, against the system ffmpeg's decode with its assembly off: MP2, ADTS AAC, Opus, WMA 1 and 2, AC-3, DTS: 7 files, 1,878,016 frames, all equal.
- A gapless pair per lossy encoder, 20 s each, through a preloaded change in one PCM open, each file exactly its source's length and its reference's samples: FFmpeg's AAC in M4A (edit list and a short last packet), iTunes' shape (whole frames and iTunSMPB), MP3 from FFmpeg's LAME, Vorbis: 1,764,000 frames each; Opus: 1,920,000. FFmpeg 7.1 leaves M4A's padding in (688 frames a file here: the decoder gives the last packet's whole frame), so `source.c` cuts the last packet to its duration when no packet follows, as Q2 Pod's player cuts it.
- An MP3 with 3 MB of junk after its frames: FFmpeg then distrusts the Xing count and decodes the junk; `source.c` reads the count itself and stops there, since no frames chain after it: 882,000 frames, the reference's, and `LENGTH` 20.
- A start at 7 s: 573,300 frames, the reference from frame 308,700. A seek to 9 s: the reference from frame 396,900 to the end. A CUE sheet's three tracks of one file by `ss=` and `set-nexturl`: each from its start, the last to the end.
- A preload that is not audio: the song plays to its end, then `exit`, `error` and `/tmp/notsupport`, as Q2 Pod's (`fixtures/pod-badnext.txt`). A preload cancelled by `mcl-closegapless`, after it opened and while its open was blocked (a FIFO): none of it plays. A preload the song outran: `exit` at the song's end, then the file once it opened. A preloaded file with no audio: its `exit` 500 ms after the one before.
- `media-info` for 25 files of every format against Q2 Pod's: the same `RATE`, `LENGTH`, `CODEC` and `BITS` and `BITRATE` within 2%, but for the two deviations listed (WavPack's 24 bits, DTS).
- Each new test was seen to fail: the M4A cut removed (both M4A pairs), the MP3 count ignored (the junk played, 479 frames of it), one bit dropped from 32-bit samples (lossless), float scaled by 0.99999 (lossy), `AC2` renamed, the bad preload's reopen removed (no `error`), the cancel flag never set and `mcl-closegapless` ignored (the cancelled preload played), a seek one frame late (seek, start, CUE), the outrun preload cancelled, and the exit hold and the end's drain removed together (one exit seen). The hold alone removed does not fail it: the PCM's drain at a song's end already spaces the two by 500 ms.

**In the emulator** (the MIPS binary, 2,794,792 bytes, in the stock root filesystem under qemu):

- All 31 fixtures it is held to: the same events and `media-info`, and positions within 2 s.
- The FLAC, MP3, 24-bit FLAC, MP3-with-junk and every lossless and integer lossy case bit for bit, PCM captured at `snd_pcm_writei`. AAC, Opus and WMA are not: 96 of 532,480 samples (ADTS AAC), 3,042 of 576,000 (Opus) and about 5,000 of 528,384 (WMA) differ from this machine's decode, each by one unit in the last place (at most 3e-8), and Vorbis is equal. The cause is not established; see [Risks](#risks).
- The DAC's ioctls in stock's order (`fixtures`' shim logs): `0x1b` and `0x0a` at each song, mute before a PCM opens or drops, unmute after; a resume unmutes, waits 130 ms, then `snd_pcm_pause(0)`; a skip mutes and drops and keeps the PCM, as the deviation says.
- The fallback, `--stock` and `--killall 5`: without `/mnt/data/q2player` the process is `/usr/lib/hciplayer-stock/hciplayer` (its `comm` `hciplayer`), with it `/usr/bin/hciplayer`; demo's `killall -9 hciplayer` ends either (exit 0, the socket closes at 5.2 s).
- demo itself, from a `--dev --new-player` build with the switch file made, driven through its screens: Folder lists the card (the shim's `readdir` fix), a pick plays, Gapless's `set-gapless` changes to the next song in the same PCM, Next sends `set-nexturl`, Play pauses and resumes. What reached the PCM was the two FLACs bit for bit up to the Next (1,485,648 frames), then the MP3 from its first frame to its last (882,000) through the pause, and on into the next.
- The `--dev --new-player` root filesystem: 50,479,104 bytes. With the room lowered by 300,000 the build stops: `Repacked rootfs is 50,479,104 bytes, 91,104 over the 50,388,000 the stock image holds (--new-player adds q2player beside hciplayer: make room or build without it)`.

**Not done in phase 2:** APE, TAK, WMA Lossless and WMA Pro have no encoder here and no file to hand, so nothing tested them; nor an MP3 whose gapless numbers are only an iTunSMPB comment (FFmpeg reads it, 529 samples off Q2 Pod's reading, [internals](internals.md#mp3-gapless-playback)); nor 32-bit FLAC. Q2 Pod's unpause rewrite (`ps_ring`, [internals: Unpause](internals.md#unpause)) is not ported: a resume unmutes, waits and resumes, so the ring's skip on a Q2 is stock's until it is (row 60). Nor its cold-route stay-open, where a natural start that demo marked keeps the PCM at the same format: q2player drains and closes at a song's end with nothing preloaded, as stock does.

## Parity checklist

One row per behaviour. "How" is the test that proves it; "device" means it cannot be closed without a Q2. Status: **done** (verified, by phase 1 or 2 as the results say), or the phase that owns it.

| # | Behaviour (stock or Q2 Pod's patched player) | How it is verified | Status |
| --- | --- | --- | --- |
| 1 | Connects to `/tmp/sock_play`; exits when demo closes it; started as `hciplayer -ao alsa` | emulator: demo itself starts it by that name and its boot commands arrive | done |
| 2 | Parses `{name\arg}`, several per packet; `%0x7d` in a path | host: fixtures; a path with `}` | done: fixtures (demo's boot pair in one packet), `pod-brace` and `stock-brace` |
| 3 | `player-status`: `ready` until sound, then `playing`, also while paused | fixtures `transport`, `pause-seek` | done: `ready` until the song's first frame is heard, from the PCM's delay (a deviation: stock's ready is its own opening time); position 0 until then, as stock's |
| 4 | `media-info` fields and `no-playing-status` | fixtures, every scenario | done: every fixture, and `test_media_info_per_format` for 25 files |
| 5 | `CODEC` names and `BITS` per format | fixture `formats`, extended to every format | done for every format there is a file of (`formats-all`, against Q2 Pod's); APE, TAK, WMA Lossless and WMA Pro untested (no encoder) |
| 6 | `set-songurl` starts a song; `ss=` start | fixture `transport`; a start-offset test | done: `test_start_offset` exact, fixture `pod-start` |
| 7 | `set-nexturl` cuts and answers `player-nextstatus\exit` | fixture `skip` | done |
| 8 | `mcl-stop` answers `player-stopstatus\exit`, nothing when idle | fixtures `transport`, `idle` | done |
| 9 | A song's end answers `player-status\exit` once | fixture `end` | done |
| 10 | Missing and unplayable files: `error`, `/tmp/notfile`, `/tmp/notsupport` | fixture `errors` plus the two files | done |
| 11 | Pause and resume; position stands still | fixture `transport` | done |
| 12 | Seek, playing and paused; lands on the second asked | fixtures; host seek exactness | done: fixtures, `test_seek_exact` |
| 13 | A FLAC seek near the end plays to the end | fixture `pod-transport` | done (fixtures `pod-seek`, `pod-start` too) |
| 14 | Preload: the next song follows in the same PCM, no sample added or lost (FLAC) | host and emulator exactness | done |
| 15 | MP3 delay and padding cut (LAME, Info, iTunSMPB) | host and emulator exactness (LAME) | done for LAME's and FFmpeg's tags, host and emulator; the frame count's end with junk after it (`test_mp3_tail_not_played`); iTunSMPB-only MP3 open: needs an iTunes-encoded file |
| 16 | AAC in M4A, Opus, Vorbis delay and padding cut | host exactness per pair | done: four pairs exact on the host; in the emulator Vorbis exact, AAC and Opus to the last unit in the last place ([Risks](#risks)) |
| 17 | `mcl-closegapless`; a preload cancelled or outrun is not played | host: cancel during open | done: `test_preload_cancelled`, `_while_opening`, `test_preload_outrun` |
| 18 | A bad next file leaves the current song playing; then `error` | host: junk as the preload | done: `test_bad_preload`, fixture `pod-badnext`; two `exit`s 300 ms apart at least: `test_exits_apart`, `pod-emptynext` |
| 19 | CUE: tracks of one file by `ss=`, cut by demo with `set-nexturl` | host: three starts in one FLAC | done: `test_cue_tracks`, fixture `pod-cue` |
| 20 | Every lossless format bit-exact: FLAC, ALAC, WAV, AIFF, APE, WavPack, TAK, WMA Lossless, at 16, 24 and 32 bits | host exactness per format | done for FLAC (16, 24), WAV, AIFF, WavPack (16, 24, 32), ALAC (16, 24), float WAV, host and emulator; APE, TAK, WMA Lossless, 32-bit FLAC and ALAC: no file to test (blocked) |
| 21 | Every lossy format decodes: MP3, MP2, AAC (M4A, ADTS), Vorbis, Opus, WMA, AC-3, DTS | host exactness per format | done: all exact on the host; in the emulator MP3, MP2, AC-3, DTS, Vorbis exact, AAC, Opus and WMA to one unit in the last place; WMA Pro untested |
| 22 | ALSA on the DAC: `default`, the file's rate, no resampling, 500 ms in 16 periods; `S16_LE`, `S32_LE` or `FLOAT_LE` by decoder | emulator shim log against stock's | done: the shim's log for every format; 24-bit WAV and AIFF differ by design (a deviation); the buffer granted is a device question |
| 23 | The DAC's mode, mute and unmute around start, seek, pause, reopen | emulator: ioctl order in the shim log; device: a recording | done in the emulator (stock's order, Q2 Pod's 130 ms on a press, seek and resume); the unpause rewrite is not ported (row 60); a recording: 6 |
| 24 | The fallback switch; `killall -9 hciplayer` | emulator | done: with and without `/mnt/data/q2player`, `killall -9 hciplayer` ends either |
| 25 | PEQ: bands, scopes, preamp, Auto, balance, ramps, from `/mnt/data/peq-active` | `test/peq.py` on the player's chain | 3 |
| 26 | 3EQ | the same | 3 |
| 27 | EQ per Output (the payload swaps the file; the player rereads it) | host: file swap while playing | 3 |
| 28 | EQ memory carried across a gapless change | `carry_check` | 3 |
| 29 | Crossfade: hold, equal-power mix, other rate and channels, stop drops the tail | `test/peq.py`, `test/xfade_player.py`'s cases | 3 |
| 30 | Mono Audio | host | 3 |
| 31 | The visualizer's tap | host: the tap file's frames | 3 |
| 32 | ReplayGain, track and album, every tag form | host | 3 |
| 33 | `mcl-getuvmeter`, `mcl-getspectrum` | fixture `controls`; device for the scale | 3, 6 |
| 34 | EQ and filters at every rate up to 384 kHz | host | 3 |
| 35 | USB DAC: `hw:2,0`, 500 ms buffer | emulator shim log; device | 4 |
| 36 | USB software volume (`mcl-softvolflag`, `mcl-setvol`) | host: scaled PCM | 4 |
| 37 | Bluetooth: `plug:bluealsa` at the transport's rate | emulator; device | 4 |
| 38 | Bluetooth volume (`mcl-setvol`) | host; device for level | 4 |
| 39 | Bluetooth resampler state across a gapless change | host: split tone equals whole | 4 |
| 40 | The PCM kept open across a change on USB and Bluetooth | emulator: one open | 4 |
| 41 | An output-way change while playing | host and emulator | 4 |
| 42 | DSF and DFF, D2P | host exactness against FFmpeg | 4 |
| 43 | DoP | host: unpacked bits equal the file's | 4 |
| 44 | Native DSD | emulator shim log; device | 4 |
| 45 | DSD's `RATE` and `CODEC` | fixture with a `.dsf` | 4 |
| 46 | bluealsa's own patches still work with the new player (AAC, silence) | device | 4, 6 |
| 47 | `http` sources: DLNA and the phone's push | host HTTP server | 5 |
| 48 | `https` | host TLS server | 5 |
| 49 | The cache: size, start percent, `get_cache_buffer` | host: throttled server | 5 |
| 50 | Tidal: files from Q2 Pod's cache and direct URLs | host; device | 5 |
| 51 | QQ Music: `qm_songid`, `/tmp/qm_ID`, `libQMDecryption.so` | host stub; emulator with the real library | 5 |
| 52 | SACD ISO, stereo area, DST and plain DSD | host stub; device with an image | 5 |
| 53 | A stream that stalls, drops or ends early | host | 5 |
| 54 | Radio, AirPlay, Spotify and Videos still get the PCM when the player stops | emulator: the PCM is closed after `mcl-stop` and `mcl-orderstop` | 2, 5 |
| 55 | MQA files play as their PCM | host | 2 |
| 56 | M3U and playlists (demo expands them) | fixture | 2 |
| 57 | CPU no worse than stock plus a fifth at 44.1 to 384 kHz | device: `top` | 6 |
| 58 | Resident memory no worse than stock | device: `/proc/PID/status` | 6 |
| 59 | Wired start: no cut after a press, seek or resume | device: recording | 6 |
| 60 | Unpause: nothing skipped (Q2 Pod's `ps_ring` rewrite, not yet ported: phase 2's resume is stock's with the 130 ms wait) | device: recording | 6 |
| 61 | A reopen at another rate: the old song's end is heard | device: recording | 6 |
| 62 | Paused and stopped: no wakeups | device: `top`, or `strace -c` for a minute | 6 |
| 63 | Standby and resume with a song loaded | device | 6 |
| 64 | A day of ordinary use | device | 6 |

## Deviations

Each is a difference from stock V1.36's hciplayer, and why. Q2 Pod's existing fixes are in [player.md: What Q2 Pod patches](player.md#what-q2-pod-patches) and count as parity, not as deviations.

| Deviation | Why |
| --- | --- |
| FFmpeg 7.1.5, mbedTLS 3.6.7, no libmad and no OpenSSL 1.0.1 | stock's FFmpeg 3.0 and OpenSSL 1.0.1 are years out of maintenance and parse every file and stream played |
| MP3 decoded by FFmpeg's integer decoder, not libmad | one decoder stack; both put out 16 bits, and the samples differ in the last bit, as any two MP3 decoders' do |
| `https` checks the server's name and chain | stock's does not check either; a stream could be replaced by anyone on the network path. A server whose root is not in the CA file fails where it played before: phase 5 measures which servers users reach before turning it on |
| A message split across two `recv`s is put together | stock drops it. demo sends each message in one `send`, so this only matters under load |
| No start-up check of the unit record (`mtd_debug read /dev/mtd5`) | it protects nothing of the player's; demo makes its own check |
| No watchdog exit when the playback thread is busy | stock kills itself when its main loop stalls; the new player's command thread never waits on playback, so a stall cannot block demo |
| `mcl-mqadecodemode` and `mcl-playspeed` are stored and unused | as stock, where nothing reads them; listed so nobody looks for the feature |
| The stock ten-band `mcl-seteq` filter is not kept | Q2 Pod already replaces it with its own filter and uses `mcl-seteq` only as a signal; the ten gains are ignored there too |
| The PCM stays open across any same-format change, on the DAC as on USB and Bluetooth | stock and Q2 Pod do on some paths and reopen on others; reopening is where the cut starts and lost tails come from. Phase 6 confirms on a device that nothing relied on the reopen |
| Seeks and start offsets (`ss=`) land on the exact sample | stock's land on a frame boundary near it (Q2 Pod's start at 7 s of a FLAC gave 36 frames fewer); nothing depends on the error |
| `ready` lasts until the song's first frame is heard: a few ms after a start on a Q2's card, and on a preloaded change until the song before has played out of the PCM | stock's lasts about 600 ms after a start and 400 ms after a change under qemu, its own opening time. demo treats `ready` and `playing` alike (`0x5bad38` sets `mcl+0x2e` for both), and the payload reads neither |
| WavPack at 24 bits reports `BITS` 24 | stock says 32 (captured); it is display only, and 24 is the file's |
| A DTS stream stock refuses plays | stock answers `error` for FFmpeg's own encoder's `.dts` (captured); FFmpeg 7.1 decodes it bit for bit as the reference does |
| A WMA file's position starts at 0 | stock's starts at the ASF preroll, about 3 s on, so demo's `set-gapless` and Crossfade's hold came 3 s early ([internals: Forced EOF](internals.md#forced-eof)) |
| 24-bit WAV goes to ALSA as `S32_LE`, AIFF as `S16_LE` or `S32_LE` | stock asks for `S24_3LE`, `S16_BE`, `S24_3BE` and `S32_BE` (captured). On the DAC `default`'s `plug` converts either to the hardware's format, so the DAC is given the same samples; on a USB DAC, with no `plug`, phase 4 checks which formats a DAC refuses |
| An MP3 whose Xing count FFmpeg distrusts (much shorter than the file) ends at exactly its count when no MPEG frames follow it | Q2 Pod's player ends it once the position is a second past, about 0.3 s of the junk heard; frames that chain at the count (two files joined, a stale count) play on in both |

A candidate that is **not** adopted without a ruling: MP3 through FFmpeg's float decoder, written as float like AAC and Vorbis. It keeps what 16-bit rounding discards, but it changes the format the DAC is opened with for most of a library and `BITS` in `media-info` from 16 to 32. Parity keeps the integer decoder.

## Risks

- **demo in the emulator** plays since phase 2: the empty Folder page was the guest's `readdir` failing on the host's 64-bit directory offsets under qemu-user, fixed in the emulator's shim. A later phase's real-demo runs need the emulator copy with that fix ([player.md: Capturing](player.md#capturing)).
- **Float decoders on the device.** For AAC, Vorbis, Opus and the other float decoders, exactness rests on the X2000's FPU being IEEE, as the ABI assumes (FLAC, the other lossless formats and MP3 use integers alone). Already under qemu the MIPS build's AAC, Opus and WMA differ from this machine's in the last place of a few samples in a thousand, while Vorbis, AC-3 and DTS do not. Not established why: the guest's glibc 2.28 `libm` against the host's (tables FFmpeg computes at init with `sin`, `cos`, `pow`), or GCC contracting multiplies and adds into `madd.s` on MIPS. A MIPS build with `-ffp-contract=off` under the same emulator settles the second; a capture on a Q2 settles what the device does. Phase 6's first test plays the reference files and compares a USB or loopback capture, or the PCM logged by an `LD_PRELOAD` as the emulator's shim does.
- **Space while both players ship.** About 0.2 MB spare before TLS. If `sec/libs`'s larger libraries land first, phases 2 to 6 may need the dev build to drop something else (the Unicorn suites need hciplayer in the image, so it cannot be the one to go).
- **`default` is a `plug`.** Stock relies on it not converting. If a rate or width is not the hardware's, `plug` converts silently; the player asks for no resampling but cannot forbid a width change. Phase 6 reads `hw_params` at each format and, if one is converted, `output.c` asks for the hardware's width itself.
