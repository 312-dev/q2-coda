"""Built-in themes: assets/themes/<id>/theme.xml in the Q2 theme XML, read, checked and written out as
the payload's theme table (themes.inc)."""
import colorsys
import dataclasses
import json
import math
import re
from typing import NoReturn
from build import FACES, ROOT, check, need
from ipod import GEOM_VALS, INC, O, STOCK_GRID, SLIDE, VALUE_NAMES, Geometry, corner_inset, immediate_values


ACCENTS_BLOCK = need(re.search(r'#define ACCENTS (?:.*\\\n)*.*', INC), 'Missing ACCENTS')[0]
ACCENT_NAMES = [n.lower() for n in re.findall(r'\}\s*,?\s*/\* (\w+)', ACCENTS_BLOCK)]
ACCENT_TONES = [[int(x, 16) for x in re.findall(r'0x([0-9a-fA-F]{6})', row)] for row in re.findall(r'\{([^}]*)\}', ACCENTS_BLOCK)]
ALL_ACCENTS = (1 << len(ACCENT_NAMES)) - 1
SETTINGS = {'light': ('SETTING_LIGHT', ('off', 'on', 'auto')), 'accent': ('SETTING_ACCENT', ACCENT_NAMES),
            'home': ('SETTING_HOME', ('split', 'full')),
            'battery': ('SETTING_BATTERY', ('icon', 'percent', 'both', 'inside'))}
ACCENT_KEYS = {'ACCENT': '&st.accent'}
# The IPOD keys the payload reads itself (theme_engine.c setting_keys[], every "IPOD", "<KEY>" literal): a
# theme's own key is never one of them.
PAYLOAD_KEYS = frozenset(
    re.findall(r'"([A-Z0-9_]+)"', need(re.search(r'setting_keys\[\] = \{([^}]*)\}', (ROOT/'patch/theme_engine.c').read_text()), 'Missing setting_keys')[1])
    + [k for f in sorted((ROOT/'patch').glob('**/*.c')) for k in re.findall(r'"IPOD", "([A-Z0-9_]+)"', f.read_text())])
# value name -> its C name (theme.h); the font keys take FONTS, build.py's faces (stock.h's FONT_*)
FONTS = {k: f.c for k, f in FACES.items()}
CHOICES = {**dict.fromkeys(('playing.title.font', 'playing.info.font', 'home.rows.font', 'list.row.font', 'list.row.meta_font', 'list.row.tall_font', 'bar.title.font', 'playing.artist.font'), FONTS),
           'scheme.mode': {'derive': 'SCHEME_DERIVE', 'light': 'SCHEME_LIGHT', 'dark': 'SCHEME_DARK'}}
# coverflow.shelf's thumb: each value's top (docs/internals.md#coverflow-thumb); fold_near past 50 would
# let the fold start with the caption still on the album before the letter's; jump_at, jump_ms and jump_pace are only Fast Navigation off's
# (1.7.6's letters), big_jump_at and big_jump_ms its own
CF_THUMB = {'thumb': 1, 'ticks_turn': 200, 'ticks_album': 200, 'fast_at': 1000, 'fast_full': 1000, 'top_speed': 100, 'track': 1000,
            'snap': 1000, 'spin': 1000, 'far_left': 2000, 'detent': 100, 'hold_ms': 30000, 'stop_ms': 1000, 'stop_left': 500,
            'rate_up': 1000, 'rate_down': 1000, 'back': 500, 'dead': 20, 'contact_ms': 200, 'aim_snap': 100, 'lead_from': 500,
            'lead_full': 1000, 'lead_ms': 1000, 'row_shift': 200, 'row_from': 1000, 'row_full': 2000, 'row_shrink': 50, 'row_flat': 800,
            'row_out': 100, 'row_edge': 800, 'row_width': 100, 'row_key': 100,
            'jump_ms': 5000, 'jump_ticks': 200, 'jump_at': 1000, 'jump_pace': 2000, 'big_jump_at': 1000, 'big_jump_ms': 5000,
            'enter_albums': 12, 'slide_ms': 1000, 'fold_near': 50,
            'arch_at': 100, 'arch_full': 100, 'arch_span': 700, 'arch_in': 1000, 'arch_out': 1000, 'arch_tau': 1000}
CF_WIDE = 2 * int(need(re.search(r'#define CF_WIDE_REACH (\d+)', (ROOT/'patch/coverflow.h').read_text()),
                       'Missing CF_WIDE_REACH')[1]) + 1  # the row's window
# AWTK's easing_type_t, in enum order: a value's index is its number
EASINGS = ('linear', 'quadratic_in', 'quadratic_out', 'quadratic_inout', 'cubic_in', 'cubic_out', 'sin_in', 'sin_out', 'sin_inout',
           'pow_in', 'pow_out', 'pow_inout', 'circular_in', 'circular_out', 'circular_inout', 'elastic_in', 'elastic_out', 'elastic_inout',
           'back_in', 'back_out', 'back_inout', 'bounce_in', 'bounce_out', 'bounce_inout', 'follow')  # follow: ours, EASE_FOLLOW
ANIMS = ('htranslate', 'vtranslate', 'fade', 'center_scale', 'popup', 'popdown', 'slide_up', 'slide_down', 'slide_left', 'slide_right')
# theme.h's MOTION_* order: what a window animates on load or open, what a widget glide takes
MOTION_WINDOW = ('page', 'playing', 'coverflow')
MOTION_WIDGET = ('inplace', 'swap', 'step', 'step_fast', 'coverflow_step', 'coverflow_step_fast')


@dataclasses.dataclass(frozen=True)
class Key:
    path: str
    field: str
    type: str
    lo: int
    hi: int
    default: object
    cls: str  # 'live' | 'open'
    final: bool = False  # drawn as stated: each half its own value (light.<key>, dark.<key>)


def key(path, field, type, hi=None, final=False):
    hi = {'bool': 1, 'color': 0xffffff, 'enum': len(CHOICES.get(path, ())) - 1}.get(type, hi)
    return Key(path, field, type, 0, hi, 0, 'live', final)


