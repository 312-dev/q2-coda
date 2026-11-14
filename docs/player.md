# The stock player

What `hciplayer` is, how `demo` drives it, and everything it does besides decoding, written down so a new player ([player-plan.md](player-plan.md)) can stand in for it. Every address is stock V1.36's. Facts come from three places, named with each: the binaries (`usr/bin/hciplayer`, SHA-256 `f02ccc45…`, stripped; `release/bin/demo`, which has symbols), traffic captured from the real programs under qemu ([Capturing](#capturing)), and [internals.md](internals.md), which already covers each fault Q2 Pod patches. Where the emulator cannot settle something, [Open questions](#open-questions) says what measurement on a Q2 would.

## What it is

MPlayer 1.3.0 (`MPlayer 1.3.0-9.3.0`, its configure line at `0x887a98`), built by Shanling with:

- FFmpeg linked in statically: libavcodec 57.24.102 and libavformat 57.25.100 (FFmpeg 3.0, its banners in the player's log), and OpenSSL 1.0.1 for `https`;
- libmad for MP3 (`--enable-mad`, `libmad.so.0`), MPlayer's own `demux_audio` in front of libavformat for MP3, WAV and FLAC;
- sacd-ripper 0.37's libsacdread, libid3 and DST decoder, compiled in (`sacd/libsacd/scarletbook_read.c` and the rest at `0x89acf0` on). `libsacd.so` is in `DT_NEEDED`, but the player imports no symbol from it;
- MPD's `pcm_dsd` (DSD to PCM) and `pcm_dsd_to_dop` (`0x899aa0`, `0x899af0`);
- an MQA library that nothing reaches ([internals: MQA detection](internals.md#mqa-detection));
- `libQMDecryption.so` for QQ Music ([QQ Music](#qq-music)), `libasound.so.2` (alsa-lib 1.1.6), zlib.

ELF: MIPS32r2 little-endian, o32, hard float, `-mfp64`, NaN 2008 (flags `0x70001405`), interpreter `/lib/ld-linux-mipsn8.so.1`. 7,636,716 bytes.

It is one process with a main thread (MPlayer's play loop), a command thread (`0x42d634`), and a preload thread per `set-gapless` (`0x42b930`).

## Start

demo's `initMclModule` (`0x5bc9f8`) makes the socket `/tmp/sock_play` (`AF_UNIX`, `SOCK_STREAM`), listens, and starts three threads: `mclWaitAccept` (`0x5ba3d0`, an `accept` every 100 ms into `mcl+0x8c`; `mcl` is demo's player state at `0xa50000`), `mclParseCommand` (`0x5bb404`) and `mclSyncPlayerStatus` (`0x5bb874`). The player is started with `system("nice -n -5 hciplayer -ao alsa &")` (`0x5ba4d4`) and stopped with `killall -9 hciplayer` (`0x5bf7d4`, when `mcl+0x24` is set after a song command). When `recv` returns 0 or -1, `mclParseCommand` closes the socket, marks nothing playing and starts the player again (`0x5bb52c`-`0x5bb608`).

hciplayer, before anything else, runs `mtd_debug read /dev/mtd5 0 64 /tmp/ampsco.ini > /dev/null` and checks the record against a DES result (`Author failed` / `author sucess`, `0x420f44`): the same unit record demo checks. Its command thread then connects to `/tmp/sock_play` (`0x42d6fc`), and loops on `select` (1 s) and `recv` (2048 bytes) with a 10 ms sleep between passes. Once a second the thread also checks a flag the main loop sets (`0xb11e68`); when the main loop has not run it counts (`process_running=blocking_num`) and in the end exits the process (`block=exit`).

## The socket

Both directions carry text messages `{name\argument}`, with no length and no terminator other than `}`. Several can arrive in one `recv`, and both sides scan the buffer for `{`, `}` and the first `\` between them (hciplayer `0x42be80`, demo `0x5bb620`). Neither puts together a message split across two reads. demo writes a `}` inside a path as `%0x7d` (`toolsReplaceString`, `0x5c02b4`).

demo queues every message it receives as a (name, argument) pair (`0x5bb7f4`); a sender that wants an answer polls that queue by name, 10 times 10 ms apart (`0x5ba538`), and goes on without one.

### Commands

`flag` below is `p_isplaying` (`0xb11e80`): 0 nothing loaded, 1 a file is opening, 2 playing or paused.

| Command | Argument | What hciplayer does (address) | Answer |
| --- | --- | --- | --- |
| `set-songurl` | `URL\|ss=S\|C\|P\|G\|H\|90` | Sets the play flag `0xb11e34` and clears a pending preload (`0x42d404`). With nothing loaded the song starts. While a song plays it is not cut: the new one starts when that song has ended (captured, `stock-replace.txt`), and `mcl-stop` forgets it. | none; see [Events](#events) |
| `set-nexturl` | the same | The same, and sets the next flag `0xb11e6c` (`0x42d1c8`): the song playing is cut. | `{player-nextstatus\exit}` when the old song is closed |
| `set-gapless` | `URL\|ss=S\|C\|P\|G\|90` | Open this on the preload thread and play it when the current song ends (`0x42cf8c`, flag `0xb11df8`). | none |
| `mcl-closegapless` | `null` | Forget a finished preload (`0x42c2bc`). | none |
| `mcl-pause` | `null` | `mpctx` state 2 (`0x42c5a8`); the play loop pauses the PCM. | none |
| `mcl-play` | `null` | State 1 (`0x42c6dc`), only when `flag` is not 0. | none |
| `mcl-seek` | seconds | With `flag` not 0: mute (`0x43b004`), sleep 150 ms, then the target (float `0xb11be0`) and the seek flag `0xb11c40` (`0x42c614`). | none |
| `mcl-stop` | `null` | The stop flag `0xb11e70` (`0x42c9d0`). | `{player-stopstatus\exit}` when a song was loaded, else nothing |
| `mcl-orderstop` | `null` | Forgets the open PCM's format (`0xafb164`-`0xafb16c` to -1) and, off Bluetooth with `flag` 0, closes a PCM left open (`0x43af54`). | none |
| `mcl-setoutputway` | 0, 1, 2 | The output way `0xb11e54` and "changed" `0xb11e4c` (`0x42c90c`): 0 the DAC, 1 Bluetooth, 2 a USB DAC. | none |
| `mcl-setvol` | `L<<8 \| R`, each 0-255 | Stored (`0xb11e04`); applied as software volume (`0x4287a4`) only on Bluetooth, or on USB when the DAC has no volume of its own (`0x42c7fc`). | none |
| `mcl-softvolflag` | 0, 1 | `usbaudio_softvol` (`0xb11e50`): the USB DAC's volume is done in software. | none |
| `mcl-dsdmode` | 0, 1, 2 | DSD output: D2P (to PCM), DoP, Native (`0xb11e60`; demo's `dsdmode_d2p`, `dsdmode_dop`, `dsdmode_native`). | none |
| `mcl-replaygain` | mode | `0xb11e48`, taken at the next file (`0xb11e40` = 2). | none |
| `mcl-seteq` | ten floats and a flag, `\|` between | The ten band gains (`0xb11e0c` on) and on (1), "preset changed" (2) or off (0) (`0x42cb08`). Stock refuses it above 48 kHz. | none |
| `mcl-playspeed` | integer | Stored (`0xb11e00`). | none |
| `mcl-setexplayinfo` | 0, 1, 2 | Which meter the play loop fills: none, level, spectrum (`0xb11db0`). | none |
| `mcl-mqadecodemode` | integer | Stored and read by nothing. | none |
| `player-status` | `request` | | `{player-status\playing}` when `flag` is 2, else `{player-status\ready}` |
| `media-info` | `get-music-info` | | `{media-info\BITRATE\|RATE\|LENGTH\|POSITION\|CODEC\|BITS}`, or `{media-info\no-playing-status}` |
| `get_cache_buffer` | `null` | | `{media-cache-buffer\N}`: a stream's cache fill, 0 for a file |
| `mcl-getuvmeter` | `null` | | `{mcl-getuvmeter\L\|R}` |
| `mcl-getspectrum` | `null` | | `{mcl-getspectrum\` twelve integers `}` |
| `mcl-pcmstate` | `null` | | `{mcl-pcmstate\N}`, `0xb11c24` or 0 when it is negative |

The song fields, from demo's sender `0x5bf504` and hciplayer's parser:

| Field | From demo | In hciplayer |
| --- | --- | --- |
| `URL` | a path or a URL; QQ Music adds `&qm_songid=ID` | the file to open |
| `ss=S` | where to start, in seconds: a CUE track's start, a resumed song | `-ss` (`0xafab78`; a preload's at `0xb11df0`) |
| `C` | stream cache in KB, by queue type and extension (`mclStartPlayer`, `0x5c0118`): 512 for a file, 1024 or 2048 for `.ape` and `.flac`, 4096 for `.dff`, `.dsf` and `.iso` on some queue types, -1 on another; always -1 in `set-gapless` | `stream_cache_size` (`0xafab60`) |
| `P` | the percent of the cache to fill first: 20, or 50 with the 4096 KB cache | `stream_cache_min_percent` (`0xafab5c`) |
| `G` | demo's Gapless setting (`mcl+0x2c`, `PLAYSET GAPLESS`) | `0xb11e58`: with 1 the PCM is kept open across the change when the format allows |
| `H` | the hardware type, 17 on a Q2 (`mcl+0x9c` is 23, read from a running demo, and `0x5bf504` maps 23 to 17); not in `set-gapless` | `0xb11dfc`: picks the ALSA device and some waits |
| `90` | constant | `stream_cache_seek_min_percent` (`0xafab58`) |

`media-info`'s fields, as demo's `mclSyncPlayerStatus` reads them into `mcl-0x10` on:

| Field | Meaning |
| --- | --- |
| `BITRATE` | bits a second: the demuxer's bytes a second times 8. For what MPlayer's own demuxer reads (MP3, FLAC, WAV) that is the file's size over its length (101696 for a 254,244-byte 20 s FLAC); for what libavformat reads it is the stream's declared rate (112000 for a Vorbis file whose average is 25 kbps) |
| `RATE` | the sample rate in Hz; for DSD the PCM rate times 8, 16 or 32 by mode (`0x42c70c`-`0x42c954`) |
| `LENGTH` | whole seconds |
| `POSITION` | whole seconds, of what is being heard (the decoder's position less the output's delay) |
| `CODEC` | one of `UNK MP2 MP3 AAC APE OGG RM ALAC RA DTS AIFF WAV AC2 FLAC DSD DSF ISO TAK OPUS WMA` (five-byte entries at `0xafb100`, indexed by `sh_audio+0xd4`) |
| `BITS` | the sample width: 16 for 16-bit FLAC and for MP3 (libmad), 24 for 24-bit FLAC, 32 for AAC and Vorbis (float decoders) |

### Events

hciplayer sends these on its own, from the main loop, when a song ends (`0x422400`-`0x422e28`):

| Message | When |
| --- | --- |
| `{player-status\exit}` | the song played to its end |
| `{player-nextstatus\exit}` | `set-nexturl` cut it |
| `{player-stopstatus\exit}` | `mcl-stop` cut it |
| `{player-status\error}` | it did not open or decode. Before sending, the player runs `touch /tmp/notfile` (the file is missing, `0x423538`) or `touch /tmp/notsupport` (nothing can play it, `0x423548`); demo's dialog timer reads and removes them |

demo's reading of them (`0x5bad38`, on each `player-status` answer): `exit` and `error` clear its "playing" byte `mcl+0x2d`; `error` also sets status 1 (stopped); `playing` and `ready` set `mcl+0x2e`.

### What demo sends, and when

- **Every 200 ms while it believes a song is playing** (`mclSyncPlayerStatus`): `{player-status\request}` then `{media-info\get-music-info}`. It sends nothing while stopped.
- **At boot:** `{mcl-dsdmode\N}` and `{mcl-replaygain\N}` (captured), and the output way, volume and EQ as their settings load.
- **A song chosen while nothing plays:** on the DAC it mutes first (`mclSetMute(1)`, 100 ms), then `set-songurl` (`0x5bf894`-`0x5bf908`).
- **A song chosen while one plays** (Next, Previous, a list pick): `set-nexturl` (`0x5bf714`).
- **Gapless** (`mclAutoChange`, `0x5c1140`): with Gapless on, a song of 3 s or more, less than 6 s from its end and no preload sent yet, `set-gapless` for the next song (`0x5bb2cc`, `0x5baff8`). Never for `.dff`, `.dsf` or `.iso`. When `{player-status\exit}` then comes, `mclNextSong(1)` (`0x5c063c`) sends nothing: it moves its own index to the preloaded song and the player plays on alone. A queue edit sends `mcl-closegapless` (14 call sites).
- **No preload:** `exit` leads to `mclNextSong(1)` and a `set-songurl` for the next song, or at the queue's end `mclStop` and `{mcl-orderstop\null}` (`0x5c0810`-`0x5c0858`).
- **CUE:** tracks are entries of one file with `ss=` starts; demo ends a track itself when the position reaches the next track's start (`0x5c126c`-`0x5c12f0`) and sends the next entry.
- **Volume:** on the DAC demo sets the CS43131's own volume (`ioctl(/dev/shanling_dac, 0xc0044d00, L<<8|R)`, `mclSetLocalVol`) and sends nothing; on Bluetooth and USB it sends `mcl-setvol` (`mclSetBtVol`, `mclUsbAudioSetVol`).

### Captured sequences

From stock under qemu ([Capturing](#capturing)); times in seconds, polls every 200 ms.

- **A song starts:** `set-songurl` at 1.000; polls answer `ready` and `no-playing-status`; from 1.202 `media-info` has the song; from 1.604 `playing`. So `ready` lasts about 600 ms and the song's facts are known 400 ms before it sounds.
- **A preloaded change:** `{player-status\exit}` at the old song's end, about 100 ms later `ready` with the new song's `media-info`, about 400 ms later `playing`. Two 20 s FLACs gave 1,764,000 frames in one PCM open, the two files' samples and nothing else.
- **Next:** `{player-nextstatus\exit}` 60-80 ms after `set-nexturl`, then as a start.
- **Stop:** `{player-stopstatus\exit}` 60-160 ms after `mcl-stop`. With nothing loaded, no answer.
- **A missing file, a file of noise:** `{player-status\error}` about 55 ms after `set-songurl`, with `/tmp/notfile` and `/tmp/notsupport` made.
- **Pause:** `player-status` goes on answering `playing`; the position stands still. A seek while paused shows in the position only after `mcl-play`.
- **A seek:** the position follows 0.2 to 1.5 s later. A FLAC seek can end the song: to 15 s of a 20 s file it did, half a second later (`stock-transport.txt`, `stock-gapless-flac.txt`), and to 16 s of the same file it did not (`stock-end.txt`); Q2 Pod's player plays on in all three.

The transcripts are in `test/player/fixtures/` (`stock-*.txt` from stock's binary, `pod-*.txt` from Q2 Pod's patched one), and `test/player/run.py` holds the new player to them.

## Output

ao_alsa's init (`0x438248`) picks the device from the output way and the hardware type (`0x438f64`-`0x4391f8`):

| Way | Device | Notes |
| --- | --- | --- |
| 0, the DAC | `default` on a Q2 (hardware type 17; captured). Types 14 and 15 open `plughw:1,0`, types 7 and 11 `hw:1,0`: other Shanling players | The CS43131 is card 0 and the only card. stock's `alsa.conf` makes `default` a `plug` over `hw:0,0`, and the player turns ALSA's resampling off (`snd_pcm_hw_params_set_rate_resample`, "Unable to disable resampling") |
| 1, Bluetooth | `plug:bluealsa` | bluealsa takes one client; the transport's rate (48 kHz for SBC and AAC, 96 kHz S32 for LDAC) is not the file's, so MPlayer adds `lavcresample` ([internals: Bluetooth resampler](internals.md#bluetooth-resampler)) |
| 2, a USB DAC | `hw:2,0` | a 100 ms buffer in stock; software volume when `mcl-softvolflag` is 1 |

Parameters: interleaved read/write access, the file's rate (`set_rate_near`), its channels, a 500 ms buffer (`0x4382fc`) in 16 periods (`0x438264`). The format is the decoder's (captured, the shim's log of `snd_pcm_hw_params_set_format`): `S16_LE` for 16-bit FLAC and for MP3, `S32_LE` for 24-bit FLAC, and `FLOAT_LE` for AAC and Vorbis, whose decoders put out float; `default`'s `plug` turns that into what the DAC takes. Where the device refuses a format the init tries others (`0x439060`-`0x439110`, "Format %s is not supported by hardware, trying default"). The player keeps the PCM open across a preloaded change when rate, channels and format are what it last asked for (`0xafb164`-`0xafb16c`), and otherwise closes and reopens. Pause is `snd_pcm_pause`, a seek is `snd_pcm_drop` and `snd_pcm_prepare`.

`/dev/shanling_dac` is opened by the player too: `0xc0044d1b` sets PCM or DSD mode per track, `0xc0044d1f` is `user_mute` in `codec_cs43131.ko` (the mute line follows 90-100 ms after an unmute), and init also sends `0xc0044d0a`. Mutes surround every pause, seek, start and reopen ([internals: Wired start](internals.md#wired-start), [Unpause](internals.md#unpause)).

**DSD** (`.dsf`, `.dff`, SACD `.iso`): mode 0 converts to PCM (MPD's `pcm_dsd`), mode 1 packs DoP (`pcm_dsd_to_dop`: 24-bit PCM frames at the DSD rate over 16 carrying the bits and the `0x05`/`0xFA` markers), mode 2 writes native DSD. `ET3 not force dsd native` (`0x888abc`) and the hardware type gate which modes a given output takes. DSD skips every filter.

**Volume and gain.** On the DAC nothing in the player touches a sample unless the EQ or ReplayGain is on: volume is the DAC's. On Bluetooth, `mcl-setvol`'s two bytes scale the PCM in MPlayer's float volume filter (`0x45be84`), which multiplies and then saturates at the conversion to integers.

**ReplayGain** (`mcl-replaygain`): the vendor changed FFmpeg's `ff_replaygain_export` (`0x538c84`) to leave the track and album gains and peaks in a global (`0xafd790`), and a filter set up at the next file (`0x4282c0`) applies them. MP3 goes through MPlayer's own demuxer and a vendor ID3v2 walker that misses most tags ([internals: MP3 ReplayGain](internals.md#mp3-replaygain)).

**EQ** (`mcl-seteq`): MPlayer's ten-band `af_equalizer`, refused above 48 kHz. Q2 Pod replaces it ([below](#what-q2-pod-patches)).

**Meters.** With `mcl-setexplayinfo` 1 the play loop keeps a level per channel, in dB: a 441 Hz tone at -18 dBFS peak read `-21|-21` (its RMS), and that tone with a louder sweep over it read `-24|-24`, so the window and scale are not yet known. With 2 it keeps twelve spectrum bands. Both read 0 with nothing playing.

## Sources

MPlayer's stream layer opens whatever `URL` names: a path, `http://` and, through the static FFmpeg and OpenSSL 1.0.1, `https://` (with no check of the server's name or chain: the build has no CA file). The cache fields of the song command size its read-ahead; `get_cache_buffer` reports how full it is.

| Feature | What reaches the player | Evidence |
| --- | --- | --- |
| Local files, CUE, M3U | paths; demo expands a CUE or M3U into entries | `mclLoadPlayList` |
| DLNA renderer and the phone's push | `http://` URLs; for queue types `0xf0`-`0xf3` (`mcl+0x78`) `mclStartPlayer` takes the URL from a callback | `player_dlna_stop`, `dmr_mainscheduling`, `0x5c01c8`-`0x5c03c4` |
| Tidal | `mcl_tidalStartPlayer` (`0x5bf948`); Q2 Pod's cache plays files it downloaded | [internals: Tidal cache](internals.md#tidal-cache) |
| Baidu, QQ Music | `mcl_baiduStartPlayer` (`0x5bfac4`), URLs with `qm_songid` | below |
| Internet Radio (Q2 Pod) | not the player: `q2video -r` runs `/usr/bin/ffmpeg` and writes the PCM itself | [internals: Internet Radio](internals.md#internet-radio) |
| AirPlay | not the player: `shairport -o alsa` | demo `0x78ac38` |
| Spotify (Q2 Pod) | not the player: librespot through stock `aplay` to `plughw:0,0` | [librespot.md](librespot.md) |

The last three share the PCM with the player, so demo stops the player before they open it.

### QQ Music

A stream filter in hciplayer (`0x4a9f90`-`0x4aa740`, log prefix `[qmdec]`) acts when the URL contains `qm_songid=ID` (each character checked, `bad songid char`). It reads the key demo wrote to `/tmp/qm_ID` (one line; demo removes old ones with `rm /tmp/qm_* -f`), and calls `libQMDecryption.so`, a C++ library with three C exports:

```c
void *CreateDecryption(const char *ekey, int ekey_length);              /* NULL on a bad key */
int Decrypt(void *handle, int64_t offset, unsigned char *data, int n);  /* in place; returns n */
void DestroyDecryption(void *handle);
```

Every block read from the inner stream is decrypted by its absolute offset in the file (`0x4aa034`-`0x4aa044`), so a seek only moves the offset. The format under the cipher is then demuxed as any other.

### SACD ISO

libsacdread opens the image, and the player presents each track as DSD (DST-compressed areas are decoded by sacd-ripper's `dst_decoder`). demo lists the tracks itself and passes `ss=` starts; it never preloads an `.iso`.

## Formats

By demo's extension list (`0x782904` on, and the scan's table): `.mp3 .mp2 .flac .wav .aif .aiff .ape .m4a .aac .alac .ogg .opus .wma .ac3 .dts .dsf .dff .dsd .dst .dxd .iso`, playlists `.cue .m3u .m3u8`. By decoder:

| Format | Demuxer and decoder in stock | Named |
| --- | --- | --- |
| MP3, MP2 | `demux_audio`, libmad (16-bit) | `MP3`, `MP2` |
| FLAC | `demux_audio` (its own metadata walk and seek), FFmpeg `flac` | `FLAC` |
| WAV, AIFF | `demux_audio` or libavformat, PCM | `WAV`, `AIFF` |
| ALAC, AAC in M4A; ADTS AAC | libavformat `mov`, a vendor AAC scan; FFmpeg `alac`, `aac` | `ALAC`, `AAC` |
| Ogg Vorbis, Opus | libavformat `ogg`; FFmpeg `vorbis`, `opus` | `OGG`, `OPUS` |
| APE | libavformat `ape`, FFmpeg `ape` | `APE` |
| WMA | MPlayer's ASF demuxer, FFmpeg `wmav1`, `wmav2`, `wmapro`, `wmalossless` | `WMA` |
| AC-3, DTS | libavformat, FFmpeg | `DTS`; the table's `AC2` is presumably AC-3's (not played to check) |
| TAK, WavPack, RealAudio | libavformat (the table has `TAK`, `RA`, `RM`; demo lists none of the extensions) | `TAK`, `UNK`, `RA` |
| DSF, DFF, SACD ISO | vendor demuxers (`0x47b6fc`, `0x47a684`, `0x478990`) | `DSF`, `DSD`, `ISO` |
| MQA | plays as the PCM it carries | by its container |

## What it writes

`/tmp/ampsco.ini` (the unit record, at start), `/tmp/notfile` and `/tmp/notsupport` (through `system("touch …")`), and nothing else: no configuration, no state. Q2 Pod's patches add reads of `/mnt/data/peq-active` and `/tmp/q2xfade`, and the visualizer's shared tap.

## What Q2 Pod patches

`tools/peq.py`'s `patch_player` is the whole list: each row is a checked word or slot in hciplayer pointed at `patch/peq_player.c`, `mp3_gapless.c`, `lavf_gapless.c` or `flac_*.c`, mapped at `0xe20000`. A new player has no such faults to patch, but must do what each fix made true.

| Patch (`tools/peq.py`) | What it makes true | Detail |
| --- | --- | --- |
| `EQ_OPEN_SLOT`, `EQ_RATE_GATES` | The EQ filter is Q2 Pod's: up to 30 biquad bands with per-channel scope, preamp (fixed or Auto per rate), balance, 20 ms ramps between presets, the 3EQ's three bands on the same engine, state read from `/mnt/data/peq-active` (`Q2PEQ03`), at every rate; filter memory carried across a gapless change | [Parametric EQ](internals.md#parametric-eq), [3EQ](internals.md#3eq) |
| the same filter | Crossfade: the tail of a song held and mixed under the next at equal power, length from `/tmp/q2xfade`; Mono Audio; the visualizer's PCM tap | [Crossfade](internals.md#crossfade), [Mono Audio](internals.md#mono-audio) |
| `MP3_ALLOC`, `MP3_FILTER`, `MP3_CLOSE`, `MP3_READ`, `MP3_FILL`, `SEEK_SLOT`, `VBR_SCAN` | MP3: LAME/Info and iTunSMPB delay and padding cut, the last frame decoded, tail tags not decoded as audio, no whole-file walk at open, seeks by the Xing table | [MP3 gapless playback](internals.md#mp3-gapless-playback), [Large MP3s](internals.md#large-mp3s) |
| `LAVF_PRIV`, `MOV_EDITS` | AAC in M4A, Opus and Vorbis: priming and padding cut from the timestamps libavformat gives | [Lossy gapless playback](internals.md#lossy-gapless-playback) |
| `ID3_CALLS` | MP3 ReplayGain read from the whole ID3v2 tag, any text encoding | [MP3 ReplayGain](internals.md#mp3-replaygain) |
| `RS_OPEN_SLOT` | Bluetooth: the resampler's state survives a gapless change | [Bluetooth resampler](internals.md#bluetooth-resampler) |
| `AO_INIT_SLOT`, `AO_UNINIT_SLOT`, `USB_BUFFER` | Bluetooth and USB keep the PCM open across a change at the negotiated format; USB gets the 500 ms buffer; a song that ends before a reopen is drained, not cut | [USB output buffer](internals.md#usb-output-buffer) |
| `DAC_RELEASE` | The DAC is unmuted and given its 90-100 ms before sound flows after a press, a seek or a resume; an unpause replays what the DMA restart skipped | [Wired start](internals.md#wired-start), [Unpause](internals.md#unpause) |
| `FORCED_EOF` | Only an MP3's frame count ends a song early; other lengths are estimates | [Forced EOF](internals.md#forced-eof) |
| `MEDIA_INFO_NULL`, the `mcl-seteq` loads | `get-music-info` and `mcl-seteq` between songs answer instead of faulting | [Music info between songs](internals.md#music-info-between-songs) |
| `PROBE_STOP`, `STOP_SITES`, `STOP_FILL`, `EXIT_HOLD` | A bad next file does not stop the song playing; two `exit`s are never closer than 300 ms | [A bad file next](internals.md#a-bad-file-next) |
| `PRELOAD_PUBLISH`, `PRELOAD_FAILED`, `COLD_CLEAR`, `PRELOAD_CLOSE` | A preload that lost the race, or was cancelled while opening, is not played | [A preload too late](internals.md#a-preload-too-late) |
| `FLAC_GATE`, `SEEK_NOSYNC`, `SEEK_WORDS`, `FLAC_META_EOF`, `flac_*.c` | A FLAC seek near the end plays to the end (stock ends the song: captured, a seek to 15 s of 20 ends it half a second later); a truncated FLAC does not hang the open | [A FLAC seek near the end](internals.md#a-flac-seek-near-the-end) |
| `PAUSE_WAIT`, `CMD_DONE`, `IDLE_WAIT` | Paused and stopped, the player sleeps on a futex, not a 10-20 ms poll | [Battery](internals.md#battery) |

Outside the player: `patch_bluealsa` and `tools/bt_silence.S` change bluealsa (AAC, silence when the player writes nothing), and stay as they are.

## Capturing

`test/player/fakedemo.py` is demo's side of the socket: it listens, starts a player, sends a scenario's commands at their times, polls every 200 ms as demo does, and writes a transcript. For stock it starts `hciplayer` under `qemu-mipsel-static` in a `bwrap` sandbox of an unpacked root filesystem, with an `LD_PRELOAD` shim that stands in for what the sandbox lacks:

- `/dev/shanling_dac` opens as `/dev/null` and its ioctls answer 0;
- `mtd_debug` hands out a unit record (as the q2-emu emulator's does);
- `snd_pcm_open` opens ALSA's `null` device, whatever was asked for, and the shim logs the name, format, channels and rate; `snd_pcm_writei` copies what is written to a file per open and sleeps for its duration, so the player runs in real time. (`snd_pcm_hw_params_set_rate_near` must be found with `dlvsym(…, "ALSA_0.9.0rc4")`: the unversioned name is the 0.9 ABI, which takes the rate by value.)

```sh
python3 test/player/music.py MUSIC
python3 test/player/fakedemo.py --emu EMULATOR --hciplayer STOCKFS/usr/bin/hciplayer --music MUSIC --out OUT transport
```

`EMULATOR` holds `rootfs/` (a build's unpacked root filesystem), `qemu-mipsel-static`, `shim/libq2emu.so` and `state/` with the unit record. The same command with `--hciplayer player/build/mips/q2player` runs the new player in the same sandbox, against the same ALSA library.

demo itself was run under the emulator with the shim logging both programs' `send` and `recv`: at boot it sends `{mcl-dsdmode\0}` and `{mcl-replaygain\0}`, two `send`s the player reads in one `recv`. Playing a song from its screens was not achieved there (the Folder page stays empty and the library scan stops the UI in the sandbox), so the sequences demo sends during play are read from its code above, and the transcripts are from `fakedemo.py` sending what that code sends.

## Open questions

Each needs a Q2; none can be settled under qemu.

| Question | Measurement |
| --- | --- |
| Does the X2000 report MSA, and which FPU? The kernel has MSA support compiled in (`MSA revision is: %08x`), which does not say the CPU has it | `cat /proc/cpuinfo` (the `ASEs implemented` and `isa` lines) |
| What `default` resolves to with the device's ALSA state, and the buffer and period it grants at each rate from 44.1 to 384 kHz | `hciplayer`'s `-v` log, or `cat /proc/asound/card0/pcm0p/sub0/hw_params` while each rate plays |
| What the DAC is given for each request: the player asks for `S16_LE`, `S32_LE` or `FLOAT_LE` (captured), and under qemu the `null` device accepts anything | `hw_params` for a 16-bit FLAC, a 24-bit FLAC, a 32-bit WAV and an M4A |
| DSD: the exact PCM formats and rates of DoP and Native on the DAC and on a USB DAC, and which hardware types force which mode | `hw_params` with a DSD64 and a DSD256 `.dsf` in each of the three modes, on each output |
| Bluetooth: the rate and format bluealsa takes per codec, and the level `mcl-setvol` gives | `hw_params` equivalent from bluealsa's log, and a capture of its PCM |
| The meters' scale at silence and full scale, and the twelve spectrum bands' edges | `mcl-getuvmeter` and `mcl-getspectrum` over `q2remote` with test tones |
| CPU: stock's load at 44.1, 192 and 384 kHz, the new player's at the same | `top -b -d 1` for a minute of each |
| `set-songurl` while a song plays, which demo never sends: stock's answer under qemu is in `stock-replace.txt`, and whether any demo path relies on it is unknown | a trace of the socket on a Q2 over a day's use |
