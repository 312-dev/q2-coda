# The game engine

A small engine inside the UI payload for games drawn at the panel's refresh: a fixed 60 Hz step, a frame's input as one record, drawing straight into the screen's buffer inside the rects a step said changed, the Clicker's sounds, and sealed saves. It is `patch/engine.c` and `patch/engine.h`; `patch/engine_demo.c` is a scene that proves it and shows a game's four calls. Games (`patch/games.c`) is its page: the chooser lists the engine's games after its own (those `GAME_*` in `patch/peq.h` ships: none in this build, so Bricks is the first row), and the pause menu is Games' pop-up.

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
    unsigned size, flags;                                 /* state bytes, zeroed; ENG_RAW, ENG_STILL, ENG_FULL */
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

`draw` gets a `struct eng_surface`: the screen's pixels, the sheet's origin, and the rows and columns of this paint. The sheet starts under the status row, `h` rows of it (262 to 290 by theme); a game flagged `ENG_FULL` gets the whole screen instead, 375 x 320 over the status row (`show` in `games.c` moves the sheet to the screen's top for it, and back for any other), so its `h` is 320 and its row y is the screen's. It paints its whole scene, background first; every call clips, so the cost is the changed part's. Coordinates are the sheet's, colors `0xRRGGBB` as they reach the screen (Light Mode does not flip them; Night Shift still warms them, as it reads every pixel written).

| Call           | Draws                                                                                          |
| -------------- | ---------------------------------------------------------------------------------------------- |
| `eng_fill`     | a box, solid                                                                                   |
| `eng_disc`     | a disc with a smoothed edge, its center and radius in 16.16: it moves by sixteenths of a pixel |
| `eng_round`    | a box with round corners, smoothed                                                             |
| `eng_text`     | ASCII in the theme's face through stock's canvas (left, centered or right); returns its width  |
| `eng_inset(y)` | not a drawing: the columns the glass's 80 px corners hide at each end of the sheet's row `y`   |

A buffer that is not stock's 375 x 320 BGRA8888 `lcd_mem` (a window animation's snapshot of another layout) is left as stock drew it.

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

