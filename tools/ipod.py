"""Build-time edits for the SHA-pinned UI assets (local browsing, settings, status bar) and native call sites.

No global widget hook: excluded pages stay byte-identical; iPod's list style edits are audited in place.
The audit records full original instructions and asset hashes, not search/replace patterns.
"""
import copy
import dataclasses
import functools
import json
import math
import pathlib
import re
import struct
import types
from build import FACES, ROOT, check, fileoff, need, sha, jal

AUDIT = json.loads((pathlib.Path(__file__).resolve().parents[1]/'patch/ipod.json').read_text())
UI_ASSETS = AUDIT['assets'] | AUDIT['navbar_only'] | AUDIT['slide_only']
STOCK_ROWS = AUDIT['stock_rows']  # pages left as stock made them until a set re-pitches them (Geometry.stock_row)
# The named immediates' values (ipod.json): text rows' plain, art rows' art_.
VALUE_NAMES = list(dict.fromkeys(g['value'] for g in AUDIT['immediates'] if isinstance(g['value'], str)))
INC = (ROOT/'patch/offsets.inc').read_text()


# The integer #defines of patch/offsets.inc, which the payload compiles with.
O = {m[1]: int(m[2], 0) for m in re.finditer(r'^#define\s+(\w+)\s+(0x[0-9A-Fa-f]+|\d+)\b', INC, re.M)}
inc = O.__getitem__

@dataclasses.dataclass(frozen=True)
class Geometry:
    """The app window is the 375x320 screen, its rows resting below the 30px status bar (an overlay), so a 290px list holds four
    72px rows. The stock 52px artwork is drawn 1:1 (no rescaling) with an 8px inset inside the
    68px row body. Rows keep the row layout's eight-pixel left margin for the artwork.
    text_art, text_art_inset: the text pages' artwork and its inset (0 hides it, a text-only row).
    set_row, set_top, set_rows, set_icon: settings lists hold set_rows rows of set_row from set_top, the
    icon at set_icon (0 hides it); payload rows are built 70 px high (SET_STOCK_BODY), stock's shape,
    and mapped like stock's.
    stream_row: Streaming's settings rows, which follow Row Art; taller than set_row, they have SET_ICON's icon.
    STOCK_GRID is stock's own grid, its pages' measurements: the theme's <geometry> (its theme.xml, tools/theme.py)
    states the build's grid over it."""
    name: str = 'ipod'
    bar: int = 30
    pitch: int = 72
    body: int = 68
    art: int = 52
    art_inset: int = 8
    art_pitch: int = 72
    art_body: int = 68
    text_art: int = 52
    text_art_inset: int = 8
    lib_pitch: int = 72  # the Library's rows, a kind of their own: its pitch, body, icon inset and icon (0 hides it)
    lib_body: int = 68
    lib_inset: int = 8
    lib_art: int = 52
    title24_y: int = 13  # text y in the body: the 24px title, the title, metadata, playlist count
    title_y: int = 10
    meta_y: int = 39
    count_y: int = 40
    art_title24_y: int = 13
    art_meta_y: int = 39
    folder_pitch: int = 0  # Folder view's rows, a kind of their own: its pitch and body (0: art_pitch's and art_body's)
    folder_body: int = 0
    song_pitch: int = 72  # Songs' rows, a kind of their own: its pitch, body, title and metadata y
    song_body: int = 68
    song_title24_y: int = 13
    song_meta_y: int = 39
    playlist_pitch: int = 0  # Playlists' rows (playlists.c pl_row, page_detail high) and their list's pitch (0: song_pitch's)
    set_row: int = inc('SET_ROW')
    set_top: int = inc('SET_TOP')
    set_rows: int = inc('SET_ROWS')
    set_icon: int = inc('SET_ICON')
    stream_row: int = inc('SET_ROW')
    bar_px: int = 0  # the status row's title and clock px (status_row)
    batt_w: int = inc('BATT_BODY_W')  # the slim battery's body (list_marks.c battery_slim), its nub outside
    batt_h: int = inc('BATT_BODY_H')
    text_x: int = 0  # list text's left edge, clear of the glass at the first row (0: stock's)
    action_row: int = 0  # Songs' action bar as a plain row this high (0: stock's 50 px bar)
    list_end: int = 0  # the window row where a selected row's scroll stops, lists scrolling by whole rows: below it the glass hides its text, and the rows past it fade into the window's foot (fit_rows)
    home_row: int = 39  # Home's and its Settings list's row height; Python only, no C reads it
    home_lib: int = 0  # Home's Library row, in Split, slides in a list of the Library's rows (coverflow.c home_library)
    # Stock's own list pages (STOCK_ROWS, Lock Screen): their rows, its title's y and box, stock's at 78. The one rule to reverse them.
    stock_row: int = 78
    stock_body: int = 70
    stock_title_y: int = 11
    stock_title_h: int = 20
    # Search's rows, stock's at 78. search_art 0 makes them two lines of text, as Songs'.
    search_row: int = 78
    search_body: int = 70
    search_art: int = 50
    search_title_y: int = 11
    search_title24_y: int = 14
    search_title_h: int = 24
    search_meta_y: int = 41
    search_meta_h: int = 16
    # The status row in place of a bar band (status_row): its glyphs centred on screen row status_y, the page's title from
    # STATUS_ROW_INSET in the bar's face and px (bar_px), the clock centred (at the row's left on Home's Split; navigation.c clock_place), the play state and
    # battery ending STATUS_ROW_INSET from the screen's right edge; the bar is drawn black, so the row reads as part of the page.
    status_y: int = 0
    right_x: int = 0  # the right column, from the screen's right edge: a row's chevron and value (0: stock's)
    # Now Playing on the grid (oled-design.md Now Playing): the art np_art square at text_x on the window's top, the words
    # NP_GRID_GAP right of it to the right column, a 6 px bar from text_x to the right column with its top np_bar_y on
    # screen, the times under its ends, shuffle and repeat under them (playing_page np_grid).
    np_art: int = 0
    np_bar_y: int = 0
    # Home in Split (coverflow.c home_split): the menu's width, which its rows, chevrons and selection bar take as theirs;
    # the art's pane from split_art to the screen's right edge; split_art_up rows of the pane above the window, in the
    # bar, which paints them (coverflow_home_bar) and inks its glyphs over them by contrast (home_ink); the cover pans
    # across its 110 % zoom, and split_inset keeps that percent of the zoomed cover clear of the pane at each edge, so a
    # cover's own frame never shows (coverflow.c drift_axis).
    split_row: int = inc('HOME_SPLIT_W')
    split_art: int = inc('HOME_SPLIT_W')
    split_art_up: int = 0
    split_inset: int = 0


STOCK_GRID = Geometry()
MARGIN = 8
# The 375x320 panel's glass has rounded corners that hide whatever is drawn in them. CORNER_R is
# the device calibration knob: the corner radius in pixels, fitted to device photos (V5.4I cut
# "20 of 29" at x 8, y 42 and the "Sy" of the last Home row, which needs about 80) and to stock's
# 50px status bar margins, which clear it. Text and icons near a corner keep CORNER_SLACK more.
CORNER_R = inc('GLASS_R')
CORNER_SLACK = inc('GLASS_SLACK')


def corner_inset(y):
    """The width the glass hides at each end of screen row y."""
    d = min(max(CORNER_R - y, y - (320 - CORNER_R), 0), CORNER_R)
    return math.ceil(CORNER_R - math.sqrt(CORNER_R**2 - d*d))


def corner_x(y, h):
    """The side inset, slack included, for content spanning screen rows y to y + h."""
    return max(corner_inset(y), corner_inset(y + h)) + CORNER_SLACK


