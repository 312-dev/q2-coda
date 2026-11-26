"""patch/navigation.c's painters and settings rows, with the scheme and Light Mode (now theme_engine.c's)
and a row action's confirm (now playlists.c's): the marquee's lull and fade, OLED's dialog, the
selection, dividers, marks and the wheel's scroll indicator, ringnav_paint, the settings pages' rows,
the tag and scan hooks, the lyrics' follow and the status bar's play state and clock.

The widgets are a small AWTK of their own (Ui): each a zeroed block whose box and parent the C reads
by offset, its type, name, props, text and children answered by the stubs; the canvas (Canvas) keeps
what was drawn, pixel by pixel, in the colours the C set."""
import contextlib
import ctypes as C
import random
import re
import struct
import subprocess
import typing

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, c_enum, Deques, Files, claim, high_halves, libc, stock_prop_str, stock_start_with, threads_now, ui_name

STRINGS = dict(re.findall(r'^#define (\w+) "([^"]*)"', (host.ROOT/'patch/offsets.inc').read_text(), re.M))
BT_GLYPH = STRINGS['BT_GLYPH']

W_X, W_Y, W_W, W_H, W_PARENT, W_EMITTER = O['W_X'], O['W_Y'], O['W_W'], O['W_H'], O['W_PARENT'], O['W_EMITTER']
EVT_CLICK, EVT_DESTROY = O['EVT_CLICK'], O['EVT_DESTROY']
CANVAS_X, CANVAS_Y, CANVAS_LCD = O['CANVAS_X'], O['CANVAS_Y'], O['CANVAS_LCD']
LCD_FILL, LCD_STROKE = O['LCD_FILL_COLOR'], O['LCD_STROKE_COLOR']
THEMES = host.tools_theme.builtins()
OLED = next(i for i, t in enumerate(THEMES) if t['name'] == 'OLED')  # the one theme
GEOMETRY = host.tools_theme.built_geometry(THEMES)  # the one set the build bakes: OLED's


def i32(v):
    return C.c_int(v).value


def rgba(c, a=0xff):
    """0xRRGGBB as the canvas takes it (theme.h RGBA): bytes r, g, b, a."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16 & 255


def wide(s):
    return [ord(ch) for ch in s]


def bright(c):
    """A colour's channels summed, in either byte order: which of two is the darker."""
    return sum(c >> s & 255 for s in (0, 8, 16))


POD = c_enum('POD_CHARGE')  # pod_rows' rows
SETTING = c_enum('SETTING_ACCENT')  # Display's rows, st.setting_label's slots


class Node:
    """A widget: a zeroed block the C reads its box (+0) and parent (+0x48) from, and what the stubs answer."""
    def __init__(self, kind, parent, box, name):
        self.block = C.create_string_buffer(0x200)
        self.addr = C.addressof(self.block)
        self.kind, self.name, self.parent = kind, name, parent
        self.props: dict = {}
        self.children: list = []
        self.visible = 1
        self.text: list = []
        self.wide = None
        self.handlers: list = []
        self.items: list = []
        self.emitter = C.create_string_buffer(8)
        self.move(*box)
        struct.pack_into('<Q', self.block, W_PARENT, parent.addr if parent else 0)

    def move(self, x, y, w, h):
        struct.pack_into('<iiii', self.block, W_X, x, y, w, h)

    @property
    def box(self):
        return struct.unpack_from('<iiii', self.block, W_X)

    def int_at(self, offset):
        return C.c_int.from_address(self.addr + offset)

    def byte_at(self, offset):
        return C.c_ubyte.from_address(self.addr + offset)

    def clickable(self, ctx=0):
        """An EVT_CLICK on its emitter (V1.36: emitter @0x60, its first item, type @8, next @0x20)."""
        item = C.create_string_buffer(0x28)
        struct.pack_into('<Q', item, 0, ctx)
        struct.pack_into('<i', item, O['EMIT_TYPE'], EVT_CLICK)
        if self.items:
            struct.pack_into('<Q', self.items[-1], O['EMIT_NEXT'], C.addressof(item))
        else:
            struct.pack_into('<Q', self.emitter, 0, C.addressof(item))
            struct.pack_into('<Q', self.block, W_EMITTER, C.addressof(self.emitter))
        self.items.append(item)
        return self

    def child(self, name):
        return next(c for c in self.walk() if c.name == name)

    def walk(self):
        for c in self.children:
            yield c
            yield from c.walk()

    def __repr__(self):
        return f'<{self.kind} {self.name!r} {self.box}>'