| Game (see [Games](#games))           | Payload bytes     | Heap (`size`)           | A frame, worst (instructions) | Whole sheet |
| ------------------------------------ | ----------------- | ----------------------- | ----------------------------- | ----------- |
| Brick breaker (Bricks)               | 9 KB              | 2 KB                    | 600,000                       | 2,000,000   |
| Turret shooter                       | 9 KB              | 4 KB                    | 600,000                       | 2,000,000   |
| Music quiz                           | 8 KB              | 8 KB                    | 600,000                       | 2,000,000   |
| Klondike solitaire (Cabin Solitaire) | 28 KB (31.4 used) | 206 KB, its art read in | 600,000                       | 2,000,000   |
| Sudoku                               | 8 KB              | 2 KB                    | 600,000                       | 2,000,000   |
| **All five**                         | **62 KB**         | one at a time           |                               |             |

- **A frame's hard limit is 2,000,000 instructions** of the payload's own (about 4 ms by the estimate above) for the step and every rect's paint together; 600,000 is what a game is held to, so stock's own painting of the same rects, the flush's turn of them onto the framebuffer and the loop's polls keep the rest of the 16.67 ms.
- **Heap** is one game's at a time, freed on leaving it. The device has little to spare next to the player; 8 KB is the most a game's own state is given, and a game whose art is a file in the root filesystem reads it into its state once, at most 256 KB in all (Cabin Solitaire's is 210,652 bytes: its 37,856 bytes of art and the tables made from it).
- **Scratch** (131,072 bytes, about 31 KB free): the engine uses 4. A game uses none: its state is the heap's.
- **The root filesystem** (at most 75,497,472 bytes, `ROOTFS_ROOM`) is not used by the engine itself. A game's art that would not fit its payload bytes is a file there, `usr/share/q2games/<name>.bin` from `assets/games/` (`GAMES_DATA` in `tools/build.py`), read once when the game opens, never in a step or a paint: Cabin Solitaire's is 37,856 bytes.
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

### Cabin Solitaire

`patch/game_solitaire.c` (`eng_solitaire`, `ENG_STILL | ENG_FULL`), Games' second engine row: Klondike on a cabin's card table, the redesign in the emulator project's `design/games/solitaire/` (its `SPEC.md`, `STYLE.md` and element sheet). Seven columns, four foundations by suit from the ace (hearts, spades, diamonds, clubs, left to right), a stock and a waste; columns build down in alternate colours and only a king takes an empty one; draw one or three, unlimited passes (the waste turned back over in order). A card is `suit * 13 + rank` with `UP` when face up in a column; the state is the piles and the record together (`struct record`), so a save is the game.

**The cursor and the hand.** `sources` lists every place with somewhere to go, in the wheel's order: the waste, the foundations, then each column's face-up runs from the deepest to its top card; the wheel walks only those, so an illegal move cannot be chosen and there is no error state. Center picks the place up: the card (or run) lifts and hovers over the best destination (`dests`: home first, then the longest column it builds on, then an empty column; a king alone in its column has nowhere new), the others outlined in gold where it would land, the hovered one brighter. The wheel moves the hover, Center drops, Previous puts it back. With nothing to move and nothing in the stock or the waste that could, the slip says "No moves. Prev: undo".

**Controls.** Wheel: the next place, or the next destination with a card in hand. Center: pick up, drop (with no place to pick, it draws). Play/Pause: draw, or the waste back to the stock; once every card is face up and the stock and waste are empty, the auto-finish sends everything home one card every 4 frames (Center or Play again sends the rest at once). Previous: undo, from a 64-move ring (`apply` and `unapply` leave the state byte for byte as it was), or put the hand back. Next: the cursor's card (or the hand) home, if it can go. A finger: the stock draws, a card that can move is picked up, a legal place takes the hand and anywhere else puts it back.

**Screens.** The start board (Deal: Winnable or Random; Draw: One card or Three cards; Resume, with a game in progress; wins, best time and fewest moves under it) is where the game opens; the wheel moves the gold marker, Center changes the row or resumes, Play deals. The deal flies a card from the stock every 2 frames, each a 6-frame glide on a 10 px arc, the last of each column turning as it lands. A move glides 8 frames (ease-out cubic); a card turned narrows and widens over 6; a card home flashes gold for 6 frames with 8 sparks flying out; an undo glides back with "Undo: N moves" on the slip. The win lights the four kings in turn, 6 frames apart, then shows the board ("Well played": time, best, moves, wins) with gold motes beside it; a new best's tag grows to 1.15 and settles in 10. The design drops the board in 14 frames and lets the motes drift: a frame of that drop measured 1,803,626 instructions (the board's whole box each frame), so the board shows at once and the motes stand still. The pause is the engine's (Games' pop-up, the system bar's clock and battery shown over the table), not the design's own board.

**The table.** Felt from a 64 x 64 fibre tile and a 94 x 80 map of 4 px shade cells (wear, the darker edge, two stains), each pixel a lookup into a colour table per shade level: the design's felt with its shade stepped per cell rather than smoothed. Cards are a body (the 1 px warm edge and the face, their corners from two 5 x 5 tables) with a 2 px drop shadow and the contact shade over the top edge, both drawn only where the card does not cover them; their pieces (indexes, pips, the court, the back) are masks and small palette images. Every rounded box (shadows, gold edges, outlines, the rail, the boards) is one routine, `rbox`, from constant tables of layers (`layers`); a card's five looks (at rest, raised in a run, under the cursor, home, in the hand) are made once when the game opens (`looks`: each look's layers over black and over white give, per pixel, how much of what is under shows and what they add) and drawn as two runs a row (`deco`). The felt is left out under what is painted opaque over it (`covers`: the rail, a column at rest, a top-row card, a board), and a step's changed rects are joined where they overlap (`mark`). A column fans 7 px a face-down card and 17 a face-up one, closing to 5 then 3 and to 11 at the least only when it must (`fan`, SPEC.md's rule), so the longest pile there can be (six face down under a king to an ace, column 7) ends at y 296, above the rail; each card is drawn only down to where the next covers it. The design's queen and jack hung under the baseline, cut at an 11 px fan, so the index art has a queen whose tail and a jack whose hook sit above the next card's edge. The last column is at x 309 (the design's 310), so no shadow or gold edge reaches under the glass's corner.

**Art.** One file, `assets/games/solitaire.bin` (installed as `/usr/share/q2games/solitaire.bin`, 37,856 bytes, sealed as a save is), read whole once in `init` into the state: 181 entries of 4-bit masks and 16-colour palette images (each row from a byte's start), the felt's tile, map and colour table, and per text style a table of glyphs and advances. It is written by the design's `src/export.py`, which renders each piece with the mockups' own code (`cards.py`), and its layout is `patch/game_solitaire_art.h`. Type is Libre Baskerville (indexes, labels, figures) and Playfair Display (titles), as glyph masks of the sizes and letters drawn ([third-party](third-party.md#artwork)). Without the file (or with a torn one) the sheet is a flat felt saying the art is missing; the game's state and save are kept.

**Winnable deals.** A deal is a seed: an xorshift of it shuffles the deck (Fisher and Yates), the columns are dealt row by row. Winnable deals take a seed from `sol_seeds` (`patch/game_solitaire_seeds.h`, 256 of them, 512 bytes), each one the test tree's solver won in draw three and in draw one (`test/fw/solitaire_solver.c`, a depth-first search with a visited set bounded at 200,000 states, written out by `test/fw/solitaire_solver.py --write`); random deals take any seed. `test_game_solitaire.py` wins a sample of the table again and plays each win through the game's own moves.

**Time.** The engine has no clock for a game and the frame timer stops at rest, so the game reads the UI's millisecond clock (`time_now_ms`) at each step of play and adds the time since the last, ten minutes at most; a pause starts it over, so time away is not counted. The slip shows it as of the last step that painted: it does not tick while nothing moves.

**Sound** is the Clicker's three: a tick for the cursor, a hover and a draw (three for a recycle), a tap for a pick-up and a drop (two when a card turns), the alert once when nothing can move, ticks for the kings, two taps for a new best. The SPEC's table of 23 sounds of its own (a rising scale for cards home) needs the q2player change of [Sound](#sound).

**Save:** `/mnt/data/game-solitaire`, `CCS1`: the game in progress, the start board's choices and the record (wins, games played, best time, fewest moves), written on the step after every tenth move once nothing glides, at the game's end, and at a pause when changed. A save is read only if every card is in it once, face-down cards lie under face-up ones in each column and the lengths and record are sane. Games' older Solitaire keeps its own file, `/mnt/data/ringnav-solitaire`, unread while it is not built.

**Frames.** Measured on build `--dev` (`test/patch.py`'s section), the whole screen (`ENG_FULL`):

| What                                                                      | Instructions (MIPS, payload)    | Bound in `test/patch.py` |
| ------------------------------------------------------------------------- | ------------------------------- | ------------------------ |
| The whole sheet: the start board, a table, the win                        | 1,993,583, 1,963,043, 1,982,771 | 2,000,000                |
| A screen's change (the deal starting, the win)                            | 1,509,566                       | 2,000,000                |
| A second of play: the cursor, a pick-up, a hover, a drop, an undo, a draw | 714,995                         | 800,000                  |
| A king-to-ace run of 13, lifted and dropped on an empty column            | 1,115,263                       | 1,250,000                |
| The deal, its dearest frame                                               | 1,168,977                       | 1,300,000                |
| The auto-finish, its dearest frame                                        | 1,097,967                       | 1,250,000                |
| The win board showing                                                     | 1,199,318                       | 1,350,000                |

ponytail: every frame but the whole sheet's is over the 600,000 a game is held to (under the engine's hard 2,000,000): a frame repaints whole cards, and a card costs 20,000 to 30,000 instructions (its looks, body, index and pip) over felt at about 6 an instruction a pixel. What would bring them under: a card's strip in a fan drawn from a cached image of the strip rather than from its pieces, and the glides moving fewer pixels (a run as one image).

In the emulator, 10 s of play from the start board (a deal, then the wheel, Center, Play and Previous every 0.2 s): 190 page flips, a median gap of 17.4 ms while anything moved; the 31 gaps over 34 ms are the game at rest between inputs (`ENG_STILL` paints nothing then), with the host's load average near 5. The payload grows by 32,192 bytes over the integration branch's build, over the 28 KB a redesigned game may add (ponytail: the drawing that brought the frames down to the table above, the looks, the covers and the joined rects, added about 5 KB; cutting the flip, the sparks and the tag's pop would give back about 2 KB). The state is 210,652 bytes on the heap: the art file (37,856), each look's tables (117,000) and the felt's; no `.scratch`.

## Libraries

Looked at for vendoring. The project is GPL-3.0, so MIT, BSD, zlib, public domain and GPL-2.0-or-later code could all be taken; none is, because each need is smaller than the library that meets it.

| Need                          | Candidate                                                           | License               | Size                                                            | Verdict                                                                                                                                                                                                                                                             |
| ----------------------------- | ------------------------------------------------------------------- | --------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fixed-point maths             | [libfixmath](https://github.com/PetteriAimonen/libfixmath)          | MIT                   | Q16.16 in C; its caches are 32 KB and 80 KB unless compiled out | No. Two inline functions and Vortex's 257-entry sine and arctangent tables, already in the payload, are the whole need                                                                                                                                              |
| Physics and collision         | [Chipmunk2D](https://github.com/slembcke/Chipmunk2D)                | MIT                   | a rigid-body engine in floating point, over 100 KB compiled     | No. Far past the payload's room, and nothing here is a rigid body                                                                                                                                                                                                   |
| Collision tests only          | [cute_c2](https://github.com/RandyGaul/cute_headers)                | zlib or public domain | one header, floating point                                      | No. A disc against a disc and a disc against a box are a few integer lines each, exact and testable in fixed point                                                                                                                                                  |
| Sudoku solving and generating | [kudoku](https://github.com/attractivechaos/plb/tree/master/sudoku) | MIT                   | 190 lines of C, integers only, one `calloc` of its matrix       | No, but the nearest to a yes. It solves by exact cover; the game needs a solver that counts to two solutions and can stop and resume between frames, which is a different loop. A bitmask backtracker is under 100 lines                                            |
| Sudoku generating             | [QQWing](https://qqwing.com)                                        | GPL-2.0-or-later      | C++                                                             | No. The payload is C with no C++ runtime                                                                                                                                                                                                                            |
| Klondike solvability          | [Klondike-Solver](https://github.com/ShootMe/Klondike-Solver)       | MIT                   | C++; searches up to 5,000,000 states by default                 | Not in the firmware: that search does not fit a Q2's memory. Solvability is checked offline by a small solver in the test tree (`test/fw/solitaire_solver.c`), and the firmware carries a table of seeds it solved (512 bytes, [Cabin Solitaire](#cabin-solitaire)) |
| Sprite and shape blitting     | [olive.c](https://github.com/tsoding/olive.c)                       | MIT                   | one header                                                      | No. `eng_fill`, `eng_disc` and `eng_round` are short, clip to the rect being painted, and write the screen's own pixel format                                                                                                                                       |
| Random numbers                | [PCG](https://www.pcg-random.org)                                   | Apache-2.0 or MIT     | a few lines                                                     | No. `rand_below` (xorshift, `patch/playback.c`) is already there and is enough for deals and levels                                                                                                                                                                 |
| Whole games to port           | [Rockbox](https://www.rockbox.org)'s plugins                        | GPL-2.0-or-later      | each tied to Rockbox's plugin API and LCD model                 | No. Compatible, but a port brings the API with it, and these games are designed for this screen and wheel                                                                                                                                                           |

The licenses of libfixmath, kudoku, cute_headers and Klondike-Solver were read from their repositories on 2026-11-15; the others are as their projects state them. Chipmunk2D's size is an estimate, not a build for MIPS.
