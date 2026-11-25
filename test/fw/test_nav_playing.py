"""patch/navigation.c's status bar and Now Playing under OLED, the one theme: the battery slot and its painter, the
theme's faces, scrub and the Centre cycle, the lyrics' wheel and touch, the full-screen art and the geometry sets'
boot; np_bar.c's bar title, track, fill glide and Track Time; np_marquee.c's lines; song_rows.c's song_fit."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Deques, Externpic, ask_cover, in_fork, libc, ui_name
from test_nav_loop import DOWN, UP, key_event

THEMES = host.tools_theme.builtins()
OLED, = THEMES
GEOMETRY = host.tools_theme.built_geometry(THEMES)  # the one set the build bakes: OLED's own
THEME_OLED = 3  # the THEME key's value from the four themes there were: OLED's (navigation.c theme_boot)
RET_REPEAT, STOP = 8, 11
FONT_ROW = -2
ROLE_TITLE = 0
W_PARENT = O['W_PARENT']
CANVAS_LCD, LCD_FILL, LCD_TEXT = O['CANVAS_LCD'], O['LCD_FILL_COLOR'], O['LCD_TEXT_COLOR']
SCROLL_X, SCROLL_Y, CONTENT_H = O['SCROLL_X'], O['SCROLL_Y'], O['VIEW_CONTENT_H']
KEY_NEXT, KEY_PREV, KEY_CENTER = O['KEY_NEXT'], O['KEY_PREV'], O['KEY_CENTER']
PCT_W, NUB_W = O['BATT_PCT_W'], O['BATT_NUB_W']
BT_GLYPH = b'bar_btcon'
# the named periods of navigation.h and its units (NAVIGATION_SOURCE), as their comments state the behaviour by them
NAV = {k: int(v) for k, v in re.findall(r'^#define (\w+) (\d+)\b', NAVIGATION_SOURCE, re.M)}


def rgba(c, a=0xff):
    """theme.h's RGBA: 0xRRGGBB as the canvas's 0xAABBGGRR."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def wide(s):
    return (C.c_uint * (len(s) + 1))(*map(ord, s))


def read_wide(p):
    out = []
    while p and C.c_uint.from_address(p + 4 * len(out)).value:
        out.append(chr(C.c_uint.from_address(p + 4 * len(out)).value))
    return ''.join(out)


class Config:
    """config.ini's [IPOD] keys: a key's value, else the caller's default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): str(v).encode() for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture(scope='module')
def ranges(library):
    """Inter's cmap as the build cut it (tools/build.py subset_font): INTER_RANGES."""
    header = (library.parent/'stock.h').read_text()
    def runs(name):
        values = [int(v, 16) for v in host.tools_build.need(re.search(rf'#define {name} \{{([^}}]*)\}}', header), name)[1].split(',')]
        return list(zip(values[::2], values[1::2]))
    return {'inter': runs('INTER_RANGES')}


def has(runs, text):
    return all(any(lo <= ord(c) <= hi for lo, hi in runs) for c in text)


PTRS = set('''bar_bt bar_wifi bar_pct bar_slot bar_icon bar_idle bar_state bar_eq bar_title np_win
    np_album np_slider np_remain np_elapsed np_cover np_title np_artist np_slide np_lrc genre_label'''.split())
UNSIGNED = set('''codec_timer batt_key bar_timer np_hash np_art scrub_timer scrub_track settle_timer lyric_push_at
    lyric_at lyric_follow_at lyric_song lyric_rows_song bc_timer bc_track bc_repaint bc_key bc_box bc_shape bc_url
    clock_key clock_timer keep_lit_at genre_timer last_input lyric_held_at lyric_seek_at'''.split())


class State:
    """navigation.c's st by field name, as this build lays it out."""
    def __init__(self, fw, scratch):
        object.__setattr__(self, 'base', C.addressof(fw.var('st', C.c_char)))
        object.__setattr__(self, 'at', scratch)

    def ref(self, name, i=0):
        kind = C.c_void_p if name in PTRS or name == 'bc' else C.c_uint if name in UNSIGNED else C.c_int
        return kind.from_address(self.base + self.at[name] + i * C.sizeof(kind))

    def __getattr__(self, name):
        return self.ref(name).value or 0

    def __setattr__(self, name, value):
        self.ref(name).value = value


@pytest.fixture
def st(fw, scratch):
    return State(fw, scratch)


class Ui:
    """Stock's widgets as host blocks (x, y, w, h at 0, the parent at W_PARENT) with their names, props, text,
    visibility and children kept here, stock's widget calls answering from them as AWTK's do; the timers
    and handlers the C adds, kept to run as the loop would."""
    def __init__(self, fw):
        self.fw, self.keep, self.next_timer, self.now, self.top = fw, [], 0, 100000, 0
        self.kids, self.names, self.ints, self.strs, self.texts, self.shown, self.types = {}, {}, {}, {}, {}, {}, {}
        self.timers, self.handlers, self.int_log, self.dirty, self.text_log = {}, [], [], [], []
        self.wm = self.widget(None, 'window_manager', 0, 0, 375, 320)
        m = fw.mock
        m('widget_lookup', lambda w, name, deep, *_: self.lookup(w, C.string_at(name).decode(), deep))
        m('widget_count_children', lambda w, *_: len(self.kids.get(w, ())))
        m('widget_get_child', lambda w, i, *_: self.child(w, C.c_int(i).value))
        m('widget_index_of', lambda w, *_: self.kids[self.parent(w)].index(w) if self.parent(w) in self.kids else -1)
        m('widget_get_prop_int', lambda w, key, default, *_: self.ints.get((w, C.string_at(key)), C.c_int(default).value))
        m('widget_set_prop_int', self.set_int)
        m('widget_invalidate_force', lambda w, r, *_: self.dirty.append(
            (w, struct.unpack_from('<4i', (C.c_char * 16).from_address(r)) if r else None)))
        m('widget_get_prop_str', lambda w, key, default, *_: self.str_ptr(w, C.string_at(key), default))
        m('widget_set_prop_str', lambda w, key, v, *_: self.set_str(w, C.string_at(key), C.string_at(v)))
        m('image_base_set_image', lambda w, name, *_: self.set_str(w, b'image', C.string_at(name)))
        m('widget_get_text', lambda w, *_: C.addressof(self.texts[w]) if w in self.texts else 0)
        m('widget_set_text_utf8', lambda w, s, *_: self.set_text(w, C.string_at(s).decode()))
        m('widget_get_text_utf8', lambda w, out, n, *_: C.memmove(out, s := (self.text(w) or '').encode()[:n - 1] + b'\0', len(s)) and 0)
        m('widget_get_visible', lambda w, *_: self.shown.get(w, 1))
        m('widget_set_visible', lambda w, v, *_: self.shown.__setitem__(w, int(bool(v))))
        m('widget_move_resize', lambda w, x, y, ww, h, *_: self.place(w, x, y, ww, h))
        m('widget_resize', lambda w, ww, h, *_: self.resize(w, ww, h))
        m('widget_get_type', lambda w, *_: self.cstr(self.types.get(w, b'view')))
        m('widget_set_name', lambda w, name, *_: self.rename(w, C.string_at(name).decode()))
        m('timer_add', self.timer_add)
        m('timer_remove', lambda t, *_: self.timers.pop(t, None) and 0)
        m('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        m('window_manager', lambda *_: self.wm)
        m('window_manager_get_top_window', lambda *_: self.top)
        m('time_now_ms', lambda *_: self.now)
        m('fw___xstat', lambda *_: -1)  # no Rockbox on the card
        m('hscroll_label_create', lambda parent, x, y, w, h, *_: self.widget(parent, '', x, y, w, h))
        m('image_create', lambda parent, x, y, w, h, *_: self.widget(parent, '', x, y, w, h))
        m('widget_factory_create_widget', lambda f, kind, parent, *_: self.widget(parent, ''))

    def cstr(self, value):
        self.keep.append(C.create_string_buffer(value))
        return C.addressof(self.keep[-1])

    def widget(self, parent, name, x=0, y=0, w=0, h=0, kind=None, text=None):
        block = C.create_string_buffer(0x400)
        self.keep.append(block)
        p = C.addressof(block)
        struct.pack_into('<4i', block, 0, x, y, w, h)
        struct.pack_into('<Q', block, W_PARENT, parent or 0)
        self.names[p] = name
        if name:
            self.set_str(p, b'name', name.encode())
        if parent:
            self.kids.setdefault(parent, []).append(p)
        if kind:
            self.types[p] = kind.encode()
        if text is not None:
            self.set_text(p, text)
        return p

    def set_int(self, w, key, v, *_):
        """The key as the call passed it: a style key is a buffer on the C's stack."""
        self.int_log.append((w, C.string_at(key), C.c_int(v).value))
        self.ints[w, C.string_at(key)] = C.c_int(v).value

    def rename(self, w, name):
        self.names[w] = name
        self.set_str(w, b'name', name.encode())

    def parent(self, w):
        return C.c_void_p.from_address(w + W_PARENT).value or 0

    def child(self, w, i):
        kids = self.kids.get(w, ())
        return kids[i] if 0 <= i < len(kids) else 0

    def lookup(self, w, name, deep):
        for k in self.kids.get(w, ()):
            if self.names.get(k) == name:
                return k
            found = deep and self.lookup(k, name, deep)
            if found:
                return found
        return 0

    def str_ptr(self, w, key, default):
        return C.addressof(self.strs[w, key]) if (w, key) in self.strs else default

    def set_str(self, w, key, value):
        self.strs[w, key] = C.create_string_buffer(value)

    def prop(self, w, key):
        return self.strs[w, key].value if (w, key) in self.strs else self.ints.get((w, key))

    def set_text(self, w, s):
        self.texts[w] = wide(s)
        self.text_log.append((w, s))  # each text as set: the C's buffer may be its stack's (clock_text)

    def text(self, w):
        return read_wide(C.addressof(self.texts[w])) if w in self.texts else None

    def box(self, w):
        return struct.unpack_from('<4i', (C.c_char * 16).from_address(w))

    def screen(self, w):
        """w's box on the screen: its parents' offsets added, up to the window manager's."""
        x, y, ww, h = self.box(w)
        p = self.parent(w)
        while p and p != self.wm:
            px, py, _, _ = self.box(p)
            x, y, p = x + px, y + py, self.parent(p)
        return x, y, ww, h

    def place(self, w, x, y, ww, h):
        struct.pack_into('<4i', (C.c_char * 16).from_address(w), 0, *(C.c_int(v).value for v in (x, y, ww, h)))

    def resize(self, w, ww, h):
        x, y, _, _ = self.box(w)
        self.place(w, x, y, ww, h)

    def timer_add(self, fn, ctx, ms, *_):
        self.next_timer += 1
        self.timers[self.next_timer] = (fn, ctx, ms)
        return self.next_timer

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, name), C.c_void_p).value

    def due(self, name):
        """The live timers that run the static name, of navigation.c or the unit it moved to (ui_name finds it),
        oldest first."""
        return [t for t, (fn, _, _) in sorted(self.timers.items()) if fn == self.fn(ui_name(self.fw.lib, name))]

    def run(self, timer):
        """A timer's pass: AWTK drops it unless it answers RET_REPEAT."""
        fn, ctx, _ = self.timers[timer]
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx)
        if result != RET_REPEAT:
            self.timers.pop(timer, None)
        return result

    def run_all(self, name, limit=100):
        for _ in range(limit):
            due = self.due(name)
            if not due:
                return
            self.run(due[0])
        raise AssertionError(f'{name} never stops')

    def emit(self, w, evt, event=0):
        """The handlers widget_on gave w for evt, as AWTK dispatches them."""
        return [C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, event) for ww, e, fn, ctx in self.handlers
                if ww == w and e == evt]

    def invalidated(self, w=None):
        return [d for d, _ in self.dirty if w is None or d == w]

    def dirty_rects(self):
        return [(d, r) for d, r in self.dirty if r]