# theme_t declaration order; the scheme.* keys are scheme_t's.
SCHEMA = {k.path: k for k in (
    # the selection bar on a list row: its ends' radius, at most half the row (SEL_MAX_RADIUS: always the pill), and
    # its inset from each side of the row
    Key('list.selection.radius', 'sel_radius', 'int', 0, O['SEL_MAX_RADIUS'], O['SEL_MAX_RADIUS'], 'live'),
    Key('list.selection.inset', 'sel_inset', 'int', 0, 64, O['SEL_INSET'], 'live'),
    key('list.marquee.fade', 'marquee_fade', 'bool'),  # a scrolling label's ends fade into what is behind it (list_marks.c)
    key('list.reopen.top', 'reopen_top', 'bool'),  # a list left by Return opens at its top when chosen again (navigation.c forget_left)
    key('list.shuffle.row', 'shuffle_row', 'bool'),  # Songs' action row reads Shuffle and takes the wheel's selection (navigation.c shuffle_key)
    key('home.lists.full', 'home_lists_full', 'bool'),  # Split's Settings, Library and Streaming lists take the whole width, the art hidden (coverflow.c sub_full)
    key('list.entry.ipod', 'entry_ipod', 'bool'),  # text entry the iPod's way: Search's letter strip (search_strip.c), the keyboard walked by the wheel (keyboard.c)
    key('playing.art.radius', 'np_art_radius', 'int', 75),  # Now Playing's art corners in px; 0: NP_ART_RADIUS's share of its size
    key('playing.modes.icons', 'np_modes', 'bool'),  # shuffle and repeat as the theme's own pair (np_bar.c np_modes), heart and More its own glyphs (np_glyphs)
    key('home.chevron.visible', 'chevron_home', 'bool'),
    key('list.art.x', 'art_x', 'int', 375),  # a row with art, on screen: the cover, 0: stock's layout
    key('list.art.text_x', 'art_text_x', 'int', 375),  # ... its text's first and last columns
    key('list.art.text_end', 'art_text_end', 'int', 375),
    key('list.art.placeholder', 'art_placeholder', 'int', 51),
    key('list.art.radius', 'art_radius', 'int', 26),  # a row cover's corners on screen at the set's art size; 0: NP_ART_RADIUS's share  # stock's grey tiles (list_folder, list_song) this size, centred in their row; 0: stock's
    key('list.row.tall_font', 'font[ROLE_TALL]', 'enum'),  # a two-line row's title
    key('list.row.tall_px', 'font_px[ROLE_TALL]', 'int', 64),
    key('playing.artist.font', 'np_meta_font', 'enum'),  # Now Playing's artist and album, at the asset's size; 0: the style's
    key('list.icons.mono', 'icons_mono', 'bool'),
    key('list.icons.badge_muted', 'icons_badge_muted', 'bool'),
    key('list.icons.addon_mono', 'icons_addon_mono', 'bool'),
    key('playing.title.font', 'font[ROLE_NP]', 'enum'),
    key('playing.title.font_px', 'font_px[ROLE_NP]', 'int', 64),
    key('playing.info.font', 'font[ROLE_INFO]', 'enum'),  # small text: the QR pages, the Time Zone caption, page notes
    key('playing.info.font_size', 'font_px[ROLE_INFO]', 'int', 64),
    key('playing.info.value', 'np_info_value', 'color', final=True),  # Now Playing's info rows' values, right of their names
    key('playing.album.opacity', 'np_album_op', 'int', 255),
    key('playing.state.opacity', 'np_state_op', 'int', 255),
    key('home.rows.top', 'home_top', 'int', 375),
    key('home.rows.full_row', 'home_full_row', 'int', 375),
    key('home.rows.text_x', 'home_text_x', 'int', 375),
    key('home.rows.label_end_split', 'home_label_end[0]', 'int', 375),
    key('home.rows.label_end_full', 'home_label_end[1]', 'int', 375),
    key('home.rows.font', 'font[ROLE_HOME]', 'enum'),
    key('home.rows.font_px', 'font_px[ROLE_HOME]', 'int', 64),
    key('settings.rows.top', 'set_top', 'int', 375),
    key('settings.values.text', 'set_text', 'bool'),
    key('settings.values.switch', 'set_switch', 'bool'),  # an on/off row's switch, drawn (list_marks.c switch_mark) where set_text would write On or Off
    key('settings.values.ink', 'set_value_ink', 'color', final=True),  # a set_text row's value, right-aligned; 0: the label style's white
    key('pages.own.art_final', 'art_final', 'bool'),  # the theme's own art (art.py's <art>_ images) is final
    key('playing.scrubber.height', 'np_bar', 'int', 375),
    key('playing.scrubber.scrub_height', 'np_scrub', 'int', 375),
    # the progress bar's track (np_theme), the volume's over it and Spotify's
    Key('playing.scrubber.track', 'np_track_rgb', 'color', 0, 0xffffff, O['TRACK_COLOR'], 'live'),
    key('list.row.font', 'font[ROLE_TITLE]', 'enum'),
    key('list.row.meta_font', 'font[ROLE_META]', 'enum'),
    key('list.row.title_px', 'font_px[ROLE_TITLE]', 'int', 64),
    key('list.row.meta_px', 'font_px[ROLE_META]', 'int', 64),
    key('bar.title.font', 'font[ROLE_BAR]', 'enum'),
    key('bar.title.font_size', 'font_px[ROLE_BAR]', 'int', 64),
    *(key(f'list.scrollbar.{a}', f'sb_{a}', 'int', 375) for a in ('w', 'right', 'top', 'bottom', 'min')),  # list_marks.c's track and thumb, while the wheel turns
    key('list.scrollbar.thumb', 'sb_thumb', 'color', final=True),
    key('list.scrollbar.track', 'sb_track', 'color', final=True),
    key('scheme.chevron', 'chevron', 'color'),
    key('scheme.muted', 'muted', 'color'),
    key('scheme.sel.text', 'sel_text', 'color', final=True),
    key('scheme.mode', 'mode', 'enum'),
    # the light half's flip ends; the dark half's are fixed, #000000 and #ffffff
    Key('scheme.paper', 'paper', 'color', 0, 0xffffff, O['LIGHT_PAPER'] * 0x010101, 'live'),
    Key('scheme.ink', 'ink', 'color', 0, 0xffffff, O['LIGHT_INK'] * 0x010101, 'live'),
    *(Key(f'coverflow.{e}.{a}', f'cf.{a}', 'int', lo, hi, 0, 'open') for e, a, lo, hi in (
        ('shelf', 'top', 0, 50), ('shelf', 'x1', 0, 1023), ('shelf', 'xs', 0, 799), ('shelf', 'z1', 0, 799),
        ('shelf', 'zs', 0, 799), ('shelf', 'eye', 1, 2046), ('shelf', 'bright1', 1, 256), ('shelf', 'bright2', 1, 256),
        ('shelf', 'reflect', 1, 50), ('shelf', 'reflect_top', 0, 256), ('shelf', 'stride', 1, 375),
        ('shelf', 'radius', 0, 83), ('shelf', 'tilt', 30, 90), ('shelf', 'horizon', 1, 99), ('shelf', 'third', 0, 1),
        ('shelf', 'swing', 0, 2), ('shelf', 'lift', 0, 40), ('shelf', 'eye_turn', 0, 400), ('shelf', 'view_h', O['CF_VIEW_H'], 294),
        *(('shelf', a, 0, hi) for a, hi in CF_THUMB.items()), ('caption', 'name_px', 1, 32), ('caption', 'artist_px', 1, 28),
        ('caption', 'text_y', 170, 290), ('caption', 'artist_y', 20, 32))),
    # % of the plane's own spacing of a cover's art, from even (0); 100, the plane's, the default
    Key('coverflow.shelf.art_persp', 'cf.art_persp', 'int', 0, 100, 100, 'open'),
    Key('page.rows.height', 'page_row', 'int', 20, 64, 48, 'open'),
    Key('page.rows.detail_height', 'page_detail', 'int', 20, 80, 64, 'open'),
    Key('page.title.height', 'page_title_h', 'int', 0, 48, 48, 'open'),
    Key('page.rows.text_x', 'page_x', 'int', 0, 64, 36, 'open'),  # CF_EDGE
    # a detail row's two labels, each box at least its font's px (a label draws nothing in a shorter one)
    Key('page.rows.title_y', 'page_cap_y', 'int', -4, 16, 4, 'open'),
    Key('page.rows.title_h', 'page_cap_h', 'int', 16, 48, 32, 'open'),
    Key('page.rows.detail_y', 'page_det_y', 'int', 16, 60, 36, 'open'),
    Key('page.rows.detail_h', 'page_det_h', 'int', 12, 40, 24, 'open'),
    *(Key(f'motion.{k}.{a}', f'motion.{k}.{a}', t, 0, hi, 0, 'motion')
      for k in (*MOTION_WINDOW, *MOTION_WIDGET) for a, t, hi in (('ms', 'int', 2000), ('easing', 'easing', len(EASINGS) - 1))),
    *(Key(f'motion.{k}.anim', f'motion.{k}.anim', 'anim', 0, len(ANIMS) - 1, 0, 'motion') for k in MOTION_WINDOW),
    Key('motion.recall.ms', 'recall_ms', 'int', 0, 2000, 0, 'motion'))}
FIELDS = {k.field: k for k in SCHEMA.values()}


def view_attr(path):
    """Whether a SCHEMA path is a view element's attribute (list.selection.radius), not a scheme role
    (scheme.sel.text)."""
    return path.count('.') == 2 and not path.startswith('scheme.')


ELEMENTS = {p.rpartition('.')[0] for p in SCHEMA if view_attr(p)}
VIEWS = {e.partition('.')[0] for e in ELEMENTS}
CHILDREN = {'theme': {'colorSchemes', 'colorScheme', 'geometry', 'option', 'view'}, 'view': {'element'}, 'option': {'value'}}
OPTION_ATTRS = {'setting', 'key', 'label', 'default', 'preset', 'keep', 'values'}
# Per-theme numbers stay in offsets.inc: theme id -> {C field: expression over O}.
CF_ROLES = {'cf.top': 'CF_TOP',
            'cf.bright1': 'CF_BRIGHT1', 'cf.bright2': 'CF_BRIGHT2', 'cf.reflect': 'CF_REFLECT',
            'cf.reflect_top': 'CF_REFLECT_TOP', 'cf.stride': 'CF_STRIDE', 'cf.radius': 'NP_ART_RADIUS',
            'cf.third': 'CF_THIRD', 'cf.view_h': 'CF_VIEW_H',
            'cf.name_px': 'CF_NAME_PX', 'cf.artist_px': 'CF_ARTIST_PX', 'cf.text_y': 'CF_TEXT_Y', 'cf.artist_y': 'CF_NAME_H'}
