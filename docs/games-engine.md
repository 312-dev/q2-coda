# The game engine

A small engine inside the UI payload for games drawn at the panel's refresh: a fixed 60 Hz step, a frame's input as one record, drawing straight into the screen's buffer inside the rects a step said changed, the Clicker's sounds, and sealed saves. It is `patch/engine.c` and `patch/engine.h`; `patch/engine_demo.c` is a scene that proves it and shows a game's four calls. Games (`patch/games.c`) is its page: the chooser lists the engine's games after its own (those `GAME_*` in `patch/peq.h` ships: none in this build, so Bricks is the first row and Caller Ten the second), and the pause menu is Games' pop-up.

This page is the design, the measurements behind it, the API, the budgets and the libraries considered. The older games (Sudoku, Vortex, Solitaire, Snake) are in [internals: Games](internals.md#games) and do not use the engine.

## Where it runs

**In the payload, on demo's UI thread.** The alternative, a program of its own in the root filesystem that takes `/dev/fb0` as `q2video` does ([internals: Videos](internals.md#videos)) or a card game's ([Card apps](internals.md#card-apps)), was weighed and set aside:

| Question           | In the payload                                                                                  | A separate program                                                                                                         |
| ------------------ | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Sound under music  | The Clicker's route: q2player mixes the sound into the song ([Key Tone](internals.md#key-tone)) | None. The player holds the one PCM (`hw`, no `dmix`), so a second program cannot open it while a song plays                |
| The user's library | In reach (the music quiz reads the library database and drives the player)                      | Would need a protocol to demo for every query and every play                                                               |
| Input              | The wheel's own thread and speed, keys and touch, already decoded                               | demo must stay the reader and forward each event as a datagram (what card games do), a pass of the UI loop late            |
| Text and theme     | Inter and the theme's faces through stock's canvas                                              | Its own font rasteriser and font files                                                                                     |
| Memory             | The game's state on demo's heap (2 to 8 KB a game)                                              | A second process: its own libc mappings and stacks, some hundreds of KB next to the player on a device with no swap        |
| Smoothness         | One paint a refresh, paced by the flush thread's wait for vertical sync (measured below)        | The same pacing at best: it would pan `/dev/fb0` and wait for the same sync                                                |
| Size               | 7.6 KB of the payload for the engine and its demo (below)                                       | Payload room spared, but 225 KB is all the root filesystem has free, and the forwarding code would still be in the payload |

Nothing the separate program offers is needed, and it loses sound under music outright. The payload's limit is the cost, so each game has a byte budget ([Budgets](#budgets)).

## The panel

The screen is `lcd_h0209s`, 320 x 375 physical, shown turned as 375 x 320. Its mode is in `module_driver/lcd_h0209s.ko`'s `fb_videomode`: refresh 60, 320 x 375, margins 24 and 20 (horizontal), 12 and 16 (vertical), sync lengths 20 and 2. That is 384 x 405 clocks a frame, and at 60 Hz 9,331,200 Hz, the "9.3 MHz pixel clock" of [hardware](hardware.md). So **the refresh is 60 Hz and a frame is 16.67 ms.** `ENG_HZ` is that number. ponytail: this is the driver's stated mode, not a measurement of a panel; no Q2 was on hand to time `FBIO_WAITFORVSYNC`. `tools/q2remote.py profile` on a development Q2 would settle whether the real figure is a little off 60.

The path a frame takes is stock's ([internals: Night Shift](internals.md#night-shift) describes the same path): AWTK paints each changed rect into the screen's `lcd_mem` buffer (375 x 320, BGRA8888), `lcd_end_frame` hands the frame to the flush thread, which turns it onto the page of `/dev/fb0` not on screen, pans to it (`FBIOPAN_DISPLAY`) and waits for vertical sync (`FBIO_WAITFORVSYNC`; `soc_fb.ko` is loaded with `pan_display_sync=1`). The UI thread's next paint waits for that flush, so **while the picture changes every pass, the UI loop runs once a refresh**: the emulator shows it, where a sync is a 16 ms sleep and the demo scene flips every 17 ms ([In the emulator](#in-the-emulator)). ponytail: seen in the emulator and read from stock's flush path, not timed on a Q2. If a Q2's loop came round sooner than its panel, the loop below would still step by the clock (its second case), a frame now and then shown twice. The engine does not flip pages or wait for sync itself; it rides this.

## The frame loop

`eng_frame` is an AWTK timer of `ENG_TIMER_MS` (4), so it is due at every pass of the UI loop. Each pass it looks at the time since the last pass (stock's millisecond clock):

- **15 ms or more** (`ENG_SNAP_LO`): the pass followed a paint, so the gap is whole refreshes. It is rounded to the nearest count of them, one step each: 16 and 17 ms are both one step, 33 is two, at most `ENG_MAX_STEPS` (4); time beyond that is dropped, so a stall never becomes a burst.
- **less**: nothing was painted, the loop came round early. The time is added to what is owed (`acc`, in 1/60000 s) and a step is taken when a whole one is owed.
- **the timer's first pass** is a step whenever it comes, so a turn-based game answers a key on the next pass.

So on a Q2, where a pass is one refresh, a game steps exactly once per displayed frame and never 0 or 2 by the millisecond clock's rounding: motion is even, with no interpolation needed. A step is always 1/60 s of game time; a game never reads a clock.

After the steps, each rect the game marked (`eng_dirty`) is invalidated on the sheet. Stock paints each apart with the canvas clipped to it, and `games_paint` calls the game's `draw` once per rect through `eng_paint`. A step that marks nothing paints nothing.

`ringnav_sleep`'s idle sleep is off while the timer runs (`games_live`, `np_animating` in `patch/navigation.c`). A turn-based game (`ENG_STILL`) keeps the timer only after input and while its `step` returns 1; at rest there is no timer and the loop idles as on any page.

**Pausing.** The screen off or another window on top: `eng_frame` ends its timer and Games shows "Paused" (`engine_held`); Center or Play/Pause resumes. Return opens Games' menu (Continue, Quit). Either way `eng_hold` stops the timer, gives the wheel back to ticks and calls the game's `pause`, which is where a game saves. Held Return is Home, as everywhere.

**Nothing is allocated in a frame.** `eng_open` makes one `calloc` of the engine's record and the game's state (`size`), freed by `eng_close`. The payload's scratch holds one pointer (4 bytes).

## Measurements

All on the demo scene, build `--dev --new-player`.

### On MIPS

`test/patch.py` runs the built payload under Unicorn and counts the payload's instructions (stock's text drawing and the flush are mocked, so these are the engine's and the game's own):

| What                                                                | Instructions |
| ------------------------------------------------------------------- | ------------ |
| A frame's step (ball, paddle, the wheel turning), the dearest of 60 | 499          |
| That frame with its changed rects painted (2 to 5 rects)            | 61,511       |
| The whole sheet painted (375 x 290: the start, a pause, a resume)   | 703,036      |

The suite bounds each a little above what it measured (600, 70,000 and 780,000), so a change cannot quietly make a frame dearer. The whole sheet is 6.5 instructions a pixel, nearly all of it the black fill; a frame of play repaints two to five boxes of about 15 x 15 px.

**In time.** [Night Shift](internals.md#night-shift)'s scan costs 9 to 20 instructions a pixel and a full screen of it takes 2 to 3 ms on a Q2, which is 1.3 to 1.9 ns an instruction; 2 ns is used here, on the safe side. The demo's dearest frame is then 0.12 ms of the 16.67 ms a refresh allows, and a whole-sheet paint 1.4 ms. For scale, a Cover Flow frame is bounded at 6.45 million payload instructions (`test/patch.py`). ponytail: instructions are not time (cache misses on a 480 KB buffer, the other core's work); the figure is an estimate until a Q2 times it, and the bounds are in instructions because those can be tested.

### In the emulator

`q2-emu` runs the same `demo` under QEMU; its shim answers `FBIO_WAITFORVSYNC` with a 16 ms sleep and counts every page flip. A capture of the demo scene playing (`design/games/capture.py`, which records each flip's time and frame; the video is `design/games/engine-demo.mp4`):

| Capture                      | Flips a second | Gap between flips: median | 95th | 99th | Longest | Over 34 ms |
| ---------------------------- | -------------- | ------------------------- | ---- | ---- | ------- | ---------- |
| 6 s, the ball alone          | 58.3           | 17.0 ms                   | 18.1 | 20.1 | 33.7 ms | 0          |
| 10 s, the wheel and a finger | 58.6           | 16.9 ms                   | 17.9 | 20.5 | 44.2 ms | 1          |

The shim's sleep is 16 ms plus the frame's work, so about 58 flips a second is the emulator's ceiling, not the engine's: a median gap of 17 ms means the loop painted at every sync the shim gave it. The longer gaps, one of them 44 ms, came with the host busy (its load average was about 5, other builds running beside the capture); nothing here tells them apart from a slow frame of demo's, so they are stated, not explained away. A gap is whole steps to the engine (25 ms or more is two), so the ball's place stays true to time; nothing drifts.

### Measuring

- MIPS: `python3 test/patch.py BUILD` prints `Game engine on MIPS: ...`. A new game adds its own scripted second there with its own bounds.
- Emulator: `python3 capture.py EMU_DIR SECONDS OUT.mp4` beside a running emulator copy, with the game on screen.
- Host: `python3 -m pytest test/fw/test_engine.py` (the loop's steps for each gap, input, rects, drawing, saves, the demo).

## API

`patch/engine.h` is the reference; this is the shape.

```c
struct eng_game {
    const char *name;
    unsigned size, flags;                                 /* state bytes, zeroed; ENG_RAW, ENG_STILL */
    void (*init)(void *state, int h);                     /* h: the sheet's height */
    int (*step)(void *state, const struct eng_input *in); /* 1/60 s; 1 while something moves */
    void (*draw)(const void *state, struct eng_surface *s);
    void (*pause)(void *state);                           /* may be 0 */
};
```

A game is one such record, listed in `eng_games[]` (`patch/engine.c`) before `eng_demo`. `eng_count` is what Games lists; the demo is counted only in a `--dev` build with `[Q2POD] ENGINEDEMO=1` in `config.ini`, and a release build leaves it out of `eng_games[]` (`#if Q2POD_DEV`), so none of `engine_demo.c` is linked.

### Input

`struct eng_input`, one a step. A frame's edges and travel go to its first step; what is held stays.

| Field                         | Meaning                                                                                                                                                                               |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `held`, `pressed`, `released` | bits `ENG_CENTRE`, `ENG_PLAY`, `ENG_PREV`, `ENG_NEXT`, `ENG_TOUCH`. A press comes from `ringnav_keydown` (`games_keydown`), so a held button is known and stock makes no click for it |
| `ticks`                       | the wheel's detents, clockwise positive (the wheel's sensitivity setting applies, as in a list)                                                                                       |
| `fast`                        | the same with each tick worth 1 to `ENG_ACCEL_MAX` (10) by the wheel's speed: the volume's curve ([Wheel volume](internals.md#wheel-volume)), Rockbox's `(speed / 270)^4`             |
| `wheel`                       | with `ENG_RAW`: raw travel, 200 a turn, clockwise positive, read from the wheel's own thread (`wheel_travel`); no ticks then                                                          |
| `tx`, `ty`                    | the finger on the sheet while `ENG_TOUCH` is held or just released                                                                                                                    |

Return never reaches a game. A game that sets neither flag gets ticks and runs its timer always.

### Drawing

`draw` gets a `struct eng_surface`: the screen's pixels, the sheet's origin, and the rows and columns of this paint. It paints its whole scene, background first; every call clips, so the cost is the changed part's. Coordinates are the sheet's, colors `0xRRGGBB` as they reach the screen (Light Mode does not flip them; Night Shift still warms them, as it reads every pixel written).

| Call           | Draws                                                                                          |
| -------------- | ---------------------------------------------------------------------------------------------- |
| `eng_fill`     | a box, solid                                                                                   |
| `eng_disc`     | a disc with a smoothed edge, its center and radius in 16.16: it moves by sixteenths of a pixel |
| `eng_round`    | a box with round corners, smoothed                                                             |
| `eng_text`     | ASCII in the theme's face through stock's canvas (left, centered or right); returns its width  |
| `eng_inset(y)` | not a drawing: the columns the glass's 80 px corners hide at each end of the sheet's row `y`   |

A buffer that is not stock's 375 x 320 BGRA8888 `lcd_mem` (a window animation's snapshot of another layout) is left as stock drew it.

### Full screen

A game flagged `ENG_FULL` gets the whole 375 x 320 screen: Games moves its page over the status row, hides the row while the game runs and shows it again at a pause, so the clock and battery show under the pop-up (`full_screen`, `bar_shown` in `patch/games.c`). Its sheet's height is 320. Leaving it puts the page back under the row.

`eng_state_of(game)` is that game's state while it is the one open, running or held, else 0: what a game's own timer or a hook outside its `step` reads to know whether it is still open (Caller Ten's `ct_watch`, its `callerten_sound`).

### Sound

`eng_sfx(ENG_SFX_TICK | ENG_SFX_TAP | ENG_SFX_ALERT)` plays one of the Clicker's three sounds by the Clicker's route (`clicker_route`): q2player's under Headphones or Both (mixed into the song while one plays, in silence of its own otherwise), the buzzer's by the Clicker's rule, nothing with the Clicker off. The music is never paused, ducked or reopened.

ponytail: three sounds, and the buzzer has one. Sounds of a game's own belong in q2player's table (`SOUNDS` in `player/output.c`, a pitch, a length and a decay each, made at first use): add kinds there and names to `mcl-click`, with `test/player/run.py`'s click tests extended; no payload bytes beyond the names. Not done here: it is a player change with its own tests, and one for all five games rather than per game.

### Saves

`eng_load(name, buf, size, magic)` and `eng_save`: the file is `/mnt/data/game-NAME`, its first word the magic, its last the FNV-1a of the rest (`games_seal`, `games_ok`), written to `NAME.tmp`, flushed, `fsync`ed and renamed over (`blob_io`). A power loss leaves the old file or the new one; a torn, foreign or missing file reads as no save, the buffer zeroed. The write waits on flash, so it belongs in `pause` or at a game's end, never in a frame of play. A failed write toasts "Couldn't save".

### Fixed point

`fx` is 16.16: `FX()`, `FX_INT`, `fx_mul` (a 64-bit product, one `mult` on MIPS), `fx_div` (32-bit, exact to 8 fraction bits, for |a| under 128). `eng_sin`, `eng_cos`, `eng_atan2` (1024 a turn) and `eng_sqrt` are Vortex's tables. The X2000 has an FPU and the payload uses `float` where stock's vector canvas wants it, but a game's `step` and `draw` stay in integers: results are then the same on the host tests, under Unicorn and on the Q2, and a test can assert a ball's place exactly.

## The demo scene

`patch/engine_demo.c`: a ball (a 6 px `eng_disc` at 2.25 and 2.5 px a step, so its edge moves by parts of a pixel) bouncing in a field, a paddle the raw wheel moves 2 px a position, a finger places and Previous and Next push; Center changes the speed of the next serve, Play/Pause leaves (`eng_quit`), a paddle hit clicks and counts, a miss beeps and serves again, and the best count is saved at a pause (`/mnt/data/game-demo`). A frame marks the ball's old and new box, and the paddle's when it moved: 2 to 5 rects of about 15 x 15 px.

To see it: a `--dev` build, `ENGINEDEMO=1` under `[Q2POD]` in `/mnt/data/config.ini`, then Library > Games > Engine Demo.

## Budgets

**The payload** may hold 1,572,864 bytes of code and constants (1,048,576 when this was measured). With the engine and its demo, `patch_bytes` is 982,392 (build `--dev --new-player`), so 66,184 bytes are free. The engine is 5.9 KB of code and constants (`engine.o`), the demo 1.7 KB.

| Game (see [Games](#games)) | Payload bytes | Heap (`size`) | A frame, worst (instructions) | Whole sheet |
| -------------------------- | ------------- | ------------- | ----------------------------- | ----------- |
| Brick breaker (Bricks)     | 9 KB          | 2 KB          | 600,000                       | 2,000,000   |
| Turret shooter             | 9 KB          | 4 KB          | 600,000                       | 2,000,000   |
| Music quiz (Caller Ten)    | 28 KB, art in the root filesystem | 256 KB with its art | 600,000                       | 2,000,000   |
| Klondike solitaire         | 12 KB         | 2 KB          | 600,000                       | 2,000,000   |
| Sudoku                     | 8 KB          | 2 KB          | 600,000                       | 2,000,000   |
| **All five**               | **46 KB**     | one at a time |                               |             |

- **A frame's hard limit is 2,000,000 instructions** of the payload's own (about 4 ms by the estimate above) for the step and every rect's paint together; 600,000 is what a game is held to, so stock's own painting of the same rects, the flush's turn of them onto the framebuffer and the loop's polls keep the rest of the 16.67 ms.
- **Heap** is one game's at a time, freed on leaving it. The device has little to spare next to the player; 8 KB is the most any game is given.
- **Scratch** (131,072 bytes, about 31 KB free): the engine uses 4. A game uses none: its state is the heap's.
- **The root filesystem** (at most 75,497,472 bytes, `ROOTFS_ROOM`) is not used by the engine at all.
- The older four games are out of the payload in this build (`GAME_*` in `patch/peq.h` all 0), which gave back 15,568 bytes (`patch_bytes` 1,331,960 to 1,316,392, 2026-11-22); turning them back on costs that again.

## Games

Each game built on the engine gets a subsection here: its file, rules in a paragraph, its save, and its measured frame figures. The briefs they are built from are in the emulator project's `design/games/BRIEFS.md`.

Every one is an original game in its genre. None reproduces an Apple iPod game's name, screens, art, sounds or level layouts; the briefs say what makes each its own.

### Bricks

`patch/game_bricks.c` (`eng_bricks`, `ENG_RAW`), Games' first engine row in every build. A paddle along the bottom, a ball, and above them a lattice of hexagon-packed discs: seven offset rows of 16 and 15 cells of radius 8 at a 20 px pitch across and 17 down, so a rebound comes off a curved face and the ball's heading keeps changing. A white cell takes one hit; a gray (`#ABABAB`) takes two and turns white at the first; the level's one Accent Blue keystone, broken, takes its six neighbours with it (`around`, either row parity). A cell broken scores the level's number, so the score is cells broken times the level. Clearing the lattice is the next level; a miss costs one of three lives, and the last ends the game. The score line is the sheet's top row: the score in Inter 15 px at the left, the lives as three small discs at the right, both drawn in from the glass's corner (`eng_inset`).

**Levels** are made, not drawn: `build` takes the game's seed (`rand_below` at a new game) and the level number alone, picks one of four masks mirrored about the middle column (a diamond, solid or hollow; a ring; twin columns; chevrons) in turn from the seed, draws its sizes from an xorshift of the two, makes grays more often as the levels go (one cell in 8 at level 1, one in 2 from level 7), and puts the keystone on the middle column where that has a cell. A mask that leaves under 12 cells is drawn again; the eighth try fills the lattice, so no level is empty. The ball's speed is 2.5 px a step at level 1 and 0.15 more a level, to 5.

**The ball** is 16.16 fixed point, its heading 1024 a turn and its speed a scalar, so a rebound never changes its speed. `travel` sweeps its path for the step against the cells within two rows and two columns of its place on the lattice (at most 25), the walls and the paddle's top, in 1/64 px with the quadratic solved in 64-bit products and 32-bit divisions: the first contact stops it there, turns it (a cell's face mirrors it about the normal, `eng_atan2`), and the rest of the step goes on, four contacts a step at most. A ball at 5 px a step meets a cell 13 px out from its center, so it cannot pass one between steps. No heading is shallower than 15 degrees from the horizontal (`steep`). The paddle sets the way out by where it was hit: straight up in the middle, 60 degrees from vertical at its ends.

**Controls.** The raw wheel moves the paddle 2 px a position, no acceleration; Previous and Next held, 4 px a step; a finger, to its x. Center launches from Ready (the ball rides the paddle a third of its width out, alternating sides) and plays again after the end. Play/Pause does nothing.

**Screens.** Ready shows "Level N" and "Center to launch" (Text Tertiary) under the lattice. Clearing the lattice fades the next one in over 20 frames: each row takes 4 of them, the rows starting in turn, so a frame repaints two rows at most, their discs drawn in their colors scaled toward black (`dim`). The brief asked for the fade by an alpha blend over the drawn rows; that measured 634,287 instructions for its dearest frame, over the 600,000 a frame may, so the colors are scaled before drawing instead, which looks the same and skips a pass over the rows' pixels. The end shows the score, the best (or "New best") and "Center: play again". Pausing is the engine's.

**Sound** is the Clicker's three as rhythm: a tick off the paddle and for a gray's first hit, a tap for a cell broken, two taps six frames apart for the keystone, three ticks for a level cleared, the alert for a miss.

**Save:** `/mnt/data/game-bricks`, `BRK1`: the best score and the best level, written at the game's end when either was beaten (and tried again at a pause if that write failed), never during play. A save with a best over 99,999,999 or a level over 99 reads as none. An unfinished game is not kept.

**Frames.** A frame of play marks one rect for the ball's old and new place and one for the paddle's when it moved, a broken cell one more, the score line one; the keystone's seven are one joined rect. Measured on build `--dev --new-player`, on OLED's 262 px sheet:

| What                                                       | Instructions (MIPS, payload) | Bound in `test/patch.py` |
| ---------------------------------------------------------- | ---------------------------- | ------------------------ |
| A frame's step, the dearest of a scripted second of play   | 2,157                        | 2,500                    |
| That second's dearest frame with its changed rects painted | 216,412                      | 250,000                  |
| The keystone taking its six neighbours, painted            | 174,268                      | 200,000                  |
| The dearest frame of the next level's fade, painted        | 233,143                      | 270,000                  |
| The whole sheet with a full lattice                        | 1,177,926                    | 1,450,000                |

In the emulator, 10 s of play (`design/games/bricks/bricks-play.mp4`, a finger following the ball, `bricks/autopilot.py`): 58.2 flips a second, median gap 17.03 ms, longest 24.1 ms, none over 25 ms, with the host's load average near 8. Ready and the end paint nothing while nothing moves, so a capture that waits there shows a long gap between flips that is the game at rest, not a slow frame. The payload grows by 7,344 bytes over the engine's build (989,736 against 982,392), of the brief's 9 KB; the state is 208 bytes of the brief's 2 KB; no `.scratch`.

### Caller Ten

`patch/game_callerten.c` (`eng_callerten`, `ENG_STILL | ENG_FULL`), Games' second engine row: a radio call-in contest on the user's own library, the redesign of the music quiz brief (the design is the emulator project's `design/games/guess-the-song/`, its SPEC.md, STYLE.md and element sheet). An evening side table fills the screen: a wooden table radio plays a clip, a red rotary phone is the control and a notepad holds the four answers. The radio says what the game is doing (ON AIR, STAND BY, one line on its glass), ten clock lamps count the seconds, the dial and the pad's chosen row always show the same number.

**Rules.** Tune the radio to one of three contests (the needle slides between three stations; the pad lists them with their bests): Titles ("Name the song"), Artists ("Who is playing?") and Deep cuts (titles of songs played once or never: the [play counts](internals.md#play-counts) of two or more left out). A round plays a clip from 30 to 60 percent into its song; the round's clock starts when the player answers that the clip is heard, not when it was asked for, and its points fall from 100 to 10 over 10 s (`100 - 3 at / 20` steps, the last lamp staying lit; there is no timeout). Ten callers make a contest, three wrong answers end it early ("LINES ARE CLOSED"). A wrong answer is struck in red and the right one shown green with a check; three right in a row light "HOT LINE x3" on the glass and swell ON AIR's halo once, with no score of its own. The summary lists the songs missed: Center on one plays it in full from its folder (`songs_open`) and leaves for Now Playing; Play/Pause tunes in again.

**Controls.** The wheel tunes, dials 1 to 4 (it stops at each end, no wrap) or picks a missed song; Center calls in, places the call, plays a missed song; Play/Pause replays the clip from its start once a caller (the replay lamp goes dark), and on the summary starts a new contest; a finger on a row answers or picks it. Previous and Next do nothing. Return is Games' menu.

**The library.** A worker thread (`pick_worker`, as slideshow.c's shape) reads songs in a random order from `main.songtable` alone, through `era_sql` (the library scope applies; naming the table in full keeps a server's `cat.servertable` out, and a row of a server's at a card path, `catalogue_path`, is skipped too), with `MUSIC_WHERE` (no audiobooks, podcasts or `.m4b`), no CUE tracks and a title (Artists: an artist too), keeping 64. The first 26 are checked as answers on the worker: the file there (`access`) and at least 40 s long by TagLib (`seconds`, durations.c's way), 13 kept: ten callers and three spares. A contest needs 12 songs and four that read differently ("A contest needs 12 songs", or 4 artists, on the pad under NO SIGNAL; no playable answer says so too). Each round deals its three decoys on the UI thread from the 64, those of the answer's genre and decade first, never two that read the same (ASCII case folded). One round ahead a small thread looks whether the next answer's file is still there (`look_ahead`): a song gone mid-game is passed over before its round, and a clip the player answers `error` for, or that is not heard in 5 s, is passed over at once for a spare, the caller's number kept; with no spare left the contest ends short. The worker's rows are a block of its own (`struct pick`, refcounted), read by the game only after its `done`, so a game left while it runs frees nothing it uses.

**Playback.** The game owns the sound while a contest runs. At its start `take` stops Spotify and a radio and holds the Library's song where it was (`now_hold`, as a video does, [Now Playing source](internals.md#now-playing-source)), noting whether it played. Each clip goes straight to q2player over demo's player socket (`{mcl-stop}{set-songurl\PATH|ss=S|512|20|0|17|90}`, demo's own fields), never through the queue, so no play count, scrobble, resume place, Up Next or car title moves; the jack is readied as stock's start readies it (`dac_take`) and the DAC kept on. The game asks `{player-status\request}` every 6 steps while a clip is cued and reads the answer from demo's reply list (`mcl_reply_take_by_key`, stock's, never waiting); `playing` is the player's state once the clip is heard, which starts the clock. The reveal pauses the clip; a pause of the game (the menu, the screen off) pauses it and Continue plays it on. While the game holds the sound `addon_game_audio` says so (`callerten_sound`), so stock's player starts nothing over it ([internals: card games](internals.md#card-apps)). Games frees a game's state at its close without calling it, so `pause` arms a watch (`ct_watch`, every 100 ms, `eng_state_of`): once the game is gone it stops the clip and gives the Library's song back as it was, playing on from its place (`now_take_back`) or loaded paused there (`now_release`), a stopped one left stopped; it also frees the art and the worker's rows.

**Screens.** Tune-in, stand by (the worker; STAND BY lit, three lamps chasing), a round (the question on the glass; ON AIR, the rings and the ten lamps once heard), the call (the handset lifts 27 px and tilts 13 degrees over 6 frames with a back-out, the clip plays on 20 frames), the reveal (60 frames: the wash in three steps, the check drawn in 6, the strike in 8, the points and the score counting up in 20), the summary (the wide sheet comes up from under the screen's edge over 14 frames with a 5 px overshoot, the score counts up in 40, NEW BEST) and NO SIGNAL. A title is drawn at 17 px and tried at 16, 15 and 14 before it is cut with "..." (`fit_text`); the chosen row, cut, scrolls its whole title (40 steps still, 1 px every 2 steps, 40 still, back), and the right row on the reveal once through. Under Reduce Motion (`motion_reduced`) the dial does not spring, the handset is up at once, the rings stand still and the summary is there at once. The screen is kept lit through a round (`eng_awake` each second), as a video keeps it.

**Art.** `assets/games/callerten.bin` (`usr/share/q2games/callerten.bin`, 192,228 bytes), made with `patch/game_callerten_art.h` by the design's `src/export.py` from the same drawing code as the mockups: one 256-colour palette (median cut refined by k-means over the pictures' own pixels), 100 pictures each a rectangle at its place cut from a whole render of the scene in that state (the room with the radio, phone and pad at rest; ON AIR's three states, the replay lamp, the rings' three frames, each lamp lit amber and red, the dial on each number and its three turn steps, the hub's answer, the handset's five poses and its cord's, the pad's sheets, rows, badges, check, strike boxes, the summary's sheet and rows), row run-length coded with row offsets, and glyph masks of Righteous (15 and 14 px) and Bungee (18, 21 and 36 px digits) for the type the game sets at run time (OFL, `assets/games/callerten-fonts.txt`, [third-party](third-party.md#artwork)). The glass's corners are black in the art. It is read whole at `init` and checked by its size and seal (`games_ok`); a missing or damaged file toasts "Caller Ten's pictures are missing" and goes back to the list. `blit` decodes a picture's rows within the part being painted, skipping where a sheet drawn over it covers the room. The library's titles and artists go through stock's canvas in the theme's face or the fallback that has the script (`theme_face`, `draw_centred`), so every script the firmware draws shows.

**Sound** is the Clicker's three (the SPEC's tones are a q2player change for all games, [Sound](#sound)): a tick at each detent, a tap before the clip starts (never over it), three taps after a right answer and two ticks more on a streak, the alert for a wrong one, a tap at the end and three for a new best.

**Save:** `/mnt/data/game-callerten`, `CTN1`: the best score of each contest and the contests played, written at a contest's end. A best over 1,000 or a count over 99,999,999 reads as none.

**Frames.** FRAMES_TABLE

## Libraries

Looked at for vendoring. The project is GPL-3.0, so MIT, BSD, zlib, public domain and GPL-2.0-or-later code could all be taken; none is, because each need is smaller than the library that meets it.

| Need                          | Candidate                                                           | License               | Size                                                            | Verdict                                                                                                                                                                                                                  |
| ----------------------------- | ------------------------------------------------------------------- | --------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Fixed-point maths             | [libfixmath](https://github.com/PetteriAimonen/libfixmath)          | MIT                   | Q16.16 in C; its caches are 32 KB and 80 KB unless compiled out | No. Two inline functions and Vortex's 257-entry sine and arctangent tables, already in the payload, are the whole need                                                                                                   |
| Physics and collision         | [Chipmunk2D](https://github.com/slembcke/Chipmunk2D)                | MIT                   | a rigid-body engine in floating point, over 100 KB compiled     | No. Far past the payload's room, and nothing here is a rigid body                                                                                                                                                        |
| Collision tests only          | [cute_c2](https://github.com/RandyGaul/cute_headers)                | zlib or public domain | one header, floating point                                      | No. A disc against a disc and a disc against a box are a few integer lines each, exact and testable in fixed point                                                                                                       |
| Sudoku solving and generating | [kudoku](https://github.com/attractivechaos/plb/tree/master/sudoku) | MIT                   | 190 lines of C, integers only, one `calloc` of its matrix       | No, but the nearest to a yes. It solves by exact cover; the game needs a solver that counts to two solutions and can stop and resume between frames, which is a different loop. A bitmask backtracker is under 100 lines |
| Sudoku generating             | [QQWing](https://qqwing.com)                                        | GPL-2.0-or-later      | C++                                                             | No. The payload is C with no C++ runtime                                                                                                                                                                                 |
| Klondike solvability          | [Klondike-Solver](https://github.com/ShootMe/Klondike-Solver)       | MIT                   | C++; searches up to 5,000,000 states by default                 | Not in the firmware: that search does not fit a Q2's memory. Solvability is checked offline by a small solver in the test tree, and the firmware carries a table of seeds it solved (512 bytes)                          |
| Sprite and shape blitting     | [olive.c](https://github.com/tsoding/olive.c)                       | MIT                   | one header                                                      | No. `eng_fill`, `eng_disc` and `eng_round` are short, clip to the rect being painted, and write the screen's own pixel format                                                                                            |
| Random numbers                | [PCG](https://www.pcg-random.org)                                   | Apache-2.0 or MIT     | a few lines                                                     | No. `rand_below` (xorshift, `patch/playback.c`) is already there and is enough for deals and levels                                                                                                                      |
| Whole games to port           | [Rockbox](https://www.rockbox.org)'s plugins                        | GPL-2.0-or-later      | each tied to Rockbox's plugin API and LCD model                 | No. Compatible, but a port brings the API with it, and these games are designed for this screen and wheel                                                                                                                |

The licenses of libfixmath, kudoku, cute_headers and Klondike-Solver were read from their repositories on 2026-11-15; the others are as their projects state them. Chipmunk2D's size is an estimate, not a build for MIPS.
