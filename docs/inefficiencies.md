# Inefficiencies

Problems found worth fixing, ranked by impact over effort, as read on branch `oled-theme` at 1.8.2; the `file:LINE` references are from that reading. Each entry has where it is, what shows it and what it costs, and its status in the table. A **resolved** entry's section says in **State** what the tree does and which check guards it. A **measured** one has the figures under **Measured** and a **Decision** to leave it. A **deferred** or **left as is** entry has a **Decision**: why not now, or why the cost is acceptable, and what would bring it back. An **open** one has a fix and an effort (S: an hour or two, M: a day, L: several days). An entry marked **unconfirmed** says what would confirm it.

The figures from a build are a `--dev` build's: its `manifest.json`, `stock.squashfs` and `rootfs.squashfs`. The Q2 is a 2-core Ingenic X2000 with 128 MB of RAM ([hardware.md](hardware.md)).

`ponytail:` in a comment or a doc marks a limit the code leaves on purpose, with the upgrade it names. It is on 237 lines of `patch/` and `tools/` (`grep -r ponytail patch tools | wc -l`); entries 7 to 17 below are costs the code marked so, 13 among them before it was resolved.

## Ranked

| #   | Problem                                                                        | Category  | Effort | Status     |
| --- | ------------------------------------------------------------------------------ | --------- | ------ | ---------- |
| 1   | The UI payload's room (resolved)                                               | fragile   | M      | resolved   |
| 2   | `tools/stock_sites.tsv` is not current, and nothing checks it (resolved)       | test gap  | S      | resolved   |
| 3   | No test resolves the doc anchors the sources cite (resolved)                   | test gap  | S      | resolved   |
| 4   | The theme picker's order and Status Bar's values (resolved)                    | fragile   | S      | resolved   |
| 5   | A build runs `unsquashfs -cat` once per file, hundreds of times                | build     | S      | measured   |
| 6   | Every build compiles all 55 objects one after another                          | build     | S      | measured   |
| 7   | Scrobble upload rewrites the log once a window                                 | runtime   | S      | left as is |
| 8   | Playlist art reads the playlist table once per playlist                        | runtime   | S      | deferred   |
| 9   | A folder's play loads each subfolder on the UI thread                          | runtime   | M      | measured   |
| 10  | A card app's import reads every song's tags on the UI thread                   | runtime   | M      | deferred   |
| 11  | Spotify's cover is decoded on the UI thread                                    | runtime   | S      | deferred   |
| 12  | A stock database write on the UI thread waits for a count                      | runtime   | M      | left as is |
| 13  | Ratings compare every song with every rated one (resolved)                     | runtime   | S      | resolved   |
| 14  | An EPUB's spine is quadratic in its items                                      | runtime   | S      | left as is |
| 15  | Tidal's Clear All Downloads unlinks the cache on the UI thread, under a lock   | runtime   | M      | left as is |
| 16  | A refused cover costs a process each                                           | runtime   | M      | left as is |
| 17  | A whole-library read holds a second copy of the library                        | runtime   | L      | deferred   |
| 18  | Every painted widget passes through about 25 painters                          | runtime   | M      | measured   |
| 19  | Nothing that needs a build runs in CI                                          | test gap  | L      | deferred   |
| 20  | `classic_light_*` art and the Classic face are named in C                      | fragile   | M      | deferred   |
| 21  | Tests unpack the themes by count (resolved)                                    | fragile   | S      | resolved   |
| 22  | Two source lists, a link list and a format list name the same files (resolved) | duplicate | S      | resolved   |
| 23  | `internals.md` is 832 KB in 1,752 lines                                        | docs      | M      | deferred   |
| 24  | The Hooks table lists 43 of 108 hooks (resolved)                               | docs      | S      | resolved   |
| 25  | `format.sh` formats 21 of the UI payload's 48 C files, and CI none (resolved)  | duplicate | S      | resolved   |
| 26  | The stock version is written in three places in `build.py` (resolved)          | fragile   | S      | resolved   |
| 27  | `DEMO_SHA` is recorded but never compared (resolved)                           | fragile   | S      | resolved   |
| 28  | The 10-accent limit's stated reason does not match the reader (resolved)       | docs      | S      | resolved   |
| 29  | `lockscreen.c` says the rootfs has no room (resolved)                          | docs      | S      | resolved   |
| 30  | A new theme's Corners default follows its index (resolved)                     | fragile   | S      | resolved   |
| 31  | `test/patch.py` is one 21,462-line file                                        | test gap  | L      | deferred   |
| 32  | C mutation testing reaches 7 files                                             | test gap  | L      | deferred   |
| 33  | The C coverage floors are 40% and 38%                                          | test gap  | M      | deferred   |
| 34  | Boot reads `config.ini` once per key                                           | runtime   | M      | measured   |
| 35  | A large library's Songs, Albums and Artists load and sort on every open        | runtime   | M      | measured   |