class Canvas:
    """A canvas and its lcd: the fill and text colours stock's setters write there, every rect filled in the
    fill colour, the text drawn with its font and colour, the images by name."""
    def __init__(self, fw, x=0, y=0, fill=0xff123456, sizes=None):
        self.raw, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        struct.pack_into('<ii', self.raw, 0, x, y)
        struct.pack_into('<Q', self.raw, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<II', self.lcd, LCD_TEXT, 0xff654321, fill)
        self.p, self.rects, self.texts, self.images, self.font, self.clip, self.loaded = C.addressof(self.raw), [], [], [], (0, 0), None, []
        self.sizes = sizes or {}
        m = fw.mock
        m('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL, color & 0xffffffff))
        m('canvas_set_text_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_TEXT, color & 0xffffffff))
        m('canvas_fill_rect', lambda c, x, y, w, h, *_: self.rects.append(
            (C.c_int(x).value, C.c_int(y).value, C.c_int(w).value, C.c_int(h).value, self.fill)))
        m('canvas_set_font', lambda c, face, px, *_: setattr(self, 'font', (C.string_at(face) if face else None, px)))
        m('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append(
            (''.join(chr(v) for v in (C.c_uint * n).from_address(s)), struct.unpack_from('<4i', (C.c_char * 16).from_address(r)),
             self.font, self.text_color, self.clip)))
        m('canvas_measure_text', lambda c, s, n, *_: sum({'%': 12.6}.get(chr(v), 9.4) for v in (C.c_uint * n).from_address(s)))
        m('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                    *(self.clip or (0, 0, 375, 320))))
        m('canvas_set_clip_rect', lambda c, r, *_: setattr(self, 'clip', struct.unpack_from('<4i', (C.c_char * 16).from_address(r))))
        m('widget_load_image', self.load)
        m('canvas_get_vgcanvas', lambda *_: 1)  # a vgcanvas: its calls are in fw.calls, its float arguments are not
        m('canvas_draw_image', lambda c, bitmap, src, dst, *_: self.images.append(
            (self.loaded[C.c_uint.from_address(bitmap + 8).value], struct.unpack_from('<4i', (C.c_char * 16).from_address(dst)))))

    def load(self, w, name, bitmap, *_):
        name = C.string_at(name)
        if self.sizes.get(name, (12, 12)) is None:
            return 1
        self.loaded.append(name)
        struct.pack_into('<3I', (C.c_char * 12).from_address(bitmap), 0, *self.sizes.get(name, (12, 12)), len(self.loaded) - 1)
        return 0

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL)[0]

    @property
    def text_color(self):
        return struct.unpack_from('<I', self.lcd, LCD_TEXT)[0]

    def pixels(self):
        """Each pixel's last fill, as the rects land."""
        out = {}
        for x, y, w, h, color in self.rects:
            for j in range(y, y + h):
                for i in range(x, x + w):
                    out[i, j] = color
        return out


def theme(fw, st, light=0, **keys):
    """OLED, light or dark, with the [IPOD] keys given."""
    Config(fw, THEME=THEME_OLED, LIGHT=light, **keys)
    st.settings_read = 0
    fw.fn('accent', C.c_int)()


# The status bar's battery slot (bar_sync) and what view_battery paints in it (paint_battery).
class Bar:
    """systembar's widgets: Bluetooth, Wi-Fi, the battery label stock writes "88%" in, its slot and icon, the
    play state, EQ, the idle label and the clock, right-aligned in its row."""
    def __init__(self, fw, ui, title=False, eq_x=0):
        self.ui = ui
        self.bar = ui.widget(ui.wm, 'system_bar', 0, 0, 375, 30)
        self.row = ui.widget(self.bar, 'view_right', 240, 0, 135, 30)
        self.bt = ui.widget(self.row, 'img_bt', 10, 6, 14, 18)
        self.wifi = ui.widget(self.row, 'img_wifi', 30, 6, 20, 18)
        self.pct = ui.widget(self.row, 'label_battery', 0, 0, 40, 30, text='88%')
        self.slot = ui.widget(self.row, 'view_battery', 60, 0, 40, 30)
        self.icon = ui.widget(self.row, 'img_battery', 0, 0, 20, 30)
        self.state = ui.widget(self.row, 'img_state', 100, 4, 16, 20)
        self.eq = ui.widget(self.row, 'label_eq', eq_x, 4, 30, 20)
        self.idle = ui.widget(self.row, 'label_idle', 0, 4, 30, 20)
        self.clock = ui.widget(self.bar, 'label_clock', 150, 0, 75, 30, text='12:00')
        self.title = ui.widget(self.bar, 'label_title', 20, 2, 91, 25, text='') if title else 0
        ui.shown.update({self.wifi: 0, self.state: 0, self.eq: 0})
        ui.set_str(self.icon, b'image', b'bar_battery')
        fw.var('fw_ctx_system_bar', C.c_void_p).value = self.bar
        self.sync = lambda: fw.fn('navigation__bar_sync', None, C.c_void_p)(self.bar)

    def level(self, text, icon=b'bar_battery'):
        self.ui.set_text(self.pct, text)
        self.ui.set_str(self.icon, b'image', icon)


@pytest.fixture
def ui(fw):
    return Ui(fw)


def slot_width(ui, bar):
    return ui.box(bar.slot)[2]


ICON_W = GEOMETRY.batt_w + NUB_W  # the slim battery: the set's body and its nub (navigation.c bar_sync)


# OLED's Battery is Icon or Percent (its battery option's values): a stored Icon + Percent (2) or Percent Inside (3)
# reads as its default, Icon (theme_engine.c settings_read).
@pytest.mark.parametrize('mode, width', [(0, ICON_W), (1, PCT_W), (2, ICON_W), (3, ICON_W)])
def test_the_slot_is_its_modes_ink_wide_and_the_bars_height(fw, st, ui, mode, width):
    theme(fw, st, BATTERY=mode)
    bar = Bar(fw, ui)
    bar.sync()
    assert ui.box(bar.slot)[2:] == (width, GEOMETRY.bar) and st.batt_mode == (mode == 1)
    assert (ui.shown[bar.icon], ui.shown[bar.pct], ui.shown[bar.slot]) == (0, 0, 1)  # the slot paints it all


def test_only_a_new_level_or_charge_repaints_the_bar_after_the_frame(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    repaints = []
    def sync(*level):
        if level:
            bar.level(*level)
        bar.sync()  # mid-paint: an invalidate here is lost, so charging's colour reached one digit
        repaints.append(len(ui.due('bar_repaint')))
        ui.run_all('bar_repaint')
    sync()
    sync()
    sync('87%')
    sync('87%', b'bar_charge')
    sync('87%', b'bar_lowcharge')
    assert repaints == [1, 0, 1, 1, 1] and ui.invalidated(bar.bar) == [bar.bar] * 4


# Stock's battery writes land on the hidden label_battery and img_battery, which repaint nothing: showface's tick
# repaints the bar for a new level or charge, as the minute does for the clock, and only then.
@pytest.mark.parametrize('mode', [0, 1])
def test_a_new_level_repaints_the_bar_with_nothing_else_painting(fw, st, ui, mode):
    theme(fw, st, BATTERY=mode)
    bar = Bar(fw, ui)
    lit = fw.var('g_backlight_status', C.c_ubyte)
    lit.value = 1
    st.pct_digit, st.pct_sign = 10, 10  # as the first paint measured them
    bar.sync()  # the first paint
    ui.run_all('bar_repaint')
    def tick(*level):
        if level:
            bar.level(*level)
        before = len(ui.invalidated(bar.bar))
        fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
        return len(ui.invalidated(bar.bar)) - before
    def frame():  # the repaint's frame shows the new level and asks for no other, unless the slot resized
        bar.sync()
        asked = len(ui.due('bar_repaint'))
        ui.run_all('bar_repaint')
        return asked
    assert [tick(), tick('87%'), frame(), tick(), tick('87%', b'bar_charge'), frame(), tick()] == [0, 1, 0, 0, 1, 0, 0]
    grows = st.batt_mode == 1  # the digits shown; the icon keeps its width
    assert [tick('9%'), frame()] == [1, grows]  # a digit fewer
    width = slot_width(ui, bar)
    # a digit more: the slot widens and the row relays, after the frame
    assert [tick('10%'), frame()] == [1, grows] and slot_width(ui, bar) == width + 10 * grows
    lit.value = 0
    assert tick('86%') == 0  # dark: the wake paints it


def test_a_codec_badge_fades_to_the_glyph_and_takes_no_room(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    ui.shown[bar.wifi] = 1
    ui.set_str(bar.bt, b'image', b'bar_ldac')
    bar.sync()
    assert slot_width(ui, bar) == PCT_W  # the badge is off the row, which has the room: the percentage stays
    assert [ui.timers[t][2] for t in ui.due('codec_fade')] == [O['CODEC_MS']]
    ui.run_all('codec_fade')
    assert ui.prop(bar.bt, b'image') == BT_GLYPH
    ui.set_str(bar.bt, b'image', b'bar_ldac')  # stock writes the badge again each second
    bar.sync()
    assert ui.prop(bar.bt, b'image') == BT_GLYPH and slot_width(ui, bar) == PCT_W
    assert ui.due('codec_fade') == []


def test_a_new_codec_starts_over_and_the_glyph_alone_ends_the_badge(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    opacity = []
    fw.mock('widget_set_opacity', lambda w, v, *_: opacity.append(v))
    ui.set_str(bar.bt, b'image', b'bar_aac')
    bar.sync()
    ui.run_all('codec_fade')
    ui.set_str(bar.bt, b'image', b'bar_sbc')
    bar.sync()
    assert opacity[-1] == 255 and len(ui.due('codec_fade')) == 1  # shown whole again, for CODEC_MS
    ui.set_str(bar.bt, b'image', BT_GLYPH)  # a badge's own faded glyph: still that badge
    bar.sync()
    assert len(ui.due('codec_fade')) == 1
    ui.set_str(bar.bt, b'image', b'bar_bt')  # stock's plain glyph: no badge
    bar.sync()
    assert ui.due('codec_fade') == [] and opacity[-1] == 255


def test_the_icon_is_the_slim_battery(fw, st, ui):
    theme(fw, st, BATTERY=0)
    canvas = Paint(fw, ui, Bar(fw, ui))()
    # OLED's slim battery is vgcanvas paths, whose float coordinates the stubs can't see: its outline, the charge
    # and the nub (the emulator's screenshots check where it lands)
    assert canvas.rects == [] and len(fw.calls('vgcanvas_stroke')) == 1 and len(fw.calls('vgcanvas_fill')) == 2


KEY_LOW, KEY_CHARGING, KEY_PLUGGED, KEY_WEAK = 1, 2, 4, 8  # navigation.h BATT_KEY_*


@pytest.mark.parametrize('level, bits', [(62, 0), (62, KEY_CHARGING), (5, KEY_CHARGING), (100, KEY_PLUGGED), (41, KEY_WEAK)])
def test_the_slim_batterys_fill_is_the_level_charging_or_not(fw, level, bits):
    """The fill shows the real level on the cable too: forced full while charging, it hid how much was left."""
    fill = fw.fn('list_marks__battery_fill', C.c_uint, C.c_uint)
    assert fill(level << 4 | bits) == level


@pytest.mark.parametrize('level, bits, tone', [(62, 0, 0xffffff), (12, 0, O['BATT_LOW_RGB']), (12, KEY_CHARGING, O['BATT_CHARGE_RGB']),
                                               (80, KEY_PLUGGED, O['BATT_CHARGE_RGB']), (41, KEY_WEAK, O['BATT_WEAK_RGB'])])
def test_the_fill_is_white_red_green_or_orange(fw, level, bits, tone):
    assert fw.fn('list_marks__battery_tone', C.c_uint, C.c_uint, C.c_uint)(level << 4 | bits, 0xffffff) == tone


@pytest.mark.parametrize('bits, light, glyph', [(KEY_CHARGING, 0, b'battbolt_d'), (KEY_CHARGING, 1, b'battbolt_l'),
                                                (KEY_WEAK, 0, b'battwarn_d'), (KEY_PLUGGED, 0, b'battplug_d'),
                                                (KEY_PLUGGED, 1, b'battplug_l'), (0, 0, None), (0, 1, None)])
def test_charging_puts_the_bolt_inside_full_or_held_the_plug_and_a_weak_charger_the_warning(fw, st, ui, bits, light, glyph):
    """Full or held at Charge Limit, with the cable in, is the plug (docs/oled-design.md#status-row); on battery,
    no glyph."""
    theme(fw, st, light=light)
    name = fw.fn('list_marks__battery_glyph', C.c_char_p, C.c_uint)(50 << 4 | bits)
    assert (name and name.split(b'_', 1)[1]) == glyph


def bar_key(fw, st, ui, text, icon, state=0, held=0, dac=1, cable=1):
    """battery_now's key for stock's label and icon, g_power_chargestate, the cable (g_usbdet_value) and Charge
    Limit's hold."""
    from test_usb_charge import Charging
    theme(fw, st, BATTERY=0)
    bar = Bar(fw, ui)
    Charging(fw, pref=dac, mode=1, cable=cable).config()
    fw.var('fw_ctx_g_power_chargestate', C.c_int).value = state
    fw.var('g_usbdet_value', C.c_ubyte).value = cable
    st.charge_held = held
    bar.level(text, icon)
    bar.sync()
    return fw.fn('navigation__battery_now', C.c_uint)()


# Stock's icon is bar_charge for a cable only while the AXP2101 says it is not Full (systembar_showface,
# 0x541e2c: g_power_chargestate 2 picks bar_battery), and the plain icon with USB DAC charging Off over a DAC link;
# so the cable is g_usbdet_value, and Full is the charger's word, not the icon's.
@pytest.mark.parametrize('text, icon, state, held, dac, cable, key', [
    ('62%', b'bar_charge', 1, 0, 1, 1, 62 << 4 | KEY_CHARGING),
    ('100%', b'bar_battery', 2, 0, 1, 1, 100 << 4 | KEY_PLUGGED),  # Full, cable in: stock's plain icon
    ('100%', b'bar_battery', 2, 0, 1, 0, 100 << 4),  # Full left in g_power_chargestate after an unplug
    ('62%', b'bar_battery', 0, 0, 1, 0, 62 << 4),
    ('80%', b'bar_charge', 1, 1, 0, 1, 80 << 4 | KEY_PLUGGED),  # held at Charge Limit
    ('80%', b'bar_battery', 0, 1, 0, 1, 80 << 4 | KEY_PLUGGED),  # held, the AXP2101's status neither 1 nor 2
    ('50%', b'bar_charge', 1, 0, 0, 1, 50 << 4),  # USB DAC charging Off: held, not charging
    ('50%', b'bar_battery', 0, 0, 0, 1, 50 << 4)])  # the same over a DAC link: stock's plain icon
def test_the_key_says_charging_full_or_held(fw, st, ui, text, icon, state, held, dac, cable, key):
    """Full or held at Charge Limit with the cable in is green with the plug, USB DAC charging Off's hold is white:
    neither charges."""
    assert bar_key(fw, st, ui, text, icon, state, held, dac, cable) == key


def test_with_a_bar_widget_missing_nothing_moves(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    ui.names[bar.slot] = 'gone'
    before = ui.box(bar.slot)
    bar.sync()
    assert ui.box(bar.slot) == before and ui.invalidated() == []


class Paint:
    """The bar synced, then view_battery painted on a fresh canvas."""
    def __init__(self, fw, ui, bar, **kwargs):
        self.fw, self.ui, self.bar, self.kwargs = fw, ui, bar, kwargs

    def __call__(self, text='88%', icon=b'bar_battery'):
        self.bar.level(text, icon)
        self.bar.sync()
        canvas = Canvas(self.fw, **self.kwargs)
        # the percentage is one line (canvas_draw_text at its top-left), never a wrapping rect: as a rect at (x, y)
        self.fw.mock('canvas_draw_text', lambda c, s, n, x, y, *_: canvas.texts.append(
            (''.join(chr(v) for v in (C.c_uint * n).from_address(s)), (C.c_int(x).value, C.c_int(y).value, 0, 0),
             canvas.font, canvas.text_color, canvas.clip)))
        self.fw.fn('navigation__paint_battery', None, C.c_void_p, C.c_void_p)(self.bar.slot, canvas.p)
        assert canvas.fill == 0xff123456  # put back
        return canvas


def test_percent_alone_is_the_level_and_a_percent_sign_centred_in_the_slot(fw, st, ui):
    """One line, never wrapped (a rect narrower than its text wrapped it onto a second line on a Q2): centred
    across the slot, its top BATT_PX/2 above the row's centre, as the icon centres on the row."""
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    canvas = Paint(fw, ui, bar)()
    (text, (x, y, _, _), (face, px), color, _), = canvas.texts
    assert (text, px, color) == ('88%', O['BATT_PX'], 0xffffffff)
    assert x == (ui.box(bar.slot)[2] - int(2 * 9.4 + 12.6)) // 2  # Canvas measures '0' 9.4 px, '%' 12.6 and y == (30 + 1 - O['BATT_PX']) // 2
    assert canvas.rects == [] and not fw.calls('canvas_draw_text_in_rect')


def test_the_slot_holds_the_percentage_whole(fw, st, ui):
    """Each digit and the sign measured up to a whole pixel: the slot is never narrower than the ink, at 100% too."""
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    paint = Paint(fw, ui, bar)
    paint('100%', b'bar_charge')
    canvas = paint('100%', b'bar_charge')
    (text, (x, _, _, _), *_), = canvas.texts
    assert text == '100%' and x >= 0 and slot_width(ui, bar) >= 3 * 9.4 + 12.6


@pytest.mark.parametrize('text, icon, color', [('20%', b'bar_battery', 0xffffff), ('15%', b'bar_charge', O['BATT_CHARGE_RGB']),
                                               ('19%', b'bar_battery', O['BATT_LOW_RGB']), ('15%', b'bar_lowcharge', O['BATT_LOW_RGB'])])
def test_the_percentage_says_charging_or_low_in_its_colour(fw, st, ui, text, icon, color):
    """Low is one threshold, under BATT_LOW_PCT (20%), as the slim battery's red fill."""
    theme(fw, st, BATTERY=1)
    canvas = Paint(fw, ui, Bar(fw, ui))(text, icon)
    assert canvas.texts[0][3] == rgba(color) and O['BATT_LOW_RGB'] == 0xff453a


def test_a_cable_in_with_charging_held_off_is_not_shown_charging(fw, st, ui):
    """Stock's icon is bar_charge for any cable; USB DAC with charging Off, or Charge Limit, charges nothing."""
    from test_usb_charge import Charging
    theme(fw, st, BATTERY=1)
    paint, q = Paint(fw, ui, Bar(fw, ui)), Charging(fw, pref=0, mode=1, cable=1)
    q.config()  # Audio and DAC, charging Off: held
    assert [paint(text, b'bar_charge').texts[0][3] for text in ('50%', '14%')] == [rgba(0xffffff), rgba(O['BATT_LOW_RGB'])]
    q.pref = 1
    q.config()  # charging On
    assert paint('14%', b'bar_charge').texts[0][3] == rgba(O['BATT_CHARGE_RGB'])


def test_the_first_paint_measures_the_digits_and_the_slot_fits_them(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    paint = Paint(fw, ui, bar)
    paint('88%')
    digit, sign = int(9.4) + 1, int(12.6) + 1  # Canvas measures '0' 9.4 px and '%' 12.6: each up a pixel
    widths = {}
    for text in ('5%', '88%', '100%'):
        paint(text)
        widths[text] = slot_width(ui, bar)
    assert widths == {'5%': digit + sign, '88%': 2 * digit + sign, '100%': 3 * digit + sign}


def test_no_lcd_paints_nothing(fw, st, ui):
    theme(fw, st, BATTERY=1)
    bar = Bar(fw, ui)
    bar.sync()
    canvas = Canvas(fw)
    struct.pack_into('<Q', canvas.raw, CANVAS_LCD, 0)
    fw.fn('navigation__paint_battery', None, C.c_void_p, C.c_void_p)(bar.slot, canvas.p)
    assert canvas.rects == canvas.texts == []


# EQ is stock's to show or hide: a bar without it keeps the label off-screen (tools/ipod.py).
def test_the_payload_leaves_eq_as_stock_set_it(fw, st, ui):
    theme(fw, st)
    bar = Bar(fw, ui)
    ui.shown[bar.eq] = 1
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
    assert ui.shown[bar.eq] == 1


# Stock's systembar_showface each second: the play state follows whichever player plays.
@pytest.mark.parametrize('status, shown, image', [(2, 1, b'oled_play_d'), (3, 1, b'oled_pause_d'), (1, 0, None)])
def test_showface_shows_the_play_state_and_answers_as_stock(fw, st, ui, status, shown, image):
    theme(fw, st)
    bar = Bar(fw, ui)
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('stock_showface_trampoline', lambda b, *_: 77 if b == bar.bar else 0)
    assert fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar) == 77
    assert ui.shown[bar.state] == shown and ui.prop(bar.state, b'image') == image
    assert ui.shown[bar.idle] == (not shown)  # neither state nor EQ: the idle label


def test_the_play_state_is_the_themes_art_in_its_half(fw, st, ui):
    theme(fw, st, light=1)
    bar = Bar(fw, ui)
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('ringnav_showface', C.c_int, C.c_void_p)(bar.bar)
    assert ui.prop(bar.state, b'image') == b'oled_play_l'


# The page's window, and its hidden navbar's stock title.
def window(ui, name, navbar_title=None, navbar_shown=0):
    win = ui.widget(ui.wm, name, 0, 0, 375, 320, kind='window')
    ui.set_str(win, b'name', name.encode())
    if navbar_title is not None:
        nav = ui.widget(win, 'view_navbar', 0, 0, 375, 60)
        ui.shown[nav] = navbar_shown
        ui.widget(nav, 'scrlabel_title', 0, 0, 200, 40, kind='hscroll_label', text=navbar_title)
    return win


# The theme's faces (theme_font, theme_face): its own face (Inter) where it has the text's characters, else its
# fallback, stock's, which has CJK, at the fallback's share of the px.
LATIN, CYRILLIC, CJK = 'Albums', 'Альбом', '专辑'
TITLE_PX = OLED['font_px[ROLE_TITLE]']
FACES = [f.name.encode() for f in host.tools_build.FACES.values()]  # navigation.c's font_faces, by FONT_*
FALLBACK_PX = (TITLE_PX * host.tools_build.FACES['inter'].fallback_pct + 50) // 100  # navigation.c fallback_px


def first_face(ranges, text):
    """The face theme_font gives text, by the cmaps the build cut: Inter, else stock's."""
    return b'inter' if has(ranges['inter'], text) else b'default'


def label_font(ui, w):
    return ui.prop(w, b'style:normal:font_name'), ui.prop(w, b'style:normal:font_size')


@pytest.mark.parametrize('text, face', [(LATIN, b'inter'), (CYRILLIC, b'inter'), (CJK, b'default')])
def test_the_title_face_falls_back_by_the_text(fw, st, ui, ranges, text, face):
    assert first_face(ranges, text) == face  # the text is where the cmaps say, or the test proves nothing
    theme(fw, st)
    label = ui.widget(ui.wm, 'label', text=text)
    answer = fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(label, ROLE_TITLE, -1)
    got, px = label_font(ui, label)
    assert got == face and answer == FACES.index(face) | px << 4
    assert OLED['font[ROLE_TITLE]'] == FACES.index(b'inter')  # the list title's face, or this tests another
    assert px == (TITLE_PX if face == b'inter' else FALLBACK_PX)  # stock's standing in at its share


def test_a_face_already_written_is_not_written_again(fw, st, ui):
    theme(fw, st)
    label = ui.widget(ui.wm, 'label', text=LATIN)
    font = fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)
    was = font(label, ROLE_TITLE, -1)
    writes = len(fw.calls('widget_set_prop_str'))
    assert font(label, ROLE_TITLE, was) == was and len(fw.calls('widget_set_prop_str')) == writes


@pytest.mark.parametrize('text', [LATIN, CYRILLIC, CJK])
def test_canvas_text_takes_the_face_and_size_a_label_would(fw, st, ui, ranges, text):
    theme(fw, st)
    label = ui.widget(ui.wm, 'label', text=text)
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(label, ROLE_TITLE, -1)
    face, px = label_font(ui, label)
    size = C.c_int(TITLE_PX)
    s = wide(text)
    got = fw.fn('theme_face', C.c_char_p, C.c_void_p, C.c_int, C.POINTER(C.c_int))(C.addressof(s), -1, C.byref(size))
    assert face == first_face(ranges, text) and (got or b'default', size.value) == (face, px)  # none: the system font


def test_a_pooled_rows_labels_find_their_face_again_for_the_text_stock_bound(fw, st, ui):
    """#217: a table reuses a row, stock's on_load_data writes the new text with no relayout."""
    theme(fw, st)
    table = ui.widget(ui.wm, 'table')
    row = ui.widget(table, 'row')
    title = ui.widget(row, 'title', text=LATIN)
    plain = ui.widget(row, 'plain', text=LATIN)
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(title, ROLE_TITLE, FONT_ROW)
    assert label_font(ui, title)[0] == b'inter'
    bound = []
    def load(ctx, index, w):
        bound.append((ctx, index, w))
        ui.set_text(title, CJK)
        ui.set_text(plain, CJK)
        return 7
    stock = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(load)
    C.c_void_p.from_address(table + O['TABLE_LOAD_DATA']).value = C.cast(stock, C.c_void_p).value
    assert fw.fn('ringnav_table_load', C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(0x55, 3, row) == 7
    assert bound == [(0x55, 3, row)]
    assert label_font(ui, title)[0] == b'default' and ui.prop(plain, b'style:normal:font_name') is None


@pytest.mark.parametrize('text', [LATIN, CYRILLIC, CJK])
def test_the_playing_rows_mark_keeps_its_titles_face(fw, st, ui, ranges, text):
    """View Queue's playing row, bound as stock's on_load_data binds it: title, style, then the faces again."""
    theme(fw, st)
    table = ui.widget(ui.wm, 'table')
    row = ui.widget(table, 'row')
    title = ui.widget(row, 'title', text='')
    def load(ctx, index, w):
        fw.fn('stock_row_text', C.c_int, C.c_void_p, C.c_char_p)(title, text.encode())
        fw.fn('stock_row_style', C.c_int, C.c_void_p, C.c_char_p)(title, b's_scrlabel_red24l')
        return 7
    stock = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(load)
    C.c_void_p.from_address(table + O['TABLE_LOAD_DATA']).value = C.cast(stock, C.c_void_p).value
    assert fw.fn('ringnav_table_load', C.c_int, C.c_void_p, C.c_uint, C.c_void_p)(0x55, 3, row) == 7
    plain = ui.widget(ui.wm, 'label', text=text)  # the same title without the mark
    fw.fn('theme_font', C.c_int, C.c_void_p, C.c_int, C.c_int)(plain, ROLE_TITLE, FONT_ROW)
    face, px = label_font(ui, title)
    assert ui.text(title).startswith('▶ ') and (face, px) == label_font(ui, plain)
    assert face == first_face(ranges, text) and px == (TITLE_PX if face == b'inter' else FALLBACK_PX)


# Now Playing (ringnav_playing): the iPod labels bound, the bar and its track, the times, scrub.
def clock(sec):
    """Seconds as stock's toolsTimeItoa writes them: 1:05."""
    return f'{sec // 60}:{sec % 60:02}'


class Now:
    """Stock's playing_page as its init leaves it, over a queue of twelve tracks playing the third: the
    track's place, album and remaining time, the slider and elapsed time, the art and title, the
    slide_view of art, lyrics and info pages in their column, and the lyrics' scroll_view."""
    def __init__(self, fw, ui, mcl, st, sec=50, total=200, status=2, layout='beside', lyrics=(), opened=True):
        self.fw, self.ui, self.st, self.mcl = fw, ui, st, mcl
        self.sec, self.total, self.status = sec, total, status
        self.deques = Deques(fw)
        self.paths = [C.create_string_buffer(f'/mnt/mmc/Music/{i:02}.flac'.encode()) for i in range(12)]
        self.recs = [C.create_string_buffer(0x60) for _ in self.paths]
        for rec, path in zip(self.recs, self.paths):
            struct.pack_into('<Q', rec, O['REC_PATH'], C.addressof(path))
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.deques.new([C.addressof(r) for r in self.recs])
        mcl.pos = 2
        info = fw.var('fw_ctx_g_play_id3_info', C.c_char * 2716)
        C.memmove(info, self.paths[2].raw, len(self.paths[2].raw))
        C.memmove(C.addressof(info) + O['ID3_ALBUM'], b'Blue Train\0', 11)
        fw.mock('player_playtime_and_length', self.playtime)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('toolsTimeItoa', lambda s, t, *_: C.memmove(s, clock(t).encode() + b'\0', len(clock(t)) + 1))
        fw.mock('stock_playing_trampoline', lambda w, ctx, *_: 41)
        fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<ii', (C.c_char * 8).from_address(w + SCROLL_X), 0,
                                                                             C.c_int(x).value, C.c_int(y).value))
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.win = win = ui.widget(ui.wm, 'playing_page', 0, 0, 375, 320, kind='window')
        ui.set_str(win, b'name', b'playing_page')
        self.title = ui.widget(win, 'scrlabel_title', 206, 38, 160, 28, text='Moment\'s Notice')
        self.album = ui.widget(win, 'label_ipod_album', 206, 100, 160, 22)
        self.cover = ui.widget(win, 'img_cover', 16, 22, 178, 178)
        self.state = ui.widget(win, 'img_playstate', 0, 0, 40, 40)
        times_y = 245 if layout == 'beside' else 275
        self.elapsed = ui.widget(win, 'label_playtime', 23, times_y, 35, 21)
        self.remain = ui.widget(win, 'label_ipod_remain', 317, times_y, 35, 21)
        self.col = ui.widget(win, 'slide_view_view', 80, 22, 225, 220)
        self.slide = ui.widget(self.col, 'slide_view', 0, 0, 225, 198)
        self.dots = ui.widget(self.col, 'slide_indicator1', 0, 205, 225, 10)
        self.art_page = ui.widget(self.slide, 'view_art', 0, 0, 225, 198)
        self.lyric = ui.widget(self.slide, 'view_lyric', 0, 0, 225, 198)
        self.lrc = ui.widget(self.lyric, 'scroll_lrc', 0, 10, 225, 178)
        self.rows = [ui.widget(self.lrc, f'row{i}', 0, 20 * i, 225, 20, text=line) for i, (line, _) in enumerate(lyrics)]
        for row, (_, sec_) in zip(self.rows, lyrics):
            ui.ints[row, b'lrc_time'] = sec_
        struct.pack_into('<i', (C.c_char * 4).from_address(self.lrc + CONTENT_H), 0, 20 * len(lyrics))
        self.slider = ui.widget(win, 'slider_play', 66, 245, 243, 20)  # last: over the column, as stock's
        ui.ints[self.slider, b'max'], ui.ints[self.slider, b'value'] = 100, 0  # the asset's
        ui.top = win
        if opened:
            assert self.open() == 41

    def open(self):
        return self.fw.fn('ringnav_playing', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0x77)

    def playtime(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total

    def key(self, key):
        return self.fw.fn('navigation__np_key', C.c_int, C.c_void_p, C.c_uint)(self.win, key)

    def value(self):
        return self.ui.ints[self.slider, b'value']

    def seeks(self):
        """player_seek_time's track seconds, then each mclSetSeek's file seconds as ('set', s): a playing scrub's
        (seek.c seek_scrub) and a paused one's commit."""
        return [a[0] for _, a in self.fw.calls('player_seek_time')] + [('set', a[0]) for _, a in self.fw.calls('mclSetSeek')]

    def fill(self):
        return self.ui.prop(self.slider, b'style:normal:fg_color') & 0xffffffff


@pytest.fixture
def now(fw, ui, mcl, st):
    theme(fw, st)
    return Now(fw, ui, mcl, st)


def test_the_page_shows_its_album_and_time_left_and_no_place_in_the_queue(fw, ui, now):
    assert (ui.text(now.album), ui.text(now.remain)) == ('Blue Train', '-2:30')
    assert not [t for _, t in ui.text_log if ' of ' in t]  # no "3 of 12"
    sets = [(w, key) for w, key, _ in ui.int_log if w == now.slider]
    assert sets.index((now.slider, b'max')) < sets.index((now.slider, b'value'))  # the max first, or it clamps
    assert now.value() == 50 and ui.ints[now.slider, b'max'] == 200


def test_the_labels_are_written_only_when_their_source_changes(fw, ui, mcl, now, st):
    sync = fw.fn('np_sync', C.c_int, C.c_void_p)
    writes = len(fw.calls('widget_set_text_utf8'))
    assert sync(now.win) == 0 and len(fw.calls('widget_set_text_utf8')) == writes
    ui.ints[now.slider, b'value'] = 60
    assert sync(now.win) == 1 and ui.text(now.remain) == '-2:20'
    mcl.pos = 3
    sync(now.win)
    assert ui.text(now.album) == ''  # the parsed tags are another track's
    assert sync(ui.wm) == 0


def test_a_slider_past_its_end_shows_no_negative_time(fw, ui, mcl, now):
    mcl.pos = 12
    ui.ints[now.slider, b'value'] = 260
    fw.fn('np_sync', C.c_int, C.c_void_p)(now.win)
    assert ui.text(now.remain) == '-0:00'


def test_the_bar_takes_its_themes_height_and_ends_and_the_glyph_and_album_their_opacity(fw, ui, mcl, st):
    t = OLED
    theme(fw, st)
    opacity = {}
    fw.mock('widget_set_opacity', lambda w, v, *_: opacity.__setitem__(w, v))
    now = Now(fw, ui, mcl, st)
    assert ui.ints[now.slider, b'bar_size'] == t['np_bar']
    # round ends: stock's slider rounds nothing under a round_radius of 4 (0x62dd14), and OLED's bar is 6 px
    assert t['np_bar'] == 6
    assert {ui.ints[now.slider, k] for k in (b'style:normal:round_radius', b'style:focused:round_radius')} == {4}
    assert (opacity[now.state], opacity[now.album]) == (t['np_state_op'], t['np_album_op'])


TRACK_KEYS = [f'style:{s}:bg_color'.encode() for s in ('normal', 'pressed', 'over', 'disable', 'focused')]


def rgba_int(c):
    """theme.h's RGBA(c) as widget_set_prop_int's int."""
    return C.c_int(0xff000000 | (c & 255) << 16 | (c & 0xff00) | c >> 16).value


def test_oled_writes_its_own_track(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st)
    assert OLED['np_track_rgb'] == 0x333333 != O['TRACK_COLOR']
    assert [ui.ints.get((now.slider, k)) for k in TRACK_KEYS] == [rgba_int(0x333333)] * 5


def test_a_theme_s_own_track_is_written_and_the_asset_s_written_back_once(fw, ui, mcl, st, library):
    """playing.scrubber.track, as a theme's table states it; then the table on TRACK_COLOR, the asset's (theme.xml
    without a track), which is written back once and then not at all."""
    theme(fw, st)
    track = fw.fn('theme', C.c_void_p)() + host.offsets(library.parent/'navigation.o', 'theme_t')['np_track_rgb']
    assert not libc.mprotect(track & ~0xfff, 0x2000, 3)  # this test's own load of the table, made writable
    C.c_uint.from_address(track).value = 0x262626
    now = Now(fw, ui, mcl, st)
    assert [ui.ints.get((now.slider, k)) for k in TRACK_KEYS] == [rgba_int(0x262626)] * 5
    C.c_uint.from_address(track).value = O['TRACK_COLOR']
    np_theme = fw.fn('np_theme', None)
    np_theme()
    assert [ui.ints.pop((now.slider, k)) for k in TRACK_KEYS] == [rgba_int(O['TRACK_COLOR'])] * 5
    np_theme()
    assert not [k for k in TRACK_KEYS if (now.slider, k) in ui.ints]


def test_no_window_binds_nothing(fw, ui, mcl, st):
    theme(fw, st)
    Now(fw, ui, mcl, st, opened=False)
    assert fw.fn('ringnav_playing', C.c_int, C.c_void_p, C.c_void_p)(None, 0) == 41
    assert st.np_win == 0 and ui.handlers == []


def test_a_page_stock_refused_binds_nothing(fw, ui, mcl, st):
    """Headphones required with none to play to: stock toasts, closes the window it was given and returns STOP (11)."""
    theme(fw, st)
    now = Now(fw, ui, mcl, st, opened=False)
    fw.mock('stock_playing_trampoline', lambda w, ctx, *_: 11)
    assert now.open() == 11
    assert st.np_win == 0 and ui.handlers == []


def stack_with(fw, ui, *names):
    """Windows under the one Now opens, bottom first; a forced close takes one off the stack, as AWTK's does."""
    under = [ui.widget(ui.wm, name, kind='window') for name in names]
    fw.mock('window_manager_close_window_force', lambda wm, w, *_: ui.kids[ui.wm].remove(w))
    return under


def test_a_new_now_playing_closes_the_one_it_was_opened_over(fw, ui, mcl, st):
    """Stock's navigator opens a playing_page over whatever is in front, so Shuffle on the artist page Now
    Playing's artist tap opened left the first Now Playing under it: covered, it kept the song it last showed,
    and Return came back to it before Home (the emulator, 2026-11-17: Song 1 at 1:01 while Song 2 played). The
    new page closes the older one as it opens; the pages between stay, so Return goes to the artist and Home."""
    theme(fw, st)
    home, old, artist = stack_with(fw, ui, 'home_page', 'playing_page', 'artistinfo_page')
    now = Now(fw, ui, mcl, st)
    assert [a[1] for _, a in fw.calls('window_manager_close_window_force')] == [old]
    assert ui.kids[ui.wm] == [home, artist, now.win] and st.np_win == now.win


def test_the_only_now_playing_closes_nothing(fw, ui, mcl, st):
    theme(fw, st)
    stack_with(fw, ui, 'home_page', 'allmusic_page')
    Now(fw, ui, mcl, st)
    assert not fw.calls('window_manager_close_window_force')


def test_a_now_playing_stock_refused_leaves_the_one_under_it(fw, ui, mcl, st):
    """Headphones required with none to play to: stock closes the new page itself, and the one the user had stays."""
    theme(fw, st)
    stack_with(fw, ui, 'home_page', 'playing_page', 'artistinfo_page')
    now = Now(fw, ui, mcl, st, opened=False)
    fw.mock('stock_playing_trampoline', lambda w, ctx, *_: 11)
    assert now.open() == 11
    assert not fw.calls('window_manager_close_window_force')


# Scrub: a Centre press starts it, the wheel previews, one press commits.
def press(now, at):
    now.ui.now = at
    return now.key(KEY_CENTER)


def scrub(now, at=200000):
    assert press(now, at) == STOP and now.st.scrub == 1


def test_a_press_starts_a_scrub_on_the_held_bar_in_the_accent(fw, ui, now, st):
    accent = now.fill()
    scrub(now)
    assert now.fill() == accent  # a status row's set marks it with a knob (paint_scrubber), not a white fill
    assert len(fw.calls('playing_timer_clear')) == 1 and [ui.timers[t][2] for t in ui.due('scrub_expire')] == [O['SCRUB_MS']]


def test_the_wheel_moves_the_target_in_steps_the_track_follows_and_one_press_ends_there(fw, ui, now, st):
    scrub(now)
    step = O['SCRUB_STEP']
    for _ in range(3):
        ui.now += 300  # slow ticks: a step each, each heard (SCRUB_SEEK_MS apart and more)
        assert now.key(KEY_NEXT) == STOP
    to = now.sec + 3 * step
    assert now.value() == to and ui.text(now.elapsed) == clock(to) and ui.text(now.remain) == '-' + clock(now.total - to)
    assert now.seeks() == [('set', now.sec + step), ('set', now.sec + 2 * step), ('set', to)]
    ui.now += 300
    now.key(KEY_PREV)
    assert now.value() == to - step
    press(now, ui.now + 1000)
    assert now.seeks()[-1] == ('set', to - step) and len(now.seeks()) == 4  # the player is there: no seek at the end
    assert st.scrub == 0 and now.fill() != 0xffffffff
    assert len(ui.due('settle_poll')) == 1  # the bar holds the target until the player gets there


SCRUB_SEEK_MS = 200  # seek.c


def test_a_fast_spin_is_heard_at_most_every_scrub_seek_ms_and_lands_on_the_target(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, sec=0, total=3000)
    scrub(now)
    sent = []  # (when, file second)
    fw.mock('mclSetSeek', lambda s, *_: sent.append((ui.now, s)))
    armed = {}  # timer: when it was added
    for _ in range(25):
        ui.now += 40
        for t in ui.due('scrub_due'):
            if ui.now >= armed.setdefault(t, ui.now - 40) + ui.timers[t][2]:
                ui.run(t)
        now.key(KEY_NEXT)
        for t in ui.due('scrub_due'):
            armed.setdefault(t, ui.now)
    gaps = [b[0] - a[0] for a, b in zip(sent, sent[1:])]
    assert sent and len(sent) <= 25 * 40 // SCRUB_SEEK_MS + 1 and min(gaps) >= SCRUB_SEEK_MS - 40, sent
    assert sent[0] == (ui.now - 24 * 40, O['SCRUB_STEP'])  # the first tick at once
    press(now, ui.now + 50)
    assert sent[-1][1] == now.value() and st.scrub == 0  # the end goes to the last target if the player isn't there


def test_a_paused_scrub_is_silent_until_it_ends(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, status=3)
    scrub(now)
    for _ in range(3):
        ui.now += 300
        now.key(KEY_NEXT)
    assert now.seeks() == [] and ui.due('scrub_due') == []
    press(now, ui.now + 1000)
    assert now.seeks() == [('set', now.sec + 3 * O['SCRUB_STEP'])]


def test_a_fast_spin_steps_further_than_slow_ticks(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, sec=0, total=3000)
    moved = []
    for gap in (200, 40):
        ui.now += 1000
        scrub(now, ui.now)
        start = now.value()
        for _ in range(12):
            ui.now += gap
            now.key(KEY_NEXT)
        moved.append(now.value() - start)
        press(now, ui.now + 1000)
        fw.fn('navigation__np_cancel', None)()  # Return: out of the Centre cycle, so the next press scrubs
    slow, fast = moved
    assert slow == 12 * O['SCRUB_STEP'] < fast


@pytest.mark.parametrize('key, start, end', [(KEY_NEXT, 199, 200), (KEY_PREV, 1, 0)])
def test_the_target_stays_within_the_track(fw, ui, mcl, st, key, start, end):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, sec=start, total=200)  # a second from either end: a step would pass it
    scrub(now)
    ui.now += 300
    now.key(key)
    assert now.value() == end


def test_a_scrub_that_never_moved_seeks_nowhere_and_the_timer_comes_back(fw, ui, now):
    scrub(now)
    press(now, ui.now + 1000)
    assert now.seeks() == [] and len(fw.calls('playing_timer_start')) == 1 and ui.due('settle_poll') == []


def test_a_scrub_ends_by_itself_after_its_time_and_seeks(fw, ui, now, st):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    expire, = ui.due('scrub_expire')
    ui.run(expire)
    assert st.scrub == 0 and now.seeks() == [('set', now.sec + O['SCRUB_STEP'])]  # heard at the tick, not again


def test_another_track_drops_the_scrub_without_a_seek(fw, ui, mcl, now, st):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    mcl.pos = 5
    assert now.key(KEY_NEXT) == STOP
    assert st.scrub == 0 and now.seeks() == [('set', now.sec + O['SCRUB_STEP'])]  # the tick's own, before it changed


def test_a_paused_scrub_seeks_on_play_and_the_bar_holds_its_target(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, status=3)
    cue = 600
    struct.pack_into('<i', now.recs[2], O['REC_CUE_START'], cue)  # a CUE track: file seconds
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    to = now.sec + O['SCRUB_STEP']
    assert now.seeks() == [('set', cue + to)] and st.held_sec == now.sec + 1  # the player's second then, +1
    settle, = ui.due('settle_poll')
    for _ in range(20):
        assert ui.run(settle) == RET_REPEAT  # paused: the player's second stands still
    assert fw.calls('playing_timer_start') == []
    now.status, now.sec = 2, to  # Play: the player gets there
    assert ui.run(settle) == 0 and len(fw.calls('playing_timer_start')) == 1


def test_a_seek_the_player_never_reaches_gives_up_after_a_while_playing(fw, ui, now):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    settle, = ui.due('settle_poll')
    period, runs = ui.timers[settle][2], 1
    while ui.run(settle) == RET_REPEAT:
        runs += 1
    # SETTLE_PLAY_MS of playing short of the target, then stock's timer has the bar back
    assert runs > 1 and runs * period == NAV['SETTLE_PLAY_MS'] and len(fw.calls('playing_timer_start')) == 1


def test_reopening_the_page_mid_wait_shows_the_target_again(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, status=3)
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    ui.emit(now.win, O['EVT_DESTROY'], 0)
    again = Now(fw, ui, mcl, st, status=3)
    to = now.sec + O['SCRUB_STEP']
    assert again.value() == to and ui.text(again.elapsed) == clock(to) and len(ui.due('settle_poll')) == 1
    third = Now(fw, ui, mcl, st, status=2)  # playing again: the target is gone
    assert third.value() == now.sec and st.held_sec == 0


# Centre, after an iPod classic's: a press scrubs, the next puts a focus ring on shuffle; the wheel moves the ring across
# shuffle, repeat and the heart and changes nothing, and Centre works the ringed control. A short hold turns the
# screen off.
NP_HOLD_MS, NP_STAGE_MS = (int(re.findall(rf'#define {n} (\w+)', NAVIGATION_SOURCE)[0].replace('SCRUB_MS', str(O['SCRUB_MS'])))
                           for n in ('NP_HOLD_MS', 'NP_STAGE_MS'))
REST, SHUFFLE, REPEAT, HEART = range(4)


def cpress(now, at, held=60):
    """Centre down at - held, up at at"""
    now.st.center_down_at = at - held
    return press(now, at)


def ringed(fw, ui, mcl, st, heart=True):
    """Now Playing under OLED, with the heart on the art page when heart, the play mode Order, scrubbed and
    then a Centre press on: the ring on shuffle. The modes stock is asked for go to the list returned."""
    theme(fw, st)
    releases, modes = [], []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: releases.append(1))
    fw.mock('config_playmode', lambda m, notify, *_: modes.append(m) or setattr(mcl, 'mode', m) or 0)
    now = Now(fw, ui, mcl, st, opened=False)
    if heart:
        fav = ui.widget(now.art_page, 'img_fav', 180, 150, 40, 40)
        ui.set_str(fav, b'image', b'play_unfav')
    assert now.open() == 41
    mcl.mode = 0
    assert cpress(now, 200000) == STOP and st.scrub == 1
    cpress(now, 200500)  # the scrub ends, the ring on shuffle
    assert st.scrub == 0 and st.np_stage == SHUFFLE
    return now, modes, releases


def test_the_wheel_moves_the_ring_and_changes_nothing(fw, ui, mcl, st):
    now, modes, _ = ringed(fw, ui, mcl, st)
    assert now.key(KEY_NEXT) == STOP and st.np_stage == REPEAT and modes == []  # shuffle stays off
    now.key(KEY_NEXT)
    assert st.np_stage == HEART and modes == []
    now.key(KEY_NEXT)  # the last stop: the ring stays
    assert st.np_stage == HEART
    now.key(KEY_PREV), now.key(KEY_PREV), now.key(KEY_PREV)
    assert st.np_stage == SHUFFLE and modes == []
    (t,) = ui.due('np_stage_end')  # each tick restarts the stage's timeout
    assert ui.timers[t][2] == NP_STAGE_MS


def test_centre_works_the_ringed_control_and_the_ring_stays(fw, ui, mcl, st):
    clicks = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: clicks.append(w) or 0)
    now, modes, releases = ringed(fw, ui, mcl, st)
    cpress(now, 201000)
    assert modes == [2] and st.np_stage == SHUFFLE  # shuffle on, the ring still on it
    now.key(KEY_NEXT)
    cpress(now, 201500)  # repeat All under shuffle: the mode stays Shuffle
    cpress(now, 202000)  # Repeat One
    assert modes == [2, 1] and st.np_stage == REPEAT
    now.key(KEY_NEXT)
    cpress(now, 202500)  # the heart: a tap on it, its like on or off (no rating picker: the rating page has the stars)
    fav = ui.lookup(now.art_page, "img_fav", 1)
    assert clicks == [fav] and st.np_stage == HEART and releases == [] and modes == [2, 1]
    assert [a[1:3] for _, a in fw.calls('pointer_event_init')][-1:] == [(O['EVT_CLICK'], fav)]  # a click on the heart
    assert not fw.calls('navigator_to')


def test_without_the_heart_the_ring_ends_at_repeat(fw, ui, mcl, st):
    now, modes, _ = ringed(fw, ui, mcl, st, heart=False)
    now.key(KEY_NEXT), now.key(KEY_NEXT)
    assert st.np_stage == REPEAT and modes == []


def test_a_stage_ends_by_itself_and_return_ends_it(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st)
    cpress(now, 200000)
    cpress(now, 200500)
    assert st.np_stage == SHUFFLE
    (t,) = ui.due('np_stage_end')
    assert ui.timers[t][2] == NP_STAGE_MS
    ui.run(t)
    assert st.np_stage == REST
    cpress(now, 210000)
    cpress(now, 210500)
    fw.fn('navigation__np_cancel', None)()  # Return, a touch, another window
    assert st.np_stage == REST and not ui.due('np_stage_end')


def test_a_short_hold_turns_the_screen_off(fw, ui, mcl, st):
    theme(fw, st)
    releases = []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: releases.append(1))
    now = Now(fw, ui, mcl, st)
    assert cpress(now, 200000, held=NP_HOLD_MS) == STOP and releases == [1] and st.scrub == 0
    cpress(now, 201000, held=NP_HOLD_MS - 50)  # shorter: a press
    assert st.scrub == 1 and releases == [1]
    cpress(now, 201500, held=NP_HOLD_MS)  # held while scrubbing: the scrub ends, the screen stays on
    assert st.scrub == 0 and releases == [1]


@pytest.mark.parametrize('gap', [120, O['DOUBLE_CLICK_MS'] - 1])
def test_a_double_press_turns_the_screen_off_and_a_single_still_scrubs(fw, ui, mcl, st, gap):
    """docs/changelog.md (OLED the one theme): on Now Playing a single Center press starts seeking and a double press
    turns the screen off, as on every other page. The user, 2026-11-19: the double press went into the select mode
    (the first press's scrub, the second's ring on shuffle). The first press still scrubs at once; a second within
    DOUBLE_CLICK_MS of it, the wheel not moved between, undoes that scrub unseeked and turns the screen off."""
    theme(fw, st)
    releases = []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: releases.append(1))
    now = Now(fw, ui, mcl, st)
    assert cpress(now, 200000) == STOP and st.scrub == 1 and releases == []  # a single press: seeking, at once
    assert cpress(now, 200000 + gap) == STOP
    assert releases == [1] and st.scrub == 0 and st.np_stage == REST and not st.scrub_moved
    assert not fw.calls('player_seek_time') and not fw.calls('mclSetSeek')  # the first press's scrub moved nothing
    cpress(now, 210000)  # a press later on: a scrub again, and one after DOUBLE_CLICK_MS the ring, as before
    cpress(now, 210000 + O['DOUBLE_CLICK_MS'])
    assert st.np_stage == SHUFFLE and releases == [1]
    now.fw.fn('navigation__np_cancel', None)()
    cpress(now, 220000)
    now.key(KEY_PREV)  # a turn between, even one against the bar's start that moves nothing: the scrub's own press
    cpress(now, 220100)
    assert st.np_stage == SHUFFLE and releases == [1]


