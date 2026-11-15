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
| Status row    | glyphs centred on y 37       | No band and no rule: the bar is 58 px of the page's black                               |
| Content area  | 375x262 from y 58            | Seven 32 px rows, y 58 to 282                                                           |
| Panel type    | LCD (`lcd_h0209s`), not OLED | Black is a colour here, not "off": keep large areas black anyway, it is the look        |

The geometry below (the status row, the 58 px list top, the 16 px selection inset, the 28 px right column and
the scroll indicator) is what the user chose from mock-ups on 2026-11-13 (mock-up E and its six-screen
follow-up), in place of a first draft that stepped the selection bar in at the corners and drew the scroll
track along the glass.

### Corners

The concept puts the clock in the top-left corner and the battery in the top-right. On the Q2
those spots are under the glass. So:

- The status row sits low enough that the glass hides at most 18 px at its glyphs' top edge
  (y 31), so its ends can sit on the list's own columns: the title at the 24 px text column, the
  battery ending on the 28 px right column, the clock beside its play state (or centred, on a page
  with no title).
- No text or control may start inside `corner_x(y, h)` for its row. In the middle of the
  screen, from y 80 to y 240, the inset is 0.
- A list ends at the last whole row above the bottom corners: the 32 px slots from y 58 are
  y 58, 90, ..., 250, seven in all, ending at y 282; 52 px rows fit four (y 58 to 266). The strip
  below stays black and a selected row never lands there (Q2 Pod's `list_end` and `list_fit`).
- Every screen is checked against the glass: a screenshot composited with the 80 px mask shows
  no lit pixel under the curve.

## Colour

All colours are opaque. Alpha is not used for text: greys are stated as greys.

| Token          | Hex       | Use                                                                          | Concept measurement                                        |
| -------------- | --------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Canvas         | `#000000` | Every background                                                             | `#000000` on all grid screens                              |
| Text Primary   | `#FFFFFF` | Row titles, page titles, song title, times                                   | `#FCFCFC` to `#FFFFFF`                                     |
| Text Secondary | `#ABABAB` | Artist, album, values in key/value rows ("Off", "Flat")                      | `#AAAAAA` to `#B2B2B2`                                     |
| Text Tertiary  | `#858585` | Second lines ("1,234 Songs", "Connected"), section headers, captions         | `#7D` to `#8C` by ratio (est)                              |
| Chevron        | `#999999` | List chevrons on black                                                       | `#8C8C8C` read on a 1 px stroke, so lighter in truth (est) |
| Divider        | `#222222` | 1 px rules between two-line rows only                                        | `#222222`                                                  |
| Track          | `#333333` | Unfilled progress, slider and storage tracks; switch off                     | `#313131`                                                  |
| Accent Blue    | `#2160D8` | Selection bar, progress fill, switch on, slider knob ring, big status glyphs | Bar `#1544B2` to `#225BCB`, progress `#2368D9`             |
| On Accent      | `#FFFFFF` | Text and chevrons on the selection bar                                       |                                                            |
| Positive       | `#30D158` | "Connected" dots only                                                        | Green dot on Bluetooth                                     |

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
| Divider        | `#222222` | `#E5E5E5` |
| Track          | `#333333` | `#D9D9D9` |
| Accent Blue    | `#2160D8` | `#2160D8` |

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
- **A number, a space, its unit:** "15 s", "1 min", "4000 K"; a percentage takes no space
  ("50%").
- **Size steps are 15, 16, 18, 20, 22** (56 for the confirmation's glyph). Nothing else.
- **Row pitch is about 1.6 times the row size** (32 px for 20 px type), as the concept's is
  about twice its cap height.
- **Sizes are the em.** AWTK sizes a face by its line (ascent less descent), which for Inter is
  1.21 em, so `theme.xml` states each size times 1.21, rounded: 15 is 18, 16 is 19, 18 is 22,
  20 is 24, 22 is 27.
- **Truncate, then scroll.** A long title ends in an ellipsis; the selected row's title scrolls.
  Never shrink type to fit.

## Spacing

All lists share one grid.

| Token              | Value                                        | Notes                                                                              |
| ------------------ | -------------------------------------------- | ---------------------------------------------------------------------------------- |
| List top           | y 58                                         | 14 px under the status row's glyphs                                                |
| Row pitch          | 32 px                                        | seven rows, y 58 to 282 (see [Corners](#corners))                                  |
| Two-line row pitch | 52 px                                        | four rows, y 58 to 266                                                             |
| Text inset (left)  | 24 px                                        | the status row's title, the rows and the art share this column                     |
| Right column       | 28 px from the right edge                    | chevrons, values and the battery's end, on every row: they never move between rows |
| Leading art        | 40 px at x 24, 6 px down its row, text at 72 | 4 px corners, in two-line rows                                                     |
| Bar inset          | 16 px each side, every row                   | no corner stepping: at the bottom row the corner hides 14.5 px                     |
| Bar radius         | 8 px                                         |                                                                                    |

## Components

### Status row

- No band: the bar is the page's black, 58 px, with no rule under it. Its glyphs centre on y 37.
- Left, on the 24 px text column: the page's title in Inter Medium 15 px, Text Secondary, ending
  with an ellipsis 12 px short of the clock's widest ("00:00").
- The clock, in the same face, size and colour, right-aligned 24 px left of the battery's body,
  beside the play state. Home, Now Playing and Cover Flow's covers (but an artist's, titled by the
  artist) have no title, and there the clock is centred across the row (x 187.5) instead; Home in
  Split keeps it at the right, over the art, inked white or black as one glyph with the play state
  and battery (approved from `mock_clock_center.py` and `mock_clock_right.py` on 2026-11-15).
- Right: the play state (Phosphor `play`/`pause`, fill, 13 px, Text Primary, nothing when
  stopped), then the battery, its nub's end on the 28 px right column, the play state's centre
  12 px left of the battery's body.
- Battery, in iOS's proportions: a 25x12 body with 4 px corners and a 1 px `#6E6E6E` outline,
  the charge a solid fill 2 px inside it with 2.2 px corners in Text Primary (under 20 %
  `#FF453A`, the only red on any screen; green while charging), and a 1.5x4.4 nub with 0.8 px
  corners in `#6E6E6E`.

### Selection bar

- Accent Blue, flat, the full row pitch tall, inset 16 px each side on every row, 8 px corners,
  whatever the Corners setting says (`list.selection.fixed`).
- Text and chevron on it are On Accent, the second line too. Nothing else on the row changes (no
  bold, no indent).
- It moves one row per wheel tick, with no easing longer than the theme's list motion.
- A list left by Return opens at its top when it is chosen again; Return lands on the row the
  page behind was left at (`list.reopen.top`, every theme's, from Minimal).
- Exactly one per screen. A dialog over a list takes the selection; the list's bar is hidden
  while the dialog is open.

### List row

- 32 px pitch, title at the text inset, a chevron on the right column when the row opens
  another page, nothing at the right when it acts in place (Home's Shuffle Songs, a song).
- **Chevron:** Phosphor `caret-right`, bold, 15 px, centred 5 px left of the right column:
  Chevron grey, On Accent on the selection.
- **Key/value row:** value in Text Secondary, right-aligned on the right column, no chevron.
  On the selection bar the value is On Accent.
- **Switch row:** every on/off setting, a 34x20 fully round switch ending on the right column, its
  16 px white knob 2 px in: Track when off, Accent Blue when on. On the selection bar: on is a white
  track with an Accent Blue knob, off a 1.5 px white outline round the white knob. No "On"/"Off"
  text beside it. A setting with two or three named choices (Gain's High and Low, Charge Limit's
  Off and 80%) is a key/value row that Centre cycles; the value is never part of the label.
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
  (the last 200 ms of it).

### Two-line row

- 52 px pitch, four to a page. Title (Row, Text Primary) and second line (Row second line, Text
  Tertiary, On Accent on the selection): the title's baseline 25 px down the row, the second
  line's top 32 px down.
- Optional leading 40 px square art with 4 px corners (Albums) at x 24, 6 px down; text then
  starts at 72.
- Dividers (Divider, 1 px) between two-line rows only, from the text column to the right column,
  not drawn on either edge of the selected row. Single-line lists have no dividers. Q2 Pod's own
  two-line lists (Era, View Queue, Most Played, Ratings' lists) have them as stock's do, and end
  on the last whole row above y 282 (`page_fit`).

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
- **The Shuffle row** stands first on Songs, a playlist's songs and an album's tracks, in place of
  Play All (a song already plays the rest of its list in order): Phosphor `shuffle` (regular, 20
  px) on the text column and "Shuffle" 28 px after it, both Accent Blue, white on the selection.
  The wheel reaches it from the first song; Centre shuffles the list, and Return from Now Playing
  lands on it again. Sort has no place on the row: it is the hold menu's Sorting row
  ([Menus](#menus)).
- **Folder view** reads as an album: the same 32 px rows. A song file is a track, its number in the
  album's column, the title from the library's tags (found by the file's path) at x 50 and its
  length on the right column when known; a file the library doesn't know shows its file name
  without the extension, a leading track number ("01. ") or the "Artist - " every such name in the
  folder starts with. A folder has Phosphor `folder` (regular, 20 px, Text Secondary, white on the
  selection) centred on x 34, its name at x 52 and the chevron on the right column; the card's root
  too.
- Which list is which comes from its page: Songs' page (`allmusic_page`, which also holds a genre's songs
  and the other lists stock opens there) is mixed, the artist page's songs are an artist's, the album page is an
  album, Folder view (`folder_page`) a folder, and an album's tracks opened from Cover Flow are an album too (their
  status row names the album, with no row of its own above the tracks). Lists Q2 Pod draws itself (Most Played,
  Long Songs, Ratings) keep their own rows.
- Streaming's rows are one line, 32 px, each service's mark (20 px, monochrome, no disc) centred on x 34 in Text
  Secondary, white on the selection, its name from x 52: Phosphor's `spotify-logo`, `app-window` (a card app without
  its own icon), `radio`, `airplay` and `sliders-horizontal` (Configuration), and TIDAL's own four diamonds, drawn in
  `tools/art.py` (Phosphor has none); TIDAL and Tidal Downloads share it.
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
| State row      | centred on y 276, 44 px apart  | Phosphor `shuffle` and `repeat`, bold, 22 px: Accent Blue when on, Text Secondary when off |

No page title, no background art blur, no reflection.

Built as the `oled` set's `np_art` and `np_bar_y` (`tools/ipod.py` `np_grid`) with the theme's
`playing.art.radius`, `playing.artist.font`, `playing.scrubber.height` and `playing.modes.icons`.
The album is one line that scrolls, as the title and artist do, rather than two. What the design
has no place for keeps a place of its own:

- **More and the heart** stand at the right end of the art's bottom line (baseline 208), on the
  art's page, so a swipe takes them away with it: Phosphor `dots-three` and `heart` (regular, 20 px, Text Primary;
  More pressed in Text Secondary), the dots' ink ending on the right column, the heart `fill` in
  Accent Blue while the song is a favourite.
- **The page dots** sit under shuffle and repeat, centred on y 302; the lyrics, the track's details
  and the visualizer are still the swipe's other pages, each ending 5 px above the bar.
- **Shuffle and repeat** are two controls on stock's play mode button: a tap on shuffle turns it
  on or off, a tap on repeat steps Off, All, One (repeat-once's glyph for One), and each keeps the
  other as it was. Stock's Shuffle never ends, so shuffle with repeat Off plays on as with All.
- **The volume** and the scrub draw on the bar where it is. The volume (the "A: iPod speakers"
  mock-up) keeps the bar's rows, inset to x 48 to 323, white on the track, with Phosphor's
  `speaker-simple-none` and `speaker-simple-high` (fill, 16 px, Text Secondary) centred at x 32
  and 339 on its centre line, and no number; the times' row stays blank meanwhile. The scrub is
  a 14 px white knob on the fill's end. The A-B markers follow the bar
  across but keep stock's row, y 308 on screen, under the page dots. ponytail: a marker within
  about 14 px of either end of the bar falls under the bottom corners' glass; moving them onto
  the bar needs the y that stock's placement (`0x53baa4`) writes made per set.

### Progress and sliders

- **Progress:** 6 px, round ends, Track and Accent Blue. Used for playback, scans and
  downloads.
- **Slider page** (Max Volume, Default Volume, Channel Balance, Brightness, Crossfade, Resume Songs,
  Night Shift, Wheel Sensitivity): the status row names it; a feature with a switch has it as the
  first row, selected; the value in Inter Medium 22 centred on y 150; a 6 px track from the text
  column to the right column with its top at y 186, Track behind and Accent Blue filled to a 14 px
  white knob (Channel Balance fills from the centre); the range's ends in 15 px Text Tertiary
  centred on y 214, at the two columns. The wheel moves it; there are no +/- buttons.
- **Vertical slider (EQ):** 4 px Track line, Accent Blue fill from 0 dB to the value (Text Tertiary
  while the EQ is off), a 14 px white knob, ringed 2 px in Accent Blue while the wheel is on it.
  The band's name and gain under it in 15 px Text Tertiary, Text Primary on the band the wheel is
  on. Above the three, the EQ's switch, Presets and Reset are rows.
- **Storage bar:** 12 px, round ends, segments in Accent Blue, Text Secondary and Track, with a
  legend of 8 px dots and 15 px labels. Segments never use more than three colours.

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

- A dialog is a list: its question as the page title, its answers as rows, the selection bar
  on the default answer. No boxes, no shadows.
- A confirm (stock's `confirminfo_dialog`: Shut Down, Restart, the three Resets, Delete Song, Delete
  Playlist and the rest) is that dialog: the question in the status row in sentence case, naming
  the object where it can ("Delete playlist “a”?"), then **Cancel**, selected when it opens, and
  the verb that says what the other does ("Shut Down", "Reset", "Delete"), never "OK". Delete
  Song's "Also delete source file" is a third row with a switch on the right column.
- A toast is one line of 16 px Text Primary on a `#1C1C1E` pill, 12 px corners, 32 px tall,
  as wide as its text and 16 px each side, centred on y 252 (y 236 to 268), above the bottom
  glass curve. It is the only raised surface in the theme. The page stays in sight round it,
  with its status row and its selection; it lasts 1.5 s (stock's own toasts their own time).
  Words in sentence case ("Added to Queue", "Name can't be empty").
- Every in-place action says what it did in a toast: the hold menu's Play Next ("Playing Next"),
  Add to Queue ("Added to Queue") and Add to and Remove from Favourites ("Added to Favorites",
  "Removed from Favorites").

### Empty and offline

- A list with nothing in it keeps its action rows (New Playlist..., Check for Updates) as
  normal rows with the selection; the message is one line of 16 px Text Secondary centred in the
  free area below them, down to y 282, with an optional hint in 15 px Text Tertiary 24 px lower.
  No fake rows, and no Shuffle row over nothing: Favorites, Recent and the other song lists hide
  it and say what is missing ("No favorites", hint "Hold Play/Pause on a song to add it").
- Offline and missing things use the same form (Spotify's "Spotify isn't on the card", the App
  Store's "No apps listed" under its Check for Updates row, one line in the list's pitch). TIDAL
  with no Wi-Fi opens a page of its own titled TIDAL: "Not connected", hint "Turn on Wi-Fi in
  System > Wireless".
- Busy: no stock spinner (Playlists' while it adds a song); the toast that follows says it is done.

### Menus

Actions on the highlighted item (the hold menus, Now Playing's More and a playlist's More) open as
an inset modal over the page, which stays on screen dimmed half way to black; nothing is blurred.

- **Panel:** opaque `#1C1C1E`, 259 px wide from x 58, top at y 58, 16 px corners, 8 px padding, no
  title (the row behind says which item). Rows are 32 px, at most six in view, then they scroll
  inside the panel; the wheel's scroll indicator stands in the panel's right padding, never past its edge.
- **Rows:** Inter Regular 20 at the panel's 16 px inset; an action's Phosphor symbol (regular,
  19 px) at the right in Text Secondary, white on the selection. The selection is the list's bar, 8
  px in from the panel with 8 px ends.
- **A setting** (Sorting, Speed, Shuffle, Repeat, Group by) has its symbol before the label, the
  label 26 px further in, and its value alone at the right in Text Secondary; Centre opens a picker
  in the same panel, the current choice with the accent's check before its label.
- **Rating** (Now Playing's Centre cycle, a menu's Rate) is a picker in the same panel: Unrated, 1 Heart
  to 5 Hearts, the current one checked; Centre keeps a row's rating and closes, Return closes.
- **Gaps:** a setting heading the menu stands 12 px above the actions; a destructive row (Delete
  Song) comes last, 12 px below them, and asks before it acts. Space, never a rule.
- The menu opens on its first action, lit; the wheel moves, Centre picks, Return closes it back to
  the row it came from.

Built by `patch/menu_modal.c` under a set with `modal` (OLED's). Now Playing's More and a
playlist's More are stock pages, which such a set loads as dialogs (`tools/ipod.py` `modal_page`).

### Icons

- One set: [Phosphor Icons](https://phosphoricons.com) (MIT), `@phosphor-icons/core` 2.1.1. The
  SVGs the theme uses are vendored in `assets/icons/phosphor/` with their licence, and
  `tools/art.py` draws the theme's bitmaps from them at build time, in final colours per half.
- Weights by role: `fill` for the play state and a favourite's heart, `bold` for chevrons and Now
  Playing's and the confirmation's shuffle and repeat, `regular` for row symbols and Now Playing's
  heart and More. Sizes are the design's: 13 px play state, 15 px chevron, 20 px row symbols,
  heart and More, 22 px state row, 56 px confirmation.
- No coloured app tiles: the concept's tinted "For You" squares are for content the Q2 does
  not have.

### Motion

- Page push and pop: 220 ms horizontal slide, `cubic_out`.
- Selection bar: moves with the wheel, no animation of its own beyond the list's scroll.
- Nothing pulses, glows or bounces.
- **Marquee fades.** A scrolling text (the selected row's title, the status row's title, Now
  Playing's title, artist and album) is clipped to its own column, inside the glass and before any
  value or chevron on its row. A 12 px fade covers its right end while the text goes on past it,
  and its left end while it is scrolled away from its start, so it rests crisp; each goes from
  clear to the colour behind the text (the Canvas, or Accent Blue on the selection). A truncated
  text that is not scrolling keeps its ellipsis (`list.marquee.fade`).
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
| Home (Full)                         | List row                    | Home uses the Full layout in this theme: the concept's menu is a full-width list. No page title: the clock, centred |
| Home (Split)                        | List row beside art         | Rows from y 40 (into the status row's empty left, the clock at its right) to x 204 (bar 16 to 188, chevrons 176); the art from x 196 and the top edge to the edges, panning slowly; the status row's clock, play state and battery over it at the right, each glyph white or black by contrast |
| Now Playing                         | [Now Playing](#now-playing) |                                                                                                            |
| Library                             | List row                    |                                                                                                            |
| Albums                              | Two-line row with art       |                                                                                                            |
| Album tracks                        | Song rows                   | Track number, title, length                                                                                |
| Folder view                         | Song rows                   | Songs as album tracks; folders with the folder glyph and a chevron                                         |
| Artists, Genres, Composers          | List row                    |                                                                                                            |
| Songs, a playlist's songs           | Song rows                   | The artist inline                                                                                          |
| Volume                              | Progress                    | A 6 px bar across the bottom of Now Playing, not a panel                                                   |
| Cover Flow                          | Own                         | Covers on black, no reflection floor; the caption uses Song title and Artist roles; an album's tracks as Album tracks; an artist's covers titled by the artist, other covers with the clock in the status row as Home |
| Playlists                           | Two-line row with icon      | New Playlist... and the other action rows take the same 52 px pitch once there is a playlist, the picker's too |
| Hold menu, More                     | [Menus](#menus)             | The inset modal over the page                                                                              |
| Settings and its pages              | Key/value and switch rows   |                                                                                                            |
| About                               | Key/value table             | Inert: no selection; values in Text Secondary                                                              |
| Lock Screen settings                | List row, then pictures     | Three a row from the text column to the right column; the check is the one accent                          |
| Pickers (Play mode, Sort, Language) | List row                    | The current choice carries a 12 px Accent Blue check on the right column                                   |
| Slider settings                     | Slider page                 | [Progress and sliders](#progress-and-sliders)                                                              |
| Equalizer (Simple)                  | Vertical slider             | Its tabs, then rows: the switch, Presets, Reset                                                            |
| Wi-Fi                               | Switch row, then a section  | Search Again row; "Networks" after 12 px, "Searching..." on its line; glyphs at right                      |
| Bluetooth                           | Two-line row                | Second line "Connected" with the Positive dot                                                              |
| Update Local Music                  | Progress                    | Counts as key/value rows under the status row                                                              |
| Shuffle, Repeat changes             | Confirmation                | New in this theme                                                                                          |
| Quick Settings                      | Own                         | Seven rows under the status row, Brightness's slider inline: Centre on it gives it the wheel               |
| Search                              | Own                         | An iPod classic's letter strip at y 216-254 under four single-line results ([ipod](ipod.md#search))         |
| Keyboard                            | Own                         | Stock's keys as `#1C1C1E` boxes on black, the wheel's key in the accent ([ipod](ipod.md#keyboard))          |
| Lock screen                         | Own                         | Clock 56 px Inter Medium centred, date 16 px Secondary, the song in two lines; black or the picture at 50% |

The concept's Apple Music, Apple TV, CD sync and storage pages have no Q2 Pod equivalent and are
out of scope. Spotify's pages follow the list patterns.

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

- A fourth built-in theme (picker order and tests derived from `assets/themes/`).
- Font faces as a table, with Inter Regular and Medium and tabular digits.
- A theme's own accent tones, and the selection bar's radius, inset and `fixed` (every row,
  whatever Corners says).
- A status row in place of a bar band (`status_y`), its title on the text column, its battery on the
  right column (`right_x`) and its clock beside the play state, or centred where there is no title; list boxes that end on a whole row by `list_end`
  (`list_fit`).
- A theme's own art from Phosphor (`tools/art.py`), loaded by its own prefix (`theme_art`): the
  chevron (`list.chevron.painter="icon"`) and the play state; the slim battery
  (`bar.battery.painter="slim"`); the wheel's scroll indicator (`list.scrollbar.painter="wheel"`);
  1 px dividers on the text and right columns (`list.divider.ink`); row art's 4 px corners
  (`list.art.radius`); key/value rows, switches and values beside a chevron on the right column; the
  switch, Gain's value, the pickers' check, disabled rows, the slider page, EQ's Simple tab and the Wi-Fi
  page as above (`settings.values.switch`); song rows
  (`list.songs.painter="inline"`) and the Song Artist and Song Length options; Songs' action row
  as the Shuffle row (`list.shuffle.row`); a list left by Return opening at its top when chosen
  again (`list.reopen.top`, which Minimal sets for every theme).
- Now Playing on the set's grid (`np_art`, `np_bar_y`), its art's corners (`playing.art.radius`), its
  artist and album in Inter (`playing.artist.font`), shuffle and repeat, the heart and More as the theme's
  own glyphs (`playing.modes.icons`), the marquee fades (`list.marquee.fade`), and Track Time (`NPTIME`), whose
  tap on the right-hand time shows the length or the time left.
- Menus as the inset modal over the page ([Menus](#menus)): the set's `modal`, `patch/menu_modal.c`, the
  Phosphor symbols as the theme's own art.
- Home's Split on the grid: the set's `split_row`, `split_art`, `split_art_up` and `split_inset` (the art from
  x 196 and the top edge to the edges, its top 58 rows painted by the bar with the status row's clock, play state
  and battery over them, each glyph in one ink, white or black, whichever has the higher WCAG contrast against the
  art's mean luminance under it, weighted by the glyph's coverage and held 0.03 past the bound against flicker; the
  charging green and low red keep their colours (`home_ink`, approved from `mock_split_top.py` on 2026-11-14, one
  ink a glyph from the same day, as each pixel's own pick speckled over lettering), panning slowly across its 110 % zoom with an
  eased turn at each end and 2 % of the zoom kept clear of the pane at each edge, so a cover's own border never
  shows), the main menu from y 40, its top rows painted by the bar (`coverflow_home_strip`, approved from
  `mock_overflow.py` on 2026-11-15), and the lists Home's rows open as full-width pages with the art hidden
  (`home.lists.full`).
- The toast, the confirm as a dialog page and the empty note (`dialog.confirm.painter="oled"`,
  `patch/feedback.c`), and the confirmation after a shuffle.

Still to build:

- The confirmation for Repeat and Now Playing's mode changes.
- The storage bar drawn as above.
