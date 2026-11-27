"""patch/navigation.c's Play/Pause hold and what it reaches: the queue menu (docs/internals.md#queue-menu) and
playlists.c's playlist editor, Playlists' More page and its rows, then the UI loop's polls that keep
a place, a listen and the state record (resume, library_lists.c's Most Played, scrobbling, and system.c's charge
limit, watchdog and q2-state)."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import O, STRIP_SEARCH, Deques, Files, Rows, callback_at, claim, favdir_card, fnv1a, high_halves, in_fork, stock_prop_str, ui_name

W_X, W_PARENT, W_EMITTER = O['W_X'], O['W_PARENT'], O['W_EMITTER']
EMIT_TYPE, EMIT_PENDING_REMOVE, EMIT_NEXT = O['EMIT_TYPE'], O['EMIT_PENDING_REMOVE'], O['EMIT_NEXT']
EVENT_TYPE, EVENT_KEY, EVENT_X, EVENT_Y, EVENT_TARGET = (O[k] for k in ('EVENT_TYPE', 'EVENT_KEY', 'EVENT_X', 'EVENT_Y',
                                                                         'EVENT_TARGET'))
EVT_DESTROY, EVT_CLICK, EVT_KEY_UP, EVT_IM_ACTION = O['EVT_DESTROY'], O['EVT_CLICK'], O['EVT_KEY_UP'], O['EVT_IM_ACTION']
EVT_WINDOW_CLOSE, EVT_POINTER_ABORT, EVT_POINTER_UP_BEFORE = O['EVT_WINDOW_CLOSE'], O['EVT_POINTER_ABORT'], O['EVT_POINTER_UP_BEFORE']
KEY_PLAY, KEY_RETURN, KEY_BACK_BTN, KEY_FWD_BTN = O['KEY_PLAY'], O['KEY_RETURN'], O['KEY_BACK_BTN'], O['KEY_FWD_BTN']
REC_ID, REC_NAME, REC_PATH, REC_TITLE, REC_ALBUM, REC_ARTIST = (O[k] for k in ('REC_ID', 'REC_NAME', 'REC_PATH', 'REC_TITLE',
                                                                              'REC_ALBUM', 'REC_ARTIST'))
REC_TYPE, REC_TRACK, REC_DISC, REC_CUE_START, REC_CUE_END = (O[k] for k in ('REC_TYPE', 'REC_TRACK', 'REC_DISC',
                                                                            'REC_CUE_START', 'REC_CUE_END'))
TIMER_CTX, INPUT_KEYS, INPUT_KEY_SIZE, INPUT_KEY_TIME = O['TIMER_CTX'], O['INPUT_KEYS'], O['INPUT_KEY_SIZE'], O['INPUT_KEY_TIME']
PLAYLIST_ADD_MODE, G_ALLSELECT_FLAG = O['PLAYLIST_ADD_MODE'], O['G_ALLSELECT_FLAG']
STOP = 11
PLAYLIST_PICK, PLAYLIST_KEYUP = 0x4b4840, 0x4f2394  # playlists.c's stock handlers
PL_SELECT_ALL, PL_NEW, PL_EDIT, PL_IMPORT, PL_EXPORT, PL_DELETE, PL_DONE = range(7)
PL_STOCK = [0x4b46a4, 0x4b45a4, 0x4b44d4, 0x4b5e18, 0x4b5c70, 0x4b5b08, 0x4b45ec]  # pl_stock[]: stock's own handlers
PL_MARK_X = 333
THEMES = host.tools_theme.builtins()
GEOMETRY = host.tools_theme.built_geometry(THEMES)  # the one set the build bakes: OLED's


def cstr(address):
    return C.string_at(address).decode('utf-8', 'replace') if address else None


# Records.
class Records:
    """stSongInfo rows as the Q2 lays them out, read by a 64-bit P(): each 4-byte field from REC_NAME (0x08) to the
    cover (0x28) is a pointer the C may read, and P() reads it with the next field as its high half. So each field's
    word is the low half of its own string's address and the high half of the field before's: every field reads as
    a string of its own (empty unless given), each in a page mapped there, past AddressSanitizer's shadow."""
    FIRST, LAST = REC_NAME, 0x2c  # the words: 0x2c is only the cover's high half
    HIGHS = high_halves(0x400)

    def __init__(self):
        self.libc = C.CDLL(None)
        self.libc.mmap.restype, self.libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
        self.libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
        self.pages, self.keep = set(), []
        # this file's high halves (conftest.high_halves), each a string's high half once; its low half may sit near
        # its page's end, so page() maps the next page too
        self.words = iter(self.HIGHS)

    def page(self, address):
        page = address & ~0xfff
        if page not in self.pages:
            assert self.libc.mmap(page, 0x2000, 3, 0x100022, -1, 0) == page  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
            self.pages.add(page)

    def __call__(self, kind=8, id_=0, disc=0, cue=0, end=0, track=0, **fields):
        """A record: name, path, title, album, artist (bytes or str) at their fields; the rest empty strings."""
        named = {REC_NAME: 'name', REC_PATH: 'path', REC_TITLE: 'title', REC_ALBUM: 'album', REC_ARTIST: 'artist',
                 0x1c: 'genre', 0x20: 'composer', 0x24: 'album_artist'}
        assert set(fields) <= set(named.values()), fields
        r = C.create_string_buffer(0x60)
        words = [next(self.words) for _ in range(self.FIRST, self.LAST + 4, 4)]
        for i, off in enumerate(range(self.FIRST, self.LAST + 4, 4)):
            struct.pack_into('<I', r, off, words[i])
        strings = {}
        for i, off in enumerate(range(self.FIRST, self.LAST, 4)):
            address = words[i] | words[i + 1] << 32
            self.page(address)
            value = fields.get(named.get(off, ''), b'')
            value = strings[off] = value.encode() if isinstance(value, str) else value
            C.memmove(address, value + b'\0', len(value) + 1)
        struct.pack_into('<ii', r, REC_ID, id_, 0)
        struct.pack_into('<i', r, REC_TYPE, kind)
        struct.pack_into('<i', r, REC_TRACK, track)
        struct.pack_into('<i', r, REC_DISC, disc)
        struct.pack_into('<ii', r, REC_CUE_START, cue, end)
        self.keep.append(r)
        # each field, read as P() reads it, is its own string: the numbers packed after left the words alone
        assert all(field(C.addressof(r), off) == s.decode('utf-8', 'replace') for off, s in strings.items())
        return C.addressof(r)

    def close(self):
        for page in self.pages:
            self.libc.munmap(page, 0x2000)


@pytest.fixture
def recs():
    made = Records()
    yield made
    made.close()


def field(r, off):
    return cstr(C.c_void_p.from_address(r + off).value)


# AWTK, as far as the C reads it.
class Node:
    """A widget: a zeroed block holding its rect, parent and click emitter at stock's offsets; the rest here."""
    def __init__(self, kind, parent):
        self.block, self.emitter = C.create_string_buffer(0x200), C.create_string_buffer(8)
        self.addr = C.addressof(self.block)
        C.c_void_p.from_address(self.addr + W_PARENT).value = parent or None
        C.c_void_p.from_address(self.addr + W_EMITTER).value = C.addressof(self.emitter)
        self.kind, self.parent, self.children = kind, parent, []
        self.name = self.text = self.image = ''
        self.visible, self.props, self.on, self.strings, self.tr = 1, {}, [], [], False

    def rect(self):
        return struct.unpack_from('<4i', self.block, W_X)

    def link(self):
        """emitter_on's items as click_bound walks them: {ctx @0, type @8, handler @12, pending_remove, next}."""
        address = 0
        for item in reversed(self.on):
            C.c_void_p.from_address(C.addressof(item[3])).value = item[2] or None
            C.c_int.from_address(C.addressof(item[3]) + EMIT_TYPE).value = item[0]
            C.c_void_p.from_address(C.addressof(item[3]) + 12).value = item[1] or None
            C.c_void_p.from_address(C.addressof(item[3]) + EMIT_NEXT).value = address or None
            address = C.addressof(item[3])
        C.c_void_p.from_address(C.addressof(self.emitter)).value = address or None


class Ui:
    """Widgets, the window stack, timers, the navigator and toasts, mocked over the stubs: what a page's code builds
    can be read back, its handlers fired and its timers run as the next loop pass would."""
    def __init__(self, fw):
        self.fw, self.nodes, self.errors = fw, {}, []
        self.wm = self.new('window_manager', 0)
        self.stack, self.timers, self.toasts, self.went, self.closed, self.dispatched = [], {}, [], [], [], []
        self.routes, self.answers, self.contexts = {}, {}, []
        self.keys = C.create_string_buffer(INPUT_KEYS + 16 * INPUT_KEY_SIZE)
        self.pressed, self.ids, self.held = 0, 0, []
        mocks = {
            'window_manager': lambda *_: self.wm,
            'window_manager_get_top_window': lambda *_: self.top(),
            'window_manager_get_pointer_pressed': lambda *_: self.pressed,
            'window_manager_get_input_device_status': lambda *_: C.addressof(self.keys),
            'window_manager_close_window_force': lambda wm, w, *_: self.close(w),
            'window_create': lambda *_: self.window(''),
            'window_close': lambda w, *_: self.close(w),
            'widget_factory_create_widget': lambda f, kind, parent, x, y, w, *_: self.child(parent, cstr(kind), x, y, w, 0),
            'widget_get_child': lambda w, i, *_: self.kid(w, C.c_int(i).value),
            'widget_count_children': lambda w, *_: len(self.kids(w)),
            'widget_lookup': lambda w, name, deep, *_: self.lookup(w, cstr(name), C.c_int(deep).value),
            'widget_get_prop_int': lambda w, name, d, *_: self.prop(w, cstr(name), C.c_int(d).value),
            'widget_get_prop_bool': lambda w, name, d, *_: self.prop(w, cstr(name), C.c_int(d).value),
            'widget_get_prop_str': lambda w, name, d, *_: self.prop_str(w, cstr(name), d),
            'widget_set_prop_int': lambda w, name, v, *_: self.set(w, cstr(name), C.c_int(v).value),
            'widget_set_prop_str': lambda w, name, v, *_: self.set(w, cstr(name), cstr(v)),
            'widget_set_name': lambda w, name, *_: self.attr(w, 'name', cstr(name)),
            'widget_get_type': lambda w, *_: self.c(w, self.nodes[w].kind) if w in self.nodes else 0,
            'widget_get_visible': lambda w, *_: self.nodes[w].visible if w in self.nodes else 0,
            'widget_set_visible': lambda w, v, *_: self.attr(w, 'visible', C.c_int(v).value),
            'widget_set_text_utf8': lambda w, s, *_: self.attr(w, 'text', cstr(s)),
            'widget_set_tr_text': lambda w, s, *_: self.attr(w, 'text', cstr(s)) or self.attr(w, 'tr', True),
            'widget_get_text_utf8': self.get_text,
            'widget_get_text': lambda w, *_: self.wide(w),
            'widget_move_resize': lambda w, x, y, ww, h, *_: self.place(w, x, y, ww, h),
            'widget_resize': lambda w, ww, h, *_: self.place(w, self.nodes[w].rect()[0], self.nodes[w].rect()[1], ww, h)
            if w in self.nodes else 0,
            'widget_on': lambda w, kind, fn, ctx, *_: self.bind(w, C.c_int(kind).value, fn, ctx),
            'widget_off_by_func': lambda w, kind, fn, ctx, *_: self.unbind(w, C.c_int(kind).value, fn, ctx),
            'widget_destroy_children': lambda w, *_: self.nodes[w].children.clear() if w in self.nodes else 0,
            'widget_restack': lambda w, at, *_: self.restack(w, C.c_int(at).value),
            'widget_use_style': lambda w, s, *_: self.set(w, 'style', cstr(s)),
            'image_base_set_image': lambda w, s, *_: self.attr(w, 'image', cstr(s)),
            'timer_add': self.add_timer,
            'timer_remove': lambda t, *_: self.timers.pop(C.c_uint(t).value, None) and 0,
            'navigator_to': lambda name, *_: self.go(cstr(name), 0),
            'navigator_to_with_context': lambda name, ctx, *_: self.go(cstr(name), ctx),
            'navigator_window_is_exist': lambda name, *_: any(self.nodes[w].name == cstr(name) for w in self.stack),
            'navigator_back': lambda *_: self.back(),
            'pointer_event_init': self.pointer_event,
            'stock_dispatch_trampoline': lambda w, e, *_: self.dispatched.append((w, C.c_int.from_address(e + EVENT_TYPE).value)),
            'tk_str_start_with': lambda s, prefix, *_: bool(s) and C.string_at(s).startswith(C.string_at(prefix)),
        }
        for kind in ('list_item', 'button', 'image', 'hscroll_label', 'label', 'list_view', 'scroll_view'):
            mocks[f'{kind}_create'] = lambda parent, x, y, w, h, *_, kind=kind: self.child(parent, kind, x, y, w, h)
        for name, fn in mocks.items():
            fw.mock(name, self.guard(fn))
        fw.var('g_backlight_status', C.c_ubyte).value = 1

    def guard(self, fn):
        """A mock's exception is kept: ctypes would print it and answer 0."""
        def run(*args):
            try:
                return fn(*args)
            except Exception as e:  # noqa: BLE001
                self.errors.append(repr(e))
                raise
        return run

    def new(self, kind, parent):
        node = Node(kind, parent)
        self.nodes[node.addr] = node
        return node.addr

    def child(self, parent, kind, x=0, y=0, w=0, h=0):
        address = self.new(kind, parent)
        if parent in self.nodes:
            self.nodes[parent].children.append(address)
        self.place(address, x, y, w, h)
        return address

    def window(self, name, kind='window'):
        address = self.new(kind, self.wm)
        self.nodes[address].name = name
        self.stack.append(address)
        return address

    def top(self):
        return self.stack[-1] if self.stack else 0

    def close(self, w):
        if w in self.stack:
            self.stack.remove(w)
            self.closed.append(w)
            self.fire(w, EVT_DESTROY)
        return 0

    def back(self):
        self.went.append('back')
        if self.stack:
            self.close(self.stack[-1])
        return 0

    def go(self, name, ctx):
        if name == 'dialog/msginfo_dialog':
            self.toasts.append(cstr(ctx + 8))
            return 0
        self.went.append(name)
        self.contexts.append((name, ctx))
        if name in self.routes:
            self.routes[name](ctx)
        return self.answers.get(name, 0)

    def kids(self, w):
        return self.nodes[w].children if w in self.nodes else []

    def kid(self, w, i):
        kids = self.kids(w)
        return kids[i] if 0 <= i < len(kids) else 0

    def lookup(self, w, name, deep):
        for k in self.kids(w):
            if self.nodes[k].name == name:
                return k
            if deep and (found := self.lookup(k, name, deep)):
                return found
        return 0

    def prop(self, w, name, default):
        value = self.nodes[w].props.get(name, default) if w in self.nodes else default
        return value if isinstance(value, int) else default

    def c(self, w, text):
        buf = C.create_string_buffer(text.encode())
        self.nodes[w].strings.append(buf)
        return C.addressof(buf)

    def prop_str(self, w, name, default):
        node = self.nodes.get(w)
        if node is None:
            return default  # stock: widget_get_prop fails on a NULL widget
        value = (node.name or None) if name == 'name' else (node.image or None) if name == 'image' else node.props.get(name)
        value = stock_prop_str(node.kind.encode(), name.encode(), value.encode() if isinstance(value, str) else value, default)
        return 0 if value is None else self.c(w, value.decode()) if isinstance(value, bytes) else value

    def set(self, w, name, value):
        if w in self.nodes:
            self.nodes[w].props[name] = value
        return 0

    def attr(self, w, name, value):
        if w in self.nodes:
            setattr(self.nodes[w], name, value)
        return 0

    def get_text(self, w, out, n, *_):
        data = self.nodes[w].text.encode()[:n - 1] if w in self.nodes else b''
        C.memmove(out, data + b'\0', len(data) + 1)
        return 0

    def wide(self, w):
        if w not in self.nodes:
            return 0
        chars = [ord(ch) for ch in self.nodes[w].text] + [0]
        buf = (C.c_uint * len(chars))(*chars)
        self.nodes[w].strings.append(buf)
        return C.addressof(buf)

    def place(self, w, x, y, ww, h):
        if w in self.nodes:
            struct.pack_into('<4i', self.nodes[w].block, W_X, *(C.c_int(v).value for v in (x, y, ww, h)))
        return 0

    def bind(self, w, kind, fn, ctx):
        if w not in self.nodes or not fn:  # emitter_on refuses no handler (TK_INVALID_ID)
            return 0
        self.ids += 1
        self.nodes[w].on.append([kind, fn, ctx, C.create_string_buffer(0x28)])
        self.nodes[w].link()
        return self.ids

    def unbind(self, w, kind, fn, ctx):
        if w in self.nodes:
            node = self.nodes[w]
            node.on = [i for i in node.on if (i[0], i[1], i[2]) != (kind, fn, ctx)]
            node.link()
        return 0

    def restack(self, w, at):
        parent = self.nodes[self.nodes[w].parent]
        parent.children.remove(w)
        parent.children.insert(at, w)
        return 0

    def add_timer(self, fn, ctx, ms, *_):
        self.ids += 1
        self.timers[self.ids] = (fn, ctx, ms)
        return self.ids

    def pointer_event(self, out, kind, target, x, y, *_):
        C.memset(out, 0, 0x20)
        C.c_int.from_address(out + EVENT_TYPE).value = kind
        C.c_void_p.from_address(out + EVENT_TARGET).value = target or None
        C.c_int.from_address(out + EVENT_X).value, C.c_int.from_address(out + EVENT_Y).value = C.c_int(x).value, C.c_int(y).value
        return out

    # What a test does.
    def fn(self, name):
        """A function's address, in navigation.c or the unit it moved to (ui_name finds it), as the C passes it to
        widget_on and timer_add."""
        return C.cast(getattr(self.fw.lib, name if '__' in name or name.startswith('ringnav') else ui_name(self.fw.lib, name)), C.c_void_p).value

    def due(self, name=None):
        """The next loop pass: every timer pending now (or only name's) runs once, as stock's one-shots do."""
        ran = []
        for t, (fn, ctx, ms) in list(self.timers.items()):
            if name is not None and fn != self.fn(name):
                continue
            del self.timers[t]
            info = C.create_string_buffer(0x40)
            C.c_void_p.from_address(C.addressof(info) + TIMER_CTX).value = ctx or None
            ran.append(C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info)))
        return ran

    def pending(self):
        return [fn for fn, _, _ in self.timers.values()]

    def fire(self, w, kind, event=None):
        """w's handlers for kind, in order, until one stops it; the last answer."""
        answer = 0
        if event is None:
            event = C.create_string_buffer(0x40)
            C.c_int.from_address(C.addressof(event) + EVENT_TYPE).value = kind
        for k, fn, ctx, _ in list(self.nodes[w].on if w in self.nodes else []):
            if k == kind:
                answer = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, C.addressof(event))
                if answer == STOP:
                    break
        return answer

    def click(self, w, x=0):
        event = C.create_string_buffer(0x40)
        self.pointer_event(C.addressof(event), EVT_CLICK, w, x, 0)
        return self.fire(w, EVT_CLICK, event)

    def key(self, w, key, kind=EVT_KEY_UP):
        event = C.create_string_buffer(0x40)
        C.c_int.from_address(C.addressof(event) + EVENT_TYPE).value = kind
        C.c_int.from_address(C.addressof(event) + EVENT_KEY).value = key
        return self.fire(w, kind, event)

    def handlers(self, w, kind):
        return [fn for k, fn, _, _ in self.nodes[w].on if k == kind]

    def label(self, item):
        """A row's caption: its first labelled descendant's text."""
        for k in self.kids(item):
            if self.nodes[k].text:
                return self.nodes[k].text
            if (found := self.label(k)) is not None:
                return found
        return None

    def items(self, view):
        """View's rows: its kids but a menu sheet's header space (menu_modal.c MM_HEAD)."""
        return [k for k in self.kids(view) if self.nodes[k].name != '_mm_head']

    def rows(self, view):
        return [self.label(item) for item in self.items(view)]

    def find(self, w, text):
        """The row of view w whose caption is text."""
        rows = self.rows(w)
        assert text in rows, rows
        return self.items(w)[rows.index(text)]

    def press(self, key=KEY_PLAY, at=0x1234567890):
        """Stock's input device status: key held, pressed at at (the Play hold's latch reads it)."""
        struct.pack_into('<IQ', self.keys, INPUT_KEYS, key, 0)
        struct.pack_into('<Q', self.keys, INPUT_KEYS + INPUT_KEY_TIME, at)


