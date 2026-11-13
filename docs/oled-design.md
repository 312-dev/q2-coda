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

| Property | Value | Notes |
|---|---|---|
| Panel | 375x320, upright | The concept's screens are near square; the Q2 is wider, so layouts gain width, not rows |
| Glass corners | 80 px radius (`GLASS_R`) | Hides up to 46 px at each end of a row 8 px from an edge; see [Corners](#corners) |
| Status bar | 30 px | Concept: about 0.10 of screen height |
| Content area | 375x290 under the bar | |
| Panel type | LCD (`lcd_h0209s`), not OLED | Black is a colour here, not "off": keep large areas black anyway, it is the look |

### Corners

The concept puts the clock in the top-left corner and the battery in the top-right. On the Q2
those spots are under the glass. So:

- The status bar keeps Q2 Pod's arrangement: clock centred, play state and battery inset from
  the right by `corner_x`, the page title (when shown) inset from the left by `corner_x`.
- No text or control may start inside `corner_x(y, h)` for its row. In the middle of the
  screen, from y 80 to y 240, the inset is 0.
- A list ends at the last row whose text clears the glass at the text inset. With the 30 px
  bar and 32 px rows that is the row starting at y 254: the slots under the bar are y 30, 62,
  ..., 254, eight in all, so a list page shows its title and seven rows. The strip below
  (y 286 to 320) stays black; a selected row never lands there (Q2 Pod's `list_end`).
- The selection bar on a row near a corner is drawn inside the glass's curve, never clipped
  by it.

## Colour

All colours are opaque. Alpha is not used for text: greys are stated as greys.

| Token | Hex | Use | Concept measurement |
|---|---|---|---|
| Canvas | `#000000` | Every background | `#000000` on all grid screens |
| Text Primary | `#FFFFFF` | Row titles, page titles, song title, times | `#FCFCFC` to `#FFFFFF` |
| Text Secondary | `#ABABAB` | Artist, album, "1 of 10", values in key/value rows ("Off", "Flat") | `#AAAAAA` to `#B2B2B2` |
| Text Tertiary | `#858585` | Second lines ("1,234 Songs", "Connected"), section headers, captions | `#7D` to `#8C` by ratio (est) |
| Chevron | `#999999` | List chevrons on black | `#8C8C8C` read on a 1 px stroke, so lighter in truth (est) |
| Divider | `#222222` | 1 px rules between two-line rows only | `#222222` |
| Track | `#333333` | Unfilled progress, slider and storage tracks; switch off | `#313131` |
| Accent Blue | `#2160D8` | Selection bar, progress fill, switch on, slider knob ring, big status glyphs | Bar `#1544B2` to `#225BCB`, progress `#2368D9` |
| On Accent | `#FFFFFF` | Text and chevrons on the selection bar | |
| Positive | `#30D158` | "Connected" dots only | Green dot on Bluetooth |

The concept grades its blue differently in each image. Accent Blue is one value between the bar
and the progress fill, chosen so white text on it reads at about 5:1. Use it everywhere; do not
add a lighter or darker blue.

### Light Mode

The concept has no light screens; Q2 Pod's Light Mode exists and must keep working. The light
half flips greys, not hues:

| Token | Dark | Light |
|---|---|---|
| Canvas | `#000000` | `#FFFFFF` |
| Text Primary | `#FFFFFF` | `#000000` |
| Text Secondary | `#ABABAB` | `#5C5C5C` |
| Text Tertiary | `#858585` | `#6E6E6E` |
| Chevron | `#999999` | `#8A8A8A` |
| Divider | `#222222` | `#E5E5E5` |
| Track | `#333333` | `#D9D9D9` |
| Accent Blue | `#2160D8` | `#2160D8` |

## Type

Face: **Inter** (SIL OFL 1.1), Regular and Medium, subset as the build subsets its other faces,
with tabular digits so a running time never changes width. Inter stands in for the concept's
San Francisco, which cannot ship. See `assets/fonts/` for the files and licence.

| Role | Face | Size | Colour | Example |
|---|---|---|---|---|
| Status clock | Inter Medium | 17 px | Text Primary | 9:41 |
| Page title | Inter Medium | 20 px | Text Primary | Now Playing |
| Row | Inter Regular | 20 px | Text Primary | Playlists |
| Row value | Inter Regular | 20 px | Text Secondary | Off |
| Row second line | Inter Regular | 15 px | Text Tertiary | 1,234 Songs |
| Section header | Inter Regular | 15 px | Text Tertiary | Devices |
| Song title | Inter Medium | 22 px | Text Primary | Time |
| Artist, album | Inter Regular | 18 px | Text Secondary | Pink Floyd |
| Track position | Inter Regular | 15 px | Text Secondary | 1 of 10 |
| Times | Inter Regular | 16 px | Text Primary | 3:47 and -3:22 |
| Confirmation label | Inter Medium | 22 px | Text Primary | Shuffled Songs |

Rules:

- **Sentence case** for everything the theme writes (status, captions, values). Menu items and
  page names stay as the firmware names them, which is Title Case ("Sound Check"). Never all
  capitals.
- **Size steps are 15, 16, 17, 18, 20, 22.** Nothing else.
- **Row pitch is about 1.6 times the row size** (32 px for 20 px type), as the concept's is
  about twice its cap height.
- **Truncate, then scroll.** A long title ends in an ellipsis; the selected row's title scrolls.
  Never shrink type to fit.

## Spacing

All lists share one grid.

| Token | Value | Concept |
|---|---|---|
| Row pitch | 32 px | 0.080 of screen height; eight slots under the bar (see [Corners](#corners)) |
| Two-line row pitch | 52 px | 1.8 times a row, as measured |
| Text inset (left) | 24 px | 0.061 of screen width; the clock, title and rows share this column |
| Chevron inset (right) | 20 px to the chevron's right edge | Concept 0.092 W on a narrower screen |
| Value inset (right) | 20 px to the value's right edge | Same column as chevrons |
| Leading icon or art | 16 px from the left, text at 56 px | Icons 20 px, art 36 px in two-line rows |
| Bar inset | 8 px each side | The concept's insets are uneven (2 to 12 px); this picks one, symmetric |
| Bar radius | 6 px | About 0.15 of the bar's height, as measured |

## Components

### Status bar

- 30 px tall, Canvas background, no rule under it.
- Clock centred, Inter Medium 17 px. Right group: play state (filled triangle while playing,
  two bars while paused, nothing when stopped) then the battery, inset by `corner_x`.
- The page title is not in the status bar. It is the first line of the content
  (see [Page title](#page-title)), except on Home and Now Playing, which have none.
- Battery: outline with a nub, filled in Text Primary; under 20 % the fill is the only red on
  any screen (`#FF453A`).

### Page title

- One line, Inter Medium 20 px, Text Primary, at the text inset, 32 px tall: it takes the
  first row slot, so a list under it starts on the grid.
- Shown on every list page except Home. Not repeated in the status bar.

### Selection bar

- Accent Blue, flat, the full row pitch tall, inset 8 px each side, 6 px corners.
- Text and chevron on it are On Accent. Nothing else on the row changes (no bold, no
  indent).
- It moves one row per wheel tick, with no easing longer than the theme's list motion.
- Exactly one per screen. A dialog over a list takes the selection; the list's bar is hidden
  while the dialog is open.

### List row

- 32 px pitch, title at the text inset, chevron at the chevron inset when the row opens
  another page, nothing at the right when it acts in place.
- **Key/value row:** value in Text Secondary, right-aligned at the value inset, no chevron.
  On the selection bar the value is On Accent.
- **Switch row:** a 34x20 switch at the value inset: Track when off, Accent Blue when on,
  white knob. No "On"/"Off" text beside it.

### Two-line row

- 52 px pitch. Title (Row, Text Primary) and second line (Row second line, Text Tertiary),
  6 px between them, vertically centred as a pair.
- Optional leading 36 px square art (Albums) or 20 px icon (Playlists, Podcasts), at 16 px;
  text then starts at 56 px.
- Dividers (`#222222`, 1 px) between two-line rows only, from the text column to the right
  inset. Single-line lists have no dividers.

### Now Playing

The most-seen screen. Art left, words right, time across the bottom.

| Element | Position (x, y) and size | Notes |
|---|---|---|
| Art | 16, 46, 160x160 | Square, 6 px corners. The concept's art is not square in one render; square is the rule |
| Song title | 192, 54 | Inter Medium 22 px, Text Primary, one line, scrolls when long |
| Artist | 192, 86 | Inter Regular 18 px, Text Secondary |
| Album | 192, 110 | Inter Regular 18 px, Text Secondary, up to two lines |
| Track position | 192, 186 | "1 of 10", 15 px, Text Secondary, baseline level with the art's bottom |
| Progress bar | 16, 230, 343x6 | Track `#333333`, fill Accent Blue, round ends |
| Elapsed | 16, 246 | 16 px, Text Primary, tabular |
| Remaining | right edge at 359, 246 | 16 px, Text Primary, with a leading minus |
| State row | centred, 276 | Shuffle and repeat glyphs, 20 px, Text Secondary when off, Accent Blue when on |

No page title, no background art blur, no reflection.

### Progress and sliders

- **Progress:** 6 px, round ends, Track and Accent Blue. Used for playback, scans and
  downloads.
- **Vertical slider (EQ):** 4 px Track line, Accent Blue fill from 0 dB to the value, a 14 px
  white knob. Band labels under it in Text Tertiary 15 px.
- **Storage bar:** 12 px, round ends, segments in Accent Blue, Text Secondary and Track, with a
  legend of 8 px dots and 15 px labels. Segments never use more than three colours.

### Confirmation

Shown for a moment after a mode change (Shuffle, Repeat).

- Centred glyph, 56 px, Accent Blue; label Inter Medium 22 px under it; count in Text Tertiary
  15 px under that. Nothing else on screen but the status bar.
- Dismisses itself after 1.2 s or on any input.

### Dialogs and toasts

- A dialog is a list: its question as the page title, its answers as rows, the selection bar
  on the default answer. No boxes, no shadows.
- A toast is one line of 16 px Text Primary on a `#1C1C1E` pill, 12 px corners, centred above
  the bottom glass curve. It is the only raised surface in the theme.

### Icons

- Line icons, 1.5 px stroke at 20 px, round caps, Text Primary (Text Secondary when inactive).
  Filled only for the play state and the active state of a toggle glyph.
- One icon set per screen. No coloured app tiles: the concept's tinted "For You" squares are
  for content the Q2 does not have.
- Chevrons are 1.5 px open angles, 6x10 px.

### Motion

- Page push and pop: 220 ms horizontal slide, `cubic_out`.
- Selection bar: moves with the wheel, no animation of its own beyond the list's scroll.
- Nothing pulses, glows or bounces.

## Screens

Q2 Pod's screens mapped to the patterns above, most-seen first. The inventory behind this
table is the 126-screen survey in the design notes; screens not listed follow their nearest
pattern.

| Q2 Pod screen | Pattern | Notes |
|---|---|---|
| Home (Full) | List row | Home uses the Full layout in this theme: the concept's menu is a full-width list. No page title |
| Home (Split) | List row beside art | Allowed: list left, art pane right; the pane is square art on black, no placeholder gradient |
| Now Playing | [Now Playing](#now-playing) | |
| Library | List row | |
| Albums, album tracks | Two-line row with art | |
| Artists, Genres, Composers | List row | |
| Songs | Two-line row | Second line is the artist |
| Volume | Progress | A 6 px bar across the bottom of Now Playing, not a panel |
| Cover Flow | Own | Covers on black, no reflection floor; the caption uses Song title and Artist roles |
| Playlists | Two-line row with icon | |
| Hold menu | Dialog | |
| Settings and its pages | Key/value and switch rows | |
| Pickers (Play mode, Sort, Language) | List row | The current choice carries a 12 px Accent Blue check at the value inset |
| Equalizer (Simple) | Vertical slider | |
| Bluetooth | Two-line row | Second line "Connected" with the Positive dot |
| Update Local Music | Progress | Counts as key/value rows under the bar |
| Shuffle, Repeat changes | Confirmation | New in this theme |
| Quick Settings | Own | Tiles become rows: the theme has no tiles |
| Lock screen | Own | Clock 56 px Inter Medium centred, date 16 px Text Secondary, black |

The concept's Apple Music, Apple TV, CD sync and storage pages have no Q2 Pod equivalent and are
out of scope. Spotify's pages follow the list patterns.

## Do and don't

| Do | Don't |
|---|---|
| Separate with space and grey | Add boxes, cards or panels behind lists |
| Use Accent Blue for the one selected or active thing | Tint icons, headers or second lines blue |
| Keep every list on the 32 px grid | Mix pitches in one list |
| Let titles scroll when selected | Shrink type to fit |
| Inset from the glass with `corner_x` | Put anything in the corners the glass hides |
| Write sentence case | Write capitals or add letter spacing |

## What the engine needs

The theme needs these from the engine; each is tracked as its own change:

- A fourth built-in theme (picker order and tests derived from `assets/themes/`).
- Font faces as a table, with Inter Regular and Medium and tabular digits.
- A theme's own accent tones in theme.xml, and the selection bar's radius and inset as keys.
- The status bar's title moved out of the bar into the first content row.
- Now Playing's positions from the theme rather than fixed in `tools/ipod.py`.
- A confirmation screen for Shuffle and Repeat.
- Switch rows, the EQ slider and the storage bar drawn as above.
