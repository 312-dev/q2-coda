# Library 1: the media library

`q2libd`'s two databases ([q2libd](../libd.md)), read-only to everyone else: open them with SQLite's read-only flag, and read again when they change (each is written whole beside itself and renamed over it, so a reader never sees half a scan; a reader holding the old file open keeps reading the old one). One change is made in place: `servermeta`'s `local`, in one transaction, as an app's Stream Cache gains or loses a copy. The same library is asked over a socket too, [Q2LIB/1](#q2lib1-the-library-socket), for a program that would rather not read SQLite: songs, albums, artists, genres and playlists, search, a song's cover, and a word when the library changes.

| File | Tables |
| --- | --- |
| `/mnt/data/vamp/library.db` | the card's songs, their covers, the card's playlists |
| `/mnt/mmc/.vamp/catalogue.db` | the signed-in servers' songs, less those that are card songs, their offline copies, and the servers' playlists |

**Ids.** A card song's id is below `0x40000000` and stays while its path does. A server song's is `0x40000000 + slot * 0x01000000 + n`, slot 0 to 3 its app's place among the signed-in apps by name: stable while the app's catalogue is, not across a sign-out. [Q2PLAY/1](playback.md) plays either kind by id.

**Paths.** `fileurl` is the path as the Q2 names it (`/mnt/mmc/...`). A server row's is `/mnt/mmc/.<app>/cat/<album id>/<sid>.<suffix>`, a name and not a file: the app fetches it ([server-library](../server-library.md)). A cover's `file` is a JPEG or PNG on the card.

**Offline copies.** `servermeta` has a row for each `servertable` row: its `id`, its app's `app` id, the server's `sid`, and `local`, the path of a whole copy on the card (the app's Stream Cache) or NULL. A server song with a copy plays from it when its app does not answer ([q2playd: Offline copies](../playd.md#offline-copies)).

**Server playlists.** `serverpl` holds each (`app`, `pid` the server's id, `name`); `serveritem` its songs (`app`, `pid`, `pos` from 0 in order, `sid`, and `path`: a synced song's card file, else NULL and the song is the `servermeta` row of that `app` and `sid`).

**Playlists.** `playlist` holds a header row for each (`id` and `cd_num` its number, 1 to 100, `name` its name) and its songs (`id` from 101 in order, `cd_num` the playlist's, the other columns the song's `songtable` row).

## Conformance

The tables and their columns, in order, as a scan writes them; `test/libd.py`'s `test_conformance_library` scans a stand-in card with a signed-in app and compares each file's tables and columns with these blocks, so a column cannot change without this page. Columns beyond these may come in a later Library 1, never fewer.

```schema library.db
songtable: id filename fileurl album songer style name composer albumsonger year track cd_num time time_end time_create Initial bitdepth samprate
vamp_files: fileurl size mtime id
vamp_cover: id file
playlist: id filename fileurl album songer style name composer albumsonger year track cd_num time time_end time_create Initial bitdepth samprate
```

```schema catalogue.db
servertable: id filename fileurl album songer style name composer albumsonger year track cd_num time time_end time_create Initial bitdepth samprate
serverdup: id fileurl
servermeta: id app sid local
serverpl: app pid name
serveritem: app pid pos sid path
```

## Q2LIB/1: the library socket

`q2libd` serves `/run/vamp/lib.sock` (mode 0660, the `library` group's) in the [common shape](README.md#the-common-shape), from a thread of its own, so a scan never holds an answer up. Each command reads the two files as they are at that moment (read-only, `catalogue.db` attached), so an answer is never half of one scan and half of the next. A caller without the `library` permission is answered `err permission` at its hello. Nothing on this socket changes the library.

**A song** is one line, the same for a card song and a server song:

```text
song id=ID title=T artist=A albumartist=AA album=AL genre=G composer=C year=Y track=N disc=N time=S source=SRC
```

`id` is the song's id in the two files (**Ids**, above), what [Q2PLAY/1](playback.md) plays; `time` the length in seconds; `source` is `card`, or the app id of the server it came from (`servermeta`'s `app`). Text is percent-encoded, an empty value is nothing after the `=`, and a number not known is `0`.

**Order.** Songs are listed by album artist (the artist where there is none), album, disc, track, title, then id, each text case aside: the Library's own album order. Albums by artist then name, artists and genres by name.

**Pages.** A listing takes `from=N` (the first row, from 0) and `count=N` (100 when not given, at most 500); a page shorter than `count` is the last.

| Command | Answer |
| --- | --- |
| `songs [album=A] [artist=A] [genre=G] [source=SRC] [from=N] [count=N]` | the songs that match every filter given, each compared case aside (`artist` is the artist or the album artist), a `song` line each, then `ok` |
| `song ID` | its `song` line, or `err none` |
| `albums [artist=A] [from=N] [count=N]` | `album name=AL artist=AA year=Y songs=N first=ID` a line, `first` its first song in the order above (for its cover, `art`), then `ok`; songs with no album are in none |
| `artists [from=N] [count=N]` | `artist name=AA albums=N songs=N` a line, the album artist where a song has one (songs with neither are in none), then `ok` |
| `genres [from=N] [count=N]` | `genre name=G songs=N` a line (songs with no genre are in none), then `ok` |
| `playlists` | the card's playlists, `playlist id=N name=T songs=N`, then the servers', `playlist app=APP pid=P name=T songs=N`, then `ok` |
| `playlist id=N` or `playlist app=APP pid=P` `[from=N] [count=N]` | the playlist's songs in its order, a `song` line each (a song the library does not have is left out), then `ok`; `err none` for no such playlist |
| `search TEXT [count=N]` | the songs whose title, artist, album artist or album holds TEXT (percent-encoded; case aside), in the order above, then `ok` |
| `art ID` | `art id=ID file=PATH`, the song's cover on the card (`/mnt/mmc/.vamp/covers/...`, JPEG or PNG), or `err none` |
| `lyrics ID` | the song's lyrics, `lyrics id=ID kind=word\|line\|plain source=file\|tag lines=N`, then `line at=MS text=T words=MS:BYTE,...` a line in time order, then `ok`; `err none` for a song with no lyrics, a server song or an unknown id |
| `watch` | `ok`; then `changed library` when `library.db` is written again, `changed catalogue` when `catalogue.db` is (a scan, or an offline copy gained or lost), looked at every second |

**Lyrics** are read from what is on the card, as today's firmware reads them (`patch/lyrics.c` is the reference). The sources, in order: the sidecar beside the song, its path with `.lrc` for its extension (then `.LRC`), then the file's own tags as FFmpeg reads them (`lyrics`, any key starting `lyrics-` such as an ID3 USLT frame's `lyrics-eng`, else `UNSYNCEDLYRICS`, case aside); `source` says which answered, and only a text's first 64 KB is read. A text that is not UTF-8 and has no byte-order mark is converted from GB18030, as today's firmware does. A song that is a span of a file (a CUE track) takes its file's sidecar and tags; `lyrics` does not cut a file's lyrics to the track.

`kind` is `plain` when the text has no time stamps (every `at` is `-1`, the lines in the text's order), `line` when its lines are timed (`[mm:ss.xx]`, which may stand more than once before a line: the line is listed once for each, in time order, and lines under one stamp, as a translation is, keep the file's order with the same `at`), and `word` when any line also has enhanced-LRC word stamps (`<mm:ss.xx>` inside it). `at` is the line's start in milliseconds. `words` is empty (`words=`) for a line with no word marks; otherwise each mark is its time in milliseconds and the byte offset in the line's text (the percent-decoded UTF-8, stamps removed) where that word starts. Only a line with exactly one line stamp keeps its word marks. Tag lines such as `[ar:Ann]` and `[offset:0]` are dropped, not applied; blank lines are dropped, and spaces at a line's ends.

Which line is current, and how far its words are lit, is the caller's, as today's `patch/lyrics.c` does it (`lyrics_publish` and the page's painter): the current line is the last whose `at` is at or before the playing position; the lit width is interpolated between word marks; the last word lights at 90 ms per letter, at least 800 ms, and is done by the next line's start.

**Not served yet** (designed only): lyrics from the network. Today's manual "Download Cover & Lyrics" and "Get Album Lyrics" (`patch/onlinematch.c`) ask `https://lrclib.net/api/search?track_name=T&artist_name=A`, take the first result's `syncedLyrics` and save it beside the song as a UTF-8 `.lrc` with a byte-order mark, and a card app's `lyrics=1` asks the same of its server. Q2LIB/1 would carry them as `lyrics fetch ID` and `lyrics fetch album=AL [artist=AA]`, answered `ok` at once, the outcome a `watch` event `lyrics id=ID state=saved|none|offline|busy|unwritable` (a later `lyrics ID` then finds the sidecar). `q2libd` has no TLS client yet; `q2scrobd`'s mbedTLS client is the model.

A command it does not know is `err command`; a filter or number it cannot read, `err argument`.

**A catalogue provider** stays a file, as it is today: an app is in the Library when its card folder holds `catalogue.tsv` and `credentials` ([q2libd: The server catalogue](../libd.md#the-server-catalogue)); Q2LIB/1 adds no way to write one.

### Conformance

`test/libd.py` runs `q2libd` on a stand-in card: `Music/Ann/First/01 Alpha.flac` (Alpha, Ann, album First, album artist Ann, 2001, track 1, a `cover.jpg` beside it), `Music/Bob/b.mp3` (Beta, Bob, album Second, track 7), `loose.wav` (no tags), each a second long; beside Alpha a sidecar `01 Alpha.lrc` (a tag line, one line with word stamps, one line with two line stamps), and Beta with an unsynced `lyrics` tag (an ID3 USLT frame); a card playlist `Road.m3u` (Beta, then Alpha); and the signed-in app `subsonic` with two songs (`Server<tab>Song` by Sam on Sam's Live, 1999, disc 1, track 3, 200 s, Rock; Other by Sue on B-Sides, track 1, 100 s, Pop) and its playlist `Mix` (the first). Each block is replayed on a connection of its own.

```conformance
> Q2LIB/1
< Q2LIB/1 ok
> caps
< caps songs song albums artists genres playlists playlist search art watch lyrics
> songs
< song id=3 title=loose artist= albumartist= album= genre= composer= year=0 track=0 disc=0 time=1 source=card
< song id=1 title=Alpha artist=Ann albumartist=Ann album=First genre= composer= year=2001 track=1 disc=0 time=1 source=card
< song id=2 title=Beta artist=Bob albumartist= album=Second genre= composer= year=0 track=7 disc=0 time=1 source=card
< song id=1073741824 title=Server%09Song artist=Sam albumartist=Sam album=Live genre=Rock composer= year=1999 track=3 disc=1 time=200 source=subsonic
< song id=1073741825 title=Other artist=Sue albumartist= album=B-Sides genre=Pop composer= year=0 track=1 disc=0 time=100 source=subsonic
< ok
> songs from=1 count=2
< song id=1 title=Alpha artist=Ann albumartist=Ann album=First genre= composer= year=2001 track=1 disc=0 time=1 source=card
< song id=2 title=Beta artist=Bob albumartist= album=Second genre= composer= year=0 track=7 disc=0 time=1 source=card
< ok
> songs artist=sam
< song id=1073741824 title=Server%09Song artist=Sam albumartist=Sam album=Live genre=Rock composer= year=1999 track=3 disc=1 time=200 source=subsonic
< ok
> songs source=card genre=Rock
< ok
> song 2
< song id=2 title=Beta artist=Bob albumartist= album=Second genre= composer= year=0 track=7 disc=0 time=1 source=card
> song 99
< err none
> songs count=x
< err argument
> songs year=2001
< err argument
> songs Ann
< err argument
> shuffle
< err command
```

```conformance
> Q2LIB/1
< Q2LIB/1 ok
> albums
< album name=First artist=Ann year=2001 songs=1 first=1
< album name=Second artist=Bob year=0 songs=1 first=2
< album name=Live artist=Sam year=1999 songs=1 first=1073741824
< album name=B-Sides artist=Sue year=0 songs=1 first=1073741825
< ok
> albums artist=Bob
< album name=Second artist=Bob year=0 songs=1 first=2
< ok
> artists
< artist name=Ann albums=1 songs=1
< artist name=Bob albums=1 songs=1
< artist name=Sam albums=1 songs=1
< artist name=Sue albums=1 songs=1
< ok
> genres
< genre name=Pop songs=1
< genre name=Rock songs=1
< ok
> search SONG
< song id=1073741824 title=Server%09Song artist=Sam albumartist=Sam album=Live genre=Rock composer= year=1999 track=3 disc=1 time=200 source=subsonic
< ok
> search server%09
< song id=1073741824 title=Server%09Song artist=Sam albumartist=Sam album=Live genre=Rock composer= year=1999 track=3 disc=1 time=200 source=subsonic
< ok
> search b
< song id=2 title=Beta artist=Bob albumartist= album=Second genre= composer= year=0 track=7 disc=0 time=1 source=card
< song id=1073741825 title=Other artist=Sue albumartist= album=B-Sides genre=Pop composer= year=0 track=1 disc=0 time=100 source=subsonic
< ok
> art 1
< ~ art id=1 file=/mnt/mmc/\.vamp/covers/[0-9a-f]{8}\.jpg
> art 2
< err none
```

```conformance
> Q2LIB/1
< Q2LIB/1 ok
> playlists
< playlist id=1 name=Road songs=2
< playlist app=subsonic pid=pl1 name=Mix songs=1
< ok
> playlist id=1
< song id=2 title=Beta artist=Bob albumartist= album=Second genre= composer= year=0 track=7 disc=0 time=1 source=card
< song id=1 title=Alpha artist=Ann albumartist=Ann album=First genre= composer= year=2001 track=1 disc=0 time=1 source=card
< ok
> playlist app=subsonic pid=pl1
< song id=1073741824 title=Server%09Song artist=Sam albumartist=Sam album=Live genre=Rock composer= year=1999 track=3 disc=1 time=200 source=subsonic
< ok
> playlist id=7
< err none
> watch
< ok
```

```conformance
> Q2LIB/1
< Q2LIB/1 ok
> lyrics 1
< lyrics id=1 kind=word source=file lines=3
< line at=1000 text=Hello%20world words=1000:0,1500:6
< line at=2000 text=Again words=
< line at=4000 text=Again words=
< ok
> lyrics 2
< lyrics id=2 kind=plain source=tag lines=2
< line at=-1 text=Line%20one words=
< line at=-1 text=Line%20two words=
< ok
> lyrics 3
< err none
> lyrics 1073741824
< err none
> lyrics 99
< err none
> lyrics x
< err argument
```

A hello that is not `Q2LIB/1`:

```conformance
> Q2LIB/2
< err version
```
