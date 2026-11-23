# Server library

A music server's songs (Navidrome or another OpenSubsonic server) in the Library beside the card's
own: Artists, Albums, Songs, Cover Flow and playlists. When the Q2 is online every row plays;
offline, rows that are not on the card are grayed and only card files and cached copies play, and
shuffle leaves the grayed rows out. There is no setting (the user's, 2026-11-19: "there is no
toggle, that just how it works"): while a card app such as Subsonic is signed in, its songs are in
the Library; signed out, they are not, and a Library with no app signed in is the card's alone. An
older build's `[Q2POD] LIBRARYSOURCE` (the Streaming Songs switch, Local Only by default) is never
read. The firmware's half is built
(`patch/catalogue.c`, [internals.md: Server library](internals.md#server-library)); the Subsonic
app's half is on its branch `feat/q2-catalogue`. This file is the contract between them and the
design behind both.

It rests on two studies made while planning it: one of the Subsonic card app and of the firmware's
library, playback and card-app code, and one of how open-source Subsonic clients (Ultrasonic, Tempus,
Amperfy, Supersonic) model offline libraries and keep them in step with the server.

## What exists today

- **The Library is the card.** Stock's `songtable` in `/mnt/data/database.db` is written only by the
  card scan (`scanAllMusicFile`, `scanSpecFolder`, `deleteMusicFromMusicDb`); every other database
  write goes to favorites, playlists or play records. Update Local Music drops `songtable` and
  rebuilds it.
- **Nearly every Library list goes through one SQL rewrite**, `library_query`
  (`patch/navigation.c:124-176`); Era builds its own SQL (`patch/library_lists.c:366-568`).
- **The Subsonic card app** ([andsuor/q2-subsonic](https://github.com/andsuor/q2-subsonic), MIT) is a
  Go daemon that talks to the firmware over the card-app socket and files ([internals.md: card
  apps](internals.md)). It syncs chosen items into `Subsonic/` on the card, which the scan then
  indexes, and it streams by downloading into its Stream Cache (`.subsonic/stream/`, hidden from the
  Library) and handing the firmware an ordinary file queue (`.play`, an m3u). It signs in over HTTPS
  with token and salt or an OpenSubsonic API key, reports plays to the server, and keeps a full
  server index (`library.json`) only for Sync Entire Library, up to 20,000 songs, reused while
  `getScanStatus` reports the same last scan and song count.

So a synced song is a card file and already in the Library; a streamed song never is.

## The design

### A catalogue extension, not a Subsonic client in the firmware

The firmware does not speak Subsonic. A card app that knows a server supplies a **catalogue**: the
server's songs that are not on the card, its playlists, and which songs have an offline copy. The
firmware imports it, shows it and plays it. Any future server app (Jellyfin, Plex) uses the same
channel, and an app that does not declare it is never read.

- **Declaring it:** `catalogue=1` in the app's `q2app`; the firmware adds the word `catalogue` to its
  capability line (`ADDON_CAPS`, `patch/addon.c:90-91`). An app writes nothing new unless it sees the
  word, so older firmware is unaffected.
- **The file:** `/mnt/mmc/.<id>/catalogue.tsv`, written whole (temp and rename), UTF-8, tab-separated,
  with the card-app channel's escapes:

```text
#Q2CAT/1	seq=<n>	account=<8 hex>	rows=<N>
s	<sid>	<title>	<artist>	<album artist>	<album>	<album id>	<year>	<disc>	<track>	<seconds>	<genre>	<suffix>	<local path or empty>	<size bytes>	<musicBrainzId or empty>
p	<pid>	<playlist name>
i	<pid>	<sid>	<card path or empty>
```

  `s` rows are server songs **not already synced** (a synced song is a card row; listing it again
  would show it twice). `p` and `i` rows are server playlists and their items in order; an `i`
  row's card path is a synced song's file, which has no `s` row.
  `<size bytes>` is the server's `size` and `<musicBrainzId or empty>` its OpenSubsonic
  `musicBrainzId` (the recording's id), both for the duplicate rule below. `<local path>` is a
  complete offline copy on the card (the Stream Cache), empty when there is none.
- **Availability changes** without rewriting the catalogue: `/mnt/mmc/.<id>/catalogue.avail`,
  `<sid>\t<local path>` to add an offline copy, `-<sid>` when one is evicted. The app appends
  lines; the firmware applies them in order, reading on from where it stopped while the file
  grows, and reads it whole again when it shrinks or a new catalogue is imported (each line sets a
  state, so reading one twice is harmless). The app may truncate it when it writes a new
  `catalogue.tsv`, whose local paths are then current.
- **Server state:** `server=up` or `server=down` in the app's existing `/tmp/q2-<id>.state`. With
  neither, the server counts as up while Wi-Fi is connected.
- **Answer:** `/tmp/q2-<id>.catalogue.done`, `<seq> ok <rows>` or `<seq> error <why>`, as the
  `library` extension answers. `<rows>` counts the `s`, `p` and `i` rows read, as the header's `rows=` does.
- **Play request:** `/tmp/q2-<id>.catplay`, written whole by the firmware when a list with server
  rows is played online: `<seq> <start>` on its first line, then one line per row of the list in
  its order, a server row's `fileurl` or a card file's path. `<start>` counts those lines from 0.
  The app answers with its usual `.play` (`<seq> <m3u>`, the seq the catplay's): an m3u of card
  paths from the row at `<start>` on, Stream Cache copies for server songs, each waited for until
  it is there. A `.play` that changes within `CATPLAY_WAIT_MS` (4 s) is the answer; otherwise the
  firmware plays the list itself from card rows and offline copies.
  Shuffle Songs (and every shuffle through `shuffle_play`: an album's, an artist's, a playlist's,
  Car Remote's) hands the app its list already shuffled, its first song the one drawn and `<start>`
  0, as the app plays the catplay's order (`library_lists.c` `shuffled_for_app`).
- **Starting the app:** an app that isn't running when a list is played online (none since boot,
  or gone after its own idle exit) is started as its Streaming row starts it, the capability line
  written first, and the catplay is written once the app's `/tmp/q2-<id>.browse.sock` is there:
  an app takes a `.catplay` present at its start as already answered. The 4 s wait runs from the
  write; an app that isn't listening within 15 s (`CATPLAY_START_MS`) has its list played by the
  firmware. Nothing is started for a signed-out app, offline, or for an app whose binary is gone; the
  app's own idle exit stands, and the next press starts it again. With Smart Wi-Fi's radio off
  (after the app's idle exit nothing keeps it on) the rows still count as online: a press wakes
  the radio and starts the app once the link is up, or plays the list offline when no link comes
  within 15 s.

- <a id="covers"></a>**Covers:** `/mnt/mmc/.<id>/covers/<album id>.jpg`, written whole (temp and rename) by the app
  whenever it has one: the cover of an album the catalogue has `s` rows of, a baseline JPEG of at
  most 160 px a side (Cover Flow's texture; the Library's rows and grids are smaller). The
  firmware finds it by the album id in a server row's path, or in the path of that row's offline
  copy, or of a song's download in the app's stream cache (`.../stream/.../<title> [<sid>].<suffix>`,
  which no `catalogue.avail` line names: the sid in its name is the row's, `patch/stream_path.h`), and uses it where it would use a card album's cover: the Library's album rows and grids
  (`grid_cover`, through stock's thumbnailer), Cover Flow's carousel (its art cache, built from
  it), Now Playing (`coverflow_player_cover`, after the file's own art and folder images), and
  under OLED the app's own artist page's Albums, Singles & EPs, Top Songs and All Songs
  ([oled-design.md](oled-design.md#artist-page)), by the album id in the row's `album:` context.
  Until it is there the placeholder shows; nothing waits for it. Once the app has every album's 160 px cover it also
  fetches each at 512 px into `covers/512/<album id>.jpg` (the same baseline-JPEG rule, a budget of its own), which only
  the car remote reads, for a phone that asks for more than 160 px ([internals.md: Car remote](internals.md#car-remote)). A server playlist's cover (its
  `coverArt`, at the same size) is `covers/pl-<pid>.jpg`, which OLED's Playlists rows show
  ([oled-design.md](oled-design.md#playlists)), as do Cover Flow's playlist covers (as the playlist's picture when it has
  none of its own, before the 2x2 of its albums: [internals.md: Playlist art](internals.md#playlist-art)) and the car
  remote's Playlists rows ([remote-protocol.md](remote-protocol.md#playlist-covers)). The app fetches it again when
  the server gives the playlist a new `coverArt` id (Navidrome's changes with its songs), keeping each file's id in
  `covers/.art`. The app keeps the folder within its own budget and deletes an album's cover when the album
  leaves the catalogue ([The app's side](#the-apps-side)).

### Duplicates

The catalogue leaves out only the songs the app synced. A song that reached the card another way
(copied over USB, sent over LocalSend, synced before the app kept track) and is also on the
server would show twice, so the firmware matches each server song against the card's songtable,
strongest evidence first:

1. **MusicBrainz recording id.** The server's `musicBrainzId` against the card file's own: a FLAC's
   or Ogg's `MUSICBRAINZ_TRACKID` comment, an MP3's ID3 `UFID` frame owned by
   `http://musicbrainz.org` (both are Picard's recording id; ID3's `TXXX:MusicBrainz Release Track
   Id` is a release track's id, not the recording's, and is not read). Equal ids are the same song.
2. **Same file.** Equal size in bytes, equal suffix, and durations within 1 s.
3. **Tags.** Equal normalised artist, album and title (lower case, punctuation and spaces dropped,
   a "feat." part and a trailing bracketed or dashed "Remaster", "Remastered", "Live"-free
   edition note such as "(Remastered 2009)" or "- 2012 Stereo Mix" cut), equal track number, and
   durations within 2 s.

A match on any layer hides the server row and links its sid to the card file (`servermeta.dup`):
the song plays from the card, and ratings sync by that sid. Layer 3 never matches on its own when a
stronger layer disagrees: both sides have MusicBrainz ids and they differ. A duration the firmware cannot read (taglib finds no length) fails layers 2 and 3.

The match runs at each import and again whenever the card's library changes (Update Local Music, a
scan after files arrive over USB or LocalSend: each moves `library_gen`), from the songs kept
in `serverall`, so a song copied to the card later leaves the server rows without a new catalogue.
Card files are read (their size, and their MusicBrainz id and length) only where a server song has
the same normalised title, or, for a card file with no title tag, the same size, so a large card is
not read whole. A size that differs is no disagreement (a retagged copy, new cover art), and the
tag layer's 2 s keeps a live take apart from the studio one.

### Where it is kept

The server songs are not in `/mnt/data/database.db`: that partition is 17.7 MB and a 50,000-song
catalogue is about 25 MB of rows. They live on the card, in `/mnt/mmc/.q2pod/catalogue.db`, which
the firmware attaches to stock's shared database connection (`stock_db_open`) and to Era's, as
schema `cat`, while a signed-in app's rows are in it and the card is in:

- `cat.serverall`: every catalogue song, with songtable's own columns, ids from `0x40000000`.
- `cat.servermeta(id, fileurl, app, sid, local, seconds, size, mbid, suffix, dup)`: what songtable
  has no column for. `local` is the offline copy, `dup` the linked card path.
- `cat.servertable`: the rows the Library shows, `serverall` less the duplicates, rebuilt after
  each match.
- `cat.serverpl(app, pid, name)` and `cat.serveritem(app, pid, sid, path)`: the catalogue's
  playlists and their items, in the file's order.
- `cat.serverlists(app, pid, list, twin)`: which stock playlist (`playlist`'s header id) holds each
  catalogue playlist; `twin` when it was the app's synced copy (below).

The job that imports a catalogue, or matches again, writes a whole new file beside it
(`catalogue.db.new`) on its own thread and connection; the UI thread then detaches the old file,
renames the new one over it and attaches it again, so no Library query waits on a long write.

### Playlists

Each catalogue playlist is a stock playlist (a header row, id 0 to 99, and its items), so Library >
Playlists lists it with the user's: a server song's item is its `servertable` row, a synced or
linked song's its card file's. A playlist the app also synced to the card as an `.m3u` (imported by
the [library extension](internals.md#card-apps)) is the same playlist twice; the firmware shows one,
the catalogue's: a stock playlist with the catalogue playlist's name whose items are that
playlist's card files in order is its twin, and the catalogue playlist takes the twin's place (its
id, its items replaced by the catalogue's, which also hold the server songs). A twin is only
unlinked, never deleted, when its playlist leaves the catalogue, since it is the user's synced
file's. A playlist of the user's with that name and other songs keeps the name, and the catalogue's
is left out. A catalogue playlist that leaves the catalogue is deleted. At most stock's 100
playlists.

#### Playlists from the Q2

A server playlist changed on the Q2 (OLED's Add Songs, Add to Playlist…, Remove, Rename, Delete;
docs/oled-design.md#playlists) goes back to the server through its app, when the app declares the
caps word `playlists` (Q2 Muse writes it beside `catalogue`). The firmware appends a line to
`/mnt/mmc/.<id>/playlists.out` for each change, in the card-app channel's escapes, as it appends
to `ratings.out` (`catalogue_playlist_sync`, `patch/catalogue.c`):

```text
set	<pid>	<sid>	<sid>...    its songs now, in their order, after an add or a removal
name	<pid>	<name>          renamed
delete	<pid>               deleted
```

The playlist is the one `cat.serverlists` maps the stock playlist to; a song's sid is its
`servermeta` row's (a server row, or a card file linked to one) or its `serveritem` row's (a
synced song's card path). A song with neither, a card file the server doesn't have, is left out of
`set`. A local playlist writes nothing.

The app takes the file by renaming it, as `ratings.out` (`playlists.out.sending` holds what is
left to send), and, while the server answers, sends each line in order: a playlist's last `set`
alone (`createPlaylist` with its `playlistId` and the first 200 song ids, which replaces its songs,
then `updatePlaylist` adding the rest 200 at a time), `name` as `updatePlaylist`'s `name`, `delete`
as `deletePlaylist`, and nothing else for a playlist the lines go on to delete. A playlist the
server no longer has, or won't change, is dropped; a lost server keeps the rest for the next
connection. Lines waiting run the connection's checks again at the next ping, so the server's
playlists are read back and the catalogue written with them, and the Q2's import then agrees with
what it already shows. ponytail: a change the Q2 makes before the app has sent the one before is
sent in order, but a catalogue the app writes in between still has the server's older playlist
until the next one.

### Core firmware changes

| Change | Where | Built |
|---|---|---|
| Import: `catalogue.tsv` read on a pthread when its `seq` changes, a line at a time, into a new `catalogue.db` (`serverall` with `songtable`'s own columns, `servermeta` beside it), in one transaction on the job's own SQLite connection; the header's `rows=` checked against the rows read; answered in `.catalogue.done`; the library generation bumped so open lists query again | `catalogue.c` (`cat_worker`, `import_file`), polled from `addon_poll` | yes |
| Each server row's identity: `fileurl` = `/mnt/mmc/.<id>/cat/<album id>/<sid>.<suffix>`, a hidden folder the scan skips, stable across imports, so playlists, ratings, favorites and play counts key on it as on a card path; `filename` the title alone, which stock's Search lists a song by | `catalogue.c` (`imp_song`) | yes |
| Duplicates: the three layers, again whenever `library_gen` moves (Update Local Music, the scans after USB storage and LocalSend), including a scan that ends while a job runs | `catalogue.c` (`cat_match`, `cat_same`, `cat_poll`) | yes |
| Merge: `union all select * from servertable` inside `library_query`'s music loads and in Era's SQL, only while a signed-in app's rows are indexed and the file is attached | `navigation.c` (`library_query`), `library_lists.c` (`era_sql`) | yes |
| Search: stock's Search (Library > Search, pull-to-search) runs `SearchMusicByName` and its kin, `... from songtable where <field> like '%<text>%'`, through `toolsQueryDbTable`, which is `library_query`, outside a music load; with an app signed in such a query reads the union, so server songs are found (Songs, Artists, Albums and Genres tabs). Pressing a result is stock's `search_track_row_on_click`, one of the `CATALOGUE_ACCESS` sites, so the offline rule holds; each tab's rows (one per record of `p_deque_searchshowlist`) are grayed as the rows below are: a song that can't play, an artist, album or genre none of whose songs can | `navigation.c` (`library_query`, three lines; `grey_rows`, `ringnav_search_rows`), `catalogue.c` (`catalogue_search`) | yes |
| Availability: an in-memory index of the server rows and linked card files (by path, and by sid), with each offline copy, from `servermeta.local` and `catalogue.avail`; online is `networkGetConnectStatus()` and no app's `server=down`, read once a second | `catalogue.c` | yes |
| Grayed rows ([below](#grayed-rows)): stock's pooled Library rows as `ringnav_table_load` binds them to `p_deque_showlist`'s records; the scroll_view lists whose rows are one record each in order (an album's songs on `albuminfo_page` and Go to Album's `playerjumpinfo_page`, Cover Flow's tracks, where Go to Album lands with Artist View on, Most Played's and the Ratings lists' songs, and each of Search's tabs) as their surface paints, and the OLED letter strip's results as it paints them (`search_strip.c`); a press on one fails stock's play check (`CATALOGUE_ACCESS`), which says File Not Found. An album row (Library > Albums, an artist's, composer's or genre's albums), and Search's artist, album and genre rows (the letter strip's artists and albums too, `catalogue_artist_unavailable`), are grayed when none of their songs can play: offline, no card song has its name and none of its server songs has an offline copy | `navigation.c` (`ringnav_table_load`, one call; `grey_rows`, `paint_offline`, `ringnav_style_color`), `catalogue.c` (`catalogue_bind`, `catalogue_album_unavailable`, `group_unavailable`, `catalogue_mark`, `catalogue_greyed`, `catalogue_fit`, `catalogue_mark_paint`) | yes |
| Play: a list with server rows played online writes `.catplay`, its app started first when it isn't running; the app's `.play` within 4 s is the answer, else the firmware plays the list itself from card rows and offline copies; offline it does so at once. Meanwhile Now Playing shows the pressed song ([below](#now-playing-while-it-loads)) | `playback.c` (`ringnav_load`), `catalogue.c` (`catalogue_load`, `catplay_poll`, `catalogue_np_now`), `addon.c` (`addon_wake`, `addon_ready`), `np_bar.c` (`np_sync`, two lines), `navigation.c` (`np_art`, two lines), `tags.c` (`id3_parses`) | yes |
| Shuffle and connection loss: offline, the queue loaded is the playable rows only, so Shuffle Songs (which plays the whole library through the same load) leaves the grayed out; mid-queue, `upcoming()` skips rows that can't play now | `playback.c` | yes |
| Covers: a server row's album cover is the app's `covers/<album id>.jpg` (the album id from the row's path, or from its offline copy's row), in the Library's album rows and grids, Cover Flow's carousel and Now Playing | `catalogue.c` (`catalogue_cover`), `coverflow.c` (`grid_cover`, `build_art`, `coverflow_player_cover`) | yes |
| Cover Flow: with an app signed in its album queries read the union (`cf_staged`), so the carousel has the server's albums; a server album's art is built from its app's cover once that is fetched (no job until then, so no "no art" marker); the centered album's captions are grayed with the offline mark while none of its songs can play | `coverflow.c` (`cf_staged`, `build_art`, `load_albums`, `caption_offline`, `caption_mark`) | yes |
| Guards where code assumes a file exists: durations use the catalogue's seconds | `durations.c` | yes |
| Play counts: a server song plays from its offline copy (the app's `.play`, or the firmware's offline queue), a Stream Cache file no Library row has, so a listen of a copy counts under its server row's `fileurl`; Most Played, a song's play count and Cover Flow's Most Played order follow the song | `navigation.c` (`play_count`, one line), `catalogue.c` (`catalogue_song_key`), `library_lists.c` (`mp_counted`) | yes |
| Delete Song | | not needed: a server row never plays under its own `fileurl` (the app's `.play` and the offline queue both hold card files), so Now Playing's Delete only ever sees a card file, a Stream Cache copy included |
| Go to Album and Go to Artist on a server row (the queue menu): the Library's own pages for the row's album and artist, never the app's (`goto_app` leaves a server row out, `addon_goes` offers Go to on it), so they work offline and list the album's card and server songs | `addon.c` (`goto_app`, `addon_goes`) | yes |
| Now Playing's info page for a server song, which plays from its Stream Cache copy (a file no `songtable` row describes): Genre and Year are the catalogue's, Location reads `Server` | `navigation.c` (`ringnav_np_info`, three lines), `catalogue.c` (`catalogue_np_info`) | yes |
| Long Songs for a server row | | not built: Long Songs reads `songtable` alone, so server songs are not in it |
| Signed in: the app's card folder holds its `credentials` file (looked for, never opened); only a signed-in app's catalogue is imported, such an app is started once at boot, and one that signs out (its credentials or its `catalogue.tsv` gone) has its rows taken out by a job with no seq, which answers nothing | `catalogue.c` (`cat_signed_in`, `catalogue_poll`, `catalogue_signed`, `cat_autostart`) | yes |
| No setting: the server library is always on for a signed-in app (2026-11-19); `[Q2POD] LIBRARYSOURCE` is never read, and apps that read `/tmp/q2-addon.library` (caps word `libsource`) find `server`; a `needslib` row is an action | `library_settings.c` (`library_source_file`), `addon.c` | yes |
| Ratings: `ratings.out` appended for every app with `ratings=2`, a `stars=` or `like=` line a change; `ratings.in` renamed to `ratings.in.taking`, applied, then deleted. Stars given on Now Playing while a server song plays from its Stream Cache copy are its server row's (kept under the row's key, the line with the row's sid, the copy's My Fav counted as the row's), and the Ratings lists read the server's songs beside every card song (`all_songs`'s `LIBRARY_ALL`) | `catalogue.c` (`catalogue_rated`, `catalogue_liked`, `catalogue_song_key`), `ratings.c` (`rate_song_key`, `rate_all`, `rated_open`), `navigation.c` (`library_query`) | yes |

The server library adds 43,600 bytes to the payload: `patch_bytes` is 864,504 of the then 1 MB in this branch's `--dev` build against 820,904 at the commit it branched from, `catalogue.o` being 39,870 bytes of text and 1,236 of data (`llvm-size`). All catalogue data lives on the heap or the card.

### Grayed rows

A row that can't play now (a server song without an offline copy while offline, or with Local
only; an album none of whose songs can play) keeps its place in the list and says so twice, so
it reads on any background:

- **Its text** is `#666666` off the selection. On the selection it is the selection's own ink
  (white on OLED's Accent Blue bar) at 70% alpha,
  never gray on the bar, so it keeps the contrast the theme chose for the bar.
- **The offline mark**, Phosphor's `cloud-slash` (bold, 20 px wide, 16 high), sits at the row's
  right in the same color as its text: in the chevron's column, or just left of the chevron on
  a row that shows one (a drill row's, or the selected album row's). It reserves its width and
  the 12 px a length keeps from the text: while the row is grayed its labels' text ends 12 px
  short of the mark's ink, so a long title ellipsizes or scrolls clear of it, and they take their
  width back when the row plays again. On OLED's one-line song rows the mark takes the right
  column's place: the length stands 12 px left of it, and the title and inline artist end 12 px
  short of whichever is leftmost.

Under OLED a song row that can play says where it lives in the same place: Phosphor's `cloud` for a
server row (it streams), `arrow-circle-down` for a card file in the app's downloads folder (its
`q2app`'s `downloads=`, q2-subsonic's `Subsonic`), nothing for the user's own file
([oled-design.md](oled-design.md#song-rows), [internals.md](internals.md#server-library)).

### Now Playing while it loads

Online, a pressed server song is the app's to fetch, and nothing loads until its `.play` (or
the firmware's own queue, 4 s on, or up to 15 s more while an app that wasn't running starts). Stock opens Now Playing at the press all the same, so it
shows the pressed song at once: its title, artist and album from the row, its place in the list
("2 of 12"), and the app's cover of its album (stock's placeholder until the app has fetched
one). The song that was playing stops, so its time and sound don't run on under the new title.
Stock's `player_start` would start the queue it had again after the load that loaded nothing, the
song playing or paused from its start until that stop reached the player, heard as a blip of the
paused song; the payload refuses that start (`patch/playback.c` `playback_start_refused`, from the
`mclStartPlayer` hook), so the song stays where it was until it stops.
Now Playing goes back to the player's song as soon as the player parses one (the pressed
song once it plays, or another the user picked), when nothing of the list could play, or after
60 s with neither (`CATPLAY_SHOW_MS`). The car remote's Now Playing follows the same song from the press ([remote-protocol.md](remote-protocol.md#a-streaming-song)). A Next or Previous while the pressed song is still to come
plays the song it moves to, and the pressed song, once it lands, stays on the card without
playing (`addon.c` `moved_on`).

Next on the last song queued while the app's next song is still downloading (a radio's first
minutes) moves Now Playing to that song at once, named and with its cover, the song playing
stopped and the spinner turning while it loads; it plays once the download is whole with stock's
player, or once enough of it is there with q2player, which plays a song still arriving
(`addon.c` `addon_next_waits`). Up Next lists the whole queue file, the songs not downloaded yet
dimmed with a cloud, and Center on one moves there the same way, `.playing` naming it so the app
fetches it first (`addon_pending_play`). Songs streamed into the queue take their server row's tags, or
their file's, as they join, so Up Next names them as the Library does (`tags.c`
`card_record_tags`).

### Compatibility

- With no catalogue app signed in there is no union, no gray test and no import, and catalogue files
  are ignored: the Library is the card's, as it was before.
- An older build's `LIBRARYSOURCE` key stays in `config.ini` unread.
- Existing card apps see only a longer capability line; no existing file format changes.
- Turning the mode off stops the union, detaches the file and bumps the library generation. The
  playlists the catalogue made are kept, their server songs grayed and refused ("Turn on Local +
  Server to play it"); turning it on again needs no new import.

### The app's side

In the Subsonic app (another maintainer's repository, so a contribution there): a "Show in Library"
option; writing `catalogue.tsv` after each server check, streamed rather than held in memory, without
its 20,000-song cap; `catalogue.avail` when the Stream Cache changes; `server=up/down`; answering
`.catplay`; and, when the server is lost mid-queue, dropping uncached songs from its m3u so the
firmware does not wait on them.

**Covers** (`covers/`, above): the app fetches them with `getCoverArt` (`id` the album id,
`size=160`), two at a time, while no sync runs and the stream has no song still to fetch (a list playing what it already fetched, or from the Stream Cache, does not stop it) or the Q2 charges, and the server answers, for at most
30 s a minute, through the network slot syncs and streams take first; a stopped round goes on at
the next. The folder holds at most 256 MB, each file counted as whole clusters of the card (about
8,000 albums with 32 KB clusters, 2,000 with 128 KB), and no cover is written while the card has
under 1 GB free. Albums are taken in the Library's order (by name), so the top of Albums fills
first. An album the server has none for (error 70, a 404, or a picture over 160 px) is noted in
`covers/.none` and not asked again; Show in Library off and Sign Out delete the folder. Measured
on the developer's Navidrome: a 160 px baseline JPEG of 3 to 6 KB, 50 to 350 ms each.

## Download Ahead and the streaming state

Status: built (p32): the app's half in q2-subsonic `feat/p32-prefetch`; here `patch/stream_state.c`, `patch/np_stream.c` and q2player's `player/grow.c`. A streamed song today is handed to the player only once it is
whole (`.part`, sync, rename; `m3u_songs` waits at a line until its file exists), the app fetches a
fixed two songs ahead in list order (`streamWindow`), and nothing tells the firmware how far a
download has got. Three files, all on tmpfs and written whole (temp and rename), and two
capability words close that. An app or firmware without them behaves as today.

**Capability words** (`/tmp/q2-addon.caps`): `ahead`, the firmware writes `.ahead` and reads
`.stream`; `grow`, the player plays a file that is still arriving (q2player only: written while
q2player is the player, never with stock's hciplayer, which would end the song at the bytes it
found).

**Download Ahead** is the firmware's setting: Streaming > Configuration, **Download Ahead**, Off, 1,
2 or 3 songs, 2 by default (`[Q2POD] AHEAD`, `streamconf_page`).

**`/tmp/q2-<id>.ahead`** (firmware to app). Written first just before the `.catplay` that hands the
app a list, for that catplay's seq (which the app's `.play` carries) with `n` and no lines, as the
firmware's queue holds none of the list yet (`stream_state.c` `ahead_start`), so the app never runs
a list on its default; then while the queue playing is that app's `.play` m3u: at the queue's
start, and whenever the songs after the one playing change in play order (a
skip, a jump in Up Next, shuffle or repeat changed, a song added or removed, the setting changed).
Not rewritten for position or pause.

```text
#Q2AHEAD/1	seq=<the .play's seq>	n=<0..3>
<card path>
```

One line a song: the songs the firmware's queue holds after the one playing, in its play order
(shuffle, repeat and Play Next applied, `playback_upcoming`), at most `n`, and only that app's own
lines. Its queue holds the m3u's lines up to the first one not there yet (or under `START`, below),
so it may name fewer than `n`: the rest are the m3u's songs after the one playing that are not
whole yet, in list order, the ones it queues next, which the app takes itself. Under Repeat All it
is always full: once the songs queued have all played it names Repeat All's next pass, songs already
whole, and the app gives a whole song behind the one playing no place of `n`, so the list's next
songs are still fetched (the drive of 2026-11-22: a radio under Shuffle and Repeat All stopped at its
fourth song, the fifth never fetched; `test/fw/test_stream_state.py`
`test_repeat_all_names_whole_songs_never_one_still_to_land`). `n=0` is Off: the
app fetches only the song playing. The song playing is not listed: it is `.playing`, as today. A
seq that is not the app's latest `.play` is ignored. With no `.ahead` (older firmware) the app keeps
two ahead in list order.

**What the app does with it.** Its plan is the song playing, then the `.ahead` lines in order,
then the list's songs not yet whole: `n` of them after the one playing, a whole song from before it
taking none; one download at a time, each started once the one before it is whole. A song whose file is already
whole (synced, or in the Stream Cache) is skipped over. When `.ahead` or `.playing` changes, a
download whose song is no longer in the plan is cancelled and its partial file deleted, and the
plan starts again from its first song not yet whole. Fetching needs the server reachable over the
Q2's Wi-Fi (its only link); Download Ahead never wakes Smart Wi-Fi's radio, which stays up while the
app runs (`addon_running`) as today. Space comes from the Stream Cache under its limit as now
(`trim`: played longest ago first), and the planned songs are protected (`protect`): never the song
playing or the next `n`. With the Stream Cache off, the songs more than two behind the one playing
are deleted, but not one the plan names to come.

**`/tmp/q2-<id>.stream`** (app to firmware and player). Written while a stream runs: at once on
each change of a song's state, and every 500 ms while a line is `get` (2 a second at most); not at
all while nothing moves. A file left with a `get` line and no write for 3 s is stale (the app died
or stopped): its `get` lines read as `fail`.

```text
#Q2STREAM/1	seq=<the .play's seq>
<state>	<bytes on the card>	<total bytes, 0 unknown>	<length ms, 0 unknown>	<card path>
```

One line for the song playing and one for each planned song, in plan order (tab-separated, the
card-app channel's escapes, path last). States: `wait` (planned, no byte yet), `get` (arriving),
`stall` (arriving, but no byte for 2 s), `done` (whole at its path), `fail` (given up; `server=down`
in `.state` says why when the server is gone). Total is the response's Content-Length, else the
server's size; length is the server's duration.

**Where the bytes are.** With `grow` the app writes a song in place, at the path its m3u line
names, so the player can open it while it arrives; without it, at `<path>.part`, renamed when
whole, as today (the `.stream` lines still count its bytes, for the display). An in-place file is
listed in `.subsonic/stream/.growing` while it is written, and the app deletes any file listed
there when it starts, so a file cut by a crash or a pulled card is never taken for a whole one.

**Starting before the end** (firmware, `m3u_songs`, with `grow`). A line whose file exists but whose
`.stream` line is `wait` or `get` with fewer than `START` bytes is waited for as a missing file is
today; `START` is 3 s of the song (total x 3000 / length), at least 256 KB, or the whole file when
total or length is unknown. A `stall` line waits the same. Then it is queued as any card file.
Gapless and the FLAC paths are unchanged: the next song is a file in the queue, whole or arriving.
ponytail: the stock player reads a file's length from its header (FLAC STREAMINFO, an MP3's Xing
frame); a CBR MP3 without one reads short while it arrives, so while a `.stream` line is not `done`
the firmware shows the server's length (the line's length, else the catalogue's), or `--:--` when
there is none ([internals](internals.md#streaming-configuration)).

**The player** (q2player, `source.c`). Opening a file under `/mnt/mmc/.<id>/`, it reads
`/tmp/q2-<id>.stream` for its path. A `get` or `stall` line wraps the file in a growing read: at the
bytes there are, it waits (polling `.stream` every 100 ms) rather than end. A seek searches only the
bytes there are, so one inside them plays from its target at once, and one past them waits there,
its position the target's, and plays from it as its bytes come. Once the song is playing, a stall waits for a
refill before it plays on: 3 s of the song ahead, then 6 s and 12 s at the stalls after, or the song whole
([internals](internals.md#streaming-configuration)). Its open of such a file analyses at most
100 ms of audio (`GROW_ANALYZE_US`): an embedded cover, which its FFmpeg cannot size, otherwise held the open
until 5 s of audio had come ([internals](internals.md#streaming-configuration)). While it waits it writes `/tmp/q2player.wait`, the path and a newline, and removes the file
once it plays again. `done`, `fail`, a stale file or no line: the end of the bytes is the song's end.

**What the firmware shows.** A server song is loading while it is the song Now Playing shows and
either it has not started (the pressed song, `catalogue_np_now`: the app's first `.stream` line) or
`/tmp/q2player.wait` names it (`np_stream.c`). ponytail: a queued song still short of `START` after
the one before it ended shows the ended song, not a spinner; the pressed song reads whatever
`.stream` holds, an older run's until the app's new run writes it.
Then Now Playing's times row shows the spinner and `<bytes> of <total> MB` from its `.stream` line,
and the car remote's NowPlaying says buffering (below). Read at Now Playing's 500 ms tick, and only
while a stream's `.stream` exists.

**Retry** (firmware to app): while a server song can't go on because the app says `server=down` or
`reach=down <code>` (the pressed song, or one q2player waits for), Now Playing shows why beside the
spinner for 10 s ("Server error 502 · trying again"), then why with Retry and Skip (`np_stream.c`).
The app waits at such a song rather than skip it, a proxy's 502, 503 or 504 included. Retry writes `/tmp/q2-<id>.retry` (`1` and a newline); the app's stream
run takes it by deleting it and tries every song still to fetch at once, its backoff or a lost
server's wait cut short (`takeRetry`). Skip is Next. An app without it leaves the file, and the
next Retry writes it again.

**After a restart** (firmware to app, q2app `resume=1`): Memory Playback restores only the songs
on the card, so a restored stream's others are asked of its app in `/tmp/q2-<id>.resume`,
`<seq> <index> <seconds> paused|playing` and then every saved song's card path a line. The app
answers `resumeack=<seq> <code>` in its state file and a `.play` of that seq whose queue file starts
with the song restored, which stays paused where it was while the songs after it download ahead;
Up Next lists every saved song meanwhile. An app without the word, or one that declines, leaves the
songs on the card ([internals.md](internals.md#memory-playback-and-a-streamed-queue)).

**The car remote** (`remote/coda-rc.proto`, `CAP_BUFFERING` = 8192): NowPlaying gains `buffering` (13, the
loading state above), `buffered_bytes` (14) and `total_bytes` (15) (the `.stream` line's, 0 when
not streaming) and `buffered_ms` (16) (bytes over total times length, 0 when unknown), sent when they change,
at most twice a second.

## Keeping in step with the server

From a study of Ultrasonic, Tempus, Amperfy and Supersonic (all GPL-3.0, so their ideas and not their
code), the OpenSubsonic specification and Navidrome's source. None of those clients keeps a fresh
full copy cheaply, and none uses the server's change hints to drive sync; the plan below is ours.

- **First copy:** page through every song with an empty `search3` query, 500 per request, writing each
  page to the card as it arrives (about 100 requests for 50,000 songs). The specification requires
  servers to accept the empty query for offline sync, and Navidrome returns songs in a stable order.
  Then playlists and starred items. Never one request per album: that is what overloaded servers and
  doubled songs for Amperfy ([#345](https://github.com/BLeeEZ/amperfy/issues/345),
  [#778](https://github.com/BLeeEZ/amperfy/issues/778)). One sync runs at a time.
- **Noticing a change:** on each connection, `getScanStatus`'s `lastScan` and `count` (Navidrome's
  fields), or `getIndexes` with `ifModifiedSince` on other servers. Either only says something may
  have changed: songs and albums carry no change date, Navidrome moves `lastScan` on every scan even
  when nothing changed, and its `getArtists` ignores `ifModifiedSince`.
- **Applying it:** a full walk again, marking every song seen; after a complete walk, a song not seen
  is gone from the server. The walk runs when the hint changed and the Q2 is idle or charging, and on
  a manual resync. Playlists' `changed` dates and starred items are checked on every connection, since
  a scan does not cover them.
- **Deletions:** Navidrome hides a missing file rather than deleting it and reuses its id if the file
  returns, so a song gone from the server is marked, not removed, and a downloaded copy of it is kept
  until the user clears it.
- **Two flags per song:** on the server (present, gone) and on the Q2 (none, partial, cached, pinned).
  A song plays if it has a complete local copy, or if the server is reachable and has it. A gone song
  without a local copy is hidden; a playable-later one is grayed. (The clients studied all hide
  unavailable songs; graying is this design's choice, so a list keeps its shape offline.)
- **Online means the server answers,** not that Wi-Fi is up: only connection failures (name lookup,
  refused, timeout) switch to offline; a sign-in error is shown, not treated as offline; after a
  playback error the app pings before deciding. A hotspot with no internet can still reach a server
  on that hotspot ([Tempus #1184](https://github.com/eddyizm/tempus/issues/1184)).
- **Connection lost mid-queue:** keep the queue, skip unplayable songs as playback reaches them, stop
  after a full pass finds none playable, and download the next song ahead so a short drop rarely
  interrupts.
- **On the card, not in RAM:** the catalogue and long queues live on the card (an estimated 10 to
  15 MB for 50,000 songs). Downloaded files are named by server id, not by artist and album: FAT
  refuses some names, such as a folder ending in a period (an Ultrasonic bug). Plays and stars made
  offline are queued on the card and sent when the server returns.

## Ratings and favorites

A song's stars and its like ([internals.md: Ratings](internals.md#ratings)) follow it to the server and back,
apart from each other, for server rows and for synced card files alike, whenever the Q2 can reach the server.

- **Mapping:** 1 to 5 stars (Now Playing's rating page, [ipod.md](ipod.md#rating-page), the one place to rate)
  is OpenSubsonic's `userRating` 1 to 5, set with `setRating`; 0 is `setRating` 0. The like (Now Playing's heart,
  stock's My Fav) is `star` and `unstar`. Neither touches the other: liking sets no stars, and five stars star
  nothing.
- **Which songs:** server rows by their sid; a synced card file by the sid the Subsonic app recorded
  when it downloaded it, so the app owns the path-to-sid table and the firmware never needs one. A
  server song playing from its Stream Cache copy (a file no Library row has, at a path that changes
  with the stream's quality) is its server row: its stars are kept under the row's `listen_key` and its
  line carries the row's sid; a like given on Now Playing is stock's My Fav of the copy, which the Ratings
  lists count as the row's. The other way round, a song starred on the server puts the row in My Fav, so
  Now Playing fills the copy's heart by the row (stock draws it from the copy's own path), and taking the
  heart away takes the row out (stock's toggle would put the copy in instead).
- **From the queue menu:** Favorite and Unfavorite on a song send a like line as Now Playing's
  heart does, its stars left alone, for a Library row and for the playing copy alike; Remove on a copy
  takes its row out of My Fav too. An album's Favorite sends a like line for each of its songs.
- **Out:** each change on the Q2 is appended to `/mnt/mmc/.<id>/ratings.out` as one line
  (`<listen_key>\t<sid or empty>\t<value>\t<unix time>`), `<value>` being `stars=N` (0 to 5) or `like=1`
  or `like=0`; the app sends it when the server answers and removes the lines it sent. Offline changes wait
  there, as plays and stars already do. `<listen_key>` is 8 lowercase hex digits: the FNV-1a 32 of the song's
  path (its bytes, then one more multiply by the prime, never 0; a CUE track's start hashed in after), which
  the app computes for its synced files to find their sids. `<sid>` is filled for a server row and for a card
  file the duplicate rule linked to one. Lines go to every app that declared `ratings=2`. An app that declared
  `ratings=1` takes one number, five for its star, so it could carry a like or stars only by changing the
  other: it is sent nothing until it declares `ratings=2`. The app takes the file by renaming it (to
  `ratings.out.taking`, whose lines it moves to `ratings.out.sending` until they are sent), so the firmware
  only ever creates or appends to `ratings.out`, and the app leaves out of `ratings.in` every song with a line
  still to send.
- **In:** on each connection the app reads `userRating` and `starred` from the walk it already makes
  (`search3` pages carry both) and writes the differences to `/mnt/mmc/.<id>/ratings.in`, whole
  (temp and rename), in `ratings.out`'s columns, a line for the stars and one for the like. The firmware
  applies `stars=N` by `listen_key` and `like=0|1` to My Fav by the song's path (a server row's or a linked
  file's from its sid, any other card file's found by its key in songtable), a server row's Stream Cache copy
  taken out of My Fav either way (a heart given to the copy on Now Playing is the copy's own path, so an unstar
  from the server would otherwise leave it full; a star puts the row in its place). A `ratings=1` app's line
  of one number is read too: five is its star, the like, the stars left as they are; under five is that many
  stars and no like. A line is skipped when the song's own `ratings.out` line of the same kind is newer (a line
  of one number counts as both). The file is then deleted. It waits while an import runs or the app's
  catalogue on the card is not yet imported (a boot's first poll), so a server row's path is in the index when
  its line is read.
- **Conflicts:** the newer change wins, by the time the Q2 recorded it against the server's
  `starred` date where one exists; a rating has no server date, so a pending local change is sent
  before the server's value is read back.
- **Capability:** the app declares `ratings=2` in its `q2app` (`ratings=1`: ratings in only), and the
  firmware adds `ratings` and `likes` to its capability line, as the catalogue does; without both, ratings
  stay on the device.
- **The app's side (q2-subsonic, not yet done):** `internal/catalogue/ratings.go` reads and writes one
  number (`Out.Hearts`; `send` stars at five and unstars below it, `Song.Hearts` reads a star as five). It
  needs the `stars=` and `like=` values: `setRating` for one, `star`/`unstar` for the other, `WriteIn` writing
  the two apart, and `ratings=2` in its `q2app`.

## Scale

Measured on the emulator (`~/q2-emu-server`: the build's own `demo` under `qemu-user` on a
laptop, so time is not a Q2's and memory includes QEMU's), with catalogues from
`test/server_catalogue.py` (made-up songs in albums of 12, five playlists, and the card's 24 FLAC
files listed again, which the size layer hides):

| Server songs | Import (file written to the new file swapped in, 1 s poll included) | `catalogue.db` | Library > Songs open |
|---|---|---|---|
| 100 | 1.0 s | 0.1 MB | under 0.5 s (124 songs) |
| 10,000 | 1.8 s | 4.8 MB | not measured |
| 50,000 | 9.8 s | 24.7 MB | about 0.5 s (50,024 songs) |

Cover Flow, with the developer's Navidrome signed in (9,199 songs, 6,175 albums with the card's): a cold open (the albums queried and split, art jobs listed) took 878 ms, of which `getAllAlbum` 524 ms, and building 6,169 albums' art from the app's covers about 20 s (some 330 a second) on the first open only; signed out, with the card's 5 albums, opens in tens of milliseconds.

demo's process (QEMU included) held 70 MB after boot and 96 MB after browsing the 50,000-song
Library; a Q2 has 128 MB, so this wants checking on a device. Every job copies the whole file
(the old tables into the new one), so a match after a card change costs about what an import does.

## Risks and open questions

1. **Graying** covers stock's pooled Library lists (bound in `ringnav_table_load`), song rows and
   album rows, and the one-record-per-row lists `grey_rows` knows (an album's songs, Cover Flow's
   tracks, Most Played, the Ratings lists, each of Search's tabs); a list built another way is not
   grayed, though its rows' play check still refuses. An album, artist or genre row is matched by its name, as the Library's lists
   group it, so a server-only album that shares its name with a card album (another artist's
   "Greatest Hits") is not grayed.
2. **Scale** on a device: the emulator's numbers (above) are not a Q2's, and 50,000 server songs
   took demo's process from 70 to 96 MB there, QEMU included. To be measured on a Q2 (128 MB).
3. **Code that expects a file** beyond the guards built: Long Songs leaves server songs out. Home's
   art and the status bar's play state follow the player, so they show the stopped song while a
   pressed server song loads.
4. **Connection loss mid-queue**: the firmware skips rows that can't play as playback reaches them;
   rows the app's `.play` already holds are the app's to drop.
5. **Server deletions**: a song removed on the server but present in a Q2 playlist, a rating or play
   history.
6. **Duplicates the match can't see**: a tagged card file is compared only with server songs of its
   normalised title, so a file whose title tag differs from the server's shows twice even at equal
   size. No wider rule is safe enough to add: the server reads its titles from the same tags, so a
   byte-identical file rarely differs by title; equal size, suffix and length across titles could
   merge two CBR MP3s of one length, whose sizes differ only by their tags; album, track and length
   without the title would merge editions that order a disc differently (the tag layer has no disc
   number); and the MusicBrainz id across titles means reading every card file's tags at each
   match. A missed duplicate shows twice; a wrong merge hides a server song.
