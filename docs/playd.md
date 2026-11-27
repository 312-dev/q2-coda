# q2playd

Vamp OS's playback service ([roadmap: Who owns what](vamp-os.md#who-owns-what)): the server end of the player's socket, the queue and what plays. On today's Vamp `demo` is that server ([player: The socket](player.md#the-socket)); in Vamp OS `q2playd` is, so `q2player` and `q2ui` play local files with no `demo` ([vamp-os-base](vamp-os-base.md)). This is its first slice: no resume, play counts, ratings, gapless preload, cover art or audio focus yet ([What is missing](#what-is-missing)).

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
| `volume V` | 0 to 100, kept and shown | nothing yet ([What is missing](#what-is-missing)) |
| `stop` | Nothing plays | `mcl-stop` |

A song's `set-songurl` is built as `demo`'s `mclStartPlayer` builds it: the path (a `}` written `%0x7d`), `ss=0`, the cache by kind (512 KB, 1024 for `.flac` and `.ape`, 4096 with a 50% fill for `.dff`, `.dsf`, `.iso`), Gapless 0, the Q2's hardware type 17, and 90. When the player says a song ended (`{player-status\exit}`) the next plays; when it did not open (`error`) it is passed over, and after 20 in a row, or an id not in the library 20 times, the queue stops; at the queue's end it sends `mcl-orderstop` and nothing plays. A cut's own close (`player-nextstatus`, `player-stopstatus`) is no end. A player that goes away leaves nothing playing; `runsv` starts it again and `q2playd` takes its connection.

## Q2PLAY/1

The socket the UI is meant to move to, the shape the roadmap gives every service ([Process model](vamp-os.md#process-model)): line-framed text, a versioned first line, a reply to every command, and a subscription in place of a file to poll. `ui/player.c` and `ui/now.c` say they are what changes when `q2ui` stops going through `demo`'s queue; with this socket `player_send` becomes one line written and its `ok` read, and `now_read` the `now` lines a `watch` delivers, with no inotify on `/tmp`.

```text
> Q2PLAY/1                 the client's first line; anything else is "err version" and the connection closes
< Q2PLAY/1 ok
> play 0 1 3               then the three ids, a line each
> 12
> 40
> 7
< ok
> watch                    from now on, a "now" line at each change (as the now file's, at most twice a second)
< ok
< now title=Beta artist=Bob album=First%20Light state=1 pos=0 len=0 volume=24 id=12
> volume 30
< ok
> bogus
< err bogus
```

`now` alone answers one such line. At most 8 clients at once; a line from a client is 512 bytes at most. The socket is mode 0600, root's: the permission check is the file's ([Permissions and isolation](vamp-os.md#permissions-and-isolation) will give it a group).

**Fields.** A `now` line is `now` and then `key=value` fields separated by single spaces, in this order: `title`, `artist`, `album`, `state`, `pos`, `len`, `volume`, `id`. The free-text values (`title`, `artist`, `album`, which come from tags and can hold anything) are percent-encoded: every byte below `0x21` (space, line ends, other controls), `%`, `"`, `=` and `0x7f` is `%` and two upper-case hex digits; every other byte, UTF-8 included, is as it is. So no value holds a space, a line end or an `=`: a client splits the line on spaces, each field at its first `=`, and URL-decodes the value (`urllib.parse.unquote`, `decodeURIComponent`). A title `Cut state=2` is `title=Cut%20state%3D2`. The numbers are plain decimal. Commands carry no free text, so nothing a client sends is decoded. The now file for `q2ui` keeps the shim's format, a field a line and no encoding, with any line end or other control byte in a value made a space.

## Tests

`python3 test/playd.py` (pytest) builds `q2playd` for this machine against its own SQLite and runs it under a temporary root with a stand-in for `q2player` on its socket: a play from the FIFO and list file and its `set-songurl` byte for byte, the cache by kind and the brace's escape, an end playing the next and the queue's end stopping, a failed song and a missing id passed over, `next` cutting without a double advance, `toggle`, `previous`'s two ways, the position and length from `media-info`, the volume's range, a player that goes away and one that comes back, shuffle's order, and Q2PLAY/1's greeting, refusal of another version, play, `now` and `watch`, and a title holding ` state=`, `=`, `%` and a line end that a client splits back exactly while the now file keeps it on one line. Copies of `q2playd.c` with the escape, the advance at a song's end, the cut, the watchers' message or the field encoding removed each fail it (`Q2PLAYD_SOURCE`). `test/os_image.py`'s boot runs the image's own `q2playd` and `q2player` under QEMU: `q2player` connects, and a play asked for over Q2PLAY/1 reaches it and its end comes back (here "did not play": the emulator has no sound card). With the `q2playd` service replaced (`--perturb playd-off`) both checks fail.

## What is missing

- **The volume on the DAC.** On the Q2's own output `demo` sets the CS43131's volume with `ioctl(/dev/shanling_dac, 0xc0044d00, L<<8|R)` ([player: What demo sends](player.md#what-demo-sends-and-when)), from its 0-100 through a mapping not yet read; until it is (a trace on a `--dev` Q2), `volume` is kept and shown and changes nothing heard. It belongs to `q2sysd` with the DAC's power ([roadmap](vamp-os.md#who-owns-what)). Bluetooth and USB, where it is `mcl-setvol`, are not up in Vamp OS yet.
- **The DAC's power and mode before a song**: `demo` powers the DAC and sets its PCM mode before each track (`dac_take`, [internals: Videos](internals.md#videos)). Whether `q2player`'s own `/dev/shanling_dac` handling is enough for sound on a Q2 with no `demo` is a Q2 question.
- Gapless (`set-gapless` near a song's end), CUE tracks, resume (Memory Playback's files), play counts and ratings, repeat, the server library's songs (the catalogue's `servertable`), the cover (`/tmp/externpic.jpg`, which `q2ui` shows), audio focus between sources.
