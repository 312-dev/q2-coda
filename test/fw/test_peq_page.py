"""peq_ui.c's EQ page end to end on the device path (docs/internals.md#3eq): opened through peq_page_init and
driven only through what it registers (rows' clicks, the edit's events, keyup, the tab bar's and sliders'
touches, Return, closing), eq_key and peq_paint, with ringnav's real list_toggle, row_into and center_press
between, and the EQ state on a card of fopen and friends. EQ per Output (eq_output) too. test_misc.py has
the page's rules a function at a time; test/peq.py the same flows through its own shim."""
import ctypes as C
import struct

import pytest

import host
from conftest import O, STRIP_SAVE, strip_return

KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_RETURN = O['KEY_CENTER'], O['KEY_PREV'], O['KEY_NEXT'], O['KEY_RETURN']
KEY_FWD_BTN, KEY_BACK_BTN = O['KEY_FWD_BTN'], O['KEY_BACK_BTN']
EVT_DESTROY, EVT_VALUE_CHANGED, EVT_CLICK, EVT_FOCUS, EVT_KEY_UP, EVT_IM_ACTION = (O[k] for k in (
    'EVT_DESTROY', 'EVT_VALUE_CHANGED', 'EVT_CLICK', 'EVT_FOCUS', 'EVT_KEY_UP', 'EVT_IM_ACTION'))
EVT_POINTER_DOWN, EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE = (O[k] for k in (
    'EVT_POINTER_DOWN', 'EVT_POINTER_MOVE_BEFORE', 'EVT_POINTER_UP_BEFORE'))
EVENT_KEY, EVENT_X, EVENT_Y, W_PARENT, IM_WIDGET, IM_KEYBOARD = (
    O['EVENT_KEY'], O['EVENT_X'], O['EVENT_Y'], O['W_PARENT'], O['IM_WIDGET'], 0x0c)
RET_REMOVE, RET_REPEAT = 7, 8
ACTIVE, SAVED, FOLDER, OUTPUTS = '/mnt/data/peq-active', '/mnt/data/peq-presets', '/mnt/mmc/EQ', '/mnt/data/peq-outputs'
EQ_OFF, EQ_3, EQ_PEQ = range(3)
HOME, BAND, PRESETS, SAVES, CONFIRM, DELETES, PICK, ADJUST, STOCKS, RENAMES, NAMING, TYPES = range(12)
PK, LS, HS, LP, HP, BP, NO, AP = range(8)
TYPE_NAMES = ['Peaking', 'Low shelf', 'High shelf', 'Low pass', 'High pass', 'Band pass', 'Notch', 'All pass']
SIMPLE, ADVANCED = range(2)
E3_TABS, E3_SWITCH, E3_PRESETS, E3_RESET, E3_LOW, E3_MID, E3_HIGH = range(7)
GRAPH_X, GRAPH_W, PLOT_TOP, PLOT_BOTTOM, GRAPH_DB = 16, 343, 6, 78, 15
WHITE = 3 * 255  # bright() of white
ROW_PX = 24  # OLED's row font size (assets/themes/oled/theme.xml row title_px)
MUTED, TRACK = 0x858585, 0x333333  # its scheme's muted (theme.xml) and tools/theme.py TOKENS track
EQ_ROWS_Y, EQ_ROW, EQ_TOP, EQ_BOTTOM, EQ_LINE = 48, 32, 150, 186, 4  # peq.h EQ_*, slider_page.c EQ_LINE
BANDS = 30
r = C.byref


class Band(C.Structure):
    _fields_ = [('enabled', C.c_int), ('type', C.c_int), ('frequency', C.c_double), ('gain', C.c_double), ('q', C.c_double)]


class Preset(C.Structure):
    _fields_ = [('count', C.c_int), ('bypass', C.c_int), ('preamp', C.c_double), ('bands', Band * BANDS),
                ('balance', C.c_double), ('automatic', C.c_int), ('spare', C.c_int)]


class EqState(C.Structure):
    _fields_ = [('mode', C.c_int), ('hold', C.c_int), ('peq', Preset), ('eq3', Preset)]


class Ui(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view edit graph bar'.split()), ('timer', C.c_uint), ('keyboard_timer', C.c_uint),
                *((k, C.c_int) for k in 'screen band step count previous rendered dirty pick adjust tries capacity file_page tab tabs from_simple'.split()),
                ('kept', (C.c_int * 2) * 2), ('typed', C.c_double), ('draft', Preset), ('candidate', Preset), ('named', Preset), ('names', C.c_void_p),
                ('destination', C.c_char * 600), ('status', C.c_char * 160), ('name', C.c_char * 256), ('applied', C.c_char * 256),
                ('curve', (C.c_float * GRAPH_W) * 2)]


class E3(C.Structure):
    _fields_ = [('view', C.c_void_p), ('save', C.c_uint), ('press', C.c_uint), ('press_at', C.c_uint),
                *((k, C.c_int) for k in 'focus adjusting dragging dirty on h'.split()), ('gain', C.c_double * 3), ('status', C.c_char_p),
                ('caption', C.c_int)]


class Dirent(C.Structure):
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def i32(v):
    return C.c_int(v).value


class Card:
    """/mnt as a dict: fopen and friends, access, unlink and rename, and a folder's files by opendir."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.dirs, self.fail_writes = dict(files or {}), {}, {}, False
        self.folders = {p.rpartition('/')[0] for p in self.files}
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fread', self.fread)
        fw.mock('fw_fwrite', self.fwrite)
        fw.mock('fw_fclose', self.fclose)
        fw.mock('fw_rename', lambda old, new, *_: self.files.__setitem__(C.string_at(new).decode(), self.files.pop(C.string_at(old).decode())))
        fw.mock('fw_unlink', lambda path, *_: 0 if self.files.pop(C.string_at(path).decode(), None) is not None else -1)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files else -1)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', lambda d, *_: next(self.dirs[d], 0))
        fw.mock('fw_mkdir', lambda path, *_: self.folders.add(C.string_at(path).decode()))

    def fopen(self, path, mode, *_):
        path, mode = C.string_at(path).decode(), C.string_at(mode).decode()
        if 'w' in mode and self.fail_writes or 'r' in mode and path not in self.files:
            return 0
        self.folders.add(path.rpartition('/')[0])
        handle = 0x2000 + len(self.open) + len(self.files)
        while handle in self.open:
            handle += 1
        self.open[handle] = [path, 0, b'' if 'w' in mode else None]
        return handle

    def fread(self, out, size, count, f, *_):
        path, at, _ = self.open[f]
        data = self.files[path][at:at + size * count]
        C.memmove(out, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, data, size, count, f, *_):
        self.open[f][2] += C.string_at(data, size * count)
        return count

    def fclose(self, f, *_):
        path, _, written = self.open.pop(f)
        if written is not None:
            self.files[path] = written
        return 0

    def opendir(self, path, *_):
        folder = C.string_at(path).decode() + '/'
        names = sorted({p[len(folder):] for p in self.files if p.startswith(folder) and '/' not in p[len(folder):]}, reverse=True)
        if folder[:-1] not in self.folders:
            return 0
        entries = [Dirent(0, 0, 0, 8, n.encode()) for n in names]  # newest first, as FAT gives no order
        handle = 0x3000 + len(self.dirs)
        self.dirs[handle] = iter([C.addressof(e) for e in entries])
        self.dirs[handle, 'keep'] = entries
        return handle

    def state(self, path=ACTIVE):
        assert self.files[path][:8] == b'Q2PEQ03\0'
        return EqState.from_buffer_copy(self.files[path], 8)


def apo(fw, p):
    """p as a preset on the card (peq_export)."""
    out = C.create_string_buffer(8192)
    n = fw.fn('peq_export', C.c_uint, C.c_void_p, C.c_void_p, C.c_uint)(r(p), out, 8192)
    assert n
    return out.raw[:n]


def default(fw):
    p = Preset()
    fw.fn('peq_default', None, C.c_void_p)(r(p))
    return p


def state(fw, mode=EQ_PEQ, hold=0, gains=(0, 0, 0), peq=None):
    s = EqState(mode, hold)
    s.peq = peq if peq is not None else default(fw)
    s.peq.bypass = mode != EQ_PEQ
    fw.fn('eq3_bands', None, C.c_void_p, C.c_void_p)(r(s.eq3), (C.c_double * 3)(*gains))
    return s


def saved(s):
    return b'Q2PEQ03\0' + bytes(s)


def peak(fw, gain=6.0, frequency=1000.0, enabled=1, count=1):
    p = default(fw)
    p.count, p.bypass, p.automatic = count, 0, 1
    p.bands[0] = Band(enabled, 0, frequency, gain, 1.0)
    return p


class Page:
    """The EQ page in an AWTK of zeroed widgets (x, y, w, h and parent where stock keeps them), their texts, int
    props, names and images; its timers, run as the next loop pass would; the input method and its keyboard; the
    toasts it opens."""
    def __init__(self, fw, card, values=None, now=10000):
        self.fw, self.card, self.now = fw, card, now
        self.blocks, self.parent, self.kids, self.alive, self.kind, self.on = {}, {}, {}, {}, {}, {}
        self.text, self.props, self.names, self.images, self.timers, self.removed = {}, {}, {}, {}, [], set()
        self.requests, self.focused, self.selected, self.pages = [], [], [], []
        for name in ('list_item_create', 'label_create', 'list_view_create', 'scroll_view_create', 'image_create', 'hscroll_label_create'):
            fw.mock(name, lambda parent, x, y, w, h, *_: self.new(parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(parent, x, y, w, 0, C.string_at(kind).decode()))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_get_text', self.wide)
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), i32(v)))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self.props.get((w, C.string_at(k).decode()), i32(d)))
        fw.mock('widget_set_name', lambda w, s, *_: self.names.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_lookup', self.lookup)
        fw.mock('widget_count_children', lambda w, *_: len(self.children(w)))
        fw.mock('widget_get_child', lambda w, i, *_: self.children(w)[i])
        fw.mock('widget_destroy_children', self.destroy_children)
        fw.mock('widget_resize', lambda w, width, h, *_: self.props.__setitem__((w, 'resized'), i32(h)))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.on.setdefault((w, i32(evt)), []).append((fn, ctx)) or len(self.on))
        fw.mock('scroll_view_set_offset', lambda w, x, y, *_: self.props.__setitem__((w, 'offset'), i32(y)))
        fw.mock('widget_set_focused', lambda w, on, *_: self.focused.append((w, i32(on))))
        fw.mock('edit_set_select', lambda w, a, b, *_: self.selected.append((w, i32(a), i32(b))))
        fw.mock('pages_set_active_by_name', lambda w, name, *_: self.pages.append((w, C.string_at(name).decode())))
        fw.mock('image_base_set_image', lambda w, s, *_: self.images.__setitem__(w, C.string_at(s).decode()))
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, i32(ms))) or len(self.timers))
        fw.mock('timer_remove', lambda timer, *_: self.removed.add(timer))
        fw.mock('time_now_ms', lambda *_: self.now)
        self.im, self.keyboard, self.panel = C.create_string_buffer(0x20), 0, 0
        fw.mock('input_method', lambda *_: C.addressof(self.im))
        fw.mock('input_method_request', self.request)
        self.values, self.config = dict(values or {}), []
        fw.mock('toolsReadConfig', self.read_config)
        self.said = []  # the toasts' texts as they open (navigation.c toast: the text 8 bytes into its info)
        fw.mock('navigator_to_with_context', lambda name, info, *_: self.said.append(C.string_at(info + 8).decode())
                if C.string_at(name) == b'dialog/msginfo_dialog' else None)
        fw.mock('write_int_config', lambda v, s, k, *_: self.config.append((C.string_at(s).decode(), C.string_at(k).decode(), i32(v))))
        self.page = self.new(0, 0, 0, 375, 320)
        self.props[(self.page, 'h')] = 320  # the whole screen, the status row over it (docs/oled-design.md#full-bleed)
        data = card.files.get(ACTIVE, b'')
        on = data[:8] == b'Q2PEQ03\0' and EqState.from_buffer_copy(data, 8).mode != EQ_OFF
        fw.var('g_equalizer_flag', C.c_ubyte).value = on  # as peq_stock_eq left it at boot
        self.ui, self.e3 = fw.var('peq_ui__ui', Ui), fw.var('peq_ui__e3', E3)

    # AWTK
    def new(self, parent, x, y, w, h, kind=''):
        block = C.create_string_buffer(0x200)
        struct.pack_into('<4i', block, 0, i32(x), i32(y), i32(w), i32(h))
        struct.pack_into('<Q', block, W_PARENT, parent)
        p = C.addressof(block)
        self.blocks[p], self.parent[p], self.kind[p], self.alive[p] = block, parent, kind, True
        self.kids.setdefault(parent, []).append(p)
        return p

    def children(self, w):
        return self.kids.get(w, [])

    def destroy_children(self, w, *_):
        for c in self.kids.pop(w, []):
            self.destroy_children(c)
            del self.alive[c]

    def get_text(self, w, out, n, *_):
        s = self.text.get(w, '').encode()
        C.memmove(out, s[:n - 1] + b'\0', min(len(s), n - 1) + 1)
        return 0

    def wide(self, w, *_):
        buffer = (C.c_uint * (len(self.text.get(w, '')) + 1))(*map(ord, self.text.get(w, '')))
        self.blocks[C.addressof(buffer)] = buffer
        return C.addressof(buffer)

    def lookup(self, w, name, *_):
        name = C.string_at(name).decode()
        if w == self.keyboard and name == 'panel':
            return self.panel
        return next((c for c in self.children(w) if self.names.get(c) == name), 0)

    def request(self, im, edit, *_):
        self.requests.append(edit)
        struct.pack_into('<QQ', self.im, IM_WIDGET, edit, self.keyboard if edit else 0)

    def keyboard_opens(self):
        """T9's window, a while after the edit asked for it."""
        self.keyboard, self.panel = self.new(0, 0, 0, 375, 290), self.new(0, 0, 0, 375, 290)
        struct.pack_into('<Q', self.im, IM_KEYBOARD, self.keyboard)

    def read_config(self, path, section, key, out, default, *_):
        v = self.values.get((C.string_at(section).decode(), C.string_at(key).decode()))
        if v is not None:
            C.memmove(out, v.encode() + b'\0', len(v) + 1)
        return 0

    # the page's own
    def fn(self, name):
        return C.cast(getattr(self.fw.lib, 'peq_ui__' + name), C.c_void_p).value

    def pending(self, name):
        return [k for k, (f, _) in enumerate(self.timers, 1) if f == self.fn(name) and k not in self.removed]

    def run(self, name):
        """The newest timer running name, as the loop runs it: kept while it answers RET_REPEAT."""
        timer = self.pending(name)[-1]
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(self.timers[timer - 1][0])(None)
        if result != RET_REPEAT:
            self.removed.add(timer)
        return result

    def settle(self):
        """The render a click or key asked for, if it did."""
        if self.pending('render'):
            self.run('render')

    def handlers(self, w, event):
        return self.on.get((w, event), [])

    def handler(self, w, event):
        fn, ctx = self.handlers(w, event)[-1]
        return lambda e=None: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, e)

    def open(self):
        assert self.fw.fn('peq_page_init', C.c_int, C.c_void_p, C.c_void_p)(self.page, 0) == 0
        return self

    def key(self, key):
        return self.fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)(self.page, key)

    def centre(self):
        """A Centre press a while after the last: a single press, its action when its timer runs."""
        self.now += 1000
        return self.key(KEY_CENTER)

    def ret(self):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, KEY_RETURN)
        assert self.handler(self.page, EVT_KEY_UP)(C.addressof(event)) == 11  # RET_STOP: stock's keyup never sees it
        self.settle()

    def touch(self, w, kind, x, y):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, kind)
        struct.pack_into('<ii', event, EVENT_X, x, y)
        target = w if kind == EVT_POINTER_DOWN else self.page
        return self.handler(target, kind)(C.addressof(event))

    def bar(self):
        """The title label, the newest 48-high label straight on the page."""
        return [w for w in self.children(self.page) if self.kind[w] == '' and struct.unpack_from('<i', self.blocks[w], 12)[0] == 48][-1]

    def title(self):
        return self.text.get(self.bar(), '')

    def tab(self, x):
        """A tap on the tab bar at x, and the render it asks for."""
        self.touch(self.bar(), EVT_POINTER_DOWN, x, 20)
        self.settle()

    def advanced(self):
        self.open().tab(300)
        assert self.ui.tab == ADVANCED and self.ui.screen == HOME
        return self

    # the rows
    def labels(self, item):
        out = []
        for c in self.children(item):
            if self.images.get(c) in ('switch_on', 'switch_off'):
                out.append('On' if self.images[c] == 'switch_on' else 'Off')
            elif self.text.get(c):
                out.append(self.text[c])
        return out

    def rows(self):
        """The list's rows in order: (caption and value joined by a space, the item)."""
        return [(' '.join(self.labels(item)), item) for item in self.alive if self.handlers(item, EVT_CLICK)]

    def captions(self):
        return [text for text, _ in self.rows()]

    def chevron(self, item):
        return any(self.images.get(c) == 'list_into' for c in self.children(item))

    def click(self, text):
        (item,) = [item for t, item in self.rows() if t.startswith(text)][:1]
        self.handler(item, EVT_CLICK)()
        self.settle()

    def edit(self):
        return [w for w in self.alive if self.kind[w] == 'edit'][-1]

    def row_y(self, text):
        item = [item for t, item in self.rows() if t.startswith(text)][0]
        return struct.unpack_from('<i', self.blocks[item], 4)[0]