class Ui:
    """AWTK's widget calls over Nodes; the window manager's top window is top."""
    CREATE = {'list_item_create': 'list_item', 'button_create': 'button', 'image_create': 'image',
              'hscroll_label_create': 'hscroll_label', 'label_create': 'label', 'scroll_view_create': 'scroll_view'}

    def __init__(self, fw):
        self.fw = fw
        self.nodes: dict = {}
        self.cstr: dict = {}
        self.wm = self.add('window_manager', None)
        self.top: typing.Any = None  # the top window, a Node
        self.pressed = 0  # the pointer's down: window_manager_get_pointer_pressed
        m = fw.mock
        m('window_manager', lambda *_: self.wm.addr)
        m('window_manager_get_top_window', lambda *_: self.top.addr if self.top else 0)
        m('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        m('widget_get_type', lambda w, *_: self.c(self.nodes[w].kind) if w in self.nodes else 0)
        m('widget_get_prop_str', self.prop_str)
        m('widget_get_prop_int', self.prop_int)
        m('widget_get_prop_bool', lambda w, name, default, *_: i32(default))
        m('widget_set_prop_int', lambda w, name, v, *_: self.nodes[w].props.__setitem__(C.string_at(name).decode(), i32(v)))
        m('widget_set_prop_str', lambda w, name, v, *_: self.nodes[w].props.__setitem__(C.string_at(name).decode(), C.string_at(v).decode()))
        m('widget_count_children', lambda w, *_: len(self.nodes[w].children) if w in self.nodes else 0)
        m('widget_get_child', lambda w, i, *_: self.nodes[w].children[i & 0xffffffff].addr
          if w in self.nodes and (i & 0xffffffff) < len(self.nodes[w].children) else 0)
        m('widget_lookup', self.lookup)
        m('widget_get_visible', lambda w, *_: self.nodes[w].visible if w in self.nodes else 0)
        m('widget_set_visible', lambda w, v, *_: setattr(self.nodes[w], 'visible', int(bool(i32(v)))))
        m('widget_move_resize', lambda w, x, y, ww, h, *_: self.nodes[w].move(i32(x), i32(y), i32(ww), i32(h)))
        m('widget_resize', lambda w, ww, h, *_: self.nodes[w].move(*self.nodes[w].box[:2], i32(ww), i32(h)))
        m('widget_get_text', self.get_text)
        m('widget_set_text_utf8', lambda w, s, *_: self.set_text(self.nodes[w], C.string_at(s).decode()))
        m('image_base_set_image', lambda w, s, *_: self.nodes[w].props.__setitem__('image', C.string_at(s).decode()))
        m('widget_set_name', lambda w, s, *_: setattr(self.nodes[w], 'name', C.string_at(s).decode()))
        m('widget_use_style', lambda w, s, *_: self.nodes[w].props.__setitem__('style', C.string_at(s).decode()))
        m('widget_destroy', self.destroy)
        m('widget_destroy_children', lambda w, *_: [self.destroy(c.addr) for c in list(self.nodes[w].children)] and 0)
        m('widget_restack', self.restack)
        m('widget_on', self.on)
        for name, kind in self.CREATE.items():
            m(name, lambda parent, x, y, w, h, *_, kind=kind: self.add(kind, self.nodes[parent], (i32(x), i32(y), i32(w), i32(h))).addr)

    def c(self, s):
        if s is None:
            return 0
        if s not in self.cstr:
            self.cstr[s] = C.create_string_buffer(s.encode())
        return C.addressof(self.cstr[s])

    def add(self, kind, parent, box=(0, 0, 0, 0), name='', **props):
        node = Node(kind, parent, box, name)
        node.props.update(props)
        self.nodes[node.addr] = node
        if parent:
            parent.children.append(node)
        return node

    def window(self, name, box=(0, 0, 375, 320), kind='window', top=True):
        win = self.add(kind, self.wm, box, name)
        if top:
            self.top = win
        return win

    def prop_str(self, w, name, default, *_):
        node, key = self.nodes.get(w), C.string_at(name).decode()
        if node is None:
            return default  # stock: widget_get_prop fails on a NULL widget
        value = (node.name or None) if key == 'name' else node.props.get(key)  # '' is a node never named
        value = stock_prop_str(node.kind.encode(), key.encode(), value.encode() if isinstance(value, str) else value, default)
        return 0 if value is None else self.c(value.decode()) if isinstance(value, bytes) else value

    def prop_int(self, w, name, default, *_):
        node = self.nodes.get(w)
        value = node.props.get(C.string_at(name).decode()) if node else None
        return value if isinstance(value, int) else i32(default)

    def lookup(self, w, name, recursive, *_):
        node, key = self.nodes.get(w), C.string_at(name).decode()
        if node is None:
            return 0
        found = node.walk() if i32(recursive) else iter(node.children)
        return next((c.addr for c in found if c.name == key), 0)

    def set_text(self, node, s):
        node.text = wide(s)
        node.wide = (C.c_uint * (len(node.text) + 1))(*node.text)

    def get_text(self, w, *_):
        node = self.nodes.get(w)
        return C.addressof(node.wide) if node is not None and node.wide is not None else 0

    def destroy(self, w, *_):
        node = self.nodes[w]
        node.parent.children.remove(node)

    def restack(self, w, index, *_):
        node = self.nodes[w]
        siblings = node.parent.children
        siblings.remove(node)
        siblings.insert(min(i32(index), len(siblings)), node)

    def on(self, w, event, fn, ctx, *_):
        node = self.nodes[w]
        node.handlers.append((i32(event), fn, ctx))
        if i32(event) == EVT_CLICK:
            node.clickable(ctx)
        return len(node.handlers)

    def fire(self, node, event, data=0):
        """node's handlers for event, as AWTK calls them: (ctx, event)."""
        return [C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(own, data) for kind, fn, own in node.handlers if kind == event]

    def handled(self, node, fn):
        """The contexts node's handlers that are fn were given."""
        return [own for _, f, own in node.handlers if f == fn]

    def text(self, node):
        return ''.join(map(chr, node.text))


class Canvas:
    """A canvas and its lcd: the fill as the C sets it (lcd's fill too, as stock's setter does), every
    rect and row it fills into px, clipped as stock clips, rounded boxes and text as drawn."""
    def __init__(self, fw, x=0, y=0, clip=(0, 0, 375, 320), fill=0xff0a0b0c, stroke=0xff0d0e0f, char_w=10):
        self.block, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        self.addr = C.addressof(self.block)
        struct.pack_into('<ii', self.block, CANVAS_X, x, y)
        struct.pack_into('<Q', self.block, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<II', self.lcd, LCD_FILL, fill, stroke)
        self.start = (fill, stroke)
        self.clip = list(clip)
        self.char_w = char_w  # every character this wide: a text's width is its length's
        self.px: dict = {}
        self.rects: list = []
        self.boxes: list = []
        self.frames: list = []
        self.texts: list = []
        self.icons: list = []
        self.images: list = []
        self.art: dict = {}  # the theme images widget_load_image finds: name -> (width, height)
        self.text_color = 0
        m = fw.mock
        m('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL, color & 0xffffffff))
        m('canvas_set_stroke_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_STROKE, color & 0xffffffff))
        m('canvas_set_text_color', lambda c, color, *_: setattr(self, 'text_color', color & 0xffffffff))
        m('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fill_rect(i32(x), i32(y), i32(w), i32(h)))
        m('canvas_stroke_rect', lambda c, x, y, w, h, *_: self.frames.append(((i32(x), i32(y), i32(w), i32(h)), self.stroke, 0)))
        m('canvas_fill_rounded_rect', self.rounded)
        m('canvas_stroke_rounded_rect', lambda c, r, _, color, radius, *rest: self.stroke_round(r, color, radius))
        m('canvas_get_clip_rect', lambda c, out, *_: C.memmove(out, struct.pack('<4i', *self.clip), 16) and 0)
        m('canvas_set_clip_rect', lambda c, r, *_: setattr(self, 'clip', list(struct.unpack('<4i', C.string_at(r, 16)))))
        m('canvas_measure_text', lambda c, s, n, *_: float(self.char_w * (n & 0xffffffff)))
        m('canvas_draw_text_in_rect', self.draw_text)
        m('canvas_draw_icon', lambda c, bitmap, x, y, *_: self.icons.append((struct.unpack('<II', C.string_at(bitmap, 8)), i32(x), i32(y))))
        m('canvas_draw_image', lambda c, bitmap, src, dst, *_: self.images.append(struct.unpack('<4i', C.string_at(dst, 16))))
        m('widget_load_image', self.load_image)

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL)[0]

    @property
    def stroke(self):
        return struct.unpack_from('<I', self.lcd, LCD_STROKE)[0]

    def fill_rect(self, x, y, w, h):
        self.rects.append(((x, y, w, h), self.fill))
        cx, cy, cw, ch = self.clip
        for yy in range(max(y, cy), min(y + h, cy + ch)):
            for xx in range(max(x, cx), min(x + w, cx + cw)):
                self.px[xx, yy] = self.fill

    def rounded(self, c, r, _, color, radius, *rest):
        """A rounded box, painted as its rect; it leaves the fill alone, as stock's vgcanvas does."""
        box, ink = struct.unpack('<4i', C.string_at(r, 16)), C.c_uint.from_address(color).value
        self.boxes.append((box, ink, i32(radius)))
        saved = self.fill
        struct.pack_into('<I', self.lcd, LCD_FILL, ink)
        self.fill_rect(*box)
        self.rects.pop()
        struct.pack_into('<I', self.lcd, LCD_FILL, saved)
        return 0

    def stroke_round(self, r, color, radius):
        self.frames.append((struct.unpack('<4i', C.string_at(r, 16)), C.c_uint.from_address(color).value, i32(radius)))
        return 0

    def draw_text(self, c, s, n, r, *_):
        chars = [C.c_uint.from_address(s + 4 * i).value for i in range(n & 0xffffffff)]
        self.texts.append((''.join(map(chr, chars)), struct.unpack('<4i', C.string_at(r, 16)), self.text_color))
        return 0

    def load_image(self, w, name, bitmap, *_):
        size = self.art.get(C.string_at(name).decode())
        if size is None:
            return 1
        C.memmove(bitmap, struct.pack('<II', *size), 8)
        return 0

    def restored(self):
        """Whether the painter left the fill and stroke as it found them."""
        return (self.fill, self.stroke) == self.start

    def row(self, y, x0=0, x1=375):
        return [self.px.get((x, y)) for x in range(x0, x1)]


class Settings:
    """config.ini through toolsReadConfig: a key's value, or the default; written keys as toolsWriteConfig
    and write_int_config left them."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        self.written: list = []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('write_int_config', lambda v, section, key, *_: self.written.append((C.string_at(key).decode(), i32(v))))
        fw.mock('toolsWriteConfig', lambda path, section, key, v, *_: self.written.append((C.string_at(key).decode(), C.string_at(v))))

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        if value or C.string_at(key) in self.values:
            C.memmove(out, value + b'\0', len(value) + 1)


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


def strings(fw):
    fw.mock('tk_str_start_with', stock_start_with)


def theme(fw, **values):
    """config.ini's keys for the theme (OLED, stored as THEME 3), read as accent() reads them."""
    settings = Settings(fw, THEME=b'3', **values)
    fw.fn('ipod_light', C.c_int)()
    return settings


def g(fw, name, value=None):
    """One of stock's byte globals."""
    v = fw.var(name, C.c_ubyte)
    if value is not None:
        v.value = value
    return v.value


# The genre's strcpy: whole " / " entries within 0x7f bytes, or whole characters of one too long.
def kept_as_the_genre(joined, out):
    """Whether out is what the comment over ringnav_genre_copy keeps of joined: asserted, a property
    at a time."""
    assert joined.startswith(out) and len(out) <= 0x7f
    out.decode()  # whole characters
    rest, first = joined[len(out):], joined.split(b' / ')[0]
    if len(first) > 0x7f:  # the first entry alone is too long: as many of its whole characters as fit
        lead = rest[0]
        size = 1 if lead < 0x80 else 2 if lead >> 5 == 6 else 3 if lead >> 4 == 14 else 4
        assert len(out) + size > 0x7f
    else:  # whole entries: it ends on a boundary, and the next whole entry would pass 0x7f
        assert not rest or rest.startswith(b' / ')
        if rest:
            assert len(out) + 3 + len(rest[3:].split(b' / ')[0]) > 0x7f
    return True


@pytest.mark.parametrize('joined, kept', [
    (b'Rock', b'Rock'), (b'Rock / Pop', b'Rock / Pop'), (b'', b''),
    (b'a' * 100 + b' / ' + b'b' * 30, b'a' * 100),
    (b'a' * 0x7f, b'a' * 0x7f), (b'a' * 0x80, b'a' * 0x7f),
    (('é' * 100).encode(), ('é' * 63).encode()),  # one too long: 126 bytes of whole characters
    (b'x' * 60 + b' / ' + b'y' * 64, b'x' * 60 + b' / ' + b'y' * 64),
    (b'x' * 60 + b' / ' + b'y' * 65, b'x' * 60),
    (b' / ' + b'z' * 200, b''),  # the ponytail: an empty first entry before one too long
    (b'Rock/Pop; Jazz', b'Rock/Pop; Jazz'),
])
def test_genre_copy(fw, joined, kept):
    dst = (C.c_char * 0x80)()
    back = fw.fn('ringnav_genre_copy', C.c_void_p, C.c_void_p, C.c_char_p)(dst, joined)
    assert back == C.addressof(dst) and dst.value == kept and len(kept) <= 0x7f


def test_genre_copy_any_tag(fw):
    """Whole entries, never past 0x7f bytes, never inside a UTF-8 character, and no shorter than
    those rules make it."""
    copy = fw.fn('ringnav_genre_copy', C.c_void_p, C.c_void_p, C.c_void_p)
    calloc = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)
    free = fw.fn('free', None, C.c_void_p)
    rng = random.Random(0x6e7e)
    pieces = [b'Rock', b'Hip-Hop', b' / ', b'/', b' ', 'é'.encode(), '音楽'.encode(), '\U0001f3b8'.encode(), b'x' * 40]
    for _ in range(1500):
        joined = b''.join(rng.choice(pieces) for _ in range(rng.randrange(0, 30)))
        src, dst = calloc(len(joined) + 1, 1), calloc(0x80, 1)
        C.memmove(src, joined, len(joined))
        copy(dst, src)
        assert kept_as_the_genre(joined, C.string_at(dst)), joined
        free(src), free(dst)


# The marquee's lull: each pass of a looping label ends here, on its end or (yoyo) its way back.
HOLD, PASSES = O['MARQUEE_HOLD_MS'], O['MARQUEE_PASSES']
LULL = C.CFUNCTYPE(C.c_int, C.c_void_p)


class Lull:
    """ringnav_hscroll_lull's timer, fired as stock's timer manager would, over stock's own lull callback."""
    def __init__(self, fw, label, loop=1, yoyo=0, answer=8):
        self.fw, self.label, self.seen = fw, label, []
        label.byte_at(O['HSCROLL_LOOP']).value, label.byte_at(O['HSCROLL_YOYO']).value = loop, yoyo
        self.timers: list = []
        fw.mock('widget_add_timer', lambda w, fn, ms, *_: self.timers.append((w, fn, i32(ms))) or 41)
        self.stock = LULL(lambda info: self.seen.append(len(fw.calls('hscroll_label_set_xoffset'))) or answer)
        self.info = C.create_string_buffer(0x30)
        struct.pack_into('<Q', self.info, O['TIMER_CTX'], label.addr)
        fw.fn('ringnav_hscroll_lull', C.c_uint, C.c_void_p, C.c_void_p, C.c_uint)(label.addr, C.cast(self.stock, C.c_void_p), 3000)

    def end(self):
        return LULL(self.timers[-1][1])(C.addressof(self.info))


@pytest.mark.parametrize('loop, yoyo, ms', [(1, 0, HOLD), (0, 0, HOLD), (0, 1, HOLD), (1, 1, 0)])
def test_a_lull_holds_the_end_unless_a_yoyo_turns_there(fw, loop, yoyo, ms):
    """Stock's 2000 or 3000 ms lull gives way to MARQUEE_HOLD_MS; a looping yoyo label holds its
    turn with its start's delay, so its lull is none."""
    ui = Ui(fw)
    lull = Lull(fw, ui.add('hscroll_label', None), loop, yoyo)
    assert [(w, ms) for w, _, ms in lull.timers] == [(lull.label.addr, ms)]


def test_a_pass_ends_back_on_the_start_and_stock_restarts_it(fw):
    ui = Ui(fw)
    lull = Lull(fw, ui.add('hscroll_label', None), answer=8)
    assert lull.end() == 8
    # the start is back before stock's callback restarts the label, so its delay holds there
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_xoffset')] == [(lull.label.addr, 0)]
    assert lull.seen == [1] and not fw.calls('hscroll_label_stop')


def test_a_yoyo_pass_turns_on_its_end(fw):
    ui = Ui(fw)
    lull = Lull(fw, ui.add('hscroll_label', None), yoyo=1)
    lull.end()
    assert lull.seen == [0] and not fw.calls('hscroll_label_set_xoffset')


def test_a_label_that_doesnt_loop_stops_on_its_start(fw):
    ui = Ui(fw)
    label = ui.add('hscroll_label', None)
    label.int_at(O['HSCROLL_TIMER']).value = 41
    lull = Lull(fw, label, loop=0)
    assert lull.end() == 7  # RET_REMOVE: this firing timer is the one stopped
    assert label.int_at(O['HSCROLL_TIMER']).value == 0 and lull.seen == []
    assert [c[1][0] for c in fw.calls('hscroll_label_stop')] == [label.addr]


def library(fw, ui, rows=4, pitch=48, name='localmusic_page', titles=None):
    """A lit, usable screen: a Library-style page over a vertical scroll_view of tappable rows, each a
    list_item holding a button and its title's hscroll_label. Returns the view and the buttons."""
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0), ('g_poweroff_state', 0)):
        g(fw, flag, value)
    win = ui.window(name)
    view = ui.add('scroll_view', win, (0, 26, 375, 294), 'scroll_view_list')
    view.byte_at(O['VIEW_VERTICAL']).value = 1
    view.int_at(O['VIEW_CONTENT_H']).value = rows * pitch
    buttons = []
    for i in range(rows):
        item = ui.add('list_item', view, (0, i * pitch, 375, pitch))
        button = ui.add('button', item, (20, 0, 335, pitch), f'row{i}').clickable()
        label = ui.add('hscroll_label', button, (10, 0, 260, pitch), f'title{i}')
        ui.set_text(label, titles[i] if titles else f'Row {i}')
        buttons.append(button)
    return view, buttons


def test_the_selected_rows_title_scrolls_its_passes_then_rests(fw, st):
    ui = Ui(fw)
    view, rows = library(fw, ui)
    label = rows[1].children[0]
    st('sel_w', C.c_void_p).value, st('sel_row').value = view.addr, 1
    st('marq_row', C.c_void_p).value, st('marq_id').value = rows[1].addr, 1
    st('marq_label', C.c_void_p).value = label.addr
    fw.var('navigation__marq_focus', C.c_ubyte * 2)[0] = 1  # as marq_moved found it: only_focus
    lull = Lull(fw, label)
    for passes in range(1, PASSES + 1):
        lull.end()
        assert st('marq_passes').value == passes
        assert bool(fw.calls('hscroll_label_stop')) == (passes == PASSES)
    # the last pass gives the label back its ellipsis: only_focus again, and no longer the marquee's
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_only_focus')] == [(label.addr, 1)]
    assert st('marq_label', C.c_void_p).value is None


@pytest.mark.parametrize('lit, moved', [(0, 0), (1, 1)])
def test_a_marquee_off_its_row_or_screen_ends_with_the_pass(fw, st, lit, moved):
    """A dark screen, or a selection moved to another row, stops it at once, and the next paint with a
    selection starts over (marq_row 0)."""
    ui = Ui(fw)
    view, rows = library(fw, ui)
    g(fw, 'g_backlight_status', lit)
    label = rows[1].children[0]
    st('sel_w', C.c_void_p).value, st('sel_row').value = view.addr, 2 if moved else 1
    st('marq_row', C.c_void_p).value, st('marq_id').value = rows[1].addr, 1
    st('marq_label', C.c_void_p).value = label.addr
    Lull(fw, label).end()
    assert st('marq_row', C.c_void_p).value is None and st('marq_label', C.c_void_p).value is None
    assert [c[1][0] for c in fw.calls('hscroll_label_stop')] == [label.addr]


@pytest.mark.parametrize('key_w, value_w, scrolls', [(80, 300, 'value'), (400, 300, 'key'), (80, 100, None)])
def test_a_cut_off_value_scrolls_on_the_selected_row(fw, st, key_w, value_w, scrolls):
    """A key/value row (About's CFW. Version, 0about): the house marquee takes the row's first label that is cut off,
    the title before its value, so a value cut off at its column scrolls when its row is selected; with nothing cut
    off, nothing moves."""
    ui = Ui(fw)
    view, rows = library(fw, ui, name='about_page')
    key = rows[1].children[0]
    value = ui.add('hscroll_label', rows[1], (180, 0, 150, 48), 'value1')
    for label, w in ((key, key_w), (value, value_w)):
        label.move(*label.box[:2], 150, 48)
        label.int_at(O['HSCROLL_TEXT_W']).value = w
        label.byte_at(O['HSCROLL_ONLY_FOCUS']).value = 1
    st('sel_w', C.c_void_p).value, st('sel_row').value = view.addr, 1
    st('marq_row', C.c_void_p).value, st('marq_id').value = rows[1].addr, 1
    fw.fn('navigation__marq_moved', C.c_int, C.c_void_p)(None)
    started = [a[0] for _, a in fw.calls('hscroll_label_start')]
    assert started == ([] if scrolls is None else [(key if scrolls == 'key' else value).addr])


# The wheel's scroll indicator: a track and its thumb on a list the wheel just moved, drawn after the list's children
# (ringnav_paint, list_marks.c wheel_sb_alpha). The track stands on the screen rows sb_top to sb_bottom whatever the
# list's own box; the thumb's place and length are the scroll's and the screenful's shares of it.
class Lists:
    """A list_view holding its scroll_view (or a table's client) and stock's scroll_bar_m, its top at screen row y,
    the wheel's tick on it just now."""
    def __init__(self, fw, y=26, h=294, content=1200, value=0, table=0):
        self.ui, self.fw = Ui(fw), fw
        theme(fw)
        self.T = THEMES[OLED]
        fw.mock('time_now_ms', lambda *_: 5000)
        win = self.ui.window('allmusic_page')
        self.list = self.ui.add('list_view', win, (0, y, 375, h))
        client = self.ui.add('table_client' if table else 'scroll_view', self.list, (0, 0, 375, h))
        client.int_at(O['VIEW_CONTENT_H']).value = content
        fw.mock('table_client_get_virtual_h', lambda *_: content)
        self.bar = self.ui.add('scroll_bar_m', self.list, (369, 0, 6, h))
        self.bar.int_at(O['BAR_MAX']).value = 1000 if table else content  # a table's bar: an asset's 1000
        self.bar.int_at(O['BAR_VALUE']).value = value
        fw.fn('wheel_sb_arm', None, C.c_void_p)(client.addr)
        self.canvas = Canvas(fw, y=y)

    def paint(self):
        self.fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(self.list.addr, self.canvas.addr)
        return sorted(self.canvas.px)

    def thumb(self):
        """The pixels in the dark half's thumb colour (its ends' too, part transparent)."""
        ink = rgba(self.T['sb_thumb']) & 0xffffff
        return [p for p, c in self.canvas.px.items() if c & 0xffffff == ink]

    def track(self):
        """The screen rows the track stands on, in the list's own rows."""
        y = struct.unpack_from('<i', self.canvas.block, CANVAS_Y)[0]
        return self.T['sb_top'] - y, self.T['sb_bottom'] - y


def thumb_box(pixels):
    xs, ys = [x for x, _ in pixels], [y for _, y in pixels]
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def test_the_thumb_sits_at_the_lists_right_edge_and_starts_at_the_tracks_top(fw):
    lists = Lists(fw)
    lists.paint()
    x0, y0, x1, y1 = thumb_box(lists.thumb())
    top, bottom = lists.track()
    T = lists.T
    assert (x0, x1) == (375 - T['sb_right'] - T['sb_w'], 375 - T['sb_right'])
    assert y0 == top and y1 - y0 == (bottom - top) * 294 // 1200  # the screenful's share of the track
    assert thumb_box(lists.paint()) == (x0, top, x1, bottom)  # the track under it, top to bottom
    assert lists.canvas.restored()


@pytest.mark.parametrize('value', [-50, 0, 300, 906, 1200, 5000])
def test_the_thumb_moves_with_the_list_and_stays_on_its_track(fw, value):
    """The scroll's share of the track places it, held to the bar's range; it never runs past the track's end, so
    at the bar's max it has no rows."""
    lists = Lists(fw, value=value)
    lists.paint()
    top, bottom = lists.track()
    track = bottom - top
    at = track * max(0, min(value, 1200)) // 1200
    length = min(track * 294 // 1200, track - at)
    thumb = lists.thumb()
    if length <= 0:
        assert thumb == []
        return
    _, y0, _, y1 = thumb_box(thumb)
    assert (y0, y1) == (top + at, top + at + length) and y1 <= bottom


def test_a_list_that_fits_has_no_thumb(fw):
    lists = Lists(fw, content=294)
    assert lists.paint() == []


def test_a_table_measures_by_its_virtual_height(fw):
    """Before its first scroll a table's bar holds an asset's 1000: the client's height counts."""
    lists = Lists(fw, content=2940, table=1)
    lists.paint()
    _, y0, _, y1 = thumb_box(lists.thumb())
    top, bottom = lists.track()
    assert y1 - y0 == (bottom - top) * 294 // 2940


def test_a_list_under_the_bar_keeps_the_thumb_below_it(fw):
    """The track's ends are screen rows: a list from the top of the screen starts its thumb at sb_top."""
    lists = Lists(fw, y=0, h=320)
    lists.paint()
    _, y0, _, _ = thumb_box(lists.thumb())
    assert y0 == lists.T['sb_top']


def test_the_thumb_is_the_themes_in_either_half(fw, st):
    lists = Lists(fw)
    dark = set(lists.canvas.px[p] for p in lists.paint())
    st('light').value = 1
    lists.canvas.px.clear()
    light = set(lists.canvas.px[p] for p in lists.paint())
    assert rgba(lists.T['sb_thumb']) in dark and dark != light


def test_a_menu_sheets_scroll_indicator_is_the_lists_own(fw, st):
    """OLED's wheel indicator on a menu sheet's rows (menu_modal.c): the rows span the screen on the house grid, so the
    indicator is a list's, in the gutter right of the rows' right column (x 347 on screen), inside the screen."""
    now = [5000]
    fw.mock('time_now_ms', lambda *_: now[0])
    ui = Ui(fw)
    theme(fw)
    win = ui.window('sortselect_dialog', kind='dialog')
    lv = ui.add('list_view', win, (0, 44, 375, 210), 'list_view')
    view = ui.add('scroll_view', lv, (0, 0, 375, 210), 'scroll_view')
    for _ in range(10):
        ui.add('list_item', view, (0, 0, 375, 32))
    view.int_at(O['VIEW_CONTENT_H']).value = 320
    bar = ui.add('scroll_bar_m', lv, (369, 0, 6, 210))
    bar.int_at(O['BAR_MAX']).value = 320
    fw.fn('modal_frame', None, C.c_void_p, C.c_void_p)(win.addr, lv.addr)
    view.int_at(O['VIEW_CONTENT_H']).value = 320
    fw.fn('wheel_sb_arm', None, C.c_void_p)(view.addr)
    canvas = Canvas(fw, x=0, y=58)
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(lv.addr, canvas.addr)
    x0, y0, x1, y1 = thumb_box(canvas.px)
    assert x0 >= 375 - 28 and x1 <= 375


@pytest.mark.parametrize('rows, gap', [(5, 12), (8, 0)])
def test_a_menu_sheets_gap_stays_only_while_nothing_scrolls(fw, st, rows, gap):
    """menu_modal.c modal_frame (audit2 E1): a destructive row's 12 px gap stands while every row shows; a sheet that
    scrolls drops it, so it scrolls and ends on whole 32 px rows, not cut by 12 px (Now Playing's More)."""
    ui = Ui(fw)
    theme(fw)
    win = ui.window('sortselect_dialog', kind='dialog')
    lv = ui.add('list_view', win, (0, 44, 375, 210), 'list_view')
    view = ui.add('scroll_view', lv, (0, 0, 375, 210), 'scroll_view')
    items = [ui.add('list_item', view, (0, 0, 375, 32)) for _ in range(rows)]
    ui.add('label', view, (0, 0, 375, 12), 'gap')
    last = ui.add('list_item', view, (0, 0, 375, 32))
    fw.fn('modal_frame', None, C.c_void_p, C.c_void_p)(win.addr, lv.addr)
    assert last.box[1] - items[-1].box[1] == 32 + gap
    # the rows' height, and the pad that runs them on past the list end to the sheet's foot (LIST_PAD)
    assert view.props.get('virtual_h') - view.props.get('_list_pad') == 32 * (rows + 1) + gap


# A scrolling text's faded ends (list.marquee.fade, OLED's; list_marks.c marquee_fade_under and marquee_fade): the text
# itself fades over 12 columns at an end, inside stock's 2 px margins, onto whatever is behind it in the canvas's
# buffer: the end column shows exactly what was behind, the inside the text nearly whole.
FADE = 12
SCREEN_W, SCREEN_H = 375, 320
BLUE, RED, WHITE = 0xff2160d8, 0xffff453a, 0xffffffff


class Fade:
    """An hscroll_label 200 x 24 at (100, 50) whose text is text_w wide, scrolled off px, over a 375 x 320 BGRA buffer
    filled by behind(x, y); its paint is stock's text, white over the whole label, between the two calls."""
    def __init__(self, fw, st, text_w=260, off=0, timer=41, ellipses=1, behind=lambda x, y: 0xff000000,
                 clip=(0, 0, SCREEN_W, SCREEN_H)):
        self.fw, self.ui, self.behind, self.clip = fw, Ui(fw), behind, clip
        theme(fw)
        self.label = self.ui.add('hscroll_label', None, (100, 50, 200, 24), 'title')
        self.label.int_at(O['HSCROLL_TEXT_W']).value = text_w
        self.label.int_at(O['HSCROLL_XOFFSET']).value = off
        self.label.int_at(O['HSCROLL_TIMER']).value = timer
        self.label.byte_at(O['HSCROLL_ELLIPSES']).value = ellipses
        self.fb = (C.c_uint * (SCREEN_W * SCREEN_H))()
        self.lcd, self.raw = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.raw, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<ii', self.raw, CANVAS_X, 100, 50)  # the label's origin: stock paints it there
        struct.pack_into('<Q', self.lcd, O['LCD_OFFLINE_FB'], C.addressof(self.fb))
        for k, v in (('LCD_W', SCREEN_W), ('LCD_H', SCREEN_H), ('LCD_LINE_LENGTH', 4 * SCREEN_W),
                     ('LCD_FORMAT', O['LCD_BGRA8888'])):
            struct.pack_into('<i', self.lcd, O[k], v)
        fw.mock('style_get_int', lambda style, key, default, *_: i32(default))  # stock's margins: 2 px each
        fw.mock('canvas_get_clip_rect', lambda c, out, *_: C.memmove(out, struct.pack('<4i', *self.clip), 16) and 0)

    def px(self, x, y):
        return self.fb[y * SCREEN_W + x]

    def paint(self):
        """The buffer after the label's background (its copy), stock's text and the fade."""
        for y in range(SCREEN_H):
            for x in range(90, 310):
                self.fb[y * SCREEN_W + x] = self.behind(x, y)
        self.fw.fn('marquee_fade_under', None, C.c_void_p, C.c_void_p)(self.label.addr, C.addressof(self.raw))
        for y in range(50, 74):
            for x in range(100, 300):
                self.fb[y * SCREEN_W + x] = WHITE
        self.fw.fn('marquee_fade', None, C.c_void_p, C.c_void_p)(self.label.addr, C.addressof(self.raw))
        return self

    def faded(self, y=60):
        """The screen columns of row y that are not plain text."""
        return [x for x in range(100, 300) if self.px(x, y) != WHITE]


def fade_mix(behind, text, k):
    """Each byte of behind moved k/12 of the way to text's, as the fade blends."""
    return sum((((behind >> c) & 255) + ((((text >> c) & 255) - ((behind >> c) & 255)) * k // FADE)) << c
               for c in (0, 8, 16, 24))


def test_a_fade_needs_text_wider_than_its_column(fw, st):
    assert Fade(fw, st, text_w=196).paint().faded() == []  # 196 + the margins: exactly the box
    assert Fade(fw, st, text_w=197).paint().faded()


@pytest.mark.parametrize('behind', [BLUE, 0xff000000, RED], ids=['selection', 'page', 'destructive'])
def test_text_at_its_start_fades_at_the_right_into_what_is_behind_it(fw, st, behind):
    """The selection's blue, the page's black, a destructive answer's red: the end column is exactly the colour behind,
    each column in from it the text by one twelfth more; no colour is guessed."""
    fade = Fade(fw, st, behind=lambda x, y: behind).paint()
    end = 100 + 200 - 2 - 1
    assert fade.faded() == list(range(end - FADE + 1, end + 1))
    assert [fade.px(end - i, 60) for i in range(FADE)] == [fade_mix(behind, WHITE, i) for i in range(FADE)]
    assert fade.px(end - FADE // 2, 60) not in (behind, WHITE)  # mid-ramp: a blend of the two


def test_a_fade_over_art_ends_on_each_pixel_behind_it(fw, st):
    """Over a cover (a gradient here): every row's end column is that row's own pixel behind the text."""
    art = lambda x, y: 0xff000000 | (x * 3 & 255) << 16 | (y * 5 & 255) << 8 | (x + y) & 255  # noqa: E731
    fade = Fade(fw, st, behind=art).paint()
    end = 100 + 200 - 2 - 1
    assert all(fade.px(end, y) == art(end, y) for y in range(50, 74))
    assert all(fade.px(end - 6, y) == fade_mix(art(end - 6, y), WHITE, 6) for y in range(50, 74))


def test_text_scrolled_off_its_start_fades_at_both_ends_until_its_end_shows(fw, st):
    fade = Fade(fw, st, off=30).paint()
    assert fade.faded() == list(range(102, 102 + FADE)) + list(range(298 - FADE, 298))
    assert fade.px(102, 60) == 0xff000000 and fade.px(297, 60) == 0xff000000
    fade = Fade(fw, st, off=260 + 4 - 200).paint()  # its end in view: nothing past the right
    assert fade.faded() == list(range(102, 102 + FADE))


def test_a_fade_keeps_to_the_column_its_styles_margins_leave(fw, st):
    """A folder row's label starts under its icon, its text past margin_left: the fades end on that column."""
    fade = Fade(fw, st, text_w=200, off=10)
    margins = {b'margin_left': 40, b'margin_right': 6}
    fw.mock('style_get_int', lambda style, key, default, *_: margins.get(C.string_at(key), i32(default)))
    assert fade.paint().faded() == list(range(140, 140 + FADE)) + list(range(294 - FADE, 294))
    fade.label.int_at(O['HSCROLL_TEXT_W']).value = 154  # 200 - 46: it fits
    assert fade.paint().faded() == []


def test_a_fade_touches_nothing_outside_the_frames_clip(fw, st):
    """Pixels outside the clip are an older frame's, not this one's background: they are left alone."""
    fade = Fade(fw, st, clip=(0, 0, 290, SCREEN_H)).paint()  # the right end's columns run past 290
    assert fade.faded() == []
    fade = Fade(fw, st, clip=(0, 0, SCREEN_W, 60)).paint()  # rows from 60 down are outside
    assert fade.faded(59) and fade.faded(60) == []


@pytest.mark.parametrize('mode, light, names', [
    (0, 0, ['oled_mode_shuffle_d', 'oled_mode_repeat_d']), (1, 0, ['oled_mode_shuffle_d', 'oled_mode_repeat1_on']),
    (2, 1, ['oled_mode_shuffle_on', 'oled_mode_repeat_on']), (3, 1, ['oled_mode_shuffle_l', 'oled_mode_repeat_on'])])
def test_now_playings_modes_are_the_themes_pair_lit_by_stocks_mode(fw, st, mcl, mode, light, names):
    """playing.modes.icons (np_bar.c np_modes): the row's four buttons evenly across its target, shuffle, radio, repeat
    and Up Next; shuffle and repeat each the accent's while on (shuffle at MCL_MODE 2, repeat at 3 and at 2, as stock's Shuffle never ends, unless
    Off was chosen under it; repeat-once's glyph at 1), else the half's grey."""
    ui = Ui(fw)
    theme(fw)
    st('light').value, mcl.mode = light, mode
    win = ui.window('playing_page')
    button = ui.add('image', win, (53, 264, 269, 44), 'img_playmode')  # the row's target, x 53 to 322 (np_bar.c)
    fw.mock('widget_lookup', lambda w, name, deep, *_: button.addr if C.string_at(name) == b'img_playmode' and not deep else 0)
    loads, drawn = [], []
    fw.mock('widget_load_image', lambda w, name, bitmap, *_: loads.append(C.string_at(name).decode()) or 0)
    fw.mock('canvas_draw_icon', lambda c, bitmap, x, y, *_: drawn.append((i32(x), i32(y))))
    lcd, raw = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
    struct.pack_into('<Q', raw, CANVAS_LCD, C.addressof(lcd))
    fw.fn('np_modes', None, C.c_void_p, C.c_void_p)(win.addr, C.addressof(raw))
    half = 'l' if light else 'd'  # radio (off) and Up Next after them, in the half's ink
    assert loads == names + [f'oled_mode_radio_{half}', f'oled_mode_queue_{half}']
    assert drawn == [(64, 286), (228, 286), (146, 286), (311, 286)]  # shuffle, repeat, then radio and Up Next


class Row:
    """OLED's Now Playing row over glyphs whose ink is a known box: each 22 px image's opaque pixels are x 2 to 19,
    y 4 to 17 (its faint edge, alpha 40, a pixel round them, under NP_INK_ALPHA), so drawn centred on (cx, 286) its
    ink is (cx - 9, 279, 18, 14). Records the rounded boxes painted, as (x, y, w, h, colour)."""
    INK = (2, 4, 18, 14)

    def __init__(self, fw, st, mcl, mode=0, stage=0):
        self.fw = fw
        ui = Ui(fw)
        theme(fw)
        mcl.mode = mode
        st('np_stage', C.c_uint).value = stage
        self.win = ui.window('playing_page')
        self.button = ui.add('image', self.win, (53, 264, 269, 44), 'img_playmode')
        st('np_win', C.c_void_p).value = self.win.addr
        fw.mock('widget_lookup', lambda w, name, deep, *_: self.button.addr if C.string_at(name) == b'img_playmode' else 0)
        self.pixels = C.create_string_buffer(22 * 22 * 4)
        x, y, w, h = self.INK
        for py in range(y - 1, y + h + 1):
            for px in range(x - 1, x + w + 1):
                inside = x <= px < x + w and y <= py < y + h
                self.pixels[(py * 22 + px) * 4 + 3] = b"\xff" if inside else b"\x28"

        def load(w, name, bitmap, *_):
            struct.pack_into('<II', (C.c_char * 16).from_address(bitmap), 0, 22, 22)
            C.c_ushort.from_address(bitmap + 14).value = 1  # RGBA8888
            return 0
        fw.mock('widget_load_image', load)
        fw.mock('bitmap_lock_buffer_for_read', lambda *_: C.addressof(self.pixels))
        fw.mock('bitmap_get_line_length', lambda *_: 22 * 4)
        self.boxes = []

        def box(canvas, r, src, color, radius, *_):
            red, green, blue = C.string_at(color, 3)  # RGBA's bytes in order
            self.boxes.append((*struct.unpack('<4i', C.string_at(r, 16)), red << 16 | green << 8 | blue))
            return 0
        fw.mock('canvas_fill_rounded_rect', box)
        self.now = 1000
        fw.mock('time_now_ms', lambda *_: self.now)

    def paint(self):
        self.boxes.clear()
        lcd, raw = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', raw, CANVAS_LCD, C.addressof(lcd))
        self.fw.fn('np_modes', None, C.c_void_p, C.c_void_p)(self.win.addr, C.addressof(raw))
        return self.boxes

    def tap(self, x):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, O['EVT_CLICK'])
        struct.pack_into('<ii', event, O['EVENT_X'], x, 286)
        return self.fw.fn('np_mode_click', C.c_int, C.c_void_p, C.c_void_p)(self.button.addr, C.addressof(event))


def ink_box(cx, pad, dot=False):
    """Row's ink centred on cx, 286, joined with the on dot (y 299 to 302) when it shows, padded pad px."""
    x, y, w, h = cx - 9, 279, 18, (303 - 279) if dot else 14
    return (x - pad, y - pad, w + 2 * pad, h + 2 * pad)


@pytest.mark.parametrize('mode, dot', [(0, False), (2, True)])
def test_now_playings_focus_outline_fits_the_glyph_and_its_dot(fw, st, mcl, mode, dot):
    """22 v4 (docs/oled-design.md#now-playing): the Centre cycle's Shuffle stage draws a 2 px white outline 6 px clear
    of the glyph's ink (8 px to its outer edge), the ink alone with shuffle off, the ink and the on dot with it on."""
    row = Row(fw, st, mcl, mode, stage=1)  # NP_STAGE_SHUFFLE
    boxes = row.paint()
    outer, inner = ink_box(64, 8, dot), ink_box(64, 6, dot)
    assert boxes[:2] == [(*outer, 0xffffff), (*inner, 0)]
    # the dot after the glyph, over the outline's inside
    assert ((62, 299, 4, 4) in [b[:4] for b in boxes[2:]]) == dot


def test_now_playings_focus_outline_hides_while_touch_is_the_modality(fw, st, mcl):
    """oled-design.md#focus-ring: the Centre cycle's outline is the wheel's, so after a touch (st.touch_mode,
    ui_focus_shown 0) the stage still stands but no outline is drawn; the wheel's next turn brings it back."""
    row = Row(fw, st, mcl, 0, stage=1)  # NP_STAGE_SHUFFLE
    st('touch_mode', C.c_int).value = 1
    assert row.paint() == []
    st('touch_mode', C.c_int).value = 0
    assert row.paint()[:2] == [(*ink_box(64, 8), 0xffffff), (*ink_box(64, 6), 0)]


def test_now_playings_tapped_button_shows_a_square_that_fades(fw, st, mcl):
    """22 v4: a tap puts #2C2C2E under the glyph's ink, 8 px clear on every side, fading over 250 ms; the wheel's outline
    is not drawn for it, and Up Next, which has no dot, fits its glyph alone."""
    row = Row(fw, st, mcl, 0)
    assert row.paint() == []
    assert row.tap(311) == 1  # Up Next
    assert row.paint() == [(*ink_box(311, 8), 0x2c2c2e)]
    row.now += 125
    assert row.paint() == [(*ink_box(311, 8), 0x161617)]  # half way
    row.now += 125
    assert row.paint() == []


def test_now_playings_heart_and_more_are_the_themes_glyphs_in_each_half(fw, st):
    """np_bar.c np_glyphs, on each paint of OLED's Now Playing: stock's play_fav and play_unfav become the theme's full
    heart and its empty one in the half's ink, an empty heart follows a Light Mode change, an app's heart stays, and
    More's states take the half's dots (pressed, the dimmer ones) once per half."""
    ui = Ui(fw)
    theme(fw)
    win = ui.window('playing_page')
    fav = ui.add('image', win, (289, 145, 34, 34), 'img_fav', image='play_unfav')
    more = ui.add('image', win, (323, 145, 34, 34), 'img_more', **{'style:normal:bg_image': 'play_more'})
    st('np_win', C.c_void_p).value = win.addr
    fw.fn('np_glyphs_bind', None, C.c_void_p)(win.addr)
    glyphs = fw.fn('np_glyphs', None, C.c_void_p)
    states = ('normal', 'pressed', 'selected', 'disable', 'focused')
    glyphs(win.addr)
    assert fav.props['image'] == 'oled_np_unfav_d'
    assert [more.props[f'style:{s}:bg_image'] for s in states] == ['oled_np_more_d', 'oled_np_moredown_d'] + ['oled_np_more_d'] * 3
    fav.props['image'] = 'play_fav'  # stock's click
    glyphs(win.addr)
    assert fav.props['image'] == 'oled_np_fav_d'  # Text Primary, no colour: the half's too
    st('light').value = 1
    glyphs(win.addr)
    assert fav.props['image'] == 'oled_np_fav_l' and more.props['style:pressed:bg_image'] == 'oled_np_moredown_l'
    fav.props['image'] = 'oled_np_unfav_d'
    glyphs(win.addr)
    assert fav.props['image'] == 'oled_np_unfav_l' and more.props['style:normal:bg_image'] == 'oled_np_more_l'
    fav.props['image'] = 'tidal_fav_add'
    glyphs(win.addr)
    assert fav.props['image'] == 'tidal_fav_add'


class Modes:
    """OLED's Now Playing on top with the row's target at (53, 264, 269, 44): a tap goes to the nearest of shuffle,
    radio, repeat and Up Next (np_bar.c np_mode_click). config_playmode sets stock's mode as stock's does."""
    def __init__(self, fw, st, mcl, mode):
        self.fw, self.mcl = fw, mcl
        ui = Ui(fw)
        self.settings = theme(fw)
        mcl.mode = mode
        self.win = ui.window('playing_page')
        self.button = ui.add('image', self.win, (53, 264, 269, 44), 'img_playmode')
        st('np_win', C.c_void_p).value = self.win.addr
        fw.mock('widget_lookup', lambda w, name, deep, *_: self.button.addr if C.string_at(name) == b'img_playmode' else 0)
        self.sent = []
        fw.mock('config_playmode', lambda m, notify, *_: self.sent.append(i32(m)) or setattr(mcl, 'mode', i32(m)) or 0)

    def tap(self, side):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, O['EVT_CLICK'])
        struct.pack_into('<ii', event, O['EVENT_X'], {'shuffle': 64, 'radio': 146}.get(side, 228), 286)
        return self.fw.fn('np_mode_click', C.c_int, C.c_void_p, C.c_void_p)(self.button.addr, C.addressof(event))


def test_shuffle_and_repeat_are_two_controls_over_stocks_one_mode(fw, st, mcl):
    m = Modes(fw, st, mcl, 0)  # order: shuffle off, repeat Off
    assert m.tap('shuffle') == 1 and m.sent == [2]  # shuffle on
    m.tap('repeat')  # repeat All under shuffle: stock's Shuffle plays on either way, nothing sent
    assert m.sent == [2] and mcl.mode == 2
    m.tap('repeat')  # Repeat One
    assert m.sent == [2, 1]
    m.tap('shuffle')  # shuffle off under Repeat One: still Repeat One
    assert m.sent == [2, 1]
    m.tap('repeat')  # repeat Off, shuffle off: order
    assert m.sent == [2, 1, 0]


def test_the_radio_tap_starts_a_radio_of_the_playing_song(fw, st, mcl):
    """The Radio button starts a song radio (song_radio.c song_radio_tap, on a timer: a radio's queue opens Now
    Playing). It opened the playing app's page (addon_more) before, which did nothing for the Subsonic app, whose
    q2app has no more= (the user's dead tap, 2026-11-16 17:09); stock's mode is left alone."""
    m = Modes(fw, st, mcl, 0)
    timers: list = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(fn) or 1)
    assert m.tap('radio') == 1 and m.sent == []
    assert timers[-1] == C.cast(fw.lib.song_radio_tap, C.c_void_p).value


@pytest.mark.parametrize('radio', [False, True], ids=['antenna', 'pill'])
def test_no_x_on_the_radio_button_or_its_pill_is_up_next(fw, st, mcl, radio):
    """The user's, 2026-11-18: a tap on Radio seemed to open Up Next. Every x from the antenna's or the pill's left
    edge to its right (on screen, the pill 112 to 204 while radio is on) is Radio's: np_mode_click asks for
    song_radio_tap and never for np_queue."""
    m = Modes(fw, st, mcl, 0)
    fw.var('song_radio__sr', C.c_int).value = int(radio)  # sr.local: a local radio plays
    timers: list = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(fn) or 1)
    radio_tap, queue = (C.cast(getattr(fw.lib, n), C.c_void_p).value for n in ('song_radio_tap', 'np_queue'))
    for x in range(112, 205) if radio else range(135, 158):
        timers.clear()
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, O['EVT_CLICK'])
        struct.pack_into('<ii', event, O['EVENT_X'], x, 286)
        assert fw.fn('np_mode_click', C.c_int, C.c_void_p, C.c_void_p)(m.button.addr, C.addressof(event)) == 1
        assert radio_tap in timers and queue not in timers, x


