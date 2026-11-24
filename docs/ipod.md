# iPod UI

The build carries the compact layout payload helpers and build-time edits in `tools/ipod.py`.
`patch/ipod.json` records the original
asset hashes and full MIPS instructions. The builder also pins the complete stock
ZIP and executable, rejects mismatches, and records every changed asset/site.

AWTK binary UI files contain a four-byte magic, recursive widgets with a 32-byte
type and four signed geometry fields, NUL-separated properties and child/end
markers. Decode/encode must round-trip exactly before editing. Only the assets
pinned in `ipod.json` are accepted: nine local browsing pages, the settings
and streaming pages, Home, the status bar, Now Playing, the quick settings
pull-down, the confirm and volume pop-ups, the equalizer page (its [transition](#transitions) only) and the
theme (`styles/default.bin`). The primary `view_navbar` stays allocated but invisible
and disabled, including dynamically recreated children. Separate action bars are
moved into its space.

## Rows

`PITCH = 72` is shared by all build-time row geometry edits, native row-height
resets and artwork offset divisors. The page is the whole screen and its rows rest below the
status bar: four rows fit the 290 pixels from the bar's foot, so `BOTTOM = 290` replaces the stock lists' 260-pixel content
bottom. Row bodies are `PITCH - 4` pixels, and the stock 52-pixel artwork is
drawn at natural size (`ART = 52`) with `ART_INSET = 8` on all four sides: the
artwork never rescales, so glyphs and covers stay as sharp as stock, and the row
layout's eight-pixel left margin seats the artwork exactly. ART_INSET also
positions the playing overlay. Font definitions are untouched. Full-height
text/icon containers are shortened with their button. Titles and metadata keep
their stock centering, one pixel higher for the two-pixel-shorter body.

Native local row-pool constructors are at 0x533bc4 (folder), 0x4ac588 (songs),
0x4b39f4 (local categories) and 0x4a6910 (album list/grid). The album grid branch
is unchanged. Album detail, artist track and playlist constructors have their own
explicit sites in the audit. Folder reset at 0x532000, return offset division at
0x532308, scrolling cover division at 0x533288 and the folder constructor's own pitch at
0x533c54 take the set's `folder_pitch`, the art rows' unless the set gives its own (OLED's 32).
The category cover callback originally divides by 120 despite using 78-pixel
rows; iPod corrects its audited divisor at 0x4b2a48 to 72. Rebinding and delayed
cover callbacks keep the same widget geometry and saved cover preferences.
Artists rows hide their head picture (`CLASS_ICON`), so the title takes its width.

Seven audited row-constructor calls install a per-instance children layouter for
folder, song, album-list, category, album-track, artist-track and playlist rows.
Before the stock horizontal layout runs, it gives the title (and its containing
view, where present) the row width minus the existing side margins, visible sibling
widths and gaps. Hidden artwork and controls reserve no space. The stock layouter
still positions the children, preserving the title's left edge, artwork, row height
and padding. Its clone/destruction and parameter functions remain native; neither
the shared widget implementation nor album grids are hooked. The folder rebind's
140/190-pixel resize call is disabled so recycled titles retain their computed
width. Title styles and ellipsis settings are untouched; the title scrolls only while focused
(`hscroll_label_set_only_focus`), which no row is, so a long title keeps its ellipsis instead of
every row's scrolling once as the page opens. The selected row's scrolls, as an iPod's does: once
the selection settles the payload runs stock's scroller on it (`hscroll_label_start`) as a looping
label, with every label's holds and pace (docs/internals.md, `ringnav_hscroll_start`): its start
held `MARQUEE_HOLD_MS`, a scroll at Now Playing's pace, its end held as long, then back to its start.
The lull at each pass's end counts it, and after `MARQUEE_PASSES` (3) the title rests on its start
until the selection moves; each pass repaints every 16 ms while it moves. `only_focus` is off while
it runs, since stock's timer (`0x5fb7ac`) stops an unfocused `only_focus` label on its first tick;
once running, stock's paint (`0x5fbff0`) drops the ellipsis, so the selected title shows its start
without one as it holds. A paint that moves the selection, to another widget or to another row in the
same pooled widget, stops it from a timer. A title that fits costs one timer per selection.
ponytail: the first `hscroll_label` of the row is taken as its title, and a dark screen, a covered
page or a touch's hidden selection stops it only at the end of its pass, not at once.

## Flat rows and selection

The iPod theme uses black surfaces, white titles and subdued secondary text. Settings icons are
resized as before; a theme with `list.icons.mono` (OLED) turns every icon's colored disc the same
neutral gray (the scheme's `chevron`, `MINIMAL_CHEVRON` `#777777`, as stock's folder and song discs),
keeping the white glyph and blending its edges by saturation, as the image manager loads them
(`ringnav_image_add`). Built-in library discs use the same mapping; navigation, streaming and
control icons become monochrome while retaining their light/dark glyphs and active states. Alpha
stays intact. Artwork, photos, external images and battery warning colors stay unchanged.
The pinned style edits in `patch/ipod.json` remove list fills and corners and make pressed
feedback neutral gray. Playing titles keep the separate playing glyph.

Wheel selection is the accent's flat bar (`gradient()` in the scheme's tones), `list.selection.inset`
in from each side of a list row (OLED's 16; `SEL_INSET`, 6, for a theme that states none) with ends
rounded by `list.selection.radius` at most (OLED's 8), less on a row whose cover the round end would
cut, such as Search's results: `pill_radius` shrinks them until the cover's corners are inside,
checking only the row's own visible left-hand, non-centered images, so Folder's hidden multi-select
check doesn't square it. A grid tile's selection is a square card in the accent's color,
`TILE_INSET` inside the tile ([Theme](#theme), [internals](internals.md#themes)). The rows keep their
own (white) ink on the bar (`sel_accent`). The background hook resolves the selected row. Paint
ancestry determines the colors, so no style overrides can remain on recycled rows or stale
scrolling labels. Selected backgrounds stay transparent over the selection. Touch hides wheel
selection until wheel/center input; the native pressed state remains visible. A pressed list row
(`s_btn_listitem`) is gray across the whole row (`pressed_pill`), and a pressed tile under half its
row's width (the album grid's, the playlist page's pair) is the tile's card, `TILE_INSET` inside it;
other pressed widgets keep stock's box. The same hooks cover settings, streaming, dialogs, PEQ rows
and media browser chrome. Video and EQ plots keep their own
drawing.

A list left with Return opens at its top when it is chosen again (`list.reopen.top`, OLED's), as an
iPod's menus and stock's own pages do; Return
still lands on the row the page behind was left at ([internals](internals.md#position-memory)).

## Home

Home splits the screen: the menu on the left, the current local track's art in the right panel.
The selection is the lists' bar, and each row that opens a page has the chevron ([Theme](#theme)).

The eight rows, by default in the order the menu review chose (2026-11-15, with Games and Streaming moved on
2026-11-19, and Radio added on 2026-11-20), are the play state's row, Library, Radio, Games, Folders,
Cover Flow, Search and Settings. **Radio** opens the tuner (`tuner_open`,
[internals: Radio](internals.md#radio-home)). **Games** opens the
Games page (`games_open`, [Games](#games)), which the Library held before; Streaming is a Settings row, after
Playback. **The first row** is stock's Now Playing row named for what plays
(`patch/home_np.c`; approved 2026-11-16, `design/home-np-icon`): **Playing**, **Paused**, or **Guest Play** while a
guest plays ([internals](internals.md#guest-play)), when it opens the guest's Now Playing (its click and a short
Return, `guest_home_np`), else the owner's. It leads with the play state, which leaves the status row while the row
is in sight (`home_np_lead`, [oled-design](oled-design.md#homes-first-row)), and only its words move, 18 px past the
glyph; Edit Home lists it as Playing. The row is there only while something is loaded, playing or paused, or a guest plays, as
on an iPod: with nothing loaded, or once play has stopped for `NP_GONE_MS` (3 s, so the moment
between two tracks keeps it), the row goes, in Split and Full, and it comes back as play
starts (`np_loaded`, checked on each bar paint and by `np_due` when the 3 s are up;
`coverflow_home_layout` restacks the hidden row last). Coming or going it keeps
the selection on the same row and the same rows in view (`home_keep`); on Now Playing as it
goes, the selection moves to the row after it. **Folders** is stock's
Folder card under its plural name, shown only while the SD card has folders to list (`home_folders`, the empty
Folders page's test) until the user shows or hides it. **Edit Home** (Center held on the list, or a finger held on a
row; [oled-design](oled-design.md#edit-library), [internals](internals.md#library-order)) puts the rows in the
user's order and shows or hides each, Settings always shown. **Search** opens the Library's search (`library_search_open`: the
letter strip under `list.entry.ipod`, OLED's, stock's search without it): every song, artist and album, the one
search across everything; each list page searches its own rows from its search bar. Shuffle Songs is not on
the list: the Library's Shuffle page keeps it. A stored `IPOD/SHORTCUT` is not a setting and is never read. Rockbox, when
the card has it, is a row of Settings → General ([boot](boot.md#rockbox-from-home)). Settings opens its own list in the menu's place, as an iPod's submenu: Wi-Fi, Bluetooth, General, Playback, Streaming, Car Remote, Display, Controls and Battery ([Settings](#settings)); Return restores the menu's previous selection, and a short Return on the main menu selects
its first row, Now Playing while it shows; a short Return with Now Playing selected opens it without
changing playback. Return there goes back to Home. Both lists end at their ends, as every list does: the wheel stops there and the selection leans against the end ([internals.md](internals.md#list-ends)).
Folder opens directly at the SD root when a card is in and no OTG drive is connected; Back at that root returns
to Home. With OTG connected or no card in on entry, Folder keeps the storage selector. Nested folders and
Library’s Podcasts/Audiobooks shortcuts keep their usual Back navigation.
The native stock handlers, translation keys, full-row tap targets and submenu animation remain intact.

Home's rows are the grid's `home_row` (OLED's 32), directly below the status row in Full,
from y 40 in Split, into the status row's empty left, matching Library. The main menu rests on `HOME_REST` (7) rows, as many as every set's window fits, and
scrolls the others as a list does: scrolled to its end, its last row sits where the seventh's does
(`patch/home.c` `home_overflow`, `home_rest`; a hidden Now Playing row, restacked last, is not scrolled
to). The menu runs on to the screen's bottom, and the rows below the seventh show through a fade
into the black: in Full a straight one from 16 px above the seventh's foot (y 266) to y 316, and
once the menu has scrolled its rows run on under the status row, fading to black at the screen's top
edge ([oled-design: List edges](oled-design.md#list-edges)); in Split both follow the glass's
rounded edge, 34 px from its top edge and 56 px from its bottom one, the top one reaching black no
higher than the row under the clock's digits (y 28), so no row shows through the clock (`home_fades`,
from the list's paint). A fade is drawn only while rows lie past it, and never over the selected
row. The labels start at `home.rows.text_x` (OLED's 24) in the theme's Home face (Inter at 24 here,
the design's 20), and end `home.rows.label_end_split` (44) before the art in Split and
`HOME_LABEL_END` (41) from the screen edge in Full, where the chevron's box starts. Both menus are 375 pixels wide in the asset; the
payload lays them out (below). Long translated labels ellipsize and scroll.
The background view paints before the transparent menus, and both menus are clipped to the
window while sliding.

Display settings offers **Home: Split / Full**, stored as `IPOD/HOME` 0 or 1; any other value, absent or invalid
(2 among them), means Split, and the row's next save writes 0 or 1. `coverflow_home_layout` moves the asset's widgets at runtime:
Split narrows both menus to the grid's `split_row` (OLED's 204; `HOME_SPLIT_W`, 187, half the
screen, in the built-in set) and shows the cover itself in the panel right of them, from `split_art`
(OLED's 196) to the screen's edge, and with OLED's `split_art_up` from the top edge, under the status
row ([Theme](#theme)) (`img_homeart` is an `image` for this, fitted to the cover's proportions and
clipped to the panel, the menus clipped left of it; `default_album_home` without a cover). The cover's top
50 and bottom 40 rows on the screen are darkened toward 25% brightness, 0.25 + 0.75 t^1.6 of each row, t its
distance from the edge over the fade (`coverflow.c` `vig_k`, the V4 vignette approved on 2026-11-15), as the frame
is drawn (`home_render`), so the status row's glyphs, which take white or black from the frame under them
(`home_ink`), read the darkened art; Full
hides the art and widens the menus to the screen, their rows to `home.rows.full_row` (375). Where
the theme's face lacks a character of a label's text, stock's font stands in, which has CJK
([internals](internals.md#themes), Faces). The chevron is the theme's own glyph on every row that
opens a page.

The art is the player's cover once `g_lastcover_url` matches the current queue path, else the
current album's Cover Flow thumbnail, else the placeholder; the previous track's player file is
never used as a fallback. It reloads only when its key (the queue path, usable player-cover type
and parsed album tags) changes, and not at all in Full. The reload runs inside a paint, and AWTK
drops what a paint invalidates when the frame ends, so a 0 ms timer then repaints the whole screen;
otherwise the new art and its fit would reach only the regions repainted later. `g_playcover_finishflag` remains
stock-owned. Now Playing and Spotify's page keep their black background behind the art.

Stock's home carousel (its `slide_menu` of cards, less V1.36's Album Wall card, and the two arrows) stays in Home's window, hidden and taking no taps, as `home_page_init`'s visitor binds the arrows and cards by name (`tools/ipod.py` `ipod_home`); nothing shows or steps it.

The player writes every track's cover to the same `/tmp` file, so before Split loads it `coverflow_home_art` drops AWTK's cached bitmap of that name, as the full-screen art does; otherwise the image manager hands back the last track's cover.

In Split a cover pans slowly, as an iPod classic's menu art did: `slide_load` box filters it once
(at most 4 × 4 samples a texel) to its fit zoomed `DRIFT_ZOOM_PCT` (110), and each axis runs its
own ping-pong across that margin alone, from the old crop (`DRIFT_X_MS` 20 s, `DRIFT_Y_MS`
26 s): about 1.5 pixels a second on average on a square cover, as iPod.js's recreation moves, and the cover's
edges never show. Each leg is eased (`drift_at`, smoothstep): it slows to a stop at each end and
turns there with no jolt, its speed peaking at 1.5 times the average halfway, so the two axes
together trace a smooth curve with no corner. The art widget then spans the panel with an empty image, and `home_paint` (from
`coverflow_home_clip`, inside the panel's clip) draws one 188 × 290 frame `home_render` filters from
the cover at the pan's position in 1/256 px, a cubic B-spline over 4 × 4 texels (each cover row
across once, then down). Whole-pixel steps at that speed read as a jerk every two thirds of a
second, one axis at a time; the filter moves it by fractions of a pixel, as the iPod's own
rendering does. Bilinear would keep a whole-pixel frame's texels but mix a half-pixel one's 50/50,
so the cover's sharpness would pulse with the pan's phase, most visibly in lettering. The
B-spline blurs every phase about alike but is soft, so `slide_load` sharpens the filtered cover
once, `[-1,6,-1]/4` across and down (`home_sharpen`), and keeps it inside a border of its edges (a
texel left and above, two right and below) for the filter's reads past the panel. The art is about
twice as sharp as a `[1,2,1]/4` softening with bilinear, and its sharpness changes with the phase by
about a tenth on a photo and a fifth on lettering. A frame costs about 4.8 million instructions,
2.3 times the bilinear's, five a second while Home shows; the sharpening adds about 13 million
once a cover. A cover whose zoomed fit passes `DRIFT_TEXELS` (four panels: wider
than about 2:1, or taller than about 5.5:1), a decode that is not 32-bit, the placeholder and Full stay still and unzoomed,
drawn by the stock fill. Nothing is stored: the pan's phase counts only time shown, so leaving
Home resumes it where it was, and a new track's cover takes up the phase of the
one it replaces. One `DRIFT_TICK_MS` (200 ms, about 0.3 px a step; a late tick steps at most `DRIFT_MAX_STEP_MS`, 500 ms) timer
runs it, armed from Home's paint only while Home is the top window and not sliding and the screen is
lit; its next step after any of those changes removes it, and
`coverflow_home_layout` (Home, a new Home window) removes it at once. A step is capped at
`DRIFT_MAX_STEP_MS`, so a stalled loop never jumps. A step invalidates only the panel (with a `split_art_up`, the bar's rows over it too), and a paint
renders the frame again only once the pan or a fade has moved (`frame_stamp`). Light Mode's paper
behind a transparent cover follows only the next cover change. The timer trusts `home.win` as Home's
identity, as the art's paint always has: Home is only replaced through `coverflow_home`, which
stops the timer first.

**The slideshow.** An iPod classic's main menu shows the art for the highlighted row: on Music,
a slideshow of the library's album covers, each drifting and zooming slowly (the Ken Burns look)
and cross-fading into the next; on Now Playing, which is there only while something is loaded,
the playing album's cover. Split does the same (`art_source`), the pane following the highlighted
row (`coverflow_home_row`): Now Playing's row shows the playing cover, playing or paused, and every
other row (Library, Cover Flow, Search, Settings and the rest) the slideshow, as the
iPod shows the library's art on Music; moving the wheel between Now Playing and another row
cross-fades from the frame as it stands, so it never cuts, even mid-fade (`frame_hold`). There is
no Row Preview setting: the pane always follows the row. Each library cover holds `SLIDE_HOLD_MS` (6 s) between `SLIDE_FADE_MS` (1.5 s)
cross-fades; over its whole life (9 s) its window moves once at an even pace from one end to the
other (`slide_at`, `kb_ends`): a focus picked at random in the middle 40 % of the zoomed cover, a
drift of up to `KB_DRIFT` (24 px) each way about it, and a zoom between the pan's scale and
`KB_NEAR` (0.9 of its texels, so up to about 11 % closer), in or out at random, both ends inside
`split_inset`. `home_render` takes the window's scale, each column with its own B-spline weights
(at the playing cover's scale 1 they are one set, as before), and lays the incoming cover over the
outgoing one by the fade's share while one runs, both moving. A new track's cover cross-fades the
same way, as does the change between the playing cover and the slideshow; when play starts or the
wheel reaches Now Playing while a library cover is still fading in, the playing cover fades in over
the frame as it stood, those two covers held still under it (`frame_hold`), and the wheel leaving
Now Playing while its cover fades in brings the next library cover in over the frame the same way,
so nothing jumps; a cover that cannot
move (a 16-bit decode, a cover too long for `DRIFT_TEXELS`, none) still cuts. The timer steps every
`SLIDE_TICK_MS` (100 ms) while a fade runs, so it steps in 15 shades, and every `DRIFT_TICK_MS`
otherwise. The covers come from `slideshow.c`, off the UI thread (`slide_pick`): a song at a random
rowid of the library (no sort of the table, `MUSIC_WHERE`'s scope kept), from the card's
`songtable` or a signed-in server's `servertable` in proportion to their rows (`SLIDE_COUNT`, so a
library that is all a server's still turns); a card song's album's embedded art, else its folder
image, a server song's the cover its app keeps for the album, its 512 px one
(`catalogue_cover_large`, `covers/512/<album>.jpg`, 500 px from q2-subsonic) when the card has it,
else the 160 px row one (`catalogue_cover`), scaled up and blocky, each read by
stock's own readers under both art locks at
`SLIDE_ART_PX` (352, the tallest pane zoomed 110 %) square into `/tmp/q2-slide.jpg`, else the album's
Cover Flow thumbnail (160 px, softer); up to `SLIDE_TRIES` (8) songs a pick, the last
`SLIDE_RECENT` (6) albums passed over but for the last two tries, so a small library still turns.
The pick starts `SLIDE_AHEAD_MS` (3 s) before its cover is due, the decode and filtering then run
on the UI thread once a cover (as a track change's always have), and a pick that found nothing, a
library scan or a refused thread waits `SLIDE_RETRY_MS` (30 s) for the next. A library with no art
at all keeps the playing cover, or the theme's pane. The two slides' textures (about 0.5 MB each at
352 square) are on the heap only while they show.

A set with `split_inset` (OLED's 2) keeps that percent of the zoomed cover clear at each edge
(`drift_axis`), so a cover's own frame (many have a thin white border) never shows at the pane's
edges even at the pan's ends: on OLED's 179 × 320 pane a square cover pans 32 pixels across and 18
down. An inset the zoom's margin cannot hold leaves the cover still in the zoom's middle, and no
timer runs. A theme with `home.lists.full` (OLED's) opens the lists Home's rows open in Split
(Settings, and Library and Streaming where a set keeps them on Home) as pages: the list takes the
screen's width in Full's rows (`sub_full`), the art is hidden while it shows and no pan runs, and
Return brings the main list back beside the art, whose last frame shows at once. A set with `split_art_up` starts the pane that many rows above the status row's foot: Home's window is the whole screen ([oled-design: Full-bleed](oled-design.md#full-bleed)), so the pane is one widget from there to the screen's bottom and Home's own paint and its snapshot under a slide hold it all. OLED's `split_art_up` is the bar's whole 58, so the art reaches the top edge and the status row's clock, play state and battery stand over it; on the screen itself the bar lays its flat ground over the pane's rows before its glyphs (`coverflow_home_bar`) and its border paint puts the art back with them inked by contrast (`home_ink`). The main menu rises too: the list starts at y 40 (`HOME_SPLIT_TOP`), just under the clock, which stands at the row's left, and its top rows go under the row's overlay. Settings and the other
lists Home opens are full pages under OLED, and stay in the window. The clock and every glyph
are drawn in one ink for the whole row, white or black, whichever has the higher WCAG contrast
against the art under them all, as Scrolly's feed-end line does and as iOS's status bar takes one
style (`mock_status_icons.py`, the user's, 2026-11-16: inked a glyph at a time, the level bars came
out black beside a white clock over a mid-grey cover): the art's relative luminance (from a
256-entry linear table) averaged under every glyph together, each pixel weighted by its glyph's
coverage there, white while that is at most 0.179. Each glyph keeps its shape, and an antialiased
edge blends the ink over the art by its share, so a play state or battery over lettering stays one
smooth shape rather than a speckle of both inks. A glyph is still a run of the bar's columns with
ink, parted from the next by four empty ones (`INK_GAP`; a pause's two bars are one glyph), so the
battery's charging green and low red are found in their own glyph and keep their hue. The row
keeps its ink until the mean passes the bound by 0.03 (`INK_HOLD`), so a mean the pan carries
along the bound doesn't flicker: on the emulator's test covers (30 s pans) a hold of 0.03 left a
glyph changing ink at most twice, each time for seconds, where with none a battery over lettering
changed up to ten times, and inside that band either ink still has at least 3.98:1. The row's ink
is held in every pane column (`home.held`). Under the vignette the art behind rows 17 to 27 is
darkened to between 38% and 51% of itself, so the row is white over almost any cover; a white cover sits
near the bound and stays white. To get the glyphs alone, the bar's background leaves the pane's rows its flat ground
when it draws straight onto the screen; the glyphs paint on that, and the bar's border paint
(`coverflow_home_clip`, `home_ink`) reads each pixel's distance from the ground as the glyph's
share and writes the art with the ink over it, only within the paint's clip. A paint the clip
cuts short of the pane (the play state's frames, a repaint of part of the battery) keeps the row's
ink, as its part alone would average differently. A pan step invalidates those rows of the bar with the
window's, so the ink follows the art. Inking them costs about 0.84 million instructions a step
(`test/patch.py`, 188 × 58 with a pause and a battery), a sixth of the frame's render: a pixel left
the ground's color (the bare art, most of them) takes one compare, not the share's three channels. The clock is drawn in Text Primary there (`clock_place`), so it
is inked with the others.
The battery's charging green and low red keep their own colors over the art, each pixel's share
read against the glyph's most saturated pixel, and only its gray part (the dim outline as a
lighter share, the nub) is inked (`INK_KEEP_STATUS_COLOURS`; 0 inks them with the rest). Where the bar
draws off the screen, the art is painted under the glyphs as they are. With nothing
playing, or a cover the frame cannot hold, the pane is the window's panel, as it would be with no
`split_art_up`.

## Status bar and clock

`systembar_showface` (`0x5417d0`, run by `system_bar_init` and then a 1 s widget
timer) finds each status bar widget with a recursive `widget_lookup` from the bar,
so parents and order are free to change. Every tick it re-shows the volume,
EQ, Bluetooth, SyncLink and Wi-Fi widgets and sets their images and text, but
never their geometry. `system_bar.bin` is therefore OLED's status row (`tools/ipod.py` `status_row`): the title on the
left and the clock, the payload's glyph slots and the battery on the right ([Theme](#theme), [Status row
slots](internals.md#status-row-slots)), every other widget stock updates kept off-screen at `x = -200`. Stock's tick also hides `img_state` whenever its own player is stopped, as it is under Spotify, radio or a video; `ringnav_showface` runs `bar_state_sync`, the play state and the `Q2` label alone, straight after the tick, so the payload's play state is back before any paint, dark or not (no file reads; a relayout waits for the paint); the codec badge, accent and Rockbox check wait for paint (`bar_sync`). A new battery level or charge state repaints the bar from the tick while lit ([Status bar](internals.md#status-bar-ipod)). Correcting it at paint time instead relaid the row twice a second, and EQ was drawn for a frame in the play icon's place. When `bar_sync` changes `img_state` while another window paints (Now Playing paints every 250 ms, so it usually sees a Play/Pause first), it arms a 0 ms timer that runs `widget_invalidate_force` on the bar after the frame: an invalidate made mid-paint is dropped with that frame's dirty rects, and `img_state` keeps its dirty flag (`+0x24` bit `0x200`), which makes stock `widget_invalidate` return early, so the 1 s tick's own invalidates skipped it until something else repainted the bar (#116). `bar_sync` updates its visibility without moving the clock. The payload paints the bar and writes the time into
`label_clock` (see [internals.md](internals.md#status-bar-ipod)).

**Time in Title.** There is no such setting: the status row names the page in a title of its own, the clock centred after it (`bar_title`). An `IPOD TIMETITLE` key an older build wrote is never read.

**Codec.** The Bluetooth images are 42-pixel canvases with their ink at the right: the glyph takes
10 pixels, while stock's codec badges take up to 40 (aptX HD). A new badge shows for a second
(`CODEC_MS`), then fades out and the white Bluetooth glyph (`bar_btcon`) fades in at the same place,
150 ms each way; the glyph then stays until the codec changes or Bluetooth goes off.

**Battery.** The Battery setting (see [Display settings](#display-settings)) offers the modes the theme lists, at the right end; the layout skips hidden widgets. OLED lists Icon and Percent (`<option setting="battery" values="icon percent">`): Icon is its slim battery (`battery_slim`, `patch/list_marks.c`, the set's `batt_w` 25 by `batt_h` 12), and Percent the percentage alone. A stored value the theme does not list (Icon + Percent, 2, or Percent Inside, 3, from a build with more themes) reads as its default, Icon, the key untouched (`settings_read`). On the built-in set's bar:

- Icon: a clean 16×11 charge-level outline and a 2-pixel nub, drawn in `view_battery`.
- Percent: the 16 px percentage alone, drawn in `view_battery`.
- Icon + Percent: the same charge-level icon with a 16 px percentage beside it.
- Percent Inside (`BATTERY=3`): a compact 28×15 solid battery with a 2×5 terminal. Centered 13 px dark digits show the percentage without a percent sign. The body turns green while charging and red when low.

The percentage's slot is as wide as its digits (measured once in the stock font, which has tabular
digits), so every mode ends on the icons' margin with the same 4-pixel gap to Wi-Fi; `BATT_PCT_W`
(42, "100%") stands in until the first paint has measured them.

The playback, Bluetooth and Wi-Fi glyphs share smooth edges, rounded ends and a consistent visual weight. Four-pixel spacing keeps the enlarged numbers clear of connectivity icons. Wi-Fi's arcs are concentric round its dot and stand the battery's 11 pixels tall; it retains all five signal states.

In Icon and Icon + Percent, charging fills the icon solid green (`BATT_CHARGE_RGB`); low battery always uses
`BATT_LOW_RGB` (`#FF1448`) under every accent; OLED's slim battery fills green while charging and
red (`#FF453A`) under 20 %. The bar is black, or Light Mode's paper. The payload shows the icon alone where the
battery and the status row's glyphs would have to scroll: `batt_room` (navigation.c) is the span from the battery's end
(`STATUS_ROW_INSET`) to `CLOCK_BESIDE` past the widest clock, centred (`strip_left`), and the group is the
battery's form and `status_slots_reach`.

The navbar is hidden, as on the local pages, on the settings pages
(`systemset/*`, `playset/*`), `audiosetting_page` and `stream_page`, listed in
`navbar_only` in `ipod.json` with their pinned hashes. Their native
inits destroy the navbar's children and create an unnamed title `hscroll_label`,
back, Home and Now Playing buttons; none has a control the keys lack, except the on/off switches
of the sleep timer (`img_switch`) and Startup volume (`img_bootswitch`, which `playset_bootvol_page_init` puts in
the navbar; Night Shift reuses the page), whose fields hide while off:
those two pages (`TOOLBAR_SWITCHES`) keep it. There the switch takes the toolbar's right-hand
slot and the title runs up to it (225 pixels); the Home and Now Playing buttons are 0 wide, as the
keys do what they did (`immediates` in `ipod.json`). `patch_asset` hides those toolbars
too, Startup volume's aside: `navigation.c`'s `ringnav_sleeptimer` makes the Sleep Timer's switch a row whose click is
`img_switch`'s, hidden with its toolbar, a loop pass later; its fields' panel (`view_sleep`) moves down a row under it and is cut to end with the window. The grid's `stock_row`, `stock_body`,
`stock_title_y` and `stock_title_h` re-pitch stock's own pages (`stock_rows` in `ipod.json`): Update Library and More as settings rows, Queue, Go to and
Special Folders through their row constructors' immediates. More's Speed row keeps 78 pixels: its
four 42 x 61 buttons don't fit (`stock_more_speed`). Lists hold
settings rows (see [Settings](#settings)); other panels move up 50 pixels and keep
their size. The settings inits never move or resize
these widgets. Left out: `wifitransport_page` (its image starts above the
navbar's bottom edge), `fwdownload_page` (no navbar), the PEQ page (it replaces
all children) and every Tidal page, whose navbars hold the search and sort
buttons. A page with a visible navbar keeps its own title. The artist page is the design's
artist page, with no tabs or navbar ([internals.md](internals.md#library-artist-page)).

## Settings

Settings, Playback and Streaming lists use 68-pixel controls
(`SET_ROW`, `SET_ROWS`) on the built-in set, starting 8 pixels (`SET_TOP`) below the status bar. The
list stays 272 pixels high, preserving scrolling and full-row tap targets; OLED's rows come
from its set (below). The `navbar_only` assets set their
`list_view`'s `default_item_height` from 78 to 68, which no stock asset uses, and nothing else.
Constants are `SET_*` in `patch/offsets.inc`.
Under OLED's grid (32-pixel rows from the top, no icons) the row height, top, row
count and icon size come from the grid (`set_row`, `set_top`, `set_rows`, `set_icon` 0); the payload
reads `set_row` from the grid the build baked (`geom_vals`), hides the icon and starts the
text where a row without an icon starts. Streaming's rows (`scroll_view_streamsset`, matched by the view's name at layout time) take `set_row` too
(the grid's `stream_row` is the same 32). The logos give way to the theme's 20 px monochrome marks (`patch/home.c` `stream_marks`, [internals: Themes](internals.md#themes)), a card app's own icon to the same 20 px box and the Subsonic app's to its own mark. A payload picker's `select` or `unselect` (Key Lock)
is not an icon: at 0 it stays, in the column of stock's own marks, and under a `set_text` theme
(OLED) every picker opens on its mark when a tap opened it too, as a `CHOICE` picker does. Another list whose `default_item_height` equals the set's
`set_row` carries an explicit `item_height`, so only the settings pages are told apart by it.

Settings, as iOS's, lists Wi-Fi, Bluetooth, General, Playback, Streaming, Car Remote, Display, Controls and Battery
(the menu review's order, 2026-11-15, Streaming from Home since 2026-11-19; `tools/ipod.py` `HOME_SETS`): Wi-Fi and
Bluetooth open stock's pages, which its Wireless page held, and the rows that are not stock's are bound by
`home_sets_bind`. Streaming opens stock's Streaming page (`on_home_stream_onclick`, bound by `coverflow_home`), or in a
set with `home_lib` its rows over the Settings list, which Return gives back. General is stock's System page
(`ringnav_systemset`): About, Software Update (`sw_update.c`: online and the card at once), Storage (with **Update Library**, from the
Library's page since 2026-11-19, [internals](internals.md#storage)), USB (the remembered USB choice as its value; its picker is `usb_ui.c` `usb_pick_open`, [internals](internals.md#usb-pages)), Date & Time, App Store, Network (DLNA, AirPlay and Wi-Fi File
Transfer), then Restart and Shut Down after a 12 px gap and Reset Settings last, directly under Shut Down, in Negative red as a destructive row (`power_rows`); Battery is stock's Power
page under its new name. No list has headings. EQ and **Library** come first, then Play Mode, Gapless, Smart Crossfade, Memory Playback, Resume Songs and the Sleep Timer (stock's timer page; stock's Headphones Required is hidden, as it is always on: [internals](internals.md#no-playback-without-an-output)); the volume rows (Volume, Max Volume, Default Volume) and specialist output controls come next. Volume opens the slider page on the volume itself ([Wheel volume](internals.md#wheel-volume)); Max Volume, Balance and Default Volume's level are inline sliders set in the list, Default Volume a switch with its slider under it while on, Smart Crossfade one switch, and Display's Brightness is one too ([oled-design](oled-design.md#inline-sliders)). Added binary settings
use Shanling's `switch_on` / `switch_off` images with the whole row as the tap target. Settings
that cycle between named choices retain their value labels. Volume, Resume Songs, Wheel
Sensitivity and App Store show a chevron, as do the added Library browsing rows.

Playback's **Library** (`patch/library_settings.c`) holds what the Library contains and how it is ordered and shown.
Its pages are stock's Playback page opened again (`library_open`, `playset_sub` naming the page while its init runs), stock's
rows dropped, each page's own built; each keeps its own place in position memory (`SUB_PAGE`):

- Library: an **On-The-Go** switch first (`Q2POD OTG`, on by default: Playlists' On-The-Go row and the hold menu's
  Add to On-The-Go; there is no Streaming Songs switch: a signed-in card app's server songs and playlists are always
  in the Library, grayed while they can't play, [Server library](internals.md#server-library)); then Sorting, "Artists: Artist" or "Album Artist" (stock's `PLAYSET ARTISTTYPE`), "Artist View: Cover
  Flow" (`Q2POD ARTISTCF`, the default; Grid and List; on Cover Flow or List, Go to Album opens the album's tracks in
  Cover Flow, see [internals.md](internals.md#artist-view-and-go-to-album-in-coverflow)), Playlists, Folders, and a "Scan Song Durations" switch
  (`Q2POD SCANDURATIONS`, off; caption "Measures lengths after each update"; durations.c).
- Sorting: **Songs**, a picker of Title, Artist, Album and Recently Added (`song_sort.c`), the current one's direction
  as an arrow, Center on it again flipping it and another taking its natural one (A to Z, the newest first); "Artist's
  Albums: Year" or "A–Z" (`Q2POD ARTISTAZ`); "Years: Newest First" or "Oldest First" (`Q2POD YEAROLD`).
- Playlists: "Playlist View: List" (`Q2POD PLAYLISTCF`, List (default) or Cover Flow; [Playlists as
  covers](#cover-flow)), then Import Playlists and Export Playlists after a 12 px gap.
- Folders: Folder Root (`Q2POD FOLDERROOT`, cycling the card's top folders), stock's Folder Skip and Folder Album
  Cover.

The pickers (Songs here, Display > Cover Flow's Sort) are stock's Buttons Lock page opened again (`picker_open`), its
rows built by `picker_rows` from `ringnav_keylock_rows`; a pick stays on the page, as Buttons Lock's does. Library is depth 3 below Home
(Settings, Playback, Library); its Sorting, Playlists and Folders pages are depth 4, and the Songs picker depth 5.
From Home with Now Playing selected, counting detents and Center presses straight down (Settings is 6 detents and
Center, Playback, its fourth row, 3 and Center, Library, Playback's second, 1 and Center: 13 to open it), a row's own
press included: On-The-Go 14, Artists 16, Artist View 17, Scan Song Durations 20; the Songs picker
16, Artist's Albums 17, Years 18; Playlist View 19; Folder Root 20. Elsewhere: Sleep Timer's page 20, Enlarged Album
Art 20, Cover Flow's Sort picker 20.

Stock's Wireless page (Wi-Fi, Bluetooth and SyncLink) is not reached from Settings, whose own rows open Wi-Fi's and Bluetooth's pages; SyncLink stays off ([internals](internals.md#synclink)).

**Car Remote** (`Q2POD CARREMOTE`, off by default) opens its own page: the switch first, then the
paired phones by name. On, demo starts the `coda-rc` daemon, which lets a paired phone browse and play
the library from the car ([remote-protocol](remote-protocol.md), [internals](internals.md#car-remote));
off, it stops, and nothing listens. Center on a phone asks `Forget "name"?` on the [confirmation card](oled-design.md#confirmation-card), Cancel first,
then Forget; Return is Cancel. With none paired the page shows the empty state under the switch, "No Paired Phones", "Open Q2 Muse
Remote on your phone" ([oled-design](oled-design.md#empty-and-offline)).

Every row is built natively, about 60 builders in all, one pattern throughout: a
`list_item_create(view, 0, 0, 0, 0)` (some pass 78 as the height) holding a `button_create(item,
20, 0, 335, 70)`, and in the button an optional 52-pixel icon at x 10, a 24-pixel `hscroll_label`
at x 72 (x 10 without an icon) and a 50-pixel trailing image (chevron, tick or switch) at x 276
or 282. Two-line rows put a 20-pixel label at y 25; USB volume shows a value label at x 176. The
builders never move, resize or scroll their widgets afterwards (the stock equalizer page, which
the PEQ editor replaces, is the only settings code that calls `widget_move`), so one layout hook
owns the geometry:

- iPod points the `list_view` children layouter's vtable layout slot (`0x93a4c4`, vtable
  `0x93a4bc` + 8, stock `0x5fcd44`) at `ipod_list_layout`. Every list lays out through it; only a
  list with no `item_height` and a `default_item_height` of 68 gets settings rows, so Home (an
  `item_height` of 39), the local lists (72) and every stock list are untouched.
- Before the stock layout it sets list items that stock made 78 high to 68: the stock layout keeps
  an item's own height over `default_item_height` (`0x5fd454` onward). The stock layout then
  stacks the rows and sizes the scroll view, so scrolling, the scroll bar and the payload's
  selection all see 68-pixel rows.
- After it, each stock button (x 20, 335 wide) in a visible row spans its row, 375 by 68, so the
  selection bar and the tap target are the whole row. The stock layout skips hidden rows
  (`widget_get_children_for_layout` without `keep_invisible`, `0x5fd074`) and leaves them unsized,
  so a hidden row is mapped once it is shown and laid out. Its icon box shrinks to 40 pixels (`SET_ICON`, drawn
  `scale_down`, value 5 in the stock draw type table at `0x93ae54`, which draws a bitmap that
  already fits 1:1; see [Settings icons](#settings-icons)), centered on the row at x 28
  (`SET_ICON_X`). Text starts 12 pixels (`SET_GAP`) after the icon, at x 80, or at x 20
  (`SET_TEXT_X`) without one.
  Trailing images end 24 pixels (`SET_EDGE`) from the right, at x 351; value labels and the text's
  right edge move with them, never nearer the edge than 20. Full-height children fill the row and
  shorter ones keep their center. A mapped button no longer matches the stock geometry, so a later
  layout leaves it alone.

With these values the text, icons and trailing images of the first and the last visible row clear
the rounded glass (`test/patch.py` runs the Language, Bluetooth quality, System Settings and
Wi-Fi builders and the Display rows, then checks both positions).

### Settings icons

Scaling the 52-pixel artwork down on the device left jagged edges, so the build pre-sizes it
instead. `settings_icons` in `ipod.json` pins the 39 settings icons by hash: `system_*`,
`playset_*`, `display_*`, `wifiset_*`, `netservice_*`, `usb_chargeswitch` and `bt_adjvol`, 52-pixel
RGBA PNGs that only native settings code names (top level and nested pages such as Display,
Wi-Fi, Bluetooth and Network services); no UI asset or other screen uses them. For each one the
build runs ImageMagick (`magick`, else `convert`) with an alpha-weighted Lanczos resize to
40x40, strips metadata and date chunks so the bytes are reproducible, and replaces the file in
place, keeping its inode metadata. The build fails if an input's hash or format differs, or if an
output is not 40-pixel 8-bit RGBA with the same transparency; the manifest records both hashes
and the ImageMagick version under `changed_assets` and `tools`. `test/build.py` checks the
packaged bytes, the sizes, and that each icon's average color on black and on the Graphite
selection gray matches the stock icon's, and that the runtime gray (`MINIMAL_CHEVRON`, OLED's) is neutral on black. Other 52-pixel images that land in settings rows, such as
Streaming's Tidal logo (`list_tidal`, which the folder root may also use) and the Spotify row's
build-added icon ([internals.md](internals.md#spotify)), keep their bytes and still scale down. The
Spotify row's Now Playing page uses Now Playing's layout (`NP_*`), rounded art included. The recolouring of accent-red artwork
(`ringnav_image_add`) works on the decoded bitmap, so it applies at either size.

Brightness, Balance, Maximum Volume and Default Volume use eight-pixel rounded, theme-aware bars
with their original touch areas, where they still open (Settings sets them in its list instead). Wheel Sensitivity, Resume Songs, Night Shift and Playback's Volume reuse Default Volume's
page and inherit its styling. All keep their ranges and wheel/touch controls, but lose their endpoint icons (`slider_plain`, tools/ipod.py):
the images stay, named, with no image and no size, since stock looks them up. The quick
settings slider and custom volume overlay retain their layouts. Under OLED (`settings.values.switch`) the four
windows and Default Volume's other uses take one slider page instead ([oled-design.md](oled-design.md#progress-and-sliders),
`patch/slider_page.c`): the value centered, a 6 px track with a knob, the range's ends under it, and the feature's
switch, where it has one, as a row; the +/- discs are hidden, and the wheel moves the value.

## Quick settings

The pull-down (`dialog/statusbar_dialog.bin`) covers the whole screen when open. In the asset its eight 60-pixel
controls keep their four columns in two rows from y 20. Every label is the stock 16-pixel white
style (`s_label_white16c`), 80 pixels wide and 40 high (two lines), top-aligned so one- and
two-line labels start on the same line, 6 pixels under its icon and 12 above the next row. The
brightness slider becomes a 6-pixel track in Now Playing's default colors (`TRACK_COLOR`, whatever the theme's progress track, filled with the accent's light tone as the progress bar, `quick_fill`) in a 48-pixel-high slider that
still takes a tap or drag anywhere on it (`slide_with_bar`), from x 68 to 307. The stock dim and
bright suns stay at its ends, in line with the first and last icon columns. Sizes are `QS_*` in
`tools/ipod.py`. `dialog_statusbar_dialog_init` (`0x4a2338`) finds every widget by name and
never moves or resizes one. A finger held on Wi-Fi or Bluetooth opens its settings
([internals.md](internals.md#quick-settings-holds)); a tap stays stock's.

At run time the page is Control Center ([oled-design](oled-design.md#control-center), `patch/control_center.c`): `cc_open` hides stock's buttons and labels but Wi-Fi's and Bluetooth's, which it lays at opacity 0 under their circles for `cc_act` to click, puts the brightness slider at opacity 0 over the Brightness slider's track, and makes a clear button over every other control, the six circles included ([internals](internals.md#quick-settings-holds)). `cc_paint` draws the page under the window's children: the status row first, its clock centered (no title), then the blocks. The wheel walks the stops (the six toggles by rows, the Now Playing block while something is playing or paused, Brightness, Volume, then the USB row while a cable is in; the window's `_cc_focus`), stopping at the ends; any tick counts as the wheel for the focus rule (`st.touch_mode`), as a list's does; Center acts on a stop as a tap does, or gives a slider the wheel (`_cc_adjust`, the accent's ring) until Center or Return takes it back (`cc_key`, `cc_wheel`). Return off a slider closes the page. The ring shows only after the wheel or a key: pulled down there is none, the first tick or Center brings it up without stepping or acting (`_cc_seen`), and it goes 5 s after the last (`CC_RING_MS`). With a cable in (`usb_ui_cable`), the USB row lies under the blocks, the cluster's width, with the remembered choice (`usb_ui_value`); a tap or Center on it closes the page and opens the USB picker (`usb_pick_open`), and the row comes and goes with the cable while the page is open (`cc_wifi_poll`). A tap on the Now Playing block, or Center on it, closes the page and opens Now Playing (`playback_np_open`); the transport's buttons lie over it and keep their taps. A toggle held `QUICK_HOLD_MS` opens its settings page ([internals](internals.md#quick-settings-holds)). Coming to rest open, the blocks spring 8 px past rest and back (`cc_land`, `cc_spring`). Volume steps as Now Playing's wheel volume (`vol_next`) and is set as Car Remote sets it (up to Volume Limit, saved for the output in use); Brightness steps as the Brightness page does (`bright_wheel`, [internals](internals.md#wheel-volume)); Car Remote starts or stops its daemon (`coda_rc_toggle`), EQ turns the EQ off or on as PEQ (`eq_toggle`), Smart Wi-Fi its setting; AirPlay closes the page for its settings page. A toggle's name is set glyph by glyph (`ui_set`) in its room; one too wide fades at its edge (`ui_set_in`, `text_fade`) and, while the wheel is on its circle, scrolls through a clear `hscroll_label` of its own whose `xoffset` stock's scroller moves (`cc_marq`). The Wi-Fi circle's glyph is the link as the status row reads it (`cc_wifi`: `wifi_level`, `wifi_offline`), polled every second while the page is open (`cc_wifi_poll`, which redraws that circle alone when it changes).

The rows page keeps stock's controls at opacity 0, for their taps. Their stock images are 60-pixel discs: `#444444` with a white glyph when off, stock red
(`#FF1448`) with a white glyph when on (`drop_wifiopen`, `drop_btopen`, `drop_keylockopen`,
`drop_highgain`, `drop_lo`, `drop_usbaudio`, `drop_usbdac`), gray glyphs when disabled. An active
disc takes the accent's red tone, as everything else red does, so it stands clearly apart from the
gray inactive discs. On a tone brighter than `GLYPH_LIGHT_MAX` (perceived brightness 160 of 255),
which is Graphite's silver, the white glyph and its anti-aliased edges turn `CONFIRM_SURFACE`
(`#2B2B2B`) so the glyph stays legible; Crimson, Tidal, Champagne and Cobalt keep the white glyph (3:1 or
more). Only an image that holds the red disc gets the dark glyph, so inactive discs keep their white
glyphs; off and disabled discs contain no red and keep their stock look, and alpha is untouched.
The brightness suns (`drop_lighleft`, `drop_lightright`) sit on black, not a disc, and keep the
accent's red tone (`DROPDOWN_SUN`).

## Cover Flow

<a id="coverflow"></a>The covers are drawn with depth ([internals](internals.md#coverflow-depth)): the
selected cover at its native 160 pixels, its neighbours a side turned edge-on to it and, while they move, the ones coming in at the
frame's edge, drawn whole from their first pixel, with no reflection floor under OLED (`reflect_top` 0: a
reflection starts at none of its cover's brightness, so it is the page's own black), all in the top
`coverflow.shelf.view_h` pixels of the page (`CF_VIEW_H`, 220). The album name sits under the covers
(`coverflow.caption` `text_y`, OLED's 173, so the artist's tails end on the lists' end, y 282) at the theme's Song title size (OLED's 27 here, the design's
22), the artist under that at its Artist size (22, the design's 18) in stock's secondary text as the
theme inks it, and both, like the
track list's title and rows and the progress and empty messages, keep 36 pixels (`CF_EDGE`) from
each side, where the lowest visible track row's text clears the bottom corners. The covers stand as an iPod's do, from the Cover Flow Parameter Lab in [#329](https://github.com/DiamondBond/q2-pod/issues/329). Long names still
scroll within that. A card (an artist's All albums, the rated albums' Sort) uses the album line.
Values are `coverflow.shelf` and `coverflow.caption` in OLED's `theme.xml` and `CF_*` in `patch/offsets.inc`.

**Opening.** The first open after a restart, a library update or a server catalogue import reads the library's albums first ([internals](internals.md#coverflow)), half a second to a second for 2,000 albums in the emulator; later opens reuse them. Home's Cover Flow row keeps the selection meanwhile and its chevron turns into a spinner (eight spokes, white, in the chevron's 15 px place, [oled-design](oled-design.md#list-row)) until the covers show; Return stays on Home, and the wheel and the other buttons do nothing until it opens. An open quick enough never shows it.

The wheel holds the covers directly ([internals](internals.md#coverflow-thumb)) and moves them as the lists move their rows ([internals](internals.md#wheel-acceleration)): one album a click of the wheel turned slowly, more a click as a fast turn keeps going (two after 0.3 s, one more each 0.2 s, at most 8), back to one as soon as it slows or pauses, and the covers slide through every album on the way; never by letter, and no letter shows over them. Let go, they settle on the album the caption names. From about a turn and a half a second the covers bend into an arch: the ones near the middle drop back level with the side covers and lean in gradually, none swinging out towards you, and as you slow or stop they open out again (with Fast Navigation on there is no arch: the side covers form a narrower row that slides along as the caption goes on ahead). Center, Return, a touch or the screen going dark land on the caption's album at once. The numbers are `coverflow.shelf`'s and `wheel_accel.h`'s.

Center on a cover opens the album's tracks as a list in the page, the covers hidden behind it ([internals](internals.md#coverflow)); Return comes back to them.

**Playlists as covers.** Display → Cover Flow's **Playlist View: Cover Flow** (`Q2POD PLAYLISTCF`, List by default) opens the Playlists list ([internals.md](internals.md#playlist-art)) as covers, from every entry (Library, Home's list, the shortcut): the page opens veiled, and on the next pass the covers open over it, the playlists in the list's order, stopping at both ends. Return closes both and goes back to where Playlists was opened. With no playlists, or in the Add to Playlist picker, the list shows. A cover is the playlist's art at 160 pixels (a placeholder until it is made): the covers are the view; the caption is its name over "N songs" (or "1 song"), in the album's two lines. There are no letters, no Sort cards and no remembered position. Center opens the playlist's own list over the covers, as a tap on its row does, and Return comes back to them. A song plays in the list's order; Now Playing's More then has no Delete, which for those songs would take the song out of the library, not the playlist, until another queue plays. Edits made from the covers show as they come back to them: after More…'s Rename or Delete a deleted playlist's cover goes and a renamed one shows its new name, a new playlist gets a cover, and a playlist whose songs changed shows its new count and art. Hold Play/Pause on a cover gives Play Next, Add to Queue, Shuffle and More… as on the list's row, then **New Playlist**, which closes the covers for New Playlist's letter strip over the Playlists page, and, on a set with no toolbar (OLED's), **Select Playlists…**, which closes them for its select mode (a set with a toolbar edits from the page's toolbar).

## Rounded corners

The panel's glass rounds its corners and hides what is drawn under them. `CORNER_R` in
`tools/ipod.py` (80 pixels) is the calibration knob: the radius, fitted to V5.4I photos and
to stock's 50-pixel status bar margins. `corner_inset(y)` gives the width hidden at each end of
screen row `y`, and `corner_x` adds `CORNER_SLACK` (4). The status row's ends (`STATUS_ROW_INSET`) and
the Home labels are checked against it; settings notes moved under the hidden navbar end their text clear of the
top-right corner. The runtime layouts' values
(`SET_*`, `CF_*`, `CLOCK_EDGE` in `patch/offsets.inc`) are checked against the
same calibration by the tests.

`test/build.py` fails a build when any fixed text or icon in a changed asset reaches
under the glass (screen coordinates: the bar at y 0 to 58, windows at 58 to 320, the quick settings
and confirm pop-ups at 0 to 320): a label's font-high band, an image drawn centered at its size, a
slider's bar, else the widget. Backgrounds, tap targets and list rows, which scroll, are not
checked there; `test/patch.py` checks the settings rows in the first and last visible slots,
Cover Flow's labels and lowest track row, and the status bar clock against every combination of
icons.

## Hold Return

The stock long-key function at 0x4f27e4 retains all instructions except the final
Home call at 0x4f29cc (`HOLD_RETURN`). The hold fires after 700 ms, as Play/Pause's does
([internals.md](internals.md#queue-menu), `HOLD_KEYS`); stock waited 1000 ms. Stock power, lock, test and key-lock gates and its release
latch execute first. The payload cancels center confirmation and spin state, checks
the shared screen/navigation restrictions, and then, on Now Playing, goes back
(`navigator_back`) when the page it opened over (`np_origin`) lies right under it, or
calls the stock Home destination otherwise; anywhere else it remembers the page and calls the stock switch
function with `playing_page` and `{0, 0, 0xff, 2}`. The `0xff` context skips
player_start, so playback is not restarted. The latch stays armed, as on the way
Home: the stock release filter swallows the hold's key-up where it arrives, and
`chord_gate` clears a latch whose release never arrived on Return's next press
([internals.md](internals.md#hold-return)). The hold's release therefore leaves the unit on Now
Playing and the first short Return reaches the stock Back path, with position
memory restoring the browsing page and its selection. No short-Return callback or
other long-key destination changes.

Over Quick Settings (`statusbar_dialog` in front) the hold closes it, as a short Return does
(`dialog_quit`), and, unless the quit was refused, stamps `st.qs_hold`; `quick_poll`, every UI loop pass, runs the hold again once
the dialog has gone and no window moves, so it acts on the page under it, whichever the destination.
The dialog is modal and its own idle closes it a pass or two after the quit, so the hold waits
rather than navigating under its loop; one still up `QS_HOLD_MS` (1 s) later is forgotten. Opened
over the dialog, Now Playing left it open underneath, and stock's pull-down
(`on_wm_tsmove_fun`) starts none while `g_dropdown_menu_state` is not 0, which only the dialog's
close (`0x4a1d30`, on `EVT_WINDOW_CLOSE`) makes it: Now Playing had no Quick Settings until Return
had gone back to the one left open and closed it (#303). Inferred from the handlers; the emulator
mocks the window manager.

Over a prompt (`confirminfo_dialog` in front: Shut Down?, and Q2 Muse's own) the hold cancels it
with `dialog_quit(top, 4)`, the prompt's own Return (`0x499bdc`). `usable` is true there, so the
hold used to open Now Playing under the prompt's modal loop, and the prompt came back over it: a
second Shut Down? (#303). That it was the same prompt back, not a new one, is inferred from a
tester's video and stock's handlers; the emulator mocks the window manager.

**System settings → Controls → Hold Return** (`Q2POD HOLDRETURN`) picks the
destination: Recent (0, the default) is the behavior above; Home (1) calls the stock Home
destination from any page but Home, Now Playing included, and clears `np_origin`. On Home the hold
still opens Now Playing, as does iPod's short Return on Home's first row (on any other row it first
selects that row). A list open on Home (Library, Streaming, Settings) closes first: at once under a page
going Home, with its slide when held on the list itself. The release latch on the way Home: [internals.md](internals.md#hold-return).

## Transitions

Pages slide with stock's own window animator, its page under drawn with parallax
(`page_parallax.c`, [internals](internals.md#page-parallax)). The build sets `anim_hint` to `SLIDE`
(`tools/ipod.py`) on the window of the nine local browsing pages, the settings and streaming pages
and the equalizer page. A hinted page slides in from the right over 220 ms while the page below it
drifts 30% of the width left and darkens, and Return reverses that over 200 ms; the status bar is
not part of either page and stays still.

Left immediate: Home (opened once, never closed), Now Playing (its open starts the player, which
can hold the UI thread; opened from Home's art or an album page, its cover travels into it instead,
[internals](internals.md#now-playing-motion)), Cover Flow (its open loads the albums and covers), the dialogs and quick
settings, the queue, More and jump pages over Now Playing, and the pages that keep their stock
assets: Tidal and the other online pages and the firmware download. Moving between
folders reloads the folder page in place, so only opening and leaving that page slides. Stock
drops every key and touch while a page slides; none is queued
([internals.md](internals.md#page-transitions-ipod)).

A drag right from the screen's left edge is Return by touch ([internals](internals.md#swipe-back),
`patch/swipe_back.c`): the page, or on Home the list a row opened, follows the finger over black,
and let go past 40% of the width or flicked it slides on and Return goes back, with the window's
own close slide left out; let go short it springs back.

A theme can set these from its `motion` view ([internals](internals.md#themes), Motion); OLED takes
the defaults from `patch/offsets.inc`, so its pages open and close with stock's slide or none, as
above, and confirms, add to playlist, the hold menu and every other dialog show at once, with no
fade. A theme with `motion.inplace` would move a level inside a page instead; OLED sets none.

## Chevrons

Stock draws `list_into` on Local Music's categories, the `localclass_page` rows (artists, genres,
composers), the album list and folder rows that are not songs, hidden in multi-select. iPod adds
it, aligned with those (see [internals.md](internals.md#drawing)), only on the `DRILL` windows in
`patch/contexts.inc`: Home and the playlist list, whose rows open their tracks. OLED draws the
theme's own caret in their place (`chevron_icon`, `patch/list_marks.c`; [oled-design](oled-design.md#list-row))
on every row that opens a page, white on the selection. On a set with no toolbar (OLED's), Playlists is a list of two-line rows, each
with its art (the playlist's own picture, up to 12 megapixels, or the first four of its albums with
art in the order its songs were added,
[internals.md](internals.md#playlist-art)) at the row's start, as an album's row, with no toolbar: New Playlist… and Select Playlists… head it, Select Playlists is
stock's select mode with Select All, Import, Export, Delete and Done as rows, and Return leaves that
mode ([internals.md](internals.md#playlists-without-a-toolbar)). Tiles narrower than half the list (the
playlist page's Import/Export on a set with a toolbar) get none. Those two tiles, built by stock at run time,
have no icon: the build gives the icon no width and stretches the label across the tile in stock's
centered `s_scrlabel_white24c` (`tools/ipod.py`). The artist page's Albums tab drills but has no
stock chevron or payload row layouter, so it has none.

The payload's own pages (`page_list`) draw the same `list_into` at x 302 on every row
that opens another page or screen: Spotify's menu, its Downloads hub and Storage Management, its
albums, playlists, artists, saved playlists and download jobs, and Download Controls; a card app's
lists, folders and phone-remote rows; Internet Radio's lists, Games, Ratings, Era's decades and
albums; and the equalizer's Balance, Preamp, Presets, band, Frequency, Gain and Q rows and the
Presets menu, the value moved left of it (`patch/peq_ui.c` `INTO`). A 64 px row with an empty detail
centers its caption beside it. Rows that act in place (play, toggle, cycle a value) have none; on/off
rows (EQ Enabled, a band's Band, Spotify's Shuffle, Streaming Configuration's services) carry stock's
`switch_on`/`switch_off` (`list_toggle`), as Shanling's settings rows do.

## Fast-scroll letter

Spinning quickly through a list of more than 16 rows shows the first character of the selected
row's title in a large white letter, centered over the list on a rounded dark square. It appears
once a step moves more than one row, from 300 ms of continuous spin
([internals.md](internals.md#wheel-movement)), and disappears 400 ms after the last such step, or
at once on a slow tick, a touch or the end of the list. Latin letters show in
capitals; leading spaces are skipped, as is a leading "The", "A" or "An" in library lists
([internals.md](internals.md#sorting)), and any other character shows as it is. Only the
Library's lists in name order take it: Songs A to Z (and a playlist sorted by name, Hi-Res and
Favorites), Albums, Artists, Composers, Genres, an artist's albums, Folders, Cover Flow's covers
and Edit Playlist's Add Songs picker. Every other list is in an order of its own, where a row's
letter would say nothing of how far the spin has got, and never shows it: Wi-Fi's networks,
Bluetooth's devices, settings, Home, the queue, Chapters, Recent, Frequent, Recently Added, Songs
sorted by Time added, a playlist in the order songs were added, an artist's or a genre's songs,
an album's tracks, Most Played, Era, Internet Radio, card apps and streaming lists. Values are in `patch/offsets.inc` (`LETTER_*`); see
[internals.md](internals.md#drawing).

## Now Playing

`playing_page.bin` follows Rockbox's iVideo Now Playing on the full-bleed 375x320 page, its content resting
below the status bar and its clock (a 290 px column), with Apple's finish: rounded art, a larger title over gray artist and
album, and a four-pixel progress line:

```
  0 +---------------------------------------------------------+
    |                                                more 303 |  icon 50x40
 40 +---------------------------------------------------------+
    |  .-----------.                                          |  slide_view 0,40 375x186
    |  |    art    |  Title   (194,58 165x28, white 22)       |
    |  |   16,42   |  Artist  (194,92 165x22, gray 18)        |
    |  |  166x166  |  Album   (194,120 165x22, gray 18)       |
    |  '-----------'              mode 247  fav 303 (y 158)   |  icons 50x50
222 |                     . o .   (page dots)                 |
253 |  (=========================------------------------)   |  line 21,253 333x4
265 |      01:23 (46 80x16)           -02:34 (249 80x16)      |  gray 14
290 +---------------------------------------------------------+
```

The art and the metadata keep 16 pixels from the sides (`NP_MARGIN`) and 12 from each other; the
art is 166 pixels, as large as that leaves while the text column keeps its 165 pixels. Each band
has its own space: the top row, then the art 2 pixels below it, 14 pixels under the foot of a
16-pixel line centered in the top row and as far above the page dots (`NP_ART_Y`), the bar with the stock A-B markers
(y 250 to 260) and the quiet time labels at y 265. The page dots keep their place while a swipe
moves the pages: they are anchored on their own row, at the slide_view's center.

The art's corners are rounded at 12 pixels (`NP_ART_RADIUS`), the radius of stock's own
placeholder cover at this size, so real art and the placeholder match; a theme's
`playing.art.radius` sets its own (OLED's 6). The payload paints them
over the scaled image in the page's black, with an anti-aliased
edge pixel, so opposite corners share the same pixel grid. The title is 22 pixels
white (`NP_TITLE_PX`) over the 18-pixel (`NP_META_PX`) artist, `#CCCCCC` (`NP_ARTIST_RGB`), and
album, `#BBBBBB` (`NP_ALBUM_RGB`); both times remain `#AAAAAA`. The page shows no place in
the queue ("3 of 12"): the top row holds More alone. Spotify uses the same hierarchy. Long lines scroll, as stock.

The art follows the player's cover on both Now Playing pages (`np_art_poll`, each lit UI pass).
Stock's 250 ms timer reloads `img_cover` only when it finds one of three one-shot flags set: a
song change (`g_autochange_flag`), a forced refresh (`g_forcerefresh_flag`) or the cover thread's
finish (`g_playcover_finishflag`). Enlarged Art's timer (`0x52dac0`) clears all three while
`bigcover_page` is open, so a change it took left the page on the last track's art. The payload
keys the playing track and the cover it can use, once `g_lastcover_url` is this track's path:
`/tmp/coverpic.jpg` (type 1), `/tmp/externpic.jpg` (2, 4) or stock's placeholder (3, none), with
that file's size and modification time (`now_cover_written`, a `stat` of tmpfs). A card app's URL
queue needs that: its songs have no path and a cover by URL leaves `g_lastcover_url` empty, so the
key was the same for every song and the first one's art stayed. Stock's own request can't tell
them apart: the cover thread clears it before the download, and a cover asked for while the last
one downloads lands after that one, both as type 2. Each write has its own time, as the thread
waits 100 ms between covers. Home's art and Enlarged Art's shape take the file into their keys
too; a paint after a song change, before its cover lands, still shows the previous file. When
that key changes, it reloads the art the way stock does, dropping AWTK's cached bitmap of the name
first, since the player writes every track's cover to the same file. While the cover is still
being parsed, and for Tidal, it leaves stock's image. ponytail: one more decode per track change while the page is up, beside
stock's own reload.

There is no on-screen transport row: the buttons play, pause and skip.

OLED lays the page out on its own grid ([oled-design.md](oled-design.md#now-playing)): the
150-pixel art at the 24-pixel text column under the status row, which shows the clock, its corners
6 pixels; the title, artist and album 18 pixels right of it, ending on the 28-pixel right column,
the artist and album in Inter's 18 in Text Secondary, the album one line ending in an ellipsis; the
heart, 34 pixels, in the gap under the album; More at the right end of the art's foot, on an 18-pixel
line whose baseline is the art's bottom row; a 6-pixel bar from the text column to the right column at y 228,
the times under its ends in Inter's tabular digits, and shuffle and repeat centered under them in the
accent when on, gray when off, over the play mode button: a tap on shuffle turns it on or off and
a tap on repeat steps Off, All, One, each keeping the other. The page dots
sit under those, and the swipe's other pages stop above the bar. Each line that scrolls fades at
the end it runs past (Display's [Theme](#theme)).

The art, title, artist and album are the slide_view's first page, so a swipe replaces all of them
with the lyrics page, and another with the [rating page](#rating-page); three page dots. The track's details are not a page of
the swipe: ⋯'s last row, **About** (under OLED last before Delete Song), opens them as a page of their own, titled
About, and Return goes back to ⋯ (under OLED its panel, over Now Playing), as from ⋯'s other
rows' pages. The build takes stock's info page out of the slide_view and leaves it hidden in Now
Playing's window, where stock fills it; About moves it into its own page while open, at the same
place on screen (under About's title where that is a row of the page), so the details look as they did on the swipe (`tools/ipod.py`
`np_info_out`, `patch/np_about.c`). The details keep their stock 225-pixel column, centered; the lyrics
page spans the screen (**Lyrics** below). The details' lines read `Label: value`, with spaced units
(`35.92 MB`, `16-bit`, `950 kbps`, `44.1 kHz`), **Genre** and **Year** for stock's Genres and Era,
`Unknown` for a missing value and `Location:` before the folder (`ringnav_np_info`). Under OLED About is an info grid instead ([oled-design](oled-design.md#track-details), `patch/np_about.c`): the cover, title, artist and length; a quality line, a Lossless, Hi-Res Lossless or DSD badge and the format's spec (`FLAC · 16-bit / 44.1 kHz`, `MP3 · 320 kbps`); then small gray names over white values, two a row, Album, Year, Genre, Bit Rate and Size, and Location across the last row in plain words (`SD Card · Music/Disclosure`, `Server · Downloaded`, `Server · Streaming`). A value that is unknown leaves its cell out, and the wheel scrolls the page when it is taller than the screen. A Genre line too long for the column scrolls at the album marquee's pace while About shows (`genre_step`). The play/pause overlay has zero opacity, retaining its original gesture target without
a transport glyph; stock may update its visibility without making it appear. The loading
spinner stays centered on the artwork. The on-screen Return icon moves off-screen, as on the pages whose navbars are
hidden; the hardware Return does the same. More keeps its place at the top row's right. The play
mode (shuffle/order) and Favorite icons sit at the foot of the art page under the text, the
favorite centered directly beneath More at x 303, with the play-mode control 56 pixels to its left, leaving a 6 px gap between touch targets;
the three lines center in the art's height above them. Both retain their 50 px touch targets and
vertical position. Both keep their stock images and handlers and swipe away with the art.
Internet Radio uses the same heart alignment.

The bar is a plain-color line inside a 44-pixel seek target, with no thumb: a track in
`playing.scrubber.track` (`TRACK_COLOR`, `#1C1C1C`, where a theme states none; OLED's `#333333`) and a
fill in the accent, `playing.scrubber.height` high (OLED's 6; `bar_size` and `round_radius` set on the
slider at runtime, `np_theme`). Spotify's bar follows the theme's height up to its 8-pixel view.
The asset holds Graphite's; `ringnav_playing` sets the current accent's. The volume shown over the page draws the same bar in the same fill. The wheel moves the volume 1 a tick turned slowly and up to 10 turned fast, with Rockbox's iPod acceleration curve, and its bar eases to each new level rather than jumping (every page that shows the volume does both; [Wheel volume](internals.md#wheel-volume)). Tap or drag anywhere in the bar's taller target to seek; the page dots sit just above it so page swipes keep a separate target. The elapsed time
is stock's label; the remaining time replaces stock's total. Sizes are `NP_*` constants in
`tools/ipod.py`; see [internals.md](internals.md#now-playing-ipod).

**Links.** On the art page, a tap on the artist opens that artist, as ⋯'s Look up Artist does, and a tap on the album or the art opens that album's
songs, as ⋯'s Look up Album does: stock's album page, never a carousel. Return comes back to Now
Playing. Where the hold menu offers no Go to row (a stream, AirPlay, a song with no album or artist
tag, its page already open) the tap does nothing. While the finger is down the artist or album
turns the accent's text color and the art dims 15%; a touch that moves more than 10 pixels is a swipe,
which changes the page and goes nowhere. The taps use the layout's own boxes
([internals](internals.md#now-playing-links), `np_taps.c`). The wheel's way to the same places is
the hold menu or ⋯.

A hold on the art, half a second without moving, opens stock's full-screen art (`bigcover_page`;
any tap or Return closes it), whatever Enlarged Album Art is set to, and while the player is not
stopped, as stock's tap opened it; the art is dimmed while held, and letting go after it opens
does not also open the album. A stream's art holds to full screen too, though its tap goes
nowhere. Enlarged Album Art's idle countdown still opens it as well. Stock fits the
cover to the whole screen, 320 pixels high, so the glass hid its corners; iPod centers it on black as
the largest box of the cover's own shape whose corners clear the glass by `GLASS_SLACK` (`bc_fit`; a
square cover is `BC_ART` in `tools/ipod.py`, 292 pixels with the defaults: 41 from the sides, 14 from
the top and bottom; a 16:9 one 360 by 202), so the whole cover shows,
rounded as Now Playing's is. `bc_fit` sizes it as the window paints. It decodes the image for its
shape only when the track, stock's cover or the image changes, past the image manager's cached copy
(the player reuses one file name, so that may be the last track's). A player file counts only once it
is its `g_playcover_type`'s and `g_lastcover_url` is this track's, as Home's art; until then the art
keeps the shape of `g_lastcover_url`'s cover, so a skip doesn't flash the square, or takes the square
once that is another track's (Tidal's URL never is a queue path, so its art stays square). Stock's
`default_bigcover` placeholder is the square. A layout back to the square refits from the kept shape. Its song name and the shade band behind it sit on the art's foot, show for `BC_MS` (3 s), then fade out together over `BC_STEPS` steps
`BC_STEP_MS` apart (`ringnav_bigcover`, after stock's init). They show again for each new song while it is open.

**Scrub.** A press of the center button starts scrubbing, as on an iPod classic (with the screen off it only
wakes it, [Wake](internals.md#wake)), and a 14 px white knob marks the playhead while it lasts, the fill keeping the accent (on a set with
no status row the fill turns white instead, `np_fill`). Each wheel tick moves 5 seconds, times a ramp of one more step per 100 ms of spin
(up to 40 seconds a tick; the lists' gentler ramp does not apply), within the
track. Both times and the bar follow the target at once, and so does the music: playing, the
track jumps to the target as you turn, at most every 200 ms (`SCRUB_SEEK_MS`, `patch/seek.c`), so a
fast spin is heard as a few jumps and lands on the target. A press of the center button confirms it at once
and leaves the screen on (and moves on to shuffle, below), as do Return, a touch and
3 seconds without a tick, and each gives the wheel back to the volume; Return then stays on the
page. Ending without having moved the target does not seek. Paused, the track stays paused and
plays from the target at Play. Center works as an iPod classic's
(`np_centre`): a press starts scrubbing at once, and the next ends the scrub and puts a ring on
Shuffle. The wheel then moves the ring across Shuffle, Repeat and the heart, changing nothing;
Center works the ringed control as a tap does (Shuffle on or off, Repeat Off, All, One, the heart's
like on or off) and the ring stays. While a radio plays the ring has only the heart; under a theme
without the pair there is no ring and the scrub's press ends the cycle. Return, a touch or 3
seconds without a key end the ring. A double press (within `DOUBLE_CLICK_MS`, 250 ms, the wheel
not turned) or a short hold (`NP_HOLD_MS`, 400 ms) turns the screen off, and a long hold is
still Shut Down. The presses work the same while the volume shows
`patch/offsets.inc`; see [internals.md](internals.md#scrub-ipod).

The top row's text and icons, the bar's ends and the times keep clear of the corners
([Rounded corners](#rounded-corners)).

<a id="lyrics"></a>**Lyrics.** The lyrics page shows the lyrics large, for reading at a glance, as Apple Music's do:
the playing line centered, the one before above it and the next below, on the 24-pixel text column
(`LYRIC_OLED_X`). Every line is Inter (the design's 22 px, AWTK's 27, `LYRIC_OLED_PX`), the playing line
in Medium and the rest in Regular; a long one wraps, at a space or beside a CJK character, onto as
many rows as it takes, up to four, which still fit the page (the rest cut), `LY_PITCH` (30 pixels)
apart, with `LYRIC_GAP` (21 pixels) between lines. No row is dimmed whole: the top and foot 32 pixels
of what shows of the page fade linearly to the page's ground (`lyrics_fade`), so a line leaving the
view thins out rather than being cut, while it scrolls too. The playing line lights white
(`LYRIC_LIT`); its unsung part and the other lines are Text Secondary (`#ABABAB`,
`LYRIC_OLED_REST`). A line with word stamps (A2's enhanced LRC, `<mm:ss.xx>` before a word, in a .lrc or a
LYRICS tag) lights word by word as it is sung, each word from its stamp to the next (the last, with
no stamp after it, at `LYRIC_UNIT_MS`, 90 ms, a letter, done by the next line); a line timed by the line alone, as LRCLIB's are, lights whole as it
starts. Lines that start together, as a line and its translation, play
together. Blank lines in the lyrics (empty lines between verses, a stamp with no words, lines of
spaces) are left out and the spaces at a line's ends trimmed, so the lines keep an even spacing. Before the first line the first waits in the middle, unlit. Lines change at their
hundredths of a second, by a clock of the page's own between the player's whole seconds. On the
lyrics page, while the track has lyrics, the wheel scrolls them smoothly, a pixel at a time as it
turns and as far a turn as a line a tick, instead of changing the volume or scrubbing; a scrub
under way ends first, as any other end does. While the finger stays on the wheel they stay
exactly where it put them; let go, they glide to the nearest whole line. On the page, the follow
to a new line glides too. While they follow, the wheel meets a list end's resistance: travel short of
`LYRIC_PUSH_DETENTS` (2) ticks within `LYRIC_PUSH_MS` of the last only nudges them `BUMP_PX`, held while the
finger stays and springing back when it lets go, still following, so a stray touch keeps the follow.
Lyrics moved by the wheel or a finger stay where they are, while the bar and the
highlight go on, a swipe to another page and back included, until Now Playing closes or another
song starts at the top; timed lyrics left alone for 5 seconds (`LYRIC_RESUME_MS`; no turn, no
touch on the page, no thumb resting on the wheel, on the page or away from it) go back to the
playing line and follow it again. Untimed lyrics have no playing line, so they stay where they
were left. A tap on a timed line other than the playing one plays the song from that line's
start (its whole second; paused, from there on Play), and the lyrics go to it and follow on; a
drag of the lines seeks nowhere. A tap on the playing line, off the lines, or on untimed lyrics
shows them full screen, and another brings the page back. Center on the lyrics page starts a
scrub of the bar, as on the art page, which the wheel's next turn ends as it scrolls the lyrics; the
lyrics have no selected line for Center to play. A song from a card app's server (Subsonic's) with no lyrics of its own has them asked of its app
while the page shows it; the page shows none until they arrive, then fills in
([Lyrics from a card app's server](internals.md#server-lyrics)). Under OLED the album art's glow is behind them
([Rating page](#rating-page)). See [internals.md](internals.md#scrub-ipod) and [Word-timed lyrics](internals.md#word-timed-lyrics).

<a id="rating-page"></a>**Rating page.** A swipe past the lyrics shows the song's rating: its title and artist, five
stars and the page dots, full screen over a soft glow of the album art's colours (OLED's
[Rating page](oled-design.md#rating-page) has the sizes). The stars are the song's rating, 0 to 5, and nothing else:
the heart on the art's page is a like, apart from them. Ratings of 1 to 4 given before show as they were; a song
that was a favourite is still liked and has no stars until it is rated here. Turning the wheel
moves a target star, drawn larger, from the rating's top; Center keeps it. A tap on a star keeps it at once. The
star that is already the rating's top, tapped or kept by Center, clears the rating. With nothing turned, Center
puts the target on the rating's top star. A stream or radio, which can't be rated, leaves the wheel on the volume.
This page is the one place to rate: the heart on the art's page only likes a song (a tap, or a press held however
long), and the hold menus have no Rate row. The glow is made once for each song's cover, not per frame, and is behind the lyrics page as well.
See [internals.md](internals.md#rating-page).

## Previous and Next

The wheel's Previous and Next work as an iPod's, from any page where they control the music:

- **Next**, pressed, plays the next track.
- **Previous**, pressed, starts the track again from 3 seconds in, and before that goes back to the
  track before. So one press restarts a song, and two in a row go back one. Apple's iPod classic
  guide says only "press Previous/Rewind" to start a song over and "press twice" for the one
  before; 3 s is Rockbox's figure (`DEFAULT_SKIP_THRESH`, 3000 ms, `apps/gui/wps.h`), which an iPod
  recreation measured on the hardware also reports. Apple's iPod shuffle guides give 6 s for that
  model. A track holding a place to resume still asks first ([Resume](internals.md#resume)).
- **Held** (half a second), Next fast-forwards and Previous rewinds, and you hear the song as it
  goes, faster the longer you hold. With the new player (`q2player`), the song plays at 2 times
  its speed for the first 2 s of the hold, then 4 times, then 8 times, at its own pitch, like a
  tape on fast-forward that does not squeak; from 6 s it jumps ahead 8 s each half second, still
  heard at 4 times (16 times overall), and from 10 s 16 s each half second (32 times). Rewind
  steps back as a CD player does: half a second of the song heard forward at twice its speed,
  then a step back, the steps growing so the scan goes back at 2, 4, 8, 16 and then 32 times the
  song's speed over the same times. Letting go plays on at normal speed: after fast-forward from
  exactly what you were hearing, after rewind from the second you were hearing.
  With the stock player, or for a song the new player cannot
  speed up (DSD, or above 192 kHz), you hear half a second of the song at its own speed, then a
  jump, then another half second: each jump is 2 s for the first 3 s of the hold (4 times the
  song's speed), 4 s until 6 s (8 times), 8 s until 10 s (16 times), then 16 s (32 times). The
  bar and times follow. Fast-forward stops a second before the end, plays out into the next
  track and goes on scanning that; rewind stops at the start and plays from there. Paused, the
  track stays silent and paused: the bar moves, and Play starts from where you let go. A held
  button does not skip.

Apple's guides say only "press and hold" to fast-forward or rewind; the speeds and the stretches
are this firmware's, in the spirit of the iPod's audible scan. At 96 kHz the new player plays at
most 4 times and at 192 kHz at most 2, then jumps sooner. Streams (Spotify, radio, Tidal's online
queues), games, the playlist editor and the time and sleep-timer fields keep the buttons as they
were, and a held press there does what it did. Values are `SEEK_*` and `seek_phases` in
`patch/seek.c`; see [internals.md](internals.md#scan) and [player.md](player.md#scan).

## Pop-ups

The confirm and choice dialogs in `patch/contexts.inc` (flag `BUTTONS`) have no list: their buttons
sit directly in the dialog. For those, the dialog itself is the navigation surface, a kind that
never scrolls, and its clickable descendants are the rows in UI order. The wheel moves between
them with hard ends, Center clicks the selected one after the usual double-press window, and the
bar is drawn in the dialog's background: the button's own rectangle for a button narrower than half
the dialog (the confirm pair), the full width otherwise. A new dialog starts on its first button,
Cancel on the confirm pair.

The confirm pair (`img_cancel`, `img_enter`) is two full-width 48-pixel text rows under the prompt,
**Cancel** at y 208 and **OK** at y 256 (`tools/ipod.py`, after maks112v/qpod), instead of stock's
80-pixel disc tiles: the wheel's selection is then the lists' own bar or card, with no frame. Stock
still finds both by name and binds its callbacks; the initial Cancel is stock's. The image hook
still gives the discs (`confirm_ok`, `confirm_cancel`) `CONFIRM_SURFACE` (`#2B2B2B`) under every
accent should stock draw them. Tidal's own confirm pop-up (cyan, black glyphs) keeps its look and
its tiles, framed in white when selected.
The confirm itself is the confirmation card over the page that asked (`patch/feedback.c`,
[oled-design](oled-design.md#confirmation-card)): its title asks the question ("Shut Down?", "Delete Playlist
“a”?"), and its answers are **Cancel** and the action's verb ("Shut Down", "Reset", "Delete"), a verb that
loses something red; Delete Song's "Also delete source file" is the card's switch row, which
Center turns. Its toasts are a pill near the screen's foot over the page, which keeps its status row and
selection.

| Dialog                        | Buttons                                         |
| ----------------------------- | ----------------------------------------------- |
| `confirminfo_dialog`          | Cancel, OK (full-width rows)                    |
| `tidal_confirminfo_dialog`    | `img_cancel`, `img_enter` (80x80, side by side) |
| `autoshutdown_dialog`         | `btn_cancel`                                    |
| `playlistsmore_page` (a page) | Rename, Delete Playlist, Edit Playlist          |
| `tidal_quality_select_dialog` | four quality rows, `btn_ok`                     |
| `tidal_sortmode_dialog`       | three sort rows, `btn_cancel`                   |

`sortselect_dialog` and the search result dialogs already navigate their lists. Left out, so the
wheel does nothing there (the letter strip takes it instead, [The keyboard](#keyboard); [Wheel volume](internals.md#wheel-volume)): the text-entry dialogs (`addplaylist`, `editwifi`, `kbwifiadd`,
`kbwifipass`, `renameplaylist`, `searchbox`, `tidal_searchbox` and Baidu's `edit_dialog`), the Update
Local Music progress (`updatemusic_dialog`, whose only button cancels a scan that can run for
minutes), the pull-down quick settings (`statusbar_dialog`, whose rows the wheel walks, handing it to the brightness slider on Center: see
[internals.md](internals.md#setting-editors)), and the dialogs without buttons
(`volume_dialog`, `msginfo_dialog`, `checkfw_dialog`, `showsn_dialog`, `dialog_wifibt_test`).

## Boot

The firmware starts on Home with the remembered queue and track restored paused; In-Vehicle Mode
keeps the stock start on Now Playing. See [internals.md](internals.md#boot-resume-ipod).

## Display settings

`systemset_display_page_init` (`0x4c7168`) destroys the children of `scroll_view_display` and
builds three rows with `0x4c6b94`: a `list_item_create(view, 0, 0, 0, 0)` in `s_listitem_black`
(the list view lays it out 78 pixels high), holding a `button_create(item, 20, 0, 335, 70)` in
`s_btn_listitem` with a click handler, and in it a 52-pixel icon at x 10, a
`s_scrlabel_white24l` `hscroll_label` at (72, 0, 210, 70) and `list_into` at x 282. The rows
show no value; each opens a sub-page (iPod's [settings rows](#settings) then lay them out 68
pixels high). The build destroys stock's Lock Screen and Show Navigation Bar rows (Q2 Muse has no lock screen: [Screen off and wake](internals.md#screen-off-and-wake)), then adds a "Smart Awake" switch and Night Shift, and, the same way with stock's `list_into` chevron, **Appearance**
(`system_display`) and **Cover Flow** (Play settings' cover mode icon, `playset_covermode`), in that order: Brightness, Screen Off Time, Smart Awake, Night Shift, Appearance, Cover Flow, the **Enlarged Album Art** switch (stock's `PLAYSET COVERMODE`, Now Playing's art), and last the **Reduce Motion** switch. System settings' **Controls** row (Play settings' folder jump icon, `playset_folderjump`) is the third sub-page. Each opens stock's
Display page again (`display_open`, `display_sub` naming the sub-page while its init runs), which
drops the stock rows, takes the sub-page's title and builds only its own rows:

- Appearance: the theme's options in its theme.xml's order (`setting_row`), under OLED "Light Mode: Off"
  ([Light Mode](#light-mode)), "Accent: Blue", "Home: Split" and "Battery: Icon" (Icon or
  Percent; see [Status bar and clock](#status-bar-and-clock)), then its own options, "Song Artist: Inline",
  "Song Length: Albums" and "Track Time: Remaining" ([Theme](#theme)). Under OLED (`set_text`) a row shows its
  value on the right column, apart from its label.
- Display's main page, last: a "Reduce Motion" switch (`IPOD REDUCEMOTION`, off unless chosen; `ui_kit.c`
  `motion_reduced`, read with the other `IPOD` keys and written by the row's click, so the next animation follows it),
  captioned "Fades in place of slides, zooms and bounces": what it changes is [oled-design.md:
  Motion](oled-design.md#motion).
- Display's main page: a "Smart Awake" switch (`IPOD KEEPLIT`, on unless chosen, and a reset's `config.ini` says 1;
  `keep_lit_poll`): with Now Playing lit in front on its lyrics page, or its
  full-screen art (`bigcover_page`) or ⋯'s About in front, and a song playing, it calls
  `reset_poweroptions_timer(1, 1, 1)` once a second, so the screen stays on there (with Enlarged Album
  Art on, the art page too, once its countdown opens the full-screen art);
  paused, it times out as usual. A card game in Games is held open and lit while it is on, playing
  or not, with no idle quit ([Card games](internals.md#card-apps)). Enlarged Album Art never opens off the art page (#57): while Now Playing's slide view is on
  another page, or ⋯'s About is over it (`np_about_page`), each `ringnav_sleep` pass holds stock's countdown (`BC_IDLE`) at its full 10 s.
  Before, the window opened and closed a pass later, flashing over the lyrics every 10 s (#153).
- Cover Flow, what changes Cover Flow alone: "Sort: Album", a picker of Album, Artist, Year, Recently Added, Most
  Played and Random (`coverflow_sort_set`), a "Fast Navigation" switch (`Q2POD BIGLIBRARY`, off;
  `playset_covermode`): the narrower row at speed in place of the arch, a
  "Position Bar" switch (`Q2POD CFPOSITION`, off; `playset_covermode`; coverflow_position.c), then after a 12 px gap
  "Refresh Cover Flow" (`system_update`; it empties `.coverflow/`, the playlists' art with the albums', which builds
  again). The Library's own settings (Artist View, Playlist View, Scan Song Durations) are on
  [Settings > Playback > Library](#settings).
- Controls: Wheel Sensitivity, which opens its slider page; "Hold Return: Recent"
  (`Q2POD HOLDRETURN`, [Hold Return](#hold-return), `playset_folderjump`); "Clicker: Both", Key Tone's row, cycling Speaker, Headphones, Both and Off (`IPOD CLICKER`, Off being Key Tone off; [Key Tone](internals.md#key-tone)); Buttons Lock, which opens stock's page; and "Wake: Any Button" (or "Wake: Double Press").

All are among the [settings icons](#settings-icons) the build pre-sizes. A value row has its value
in the label (260 pixels wide, to where the chevron ends) and no chevron, since Center or a tap
changes it in place; on a `set_text` theme (OLED) the label is the title alone and the value stands
on the right column (`row_value_text`), as with the Clicker, Boot to and AirPlay's "Speaker" rows.
The build also appends **Boot to: Rockbox / Q2 Muse** to Battery when Rockbox is present; see [Boot](boot.md#rockbox).
General ends with **Restart**, **Shut Down** and **Reset Settings** (`patch/power_rows.c`), 12 px below the rows above them (an empty `label`, which
the wheel passes) and with no chevron. Each asks first in stock's `confirminfo_dialog`, drawn as the confirmation card
("Restart?" or "Shut Down?" as the title, then Cancel, selected, and the verb); Cancel or Return leaves the page as it was.
Restart reboots (`ringnav_reboot`: the queue saved, then stock's `device_reboot`), with no message first. Shut Down asks stock's own `msg_poweroff` and opens stock's `poweroff_page`, as the power key's Shut Down? and Auto
power-off do; its `into_poweroff` saves the queue.
Reset Settings, the last row and the one that loses settings, is drawn in Negative red and keeps its chevron, as it opens stock's reset page; Shut Down is not red, as it loses nothing (the confirm card's consequential kind).
The page is `CTX_FIXED`, so the wheel walks onto them like the stock rows.

A change is saved at once with the stock `write_int_config(value, "IPOD", key)` (`0x4fe67c`):
`sprintf("%d")`, then `toolsWriteConfig("/mnt/data/config.ini", section, key, text)`, which
rewrites the key or appends `[IPOD]` with it (`"[%s]\n%s=%s\n"`; its hook first starts a section run into the line before it on a line of its own, [config.ini](internals.md#config-ini)). The keys are `OLED_ACCENT` (OLED's accent), `LIGHT` (Light Mode: 0 Off, 1 On, 2 Auto; missing is Off), `HOME`, `BATTERY`, `SHORTCUT`, `KEEPLIT`, `CLICKER`, `THEME` ([Theme](#theme)), and OLED's own `SONGARTIST`, `SONGLENGTH` and `NPTIME`; `ACCENT`, the accent key of a theme that names none, is read but not OLED's. The retired themes' keys (`MINIMAL_ACCENT`, `CLASSIC_LIGHT_ACCENT`, `ROUNDED`, `STATUSBAR`, `NPCENTRED`, `SCRUBSINGLE`, `HOMELIB`, `ROWPREVIEW`, `ROWART`) stay in the file, unread. The values are read once,
on the payload's first use (after stock `config_init`: `application_init` runs `platform_init`, which
calls it, before it opens any window), with `toolsReadConfig` (`0x5cfff4`), in the order stock `config_init`
calls it: `(path, section, key, out, default)`. It reads the file line by line
(`strcasecmp` on the section and the key), copies the trimmed value to `out` and returns 1; a
missing key copies the default and returns -1. The default must not be null (stock reads its
first byte). The payload passes `"0"`, so a missing or unreadable entry, or any value that is not
a valid one (up to three digits, below the key's count), is Split and Icon; a theme's own key (`OLED_ACCENT`,
`SONGARTIST`, ...) reads its option's default instead (Blue, Inline, ...), and a stored value outside an option's
values reads as its default, the key untouched (`settings_read`). A `THEME` other than 3, or none, is a unit
from a build with more themes: it takes OLED's preset once ([Theme](#theme)).

OLED offers one accent, its own Blue (`OLED_ACCENT` 9, after the nine `ACCENTS` rows): `#2160D8` in all
five tones (`<colorScheme name="oled-blue" base="cobalt">`), the selection bar, the progress fill and
every red stock draws ([Theme](#theme)). The engine keeps the `ACCENTS` rows (`patch/offsets.inc`:
Graphite, Crimson, Tidal, Champagne, Cobalt, Tangerine, Plum, Ivory, Green) for a theme to offer;
Crimson returns every color unchanged. Grays and artwork remain unchanged by the live accent
mapper. See [internals.md](internals.md#accent) for the mapping.

Now Playing uses 24 px monochrome control glyphs on 28 px canvases inside the existing touch targets. Favorite keeps its outline/filled states; inactive dots are muted. Artwork, scrolling metadata and playback controls retain their existing behavior.

### Theme

OLED is the one theme, built to [oled-design.md](oled-design.md): black, white sentence-case Inter, one
blue, on its own layout. `assets/themes/oled/theme.xml` stands alone (it extends no other theme), states its
own grid (its `<geometry>`) and is row 0 of the theme table, so Appearance has no Theme row and
there is no picker ([internals.md](internals.md#themes)).

`IPOD/THEME` stays, always 3, the number OLED had among the four themes there were. A unit an older build
left on another (`THEME` 0 Minimal, 1 Classic, 2 Modern, or no key, which read as Minimal) takes OLED's
preset once, at its first boot on this build, as choosing OLED did, and keeps 3 from then on (`theme_boot`,
`theme_preset` in `patch/navigation.c`): Light Mode Off unless it was on Auto, Blue (`OLED_ACCENT` 9), Home
Split, Song Artist Inline, Song Length Albums and Track Time Remaining; the battery is kept
(a Battery value OLED does not offer, Icon + Percent or Percent Inside, reads as Icon), and only values
that change are saved. The retired themes' own keys stay in `config.ini`, unread ([Display
settings](#display-settings)).

An album grid's cover sits inset inside its tile (`album_children`), so the selection card frames it.
Now Playing's bar has no thumb: stock draws its dragger (a box and knob, the stock theme's blue) unless
`border_color` is clear and the style has no `icon`, so `playing_page` clears the first in every state and
`slider_knobless` the second, which stock's default slider style would otherwise supply. 1.3.9 first
painted it white, which drew the box at rest.

- **The status row.** No bar band: the page's title, small and gray, from x 62, and on the right, right to left, a
  slim battery ending 62 px from the right edge (`STATUS_ROW_INSET`), three level bars while something plays (a pause
  in their place while paused, in the same white; a tap on either opens Now Playing), Wi-Fi's signal while connected
  (iOS's fan; a crossed glyph with no link past the access point), a card app's sync (turning arrows while it checks, a filling ring while it
  downloads), a Bluetooth headset's battery, a 13 px padlock while Buttons Lock is on, and an orange crossed-out cloud while a card
  app's server can't be reached, each the clock's digits' height (11 px) on their
  rows, 6 px apart, up in the glass's top curve and clear of it ([Status row slots](internals.md#status-row-slots)). A press Buttons Lock
  blocks with the screen off shows a card saying so for 1.5 s, as the iPod's Hold switch did ([Buttons
  lock](internals.md#buttons-lock)). The clock, in the title's face and gray, is centered across the row,
  and the title ends with an ellipsis 12 px short of the widest clock ("00:00"), however long it is: under a status row a
  page's caption never takes a row of its own (`bar_title_fits`), so the list keeps its top. Glyphs that would reach the clock
  scroll between it and the battery instead (the strip's marquee). A page with no title (Home, Now Playing, Cover Flow's covers;
  an artist's are titled by the artist) has the clock centered too, but Home in Split puts it over the list at the row's left, its text 62 px in (`STATUS_ROW_INSET`), as the battery ends 62 px from
  the right; the glyphs stay over the art at the right, inked white or black, one ink for the row (`clock_place`, `home_ink`).
- **The grid.** 32 px rows from y 58 with their text at x 24, seven to a screen: the list ends at y 282, above
  the bottom corners, and the strip below it stays black (`list_end`, `fit_rows`). Chevrons and values share one column 28 px
  from the right edge on every row. Settings, the Library, Home (Full and Split) and stock's own list pages all
  take it; Albums and stock's search results are two lines at 52 px, four to a page, the covers with 4 px corners. A row that
  opens a page and shows a value (Resume Songs, Volume) has the value just left of its chevron. The status row
  names the Library and Songs by those words, where stock calls both "Local Songs".
- **Color.** Black (white in Light Mode), white text, second lines in Text Tertiary (`#858585`), a settings row's
  value in Text Secondary (`#ABABAB`, `#5C5C5C` on paper), and one blue, `#2160D8`, for the selection bar, the
  progress fill and every mark stock draws red. **Accent** offers that Blue alone (`OLED_ACCENT`).
- **Type.** Inter Regular for rows (20 px) and second lines (15 px), Inter Medium for the status row (15 px) and
  Now Playing's title (22 px), with tabular digits ([Its font](#oled-type) below).
- **The selection.** The accent's flat bar, 16 px in from each side with 8 px ends on every row.
- **Scrolling.** A short track and thumb on the right edge, beside the list's middle, while the wheel turns; it
  fades a second after the last tick. A title that scrolls (the selected row's, the status row's, Now Playing's
  lines) stays in its own column and fades out over 12 px at the end it runs past: the right while there is more
  to come, the left while its start is out of view, so at rest its start is crisp. A long title that isn't
  scrolling keeps its ellipsis.
- **Now Playing.** On the grid as the mock-up shows ([Now Playing](#now-playing)).
- **Icons.** Phosphor's (MIT, [oled-design.md](oled-design.md#icons)): the caret on each row that opens a page,
  the play state, and Now Playing's shuffle, repeat, heart and More.
- **Home.** Split by default (OLED's preset, and a missing `IPOD HOME`, 0). In Split the menu's rows are 204 px wide, so the selection bar ends at x 188 and the
  chevrons stand at 176, and the playing album's art fills the rest from x 196 and the top edge to the right and
  bottom edges, panning slowly, the glass rounding its corners; the status row's play state and battery stand over
  it, each pixel white or black by contrast with the art beneath (`split_row`, `split_art`, `split_art_up`,
  `split_inset`; [Home](#home)).
- **Song rows.** One line each, no chevron. In Songs and the other lists stock shows on Songs' page (a genre's
  songs among them) the artist follows the title two spaces on, in 15 px Text Tertiary, both ending in an ellipsis
  where they run out of room and the artist dropped when under 40 px would be left; an artist's own songs show
  none; an album shows each track's number in a 14 px column before the title (from x 50) and its length on the
  right column, once Scan Song Durations has measured it. **Song Artist**: Inline (the default), Selected Row (the
  selected row's artist on the right column), Right (every row's) or Off. **Song Length**: Albums (the default), All
  Lists or Off; with a length on the right column, Right and Selected Row put the artist inline. Lists Q2 Muse builds
  itself (Most Played, Long Songs, Ratings, Cover Flow's tracks) keep their own rows.
- **A list's search bar** ([oled-design](oled-design.md#search-bar), `patch/search_bar.c`): on every music list
  page (Songs, Artists, Albums, Genres, Playlists, Years, Ratings, Most Played, Long Songs, and inside a playlist,
  an artist or an album) a turn up from the first row brings in a search bar above it, "Search <the page's title>";
  Centre opens the letter strip below, which filters the page's own rows as the text is typed. Return closes the
  strip and lights the bar, the text and the filtered rows kept; a turn down goes into them, Return again goes back
  to the page with its bar lit, and a third leaves the page. The go key goes to the first match, or with none lights
  the bar over No Matches. Erasing on the strip clears the text. A match opens its row on the page, as the row
  itself does there. Home's Search searches everything.
- <a id="search"></a>**Search.** Home's Search row (and Songs' pull) opens an iPod classic's letter strip, not
  stock's search box and keyboard (`list.entry.ipod`, `patch/search_strip.c`, the user's mock-up
  `keyboard-inscreen/strip.html`). The query reads on the page's first line: a magnifier in Text Secondary at x 31,
  the text in 20 px white from x 48 with a steady 2 px cursor while the strip has the wheel, or "Search your music"
  in Text Tertiary while it is empty (Add Songs' Search: "Search songs"), and a 1 px raised-gray rule under it on
  y 93. The strip is a `#1C1C1E` pill low in the glass, x 40 to 335 and y 254 to 288 with 17 px corners, and one
  loop the wheel walks: the set switcher docked at its left (x 40 to 96, a `#2C2C2E` pill naming the set, abc, ABC,
  123 or #+=, in Inter Medium 16, the accent while the wheel is on it); the set's characters and a space, each in
  17 px Inter Text Primary in an equal 19 px cell from x 106, the lit one white on an 18 px accent key; and the
  return key docked at its right, a 34 px circle in the accent with its mark in white, turned to a white circle
  with the mark in the accent while the wheel is on it. The mark is what submitting does: a magnifier on a search
  (Search's own, Add Songs', a playlist's, Internet Radio's, Spotify's, TIDAL's, a card app's), a check where a
  name or a value is saved (New Playlist, Rename Playlist, the Bluetooth and Guest Play names, a paired device's
  or a trusted guest's Rename, an EQ preset's name, a band's frequency and Q), and an arrow right anywhere else (`search_strip.c` `ss_ret_kind`,
  [oled-design](oled-design.md#text-entry)). #+= holds all 32 printable ASCII symbols, so the four sets type every
  printable character. Ten cells show; the run starts at its first character and moves only when the highlight
  comes within a cell of an edge, its letters themselves fading out over 22 px where more of the set lies past that
  edge (nothing is painted over them). One turn of the wheel (ticks under 250 ms apart) stops on the switcher or the return
  key, whichever end it reaches; a new turn goes on round to the other. Search opens on A. When the wheel lands on a character its key pops up,
  as an iPhone's keyboard does: the key grows a bubble above the strip (a 30 px head with 9 px corners narrowing
  over 18 px to the 18 px key, the accent) holding it in 24 px white Inter Medium, and the key keeps it at the
  strip's size in white at 60 %; it goes 300 ms after the wheel stops. Turning faster than 8 characters a second
  shows none until the wheel stops, then one. The bubble may cover the last result rows while it shows. Center
  types the lit character at the cursor, cycles the set on the switcher (the highlight stays on it), and submits on
  the return key; Play/Pause submits from anywhere on the strip, and a submit shuts the strip, the results then
  running to the screen's foot. Return on the strip shuts it, never a submit, as an edge swipe does
  (`search_strip_close`): the query stays, highlighted, and the next Return leaves Search; Center or a tap on the
  query opens the strip again. A held Return closes Search from anywhere on the page: 200 ms into the hold a
  card says "Keep holding to go back" over the dimmed page, its ring filling until 600 ms, when Search closes; let
  go before and the strip shuts (`search_strip_down`, [oled-design](oled-design.md#text-entry)).
  Previous and Next move the cursor along the text; Previous held erases the character before it 450 ms into the
  hold, the next 500 ms later and each after that 15 % sooner, down to one every 110 ms, and its release moves
  nothing (a hold of Previous or Next there never scans the song). There is no delete key. A space is never typed
  first or beside another. A query longer than its line scrolls to keep the cursor in it.
  What holds the text lists under the query as it is typed: the songs whose title, artist or album holds it, then
  the artists whose name holds it, then the albums, each artist and album once; four single-line rows from y 98,
  the name in 20 px white and after it, in 15 px Text Tertiary, the artist on a song's row, "Artist" on an artist's,
  "Album" and its artist on an album's. Submitting moves to the results: the selection bar takes the first one, the
  strip's highlights go, and an iOS segmented control takes the first row under the query (All, Songs, Artists,
  Albums; a 30 px `#1C1C1E` track with 9 px corners, the open tab a raised `#3A3A3C` segment, the accent while the
  wheel is on it, labels in Inter Medium 17, the others `#ABABAB`), leaving three rows under it. All lists the three
  groups, 32 of each at most, so a common string's artists are never more than 32 rows down (under five turns of
  the wheel at the default sensitivity); Songs, Artists and Albums list their group in full, the artists and albums
  A to Z. The tab chosen stays while the text changes, and the strip's list shows it. The wheel moves the bar;
  Center plays a song with the songs found after it as the queue, opens an artist's page, or opens an album's
  tracks in Cover Flow; Play/Pause there is the player's. A turn up from the first result lands on the tabs, where
  the wheel moves between them, each picked as it is reached ([Tabs](oled-design.md#tabs)): a turn down past
  Albums, or Center, goes back to the results and a turn up past All to the query line, which takes the selection bar and nothing more (the strip stays shut and the wheel goes on down to the tabs); Center there, or a tap, opens the strip; Next picks the next tab and
  Previous the one before. Previous on the results goes back to the strip with the
  text, cursor and tab as they were; Return on the results or the tabs leaves Search. With text and no match, the empty state stands in the list's place, "No
  Matches", "Try fewer letters" ([oled-design](oled-design.md#empty-and-offline)), and a submit shuts the strip on the query;
  on a tab with nothing, it lands on the tabs. A server song that can't play now (offline, no copy on the card),
  and an artist or album none of whose songs can, is grayed with the offline mark at its right, as in every other
  list ([server library](server-library.md#grayed-rows)). By touch, a drag along the strip moves the highlight a
  cell per 19 px as the finger goes (to the left the later characters), popping each as the wheel does, as one
  turn, and types nothing; a tap on a character pops it and types it at the cursor, a tap on the switcher cycles
  the set and a tap on the return key submits; a tap on a tab opens it, a tap on a result opens or plays it, and a
  drag up or down the results scrolls them a row per 32 px. In the text, in every form a text takes (the query
  line, a row, a cell, a field), a tap puts the cursor at the nearest character boundary and gives the wheel back
  to the strip (while the wheel is elsewhere on Search, the tap counts when the finger lifts within 8 px of where it went down; a drag from the query is a scroll and changes nothing); held 250 ms, or moved, the finger brings up a loupe as an iPhone's does: a 104 by 46 px lens with
  14 px corners and a `#6E6E6E` line, its foot 10 px over the text's top and kept 4 px inside the screen (over the
  status row on Search), showing the text around the cursor at 1.6 times, drawn by the same text renderer, with a
  steady cursor in its middle; it grows in from half its size over 100 ms (at once under Reduce Motion) and the
  cursor follows the finger. Held within 10 px of an edge that cuts the text, the finger nudges it a character
  every 150 ms until that end is in view. Letting go leaves the cursor there. A double tap selects a word and a
  triple tap the whole text, on the accent at 40% with a handle at either end ([oled-design](oled-design.md#text-entry));
  a drag on from the double tap, or on either handle, moves an end, typing replaces the selection, Previous held
  deletes it, and Previous or Next puts the cursor at its start or end. A name typed elsewhere (a
  playlist's, a Bluetooth name) may hold any character: the cursor steps, erases and lands on whole ones. Each
  tick along the strip clicks as a list's row change does, and Center, Next, Previous and Return click as a button
  does, through the Clicker setting.
- <a id="name-strip"></a>**Naming a playlist.** New Playlist (stock's add-playlist dialog's place: Cover Flow's
  playlists' hold menu, the Add to Playlist picker's New Playlist… row; `playlist_new_open`) and a playlist's Rename
  (`playlist_rename_open`, prefilled with the name, the cursor at its end) are the same strip, opening on abc, and
  the name is typed in the row the wheel was on, in its place over the page under ([oled-design](oled-design.md#text-entry)'s
  row form), with a blinking cursor and no hint. The strip is a page of its own, so the window manager no longer
  paints the page under it: `ss_place` paints that page once into a bitmap as it opens (`under_snap`, the swipe
  back's) and finds the row from navigation's selection (`st.sel_w`, `sel_y`, `sel_h`, matched only while the row's
  list is still in that page), and with no such row in sight, or one the strip would cover, the text goes in a field
  of its own. The status row reads New Playlist or Rename Playlist. Capitals are ABC's. Previous and Next move the cursor along
  the name; submitting (Play/Pause, or Center on the return key) creates the playlist (`ringnav_playlist_create`,
  "Playlist created", and the list goes back to open on it) or saves the new name ("Renamed"), and the page closes;
  an empty name ("Name can't be empty") or one taken ("A playlist has that name") says so and stays. Return, held
  Return and held Previous are as on Search.
- <a id="keyboard"></a>**The keyboard.** Stock's keyboard is gone (the user's, 2026-11-18): no text box shows it,
  and its page, style and key images are not in the image. Every keyboard stock opens is opened by its input method,
  and both of that one's calls open `patch/keyboard.c`'s `kb_host_open` instead: a bare keyboard window, no keys and no
  slide up, over the screen under the status row, which the letter strip takes for the input method's edit before its
  first frame (`search_strip_box`, [oled-design](oled-design.md#text-entry)); a page of its own would take the edit's
  focus, which ends a search box. The box's text is prefilled, the strip opening on abc (123 for a box holding a number), and typed where the box's
  value lives: a search box's on the query line, a Wi-Fi password or network name in a field under its label, a box
  whose page names its row (`_strip_label`: an EQ preset's Name, a band's Frequency or Q, a card app's field) in that
  row. A password shows as dots, the character just typed for a second, with no way to show the rest: a Wi-Fi
  network's, Add Network's, a card app's password field and the Guest Play hotspot's. Submitting writes the
  text into the edit and presses the keyboard's OK (`input_method_dispatch_action`, as its Enter key does), so the box
  acts as it did: the Wi-Fi password joins, an EQ preset is renamed, a search searches; the keyboard, the strip with it,
  closes as its Done closes it. Return, or an edge swipe, shuts the strip and leaves the box and its text in place;
  Center or a tap in the text opens it again, and Play/Pause is still the box's OK. The next Return, or a held one,
  closes the keyboard and the strip (an edit of ours emptied first, as the
  keyboard's Return did), leaving the box; on Add Network's two rows, whose dialog (`editwifi_dialog`) holds nothing
  but the box, it quits the dialog too, a loop pass later (quit inside the keyboard's close, demo crashes), back to the
  rows with their text as it was. That dialog's own box, in a row at the status row's height, is hidden while the strip
  is up (`search_strip_box`) and shown again if the dialog outlives the strip.
- **Track Time.** Now Playing's right-hand time shows what is left of the track ("-3:29", the default) or its
  length ("4:12"). A tap on the time switches it and the choice is kept across tracks and restarts (`IPOD NPTIME`,
  an own option; `np_time_tap`, outside the bar's target, so it never seeks); Appearance's Track Time row is the
  wheel's way to it.
- **Appearance's rows.** Light Mode, Accent, Home, Battery (Icon or Percent), Song Artist, Song Length
  and Track Time.

<a id="oled-type"></a>Inter 4.1 Regular and Medium (`assets/fonts`, SIL OFL 1.1) ship as `fonts/inter.ttf` and
`fonts/inter-medium.ttf`, subset as the other faces are; where Inter lacks a character, stock's face stands in.

### Light Mode

**Light Mode: Auto** (`LIGHT=2`) is light from 07:00 to 18:59 by the device clock and dark the rest of the day. The row cycles Off, On, Auto on each click. It switches at 07:00 and 19:00 while the device is on, and the cached images are dropped and an open screen repaints then (`clock_show`, at each minute with the status bar's clock). The boot logo picks its mode by the same hours, in the saved time zone ([boot](boot.md#light-mode)).

**Light Mode: On** (`LIGHT=1`) turns the theme light at runtime, over the same assets. Every
neutral color (channels within `LIGHT_SPREAD`) flips per channel, black to `LIGHT_PAPER` (`#F4F4F4`)
and white to `LIGHT_INK` (`#161616`), or the theme's own paper and ink (OLED's `#FFFFFF` and `#000000`, `scheme.paper`, `scheme.ink`), alpha kept; other hues are left as they are. Stock's colors
flip in the style hooks (`accent_map`, after the accent), the payload's own in `ui_ink`, which
`patch/peq.h` puts in front of every canvas and vgcanvas color setter. A color read back from the
LCD to restore it goes through `stock_canvas_set_*`, since it has flipped already. Only the outermost
style hook call maps: a mutable style (inline `style:*`) answers from its default style through
`style_get_color` again, and the flip is not idempotent. A clear color (`#00000000`) stays clear,
because stock tests for it (Now Playing's dragger).

The accent keeps its tones. On the accent's bar the rows' ink stays white, as on an iPod's blue bar
(`sel_accent`), and so does the chevron (the theme's white caret, `chevron_icon`). A red tint over black
(stock's pressed tints) becomes the same tint of the tone over paper.

A theme image without color (no pixel more than `LIGHT_CHROMA` saturated) flips too when it is
mostly glyph: under 1/`LIGHT_SURFACE` of its area opaque and darker than `LIGHT_GLYPH`. White marks,
chevrons, spinners and status icons turn dark, while a gray or dark disc keeps its white glyph.
Quick Settings' inactive discs (`drop_*`) flip whole, to light discs under dark glyphs, and the
active ones keep the accent. An image the accent mapped is final, and `switch_off` keeps its white
knob, as `switch_on` does. Cover Flow's frame composites over paper instead of black, its side covers and reflections fading to paper. Solitaire's face-up cards stay light with black suits (`unflipped`). The status bar's stock
black border is cleared so it does not flip to a paper line; the bar is paper. Album art,
photos, wallpapers and videos are never changed.

ponytail: a non-square or transparent cover in Cover Flow keeps black bars and a black background
inside its square (`cf_decode`).

## LocalSend

**System settings → Network → LocalSend** takes the place of stock's Wi-Fi File Transfer: files go to and
from a phone or computer running the free LocalSend app on the same Wi-Fi, with nothing to type. The page
lists **Send Logs**, the devices found nearby and, once there are any, **Trusted Devices**; a phone sending
asks on the Accept card, and what arrives goes to `Received` on the card and into the library. The design,
the protocol and the daemon: [localsend.md](localsend.md).

## Games

**Home → Games** (the Library's row until 2026-11-19) opens Bricks and Cabin Solitaire, then the card's games ([internals](internals.md#games)).
Sudoku, Vortex, Solitaire and Snake are left out of this build while the games in progress are
finished (`GAME_*` in `patch/peq.h` turns each back on); their keys, below, are as they were. A game
takes every key but Power and a held Return, so the music keeps playing untouched.

| Game      | Wheel                                    | Center                                          | Play/Pause            | Previous/Next                                                           | Return                              |
| --------- | ---------------------------------------- | ----------------------------------------------- | --------------------- | ----------------------------------------------------------------------- | ----------------------------------- |
| Sudoku    | Moves through the cells; picks the digit | Opens the digit, then writes it                 | New game (asks first) | Up or down a row                                                        | Cancels the digit; back to Games    |
| Vortex    | Turns the paddle                         | Launches; plays again after a game              | Pauses and resumes    |                                                                         | Pause menu: Continue or Quit        |
| Solitaire | Moves between the piles                  | Draws; picks up, drops; twice sends a card home | New game (asks first) | Up or down a row                                                        | Drops the held cards; back to Games |
| Snake     | Disabled                                 | Plays again after a crash                       | Pauses and resumes    | Turn left/right; wheel or Previous/Next picks Continue/Quit in the menu | Pause menu: Continue or Quit        |

## Review sheets

Review sheets are native-size **composed previews**, not firmware captures.
Host and MIPS checks cover geometry, clipping, selection contrast, recycled rows,
preferences, input and packaging.