class Canvas:
    """A canvas on the screen's LCD: the rectangles filled (colour, x, y, w, h), the texts drawn (text, px, colour, rect),
    and the vgcanvas's calls by fw.calls."""
    def __init__(self, fw, vg=0x7a00, fill=0xff102030):
        self.raw, self.lcd = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, 0xbc, fill)
        self.p, self.fill, self.rects, self.texts, self.color, self.px, self.lcd_fill = C.addressof(self.raw), 0, [], [], 0, 0, fill
        fw.mock('canvas_set_fill_color', lambda c, color, *_: setattr(self, 'fill', color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.rects.append((self.fill, i32(x), i32(y), i32(w), i32(h))))
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'color', color & 0xffffffff))
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'px', i32(px)))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, rect, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), self.px, self.color, tuple((C.c_int * 4).from_address(rect)))))
        fw.mock('canvas_get_vgcanvas', lambda *_: vg)

    def text(self, s):
        return [t for t in self.texts if t[0] == s]


def paint(fw, w, canvas):
    fw.fn('peq_paint', None, C.c_void_p, C.c_void_p)(w, canvas.p)


def rgb(color):
    """color_t (r, g, b, a bytes) as 0xRRGGBB."""
    return (color & 255) << 16 | (color & 0xff00) | (color >> 16 & 255)


def bright(color):
    """r + g + b of a color_t or an 0xRRGGBB, either way round: the lighter of two, the larger."""
    return sum(color >> s & 255 for s in (0, 8, 16))


def grey(color):
    return len({color >> s & 255 for s in (0, 8, 16)}) == 1


def holds(outer, inner):
    """Whether the rect (x, y, w, h) outer covers inner."""
    x, y, w, h = outer
    return x <= inner[0] and inner[0] + inner[2] <= x + w and y <= inner[1] and inner[1] + inner[3] <= y + h


def tone(fw):
    return fw.fn('accent_tone', C.c_uint, C.c_int)(2) & 0xffffff  # TONE: the accent's light tone


@pytest.fixture(scope='module')
def scratch(library):
    """navigation.c's scratch_t's field offsets as this build lays it out."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


def page(fw, s=None, files=None, values=None):
    card = Card(fw, {ACTIVE: saved(s if s is not None else state(fw)), **(files or {})})
    return Page(fw, card, values)


# Opening, and the tabs.

def test_opening_changes_nothing_and_shows_simple_s_sliders(fw):
    p = page(fw, state(fw, EQ_3, gains=(3, 0, -2)))
    before = dict(p.card.files)
    p.open()
    assert p.card.files == before and not p.config and not fw.calls('stock_eq_trampoline')
    assert p.ui.tab == SIMPLE and p.e3.view and p.kind[p.e3.view] == 'view' and p.e3.on == 1
    assert list(p.e3.gain) == [3, 0, -2] and p.e3.focus == E3_LOW
    assert {evt for w, evt in p.on if w == p.page} == {
        EVT_DESTROY, EVT_KEY_UP, EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE}


def bar_foot(fw):
    """The status row's foot, the screen row a full-bleed page's content starts on (theme_engine.c bar_foot)."""
    names = host.tools_theme.GEOM_VALS
    return fw.var('theme_engine__geom_vals', C.c_short * len(names))[names.index('bar')]


@pytest.mark.parametrize('screen', ('Simple', 'Advanced', 'Frequency', 'Rename'))
def test_every_screen_starts_under_the_status_row(fw, screen):
    """The page's window takes the whole screen with the status row painted over its top (docs/oled-design.md#full-bleed):
    the tab bar, the list and an edit over the title all start at the row's foot, never under its glyphs."""
    files = {f'{FOLDER}/Rock.txt': b'x'}
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6)), files)
    if screen == 'Simple':
        p.open()
    else:
        p.advanced()
    if screen == 'Frequency':
        p.click('1  ')
        p.click('Frequency')
    elif screen == 'Rename':
        p.click('Presets')
        p.click('Rename a preset')
        p.click('Rock.txt')
    foot = bar_foot(fw)
    assert foot > 0, 'the geometry set has no status row'
    top = lambda w: struct.unpack_from('<i', p.blocks[w], 4)[0]
    tops = [(p.kind[w] or 'label', top(w)) for w in p.children(p.page)]
    assert tops and all(y >= foot for _, y in tops), tops
    if screen in ('Frequency', 'Rename'):
        assert top(p.edit()) == foot + 2  # in the title's place, clear of T9's top


def test_no_page_is_refused(fw):
    assert fw.fn('peq_page_init', C.c_int, C.c_void_p, C.c_void_p)(0, 0) == 16


def test_the_bar_s_halves_are_the_tabs(fw):
    p = page(fw).open()
    p.tab(300)
    assert p.ui.tab == ADVANCED and p.captions()[:2] == ['Nothing to apply', 'Reset to defaults']
    p.tab(100)
    assert p.ui.tab == SIMPLE and p.e3.view and not p.rows()
    p.tab(100)  # the open tab: nothing to do
    assert p.ui.tab == SIMPLE and not p.pending('render')