@pytest.fixture
def ui(fw):
    world = Ui(fw)
    yield world
    assert not world.errors


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture(scope='module')
def power_fields(library):
    """system.c's own state, pw: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'system.o', 'pw')


@pytest.fixture
def power(fw, power_fields):
    base = C.addressof(fw.var('system__pw', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + power_fields[name])


@pytest.fixture(scope='module')
def lists_fields(library):
    """library_lists.c's own state, ll: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'library_lists.o', 'll')


@pytest.fixture
def lists(fw, lists_fields):
    base = C.addressof(fw.var('library_lists__ll', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + lists_fields[name])


@pytest.fixture
def stock_globals():
    """Stock's add mode and Select All flag, at their fixed addresses in conftest's map: zeroed, put back after."""
    saved = [(a, C.string_at(a, 4)) for a in (PLAYLIST_ADD_MODE, G_ALLSELECT_FLAG)]
    for a, _ in saved:
        claim(a)
        C.memset(a, 0, 4)
    yield
    for a, data in saved:
        C.memmove(a, data, 4)


def use_set(st):
    """OLED's geometry set, the one the build bakes, with the settings already read (the config would set them
    again)."""
    st('settings_read').value = 1
    return GEOMETRY


# The playlist editor (ple_*).
class Ple(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view songs picker edit match'.split()),
                *((k, C.c_int) for k in 'dest mode selected pending nmatch'.split()),
                ('key', C.c_uint), ('timer', C.c_uint), ('name', C.c_char * 128), ('query', C.c_char * 128)]


def directory(fw):
    """The deque stock's queries fill: staged() puts its own there for each query (Deques masks the high half)."""
    return fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value


class Table:
    """The library's playlist table: songs (id > 100, cd_num their playlist's), as getMusicByPlayList,
    checkPlayListExist, batch_add_file and sqlite3_exec see it, each query's records fresh as stock's; and the
    library's songs (getAllMusic). A failing statement (fail) rolls back."""
    def __init__(self, fw, recs, deques, titles):
        self.fw, self.recs, self.deques, self.titles = fw, recs, deques, titles
        self.rows, self.log, self.fail, self.saved = {}, [], (), {}
        self.library = [recs(path=p, title=t, id_=i + 1) for i, (p, t) in enumerate(titles.items())]
        fw.mock('getMusicByPlayList', self.playlist)
        fw.mock('sort_playlist_addtime', self.playlist)  # the editor's read
        fw.mock('getAllMusic', lambda *_: self.fill(self.library))
        fw.mock('checkPlayListExist', lambda r, dest, *_: (C.c_int(dest).value, field(r, REC_PATH)) in self.rows.values())
        fw.mock('batch_add_file', self.add)
        fw.mock('_create_vector', lambda *_: 0x7e)  # the editor's own selection
        fw.mock('sqlite3_open', lambda path, db, *_: setattr(C.c_void_p.from_address(db), 'value', 0x5d))
        fw.mock('sqlite3_exec', self.exec)

    def fill(self, records):
        self.deques[directory(self.fw)][:] = records
        return len(records)

    def songs(self, dest):
        return [self.titles[p] for i, (c, p) in sorted(self.rows.items()) if c == dest]

    def put(self, dest, *paths):
        for p in paths:
            self.rows[max(self.rows, default=100) + 1] = (dest, p)

    def playlist(self, dest, *_):
        dest = C.c_int(dest).value
        return self.fill([self.recs(path=p, title=self.titles[p], id_=i) for i, (c, p) in sorted(self.rows.items()) if c == dest])

    def add(self, cls, kind, songs, select, dest, *_):
        for r in self.deques[songs]:
            self.put(C.c_int(dest).value, field(r, REC_PATH))
        return 0

    def exec(self, db, sql, callback, ctx, *_):
        text = cstr(sql)
        self.log.append(text)
        if any(f in text for f in self.fail):
            return 1
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback) if callback else None
        changes = 0
        for s in text.split(';'):
            rows: list = []
            if s == 'begin immediate':
                self.saved = dict(self.rows)
            elif s == 'rollback':
                self.rows = self.saved
            elif s in ('commit', 'PRAGMA busy_timeout=500'):  # pl_db's wait for a reader
                pass
            elif m := re.fullmatch(r'select id,fileurl,time,time_end from playlist where cd_num=(-?\d+) and id in \((-?\d+),(-?\d+)\)', s):
                dest, a, b = map(int, m.groups())
                rows = [[b'%d' % i, p.encode(), b'0', b'0'] for i, (c, p) in sorted(self.rows.items()) if c == dest and i in (a, b)]
            elif s.startswith('select case when ifnull(max(id),100)<2147483647'):
                rows = [[b'%d' % (max(max(self.rows, default=100), 100) + 1)]]
            elif m := re.fullmatch(r'update playlist set id=(\d+) where cd_num=(\d+) and id=(\d+)', s):
                new, dest, old = map(int, m.groups())
                changes = int(self.rows.get(old, (None,))[0] == dest and new not in self.rows)
                if changes:
                    self.rows[new] = self.rows.pop(old)
            elif m := re.fullmatch(r'delete from playlist where cd_num=(\d+) and id=(\d+)', s):
                dest, old = map(int, m.groups())
                changes = int(self.rows.get(old, (None,))[0] == dest)
                if changes:
                    del self.rows[old]
            elif s == 'select changes()':
                rows = [[b'%d' % changes]]
            else:
                raise AssertionError(s)
            for row in rows:
                if call and call(ctx, len(row), (C.c_char_p * len(row))(*row), None):
                    return 4  # SQLITE_ABORT
        return 0


TITLES = {'/mnt/mmc/a.flac': 'One', '/mnt/mmc/b.flac': 'Two', '/mnt/mmc/c.flac': 'Three', '/mnt/mmc/d.flac': 'Twenty',
          '/mnt/mmc/e.flac': 'Five'}


class Editor:
    """Road Trip (playlist 3: One, Two, Three) as Playlists' row 1 opens it in the editor."""
    def __init__(self, fw, ui, recs, songs=('a', 'b', 'c')):
        self.fw, self.ui = fw, ui
        self.deques = Deques(fw)
        self.table = Table(fw, recs, self.deques, TITLES)
        self.table.put(3, *(f'/mnt/mmc/{s}.flac' for s in songs))
        headers = [recs(title='Gym', disc=1, id_=1), recs(title='Road Trip', disc=3, id_=3)]
        fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = self.deques.new(headers)
        self.ple = fw.var('playlists__ple', Ple)
        self.open = fw.fn('playlists__ple_open', C.c_int, C.c_void_p, C.c_void_p)

    def view(self):
        return self.ple.view

    def rows(self):
        return self.ui.rows(self.ple.view)

    def pick(self, caption):
        """A row's click and the pass after it, which runs it."""
        self.ui.click(self.ui.find(self.ple.view, caption))
        self.ui.due('ple_run')

    def title(self):
        """The page's title line (page_title's label)."""
        return self.ui.nodes[self.ui.kids(self.ple.page)[0]].text

    def ticked(self):
        return [self.ui.label(item) for item in self.ui.kids(self.ple.view)
                if any(self.ui.nodes[k].image == 'select' for k in self.ui.kids(item))]


@pytest.fixture
def editor(fw, ui, recs, st):
    e = Editor(fw, ui, recs)
    use_set(st)
    assert e.open(1, None) == 0
    return e


def test_edit_playlist_lists_its_songs_under_add_and_done(editor, ui):
    assert ui.nodes[ui.top()].name == 'playlistedit_page' and editor.ple.dest == 3
    assert editor.rows() == ['Add Songs', 'Done', 'One', 'Two', 'Three']
    assert editor.title() == 'Editing: Road Trip'
    assert ui.nodes[editor.view()].props['_ringnav_index'] == 2  # the first song, below Add Songs and Done


@pytest.mark.parametrize('at', [2, 5, 0xffffffff])
def test_edit_playlist_of_no_such_row_opens_nothing(fw, ui, recs, at):
    e = Editor(fw, ui, recs)
    assert e.open(at, None) == 0 and not ui.stack and not e.ple.page


@pytest.mark.parametrize('disc', [-1, 100])
def test_a_playlist_whose_number_is_no_playlists_opens_nothing(fw, ui, recs, disc):
    e = Editor(fw, ui, recs)
    e.fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = e.deques.new([recs(title='Odd', disc=disc)])
    assert e.open(0, None) == 0 and not ui.stack


def test_a_second_open_is_the_page_already_open(editor, ui):
    assert editor.open(0, None) == 0 and len(ui.stack) == 1 and editor.ple.dest == 3


def test_move_down_saves_the_new_order_and_follows_the_song(editor, ui):
    editor.pick('Two')
    assert editor.rows() == ['Move Up', 'Move Down', 'Remove from Playlist'] and editor.title() == 'Song Actions: Road Trip'
    editor.pick('Move Down')
    assert editor.table.songs(3) == ['One', 'Three', 'Two'] and editor.rows()[2:] == ['One', 'Three', 'Two']
    assert ui.nodes[editor.view()].props['_ringnav_index'] == 4 and editor.table.log[-1] == 'commit'


def test_move_up_saves_the_new_order(editor):
    editor.pick('Three')
    editor.pick('Move Up')
    assert editor.table.songs(3) == ['One', 'Three', 'Two'] and editor.ple.selected == 1


@pytest.mark.parametrize('song, move', [('One', 'Move Up'), ('Three', 'Move Down')])
def test_a_move_past_either_end_changes_nothing(editor, song, move):
    editor.pick(song)
    editor.pick(move)
    assert editor.table.songs(3) == ['One', 'Two', 'Three'] and not editor.table.log
    assert editor.ple.mode == 0


def test_a_move_that_cant_be_saved_says_so_and_keeps_the_order(editor, ui):
    editor.table.fail = ('update',)
    editor.pick('Two')
    editor.pick('Move Down')
    assert ui.toasts == ['Could not save playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']
    assert editor.table.log[-1] == 'rollback'


def test_a_song_changed_under_the_editor_is_not_moved(editor, ui):
    editor.table.rows[102] = (3, '/mnt/mmc/e.flac')  # Two's row is another song now
    editor.pick('Two')
    editor.pick('Move Down')
    assert ui.toasts == ['Could not save playlist'] and editor.table.log[-1] == 'rollback'