MOTION_ROLES = {'motion.page.ms': 'PAGE_SLIDE_MS', 'motion.page.easing': 'EASE_CUBIC_OUT',
                'motion.swap.ms': 'PAGE_SLIDE_MS', 'motion.swap.easing': 'SLIDE_EASING',
                'motion.step.ms': 'HOME_SLIDE_MS', 'motion.step.easing': 'SLIDE_EASING',
                'motion.step_fast.ms': 'HOME_FAST_SLIDE_MS', 'motion.step_fast.easing': 'SLIDE_EASING',
                'motion.coverflow_step.ms': 'HOME_SLIDE_MS', 'motion.coverflow_step.easing': 'SLIDE_EASING',
                'motion.coverflow_step_fast.ms': 'HOME_FAST_SLIDE_MS', 'motion.coverflow_step_fast.easing': 'SLIDE_EASING',
                'recall_ms': 'GLIDE_MS'}
# OLED's macros cover the fields its theme.xml leaves to offsets.inc: the rest it states itself, and a macro, applied
# after the file (resolve), would override it.
ROLE_MACROS = {
    'oled': {'home_label_end[1]': 'HOME_LABEL_END', 'chevron': 'MINIMAL_CHEVRON',
             **{k: v for k, v in CF_ROLES.items() if k not in ('cf.reflect_top', 'cf.name_px', 'cf.artist_px', 'cf.text_y', 'cf.artist_y')},
             **MOTION_ROLES},
}