## Fragile hard-coding

### 1. The UI payload's room (resolved)

- **Where:** `tools/build.py:32` (`SCRATCH`), `check_layout` in `tools/build.py`, `patch/link.ld`.
- **State:** `SCRATCH` is `0xc00000`, so code and constants have 1 MB below it. demo's heap starts where the scratch state ends (a running demo's `/proc/<pid>/maps` shows one anonymous mapping from the payload's end through the heap), so nothing of stock's lies in the range the move took.
- **Watch:** `patch_bytes` in a build's `manifest.json` against `SCRATCH - BASE` (1,048,576).

### 4. The theme picker's order and Status Bar's values (resolved)

- **State:** each `theme.xml` states its `order`; `tools/theme.py` generates `theme_rows[]` and expands the Status Bar option's `<value themes="picker"/>` into every built-in theme in picker order, and refuses a duplicate or missing order. A new theme is one file.

### 20. `classic_light_*` art and the Classic face are named in C

- **Where:** 63 lines with a string literal `"classic_light_..."` or `"classic-light"`, in `patch/navigation.c` (32), `patch/classic_pages.c` (18), `patch/coverflow.c` (5), `patch/volume.c` (5), `patch/ratings.c` (2) and `patch/radio.c` (1) (`grep -c '"classic_light_\|"classic-light"' patch/*.c`, recounted at the hygiene pass). `validate` in `tools/theme.py` allows `art_final` only for a theme whose `art_id` is `classic-light`.
- **Evidence:** the C loads Classic's names literally. A theme's `art` attribute (`art_id` in `tools/theme.py`) names the theme whose generated images it draws, and OLED (`art="classic-light"`) draws Classic's through it.
- **Impact:** a theme cannot use the `classic-light` painters with art of its own.
- **Decision:** deferred. What is left is a theme with its own art under Classic's painters, and none exists. Then the fix is a per-theme art prefix in `theme_t`, written by `emit`, and a helper that joins it (M).

### 21. Tests unpack the themes by count (resolved)

- **Where:** `test/fw/test_nav_playing.py:16` (`MINIMAL, CLASSIC, MODERN = THEMES`), `:549-550` (`range(4)` Status Bar looks with four labels), `test/fw/test_coverflow.py:2362` (`range(3)`), `test/unit/test_theme.py:522` (`== [0, 0, 0]`), `:536` (`== 3`), `test/ui_preview.py:21-24` (three ids).
- **State:** `test/fw/test_nav_playing.py` and `test/fw/test_coverflow.py` look each theme, or its `index`, up by id in `host.tools_theme.builtins()`, so a theme added or moved fails no test that is not about it. `test/ui_preview.py`'s three ids are a review tool's arguments and `three()` in `test/unit/test_theme.py` a stated fixture: both stay. Guard: `test_coverflow.py`'s 503 tests, which fail in dozens when two of its ids are swapped.

### 26. The stock version is written in three places in `build.py` (resolved)