def ple_answer(fw, row):
    """Remove from Playlist's confirmation card answered (feedback.c runs it once the card has closed)."""
    fw.fn('playlists__ple_remove_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, 0)


def test_remove_asks_on_the_card_then_takes_the_song_out(editor, ui):
    editor.pick('Two')
    editor.pick('Remove from Playlist')
    assert ui.went[-1] == 'dialog/sortselect_dialog' and editor.table.songs(3) == ['One', 'Two', 'Three']
    ple_answer(editor.fw, 1)  # Remove
    assert editor.table.songs(3) == ['One', 'Three'] and editor.rows() == ['Add Songs', 'Done', 'One', 'Three']


def test_cancel_keeps_the_song(editor):
    editor.pick('Two')
    editor.pick('Remove from Playlist')
    ple_answer(editor.fw, 0)
    assert editor.table.songs(3) == ['One', 'Two', 'Three'] and editor.rows()[0] == 'Add Songs' and not editor.table.log


def test_the_last_song_removed_leaves_the_selection_on_the_list(fw, ui, recs, st):
    e = Editor(fw, ui, recs, songs=('a',))
    use_set(st)
    e.open(1, None)
    e.pick('One')
    e.pick('Remove from Playlist')
    ple_answer(fw, 1)
    assert e.rows() == ['Add Songs', 'Done'] and e.ple.selected == 0


def test_a_remove_that_cant_be_saved_says_so(editor, ui):
    editor.table.fail = ('delete',)
    editor.pick('One')
    editor.pick('Remove from Playlist')
    ple_answer(editor.fw, 1)
    assert ui.toasts == ['Could not save playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']


def test_done_leaves_the_editor_and_its_state(editor, ui):
    editor.pick('Done')
    assert ui.went == ['back'] and not ui.stack and editor.ple.page is None and editor.ple.songs is None


def test_add_songs_lists_the_library_with_the_playlists_ticked(editor, ui):
    editor.pick('Add Songs')
    assert editor.rows() == ['Search', 'One', 'Two', 'Three', 'Twenty', 'Five']
    assert editor.title() == 'Add Songs' and editor.ticked() == ['One', 'Two', 'Three']  # the bar names the playlist


def test_a_picked_song_is_added_and_ticked(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Five')
    assert editor.table.songs(3) == ['One', 'Two', 'Three', 'Five'] and ui.toasts == ['Added to Playlist']
    assert 'Five' in editor.ticked() and editor.ple.mode == 1  # the picker stays on its song
    calls = [a for _, a in editor.fw.calls('batch_add_file')]
    assert [a[:2] for a in calls] == [(0xf001, 0xf00b)] and calls[0][4] == 3


def test_a_song_already_there_is_not_added_twice(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Two')
    assert ui.toasts == ['Already in playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']
    assert not editor.fw.calls('batch_add_file')


def test_an_add_that_stock_refused_says_so(editor, ui):
    editor.pick('Add Songs')
    editor.fw.mock('batch_add_file', lambda *_: 0)
    editor.pick('Five')
    assert ui.toasts == ['Could not save playlist'] and 'Five' not in editor.ticked()


def test_the_add_selection_is_made_once(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Five')
    editor.pick('Twenty')
    assert len(editor.fw.calls('_create_vector')) == 1 and len({a[3] for _, a in editor.fw.calls('batch_add_file')}) == 1


def test_an_add_with_no_memory_says_so(editor, ui, fw):
    editor.pick('Add Songs')
    fw.mock('fw__create_deque', lambda *_: 0)
    editor.pick('Five')
    assert ui.toasts == ['Could not save playlist'] and editor.table.songs(3) == ['One', 'Two', 'Three']


def test_search_filters_by_title_case_aside(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    edit = ui.lookup(editor.ple.page, 'search_edit', 1)
    assert edit and editor.ple.mode == 4 and ui.nodes[edit].props['tips'] == 'Search songs'
    ui.nodes[edit].text = 'tW'
    ui.fire(edit, EVT_IM_ACTION)
    ui.due('ple_run')
    assert editor.rows() == ['Search: tW', 'Two', 'Twenty'] and editor.ticked() == ['Two']


def test_a_search_that_matches_nothing_says_so(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text = 'zz'
    assert ui.key(editor.ple.page, KEY_RETURN) == STOP  # Return applies the box, from the next pass
    ui.due('ple_run')
    # docs/oled-design.md#empty-and-offline: the Search row alone, the empty state under it in its compact form
    # (a row above it: no glyph), centred from the row's foot, with no button and no fake row to pick
    assert editor.rows() == ['Search: zz']
    art = ui.lookup(editor.ple.page, '_empty_state', 0)
    assert art and ui.nodes[art].visible
    props = ui.nodes[art].props
    assert (props['_es_glyph'], props['_es_title'], props['_es_hint'], props['_es_row']) == ('', 'No Matches', 'Try fewer letters', '')
    assert not ui.lookup(editor.ple.page, '_empty_row', 1)
    editor.pick('Search: zz')  # a search that matches: the state goes with the page's rows
    ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text = 'tw'
    ui.key(editor.ple.page, KEY_RETURN)
    ui.due('ple_run')
    assert editor.rows() == ['Search: tw', 'Two', 'Twenty'] and not ui.lookup(editor.ple.page, '_empty_state', 0)


def test_the_search_box_keeps_its_query(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text = 'one'
    ui.key(editor.ple.page, KEY_RETURN)
    ui.due('ple_run')
    editor.pick('Search: one')
    assert ui.nodes[ui.lookup(editor.ple.page, 'search_edit', 1)].text == 'one'


def test_an_unreadable_box_clears_the_query(editor, ui):
    editor.pick('Add Songs')
    editor.pick('Search')
    ui.fw.mock('widget_get_text_utf8', lambda *_: 1)
    ui.key(editor.ple.page, KEY_RETURN)
    ui.due('ple_run')
    assert editor.rows()[0] == 'Search' and len(editor.rows()) == 6


@pytest.mark.parametrize('steps', [('Two',), ('Two', 'Remove from Playlist'), ('Add Songs',)])
def test_return_from_a_step_goes_back_to_the_list(editor, ui, steps):
    for caption in steps:
        editor.pick(caption)
    assert ui.key(editor.ple.page, KEY_RETURN) == STOP
    assert editor.ple.mode == 0 and editor.rows()[:2] == ['Add Songs', 'Done'] and len(ui.stack) == 1


def test_return_on_the_list_leaves(editor, ui):
    assert ui.key(editor.ple.page, KEY_RETURN) == STOP and ui.went == ['back'] and not ui.stack


def test_other_keys_are_not_the_editors(editor, ui):
    assert ui.key(editor.ple.page, KEY_PLAY) == 0 and len(ui.stack) == 1


def test_a_second_click_in_one_pass_is_dropped(editor, ui):
    ui.click(ui.find(editor.view(), 'Two'))
    ui.click(ui.find(editor.view(), 'Three'))
    ui.due('ple_run')
    assert editor.title() == 'Song Actions: Road Trip'
    editor.pick('Move Down')  # Two's actions, the first click's: Three, the last song, could not move down
    assert editor.table.songs(3) == ['One', 'Three', 'Two']


def test_a_click_run_after_the_page_closed_does_nothing(editor, ui):
    ui.click(ui.find(editor.view(), 'Done'))
    ui.close(editor.ple.page)
    ui.due()
    assert ui.went == []


def test_an_out_of_range_pick_does_nothing(editor, ui):
    editor.pick('Add Songs')
    editor.ple.pending = 9
    rows = editor.rows()
    assert editor.fw.fn('playlists__ple_run', C.c_int, C.c_void_p)(None) == 0 and editor.rows() == rows


def test_a_toolbarless_editor_moves_only_the_selected_rows_title(fw, ui, recs, st):
    e = Editor(fw, ui, recs)
    use_set(st)
    e.open(1, None)
    focus = [a[0] for _, a in fw.calls('hscroll_label_set_only_focus') if a[1] == 1]
    assert focus == [ui.kids(item)[0] for item in ui.kids(e.view())] and len(focus) == 5


def test_add_songs_title_is_the_task_alone_where_the_bar_holds_the_caption(fw, ui, recs, st):
    """OLED's bar holds the page's caption (no title row, page_title_h 0): room for the task's name alone."""
    e = Editor(fw, ui, recs)
    use_set(st)
    e.open(1, None)
    e.pick('Add Songs')
    assert e.title() == 'Add Songs'


# The side buttons move the selected song: phase 1 the press, 2 its release, 3 stock's long press.
def ple_key(fw, key, phase):
    return fw.fn('ple_key', C.c_int, C.c_uint, C.c_int)(key, phase)


def test_the_next_button_moves_the_selected_song_down(editor, ui, fw):
    ui.nodes[editor.view()].props['_ringnav_index'] = 3  # Two
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1
    assert editor.table.songs(3) == ['One', 'Three', 'Two']
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1 and editor.table.songs(3) == ['One', 'Three', 'Two']  # a repeat
    assert ple_key(fw, KEY_FWD_BTN, 2) == 1 and editor.ple.key == 0


def test_the_previous_button_moves_it_up(editor, ui, fw):
    ui.nodes[editor.view()].props['_ringnav_index'] = 3
    ple_key(fw, KEY_BACK_BTN, 1)
    assert editor.table.songs(3) == ['Two', 'One', 'Three']


def test_a_button_on_add_and_done_moves_nothing(editor, ui, fw):
    ui.nodes[editor.view()].props['_ringnav_index'] = 1
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1 and not editor.table.log


def test_a_button_under_another_window_is_held_but_moves_nothing(editor, ui, fw):
    ui.window('msginfo_dialog')
    assert ple_key(fw, KEY_FWD_BTN, 1) == 1 and not editor.table.log and editor.ple.key == 2


def test_a_release_after_the_page_closed_is_still_swallowed(editor, ui, fw):
    ple_key(fw, KEY_BACK_BTN, 1)
    ui.back()
    assert editor.ple.page is None and editor.ple.key == 1
    assert ple_key(fw, KEY_BACK_BTN, 2) == 1 and ple_key(fw, KEY_BACK_BTN, 2) == 0


def test_the_buttons_are_stocks_without_the_editor(fw, ui):
    assert ple_key(fw, KEY_FWD_BTN, 1) == 0 and ple_key(fw, KEY_PLAY, 1) == 0


def test_a_long_press_of_a_side_button_is_the_editors(editor, ui, fw):
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, KEY_FWD_BTN)
    assert fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)(None, event) == STOP
    assert not fw.calls('stock_keylong_trampoline') and not editor.table.log


# Stock code at a fixed address, for a handler the C calls there. Only ever in a fork.
def stock_handler(address, result=0):
    """A stand-in at address: its first two arguments and the event's x (the second's EVENT_X) logged, result returned."""
    log = (C.c_long * 3)()
    claim(address)
    def handler(a, b, _):
        log[0], log[1], log[2] = a, b, C.c_uint32.from_address(b + EVENT_X).value
        return result & 0xffffffff
    callback_at(address, handler)
    return log


# Playlists' More page (ringnav_playlistsmore).
class More:
    """Stock's playlistsmore_page: Rename and Delete Song rows, each a list_item holding its button, icon and label."""
    def __init__(self, fw, ui, pitch):
        self.ui, self.pitch = ui, pitch
        self.win = ui.window('playlistsmore_page')
        self.rows = []
        for i, name in enumerate(('rename', 'del')):
            # from the top with no toolbars (the grid's toolbar 0, tools/ipod.py)
            item = ui.child(self.win, 'list_item', 0, i * pitch, 375, pitch)
            button = ui.child(item, 'button', 20, 0, 335, pitch)
            icon = ui.child(button, 'image', 10, 0, 52, pitch)
            label = ui.child(button, 'label', 72, 0, 253, 20)
            ui.nodes[icon].name, ui.nodes[label].name = f'img_{name}', f'label_{name}'
            ui.nodes[label].text = 'Rename' if name == 'rename' else 'Delete Song'
            self.rows.append((item, icon, label))
        self.more = fw.fn('ringnav_playlistsmore', C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('stock_playlistsmore_trampoline', lambda *_: 0x55)

    def edit(self):
        """The row this adds: its list_item, button, icon and label."""
        item = self.ui.kids(self.win)[-1]
        button = self.ui.kids(item)[0]
        icon, label = self.ui.kids(button)[:2]
        return item, button, icon, label


def pitch(st):
    return host.tools_theme.immediate_values(GEOMETRY)['pitch']


def test_more_adds_edit_playlist_after_delete_at_the_same_spacing(fw, ui, st):
    use_set(st)
    more = More(fw, ui, pitch(st))
    assert more.more(more.win, 1) == 0x55 and ui.nodes[more.win].props['_playlist'] == 1
    item, button, icon, label = more.edit()
    rename, delete = (ui.nodes[r[0]].rect()[1] for r in more.rows)
    assert ui.nodes[item].rect()[1] - delete == delete - rename and ui.nodes[item].rect()[3] == more.pitch
    assert ui.nodes[label].text == 'Edit Playlist' and ui.nodes[icon].image == 'rename'
    assert ui.handlers(button, EVT_CLICK) == [ui.fn('ple_open')]


def test_mores_delete_says_playlist_and_keeps_its_translation_key(fw, ui, st):
    use_set(st)
    more = More(fw, ui, pitch(st))
    more.more(more.win, 1)
    label = ui.nodes[more.rows[1][2]]
    assert label.text == 'Delete Playlist' and label.tr and ui.nodes[more.rows[0][2]].text == 'Rename'


def test_edit_playlist_from_more_opens_the_editor_on_that_playlist(fw, ui, st, recs):
    use_set(st)
    editor = Editor(fw, ui, recs)
    more = More(fw, ui, pitch(st))
    more.more(more.win, 1)
    ui.click(more.edit()[1])
    assert ui.nodes[ui.top()].name == 'playlistedit_page' and editor.rows()[2:] == ['One', 'Two', 'Three']


def test_more_rows_lose_their_icons_and_line_up_at_the_text_column(fw, ui, st):
    geometry = use_set(st)
    more = More(fw, ui, pitch(st))
    more.more(more.win, 1)
    item, button, icon, label = more.edit()
    rows = [(r[1], r[2]) for r in more.rows] + [(icon, label)]
    assert not any(ui.nodes[i].visible for i, _ in rows)
    assert {ui.nodes[lb].rect()[0] for _, lb in rows} == {geometry.text_x}
    assert all(ui.nodes[ui.nodes[lb].parent].rect()[2] == 375 for _, lb in rows)  # each button the row's width
    chevrons = [[k for k in ui.kids(r) if ui.nodes[k].kind == 'image'] for r in (more.rows[0][0], more.rows[1][0], item)]
    assert [len(c) for c in chevrons] == [1, 0, 1] and not any(ui.nodes[c[0]].visible for c in chevrons if c)
    assert ui.nodes[item].rect()[1] == 2 * more.pitch  # no toolbar above the rows


def test_more_of_no_page_is_stocks(fw, ui):
    fw.mock('stock_playlistsmore_trampoline', lambda *_: 7)
    assert fw.fn('ringnav_playlistsmore', C.c_int, C.c_void_p, C.c_void_p)(None, 1) == 7 and not ui.nodes[ui.wm].children


# Playlists as rows, with no toolbars (the grid's toolbar 0; pl_*, ringnav_playlist).
class Playlists:
    """Stock's playlist_page as its init and every rebuild (stock_playlist_trampoline) leave it: the toolbar
    (view_navbar), its title and the buttons bound to stock's handlers; the list (scroll_view): Import and Export's
    tile row (none in the picker), then a row a playlist, its button bound to PLAYLIST_PICK holding the art, view_info
    (name and "N songs"), its dots img_more<i> and select mode's mark img_choice."""
    def __init__(self, fw, ui, recs, counts=(12, 1, 0), add=0):
        self.fw, self.ui, self.counts = fw, ui, counts
        self.deques = Deques(fw)
        self.names = ['Gym', 'Road Trip', 'Sleep'][:len(counts)]
        self.headers = [recs(name=n, title=n, disc=i + 1, id_=i + 1) for i, n in enumerate(self.names)]
        fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = self.deques.new(self.headers)
        self.marked: set = set()
        fw.mock('batch_get_selectitem', lambda i, *_: int(C.c_int(i).value in self.marked))
        C.c_int.from_address(PLAYLIST_ADD_MODE).value = add
        self.win = ui.window('playlist_page')
        nav = ui.child(self.win, 'view', 0, 0, 375, 30)
        ui.nodes[nav].name = 'view_navbar'
        ui.nodes[ui.child(nav, 'label')].text = 'Gym'  # stock writes the last list opened
        for action in (PL_SELECT_ALL, PL_NEW, PL_EDIT, PL_DELETE, PL_DONE):
            ui.bind(ui.child(nav, 'button'), EVT_CLICK, PL_STOCK[action], self.win)
        lv = ui.child(self.win, 'list_view', 0, 30, 375, 290)
        self.view = ui.child(lv, 'scroll_view', 0, 0, 375, 290)
        ui.nodes[self.view].name = 'scroll_view'
        C.c_ubyte.from_address(self.view + O['VIEW_VERTICAL']).value = 1
        self.ring = fw.fn('ringnav_playlist', C.c_int, C.c_void_p)
        fw.mock('stock_playlist_trampoline', lambda win, *_: self.build())

    def build(self):
        ui = self.ui
        ui.nodes[self.view].children.clear()
        if not C.c_int.from_address(PLAYLIST_ADD_MODE).value:
            tiles = ui.child(self.view, 'list_item', 0, 0, 375, 72)
            for action in (PL_IMPORT, PL_EXPORT):
                ui.bind(ui.child(tiles, 'button'), EVT_CLICK, PL_STOCK[action], self.win)
        marking = self.fw.var('g_navbar_status', C.c_ubyte).value == 1
        for i, (name, count) in enumerate(zip(self.names, self.counts)):
            item = ui.child(self.view, 'list_item', 0, 72 * (i + 1), 375, 72)
            button = ui.child(item, 'button', 0, 0, 375, 72)
            ui.bind(button, EVT_CLICK, PLAYLIST_PICK, button)
            ui.child(button, 'image', 10, 0, 52, 52)  # the art
            info = ui.child(button, 'view', 72, 0, 200, 72)
            ui.nodes[info].name = 'view_info'
            ui.nodes[ui.child(info, 'hscroll_label')].text = name
            ui.nodes[ui.child(info, 'hscroll_label')].text = f'{count} songs'
            dots = ui.child(button, 'image', 286, 0, 89, 72)
            ui.nodes[dots].name, ui.nodes[dots].image = f'img_more{i}', 'more'
            mark = ui.child(button, 'image', 330, 0, 40, 72)
            ui.nodes[mark].name, ui.nodes[mark].image, ui.nodes[mark].visible = 'img_choice', 'mulselect', int(marking)
        return 0x31

    def shown(self):
        """The list's rows as they show: (caption, second line) of each visible row."""
        rows = []
        for item in self.ui.kids(self.view):
            if not self.ui.nodes[item].visible:
                continue
            info = self.ui.lookup(item, 'view_info', 1)
            rows.append(tuple(self.ui.nodes[k].text for k in self.ui.kids(info)) if info else (self.ui.label(item),))
        return rows

    def playlist(self, i):
        """Playlist i's row button."""
        return [self.ui.kids(item)[0] for item in self.ui.kids(self.view) if self.ui.lookup(item, 'view_info', 1)][i]

    def action(self, caption):
        return self.ui.kids(self.ui.find(self.view, caption))[0]

    def title(self):
        return self.ui.nodes[self.ui.kids(self.ui.lookup(self.win, 'view_navbar', 1))[0]].text


@pytest.fixture
def rows_page(fw, ui, recs, st, stock_globals):
    use_set(st)
    return lambda **kw: Playlists(fw, ui, recs, **kw)


def test_the_picker_offers_new_playlist_and_no_dots(rows_page, ui):
    p = rows_page(add=1)
    p.ring(p.win)
    assert p.shown() == [('New Playlist…',), ('Gym', '12 Songs'), ('Road Trip', '1 Song'), ('Sleep', '0 Songs')]
    assert ui.nodes[p.win].props['_pl_mode'] == 1
    assert not any(ui.nodes[ui.lookup(p.playlist(i), f'img_more{i}', 0)].visible for i in range(3))
    assert not ui.handlers(p.win, EVT_KEY_UP)


def test_no_playlists_is_the_empty_state_under_the_rows_left(rows_page, ui, fw, recs):
    """docs/oled-design.md#empty-and-offline: no playlists, the empty state under the rows still shown, in its
    compact form (rows sit above it: no glyph, the title and hint alone, 44 px) centred in what is left, with no row of
    its own (New Playlist is a song's hold menu's); one playlist made, it goes. The picker keeps its rows alone: no
    room under them."""
    p = rows_page(counts=())
    p.ring(p.win)
    art = ui.lookup(p.win, '_empty_state', 0)
    assert art and ui.nodes[art].visible
    props = ui.nodes[art].props
    assert p.shown()  # On-The-Go's row above it
    assert (props['_es_glyph'], props['_es_title'], props['_es_hint']) == ('', 'No Playlists', 'Make one with New Playlist')
    free = 58 + 44 * len(p.shown())  # the status row's foot (V_BAR) and the rows shown, 44 px each
    assert props['_es_top'] == free + (320 - free - 44) // 2  # the title's 24 px band and the hint's 20, no button
    fw.var('fw_ctx_p_deque_playlist', C.c_uint).value = p.deques.new([recs(name='Gym', title='Gym', disc=1, id_=1)])
    fw.fn('playlists__pl_rows', None, C.c_void_p)(p.win)  # the rows again, as a rebuild or a click has them
    assert not ui.nodes[art].visible


def test_each_page_keeps_the_add_mode_it_opened_with(rows_page, ui):
    p = rows_page(add=1)
    p.ring(p.win)
    C.c_int.from_address(PLAYLIST_ADD_MODE).value = 0  # a picker over it closed
    p.ring(p.win)
    assert C.c_int.from_address(PLAYLIST_ADD_MODE).value == 1 and p.shown()[0] == ('New Playlist…',)


@pytest.mark.parametrize('left', [1, 2])
def test_a_covers_centre_opens_its_playlist_whatever_mode_a_picker_left(rows_page, ui, fw, left):
    """Cover Flow's Centre (playlist_open) runs stock's row click, which reads its one add mode global: an Add to
    Playlist picker over the covers leaves it at 1 (a song's hold) or 2 (Now Playing's), which would add to the
    playlist; the page's own mode, the list's 0, goes back first."""
    p = rows_page()
    p.ring(p.win)
    for i in range(3):
        ui.nodes[p.playlist(i)].name = str(i)  # stock names a row by its place in the live list
    C.c_int.from_address(PLAYLIST_ADD_MODE).value = left
    clicks = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: clicks.append((w, C.c_int.from_address(PLAYLIST_ADD_MODE).value)) or 0)
    fw.fn('playlist_open', None, C.c_void_p, C.c_int)(p.win, 2)  # Road Trip's cd_num
    assert clicks == [(p.playlist(1), 0)]


def recount(p, ui):
    """A playlist row's click: stock's pick (a stub: the marks are p.marked), then the rows count them."""
    stock_handler(PLAYLIST_PICK, 0)
    ui.click(p.playlist(0), 0)


@in_fork
def test_a_mark_outside_select_mode_changes_nothing(rows_page, ui, fw):
    p = rows_page()
    p.ring(p.win)
    rows = p.shown()
    p.marked = {0}
    recount(p, ui)
    assert p.shown() == rows


@pytest.mark.parametrize('failing', ['select id from playlist', 'select count(*)'])
def test_a_new_playlist_whose_lookup_fails_is_not_made(fw, failing):
    seen = []
    fw.mock('sqlite3_exec', lambda db, sql, cb, ctx, *_: seen.append(cstr(sql)) or int(cstr(sql).startswith(failing)))
    assert fw.fn('ringnav_playlist_create', C.c_int, C.c_void_p, C.c_char_p)(0x10, b'Mine') == -1
    assert seen[-1] == 'rollback' and not any(q.startswith('insert') for q in seen)


def test_leaving_the_page_otherwise_leaves_stocks_mode_alone(rows_page, ui, fw):
    p = rows_page()
    p.ring(p.win)
    fw.var('g_navbar_status', C.c_ubyte).value = 2
    ui.close(p.win)
    assert fw.var('g_navbar_status', C.c_ubyte).value == 2


@in_fork
def test_return_outside_select_mode_is_stocks(rows_page, ui):
    p = rows_page()
    p.ring(p.win)
    log = stock_handler(PLAYLIST_KEYUP, 9)
    assert ui.key(p.win, KEY_RETURN) == 9 and log[0] == p.win and not ui.timers


@in_fork
@pytest.mark.parametrize('add, status, x, passed', [
    (1, 0, PL_MARK_X + 10, 0),          # the picker has no More: a pick
])
def test_a_picker_rows_tap_anywhere_is_a_pick(rows_page, ui, fw, add, status, x, passed):
    p = rows_page(add=add)
    p.ring(p.win)
    fw.var('g_navbar_status', C.c_ubyte).value = status
    log = stock_handler(PLAYLIST_PICK, 0)
    button = p.playlist(0)
    assert ui.handlers(button, EVT_CLICK)[0] == ui.fn('pl_pick')
    ui.click(button, x)
    assert log[0] == button and log[2] == passed


@in_fork
def test_the_lit_rows_tap_opens_even_on_the_dots(rows_page, ui, st):
    p = rows_page()
    p.ring(p.win)
    log = stock_handler(PLAYLIST_PICK, 0)
    button = p.playlist(0)
    st('sel_accent', C.c_void_p).value = ui.nodes[button].parent
    ui.click(button, PL_MARK_X + 5)
    assert log[2] == 0


class Menu(C.Structure):
    _fields_ = [('w', C.c_void_p), ('at', C.c_void_p * 512), ('id', C.c_int * 512), *((k, C.c_int) for k in
                'n kind rows row height ctx'.split()), ('scope', C.c_uint)]


def menu(fw, rows):
    """The C's own menu (g_menu, which fw.var checks the mirror against), holding rows."""
    m = fw.var('g_menu', Menu)
    m.n = len(rows)
    for i, b in enumerate(rows):
        m.at[i], m.id[i] = b, i
    return m


def test_a_fresh_list_starts_on_its_first_playlist(rows_page, ui, fw):
    p = rows_page()
    p.ring(p.win)
    m = menu(fw, [ui.kids(item)[0] for item in ui.kids(p.view) if ui.nodes[item].visible])
    first = fw.fn('pl_first', C.c_int, C.POINTER(Menu), C.c_int)
    assert first(m, 0) == 0 and first(m, 3) == 3  # On-The-Go, its first row; a playlist stays


def test_a_playlist_just_made_is_where_the_list_lands_once(rows_page, ui, fw):
    """After New Playlist the reloaded list opens on the new one (an iPod's lands on what was added), then as before."""
    p = rows_page()
    p.ring(p.win)
    m = menu(fw, [ui.kids(item)[0] for item in ui.kids(p.view) if ui.nodes[item].visible])
    first = fw.fn('pl_first', C.c_int, C.POINTER(Menu), C.c_int)
    fw.var('playlists__pl_made', C.c_char * 64).value = b'Road Trip'
    assert first(m, 0) == 2 and first(m, 0) == 0  # Gym, Road Trip, Sleep below On-The-Go


def test_a_list_with_no_actions_first_starts_where_it_is(fw, ui):
    m = menu(fw, [ui.child(ui.child(0, 'list_item'), 'button') for _ in range(2)])
    assert fw.fn('pl_first', C.c_int, C.POINTER(Menu), C.c_int)(m, 0) == 0


# Go to Album and Go to Artist (qm_goto), and the classinfo they borrow.
def classinfo(fw):
    return (C.c_char * 912).from_address(C.addressof(fw.var('fw_ctx_g_local_classinfo_save', C.c_char)))


def test_go_to_album_opens_its_songs_and_puts_the_browsing_back_after(fw, ui, st, recs):
    info = classinfo(fw)
    struct.pack_into('<I', info, 0, 0xf003)
    info[0xd:0xd + 4] = b'Old\0'
    before = bytes(info)
    ui.routes['playerjumpinfo_page'] = lambda ctx: ui.window('playerjumpinfo_page')
    r = recs(album='Abbey Road', path='/mnt/mmc/a.flac', id_=4)
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(r, 1)
    assert struct.unpack_from('<I', info)[0] == 0xff10 and info[9] == b'\0'
    assert C.string_at(C.addressof(info) + 0xd) == b'Abbey Road' and ui.went == ['playerjumpinfo_page']
    page = ui.top()
    assert ui.handlers(page, EVT_WINDOW_CLOSE) == [ui.fn('qm_restore')]
    ui.fire(page, EVT_WINDOW_CLOSE)
    assert bytes(info) == before


def test_go_to_album_writes_the_artist_too(fw, ui, st, recs):
    r = recs(artist='The Beatles', path='/mnt/mmc/a.flac')
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(r, 1)
    assert C.string_at(C.addressof(classinfo(fw)) + 0x10d) == b'The Beatles'


def test_go_to_artist_opens_artistinfo_with_the_class_and_record(fw, ui, st, recs):
    seen = []
    ui.routes['localmusic/artistinfo_page'] = lambda ctx: seen.append((C.c_int.from_address(ctx).value,
                                                                         C.c_void_p.from_address(ctx + 8).value)) or ui.window('artistinfo_page')
    st('qm_cls', C.c_uint).value = 0xf004
    r = recs(artist='Nina Simone', path='/mnt/mmc/n.flac')
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(r, 0)
    assert seen == [(0xf004, r)] and ui.handlers(ui.top(), EVT_WINDOW_CLOSE) == [ui.fn('qm_restore')]


def test_a_go_to_that_opened_nothing_keeps_no_restore(fw, ui, st, recs):
    under = ui.window('playing_page')
    fw.fn('navigation__qm_goto', None, C.c_void_p, C.c_int)(recs(album='A'), 1)
    assert ui.top() == under and not ui.handlers(under, EVT_WINDOW_CLOSE)


# The queue menu: a Play/Pause hold (ringnav_keylong, qm_hold), its dialog (qm_open), a row's run (qm_run).
class Card(Files):
    """Files, appends too ("ab" keeps what the file held), with access() and fputs."""
    def __init__(self, fw, files=None):
        super().__init__(fw, files, card=True)  # places and counts are on it (card_store.c)
        fw.mock('fw_access', lambda path, *_: 0 if cstr(path) in self.files else -1)
        fw.mock('fw_fputs', lambda s, f, *_: self.fwrite(s, 1, len(C.string_at(s)), f) and 0)

    def fopen(self, path, mode, *_):
        handle = super().fopen(path, mode)
        if handle and 'a' in cstr(mode):
            self.open[handle][2] = self.files.get(cstr(path), b'')
        return handle

    def fseek(self, f, offset, whence, *_):
        state = self.open[f]
        if whence == 2 and state[2] is not None:  # an append's end is what it holds, a new file's too
            state[1] = C.c_int(offset).value + len(state[2])
            return 0
        return super().fseek(f, offset, whence)


class Hold:
    """A Play/Pause hold from ringnav_keylong to the menu's rows: Play held in stock's input status, the hold's
    timer run as the next pass, the sortselect dialog stock opens for it (its navbar's title and Return, its list
    of stock's sort rows, which the menu replaces)."""
    def __init__(self, fw, ui, st, recs, mcl):
        self.fw, self.ui, self.st, self.recs, self.mcl = fw, ui, st, recs, mcl
        self.deques, self.dialog, self.folders = Deques(fw), 0, set()
        self.card = Card(fw)
        C.c_uint64.from_address(O['MCL_POOL']).value = self.deques.new()
        ui.routes['dialog/sortselect_dialog'] = lambda ctx: self.open_dialog()
        ui.press(KEY_PLAY, 0x51)
        fw.mock('fw___xstat', lambda v, path, info, *_: setattr(C.c_uint.from_address(info + 20), 'value',
                                                               0o040755 if cstr(path) in self.folders else 0o100644))
        self.keylong = fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)

    def open_dialog(self):
        ui = self.ui
        d = ui.window('sortselect_dialog', 'dialog')
        nav = ui.child(d, 'view', 0, 0, 375, 44)
        ui.nodes[nav].name = 'view_navbar'
        ui.nodes[ui.child(nav, 'image', 0, 0, 44, 44)].name = 'img_return'
        ui.nodes[ui.child(nav, 'hscroll_label', 44, 0, 287, 44)].name = 'scrlabel_title'
        lv = ui.child(d, 'list_view', 0, 44, 375, 210)
        ui.nodes[lv].name = 'list_view'
        view = ui.child(lv, 'scroll_view', 0, 0, 375, 210)
        ui.nodes[view].name = 'scroll_view'
        for caption in ('Title', 'Artist', 'Album'):
            ui.nodes[ui.child(ui.child(view, 'list_item'), 'hscroll_label')].text = caption
        self.dialog = d

    def now_playing(self, songs, at=1, held=False):
        """Now Playing over a queue of songs, playing its at'th. Held, the queue holds its own copies of them as
        libcstl's does (Rows), for Play Next, which turns rows into place by their bytes (playback_queue_insert)."""
        if held:
            rows = Rows(self.fw)
            self.deques = rows
            C.c_uint64.from_address(O['MCL_POOL']).value = rows.new()
            self.queue = rows.rows(songs)
        else:
            self.queue = self.deques.new(songs)
        self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        self.mcl.type, self.mcl.pos = 1, at
        self.page = self.ui.window('playing_page')
        return self

    def songs_page(self, name, records, sel=0, cls=0xf001, showlist=True):
        """A page of rows, one a record of the showlist, the selection on sel as ringnav_select left it; g_class_type
        cls."""
        ui = self.ui
        self.page = ui.window(name)
        self.list = ui.child(self.page, 'scroll_view', 0, 30, 375, 290)
        C.c_ubyte.from_address(self.list + O['VIEW_VERTICAL']).value = 1
        for i, r in enumerate(records):
            item = ui.child(self.list, 'list_item', 0, 72 * i, 375, 72)
            button = ui.child(item, 'button', 0, 0, 375, 72)
            ui.bind(button, EVT_CLICK, 0x4b0000, i)  # stock's row click
            ui.nodes[ui.child(button, 'hscroll_label')].text = f'row {i}'
        if showlist:
            self.fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = self.deques.new(records)
        self.fw.var('fw_ctx_g_class_type', C.c_uint).value = cls
        self.fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(self.list, sel, len(records))
        return self

    def hold(self, key=KEY_PLAY):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, key)
        return self.keylong(None, event)

    def view(self):
        return self.ui.lookup(self.dialog, 'scroll_view', 1)

    def menu(self):
        """The menu, once the hold's pass ran: its rows, None when none opened."""
        self.ui.due('qm_open')
        return self.ui.rows(self.view()) if self.dialog in self.ui.stack else None

    def title(self):
        return self.ui.nodes[self.ui.lookup(self.dialog, 'scrlabel_title', 1)].text

    def pick(self, caption):
        self.ui.click(self.ui.find(self.view(), caption))
        self.ui.due('qm_run')


@pytest.fixture
def hold(fw, ui, st, recs, mcl):
    use_set(st)
    return Hold(fw, ui, st, recs, mcl)


def song(recs, n, **kw):
    return recs(path=f'/mnt/mmc/Music/{n}.flac', title=f'Song {n}', album=kw.pop('album', 'Abbey Road'),
                artist=kw.pop('artist', 'The Beatles'), **kw)


PLAYING_ROWS = ['Play Next', 'Play Last', 'View Queue', 'Favorite', 'Add to Playlist', 'Go to Album',
                'Go to Artist', 'Shuffle', 'Repeat', 'Group by']


def test_holding_play_on_now_playing_opens_its_menu(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    assert hold.hold() == STOP and ui.pending() == [ui.fn('qm_open')] and not hold.fw.calls('stock_keylong_trampoline')
    assert hold.menu() == PLAYING_ROWS and hold.title() == 'Song 1'
    assert ui.nodes[ui.lookup(hold.dialog, 'scrlabel_title', 1)].props['loop'] == 1  # it loops, as Now Playing's
    assert st('qm_press', C.c_ulonglong).value == 0x51


def test_now_playings_menu_offers_its_folder_and_more_and_no_rate(hold, recs, ui, st):
    """No Rate row: the rating page is the one place to rate (docs/oled-design.md#rating-page)."""
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.folders.add('/mnt/mmc/Music')
    st('rate_win', C.c_void_p).value = hold.page
    st('np_win', C.c_void_p).value = hold.page
    ui.nodes[ui.child(hold.page, 'image')].name = 'img_more'
    hold.hold()
    assert hold.menu() == PLAYING_ROWS[:7] + ['Go to Folder'] + PLAYING_ROWS[7:] + ['More…']


def test_a_favourite_reads_unfavorite(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    st('rate_win', C.c_void_p).value = hold.page
    hold.fw.mock('checkFavExist', lambda *_: 1)
    hold.hold()
    rows = hold.menu()
    assert 'Unfavorite' in rows and 'Favorite' not in rows
    assert not [r for r in rows if r.startswith('Rate')]


@pytest.mark.parametrize('fav, icon', [(1, 'heart-fill'), (0, 'heart')])
def test_a_favourites_row_shows_the_filled_heart(hold, recs, ui, st, fav, icon):
    """The user's (2026-11-20, sync-heart-1120/menu-heart-*.png): Unfavorite's symbol is Phosphor's heart (fill), drawn in
    Favorite pink-red and white on the bar (tools/art.py menu_heart-fill_*); the row to add one keeps the outline."""
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.fw.mock('checkFavExist', lambda *_: fav)
    hold.hold()
    hold.menu()
    icons = {ui.label(item): ui.nodes[item].props.get('_mm_icon') for item in ui.kids(hold.view())}
    row = [r for r in icons if r in ('Favorite', 'Unfavorite')]
    assert len(row) == 1 and icons[row[0]] == icon and (row[0] == 'Unfavorite') == bool(fav), icons


def test_go_to_is_left_out_where_its_page_is_open(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.window('artistinfo_page')
    ui.window('playerjumpinfo_page')
    hold.page = ui.window('playing_page')
    hold.hold()
    rows = hold.menu()
    assert 'Go to Album' not in rows and 'Go to Artist' not in rows


def test_an_untagged_song_has_no_go_to(hold, recs):
    hold.now_playing([recs(path=f'/mnt/mmc/{i}.flac', name=f'{i}.flac') for i in range(3)])
    hold.hold()
    rows = hold.menu()
    assert 'Go to Album' not in rows and 'Go to Artist' not in rows and hold.title() == '1'  # the name less its type


def test_now_playing_offers_a_books_chapters(hold, recs, ui):
    hold.now_playing([recs(path=f'/mnt/mmc/Music/{i}.m4b', title=f'Book {i}') for i in range(3)])
    hold.hold()
    assert hold.menu()[:3] == ['Play Next', 'Play Last', 'Chapters']


def test_return_closes_the_menu_and_other_keys_dont(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    assert ui.key(hold.dialog, KEY_PLAY) == 0 and hold.dialog in ui.stack
    assert ui.key(hold.dialog, KEY_RETURN) == STOP and hold.dialog not in ui.stack and not st('qm_dialog', C.c_void_p).value


def test_the_title_bars_return_closes_it(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    ui.click(ui.lookup(hold.dialog, 'img_return', 1))
    assert hold.dialog not in ui.stack


def test_add_to_favourites_tags_the_playing_song_alone(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Favorite')
    assert hold.dialog not in ui.stack
    assert [a[0] for _, a in hold.fw.calls('batch_init_selectrecord')] == [3]
    assert [a[0] for _, a in hold.fw.calls('batch_set_selectitem')] == [1]
    (cls, kind, songs, *_), = [a for _, a in hold.fw.calls('batch_add_file')]
    assert (cls, kind, songs & 0xffffffff) == (0xf001, 0xf00a, hold.queue)


def test_remove_from_favourites_untags_the_song(hold, recs, ui, fw):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs)
    fw.mock('checkFavExist', lambda *_: 1)
    hold.hold()
    hold.menu()
    hold.pick('Unfavorite')
    assert [a[0] for _, a in fw.calls('deleteMusicFromFav')] == [songs[1]] and fw.var('g_delete_flag', C.c_ubyte).value == 0


@pytest.mark.parametrize('row, state', [('Favorite', b'1'), ('Unfavorite', b'0')])
def test_a_favourite_from_the_menu_is_told_to_the_song_s_card_app(hold, recs, fw, row, state):
    """A song in a card app's favdir (q2-jellyfin's synced Jellyfin): the row's My Fav change goes to its fav.log."""
    hold.now_playing([recs(path=f'/mnt/mmc/Jellyfin/{i}.flac', title=f'Song {i}') for i in range(3)])
    fw.mock('checkFavExist', lambda *_: row == 'Unfavorite')
    favdir_card(fw, hold.card.files)
    hold.hold()
    hold.menu()
    hold.pick(row)
    assert hold.card.files['/mnt/mmc/.jf/fav.log'] == b'\npath\t' + state + b'\t/mnt/mmc/Jellyfin/1.flac\n'


def test_view_queue_opens_up_next_in_queue_order(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('View Queue')
    assert ui.nodes[ui.top()].name == 'upnext_page' and 'playerqueue_page' not in ui.went
    assert up_next_rows(ui) == ['Song 2']  # Song 1 plays, heading it; then the queue's order


def test_add_to_queue_appends_the_song(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs)
    hold.hold()
    hold.menu()
    hold.pick('Play Last')
    assert hold.deques[hold.queue] == songs + [songs[1]] and ui.toasts == ['Playing Last']  # what it did, in place


def test_play_next_puts_the_song_after_the_playing_one(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs, at=0, held=True)
    hold.hold()
    hold.menu()
    hold.pick('Play Next')
    want = [songs[0], songs[0], songs[1], songs[2]]
    assert [field(r, REC_PATH) for r in hold.deques[hold.queue]] == [field(r, REC_PATH) for r in want]


@pytest.mark.parametrize('row, said', [('Play Last', 'Playing Last'), ('Play Next', 'Playing Next')])
def test_oled_says_what_an_in_place_action_did(hold, recs, ui, st, row, said):
    """OLED's feedback (docs/oled-design.md#dialogs-and-toasts): every in-place action gets a toast."""
    use_set(st)
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs, at=0, held=True)
    hold.hold()
    hold.menu()
    hold.pick(row)
    assert ui.toasts == [said] and len(hold.deques[hold.queue]) == 4


# OLED's inset modal names the rows in US English, as its toasts do
@pytest.mark.parametrize('row, said', [('Favorite', 'Added to Favorites'),
                                       ('Unfavorite', 'Removed from Favorites')])
def test_oled_says_a_favourite_was_kept(hold, recs, ui, fw, st, row, said):
    use_set(st)
    hold.now_playing([song(recs, i) for i in range(3)])
    fw.mock('checkFavExist', lambda *_: row == 'Unfavorite')
    hold.hold()
    hold.menu()
    hold.pick(row)
    assert ui.toasts == [said]


def test_a_queue_changed_under_the_menu_is_left_alone(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs)
    hold.hold()
    hold.menu()
    hold.deques[hold.queue].append(song(recs, 9))
    hold.pick('Play Last')
    assert ui.toasts == ['Queue unchanged'] and len(hold.deques[hold.queue]) == 4


def test_go_to_folder_opens_the_songs_folder(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.folders.add('/mnt/mmc/Music')
    seen = []
    ui.routes['folder_page'] = lambda ctx: seen.append((st('go_layer').value, C.string_at(C.addressof(st('go_folder', C.c_char)))))
    hold.hold()
    hold.menu()
    hold.pick('Go to Folder')
    assert seen == [(3, b'/mnt/mmc/Music')] and st('go_layer').value == 0  # never left for a later visit


def test_a_folder_gone_since_says_so(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.folders.add('/mnt/mmc/Music')
    hold.hold()
    hold.menu()
    hold.folders.clear()
    hold.pick('Go to Folder')
    assert ui.toasts == ['Folder not found'] and 'folder_page' not in ui.went


def test_go_to_album_from_the_menu(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Go to Album')
    assert ui.went[-1] == 'playerjumpinfo_page'
    assert C.string_at(C.addressof(classinfo(hold.fw)) + 0xd) == b'Abbey Road'


# Now Playing's art, artist and album as links (np_taps.c np_tap, on the window): the art and album go to the album,
# the artist to the artist, as the menu's Go to rows, when the touch lifts within TAP_PX of where it came down.
class Taps:
    """Now Playing's art page at rest in its slide_view (the iPod layout's boxes, on screen at the art 16,42 166x166,
    the artist 194,92 165x22 and the album 194,120 165x22), playing the second of three songs."""
    def __init__(self, hold, recs, ui, st, fw):
        self.ui, self.st, self.fw = ui, st, fw
        hold.now_playing([song(recs, i) for i in range(3)])
        self.page = hold.page
        self.slide = ui.child(self.page, 'slide_view', 0, 40, 375, 186)
        art_page = ui.child(self.slide, 'view', 0, 0, 375, 186)
        self.cover = ui.child(art_page, 'image', 16, 2, 166, 166)
        self.artist = ui.child(art_page, 'hscroll_label', 194, 52, 165, 22)
        self.album = ui.child(art_page, 'label', 194, 80, 165, 22)
        for name, w in (('np_win', self.page), ('np_slide', self.slide), ('np_cover', self.cover),
                        ('np_artist', self.artist), ('np_album', self.album)):
            st(name, C.c_void_p).value = w
        ui.routes['playerjumpinfo_page'] = lambda ctx: ui.window('playerjumpinfo_page')
        ui.routes['localmusic/artistinfo_page'] = lambda ctx: ui.window('artistinfo_page')
        self.np_tap = fw.fn('np_tap', C.c_int, C.c_void_p, C.c_void_p)

    def touch(self, kind, x, y):
        event = C.create_string_buffer(0x40)
        self.ui.pointer_event(C.addressof(event), kind, self.page, x, y)
        return self.np_tap(None, C.addressof(event))

    def tap(self, x, y, to=None, moves=()):
        """A touch down at (x, y), through moves, lifted at to (else where it came down); the pass after. The answers
        of each event, which must all be 0 so the touch goes on to the child that has it."""
        answers = [self.touch(O['EVT_POINTER_DOWN'], x, y)]
        answers += [self.touch(O['EVT_POINTER_MOVE_BEFORE'], *m) for m in moves]
        answers.append(self.touch(EVT_POINTER_UP_BEFORE, *(to or (x, y))))
        self.ui.due()
        return answers

    def lit(self, w):
        return self.fw.fn('np_tap_lit', C.c_int, C.c_void_p)(w)


@pytest.fixture
def taps(hold, recs, ui, st, fw):
    return Taps(hold, recs, ui, st, fw)


def test_a_tap_on_the_artist_opens_the_artist(taps, ui, st):
    assert taps.tap(250, 100) == [0, 0]
    assert ui.went == ['localmusic/artistinfo_page'] and C.c_int.from_address(ui.contexts[-1][1]).value == 0xf001
    assert ui.handlers(ui.top(), EVT_WINDOW_CLOSE) == [ui.fn('qm_restore')]  # as the menu's Go to Artist


@pytest.mark.parametrize('x, y', [(250, 128), (100, 120)], ids=['album', 'art'])
def test_a_tap_on_the_album_or_the_art_opens_the_album(taps, ui, hold, x, y):
    taps.tap(x, y)
    assert ui.went == ['playerjumpinfo_page']
    assert C.string_at(C.addressof(classinfo(hold.fw)) + 0xd) == b'Abbey Road'


def test_the_link_is_lit_while_held_and_only_there(taps, ui):
    taps.touch(O['EVT_POINTER_DOWN'], 250, 100)
    assert taps.lit(taps.artist) and not taps.lit(taps.album) and not taps.lit(taps.cover)
    taps.touch(O['EVT_POINTER_MOVE_BEFORE'], 255, 104)  # a finger's wobble keeps it
    assert taps.lit(taps.artist)
    taps.touch(O['EVT_POINTER_MOVE_BEFORE'], 290, 100)  # a swipe starting: no longer
    assert not taps.lit(taps.artist)
    taps.touch(O['EVT_POINTER_DOWN'], 250, 128)
    taps.touch(EVT_POINTER_ABORT, 250, 128)  # the slide_view took it
    assert not taps.lit(taps.album) and not ui.pending()


# A hold on the art (npt_hold, HOLD_MS after the touch came down) opens stock's full-screen art instead.
def hold_art(taps, ui, moves=()):
    taps.touch(O['EVT_POINTER_DOWN'], 100, 120)
    for m in moves:
        taps.touch(O['EVT_POINTER_MOVE_BEFORE'], *m)
    ui.due('npt_hold')
    taps.touch(EVT_POINTER_UP_BEFORE, *(moves[-1] if moves else (100, 120)))
    ui.due()


def test_a_hold_on_the_art_opens_it_full_screen_and_its_lift_is_no_tap(taps, ui, fw):
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    taps.touch(O['EVT_POINTER_DOWN'], 100, 120)
    assert [ms for fn, _, ms in ui.timers.values() if fn == ui.fn('npt_hold')] == [500]
    taps.touch(O['EVT_POINTER_MOVE_BEFORE'], 104, 126)  # a finger's wobble keeps the hold
    ui.due('npt_hold')
    assert ui.went == ['bigcover_page']
    taps.touch(EVT_POINTER_UP_BEFORE, 104, 126)
    ui.due()
    assert ui.went == ['bigcover_page']  # never the album too
    taps.tap(100, 120)  # the next touch is a tap again
    assert ui.went == ['bigcover_page', 'playerjumpinfo_page']


def test_the_holds_lift_never_reaches_the_full_screen_art_it_opened_but_the_next_tap_does(taps, ui, fw):
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    ui.routes['bigcover_page'] = lambda ctx: ui.window('bigcover_page')
    taps.touch(O['EVT_POINTER_DOWN'], 100, 120)
    ui.due('npt_hold')
    big = ui.top()
    dispatch = fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)

    def send(kind):
        event = C.create_string_buffer(0x40)
        ui.pointer_event(C.addressof(event), kind, big, 100, 120)
        return dispatch(big, C.addressof(event))
    assert [send(k) for k in (EVT_POINTER_UP_BEFORE, EVT_POINTER_UP_BEFORE - 1, EVT_CLICK)] == [STOP] * 3
    assert not ui.dispatched  # stock's pointer-up, which closes the page, never ran
    send(O['EVT_POINTER_DOWN'])
    send(EVT_POINTER_UP_BEFORE - 1)  # a later tap closes it, as stock's does
    assert ui.dispatched == [(big, O['EVT_POINTER_DOWN']), (big, EVT_POINTER_UP_BEFORE - 1)]


def test_a_hold_that_moves_or_a_stopped_player_opens_no_full_screen(taps, ui, fw):
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    hold_art(taps, ui, moves=((140, 120),))  # a swipe that stays down
    fw.mock('mclGetPlayStatus', lambda *_: 1)
    hold_art(taps, ui)
    assert 'bigcover_page' not in ui.went


def test_a_streams_art_holds_to_full_screen_though_its_tap_goes_nowhere(taps, ui, hold, fw):
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    hold.mcl.type = 0x2
    taps.tap(100, 120)
    assert not ui.went
    taps.touch(O['EVT_POINTER_DOWN'], 100, 120)
    assert taps.fw.fn('np_tap_lit', C.c_int, C.c_void_p)(taps.cover) == 0  # the art is no text
    ui.due('npt_hold')
    assert ui.went == ['bigcover_page']


@pytest.mark.parametrize('to, moves', [((300, 120), ()), ((100, 120), ((200, 120),)), ((111, 120), ())],
                         ids=['swipe-off', 'swipe-back', 'past-slop'])
def test_a_swipe_or_a_drag_goes_nowhere(taps, ui, to, moves):
    assert taps.tap(100, 120, to, moves) == [0] * (2 + len(moves))
    assert not ui.went and not ui.pending()


def swiped_up(taps, ui, at, to):
    """A touch down at at, lifted at to: 1 when it asked for Up Next (np_queue on the next pass)."""
    for kind, xy in ((O['EVT_POINTER_DOWN'], at), (EVT_POINTER_UP_BEFORE, to)):
        taps.touch(kind, *xy)
    asked = ui.pending().count(ui.fn('np_queue'))
    ui.timers.clear()
    return asked


def test_a_swipe_up_on_the_arts_page_opens_up_next(taps, ui):
    assert swiped_up(taps, ui, (100, 200), (104, 140)) == 1  # the art: 60 px up, 4 across
    assert swiped_up(taps, ui, (100, 200), (100, 160)) == 0  # 40 px: short of SWIPE_UP_PX
    assert swiped_up(taps, ui, (100, 200), (160, 140)) == 0  # as much across as up: the slide_view's


@pytest.mark.parametrize('at', [(158, 286), (146, 270), (311, 300), (200, 250)],
                         ids=['radio-pill', 'antenna', 'up-next', 'times'])
def test_a_swipe_up_that_came_down_under_the_arts_page_opens_nothing(taps, ui, at):
    """The user's, 2026-11-18 00:05: a tap on Radio opened Up Next. np_mode_click gives every x of the pill to Radio
    (test_nav_painters.py); the window's swipe up took any touch, so a press on the state row that rolled 48 px up as
    it lifted opened the queue, its lift off the row so Radio got no click. Only the art's page (the slide_view, above
    the bar) starts it."""
    x, y = at
    assert swiped_up(taps, ui, at, (x, y - 60)) == 0


def test_off_the_art_page_or_with_lyrics_full_screen_nothing_is_a_link(taps, ui, st):
    ui.nodes[taps.slide].props['value'] = 1  # the lyrics page in front
    taps.tap(250, 100)
    ui.nodes[taps.slide].props['value'] = 0
    st('np_full').value = 1
    taps.tap(100, 120)
    st('np_full').value = 0
    taps.tap(250, 160)  # under the album: the heart's row
    taps.tap(200, 253)  # the bar
    assert not ui.went


def test_a_stream_or_a_go_to_already_open_goes_nowhere_and_lights_nothing(taps, ui, hold):
    hold.mcl.type = 0x2  # not a Library or folder queue: a stream's
    taps.touch(O['EVT_POINTER_DOWN'], 250, 100)
    assert not taps.lit(taps.artist)
    taps.tap(250, 100)
    hold.mcl.type = 1
    ui.window('artistinfo_page')  # Go to Artist's page is open, under this one
    ui.stack.append(ui.stack.pop(-2))
    taps.tap(250, 100)
    assert not ui.went


def test_the_arts_click_is_never_stocks_enlarged_art_and_the_hearts_still_is(taps, ui, fw):
    dispatch = fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)
    heart = ui.child(taps.page, 'image', 300, 150, 50, 50)
    for w in (taps.cover, heart):
        event = C.create_string_buffer(0x40)
        ui.pointer_event(C.addressof(event), EVT_CLICK, w, 10, 10)
        dispatch(w, C.addressof(event))
    assert ui.dispatched == [(heart, EVT_CLICK)]


def test_more_clicks_now_playings_own_more(hold, recs, ui, st):
    hold.now_playing([song(recs, i) for i in range(3)])
    st('np_win', C.c_void_p).value = hold.page
    more = ui.child(hold.page, 'image')
    ui.nodes[more].name = 'img_more'
    hold.hold()
    hold.menu()
    hold.pick('More…')
    assert ui.dispatched == [(more, EVT_CLICK)]


def test_more_opens_the_apps_page_while_its_queue_plays(hold, recs, ui, st):
    # addon.c addon_more: an app's q2app said more=, and the queue playing is the one its .play started.
    from test_addon import Ap, Qa
    hold.now_playing([song(recs, i) for i in range(3)])
    st('np_win', C.c_void_p).value = hold.page
    ui.nodes[ui.child(hold.page, 'image')].name = 'img_more'
    ap = hold.fw.var('addon__ap', Ap)
    ap.n, a = 1, ap.a[0]
    a.id, a.name, a.more, a.bin, a.pid, a.launched = b'foo', b'Foo', b'playlists', b'/mnt/mmc/.foo/q2-foo', b'/tmp/q2-foo', 1
    hold.card.files.update({'/mnt/mmc/.foo/q2-foo': b'', '/tmp/q2-foo': b'1'})
    qa = hold.fw.var('addon__qa', Qa)
    qa.app, qa.loads = C.addressof(a), hold.fw.var('playback_loads', C.c_uint).value
    hold.hold()
    hold.menu()
    hold.pick('More…')
    assert ui.dispatched == [] and ui.nodes[ui.top()].name == 'addon_page'


class Rect(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('w', C.c_int), ('h', C.c_int)]


@pytest.mark.parametrize('image, selected, strokes, fills', [('switch_on', 0, 0, 2), ('switch_off', 1, 1, 1)])
def test_oled_more_shows_ab_repeat_as_the_themes_switch(fw, ui, st, image, selected, strokes, fills):
    """Now Playing's More under a modal set: A-B Repeat's stock switch hides and its row paints the theme's switch
    (list_marks.c switch_mark, rule 7) by the hidden image's state, its label ending short of it: a track and a knob,
    or on the selection when off, an outline round the knob."""
    use_set(st)
    win = ui.window('playermore_page', 'dialog')
    lv = ui.child(win, 'list_view', 0, 44, 375, 270)
    ui.nodes[lv].name = 'list_view_more'
    view = ui.child(lv, 'scroll_view', 0, 0, 375, 270)
    ui.nodes[view].name = 'scroll_view_more'
    button = ui.child(ui.child(view, 'list_item', 0, 0, 375, 70), 'button', 0, 0, 375, 70)
    label = ui.child(button, 'hscroll_label', 20, 0, 280, 70)
    ui.nodes[label].text = 'AB Repeat'
    sw = ui.child(button, 'image', 300, 10, 50, 50)
    ui.nodes[sw].image = image
    fw.fn('modal_more', None, C.c_void_p)(win)
    right = 375 - 28  # the house grid's right column (OLED's right_x), the row the screen's width
    x, _, w, _ = ui.nodes[label].rect()
    assert not ui.nodes[sw].visible and x + w <= right - 40
    assert ui.fn('switch_flipped') in ui.handlers(button, EVT_CLICK)  # stock flips the hidden image: the row repaints
    lcd = C.create_string_buffer(0x400)
    canvas = C.create_string_buffer(0x400)
    struct.pack_into('<Q', canvas, O['CANVAS_LCD'], C.addressof(lcd))
    fw.mock('canvas_get_vgcanvas', lambda *_: 1)
    paint = fw.fn('modal_row_paint', None, C.c_void_p, C.c_void_p, C.c_void_p, Rect, C.c_int)
    paint(view, C.addressof(canvas), button, Rect(0, 0, 375, 32), selected)
    assert len(fw.calls('vgcanvas_stroke')) == strokes and len(fw.calls('vgcanvas_fill')) == fills


def test_oled_more_speed_rows_hidden_buttons_take_no_taps(fw, ui, st):
    """Now Playing's More under a modal set: Speed's four stock buttons hide, and stock's hit test skips only an
    insensitive widget, hidden or not, so a tap on the row's left half set 1x behind the picker it opened. Each is
    made insensitive; a pick still clicks the one it names (more_speed_pick dispatches to it)."""
    use_set(st)
    win = ui.window('playermore_page', 'dialog')
    lv = ui.child(win, 'list_view', 0, 44, 375, 270)
    ui.nodes[lv].name = 'list_view_more'
    view = ui.child(lv, 'scroll_view', 0, 0, 375, 270)
    ui.nodes[view].name = 'scroll_view_more'
    button = ui.child(ui.child(view, 'list_item', 0, 0, 375, 70), 'button', 0, 0, 375, 70)
    label = ui.child(button, 'hscroll_label', 72, 12, 40, 20)
    ui.nodes[label].text = 'Multiple Speed'
    speeds = []
    for i, x in enumerate((120, 177, 231, 283)):  # as stock places them (test/patch.py's More)
        speeds.append(ui.child(button, 'image', x, 9, 42, 61))
        ui.nodes[speeds[-1]].name = f'img_forward_{i}'
    fw.fn('modal_more', None, C.c_void_p)(win)
    insensitive = {a[0] for _, a in fw.calls('widget_set_sensitive') if not C.c_int(a[1]).value}
    assert all(not ui.nodes[s].visible for s in speeds) and set(speeds) <= insensitive


def advanced(fw, kind, value):
    """Playback's own Shuffle, Repeat or Group by, as its menu sets them."""
    return fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(kind, value)


def test_shuffle_albums_puts_skipping_the_album_first(hold, recs, ui, fw):
    hold.now_playing([song(recs, i, album=a) for i, a in enumerate(('A', 'A', 'B'))])
    assert advanced(fw, 0, 3)
    hold.hold()
    assert hold.menu()[:2] == ['Next Album', 'Previous Album']


def test_a_skip_with_no_other_album_says_so(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    advanced(fw, 0, 3)
    hold.hold()
    hold.menu()
    hold.pick('Next Album')
    assert ui.toasts == ['No other group']


def test_view_queue_under_advanced_shuffle_is_up_next(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    advanced(fw, 0, 1)
    hold.hold()
    hold.menu()
    hold.pick('View Queue')
    assert ui.nodes[ui.top()].name == 'upnext_page' and 'playerqueue_page' not in ui.went


def up_next_rows(ui):
    """Up Next's songs after the playing one, its page the top window, by the title each row reads as."""
    lv, = [k for k in ui.kids(ui.top()) if ui.nodes[k].kind == 'list_view']
    return [ui.nodes[k].props['_un_title'] for k in ui.kids(ui.kids(lv)[0])
            if ui.nodes[k].kind == 'list_item' and '_un_title' in ui.nodes[k].props]


@pytest.mark.parametrize('seed', [1, 0x9e3779b9, 0x2545f491])  # whatever order the shuffle draws
def test_a_play_next_stocks_shuffle_holds_goes_first_in_up_next(hold, recs, ui, fw, mcl, seed):
    hold.now_playing([song(recs, i) for i in range(8)], at=0, held=True)
    mcl.mode = 2  # stock's Shuffle
    fw.var('playback__rng', C.c_uint).value = seed
    hold.hold()
    hold.menu()
    hold.pick('Play Next')  # Song 0, playing, once more after itself
    hold.hold()
    hold.menu()
    hold.pick('View Queue')
    assert ui.nodes[ui.top()].name == 'upnext_page' and up_next_rows(ui)[0] == 'Song 0'


SHUFFLE = ['Off', 'All Songs', 'Songs in Album', 'Albums', 'Albums and Songs']  # stock's play modes, in its order
REPEAT = ['Play One Song', 'Play One Album', 'Play All', 'Repeat Song', 'Repeat Album', 'Repeat All']


def ticked_rows(ui, view):
    """The rows the inset modal checks (menu_modal.c modal_row's icon)."""
    return [ui.label(item) for item in ui.kids(view) if ui.nodes[item].props.get('_mm_icon') == 'check']


@pytest.mark.parametrize('caption, title, rows, ticked', [
    ('Shuffle', 'Shuffle', SHUFFLE, 'Off'),
    ('Repeat', 'Repeat', REPEAT, 'Play All'),
])
def test_a_play_mode_row_opens_its_choices_on_the_current_one(hold, recs, ui, caption, title, rows, ticked):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick(caption)
    assert hold.title() == title and ui.rows(hold.view()) == rows
    assert ticked_rows(ui, hold.view()) == [ticked] and ui.nodes[hold.view()].props['_ringnav_index'] == rows.index(ticked)


def test_settings_show_their_value_alone_and_group_by_flips_in_place(hold, recs, ui, fw):
    """The sheet's setting rows say their value without the label's word (audit2 L3): Shuffle Off, Repeat Off (stock's
    Play All), Group by Album. Group by, two choices, flips on Centre with the sheet left open, and the other rows'
    values follow it (Shuffle's and Repeat's name the group)."""
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    advanced(fw, 1, REPEAT.index('Repeat Album'))

    def values():
        return {ui.label(r): ui.nodes[r].props.get('_mm_value') for r in ui.items(hold.view())
                if ui.label(r) in ('Shuffle', 'Repeat', 'Group by')}
    hold.pick('Group by')
    assert hold.dialog in ui.stack and fw.fn('playback_option', C.c_int, C.c_int)(2) == 1
    assert values() == {'Shuffle': 'Off', 'Repeat': 'Folder', 'Group by': 'Folder'}
    hold.pick('Group by')
    assert fw.fn('playback_option', C.c_int, C.c_int)(2) == 0
    assert values() == {'Shuffle': 'Off', 'Repeat': 'Album', 'Group by': 'Album'}


def test_a_choice_picked_is_played_and_ticked_next_time(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    hold.pick('Repeat')
    hold.pick('Repeat All')  # from Play All
    assert hold.dialog not in ui.stack and not ui.toasts
    assert fw.fn('playback_option', C.c_int, C.c_int)(1) == REPEAT.index('Repeat All')
    hold.hold()
    assert hold.menu() == PLAYING_ROWS  # the next hold is the menu again, not the choice
    hold.pick('Repeat')
    assert ticked_rows(ui, hold.view()) == ['Repeat All']


def test_oled_draws_the_menu_as_the_sheet(fw, ui, st, recs, mcl):
    """A modal set's hold menu (menu_modal.c): no title bar, a sheet whose list runs from y 50 to the screen's foot
    (280 px lower until its first paint starts the rise), the header's 72 px heading its rows, which span the screen
    32 px each, so the first is on the house grid at y 122; the settings read as settings, US English, and the menu
    opens on its first row."""
    use_set(st)
    hold = Hold(fw, ui, st, recs, mcl).now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    rows = hold.menu()
    lv = ui.lookup(hold.dialog, 'list_view', 1)
    assert tuple(ui.nodes[lv].rect()) == (0, 50 + 280, 375, 270)  # below the screen until its first paint's rise
    assert not ui.nodes[ui.lookup(hold.dialog, 'view_navbar', 1)].visible
    head = ui.kids(hold.view())[0]
    assert ui.nodes[head].name == '_mm_head' and tuple(ui.nodes[head].rect()) == (0, 0, 375, 72)
    assert ui.nodes[ui.items(hold.view())[0]].rect()[1] == 72
    assert all(tuple(ui.nodes[item].rect()[2:]) == (375, 32) for item in ui.items(hold.view()))
    assert rows[:4] == ['Play Next', 'Play Last', 'View Queue', 'Favorite'] and 'Group by' in rows
    assert ui.prop(hold.view(), '_ringnav_index', -1) == 0


def test_oled_menu_on_songs_has_no_sorting_row(fw, ui, st, recs, mcl):
    """Songs (0xf001), whose action row holds stock's Sort: its order is Playback > Library > Sorting's, so the menu
    starts with its actions and opens on Play Next."""
    use_set(st)
    hold = Hold(fw, ui, st, recs, mcl).songs_page('allmusic_page', [song(recs, i) for i in range(3)])
    bar = ui.child(hold.page, 'view', 0, 0, 375, 32)
    for _ in range(3):  # its label, Play All's image, Sort's bars
        ui.child(bar, 'image')
    st('action_view', C.c_void_p).value = bar
    hold.hold()
    rows = hold.menu()
    assert rows[0] == 'Play Next' and 'Sorting' not in rows and None not in rows
    assert ui.prop(hold.view(), '_ringnav_index', -1) == 0


def test_add_to_playlist_opens_the_picker_guarded_by_the_menus_record(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    picker = []
    ui.routes['localmusic/playlist_page'] = lambda ctx: picker.append(ctx) or ui.window('playlist_page')
    hold.hold()
    hold.menu()
    hold.pick('Add to Playlist')
    assert picker == [0x20000 | 0xf001] and st('qm_playlist', C.c_void_p).value == ui.top()
    assert ui.handlers(ui.top(), EVT_DESTROY) == [ui.fn('qm_playlist_gone')]
    armed = dict(ui.timers)  # the modal menu's own (menu_modal.c modal_lit)
    assert hold.hold() == 0 and ui.timers == armed  # no menu over the picker: stock's hold
    ui.close(ui.top())
    assert not st('qm_playlist', C.c_void_p).value


def test_the_pickers_rows_are_rebound_to_the_guard_after_each_rebuild(fw, ui, st, recs, mcl, stock_globals):
    use_set(st)
    p = Playlists(fw, ui, recs, add=1)
    st('qm_playlist', C.c_void_p).value = p.win
    p.ring(p.win)
    assert all(ui.handlers(p.playlist(i), EVT_CLICK) == [ui.fn('qm_playlist_pick')] for i in range(3))


@in_fork
def test_a_pick_in_the_guarded_picker_adds_the_menus_song(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.routes['localmusic/playlist_page'] = lambda ctx: ui.window('playlist_page')
    hold.hold()
    hold.menu()
    hold.pick('Add to Playlist')
    row = ui.child(ui.top(), 'button')
    ui.bind(row, EVT_CLICK, ui.fn('qm_playlist_pick'), row)
    log = stock_handler(PLAYLIST_PICK, 0)
    ui.click(row, 200)
    # a toolbar-less picker has no dots: its pick is a click at x 0 (playlists.c qm_playlist_pick)
    assert log[0] == row and log[2] == 0 and [a[0] for _, a in fw.calls('batch_set_selectitem')][-1] == 1


@in_fork
def test_a_pick_after_the_queue_changed_adds_nothing(hold, recs, ui, st, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.routes['localmusic/playlist_page'] = lambda ctx: ui.window('playlist_page')
    hold.hold()
    hold.menu()
    hold.pick('Add to Playlist')
    row = ui.child(ui.top(), 'button')
    ui.bind(row, EVT_CLICK, ui.fn('qm_playlist_pick'), row)
    log = stock_handler(PLAYLIST_PICK, 0)
    hold.mcl.pos = 2
    assert ui.click(row) == 0 and ui.toasts == ['Queue unchanged'] and log[0] == 0


# The hold on a list's row.
def test_holding_play_on_a_song_row_opens_the_songs_menu(hold, recs, ui):
    songs = [song(recs, i) for i in range(3)]
    hold.songs_page('allmusic_page', songs, sel=2)
    assert hold.hold() == STOP
    assert hold.menu() == ['Play Next', 'Play Last', 'Add to On-The-Go', 'Add to Playlist…', 'Favorite', 'Go to Album',
                           'Go to Artist']
    assert hold.title() == 'Song 2'


def test_a_songs_play_next_on_an_empty_queue_loads_it_unplayed(hold, recs, ui, fw, mcl):
    songs = [song(recs, i) for i in range(3)]
    hold.songs_page('allmusic_page', songs, sel=0)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = hold.deques.new()
    mcl.type = 1
    loaded = []
    fw.mock('mclLoadPlayList', lambda q, at, cls, *_: loaded.append((list(hold.deques[q]), at, cls)))
    hold.hold()
    hold.menu()
    hold.pick('Play Next')
    assert loaded == [([songs[0]], 0, 0xf001)] and not fw.calls('mclStartPlayer')


def test_my_favs_remove_reloads_my_fav(hold, recs, ui, fw):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(2)], sel=1, cls=0xf00a)
    fw.mock('checkFavExist', lambda *_: 1)
    hold.hold()
    hold.menu()
    hold.pick('Unfavorite')
    assert fw.var('g_delete_flag', C.c_ubyte).value == 1  # My Fav reloads, as Now Playing's heart


def test_a_list_changed_under_the_menu_runs_nothing(hold, recs, ui, fw):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=1)
    hold.hold()
    hold.menu()
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), b'/mnt/mmc/x\0', 11)  # browsing moved on
    hold.pick('Favorite')
    assert not fw.calls('batch_add_file') and ui.toasts == ['Queue unchanged']


def test_an_album_rows_menu_shuffles_and_goes_to_its_artist(hold, recs, ui):
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=1, cls=0xf003)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Play Last', 'Shuffle', 'Favorite', 'Add to Playlist', 'Go to Artist']
    assert hold.title() == 'Revolver'


def test_an_albums_favourite_is_kept_for_its_next_menu(hold, recs, ui):
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=1, cls=0xf003)
    hold.hold()
    hold.menu()
    hold.pick('Favorite')
    assert not ui.toasts and '/mnt/data/ringnav-album-ratings' in hold.card.files
    hold.hold()
    rows = hold.menu()
    assert 'Unfavorite' in rows and not [r for r in rows if r.startswith('Rate')]


@pytest.mark.parametrize('sel, opened', ((1, [2]), (0, [])))  # 2: DISPLAY_COVERFLOW
def test_a_hold_on_homes_cover_flow_row_opens_displays_cover_flow_page(hold, recs, ui, fw, sel, opened):
    hold.songs_page('home_page', [recs(), recs()], sel=sel, showlist=False)
    ui.nodes[ui.kids(ui.kids(hold.list)[1])[0]].name = 'img_cover_flow'
    seen = []
    ui.routes['systemset/display_page'] = lambda ctx: seen.append(fw.var('navigation__display_sub', C.c_int).value)
    assert hold.hold() == (STOP if opened else 0)  # another row's hold stays stock's
    assert seen == opened and hold.menu() is None


@pytest.mark.parametrize('cls, field_', [(0xf004, 'artist'), (0xf005, 'composer'), (0xf006, 'genre')])
def test_an_artist_composer_or_genre_row_is_titled_by_its_own_tag(hold, recs, ui, cls, field_):
    rows = [recs(kind=4, **{field_: f'{field_} {i}'}) for i in range(2)]
    hold.songs_page('localclass_page', rows, sel=1, cls=cls)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Play Last', 'Shuffle', 'Add to Playlist'] and hold.title() == f'{field_} 1'


def test_a_folder_rows_menu(hold, recs, ui, fw):
    rows = [recs(name=n, kind=4) for n in ('Jazz', 'Rock')]
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), b'/mnt/mmc/Music\0', 15)
    hold.songs_page('folder_page', rows, sel=0)
    hold.hold()
    assert hold.menu() == ['Play Next', 'Play Last', 'Shuffle', 'Add to Playlist'] and hold.title() == 'Jazz'


def test_most_playeds_tracks_hold_as_its_songs(hold, recs, ui, st, fw):
    songs = [song(recs, i) for i in range(3)]
    hold.songs_page('mostplayed_page', songs, sel=1, showlist=False, cls=0xf003)  # the Library last listed albums
    st('mp_page', C.c_void_p).value = hold.page
    st('mp_list', C.c_void_p).value = mp = hold.deques.new(songs)
    hold.hold()
    rows = hold.menu()
    assert hold.title() == 'Song 1' and 'Favorite' in rows
    hold.pick('Favorite')
    (cls, kind, tracks, *_), = [a for _, a in fw.calls('batch_add_file')]
    assert (cls, kind, tracks & 0xffffffff) == (0xf001, 0xf00a, mp)  # its own tracks, as songs whatever the Library


# What a hold leaves stock.
@pytest.mark.parametrize('how', ['dialog', 'timer', 'picker', 'dark', 'animating', 'touch', 'airplay', 'select mode',
                                 'not a list', 'stream', 'no key', 'video', 'rows changed'])
def test_a_hold_that_isnt_the_menus_is_stocks(hold, recs, ui, st, fw, mcl, how):
    songs = [song(recs, i) for i in range(3)]
    if how in ('not a list', 'video', 'rows changed'):
        rows = songs if how != 'video' else [recs(path=f'/mnt/mmc/{i}.mp4', kind=8) for i in range(3)]
        hold.songs_page('notes_page' if how == 'not a list' else 'allmusic_page', rows)
        if how == 'rows changed':
            hold.deques[fw.var('fw_ctx_p_deque_showlist', C.c_uint).value].append(songs[0])
    else:
        hold.now_playing(songs)
    {'dialog': lambda: setattr(st('qm_dialog', C.c_void_p), 'value', 1),
     'timer': lambda: setattr(st('qm_timer', C.c_uint), 'value', 5),
     'picker': lambda: setattr(st('qm_playlist', C.c_void_p), 'value', 1),
     'dark': lambda: setattr(fw.var('g_backlight_status', C.c_ubyte), 'value', 0),
     'animating': lambda: fw.mock('window_manager_is_animating', lambda *_: 1),
     'touch': lambda: setattr(ui, 'pressed', 1),
     'airplay': lambda: fw.mock('airplayGetFlag', lambda *_: 2),
     'select mode': lambda: setattr(fw.var('g_navbar_status', C.c_ubyte), 'value', 1),
     'stream': lambda: setattr(mcl, 'type', 0x2000),
     'no key': lambda: ui.press(0x99)}.get(how, lambda: None)()
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold() == 0x77 and ui.went == [] and not ui.timers


def test_a_hold_on_a_page_with_no_list_is_stocks(hold, ui, fw):
    ui.window('allmusic_page')
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold() == 0x77 and not ui.timers


def test_another_key_held_is_stocks(hold, recs, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    fw.mock('stock_keylong_trampoline', lambda *_: 0x77)
    assert hold.hold(KEY_RETURN) == 0x77 and fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0x77


def test_a_menu_whose_record_went_before_it_opened_opens_nothing(hold, recs, ui, mcl):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    mcl.pos = 0
    assert hold.menu() is None and ui.went == []


def test_a_menu_that_opened_something_else_closes_it(hold, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    ui.routes['dialog/sortselect_dialog'] = lambda ctx: ui.window('msginfo_dialog')
    hold.hold()
    ui.due('qm_open')
    assert [ui.nodes[w].name for w in ui.closed] == ['msginfo_dialog']


def test_a_run_after_the_menu_closed_does_nothing(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    ui.click(ui.find(hold.view(), 'Favorite'))
    ui.key(hold.dialog, KEY_RETURN)
    ui.due('qm_run')
    assert not fw.calls('batch_add_file')


def test_a_second_click_before_the_run_is_dropped(hold, recs, ui, fw):
    hold.now_playing([song(recs, i) for i in range(3)])
    hold.hold()
    hold.menu()
    ui.click(ui.find(hold.view(), 'Favorite'))
    ui.click(ui.find(hold.view(), 'View Queue'))
    ui.due('qm_run')
    assert [a[1] for _, a in fw.calls('batch_add_file')] == [0xf00a] and 'playerqueue_page' not in ui.went


# Quick Settings and the lock window under a held key.
def test_oled_s_quick_settings_keeps_the_wheel_on_its_rows_through_a_hold(fw, ui):
    """OLED's wheel walks its rows (navigation.c quick_walk): a held Play/Pause there is stock's, as on any page."""
    def config(path, section, key, out, default, *_):
        value = b'3' if C.string_at(key) == b'THEME' else C.string_at(default)  # IPOD THEME 3, OLED
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', config)
    ui.window('statusbar_dialog')
    event = C.create_string_buffer(0x40)
    struct.pack_into('<i', event, EVENT_KEY, KEY_PLAY)
    fw.fn('ringnav_keylong', C.c_int, C.c_void_p, C.c_void_p)(None, event)
    assert not fw.calls('write_int_config') and fw.calls('stock_keylong_trampoline')


# A press whose release never reached the window manager (stale_heal) and a pull-down left open (quick_poll).
def test_a_press_stuck_past_the_limit_is_aborted_and_released_where_it_was(fw, ui, st):
    page = ui.window('allmusic_page')
    now = [100000]
    fw.mock('time_now_ms', lambda *_: now[0])
    seen: list = []
    fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: seen.append((w, C.c_int.from_address(e).value)))
    fw.mock('stock_input_trampoline', lambda wm, e, *_: seen.append((wm, C.c_int.from_address(e).value, C.c_int.from_address(e + EVENT_X).value,
                                                                     C.c_int.from_address(e + EVENT_Y).value)))
    st('touch_at', C.c_uint).value, st('touch_x').value, st('touch_y').value = 100000 - O['STALE_TOUCH_MS'], 120, 200
    heal = fw.fn('navigation__stale_heal', None)
    heal()
    assert not seen  # nothing pressed
    ui.pressed = 1
    heal()
    assert seen == [(page, EVT_POINTER_ABORT), (ui.wm, EVT_POINTER_UP_BEFORE - 1, 120, 200)]
    heal()
    assert len(seen) == 2 and st('touch_at', C.c_uint).value == 100000  # one try each STALE_TOUCH_MS


def test_a_press_younger_than_the_limit_is_left(fw, ui, st):
    fw.mock('time_now_ms', lambda *_: 100000)
    st('touch_at', C.c_uint).value = 100001 - O['STALE_TOUCH_MS']
    ui.pressed = 1
    fw.fn('navigation__stale_heal', None)()
    assert not fw.calls('stock_input_trampoline')


def test_a_stale_press_is_noted_eight_times_a_boot(fw, ui, st):
    card = Card(fw)
    ui.window('allmusic_page')
    fw.mock('time_now_ms', lambda *_: 61000)
    note = fw.fn('navigation__stale_note', None, C.c_uint)
    for _ in range(9):
        note(5100)
    lines = card.files['/mnt/data/q2-wheel.log'].decode().splitlines()
    assert len(lines) == 8 and lines[0].startswith('== wheel: stale press at 61 s: top=allmusic_page ')
    assert lines[0].endswith(' 5100 ms ago')


def test_a_note_says_whether_the_touch_thread_holds_a_press(fw, ui):
    card = Card(fw)
    loop = C.create_string_buffer(0x80)
    loop[O['LOOP_PRESSED']] = b'\x01'
    fw.mock('main_loop', lambda *_: C.addressof(loop))
    fw.fn('navigation__stale_note', None, C.c_uint)(6000)
    assert ' thread=1 ' in card.files['/mnt/data/q2-wheel.log'].decode()


def test_a_note_with_no_log_writes_nothing(fw, ui):
    fw.mock('fw_fopen', lambda *_: 0)
    fw.fn('navigation__stale_note', None, C.c_uint)(5100)
    assert not fw.calls('fw_fputs')


def quick(fw, ui, state, idex=-1, pressed=0):
    top = ui.window('statusbar_dialog')
    ui.nodes[top].props['timer_idex'] = idex
    fw.var('fw_ctx_g_dropdown_menu_state', C.c_int).value = state
    ui.pressed = pressed
    fw.fn('navigation__quick_poll', None)()
    return top


@pytest.mark.parametrize('state', [1, 2])
def test_a_pull_down_left_with_its_release_is_handed_one(fw, ui, state):
    top = quick(fw, ui, state)
    assert ui.dispatched == [(top, EVT_POINTER_UP_BEFORE - 1)]


@pytest.mark.parametrize('state, idex, pressed', [(0, -1, 0), (3, -1, 0), (1, 4, 0), (1, -1, 1)])
def test_a_pull_down_moving_or_held_is_left(fw, ui, state, idex, pressed):
    quick(fw, ui, state, idex, pressed)
    assert not ui.dispatched


# Resume: a long track's place, kept each second by the UI loop (resume_poll), and a spoken book's.
RESUME_FILE, PLAYS_FILE = '/mnt/mmc/.q2pod/ringnav-resume', '/mnt/mmc/.q2pod/ringnav-plays'  # card_store.c
EDGE, SAVE_S = O['RESUME_EDGE_S'], O['RESUME_SAVE_S']


def key_of(path, cue=0):
    """listen_key: the path's FNV-1a, a CUE start mixed in, never 0."""
    key = fnv1a(path.encode()) * 16777619 & 0xffffffff
    if cue:
        key = fnv1a(struct.pack('<i', cue), key)
    return key or 1


def ring(data):
    """The saved places, (key, seconds) most recent first, as RESUME_FILE holds them."""
    pairs = struct.iter_unpack('<Ii', data)
    return [p for p in pairs if p[0]]


class Player:
    """The queue and the player's clock as resume_poll reads them, each poll a second after the last."""
    def __init__(self, fw, mcl, deques=None, card=None):
        self.fw, self.mcl = fw, mcl
        self.deques, self.card = deques or Deques(fw), card or Card(fw)
        self.now, self.sec, self.total, self.status, self.seeks = 50000, 0, 0, 2, []
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('player_playtime_and_length', self.times)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('stock_seek_time_trampoline', lambda s, *_: self.seeks.append(C.c_int(s).value))
        fw.mock('fw_time', lambda *_: 1700000000)
        self.poll_fn = fw.fn('navigation__resume_poll', None)
        mcl.type = 1

    def times(self, sec, total, *_):
        if self.total <= 0:
            return -1
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total
        return 0

    def play(self, songs, at=0, total=3600):
        self.queue = self.deques.new(songs)
        self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        self.mcl.pos, self.total = at, total

    def poll(self, sec=None, step=1000):
        self.now += step
        if sec is not None:
            self.sec = sec
        self.poll_fn()

    def places(self):
        return ring(self.card.files.get(RESUME_FILE, b''))


LONG = '/mnt/mmc/Music/long.flac'


@pytest.fixture
def player(fw, ui, mcl):
    return Player(fw, mcl)


def test_a_long_tracks_place_is_kept_each_minute_of_play(player, recs):
    player.play([recs(path=LONG, title='Long')])
    player.poll(0), player.poll(1)  # the change, then a poll to settle
    for s in range(2, SAVE_S + 1):
        player.poll(s)
    assert player.places() == []  # under a minute's movement
    player.poll(SAVE_S + 1)
    assert player.places() == [(key_of(LONG), SAVE_S + 1)]


def test_a_pause_keeps_the_place_after_two_still_polls(player, recs):
    player.play([recs(path=LONG)])
    player.poll(100), player.poll(101), player.poll(140)
    player.poll(140)
    assert player.places() == []
    player.poll(140)
    assert player.places() == [(key_of(LONG), 140)]


def test_a_track_change_saves_where_it_was_and_coming_back_jumps_there(player, recs):
    long, short = recs(path=LONG), recs(path='/mnt/mmc/Music/short.flac')
    player.play([long, short])
    player.poll(0), player.poll(1)
    player.poll(500)  # a seek: saved
    player.poll(512)
    player.mcl.pos, player.total = 1, 200
    player.poll(0)
    assert player.places() == [(key_of(LONG), 512)]
    player.mcl.pos, player.total = 0, 3600
    player.poll(0), player.poll(0)
    player.poll(0)
    assert player.seeks == []  # not playing yet: a paused resume waits for Play
    player.poll(1)
    assert player.seeks == [512]


def places_file(*pairs, slots=O['RESUME_SLOTS']):
    return b''.join(struct.pack('<Ii', k, s) for k, s in pairs).ljust(8 * slots, b'\0')


@pytest.mark.parametrize('slots', [O['RESUME_SLOTS'], 32])  # 32: earlier releases' ring
def test_a_place_saved_before_a_restart_is_where_the_track_starts(player, recs, slots):
    player.card.files[RESUME_FILE] = places_file((key_of('/x.flac'), 99), (key_of(LONG), 700), slots=slots)
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(0), player.poll(2)
    assert player.seeks == [700]


def test_a_place_near_either_end_is_forgotten(player, recs):
    player.play([recs(path=LONG)], total=3600)
    player.poll(0), player.poll(1), player.poll(600)
    assert player.places() == [(key_of(LONG), 600)]
    player.poll(3600 - EDGE)
    assert player.places() == []


def test_a_place_already_first_writes_nothing(fw, ui, st):
    card = Card(fw)
    keep = fw.fn('navigation__spot_keep', None, C.c_uint, C.c_int, C.c_int)
    keep(7, 100, 3600)
    first = card.files[RESUME_FILE]
    card.files.clear()
    keep(7, 100, 3600)
    keep(8, 10, 3600)  # near the start, and nothing kept for it to forget
    assert card.files == {} and ring(first) == [(7, 100)]


def test_a_full_ring_drops_its_oldest_place(fw, ui, st):
    card = Card(fw)
    keep = fw.fn('navigation__spot_keep', None, C.c_uint, C.c_int, C.c_int)
    for k in range(1, O['RESUME_SLOTS'] + 2):
        keep(k, 100 + k, 3600)
    kept = ring(card.files[RESUME_FILE])
    assert len(kept) == O['RESUME_SLOTS'] and kept[0] == (O['RESUME_SLOTS'] + 1, 101 + O['RESUME_SLOTS']) and (1, 101) not in kept


def test_a_short_track_keeps_no_place(player, recs):
    player.play([recs(path=LONG)], total=29 * 60)
    player.poll(0), player.poll(1), player.poll(600), player.poll(600), player.poll(600)
    assert player.places() == [] and RESUME_FILE not in player.card.files


def test_a_stopped_long_track_started_again_goes_back_to_where_it_stopped(player, recs):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    total = player.total
    player.total, player.status = 0, 1  # stopped
    player.poll()
    player.total, player.status = total, 2
    player.poll(1)
    assert player.seeks == [400]


def test_a_confirmed_restart_starts_over(player, recs, fw):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    total = player.total
    player.total, player.status = 0, 1
    player.poll()
    fw.fn('resume_restart', None)()
    player.total, player.status = total, 2
    player.poll(1)
    assert player.seeks == []


def test_two_polls_in_a_second_are_one(player, recs):
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(600)
    player.poll(900, step=999)
    assert player.places() == [(key_of(LONG), 600)]


def test_resume_held_says_whether_starting_over_loses_a_place(player, recs, fw):
    held = fw.fn('resume_held', C.c_int)
    player.play([recs(path=LONG)])
    player.poll(0), player.poll(1), player.poll(400)
    assert held() == 1
    player.sec = 10
    assert held() == 0
    player.total = 0  # stopped: where it was
    assert held() == 1


BOOK = '/mnt/mmc/Audiobooks/book.m4b'
CHAPTERS = [(0, 600), (600, 1200), (1200, 0)]


def book(recs):
    return [recs(path=BOOK, title=f'Chapter {i + 1}', cue=c, end=e) for i, (c, e) in enumerate(CHAPTERS)]


def test_resume_on_a_books_chapter_plays_from_the_saved_place(hold, recs, ui, st, fw, mcl):
    chapters = book(recs)
    hold.card.files[RESUME_FILE] = places_file((key_of(BOOK), 900))
    hold.songs_page('allmusic_page', chapters, sel=0)
    hold.hold()
    assert hold.menu()[0] == 'Resume'
    played = []
    ui.routes['playing_page'] = lambda ctx: played.append((list(hold.deques[C.c_void_p.from_address(ctx).value]),
                                                         C.c_int.from_address(ctx + 8).value, C.c_int.from_address(ctx + 12).value))
    p = Player(fw, mcl, hold.deques, hold.card)
    hold.pick('Resume')
    assert played == [(chapters, 1, 0xf001)]  # the list's songs, from the chapter holding 900 s
    assert (st('rs_go').value, st('rs_go_key', C.c_uint).value) == (300, key_of(BOOK, 600))
    p.play(chapters, at=1, total=600)  # it plays: the chapter's first second seeks to the place
    p.poll(0), p.poll(0)
    assert p.seeks == []
    p.poll(1)
    assert p.seeks == [300]


def test_resume_with_its_place_gone_says_so(hold, recs, ui, st):
    chapters = book(recs)
    hold.card.files[RESUME_FILE] = places_file((key_of(BOOK), 900))
    hold.songs_page('allmusic_page', chapters, sel=0)
    hold.hold()
    hold.menu()
    C.memset(C.addressof(st('spots')), 0, 8 * O['RESUME_SLOTS'])  # the book finished since
    hold.pick('Resume')
    assert ui.toasts == ['Queue unchanged'] and 'playing_page' not in ui.went


def test_a_resume_rows_chapter_that_never_came_is_forgotten(player, recs, st):
    chapters = book(recs)
    st('rs_go_key', C.c_uint).value, st('rs_go').value, st('rs_go_at', C.c_uint).value = key_of(BOOK, 600), 300, player.now
    player.play(chapters, at=1, total=600)
    player.now += 8001
    player.poll(0), player.poll(0), player.poll(1)
    assert player.seeks == [] and st('rs_go_key', C.c_uint).value == 0


def test_a_books_chapter_keeps_the_books_place_past_its_first_seconds(player, recs):
    player.play(book(recs), at=1, total=600)
    player.poll(0), player.poll(1)
    player.poll(EDGE - 1)
    player.poll(EDGE - 1), player.poll(EDGE - 1)
    assert player.places() == []  # a chapter's start leaves the book's place alone
    player.poll(EDGE + 70)
    assert player.places() == [(key_of(BOOK), 600 + EDGE + 70)]


def test_a_music_cue_image_is_left_alone(player, recs):
    player.play([recs(path='/mnt/mmc/Music/live.flac', cue=600, end=4800)], total=4200)
    player.poll(0), player.poll(1), player.poll(900), player.poll(900), player.poll(900)
    assert player.places() == []


def test_the_chapters_page_suppresses_a_jump(player, recs, st):
    player.card.files[RESUME_FILE] = places_file((key_of(LONG), 700))
    player.play([recs(path=LONG)])
    st('ch_key', C.c_uint).value = key_of(LONG)
    player.poll(0), player.poll(0), player.poll(1)
    assert player.seeks == []


# Listens: a play count (Most Played) and a scrobble once half the track (or LISTEN_MAX_S) is heard.
LISTEN_MAX_S, LISTEN_MIN_S = O['LISTEN_MAX_S'], O['LISTEN_MIN_S']


def plays(data):
    return [p for p in struct.iter_unpack('<II', data) if p[0]]


def test_half_a_song_heard_counts_a_play_and_scrobbles_it(player, recs):
    r = recs(path='/mnt/mmc/Music/a.flac', title='Help!', artist='The Beatles', album='Help!', track=1)
    player.play([r], total=200)
    for s in range(0, 101):  # heard from the second poll on, a second a poll: 99 s
        player.poll(s)
    assert PLAYS_FILE not in player.card.files
    player.poll(101)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of('/mnt/mmc/Music/a.flac'), 1)]
    log = player.card.files['/mnt/mmc/.scrobbler.log'].decode()
    assert log.startswith('#AUDIOSCROBBLER/1.1\n#TZ/UTC\n') and log.count('#AUDIOSCROBBLER') == 1
    assert log.splitlines()[-1] == f'The Beatles\tHelp!\tHelp!\t1\t200\tL\t{1700000000 - 100}\t'  # started: now less heard


@pytest.mark.parametrize('takes', (True, False))
def test_only_a_listen_the_card_takes_counts_for_an_upload(fw, player, recs, takes):
    log = '/mnt/mmc/.scrobbler.log'

    def fclose(f, *_):  # a full card: the log's close fails
        refused = not takes and player.card.open[f][0] == log
        return player.card.fclose(f) or -refused
    fw.mock('fw_fclose', fclose)
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=200)
    for s in range(0, 102):
        player.poll(s)
    assert PLAYS_FILE in player.card.files and (log in player.card.files) == takes
    assert fw.var('scrobble_ui__sa', C.c_uint).value == takes  # pending, the struct's first field


def test_reads_3_s_apart_still_count_as_heard(player, recs):
    """Dark, stock's status cache is up to DARK_POLL_US old, so two reads a second apart can be 3 s apart."""
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=200)
    for s in range(0, 103, 3):  # heard from the second poll (3 s) on: 99 s
        player.poll(s)
    assert PLAYS_FILE not in player.card.files
    player.poll(105)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of('/mnt/mmc/Music/a.flac'), 1)]


def test_a_seek_is_not_heard(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=200)
    player.poll(0), player.poll(1), player.poll(150), player.poll(151)
    assert PLAYS_FILE not in player.card.files


def test_a_long_song_counts_after_listen_max(player, recs):
    player.play([recs(path=LONG, artist='A')], total=3600)
    for s in range(0, LISTEN_MAX_S + 1):
        player.poll(s)
    assert PLAYS_FILE not in player.card.files
    player.poll(LISTEN_MAX_S + 1)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of(LONG), 1)]