def test_the_wheel_reaches_advanced_s_tab_bar_past_its_first_row(fw):
    p = page(fw).advanced()
    view = p.ui.view
    assert p.ui.tabs == 1 and p.props[(view, '_ringnav_index')] == -1  # a tab opened from the bar keeps the wheel there
    assert p.key(KEY_NEXT) == 1 and p.ui.tabs == 0 and p.props[(view, '_ringnav_index')] == 0  # past the last tab: its rows
    assert p.key(KEY_NEXT) == 0 and p.key(KEY_CENTER) == 0  # the list's own
    assert p.key(KEY_FWD_BTN) == 0 and p.key(KEY_BACK_BTN) == 0  # off the bar: stock's track skip
    p.props[(view, '_ringnav_index')] = 1
    assert p.key(KEY_PREV) == 0  # not past its first row
    p.props[(view, '_ringnav_index')] = 0
    assert p.key(KEY_PREV) == 1 and p.ui.tabs == 1 and p.props[(view, '_ringnav_index')] == -1
    assert p.centre() == 1 and p.ui.tabs == 1  # a single press waits out a double's
    assert p.run('e3_single') == RET_REMOVE and p.ui.tab == ADVANCED  # Centre on the bar: into the tab's rows
    assert p.ui.tabs == 0 and p.props[(view, '_ringnav_index')] == 0 and not p.pending('render')


def test_the_wheel_moves_between_the_tabs_opening_each(fw):
    """Up from Simple's first row to its tab bar; the wheel then opens Advanced as it reaches it, its rows past it, and
    back up the bar to Simple, whose own rows Centre enters; a turn up on Simple, the first tab, stays."""
    p = page(fw).open()
    for _ in range(4):
        p.key(KEY_PREV)
    assert p.e3.focus == E3_TABS and p.ui.tab == SIMPLE
    assert p.key(KEY_PREV) == 1 and p.e3.focus == E3_TABS and p.ui.tab == SIMPLE and not p.pending('render')
    assert p.key(KEY_NEXT) == 1 and p.ui.tab == ADVANCED and p.ui.tabs == 1  # opened at once, the wheel on the bar
    assert p.key(KEY_NEXT) == 1 and p.ui.tabs == 0  # a turn down before its render: its first row, once it renders
    p.settle()
    assert p.props[(p.ui.view, '_ringnav_index')] == 0
    assert p.key(KEY_PREV) == 1 and p.ui.tabs == 1  # up from it: the bar
    assert p.key(KEY_PREV) == 1 and p.ui.tab == SIMPLE and p.e3.focus == E3_TABS
    p.settle()
    p.centre()
    p.run('e3_single')
    assert p.ui.tab == SIMPLE and p.e3.focus == E3_SWITCH  # Centre: into Simple's rows


def test_previous_and_next_on_the_tab_bar_open_the_other_tab(fw):
    """As on Search's tabs: the buttons switch the tab while the wheel is on the bar, and skip the track elsewhere."""
    p = page(fw).open()
    assert p.e3.focus != E3_TABS and p.key(KEY_FWD_BTN) == 0
    while p.e3.focus != E3_TABS:
        p.key(KEY_PREV)
    assert p.key(KEY_FWD_BTN) == 1 and p.ui.tab == ADVANCED and p.ui.tabs == 1
    p.settle()
    assert p.key(KEY_FWD_BTN) == 1 and p.ui.tab == SIMPLE and p.e3.focus == E3_TABS
    p.settle()
    assert p.key(KEY_BACK_BTN) == 1 and p.ui.tab == ADVANCED


def test_a_double_centre_press_is_the_screen_s(fw):
    p = page(fw).open()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    p.centre()
    assert p.key(KEY_CENTER) == 2 and not p.pending('e3_single')  # the second within DOUBLE_CLICK_MS
    (_, (top, event, *_)), = fw.calls('on_wm_keyup_fun')
    assert top == 0 and C.c_uint.from_address(event + EVENT_KEY).value == KEY_CENTER
    assert p.e3.focus == E3_LOW and not p.e3.adjusting  # nothing taken


def test_keys_on_another_page_or_none_are_not_the_eq_s(fw):
    p = page(fw)
    assert p.key(KEY_NEXT) == 0  # not open
    p.open()
    assert fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)(p.page + 1, KEY_NEXT) == 0 and p.key(KEY_RETURN) == 0


# Advanced's main screen.

def test_advanced_s_rows_under_the_curve(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    s.peq.balance = -1.5
    p = page(fw, s).advanced()
    rh, x = fw.fn('page_h', C.c_int, C.c_int)(48), fw.fn('page_x', C.c_int)()
    # a +6 dB peak: Auto cuts 6 dB at either rate; the other nine of the ten default bands are off
    assert p.captions()[:9] == ['Nothing to apply', 'Reset to defaults', 'Enabled On', 'EQ per Output Off', 'Balance L 1.5 dB',
                                'Preamp Auto (-6.0 dB)', 'Presets', '1  Peaking 1000 Hz +6.0 dB', '2  Peaking 63 Hz Off']
    assert len(p.rows()) == 7 + 10 and p.captions()[-1] == '10  Peaking 16000 Hz Off'
    canvas = Canvas(fw)
    paint(fw, p.ui.graph, canvas)
    drawn = struct.unpack_from('<i', p.blocks[p.ui.graph], 4)[0] + max(t[3][1] + t[3][3] for t in canvas.texts)  # its scale, lowest
    head = p.row_y('Nothing')
    assert head % rh == 0 and drawn <= head < drawn + rh  # the rows under the curve, from the first whole row clear of it
    assert [p.row_y(t) for t in ('Nothing', 'Reset', '1  ')] == [head, head + rh, head + 7 * rh]
    assert p.props[(p.ui.view, 'virtual_h')] == head + 17 * rh
    rows = dict((t.split()[0], item) for t, item in p.rows())
    assert [p.chevron(rows[k]) for k in ('Reset', 'Enabled', 'Balance', 'Preamp', 'Presets', '1')] == [False, False, True, True, True, True]
    caption = lambda k: [c for c in p.children(rows[k]) if p.text.get(c, '').startswith(k + '  ')][0]
    dim = p.props.get((caption('2'), 'style:normal:text_color'))
    assert dim is not None and bright(dim) < WHITE  # an off band is dim; the on one keeps the row's white
    assert p.props.get((caption('1'), 'style:normal:text_color')) is None
    assert all(struct.unpack_from('<i', p.blocks[c], 0)[0] == x for c in p.children(rows['Reset']))
    assert p.title() == '' and p.ui.bar == p.bar()  # the tabs' bar


def test_nothing_to_apply_is_display_only_until_an_edit(fw):
    p = page(fw).advanced()
    before = dict(p.card.files)
    p.click('Nothing to apply')
    assert p.ui.screen == HOME and p.card.files == before
    p.click('1  ')
    p.click('Gain')
    p.click('+6.0 dB')
    p.ret()
    assert p.captions()[0] == 'Apply changes'
    p.click('Apply changes')
    assert p.said == ['Applied'] and p.title() == ''  # a toast; the bar keeps the tabs
    assert p.card.state().peq.bands[0].gain == 6 and p.card.state().mode == EQ_PEQ
    assert p.captions()[0] == 'Nothing to apply'


def headroom(fw, p):
    return fw.fn('peq_headroom', C.c_double, C.c_void_p)(r(p))


def test_auto_is_one_cut_for_every_rate_at_the_row_s_size(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 12, 15000))
    p = page(fw, s).advanced()
    cut = headroom(fw, s.peq)
    assert f'Preamp Auto ({cut:.1f} dB)' in p.captions() and cut < fw.fn('peq_auto', C.c_double, C.c_void_p, C.c_int)(r(s.peq), 48000)
    preamp = [item for t, item in p.rows() if t.startswith('Preamp')][0]
    value = [c for c in p.children(preamp) if p.text.get(c, '').startswith('Auto')][0]
    caption = [c for c in p.children(preamp) if p.text.get(c) == 'Preamp'][0]
    size = lambda w: p.props.get((w, 'style:normal:font_size'))
    assert size(value) == size(caption) == ROW_PX  # the row's own size, not fit's 20


def test_applying_auto_saves_the_cut_the_player_plays(fw):
    p = page(fw).advanced()
    p.click('1  ')
    p.click('Gain')
    p.click('+12.0 dB')
    p.ret()
    p.click('Apply changes')
    saved = p.card.state().peq
    assert saved.automatic == 1 and saved.preamp == headroom(fw, saved) < -11


@pytest.mark.parametrize('automatic', (0, 1))
def test_boot_turns_another_version_s_auto_into_the_cut_for_every_rate(fw, automatic):
    s = state(fw, EQ_PEQ, peq=peak(fw, 12, 15000))
    s.peq.automatic, s.peq.preamp = automatic, -1  # a 48 kHz figure, or 1.3.5's never-picked preamp
    card = Card(fw, {ACTIVE: saved(s)})
    fw.fn('peq_boot', None)()
    assert card.state().peq.automatic == 1 and card.state().peq.preamp == headroom(fw, s.peq)
    before = dict(card.files)
    fw.fn('peq_boot', None)()
    assert card.files == before  # once right, left alone


def test_the_switch_puts_another_version_s_auto_right_before_it_plays(fw):
    s = state(fw, EQ_OFF, peq=peak(fw, 12, 15000))
    s.peq.automatic, s.peq.preamp = 1, -1
    p = page(fw, s).advanced()
    p.click('Enabled Off')
    assert p.card.state().mode == EQ_PEQ and p.card.state().peq.preamp == headroom(fw, s.peq)


def test_a_preset_s_name_and_a_picked_preamp_show_as_set(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 3))
    s.peq.automatic, s.peq.preamp = 2, -4.5
    files = {f'{FOLDER}/Rock.txt': apo(fw, s.peq)}
    p = page(fw, s, files).advanced()
    p.click('Presets')
    p.click('Rock.txt')
    assert 'Presets Rock' in p.captions() and 'Preamp -4.5 dB' in p.captions()
    assert p.said == ['Preset loaded; choose Apply to activate'] and p.title() == ''


def test_a_long_preset_name_ellipsises_right_aligned_clear_of_its_caption(fw):
    cut, focus, style = [], [], {}
    fw.mock('hscroll_label_set_ellipses', lambda w, on, *_: cut.append((w, i32(on))))
    fw.mock('hscroll_label_set_only_focus', lambda w, on, *_: focus.append((w, i32(on))))
    fw.mock('widget_use_style', lambda w, name, *_: style.__setitem__(w, C.string_at(name).decode()))
    s = state(fw, EQ_PEQ, peq=peak(fw, 3))
    name = 'LETSHUOER DEFIANT-to-iBASSO Exemplar'
    p = page(fw, s, {f'{FOLDER}/{name}.txt': apo(fw, s.peq)}).advanced()
    p.click('Presets')
    p.click(f'{name}.txt')
    row = [item for t, item in p.rows() if t.startswith('Presets')][0]
    value = [c for c in p.children(row) if p.text.get(c) == name][0]
    x, _, w, _ = struct.unpack_from('<4i', p.blocks[value], 0)
    assert style[value] == 's_scrlabel_white20r' and p.props[(value, 'style:normal:margin_right')] == 0
    assert (value, 1) in cut and (value, 1) in focus  # without only_focus stock scrolls it from its first paint
    assert x > 118 and x + w == 316  # past the widest caption at 27 px; ends where the other values do


# The curve.

def grid(canvas):
    return [(rgb(c), x, y, w, h) for c, x, y, w, h in canvas.rects]