def test_a_short_hold_is_timed_from_the_press_not_its_repeats(fw, ui, mcl, st):
    """A Q2's held Centre repeats its key-down every 50 ms after about 258 ms; each passes chord_gate and
    ringnav_keydown, and the hold is still the press's."""
    theme(fw, st)
    releases = []
    fw.mock('on_wm_keyup_fun', lambda w, event, *_: releases.append(1))
    fw.mock('stock_keydown_trampoline', lambda *_: 0)
    now = Now(fw, ui, mcl, st)
    gate = fw.fn('navigation__chord_gate', C.c_int, C.c_void_p)
    keydown = fw.fn('ringnav_keydown', C.c_int, C.c_void_p, C.c_void_p)

    def down(at):
        ui.now = at
        e = key_event(KEY_CENTER, DOWN)
        gate(e)
        keydown(None, e)

    for at in [200000] + list(range(200258, 200800, 50)):
        down(at)
    gate(key_event(KEY_CENTER, UP))
    assert press(now, 200800) == STOP and releases == [1] and st.scrub == 0
    down(202000)  # the next press is timed afresh: a tap
    gate(key_event(KEY_CENTER, UP))
    assert press(now, 202080) == STOP and releases == [1] and st.scrub == 1


def test_a_scrub_closes_the_volume_band_over_the_page_first(fw, ui, now, st):
    vol = ui.widget(ui.wm, 'volume_dialog', 0, 200, 375, 120, kind='dialog')
    ui.set_str(vol, b'name', b'volume_dialog')
    ui.top = vol
    fw.fn('navigation__scrub_start', None)()
    assert [a[0] for _, a in fw.calls('window_close')] == [vol] and st.scrub == 1