def test_a_song_too_short_never_counts(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=LISTEN_MIN_S)
    for s in range(LISTEN_MIN_S):
        player.poll(s)
    assert PLAYS_FILE not in player.card.files


def test_a_podcast_is_never_counted(player, recs, ui):
    player.play([recs(path='/mnt/mmc/Podcasts/ep.mp3', artist='A')], total=200)
    for s in range(150):
        player.poll(s)
    assert PLAYS_FILE not in player.card.files and '/mnt/mmc/.scrobbler.log' not in player.card.files


def test_a_repeat_counts_again(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=100)
    for s in list(range(60)) + list(range(60)):
        player.poll(s)
    assert plays(player.card.files[PLAYS_FILE]) == [(key_of('/mnt/mmc/Music/a.flac'), 2)]


def test_scrobble_percent_sends_at_its_share(player, recs, fw):
    fw.mock('toolsReadConfig', lambda path, section, key, out, d, *_: C.memmove(out, b'90\0', 3) if cstr(key) == 'PERCENT' else 0)
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=202)
    for s in range(0, 181):
        player.poll(s)
    assert '/mnt/mmc/.scrobbler.log' not in player.card.files and PLAYS_FILE in player.card.files
    player.poll(181)
    assert '/mnt/mmc/.scrobbler.log' in player.card.files  # 90% of all but its last 2 s


