# q2playd

Vamp OS's playback service ([roadmap: Who owns what](vamp-os.md#who-owns-what)): the server end of the player's socket, the queue and what plays. On today's Vamp `demo` is that server ([player: The socket](player.md#the-socket)); in Vamp OS `q2playd` is, so `q2player` and `q2ui` play local files with no `demo` ([vamp-os-base](vamp-os-base.md)). This is its first slice: server songs through their app, the cover, and a URL played with audio focus; no resume, play counts, ratings or gapless preload yet ([What is missing](#what-is-missing)).

| What | Where |
| --- | --- |
| Source | `playd/q2playd.c` (one file, one thread, one `poll` loop), `playd/Makefile` |
| In the image | `/usr/bin/q2playd`, the `q2playd` service (`os/package/q2playd`, `/etc/sv/q2playd`) |
| Tests | `test/playd.py` (this machine, a stand-in for `q2player`), `test/os_image.py`'s boot (the real `q2player` under QEMU) |

## What it talks to

| Side | Path | How |
| --- | --- | --- |
| `q2player` | `/tmp/sock_play` | It listens; `q2player` connects, as to `demo`. `{name\argument}` messages ([player: Commands](player.md#commands)) |
| `q2player`'s status push | `/tmp/q2player.push` | A datagram socket it binds: each push makes it ask `player-status` and `media-info`; besides, it asks once a second while a song is loaded, never while none is |
| `q2ui` today | `/tmp/coda-rc.cmdq`, `/tmp/coda-rc.list` | The FIFO and list file `ui/player.c` writes for `demo`'s shim (`patch/coda_rc_cmdq.h`): `q2playd` makes the FIFO and reads it, so `q2ui` needs no change |
| `q2ui` today | `/tmp/coda-rc.now` | What plays, as `demo`'s shim writes it (`ui/now.c` reads it): `title`, `artist`, `album`, `state` (0 stopped, 1 playing, 2 paused), `pos` (ms), `len` (s), `volume`, `id`, a `key=value` line each, rewritten whole (a temporary name, then renamed) on a change, twice a second at most |
| Clients to come | `/run/vamp/play.sock` | [Q2PLAY/1](#q2play1) |
| The library | `/mnt/data/database.db` | Stock's `songtable`: a song's `fileurl`, `name`, `songer`, `album` by its `id`, the id `q2ui` lists |

## Commands

The same verbs from the FIFO (`<seq> <verb>`) and from Q2PLAY/1:

| Verb | What it does | Sends the player |
| --- | --- | --- |
| `play AT SHUFFLE N` | A new queue of N ids (the FIFO's from `/tmp/coda-rc.list`, one a line; Q2PLAY/1's on the next N lines), from the one at AT; with SHUFFLE 1, that song first and the rest in a random order, each once | `set-songurl` (nothing playing) or `set-nexturl` (cutting the song playing) |
| `toggle`, `pause`, `resume` | Pause and play; `resume` with nothing loaded starts the queue's current song again | `mcl-pause`, `mcl-play`, or a song |
| `next`, `previous` | The next or the previous song; `previous` more than 3 s into a song starts it again | `set-nexturl`, or `mcl-seek 0` |
| `seek S` | To S seconds | `mcl-seek` |
| `volume V` | 0 to 100, capped at Max Volume, set on the DAC ([The volume](#the-volume)) and saved | nothing: the DAC's own ioctl |
| `stop` | Nothing plays | `mcl-stop` |

Q2PLAY/1 alone has `source` and `focus` ([Sources and audio focus](#sources-and-audio-focus)): a URL needs a holder to tell when it ends, and the FIFO has no answer channel.

A song's `set-songurl` is built as `demo`'s `mclStartPlayer` builds it: the path (a `}` written `%0x7d`), `ss=0`, the cache by kind (512 KB, 1024 for `.flac` and `.ape`, 4096 with a 50% fill for `.dff`, `.dsf`, `.iso`), Gapless 0, the Q2's hardware type 17, and 90. When the player says a song ended (`{player-status\exit}`) the next plays; when it did not open (`error`) it is passed over, and after 20 in a row, or an id not in the library 20 times, the queue stops; at the queue's end it sends `mcl-orderstop` and nothing plays. A cut's own close (`player-nextstatus`, `player-stopstatus`) is no end. A player that goes away leaves nothing playing; `runsv` starts it again and `q2playd` takes its connection.

## Q2PLAY/1

The socket the UI is meant to move to, `/run/vamp/play.sock`. Its protocol is Vamp API 1's playback page, [Q2PLAY/1](platform/playback.md), with its commands, its `now` fields and their encoding, its capability words and the transcripts its conformance test replays here. `ui/player.c` and `ui/now.c` say they are what changes when `q2ui` stops going through `demo`'s queue; with this socket `player_send` becomes one line written and its `ok` read, and `now_read` the `now` lines a `watch` delivers, with no inotify on `/tmp`. The now file for `q2ui` keeps the shim's format, a field a line and no encoding, with any line end or other control byte in a value made a space, and its `pos` in milliseconds; Q2PLAY/1's `pos` is whole seconds.

Only root and a package whose user is in the `playback` group get past the hello; anyone else is answered `err permission` and logged ([Packages: Users and groups](platform/packages.md#users-and-groups), `test/perm.py`). The socket is mode 0660 and that group's.

## Sources and audio focus

A `source URL` from a Q2PLAY/1 client (a DLNA renderer, an app's stream) plays one `http://` or `https://` URL in place of the queue, which is put aside whole: its ids and play order, or an app's m3u, its place, its position and length, and whether it was playing. The URL goes to the player as a song does (`send_song`: `ss=0`, the cache by the URL's last dot, a `}` as `%0x7d`), as a cut (`set-nexturl`) when something is loaded; `q2player` opens it through MPlayer's stream layer ([player: Streams](player.md#streams)). A decoded URL holding a space, a control byte or `|` (the player's field separator) is refused, as is any other scheme: a local path would let a package play a file its permissions do not reach once they are enforced. The cover file is removed.

The client is the source's holder until it gives it back (`focus release`), its connection closes, or the player is taken from it; the protocol's page lists when it is told and what comes back ([Q2PLAY/1: Audio focus](platform/playback.md#audio-focus)). A queue given back is **paused and not loaded**: `now` shows its song, position and length, the cover is its song's again, and `resume` (or `toggle`) sends that song with `ss=` its position in whole seconds. A pending catplay is cancelled by a source.

Decisions: the queue comes back paused rather than playing, so a phone that stops casting does not start the Library's music on its own (Android resumes after a short loss; a cast is no short loss); and a `play` drops the queue put aside, because the user chose new music.

## The volume

**What stock does** (learned from `demo`'s code, privately, and written here as the interface; [study hygiene](vamp-os.md#study-hygiene)): `device_set_volume(v, save)` is the one place the volume is applied. On the Q2's own DAC it calls `mclSetLocalVol(L, R)`, which is one `ioctl(/dev/shanling_dac, 0xc0044d00, &x)` with `x = (L & 0xff) << 8 | (R & 0xff)`, done only once the DAC is open. `L` and `R` are on the same 0 to 100 scale as the volume itself, both `v`: the mapping from 100 steps to the CS43131s' attenuation is the driver's (`codec_cs43131.ko`), not `demo`'s. Its other cases:

| When | Left, right |
| --- | --- |
| The usual case: the DAC | `v`, `v`; and `mclSetBtVol(v, v)` too, which sends `{mcl-setvol\v<<8 \| v}` only while the output is Bluetooth |
| Line out fixed (`g_output_way` 1 with the DAC as the way) | 100, 100, whatever `v` |
| A USB DAC (way 2) | `mclUsbAudioSetVol`, `v`, or 100 with USB volume fixed (`g_usbvol_mode` 0) |
| Bluetooth receiving, or AirPlay received | `v` scaled by the sender's level (over 127 or over 100) |

Balance is not here: it is a separate ioctl (`config_balance`).

**What q2playd does**: the usual case. At its start it reads `[PLAYSET]` in `/mnt/data/config.ini`, stock's file: `VOLUME`, or `BOOTVOLUME` when `BOOTVOLFLAG` is set (Default Volume), capped at `MAXVOLUME` (Max Volume, 100 when absent), and sets the DAC; each `volume V` (refused outside 0 to 100, held to Max Volume) is set on the DAC at once and written back as `VOLUME` 2 s after the last change, the file rewritten whole with that one line changed, so stock and today's Vamp read it as before. It keeps `/dev/shanling_dac` open beside `q2player`, which uses it for the mode and the mute. With no `/dev/shanling_dac` (the emulator) the volume is kept and shown and the boot log says so once.

Decision: the volume is set by `q2playd`, which owns its level, rather than by `q2sysd` as the roadmap's table has it ([Who owns what](vamp-os.md#who-owns-what)): stock does it in its player module (`mcl`), it is one ioctl, and splitting it across two processes would make every wheel tick a message. The DAC's power and its gain and filter stay `q2sysd`'s.

**Needs a Q2**: the driver's law, 0 to 100 into decibels (measured with a level meter at the jack, or read from the driver's register writes over I2C with `i2cget`); whether the ioctl does anything before the DAC is powered and in PCM mode with no `demo` to do that; and the line-out, USB and Bluetooth cases above, which are not built.

## Server songs

A play whose list holds server rows (ids from `0x40000000`, which `q2libd` writes into the card's `/mnt/mmc/.vamp/catalogue.db` `servertable`, [libd](libd.md)) is the app's to fetch, as on today's Vamp ([server-library: Play request](server-library.md)). `q2playd` keeps the firmware's side of that contract:

1. The app is the one whose catalogue the row is (`/mnt/mmc/.<id>/cat/...`). With its `/tmp/q2-<id>.browse.sock` there, the list goes to it as `/tmp/q2-<id>.catplay`, written beside and renamed: `<seq> <start>`, then a line per row in play order (a server row's `fileurl`, a card song's path), `<start>` the chosen song's line (0 when shuffled, the list already in shuffle order).
2. Nothing plays until the answer: `/tmp/q2-<id>.play` changing to `<seq> <m3u>` with that catplay's seq. Its m3u's card paths (Stream Cache copies for server songs, from `<start>` on) are then the queue, played as paths, the file's name as the title.
3. No answer within 4 s, an answer to another seq, or the app not listening: the list's card songs alone play, from the first at or after the chosen one.

Not yet: starting a stopped app for the play (today's Vamp starts it and waits up to 15 s), offline copies of server songs when no answer comes (the firmware plays them from `.vamp`'s Stream Cache), `.ahead` (Download Ahead), and Now Playing while the answer is awaited.

## The cover

At each song's start its cover is copied to `/tmp/externpic.jpg`, where stock's cover thread leaves it and `q2ui` reads it (`ui/now.c`), written beside and renamed; with none the file is removed, so the last song's does not stay. A card song's is `q2libd`'s (`library.db`'s `vamp_cover`, [libd: Covers](libd.md#covers)); a server row's its app's `covers/<album id>.jpg` ([server-library: Covers](server-library.md)). A song of an app's m3u takes its row's title, artist, album and cover: the card song of that path, or for a Stream Cache copy (`/mnt/mmc/.<id>/stream/.../<title> [<sid>].<suffix>`, `patch/stream_path.h`) the server row of that sid.

## Tests

`python3 test/playd.py` (pytest) builds `q2playd` for this machine against its own SQLite and runs it under a temporary root with a stand-in for `q2player` on its socket: a play from the FIFO and list file and its `set-songurl` byte for byte, the cache by kind and the brace's escape, an end playing the next and the queue's end stopping, a failed song and a missing id passed over, `next` cutting without a double advance, `toggle`, `previous`'s two ways, the position and length from `media-info`, the volume's range, a player that goes away and one that comes back, shuffle's order, the volume (its start from `config.ini` and Default Volume, the DAC's ioctl recorded by an `LD_PRELOAD` stand-in, Max Volume's cap, the save changing one line), and Q2PLAY/1's greeting, refusal of another version, play, `now` and `watch`, the position in seconds on Q2PLAY/1 and in milliseconds in the now file, and a title holding ` state=`, `=`, `%` and a line end that a client splits back exactly while the now file keeps it on one line; server songs: a list handed to its app as a catplay byte for byte with nothing played until its m3u answers and then the m3u's songs in turn, an unanswered catplay playing the card songs from the chosen one after 4 s, an app not listening getting no catplay and the card songs at once, and an answer to another seq not taken; the cover copied for a song with one and removed for the next without, and a Stream Cache copy showing its server row's title, artist, album and cover. Copies of `q2playd.c` with the escape, the advance at a song's end, the cut, the watchers' message, the field encoding, the DAC's two bytes, the cap, Default Volume, the catplay, its seq check, the card-only fallback, the cover, its removal or the Stream Cache lookup removed each fail it (`Q2PLAYD_SOURCE`). `test/os_image.py`'s boot runs the image's own `q2playd` and `q2player` under QEMU: `q2player` connects, and a play asked for over Q2PLAY/1 reaches it and its end comes back (here "did not play": the emulator has no sound card). With the `q2playd` service replaced (`--perturb playd-off`) both checks fail. The sources: a URL played as a cut with its percent-decoded fields and `id=0`, `next` and `previous` refused, `focus` for the holder and another client, a release only by its holder, the queue given back paused at 42 s and resumed with `ss=42`; a URL played out telling `focus lost why=end` without the queue starting, one that does not open telling `error`; a `play` from the FIFO taking it (`taken`) and dropping the queue; another client's `source` taking it while the first queue still comes back; a `stop` telling `stop` and giving the queue back stopped; a holder whose connection closes giving the queue back; and an `ftp://` URL, a path, and URLs with a space, a `|` or a line end refused. Copies with the position not kept, no `focus lost`, a `|` allowed, the resume from 0, no check for a holder gone, `previous` allowed, a play not dropping the source, or no percent-decoding each fail it. Its `test_conformance_*` tests replay [playback.md](platform/playback.md)'s transcripts against it and check that every capability word is sent by one ([conformance](platform/README.md#conformance)).

## What is missing

- **The volume's other outputs**: line out fixed, USB DACs, Bluetooth ([The volume](#the-volume)), and Balance.
- **The DAC's power and mode before a song**: `demo` powers the DAC and sets its PCM mode before each track (`dac_take`, [internals: Videos](internals.md#videos)). Whether `q2player`'s own `/dev/shanling_dac` handling is enough for sound on a Q2 with no `demo` is a Q2 question.
- Gapless (`set-gapless` near a song's end), CUE tracks, resume (Memory Playback's files), play counts and ratings, repeat, the rest of server songs ([Server songs](#server-songs)); a source's next URL ahead of time (UPnP's `SetNextAVTransportURI`), and a source's cover (an album art URL).
