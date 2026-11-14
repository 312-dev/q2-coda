# Inefficiencies

Problems worth fixing, ranked by impact over effort, as read on branch `oled-theme` at 1.8.2. Each has where it is, what shows it, what it costs, a fix and an effort (S: an hour or two, M: a day, L: several days). Nothing here is fixed in this file's change; an entry marked **unconfirmed** says what would confirm it.

The figures from a build are a `--dev` build's: its `manifest.json`, `stock.squashfs` and `rootfs.squashfs`. The Q2 is a 2-core Ingenic X2000 with 128 MB of RAM ([hardware.md](hardware.md)).

`ponytail:` in a comment or a doc marks a limit the code leaves on purpose, with the upgrade it names. It is on 238 lines of `patch/` and `tools/` (`grep -c ponytail`); the ones that are costs are entries 7 to 17 below.

## Ranked

| #   | Problem                                                             | Category  | Effort |
| --- | ------------------------------------------------------------------- | --------- | ------ |
| 1   | The UI payload's room (resolved)                                    | fragile   | M      |
| 2   | `tools/stock_sites.tsv` is not current, and nothing checks it       | test gap  | S      |
| 3   | No test resolves the doc anchors the sources cite                   | test gap  | S      |
| 4   | The theme picker's order and Status Bar's values (resolved)         | fragile   | S      |
| 5   | A build runs `unsquashfs -cat` once per file, hundreds of times     | build     | S      |
| 6   | Every build compiles all 55 objects one after another               | build     | S      |
| 7   | Scrobble upload rewrites the log once a window                      | runtime   | S      |
| 8   | Playlist art reads the playlist table once per playlist             | runtime   | S      |
| 9   | A folder's play loads each subfolder on the UI thread               | runtime   | M      |
| 10  | A card app's import reads every song's tags on the UI thread        | runtime   | M      |
| 11  | Spotify's cover is decoded on the UI thread                         | runtime   | S      |
| 12  | A stock database write on the UI thread waits for a count           | runtime   | M      |
| 13  | Ratings compare every song with every rated one                     | runtime   | S      |
| 14  | An EPUB's spine is quadratic in its items                           | runtime   | S      |
| 15  | Tidal's cache trim walks the cache on the UI thread, under a lock   | runtime   | M      |
| 16  | A refused cover costs a process each                                | runtime   | M      |
| 17  | A whole-library read holds a second copy of the library             | runtime   | L      |
| 18  | Every painted widget passes through about 25 painters               | runtime   | M      |
| 19  | Nothing that needs a build runs in CI                               | test gap  | L      |
| 20  | `classic_light_*` art and the Classic face are named in C           | fragile   | M      |
| 21  | Tests unpack the themes by count                                    | fragile   | S      |
| 22  | Two source lists, a link list and a format list name the same files | duplicate | S      |
| 23  | `internals.md` is 832 KB in 1,752 lines                             | docs      | M      |
| 24  | The Hooks table lists 43 of 108 hooks                               | docs      | S      |
| 25  | `format.sh` formats 21 of the UI payload's 48 C files, and CI none  | duplicate | S      |
| 26  | The stock version is written in three places in `build.py`          | fragile   | S      |
| 27  | `DEMO_SHA` is recorded but never compared                           | fragile   | S      |
| 28  | The 10-accent limit's stated reason does not match the reader       | docs      | S      |
| 29  | `lockscreen.c` says the rootfs has no room                          | docs      | S      |
| 30  | A new theme's Corners default follows its index                     | fragile   | S      |
| 31  | `test/patch.py` is one 21,462-line file                             | test gap  | L      |
| 32  | C mutation testing reaches 8 files                                  | test gap  | L      |
| 33  | The C coverage floors are 40% and 38%                               | test gap  | M      |
| 34  | Boot reads `config.ini` once per key                                | runtime   | M      |

## Fragile hard-coding

### 1. The UI payload's room (resolved)

- **Where:** `tools/build.py:32` (`SCRATCH`), `check_layout` in `tools/build.py`, `patch/link.ld`.
- **State:** `SCRATCH` is `0xc00000`, so code and constants have 1 MB below it. demo's heap starts where the scratch state ends (a running demo's `/proc/<pid>/maps` shows one anonymous mapping from the payload's end through the heap), so nothing of stock's lies in the range the move took.
- **Watch:** `patch_bytes` in a build's `manifest.json` against `SCRATCH - BASE` (1,048,576).