def test_an_untagged_song_counts_but_isnt_scrobbled(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac')], total=100)
    for s in range(60):
        player.poll(s)
    assert PLAYS_FILE in player.card.files and '/mnt/mmc/.scrobbler.log' not in player.card.files


def test_a_clock_never_set_isnt_scrobbled(player, recs, fw):
    fw.mock('fw_time', lambda *_: 1000)
    player.play([recs(path='/mnt/mmc/Music/a.flac', artist='A')], total=100)
    for s in range(60):
        player.poll(s)
    assert '/mnt/mmc/.scrobbler.log' not in player.card.files


def test_album_artist_scrobbles_in_the_artists_place_when_asked(player, recs, fw):
    fw.mock('toolsReadConfig', lambda path, section, key, out, d, *_: C.memmove(out, b'1\0', 2) if cstr(key) == 'ALBUM_ARTIST' else 0)
    player.play([recs(path='/mnt/mmc/Music/a.flac', title='T', artist='A feat. B', album_artist='A')], total=100)
    for s in range(60):
        player.poll(s)
    assert player.card.files['/mnt/mmc/.scrobbler.log'].decode().splitlines()[-1].startswith('A\t')


def test_a_tab_in_a_tag_stays_one_field(player, recs):
    player.play([recs(path='/mnt/mmc/Music/a.flac', title='a\tb', artist='x\ny')], total=100)
    for s in range(60):
        player.poll(s)
    assert player.card.files['/mnt/mmc/.scrobbler.log'].decode().splitlines()[-1].split('\t')[:3] == ['x y', '', 'a b']


def test_a_counted_song_joins_most_played_kept_tracks_unless_its_page_shows(player, recs, st, fw, lists):
    a, b = recs(path='/mnt/mmc/Music/a.flac'), recs(path='/mnt/mmc/Music/b.flac')
    kept = player.deques.new([a])
    st('mp_list', C.c_void_p).value = kept
    player.play([b], total=100)
    for s in range(60):
        player.poll(s)
    assert player.deques[kept] == [a, b]
    st('mp_page', C.c_void_p).value = 1
    player.play([recs(path='/mnt/mmc/Music/c.flac')], total=100)
    for s in range(60):
        player.poll(s)
    assert len(player.deques[kept]) == 2 and lists('mp_gen', C.c_uint).value == ~fw.var('library_gen', C.c_uint).value & 0xffffffff


def test_a_full_table_replaces_the_least_played_least_recent(player, recs, st):
    table = (C.c_uint * (2 * O['PLAYS_SLOTS'])).from_address(C.addressof(st('plays')))
    for i in range(O['PLAYS_SLOTS']):
        table[2 * i], table[2 * i + 1] = 1000 + i, 1 if i == 7 or i == 300 else 2
    st('plays_read', C.c_uint).value = 1
    player.play([recs(path='/mnt/mmc/Music/a.flac')], total=100)
    for s in range(60):
        player.poll(s)
    got = plays(player.card.files[PLAYS_FILE])
    assert got[0] == (key_of('/mnt/mmc/Music/a.flac'), 1) and (1000 + 300, 1) not in got and (1000 + 7, 1) in got


def test_a_played_song_moving_out_of_the_table_marks_most_played_stale(player, recs, st, fw, lists):
    a = recs(path='/mnt/mmc/Music/a.flac')
    table = (C.c_uint * (2 * O['PLAYS_SLOTS'])).from_address(C.addressof(st('plays')))
    for i in range(O['PLAYS_SLOTS']):
        table[2 * i], table[2 * i + 1] = 1000 + i, 2
    table[2 * 5], table[2 * 5 + 1] = key_of('/mnt/mmc/Music/a.flac'), 1  # the least played: shown by Most Played
    st('plays_read', C.c_uint).value = 1
    st('mp_list', C.c_void_p).value = player.deques.new([a])
    player.play([recs(path='/mnt/mmc/Music/b.flac')], total=100)
    for s in range(60):
        player.poll(s)
    assert lists('mp_gen', C.c_uint).value == ~fw.var('library_gen', C.c_uint).value & 0xffffffff


# Stock Shuffle's pick and a pending Play next (ringnav_shuffle, shuffle_forced).
def rec_hash(fw, r):
    return fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(r)


def shuffle_queue(fw, recs, mcl, n=4):
    deques = Deques(fw)
    songs = [recs(path=f'/mnt/mmc/{i}.flac') for i in range(n)]
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new(songs)
    return deques, songs


def test_a_play_next_held_for_stocks_shuffle_plays_next_once(fw, recs, mcl, st):
    deques, songs = shuffle_queue(fw, recs, mcl)
    st('qm_forced', C.c_uint).value, st('qm_forced_hash', C.c_uint).value = 3, rec_hash(fw, songs[2])
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 1) or 0x10)
    shuffle = fw.fn('ringnav_shuffle', C.c_int, C.c_int)
    assert shuffle(1) == 0x10 and mcl.pos == 2 and st('qm_forced', C.c_uint).value == 0
    assert shuffle(1) == 0x10 and mcl.pos == 1  # stock's own pick after it