# Geometry field -> its inclusive (lo, hi), each an int or a function of the finished set.
GEOM_RANGES = {
    # 26: Home's scratch frame (DRIFT_TEXELS, 4 x 188 x 294) holds a client of 294 at most; 40: an eighth of the screen,
    # or 58 under a status row (status_y), whose bar is the page's black above its first row
    'bar': (26, lambda g: 58 if g.status_y else 40),
    'pitch': (28, 78),  # 28: the smallest row proved to fill the stock row pool at h 294; 78: stock's own row
    'body': (24, lambda g: g.pitch),  # inside its row, and tall enough for stock's 24 px title box
    'art': (0, lambda g: min(52, g.art_body)),  # stock covers are 52 px, drawn 1:1 or smaller; 0 hides them
    'art_inset': (0, lambda g: (g.art_body - g.art) // 2),  # the art and its insets fit the body
    'art_pitch': (28, 78),
    'art_body': (24, lambda g: g.art_pitch),
    'text_art': (0, lambda g: min(52, g.body)),
    'text_art_inset': (0, lambda g: (g.body - g.text_art) // 2),
    'lib_pitch': (28, 78),  # the Library's own rows
    'lib_body': (24, lambda g: g.lib_pitch),
    'lib_art': (0, lambda g: min(52, g.lib_body)),
    'lib_inset': (0, lambda g: (g.lib_body - g.lib_art) // 2),
    'title24_y': (0, lambda g: g.body - 24),  # stock's 24 px label box inside the body
    'title_y': (0, lambda g: g.body - 20),  # the 20 px box
    'meta_y': (0, lambda g: g.body - 16),  # the 16 px boxes
    'count_y': (0, lambda g: g.body - 16),
    'art_title24_y': (0, lambda g: g.art_body - 24),
    'art_meta_y': (0, lambda g: g.art_body - 16),
    'folder_pitch': (lambda g: 28 if g.folder_pitch else 0, 78),  # Folder view's own rows; 0 takes art_pitch (ipod.folder_pitch)
    'folder_body': (0, lambda g: g.folder_pitch or g.art_pitch),
    'song_pitch': (28, 78),  # Songs' own rows
    'song_body': (24, lambda g: g.song_pitch),
    'song_title24_y': (0, lambda g: g.song_body - 24),
    'song_meta_y': (0, lambda g: g.song_body - 16),
    'playlist_pitch': (lambda g: 28 if g.playlist_pitch else 0, 78),  # Playlists' own rows; 0 takes song_pitch (ipod.page_rows)
    'set_row': (28, 78),
    'set_top': (0, 40),  # a short header at most under the bar
    'set_rows': (1, lambda g: (320 - g.bar - g.set_top) // g.set_row),  # the settings list fits the client
    'set_icon': (0, lambda g: g.set_row),
    'stream_row': (28, 78),
    'bar_px': (0, lambda g: g.bar),  # the bar title's size inside the bar; 0: no title
    'batt_w': (1, 40),
    'batt_h': (1, lambda g: g.bar - 2),  # inside the bar, clear of its line row
    'text_x': (0, 60),  # the glass rule stays test/build.py's
    'action_row': (0, 50),
    'list_end': (0, lambda g: 320 - g.bar),  # ... and list_end's, inside the window
    'home_lib': (0, 1),  # Home's Library row slides in a list of the Library's rows
    'home_row': (28, lambda g: (320 - g.bar - O['HOME_TOP']) // O['HOME_REST']),  # the rows Home rests on under HOME_TOP fit the client
    'stock_row': (28, 78),
    'stock_body': (24, lambda g: g.stock_row),
    'stock_title_y': (0, lambda g: g.stock_body - 20),
    'stock_title_h': (20, lambda g: g.stock_body),
    'search_row': (28, 78),
    'search_body': (24, lambda g: g.search_row),
    'search_art': (0, 50),
    'search_title_y': (0, lambda g: g.search_body - g.search_title_h),
    'search_title24_y': (0, lambda g: g.search_body - g.search_title_h),
    'search_title_h': (20, lambda g: g.search_body),
    'search_meta_y': (0, lambda g: g.search_body - g.search_meta_h),
    'search_meta_h': (16, lambda g: g.search_body),
    'status_y': (0, lambda g: g.bar - 12),  # its glyphs (12 px) inside the bar
    'right_x': (0, 60),
    'np_art': (0, 200),  # Now Playing on the grid
    'np_bar_y': (0, lambda g: 320 - 56 if g.np_art else 0),  # its bar, with the times and the state row under it, on screen
    # Split: Home's rows never wider than the screen's half and the pane's overlap; the pane no wider than
    # HOME_SPLIT_W leaves (home.ring, DRIFT_TEXELS), and no taller than the screen: up to the bar's whole height, the
    # status row's glyphs then over the art, inked by contrast (coverflow.c home_ink)
    'split_row': (120, lambda g: g.split_art + 16),
    'split_art': (O['HOME_SPLIT_W'], 260),
    'split_art_up': (0, lambda g: g.bar),
    'split_inset': (0, 4),  # percent of the zoomed cover; past about 4.5 the 110 % zoom leaves no pan
}
THEME_MOTION_OFF = 4  # offsetof(theme_t, motion), right after three flags; asserted in theme_engine.c; test/patch.py pokes it
SCHEME_MODE_OFF = 4  # offsetof(scheme_t, mode) on MIPS, right after tones; asserted in theme_engine.c; test/patch.py pokes it


def check_set(g):
    """The first field of g, in Geometry order, outside its range: its message; else None."""
    for f in dataclasses.fields(Geometry)[1:]:
        v = getattr(g, f.name)
        lo, hi = (b(g) if callable(b) else b for b in GEOM_RANGES[f.name])
        if not lo <= v <= hi:
            return f'geometry.{f.name}: {v} outside {lo}..{hi}'
    return None


def declare(node, base, name):
    """A theme's <geometry>: its fields over base's (the extended theme's grid, or STOCK_GRID, stock's), checked, as a
    Geometry named name."""
    def err(message): fail(node.file, node.line, message)
    ints = {}
    for k, text in node.attrs.items():
        if k not in GEOM_RANGES:
            err(f'unknown key geometry.{k}')
        if not re.fullmatch('-?[0-9]+', text):
            err(f'geometry.{k}: expected an integer')
        ints[k] = int(text)
    g = dataclasses.replace(base, name=name, **ints)
    message = check_set(g)
    if message:
        err(message)
    return g


def geometry(t):
    """Theme t's grid, its <geometry>: the one tools/ipod.py bakes into the build."""
    return t['geom']


def built_geometry(themes):
    """The grid the build bakes into stock's immediates and pages: the themes' one grid."""
    found = {geometry(t) for t in themes}
    check(len(found) == 1, f'{len(found)} grids: the build bakes one')
    g = found.pop()
    for n, x in immediate_values(g).items():
        check(0 <= x < 0x8000, f'{g.name}: {n} {x} does not fit an immediate')
    return g


class ThemeError(Exception):
    pass


def fail(file, line, message) -> NoReturn:
    raise ThemeError(f'{file}:{line}: {message}')


@dataclasses.dataclass
class Node:
    tag: str
    attrs: dict
    children: list
    line: int
    file: str


# A strict XML subset: comments, elements with double-quoted lowercase attributes, and whitespace.
# WS, not \s, which also accepts \xa0 and other characters XML calls text.
WS = '[ \t\r\n]'
# Values exclude controls (etree folds \t\r\n to spaces) and comments '--' (etree rejects it).
TOKEN = re.compile(rf'<!--(?:(?!--).)*-->|<(/?)([A-Za-z][A-Za-z0-9]*)'
                   rf'((?:{WS}+[a-z0-9_.]+="[^"<\x00-\x1f]*")*){WS}*(/?)>|{WS}+', re.S)
ENTITIES = {'amp': '&', 'lt': '<', 'gt': '>', 'quot': '"'}


def tokenize(text, file):
    """(kind, tag, attrs, line) for each tag, kind 'open', 'empty' or 'close'."""
    tokens, pos = [], 0
    while pos < len(text):
        line = text.count('\n', 0, pos) + 1
        m = TOKEN.match(text, pos)
        if not m:
            rest = text[pos:]
            fail(file, line, 'unclosed comment' if rest.startswith('<!--') else
                 'malformed tag' if rest.startswith('<') else 'text between tags')
        pos = m.end()
        if m[2] is None:
            continue  # a comment or whitespace
        if m[1] and (m[3] or m[4]):
            fail(file, line, 'malformed tag')
        attrs = {}
        for name, value in re.findall(r'([a-z0-9_.]+)="([^"]*)"', m[3]):
            if name in attrs:
                fail(file, line, f'duplicate attribute {name}')
            if re.search(r'&(?!(?:amp|lt|gt|quot);)', value):
                fail(file, line, 'unknown entity')
            attrs[name] = re.sub(r'&(amp|lt|gt|quot);', lambda e: ENTITIES[e[1]], value)
        tokens.append(('close' if m[1] else 'empty' if m[4] else 'open', m[2], attrs, line))
    return tokens


def parse(text, file):
    """The root <theme format="1"> Node of a theme file's text."""
    root, stack = None, []
    for kind, tag, attrs, line in tokenize(text, file):
        if root and not stack:
            fail(file, line, 'more than one root element')
        if kind == 'close':
            if not stack or stack[-1].tag != tag:
                fail(file, line, f'mismatched </{tag}>')
            stack.pop()
            continue
        node = Node(tag, attrs, [], line, file)
        if root:
            stack[-1].children.append(node)
        elif tag != 'theme' or attrs.get('format') != '1':
            fail(file, line, 'root must be <theme format="1">')
        else:
            root = node
        if kind == 'open':
            stack.append(node)
    if stack:
        fail(file, stack[-1].line, f'unclosed <{stack[-1].tag}>')
    if not root:
        fail(file, 1, 'root must be <theme format="1">')
    return root


def load(path):
    """parse() a theme.xml, named <id>/theme.xml in errors."""
    file = f'{path.parent.name}/{path.name}'
    data = path.read_bytes()
    if len(data) > 32768:
        fail(file, 1, 'larger than 32 KB')
    try:
        text = data.decode()
    except UnicodeDecodeError:
        fail(file, 1, 'not UTF-8')
    return parse(text, file)


def zero_scheme():
    return {**{k.field: k.default for k in SCHEMA.values() if k.path.startswith('scheme.')}, 'light': {}}


def split_half(attr):
    """'light.sel.top' -> ('light', 'sel.top'); 'dark.x' -> ('dark', 'x'); 'x' -> (None, 'x')."""
    h, dot, rest = attr.partition('.')
    return (h, rest) if dot and h in ('light', 'dark') else (None, attr)


def flip(c, paper, ink):
    """theme_engine.c's flip(): a neutral (channels within LIGHT_SPREAD) per channel paper - v * (paper - ink) // 255
    on the low bytes of paper and ink; else c."""
    ch = [c >> s & 255 for s in (0, 8, 16)]
    if max(ch) - min(ch) > O['LIGHT_SPREAD']:
        return c
    p, i = paper & 255, ink & 255
    return sum((p - v * (p - i) // 255) << s for v, s in zip(ch, (0, 8, 16)))


def unflip(c, paper, ink):
    """flip's approximate inverse: a neutral per channel (paper - v) * 255 // (paper - ink), clamped to 0..255; else c."""
    ch = [c >> s & 255 for s in (0, 8, 16)]
    if max(ch) - min(ch) > O['LIGHT_SPREAD']:
        return c
    p, i = paper & 255, ink & 255
    return sum(min(255, max(0, (p - v) * 255 // (p - i))) << s for v, s in zip(ch, (0, 8, 16)))


FINAL = [k for k in SCHEMA.values() if k.final]

# The design's colour tokens (docs/oled-design.md#color), 0xRRGGBB final per half: (dark, light). A light of None is
# the dark's flip by OLED's light paper and ink (TOKEN_PAPER, assets/themes/oled/theme.xml), the colour flip_if gives. patch/tokens.h holds them for C as TOK_<NAME>
# (ui_kit.c tok's index), RGB_<NAME> and RGB_<NAME>_L (write_tokens writes it, test/unit/test_theme.py checks it is
# current); tools/art.py inks its glyphs from them.
TOKENS = {'canvas': (0x000000, 0xffffff), 'primary': (0xffffff, 0x000000), 'secondary': (0xababab, 0x5c5c5c),
          'tertiary': (0x858585, 0x6e6e6e), 'chevron': (0x999999, 0x8a8a8a), 'track': (0x333333, 0xd9d9d9),
          'on_accent': (0xffffff, 0xffffff), 'caption_on_accent': (0xd6e2fa, 0xd6e2fa),
          'raised': (0x1c1c1e, None), 'raised_2': (0x2c2c2e, None), 'off': (0x3a3a3c, None),
          'positive': (0x34c759, 0x34c759), 'warning': (0xff9f0a, 0xff9f0a), 'negative': (0xff453a, 0xff453a),
          # a song liked: Now Playing's heart filled (tools/art.py np_heart), iOS's dark systemPink, not Negative's red,
          # which means a failure; Light Mode keeps the hue (the user's, 2026-11-19)
          'favorite': (0xff375f, 0xff375f),
          # text in the accent (an action row's words, a link under a button): the accent lifted for 4.5:1 on black
          # (design/mockups/action-row-blue.png), and the accent itself on white (5.6:1)
          'accent_text': (0x4a8bff, 0x2160d8)}


TOKEN_PAPER = (0xffffff, 0x000000)


def token(name, light=False):
    """TOKENS[name]'s colour in one half."""
    dark, lit = TOKENS[name]
    return dark if not light else lit if lit is not None else flip(dark, *TOKEN_PAPER)


def token_header():
    """patch/tokens.h: TOKENS as the enum, each half's macro and the table's initialiser (ui_kit.c's)."""
    names = [n.upper() for n in TOKENS]
    return '\n'.join(['/* tools/theme.py write_tokens wrote this from its TOKENS; edit those, then run it again. */',
                      'enum { ' + ', '.join(f'TOK_{n}' for n in names) + ', TOK_N };',
                      *(f'#define RGB_{n} {token(k):#08x}' for n, k in zip(names, TOKENS)),
                      *(f'#define RGB_{n}_L {token(k, True):#08x}' for n, k in zip(names, TOKENS)),
                      '#define UI_TOKENS {' + ', '.join(f'{{RGB_{n}, RGB_{n}_L}}' for n in names) + '}', ''])


def write_tokens(path=ROOT/'patch/tokens.h'):
    """Write token_header() to path."""
    path.write_text(token_header())


def half(d, light, paper, ink):
    """d's final fields in one half: the stated value, else the other half's flipped (light) or unflipped
    (dark); unset stays unset; 'light' dropped."""
    out = {f: v for f, v in d.items() if f != 'light'}
    f_ = flip if light else unflip
    for k in FINAL:
        if k.field not in d:
            continue
        v = d['light'].get(k.field) if light else d[k.field] or None
        if v is None:  # unstated: the other half's, flipped; unset (0, ()) stays unset
            o = d[k.field] if light else d['light'].get(k.field, k.default)
            v = o and f_(o, paper, ink)
        out[k.field] = v
    return out


def scheme_half(s, light):
    """half(s, light, s['paper'], s['ink']), paper and ink the half's."""
    return {**half(s, light, s['paper'], s['ink']), **({} if light else {'paper': 0, 'ink': 0xffffff})}


def shared(t):
    """No scheme states a final value in either half: one array serves both."""
    return not any(s['light'] or any(s[k.field] for k in FINAL if k.path.startswith('scheme.')) for s in t['schemes'])


def check_tags(node):
    for c in node.children:
        if c.tag not in CHILDREN.get(node.tag, ()):
            fail(c.file, c.line, f'unknown element <{c.tag}>')
        check_tags(c)


def ranged(node, k, v):
    if not k.lo <= v <= k.hi:
        fail(node.file, node.line, f'{k.path}: {v} outside {k.lo}..{k.hi}')
    return v


def value(node, path, text, macros):
    """SCHEMA[path]'s value for an attribute's text."""
    def err(message) -> NoReturn: fail(node.file, node.line, message)
    k = SCHEMA.get(path)
    if not k:
        err(f'unknown key {path}')
    # ponytail: a final colour's 0 means unset (theme.h), so it has no black; a role needs #010101 until one wants a flag
    if k in FINAL and k.type == 'color' and text.lower() == '#000000':
        err(f'{path}: #000000 reads as unset; use #010101')
    if k.field in macros:
        err(f'{path} comes from patch/offsets.inc')
    if k.type == 'bool':
        if text not in ('0', '1'):
            err(f'{path}: expected 0 or 1')
        v = int(text)
    elif k.type == 'enum':
        names = list(CHOICES[path])
        if text not in names:
            err(f'{path}: unknown value {text}')
        v = names.index(text)
    elif k.type in ('easing', 'anim'):
        names = EASINGS if k.type == 'easing' else ANIMS
        if text not in names:
            err(f'{path}: {text} is not one of {", ".join(names)}')
        v = names.index(text)
    elif k.type == 'color':
        if not re.fullmatch('#[0-9a-fA-F]{6}', text):
            err(f'{path}: expected #rrggbb')
        v = int(text[1:], 16)
    else:
        if not re.fullmatch('-?[0-9]+', text):
            err(f'{path}: expected an integer')
        v = int(text)
    return ranged(node, k, v)


def own_key(node, key):
    """A theme's own IPOD key: never the payload's."""
    if not re.fullmatch('[A-Z][A-Z0-9_]*', key):
        fail(node.file, node.line, 'option.key: expected [A-Z][A-Z0-9_]*')
    if key in PAYLOAD_KEYS:
        fail(node.file, node.line, f"{key}: the payload's key")
    return key


def label_ok(text):  # a row's text: 1..24 bytes, no control characters
    return 0 < len(text.encode()) <= 24 and not re.search('[\x00-\x1f\x7f-\x9f]', text)


def presets(c, o, names, what):
    """o's def, preset and keep: indices into names of c's default, preset and keep, -1 when absent."""
    for attr, field in (('default', 'def'), ('preset', 'preset'), ('keep', 'keep')):
        if attr in c.attrs:
            if c.attrs[attr] not in names:
                fail(c.file, c.line, f'{what}: unknown value {c.attrs[attr]}')
            o[field] = names.index(c.attrs[attr])
    return o


def offered(c, o, names, what):
    """c's values, the bitmask of names its row takes. A stored value outside them reads as its default, so it
    has one, and every value the option names is one of them."""
    listed = c.attrs['values'].split()
    if len(set(listed)) < 2:
        fail(c.file, c.line, f'{what}: 2 or more values expected')
    for n in listed:
        if n not in names:
            fail(c.file, c.line, f'{what}: unknown value {n}')
    if o['def'] < 0:
        fail(c.file, c.line, f'{what}: values need a default, what a stored value outside them reads as')
    for attr, field in (('default', 'def'), ('preset', 'preset'), ('keep', 'keep')):
        if o[field] >= 0 and names[o[field]] not in listed:
            fail(c.file, c.line, f'{what}: {attr} {names[o[field]]} is not in values')
    return sum(1 << names.index(n) for n in set(listed))


PLAIN_KEYS = {'SONGARTIST', 'SONGLENGTH', 'NPTIME'}  # the plain own keys the payload reads (theme_engine.c song_artist, song_length, np_time_total)


def own_option(c):
    """A theme's own <option key label>: a plain choice the payload reads as st.own[OWN_<KEY>]."""
    def err(node, message): fail(node.file, node.line, message)
    key = own_key(c, c.attrs['key'])
    if 'values' in c.attrs:
        err(c, f'{key}: its <value>s are its values')
    if not label_ok(c.attrs.get('label', '')):
        err(c, f'{key}: label expected 1..24 bytes, no control characters')
    names = []
    for v in c.children:
        for k in v.attrs:
            if k != 'label':
                err(v, f'unknown key value.{k}')
        x = v.attrs.get('label', '')
        if not label_ok(x):
            err(v, f'{key}: value label expected 1..24 bytes, no control characters')
        if x in names:
            err(v, f'{key}: duplicate value {x}')
        names.append(x)
    # Ten is this format's own bound, not a reader's: config_value reads up to three digits and write_int_config
    # writes any. Past 32, setting_click's value mask (32 bits) could not reach a value.
    if not 2 <= len(names) <= 10:
        err(c, f'{key}: 2 to 10 values expected')
    if key not in PLAIN_KEYS:
        err(c, f'{key}: a plain choice the payload does not read (PLAIN_KEYS)')
    # ponytail: a value that selects a scheme or a font adds its attribute, and its payload reader, when a theme needs it
    return presets(c, {'setting': 'SETTING_OWN', 'key': key, 'label': c.attrs['label'], 'names': names,
                       'def': -1, 'preset': -1, 'keep': -1, 'values': 0}, names, key)


def scheme_names(t):
    """The names accent values index: the ACCENTS rows, then the theme's own schemes."""
    return ACCENT_NAMES + [s['name'] for s in t['schemes'][len(ACCENT_NAMES):]]


def resolve(root, by_id, seen=()):
    """C field -> value for one theme: the defaults or its base, then its body, then ROLE_MACROS."""
    def err(node, message): fail(node.file, node.line, message)
    a = root.attrs
    for k in a:
        if k not in ('format', 'id', 'name', 'index', 'extends', 'art'):
            err(root, f'unknown key theme.{k}')
    tid = a.get('id', '')
    if not re.fullmatch('[a-z][a-z0-9_-]*', tid):
        err(root, 'id: expected [a-z][a-z0-9_-]*')
    # a name is for people reading the file and the errors: short, no control characters
    if not 0 < len(a.get('name', '').encode()) <= 48 or re.search('[\x00-\x1f\x7f-\x9f]', a['name']):
        err(root, 'name: expected 1..48 bytes, no control characters')
    if not re.fullmatch('[0-9]', a.get('index', '')):
        err(root, 'index: expected a digit')
    if tid in seen:
        err(root, f'extends cycle at {tid}')
    check_tags(root)
    if 'extends' in a:
        if a['extends'] not in by_id:
            err(root, f'extends unknown theme {a["extends"]}')
        t = resolve(by_id[a['extends']], by_id, seen + (tid,))  # a fresh dict: ours to change
    else:
        t = {'accents': ALL_ACCENTS, 'accent_key': None, 'schemes': [zero_scheme() for _ in ACCENT_NAMES], 'opts': [], 'geom': STOCK_GRID,
             **{k.field: k.default for k in SCHEMA.values() if not k.path.startswith('scheme.')}, 'light': {}}
    t.update(id=tid, name=a['name'], index=int(a['index']), line=root.line)
    # art: the theme whose generated images (tools/art.py <art_id>_*) this one draws, as the payload loads them by name
    if a.get('art', tid) not in (*by_id, tid):
        err(root, f'art: unknown theme {a["art"]}')
    t['art_id'] = a.get('art', tid)
    macros = ROLE_MACROS.get(tid, {})
    opts, geom_seen = None, False
    named = []
    for c in root.children:
        if c.tag == 'colorSchemes':
            t['accents'] = 0
            for k in c.attrs:
                if k != 'accents':
                    err(c, f'unknown key colorSchemes.{k}')
            names = scheme_names(t)
            for n in c.attrs.get('accents', '').split():
                if n not in names:
                    err(c, f'unknown scheme {n}')
                t['accents'] |= 1 << names.index(n)
        elif c.tag == 'colorScheme':
            n = c.attrs.get('name')  # none: every scheme the theme has
            own = n is not None and 'base' in c.attrs  # a scheme of the theme's own, after the ACCENTS rows
            if own:
                label = c.attrs.get('label', '')
                if n in ACCENT_NAMES:
                    err(c, f'{n}: an ACCENTS row takes no base')
                if c.attrs['base'] not in ACCENT_NAMES:
                    err(c, f'{n}: unknown base {c.attrs["base"]}')
                if not label:
                    err(c, f'{n}: an own scheme needs a label')
                if len(label.encode()) > 48 or re.search('[\x00-\x1f\x7f-\x9f]', label):
                    err(c, f'{n}: label: expected 1..48 bytes, no control characters')
                if n not in scheme_names(t):
                    b = t['schemes'][ACCENT_NAMES.index(c.attrs['base'])]
                    t['schemes'].append(dict(b, name=n, label=label, base=ACCENT_NAMES.index(c.attrs['base']),
                                             light=dict(b['light'])))
                    t['accents'] |= 1 << len(t['schemes']) - 1
            elif 'tones' in c.attrs:
                err(c, f'{n or "colorScheme"}: tones are an own scheme\'s (name, base, label)')
            if own and 'tones' in c.attrs:  # its own ACCENTS-shaped row in place of the base's (emit's <id>_tones)
                tones = c.attrs['tones'].split()
                if len(tones) != len(ACCENT_TONES[0]) or not all(re.fullmatch('#[0-9a-fA-F]{6}', x) for x in tones):
                    err(c, f'{n}: tones: expected {len(ACCENT_TONES[0])} of #rrggbb (top, bottom, light, red, highlight)')
                t['schemes'][scheme_names(t).index(n)]['tones'] = tuple(int(x[1:], 16) for x in tones)
            names = scheme_names(t)
            if n is not None and n not in names:
                err(c, f'unknown scheme {n}')
            if n is not None:
                named.append((c, n))
            targets = t['schemes'] if n is None else [t['schemes'][names.index(n)]]
            for attr, text in c.attrs.items():
                if attr == 'name' or own and attr in ('base', 'label', 'tones'):
                    continue
                h, k = split_half(attr)
                path = f'scheme.{k}'
                x = value(c, path, text, macros)
                f = SCHEMA[path]
                if k in ('paper', 'ink'):  # the light half's flip ends; the dark half's are fixed
                    if h != 'light':
                        err(c, f"{path}: the dark half's is {'#000000' if k == 'paper' else '#ffffff'}; write light.{k}")
                elif h and not f.final:
                    err(c, f'{path}: one value for both halves')
                elif h == 'dark' and k in c.attrs:
                    err(c, f'{path}: stated twice ({k} and dark.{k})')
                for s in targets:
                    (s['light'] if h == 'light' and f.final else s)[f.field] = x
        elif c.tag == 'geometry':
            if geom_seen:
                err(c, 'one <geometry> per theme')
            geom_seen = True
            t['geom'] = declare(c, t['geom'], tid)
        elif c.tag == 'option':
            if opts is None:
                opts = t['opts'] = []  # this file's options replace the inherited list
            for k in c.attrs:
                if k not in OPTION_ATTRS:
                    err(c, f'unknown key option.{k}')
            if 'key' in c.attrs and 'setting' not in c.attrs:
                opts.append(own_option(c))
                continue
            s = c.attrs.get('setting', '')
            if s not in SETTINGS:
                err(c, f'unknown setting {s}')
            if c.children:
                err(c.children[0], f'{s}: a payload setting takes no <value>')
            if 'label' in c.attrs:  # its row's title is the payload's
                err(c, f'{s}: a payload setting takes no label')
            macro, names = SETTINGS[s]
            if s == 'accent':
                names = scheme_names(t)
            o = presets(c, {'setting': macro, 'def': -1, 'preset': -1, 'keep': -1, 'values': 0}, names, s)
            if 'values' in c.attrs:
                if s == 'accent':
                    err(c, 'accent: colorSchemes states its values')
                o['values'] = offered(c, o, names, s)
            if 'key' in c.attrs:
                if s != 'accent':
                    err(c, f'{s}: unknown key {c.attrs["key"]}')
                # ACCENT, else a theme-owned key (theme_keys)
                t['accent_key'] = c.attrs['key'] if c.attrs['key'] in ACCENT_KEYS else own_key(c, c.attrs['key'])
            opts.append(o)
        else:  # view
            v = c.attrs.get('name', '')
            if v not in VIEWS:
                err(c, f'unknown view {v}')
            for k, text in c.attrs.items():
                if k != 'name':
                    if f'{v}.{k}' not in SCHEMA:
                        err(c, f'unknown key view.{k}')
                    t[SCHEMA[f'{v}.{k}'].field] = value(c, f'{v}.{k}', text, macros)
            for e in c.children:
                el = f'{v}.{e.attrs.get("name", "")}'
                if el not in ELEMENTS:
                    err(e, f'unknown element {el}')
                for attr, text in e.attrs.items():
                    if attr != 'name':
                        h, k = split_half(attr)
                        x = value(e, f'{el}.{k}', text, macros)
                        f = SCHEMA[f'{el}.{k}']
                        if h and not f.final:
                            err(e, f'{el}.{k}: one value for both halves')
                        if h == 'dark' and k in e.attrs:
                            err(e, f'{el}.{k}: stated twice ({k} and dark.{k})')
                        (t['light'] if h == 'light' else t)[f.field] = x
    for c, n in named:
        if not t['accents'] >> scheme_names(t).index(n) & 1:
            err(c, f'scheme {n} is not in colorSchemes')
    for field, expr in macros.items():
        k = FIELDS[field]
        v = ranged(root, k, eval(expr, {'__builtins__': {}}, O))
        if k.path.startswith('scheme.'):
            for s in t['schemes']:
                s[field] = v
        else:
            t[field] = v
    t['schemes'] = [s if i >= len(ACCENT_NAMES) or t['accents'] >> i & 1 else zero_scheme() for i, s in enumerate(t['schemes'])]
    for o in t['opts']:
        if o['setting'] == 'SETTING_ACCENT':
            o['values'] = 0 if t['accents'] == ALL_ACCENTS else t['accents']
    t['undeclared'] = [m for m, _ in SETTINGS.values() if m not in [o['setting'] for o in t['opts']]]
    return t


def validate(t, name):
    """The whole-theme rules resolve() can't check per attribute."""
    def err(message) -> NoReturn: fail(name, t['line'], message)
    settings = [o.get('key') or o['setting'] for o in t['opts']]
    accent = next((o for o in t['opts'] if o['setting'] == 'SETTING_ACCENT'), None)
    if not accent:
        err('no accent option')
    if not t['accent_key']:
        err('accent: no key')
    for s in settings:
        if settings.count(s) > 1:
            err(f'{s} declared twice')

    for end in ('top', 'bottom') if t['sb_w'] else ():  # the thumb's round end gives its corner pixel to the glass
        y = t['sb_' + end]
        if corner_inset(y) > t['sb_right'] + 1:
            err(f'list.scrollbar: {end} {y} is under the glass (inset {corner_inset(y)} > right {t["sb_right"]} + 1)')
    if t['sb_w']:
        if t['sb_top'] >= t['sb_bottom']:
            err(f"list.scrollbar: top {t['sb_top']} is not above bottom {t['sb_bottom']}")
        if not t['sb_min']:
            err('list.scrollbar: no min')
        if not t['sb_thumb'] and not t['light'].get('sb_thumb'):
            err('list.scrollbar: no thumb colour')
    if any(o.get('key') == t['accent_key'] for o in t['opts']):
        err(f"{t['accent_key']} declared twice")
    if not t['accents']:
        err('colorSchemes is empty')
    # Ten is this format's own bound, not a reader's: config_value reads up to three digits and write_int_config
    # writes any. Past 32, the accents mask (option_t.values, 32 bits) could not hold a scheme.
    if len(t['schemes']) > 10:
        err(f"{len(t['schemes'])} accents, at most 10")
    # the chevrons and song rows' marks stand on the set's right column; the wheel's indicator draws sb_w wide between
    # sb_top and sb_bottom
    if not geometry(t).right_x:
        err('the chevrons and song rows need the grid\'s right_x')
    if not (t['sb_w'] and t['sb_bottom'] > t['sb_top']):
        err('list.scrollbar: the wheel\'s indicator needs w, top and bottom')
    g = geometry(t)  # the bar's own label is laid out at bar_px (tools/ipod.py status_row), its text drawn at font_size
    if g.bar_px and t['font_px[ROLE_BAR]'] != g.bar_px:
        err(f"bar.title.font_size {t['font_px[ROLE_BAR]']} is not the geometry's bar_px {g.bar_px}")
    for o in t['opts']:
        if o.get('setting') != 'SETTING_ACCENT' and not o.get('key') and o['def'] >= 0 and not o.get('values'):
            err(f"{o['setting']}: default has no effect without values (settings_read holds a stock setting's default)")
    for field, word in (('def', 'default'), ('preset', 'preset')):
        if accent[field] >= 0 and not t['accents'] >> accent[field] & 1:
            err(f'accent {word} {scheme_names(t)[accent[field]]} is not in colorSchemes')
    if accent['def'] < 0 and not t['accents'] & 1:
        err('accent: slot 0 is not offered, so a missing key needs a default')
    for k in SCHEMA.values():  # every value, those from a default or a base too (coverflow divides by some)
        if k.type != 'set':
            for v in [s[k.field] for s in t['schemes']] if k.path.startswith('scheme.') else [t[k.field], t['light'].get(k.field, k.lo)]:
                if not k.lo <= v <= k.hi:
                    err(f'{k.path}: {v} outside {k.lo}..{k.hi}')
    for s in t['schemes']:
        for f in ('paper', 'ink'):
            if len({s[f] >> 16, s[f] >> 8 & 255, s[f] & 255}) > 1:
                err(f'scheme.{f}: expected a grey')
        if (s['paper'] & 255) <= (s['ink'] & 255):
            err('scheme.paper: must be lighter than ink')
    if 'SETTING_LIGHT' in settings and any(s['mode'] for s in t['schemes']):
        err('light: a scheme with its own mode has no Light Mode option')
    shelf = t['cf.top'] + 160 + t['cf.reflect']  # 160: coverflow.c's ART_SIZE
    if shelf > t['cf.view_h']:
        err(f"coverflow.shelf: top + 160 + reflect = {shelf} exceeds {t['cf.view_h']}")
    near = 80 * math.sin(math.radians(t['cf.tilt']))  # half a side cover's width turned toward the eye
    if t['cf.eye'] + t['cf.z1'] <= near:
        err(f"coverflow.shelf: eye + z1 = {t['cf.eye'] + t['cf.z1']} doesn't clear 80 sin(tilt) = {near:.1f}")
    if t['cf.swing'] == 1:  # and all through a step: depth follows the turn, less lift at most a quarter's worth mid-way
        clear = t['cf.eye'] - max(0, 80 - t['cf.z1'] / math.sin(math.radians(t['cf.tilt']))) - t['cf.lift'] / 4
        if clear <= 0:
            err(f"coverflow.shelf: swing's near edge reaches the eye mid-step: eye - max(0, 80 - z1 / sin(tilt)) - lift / 4 = {clear:.1f}")
        rest = (t['cf.x1'] + 80 * math.cos(math.radians(t['cf.tilt']))) * t['cf.eye'] / (t['cf.eye'] + t['cf.z1'] - near)
        if rest >= 2048:  # pose()'s 1/16 px products stay in 32 bits below this
            err(f"coverflow.shelf: swing's near edge rests {rest:.0f} px out, past 2048")
    if t['cf.eye'] + t['cf.eye_turn'] > 2046:  # unproject's 1/16 px eye times a 16.16 cosine stays in 32 bits
        err(f"coverflow.shelf: eye + eye_turn = {t['cf.eye'] + t['cf.eye_turn']} exceeds 2046")
    if t['cf.thumb']:  # coverflow_wheel_poll, draw_row and the entry divide by these
        for a in ('ticks_turn', 'ticks_album', 'track', 'snap', 'spin', 'far_left', 'rate_up', 'rate_down', 'lead_ms', 'row_edge',
                  'enter_albums', 'slide_ms'):
            if not t[f'cf.{a}']:
                err(f'coverflow.shelf: thumb needs {a}')
        if t['cf.arch_span']:  # th_arch and arch_pose divide by these; a span under an album would break a step's turn
            for a in ('arch_in', 'arch_out', 'arch_tau'):
                if not t[f'cf.{a}']:
                    err(f'coverflow.shelf: arch_span needs {a}')
            if t['cf.arch_span'] < 100 or t['cf.arch_full'] <= t['cf.arch_at']:
                err('coverflow.shelf: arch_span must be an album (100) or more, and arch_full must pass arch_at')
        for lo, hi in (('fast_at', 'fast_full'), ('fast_at', 'jump_at'), ('fast_at', 'big_jump_at'), ('lead_from', 'lead_full'), ('row_from', 'row_full')):
            if t[f'cf.{hi}'] <= t[f'cf.{lo}']:
                err(f'coverflow.shelf: {hi} {t[f"cf.{hi}"]} must pass {lo} {t[f"cf.{lo}"]}')
        # The held row's covers on the screen, which entry_window's CF_WIDE must all draw: formed
        # and shrunk, row_out px apart, each the side pose()'s at row_flat (1 album at least: a
        # nearer one is mid-turn) narrowed by row_width
        sh, f, a = 1 - t['cf.row_shrink'] / 100, max(t['cf.row_flat'] / 100, 1), math.radians(t['cf.tilt'])
        x, z, eye = t['cf.x1'] + t['cf.xs'] * (f - 1), t['cf.z1'] + t['cf.zs'] * (f - 1), t['cf.eye']
        edge = [(x + u * math.cos(a)) * eye / (eye + z - u * math.sin(a)) for u in (80, -80)]  # project()'s
        w = (edge[0] - edge[1]) * (1 - t['cf.row_width'] / 100) * sh
        n = math.ceil((O['CF_VIEW_W'] + w) / (t['cf.row_out'] * sh)) if t['cf.row_out'] else math.inf
        if n > CF_WIDE:
            err(f"coverflow.shelf: row_out {t['cf.row_out']} puts {n} covers of the held row on the screen, past CF_WIDE {CF_WIDE}")
    for k in (*MOTION_WINDOW, *MOTION_WIDGET):
        if not k.startswith('coverflow_step') and EASINGS[t[f'motion.{k}.easing']] == 'follow':
            err(f'motion.{k}.easing: follow is only for coverflow_step and coverflow_step_fast')
    if t['cf.bright2'] > t['cf.bright1']:
        err(f'coverflow.shelf: bright2 {t["cf.bright2"]} exceeds bright1 {t["cf.bright1"]}')
    h = max(O['CF_NAME_H'], t['cf.artist_y'] + O['CF_ARTIST_H'])  # the album's box, and the artist's under it (coverflow.c)
    bar = geometry(t).bar
    if t['cf.view_h'] > 320 - bar:
        err(f"coverflow.shelf: view_h {t['cf.view_h']} exceeds the client height {320 - bar}")
    if t['cf.text_y'] + h > 320 - bar:
        err(f'coverflow.caption: text_y + {h} = {t["cf.text_y"] + h} exceeds the client height {320 - bar}')
    for box, y, h in (('title', t['page_cap_y'], t['page_cap_h']), ('detail', t['page_det_y'], t['page_det_h'])):
        if y + h > t['page_detail']:
            err(f"page.rows: {box} box ends at {y + h}, past the {t['page_detail']} px row")
    # a label draws nothing in a box shorter than its font
    for box, h, role in (('title_h', t['page_cap_h'], 'ROLE_TALL'), ('detail_h', t['page_det_h'], 'ROLE_META'), ('height', t['page_row'], 'ROLE_TITLE')):
        if h < t[f'font_px[{role}]']:
            err(f"page.rows.{box}: {h} is shorter than its {t[f'font_px[{role}]']} px font")


def accent_default(t):
    return next(o['def'] for o in t['opts'] if o['setting'] == 'SETTING_ACCENT')


def c_name(t):
    """t's id as a C identifier's start: an id may hold a hyphen."""
    return t['id'].replace('-', '_')


def theme_keys(themes):
    """The themes' own IPOD keys in read order, after the payload's thirteen: each theme's own accent key and own
    options, theme then declaration order, each key once. key -> (value count, default, the own option or None
    for an accent key)."""
    keys = {}
    for t in themes:
        for o in t['opts']:
            if o.get('key'):
                keys.setdefault(o['key'], (len(o['names']), max(o['def'], 0), o))
            elif o['setting'] == 'SETTING_ACCENT' and t['accent_key'] not in ACCENT_KEYS:
                keys.setdefault(t['accent_key'], (len(t['schemes']), max(accent_default(t), 0), None))
    return keys


def builtins(root=ROOT/'assets/themes'):
    """Every <id>/theme.xml under root, resolved and validated, sorted by index."""
    nodes = [load(p) for p in sorted(root.glob('*/theme.xml'))]
    by_id = {}
    for n in nodes:
        i = n.attrs.get('id')
        if i in by_id:
            fail(n.file, n.line, f"id {i} is {by_id[i].file}'s too")
        by_id[i] = n
    found, indexes = [], {}
    for n in nodes:
        t = resolve(n, by_id)
        if t['index'] in indexes:  # the stored THEME value: one theme's
            fail(n.file, n.line, f"index {t['index']} is {indexes[t['index']].file}'s too")
        indexes[t['index']] = n
        validate(t, n.file)
        if t['id'] != n.file.partition('/')[0]:
            fail(n.file, n.line, f'id {t["id"]} is not its folder name')
        found.append((t['index'], n, t))
    found.sort(key=lambda f: f[0])
    # An own key is one stored value too: its values and default alike; its preset is each theme's (as LIGHT's).
    def shape(e): return e[:2] + ({k: v for k, v in e[2].items() if k not in ('preset', 'keep')} if e[2] else None,)
    owners = {}
    for _, n, t in found:
        for key, e in theme_keys([t]).items():
            o = owners.setdefault(key, (t, e))
            if shape(o[1]) != shape(e):
                fail(n.file, n.line, f"{key} is {o[0]['id']}'s, with other values or default")
    for i, (index, n, _) in enumerate(found):
        if index != i:
            fail(n.file, n.line, f'index {index}, expected {i}')
    return [t for _, _, t in found]


def own_tones(t):
    """The distinct tones rows t's own schemes state, in scheme order: <id>_tones[]."""
    return list(dict.fromkeys(s['tones'] for s in t['schemes'] if 'tones' in s))


def motion_fields(t):
    """theme_t.motion's rows and the recall after them, as emit's (C field, value) pairs."""
    def hint(k):
        ms, anim, easing = (t[f'motion.{k}.{a}'] for a in ('ms', 'anim', 'easing'))
        tail = '' if EASINGS[easing] == 'cubic_out' else f',easing={EASINGS[easing]}'  # cubic_out: the window animator's own default
        h = f'{ANIMS[anim]}(duration={ms}{tail})'
        return 0 if not ms or k == 'page' and h == SLIDE else json.dumps(h)  # 0: the window keeps its asset hint, page's already being SLIDE
    return [*((f'motion[MOTION_{k.upper()}].hint', hint(k)) for k in MOTION_WINDOW),
            *((f'motion[MOTION_{k.upper()}].{a}', t[f'motion.{k}.{a}']) for k in MOTION_WIDGET for a in ('ms', 'easing')),
            ('recall_ms', t['recall_ms'])]


def emit(themes, out):
    """out/'themes.inc': the schemes, options and themes[] theme_engine.c includes, then the geometry set's values;
    out/'themes.h': their enums and macros, which every file of tools/build.py's NAVIGATION reads."""
    g = built_geometry(themes)
    keys = list(theme_keys(themes))
    def setting(o): return f'SETTING_OWN + {keys.index(o["key"])}' if o.get('key') else o['setting']
    def c(k, v):
        return list(CHOICES[k.path].values())[v] if k.type == 'enum' else v
    lines = ['/* tools/theme.py wrote this from assets/themes/<id>/theme.xml; edit those. */']
    def tones(t, v, s):  # an own scheme's own row, else its ACCENTS row (its base's)
        return f'{c_name(t)}_tones[{own_tones(t).index(s["tones"])}]' if 'tones' in s else f'accents[{s.get("base", v)}]'
    def rows(t, light):
        return [f'    {{ .tones = {tones(t, v, s)}, .chevron = 0x{s["chevron"]:06x}, .muted = 0x{s["muted"]:06x}, .sel_text = 0x{s["sel_text"]:06x}, '
                f'.mode = {c(SCHEMA["scheme.mode"], s["mode"])}, .paper = 0x{s["paper"]:06x}, .ink = 0x{s["ink"]:06x}'
                + (f', .label = {json.dumps(s["label"])}' if 'label' in s else '') + ' },'
                for v, s in enumerate(scheme_half(s, light) for s in t['schemes'])]
    for t in themes:
        i, one = c_name(t), shared(t)
        # one: the light rows serve both halves (today's lines); else the dark rows, then the light ones
        if own_tones(t):
            lines += [f'static const unsigned {i}_tones[][5] = {{', *('    { %s },' % ', '.join(f'0x{x:06x}' for x in r) for r in own_tones(t)), '};']
        lines += [f'static const scheme_t {i}_schemes[] = {{', *rows(t, one), '};',
                  f'_Static_assert(sizeof {i}_schemes / sizeof *{i}_schemes >= ACCENT_N, "one scheme per ACCENTS row, then the theme\'s own");']
        if not one:
            lines += [f'static const scheme_t {i}_light[] = {{', *rows(t, True), '};',
                      f'_Static_assert(sizeof {i}_light == sizeof {i}_schemes, "one light half per scheme");']
        lines.append(f'static const option_t {i}_opts[] = {{')
        lines += [f'    {{ .setting = {setting(o)}, .def = {o["def"]}, .preset = {o["preset"]}, .keep = {o["keep"]}, '
                  f'.values = 0x{o["values"]:x} }},' for o in t['opts']]
        lines.append('};')
    lines.append('const theme_t themes[THEME_N] = {')
    for t in themes:
        # a final field's halves {dark, light}, flipped between the default accent's light paper and ink
        d = t['schemes'][max(accent_default(t), 0)]
        halves = [half(t, light, d['paper'], d['ink']) for light in (False, True)]
        def field(k): return '{%s, %s}' % tuple(c(k, h[k.field]) for h in halves) if k.final else c(k, t[k.field])
        # theme_t order: the live element fields, the pointers and n_opts, then the 'open' fields.
        fields = [*((k.field, field(k)) for k in SCHEMA.values() if k.cls == 'live' and view_attr(k.path)),
                  ('accent_slot', ACCENT_KEYS.get(t['accent_key']) or f'&st.own[{keys.index(t["accent_key"])}]'), ('accent_key', json.dumps(t['accent_key'])),
                  ('schemes', f'{c_name(t)}_schemes'), ('light', f'{c_name(t)}_schemes' if shared(t) else f'{c_name(t)}_light'),
                  ('n_schemes', len(t['schemes'])),
                  ('opts', f'{c_name(t)}_opts'), ('n_opts', len(t['opts'])),
                  ('undeclared', ' | '.join(f'(1 << {m})' for m in t['undeclared']) or 0),
                  *((k.field, c(k, t[k.field])) for k in SCHEMA.values() if k.cls == 'open'),
                  *motion_fields(t),
                  ('art', json.dumps(t['art_id'].replace('-', '_') + '_'))]  # theme_art's prefix (list_marks.c)
        lines += ['    {', *(f'        .{k} = {v},' for k, v in fields), '    },']
    lines.append('};')
    lines += ['enum { ' + ', '.join(f'G_{n.upper()}' for n in VALUE_NAMES) + ', G_N };',
              # the words tools/ipod.py patch_code bakes into stock's immediates; volatile: -Oz can't fold the
              # initialisers, which test/patch.py pokes
              'const volatile unsigned short geom_words[G_N] = {' + ', '.join(
                  str(immediate_values(g)[n]) for n in VALUE_NAMES) + '};',
              'enum { ' + ', '.join(f'V_{n.upper()}' for n in GEOM_VALS) + ', V_N };',
              # volatile as geom_words: test/patch.py pokes it
              'static const volatile short geom_vals[V_N] = {' + ', '.join(str(getattr(g, n)) for n in GEOM_VALS) + '};',
              f'#define THEME_MOTION_OFF {THEME_MOTION_OFF}',
              f'#define SCHEME_MODE_OFF {SCHEME_MODE_OFF}',
              *(f'#define SETTING_N_{n.upper()} {len(v)}' for n, (_, v) in SETTINGS.items()),
              *(f'#define OWN_{k} {i}' for i, k in enumerate(theme_keys(themes))),
              'const theme_key_t theme_keys[THEME_KEYS] = {', *(
                  f'    {{ .key = {json.dumps(k)}, .label = 0, .names = 0, .n = {n}, .def = {d} }},' if o is None else
                  f'    {{ .key = {json.dumps(k)}, .label = {json.dumps(o["label"])}, '
                  f'.names = (const char *const[]){{{", ".join(map(json.dumps, o["names"]))}}}, .n = {n}, .def = {d} }},'
                  for k, (n, d, o) in theme_keys(themes).items()), '};', '']
    names = [l for l in lines[1:] if l.startswith(('enum {', '#define '))]
    globals = ['extern ' + l.split(' = ')[0] + ';' for l in lines if l.startswith('const ')]  # theme_engine.c defines them
    (out/'themes.h').write_text('\n'.join([lines[0], *names, *globals, '']))
    (out/'themes.inc').write_text('\n'.join(l for l in lines if l not in names))