class Corners(Row):
    """Row's page with its window handler's touches (np_taps.c np_tap): a touch down at one point and up at
    another, through np_tap, then np_mode_click on the row's target when both were on it (AWTK's click)."""
    def __init__(self, fw, st, mcl, radio=False):
        super().__init__(fw, st, mcl)
        fw.var('song_radio__sr', C.c_int).value = int(radio)  # sr.local: a local radio plays
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or 1)
        self.np_tap = fw.fn('np_tap', C.c_int, C.c_void_p, C.c_void_p)
        self.sent: list = []
        fw.mock('config_playmode', lambda m, notify, *_: self.sent.append(i32(m)) or setattr(mcl, 'mode', i32(m)) or 0)

    def touch(self, kind, x, y):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, kind)
        struct.pack_into('<ii', event, O['EVENT_X'], x, y)
        return self.np_tap(None, C.addressof(event))

    def press(self, x, y, to=None):
        """What the touch did: 'shuffle' (stock's mode sent), 'radio', 'queue' or None."""
        self.timers.clear()
        self.sent.clear()
        to = to or (x, y)
        assert self.touch(O['EVT_POINTER_DOWN'], x, y) == 0
        assert self.touch(O['EVT_POINTER_UP_BEFORE'], *to) == 0
        on = lambda px, py: 53 <= px < 322 and 264 <= py < 308
        if on(x, y) and on(*to):
            self.tap(to[0])
        lib = self.fw.lib
        if C.cast(lib.np_queue, C.c_void_p).value in self.timers:
            return 'queue'
        if C.cast(lib.song_radio_tap, C.c_void_p).value in self.timers:
            return 'radio'
        return 'shuffle' if self.sent else None


# Each button's touch past the target (the user's, 2026-11-18 00:07 and 05:10): shuffle's to the screen's left edge,
# Up Next's to its right, each from the row's top down to the foot; Radio's and Repeat's under the target, down to the
# foot; across, each as far as the target gives it (the midpoints with its neighbours). Every hit rect on screen,
# x0, x1, y0, y1 inclusive, radio off and on (the pill 112 to 204). The drawn row, its press and its ring stay where
# they are; nothing reaches above y 264, the times' row (the scrubber's target ends at 242).
HITS = {False: {'shuffle': (0, 105, 264, 319), 'radio': (106, 187, 308, 319), 'repeat': (188, 269, 308, 319),
                'queue': (270, 374, 264, 319)},
        True: {'shuffle': (0, 111, 264, 319), 'radio': (112, 205, 308, 319), 'repeat': (206, 281, 308, 319),
               'queue': (282, 374, 264, 319)}}


@pytest.mark.parametrize('radio', [False, True], ids=['radio-off', 'radio-on'])
def test_each_row_button_takes_a_tap_out_to_its_hit_rect(fw, st, mcl, radio):
    row = Corners(fw, st, mcl, radio)
    want = {'shuffle': None if radio else 'shuffle', 'repeat': None if radio else 'shuffle',  # radio on: ignored
            'radio': 'radio', 'queue': 'queue'}
    for name, (x0, x1, y0, y1) in HITS[radio].items():
        for x, y in ((x0, y1), (x1, y1), (x0, max(y0, 309)), (x1, max(y0, 309)), ((x0 + x1) // 2, 318)):
            assert row.press(x, y) == want[name], (name, x, y)
        if name in ('shuffle', 'queue'):
            assert row.press(x0 if name == 'shuffle' else x1, y0) == want[name], name
    # the times' row above is no button's; a drag is no tap
    assert row.press(10, 263) is None and row.press(360, 263) is None and row.press(146, 263) is None
    assert row.press(20, 300, to=(60, 300)) is None
    # down in one button's rect and up in its neighbour's: nothing
    assert row.press(HITS[radio]['radio'][1], 315, to=(HITS[radio]['repeat'][0] + 1, 315)) is None


def test_mores_touch_reaches_right_and_down_to_the_bar_on_the_art_page(fw, st, mcl):
    """More's reach (np_bar.c more_reach, the user's, 2026-11-18 05:10): a tap down and up in x 292 to 374, y 172 to the
    progress bar's target top (214 here) clicks More, as its glyph does; outside it, half in it, on the glyph itself (the
    glyph's own click) or off the art's page (the lyrics shown, or full screen) the reach does nothing."""
    ui = Ui(fw)
    theme(fw)
    win = ui.window('playing_page')
    slide = ui.add('slide_view', win, (0, 0, 375, 209), 'slide_view', value=0)
    ui.add('image', slide, (182, 142, 56, 42), 'img_fav', image='oled_np_unfav_d')
    more = ui.add('image', slide, (327, 138, 34, 34), 'img_more')
    ui.add('slider', win, (24, 214, 327, 22), 'slider_play')
    st('np_win', C.c_void_p).value = win.addr
    st('np_slide', C.c_void_p).value = slide.addr
    st('np_full', C.c_int).value = 0
    fw.fn('np_glyphs_bind', None, C.c_void_p)(win.addr)
    clicks: list = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: clicks.append(w) or 0)
    tap = fw.fn('np_corner_tap', None, C.c_int, C.c_int, C.c_int, C.c_int)

    def more_clicked(x, y, to=None):
        clicks.clear()
        tap(x, y, *(to or (x, y)))
        return clicks == [more.addr]
    for x, y in ((292, 172), (374, 172), (292, 213), (374, 213), (300, 190), (360, 200)):
        assert more_clicked(x, y), (x, y)
    for x, y in ((291, 190), (300, 171), (300, 214), (360, 230), (340, 150), (210, 160)):
        assert not more_clicked(x, y) and clicks == [], (x, y)  # left of it, above, the bar, the glyph, the heart
    assert not more_clicked(300, 190, to=(280, 190))  # down in the reach, up out of it
    slide.props['value'] = 1  # the lyrics' page
    assert not more_clicked(300, 190)
    slide.props['value'] = 0
    st('np_full', C.c_int).value = 1  # the lyrics full screen
    assert not more_clicked(300, 190)
    st('np_full', C.c_int).value = 0
    assert more_clicked(300, 190)


def test_the_corners_draw_nothing_new(fw, st, mcl):
    """Touch only (the user's, 00:07): a corner's tap shows the press square where a tap on the glyph does."""
    row = Corners(fw, st, mcl)
    row.press(370, 318)
    assert row.paint() == [(*ink_box(311, 8), 0x2c2c2e)]


def test_each_control_finds_the_other_as_it_was(fw, st, mcl):
    m = Modes(fw, st, mcl, 3)  # repeat All
    m.tap('shuffle')
    m.tap('shuffle')
    assert m.sent == [2, 3]  # repeat All again, not order
    m.tap('repeat')  # Repeat One, then shuffle on under it: kept until repeat leaves One
    m.tap('shuffle')
    assert m.sent == [2, 3, 1] and mcl.mode == 1
    m.tap('repeat')  # Off: shuffle comes back
    assert m.sent == [2, 3, 1, 2]


@pytest.mark.parametrize('mode, taps, names', [
    (0, ['shuffle'], ['oled_mode_shuffle_on', 'oled_mode_repeat_d']),  # repeat Off chosen under shuffle
    (2, ['repeat'], ['oled_mode_shuffle_on', 'oled_mode_repeat1_on'])])  # shuffle kept under Repeat One
def test_what_stocks_mode_cant_say_outlives_a_restart(fw, st, mcl, library, tmp_path, mode, taps, names):
    """np_bar.c np_modes_read: what MCL_MODE can't hold is saved as [IPOD] NPREPOFF and NPSHUFONE, and a fresh load
    reads it back, so the pair draws after a restart as it did before. Stock keeps MCL_MODE itself (PLAYMODE)."""
    m = Modes(fw, st, mcl, mode)
    for side in taps:
        m.tap(side)
    saved = {k: str(v).encode() for k, v in m.settings.written}
    (tmp_path/'restart').mkdir()
    again = host.Fw(library, tmp_path/'restart')  # scratch zeroed, as at power-on; mcl's globals stay
    try:
        theme(again, **saved)
        win = Ui(again).window('playing_page')
        button = Ui(again).add('image', win, (53, 264, 269, 44), 'img_playmode')
        again.mock('widget_lookup', lambda w, name, deep, *_: button.addr if C.string_at(name) == b'img_playmode' else 0)
        loads: list = []
        again.mock('widget_load_image', lambda w, name, bitmap, *_: loads.append(C.string_at(name).decode()) or 0)
        lcd, raw = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', raw, CANVAS_LCD, C.addressof(lcd))
        again.fn('np_modes', None, C.c_void_p, C.c_void_p)(win.addr, C.addressof(raw))
        assert loads == names + ['oled_mode_radio_d', 'oled_mode_queue_d']
    finally:
        again.close()


def test_a_tap_that_keeps_what_was_saved_writes_nothing(fw, st, mcl):
    m = Modes(fw, st, mcl, 2)  # shuffle, repeat All: nothing kept yet
    m.tap('repeat')  # Repeat One with shuffle on: NPSHUFONE alone moves
    assert m.settings.written == [('NPSHUFONE', 1)]
    m.tap('repeat')  # Off: shuffle still on, so only NPREPOFF moves
    assert m.settings.written == [('NPSHUFONE', 1), ('NPREPOFF', 1)]
    m.tap('repeat')  # All
    m.tap('repeat')  # One again: both as saved
    assert m.settings.written == [('NPSHUFONE', 1), ('NPREPOFF', 1), ('NPREPOFF', 0)]


@pytest.mark.parametrize('timer, ellipses, faded', [(0, 1, False), (41, 1, True), (0, 0, True)])
def test_a_label_at_rest_on_its_ellipsis_keeps_it(fw, st, timer, ellipses, faded):
    """Stock ends a stopped label with ellipses in "...": that one is left alone. Running, or stopped without an ellipsis
    (cut at its column), it fades."""
    assert bool(Fade(fw, st, timer=timer, ellipses=ellipses).paint().faded()) == faded


def test_other_widgets_and_other_buffers_get_no_fade(fw, st):
    """A plain label scrolls nothing; a canvas whose buffer is not 32-bit (an animation's own) shows the text clipped."""
    fade = Fade(fw, st)
    assert fade.paint().faded()
    fade.label = fade.ui.add('label', None, (100, 50, 200, 24), 'plain')
    fade.label.int_at(O['HSCROLL_TEXT_W']).value = 260
    assert fade.paint().faded() == []
    fade = Fade(fw, st)
    struct.pack_into('<i', fade.lcd, O['LCD_FORMAT'], 0)
    assert fade.paint().faded() == []
    assert THEMES[OLED]['marquee_fade'] == 1  # ringnav_paint_bg and ringnav_paint ask list.marquee.fade first


# The status bar's play state (ringnav_showface, each second).
class Bar:
    NAMES = ('img_bt', 'img_wifi', 'label_battery', 'view_battery', 'img_battery', 'label_idle', 'img_state',
             'label_eq', 'label_title', 'label_clock')

    def __init__(self, fw, status=0):
        self.fw, self.ui = fw, Ui(fw)
        self.status = status
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.bar = self.ui.window('statusbar', (0, 0, 375, 26), top=False)
        fw.var('fw_ctx_system_bar', C.c_void_p).value = self.bar.addr
        for name in self.NAMES:
            self.ui.add('image' if name.startswith('img') else 'label', self.bar, (0, 0, 20, 20), name)
        self.w = {n.name: n for n in self.bar.children}

    def sync(self):
        self.fw.fn('ringnav_showface', C.c_int, C.c_void_p)(self.bar.addr)


@pytest.mark.parametrize('status, shown, image', [(2, 1, 'oled_play_d'), (3, 1, 'oled_pause_d'), (0, 0, None), (1, 0, None)])
def test_the_bar_shows_whats_playing(fw, status, shown, image):
    theme(fw)
    bar = Bar(fw, status)
    bar.w['label_eq'].visible = 0
    bar.sync()
    assert bar.w['img_state'].visible == shown and bar.w['img_state'].props.get('image') == image
    assert bar.w['label_idle'].visible == (not shown)  # idle when neither the state nor EQ shows


def test_idle_stays_hidden_under_eq(fw):
    theme(fw)
    bar = Bar(fw, 0)
    bar.sync()
    assert bar.w['label_eq'].visible and not bar.w['label_idle'].visible


def test_a_second_sync_with_nothing_new_sets_no_image(fw):
    """The image is set only when it changes (a set image is a relayout and a repaint); visibility
    writes are stock's no-ops when unchanged."""
    theme(fw)
    bar = Bar(fw, 2)
    bar.sync()
    shown = {n: w.visible for n, w in bar.w.items()}
    bar.sync()
    assert len(fw.calls('image_base_set_image')) == 1 and {n: w.visible for n, w in bar.w.items()} == shown
    bar.status = 3
    bar.sync()
    assert bar.w['img_state'].props['image'] == 'oled_pause_d'


@pytest.mark.parametrize('light', [0, 1])
@pytest.mark.parametrize('status', [2, 3])
def test_the_state_is_the_themes_own_art_by_half(fw, light, status):
    """The play state is the theme's own art (tools/art.py, its art prefix), by state and half."""
    theme(fw, LIGHT=str(light).encode())
    bar = Bar(fw, status)
    bar.sync()
    state = 'play' if status == 2 else 'pause'
    assert bar.w['img_state'].props['image'] == f'oled_{state}_{"dl"[light]}'
    assert bar.w['label_eq'].visible  # stock's, left alone


@pytest.mark.parametrize('status', [2, 3])
def test_on_homes_list_the_state_stays_by_the_battery_and_the_title_shows(fw, status):
    """Home's own list is like any page: the play state shows in img_state and leaves the title alone."""
    theme(fw)
    bar = Bar(fw, status)
    home = bar.ui.window('home_page')
    bar.sync()
    on_home = bar.w['img_state'].props.get('image')
    assert bar.w['img_state'].visible and on_home and bar.w['label_title'].visible
    bar.ui.window('allmusic_page')
    bar.sync()
    assert bar.w['img_state'].visible and bar.w['img_state'].props['image'] == on_home  # as over a page
    bar.ui.top = home
    bar.status = 0
    bar.sync()
    assert not bar.w['img_state'].visible and bar.w['label_title'].visible


# The selection, painted behind the rows (ringnav_paint_bg).
@pytest.fixture(scope='module')
def layout(library):
    obj = library.parent/'navigation.o'
    return host.offsets(obj, 'scheme_t')


class Scheme:
    """The live scheme's fields (theme.h scheme_t), as the theme table states them."""
    def __init__(self, fw, layout):
        base = fw.fn('scheme', C.c_void_p)()
        tones = C.c_void_p.from_address(base + layout['tones']).value or 0
        self.tones = [C.c_uint.from_address(tones + 4 * i).value for i in range(5)]
        get = lambda name: C.c_uint.from_address(base + layout[name]).value
        self.chevron, self.muted, self.sel_text, self.paper, self.ink = (get('chevron'), get('muted'), get('sel_text'),
                                                                         get('paper'), get('ink'))


class Page:
    """A list page, its row sel the one selected and settled (its count and scope the
    view's), painted as stock paints a widget's background."""
    def __init__(self, fw, rows=4, pitch=48, sel=1, name='localmusic_page', **config):
        self.fw = fw
        theme(fw, **config)
        self.ui = Ui(fw)
        self.view, self.rows = library(fw, self.ui, rows, pitch, name)
        if sel is not None:
            self.view.props.update({'_ringnav_count': rows, '_ringnav_scope': 1, '_ringnav_index': sel})
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append(fn) or len(self.timers))
        self.canvas = Canvas(fw)

    def paint(self, w=None):
        self.fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)((w or self.view).addr, self.canvas.addr)
        return self.canvas

    def band(self):
        """The rows painted, top to bottom, each (x0, x1, colours across)."""
        rows = {}
        for (x, y), c in self.canvas.px.items():
            rows.setdefault(y, {})[x] = c
        return {y: (min(r), max(r) + 1, [r[x] for x in sorted(r)]) for y, r in sorted(rows.items())}


# The bar's flat form, Reduce Motion's: the shape tests below pin its inset, ends, pitch and tones; its rest lift
# (tilt.c, 1.02 and a shadow) is pinned by test_the_bar_at_rest_is_lifted_with_a_shadow_under_it and test_tilt.py.
FLAT = {'REDUCEMOTION': b'1'}


def solid(row):
    return len(set(row[2])) == 1 and row[2][0]


def mix(a, b, j, n):
    """0xRRGGBB j/n of the way from a to b, per channel."""
    return sum((((a >> s & 255) * (n - j) + (b >> s & 255) * j) // n) << s for s in (0, 8, 16))


def neutral(c):
    """A grey: its channels within 16 of each other."""
    ch = [c >> s & 255 for s in (0, 8, 16)]
    return max(ch) - min(ch) <= 16


def style_ink(fw, color=0xffffffff):
    """The text colour stock's style lookup answers (ringnav_style_color) for the widget painting now:
    stock's white text, as the row paints it."""
    fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))
    c = C.c_uint(color)
    fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(C.byref(c), None, b'text_color', 0)
    return c.value


def text_ink(fw, st, w, color=0xffffffff):
    """style_ink while w paints (stock's paint of w under way, as ringnav_paint_bg marks it)."""
    st('paint_w', C.c_void_p).value = w.addr
    return style_ink(fw, color)


def test_the_selected_row_is_the_accents_bar_inset_with_round_ends(fw, st, layout):
    """list.selection: the bar sel_inset in from each side, its ends sel_radius round, a one-pixel highlight over the
    accent's gradient, each row j / 47 of the way top tone to bottom (OLED's tones are one blue: solid)."""
    page = Page(fw, **FLAT)
    S = Scheme(fw, layout)
    T = THEMES[OLED]
    inset, radius = T['sel_inset'], T['sel_radius']
    band = page.band() if page.paint() else {}
    assert list(band) == list(range(48, 96))
    assert all(band[y][:2] == (inset, 375 - inset) for y in range(48 + radius, 96 - radius))
    assert band[48][0] > inset and band[95][0] > inset  # its ends round in
    assert solid(band[72]) == rgba(mix(S.tones[0], S.tones[1], 24, 47)) and band[48][2][len(band[48][2]) // 2] == rgba(S.tones[4])
    assert S.tones[0] == T['schemes'][T['accents'].bit_length() - 1]['tones'][0]  # OLED's Accent Blue
    assert page.canvas.restored() and page.canvas.clip == [0, 0, 375, 320]


def test_the_bar_at_rest_is_lifted_with_a_shadow_under_it(fw, layout):
    """The press's rest state (tilt.c, docs/internals.md#row-press): the bar 1.02 of its size about its centre, 3 px
    wider each side on a 375 px list, the same height, and the accent's translucent shadow in the rows under it."""
    inset = THEMES[OLED]['sel_inset']
    page = Page(fw)
    band = page.band() if page.paint() else {}
    grow = (375 - 2 * inset) * 20 // 1000 // 2
    opaque = sorted(x for (x, y), c in page.canvas.px.items() if y == 72 and c >> 24 == 255)  # the bar, not its shadow
    assert (opaque[0], opaque[-1] + 1) == (inset - grow, 375 - inset + grow)
    assert min(band) == 48 and max(band) > 95  # the shadow below the bar's last row
    assert all(c >> 24 < 255 for c in band[max(band)][2])  # translucent


# A theme's own selection shape and accent (an OLED theme's: a flat exact blue, 8 px in, 6 px ends), written into the
# live theme as tools/theme.py's table would state them: list.selection.radius and inset, an own scheme's tones.
OLED_TONES = (C.c_uint * 5)(0x2f5bea, 0x2f5bea, 0x5a7cee, 0x5a7cee, 0x2f5bea)


@pytest.fixture(scope='module')
def theme_layout(library):
    return host.offsets(library.parent/'navigation.o', 'theme_t')


def poke(address, ctype, value):
    """Write value over the table's read-only copy (this test's own load of the library)."""
    page = address & ~0xfff
    assert not libc.mprotect(page, 0x2000, 3)  # PROT_READ | PROT_WRITE
    ctype.from_address(address).value = value


def own_shape(fw, theme_layout, layout, radius, inset):
    T = fw.fn('theme', C.c_void_p)()
    poke(T + theme_layout['sel_radius'], C.c_ubyte, radius)
    poke(T + theme_layout['sel_inset'], C.c_ubyte, inset)
    poke(fw.fn('scheme', C.c_void_p)() + layout['tones'], C.c_void_p, C.addressof(OLED_TONES))


def test_a_theme_s_own_bar_is_its_inset_its_radius_and_its_tones(fw, layout, theme_layout):
    page = Page(fw, **FLAT)
    own_shape(fw, theme_layout, layout, 6, 8)
    band = page.band() if page.paint() else {}
    blue = rgba(0x2f5bea)
    assert list(band) == list(range(48, 96)) and solid(band[72]) == blue  # flat: its highlight is its top tone
    # 6 px ends: the rows from the 7th to the 7th from the bottom span the row less 8 a side; those nearer round in
    assert all(band[y][:2] == (8, 375 - 8) and solid(band[y]) == blue for y in range(48 + 6, 96 - 6))
    assert all(band[y][0] > 8 and band[y][2][0] != blue for y in (48, 95))
    assert band[48 + 5][:2] == (8, 375 - 8)  # the last row in, by under a pixel: its edge pixels all but opaque


def test_a_theme_s_square_ended_bar_keeps_its_inset(fw, layout, theme_layout):
    page = Page(fw, **FLAT)
    own_shape(fw, theme_layout, layout, 0, 8)
    band = page.band() if page.paint() else {}
    assert list(band) == list(range(48, 96)) and all(r[:2] == (8, 375 - 8) and solid(r) == rgba(0x2f5bea) for r in band.values())


def test_an_own_scheme_s_tones_compile_into_its_row(tmp_path):
    """tools/theme.py's table for a theme whose own scheme states its tones: that row's tones are them, every ACCENTS
    row's its own, as cc reads the emitted C."""
    T = host.tools_theme
    text = (host.ROOT/'assets/themes/oled/theme.xml').read_text()
    own = re.sub(r'(<colorScheme name="oled-blue"[^>]*tones=")[^"]*"', r'\1#2f5bea #2f5bea #5a7cee #5a7cee #2f5bea"', text)
    assert own != text  # OLED's own scheme, its tones restated
    root = T.parse(own, 'oled/theme.xml')
    t = T.resolve(root, {'oled': root})
    T.validate(t, 'oled/theme.xml')
    T.emit([t], tmp_path)
    inc = (tmp_path/'themes.inc').read_text()
    rows = inc[inc.index('static const unsigned oled_tones'):inc.index('static const option_t')]
    (tmp_path/'tones.c').write_text(
        '#include <stdio.h>\n#include "theme.h"\nenum { ACCENT_N = %d };\n%s\nstatic const unsigned accents[][5] = { ACCENTS };\n%s'
        'int main(void) {\n    for (int v = 0; v < %d; ++v)\n'
        '        printf("%%06x %%06x %%06x %%06x %%06x\\n", oled_schemes[v].tones[0], oled_schemes[v].tones[1],\n'
        '               oled_schemes[v].tones[2], oled_schemes[v].tones[3], oled_schemes[v].tones[4]);\n}\n'
        % (len(T.ACCENT_NAMES), T.ACCENTS_BLOCK, rows, len(t['schemes'])))
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Werror', '-I', str(host.ROOT/'patch'), str(tmp_path/'tones.c'), '-o', str(tmp_path/'tones')],
                   check=True)
    out = subprocess.run([str(tmp_path/'tones')], capture_output=True, text=True, check=True).stdout.splitlines()
    assert out == [' '.join(f'{x:06x}' for x in row) for row in T.ACCENT_TONES] + ['2f5bea 2f5bea 5a7cee 5a7cee 2f5bea']


def test_a_radius_past_half_the_row_is_the_pill(fw, layout, theme_layout):
    """SEL_MAX_RADIUS, the default, and any radius past half the row draw the pill alike."""
    bands = []
    for radius in (O['SEL_MAX_RADIUS'], 24, 30):
        page = Page(fw)
        own_shape(fw, theme_layout, layout, radius, O['SEL_INSET'])
        bands.append(page.band() if page.paint() else {})
    assert bands[0] == bands[1] == bands[2] and bands[0][48][0] > O['SEL_INSET'] + 10


@pytest.mark.parametrize('stop, lit', [(1, True), (2, False)])
def test_the_shuffle_rows_label_on_the_bar_keeps_the_selections_ink_in_light_mode(fw, st, stop, lit):
    """OLED's Shuffle row stands outside the list (navigation.c shuffle_paint): while its Shuffle is lit its label takes
    the selection's text colour, white on the bar in Light Mode too, not the page's flipped ink; on Sort's stop it is
    page text again."""
    page = Page(fw)
    page.paint()
    st('light').value = 1
    label, view = page.rows[2].children[0], page.rows[3]
    fw.var('navigation__shuffle_label', C.c_void_p).value = label.addr
    st('action_view', C.c_void_p).value = st('shuffle_on', C.c_void_p).value = view.addr
    st('shuffle_stop').value = stop
    assert (text_ink(fw, st, label) == 0xffffffff) == lit


def test_a_fresh_page_settles_on_its_first_row(fw, layout):
    page = Page(fw, sel=None, **FLAT)
    band = page.band() if page.paint() else {}
    assert min(band) == 0 and max(band) == 47
    assert page.view.props['_ringnav_index'] == 0 and page.view.props['_ringnav_count'] == 4


def test_touch_hides_the_selection_until_the_wheel(fw, st):
    page = Page(fw)
    st('touch_mode').value = 1
    assert not page.paint().px and page.view.props['_ringnav_index'] == 1


def test_the_band_takes_a_rows_whole_pitch_under_its_button(fw):
    """The Library's row is a button over its pitch's top: the bar fills the row."""
    page = Page(fw, **FLAT)
    for item in page.view.children:
        item.move(*item.box[:3], 56)
        item.children[0].move(20, 0, 335, 48)
    for i, item in enumerate(page.view.children):
        item.move(0, 56 * i, 375, 56)
    band = page.band() if page.paint() else {}
    assert (min(band), max(band) + 1) == (56, 112)


def test_a_new_row_starts_its_marquee_timer_and_the_same_row_doesnt(fw, st):
    page = Page(fw)
    marq_moved = C.cast(fw.lib.navigation__marq_moved, C.c_void_p).value
    page.paint()
    assert page.timers.count(marq_moved) == 1 and st('marq_row', C.c_void_p).value == page.rows[1].addr
    page.paint()
    assert page.timers.count(marq_moved) == 1
    page.view.props['_ringnav_index'] = 2
    page.paint()
    assert page.timers.count(marq_moved) == 2 and st('marq_id').value == 2


def grid(page, columns=3):
    """The view's rows as an album grid's: each item a row of tiles, each a third of the width."""
    w = 375 // columns
    for item in page.view.children:
        button = item.children[0]
        button.move(0, 0, w - 1, item.box[3])
        for k in range(1, columns):
            page.ui.add('button', item, (k * w, 0, w - 1, item.box[3]), f'{button.name}.{k}').clickable()


def test_a_grid_tile_keeps_its_own_card_inset_all_round(fw, layout):
    """A tile's card is the accent's top tone, square, TILE_INSET inside the tile."""
    page = Page(fw)
    grid(page)
    page.view.props.update({'_ringnav_count': 12, '_ringnav_index': 4})  # the second row's middle tile
    page.paint()
    t = O['TILE_INSET']
    assert page.canvas.boxes == [((125 + t, 48 + t, 124 - 2 * t, 48 - 2 * t), rgba(Scheme(fw, layout).tones[0]), 0)]