def test_a_play_next_whose_track_moved_is_dropped(fw, recs, mcl, st):
    deques, songs = shuffle_queue(fw, recs, mcl)
    st('qm_forced', C.c_uint).value, st('qm_forced_hash', C.c_uint).value = 3, rec_hash(fw, songs[1])
    assert fw.fn('shuffle_forced', C.c_int)() == -1
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 3))
    fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
    assert mcl.pos == 3


@pytest.mark.parametrize('forced', [0, 5])
def test_no_play_next_is_none(fw, recs, mcl, st, forced):
    shuffle_queue(fw, recs, mcl)
    st('qm_forced', C.c_uint).value = forced
    assert fw.fn('shuffle_forced', C.c_int)() == -1


def test_next_after_previous_in_stocks_shuffle_returns_to_the_track_it_left(fw, recs, mcl):
    shuffle_queue(fw, recs, mcl)
    mcl.mode, mcl.pos = 2, 2
    fw.mock('stock_prev_trampoline', lambda *_: setattr(mcl, 'pos', 1))
    fw.fn('ringnav_prev', C.c_int)()
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 3))
    fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
    assert mcl.pos == 2


def test_next_in_stocks_shuffle_is_the_track_drawn_ahead_for_crossfade(fw, recs, mcl):
    deques, songs = shuffle_queue(fw, recs, mcl)
    mcl.mode, mcl.pos = 2, 0
    drawn = [C.c_int(3), C.c_int(3)]  # stock's unplayed pool: index 3 whatever the draw
    C.c_uint64.from_address(O['MCL_POOL']).value = deques.new([C.addressof(d) for d in drawn])
    fw.fn('playback_poll', None)()
    fw.mock('mcl_shuffle_pick', lambda forward, *_: setattr(mcl, 'pos', 1))
    fw.fn('ringnav_shuffle', C.c_int, C.c_int)(1)
    assert mcl.pos == 3