# iPod: the full-screen art (bigcover_page) is the largest centred square, even so its top and bottom
# margins match, whose square corners clear the glass by CORNER_SLACK: the whole cover shows. Stock fits it
# to the 375x320 window, where a square cover is 320 high and the glass hides its corners.
BC_ART = max(s for s in range(2, 321, 2) if (375 - s) // 2 >= corner_x((320 - s) // 2, s))


# Home's full-width rows share one left edge clear of the rounded glass. The payload lays them out
# at runtime (coverflow_home_layout): Split narrows the list to the set's split_row, the art from split_art.


# iPod page transition: the window anim_hint of the browsing, settings and equalizer pages. Stock's
# htranslate animator slides a page in from the right and back out on Return; the duration is the
# calibration knob.
SLIDE = f'htranslate(duration={inc("PAGE_SLIDE_MS")})'
HOME_TOP = inc('HOME_TOP')
HOME_ROW = STOCK_GRID.home_row
HOME_REST = inc('HOME_REST')
HOME_TEXT_X = inc('HOME_DOT_X') + inc('HOME_DOT') + inc('HOME_DOT_GAP')  # labels after the selection dot
HOME_LABEL_END = inc('HOME_LABEL_END')  # clear of the chevron's box, and so of the lower-right glass
HOME_LIST_W = 375
# iPod Now Playing (Rockbox iVideo), the grid the Spotify and Radio pages mirror (the SPOT_* checks below;
# playing_page lays out OLED's own, np_grid): a 40px top row, the art band below it, then the progress bar
# with the times under its ends. Stock draws the 3x10 A-B markers at y 250; the 4px line sits at
# 253; their x follows NP_BAR through the np_bar_* immediates in ipod.json. The window starts
# at screen y Geometry.bar; the top and bottom rows take their insets from the corners. The bar
# is a capsule (round_radius half its height) and the payload rounds the art's corners (paint_cover).
# The art and the metadata keep NP_MARGIN from the sides, 12px apart; the art is as large as that
# leaves while the text column keeps NP_TEXT_W.
NP_TOP = 40
NP_ICON = 50                     # the stock 50px control icons, centred in the top row
NP_ICON_PAD = inc('NP_ICON_PAD')  # play_fav/play_order: 28px, centred, glyph ends 4px above
NP_MARGIN = 16
NP_TEXT_W = 165
NP_ART = 375 - 2 * NP_MARGIN - 12 - NP_TEXT_W
NP_SLIDE_H = 186                 # the swipeable art and lyrics pages; the dots sit below
NP_DOTS_Y = NP_SLIDE_H - 4       # above the seek bar's NP_TOUCH target
# The art's gap below to the dots matches its gap above to the foot of a 16px line centred in the top row.
NP_ART_Y = (NP_DOTS_Y - NP_ART - (NP_TOP - 16) // 2) // 2
NP_TOUCH = 44
NP_TIMES_Y = 265  # 14px text in a 16px label, clear of the 10px A-B markers
NP_TEXT_X = NP_MARGIN + NP_ART + 12


def with_geometry(g):
    """The row sizes and the Now Playing insets that clear the glass, for a window starting at g.bar."""
    bar_x = max(MARGIN, corner_x(g.bar + 251, 8))
    return types.SimpleNamespace(
        BOTTOM=320 - g.bar, PITCH=g.pitch, BODY=g.body, ART=g.art, ART_INSET=g.art_inset,
        NP_ICONS_END=375 - corner_x(g.bar, NP_TOP),  # the 50px icon images fill the row's height
        NP_BAR_X=bar_x, NP_BAR=[bar_x, 253, 375 - 2 * bar_x, 4],
        NP_TIME_X=max(MARGIN, corner_x(g.bar + NP_TIMES_Y + 1, 14)))


GEOM_VALS = ('bar', 'set_row', 'batt_w', 'batt_h', 'text_x', 'list_end', 'action_row', 'status_y', 'right_x', 'split_row', 'split_art', 'split_art_up', 'split_inset')  # Geometry fields the payload reads at run time (themes.inc geom_vals), not immediates


def folder_pitch(g):
    """Folder view's row pitch under g: its own, else its art rows'."""
    return g.folder_pitch or g.art_pitch


def folder_body(g):
    return g.folder_body or g.art_body


def immediate_values(g=STOCK_GRID):
    """{value name: word} for the named ipod.json immediates under geometry g."""
    geo = with_geometry(g)
    values = {k: v for k, v in dataclasses.asdict(g).items() if k in VALUE_NAMES}
    return values | {'folder_pitch': folder_pitch(g), 'folder_body': folder_body(g),
                     'scroll': geo.BOTTOM - (g.action_row or 50), 'np_bar_x': geo.NP_BAR[0], 'np_bar_w': geo.NP_BAR[2]}


IPOD = with_geometry(STOCK_GRID)
BOTTOM, PITCH, BODY, ART, ART_INSET = IPOD.BOTTOM, IPOD.PITCH, IPOD.BODY, IPOD.ART, IPOD.ART_INSET
NP_ICONS_END, NP_BAR_X, NP_BAR, NP_TIME_X = IPOD.NP_ICONS_END, IPOD.NP_BAR_X, IPOD.NP_BAR, IPOD.NP_TIME_X
check(set(vars(IPOD)) == {'BOTTOM', 'PITCH', 'BODY', 'ART', 'ART_INSET', 'NP_ICONS_END', 'NP_BAR_X', 'NP_BAR', 'NP_TIME_X'},
      'a with_geometry field needs its module name above')
check([NP_MARGIN, NP_ART, NP_TEXT_X, NP_TEXT_W, *NP_BAR[::2], NP_TIMES_Y, NP_TIME_X] ==  # Spotify's and Radio's
      [inc('SPOT_' + n) for n in 'ART_X ART_PX TEXT_X TEXT_W BAR_X BAR_W TIMES_Y TIME_X'.split()], 'SPOT_* != NP_*')
check(inc('NP_ART_TOP') == NP_TOP + NP_ART_Y, 'NP_ART_TOP != NP_TOP + NP_ART_Y')
# The track is TRACK_COLOR until np_theme writes a theme's own; the fill is Graphite's light tone until ringnav_playing sets the
# accent's (3.3:1 or more on the track for every preset).
NP_TRACK = f'#{inc("TRACK_COLOR"):06x}'
NP_FILL = '#' + need(re.search(r'#define ACCENTS \{ 0x\w+, 0x\w+, 0x(\w+),', INC), 'Missing ACCENTS')[1]


def decode(data):
    check(data[:4] == bytes.fromhex('12122211'), 'Unexpected AWTK UI magic')
    i = 4

    def string():
        nonlocal i
        end = data.index(0, i)
        value = data[i:end].decode('utf-8')
        i = end + 1
        return value

    def node():
        nonlocal i
        check(i + 48 <= len(data), 'Truncated UI widget')
        kind = data[i:i+32].split(b'\0')[0].decode('ascii')
        i += 32
        geometry = list(struct.unpack_from('<4i', data, i))
        i += 16
        props = {}
        while data[i]:
            key, value = string(), string()
            check(key not in props, 'Duplicate UI property')
            props[key] = value
        i += 1
        children = []
        while data[i]:
            children.append(node())
        i += 1
        return [kind, geometry, props, children]

    root = node()
    check(i == len(data), 'Trailing UI data')
    return root


def walk(n):
    yield n
    for child in n[3]:
        yield from walk(child)


def encode(root):
    def node(n):
        kind, geometry, props, children = n
        return (kind.encode().ljust(32, b'\0') + struct.pack('<4i', *geometry) +
                b''.join(k.encode()+b'\0'+v.encode()+b'\0' for k, v in props.items()) +
                b'\0' + b''.join(node(c) for c in children) + b'\0')
    return bytes.fromhex('12122211') + node(root)


# Stock artist detail tabs carry literal Chinese `text` in every language; the
# stock string table already has these keys. The Albums tab and page start active (see ARTIST_ALBUMS).
ARTIST_PAGE = 'localmusic/artistinfo_page.bin'
ARTIST_TABS = {'btn_track': ('单曲', 'local_allsongs'), 'btn_album': ('专辑', 'album')}
# Stock init builds the Songs view (0x4af8ac); call the stock Albums tab click handler (0x4aeb1c)
# instead, which sets the tab state, queries the artist's albums and builds them. Songs stays a tap away.
# The handler's address makes both patched words (the low half $t9 takes, the bal's distance), so a rebase carries them.
ARTIST_ALBUMS_TAB = 0x4aeb1c
ARTIST_ALBUMS = [(0x4b12bc, 0x2739f8ac, 0x27390000 | ARTIST_ALBUMS_TAB & 0xffff),
                 (0x4b12c0, 0x0411f97a, 0x04110000 | (ARTIST_ALBUMS_TAB - 0x4b12c0 - 4 >> 2) & 0xffff)]


def artist_tabs(root):
    found = []
    for n in walk(root):
        name = n[2].get('name')
        if name in ARTIST_TABS:
            text, key = ARTIST_TABS[name]
            check(n[0] == 'tab_button' and n[2].get('text') == text, f'{name}: unexpected tab')
            n[2] = {('tr_text' if k == 'text' else k): (key if k == 'text' else v) for k, v in n[2].items()}
            if name == 'btn_album':
                n[2]['value'] = 'true'
            found.append(name)
        # tab_button loads before its pages sibling and can't sync it, so show the Albums view too.
        if n[0] == 'pages':
            check('value' not in n[2], 'Unexpected artist pages')
            n[2]['value'] = '1'
            found.append('pages')
    check(sorted(found) == sorted([*ARTIST_TABS, 'pages']), 'Unexpected artist tabs')


HOME_PAGE = 'home_page.bin'
LIBRARY = {'name': 'label_library', 'text': 'Library'}


# Home becomes a list of the stock cards' names in the user's order (menu review, 2026-11-15): Now Playing,
# Library, Streaming, Folders, Cover Flow, then two rows of the payload's, Search and Settings.
# home_page_init (0x534b6c) looks up no widget: its widget_foreach visitor (0x534750) binds img_*
# clicks and translates label_* by name, and only img_left/img_right reach the slide_menu. Each row's transparent image covers the row, on top of its label, so it takes the
# tap and is the row's click target for the wheel. The payload binds Cover Flow, Search and Settings, which slides
# list_view_homeset, hidden until then, into the list's place: Wi-Fi, Bluetooth, General (stock's System setting
# row), Playback (stock's Playback setting row), Car Remote, Display, Controls and Battery, the six that are not
# stock's bound by the payload (navigation.c home_sets_bind). Search's name is none stock's visitor knows (stock's search
# pages have an img_search).
# Home's main list (the menu review's order, Games and Streaming moved on 2026-11-19: docs/ipod.md#home): Games
# from the Library, Streaming to Settings, after Playback. Radio (2026-11-20, radio_tuner.c) under the Library.
HOME_ROWS = ['playing', 'localmusic', 'radio', 'games', 'folder', 'coverflow', 'home_search', 'settings']
HOME_SETS = ['set_wifi', 'set_bt', 'sysset', 'playset', 'set_stream', 'set_car', 'set_display', 'set_controls',
             'set_power']
HOME_LITERAL = {'radio': 'Radio', 'games': 'Games', 'set_stream': 'Streaming', 'coverflow': 'Cover Flow', 'home_search': 'Search',
                'settings': 'Settings', 'set_wifi': 'Wi-Fi',
                'set_bt': 'Bluetooth', 'set_car': 'Car Remote', 'set_display': 'Display', 'set_controls': 'Controls',
                'set_power': 'Battery'}
# Stock's visitor translates label_localmusic, label_folder, label_playset and label_sysset by name; renamed, they
# keep these literals.
HOME_RENAMED = {'localmusic': LIBRARY, 'folder': {'name': 'label_folders', 'text': 'Folders'},
                'playset': {'name': 'label_playback', 'text': 'Playback'},
                'sysset': {'name': 'label_system', 'text': 'General'}}
# Since stock V1.34 the visitor knows img_coverflow and label_coverflow too: its own Album Wall card's, which it
# binds to that page and names so. Cover Flow's image and label are named apart, so stock binds and names neither.
HOME_OURS = {'coverflow': 'cover_flow'}


# The theme default list_view is a light rounded card; Home's lists clear it to transparent.
LIST_CLEAR = {f'style:{state}:{prop}': '#00000000' for state in ('normal', 'disable', 'focused')
              for prop in ('bg_color', 'border_color')}


HOME_LISTED = {'localmusic': 'img_library'}  # under home_lib: renamed, so stock's name match binds it nowhere


def home_list(name, rows, g, extra=None, view_name=None):
    """A Home list_view of g.home_row rows: each a label under a full-row transparent tap image. With no rows, a
    window-high list a page's constructor fills (coverflow.c home_library, home_streaming), its view named as the page's."""
    views = []
    for i, row in enumerate(rows):
        # Long translations ellipsize and scroll inside the shared text margins.
        label = {'name': 'label_' + HOME_OURS.get(row, row), 'style': 's_scrlabel_white20l', 'only_focus': 'true', 'ellipses': 'true'}
        if row in HOME_LITERAL:
            label['text'] = HOME_LITERAL[row]
        label.update(HOME_RENAMED.get(row, {}))
        views.append(['view', [0, i * g.home_row, HOME_LIST_W, g.home_row], {'name': 'btn_' + row}, [
            ['hscroll_label', [HOME_TEXT_X, 0, HOME_LIST_W - HOME_TEXT_X - HOME_LABEL_END, g.home_row], label, []],
            ['image', [0, 0, HOME_LIST_W, g.home_row], {'name': g.home_lib and HOME_LISTED.get(row) or 'img_' + HOME_OURS.get(row, row), 'clickable': 'true'}, []]]])
    # The list_view's layout (0x5fd234) makes its scroll view vertical only for a mobile scroll bar,
    # which Home has none of, and scroll_view_create leaves it off; the payload navigates only
    # vertical scroll views.
    # Home's main list shows HOME_REST rows and scrolls the rest (coverflow.c home_overflow). The lists its rows open
    # (Settings', and Library's and Streaming's, which a page's constructor fills) run on to the window's foot, their
    # rows past list_end fading into it (navigation.c list_fade), as every list does.
    h = g.home_row * min(len(views), HOME_REST) or 320 - g.bar
    if name != 'home' and views:
        h = fit_rows(g, HOME_TOP, g.home_row)
    view = ['scroll_view', [0, 0, HOME_LIST_W, h],
            {'name': 'scroll_view_' + (view_name or name), 'self_layout': 'default(x=0,y=0,w=100%,h=100%)', 'yslidable': 'true'}, views]
    return ['list_view', [0, HOME_TOP, HOME_LIST_W, h],
            {'name': 'list_view_' + name, 'item_height': str(g.home_row), **LIST_CLEAR, **(extra or {})}, [view]]


def ipod_home(root, g):
    geo = with_geometry(g)
    check([n[0] for n in root[3]] == ['slide_menu', 'image', 'image'], 'Unexpected home carousel')
    check([n[2]['name'] for n in root[3][0][3]] == ['btn_' + r for r in
            ('playing', 'localmusic', 'coverflow', 'folder', 'stream', 'playset', 'sysset')], 'Unexpected home cards')
    # Stock's carousel of cards and its two arrows stay in the window, hidden and taking no taps: home_page_init's
    # visitor (0x534750) binds the arrows to the slide_menu and the cards' img_* by name, so the three stay for
    # it. Home is its lists alone (Split or Full); nothing shows or steps the carousel. Stock
    # V1.36's Album Wall card goes: its page does not ship (STOCK_PAGES), and no shipped page names img_coverflow.
    del root[3][0][3][2]
    for n in root[3]:
        n[2].update(visible='false', sensitive='false')
    # An image for the playing track's art, moved to the right panel and fitted by coverflow_home_art.
    root[3] = [['image', [0, 0, 375, geo.BOTTOM], {'name': 'img_homeart', 'draw_type': 'fill'}, []],
               home_list('home', HOME_ROWS, g), home_list('homeset', HOME_SETS, g, {'visible': 'false'}),
               *([home_list('homelib', [], g, {'visible': 'false'}, 'localmusic'),
                  home_list('homestream', [], g, {'visible': 'false'}, 'streamsset')] if g.home_lib else []), *root[3]]


def style_props(data):
    """Yield (widget, style, state, prop, value offset, value) for each property of an AWTK style file."""
    magic, _, count = struct.unpack_from('<3I', data)
    check(magic == 0xfafbfcfd, 'Unexpected AWTK style magic')
    for i in range(count):
        at, *names = struct.unpack_from('<I32s32s32s', data, 12 + 100*i)
        state, style, widget = (n.split(b'\0')[0].decode() for n in names)
        props, at = struct.unpack_from('<I', data, at)[0], at + 4
        for _ in range(props):
            _, key_len, size = struct.unpack_from('<BBH', data, at)
            prop, at = data[at+4:at+3+key_len].decode(), at + 4 + key_len
            yield widget, style, state, prop, at, data[at:at+size]
            at += size


def patch_style(data, audit):
    """Replace each edit's old value, same size, in the n states of widget/style that hold it: after the edits
    before it, and in the one state an edit names, if it names one."""
    check(sha(data) == audit['sha256'], 'Unaudited style file')
    out, props = bytearray(data), list(style_props(data))
    for widget, style, prop, old, new, n, *state in audit['edits']:
        old, new = bytes.fromhex(old), bytes.fromhex(new)
        check(len(old) == len(new), f'{style}.{prop}: edit changes size')
        sites = [at for w, s, st, p, at, value in props
                 if (w, s, p) == (widget, style, prop) and out[at:at+len(value)] == old and state in ([], [st])]
        check(len(sites) == n, f'{style}.{prop}: expected {n} states with {old.hex()}, found {len(sites)}')
        for at in sites:
            out[at:at+len(new)] = new
    return bytes(out)


# systembar_showface (0x5417d0) finds every widget by name, recursively from the bar,
# and each second re-shows the volume, EQ, BT, synclink and Wi-Fi widgets and resets their text and
# images, but never their geometry. So widgets iPod hides move off-screen instead of going invisible.
# The volume number stays hidden: the volume dialog shows it on each wheel change (volume.c vol_wm_keyup's pages).
STATUS_BAR = 'system_bar.bin'
STATUS_LEFT = ['img_state', 'label_eq']
STATUS_RIGHT = ['img_bt', 'img_wifi', 'label_battery', 'view_battery', 'img_battery']
STATUS_HIDDEN = ['img_vol', 'label_vol', 'img_synclink']
# The Battery setting (navigation.c bar_sync) shows one of the last three: the stock icon, stock's
# "88%" (label_battery at BATT_PCT_PX, right-aligned; "100%" is BATT_PCT_W) or the payload's charge-level battery
# with the percentage beside it (view_battery). The layout skips hidden children.
BATT_PCT_W, BATT_PCT_PX = inc('BATT_PCT_W'), inc('BATT_PCT_PX')
BATT_H_W = inc('BATT_BODY_W') + inc('BATT_NUB_W') + inc('BATT_GAP') + BATT_PCT_W  # the payload's battery with its nub


def status_bar(root, g):
    left, right = root[3]
    # Clear in every state (the bar is disabled, so AWTK paints its disable style): the bar is an overlay on a full-bleed
    # page (docs/oled-design.md#full-bleed); the payload fills it black only over a page that starts under it
    # (theme_titles_paint.c ringnav_paint_bg). Its border clear too: AWTK draws the border over the bar, and Light Mode
    # flips black to paper.
    root[2].update({f'style:{s}:{k}': '#00000000' for s in ('normal', 'disable', 'focused') for k in ('bg_color', 'border_color')})
    check([left[2].get('name'), right[2].get('name')] == ['view_left', 'view_right'], 'Unexpected status bar')
    check(root[1][3] == left[1][3] == right[1][3] == 30, 'Unexpected status bar height')
    root[1][3] = left[1][3] = right[1][3] = g.bar
    widgets = {n[2]['name']: n for n in left[3] + right[3]}
    check(sorted([*widgets, 'view_battery']) == sorted(STATUS_LEFT + STATUS_RIGHT + STATUS_HIDDEN), 'Unexpected status bar widgets')
    widgets['img_wifi'][1][2] = 14
    eq = widgets['label_eq']
    eq[1][2] = 28
    for s in ('normal', 'disable', 'focused'):
        eq[2][f'style:{s}:font_size'] = '16'
    pct = widgets['label_battery']
    pct[1][2] = BATT_PCT_W
    for s in ('normal', 'disable', 'focused'):
        check(pct[2][f'style:{s}:font_size'] == '18', 'Unexpected battery label')
        pct[2].update({f'style:{s}:font_size': str(BATT_PCT_PX), f'style:{s}:text_align_h': 'right'})
    pct[2]['visible'] = 'false'
    widgets['view_battery'] = ['view', [0, 0, BATT_H_W, 0], {'name': 'view_battery', 'visible': 'false'}, []]
    status_row(root, g, widgets, left, right, eq)


STATUS_ROW_RIGHT = STATUS_RIGHT[2:]  # a status row's right group: the battery, its slots left of it the payload's (status_row.c)
STATUS_ROW_ICON = 16  # stock's status icon canvases (bar_play, bar_pause), their glyph centred
STATUS_ROW_TITLE_W = 240  # the title's and clock's box from the row's inset, until the payload's first paint moves both (clock_place)


def status_row(root, g, widgets, left, right, eq):
    """The status row: no band, the bar black, its glyphs centred on row status_y, both ends STATUS_ROW_INSET in from
    the screen's sides, inside the glass's top curve. The title and the clock start in one box whose text starts on
    that inset, in the bar's face at bar_px in Text Secondary; the payload centres the clock (on Home's Split moves it
    to the row's left) and ends the title's box short of it (navigation.c clock_place). The right group is the
    battery, its nub's last column STATUS_ROW_INSET from the screen's edge; the payload paints the row's other glyphs
    left of it (status_row.c: the level bars, Wi-Fi, a sync, a headset's battery, Buttons Lock's padlock, a server out
    of reach). The play state, Bluetooth, Wi-Fi, EQ and the rest stay, off-screen,
    for stock to update: the payload reads the play state's and Wi-Fi's from them."""
    inset = inc('STATUS_ROW_INSET')
    top = g.status_y - STATUS_ROW_LINE // 2
    check(inset >= corner_x(top, STATUS_ROW_LINE), f'Status row inset {inset} is under the glass at y {top}')
    box_y = g.status_y - STATUS_ROW_ICON // 2
    left[3] = []
    right[3] = [widgets[n] for n in STATUS_ROW_RIGHT]
    layout = right[2]['children_layout']
    check('xm=50,s=5)' in layout, 'Unexpected status bar layout')
    right[2]['children_layout'] = layout.replace('xm=50,s=5', f'xm={inset},s={STATUS_ROW_GAP}')
    right[1][1], right[1][3] = box_y, STATUS_ROW_ICON
    widgets['img_state'][1][2] = STATUS_ROW_ICON
    for name in (*STATUS_HIDDEN, 'img_state', 'img_bt', 'img_wifi'):
        box = widgets[name][1]
        box[0], box[3] = -200, g.bar  # still updated by stock, drawn off-screen
        root[3].append(widgets[name])
    eq[1][:2] = [-200, -200]
    eq[2]['floating'] = 'true'
    root[3].append(eq)
    root[3].append(['label', [-200, 0, 28, g.bar], {
        'name': 'label_idle', 'text': 'Q2', 'visible': 'false', 'style:normal:font_size': '18'}, []])
    # The title's and the clock's box: hscroll_label keeps 2 px each side, so its text starts on the inset
    text = {f'style:{s}:{k}': v for s in ('normal', 'disable', 'focused')
            for k, v in (('font_size', str(g.bar_px)), ('text_color', STATUS_ROW_INK))}
    for name in ('title', 'clock'):
        root[3].append(['hscroll_label', [inset - 2, top, STATUS_ROW_TITLE_W, STATUS_ROW_LINE],
                        {'name': 'label_' + name, 'style': 's_scrlabel_white20l', 'only_focus': 'true', 'ellipses': 'true', **text}, []])


STATUS_ROW_GAP = 3  # the right group's spacing (the battery's modes, one shown at a time)
STATUS_ROW_LINE = 24  # the title's and clock's line box, centred on status_y
STATUS_ROW_INK = '#ABABAB'  # Text Secondary (docs/oled-design.md): Light Mode's flip gives the paper's grey


# Stock finds every Now Playing widget by name, recursively, so they can move: title, artist
# and a new album label join the art on the slide_view's first page, so a swipe still swaps all of it for
# the lyrics page. The info page (⋯'s About, np_info_out) keeps its stock 225px column, centred; the lyrics page is the
# payload's (patch/lyrics.c). The payload fills the label_ipod_* labels (ringnav_playing); label_playlen,
# the total, hides.
PLAYING_PAGE = 'playing_page.bin'


def plain_slider(props, track, fill, bar):
    """A slider's props drawn in plain colour (stock slider paint uses bg/fg_color without images). The
    style has no theme entry, but stock still reads its default slider style's thumb icon for it, so the
    payload clears the icon in every state (np_bar.c slider_knobless): stock then fills exactly to the value,
    and slide_with_bar keeps tap and drag."""
    props = {k: v for k, v in props.items() if not k.endswith((':bg_image', ':fg_image', ':icon', ':y_offset'))}
    for key in props:
        if key.endswith(':bg_color'): props[key] = track
        if key.endswith(':fg_color'): props[key] = fill
    props.update(style='s_ipod_progress', bar_size=str(bar))
    return props


def np_slider(slider, x, cy, w, h):
    """Now Playing's progress bar: an h-high plain track centred on row cy in an NP_TOUCH-high slider,
    the accent's fill and the Theme's height set at runtime (np_bar.c np_fill, np_theme)."""
    slider[1] = [x, cy - NP_TOUCH // 2, w, NP_TOUCH]
    slider[2] = plain_slider(slider[2], NP_TRACK, NP_FILL, h)
    # No thumb: stock skips the dragger only when border_color is clear and there is no icon (slider_knobless).
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        slider[2][f'style:{state}:border_color'] = '#00000000'
    # Stock draws small radii square; the four-pixel line deliberately has square ends.
    for key in slider[2]:
        if key.endswith(':round_radius'): slider[2][key] = str(h // 2)  # a capsule, track and fill


def np_buttons(named):
    """Stock V1.36's Now Playing buttons without the two it added, for its spectrum and VU pages, which Q2 Muse doesn't
    offer: playing_page_init's visitor (0x53c5dc) binds them by name, img_spectrum and img_vu, so with neither in the
    page nothing opens those. Stock places Return and More by layout since; here their boxes are set."""
    buttons = named['view_buttons']
    check([n[2].get('name') for n in buttons[3]] == ['img_return', 'img_spectrum', 'img_fav', 'img_more', 'img_vu', 'img_playmode'],
          'Unexpected Now Playing buttons')
    buttons[3] = [n for n in buttons[3] if n[2]['name'] not in ('img_spectrum', 'img_vu')]
    for name in ('img_return', 'img_more'):
        named[name][2].pop('self_layout', None)


def playing_page(root, g):
    named = {n[2].get('name'): n for n in walk(root)}
    check([n[2].get('name') for n in root[3]] == [
        'view_buttons', 'scrlabel_title', 'scrlabel_artist', 'label_playtime', 'label_playlen',
        'slide_view_view', 'slider_play', 'img_repeata', 'img_repeatb', 'image_wait'], 'Unexpected Now Playing page')
    np_buttons(named)
    buttons, title, artist = root[3][:3]
    buttons[1] = [0, 0, 375, NP_TOP]
    named['img_return'][1][0] = -200  # the hardware Return, as on the pages whose navbars are hidden
    for name in ('img_fav', 'img_more', 'img_playmode'):
        n = named[name]
        n[2] = {k: v for k, v in n[2].items() if not k.endswith(('_offset', 'text_align_h'))}
        if 'image' in n[2]:
            n[2]['draw_type'] = 'center'
    for name in ('img_playmode', 'img_fav'):
        buttons[3].remove(named[name])
    title[2]['style'] = 's_scrlabel_white20l'
    for key in artist[2]:
        if key.endswith(':text_align_h'): artist[2][key] = 'left'
    album = copy.deepcopy(artist)
    album[2].update(name='label_ipod_album', text='')
    named['img_playstate'][2]['opacity'] = '0'  # retain stock gesture target without a transport glyph
    named['view_album'][3] += [title, artist, album, named['img_playmode'], named['img_fav']]
    column = (375 - 225) // 2
    named['label_lyricmsg'][1][0] += column
    # The payload paints each lyric line over its stock row, a blank label LYRIC_SLOT high for a line of one
    # row (patch/lyrics.c): the rows span the page, and the list layout (stock's list_view(m=0,s=0) otherwise,
    # the payload's own once it opens) starts them a slot down, so the first line can sit in the middle.
    lrc = named['view_lrc'][3][0][2]
    check(lrc['self_layout'].startswith('default(x=0,y=0,w=-10,') and 'children_layout' not in lrc, 'Unexpected lyrics scroll view')
    check(3 * inc('LYRIC_SLOT') <= 178, 'Three lyric lines must fit the page')
    lrc['self_layout'] = 'default(x=0,y=0,w=100%,h=100%)'
    lrc['children_layout'] = f'list_view(x=0,y={inc("LYRIC_SLOT")},s=0)'
    # its y set as laid out too, so a read before the first layout finds where the lyrics start
    named['view_lrc'][1][1] = 0
    for n in named['view_info'][3]:
        n[1][0] += column
    dots = named['slide_indicator1']
    for key in dots[2]:
        if key.endswith(':selected_fg_color'): dots[2][key] = '#eeeeee'
        elif key.endswith(':fg_color'): dots[2][key] = '#555555'
    # Without an anchor, stock's indicator centres its dots on the slide_view as widget_to_screen places it,
    # which takes away the view's own xoffset, a swipe's drag: dots laid out mid-swipe (any relayout lays them
    # out) slid with the page until the next layout. The same point as the indicator's own anchor: the
    # slide_view's centre, and above the indicator's middle, which puts the dots on its top edge.
    dots[2].update(anchor_x=f'{375 // 2}px', anchor_y='0px')
    total = named['label_playlen']
    remain = copy.deepcopy(total)
    remain[2].update(name='label_ipod_remain', text='')
    total[2]['visible'] = 'false'
    root[3][1:3] = []
    root[3].insert(3, remain)
    np_grid(root, named, g, title, artist, album, remain)
    np_info_out(root, named)
    np_rate_page(named)


def np_info_out(root, named):
    """The track's details leave the swipe for ⋯'s About (patch/np_about.c), which moves them into a page of their own
    while it is open: view_info goes from the slide_view to the window, hidden, at the slide's place in it (stock's
    np_info still finds and fills its labels there), so on About its rows stand where they stood on Now Playing."""
    info, box, slide = named['view_info'], named['slide_view_view'], named['slide_view']
    slide[3].remove(info)
    info[1] = [box[1][0] + slide[1][0], box[1][1] + slide[1][1], slide[1][2], slide[1][3]]
    info[2] = {k: v for k, v in info[2].items() if k != 'self_layout'} | {'visible': 'false'}
    # first, under the rest: stock's AWTK hands a touch to a hidden widget too, and last it took every one on the page
    # (the swipe, More, the bar: measured in the emulator); first, the slide_view_view over it takes them
    root[3].insert(0, info)


def np_rate_page(named):
    """The rating page (patch/np_rate.c): an empty view, the slide_view's page after the lyrics, so a swipe from them
    reaches it and the slide_view's value is 2 on it. The payload paints it whole: the album art's glow, the title,
    the artist, five stars and the page dots, full screen while it rests there."""
    slide = named['slide_view']
    lyrics = next(i for i, page in enumerate(slide[3]) if any(n is named['view_lrc'] for n in walk(page)))
    rate = ['view', [0, 0, slide[1][2], slide[1][3]], {'self_layout': 'default(x=0,y=0,w=100%,h=100%)', 'name': 'view_rate'}, []]
    slide[3].insert(lyrics + 1, rate)


# Now Playing on the grid (Geometry.np_art: docs/oled-design.md#now-playing), in window rows from the grid's
# bar: the art np_art square at text_x on the window's top, its corners the theme's playing.art.radius; NP_GRID_GAP
# right of it the title, artist and album, their line tops NP_GRID_LINES down the art, the heart under the album
# (NP_GRID_HEART) and More at the right end of the art's bottom row (both on the art's page); the slide
# NP_GRID_SLIDE_GAP above the bar, which runs from text_x to the right column with its top at np_bar_y on screen; the
# times NP_GRID_TIMES_DY under that, centred, on the same columns; the row of shuffle, radio, repeat and Up Next
# (np_bar.c np_modes) centred NP_GRID_MODES_DY under it, over the play mode's target, which spans the row; the page
# dots NP_GRID_DOTS_DY under the bar, hidden, as the row's on dots take their place. Sizes are AWTK's
# (1.21 times the design's em): the artist and album 22, the times 19 in Inter, whose digits are tabular.
NP_GRID_GAP = 18
NP_GRID_LINES = (6, 38, 62)
NP_GRID_TITLE_PX = 27  # the title's line: OLED's playing.title.font_px, which theme_font sets at run time
NP_GRID_META_PX = 22
# More and the heart centre on an 18 px Inter line whose baseline sits on the art's bottom row (its ascent 0.969 of its
# 1.21 em line), the line SPEC-next's "1 of 10" took
NP_GRID_ROW_PX = 18
NP_GRID_ROW_ASCENT = 14
NP_GRID_TIME_PX = 19
NP_GRID_SECONDARY = '#ABABAB'  # Text Secondary; Light Mode flips it as every grey
NP_GRID_ICON = 34  # More's target, the theme's 20 px glyph centred in it (np_bar.c np_glyphs)
# The heart's target (the user's, 2026-11-18, docs/oled-design.md#now-playing): x from NP_GRID_HEART[0] left of the
# text column, top on the album line's foot, w by h, the theme's 34 px heart image (tools/art.py NP_HEART_PX) centred in
# it, so it stands at screen x 193 to 227 and y 146 to 180 (its 18 px of ink from x 194, np_heart) and the target covers
# x 182 to 238 and y 142 to 184
NP_GRID_MORE_INSET = 7  # More's centre from the right column: Phosphor's dots-three, 12.5 px of ink, ends on it
NP_GRID_HEART = (10, 56, 42)
NP_GRID_BAR = 6
NP_GRID_TOUCH = 22  # the bar's target, short of the heart's above and the times' below
NP_GRID_SLIDE_GAP = 5
NP_GRID_TIMES_DY = 22
NP_GRID_MODES_DY = 58  # the row centred on y 286 on screen
NP_GRID_MODES = (269, 44)  # the play mode's target: the row, its outer glyphs' edges at x 53 and 322 (np_bar.c np_modes)
NP_GRID_DOTS_DY = 70  # the page dots centred on y 298 on screen, hidden
NP_GRID_MARGIN = 2  # hscroll_label's own margin each side of its text (stock's margin_left and margin_right)


def np_grid(root, named, g, title, artist, album, remain):
    """playing_page's widgets placed on g's grid (NP_GRID_*), the only layout the page has; every name the payload binds keeps its widget."""
    a, tx, right = g.np_art, g.text_x + g.np_art + NP_GRID_GAP, 375 - g.right_x
    bar_y, page = g.np_bar_y - g.bar, named['view_album'][3]
    named['img_cover'][1] = [g.text_x, 0, a, a]
    named['img_playstate'][1] = [g.text_x + (a - 120) // 2, (a - 120) // 2, 120, 120]
    named['image_wait'][1] = [g.text_x + (a - 54) // 2, (a - 54) // 2, 54, 54]
    for n, top, px in zip((title, artist, album), NP_GRID_LINES, (NP_GRID_TITLE_PX, NP_GRID_META_PX, NP_GRID_META_PX)):
        # the box takes the margins, so the text's column is tx to the right column, which clips it
        n[1] = [tx - NP_GRID_MARGIN, top, right - tx + 2 * NP_GRID_MARGIN, px]
        if n is title:  # every state: the labels are disabled (enable false), so a lone normal size would not apply
            n[2].update({f'style:{s}:font_size': str(px) for s in ('normal', 'disable', 'focused')})
            check({v for k, v in n[2].items() if k.endswith(':font_size')} == {str(px)}, 'Now Playing text keeps a state size')
        for key in n[2]:
            if key.endswith(':font_size'): n[2][key] = str(px)
            if n is not title and key.endswith(':text_color'): n[2][key] = NP_GRID_SECONDARY
    # the album one line ending in stock's ellipsis, never scrolled (np_marquee.c drives the title and artist alone),
    # so the heart under it keeps its place
    album[2].update(only_focus='true', ellipses='true')
    named['view_buttons'][3].remove(named['img_more'])
    cy = a - NP_GRID_ROW_ASCENT + NP_GRID_ROW_PX // 2
    more_x = right - NP_GRID_MORE_INSET - NP_GRID_ICON // 2
    named['img_more'][1] = [more_x, cy - NP_GRID_ICON // 2, NP_GRID_ICON, NP_GRID_ICON]
    pad, w, h = NP_GRID_HEART
    named['img_fav'][1] = [tx - pad, NP_GRID_LINES[2] + NP_GRID_META_PX, w, h]
    page.remove(named['img_playmode'])
    page.append(named['img_more'])
    slide_h = bar_y - NP_GRID_SLIDE_GAP
    named['slide_view'][1] = [0, 0, 375, slide_h]
    named['view_lrc'][2]['self_layout'] = f'default(x=0,y=0,w=375,h={slide_h})'
    named['view_lrc'][1][3] = slide_h
    dots_y = bar_y + NP_GRID_DOTS_DY
    named['slide_view_view'][1] = [0, 0, 375, dots_y + 10]
    dots = named['slide_indicator1']
    dots[1][1] = dots_y
    dots[2]['self_layout'] = f'default(x=0,y={dots_y},w=100%,h=10)'
    dots[2]['visible'] = 'false'  # the row's on dots sit there; a swipe still turns the page
    np_slider(named['slider_play'], g.text_x, bar_y + NP_GRID_BAR // 2, right - g.text_x, NP_GRID_BAR)
    named['slider_play'][1][1:4:2] = [bar_y + NP_GRID_BAR // 2 - NP_GRID_TOUCH // 2, NP_GRID_TOUCH]
    for name in ('img_repeata', 'img_repeatb'):
        named[name][1][0] = g.text_x
    for n, x in ((named['label_playtime'], g.text_x), (remain, right - 80)):
        n[1] = [x, bar_y + NP_GRID_TIMES_DY - NP_GRID_TIME_PX // 2, 80, NP_GRID_TIME_PX]
        for state in [k.split(':')[1] for k in n[2] if k.endswith(':text_color')]:
            n[2].update({f'style:{state}:text_color': '#FFFFFF', f'style:{state}:font_name': FACES['inter'].name,
                         f'style:{state}:font_size': str(NP_GRID_TIME_PX)})
    w, h = NP_GRID_MODES
    mode = named['img_playmode']
    mode[1] = [(375 - w) // 2, bar_y + NP_GRID_MODES_DY - h // 2, w, h]
    mode[2]['opacity'] = '0'  # its target stays, each button's share np_bar.c np_mode_click's; np_modes draws the row over it
    root[3].append(mode)
    np_info_values(named)


# The grid's info page (⋯'s About, np_info_out) as the source of About's info grid (oled-design.md#track-details), which
# np_about.c paints itself: the details stay hidden, and each of stock's labels gets a <name>_value hscroll_label
# beside it, so navigation.c ringnav_np_info splits name and value; np_about_fill reads them.
NP_INFO_NAMES = ('label_album', 'label_genre', 'label_year', 'label_encode', 'label_bits', 'label_sample', 'label_kbps',
                 'label_size', 'label_location')


def np_info_values(named):
    info = named['view_info'][3]
    check(sorted(n[2]['name'] for n in info) == sorted(NP_INFO_NAMES), 'Unexpected info page')
    for key in NP_INFO_NAMES:
        info.append(['hscroll_label', [0, 0, 0, 0], {'name': key + '_value', 'style': 's_scrlabel_white20r', 'visible': 'false'}, []])


# The full-screen art (a tap on Now Playing's opens it, any tap closes it): stock's art, shade and
# song name, the art a centred BC_ART square on the window's black (bigcover_page_init only loads its image
# and sets its draw type, scale_auto as here), the shade and the name on its foot as stock has them on the
# screen's. paint_cover rounds the playing art after scaling.
BIGCOVER = 'bigcover_page.bin'
BC_TITLE_PAD = inc('BC_TITLE_PAD')


def bigcover_page(root):
    check(root[2].get('fullscreen') == 'true' and [n[2].get('name') for n in root[3]] == [
        'img_bigcover', 'view_shade', 'scrlabel_title'], 'Unexpected full-screen art page')
    art, shade, title = root[3]
    check(art[2].get('self_layout') == 'default(x=0,y=0,w=100%,h=100%)' and art[2].get('draw_type') == 'scale_auto' and
          shade[1] == [0, 270, 375, 50] and title[1] == [42, 281, 290, 28], 'Unexpected full-screen art layout')
    x, y = (375 - BC_ART) // 2, (320 - BC_ART) // 2
    art[1] = [x, y, BC_ART, BC_ART]
    art[2]['self_layout'] = f'default(x={x},y={y},w={BC_ART},h={BC_ART})'
    foot = y + BC_ART - shade[1][3]
    title[1] = [x + BC_TITLE_PAD, foot + title[1][1] - shade[1][1], BC_ART - 2 * BC_TITLE_PAD, title[1][3]]
    shade[1] = [x, foot, BC_ART, shade[1][3]]


# Quick settings (the pull-down statusbar_dialog, which covers the whole screen): the eight
# stock 60px controls stay in their four columns, two rows from QS_TOP. Each label gets the same
# two-line QS_LABEL_H area in the stock 16px style, top-aligned so single- and two-line labels start
# on one line, QS_LABEL_GAP under its icon and QS_ROW_GAP above the next row. Brightness becomes a
# slim QS_BAR track in a QS_TOUCH-high slider (tap or drag anywhere on it, as stock) between the
# stock dim and bright suns, which keep their images. dialog_statusbar_dialog_init (0x4a2338)
# finds these widgets by name and never moves or resizes them.
QUICK_SETTINGS = 'dialog/statusbar_dialog.bin'
QS_TOP, QS_ICON, QS_LABEL_GAP, QS_LABEL_H, QS_ROW_GAP, QS_LABEL_W = 20, 60, 6, 40, 12, 80
QS_PITCH = QS_ICON + QS_LABEL_GAP + QS_LABEL_H + QS_ROW_GAP
QS_SUN, QS_BAR, QS_TOUCH = 26, 6, 48
QS_EDGE = 30                     # the suns line up with the first and last icon columns
QS_GRID = {'wifiswitch': 'wifi', 'btswitch': 'bt', 'lock': 'lock', 'gain': 'gain',
           'usbmode': 'usbmode', 'po': 'outputway', 'playset': 'playset', 'sysset': 'sysset'}


def quick_settings(root):
    menu, light = root[3]
    check([menu[2].get('name'), light[2].get('name')] == ['view_menu', 'view_backlight'], 'Unexpected quick settings')
    named = {n[2]['name']: n for n in menu[3]}
    check(sorted(named) == sorted([f'img_{k}' for k in QS_GRID] + [f'label_{v}' for v in QS_GRID.values()]),
            'Unexpected quick settings controls')
    for i, (icon, label) in enumerate(QS_GRID.items()):
        image, text = named['img_' + icon], named['label_' + label]
        x, y, w, h = image[1]
        check((w, h) == (QS_ICON, QS_ICON) and y == (0, 112)[i // 4] and text[2].get('style') == 's_label_white18c',
                f'{icon}: unexpected quick settings control')
        image[1][1] = (i // 4) * QS_PITCH
        text[1] = [x + (QS_ICON - QS_LABEL_W) // 2, image[1][1] + QS_ICON + QS_LABEL_GAP, QS_LABEL_W, QS_LABEL_H]
        text[2].update({'style': 's_label_white16c', 'style:normal:text_align_v': 'top'})
    menu[1] = [0, QS_TOP, 375, 2 * QS_PITCH - QS_ROW_GAP]
    slider, dim, bright = light[3]
    check([n[2]['name'] for n in light[3]] == ['slider_backlight', 'image0', 'image1'] and
            (dim[1][2], bright[1][2]) == (QS_SUN, QS_SUN), 'Unexpected brightness row')
    light[1] = [0, menu[1][1] + menu[1][3] + QS_ROW_GAP, 375, QS_TOUCH]
    check(light[1][1::2] == [inc('VOL_PANEL_Y'), inc('VOL_PANEL_H')], 'The volume panel must cover the brightness row')
    dim[1] = [QS_EDGE, (QS_TOUCH - QS_SUN) // 2, QS_SUN, QS_SUN]
    bright[1] = [375 - QS_EDGE - QS_SUN, (QS_TOUCH - QS_SUN) // 2, QS_SUN, QS_SUN]
    track = [QS_EDGE + QS_SUN + 12, 0, 375 - 2 * (QS_EDGE + QS_SUN + 12), QS_TOUCH]
    check(slider[2].get('slide_with_bar') == 'true', 'Unexpected brightness slider')
    props = plain_slider(slider[2], NP_TRACK, NP_FILL, QS_BAR)  # Now Playing's; the accent at runtime (quick_fill)
    props.update(self_layout='default(x={},y=0,w={},h={})'.format(*track[::2], QS_TOUCH), dragger_size=str(QS_TOUCH))
    slider[1], slider[2] = track, props


# The confirm pair (img_cancel, img_enter; 80px tiles around 60px discs) becomes two
# full-width text rows, Cancel and OK (after maks112v/qpod).
CONFIRM = 'dialog/confirminfo_dialog.bin'
CONFIRM_TILE = 80
CONFIRM_TOP, CONFIRM_ROW = inc('CONFIRM_TOP'), 48  # Cancel and OK, under the prompt


def confirm_dialog(root):
    check([n[2].get('name') for n in root[3]] == ['img_cancel', 'img_enter'] and
            all(n[1][2:] == [CONFIRM_TILE, CONFIRM_TILE] for n in root[3]), 'Unexpected confirm dialog')
    # Clear, as the status bar's: the dialog's border is drawn over the bar's ends, paper in Light Mode.
    root[2].update({f'style:{s}:border_color': '#00000000' for s in ('normal', 'disable', 'focused')})
    # Two full-width text rows under the prompt instead of the disc tiles, so the wheel's selection
    # is the lists' own bar or card. Stock looks both up by name and binds the same callbacks.
    for i, n in enumerate(root[3]):
        n[0], n[1] = 'button', [0, CONFIRM_TOP + i * CONFIRM_ROW, 375, CONFIRM_ROW]
        n[2] = {'name': n[2]['name'], 'text': ('Cancel', 'OK')[i], 'focusable': 'false',
                'style:normal:font_size': '20', 'style:normal:text_align_h': 'center'}
        # Every state, or AWTK's button/default shows its blue border (1px) and fill; a tap gets
        # the lists' pressed grey, the wheel the selection.
        for state in ('normal', 'pressed', 'over', 'focused'):
            n[2].update({'style:%s:bg_color' % state: '#333333' if state == 'pressed' else '#00000000',
                         'style:%s:border_color' % state: '#00000000', 'style:%s:border_width' % state: '0',
                         'style:%s:text_color' % state: '#FFFFFF'})


# The volume dialog loses its highlight="default(alpha=200)", so the window manager
# creates no highlighter and nothing under it dims; the payload draws the volume into the dialog
# (volume.c vol_paint): a band on Now Playing, a panel elsewhere.
VOLUME = 'dialog/volume_dialog.bin'


def volume_dialog(root):
    check(root[0] == 'dialog' and root[2].get('name') == 'volume_dialog' and
            root[2].get('highlight') == 'default(alpha=200)', 'Unexpected volume dialog')
    del root[2]['highlight']
    # Clear, as the status bar's: stock's black border rings the screen, paper in Light Mode.
    root[2].update({f'style:{s}:border_color': '#00000000' for s in ('normal', 'disable', 'focused')})
    # Snapshot paints can precede vol_paint; never expose the stock full-screen controls.
    check([(n[0], n[2].get('name')) for n in root[3]] ==
          [('slider', 'slider_vol'), ('label', 'label_vol')], 'Unexpected volume controls')
    for child in root[3]:
        child[2]['visible'] = 'false'


# Settings and Streaming lose their navbar, as on the local pages, and their lists hold
# SET_ROWS complete SET_ROW rows from SET_TOP; ipod_list_layout (patch/navigation.c) lays the native
# rows out to match. Tidal keeps its navbars: most hold a search button with no hardware equivalent.
NAVBAR_ONLY = AUDIT['navbar_only']
SET_ROW, SET_TOP, SET_ROWS, SET_STOCK_ROW, SET_STOCK_BODY = (inc(n) for n in ('SET_ROW', 'SET_TOP', 'SET_ROWS', 'SET_STOCK_ROW', 'SET_STOCK_BODY'))

# The re-pitched list pages' rows: art pages' carry artwork (Albums), Folder's are its own (folder_*, an
# art page's unless the set says), the Library's too (lib_*), text pages' carry none. Any other page rows() re-pitches (the playlist actions) is text. test/build.py imports it.
PAGE_KIND = {'localmusic/allmusic_page.bin': 'song', 'localmusic/albuminfo_page.bin': 'text',
             'localmusic/artistinfo_page.bin': 'text', 'localmusic/playlist_page.bin': 'playlist',
             'localmusic/album_page.bin': 'art', 'folder_page.bin': 'folder', 'localmusic_page.bin': 'lib',
             'localmusic/localclass_page.bin': 'text'}


def page_rows(g, kind):
    """(pitch, body, artwork inset) of a page kind's rows under g."""
    return {'art': (g.art_pitch, g.art_body, g.art_inset), 'lib': (g.lib_pitch, g.lib_body, g.lib_inset),
            'song': (g.song_pitch, g.song_body, g.text_art_inset),
            'folder': (folder_pitch(g), folder_body(g), g.art_inset),
            # Playlists' rows are their own where the grid gives them a pitch; playlists.c's pl_dress sizes them
            'playlist': (g.playlist_pitch, g.playlist_pitch, g.text_art_inset) if g.playlist_pitch else
                        (g.song_pitch, g.song_body, g.text_art_inset)
            }.get(kind, (g.pitch, g.body, g.text_art_inset))


def text_y(g, y):
    """A stock row label's y in the 70px body in g's body: the 20px title (11), the 24px title (14),
    a 28px label (21), metadata (40) and a count (41)."""
    return {11: g.title_y, 14: g.title24_y, 21: (g.body - 28) // 2, 40: g.meta_y, 41: g.count_y}[y]


# The settings rows' stock 52px artwork (SET_STOCK_ICON), pinned by hash in ipod.json,
# is pre-sized to SET_ICON at build time so the rows draw it 1:1 instead of scaling it on the device.
# ImageMagick's Lanczos resize weights colour by alpha, so edges keep their colour and transparency;
# -strip and the excluded date chunks keep the bytes reproducible. Only native settings code names
# these images, so no other screen sees the smaller size. An icons.mono theme greys them as they load
# (navigation.c ringnav_image_add), as it does list_into's ink.
SETTINGS_ICONS = AUDIT['settings_icons']
SET_ICON, SET_STOCK_ICON = inc('SET_ICON'), inc('SET_STOCK_ICON')


def png_header(data):
    """(width, height, bit depth, colour type) from a PNG's IHDR."""
    check(data[:8] == b'\x89PNG\r\n\x1a\n' and data[12:16] == b'IHDR', 'Not a PNG')
    return struct.unpack('>IIBB', data[16:26])


def imagemagick(*args, data):
    import subprocess
    tool = need(imagemagick_tool(), 'ImageMagick (magick or convert) is required for the iPod settings icons')
    return subprocess.run([tool, *args], input=data, stdout=subprocess.PIPE, check=True).stdout

IMAGEMAGICK_TOOL: dict = {}

def imagemagick_tool():
    """magick or convert, looked up once per PATH (and per shutil.which, so a test's stand-in looks again): a
    search that misses magick stats every directory on PATH, under WSL mostly Windows' (/mnt/c, slow); 146
    searches took 10 s of a 41 s build."""
    import os, shutil
    key = (os.environ.get('PATH'), shutil.which)
    if key not in IMAGEMAGICK_TOOL:
        IMAGEMAGICK_TOOL[key] = shutil.which('magick') or shutil.which('convert')
    return IMAGEMAGICK_TOOL[key]


# Output PNGs: no metadata, always 8-bit RGBA.
PNG_OUT = ['-strip', '-define', 'png:exclude-chunks=date,time', '-define', 'png:color-type=6',
           '-define', 'png:bit-depth=8', 'png:-']

STATUS_ICONS = AUDIT['status_icons']


def status_icon(name, data):
    """One optical weight, rendered at 4x for smooth edges on the native-size panel."""
    check(sha(data) == STATUS_ICONS.get(name), f'{name}: unaudited status icon')
    width = 14 if name.startswith('bar_wifi') else 42 if name.startswith('bar_bt') else 12
    if name.startswith('bar_wifi'):
        level = int(name[-5])
        # Concentric arcs (+-44 degrees) round the dot at 7,12.6: ink rows 3-14, the battery's 11 px.
        paths = ('M0.88,6.27 A8.8,8.8 0 0,1 13.12,6.27', 'M2.76,8.21 A6.1,6.1 0 0,1 11.24,8.21',
                 'M4.64,10.15 A3.4,3.4 0 0,1 9.36,10.15')
        shapes = 'stroke-width 1.5 ' + ' '.join(f"stroke '{'white' if level >= 4-i else '#666666'}' path '{p}'"
                                                for i, p in enumerate(paths))
        shapes += f" stroke none fill '{'white' if level else '#666666'}' circle 7,12.6 8.2,12.6"
    elif name.startswith('bar_bt'):
        color = 'white' if name == 'bar_btcon.png' else '#666666'
        shapes = f"stroke '{color}' path 'M34.2,4 L41.2,11 L37.2,14 L37.2,2 L41.2,5 L34.2,12'"
    elif name == 'bar_play.png':
        shapes = "fill white stroke white stroke-width 1 path 'M2,2.5 L10,8 L2,13.5 Z'"
    else:
        shapes = "stroke white stroke-width 2.5 path 'M3,3 L3,13 M9,3 L9,13'"
    draw = 'scale 4,4 fill none stroke-width 1.6 stroke-linecap round stroke-linejoin round '+shapes
    return imagemagick('-size', f'{width*4}x64', 'xc:none', '-draw', draw,
                      '-filter', 'Lanczos', '-resize', f'{width}x16!', *PNG_OUT, data=b'')



def row_icon(data, size=SET_ICON, sharp=False):
    """A 52px row icon pre-sized to SET_ICON, or the pane's size, so it draws 1:1 (set_child) instead of
    scaling it on the device with hard edges. sharp: a light unsharp mask after the shrink, so the
    Streaming logos stay as crisp as list_tidal beside them (the audited settings icons keep stock's
    plain filter, test/build.py compares their colours)."""
    return imagemagick('png:-', '-alpha', 'on', '-filter', 'Lanczos', '-resize', f'{size}x{size}!',
                       *(['-unsharp', '0x0.6+0.8+0'] if sharp else []), *PNG_OUT, data=data)


# Each stock placeholder's replacement size: stock's own, but Now Playing's and the full-screen art's at
# the art they fill (stock's 188 scaled down there on the device, jagged), and the album grids' at
# album_children's inset cover (110 less TILE_INSET + TILE_CLEARANCE a side), drawn 1:1 by its scale-down.
# The square ones keep the disc's transparent corners, so a selected row's bar shows through them; under
# square art (ipod_square_art) navigation.c's square_disc fills them as they load.
ART_PLACEHOLDERS = {'default_album_big.png': 110 - 2 * (inc('TILE_INSET') + inc('TILE_CLEARANCE')), 'default_album_small.png': 52,
                    'play_defaultcover.png': NP_ART,
                    'default_smallcover.png': 140, 'default_bigcover.png': BC_ART}


def sized_art(data, size):
    w, h = size if isinstance(size, tuple) else (size, size)
    px = min(w, h)
    return imagemagick('png:-', '-filter', 'Lanczos', '-resize', f'{px}x{px}!', '-background', 'black' if w != h else 'none',
                       '-gravity', 'center', '-extent', f'{w}x{h}', *PNG_OUT, data=data)


def settings_icon(name, data):
    check(sha(data) == SETTINGS_ICONS.get(name), f'{name}: unaudited settings icon')
    check(png_header(data) == (SET_STOCK_ICON, SET_STOCK_ICON, 8, 6), f'{name}: unexpected stock icon format')
    out = row_icon(data)
    check(png_header(out) == (SET_ICON, SET_ICON, 8, 6), f'{name}: filtered icon is not {SET_ICON}px RGBA')
    opaque = [imagemagick('png:-', '-format', '%[opaque]', 'info:', data=d) for d in (data, out)]
    check(opaque[0] == opaque[1], f'{name}: filtering changed the transparency')
    return out


QUIET_ICONS = AUDIT['quiet_icons']
SETTINGS_SLIDERS = ('systemset/backlight_page.bin', 'playset/balance_page.bin',
                    'playset/maxvol_page.bin', 'playset/bootvol_page.bin')
# Pages whose init puts an on/off switch in the toolbar (img_bootswitch, img_switch); they keep it.
TOOLBAR_SWITCHES = ('systemset/sleepshutdown_page.bin', 'playset/bootvol_page.bin')
# Pages that keep their toolbar under any set: the slider page's is Crossfade's and its own; these two hold buttons or have no switch to move.
KEEP_TOOLBAR = ('playset/bootvol_page.bin', 'playerjumpinfo_page.bin', 'specfolder_page.bin')
SLEEP_TIMER = 'systemset/sleepshutdown_page.bin'


def slider_plain(path, root):
    """A settings slider loses its endpoint glyphs: the bar, value and -/+ say enough. The images stay,
    named, imageless and empty, as stock looks them up."""
    if path not in SETTINGS_SLIDERS:
        return
    slider, = [n for n in walk(root) if n[0] == 'slider']
    slider[2] = plain_slider(slider[2], NP_TRACK, NP_FILL, 8)
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        slider[2].update({f'style:{state}:round_radius': '4',
                          f'style:{state}:border_color': '#00000000'})
    endpoints = [n for n in walk(root) if n[0] == 'image' and n[2].get('image') in (
        'drop_lighleft', 'drop_lightright', 'balance_left', 'balance_right', 'vol_left', 'vol_right')]
    check(len(endpoints) == 2, f'{path}: unexpected slider endpoints')
    for n in endpoints:
        del n[2]['image']
        n[1][2:] = [0, 0]


def quiet_icon(name, data):
    check(sha(data) == QUIET_ICONS.get(name), f'{name}: unaudited control icon')
    return imagemagick('png:-', '-trim', '+repage', '-filter', 'Lanczos', '-resize', '24x24',
                      '-channel', 'RGB', '-fill', '#aaaaaa' if name == 'play_moredown.png' else '#eeeeee',
                      '-colorize', '100', '+channel', '-gravity', 'center', '-background', 'none',
                      '-extent', '28x28', *PNG_OUT, data=data)


def branch(op, at, target):
    """op (beq, b, bal...) at address at, to target."""
    delta = (target - at - 4) // 4
    check(-32768 <= delta <= 32767, f'{at:#x}: branch out of range')
    return op | (delta & 0xffff)


def patch_word(data, address, old, new, purpose, changes=None):
    off = fileoff(data, address)
    check(struct.unpack_from('<I', data, off)[0] == old, f'{address:#x}: unexpected instruction ({purpose})')
    struct.pack_into('<I', data, off, new)
    if changes is not None:
        changes.append(dict(address=hex(address), original=hex(old), patched=hex(new), purpose=purpose))


def songs_action_row(root, g):
    """Songs' action bar as a plain row: the row and what is under it. Stock rebuilds the row's children on each refresh, so the
    payload shapes those as it paints (navigation.c action_row)."""
    row = next(n for n in root[3] if n[2].get('name') == 'view_navbar_allplay')
    row[1][3] = g.action_row
    for n in root[3]:
        if n[1][1] == 50 and n is not row:
            if n[0] in ('table_view', 'list_view'):  # the whole rows under it
                n[1][3] = fit_rows(g, g.action_row, g.song_pitch)
            n[1][1] = g.action_row


def fit_rows(g, top, pitch):
    """A list box from top: on to the window's foot, the rows past list_end fading into it (navigation.c list_fade), as
    Home's do, whatever the rows' pitch."""
    return 320 - g.bar - top


# Lists that may end above list_end: Home's main list, which home_overflow runs on at run time (coverflow.c),
# Now Playing's lyrics, which are no rows and fade their own (lyrics.c lyrics_fade), and stock's Scan Specified
# Folders table, whose Scan button sits under it. ponytail: Scan Specified Folders keeps stock's 78 px table until its
# page is rebuilt as rows; then it leaves this list.
UNFIT_LISTS = (('home_page.bin', 'list_view_home'), ('playing_page.bin', 'view_lrc'), ('specfolder_page.bin', 'table_view'))


def cut_lists(path, data, g):
    """The names of page path's lists (list_view, table_view; data, the page built) whose box stops at or above
    list_end, where their rows would end on a whole row over a black band rather than peek through the fade into the
    window's foot (navigation.c list_fade, which leaves such a box alone). A box sized by its parent's layout is its
    parent's; the More menus end in their panel (MODAL_PAGES, menu_modal.c)."""
    if path in MODAL_PAGES:
        return []
    cut = []

    def down(n, y):
        kind, box, props, children = n
        if kind != 'window':
            y += box[1]
            if (kind in ('list_view', 'table_view') and '100%' not in props.get('self_layout', '') and
                    props.get('visible') != 'false' and y + box[3] <= g.list_end and
                    (path, props.get('name', '')) not in UNFIT_LISTS):
                cut.append(props.get('name', kind))
        for c in children:
            down(c, y)
    down(decode(data), 0)
    return cut


# The menus that open over the page under them: Now Playing's More and a playlist's More, each the page patch_asset
# makes as a dialog, which the payload draws as the menu sheet (menu_modal.c), as it does the hold menus.
MODAL_PAGES = ('playermore_page.bin', 'localmusic/playlistsmore_page.bin')


def modal_page(data):
    """A page as a dialog with no slide: the window manager paints the page under it, which the payload dims and
    insets the menu over (menu_modal.c modal_more). Its init makes it modeless (to_modal), so navigator_to returns."""
    root = decode(data)
    check(root[0] == 'window', 'Unexpected modal page')
    root[0] = 'dialog'
    root[1] = [0, 0, 375, 320]  # a window takes the screen from the window manager; a dialog keeps its own box
    root[2].pop('anim_hint', None)
    return encode(root)


def patch_asset(path, data, g):  # pragma: no mutate block
    """UI asset path's bytes (data, stock's) laid out on grid g."""
    page = patch_page(path, data, g)
    return modal_page(page) if path in MODAL_PAGES else page


def full_bleed(data, bar):
    """A UI asset as a full-bleed page: every normal window takes the whole screen (tools/build.py FULL_BLEED), the
    status row an overlay on it, so a window's top-level children move down bar px and rest where they did when the
    window started under the row (docs/oled-design.md#full-bleed). A child placed by self_layout moves in its params,
    which AWTK lays out again on each relayout: a px y gains bar, a 100% height loses it (AWTK's negative h is the
    parent's less that). Dialogs, the keyboard and the system bar keep their own boxes."""
    root = decode(data)
    if root[0] != 'window':
        return data
    for child in root[3]:
        layout = child[2].get('self_layout')
        if layout is None:
            child[1][1] += bar
            continue
        found = need(re.fullmatch(r'default\((.*)\)', layout), f'Unexpected self_layout {layout}')
        params = dict(kv.split('=', 1) for kv in found[1].split(','))
        if re.fullmatch(r'-?\d+', params.get('y', '0')):
            params['y'] = str(int(params.get('y', '0')) + bar)
        if params.get('h') == '100%':
            params['h'] = str(-bar)
        child[2]['self_layout'] = 'default(' + ','.join(f'{k}={v}' for k, v in params.items()) + ')'
        child[1][1] += bar
    return encode(root)


def patch_page(path, data, g):  # pragma: no mutate block
    geo = with_geometry(g)
    pitch, body, inset = page_rows(g, PAGE_KIND.get(path, 'text'))
    stock = path in STOCK_ROWS
    check(sha(data) == (STOCK_ROWS if stock else UI_ASSETS)[path], f'{path}: unaudited UI asset')
    if stock:
        pitch, body = g.stock_row, g.stock_body
    plain = path not in KEEP_TOOLBAR  # the bar holds the title, a switch moves to a row (navigation.c switch_row)
    fits = g.stock_row == g.set_row  # at any other pitch stock's rows aren't settings rows: ipod_list_layout
    settings = path in NAVBAR_ONLY or (stock and plain and fits and path != 'playerqueue_page.bin')
    root = decode(data)
    check(encode(root) == data, f'{path}: UI round trip differs')
    if path == ARTIST_PAGE:
        artist_tabs(root)
    whole: dict = {HOME_PAGE: functools.partial(ipod_home, g=g), STATUS_BAR: functools.partial(status_bar, g=g),
             PLAYING_PAGE: functools.partial(playing_page, g=g), QUICK_SETTINGS: quick_settings,
             CONFIRM: confirm_dialog, VOLUME: volume_dialog, BIGCOVER: bigcover_page}
    if path in whole:
        whole[path](root)
        return encode(root)
    if path == 'dialog/addplaylist_dialog.bin':
        edit = root[3][0][3][0]
        check(edit[0] == 'edit' and edit[2].get('name') == 'ssedit' and
              edit[2].get('sensitive') == 'false', 'Unexpected new playlist editor')
        edit[2]['sensitive'] = 'true'
        return encode(root)
    # Home is never reopened; Now Playing, Coverflow and the dialogs open at once.
    check(root[0] == 'window' and not any(k.endswith('anim_hint') for k in root[2]), f'{path}: unexpected window')
    if not stock:
        root[2]['anim_hint'] = SLIDE
    if path in AUDIT['slide_only']:  # the PEQ editor builds its own children (patch/peq_ui.c)
        return encode(root)
    nav = [n for n in root[3] if n[2].get('name') == 'view_navbar']
    check(len(nav) == 1 and nav[0][1] in ([0, 0, 375, 50], [0, 0, 370, 50]), f'{path}: unexpected toolbar')  # 370: stream_page
    # Keep the widget (and callback lookups) alive. Children may be recreated by stock.
    if path in TOOLBAR_SWITCHES and not plain:
        slider_plain(path, root)
        return encode(root)
    if stock and not plain:  # the rows alone, under the toolbar; the lists keep their boxes
        def table_rows(n):
            for prop in ('row_height', 'default_item_height'):
                if n[2].get(prop) == '78':
                    n[2][prop] = str(g.stock_row)
            for child in n[3]:
                table_rows(child)
        table_rows(root)
        return encode(root)
    nav[0][2]['visible'] = 'false'
    nav[0][2]['enable'] = 'false'
    for n in root[3]:
        if n is nav[0]:
            continue
        kind, box, props, _ = n
        if kind == 'list_view' and settings:  # settings rows: see ipod_list_layout
            check(box[1] == 50 and props.get('default_item_height') == str(SET_STOCK_ROW) and
                    'item_height' not in props, f'{path}: unexpected settings list')
            row = g.stream_row if path == 'stream_page.bin' else g.set_row
            props['default_item_height'] = str(row)
            box[1], box[3] = g.set_top, fit_rows(g, g.set_top, row)
        elif kind in ('list_view', 'table_view', 'tab_control'):
            check(box[1] in (50, 100), f'{path}: unexpected list position')
            box[1] -= 50
            box[3] = fit_rows(g, box[1], pitch)
        elif props.get('name') == 'view_navbar_allplay':
            check(box == [0, 50, 375, 50], f'{path}: unexpected action bar')
            box[1] = 0
        elif kind == 'list_item':  # playlist editing actions
            box[1] = ((box[1] - 60) // 78) * pitch  # playlists.c ringnav_playlistsmore
        elif props.get('name') == 'view':  # allmusic's stock empty-state panel
            box[1] -= 50
        elif path in NAVBAR_ONLY:  # settings panels and notes keep their size; text ends clear of a top corner
            check(box[1] >= 50, f'{path}: unexpected content under the toolbar')
            box[1] -= 50 - (g.set_row if path == SLEEP_TIMER else 0)  # under the row its switch moves to
            if path == SLEEP_TIMER:  # moved down a row, its 240 px would run past the window's foot
                box[3] = min(box[3], geo.BOTTOM - box[1])
            if kind in ('label', 'hscroll_label') and corner_inset(g.bar + box[1]):
                box[2] = min(box[2], 375 - box[0] - corner_x(g.bar + box[1], 0))
    if path in NAVBAR_ONLY:
        slider_plain(path, root)
        return encode(root)
    if settings:  # a 20 px label in Update Library's row: it takes the row, whose font it wouldn't hold
        for n in walk(root):
            if n[0] == 'hscroll_label' and n[1][1:2] == [25] and n[1][3] == 20:
                n[1][1], n[1][3] = 0, SET_STOCK_BODY
        return encode(root)
    # Songs' action bar as a plain row; the album and other song pages' too, so every list holds whole rows
    if path == 'localmusic/allmusic_page.bin' or any(n[2].get('name') == 'view_navbar_allplay' for n in root[3]):
        songs_action_row(root, g)

    def rows(n, in_row=False):
        kind, box, props, children = n
        if path == 'localmusic/album_page.bin' and props.get('name') == 'button1':  # inline black grid buttons
            for key, value in props.items():
                if key.endswith(':bg_color') and value == '#000000':
                    props[key] = '#00000000'
        if kind == 'table_client' and props.get('self_layout', '').startswith('default(x=0,y=0,w=365,'):
            # the full width, as the Library's scroll_view: the scroll bar overlays the row's right
            # edge, and the selection card and chevrons line up with every other list
            props['self_layout'] = props['self_layout'].replace('w=365', 'w=375')
            box[2] = 375
        in_row = in_row or kind in ('list_item', 'table_row') and box[3] in (0, 78)
        for prop in ('row_height', 'default_item_height'):
            if props.get(prop) == '78':
                props[prop] = str(pitch)
        if kind == 'list_view' and props.get('default_item_height') == str(g.set_row) and 'item_height' not in props:
            props['item_height'] = props['default_item_height']  # not a settings list (navigation.c settings_list)
        if in_row:
            if kind in ('list_item', 'table_row') and box[3] == 78:
                box[3] = pitch
            elif box[3] == 70:
                box[3] = body
            if box[1] in (11, 14, 21, 40, 41):
                box[1] = text_y(g, box[1])  # titles and metadata at the set's y in its body
            elif box[1] in (9, 10) and kind == 'image':
                box[1] = inset
        if path == ARTIST_PAGE:
            if kind == 'pages':
                check(props.get('self_layout') == 'default(x=0,y=40,w=100%,h=170)', 'Unexpected tabs layout')
                props['self_layout'] = f'default(x=0,y=40,w=100%,h={geo.BOTTOM - 40})'
                box[3] = geo.BOTTOM - 40
            if props.get('name') == 'list_view_album':
                box[3] = geo.BOTTOM - 40
        for child in children:
            rows(child, in_row)
    rows(root)
    return encode(root)


def ui_pages(cat, g):
    """{rel: page} for the UI windows the build writes over stock's under geometry g: every UI_ASSETS page, and
    each STOCK_ROWS page g changes."""
    ui, out = 'release/assets/default/raw/ui/', {}
    for rel in sorted(UI_ASSETS | STOCK_ROWS):
        data = cat(ui + rel)
        mine = patch_asset(rel, data, g=g)
        cut = cut_lists(rel, mine, g)
        check(not cut, f'{rel}: {", ".join(cut)} end above list_end under {g.name}: run them on to the window foot (fit_rows)')
        if rel in UI_ASSETS or mine != data:
            out[rel] = mine
    return out


# The GOT loads patch_code writes, of stock's navigator_close and dummy_destroy. No stock word beside one names its slot,
# and a slot moves with each stock build: tools/build.py checks that each still holds its function.
CLOSE_LOAD, DUMMY_LOAD = 0x8f99a914, 0x8f99ea00


def patch_code(data, symbols, g):
    changes = []
    word = functools.partial(patch_word, data, changes=changes)

    # Lookup song handlers loop until Now Playing is on top; animated Back cannot finish
    # until they return. Close each named page synchronously with stock navigator_close.
    for address, name_slot in ((0x4a7ed4, 0x468), (0x4af83c, 0x468), (0x537fbc, 0x420)):
        word(address, 0x8f99af04, CLOSE_LOAD, 'lookup unwind uses navigator_close')
        word(address + 4, 0x0320f809, 0x0320f809, 'lookup close call')
        word(address + 8, 0, 0x8fa40000 | name_slot, 'lookup close receives the top window name')

    values = immediate_values(g)  # the theme's set (tools/theme.py built_geometry)
    for group in AUDIT['immediates']:
        value = values[group['value']] if isinstance(group['value'], str) else group['value']
        for address, instruction in group['sites']:
            old = int(instruction, 16)
            word(int(address, 16), old, (old & 0xffff0000) | value, group['purpose'])
    group = AUDIT['row_layout_calls']
    for address, instruction in group['sites']:
        word(int(address, 16), int(instruction, 16),
             jal(symbols['compact_set_row_layout']), group['purpose'])
    for name, call in (('stock_row_layout_calls', 'stock_set_row_layout'), ('stock_row_text_calls', 'stock_row_text'), ('stock_row_style_calls', 'stock_row_style')):
        for address, instruction in AUDIT[name]['sites']:
            word(int(address, 16), int(instruction, 16), jal(symbols[call]), AUDIT[name]['purpose'])
    word(0x532d3c, 0x0320f809, 0, 'folder rebind retains the width owned by its row layouter')
    # Show Navigation Bar: each page moves its list for the switch, to the box its asset has anyway while the bar shows. The
    # lists keep the boxes their assets give them, ours included: each call's GOT load takes stock's dummy_destroy (0x72bd8c,
    # which returns 0) instead of widget_move_resize or widget_resize.
    for address, instruction in AUDIT['navbar_resize_calls']['sites']:
        word(int(address, 16), int(instruction, 16), DUMMY_LOAD, AUDIT['navbar_resize_calls']['purpose'])
    # Playlist page's Import/Export tiles: no icon (0 wide), the label spans the 164px tile in
    # stock's centred s_scrlabel_white24c (0x77c990) instead of 91px after the icon.
    for icon, style in ((0x4b56c0, 0x4b5748), (0x4b57e8, 0x4b5874)):
        word(icon, 0x24070032, 0x24070000, 'playlist tile icon width 0')
        word(icon + 0x54, 0x2407005b, 0x240700a4, 'playlist tile label spans the tile')
        word(icon + 0x60, 0x2405003f, 0x24050000, 'playlist tile label at the tile edge')
        word(style, 0x2505c434, 0x25050000 | 0x77c990 & 0xffff, 'playlist tile label centred')
    # iPod settings rows: the list_view layouter's layout slot (data, vtable 0x93a4bc + 8).
    word(inc('LIST_VIEW_LAYOUT_SLOT'), inc('LIST_VIEW_LAYOUT'), symbols['ipod_list_layout'],
         'settings lists stack SET_ROW rows and map their children')
    # toolsTimeItoa's formats, as an iPod writes times: 0:02 and 1:02:03, not 00:02 and 01:02:03.
    for address, old, new in ((0x78bba0, 0x64323025, 0x253a6425), (0x78bba4, 0x3230253a, 0x3a643230),
                              (0x78bba8, 0x30253a64, 0x64323025), (0x78bbac, 0x00006432, 0),
                              (0x78bbb0, 0x64323025, 0x253a6425), (0x78bbb4, 0x3230253a, 0x00643230),
                              (0x78bbb8, 0x00000064, 0)):
        word(address, old, new, 'times without a leading zero')
    # home_page_init's memory-play resume opens Now Playing; outside car mode it runs the page's player_start alone.
    word(0x534cf8, 0x0320f809, jal(symbols['ringnav_boot']),
         'boot resume restores the queue paused and stays on Home')
    # Stock skips that resume with Headphones Required on (bnez g_block_withoutphone_flag), whatever is connected.
    word(0x534c4c, 0x1440003c, 0, 'boot resume with Headphones Required on')
    return changes