def test_the_graph_s_grid_and_scale(fw):
    p = page(fw).advanced()
    canvas = Canvas(fw)
    paint(fw, p.ui.graph, canvas)
    rows = [g for g in grid(canvas) if g[3] == GRAPH_W and g[4] == 1]
    ys = [y for _, _, y, _, _ in rows]
    zero = (PLOT_TOP + PLOT_BOTTOM) // 2
    assert ys[0] == zero and len(set(ys)) == len(ys) == 5  # 0 dB first
    d = sorted(abs(y - zero) for y in ys[1:])
    assert d[1] - d[0] <= 1 and d[3] - d[2] <= 1 and abs(d[2] - 2 * d[0]) <= 1  # +-6 and +-12 dB, evenly
    assert all(PLOT_TOP < y < PLOT_BOTTOM for y in ys)
    assert sum(rows[0][0].to_bytes(3, 'big')) > sum(rows[1][0].to_bytes(3, 'big'))  # 0 dB the lighter line
    ticks = [x for _, x, y, w, h in grid(canvas) if w == 1 and (y, h) == (PLOT_TOP, PLOT_BOTTOM - PLOT_TOP)]
    assert len(ticks) == 3 and abs((ticks[1] - ticks[0]) - (ticks[2] - ticks[1])) <= 1  # a decade apart each
    assert [(t[0], t[3][0] + t[3][2] // 2) for t in canvas.texts] == [('100', ticks[0]), ('1k', ticks[1]), ('10k', ticks[2])]
    assert all(grey(t[2]) and bright(t[2]) < WHITE and t[3][1] > PLOT_BOTTOM for t in canvas.texts)  # grey, under the plot
    assert canvas.fill == canvas.lcd_fill  # the LCD's own fill back


def test_the_response_fills_toward_0_db_and_strokes_once(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    canvas = Canvas(fw)
    paint(fw, p.ui.graph, canvas)
    gradients = [a for _, a in fw.calls('vgcanvas_set_fill_linear_gradient')]
    assert len(gradients) == 2 and all(rgb(a[1]) == rgb(a[2]) == tone(fw) and a[1] >> 24 > 0 == a[2] >> 24 for a in gradients)
    assert [a[1] >> 24 for _, a in fw.calls('vgcanvas_set_stroke_color')][:1] == [0xff]
    assert len(fw.calls('vgcanvas_stroke')) == 2  # the line, and the one band's ring
    assert len(fw.calls('vgcanvas_save')) == len(fw.calls('vgcanvas_restore')) == 1


def test_channels_that_differ_draw_the_other_dimmer(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    s.peq.bands[0].enabled = 2  # left only
    p = page(fw, s).advanced()
    paint(fw, p.ui.graph, Canvas(fw))
    right, left = [a[1] for _, a in fw.calls('vgcanvas_set_stroke_color')][:2]
    assert 0 < right >> 24 < left >> 24 and rgb(right) == rgb(left)  # the right under the left, fainter
    assert len(fw.calls('vgcanvas_stroke')) == 3  # two lines, and the band's ring


def test_bypassed_is_the_line_alone_in_grey(fw):
    p = page(fw, state(fw, EQ_OFF, peq=peak(fw, 6))).advanced()
    paint(fw, p.ui.graph, Canvas(fw))
    assert not fw.calls('vgcanvas_set_fill_linear_gradient')
    line = rgb(fw.calls('vgcanvas_set_stroke_color')[0][1][1])
    assert grey(line) and line != tone(fw)


def test_rings_mark_the_bands_in_use_and_the_edited_one(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6, count=3))
    s.peq.bands[1] = Band(0, 0, 200, 0, 1)
    s.peq.bands[2] = Band(1, 1, 100, 3, .7)
    p = page(fw, s).advanced()
    paint(fw, p.ui.graph, Canvas(fw))
    assert len(fw.calls('vgcanvas_arc')) == 2  # the off band has none
    fills = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_fill_color')]
    assert fills == [0, 0]  # hollow
    p.click('2  ')  # the off band, being edited
    assert p.ui.screen == BAND and p.title() == 'Band 2'
    before = len(fw.calls('vgcanvas_arc'))
    paint(fw, p.ui.graph, Canvas(fw))
    assert len(fw.calls('vgcanvas_arc')) - before == 3
    fills = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_fill_color')][2:]
    strokes = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_stroke_color')][-3:]
    assert fills == [0, tone(fw), 0] and strokes == [tone(fw), 0xffffff, tone(fw)]  # filled, white-ringed


def test_another_widget_or_no_screen_paints_nothing(fw):
    p = page(fw).advanced()
    canvas = Canvas(fw)
    paint(fw, p.ui.view, canvas)
    paint(fw, 0, canvas)
    struct.pack_into('<Q', canvas.raw, 0x38, 0)
    paint(fw, p.ui.graph, canvas)
    assert not canvas.rects and not canvas.texts


def test_no_vgcanvas_leaves_the_grid(fw):
    p = page(fw).advanced()
    canvas = Canvas(fw, vg=0)
    paint(fw, p.ui.graph, canvas)
    assert len(canvas.rects) == 8 and not fw.calls('vgcanvas_save')


# A band, its value menu and the pickers.

def test_a_band_s_screen(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    assert p.captions() == ['Band On', 'Channels Both', 'Type Peaking', 'Frequency 1000 Hz', 'Gain +6.0 dB', 'Q 1.00']
    assert p.title() == 'Band 1' and p.ui.graph and p.ui.bar is None
    p.click('Channels')
    p.click('Type')
    assert p.ui.screen == TYPES and p.captions() == TYPE_NAMES and p.title() == 'Band 1 Type'
    p.click('Low shelf')
    assert p.ui.screen == BAND and 'Channels Left' in p.captions() and 'Type Low shelf' in p.captions()
    p.click('Band')
    assert p.captions()[:2] == ['Band Off', 'Type Low shelf']  # an off band has no channels
    p.ret()
    assert p.ui.screen == HOME and '1  Low shelf 1000 Hz Off' in p.captions()


def test_a_band_past_the_count_joins_the_preset(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('2  ')
    assert p.ui.draft.count == 2


def adjust(fw, what='Frequency'):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click(what)
    return p


def test_the_frequency_menu_and_its_edit(fw):
    p = adjust(fw)
    assert p.ui.screen == ADJUST and p.captions() == ['Band 1 Frequency (Hz)', 'Raise 1 Hz', 'Lower 1 Hz', 'Step 1 Hz']
    edit = p.edit()
    assert p.parent[edit] == p.page and p.text[edit] == '1000' and p.ui.edit == edit
    assert p.handlers(edit, EVT_FOCUS) and not p.handlers(edit, EVT_IM_ACTION)
    p.click('Step')
    p.click('Raise')
    assert p.ui.draft.bands[0].frequency == 1010 and p.text[p.edit()] == '1010' and 'Step 10 Hz' in p.captions()


def test_a_typed_number_is_taken_and_anything_else_shows_the_value_again(fw):
    p = adjust(fw)
    p.text[p.edit()] = '440'
    p.handler(p.edit(), EVT_VALUE_CHANGED)()
    p.settle()
    assert p.ui.draft.bands[0].frequency == 440 and p.ui.dirty and p.text[p.edit()] == '440'
    p.text[p.edit()] = '4x0'
    p.handler(p.edit(), EVT_VALUE_CHANGED)()
    assert p.pending('render') and p.ui.draft.bands[0].frequency == 440
    p.settle()
    assert p.text[p.edit()] == '440'


def test_a_value_change_from_a_replaced_edit_is_ignored(fw):
    p = adjust(fw)
    stale = p.handler(p.edit(), EVT_VALUE_CHANGED)
    p.ui.edit = 0  # render let it go
    p.text[p.edit()] = '440'
    assert stale() == 0 and p.ui.draft.bands[0].frequency == 1000 and not p.pending('render')


def test_q_s_menu_has_no_step(fw):
    p = adjust(fw, 'Q')
    assert p.captions() == ['Band 1 Q', 'Raise 0.05', 'Lower 0.05'] and p.text[p.edit()] == '1.00'


def test_the_value_row_opens_t9_s_numbers_with_the_value_selected(fw):
    p = adjust(fw)
    p.click('Band 1 Frequency')
    assert p.focused[-1] == (p.edit(), 1) and not p.pending('render')  # a render would close it
    assert struct.unpack_from('<Q', p.im, IM_WIDGET)[0] == p.edit()
    assert p.run('numbers') == RET_REPEAT  # the keyboard isn't up yet
    p.keyboard_opens()
    assert p.run('numbers') == RET_REMOVE and p.ui.keyboard_timer == 0
    assert p.pages == [(p.panel, 'symnum')]
    assert p.selected[-1] == (p.edit(), 0, 4)  # "1000": the first key replaces it


def test_a_keyboard_that_never_opens_is_given_up_after_twenty_looks(fw):
    p = adjust(fw)
    p.handler(p.edit(), EVT_FOCUS)()
    results = [p.run('numbers') for _ in range(20)]
    assert results == [RET_REPEAT] * 19 + [RET_REMOVE] and not p.pages


def test_another_edit_s_keyboard_is_not_this_one_s(fw):
    p = adjust(fw)
    p.handler(p.edit(), EVT_FOCUS)()
    p.keyboard_opens()
    struct.pack_into('<Q', p.im, IM_WIDGET, p.edit() + 1)
    assert p.run('numbers') == RET_REPEAT and not p.pages


def test_a_render_lets_t9_go_before_replacing_its_edit(fw):
    p = adjust(fw)
    p.handler(p.edit(), EVT_FOCUS)()
    old = p.edit()
    p.click('Raise')
    requests = p.requests
    assert requests[-1] == 0 and p.edit() != old and p.ui.keyboard_timer == 0


@pytest.mark.parametrize('pick', (None, 'Balance'))
def test_a_scrolling_list_runs_on_to_the_page_s_foot(fw, pick):
    """Under OLED's grid Advanced's list and a picker's run from under the tab bar on to the page's foot,
    so their rows past the list end peek through its fade (navigation.c list_fade) as every list's do, rather than
    stopping on a whole row above it over a black band."""
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    if pick:
        p.click(pick)
    view = p.ui.view
    lv = p.parent[view]
    box = lambda w: struct.unpack_from('<4i', p.blocks[w], 0)
    foot, top = p.props[(p.page, 'h')], bar_foot(fw) + 48  # under the tab bar, itself under the status row
    assert box(lv)[1] == top and box(lv)[1] + box(lv)[3] == foot and box(view)[3] == foot - top
    assert (lv, 'resized') not in p.props  # it scrolls: never shrunk to its rows


@pytest.mark.parametrize('pick, value, title, rows, row', (('Balance', 0, 'Balance', 49, 24), ('Balance', -3, 'Balance', 49, 18),
                                                          ('Preamp', None, 'Preamp', 74, 0), ('Preamp', -3, 'Preamp', 74, 31)))
def test_a_picker_opens_centred_on_its_value(fw, pick, value, title, rows, row):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    if pick == 'Balance':
        s.peq.balance = value
    elif value is not None:
        s.peq.automatic, s.peq.preamp = 2, value
    p = page(fw, s).advanced()
    p.click(pick)
    rh = fw.fn('page_h', C.c_int, C.c_int)(48)
    height = (fw.fn('eq_list_end', C.c_int)() - 48) // rh * rh  # whole rows down to the set's list_end (render)
    assert p.title() == title and len(p.rows()) == rows and p.props[(p.ui.view, '_ringnav_count')] == rows
    assert p.props[(p.ui.view, '_ringnav_index')] == row and not p.ui.graph
    offset = p.props[(p.ui.view, 'offset')]
    assert offset == max(0, min(row * rh - (height - rh) // 2, rows * rh - height))
    if pick == 'Balance':
        assert p.captions()[0] == 'L 12.0 dB' and p.captions()[24] == 'Center' and p.captions()[-1] == 'R 12.0 dB'
    else:
        assert p.captions()[0].startswith('Auto (') and p.captions()[1] == '+12.0 dB' and p.captions()[-1] == '-24.0 dB'


def test_a_picked_preamp_at_its_top_opens_on_its_row_not_auto(fw):
    s = state(fw, EQ_PEQ, peq=peak(fw, 6))
    s.peq.automatic, s.peq.preamp = 2, 15  # past the picker's +12.0
    p = page(fw, s).advanced()
    p.click('Preamp')
    assert p.props[(p.ui.view, '_ringnav_index')] == 1


def test_the_gain_picker_is_the_band_s(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click('Gain')
    assert p.title() == 'Band 1 Gain' and len(p.rows()) == 97 and p.props[(p.ui.view, '_ringnav_index')] == 36
    p.ret()
    assert p.ui.screen == BAND


def test_a_kept_screen_keeps_its_selection_and_scroll(fw):
    p = page(fw).advanced()
    view = p.ui.view
    p.props[(view, '_ringnav_index')], p.props[(view, 'yoffset')] = 5, 96
    p.click('Reset to defaults')
    assert p.props[(p.ui.view, '_ringnav_index')] == 5 and p.props[(p.ui.view, 'offset')] == 96
    assert p.said == ['Reset; choose Apply to activate'] and p.title() == ''


def test_coming_back_from_a_picker_lands_on_the_row_that_opened_it(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    view = p.ui.view
    p.props[(view, '_ringnav_index')], p.props[(view, 'yoffset')] = 4, 48
    p.click('Balance')
    assert p.ui.screen == PICK and p.props[(p.ui.view, '_ringnav_index')] == 24
    p.click('R 0.5 dB')  # a pick, which changes the draft and goes back
    assert p.ui.screen == HOME and p.captions()[0] == 'Apply changes'
    assert p.props[(p.ui.view, '_ringnav_index')] == 4 and p.props[(p.ui.view, 'offset')] == 48
    assert p.props[(p.ui.view, '_ringnav_count')] == len(p.rows())  # so ringnav keeps that row (navigation.c load)
    p.click('Preamp')
    p.ret()  # Return comes back the same way
    assert p.ui.screen == HOME and p.props[(p.ui.view, '_ringnav_index')] == 4


def test_a_band_s_rows_are_kept_under_its_pickers_and_the_next_band_opens_at_its_top(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.props[(p.ui.view, '_ringnav_index')] = 7
    p.click('1  ')
    assert p.ui.screen == BAND and p.props[(p.ui.view, '_ringnav_index')] == 0
    p.props[(p.ui.view, '_ringnav_index')] = 4
    p.click('Gain')
    p.ret()
    assert p.ui.screen == BAND and p.props[(p.ui.view, '_ringnav_index')] == 4
    p.ret()
    assert p.ui.screen == HOME and p.props[(p.ui.view, '_ringnav_index')] == 7
    p.click('2  ')
    assert p.ui.screen == BAND and p.props[(p.ui.view, '_ringnav_index')] == 0


def test_a_tab_switch_forgets_the_kept_row(fw):
    p = page(fw).advanced()
    p.props[(p.ui.view, '_ringnav_index')] = 3
    p.click('Presets')
    assert p.ui.screen == PRESETS and p.ui.kept[0][0] == 4
    p.ret()
    assert p.props[(p.ui.view, '_ringnav_index')] == 3 and p.ui.kept[0][0] == 0  # taken once
    p.ui.kept[0][0] = 4
    p.tab(100)
    p.tab(300)
    assert p.ui.kept[0][0] == 0 and p.props[(p.ui.view, '_ringnav_index')] == -1  # the wheel on the tabs


# The presets' screens.

def test_the_presets_screen_lists_saved_presets_sorted(fw):
    p = page(fw, files={f'{FOLDER}/b.txt': b'x', f'{FOLDER}/a.txt': b'x', f'{FOLDER}/C.TXT': b'x', f'{FOLDER}/notes.md': b'x',
                        f'{SAVED}/old.peq': b'x'}).advanced()
    p.click('Presets')
    assert p.captions() == ['Stock presets', 'Save editor preset', 'Rename a preset', 'Delete a preset', 'C.TXT', 'a.txt', 'b.txt']
    assert p.title() == 'Presets' and not p.ui.graph and p.ui.bar is None
    rows = dict(p.rows())
    assert all(p.chevron(rows[t]) for t in p.captions()[:4]) and not p.chevron(rows['a.txt'])


def test_more_than_a_page_of_presets_pages(fw):
    files = {f'{FOLDER}/{k:03}.txt': b'x' for k in range(130)}
    p = page(fw, files=files).advanced()
    p.click('Presets')
    assert p.title() == 'Presets 1/2' and p.captions()[4:6] == ['Next page', '000.txt'] and p.captions()[-1] == '127.txt'
    assert len(p.rows()) == 4 + 1 + 128
    p.click('Next page')
    assert p.title() == 'Presets 2/2' and p.captions()[4:] == ['Previous page', '128.txt', '129.txt']
    p.click('Previous page')
    assert p.title() == 'Presets 1/2'


def test_a_page_the_card_no_longer_fills_is_its_last(fw):
    files = {f'{FOLDER}/{k:03}.txt': b'x' for k in range(130)}
    p = page(fw, files=files).advanced()
    p.click('Presets')
    p.click('Next page')
    del p.card.files[f'{FOLDER}/128.txt'], p.card.files[f'{FOLDER}/129.txt']  # gone from the card meanwhile
    p.click('128.txt')
    assert p.said == ['Line 1: cannot open file'] and p.ui.file_page == 0 and p.captions()[4] == '000.txt'
    assert p.title() == 'Presets'  # 128 left: one page (FILE_PAGE)
    assert p.props[(p.ui.view, 'offset')] == 0 and p.props[(p.ui.view, '_ringnav_index')] == 0


def eq_carded(fw):
    """Whether the confirmation card asked (it opens stock's sortselect_dialog)."""
    return any(C.string_at(a[0]) == b'dialog/sortselect_dialog' for _, a in fw.calls('navigator_to'))


def eq_answer(fw, row):
    fw.fn('peq_ui__eq_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, 0)


def test_saving_offers_ten_slots_and_asks_before_replacing(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('Presets')
    p.click('Save editor preset')
    assert p.captions() == [f'Save as Manual {k:02}' for k in range(1, 11)] and p.title() == 'Presets'
    p.click('Save as Manual 03')
    assert f'{FOLDER}/Manual 03.txt' in p.card.files and p.said == ['Preset saved; active EQ unchanged'] and p.title() == 'Presets'
    p.click('Save as Manual 03')  # the card asks over the slots (feedback.c confirm), CONFIRM the state
    assert p.ui.screen == CONFIRM and eq_carded(fw) and p.captions()[2] == 'Save as Manual 03'
    eq_answer(fw, 0)  # Cancel
    assert p.ui.screen == SAVES


def test_a_failed_save_says_so(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Save editor preset')
    p.card.fail_writes = True
    p.click('Save as Manual 01')
    assert p.said == ['Save failed; settings unchanged'] and p.title() == 'Presets' and f'{FOLDER}/Manual 01.txt' not in p.card.files


def test_deleting_asks_then_deletes(fw):
    p = page(fw, files={f'{FOLDER}/a.txt': b'x'}).advanced()
    p.click('Presets')
    p.click('Delete a preset')
    p.click('a.txt')  # the card asks over the presets, Cancel first (feedback.c confirm)
    assert p.ui.screen == CONFIRM and eq_carded(fw) and 'a.txt' in p.captions()
    eq_answer(fw, 1)  # Delete
    p.settle()
    assert f'{FOLDER}/a.txt' not in p.card.files and p.said == ['Deleted a.txt; active EQ unchanged']
    assert p.title() == 'No presets found'  # the list's own state, which its render puts in the title (list_files)
    p.ret()
    assert p.ui.screen == PRESETS


def test_a_failed_delete_says_so(fw):
    p = page(fw, files={f'{FOLDER}/a.txt': b'x'}).advanced()
    p.click('Presets')
    p.click('Delete a preset')
    p.click('a.txt')
    fw.mock('fw_unlink', lambda *_: -1)
    eq_answer(fw, 1)
    p.settle()
    assert p.said == ['Delete failed; preset kept'] and p.title() == 'Presets'


def test_the_stock_presets(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Stock presets')
    assert p.captions() == ['Pop', 'Rock', 'Dance', 'Blues', 'Metal', 'Vocal', 'Classical', 'Jazz']
    p.click('Jazz')
    assert p.ui.screen == HOME and 'Presets Jazz' in p.captions() and p.captions()[0] == 'Apply changes'


def test_a_preset_with_nothing_usable_says_which_line(fw):
    p = page(fw, files={f'{FOLDER}/x.txt': b'# notes\nGraphicEQ: 20 -3; 40 -2\n'}).advanced()
    p.click('Presets')
    assert p.captions()[4:] == ['x.txt'] and p.title() == 'Presets'
    p.click('x.txt')
    assert p.said == ['Use the ParametricEQ file, not GraphicEQ'] and p.title() == 'Presets'
    assert p.ui.screen == PRESETS and p.ui.draft.count == 10


@pytest.mark.parametrize('extra, said', ((b'Filter: ON IIR Fc 30 Hz\n', 'Loaded; skipped IIR filter'),
                                         (b'Device: all\nfilter: on iir fc 30hz\nCopy: L=R\n', 'Loaded; skipped Device, 2 more')))
def test_a_preset_loads_what_plays_and_says_what_it_left_out(fw, extra, said):
    p = page(fw, files={f'{FOLDER}/x.txt': b'Preamp: -3 dB\nFilter: ON PK Fc 1000 Hz Gain 2 dB Q 1\n' + extra}).advanced()
    p.click('Presets')
    p.click('x.txt')
    assert p.said == [said] and p.title() == '' and p.ui.screen == HOME and p.ui.draft.count == 1 and p.ui.draft.preamp == -3


def test_no_folder_on_the_card_says_why(fw):
    p = page(fw).advanced()
    p.click('Presets')
    assert p.captions()[4:] == [] and p.title() == 'Folder missing or unavailable'


MOUNTS = {'/proc/mounts': b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'}


def binary(p):
    return b'Q2PEQ02\0' + bytes(p)


def test_earlier_binary_presets_are_written_out_once_and_kept(fw):
    rock, imp, same = peak(fw, 3), peak(fw, 4), peak(fw, 5)
    rock.automatic, same.automatic = 2, 2
    imp.automatic, imp.preamp = 1, -4  # its file's preamp switched to Auto, which the file never held
    imported = Preset.from_buffer_copy(imp)
    imported.automatic, imported.preamp = 2, -1.5
    files = {**MOUNTS, f'{SAVED}/Rock.peq': binary(rock), f'{SAVED}/Imp.peq': binary(imp), f'{SAVED}/Same.peq': binary(same),
             f'{SAVED}/Apo.peq': binary(imp), f'{FOLDER}/Apo.txt': b'Device: all\n' + apo(fw, imported),
             f'{SAVED}/bad.peq': b'junk', f'{FOLDER}/Imp.txt': apo(fw, imported), f'{FOLDER}/Same.txt': b'# mine\n' + apo(fw, same)}
    p = page(fw, files=files).advanced()
    p.click('Presets')
    assert p.captions()[4:] == ['Apo.txt', 'Imp.txt', 'Rock.txt', 'Same.txt']
    assert p.card.files[f'{FOLDER}/Apo.txt'] == files[f'{FOLDER}/Apo.txt']  # a line APO needs: kept
    assert p.card.files[f'{FOLDER}/Rock.txt'] == apo(fw, rock) and p.card.files[f'{FOLDER}/Imp.txt'] == apo(fw, imp)
    assert p.card.files[f'{FOLDER}/Same.txt'].startswith(b'# mine\n')  # the same preset: the file stays as it is
    assert all(p.card.files[k] == v for k, v in files.items() if k.startswith(SAVED)) and f'{SAVED}/.written' in p.card.files
    del p.card.files[f'{FOLDER}/Rock.txt']
    p.ret()
    p.click('Presets')
    assert f'{FOLDER}/Rock.txt' not in p.card.files  # once


def test_binary_presets_wait_for_the_card(fw):
    p = page(fw, files={f'{SAVED}/Rock.peq': binary(peak(fw, 3))}).advanced()
    p.click('Presets')
    assert sorted(p.card.files) == [ACTIVE, f'{SAVED}/Rock.peq']
    p.card.files.update(MOUNTS)
    p.card.fail_writes = True
    p.ret()
    p.click('Presets')
    assert f'{SAVED}/.written' not in p.card.files  # a failed write is tried again
    p.card.fail_writes = False
    p.ret()
    p.click('Presets')
    assert f'{FOLDER}/Rock.txt' in p.card.files and f'{SAVED}/.written' in p.card.files


def test_renaming_opens_the_name_with_the_keyboard_up(fw):
    p = page(fw, files={f'{FOLDER}/Rock.txt': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.txt')
    edit = p.edit()
    assert p.ui.screen == NAMING and p.captions() == ['Rename'] and p.text[edit] == 'Rock' and p.title() == ''
    assert p.props[(edit, 'select_none_when_focused')] == 1 and p.focused[-1] == (edit, 1)
    assert p.handlers(edit, EVT_IM_ACTION) and not p.handlers(edit, EVT_FOCUS)
    p.text[edit] = 'Jazz'
    p.handler(edit, EVT_IM_ACTION)()
    p.handler(edit, EVT_VALUE_CHANGED)()  # Done sends both: renamed once
    p.settle()
    assert f'{FOLDER}/Jazz.txt' in p.card.files and p.ui.screen == PRESETS and p.said == ['Renamed to Jazz'] and p.title() == 'Presets'


def test_return_from_the_name_goes_back_to_the_list(fw):
    p = page(fw, files={f'{FOLDER}/Rock.txt': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.txt')
    p.ret()
    assert p.ui.screen == RENAMES and p.captions() == ['Rock.txt'] and p.card.files[f'{FOLDER}/Rock.txt'] == b'x'


def test_a_tap_on_the_row_renames_through_the_edit_s_blur_once(fw):
    p = page(fw, files={f'{FOLDER}/Rock.txt': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.txt')
    p.text[p.edit()] = 'Jazz'
    row = p.rows()[0][1]
    p.handler(p.edit(), EVT_VALUE_CHANGED)()  # the blur, as the touch leaves the edit
    p.handler(row, EVT_CLICK)()  # then the row's own click: renamed already
    p.settle()
    assert sorted(p.card.files) == [ACTIVE, f'{FOLDER}/Jazz.txt'] and p.ui.screen == PRESETS and p.said == ['Renamed to Jazz']


def test_return_steps_back_and_leaves_from_the_main_screen(fw):
    p = page(fw).advanced()
    p.click('Presets')
    p.click('Stock presets')
    p.ret()
    assert p.ui.screen == PRESETS
    p.ret()
    assert p.ui.screen == HOME and not fw.calls('navigator_back')
    p.ret()
    assert fw.calls('navigator_back')


def test_the_switch_and_per_output_rows(fw):
    p = page(fw, state(fw, EQ_OFF)).advanced()
    p.click('Enabled')
    assert p.card.state().mode == EQ_PEQ and 'Enabled On' in p.captions()
    p.click('EQ per Output')
    assert ('Q2POD', 'EQOUTPUT', 1) in p.config and 'EQ per Output On' in p.captions()


# Closing.

def test_closing_saves_simple_s_gains_and_ends_the_held_comparison(fw):
    p = page(fw, state(fw, EQ_OFF, hold=1, gains=(3, 0, 0))).open()
    p.e3.gain[1], p.e3.dirty = 2, 1  # a turn the save timer hasn't taken yet
    p.handler(p.page, EVT_DESTROY)()
    s = p.card.state()
    assert s.hold == 0 and list(s.eq3.bands[k].gain for k in range(3)) == [3, 2, 0] and fw.calls('stock_eq_trampoline')
    assert p.ui.page is None and p.ui.view is None and p.ui.names is None and p.e3.view is None
    assert p.key(KEY_NEXT) == 0  # nothing open


def test_closing_from_simple_s_presets_reopens_on_simple(fw):
    p = page(fw).open()
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    assert p.ui.tab == ADVANCED and p.ui.screen == STOCKS and p.ui.from_simple
    p.handler(p.page, EVT_DESTROY)()
    assert p.ui.tab == SIMPLE and not p.ui.from_simple


def test_closing_with_an_edit_up_lets_t9_go(fw):
    p = adjust(fw)
    p.handler(p.page, EVT_DESTROY)()
    assert p.requests[-1] == 0 and p.ui.edit is None


def test_closing_frees_the_preset_list(fw):
    p = page(fw, files={f'{FOLDER}/a.txt': b'x'}).advanced()
    p.click('Presets')
    assert p.ui.capacity and p.ui.names
    p.handler(p.page, EVT_DESTROY)()
    assert p.ui.capacity == 0 and p.ui.names is None and p.ui.file_page == 0


# Simple's tab.

def test_simple_s_presets_play_a_stock_preset_at_once_and_come_back(fw):
    p = page(fw, state(fw, EQ_3, gains=(2, 0, 0))).open()
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    stocks = p.captions()
    assert p.ui.screen == STOCKS and 'Rock' in stocks
    p.click('Rock')
    s = p.card.state()
    rock = list(fw.var('peq_ui__stock_gains', (C.c_byte * 10) * len(stocks))[stocks.index('Rock')])  # its row in stock's table
    assert s.mode == EQ_PEQ and not s.peq.bypass and [s.peq.bands[k].gain for k in range(10)] == rock
    assert [s.peq.bands[k].enabled for k in range(10)] == [int(g != 0) for g in rock]  # a band at 0 dB is off
    assert p.ui.tab == SIMPLE and p.e3.focus == E3_PRESETS and p.e3.status is None and p.e3.view
    assert p.ui.applied == b'Rock'


def test_back_from_simple_s_presets_changes_nothing(fw):
    p = page(fw, state(fw, EQ_3, gains=(2, 0, 0))).open()
    before = dict(p.card.files)
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    p.ret()
    assert p.ui.tab == SIMPLE and p.card.files == before and p.e3.focus == E3_PRESETS


def test_a_failed_apply_from_simple_s_presets_says_so_on_simple(fw):
    p = page(fw, state(fw, EQ_3)).open()
    p.e3.focus = E3_PRESETS
    p.centre()
    p.run('e3_single')
    p.settle()
    p.card.fail_writes = True
    p.click('Rock')
    assert p.ui.tab == SIMPLE and p.e3.status == b'Apply failed; active EQ unchanged'


def test_unsaved_gains_keep_simple_when_presets_can_t_save_them(fw):
    p = page(fw, state(fw, EQ_3)).open()
    p.e3.gain[0], p.e3.dirty, p.e3.focus = 4, 1, E3_PRESETS
    p.card.fail_writes = True
    p.centre()
    p.run('e3_single')
    assert p.ui.tab == SIMPLE and p.e3.dirty and not p.pending('render')
    p.tab(300)
    assert p.ui.tab == SIMPLE  # nor the tab bar


def test_the_save_timer_saves_a_turn(fw):
    p = page(fw, state(fw, EQ_3)).open()
    p.centre()
    p.run('e3_single')
    assert p.e3.adjusting
    p.key(KEY_NEXT), p.key(KEY_NEXT)
    assert p.card.state().eq3.bands[0].gain == 0 and len(p.pending('e3_tick')) == 1  # one save for the turns
    assert p.run('e3_tick') == RET_REMOVE
    assert p.card.state().eq3.bands[0].gain == 1 and not p.e3.dirty and fw.calls('stock_eq_trampoline')


def test_centre_or_return_ends_an_adjustment(fw):
    p = page(fw, state(fw, EQ_3)).open()
    for end in ('centre', 'return'):
        p.centre()
        p.run('e3_single')
        p.key(KEY_PREV)
        if end == 'centre':
            assert p.key(KEY_CENTER) == 1 and not p.pending('e3_single')
        else:
            p.ret()
        assert not p.e3.adjusting and p.e3.focus == E3_LOW and not fw.calls('navigator_back')
    assert p.card.state().eq3.bands[0].gain == -1
    p.ret()
    assert fw.calls('navigator_back')


def test_a_tap_on_reset_resets_the_sliders_and_a_drag_follows_the_finger(fw):
    p = page(fw, state(fw, EQ_3, gains=(3, -2, 4))).open()
    view, y, shown = p.e3.view, fw.fn('peq_ui__e3_y', C.c_int, C.c_double), e3_canvas(fw, p)
    centre = lambda text: (lambda left, top, w, h: (left + w // 2, top + h // 2))(*shown.text(text)[0][3])  # where it's drawn
    assert EQ_ROWS_Y + 2 * EQ_ROW <= centre('Reset')[1] < EQ_ROWS_Y + 3 * EQ_ROW  # the third row
    assert p.touch(view, EVT_POINTER_DOWN, *centre('Reset')) == 0 and p.e3.focus == E3_RESET
    assert list(p.e3.gain) == [0, 0, 0] and [p.card.state().eq3.bands[k].gain for k in range(3)] == [0, 0, 0]  # saved at once
    assert fw.calls('stock_eq_trampoline') and not p.e3.dirty  # and heard
    top, bottom, x = y(6), y(-6), centre('Mid')[0]  # the travel's ends: +-6 dB
    p.touch(view, EVT_POINTER_DOWN, x, (top + bottom) // 2)
    assert p.e3.focus == E3_MID and p.e3.dragging and p.e3.adjusting and p.e3.gain[1] == 0
    p.touch(view, EVT_POINTER_MOVE_BEFORE, x, bottom)
    assert p.e3.gain[1] == -6
    p.touch(view, EVT_POINTER_MOVE_BEFORE, x, top - 20)  # past the end
    assert p.e3.gain[1] == 6
    p.touch(view, EVT_POINTER_UP_BEFORE, x, top)
    assert not p.e3.dragging and p.card.state().eq3.bands[1].gain == 6
    assert p.touch(view, EVT_POINTER_MOVE_BEFORE, x, bottom) == 0 and p.e3.gain[1] == 6  # no finger down


# Simple's paint.

def e3_canvas(fw, p):
    canvas = Canvas(fw)
    paint(fw, p.e3.view, canvas)
    return canvas


def test_simple_paints_the_rows_the_sliders_and_their_values(fw):
    """slider_page.c eq_rows_paint: Enabled, Presets and Reset on EQ_ROW rows from EQ_ROWS_Y; each band a track from
    EQ_TOP to EQ_BOTTOM, a bar from 0 dB to its gain in the accent, and its name and gain centred under it."""
    p = page(fw, state(fw, EQ_3, gains=(1.5, 0, -6))).open()
    before = len(fw.calls('vgcanvas_set_fill_color'))
    c = e3_canvas(fw, p)
    accent = rgb(fw.calls('vgcanvas_set_fill_color')[before + 2][1][1])  # Low's ring, after the on switch's two
    assert not grey(accent)
    assert [t[0] for t in c.texts] == ['Enabled', 'Presets', 'Reset', 'Low', '+1.5 dB', 'Mid', '0 dB', 'High', '-6.0 dB']
    centre = lambda t: (t[3][0] + t[3][2] // 2, t[3][1] + t[3][3] // 2)
    assert [centre(t)[1] for t in c.texts[:3]] == [EQ_ROWS_Y + k * EQ_ROW + EQ_ROW // 2 for k in range(3)]
    assert [(t[1], rgb(t[2])) for t in c.texts[:3]] == [(ROW_PX, 0xffffff)] * 3
    xs = [62 + 125 * k for k in range(3)]  # the columns e3_touch reads
    assert [centre(t) for t in c.texts[3:]] == [(x, y) for x in xs for y in (200, 216)]  # EQ_NAME_Y, EQ_GAIN_Y
    # 0 dB at 168, 12 dB to the travel's 36 px: +1.5 dB 4.5 px up, 0 dB no bar, -6 dB down to the end
    assert grid(c) == [(TRACK, 60, EQ_TOP, EQ_LINE, 36), (accent, 60, 163, EQ_LINE, 5), (TRACK, 185, EQ_TOP, EQ_LINE, 36),
                       (TRACK, 310, EQ_TOP, EQ_LINE, 36), (accent, 310, 168, EQ_LINE, 18)]
    assert c.fill == c.lcd_fill
    # the wheel's band's name in Text Primary, the rest muted; a turn moves it along
    assert [(t[1], rgb(t[2])) for t in c.texts[3:]] == [(18, 0xffffff)] + [(18, MUTED)] * 5  # SP_ENDS_PX
    assert p.key(KEY_NEXT) == 1 and p.e3.focus == E3_MID
    assert [rgb(t[2]) for t in e3_canvas(fw, p).texts[3:]] == [MUTED, MUTED, 0xffffff, MUTED, MUTED, MUTED]


def test_simple_off_is_muted_and_an_adjusted_band_s_gain_lit(fw):
    """Off, eq_rows_paint's bars are the scheme's muted, not the accent; adjusting lights that band's gain alone."""
    p = page(fw, state(fw, EQ_OFF, gains=(3, -2, 4))).open()
    gains = lambda c: [rgb(t[2]) for t in c.texts if t[0].endswith('dB')]
    c = e3_canvas(fw, p)
    assert [g[0] for g in grid(c)] == [TRACK, MUTED] * 3 and gains(c) == [MUTED] * 3
    p.centre()
    p.run('e3_single')
    assert p.e3.adjusting and p.e3.focus == E3_LOW
    c = e3_canvas(fw, p)
    assert [g[0] for g in grid(c)] == [TRACK, MUTED] * 3  # adjusting doesn't turn it on
    assert gains(c) == [0xffffff, MUTED, MUTED]


@pytest.mark.parametrize('name, shown', (('', 'Advanced'), ('Rock', 'Rock'), ('Headphones', 'Headphones'),
                                         ('Headphones X', 'Headphones X'), ('Sennheiser HD 600', 'Sennheiser H'),
                                         ('Café Del Mar!', 'Café Del Ma'),  # 12 bytes, the e two of them
                                         ('Bass Boost €€', 'Bass Boost '),  # byte 12 inside the euro: all of it goes
                                         ('Party Mix \U0001f389', 'Party Mix '),
                                         ('€€€€€', '€€€€')))
def test_a_playing_peq_shows_its_preset_s_name_as_presets_value(fw, name, shown):
    """e3_paint hands eq_rows_paint the applied preset's name, 12 bytes at most cut on a UTF-8 boundary (Advanced
    unnamed), drawn as Presets' value at the row's size."""
    p = page(fw, state(fw, EQ_PEQ)).open()
    p.ui.applied = name.encode()
    c = e3_canvas(fw, p)
    assert [t[0] for t in c.texts[:4]] == ['Enabled', 'Presets', shown, 'Reset'] and len(shown.encode()) <= 12
    assert c.texts[2][1] == c.text('Presets')[0][1] == ROW_PX


def test_a_stray_continuation_byte_is_drawn_as_itself(fw):
    p = page(fw, state(fw, EQ_PEQ)).open()
    p.ui.applied = b'a\x80b\xf0\x9f\x8e\xb5'
    assert e3_canvas(fw, p).texts[2][0] == 'a\x80b\U0001f3b5'


def test_a_message_on_simple_has_the_tab_bar(fw):
    """tabs_paint: the segmented control, unless Simple's message has the bar (e3_show puts it in the bar's title)."""
    p = page(fw).open()
    c = Canvas(fw)
    paint(fw, p.ui.bar, c)
    assert [t[0] for t in c.texts] == ['Simple', 'Advanced'] and len(fw.calls('canvas_fill_rounded_rect')) == 2
    p.e3.status = b'Save failed; EQ unchanged'
    c = Canvas(fw)
    paint(fw, p.ui.bar, c)
    assert not c.texts and not c.rects and len(fw.calls('canvas_fill_rounded_rect')) == 2  # the track and segment only


def light_mode(fw, light):
    """Config reads as their defaults, LIGHT 1 (Light Mode) if light."""
    def config(path, section, key, out, default, *_):
        value = b'1' if light and C.string_at(key) == b'LIGHT' else C.string_at(default)
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', config)


def test_oled_s_tabs_are_a_segmented_control_whose_open_tab_turns_accent_only_with_the_wheel(fw):
    """docs/oled-design.md#tabs: a #1C1C1E track, the open tab a raised #3A3A3C segment, the accent while the wheel is on
    the bar; the open label white in Inter Medium 17 (21 as AWTK sizes it), the other #ABABAB."""
    light_mode(fw, False)
    assert fw.fn('eq_rows', C.c_int)()  # the theme read now: the page's card answers config reads after
    rounded = []
    def fill(c, r, _, color, radius, *__):
        rounded.append((tuple((C.c_int * 4).from_address(r)), rgb(C.c_uint.from_address(color).value), radius))
        return 0
    fw.mock('canvas_fill_rounded_rect', fill)
    p = page(fw).open()

    def bar():
        del rounded[:]
        c = Canvas(fw)
        paint(fw, p.ui.bar, c)
        return rounded[:], {t[0]: (rgb(t[2]), t[1]) for t in c.texts}

    track = ((20, 7, 335, 34), 0x1c1c1e, 9)
    shapes, labels = bar()  # Simple open, the wheel on its sliders
    assert p.e3.focus != E3_TABS and shapes == [track, ((22, 9, 165, 30), 0x3a3a3c, 8)]
    assert labels == {'Simple': (0xffffff, 21), 'Advanced': (0xababab, 21)}
    for _ in range(E3_LOW - E3_TABS):
        p.key(KEY_PREV)
    shapes, labels = bar()  # the wheel on the bar: the open tab's segment in the accent
    (_, accent, _), = shapes[1:]
    assert shapes == [track, ((22, 9, 165, 30), accent, 8)] and not grey(accent)
    assert labels == {'Simple': (0xffffff, 21), 'Advanced': (0xababab, 21)}
    p.tab(300)  # Advanced, the wheel still on the bar
    assert bar()[0] == [track, ((187, 9, 166, 30), accent, 8)]
    p.key(KEY_NEXT)  # down to its first row
    shapes, labels = bar()
    assert p.ui.tabs == 0 and shapes == [track, ((187, 9, 166, 30), 0x3a3a3c, 8)]
    assert labels == {'Simple': (0xababab, 21), 'Advanced': (0xffffff, 21)}

@pytest.mark.parametrize('light', [False, True])
def test_oled_s_knobs_are_white_and_edged_in_track_in_light_mode_alone(fw, light):
    """docs/oled-design.md Progress and sliders: a 14 px white knob, ringed in the accent on the band the wheel is on.
    White is Light Mode's canvas, so there a knob with no ring is edged in its Track, #D9D9D9."""
    light_mode(fw, light)
    assert fw.fn('eq_rows', C.c_int)() and fw.fn('ipod_light', C.c_int)() == light
    p = page(fw, state(fw, EQ_3, gains=(1.5, 0, -3))).open()  # the wheel on Low
    before = len(fw.calls('vgcanvas_set_fill_color'))
    e3_canvas(fw, p)
    fills = [rgb(a[1]) for _, a in fw.calls('vgcanvas_set_fill_color')[before + 2:]]  # after the on switch's two
    accent = fills[0]
    assert not grey(accent)
    bare = [0xd9d9d9, 0xffffff] if light else [0xffffff]
    assert fills == [accent, 0xffffff, *bare, *bare]  # Low ringed in the accent; Mid and High


@pytest.mark.parametrize('light', [False, True])
def test_oled_s_switch_stays_enabled_and_presets_shows_the_preset_playing(fw, light):
    """iOS's value row: the switch keeps its title while PEQ plays, and the preset's name is Presets'
    value, right-aligned short of its chevron in Text Secondary (#ABABAB, #5C5C5C in Light Mode)."""
    light_mode(fw, light)
    assert fw.fn('eq_rows', C.c_int)()
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw))).open()
    p.ui.applied = b'Rock'
    labels = [t[0] for t in e3_canvas(fw, p).texts]
    assert labels[:4] == ['Enabled', 'Presets', 'Rock', 'Reset']
    (_, px, color, (x, y, w, h)), = e3_canvas(fw, p).text('Rock')
    (_, row_px, _, (tx, ty, _, _)), = e3_canvas(fw, p).text('Presets')
    assert px == row_px and rgb(color) == (0x5c5c5c if light else 0xababab) and y == ty and x == tx
    (_, (_, _, chevron, *_)), = fw.calls('canvas_draw_icon')[-1:]  # its centre CHEV_SHIFT (5) left of the right column
    assert x + w == i32(chevron) + 5 - 1 - 21  # as a settings row's value: VALUE_CHEVRON_GAP short of the column
    for _ in range(E3_LOW - E3_PRESETS):
        p.key(KEY_PREV)
    assert p.e3.focus == E3_PRESETS and rgb(e3_canvas(fw, p).text('Rock')[0][2]) == 0xffffff  # on the selection
    p.ui.applied = b''  # unnamed: Advanced's
    assert [t[0] for t in e3_canvas(fw, p).texts][:3] == ['Enabled', 'Presets', 'Advanced']
    p.ui.draft.bypass = 1  # 3EQ or off: no preset plays, no value
    assert [t[0] for t in e3_canvas(fw, p).texts][:3] == ['Enabled', 'Presets', 'Reset']


# EQ per Output.

def owner(card):
    data = card.files.get(f'{OUTPUTS}/owner')
    return data[8:].rstrip(b'\0').decode() if data and data[:8] == b'Q2OWNER\0' else None


def test_each_output_keeps_its_own_eq(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(3, 0, 0)))})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(b'3.5mm')
    assert owner(card) == '3.5mm' and card.state().mode == EQ_3  # the first: keeps what plays
    output(b'usb')
    assert owner(card) == 'usb' and card.state(f'{OUTPUTS}/3.5mm').eq3.bands[0].gain == 3
    usb = state(fw, EQ_PEQ, peq=peak(fw, 6))
    card.files[ACTIVE] = saved(usb)
    output(b'3.5mm')
    assert card.state().mode == EQ_3 and card.state().eq3.bands[0].gain == 3 and fw.calls('stock_eq_trampoline')
    assert card.state(f'{OUTPUTS}/usb').mode == EQ_PEQ and owner(card) == '3.5mm'
    calls = len(fw.calls('fw_rename'))
    output(b'3.5mm')  # the same output: nothing
    assert len(fw.calls('fw_rename')) == calls


def test_off_to_off_leaves_the_filter_out(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_OFF)), f'{OUTPUTS}/usb': saved(state(fw, EQ_OFF, gains=(1, 0, 0)))})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(b'3.5mm')
    output(b'usb')
    assert card.state().eq3.bands[0].gain == 1 and not fw.calls('stock_eq_trampoline')


def test_a_new_output_s_eq_that_can_t_be_saved_keeps_the_old_owner(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/usb': saved(state(fw, EQ_PEQ, peq=peak(fw, 6)))})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(b'3.5mm')
    renames = len(fw.calls('fw_rename'))
    real = card.fopen
    fw.mock('fw_fopen', lambda path, mode, *_: 0 if C.string_at(path).startswith(ACTIVE.encode()) and b'w' in C.string_at(mode) else real(path, mode))
    output(b'usb')
    assert owner(card) == '3.5mm' and card.state().mode == EQ_3 and len(fw.calls('fw_rename')) == renames + 1  # only 3.5mm's slot


@pytest.mark.parametrize('kept', (31, 32))  # EQ_DEVICES (peq.h) less one, and all of them
def test_a_new_bluetooth_device_is_remembered_until_the_slots_are_full(fw, kept):
    macs = [f'00:11:22:33:44:{k:02X}' for k in range(kept)]
    gain = lambda k: k % 6 + 1  # each device's own Low
    files = {f'{OUTPUTS}/{m}': saved(state(fw, EQ_3, gains=(gain(k), 0, 0))) for k, m in enumerate(macs)}
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_PEQ, peq=peak(fw, 6))), **files})
    output = fw.fn('eq_output', None, C.c_char_p)
    output(macs[0].encode())
    output(b'AA:BB:CC:DD:EE:FF')  # a new one: it plays what played, the first's
    assert card.state().mode == EQ_3 and card.state().eq3.bands[0].gain == gain(0)
    output(b'usb')
    slot = f'{OUTPUTS}/AA:BB:CC:DD:EE:FF'
    assert (slot in card.files) == (kept < 32) and owner(card) == 'usb'
    if slot in card.files:  # what it played, filed under it
        assert card.state(slot).mode == EQ_3 and card.state(slot).eq3.bands[0].gain == gain(0)
    assert all(card.state(f'{OUTPUTS}/{m}').eq3.bands[0].gain == gain(k) for k, m in enumerate(macs))  # the others' kept


def test_turning_per_output_off_files_the_eq_and_forgets_the_owner(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(2, 0, 0)))})
    fw.fn('eq_output', None, C.c_char_p)(b'4.4mm')
    card.files[ACTIVE] = saved(state(fw, EQ_3, gains=(5, 0, 0)))
    fw.fn('eq_output_forget', None)()
    assert card.state(f'{OUTPUTS}/4.4mm').eq3.bands[0].gain == 5 and owner(card) is None
    assert card.state().eq3.bands[0].gain == 5  # what plays stays


def test_an_owner_read_back_after_a_restart_files_the_right_slot(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(4, 0, 0))), f'{OUTPUTS}/owner': b'Q2OWNER\0' + b'usb'.ljust(20, b'\0')})
    fw.fn('eq_output_forget', None)()
    assert card.state(f'{OUTPUTS}/usb').eq3.bands[0].gain == 4


@pytest.mark.parametrize('data', (b'Q2OWNER\0' + b'u' * 20, b'Q2OWNEX\0' + b'usb'.ljust(20, b'\0'), b'Q2OWNER\0usb'))
def test_a_bad_owner_file_is_no_owner(fw, data):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/owner': data})
    fw.fn('eq_output_forget', None)()
    assert not [p for p in card.files if p.startswith(OUTPUTS)]


def test_an_unreadable_eq_is_not_filed_over_a_good_slot(fw):
    card = Card(fw, {ACTIVE: b'junk', f'{OUTPUTS}/usb': saved(state(fw, EQ_3, gains=(1, 0, 0)))})
    fw.fn('eq_output', None, C.c_char_p)(b'usb')  # owner usb, its slot loaded over the junk
    card.files[ACTIVE] = b'junk again'
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    assert card.state(f'{OUTPUTS}/usb').eq3.bands[0].gain == 1


@pytest.mark.parametrize('key, caption', (('3.5mm', 'EQ for 3.5 mm'), ('4.4mm', 'EQ for 4.4 mm'), ('usb', 'EQ for USB'),
                                          ('00:11:22:33:44:55', 'EQ for this Bluetooth device')))
def test_the_page_opens_under_whose_eq_it_is(fw, key, caption):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/owner': b'Q2OWNER\0' + key.encode().ljust(20, b'\0')})
    p = Page(fw, card, {('Q2POD', 'EQOUTPUT'): '1'}).open()
    assert p.title() == caption and p.e3.caption
    assert p.key(KEY_NEXT) == 1 and p.title() == '' and not p.e3.caption  # the first turn clears it
    p.tab(300)
    assert p.title() == '' and 'EQ per Output On' in p.captions()


def test_without_per_output_the_page_opens_bare(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3)), f'{OUTPUTS}/owner': b'Q2OWNER\0' + b'usb'.ljust(20, b'\0')})
    p = Page(fw, card).open()
    assert p.title() == '' and not p.e3.caption


def test_another_output_taking_over_reloads_the_open_page(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3, gains=(2, 0, 0))), f'{OUTPUTS}/usb': saved(state(fw, EQ_3, gains=(-4, 0, 0)))})
    p = Page(fw, card, {('Q2POD', 'EQOUTPUT'): '1'})
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    p.open()
    assert p.e3.gain[0] == 2
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    assert p.e3.gain[0] == -4 and p.pending('render')
    p.settle()
    assert p.title() == 'EQ for USB'


def test_unapplied_advanced_edits_follow_to_the_new_output(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_PEQ, peq=peak(fw, 6))), f'{OUTPUTS}/usb': saved(state(fw, EQ_3))})
    p = Page(fw, card)
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    p.advanced()
    p.click('Reset to defaults')
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    p.settle()
    assert p.ui.draft.bands[0].gain == 0 and p.ui.dirty and p.captions()[0] == 'Apply changes' and p.captions()[2] == 'Enabled Off'