# Sorting names without their article (ringnav_sort_key).
@pytest.mark.parametrize('name, key', [
    (b'The Beatles', b'Beatles'), (b'A Tribe Called Quest', b'Tribe Called Quest'), (b'An Horse', b'Horse'),
    (b'THE WHO', b'WHO'), (b'the the', b'the'), (b'Theory', b'Theory'), (b'The', b'The'), (b'The ', b'The '),
    (b'The  Band', b'The  Band'), (b'Anna', b'Anna'), (b'A', b'A'), (b'', b''),
])
def test_a_names_article_is_left_out_of_its_sort(fw, name, key):
    s = C.create_string_buffer(name, 32)
    fw.fn('ringnav_sort_key', None, C.c_char_p)(s)
    assert s.value == key and len(fw.calls('toolsTrimLeft')) == 1


# The charge limit (charge_poll): charging stops at CHARGE_STOP and is handed back at CHARGE_RESUME.
STOP_AT, RESUME_AT, CHARGE_POLL = O['CHARGE_STOP'], O['CHARGE_RESUME'], O['CHARGE_POLL_MS']


class Battery:
    def __init__(self, fw, ui, st, limit=1):
        self.fw, self.now = fw, 1000
        st('charge_limit').value = limit
        self.level, self.state = fw.var('fw_ctx_g_power_capacity', C.c_int), fw.var('fw_ctx_g_power_chargestate', C.c_int)
        fw.mock('time_now_ms', lambda *_: self.now)
        self.poll_fn = fw.fn('system__charge_poll', None)

    def poll(self, level, state=1):
        self.level.value, self.state.value = level, state
        self.poll_fn()
        self.now += CHARGE_POLL

    def switches(self):
        return [a[0] for _, a in self.fw.calls('switch_charge_enable')]


def test_charging_stops_at_the_limit_and_comes_back_below_resume(fw, ui, st):
    battery = Battery(fw, ui, st)
    for level in range(STOP_AT - 3, STOP_AT):
        battery.poll(level)
    assert battery.switches() == []
    battery.poll(STOP_AT)
    assert battery.switches() == [0]
    for level in range(STOP_AT - 1, RESUME_AT, -1):
        battery.poll(level, state=0)
    assert battery.switches() == [0]
    battery.poll(RESUME_AT, state=0)
    assert battery.switches() == [0, 1]


def test_charging_turned_back_on_while_held_is_stopped_again(fw, ui, st):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT)
    battery.poll(STOP_AT - 2, state=0)
    battery.poll(STOP_AT - 2, state=1)  # USB mode's exit, or AirPlay, charges again
    assert battery.switches() == [0, 0]


def test_a_full_battery_is_held_too(fw, ui, st):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT + 5, state=2)
    assert battery.switches() == [0]


@pytest.mark.parametrize('page, flag, back', [
    ('usbmode_page', 0, 0), ('usbmode_page', 1, 1), ('airplay_page', 0, 0), ('home_page', 0, 1),
])
def test_the_limit_turned_off_hands_charging_back_as_usb_mode_or_airplay_chose(fw, ui, st, page, flag, back):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT)
    ui.window(page)
    fw.var('g_usbdac_chargeflag', C.c_ubyte).value = flag
    st('charge_limit').value = 0
    battery.poll(STOP_AT, state=0)  # not charging: a charge still held would be left as it is
    assert battery.switches() == [0, back]


def test_the_limit_off_leaves_charging_alone(fw, ui, st):
    battery = Battery(fw, ui, st, limit=0)
    battery.poll(100, state=1)
    assert battery.switches() == []


def test_the_battery_is_read_once_each_poll_period(fw, ui, st):
    battery = Battery(fw, ui, st)
    battery.poll(STOP_AT - 1)
    battery.now -= 1
    battery.poll(STOP_AT)
    assert battery.switches() == []
    battery.poll(STOP_AT)
    assert battery.switches() == [0]


# get_battery_capacity's three reads (system.c ringnav_batt_level, ringnav_batt_voltage, ringnav_batt_status): at most
# every BATT_DARK_MS with the screen off on battery, stock's second otherwise.
BATT_DARK_MS, BATT_DARK_FLOOR = O['BATT_DARK_MS'], O['BATT_DARK_FLOOR']


class Gauge:
    """The CW2015's level and voltage and the AXP2101's status as sysfs has them, and stock's get_battery_capacity
    (0x4ff258) around the three payload calls: a level under 101 stored, a voltage not negative stored, a status under 3
    stored, then the cable read (usb_detect, through ringnav_usb_detect's hook) into g_usbdet_value. tick() is one of
    the status bar's seconds."""
    def __init__(self, fw, level=60, mv=3900, status=0, cable=0, lit=0):
        self.fw, self.now, self.level, self.mv, self.status, self.cable = fw, 100000, level, mv, status, cable
        self.reads = 0
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('toolsGetBatterycapacity', self.read_level)
        fw.mock('toolsGetBatteryVoltage', lambda *_: self.mv)
        fw.mock('toolsGetChargeStatus', lambda *_: self.status)
        fw.mock('stock_usb_detect_trampoline', lambda *_: self.cable)
        fw.mock('toolsGpioGetValue', lambda *_: 0)
        self.lit = fw.var('g_backlight_status', C.c_ubyte)
        self.lit.value = lit
        self.stored = [fw.var('fw_ctx_g_power_capacity', C.c_int), fw.var('fw_ctx_g_power_chargestate', C.c_int)]
        self.det = fw.var('g_usbdet_value', C.c_ubyte)
        self.fns = [fw.fn(n, C.c_int) for n in ('ringnav_batt_level', 'ringnav_batt_voltage', 'ringnav_batt_status')]
        self.detect = fw.fn('ringnav_usb_detect', C.c_int)

    def read_level(self, *_):
        self.reads += 1
        return self.level

    def tick(self, seconds=1):
        level, mv, status = (f() for f in self.fns)
        if 0 <= level < 101:
            self.stored[0].value = level
        if mv >= 0:
            self.voltage = mv
        if 0 <= status < 3:
            self.stored[1].value = status
        self.det.value = self.detect()
        self.now += seconds * 1000
        return level, mv, status


def test_the_battery_is_read_every_second_while_lit(fw):
    g = Gauge(fw, lit=1)
    for _ in range(5):
        g.tick()
    assert g.reads == 5


