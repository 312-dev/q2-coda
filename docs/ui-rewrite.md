# The menu UI as a program of our own

A design and an engine behind a build switch, not yet what a default build ships. The direction (the owner's, 2026-11-26): "we rewrite this part of the firmware during build process and make it much more efficient and capable and built for the hardware and easier to build on", and "to be clear this is a firmware rewrite vs hooking". So this page is not about a better painter inside stock's `demo`. It is about `q2ui`: a program of Vamp's own, built from source and installed by the build as `q2player` was ([player-plan](player-plan.md)), that draws the menus on `/dev/fb0` itself, reads the wheel and the buttons itself, and has its own lists, text and layout. `demo` is what it retires.

**Status (2026-11-26).** Phase 1 (this design and a spike that drew Songs) measured well enough on a Q2 that the owner said to go: "completley redo the UI engine from ground up". Phase 2 is that. Its first step, [the toolkit](#the-toolkit), is built: `ui/` is an engine with a frame clock, springs, layers, dithered gradients, sub-pixel shapes and text, pictures, a list with every row kind and a card of layers, driven by tests on its own clock. It is behind the same two switches as the spike (`tools/build.py --ui-spike`, `/mnt/data/ui-target`), so a default build has none of it and `demo` is always one Return away. Where each step stands is in [Phase 2](#phase-2); what the owner asked of the look and motion, and how each point is met and checked, is in [Requirements](#requirements); what is at parity with `demo` is in [Parity](#parity).

**Where it is going.** The owner's end goal (2026-11-26) is Vamp OS: every userspace piece of the Q2 Vamp's own or built from open source, on Shanling's kernel and drivers. q2ui is its UI layer, so two rules bind this work beyond the staging below. It must not come to depend on `demo` or on a library only stock has, past the hand-over that stages the move: [What q2ui still leans on](#what-q2ui-still-leans-on) lists every such place. And parity with `demo` is the floor, not the ceiling (the owner: "not to bound ourselves to the same limitations we dont have to anymore ... we now have more control and should provide a flexible foundation/OS for others to use"): the toolkit is an interface other people build on, written down in [The toolkit](#the-toolkit), not AWTK's page model carried over. The roadmap, the app model and the package format are `docs/vamp-os.md`'s once it lands (branch `docs/vamp-os`); this page follows it where they meet.

## Verdict

**Go, as a staged replacement with `demo` kept running headless as a service until each of its duties has a new owner.** Not a flag day.

- The renderer is the small part and it pays at once. The spike was 1,160 lines of C and a 49 KB binary (the engine that followed it is about 3,400 lines and 125 KB). On the same emulator and the same 500 and 9,000-song libraries it spends a tenth of the CPU `demo` spends on a wheel tick, slow or fast, an eleventh to a thirteenth frame for frame, and none at rest, where `demo` never stops waking ([Measurements](#measurements)). On a Q2, on the owner's 9,198 songs, the gap is wider: a nineteenth of `demo`'s CPU on a slow tick, a tenth on a fast spin, a sixth of its resident memory ([Measured on a Q2](#measured-on-a-q2)).
- The application is the large part. `demo` is not a renderer with data behind it: it is 90 pages and 22 dialogs (`*_page_init`, `*_dialog_init` in its symbol table), the server end of the player's socket, the library scan, Bluetooth, Wi-Fi, USB, power and the updater. Vamp's payload is another 221 C files, about 122,000 lines, written against stock's widgets (3,137 `widget_*` and 624 `canvas_*` call sites) and 349 stock functions. A rewrite replaces all of that, surface by surface. [Effort](#effort) puts numbers on it.
- So the version that fits is the one `q2player` took: parity as the rule of scope, a checklist, both programs in the image while the new one grows, and a switch the user can flip back. What is different from the player is that the UI can be cut over a page at a time, because the screen and the keys can be handed between two programs ([Cutover](#cutover)), which the payload already does for `q2video` and card games.

Where phase 2 stands is at the [end](#phase-2).

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
| The library database                         | stock's SQLite schema in `/mnt/data/database.db`, queried by `demo`                                                                                 | **new** for reading: q2ui opens it read-only (`ui/songs.c`), with the server library's `catalogue.db` attached, as `coda-rc` and `q2-iap` already do                                                              | q2ui, with an index of its own ([The library](#the-library))                                                                                                                                                                        |
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

One process, one thread for everything the user sees, in C.

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
- **The panel's depth.** The canvas is 8 bits a channel; the link to the glass may carry fewer ([Gradients](#gradients)). When it does (`--panel-bits 6` or `5`), the turn is where each pixel is dithered down to it.

### The loop

`ui/main.c`. One `poll` on the input devices, the daemons' sockets and a wake pipe, with a timeout that is the next thing due: nothing while a frame is owed, the motion's next step, else the clock's next minute. Then, always in this order:

1. read what arrived and change state (a tick moves the selection; nothing draws);
2. step whatever is in motion to the time now (it marks the rects it changes);
3. draw the marked rects from the state, once;
4. show the frame, which waits for the panel.

**Update, then draw.** Nothing is drawn from an input handler or a timer, and no state changes while a frame is being drawn. That is the whole fix for the glitch class the mixed ownership causes today (ink set after a label painted, a toggle's icon and shading a frame apart): there is one writer of state and one reader of it per frame.

**No state change while a frame is drawn** is enforced, not asked: `ui_mark` called from a page's draw aborts the program (`ui_painting`, `ui/ui.c`), and `ui/test_engine.c` holds that it does.

**Pacing.** A frame that was shown waited for the refresh, so motion steps by the clock at most once a refresh and input that arrives faster is folded into one frame: 100 ticks in a 2.7 s spin made 124 to 136 frames in the spike, not 100 and not 1,900. The refresh's length is read from the mode's own timings (the pixel clock's period times the frame's pixels, blanking included; `fb_refresh_us`), and every frame is accounted against it (`ui_stats`, written to the stats file): its work's wall time from wake to pan, how many ran past a refresh (`over_budget`, and `cpu_over_budget` by CPU time alone), and how many reached the panel a refresh or more late (`late`, `dropped`). The emulator's shim answers the wait for the refresh with a 16 ms sleep after each frame, so there a frame's period is its work plus 16 ms and `late` counts every frame whose work passed 9 ms; `cpu_over_budget` is the count that means a missed refresh there. **At rest nothing runs.** The spike at rest is asleep in `poll` until input or the next minute: 0.0 ms of CPU in 5 s in eight runs of nine and no wake-ups, where `demo` used 40 to 55 ms in the same 5 s with nothing on screen changing ([Measurements](#measurements), `test/ui.py` holds it to one wake a second). On a device whose battery is spent mostly idle with the screen on or off, that is the gain that matters most, and it comes from the shape of the loop, not from tuning.

### Text

`ui/text.c`. The theme's faces (`inter.ttf`, `inter-medium.ttf`, the CJK fallback) are rasterised by stb_truetype, the rasteriser AWTK itself uses. Three levels, each removing work the level above would repeat:

- **A glyph** is rasterised once per face, size, code point and quarter-pixel phase and kept as a coverage bitmap (2,048 slots; full, the ones no frame has drawn since the last fill go). No clock is read to age them: the frame counter does it, where stock's cache made a `clock_gettime` call per glyph looked up ([Songs scrolling cost](internals.md#song-rows-cost)).
- **A run** is a string laid out once: its glyphs and their pens, cut with an ellipsis to its room. Pens are the face's own advances, unrounded, and a glyph whose pen falls between pixels is the one rasterised at the nearest quarter pixel left of it, so a word's spacing is Inter's and not a column grid's. AWTK rounds every advance to a pixel and carries the error on ([oled-design: Type](oled-design.md#type) names the uneven gaps that makes); `--text awtk` lays text out that way, which is what put every glyph of a Songs row on the column `demo` draws it in when the spike was compared with it.
- **A row** in sight keeps its runs. Drawing it again is blits: no measuring, no layout, no cut search. Today a Songs row measures its text about 7 times on every repaint, whatever the frame's dirty area.

A run drawn at a place between pixels (a row gliding) is filtered there by the blit: each pixel takes its share of the two rows, or the four pixels, the shifted glyph lies over (`blit_a8`).

**Making a glyph is the one cost a frame cannot bound by its changed pixels**: a fast spin brings text no frame has shown. So a frame of a motion may spend 3 ms making glyphs and no more (`text_budget_us`); past that a new glyph keeps its room in its run and its ink comes with the next frame, which reads as a letter arriving a sixtieth of a second late where it would otherwise be a dropped frame (`glyphs_deferred` in the stats counts them). A page's first frame, from rest, makes all it shows. And at rest, once, the loop makes printable ASCII at the list's sizes ahead of need, 4 ms at a time between polls of the input (`text_warm`).

A size is AWTK's (the face scaled so its ascent less its descent is the size), so `assets/themes/*/theme.xml` means the same thing to both programs; `TYPE(px)` turns the design's em sizes into it. stb_truetype does no shaping and q2ui applies no kerning; a script that needs shaping (Arabic, Indic) needs HarfBuzz, which stock has not got either.

### Lists and scrolling

`ui/list.c`. A list is rows of one height from a data source (a count, and a row's strings and marks on demand), a selection, and the motion between two states of them ([Motion](#motion)). Drawing a rect draws the rows that cross it, and inside a row only what crosses it. Rows can be pinned at the top (Songs' Shuffle), in which case the rest scroll under them; a header or a greyed row is passed over by the wheel.

**Scroll by copying, measured in the spike: it does not pay here.** The spike could scroll by moving the rows both views share inside the canvas and drawing only the new row. Under the emulator a scrolled frame cost 525 to 568 us drawn whole and 523 to 537 us copied: the draw fell from about 315 to 232 us, but the turn onto the panel (about 235 us) is the same either way, because the whole list still changed on screen. The engine does not have it, and with a list that glides at places between pixels there is nothing whole to copy.

### Motion

Everything that moves is a function of the frame's time (`ui/anim.c`): `ui_time()` is read once a frame, so one frame shows one instant, and a frame that comes late shows the motion where it should be by then. A dropped frame costs a step's smoothness, never the motion's length.

- **Springs** (`spring_t`, `spring_step`) are the closed form of a damped spring, so a step of any length lands where many small ones would, and a new target mid-way keeps the place and the speed: that is all "retargeting" is. Critically damped for a glide (no overshoot), a little under for a lean and a press (once past rest and back). They are doubles: a 9,000-row list's scroll is 288,000 px and must still come to rest on a row to a hundredth of a pixel.
- **The scroll glides.** A wheel tick still moves the selection exactly one row; when that takes the list a row on, the rows glide there over about 200 ms, at places between pixels, the incoming row sliding into view. A tick mid-glide goes on from where the list is, and a fast spin is one long glide, never steps.
- **The platter** (the selection's bar) slides from row to row on a spring of its own, and stays where it is while the list glides under it.
- **The lean, tvOS style** ([oled-design: Selection bar](oled-design.md#selection-bar) is the approved motion; `refs/parallax_collection_view_cell.gif` is the owner's reference for how it should read). While a finger turns the wheel short of its next tick (`IN_TUG`, the share of a tick turned), the platter leans toward the row it is heading for: up to 3 px that way, up to 3.5% larger, turned about its middle line with perspective (the edge it leans to falls away, a trapezoid under a pixel or two different at its ends), a deeper shadow under it and a white gleam sliding the other way. A tick kicks it the same way. It springs back when the finger lets go. **What is on the platter moves with it, and further**: the text 1.4 times the platter's lean and a glyph or art 1.8 times, each layer its own depth. That the contents stayed put while only the bar moved is what the owner called wrong in today's Vamp.
- **The press**: Centre sinks the platter to 0.965 and brightens its blue to `#4080F0`; the release springs it back past rest once.
- **A switch's knob** slides when its row's setting flips, and turns back from where it is.
- **A page** opening slides in from the right over 220 ms while the page under drifts 30% left and darkens a quarter; closing is the reverse over 200 ms. Both pages are drawn once, each into a canvas of its own held for the slide, and a frame of the slide is those two pictures placed, the status row still over them.
- **Reduce Motion** (`--reduce-motion`) turns off the lean and the slides and keeps the glide and the platter's slide, as [oled-design: Motion](oled-design.md#motion) lists them. `--motion 0` puts every move at its end at once (the bench's still cases).

### Layers

What draws over what is by depth, and focus is the top of it. A list's draw, bottom up:

1. the scrolling rows in their resting inks, clipped to the list's own frame (the whole screen for a list under the status row) and faded at its top and foot;
2. the pinned rows, which the rest leave under, fading out in the first 6 px below them so no row ever shows through one;
3. the scroll indicator;
4. **the focus layer**, last and clipped by nothing but the screen: the platter's shadow, the platter, and over it the contents of whatever rows the platter covers, in their focused inks, cut to the platter.

So a pinned row, a neighbour and the fades never cut the platter's lean, its growth or its shadow: the owner's "the shuffle pinned row clips the contents / overflow of the highlighted row tilting beneath it" cannot happen, because the pinned row is under the focus layer rather than a clip around the rows. The status row is drawn after every page (`ui/status.c`), so a lean on the first row reaches up under its glyphs. `test/ui.py` holds both ends: leaning up from the first song the platter is drawn over the pinned row, and leaning down from the last row it is drawn over the foot's fade at full brightness.

### Gradients

Two things make a ramp band on this panel, and each has its own cure.

- **A ramp slower than its levels.** A fade over 30 rows from a dim grey to nothing has more rows than levels. Every ramp q2ui draws (the list's fades, a shadow, a gleam, `fill_ramp`) is computed at 16 bits and brought to 8 with blue noise under the last bit (`dither64`, a 64 x 64 table made by `ui/tools/bluenoise.py` by filling voids, each value 16 times), so its steps are grain too fine to see as bands. The noise is fixed to the screen: a still picture is still.
- **A link narrower than the framebuffer.** `/dev/fb0` is 32 bits a pixel, but the panel's link may carry 6 or 5 bits a channel, and nothing the kernel reports says which. `--panel-bits 6` (or `5`) makes the turn add noise of one step's size before the low bits go, so the glass shows the 8-bit picture's mean; black stays black. **The Q2's depth is not yet measured.** `--page depth` is the instrument: five ramps to white and five to the accent, each drawn as a link of 8, 6 and 5 bits would show it, cut off and dithered. On the panel, the first cut-off ramp that looks no worse than the 8-bit one above it is the link's depth.

### Edges

Every shape's edge pixel is blended by the share of the pixel the shape covers, onto the colour already there: no assumed background, so no dark or coloured fringe on any ground. Rounded boxes (leaning ones too), discs, strokes with round ends and pictures' corners are all placed at sub-pixel positions (`fill_round`, `stroke_line`, `image_draw`). A row's own numbers are worked out once in floats and its pixels in whole numbers (distances in 1/64 px, `px_box_dist`), so an edge costs the same on a Q2 as under an emulator that pays for every float.

### Cards

`ui/card.c`, for art (`refs/parallax_view.gif` is the owner's reference). A card is a stack of layers, each with a depth. Pushed (by the wheel's tug or a tick's kick), the card leans about its horizontal middle with perspective, its shadow goes the other way, a glint crosses its face, and each layer is drawn moved by its depth times the push: a layer behind the face lags the card, one in front leads it. The card's own picture is shown a little larger than its frame and moved inside it, so no edge of it ever shows. The wheel has one axis, so a push is up or down; a lean about the vertical axis (Cover Flow's) is a second scanline mapping in `image_draw` that is not written yet. `--page card` is the example.

### Input

`ui/input.c`. The wheel is `hyn_sliderbar`, a finger's place round the ring as `ABS_X` 1 to 200; a tick is 28 units of travel, and between ticks the share of the next one turned is reported too (`IN_TUG`; the finger leaving is a tug of 0). The buttons are `md-gpio-keys`; one held 600 ms is a hold (`IN_KEY_HOLD`), and its release is then not a press. Both devices are read as evdev by name. The payload's own input logic is list-agnostic already and comes across unchanged: q2ui compiles `patch/wheel_accel.c` as it is, and the wheel's speed and sensitivity ([Wheel movement](internals.md#wheel-movement)) are arithmetic on the same samples. Touch is not read yet; it is `hyn_ts`, read the same way; stock reads it through tslib's filters, whose settings a port would match by test.

### The library

`ui/lib.c` reads the Library's songs whole, once, into one block of text and two offsets a row: `songtable`, and the server library's `servertable` from the card's `catalogue.db` when there is one ([server-library](server-library.md)), as `demo`'s Songs lists both. 358 KB for 9,000 songs, 150 to 220 ms under the emulator and 285 to 319 ms for 9,198 on a Q2, sorted by SQLite. That is the honest floor for "open Songs" and it is already better than stock's load and sort on every open ([inefficiencies 35](inefficiencies.md)). The design for phase 2: q2ui keeps a sorted index per list (rowids in title, album and artist order) in memory, built once at boot and after a scan, 36 KB a list for 9,000 songs, and fetches a row's strings by rowid when the row comes into sight. Opening any list is then instant and costs nothing per song.

### Themes, Light Mode, Night Shift

- **Themes.** Colours are `tools/theme.py`'s tokens (`patch/tokens.h`, which q2ui includes as the payload does; `tok()` in `ui/theme.c`). The list's geometry is OLED's as constants in `ui/list.c`; reading it from `theme.xml`'s table (`themes.inc`), so that a second theme is data, is to do.
- **Light Mode** (`--light`) is the tokens' other half. With one writer of pixels, paper and ink are chosen where a colour is used; nothing flips colours after the fact.
- **Night Shift** stays what it is ([Night Shift](internals.md#night-shift)): a pass over the frame's changed rects before the turn. It gets simpler: q2ui knows exactly which rects it drew, so the `0xf7` alpha mark that tells stock's already-warmed pixels apart is not needed.
- **Window animations and snapshots.** A page slide is two pictures moving ([Motion](#motion)): both pages drawn once into canvases of their own (480 KB each, held only for the slide) and placed by rows each frame, which is what AWTK's `htranslate` does, without the offline canvases and without painters having to know they are being snapshotted (`slides()` in the payload exists for that).
- **`fb_rotate`** is the turn in [The frame](#the-frame); the hook over stock's goes with `demo`.

### Language and toolchain

C11, as the payload and q2player. Built by `tools/ui.py` exactly as `tools/player.py` builds q2player: the Codescape GCC 9.3 toolchain stock was built with, stock's ABI flags, dynamic against the root filesystem's glibc 2.28 and its own SQLite (linked by file, as q2player links `libasound`), and a host build of the same sources for tests. Two third-party sources, both single public-domain headers fetched at one pinned commit and checked by SHA-256 by `tools/thirdparty.py`'s `fetch`, not vendored: `stb_truetype.h` 1.26 (text) and `stb_image.h` 2.30 (PNG for the theme's glyphs, JPEG for covers).

Not Rust or C++: the image has one Rust program (`q2-iroh`, 5.3 MB) and its toolchain is a nightly; the payload's reusable code (the EQ, the wheel, Cover Flow's math, the games) is C and compiles into q2ui as it is. Not LVGL or another toolkit: the point of the exercise is that a menu on this panel needs a list, a text cache, springs and a dirty-rect canvas, a few thousand lines, and every toolkit brings its own layout pass and timers back.

## The toolkit

The "easier to build on" goal, and since the owner's word on Vamp OS a public one: this is the interface other people's pages and apps are written against, so it is written down here as it settles and versioned in the header (`Q2UI_API_MAJOR`.`Q2UI_API_MINOR` in `ui/q2ui.h`, 0.1: nothing is frozen before 1.0; the first number changes when a page written for the last would no longer build or behave). `ui/q2ui.h` is the whole of it, one header, grouped as below. What is not here yet is listed at the end, so nobody mistakes a gap for a decision.

**A page** is a record of calls and a state of its own. Nothing else is needed to put something on screen:

```c
typedef struct ui_page {
    const char *name;
    void *(*open)(void *arg);                   /* build the state; arg is what the opener passed */
    void (*event)(void *st, const input_t *e);  /* a tick, a tug, a key: change state, ui_mark() what changed */
    void (*step)(void *st);                     /* the motion at ui_time(); ui_mark(), ui_again() while it moves */
    void (*draw)(void *st, rect_t r);           /* everything of the page inside r, from the state alone */
    void (*close)(void *st);
    void (*shown)(void *st);                    /* on top again: a page above it closed */
} ui_page;
void ui_push(const ui_page *p, void *arg);      /* slides in */
void ui_pop(void);                              /* slides out; the last page's pop ends the program */
```

The contract is the loop's ([The loop](#the-loop)): `event` and `step` change state and mark rects; `draw` only draws. `ui_again()` asks for the next refresh, `ui_wake_ms(n)` for a step in n ms with nothing drawn between (a marquee's hold), and with neither the program sleeps.

**A list** needs no page code at all: it is a data source.

```c
typedef struct ui_source {
    int (*count)(void *ctx);
    void (*row)(void *ctx, int i, ui_row *out); /* title, detail, value, sub, lead, icon, art, flags */
    void (*chosen)(void *ctx, int i);           /* Centre */
    void (*held)(void *ctx, int i);             /* Centre held */
    void (*focus)(void *ctx, int i);            /* the selection came to row i */
    void *ctx;
    int pinned, row_h, letters;                 /* rows that stay put; 32 or 52; sorted, so a spin jumps by letter */
    rect_t box;                                 /* where it lives; none is the whole screen under the status row */
} ui_source;

ui_source src = { .count = count, .row = row, .chosen = chosen };
ui_push(&list_page, &src);
```

A row is described, not drawn: `ROW_CHEVRON`, `ROW_SWITCH` with `ROW_ON`, `ROW_GREY`, `ROW_PLAYING`, `ROW_ACCENT`, `ROW_CHECK`, `ROW_HEADER`, with a title and optionally a detail after it (a song's artist), a value at the right column, a second line, a leading number, a glyph or art. The list lays it out, cuts what does not fit, slides a cut title while its row is selected, and gives every row the focus motion. A list can also be one part of a page that draws other things: `list_new`, `list_event`, `list_step`, `list_draw` in a box.

**The rest, by group** (each a section above):

| Group        | Calls                                                                                                          |
| ------------ | -------------------------------------------------------------------------------------------------------------- |
| Motion       | `ui_time`, `spring_t`, `spring_step`, `spring_set`, `ease_cubic_out`, `ease_cubic_in`, `rgb_mix`               |
| Shapes       | `fill`, `fill_alpha`, `fill_round` (lean, gleam), `fill_disc`, `stroke_line`, `shadow_round`, `fill_ramp`      |
| Fades        | `shade_rows`, `shade_cols`, `shade_all`, `fade_set` and `fade_clear` (a marquee's ends)                         |
| Text         | `run_make`, `run_draw`, `text_width`, the faces `FACE_REGULAR` and `FACE_MEDIUM`, `TYPE(px)`                    |
| Pictures     | `image_load`, `image_mem`, `image_fit`, `image_new`, `image_draw`, `image_draw_part`, `icon(name)`              |
| Cards        | `ui_layer`, `card_draw`, `card_bounds`                                                                          |
| Theme        | `tok(TOK_...)`, `ui_light`, `RGB_ACCENT`                                                                         |
| Input        | `input_t`: `IN_TICK`, `IN_TUG`, `IN_KEY_DOWN`, `IN_KEY_UP`, `IN_KEY_HOLD`                                        |
| Status row   | drawn over every page by the loop; a page does nothing for it                                                   |

**Examples**, in the tree and built into the program: `ui/page_gallery.c` is a list of every row kind in sixty lines (`--page gallery`) and a page with its own state, spring and draw (`--page card`); `ui/page_depth.c` is a page that only draws.

**Running it without a Q2.** `python3 tools/ui.py build host` builds the same sources for this machine (it needs a C compiler, SQLite, and the two headers `tools/ui.py` fetches). It runs against a file standing in for the panel (two 320 x 375 pages, `--fb FILE`) and either FIFOs standing in for the wheel and keys (`--input DIR`) or a script:

### Scripts

`q2ui --script FILE` runs a file of input on a clock of the program's own, a frame every 1/60 s whatever the machine's speed, so a motion is the same on every run and can be tested and recorded frame by frame. Lines: `tick N` (N ticks, one a frame; negative is up), `tug SHARE`, `key NAME` (`centre`, `return`, `play`, `next`, `prev`), `down`, `up` and `hold NAME`, `run MS`, `rest` (run until nothing moves), `shot FILE` (the canvas as a PPM), `record DIR` and `record off` (every frame shown, numbered). `--trace FILE` writes a line a frame of the list's motion (`sel`, `scroll`, `bar`, `lean`, `press`, `knob`), `--clock HH:MM` fixes the status row's clock, and `--page` picks the first page. `test/ui.py` is written this way, and the recordings under `~/.cache/q2-ui-rewrite/shots/` were made this way.

**Not here yet**, in the order they are wanted: layout beyond the list's own (a row or column of widgets with weights, so a page is described and not placed by hand); a focus model for more than one focusable thing on a page; touch; widgets beyond the list, the card and the switch (slider, progress, text entry, dialog, toast, tabs); a theme's geometry as data; an app's own process and its package (Vamp OS's to define). A declarative form (a page as a table, as `ui_source` already is for a list and `ui_setting` below would be for Settings) is the direction where it helps:

```c
/* A settings page is a table (step 4). */
static const ui_setting display[] = {
    { "Light Mode",   UI_CHOICE, "IPOD", "LIGHT",      .choices = "Off\0On\0Auto\0" },
    { "Night Shift",  UI_PAGE,   .page = &night_shift_page },
    { "Screen Off",   UI_CHOICE, "SYSSET", "LIGHTTIME", .choices = "30 s\0001 min\0005 min\00030 min\0" },
};
```

What a feature's author no longer does: find a stock page's init to hook, check a widget's type by string, set a colour before a label paints, add a painter to a chain every widget passes through ([inefficiencies 18](inefficiencies.md)), register a window in `patch/contexts.inc`, count bytes of payload room, or add a stock address with its guard and its rebasing rows.

### What q2ui still leans on

Kept short on purpose, and each with what removes it, so q2ui can move to a base where `demo` and stock's libraries are gone.

| Leans on                                             | Where                                             | What removes it                                                                                                      |
| ---------------------------------------------------- | ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| The root filesystem's `libsqlite3.so.0.8.6`, by file | `tools/ui.py` (the MIPS link), `ui/lib.c`         | Nothing of stock's is in it but the copy: SQLite is public domain and `tools/thirdparty.py` already builds one; link that |
| The root filesystem's glibc 2.28 and loader          | the MIPS link                                     | Any libc of the new base; q2ui uses C11, POSIX, `poll`, `mmap` and the framebuffer and evdev ioctls, nothing else     |
| Stock's library schema (`songtable`, its columns)    | `ui/lib.c`                                        | A library of Vamp's own ([The library](#the-library)); the queries are in one file                                    |
| The server catalogue's schema (`servertable`)        | `ui/lib.c`                                        | Nothing: it is Vamp's own ([server-library](server-library.md))                                                        |
| Fonts and glyphs at stock's asset paths              | `ui/main.c` (`--fonts`, `--icons` defaults)       | The files are Vamp's (Inter, `tools/art.py`'s PNGs); only the folder is stock's layout. Two options already move them |
| `demo`, for every duty not yet moved                 | the hand-over ([Cutover](#cutover))               | Each step of [Migration order](#migration-order); q2ui's own code calls nothing in `demo`                             |
| `demo`'s command queue and its files in `/tmp`       | `ui/player.c`                                     | q2ui owning the player's socket (step 5); the one file to change                                                      |
| The kernel's `jzfb` and input drivers                | `ui/fb.c`, `ui/input.c`                           | Stays: the kernel is Shanling's in Vamp OS too. The interfaces used are the stock Linux ones (fbdev, evdev)           |

## Cutover

Two switches, both of a kind the firmware already has.

**Which UI has the screen.** One file: while `/mnt/data/ui-target` reads `q2ui` and the image has `/usr/bin/q2ui` (a `--ui-spike` build), q2ui does. No file, anything else in it, or a build without q2ui, is `demo`, exactly as before: the fallback is the default. A shipped version adds what Rockbox's choice has: a held key at power-on that ignores the file for that boot, and a row in Settings.

**How the two share it** (`patch/q2ui_gate.c`, demo's side, and it is the whole of the new payload code: transition scaffolding that goes when `demo` does). The payload already hands `/dev/fb0` and the keys to another program and takes them back: `ringnav_wm_paint` paints nothing "while q2video or a card game has the framebuffer" and `ringnav_input` drops input for the same time. q2ui is a third holder by the same test (`q2ui_on()`), and like those two it is `demo`'s child:

- **`demo` starts it**, on its first pass, having read the target before its first paint, so no frame of `demo`'s shows first. It ends the boot animation as it does (`killall q2splash`, as before Rockbox): the animation draws on the page on screen until `demo` shows a frame, which it will not. A q2ui that ships waits on the animation's lock as `demo` does ([boot](boot.md#boot-animation)).
- **While q2ui runs, `demo` is headless and whole**: every service it owns goes on (the player, Bluetooth, Wi-Fi, USB, power, the scan). It leaves the wheel, Centre, Return and touches to q2ui, which reads them itself, and each one it drops resets its screen-off timer, so the screen stays lit while it is used and sleeps when it is not. Play/Pause and the side buttons stay `demo`'s: the music's keys work as on any page.
- **q2ui ends** (the last row of its Home, Old Menus, or a crash): `demo` sees its child gone on the next pass and draws its whole screen, and keeps it until `demo` next starts (a restart of the Q2). This is the owner's way out, always on Home. Return cannot be it: on Home, Return opens Now Playing, as `demo`'s does. And no key on `demo`'s side brings q2ui back: every one on its Home is taken (Return opens Now Playing, its hold is the user's Hold Return choice), and a way back is not needed once q2ui's own pages cover what its Home leads to.
- **`demo` needs the screen**: when a window other than Home or Now Playing comes to the top by itself (the USB prompt, a pairing request, Shut Down?, a scan's dialog), `demo` ends q2ui so the window can be seen and answered, and starts it again once Home is back on top. q2ui keeps the row it rested on in `/tmp/q2ui.state` and a start within ten minutes opens on it, so the list is where it was left.
- **The screen dark**: `demo` turns the backlight off after its idle time and on at the next press, which is a wake and nothing else. q2ui reads the same keys, so it drops what arrives while the backlight is off and for 0.4 s after (`ui/ui.c` `dark`).

**How q2ui uses `demo`'s services** (`ui/player.c`): through the command queue Car Remote and Car Audio already use (`patch/coda_rc_cmdq.h`), which `demo` keeps open while q2ui has the screen. Centre on a song writes the list's ids to `/tmp/coda-rc.list` and sends one line, `play AT SHUFFLE COUNT`; Now Playing's state is `/tmp/coda-rc.now`. Nothing of q2ui's calls into `demo`, and when q2ui (or a `q2ctl` daemon) owns the player's socket, `ui/player.c` is the one file that changes.

Why a child that is started and ended, and not a resident q2ui asleep under `demo`: it is the mechanism the payload has and has tested for two other programs, it needs no new channel between the two (an exit is the message), and a crash of q2ui is handled by the same line as a Return. Its cost is q2ui's start, which is the library's read ([The library](#the-library): 0.3 s for 9,198 songs on a Q2 until q2ui keeps an index). Run in the emulator on 2026-11-26: q2ui comes up after the boot animation with no frame of `demo`'s first; Centre on a song plays it; Return from the page `demo` took the screen for brings q2ui back on its row. Not yet checked there: a play with Car Remote off (the pool's lease turns it on), and the dark screen, which the emulator has no backlight for. **The emulator's input devices are FIFOs**, where an event goes to one reader, not to each as on a Q2; `ui/tools/emu_input.py` writes each event for both programs.

So a user on the new UI sees q2ui's pages, and falls through to `demo`'s for what q2ui has not got yet, with a cut where a slide would be. Each release moves pages across. When the last page and the last duty in the [table](#what-demo-does-and-who-does-it-after) have moved, `demo` is not started, and the build stops patching it.

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

|                        | q2ui (spike, measured)                                                                                                                                           | q2ui (whole, estimated)                                                                        | demo today                                                                                            |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Program                | 48,588 bytes stripped                                                                                                                                            | 600 KB to 1 MB: the payload is 1.47 MB with its stock glue                                     | `demo` 8.4 MB, `libawtk.so` 7.4 MB, `libtkc.so` 1.4 MB                                                |
| Root filesystem        | +49 KB                                                                                                                                                           | under 2 MB while both ship (20 MB are free); about 30 MB smaller once `demo` and its assets go | 55.5 MB squashfs                                                                                      |
| Memory, private        | canvas 480 KB, library text 358 KB (9,000 songs), glyphs 17 KB after a long scroll, under 1.5 MB in all; on a Q2 3.5 MB anonymous, 7.1 MB resident (9,198 songs) | 4 to 6 MB: a slide's second canvas, art for one screen, indexes                                | on a Q2 19.8 MB anonymous, 41.1 MB resident                                                           |
| Payload room and state | none: no 1.5 MB limit, no 128 KB of scratch, `.data` and the heap as any program has                                                                             | none                                                                                           | 101,512 and 30,356 bytes free                                                                         |
| A frame of a tick      | 0.2 to 0.3 ms of CPU under the emulator (two rows drawn and turned), 0.55 ms a scrolled frame; on a Q2 0.67 to 0.71 ms and 1.94 to 2.00 ms                       | the same shape; a frame's cost is its changed pixels                                           | 4.1 to 6.9 ms; on a Q2 about 6.9 ms                                                                   |
| At rest                | no wake-ups                                                                                                                                                      | the clock's minute; a daemon's message when one comes                                          | never asleep: 8 to 11 ms of CPU a second under the emulator, 9 on a Q2, and a frame a second on Songs |

**On a Q2**, the estimate made from the one figure both worlds share (the turn at about 2.4 ns a pixel in the emulator and 7.5 ns on a Q2, `patch/fb_rotate.c`) was a scrolled frame of about 1.8 ms and a tick's frame of about 0.7 ms. Measured, they are 1.94 to 2.00 ms and 0.67 to 0.71 ms of the 16.7 ms a refresh allows ([Measured on a Q2](#measured-on-a-q2)); the turn itself came to 12 ns a pixel inside q2ui.

## The program

| File                                           | What it is                                                                          |
| ---------------------------------------------- | ----------------------------------------------------------------------------------- |
| `ui/q2ui.h`                                    | the toolkit's interface, whole                                                      |
| `ui/main.c`                                    | options, the first page, the stats file, the bench, scripts                         |
| `ui/ui.c`                                      | pages and their stack, slides, the loop, the frame's accounting                     |
| `ui/anim.c`                                    | the frame clock, springs, curves                                                    |
| `ui/fb.c`                                      | the canvas, dirty rects, the turn (and its dither), pan and wait                    |
| `ui/draw.c`, `ui/px.h`                         | shapes, shadows, ramps and fades, glyph blits                                       |
| `ui/text.c`                                    | faces, the glyph cache, runs                                                        |
| `ui/image.c`                                   | PNG and JPEG, scaled and leaning draws, the theme's glyphs                          |
| `ui/input.c`                                   | the wheel, its tug and the keys from evdev                                          |
| `ui/list.c`                                    | the list: rows, selection, glide, the focus layer, the indicator, the marquee       |
| `ui/card.c`                                    | a card of layers                                                                    |
| `ui/status.c`, `ui/theme.c`                    | the status row (the clock so far); the tokens                                       |
| `ui/lib.c`                                     | the library's songs from SQLite                                                     |
| `ui/player.c`                                  | play and transport, through `demo`'s command queue                                  |
| `ui/page_songs.c`                              | Library > Songs                                                                     |
| `ui/page_gallery.c`, `ui/page_depth.c`         | the examples; the panel's colour depth card                                         |
| `ui/bluenoise.inc`, `ui/tools/bluenoise.py`    | the dither's noise table and what makes it                                          |
| `ui/test_engine.c`                             | the toolkit's own checks                                                            |
| `ui/Makefile`, `tools/ui.py`                   | the host and MIPS builds                                                            |
| `ui/tools/fixture.py`, `measure.py`, `shot.py` | a made-up library of N songs, the timing harness, a panel file as a PNG             |
| `test/ui.py`                                   | the host test: the toolkit's checks, scripts, a live run                            |

The first page is Home (`--page home`); `--page songs` starts on Songs, as the tests do. To run it in the emulator:

```sh
B=$(python3 tools/ci/rebuild.py --dev -- --ui-spike | tail -1)   # a default build has no q2ui
python3 tools/ci/emu_pool.py get "$B"                            # prints the copy's folder, E
python3 ui/tools/fixture.py 9000 E/state/data/database.db        # over the copy's library, emulator stopped
echo q2ui > E/state/data/ui-target; E/q2emu restart "$B"         # q2emu says demo did not start: q2ui did
echo "--input /mnt/data/q2ui-input" > E/state/data/q2ui.args    # the emulator's devices are FIFOs: see Cutover
python3 ui/tools/emu_input.py E wheel 5; E/q2emu shot home.png   # input to both programs; the last Home row leaves q2ui
```

Options go in `/mnt/data/q2ui.args` (one line) or on the command line: `--db FILE`, `--catalogue FILE` (the server library's, `/mnt/mmc/.q2pod/catalogue.db` unless given; `""` for none), `--fonts DIR`, `--icons DIR`, `--limit N`, `--motion 0|1`, `--reduce-motion`, `--light`, `--panel-bits 8|6|5`, `--text awtk`, `--page songs|depth|gallery|card`, `--bench`, and a script's ([Scripts](#scripts)). Stats are written to `/mnt/data/q2ui-stats.json` whenever it comes to rest.

## Measurements

Phase 1's, of the spike (a list that stepped a row a tick, with only the bar's spring between), which are what the go was given on; the engine's own follow in [The engine, measured](#the-engine-measured). All in one leased emulator copy (`q2-emu`, QEMU user mode), 2026-11-26, the same `--dev --ui-spike` build for both programs, libraries of 500 and 9,000 made-up songs (`ui/tools/fixture.py`), by `ui/tools/measure.py`. Flips are the shim's count of pans. CPU is the on-CPU time of every thread of the program (`/proc/PID/task/*/schedstat`), less its own rate at rest, so `demo`'s includes its flush thread and both include the emulator's translation: these are ratios between two programs on one machine, not a Q2's milliseconds. Instructions were not counted: the pool's `qemu-mipsel` is built without plugins, and `test/patch.py`'s Unicorn counts the payload's instructions only, not stock's paint, so there is no equal footing for them yet.

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

### The engine, measured

Under the emulator, 2026-11-26, 9,000 made-up songs, `--bench` on its own clock (frames a sixtieth of a second apart, drawn and turned with no wait). **The machine was not quiet**: its load average was 10 to 22 on 16 cores through every run (other workers' builds), where phase 1's runs had it to themselves, and CPU time a pass moved by a factor of two between runs. So each row is the best of three runs, and the last columns bring it to the terms phase 1 measured in by the one cost both share, the turn: phase 1 turned a pixel in 2.4 ns under the emulator and 12 ns on a Q2 inside q2ui, and a scrolled frame's draw took 2.5 times as long on a Q2 as under the emulator.

| Pass                                             | Pixels turned | Drawn, CPU | Turned | Worst pass | Load, by the turn | A Q2, estimated: drawn + turned | Worst |
| ------------------------------------------------ | ------------: | ---------: | -----: | ---------: | ----------------: | ------------------------------: | ----: |
| A tick on the page, no motion                    |        57,430 |     663 us | 223 us |     964 us |               1.6 |        1.0 + 0.7 = 1.7 ms       | 1.9 ms |
| A tick that scrolls, no motion                   |       120,000 |   2,099 us | 776 us |   9,983 us |               2.7 |        1.9 + 1.4 = 3.4 ms       | 9.3 ms |
| Every frame of slow ticks' glides (408 frames)   |       104,403 |   1,668 us | 593 us |   7,044 us |               2.4 |        1.8 + 1.3 = 3.0 ms       | 7.4 ms |
| Every frame of a fast spin, 100 ticks (217)      |        87,573 |   1,818 us | 638 us |  10,440 us |               3.0 |        1.5 + 1.1 = 2.6 ms       | 8.6 ms |
| The same spin back (217)                         |        84,288 |   1,603 us | 508 us |   8,334 us |               2.5 |        1.6 + 1.0 = 2.6 ms       | 8.3 ms |

Read with these in mind:

- **A frame of a glide is the whole list**, drawn at places between pixels with its fades, its shadow and its platter, where the spike's scrolled frame was the same pixels on the grid with none of those: about 0.7 ms of draw in phase 1's terms against the spike's 0.31. That is the price of the motion, and at an estimated 3 ms of a Q2's 16.7 ms refresh it leaves five sixths of every frame idle.
- **The worst passes** are the ones that bring new rows of text: layout, and up to 3 ms of new glyphs ([Text](#text)). The estimates put them at half a refresh. No pass of any run took a refresh of CPU on this scale except in the run made while the load average was 22, where three of 1,371 did, one of them 119 ms long, which is this machine's doing and not a frame's.
- **Live**, with the wheel's own events sent by `ui/tools/measure.py` (same build, same loaded machine): 778 frames for 20 slow ticks and two 100-tick spins; draw 1.5 ms a frame on average and 7.6 ms at worst, by CPU; 771 of them shown in under 8 ms of wall time. A slow tick is now about 22 frames (the glide, the lean and the indicator's fade, every one drawn) and 45 ms of CPU on this load, against 5.5 ms for the spike's 15 frames and `demo`'s 53 to 58 ms on a quiet machine. At rest: 0.0 ms in 5 s, no frame.
- **The estimates are estimates.** They rest on two ratios from one Q2 run of other code. What they are for is deciding whether the design fits a refresh, which it does with room; the figures to trust come from the device ([Phase 2](#phase-2), device questions).
- **To do when the machine is quiet**: the same table, and `demo` beside it, in one sitting.
- **Not timed**: Home, the Library's group lists and Now Playing. They were written after the move to a machine whose build image cannot yet run the emulator ([Phase 2](#phase-2)), and a host's figures are not on this table's scale. Home's frame while the selection moves is its list's 196 px and the pane beside it (a cover drawn scaled, 179 x 320); Now Playing at rest draws its bar and times four times a second while a song plays, and nothing while it is paused.

### Measured on a Q2

The owner's Q2, 2026-11-26, the `--dev --ui-spike` build V24Ri, both programs on the owner's library: 9,198 songs, all of them the server library's (`catalogue.db`'s `servertable`; the card's `songtable` is empty, its only audio being cached copies in hidden folders). The installed q2ui read `songtable` alone and showed an empty list there, which is how [The library](#the-library) came to read the catalogue too; the q2ui timed is that build of the spike, run from `/tmp` with the installed one paused, at `--motion 1` (as it would ship) only. The phases are `ui/tools/measure.py`'s, the same gestures as the emulator's, run on the Q2 by a script over adb.

| Songs list, 9,198 songs | Slow tick: frames |    CPU a tick | CPU a frame | Fast spin: frames a tick |    CPU a tick | CPU a frame | At rest, 5 s           |
| ----------------------- | ----------------: | ------------: | ----------: | -----------------------: | ------------: | ----------: | :--------------------- |
| `demo`                  |              16.6 | 113 to 114 ms |      6.9 ms |                      1.1 |   30 to 34 ms |       28 ms | 46 ms of CPU, 5 frames |
| q2ui `--motion 1`       |               4.7 | 5.8 to 6.0 ms |      1.3 ms |               1.4 to 1.7 | 3.0 to 3.3 ms |      2.1 ms | 0.0 ms, no frame       |

So on a Q2 a slow tick costs q2ui a nineteenth of `demo`'s CPU and a fast spin a tenth; frame for frame a fifth and a thirteenth. The emulator's ratios (a fourteenth and a ninth, `--motion 1`) understated it: `demo` is 2.2 times slower on a Q2 than under the emulator, q2ui 1.6 times.

|                 | `demo`                                                                                                                | q2ui                                                                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| Resident memory | 41.1 MB (19.8 MB anonymous, 21.4 MB of files), 42.9 MB at most                                                        | 6.9 MB at open, 7.1 MB after the runs (3.5 MB anonymous)                                                        |
| Threads         | 30                                                                                                                    | 1                                                                                                               |
| Opening Songs   | from Library's row: the slide, 21 frames to 0.38 s; no frame for 0.5 s; 16 more to 1.13 s (three opens, within 30 ms) | from its start: the 9,198 rows read and sorted in 285 to 319 ms, the first frame 5 to 15 ms later (five starts) |

The two opens are not the same act: `demo`'s is a page opened in a running program, slide included; q2ui's is the program starting, with no slide, and the [index](#the-library) is what would make its open instant.

The bench on the Q2, three runs:

| Pass                             | Pixels turned |        CPU a pass | of which drawn |            turned |
| -------------------------------- | ------------: | ----------------: | -------------: | ----------------: |
| A tick on the page: two rows     |        42,000 |     667 to 709 us |  185 to 196 us |     477 to 506 us |
| A tick that scrolls, drawn whole |        98,250 | 1,956 to 1,996 us |  769 to 799 us | 1,181 to 1,192 us |
| A tick that scrolls, rows copied |        98,250 | 1,939 to 1,962 us |  540 to 544 us | 1,159 to 1,171 us |
| The whole screen                 |       120,000 | 1,751 to 1,804 us |                |                   |

How each was taken, and its limits:

- **Input.** The wheel's events written to `/dev/input/event3` by a script on the Q2, 10 ms apart with 3 at rest, as the emulator's viewer sends them. The node is opened once: opening it costs about 42 ms on a Q2, so a script that opens it for every sample (as `tools/q2remote.py`'s `event_writer` does) turns the wheel three times slower than it says. q2ui counted 220 ticks of 220 in each run; `demo`'s count cannot be read, and it was sent the same bytes.
- **CPU** is the sum of `se.sum_exec_runtime` over `/proc/PID/task/*/sched` (this kernel has no `schedstat`), less the program's own rate over the 5 s at rest, two runs each. `demo`'s holds all 30 of its threads, whatever they were doing then.
- **Frames** were counted by a busybox loop at nice 19 reading `/sys/class/graphics/fb0/pan`: both programs pan to the other page for every frame, so each change is one. It counted 396 in q2ui's run, q2ui's own count was 396. The loop costs CPU (it added 30% to `demo`'s per tick), so frames come from a third run of their own and CPU from the two without it.
- **Memory** is `/proc/PID/status` (`VmRSS`, `RssAnon`, `RssFile`): this kernel has no `smaps`, so private memory is read as the anonymous pages.
- **Open times** are `/proc/uptime`, 10 ms steps, against the poller's frame log for `demo`, and q2ui's own `open_ms` and `first_frame_ms`.
- One Q2, one library, `--motion 1` alone: the frame-for-frame row (`--motion 2`) was not run on the device.

## Requirements

The owner's, 2026-11-26 (`~/.cache/q2-ui-rewrite/refs/REQUIREMENTS.md`, with the two approved motion references beside it), and where each stands. "Held by" names the check that fails if it stops being true; each was seen to fail with the behaviour taken out (a snap for the glide, a clip round the focus layer, the contents' depth set to 1, the dither's noise set constant, and so on), then put back.

| #   | Asked                                                                                         | How                                                                                                         | Held by                                                                                                                         | Still open                                                                                         |
| --- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 1   | 60 FPS, locked to the refresh, motion by the clock, missed frames counted                     | [The loop](#the-loop), [Motion](#motion): one frame a refresh, closed-form springs, `ui_stats`              | `ui/test_engine.c` (a spring is the same in one step or a hundred); `test/ui.py` (no frame of a glide takes a refresh of CPU)   | The panel's real rate and the counts on a Q2: device questions 1 and 3                             |
| 2   | Smooth gradients: measure the panel's depth, dither                                           | [Gradients](#gradients): every ramp dithered with blue noise; the turn dithers to `--panel-bits`           | `ui/test_engine.c` (a fade's rows keep the ramp's mean and mix two levels; a 6-bit turn keeps the mean and sends no low bits)   | The depth itself: device question 2, by `--page depth`                                             |
| 3   | Smooth edges, right blending, sub-pixel text                                                  | [Edges](#edges), [Text](#text)                                                                              | `ui/test_engine.c` (half-covered edge is half and half on any ground; a box covers its own area; a glyph between pixels keeps its ink) | Kerning is not applied                                                                             |
| 4   | The focused row's contents move and tilt with the platter, each layer its own depth           | [Motion](#motion): the lean, the platter's tilt, text at 1.4 and glyphs at 1.8 of its move                  | `test/ui.py` (the text moves with the platter and further; the row under it stays)                                              | Text is moved, not scaled or tilted itself: a 32 px row's tilt is under a pixel across its text    |
| 5   | Art as layers with parallax                                                                   | [Cards](#cards)                                                                                             | `test/ui.py` (a pushed card moves, and the layer in front moves further)                                                        | Used by no page yet (Home's pane and Now Playing's art are step 3); no lean about the vertical axis |
| 6   | A tick is one row, and the list glides there; a tick mid-glide retargets; a spin keeps gliding | [Motion](#motion): the scroll's spring                                                                      | `test/ui.py` (8 or more frames between rows, none back, none a jump, at places between pixels; a second tick goes on from there) | None known                                                                                         |
| 7   | The focused row above its neighbours: never cut by a pinned row or a fade                     | [Layers](#layers): the focus layer is last and clipped by the screen alone                                  | `test/ui.py` (leaning up it is over the pinned row; leaning down, over the fade at full brightness)                             | The same sweep on pages not built yet (headers, tabs)                                              |

## Parity

What a user sees and can do in `demo` today, against q2ui. The scope is the owner's for Vamp OS (2026-11-26): not carried, and so not rows here, are Tidal, Spotify, AirPlay, Videos, Rockbox, the VU meter and spectrum displays and stock's Internet Radio pages (Vamp's own Radio tuner stays); Audiobooks and Podcasts are deferred. Everything else `demo` and the payload do is in scope and gets its rows as its step starts. **Better** marks where q2ui is meant to differ.

| Surface             | Item                                                                                   | State                                                                                         |
| ------------------- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Every list          | 32 px rows from y 58, seven to a page, text at x 24, the right column 28 px in         | done                                                                                          |
|                     | The selection's bar: accent, 16 px inset, 8 px corners, raised 2% with a soft shadow   | done                                                                                          |
|                     | Tug to tilt: lean 3 px, grow 3.5%, shadow, gleam; spring back                          | done; **better**: a real tilt, and the row's contents move with it                            |
|                     | Press: sink to 0.965, brighter blue, spring back                                       | done                                                                                          |
|                     | One row a tick; the wheel's acceleration and letter jumps (`wheel_accel.c`)            | done; **better**: the list glides                                                             |
|                     | A list's end is its end: no wrap, no bounce, the lean held against it                  | done                                                                                          |
|                     | The foot's fade (rows past y 282 fading out by y 311) and the top's once scrolled      | done, the foot's ramp measured from `demo`'s screen                                           |
|                     | The scroll indicator: 4 px, y 134 to 206, shown a second after a tick                  | done                                                                                          |
|                     | A cut title slides while its row is selected, its ends faded                           | done (title only; a cut value does not yet)                                                   |
|                     | Chevron, key/value, switch (and on the selection), check, greyed row, section header   | done as drawn shapes; compared with `demo`'s pages as each page is built                      |
|                     | Two-line rows with 40 px art (52 px pitch)                                             | built, on no page yet                                                                         |
|                     | Captioned rows, inline sliders, the A-Z index, Edit Library's slide-aside              | not yet                                                                                       |
|                     | Touch: drag, fling, tap                                                                | not yet                                                                                       |
|                     | The hold menu (Centre held)                                                            | the hold reaches the page (`held`); no menu yet                                               |
| Status row          | The clock, Inter Medium 15, Text Secondary, from x 62                                  | done; reads the system's time, not yet `demo`'s time zone and 12-hour setting                 |
|                     | Battery, play state, Wi-Fi, headphones, Bluetooth, lock, sync, offline; the strip      | not yet                                                                                       |
| Songs               | The Shuffle row pinned, its glyph, the accent's ink; the list opens on the first song  | done                                                                                          |
|                     | Title, then the artist inline in 15 px Text Tertiary, cut to fit or dropped under 40 px | done                                                                                          |
|                     | The card's songs and the server catalogue's in one list by title                       | done                                                                                          |
|                     | Centre plays the song from its place in the list; Shuffle shuffles the list            | sent to `demo`'s queue (host-tested); not yet run against `demo`                              |
|                     | The playing song's row; where a song lives                                             | not yet                                                                                       |
|                     | Song Artist and Song Length options; Sorting                                           | not yet                                                                                       |
| Page slides         | Push and pop with parallax, the status row still                                       | done between q2ui's pages                                                                     |
| Light Mode          | The tokens' light half                                                                 | done for what is built (`--light`); not yet read from the setting                             |
| Reduce Motion       | No lean, no slides; the glide and the bar stay                                         | done (`--reduce-motion`); not yet read from the setting                                       |
| Night Shift         |                                                                                        | not yet                                                                                       |
| Songs               | A fast turn up to the pinned Shuffle row leaves the songs at their top                 | done (a fault the owner met on a Q2, 2026-11-26: a step that passed over the first song left the list a row down) |
| Home                | Split: the list at the left, the playing cover over the pane at the right              | written, held by `test/ui.py` on the host; **not yet seen beside `demo`'s in an emulator**    |
|                     | The first row is Playing or Paused while something is, and opens Now Playing            | the same                                                                                      |
|                     | Return opens Now Playing while something plays                                         | the same                                                                                      |
|                     | The cover moves in its pane as the selection moves; **better**: a card of layers        | the same                                                                                      |
|                     | Radio, Games, Folders, Cover Flow, Search, Settings                                    | not yet: reached through the last row, Old Menus, which leaves q2ui for `demo`'s menus         |
|                     | The clock in Text Primary over the list; the status glyphs over the art in one ink      | not yet                                                                                       |
|                     | Edit Home, the Full layout, Home's Settings and Streaming sub-lists                     | not yet                                                                                       |
| Library             | Its list                                                                               | Songs, Artists and Albums; the other rows through Old Menus                                    |
|                     | Artists: each artist, then that artist's songs by title; Centre plays them from the row | written, host-tested; `demo`'s artist page (albums first, a monogram header) is not matched    |
|                     | Albums: each album with its artist, then its tracks in order with their numbers         | written, host-tested; one-line rows, no covers yet; `demo`'s album page header is not matched  |
|                     | Playlists, Genres, Composers, Years, Ratings, Recently Added, Most Played, Folders      | not yet                                                                                       |
| Now Playing         | The cover at 150 px with 6 px corners, title, artist, album, the bar, the two times     | written, host-tested; not yet seen beside `demo`'s                                            |
|                     | Centre plays and pauses; the wheel is the volume                                       | sent to `demo`'s queue (host-tested)                                                          |
|                     | The art settles smaller on pause; **better**: it leans and its picture moves when pushed | written                                                                                       |
|                     | The bar creeps between seconds                                                         | done by the clock between `demo`'s writes of the position                                     |
|                     | The lines' marquee, the heart, More, shuffle, repeat, radio and Up Next, lyrics, rating | not yet                                                                                       |
|                     | The volume's own display, a track change's slide, the cover's flight from Home          | not yet                                                                                       |
| Settings            |                                                                                        | not yet (step 4)                                                                              |

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
- **Memory while both run.** 128 MB, no swap, the player's crossfade hold is 6 MB. The spike is 7.1 MB resident (3.5 MB anonymous) beside `demo`'s 41.1 MB on a Q2; the whole q2ui adds an estimated 4 to 6 MB to that during the transition, measured again before step 2 ships.
- **Fonts and scripts.** stb_truetype does no shaping. Stock does none either, so nothing regresses, but it is not a base for Arabic or Indic titles.
- **Rebasing does not go away until step 8.** Until `demo` is off, a new stock version still needs the hooks rebased; each step makes that smaller.
- **The emulator flatters.** Every ratio here is under QEMU. A Q2's cache and memory speed could narrow them; nothing suggests they could reverse, since the saving is work not done (layout, measuring, clock reads, idle wake-ups), not work done faster.

## Phase 2

Its first three steps:

1. **Time the spike on a Q2**: done 2026-11-26, [Measured on a Q2](#measured-on-a-q2). The owner, on seeing it, said go, and that the rows should slide into view, not jump a row a tick.
2. **The toolkit** (step 1 of [Migration order](#migration-order)): done 2026-11-26 for what the Library, Home and Now Playing need first: the engine and everything in [The toolkit](#the-toolkit), with [Requirements](#requirements) 1 to 7 met and held by tests. Left in it, and built with the pages that need them: the status row's glyphs, Night Shift, touch, the widgets the toolkit lists as not here yet.
3. **The hand-over** ([Cutover](#cutover)): done 2026-11-26, built and run in the emulator and held by `test/fw/test_q2ui_gate.py` and `test/ui.py`. q2ui is the third holder of the screen in the payload's two gates and `demo`'s child; Centre on a song plays it through `demo`'s command queue; Return hands the screen back. With it, q2ui's Songs is a page a user can actually use.
4. **The Library, Home and Now Playing** (Vamp OS's M1, with the hand-over): begun 2026-11-26, and since the move to the Mac held on the host by `test/ui.py` (`pages`, and the route across a restart), each check seen to fail with its behaviour taken out. **What is missing is an emulator**: the Mac's arm64 build image cannot yet run the pooled emulator (its copy's `qemu-mipsel-static` is an x86-64 program and the VM has no MIPS `binfmt_misc` entry, so the guest's first `exec` fails), so these pages have not been run beside `demo`, compared with its screens or timed there. First written 2026-11-26. Home (the list and the cover's pane), the Library's list, Artists, Albums and a group's songs, and Now Playing (`ui/page_home.c`, `page_library.c`, `page_now.c`, `ui/now.c`) are written and drawn on the host, not yet run in the emulator, compared with `demo`'s screens or given tests of their own, so [Parity](#parity) does not list them as done. q2ui can ask `demo` for a page it has not got: it names the page in `/tmp/q2ui.open` and ends with exit code 3, `demo` opens the page (`navigator_to`, for a name made of lower-case letters and underscores ending `_page` and nothing else), and q2ui is started again, on the pages and rows it had open (`ui_route`), once the page has been left. No row uses it yet: most of `demo`'s pages behind Home's and the Library's rows (Radio, Games, Cover Flow, Years, Ratings, Most Played) are built by the payload's own openers (`tuner_open`, `games_open`, `era_menu`, `ratings_menu`, `most_played`), not by a stock page's name, so the gate has to call those, and each has to be seen to work in an emulator first. Until then Home's last row, Old Menus, leaves q2ui for `demo`'s menus. Then **Settings**.

**Device questions**, which only a Q2 answers and nothing above waits on:

1. The panel's refresh: `cat /sys/class/graphics/fb0/modes`, and `refresh_us` in `/mnt/data/q2ui-stats.json` once this q2ui has run there (it is worked out from the mode's pixel clock and margins).
2. The panel link's colour depth: `dmesg | grep -i -E "lcd|slcd|jzfb|h0209"` for what the driver says, then by eye with `--page depth` in `/mnt/data/q2ui.args` ([Gradients](#gradients)); the answer goes into `--panel-bits`.
3. The frame counts on the device after scrolling Songs: `dropped`, `late`, `over_budget`, `cpu_over_budget`, `work_us_max`, `work_hist_ms` and `glyphs_deferred` in the stats file.