def test_an_output_change_waits_while_a_keyboard_is_up(fw):
    p = adjust(fw)
    card = p.card
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    assert owner(card) is None and not p.pending('render')


def test_unsaved_gains_wait_for_the_next_pass(fw):
    card = Card(fw, {ACTIVE: saved(state(fw, EQ_3))})
    p = Page(fw, card)
    fw.fn('eq_output', None, C.c_char_p)(b'3.5mm')
    p.open()
    p.e3.gain[0], p.e3.dirty = 3, 1
    card.fail_writes = True
    fw.fn('eq_output', None, C.c_char_p)(b'usb')
    assert owner(card) == '3.5mm' and p.e3.dirty


def test_a_message_is_a_toast_over_the_page_s_own_title(fw):
    """action: what an action did opens stock's msginfo dialog (navigation.c toast) and leaves ui.status clear, so the
    render that follows keeps the screen's own title (docs/oled-design.md#dialogs-and-toasts)."""
    p = page(fw, files={f'{FOLDER}/x.txt': b'GraphicEQ: 20 -3\n'}).advanced()
    p.click('Presets')
    p.click('x.txt')
    assert [C.string_at(c[1][0]) for c in fw.calls('navigator_to_with_context')] == [b'dialog/msginfo_dialog']
    assert p.said == ['Use the ParametricEQ file, not GraphicEQ'] and p.ui.status == b'' and p.title() == 'Presets'