def test_a_page_under_a_sliding_one_draws_the_row_it_holds(fw, st):
    """The snapshot of the page under a sliding page: its own held row, nothing loaded or settled."""
    page = Page(fw, **FLAT)
    top = page.ui.window('playset_page')
    top.props['anim_hint'] = 'htranslate'
    page.view.props['_ringnav_index'] = 3
    band = page.band() if page.paint() else {}
    assert (min(band), max(band) + 1) == (144, 192)


def test_a_list_mid_layout_paints_nothing_and_ends_a_pending_centre(fw, st):
    page = Page(fw)
    page.view.int_at(O['VIEW_CONTENT_H']).value = -1  # stock's relayout under way
    st('center_timer').value = 7  # a centre press pending on this list
    st('center_top', C.c_void_p).value, st('center_surface', C.c_void_p).value = page.ui.top.addr, page.view.addr
    assert not page.paint().px and st('center_timer').value == 0 and fw.calls('timer_remove')


def buttons_page(fw, name='playlistsmore_page', **config):
    """A BUTTONS window, its own tappable buttons the rows: three tiles side by side."""
    theme(fw, **config)
    ui = Ui(fw)
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0)):
        g(fw, flag, value)
    win = ui.window(name, kind='dialog')
    tiles = [ui.add('button', win, (20 + 112 * i, 200, 110, 40), f'b{i}').clickable() for i in range(3)]
    win.props.update({'_ringnav_count': 3, '_ringnav_scope': 0, '_ringnav_index': 1})  # a dynamic page: no scope
    return ui, win, tiles


def test_a_pop_ups_button_is_framed_white_on_its_accent(fw):
    ui, win, tiles = buttons_page(fw)
    canvas = Canvas(fw)
    fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    assert canvas.frames[0][:2] == (tiles[1].box, 0xffffffff)  # the selected tile's own box
    assert canvas.restored()


# The confirm pop-up, OLED's dialog page (feedback.c).
def paint_bg(fw, w, canvas):
    return fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(w.addr, canvas.addr)


class Confirm:
    """Stock's confirminfo_dialog as dialog_confirminfo_dialog_init leaves it: full-screen and clear, its
    prompt's hscroll_label lines and the asset's two buttons."""
    def __init__(self, fw, ui, lines=('Delete this song?',), cells=('Cancel', 'OK')):
        self.fw, self.ui = fw, ui
        self.win = ui.window('confirminfo_dialog', kind='dialog')
        self.lines = []
        for text in lines:
            self.lines.append(ui.add('hscroll_label', self.win, (0, 100, 375, 30)))
            ui.set_text(self.lines[-1], text)
        self.cancel = ui.add('button', self.win, (40, 250, 140, 50), 'img_cancel').clickable()
        self.enter = ui.add('button', self.win, (195, 250, 140, 50), 'img_enter').clickable()
        ui.set_text(self.cancel, cells[0])
        ui.set_text(self.enter, cells[1])
        self.canvas = Canvas(fw)

    def paint(self):
        paint_bg(self.fw, self.win, self.canvas)
        return self.canvas


@pytest.mark.parametrize('prompt, title, verb', [('Restart?', 'Restart?', 'Restart'), ('Shut Down?', 'Shut Down?', 'Shut Down'),
                                                  ('Line Out sets max volume, continue?', 'Use Line Out?', 'Use Line Out'),
                                                  ('Delete Record?', 'Forget This Network?', 'Forget')])
def test_oleds_confirms_are_the_card_with_cancel_and_their_verb(fw, st, prompt, title, verb):
    """Stock's confirm pop-up as the confirmation card (feedback.c feedback_confirm_dress): the card at x 42, 291 wide,
    the prompt named as the design writes it, Cancel and then the verb painted on the cells, never OK; stock's own
    prompt hidden and the cells' words cleared."""
    theme(fw)
    ui = Ui(fw)
    confirm = Confirm(fw, ui, [prompt])

    def utf8(w, out, size, *_):  # the prompt's words, as feedback_confirm_dress reads them
        words = ui.text(ui.nodes[w]).encode()[:size - 1] + b'\0'
        C.memmove(out, words, len(words))
        return 0
    fw.mock('widget_get_text_utf8', utf8)
    texts = [t[0] for t in confirm.paint().texts]
    assert texts[0] == title and texts[-2:] == ['Cancel', verb]
    assert any(b[0][0] == 42 and b[0][2] == 291 for b in confirm.canvas.boxes)
    assert not confirm.lines[0].visible and confirm.ui.text(confirm.enter) == ''
    assert confirm.cancel.box[0] == 42 + 8 - THEMES[OLED]['sel_inset']  # the bar 8 px in from the card


@pytest.mark.parametrize('prompt, title, body, verb', [
    ('Reset Settings Only', 'Reset Settings?', ['Forgets Wi-Fi, Bluetooth, EQ;', 'music and playlists stay.'],
     'Reset Settings'),
    ('Reset Database', 'Reset the Database?', ['Erases playlists, Favorites;', 'then restarts and rescans.'],
     'Reset Database'),
    ('Full Factory Reset', 'Full Factory Reset?', ['Erases settings, playlists', 'and Favorites. Music stays.'],
     'Reset All')])
def test_oleds_reset_settings_confirm_says_what_it_takes(fw, st, prompt, title, body, verb):
    """30 v2: each reset's card says what stock's handler really takes (reset_page's: init_usersetting deletes the
    settings, saved Wi-Fi networks, Bluetooth pairings and EQ presets; init_database deletes database.db, with the
    playlists and My Fav, and restarts; the factory reset both), and its verb names it, red, a row under Cancel."""
    theme(fw)
    ui = Ui(fw)
    confirm = Confirm(fw, ui, [prompt])

    def utf8(w, out, size, *_):
        words = ui.text(ui.nodes[w]).encode()[:size - 1] + b'\0'
        C.memmove(out, words, len(words))
        return 0
    fw.mock('widget_get_text_utf8', utf8)
    confirm.canvas = Canvas(fw, char_w=5)  # the body fits its 251 px
    texts = confirm.paint().texts
    assert [t[0] for t in texts] == [title, *body, 'Cancel', verb]  # the body two lines as written
    assert texts[-1][2] == rgba(0xff453a)  # red
    assert confirm.enter.box[1] - confirm.cancel.box[1] == 32  # evenly spaced, as Shut Down's


# No list has dividers (user, 2026-11-15): two-line rows, a card app's page_list's and a table's, stand on space alone.
def oled(fw, st, **config):
    """OLED on its own set on a lit, usable screen."""
    theme(fw, **config)
    for flag, value in (('g_backlight_status', 1), ('g_usblink_status', 0)):
        g(fw, flag, value)
    return Ui(fw)


def detail_page(fw, st, rows=5, h=None, scroll=0, touch=0):
    """A page_list's rows, each page_detail high, a title and its detail line, tappable, in a vertical view."""
    ui = oled(fw, st)
    h = h or THEMES[OLED]['page_detail']
    win = ui.window('about_page')
    view = ui.add('scroll_view', win, (0, 26, 375, 294))
    view.byte_at(O['VIEW_VERTICAL']).value = 1
    view.int_at(O['VIEW_CONTENT_H']).value = rows * h
    view.int_at(O['SCROLL_Y']).value = scroll
    items = [ui.add('list_item', view, (0, i * h, 375, h), '_page_tall').clickable() for i in range(rows)]
    for item in items:
        ui.add('label', item, (24, 4, 300, 24))
        ui.add('hscroll_label', item, (24, 31, 300, 20))
    view.props.update({'_ringnav_count': rows, '_ringnav_scope': 1, '_ringnav_index': 1})
    st('touch_mode').value = touch
    canvas = Canvas(fw)
    paint_bg(fw, view, canvas)
    return view, items, canvas


def test_a_page_list_s_two_line_rows_have_no_divider(fw, st):
    _, _, canvas = detail_page(fw, st, rows=10, scroll=THEMES[OLED]['page_detail'] + 10, touch=1)
    assert not canvas.px  # under touch no selection either: nothing but the rows' own widgets


def faded_rows(canvas):
    """The screen rows the fades filled, each a 1 px band across the clip."""
    return sorted({y for (x, y, w, h), _ in canvas.rects if h == 1 and w == 375})


@pytest.mark.parametrize('scroll', [0, 40])
def test_a_list_past_the_list_end_fades_as_home_s_does(fw, st, scroll):
    """Every scrolling list (navigation.c list_fade) as Home (Full): its rows past the list end (y 282) peek through a
    fade from 16 px above it to y 316, the ground below; scrolled, its top 14 px fade on into its rows under the
    status row (list_reach_paint), so the box's own are near clear by then; and its
    content runs on by the box's rows past the end, so a drag stops where the wheel does."""
    rows, h = 12, THEMES[OLED]['page_detail']
    view, _, _ = detail_page(fw, st, rows=rows, scroll=scroll, touch=1)
    canvas = Canvas(fw)
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(view.addr, canvas.addr)
    end = GEOMETRY.bar + GEOMETRY.list_end
    bands = faded_rows(canvas)
    assert [y for y in bands if y >= 40] == list(range(end - 14, 320))  # the foot: from y 266, clear for its first rows
    assert [y for y in bands if y < 40] == (list(range(26, 26 + 13)) if scroll else [])  # the top, once scrolled: to clear 14 px in
    assert view.int_at(O['VIEW_CONTENT_H']).value == rows * h + (320 - end)


def test_a_list_scrolled_to_its_end_keeps_its_padding_through_stock_s_relayout(fw, st):
    """The last row selected, opened, then Return: the page's relayout puts stock's height back (the rows' foot) and
    AWTK clamps the scroll to it, which sat the last row on the screen's foot (seen on Artists). The paint pads the
    content again and puts back the scroll it last saw; a scroll of the user's own, with no relayout, stays."""
    rows, h = 12, THEMES[OLED]['page_detail']
    view, _, _ = detail_page(fw, st, rows=rows, touch=1)
    fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<i', view.block, O['SCROLL_Y'], y) or 0)
    paint = lambda: fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(view.addr, Canvas(fw).addr)
    box = 294
    paint()
    padded = view.int_at(O['VIEW_CONTENT_H']).value
    end_top = padded - box  # the wheel's end: the last row's foot on the list end
    view.int_at(O['SCROLL_Y']).value = end_top
    paint()
    view.int_at(O['VIEW_CONTENT_H']).value = rows * h  # Return: stock's relayout
    view.int_at(O['SCROLL_Y']).value = rows * h - box  # and AWTK's clamp to it
    paint()
    assert view.int_at(O['VIEW_CONTENT_H']).value == padded and view.int_at(O['SCROLL_Y']).value == end_top
    view.int_at(O['SCROLL_Y']).value = h  # a drag up, no relayout
    paint()
    assert view.int_at(O['SCROLL_Y']).value == h


@pytest.mark.parametrize('animating', [0, 1])
def test_a_relayout_puts_a_padded_list_s_scroll_back_before_any_frame_draws_it(fw, st, animating):
    """Stock's list layout of a list scrolled to its end (the Bluetooth page's rebuild on each scan result, the page
    back on top from Headset Volume) puts stock's height back and AWTK clamps the scroll to it. ipod_list_layout pads
    the content again and puts back the scroll the last paint saw, there and then: left to the paint's end, the frame
    drew the rows at the clamp, a pad lower than the scroll the paint then set, and a later repaint of part of the
    screen at that scroll left both (Settings > Bluetooth's Other Devices header twice, seen on V18Ri); a page coming
    back on top, whose slide-in paints skip the fades, flicked down then up. Each relayout, one after another."""
    rows, h, box = 12, THEMES[OLED]['page_detail'], 294
    view, _, _ = detail_page(fw, st, rows=rows, touch=1)
    fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<i', view.block, O['SCROLL_Y'], y) or 0)
    paint = lambda: fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(view.addr, Canvas(fw).addr)
    paint()
    padded = view.int_at(O['VIEW_CONTENT_H']).value
    end_top = padded - box  # the wheel's end: the last row's foot on the list end
    view.int_at(O['SCROLL_Y']).value = end_top
    paint()
    # stock's layouter: the rows' foot as the content, and AWTK's clamp of the scroll to it
    content, scroll = (C.addressof(view.int_at(O[k])) for k in ('VIEW_CONTENT_H', 'SCROLL_Y'))
    code = (b'\x48\xb8' + struct.pack('<Q', content) + b'\xc7\x00' + struct.pack('<i', rows * h) +
            b'\x48\xb8' + struct.pack('<Q', scroll) + b'\xc7\x00' + struct.pack('<i', rows * h - box) +
            b'\xb8' + struct.pack('<i', 6) + b'\xc3')  # movabs rax; mov dword [rax], imm (twice); mov eax, 6; ret
    address = O['LIST_VIEW_LAYOUT']
    claim(address)
    saved = C.string_at(address, len(code))
    C.memmove(address, code, len(code))
    try:
        fw.mock('window_manager_is_animating', lambda *_: animating)
        layout = fw.fn('ipod_list_layout', C.c_int, C.c_void_p, C.c_void_p)
        for _ in range(3):
            assert layout(None, view.addr) == 6  # stock's answer
            assert view.int_at(O['VIEW_CONTENT_H']).value == padded and view.int_at(O['SCROLL_Y']).value == end_top
            paint()
            assert view.int_at(O['SCROLL_Y']).value == end_top
    finally:
        C.memmove(address, saved, len(saved))


def test_a_list_cut_above_the_list_end_has_no_fade(fw, st):
    ui = oled(fw, st)
    view = ui.add('scroll_view', ui.window('about_page'), (0, 26, 375, 200))
    view.byte_at(O['VIEW_VERTICAL']).value = 1
    view.int_at(O['VIEW_CONTENT_H']).value = 400
    view.int_at(O['SCROLL_Y']).value = 40
    canvas = Canvas(fw)
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(view.addr, canvas.addr)
    assert not faded_rows(canvas)


class Reach:
    """A full-bleed page's list, its box from screen row top, scrolled by scroll, under the status row (bar) that
    paints its rows above that top into the row's band (row_paint.c list_reach_paint). widget_paint records the list as
    the band's paint sees it: its box, its scroll and the clip, on the screen."""
    def __init__(self, fw, st, scroll, top=58, table=False):
        self.fw = fw
        self.ui = ui = oled(fw, st)
        self.win = ui.window('settings_page')
        self.bar = ui.add('system_bar', ui.wm, (0, 0, 375, 58))
        fw.var('fw_ctx_system_bar', C.c_void_p).value = self.bar.addr
        holder = ui.add('table_view' if table else 'list_view', self.win, (0, top, 375, 320 - top))
        self.view = ui.add('table_client' if table else 'scroll_view', holder, (0, 0, 375, 320 - top))
        self.off = O['TABLE_TOP'] if table else O['SCROLL_Y']
        if table:
            self.view.int_at(O['ROW_HEIGHT']).value, self.view.int_at(O['TABLE_ROWS']).value = 32, 30
        else:
            self.view.byte_at(O['VIEW_VERTICAL']).value = 1
            self.view.int_at(O['VIEW_CONTENT_H']).value = 30 * 32
        self.view.int_at(self.off).value = scroll
        self.canvas = Canvas(fw)
        self.painted: list = []
        self.moved = [0, 0]
        fw.mock('canvas_translate', lambda c, x, y, *_: self.translate(i32(x), i32(y)))
        fw.mock('widget_paint', lambda w, c, *_: self.painted.append(
            (self.ui.nodes[w].box, self.view.int_at(self.off).value, tuple(self.canvas.clip), tuple(self.moved))) or 0)
        self.invalidated: list = []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.invalidated.append(
            (w, struct.unpack('<4i', C.string_at(r, 16)) if r else None)) or 0)

    def translate(self, x, y):
        self.moved[0] += x
        self.moved[1] += y
        return 0

    def paint(self):
        self.fw.fn('list_reach_paint', None, C.c_void_p, C.c_void_p, C.c_void_p)(self.bar.addr, self.win.addr,
                                                                               self.canvas.addr)

    def due(self):
        self.fw.fn('list_reach_due', None)()

    def band(self):
        """The fade's rows in the band: screen row -> the ground's alpha over it."""
        return {y: color >> 24 for (x, y, w, h), color in self.canvas.rects if h == 1 and y < 58}


@pytest.mark.parametrize('table', [False, True])
def test_a_scrolled_list_runs_on_under_the_status_row_to_the_screen_s_top(fw, st, table):
    """Full-bleed lists (docs/oled-design.md#list-edges): scrolled, a list's rows above its top paint into the status
    row's band, the list's own paint moved up the band's height with its scroll, clipped to the band, and fade to the
    ground at the screen's top edge, the glyphs over it legible: opaque to y 8, then less and less to the list's top.
    Its box and scroll are its own again after, so taps, the selection and its scroll range are as they were."""
    r = Reach(fw, st, scroll=100, table=table)
    before = (r.view.box, r.view.int_at(r.off).value)
    r.paint()
    assert r.painted == [((0, -58, 375, 320), 42, (0, 0, 375, 58), (0, 58))]
    assert (r.view.box, r.view.int_at(r.off).value) == before and r.moved == [0, 0]
    alpha = r.band()
    assert sorted(alpha) == list(range(58))
    assert all(alpha[y] == 255 for y in range(9)) and 0 < alpha[57] < 64
    assert all(alpha[y] >= alpha[y + 1] for y in range(57))
    assert alpha[23] > 200  # under the glyphs' middle the rows show at a fifth at most


def test_a_list_scrolled_less_than_the_band_paints_only_its_first_rows_there(fw, st):
    r = Reach(fw, st, scroll=20)
    r.paint()
    assert r.painted == [((0, -20, 375, 282), 0, (0, 38, 375, 20), (0, 58))]
    assert sorted(r.band()) == list(range(38, 58))


@pytest.mark.parametrize('scroll, top', [(0, 58), (100, 106)])
def test_a_list_at_its_top_or_low_on_the_screen_leaves_the_band_alone(fw, st, scroll, top):
    """At rest no row lies above the list; a list lower down (EQ's, under its tabs) keeps its rows in its box."""
    r = Reach(fw, st, scroll=scroll, top=top)
    r.paint()
    assert not r.painted and not r.band()


@pytest.mark.parametrize('table', [False, True])
def test_songs_scrolled_under_its_shuffle_row_paints_no_rows_above_the_row(fw, st, table):
    """Songs (the user's screenshot, 2026-11-19): its action row, Shuffle, stands between the status row and the list,
    from y 58 to 90. The band painted the list's rows above its top over the band alone, so a row showed above Shuffle
    and Shuffle floated over the scrolled list. A page with the row showing keeps the list's rows in its box."""
    r = Reach(fw, st, scroll=100, top=90, table=table)
    shuffle = r.ui.add('view', r.win, (0, 58, 375, 32), name='view_navbar_allplay')
    st('action_view', C.c_void_p).value = shuffle.addr
    r.paint()
    st('action_view', C.c_void_p).value = None  # the next test's page has none
    assert not r.painted and not r.band()


def test_the_band_repaints_when_its_list_scrolls_and_not_otherwise(fw, st):
    """The list's own invalidate keeps inside its box, so before a frame list_reach_due asks for the band once its list
    has moved since the band last showed it, and once more when the list is back at its top, its rows gone from it."""
    r = Reach(fw, st, scroll=100)
    band = (r.bar.addr, (0, 0, 375, 58))
    r.due()
    assert r.invalidated == [band]
    r.paint()
    r.due()
    assert r.invalidated == [band]  # nothing moved
    r.view.int_at(r.off).value = 132
    r.due()
    r.paint()
    r.view.int_at(r.off).value = 0
    r.due()
    assert r.invalidated == [band] * 3
    r.due()
    assert len(r.invalidated) == 3


def test_a_virtual_table_paints_only_inside_its_box(fw, st):
    """A table_client draws the row its scroll cuts at its top whole, above its box: under Songs' Shuffle row, and
    twice under the status row with the band's paint. From its background to its border its paint keeps to its box,
    and the clip it had comes back after."""
    ui = oled(fw, st)
    client = ui.add('table_client', ui.window('allmusic_page'), (0, 90, 375, 230))
    canvas = Canvas(fw, x=0, y=90)
    paint_bg(fw, client, canvas)
    assert canvas.clip == [0, 90, 375, 230]
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(client.addr, canvas.addr)
    assert canvas.clip == [0, 0, 375, 320]


def table(fw, st):
    """A virtual table of two-line rows (taller than the set's pitch): each row a tappable table_row, its leading
    image and its title."""
    ui = oled(fw, st)
    win = ui.window('allmusic_page')
    client = ui.add('table_client', win, (0, 26, 375, 294))
    row_h = GEOMETRY.pitch + 20
    client.int_at(O['ROW_HEIGHT']).value, client.int_at(O['TABLE_ROWS']).value = row_h, 4
    for i in range(4):
        row = ui.add('table_row', client, (0, i * row_h, 375, row_h)).clickable()
        row.int_at(O['ROW_INDEX']).value = i
        ui.add('image', row, (0, 0, 52, row_h))
        ui.add('hscroll_label', row, (10, 0, 300, 30))
    client.props.update({'_ringnav_count': 4, '_ringnav_scope': 1, '_ringnav_index': 0})
    st('touch_mode').value = 1
    canvas = Canvas(fw)
    strings(fw)
    paint_bg(fw, client, canvas)
    return row_h, canvas


def test_a_table_s_two_line_rows_have_no_divider(fw, st):
    _, canvas = table(fw, st)
    assert not canvas.px


# Marks on a row (ringnav_paint over an image): a checked row's mark and a playlist's quiet More.
class Mark:
    """An image in a row of a list, painted as AWTK paints a widget: its canvas at its own place."""
    def __init__(self, fw, st, image, name='', selected=True, size=(50, 48), **config):
        self.fw, self.st = fw, st
        theme(fw, **config)
        self.ui = Ui(fw)
        self.view, self.rows = library(fw, self.ui)
        row = self.rows[1] if selected else self.rows[2]
        self.w = self.ui.add('image', row, (280, 0, *size), name, image=image)
        st('sel_w', C.c_void_p).value = self.view.addr
        st('sel_accent', C.c_void_p).value = self.rows[1].addr  # the selected row, on the accent's bar
        self.canvas = Canvas(fw, x=300)

    def paint(self):
        self.st('paint_w', C.c_void_p).value = self.w.addr
        strings(self.fw)
        self.fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(self.w.addr, self.canvas.addr)
        return self.canvas


MIDDLE = (50, 48)  # Mark's image, 50 x 48: its middle, doubled


def middle(box):
    """A box's middle, doubled so it stays whole."""
    x, y, w, h = box
    return 2 * x + w, 2 * y + h


def dots_in_a_row(boxes):
    """Whether the boxes are round dots alike, side by side on one row and evenly apart."""
    (x, y, w, h), _, r = boxes[0]
    xs = [b[0][0] for b in boxes]
    return (all(b[0][1:] == (y, w, h) and b[2] == r for b in boxes) and w == h == 2 * r
            and len({b - a for a, b in zip(xs, xs[1:])}) == 1 and xs[1] - xs[0] > w)


def test_an_unchecked_row_has_nothing(fw, st):
    assert not Mark(fw, st, 'unselect').paint().px


@pytest.mark.parametrize('selected', [0, 1])
def test_a_playlists_quiet_more_is_three_grey_dots_off_the_band(fw, st, layout, selected):
    mark = Mark(fw, st, 'unselect', 'img_more3', selected=selected)
    S = Scheme(fw, layout)
    boxes = mark.paint().boxes
    if selected:  # on the band its chevron takes their place
        assert boxes == []
        return
    assert len(boxes) == 3 and dots_in_a_row(boxes) and {b[1] for b in boxes} == {rgba(S.muted)}
    assert middle(boxes[1][0]) == MIDDLE  # the middle one on the image's middle


# ringnav_paint's own work around the painters.
def test_ringnav_paint_returns_stocks_and_hands_the_paint_to_the_parent(fw, st):
    """Stock paints a widget's border after its children: once the selected row's title is done, the
    row is painting again and takes the selection's ink; once the row is done, its item, off the bar."""
    page = Page(fw)
    page.paint()
    row = page.rows[1]
    grey = 0xff808080  # stock's colour for a text: the selection's text colour stands out from it
    lit = text_ink(fw, st, row.children[0], grey)
    fw.mock('stock_paint_trampoline', lambda *_: 5)
    paint = fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)
    assert paint(row.children[0].addr, Canvas(fw).addr) == 5  # stock's answer
    assert style_ink(fw, grey) == lit != grey
    paint(row.addr, Canvas(fw).addr)
    assert style_ink(fw, grey) != lit


@pytest.mark.parametrize('same, kept', [(1, 1), (0, 0)])
def test_a_paint_ends_a_pending_centre_once_the_top_window_changed(fw, st, same, kept):
    ui = Ui(fw)
    g(fw, 'g_backlight_status', 1)
    first = ui.window('playset_page')
    if not same:
        ui.window('about_page')
    st('center_timer').value, st('center_top', C.c_void_p).value = 7, first.addr
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(first.addr, Canvas(fw).addr)
    assert st('center_timer').value == 7 * kept and len(fw.calls('timer_remove')) == 1 - kept


def test_a_paint_off_a_carousel_ends_homes_slide(fw, st):
    ui = Ui(fw)
    g(fw, 'g_backlight_status', 1)
    home = ui.window('home_page')
    st('home_surface', C.c_void_p).value = home.addr
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(home.addr, Canvas(fw).addr)
    assert st('home_surface', C.c_void_p).value == home.addr
    ui.window('about_page')
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(home.addr, Canvas(fw).addr)
    assert st('home_surface', C.c_void_p).value is None


# The settings pages' rows: stock's builders, then ours in the same widgets and styles.
def settings_page(fw, win_name, view_name, stock_rows=0, row_h=70, **config):
    """A settings window and its list, stock's own rows (each list_item named s<id>, row_h high, its
    button a settings row's) built by stock's init, as its trampoline does."""
    settings = theme(fw, **config)
    ui = Ui(fw)
    win = ui.window(win_name)
    view = ui.add('scroll_view', win, (0, 26, 375, 294), view_name)

    def build(*_):
        for i in range(stock_rows):
            item = ui.add('list_item', view, (0, 0, 375, row_h), f's{i}')
            ui.add('button', item, (20, 0, 335, row_h), str(i)).clickable()
        return 3
    fw.mock('fw___xstat', lambda *_: -1)  # no Rockbox on the card
    return ui, win, view, settings, build


def label_of(item):
    """A row's title: its first hscroll_label."""
    return next(c for c in item.walk() if c.kind == 'hscroll_label')


def kv(ui, item):
    """A row's "Title: value" as it reads: its title, and its value on the set's right column (row_value_text)."""
    value = next((c for c in item.walk() if c.name == '_setting_value'), None)
    title = ui.text(label_of(item))
    return f'{title}: {ui.text(value)}' if value is not None and ui.text(value) else title


def row(ui, view, text):
    return next(item for item in view.children if any(ui.text(c) == text for c in item.walk()))