### 4. The theme picker's order and Status Bar's values (resolved)

- **State:** each `theme.xml` states its `order`; `tools/theme.py` generates `theme_rows[]` and expands the Status Bar option's `<value themes="picker"/>` into every built-in theme in picker order, and refuses a duplicate or missing order. A new theme is one file.

### 20. `classic_light_*` art and the Classic face are named in C

- **Where:** 72 string literals `"classic_light_..."` or `"classic-light"` in `patch/navigation.c` (34), `patch/classic_pages.c` (18), `patch/lockscreen.c` (7), `patch/coverflow.c` (5), `patch/volume.c` (5), `patch/ratings.c` (2), `patch/radio.c` (1) (`grep -c '"classic_light_\|"classic-light"' patch/*.c`). `tools/theme.py:913` allows `art_final` for the id `classic-light` alone.
- **Evidence:** `tools/art.py`'s `images(t)` names generated art by the theme's id, and the C loads Classic's names literally, so another theme's generated art ships and is never drawn.
- **Impact:** a second theme cannot use the `classic-light` painters with its own art. In flux on branch `oled-theme`.
- **Fix:** a per-theme art prefix in `theme_t`, written by `emit`, and a helper that joins it.
- **Effort:** M.

### 21. Tests unpack the themes by count

- **Where:** `test/fw/test_nav_playing.py:16` (`MINIMAL, CLASSIC, MODERN = THEMES`), `:549-550` (`range(4)` Status Bar looks with four labels), `test/fw/test_coverflow.py:2362` (`range(3)`), `test/unit/test_theme.py:522` (`== [0, 0, 0]`), `:536` (`== 3`), `test/ui_preview.py:21-24` (three ids).
- **Impact:** a fourth theme fails tests that are not about it.
- **Fix:** look themes up by id, and assert over `builtins()` instead of a literal length.
- **Effort:** S.

### 26. The stock version is written in three places in `build.py`

- **Where:** `tools/build.py:1731` (`STOCK_VERSION = 'V1.36'`), `:1782` (`'Q2 Firmware V1.36/update.tar'`), and the docstring and usage text; `tools/build.py:1755` writes `DiamondBond/q2-pod` into `Q2POD_UA` although `REPO` (`:13`) holds it.
- **Impact:** a rebase or a fork edits each by hand; `tools/rebase.py apply` rewrites pins, but whether it rewrites the member path is **unconfirmed** (read `apply`, `tools/rebase.py:774`).
- **Fix:** build the member path from `STOCK_VERSION` and the user agent from `REPO`.
- **Effort:** S.

### 27. `DEMO_SHA` is recorded but never compared

- **Where:** `tools/build.py:10`, used only at `:2272` (`stock_demo_sha256=DEMO_SHA`).
- **Evidence:** `grep -n DEMO_SHA tools/build.py` gives those two lines. The ZIP's hash (`:1780`) is what guards demo.
- **Impact:** the manifest states a hash the build did not compute; a wrong constant would go unseen by the build.
- **Fix:** `check(sha(raw_demo) == DEMO_SHA, ...)` after `:1792`, or write `sha(raw_demo)`.
- **Effort:** S.

### 30. A new theme's Corners default follows its index

- **Where:** `patch/theme_engine.c:183`: `config_value("IPOD", "ROUNDED", 2, !st.theme)`.
- **Impact:** Round is the default for theme 0 alone, so any other theme's missing key reads Square whatever its `preset` says until the theme is picked once.
- **Fix:** read the default from the theme's option row, as the own keys do (`theme_keys[k].def`, `patch/theme_engine.c:192`).
- **Effort:** S.

## Build-time cost

### 5. A build runs `unsquashfs -cat` once per file, hundreds of times

- **Where:** `tools/build.py:1791` (`cat`), called in the window loop at `:1811-1815` and 24 more places (`grep -c 'cat(' tools/build.py` is 25), and per geometry set in `set_assets` (`tools/ipod.py:1289`).
- **Evidence:** the stock rootfs has 127 `raw/ui/*.bin` files (`unsquashfs -l stock.squashfs`), each read by its own process from a 50 MB LZO image, before any asset is patched; a `--dev` build's output holds 325 files.
- **Impact:** process start and a metadata walk per file, on a build that takes minutes.
- **Fix:** one `unsquashfs -d` into a directory under `--out` at the start and `cat = lambda p: (root/p).read_bytes()`. The files are Shanling's either way, and `--out` is ignored by git.
- **Effort:** S.