def test_a_volume_dialog_over_another_page_stays(fw, ui, now, st):
    ui.widget(ui.wm, 'home_page', kind='window')
    vol = ui.widget(ui.wm, 'volume_dialog', kind='dialog')
    ui.set_str(vol, b'name', b'volume_dialog')
    ui.top = vol
    fw.fn('navigation__scrub_start', None)()
    assert fw.calls('window_close') == [] and st.scrub == 1


def test_the_page_going_mid_scrub_seeks_nowhere_and_forgets_it(fw, ui, now, st):
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    ui.emit(ui.wm, O['EVT_DESTROY'], 0)  # another window's destroy: nothing
    assert st.np_win == now.win
    ui.emit(now.win, O['EVT_DESTROY'], 0)
    assert (st.np_win, st.np_slider, st.np_lrc, st.scrub) == (0, 0, 0, 0)
    assert now.seeks() == [('set', now.sec + O['SCRUB_STEP'])] and ui.due('scrub_expire') == []  # the tick's own only


def test_closing_mid_wait_restarts_stocks_timer(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, status=3)
    scrub(now)
    ui.now += 300
    now.key(KEY_NEXT)
    press(now, ui.now + 1000)
    starts = len(fw.calls('playing_timer_start'))
    fw.fn('navigation__np_cancel', None)()
    assert ui.due('settle_poll') == [] and len(fw.calls('playing_timer_start')) == starts + 1


