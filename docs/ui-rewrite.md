# The menu UI as a program of our own

A design and a spike, not shipped code. The direction (the owner's, 2026-11-26): "we rewrite this part of the firmware during build process and make it much more efficient and capable and built for the hardware and easier to build on", and "to be clear this is a firmware rewrite vs hooking". So this page is not about a better painter inside stock's `demo`. It is about `q2ui`: a program of Vamp's own, built from source and installed by the build as `q2player` was ([player-plan](player-plan.md)), that draws the menus on `/dev/fb0` itself, reads the wheel and the buttons itself, and has its own lists, text and layout. `demo` is what it retires.

**Status (2026-11-26).** Phase 1 is done: this design, and a spike (`ui/`, `tools/ui.py`) that draws the Songs list from the library database and scrolls it from the wheel in the emulator, behind a build switch (`tools/build.py --ui-spike`) and a boot switch (`/mnt/data/ui-target`). A default build has none of it. The spike's numbers are in [Measurements](#measurements); the recommendation is in [Verdict](#verdict).

## Verdict

**Go, as a staged replacement with `demo` kept running headless as a service until each of its duties has a new owner.** Not a flag day.

- The renderer is the small part and it pays at once. The spike is 1,160 lines of C and a 49 KB binary. On the same emulator and the same 500 and 9,000-song libraries it spends a tenth of the CPU `demo` spends on a wheel tick, slow or fast, an eleventh to a thirteenth frame for frame, and none at rest, where `demo` never stops waking ([Measurements](#measurements)).
- The application is the large part. `demo` is not a renderer with data behind it: it is 90 pages and 22 dialogs (`*_page_init`, `*_dialog_init` in its symbol table), the server end of the player's socket, the library scan, Bluetooth, Wi-Fi, USB, power and the updater. Vamp's payload is another 221 C files, about 122,000 lines, written against stock's widgets (3,137 `widget_*` and 624 `canvas_*` call sites) and 349 stock functions. A rewrite replaces all of that, surface by surface. [Effort](#effort) puts numbers on it.
- So the version that fits is the one `q2player` took: parity as the rule of scope, a checklist, both programs in the image while the new one grows, and a switch the user can flip back. What is different from the player is that the UI can be cut over a page at a time, because the screen and the keys can be handed between two programs ([Cutover](#cutover)), which the payload already does for `q2video` and card games.

The first three steps of phase 2 are at the [end](#phase-2).

## Where the prior study was right and wrong

A read-only study before this one concluded that a full UI replacement "does not fit" and recommended targeted ownership inside `demo`. Against the evidence here:

- **Right about the size.** The 3,300 `widget_*` call sites it counted are real (3,137 today), and they are the payload's own, not stock's: every one is Vamp code that would have to be written again against another toolkit. Nothing in the spike makes that smaller.
- **Wrong about the room.** Its limits were the payload's: 1.5 MB of code (101,512 bytes free in a `--dev` build of this branch) and 128 KB of state (30,356 free). A program of its own has neither. The root filesystem has 20 MB free of the 72 MB `mtd2` holds (`rootfs_squashfs_bytes` 55,463,936 of `ROOTFS_ROOM`), and `demo`, `libawtk.so`, `libtkc.so` and stock's page and style assets, which go when `demo` does, are 8.4 MB, 7.4 MB, 1.4 MB and 19 MB unpacked.
- **Wrong that the choice is all or nothing.** It weighed replacing `demo` in one step. The staged route below was not on its list.
- **Not asked: what the hooks cost to keep.** Every stock address is V1.36's, with a guard, a row in `tools/stock_map.tsv` or `tools/stock_sites.tsv`, and a rebase when Shanling ships again ([rebasing](rebasing.md)). 133 hooks and 349 called functions is the standing price of living inside `demo`. A page moved to `q2ui` stops paying it.

## What demo does, and who does it after

For each duty: **new** (q2ui or a Vamp daemon does it from source), **daemon** (a program already in the image does it and q2ui talks to it), or **demo** (a headless `demo` keeps doing it during the transition, behind the hand-over in [Cutover](#cutover)). The last column is where it ends up.

| Duty                                         | Today                                                                                                                                               | In the transition                                                                                                                                                                                                 | In the end                                                                                                                                                                                                                          |
| -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Drawing, layout, text, windows               | AWTK in `demo`, the payload's painters over it                                                                                                      | **new**: q2ui, page by page                                                                                                                                                                                       | q2ui                                                                                                                                                                                                                                |
| Wheel, buttons, touch                        | stock's encoder thread and tslib in `demo`, `wheel_sample` hooked in                                                                                | **new**: q2ui reads evdev itself while it has the screen (`ui/input.c`)                                                                                                                                           | q2ui                                                                                                                                                                                                                                |
| Buzzer, key tone                             | `cmd_mcu write_str buzzer` from `demo`; the Clicker mixed by q2player ([Key Tone](internals.md#key-tone))                                           | **new**: q2ui runs the same command and writes the Clicker's file; both are Vamp's routes already                                                                                                                 | q2ui                                                                                                                                                                                                                                |
| The player                                   | `demo` is the server of `/tmp/sock_play`, q2player the client ([player](player.md#the-socket))                                                      | **demo**: q2ui sends play, transport, seek, volume, modes and queue through the command queue Car Remote already uses (`patch/coda_rc_cmdq.h`, `/tmp/coda-rc.cmdq`) and reads Now Playing from `/tmp/coda-rc.now` | **new**: q2ui (or a small `q2ctl` daemon) owns the socket; the protocol is written down in [player](player.md) and q2player is ours                                                                                                 |
| Queue, shuffle, resume, play counts, ratings | payload code over stock's lists (`patch/playback.c`, `upnext.c`, `ratings.c`)                                                                       | **demo**                                                                                                                                                                                                          | **new**: ported; their files in `/mnt/data` keep their formats                                                                                                                                                                      |
| The library database                         | stock's SQLite schema in `/mnt/data/database.db`, queried by `demo`                                                                                 | **new** for reading: q2ui opens it read-only (`ui/songs.c`), as `coda-rc` and `q2-iap` already do                                                                                                                 | q2ui, with an index of its own ([The library](#the-library))                                                                                                                                                                        |
| The scan                                     | `demo`'s scanner threads and stock's `libtag_c`                                                                                                     | **demo**                                                                                                                                                                                                          | **new**: a `q2scan` program on FFmpeg's demuxers (q2player's build has them) or TagLib built by `tools/thirdparty.py`; the largest single piece after the pages                                                                     |
| Settings                                     | `/mnt/data/config.ini`, stock's reader and `write_int_config`                                                                                       | **new**: an INI reader and an atomic writer (a hundred lines); the keys and file stay                                                                                                                             | q2ui                                                                                                                                                                                                                                |
| Bluetooth                                    | `demo` drives `bluetoothd` and `bluealsa` (D-Bus, `libbtctl.so`); Vamp's `q2guard` already watches the link over D-Bus (`patch/q2dbus.c`, `q2bt.c`) | **demo**                                                                                                                                                                                                          | **new**: a BlueZ D-Bus client in a daemon (`q2guard` grows it, or a `q2bt` of its own); `bluetoothd` and `bluealsa` stay as they are                                                                                                |
| Wi-Fi                                        | `demo` runs `wifi_connect.sh`, `wpa_supplicant`, `udhcpc`                                                                                           | **demo**                                                                                                                                                                                                          | **new**: the same scripts and `wpa_cli` from q2ui's settings pages                                                                                                                                                                  |
| USB modes                                    | stock's `check_usb_status` and the `usr/bin/*.sh` gadget scripts; Vamp's `usb_car.c`, `q2car`, `q2-iap`                                             | **demo**                                                                                                                                                                                                          | **new**: the cable watch moves to `q2guard` (it already reads the PMIC and Type-C state); the scripts stay                                                                                                                          |
| AirPlay, DLNA                                | `shairport` and `libdmr`, started and stopped by `demo`                                                                                             | **demo**                                                                                                                                                                                                          | **daemon**: `shairport` is its own program; q2ui starts and stops it. DLNA's renderer is inside `demo` and goes unless rebuilt (a deviation to decide)                                                                              |
| Tidal, QQ Music, NetEase, SyncLink           | inside `demo` (57 `tidal*` functions alone)                                                                                                         | **demo**                                                                                                                                                                                                          | not carried: Vamp's own card apps and bundled Subsonic app cover streaming ([Card apps](internals.md#card-apps)). A deviation the owner signs off, as [player-plan](player-plan.md#deviations) did                                  |
| Power, screen off, standby, shut down        | `demo` (`device_standby`, the backlight, `reset_poweroptions_timer`), `q2guard` for heat and battery                                                | **demo**                                                                                                                                                                                                          | **new**: q2ui blanks the panel (`FBIOBLANK`) and the backlight; standby and power off are the kernel's interfaces `demo` calls today, to be traced once                                                                             |
| Jack detect, output switching                | `check_headset_status` polls two GPIOs once a second                                                                                                | **demo**                                                                                                                                                                                                          | **new**: `q2guard` reads the pins (by interrupt if the kernel exposes one) and tells the player                                                                                                                                     |
| Battery, charge limit, heat                  | `q2guard`, `patch/battery.c`, `usb_charge.c`                                                                                                        | **daemon** / **demo**                                                                                                                                                                                             | `q2guard`; the pages are q2ui's                                                                                                                                                                                                     |
| Card apps                                    | `patch/addon.c` forwards input as datagrams; an app draws on `/dev/fb0` itself                                                                      | **demo**                                                                                                                                                                                                          | **new**: q2ui is the launcher; the app protocol is ours already                                                                                                                                                                     |
| Games                                        | `patch/engine.c` and the games in the payload                                                                                                       | **demo**                                                                                                                                                                                                          | **new**: the engine draws into a pixel buffer with rects and takes one input record a step ([games-engine](games-engine.md)); under q2ui it gets the canvas and q2ui's input with no stock call left, so the games port as they are |
| Videos                                       | `q2video`, a program of ours                                                                                                                        | **daemon**                                                                                                                                                                                                        | q2ui starts it                                                                                                                                                                                                                      |
| Updates                                      | stock's updater installs `update.tar`; `patch/netupdate.c`, `sw_update.c` fetch and verify                                                          | **demo**                                                                                                                                                                                                          | **new**: the fetch and the signature check are ours; the install is the recovery image's, which a marker file starts, to be traced once                                                                                             |
| Car Remote, Guest Play, LocalSend, Find My   | Vamp daemons (`coda-rc`, `q2-guest`, `q2send`) with shims in the payload                                                                            | **daemon**                                                                                                                                                                                                        | the shims' few lines move into q2ui                                                                                                                                                                                                 |
| Boot, dual boot, the splash                  | `S90play`, `q2boot`, `q2splash`                                                                                                                     | unchanged                                                                                                                                                                                                         | unchanged, plus the UI choice ([Cutover](#cutover))                                                                                                                                                                                 |

Reading stock to learn an interface (which GPIO, which ioctl, which D-Bus call) is how [player](player.md) was written and is fine. Nothing of Shanling's is copied or shipped: the transition keeps their `demo` where it already is, in the stock ZIP the build patches.

## Architecture

One process, one thread for everything the user sees, in C, about what the spike is with pages added.

```text
evdev (wheel, keys, touch) --+
timers (motion, the clock) --+--> poll() --> update state --> draw changed rects --> turn, pan, wait for refresh
sockets (player, daemons)  --+                    |                                        |
                                              page stack                              /dev/fb0, two pages
worker threads (library queries, art, network) touch files and their own results only; a pipe wakes poll
```

### The frame

`ui/fb.c`. The UI is landscape, 375 x 320; the panel is portrait, 320 x 375, 32 bits a pixel, two pages ([boot](boot.md#framebuffer)). q2ui keeps one landscape canvas in memory (480,000 bytes) and a list of the rects that changed. A frame draws only inside those rects, turns them onto the page that is not shown (canvas pixel (x, y) is the page's (319 - y, x)), with the last frame's rects too since that page is two frames old, pans to it (`FBIOPAN_DISPLAY`) and waits for the refresh (`FBIO_WAITFORVSYNC`). The turn is the banded one `patch/fb_rotate.c` measured at 0.9 ms a whole frame on a Q2 against stock's 3.3.

- **No retained layers.** The canvas is the one retained surface. A row's pixels are cheap to make again from its kept text runs (below), so a second copy of them buys nothing; the spike measured it ([Lists and scrolling](#lists-and-scrolling)). Memory: the canvas, and nothing per page.
- **Why not draw portrait and skip the turn.** With two pages every frame's change must reach both, so drawing straight into a page means drawing each change twice. Drawing once and turning twice is less work, and the canvas is what a window's slide and Night Shift read.
- **Dirty rects** are at most 8 a frame, merged past that. A rect inside one already listed adds nothing.

### The loop

`ui/main.c`. One `poll` on the input devices, the daemons' sockets and a wake pipe, with a timeout that is the next thing due: nothing while a frame is owed, the motion's next step, else the clock's next minute. Then, always in this order:

1. read what arrived and change state (a tick moves the selection; nothing draws);
2. step whatever is in motion to the time now (it marks the rects it changes);
3. draw the marked rects from the state, once;
4. show the frame, which waits for the panel.

**Update, then draw.** Nothing is drawn from an input handler or a timer, and no state changes while a frame is being drawn. That is the whole fix for the glitch class the mixed ownership causes today (ink set after a label painted, a toggle's icon and shading a frame apart): there is one writer of state and one reader of it per frame.

**Pacing.** A frame that was shown waited for the refresh, so motion steps by the clock at most once a refresh and input that arrives faster is folded into one frame: 100 ticks in a 2.7 s spin made 124 to 136 frames in the spike, not 100 and not 1,900. **At rest nothing runs.** The spike at rest is asleep in `poll` until input or the next minute: 0.0 ms of CPU in 5 s in eight runs of nine and no wake-ups, where `demo` used 40 to 55 ms in the same 5 s with nothing on screen changing ([Measurements](#measurements), `test/ui.py` holds it to one wake a second). On a device whose battery is spent mostly idle with the screen on or off, that is the gain that matters most, and it comes from the shape of the loop, not from tuning.

### Text

`ui/text.c`. The theme's faces (`inter.ttf`, `inter-medium.ttf`, the CJK fallback) are rasterised by stb_truetype, the rasteriser AWTK itself uses, so glyphs are the ones the menus have today. Three levels, each removing work the level above would repeat:

- **A glyph** is rasterised once per face, size and code point and kept as a coverage bitmap (1,024 slots; full, the ones no frame has drawn since the last fill go). No clock is read to age them: the frame counter does it, where stock's cache made a `clock_gettime` call per glyph looked up ([Songs scrolling cost](internals.md#song-rows-cost)).
- **A run** is a string laid out once: its glyphs and their pens, cut with an ellipsis to its room. Advances are whole pixels, rounded, as AWTK keeps them, which is what put every glyph of a Songs row on the column `demo` draws it in.
- **A row** in sight keeps its runs. Drawing it again is blits: no measuring, no layout, no cut search. Today a Songs row measures its text about 7 times on every repaint, whatever the frame's dirty area.

A size is AWTK's (the face scaled so its ascent less its descent is the size), so `assets/themes/*/theme.xml` means the same thing to both programs. A real build wants FreeType or HarfBuzz only if a script needs shaping (Arabic, Indic); stock has neither today.

### Lists and scrolling

`ui/list.c`. A list is rows of one height from a data source (`n`, and a row's strings on demand), a selection and a top row. A tick marks the two rows the selection left and came to; a scroll marks the list. Drawing a rect draws the rows that cross it, and inside a row only what crosses it.

**Scroll by copying, measured: it does not pay here.** The spike can scroll by moving the rows both views share inside the canvas and drawing only the new row, the two selection rows and the faded foot (`--copy`). Under the emulator a scrolled frame cost 525 to 568 us drawn whole and 523 to 537 us copied over three runs: the draw fell from about 315 to 232 us, but the turn onto the panel (about 235 us) is the same either way, because the whole list still changed on screen. Between nothing and 8% of the frame, for a second code path whose every overlay (the fade, the indicator, a leaning bar) has to be drawn again by hand. The design keeps it out. `test/ui.py` still proves the two give the same pixels, so it can come back if a Q2 measures otherwise.

### Input

`ui/input.c`. The wheel is `hyn_sliderbar`, a finger's place round the ring as `ABS_X` 1 to 200; a tick is 28 units of travel. The buttons are `md-gpio-keys`. Both are read as evdev by name. The payload's own input logic is list-agnostic already and comes across unchanged: the spike compiles `patch/wheel_accel.c` as it is, and the wheel's speed, sensitivity and tug ([Wheel movement](internals.md#wheel-movement)) are arithmetic on the same samples. Touch is `hyn_ts`, read the same way; stock reads it through tslib's filters, whose settings a port would match by test.

### The library

The spike reads `songtable` whole, once, into one block of text and two offsets a row: 358 KB for 9,000 songs, 150 to 220 ms under the emulator, sorted by SQLite. That is the honest floor for "open Songs" and it is already better than stock's load and sort on every open ([inefficiencies 35](inefficiencies.md)). The design for phase 2: q2ui keeps a sorted index per list (rowids in title, album and artist order) in memory, built once at boot and after a scan, 36 KB a list for 9,000 songs, and fetches a row's strings by rowid when the row comes into sight. Opening any list is then instant and costs nothing per song.

### Themes, Light Mode, Night Shift

- **Themes.** `tools/theme.py` already turns `theme.xml` into a C table (`themes.inc`) for the payload; q2ui includes the same table. The spike has OLED's numbers as constants.
- **Light Mode** is a second set of colours in the same table. With one writer of pixels, paper and ink are chosen where a colour is used; nothing flips colours after the fact.
- **Night Shift** stays what it is ([Night Shift](internals.md#night-shift)): a pass over the frame's changed rects before the turn. It gets simpler: q2ui knows exactly which rects it drew, so the `0xf7` alpha mark that tells stock's already-warmed pixels apart is not needed.
- **Window animations and snapshots.** A page slide is two pictures moving. q2ui draws the incoming page once into a second canvas (480 KB, held only for the slide's 200 ms) and blits both by rows each frame, which is what AWTK's `htranslate` does, without the offline canvases and without painters having to know they are being snapshotted (`slides()` in the payload exists for that).
- **`fb_rotate`** is the turn in [The frame](#the-frame); the hook over stock's goes with `demo`.

### Language and toolchain

C11, as the payload and q2player. Built by `tools/ui.py` exactly as `tools/player.py` builds q2player: the Codescape GCC 9.3 toolchain stock was built with, stock's ABI flags, dynamic against the root filesystem's glibc 2.28 and its own SQLite (linked by file, as q2player links `libasound`), and a host build of the same sources for tests. One third-party source: `stb_truetype.h` 1.26, public domain, fetched at a pinned commit and SHA-256 by `tools/thirdparty.py`'s `fetch`, not vendored.

Not Rust or C++: the image has one Rust program (`q2-iroh`, 5.3 MB) and its toolchain is a nightly; the payload's reusable code (the EQ, the wheel, Cover Flow's math, the games) is C and compiles into q2ui as it is. Not LVGL or another toolkit: the point of the exercise is that a menu on this panel needs a list, a text cache and a dirty-rect canvas, which is 1,160 lines, and every toolkit brings its own layout pass and timers back.

## Building on it

The "easier to build on" goal. A page is a record of four calls and a list is a data source; nothing else is needed to put a menu on screen. A sketch of the API phase 2 would grow from the spike's `list.c`:

```c
/* A page: opened with its own state on the heap, closed by Return. */
struct ui_page {
    const char *title;
    void *(*open)(void *arg);                    /* build the state; arg is what the row that opened it passed */
    void (*event)(void *st, const ui_event *e);  /* a tick, a key, a touch, a message: change state, ui_mark() */
    void (*draw)(const void *st, ui_canvas *c);  /* draw what lies in c's clip, from the state alone */
    void (*close)(void *st);
};

/* A list page needs no draw or event of its own: it is a data source. */
struct ui_list {
    int (*count)(void *st);
    void (*row)(void *st, int i, ui_row *out);   /* title, detail, right text, icon, flags (chevron, greyed, playing) */
    void (*chosen)(void *st, int i);             /* Centre: ui_push(&page, arg), or act */
};

/* A settings page is a table. */
static const ui_setting display[] = {
    { "Light Mode",   UI_CHOICE, "IPOD", "LIGHT",      .choices = "Off\0On\0Auto\0" },
    { "Night Shift",  UI_PAGE,   .page = &night_shift_page },
    { "Screen Off",   UI_CHOICE, "SYSSET", "LIGHTTIME", .choices = "30 s\0001 min\0005 min\00030 min\0" },
};
```

What a feature's author no longer does: find a stock page's init to hook, check a widget's type by string, set a colour before a label paints, add a painter to a chain every widget passes through ([inefficiencies 18](inefficiencies.md)), register a window in `patch/contexts.inc`, count bytes of payload room, or add a stock address with its guard and its rebasing rows. A host build runs the page on a laptop against a file (`test/ui.py` does), so a page has a test before it has a device.

## Cutover

Two switches, both already of a kind the firmware has.

**Which UI starts.** `S90play` already chooses between Rockbox and `demo` from a file in `/mnt/data` and a held key ([boot](boot.md#rockbox)). The spike adds the same shape: while `/mnt/data/ui-target` reads `q2ui`, `S90play` starts `/usr/bin/q2ui` first, and whenever it ends, by Return in the spike or by a crash, `demo` starts as in any build (`UI_SPIKE_HOOK` in `tools/build.py`). The hook ends the boot animation first, as it does before Rockbox: `q2splash` draws on the page on screen until `demo`, which waits for it (`patch/splash_hold.c`), shows a frame, and the spike's first run in the emulator had the animation's last frames over its list. A q2ui that ships waits on the animation's lock as `demo` does. The fallback is the default: no file, or anything else in it, is `demo`. A shipped version adds what Rockbox's has: a held key at power-on that ignores the file for that boot, and a row in Settings.

**Who has the screen, while both run.** The payload already hands `/dev/fb0` and the keys to another program and takes them back: `ringnav_wm_paint` paints nothing "while q2video or a card game has the framebuffer" and `ringnav_input` drops input for the same time (`video_on()`, `addon_game_on()`). The transition adds a third holder by the same test: while q2ui holds the screen, `demo` runs on without painting or reading keys, as a service; when q2ui opens a page it does not have yet, it releases the screen, tells `demo` which page (one line through the command queue), and sleeps until `demo` hands back. That piece of payload is transition scaffolding, a few dozen lines beside the two gates that exist, and it is the only new hook the plan needs.

So a user on the new UI sees q2ui's Home, Library and lists, and falls through to `demo`'s Settings or Now Playing until those are ported, with one page slide's worth of seam. Each release moves pages across. When the last page and the last duty in the [table](#what-demo-does-and-who-does-it-after) have moved, `demo` is not started, and the build stops patching it.

## Migration order

By what is used most and coupled least. Each step ships behind the switch and is checked against `demo`'s screen for the same state, as the spike was.

1. **The toolkit**: pages and the stack, the list with every row kind the theme has (one line, two lines with art, value on the right, switch, chevron, greyed, playing), the status row, slides, Light Mode, Night Shift. The spike is the start of it.
2. **Library lists**: Songs, Albums, Artists, Genres, Composers, Years, Playlists, an album's tracks, Folder view, the A-Z index, the hold menu. Read-only over the database; playing a row goes through the command queue. The hot path, and everything `docs/internals.md#song-rows-cost` measures.
3. **Home** and its art pane, **Now Playing** (art, times, scrub, volume, lyrics, modes). After this q2ui is what the user sees almost always, and the top-level program.
4. **Settings**, as tables over `config.ini`, with the pages that only toggle a daemon.
5. **The player's server end** and the queue: the first of `demo`'s services to go.
6. **Cover Flow, Search, the EQ page, Games, card apps, Videos**: Vamp's own features, whose logic ports and whose stock glue disappears.
7. **Bluetooth, Wi-Fi, USB, power, jack, the scan, updates**: the services, each to a daemon or to q2ui, each traced on a Q2 first.
8. **`demo` off.** The build drops `HOOKS`, the payload, `tools/stock_map.tsv`, the asset rewrites and stock's 35 MB under `release/`.

## Budgets

|                        | q2ui (spike, measured)                                                                                  | q2ui (whole, estimated)                                                                        | demo today                                                           |
| ---------------------- | ------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Program                | 48,588 bytes stripped                                                                                   | 600 KB to 1 MB: the payload is 1.47 MB with its stock glue                                     | `demo` 8.4 MB, `libawtk.so` 7.4 MB, `libtkc.so` 1.4 MB               |
| Root filesystem        | +49 KB                                                                                                  | under 2 MB while both ship (20 MB are free); about 30 MB smaller once `demo` and its assets go | 55.5 MB squashfs                                                     |
| Memory, private        | canvas 480 KB, library text 358 KB (9,000 songs), glyphs 17 KB after a long scroll, under 1.5 MB in all | 4 to 6 MB: a slide's second canvas, art for one screen, indexes                                | not measured here; to take from `/proc/PID/smaps` on a Q2 in phase 2 |
| Payload room and state | none: no 1.5 MB limit, no 128 KB of scratch, `.data` and the heap as any program has                    | none                                                                                           | 101,512 and 30,356 bytes free                                        |
| A frame of a tick      | 0.2 to 0.3 ms of CPU under the emulator (two rows drawn and turned), 0.55 ms a scrolled frame           | the same shape; a frame's cost is its changed pixels                                           | 4.1 to 6.9 ms                                                        |
| At rest                | no wake-ups                                                                                             | the clock's minute; a daemon's message when one comes                                          | never asleep: 8 to 11 ms of CPU a second under the emulator          |

**On a Q2**, by the one figure both worlds share: the turn onto the panel costs about 2.4 ns a pixel in the emulator (235 us for 98,250 pixels) and 7.5 ns on a Q2 (0.9 ms a frame, `patch/fb_rotate.c`). At that ratio a scrolled frame is about 1.8 ms and a tick's frame about 0.7 ms of the 16.7 ms a refresh allows. ponytail: one ratio from one memory-bound loop is an estimate; phase 2's first step times the spike on a Q2.

## The spike

| File                                           | What it is                                                               |
| ---------------------------------------------- | ------------------------------------------------------------------------ |
| `ui/main.c`                                    | the loop, options, stats, the bench                                      |
| `ui/fb.c`                                      | the canvas, dirty rects, the turn, pan and wait                          |
| `ui/draw.c`                                    | fills, the rounded bar, glyph blits, the fade                            |
| `ui/text.c`                                    | faces, the glyph cache, runs                                             |
| `ui/input.c`                                   | the wheel and keys from evdev                                            |
| `ui/songs.c`                                   | the library's songs from SQLite                                          |
| `ui/list.c`                                    | the Songs page: rows, selection, scroll, the bar's spring, the indicator |
| `ui/Makefile`, `tools/ui.py`                   | the host and MIPS builds                                                 |
| `ui/tools/fixture.py`, `measure.py`, `shot.py` | a made-up library of N songs, the timing harness, a panel file as a PNG  |
| `test/ui.py`                                   | the host test                                                            |

To run it:

```sh
B=$(python3 tools/ci/rebuild.py --dev -- --ui-spike | tail -1)   # a default build has no q2ui
python3 tools/ci/emu_pool.py get "$B"                            # prints the copy's folder, E
python3 ui/tools/fixture.py 9000 E/state/data/database.db        # over the copy's library, emulator stopped
echo q2ui > E/state/data/ui-target; E/q2emu restart "$B"         # q2emu says demo did not start: q2ui did
E/q2emu wheel 5; E/q2emu shot songs.png; E/q2emu key return      # Return ends q2ui; demo starts
```

Options go in `/mnt/data/q2ui.args`: `--motion 0|1|2` (none; the bar's spring, drawn when it moves a pixel; the spring drawn at every refresh), `--copy`, `--limit N`, `--bench`. Stats are written to `/mnt/data/q2ui-stats.json` whenever it comes to rest.

What the spike is not: it has no status glyphs, no Shuffle icon, no marquee for a long title, no A-Z index, no touch, no playing-song bars, no selection shadow, and Centre does nothing. Those are rows in phase 2's checklist, not unknowns: each is drawing or state of a kind the spike already has.

## Measurements

All in one leased emulator copy (`q2-emu`, QEMU user mode), 2026-11-26, the same `--dev --ui-spike` build for both programs, libraries of 500 and 9,000 made-up songs (`ui/tools/fixture.py`), by `ui/tools/measure.py`. Flips are the shim's count of pans. CPU is the on-CPU time of every thread of the program (`/proc/PID/task/*/schedstat`), less its own rate at rest, so `demo`'s includes its flush thread and both include the emulator's translation: these are ratios between two programs on one machine, not a Q2's milliseconds. Instructions were not counted: the pool's `qemu-mipsel` is built without plugins, and `test/patch.py`'s Unicorn counts the payload's instructions only, not stock's paint, so there is no equal footing for them yet.

| Songs list        |  Rows | Slow tick: frames | CPU a tick | CPU a frame | Fast spin: frames a tick | CPU a tick | CPU a frame | At rest, CPU in 5 s |
| ----------------- | ----: | ----------------: | ---------: | ----------: | -----------------------: | ---------: | ----------: | ------------------: |
| `demo`            |   500 |              12.6 |    58.1 ms |     4.61 ms |                     1.19 |    8.15 ms |     6.85 ms |             52.8 ms |
| `demo`            | 9,000 |              13.0 |    52.6 ms |     4.05 ms |                     1.25 |    8.33 ms |     6.66 ms |             43.5 ms |
| q2ui `--motion 2` |   500 |              14.7 |    5.31 ms |     0.36 ms |                     1.24 |    0.93 ms |     0.75 ms |              1.2 ms |
| q2ui `--motion 2` | 9,000 |              14.7 |    5.53 ms |     0.38 ms |                     1.36 |    0.93 ms |     0.68 ms |              0.0 ms |
| q2ui `--motion 1` |   500 |               4.7 |    4.19 ms |     0.90 ms |                     1.13 |    1.02 ms |     0.90 ms |              0.0 ms |
| q2ui `--motion 1` | 9,000 |               4.7 |    3.73 ms |     0.80 ms |                     1.25 |    0.98 ms |     0.79 ms |              0.0 ms |
| q2ui `--motion 0` | 9,000 |               1.7 |    2.00 ms |     1.22 ms |                     0.85 |    0.92 ms |     1.08 ms |              0.0 ms |

Slow: 20 single ticks, 0.3 s after each. Fast: one turn of 100 ticks down. So, frame for frame (`--motion 2`), q2ui costs `demo`'s CPU divided by 11 to 13 on a slow tick and by 9 to 10 on a fast spin; per tick, by 10 slow and 9 fast; and as it would ship (`--motion 1`), a slow tick is a fourteenth of `demo`'s.

Read with these in mind:

- **Frames per slow tick are the design's, in both.** `demo`'s 14 are the selection bar's 220 ms spring. q2ui at `--motion 2` draws its own 220 ms spring at every refresh to match, so the two columns compare frame for frame; that is the fair row for cost per frame. `--motion 1` is what the design would ship: the same spring, drawn only when the bar moves a pixel. `--motion 0` is a tick with no motion: under two frames (the selection, and the indicator appearing).
- **A fast spin** is a 100-tick turn in about 2.7 s. Both programs fold it into about 1.2 to 1.4 frames a tick; `demo` at 6.7 ms of CPU a frame here, q2ui at 0.7; for both the count is set by the panel's refresh and how the ticks bunch, not by cost.
- **Library size does not matter to either while scrolling**: both draw a screenful. It matters at open: q2ui read and sorted 9,000 songs in 115 to 222 ms and 500 in 54 to 77 ms (`open_ms`, eight starts), with its first frame 29 to 43 ms after. `demo`'s open was not timed: its page slide and its load overlap, and telling them apart needs a mark inside `demo`.
- **At rest** `demo` used 40 to 55 ms of CPU in 5 s with a still screen (four runs; a fifth, 9 ms); q2ui 0.0 in eight runs and 1.2 ms in one, with no frame shown in any.

The bench (`--bench`: frames drawn and turned with no wait for the panel, CPU time per pass inside the emulator):

| Pass                             | Pixels turned | CPU a pass, three runs | of which drawn |        turned |
| -------------------------------- | ------------: | ---------------------: | -------------: | ------------: |
| A tick on the page: two rows     |        42,000 |          187 to 304 us |   96 to 148 us |  84 to 143 us |
| A tick that scrolls, drawn whole |        98,250 |          525 to 568 us |  304 to 326 us | 218 to 239 us |
| A tick that scrolls, rows copied |        98,250 |          523 to 537 us |  230 to 234 us | 230 to 240 us |
| The whole screen                 |       120,000 |          620 to 741 us |                |               |

The first row's spread is six passes a run; the scrolled rows are 200 passes each.

**Looks.** `q2emu shot` of each program on the same library with the first song selected, compared pixel by pixel over the rows both show the same text in (66,090 pixels: the list from y 90, less the three tied titles' artists, which stock orders differently): no pixel differs by more than 48 of 255, 9,287 differ at all. Text is within 3 of 255 everywhere (every glyph on the same pixel; the blend rounds differently). What differs more: the selection bar's soft shadow and raised look, which the spike does not draw (up to 45), and the foot's fade ramp (up to 24). Outside the compared rows the spike lacks the status glyphs and the Shuffle icon.

**What surprised.**

- Text was the easy part. The same rasteriser and whole-pixel advances gave `demo`'s glyph placement on the second try.
- Scroll by copying, which the brief expected to pay, does not: the panel turn dominates a scrolled frame and copying cannot shrink it.
- `demo` at rest. The idle cost was not what anyone set out to measure and is the largest ratio on the page.
- The boot animation drew over the spike. Two programs on one framebuffer need a rule for who has it, at boot too: the first thing phase 2 builds.
- A wheel tick's ~14 frames are not waste to remove. They are a spring someone designed. The saving is in what each costs, and in not drawing the ones where nothing moved.

## What carries over

From the payload, as source: `wheel_accel.c` (compiled into the spike unchanged), `peq.c` (already shared with q2player), the theme table and `tools/theme.py`, `tools/art.py`'s drawn icons (PNG files q2ui would load with the image's libpng), `fb_rotate.c`'s turn, the game engine and its five games, Cover Flow's layout math, `song_rows.c`'s fitting rules (`song_fit` is the spike's row layout), the Clicker, tag and sort rules, and every file format in `/mnt/data`. From the daemons, as they are: `q2player`, `q2guard`, `coda-rc`, `q2-guest`, `q2send`, `q2video`, `q2splash`, `q2boot`. From the tests: the emulator, its pool, `q2emu shot` comparisons, and the host-build pattern of `test/player`.

What does not: every hook, `patch/offsets.inc`, the asset rewrites in `tools/ipod.py`, `patch/contexts.inc`, the rebasing tables, and the parts of each feature that exist to bend a stock page (most of `patch/nav/`).

## Effort

Counted from what exists, not guessed from the spike's size.

| Piece                                         | Basis                                                                                                               | Size                                                         |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| Toolkit (step 1)                              | the spike is 1,160 lines for one row kind and one page                                                              | 6,000 to 8,000 lines                                         |
| Library lists, Home, Now Playing (steps 2, 3) | `patch/nav/` is 10,700 lines, `coverflow.c` (Home's art) 5,500, of which under half is logic that survives          | 12,000 to 15,000 lines                                       |
| Settings (step 4)                             | 41 of stock's 90 pages are `playset_*` or `systemset_*`; as tables                                                  | 4,000 lines and the tables                                   |
| Player server, queue, resume (step 5)         | `playback.c`, `upnext.c`, `chapters.c`, 3,500 lines, plus the socket's server end, which `player/ipc.c` mirrors     | 4,000 to 5,000 lines                                         |
| Vamp's features (step 6)                      | the payload less the above: about 80,000 lines, most of which port with their stock calls swapped for the toolkit's | the largest block; mechanical per feature, not small         |
| Services (step 7)                             | Bluetooth, Wi-Fi, USB, power, scan, updates: `demo`'s own code, none of it ours yet                                 | 10,000 to 15,000 lines of new code, and the most device time |

In the terms the project measures work by ([orchestration](orchestration.md): one item, one agent, one context): steps 1 to 3 are about 25 to 35 items, at which point the new UI is what a user sees day to day and the gains on this page are theirs. Steps 4 to 6 are 60 to 90 items. Step 7 is 20 to 30 and is paced by a Q2 on a desk, not by writing. `q2player`, for scale, was 6,400 lines in seven phases with a parity checklist of about 70 rows. The UI is ten to fifteen times that, and the staging is what makes it survivable: the user is never without a working menu, and the work can stop after any step with a better firmware than before it.

## Risks

- **Parity is long-tailed.** Stock's 90 pages hold behaviour nobody has listed (what a dialog does when a cable comes out under it). The checklist is built page by page from `demo` running beside q2ui in the emulator, as [player](player.md) was built from captured traffic.
- **Two UIs for months.** A fix may be needed in both. The rule that keeps it bounded: a page that has moved is deleted from the payload in the same change, so nothing exists twice for long.
- **The hand-over seam.** A slide between a q2ui page and a `demo` page is two programs drawing one animation. The first version cuts instead of sliding; whether that is acceptable is the owner's call and the reason step 3 comes early.
- **Device-only unknowns.** Standby, power off, the backlight, the jack and USB are interfaces the emulator fakes or lacks. Each is traced on a Q2 before its step, and none blocks steps 1 to 6.
- **Memory while both run.** 128 MB, no swap, the player's crossfade hold is 6 MB. q2ui adds an estimated 4 to 6 MB beside `demo` during the transition; phase 2 measures it on a Q2 before step 2 ships.
- **Fonts and scripts.** stb_truetype does no shaping. Stock does none either, so nothing regresses, but it is not a base for Arabic or Indic titles.
- **Rebasing does not go away until step 8.** Until `demo` is off, a new stock version still needs the hooks rebased; each step makes that smaller.
- **The emulator flatters.** Every ratio here is under QEMU. A Q2's cache and memory speed could narrow them; nothing suggests they could reverse, since the saving is work not done (layout, measuring, clock reads, idle wake-ups), not work done faster.

## Phase 2

Its first three steps:

1. **Time the spike on a Q2** (`--bench`, and `ui/tools/measure.py`'s three phases through `tools/q2remote.py`), with `demo`'s and q2ui's resident memory beside them. This replaces every "under the emulator" on this page with a device figure, and takes a day.
2. **Build the hand-over**: the third holder of the screen in the payload's two gates, the "open this page" line in the command queue, and q2ui resident from boot behind `ui-target`. With it, q2ui's Songs is a page a user can actually use: a row plays through `demo`.
3. **Grow the toolkit to the Library** (step 1 and 2 of [Migration order](#migration-order)): every row kind, the status row, slides, then Songs, Albums and Artists against `demo`'s screens in the emulator, with the parity checklist started as its own page.
