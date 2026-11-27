# q2libd

Vamp OS's library service ([roadmap: Who owns what](vamp-os.md#who-owns-what)), its first slice: the card's songs and the server catalogues, scanned with no `demo`, into the two databases in the shapes `q2ui` and `q2playd` already read, so neither needed a change ([vamp-os-base](vamp-os-base.md)). Its second adds covers, the card's M3U playlists and duplicates between the card and a server. Full-text search, incremental scans by inotify, thumbnails, the servers' playlists, CUE, and the Q2LIB socket are later slices.

| What | Where |
| --- | --- |
| Source | `libd/q2libd.c`, `libd/Makefile` |
| In the image | `/usr/bin/q2libd`, the `q2libd` service (`os/package/q2libd`, `/etc/sv/q2libd`), linked against `q2player`'s own static FFmpeg 7.1.5 (its demuxers read the tags) and the image's SQLite |
| Tests | `test/libd.py` (this machine), `test/os_image.py`'s boot |

## The two databases

| File | What | Shape | Read by |
| --- | --- | --- | --- |
| `/mnt/data/vamp/library.db` | The card's songs | Stock's `songtable`, every column (`id`, `filename`, `fileurl`, `album`, `songer`, `style`, `name`, `composer`, `albumsonger`, `year`, `track`, `cd_num`, `time`, `time_create`, `Initial`, `bitdepth`, `samprate`); `vamp_files` (`fileurl`, `size`, `mtime`, `id`), what the next scan compares with; `vamp_cover` (`id`, `file`), each song's cover ([Covers](#covers)); and stock's `playlist` ([Playlists](#playlists)) | `q2ui --db`, `q2playd --db` |
| `/mnt/mmc/.vamp/catalogue.db` | The servers' songs | `servertable` with the same columns, as the payload's `cat.servertable` (`patch/catalogue.c`), less the ones that are card songs; `serverdup` (`id`, `fileurl`) names those ([Duplicates](#duplicates)) | `q2ui --catalogue` (attached as `cat`, `ui/lib.c`'s union) |

Decisions, by the roadmap's [User data](vamp-os.md#user-data) rule (a switch either way loses nothing, and nothing stock or today's Vamp owns is rewritten):

- **Stock's `/mnt/data/database.db` is never touched**, and neither is today's Vamp's `/mnt/mmc/.q2pod/catalogue.db`: both are files of their own. `q2libd`'s index is in `/mnt/data/vamp/`, the roadmap's place for everything new.
- **The catalogue is on the card**, as today's Vamp keeps its own: a 50,000-song catalogue is about 25 MB of rows, past what `/mnt/data` (17.7 MB) holds ([server-library: Where it is kept](server-library.md#where-it-is-kept)).
- **A song keeps its id while its path stays**: q2playd's queue and q2ui's state name songs by id. A file whose size and modification time are what the last scan saw is not opened again; its row is copied.

## The card

A walk of `/mnt/mmc` in name order, at most 16 folders deep, leaving out every dot folder and dot file (an app's, the system's: `.vamp`, `.q2pod`, `.<id>`) and every file whose extension is not one the player plays (`flac mp3 wav aif aiff m4a mp4 alac ogg oga opus ape wv wma aac ac3 dts dsf dff tak`). Each new or changed file is opened with libavformat and its stream info read: `title`, `artist`, `album`, `album_artist`, `genre`, `composer`, `date` (the year), `track`, `disc` (the leading number of `7/12`), the length, the sample rate and width. A song with no title is listed by its file name without the extension, as stock lists it; `Initial` is the title's first letter upper-cased or `#`, as `patch/catalogue.c` gives it.

## The server catalogue

Every card app folder `/mnt/mmc/.<id>` holding both `catalogue.tsv` and `credentials` (signed in: [server-library](server-library.md)), the first four in name order, each a slot as the payload's (`CAT_APPS`): its `s` rows become `servertable` rows with ids from `0x40000000 + slot * 0x01000000`, the path `/mnt/mmc/.<id>/cat/<album id>/<sid>.<suffix>` as the payload makes it, the card-app channel's `%XX` escapes undone. A catalogue whose header is not `#Q2CAT/1` with a `rows=`, or whose `s`, `p` and `i` lines do not add up to `rows=` (a file cut short), is left out whole. `p` and `i` rows (the servers' playlists) are counted and not yet kept.

## Covers

An album's cover is kept once, as `/mnt/mmc/.vamp/covers/<key>.jpg` on the card (not `/mnt/data`, which is small), the bytes as found (JPEG or PNG; `q2ui` reads either), at most 4 MB. `<key>` is `%08x` of the FNV-1a of the album artist (else the artist), a tab and the album, lower-cased; with no album tag, of the song's folder. The picture is the file's own (FFmpeg's attached picture), else the first of `cover`, `folder` and `front` `.jpg` or `.png` in its folder. It is looked for when a file is read; an unchanged file keeps its row. A cover file already there is kept, so a changed picture shows once its file is removed. `q2playd` copies the playing song's to `/tmp/externpic.jpg` for `q2ui` ([q2playd: The cover](playd.md#the-cover)).

## Playlists

Each `.m3u` or `.m3u8` in the card's `/mnt/mmc/_explaylist_data/` (stock's import folder), in name order, the first 100, becomes rows of `library.db`'s `playlist`, stock's table: a header row (`id` and `cd_num` the playlist's number from 1, `name` and `filename` the file's name less its extension), then its songs from `id` 101 on in the file's order, each its `songtable` row with `cd_num` the playlist's. An entry is a card path (`/mnt/mmc/...`) or one relative to the folder (`..` allowed, `\` read as `/`); a leading UTF-8 BOM, `#` lines and blank lines are skipped, and an entry that is no song in the library is left out. The numbering from 1 is this slice's own: stock's header ids are below 101 and its songs above, which this keeps. The folder's `.m3u` files are part of what the 10 s look compares, so a new or changed one is read at the next look.

## Duplicates

A server song that is already a card song is left out of `servertable`, so the Library lists the card's file once, and `serverdup` names the card file it was. The match is the payload's tags layer (`cat_same` in `patch/catalogue.c`): the same title, album and track, the same artist or album artist (case aside), and lengths within 2 s, both known. A length further off is another take and stays. Matched at each catalogue write, which now follows each card scan as well. The payload's MusicBrainz-id and same-file layers are not done.

## When it scans

At its start: an empty library first when there is none, so `q2ui` always has one to open. Then, looking every 10 s: when the card's line in `/proc/mounts` changes (inserted, removed, a remount) or a playlist file in `_explaylist_data/` does, the card again, and the catalogue after it (its duplicates); when a signed-in app's `catalogue.tsv` or `credentials` changes (size, time) or an app comes or goes, the catalogue again; and both at `SIGHUP`. Each database is written whole beside itself (`.new`) and renamed over it, so a reader never sees half a scan. After each write it runs `--after`'s command, in the image `sv hup /etc/sv/q2ui`: `q2ui` starts itself again in place ([hot reload](vamp-os-base.md#hot-reload)) and shows the new lists.

## Tests

`python3 test/libd.py` (pytest) builds `q2libd` for this machine against its own FFmpeg and SQLite and runs it on a stand-in card of songs `ffmpeg` makes: the tags, the no-title name, the length, dot folders and other files left out, stock's `database.db` byte for byte the same; a second scan reading nothing and keeping every id, a removed file gone, a changed one read again under its old id, a new one a new id; a signed-in app's catalogue with an escaped tab and both row shapes, a signed-out app and a cut-short catalogue left out; `q2ui`'s own union query over the two files; covers from a file's own picture and from its folder's `cover.jpg` byte for byte, none without, kept for unchanged files; playlists from two M3U files (BOM, comments, CRLF, a relative `..` path with backslashes, an entry not in the library) and a new one read by the running service within its look; a server song with the card's tags left out and named in `serverdup`, one 59 s longer kept; and as a service, an empty library and `--after` before the card is mounted, the card scanned once it is. Copies without the `rows=` check, without keeping ids, reading dot folders, not asking for `credentials`, without the attached picture, without keeping covers, without the path tidy, without the duplicate delete or without the playlist folder in the look each fail it (`Q2LIBD_SOURCE`). `test/os_image.py`'s boot: the image's `q2libd` scans the boot's card through FFmpeg under QEMU into `library.db`, and the song played over Q2PLAY/1 is that row; with the service replaced (`--perturb libd-off`) the scan check fails.

## What is missing

- Server songs play through their app by `q2playd`'s catplay ([q2playd: Server songs](playd.md#server-songs)); starting a stopped app for it and the offline copies are not done yet.
- Duplicates by MusicBrainz id or the same file, the servers' playlists (`p` and `i` rows), `catalogue.avail`'s offline copies, thumbnails of covers, a changed cover picked up without its file removed.
- Scans driven by inotify instead of a 10 s look, and progress for a long first scan: measured under QEMU only, not on a Q2's card.
