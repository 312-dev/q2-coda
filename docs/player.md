# The stock player

What stock's `hciplayer` is, how `demo` drives it, and everything it does besides decoding: the contract `q2player` ([player-plan.md](player-plan.md)), which replaces it, keeps. Every address is stock V1.36's. Facts come from three places, named with each: the binaries (`usr/bin/hciplayer`, SHA-256 `f02ccc45…`, stripped; `release/bin/demo`, which has symbols), traffic captured from the real programs under qemu ([Capturing](#capturing)), and [internals.md](internals.md), which already covers each fault Vamp patches. Where the emulator cannot settle something, [Open questions](#open-questions) says what measurement on a Q2 would.

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
| `set-songurl` | `URL\|ss=S\|C\|P\|G\|H\|90` | Sets the play flag `0xb11e34` and clears a pending preload (`0x42d404`). With nothing loaded the song starts. While a song plays it is not cut: the new one starts when that song has ended (captured, `stock-replace.txt`), and `mcl-stop` forgets it. A stop sent just before, that q2player has not yet taken from the song playing, is that song's: demo's restart after an output change sends `mcl-stop` and then the same song from where it was (`player_save_start`), and q2player ends the song playing, answers the stop, and plays the new one next (`test_a_stop_before_a_restart_is_kept`). On a drive (2026-11-23) a write to the car's PCM held q2player 10.8 s, the stop was lost, the song played on and then again, and every song after it played one late. | none; see [Events](#events) |
| `set-nexturl` | the same | The same, and sets the next flag `0xb11e6c` (`0x42d1c8`): the song playing is cut. | `{player-nextstatus\exit}` when the old song is closed |
| `set-gapless` | `URL\|ss=S\|C\|P\|G\|90` | Open this on the preload thread and play it when the current song ends (`0x42cf8c`, flag `0xb11df8`). | none |
| `mcl-closegapless` | `null` | Forget a finished preload (`0x42c2bc`). | none |
| `mcl-pause` | `null` | `mpctx` state 2 (`0x42c5a8`); the play loop pauses the PCM. Memory Playback's paused boot and a restart after an output change (`player_save_start`) send it in the same instant as their `set-songurl`: q2player, with that song not yet open, holds the pause for it and opens it paused, and an `mcl-play` before then lifts it (`test_a_pause_right_after_a_song_holds_for_it`). A pause or play with no song open or asked for does nothing. | none |
| `mcl-play` | `null` | State 1 (`0x42c6dc`), only when `flag` is not 0. | none |
| `mcl-seek` | seconds | With `flag` not 0: mute (`0x43b004`), sleep 150 ms, then the target (float `0xb11be0`) and the seek flag `0xb11c40` (`0x42c614`). | none |
| `mcl-stop` | `null` | The stop flag `0xb11e70` (`0x42c9d0`). It holds for a song a `set-nexturl` named just before (20 to 50 ms, captured under qemu): that song is answered as cut and does not play, and the stop is answered too; sent in the same instant as the `set-nexturl`, stock plays the song (q2player stops: `test_stop_right_after_a_next`). A Library press of a server song sent that pair until the payload refused stock's replay of the queue (`patch/playback.c` `playback_start_refused`). | `{player-stopstatus\exit}` when a song was loaded, else nothing |
| `mcl-carrelease` | `null` | Not stock's: Vamp's, before the car's gadget is unbound ([Releasing the car](#releasing-the-car)). A car PCM held paused or between songs closed at once; the song stays paused. `mcl-dropcar` and SIGUSR2 to q2player do the same. | none |
| `mcl-orderstop` | `null` | Forgets the open PCM's format (`0xafb164`-`0xafb16c` to -1) and, off Bluetooth with `flag` 0, closes a PCM left open (`0x43af54`). | none |
| `mcl-setoutputway` | 0, 1, 2 | The output way `0xb11e54` and "changed" `0xb11e4c` (`0x42c90c`): 0 the DAC, 1 Bluetooth, 2 a USB DAC. A song playing goes on where it is; the way is taken at the next song (captured, `stock-way.txt`). | none |
| `mcl-setvol` | `L<<8 \| R`, each 0-255 | Stored (`0xb11e04`). While a song plays, on Bluetooth or USB, it reaches the software volume at once (`0x42c820`, [Volume and gain](#output)), on USB whatever `mcl-softvolflag` says, but not for DSD sent as DoP or Native; at each song's start it is applied on Bluetooth, and on USB with `mcl-softvolflag` 1 (`0x4235a8`). | none |
| `mcl-softvolflag` | 0, 1 | `usbaudio_softvol` (`0xb11e50`): the USB DAC's volume is done in software from each song's start. | none |
| `mcl-dsdmode` | 0, 1, 2 | DSD output: D2P (to PCM), DoP, Native (`0xb11e60`; demo's `dsdmode_d2p`, `dsdmode_dop`, `dsdmode_native`). On a Q2 it counts on USB alone: the DAC always gets DoP and Bluetooth PCM ([Output](#output)). | none |
| `mcl-replaygain` | mode | `0xb11e48`, taken at the next file (`0xb11e40` = 2). | none |
| `mcl-seteq` | ten floats and a flag, `\|` between | The ten band gains (`0xb11e0c` on) and on (1), "preset changed" (2) or off (0) (`0x42cb08`). Stock refuses it above 48 kHz. | none |
| `mcl-playspeed` | 0 to 3 | Stored (`0xb11e00`, `0x42c7c4`) and read only where a file's audio chain is built (`0x42e7c8`, in `reinit_audio_chain`): 1, 2 and 3 play at 1.25, 1.5 and 2 times (floats at `0x88c910`, `0x88c90c`, `0x88c908`) through MPlayer's `scaletempo`, pitch kept (a filter control `0x40003500`, MPlayer's `AF_CONTROL_PLAYBACK_SPEED` set), and only for a file of 48 kHz or less, as demo's Speed page says; 0 and anything above 48 kHz play at 1. q2player plays it as stock does through its scan's stretch (`player/stretch.c`, [Scan](#scan)), taken at a song's start. | none |
| `mcl-scan` | hundredths, below 0 backward | Not stock's: Vamp's hold-to-scan ([Scan](#scan)), which only q2player takes. | none |
| `mcl-setexplayinfo` | 0, 1, 2 | Which meter the play loop fills: none, level, spectrum (`0xb11db0`). | none |
| `mcl-mqadecodemode` | integer | Stored and read by nothing. | none |
| `player-status` | `request` | | `{player-status\playing}` when `flag` is 2, else `{player-status\ready}` |
| `media-info` | `get-music-info` | | `{media-info\BITRATE\|RATE\|LENGTH\|POSITION\|CODEC\|BITS}`, or `{media-info\no-playing-status}` |
| `get_cache_buffer` | `null` | | `{media-cache-buffer\N}`: a stream's cache fill, 0 for a file |
| `mcl-getuvmeter` | `null` | | `{mcl-getuvmeter\L\|R}` |
| `mcl-getspectrum` | `null` | | `{mcl-getspectrum\` twelve integers `}` |
| `mcl-pcmstate` | `null` | | `{mcl-pcmstate\N}`, `0xb11c24` or 0 when it is negative: the MQA decoder's state, 1 to 3, which the UI shows as its MQA mark (the M1 Plus's `mclGetMQAState`, in [shanling-m1plus-navidrome](https://github.com/shivaluma/shanling-m1plus-navidrome)'s `docs/research/native-integration-evidence.md`). Always 0 on V1.36, where the decoder is never made |
| `mcl-click` | `wheel`, `button`, `beep`, `charge` | Not stock's: q2player's, sent by Vamp's payload for the Clicker (`patch/clicker.c`, [internals: Key Tone](internals.md#key-tone)), and `charge` at a cable in ([internals: Charging](internals.md#charging)); a click whether a song plays or not, the chirp while one plays. q2player mixes the sound into what is about to be heard, or with nothing playing plays it in silence of its own ([player-plan: Clicker](player-plan.md#the-clicker)); stock's hciplayer, and Vamp's, ignore it as any name they do not know (captured) | none |

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

Vamp's `q2player` also sends a one-byte datagram to demo's `/tmp/q2player.push`, off this socket, whenever what `player-status` or `media-info` would answer changes and after each of these events, so demo's status poll waits for it instead of asking every 200 ms ([internals: Player status push](internals.md#player-status-push)). Stock's hciplayer sends nothing of the kind.

### What demo sends, and when

- **Every 200 ms while it believes a song is playing** (`mclSyncPlayerStatus`): `{player-status\request}` then `{media-info\get-music-info}`. It sends nothing while stopped.
- **At boot:** `{mcl-dsdmode\N}` and `{mcl-replaygain\N}` (captured), and the output way, volume and EQ as their settings load.
- **A song chosen while nothing plays:** on the DAC it mutes first (`mclSetMute(1)`, 100 ms), then `set-songurl` (`0x5bf894`-`0x5bf908`).
- **A song chosen while one plays** (Next, Previous, a list pick): `set-nexturl` (`0x5bf714`).
- **Gapless** (`mclAutoChange`, `0x5c1140`): with Gapless on, a song of 3 s or more, less than 6 s from its end and no preload sent yet, `set-gapless` for the next song (`0x5bb2cc`, `0x5baff8`). Never for `.dff`, `.dsf` or `.iso`. When `{player-status\exit}` then comes, `mclNextSong(1)` (`0x5c063c`) sends nothing: it moves its own index to the preloaded song and the player plays on alone. A queue edit sends `mcl-closegapless` (14 call sites).
- **No preload:** `exit` leads to `mclNextSong(1)` and a `set-songurl` for the next song, or at the queue's end `mclStop` and `{mcl-orderstop\null}` (`0x5c0810`-`0x5c0858`).
- **CUE:** tracks are entries of one file with `ss=` starts; demo ends a track itself when the position reaches the next track's start (`0x5c126c`-`0x5c12f0`) and sends the next entry.
- **Volume:** on the DAC demo sets the CS43131's own volume (`ioctl(/dev/shanling_dac, 0xc0044d00, L<<8|R)`, `mclSetLocalVol`) and sends nothing; on Bluetooth it sends `mcl-setvol` (`mclSetBtVol`); on a USB DAC with a volume control it writes that control and sends nothing, and on one without it sends `mcl-softvolflag` and `mcl-setvol` ([USB volume](#usb-volume)).

### Captured sequences

From stock under qemu ([Capturing](#capturing)); times in seconds, polls every 200 ms.

- **A song starts:** `set-songurl` at 1.000; polls answer `ready` and `no-playing-status`; from 1.202 `media-info` has the song; from 1.604 `playing`. So `ready` lasts about 600 ms and the song's facts are known 400 ms before it sounds.
- **A preloaded change:** `{player-status\exit}` at the old song's end, about 100 ms later `ready` with the new song's `media-info`, about 400 ms later `playing`. Two 20 s FLACs gave 1,764,000 frames in one PCM open, the two files' samples and nothing else.
- **Next:** `{player-nextstatus\exit}` 60-80 ms after `set-nexturl`, then as a start.
- **Stop:** `{player-stopstatus\exit}` 60-160 ms after `mcl-stop`. With nothing loaded, no answer.
- **A missing file, a file of noise:** `{player-status\error}` about 55 ms after `set-songurl`, with `/tmp/notfile` and `/tmp/notsupport` made.
- **Pause:** `player-status` goes on answering `playing`; the position stands still. A seek while paused shows in the position only after `mcl-play`; q2player's shows it at once, still paused ([player-plan: Deviations](player-plan.md#deviations)).
- **A seek:** the position follows 0.2 to 1.5 s later. A FLAC seek can end the song: to 15 s of a 20 s file it did, half a second later (`stock-transport.txt`, `stock-gapless-flac.txt`), and to 16 s of the same file it did not (`stock-end.txt`); Vamp's player plays on in all three.

The transcripts are in `test/player/fixtures/` (`stock-*.txt` from stock's binary, `pod-*.txt` from Vamp's patched one), and `test/player/run.py` holds the new player to them.

## Output

ao_alsa's init (`0x438248`) picks the device from the output way and the hardware type (`0x438f64`-`0x4391f8`):

| Way | Device | Notes |
| --- | --- | --- |
| 0, the DAC | `default` on a Q2 (hardware type 17; captured). Types 14 and 15 open `plughw:1,0`, types 7 and 11 `hw:1,0`: other Shanling players | The CS43131 is card 0 and the only card. stock's `alsa.conf` makes `default` a `plug` over `hw:0,0`, and the player turns ALSA's resampling off (`snd_pcm_hw_params_set_rate_resample`, "Unable to disable resampling") |
| 1, Bluetooth | `plug:bluealsa` | bluealsa takes one client; the transport's rate (48 kHz for SBC and AAC, 96 kHz S32 for LDAC) is not the file's, so MPlayer adds `lavcresample` ([internals: Bluetooth resampler](internals.md#bluetooth-resampler)) |
| 2, a USB DAC | `hw:1,0` on a Q2 (hardware type 17; captured) | a 100 ms buffer in stock; software volume when `mcl-softvolflag` is 1; q2player asks it what it takes and converts the song where it must ([USB DACs](#usb-dacs)) |
| 3, the car (Vamp, `--dev` builds only) | the iPhone gadget's card, `hw:CARD=UAC1Gadget,0`, named in `/tmp/q2-iap.audio` | not stock's and never sent by demo: while that file is there, way 3 stands in for 0 and 1 (`engine.c` `car_way`), at the car's rate (the one the car set, `p_rate`, which the player asks to follow each song's: [iphone-mode: per-song rate](iphone-mode.md#per-song-rate)) through Bluetooth's resampler, unity volume, the PCM kept across songs of the car's rate and never stopped while the car listens ([iphone-mode: car audio](iphone-mode.md#car-audio)) |

**Which headset.** `plug:bluealsa` plays to `defaults.bluealsa.device`, which demo writes to `/mnt/data/asound.conf` each time its link thread sets a headset up (`SaveBlueAlsa`, `0x4f9e68`, from `checkBluetoothStatus`); stock's `alsa.conf` loads that file through its `@hooks`. alsa-lib 1.1.6 reads its configuration again only when a file named in `ALSA_CONFIG_PATH` (`alsa.conf`) changes, never for a hook's file, so a player that keeps running across a switch from one headset to another looks for the first one's PCM ("Couldn't get BlueALSA PCM: PCM not found", `plug:bluealsa: No such device`). Stock's ao_alsa calls `snd_config_update_free_global` (`0x438ffc`) before it opens, so each open read the file afresh, and q2player frees it before every Bluetooth open too (`alsa_open`, `player/output.c`). That is not enough on its own: `SaveBlueAlsa` runs only when demo's own link thread sets a headset up, so a link made another way leaves the file naming the last headset, and the car's PCM below was up 12 minutes before the file named it. So q2player opens `plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp'` (`BT_DEVICE`), which bluealsa's plugin takes as the PCM with the highest `Sequence`, the device connected last, as Android plays to the active device rather than a remembered one; the file's address is not read. `test/player/run.py`'s `test_alsa_sw_params` holds both. Without that, a Q2 that played to a Bowers & Wilkins Px7 and then linked a BMW (2026-11-19, 01:24Z to 01:37Z) said "Not Supported" at every song while bluealsa had the car's PCM up on AAC and then SBC (its status events 2 and 1), before and after `asound.conf` was rewritten at 01:36:33Z. Under qemu-user the stock 1.1.6 library keeps a rewritten `asound.conf` unread until the configuration is freed, and takes the `DEV=00:00:00:00:00:00` name through to bluealsa's D-Bus lookup.

Parameters: interleaved read/write access, the file's rate (`set_rate_near`), its channels, a 500 ms buffer (`0x4382fc`) in 16 periods (`0x438264`). Software parameters (`0x438d64`-`0x438dc4`, the same on every way; the emulator's log of alsa-lib 1.1.6's calls agrees: 22,050 frames in periods of 1,378 at 44.1 kHz, boundary 1,445,068,800): `snd_pcm_sw_params_current`, then `start_threshold` a period (`get_period_size`'s answer), `stop_threshold` and `silence_size` the boundary (`get_boundary`'s), and nothing else, so `avail_min` stays a period and `silence_threshold` 0, ALSA's defaults. A PCM that runs dry is therefore never stopped: the hardware plays the silence ALSA keeps ahead of `hw_ptr` and the stream stays `RUNNING`, where ALSA's defaults (start at the first frame, stop at a buffer of underrun) stop it and the next write's `EPIPE` restarts it. What is written after such an underrun lands behind `hw_ptr` and is lost until `appl_ptr` catches up; Vamp's player moves past it ([internals: Bluetooth resampler](internals.md#bluetooth-resampler), `ao_write`), and so does q2player (`player/output.c`, `sink`), by `snd_pcm_forward` to a period past `hw_ptr`, on every way but Bluetooth, whose plugin stops at its own underrun. The format is the decoder's (captured, the shim's log of `snd_pcm_hw_params_set_format`): `S16_LE` for 16-bit FLAC and for MP3, `S32_LE` for 24-bit FLAC, and `FLOAT_LE` for AAC and Vorbis, whose decoders put out float; `default`'s `plug` turns that into what the DAC takes. Where the device refuses a format the init tries others (`0x439060`-`0x439110`, "Format %s is not supported by hardware, trying default"). The player keeps the PCM open across a preloaded change when rate, channels and format are what it last asked for (`0xafb164`-`0xafb16c`), and otherwise closes and reopens. Pause is `snd_pcm_pause`, a seek is `snd_pcm_drop` and `snd_pcm_prepare`.

`/dev/shanling_dac` is opened by the player too: `0xc0044d1b` sets PCM or DSD mode per track, `0xc0044d1f` is `user_mute` in `codec_cs43131.ko` (the mute line follows 90-100 ms after an unmute), and init also sends `0xc0044d0a`. Mutes surround every pause, seek, start and reopen ([internals: Wired start](internals.md#wired-start), [Unpause](internals.md#unpause)). The order, from the shim's log of stock's player (each argument is a pointer to an `int`):

- **Each song's start**, a preloaded one at the same format included: `0x1b` with 0 (PCM), `0x0a` with 0. The device is opened at the first and kept open.
- **An open or a reopen** (a start, a skip, another format): mute (1), the old PCM drained and closed, the new one opened and prepared, unmute (0), then frames.
- **Pause:** mute, `snd_pcm_pause(1)`. **Resume:** `snd_pcm_pause(0)`, unmute.
- **Seek:** mute, the 150 ms wait, `snd_pcm_drop`, `snd_pcm_prepare`, unmute. Paused, the seek mutes and drops, and the resume unmutes.
- **Stop:** mute, close. **A natural end with nothing after it:** drain, close, no mute.

Vamp's player adds the wait for the line after an unmute on a press, a seek and a resume, and its resume drops, prepares and rewrites what the ring held ([internals: Unpause](internals.md#unpause)); the shim shows its resume as drop, prepare, unmute.

**DSD** (`.dsf`, `.dff`, SACD `.iso`): mode 0 converts to PCM (MPD's `pcm_dsd`), mode 1 packs DoP (`pcm_dsd_to_dop`: 24-bit PCM frames at the DSD rate over 16 carrying the bits and the `0x05`/`0xFA` markers), mode 2 writes native DSD. `ET3 not force dsd native` (`0x888abc`) and the hardware type gate which modes a given output takes. DSD skips every filter but the volume. At each file (`0x4210d4`-`0x421a24`) the mode in use is `mcl-dsdmode`'s, then 0 on Bluetooth, and on a Q2 (type 17) 1 on the DAC whatever was asked; other types put 2 on the DAC. Captured, a DSD64 and a DSD128 DSF and a DSD64 DFF in each case:

| Output, mode | ALSA | `RATE`, `BITRATE` (DSD64) | ioctl `0x1b` |
| --- | --- | --- | --- |
| the DAC, any mode | DoP, `S24_3LE` at the DSD rate over 16 (176,400 Hz for DSD64) | 1,411,200, 5,644,800: half the DSD rate | 1 |
| USB, DoP | the same | 2,822,400, 5,644,800 | 1 |
| USB, D2P | `S16_LE` at the DSD rate over 8 (352,800 Hz) | 2,822,400, 11,289,600 | 0 |
| Bluetooth, any mode | `S16_LE` at 48,000 Hz | 2,822,400, 11,289,600 | 0 |
| USB, Native | format 31 (`SND_PCM_FORMAT_SPECIAL`), refused: nothing plays and no `media-info` | | 0 |

DoP's frames are the second DSD byte heard, the first, then the marker (0x05 first, then 0xFA, in turn), and carry the file's bits exactly. `BITS` is 1 and `LENGTH` the whole seconds. Off the DAC the only ioctl is `0x1b` at each song: no `0x0a`, no mutes.

**Formats off the DAC** (captured). Bluetooth is asked for 48,000 Hz whatever the file (`0x42ecc0`) and in the file's own format (`S16_LE`, `S32_LE`, `FLOAT_LE`); bluealsa offers its transport's rate alone, and MPlayer's `lavcresample` makes the file that rate. At a change of format stock closes and reopens the PCM on either output, and at a skip too. Where the device refuses the format, the init tries `S16_LE` and then `S32_LE` (`0x4390c4`-`0x439108`).

**Volume and gain.** On the DAC nothing in the player touches a sample unless the EQ or ReplayGain is on: volume is the DAC's. On Bluetooth and USB, `mcl-setvol`'s two bytes, L and R, go to MPlayer's `mixer_setvolume` (`0x4287a4`) as levels, by the vendor's law: either at 100 takes the volume filter out (unity); otherwise from 50 up a level is `0.1 + 0.018 * (byte - 50)` (255 is 3.79, +11.6 dB), below 50 `byte * 0.002` (`soft_vol_max` 20 times `1e-4`), the branch chosen by L alone, so R takes L's law (R well below L comes out negative, and stock's dB of it is not a number: its right channel was 8.4 times louder, captured). `soft_vol` is 0 (no `/etc/mplayer.conf`), so it first asks ao_alsa's mixer (`ao_control` 5), which fails, since hciplayer imports no `snd_mixer` call ([internals: Videos](internals.md#videos)), and the software filter is put in. The filter scales a 16-bit song in integers, `(x * (int)(255 * level)) >> 8` saturated (50 is 25/256: the USB capture is that sample for sample), and anything wider in float, `x * level` clipped at full scale.

**ReplayGain** (`mcl-replaygain`): the vendor changed FFmpeg's `ff_replaygain_export` (`0x538c84`) to leave the track and album gains and peaks in a global (`0xafd790`), and a filter set up at the next file (`0x4282c0`) applies them. Mode 1 takes the track's gain, or the album's when the file has no track gain; mode 2 the album's; 0 removes the filter, and any other value changes nothing. The gain (in 1/100000 dB) becomes a factor `min(10^(gain/20), 1/peak)`, a peak of 0 (none given) counting as 1, so without a peak a gain only cuts; a gain of exactly 100000 (+1 dB) is skipped, stock's compare against that constant. The filter is MPlayer's volume filter under the name `replaygain`, which multiplies in float and clips at full scale. MP3 goes through MPlayer's own demuxer and a vendor ID3v2 walker that misses most tags ([internals: MP3 ReplayGain](internals.md#mp3-replaygain)).

**EQ** (`mcl-seteq`): MPlayer's ten-band `af_equalizer`, refused above 48 kHz. Vamp replaces it ([below](#what-vamp-patches)).

**Meters.** With `mcl-setexplayinfo` 1 ao_alsa's play (`0x43ac54`) measures each write before `snd_pcm_writei` (`0x43a778`): the peak of the first 480 bytes' samples (240 at 16 bits, 160 at 24, 120 at 32 or float), even samples for the left and odd for the right, as whole dB of full scale truncated toward 0 (`20 * log10(peak / full)`, full 32767, 8388607, 2147483647 or 1.0), -60 for a channel at 0, a write under 481 bytes, another format or DSD. The two go to `0xb11dac` and `0xb11da8`, which nothing else writes, so a level stands until the next measured write. With 2 it runs an FFT into twelve spectrum bands (`0x43962c`). Both read 0 before anything is measured. Only demo's VU and spectrum pages ask (`mclGetUVmeter`, `mclGetSpectrum` and `mclSetExPlayInfoMode` are reached from `spectrum_page_init`, `vu_page_init` and their pages' timers alone), and Vamp leaves those pages out ([internals: Stock options held](internals.md#stock-options-held)).

### USB volume

`device_set_volume` calls `mclUsbAudioSetVol(v, v, "hw:1")` (`0x5bec64`) with the Q2's volume `v` (0 to 100), or 100 when Playback's USB volume is fixed (`g_usbvol_mode` 0); stock's host pass calls it at a DAC's discovery, before the song it restarts there. Its first call runs the finder (`0x5bab48`, `mcl_usb_audio_find_volume_ctls`), which records the numids of the card's `Playback Volume` controls (`0xa50030`, their count at `USB_MIXER`, `0xa50044`; -2 for none). With none it sends hciplayer `{mcl-softvolflag\1}` and `{mcl-setvol\N}`. With some it writes, to every channel of each, as disassembled: `trunc(range * (0.4 + 0.6 * v / 100))` for a range (max less min) of 255 or more, else `trunc(range * (0.2 + 0.8 * v / 100))` (`v / 100` in single precision, the rest in double: `0x5bf018` to `0x5bf0c0`), the control's minimum for `v` 0, clamped to its minimum and maximum (not offset by the minimum). On the AB13X earbuds (3302:13d1, one control, 0 to 11520 in 1/256 dB, -45 to 0 dB) volume 1 is 4677, -26.7 dB.

`ringnav_usb_setvol` (`patch/usb_volume.c`) takes it. When the first control is an integer one with a range of 255 or more and a dB range (its TLV: `SNDRV_CTL_IOCTL_TLV_READ` on `/dev/snd/controlC1`, `DB_MINMAX`, `DB_MINMAX_MUTE` or `DB_SCALE`, alone or in a container; demo imports no TLV call, so the ioctl is the payload's), `v` from 1 to 100 is spread linearly in dB from max(the control's minimum, -60 dB) at 1 to its maximum at 100, and stock's own pass is given the volume (it can be negative) whose stock value is nearest that control value, so stock still finds and writes every control and channel. A level is then within one stock step of the dB law's, 0.6 % of the range (0.27 dB on the AB13X). `v` 0 and any other control keep stock's law exactly; the first call lets stock's finder run, then sets the dB law's level in the same call, so the level a DAC starts its first song at is the new one. The on-screen volume is `g_volume`, which this does not touch. `test/fw/test_usb_volume.py` checks stock's -26.7 dB at volume 1 on the AB13X's numbers, the spread (red on stock's law), a control reaching below -60 dB, a `DB_SCALE` in a container, stock's law kept with no dB range, a small range, no control or a non-integer one, and the attach's first call. ponytail: a DAC with more than one volume control gets the first one's mapping on all of them.

## Sources

MPlayer's stream layer opens whatever `URL` names: a path, `http://` and, through the static FFmpeg and OpenSSL 1.0.1, `https://` (with no check of the server's name or chain: the build has no CA file). The cache fields of the song command size its read-ahead; `get_cache_buffer` reports how full it is.

| Feature | What reaches the player | Evidence |
| --- | --- | --- |
| Local files, CUE, M3U | paths; demo expands a CUE or M3U into entries | `mclLoadPlayList` |
| DLNA renderer and the phone's push | `http://` URLs; for queue types `0xf0`-`0xf3` (`mcl+0x78`) `mclStartPlayer` takes the URL from a callback | `player_dlna_stop`, `dmr_mainscheduling`, `0x5c01c8`-`0x5c03c4` |
| Tidal | `mcl_tidalStartPlayer` (`0x5bf948`); Vamp's cache plays files it downloaded | [internals: Tidal cache](internals.md#tidal-cache) |
| Baidu, QQ Music | `mcl_baiduStartPlayer` (`0x5bfac4`), URLs with `qm_songid` | below |
| Internet Radio (Vamp) | not the player: `q2video -r` runs `/usr/bin/ffmpeg` and writes the PCM itself | [internals: Internet Radio](internals.md#internet-radio) |
| AirPlay | not the player: `shairport -o alsa` | demo `0x78ac38` |
| Spotify (Vamp) | not the player: librespot through stock `aplay` to `plughw:0,0` | [librespot.md](librespot.md) |

The last three share the PCM with the player, so demo stops the player before they open it.

### Streams

Captured from stock's and Vamp's hciplayer under qemu against `test/player/httpd.py` (fixtures `stock-http*`, `pod-http*`, `-https-*`; the two agree but where named):

- **The cache** is MPlayer's (`cache2.c`): `C` KB, and with `C` -1 (a preload, some queues) MPlayer's own size for an HTTP stream, 3072 KB (`Cache size set to 3072 KBytes` in its log). It fills on a thread of its own, keeps up to half of itself behind the read position, and the song waits until `P` percent is in or the stream has ended (`waiting for buffer fill...`). `get_cache_buffer` answers MPlayer's `cache_fill_status` (`0xb47aa0`, through `0x42c448`): what is held ahead of the read position as a percent of the cache, 0 while nothing is loaded or until the first fill is done, and as it last was once the stream has all come (19 for a 289 KB FLAC in a 1024 KB cache; 9 from stock and 0 from Vamp's for the same FLAC in 3072 KB, by when the thread last ran). demo only asks whether it is above 0 (`mclGetCacheBuffer`'s one caller, `playerqueue_page_init` `0x53d1d4`).
- **A slow server.** A WAV from a server at 150,000 B/s (it plays at 176,400) started 10 s after its command, one at 400,000 B/s with 4096 KB at 50% after 9 s: MPlayer's demuxer looks at the file's end and back before it plays, each look a new connection (three in the log), which the test server serves from the start of its rate. With the slow one it then played half a second in four.
- **A stall, a drop, a short stream.** The server stopping for 8 s at 1 MB: stock waited it out before it began (its demuxer's look at the end waited on the stalled connection) and played the whole file. The first connection closed at 1 MB: it asked again from there and played the whole file. Every connection ending at 1 MB of 3.5: it played what came and then `{player-status\exit}`, as at a natural end.
- **Errors.** A 404 and, from Vamp's, a certificate its check refuses: `{player-status\error}` about 30-60 ms after the command, with `/tmp/notfile`.
- **A redirect** (302) is followed. **A server without ranges** plays; a seek inside what the cache holds is served from it.
- **A FLAC stream's seek near its end ends it**, half a second later, from both players (to 15 s of 20, to 12 s of 20): the fault Vamp's player fixes for files ([internals](internals.md#a-flac-seek-near-the-end)) is still there for a stream.
- **HTTPS**: stock plays a certificate for another name and a self-signed one; Vamp's (the chain checked against `/etc/ssl/certs/ca-certificates.crt`, [internals: Stock HTTPS](internals.md#stock-https)) refuses the self-signed one and plays the other.

### QQ Music

A stream filter in hciplayer (`0x4a9f90`-`0x4aa740`, log prefix `[qmdec]`) acts when the URL contains `qm_songid=ID` (each character checked, `bad songid char`). It reads the key demo wrote to `/tmp/qm_ID` (one line; demo removes old ones with `rm /tmp/qm_* -f`), and calls `libQMDecryption.so`, a C++ library with three C exports:

```c
void *CreateDecryption(const char *ekey, int ekey_length);              /* NULL on a bad key */
int Decrypt(void *handle, int64_t offset, unsigned char *data, int n);  /* in place; returns n */
void DestroyDecryption(void *handle);
```

Every block read from the inner stream is decrypted by its absolute offset in the file (`0x4aa034`-`0x4aa044`), so a seek only moves the offset. The format under the cipher is then demuxed as any other.

The details, from the code (`0x4aa324` on): the song id runs from after `qm_songid=` to the next `&`, `#`, `|` or `}` or the end, 1 to 127 characters, each a letter, a digit, `_` or `-` (else `bad songid char 0x%02x`); the key file is `/tmp/qm_<id>` (`%s/qm_%s` with `/tmp`), opened `rb` up to five times 20 ms apart, its first line (`fgets`, 1024 bytes) with spaces, tabs, CR and LF cut from its end and spaces and tabs from its start, and `CreateDecryption` is given it and its length. No key file, an empty one or a key the library refuses (`NULL`) each logs its line and the stream is read as it is, undecrypted. A `Decrypt` that returns other than `n` fails the read (`decrypt failed at offset %lld`). A seek on an inner stream that cannot seek reads forward to the offset, decrypting as it goes, and cannot go back (`cannot seek backward`).

### SACD ISO

libsacdread opens the image (`SACD iso demuxer`, `Uncompressed SACD iso`), and the player plays the stereo area as DSD. demo lists the tracks itself and passes `ss=` starts; it never preloads an `.iso`. Captured with `test/player/music.py`'s images (fixtures `stock-sacd*`): from `ss=` the area plays to its end, not to the track's, as DoP on the DAC, D2P on a USB DAC, `CODEC` `ISO`, `BITS` 1, `LENGTH` the area's whole seconds and `RATE` and `BITRATE` as a DSF's. An area in DST is refused before it plays (`Check failed for iso demuxer(forced)`, `{player-status\error}`, `/tmp/notsupport`); a plain image with only its area's frame format byte set to 0, DST's, is refused the same, so the DST decoder compiled in is not reached from an image.

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

What each reports and how the PCM is opened for it, captured from stock's and Vamp's hciplayer (the two agree on every row) playing one file of each (`fixtures/pod-formats-all.txt`, `stock-formats-all.txt`; the files are `test/player/music.py`'s, 6 s at 44.1 kHz stereo unless named):

| File | `CODEC` | `BITS` | `BITRATE` | ALSA format |
| --- | --- | --- | --- | --- |
| FLAC, 16 and 24 bits | `FLAC` | 16, 24 | file size over length | `S16_LE`, `S32_LE` |
| MP3 (LAME), MP2 | `MP3`, `MP2` | 16 | file size over length | `S16_LE` |
| WAV, 16, 24 and 32 bits, and float | `WAV` | 16, 24, 32, 32 | rate × channels × bits (1,411,200 at 16) | `S16_LE`, `S24_3LE`, `S32_LE`, `FLOAT_LE` |
| AIFF, 16, 24 and 32 bits | `AIFF` | 16, 24, 32 | rate × channels × bits | `S16_BE`, `S24_3BE`, `S32_BE` |
| ALAC, 16 and 24 bits | `ALAC` | 16, 24 | the stream's | `S16_LE`, `S32_LE` |
| WavPack, 16, 24 and 32 bits | `WMA` (the table has no WavPack entry) | 16, 32, 32 | the stream's | `S16_LE`, `S32_LE`, `S32_LE` |
| AAC in M4A, ADTS AAC, Vorbis, Opus (48 kHz), WMA 1 and 2 | `AAC`, `OGG`, `OPUS`, `WMA` | 32 | the stream's declared rate | `FLOAT_LE` |
| AC-3 | `AC2` | 32 | the stream's | `FLOAT_LE` |
| DTS from FFmpeg's own encoder | none: `{player-status\error}` | | | |

A WMA file's `LENGTH` is its whole seconds (5 for 5.99 s), and its position starts about 3 s on, at the ASF preroll. An MP3 with 3 MB of junk after its frames (`tail.mp3`) reports `LENGTH` 20 from its Xing count and a `BITRATE` of 1,311,656, its whole size over that length; Vamp's player plays its 20 s and stops. APE, TAK, WMA Lossless and WMA Pro were not captured: nothing on the capturing machine encodes them.

## USB DACs

**What stock does.** ao_alsa asks the device for the file's rate with `snd_pcm_hw_params_set_rate_near` and takes the rate it gets; when that is another rate, MPlayer puts `lavcresample` into the chain, as it does for Bluetooth (above). So a DAC that takes 48 kHz alone played a 44.1 kHz file resampled. This is from the code path only: under the emulator ALSA's `null` device takes any rate, so `stock-usb.txt` cannot show it.

**What q2player does** (`player/output.c`, `choose` and `alsa_negotiate`, on ways 0 and 2; Bluetooth, the car and DSD are as above). The device is asked which of `S16_LE`, `S24_3LE`, `S32_LE` and `FLOAT_LE` it takes, which channel counts from 1 to 8, and then, at the width and channels chosen, which of the rates from 8 kHz to 768 kHz (`RATES`) and the song's own rate. When it takes the song's rate, width and channels, the song goes out as it is, bit for bit, as before: a DAC that takes 44.1 to 384 kHz and 32 bits gets every song at its own rate and width, and DoP and Native are unchanged. Otherwise:

- the width is the first of `candidates()`'s order the device takes (the song's own, then 32, 24 and 16 bits for a wide song, 16, 32 and 24 for a 16-bit one);
- the channels are the song's, else stereo, else the fewest the device takes. A mono song is played on both channels at its own level, as the DAC's `plug` plays it; more channels than the device has are mixed down by libswresample;
- the rate is the song's own; else the lowest whole multiple of it the device takes (88.2 kHz for 44.1); else the lowest rate above it (48 kHz for 44.1 and 32, 96 for 88.2); else the highest whole divisor of it (48 kHz for 192); else the highest the device takes (`pick_rate`). Each keeps as much of the song's bandwidth as the device can carry.

The conversion is libswresample on float, at its own defaults (32 taps, phase shift 10, cutoff 0.97: flat to about 21.4 kHz at 44.1), where Bluetooth keeps stock's lavcresample settings. A song made 16-bit (resampled, or 24 or 32 bits into a 16-bit device) gets a TPDF dither of one step, as `dsp.c`'s cast tap does. The software volume, when it is on, scales the song before the resampler. Measured on this machine (FFmpeg's C alone, as the player is built, one core): 44.1 to 48 kHz stereo costs 0.13% of real time, 96 to 48 kHz 0.22%, 192 to 48 kHz 0.44%, against 0.21% for decoding a 44.1 kHz FLAC ([Scan: Cost](#scan)). With lavcresample's settings 44.1 to 48 kHz costs 0.08%. ponytail: by the ratio of the decode's cost, the X2000 should pay a few percent of a core for 44.1 to 48 kHz. `top -b -d 1` on a Q2 with a 48 kHz-only dongle would confirm it.

Across a preloaded change the PCM stays open when the next song goes out in the same format: into a device that takes one rate, every PCM song does, so a 44.1 kHz song, a 96 kHz 24-bit one after it and a 48 kHz one play in one PCM, the resampler's tail played out and a new one made at each change of rate (`output_take`). Where the device takes the next song as it is at another rate, the PCM is opened again at that rate, as before.

The log says what the device offers once while it stays attached, beside each kind of song made another format for it: `q2player: hw:1,0 offers 48000 Hz; S16_LE; 2 ch; 44100 Hz S16_LE 2 ch played as 48000 Hz S16_LE 2 ch`, or `... refused ...` for a song it takes in no format. Another device, one that offers otherwise, starts the record again.

**Channel order.** Every way is given the same interleaved order, the first sample of a frame the left: the DSP chain (`dsp.c`, Swap L/R included, for every way alike), then the resampler, whose layouts are libswresample's defaults for the channel counts (stereo is front left, front right on both sides), and the format converters, which keep each sample in its slot. `hw:1,0` is opened without asking its channel map (`snd_pcm_get_chmap`): a USB Audio Class stream that names left and right in its `wChannelConfig` carries them in the order of those bits, left first, which is the order written. What the device then does with each channel is its own. The headphone DAC takes the first sample as its left pair's ([hardware: DAC channels](hardware.md)).

**Unplugged.** A USB DAC that is not there to open (`snd_pcm_open`'s `-ENOENT` or `-ENODEV` on `hw:1,0`), or goes under a write (`-ENODEV`), is logged once, naming the call, its answer and the sound cards there then (`/proc/asound/cards`): `q2player: hw:1,0 is gone: snd_pcm_writei -19 (No such device); sound cards: 0 audiocodec, 1 AB13X`, and the song is not reported as one that cannot play. Each was, so demo's "Not Supported" dialog moved on through the queue, every song failing on `hw:1,0: No such file or directory`. Instead the song waits where it was heard (`engine.c` `output_back`), looking every 250 ms for `/dev/snd/pcmC1D0p` (`output_present`). Meanwhile demo's own detach, `check_otg_status`, moves the output back to the DAC and starts the song again paused, which replaces it. A Next, a stop, a preload or a new song are taken while it waits, and a click is dropped. When the device comes back first, the song goes on in a PCM opened again from the frame last heard: the half second the device held is played again. Nothing else counts as gone: an underrun (`-EPIPE`) or a suspend (`-ESTRPIPE`) on a write is prepared and written again, and any other error of a write ends the song as one that cannot play, logged as `q2player: hw:1,0: snd_pcm_writei -5 (Input/output error)` (`test_usb_errors`). On a drive (2026-11-23) the AB13X earbuds were found gone about 5 s before `/sys/bus/usb/devices/1-1` went, while a second plug of them played until they left; the line of then said neither the call nor the error. If the cards then show the earbuds as card 2, they came back as a new card while the player still held card 1, and `hw:1,0` (and `output_present`'s `pcmC1D0p`) would never find them.

**Volume.** demo, not the player, decides per device (`mclUsbAudioSetVol`, `0x5bec64`). After an attach (`check_otg_status`: `mclUsbAudioFree`, then `device_set_volume`, before the player's restart) it opens `hw:1`'s controls and keeps the numid of each element whose name has `Playback Volume` in it (`0x5bab48`; their count at `USB_MIXER`, `0xa50044`). With one it writes the Q2's volume `v` (0 to 100, or 100 when Playback's USB volume is fixed) to the control at each change: `trunc(range * (0.4 + 0.006 v))` when the control has 255 steps or more, `trunc(range * (0.2 + 0.008 v))` below that, 0 at `v` 0, clamped to the control's range. demo then sends the player nothing, and the player plays at unity. With none, it sends `{mcl-softvolflag\1}` once and every volume as `mcl-setvol`, and the player applies stock's law (above). The AB13X of 2026-11-22 (`3302:13d1`, full speed) has a `PCM Playback Volume` of 11,520 steps, 1/256 dB each from -45 to 0 dB. The kernel calls that range "unlikely", but only warns, after checking the step by reading it back. So demo uses it: -26.7 dB at volume 1, -18.9 dB at 30, -15.3 dB at 50, 0 dB at 100, and -45 dB at 0. That is never full scale, but its quietest step is loud in sensitive earphones. ponytail: demo's choice is the firmware's, not the player's; treating a control of more than 384 steps (the kernel's own threshold) as absent would give such a device stock's software law instead (-54 dB at 1).

`test/player/run.py`: `test_usb_48k_only` (the 44.1 kHz pair through a preloaded change into a 48 kHz S16 device is the whole file resampled, within a dither step of FFmpeg's own resample, no step at the cut, the offer logged once), `test_usb_48k_rates` (a tone then a 24-bit 96 kHz song in one 48 kHz PCM, each its own resample, the tone still 1 kHz), `test_usb_48k_soft_volume`, `test_usb_capable_exact` (44.1 kHz S16 and 96 kHz S32 bit for bit), `test_usb_gone` and `test_usb_gone_and_back` (no error event, the going logged once, the song on from the frame heard), and `test_alsa_sw_params`'s `usb48`, `usb48-96`, `usb48-mono` and `usb-gone` (the same through `alsa_open` against `alsa_test.c`'s libasound). Each but `test_usb_capable_exact`, the guard that a capable DAC stays bit for bit, failed before: the song was refused with `hw:1,0 gave 48000 Hz for 44100`, or reported as an error at the unplug.

<a id="pause"></a>
## Pause

`mcl-pause` pauses the PCM (`snd_pcm_pause`). On Bluetooth the PCM is closed once the song has been paused for `BT_PAUSE_MS` (20 s), so bluealsa's keep-alive lets the A2DP stream go, and the Play, a click or a seek opens one again, the song on from the frame heard ([internals: Bluetooth pause](internals.md#bluetooth-pause)). A device that refuses (the car's gadget advertises a pause its trigger cannot do: `-EINVAL`, [iphone-mode](iphone-mode.md#car-audio-in-the-image)) has its frames not yet heard taken back and kept, as for a click on a paused song (`output.c` `hold`, with the delay read before the refused pause moved it), and the PCM stopped and prepared; `mcl-play` writes them again, and the song goes on from the first frame not heard. A refused resume is logged and the next write goes on. `test/player/run.py` `test_alsa_sw_params` (`car-pause-fails`): the frames written again at the resume are the 60 of 100 not yet heard, from the first of them; it failed while the refusal was ignored.

<a id="releasing-the-car"></a>
## Releasing the car

Unbinding the car's gadget (`patch/q2car.sh` `gadget_down`, writing `""` to the gadget's `UDC`) frees its sound card, and that waits for the last close of the card while it holds the USB device controller's lock: a song paused on the car's way held its PCM open, its thread asleep until demo's next command, so the unbind, and every gadget change after it, waited for the next Play. Before the unbind the firmware asks the player to let go: `{mcl-carrelease\null}` on demo's socket (`mcl-dropcar` is the same), or `SIGUSR2` to q2player from a script (`kill -USR2 $(pidof hciplayer)`: q2player runs as `/usr/bin/hciplayer`). The playback thread wakes for it: a car PCM held paused, or kept open between songs, is dropped and closed (`snd_pcm_close`), and `q2player: the car's PCM released` logged after the close. The firmware can wait for `/proc/asound/UAC1Gadget/pcm0p/sub0/status` to read `closed`. A song paused there stays paused; at the Play it opens a PCM on the way the car's file says then (q2-iap's and q2car's to keep or remove) and goes on from the frame last heard, the frames written and not yet heard played again. A song playing, and another source's session (Radio, Videos), write on: the unbind's `-ENODEV` ends the car's way for them (`sink`). With no car PCM open it does nothing. `test/player/run.py` `test_car_release_while_paused` (each name, and the signal): the car's PCM open while paused and closed 0.4 s after the request (`/proc/PID/fd`), the release logged within that, before the Play; with the car's file kept a second car PCM going on 499 ms back, at the frame heard, and with it removed a Bluetooth PCM. It failed without the command (nothing released; the signal ended the player) and, the frame heard not sought, the song went on where the decode had got to.

**A car that stops reading.** A write to the car's PCM waits for room in `snd_pcm_wait`, `CAR_LOOK_MS` (250 ms) at a time, looking at the car file and the engine's cut between waits, and then writes no more than `snd_pcm_avail` says there is room for (`output.c` `sink_to`). A blocking `snd_pcm_writei` of more writes the room and then waits in the kernel for the rest until 5.10's timeout (`pcm_lib.c` `wait_for_avail`, 10 s), deaf to a stop or a skip, which a car that has stopped reading never ends. A write that takes a second or more logs one line, `q2player: a car write took 10862 ms: 4096 frames, 1 ms waiting (snd_pcm_wait 1), 10861 ms writing (snd_pcm_writei 2048, errno 0), RUNNING, avail 0` (the figures here are an example), saying where the time went. The drive of 2026-11-23 had one write of 10.8 s across the car's pause, a stop and a restart; whether it was that kernel wait the line will say next time. `test/player/run.py` `test_car_write_slow` (`alsa_test.c`'s `car-slow` cases): a stalled car with a stop at the first wait ends the write without a blocking writei (red before: one, 1.2 s in the model), and a write slow in its waits or in the writei logs its line.

## What it writes

`/tmp/ampsco.ini` (the unit record, at start), `/tmp/notfile` and `/tmp/notsupport` (through `system("touch …")`), and nothing else: no configuration, no state. While the car's USB audio plays, q2player also writes its trace lines to the unified log (a line a minute while all is well, one for each second with something wrong) and, only while `/mnt/mmc/.q2muse/logs/car-pcm.on` exists, the tap `/mnt/mmc/.q2muse/logs/car-pcm.raw` ([iphone-mode: The car's trace](iphone-mode.md#car-trace)), and `/tmp/q2-iap.under`, its underrun count. Vamp's patches add reads of `/mnt/data/peq-active` and `/tmp/q2xfade`. Every such path is the device's own in the image; a host build (`test/player`, `PEQ_ROOT "."`) keeps each under its run's directory, `/tmp/notsupport`, the car's files, the feed's FIFO and the sockets' defaults included, so players run side by side on one machine never see each other's files.

## What Vamp patches

Vamp shipped hciplayer patched until 2026-11-18, when `q2player` replaced it and the patches (`tools/peq.py`'s `patch_player`, `patch/peq_player.c`, `mp3_gapless.c`, `lavf_gapless.c`, `flac_*.c`) were deleted; they are in the history before that. Each row is a fault of the stock player and what its fix made true, which `q2player` must also make true ([player-plan: Parity checklist](player-plan.md#parity-checklist)).

| Patch (`tools/peq.py`) | What it makes true | Detail |
| --- | --- | --- |
| `EQ_OPEN_SLOT`, `EQ_RATE_GATES` | The EQ filter is Vamp's: up to 30 biquad bands with per-channel scope, preamp (fixed or Auto per rate), balance, 20 ms ramps between presets, the 3EQ's three bands on the same engine, state read from `/mnt/data/peq-active` (`Q2PEQ03`), at every rate; filter memory carried across a gapless change | [Parametric EQ](internals.md#parametric-eq), [3EQ](internals.md#3eq) |
| the same filter | Crossfade: the tail of a song held and mixed under the next, from `/tmp/q2xfade`: a plain fade at equal power, or Smart Crossfade's, planned from both songs' loudness (`player/smart.c`, the next song from `/tmp/q2xfade.next`); Mono Audio | [Crossfade](internals.md#crossfade), [Smart Crossfade](internals.md#smart-crossfade), [Mono Audio](internals.md#mono-audio) |
| `MP3_ALLOC`, `MP3_FILTER`, `MP3_CLOSE`, `MP3_READ`, `MP3_FILL`, `SEEK_SLOT`, `VBR_SCAN` | MP3: LAME/Info and iTunSMPB delay and padding cut, the last frame decoded, tail tags not decoded as audio, no whole-file walk at open, seeks by the Xing table | [MP3 gapless playback](internals.md#mp3-gapless-playback), [Large MP3s](internals.md#large-mp3s) |
| `LAVF_PRIV`, `MOV_EDITS` | AAC in M4A, Opus and Vorbis: priming and padding cut from the timestamps libavformat gives | [Lossy gapless playback](internals.md#lossy-gapless-playback) |
| `ID3_CALLS` | MP3 ReplayGain read from the whole ID3v2 tag, any text encoding | [MP3 ReplayGain](internals.md#mp3-replaygain) |
| `RS_OPEN_SLOT` | Bluetooth: the resampler's state survives a gapless change | [Bluetooth resampler](internals.md#bluetooth-resampler) |
| `AO_INIT_SLOT`, `AO_UNINIT_SLOT`, `USB_BUFFER` | Bluetooth and USB keep the PCM open across a change at the negotiated format; USB gets the 500 ms buffer; a song that ends before a reopen is drained, not cut | [USB output buffer](internals.md#usb-output-buffer) |
| `DAC_RELEASE` | The DAC is unmuted and given its 90-100 ms before sound flows after a press, a seek or a resume; an unpause replays what the DMA restart skipped | [Wired start](internals.md#wired-start), [Unpause](internals.md#unpause) |
| `FORCED_EOF` | Only an MP3's frame count ends a song early; other lengths are estimates | [Forced EOF](internals.md#forced-eof) |
| `MEDIA_INFO_NULL`, the `mcl-seteq` loads | `get-music-info` and `mcl-seteq` between songs answer instead of faulting | [Music info between songs](internals.md#music-info-between-songs) |
| `PROBE_STOP`, `STOP_SITES`, `STOP_FILL`, `EXIT_HOLD` | A bad next file does not stop the song playing; two `exit`s are never closer than 300 ms | [A bad file next](internals.md#a-bad-file-next) |
| `PRELOAD_PUBLISH`, `PRELOAD_FAILED`, `COLD_CLEAR`, `PRELOAD_CLOSE` | A preload that lost the race, or was canceled while opening, is not played | [A preload too late](internals.md#a-preload-too-late) |
| `FLAC_GATE`, `SEEK_NOSYNC`, `SEEK_WORDS`, `FLAC_META_EOF`, `flac_*.c` | A FLAC seek near the end plays to the end (stock ends the song: captured, a seek to 15 s of 20 ends it half a second later); a truncated FLAC does not hang the open | [A FLAC seek near the end](internals.md#a-flac-seek-near-the-end) |
| `PAUSE_WAIT`, `CMD_DONE`, `IDLE_WAIT` | Paused and stopped, the player sleeps on a futex, not a 10-20 ms poll | [Battery](internals.md#battery) |

Outside the player: `patch_bluealsa` and `tools/bt_silence.S` change bluealsa (AAC, silence when the player writes nothing, SBC's bitpool, the write MTU), and stay as they are.

## Scan

A held Previous or Next scans the track (`patch/seek.c`, [internals: Scan](internals.md#scan)). Stock's `scaletempo` could play a scan at speed, but only at a file's start, up to 2 times and at 48 kHz or less (`mcl-playspeed` above), so with stock's player the scan is jumps with half a second of the song at its own speed after each. q2player takes `{mcl-scan\N}`: N hundredths of the song's speed from the next block on, below 0 backward, 0 its own speed again (`player/engine.c`, `player/stretch.c`). The payload sends it only while `/tmp/q2scan` is there, which q2player makes at each song it can stretch and removes at one it cannot (DSD, or a rate whose top speed, `stretch_top`, is under 2) and when it hands over to stock's player.

**Forward.** The decoded song goes through a WSOLA time-stretch, the method of MPlayer's `scaletempo`: 40 ms segments out, each taken from where the song would be at that speed, moved up to 15 ms to where it best continues the segment before (the highest correlation of mono sums over 10 ms, over the candidate's energy's root), and crossfaded into it over those 10 ms. The pitch stays the song's. Above 48 kHz the search looks at every `rate / 48000`-th frame at every `rate / 48000`-th place, so its cost is about the same at every rate. The speed is capped at `384000 / rate` (`stretch_top`): no more frames a second decoded than a 384 kHz file at its own speed, which a Q2 plays. So 44.1 and 48 kHz go to 8 times, 96 kHz to 4, 192 kHz to 2, and nothing above 192 kHz stretches. While a scan runs only `SCAN_LEAD_MS` (150 ms) is written ahead, not the PCM's half second, so a change of speed and the release are heard that soon. N 0 plays on from the frame after the last segment's: nothing is skipped or played twice at the release.

**Backward.** The payload's default rewind steps back by snippets heard forward and does not use this; `SEEK_REWIND_SNIPPETS` 0 does (`patch/seek.c`). The song is decoded a whole second at a time from a seek (`rewind_block`), and each second is played last frame first, then the second before it, so the song runs backward without a break; the same stretch keeps its pitch. A turn between forward and backward starts again from the second being heard, with `mcl-seek`'s mute and 150 ms wait. At the song's start a backward scan waits, silent. `media-info`'s position is the second heard in either direction (`heard_at`: the frames decoded less what the stretch holds and the PCM's backlog at its speed).

**Cost.** Measured on this machine (one core, scalar C, `-O2 -fno-tree-vectorize`): the stretch costs 0.20 to 0.33% of real time a second of output at any speed and rate (44.1 kHz at 1 to 8 times, 96 kHz at 4, 192 kHz at 2), about what decoding 44.1 kHz FLAC costs at its own speed (0.21%; MP3 and AAC 0.34%, 192 kHz 24-bit FLAC 0.65%, FFmpeg C alone). The decoding scales with the speed: at 8 times, 44.1 kHz FLAC is 1.7% and MP3 2.7% of this core, so a scan's cost is mostly its decoding. By count, the search is about 7.3 million multiply-adds a second of output at 44.1 kHz, in four running sums. Under `qemu-mipsel` the float arithmetic is emulated in software and the stretch reads 24% of real time, which says nothing of a Q2's FPU. ponytail: on a Q2, `top -b -d 1` during 8 times on a 44.1 kHz MP3 and 2 times on a 192 kHz FLAC is the measurement that says whether `stretch_top`'s 384 kHz ceiling is right; a decoder that falls behind is heard as gaps in the scan only, never at the song's own speed.

## Capturing

`test/player/fakedemo.py` is demo's side of the socket: it listens, starts a player, sends a scenario's commands at their times, polls every 200 ms as demo does, and writes a transcript. For stock it starts `hciplayer` under `qemu-mipsel-static` in a `bwrap` sandbox of an unpacked root filesystem, with an `LD_PRELOAD` shim that stands in for what the sandbox lacks:

- `/dev/shanling_dac` opens as `/dev/null` and its ioctls answer 0;
- `mtd_debug` hands out a unit record (as the q2-emu emulator's does);
- `snd_pcm_open` opens ALSA's `null` device, whatever was asked for, and the shim logs the name, format, channels and rate; `snd_pcm_writei` copies what is written to a file per open and sleeps for its duration, so the player runs in real time. (`snd_pcm_hw_params_set_rate_near` must be found with `dlvsym(…, "ALSA_0.9.0rc4")`: the unversioned name is the 0.9 ABI, which takes the rate by value.)
- `Q2EMU_RATE`, when set, is the one rate every PCM takes whatever is asked, as bluealsa offers only its transport's (`fakedemo.py --rate`: 48,000 for SBC and AAC, 96,000 for LDAC);
- every log line has the monotonic time in ms; the DAC's ioctls are logged with their argument, and `snd_pcm_pause`, `snd_pcm_drop`, `snd_pcm_drain` and `snd_pcm_prepare` with their order among them;
- `readdir` and `scandir` are rebuilt on `readdir64`: under qemu-user the host hands out 64-bit directory offsets, which the guest's 32-bit `readdir` cannot hold, so on ext4 every directory read as empty after its first entry (demo's Folder page, its library scan, ALSA's `alsa.conf.d`); and a stand-in `nice` runs demo's `nice -n -5 hciplayer` at the priority the sandbox has.

`fakedemo.py --emu`'s `--killall N` has a shell in the sandbox list each process's name and command line in `ps.txt` and run demo's `killall -9 hciplayer` N seconds in.

```sh
python3 test/player/music.py MUSIC
python3 test/player/fakedemo.py --emu EMULATOR --hciplayer STOCKFS/usr/bin/hciplayer --music MUSIC --out OUT transport
```

`EMULATOR` holds `rootfs/` (a build's unpacked root filesystem), `qemu-mipsel-static`, `shim/libq2emu.so` and `state/` with the unit record. The same command with `--hciplayer player/build/mips/q2player` runs the new player in the same sandbox, against the same ALSA library.

demo itself was run under the emulator with the shim logging both programs' `send` and `recv`: at boot it sends `{mcl-dsdmode\0}` and `{mcl-replaygain\0}`, two `send`s the player reads in one `recv`. Once the shim rebuilt `readdir` (above), Folder lists the card and demo plays from it: with a build carrying `q2player` and no `/mnt/data/stock-player`, a Folder pick sends `set-songurl` (cache -1 from that queue), Gapless sends `set-gapless` about 5 s before the end, Next sends `set-nexturl`, and Play pauses with demo's own `mclSetMute(2)` and then `mcl-pause` ([player-plan.md: Phase 2 results](player-plan.md#phase-2-results)). The transcripts in `test/player/fixtures/` are still from `fakedemo.py`, sending what demo's code sends, since they need fixed times.

## Open questions

Each needs a Q2; none can be settled under qemu.

| Question | Measurement |
| --- | --- |
| Does the X2000 report MSA, and which FPU? The kernel has MSA support compiled in (`MSA revision is: %08x`), which does not say the CPU has it | `cat /proc/cpuinfo` (the `ASEs implemented` and `isa` lines) |
| What `default` resolves to with the device's ALSA state, and the buffer and period it grants at each rate from 44.1 to 384 kHz. Known: at 44.1 kHz `S16_LE` a Q2 granted 22,048 frames in periods of 848 (19 ms), not the emulator's 1,378, read on 2026-11-20; the AIC driver's `aic_pcm_hardware` (`soc_aic.ko`) allows a buffer of at most 131,072 bytes, periods of 1,024 to 4,096 bytes and 3 to 128 of them, so 500 ms fits only at 4 bytes a frame up to 48 kHz: 24-bit or 32-bit (8 bytes a frame) gets about 370 ms at 44.1 kHz, 170 ms at 96 kHz and 85 ms at 192 kHz. Not seen: what each of those rates gets, and whether the short buffers underrun | `hciplayer`'s `-v` log, or `cat /proc/asound/card0/pcm0p/sub0/hw_params` while each rate plays |
| What the DAC is given for each request: the player asks for `S16_LE`, `S32_LE` or `FLOAT_LE` (captured), and under qemu the `null` device accepts anything | `hw_params` for a 16-bit FLAC, a 24-bit FLAC, a 32-bit WAV and an M4A |
| DSD: what the DAC makes of DoP through `default` (`S24_3LE` at 176.4 and 352.8 kHz, captured as asked: whether the CS43131 plays it as DSD after ioctl `0x1b` 1), whether a USB DAC is always card 1, and whether one takes q2player's Native (`DSD_U32_BE`) where stock's format 31 is refused | `hw_params` and a listen with a DSD64 and a DSD256 `.dsf` in each of the three modes, on each output; `cat /proc/asound/cards` with a USB DAC in |
| Bluetooth: the rate and format bluealsa takes per codec, and the level `mcl-setvol` gives | `hw_params` equivalent from bluealsa's log, and a capture of its PCM |
| Whether `snd_pcm_rewind` works through `default` on the DAC, and how soon after a turn of the wheel q2player's click is heard (the Clicker, [player-plan](player-plan.md#the-clicker)) | `snd_pcm_rewindable` in q2player's log while a song plays; a recording of the headphone output against the wheel's tick |
| CPU: stock's load at 44.1, 192 and 384 kHz, the new player's at the same | `top -b -d 1` for a minute of each |
| `set-songurl` while a song plays, which demo never sends: stock's answer under qemu is in `stock-replace.txt`, and whether any demo path relies on it is unknown | a trace of the socket on a Q2 over a day's use |