def switch(item):
    s = next((c for c in item.walk() if c.name == '_setting_switch'), None)
    return s and s.props['image']


def click(ui, item):
    button = item.children[0]
    return ui.fire(button, EVT_CLICK)


def test_power_adds_standby_charge_and_in_vehicle_rows_after_stocks(fw):
    ui, win, view, settings, build = settings_page(fw, 'powermanager_page', 'scroll_view_powermanager', 2)
    fw.mock('stock_power_trampoline', build)
    assert fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None) == 3
    stock, ours = view.children[:2], view.children[2:]
    assert [s.name for s in stock] == ['s0', 's1'] and len(ours) == 6  # Restart and Shut Down are General's
    icons = [next(c.props['image'] for c in r.walk() if c.kind == 'image') for r in ours]
    # Standby, Charge, USB DAC Charging, Smart Wi-Fi, In-Vehicle, Battery History
    assert icons == ['system_powermanager', 'usb_chargeswitch', 'usb_chargeswitch', 'wifiset_wifi', 'system_carmode',
                     'system_powermanager']
    standby, charge, *_, history = (kv(ui, r) for r in ours)
    # Standby off; Charge Limit a key/value row on a set_switch theme, Off or where it stops (here off)
    assert standby.endswith('Off') and charge == 'Charge Limit: Off'

    assert history == 'Battery History' and 'list_into' in [c.props.get('image') for c in ours[-1].walk()]  # opens a page
    assert [switch(r) for r in ours] == [None, None, 'switch_off', 'switch_off', 'switch_off', None]
    for r in ours:  # stock's settings row: its button, the title clear of the icon, both the row's height
        button, label = r.children[0], label_of(r)
        icon = next(c for c in r.walk() if c.kind == 'image')
        assert button.box == stock[0].children[0].box
        assert icon.box[0] + icon.box[2] <= label.box[0] and label.box[0] + label.box[2] <= button.box[2]
        assert icon.box[3] == label.box[3] == button.box[3]
    tr = [C.string_at(c[1][1]) for c in fw.calls('widget_set_tr_text')]
    assert tr == [b'system_carmode']  # stock's row, stock's translation


def test_standby_limit_cycles_its_four_choices_and_saves_each(fw):
    ui, win, view, settings, build = settings_page(fw, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    standby = view.children[0]
    texts = []
    for _ in range(4):
        click(ui, standby)
        texts.append(kv(ui, standby))
    hours = [O[f'STANDBY_LIMIT_{k}'] for k in (1, 2, 3)]
    assert [re.findall(r'\d+', t) for t in texts] == [[str(h)] for h in hours] + [[]]  # each limit's hours, then none
    assert texts[-1].endswith('Off')
    assert settings.written == [('STANDBYLIMIT', v) for v in (1, 2, 3, 0)]


def test_charge_limit_switches_and_takes_effect_on_the_next_pass(fw, st):
    ui, win, view, settings, build = settings_page(fw, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    charge = view.children[1]
    st('charge_at', C.c_uint).value = 12345
    click(ui, charge)
    on = f'Charge Limit: {O["CHARGE_STOP"]}%'  # where it stops: a set_switch theme's key/value row
    assert kv(ui, charge) == on and settings.written == [('CHARGELIMIT', 1)] and st('charge_at', C.c_uint).value == 0
    click(ui, charge)
    assert kv(ui, charge) == 'Charge Limit: Off' and settings.written[-1] == ('CHARGELIMIT', 0)
    assert switch(charge) is None


def dac_power(fw, **charging):
    """The Power page over usb_charge.c's guard, its settings loaded (test_usb_charge.Charging); its USB DAC
    Charging row."""
    from test_usb_charge import Charging
    ui, win, view, settings, build = settings_page(fw, 'powermanager_page', 'scroll_view_powermanager')
    q = Charging(fw, **charging)
    q.config()
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    dac = view.children[2]
    assert ui.text(label_of(dac)) == 'USB DAC Charging'
    return ui, win, dac, q


def test_usb_dac_charging_is_the_dac_screens_choice_and_drives_the_pin_at_once(fw):
    ui, win, dac, q = dac_power(fw, pref=1, cable=1)  # Audio and DAC, a cable in
    assert switch(dac) == 'switch_on' and q.gpio == 0
    click(ui, dac)
    assert q.flag.value == 0 and q.saved == [('PLAYSET', 'USBDAC_CHARGE', 0)] and switch(dac) == 'switch_off'
    assert q.gpio == 1  # inhibited now, not at the next poll
    click(ui, dac)
    assert q.flag.value == 1 and q.saved[-1] == ('PLAYSET', 'USBDAC_CHARGE', 1) and switch(dac) == 'switch_on'
    assert q.gpio == 0


def test_usb_dac_charging_on_charges_after_the_dac_screens_off_and_off_leaves_usb_storage_charging(fw):
    ui, win, dac, q = dac_power(fw, pref=0, cable=1)
    q.charge(0)  # the DAC screen's switch turned Off: stock's switch_charge_enable(0)
    click(ui, dac)
    assert q.gpio == 0
    q.usbmode.value = 0  # USB Storage: Off is for Audio and DAC only
    click(ui, dac)
    assert q.flag.value == 0 and q.gpio == 0


def test_the_dac_screens_switch_shows_in_the_row_when_power_is_back(fw):
    ui, win, dac, q = dac_power(fw, pref=1)
    q.flag.value = 0  # the DAC screen's switch, over this page
    ui.fire(win, O['EVT_WINDOW_FOREGROUND'])
    assert switch(dac) == 'switch_off'


def test_charge_limits_hold_wins_over_usb_dac_charging_on(fw, st):
    ui, win, dac, q = dac_power(fw, pref=0, cable=1)
    st('charge_held').value = 1
    q.charge(0)  # charge_poll's hold
    click(ui, dac)
    assert q.flag.value == 1 and q.gpio == 1
    st('charge_held').value = 0
    q.charge(1)  # its hand-back at CHARGE_RESUME
    assert q.gpio == 0


def test_in_vehicle_mode_is_stocks_byte_and_key(fw):
    ui, win, view, settings, build = settings_page(fw, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    g(fw, 'g_carmode_state', 4)
    standby, charge, dac, smart, car, history = view.children[:6]  # then the gap, Restart and Shut Down
    click(ui, car)
    assert (g(fw, 'g_carmode'), g(fw, 'g_carmode_state')) == (1, 0) and settings.written == [('CARMODE', 1)]
    assert switch(car) == 'switch_on'


def test_smart_wifi_switches_and_saves_its_own_key(fw, st):
    ui, win, view, settings, build = settings_page(fw, 'powermanager_page', 'scroll_view_powermanager')
    fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    car_mode = g(fw, 'g_carmode')
    standby, charge, dac, smart, car, history = view.children[:6]  # then the gap, Restart and Shut Down
    click(ui, smart)
    assert switch(smart) == 'switch_on' and settings.written == [('SMARTWIFI', 1)] and st('smart_wifi').value == 1
    assert kv(ui, charge) == 'Charge Limit: Off' and switch(car) == 'switch_off'  # the other rows as they were
    assert g(fw, 'g_carmode') == car_mode
    click(ui, smart)
    assert switch(smart) == 'switch_off' and settings.written[-1] == ('SMARTWIFI', 0) and st('smart_wifi').value == 0


@pytest.mark.parametrize('bt', [1, 0])
def test_bluetooth_ends_with_resume_and_link_volume(fw, bt):
    """Iconless and as high as stock's rows (78 px on this page), each saving its own key, last in the Audio group
    (bt_ui.c orders the page; test_bt_ui.py)."""
    ui, win, view, settings, build = settings_page(fw, 'bluetooth_page', 'scroll_view_bluetooth', 7, row_h=78)
    fw.mock('stock_btrows_trampoline', build)
    g(fw, 'g_bluetoothflag', bt)
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    rows = view.children
    if not bt:
        assert 'Link Volume' not in [ui.text(c) for r in rows for c in r.walk()]
        return
    ours = rows[-2:]
    for r in ours:
        click(ui, r)
    assert [key for key, _ in settings.written] == ['RESUME', 'BTVOLLINK']  # Resume on Connect, Link Volume
    assert [r.box[3] for r in ours] == [78] * 2
    beside = ui.add('scroll_view', win, (0, 0, 375, 100), 'beside')  # a settings row with an icon
    with_icon = ui.nodes[fw.fn('list_row', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)(beside.addr, b'icon', None, None)]
    assert [c.name for r in ours for c in r.walk() if c.kind == 'image'] == ['_setting_switch'] * 2  # no icon
    assert [label_of(r).box[0] for r in ours] == [with_icon.parent.children[0].box[0]] * 2  # in the icon's place


@pytest.mark.parametrize('way, sent', [(1, 1), (0, 0)])
def test_device_volume_off_gives_stock_its_gain_back_at_once(fw, way, sent):
    ui, win, view, settings, build = settings_page(fw, 'bluetooth_page', 'scroll_view_bluetooth', 5, row_h=78, BTVOLLINK=b'1')
    fw.mock('stock_btrows_trampoline', build)  # stock's five head rows
    g(fw, 'g_bluetoothflag', 1)
    g(fw, 'g_volume', 37)
    fw.mock('mclGetOutputWay', lambda *_: way)
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    link = row(ui, view, 'Link Volume')
    bt_link = fw.fn('navigation__bt_link', C.c_int)
    assert switch(link) == 'switch_on' and bt_link() == 1
    click(ui, link)
    assert switch(link) == 'switch_off' and settings.written == [('BTVOLLINK', 0)] and bt_link() == 0
    assert [c[1][:2] for c in fw.calls('device_set_volume')] == [(37, 0)] * sent


def test_link_volume_unset_shows_and_turns_off_from_what_applies(fw):
    """Never chosen, the switch shows what applies to the device connected now: on for headphones; a click saves the
    other way from there (off), and that choice holds."""
    ui, win, view, settings, build = settings_page(fw, 'bluetooth_page', 'scroll_view_bluetooth', 5, row_h=78)
    fw.mock('stock_btrows_trampoline', build)
    g(fw, 'g_bluetoothflag', 1)
    mac = 'AA:BB:CC:DD:EE:07'
    fw.mock('getBtLinkSize', lambda *_: 1)
    def item(i, status, out, *_):
        C.c_int.from_address(status).value = 3
        C.memmove(out, mac.encode() + b'\0', len(mac) + 1)
        return 0
    fw.mock('getBtLinkItem', item)
    fw.mock('fw_system', lambda *_: 0)
    threads_now(fw)  # the battery run (bt_battery.c) ends at once; its answer is the file
    Files(fw, {'/tmp/q2-btbatt': f'{mac} 80 48000 {0x240418} Px7\n'.encode()})  # headphones
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    link = row(ui, view, 'Link Volume')
    bt_link = fw.fn('navigation__bt_link', C.c_int)
    assert switch(link) == 'switch_on' and bt_link() == 1
    click(ui, link)
    assert switch(link) == 'switch_off' and settings.written == [('BTVOLLINK', 0)] and bt_link() == 0


def playset(fw, ui, view, stock_rows=13):
    """Stock's Playback rows, s0-s12: s8 Headphones required, s11 Volume and Album Display, s12 Folder skip."""
    def build(*_):
        for i in range(stock_rows):
            item = ui.add('list_item', view, (0, 0, 375, 70), f's{i}')
            ui.add('button', item, (20, 0, 335, 70)).clickable()
        return 0
    fw.mock('stock_audioset_trampoline', build)


def icon(item):
    return next((c.props.get('image') for c in item.walk() if c.kind == 'image' and c.box[0] < 72), None)


def test_playback_orders_its_rows_eq_and_the_library_first(fw):
    ui, win, view, settings, _ = settings_page(fw, 'playset_page', 'scroll_view_playset')
    playset(fw, ui, view)
    fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    rows = view.children
    # stock's Volume and Album Display is not offered; Folder skip is Library > Folders'; stock's Balance (s10) stays,
    # hidden, after the inline Balance (inline_slider.c); Smart Crossfade's one switch; Default Volume's Level under it
    seq = [r.name or ui.text(label_of(r)).split(':')[0] for r in rows]
    assert seq == ['s2', 'Library', 's3', 's7', 'Smart Crossfade', 's6', 'Resume Songs', 's8', 'Sleep Timer',
                   'Volume', 'Max Volume', 'Default Volume', 'Level', 's0', 's9', 'Balance', 's10', 'Mono Audio',
                   'Swap L/R', 's1', 's4', 's5']
    assert 'list_into' in [c.props.get('image') for c in rows[1].walk()] and not rows[seq.index('s10')].visible
    assert 'playset_covermode' not in [icon(r) for r in rows]
    assert icon(rows[seq.index('Volume')]) == 'playset_maxvol'  # crossfade.c volume_row, just before Max Volume


def test_playback_s_volume_rows_change_in_place(fw):
    """Max Volume and Default Volume open no page: an inline slider and a switch (inline_slider.c)."""
    ui, win, view, settings, _ = settings_page(fw, 'playset_page', 'scroll_view_playset')
    playset(fw, ui, view)
    fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    opened = []
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name).decode()))
    for title in ('Max Volume', 'Default Volume'):
        item = row(ui, view, title)
        assert 'list_into' not in [c.props.get('image') for c in item.walk()]
        click(ui, item)
    assert opened == []


def test_enlarged_album_art_is_display_s_last_row_and_stock_s_switch(fw):
    ui, win, view, settings, _ = settings_page(fw, 'display_page', 'scroll_view_display')
    g(fw, 'g_bigcovermode', 0)
    pod_rows(fw, win, 'scroll_view_display', POD['POD_COVERMODE'], 1)
    art = view.children[0]
    assert switch(art) == 'switch_off' and ui.text(label_of(art)) == 'Enlarged Album Art'
    assert [C.string_at(c[1][1]) for c in fw.calls('widget_set_tr_text')] == [b'playset_covermode']
    click(ui, art)
    assert switch(art) == 'switch_on' and g(fw, 'g_bigcovermode') == 1 and settings.written == [('COVERMODE', 1)]
    click(ui, art)
    assert switch(art) == 'switch_off' and g(fw, 'g_bigcovermode') == 0 and settings.written[-1] == ('COVERMODE', 0)


def test_the_library_is_stocks_page_again_with_its_pages(fw, st):
    ui, win, view, settings, _ = settings_page(fw, 'playset_page', 'scroll_view_playset')
    fw.var('fw_ctx_artist_type', C.c_int).value = 1
    playset(fw, ui, view)
    audioset = fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)
    audioset(win.addr, None)
    pages = []

    def open_page(name, *_):
        page = ui.window('playset_page')
        nav = ui.add('view', page, (0, 0, 375, 26), 'view_navbar')
        ui.add('label', nav, (0, 0, 375, 26), 'title')
        sub_view = ui.add('scroll_view', page, (0, 26, 375, 294), 'scroll_view_playset')
        playset(fw, ui, sub_view)
        audioset(page.addr, None)
        pages.append((C.string_at(name), ui.text(nav.children[0]), sub_view))
        return 0

    fw.mock('navigator_to', open_page)
    click(ui, row(ui, view, 'Library'))
    name, title, library = pages[-1]
    assert name == b'playset/playset_page' and title == 'Library'
    assert [kv(ui, r) for r in library.children] == [
        'On-The-Go', 'Sorting', 'Artists: Album Artist', 'Artist View: Cover Flow', 'Playlists',
        'Folders', 'Scan Song Durations']
    click(ui, row(ui, library, 'Folders'))
    _, title, folders = pages[-1]
    root, skip, art = folders.children
    # Folder Root, stock's Folder skip, then Folder Album Cover (a row of stock's Volume and Album Display)
    assert title == 'Folders' and skip.name == 's12' and icon(art) == 'playset_folderjump'
    assert ui.text(label_of(art)) == 'Folder Album Cover' and ui.text(label_of(root)).startswith('Folder Root')
    assert C.string_at(fw.calls('widget_set_tr_text')[-1][1][1]) == b'playset_foldercover'  # stock's row, stock's translation
    was = g(fw, 'g_foldercover_flag')
    click(ui, root)
    click(ui, art)
    assert [key for key, _ in settings.written] == ['FOLDERROOT', 'FOLDERCOVER']
    assert g(fw, 'g_foldercover_flag') == (not was) and switch(art) == ('switch_off' if was else 'switch_on')
    click(ui, row(ui, library, 'Playlists'))
    _, title, playlists = pages[-1]
    assert title == 'Playlists' and [kv(ui, r) for r in playlists.children if r.kind == 'list_item'] == [
        'Playlist View: List', 'Import Playlists', 'Export Playlists']  # the Playlists page's own rows (playlists.c)
    again = ui.add('scroll_view', ui.window('playset_page'), (0, 26, 375, 294), 'scroll_view_playset')
    playset(fw, ui, again)
    audioset(ui.top.addr, None)
    assert len(again.children) == 22  # the next open is the main page again


def caption_texts(fw, view):
    """view's captioned rows (settings_pages.c's table) and their captions."""
    table = fw.var('settings_pages__cap', C.c_void_p * 17)  # view, item[CAP_MAX], text[CAP_MAX]
    text = {table[1 + k]: C.wstring_at(table[9 + k]) for k in range(8) if table[1 + k]} if table[0] == view.addr else {}
    return {item: text[item.addr] for item in view.children if item.addr in text}


def test_reset_settings_rows_lose_their_chevrons_and_say_what_they_take(fw):
    """30 v2 (settings_pages.c reset_rows): stock's three Reset rows open the confirmation card, so their list_into
    chevrons go (insensitive too: stock's hit test skips only the insensitive), and each gets its caption once, however
    often the list is laid out."""
    ui = Ui(fw)
    theme(fw)
    win = ui.window('reset_page')
    view = ui.add('scroll_view', win, (0, 58, 375, 262), 'scroll_view_reset')
    chevrons = []
    for i, title in enumerate(('Reset Settings Only', 'Reset Database', 'Full Factory Reset')):
        button = ui.add('button', ui.add('list_item', view, (0, 32 * i, 375, 32)), (0, 0, 375, 32), str(i))
        ui.add('hscroll_label', button, (10, 0, 280, 32), text=title)
        chevrons.append(ui.add('image', button, (330, 0, 24, 32), image='list_into'))
    rows = fw.fn('reset_rows', None, C.c_void_p)
    rows(view.addr)
    rows(view.addr)  # a second layout
    assert not any(c.visible for c in chevrons)
    assert {a[0] for _, a in fw.calls('widget_set_sensitive') if not C.c_int(a[1]).value} >= {c.addr for c in chevrons}
    assert list(caption_texts(fw, view).values()) == ['Keeps your music, playlists and ratings',
                                                      'Erases playlists and Favorites, then rescans',
                                                      'Erases all settings and playlists; music stays']


def test_playback_s_and_library_s_rows_carry_their_captions(fw):
    """Playback's own rows carry one, Smart Crossfade's (crossfade.c xfade_text); Playback > Library's switches do
    (library_settings.c), and Library > Folders' stock Folder skip has its own (ringnav_audioset)."""
    ui, win, view, settings, _ = settings_page(fw, 'playset_page', 'scroll_view_playset')
    playset(fw, ui, view)
    audioset = fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)
    audioset(win.addr, None)
    assert {ui.text(label_of(r)): t for r, t in caption_texts(fw, view).items()} == {
        'Smart Crossfade': 'Overlaps songs from different albums, timed to how each ends and begins'}

    def sub_page(n):  # Library's page n (LIB_*): stock's page again, built afresh
        page = ui.window('playset_page')
        ui.add('view', page, (0, 0, 375, 26), 'view_navbar')
        sub = ui.add('scroll_view', page, (0, 26, 375, 294), 'scroll_view_playset')

        def build(*_):  # stock's rows, each button holding its icon and label as stock's do
            for i in range(13):
                button = ui.add('button', ui.add('list_item', sub, (0, 0, 375, 70), f's{i}'), (20, 0, 335, 70)).clickable()
                ui.add('image', button, (10, 9, 52, 52))
            return 0
        fw.mock('stock_audioset_trampoline', build)
        fw.mock('navigator_to', lambda *_: audioset(page.addr, None))
        fw.fn('library_open', C.c_int, C.c_void_p, C.c_void_p)(n, None)
        return sub
    library = sub_page(1)
    assert {ui.text(label_of(r)): t for r, t in caption_texts(fw, library).items()} == {
        'On-The-Go': 'Shows On-The-Go in Playlists and hold menus',
        'Scan Song Durations': 'Measures lengths after each update'}
    folders = sub_page(4)
    assert [(r.name, t) for r, t in caption_texts(fw, folders).items()] == [
        ('s12', 'Plays on into the next folder when one ends')]


def test_artists_toggles_stocks_artist_type(fw):
    ui, win, view, settings, _ = settings_page(fw, 'playset_page', 'scroll_view_playset')
    fw.var('fw_ctx_artist_type', C.c_int).value = 1
    fw.fn('pod_rows', None, C.c_void_p, C.c_char_p, C.c_int, C.c_int, C.c_void_p)(win.addr, b'scroll_view_playset', 4, 1, (C.c_char_p * 1)(b'x'))
    artists = view.children[0]
    assert kv(ui, artists) == 'Artists: Album Artist'
    click(ui, artists)
    assert fw.var('fw_ctx_artist_type', C.c_int).value == 0 and kv(ui, artists) == 'Artists: Artist'
    assert settings.written == [('ARTISTTYPE', 0)]


def pod_rows(fw, win, view_name, first, n):
    fw.fn('pod_rows', None, C.c_void_p, C.c_char_p, C.c_int, C.c_int, C.c_void_p)(
        win.addr, view_name.encode(), first, n, (C.c_char_p * n)(*[b'icon'] * n))


def test_cover_flow_sort_opens_its_picker_and_refresh_says_when(fw):
    ui, win, view, settings, _ = settings_page(fw, 'display_page', 'scroll_view_display')
    Files(fw)
    toasts, opened = [], []
    fw.mock('navigator_to_with_context', lambda name, info, *_: toasts.append(C.string_at(info + 8)) and 0)
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name)))
    assert POD['POD_CF_REFRESH'] == POD['POD_CF_SORT'] + 1  # one call builds both
    pod_rows(fw, win, 'scroll_view_display', POD['POD_CF_SORT'], 2)
    sort, refresh = view.children
    first, action = kv(ui, sort), kv(ui, refresh)
    assert first == 'Sort: Album' and 'list_into' in [c.props.get('image') for c in sort.walk()]
    click(ui, sort)
    assert kv(ui, sort) == first and opened == [b'systemset/keylock_page'] and not toasts  # its picker
    click(ui, refresh)
    assert kv(ui, refresh) == action and fw.calls('image_manager_unload_all')
    assert len(toasts) == 1 and toasts[0]  # nothing in view changes: a toast says when


def test_scan_song_durations_turned_off_cancels_a_pass(fw):
    from test_durations import Ds
    ui, win, view, settings, _ = settings_page(fw, 'display_page', 'scroll_view_display', SCANDURATIONS=b'1')
    pod_rows(fw, win, 'scroll_view_display', POD['POD_SCAN_DURATIONS'], 1)
    state = fw.var('durations__ds', Ds)
    state.due = 1  # an update's end, its paths not read yet
    click(ui, view.children[0])
    assert settings.written == [('SCANDURATIONS', 0)] and state.cancel and not state.due


# The Clicker, Key Tone's row (patch/clicker.c): Speaker, Headphones, Both, Off and round; Off is Key Tone off
# (stock's byte and SYSSET KEYTONE), the others IPOD CLICKER (0 Both, 1 Speaker, 2 Headphones).
@pytest.mark.parametrize('tone, clicker, now, then, written', [
    (1, b'1', 'Speaker', 'Headphones', [('CLICKER', 2)]), (1, b'2', 'Headphones', 'Both', [('CLICKER', 0)]),
    (1, b'0', 'Both', 'Off', [('KEYTONE', 0)]), (0, b'0', 'Off', 'Speaker', [('KEYTONE', 1), ('CLICKER', 1)]),
    (0, b'1', 'Off', 'Speaker', [('KEYTONE', 1)]), (1, None, 'Both', 'Off', [('KEYTONE', 0)]),
])
def test_the_clicker_row_cycles_and_saves(fw, tone, clicker, now, then, written):
    ui, win, view, settings, _ = settings_page(fw, 'controls_page', 'scroll_view_controls', **({'CLICKER': clicker} if clicker else {}))
    g(fw, 'g_keytone_flag', tone)
    pod_rows(fw, win, 'scroll_view_controls', POD['POD_KEYTONE'], 1)
    row = view.children[0]
    assert kv(ui, row) == f'Clicker: {now}'
    click(ui, row)
    assert kv(ui, row) == f'Clicker: {then}' and g(fw, 'g_keytone_flag') == (then != 'Off')
    assert settings.written == written


def split_row(ui, button, label, value, text):
    """A key/value row (docs/oled-design.md#list-row): the label has no value in it, the value widget is
    right-aligned on the right column and clear of the label, with no chevron."""
    v = next(c for c in button.walk() if c.name == '_setting_value')
    assert ui.text(label) == text and ': ' not in ui.text(label) and ui.text(v)
    (lx, _, lw, _), (vx, _, vw, _) = label.box, v.box
    assert vx + vw == 374 - GEOMETRY.right_x - button.box[0] and lx + lw < vx  # KV_GAP itself is kv_fit's (unmeasured here)
    assert 'list_into' not in [c.props.get('image') for c in button.walk()]
    return v


def test_the_clicker_row_is_a_key_value_row_with_the_value_on_the_right(fw):
    ui, win, view, settings, _ = settings_page(fw, 'controls_page', 'scroll_view_controls', CLICKER=b'0')
    g(fw, 'g_keytone_flag', 1)
    pod_rows(fw, win, 'scroll_view_controls', POD['POD_KEYTONE'], 1)
    row = view.children[0]
    v = split_row(ui, row.children[0], label_of(row), None, 'Clicker')
    assert ui.text(v) == 'Both'
    click(ui, row)  # Center still cycles it
    assert (ui.text(label_of(row)), ui.text(v)) == ('Clicker', 'Off')


@pytest.mark.parametrize('first, view_name', [(POD['POD_CHARGE'], 'scroll_view_powermanager'),
                                             (POD['POD_WAKE'], 'scroll_view_display'),
                                             (POD['POD_HOLD_HOME'], 'scroll_view_display'),
                                             (POD['POD_KEYTONE'], 'scroll_view_display')])
def test_no_pod_row_carries_its_value_in_the_label(fw, first, view_name):
    ui, win, view, settings, _ = settings_page(fw, 'display_page', view_name)
    pod_rows(fw, win, view_name, first, 1)
    for row in view.children:
        assert ': ' not in ui.text(label_of(row))


@pytest.mark.parametrize('on', [1, 0])
def test_eq_per_output_turned_off_forgets_the_outputs_eq(fw, on):
    config = Settings(fw, EQOUTPUT=str(on).encode())
    assert fw.fn('ringnav_eq_output', C.c_int)() == on
    fw.fn('ringnav_eq_output_toggle', None)()
    assert fw.fn('ringnav_eq_output', C.c_int)() == (not on) and config.written == [('EQOUTPUT', int(not on))]
    assert bool(fw.calls('fw_unlink')) == bool(on)  # off: what plays stays, the owner's file goes