- **Where:** `tools/build.py` (`STOCK_VERSION`, the ZIP member `'Q2 Firmware V1.36/update.tar'`, `stock_header`'s `Q2POD_UA`), `pins()` in `tools/rebase.py`.
- **State:** `Q2POD_UA`, the contact sent with lyrics and cover searches (`patch/onlinematch.c`), is built from the module's `REPO`, never from `--update-repo`; `test_stock_header_versions` in `test/unit/test_build.py` pins it, including for a build with another `--update-repo`. The stock version stays written out: on a rebase `pins()` rewrites the ZIP member path (`text.replace(old.member, new.member)`) and the version text, so a new stock firmware needs no hand edit of either.

### 27. `DEMO_SHA` is recorded but never compared (resolved)

- **Where:** `tools/build.py` (`DEMO_SHA`; `build()`, after `raw_demo = cat('release/bin/demo')`).
- **State:** `build()` hashes the `demo` it read from the stock image and stops with `Unsupported demo: SHA-256 differs from the audited original` when it is not `DEMO_SHA`, so the manifest's `stock_demo_sha256` is a hash the build compared. `build()` is `# pragma: no mutate block`; the check was seen to stop a release build within a second with one hex digit of `DEMO_SHA` changed, and to pass with it restored.

### 30. A new theme's Corners default follows its index (resolved)

- **Where:** `settings_read` in `patch/theme_engine.c`.
- **State:** a missing `ROUNDED` key reads the live theme's Corners `preset` (`option_of(&themes[st.theme], SETTING_ROUNDED)`), Square where the theme states none, so a new theme's preset holds before it is picked. The built-in themes read as before: Minimal Round; Modern, Classic and OLED Square.
- **Guard:** `test_a_missing_rounded_follows_the_live_theme_s_preset_not_its_place` and `test_a_missing_rounded_reads_the_theme_s_corners` in `test/fw/test_navigation.py`.

## Build-time cost

### 5. A build runs `unsquashfs -cat` once per file, hundreds of times

- **Where:** `tools/build.py:1791` (`cat`), called in the window loop at `:1811-1815` and 24 more places (`grep -c 'cat(' tools/build.py` is 25), and per geometry set in `set_assets` (`tools/ipod.py:1289`).
- **Evidence:** the stock rootfs has 127 `raw/ui/*.bin` files (`unsquashfs -l stock.squashfs`), each read by its own process from a 50 MB LZO image, before any asset is patched; a `--dev` build's output holds 325 files.
- **Impact:** process start and a metadata walk per file, on a build that takes minutes.
- **Measured:** about 18 ms a call. cProfile of release builds on a 16-core machine put 1.6 s in `cat`'s 443 calls on a 273 s build, 3.0 s on a 136 s one under load from other jobs, and 1.5 s on an 82 s one; one whole `unsquashfs -n -q -d DIR stock.squashfs` takes 0.15 s.
- **Decision:** left as it is. The whole stage is below the 5 s that a build-speed change has to save, so one extraction (and `thirdparty.install` reusing it) cannot pay for the extra tree under `--out` and the symlink fallback `cat` would need.

### 6. Every build compiles all 55 objects one after another

- **Where:** `tools/build.py:1601-1602` (18 `NAVIGATION` files), `tools/peq.py:251-254` (30 `UI_SOURCES`, then the player's 4 to 7), each a blocking `run('clang', ...)`; `tools/build.py:1774` refuses an `--out` that holds `update.tar`, so nothing is reused.
- **Impact:** `navigation.c` is 11,223 lines and `coverflow.c` 4,824 at `-Oz`; one core does all of it on a machine with several. Reproducibility needs the same objects, not the same order of compiling.
- **Measured:** cProfile puts 8 to 9 s of a release build in clang (`compile_payload` with `compile_common`'s UI sources, then the player's and the two helpers'). With `NAVIGATION` and `compile_common`'s sources each compiled through a `ThreadPoolExecutor(min(8, os.cpu_count()))` and linked in list order, the seven manifest hashes were unchanged, and the span from `stock.h` written to `patch.elf` written went from 7.7 s to 4.6 s: `navigation.c` alone is most of it. Release build wall time was 60.0 s without and 60.8 s and 66.7 s with, inside the noise of a shared machine.
- **Decision:** left serial. About 3 s is below the 5 s (or 10%) a build-speed change has to save.

## Runtime cost on the Q2

Entries 7 to 17 are costs the code marks itself.

### 7. Scrobble upload rewrites the log once a window

- **Where:** `patch/scrobble.c:344-347`; [internals.md](internals.md#scrobbling).
- **Evidence:** its comment: "each pass rewrites the rest of the log under the lock, so card writes grow with the square of the backlog (a 3 MB log writes about 3 MB extra, 10 MB about 45 MB)".
- **Impact:** card wear and a long upload after weeks offline.
- **Decision:** left as is. Nothing is rewritten twice until the log passes `LOG_HEAD` (1 MB, about 7,000 scrobbles of some 150 bytes). Beyond that the extra writes are small beside one song file, and carrying an offset would mean one `retire` for the whole backlog: its `calloc(1, done + 1)` grows with the backlog, and a crash mid-upload re-sends all of it. The `ponytail:` stays.

### 8. Playlist art reads the playlist table once per playlist

- **Where:** `patch/playlist_art.c:151-153`.
- **Evidence:** "each job reads the table again for its own cd_num (no index), so a first build of N playlists reads it N + 1 times". `test/fw/messy_playlists.py` models 101 playlists over about 55,000 songs.
- **Impact:** the first build after a big import, on the worker, competing with the UI for the card.
- **Decision:** deferred. The reads are on the worker (`pl_worker`, below the player and the UI by `thread_lower`), for a first build only, and a typical library (ten playlists of some hundreds of rows) reads little. Each job's own read is also what names its 2x2 ("named by the songs this read", `pl_build`). Revisit if a profile on a device shows first builds of 50 or more playlists dominated by the reads; the fix is then the comment's: collect each playlist's first albums in the refresh's one pass (S).

### 9. A folder's play loads each subfolder on the UI thread

- **Where:** `patch/navigation.c:8266` (`folder_tracks`); [internals.md](internals.md#queue-menu).
- **Evidence:** "one toolsLoadDirectory per folder on the UI thread; a big tree takes a while".
- **Impact:** the UI does not answer until the walk ends. `qm_tracks` calls `ringnav_wdt_long()` first, so the watchdog counts `WDT_LONG_MS` (15 minutes) for that pass, not its usual span, and the walk does not reboot ([Watchdog](internals.md#watchdog)).
- **Measured:** the guest syscalls of one Add to Queue, taken with `Q2EMU_STRACE=1` in the emulator on a `--dev` build of `oled-theme` at `c056e7b4`, the library scanned (1,200 songs), from the key press to the frame with the menu gone. `Tree/` is 50 folders of 12 songs, `Flat/` 600 songs in one folder, each song the same 17 KB tagged MP3. Each folder is listed twice by `toolsLoadDirectory`: two `opendir` passes, each one `getdents64` with entries and one at the end.

  |                          | Tree (51 folders, 600 songs) | Flat (1 folder, 600 songs) |
  | ------------------------ | ---------------------------: | -------------------------: |
  | directory opens          |                          102 |                          2 |
  | `getdents64` calls       |                          204 |                          4 |
  | dirent bytes             |                       45,696 |                     38,496 |
  | opens of `.mp3` files    |                            0 |                          0 |
  | bytes read from songs    |                            0 |                          0 |
  | `database.db` operations |                            0 |                          0 |
  | key to changed frame     |                       0.52 s |                     0.47 s |

  The queue held both adds afterwards: the saved queue after Tree's add held 600 paths, `F01/S01.mp3` to `F50/S12.mp3` in order, and Now Playing showed "1 of 1200" after both. The times are the emulator's, with every syscall logged, the card in RAM: no figure for the Q2's card. The emulator's card had to be on tmpfs: on ext4 the host's 64-bit `d_off` cookies make the guest's 32-bit `readdir` stop after its first `getdents64`, so Folder showed nothing and a scan found no songs.

- **Decision:** the rule was 600 or more `.mp3` opens, or 600 x 4 KB read from them, for the 600-song tree (each a random card read, about 5 ms at SD High Speed, an estimate). The add opens no song and reads nothing from one; a song costs a directory entry and memory. Not worth fixing. What a tree costs is two directory listings per folder.

### 10. A card app's import reads every song's tags on the UI thread

- **Where:** `patch/addon_library.c:295`.
- **Impact:** the UI does not answer while stock's `importPlayList()` reads every listed song's tags, as stock's own Import row runs it. `addon_m3u_dir` calls `ringnav_wdt_long()` first, so the watchdog does not reset the Q2 during it.
- **Decision:** deferred. Moving it means proving stock's import safe on another thread, or replacing it; staging it as Update Local Music is staged is the fix (M).

### 11. Spotify's cover is decoded on the UI thread

- **Where:** `patch/spotify.c:443`: "the cover is sized on the UI thread, one decode a track; a worker if it stutters".
- **Impact:** a stall at each track change while Spotify plays.
- **Decision:** deferred. One `thumb()` a track, unmeasured, and the emulator has no Wi-Fi. It needs `tools/q2remote.py prof` on a device across a track change; worth Cover Flow's art worker (S) if the decode holds the UI more than 100 ms.

### 12. A stock database write on the UI thread waits for a count

- **Where:** `patch/coverflow.c:4857` (`count_worker`); [internals.md](internals.md#playlist-art) (the paragraph on the wait).
- **Evidence:** the doc: a write on the UI thread "that meets Home's or the update dialog's count ... a read of the whole `songtable` on their own connections, seconds on a big library" holds the UI.
- **Decision:** left as is. The fix proposed, a count once a scan, is what `home_counts` does: it starts a count only when `library_gen` differs from the generation it last asked at. What remains is a stock write that waits at most 500 ms a statement (`PRAGMA busy_timeout=500`, `stock_db_open` in `patch/playlists.c`) when it meets a running count.

### 13. Ratings compare every song with every rated one (resolved)

- **Where:** `rate_all` in `patch/ratings.c`, inlined into `rated_open`: once per Ratings list opened, on the UI thread.
- **Measured:** the scan compared each song with every favourite and kept slot, 12 MIPS instructions per entry that did not match (`-Oz`, `llvm-objdump` of `rated_open`). At 20,000 songs and 1,000 rated that is 240,000,000 instructions, about 480 ms at 5 x 10^8 a second.
- **State:** the favourites and kept slots are sorted once (`qsort`, by key above the hearts' three bits, then by their place in the old scan), and each song binary-searches its run and reads it in that order, so every song's hearts are the scan's. A search step is 16 instructions: about 180 a song at 1,000 rated, 3,600,000 for 20,000 songs, plus the sort (about 10,000 comparisons of an 11-instruction comparator through libc's `qsort`), under 10 ms in all.
- **Guard:** the `rate_all` cases in `test/fw/test_nav_library_pages.py` (favourite and kept, a damaged table's repeated keys, slots of 5 to 7 hearts, and 300 songs against a model of the scan).

### 14. An EPUB's spine is quadratic in its items

- **Where:** `patch/books.c:358`.
- **Decision:** left as is. The OPF is capped at `META_MAX` (1 MB), the scan is in memory on the Books worker and can be cancelled at each itemref, and each itemref then inflates a document, which dwarfs it. An id index, as the comment says, if big books prepare slowly.

### 15. Tidal's Clear All Downloads unlinks the cache on the UI thread, under a lock

- **Where:** `patch/tidal.c:203` (`tidal_clear`): "on the UI thread, holding the lock, a moment for a full 8 GB cache". `trim()`, the cache's size cap, runs on the download thread: `download` calls it.
- **Decision:** left as is. The `ponytail:` is on Clear All Downloads, one user action that unlinks the cache's files under the lock.

### 16. A refused cover costs a process each

- **Where:** `patch/coverflow.c:182`: "a process per cover; a resident decoder if first builds drag".
- **Impact:** first art builds of a library whose covers stock's decoder refuses (WebP, large PNG).
- **Decision:** left as is. The process is deliberate: these are the pictures stock's in-process decoder refuses, and the child is marked for the kernel to kill first (`child_oom_first`), so a picture that needs tens of MB costs ffmpeg, not demo. A resident decoder would trade that for a faster first build.

### 17. A whole-library read holds a second copy of the library

- **Where:** [internals.md](internals.md#play-counts) (Most Played's paragraph and the one after): "one copy per whole-library read is the floor ... the next whole-library read holds two".
- **Impact:** memory on a 128 MB device with a large library.
- **Decision:** deferred, L effort; the design is in the doc (Most Played ranks with `sqlite3_exec` over `fileurl` and fetches only the top rows). Revisit on an out-of-memory kill in a `q2-crash.log` from a large library.

### 18. Every painted widget passes through about 25 painters

- **Where:** `patch/navigation.c:2723-2772` (`ringnav_paint`, the hook on `widget_on_paint_border`).
- **Evidence:** the function calls `night_paint`, `coverflow_paint`, `keylock_paint`, `edit_paint`, `photos_paint`, `books_paint`, `spot_paint`, `addon_paint`, `peq_paint`, `battery_paint`, `visualizer_paint`, `games_paint`, `paint_chevrons`, `paint_radio`, `paint_into`, `paint_letter`, `paint_cover`, `paint_scrubber`, `slider_scrub`, `quick_scrub`, `search_over`, `paint_caps`, `hold_paint` and `coverflow_home_clip` for every widget stock paints, and each decides for itself whether the widget is its own.
- **Measured:** payload instructions of one `ringnav_paint` call, counted with a `UC_HOOK_BLOCK` hook over `BASE` to `SCRATCH` in a scratch copy of `test/patch.py` (the real MIPS payload under Unicorn; stock's paint and the mocks count nothing), on a `--dev` build of `oled-theme` at `c056e7b4`. The page is `folder_page` with eight rows from stock's own row constructor (`0x4b39f4`), laid out, after one whole paint. Every row's label and images, and the window, are owned by no painter:

  |                    | row label | row image | window |
  | ------------------ | --------: | --------: | -----: |
  | Minimal, Light off |     1,393 |     1,381 |  1,443 |
  | Minimal, Light on  |     1,398 |     1,386 |  1,448 |
  | Classic, Light off |     1,586 |     1,586 |  1,648 |
  | Classic, Light on  |     1,586 |     1,586 |  1,648 |

  Median and largest are the same in every cell, and a second call gives the same count as the first. A list with a scrollbar, which Classic's scrollbar painter owns, counts 3,150. The ten functions with the most instructions for one row label under Classic, Light on (1,586): `ringnav_paint` 298, `accent` 240, `theme` 135, `lockscreen_paint` 94, `kind` 57, `light_on` 54, `peq_paint` 45, `visualizer_paint` 45, `coverflow_paint` 44, `coverflow_home_clip` 44. A third of it is `accent`, `theme` and `light_on`, asked again by painter after painter.

- **Decision:** the rule: a full repaint of about 100 widgets in a 60 Hz frame of 8,300,000 instructions (5 x 10^8 a second, generous for the X2000), worth fixing at 2% of it, 1,600 instructions or more per unowned widget. The largest list page paints fewer than 100: about 77 row widgets (seven Songs rows of up to eleven in the table's 294 pixels), the page's own four and the status bar's 16. 1,586 is under it, by 1%; not worth fixing. Any painter added to the chain, or a lookup added to one, crosses the line: then the fix is one owner check before the chain, or `theme()` and `accent()` read once per call and passed down.

### 34. Boot reads `config.ini` once per key

- **Where:** `patch/theme_engine.c:8-15` (`config_value` calls stock's `toolsReadConfig` with the file's path each time); 22 call sites in `patch/*.c`, most in the two loops at `patch/theme_engine.c:178-192` and `patch/navigation.c:3181-3187`.
- **Measured:** one boot in the emulator with `Q2EMU_STRACE=1`, on a `--dev` build of `oled-theme` at `c056e7b4`, from the log's start to Home's first frame (the fifth flip): `config.ini` is opened 93 times (91 to read, one `O_RDWR` and one rewrite, stock's), all on demo's UI thread, 71 of them before stock prints `config_init end`. The file is 601 bytes, read whole with one 4,096-byte `read` per open. The span holds about 18,700 syscalls. The values are cached after the first read (`st.settings_read`, `st.pod_read`), so it is paid once a boot.
- **Decision:** the rule: one open, read and parse of a small cached file is about 0.1 ms on the Q2 (an estimate), worth fixing at 100 ms a boot, 1,000 opens or more. 93 opens, stock's included, is about 9 ms: not worth fixing.

### 35. A large library's Songs, Albums and Artists load and sort on every open

- **Where:** `patch/navigation.c` (`ringnav_localclass`, `songs_load`, `library_query`), `patch/coverflow.c` (`coverflow_split`), stock's `toolsQueryDbTable` and `toolsQueryDbTableAndSort`.
- **Measured:** the emulator on a `--dev` build with each phase timed in the payload (`clock_gettime` around each query, its row callback and the first `ringnav_sort_key` call; the first `widget_on_paint_background` after the load), a synthetic library of 20,103 songs, 2,011 albums and 202 artists, three opens of each list after a boot. Milliseconds, Local; the host's load moved whole runs by up to 2x, so each pair below was run back to back:

  | Open    | Phase                                                                   |              Before |              After |
  | ------- | ----------------------------------------------------------------------- | ------------------: | -----------------: |
  | all     | Centre's release to the load (the double-press wait, `DOUBLE_CLICK_MS`) |             250-270 |            250-270 |
  | Songs   | `getAllMusic(0)`: exec and row callback                                 |             220-530 |            225-270 |
  | Songs   | its name sort                                                           |         1,250-2,740 |        0 once kept |
  | Songs   | load end to first paint                                                 |              40-120 |              40-50 |
  | Albums  | `getAllAlbum`: exec, then its name sort                                 |       55-90, 65-100 | 55-80, 0 once kept |
  | Albums  | `coverflow_split`'s `ALBUM_SQL` grouping                                |             185-320 |          unchanged |
  | Albums  | the Unknown Album check (`class_query`)                                 |               32-50 |          unchanged |
  | Artists | the artists query, its sort; the Unknown check                          | 80-120, 8-13; 37-64 |          unchanged |

  With 10,000 server songs beside them (Local + Server), Songs' sort was 1,860-1,970 ms of a 2.4-2.6 s load; kept, the load took 470-530 ms. Albums' grouping was 330-350 ms there.

- **State:** the name sort's order is kept ([internals](internals.md#name-order)); the first open after a boot, a scan or a server import still sorts. Nothing repeats a query within one open.
- **Decision:** the rest left as is. Three indexes (the album grouping's three terms, `album`, and the album-artist expression) cut the grouping to 120-180 ms and both Unknown checks to under 1 ms, but cost 0.8, 0.5 and 0.4 MB of `/mnt/data` (17.7 MB) at 20,000 songs, more with real paths, and time at the end of every scan; worth it if `/mnt/data` moves to the card. The double-press wait is Centre's (a second press turns the screen off). A spinner during the sort would need the sort to yield, and it runs to its end inside stock's `toolsQueryDbTable`: only a sort of the payload's own, calling stock's comparator for the same order, could stop between steps for a main-loop pass. Keeping the order on the card as well would leave the sort to the first open after a library change.

## Duplicated tables kept in step by hand

### 22. Two source lists, a link list and a format list name the same files (resolved)

- **Where:** `NAVIGATION` (`tools/build.py:1594`), `UI_SOURCES` (`tools/peq.py:187`), the `--undefined=` names at `tools/build.py:1611-1615` (symbols stock's patched words call, beside `HOOKS`), `tools/format.sh:9` (three Cover Flow files by name), the table in `test/cmut/sandbox.sh:22-39`, and the suite list in `.github/workflows/firmware.yml:42` beside [building.md](building.md#tests)'s.
- **State:** `tools/format.sh` reads `NAVIGATION` and `UI_SOURCES` but `UNFORMATTED` (beside `UI_SOURCES` in `tools/peq.py`) in one `python3 -c`, so a new file in either list is formatted unless it is exempted by name (entry 25). Guard: `test_unformatted_names_ui_sources_files` in `test/unit/test_peq_tool.py`.
- **Not done, on purpose:** no CI step runs the formatter (a workflow edit cannot be proven from a local run). The `--undefined=` names in `compile_payload` are not derived: a missing one fails the build at once, when `build()` looks the symbol up, so it is not a silent miss. `test/cmut/sandbox.sh`'s table and the workflow's suite list stay by hand.

### 25. `format.sh` formats 21 of the UI payload's 48 C files, and CI none (resolved)

- **Where:** `tools/format.sh:8-9`: `NAVIGATION`'s 18 files, three headers and three Cover Flow files. `UI_SOURCES`' other 27 files of 30 (`coverflow.c`, `addon.c`, `spotify.c`, `books.c` and the rest) are not named. No workflow runs `clang-format` (`grep -n format .github/workflows/*.yml` finds only coverage's report format).
- **Measured:** `clang-format --dry-run` (18.1.8, which leaves upstream's formatted files unchanged) over `UI_SOURCES`: 24 of upstream's files do not conform, and never did: `coverflow.c` (441 warnings), `peq_ui.c` (312), `peq.c` (218), `spotify.c` (177) and 20 more down to `upnext.c` (2). `upstream/main`'s copies warn as much, but for four the fork has added to: `netupdate.c` (32 there, 91 here), `tidal.c` (59, 61), `coverflow.c` and `scrobble.c` (one each); the fork's lines in them stay unformatted with the rest. The fork's own `release_sig.c` and `stock_tls.c` did not either, nor `wifi_transfer.c` in `NAVIGATION`.
- **State:** reformatting upstream's files would make every later merge of them a conflict, so `UNFORMATTED` in `tools/peq.py` names them, and the vendored `patch/monocypher/` files, and `tools/format.sh` formats the rest of `UI_SOURCES` with `NAVIGATION` and six headers; `.clang-format`'s head says so and names version 18. The fork's files were formatted. `tools/format.sh --check` runs `clang-format --dry-run -Werror` over the same list and `prettier --check` over the docs, changing nothing; its C half passes, and fails on one misformatted line. `docs/backlog.md`, `docs/oled-design.md`, `docs/server-library.md` and `docs/source-map.md` fail prettier's check and are left as they are. Not done: a CI step (entry 22).

## Test gaps

### 2. `tools/stock_sites.tsv` is not current, and nothing checks it (resolved)

- **Where:** `docs/rebasing/sitenotes.py`, `tools/stock_sites.tsv`.
- **Evidence:** `python3 -I docs/rebasing/sitenotes.py 'Q2 Firmware V1.36.zip' --check` exits 1 with `tools/stock_sites.tsv is not current: run docs/rebasing/sitenotes.py`. No test or workflow names `sitenotes` (`grep -rn sitenotes test .github`).
- **State:** the table is current (580 sites, none in no named function); one row was missing, the `bnez` in `home_page_init` at `0x534c4c` that `patch_code` in `tools/ipod.py` changes. `tools/release.py package`, which holds the ZIP, runs `python3 -I docs/rebasing/sitenotes.py ZIP --check` before either build and stops with the command to run ([releasing.md](releasing.md)); `test_package_wants_current_stock_sites` in `test/unit/test_release.py` guards the call. It cannot run in CI.

### 3. No test resolves the doc anchors the sources cite (resolved)

- **Where:** comments such as `patch/onlinematch.c:1` (`docs/internals.md#lyrics-search`), `patch/coverflow.c:2623` (`#coverflow-touch`), `patch/lyrics.c:2` (`docs/ipod.md#lyrics`).
- **Evidence:** a script over `git ls-files patch tools test` found 12 citations of anchors that no heading or `<a id>` defined, in 6 files. The anchors were added to the docs beside this report; nothing stops the next rename from breaking them.
- **State:** `test/unit/test_docs.py` resolves every `docs/<name>.md#<anchor>` in `patch/`, `tools/` and `test/` (279) and every relative Markdown link with an anchor in `docs/*.md`, `AGENTS.md`, `CONTRIBUTING.md` and `README.md` (772 in all) against each file's GitHub heading anchors and `<a id>`s, and lists each broken one as `file:line: target#anchor`. It found three: `#curl` (the heading is curl and mbedTLS), `#cover-search` (an `<a id>` added) and `#safe-file-manager` split over two comment lines. It also refuses a `file:LINE` in [architecture.md](architecture.md), which names each symbol by its file, and checks each symbol it pairs with a file is in that file.

### 19. Nothing that needs a build runs in CI

- **Where:** `.github/workflows/firmware.yml:33-42`: `test/fw`, then `library_scope mp3 peq coverflow chapters mp3_gapless remote build rotator_build`.
- **Evidence:** [building.md](building.md#firmware-tests): "`test/patch.py` runs the real MIPS payload, but it needs a build, so a stock ZIP". The 20 or so suites that take `BUILD` (`patch.py`, `playback.py`, the `hci_*.py` player suites, `bluealsa.py`, `player_seam.py`) run only when someone runs them. `patch/scan_folders.c` is reached by `test/patch.py` alone and `patch/lavf_gapless.c` by `test/player_seam.py` alone (`grep -l` over `test/`).
- **Impact:** a pull request can break the MIPS payload, the player hooks or the packaging and pass CI.
- **Fix:** a self-hosted runner, or a private CI job, that holds the ZIP; at least a release checklist line that names the suites ([releasing.md](releasing.md) runs them through `tools/release.py`'s `suites`, `tools/release.py:61`).
- **Effort:** L.
- **Decision:** deferred. It needs a private runner that holds the stock ZIP: the owner's infrastructure decision. Meanwhile `tools/release.py package` runs every suite before a release, and checks `tools/stock_sites.tsv` against the ZIP (entry 2).

### 31. `test/patch.py` is one 21,462-line file

- **Where:** `test/patch.py`; it takes about 3.5 minutes a run ([building.md](building.md#c), "The order for the rest").
- **Impact:** no way to run one area; too slow for per-mutant runs; hard for a reader or an agent to open.
- **Fix:** split by area behind the same machine (`test/playback.py` and `test/headset_io.py` already import it), with a case filter on the command line.
- **Effort:** L.
- **Decision:** deferred: the same merge cost as 23, at L effort.

### 32. C mutation testing reaches 7 files

- **Where:** [building.md](building.md#c)'s table: `library_scope.c`, `chapters.c`, `mp3_gapless.c`, `photos.c`, `headset.c`, `radio.c`, `visualizer.c`. `navigation.c`, `playback.c`, `coverflow.c` and the `peq*` files are listed there as still to do.
- **Effort:** L.
- **Decision:** deferred: L effort of test infrastructure with no defect behind it.

### 33. The C coverage floors are 40% and 38%

- **Where:** `.github/workflows/firmware.yml:48` (`--fail-under-line 40 --fail-under-branch 38`).
- **Evidence:** [building.md](building.md#coverage): most of `navigation.c` is `test/patch.py`'s, which CI cannot run.
- **Fix:** follows from 19.
- **Effort:** M.
- **Decision:** deferred: follows from 19.

## Documentation

### 23. `internals.md` is 832 KB in 1,752 lines

- **Where:** `docs/internals.md`: 268 lines are over 1,000 characters and the longest is 10,766 (`awk` over the file).
- **Impact:** a diff of one changed sentence is the whole paragraph; `grep` prints kilobytes per hit; an agent that reads the file whole spends most of its context on it. [AGENTS.md](../AGENTS.md) tells agents to read by heading.
- **Fix:** one sentence a line (prettier leaves line breaks inside a paragraph alone), and a split by area along the headings, keeping the anchors.
- **Effort:** M.
- **Decision:** deferred. This tree merges upstream, which edits `internals.md` in most changes; a reflow or a split would make each merge of it a whole-file conflict. For upstream to do, or not at all.

### 24. The Hooks table lists 43 of 108 hooks (resolved)

- **Where:** [internals.md](internals.md#hooks) against `HOOKS` (`tools/build.py:34`).
- **Evidence:** importing `tools/build.py` and comparing addresses: 68 hooks are not in the table, and the addresses of 25 appear nowhere in `internals.md` (`config_init`, `usb_detect`, `createPlayListItem`, `mclSetBtVol`, `mclSetOutputWay`, `write_int_config`, `mclLoadPlayList`, `mclPrevSong`, `mcl_preload`, `ui_loader_load_widget_with_parent`, `list_get_img_pic` among them).
- **State:** the table is the main hooks, and the sentence above it says `HOOKS` is the whole list (112 entries), each group under a comment that says what it is for. Three rows named a row click, not the hook (`stream_row`, `netservice_row`, `playermore_online`); they name it. `test_internals_hooks_table_agrees_with_build` in `test/unit/test_docs.py` checks every row's function and address against `HOOKS` and the `*_LEAF` tuples.

### 28. The 10-accent limit's stated reason does not match the reader (resolved)

- **Where:** `tools/theme.py:930` (`if len(t['schemes']) > 10:  # config_value reads one digit`) against `patch/theme_engine.c:13`, whose loop reads up to three digits (`for (; i < 3 && ...`).
- **State:** no reader needs one digit: `config_value` reads up to three, stock's `write_int_config` writes any with `%d`, the boot scripts match only `LIGHT=1` and `LIGHT=2` whole lines (`LOGO_HOOK`), and the tests' config mocks pass the stored text through. Both bounds in `tools/theme.py` (an own option's values, a theme's schemes) say ten is the format's own bound, and that past 32 the 32-bit value masks (`option_t.values`, `setting_click`'s) could not hold a value; `docs/internals.md`'s Themes paragraph says the same. The bounds stay, guarded by `test/unit/test_theme.py`'s cases for each.

### 29. `lockscreen.c` says the rootfs has no room (resolved)

- **Where:** `patch/lockscreen.c:1-2`: "drawn here since the rootfs has no room for image files".
- **Evidence:** the stock image is 50,688,000 bytes and a `--dev --new-player` build's rootfs of 2026-11-15 50,393,088 (`rootfs_bytes` in its `manifest.json`; `tools/build.py:2639` compares the two): about 295 KB to spare.
- **State:** the head comment said the wallpapers were drawn by `wall_render` and shipped as no image files. The lock screen and `lockscreen.c` are gone since ([internals](internals.md#screen-off-and-wake)).
