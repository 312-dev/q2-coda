# Feature gaps

What Vamp lacks against the iPod lineage first, then Rockbox and present-day portable players, whether the Q2 can carry each feature, and where it would go. Written on 2026-11-13 against branch `oled/docs` (Vamp 1.8.x). A feature is listed as missing only after a search of `README.md`, [ipod.md](ipod.md), [internals.md](internals.md), [changelog.md](changelog.md) and `patch/`; the [Already in Vamp](#already-in-vamp) section lists what that search found, with where.

## Summary

Vamp covers almost all of the iPod classic's music side: Cover Flow, Shuffle Songs at the top of Music, ratings, Most Played, Recently Added, a live queue, synced lyrics, audiobooks with chapters and speed, podcasts, video and games. Its gaps are in three places. The iPod's automatic lists are missing: no Smart Playlists, no Genius or Genius Mixes, and no way to save the queue as a playlist (On-The-Go). The Extras menu is missing apart from games and the sleep timer: no alarm, stopwatch, world clock, calendar, contacts or combination locks. Accessibility and reach are missing: the interface is English only (the Language row is hidden, [changelog.md](changelog.md)) and there are no Spoken Menus or larger text. Two hardware limits shape every answer: the UI payload has a fixed 1 MB for code ([inefficiencies.md, entry 1](inefficiencies.md#1-the-ui-payloads-room-resolved)), so features that fit stay in `demo`, and anything large belongs in a card app ([internals.md, Card apps](internals.md#card-apps)), which can already show pages, play card files and queues, import playlists, set sleep timers and run full-screen games with sound. The hciplayer payload (`PLAYER_BASE`, `tools/peq.py:8`) has no comparable squeeze, so sound processing is cheap to add.

### Top 10 by value over effort

| #   | Feature                                                                                     | Effort | Value      | Where                                          |
| --- | ------------------------------------------------------------------------------------------- | ------ | ---------- | ---------------------------------------------- |
| 1   | [Save Queue as Playlist (On-The-Go)](#1-save-queue-as-playlist)                             | S      | high       | UI payload (about 1 KB)                        |
| 2   | [Crossfeed](#2-crossfeed)                                                                   | S      | medium     | player payload, plus one row in the UI payload |
| 3   | [Smart Shuffle](#3-smart-shuffle)                                                           | S      | medium     | UI payload (under 1 KB)                        |
| 4   | [Smart Playlists](#4-smart-playlists)                                                       | M      | high       | card app                                       |
| 5   | [Genius-style mixes](#5-genius-style-mixes)                                                 | M      | high       | card app                                       |
| 6   | [Alarm clock](#6-alarm-clock)                                                               | M      | medium     | card app, plus a small UI payload hook         |
| 7   | [Extras: Clock, World Clock, Stopwatch, Timer](#7-extras-clock-world-clock-stopwatch-timer) | S      | low-medium | card game                                      |
| 8   | [Photo slideshow](#8-photo-slideshow): dropped, Photos is retired                           | -      | -          | -                                              |
| 9   | [Lyrics timing offset](#9-lyrics-timing-offset)                                             | S      | low-medium | UI payload (under 1 KB)                        |
| 10  | [AirPlay 2 receiver](#10-airplay-2-receiver)                                                | L      | high       | card app                                       |

Items 1, 3 and 9 together come to under 3 KB of payload, which the 1.5 MB below `SCRATCH` ([inefficiencies.md entry 1](inefficiencies.md#1-the-ui-payloads-room-resolved)) holds.

### Quick wins (S effort, medium or high value)

- **Save Queue as Playlist**: S, high.
- **Crossfeed**: S, medium.
- **Smart Shuffle**: S, medium.

### Big bets (L or XL effort, high value)

- **AirPlay 2 receiver** (L): iPhone users' first way in, and the receiver stock ships is AirPlay 1 only.
- **Translations** (XL): the interface is English only; the Language row is hidden until other languages exist.
- **Spoken Menus** (XL): the iPod nano's and shuffle's voice feedback, for listeners who cannot see the screen. [ACCESSIBILITY.md](../ACCESSIBILITY.md) says there is none.

## Every gap considered

Support: **yes** the Q2 can do it as it is, **partial** with a limit named, **no** with the reason. Effort: S a day or two in one or two files, M several files or a new card app of modest size, L a new helper program or protocol, XL work across the whole interface.

| Feature                                  | Products                                                      | Q2 support                                                                                                           | Approach                                                                      | Effort | Value      |
| ---------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------- | ------ | ---------- |
| Save Queue as Playlist (On-The-Go)       | iPod classic, nano, mini; Rockbox (Save Current Playlist)     | yes: the queue and stock's playlist import exist                                                                     | a hold-menu row writes the queue as an M3U and imports it                     | S      | high       |
| Smart Playlists                          | iTunes, synced to every iPod; Rockbox (database filters)      | yes: the library is SQLite, ratings and plays are files                                                              | card app evaluates rule files, writes M3Us, asks for import                   | M      | high       |
| Genius and Genius Mixes                  | iPod nano 4G on, iPod classic from 2008; Sony SensMe          | partial: offline similarity only, or online over Wi-Fi                                                               | card app: tags, plays and co-listens, optionally ListenBrainz/Last.fm similar | M      | high       |
| Smart Shuffle                            | iTunes (Smart Shuffle), Rockbox (database filters)            | yes                                                                                                                  | weight `mcl_shuffle_pick`'s result by hearts and recent plays                 | S      | medium     |
| Crossfeed                                | Rockbox; Meier and Linkwitz headphone amplifiers              | yes: hciplayer filter                                                                                                | a Bauer-style filter beside Mono Audio in `peq_player.c`                      | S      | medium     |
| Alarm clock                              | iPod classic, nano (Extras > Alarms); Rockbox (wake-up alarm) | partial: wakes from Standby through the RTC, not from power-off; sound through headphones or the buzzer              | card app sets it; payload arms `wakealarm` before Standby                     | M      | medium     |
| Clock, World Clock, Stopwatch, Timer     | iPod classic, nano (Extras > Clock, Stopwatch)                | yes                                                                                                                  | one card game, full screen                                                    | S      | low-medium |
| Lyrics timing offset                     | the `.lrc` format's `[offset:]` tag, honoured by many players | yes                                                                                                                  | a per-song offset added in `lyrics_now`                                       | S      | low-medium |
| AirPlay 2 receiver                       | network streamers (WiiM, Bluesound); stock has AirPlay 1      | yes: Wi-Fi, CPU; needs nqptp's PTP ports                                                                             | card app: shairport-sync 4.x and nqptp, as librespot                          | L      | high       |
| Translations (Language)                  | every iPod; stock Q2                                          | partial: stock's languages exist, Vamp's own strings are C literals                                                | string table on the rootfs or card, looked up by key                          | XL     | high       |
| Spoken Menus / VoiceOver                 | iPod nano 4G on, iPod shuffle 3G on                           | partial: one DAC stream, so speech must pause or duck music                                                          | pre-rendered or on-device TTS clips, played through a helper                  | XL     | high       |
| Larger text                              | iPod nano 4G on (Large Text)                                  | yes                                                                                                                  | a font-size role per theme ([oled-design.md](oled-design.md#type))            | M      | medium     |
| Volume Limit with combination            | iPod classic, nano (Settings > Volume Limit)                  | yes: Maximum Volume is stock's                                                                                       | a four-digit lock on the Maximum Volume page                                  | S      | low-medium |
| Screen Lock with combination             | iPod classic, nano (Extras > Screen Lock)                     | yes                                                                                                                  | a code to leave Buttons Lock's Hold ([keylock.c](../patch/keylock.c))         | M      | low        |
| Music Quiz                               | iPod 4G and 5G (Extras > Games)                               | yes: card games with `audio=1` own the output                                                                        | card game playing random clips of card songs                                  | M      | low-medium |
| Calendar and Contacts                    | iPod classic, nano (Extras)                                   | yes                                                                                                                  | card app reading `.ics` and `.vcf` from the card                              | M      | low        |
| Main menu and Library list customisation | iPod classic (Settings > Main Menu), nano (Music Menu)        | partial: Home has a Shortcut row and three layouts                                                                   | per-row show and order keys for Library's rows                                | M      | medium     |
| Sound Check (iTunNORM)                   | every iPod with Sound Check                                   | partial: stock ReplayGain (Song, Album) works; iTunes' `iTunNORM` is not read                                        | read `iTunNORM` where no ReplayGain tag is, in the player payload             | M      | low-medium |
| Compilations menu                        | iPod classic, nano (Music > Compilations)                     | partial: album artist groups "Various Artists"; no compilation flag in stock's database                              | read `TCMP`/`cpil`/`COMPILATION` at scan, a Library row                       | M      | low-medium |
| Bookmarks (several per file)             | Rockbox, Sony Walkman (Bookmark List)                         | partial: Resume Songs keeps one place per file                                                                       | named marks per file beside Resume's record                                   | M      | low-medium |
| Listening statistics export              | iTunes play counts; Rockbox (database stats); Plexamp         | partial: play counts are 512 path hashes (`PLAYS_SLOTS`, `patch/offsets.inc:498`); `.scrobbler.log` has every listen | card app or desktop script summarising `.scrobbler.log`                       | S      | low-medium |
| Phone remote control                     | FiiO Link, HiBy Link                                          | yes: Wi-Fi; card apps serve web pages and QR codes                                                                   | card app with a Now Playing web page                                          | M      | medium     |
| UPnP/DLNA media server client            | FiiO Music and HiBy Music apps on Android players             | yes: stock DLNA is the renderer side (`g_dlnaflag`)                                                                  | card app browsing a server, downloading or streaming as Subsonic's does       | L      | medium     |
| SMB share playback                       | FiiO Music and HiBy Music apps on Android players             | partial: no CIFS in the stock kernel is known; userspace SMB works                                                   | card app syncing from a share, as q2-subsonic syncs                           | L      | low-medium |
| Qobuz                                    | FiiO, HiBy, Shanling M-series (Android apps)                  | partial: needs Qobuz's app credentials and its terms                                                                 | card app, as q2-deezer                                                        | L      | medium     |
| Dynamic range compressor                 | Rockbox, Sony (Dynamic Normalizer)                            | yes                                                                                                                  | a compressor stage in `peq_player.c`                                          | M      | low-medium |
| Clicker through the headphones           | iPod classic (Settings > Clicker: Headphones)                 | partial: Clicker's Headphones and Both, q2player mixing it into a song, or with none playing in silence of its own (in testing)      | the new player as the player (player-plan.md)                                 | M      | low        |
| iTunes library import                    | iTunes and Finder sync                                        | partial: M3U import exists; ratings and counts do not come across                                                    | desktop script: `Library.xml` to M3U and `.ratings.tsv`                       | M      | medium     |
| Voice Memos                              | iPod 5G and classic (with a mic accessory), nano 5G on        | partial: no microphone; the kernel has USB audio host support, so a USB mic might enumerate (not tried)              | card app recording through `arecord`                                          | L      | low        |
| MQA decoding                             | FiiO, HiBy, Shanling M-series                                 | no: stock's hciplayer never probes MQA ([internals.md](internals.md#mqa-detection)); the decoder is licensed         | none                                                                          | -      | low        |
| FM radio and Live Pause                  | iPod nano 5G on                                               | no: no FM tuner                                                                                                      | none (Internet Radio exists)                                                  | -      | -          |
| Shake to Shuffle, pedometer, Nike+       | iPod nano 4G on                                               | no: no accelerometer                                                                                                 | none                                                                          | -      | -          |
| Video recording, camera                  | iPod nano 5G                                                  | no: no camera                                                                                                        | none                                                                          | -      | -          |

## Shortlisted features

### 1. Save Queue as Playlist

**What.** The iPod's On-The-Go playlist: songs collected while browsing, then saved as a named playlist. iPod classic, nano and mini have it under Music > Playlists; Rockbox has Save Current Playlist.

**Already there.** Play Next, Add to Queue and View Queue build the queue, and Add to Playlist adds one item to a named playlist ([internals.md, Queue menu](internals.md#queue-menu)). What is missing is the last step: the queue, once built, cannot be kept.

**Support.** Yes. The queue is `*mcl_pdeqplaylist` (`0xa3a888`), each record's path at `REC_PATH`. Stock's `importPlayList` (`patch/addon_library.c:302`) turns an M3U in `/mnt/mmc/_explaylist_data/` into a playlist, replacing one of the same name; `ringnav_playlist_create` (`patch/playlists.c:383`) allocates a header if the playlist is built in the database instead.

**Approach.** A **Save Queue** row in the Play/Pause hold menu (its rows at `patch/navigation.c:9213`) and on View Queue. It writes the queue's local paths as `On-The-Go <n>.m3u` through `.tmp` and a rename, then imports that one file as `addon_library.c` does (its `only` list limits the import). CUE tracks and streamed (Tidal) records have no card path of their own and are left out. Code goes in `patch/playlists.c`, next to the editor.

**Effort and value.** S. High: every iPod owner knows On-The-Go, and building a queue then losing it at the next shuffle is the gap users meet first.

**Risks.** About 1 KB of payload. A library-wide queue (Shuffle Songs) is thousands of lines; cap it and say so in a toast.

### 2. Crossfeed

**What.** A filter that leaks a little of each channel, delayed and low-passed, into the other, so headphones sound less like two speakers inside the head. Rockbox has it (Sound Settings > Crossfeed, with Meier and custom modes, [Rockbox manual](https://www.rockbox.org/manual.shtml)), and Meier's and Linkwitz's headphone amplifiers do it in analogue.

**Support.** Yes. hciplayer's filter already runs every block through `peq_player.c`, and Mono Audio is the same shape of feature: a flag file demo writes, watched once a second of audio (`patch/peq_player.c:176`, `:480`).

**Approach.** A second watched flag beside `mono` at `patch/peq_player.c:480`, applying a Bauer/Meier crossfeed (one first-order low-pass and a short delay per side, 700 Hz and about 0.3 ms, at the stream's rate) after the EQ's sides. The row copies Mono Audio's (`mono_sync`, `mono_row`, `patch/crossfade.c:194`, `:207`). Bypassed while Mono Audio is on and for USB DAC or Bluetooth if wanted.

**Effort and value.** S. Medium: wanted by headphone listeners of old stereo recordings; inaudible to most others.

**Risks.** CPU at 768 kHz input is the only cost worth measuring; skip the filter above 192 kHz if it shows. A few hundred bytes of UI payload for the row.

### 3. Smart Shuffle

**What.** Shuffle that favours what you like and spaces out what you just heard. iTunes 5 to 8 had a Smart Shuffle slider that made songs by the same artist more or less likely to follow each other; Rockbox reaches rating-led shuffles through database filters.

**Support.** Yes. Vamp already routes every shuffle pick through `mcl_shuffle_pick` (`SHUFFLE_CALL`, `tools/build.py:422`; the caller at `patch/navigation.c:9540`), hearts are in `ratings.c`, and play counts are loaded by `plays_load` (used at `patch/library_lists.c:98`).

**Approach.** A **Shuffle: Smart** choice beside Songs and Albums. At each pick draw two or three candidates from stock's unplayed pool and keep the best by hearts (five hearts first, zero hearts last) and by distance from the last artist; return the others to the pool. The pool keeps stock's guarantee that every song plays once per pass.

**Effort and value.** S. Medium: people who rate songs get something back for it.

**Risks.** A pick on the UI thread must stay cheap: read hearts from the in-memory table, never the card ([inefficiencies.md entry 13](inefficiencies.md#13-ratings-compare-every-song-with-every-rated-one-resolved) says how the ratings lookup scales).

### 4. Smart Playlists

**What.** Playlists defined by rules (genre is Jazz, rating at least four, not played in 30 days, limit 50 songs) that refill themselves. Built in iTunes and synced to every iPod; the iPod classic and nano 7G keep live-updating ones.

**Support.** Yes, outside the payload. The library is SQLite at `/mnt/data/database.db` (Era already queries it with `sqlite3_exec`, `patch/library_lists.c:372`), ratings are `/mnt/mmc/.ratings.tsv` (`RC_FILE`, `patch/ratings.c:525`), every listen is in `/mnt/mmc/.scrobbler.log` (`patch/scrobble.c:9`), and play counts are FNV-1a path hashes in `/mnt/data/ringnav-plays` ([internals.md, Play counts](internals.md#play-counts)), which an app can compute the same way. A card app can ask for a playlist import (`/tmp/q2-<id>.library`, `patch/addon_library.c:1-11`).

**Approach.** A card app, `q2-smart`, with a rules file per playlist on the card (`Smart/<name>.rules`, plain text: field, operator, value, a limit and a sort). Its page lists the rules and a **Refresh** action; refresh evaluates each against the database and the three files, writes `_explaylist_data/<name>.m3u` and asks for `import`. A first version ships rules iTunes users know and Vamp's fixed lists lack: 90s Music, Top Rated by genre, Never Played, Not Played in a Year.

**Effort and value.** M. High: Smart Playlists are the iPod library's other half, and every iTunes user built some.

**Risks.** Writing rules on the device needs the text box (`field` rows exist in the card-app protocol); a desktop-written file is the simpler first step. The app reads `database.db` while stock may write it: open read-only, retry on `SQLITE_BUSY`.

### 5. Genius-style mixes

**What.** Genius builds a playlist of songs that go with the one playing; Genius Mixes are standing mixes per style. iPod nano 4G and iPod classic from 2008 ([Apple, 2008](https://www.apple.com/uk/newsroom/2008/09/09Apple-Introduces-New-iPod-nano/)); Sony's SensMe channels do the same by mood. Apple's version used iTunes' cloud data, which does not exist for a Q2.

**Support.** Partial. Offline, the card has what a mix needs: genre, year, album artist, hearts, and which songs are played near each other in `.scrobbler.log`. Online, ListenBrainz's and Last.fm's similar-artist data are reachable over Wi-Fi, and the user's scrobbling account is already set up (`patch/scrobble.c`).

**Approach.** A card app with two actions. **Mix from This Song** needs the playing song from the firmware: a small file written at each track change, as `patch/airplay.c` writes `/tmp/q2cast.track` for the sender, is the pattern (a few hundred bytes of payload). It then scores the library by shared artist, similar artists (online, cached on the card), genre, decade and co-listens, and plays the top 50 as an M3U through `/tmp/q2-<id>.play`. **Mixes** builds four to eight standing playlists by clustering genre and decade, imported as playlists. The heart integration (`fav`) lets the app learn from hearts given while a mix plays.

**Effort and value.** M. High: Genius was the iPod feature that made a large library playable without choosing.

**Risks.** "Genius" is Apple's trademark: name it Mixes or Discover. Online lookups need the network up (Smart Wi-Fi turns it off when idle, `patch/smart_wifi.c`), so cache results on the card.

### 6. Alarm clock

**What.** Extras > Alarms on the iPod classic and nano: a time, a repeat rule, and a sound or playlist. Rockbox has a wake-up alarm on targets with an RTC alarm.

**Support.** Partial. The X2000's RTC has a wake alarm ([hardware.md](hardware.md)), and Vamp already sets and clears it around Standby's suspend (`RTC_ALARM`, `patch/system.c:335`; `ringnav_standby`, `:353`). So the Q2 can wake from Standby at a set time; from power-off it cannot (whether the AXP2101 powers on from the RTC is not known). There is no loudspeaker: the alarm plays through headphones or a Bluetooth speaker, or beeps the buzzer (`buzzeer_switch`, gated at `patch/navigation.c:10304`).

**Approach.** The page and its settings in a card app (time, days, a playlist or folder, volume), saved to `/mnt/data/q2-alarm`. In the payload, `ringnav_standby` arms `wakealarm` at the earlier of the Standby limit and the next alarm; on a resume at the alarm's time it plays the chosen M3U, as a card app's `.play` does, ramping the volume, or beeps the buzzer if no output is connected. The sleep-timer path (`sleep_read`, `patch/addon.c:661`) is the pattern for a card app driving playback at a time.

**Effort and value.** M. Medium: a bedside Q2 on a speaker is a real use; most listeners carry a phone alarm.

**Risks.** Battery: none while asleep, since the RTC holds the alarm. A Q2 powered off by the Standby limit misses the alarm; Standby's limit should yield to a pending alarm. The buzzer is quiet.

### 7. Extras: Clock, World Clock, Stopwatch, Timer

**What.** The iPod classic's Extras > Clock (several cities' clocks) and Stopwatch (with laps), and a countdown timer.

**Support.** Yes. The clock and the time zones are already right ([internals.md, Clock](internals.md#clock)); a card game (`game=1`) runs full screen until it exits and takes the wheel and buttons (`patch/addon.c:39-43`).

**Approach.** One card game, `q2-extras`, with three screens: world clocks from the rootfs zoneinfo (the same `TZ` names `TIMEZONES` in `tools/build.py` uses), a stopwatch with laps (Center starts and stops, Next marks a lap), and a timer that pauses playback or beeps at zero. No firmware change.

**Effort and value.** S. Low-medium: nostalgia and the occasional kitchen timer.

**Risks.** A card game stops local playback only when it declares `audio=1`; this one should not, so music keeps playing.

### 8. Photo slideshow

**Dropped.** Photos, the viewer a slideshow would have stepped through, was retired from the firmware ([changelog.md](changelog.md)), so there is nothing on the Q2 to show photos with.

### 9. Lyrics timing offset

**What.** Shifting a song's lyrics earlier or later when an `.lrc` file is out of step. The `.lrc` format has an `[offset:]` tag for it, which many desktop and phone players honour.

**Support.** Yes. Every lyric line is placed from `lyrics_now` (`patch/lyrics.c:461`), so one added offset moves them all, word stamps included.

**Approach.** On the lyrics page, Previous and Next held (or the wheel in an edit mode) move the offset by 0.25 s with a toast; the offset is saved per song in the `.lrc` itself as the standard `[offset:+250]` tag (milliseconds), which other players read too. For tag-sourced lyrics the offset goes in a small table in `/mnt/data`.

**Effort and value.** S. Low-medium: LRCLIB downloads are usually in step; ripped or found `.lrc` files often are not.

**Risks.** Writing beside the song touches the card; `ringnav_lyric_save` already does so safely.

### 10. AirPlay 2 receiver

**What.** Receiving AirPlay 2 from an iPhone or a Mac: multi-room, Siri, and the iOS share sheet's newer path. Most network streamers (WiiM, Bluesound) have it. The Q2's stock receiver is Shairport 1.1.1, AirPlay 1 only ([internals.md, AirPlay](internals.md#airplay)).

**Support.** Yes. [internals.md](internals.md#airplay) already names the route: shairport-sync 4.x with nqptp, as an add-on on the card like librespot. The q2-librespot build ([librespot.md](librespot.md)) shows how: hard-float, linked against the stock glibc, playing through `aplay`.

**Approach.** A card app, `q2-airplay2`, built as librespot is, started from Streaming's AirPlay row in place of stock's service when installed. It uses the stock AirPlay page's DAC setup (`config_outputchannel`, PCM mode) as Spotify does, and the `now` capability for Now Playing's title and cover. `patch/airplay.c` already shares state with the q2-airplay sender; a receiver adds the reverse.

**Effort and value.** L. High: AirPlay is how an iPhone owner plays to a player, and AirPlay 2 adds multi-room and a steadier link than RAOP's.

**Risks.** nqptp needs UDP ports 319 and 320 and keeps timing traffic going, which costs battery while the service runs; start it only from the row, as Spotify does. shairport-sync and nqptp ship as card files, apart from the firmware, so their licenses are checked for the release zip, not for Vamp. AirPlay is Apple's trademark and this is not a licensed implementation; the app should say it is independent, as librespot's does for Spotify.

## Already in Vamp

iPod-lineage features Vamp or stock already has, with where. Stock means Shanling's V1.36 feature that Vamp keeps.

| iPod feature                                 | In Vamp as                                                                               | Where                                                                             |
| -------------------------------------------- | ------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Cover Flow                                   | Cover Flow, from Home and Library, with sort and letters                                   | `patch/coverflow*.c`, [internals.md](internals.md#coverflow)                      |
| Shuffle Songs at the top of Music            | Library's first row, Shuffle (songs, albums, folders)                                      | `patch/library_lists.c:72` (`shuffle_menu`), [internals.md](internals.md#shuffle) |
| Rating songs                                 | 0 to 5 hearts on songs and albums, Top Rated, Loved                                        | `patch/ratings.c`, [internals.md](internals.md#ratings)                           |
| Most Played, Recently Played, Recently Added | Most Played (Vamp), Recent and Recently Added (stock)                                    | `patch/library_lists.c:160`, [internals.md](internals.md#shuffle)                 |
| Artists, Albums, Songs, Genres, Composers    | stock lists, with Vamp's empty-list and album-artist fixes                               | `patch/navigation.c:8061`, [internals.md](internals.md#album-artists)             |
| Sort by, "The" ignored                       | Coverflow's Sort, Songs' and playlists' Sort; leading "The", "A", "An" skipped             | [internals.md, Sorting](internals.md#sorting)                                     |
| Now Playing upcoming queue                   | View Queue, Up Next, Play Next, Add to Queue                                               | `patch/upnext.c`, [internals.md](internals.md#queue-menu)                         |
| Playlists, editing on the device             | Edit Playlist, New, Delete, M3U import and export                                          | `patch/playlists.c`, [internals.md](internals.md#local-playlist-editing)          |
| Search                                       | Search with an iPod classic's letter strip; the wheel-walked keyboard elsewhere            | `patch/search_strip.c`, `patch/keyboard.c`, [ipod.md](ipod.md#search)             |
| Lyrics display                               | synced and word-timed `.lrc`, tags, LRCLIB download                                        | `patch/lyrics.c`, `patch/onlinematch.c`                                           |
| Sound Check                                  | stock ReplayGain, Song or Album (`replaygain_track` labelled Song), with Vamp's MP3 fix  | `tools/build.py:665`, [internals.md](internals.md#mp3-replaygain)                 |
| Equalizer presets by name                    | Simple's presets, Advanced PEQ, AutoEQ and Equalizer APO import                            | `patch/peq_ui.c`, [internals.md](internals.md#3eq)                                |
| Audiobooks, speed, chapters                  | `.m4b` and CUE books, resume, chapters, Speed per folder (stock's four steps)              | `patch/chapters.c`, `patch/playback.c:404` (`ringnav_speed`)                      |
| Podcasts                                     | Podcasts folder with resume; the Podcasts card app subscribes and downloads                | [internals.md](internals.md#podcasts-and-audiobooks), `sources.list`              |
| Sleep timer                                  | stock's Sleep Timer page; card apps' pause-at-time timer                                   | `patch/navigation.c:2888` (`ringnav_sleeptimer`), `patch/addon.c:616`             |
| Video                                        | Videos folder, played by `q2video`                                                         | `patch/video.c`, [internals.md](internals.md#videos)                              |
| Games                                        | Pop Stall, Cabin Solitaire, the Evening Puzzle, Caller Ten (a music quiz on the library), Ember Garden, and card games (games.c's Sudoku, Vortex, Solitaire and Snake held back) | `patch/games.c`, [internals.md](internals.md#games)                               |
| Clock in the status bar, time zones          | clock kept across power-off, every zone at its current offset                              | [internals.md, Clock](internals.md#clock)                                         |
| Battery percentage                           | Battery: Icon, Percent, Percent Inside                                                     | [ipod.md](ipod.md#status-bar-and-clock)                                           |
| Hold switch                                  | Buttons Lock: Off or On (the buttons; the dark wheel sets nothing)                         | `patch/navigation.c:2686`, [internals.md](internals.md#buttons-lock)              |
| Clicker                                      | Speaker (the buzzer, silent with headphones in), Headphones (mixed by q2player), Both, Off | [internals.md, Key Tone](internals.md#key-tone)                                   |
| Volume Limit                                 | stock's Maximum Volume                                                                     | [ipod.md](ipod.md#settings-icons)                                                 |
| Backlight timer, brightness                  | stock's Backlight settings, Smart Awake, Night Shift                                       | `patch/nightshift.c`                                                              |
| Main Menu customisation                      | Home's Split or Full and Edit Home's rows (part of the iPod's setting)                       | `patch/coverflow.c` (`coverflow_home_layout`), [ipod.md](ipod.md#home)            |
| Gapless and crossfade                        | gapless FLAC, MP3, AAC, Opus, Vorbis; Smart Crossfade, up to 15 s                                   | `patch/mp3_gapless.c`, `patch/lavf_gapless.c`, `patch/crossfade.c`                |
| Remote and headset controls                  | headset buttons, AirPods stem controls and ear detection                                   | `patch/headset.c`                                                                 |
| A-B repeat                                   | stock's A-B Repeat on Now Playing's More                                                   | [ipod.md](ipod.md#now-playing)                                                    |

From the other two yardsticks Vamp already has scrobbling (Last.fm and ListenBrainz, `patch/scrobble.c`), a folder library, favorites, PEQ, Spotify Connect (librespot), an AirPlay 1 receiver and an AirPlay sender, Internet Radio, Tidal (stock), Deezer, Subsonic and Soulseek card apps, Rockbox itself through dual boot (with its crossfeed, bookmarks and plugins, a reboot away), and from stock: Bluetooth receive mode, LDAC and aptX HD, USB DAC, DSD512, car mode and the DLNA renderer.

**Nearly called missing.** These turned up only on a second search: Composers (stock's list, missing from the Library rows `localmusic_page_init` names, yet handled by Vamp's empty-list toast, `patch/navigation.c:8070`); the sleep timer (stock's page, not a Vamp feature); ReplayGain album mode (stock's, labelled Song and Album); playback speed (stock's, kept per folder by Vamp); A-B repeat (stock's, on More); Recent, Vamp's equivalent of Recently Played (stock's row).

## Not possible on this hardware

| Feature                                               | Why                                                                                                                                          |
| ----------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| FM radio and Live Pause                               | No FM tuner: the parts list in [hardware.md](hardware.md) has none. Internet Radio covers listening to stations over Wi-Fi.                  |
| Shake to Shuffle                                      | No accelerometer in the device's parts list.                                                                                                 |
| Pedometer, Fitness, Nike+                             | No accelerometer; Nike+ also needed Apple's receiver dongle and service, both gone.                                                          |
| Built-in Voice Memos                                  | No microphone. A USB microphone through the OTG port might work (the kernel has USB audio host support), so this is listed as partial above. |
| Video recording, camera                               | No camera.                                                                                                                                   |
| Loudspeaker playback (alarm sounds, clicker on music) | The only speaker is the MCU's buzzer, which beeps and plays nothing else.                                                                    |
| USB keyboard or remote input                          | The stock kernel has no `hid-generic` ([hardware.md](hardware.md)); a module could add it, but Vamp ships the stock kernel unchanged.      |

MQA decoding is possible in principle but excluded: stock's hciplayer never reaches its MQA decoder, and the decoder needs a license ([internals.md, MQA detection](internals.md#mqa-detection)).

## Sources

- Apple, [iPod nano (4th generation) technical specifications](https://support.apple.com/112320): Genius, spoken menus, larger text.
- Apple Newsroom, [Apple Introduces New iPod nano, 2008](https://www.apple.com/uk/newsroom/2008/09/09Apple-Introduces-New-iPod-nano/): Genius, Shake to Shuffle.
- Apple Newsroom, [Apple Provides Volume Limit Setting for iPod, 2006](https://www.apple.com/newsroom/2006/03/29Apple-Provides-Volume-Limit-Setting-for-iPod/): Volume Limit and its combination.
- Apple, [iPod classic 120GB user guide (PDF)](https://cdsassets.apple.com/live/6GJYWVAV/user/ma630_ipod_classic_120gb_en.pdf): Extras (Alarms, Clock, Stopwatch, Screen Lock, Notes, Calendars, Contacts), Volume Limit.
- O'Reilly, [iPod: The Missing Manual, What's in the Extras Menu](https://www.oreilly.com/library/view/ipod-the-missing/9780596155834/ch02s08.html).
- Trusted Reviews, [Apple iPod nano 5th Gen review](https://www.trustedreviews.com/Apple-iPod-nano-5th-Gen-8GB-review): FM radio with Live Pause, pedometer, Voice Memos, video camera.
- Rockbox, [the Rockbox manual](https://www.rockbox.org/manual.shtml): crossfeed, bookmarks, lyrics, wake-up alarm.
- Headphonecheck, [FiiO Snowsky Echo Mini](https://www.headphonecheck.com/test/fiio-snowsky-echo-mini/): the retro-styled player's SBC-only Bluetooth and gaps between tracks, both of which Vamp is past.