# The art follows the player's cover (np_art_poll).
def test_the_art_reloads_when_the_players_cover_changes(fw, ui, now, st):
    poll = fw.fn('navigation__np_art_poll', None)
    def unloads():
        return [C.string_at(a[1]) for _, a in fw.calls('image_manager_unload_bitmap_by_name')]
    poll()
    assert unloads() == []  # stock's init loaded this one
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = now.paths[2].value
    fw.var('g_playcover_type', C.c_ubyte).value = 1
    poll()
    assert unloads() == [b'file:///tmp/coverpic.jpg'] and ui.prop(now.cover, b'image') == b'file:///tmp/coverpic.jpg'
    poll()
    assert len(unloads()) == 1
    fw.var('g_playcover_type', C.c_ubyte).value = 3
    poll()
    assert ui.prop(now.cover, b'image') == b'play_defaultcover'
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    fw.var('g_playcover_type', C.c_ubyte).value = 2
    poll()
    assert ui.prop(now.cover, b'image') == b'play_defaultcover'  # dark: the next lit pass


# A card app's URL queue: every song's REC_PATH is "" and a cover by URL leaves g_lastcover_url "", so the type and the
# track's path are the same for every song; only the file stock's cover thread wrote (its size and time) tells their
# art apart. The thread clears its request before it downloads, and a cover asked for mid-download lands after it.
@in_fork
def test_each_cover_a_url_queue_downloads_reloads_the_art_dark_or_lit(fw, ui, now, st):
    poll = fw.fn('navigation__np_art_poll', None)
    def unloads():
        return len(fw.calls('image_manager_unload_bitmap_by_name'))
    for path in now.paths:
        path.value = b''
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b''
    kind, lit, pic = fw.var('g_playcover_type', C.c_ubyte), fw.var('g_backlight_status', C.c_ubyte), Externpic(fw)
    def downloaded(size=20000):
        pic.write(size)
        kind.value = 2
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 1 and ui.prop(now.cover, b'image') == b'file:///tmp/externpic.jpg'
    pic.write(8000)  # read mid-write: part of the file there
    poll()
    assert unloads() == 2
    pic.grow(20000)  # the rest, in the same tick: only the size tells
    poll()
    poll()
    assert unloads() == 3 and st.np_art == fw.fn('navigation__np_art', C.c_uint, C.c_void_p)(C.byref(C.c_char_p()))
    lit.value = 0
    now.mcl.pos = 3  # the next song while the screen is dark: no pass sees its cover asked for
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 3
    lit.value = 1
    poll()
    assert unloads() == 4  # its own art at the first lit pass
    poll()
    now.mcl.pos = 4  # lit, and no pass while its cover downloads
    poll()
    assert unloads() == 4  # the last song's file still: nothing new to show yet
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 5
    now.mcl.pos = 5  # a quick skip: asked while song 4's cover still downloads, which lands first (as large, the
    # same second: only the nanoseconds tell)
    ask_cover(fw)
    downloaded()
    poll()
    assert unloads() == 6
    kind.value = 0  # the thread takes song 5's request; no pass sees it
    downloaded(31000)
    poll()
    assert unloads() == 7
    now.mcl.pos = 12  # stopped past the queue's end: no track, nothing read from one
    poll()
    assert unloads() == 7


# The progress bar's paint over stock's (np_bar.c paint_scrubber).
def scrubber(fw, w, canvas):
    fw.fn('paint_scrubber', None, C.c_void_p, C.c_void_p)(w, canvas.p)


# The glide between the player's whole seconds (np_bar.c paint_scrubber): the fill runs on from the value by the time
# since it changed, at most a second, while playing; a timer repaints it a pixel at a time.
def played(fw, now):
    """The glide's fill as one paint draws it over stock's: its width, or None where stock's own fill (the value's
    share) is all there is."""
    boxes = []
    fw.mock('canvas_fill_rounded_rect', lambda c, r, sr, color, radius, *_: boxes.append(
        struct.unpack_from('<4i', (C.c_char * 16).from_address(r))) or 0)
    scrubber(fw, now.slider, Canvas(fw))
    assert len(boxes) <= 1
    return boxes[0][2] if boxes else None