# A set_text theme's settings rows: the title left, the value right-aligned.
def set_text_row(fw, text):
    """navigation.c's row_label on a settings row: the row's button, title and value."""
    theme(fw)
    ui = Ui(fw)
    button = ui.add('button', ui.add('list_item', ui.window('display_page')), (20, 0, 335, 70))
    label = ui.add('hscroll_label', button, (72, 0, 260, 70))
    fw.fn('navigation__row_label', None, C.c_void_p, C.c_char_p)(label.addr, text.encode())
    return ui, button, label, button.child('_setting_value')


@pytest.mark.parametrize('text, title, val', [('Light Mode: Auto', 'Light Mode', 'Auto'), ('Blue', 'Accent', 'Blue'),
                                              ('Wheel: 50%: x', 'Wheel', '50%: x')])
def test_a_set_text_rows_title_and_value(fw, text, title, val):
    ui, button, label, v = set_text_row(fw, text)
    assert (ui.text(label), ui.text(v)) == (title, val)
    (lx, ly, lw, lh), (vx, vy, vw, vh), width = label.box, v.box, button.box[2]
    assert (lx, ly, lh) == (72, 0, 70) and (vy, vh) == (0, 70)  # the title where it was, both the row's height
    # the value ending on the set's right column (hscroll_label keeps 2 px), clear of the title
    assert lx + lw < vx and width // 2 < vx and vx + vw == 374 - GEOMETRY.right_x - button.box[0]
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_only_focus')] == [(v.addr, 1)]  # scrolls only focused
    assert [c[1][:2] for c in fw.calls('hscroll_label_set_ellipses')] == [(v.addr, 1)]


def test_a_long_title_is_cut_and_keeps_its_value(fw):
    ui, button, label, v = set_text_row(fw, 'x' * 50 + ': y')
    title = ui.text(label)
    assert title and 'x' * 50 != title == 'x' * len(title) and ui.text(v) == 'y'


def test_the_librarys_rows_take_its_constructors_live_words(fw):
    """Under the Library's own list the row's body and icon x are the ipod.json words the build baked."""
    body, icon_x = O['LIBRARY_ROW_BODY'], O['LIBRARY_ROW_ICON_X']
    saved = C.c_ushort.from_address(body).value, C.c_ushort.from_address(icon_x).value
    try:
        C.c_ushort.from_address(body).value, C.c_ushort.from_address(icon_x).value = 52, 14
        ui, win, view, settings, _ = settings_page(fw, 'localmusic_page', 'scroll_view_localmusic')
        list_row = fw.fn('list_row', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)
        label = ui.nodes[list_row(view.addr, b'music_artist', None, None)]
        other = ui.nodes[list_row(ui.add('scroll_view', win, (0, 26, 375, 294), 'scroll_view_playset').addr, b'icon', None, None)]
        button, icon = label.parent, label.parent.children[0]
        assert button.box[:3] == other.parent.box[:3] and button.box[3] == 52  # a settings row, the body's height
        assert icon.box[0] == 14 != other.parent.children[0].box[0] and icon.props['image'] == 'music_artist'
        assert icon.box[3] == label.box[3] == 52 and icon.box[0] + icon.box[2] <= label.box[0]
        assert not button.handlers  # no click: a row that only shows
    finally:
        C.c_ushort.from_address(body).value, C.c_ushort.from_address(icon_x).value = saved


# Buttons lock and Quick Settings.
def test_buttons_lock_lists_off_and_on_and_a_pick_moves_the_check(fw):
    ui, win, view, settings, _ = settings_page(fw, 'keylock_page', 'scroll_view_keylock')
    old = ui.add('list_item', view)
    assert fw.fn('ringnav_keylock_rows', C.c_int, C.c_void_p)(win.addr) == 0
    names = [ui.text(label_of(r)) for r in view.children]
    assert old not in view.children and names == ['Off', 'On']
    checks = lambda: [r.children[0].children[0].props['image'] for r in view.children]
    assert checks() == ['select', 'unselect']
    click(ui, view.children[1])
    assert checks() == ['unselect', 'select']
    assert (g(fw, 'g_keylock_flag'), g(fw, 'g_keylock_mode')) == (1, 2)  # stock's: 2 the buttons
    assert settings.written == [('LOCKMODE', 2), ('LOCKFLAG', 1)]


class Quick:
    """Quick Settings (statusbar_dialog): its icons and labels by stock's names, opened through its init."""
    ICONS = ['img_wifiswitch', 'img_btswitch', 'img_lock', 'img_gain', 'img_usbmode', 'img_po', 'img_playset', 'img_sysset']

    def __init__(self, fw, st):
        self.fw, self.st = fw, st
        theme(fw)
        self.ui = Ui(fw)
        g(fw, 'g_backlight_status', 1)
        self.win = self.ui.window('statusbar_dialog', kind='dialog')
        self.menu = self.ui.add('view', self.win, (0, 40, 375, 200), 'view_menu')
        self.menu_was = self.menu.box
        self.icons = {n: self.ui.add('image', self.menu, (10 * i, 10, 50, 50), n) for i, n in enumerate(self.ICONS)}
        self.ui.add('label', self.menu, (0, 0, 80, 20), 'label_lock')
        for n in ('image0', 'image1'):
            self.ui.add('image', self.win, (0, 0, 10, 10), n)
        self.slider = self.ui.add('slider', self.ui.add('view', self.win, (0, 250, 375, 60), 'view_backlight'), (20, 10, 335, 30),
                                  'slider_backlight')
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, i32(ms))) or len(self.timers))
        fw.mock('pointer_event_init', lambda out, kind, *_: C.memmove(out, struct.pack('<i', i32(kind)), 4) or out)
        self.dispatched: list = []
        fw.mock('widget_dispatch_event_to_target_recursive',
                lambda w, e, *_: self.dispatched.append((w, C.c_int.from_address(e).value)) and 0)
        self.opened = fw.fn('ringnav_quick_settings', C.c_int, C.c_void_p, C.c_void_p)(self.win.addr, None)

    def fn(self, name):
        return C.cast(getattr(self.fw.lib, ui_name(self.fw.lib, name)), C.c_void_p).value

    def press(self, name, x=30, y=40):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<iii', event, O['EVENT_X'], x, y, 0)
        self.event = event
        self.ui.pressed = 1
        return self.ui.fire(self.icons[name], O['EVT_POINTER_DOWN'], C.addressof(event))

    def dispatch(self, name, kind):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, O['EVENT_TYPE'], kind)
        return self.fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)(self.icons[name].addr, event)

    def hold(self):
        """The hold timer, run as the timer manager would at QUICK_HOLD_MS."""
        fn = [f for f, ms in self.timers if f == self.fn('quick_long')][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def test_quick_settings_names_the_lock_and_arms_three_holds(fw, st):
    quick = Quick(fw, st)
    assert quick.ui.text(quick.win.child('label_lock')) == 'Off' and quick.icons['img_lock'].props['image'] == 'drop_keylock'
    assert [quick.icons[n].props.get('_quick_hold') for n in Quick.ICONS] == [1, 2, None, None, None, None, 3, None]
    down = quick.fn('quick_down')
    assert [len(quick.ui.handled(quick.icons[n], down)) for n in Quick.ICONS] == [1, 1, 0, 0, 0, 0, 1, 0]
    cancel = quick.fn('quick_cancel')
    assert quick.ui.handled(quick.win, cancel) == [quick.win.addr] * 3  # destroy, close, background


@pytest.mark.parametrize('name, page', [('img_wifiswitch', b'systemset/wifi_page'), ('img_btswitch', b'systemset/bluetooth_page'),
                                        ('img_playset', b'playset/equalizer_page')])
def test_a_held_icon_opens_its_page_and_its_release_doesnt_click(fw, st, name, page):
    quick = Quick(fw, st)
    quick.press(name)
    assert quick.timers[-1] == (quick.fn('quick_long'), O['QUICK_HOLD_MS'])
    quick.hold()
    assert [C.string_at(c[1][0]) for c in fw.calls('navigator_to')] == [page]
    assert [c[1][0] for c in fw.calls('dialog_quit')] == [quick.win.addr]
    assert quick.dispatched == [(quick.win.addr, O['EVT_POINTER_ABORT'])]  # the press ends before the window does
    assert quick.dispatch(name, EVT_CLICK) == 11 and not fw.calls('stock_dispatch_trampoline')  # STOP
    quick.press(name)  # the next press is a press again
    assert quick.icons[name].props['_quick_hold'] > 0


def test_a_release_before_the_hold_is_stocks_tap(fw, st):
    quick = Quick(fw, st)
    quick.press('img_btswitch')
    quick.dispatch('img_btswitch', O['EVT_POINTER_UP_BEFORE'])
    assert fw.calls('timer_remove')
    quick.ui.pressed = 0
    quick.hold()  # a timer that fired anyway: the finger is up, nothing opens
    assert not fw.calls('navigator_to') and not fw.calls('dialog_quit')


@pytest.mark.parametrize('name, window', [('img_wifiswitch', b'wifi_page'), ('img_btswitch', b'bluetooth_page'),
                                          ('img_playset', b'equalizer_page')])
def test_an_open_settings_page_is_already_the_goal(fw, st, name, window):
    """Held over the page it opens (Control Center pulled down on Settings > Wi-Fi), a hold closes Control Center
    and opens no second page: a second wifi_page stacked on the first, and Return took two presses to leave
    Wi-Fi (the emulator, 2026-11-17)."""
    quick = Quick(fw, st)
    fw.mock('navigator_window_is_exist', lambda n, *_: C.string_at(n) == window)
    quick.press(name)
    quick.hold()
    assert fw.calls('dialog_quit') and not fw.calls('navigator_to')


def test_a_tap_on_the_lock_steps_through_its_choices(fw, st):
    quick = Quick(fw, st)
    first = quick.ui.text(quick.win.child('label_lock'))
    names, images, locks = [], [], []
    for _ in range(2):
        fw.fn('ringnav_quick_lock', C.c_int, C.c_void_p, C.c_void_p)(quick.win.addr, None)
        names.append(quick.ui.text(quick.win.child('label_lock')))
        images.append(quick.icons['img_lock'].props['image'])
        locks.append((g(fw, 'g_keylock_flag'), g(fw, 'g_keylock_mode')))
    assert locks == [(1, 2), (0, 2)]  # On (stock's mode 2, the buttons), then Off
    assert names == ['On', 'Off'] and names[-1] == first  # each choice named, round to where it began
    assert images[0] != images[1]  # one image locked, another not


def test_control_center_lays_stock_s_buttons_over_its_circles_and_slider(fw, st):
    """Control Center (control_center.c cc_open): Wi-Fi's and Bluetooth's buttons over their circles at opacity 0, so a
    tap and a hold there are stock's; the rest of stock's buttons hidden; the brightness slider over its track."""
    quick = Quick(fw, st)
    assert [quick.icons[n].box for n in ('img_wifiswitch', 'img_btswitch')] == [(46, 48, 40, 40), (112, 48, 40, 40)]
    assert [quick.icons[n].visible for n in Quick.ICONS] == [1, 1, 0, 0, 0, 0, 0, 0]
    assert quick.slider.parent.box == (188, 162, 167, 38) and quick.slider.box == (0, 0, 167, 38)


# A page's switch as a row, with no stock toolbars (the grid's toolbar 0).
def sleep_page(fw, st, image='switch_off'):
    theme(fw)
    ui = Ui(fw)
    win = ui.window('sleeptimer_page')
    toolbar = ui.add('view', win, (0, 270, 375, 50), 'view_toolbar')
    sw = ui.add('image', toolbar, (300, 0, 50, 50), 'img_switch', image=image)
    ui.add('view', win, (0, 60, 375, 200), 'view_sleep')
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(fn) or 1)
    fw.fn('ringnav_sleeptimer', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    return ui, win, sw, timers


def test_the_sleep_timers_switch_is_its_first_row_reading_the_switch(fw, st):
    ui, win, sw, timers = sleep_page(fw, st)
    label = win.child('_switch_row')
    button = label.parent
    s = GEOMETRY
    assert button.box == (0, 0, 375, s.set_row) and button.props['style'] == 's_btn_listitem'
    assert label.box[0] == s.text_x and ui.text(label) == 'Sleep Timer' and switch(button) == 'switch_off'


def test_the_rows_click_is_the_switchs_on_the_next_pass(fw, st):
    ui, win, sw, timers = sleep_page(fw, st)
    fw.mock('stock_dispatch_trampoline', lambda target, *_: sw.props.__setitem__('image', 'switch_on') or 0)
    button = win.child('_switch_row').parent
    ui.fire(button, EVT_CLICK)
    assert not fw.calls('stock_dispatch_trampoline')  # a click rebuilds Lock Screen's list: it waits
    C.CFUNCTYPE(C.c_int, C.c_void_p)(timers[-1])(None)
    assert [c[1][0] for c in fw.calls('stock_dispatch_trampoline')] == [sw.addr]
    assert switch(button) == 'switch_on'


# The library scan's refresh of a row it already holds.
MAP = 0x100022  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
ROW_HIGHS = high_halves(2)  # song_row's
libc.munmap.argtypes = [C.c_void_p, C.c_size_t]


@contextlib.contextmanager
def mapped(*pages):
    """The pages (address, size) mapped where asked for the test; whatever got mapped is unmapped after,
    whether the setup failed or not."""
    held = []
    try:
        for at, size in pages:
            got = libc.mmap(at, size, 3, MAP, -1, 0)
            if got not in (None, C.c_void_p(-1).value):
                held.append((got, size))
            assert got == at, f'{at:#x} is taken'
        yield
    finally:
        for got, size in held:
            libc.munmap(got, size)


@pytest.fixture
def song_row():
    """A songtable row with its name (+8) and path (+0xc), as a 64-bit read of each finds them: on the
    Q2 they are 4-byte pointers, here P() reads 8, so the name's high half is the path's low half.
    Their pages sit at addresses that make that so, at this file's two high halves (conftest.high_halves):
    the name at the first << 32, the path at the second << 32 | the first. The trick holds two pointers
    only: the CUE branch reads the row's tags at a 4-byte stride (0x10, 0x14...), which no 64-bit layout
    holds, so it belongs to test/patch.py, which runs the MIPS build (docs/building.md, Limits)."""
    name_at, path_at = ROW_HIGHS[0] << 32, ROW_HIGHS[1] << 32 | ROW_HIGHS[0]
    record = C.create_string_buffer(0x60)

    def fill(name, path, cue=(0, 0)):
        C.memmove(name_at, name + b'\0', len(name) + 1)
        C.memmove(path_at, path + b'\0', len(path) + 1)
        struct.pack_into('<Q', record, O['REC_NAME'], name_at)
        struct.pack_into('<I', record, O['REC_PATH'] + 4, path_at >> 32)  # with the name's high half, the path
        struct.pack_into('<ii', record, O['REC_CUE_START'], *cue)
        return C.addressof(record)
    with mapped((name_at, 0x1000), (path_at & ~0xfff, 0x10000)):
        yield fill


class Scan:
    """toolsGetMusicInfo's tags for the file, TagLib's open, and the statements the database got."""
    FIELDS = {'album': 0, 'songer': 0x100, 'style': 0x200, 'name': 0x280, 'composer': 0x480, 'albumsonger': 0x580}

    def __init__(self, fw, readable=1, info=1, exists=1, **tags):
        self.tags = {'album': b'Record', 'songer': b'Band', 'style': b'Rock', 'name': b'Song', 'composer': b'Writer',
                     'albumsonger': b'The Band'} | tags
        self.numbers = (1999, 7, 2)
        self.sql: list = []
        Files(fw)
        fw.mock('toolsCheckItemExist', lambda *_: exists)
        fw.mock('taglib_file_new', lambda *_: 0x5000)
        fw.mock('taglib_file_tag', lambda *_: readable)
        fw.mock('toolsGetMusicInfo', lambda a, out, path, *_: self.info(out) if info else 0)
        fw.mock('toolsHanziToPinyin', lambda s, key, *_: C.memmove(key, C.string_at(s) + b'\0', len(C.string_at(s)) + 1) and 0)
        fw.mock('toolsTrimLeft', lambda s, *_: self.trim(s, bytes.lstrip))
        fw.mock('toolsTrimRight', lambda s, *_: self.trim(s, bytes.rstrip))
        fw.mock('toolsUpdateDbTable', lambda db, sql, *_: self.sql.append((C.string_at(db), C.string_at(sql).decode())) and 0)
        self.refresh = fw.fn('ringnav_scan_refresh', C.c_int, C.c_void_p, C.c_char_p, C.c_int)

    def info(self, out):
        for key, at in self.FIELDS.items():
            C.memmove(out + at, self.tags[key] + b'\0', len(self.tags[key]) + 1)
        C.memmove(out + 0x680, struct.pack('<3i', *self.numbers), 12)
        return 1

    @staticmethod
    def trim(s, how):
        value = how(C.string_at(s))
        C.memmove(s, value + b'\0', len(value) + 1)


def test_a_rescanned_song_keeps_its_row_and_takes_its_files_tags(fw, song_row):
    """Only that row (its name, path and CUE bounds) is updated: its ID, creation time and play count stay,
    so saved queues and favourites still name the same song."""
    scan = Scan(fw)
    assert scan.refresh(song_row(b'01 Song.flac', b'/mnt/mmc/Band/01 Song.flac'), b'songtable', 0) == 1
    assert scan.sql == [(b'/mnt/data/database.db',
                         "update songtable set album='Record',songer='Band',style='Rock',name='Song',composer='Writer',"
                         f"albumsonger='The Band',Initial={ord('S')},year=1999,track=7,cd_num=2 where filename='01 Song.flac' "
                         "and fileurl='/mnt/mmc/Band/01 Song.flac' and time=0 and time_end=0")]


def test_a_quote_in_a_tag_or_path_is_doubled(fw, song_row):
    scan = Scan(fw, songer=b"Guns N' Roses")
    scan.refresh(song_row(b"It's.flac", b"/mnt/mmc/It's.flac"), b'songtable', 0)
    sql = scan.sql[0][1]
    assert "songer='Guns N'' Roses'" in sql and "filename='It''s.flac'" in sql and "fileurl='/mnt/mmc/It''s.flac'" in sql


@pytest.mark.parametrize('title, initial', [(b'abc', 'A'), (b'  zed', 'Z'), (b'9 Lives', '*'), (b'!Hey', '#'), (b'', 'N'), (b'(null)', 'N')])
def test_the_initial_is_the_titles_else_the_file_names(fw, song_row, title, initial):
    scan = Scan(fw, name=title)
    scan.refresh(song_row(b'name.flac', b'/mnt/mmc/name.flac'), b'songtable', 0)
    assert f',Initial={ord(initial)},' in scan.sql[0][1]


@pytest.mark.parametrize('readable, info', [(0, 1), (1, 0)])
def test_a_file_without_readable_tags_keeps_the_old_ones(fw, song_row, readable, info):
    scan = Scan(fw, readable=readable, info=info)
    assert scan.refresh(song_row(b'a.flac', b'/mnt/mmc/a.flac'), b'songtable', 0) == 1 and not scan.sql


def test_a_statement_that_wont_fit_is_never_run(fw, song_row):
    scan = Scan(fw)
    assert scan.refresh(song_row(b'a.flac', b'/mnt/mmc/' + b"'" * 4200), b'songtable', 0) == 1 and not scan.sql


# The crash log's records (crash_note): the file in progress, and every nth a line with demo's memory.
class Proc:
    """open/read/write/close over files in memory: /proc/self/statm's pages, and what the C wrote."""
    def __init__(self, fw, statm=b'5000 1234 300 10 0 900 0\n'):
        self.files = {'/proc/self/statm': statm} if statm is not None else {}
        self.fds: dict = {}
        self.flags: list = []
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', lambda fd, out, n, *_: self.read(fd, out, n))
        fw.mock('fw_write', lambda fd, data, n, *_: self.write(fd, C.string_at(data, n)))
        fw.mock('fw_close', lambda fd, *_: self.fds.pop(fd) and 0)

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        if not flags & 3 and path not in self.files:
            return -1
        self.flags.append((path, flags & 0xffffffff))
        if flags & 0x200:
            self.files[path] = b''
        self.files.setdefault(path, b'')
        fd = 10 + len(self.fds)
        self.fds[fd] = [path, 0]
        return fd

    def read(self, fd, out, n):
        path, at = self.fds[fd]
        data = self.files[path][at:at + n]
        C.memmove(out, data, len(data))
        self.fds[fd][1] += len(data)
        return len(data)

    def write(self, fd, data):
        self.files[self.fds[fd][0]] += data
        return len(data)


def crash_note(fw, n, path, every=3):
    fw.fn('crash_note', None, C.c_char_p, C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)(path, b'/tmp/file', b'/tmp/log', every, n)


def test_the_crash_log_holds_the_file_in_progress_and_every_nth_with_demos_memory(fw):
    proc, n = Proc(fw), (C.c_uint * 2)()
    for i in range(1, 8):
        crash_note(fw, n, b'/mnt/mmc/%d.flac' % i)
        assert proc.files['/tmp/file'] == b'/mnt/mmc/%d.flac\n' % i
    assert proc.files['/tmp/log'] == b'3 4936 /mnt/mmc/3.flac\n6 4936 /mnt/mmc/6.flac\n'  # resident pages of 4 kB
    assert list(n) == [7, len(proc.files['/tmp/log'])]
    log_opens = [f for p, f in proc.flags if p == '/tmp/log']  # the Q2's (MIPS) flags: O_APPEND 8, O_TRUNC 0x200
    assert log_opens[0] & 0x200 and not log_opens[0] & 8 and log_opens[1] & 8 and not log_opens[1] & 0x200


def test_a_new_demo_starts_the_log_afresh_and_it_stops_at_64_kb(fw):
    proc, n = Proc(fw), (C.c_uint * 2)()
    proc.files['/tmp/log'] = b'last demo\n'
    crash_note(fw, n, b'/a', every=1)
    assert proc.files['/tmp/log'] == b'1 4936 /a\n'
    n[1] = 65536
    crash_note(fw, n, b'/b', every=1)
    assert proc.files['/tmp/log'] == b'1 4936 /a\n' and proc.files['/tmp/file'] == b'/b\n'


def test_a_long_path_is_cut_in_the_log_and_unreadable_memory_is_0(fw):
    proc, n = Proc(fw, statm=None), (C.c_uint * 2)()
    crash_note(fw, n, b'/' + b'p' * 700, every=1)
    assert proc.files['/tmp/log'] == b'1 0 /' + b'p' * 499 + b'\n'


def test_the_library_scan_notes_each_file_it_reads(fw):
    proc = Proc(fw)
    Files(fw)
    info = (C.c_char * 0x698)()
    for i in range(500):
        fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, info, b'/mnt/mmc/%d.mp3' % i)
    assert proc.files['/tmp/q2-scan-file'] == b'/mnt/mmc/499.mp3\n'
    assert proc.files['/tmp/q2-scan.log'] == b'500 4936 /mnt/mmc/499.mp3\n'


# An Ogg file whose comment header is over OGG_TAGS_MAX: xiph.c reads it, not TagLib.
def comments(*items, vendor=b'libopus'):
    return struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', len(items)) + b''.join(struct.pack('<I', len(c)) + c for c in items)


def opus(tags):
    """An Ogg Opus file: OpusHead, then tags as packet 1 on pages of its own (no CRC: xiph.c reads none)."""
    pages, seq = [], 0
    for packet in (b'OpusHead' + bytes([1, 2]) + struct.pack('<HIhB', 312, 48000, 0, 0), tags):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        for at in range(0, len(lacing), 255):
            part = lacing[at:at + 255]
            pages.append(b'OggS\0' + bytes([at > 0]) + bytes(8) + struct.pack('<III', 1, seq, 0) + bytes([len(part)]) +
                         bytes(part) + packet[at * 255:at * 255 + sum(part)])
            seq += 1
    return b''.join(pages)


BIG = b'P=' + b'v' * (4 << 20)


def fix_id3(fw):
    """Stock's toolsFixId3ErrorCode as it treats UTF-8 text: out is the text."""
    fw.mock('toolsFixId3ErrorCode', lambda latin, latin1, utf8, out, k, *_: C.memmove(out, C.string_at(utf8) + b'\0', len(C.string_at(utf8)) + 1) and 0)


@pytest.mark.parametrize('a', [0, 1])
def test_an_oversized_ogg_header_is_read_field_by_field(fw, a):
    genre = b' / '.join(b'g%02d' % i + b'x' * 20 for i in range(8))
    Files(fw, {'/mnt/mmc/a.opus': opus(b'OpusTags' + comments(
        b'TITLE=Song', b'ARTIST=Band', b'ALBUM=Record', b'GENRE=' + genre, b'COMPOSER=Writer', b'ALBUMARTIST=The Band',
        b'DATE=1999', b'TRACKNUMBER=7', b'DISCNUMBER=2', b'MQAENCODER=x', BIG))})
    fix_id3(fw)
    info = (C.c_char * 0x698)()
    assert fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(a, info, b'/mnt/mmc/a.opus') == 1
    at = lambda o: C.string_at(C.addressof(info) + o)
    assert [at(o) for o in (0x280, 0x100, 0, 0x480, O['MUSIC_INFO_ALBUM_ARTIST'])] == [b'Song', b'Band', b'Record', b'Writer', b'The Band']
    assert at(0x200) == b' / '.join(genre.split(b' / ')[:5]) and len(at(0x200)) == 0x7f  # whole entries in 0x7f bytes
    year, disc, track, flags, rate = struct.unpack_from('<5i', info, 0x680)
    assert (year, disc, track) == (1999, 2, 7) and flags & 0x100  # MQA
    assert (rate, flags & 16) == ((48000, 16) if a else (0, 0))  # the audio properties, asked for
    assert not fw.calls('stock_music_info_trampoline')


def test_a_small_ogg_header_is_taglibs(fw):
    Files(fw, {'/mnt/mmc/a.opus': opus(b'OpusTags' + comments(b'TITLE=Song'))})
    fw.mock('stock_music_info_trampoline', lambda *_: 1)
    info = (C.c_char * 0x698)()
    fw.fn('ringnav_music_info', C.c_int, C.c_int, C.c_void_p, C.c_char_p)(0, info, b'/mnt/mmc/a.opus')
    assert len(fw.calls('stock_music_info_trampoline')) == 1


# A card app's song in a library queue takes its file's tags as it starts.
@pytest.fixture
def low():
    """A page under 4 GB: strings whose addresses fit the Q2's 4-byte record fields."""
    at = 0x10000000
    used = [at]

    def string(s):
        here = used[0]
        C.memmove(here, s + b'\0', len(s) + 1)
        used[0] += (len(s) + 16) & ~7
        return here
    with mapped((at, 0x10000)):
        yield string