def test_dark_on_battery_the_battery_is_read_every_thirty_seconds_and_stock_keeps_the_last_values(fw):
    g = Gauge(fw, level=60, mv=3900)
    assert g.tick() == (60, 3900, 0)  # the first pass reads
    g.level, g.mv = 59, 3880
    seen = [g.tick() for _ in range(BATT_DARK_MS // 1000 - 1)]
    assert g.reads == 1 and set(seen) == {(60, 3900, 0)} and g.stored[0].value == 60
    assert g.tick() == (59, 3880, 0) and g.reads == 2  # BATT_DARK_MS after the last read
    assert len(fw.calls('stock_usb_detect_trampoline')) == BATT_DARK_MS // 1000 + 1  # the cable: every second


def test_lit_again_the_next_second_reads(fw):
    g = Gauge(fw)
    g.tick(), g.tick()
    g.lit.value = 1
    g.level = 55
    assert g.tick()[0] == 55 and g.reads == 2


@pytest.mark.parametrize('cable, status, level', [(1, 1, 60), (1, 2, 100), (1, 0, 80), (0, 1, 60), (0, 2, 100),
                                                  (0, 0, BATT_DARK_FLOOR - 1)])
def test_dark_the_battery_keeps_stocks_second_plugged_charging_full_or_low(fw, cable, status, level):
    g = Gauge(fw, level=level, status=status, cable=cable)
    for _ in range(5):
        g.tick()
    assert g.reads == 5


def test_dark_a_plug_and_an_unplug_are_read_the_second_after(fw):
    g = Gauge(fw, level=60)
    g.tick(), g.tick()
    g.cable, g.status = 1, 1  # plugged in: get_battery_capacity's cable read sees it after its battery reads
    g.tick()
    assert g.reads == 1
    assert g.tick()[2] == 1 and g.reads == 2  # the next second: charging, read
    g.cable, g.status = 0, 0  # out
    g.tick()  # the cable still read as in when the battery was
    assert g.tick()[2] == 0 and g.reads == 4  # the flag fell since that read: read once more
    g.tick()
    assert g.reads == 4  # on battery again: the slow pace


def test_charge_limit_stops_charging_dark_on_the_poll_after_the_level_reaches_it(fw, ui, st):
    """Dark and plugged in, the battery keeps stock's second, so charge_poll's next pass sees the limit."""
    g = Gauge(fw, level=STOP_AT - 2, status=1, cable=1)
    st('charge_limit').value = 1
    poll = fw.fn('system__charge_poll', None)
    first = None
    for second in range(40):
        if second % 10 == 9:
            g.level += 1  # a level every 10 s, a fast charge
        g.tick()
        poll()
        if first is None and fw.calls('switch_charge_enable'):
            first = g.stored[0].value
    assert first == STOP_AT and fw.calls('switch_charge_enable')[0][1][0] == 0  # stopped at the limit, not past it
    assert g.reads == 40


# The hardware watchdog (wdt_*), started by the first loop pass and fed by every one after.
WDT_MS, WDT_LONG_MS = O['WDT_MS'], O['WDT_LONG_MS']
WDT_START, WDT_STOP, WDT_FEED = 0x8004570d, 0x2000570e, 0x2000570f


class Watchdog:
    def __init__(self, fw, fd=5, refuse=False):
        self.fw, self.calls = fw, []
        fw.mock('fw_open', lambda path, flags, *_: fd if cstr(path) == '/dev/jz_watchdog' else -1)
        fw.mock('fw_ioctl', self.ioctl)
        self.refuse = refuse
        self.poll = fw.fn('system__wdt_poll', None)

    def ioctl(self, fd, request, arg, *_):
        """(fd, request, its int): only a write request (MIPS _IOW, 4 in the top 3 bits: START) reads an
        argument; STOP and FEED (_IO, 1 there) take none, so whatever the C passes them isn't theirs."""
        request &= 0xffffffff
        self.calls.append((fd, request, C.c_int.from_address(arg).value if request >> 29 == 4 else None))
        return -1 if self.refuse and request == WDT_START else 0


def test_the_first_pass_starts_the_watchdog_and_every_pass_feeds_it(fw):
    wdt = Watchdog(fw)
    wdt.poll(), wdt.poll(), wdt.poll()
    assert wdt.calls == [(5, WDT_START, WDT_MS), (5, WDT_FEED, None), (5, WDT_FEED, None)]


def test_a_refused_start_is_stocks_for_good(fw):
    wdt = Watchdog(fw, refuse=True)
    wdt.poll(), wdt.poll()
    assert wdt.calls == [(5, WDT_START, WDT_MS)] and [a[0] for _, a in fw.calls('fw_close')] == [5]


def test_no_watchdog_is_never_tried_again(fw):
    wdt = Watchdog(fw, fd=-1)
    wdt.poll(), wdt.poll()
    assert wdt.calls == [] and len(fw.calls('fw_open')) == 1


def test_a_long_pass_gets_its_long_span_then_the_usual_one(fw):
    wdt = Watchdog(fw)
    wdt.poll()
    long_ = fw.fn('ringnav_wdt_long', None)
    long_(), long_()
    wdt.poll(), wdt.poll()
    assert wdt.calls[1:] == [(5, WDT_STOP, None), (5, WDT_START, WDT_LONG_MS), (5, WDT_STOP, None), (5, WDT_START, WDT_MS),
                             (5, WDT_FEED, None)]


def test_a_long_pass_with_no_watchdog_does_nothing(fw):
    wdt = Watchdog(fw, fd=-1)
    fw.fn('ringnav_wdt_long', None)()
    wdt.poll()
    fw.fn('ringnav_wdt_long', None)()
    assert wdt.calls == []


def test_a_clean_end_stops_the_watchdog_and_removes_the_state_record(fw, ui):
    card = Card(fw, {'/mnt/data/q2-state': b'screen:on\n'})
    wdt = Watchdog(fw)
    wdt.poll()
    end = fw.fn('ringnav_clean_end', None)
    end(), end()
    wdt.poll()
    fw.fn('system__state_poll', None)()
    assert wdt.calls == [(5, WDT_START, WDT_MS), (5, WDT_STOP, None)] and card.files.keys() <= {'/proc/mounts'}


# The state record (state_poll): /mnt/data/q2-state, one line rewritten when it changes, a burst's latest
# STATE_SETTLE_MS (5 s) after its first change; a run's first line and the power field at once.
class State:
    def __init__(self, fw, ui):
        self.fw, self.card, self.ms = fw, Card(fw), 1000
        self.status, self.out = 2, 0
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('mclGetOutputWay', lambda *_: self.out)
        fw.mock('time_now_ms', lambda *_: self.ms)
        self.poll_fn = fw.fn('system__state_poll', None)

    def poll(self, ms=0):
        self.ms += ms
        self.poll_fn()
        return self.card.files.get('/mnt/data/q2-state', b'').decode()

    def writes(self):
        return len([a for _, a in self.fw.calls('fw_fopen') if C.string_at(a[0]) == b'/mnt/data/q2-state'])


def test_the_state_record_says_what_the_q2_is_doing(fw, ui):
    state = State(fw, ui)
    fw.var('fw_ctx_g_power_capacity', C.c_int).value = 57
    assert state.poll() == 'screen:on power:running cable:out battery:57 play:playing out:jack\n'
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    fw.var('g_usbdet_value', C.c_ubyte).value = 1
    state.status, state.out = 3, 1
    state.poll()  # the change seen; written once it has settled
    assert state.poll(5000) == 'screen:off power:running cable:in battery:57 play:paused out:bluetooth\n'


@pytest.mark.parametrize('level, status, out, line', [
    (0, 1, 2, 'battery:0 play:stopped out:usb'), (100, 0, 0, 'battery:100 play:? out:jack'),
    (250, 4, 3, 'battery:100 play:? out:jack'), (-1, -1, -1, 'battery:100 play:? out:jack'),
])
def test_the_state_records_odd_readings(fw, ui, level, status, out, line):
    state = State(fw, ui)
    fw.var('fw_ctx_g_power_capacity', C.c_int).value = level
    state.status, state.out = status, out
    assert line in state.poll()


def test_the_state_record_is_written_only_when_it_changes(fw, ui):
    state = State(fw, ui)
    state.poll()  # a run's first line: at once, the forced-restart mark
    assert state.writes() == 1 and len(fw.calls('fw_fsync')) == 1
    for _ in range(20):
        state.poll(1000)  # nothing changed: nothing written, however long
    assert state.writes() == 1
    state.status = 3
    state.poll(), state.poll(4999)
    assert state.writes() == 1  # a change waits for the burst
    assert 'play:paused' in state.poll(1) and state.writes() == 2


def test_a_burst_of_changes_is_one_write_of_the_latest_line(fw, ui):
    state = State(fw, ui)
    state.poll()
    for level in range(90, 80, -1):  # ten changes, 400 ms apart: battery, screen, playback
        fw.var('fw_ctx_g_power_capacity', C.c_int).value = level
        fw.var('g_backlight_status', C.c_ubyte).value = level % 2
        state.status = 2 + level % 2
        state.poll(400)
    assert state.writes() == 1
    line = state.poll(1400)  # 5 s after the burst's first change
    assert state.writes() == 2 and line == 'screen:on power:running cable:out battery:81 play:paused out:jack\n'
    state.poll(5000)
    assert state.writes() == 2


def test_a_change_undone_within_the_burst_writes_nothing(fw, ui):
    state = State(fw, ui)
    state.poll()
    state.status = 3
    state.poll(1000)
    state.status = 2
    state.poll(1000), state.poll(5000)
    assert state.writes() == 1


def test_a_record_that_cant_be_written_is_tried_again(fw, ui):
    state = State(fw, ui)
    fw.mock('fw_fopen', lambda *_: 0)
    state.poll()
    state.card = Card(fw)
    assert state.poll().startswith('screen:on')


def test_standby_records_the_suspend_it_may_never_wake_from(fw, ui, power):
    state = State(fw, ui)
    seen = []
    fw.mock('stock_standby_trampoline', lambda *_: seen.append(state.card.files['/mnt/data/q2-state'].decode()) or 0)
    state.poll()
    state.status = 3
    state.poll(100)  # a change waiting out its burst
    fw.fn('ringnav_standby', C.c_int)()  # 100 ms after the last write: the suspend is not held back
    assert 'power:suspending' in seen[0] and 'play:paused' in seen[0] and power('suspended').value == 0
    assert 'power:running' in state.poll(100)  # and the resume, at once too


# The RTC's clock (rtc_now), as Standby's limit reads it.
@pytest.mark.parametrize('data, now', [(b'1700000000\n', 1700000000), (b'42', 42), (b'', 0), (b'x1', 0), (b'12x3', 12)])
def test_the_rtcs_seconds(fw, ui, data, now):
    Card(fw, {'/sys/class/rtc/rtc0/since_epoch': data})
    assert fw.fn('system__rtc_now', C.c_uint)() == now


def test_no_rtc_reads_zero(fw, ui):
    Card(fw)
    assert fw.fn('system__rtc_now', C.c_uint)() == 0


def test_song_order_takes_the_sort_pickers_order_on_the_title_order(fw, st, recs):
    """song_sort.c over a list loaded in stock's title order: Artist stably (the titles stay in order within an
    artist), an untagged artist last either way; Album with its discs and tracks in their own order even descending;
    Title descending reversed; nothing for Recently Added, which stock's query orders (g_sort_flag 3)."""
    use_set(st)
    saved = {}
    fw.mock('write_int_config', lambda v, s, k, *_: saved.__setitem__(cstr(k), C.c_int(v).value))
    flag = fw.var('fw_ctx_g_sort_flag', C.c_int)
    rows = Rows(fw)
    songs = [recs(title='A', artist='Beta', album='Two', track=2), recs(title='B', artist='', album='One', track=1),
             recs(title='C', artist='alpha', album='Two', track=1), recs(title='D', artist='Beta', album='One', track=2)]
    q = rows.rows(songs)
    sort_set = fw.fn('song_sort_set', None, C.c_int, C.c_int)
    order = fw.fn('song_order', None, C.c_void_p)

    def titles():
        return ''.join(field(r, REC_TITLE) for r in rows[q])
    sort_set(1, 0)  # Artist, A to Z
    order(q)
    assert titles() == 'CADB' and flag.value == 0 and saved == {'SORTSELECT': 0, 'SONGSORT': 1, 'SONGDOWN': 0}
    sort_set(1, 1)  # Z to A, the untagged one still last
    order(q)
    assert titles() == 'ADCB'
    sort_set(2, 1)  # Album, Z to A: Two (tracks 1, 2), then One (tracks 1, 2)
    order(q)
    assert titles() == 'CABD'
    sort_set(0, 1)  # Title, Z to A: the list reversed
    order(q)
    assert titles() == 'DBAC'
    sort_set(3, 1)  # Recently Added, newest first: stock's flag 3, its query's order
    assert flag.value == 3 and saved['SORTSELECT'] == 3
    order(q)
    assert titles() == 'DBAC'


@pytest.mark.parametrize('where, row, hearts', [('library', 'Favorite', 1), ('library', 'Unfavorite', 0),
                                                ('playing', 'Favorite', 1), ('playing', 'Unfavorite', 0)])
def test_a_favourite_from_the_menu_on_a_server_song_goes_to_its_server(fw, ui, st, recs, mcl, tmp_path, row, hearts,
                                                                       where):
    """Favorite and Unfavorite on a server song, its Library row or its Stream Cache copy on Now Playing,
    write the song's ratings.out line as Now Playing's heart does: the row's key and sid, like=1 or like=0, its
    kept stars left as they were. Remove on the copy takes the row out of My Fav too, where a star from the server
    put it."""
    from test_catalogue import APP, Rig, listen_key, myfav, song_row, url
    copy = '/mnt/mmc/.subsonic/stream/acct/orig/o/One [s1].flac'
    rig = Rig(fw, tmp_path)
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, local=copy)])
    rig.poll()
    key = listen_key(url('s1'))
    if hearts == 0:  # starred on the server: the row in My Fav
        rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = f'{key:08x}\ts1\tlike=1\t1600000000\n'.encode()
        rig.poll(3)
        assert myfav(rig) == [url('s1')]
    use_set(st)
    hold = Hold(fw, ui, st, recs, mcl)  # after the import: its deques and card files from here on
    hold.card.files[f'/mnt/mmc/.{APP}/q2app'] = rig.files.files[f'/mnt/mmc/.{APP}/q2app']
    fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)(0, key, 3)
    # stock's My Fav by the record's own path, on the test's database
    fw.mock('checkFavExist', lambda r, *_: cstr(C.c_void_p.from_address(r + REC_PATH).value) in myfav(rig))
    fw.mock('deleteMusicFromFav', lambda *_: 1)
    songs = [recs(path=(url('s1') if where == 'library' else copy), title='One'), song(recs, 1)]
    if where == 'library':
        hold.songs_page('allmusic_page', songs, sel=0)
    else:
        hold.now_playing(songs, at=0)
    hold.hold()
    assert row in hold.menu()
    hold.pick(row)
    assert hold.card.files[f'/mnt/mmc/.{APP}/ratings.out'] == f'{key:08x}\ts1\tlike={hearts}\t1700000000\n'.encode()
    assert fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)(0, key) == 3
    if hearts == 0:  # the copy's row: out of My Fav with it (the Library row's own path is stock's to delete)
        assert myfav(rig) == ([] if where == 'playing' else [url('s1')])


def test_the_song_search_box_gives_the_strip_its_magnifier(editor, ui):
    """A playlist's song search: the letter strip's return key shows the magnifier (search_strip.c ss_ret_kind)."""
    editor.pick('Add Songs')
    editor.pick('Search')
    edit = ui.lookup(editor.ple.page, 'search_edit', 1)
    assert ui.nodes[edit].props.get('_strip_return') == STRIP_SEARCH


# A sideways drag across a Settings row is no tap on it (the device's trial, 2026-11-22: a 110 px drag across Gain
# changed it High to Low, one across Default Volume switched it on). AWTK clicks a button lifted inside it, and a
# list's scroll view takes only drags up or down, so ringnav_dispatch drops a row's click whose touch went more
# than TAP_PX from its press either way; a tap's few pixels of jitter still click, as does Centre after a drag.
DOWN, MOVE, UP = O['EVT_POINTER_DOWN'] - 1, O['EVT_POINTER_MOVE_BEFORE'] - 1, O['EVT_POINTER_UP_BEFORE'] - 1


def touch_path(ui, fw, target, path):
    """One touch along path (x, y points: press, moves, lift) through ringnav_dispatch as the window manager sends
    it to target, then the click AWTK makes on a lift inside it: the click's answer."""
    dispatch = fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)
    event = C.create_string_buffer(0x40)
    for i, (x, y) in enumerate(path):
        kind = DOWN if i == 0 else UP if i == len(path) - 1 else MOVE
        ui.pointer_event(C.addressof(event), kind, target, x, y)
        dispatch(target, C.addressof(event))
    ui.pointer_event(C.addressof(event), O['EVT_CLICK'], target, *path[-1])
    return dispatch(target, C.addressof(event))


def settings_row(ui):
    win = ui.window('playset_page')
    view = ui.child(ui.child(win, 'list_view', 0, 40, 375, 280), 'scroll_view', 0, 0, 375, 280)
    return win, ui.child(view, 'list_item', 0, 64, 375, 32)


def clicks(ui, w):
    return [d for d in ui.dispatched if d == (w, O['EVT_CLICK'])]


@pytest.mark.parametrize('path', [
    [(100, 75)] + [(100 + 11 * i, 75 + (i % 2)) for i in range(1, 11)],  # 110 px right, as hwtouch.py drew it
    [(330, 172), (260, 172), (200, 171)],                                  # 130 px left
    [(150, 70), (150, 82)],                                                # 12 px down, the scroll's to take
])
def test_a_drag_across_a_row_is_no_tap_on_it(ui, fw, path):
    _, row = settings_row(ui)
    assert touch_path(ui, fw, row, path) == STOP and clicks(ui, row) == []


@pytest.mark.parametrize('path', [[(100, 75), (100, 75)], [(100, 75), (103, 76), (98, 73), (102, 77)],
                                  [(100, 75), (110, 85)]])
def test_a_tap_with_a_finger_s_jitter_still_clicks_its_row(ui, fw, path):
    _, row = settings_row(ui)
    touch_path(ui, fw, row, path)
    assert clicks(ui, row) == [(row, O['EVT_CLICK'])]


def test_centre_after_a_drag_still_clicks_and_a_button_off_the_lists_keeps_stock_s_click(ui, fw):
    win, row = settings_row(ui)
    assert touch_path(ui, fw, row, [(100, 75), (210, 75)]) == STOP
    dispatch = fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)
    key = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(key) + O['EVENT_TYPE']).value = O['EVT_KEY_DOWN']
    C.c_int.from_address(C.addressof(key) + O['EVENT_KEY']).value = O['KEY_CENTER']
    dispatch(row, C.addressof(key))
    click = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(click) + O['EVENT_TYPE']).value = O['EVT_CLICK']
    dispatch(row, C.addressof(click))
    assert clicks(ui, row) == [(row, O['EVT_CLICK'])]
    ok = ui.child(win, 'button', 100, 250, 175, 40)  # a dialog's own button, in no list
    touch_path(ui, fw, ok, [(120, 260), (240, 262)])
    assert clicks(ui, ok) == [(ok, O['EVT_CLICK'])]


# ---- a start from the menu with nothing to play to (output_rule.c): refused before anything changes ----

def no_output(fw):
    asked = []
    fw.mock('check_forbid_play_no_headset', lambda quiet, *_: asked.append(quiet) or 1)
    return asked


def test_resume_with_nothing_to_play_to_sets_no_seek(hold, recs, ui, st, fw):
    """Refused, the chapter's place is not left waiting (rs_go) for whatever plays that book next, and no page opens."""
    chapters = book(recs)
    hold.card.files[RESUME_FILE] = places_file((key_of(BOOK), 900))
    hold.songs_page('allmusic_page', chapters, sel=0)
    hold.hold()
    assert hold.menu()[0] == 'Resume'
    asked = no_output(fw)
    hold.pick('Resume')
    assert asked == [0] and 'playing_page' not in ui.went and ui.toasts == []
    assert (st('rs_go').value, st('rs_go_key', C.c_uint).value) == (0, 0)


def test_shuffle_with_nothing_to_play_to_keeps_the_play_mode(hold, recs, ui, fw):
    """Shuffle sets stock's mode to Shuffle and says "Shuffled Songs" around its start (library_lists.c shuffle_play):
    refused first, neither happens."""
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=1, cls=0xf003)
    hold.hold()
    hold.menu()
    asked = no_output(fw)
    hold.pick('Shuffle')
    assert asked == [0] and 'playing_page' not in ui.went and ui.toasts == []
    assert not fw.calls('config_playmode') and not fw.calls('mclLoadPlayList')
