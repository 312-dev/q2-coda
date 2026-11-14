# Server library (design)

A planned library mode, **Local + Server**, in which a music server's songs (Navidrome or another
OpenSubsonic server) appear in the Library beside the card's own: Artists, Albums, Songs, Cover Flow
and playlists. When the Q2 is online every row plays; offline, rows that are not on the card are
greyed and only card files and cached copies play, and shuffle leaves the greyed rows out. **Local
only** stays the default and behaves as the Library does today. Nothing here is built yet; this file
is the plan, written so the work can start once the security and efficiency branches have merged.

It rests on two studies made while planning it: one of the Subsonic card app and of the firmware's
library, playback and card-app code, and one of how open-source Subsonic clients (Ultrasonic, Tempus,
Amperfy, Supersonic) model offline libraries and keep them in step with the server.

## What exists today

- **The Library is the card.** Stock's `songtable` in `/mnt/data/database.db` is written only by the
  card scan (`scanAllMusicFile`, `scanSpecFolder`, `deleteMusicFromMusicDb`); every other database
  write goes to favourites, playlists or play records. Update Local Music drops `songtable` and
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
s	<sid>	<title>	<artist>	<album artist>	<album>	<album id>	<year>	<disc>	<track>	<seconds>	<genre>	<suffix>	<local path or empty>
p	<pid>	<playlist name>
i	<pid>	<sid>
```

  `s` rows are server songs **not already synced** (a synced song is a card row; listing it again
  would show it twice). `p` and `i` rows are server playlists and their items in order.
- **Availability changes** without rewriting the catalogue: `/mnt/mmc/.<id>/catalogue.avail`,
  `<sid>\t<local path>` to add an offline copy, `-<sid>` when one is evicted.
- **Server state:** `server=up` or `server=down` in the app's existing `/tmp/q2-<id>.state`.
- **Answer:** `/tmp/q2-<id>.catalogue.done`, `<seq> ok <rows>` or `<seq> error <why>`, as the
  `library` extension answers.

### Core firmware changes

| Change | Where | Payload (estimate) |
|---|---|---|
| Import: read `catalogue.tsv` on a pthread when its `seq` changes; create `servertable` with `songtable`'s own columns (`create table servertable as select * from songtable where 0`) and a side table `servermeta(fileurl, app, sid, local)`; insert in one transaction through the payload's own SQLite connection, as Era does (`library_lists.c:371-374`); bump the library generation so open lists query again | new `patch/catalogue.c`, polled from `addon_poll` | 6-10 KB |
| Each server row's identity: `fileurl` = `/mnt/mmc/.<id>/cat/<album id>/<sid>.<suffix>`, a hidden folder the scan skips, stable across imports, so playlists, ratings, favourites and play counts key on it as on a card path | `catalogue.c` | in the above |
| Merge: `union all select * from servertable` inside `library_query` and in Era's SQL, only in Local + Server | `navigation.c:138-144` (where the query is built), `library_lists.c` | under 2 KB |
| Availability: an in-memory set of sids with an offline copy (from `servermeta.local` and `.avail`); `online()` from `networkGetConnectStatus()` cached once per poll, and the app's `server=` | `catalogue.c` | about 2 KB |
| Greyed rows: in `ringnav_style_color`, before the selected-row branches, a server row that is unavailable while offline takes the muted grey; a press on one says "Not available offline" | `navigation.c:6036-6062`, `coverflow.c:1468-1492` | 1-2 KB |
| Play: a press on a list containing server rows writes `/tmp/q2-<id>.catplay` (the list and the start row) when online; the app answers with its usual `.play` m3u. Offline, or with no answer in a few seconds, the firmware builds the queue itself from card rows and cached copies only | the Library's play-from-row path | 3-5 KB |
| Shuffle and connection loss: offline, shuffle filters greyed rows out before it orders the queue (the pattern of `no_videos`, `playback.c:886-898`); if the link drops mid-queue, `upcoming()` (`playback.c:782-803`) skips unavailable rows from then on | `patch/playback.c` | 1-2 KB |
| Guards where code assumes a file exists: Cover Flow art writes a "no art" marker instead of retrying a missing file (`coverflow.c:431-448`); durations use the catalogue's seconds; delete is refused; Long Songs, Go to Album and the info page read the catalogue's fields | `coverflow.c`, `durations.c`, `folder_delete.c`, `library_lists.c` | 2-3 KB |
| Setting: `[Q2POD] library_source`, 0 Local only (default) or 1 Local + Server, as a Library settings row | `pod_keys` (`navigation.c:3148`) | under 1 KB |

Total about 17 to 27 KB of the payload's 1 MB (`patch_bytes` in a build's `manifest.json` was 787 to
818 KB in this session's builds); all catalogue data lives on the heap.

### Compatibility

- **Local only** runs no union, no grey test and no import, and ignores catalogue files.
- **Local + Server** with no catalogue app installed behaves as Local only.
- Existing card apps see only a longer capability line; no existing file format changes.
- Turning the mode off stops the union and bumps the library generation. Whether the playlists the
  catalogue created are kept (as playlists of greyed songs) or removed is an open decision.

### The app's side

In the Subsonic app (another maintainer's repository, so a contribution there): a "Show in Library"
option; writing `catalogue.tsv` after each server check, streamed rather than held in memory, without
its 20,000-song cap; `catalogue.avail` when the Stream Cache changes; `server=up/down`; answering
`.catplay`; and, when the server is lost mid-queue, dropping uncached songs from its m3u so the
firmware does not wait on them.

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
  without a local copy is hidden; a playable-later one is greyed. (The clients studied all hide
  unavailable songs; greying is this design's choice, so a list keeps its shape offline.)
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

## Ratings and favourites

A song's hearts ([internals.md: Ratings](internals.md#ratings)) follow it to the server and back,
for server rows and for synced card files alike, whenever the Q2 can reach the server.

- **Mapping:** 1 to 5 hearts is OpenSubsonic's `userRating` 1 to 5, set with `setRating`; 0 hearts
  is `setRating` 0. Five hearts is also stock's My Fav, so five hearts stars the song (`star`), and
  taking the fifth heart away unstars it. A song starred on the server with no rating arrives as five
  hearts.
- **Which songs:** server rows by their sid; a synced card file by the sid the Subsonic app recorded
  when it downloaded it, so the app owns the path-to-sid table and the firmware never needs one.
- **Out:** each change on the Q2 is appended to `/mnt/mmc/.<id>/ratings.out`
  (`<listen_key>\t<sid or empty>\t<hearts>\t<unix time>`); the app sends it when the server answers
  and removes the lines it sent. Offline changes wait there, as plays and stars already do.
- **In:** on each connection the app reads `userRating` and `starred` from the walk it already makes
  (`search3` pages carry both) and writes the differences to `/mnt/mmc/.<id>/ratings.in`; the firmware
  applies them through the same path as a rating made on the device.
- **Conflicts:** the newer change wins, by the time the Q2 recorded it against the server's
  `starred` date where one exists; a rating has no server date, so a pending local change is sent
  before the server's value is read back.
- **Capability:** the app declares `ratings=1` in its `q2app`, and the firmware adds `ratings` to its
  capability line, as the catalogue does; without both, ratings stay on the device.

## Risks and open questions

1. **Greying stock's own lists**: getting from a stock list's row widget to its song record at paint
   time is not confirmed. Payload-built lists are straightforward.
2. **Scale**: stock builds a full in-memory row for every song on the UI thread. Import time, list
   open time and memory at 50,000 server songs must be measured on a device (128 MB of RAM).
3. **Code that expects a file** beyond the guards listed above: an audit of every path that opens
   `fileurl`.
4. **Connection loss mid-queue** needs both the firmware skip and the app change; until both exist,
   playback stalls at the first uncached song.
5. **Server deletions**: a song removed on the server but present in a Q2 playlist, a rating or play
   history.

## Effort

| Part | Effort |
|---|---|
| Catalogue format and protocol docs | 1 day |
| Import (`catalogue.c`, schema copy, transaction, answer, generation) with host tests in `test/fw` | 3-4 days |
| Union in `library_query` and Era, setting | 1 day |
| Greying: payload lists half a day; stock lists 1-3 days | 1.5-3.5 days |
| Playback hand-off and the offline queue | 2-3 days |
| Shuffle filter and mid-queue skip | 1-2 days |
| Guards for rows without files | 2 days |
| Device testing at scale | 2 days |
| The app's side | 3-4 days |

About three to four weeks of firmware work and one week in the app.
