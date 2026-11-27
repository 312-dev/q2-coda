# OLED design standard

The OLED theme gives Vamp the look of a present-day iPod classic: a black screen, white
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
   glows are not used to separate things; spacing and gray levels are. One glow is used, as
   atmosphere and not to separate: the album art's, behind Now Playing's lyrics and
   [rating](#rating-page) pages, fading to black.
3. **One accent.** Accent Blue marks the one thing that is selected, playing or on. If two
   blue things compete on a screen, one of them is wrong.
4. **Type does the work.** Hierarchy comes from size, weight and gray level, in that order.
   No capitals, no letter spacing, no outlines.
5. **The wheel decides the layout.** Lists move one row per tick and the selection is always
   a full row, so every list is built from the same row pitch.

## The screen

| Property      | Value                        | Notes                                                                                   |
| ------------- | ---------------------------- | --------------------------------------------------------------------------------------- |
| Panel         | 375x320, upright             | The concept's screens are near square; the Q2 is wider, so layouts gain width, not rows |
| Glass corners | 80 px radius (`GLASS_R`)     | Hides up to 46 px at each end of a row 8 px from an edge; see [Corners](#corners)       |
| Status row    | glyphs centered on y 23       | An overlay on the page: no band, no rule, no fill; ends 62 px in from each side         |
| Page          | 375x320, the whole screen     | [Full-bleed](#full-bleed): backgrounds and art reach both edges under the status row    |
| List rest     | rows from y 58 to 282        | Seven 32 px rows; where content rests, not where it is clipped                          |
| Panel type    | LCD (`lcd_h0209s`), not OLED | Black is a color here, not "off": keep large areas black anyway, it is the look        |

The geometry below (the status row, the 58 px list top, the 16 px selection inset, the 28 px right column and
the scroll indicator) is what the user chose from mock-ups on 2026-11-13 (mock-up E and its six-screen
follow-up), in place of a first draft that stepped the selection bar in at the corners and drew the scroll
track along the glass. The status row's height and ends are the user's from `mock_status_up.py` on 2026-11-15
("do 62 and '...' any title thats too long"): up from y 37 into the glass's top curve, the list's top unchanged.

### Full-bleed

Every page is full-bleed: its window is the whole 375x320 screen (`tools/build.py` `FULL_BLEED`), and the status
row is an overlay painted after it, its glyphs on top with nothing behind them, so a page's background, the glow and
its art run to the top and bottom edges. The layout insets are rest positions for content, not clip edges: a list's
first row rests at y 58 and its last at y 282. Stock's pages move their content down the 58 px the row takes
(`tools/ipod.py` `full_bleed`), so each rests where it did; payload code that lays such a page out from the row's foot
adds the window's `bar_foot` (`patch/theme_engine.c`), and a list end or a settings row is measured on the screen
(`list_fit_end`). The pages the payload builds (`patch/coverflow.c` `page_open`, the shuffle confirmation) are the same:
the window is the whole screen and each lays its content out from `bar_foot`, or in a view placed there `page_room`
high, so it rests where it did; a header that stands above the row's foot (an artist's monogram, About's cover) is the
page's own paint under the row, and their lists run under the row as any list does ([List edges](#list-edges)). The
row's background is clear in every state (the bar is a disabled widget, so AWTK paints its disable style) and it fills
nothing over any page. A page slide draws both pages' full-screen snapshots and then the row over them
(`page_parallax.c` `parallax_curr`). A press on the row is the row's, as before: Control Center's pull and the row's own
taps, never a page's swipe back.

### List edges

A scrolling list runs edge to edge (the user, 2026-11-18: "vignette edges to the screen's edge"): at rest its first
row is on y 58 and, scrolled to its end, its last row's foot on y 282, as [Corners](#corners) says; in between its
rows run on under the status row to the screen's top edge and past the list end to its foot, and fade to black at
both edges. At the top the rows above the list's top fade from clear 14 px under that top to black at y 8, so the
status row's glyphs, which stand over them, stay legible: under the glyphs' middle (y 23) a row shows at about a
seventh of its brightness. At the foot the rows fade as Corners says, into black by y 316.

- **Which lists.** Every vertical list whose box starts at most 90 px down the screen (`REACH_TOP`): the
  settings pages, the Library's lists and tables (Songs, a playlist's songs), Home's lists and Edit Home, and the
  pages the payload builds (Recently Added, card apps' lists). A list lower down, under a page's own header or tabs (EQ's), keeps its
  rows in its box. So does a list under Songs' Shuffle row (the user's, 2026-11-19): the row stays fixed under the
  status row and the songs scroll under it, with no row above it, where one showing made Shuffle look as if it
  floated over the list.
- **A menu sheet's rows** (the menus, Up Next) stay in the sheet: the sheet's top is their edge, and they fade under
  y 50 as [Menus](#menus) says.
- **Taps** on the band are the status row's, as on any page ([Full-bleed](#full-bleed)): a row shown under the
  status row is not tappable there; scroll it down to tap it. Its box, its selection, the wheel and a drag are as
  they were, so a selected row never stops under the status row. On Home, whose bar shows under touch, a drag
  that ends with the selected row under the status row, cut by it, or in the fade past the list's rest moves the
  selection to the whole row nearest the list's middle, and the old bar goes with it: the clock always stands
  alone on its line ([internals](internals.md#touch-mode)).
- Built as a paint of the list's rows into the status row's band, under its glyphs (`patch/nav/row_paint.c`
  `list_reach_paint`, the fade `patch/home.c` `list_top_fade`; Home's Split fades follow the glass's corners as
  before, `home_fades`).

### Corners

The concept puts the clock in the top-left corner and the battery in the top-right. On the Q2
those spots are under the glass. So:

- The status row sits up in the glass's top curve, centered on y 23, where the glass hides 32 px at
  its glyphs' top edge (y 16), so its ends draw in to follow the curve: the title's text starts
  62 px in (`STATUS_ROW_INSET`) and the battery ends 62 px from the right edge, the clock beside
  its glyphs (or centered, on a page with no title). The list's own columns (24 and 28) stay
  below it.
- No text or control may start inside `corner_x(y, h)` for its row. In the middle of the
  screen, from y 80 to y 240, the inset is 0.
- Every scrolling list ends as Home (Full)'s does (the user's, 2026-11-15): its box runs on to the
  screen's foot and the rows past the list end, y 282 (the 32 px slots from y 58 are y 58, 90, ...,
  250, seven in all), peek through a fade into black from y 266 to y 316, black below; once the
  list has scrolled, its rows run on under the status row to the screen's top edge, fading to black
  there ([List edges](#list-edges)). A selected row is never
  faded and never lands past y 282: scrolled to its end, by the wheel or a drag, the last row's
  foot is on y 282 (Vamp's `list_end`, `navigation.c` `list_fade`; a virtual table's, Songs and the other Library tables, by `lyrics_status_bar.c` `ringnav_table_drag` and `ringnav_table_scroll`, a drag and a fling alike), also after
  the page comes back on top (Return from a row's page) or its list is rebuilt (Bluetooth's, as it
  scans), or a caption closes under the wheel's move to the last row (Battery's In-Vehicle Mode, then Battery History): the layout itself puts the padding and the scroll back (`row_paint.c` `list_relaid`), so
  no frame shows the rows lower and no part of the screen keeps that frame. A row that grows in place, a captioned row showing its caption
  or a grabbed inline slider, scrolls the list by whole rows until its grown foot is on or above y 282 as well, never
  only inside the list's box, which runs on to the screen's foot (`nav/lists.c` `list_shown_h`). A page with nothing to select (text) ends the
  same way: the wheel scrolls it until its content's foot is on y 282, as a drag does. A list whose top is off the 32 px grid (EQ's, under its
  48 px tab bar) or whose rows are of another pitch keeps whole rows at its top, so scrolled to its end its last row
  ends on the last whole row above y 282. The build refuses a list box that stops at or above y 282
  (`tools/ipod.py` `cut_lists`).
- Every screen is checked against the glass: a screenshot composited with the 80 px mask shows
  no lit pixel under the curve.

## Color

All colors are opaque. Alpha is not used for text: grays are stated as grays.

The tokens live once, in `tools/theme.py` `TOKENS`, written to `patch/tokens.h` for the payload (`tok()` in
`patch/ui_kit.c` picks the half; `RGB_<NAME>` and `RGB_<NAME>_L` are each half) and read by `tools/art.py` for
its glyphs' inks. A test fails on a color literal equal to a token anywhere else in `patch/`.

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
| Favorite       | `#FF375F` | Now Playing's heart while the song is liked (iOS's dark systemPink; not Negative, which means a failure) | the same in Light Mode |
| Raised         | `#1C1C1E` | Toasts, cards, the modal's panel, keys                                       |                                                            |
| Raised 2       | `#2C2C2E` | Pills and placeholders on a raised surface, a lifted row                     |                                                            |
| Off            | `#3A3A3C` | A gray glyph's circle, a control that is off                                 |                                                            |
| Caption on accent | `#D6E2FA` | A selected row's caption, on the bar                                      |                                                            |

The concept grades its blue differently in each image. Accent Blue is one value between the bar
and the progress fill, chosen so white text on it reads at about 5:1. Use it everywhere; do not
add a lighter or darker blue.

### Light Mode

The concept has no light screens; Vamp's Light Mode exists and must keep working. It is Auto unless
chosen (light from 7:00 to 19:00; the owner's default from 2026-11-27, `[IPOD] LIGHT` absent reading as 2). The light
half flips grays, not hues:

| Token          | Dark      | Light     |
| -------------- | --------- | --------- |
| Canvas         | `#000000` | `#FFFFFF` |
| Text Primary   | `#FFFFFF` | `#000000` |
| Text Secondary | `#ABABAB` | `#5C5C5C` |
| Text Tertiary  | `#858585` | `#6E6E6E` |
| Chevron        | `#999999` | `#8A8A8A` |
| Track          | `#333333` | `#D9D9D9` |
| Accent Blue    | `#2160D8` | `#2160D8` |
| Raised, Raised 2, Off | as dark | flipped as every gray: `#DCDCDA`, `#CECECC`, `#C2C2C0` |
| Positive, Warning, Negative, Caption on accent | as dark | the same |

## Type

Face: **Inter** (SIL OFL 1.1), Regular and Medium, subset as the build subsets its other faces,
with tabular digits so a running time never changes width. Inter stands in for the concept's
San Francisco, which cannot ship. See `assets/fonts/` for the files and license.

| Role                                  | Face          | Size  | Color         | Example        |
| ------------------------------------- | ------------- | ----- | -------------- | -------------- |
| Status row (the clock)                | Inter Medium  | 15 px | Text Secondary | 9:41           |
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
- **Set text for small labels the payload draws itself.** AWTK places each glyph on its advance rounded to a
  whole pixel and carries the error on, and draws a space about 3 px wider than Inter's, so at 13 px a label's
  gaps run uneven ("Smart  Wi-Fi"). `ui_set` (`patch/ui_kit.c`) sets each glyph from Inter's own advances
  (`FONT_ADV`, written into `stock.h` by `tools/build.py` `face_advances`), each rounded on its own, with no
  added tracking (`TRACK_CAPTION` 0). Control Center's names are set this way.

## Spacing

All lists share one grid.

| Token              | Value                                        | Notes                                                                              |
| ------------------ | -------------------------------------------- | ---------------------------------------------------------------------------------- |
| List top           | y 58                                         | 28 px under the status row's glyphs                                                |
| Row pitch          | 32 px                                        | seven rows, y 58 to 282 (see [Corners](#corners))                                  |
| Two-line row pitch | 52 px                                        | four rows, y 58 to 266                                                             |
| Text inset (left)  | 24 px                                        | the rows and the art share this column; the status row's clock starts at 62        |
| Right column       | 28 px from the right edge                    | chevrons and values on every row: they never move between rows                     |
| Leading art        | 40 px at x 24, 6 px down its row, text at 72 | 4 px corners, in two-line rows                                                     |
| Bar inset          | 16 px each side, every row                   | no corner stepping: at the bottom row the corner hides 14.5 px                     |
| Bar radius         | 8 px                                         |                                                                                    |

## Components

### Status row

- No band: the status row is an overlay on the page's own ground, with no fill or rule, over the 58 px where content does not rest. Its glyphs center on y 23.
- Left: the clock, in Inter Medium 15 px, Text Secondary, its text starting 62 px in (`STATUS_ROW_INSET`), as the
  battery ends 62 px from the right, on every page; it never moves for the glyphs. Its figures stand on rows 17 to 27.
  The row names no page, as iOS's names none (layout D of the user's `hpicon/mockup3.png`, 2026-11-25): a page that
  needs its name says it in its own content (an album's or artist's header). A 12-hour clock has no AM or PM. Home in
  Split's clock stands over the list, in Text Primary where the art runs up under the row (the user's, 2026-11-20,
  `design/home-clock-left`); the glyphs stay over the art at the right, where they take one ink, white or black, picked once for the row from the art's
  mean lightness under all of them, as iOS's status bar takes one style; the battery's green, red and orange keep
  their hue (`mock_status_icons.py`, the user's, 2026-11-16). Under Split's vignette the top of the art is darkened
  toward 25%, so that ink is white over almost any cover.
- Right, right to left: the battery, its nub's end 62 px from the right edge (x 313), then the glyph
  slots, each shown only while it has something to say, nothing in the row ever wrapping (`status_row.c`;
  [internals](internals.md#status-row-slots)):
  - **Play state**, left of the battery's body, a 10 x 11 px box in Text Primary playing or paused, as every
    glyph: playing, three level bars as Up Next's, 2 px wide with round ends and 4 px apart, standing on the
    digits' baseline, 3 to 11 px tall, moving as every [level bar](#level-bars); paused, the pause, two 3 px bars
    11 px tall with 0.9 px corners at 0.5 and 6.5 px into the same box; stopped, nothing. On pause the bars stop and
    cross-dissolve into the pause over 250 ms, `cubic_out`, and back on play, the bars moving again once it ends;
    the dissolve stays in the 10 x 11 box, so nothing else in the row moves; Reduce Motion swaps them at once (the
    user's, 2026-11-16 21:52). Only the box repaints as
    the bars move. A tap on it opens Now Playing (`mock_status_bars.py`, 2026-11-15; `mock_status_icons.py`,
    2026-11-16). On Home, while its first row is in sight, they lead that row instead ([Home's first
    row](#homes-first-row)).
  - **Wi-Fi**, left of the play state: iOS's fan, a 90 degree sector 11 px in radius from an apex on the digits'
    baseline (15.6 x 11 px), cut into a dot (radius 3.6) and two bands (5.0 to 7.3, 8.7 to 11) with 1.4 px gaps;
    the signal lights the dot, then the bands, in Text Primary and the rest is `#3A3A3C` (`#C7C7CC` in Light
    Mode), so the shape keeps its size; Phosphor `wifi-x` (bold) connected with no internet; nothing with Wi-Fi
    off or not connected (`mock_wifi_status.py`, `mock_status_icons.py`).
  - **Sync**, left of the glyph before it: while a card app checks its server or its metadata, Phosphor
    `arrows-clockwise` (bold) turning in Text Primary, 30 frames a second, a half turn a second (S1, the user's
    30 fps and white, 2026-11-20, `sync-heart-1120/sync-arrows-white.mp4`), its box its upright ink's 12 px so
    the 6 px either side match the row's (turning, its corners pass up to 1 px nearer); while it downloads songs, a 14 px ring (1.6 px) in
    `#333333` filling clockwise from 12 o'clock in the accent, Phosphor `arrow-down` (bold, 8 px, Text Primary) in
    it (S2, `mock_sync_status.py`).
  - **Wired headphones**, left of the sync: Phosphor `headphones` (regular) in Text Primary, 12 px tall on rows 16 to
    27 as the battery's body (its ink 13.6 px wide), while the sound goes to a jack (3.5 mm or 4.4 mm) or a USB DAC
    (USB-C headphones among them) (the user's, 2026-11-25, `hpicon/mockup2.png`). With no output connected (nothing
    plugged in, no Bluetooth device connected, no guest or AirPlay speaker playing), a deaf ear in the same place:
    Phosphor `ear-slash` (regular), 12 px tall on the same rows, in a 10 px box, its ink as far from the fan's as the
    headphones' (the user's, 2026-11-25: "a deaf ear icon ... ship it"), in Negative (red) in both halves (the user's, 2026-11-26).
  - **Bluetooth** (P9 in `design/bt-redesign`), left of wired headphones: nothing while Bluetooth is off or nothing
    is connected, or while wired headphones take the sound (a jack plugged in over a linked headset plays there, and
    the row shows where the sound goes: one glyph, never both); Phosphor `bluetooth` (bold, 12 px tall on rows 16 to
    27 as wired headphones, 8.4 px wide) in Text Tertiary while a device connects and in Text Primary once one is connected; a
    headset that reports its level, `headphones` (bold) and a 15x8 battery 2 px after it, filled to the level as
    the status battery's (red under 20%); a car, `car-profile` (bold, 18.5 px wide at 11 tall). While a guest plays
    ([Guest Play](#guest-play)), `device-mobile` (fill) in the accent, and while it is relayed to the Q2's
    Bluetooth device a `›` (8 px, Text Secondary) and that device's glyph.
  - **Buttons Lock**: the padlock (Phosphor `lock-simple`, fill, 13 px, Text Primary).
  - **Offline**, the leftmost, so it reads first after the clock: Phosphor `cloud-slash` (bold) in Warning
    `#FF9F0A`, 14 px wide at 11 tall, while a card app (Subsonic) says its server can't be reached (no answer, a
    name that doesn't resolve, a proxy's 502, 503 or 504) and Wi-Fi is connected, until a request to the server
    succeeds; with no Wi-Fi the missing fan already says why. On Now Playing too (`mock_subsonic_offline.py`, the
    user's, 2026-11-19; [internals](internals.md#card-app-reach)).
- **The strip.** The glyphs left of the battery have the room from 8 px past the widest clock, at the row's left, to
  the battery (about 170 px: every slot at once with a headset's battery takes about 145). When they need more, that strip (never the battery) scrolls as a marquee: at rest its
  left end shows, the first glyph after the clock, for 2.75 s; then it slides left at 35 px/s until its right end
  meets the battery's 6 px, holds there 1.5 s and goes back, as Now Playing's lines do. Each end whose glyphs are cut
  fades over 6 px, the house marquee's 12 tightened: the right at rest, both while it slides, the left at its end.
  It runs under Reduce Motion, as every marquee does; only the strip's box repaints as it moves (the user's,
  2026-11-19, `mock_subsonic_offline.py` C and D).
- Every glyph is 11 px tall, the clock's digits' height, on their rows (17 to 27), but the Bluetooth rune and wired
  headphones, 12 px on rows 16 to 27 as the battery's body, so their ink centres on row 21.5 with the battery's and
  the fan's (the user's, 2026-11-25): the Phosphor ones sized by
  their ink's height, not its longer side, so a wide one (`wifi-x`, the car) is as tall as the rest; strokes come
  out about 2 px (the fan's bands 2.3, the bars 2, the pause 3). 6 px of black part two glyphs, 8 px the clock
  from the first, as iOS spaces them (`mock_status_icons.py`, the user's, 2026-11-16).
- Battery, in iOS's proportions: a 25x12 body on rows 16 to 27 (a row past the digits' top, as iOS's stands)
  with 4 px corners and a 1 px `#6E6E6E` outline,
  the charge a solid fill 2 px inside it with 2.2 px corners to the level, charging or not, in
  Text Primary (under 20 % `#FF453A`, the only red on any screen; `#34C759` green on a cable that
  charges it, holds it full or holds it at Charge Limit; `#FF9F0A` orange on a charger too weak to
  keep up), and a 1.5x4.4 nub with 0.8 px corners in `#6E6E6E`.
- Charging, Phosphor's `lightning` (fill, 7 px, Text Primary) sits on the body's center with a
  1 px gap in the Canvas cut round it where it crosses the fill, and no outline; a weak charger's
  `warning` (fill) takes its place the same way. Picked as I2 from `mock_batt_inside.py` on
  2026-11-15. Full or held at Charge Limit with the cable in, the fill is green with an upright plug
  in the bolt's place, drawn on whole pixels (5x7 ink, panel rows 19 to 25, columns 295 to 299: two
  1 px prongs with a 1 px slot, a 5 px body with rounded lower corners, a 1 px cord) over the same
  1 px Canvas gap; the two differ only by level, and a tap's toast names which. Picked as option A
  from `mock_plugged_full.py` on 2026-11-17. On battery there is no glyph.
- With Battery on Percent, the level and "%" in BATT_PX type stand in the battery's place, in its
  colors, on one line: nothing in the status row ever wraps. The slot is as wide as "100%" measures,
  each digit and the sign rounded up to a whole pixel, and the row lays out from the right edge, so
  the glyphs to its left move over for it.
- **The volume pill.** A volume change made off Now Playing (a headset's own keys or its AVRCP volume, the Car
  Remote) shows in the clock's place: a Raised (`#1C1C1E`) pill 30 px tall on the row's centre (rows 8 to 37) with
  15 px round ends, from x 50, so its speaker starts where the clock's text does, 199 px wide, narrower from its
  right end where the glyph slots leave it less room, ending 8 px short of the leftmost slot, down to 141 px, which
  then ends there and moves left (never past x 24). Inside, 12 px from each end: the speaker
  (Phosphor `speaker-simple-high-fill`, 11 px tall as every status glyph, Text Primary), 8 px, a 4 px track in Track
  with round ends filled in Text Primary to the level, 8 px, and the number (0 to 100) in the row's face, Inter
  Medium 15 px, Text Secondary, ending at the pill's inner edge in a 32 px column of tabular digits, so the track
  never moves. At 0, the mute, `speaker-simple-x-fill` and an empty track. The number and fill are the Q2's 0 to 100 in
  every case: with Link Volume on a headset, its own level read back on that scale, as everywhere else. The clock goes out while it
  shows; it follows every change, holds 2 s after the last, then fades out over 250 ms (cubic out) as they fade back
  (a cut under Reduce Motion). Not on Now Playing, the AirPlay or radio player (their own volume band), over Control
  Center (its slider follows) or a video. A change repaints the pill's box alone. Picked as option A from the user's
  `mock.html` on 2026-11-18 ("status pill"). The same pill says a short thing that happened, a glyph (15 px) and
  words in 15 px Text Primary, as wide as they are, from the same x 50, or where the slots leave too little ending 8 px
  short of them and no nearer the left than x 24 (`vol_pill.c` `status_pill_show`): [adb](#adb)'s "MacBook-Pro allowed". It
  shows on Now Playing too, as it is no volume.
- A tap on the battery (a 44 x 40 px target round its body, or round the percentage on Percent, so
  at least 40 px round it; where it meets the level bars' target the nearer center has it) toasts
  its state; see [Dialogs and toasts](#dialogs-and-toasts).

### Level bars

The playing song's three level bars (the status row's play state, Home's first row, Up Next's header) are one
motion wherever they show (`ui_kit.h` `lvl_*`): each bar passes through its column of the status row's ten approved
frames (`mock_status_bars.py`: offset sines, a 1.25 s loop) every 125 ms, on a smooth curve between them, repainted
30 times a second, so it rises and falls continuously instead of stepping between a few heights (the user's,
2026-11-17 08:35: "smoother fps"). A height between two pixels shows its top row part-lit. Each screen keeps its own
size (the status row's 2 px bars 3 to 11 px, Home's and Up Next's 3 px bars 5 to 12 px). Only the bars' own box
repaints. Paused, they hold where they stopped and go on from there; under Reduce Motion they stand still.

### Home's first row

Approved 2026-11-16 (`design/home-np-icon`, 2a and the morph; `patch/home_np.c`):

- Stock's Now Playing row reads **Playing** while something plays, **Paused** while it is paused and **Guest Play**
  while a guest plays (its click opens the guest's Now Playing); with nothing queued and no guest it is not there.
  Edit Home lists it as Playing.
- It leads with the play state at the text column (x 28 in Split), kept in the status row too (the user's,
  2026-11-26): Up Next's [level bars](#level-bars) (3 px wide, 4.5 px apart, 5 to 12 px) in the accent while
  playing, a pause glyph (Phosphor `pause`'s proportions: two 4 x 12 px bars, 2 px apart) in Text Secondary while
  paused, either white on the selection bar; a guest's by the guest's state. Only this row's words move, 18 px on
  (x 46); the other rows keep theirs. The bars stand still under Reduce Motion.
- Between playing and paused the glyph morphs over 250 ms, cubic out: the bars freeze, the middle one narrows and
  fades, the outer two widen and rise into the pause's bars, the accent turning Text Secondary; on resume the same
  backwards, then the bars move again. The words cross-fade in the same 250 ms. Under Reduce Motion both are a cut.

### Home's art pane while Radio plays

While Radio is Now Playing (playing or paused; `patch/radio_pane.c`, `coverflow.c` `SHOW_RADIO`), Split's art pane
is Radio's, edge to edge where the cover goes: design K1, approved 2026-11-20
(`~/q2-emu/design/radio-tuner/radio-now-playing/pane-v6`: `mock.py` holds every number, `sheet.png` the still,
`K1-rings.mp4` the motion; built: `pane-v6/built/`).

- **Ground:** dark red (20, 3, 7) at the top, 35% darker at the bottom; a glow of (88, 7, 22) behind the badge,
  t = clamp(1 - r / 150)^2.2 x 0.45; a faint lighter line (+30, 7, 11) down the pane's left edge.
- **Badge:** a 64 px circle centred at screen y 138 (the badge and the frequency centred together), a vertical
  gradient (214, 46, 76) to (122, 10, 32) with a soft gloss high on it, the broadcast tower white at 39 px (62%;
  the Car Remote app's ic_radio, `tools/art.py` `RADIO_TOWER`).
- **Frequency only:** "93.1" in Inter Medium 30 white and "FM" in 15 Text Secondary 3 px after it, centred, its
  baseline 70 px below the badge's centre, over a soft vignette (an ellipse 124 x 46, black 210/255, blurred 12 px).
  No station name, song, call sign or city.
- **Rings:** four, from the badge's centre, radius 40 to 170 as they travel, alpha 0.55 x (1 - u)^1.6, 1.2 to 2.8 px
  wide, (205, 38, 66), with a 5 px bloom; one leaves every 2.4 s (each travels 9.6 s) while Radio plays and the pane
  shows, the pane alone repainted every 33 ms; still while paused, and under Reduce Motion.
- **The way in** (pane-v3 `anim.py`, approved as "Apple-like slide"): the cover recedes to 0.92, dims and drifts
  18 px left over 350 ms while the pane slides in from the right on a damped spring (settling about 0.4 s, a slight
  overshoot); the glow blooms from 180 ms over 500 ms; the badge from 300 ms, then the frequency from 380 ms, rise
  8 px and fade in over 300 ms. Under Reduce Motion the pane is there at once.
- **Light Mode:** the same; the pane is art, as the cover it replaces is, which Light Mode leaves as it is.
- The covers come back when the Library takes over.

### Selection bar

- Accent Blue, flat (raised at rest, see Press below), the full row pitch tall, inset 16 px each side on every row, 8 px corners,
  (`list.selection.inset`, `list.selection.radius`).
- Text and chevron on it are On Accent, the second line too. Nothing else on the row changes (no
  bold, no indent).
- It moves one row per wheel tick, with no easing longer than the theme's list motion.
- **Tug to tilt** (approved on 2026-11-15 from `design/mockups/tilt-menu.html`; `patch/tilt.c`): while a finger
  turns the wheel short of its next tick, the bar leans toward the row it is heading for by the share of the
  tick turned, eased 1 - (1 - a)^2: up to 3 px that way, up to 3.5% larger, an accent shadow under it, and a
  white gleam (0.42 at its center, from 0.15 to full strength over the lean) sliding the other way. The tick
  is unchanged: past it the bar steps a row and the next tug starts from the leftover turn. Let go short of
  it and the bar springs back over 220 ms. The mock-up's 1 degree tilt is under a pixel on a 32 px row and is
  not drawn. A touch drag scrolls the list with the bar hidden, as touch always does, so it does not tilt.
- **A list's end is its end** (approved on 2026-11-16): the wheel stops on the first and last rows, never wrapping
  to the other end and never bouncing. Turning on past an end, the bar's tug holds full against it, the
  selection staying, and springs back when the finger lifts. A drag stops at the ends too, with no rubber band.
  The lean is never cut at the list's edge: on the first or last row it reaches up under the status row's
  glyphs and down into the fade.
- **Press** (approved on 2026-11-16 from `design/row-press`, option C; [internals](internals.md#row-press)): the bar
  sits raised at 1.02 with a soft accent shadow; Centre's press sinks it to 0.965 over 90 ms and brightens its
  blue to `#4080F0`; the release springs it back past 1.02 once over 280 ms and the blue fades back over 120 ms. A
  press during a tug wins; the lean goes. Reduce Motion: the brighter blue only, no scale and no shadow.
- A list left by Return opens at its top when it is chosen again; Return lands on the row the
  page behind was left at (`list.reopen.top`).
- Exactly one per screen. A dialog over a list takes the selection; the list's bar is hidden
  while the dialog is open.

### List row

- 32 px pitch, title at the text inset, a chevron on the right column when the row opens
  another page, nothing at the right when it acts in place (a song, a playlist in Add to Playlist, Reset Settings'
  rows), or opens only a confirmation card.
- **Chevron:** Phosphor `caret-right`, bold, 15 px, centered 5 px left of the right column:
  Chevron gray, On Accent on the selection.
- **Opening:** a row whose page has to load first (Home's Cover Flow, on its first open after a
  restart, a library change or a new Sort) keeps the selection and turns its chevron into an activity
  spinner: eight spokes in the chevron's 15 px box, On Accent, the leading spoke solid and each
  one behind it 10% fainter, a spoke on every 100 ms (redrawn between the open's steps, so it can
  pause on a long one), until the page opens. It shows only once the
  open has taken 75 ms, as one that has will pass 150 ms, so a quick open never flickers;
  meanwhile the wheel and buttons do nothing but Return, which stays on Home.
- **Key/value row:** value in Text Secondary, right-aligned on the right column, no chevron.
  On the selection bar the value is On Accent.
- <a id="labelvalue-rows"></a>**Label/value rows** (About, a settings row's value; the user's, 2026-11-16,
  from About's Wi-Fi Address): the label keeps its natural width and the value takes the rest of the
  row, at least 16 px (`KV_GAP`) after the label's text. A value wider than its box is not squeezed
  against the label or cut hard: it fades at its cut end at rest and scrolls when its row is selected
  (the house [marquee](#motion)). A label so long the value would get less than 96 px (`KV_VALUE_MIN`,
  or the value's own width when shorter) shrinks to leave it that, and scrolls the same way
  (`patch/ui_kit.c` `kv_fit`, `patch/settings_pages.c` `kv_row`).
- **Switch row:** every on/off setting, a 40x24 fully round switch ending on the right column, its
  20 px white knob 2 px in: Track when off, Accent Blue when on. A flip slides the knob over 200 ms,
  easing out, the track's color crossing with it; flipped again mid-slide it turns back from where it
  is. One painter draws every switch (`patch/list_marks.c` `switch_mark`), stock's image switches too,
  whose art is emptied and drawn over, so they all move alike; a switch appearing (a page opening, a
  list scrolling) stands still. On the selection bar: on is a white
  track with an Accent Blue knob, off a 1.5 px white outline round the white knob. No "On"/"Off"
  text beside it. A setting with two or three named choices (Gain's High and Low, Charge Limit's
  Off and 80%) is a key/value row that Center cycles; the value is never part of the label.
- **Captioned row** (Smart Wi-Fi's and the other rows `pod_caption` names; approved as S3 on 2026-11-15, `mock_radio.py` `caption_rows('reveal')`):
  the caption shows only under the selected row. That row grows over 150 ms, its title and switch where
  they were, to hold the caption under them inside the bar: Inter 13 px in `#D6E2FA`, 17 px lines from the
  title's column, wrapped at words to the right column, 5 px under the last; every other captioned row is one
  row high. The list scrolls by whole rows to keep the grown row's foot on or above the list end, y 282 (`patch/settings_pages.c`).
  Every caption but Smart Wi-Fi's is one line there, under 327 px of Inter 13 (the bar's 343 less its 8 px insets).
- **Disabled row** (Set Time Manually while the time is set automatically): label, value and
  chevron in Text Tertiary, and the wheel passes over it.
- **Picker:** the current choice carries Phosphor's `check` (bold, 12 px) on the right column, Accent
  Blue, white on the selection.

### Scroll indicator

Wear OS's position indicator, on the Q2's straight right edge:

- A 4 px track with round ends at x 366 (5 px from the glass, 7 px clear of the selection bar),
  a fixed 72 px long, centered on the list's middle (y 134 to 206). Track `#3A3A3A`, thumb
  `#E5E5E5` inside it, the thumb's position and length the visible rows over the total.
- Hidden at rest: it shows while the wheel turns and fades out a second after the last tick
  (the last 200 ms of it). It stays hidden while the [A-Z index](#a-z-index) shows.

### A-Z index

An iOS section index that is not there at rest (the user's option B from `mock_az_index.py`,
2026-11-15), on Library lists in name order with more than 50 rows: Songs under Title (and the
other song lists its page shows in name order: Favorites, Hi-Res, a playlist sorted by name),
Artists, Albums, Composers and Genres. Lists in any other order (Recently Added, Songs by artist
or album, an album's tracks, a playlist by time added, an artist's or a genre's songs) and
settings have none. The rule is one opt-in list of name-sorted Library lists (`az_index.c`
`name_sorted`), shared with the fast-scroll letter: Wi-Fi's networks, Bluetooth's devices, Up Next,
Most Played, Battery History and card apps are in their own order and show neither.

- **Shown by** a finger on the list's right edge (its last 28 px), or the wheel spun fast (300
  degrees a second or more, the speed at which Rockbox's list acceleration first moves two rows a
  click, as the volume's does). It fades out a second after the finger lifts or the last fast
  tick (the last 200 ms of it).
- **The column:** centered 13 px from the right edge (x 362), from y 80 to y 240, the straight
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
  a list a Home row opens), or a finger held as long on a row, which then drags that
  row. Center held again, or Return when no row is lifted, saves and leaves.
- **In it** every row the user can show is
  listed, each row's chevron gives way to a grip (three 18x2 px bars 5 px apart, centered where the
  chevron was) in Text Tertiary, On Accent on the selection, and a hint, "Drag ≡ to move ·
  Play/Pause shows or hides", Inter Regular 13 px in Text Tertiary, is centered 14 px above the
  screen's foot (its middle at y 298), the grip drawn as its glyph. The list ends a row higher
  than other lists, on y 250, so the hint has the room under the rows that a list has above the
  screen's foot: the rows past that end peek through the same fade, into the ground by y 284, and a
  scrolled list's top row fades as every list's does, circles and grips with their rows (the user,
  2026-11-16: no row against an edge, and the fade at whichever end has more to scroll). Six rows
  show, so Edit Home's seven scroll. Home in Split is laid out as Full while the editor is open.
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
- **The wheel:** Center lifts the selected row: nothing of it moves (the user, 2026-11-16: no
  layout shift entering or leaving the drag), only its look changes. Its words in Inter Medium at
  the row's 20 px, white, with a white grip and its circle as the selection's, each where it stood,
  on a raised `#2C2C2E` card in the selection bar's box (x 16 to 359, inside the side margins) and
  34 px tall about the row's middle, with 9 px corners, a 1 px `#141414` ring and a soft black
  shadow 6 px down. The wheel moves it a row a click, stopping at either end; Center
  puts it down and saves. Return while it is lifted puts it back exactly where it was, still in the
  editor.
- **A finger** on a row's grip (the last 64 px) drags it at once; held on a row it drags it after
  the hold. The row follows the finger on its card, held inside the list (its rest, y 58 to the list's
  end), never over the status row or the hint; the rows it passes slide aside over 150 ms (cubic
  out); within 16 px of the list's top or bottom the list scrolls on. The lift puts it in
  its place and saves. A tap selects a row and opens nothing.
- **The order** is kept through a restart and a power cut. A row the editor doesn't list (the
  Library's My Fav and Frequent, a media row without its folder) keeps its place; a row a later
  update adds appears at its default place without disturbing the rest.

### Tabs

An iOS segmented control (`slider_page.c` `segments_paint`), on EQ's Simple and Advanced (`eq_tabs_paint`) and
Search's All, Songs, Artists and Albums ([ipod](ipod.md#search)), the theme's only tabs:

- A `#1C1C1E` track rounded 9 px: on EQ from x 20 to 355, 34 px tall, centered in the 48 px tab bar; on Search
  from x 16 to 359, 30 px tall, in the results' first 32 px row.
- The open tab a segment 2 px inside the track, rounded 8 px: raised `#3A3A3C` while the wheel is
  elsewhere, Accent Blue only while the wheel is on the tabs, so the open tab reads apart from the
  wheel's place. In Light Mode the track is `#E5E5EA` and the raised segment white.
- **The wheel** reaches the tabs past the first row (or control) under them. On them it moves between the tabs, each
  opening as the wheel reaches it, left to right going down; a turn down past the last tab, or Center, goes into the
  open tab's rows, and a turn up past the first stays (EQ) or goes up to what is above (Search's query line, which only lights as a selection; Center there opens the strip). Previous
  and Next open the tab before and after, round (`peq_ui.c` `eq_key`, `search_strip.c` `search_strip_key`).
- Labels in Inter Medium 17 px, centered in their segments: the open one white (Text Primary on Light
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
  app's, `addon.c` `stack_rows`). Vamp's own two-line lists (Era, View Queue, Most Played,
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
  The wheel reaches it from the first song; Center shuffles the list, and Return from Now Playing
  lands on it again. Sort has no place on the row: Songs' order is Settings > Playback > Library > Sorting's
  Songs ([ipod: Settings](ipod.md#settings)). An album's page has Play and Shuffle above its tracks instead
  ([Album page](#album-page)).
- **Folder view** reads as an album: the same 32 px rows. A song file is a track, its number in the
  album's column, the title from the library's tags (found by the file's path) at x 50 and its
  length on the right column when known; a file the library doesn't know shows its file name
  without the extension, a leading track number ("01. ") or the "Artist - " every such name in the
  folder starts with. A folder has Phosphor `folder` (regular, 20 px, Text Secondary, white on the
  selection) centered on x 34, its name at x 52 and the chevron on the right column; the card's root
  too.
- Which list is which comes from its page: Songs' page (`allmusic_page`, which also holds a genre's songs
  and the other lists stock opens there) is mixed, the artist page's songs (OLED draws that page as the [artist page](#artist-page)) are an artist's, the album page is an
  album, Folder view (`folder_page`) a folder, and an album's tracks opened from Cover Flow are an album too (their
  status row names no page and there is no row above the tracks naming it). Lists Vamp draws itself (Most Played,
  Long Songs, Ratings) keep their own rows.
- Streaming's rows are one line, 32 px, each service's mark (20 px, monochrome, no disc) centered on x 34 in Text
  Secondary, white on the selection, its name from x 52: Phosphor's `spotify-logo`, `app-window` (a card app without
  its own icon), `radio`, `airplay` and `sliders-horizontal` (Configuration), TIDAL's own four diamonds, drawn in
  `tools/art.py` (Phosphor has none), TIDAL and Tidal Downloads sharing it, and for the Subsonic card app (id
  `subsonic`, with or without its own icon files) q2-coda's submarine with two sonar arcs. Any other card app with its
  own icon shows that icon scaled into the same 20 px box, grayed as every card app icon is under this theme
  (`icons.addon_mono`) rather than inked: a tint would turn an icon drawn on a filled tile into a solid square.
- **Where a song lives** (with a server's app signed in): a song row's right column says where the song is, as the
  user approved from `mock_radio.py`'s storage sheet on 2026-11-15: nothing for the user's own file, Phosphor
  `arrow-circle-down` for one a card app downloaded (its `q2app`'s `downloads=` folder, q2-subsonic's `Subsonic/`),
  `cloud` for a server song that streams; regular, 18 px, Text Tertiary, white on the selection, centered where the
  offline mark goes. A server song that can't play now keeps the grayed row and `cloud-slash`
  ([server-library.md](server-library.md#grayed-rows)). The length and the title end 12 px short of a mark, as they
  do of the offline mark.
- Ratings' lists show a song's stars as five Phosphor glyphs at 14 px at the start of its second line, `star-fill`
  for its stars and `star` for the rest, in Text Primary (white on the selection), no colour, then "· Artist".
  A song with no stars (one only liked, in Loved) has none, and its words start where the line does.
- **The playing song's row**, as the user approved from its mock-up on 2026-11-18 ("B. A where constrained"),
  in every song list (Songs and a genre's, an artist's, an album's tracks and Cover Flow's, Folder view, a playlist's
  songs, Search's results, Most Played, Long Songs, the Ratings lists, the card's songs; Up Next already heads with
  it): the song playing or paused, known by its path and CUE track (a server song by its row, the copy playing it
  mapped back), never its title. Its title is Accent Blue, and four bars stand on the right: Up Next's level bars'
  shape (3 px wide, 4.5 px apart), 8, 12, 5 and 10 px high, centred on the row, Accent Blue, still (no list repaints
  for them). **B:** where the row has a length, the bars stand SONG_COL_GAP (12 px) before it and the title ends 12 px
  before them. **A:** where it has none (a mixed list, a two-line row), or the title would keep under 80 px beside
  both, the bars take the length's place on the right column. On the selection the title and bars are white. A
  playlist's or a cover row's right-hand text moves left of the bars, as a length does. Centre or a tap on the row
  opens Now Playing and the song plays on, never sent to the player again ([internals](internals.md), a pick of the
  playing file).

### Now Playing

The most-seen screen. Art left, words right, time across the bottom; the status row shows the
clock, centered.

| Element        | Position (x, y) and size       | Notes                                                                                      |
| -------------- | ------------------------------ | ------------------------------------------------------------------------------------------ |
| Art            | 24, 58, 150x150                | Square, 6 px corners                                                                       |
| Song title     | 192, top 64                    | Inter Medium 22 px, Text Primary, one line, scrolls when long                              |
| Artist         | 192, top 96                    | Inter Regular 18 px, Text Secondary                                                        |
| Album          | 192, top 120                   | Inter Regular 18 px, Text Secondary, up to two lines                                       |
| Progress bar   | 24 to 347, y 228, 6 px         | Track `#333333`, fill Accent Blue, round ends                                              |
| Elapsed        | left on 24, centered on y 250   | 16 px, Text Primary, tabular                                                               |
| Remaining      | right on 347, centered on y 250 | 16 px, Text Primary, with a leading minus; a tap shows the length instead (Track Time)     |
| State row      | centered on y 286, x 53 to 322  | Shuffle, radio, repeat, Up Next: Phosphor bold 22 px, even gaps (below)                    |

No page title, no reflection, and no background art blur on the art's page (the lyrics and
[rating](#rating-page) pages have the art's glow).

Built as the `oled` set's `np_art` and `np_bar_y` (`tools/ipod.py` `np_grid`) with the theme's
`playing.art.radius`, `playing.artist.font`, `playing.scrubber.height` and `playing.modes.icons`.
The album is one line ending in an ellipsis (the title and artist scroll), rather than two, so the heart under it keeps its place. What the design
has no place for keeps a place of its own:

- **The heart** stands in the gap under the album (the user's, 2026-11-18, `heartmove` mock-up), drawn smaller and
  pink-red when on (the user's, 2026-11-19, `mock_np_heart.py`): 18 px of ink tall, its left at x 194, two pixels left
  of the text column, and its middle at y 164 (`tools/art.py` `np_heart`, in a 34 px image at x 193 to 227 and y 146
  to 180); off, Phosphor `heart` (bold) in Text Secondary; on, `heart-fill` in Favorite (`#FF375F`, both halves). The
  hold menu's Favorite rows keep the menu's own glyphs and colours. It is the like alone ([rating page](#rating-page)); its touch is x 182 to 238, y 142 to 184. The album is
  one line ending in an ellipsis, never scrolled, so the heart's place never moves.
- **More** stands at the right end of the art's bottom line (baseline 208), on the art's page, so a swipe takes it
  away with it: Phosphor `dots-three` (regular, 20 px, Text Primary; pressed in Text Secondary), its ink ending on
  the right column. Its touch reaches right to the screen's edge and down to the progress bar's target: x 292 to
  374, y 172 to 219. The badge row keeps the quality badge on the left and More on the right.
- **Touch areas** stay off the progress bar's target (y 220 to 242), the times' row and each other: the state row's
  buttons each reach to the midpoints with their neighbours and down to the screen's foot, Shuffle and Up Next
  from the row's top (x 0 to 105 and 270 to 374), Radio and Repeat under the row (x 106 to 187 and 188 to 269,
  y 308 to 319; radio on: 112 to 205 and 206 to 281). What is drawn, its press square and its focus ring stay the
  glyph's.
- **The page dots** are hidden: the state row's on dots sit where they were. The lyrics and the
  [rating page](#rating-page) are the swipe's other pages, in that order; the lyrics end 5 px above
  the bar, and the rating page, which draws its own dots, rests full screen. Now Playing shows no place in the
  queue ("1 of 10").
- **The track's details** are not a page of the swipe: ⋯'s **About** row, last before Delete Song,
  opens them as a page titled About, an [info grid](#track-details), and Return goes back to ⋯.
- **The state row** is four buttons on stock's play mode button, `shuffle`, `broadcast` (radio),
  `repeat` and `queue`, the outer two's edges fixed at x 53 and 322 and the three gaps between them
  even. Shuffle and repeat are Text Secondary off; on, Accent Blue with a 4 px Accent Blue dot 2 px
  under the glyph (repeat-once's glyph for One). A tap on shuffle turns it on or off, a tap on
  repeat steps Off, All, One, and each keeps the other as it was. Stock's Shuffle never ends, so
  shuffle with repeat Off plays on as with All. Radio is the antenna in Text Secondary; while a
  radio plays (the playing app's, or the Q2's own) it is a 92 x 34 Accent Blue pill holding the antenna (18 px, white) and
  "Radio" (Inter Medium 16 px, white): turning on, the pill grows from the glyph's width over
  200 ms (ease out) while the gaps shrink, the word fading in over the last 40 %, and back the same
  way off. While radio is on, shuffle and repeat are `#444446` and ignore taps and the Center
  cycle, as the radio picks the order. A tap on the antenna starts a radio of the playing song
  ([internals: Radio](internals.md#radio)) and "Starting Radio…" while its app is asked; a tap on
  the pill stops it ("Radio Off"). The queue button, and a swipe up on the
  art's page, open [Up Next](#up-next); the hold menu's View Queue opens it too. The swipe up
  counts only when it comes down on the art's page, above the bar: never on the times or the state
  row, so a tap on Radio that rolls up as it lifts is no swipe (the user's, 2026-11-18).
- **The state row's touch** (the user's, 2026-11-18): a tap goes to the button nearest it across
  the row's target (x 53 to 322, y 264 to 308). The outer two reach further, touch only: shuffle's
  from the screen's left edge and Up Next's to its right, each from the row's top (y 264) down to the
  screen's foot, and across as far as their share of the row reaches, the midpoint with radio for
  shuffle and with repeat for Up Next, so neither reaches Radio, its pill or the swipe up. On
  screen, radio off: shuffle x 0 to 105, Up Next 270 to 374; radio on: shuffle 0 to 111, Up Next 282
  to 374 (the pill is 112 to 204). Their glyphs, press squares and focus ring stay where they are.
- **A state button's focus and press** (approved as 22 v4 on 2026-11-16, `audit2/proposed/v2/22v4-L5-np-state-row.png`):
  the Center cycle's ring outlines the control the wheel has moved it to (Shuffle, Repeat or the heart) in today's 2 px Text Primary stroke, 9 px corners, no fill;
  a tap puts a `#2C2C2E` square, 9 px corners, under the button and fades it out over 250 ms; the wheel never
  shows the square. Both fit what is drawn: the glyph's ink alone with no dot showing (Up Next, radio, and
  shuffle or repeat while off), the glyph and its on dot together while it shows, padded 6 px to the outline's
  inner edge (8 px to its outer) or 8 px to the square's edge on every side; an outline grows by the dot when its
  control turns on. The radio's pill is its own press.
- **A server song loading** (approved as streaming-v3's variant D, `design/streaming-states/`): while the song waits
  to start, or playback has caught up with its download, a 14 px 12-spoke spinner (Text Secondary, a turn a second,
  turning under Reduce Motion too, as it reports progress) and "7.0 of 20 MB" (Inter 13 px, Text Secondary) sit
  centred in the times row, 6 px apart, between the elapsed and the remaining, which both stay. Nothing dims, the bar
  does not animate, no "Buffering…"; it goes once the song plays on (`np_stream.c`).
- **A server song's badge** (streaming-v3): at the art's bottom line, from the title's column (x 192, bottom 208), on the
  art's page so a swipe takes it with the heart and More: a 20 px badge with 6 px corners and a 1 px Text Secondary
  outline round the file's format ("FLAC", Inter Medium 13 px, Text Secondary) alone, 9 px in and 9 px past its end:
  no glyph (the user's, 2026-11-19, "remove cloud icon from FLAC"; it was a cloud, or a crossed-out cloud while the
  server was out of reach, which the status row's offline glyph and the message below still say). A card file has
  none.
- **A server song that can't go on** (streaming-v1 frame 5, as v3 keeps it: art never dimmed; the reason in place,
  sheet 5 option A, signed off 2026-11-19): while its app says the server can't be reached (`server=down`, or
  `reach=down <code>`) and the song is the pressed one or one the player waits for, the times row's centre first reads
  why beside the spinner while the app tries again, "Server error 502 · trying again" (Inter 13 px, Text Secondary;
  "Server not answering", "Server not found", else "Can't reach the server", the words of the
  [server toast](#dialogs-and-toasts)), for 10 s; then why alone in the spinner's place, and the state row is the empty states'
  capsule pair, Retry (selected) and Skip ([buttons](#buttons)). Nothing is skipped without a word. A tap takes either; Centre at rest is Retry; the
  wheel stays the volume's. Retry asks the app to try again now; Skip is Next.
- **The volume** and the scrub draw on the bar where it is. The volume (the "A: iPod speakers"
  mock-up) keeps the bar's rows, inset to x 48 to 323, white on the track, with Phosphor's
  `speaker-simple-none` and `speaker-simple-high` (fill, 16 px, Text Secondary) centered at x 32
  and 339 on its center line, and no number; the times' row stays blank meanwhile. The scrub is
  a 14 px white knob on the fill's end. The A-B markers sit on the bar,
  centered on it (`ringnav_ab_move`, which stock's placement (`0x53baa4`) calls for its y in place
  of widget_move), at the same share of its width as the point is of the song (`ringnav_ab_x`).

### Rating page

Now Playing's third page, a swipe past the lyrics (approved by the user on 2026-11-18, "stars looks good,
ship it": the `apple` mock-up), `patch/np_rate.c`. At rest it is full screen, the bar, times and state
row gone; while a swipe moves it, it slides in the pages' column. Nothing on it but these, centred:

| Element    | Position (x, y) and size                    | Notes                                                                                   |
| ---------- | ------------------------------------------- | --------------------------------------------------------------------------------------- |
| Title      | centred, line top 64                        | Inter Medium 19 px (AWTK 23), Text Primary, one line, an ellipsis when long              |
| Artist     | centred, 3 px under the title               | Inter 15 px (AWTK 18), Text Secondary                                                    |
| Stars      | five 44 px, 14 px apart, centred, top 128   | Phosphor `star-fill` in Text Primary up to the rating, `star` outlines at 30% for the rest |
| Target     | 52 px (1.18 times) on its star's centre      | the wheel's: filled if within the rating, else a full Text Primary outline              |
| Page dots  | 6 px, 7 px apart, centred, top 292          | one a page, this one Text Primary, the others at 30%                                    |

No words under the stars, no hint, no progress bar, and no colour but the glow's. The page is [full-bleed](#full-bleed):
the glow runs to the screen's top edge under the status row. The mock-up has the title at 46; it stays at 64, on Now
Playing's own title line.

- **Rating** is 0 to 5 whole stars (Subsonic's `setRating` is 0 to 5), apart from the like: the heart on the
  art's page sets no stars and five stars like nothing. This page is the one place to rate (the user, 2026-11-18:
  "the screen serves as the ratings screen. heart is only for liking a song"). The wheel moves the target from
  the rating's top (a tick from 3 is 4 or 2), within the five, and it goes back to rest after 4 s without a
  turn; Centre keeps it. A tap keeps a star at once. The rating's own top star, tapped or kept by Centre, clears it (unrated): the page's "0". Centre with no
  target puts it on the rating's top star, the first when unrated. Nothing that can be rated (a stream, a
  radio) leaves the wheel on the volume.
- **The glow** (also behind the lyrics page): the cover averaged on a 4 x 4 grid, each cell's colour raised 1.4
  times about its luma and dimmed to 45%, spread across the whole screen and shaded by the mock-up's own wash
  (the approved `apple` mock-up, 2026-11-18): an ellipse centred at x 187, y 112 (radii 273 by 220 px), its
  brightness the mock-up's measured falloff (`patch/np_rate.c` `nr_fall`), so a cover at the cap reads as the
  mock-up does: down x 60 about 17 at the top edge, 44 at its peak (y 100 to 120), 26 at y 200, black by
  y 300; it is dark only at the screen's corners. It runs under the status row to the top edge on both
  pages: full screen from the page itself, and on the lyrics page in its column from Now Playing's window
  above and below the column (behind the progress bar and the state row), which draws the glow's rows over
  the glow pages and moves them with a swipe, so there is no seam at the page's edges. No channel passes 60, so Text Secondary (`#ABABAB`, the lyrics' unsung lines)
  keeps WCAG's 4.5:1 over its brightest pixel, and the lyrics' edge fades go into the glow, not black. It is
  made once for each cover shown, never per frame; Light Mode has none.

### Up Next

The queue from the song playing on (`patch/upnext.c`), in the [menu sheet](#menus) (the user's,
2026-11-16, `design/menu-overlay/upnext`): it rises over the page it opened from as a menu does,
whichever way it is opened (Now Playing's queue button, a swipe up on the cover, the hold menu's
View Queue). Its rows are its own, not the menus': 46 px, the cover 36 px square at the text column
(4 px corners), the title (Inter Regular 17 px, Text Primary) over the artist (13 px, Text
Tertiary), 46 px right of the text column. The list runs edge to edge to the foot and fades there,
as a menu's; a lit row's foot never passes y 282.

- **The playing song** is the sheet's header and takes no selection: its cover, its title with
  three Accent Blue [level bars](#level-bars) (3 px wide, 4.5 px apart, 5 to 12 px high) 7 px after it, moving
  while it plays and still while paused, and its artist and album
  ("Pink Floyd · The Dark Side of the Moon").
- **Under a shuffle, what played** stands above the header, a row each as the rest of the queue's,
  the latest right above it (at most 100): the sheet opens with the header at the list's top and
  scrolls up into them. Center on one plays it again.
- **Playing Next** (Inter Medium 13 px, Text Secondary) heads the songs queued with Play Next, Play
  Last and the bump, with a **Clear** pill on its right, 22 px high, ending at x 359 with the
  selection bar: `#2C2C2E` with `#7FA6F5` Inter Medium 15 px at rest (Light Mode `#E5E5EA`, the tab
  track's gray, with Accent Blue words: the dark pill does not flip well), Accent Blue with white
  when the wheel is on it. It is a stop of its own; Center on it empties Playing Next, no confirm.
- **From** the list, album or page the queue was played from ("Next in Queue" when that is not
  known) heads the rest of the queue in play order.
- A row's title and artist are the song's tags; a song streamed from a server, not yet played, has
  its server's title and artist (or its download's tags), not its download's file name
  ([internals: Card apps](internals.md#card-apps), Tags).
- **A song still to download** (a radio's, or an album streamed, past the songs Download Ahead has
  fetched) follows the queue's songs: its title in Text Secondary and the server row's cloud on the
  right column (white on the selection). Center on it moves to it as Next does: Now Playing shows it
  with the loading spinner and it plays once enough of it is there; nothing refuses.
- **The bump arrow**, Phosphor `arrow-up` (regular, 16 px, Text Tertiary; white on the selection),
  stands on the right column of each queued song but the first. A tap on it, or Play/Pause held on
  the row, moves the song to the top of Playing Next, where a 3 px Accent Blue edge marks it for
  0.7 s. The rest of the queue has no arrow and Play/Pause does nothing on it.
- **Moving a queued song**: a lifted song sits on a `#2C2C2E` card with 10 px corners, its title
  in Inter Medium 18 px; Playing Next's songs show a grip (three 18 x 2 px bars) on the right
  column in place of their arrows; the wheel carries it, Center puts it down, Return puts it back
  where it was. **Move**, in a Playing Next song's menu, lifts it, the word Edit Library's hint uses
  for its lift ("Drag ≡ to move"), and its symbol the lift's grip; it stands last before the gap
  and Remove from Queue, as a menu's destructive row is last after a gap. Center held lifts a
  queued song itself only when the hold opens no menu (the second press of a double press).
- **A song's menu**: Center held half a second on any song row (Playing Next's, the rest of the
  queue's, a shuffle's played songs above the playing one, a song still to download), or a finger
  held on one, opens the song's hold menu over the sheet (Play Next, Play Last, Add to Playlist,
  Favorite, the Go to rows), as on every song list, with **Remove from Queue** last, in red: the
  song leaves the queue and the list closes up. A song still to download has Play, Sync Offline
  and the Go to rows alone. Center held on Clear does nothing, and never brings Shut Down
  ([internals](internals.md#centre-hold-menu)).
- Nothing toasts. Center on a song plays it and closes Up Next at once; Return lets the sheet fall
  away. The list follows the queue as songs end, the header with it.
- **A finger closes it too** (the user's, 2026-11-18): a tap on the dimmed page above the sheet
  (over y 40), or a drag down of 48 px or more that starts more down than across (the swipe up that
  opens it, reversed), on the grab bar's strip or on the rows while they are at their top. The
  sheet follows the finger down as it drags, the page behind coming forward as it goes; let go 48 px
  or more down, however far across a thumb's slant has carried it (2026-11-21), and it falls away from there as on Return ([Overlay motion](#overlay-motion)), short
  of that it rises back. A drag on rows scrolled down scrolls them. Under Reduce Motion the sheet
  stays put while the finger drags and the lift alone decides.

### Progress and sliders

- **Progress:** 6 px, round ends, Track and Accent Blue. Used for playback, scans and
  downloads.
- **Slider page** (Volume, Resume Songs, Night Shift, Wheel Sensitivity; stock's Max Volume, Default Volume, Channel
  Balance and Brightness pages keep the form where they still open): a feature with a switch has it as the
  first row, selected; the value in Inter Medium 22 centered on y 150; a 6 px track from the text
  column to the right column with its top at y 186, Track behind and Accent Blue filled to a 14 px
  white knob (Channel Balance fills from the center; in Light Mode, where white is the canvas, the
  knob is edged 1 px in Track, as iOS edges its white thumb); the range's ends in 15 px Text
  Tertiary centered on y 214, at the two columns. The wheel moves it; there are no +/- buttons. Center
  turns the switch on or off where there is one, else goes back; Return goes back.
  Night Shift's first row is a key/value row, its value Off, On or Auto, which Center cycles (three
  named choices: [List row](#list-row)); the value is the warmth ("3400 K") in every mode, and while
  Off it, the track and the knob are Text Tertiary with no accent, as a disabled row's (the user's,
  from audit2 J7).
- <a id="inline-sliders"></a>**Inline slider rows** (Playback's Max Volume, Balance and Default
  Volume's Level, Display's Brightness; the user's, 2026-11-16, from `design/mockups/mock_inline_slider.py`): a setting
  with one value changes in its list, not on a slider page. At rest the row holds its title, a 4 px track from x 190 to
  62 px short of the right column (Track behind, Accent Blue filled to a 12 px white knob; on the selection bar the track
  is `#7DA2EA` and the fill white) and its value in 15 px Text Secondary (Caption On Accent on the bar) ending on the
  right column; no chevron. Center or a tap grabs it: the row grows to 64 px over 150 ms, as a captioned row grows (at
  once under Reduce Motion), its value in Inter Medium 20 on the right column and a full-width track 10 px in from the
  two columns centered 43 px down with a 16 px knob; Balance grows to 82 px for Left and Right in 13 px under its
  track, and its track has a 12 px center mark and no fill. The wheel sets the value live at the wheel's speed, each
  step set and saved as stock's page for it sets a step; Center or Return sets it down, as does the selection moving
  off it. A setting with a switch and a value (Default Volume) is a switch row with its slider row under it,
  shown only while the switch is on (`patch/inline_slider.c`).
- **Vertical slider (EQ):** 4 px Track line, Accent Blue fill from 0 dB to the value (Text Tertiary
  while the EQ is off), a 14 px white knob, ringed 2 px in Accent Blue while the wheel is on it
  (1 px in Track otherwise in Light Mode, as the slider page's). The band's name and gain under it
  in 15 px Text Tertiary, Text Primary on the band the wheel is on. Above the three, the EQ's
  switch (Enabled), Presets and Reset are rows; while an Advanced preset plays, its name is
  Presets' value, as a settings row's, and the switch keeps its title.
- **Storage bar** (Settings > Storage, approved from `mock_radio.py`'s storage sheet on 2026-11-15): "33.9 GB of 60
  GB used" in 15 px Text Secondary centered on y 62 at the text column, then a 10 px bar with round ends from the
  text column to the right column, its top at y 76: the track `#2C2C2E`, then from the left Your Music in white,
  Downloads in Accent Blue and Stream Cache in `#6E6E73`, square where they meet with 1 px of track between them.
  The rows from y 100 are its legend: a 10 px dot in the segment's color on the text column (Free's in the track's),
  the label at x 44 and the size as the row's value, Downloads and Stream Cache with a chevron. Two footers follow.
- **Footer:** a grouped list's caption under its last row, as iOS's: 13 px Text Tertiary (`#858585`) on the text
  column, its first line centered 10 px below the rows, the next 16 px lower. Storage and Downloads use them.

### Confirmation

Shown for a moment after a mode change (Shuffle, Repeat).

- Centered glyph (Phosphor `shuffle` or `repeat`, bold), 56 px, Accent Blue, centered on y 140;
  label Inter Medium 22 px centered on y 200; count in Text Tertiary 15 px centered on y 228.
  Nothing else on screen but the status row.
- Dismisses itself after 1.2 s or on any input, which it takes.
- Built for shuffles (`patch/feedback.c` `feedback_shuffled`): the Library's Shuffle Songs, Albums and
  Folders, a list's Shuffle row and the hold menu's Shuffle, over the Now Playing they open, with the
  queue's song count. Repeat and Now Playing's own mode button do not show it yet.

### Dialogs and toasts

- A question is the [confirmation card](#confirmation-card) over the page that asked. Stock's confirm
  (`confirminfo_dialog`: Shut Down, Restart, the three Resets, Delete Song, Delete File, Unpair, the Wi-Fi
  network's Forget and Disconnect, Line Out and the rest) is drawn as the card with no change to its callers
  (`feedback.c` `feedback_confirm_dress`): its prompt named as the design writes it ("Forget This Network?" for
  stock's "Delete Record?", "Use Line Out?" for its "Line Out sets max volume, continue?"), **Cancel** first, then the
  verb; it opens on Cancel, or on the verb for a question whose mistake costs least (Scan, Import, Export, Reconnect,
  Turn On). The verb says what it does ("Shut Down", "Reset Settings", "Delete"), never "OK"; a verb that loses something
  (Delete, Reset, Forget, Unpair, Remove, Clear) red after the gap. Delete Song's "Also delete source file" is the
  card's switch row. What the answer leaves alone, or why it asks (a caller's `feedback_confirm_foot`: Streaming
  Songs' "Show streaming songs?" and "Hide streaming songs?"), is the body.
- **Reset Settings** (approved as 30 v2 on 2026-11-16): its three rows have no chevron, and under the selected
  one a caption says what it takes; Center asks on the card, each body what stock's handler really does
  (`feedback.c` `asks[]`): Reset Settings Only, "Reset Settings?", "Forgets Wi-Fi, Bluetooth, EQ; music and
  playlists stay." (it deletes `config.ini`, the saved networks, the pairings, the EQ presets and the resume
  points); Reset Database, "Reset the Database?", "Erases playlists, Favorites; then restarts and rescans."
  (`database.db`); Full Factory Reset, "Erases settings, playlists and Favorites. Music stays." (both). Their
  verbs are Reset Settings, Reset Database and Reset All, red. Ratings (`ringnav-ratings`) and the card's music
  survive all three.
- A toast is one line of 16 px Text Primary on a `#1C1C1E` pill, 12 px corners, 32 px tall,
  as wide as its text and 16 px each side, centered on y 252 (y 236 to 268), above the bottom
  glass curve. It is the only raised surface in the theme. The page stays in sight round it,
  with its status row and its selection; it lasts 1.5 s (stock's own toasts their own time).
  One toast at a time: a new one, the Vamp's or stock's, takes the place of the one showing at once, the same pill
  with the new words and its own time, never a second pill over the first (`feedback.c` `toast_swap`, 2026-11-25).
  Find My Vamp's "Find My Vamp" stays up while its jingle plays, up to a minute (`findmy.c`).
  Words in sentence case ("Playing Last", "Name can't be empty"). A toast that says what holds for as long as it
  holds stays up as the same pill, painted by its page (`feedback.c` `toast_stay`), and takes no keys: Radio's
  "No Wi-Fi" while the link is down (2026-11-20).
- A toast with an icon keeps the 16 px each side and puts a 15 px glyph (Phosphor, fill) after the
  left one, then 8 px before the words: 16 + 15 + 8 + words + 16. Every icon toast is this one
  (`feedback.c` `toast_icon`: USB's, charging's), approved from `mock_batt_toasts.py` on
  2026-11-15.
- A card app's server error is the icon toast with Phosphor `cloud-slash` (fill) in Warning: "Server error 502"
  (the HTTP status, a proxy's for a server that is down or the server's own 5xx for a song it couldn't send), "Server
  not answering" (no answer, a timeout) or "Server not found" (a name that doesn't resolve), once as the server goes
  out of reach and once for each streamed song skipped or stopped at for it, never with Wi-Fi off. It stays at its
  usual place on Now Playing too (the user's, 2026-11-19: "stay put for 1.5s thats fine").
- A tap on the status row's battery toasts its state, the glyph in its color: "Charging · 62%"
  (lightning, green), "Charger too weak · 41%" (warning, orange), "Charge Limit Reached (80%)" and
  "Fully charged" (battery-full, green), "Low battery · 12%" under 20 % (battery-warning, red) and
  otherwise "Battery 62%" (battery-high, Text Primary). No time-left estimate.
- Every in-place action says what it did in a toast: the hold menu's Play Next ("Playing Next"),
  Play Last ("Playing Last"), Favorite ("Added to Favorites") and Unfavorite
  ("Removed from Favorites").

### Confirmation card

A question answered in place: a card over the page that asked, the page dimmed behind it (approved 2026-11-16 from
`design/confirm-overlay/`; `patch/feedback.c` `confirm`). It replaces the full-screen dialog page for every confirm
as its callers move onto it.

- **Surface:** the whole screen, status row too, under black at 50%; the card `#1C1C1E` (white in Light Mode), 291 px
  wide at x 42 with 16 px corners, its stack centered on y 168 and kept inside y 44 to 302.
- **Content**, 20 px in from each side: 18 px of padding; the title in Inter Medium 20, Text Primary, centered, naming
  the object ("Forget WH-1000XM5?", never "Are you sure"), a long one scrolling in the text column (the house
  marquee: held 2.75 s, then 35 px/s, a copy 40 px behind, its ends faded 12 px into the card), an optional 28 px
  glyph 6 px over it; the body in 15 px Text Secondary on 19 px lines, 4 px under it, one consequence in sentence
  case, wrapped at words to the text column on as many lines as it needs (four at most), the card growing with it; a code (a pairing's "482
  913") in Inter Medium 30; then 14 px down an optional switch row (label at the text column, the switch at the right
  column) and the answers, 32 px rows of Inter Regular 20 centered, stacked; 8 px of padding.
- **Kinds:** destructive (Cancel, then the verb in Negative red; opens on Cancel), consequential
  (Cancel, the verb; opens on Cancel), neutral (the safe answer, the verb; opens on the verb), info (OK). The lit
  answer's bar is the accent, 8 px in from the card's sides, or Negative red on a destructive verb. Every kind's
  answers stand 32 px apart, with no gap before a destructive one, as Shut Down's (the user's, 2026-11-19).
- **Red:** Negative `#FF453A`, the design's one red, not the mock's `#FF3B30` (iOS's light-mode red, darker on black).
- **Input:** the wheel moves the bar; Center answers the lit row or flips the switch;
  Return answers the safe row; Play/Pause, Next and Previous do nothing while the card asks.
- **Timeout:** a card that answers itself (Guest Play's 30 s Decline) shows the seconds left on that row in 15 px
  Text Tertiary and a 2 px bar along its foot emptying toward it, and answers it at 0 whatever the input.
- **Wide form** (`.across`, approved for [adb](#adb) on 2026-11-18): 339 px wide at x 18 with 26 px corners, hugging
  its content, centered on y 168: 16 px of padding, 18 px at the sides; the glyph (24 px) and 10 px before a leading
  title on one 24 px line; 6 px down the body, left-aligned 15 px on 19 px lines, the `name` (its `%s`) in Text Primary
  and the rest in Text Secondary; 2 px down a `.note` in 13 px Text Tertiary; 10 px down the switch row (34 px, 16 px
  words 4 px in from the text column); 10 px down the two answers side by side as 40 px buttons with round ends,
  10 px apart, 16 px words: the selected one the accent, the other Raised 2. It shows no countdown for its timeout.
  The answers are rows stacked inside the buttons' band (the list lays its rows one under another), and two buttons
  over the band, its left and right halves, take a tap.
- **For code:** `confirm(&(confirm_t){ .title, .name, .body, .code, .rows, .n_rows, .kind, .back, .toggle,
  .toggle_on, .timeout_ms, .timeout_row, .answer, .ctx, .glyph, .note, .across })` (`patch/ui_kit.h`, so every payload file reaches it); `answer(ctx, row, toggle_on)` runs
  once after the card has closed. One card at a time: `confirm` answers 0 while one is up.

### Pairing

A phone pairing with the Q2 asks in one form, whoever asks: Car Remote's app, a Guest Play guest or any device
pairing over Bluetooth by numeric comparison (`patch/pair_prompt.c` `pair_prompt`, the confirmation card's
consequential kind; the user's ruling, 2026-11-16; the Bluetooth device's card `patch/bt_pair.c`,
[internals](internals.md#bluetooth-pairing-card)).

- The phone glyph (Phosphor `device-mobile`, 28 px, the accent) over the title "Pair with Pixel 10 Pro?", the phone
  as it names itself, scrolling when long; never cut short in the status row. A card the glyph would take past the
  glass's foot (y 44 to 302: a guest's, with two body lines and three answers) goes without it and shows whole.
- What to do with the code, in the body: Car Remote's "Enter this code in the Vamp app"; a guest's "Wants to play
  music. Check it's on Alex's phone."; a Bluetooth device's "Make sure both codes match". A Bluetooth device whose
  name BlueZ does not have yet is "Pair with this device?" over "Unknown device" and its address, and the card
  changes to the name in place when it comes; one the request does not identify is "Unknown device" alone.
- The code large, Inter Medium 30, as two groups of three ("242 610").
- The answers: Car Remote's Cancel alone; a guest's Allow Once, Always Allow, Decline; a Bluetooth device's Cancel,
  Pair (the consequential kind's order: a device paired by mistake keeps its access). It opens on the first.
- The pairing's time as the card's timeout: Car Remote's 2 minutes, a guest's 30 s, a Bluetooth device's 60 s (BlueZ's
  own wait for an answer), a bar along its foot. A Bluetooth device's card also closes the moment the device gives
  up, cancels or goes; it never answers Pair by itself.
- Its end, Car Remote's: the icon toast "Pixel 10 Pro paired" (Positive's check-circle) or "Pairing failed" (`carremote_page.c` `carremote_paired`).
- The digits are tabular: the build maps Inter's 0 to 9 to its `.tf` figures (`tools/build.py` `subset_font`), so
  every code takes the same width.

### Focus ring

<a id="focus-ring"></a>The user's universal rule (2026-11-16), for every screen with one or two buttons (the empty
states' buttons, a card's answers) and any other mark that shows where the wheel is:

- **Hidden by default.** It shows as soon as the wheel turns on the screen, and on arrival when the screen was
  reached through the wheel (a turn, then Center); reached by a tap, it opens hidden.
- **The last input decides, everywhere at once:** a tap anywhere hides it again, a wheel turn shows it. There is one
  record of that, `st.touch_mode` (`patch/navigation.c`: a touch sets it, the wheel clears it), read through
  `ui_focus_shown()` (`patch/ui_kit.c`); nothing keeps a second.
- **Center with no ring** still does the screen's default: the primary button, a card's default answer.
- **Now Playing's Center cycle** follows it too: the ring on Shuffle, Repeat or the heart keeps its place, but its outline
  (`np_bar.c` `glyph_framed`, `heart_ring`) draws only while `ui_focus_shown()`.
- **Not under this rule:** Control Center's ring keeps its own form (it wakes on the first wheel tick or Center
  without acting, and goes 5 s after the last, `control_center.c` `cc_ring_on`), and the lists' selection bar, which
  boots shown. Control Center's wheel still counts as the wheel for the one record (`cc_wheel`), so a page its
  Center opens (the USB picker) shows its selection.

### Empty and offline

**The empty state** (`patch/feedback.c` `empty_state`) is the one form for every page with nothing to show (the
user's outline style, 2026-11-16, `empty-states-all/mock_empty_all_v2.py`, as Apple's ContentUnavailableView): the
page's Phosphor glyph in outline (regular weight, 60 px, Text Secondary; `tools/art.py` `EMPTY_GLYPHS`) with no tile
or disc behind it; 14 px under it the title in Title Case, Inter Medium 20, Text Primary; under it the hint in 15 px
Text Secondary; and at most its [buttons](#buttons), 20 px lower. The whole stack, with or without buttons, is
centered between the status row's foot (or the end of any rows the page keeps above it) and the screen's. The glyph
shows only when the empty state is the whole page (the user's rule, 2026-11-16): under anything the page keeps above
it (Car Remote's switch, Playlists' On-The-Go, Search's tabs) it is the **compact** form, the title, hint and any
button with no glyph, centered in the room left (`empty_compact`: a top past the set's bar). No app
names. No fake rows, and no Shuffle row or Add to Sync over nothing. A page that is a list (`empty_list`) keeps its
name for the status row; a Library list stock would open black (Albums, Artists, Composers, Genres) and a streaming
service with no link open a page of their own holding only the empty state (`empty_open`). With no SD card in, the
Library's pages say so (`empty_card`).

<a id="buttons"></a>**Buttons** (`mock_empty_cta.py`, the user's, 2026-11-16; `patch/ui_kit.c` `ui_cta_paint`): one
is a filled Accent Blue capsule 38 px tall, its label Inter Medium 17 in white, 24 px each side, at least 140 px; a
pair is two equal capsules 10 px apart (20 px each side, at least 120, the two within the 327 px text box), the second
gray (`#2C2C2E`, Light Mode `#E5E5EA`) with its label in Text Primary. Selected, a button wears a 2 px Text Primary
ring 3 px out, under the [focus rule](#focus-ring); pressed, its fill darkens a step (`#1A4DAD`; gray `#3A3A3C`,
`#D1D1D6`). The first is selected when the page opens, and the wheel reaches it as a row; it has no selection bar. A page with one button of either kind draws it with `ui_button` (Software Update's offer, the
library scan): **primary** (the screen's main action) is the accent capsule, **secondary** (a lesser action: Stop)
the gray one.

| Screen | Glyph | Title | Hint | Button |
| --- | --- | --- | --- | --- |
| Songs, an empty library | `music-notes` | No Songs | Copy music to the SD card | **Scan Music Now** (stock's button, `scan_row`) |
| Artists | `microphone-stage` | No Artists | Copy music to the SD card | |
| Albums | `vinyl-record` | No Albums | Copy music to the SD card | |
| Genres | `guitar` | No Genres | Songs need a genre tag | |
| Composers | `music-notes-simple` | No Composers | Songs need a composer tag | |
| Folders | `folder` | No Folders | Copy music to the SD card | |
| Cover Flow, no card music | `cards` | No Albums | Copy music to the SD card | |
| Cover Flow, a streaming app signed in | `cards` | No Albums | Streaming albums can show here | none: its albums join once its catalogue is in |
| Cover Flow, the card's music not scanned | `cards` | No Albums Yet | Music on the card isn't scanned | **Scan Music Now** |
| Playlists | none (compact) | No Playlists | Make one with New Playlist | none: under On-The-Go's row; New Playlist is in a song's hold menu (Add to Playlist…), not on this page. The picker keeps its rows alone |
| Favorites | `heart-slash` | No Favorites | Hold Play/Pause on a song to add it | |
| Recently Played | `clock-counter-clockwise` | Nothing Played Yet | Songs you play show up here | |
| Most Played (both lists) | `chart-bar` | No Plays Yet | Your most played songs show up here | |
| Recently Added | `sparkle` | No Songs Added Yet | New music on the card shows up here | |
| Long Songs | `timer` | No Long Songs | None over 10 minutes | |
| Years (Era) | `calendar-blank` | No Dated Albums Yet | Albums need a year tag | |
| Ratings' song lists | `star` | No Rated Songs Yet | Swipe to the stars on Now Playing | |
| Ratings' Loved | `heart-slash` | No Loved Songs Yet | Tap the heart on Now Playing | |
| Add Songs (a playlist's), a search matching nothing | none (compact) | No Matches | Try fewer letters | none: under the Search row, which opens the box again |
| A playlist with no songs | none (compact) | No Songs Yet | Choose Add Songs to fill it | none: under Add Songs, the row that fills it |
| On-The-Go with no songs | none (compact) | No Songs Yet | Hold Play/Pause on a song to add it | none: under Add Songs |
| Search, typed text matching nothing | `magnifying-glass` (compact under the tabs) | No Matches | Try fewer letters | none (painted in the free area above the strip) |
| Internet Radio, a station list with none | `radio` | No Stations | Add stations from your computer | |
| Streaming, a service with no link | `cloud-slash` | Not Connected | Turn on Wi-Fi to stream | **Turn on Wi-Fi** (opens Settings > Wi-Fi) |
| Library, no SD card | `sd-card` (Phosphor's `memory`) | No SD Card | Insert an SD card with music | |
| Car Remote, no phone paired | none (compact) | No Paired Phones | Open the Vamp app on your phone to pair | **Pair a Phone** (opens the how-to page), under the switch ([Car Remote](#car-remote)) |
| Downloads (Tidal Downloads, a card app's songs) | `download-simple` | No Downloads Yet | Synced music shows up here | |
| Chapters | `list-numbers` | No Chapters | This file has no chapter marks (or: This file's chapters couldn't be read) | |

Not in this form, by design: Now Playing is reachable only while something is loaded, so it has no empty screen;
the Wi-Fi page always holds its switch, Search Again, Add Network and Reset Network Settings rows; the EQ's Presets page always holds its
four action rows (no presets is a toast); and Upload Scrobbles with nothing to send is a toast after the press.

The older note (`feedback_empty`): one line of 16 px Text Secondary centered in the free area below a page's rows,
down to y 282, with an optional hint in 15 px Text Tertiary 24 px lower. Pages whose list keeps rows the note
explains use it: the App Store's "No apps listed" under its Check for Updates row, a card app's page with nothing in it (an empty Subsonic artist: its "No albums" info row's words, drawn only while there is no next page: an info row with More… after it stays a list, its subtitle
the hint, with no Add to Sync over nothing and no selectable info row, audit2 F1), the confirm note under Reset
Settings, and a payload list's other messages (Internet Radio's "Loading…", Battery History's "Unplug to start").

- Busy: no stock spinner (Playlists' while it adds a song); the toast that follows says it is done. A page still
  being fetched is the [loading overlay](#loading). A library list whose open sorts thousands of records (the first open after a library change) shows the [loading overlay](#loading) while it sorts, captioned "Sorting songs…", "Sorting albums…" or "Sorting artists…"; it has no Return, as the sort runs inside stock's load (internals, [Name order](internals.md#name-order), [Loading wait](internals.md#loading-wait)).

### Sync

A card app's Sync (q2-subsonic's) is one centred stack per state, iOS's Software Update's way (the user's, 2026-11-19,
`mock_sync_apple.py`), the [empty state](#empty-and-offline)'s form: the state's glyph in outline (60 px, Text
Secondary), its title in Inter Medium 20, its lines in 15 px Text Secondary each on a 20 px band, at most one
[button](#buttons) (the focus rule's ring), and a footer in 13 px Text Tertiary centred on y 304, clear of the glass;
syncing, a 4 px bar 232 px wide in Track filled in the accent sits 10 px under the title. Sync asks the server every
time it opens.

| State | Glyph | Title | Lines | Button | Footer |
| --- | --- | --- | --- | --- | --- |
| Checking | `arrows-clockwise` | Checking for changes… | Asking music.example.net | | Last synced yesterday at 21:40 |
| Up to date | `check-circle` | Up to date | Your offline songs match the server. | | Checked just now · last synced yesterday at 21:40 |
| Changes | `arrow-circle-down` | 12 new songs | 380 MB · 21 GB free / 2 songs will be removed | **Sync Now** | Last synced yesterday at 21:40 |
| Syncing | `arrow-circle-down` | Syncing 4 of 12 | the bar, then Rockstar · Post Malone | **Stop** | Keep the Q2 on Wi-Fi until it finishes |
| Failure | `cloud-slash` | Couldn't reach the server | Server error 502. / Offline songs still play. | **Try Again** | Last synced yesterday at 21:40 |
| Nothing chosen | `cloud` | Nothing to sync yet | Hold a song, album or artist / and choose Sync Offline. | | |

The app sends each state as a reply led by an info row `status` (its context the glyph, its subtitle the lines,
`\n` apart, its position the bar's permille), its first action row the button and an info row `foot` the footer
(`addon.c` `status_view`, the caps word `status`; [internals](internals.md#card-apps)).

### Loading

A page whose content has to be fetched first opens only once it is there: until then the busy spinner is laid over the
page that was pressed (`patch/addon.c` `push`, `patch/list_marks.c` `load_spinner_paint`), never a Loading… page of its
own. A page opened from another module's window (a card app's first open, Chapters) lays it over that window
(`patch/list_marks.c` `load_over`, painted by `ringnav_paint` and given every input first by `ringnav_input`, as Cover
Flow's open holds Home).

- **Form:** the [busy spinner](#empty-and-offline)'s, a 64 px `#1C1C1E` square with 12 px corners and eight 7 px dots
  15 px out (11 on the diagonals), the lit one a step clockwise every 100 ms and each before it dimmer, in every theme.
  An action that names what it loads has its caption under the square: Inter 15 px in Text Secondary (`#ABABAB`) on a
  `#1C1C1E` plate 28 px tall with 12 px corners, its top 8 px under the square, 14 px of plate either side of the
  words and 240 px at most, a longer caption ending in an ellipsis; sentence case, the verb and what it acts on, an
  ellipsis after: "Shuffling songs…", "Shuffling albums…", "Sorting songs…", "Loading server songs…".
- **When:** only once the action has run 300 ms (`LOAD_SHOW_MS`); one done sooner shows nothing, so a quick press
  never flashes it. Input is held from the press on, shown or not.
- **Place:** centered on the whole screen, status row included: x 155 to 219, y 128 to 192. A window under the status
  row places it by its screen y, so it is never centered on the room under the bar.
- **The page beneath** stays in sight, undimmed, with its status row and its selection: the overlay asks nothing, and
  the 50% scrim is the [confirmation card](#confirmation-card)'s, for a question.
- **While it loads** Return, or a tap anywhere off the spinner, cancels: the page stays as it was and the answer, when it
  comes, is dropped. A tap on the spinner does nothing; the wheel's Centre and a tap on a row do nothing either, so
  nothing is asked twice. A slow load has no Stop row (a Loading… page's after 3 s): Return cancels it, and the app's
  own menu keeps its Stop row.
- **Once it is there** the new page opens as any page does, sliding in; one that failed opens on its error page (Retry,
  Stop), after the app's 15 s at most.
- **Where:** the Library's Shuffle page (Shuffle Songs, Shuffle Albums, Shuffle Folders), while the whole library is
  read and then played, over the page; Return during the read gives it up and nothing plays, while a press during the
  play waits for it, as the play is one call the Q2 cannot step out of. A card app's (a music server's) list rows: an artist, an album, a playlist, a folder of the server's
  browse, the search results, Albums and the other rows of an artist's page, a sort, More…, and a list's Next page and
  Previous page rows, the new window taking the old one's place once it is there. A row pressed while a list's next
  page is on its way lets that page go, the list's Loading… row More… again, so a cancel leaves the rest a press away.
  A search or a sign-in field's text, once typed, closes the box onto the list it was opened from, the spinner over it.
  Spotify's browser: a list pressed on a page showing its rows (the menu, a list, Search's categories, More…).
  Internet Radio's directory: Top Stations, By Country, By Genre, a country or a genre, and a search, over the list
  they were opened from, through a wait for Smart Wi-Fi's link too (a "Connecting to Wi-Fi…" toast says why).
  A card app's or Spotify's first open, from Streaming (and a card app's Go to, More and Settings > Storage): the page
  opens once the app's first answer (its rows, its pairing prompt or its error) is there, through the app's start, the
  spinner over the window it was opened from; Return there gives it up and the app runs on. Chapters, from the hold
  menu, over the window the menu was held on, until ffmpeg has read them; given up, ffmpeg is ended. In place, the page
  as it shows until the answer replaces it: Retry on an error page (a card app's, Spotify's), a card app's next or
  previous section (a sideways swipe), the reload after an action on a card app's level, Spotify's Playback Settings
  after a change, and Spotify's Back to a list more than three levels down, which let its rows go.
- **Back** in a card app waits on nothing, so it has no overlay: the level below comes back at once, as it was left,
  its selected row and every page of it, and is asked again behind the scenes; its rows change only if the answer
  differs.

### Menus

Actions on the highlighted item (the hold menus, Now Playing's More, a playlist's More and the
pickers in them) open as a frosted page sheet over the page (the user's C3, 2026-11-16,
`design/menu-overlay` `mock_menu_final.py`).

- **The page behind** recedes: drawn at 94% of its size about the screen's center and dimmed half
  way to black (Light Mode: 35%). It shows above the sheet, status row included.
- **Sheet:** from y 40 to the screen's foot, edge to edge, its top corners 40 px, a 36 x 5 grab bar
  at y 46 (`#5A5A5E`; Light Mode `#A1A1A6`). Its material is the receding page blurred (about a
  20 px gaussian), its color raised 1.6 times, 28% of its brightness kept under a 6% white tint
  (Light Mode: white at 75% over it), smooth, with no grain (the user's call, 2026-11-20): only a
  fixed 4 x 4 ordered dither of one level, which keeps its gradients, darkened to a few levels,
  from showing as bands. It is made once, when the sheet first paints whole, never per frame.
- **Header:** the item's cover (52 px, 6 px corners, at the text column from y 56; a `#2C2C2E` tile
  when it has none), its name in Inter Medium 20 at x 88 and a line under it in 15, Text Secondary:
  a song's artist and album (Now Playing's: the artist), an album's artist, "Playlist". A picker
  over a sheet keeps that sheet's header; Shuffle's, Repeat's and Group by's choices are headed by
  the setting over the song. A menu with no item (a picker opened alone) has no header and its rows
  start where a page's do. The header scrolls away with the rows.
- **Rows:** on the house list grid, the screen's width: 32 px, the first at y 122, Inter Regular 20
  at x 24, an action's Phosphor symbol (regular, 19 px) on the right column in Text Secondary,
  white on the selection, which is the list's bar 16 px in from each side with 8 px ends. Past the
  list end the rows fade into the sheet, alpha 1 at y 266 to nothing at the screen's foot; once
  scrolled, they fade under y 50 for 14 px. The lit row is never faded, and the wheel's last row
  ends on y 282.
- **A setting** (Speed, Shuffle, Repeat, Group by) has its symbol before the label, the label 26 px
  further in, and its value alone at the right in Text Secondary, without the label's word
  (audit2 L3): Shuffle Off, Songs, In Album, Albums, Albums & Songs; Repeat Stop After Song, Stop
  After Album, Off, Song, Album, All (Folder in place of Album when grouped by folder); Group by
  Album or Folder. Group by, two choices, flips in place on Center; the others open a picker in the
  sheet, the current choice with the accent's check before its label.
- **Rating** is no menu's: no hold menu has a Rate row and the heart has no hold, so the
  [rating page](#rating-page) is the one place to rate.
- **Destructive rows** (Delete Song, Delete, Delete Playlist, Remove from Playlist): red `#FF3B30`
  at rest, white on a red bar when lit, as the confirm card's. Such a row comes last, 12 px below
  the actions, and asks before it acts. Space, never a rule. A sheet whose rows are more than six
  has no gaps, so it scrolls, and ends, on whole rows (the user's, from audit2 E1: a gap cut the end
  of Now Playing's More); its settings tell themselves apart by the symbol before the label. A
  list's hold menu has no Sorting row: sort orders are Settings > Library > Sorting's.
- **Now Playing's More** names its downloads by what they get (audit2 L2): Get Cover & Lyrics
  (stock's search for this song's cover and lyrics) and Get Album Lyrics (every song of its album).
- The menu opens on its first action, lit; the wheel moves, Center picks, Return closes it back to
  the row it came from.
- **Motion:** the sheet rises from the screen's foot over 260 ms, `cubic_out`, while the page
  behind recedes to 94% and dims and the frost fades in over it; Return lets it fall away over
  180 ms, ease-in, the page coming back as it goes, and no key acts until it has gone. A picker
  over a sheet shows in its place, and a pick closes at once. Under Reduce Motion each is a
  cross-fade in place, 150 ms in and 120 ms out ([Overlay motion](#overlay-motion)). The rise starts
  from the sheet's first whole paint, which keeps the page (480 KB) until the sheet closes.
- ponytail: the name ends in an ellipsis where the design's marquee would scroll it.

Built by `patch/menu_modal.c` (the grid's `modal`); Up Next opens in it by
`modal_sheet`. Now Playing's More and a
playlist's More are stock pages, which the build loads as dialogs (`tools/ipod.py` `modal_page`).

### Artist page

A card app's artist (the Subsonic app's, opened from Now Playing's artist tap, Go to Artist or its
Artists list), approved from `mock_album_artist.py` (option 2, header with monogram) and
`mock_artist_explore.py` (layout B) on 2026-11-15. Built by `patch/artist_page.c` (the header,
the pair, the rows) and `patch/addon.c` `artist_view` (which reply rows go where).

- **Status row:** the clock at its left, as every page's.
- **Header** from y 52: a 64 px `#2C2C2E` disc at the text column with the name's first letter in
  Inter Medium 22, white; beside it from x 102 the name in Inter Medium 20 (ellipsis), the counts
  ("4 albums · 3 singles · 62 songs" as the app sends them, else "4 albums" counted from its album
  rows) in Inter Regular 15 Text Secondary, and the genre in Inter Regular 13 Text Tertiary, a line
  left out when unknown. The disc's top stands over the status row's bottom 6 px.
- **Action pair** at y 128: Shuffle and Radio side by side, each a `#1C1C1E` panel 28 px high with
  8 px corners, its Phosphor symbol (16 px) and label (Inter Medium 17) centered together in the
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
  Tertiary (white on the selection); Center plays the list from that song. An app without
  `artist` sends neither album nor length: the title alone.
- **Albums** (titled so): 40 px rows from y 58, newest first, the undated last: a 32 px cover with
  4 px corners at the text column (the server library's cover when the app has fetched it, else a
  `#2C2C2E` placeholder), the title in Inter Regular 20 at x 66, the year right-aligned on the
  right column in Inter Regular 15 Text Tertiary (white on the selection). Center opens the album.
  **Singles & EPs** is the same page of the artist's singles and EPs.
- **Similar Artists** (titled so): 40 px rows from y 58, a 32 px `#2C2C2E` disc (`#3A3A3C` on the
  selection) at the text column with the name's first letter in Inter Medium 15, white, the name
  in Inter Regular 20 at x 66, and the chevron. Center opens that artist's page.
- **Hold menu:** Play/Pause held on the page opens the menu sheet of its sync rows, Add to Sync
  (or Remove from Sync), Quality and Sync Now, each with its symbol. Play is gone: Shuffle plays
  the artist.
- **A Library artist** (card songs and a signed-in server's songs; from Library >
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

- **Status row:** the clock at its left, as every page's.
- **Header** from y 46: the cover, 56 px with 6 px corners, at the text column (a `#2C2C2E`
  placeholder without one); beside it from x 92 the title in Inter Medium 20, white, the artist
  in Inter Regular 15 Text Secondary and the length in Inter Regular 13 Text Tertiary, centered on
  y 56, 77 and 95, each ending in an ellipsis when long. The cover's top stands over the status
  row's bottom 12 px.
- **Quality line** at y 112: a badge, 20 px high with 6 px corners and a 1 px Text Secondary
  outline, Phosphor `waveform-bold` (12 px, Text Primary) centered 11 px in and its label in Inter
  Medium 13 from 21 px in, 30 px wider than the label; then, 10 px on, the spec in Inter Regular 13
  Text Tertiary. **Lossless** for FLAC, ALAC, WAV, AIFF, APE and WavPack up to 16-bit and up to
  48 kHz, **Hi-Res Lossless** above either, spec "FLAC · 16-bit / 44.1 kHz"; **DSD** for a DSF or
  DFF file, spec its rate as a multiple of 44.1 kHz ("DSD64", from the bit rate); a lossy file has
  no badge, its spec alone at the text column, "MP3 · 320 kbps".
- **Grid** from y 138, 44 px a row, two cells a row at x 24 and 192: the name in Inter Regular 13
  Text Tertiary centered 8 px down, the value in Inter Regular 17 white 26 px down, ellipsized at
  150 px. Album, Year, Genre, Bit Rate ("1,001 kbps"), Size ("38.3 MB", one decimal), then
  Location on a row of its own across to the right column: "SD Card · Music/Disclosure" (the
  song's folder) for a card file, "Server · Downloaded" for a server song the card keeps (synced
  into `Subsonic/`, or in the app's Stream Cache), "Server · Streaming" for one the app fetched
  only for this play (straight in its `stream/` folder). A cell whose value is unknown is left out
  and the rest close up: no "Unknown".
- **Scrolling:** inert, no selection, as an iOS info page. When the grid ends past the list's end
  (y 282) the wheel scrolls it a row a tick, as far as its last row, and back, and a finger dragged up
  or down moves it with the finger between the same two ends, stopping where it lifts (no momentum,
  so a flick never overshoots); the list runs under the status row and fades there (the header's top shows only at the page's top).

### Album page

An album opened from Now Playing's art or album tap (Look up Album), Go to Album, Library > Albums,
Search's albums or an artist page's Albums: the Library's (stock's album pages, dressed by
`patch/library_album.c`) and a card app's (the Subsonic app's, `patch/addon.c` `album_view`), approved
from `mock_album_artist.py` (option 2, header with cover) on 2026-11-15. It shares the artist page's
header and pair (`patch/artist_page.c`).

- **Status row:** the clock at its left, as every page's.
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
  on the selection); Center plays the album from that track. The Library's tracks are the album's
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
- **A playlist's hold menu** ([Menus](#menus)), Center held on its row (or Play/Pause): Play, Shuffle, **Sort**, Rename, then Delete after a gap,
  which asks "Delete playlist “name”?" with Cancel first. Rename is the [letter strip](#text-entry),
  titled Rename Playlist, the name prefilled over it with the cursor at its end: submitting (Play/Pause,
  or Center on the return key) saves it, and a held Return leaves the name as it was. Sort (`funnel-simple`) is a
  setting row, its value on the right: the order of that playlist's songs, its choices Recently Added (the songs
  added last first), Oldest First and Title, the current one ticked; each playlist keeps its own
  ([internals](internals.md#playlist-song-order)). No More menu, no Edit Playlist: reordering is the server's or the phone's.
- **A playlist's page**: Shuffle (Songs' Shuffle row: the accent's glyph, 18 px, at x 26 and the
  label at x 54), Add Songs (`plus-circle`) and its songs, each one line, the artist on the right
  column in 15 px Text Tertiary; no Shuffle row over nothing. Center plays the list from the song;
  a hold opens the song's whole hold menu (below), then **Remove from Playlist** last, after a gap and red, which
  asks on the confirmation card first (the user's ruling, 2026-11-16). The songs are in the order its Sort chose (On-The-Go's as added), and Center plays them in that order.
- **On-The-Go** (Settings > Playback > Library > On-The-Go, on by default; off hides its row and Add to On-The-Go):
  Shuffle, Add Songs, Save as Playlist (`arrow-down`), Clear (`x`), then its songs. Save as Playlist
  makes "On-The-Go N", the first number free, with its songs, empties On-The-Go and lands on the new
  playlist in the list; Clear asks first, Cancel first. Empty, it is Add Songs alone over the compact
  empty state, "No Songs Yet", hint "Hold Play/Pause on a song to add it"; a playlist with no songs likewise,
  hint "Choose Add Songs to fill it". A hold on a song opens its
  hold menu with **Remove from On-The-Go** last. It lasts across a restart.
- **A song's hold menu**: Play Next, Play Last (to the end of Up Next's Playing Next), Add to
  On-The-Go (while On-The-Go is on), Add to Playlist…, Favorite with Phosphor `heart` in Text Secondary (Unfavorite when it is one, with
  `heart-fill` in Favorite `#FF375F`, white on the bar: the user's, 2026-11-20, `sync-heart-1120/menu-heart-*.png`;
  an album's and Now Playing's ⋯ menu say the same, Favorite and Unfavorite: the user's, 2026-11-20), Sync Offline (a song
  from a signed-in server; Stop Syncing Offline once chosen, `cloud-arrow-down` and `cloud-slash`), then Go to Album, Go to
  Artist and Go to Folder. An album's and an artist's hold menu have Sync Offline after their own rows, a server
  playlist's before Rename and Delete, and Now Playing's ⋯ has it for the song playing; none while the app syncs its
  Entire Library ([internals](internals.md#sync-offline)). The app toasts its answer: "Syncing offline · 12 songs",
  "Stopped syncing offline", "Offline via Entire Library" (the user's, 2026-11-19). Add to Playlist… is a picker in the same panel: New Playlist (`plus`) first, which makes
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
  mark's 22 px only on a row that has one. Center adds or takes out in place, the wheel stays where
  it is, and a toast says which ("Added to name", "Removed", "Undone"); Add All adds every song not
  already in ("Added N songs", "Already added").
- Return goes back a level; from the chooser, back to the playlist, which reads its songs again.

### Search bar

<a id="search-bar"></a>Every music list page has its own search (approved as `mock_search_filter.py` v3 on
2026-11-19, `patch/search_bar.c`): Songs, Artists, Albums, Genres, Playlists, Years, Ratings, Most Played and Long
Songs, and inside a playlist, an artist (its albums and songs) or an album.

- **Where:** one wheel step above the first row. As the page opens it is out of sight: the first row lit, nothing
  above it, and no fade or vignette hinting at it. A turn up from the first row brings it in: the list moves down
  40 px and the bar takes that band; a turn down puts the list back with its first row lit.
- **Look:** a Raised 2 pill from x 16 to 359, 36 px high with 10 px corners, centred in the band; a magnifier at
  x 31 and "Search <the page's title>" ("Search Songs", "Search Road Trip", "Search The Killers") in 20 px from
  x 48. Lit (the wheel on it, or typing) a 2 px accent outline goes round the pill, its inside unchanged, and the
  words are Text Secondary; otherwise Text Tertiary.
- **Typing:** Centre opens the [letter strip](#text-entry) on the same page title, the bar at the top holding the
  text with its cursor and the page's own rows that hold the text under it, in the page's order, as they live: no
  tabs and no other kind of result. While the strip is open the wheel picks letters.
- **Return** closes the strip and lights the bar, the text and the filtered rows kept; the wheel turned down goes
  into the rows. Return on the lit bar goes back to the page, its bar still lit; Return there leaves the page.
  The text is cleared by erasing on the strip.
- **The go key** (the accent's disc) closes the strip and lights the first match; with none it lights the bar,
  which shows the text, over the empty state's No Matches.
- **A match** opens as its row does on the page: the search goes and the page's selection moves to the row.
- Home's Search row stays the one search across everything ([Text entry](#text-entry)).

### Text entry

One letter strip for every text the wheel types (approved as 05b and 05c from the audit2 mock-ups on
2026-11-16, redesigned from the user's `keyboard-inscreen/strip.html`, approved 2026-11-17;
`patch/search_strip.c`): a `#1C1C1E` pill from x 40 to 335 and y 254 to 288 with 17 px corners, one loop
the wheel walks:

- **The set switcher**, docked at the pill's left: a `#2C2C2E` pill to x 96 (the accent while the wheel is
  on it) naming the set in Inter Medium 16 white, abc, ABC, 123 or #+= (all 32 printable ASCII symbols, the
  most used in passwords first). Center cycles the set and the highlight stays on the switcher.
- **The run**: the set's characters and a space, 17 px Inter Text Primary in equal 19 px cells from x 106,
  ten in view, the lit one white on an 18 px accent key 5 px in. It starts at its first character and moves
  only when the highlight comes within a cell of an edge. Where more of the set lies past an edge the letters
  fade out toward it over 22 px: the text's own alpha, never a band painted over it.
- **The return key**, docked at the pill's right: a circle as tall as the strip (34 px) in the accent with its
  mark in white, inverted, a white circle with the mark in the accent, while the wheel is on it, in either half.
  Center on it submits. The mark says what submitting does, by the field (the user's 2026-11-17 ruling, mock-up
  `returnkey/context.html`; 24-unit glyphs drawn 20 px, round caps and joins):
  - a **magnifier** (a ring and a handle to the bottom right, 2.4 units) on every search: Search, Add Songs'
    Search, a playlist's song search, Internet Radio's, Spotify's, TIDAL's and a card app's;
  - a **check** (2.6 units) where a name or a value is saved: New Playlist, Rename Playlist, the Bluetooth name,
    Guest Play's name, a paired device's Rename, a trusted guest's Rename, an EQ preset's name, a band's frequency
    and Q;
  - an **arrow right** (2.4 units) on anything else: a Wi-Fi password, Join Other Network, the hotspot's password,
    a card app's fields, Car Remote's phone name, and any field no caller names.

One turn of the wheel stops on the switcher or the return key, whichever end it reaches; a new turn goes on
round to the other. Play/Pause submits
from anywhere on the strip. Return shuts the strip, never a submit, and so does an edge swipe (the user's
2026-11-18 ruling, on every page the strip types for): the strip goes, the text and its cursor stay where the
value lives, and the next Return, or swipe, leaves the page as it would anywhere. Centre or a tap in the text opens
the strip again, and a submit shuts it too (Search's results then fill the page under the tabs). Previous and Next move the text's
cursor; Previous held erases the character before it, the first 450 ms into the hold, the next 500 ms later,
each after that 15% sooner, down to 110 ms. There is no delete key and no way to show a password. A key's
pop bubble rises over the lit character as the wheel lands on it.

Where the text shows (the user's `keyboard-inscreen/strip.html` and `all-inputs.png`, 2026-11-17): where the
value lives, never in a box of the strip's own, and with no keyboard hints on the screen. The text is 20 px Text
Primary (a field's Inter Medium 22) with a 2 px cursor after the characters before it, blinking 530 ms on and
530 ms off while the strip has the wheel (lit again at each key, steady under Reduce Motion). A text wider than
its field scrolls by as little as keeps the cursor 10 px in from either edge, and an edge that cuts it fades the
text out over 16 px inside the field, in the text's own alpha, never a band painted over it; a text that fits is
left-aligned (right-aligned in a cell). By form:

- **Search** (and Add Songs' Search, and every search box: Internet Radio's, a card app's, a playlist's song
  search): the query on the page's first line after a magnifier, "Search your music" (a box's own tips, else
  "Search") in Text Tertiary while empty, a 1 px rule under it; on Search, the results between it and the
  strip, or the screen's foot while the strip is shut, and with text that matches nothing, the [empty state](#empty-and-offline), "No Matches", "Try fewer
  letters".
  The wheel reaching the query from the tabs (or from Add Songs' songs) only **highlights** it: the selection bar
  (the accent, 8 px corners, x 16 to 359 over the query's 32 px line) with the magnifier and the text in white and
  no cursor, the strip shut. A turn on goes down to the tabs; Center or a tap on the
  line opens the strip with the cursor in the text. A tap counts when the finger lifts within 8 px of where it went
  down; a drag that starts on the line is a scroll and leaves it as it was.
- **A row** (New Playlist, Rename Playlist): the page under as it was, and over the row the wheel was on, in its
  place, a box from x 16 to 359 in the raised grey with 8 px corners and a 2 px accent line, the text from x 24.
- **A cell** (Bluetooth's Name, Guest Play's Name, a device's or a phone's Rename, an EQ preset's Name, a card
  app's field): the same box, the row's label from x 24 in Text Secondary and the text after it, right-aligned to
  the right column. Who sees the text ("Phones and cars see this name") is 13 px Text Tertiary centered 24 px
  under the box, the page under showing only down to the box.
- **A field** (a Wi-Fi password, Add Network's name, the Guest Play hotspot's password, and a row or cell whose
  row is not in sight or would sit under the strip: an EQ band's Frequency or Q, a card app's field on its
  own page): its label in 15 px Text Tertiary from x 40 on y 92 over a raised pill from x 40 to 335 and y 106
  to 150 with 12 px corners, the text 16 px in from either end.

Touch in the text, in every form (the user's "tap or drag to move cursor with a magnification effect on any
input", `strip.html`'s `caretAt()` and `loupe()`, approved 2026-11-16): a tap puts the cursor at the nearest
character boundary; a hold of 250 ms, or a drag, shows the loupe, a 104 by 46 px lens with 14 px corners, a 1 px
`#6E6E6E` line and the field's own ground (the canvas under Search's query, the raised grey in a box), its foot
the text's size and 10 px over its line and kept 4 px inside the screen, centered on the cursor; in it the text
around the cursor at 1.6 times, drawn again by the same text renderer (never a copy of the screen), clipped and
faded at the field's edges as the field is, with a steady 3 px cursor in its middle. It grows in from half its
size about its foot over 100 ms, and shows whole at once under Reduce Motion. The cursor follows the finger; held
within 10 px of an edge that cuts the text, the finger nudges the cursor a character toward it every 150 ms, the
text scrolling under it, until that end is in view. Letting go leaves the cursor where it is.

Selecting, in every form (approved by the user, after iOS's text selection): a second tap
within 350 ms and 20 px of a tap selects the word there (the one a boundary ends, else the one it starts; a
password's whole text, its words not showing), a third all of the text, a fourth puts the cursor back. The
selected characters sit on the accent at 40%, the cursor's height, and the cursor is hidden; each end has a
handle in the accent, a 2 px line the cursor's height with an 8 px dot over the start and under the end. A
finger kept down after the double tap drags on: the word stays selected and the end on the finger's side follows
it. A press within 12 px of a handle takes that end, the loupe over it (the highlight shown in it too, its line in
the accent), and it follows the finger, never onto the other end. A tap anywhere else in the text puts the
cursor there. Typing replaces the selection; Previous held deletes it first (a space it leaves doubled goes with
it) and erases on as before; a press of Previous or Next puts the cursor at its start or end.

A password is dots, the character just typed shown for a second, in whichever form. A box holding a number opens
on 123. Submitting a box is its own OK, as the keyboard's Enter was: the box takes the text and acts.

Held, Return goes back (the user's, from `mock_hold_back.py` on 2026-11-17): 200 ms into the hold the page
dims under black at 55% and a `#1C1C1E` card with 22 px corners, centered on y 160 and sized to its line with
24 px sides and 20 px above and below, shows a 44 px ring (a 4 px `#3A3A3C` track the accent fills clockwise
from the top) round Phosphor's `arrow-u-up-left` (bold, 20 px, white) and, 14 px under it, "Keep holding to go
back" in Inter Medium 16 white; at 600 ms the page closes. Let go before that and the
strip shuts, as a press of Return shuts it. The dim covers the status row too, as Buttons Lock's card's does: both draw it the same way
(`feedback.c` `overlay_scrim`). Stock's [keyboard](ipod.md#keyboard) is gone: the window each box opens for
one is a bare one the strip takes in its first frame (`patch/keyboard.c` `kb_host_open`).

### Icons

- One set: [Phosphor Icons](https://phosphoricons.com) (MIT), `@phosphor-icons/core` 2.1.1. The
  SVGs the theme uses are vendored in `assets/icons/phosphor/` with their license, and
  `tools/art.py` draws the theme's bitmaps from them at build time, in final colors per half.
  A mark Phosphor lacks is drawn for q2-coda in Phosphor's regular style (16 unit strokes, round
  caps and joins, on the 256 unit grid), outlined to filled paths, and kept apart in
  `assets/icons/q2coda/` under the repository's license: the Subsonic card app's `subsonic.svg`, and
  `heart-slash.svg` for Favorites' empty state: Phosphor's regular heart cut as its outline `bell-slash`
  is (a 16 unit slash from (48,40) to (208,216) joined to the ring below it, the ring above it ending in
  round caps 32 units from the slash), traced to filled paths.
- Weights by role: `fill` for the padlock and a favorite's heart, `bold` for chevrons, the status row's `wifi-x`, sync, Bluetooth and `cloud-slash`, Now
  Playing's and the confirmation's shuffle and repeat, About's quality badge and Now Playing's heart while off, `regular`
  for row symbols and Now Playing's More. Sizes are the design's: 13 px padlock, 11 px tall status glyphs ([Status row](#status-row)), 15 px chevron, 20 px row symbols,
  heart and More, 22 px state row, 56 px confirmation, 12 px for About's quality badge
  (`waveform-bold`).
- **Missing art** is one placeholder everywhere stock shows its own (Now Playing, the album grids,
  Home's art, Cover Flow): a `#2C2C2E` tile, its corners rounded and clear (24 px of its 290, so no
  dark corner shows on a selected row), with Phosphor's `music-notes-simple` (regular) in Text
  Tertiary, a third of its width, centered (`assets/icons/default_album_home.png`, drawn from the
  vendored SVG; `tools/build.py` sizes it into stock's `ART_PLACEHOLDERS`), in place of stock's
  `#787878` disc (the user's, from audit2 I1).
- **Optical sizes** (the user's, 2026-11-16, `mock_wifi_trash.py`): a size is the glyph's box, and the glyph inside it
  is scaled about its ink's center until its ink's longer side is a set length, centered in the box, so a wide glyph
  (Wi-Fi's signal) and a tall one (the lock, the trash) read as one size. The 20 px symbols (rows', Streaming's marks,
  Now Playing's heart and More) have 15.5 px of ink (`tools/art.py` `ROW_INK`, `glyph_png`). Wi-Fi's row glyphs end
  on the right column, the connected row's trash (Forget) last, selected or not (`wifi_rows.c`). The status row is
  the exception: there every glyph is sized by its ink's height, 11 px, the clock's digits' (`tools/art.py`
  `status_fit`), since beside a row of figures a wide glyph sized by its width came out short (Wi-Fi at 8 px).
- No colored app tiles: the concept's tinted "For You" squares are for content the Q2 does
  not have.

### Control Center

The pull-down, drawn as iOS's Control Center (v4, "A, tightened", approved on 2026-11-16 from
`design/control-center-v4` `mock_cc4_tight.py`; `patch/control_center.c`), under the status row, whose clock is
centered, in two columns. **One spacing token, 10 px,** between every two parts: the status row's glyphs (foot y 28)
and the blocks, the block and the right column, Now Playing and the sliders, the two sliders, and round the toggles inside their block.

- **Toggles block**: 158 x 210 from (20, 38), `#1C1C1E` with 22 px corners. Six 40 px circles two by three,
  26 px apart both ways (centers x 66 and 132, y 68, 134 and 200): Wi-Fi, Bluetooth / AirPlay, Car Remote /
  Smart Wi-Fi, EQ. Accent Blue when on, `#3A3A3C` off.
- **Glyphs**: Phosphor fill, each at one optical size, its ink's longer side 18 px (`tools/art.py` `CC_INK`):
  Bluetooth `bluetooth`, AirPlay `airplay`, Car Remote `car-profile`, Smart Wi-Fi `leaf`, EQ `equalizer`. White on
  a lit circle; in Light Mode a circle that is off takes Text Primary (`CC_LIGHT`).
- **The Wi-Fi circle is the link**, as the status row reads it (`status_row.c` `wifi_level`, `wifi_offline`):
  `wifi-slash` off, `wifi-none` on but not joined, `wifi-low`, `wifi-medium` and `wifi-high` by stock's bars (1, 2,
  then 3 and 4), `wifi-x` joined with no link up. Phosphor's fill levels draw the whole fan and fill as much of it
  as the signal, so the glyph keeps one footprint. Joined, it is lit even where the radio's switch reads off, so it
  and the status row never disagree. It is drawn again when that changes while the page is open
  (`cc_wifi_poll`, every second, the circle's box alone). Its name stays "Wi-Fi", the tile's function, not the
  network's.
- **Names**: each circle's, its line 1 px under the circle, Inter Medium 13, white when on, Text Secondary off, set
  glyph by glyph at Inter's own advances (see [Type](#type)), centered on the circle in 76 px of room, 8 px from the
  block's edge (the mock's 10 px assumed a 13 px em; the house 13 px is an em of 13.2, which makes "Smart Wi-Fi"
  75 px). A name wider than its room is never shrunk or cut with an ellipsis: it starts at the room's left and its
  last 12 px fade out, the text itself going transparent onto the block (the marquee's fade, no painted band), and
  while the wheel is on its circle it scrolls as the selected row's title does ([Motion](#motion)), both ends
  fading while it is away from its start, back at its start when the wheel leaves or the ring goes. The six names
  all fit.
- **Now Playing**: 167 x 114 from (188, 38): 12 px in, the cover, 52 px with 9 px corners, the one Home's art pane
  shows (`song_cover_url`), a `#3A3A3C` square for none; the title (Inter Medium 16) and artist (Inter 13, Text
  Secondary) 10 px right of it, 10 px above and below its middle; under them, 8 px down, the transport's 30 px row,
  Previous, Play/Pause (30 px) and Next 46 px apart; 12 px under it the block's foot. Light Mode inks the transport
  Text Primary.
- **Sliders**: Brightness at y 162 and Volume at y 210, each 167 x 38 from x 188, fully round, `#3A3A3C` with a
  `#E5E5EA` fill to the level and an 18 px `#1C1C1E` sun or speaker at their start (white in Light Mode, where the
  fill is dark). Volume ends at y 248, the toggles block's foot.
- <a id="cc-usb-row"></a>**USB row** (approved 2026-11-17 from `design/usb-row` `mock_usb_row.py`), only while a cable is
  in: 335 x 38 from (20, 258), 10 px under the blocks, fully round as the sliders, in the block's color: Phosphor's
  `usb` (fill, 20 px, Text Primary) centered 22 px in, "USB" in Inter Medium 15 from 40 px in, the remembered choice
  (Automatic, Transfer Files, USB Audio, Charge Only, Ask Every Time, USB Debugging) in 15 px Text Secondary ending 30 px from its
  end, and a 13 px bold `caret-right` in the chevron's gray centered 18 px from it. The wheel's ring round it is the
  mock's: 2 px wide, 5 px out. Center or a tap opens the [USB picker](#usb). No cable, no row: the sheet as before.
- **Grab bar**: 36 x 5, `#5A5A5E`, centered, along the sheet's foot (the screen's, y 320): at y 309, 6 px off the edge, the gap the page sheets keep off their top. It is not part of the 10 px spacing; it stays on the foot whatever the blocks' height. It is where the swipe up that closes the page starts.
- **Not here**: Buttons Lock keeps its hardware hold (Settings > Controls), Night Shift its row in Display, the
  Sleep Timer its row in Playback, and Guest Play its row in Bluetooth (Home's first row reads Guest Play while a
  guest plays).
- **The wheel** rings the stop it is on in white (2 px, 1 px out); a slider that has the wheel is ringed in
  Accent Blue. The stops, in order: Wi-Fi, Bluetooth, AirPlay, Car Remote, Smart Wi-Fi, EQ, the Now Playing block
  (only while something is playing or paused, as Home's Now Playing row), Brightness, Volume, the USB row (only while
  a cable is in). The ring is not there
  when the page is pulled down, and touch never brings it: the first wheel tick (or Center) shows it on the stop the
  wheel is on, without moving or acting, and it goes 5 s after the last wheel tick or key; the next tick brings it
  back on the same stop.
- **Sliders and the wheel**: Volume steps exactly as Now Playing's volume does (1 a tick turned slowly, up to 10
  a tick on a fast spin, `vol_next`), and Brightness by the same curve over its own range, identically here and
  on Settings > Display > Brightness (`bright_wheel`); each fill eases to its level at Now Playing's bar's speed
  (`VOL_EASE_MS`).
- **Now Playing block**: a tap anywhere on it but the transport, or Center on it, closes the page and opens Now
  Playing; Previous, Play/Pause and Next keep their own taps. With nothing loaded it does nothing.
- **Holds**: a toggle held 550 ms (`QUICK_HOLD_MS`), by a finger or by Center with the ring on it, closes the page
  and opens its settings; a tap still toggles. Wi-Fi: Settings > Wi-Fi; Bluetooth: Settings > Bluetooth; AirPlay:
  the Network service page (its receiver switch); Car Remote: Settings > Car Remote; Smart Wi-Fi: Settings >
  Wi-Fi; EQ: the EQ page. The sliders and the Now Playing block have no hold.
- **Landing**: as the page comes to rest open, however it was opened, the blocks under the status row overshoot
  8 px down and spring back through rest to 2 px short, still after 280 ms (a damped sine, ease-out, as iOS's
  sheet settles). Only on opening; the status row stays put. The one exception to Motion's no bounce.

### USB

The USB pages ([internals](internals.md#usb-pages), `patch/usb_ui.c`, from `design/mockups/mock_usb_connect.py`):

- <a id="usb-picker"></a>**Picker**, "USB" (approved 2026-11-17 from `design/usb-row` `mock_usb_row.py`): Settings >
  General's USB row (after Storage, the remembered choice as its value) and Control Center's [USB row](#cc-usb-row)
  open it. Automatic, Transfer Files, USB Audio, Charge Only, Ask Every Time (and USB Debugging in a `--dev` build),
  the remembered one with the [picker](#list-row)'s check; under the rows the checked choice's note, 13 px Text
  Tertiary on the text column, its first line centered 19 px under the rows and the next 17 px lower (Automatic's "A
  car plays the Q2 through its speakers." and "A computer opens the SD card."; Ask Every Time's "Plug into a computer
  to choose."; USB Debugging's "A computer gets adb on every cable." and "Stop on its page goes back to
  Automatic."). Automatic's rows and toasts are [Car Audio's](iphone-mode.md#car-audio-in-the-image). A pick
  is remembered at once, and with a cable in that cable takes it at once; leaving Transfer Files while the computer
  has the card asks first on the [confirmation card](#confirmation-card), consequential: "Switch to USB Audio?", "Eject
  the SD card on your computer first, or a copy in progress stops.", Cancel and Switch. Automatic and Ask Every Time are the next cable's.
- **Chooser**, "Use USB For": Charge Only, Transfer Files, USB Audio (and USB Debugging in a `--dev` build), each
  with its Phosphor symbol (regular, 19 px, Text Secondary, white on the selection) and its caption under it while
  selected, the row grown to hold it; then Always Do This, a switch row, after a gap.
- **Mode page** (Transfer Files, USB Audio, USB Debugging, Ejected): a solid circle with its glyph (fill, 44 px,
  white; the eject's a bold check on Positive green), the title, a line in Text Secondary and Stop, the gray
  [button](#buttons) (`MODE_BUTTON`), ringed under the [focus rule](#focus-ring); a wheel turn on the page
  shows the ring. Stop forgets a choice Always Do This or the picker saved; Ejected has no Stop.
- **Toasts** with a 15 px glyph beside the words: Unplugged (Text Secondary), OK to Disconnect (Positive green),
  USB Audio Ready (white); and charging's: a charger in with the screen on, the battery's state as a tap on it
  says ("Charging · 62%", lightning, green), Charged to 80% when Charge Limit stops it (battery-full, green), and
  Charger too weak (warning, orange).

### adb

A `--dev` build's adb prompt and its settings ([adb](adb.md#authorization), `patch/adb_trust.c`; approved from the
mock of 2026-11-18, "card hugs its content", US spelling):

- **Allow this computer?** over whatever is on screen, the [confirmation card](#confirmation-card)'s wide form:
  Phosphor `laptop` (regular, Text Primary), "grayson@MacBook-Pro wants developer access over USB." (the name from the
  key's comment), "Key 3F:9A:1C:E0 … 7B:C2", the switch **Always allow this computer** (on), **Deny** and **Allow**,
  Allow lit. Return or 30 s answer Deny. ponytail: the mock's fingerprint is monospaced; the build ships no mono face,
  so it is Inter.
- **After Allow:** the [status row's pill](#status-row) "MacBook-Pro allowed" with the laptop (15 px) for 2 s.
- **Developer Options** (Settings > General, before Restart): Trusted Computers and their count.
- **Trusted Computers:** a row a computer, its name and the day it was added on the right column ("Today", "Nov 13"),
  then "Center to remove a computer" (13 px Text Tertiary), **Remove All** in Negative red, and "A computer you
  remove asks again / the next time it connects." Centre on a computer or Remove All asks on the card, destructive,
  Cancel first. With none, "No trusted computers". The mock's caption ("Ask Before Allowing stays on. Developer Options
  turns developer access off entirely.") named a switch the build does not have, so the caption says what removing
  does instead.

### Bluetooth

Settings > Bluetooth and its pages ([internals](internals.md#bluetooth-page), `patch/bt_ui.c`, `patch/bt_detail.c`,
`patch/bt_stopped.c`; approved from `design/bt-redesign`, its `NOTES.md` and sheets, on 2026-11-16):

- **Devices before settings.** The switch, Name (the name as its value), then My Devices (the paired ones) and
  Other Devices (the ones a scan heard while the page is open), each a section header in 15 px Text Tertiary a whole row high, then Audio:
  Quality (its choice as the value, "Auto" by default), Headset Volume, Resume on Connect and Link Volume. With Bluetooth off, the
  switch, Name and a line in 13 px Text Tertiary: "Turn on to play to headphones, a car or a speaker."
- **Scanning is automatic** while the page is open, the list's spinner (Text Tertiary) beside "Other Devices"
  while a scan runs: one as the page opens, then again each time one ends while nothing is connected. No Scan
  row.
- **Device rows** lead with the device's kind (Phosphor `headphones`, `car-profile`, `speaker-hifi`,
  `device-mobile`, else `bluetooth`; regular, 20 px, Text Secondary, white on the selection) centred on x 33, the
  name at x 52. The connected one is a two-line row: the name over "Connected · LDAC · 72%" (what is known: the
  codec, the headset's battery) after the Positive dot, with a chevron. Another paired one says "Not Connected"
  as a value before its chevron; one connecting, "Connecting…" with the activity spinner on the right column.
- **Centre** on a paired row, connected or not, opens the device's page (the user's, 2026-11-18: a device out of
  range could not be forgotten while only the connected one had a page); on one connecting it stops connecting;
  a found row pairs. Play/Pause held on any paired row opens its page too.
- **A device's page**: under the status row the path, "Q2 → WH-1000XM5" in 17 px, and the codec
  line in 14 px Text Tertiary ("LDAC · 96 kHz · 990 kbps", only what is known: the bit rate where the codec
  fixes it; "Not Connected" when it is not), then Battery (when reported), Disconnect, Volume Limit, Volume Curve,
  Rename and Forget This Device in Negative red; not connected, Connect first (selected as the page opens),
  Volume Limit, Volume Curve, Rename and Forget This Device. Volume Limit is a chevron row with its value, No
  Limit or the limit, on the right in Text Secondary, and opens the slider page (0 to No Limit). Volume Curve is
  the same kind of row, its value Even or Gentle, and opens a picker titled Volume Curve with those two, the
  device's checked, as Cover Flow's Sort. Forget asks on the [confirmation card](#confirmation-card): "Forget WH-1000XM5?", "You'll need to pair
  again to use it.", Cancel first and Forget Device in red; it works with the device out of range. Rename is the
  letter strip, "Shows on this Q2" under the name.
- **Quality**: one list, no submenu (the user's, 2026-11-18, from mock B). First a switch row, "Adapt to Signal"
  (off by default), its caption under it while selected: "On Auto or SBC, lowers quality to keep playing when the
  signal is weak". Then a "CODEC" header in 13 px Text Tertiary caps, a heading the wheel passes, and the choices:
  Auto, LDAC Best, LDAC Standard, aptX HD, aptX, AAC, SBC HQ and SBC, each with its bit rate in 15 px Text Tertiary
  where the codec fixes one, flush with the right column as a Settings row's value (the switch's edge), and just
  before the check on the checked choice (LDAC Best 990 kbps, LDAC Standard 660, aptX HD 576, aptX 384). Auto
  is the default, and the page opens on the checked choice. Auto's caption, under it while selected, "Steps with
  the signal, 330 to 990 kbps", or with a headset connected on another codec the best it supports, "Best this
  headset supports: aptX HD, 576 kbps". With a device connected, a choice whose codec it lacks is grayed as the
  Library's grayed rows are (#666666, the selection's ink at 70% on the bar) and stays pickable; its caption says
  why and what plays instead, "BMW 6R81 doesn't support aptX HD: using AAC".
- **Name** (the Q2's): the letter strip says "Phones and cars see this name" in 15 px Text Secondary under the
  name, the hint in 13 px under that.
- **Toasts**, each with its 15 px glyph: "WH-1000XM5 connected" (the device's kind, white), "WH-1000XM5
  disconnected" (the glyph in Text Secondary), "BMW 6R81 connected · resuming" with Resume on Connect on,
  "Headphones low · 10%" (battery-warning, red) once at 20% and once at 10% each connection.
- **Bluetooth Stopped**: when the radio never answers, a mode page, the warning glyph on an orange (Warning)
  circle, "Bluetooth Stopped", "Restarting the Q2 to fix it in 9 s" counting down, and a Not Now pill (Centre,
  Return or a tap), instead of a toast and an unannounced restart.

### Guest Play

A guest's phone plays through the Q2 to what the Q2 plays to ([internals](internals.md#guest-play-screens),
`patch/guest_ui.c`, on q2-guest's [contract](internals.md#contract); approved from `design/bt-redesign`'s
`bt-guest-flow.png`, G1 to G12, and N8 to N13):

- **Asking** (G3, G4): the [confirmation card](#confirmation-card): the guest's name, "wants to play music" (or "on
  Spotify", "over AirPlay"), on a first pairing the phone's code large ("482 913") with "Check that Alex's phone
  shows this code"; Allow Once (selected: one Centre answers), Always Allow, Decline, and the daemon's 30 s
  counting down to Decline. Return declines. A second guest while one plays (G6): "Replace Alex's Pixel?", "Sam's
  iPhone wants to play music", Keep Alex's Pixel (selected) and Replace, counting down to Keep.
- **The guest's Now Playing** (G5, G12): the art's place, the guest's title and artist, the phone glyph in the
  accent with "Playing from Alex's Pixel", "Your music is paused" (or "Paused on Alex's phone"), the path "Alex's
  Pixel → Q2 → BMW 6R81" (17 px) and each leg's codec line (14 px Text Tertiary), and one selected thing, Stop
  Guest Play: Centre stops at once, no confirm. Play/Pause, Next and Previous go to the guest's phone. It opens as
  a phone's guest starts.
- **Toasts** (G7, G8, G11): "Alex left. Resuming your music", "Lost Alex. Resuming your music" (warning, orange),
  "Declined Alex's Pixel" (no answer too), "Sam iPhone isn't a trusted guest", "Turned away Sam iPhone"
  (prohibit, Text Secondary). A failed pairing (G10) is a mode page: the warning on orange, "Couldn't Connect",
  "Try again from Alex's phone" and Try Again, which opens the window again.
- **Settings > Bluetooth > Guest Play** (N10, N11): the switch, Name (the strip, "Guests and AirPlay see this
  name"), Let a Guest Connect, then Allow (Ask Every Time, Trusted Guests Only), When a Guest Leaves (Resume My
  Music, Stay Paused), each choice checked, Trusted Guests (each its page: Rename, Ask Before Playing, Forget on
  the card) and Hotspot. Switched off, the switch and a line saying what it gives. The Bluetooth page's Audio group
  leads with its row, the value Ask, Trusted or Off.
- **Let a Guest Connect** (G2): "On your phone, connect to", the name in 30 px, "in Bluetooth settings"; the
  window's two minutes as an accent ring emptying round m:ss; while the hotspot runs, its join QR code with
  "AirPlay or Spotify?"; Done (Centre, a tap, Return) closes the window.
- **Hotspot** (N13): its switch, Name (Guest Play's) and Password (the strip), and while it runs its join QR code,
  "Scan to join".
- **Home**: while a guest plays, its first row reads Guest Play and opens the guest's Now Playing
  ([Home's first row](#homes-first-row)).

### Streaming Configuration

<a id="streaming-configuration"></a>Streaming's Configuration (approved as 25 v3 B on 2026-11-16,
`patch/stream_config.c`) is two levels of [list rows](#list-row), values on the right.

- **Configuration:** Stop Streaming Services; Download Ahead, its value "2 Songs" (Off, 1 Song, 2 or 3 Songs, stepped
  in place by Center); a "Services" header; **TIDAL** and **Spotify**, each with a chevron and
  its value, Shown or Hidden (what this page controls; a "Signed in" value would need each app's sign-in state), and
  the caption "Its cache, downloads and place in Streaming" (Spotify: "Its cache, sign-in and place in Streaming");
  a "Show in Streaming" header; then one switch per service that has only a switch (the card apps, Internet Radio,
  AirPlay).
- **TIDAL:** Show in Streaming, Show Downloads (Tidal Downloads' row on Streaming), and Cache, "2 GB", captioned
  "Space kept for songs played from TIDAL"; Center steps through 512 MB, 1, 2, 4 and 8 GB in place.
- **Spotify:** Show in Streaming, Cache ("Space kept for songs played from Spotify") and Erase Spotify Data ("Signs
  out of Spotify and clears its cache"), which asks on the [confirmation card](#confirmation-card).
- Headers are 32 px rows of 15 px Text Tertiary, so the list scrolls in whole rows.
- ponytail: the captions above are approved but not built. A `page_list` page's rows sit at fixed offsets, so a
  selected row cannot grow under its title as a settings list's does ([internals](internals.md#streaming-configuration)).

### Update Library

<a id="update-library"></a>The library scan (Update Local Music, Scan Music Now, a folder's scan; approved as 27 v4 on
2026-11-16, `patch/scan_page.c`) is the empty states' stack over stock's dialog: a 60 px figure, 14 px, the title in
Inter Medium 20, the line in 15 px Text Secondary, 20 px, then the shared [button](#buttons), the whole centered from
y 40 to the screen's end. The full status row stays: stock's dialog covers the bar, so the
page paints it first, as Control Center does (`msg_title`).

| State | Figure | Title | Line | Button |
| --- | --- | --- | --- | --- |
| Scanning, no total yet | Software Update's twelve-spoke spinner | "1,284 songs found" | folder glyph and the folder's name (the house marquee when long) | Stop, secondary |
| Scanning against the library before it | a 56 px ring, 4 px, Accent on Track, the percent in Medium 16 inside (at most 99%) | "312 of 704 songs" | as above | Stop |
| Done | Phosphor `check-circle`, regular, Text Secondary | "Library Updated" | "738 songs, 34 new" (a whole scan that grew the library), else "738 songs" | Done, primary |
| Stopped (Stop) | as Done | "Scan Stopped" | "120 songs found" | Done |
| `/mnt/data` full | Phosphor `memory`, regular, Text Secondary | "Not Enough Space" | "Some songs weren't added" | Done |
| The card pulled during the scan | as `/mnt/data` full | "SD Card Removed" | "Put it back and scan again" | Done |

- No frame draws the focus ring until the wheel turns ([focus ring](#focus-ring)); Center presses the one button.
- Stock holds the ending about 2.6 s (three polls, then a 2 s sleep on the UI thread) and closes the dialog itself,
  so Done shows what happened rather than waiting for a press.
- The card-removed ending's line differs from the mock-up's "Your library is unchanged": with the card pulled
  mid-scan (emulator, 2026-11-16) stock ends the scan as a finish, toasts its own "SD Card Removed", and keeps
  only the songs it had read (3,800 became 3,000), so the library is not unchanged. The page tells it from a
  finish by stock's `/tmp/mmc_add`, gone once the card is. The wording is the default pending the user's OK.

### Plug-in screen

A cable in while the screen is off lights it for 3 s and then turns it off again (S1, V1 from
`design/mockups/mock_charge_anim.py`, approved 2026-11-15; `patch/charging.c`); with the screen on there is no
screen, only the toast and the status row. Black in both halves, over the whole screen:

- **Battery**: a 170 x 76 body round (187.5, 132), 16 px corners, a 3 px `#3A3A3C` outline; a 7 x 28 nub 5 px past
  it with 4 px corners; the fill 8 px inside with 9 px corners, `#34C759`, to the level. Nothing over it.
- **Sweep**: on a 2 s loop the fill's last 14 % grows up to the level (1.3 s, ease out), holds to 1.7 s and fades
  out by 2 s. Full, or held at Charge Limit, the fill stands still.
- **Level**: "62%" in Inter Medium 30 px, white, its center 12 px right of the screen's on y 212, and Phosphor's
  `lightning` (fill, 26 px, `#34C759`) 3 px left of it. No outlined bolt.
- **Word**: "Charging" ("Charged" at 100 %) in Inter Regular 16 px, Text Secondary, centered on y 244.
- A key or a touch ends it and leaves the screen on, doing nothing else; a page opening over it (a computer's
  chooser) ends it the same way.

### Software Update

General's one **Software Update** row ([internals](internals.md#sd-card-update), `patch/sw_update.c`, approved from
`design/mockups/mock_fwupdate.py` on 2026-11-16 and as 29 v3 the same day) checks online and the SD card at once,
in mode pages as USB's:

- **Checking for an Update…**, "Looking online and on the SD card" ("Looking on the SD card" with Wi-Fi off, when only
  the card is checked): no circle, a 48 px spinner of twelve round 4 px spokes in
  Text Primary, the head turning a spoke every 80 ms and each spoke behind it dimmer, to a quarter.
- **The two checks are independent.** The online one (the latest release's tag and its update.tar's size, about 10 s
  at most) failing, from no Wi-Fi link, no DNS, no internet or a timeout, never fails or holds up the card's: the
  card's result shows, with "Couldn't check online · Try Again" under it.
- **The offer** (29 v3): a 60 px accent circle (its glyph white, 30 px: `download-simple` from the internet,
  `file-arrow-down` from the card) centered on y 92, the update's name ("Vamp 1.8.3", Inter Medium 22) on 142, "From
  the internet · 55.6 MB" or "From the SD card · 55.6 MB" (15 px Text Secondary) on 168, "Installed: 1.8.2" (13 px
  Text Tertiary) on 188, and the [button](#buttons), selected, centered on 225: **Download and Install** or
  **Install**. When both have an update the internet's is offered and "Install from SD Card instead" sits under the
  button on y 266 (15 px Accent Text); with Wi-Fi off the card's is offered with "Turn on Wi-Fi to check online"
  (Settings > Wi-Fi) there. The wheel reaches the line after the button (an underline marks it); Center acts on the
  one in focus.
- **Downloading the Update**, its percent ("Verifying…" while the signature is checked) and the restart's bar
  filling in Accent Blue; Return leaves and cancels it. Then the card's check runs on what it fetched.
- **Install Update?** on the [confirmation card](#confirmation-card) over the check's page: "Install Vamp 1.8.3 ·
  V1830?", its body "SIZE MB, over TAG. Takes about 2 min;" over "your music and settings stay.", then Cancel
  (selected) and Install and Restart.
- **Restarting to Install**, "Don't turn off the Q2": `arrow-clockwise` bold on Accent Blue, and a 180 x 6 bar with
  round ends under the stack (top + 172), Track `#333333` filled in Accent Blue with the unpacking (the user's, 2026-11-16: progress fills are the accent).
- **Results**, each a fill glyph on its circle: You're Up to Date (check, bold, Positive green, "VERSION (TAG) is
  installed"), No Update Found and No SD Card (`file-arrow-down`, `#3A3A3C`), Charge to Update and Not Enough Space
  (`battery-warning` and `warning`, `#FF9F0A`), Update Can't Be Used (`warning`, `#FF453A`), each with the online
  check's line under it when it has one (28 px under the page's own). Return leaves, and so does Center unless the
  wheel is on that line.

### Battery History

<a id="battery-history"></a>Power's Battery History (approved as 33 v3 on 2026-11-16 and laid out again from the user's
mock-up of 2026-11-18, `patch/battery.c`) is a graph over the whole page, under the status row.
The three parts stand as one block from y 57 to the list end (y 282), so nothing reaches the glass's bottom corners. Each
y here is on the screen; the page's window is the whole screen but the view starts at y 58, so the code takes them from
the canvas's origin, not the view's own y:

- **Readout**, centered at y 66 in Inter Medium 17, Text Primary: the reading the wheel is on, "Nov 16, 3:10 PM · 51%"
  (the status bar's 12- or 24-hour form; "+1:23 · 51%" with the clock never set).
- **Graph**, 100% at y 84 to 0% at y 226 (142 px): faint lines at 100% and 50%, the axis at 0%, their
  labels right-aligned in 15 px Text Tertiary; the level as steps in the accent's light tone over a faint fill; the
  reading's 1 px hairline in Text Secondary from 100% to 0% and a 10 px white dot on its level.
- **Share Log**, the shared gray [button](#buttons) centered at y 263 (y 244 to 282, 38 px above the screen's edge). The
  wheel always moves the reading (clockwise to the newer) and Center always shares, so the button is the page's only
  action and no frame draws the focus ring. It writes the log to `/tmp/battery-log.txt` and opens LocalSend's share
  sheet ([localsend](localsend.md#sending)).
- **Results** are the house toasts in the button's place ("Couldn't write the log"; the share sheet's own after a
  send): the button gives way while one shows.
- ponytail: the button has no pressed frame under Center (the share runs on the key's press); a press frame needs
  the key's down and up told apart in `battery_key`.

### Car Remote

Settings > Car Remote (approved 2026-11-16, `design/carremote-page`, `mock_carremote.py`; `patch/carremote_page.c`):

- **The switch** alone while off, its caption under it while selected ([Captioned row](#list-row)): "Control your Vamp
  from Android Auto with the Vamp app."
- **No phones:** the [empty state](#empty-and-offline) under the switch in its compact form (no glyph), "No Paired
  Phones", "Open the Vamp app on your phone to pair", and the Pair a Phone button, selected as the page opens.
  The button is a row of the page's own list after the switch, so the wheel moves between the two. ponytail: the
  mock-up's address line under the hint ("Vamp at 192.168.1.30") needs a fourth line in the shared empty state;
  the how-to page shows the address meanwhile.
- **Phones:** 10 px under the switch, Pair a Phone as the blue top-action row (the accent's `plus-circle`, 18 px at x 26,
  its label at x 54 in the accent; white on the selection, as a playlist's action rows), then each phone as a
  [two-line row](#two-line-row) 52 px tall: Phosphor `device-mobile` (regular, 20 px, Text Secondary, centred on x 34),
  the name at x 56 over its state in Text Tertiary: "Connected over Wi-Fi" after a 7 px Positive dot, or
  "Not connected · last seen Friday"; a chevron opens the phone's page.
- **A phone's page:** [label/value rows](#labelvalue-rows) Status ("Connected · Wi-Fi"), Paired ("Nov 12"), Last
  Connected ("Now", "Friday"), Rename (the name and a chevron: the letter strip), then Forget Phone in Negative red after
  a 12 px gap, which asks on the [confirmation card](#confirmation-card), Cancel first. It opens on Rename.
- **Pair a Phone** (the row and the button): one page with the pairing code ([internals: Car remote](internals.md#car-remote)).
  With a QR code, the 180 px plate at x 20, y 58, the code under it in Inter Medium 28, and beside it from x 216 (14 px
  lines 17 px apart from y 72): "On your phone, open", "the Vamp app" in Inter Medium 16, "Tap Pair a Vamp, then
  scan this or type the code", in Text Tertiary where it works from ("Works from any network" with a relay, else "Phone
  on the same Wi-Fi as this Vamp"), "Or nearby, by Bluetooth" while it advertises, "Wi-Fi 192.168.1.30" in 13 px,
  and "Waiting…" level with the code. Without one, the code in Inter Medium 40 centred near the top, 15 px "On your
  phone, open the Vamp app, tap Pair a Vamp and type this code", in 14 px Text Tertiary "Bluetooth  Phones nearby
  can find it" and "Wi-Fi  192.168.1.30", "Phone on the same Wi-Fi", then "Waiting for a phone…". With neither link,
  numbered steps (20 px Raised 2 discs, 15 px lines from y 66) "Turn on Bluetooth, or join a Wi-Fi network in Settings
  > Wi-Fi", "Come back here to pair", then from y 190 "Vamp" in Inter Medium 17 over "Not reachable yet". The Q2
  Muse cannot start a pairing; the phone does.
- **Pairing:** the code's Dialog over whatever shows, then the icon toast "Pixel 10 Pro paired" (Positive
  `check-circle`). ponytail: the card form with the 120 s countdown on Cancel's row is the shared pairing card's, not on
  this branch yet.
### Radio

Home's Radio ([internals](internals.md#radio-home), `patch/radio_tuner.c`), as the approved prototype
(`~/q2-emu/design/radio-tuner/index.html`, the tuner's redesign approved 2026-11-20; device shots in
`~/q2-emu/design/radio-tuner/device-shots-v2/`). The page's hidden title (read by Up Next's "From") is "Radio" on
the tuner, loading and error screens, "Near <city>" on the stations list and "Change Location" on the ZIP entry.

- **Tuner:** the frequency the needle is at in Inter Medium 64 (the one size past the type steps, as the plug-in
  screen's 30 is), its baseline at y 158, centred between the status row and the dial, with "FM" in Inter Medium
  17 Text Secondary 6 px after it, the pair centred. Above it the station's name in Inter Medium 20 at y 84;
  below, the song or "Paused" in 15 Text Secondary at y 186, and call sign and city in 15 Text Tertiary at y 208.
  A line wider than 315 px is the house marquee ([Motion](#motion)): 1.5 s still, then 30 px a second, a copy
  40 px behind, its ends faded 16 px. The words wait for the station, as RDS does: they appear once its stream is
  sounding and its title has come (or 4 s of sound with none), fading in over 400 ms, so tuning in feels like
  connecting; between stations, while one connects, and on a station with no stream, the frequency stands alone.
  There is no "No station" line.
- **Dial:** a scale under a fixed needle, 62 px a MHz, baseline y 270: a 7 px tick each 0.2 MHz (Text Tertiary),
  14 px on the whole MHz (Text Secondary), the even MHz labelled in 13 Text Tertiary under it; no mark for the
  stations (the detents and the sound find them); both ends fading into black over 48 px; the needle a 3 x 44 px
  bar in Favorite red (#FF375F) from y 230, gliding after the dial (90 ms time constant). The wheel tunes 0.02
  MHz a step (one every 12 degrees of the touch wheel), 0.06 turned fast; each station is a detent that holds
  three steps, and every step clicks, louder and duller on a station.
- **Centre** (approved 2026-11-20, `~/q2-emu/design/radio-tuner/centre-volume/sheet.png`; built:
  `centre-volume/built/`): a tap swaps the dial for Now Playing's volume band on the dial's row (its bar's ends,
  the two speakers, the inset track and white fill, the same ease), the wheel setting the volume; 3 s after the
  last turn or press (Now Playing's scrub timeout) the dial returns and the wheel tunes. A tap while it shows ends
  it; a double press turns the screen off. Held 550 ms (Edit Library's hold) Centre opens the stations list;
  stock's Shut Down? hold is not reached from the tuner. Frequency, name and song stay where they are.
- **Stations list:** the house list's measures (32 px rows from y 58, Inter 20, the flat selection bar
  `sel_bar_flat`, fades from y 40 to 74 and 266 to 310 where the list goes on, the 4 px scroll indicator at
  x 366 while the wheel turns), painted by the page: the name left, the frequency right (Text Secondary, Text
  Primary on the selection), a station with no stream in Text Tertiary; the station sounding carries the
  now-playing mark left of its frequency, three 3 px bars 5 px apart, up to 14 px tall, moving out of step, in
  Accent Text (white on the selection). The last rows are Use My Location and Enter ZIP Code…, with chevrons.
- **ZIP entry:** "Enter ZIP Code" (20 Medium, y 92) and "Turn to choose, Centre for the next digit" (15
  Secondary, y 118); five 42 x 56 digit boxes 52 px apart from y 150, Raised with 10 px corners, the current one
  the accent, digits in Inter Medium 30; "Menu: back" in 13 Tertiary at y 250.
- **No Wi-Fi:** while the Wi-Fi link is down, the standing toast ([Dialogs and toasts](#dialogs-and-toasts))
  says "No Wi-Fi" over whichever screen is up, the dial and the keys still working; it goes when the link is back.
- **Loading and error:** the spinner centred at y 130, "Finding stations near you…" (17 Medium, y 182) and the
  seconds so far (15 Tertiary, y 208); "Couldn’t find stations", the reason and "Centre: try again · Menu: enter
  a ZIP".

### Overlay motion

A toast, the [confirmation card](#confirmation-card), the menu sheet and Up Next in it share one enter and exit
(`ovm_t` in `patch/ui_kit.h`: `ovm_enter`, `ovm_exit`, `ovm_level`, `ovm_alpha`, `ovm_shift`, `ovm_scale`), each
interruptible: a turn mid-run starts from where it is.

- **Toast:** rises 10 px and fades in over 200 ms `cubic_out`; when it closes it stays painted over the page,
  sinking 4 px and fading over 150 ms `cubic_in` (`feedback.c` `feedback_toast_leave`).
- **Confirmation card:** the scrim and the card fade in together over 200 ms `cubic_out`; an answer fades them out
  over 120 ms, linear, and the answer runs once the card has gone. The approved scale from 1.06 is not drawn yet
  (a `ponytail:` in `feedback.c`). Stock's own confirm, which closes itself, appears and goes at once.
- **Menu sheet, and Up Next in it:** rises 260 ms `cubic_out` while the page behind recedes to 94% and dims and
  the frost fades in; falls 180 ms ease-in (quadratic), from where a rise left it (`menu_modal.c` `sheet_run`).
- Under Reduce Motion the toast and the card appear at once and fade out over 120 ms; the sheet fades only, 150 ms
  in and 120 ms out; nothing moves or scales.
- A toast's and the card's fade is their dialog's opacity, which the shared frame timer sets (`ovm_bind`), never the
  dialog's own paint: stock skips painting a widget whose opacity is under 9, so a dialog at 0 would stay there.

### Gestures

- **Swipe back**, as iOS's: a finger that comes down within 24 px of the screen's left edge and
  drags right takes the page with it, the status row staying; what it uncovers is the page under, drifting in
  from 30% left and lightening as a pop's does (still and undimmed under Reduce Motion; on Home's lists, black). Let go
  past 40% of the width, or flicked right, and the page slides on off the right edge and Return
  goes back; let go short and it springs back. Both take 160 ms from where the finger let go, `cubic_out`, Reduce
  Motion's too: they finish what the finger did.
  Wherever Return leaves a page: a page over another, and on Home the list a row opened
  (Settings, Streaming). A drag up or down from the edge scrolls as anywhere; a sideways drag that
  starts mid-screen stays the screen's own (Now Playing's pages, Cover Flow, the letter strip,
  sliders). Not on Home's own list, dialogs, Control Center or the keyboard
  ([internals](internals.md#swipe-back)).

### Motion

- **Page push and pop with parallax**, as UINavigationController's: the new page slides in from the right over
  220 ms, `cubic_out`, while the page under drifts 30% of the width left and darkens to a quarter black; a pop is
  the reverse over 200 ms. The status row stays put, while Home's raised rows and art drift and darken with Home
  in the row beside it (`PAGE_SLIDE_MS`, stock's
  window animator, `page_parallax.c`; [internals](internals.md#page-parallax)). Home's Settings and Streaming lists slide
  in over the same 220 ms.
- **Now Playing's art settles on pause**, as Apple Music's: paused, it shrinks to 88% about its center over 280 ms,
  `cubic_out`; played, it grows back over 320 ms, no overshoot. A turn mid-way starts from where it is; a page opened
  on a paused song shows it small at once. The play/pause glyph is the cue, so Reduce Motion keeps the art its size
  ([internals](internals.md#now-playing-motion)).
- **The cover travels into Now Playing**, as Apple Music's shared element: opened from Home (its art pane) or an
  album page (its header), the cover lifts off the page as one card and travels to Now Playing's art over 420 ms on a
  damped spring (response 0.42 s, damping 0.86) along a gentle arc, 14 px high at its middle, under a soft shadow
  that is darkest mid-flight; its box, crop and corners morph the whole way (from the pane's 179 x 320, square
  corners and 1.10 zoom, the pan where Home had it, to the art's 150 x 150, 6 px corners and the whole cover), never
  squashed. Around it the page fades and slides 28 px left, 40 ms behind the card, and Now Playing comes in from
  0.96 of its size; Return runs it back to the cover it came from, the pane's vignette coming in over the last 45%,
  so the card lands on the pane as Home shows it. Each page is drawn without its own cover, so the cover is never on
  the screen twice. Approved from the `np-home-transition` mock-up on 2026-11-20, with a lite variant (the same morph
  on a critically damped spring over 340 ms, straight, no shadow, no scale or slide: plain cross-fades) one build
  define away (`AZ_HERO`). Opened from anywhere else Now Playing opens at once, as before. Reduce Motion
  cross-fades both ways over 150 ms ([internals](internals.md#now-playing-motion)).
- **Now Playing's track change**: on Next, Previous, a song ending or a headset's skip the old art and lines slide 16 px
  the way the queue moved (left for Next) and fade out over 120 ms, `cubic_in`, while the new ones come in from 16 px
  the other side over 200 ms, `cubic_out`, from 60 ms: 260 ms in all. The times, the bar and the heart change at once;
  a skip mid-run starts from the frame on screen. Reduce Motion cross-fades the two in place over 150 ms.
- Selection bar: moves with the wheel, no animation of its own beyond the list's scroll.
- Nothing pulses, glows or bounces, a list's end included, with two deliberate exceptions: Control Center's
  landing (an 8 px overshoot and one 2 px undershoot over 280 ms as it comes to rest open, [Control
  Center](#control-center)), and Cover Flow's end, where a step past an artist's last album kicks the covers a
  fifth of a stride on and back (`CF_BOUNCE`).
- **One helper** (`patch/ui_kit.h`, Motion): every animation takes its duration, curve and frames from it. A
  motion has a kind: *spatial* (a slide, a scale, a spring, a lean), *decor* (a loop that says nothing new: the
  charging sweep, Up Next's level bars) or *feedback* (a finger followed, a value or progress: swipe back, the volume
  bar's ease, the progress fill, the marquee, spinners). While any runs, one shared 16 ms timer repaints the top window
  (and the status row's play box, through `motion_hook`), and it stops itself after the last one ends.
- **Reduce Motion** (Settings > Display, the last row: a switch captioned "Fades in place of slides, zooms and
  bounces", `IPOD REDUCEMOTION`, off unless chosen; it applies from the next animation, no restart). On: a page's
  push and pop is a 150 ms cross-fade (AWTK's `fade` window animator); Home's sub-list swap and a folder level in
  place change at once; Control Center lands still; tug to tilt is off (the bar steps without leaning); Cover Flow
  stops at its end with no bounce; the remembered-row recall, the captioned row's grow, Edit Library's slide-aside,
  the radio pill's grow and the switches' knob are at their end at once; Now Playing's art keeps its size on pause, and a track change and the cover into Now Playing cross-fade in place; the status row's bars and pause swap at once; the charging sweep stands at the level and Up
  Next's level bars stand still; a toast and the confirmation card fade only (150 ms in, 120 ms out). Kept: swipe
  back's finger tracking and its finish (the page under it still and undimmed), the selection bar, wheel scroll, Cover Flow's wheel step
  (the scroll itself), the volume, brightness and progress bars, the marquee and its fades, the scroll indicator's and
  A-Z index's fades, and spinners.
- **Marquee fades.** A scrolling text (the selected row's title, Now
  Playing's title, artist and album) is clipped to its own column, inside the glass and before any
  value or chevron on its row. A 12 px fade covers its right end while the text goes on past it,
  and its left end while it is scrolled away from its start, so it rests crisp; over it the text
  itself fades out, whole at the inside to gone at the end, onto whatever is behind it (the Canvas,
  the selection's blue, a destructive answer's red, a cover), never a band of a guessed color. A
  truncated text that is not scrolling keeps its ellipsis (`list.marquee.fade`).
- **A value cut off scrolls too** (the user's, 2026-11-16, from About's CFW. Version): on the selected
  row the marquee takes the first text cut off at its column, the title, else a key/value row's value,
  so any value too long for its row reads in full when its row is selected (`navigation.c`
  `marq_moved`); every other row's keeps its ellipsis, except a [label/value row](#labelvalue-rows)'s,
  which fades. That holds for stock's rows too, whose labels
  wait on their parent's focus (a Wi-Fi network's name, user 2026-11-16: they clipped).
- **Now Playing's lines** move as one marquee: the title, artist and album that overflow hold at
  rest 2.75 s, slide left together at 35 px a second (the longest line's pace; the others take the
  same time) until each line's end meets its column's end, hold there 1.5 s, and go back to rest
  together. A line slides only what overflows, so part of it always shows: none goes blank. The progress bar's fill moves a pixel at a time between the player's
  seconds rather than a second's width at once.

## Screens

Vamp's screens mapped to the patterns above, most-seen first. The inventory behind this
table is the 126-screen survey in the design notes; screens not listed follow their nearest
pattern.

| Vamp screen                       | Pattern                     | Notes                                                                                                      |
| ----------------------------------- | --------------------------- | ---------------------------------------------------------------------------------------------------------- |
| Home (Full)                         | List row                    | Home uses the Full layout in this theme: the concept's menu is a full-width list. No page title: the clock, centered. Seven rows from y 58, all at rest; a list with more (Settings' eight) fades the rows below into black from y 266 to 316, and once scrolled runs on under the status row to the screen's top edge ([List edges](#list-edges)); scrolled to the end the last row's foot is at y 282: every list's ending ([Corners](#corners)) |
| Home (Split)                        | List row beside art         | Rows from y 40 (just under the status row, the clock at its left) to x 204 (bar 16 to 188, chevrons 176); the art from x 196 and the top edge to the edges: the playing cover panning slowly while a song plays, else the library's covers in turn, each zooming and drifting and cross-fading into the next; the art's top 50 px and bottom 40 px darkened toward 25% brightness (a vignette, approved as V4 on 2026-11-15); the status row's play state and battery over it at the right (the clock at the row's left, over the list), each glyph white or black by contrast with the darkened art under it. Seven rows, all at rest (the last's foot at y 264); a list with more (Settings' eight) fades the rows past them into black within 56 px of the glass's bottom edge and, once scrolled, the row above y 40 within 34 px of its top edge, both following its rounded corners |
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
| Cover Flow                          | Own                         | Covers on black, no reflection floor; the caption uses Song title and Artist roles; an album's tracks as Album tracks; the covers with the clock in the status row as Home |
| Playlists                           | [Playlists](#playlists)     | On-The-Go first; 44 px rows with a 36 px picture or tile                                                   |
| A playlist's songs, On-The-Go       | [Playlists](#playlists)     | Action rows, then the songs with the artist on the right                                                   |
| Add Songs                           | [Add Songs](#add-songs)     | The chooser, then artists, albums and songs with marks                                                     |
| Hold menu, More                     | [Menus](#menus)             | The menu sheet over the page                                                                               |
| Settings and its pages              | Key/value and switch rows   |                                                                                                            |
| Automatic Time Set (time zones)     | Switch rows, then a Picker  | Automatic Time Set's switch, then Automatic Time Zone's ([captioned](#list-row): "Sets the zone from your network's location"; on by default, [internals](internals.md#automatic-time-zone)), then the zones as a picker: the saved one pinned first with its check, the rest west to east by the offset they have now, ties by city; each a key/value row, the city and its offset ("Chicago", "UTC-6", Text Secondary), the checked row's value clear of the check; no A-Z strip, as the order is by offset |
| About                               | [Label/value rows](#labelvalue-rows) | Model, Firmware (V1.36), the product's version under its name, Capacity and Available with their unit ("779.72 GB"), Wi-Fi Address, IP Address ("Not connected" with no network); the wheel walks the rows and nothing opens; values in Text Secondary |
| Pickers (Play mode, Sort, Language) | List row                    | The current choice carries a 12 px Accent Blue check on the right column                                   |
| Storage                             | Storage bar                 | [Progress and sliders](#progress-and-sliders): the bar, its legend rows (Downloads and Stream Cache open pages) and two footers |
| Storage > Downloads                 | Key/value rows              | Limit (its picker the menu sheet's), then each item: Phosphor `arrow-circle-down` (18 px, Accent Blue) for one the user picked or `arrows-clockwise` (Text Secondary) for one the app keeps itself, white on the selection, centered on x 33, its name at x 52, size and chevron; a footer (the mock's second line, "Auto items refill on the next Sync.", waits for the app to keep items on its own) |
| Slider settings                     | Slider page                 | [Progress and sliders](#progress-and-sliders)                                                              |
| Equalizer (Simple)                  | Vertical slider             | Its [tabs](#tabs), then rows: the switch, Presets, Reset                                                   |
| Wi-Fi                               | Switch row, then a section  | Search Again row; "Networks" after 12 px, the one header, "Searching..." on its line; glyphs at right; the network joining, joined or whose join failed is the first row under it, one line like the rest (in scan order), with the list's spinner, the Accent check (and its trash) or the Negative `warning-circle` before its lock and signal, and "Connecting…", "Connected", "Wrong password" or "Couldn't connect" as a [caption](#list-row) under its bar while selected; a press moves the network there with the selection; a failed one stays first until the next join or scan (variant E, the owner's, 2026-11-23); Reset Network Settings last |
| Bluetooth                           | [Bluetooth](#bluetooth)     | Devices first under My Devices and Other Devices, the connected one two lines ("Connected · LDAC · 72%" after the Positive dot); then Audio |
| A Bluetooth device                  | [Bluetooth](#bluetooth)     | The path and its codec line, then Battery, Disconnect, Volume Limit, Volume Curve, Rename and Forget This Device |
| Bluetooth Stopped                   | [USB](#usb)'s mode page     | The warning on an orange circle, the restart's countdown and Not Now (`bt_stopped.c`)                      |
| Guest Play and its pages            | [Guest Play](#guest-play)   | The prompts on the confirmation card, the guest's Now Playing, the settings, Let a Guest Connect, Hotspot  |
| Bluetooth receiver (a phone playing to the Q2) | [USB](#usb)'s mode page | The accent circle with the Bluetooth glyph, the phone's title over its artist ("Playing from Phone" over "Wheel sets the volume" before it names one), the codec in the pill (`bt_recv.c`) |
| Update Local Music                  | Progress                    | Counts as key/value rows under the status row                                                              |
| Shuffle, Repeat changes             | Confirmation                | New in this theme                                                                                          |
| SD Card Update                      | [Software Update](#software-update) | A spinner while it checks, the confirm, then mode pages                                         |
| Quick Settings                      | [Control Center](#control-center) | The status row's clock at its left, as every page's                                              |
| Search                              | [Text entry](#text-entry)   | The query on the first line, four single-line results, the letter strip ([ipod](ipod.md#search))            |
| New Playlist, Rename Playlist       | [Text entry](#text-entry)   | The letter strip, the name large over it ([ipod](ipod.md#name-strip))                                       |
| Keyboard                            | [Text entry](#text-entry)   | None shows: a text box is the letter strip's ([ipod](ipod.md#keyboard))                                    |
| Buttons locked                      | Own                         | A padlock leftmost in the status row while Buttons Lock is on; a blocked press shows a #1C1C1E card over the page dimmed to 45%: a 56 px padlock, "Buttons Locked" 16 px Medium and the hint 13 px Secondary, for 1.5 s |
| A card app's QR page (sign-in)      | Own                         | The code on a white plate (166 px, radius 12) centered under the status row, the app's note ("Scan with your phone to sign in") 17 px Medium white and "or open ADDR · PIN 482 913" 15 px Text Tertiary under it. Once the app reports a step, a mode page: one solid 92 px circle (Accent for phone and check, #34C759 done, #FF453A fail and error) with a 44 px white Phosphor fill glyph (`device-mobile`, `arrows-clockwise`, `check` bold, `cloud-slash`, `x` bold), the title 22 px Medium and one line 15 px Text Secondary, the stack centered between the status row and the bottom; the wheel does nothing |

The concept's Apple Music, Apple TV and CD sync pages have no Vamp equivalent and are out of scope; its storage
page became Settings > Storage. Spotify's pages follow the list patterns.

## Do and don't

| Do                                                   | Don't                                       |
| ---------------------------------------------------- | ------------------------------------------- |
| Separate with space and gray                         | Add boxes, cards or panels behind lists     |
| Use Accent Blue for the one selected or active thing | Tint icons, headers or second lines blue    |
| Keep every list on the 32 px grid                    | Mix pitches in one list                     |
| Let titles scroll when selected                      | Shrink type to fit                          |
| Inset from the glass with `corner_x`                 | Put anything in the corners the glass hides |
| Write sentence case                                  | Write capitals or add letter spacing        |

## What the engine needs

Built, as `theme.xml` keys and geometry fields ([internals](internals.md#themes)):

- The color tokens and the type scale in one place: `tools/theme.py` `TOKENS` (to `patch/tokens.h` and
  `tools/art.py`'s inks), `tok()` and `TYPE()` in `patch/ui_kit.c` and `patch/ui_kit.h`.
- OLED as the one built-in theme, standing alone: `assets/themes/oled/theme.xml`, index 0, extending
  no other theme, on its own grid (its `<geometry>`). A unit left on another theme by an older build takes
  OLED's preset once at boot ([ipod](ipod.md#theme)).
- Font faces as a table, with Inter Regular and Medium and tabular digits.
- A theme's own accent tones, and the selection bar's radius and inset on every row.
- A status row in place of a bar band (`status_y`), its title and its battery's end `STATUS_ROW_INSET`
  in from the sides, its glyph slots left of the battery (`patch/status_row.c`: `status_slots_left`,
  `status_slots_paint`, `status_slot_at`) and its clock beside them, or centered where there is no title; lists that scroll a
  whole row at a time and stop with the last row's foot on `list_end`, the rows past it fading (`fit_rows`).
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
- Menus as the menu sheet over the page ([Menus](#menus)): the set's `modal`, `patch/menu_modal.c`, the
  Phosphor symbols as the theme's own art.
- Home's Split on the grid: the set's `split_row`, `split_art`, `split_art_up` and `split_inset` (the art from
  x 196 and the top edge to the edges, its top 58 rows painted by the bar with the status row's clock, glyphs
  and battery over them, all in one ink, white or black, whichever has the higher WCAG contrast against the art's
  mean luminance under them, weighted by each glyph's coverage and held 0.03 past the bound against flicker; the
  charging green and low red keep their colors (`home_ink`, approved from `mock_split_top.py` on 2026-11-14; one
  ink for the row from `mock_status_icons.py` on 2026-11-16, as one ink a glyph put dark bars beside a white
  clock), the playing cover panning slowly
  across its 110 % zoom with an eased turn at each end and 2 % of the zoom kept clear of the pane at each edge, so a
  cover's own border never shows, on Now Playing's row (there only while something is playing or paused), and with no
  cover to show (a song without art) the placeholder tile the same way, from the top edge under the status row; on every
  other row the library's covers in turn as an iPod classic's menu shows them, cross-fading from the frame as it
  stands as the wheel moves between the two, each zooming between 1 and 0.9 of the cover and drifting once over its 9 s, 1.5 s cross-fades between covers
  and on a track change (docs/ipod.md#home, `slideshow.c`)), the main menu from y 40, its top rows under the status row (approved from
  `mock_overflow.py` on 2026-11-15), and the lists Home's rows open as full-width pages with the art hidden
  (`home.lists.full`).
- Settings > Storage and its Downloads (`patch/storage.c` `storage_paint`): the storage bar and its legend, the
  items' marks, the values beside their chevrons and the footers; and the song rows' marks for where a song lives
  (`catalogue.c` `catalogue_row_mark`).
- The toast, the confirm as the confirmation card and the empty note (`patch/feedback.c`), and the confirmation after a shuffle.
- The confirmation card (`patch/feedback.c` `confirm`, [Confirmation card](#confirmation-card)).
- One flat selection bar for pages that paint their own rows (`patch/list_marks.c` `sel_bar_flat`: Sleep Timer's
  switch row, Scan Music Now, the slider page's and the EQ's rows, Add Songs), the theme's inset and radius.
- One mode page painter for USB's pages, a card app's sign-in steps, SD Card Update and Bluetooth's receiver
  (`patch/feedback.c` `mode_page_paint`, `mode_page_t` in `patch/ui_kit.h`). The plug-in screen keeps its own
  approved layout ([Plug-in screen](#plug-in-screen)).

Still to build:

- The confirmation for Repeat and Now Playing's mode changes.
- The Songs legend, "Plain: yours · arrow: downloaded · cloud: streams" as a footer under a song list (`mock_radio.py`
  `songs_marks`): a stock table's rows are pooled and its scroll end has no room for a footer; the manual explains the
  marks meanwhile.