@pytest.fixture
def playing(fw, mcl, low):
    """The queue's playing record: its path a 4-byte field, as the Q2's, and no album."""
    deques = Deques(fw)
    record = C.create_string_buffer(0x60)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new([C.addressof(record)])
    mcl.pos, mcl.type = 0, 0xf001
    path = low(b'/mnt/mmc/Deezer/a.flac')
    struct.pack_into('<I', record, O['REC_PATH'], path)
    strings(fw)
    fw.mock('toolsGetMusicInfo', lambda a, out, p, *_: card_info(out))
    return record, path


def card_info(out):
    for at, value in ((0x280, b'Song'), (0x100, b'Band'), (O['MUSIC_INFO_ALBUM_ARTIST'], b'The Band')):
        C.memmove(out + at, value + b'\0', len(value) + 1)
    C.memmove(out + 0x684, struct.pack('<ii', 4, 2), 8)
    return 1


def id3_path(fw, path):
    id3 = (C.c_char * 0xa84)()
    fw.fn('ringnav_id3_path', C.c_void_p, C.c_void_p, C.c_char_p)(id3, path)
    return id3


def test_a_card_apps_song_takes_its_files_tags_as_it_starts(fw, playing):
    record, path = playing
    id3 = id3_path(fw, C.string_at(path))
    field = lambda o: C.string_at(struct.unpack_from('<Q', record, o)[0])
    assert [field(O[k]) for k in ('REC_TITLE', 'REC_ARTIST', 'REC_ALBUM_ARTIST')] == [b'Song', b'Band', b'The Band']
    assert struct.unpack_from('<ii', record, O['REC_TRACK']) == (4, 2)
    album = O['ID3_ALBUM']
    assert [C.string_at(C.addressof(id3) + album + o) for o in (0x280, 0x100, O['MUSIC_INFO_ALBUM_ARTIST'])] == [b'Song', b'Band', b'The Band']
    assert C.string_at(id3) == C.string_at(path)


@pytest.mark.parametrize('case', ['album', 'folder', 'other', 'off card'])
def test_a_song_that_has_its_tags_or_isnt_a_card_apps_is_left(fw, playing, low, mcl, case):
    record, path = playing
    if case == 'album':
        struct.pack_into('<I', record, O['REC_ALBUM'], low(b'Record'))
    elif case == 'folder':
        mcl.type = 1  # stock parses a folder play's file itself
    elif case == 'off card':
        struct.pack_into('<I', record, O['REC_PATH'], low(b'/mnt/usb/a.flac'))
    id3_path(fw, b'/mnt/usb/a.flac' if case == 'off card' else C.string_at(path) + (b'x' if case == 'other' else b''))
    assert struct.unpack_from('<I', record, O['REC_TITLE'])[0] == 0 and struct.unpack_from('<i', record, O['REC_TRACK'])[0] == 0


# The paused seek, and Bluetooth's start on connect.
@pytest.mark.parametrize('status, held', [(3, 43), (2, 0)])
def test_a_paused_seek_waits_for_play(fw, mcl, status, held):
    deques = Deques(fw)
    record = C.create_string_buffer(0x60)
    struct.pack_into('<i', record, O['REC_CUE_START'], 120)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new([C.addressof(record)])
    fw.mock('mclGetPlayStatus', lambda *_: status)
    fw.mock('player_playtime_and_length', lambda sec, total, *_: C.memmove(sec, struct.pack('<i', 42), 4) and 0)
    assert fw.fn('navigation__seek_position', C.c_int, C.c_int)(30) == held
    if status == 3:  # stock's seek resumes: the player's own, at the CUE track's offset, plays nothing
        assert [c[1][0] for c in fw.calls('mclSetSeek')] == [150] and not fw.calls('player_seek_time')
    else:
        assert [c[1][0] for c in fw.calls('player_seek_time')] == [30] and not fw.calls('mclSetSeek')


@pytest.mark.parametrize('resume, saved, start', [(1, 0, (1,)), (0, 2, (0,)), (0, 3, (1,)), (0, 1, ()), (0, 0, ())])
def test_a_headset_resumes_only_what_was_going(fw, resume, saved, start):
    Settings(fw, RESUME=str(resume).encode())
    fw.mock('mclGetSavePlayStatus', lambda *_: saved)
    fw.mock('player_save_start', lambda pause, *_: 5)
    got = fw.fn('ringnav_bt_connect_start', C.c_int, C.c_int)(1)
    assert [c[1][0] for c in fw.calls('player_save_start')] == list(start) and got == (5 if start else 0)


# The lyrics' follow (LYRIC_FOLLOW) and the page's rebuild of its rows (LYRIC_ROWS).
def lyrics(fw, st, ui, rows=20, row_h=20, h=178, np=False, page=1):
    g(fw, 'g_backlight_status', 1)
    win = ui.window('playing_page')
    lrc = ui.add('scroll_view', win, (0, 60, 375, h), 'scroll_lrc')
    for i in range(rows):
        ui.add('label', lrc, (0, i * row_h, 375, row_h))
    lrc.int_at(O['VIEW_CONTENT_H']).value = rows * row_h
    if np:
        slide = ui.add('slide_view', win, (0, 0, 375, 320), 'slide', value=page)
        st('np_lrc', C.c_void_p).value, st('np_slide', C.c_void_p).value, st('np_win', C.c_void_p).value = lrc.addr, slide.addr, win.addr
    return lrc


def follow(fw, lrc, y, ms=300):
    return fw.fn('ringnav_lyric_follow', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int)(lrc.addr, 0, y, ms)


def test_the_lyrics_end_on_a_whole_row(fw, st):
    """178 px is no whole number of 20 px rows: the content grows to the first row top at or past the
    end plus the view, so the last offset shows a row whole at the top."""
    lrc = lyrics(fw, st, Ui(fw))
    follow(fw, lrc, 60)
    content = lrc.int_at(O['VIEW_CONTENT_H']).value
    assert (content - 178) % 20 == 0 and 400 - 178 <= content - 178 < 400 - 178 + 20
    assert [c[1][:4] for c in fw.calls('scroll_view_scroll_to')] == [(lrc.addr, 0, 60, 300)]


@pytest.mark.parametrize('held, spun, pressed', [(1, 0, 0), (0, 2, 0), (0, 0, 1)])
def test_lyrics_the_wheel_or_a_finger_moved_stay(fw, st, held, spun, pressed):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True)
    st('lyric_held').value, st('lyric_spun').value = held, spun
    ui.pressed = pressed
    assert follow(fw, lrc, 60) == 0 and not fw.calls('scroll_view_scroll_to')


def clock(fw, start=1000):
    """time_now_ms: the list's one item, which the test moves on."""
    now = [start]
    fw.mock('time_now_ms', lambda *_: now[0])
    return now


def glided(fw, lrc, now, passes=60, ms=16):
    """The offsets the glide's steps (lyric_step, each UI pass, ms apart on the clock now) move the
    lyrics through, the view taking each one as stock's setter does."""
    fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<i', lrc.block, O['SCROLL_Y'], i32(y)))
    step = fw.fn('navigation__lyric_step', None, C.c_void_p)
    seen = []
    for _ in range(passes):
        now[0] += ms
        step(lrc.addr)
        seen.append(lrc.int_at(O['SCROLL_Y']).value)
    return seen


def eased(offsets, start, end):
    """Whether the offsets ease from start to end: the first step part of the way, each on from the
    last, none past the end, and there."""
    path = [start] + offsets
    return start < offsets[0] < end and all(a <= b <= end for a, b in zip(path, path[1:])) and offsets[-1] == end


def test_on_the_lyrics_page_the_follow_glides(fw, st):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True)
    animator = C.create_string_buffer(8)
    struct.pack_into('<Q', lrc.block, O['VIEW_ANIMATOR'], C.addressof(animator))
    now = clock(fw)
    follow(fw, lrc, 60)
    assert not fw.calls('scroll_view_scroll_to') and fw.calls('widget_animator_destroy')  # no jump
    assert struct.unpack_from('<Q', lrc.block, O['VIEW_ANIMATOR'])[0] == 0  # stock's animator stopped
    assert eased(glided(fw, lrc, now), 0, 60)  # the steps carry it there
    follow(fw, lrc, 10000)  # within the content: no further than its end
    assert eased(glided(fw, lrc, now), 60, lrc.int_at(O['VIEW_CONTENT_H']).value - 178)


def test_off_the_lyrics_page_the_follow_scrolls(fw, st):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True, page=0)
    st('lyric_glide').value = 1
    follow(fw, lrc, 60)
    assert st('lyric_glide').value == 0 and fw.calls('scroll_view_scroll_to')


LYRIC_ROWS = 0x53b67c  # stock's rebuild of scroll_lrc's rows, which ringnav_lyric_rows calls


def rows_rebuilt(fw, win, result=0):
    """ringnav_lyric_rows over stock's builder at its address: x86-64 standing in, returning result,
    and the page as conftest's map had it again after (bytes and protection), as nothing else runs there."""
    page = LYRIC_ROWS & ~0xfff
    saved = C.string_at(LYRIC_ROWS, 6)
    claim(LYRIC_ROWS)
    C.memmove(LYRIC_ROWS, b'\xb8' + struct.pack('<I', result) + b'\xc3', 6)  # mov eax, result; ret
    try:
        return fw.fn('ringnav_lyric_rows', C.c_int, C.c_void_p)(win.addr)
    finally:
        C.memmove(LYRIC_ROWS, saved, 6)
        assert not libc.mprotect(page, 0x2000, 3)


def test_a_rebuild_keeps_the_offset_for_the_same_song(fw, st):
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui)
    lrc.int_at(O['SCROLL_Y']).value = 120
    assert rows_rebuilt(fw, ui.top, 9) == 9
    assert [c[1][:3] for c in fw.calls('scroll_view_set_offset')] == [(lrc.addr, 0, 120)]


def test_rows_for_another_song_start_at_the_top(fw, st):
    """Whatever held the old song's lyrics goes with them: the page's follow moves the new ones, and
    the rows are this song's, so the next rebuild keeps their offset."""
    ui = Ui(fw)
    lrc = lyrics(fw, st, ui, np=True)
    offsets = lambda: [c[1][:3] for c in fw.calls('scroll_view_set_offset')]
    lrc.int_at(O['SCROLL_Y']).value = 120
    st('lyric_rows_song', C.c_uint).value = 0x1234  # the last rows' song; nothing plays now
    st('lyric_held').value = 1  # the wheel moved them
    rows_rebuilt(fw, ui.top)
    assert offsets() == [(lrc.addr, 0, 0)]
    # held no more: the page's follow (off the page, a jump) takes them to the playing line, none yet
    lrc.int_at(O['SCROLL_Y']).value = 50
    fw.fn('navigation__lyric_follow', None, C.c_void_p, C.c_int, C.c_int, C.c_int)(lrc.addr, 0, 0, 0)
    assert offsets() == [(lrc.addr, 0, 0)] * 2
    rows_rebuilt(fw, ui.top)
    assert offsets()[2:] == [(lrc.addr, 0, 50)]


# The status bar: the codec badge's fade, the percentage's width, the clock.
RET_REPEAT = 8  # AWTK's ret_t: a timer that answers it runs again, any other answer ends it


def test_a_codec_badge_fades_out_into_the_glyph_and_the_glyph_fades_in(fw, st):
    ui = Ui(fw)
    bt = ui.add('image', None, (0, 0, 30, 20), 'img_bt', image='bar_ldac')
    st('bar_bt', C.c_void_p).value = bt.addr
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(i32(ms)) or 3)
    fade = fw.fn('navigation__codec_fade', C.c_int, C.c_void_p)
    steps = O['CODEC_STEPS']
    answers, opacity, images = [], [], []
    for _ in range(2 * steps):
        answers.append(fade(None))
        opacity.append(fw.calls('widget_set_opacity')[-1][1][1])
        images.append(bt.props['image'])
    out, back = opacity[:steps], opacity[steps - 1:]  # CODEC_STEPS steps each way
    assert all(a > b for a, b in zip(out, out[1:])) and out[-1] == 0  # out to nothing
    assert all(a < b for a, b in zip(back, back[1:])) and back[-1] == 255  # in to whole
    swap = images.index(BT_GLYPH)  # the badge becomes the glyph unseen, and stays
    assert opacity[swap] == 0 and set(images[:swap]) == {'bar_ldac'} and set(images[swap:]) == {BT_GLYPH}
    # the CODEC_MS one-shot that ran the first step ends, a CODEC_STEP_MS timer repeats the rest, then ends
    assert answers[0] != RET_REPEAT and timers == [O['CODEC_STEP_MS']]
    assert answers[1:-1] == [RET_REPEAT] * (2 * steps - 2) and answers[-1] != RET_REPEAT
    assert st('codec_timer', C.c_uint).value == 0


@pytest.mark.parametrize('level, digits', [(5, 1), (42, 2), (100, 3)])
def test_the_percentage_is_as_wide_as_its_digits(fw, st, level, digits):
    width = fw.fn('navigation__pct_width', C.c_int, C.c_uint)
    assert width(level) == O['BATT_PCT_W']  # until the first paint measures
    st('pct_digit').value, st('pct_sign').value = 11, 13
    assert width(level) == digits * 11 + 13


@pytest.mark.parametrize('titled, shown', [(0, '6:05 PM'), (1, '6:05')])
def test_the_clocks_timer_shows_the_minute_and_rearms(fw, st, titled, shown):
    """On a status row (a bar with its hidden title) the 12-hour clock drops AM and PM, as iOS's does."""
    ui = Ui(fw)
    bar = ui.window('statusbar', top=False)
    clock = ui.add('label', bar, (150, 0, 75, 26), 'label_clock')
    if titled:
        st('bar_title', C.c_void_p).value = ui.add('label', bar, (0, 0, 140, 26), 'label_title').addr
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar.addr
    Settings(fw)
    fw.mock('fw_time', lambda *_: 1700000000)
    fw.mock('fw_localtime_r', lambda when, out, *_: C.memmove(out, struct.pack('<3i', 40, 5, 18), 12) and out)
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append((fn, i32(ms))) or 6)
    st('clock_timer', C.c_uint).value = 9
    assert fw.fn('clock_due', C.c_int, C.c_void_p)(None) == 0
    assert ui.text(clock) == shown and timers == [(C.cast(fw.lib.clock_due, C.c_void_p).value, 20000)]
    assert not fw.calls('timer_remove')  # the firing timer is done: nothing else to stop


# OLED's status row clock (navigation.c clock_place): at the row's left on every page, its text starting
# STATUS_ROW_INSET in, as the battery ends that far from the right. Its box is its text's width (canvas_measure_text)
# and hscroll_label's two 2 px margins.

CLOCK_W = 38  # canvas_measure_text's width of "00:00", the widest clock
INK_ROW = 0xffababab


class StatusRow(Bar):
    def __init__(self, fw, st, title=''):
        theme(fw)
        super().__init__(fw, 2)
        self.ui.window('home_page' if not title else 'albums_page')
        self.w['view_battery'].move(322, 25, 30, 24)  # the slot's x on the bar: the battery's body starts there
        self.w['label_title'].move(22, 25, 240, 24)
        self.w['label_clock'].move(22, 25, 240, 24)
        self.ui.set_text(self.w['label_title'], title)
        fw.mock('style_get_int', lambda style, key, default, *_: i32(INK_ROW) if C.string_at(key) == b'text_color' else i32(default))
        fw.mock('widget_get_canvas', lambda *_: 0x40)
        fw.mock('canvas_measure_text', lambda *_: float(CLOCK_W))
        fw.mock('timer_add', lambda *_: 5)
        self.sync()  # stock's showface: finds the bar's widgets

    def place(self):
        self.fw.fn('navigation__clock_place', None, C.c_void_p)(self.ui.top.addr)
        return self.w['label_clock']


@pytest.mark.parametrize('title', ['', 'A Long Album Name That Runs On Past The Clock'])
def test_every_page_s_clock_starts_at_the_row_s_inset_and_no_title_moves(fw, st, title):
    """The clock at the row's left on every page, titled or not (the user's layout D, 2026-11-25): its text from
    STATUS_ROW_INSET, its box its text; the bar's title, hidden, is neither moved nor sized, and the slots' room starts
    CLOCK_BESIDE (7) past the widest clock."""
    row = StatusRow(fw, st, title)
    title_box = row.w['label_title'].box
    row.ui.set_text(row.w['label_clock'], '9:41')
    clock = row.place()
    x, y, w, _ = clock.box
    assert x + 2 == O['STATUS_ROW_INSET'] and w == CLOCK_W + 4 and y == 25
    assert clock.props['style:normal:text_color'] == i32(INK_ROW)  # Text Secondary
    assert row.w['label_title'].box == title_box
    sr_room = C.c_int.from_address(C.addressof(C.c_char.in_dll(fw.lib, 'status_row__sr')) + fw.sizes['status_row__sr'] - 16)
    assert sr_room.value == O['STATUS_ROW_INSET'] + CLOCK_W + 7


def test_home_split_s_clock_goes_left_its_inset_the_battery_s_from_the_right(fw, st):
    """Home in Split (the user's, 2026-11-20, design/home-clock-left): the clock off the art, at the row's left, its text
    starting STATUS_ROW_INSET in, the battery's nub ending as far from the right; its colour as it was over the art."""
    from test_coverflow import Home
    row = StatusRow(fw, st)
    home = fw.var('coverflow__home', Home)
    art = row.ui.add('view', row.ui.top, (196, 0, 179, 262), 'art')
    home.art, home.win = art.addr, row.ui.top.addr
    clock = row.place()
    assert clock.box[0] + 2 == O['STATUS_ROW_INSET'] and clock.box[2] == CLOCK_W + 4 and clock.box[1] == 25
    assert clock.props['style:normal:text_color'] == i32(INK_ROW)  # no cover under the bar: the row's grey
    home.sl[0].tw = 128  # a cover that fills the pane, up under the status row
    clock = row.place()
    assert clock.box[0] + 2 == O['STATUS_ROW_INSET']
    assert clock.props['style:normal:text_color'] == i32(0xffffffff)
    moves = len(fw.calls('widget_move_resize'))
    row.place()  # the next paint: nothing to move, so no repaint after it (the style reads back its own colour)
    assert len(fw.calls('widget_move_resize')) == moves


# A tap's row takes the selection (ringnav_dispatch's click).
def tap(fw, target):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, O['EVENT_TYPE'], EVT_CLICK)
    return fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)(target.addr, event)


def test_a_tapped_row_takes_the_selection(fw, st):
    page = Page(fw)
    fw.mock('stock_dispatch_trampoline', lambda *_: 4)
    assert tap(fw, page.rows[2].children[0]) == 4  # its title: the row that owns it
    assert page.view.props['_ringnav_index'] == 2 and st('touch_mode').value == 1
    assert page.view.addr in [c[1][0] for c in fw.calls('widget_invalidate_force')]


def test_a_tap_in_one_pane_clears_the_others(fw, st):
    page = Page(fw)
    other = page.ui.add('scroll_view', page.ui.top, (0, 200, 375, 120), 'other')
    other.byte_at(O['VIEW_VERTICAL']).value = 1
    item = page.ui.add('list_item', other, (0, 0, 375, 48))
    page.ui.add('button', item, (0, 0, 375, 48)).clickable()
    other.props['_ringnav_index'] = 0
    tap(fw, page.rows[3])
    assert other.props['_ringnav_index'] == -1 and page.view.props['_ringnav_index'] == 3
    assert other.addr in [c[1][0] for c in fw.calls('widget_invalidate_force')]


def test_a_tap_off_every_row_changes_nothing(fw, st):
    page = Page(fw)
    tap(fw, page.view)
    assert page.view.props['_ringnav_index'] == 1