### 6. Every build compiles all 55 objects one after another

- **Where:** `tools/build.py:1601-1602` (18 `NAVIGATION` files), `tools/peq.py:251-254` (30 `UI_SOURCES`, then the player's 4 to 7), each a blocking `run('clang', ...)`; `tools/build.py:1774` refuses an `--out` that holds `update.tar`, so nothing is reused.
- **Impact:** `navigation.c` is 11,223 lines and `coverflow.c` 4,824 at `-Oz`; one core does all of it on a machine with several. Reproducibility needs the same objects, not the same order of compiling.
- **Fix:** compile through a `concurrent.futures.ThreadPoolExecutor` and link in the fixed order. **Unconfirmed** how much of a build's wall time is compiling: time a build's stages first.
- **Effort:** S.

## Runtime cost on the Q2

Entries 7 to 17 are costs the code marks itself.

### 7. Scrobble upload rewrites the log once a window

- **Where:** `patch/scrobble.c:344-347`; [internals.md](internals.md#scrobbling).
- **Evidence:** its comment: "each pass rewrites the rest of the log under the lock, so card writes grow with the square of the backlog (a 3 MB log writes about 3 MB extra, 10 MB about 45 MB)".
- **Impact:** card wear and a long upload after weeks offline.
- **Fix:** the comment's own: carry an offset across windows and rewrite once.
- **Effort:** S.

### 8. Playlist art reads the playlist table once per playlist

- **Where:** `patch/playlist_art.c:151-153`.
- **Evidence:** "each job reads the table again for its own cd_num (no index), so a first build of N playlists reads it N + 1 times". `test/fw/messy_playlists.py` models 101 playlists over about 55,000 songs.
- **Impact:** the first build after a big import, on the worker, competing with the UI for the card.
- **Fix:** the comment's: collect each playlist's first albums in the refresh's one pass.
- **Effort:** S.

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
- **Impact:** as 9, for a sync's playlists.
- **Fix:** stage it as Update Local Music is staged.
- **Effort:** M.

### 11. Spotify's cover is decoded on the UI thread

- **Where:** `patch/spotify.c:443`: "the cover is sized on the UI thread, one decode a track; a worker if it stutters".
- **Impact:** a stall at each track change while Spotify plays.
- **Fix:** Cover Flow's art worker already sizes covers off the UI thread.
- **Effort:** S.

### 12. A stock database write on the UI thread waits for a count

- **Where:** `patch/coverflow.c:4416` (`count_worker`); [internals.md](internals.md#playlist-art) (the paragraph on the wait).
- **Evidence:** the doc: a write on the UI thread "that meets Home's or the update dialog's count ... a read of the whole `songtable` on their own connections, seconds on a big library" holds the UI.
- **Fix:** cache the counts by library generation, so a count runs once a scan.
- **Effort:** M.

### 13. Ratings compare every song with every rated one

- **Where:** `patch/ratings.c:441`.
- **Fix:** sort the rated hashes once and search.
- **Effort:** S.

### 14. An EPUB's spine is quadratic in its items

- **Where:** `patch/books.c:358`.
- **Fix:** an id index, as the comment says.
- **Effort:** S.

### 15. Tidal's cache trim walks the cache on the UI thread, under a lock

- **Where:** `patch/tidal.c:203`: "on the UI thread, holding the lock, a moment for a full 8 GB cache".
- **Fix:** trim on the download thread.
- **Effort:** M.

### 16. A refused cover costs a process each

- **Where:** `patch/coverflow.c:182`: "a process per cover; a resident decoder if first builds drag".
- **Impact:** first art builds of a library whose covers stock's decoder refuses (WebP, large PNG).
- **Effort:** M.

### 17. A whole-library read holds a second copy of the library

- **Where:** [internals.md](internals.md#play-counts) (Most Played's paragraph and the one after): "one copy per whole-library read is the floor ... the next whole-library read holds two".
- **Impact:** memory on a 128 MB device with a large library.
- **Effort:** L.

### 18. Every painted widget passes through about 25 painters

- **Where:** `patch/navigation.c:2723-2772` (`ringnav_paint`, the hook on `widget_on_paint_border`).
- **Evidence:** the function calls `night_paint`, `coverflow_paint`, `lockscreen_paint`, `edit_paint`, `photos_paint`, `books_paint`, `spot_paint`, `addon_paint`, `peq_paint`, `battery_paint`, `visualizer_paint`, `games_paint`, `paint_chevrons`, `paint_radio`, `paint_into`, `paint_letter`, `paint_cover`, `paint_scrubber`, `slider_scrub`, `quick_scrub`, `search_over`, `paint_caps`, `hold_paint` and `coverflow_home_clip` for every widget stock paints, and each decides for itself whether the widget is its own.
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

## Duplicated tables kept in step by hand

### 22. Two source lists, a link list and a format list name the same files

- **Where:** `NAVIGATION` (`tools/build.py:1594`), `UI_SOURCES` (`tools/peq.py:187`), the `--undefined=` names at `tools/build.py:1611-1615` (symbols stock's patched words call, beside `HOOKS`), `tools/format.sh:9` (three Cover Flow files by name), the table in `test/cmut/sandbox.sh:22-39`, and the suite list in `.github/workflows/firmware.yml:42` beside [building.md](building.md#tests)'s.
- **Impact:** a new file or suite is added in up to four places; a miss is silent (not formatted, not mutated, not run in CI).
- **Fix:** derive `format.sh`'s list from both source lists, as it already does for `NAVIGATION`; derive the `--undefined` names from the tables that hold the call sites.
- **Effort:** S.

### 25. `format.sh` formats 21 of the UI payload's 48 C files, and CI none

- **Where:** `tools/format.sh:8-9`: `NAVIGATION`'s 18 files, three headers and three Cover Flow files. `UI_SOURCES`' other 27 files of 30 (`coverflow.c`, `addon.c`, `spotify.c`, `books.c` and the rest) are not named. No workflow runs `clang-format` (`grep -n format .github/workflows/*.yml` finds only coverage's report format).
- **Impact:** **unconfirmed** whether those files conform: `clang-format` was not installed where this was written. `clang-format --dry-run` over `UI_SOURCES` would say.
- **Fix:** if they conform, add them; if not, decide and say so in `.clang-format`'s head.
- **Effort:** S.

## Test gaps

### 2. `tools/stock_sites.tsv` is not current, and nothing checks it

- **Where:** `docs/rebasing/sitenotes.py`, `tools/stock_sites.tsv`.
- **Evidence:** `python3 -I docs/rebasing/sitenotes.py 'Q2 Firmware V1.36.zip' --check` exits 1 with `tools/stock_sites.tsv is not current: run docs/rebasing/sitenotes.py`. No test or workflow names `sitenotes` (`grep -rn sitenotes test .github`).
- **Impact:** the table is the record of how to find each patched instruction in the next stock version, and "a note is only of use written while the site is still known" (its docstring). Sites added since the last run have none.
- **Fix:** run it and commit the table; add `--check` to the release procedure ([releasing.md](releasing.md)), as it needs the stock ZIP and so cannot run in CI.
- **Effort:** S.

### 3. No test resolves the doc anchors the sources cite

- **Where:** comments such as `patch/onlinematch.c:1` (`docs/internals.md#lyrics-search`), `patch/coverflow.c:2560` (`#coverflow-touch`), `patch/lyrics.c:2` (`docs/ipod.md#lyrics`).
- **Evidence:** a script over `git ls-files patch tools test` found 12 citations of anchors that no heading or `<a id>` defined, in 6 files. The anchors were added to the docs beside this report; nothing stops the next rename from breaking them.
- **Fix:** a `test/unit` test that collects `docs/<file>.md#<anchor>` from the sources and the Markdown links, and checks each against the headings and `<a id>`s. It needs no stock file.
- **Effort:** S.

### 19. Nothing that needs a build runs in CI

- **Where:** `.github/workflows/firmware.yml:33-42`: `test/fw`, then `library_scope mp3 peq coverflow chapters mp3_gapless remote build rotator_build`.
- **Evidence:** [building.md](building.md#firmware-tests): "`test/patch.py` runs the real MIPS payload, but it needs a build, so a stock ZIP". The 20 or so suites that take `BUILD` (`patch.py`, `playback.py`, the `hci_*.py` player suites, `bluealsa.py`, `player_seam.py`) run only when someone runs them. `patch/scan_folders.c` is reached by `test/patch.py` alone and `patch/lavf_gapless.c` by `test/player_seam.py` alone (`grep -l` over `test/`).
- **Impact:** a pull request can break the MIPS payload, the player hooks or the packaging and pass CI.
- **Fix:** a self-hosted runner, or a private CI job, that holds the ZIP; at least a release checklist line that names the suites ([releasing.md](releasing.md) runs them through `tools/release.py`'s `suites`, `tools/release.py:61`).
- **Effort:** L.

### 31. `test/patch.py` is one 21,462-line file

- **Where:** `test/patch.py`; it takes about 3.5 minutes a run ([building.md](building.md#c), "The order for the rest").
- **Impact:** no way to run one area; too slow for per-mutant runs; hard for a reader or an agent to open.
- **Fix:** split by area behind the same machine (`test/playback.py` and `test/headset_io.py` already import it), with a case filter on the command line.
- **Effort:** L.

### 32. C mutation testing reaches 8 files

- **Where:** [building.md](building.md#c)'s table: `library_scope.c`, `chapters.c`, `mp3_gapless.c`, `lockscreen.c`, `photos.c`, `headset.c`, `radio.c`, `visualizer.c`. `navigation.c`, `playback.c`, `coverflow.c` and the `peq*` files are listed there as still to do.
- **Effort:** L.

### 33. The C coverage floors are 40% and 38%

- **Where:** `.github/workflows/firmware.yml:48` (`--fail-under-line 40 --fail-under-branch 38`).
- **Evidence:** [building.md](building.md#coverage): most of `navigation.c` is `test/patch.py`'s, which CI cannot run.
- **Fix:** follows from 19.
- **Effort:** M.

## Documentation

### 23. `internals.md` is 832 KB in 1,752 lines

- **Where:** `docs/internals.md`: 268 lines are over 1,000 characters and the longest is 10,766 (`awk` over the file).
- **Impact:** a diff of one changed sentence is the whole paragraph; `grep` prints kilobytes per hit; an agent that reads the file whole spends most of its context on it. [AGENTS.md](../AGENTS.md) tells agents to read by heading.
- **Fix:** one sentence a line (prettier leaves line breaks inside a paragraph alone), and a split by area along the headings, keeping the anchors.
- **Effort:** M.

### 24. The Hooks table lists 43 of 108 hooks

- **Where:** [internals.md](internals.md#hooks) against `HOOKS` (`tools/build.py:34`).
- **Evidence:** importing `tools/build.py` and comparing addresses: 68 hooks are not in the table, and the addresses of 25 appear nowhere in `internals.md` (`config_init`, `usb_detect`, `createPlayListItem`, `mclSetBtVol`, `mclSetOutputWay`, `write_int_config`, `mclLoadPlayList`, `mclPrevSong`, `mcl_preload`, `ui_loader_load_widget_with_parent`, `list_get_img_pic` among them).
- **Fix:** generate the table from `HOOKS` and its comments, or head it "the main hooks" and point at `HOOKS`.
- **Effort:** S.

### 28. The 10-accent limit's stated reason does not match the reader

- **Where:** `tools/theme.py:930` (`if len(t['schemes']) > 10:  # config_value reads one digit`) against `patch/theme_engine.c:13`, whose loop reads up to three digits (`for (; i < 3 && ...`).
- **Impact:** **unconfirmed** whether another reader of the accent's key takes one digit; if none does, the cap that blocks a tenth accent beside Classic's ten schemes has no cause. In flux on branch `oled-theme`.
- **Fix:** trace the stored accent's readers and correct the comment or lift the cap.
- **Effort:** S.

### 29. `lockscreen.c` says the rootfs has no room

- **Where:** `patch/lockscreen.c:1-2`: "drawn here since the rootfs has no room for image files".
- **Evidence:** the stock image is 50,688,000 bytes and a 1.8.2 `--dev` build's rootfs 48,549,888 (`stat`; `tools/build.py:2263` compares the two): about 2.1 MB to spare.
- **Fix:** reword the comment; the doc's sentence is corrected.
- **Effort:** S.
