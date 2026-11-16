# OLED design standard

The OLED theme gives Q2 Pod the look of a present-day iPod classic: a black screen, white
sentence-case type, one blue accent, and nothing on screen that is not music or a way to reach
it. This file is the standard the theme is built and reviewed against. It states what each
surface looks like on the Q2's 375x320 screen, in pixels, and why.

The look follows a fan concept, "New iPod OLED 2026 Concept" by netomeister95 on r/ipod
(https://www.reddit.com/r/ipod/comments/1sus0tx/). The concept was measured, not copied: no
image, icon or mark from it ships, and every value here was translated to the Q2's screen and
its rules. Where the concept contradicts itself, this file picks one answer and says so.

## Principles

1. **Music first.** Every screen answers "what is playing" or "what can I play". Chrome
   (status, titles, chevrons) is quieter than content.
2. **Black is the surface.** The background is `#000000`. Panels, cards, gradients and
   glows are not used to separate things; spacing and grey levels are.
3. **One accent.** Accent Blue marks the one thing that is selected, playing or on. If two
   blue things compete on a screen, one of them is wrong.
4. **Type does the work.** Hierarchy comes from size, weight and grey level, in that order.
   No capitals, no letter spacing, no outlines.
5. **The wheel decides the layout.** Lists move one row per tick and the selection is always
   a full row, so every list is built from the same row pitch.

## The screen

| Property      | Value                        | Notes                                                                                   |
| ------------- | ---------------------------- | --------------------------------------------------------------------------------------- |
| Panel         | 375x320, upright             | The concept's screens are near square; the Q2 is wider, so layouts gain width, not rows |
| Glass corners | 80 px radius (`GLASS_R`)     | Hides up to 46 px at each end of a row 8 px from an edge; see [Corners](#corners)       |
| Status row    | glyphs centred on y 23       | No band and no rule: the bar is 58 px of the page's black; ends 62 px in from each side |
| Content area  | 375x262 from y 58            | Seven 32 px rows, y 58 to 282                                                           |
| Panel type    | LCD (`lcd_h0209s`), not OLED | Black is a colour here, not "off": keep large areas black anyway, it is the look        |

The geometry below (the status row, the 58 px list top, the 16 px selection inset, the 28 px right column and
the scroll indicator) is what the user chose from mock-ups on 2026-11-13 (mock-up E and its six-screen
follow-up), in place of a first draft that stepped the selection bar in at the corners and drew the scroll
track along the glass. The status row's height and ends are the user's from `mock_status_up.py` on 2026-11-15
("do 62 and '...' any title thats too long"): up from y 37 into the glass's top curve, the list's top unchanged.

### Corners

The concept puts the clock in the top-left corner and the battery in the top-right. On the Q2
those spots are under the glass. So:

- The status row sits up in the glass's top curve, centred on y 23, where the glass hides 32 px at
  its glyphs' top edge (y 16), so its ends draw in to follow the curve: the title's text starts
  62 px in (`STATUS_ROW_INSET`) and the battery ends 62 px from the right edge, the clock beside
  its glyphs (or centred, on a page with no title). The list's own columns (24 and 28) stay
  below it.
- No text or control may start inside `corner_x(y, h)` for its row. In the middle of the
  screen, from y 80 to y 240, the inset is 0.
- Every scrolling list ends as Home (Full)'s does (the user's, 2026-11-15): its box runs on to the
  screen's foot and the rows past the list end, y 282 (the 32 px slots from y 58 are y 58, 90, ...,
  250, seven in all), peek through a fade into black from y 266 to y 316, black below; once the
  list has scrolled, its top row's first 14 px fade under the status row. A selected row is never
  faded and never lands past y 282: scrolled to its end, by the wheel or a drag, the last row's
  foot is on y 282 (Q2 Pod's `list_end` and `list_fit`, `navigation.c` `list_fade`), also after
  the page comes back on top (Return from a row's page). A page with nothing to select (text) ends the
  same way: the wheel scrolls it until its content's foot is on y 282, as a drag does. A list whose top is off the 32 px grid (EQ's, under its
  48 px tab bar) or whose rows are of another pitch keeps whole rows at its top, so scrolled to its end its last row
  ends on the last whole row above y 282. The build refuses a list box that stops at or above y 282
  (`tools/ipod.py` `cut_lists`).
- Every screen is checked against the glass: a screenshot composited with the 80 px mask shows
  no lit pixel under the curve.

## Colour

All colours are opaque. Alpha is not used for text: greys are stated as greys.

The tokens live once, in `tools/theme.py` `TOKENS`, written to `patch/tokens.h` for the payload (`tok()` in
`patch/ui_kit.c` picks the half; `RGB_<NAME>` and `RGB_<NAME>_L` are each half) and read by `tools/art.py` for
its glyphs' inks. A test fails on a colour literal equal to a token anywhere else in `patch/`.

| Token          | Hex       | Use                                                                          | Concept measurement                                        |
| -------------- | --------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Canvas         | `#000000` | Every background                                                             | `#000000` on all grid screens                              |
| Text Primary   | `#FFFFFF` | Row titles, page titles, song title, times                                   | `#FCFCFC` to `#FFFFFF`                                     |
| Text Secondary | `#ABABAB` | Artist, album, values in key/value rows ("Off", "Flat")                      | `#AAAAAA` to `#B2B2B2`                                     |
| Text Tertiary  | `#858585` | Second lines ("1,234 Songs", "Connected"), section headers, captions         | `#7D` to `#8C` by ratio (est)                              |
| Chevron        | `#999999` | List chevrons on black                                                       | `#8C8C8C` read on a 1 px stroke, so lighter in truth (est) |
| Track          | `#333333` | Unfilled progress and slider tracks; switch off (the storage bar's is its own) | `#313131`                                                  |
| Accent Blue    | `#2160D8` | Selection bar, progress fill, switch on, slider knob ring, big status glyphs | Bar `#1544B2` to `#225BCB`, progress `#2368D9`             |
| On Accent      | `#FFFFFF` | Text and chevrons on the selection bar                                       |                                                            |
| Positive       | `#34C759` | "Connected" dots, charging, OK to Disconnect, a song added                    | Green dot on Bluetooth                                     |
| Warning        | `#FF9F0A` | A charger too weak, Charge to Update                                         |                                                            |
| Negative       | `#FF453A` | The low battery (under 20%), a failed step                                  |                                                            |
| Raised         | `#1C1C1E` | Toasts, cards, the modal's panel, keys                                       |                                                            |
| Raised 2       | `#2C2C2E` | Pills and placeholders on a raised surface, a lifted row                     |                                                            |
| Off            | `#3A3A3C` | A grey glyph's circle, a control that is off                                 |                                                            |
| Caption on accent | `#D6E2FA` | A selected row's caption, on the bar                                      |                                                            |

The concept grades its blue differently in each image. Accent Blue is one value between the bar
and the progress fill, chosen so white text on it reads at about 5:1. Use it everywhere; do not
add a lighter or darker blue.

### Light Mode

The concept has no light screens; Q2 Pod's Light Mode exists and must keep working. The light
half flips greys, not hues:

| Token          | Dark      | Light     |
| -------------- | --------- | --------- |
| Canvas         | `#000000` | `#FFFFFF` |
| Text Primary   | `#FFFFFF` | `#000000` |
| Text Secondary | `#ABABAB` | `#5C5C5C` |
| Text Tertiary  | `#858585` | `#6E6E6E` |
| Chevron        | `#999999` | `#8A8A8A` |
| Track          | `#333333` | `#D9D9D9` |
| Accent Blue    | `#2160D8` | `#2160D8` |
| Raised, Raised 2, Off | as dark | flipped as every grey: `#DCDCDA`, `#CECECC`, `#C2C2C0` |
| Positive, Warning, Negative, Caption on accent | as dark | the same |

## Type

Face: **Inter** (SIL OFL 1.1), Regular and Medium, subset as the build subsets its other faces,
with tabular digits so a running time never changes width. Inter stands in for the concept's
San Francisco, which cannot ship. See `assets/fonts/` for the files and licence.

| Role                                  | Face          | Size  | Colour         | Example        |
| ------------------------------------- | ------------- | ----- | -------------- | -------------- |
| Status row (page title, the clock)    | Inter Medium  | 15 px | Text Secondary | Playback, 9:41 |
| Row                                   | Inter Regular | 20 px | Text Primary   | Playlists      |
| Row value                             | Inter Regular | 20 px | Text Secondary | Off            |
| Row second line                       | Inter Regular | 15 px | Text Tertiary  | 1,234 Songs    |
| Section header                        | Inter Regular | 15 px | Text Tertiary  | Devices        |
| Song title                            | Inter Medium  | 22 px | Text Primary   | Time           |
| Artist, album                         | Inter Regular | 18 px | Text Secondary | Pink Floyd     |
| Times                                 | Inter Regular | 16 px | Text Primary   | 3:47 and -3:22 |
| Confirmation label                    | Inter Medium  | 22 px | Text Primary   | Shuffled Songs |

Rules:

- **Sentence case** for everything the theme writes (status, captions, values). Menu items and
  page names stay as the firmware names them, which is Title Case ("Sound Check"). Never all
  capitals.
- **A number, a space, its unit:** "4000 K"; a percentage takes no space ("50%").
- **Single-letter time units attach:** 3h, 30s, 1h20m. "min" and spelled-out words keep the
  space: 15 min, 30 seconds.
- **The card is the SD card:** "SD Card" in row names and titles (SD Card Update, SD Card Test),
  "SD card" in a sentence ("Copy music to the SD card"). Never TF or microSD, stock's words.
- **Size steps are 15, 16, 18, 20, 22** (30 for the [plug-in screen](#plug-in-screen)'s level, 56 for the confirmation's glyph, 17 for the [tabs](#tabs)' labels and a dialog's or pill's emphasis, 13 for hints, captions, footers and the small labels of the [A-Z index](#a-z-index) and [Control Center](#control-center)). Nothing else: an 11 is 13, a 14 is a 15 (a note under rows a 13, as a footer), a 19 is a 20, a 30 elsewhere a 22.
- **Row pitch is about 1.6 times the row size** (32 px for 20 px type), as the concept's is
  about twice its cap height.
- **Sizes are the em.** AWTK sizes a face by its line (ascent less descent), which for Inter is
  1.21 em, so `theme.xml` states each size times 1.21, rounded: 15 is 18, 16 is 19, 18 is 22,
  20 is 24, 22 is 27. The payload writes `TYPE(px)` (`patch/ui_kit.h`), never the AWTK number.
- **Truncate, then scroll.** A long title ends in an ellipsis; the selected row's title scrolls.
  Never shrink type to fit.

## Spacing

All lists share one grid.

| Token              | Value                                        | Notes                                                                              |
| ------------------ | -------------------------------------------- | ---------------------------------------------------------------------------------- |
| List top           | y 58                                         | 28 px under the status row's glyphs                                                |
| Row pitch          | 32 px                                        | seven rows, y 58 to 282 (see [Corners](#corners))                                  |
| Two-line row pitch | 52 px                                        | four rows, y 58 to 266                                                             |
| Text inset (left)  | 24 px                                        | the rows and the art share this column; the status row's title starts at 62        |
| Right column       | 28 px from the right edge                    | chevrons and values on every row: they never move between rows                     |
| Leading art        | 40 px at x 24, 6 px down its row, text at 72 | 4 px corners, in two-line rows                                                     |
| Bar inset          | 16 px each side, every row                   | no corner stepping: at the bottom row the corner hides 14.5 px                     |
| Bar radius         | 8 px                                         |                                                                                    |

## Components

### Status row

- No band: the bar is the page's black, 58 px, with no rule under it. Its glyphs centre on y 23.
- Left, from x 62: the page's title in Inter Medium 15 px, Text Secondary, ending with an
  ellipsis 12 px short of the clock's widest ("00:00"), however long: a title never moves to a
  row of its own, so the list keeps its place.
- The clock, in the same face, size and colour, right-aligned 6 px left of the leftmost glyph
  (the battery's body when it is alone). Home, Now Playing and Cover Flow's covers (but an artist's, titled by the
  artist) have no title, and there the clock is centred across the row (x 187.5) instead, unless centred it would
  reach the glyphs; Home in
  Split keeps it at the right, over the art, inked white or black as one glyph with the other glyphs
  and battery (approved from `mock_clock_center.py` and `mock_clock_right.py` on 2026-11-15).
- Right, right to left: the battery, its nub's end 62 px from the right edge (x 313), then the glyph
  slots, each shown only while it has something to say, nothing in the row ever wrapping (`status_row.c`;
  [internals](internals.md#status-row-slots)):
  - **Play state**, 4 px left of the battery's body: three level bars as Up Next's, 2 px wide and 4 px apart,
    standing 6 px below the row's centre, 3 to 11 px tall and moving on offset sines (a 1.25 s loop) in Text
    Primary while playing; paused, frozen at their last heights in Text Secondary; stopped, nothing. Only their
    10 px box repaints as they move. A tap on them opens Now Playing (`mock_status_bars.py`, the user's, 2026-11-15).
  - **Wi-Fi**, 5 px left of the bars: Phosphor `wifi-high` (bold) lit by the signal, one, two or three arcs in Text
    Primary and the rest in `#3A3A3C` so the shape keeps its width; `wifi-x` (bold) connected with no internet;
    nothing with Wi-Fi off or not connected (`mock_wifi_status.py`).
  - **Sync**, 4 px left of the glyph before it: while a card app checks its server or its metadata, Phosphor
    `arrows-clockwise` (bold) turning in Text Secondary (S1); while it downloads songs, a 14 px ring (1.6 px) in
    `#333333` filling clockwise from 12 o'clock in the accent, Phosphor `arrow-down` (bold, 8 px, Text Primary) in
    it (S2, `mock_sync_status.py`).
  - **Bluetooth**, 5 px left (P9 in `design/bt-redesign`): nothing while Bluetooth is off or nothing is
    connected; Phosphor `bluetooth` (bold) in Text Tertiary while a device connects and in Text Primary once one
    is connected; a headset that reports its level, `headphones` (bold) and a 15x8 battery 2 px after it, filled
    to the level as the status battery's (red under 20%); a car, `car-profile` (bold).
  - **Buttons Lock**, 5 px left: the padlock (Phosphor `lock-simple`, fill, 13 px, Text Primary).
- The new glyphs are 14 px boxes at the padlock's optical size, 11 px of ink ([Icons](#icons)), centred on the
  row; every gap is at least 4 columns of black, so Split inks each glyph by itself.
- Battery, in iOS's proportions: a 25x12 body with 4 px corners and a 1 px `#6E6E6E` outline,
  the charge a solid fill 2 px inside it with 2.2 px corners to the level, charging or not, in
  Text Primary (under 20 % `#FF453A`, the only red on any screen; `#34C759` green on a cable that
  charges it, holds it full or holds it at Charge Limit; `#FF9F0A` orange on a charger too weak to
  keep up), and a 1.5x4.4 nub with 0.8 px corners in `#6E6E6E`.
- Charging, Phosphor's `lightning` (fill, 7 px, Text Primary) sits on the body's centre with a
  1 px gap in the Canvas cut round it where it crosses the fill, and no outline; a weak charger's
  `warning` (fill) takes its place the same way. Full or held at Charge Limit, the fill is green
  with no glyph. Picked as I2 from `mock_batt_inside.py` on 2026-11-15.
- With Battery on Percent, the level and "%" in BATT_PX type stand in the battery's place, in its
  colours, on one line: nothing in the status row ever wraps. The slot is as wide as "100%" measures,
  each digit and the sign rounded up to a whole pixel, and the row lays out from the right edge, so
  the glyphs to its left move over for it.
- A tap on the battery (a 44 x 40 px target round its body, or round the percentage on Percent, so
  at least 40 px round it; where it meets the level bars' target the nearer centre has it) toasts
  its state; see [Dialogs and toasts](#dialogs-and-toasts).

### Selection bar

- Accent Blue, flat, the full row pitch tall, inset 16 px each side on every row, 8 px corners,
  (`list.selection.inset`, `list.selection.radius`).
- Text and chevron on it are On Accent, the second line too. Nothing else on the row changes (no
  bold, no indent).
- It moves one row per wheel tick, with no easing longer than the theme's list motion.
- **Tug to tilt** (approved on 2026-11-15 from `design/mockups/tilt-menu.html`; `patch/tilt.c`): while a finger
  turns the wheel short of its next tick, the bar leans toward the row it is heading for by the share of the
  tick turned, eased 1 - (1 - a)^2: up to 3 px that way, up to 3.5% larger, an accent shadow under it, and a
  white gleam (0.42 at its centre, from 0.15 to full strength over the lean) sliding the other way. The tick
  is unchanged: past it the bar steps a row and the next tug starts from the leftover turn. Let go short of
  it and the bar springs back over 220 ms. The mock-up's 1 degree tilt is under a pixel on a 32 px row and is
  not drawn. A touch drag scrolls the list with the bar hidden, as touch always does, so it does not tilt.
- **A list's end is its end** (approved on 2026-11-16): the wheel stops on the first and last rows, never wrapping
  to the other end and never bouncing. Turning on past an end, the bar's tug holds full against it, the
  selection staying, and springs back when the finger lifts. A drag stops at the ends too, with no rubber band.
  The lean is never cut at the list's edge: on the first or last row it reaches up into the status row's
  black band and down into the fade.
- A list left by Return opens at its top when it is chosen again; Return lands on the row the
  page behind was left at (`list.reopen.top`).
- Exactly one per screen. A dialog over a list takes the selection; the list's bar is hidden
  while the dialog is open.

### List row

- 32 px pitch, title at the text inset, a chevron on the right column when the row opens
  another page, nothing at the right when it acts in place (a song).
- **Chevron:** Phosphor `caret-right`, bold, 15 px, centred 5 px left of the right column:
  Chevron grey, On Accent on the selection.
- **Opening:** a row whose page has to load first (Home's Cover Flow, on its first open after a
  restart or a library change) keeps the selection and turns its chevron into an activity
  spinner: eight spokes in the chevron's 15 px box, On Accent, the leading spoke solid and each
  one behind it 10% fainter, a spoke on every 100 ms (redrawn between the open's steps, so it can
  pause on a long one), until the page opens. It shows only once the
  open has taken 75 ms, as one that has will pass 150 ms, so a quick open never flickers;
  meanwhile the wheel and buttons do nothing but Return, which stays on Home.
- **Key/value row:** value in Text Secondary, right-aligned on the right column, no chevron.
  On the selection bar the value is On Accent.
- **Switch row:** every on/off setting, a 34x20 fully round switch ending on the right column, its
  16 px white knob 2 px in: Track when off, Accent Blue when on. On the selection bar: on is a white
  track with an Accent Blue knob, off a 1.5 px white outline round the white knob. No "On"/"Off"
  text beside it. A setting with two or three named choices (Gain's High and Low, Charge Limit's
  Off and 80%) is a key/value row that Centre cycles; the value is never part of the label.
- **Captioned row** (Smart Wi-Fi's and the other rows `pod_caption` names; approved as S3 on 2026-11-15, `mock_radio.py` `caption_rows('reveal')`):
  the caption shows only under the selected row. That row grows over 150 ms, its title and switch where
  they were, to hold the caption under them inside the bar: Inter 13 px in `#D6E2FA`, 17 px lines from the
  title's column, wrapped at words to the right column, 5 px under the last; every other captioned row is one
  row high. The list scrolls by whole rows to keep the grown row's foot in sight (`patch/settings_pages.c`).
  Every caption but Smart Wi-Fi's is one line there, under 327 px of Inter 13 (the bar's 343 less its 8 px insets).
- **Disabled row** (Set Time Manually while the time is set automatically): label, value and
  chevron in Text Tertiary, and the wheel passes over it.
- **Picker:** the current choice carries Phosphor's `check` (bold, 12 px) on the right column, Accent
  Blue, white on the selection.

### Scroll indicator

Wear OS's position indicator, on the Q2's straight right edge:

- A 4 px track with round ends at x 366 (5 px from the glass, 7 px clear of the selection bar),
  a fixed 72 px long, centred on the list's middle (y 134 to 206). Track `#3A3A3A`, thumb
  `#E5E5E5` inside it, the thumb's position and length the visible rows over the total.
- Hidden at rest: it shows while the wheel turns and fades out a second after the last tick
  (the last 200 ms of it). It stays hidden while the [A-Z index](#a-z-index) shows.

### A-Z index

An iOS section index that is not there at rest (the user's option B from `mock_az_index.py`,
2026-11-15), on Library lists in name order with more than 50 rows: Songs under Title (and the
other song lists its page shows in name order: Favorites, Hi-Res, a playlist sorted by name),
Artists, Albums, Composers and Genres. Lists in any other order (Recently Added, Songs by artist
or album, an album's tracks, a playlist by time added, an artist's or a genre's songs) and
settings have none.

- **Shown by** a finger on the list's right edge (its last 28 px), or the wheel spun fast (300
  degrees a second or more, the speed at which Rockbox's list acceleration first moves two rows a
  click, as the volume's does). It fades out a second after the finger lifts or the last fast
  tick (the last 200 ms of it).
- **The column:** centred 13 px from the right edge (x 362), from y 80 to y 240, the straight
  part of the glass. Letters `#`, A to Z in Inter Medium 13 px, white (black in Light Mode), never
  the accent. 27 letters do not fit 11 px slots in 160 px, so the column folds as iOS's does:
  seven letters spaced evenly, a dot between each two (`# • D • I • M • Q • V • Z`).
- **The lit letter:** a white 16 px disc with the letter in black, at that letter's place on the
  column. The finger maps evenly over all 27 letters whether or not the column is folded.
- **The bubble:** while a finger drags and while the wheel spins fast, a `#2C2C2E` box 52x48,
  12 px corners, ending 18 px left of the column with a 7 px pointer towards it, the letter in
  Inter Medium 22 px white, beside the lit letter.
- **Dragging** jumps the list so the first row under the letter is at the top and selected; a
  letter with no rows goes to the next one that has some. `#` is digits and symbols. A row files
  under its name's first letter past "The", "A" or "An", as the sort puts it.
- **While it shows**, the rows' right column moves in to x 343 (W - 32) and the selection bar
  ends at x 351, so lengths and chevrons stay clear of it; the fast-scroll letter and the scroll
  indicator stay hidden.

### Edit Library

The Library's rows, and Home's, in the user's own order and with the user's choice of rows, from
the approved mock-ups `library-reorder.html` (2026-11-15) and `mock_edit_home.py` (2026-11-16).
There is no Done button: the wheel's keys and a finger do it all. Edit Home is the same editor on
Home's list.

- **Entered by** Center held about half a second (550 ms) on the Library or on Home's list (not
  Carousel, not a list a Home row opens), or a finger held as long on a row, which then drags that
  row. Center held again, or Return when no row is lifted, saves and leaves.
- **In it** the status row reads "Edit Library" or "Edit Home", every row the user can show is
  listed, each row's chevron gives way to a grip (three 18x2 px bars 5 px apart, centered where the
  chevron was) in Text Tertiary, On Accent on the selection, and a hint, "Drag ≡ to move ·
  Play/Pause shows or hides", Inter Regular 13 px in Text Tertiary, is centered 14 px above the
  screen's foot (its middle at y 298), the grip drawn as its glyph. Home in Split is laid out as
  Full while the editor is open.
- **Check circles:** a 16 px circle centered on x 34 left of each row, its words moved to x 52. A
  row that shows has the accent's disc with a white 11 px bold check; a hidden row an empty 1.5 px
  ring in Text Tertiary, its words dimmed to Text Tertiary. On the selection the disc is white with
  the accent's check, the ring white, the words white. Home's Settings can't be hidden: its circle
  is a `#3A3A3C` disc with a 10 px padlock in Text Secondary, so Home always has a way back.
- **Showing and hiding:** Play/Pause, or a tap on a row's circle (its first 52 px), shows a hidden
  row or hides a shown one, saved at once; the rows hidden leave the list when the editor is left.
  Each row is Auto until then: it follows its own rule (Shuffle, Hi-Res, Long Songs and Upload
  Scrobbles hidden; Home's Folders shown only while the SD card has folders to list, Now Playing
  only while something is loaded), and in the editor an Auto row shows how it stands now. The first
  toggle makes it the user's, Shown or Hidden, and that sticks (Folders shown on an empty card,
  hidden with folders).
- **The wheel:** Center lifts the selected row: it grows 8% about its middle (the bar's box, so
  x 2 to 372 and 34 px tall), its words in Inter Medium 22 px, white, with a white grip and its
  circle as the selection's, on a raised `#2C2C2E` card with 9 px corners, a 1 px `#141414` ring and
  a soft black shadow 6 px down. The wheel moves it a row a click, stopping at either end; Center
  puts it down and saves. Return while it is lifted puts it back exactly where it was, still in the
  editor.
- **A finger** on a row's grip (the last 64 px) drags it at once; held on a row it drags it after
  the hold. The row follows the finger on its card; the rows it passes slide aside over 150 ms
  (cubic out); within 16 px of the list's top or bottom the list scrolls on. The lift puts it in
  its place and saves. A tap selects a row and opens nothing.
- **The order** is kept through a restart and a power cut. A row the editor doesn't list (the
  Library's My Fav and Frequent, a media row without its folder) keeps its place; a row a later
  update adds appears at its default place without disturbing the rest.

### Tabs

An iOS segmented control (`slider_page.c` `segments_paint`), on EQ's Simple and Advanced (`eq_tabs_paint`) and
Search's All, Songs, Artists and Albums ([ipod](ipod.md#search)), the theme's only tabs:

- A `#1C1C1E` track rounded 9 px: on EQ from x 20 to 355, 34 px tall, centred in the 48 px tab bar; on Search
  from x 16 to 359, 30 px tall, in the results' first 32 px row.
- The open tab a segment 2 px inside the track, rounded 8 px: raised `#3A3A3C` while the wheel is
  elsewhere, Accent Blue only while the wheel is on the tabs, so the open tab reads apart from the
  wheel's place. In Light Mode the track is `#E5E5EA` and the raised segment white.
- **The wheel** reaches the tabs past the first row (or control) under them. On them it moves between the tabs, each
  opening as the wheel reaches it, left to right going down; a turn down past the last tab, or Centre, goes into the
  open tab's rows, and a turn up past the first stays (EQ) or goes up to what is above (Search's strip). Previous
  and Next open the tab before and after, round (`peq_ui.c` `eq_key`, `search_strip.c` `search_strip_key`).
- Labels in Inter Medium 17 px, centred in their segments: the open one white (Text Primary on Light
  Mode's white segment), the other `#ABABAB` (`#8A8A8E` in Light Mode), at the size of iOS's
  own control.

### Two-line row

- 52 px pitch, four to a page. Title (Row, Text Primary) and second line (Row second line, Text
  Tertiary, On Accent on the selection): the title's baseline 25 px down the row, the second
  line's top 32 px down.
- Optional leading 40 px square art with 4 px corners (Albums) at x 24, 6 px down; text then
  starts at 72.
- No dividers (the user's, 2026-11-15): rows stand on space alone, in every list. A row with no
  second line is a one-line row at Home's pitch, even in a list whose other rows have one (a card
  app's, `addon.c` `stack_rows`). Q2 Pod's own two-line lists (Era, View Queue, Most Played,
  Ratings' lists) end as every list does ([Corners](#corners)).

### Song rows

Songs play in place, so a song row has no chevron. It is one line, 32 px, seven to a page.

- **A mixed list** (Songs, a playlist): the title in Row, then two spaces and the artist inline
  in 15 px Text Tertiary (On Accent on the selection), both ending in an ellipsis so the pair
  fits from the text column to the right column; the artist is dropped when less than about
  40 px would remain for it.
- **A list reached through an artist** (their songs, one of their albums): no artist.
- **Inside an album:** the track number in 15 px Text Tertiary right-aligned in a 17 px column
  after the 24 px inset, so two digits fit it, the title from x 50, the length right-aligned on the right column in
  15 px Text Tertiary, tabular digits.
- Two of OLED's own options change this: **Song Artist** (Inline, Selected Row, Right, Off) and
  **Song Length** (Albums, All Lists, Off); a length takes the right column, so Right, and
  Selected Row on the selected row, fall back to Inline beside it.
- The artist's baseline is the title's; on the right column it takes at most half the row.
- **The Shuffle row** stands first on Songs and a playlist's songs, in place of
  Play All (a song already plays the rest of its list in order): Phosphor `shuffle` (regular, 20
  px) on the text column and "Shuffle" 28 px after it, both Accent Blue, white on the selection.
  The wheel reaches it from the first song; Centre shuffles the list, and Return from Now Playing
  lands on it again. Sort has no place on the row: Songs' order is Settings > Playback > Library > Sorting's
  Songs ([ipod: Settings](ipod.md#settings)). An album's page has Play and Shuffle above its tracks instead
  ([Album page](#album-page)).
- **Folder view** reads as an album: the same 32 px rows. A song file is a track, its number in the
  album's column, the title from the library's tags (found by the file's path) at x 50 and its
  length on the right column when known; a file the library doesn't know shows its file name
  without the extension, a leading track number ("01. ") or the "Artist - " every such name in the
  folder starts with. A folder has Phosphor `folder` (regular, 20 px, Text Secondary, white on the
  selection) centred on x 34, its name at x 52 and the chevron on the right column; the card's root
  too.
- Which list is which comes from its page: Songs' page (`allmusic_page`, which also holds a genre's songs
  and the other lists stock opens there) is mixed, the artist page's songs (OLED draws that page as the [artist page](#artist-page)) are an artist's, the album page is an
  album, Folder view (`folder_page`) a folder, and an album's tracks opened from Cover Flow are an album too (their
  status row names the album, with no row of its own above the tracks). Lists Q2 Pod draws itself (Most Played,
  Long Songs, Ratings) keep their own rows.
- Streaming's rows are one line, 32 px, each service's mark (20 px, monochrome, no disc) centred on x 34 in Text
  Secondary, white on the selection, its name from x 52: Phosphor's `spotify-logo`, `app-window` (a card app without
  its own icon), `radio`, `airplay` and `sliders-horizontal` (Configuration), TIDAL's own four diamonds, drawn in
  `tools/art.py` (Phosphor has none), TIDAL and Tidal Downloads sharing it, and for the Subsonic card app (id
  `subsonic`, with or without its own icon files) q2-coda's submarine with two sonar arcs. Any other card app with its
  own icon shows that icon scaled into the same 20 px box, greyed as every card app icon is under this theme
  (`icons.addon_mono`) rather than inked: a tint would turn an icon drawn on a filled tile into a solid square.
- **Where a song lives** (with Library: Local + Server): a song row's right column says where the song is, as the
  user approved from `mock_radio.py`'s storage sheet on 2026-11-15: nothing for the user's own file, Phosphor
  `arrow-circle-down` for one a card app downloaded (its `q2app`'s `downloads=` folder, q2-subsonic's `Subsonic/`),
  `cloud` for a server song that streams; regular, 18 px, Text Tertiary, white on the selection, centred where the
  offline mark goes. A server song that can't play now keeps the greyed row and `cloud-slash`
  ([server-library.md](server-library.md#greyed-rows)). The length and the title end 12 px short of a mark, as they
  do of the offline mark.
- Ratings' lists show a song's hearts as five Phosphor glyphs at 14 px at the start of its second line, `heart-fill`
  for its hearts and `heart` for the rest, in Text Secondary (white on the selection), then "· Artist".

### Now Playing

The most-seen screen. Art left, words right, time across the bottom; the status row shows the
clock, centred.

| Element        | Position (x, y) and size       | Notes                                                                                      |
| -------------- | ------------------------------ | ------------------------------------------------------------------------------------------ |
| Art            | 24, 58, 150x150                | Square, 6 px corners                                                                       |
| Song title     | 192, top 64                    | Inter Medium 22 px, Text Primary, one line, scrolls when long                              |
| Artist         | 192, top 96                    | Inter Regular 18 px, Text Secondary                                                        |
| Album          | 192, top 120                   | Inter Regular 18 px, Text Secondary, up to two lines                                       |
| Progress bar   | 24 to 347, y 228, 6 px         | Track `#333333`, fill Accent Blue, round ends                                              |
| Elapsed        | left on 24, centred on y 250   | 16 px, Text Primary, tabular                                                               |
| Remaining      | right on 347, centred on y 250 | 16 px, Text Primary, with a leading minus; a tap shows the length instead (Track Time)     |
| State row      | centred on y 286, x 53 to 322  | Shuffle, radio, repeat, Up Next: Phosphor bold 22 px, even gaps (below)                    |

No page title, no background art blur, no reflection.

Built as the `oled` set's `np_art` and `np_bar_y` (`tools/ipod.py` `np_grid`) with the theme's
`playing.art.radius`, `playing.artist.font`, `playing.scrubber.height` and `playing.modes.icons`.
The album is one line that scrolls, as the title and artist do, rather than two. What the design
has no place for keeps a place of its own:

- **More and the heart** stand at the right end of the art's bottom line (baseline 208), on the
  art's page, so a swipe takes them away with it: Phosphor `dots-three` and `heart` (regular, 20 px, Text Primary;
  More pressed in Text Secondary), the dots' ink ending on the right column, the heart `fill` in
  Accent Blue while the song is a favourite.
- **The page dots** are hidden: the state row's on dots sit where they were. The lyrics are the
  swipe's other page, ending 5 px above the bar. The visualizer (Field), the design's third page, is off for the
  battery: `NP_VISUALIZER` in `patch/offsets.inc` is 0, which leaves its code out of the payload;
  at 1 it is the last page again, over the whole screen. Now Playing shows no place in the
  queue ("1 of 10").
- **The track's details** are not a page of the swipe: ⋯'s **About** row, last before Delete Song,
  opens them as a page titled About, an [info grid](#track-details), and Return goes back to ⋯.
- **The state row** is four buttons on stock's play mode button, `shuffle`, `broadcast` (radio),
  `repeat` and `queue`, the outer two's edges fixed at x 53 and 322 and the three gaps between them
  even. Shuffle and repeat are Text Secondary off; on, Accent Blue with a 4 px Accent Blue dot 2 px
  under the glyph (repeat-once's glyph for One). A tap on shuffle turns it on or off, a tap on
  repeat steps Off, All, One, and each keeps the other as it was. Stock's Shuffle never ends, so
  shuffle with repeat Off plays on as with All. Radio is the antenna in Text Secondary; while the
  playing app plays a radio it is a 92 x 34 Accent Blue pill holding the antenna (18 px, white) and
  "Radio" (Inter Medium 16 px, white): turning on, the pill grows from the glyph's width over
  200 ms (ease out) while the gaps shrink, the word fading in over the last 40 %, and back the same
  way off. While radio is on, shuffle and repeat are `#444446` and ignore taps and the Centre
  cycle, as the app picks the order. A tap on radio opens the playing app's page, where a radio
  starts and stops; with no app playing it does nothing. The queue button, and a swipe up on the
  art's page, open [Up Next](#up-next); the hold menu's View Queue opens it too.
- **The volume** and the scrub draw on the bar where it is. The volume (the "A: iPod speakers"
  mock-up) keeps the bar's rows, inset to x 48 to 323, white on the track, with Phosphor's
  `speaker-simple-none` and `speaker-simple-high` (fill, 16 px, Text Secondary) centred at x 32
  and 339 on its centre line, and no number; the times' row stays blank meanwhile. The scrub is
  a 14 px white knob on the fill's end. The A-B markers follow the bar
  across but keep stock's row, y 308 on screen, under the page dots. ponytail: a marker within
  about 14 px of either end of the bar falls under the bottom corners' glass; moving them onto
  the bar needs the y that stock's placement (`0x53baa4`) writes made per set.

### Up Next

The queue from the song playing on (`patch/upnext.c`), titled Up Next. Rows are 46 px: the cover
36 px square at the text column (4 px corners), the title (Inter Regular 17 px, Text Primary) over
the artist (13 px, Text Tertiary), 46 px right of the text column.

- **The playing song** heads it, a row that takes no selection, with three Accent Blue level bars
  (3 px wide, 4.5 px apart, up to 12 px high) 7 px after its title, moving about 8 times a second
  while it plays and still while paused.
- **Playing Next** (Inter Medium 13 px, Text Secondary) heads the songs queued with Play Next, Play
  Last and the bump, with a **Clear** pill on its right, 22 px high, ending at x 359 with the
  selection bar: `#2C2C2E` with `#7FA6F5` Inter Medium 15 px at rest, Accent Blue with white when
  the wheel is on it. It is a stop of its own; Centre on it empties Playing Next, no confirm.
- **From** the list, album or page the queue was played from ("Next in Queue" when that is not
  known) heads the rest of the queue in play order.
- **The bump arrow**, Phosphor `arrow-up` (regular, 16 px, Text Tertiary; white on the selection),
  stands on the right column of each queued song but the first. A tap on it, or Play/Pause held on
  the row, moves the song to the top of Playing Next, where a 3 px Accent Blue edge marks it for
  0.7 s. The rest of the queue has no arrow and Play/Pause does nothing on it.
- **Moving a queued song**: Centre held on it lifts it onto a `#2C2C2E` card with 10 px corners,
  its title in Inter Medium 18 px; Playing Next's songs show a grip (three 18 x 2 px bars) on the
  right column in place of their arrows; the wheel carries it, Centre puts it down, Return puts it
  back where it was.
- Nothing toasts. Centre on a song plays it and closes Up Next. The list follows the queue as songs
  end.

### Progress and sliders

- **Progress:** 6 px, round ends, Track and Accent Blue. Used for playback, scans and
  downloads.
- **Slider page** (Volume, Max Volume, Default Volume, Channel Balance, Brightness, Crossfade, Resume Songs,
  Night Shift, Wheel Sensitivity): the status row names it; a feature with a switch has it as the
  first row, selected; the value in Inter Medium 22 centred on y 150; a 6 px track from the text
  column to the right column with its top at y 186, Track behind and Accent Blue filled to a 14 px
  white knob (Channel Balance fills from the centre; in Light Mode, where white is the canvas, the
  knob is edged 1 px in Track, as iOS edges its white thumb); the range's ends in 15 px Text
  Tertiary centred on y 214, at the two columns. The wheel moves it; there are no +/- buttons. Centre
  turns the switch on or off where there is one, else goes back; Return goes back.
  Night Shift's first row is a key/value row, its value Off, On or Auto, which Centre cycles (three
  named choices: [List row](#list-row)); the value is the warmth ("3400 K") in every mode, and while
  Off it, the track and the knob are Text Tertiary with no accent, as a disabled row's (the user's,
  from audit2 J7).
- **Vertical slider (EQ):** 4 px Track line, Accent Blue fill from 0 dB to the value (Text Tertiary
  while the EQ is off), a 14 px white knob, ringed 2 px in Accent Blue while the wheel is on it
  (1 px in Track otherwise in Light Mode, as the slider page's). The band's name and gain under it
  in 15 px Text Tertiary, Text Primary on the band the wheel is on. Above the three, the EQ's
  switch (Enabled), Presets and Reset are rows; while an Advanced preset plays, its name is
  Presets' value, as a settings row's, and the switch keeps its title.
- **Storage bar** (Settings > Storage, approved from `mock_radio.py`'s storage sheet on 2026-11-15): "33.9 GB of 60
  GB used" in 15 px Text Secondary centred on y 62 at the text column, then a 10 px bar with round ends from the
  text column to the right column, its top at y 76: the track `#2C2C2E`, then from the left Your Music in white,
  Downloads in Accent Blue and Stream Cache in `#6E6E73`, square where they meet with 1 px of track between them.
  The rows from y 100 are its legend: a 10 px dot in the segment's colour on the text column (Free's in the track's),
  the label at x 44 and the size as the row's value, Downloads and Stream Cache with a chevron. Two footers follow.
- **Footer:** a grouped list's caption under its last row, as iOS's: 13 px Text Tertiary (`#858585`) on the text
  column, its first line centred 10 px below the rows, the next 16 px lower. Storage and Downloads use them.

### Confirmation

Shown for a moment after a mode change (Shuffle, Repeat).

- Centred glyph (Phosphor `shuffle` or `repeat`, bold), 56 px, Accent Blue, centred on y 140;
  label Inter Medium 22 px centred on y 200; count in Text Tertiary 15 px centred on y 228.
  Nothing else on screen but the status row, which names Now Playing.
- Dismisses itself after 1.2 s or on any input, which it takes.
- Built for shuffles (`patch/feedback.c` `feedback_shuffled`): the Library's Shuffle Songs, Albums and
  Folders, a list's Shuffle row and the hold menu's Shuffle, over the Now Playing they open, with the
  queue's song count. Repeat and Now Playing's own mode button do not show it yet.

### Dialogs and toasts

- A question is the [confirmation card](#confirmation-card) over the page that asked. Stock's confirm
  (`confirminfo_dialog`: Shut Down, Restart, the three Resets, Delete Song, Delete File, Unpair, the Wi-Fi
  network's Forget and Disconnect, Line Out and the rest) is drawn as the card with no change to its callers
  (`feedback.c` `feedback_confirm_dress`): its prompt named as the design writes it ("Forget This Network?" for
  stock's "Delete Record?", "Use Line Out?" for its "Line Out sets max volume,continue?"), **Cancel** first and
  selected when it opens, then the verb ("Shut Down", "Reset", "Delete"), never "OK"; a verb that loses something
  (Delete, Reset, Forget, Unpair, Remove, Clear) red after the gap. Delete Song's "Also delete source file" is the
  card's switch row. What the answer leaves alone, or why it asks (a caller's `feedback_confirm_foot`: Streaming
  Songs' "Show streaming songs?" and "Hide streaming songs?"), is the body: Reset Settings Only asks "Reset
  Settings?" over "Music, playlists and ratings are kept."
- A toast is one line of 16 px Text Primary on a `#1C1C1E` pill, 12 px corners, 32 px tall,
  as wide as its text and 16 px each side, centred on y 252 (y 236 to 268), above the bottom
  glass curve. It is the only raised surface in the theme. The page stays in sight round it,
  with its status row and its selection; it lasts 1.5 s (stock's own toasts their own time).
  Words in sentence case ("Playing Last", "Name can't be empty").
- A toast with an icon keeps the 16 px each side and puts a 15 px glyph (Phosphor, fill) after the
  left one, then 8 px before the words: 16 + 15 + 8 + words + 16. Every icon toast is this one
  (`feedback.c` `toast_icon`: USB's, charging's), approved from `mock_batt_toasts.py` on
  2026-11-15.
- A tap on the status row's battery toasts its state, the glyph in its colour: "Charging · 62%"
  (lightning, green), "Charger too weak · 41%" (warning, orange), "Charge Limit Reached (80%)" and
  "Fully charged" (battery-full, green), "Low battery · 12%" under 20 % (battery-warning, red) and
  otherwise "Battery 62%" (battery-high, Text Primary). No time-left estimate.
- Every in-place action says what it did in a toast: the hold menu's Play Next ("Playing Next"),
  Play Last ("Playing Last") and Add to and Remove from Favourites ("Added to Favorites",
  "Removed from Favorites").

### Confirmation card

A question answered in place: a card over the page that asked, the page dimmed behind it (approved 2026-11-16 from
`design/confirm-overlay/`; `patch/feedback.c` `confirm`). It replaces the full-screen dialog page for every confirm
as its callers move onto it.

- **Surface:** the whole screen, status row too, under black at 50%; the card `#1C1C1E` (white in Light Mode), 291 px
  wide at x 42 with 16 px corners, its stack centred on y 168 and kept inside y 44 to 302.
- **Content**, 20 px in from each side: 18 px of padding; the title in Inter Medium 20, Text Primary, centred, naming
  the object ("Forget WH-1000XM5?", never "Are you sure"), an ellipsis ending a long one; at most two body lines in
  15 px Text Secondary on 19 px lines, 4 px under it, one consequence in sentence case; a code (a pairing's "482
  913") in Inter Medium 30; then 14 px down an optional switch row (label at the text column, the switch at the right
  column) and the answers, 32 px rows of Inter Regular 20 centred, stacked; 8 px of padding.
- **Kinds:** destructive (Cancel, then 12 px of space, then the verb in Negative red; opens on Cancel), consequential
  (Cancel, the verb; opens on Cancel), neutral (the safe answer, the verb; opens on the verb), info (OK). The lit
  answer's bar is the accent, 8 px in from the card's sides, or Negative red on a destructive verb.
- **Red:** Negative `#FF453A`, the design's one red, not the mock's `#FF3B30` (iOS's light-mode red, darker on black).
- **Input:** the wheel moves the bar, stepping over the gap; Centre answers the lit row or flips the switch;
  Return answers the safe row; Play/Pause, Next and Previous do nothing while the card asks.
- **Timeout:** a card that answers itself (Guest Play's 30 s Decline) shows the seconds left on that row in 15 px
  Text Tertiary and a 2 px bar along its foot emptying toward it, and answers it at 0 whatever the input.
- **For code:** `confirm(&(confirm_t){ .title, .name, .body, .code, .rows, .n_rows, .kind, .back, .toggle,
  .toggle_on, .timeout_ms, .timeout_row, .answer, .ctx })` (`patch/navigation.h`); `answer(ctx, row, toggle_on)` runs
  once after the card has closed. One card at a time: `confirm` answers 0 while one is up.

### Empty and offline

**The empty state** (`patch/feedback.c` `empty_state`) is the one form for every page with nothing to show
(`mock_placeholders.py`, approved 2026-11-16): three stacked tiles, a 58 px one in front (`#2C2C2E`, its edge
`#48484C`) and two 48 px ones 30 px either side behind it at about a quarter's strength, 9 px corners, with the
page's 28 px Phosphor glyph (fill, white at 92 %; `tools/art.py` `EMPTY_GLYPHS`) on the front one; 18 px under them
the title in Title Case, Inter Medium 20, Text Primary; under it the hint in 15 px Text Secondary; and at most one
action row, 24 px lower, already selected. The whole stack, with or without its row, is centred between the status
row's foot (or the end of any rows the page keeps above it) and the screen's. No app names. No fake rows, and no
Shuffle row or Add to Sync over nothing. A page that is a list (`empty_list`) keeps its name for the status row; a
Library list stock would open black (Albums, Artists, Composers, Genres) and a streaming service with no link open
a page of their own holding only the empty state (`empty_open`). With no SD card in, the Library's pages say so
(`empty_card`).

| Screen | Glyph | Title | Hint | Row |
| --- | --- | --- | --- | --- |
| Songs, an empty library | `music-notes` | No Songs | Copy music to the SD card | **Scan Music Now** (stock's button, `scan_row`) |
| Artists | `microphone-stage` | No Artists | Copy music to the SD card | |
| Albums | `vinyl-record` | No Albums | Copy music to the SD card | |
| Genres | `guitar` | No Genres | Songs need a genre tag | |
| Composers | `music-notes-simple` | No Composers | Songs need a composer tag | |
| Folders | `folder` | No Folders | Copy music to the SD card | |
| Cover Flow, no card music | `cards` | No Albums | Copy music to the SD card | |
| Cover Flow, a streaming app signed in | `cards` | No Albums | Streaming albums can show here | **Turn on Streaming** (while Streaming Songs is off) |
| Cover Flow, the card's music not scanned | `cards` | No Albums Yet | Music on the card isn't scanned | **Scan Music Now** |
| Playlists | `playlist` | No Playlists | Make one with New Playlist | none: under On-The-Go's row; New Playlist is in a song's hold menu (Add to Playlist…), not on this page. The picker keeps its rows alone |
| Favorites | `heart` | No Favorites | Hold Play/Pause on a song to add it | |
| Recently Played | `clock-counter-clockwise` | Nothing Played Yet | Songs you play show up here | |
| Most Played (both lists) | `chart-bar` | No Plays Yet | Your most played songs show up here | |
| Recently Added | `sparkle` | No Songs Added Yet | New music on the card shows up here | |
| Long Songs | `timer` | No Long Songs | None over 10 minutes | |
| Years (Era) | `calendar-blank` | No Dated Albums Yet | Albums need a year tag | |
| Ratings' song lists | `star` | No Rated Songs Yet | Hold the heart on Now Playing to rate | |
| Search, typed text matching nothing | `magnifying-glass` | No Matches | Try fewer letters | none (painted in the free area above the strip) |
| Internet Radio, a station list with none | `radio` | No Stations | Add stations from your computer | |
| Streaming, a service with no link | `cloud-slash` | Not Connected | Turn on Wi-Fi to stream | **Turn on Wi-Fi** (opens Settings > Wi-Fi) |
| Library, no SD card | `sd-card` (Phosphor's `memory`) | No SD Card | Insert an SD card with music | |
| Car Remote, no phone paired | `car-profile` | No Paired Phones | Open the Car Remote app on your phone | none: under the switch; a phone pairs from its own app |
| Downloads (Tidal Downloads, a card app's songs) | `download-simple` | No Downloads Yet | Synced music shows up here | |
| Chapters | `list-numbers` | No Chapters | This file has no chapter marks (or: This file's chapters couldn't be read) | |

Not in this form, by design: Now Playing is reachable only while something is loaded, so it has no empty screen;
the Wi-Fi page always holds its switch, Search Again and Add Network rows; the EQ's Presets page always holds its
four action rows (no presets is a toast); and Upload Scrobbles with nothing to send is a toast after the press.

The older note (`feedback_empty`): one line of 16 px Text Secondary centred in the free area below a page's rows,
down to y 282, with an optional hint in 15 px Text Tertiary 24 px lower. Pages whose list keeps rows the note
explains use it: the App Store's "No apps listed" under its Check for Updates row, On-The-Go's how-to under its
rows, a card app's page with nothing in it (an empty Subsonic artist: its "No albums" info row's words, its subtitle
the hint, with no Add to Sync over nothing and no selectable info row, audit2 F1), the confirm note under Reset
Settings, and a payload list's other messages (Internet Radio's "Loading…", Battery History's "Unplug to start").

- Busy: no stock spinner (Playlists' while it adds a song); the toast that follows says it is done. A library list whose open sorts thousands of records (the first open after a library change) shows the payload's own spinner while it sorts, in every theme: a 64 px `#1C1C1E` square with 12 px corners at the screen's centre, eight dots turning clockwise (internals, [Name order](internals.md#name-order)).

### Menus

Actions on the highlighted item (the hold menus, Now Playing's More and a playlist's More) open as
an inset modal over the page, which stays on screen dimmed half way to black; nothing is blurred.

- **Panel:** opaque `#1C1C1E`, 259 px wide from x 58, top at y 58, 16 px corners, 8 px padding, no
  title (the row behind says which item). Rows are 32 px, at most six in view, then they scroll
  inside the panel; the wheel's scroll indicator stands in the panel's right padding, never past its edge.
- **Rows:** Inter Regular 20 at the panel's 16 px inset; an action's Phosphor symbol (regular,
  19 px) at the right in Text Secondary, white on the selection. The selection is the list's bar, 8
  px in from the panel with 8 px ends.
- **A setting** (Speed, Shuffle, Repeat, Group by) has its symbol before the label, the
  label 26 px further in, and its value alone at the right in Text Secondary; Centre opens a picker
  in the same panel, the current choice with the accent's check before its label.
- **Rating** (Now Playing's Centre cycle, a menu's Rate) is a picker in the same panel: Unrated, 1 Heart
  to 5 Hearts, the current one checked; Centre keeps a row's rating and closes, Return closes.
- **Gaps:** a destructive row (Delete Song) comes last, 12 px below the actions, and asks before it
  acts. Space, never a rule. A panel whose rows are more than six has no gaps, so it scrolls, and
  ends, on whole rows (the user's, from audit2 E1: a gap cut the end of Now Playing's More); its
  settings tell themselves apart by the symbol before the label. A list's hold menu has no Sorting
  row: sort orders are Settings > Library > Sorting's.
- The menu opens on its first action, lit; the wheel moves, Centre picks, Return closes it back to
  the row it came from.

Built by `patch/menu_modal.c` under a set with `modal` (OLED's). Now Playing's More and a
playlist's More are stock pages, which such a set loads as dialogs (`tools/ipod.py` `modal_page`).

### Artist page

A card app's artist (the Subsonic app's, opened from Now Playing's artist tap, Go to Artist or its
Artists list), approved from `mock_album_artist.py` (option 2, header with monogram) and
`mock_artist_explore.py` (layout B) on 2026-11-15. Built by `patch/artist_page.c` (the header,
the pair, the rows) and `patch/addon.c` `artist_view` (which reply rows go where).

- **Status row:** no title, the clock centred.
- **Header** from y 52: a 64 px `#2C2C2E` disc at the text column with the name's first letter in
  Inter Medium 22, white; beside it from x 102 the name in Inter Medium 20 (ellipsis), the counts
  ("4 albums · 3 singles · 62 songs" as the app sends them, else "4 albums" counted from its album
  rows) in Inter Regular 15 Text Secondary, and the genre in Inter Regular 13 Text Tertiary, a line
  left out when unknown. The disc's top stands over the status row's bottom 6 px.
- **Action pair** at y 128: Shuffle and Radio side by side, each a `#1C1C1E` panel 28 px high with
  8 px corners, its Phosphor symbol (16 px) and label (Inter Medium 17) centred together in the
  accent. The lit one fills with the accent, its content white. The page opens on Shuffle; the
  wheel steps Shuffle, Radio, then the rows, and back.
- **Drill-down rows** from y 168, 32 px, scrolling, the rows past y 282 fading as every list's: Top Songs,
  Albums, Singles & EPs, Similar Artists, All Songs; Albums, Singles & EPs and All Songs with their
  count right-aligned 44 px from the edge in Inter Regular 15 Text Tertiary (white on the
  selection), each with the chevron. A row the app does not send, or sends nothing for, is left
  out: the app adds Singles & EPs, Similar Artists and All Songs only with the `artist` caps word
  ([librespot.md](librespot.md#artist-rows)), and only when the server gives release types, similar artists or songs;
  it leaves Top Songs out when the server has none.
- **Top Songs** and **All Songs** (titled so): 36 px rows from y 58, a 28 px cover with 4 px
  corners at the text column (the song's album's server library cover, else the placeholder), the
  title in Inter Regular 18 at x 62, then its album in Inter Regular 15 Text Tertiary where the
  title leaves 30 px, and its length right-aligned on the right column in Inter Regular 15 Text
  Tertiary (white on the selection); Centre plays the list from that song. An app without
  `artist` sends neither album nor length: the title alone.
- **Albums** (titled so): 40 px rows from y 58, newest first, the undated last: a 32 px cover with
  4 px corners at the text column (the server library's cover when the app has fetched it, else a
  `#2C2C2E` placeholder), the title in Inter Regular 20 at x 66, the year right-aligned on the
  right column in Inter Regular 15 Text Tertiary (white on the selection). Centre opens the album.
  **Singles & EPs** is the same page of the artist's singles and EPs.
- **Similar Artists** (titled so): 40 px rows from y 58, a 32 px `#2C2C2E` disc (`#3A3A3C` on the
  selection) at the text column with the name's first letter in Inter Medium 15, white, the name
  in Inter Regular 20 at x 66, and the chevron. Centre opens that artist's page.
- **Hold menu:** Play/Pause held on the page opens the inset modal of its sync rows, Add to Sync
  (or Remove from Sync), Quality and Sync Now, each with its symbol. Play is gone: Shuffle plays
  the artist.
- **A Library artist** (card songs and, with Local + Server, server-library songs; from Library >
  Artists, Go to Artist, Search or Now Playing's artist tap: stock's artist page) is the same page,
  built by `patch/library_artist.c` from the Library's songs ([internals](internals.md#library-artist-page)):
  the counts read "8 albums · 96 songs" and the genre is the artist's songs' commonest. **Shuffle**
  stands alone across the pair's width: there is no Radio, as nothing on the card or in the server
  library gives similar songs. The rows are **Top Songs** (the artist's songs Most Played has
  counted, most played first; left out until one is), **Albums** and **All Songs** with their
  counts; Singles & EPs and Similar Artists are left out (no release types or similar artists in
  the library). Albums opens the Albums page above whatever Artist View is, and an album there its
  [album page](#album-page) (user, 2026-11-15), its covers Cover Flow's cached ones (the
  server library's for a server album), placeholders until Cover Flow has made them (and for an album with no art). Top Songs
  and All Songs are the songs page above with their album, length and cover.

### Track details

Now Playing's ⋯ > About, the user's pick of three Apple-style treatments (`mock_about.py`, option
C "Info grid", iOS Files' Info, with Apple Music's quality badge on its own line, `c_badge_spec`)
on 2026-11-15. Built by `patch/np_about.c`, which paints it from the details stock fills
(`tools/ipod.py` `np_info_values` keeps them, hidden, as its source).

- **Status row** titled About.
- **Header** from y 46: the cover, 56 px with 6 px corners, at the text column (a `#2C2C2E`
  placeholder without one); beside it from x 92 the title in Inter Medium 20, white, the artist
  in Inter Regular 15 Text Secondary and the length in Inter Regular 13 Text Tertiary, centred on
  y 56, 77 and 95, each ending in an ellipsis when long. The cover's top stands over the status
  row's bottom 12 px.
- **Quality line** at y 112: a badge, 20 px high with 6 px corners and a 1 px Text Secondary
  outline, Phosphor `waveform-bold` (12 px, Text Primary) centred 11 px in and its label in Inter
  Medium 13 from 21 px in, 30 px wider than the label; then, 10 px on, the spec in Inter Regular 13
  Text Tertiary. **Lossless** for FLAC, ALAC, WAV, AIFF, APE and WavPack up to 16-bit and up to
  48 kHz, **Hi-Res Lossless** above either, spec "FLAC · 16-bit / 44.1 kHz"; **DSD** for a DSF or
  DFF file, spec its rate as a multiple of 44.1 kHz ("DSD64", from the bit rate); a lossy file has
  no badge, its spec alone at the text column, "MP3 · 320 kbps".
- **Grid** from y 138, 44 px a row, two cells a row at x 24 and 192: the name in Inter Regular 13
  Text Tertiary centred 8 px down, the value in Inter Regular 17 white 26 px down, ellipsized at
  150 px. Album, Year, Genre, Bit Rate ("1,001 kbps"), Size ("38.3 MB", one decimal), then
  Location on a row of its own across to the right column: "SD Card · Music/Disclosure" (the
  song's folder) for a card file, "Server · Downloaded" for a server song the card keeps (synced
  into `Subsonic/`, or in the app's Stream Cache), "Server · Streaming" for one the app fetched
  only for this play (straight in its `stream/` folder). A cell whose value is unknown is left out
  and the rest close up: no "Unknown".
- **Scrolling:** inert, no selection, as an iOS info page. When the grid ends past the list's end
  (y 282) the wheel scrolls it a row a tick, as far as its last row, and back, and a finger dragged up
  or down moves it with the finger between the same two ends, stopping where it lifts (no momentum,
  so a flick never overshoots); the window clips it under the status row, where the header's top shows only at the page's top.

### Album page

An album opened from Now Playing's art or album tap (Look up Album), Go to Album, Library > Albums,
Search's albums or an artist page's Albums: the Library's (stock's album pages, dressed by
`patch/library_album.c`) and a card app's (the Subsonic app's, `patch/addon.c` `album_view`), approved
from `mock_album_artist.py` (option 2, header with cover) on 2026-11-15. It shares the artist page's
header and pair (`patch/artist_page.c`).

- **Status row:** no title, the clock centred.
- **Header** from y 52: the album's cover, 64 px square with 6 px corners, at the text column (the
  server library's cover for a server album, else Cover Flow's cached one, else a `#2C2C2E`
  placeholder); its top stands over the status row's bottom 6 px, as the artist's disc does. Beside it
  from x 102 the album in Inter Medium 20 (ellipsis), its artist in Inter Regular 15 Text Secondary,
  and "2015 · 18 songs · 1h12m" in Inter Regular 13 Text Tertiary, a part left out when unknown.
- **Action pair** at y 128: **Play** (Phosphor `play`) and **Shuffle**, as the artist page's pair. The
  page opens on Play, as the artist page opens on Shuffle; the wheel steps Play, Shuffle, then the
  tracks, and back. Play plays the album from its first track, Shuffle shuffles it.
- **Tracks** from y 168, 32 px, scrolling, the rows past y 282 fading as every list's: the track number
  right-aligned on x 38 in Inter Regular 15 Text Tertiary, the title in Inter Regular 20 from x 50
  (ellipsis), the length right-aligned on the right column in Inter Regular 15 Text Tertiary (white
  on the selection); Centre plays the album from that track. The Library's tracks are the album's
  song rows ([Song rows](#song-rows), its track numbers the tags'); a card app's are numbered in the
  order the app sends them.
- **Hold menu:** on a card app's album, Play/Pause held opens its sync rows, Add to Sync (or Remove
  from Sync), Quality and Sync Now, as on the artist page; on the Library's, the track's own hold
  menu. A card app without the `album` caps word ([librespot.md](librespot.md#album-rows)) keeps its
  plain rows.
### Playlists

Approved from the clickable mockups `design/playlist-proposal` and `design/add-songs` (2026-11-15).
Built by `patch/playlists.c` (the list, the menus' actions), `patch/playlist_pages.c` (a playlist's
page, On-The-Go) and `patch/add_songs.c`.

- **The list** (Library > Playlists): **On-The-Go** first, then the playlists by name, each a two-line
  row 44 px tall: a 36 px square at the text column less 2 (x 22), 4 px corners, the name in Row type
  over "N songs" (or "Empty") in 13 px Text Tertiary from x 66, and the chevron. The square is the
  playlist's own picture (`_explaylist_data/<name>.jpg`, as Cover Flow's) when it has one, a server
  playlist's cover from its server (the app's `covers/pl-<pid>.jpg`) when it has that, else a
  `#2C2C2E` tile with Phosphor's `playlist` (fill, 20 px, Text Tertiary); On-The-Go's tile holds
  `sneaker-move` (fill). On the selection the tile is the accent with an 18 % white veil and its
  glyph white. No dividers. No New Playlist, Select Playlists or Import and Export rows: a playlist
  is made from a song's hold menu, and Import and Export are Settings > Playback > Library > Playlists
  rows. An empty list shows the [empty state](#empty-and-offline) under its rows, "No Playlists", "Make one with New
  Playlist".
- **A playlist's hold menu** ([Menus](#menus)): Play, Shuffle, Rename, then Delete after a gap,
  which asks "Delete playlist “name”?" with Cancel first. Rename is the [letter strip](#text-entry)
  with its field off, titled Rename Playlist, the name prefilled over it with the cursor at its end:
  Next at the name's end saves it, and a held Return leaves the name as it was. No More menu, no Edit Playlist: reordering is the server's or the phone's.
- **A playlist's page**: Shuffle (Songs' Shuffle row: the accent's glyph, 18 px, at x 26 and the
  label at x 54), Add Songs (`plus-circle`) and its songs, each one line, the artist on the right
  column in 15 px Text Tertiary; no Shuffle row over nothing. Centre plays the list from the song;
  a hold offers **Remove from Playlist** alone. The songs are in the playlist's own order.
- **On-The-Go** (Settings > Playback > Library > On-The-Go, on by default; off hides its row and Add to On-The-Go):
  Shuffle, Add Songs, Save as Playlist (`arrow-down`), Clear (`x`), then its songs. Save as Playlist
  makes "On-The-Go N", the first number free, with its songs, empties On-The-Go and lands on the new
  playlist in the list; Clear asks first, Cancel first. Empty, it is Add Songs alone with the note
  "Or hold Play/Pause on any song", hint "and choose Add to On-The-Go.". A hold on a song offers
  **Remove from On-The-Go**. It lasts across a restart.
- **A song's hold menu**: Play Next, Play Last (to the end of Up Next's Playing Next), Add to
  On-The-Go (while On-The-Go is on), Add to Playlist…, Favorite (Remove from Favorites when it is one), then Go to Album, Go to Artist and Go to
  Folder. Add to Playlist… is a picker in the same panel: New Playlist (`plus`) first, which makes
  "New Playlist N" with the song in it (renamed later from its own hold), then the playlists; a
  pick adds at once and toasts "Added to name" ("Already in name").

### Add Songs

A playlist's or On-The-Go's Add Songs row opens a page titled "Add to name": Recently Played,
Recently Added, Artists, Albums and Songs, each with the chevron.

- **An artist** opens with **Add All** (the accent's `plus-circle`, the artist's song count on the
  right column), then its albums newest first as 40 px rows (a 32 px cover, the year, the chevron),
  then its songs, each with its artist on the right. **An album** is Add All, then its tracks: the number in 15 px
  Text Tertiary right-aligned in an 18 px column, the title from x 50, the length on the right.
  **Albums** are 40 px cover rows with the album's artist on the right. **Songs** has a **Search**
  row on top (`magnifying-glass`), which opens the [Search](ipod.md#search) letter strip over the
  library's songs alone, found by title or artist. Artists, Albums and Songs have the
  [A-Z index](#a-z-index) past 50 rows.
- **Marks:** each song shows where it stands against the playlist as it was when Add Songs
  opened, on the right column, 20 px Phosphor fills: `check-circle` in Text Secondary already in,
  `plus-circle` Positive green `#34C759` added now, `minus-circle` red `#FF6B5E` taken out now, white on the
  selection; putting a removed song back returns it to the check. The right column keeps the
  mark's 22 px only on a row that has one. Centre adds or takes out in place, the wheel stays where
  it is, and a toast says which ("Added to name", "Removed", "Undone"); Add All adds every song not
  already in ("Added N songs", "Already added").
- Return goes back a level; from the chooser, back to the playlist, which reads its songs again.

### Text entry

One letter strip for every text the wheel types (approved as 05b and 05c from the audit2 mock-ups on
2026-11-16; `patch/search_strip.c`): a `#1C1C1E` pill from x 40 to 335 and y 254 to 288 with 17 px corners,
its letters in equal 19 px cells about a fixed accent key, the run's ends fading into the pill's own grey.
It has two forms, in the same place, width and corners:

- **Field on** (Search, Add Songs' Search): the typed text in a black box at the pill's left with its
  cursor, the letters from x 148; the results above the strip, and with nothing typed the
  [empty note](#empty-and-offline) in their place, "Search your music" over "Artists, albums and songs";
  with text that matches nothing, the [empty state](#empty-and-offline), "No Matches", "Try fewer letters".
- **Field off** (New Playlist, Rename Playlist): the letters across the whole pill, the lit key centred,
  and the name written large in the middle of the screen, Inter Medium 22 white centred on y 140 with a
  steady cursor, over its hint in 15 px Text Tertiary on y 180, "Press Next to create" or "Press Next to
  save". Next confirms only with the cursor at the name's end; elsewhere Previous and Next move the
  cursor, as on Search's text.

- **A text box** (the Wi-Fi password, an EQ preset's name, a Bluetooth device's name, a search box: every
  box stock's keyboard served; the user's P1 from `mock_strip_password.py`, 2026-11-17): field off, a set
  chip at the pill's left (a `#2C2C2E` pill to x 96, its name in Inter Medium 16 white: abc, ABC, 123 or
  #+=), the run after it, its lit key at x 192; Play/Pause steps to the next set, each ending in the space
  and delete. The status row names the page the box is on, and the text shows as a name's does, "Press
  Next when done" under it; a box holding a number opens on 123. A password reads "Password" (15 px Text
  Tertiary, y 84) over dots (y 128), the character just typed shown for a second, all of it in plain text
  while Play/Pause is held, and "Hold Play/Pause to show · Next at the end to join" (13 px, y 176). Next
  at the text's end is the box's own OK, as the keyboard's Enter was: the box takes the text and acts. Settings > Bluetooth's Name (`bt_name.c`) is this form on a page of its own (`search_strip_text`), the status row reading Bluetooth: Next at the end saves the name.

On every form, the wheel walks the letters and Centre types the lit one. A Return press erases the character
before the cursor; held, Return goes back (the user's, from `mock_hold_back.py` on 2026-11-17): 200 ms into
the hold the page dims under black at 55% and a `#1C1C1E` card with 22 px corners, centred on y 160 and sized
to its line with 24 px sides and 20 px above and below, shows a 44 px ring (a 4 px `#3A3A3C` track the accent
fills clockwise from the top) round Phosphor's `arrow-u-up-left` (bold, 20 px, white) and, 14 px under it,
"Keep holding to go back" in Inter Medium 16 white; at 800 ms the page closes. Let go before that and nothing
happens: a hold never erases. The dim covers the status row too, as Buttons Lock's card's does: both
draw it the same way (`feedback.c` `overlay_scrim`). No box keeps stock's [keyboard](ipod.md#keyboard): one first seen gives its window to the strip
(`patch/keyboard.c` `kb_strip_due`).

### Icons

- One set: [Phosphor Icons](https://phosphoricons.com) (MIT), `@phosphor-icons/core` 2.1.1. The
  SVGs the theme uses are vendored in `assets/icons/phosphor/` with their licence, and
  `tools/art.py` draws the theme's bitmaps from them at build time, in final colours per half.
  A mark Phosphor lacks is drawn for q2-coda in Phosphor's regular style (16 unit strokes, round
  caps and joins, on the 256 unit grid), outlined to filled paths, and kept apart in
  `assets/icons/q2coda/` under the repository's licence: the Subsonic card app's `subsonic.svg`.
- Weights by role: `fill` for the padlock and a favourite's heart, `bold` for chevrons, the status row's Wi-Fi, sync and Bluetooth, Now
  Playing's and the confirmation's shuffle and repeat and About's quality badge, `regular` for row symbols and Now Playing's
  heart and More. Sizes are the design's: 13 px padlock and 14 px status glyphs, 15 px chevron, 20 px row symbols,
  heart and More, 22 px state row, 56 px confirmation, 12 px for About's quality badge
  (`waveform-bold`).
- **Missing art** is one placeholder everywhere stock shows its own (Now Playing, the album grids,
  Home's art, Cover Flow): a `#2C2C2E` tile, its corners rounded and clear (24 px of its 290, so no
  dark corner shows on a selected row), with Phosphor's `music-notes-simple` (regular) in Text
  Tertiary, a third of its width, centred (`assets/icons/default_album_home.png`, drawn from the
  vendored SVG; `tools/build.py` sizes it into stock's `ART_PLACEHOLDERS`), in place of stock's
  `#787878` disc (the user's, from audit2 I1).
- **Optical sizes** (the user's, 2026-11-16, `mock_wifi_trash.py`): a size is the glyph's box, and the glyph inside it
  is scaled about its ink's centre until its ink's longer side is a set length, centred in the box, so a wide glyph
  (Wi-Fi's signal) and a tall one (the lock, the trash) read as one size. The 20 px symbols (rows', Streaming's marks,
  Now Playing's heart and More) have 15.5 px of ink (`tools/art.py` `ROW_INK`, `glyph_png`). Wi-Fi's row glyphs end
  on the right column, the connected row's trash (Forget) last, selected or not (`wifi_rows.c`).
- No coloured app tiles: the concept's tinted "For You" squares are for content the Q2 does
  not have.

### Control Center

The pull-down, drawn as iOS's Control Center (approved as V3a on 2026-11-15, `design/mockups/mock_cc3.py`
`cc3(True)`; `patch/control_center.c`), under the status row, whose clock is centred:

- **Blocks** from y 38, `#1C1C1E` with 22 px corners: connectivity, 138 x 124 from x 20; Now Playing, 8 px
  right of it to x 355.
- **Toggles**: Wi-Fi, Bluetooth, AirPlay and Car Remote, 40 px circles two by two in the connectivity block
  (centres x 55 and 123, y 66 and 126), Accent Blue when on, `#3A3A3C` off, a 22 px white glyph, the name 9 px
  under in Inter Medium 13, white when on, Text Secondary off.
- **Now Playing**: the cover, 52 px with 9 px corners, 12 px in; the title (Inter Medium 16) and artist (Inter
  13, Text Secondary) beside it; Previous, Play/Pause (30 px) and Next on y 132, 52 px apart.
- **Sliders**: Brightness and Volume side by side from y 170, 38 px tall, fully round, `#3A3A3C` with a
  `#E5E5EA` fill to the level and an 18 px `#1C1C1E` sun or speaker at their start.
- **Tiles**: Lock, Sleep, Night Shift, EQ and Smart Wi-Fi, 44 px squares with 12 px corners on y 238, 12 px
  in from the blocks' ends and evenly apart, named under them as the toggles are.
- **Grab bar**: 36 x 5, `#5A5A5E`, at y 304, where the swipe up that closes the page starts.
- **The wheel** rings the stop it is on in white (2 px, 1 px out); a slider that has the wheel is ringed in
  Accent Blue. The stops, in reading order: the four toggles, the Now Playing block (only while something is
  playing or paused, as Home's Now Playing row), the two sliders, the five tiles. The ring is not there when the
  page is pulled down, and touch never brings it: the first wheel tick (or Center) shows it on the stop the wheel
  is on, without moving or acting, and it goes 5 s after the last wheel tick or key; the next tick brings it back
  on the same stop.
- **Sliders and the wheel**: Volume steps exactly as Now Playing's volume does (1 a tick turned slowly, up to 10
  a tick on a fast spin, `vol_next`), and Brightness by the same curve over its own range, identically here and
  on Settings > Display > Brightness (`bright_wheel`); each fill eases to its level at Now Playing's bar's speed
  (`VOL_EASE_MS`).
- **Now Playing block**: a tap anywhere on it but the transport, or Center on it, closes the page and opens Now
  Playing; Previous, Play/Pause and Next keep their own taps. With nothing loaded it does nothing.
- **Holds**: a toggle or tile held 550 ms (`QUICK_HOLD_MS`), by a finger or by Center with the ring on it, closes
  the page and opens its settings; a tap still toggles. Wi-Fi: Settings > Wi-Fi; Bluetooth: Settings >
  Bluetooth; AirPlay: the Network service page (its receiver switch); Car Remote: Settings > Car Remote; Lock:
  Buttons Lock; Sleep: Sleep Timer; Night Shift: its slider page (Display's Night Shift row); EQ: the EQ page;
  Smart Wi-Fi: Settings > Wi-Fi. The sliders and the Now Playing block have no hold.
- **Landing**: as the page comes to rest open, however it was opened, the blocks under the status row overshoot
  8 px down and spring back through rest to 2 px short, still after 280 ms (a damped sine, ease-out, as iOS's
  sheet settles). Only on opening; the status row stays put. The one exception to Motion's no bounce.

### USB

The USB pages ([internals](internals.md#usb-pages), `patch/usb_ui.c`, from `design/mockups/mock_usb_connect.py`):

- **Chooser**, "Use USB For": Charge Only, Transfer Files, USB Audio (and USB Debugging in a `--dev` build), each
  with its Phosphor symbol (regular, 19 px, Text Secondary, white on the selection) and its caption under it while
  selected, the row grown to hold it; then Always Do This, a switch row, after a gap.
- **Mode page** (Transfer Files, USB Audio, USB Debugging, Ejected): a solid circle with its glyph (fill, 44 px,
  white; the eject's a bold check on Positive green), the title, a line in Text Secondary and a Stop pill.
- **Toasts** with a 15 px glyph beside the words: Unplugged (Text Secondary), OK to Disconnect (Positive green),
  USB Audio Ready (white); and charging's: a charger in with the screen on, the battery's state as a tap on it
  says ("Charging · 62%", lightning, green), Charged to 80% when Charge Limit stops it (battery-full, green), and
  Charger too weak (warning, orange).

### Bluetooth

Settings > Bluetooth and its pages ([internals](internals.md#bluetooth-page), `patch/bt_ui.c`, `patch/bt_detail.c`,
`patch/bt_stopped.c`; approved from `design/bt-redesign`, its `NOTES.md` and sheets, on 2026-11-16):

- **Devices before settings.** The switch, Name (the name as its value), then My Devices (the paired ones) and
  Other Devices (the found ones), each a section header in 15 px Text Tertiary a whole row high, then Audio:
  Quality (its choice as the value), Headset Volume, Resume on Connect and Link Volume. With Bluetooth off, the
  switch, Name and a line in 13 px Text Tertiary: "Turn on to play to headphones, a car or a speaker."
- **Scanning is automatic** while the page is open, the list's spinner (Text Tertiary) beside "Other Devices"
  while a scan runs: one as the page opens, then again each time one ends while nothing is connected. No Scan
  row.
- **Device rows** lead with the device's kind (Phosphor `headphones`, `car-profile`, `speaker-hifi`,
  `device-mobile`, else `bluetooth`; regular, 20 px, Text Secondary, white on the selection) centred on x 33, the
  name at x 52. The connected one is a two-line row: the name over "Connected · LDAC · 72%" (what is known: the
  codec, the headset's battery) after the Positive dot, with a chevron. Another paired one says "Not Connected"
  as a value before its chevron; one connecting, "Connecting…" with the activity spinner on the right column.
- **Centre** on a paired row connects it; on the connected one it opens the device's page; Play/Pause held on
  any paired row opens its page.
- **A device's page**: the status row names it; under it the path, "Q2 → WH-1000XM5" in 17 px, and the codec
  line in 14 px Text Tertiary ("LDAC · 96 kHz · 990 kbps", only what is known: the bit rate where the codec
  fixes it), then Battery (when reported), Disconnect (Connect when not connected), Rename and Forget This
  Device. Forget asks on the [confirmation card](#confirmation-card): "Forget WH-1000XM5?", "You'll need to pair
  again to use it.", Cancel first and Forget Device in red. Rename is the letter strip, "Shows on this Q2"
  under the name.
- **Quality**: each choice with its bit rate in 15 px Text Tertiary before the check where the codec fixes one
  (LDAC Best 990 kbps, LDAC Standard 660, aptX HD 576, aptX 384); LDAC Auto's caption, under it while selected,
  "Steps with the signal, 330 to 990 kbps".
- **Name** (the Q2's): the letter strip says "Phones and cars see this name" in 15 px Text Secondary under the
  name, the hint in 13 px under that.
- **Toasts**, each with its 15 px glyph: "WH-1000XM5 connected" (the device's kind, white), "WH-1000XM5
  disconnected" (the glyph in Text Secondary), "BMW 6R81 connected · resuming" with Resume on Connect on,
  "Headphones low · 10%" (battery-warning, red) once at 20% and once at 10% each connection.
- **Bluetooth Stopped**: when the radio never answers, a mode page, the warning glyph on an orange (Warning)
  circle, "Bluetooth Stopped", "Restarting the Q2 to fix it in 9 s" counting down, and a Not Now pill (Centre,
  Return or a tap), instead of a toast and an unannounced restart.

### Plug-in screen

A cable in while the screen is off lights it for 3 s and then turns it off again (S1, V1 from
`design/mockups/mock_charge_anim.py`, approved 2026-11-15; `patch/charging.c`); with the screen on there is no
screen, only the toast and the status row. Black in both halves, over the whole screen:

- **Battery**: a 170 x 76 body round (187.5, 132), 16 px corners, a 3 px `#3A3A3C` outline; a 7 x 28 nub 5 px past
  it with 4 px corners; the fill 8 px inside with 9 px corners, `#34C759`, to the level. Nothing over it.
- **Sweep**: on a 2 s loop the fill's last 14 % grows up to the level (1.3 s, ease out), holds to 1.7 s and fades
  out by 2 s. Full, or held at Charge Limit, the fill stands still.
- **Level**: "62%" in Inter Medium 30 px, white, its centre 12 px right of the screen's on y 212, and Phosphor's
  `lightning` (fill, 26 px, `#34C759`) 3 px left of it. No outlined bolt.
- **Word**: "Charging" ("Charged" at 100 %) in Inter Regular 16 px, Text Secondary, centred on y 244.
- A key or a touch ends it and leaves the screen on, doing nothing else; a page opening over it (a computer's
  chooser) ends it the same way.

### Software Update

SD Card Update ([internals](internals.md#sd-card-update), `patch/sw_update.c`, approved from
`design/mockups/mock_fwupdate.py` on 2026-11-16), the status row "Software Update" over mode pages as USB's:

- **Checking for an Update…**, "Looking on the SD card": no circle, a 48 px spinner of twelve round 4 px spokes in
  Text Primary, the head turning a spoke every 80 ms and each spoke behind it dimmer, to a quarter.
- **Install Update?**, the confirm with the question in the status row: the update's name and tag in Inter Medium
  17 centred on y 66, "Installed: TAG · SIZE on the SD card" in 15 px Text Tertiary on y 88, then Cancel (selected)
  and Install and Restart from y 112, and a two-line footer under them ("Takes about 2 minutes. Keep the Q2 charged;
  your music and settings stay.").
- **Restarting to Install**, "Don't turn off the Q2": `arrow-clockwise` bold on Accent Blue, and a 180 x 6 bar with
  round ends under the stack (top + 172), Track `#333333` filled in Accent Blue with the unpacking (the user's, 2026-11-16: progress fills are the accent).
- **Results**, each a fill glyph on its circle: You're Up to Date (check, bold, Positive green, "VERSION (TAG) is
  installed"), No Update Found and No SD Card (`file-arrow-down`, `#3A3A3C`), Charge to Update and Not Enough Space
  (`battery-warning` and `warning`, `#FF9F0A`), Update Can't Be Used (`warning`, `#FF453A`). Return or Centre
  leaves.

### Gestures

- **Swipe back**, as iOS's: a finger that comes down within 24 px of the screen's left edge and
  drags right takes the page with it, the status row staying; what it uncovers is black. Let go
  past 40% of the width, or flicked right, and the page slides on off the right edge and Return
  goes back; let go short and it springs back. Both take the page slide's 120 ms, `cubic_out`.
  Wherever Return leaves a page: a page over another, and on Home the list a row opened
  (Settings, Streaming). A drag up or down from the edge scrolls as anywhere; a sideways drag that
  starts mid-screen stays the screen's own (Now Playing's pages, Cover Flow, the letter strip,
  sliders). Not on Home's own list, dialogs, Control Center or the keyboard
  ([internals](internals.md#swipe-back)).

### Motion

- Page push and pop: 220 ms horizontal slide, `cubic_out`.
- Selection bar: moves with the wheel, no animation of its own beyond the list's scroll.
- Nothing pulses, glows or bounces, a list's end included, with one deliberate exception: Control Center's
  landing (an 8 px overshoot and one 2 px undershoot over 280 ms as it comes to rest open, [Control
  Center](#control-center)).
- **Marquee fades.** A scrolling text (the selected row's title, the status row's title, Now
  Playing's title, artist and album) is clipped to its own column, inside the glass and before any
  value or chevron on its row. A 12 px fade covers its right end while the text goes on past it,
  and its left end while it is scrolled away from its start, so it rests crisp; each goes from
  clear to the colour behind the text (the Canvas, or Accent Blue on the selection). A truncated
  text that is not scrolling keeps its ellipsis (`list.marquee.fade`).
- **A value cut off scrolls too** (the user's, 2026-11-16, from About's CFW. Version): on the selected
  row the marquee takes the first text cut off at its column, the title, else a key/value row's value,
  so any value too long for its row reads in full when its row is selected (`navigation.c`
  `marq_moved`); every other row's keeps its ellipsis.
- **Now Playing's lines** move as one marquee: the title, artist and album that overflow hold at
  rest 2.75 s, slide left together at 35 px a second (the longest line's pace; the others take the
  same time), and land back at rest together, each followed by its own copy 40 px behind, so
  nothing snaps back. The progress bar's fill moves a pixel at a time between the player's
  seconds rather than a second's width at once.

## Screens

Q2 Pod's screens mapped to the patterns above, most-seen first. The inventory behind this
table is the 126-screen survey in the design notes; screens not listed follow their nearest
pattern.

| Q2 Pod screen                       | Pattern                     | Notes                                                                                                      |
| ----------------------------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Home (Full)                         | List row                    | Home uses the Full layout in this theme: the concept's menu is a full-width list. No page title: the clock, centred. Seven rows from y 58, all at rest; a list with more (Settings' eight) fades the rows below into black from y 266 to 316, and once scrolled the top row's first 14 px too; scrolled to the end the last row's foot is at y 282: every list's ending ([Corners](#corners)) |
| Home (Split)                        | List row beside art         | Rows from y 40 (into the status row's empty left, the clock at its right) to x 204 (bar 16 to 188, chevrons 176); the art from x 196 and the top edge to the edges: the playing cover panning slowly while a song plays, else the library's covers in turn, each zooming and drifting and cross-fading into the next; the art's top 50 px and bottom 40 px darkened toward 25% brightness (a vignette, approved as V4 on 2026-11-15); the status row's clock, play state and battery over it at the right, each glyph white or black by contrast with the darkened art under it. Seven rows, all at rest (the last's foot at y 264); a list with more (Settings' eight) fades the rows past them into black within 56 px of the glass's bottom edge and, once scrolled, the row above y 40 within 34 px of its top edge, both following its rounded corners |
| Now Playing                         | [Now Playing](#now-playing) |                                                                                                            |
| Now Playing's About                 | [Track details](#track-details) | Header, quality line, then a grid of name over value                                                   |
| Library                             | List row                    | Rows in the user's order and choice: [Edit Library](#edit-library)                                         |
| Albums                              | Two-line row with art       |                                                                                                            |
| Album tracks                        | Song rows                   | Track number, title, length                                                                                |
| Folder view                         | Song rows                   | Songs as album tracks; folders with the folder glyph and a chevron; videos (Library > Videos) plain rows on the text column |
| Artists, Genres, Composers          | List row                    | The [A-Z index](#a-z-index) past 50 rows, as Albums                                                        |
| A card app's artist                 | [Artist page](#artist-page) | Header, Shuffle and Radio, then Top Songs, Albums, Singles & EPs, Similar Artists and All Songs            |
| A Library artist                    | [Artist page](#artist-page) | Header, Shuffle alone, then Top Songs (once played), Albums and All Songs                                  |
| Songs, a playlist's songs           | Song rows                   | The artist inline; the [A-Z index](#a-z-index) past 50 rows in name order                                  |
| Volume                              | Progress                    | A 6 px bar across the bottom of Now Playing, not a panel                                                   |
| Cover Flow                          | Own                         | Covers on black, no reflection floor; the caption uses Song title and Artist roles; an album's tracks as Album tracks; an artist's covers titled by the artist, other covers with the clock in the status row as Home |
| Playlists                           | [Playlists](#playlists)     | On-The-Go first; 44 px rows with a 36 px picture or tile                                                   |
| A playlist's songs, On-The-Go       | [Playlists](#playlists)     | Action rows, then the songs with the artist on the right                                                   |
| Add Songs                           | [Add Songs](#add-songs)     | The chooser, then artists, albums and songs with marks                                                     |
| Hold menu, More                     | [Menus](#menus)             | The inset modal over the page                                                                              |
| Settings and its pages              | Key/value and switch rows   |                                                                                                            |
| About                               | Key/value table             | The wheel walks the rows and nothing opens; values in Text Secondary, a cut-off one scrolling when selected |
| Pickers (Play mode, Sort, Language) | List row                    | The current choice carries a 12 px Accent Blue check on the right column                                   |
| Storage                             | Storage bar                 | [Progress and sliders](#progress-and-sliders): the bar, its legend rows (Downloads and Stream Cache open pages) and two footers |
| Storage > Downloads                 | Key/value rows              | Limit (its picker the inset modal's), then each item: Phosphor `arrow-circle-down` (18 px, Accent Blue) for one the user picked or `arrows-clockwise` (Text Secondary) for one the app keeps itself, white on the selection, centred on x 33, its name at x 52, size and chevron; two footers |
| Slider settings                     | Slider page                 | [Progress and sliders](#progress-and-sliders)                                                              |
| Equalizer (Simple)                  | Vertical slider             | Its [tabs](#tabs), then rows: the switch, Presets, Reset                                                   |
| Wi-Fi                               | Switch row, then a section  | Search Again row; "Networks" after 12 px, "Searching..." on its line; glyphs at right                      |
| Bluetooth                           | [Bluetooth](#bluetooth)     | Devices first under My Devices and Other Devices, the connected one two lines ("Connected · LDAC · 72%" after the Positive dot); then Audio |
| A Bluetooth device                  | [Bluetooth](#bluetooth)     | The path and its codec line, then Battery, Disconnect, Rename and Forget This Device                      |
| Bluetooth Stopped                   | [USB](#usb)'s mode page     | The warning on an orange circle, the restart's countdown and Not Now (`bt_stopped.c`)                      |
| Bluetooth receiver (a phone playing to the Q2) | [USB](#usb)'s mode page | The accent circle with the Bluetooth glyph, the phone's title over its artist ("Playing from Phone" over "Wheel sets the volume" before it names one), the codec in the pill (`bt_recv.c`) |
| Update Local Music                  | Progress                    | Counts as key/value rows under the status row                                                              |
| Shuffle, Repeat changes             | Confirmation                | New in this theme                                                                                          |
| SD Card Update                      | [Software Update](#software-update) | A spinner while it checks, the confirm, then mode pages                                         |
| Quick Settings                      | [Control Center](#control-center) | The status row's clock centred, no title                                                        |
| Search                              | [Text entry](#text-entry)   | The letter strip's field on, under six single-line results ([ipod](ipod.md#search))                         |
| New Playlist, Rename Playlist       | [Text entry](#text-entry)   | The letter strip's field off, the name large over it ([ipod](ipod.md#name-strip))                          |
| Keyboard                            | [Text entry](#text-entry)   | None shows: a text box is the letter strip's ([ipod](ipod.md#keyboard))                                    |
| Buttons locked                      | Own                         | A padlock leftmost in the status row while Buttons Lock is on; a blocked press shows a #1C1C1E card over the page dimmed to 45%: a 56 px padlock, "Buttons Locked" 16 px Medium and the hint 13 px Secondary, for 1.5 s |
| A card app's QR page (sign-in)      | Own                         | The status row names the app. The code on a white plate (166 px, radius 12) centred under it, the app's note ("Scan with your phone to sign in") 17 px Medium white and "or open ADDR · PIN 482 913" 15 px Text Tertiary under it. Once the app reports a step, a mode page: one solid 92 px circle (Accent for phone and check, #34C759 done, #FF453A fail and error) with a 44 px white Phosphor fill glyph (`device-mobile`, `arrows-clockwise`, `check` bold, `cloud-slash`, `x` bold), the title 22 px Medium and one line 15 px Text Secondary, the stack centred between the status row and the bottom; the wheel does nothing |

The concept's Apple Music, Apple TV and CD sync pages have no Q2 Pod equivalent and are out of scope; its storage
page became Settings > Storage. Spotify's pages follow the list patterns.

## Do and don't

| Do                                                   | Don't                                       |
| ---------------------------------------------------- | ------------------------------------------- |
| Separate with space and grey                         | Add boxes, cards or panels behind lists     |
| Use Accent Blue for the one selected or active thing | Tint icons, headers or second lines blue    |
| Keep every list on the 32 px grid                    | Mix pitches in one list                     |
| Let titles scroll when selected                      | Shrink type to fit                          |
| Inset from the glass with `corner_x`                 | Put anything in the corners the glass hides |
| Write sentence case                                  | Write capitals or add letter spacing        |

## What the engine needs

Built, as `theme.xml` keys and geometry fields ([internals](internals.md#themes)):

- The colour tokens and the type scale in one place: `tools/theme.py` `TOKENS` (to `patch/tokens.h` and
  `tools/art.py`'s inks), `tok()` and `TYPE()` in `patch/ui_kit.c` and `patch/ui_kit.h`.
- OLED as the one built-in theme, standing alone: `assets/themes/oled/theme.xml`, index 0, extending
  no other theme, its own `oled` geometry set. A unit left on another theme by an older build takes
  OLED's preset once at boot ([ipod](ipod.md#theme)).
- Font faces as a table, with Inter Regular and Medium and tabular digits.
- A theme's own accent tones, and the selection bar's radius and inset on every row.
- A status row in place of a bar band (`status_y`), its title and its battery's end `STATUS_ROW_INSET`
  in from the sides, its glyph slots left of the battery (`patch/status_row.c`: `status_slots_left`,
  `status_slots_paint`, `status_slot_at`) and its clock beside them, or centred where there is no title; lists that scroll a
  whole row at a time and stop with the last row's foot on `list_end`, the rows past it fading (`list_fit`).
- A theme's own art from Phosphor (`tools/art.py`), loaded by its own prefix (`theme_art`): the
  chevron (`chevron_icon`, `patch/list_marks.c`) and the status row's glyphs (`STATUS_GLYPHS`); the slim battery
  (`battery_slim`); the wheel's scroll indicator (`list.scrollbar.*`, `wheel_sb_arm`);
  row art's 4 px corners
  (`list.art.radius`); key/value rows, switches and values beside a chevron on the right column; the
  switch, Gain's value, the pickers' check, disabled rows, the slider page, EQ's Simple tab and the Wi-Fi
  page as above (`settings.values.switch`); song rows
  (`patch/song_rows.c`) and the Song Artist and Song Length options; Songs' action row
  as the Shuffle row (`list.shuffle.row`); a list left by Return opening at its top when chosen
  again (`list.reopen.top`).
- Now Playing on the set's grid (`np_art`, `np_bar_y`), its art's corners (`playing.art.radius`), its
  artist and album in Inter (`playing.artist.font`), shuffle and repeat, the heart and More as the theme's
  own glyphs (`playing.modes.icons`), the marquee fades (`list.marquee.fade`), and Track Time (`NPTIME`), whose
  tap on the right-hand time shows the length or the time left.
- Menus as the inset modal over the page ([Menus](#menus)): the set's `modal`, `patch/menu_modal.c`, the
  Phosphor symbols as the theme's own art.
- Home's Split on the grid: the set's `split_row`, `split_art`, `split_art_up` and `split_inset` (the art from
  x 196 and the top edge to the edges, its top 58 rows painted by the bar with the status row's clock, glyphs
  and battery over them, each glyph in one ink, white or black, whichever has the higher WCAG contrast against the
  art's mean luminance under it, weighted by the glyph's coverage and held 0.03 past the bound against flicker; the
  charging green and low red keep their colours (`home_ink`, approved from `mock_split_top.py` on 2026-11-14, one
  ink a glyph from the same day, as each pixel's own pick speckled over lettering), the playing cover panning slowly
  across its 110 % zoom with an eased turn at each end and 2 % of the zoom kept clear of the pane at each edge, so a
  cover's own border never shows, on Now Playing's row (there only while something is playing or paused); on every
  other row the library's covers in turn as an iPod classic's menu shows them, cross-fading from the frame as it
  stands as the wheel moves between the two, each zooming between 1 and 0.9 of the cover and drifting once over its 9 s, 1.5 s cross-fades between covers
  and on a track change (docs/ipod.md#home, `slideshow.c`)), the main menu from y 40, its top rows painted by the bar (`coverflow_home_strip`, approved from
  `mock_overflow.py` on 2026-11-15), and the lists Home's rows open as full-width pages with the art hidden
  (`home.lists.full`).
- Settings > Storage and its Downloads (`patch/storage.c` `storage_paint`): the storage bar and its legend, the
  items' marks, the values beside their chevrons and the footers; and the song rows' marks for where a song lives
  (`catalogue.c` `catalogue_row_mark`).
- The toast, the confirm as a dialog page and the empty note (`patch/feedback.c`), and the confirmation after a shuffle.
- The confirmation card (`patch/feedback.c` `confirm`, [Confirmation card](#confirmation-card)).
- One mode page painter for USB's pages, a card app's sign-in steps, SD Card Update and Bluetooth's receiver
  (`patch/feedback.c` `mode_page_paint`, `mode_page_t` in `patch/ui_kit.h`). The plug-in screen keeps its own
  approved layout ([Plug-in screen](#plug-in-screen)).

Still to build:

- The confirmation for Repeat and Now Playing's mode changes.