# A Library row that can't play now (catalogue.c): its text and its offline mark.
def test_a_greyed_row_keeps_its_contrast_on_the_selection_and_carries_the_offline_mark(fw, st, layout):
    """Off the selection a greyed row's text is CAT_GREY; on the accent's bar it is the bar's own ink at
    70% (not grey on blue), and each greyed row has the cloud-slash mark at its right in its text's colour,
    after the rows are painted (ringnav_paint)."""
    page = Page(fw)
    labels = [b.children[0] for b in page.rows]
    for label in labels:  # titles as wide as the row: they would run under the mark
        label.move(10, 0, 320, 48)
    page.paint()
    normal = text_ink(fw, st, labels[1])  # the selected row's ink before any greying
    url = C.create_string_buffer(b'/mnt/mmc/.subsonic/cat/al1/s1.flac')
    record = C.create_string_buffer(O['REC_SIZE'] + 8)
    struct.pack_into('<Q', record, O['REC_PATH'], C.addressof(url))
    struct.pack_into('<i', record, O['REC_TYPE'], 8)
    fw.mock('tk_str_start_with', stock_start_with)
    for i in (1, 2):  # Local Only: a server row can't play
        fw.fn('catalogue_mark', None, C.c_void_p, C.c_void_p, C.c_int)(page.rows[i].parent.addr, C.addressof(record), 0)
    page.paint()  # before the rows: the greyed rows' titles end 12 px clear of the mark's ink (322 +- 10), their
    # text 2 px inside the box
    assert [label.box[2] for label in labels] == [320, 272, 272, 320]
    assert text_ink(fw, st, labels[0]) == normal  # an ordinary row: as it was
    assert text_ink(fw, st, labels[2]) == 0xff666666
    dim = text_ink(fw, st, labels[1])
    assert dim == (normal & 0xffffff) | 0xb3 << 24
    page.canvas.px.clear()
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(page.view.addr, page.canvas.addr)
    mark = {(x, y): c for (x, y), c in page.canvas.px.items() if x >= 300}
    rows = {y // 48: [] for x, y in mark}
    for (x, y), c in mark.items():
        rows[y // 48].append(c)
    assert sorted(rows) == [1, 2]  # rows 1 and 2, none on 0 or 3
    assert {c & 0xffffff for c in rows[1]} == {normal & 0xffffff} and max(c >> 24 for c in rows[1]) == 0xb3
    assert {c & 0xffffff for c in rows[2]} == {0x666666} and max(c >> 24 for c in rows[2]) == 0xff
    assert len(rows[2]) > 100  # the glyph's pixels, not a dot
    assert min(x for x, y in mark) == 312  # the chevron's column: 322 less half the mark
    # a row bound to a song that plays again takes its width back
    fw.fn('catalogue_forget', None, C.c_void_p)(page.rows[2].parent.addr)
    page.paint()
    assert [label.box[2] for label in labels] == [320, 272, 320, 320]


def test_a_long_list_with_greyed_rows_paints_in_lookups_its_rows_in_sight_decide(fw, st):
    """A list with greyed rows (grey_rows, paint_songs, paint_offline, paint_chevrons) asks the list's own
    chevron (drill, which walks every row of the list) once a painter, not once a row, and the rows out of
    sight nothing: a playlist page of some hundreds of server songs, offline, painted in about 200 ms on the
    owner's Q2, all of it these lookups (docs/inefficiencies.md#37-a-lists-offline-marks-asked-the-whole-list-once-a-row-resolved). The
    rows in sight stay as they were: the greyed rows' titles end clear of the mark, which each of them carries."""
    url = C.create_string_buffer(b'/mnt/mmc/.subsonic/cat/al1/s1.flac')
    record = C.create_string_buffer(O['REC_SIZE'] + 8)
    struct.pack_into('<Q', record, O['REC_PATH'], C.addressof(url))
    struct.pack_into('<i', record, O['REC_TYPE'], 8)

    kept: list = []  # every page's widgets live to the end, so no address is reused

    def lookups(rows):
        page = Page(fw, rows=rows)
        labels = [b.children[0] for b in page.rows]
        for label in labels:
            label.move(10, 0, 320, 48)
        fw.mock('tk_str_start_with', stock_start_with)
        for i in (1, 2):  # Local Only: a server row can't play
            fw.fn('catalogue_mark', None, C.c_void_p, C.c_void_p, C.c_int)(page.rows[i].parent.addr, C.addressof(record), 0)
        page.paint()
        for label in labels[:7]:  # each row in sight painted, which the mark takes its ink from
            text_ink(fw, st, label)
        count = [0]
        prop_str = page.ui.prop_str

        def counted(*a):
            count[0] += 1
            return prop_str(*a)
        fw.mock('widget_get_prop_str', counted)
        page.canvas.px.clear()
        page.paint()
        fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(page.view.addr, page.canvas.addr)
        # the mark's column on the first four rows, above the list's fade at its foot
        marks = sorted({y // 48 for x, y in page.canvas.px if 300 <= x < 340 and y < 4 * 48})
        assert [label.box[2] for label in labels[:4]] == [320, 272, 272, 320]  # the mark's room, as before
        assert marks == [1, 2]
        for i in (1, 2):  # out of catalogue.c's rows, before the next page's widgets
            fw.fn('catalogue_forget', None, C.c_void_p)(page.rows[i].parent.addr)
        kept.append(page)
        return count[0]

    short, long = lookups(20), lookups(200)
    # the 180 rows added, all out of sight: at most a few lookups each (load_rows' walk, more_image's), where
    # asking drill once a row cost about 2 x 200 x 200
    assert long - short <= 4 * 180, (short, long)


def test_oleds_song_rows_end_their_title_and_inline_artist_clear_of_the_offline_mark(fw, st):
    """OLED's one-line song rows (song_rows.c) on Songs' page: a greyed row's title and inline artist end
    SONG_COL_GAP (12 px) short of the offline mark's ink, not on the right column under it; a title with no room
    left for an artist drops it and stops there too. A row that can play keeps the right column. The box
    song_row gives a title is the last word: catalogue_fit, which runs first, never widens it again, so the rows
    settle instead of resizing on every paint."""
    page = Page(fw, rows=3, pitch=32, sel=None, name='allmusic_page')
    songs = [('Ten Thousand Weight', 'The Head and the Heart'),  # the shot's row: the artist ran under the mark
             ('A Title That Runs All The Way Across', 'Some Artist'),
             ('Ten Thousand Weight', 'The Head and the Heart')]  # the same, playable
    for row, (title, artist) in zip(page.rows, songs):
        row.children[0].move(10, 0, 320, 32)  # stock's box, past the mark: catalogue_fit narrows it first
        page.ui.set_text(row.children[0], title)
        page.ui.set_text(page.ui.add('hscroll_label', row, (10, 0, 260, 32)), artist)  # stock's second line
    url = C.create_string_buffer(b'/mnt/mmc/.subsonic/cat/al1/s1.flac')
    record = C.create_string_buffer(O['REC_SIZE'] + 8)
    struct.pack_into('<Q', record, O['REC_PATH'], C.addressof(url))
    struct.pack_into('<i', record, O['REC_TYPE'], 8)
    fw.mock('tk_str_start_with', stock_start_with)
    for i in (0, 1):  # Local Only: a server row can't play
        fw.fn('catalogue_mark', None, C.c_void_p, C.c_void_p, C.c_int)(page.rows[i].parent.addr, C.addressof(record), 0)
    for paint in range(3):  # a box changed under a paint is final on the next, and stays
        moved = len(fw.calls('widget_move_resize')) + len(fw.calls('widget_resize'))
        page.canvas.texts.clear()
        page.paint()
    assert len(fw.calls('widget_move_resize')) + len(fw.calls('widget_resize')) == moved  # none on the last
    artists = {y // 32: (x, x + w - 1) for text, (x, y, w, h), _ in page.canvas.texts if text[0] in 'TS…'}
    for row in page.rows:  # each row's text painted, which the mark takes its ink from
        text_ink(fw, st, row.children[0])
    page.canvas.px.clear()
    fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)(page.view.addr, page.canvas.addr)
    marks = {}
    for (x, y), c in page.canvas.px.items():
        if x >= 280:
            marks[y // 32] = min(x, marks.get(y // 32, x))
    assert sorted(marks) == [0, 1]
    title_end = [r.box[0] + r.children[0].box[0] + r.children[0].box[2] for r in page.rows]  # screen x
    # the greyed row: its artist (ellipsized) and title end 12 px clear of the mark's ink
    assert 0 in artists and artists[0][1] + 12 <= marks[0]
    assert title_end[0] - 2 + 12 <= marks[0]  # hscroll_label's own margin after its text
    # the long title: no room for an artist, and its box ends where the artist's would have
    assert 1 not in artists and title_end[1] - 2 + 12 == marks[1]
    # the playable row runs on to the right column, past where the mark would stand
    assert artists[2][1] > marks[0]


def test_a_greyed_album_rows_second_line_ends_clear_of_the_offline_mark_too(fw, st):
    """An album list's two-line row nests its artist a level deeper than its title (the text's view, then
    view_artist, as stock builds it): a greyed row's artist ends where its title does, 12 px clear of the mark,
    not on stock's box running up to it."""
    page = Page(fw)
    artists = []
    for b in page.rows:
        b.children[0].move(10, 0, 320, 24)
        artist = page.ui.add('hscroll_label', page.ui.add('view', page.ui.add('view', b, (10, 24, 320, 24)),
                                                          (0, 0, 320, 24), 'view_artist'), (0, 0, 320, 24))
        artists.append(artist)
    url = C.create_string_buffer(b'/mnt/mmc/.subsonic/cat/al1/s1.flac')
    record = C.create_string_buffer(O['REC_SIZE'] + 8)
    struct.pack_into('<Q', record, O['REC_PATH'], C.addressof(url))
    struct.pack_into('<i', record, O['REC_TYPE'], 8)
    fw.mock('tk_str_start_with', stock_start_with)
    fw.fn('catalogue_mark', None, C.c_void_p, C.c_void_p, C.c_int)(page.rows[2].parent.addr, C.addressof(record), 0)
    page.paint()
    assert [a.box[2] for a in artists] == [320, 320, 272, 320]  # as the title: 30 + 272 = 322 - 10 - 12 + 2
    assert page.rows[2].children[0].box[2] == 272


@pytest.mark.parametrize('name, tab, greyed', [('albuminfo_page', None, True), ('playerjumpinfo_page', None, True),
                                               ('localmusic_page', None, False), ('search_dialog', 1, True),
                                               ('search_dialog', 0, False)])
def test_an_album_page_and_search_grey_their_songs_that_cannot_play(fw, st, low, tmp_path, name, tab, greyed):
    """An album's songs, and Search's songs, are a scroll_view's rows, one per record of the showlist (Search's:
    p_deque_searchshowlist, the lit tab's rows), not a pooled table: with a signed-in app's catalogue in the
    Library, offline, a server song's row is greyed as the page paints; another page's rows, or another tab's, are left alone.
    Each record's path is a 4-byte field as the Q2's (low), so the 8-byte read of its title after it finds none:
    untagged rows, which an album page's song rows (song_rows.c) leave unpainted."""
    from test_catalogue import Rig, song_row
    rig = Rig(fw, tmp_path)  # the app signed in, its catalogue imported: the server's songs in the Library
    rig.write(1, [song_row('s1', 'Streamed', 'B', 'A', 1, 100, 1)])
    rig.poll()
    page = Page(fw, name=name)
    if tab is not None:
        group = page.ui.add('view', page.view.parent, name='tab_button_group')
        page.ui.add('tab_button', group, value=tab)
        page.ui.add('tab_button', group, value=1 - tab)
    fw.mock('tk_str_start_with', stock_start_with)
    keep = []

    def record(path):
        r = C.create_string_buffer(O['REC_SIZE'] + 8)
        struct.pack_into('<I', r, O['REC_PATH'], low(path))
        struct.pack_into('<i', r, O['REC_TYPE'], 8)
        keep.append(r)
        return C.addressof(r)
    deques = Deques(fw)
    shown = deques.new([record(p) for p in (b'/mnt/mmc/a.flac', b'/mnt/mmc/b.flac',
                                            b'/mnt/mmc/.subsonic/cat/al1/s1.flac', b'/mnt/mmc/c.flac')])
    # Search's results are its own list; the page under it keeps the showlist
    fw.var('fw_ctx_p_deque_searchshowlist' if tab is not None else 'fw_ctx_p_deque_showlist', C.c_uint).value = shown
    if tab is not None:
        fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = deques.new([record(b'/mnt/mmc/x.flac')] * 4)
    page.paint()
    labels = [b.children[0] for b in page.rows]
    assert [text_ink(fw, st, label) == 0xff666666 for label in labels] == [False, False, greyed, False]


def test_about_s_rows_take_the_selection_but_open_nothing(fw, st):
    """About's rows (settings_pages.c about_rows, 0about): no longer disabled, so the wheel walks them and a cut-off
    value scrolls on the selected row; stock's row click (systemset_about_row_on_click, the serial number's QR code)
    gives way to one that does nothing, once however often the page lays out."""
    ui = oled(fw, st)
    view = ui.add('scroll_view', ui.window('about_page'), (0, 26, 375, 294))
    item = ui.add('list_item', view, (0, 0, 375, 32))
    button = ui.add('button', item, (20, 0, 335, 32), '6').clickable(ctx=0x77)
    struct.pack_into('<Q', button.items[-1], 12, 0x4c12f8)
    ui.add('hscroll_label', button, (10, 0, 160, 32), 'key')
    offs = []
    fw.mock('widget_off_by_func', lambda w, ev, fn, ctx, *_: offs.append((w, i32(ev), fn, ctx)))
    for _ in range(2):
        fw.fn('about_rows', None, C.c_void_p)(view.addr)
    assert offs == [(button.addr, EVT_CLICK, 0x4c12f8, 0x77)]
    assert len([h for h in button.handlers if h[0] == EVT_CLICK]) == 1
    assert not [a for _, a in fw.calls('widget_set_enable') if a[0] == item.addr]


# The menu sheet (menu_modal.c, docs/oled-design.md#menus): the page receding above it and the frosted material under
# its rows, drawn straight into the screen's buffer once its first paint is whole.
class Clock:
    """time_now_ms, and the timers the sheet's motion adds (menu_modal.c sheet_tick, and ui_kit.c's shared repaint
    through ovm_t), run by hand."""
    def __init__(self, fw):
        self.now, self.timers = 10_000, []
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('timer_add', lambda f, ctx, ms, *_: self.timers.append(f) or len(self.timers))

    def at(self, fw, ms):
        """ms after the motion's start: its frame taken (the tick), as the timer would."""
        self.now = 10_000 + ms
        return fw.fn('menu_modal__sheet_tick', C.c_int, C.c_void_p)(None)


def sheet(fw, st, rows=10, risen=True, reduce=False, page=None):
    """A sortselect dialog framed as the sheet on OLED's set, with a header, and the screen's buffer behind a canvas:
    the page white above y 160 and blue below, or page (375 x 320 0xAARRGGBB pixels). Its first whole paint (the material, the rise from the page) is made at
    the clock's 10 s; risen, the rise is run to its end and the buffer the page again. Returns (ui, win, view, canvas,
    buf); the clock is fw.sheet_clock. reduce: under Reduce Motion (IPOD REDUCEMOTION)."""
    st('settings_read').value = 1
    fw.mock('window_manager_is_animating', lambda *_: 0)
    ui = Ui(fw)
    theme(fw)
    st('reduce_motion').value = reduce  # the keys count as read (settings_read): the flag as accent() leaves it
    win = ui.window('sortselect_dialog', kind='dialog')
    lv = ui.add('list_view', win, (0, 44, 375, 210), 'list_view')
    view = ui.add('scroll_view', lv, (0, 0, 375, 210), 'scroll_view')
    for _ in range(rows):
        ui.add('list_item', view, (0, 0, 375, 32))
    fw.fn('modal_head', None, C.c_char_p, C.c_char_p, C.c_void_p)(b'Time', b'Pink Floyd', None)
    fw.fn('modal_frame', None, C.c_void_p, C.c_void_p)(win.addr, lv.addr)
    canvas = Canvas(fw)
    buf = (C.c_uint * (375 * 320))(*(page or [0xffffffff] * (375 * 160) + [0xff0000ff] * (375 * 160)))
    for off, v in ((O['LCD_W'], 375), (O['LCD_H'], 320), (O['LCD_LINE_LENGTH'], 4 * 375),
                   (O['LCD_FORMAT'], O['LCD_BGRA8888'])):
        C.c_int.from_buffer(canvas.lcd, off).value = v
    C.c_void_p.from_buffer(canvas.lcd, O['LCD_OFFLINE_FB']).value = C.addressof(buf)
    fw.sheet_clock = Clock(fw)
    if risen:
        page = list(buf)
        fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
        assert fw.sheet_clock.at(fw, 1000) == 0  # done: the timer ends
        buf[:] = page
    return ui, win, view, canvas, buf


class Rect(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('w', C.c_int), ('h', C.c_int)]


def rgb(buf, x, y):
    v = buf[y * 375 + x]
    return v >> 16 & 255, v >> 8 & 255, v & 255


def test_a_menu_sheet_frames_its_rows_under_its_header(fw, st):
    """The list from y 50 to the screen's foot, a 72 px header space heading the rows, each the screen's width; the
    content padded by what lies past the list end (y 282), which list_pad takes back so the last row ends there."""
    ui, win, view, canvas, buf = sheet(fw, st)
    head = view.children[0]
    assert head.name == '_mm_head' and head.box == (0, 0, 375, 72)
    assert [k.box[1] for k in view.children[1:3]] == [72, 104] and view.children[1].box[2] == 375
    assert view.props['virtual_h'] == 72 + 10 * 32 + 38 and view.props['_list_pad'] == 38


def test_a_menu_sheet_recedes_the_page_and_frosts_it(fw, st):
    """Above y 40 the page at 94% about the centre, dimmed half way; outside the sheet's 40 px top corners the same;
    on the sheet the page blurred, its colour raised 1.6 times, 28% kept under 6% white: blue stays blue, dark. The
    material is made once: a later paint over another page draws the same."""
    ui, win, view, canvas, buf = sheet(fw, st)
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    assert all(120 <= c <= 135 for c in rgb(buf, 187, 20))  # white, dimmed half way
    assert rgb(buf, 0, 20) == (0, 0, 0)  # past the receding page's edge: black
    assert all(120 <= c <= 135 for c in rgb(buf, 20, 41))  # outside the corner's curve
    assert all(60 <= c <= 100 for c in rgb(buf, 187, 41))  # the sheet, over white: grey, dark
    r, g, b = rgb(buf, 187, 300)
    assert 70 <= b <= 95 and r <= 20 and g <= 20  # over blue
    was = list(buf)
    for i in range(375 * 320):
        buf[i] = 0xff00ff00
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    assert rgb(buf, 187, 300) == tuple((was[300 * 375 + 187] >> s) & 255 for s in (16, 8, 0))


def test_a_menu_sheets_material_has_no_grain(fw, st):
    """The user's "No grain on frosted" (2026-11-20): over a page that is one colour across, a row of the sheet's
    material has no hashed +-3 levels in it, only the dither's fixed 4 px pattern of two neighbouring levels."""
    ui, win, view, canvas, buf = sheet(fw, st)
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    for y in (230, 250):
        row = [rgb(buf, x, y) for x in range(100, 276)]
        assert row[4:] == row[:-4], y  # the same every 4 px
        for p in range(3):
            assert max(c[p] for c in row) - min(c[p] for c in row) <= 1, (y, p)


def sheet_material(fw, st, page):
    """The sheet's material over page (375 x 320 (r, g, b)), as rows of (r, g, b)."""
    ui, win, view, canvas, buf = sheet(fw, st, page=[0xff000000 | r << 16 | g << 8 | b for r, g, b in page])
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    return [[rgb(buf, x, y) for x in range(375)] for y in range(320)]


def tile_means(m, y0, x0, x1, p):
    """Channel p's mean over each 4 x 4 tile of m from row y0, along x0 to x1: what the eye sees of a dither."""
    return [sum(m[y][x][p] for y in range(y0, y0 + 4) for x in range(tx, tx + 4)) / 16 for tx in range(x0, x1, 4)]


def test_a_menu_sheets_material_over_one_colour_keeps_its_level(fw, st):
    """Over a page of one colour, the material is that colour blurred (still itself), raised 1.6 times from its luma,
    28% kept under 6% white, worked out exactly: each 4 x 4 tile of it averages that to within an eighth of a level
    (the dither's 16ths and their rounding). Its steps kept whole levels, truncated, and drew it about 1.5 levels
    darker."""
    colour = (40, 90, 210)
    m = sheet_material(fw, st, [colour] * (375 * 320))
    luma = (77 * colour[0] + 150 * colour[1] + 29 * colour[2]) / 256
    for p in range(3):
        v = min(255.0, max(0.0, luma + (colour[p] - luma) * 410 / 256))
        want = v * 72 / 256 * 241 / 256 + 255 * 15 / 256
        for y0 in (160, 200, 236):  # clear of the page's black edges, which the blur reaches 60 px in from
            for got in tile_means(m, y0, 100, 276, p):
                assert abs(got - want) <= 1 / 8, (p, y0, got, want)


def test_a_menu_sheets_material_over_a_gradient_has_no_bands(fw, st):
    """A smooth page (red to blue across) darkened to 28% spans few levels, 18 of green across the screen: in whole
    levels alone it drew bands up to 52 px wide. Dithered, the 4 x 4 tiles' means step along it a 16th at a time:
    no 3 tiles running (12 px) average the same, and none is more than a level from the next."""
    a, b = (220, 70, 40), (40, 90, 210)
    row = [tuple(round(a[p] + (b[p] - a[p]) * x / 374) for p in range(3)) for x in range(375)]
    m = sheet_material(fw, st, row * 320)
    for p in range(3):
        for y0 in (160, 200, 236):
            means = tile_means(m, y0, 80, 296, p)
            longest = run = 1
            for u, v in zip(means, means[1:]):
                run = run + 1 if u == v else 1
                longest = max(longest, run)
                assert abs(u - v) <= 1, (p, y0)
            assert longest <= 2, (p, y0, longest)


def test_a_menu_sheet_waits_for_a_whole_paint_to_make_its_material(fw, st):
    """A first paint of part of the screen can't see the whole page: the flat sheet until a whole one."""
    ui, win, view, canvas, buf = sheet(fw, st, risen=False)
    canvas.clip = [0, 100, 375, 50]
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    assert rgb(buf, 187, 300) == (0, 0, 255)  # the buffer untouched: drawn as rects on the canvas instead
    assert canvas.rects


def test_a_menu_sheets_rows_fade_into_it_past_the_list_end(fw, st):
    """The rows' text past y 266 fades into the material, to nothing at the foot; above it untouched."""
    ui, win, view, canvas, buf = sheet(fw, st)
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    mat, foot = rgb(buf, 100, 300), rgb(buf, 100, 318)
    buf[200 * 375 + 100] = buf[300 * 375 + 100] = buf[318 * 375 + 100] = 0xffffffff
    fw.fn('modal_after', None, C.c_void_p, C.c_void_p)(view.addr, canvas.addr)
    assert rgb(buf, 100, 200) == (255, 255, 255)
    faded = rgb(buf, 100, 300)
    assert all(m < f < 255 for m, f in zip(mat, faded)) and faded[0] - mat[0] < 80, (mat, faded)
    assert all(f - 1 <= c <= f + 1 for c, f in zip(rgb(buf, 100, 318), foot))  # the material's, at the foot


def test_a_destructive_rows_words_are_red_and_white_on_its_bar(fw, st):
    """modal_red: the label's ink the confirm's red at rest, white while lit, set only when it changes."""
    ui, win, view, canvas, buf = sheet(fw, st, rows=1)
    row = view.children[1]
    label = ui.add('hscroll_label', row, (24, 0, 300, 32))
    fw.fn('modal_red', None, C.c_void_p)(row.addr)
    assert fw.fn('modal_is_red', C.c_int, C.c_void_p)(row.addr)
    paint = fw.fn('modal_row_paint', None, C.c_void_p, C.c_void_p, C.c_void_p, Rect, C.c_int)
    paint(view.addr, canvas.addr, row.addr, Rect(0, 0, 375, 32), 0)
    red = label.props['style:normal:text_color']
    paint(view.addr, canvas.addr, row.addr, Rect(0, 0, 375, 32), 1)
    white = label.props['style:normal:text_color']
    assert red & 0xffffff == 0x303bff and white & 0xffffff == 0xffffff  # AWTK's colour: red in the low byte


def test_a_closed_sheet_leaves_nothing_on_the_screen(fw, st):
    """What the sheet drew straight into the screen's buffer goes when the last sheet closes: cleared to black and
    the whole screen painted again on the next pass, so a page that paints nothing at the status row (Edit Home)
    never shows the receding page or the sheet's top."""
    ui, win, view, canvas, buf = sheet(fw, st)
    timers = []
    fw.mock('timer_add', lambda f, ctx, ms, *_: timers.append(f) or 1)
    fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)(win.addr, canvas.addr)
    assert rgb(buf, 187, 30) != (0, 0, 0)
    fw.fn('menu_modal__modal_gone', C.c_int, C.c_void_p, C.c_void_p)(win.addr, None)
    assert all(v == 0xff000000 for v in buf) and timers


# The sheet's motion (menu_modal.c sheet_run through ui_kit.h's ovm_t, docs/oled-design.md#menus): it rises 260 ms (cubic_out) from the
# screen's foot while the page recedes and dims and the frost fades in; Return lets it fall 180 ms (ease-in), keys
# taken meanwhile, then it closes.
def list_y(ui, view):
    return ui.nodes[view.addr].parent.box[1]


def test_a_menu_sheet_rises_from_the_page(fw, st):
    """Its first whole paint is the page alone, the rows a sheet's height (280 px) below their place; half way through
    the rise (130 ms: cubic_out's 87.5%) the sheet's top is 35 px low, the page above it dimmed part way, the rows 35 px
    low; at 260 ms it stands, its rows in place, the timer done, the page half dimmed and the frost under y 40."""
    ui, win, view, canvas, buf = sheet(fw, st, risen=False)
    page = list(buf)
    clock = fw.sheet_clock
    paint = fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)
    assert list_y(ui, view) == 50 + 280
    paint(win.addr, canvas.addr)
    assert list(buf) == page and len(clock.timers) == 2  # the sheet's tick and ui_kit.c's shared repaint
    assert clock.at(fw, 130) == 8  # RET_REPEAT
    assert list_y(ui, view) == 50 + 35
    buf[:] = page
    paint(win.addr, canvas.addr)
    assert all(130 < c < 255 for c in rgb(buf, 187, 20))  # white, dimmed part way (half way at rest)
    assert rgb(buf, 187, 70) != rgb(buf, 187, 80)  # the sheet's top edge between them: page above, frost under
    assert clock.at(fw, 260) == 0 and list_y(ui, view) == 50
    buf[:] = page
    paint(win.addr, canvas.addr)
    assert all(120 <= c <= 135 for c in rgb(buf, 187, 20)) and all(60 <= c <= 100 for c in rgb(buf, 187, 41))


def test_return_lets_a_menu_sheet_fall_then_closes_it(fw, st):
    """modal_close: the sheet stays open while it falls, every key taken (modal_closing); at 90 ms (ease-in: a quarter
    gone) its rows are 70 px low; at 180 ms it closes, and the next close is at once."""
    ui, win, view, canvas, buf = sheet(fw, st)
    clock = fw.sheet_clock
    fw.fn('modal_close', None, C.c_void_p)(win.addr)
    assert not fw.calls('window_close') and fw.fn('modal_closing', C.c_int)() == 1
    assert clock.at(fw, 1000 + 90) == 8 and list_y(ui, view) == 50 + 70
    assert clock.at(fw, 1000 + 180) == 0
    assert [a[0] for _, a in fw.calls('window_close')] == [win.addr]


def test_under_reduce_motion_a_menu_sheet_fades_in_and_out_in_place(fw, st):
    """ovm_t under Reduce Motion (docs/oled-design.md#overlay-motion): the rows stand in their place throughout; the
    rise is a 150 ms linear fade, the page above the rows half way back at 75 ms, and the fall a 120 ms one, the
    window closed at its end."""
    ui, win, view, canvas, buf = sheet(fw, st, risen=False, reduce=True)
    page = list(buf)
    clock = fw.sheet_clock
    paint = fw.fn('modal_paint', None, C.c_void_p, C.c_void_p)
    assert list_y(ui, view) == 50
    paint(win.addr, canvas.addr)
    assert clock.at(fw, 75) == 8 and list_y(ui, view) == 50
    buf[:] = page
    paint(win.addr, canvas.addr)
    assert all(180 <= c <= 200 for c in rgb(buf, 187, 20))  # white, half way to its dimmed half
    assert clock.at(fw, 150) == 0 and list_y(ui, view) == 50
    fw.fn('modal_close', None, C.c_void_p)(win.addr)
    assert clock.at(fw, 150 + 60) == 8 and list_y(ui, view) == 50 and not fw.calls('window_close')
    assert clock.at(fw, 150 + 120) == 0
    assert [a[0] for _, a in fw.calls('window_close')] == [win.addr]


def test_a_sheet_closes_at_once_before_its_first_whole_paint(fw, st):
    """With no material yet (nothing drawn of it) there is nothing to fall: it closes at once."""
    ui, win, view, canvas, buf = sheet(fw, st, risen=False)
    fw.fn('modal_close', None, C.c_void_p)(win.addr)
    assert [a[0] for _, a in fw.calls('window_close')] == [win.addr] and fw.fn('modal_closing', C.c_int)() == 0


@pytest.mark.parametrize('key, value, key_w', [
    ('Wi-Fi Address', 'Not connected', 13 * 7),                 # both fit: the value right-aligned in the rest
    ('Wi-Fi Address', 'AA:BB:CC:DD:EE:FF:00:11:22:33:44:55', 13 * 7),  # the value scrolls in the rest
    ('K' * 60, 'Q2', 321 - 16 - 2 * 7),                         # a short value keeps its width, the key scrolls
    ('K' * 60, 'V' * 60, 321 - 16 - 96),                        # both over: the value keeps KV_VALUE_MIN
])
def test_a_label_value_row_keeps_the_gap_the_label_s_width_and_marquees_what_overflows(fw, st, key, value, key_w):
    """docs/oled-design.md#labelvalue-rows (About's Wi-Fi Address, 2026-11-16): the label at its own width, at least
    KV_GAP (16) px to the value, the value the rest; a value too wide scrolls in its box (ellipses off, so the
    marquee fades its cut end) rather than crowding or cutting hard; a label too wide shrinks and scrolls, the value
    keeping min(its width, KV_VALUE_MIN). The row's text spans 321 px: x 10 to 335, less the labels' 2 px margins."""
    ui = oled(fw, st)
    fw.mock('widget_get_canvas', lambda *_: 0x40)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: float(7 * (n & 0xffffffff)))
    view = ui.add('scroll_view', ui.window('about_page'), (0, 26, 375, 294))
    button = ui.add('button', ui.add('list_item', view, (0, 0, 375, 32)), (20, 0, 335, 32), '6')
    k = ui.add('hscroll_label', button, (10, 0, 160, 32), key)
    v = ui.add('hscroll_label', button, (176, 0, 159, 32), value)
    ui.set_text(k, key), ui.set_text(v, value)  # their wide text, which the row measures
    fw.fn('about_rows', None, C.c_void_p)(view.addr)
    val_w = 321 - 16 - key_w
    assert k.box[2] - 4 == key_w and v.box[0] + v.box[2] == 335  # the value's right edge stays on the column
    assert v.box[2] - 4 == val_w and (v.box[0] + 2) - (k.box[0] + 2 + key_w) == 16
    ellipses = dict((a[0], a[1]) for _, a in fw.calls('hscroll_label_set_ellipses'))
    assert ellipses[k.addr] == (key_w >= 7 * len(key)) and ellipses[v.addr] == (val_w >= 7 * len(value))


@pytest.mark.parametrize('prompt, selected', [('Scan music?', 1), ('Import Playlists?', 1), ('Shut Down?', None),
                                              ('Delete Record?', None)])
def test_a_neutral_stock_confirm_opens_on_its_verb_the_rest_on_cancel(fw, st, prompt, selected):
    """The card's rule for the default (docs/oled-design.md#confirmation-card): Scan, Import, Export, Reconnect and
    Turn On cost least by mistake, so their card opens on the verb; a destructive or consequential one stays on
    Cancel, stock's first stop (no selection written)."""
    theme(fw)
    ui = Ui(fw)
    confirm = Confirm(fw, ui, [prompt])

    def utf8(w, out, size, *_):
        words = ui.text(ui.nodes[w]).encode()[:size - 1] + b'\0'
        C.memmove(out, words, len(words))
        return 0
    fw.mock('widget_get_text_utf8', utf8)
    confirm.paint()
    assert confirm.win.props.get('_ringnav_index') == selected


def test_a_sheets_header_is_painted_where_its_space_stands(fw, st):
    """Up Next lists a shuffle's played rows above the playing song's header (upnext.c), so the header's space is not
    the rows' first: scrolled to it, the header paints at the list's top just as one at the head of the rows does
    unscrolled, and scrolled back to the start it is that many rows lower."""
    def title_y(above, scroll):
        ui, win, view, canvas, buf = sheet(fw, st, rows=0)  # its header's space made, the rows' only child
        space = view.children[0]
        rows = [ui.add('list_item', view, (0, 0, 375, 32)) for _ in range(above + 6)]
        view.children[:] = rows[:above] + [space] + rows[above:]
        fw.fn('modal_head', None, C.c_char_p, C.c_char_p, C.c_void_p)(b'Time', b'Pink Floyd', None)
        fw.fn('modal_frame', None, C.c_void_p, C.c_void_p)(win.addr, view.parent.addr)
        C.c_int.from_address(view.addr + O['SCROLL_Y']).value = C.c_int.from_address(space.addr + O['W_Y']).value if scroll else 0
        canvas.texts.clear()
        fw.fn('modal_after', None, C.c_void_p, C.c_void_p)(view.addr, canvas.addr)
        return [r[1] for t, r, _ in canvas.texts if t == 'Time']
    at_head = title_y(0, False)
    assert at_head and title_y(3, True) == at_head
    lower = title_y(1, False)
    assert lower and lower[0] > at_head[0]