# The Type picker (docs/internals.md#parametric-eq): every type, a screen of them or the menu sheet's.

def test_the_type_picker_opens_on_the_band_s_type_and_return_leaves_it(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.ui.draft.bands[0].type = NO
    p.click('Type')
    assert p.props[(p.ui.view, '_ringnav_index')] == NO and p.props[(p.ui.view, '_ringnav_count')] == 8
    p.ret()
    assert p.ui.screen == BAND and p.ui.draft.bands[0].type == NO and 'Type Notch' in p.captions()


@pytest.mark.parametrize('type_', (LP, HP, BP, NO, AP))
def test_a_gainless_type_has_no_gain_row_and_plays_at_its_frequency(fw, type_):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click('Type')
    p.click(TYPE_NAMES[type_])
    assert p.captions() == ['Band On', 'Channels Both', 'Type ' + TYPE_NAMES[type_], 'Frequency 1000 Hz', 'Q 1.00']
    assert p.ui.dirty == 1
    p.ret()
    assert f'1  {TYPE_NAMES[type_]} 1000 Hz On' in p.captions()
    p.click('Apply changes')
    assert p.card.state().peq.bands[0].type == type_


def test_a_notch_s_q_goes_to_30_and_back_to_10_for_another_type(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click('Type')
    p.click('Notch')
    p.ui.draft.bands[0].q = 29.98
    p.click('Q ')
    p.click('Raise')
    p.click('Raise')
    assert p.ui.draft.bands[0].q == 30
    p.ret()
    p.click('Type')
    p.click('Peaking')
    assert p.ui.draft.bands[0].q == 10 and 'Gain +6.0 dB' in p.captions()  # the gain it had, kept


def test_type_opens_the_inset_modal_s_picker(fw):
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.click('Type')
    # The stub's sortselect dialog never opens over the page, so the screen of types stands in.
    assert [C.string_at(c[1][0]) for c in fw.calls('navigator_to')] == [b'dialog/sortselect_dialog']
    assert p.ui.screen == TYPES



def test_under_the_inset_modal_the_band_s_list_waits_and_a_pick_sets_the_type(fw):
    """The panel opening over the page (menu_modal.c modal_list, its dialog stood in for): no render while it is up,
    and a pick, run as its next pass, sets the type with the wheel still on Type."""
    p = page(fw, state(fw, EQ_PEQ, peq=peak(fw, 6))).advanced()
    p.click('1  ')
    p.props[(p.ui.view, '_ringnav_index')] = 2  # the wheel on Type
    view, dialog = p.ui.view, p.new(0, 0, 0, 375, 320, 'dialog')
    named = {}
    for name in ('img_return', 'list_view', 'scroll_view'):
        named[name] = p.new(dialog, 0, 0, 375, 40)
        p.names[named[name]] = name
    name = C.create_string_buffer(b'sortselect_dialog')
    tops = iter([p.page, dialog])
    fw.mock('window_manager_get_top_window', lambda *_: next(tops, dialog))
    fw.mock('widget_get_prop_str', lambda w, k, d, *_: C.addressof(name) if w == dialog else d)
    timers = len(p.timers)
    p.click('Type')
    assert p.ui.screen == BAND and p.ui.view == view and not p.pending('render')  # no render under the panel
    picks = [w for w in p.children(named['scroll_view']) if p.handlers(w, EVT_CLICK)]
    assert len(picks) == 8
    p.handler(picks[NO], EVT_CLICK)()
    fn, _ = p.timers[timers]  # the pick's pass, after the panel's close
    C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)
    p.settle()
    assert p.ui.draft.bands[0].type == NO and p.ui.screen == BAND and 'Type Notch' in p.captions()
    assert p.props[(p.ui.view, '_ringnav_index')] == 2 and p.ui.dirty == 1
    assert p.props[(p.ui.view, '_ringnav_count')] == 5  # a count ringnav keeps the row by: no Gain row on a notch


@pytest.mark.parametrize('what', ['Frequency', 'Q'])
def test_a_band_s_value_box_gives_the_strip_its_check(fw, what):
    """EQ frequency and Q are values saved: the letter strip's return key shows the check (search_strip.c)."""
    p = adjust(fw, what)
    assert strip_return(fw, p.edit()) == STRIP_SAVE


def test_a_preset_s_name_box_gives_the_strip_its_check(fw):
    p = page(fw, files={f'{FOLDER}/Rock.txt': b'x'}).advanced()
    p.click('Presets')
    p.click('Rename a preset')
    p.click('Rock.txt')
    assert strip_return(fw, p.edit()) == STRIP_SAVE