@pytest.mark.parametrize('status, later, ms', [(2, 500, 500), (2, 1700, 1000), (3, 500, 0)])
def test_the_fill_glides_on_from_the_value_while_playing_up_to_a_second(fw, ui, now, st, status, later, ms):
    now.status = status
    w = ui.box(now.slider)[2]
    assert played(fw, now) is None  # the value 50 of 200 seen at ui.now: stock's fill
    ui.now += later
    assert played(fw, now) == ((w * 50 + w * ms // 1000) // 200 if ms else None)


def test_a_new_value_starts_the_glide_again_and_never_goes_back(fw, ui, now, st):
    w = ui.box(now.slider)[2]
    played(fw, now)
    ui.now += 900
    before = played(fw, now)
    assert before > w * 50 // 200
    ui.ints[now.slider, b'value'] = 51  # stock's next second
    assert played(fw, now) is None and w * 51 // 200 >= before  # stock's fill, from the new value


def test_the_glide_repaints_the_slider_a_pixel_at_a_time(fw, ui, now, st):
    w = ui.box(now.slider)[2]
    scrubber(fw, now.slider, Canvas(fw))
    (t,) = ui.due('bar_glide')
    assert ui.timers[t][2] == max(33, min(1000, -(-200 * 1000 // w)))  # a 200 s track: ms per pixel, rounded up
    scrubber(fw, now.slider, Canvas(fw))
    assert len(ui.due('bar_glide')) == 1  # one at a time
    now.status = 3
    ui.run(t)
    scrubber(fw, now.slider, Canvas(fw))
    assert not ui.due('bar_glide')  # paused: nothing moves


def test_stock_sliders_fill_is_drawn_again_to_the_glide(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st)
    boxes = []
    fw.mock('canvas_fill_rounded_rect', lambda c, r, sr, color, radius, *_: boxes.append(
        (struct.unpack_from('<4i', (C.c_char * 16).from_address(r)), C.c_uint.from_address(color).value, radius)) or 0)
    ui.ints[now.slider, b'bar_size'] = 6
    w, h = ui.box(now.slider)[2], ui.box(now.slider)[3]
    scrubber(fw, now.slider, Canvas(fw))
    assert boxes == []  # no further than stock's own fill
    ui.now += 800
    scrubber(fw, now.slider, Canvas(fw))
    tone = fw.fn('accent_tone', C.c_uint, C.c_int)(2)  # navigation.h TONE_LIGHT
    rgba = 0xff000000 | (tone & 255) << 16 | tone & 0xff00 | tone >> 16 & 255  # RGBA: the canvas's byte order
    assert boxes == [((0, (h - 6) // 2, (w * 50 + w * 800 // 1000) // 200, 6), rgba, 3)]


def test_oleds_scrub_keeps_the_accent_and_marks_the_playhead_with_a_knob(fw, ui, mcl, st):
    """A status row's set (OLED's): the scrub keeps the accent's fill and puts a 14 px white knob on its end (audit N3)."""
    theme(fw, st)
    now = Now(fw, ui, mcl, st)
    boxes = []
    fw.mock('canvas_fill_rounded_rect', lambda c, r, sr, color, radius, *_: boxes.append(
        (struct.unpack_from('<4i', (C.c_char * 16).from_address(r)), C.c_uint.from_address(color).value, radius)) or 0)
    w, h = ui.box(now.slider)[2], ui.box(now.slider)[3]
    fw.fn('navigation__scrub_start', None)()
    assert now.fill() != 0xffffffff  # the accent's, not white
    scrubber(fw, now.slider, Canvas(fw))
    x = w * 50 // 200
    assert boxes == [((x - 7, (h - 14) // 2, 14, 14), 0xffffffff, 7)]


# The lyrics page (lyric_poll, every UI pass): the follow glides on the page, the wheel's raw travel nudges or
# takes them, a finger's drag holds them; a tap takes them full screen, over the whole window.
LINES = [(f'Line {i}', 10 * i) for i in range(12)]
SLOT = O['LYRIC_SLOT']
LRC_FAR_SEC = 0x7fffffff // 1000  # an untimed line's lrc_time (lyrics.c lyrics_rows: LRC_FAR's seconds)
LYRICS = host.defines('lyrics.c', 'LYRIC_')


def pitch(px):
    """lyrics.c LY_PITCH: a wrapped line's rows apart at px."""
    return px + px // 8


# A one-row line's row (lyrics.c lyrics_fit): its text at OLED's LYRIC_OLED_PX and the gap that makes a LYRIC_PX
# line's LYRIC_SLOT
ROW = pitch(LYRICS['LYRIC_OLED_PX']) + SLOT - pitch(LYRICS['LYRIC_PX'])


class Lyrics(Now):
    """Now Playing on its lyrics page, twelve lines ten seconds apart; AWTK's list_view layout puts the rows
    under a blank LYRIC_SLOT, each as high as lyrics.c sizes it."""
    def __init__(self, fw, ui, mcl, st, page=1, **kwargs):
        self.layouts, self.pressed, self.animating = {}, 0, 0
        fw.mock('widget_set_children_layout', lambda w, s, *_: self.layouts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_layout_children', lambda w, *_: self.layout(ui, w))
        fw.mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        fw.mock('window_manager_is_animating', lambda *_: self.animating)
        fw.mock('mclGetLyricSize', lambda *_: len(LINES))
        super().__init__(fw, ui, mcl, st, lyrics=kwargs.pop('lyrics', LINES), **kwargs)
        ui.ints[self.slide, b'value'] = page
        self.poll_fn = fw.fn('navigation__lyric_poll', None)
        fw.mock('widget_to_local', self.to_local)

    def to_local(self, w, xy, *_):
        """AWTK's: the point less w's place on the screen, plus scroll_lrc's offset (the rows' place in it)."""
        x, y, _, _ = self.ui.screen(w)
        if w == self.lrc:
            x, y = x - C.c_int.from_address(w + SCROLL_X).value, y - self.y()
        point = (C.c_int * 2).from_address(xy)
        point[0] -= x
        point[1] -= y

    def layout(self, ui, w):
        m = re.fullmatch(r'list_view\(x=0,y=(\d+),s=0\)', self.layouts.get(w, ''))
        y = int(m[1]) if m else 0
        for k in ui.kids.get(w, ()):
            x, _, kw, kh = ui.box(k)
            ui.place(k, x, y, kw, kh)
            y += kh

    def poll(self, passes=1, ms=25):
        offsets = []
        for _ in range(passes):
            self.ui.now += ms
            self.poll_fn()
            offsets.append(self.y())
        return offsets

    def y(self):
        return C.c_int.from_address(self.lrc + SCROLL_Y).value

    def scroll(self, y):
        C.c_int.from_address(self.lrc + SCROLL_Y).value = y

    def settle(self):
        offsets = self.poll(60)
        assert offsets[-1] == offsets[-2] and self.st.lyric_glide == 0
        return offsets[-1]


def centre(line):
    """lyric_centre: row i's middle on the view's (178 px) middle, the rows ROW apart after a blank SLOT (C's
    division, toward zero)."""
    return SLOT + ROW * line - (178 - ROW) // 2


def snap(y):
    """lyric_snap: the row top nearest y (0 the first candidate, an earlier one kept on a tie)."""
    return min([0] + [SLOT + ROW * i for i in range(len(LINES))], key=lambda top: abs(top - y))


@pytest.fixture
def wheel(fw):
    travel, held = fw.var('wheel_travel', C.c_int), fw.var('wheel_held', C.c_int)
    yield travel, held
    travel.value = held.value = 0
    fw.var('wheel_raw', C.c_int).value = 0


def test_the_lyrics_page_glides_its_playing_line_to_the_middle_easing_in(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    offsets = lyr.poll(40)
    assert offsets[-1] == centre(3) and st.lyric_glide == 0
    steps = [b - a for a, b in zip(offsets, offsets[1:]) if b != a]
    assert all(s > 0 for s in steps) and steps[1:] == sorted(steps[1:], reverse=True) and steps[1] > 1
    assert fw.var('wheel_raw', C.c_int).value == 1  # the page holds the wheel's raw travel
    lyr.sec = 75
    assert lyr.poll(40)[-1] == centre(7)


def test_off_the_page_the_follow_jumps_at_stocks_pace(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35, page=0)
    assert lyr.poll(1) == [centre(3)]  # at once, no glide
    lyr.sec = 55
    assert lyr.poll(1, ms=100) == [centre(3)] and lyr.poll(1, ms=150) == [centre(5)]
    assert fw.var('wheel_raw', C.c_int).value == 0


def test_a_turn_takes_the_lyrics_a_line_a_ticks_worth_and_holds_them(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    travel, held = wheel
    detents, tick = O['LYRIC_PUSH_DETENTS'], O['WHEEL_TICK_UNITS']  # at 50% sensitivity, a tick's raw units
    travel.value = -detents * tick  # clockwise: on down the lyrics, as far as a push takes
    held.value = 1
    lyr.poll(1)
    held.value = 0
    taken = snap(centre(3) + detents * ROW)  # a tick's worth is one row (lyrics_row_h), then the nearest row top
    assert abs(taken - centre(3) - detents * ROW) <= ROW // 2  # as far as the ticks take, a row each
    assert lyr.settle() == taken and st.lyric_held == 1
    lyr.sec = 95
    assert lyr.settle() == taken  # held: the follow leaves them
    travel.value = (detents + 1) * tick  # anticlockwise: back up, a line a tick
    lyr.poll(1)
    assert lyr.settle() == taken - (detents + 1) * ROW  # rows' tops, a row a tick


def test_a_held_turn_moves_the_lyrics_one_oled_row_a_tick_before_any_snap(fw, ui, mcl, st, wheel):
    """lyric_poll: while the thumb stays on the wheel nothing snaps, so a tick's worth of travel shows bare: one of
    OLED's one-row lines (lyrics_row_h, 51 px), not stock's LYRIC_SLOT (59), which the snap would then pull back."""
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    start = lyr.settle()
    assert ROW != SLOT  # else this test tells nothing
    travel, held = wheel
    held.value = 1
    ticks = O['LYRIC_PUSH_DETENTS'] + 1
    travel.value = -ticks * O['WHEEL_TICK_UNITS']  # clockwise, past a push: taken, a row a tick
    lyr.poll(1)
    assert lyr.settle() == start + ticks * ROW


def test_a_small_turn_only_nudges_and_springs_back_still_following(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    home = lyr.settle()
    travel, held = wheel
    held.value = 1
    for _ in range(2):  # two small turns, together short of a push: the nudge keeps its home
        travel.value = -(O['LYRIC_PUSH_DETENTS'] * O['WHEEL_TICK_UNITS'] // 3)
        lyr.poll(1)
        assert lyr.settle() == home + O['BUMP_PX']
    assert fw.calls('reset_poweroptions_timer') and st.lyric_held == 0
    held.value = 0
    assert lyr.settle() == home
    lyr.sec = 55
    assert lyr.settle() == centre(5)


def test_the_stock_rows_scroll_a_stock_row_a_tick(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    ui.widget(lyr.lrc, 'row12', 0, 0, 225, 20)  # stock rebuilt its rows: they are no longer the page's
    lyr.scroll(100)
    lyr.poll(1)  # the page takes the wheel raw
    travel, held = wheel
    travel.value, held.value = -2 * O['WHEEL_TICK_UNITS'], 1
    lyr.poll(1)
    assert lyr.settle() == 100 + 2 * 20 and st.lyric_held == 1


def test_a_drag_holds_the_lyrics_and_a_tap_holds_nothing(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    at = lyr.settle()
    lyr.pressed = 1
    lyr.poll(1)
    lyr.pressed = 0
    lyr.poll(1)
    assert st.lyric_held == 0
    lyr.pressed = 1
    lyr.poll(1)
    lyr.scroll(at + 70)  # the finger moved them
    lyr.sec = 55
    assert lyr.poll(3) == [at + 70] * 3  # a finger on the screen: no follow
    lyr.pressed = 0
    lyr.poll(1)
    assert st.lyric_held == 1 and lyr.settle() == at + 70


RESUME = 5000  # the owner's 5 s, LYRIC_RESUME_MS


def hold_by_wheel(lyr, wheel, ticks=None):
    """A turn past a push (clockwise, down the lyrics), the thumb let go in the same pass: held from this pass."""
    travel, held = wheel
    travel.value = -(ticks or O['LYRIC_PUSH_DETENTS'] + 1) * O['WHEEL_TICK_UNITS']
    lyr.poll(1)
    assert lyr.st.lyric_held == 1
    return lyr.ui.now


def rest(lyr, until, ms=25):
    """UI passes every ms until the clock reads until."""
    while lyr.ui.now + ms <= until:
        lyr.poll(1, ms=ms)
    if lyr.ui.now < until:
        lyr.poll(1, ms=until - lyr.ui.now)


def test_held_timed_lyrics_follow_again_after_resume_ms_of_rest_and_not_before(fw, ui, mcl, st, wheel):
    theme(fw, st)
    assert NAV.get('LYRIC_RESUME_MS') == RESUME
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    t0 = hold_by_wheel(lyr, wheel)
    taken = lyr.settle()
    lyr.sec = 55  # the song goes on two lines
    rest(lyr, t0 + RESUME - 100)
    assert st.lyric_held == 1 and lyr.y() == taken != centre(5)  # 4.9 s: still where the wheel left them
    rest(lyr, t0 + RESUME)
    assert st.lyric_held == 0  # 5 s: following again
    assert lyr.settle() == centre(5)
    lyr.sec = 75
    assert lyr.settle() == centre(7)  # and goes on with the song


def test_another_turn_or_a_touch_starts_the_rest_again(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    t0 = hold_by_wheel(lyr, wheel)
    rest(lyr, t0 + RESUME - 1000)
    t1 = hold_by_wheel(lyr, wheel, ticks=O['LYRIC_PUSH_DETENTS'] + 1)  # another turn before the 5 s
    rest(lyr, t0 + RESUME + 500)
    assert st.lyric_held == 1  # past the first turn's 5 s
    travel, held = wheel
    held.value = 1  # the thumb rests on the wheel: no rest while it stays
    rest(lyr, t1 + RESUME + 2000)
    assert st.lyric_held == 1
    held.value = 0
    lyr.poll(1)
    t2 = ui.now
    lyr.pressed = 1  # a finger on the page, held there, then lifted without moving them
    rest(lyr, t2 + 3000)
    lyr.pressed = 0
    lyr.poll(1)
    t3 = ui.now
    rest(lyr, t3 + RESUME - 100)
    assert st.lyric_held == 1
    rest(lyr, t3 + RESUME)
    assert st.lyric_held == 0


def test_held_untimed_lyrics_stay_where_they_were_left(fw, ui, mcl, st, wheel):
    theme(fw, st)
    untimed = [(f'Line {i}', LRC_FAR_SEC) for i in range(12)]
    lyr = Lyrics(fw, ui, mcl, st, sec=35, lyrics=untimed)
    lyr.settle()
    t0 = hold_by_wheel(lyr, wheel)
    taken = lyr.settle()
    rest(lyr, t0 + 3 * RESUME)
    assert st.lyric_held == 1 and lyr.y() == taken


def test_a_swipe_away_and_back_keeps_the_hold_and_a_new_song_starts_at_the_top(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    travel, _ = wheel
    travel.value = -100
    lyr.poll(1)
    held = lyr.settle()
    assert st.lyric_held == 1
    ui.ints[lyr.slide, b'value'] = 2  # the info page
    lyr.sec = 55  # the song goes on meanwhile
    lyr.poll(10, ms=100)  # back within LYRIC_RESUME_MS of the turn
    assert st.lyric_held == 1 and fw.var('wheel_raw', C.c_int).value == 0
    ui.ints[lyr.slide, b'value'] = 1
    assert lyr.settle() == held != centre(5)  # where the wheel left them, not the playing line
    mcl.pos = 4
    lyr.poll(1)
    assert st.lyric_held == 0 and lyr.y() < centre(3)  # the next song's lyrics from the top


def test_a_rest_away_on_another_page_counts_and_they_come_back_following(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    t0 = hold_by_wheel(lyr, wheel)
    ui.ints[lyr.slide, b'value'] = 0  # the art page for longer than the rest
    lyr.sec = 55
    rest(lyr, t0 + RESUME, ms=250)
    assert st.lyric_held == 0 and lyr.y() == centre(5)  # off the page the follow jumps
    ui.ints[lyr.slide, b'value'] = 1
    assert lyr.settle() == centre(5)


@pytest.mark.parametrize('end', ['gone', 'replaced'])
def test_now_playing_going_or_replaced_drops_the_lyrics_hold(fw, ui, mcl, st, wheel, end):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    travel, _ = wheel
    travel.value = -100
    lyr.poll(1)
    assert st.lyric_held == 1
    if end == 'gone':
        ui.emit(lyr.win, O['EVT_DESTROY'], 0)
    else:  # another Now Playing over this one, which stays: np_gone leaves a page it replaces alone
        Lyrics(fw, ui, mcl, st, sec=35)
    assert st.lyric_held == 0


def test_a_tick_before_the_wheel_goes_raw_glides_to_the_next_rows_top(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    at = lyr.settle()
    lyrics = fw.fn('navigation__np_lyrics', C.c_int, C.c_uint)
    tops = sorted(ui.box(r)[1] for r in lyr.rows)
    assert lyrics(KEY_NEXT) == 1 and st.lyric_held == 1
    assert lyr.settle() == min(t for t in tops if t > at)
    assert lyrics(KEY_PREV) == 1
    assert lyr.settle() == max(t for t in tops if t < min(t for t in tops if t > at))
    for _ in range(len(tops)):
        lyrics(KEY_NEXT)
    assert lyr.settle() == C.c_int.from_address(lyr.lrc + CONTENT_H).value - 178  # no row past it: the end
    for _ in range(len(tops) + 1):
        lyrics(KEY_PREV)
    assert lyr.settle() == 0
    ui.ints[lyr.slide, b'value'] = 0
    assert lyrics(KEY_NEXT) == 0


def test_a_late_pass_glides_half_the_way_no_leap(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    first = lyr.poll(1)[0]  # the glide starts: a pixel
    late = lyr.poll(1, ms=400)[0]
    assert late - first == (centre(3) - first) * 50 // 100  # dt capped at LYRIC_EASE_MS: 50 / (50 + 50)
    on_time = lyr.poll(1, ms=25)[0]
    assert on_time - late == (centre(3) - late) * 25 // 75


def tap(ui, w, kind, x, y):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, 0, kind)
    struct.pack_into('<ii', event, O['EVENT_X'], x, y)
    return ui.emit(w, kind, C.addressof(event))


def tap_line(ui, lyr, y):
    """A tap on the lyrics at y in scroll_lrc's rows (its content's own place), where the screen shows it."""
    at = ui.screen(lyr.lrc)[1] - lyr.y() + y
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, at)
    return tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, at)


@pytest.mark.parametrize('end', ['far', 'drag', 'abort', 'page', 'reopen'])
def test_full_screen_lyrics_end_with_a_swipe_or_off_their_page(fw, ui, mcl, st, end):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 50)  # the blank slot over the first line
    tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 50)
    assert st.np_full == 2  # over the whole window
    tap_px = NAV['TAP_PX']
    if end == 'far':  # a lift far off, with no move between, is no tap
        tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 300, 100)
        tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)
        assert st.np_full == 2 and lyr.seeks() == []
        return
    if end == 'drag':  # scroll_lrc drags the lines: its STOP comes back as an abort, and any drag ends full screen
        tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
        tap(ui, lyr.lyric, O['EVT_POINTER_ABORT'], 100, 100 - tap_px)
        tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)  # back where it came down: no tap
        assert lyr.seeks() == []
    elif end == 'abort':  # the slide_view's, which takes the touch past TAP_PX sideways
        tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
        tap(ui, lyr.lyric, O['EVT_POINTER_ABORT'], 100 + tap_px + 1, 100)
    elif end == 'page':
        ui.ints[lyr.slide, b'value'] = 0
        lyr.poll(1)
    else:
        Lyrics(fw, ui, mcl, st, sec=35)
    assert st.np_full == 0 and ui.box(lyr.col)[::2] == (80, 225)


def test_without_lyrics_a_tap_stays_in_the_column(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, lyrics=())
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 100)
    tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, 100)
    assert st.np_full == 0 and fw.calls('widget_restack') == []


def test_a_tap_takes_the_lyrics_over_the_whole_window_and_back(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    places = []  # widget_set_self_layout_params' x, y, w and h, as the C passes them
    fw.mock('widget_set_self_layout_params',
            lambda w, *a: places.append((w, *(C.string_at(s).decode() for s in a[:4]))))
    view, (_, _, win_w, win_h) = ui.parent(lyr.lrc), ui.box(lyr.win)
    col, slide, dots, lines = ui.box(lyr.col), ui.box(lyr.slide), ui.box(lyr.dots), ui.box(view)
    at = ui.kids[lyr.win].index(lyr.col)
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, 50)  # the blank slot over the first line
    assert tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 108, 45) == [0]
    assert ui.box(lyr.col) == (0, 0, win_w, win_h) and st.np_full == 2  # 2: over the whole window, which the way back keeps
    assert ui.prop(lyr.col, b'style:normal:bg_color') == b'#000000'
    foot = ui.box(lyr.slide)[3]  # the pages down to the dots
    gap = win_h - foot
    assert ui.box(lyr.slide)[2] == win_w and gap == NAV['NP_FULL_FOOT']  # the dots that far above the window's foot
    # The dots at the pages' foot, their own height; the lines' view from the top, as high as its parent (the
    # pages) less the gap: the lines stop as far above the dots as the dots sit above the window's foot.
    assert places == [(lyr.dots, '0', str(foot), '100%', str(dots[3])), (view, '0', '0', '100%', str(-gap))]
    lyr.scroll(0)
    tap_line(ui, lyr, ui.box(lyr.rows[0])[1] // 2)  # the blank slot over the first line: the column back
    assert ui.box(lyr.col) == col and ui.box(lyr.slide) == slide and st.np_full == 0
    assert ui.prop(lyr.col, b'style:normal:bg_color') == b'#00000000'
    assert places[2:] == [(lyr.dots, '0', str(dots[1]), '100%', str(dots[3])),
                          (view, '0', str(lines[1]), '100%', str(lines[3]))]  # both self_layouts their old place
    assert [a[1] for _, a in fw.calls('widget_restack')] == [len(ui.kids[lyr.win]), at]


def row_y(line):
    """The middle of line's row in scroll_lrc's content (one-row lines, ROW apart after a blank SLOT); a tap hits it
    only while it is in the 178 px view."""
    return SLOT + ROW * line + ROW // 2


@pytest.mark.parametrize('status, expect', [(2, [40]), (3, [('set', 40)])])
def test_a_tap_on_a_timed_line_seeks_to_its_start_and_the_view_goes_there(fw, ui, mcl, st, status, expect):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35, status=status)
    lyr.settle()
    assert tap_line(ui, lyr, row_y(4)) == [0]  # the line under the playing one
    assert lyr.seeks() == expect and st.np_full == 0  # line 4's 40 s: playing at once, paused on Play
    assert lyr.value() == 40 and st.lyric_held == 0
    assert lyr.settle() == centre(4)  # before the player reports it: the tapped line, lit as it starts
    lyr.sec = 40
    assert lyr.settle() == centre(4) and st.lyric_seek == 0
    lyr.sec = 55
    assert lyr.settle() == centre(5)  # following on from there


def test_a_tap_off_the_playing_line_seeks_from_a_held_view_too(fw, ui, mcl, st, wheel):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    hold_by_wheel(lyr, wheel, ticks=4)
    at = lyr.settle()  # four rows on: line 3, playing, out of view
    assert at > row_y(3) and at <= row_y(7) - ROW // 2
    tap_line(ui, lyr, row_y(7))
    assert lyr.seeks() == [70] and st.lyric_held == 0
    assert lyr.settle() == centre(7)


def test_a_tap_on_the_playing_line_still_takes_them_full_screen(fw, ui, mcl, st):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    tap_line(ui, lyr, row_y(3))
    assert lyr.seeks() == [] and st.np_full == 2


@pytest.mark.parametrize('end', ['drag', 'far'])
def test_a_drag_of_the_lines_seeks_nowhere(fw, ui, mcl, st, end):
    theme(fw, st)
    lyr = Lyrics(fw, ui, mcl, st, sec=35)
    lyr.settle()
    at = ui.screen(lyr.lrc)[1] - lyr.y() + row_y(4)
    tap(ui, lyr.lyric, O['EVT_POINTER_DOWN'], 100, at)
    if end == 'drag':  # scroll_lrc takes the drag: its abort, then the lift where it came down
        tap(ui, lyr.lyric, O['EVT_POINTER_ABORT'], 100, at - NAV['TAP_PX'] - 1)
        tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, at)
    else:  # lifted over another line, past TAP_PX
        tap(ui, lyr.lyric, O['EVT_POINTER_UP_BEFORE'], 100, at + ROW)
    assert lyr.seeks() == [] and st.np_full == 0


@pytest.mark.parametrize('case', ['untimed', 'stopped'])
def test_untimed_lyrics_or_a_stopped_player_take_a_tap_full_screen_as_before(fw, ui, mcl, st, case):
    theme(fw, st)
    lines = [(f'Line {i}', LRC_FAR_SEC) for i in range(12)] if case == 'untimed' else LINES
    lyr = Lyrics(fw, ui, mcl, st, sec=35, lyrics=lines, status=1 if case == 'stopped' else 2)
    lyr.settle()
    tap_line(ui, lyr, row_y(4))
    assert lyr.seeks() == [] and st.np_full == 2


# Smart Awake (keep_lit_poll): Now Playing off its art page, a song playing, holds the screen on.
@pytest.mark.parametrize('keep, page, status, top, lit', [(1, 1, 2, 'np', 1), (1, 0, 2, 'np', 0), (1, 0, 2, 'art', 1),
                                                          (1, 1, 3, 'np', 0), (0, 1, 2, 'np', 0), (1, 1, 2, 'other', 0)])
def test_smart_awake_holds_the_screen_on_off_the_art_page_while_playing(fw, ui, mcl, st, keep, page, status, top, lit):
    theme(fw, st, KEEPLIT=keep)
    lyr = Lyrics(fw, ui, mcl, st, page=page, status=status)
    if top == 'art':
        ui.top = ui.widget(ui.wm, 'bigcover_page', kind='window')
        st.ref('bc', 2).value = ui.top
    elif top == 'other':
        ui.top = ui.widget(ui.wm, 'home_page', kind='window')
    poll = fw.fn('navigation__keep_lit_poll', None)
    ui.now += 5000
    poll()
    ui.now += 500
    poll()  # once a second
    assert len(fw.calls('reset_poweroptions_timer')) == lit
    assert fw.fn('ringnav_smart_awake', C.c_int)() == keep and st.np_slide == lyr.slide


# The full-screen art (ringnav_bigcover): the song name and its shade fade out, the art takes its cover's shape.
class Big:
    def __init__(self, fw, ui, mcl, st, image=b'default_bigcover', sizes=None):
        self.fw, self.ui, self.st = fw, ui, st
        self.now = Now(fw, ui, mcl, st)
        self.opacity = {}
        fw.mock('widget_set_opacity', lambda w, v, *_: self.opacity.setdefault(w, []).append(v))
        fw.mock('stock_bigcover_trampoline', lambda *_: 3)
        fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))
        self.canvas = Canvas(fw, sizes=sizes)  # widget_load_image's answers
        self.win = ui.widget(ui.wm, 'bigcover_page', 0, 0, 375, 320, kind='window')
        self.img = ui.widget(self.win, 'img_bigcover', 27, 0, 320, 320)
        self.shade = ui.widget(self.win, 'view_shade', 27, 260, 320, 60)
        self.title = ui.widget(self.win, 'scrlabel_title', 39, 280, 296, 30)
        ui.set_str(self.img, b'image', image)
        assert fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0) == 3

    def fit(self):
        self.fw.fn('navigation__bc_fit', None, C.c_void_p)(self.win)
        return self.ui.box(self.img)


def test_the_song_name_and_its_shade_show_then_fade_out_together(fw, ui, mcl, st):
    theme(fw, st)
    big = Big(fw, ui, mcl, st)
    first, = ui.due('bc_fade')
    assert ui.timers[first][2] == O['BC_MS']
    ui.run(first)
    assert [ui.timers[t][2] for t in ui.due('bc_fade')] == [O['BC_STEP_MS']]  # then a step each BC_STEP_MS
    ui.run_all('bc_fade')
    steps = O['BC_STEPS']
    want = [255] + [255 * (steps - n) // steps for n in range(1, steps + 1)]
    assert big.opacity[big.title] == big.opacity[big.shade] == want and st.bc_timer == 0


def test_a_new_track_shows_the_name_again_and_the_page_going_stops_it(fw, ui, mcl, st):
    theme(fw, st)
    big = Big(fw, ui, mcl, st)
    ui.run_all('bc_fade')
    fw.fn('navigation__bc_show', None)()
    assert big.opacity[big.title][-1] == 255 and len(ui.due('bc_fade')) == 1
    st.bc_repaint = ui.timer_add(0, 0, 0)
    ui.emit(big.win, O['EVT_DESTROY'], 0)
    assert ui.due('bc_fade') == [] and st.bc_repaint == 0 and st.ref('bc', 2).value is None


def clears_glass(x, y, w, h):
    """No corner of the box past the glass's rounded corner, GLASS_R round, GLASS_SLACK in from the side."""
    r, slack = O['GLASS_R'], O['GLASS_SLACK']
    dx, dy = r + slack - x, r - y
    return h <= 320 and not (dx > 0 and dy > 0 and dx * dx + dy * dy > r * r)


@pytest.mark.parametrize('size', [(400, 200), (200, 400), (500, 500), (300, 299)])
def test_the_art_is_the_largest_box_of_its_covers_shape_that_clears_the_glass(fw, ui, mcl, st, size):
    theme(fw, st)
    big = Big(fw, ui, mcl, st, image=b'cover.jpg', sizes={b'cover.jpg': size})
    x, y, w, h = big.fit()
    assert x == (375 - w) // 2 and y == (320 - h) // 2 and clears_glass(x, y, w, h)
    long_, short = max(w, h), min(w, h)
    assert abs(short / long_ - min(size) / max(size)) < 0.02 and (w >= h) == (size[0] >= size[1])
    s = long_ + 2  # the next size up would not clear it
    w2, h2 = (s, s * size[1] // size[0]) if size[0] >= size[1] else (s * size[0] // size[1], s)
    assert long_ == 374 or not clears_glass((375 - w2) // 2, (320 - h2) // 2, w2, h2)
    sx, sy, sw, sh = ui.box(big.shade)
    assert (sx, sy + sh, sw) == (x, y + h, w)  # on the art's foot
    tx, _, tw, _ = ui.box(big.title)
    assert tx == x + O['BC_TITLE_PAD'] and tw == w - 2 * O['BC_TITLE_PAD']
    assert [C.string_at(a[1]) for _, a in fw.calls('image_manager_unload_bitmap_by_name')] == [b'cover.jpg']
    repaint, = ui.due('bc_repaint')  # after the paint, which drops its own invalidates
    ui.run(repaint)
    assert ui.invalidated(big.win) == [big.win] and st.bc_repaint == 0


@pytest.mark.parametrize('image, sizes', [(b'default_bigcover', {}), (b'cover.jpg', {b'cover.jpg': (500, 100)}),
                                          (b'cover.jpg', {b'cover.jpg': None})])
def test_a_placeholder_a_cover_past_4_to_1_or_one_that_wont_load_is_square(fw, ui, mcl, st, image, sizes):
    theme(fw, st)
    big = Big(fw, ui, mcl, st, image=image, sizes=sizes)
    x, y, w, h = big.fit()
    assert w == h and clears_glass(x, y, w, h)


def test_the_box_is_fitted_once_until_the_track_or_image_changes(fw, ui, mcl, st):
    theme(fw, st)
    big = Big(fw, ui, mcl, st, image=b'cover.jpg', sizes={b'cover.jpg': (400, 200)})
    big.fit()
    loads, moves = len(big.canvas.loaded), len(fw.calls('widget_move_resize'))
    big.fit()
    assert len(big.canvas.loaded) == loads and len(fw.calls('widget_move_resize')) == moves
    big.ui.set_str(big.img, b'image', b'other.jpg')
    big.fit()
    assert big.canvas.loaded[-1] == b'other.jpg' and ui.box(big.img)[2] == ui.box(big.img)[3]


def test_the_players_file_waits_until_it_is_this_tracks_cover(fw, ui, mcl, st):
    theme(fw, st)
    big = Big(fw, ui, mcl, st, image=b'file:///tmp/coverpic.jpg', sizes={b'file:///tmp/coverpic.jpg': (400, 200)})
    x, y, w, h = big.fit()
    assert big.canvas.loaded == [] and w == h  # the last track's file: not decoded, the square
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = big.now.paths[2].value
    fw.var('g_playcover_type', C.c_ubyte).value = 1
    x, y, w, h = big.fit()
    assert big.canvas.loaded == [b'file:///tmp/coverpic.jpg'] and w > h


# A card app's URL queue (np_art's test): a paint between the song change and its cover's request decodes the last
# song's file, still in place; the next song's file, written after, is decoded once it is in.
@in_fork
def test_a_url_queues_next_cover_is_fitted_once_it_is_in(fw, ui, mcl, st):
    theme(fw, st)
    ext = b'file:///tmp/externpic.jpg'
    sizes = {ext: (400, 200)}
    big = Big(fw, ui, mcl, st, image=ext, sizes=sizes)
    for path in big.now.paths:
        path.value = b''
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b''
    kind, pic = fw.var('g_playcover_type', C.c_ubyte), Externpic(fw)
    ask_cover(fw)
    pic.write()
    kind.value = 2
    x, y, w, h = big.fit()
    assert w > h
    mcl.pos = 3
    x, y, w, h = big.fit()
    assert w > h and len(big.canvas.loaded) == 2  # the last song's file, the next one's not asked for yet
    ask_cover(fw)
    sizes[ext] = (200, 400)
    pic.write()
    kind.value = 2
    x, y, w, h = big.fit()
    assert h > w and len(big.canvas.loaded) == 3


# A unit an older build left on another theme (THEME 0 Minimal, 1 Classic, 2 Modern, or no key) takes OLED's preset
# once at boot and keeps THEME 3 from then on (theme_boot).
@pytest.mark.parametrize('stored, migrates', [(None, True), (0, True), (1, True), (2, True), (THEME_OLED, False)])
def test_a_unit_on_a_retired_theme_takes_oleds_preset_once(fw, st, stored, migrates):
    keys = {} if stored is None else {'THEME': stored}
    Config(fw, LIGHT=1, HOME=1, **keys)  # Light Mode on and Home Full: OLED's preset is dark and Split (theme.xml)
    st.settings_read = 0
    fw.fn('accent', C.c_int)()
    fw.fn('navigation__theme_boot', None)()
    writes = [(C.string_at(a[2]).decode(), C.c_int(a[0]).value) for _, a in fw.calls('write_int_config')]
    assert (('THEME', THEME_OLED) in writes) == migrates and writes.count(('THEME', THEME_OLED)) == migrates
    dark, split = (host.tools_theme.SETTINGS[k][1].index(v) for k, v in (('light', 'off'), ('home', 'split')))
    assert (writes.count(('LIGHT', dark)), writes.count(('HOME', split))) == (migrates, migrates)  # the preset, once
    assert (st.light, st.home) == ((dark, split) if migrates else (1, 1))


# A-B markers on a bar laid out off the immediates' place (ringnav_ab_x): the same share of it.
def test_an_ab_marker_keeps_its_share_of_a_fitted_bar(fw, ui, mcl, st):
    theme(fw, st)
    values = host.tools_theme.immediate_values(GEOMETRY)
    x0, w0 = values['np_bar_x'], values['np_bar_w']
    stock = {}
    fw.mock('ab_x', lambda t, *_: stock[t])
    ab_x = fw.fn('ringnav_ab_x', C.c_int, C.c_int)
    stock.update({1: x0 + w0 // 2, 2: 0, 3: x0})
    assert ab_x(1) == x0 + w0 // 2  # no Now Playing: stock's
    now = Now(fw, ui, mcl, st, sec=10, total=4000)
    x, _, w, _ = ui.box(now.slider)
    assert (x, w) != (x0, w0)  # the bar elsewhere, or the share proves nothing
    assert ab_x(1) == x + (w0 // 2) * w // w0 and ab_x(3) == x
    assert ab_x(2) == 0  # no point to mark


# A-B markers on a bar laid out off the immediates' place (ringnav_ab_move): centered on the bar, not at stock's y 250.
def test_an_ab_marker_is_centered_on_the_bar(fw, ui, mcl, st):
    theme(fw, st)
    moves = []
    fw.mock('widget_move', lambda w, x, y, *_: moves.append((w, x, y)))
    ab_move = fw.fn('ringnav_ab_move', C.c_int, C.c_void_p, C.c_int, C.c_int)
    marker = ui.widget(ui.wm, 'img_repeata', 0, 250, 3, 10)
    ab_move(marker, 77, 250)
    assert moves == [(marker, 77, 250)]  # no Now Playing: stock's
    now = Now(fw, ui, mcl, st, sec=10, total=4000)
    marker = ui.widget(now.win, 'img_repeata', 0, 250, 3, 10)
    struct.pack_into('<ii', (C.c_char * 8).from_address(now.slider + 4), 0, 300, 20)  # the bar elsewhere than stock's
    _, y, _, h = ui.box(now.slider)
    assert y + h // 2 != 255
    moves.clear()
    ab_move(marker, 77, 250)
    assert moves == [(marker, 77, y + h // 2 - 5)]


@pytest.mark.parametrize('value, part', [(0, 0), (50, 102), (100, 204), (25, 51)])
def test_the_sliders_part_is_its_share_of_the_width(fw, ui, value, part):
    s = ui.widget(ui.wm, 'slider')
    ui.ints[s, b'value'] = value
    assert fw.fn('slider_part', C.c_int, C.c_void_p, C.c_int)(s, 204) == part
    ui.ints[s, b'min'] = ui.ints[s, b'max'] = 5
    assert fw.fn('slider_part', C.c_int, C.c_void_p, C.c_int)(s, 204) == 0  # no range


def test_final_text_is_never_flipped_and_leaves_the_ink_as_it_found_it(fw, st):
    theme(fw, st, 1)
    canvas = Canvas(fw)
    text = wide('Hi')
    rect = (C.c_int * 4)(0, 0, 100, 30)
    fw.fn('final_text', None, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_uint)(
        canvas.p, b'inter', C.addressof(text), rect, 20, 0x80000000)
    assert [(t[0], t[3]) for t in canvas.texts] == [('Hi', 0x80000000)] and st.raw_ink == 0
    empty = wide('')
    fw.fn('final_text', None, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_uint)(
        canvas.p, None, C.addressof(empty), rect, 20, 0xff000000)
    assert len(canvas.texts) == 1


def test_a_labels_final_ink_is_rewritten_when_the_half_changes(fw, st, ui):
    theme(fw, st, 1)
    win = window(ui, 'page')
    label = ui.widget(win, 'label')
    button = ui.widget(win, 'button')
    fw.fn('text_ink', None, C.c_void_p, C.c_uint, C.c_int)(label, 0xffffffff, 1)
    fw.fn('text_ink', None, C.c_void_p, C.c_uint, C.c_int)(button, 0xccffffff, 5)
    assert ui.ints[label, b'style:normal:text_color'] & 0xffffffff == rgba(0)  # the light half's style hook flips it back
    assert (label, b'style:pressed:text_color') not in ui.ints and ui.ints[button, b'style:focused:text_color'] & 0xffffffff == 0xcc000000
    theme(fw, st, 0)
    fw.fn('text_inks', None, C.c_void_p)(ui.wm)
    assert ui.ints[label, b'style:normal:text_color'] & 0xffffffff == 0xffffffff
    assert ui.ints[button, b'style:over:text_color'] & 0xffffffff == 0xccffffff


def test_theme_art_names_the_themes_own_image(fw, st):
    """theme_art (list_marks.c): the theme's art prefix (tools/theme.py, its id) before the name."""
    theme(fw, st)
    assert fw.fn('theme_art', C.c_char_p, C.c_char_p)(b'chevron_d') == b'oled_chevron_d'


def test_the_wheels_scroll_indicator_shows_a_second_then_fades(fw, st, ui):
    """SB_WHEEL (OLED): a tick arms it on the list it moved and on that list's own view; full for 800 ms, fading
    to nothing at a second; another list never shows it."""
    now = [5000]
    fw.mock('time_now_ms', lambda *_: now[0])
    arm = fw.fn('wheel_sb_arm', None, C.c_void_p)
    alpha = fw.fn('wheel_sb_alpha', C.c_uint, C.c_void_p)
    owner = ui.widget(ui.wm, 'list_view', 0, 58, 375, 224)
    view = ui.widget(owner, 'scroll_view', 0, 0, 375, 224)
    other = ui.widget(ui.wm, 'list_view', 0, 58, 375, 224)
    theme(fw, st)
    assert alpha(view) == 0  # nothing armed yet
    arm(view)
    assert (alpha(view), alpha(owner), alpha(other)) == (255, 255, 0)
    seen = []
    for t in (800, 900, 999, 1000):
        now[0] = 5000 + t
        seen.append(alpha(owner))
    assert seen == [255, 127, 1, 0]


INLINE, RIGHT = 1, 2  # navigation.h SONG_INLINE, SONG_RIGHT


@pytest.mark.parametrize('place, room, title, artist, fit', [
    (INLINE, 323, 100, 80, (100, 80)),     # both fit: the title, two spaces, the artist
    (INLINE, 323, 200, 200, (200, 112)),   # the artist ends in an ellipsis in what is left
    (INLINE, 323, 272, 80, (272, 40)),     # 40 px left after the gap: still shown
    (INLINE, 323, 273, 80, (273, 0)),      # 39: dropped
    (INLINE, 323, 400, 80, (323, 0)),      # a title wider than the row ends in its own ellipsis
    (INLINE, 323, 100, 0, (100, 0)),       # no artist on the record
    (RIGHT, 323, 100, 80, (100, 80)),      # on the right column
    (RIGHT, 323, 300, 300, (161, 151)),    # each to about half
    (RIGHT, 102, 60, 80, (51, 40)),        # the title gives way to half the row, the artist its least
    (RIGHT, 100, 60, 80, (60, 0)),         # under that the artist goes
    (0, 323, 400, 80, (323, 0))])          # Off: the title alone
def test_song_fit_keeps_the_title_and_drops_a_squeezed_artist(fw, place, room, title, artist, fit):
    """song_fit (song_rows.c): a mixed list's title keeps the ink it needs and the artist takes what is left after
    the gap, ellipsised, and goes under 40 px; on the right column it takes at most half the row."""
    shown = C.c_int(-1)
    got = fw.fn('song_fit', C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.POINTER(C.c_int))(
        place, room, title, 11, artist, C.byref(shown))
    assert (got, shown.value) == fit


# Now Playing's lines as one marquee (np_marquee.c): hold, then every overflowing line slides what overflows in the
# same time, a frame a pixel, holds at its end and goes back to rest; no line ever leaves its column empty.
NPM_HOLD, NPM_PX_S = (int(re.findall(rf'#define {n} (\d+)', NAVIGATION_SOURCE)[0]) for n in ('NPM_HOLD_MS', 'NPM_PX_S'))
NPM_END = O['MARQUEE_HOLD_MS']


class Marquee:
    """OLED's Now Playing with a title text_w px wide and an artist artist_w, each 160 px of column. The album is one
    line with an ellipsis, never scrolled (np_marquee.c npm_line: the title and the artist)."""
    def __init__(self, fw, ui, mcl, st, text_w=300, artist_w=200):
        theme(fw, st)
        self.now = Now(fw, ui, mcl, st)
        self.artist = ui.widget(self.now.win, 'scrlabel_artist', 206, 70, 160, 22, text='John Coltrane')
        st.np_artist = self.artist
        self.fw, self.ui = fw, ui
        fw.mock('style_get_int', lambda s, name, d, *_: 0)
        fw.mock('hscroll_label_set_xoffset', lambda w, x, *_: struct.pack_into('<i', (C.c_char * 4).from_address(w + O['HSCROLL_XOFFSET']), 0, x))
        for w, tw in ((self.now.title, text_w), (self.artist, artist_w)):
            struct.pack_into('<i', (C.c_char * 4).from_address(w + O['HSCROLL_TEXT_W']), 0, tw)
        self.over = {self.now.title: max(text_w - 160, 0), self.artist: max(artist_w - 160, 0)}
        self.pass_ms = max(self.over.values()) * 1000 // NPM_PX_S

    def off(self, w):
        return C.c_int.from_address(w + O['HSCROLL_XOFFSET']).value

    def kick(self):
        self.fw.fn('np_marq_kick', None, C.c_void_p)(self.now.title)

    def step(self, ms):
        """ms on, then the marquee's due timer"""
        self.ui.now += ms
        (t,) = self.ui.due('npm_tick')
        self.ui.run(t)


def test_the_lines_hold_slide_what_overflows_hold_at_the_end_and_go_back_to_rest(fw, ui, mcl, st):
    m = Marquee(fw, ui, mcl, st)
    m.kick()
    (t,) = ui.due('npm_tick')
    assert ui.timers[t][2] == NPM_HOLD and m.off(m.now.title) == m.off(m.artist) == 0  # at rest first
    half = m.pass_ms // 2
    m.step(NPM_HOLD + half)
    assert m.off(m.now.title) == 140 * half // m.pass_ms  # each what overflows in the same time
    assert m.off(m.artist) == 40 * half // m.pass_ms
    (t,) = ui.due('npm_tick')
    assert ui.timers[t][2] <= 1000 // NPM_PX_S + 1  # the longest line's next pixel
    m.step(m.pass_ms - half)  # each line's end at its column's end, held there
    assert (m.off(m.now.title), m.off(m.artist)) == (140, 40)
    assert m.off(m.now.album) == 0  # one line, its ellipsis
    (t,) = ui.due('npm_tick')
    assert ui.timers[t][2] == NPM_END
    m.step(NPM_END)  # back at rest together, the next hold
    assert m.off(m.now.title) == m.off(m.artist) == 0
    (t,) = ui.due('npm_tick')
    assert ui.timers[t][2] == NPM_HOLD


def test_no_line_ever_scrolls_past_its_end(fw, ui, mcl, st):
    # a line slid further than it overflows leaves its column empty (V1.36's hscroll_label draws no copy behind it):
    # on a Q2 the title was blank for over a second each pass
    m = Marquee(fw, ui, mcl, st)
    m.kick()
    for _ in range(400):
        (t,) = ui.due('npm_tick')
        m.step(ui.timers[t][2])
        assert all(0 <= m.off(w) <= over for w, over in m.over.items()), [m.off(w) for w in m.over]


def test_every_frame_moves_the_longest_line_one_pixel(fw, ui, mcl, st):
    m = Marquee(fw, ui, mcl, st)
    m.kick()
    m.step(NPM_HOLD)
    seen = [m.off(m.now.title)]
    for _ in range(40):
        (t,) = ui.due('npm_tick')
        m.step(ui.timers[t][2])
        seen.append(m.off(m.now.title))
    assert [b - a for a, b in zip(seen, seen[1:])] == [1] * 40


def test_the_page_opening_stops_stocks_own_timer_on_the_lines(fw, ui, mcl, st):
    # stock's page init starts the title's own timer before the lines are bound: left running, it moved the
    # title through the hold and between the marquee's frames
    m = Marquee(fw, ui, mcl, st)
    stopped = []
    fw.mock('hscroll_label_stop', lambda w, *_: stopped.append(w) or C.c_int.from_address(w + O['HSCROLL_TIMER']).__setattr__('value', 0))
    C.c_int.from_address(m.now.title + O['HSCROLL_TIMER']).value = 23
    fw.fn('np_marq_reset', None)()
    assert stopped == [m.now.title] and C.c_int.from_address(m.now.title + O['HSCROLL_TIMER']).value == 0


def test_lines_that_fit_never_move_and_nothing_runs(fw, ui, mcl, st):
    m = Marquee(fw, ui, mcl, st, text_w=100, artist_w=150)
    m.kick()
    assert not ui.due('npm_tick') and m.off(m.now.title) == 0


def test_a_covered_page_rests_its_lines_and_a_new_text_starts_from_rest(fw, ui, mcl, st):
    m = Marquee(fw, ui, mcl, st)
    m.kick()
    m.step(NPM_HOLD + 2000)
    assert m.off(m.now.title) > 0
    ui.top = None  # another window on top
    m.step(50)
    assert m.off(m.now.title) == 0 and not ui.due('npm_tick')
    ui.top = m.now.win
    m.kick()
    m.step(NPM_HOLD // 2)
    assert m.off(m.now.title) == 0  # back on top: the hold first
    # a new title: stock's start comes to ringnav_hscroll_start, which leaves it to the marquee, from rest
    assert fw.fn('ringnav_hscroll_start', C.c_int, C.c_void_p)(m.now.title) == 0
    assert not ui.due('npm_tick') and not fw.calls('stock_hscroll_start_trampoline')


# OLED's Track Time (NPTIME): the right-hand time is what is left or the track's length, and a tap on it switches. The
# page takes the touch (np_time_tap on the window) and hit-tests the label.
def time_tap(ui, now, x=330, y=250, up=None):
    tap(ui, now.win, O['EVT_POINTER_DOWN'], x, y)
    tap(ui, now.win, O['EVT_POINTER_UP_BEFORE'], *(up or (x, y)))


def test_a_tap_on_the_right_hand_time_shows_the_tracks_length_and_saves_it(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, sec=50, total=200)
    assert ui.text(now.remain) == '-2:30'
    time_tap(ui, now)
    assert ui.text(now.remain) == '3:20'  # the length, with no minus
    writes = [(C.string_at(a[2]).decode(), C.c_int(a[0]).value) for _, a in fw.calls('write_int_config')]
    assert writes[-1] == ('NPTIME', 1)
    ui.ints[now.slider, b'value'] = 60
    fw.fn('np_sync', C.c_int, C.c_void_p)(now.win)
    assert ui.text(now.remain) == '3:20' and now.value() == 60  # the length stays as the track plays; no seek
    time_tap(ui, now)
    assert ui.text(now.remain) == '-2:20'


def test_a_touch_off_the_time_changes_nothing(fw, ui, mcl, st):
    theme(fw, st)
    now = Now(fw, ui, mcl, st, sec=50, total=200)
    tap(ui, now.win, O['EVT_POINTER_UP_BEFORE'], 330, 250)  # a touch that came down elsewhere
    time_tap(ui, now, up=(200, 250))  # down on the time, lifted off it
    time_tap(ui, now, x=200)  # the elapsed side of the row
    assert ui.text(now.remain) == '-2:30' and not fw.calls('write_int_config')


# A streamed song still arriving (np_stream.c np_stream_length, stream_state.c stream_length): the player's length grows
# with the bytes, so Now Playing shows the server's length, or --:-- when there is none, until the song is whole.
STREAMED = '/mnt/mmc/.subsonic/stream/9f2c/original/0/hello [VcF5].mp3'


class Arriving:
    """Now's third track as a Stream Cache song with a .stream line: state and the server's length in ms (0: none)."""
    def __init__(self, fw, now):
        from test_addon import World
        self.world, self.now = World(fw, now=1000), now
        self.path = C.create_string_buffer(STREAMED.encode())
        struct.pack_into('<Q', now.recs[2], O['REC_PATH'], C.addressof(self.path))
        self.world.mtime[b'/tmp/q2-subsonic.stream'] = 1000

    def line(self, state, ms):
        self.world.ms += 300  # np_stream_length reads a line at most every 200 ms
        self.world.files[b'/tmp/q2-subsonic.stream'] = f'#Q2STREAM/1\tseq=1\n{state}\t900000\t4000000\t{ms}\t{STREAMED}\n'.encode()


@pytest.fixture
def arriving(fw, now):
    return Arriving(fw, now)


def test_the_right_hand_time_is_unknown_until_the_song_is_whole(fw, ui, now, arriving):
    sync = fw.fn('np_sync', C.c_int, C.c_void_p)
    now.total = 61  # what the player makes of the bytes so far
    ui.ints[now.slider, b'max'], ui.ints[now.slider, b'value'] = 61, 50
    arriving.line('get', 0)
    assert sync(now.win) == 1
    assert ui.text(now.remain) == '--:--'
    arriving.line('get', 300000)  # the server's duration: certain, so it, not the player's 61 s
    assert sync(now.win) == 1 and ui.text(now.remain) == '-4:10'
    arriving.line('done', 300000)  # whole: the player's length stands
    ui.ints[now.slider, b'max'] = 300
    assert sync(now.win) == 1 and ui.text(now.remain) == '-4:10'
    ui.ints[now.slider, b'max'] = 200
    arriving.line('done', 0)
    assert sync(now.win) == 1 and ui.text(now.remain) == '-2:30'


def drawn(fw, now):
    """The rects canvas_fill_rounded_rect fills over stock's bar in one paint: (x, y, w, h) each."""
    boxes = []
    fw.mock('canvas_fill_rounded_rect', lambda c, r, sr, color, radius, *_: boxes.append(
        struct.unpack_from('<4i', (C.c_char * 16).from_address(r))) or 0)
    scrubber(fw, now.slider, Canvas(fw))
    return [b[2] for b in boxes]


def test_the_bar_has_no_fill_while_the_length_is_unknown_and_scales_to_the_servers_when_it_is_known(fw, ui, now, arriving):
    w = ui.box(now.slider)[2]
    arriving.line('get', 0)
    assert drawn(fw, now) == [w]  # the track over stock's fill, and no fill of its own
    arriving.line('get', 400000)
    ui.now += 1000
    track, fill = drawn(fw, now)
    assert track == w and w * 50 // 400 <= fill <= w * 51 // 400  # 50 s of 400 s, not of the player's 200
    arriving.line('done', 400000)
    assert w not in drawn(fw, now)  # stock's own bar again: no track of ours, only the glide's fill


def test_a_bar_with_no_length_yet_is_empty(fw, ui, now):
    """A pressed server song before the player has it (the user's device, 2026-11-17, q2player): stock's slider at
    max 0 and value 0 still draws a fill about a ninth of the bar long (emulator: 38 of 323 px), so the track is
    drawn over it."""
    ui.ints[now.slider, b'max'], ui.ints[now.slider, b'value'] = 0, 0
    now.status = 1  # stopped (catalogue.c np_stop)
    assert drawn(fw, now) == [ui.box(now.slider)[2]]  # the track, the whole width, and no fill
