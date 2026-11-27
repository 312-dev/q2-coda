"""patch/navigation.c from Display to the Library lists: the Display settings and their pages, Now Playing's
info page, touch and click dispatch, the settings and list rows' layouts, Hold Return's Now Playing, the
queue menu's checks and the Library's album and artist lists."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Deques, Files, callback_code, claim, code_at, high_halves, low_map, stock_prop_str, stock_start_with

W_PARENT, EVT_CLICK, EVENT_TYPE, EVENT_KEY, EVENT_X = O['W_PARENT'], O['EVT_CLICK'], O['EVENT_TYPE'], O['EVENT_KEY'], O['EVENT_X']
STOP = 11
THEMES = host.tools_theme.builtins()
OLED_THEME = next(t for t in THEMES if t['id'] == 'oled')  # the one built-in theme
OLED = 3  # config.ini's THEME for OLED, the value theme_boot keeps (navigation.c THEME_OLED)
GEOMETRY = host.tools_theme.built_geometry(THEMES)  # the one set the build bakes: OLED's


def tables(library):
    """themes.h's enums (G_*, V_*) by name and its two geometry tables, as this build has them."""
    inc = (library.parent/'themes.h').read_text() + (library.parent/'themes.inc').read_text()
    names = {}
    for body in re.findall(r'enum \{ ([^}]*) \};', inc):
        names |= {n.strip(): i for i, n in enumerate(body.split(','))}
    rows = {}
    for table in ('geom_words', 'geom_vals'):
        body = host.tools_build.need(re.search(table + r'\[\w+\] = \{([-\d, ]+)\};', inc), f'No {table}')[1]
        rows[table] = [int(v) for v in body.split(',')]
    return names, rows


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture(scope='module')
def geometry(library):
    return tables(library)


@pytest.fixture
def st(fw, scratch):
    """navigation.c's st by field: st('settings_read').value; st('setting_label', i=3) a pointer array's cell."""
    base = C.addressof(fw.var('st', C.c_char))
    def field(name, ctype=C.c_int, i=0):
        return ctype.from_address(base + scratch[name] + i * C.sizeof(ctype))
    return field


def use_set(st):
    """OLED's geometry set, the one the build bakes, with the settings already read."""
    st('settings_read').value = 1
    return GEOMETRY


class Config:
    """config.ini: a key's value, or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


def writes(fw):
    """write_int_config's (key, value) pairs in order."""
    return [(C.string_at(a[2]).decode(), C.c_int(a[0]).value) for _, a in fw.calls('write_int_config')]


class W:
    """A widget: a zeroed block holding its box and parent, as stock's I()/P() read them."""
    def __init__(self, ui, kind, parent, x=0, y=0, w=0, h=0, name=''):
        self.block = C.create_string_buffer(0x200)
        self.addr = C.addressof(self.block)
        self.kind, self.name, self.parent, self.children = kind, name, parent, []
        self.props, self.visible, self.text = {}, 1, ''
        self.box = (x, y, w, h)
        C.c_void_p.from_address(self.addr + W_PARENT).value = parent.addr if parent else None
        if parent:
            parent.children.append(self)
        ui.at[self.addr] = self

    @property
    def box(self):
        return struct.unpack_from('<4i', self.block, 0)

    @box.setter
    def box(self, value):
        struct.pack_into('<4i', self.block, 0, *(C.c_int(v).value for v in value))

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
            found = c.find(name)
            if found:
                return found
        return None

    def labels(self):
        """Every hscroll_label under this one, in order."""
        out = []
        for c in self.children:
            out += ([c] if c.kind == 'hscroll_label' else []) + c.labels()
        return out

    def said(self):
        """A row as it reads, "Title: value": its title, and its value on the set's right column (row_value_text)."""
        labels = self.labels()
        value = next((c for c in labels if c.name == '_setting_value'), None)
        return f'{labels[0].text}: {value.text}' if value is not None and value.text else labels[0].text


class Ui:
    """AWTK's widgets as a tree of W: the constructors, lookups, props, visibility, boxes and texts the C
    calls, and the window manager with its windows in stack order (the top last)."""
    def __init__(self, fw):
        self.fw, self.at, self.strings, self.handlers = fw, {}, {}, []
        self.wm = W(self, 'window_manager', None, 0, 0, 375, 320)
        for kind in ('list_item', 'button', 'image', 'hscroll_label', 'label', 'list_view', 'scroll_view', 'view'):
            if f'{kind}_create' in fw.stubs:
                fw.mock(f'{kind}_create', lambda parent, x, y, w, h, *_, k=kind: self.new(k, parent, x, y, w, h))
        fw.mock('window_manager', lambda *_: self.wm.addr)
        fw.mock('window_manager_get_top_window', lambda *_: self.wm.children[-1].addr if self.wm.children else 0)
        fw.mock('widget_get_type', lambda w, *_: self.cstr(self.at[w].kind))
        fw.mock('widget_count_children', lambda w, *_: len(self.at[w].children) if w else 0)
        fw.mock('widget_get_child', lambda w, i, *_: self.at[w].children[i].addr if w and 0 <= i < len(self.at[w].children) else 0)
        fw.mock('widget_lookup', self.lookup)
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_set_prop_str', lambda w, name, v, *_: self.at[w].props.__setitem__(C.string_at(name).decode(), C.string_at(v)))
        fw.mock('widget_get_prop_int', lambda w, name, d, *_: self.at[w].props.get(C.string_at(name).decode(), C.c_int(d).value) if w else C.c_int(d).value)
        fw.mock('widget_set_prop_int', lambda w, name, v, *_: self.at[w].props.__setitem__(C.string_at(name).decode(), C.c_int(v).value) if w else None)
        fw.mock('widget_get_visible', lambda w, *_: self.at[w].visible if w else 0)
        fw.mock('widget_set_visible', lambda w, v, *_: setattr(self.at[w], 'visible', int(bool(v))) if w else None)
        fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: setattr(self.at[w], 'box', (x, y, ww, h)))
        fw.mock('widget_resize', lambda w, ww, h, *_: setattr(self.at[w], 'box', self.at[w].box[:2] + (ww, h)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: setattr(self.at[w], 'text', C.string_at(s).decode()) if w else None)
        fw.mock('widget_set_tr_text', lambda w, s, *_: setattr(self.at[w], 'text', C.string_at(s).decode()) if w else None)
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_set_name', lambda w, s, *_: setattr(self.at[w], 'name', C.string_at(s).decode()) if w else None)
        fw.mock('widget_use_style', lambda w, s, *_: self.at[w].props.__setitem__('style', C.string_at(s).decode()) if w else None)
        fw.mock('image_base_set_image', lambda w, s, *_: self.at[w].props.__setitem__('image', C.string_at(s)) if w else None)
        fw.mock('widget_destroy_children', lambda w, *_: self.at[w].children.clear())
        fw.mock('widget_destroy', lambda w, *_: self.at[w].parent.children.remove(self.at[w]) if w else None)
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        fw.mock('widget_restack', self.restack)
        fw.mock('widget_to_local', self.to_local)

    def new(self, kind, parent, x, y, w, h, name=''):
        return W(self, kind, self.at[parent] if parent else None, x, y, w, h, name).addr

    def add(self, kind, parent=None, box=(0, 0, 0, 0), name='', **props):
        x, y, width, h = box
        w = W(self, kind, parent, x, y, width, h, name)
        w.props.update(props)
        return w

    def window(self, name, box=(0, 0, 375, 320)):
        """A window opened over the others."""
        return self.add('window', self.wm, box, name)

    def cstr(self, s):
        s = s.encode() if isinstance(s, str) else s
        if s not in self.strings:
            self.strings[s] = C.create_string_buffer(s)
        return C.addressof(self.strings[s])

    def lookup(self, w, name, recursive, *_):
        if not w:
            return 0
        name = C.string_at(name).decode()
        if recursive:
            found = self.at[w].find(name)
        else:
            found = next((c for c in self.at[w].children if c.name == name), None)
        return found.addr if found else 0

    def prop_str(self, w, name, default, *_):
        name = C.string_at(name).decode()
        widget = self.at.get(w)
        if widget is None:
            return default  # stock: widget_get_prop fails on a NULL widget
        value = (widget.name or None) if name == 'name' else widget.props.get(name)  # '' is a widget never named
        value = stock_prop_str(widget.kind.encode(), name.encode(), value.encode() if isinstance(value, str) else value, default)
        return 0 if value is None else self.cstr(value) if isinstance(value, bytes) else value

    def get_text(self, w, out, n, *_):
        s = self.at[w].text.encode()[:n - 1]
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def restack(self, w, i, *_):
        widget = self.at[w]
        siblings = widget.parent.children
        siblings.remove(widget)
        siblings.insert(min(i, len(siblings)), widget)

    def to_local(self, w, xy, *_):
        x = y = 0
        widget = self.at[w]
        while widget and widget is not self.wm:
            x, y = x + widget.box[0], y + widget.box[1]
            widget = widget.parent
        point = (C.c_int * 2).from_address(xy)
        point[0] -= x
        point[1] -= y

    def clicks(self, widget):
        """widget's EVT_CLICK handlers, called as stock would: (ctx, event)."""
        return [lambda e=0, f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, e)
                for w, evt, fn, ctx in self.handlers if w == widget.addr and evt == EVT_CLICK]

    def click(self, widget):
        (call,) = self.clicks(widget)
        return call()


def row_of(label):
    """A Display row's reading: its title and value (the value label's text, the switch's On or Off, None)."""
    button = label.parent
    value = button.find('_setting_value')
    switch = button.find('_setting_switch')
    if value:
        return label.text, value.text
    if switch:
        return label.text, {b'switch_on': 'On', b'switch_off': 'Off'}[switch.props['image']]
    return label.text, None


def rows(view):
    """view's rows (list_item > button > label) as row_of reads them, in order; stock's own rows are None."""
    out = []
    for item in view.children:
        labels = [l for l in item.labels() if l.name != '_setting_value']
        out.append(row_of(labels[0]) if labels else None)
    return out


def button_of(view, title):
    """The button of view's row titled title (its text up to any ': ')."""
    for item in view.children:
        for label in item.labels():
            if label.name != '_setting_value' and label.text.split(': ')[0] == title:
                return label.parent
    raise KeyError(title)


def event(kind, key=0, x=None, y=None):
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, EVENT_TYPE, kind)
    if x is None:
        struct.pack_into('<i', e, EVENT_KEY, key)
    else:
        struct.pack_into('<ii', e, EVENT_X, x, y)
    return e


# The Display settings.

class Display:
    """Settings' Display page as stock opens it: navigator_to builds the window and its four stock rows, Show
    Navigation Bar last (stock_display_trampoline), and runs ringnav_display, as systemset_display_page_init does; display_open's
    sub-pages open the same way, over it."""
    def __init__(self, fw, ui):
        self.fw, self.ui, self.pages = fw, ui, []
        self.display = fw.fn('ringnav_display', C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('stock_display_trampoline', lambda win, *_: self.stock(win))
        fw.mock('navigator_to', lambda name, *_: self.open(C.string_at(name).decode()))

    def stock(self, win):
        view = self.ui.at[win].find('scroll_view_display')
        for i in range(4):
            self.ui.add('list_item', view).name = f'stock_{i}'
        return 0

    def open(self, name):
        if name != 'systemset/display_page':
            return 0
        win = self.ui.window('display_page')
        nav = self.ui.add('view', win, name='view_navbar')
        self.ui.add('label', nav, name='title')
        self.ui.add('scroll_view', win, (0, 30, 375, 290), 'scroll_view_display')
        self.display(win.addr, None)
        self.pages.append(win)
        return 0

    def view(self):
        return self.pages[-1].find('scroll_view_display')

    def title(self):
        return self.pages[-1].find('view_navbar').children[0].text


def display(fw, ui, **config):
    Config(fw, **config)
    page = Display(fw, ui)
    page.open('systemset/display_page')
    return page


def test_display_adds_smart_awake_night_shift_and_its_two_pages_among_stocks_rows(fw):
    ui = Ui(fw)
    page = display(fw, ui)
    view = page.view()
    # Brightness, an inline slider in place of stock's first row (kept, hidden), then stock's second, Smart Awake and
    # Night Shift, then Appearance and Cover Flow, then Enlarged Album Art (stock's switch, by its translation key), then
    # Reduce Motion last; its third, Lock Screen, and its fourth, Show Navigation Bar, are not offered
    assert titles(view) == ['Brightness', 'stock_0', 'stock_1', 'Smart Awake', 'Night Shift', 'Appearance',
                            'Cover Flow', 'playset_covermode', 'Reduce Motion']
    assert not view.children[1].visible and view.children[0].visible
    # Night Shift and the two pages open a page, so they carry the chevron; Brightness, Smart Awake, Enlarged Album Art
    # and Reduce Motion change in place
    into = [any(c.props.get('image') == b'list_into' for c in item.children[0].children) for item in view.children if not item.name]
    assert into == [False, False, True, True, True, False, False]


def titles(view):
    """Each row's title (a stock row's name), its text up to any ': '; '' for a gap (settings_gap, no label)."""
    return [item.name or (item.labels()[0].text.split(': ')[0] if item.labels() else '') for item in view.children]


def appearance(fw, ui, **config):
    """Display, then its Appearance row: the sub-page over it."""
    page = display(fw, ui, **config)
    ui.click(button_of(page.view(), 'Appearance'))
    return page


# The payload's Appearance rows (setting_text, setting_keys[]): a setting's title and its stored key. A theme's own
# option brings its own label and key, its accent the theme's key.
PAYLOAD = {'SETTING_LIGHT': ('Light Mode', 'LIGHT'), 'SETTING_ACCENT': ('Accent', None), 'SETTING_HOME': ('Home', 'HOME'),
           'SETTING_BATTERY': ('Battery', 'BATTERY')}


def appearance_titles(t):
    """Appearance's rows under theme t: t's options in its theme.xml's order."""
    return [o['label'] if o['setting'] == 'SETTING_OWN' else PAYLOAD[o['setting']][0] for o in t['opts']]


def accent_name(t, v):
    """Accent value v's name: an ACCENTS row's, else the theme's own scheme's label."""
    names = host.tools_theme.ACCENT_NAMES
    return names[v].capitalize() if v < len(names) else t['schemes'][v]['label']


def test_oled_appearance_rows_read_title_and_value_in_its_order(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    t = OLED_THEME
    assert len(page.pages) == 2 and page.title() == 'Appearance'
    # set_text: each row its title, the value in its own right-hand label
    assert titles(page.view()) == appearance_titles(t) == [row[0] for row in rows(page.view())]
    read = dict(rows(page.view()))
    # Accent: the theme's own scheme, its default
    assert read['Accent'] == accent_name(t, host.tools_theme.accent_default(t))
    own = [o for o in t['opts'] if o['setting'] == 'SETTING_OWN']
    assert own and [read[o['label']] for o in own] == [o['names'][o['def']] for o in own]  # each at its default
    # nothing stored: the payload's own defaults (Light Mode's Auto, the owner's 2026-11-27; Split, Battery's Icon); no
    # Shortcut row
    assert (read['Light Mode'], read['Home'], read['Battery']) == ('Auto', 'Split', 'Icon') and 'Shortcut' not in read


def clicks_of(fw, ui, page, title, n):
    """title's row clicked n times: its reading after each and what each click wrote."""
    seen = []
    for _ in range(n):
        before = len(writes(fw))
        ui.click(button_of(page.view(), title))
        label = button_of(page.view(), title).labels()[0]
        seen.append((row_of(label)[1] or label.text.partition(': ')[2], writes(fw)[before:]))
    return seen


def test_oled_accent_offers_its_blue_alone(fw):
    """OLED's accent option names one value, its own scheme (theme.xml): a click comes back to it."""
    ui = Ui(fw)
    t = OLED_THEME
    v = host.tools_theme.accent_default(t)
    (o,) = [o for o in t['opts'] if o['setting'] == 'SETTING_ACCENT']
    assert o['values'] == 1 << v
    page = appearance(fw, ui, **{t['accent_key']: b'%d' % v})
    assert clicks_of(fw, ui, page, 'Accent', 2) == [(accent_name(t, v), [(t['accent_key'], v)])] * 2


def test_home_cycles_split_and_full(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    assert clicks_of(fw, ui, page, 'Home', 3) == [('Full', [('HOME', 1)]), ('Split', [('HOME', 0)]), ('Full', [('HOME', 1)])]


@pytest.mark.parametrize('stored', (b'2', b'3', b'9', b'x'))
def test_any_other_home_reads_as_split_and_is_never_saved_back(fw, stored):
    """Any stored HOME but Split's 0 and Full's 1 shows Split, and the row's next saves write Full and then Split,
    never the stored value."""
    ui = Ui(fw)
    page = appearance(fw, ui, HOME=stored)
    assert dict(rows(page.view()))['Home'] == 'Split' and fw.fn('ipod_home', C.c_int)() == 0
    assert clicks_of(fw, ui, page, 'Home', 2) == [('Full', [('HOME', 1)]), ('Split', [('HOME', 0)])]


def test_oled_battery_takes_only_its_two_styles(fw):
    """OLED's battery option names Icon and Percent (theme.xml values="icon percent")."""
    ui = Ui(fw)
    page = appearance(fw, ui)
    assert clicks_of(fw, ui, page, 'Battery', 2) == [('Percent', [('BATTERY', 1)]), ('Icon', [('BATTERY', 0)])]


@pytest.fixture
def clock(fw):
    """time and localtime_r: hour is what the clock says."""
    state = {'hour': 12}
    def local(now, out, *_):
        C.c_int.from_address(out + 8).value = state['hour']
        return out
    fw.mock('fw_time', lambda *_: 1700000000)
    fw.mock('fw_localtime_r', local)
    return state


@pytest.mark.parametrize('hour, day', [(12, 1), (22, 0)])
def test_light_modes_auto_applies_from_its_click(fw, st, clock, hour, day):
    ui = Ui(fw)
    page = appearance(fw, ui, LIGHT=b'1')
    clock['hour'] = hour
    st('light_day').value = 1 - day
    assert clicks_of(fw, ui, page, 'Light Mode', 1) == [('Auto', [('LIGHT', 2)])]
    assert st('light_day').value == day


def repaints(fw):
    """Cached images dropped, and repaints asked."""
    return len(fw.calls('image_manager_unload_all')), len(fw.calls('widget_invalidate_force'))


def test_a_switch_from_smart_awake_on_redraws_only_its_toggle(fw):
    """Smart Awake (SETTING_KEEPLIT, a switch on a set_switch theme) changes its row and nothing else."""
    ui = Ui(fw)
    page = display(fw, ui)
    label = button_of(page.view(), 'Smart Awake').labels()[0]
    was = row_of(label)[1]
    before = repaints(fw)
    ui.click(label.parent)
    assert row_of(label)[1] == {'On': 'Off', 'Off': 'On'}[was]
    assert writes(fw) == [('KEEPLIT', int(was == 'Off'))] and repaints(fw) == before


@pytest.mark.parametrize('stored, shown', [(None, 'On'), (b'0', 'Off'), (b'1', 'On')])
def test_smart_awake_is_on_unless_chosen_off(fw, stored, shown):
    """IPOD KEEPLIT missing reads on, as a reset's config.ini (tools/build.py DEFAULT_IPOD) writes it."""
    ui = Ui(fw)
    page = display(fw, ui, **({} if stored is None else {'KEEPLIT': stored}))
    assert row_of(button_of(page.view(), 'Smart Awake').labels()[0])[1] == shown
    assert fw.fn('ringnav_smart_awake', C.c_int)() == (shown == 'On')


@pytest.mark.parametrize('stored, shown', [(None, 'Off'), (b'1', 'On')])
def test_reduce_motion_is_displays_last_switch_off_unless_chosen_and_a_click_applies_it_at_once(fw, stored, shown):
    """Settings > Display > Reduce Motion (docs/oled-design.md#motion): IPOD REDUCEMOTION, off when missing; a click
    writes the key and the next animation reads the flag (ui_kit.c motion_reduced), no restart."""
    ui = Ui(fw)
    page = display(fw, ui, **({} if stored is None else {'REDUCEMOTION': stored}))
    label = button_of(page.view(), 'Reduce Motion').labels()[0]
    assert row_of(label)[1] == shown and fw.fn('motion_reduced', C.c_int)() == (shown == 'On')
    ui.click(label.parent)
    assert row_of(label)[1] == {'On': 'Off', 'Off': 'On'}[shown]
    assert writes(fw) == [('REDUCEMOTION', int(shown == 'Off'))]
    assert fw.fn('motion_reduced', C.c_int)() == (shown == 'Off')


def test_accent_repaints_and_drops_the_cached_images(fw):
    ui = Ui(fw)
    page = appearance(fw, ui)
    before = repaints(fw)
    clicks_of(fw, ui, page, 'Accent', 1)
    after = repaints(fw)
    assert after[0] == before[0] + 1 and after[1] > before[1]


class Prompts:
    """navigator_to_with_context's dialogs: (page, kind, ms, text); answer is what the confirm returns (10 OK)."""
    def __init__(self, fw, answer=0):
        self.seen, self.answer = [], answer
        fw.mock('navigator_to_with_context', self.show)

    def show(self, name, info, *_):
        kind, ms = struct.unpack('<ii', C.string_at(info, 8))
        self.seen.append((C.string_at(name).decode(), kind, ms, C.string_at(info + 8).decode()))
        return self.answer


def test_cover_flow_page_holds_only_what_changes_cover_flow(fw):
    """Sort (a picker's row, with a chevron), Fast Navigation and Position Bar, then Refresh Cover Flow after a gap;
    the Library's own settings are on Playback > Library."""
    ui = Ui(fw)
    page = display(fw, ui)
    ui.click(button_of(page.view(), 'Cover Flow'))
    assert page.title() == 'Cover Flow'
    view = page.view()
    assert [item.kind for item in view.children] == ['list_item'] * 3 + ['label', 'list_item']
    assert [item.said() for item in view.children if item.kind == 'list_item'] == [
        'Sort: Album', 'Fast Navigation', 'Position Bar', 'Refresh Cover Flow']
    assert view.children[3].box[3] == 12  # the gap
    into = [any(c.props.get('image') == b'list_into' for c in item.children[0].children)
            for item in view.children if item.kind == 'list_item']
    assert into == [True, False, False, False]


class Pages(Display):
    """Display, and stock's Playback page as stock opens it: navigator_to builds the window and its thirteen stock rows
    (stock_audioset_trampoline) and runs ringnav_audioset, as playset_playset_page_init does, Playback > Library's
    pages opening the same way over it; and stock's Buttons Lock page, whose rows ringnav_keylock_rows builds, for the
    pickers."""
    def __init__(self, fw, ui):
        super().__init__(fw, ui)
        self.audioset = fw.fn('ringnav_audioset', C.c_int, C.c_void_p, C.c_void_p)
        self.keylock = fw.fn('ringnav_keylock_rows', C.c_int, C.c_void_p)
        fw.mock('stock_audioset_trampoline', lambda win, *_: self.stock_rows(win))
        self.opened = []

    def stock_rows(self, win):
        view = self.ui.at[win].find('scroll_view_playset')
        for i in range(13):
            self.ui.add('list_item', view).name = f'stock_{i}'
        return 0

    def page(self, name, view_name, build):
        win = self.ui.window(name)
        nav = self.ui.add('view', win, name='view_navbar')
        self.ui.add('label', nav, name='title')
        self.ui.add('scroll_view', win, (0, 30, 375, 290), view_name)
        build(win.addr)
        self.pages.append(win)
        return 0

    def open(self, name):
        self.opened.append(name)
        if name == 'playset/playset_page':
            return self.page('playset_page', 'scroll_view_playset', lambda w: self.audioset(w, None))
        if name == 'systemset/keylock_page':
            return self.page('keylock_page', 'scroll_view_keylock', self.keylock)
        return super().open(name)

    def view(self):
        return next(c for c in self.pages[-1].children if c.name.startswith('scroll_view_'))

    def texts(self):
        """Each row's label as written ("Title: value"), a gap None."""
        return [item.said() if item.kind == 'list_item' and item.labels() else item.name or None
                for item in self.view().children]


def library(fw, ui, **config):
    """Settings > Playback, then its Library row: Playback > Library."""
    Config(fw, **config)
    page = Pages(fw, ui)
    page.open('playset/playset_page')
    ui.click(button_of(page.view(), 'Library'))
    return page


def test_playback_s_library_row_opens_the_library_page(fw):
    ui = Ui(fw)
    page = library(fw, ui)
    assert page.opened == ['playset/playset_page'] * 2 and page.title() == 'Library'
    assert page.texts() == ['On-The-Go', 'Sorting', 'Artists: Artist', 'Artist View: Cover Flow',
                            'Playlists', 'Folders', 'Scan Song Durations']  # stock's rows gone
    into = [any(c.props.get('image') == b'list_into' for c in item.children[0].children) for item in page.view().children]
    assert into == [False, True, False, False, True, True, False]  # no Streaming Songs: always on (2026-11-19)


@pytest.mark.parametrize('row, title, texts', [
    ('Sorting', 'Sorting', ['Songs: Title \u2191', "Artist's Albums: Year", 'Years: Newest First']),
    ('Playlists', 'Playlists', ['Playlist View: List', None, 'Import Playlists', 'Export Playlists']),
    ('Folders', 'Folders', ['Folder Root: Card', 'stock_12', 'playset_foldercover']),  # stock's Folder Skip kept
])
def test_library_s_pages_hold_their_rows(fw, row, title, texts):
    ui = Ui(fw)
    page = library(fw, ui)
    ui.click(button_of(page.view(), row))
    assert page.title() == title and page.texts() == texts


def test_songs_picker_flips_the_current_order_and_takes_another_s_natural_direction(fw):
    """Sorting's Songs opens its picker (stock's Buttons Lock page, its rows ours): the current order checked with its
    direction; it again flips the direction, another takes its own (A to Z, the newest first), saved, the picker staying
    and Sorting's row following."""
    ui = Ui(fw)
    page = library(fw, ui)
    ui.click(button_of(page.view(), 'Sorting'))
    sorting = page.view()
    ui.click(button_of(sorting, 'Songs'))
    assert page.opened[-1] == 'systemset/keylock_page' and page.title() == 'Songs'

    def marks():
        return [(item.labels()[0].text, item.children[0].children[0].props['image']) for item in page.view().children]
    assert marks() == [('Title \u2191', b'select'), ('Artist', b'unselect'), ('Album', b'unselect'),
                       ('Recently Added', b'unselect')]
    before = len(writes(fw))
    ui.click(page.view().children[0].children[0])
    assert marks()[0] == ('Title \u2193', b'select') and ('SONGDOWN', 1) in writes(fw)[before:]
    ui.click(page.view().children[3].children[0])
    assert marks() == [('Title', b'unselect'), ('Artist', b'unselect'), ('Album', b'unselect'),
                       ('Recently Added \u2193', b'select')]  # the newest first
    assert ('SORTSELECT', 3) in writes(fw) and sorting.children[0].said() == 'Songs: Recently Added \u2193'
    assert fw.var('g_sort_changeflag', C.c_ubyte).value == 1  # Songs reloads when it is on top again


def test_cover_flow_sort_opens_its_picker_and_a_pick_is_its_value(fw):
    ui = Ui(fw)
    page = Pages(fw, ui)
    Config(fw)
    Files(fw, {})
    page.open('systemset/display_page')
    ui.click(button_of(page.view(), 'Cover Flow'))
    cover_flow = page.view()
    ui.click(button_of(cover_flow, 'Sort'))
    assert page.title() == 'Sort'
    assert [item.labels()[0].text for item in page.view().children] == [
        'Album', 'Artist', 'Year', 'Recently Added', 'Most Played', 'Random']
    ui.click(page.view().children[2].children[0])
    assert [item.children[0].children[0].props['image'] for item in page.view().children] == [b'unselect'] * 2 + [
        b'select'] + [b'unselect'] * 3
    assert cover_flow.children[0].said() == 'Sort: Year'


def test_buttons_lock_keeps_its_own_rows_after_a_picker_closed(fw):
    ui = Ui(fw)
    page = library(fw, ui)
    ui.click(button_of(page.view(), 'Sorting'))
    ui.click(button_of(page.view(), 'Songs'))
    picker = ui.wm.children[-1]
    for w, evt, fn, ctx in list(ui.handlers):  # the picker's page closing
        if w == picker.addr and evt == O['EVT_DESTROY']:
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
    page.open('systemset/keylock_page')
    assert [item.labels()[0].text for item in page.view().children] == ['Off', 'On']  # keylock.c's own two


def test_playback_has_the_sleep_timer_and_leaves_the_library_s_rows(fw):
    ui = Ui(fw)
    Config(fw)
    page = Pages(fw, ui)
    page.open('playset/playset_page')
    texts = page.texts()
    assert texts == ['stock_2', 'Library', 'stock_3', 'stock_7', 'Smart Crossfade', 'stock_6',
                     'Resume Songs: 30 min', 'stock_8', 'Sleep Timer', 'Volume: 0', 'Max Volume', 'Default Volume',
                     'Level', 'stock_0', 'stock_9', 'Balance', 'stock_10', 'Mono Audio', 'Swap L/R', 'stock_1',
                     'stock_4', 'stock_5']  # EQ (stock_2), then the Library
    ui.click(button_of(page.view(), 'Sleep Timer'))
    assert page.opened[-1] == 'systemset/sleepshutdown_page'


class Settings:
    """Settings' System page: its stock rows (0-8: Language, Wireless, Network, Display, Power, Wheel Function, In-Vehicle,
    General, System) and what ringnav_systemset adds."""
    def __init__(self, fw, ui, keytone=0):
        self.win = ui.window('systemset_page')
        self.view = ui.add('scroll_view', self.win, name='scroll_view_sysset')
        for i in range(9):
            ui.add('list_item', self.view, name=f'stock_{i}')
        fw.var('g_keytone_flag', C.c_ubyte).value = keytone
        fw.fn('ringnav_systemset', C.c_int, C.c_void_p, C.c_void_p)(self.win.addr, None)


def test_general_adds_its_rows_and_hides_stock_s_but_network(fw):
    ui = Ui(fw)
    Config(fw)
    page = Settings(fw, ui)
    # Settings' General, iOS's: About, maintenance and Network, then Restart and Shut Down after a gap, Reset Settings last
    rows = [item for item in page.view.children if item.name or item.labels()]  # Restart's gap is a bare label
    assert [item.name or item.labels()[0].text for item in rows] == ['system_about', 'Software Update', 'Storage',
                                 'USB', 'system_time', 'App Store', 'stock_2',
                                 'stock_0', 'stock_1', 'stock_3', 'stock_4', 'stock_5', 'stock_6',
                                 'stock_7', 'stock_8', 'Restart', 'Shut Down', 'system_reset']
    # Reset Settings is the destructive row, last under Shut Down in Negative red (power_rows.c); Shut Down is not
    reset, shut = rows[-1], rows[-2]
    ink = lambda row: row.labels()[0].props.get('_final', 0) & 0xffffffff
    assert ink(reset) == 0xff000000 | 0xff453a and ink(shut) != ink(reset)
    # stock's Language (English only for now), Wireless, Display, Power (Settings has their rows), Wheel Function,
    # In-Vehicle, General and System
    assert [item.name for item in page.view.children if not item.visible] == [
        'stock_0', 'stock_1', 'stock_3', 'stock_4', 'stock_5', 'stock_6', 'stock_7', 'stock_8']
    # the rows stock moved to its System page open stock's pages from here
    opened = []
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name).decode()))
    for key in ('system_time', 'system_reset', 'system_about'):
        ui.click(button_of(page.view, key))
    assert opened == ['systemset/datetime_page', 'systemset/reset_page', 'systemset/about_page']
    # Software Update and Storage open their own pages (sw_update.c sw_update_open, storage.c storage_open)
    fw.mock('window_create', lambda *_: 0)
    ui.click(button_of(page.view, 'Software Update'))
    assert len(fw.calls('check_updatefw_exist')) == 1 and len(opened) == 3  # the card's check, not stock's page
    ui.click(button_of(page.view, 'Storage'))
    assert fw.calls('window_create')


@pytest.mark.parametrize('there', (True, False))
def test_general_has_a_rockbox_row_only_with_rockbox_on_the_card_and_it_hands_over(fw, there):
    """Settings > General > Rockbox (settings_display.c rockbox_row, docs/boot.md#rockbox-from-home): with
    /mnt/mmc/.rockbox/rockbox a regular file, a row after the page's own rows and before Restart's gap, whose click is
    rockbox_open's hand-over (its flag for S90play, demo ended); with no Rockbox on the card, no row."""
    def stat(version, path, info, *_):
        C.c_uint.from_address(info + 20).value = 0o100755
        return 0 if there and C.string_at(path) == b'/mnt/mmc/.rockbox/rockbox' else -1
    fw.mock('fw___xstat', stat)
    ui = Ui(fw)
    Config(fw)
    page = Settings(fw, ui)
    rows = [item.name or item.labels()[0].text for item in page.view.children if item.name or item.labels()]
    assert ('Rockbox' in rows) == there
    if not there:
        return
    assert rows[rows.index('Rockbox') + 1:] == ['Restart', 'Shut Down', 'system_reset']
    card = Files(fw, {})
    ui.click(button_of(page.view, 'Rockbox'))
    assert host.tools_build.ROCKBOX_FLAG in card.files and fw.calls('save_memoryplay_info')
    assert C.string_at(fw.calls('fw_system')[-1][1][0]).endswith(b'kill -9 $PPID')


@pytest.mark.parametrize('pick, shown', [(0, 'Automatic'), (1, 'Charge Only'), (2, 'Transfer Files'),
                                         (3, 'USB Audio'), (5, 'Ask Every Time')])
def test_general_s_usb_row_shows_the_remembered_choice_and_opens_its_picker(fw, pick, shown):
    """Settings > General > USB, after Storage (usb_ui.c usb_ui_row): Q2POD USBPICK as its value, beside the chevron;
    Centre opens the USB picker (usb_pick_open)."""
    ui = Ui(fw)
    Config(fw, USBPICK=str(pick).encode())
    page = Settings(fw, ui)
    button = button_of(page.view, 'USB')
    assert button.find('_setting_value').text == shown
    fw.mock('window_create', lambda *_: 0)
    ui.click(button)
    assert len(fw.calls('window_create')) == 1


@pytest.mark.parametrize('keytone', [0, 1])
def test_controls_opens_display_as_its_own_page(fw, keytone):
    ui = Ui(fw)
    Config(fw)
    Settings(fw, ui, keytone)
    page = Display(fw, ui)
    fw.fn('navigation__set_open', C.c_int, C.c_void_p, C.c_void_p)(4, None)  # Settings' Controls row on Home
    assert page.title() == 'Controls'
    found = titles(page.view())
    assert found[:3] == ['Wheel Sensitivity', 'Hold Return', 'Clicker']
    assert 'system_keylock' in found and 'Click With Headphones' not in found
    # Key Lock opens stock's page
    opened = []
    fw.mock('navigator_to', lambda name, *_: opened.append(C.string_at(name).decode()))
    ui.click(button_of(page.view(), 'system_keylock'))
    assert opened == ['systemset/keylock_page']


def captions(fw, view):
    """view's captioned rows (settings_pages.c's table, CAP_MAX of them, for one view at a time): each row's title
    and its caption."""
    table = fw.var('settings_pages__cap', C.c_void_p * 17)  # view, item[CAP_MAX], text[CAP_MAX]
    text = {table[1 + k]: C.wstring_at(table[9 + k]) for k in range(8) if table[1 + k]} if table[0] == view.addr else {}
    return {title: text[item.addr] for title, item in zip(titles(view), view.children) if item.addr in text}


def test_display_s_rows_carry_their_captions(fw):
    ui = Ui(fw)
    page = display(fw, ui)
    assert captions(fw, page.view()) == {'Smart Awake': 'Keeps the screen on for lyrics and art while playing',
                                         'playset_covermode': 'Shows the art full screen after 10s on Now Playing',
                                         'Reduce Motion': 'Fades in place of slides, zooms and bounces'}
    ui.click(button_of(page.view(), 'Cover Flow'))
    assert captions(fw, page.view()) == {
        'Fast Navigation': 'Year sort spins by year too; letters stay till you lift',
        'Position Bar': 'Shows your place in the covers while you browse'}


def test_controls_rows_carry_their_captions(fw):
    ui = Ui(fw)
    Config(fw)
    Settings(fw, ui)
    page = Display(fw, ui)
    fw.fn('navigation__set_open', C.c_int, C.c_void_p, C.c_void_p)(4, None)
    assert 'Volume Guard' not in titles(page.view())  # retired: the wheel's volume is playback screens' alone
    assert captions(fw, page.view()) == {'Hold Return': 'What holding Return opens: Now Playing or Home',
                                         'Wake': 'Any Button: every button wakes it. Double Press: Center twice'}


# Power's Boot to row: /mnt/data/boot-target, which the boot script reads with $(cat ...).
BOOT = '/mnt/data/boot-target'


def rockbox(fw, on):
    """The card's /mnt/mmc/.rockbox/rockbox: a regular file, or none."""
    def stat(version, path, info, *_):
        struct.pack_into('<I', (C.c_char * 144).from_address(info), 20, 0o100755)
        return 0 if on['card'] else -1
    fw.mock('fw___xstat', stat)


class Power:
    """Settings' Power page: stock's rows, then ringnav_powermanager's, Boot to last."""
    def __init__(self, fw, ui, files, card=True):
        self.card = {'card': card}
        rockbox(fw, self.card)
        self.files = Files(fw, files)
        Config(fw)
        self.prompts = Prompts(fw)
        self.win = ui.window('powermanager_page')
        self.view = ui.add('scroll_view', self.win, name='scroll_view_powermanager')
        fw.fn('ringnav_powermanager', C.c_int, C.c_void_p, C.c_void_p)(self.win.addr, None)

    def boot(self):
        labels = [i.labels()[0] for i in self.view.children if i.labels() and i.labels()[0].text == 'Boot to']
        return labels[0] if labels else None


def said(label):
    """The Boot to row as it reads: the label is the title alone, the value on the right column
    (docs/oled-design.md#list-row, a key/value row)."""
    value = next(c for c in label.parent.labels() if c.name == '_setting_value')
    assert label.text == 'Boot to' and value.text
    return f'{label.text}: {value.text}'


@pytest.mark.parametrize('saved, shown', [
    (None, 'Rockbox'),  # no choice saved: the boot script's own default
    (b'q2pod', 'Vamp'), (b'q2pod\n', 'Vamp'), (b'q2pod' + b'\n' * 40, 'Vamp'),  # $(cat) drops trailing newlines
    (b'rockbox', 'Rockbox'), (b'q2po', 'Rockbox'), (b'q2podx', 'Rockbox'), (b'q2pod\nx', 'Rockbox'), (b'\nq2pod', 'Rockbox'),
])
def test_boot_to_reads_what_the_boot_script_would(fw, saved, shown):
    ui = Ui(fw)
    page = Power(fw, ui, {} if saved is None else {BOOT: saved})
    assert said(page.boot()) == f'Boot to: {shown}'


def test_boot_to_a_file_that_fails_to_read_is_rockbox(fw):
    ui = Ui(fw)
    fw.mock('fw_ferror', lambda *_: 1)
    assert said(Power(fw, ui, {BOOT: b'q2pod'}).boot()) == 'Boot to: Rockbox'


def test_no_rockbox_no_boot_row(fw):
    ui = Ui(fw)
    assert Power(fw, ui, {BOOT: b'q2pod'}, card=False).boot() is None


def test_boot_to_flips_and_saves_through_a_temporary_file(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {})
    label = page.boot()
    ui.click(label.parent)
    assert said(label) == 'Boot to: Vamp' and page.files.files.get(BOOT) == b'q2pod'
    ui.click(label.parent)
    assert said(label) == 'Boot to: Rockbox' and page.files.files.get(BOOT) == b'rockbox'
    assert {C.string_at(a[0]).decode() for _, a in fw.calls('fw_fopen')} >= {BOOT + '.tmp'}


def test_boot_to_not_saved_says_so_and_stays(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {BOOT: b'rockbox'})
    page.files.fail_rename = True
    label = page.boot()
    ui.click(label.parent)
    assert said(label) == 'Boot to: Rockbox' and page.files.files[BOOT] == b'rockbox'
    assert [p[3] for p in page.prompts.seen] == ['Could not save boot choice']
    assert [C.string_at(a[0]).decode() for _, a in fw.calls('fw_unlink')] == [BOOT + '.tmp']


def test_boot_to_after_rockbox_left_the_card(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {})
    page.card['card'] = False
    label = page.boot()
    ui.click(label.parent)
    assert said(label) == 'Boot to: Rockbox' and BOOT not in page.files.files
    assert [p[3] for p in page.prompts.seen] == ['Rockbox is not on the card']


# Now Playing's info page.
NAV = NAVIGATION_SOURCE
def nav_define(name):
    return int(host.tools_build.need(re.search(rf'#define {name} (\d+)', NAV), f'No {name}')[1])


MARQUEE_PX, MARQUEE_MS = nav_define('MARQUEE_PX'), nav_define('MARQUEE_MS')
HOLD_MS = O['MARQUEE_HOLD_MS']
INFO = ['label_album', 'label_genre', 'label_size', 'label_year', 'label_encode', 'label_bits', 'label_kbps', 'label_sample',
        'label_location']


class Info:
    """Now Playing's info page: a label per stock line, stock's np_info having written them; rows adds
    the oled set's <name>_value labels (tools/ipod.py np_info_values)."""
    def __init__(self, fw, ui, texts, rows=False, location=b'Location'):
        self.fw, self.ui = fw, ui
        self.win = ui.window('playing_page')
        self.view = ui.add('view', self.win, (0, 30, 375, 290), 'view_info')
        self.labels = {}
        for i, name in enumerate(INFO):
            self.labels[name] = ui.add('label', self.view, (10, 20 * i, 355, 20), name)
            if rows:
                ui.add('label', self.view, (180, 20 * i, 185, 20), name + '_value')
        for name, value in texts.items():
            self.labels[name].text = value
        self.timers = []
        fw.mock('np_info', lambda *_: 7)
        fw.mock('getFormatString', lambda key, out, n, *_: C.memmove(out, location + b'\0', len(location) + 1))
        fw.mock('tk_str_start_with', stock_start_with)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        self.fill = fw.fn('ringnav_np_info', C.c_int, C.c_void_p)

    def __call__(self):
        assert self.fill(self.win.addr) == 7  # stock's answer, passed on
        return {name: label.text for name, label in self.labels.items()}

    def genre(self):
        return self.win.find('ringnav_genre_scroll')


STOCK_INFO = {'label_album': 'Album:Kind of Blue', 'label_size': 'Size:35.92MB', 'label_year': 'Year:Unknown Artist',
              'label_encode': 'Format:FLAC', 'label_bits': 'Bits:16bit', 'label_kbps': 'Bitrate:950kbps',
              'label_sample': 'Sample rate:44.1kHz', 'label_location': '/mnt/mmc/Music/Miles'}


def test_info_lines_get_a_space_and_their_units(fw):
    ui = Ui(fw)
    info = Info(fw, ui, STOCK_INFO)
    assert info() == {'label_album': 'Album: Kind of Blue', 'label_genre': '', 'label_size': 'Size: 35.92 MB',
                      'label_year': 'Year: Unknown', 'label_encode': 'Format: FLAC', 'label_bits': 'Bits: 16-bit',
                      'label_kbps': 'Bitrate: 950 kbps', 'label_sample': 'Sample rate: 44.1 kHz',
                      'label_location': 'Location: /mnt/mmc/Music/Miles'}


def test_a_second_fill_rewrites_nothing(fw):
    ui = Ui(fw)
    info = Info(fw, ui, dict(STOCK_INFO, label_genre='Genre:Jazz'))
    first = info()
    set_before = len(fw.calls('widget_set_text_utf8'))
    assert info() == first and len(fw.calls('widget_set_text_utf8')) == set_before


@pytest.mark.parametrize('stock, shown', [
    ('Bitrate:VBRkbps', 'Bitrate: VBRkbps'),  # a unit after no number stays as it is
    ('Bitrate:kbps', 'Bitrate: kbps'), ('Bitrate:  950kbps', 'Bitrate: 950 kbps'), ('Bitrate', 'Bitrate'),  # no colon: left
])
def test_info_units_only_after_a_number(fw, stock, shown):
    ui = Ui(fw)
    assert Info(fw, ui, {'label_kbps': stock})()['label_kbps'] == shown


@pytest.mark.parametrize('stock, location, shown', [
    ('Location:/mnt/mmc/A', b'Location', 'Location: /mnt/mmc/A'),  # already named
    ('/mnt/mmc/A', b'Ort', 'Ort: /mnt/mmc/A'),  # the language's word
    ('/mnt/mmc/A', b'(player_location)', 'Location: /mnt/mmc/A'),  # a missing string reads "(key)"
    ('/mnt/mmc/A', b'', 'Location: /mnt/mmc/A'),
])
def test_the_folder_gets_its_location_label(fw, stock, location, shown):
    ui = Ui(fw)
    assert Info(fw, ui, {'label_location': stock}, location=location)()['label_location'] == shown


def test_oled_rows_split_name_and_value_in_its_grey(fw, st):
    ui = Ui(fw)
    Config(fw, THEME=b'%d' % OLED)
    info = Info(fw, ui, dict(STOCK_INFO, label_genre='Genre:Jazz'), rows=True)
    texts = info()
    value = {name: info.win.find(name + '_value') for name in INFO}
    assert texts == {'label_album': 'Album', 'label_genre': 'Genre', 'label_size': 'Size', 'label_year': 'Year',
                     'label_encode': 'Format', 'label_bits': 'Bits', 'label_kbps': 'Bit Rate', 'label_sample': 'Sample rate',
                     'label_location': 'Location'}  # stock's Bitrate in the English name About's grid gives it
    assert [value[n].text for n in INFO] == ['Kind of Blue', 'Jazz', '35.92 MB', 'Unknown', 'FLAC', '16-bit', '950 kbps',
                                             '44.1 kHz', '/mnt/mmc/Music/Miles']
    assert info.genre() is None  # the genre is a row like the others: no marquee
    grey = OLED_THEME['np_info_value']  # dark Light Mode: the dark value ink as it is
    ink = C.c_int(0xff000000 | (grey & 255) << 16 | grey & 0xff00 | grey >> 16).value
    assert {value[n].props['_ink'] for n in INFO} == {ink} == {value[n].props['style:normal:text_color'] for n in INFO}
    set_before = len(fw.calls('widget_set_prop_int'))
    info()
    assert len([a for _, a in fw.calls('widget_set_prop_int')[set_before:] if C.string_at(a[1]) == b'_ink']) == 0


def test_oled_rows_take_a_value_without_a_value_label(fw):
    ui = Ui(fw)
    Config(fw, THEME=b'%d' % OLED)
    info = Info(fw, ui, {'label_album': 'Album:Kind of Blue'}, rows=True)
    info.win.find('label_size_value').name = 'gone'
    info.labels['label_size'].text = 'Size:35.92MB'
    assert info()['label_size'] == 'Size' and info.win.find('label_album_value').text == 'Kind of Blue'


class Np:
    """Now Playing's details open in ⋯'s About (np_about.c), its page in front and lit: the page holds the labels."""
    def __init__(self, fw, ui, st, info):
        self.about = fw.var('np_about__ab', C.c_void_p)  # the struct's first word: the open page
        self.about.value = info.win.addr
        st('np_win', C.c_void_p).value = info.win.addr
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.offsets = []
        fw.mock('hscroll_label_set_xoffset', lambda w, x, *_: self.offsets.append(x))


def measured(label, width):
    """Stock's first paint: the label's text width."""
    struct.pack_into('<i', label.block, O['HSCROLL_TEXT_W'], width)


def test_the_genre_shows_in_its_own_scrolling_label(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Jazz'})
    info()
    genre, stock = info.genre(), info.labels['label_genre']
    assert genre.text == 'Genre: Jazz' and genre.parent is stock.parent and genre.box == stock.box and not stock.visible
    assert fw.calls('hscroll_label_set_only_focus')[0][1][:2] == (genre.addr, 1)  # no stock timer: genre_step moves it


def run(fn):
    return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def test_a_long_genre_scrolls_to_its_end_holds_and_starts_over(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    np = Np(fw, ui, st, info)
    info()
    assert info.timers == []  # not measured yet: stock measures on the first paint
    over = 44
    measured(info.genre(), info.genre().box[2] - 4 + over)
    info()
    assert [ms for _, ms in info.timers] == [HOLD_MS]  # the new text holds at its start
    steps = []
    for _ in range(over // MARQUEE_PX + 2):
        np.offsets.clear()
        run(info.timers[-1][0])
        steps.append((np.offsets[-1], info.timers[-1][1]))
    moving = [(x, MARQUEE_MS) for x in range(MARQUEE_PX, over, MARQUEE_PX)]
    assert steps == moving + [(over, HOLD_MS), (0, HOLD_MS), (MARQUEE_PX, MARQUEE_MS)]


def test_the_genre_stops_once_dark_or_about_closes(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    np = Np(fw, ui, st, info)
    info()
    measured(info.genre(), 600)
    info()
    page, np.about.value = np.about.value, None  # About closed
    n = len(info.timers)
    run(info.timers[-1][0])
    assert len(info.timers) == n and st('genre_timer').value == 0
    np.about.value = page
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    info()  # np_info's next fill restarts it, lit only
    assert len(info.timers) == n


def test_the_genre_stops_under_another_window_and_starts_again_in_front(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    Np(fw, ui, st, info)
    info()
    measured(info.genre(), 600)
    info()
    n = len(info.timers)
    over = ui.window('volume_dialog')  # a window over About
    run(info.timers[-1][0])
    assert len(info.timers) == n and st('genre_timer').value == 0
    info()  # a fill while still covered starts nothing
    assert len(info.timers) == n
    ui.wm.children.remove(over)
    info()  # in front again: the next fill restarts it, holding at its start
    assert len(info.timers) == n + 1 and info.timers[-1][1] == HOLD_MS


def test_a_new_genre_starts_again_from_its_start(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    np = Np(fw, ui, st, info)
    info()
    measured(info.genre(), 600)
    info()
    run(info.timers[-1][0])
    run(info.timers[-1][0])
    assert st('genre_off').value == 2 * MARQUEE_PX
    removed = len(fw.calls('timer_remove'))
    info.labels['label_genre'].text = 'Genre:Ambient Dub Techno Extended'
    np.offsets.clear()
    info()
    assert info.genre().text == 'Genre: Ambient Dub Techno Extended' and np.offsets == [0] and st('genre_off').value == 0
    assert len(fw.calls('timer_remove')) == removed + 1 and info.timers[-1][1] == HOLD_MS


def test_without_about_open_the_genre_takes_no_marquee(fw, st):
    """Now Playing's own fill, with About closed: its hidden details never scroll."""
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Progressive Electronic Dance'})
    Np(fw, ui, st, info).about.value = None
    info()
    measured(info.genre(), 600)
    info()
    assert info.timers == [] and not st('genre_label', C.c_void_p).value


class About:
    """Now Playing with its details hidden in its window (tools/ipod.py np_info_out), first under the art's page, and
    ⋯'s About as np_about.c runs it: window_create's page, AWTK's widget_remove_child and widget_add_child (appending),
    navigator_back closing the top window and dispatching its destroy, as AWTK's does."""
    def __init__(self, fw, ui, st, texts=None):
        self.fw, self.ui, self.st = fw, ui, st
        self.info = Info(fw, ui, texts or dict(STOCK_INFO, label_genre='Genre:Jazz'))
        self.np = self.info.win
        self.details = self.np.find('view_info')
        self.np.children.remove(self.details)
        self.np.children.insert(0, self.details)
        self.art = ui.add('view', self.np, (0, 0, 375, 200), 'slide_view_view')
        self.details.visible, self.details.box = 0, (0, 60, 375, 230)  # under a title row
        st('np_win', C.c_void_p).value = self.np.addr
        self.fills = []
        fw.mock('np_info', lambda w, *_: self.fills.append(w) or 7)
        fw.mock('window_create', lambda *_: ui.window('').addr)
        fw.mock('widget_remove_child', self.remove)
        fw.mock('widget_add_child', self.add)
        fw.mock('navigator_back', self.back)

    def remove(self, parent, child, *_):
        self.ui.at[parent].children.remove(self.ui.at[child])
        self.ui.at[child].parent = None
        C.c_void_p.from_address(child + W_PARENT).value = None
        return 0

    def add(self, parent, child, *_):
        w = self.ui.at[child]
        w.parent = self.ui.at[parent]
        w.parent.children.append(w)
        C.c_void_p.from_address(child + W_PARENT).value = parent
        return 0

    def back(self, *_):
        top = self.ui.wm.children[-1]
        self.destroyed(top)
        self.ui.wm.children.remove(top)
        return 0

    def destroyed(self, w):
        for ww, evt, fn, ctx in list(self.ui.handlers):
            if ww == w.addr and evt == O['EVT_DESTROY']:
                C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0)

    def open(self):
        return self.fw.fn('np_about_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    @property
    def page(self):
        return self.ui.at.get(self.fw.fn('np_about_page', C.c_void_p)() or 0)

    def key(self, key):
        (fn, ctx), = [(fn, ctx) for w, evt, fn, ctx in self.ui.handlers if w == self.page.addr and evt == O['EVT_KEY_UP']]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, event(O['EVT_KEY_UP'], key))


def test_about_moves_the_details_into_its_page_hidden_and_filled(fw, st):
    about = About(fw, Ui(fw), st)
    assert about.open() == 0
    page = about.page
    assert page and page.name == 'npabout_page' and about.ui.wm.children[-1] is page
    # the details stay hidden, the source of the grid np_about.c paints over the page
    assert about.details.parent is page and about.details.visible == 0 and about.details not in about.np.children
    assert about.details.box == (0, 60, 375, 230)  # its place in Now Playing's window, which the page's box is
    assert about.fills == [page.addr]  # filled at once, on the page
    assert about.np.find('label_album') is None and page.find('label_album').text == 'Album: Kind of Blue'


def test_while_about_is_open_now_playing_s_fills_go_to_it(fw, st):
    about = About(fw, Ui(fw), st)
    about.open()
    assert about.info.fill(about.np.addr) == 7 and about.fills[-1] == about.page.addr


def test_about_opens_once_and_needs_the_details(fw, st):
    ui = Ui(fw)
    about = About(fw, ui, st)
    about.open()
    page = about.page
    about.open()
    assert about.page is page and len(ui.wm.children) == 2
    other = About(fw, Ui(fw), st)
    other.np.children.remove(other.details)  # no details: nothing opens
    fw.var('np_about__ab', C.c_void_p).value = None
    assert other.open() == 0 and other.page is None


def test_return_gives_the_details_back_hidden_and_first_then_goes_back(fw, st):
    about = About(fw, Ui(fw), st)
    about.open()
    assert about.key(O['KEY_CENTER']) == 0 and about.page  # only Return
    assert about.key(O['KEY_RETURN']) == STOP
    assert about.page is None and about.ui.wm.children[-1] is about.np
    assert about.np.children[0] is about.details and about.details.visible == 0  # under the rest: it takes no touch
    assert about.info.fill(about.np.addr) == 7 and about.fills[-1] == about.np.addr


def test_a_close_without_return_gives_the_details_back_too(fw, st):
    about = About(fw, Ui(fw), st)
    about.open()
    about.back()  # closed another way: the destroy event alone
    assert about.page is None and about.np.children[0] is about.details and about.details.visible == 0


def test_now_playing_going_first_leaves_the_details_to_the_page(fw, st):
    about = About(fw, Ui(fw), st)
    about.open()
    page = about.page
    fw.fn('np_about_np_gone', None, C.c_void_p)(about.np.addr)
    fw.fn('np_about_np_gone', None, C.c_void_p)(0x1234)  # another window's: nothing
    about.back()
    assert about.page is None and about.details.parent is page and about.details not in about.np.children


def test_no_genre_label_no_marquee(fw, st):
    ui = Ui(fw)
    info = Info(fw, ui, {'label_genre': 'Genre:Jazz'})
    Np(fw, ui, st, info)
    fw.mock('hscroll_label_create', lambda *_: 0)
    assert info()['label_genre'] == 'Genre:Jazz' and info.genre() is None


# OLED's About: the info grid (np_about.c), painted from the hidden details.
GRID_INFO = {'label_album': 'Album:Caracal (Deluxe)', 'label_genre': 'Genre:Electronic', 'label_size': 'File Size:38.27MB',
             'label_year': 'Year:2015', 'label_encode': 'Encoding:FLAC', 'label_bits': 'Bit Depth:16bit',
             'label_kbps': 'Bitrate:1001kbps', 'label_sample': 'Sample Rate:44.1kHz',
             'label_location': 'SD:/Music/Disclosure/Caracal (Deluxe)'}
OMEN = '/mnt/mmc/Music/Disclosure/Caracal (Deluxe)/01 Omen.flac'


class Grid(About):
    """⋯'s About under OLED: the details with their <name>_value labels (tools/ipod.py np_info_values), Now Playing's
    title, artist, slider (its max the length) and cover, the playing path, and a canvas the grid paints on."""
    def __init__(self, fw, st, geometry, texts=GRID_INFO, path=OMEN, length=237, unknown=b'Unknown Artist'):
        from test_nav_paint import Canvas
        ui = Ui(fw)
        Config(fw, THEME=str(OLED).encode(), LIGHT=b'0')
        self.fw, self.ui, self.st = fw, ui, st
        self.info = Info(fw, ui, dict(texts), rows=True)
        self.np = self.info.win
        self.details = self.np.find('view_info')
        self.details.visible = 0  # as the build leaves it
        st('np_win', C.c_void_p).value = self.np.addr
        for field, name, text in (('np_title', 'scrlabel_title', 'Omen'), ('np_artist', 'scrlabel_artist', 'Disclosure')):
            label = ui.add('label', self.np, (0, 0, 100, 20), name)
            label.text = text
            st(field, C.c_void_p).value = label.addr
        st('np_slider', C.c_void_p).value = ui.add('slider', self.np, (0, 0, 300, 20), 'slider_play', max=length).addr
        st('np_cover', C.c_void_p).value = ui.add('image', self.np, (0, 0, 166, 166), 'img_cover', image=b'file:///c.jpg').addr
        info = fw.var('fw_ctx_g_play_id3_info', C.c_char * 2716)
        C.memmove(info, path.encode() + b'\0', len(path) + 1)
        words = {b'player_unknown': unknown, b'player_location': b'Location'}
        fw.mock('getFormatString', lambda key, out, n, *_: C.memmove(out, words.get(C.string_at(key), b'') + b'\0',
                                                                     len(words.get(C.string_at(key), b'')) + 1))
        self.fills = []
        fw.mock('np_info', lambda w, *_: self.fills.append(w) or 7)
        fw.mock('window_create', lambda *_: ui.window('').addr)
        fw.mock('widget_remove_child', self.remove)
        fw.mock('widget_add_child', self.add)
        fw.mock('navigator_back', self.back)
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: ui.new(  # h: past the stub's args
            C.string_at(kind).decode(), parent, x, y, w, 262))
        self.invalidated = []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.invalidated.append(w))
        self.canvas = Canvas(fw)
        fw.mock('canvas_measure_text', lambda c, s, n, *_: 6.0 * n)  # 6 px a character: the spec fits
        self.open()

    @property
    def body(self):
        return next(c for c in self.page.children if c.kind == 'view' and c.name != 'view_info')

    def paint(self):
        """The texts the grid paints: (text, its row's centre on screen)."""
        self.canvas.texts.clear()
        self.canvas.arts.clear()
        self.fw.fn('np_about_paint', None, C.c_void_p, C.c_void_p)(self.body.addr, self.canvas.p)
        page_y = self.page.box[1]
        return [(t, page_y + self.body.box[1] + r[1] + r[3] // 2) for t, r, *_ in self.canvas.texts]


def test_oled_about_keeps_the_details_hidden_and_paints_the_grid(fw, st, geometry):
    """The header (title, artist, length), the quality line (badge, spec), then the cells two a row from y 138, 44 px
    apart, each name over its value, Location across the last row: the values in plain words."""
    g = Grid(fw, st, geometry)
    assert g.details.parent is g.page and g.details.visible == 0
    texts = g.paint()  # screen rows
    page_y = g.page.box[1]  # the page's window: full-bleed, from the screen's top
    assert [t for t, _ in texts] == [
        'Omen', 'Disclosure', '3:57', 'Lossless', 'FLAC · 16-bit / 44.1 kHz',
        'Album', 'Caracal (Deluxe)', 'Year', '2015', 'Genre', 'Electronic', 'Bit Rate', '1,001 kbps', 'Size', '38.3 MB',
        'Location', 'SD Card · Music/Disclosure/Caracal (Deluxe)']
    y = dict(texts)
    assert (y['Omen'], y['Disclosure'], y['3:57']) == (56, 77, 95)
    assert y['Lossless'] == y['FLAC · 16-bit / 44.1 kHz'] == 122
    assert [y[k] for k in ('Album', 'Caracal (Deluxe)', 'Genre', 'Size', 'Location')] == [146, 164, 190, 234, 278]
    assert g.canvas.art('file:///c.jpg') == [(24, 46 - page_y, 56, 56)]  # in the page's rows  # the cover, rounded by the page's black
    assert g.canvas.art('oled_quality_d')


def test_oled_about_names_its_cells_by_stock_s_names(fw, st, geometry):
    g = Grid(fw, st, geometry)
    texts = [t for t, _ in g.paint()]
    assert texts[5::2][:5] == ['Album', 'Year', 'Genre', 'Bit Rate', 'Size']


@pytest.mark.parametrize('encode, bits, sample, kbps, badge, spec', [
    ('Encoding:FLAC', 'Bit Depth:24bit', 'Sample Rate:96kHz', 'Bitrate:1978kbps', 'Hi-Res Lossless', 'FLAC · 24-bit / 96 kHz'),
    ('Encoding:FLAC', 'Bit Depth:16bit', 'Sample Rate:88.2kHz', 'Bitrate:1978kbps', 'Hi-Res Lossless', 'FLAC · 16-bit / 88.2 kHz'),
    ('Encoding:WAV', 'Bit Depth:16bit', 'Sample Rate:48kHz', 'Bitrate:1536kbps', 'Lossless', 'WAV · 16-bit / 48 kHz'),
    ('Encoding:MP3', 'Bit Depth:16bit', 'Sample Rate:44.1kHz', 'Bitrate:320kbps', None, 'MP3 · 320 kbps'),
    ('Encoding:DSF', 'Bit Depth:1bit', 'Sample Rate:1411.2kHz', 'Bitrate:5644kbps', 'DSD', 'DSD64'),
    ('Encoding:DSF', 'Bit Depth:1bit', 'Sample Rate:2822.4kHz', 'Bitrate:11289kbps', 'DSD', 'DSD128'),
])
def test_oled_about_s_quality_line(fw, st, geometry, encode, bits, sample, kbps, badge, spec):
    g = Grid(fw, st, geometry, dict(GRID_INFO, label_encode=encode, label_bits=bits, label_sample=sample, label_kbps=kbps))
    texts = [t for t, _ in g.paint()]
    assert texts[3:5] == ([badge, spec] if badge else [spec, 'Album'])
    assert bool(g.canvas.art('oled_quality_d')) == bool(badge)


def test_oled_about_leaves_out_what_is_unknown_and_closes_up(fw, st, geometry):
    """A missing genre, a year stock reads as unknown, an unknown bit rate: no cell, no "Unknown"; the rest close up."""
    g = Grid(fw, st, geometry, dict(GRID_INFO, label_genre='Genre:Unknown Artist', label_year='Year:Unknown Artist',
                          label_kbps='Bitrate:0kbps'))
    texts = g.paint()
    names = [t for t, _ in texts][5::2]
    assert names == ['Album', 'Size', 'Location'] and not any('Unknown' in t for t, _ in texts)
    y = dict(texts)  # screen rows
    assert (y['Album'], y['Size'], y['Location']) == (146, 146, 190)


def test_oled_about_takes_a_year_0_for_none(fw, st, geometry):
    g = Grid(fw, st, geometry, dict(GRID_INFO, label_year='Year:0'))
    assert 'Year' not in [t for t, _ in g.paint()]


@pytest.mark.parametrize('path, location', [
    ('/mnt/mmc/.subsonic/stream/night swim [s1].flac', 'Server · Streaming'),
    ('/mnt/mmc/.subsonic/stream/a1b2c3d4/raw/7/night swim [s2].flac', 'Server · Downloaded'),
    ('/mnt/mmc/Subsonic/Fleet Avenue/Night Swim/01 Night Swim.flac', 'Server · Downloaded'),
    ('/mnt/mmc/song.flac', 'SD Card'),
])
def test_oled_about_s_location_in_plain_words(fw, st, geometry, path, location):
    g = Grid(fw, st, geometry, path=path)
    texts = [t for t, _ in g.paint()]
    assert texts[-2:] == ['Location', location] and not any('.subsonic' in t for t in texts)


def test_oled_about_s_wheel_scrolls_a_row_to_its_end(fw, st, geometry):
    """Taller than the screen (Location ends at 314, the list's end 282): a tick down scrolls 32 px, as far as there
    is, a tick up back; the page takes every tick, Centre and Return go on."""
    g = Grid(fw, st, geometry)
    key = fw.fn('np_about_key', C.c_int, C.c_void_p, C.c_uint)
    loc = dict(g.paint())['Location']
    assert key(g.page.addr, O['KEY_NEXT']) == 1 and dict(g.paint())['Location'] == loc - 32
    assert key(g.page.addr, O['KEY_NEXT']) == 1 and dict(g.paint())['Location'] == loc - 32  # the end
    assert key(g.page.addr, O['KEY_PREV']) == 1 and dict(g.paint())['Location'] == loc
    assert key(g.page.addr, O['KEY_CENTER']) == 0 and key(g.np.addr, O['KEY_NEXT']) == 0
    g.back()  # closed: About opens once
    short = Grid(fw, st, geometry, dict(GRID_INFO, label_genre='Genre:', label_year='Year:', label_kbps='Bitrate:', label_size='Size:'))
    loc = dict(short.paint())['Location']
    assert key(short.page.addr, O['KEY_NEXT']) == 1 and dict(short.paint())['Location'] == loc  # it fits: still


def test_oled_about_s_drag_scrolls_it_between_the_wheel_s_ends(fw, st, geometry):
    """A finger dragged up moves the grid with it, pixel for pixel, no further than the wheel's end (32 px here) and
    no higher than its top; a flick stops where it lifts, a move after the lift does nothing, and the wheel goes on
    from where the drag left it."""
    g = Grid(fw, st, geometry)

    def touch(kind, y):
        for w, evt, fn, ctx in list(g.ui.handlers):
            if evt == kind and w in (g.body.addr, g.page.addr):
                C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, event(kind, x=187, y=y))

    loc = dict(g.paint())['Location']
    down, move, up = O['EVT_POINTER_DOWN'], O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE']
    touch(down, 250)
    touch(move, 240)
    assert dict(g.paint())['Location'] == loc - 10
    touch(move, 100)  # a flick far past the end
    assert dict(g.paint())['Location'] == loc - 32
    touch(move, 290)  # and back past the top
    assert dict(g.paint())['Location'] == loc
    touch(move, 230)
    touch(up, 230)
    assert dict(g.paint())['Location'] == loc - 20
    touch(move, 100)  # lifted: no longer the drag's
    assert dict(g.paint())['Location'] == loc - 20
    key = fw.fn('np_about_key', C.c_int, C.c_void_p, C.c_uint)
    assert key(g.page.addr, O['KEY_PREV']) == 1 and dict(g.paint())['Location'] == loc
    touch(down, 200)
    touch(O['EVT_POINTER_ABORT'], 200)
    touch(move, 150)  # aborted: still
    assert dict(g.paint())['Location'] == loc


def test_oled_about_repaints_only_on_a_change(fw, st, geometry):
    g = Grid(fw, st, geometry)
    g.invalidated.clear()
    g.info.fill(g.np.addr)
    assert g.body.addr not in g.invalidated
    g.info.labels['label_year'].text = 'Year:2016'
    g.info.fill(g.np.addr)
    assert g.body.addr in g.invalidated and '2016' in [t for t, _ in g.paint()]


# Touch and click dispatch.
POINTER_DOWN, POINTER_MOVE, POINTER_UP = O['EVT_POINTER_DOWN'] - 1, O['EVT_POINTER_MOVE_BEFORE'] - 1, O['EVT_POINTER_UP_BEFORE'] - 1
KEY_DOWN, KEY_DOWN_BEFORE = O['EVT_KEY_DOWN'], O['EVT_KEY_DOWN_BEFORE']
SLIDE_PX = nav_define('SLIDE_PX')


class Quick(C.Structure):
    """navigation.c's quick: Quick Settings' press, hold and slider state."""
    _fields_ = [('win', C.c_void_p), ('icon', C.c_void_p), ('slider', C.c_void_p), ('timer', C.c_uint),
                ('x', C.c_int), ('y', C.c_int), ('sx', C.c_int), ('sy', C.c_int), ('slide', C.c_int)]


class Ple(C.Structure):
    """navigation.c's ple: the playlist editor's page and keys."""
    _fields_ = [('page', C.c_void_p), ('view', C.c_void_p), ('songs', C.c_void_p), ('picker', C.c_void_p), ('edit', C.c_void_p),
                ('match', C.c_void_p), ('dest', C.c_int), ('mode', C.c_int), ('selected', C.c_int), ('pending', C.c_int),
                ('nmatch', C.c_int), ('key', C.c_uint), ('timer', C.c_uint), ('name', C.c_char * 128), ('query', C.c_char * 128)]


class Stock:
    """Stock's dispatch and touch handlers behind ours: each event they get, as (target, type, x, y), and their answer."""
    def __init__(self, fw, ui, answer=0):
        self.fw, self.ui, self.answer, self.seen, self.touched = fw, ui, answer, [], []
        fw.mock('stock_dispatch_trampoline', lambda target, e, *_: self.got(self.seen, target, e))
        fw.mock('stock_touch_trampoline', lambda ctx, e, *_: self.got(self.touched, ctx, e))
        fw.mock('pointer_event_init', self.init)
        fw.mock('timer_remove', lambda *_: 0)
        self.dispatch = fw.fn('ringnav_dispatch', C.c_int, C.c_void_p, C.c_void_p)
        self.touch = fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)

    def got(self, log, target, e):
        log.append((target, *self.read(e)))
        return self.answer

    @staticmethod
    def read(e):
        return (C.c_int.from_address(e).value, *struct.unpack('<ii', C.string_at(e + EVENT_X, 8))) if e else (None, None, None)

    @staticmethod
    def init(ev, kind, target, x, y, *_):
        C.memset(ev, 0, 0x30)
        struct.pack_into('<i', (C.c_char * 0x30).from_address(ev), 0, kind)
        struct.pack_into('<ii', (C.c_char * 0x30).from_address(ev), EVENT_X, x, y)
        return ev

    def send(self, target, kind, key=0, x=None, y=None):
        e = event(kind, key, x, y)
        return self.dispatch(target.addr if target else None, e)


def test_a_click_reaches_stock_and_ends_whatever_was_pending(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    row = ui.add('button', page)
    stock = Stock(fw, ui, answer=5)
    st('center_timer').value, st('touch_mode').value = 7, 0
    assert stock.send(row, EVT_CLICK) == 5
    assert stock.seen == [(row.addr, EVT_CLICK, 0, 0)]
    # the outline hides (a tap is touch), and the pending confirmation goes
    assert st('touch_mode').value == 1 and st('center_timer').value == 0


def test_a_click_a_pull_claimed_never_reaches_stock(fw):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    view = ui.add('scroll_view', page, _pull_suppress=1)
    row = ui.add('button', ui.add('list_item', view))
    stock = Stock(fw, ui)
    assert stock.send(row, EVT_CLICK) == STOP and stock.seen == []


def test_the_release_of_a_quick_settings_hold_is_no_click(fw):
    ui = Ui(fw)
    icon = ui.add('image', ui.window('statusbar_dialog'), _quick_hold=-2)
    stock = Stock(fw, ui)
    assert stock.send(icon, EVT_CLICK) == STOP and stock.seen == []


@pytest.mark.parametrize('button, clock, back', [('btn_enter', 0, 1), ('btn_enter', 3, 1), ('btn_cancel', 0, 0)])
def test_set_time_ok_goes_back_to_the_time_page(fw, st, button, clock, back):
    ui = Ui(fw)
    page = ui.window('manualtime_page')
    target = ui.add('button', page, name=button)
    stock = Stock(fw, ui)
    st('clock_timer').value = clock
    stock.send(target, EVT_CLICK)
    assert len(stock.seen) == 1 and len(fw.calls('navigator_back')) == back
    # a running bar clock shows the time just set at once: it reads the clock again
    assert bool(fw.calls('fw_time')) == bool(clock)


def test_set_time_ok_that_closed_its_page_goes_nowhere_else(fw):
    ui = Ui(fw)
    page = ui.window('manualtime_page')
    target = ui.add('button', page, name='btn_enter')
    stock = Stock(fw, ui)
    fw.mock('stock_dispatch_trampoline', lambda *_: ui.wm.children.remove(page) or 0)
    stock.send(target, EVT_CLICK)
    assert not fw.calls('navigator_back')


@pytest.mark.parametrize('kind', [KEY_DOWN, KEY_DOWN_BEFORE, O['EVT_KEY_UP'], O['EVT_KEY_LONG']])
def test_the_playlist_editors_buttons_never_reach_stock(fw, kind):
    ui = Ui(fw)
    target = ui.add('button', ui.window('home_page'))
    stock = Stock(fw, ui)
    fw.var('playlists__ple', Ple).page = ui.window('playlist_editor').addr
    assert stock.send(target, kind, O['KEY_BACK_BTN']) == STOP and stock.seen == []
    assert stock.send(target, kind, O['KEY_RETURN']) == 0 and len(stock.seen) == 1
    # the window manager's own still reaches stock: its key hooks there (ringnav_keydown, ringnav) move the editor
    assert stock.send(ui.wm, kind, O['KEY_BACK_BTN']) == 0 and stock.seen[-1][0] == ui.wm.addr


@pytest.mark.parametrize('top, inside, passes', [
    ('coverflow_page', True, True), ('coverflow_page', False, False), ('artistinfo_page', False, False),
    ('localmusic_page', False, True)])
def test_wheel_keys_to_a_covered_windows_widget_are_dropped(fw, top, inside, passes):
    ui = Ui(fw)
    under = ui.window('localmusic_page')
    covered = ui.add('list_view', under)
    page = ui.window(top)
    target = ui.add('view', page) if inside else covered
    stock = Stock(fw, ui)
    for key in (O['KEY_PREV'], O['KEY_NEXT']):
        assert stock.send(target, KEY_DOWN, key) == (0 if passes else STOP)
    assert len(stock.seen) == (2 if passes else 0)
    stock.send(target, KEY_DOWN, O['KEY_CENTER'])
    assert len(stock.seen) == (3 if passes else 1)


def test_a_key_ends_a_pending_pull(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    prompt = ui.add('label', page, name='_pull_prompt')
    stock = Stock(fw, ui)
    st('pull_page', C.c_void_p).value = page.addr
    stock.send(page, KEY_DOWN_BEFORE, O['KEY_CENTER'])
    assert not st('pull_page', C.c_void_p).value and not prompt.visible


class QuickPage:
    """Quick Settings pulled down (state 3): its dialog with the brightness slider."""
    def __init__(self, fw, ui, st, state=3):
        self.dialog = ui.window('statusbar_dialog')
        self.slider = ui.add('slider', ui.add('view', self.dialog), (40, 200, 300, 30), 'slider_backlight')
        self.state = C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_dropdown_menu_state', C.c_char)))
        self.state.value = state
        self.quick = fw.var('navigation__quick', Quick)


def test_the_brightness_slider_takes_a_sideways_drag(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    assert stock.send(s, POINTER_DOWN, x=100, y=210) == 0 and stock.seen == []  # held back
    stock.send(s, POINTER_MOVE, x=100 + SLIDE_PX - 1, y=210)
    assert stock.seen == []
    stock.send(s, POINTER_MOVE, x=100 + SLIDE_PX, y=212)
    # the press it never had, where it began, then this move
    assert stock.seen == [(s.addr, POINTER_DOWN, 100, 210), (s.addr, POINTER_MOVE, 100 + SLIDE_PX, 212)]
    stock.send(s, POINTER_UP, x=150, y=212)
    assert stock.seen[-1] == (s.addr, POINTER_UP, 150, 212) and not qs.quick.slider


def test_the_brightness_slider_gives_a_vertical_drag_to_the_swipe(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    stock.send(s, POINTER_DOWN, x=100, y=210)
    stock.send(s, POINTER_MOVE, x=102, y=210 - SLIDE_PX)
    stock.send(s, POINTER_MOVE, x=100 + 3 * SLIDE_PX, y=190)  # sideways once the swipe has it: still the swipe's
    stock.send(s, POINTER_UP, x=130, y=150)
    assert stock.seen == [] and qs.quick.slide == -1 and not qs.quick.slider


def test_a_tap_on_the_brightness_slider_is_its_press_and_release(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    stock.send(s, POINTER_DOWN, x=100, y=210)
    stock.send(s, POINTER_UP, x=101, y=211)
    assert stock.seen == [(s.addr, POINTER_DOWN, 100, 210), (s.addr, POINTER_UP, 101, 211)]


def test_the_slider_loses_its_drag_while_the_pull_down_slides(fw, st):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    s = qs.slider
    stock.send(s, POINTER_DOWN, x=100, y=210)
    stock.send(s, POINTER_MOVE, x=130, y=210)
    qs.state.value = 2
    stock.send(s, POINTER_MOVE, x=140, y=210)
    assert [k for _, k, _, _ in stock.seen] == [POINTER_DOWN, POINTER_MOVE, O['EVT_POINTER_ABORT']]
    stock.send(s, POINTER_MOVE, x=150, y=210)
    assert len(stock.seen) == 3


def test_settings_brightness_slider_is_stocks_alone(fw, st):
    ui = Ui(fw)
    slider = ui.add('slider', ui.window('backlight_page'), name='slider_backlight')
    stock = Stock(fw, ui)
    stock.send(slider, POINTER_DOWN, x=100, y=210)
    assert stock.seen == [(slider.addr, POINTER_DOWN, 100, 210)] and not fw.var('navigation__quick', Quick).slider


@pytest.mark.parametrize('kind, dx, cancels', [
    (POINTER_MOVE, 10, 0), (POINTER_MOVE, 11, 1), (POINTER_UP, 0, 1), (O['EVT_POINTER_ABORT'], 0, 1), (EVT_CLICK, 0, 1),
    (KEY_DOWN_BEFORE, None, 1)])
def test_a_quick_settings_hold_ends_with_a_move_or_any_end(fw, st, kind, dx, cancels):
    ui = Ui(fw)
    qs = QuickPage(fw, ui, st)
    stock = Stock(fw, ui)
    icon = ui.add('image', qs.dialog)
    qs.quick.timer, qs.quick.x, qs.quick.y, qs.quick.win, qs.quick.icon = 4, 60, 60, qs.dialog.addr, icon.addr
    if dx is None:
        stock.send(icon, kind, O['KEY_CENTER'])
    else:
        stock.send(icon, kind, x=60 + dx, y=60)
    assert (qs.quick.timer, qs.quick.icon) == ((0, None) if cancels else (4, icon.addr))


def test_a_touch_reaches_stock_and_hides_the_outline_once(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    stock = Stock(fw, ui, answer=3)
    st('center_timer').value = 7
    down = event(O['EVT_POINTER_DOWN'], x=100, y=100)
    assert stock.touch(None, down) == 3 and stock.touched == [(0, O['EVT_POINTER_DOWN'], 100, 100)]
    assert st('touch_mode').value == 1 and st('center_timer').value == 0
    repainted = [a[0] for _, a in fw.calls('widget_invalidate_force')]
    assert repainted == [page.addr]
    stock.touch(None, event(O['EVT_POINTER_MOVE_BEFORE'], x=100, y=120))
    assert [a[0] for _, a in fw.calls('widget_invalidate_force')] == repainted  # already hidden


@pytest.mark.parametrize('claimed', [0, 1])
def test_a_touch_on_a_list_marks_it_touched_unless_stock_took_it(fw, st, claimed):
    ui = Ui(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.mock('widget_get_prop_bool', lambda w, name, default, *_: default)
    page = ui.window('localmusic_page')
    view = ui.add('scroll_view', page, (0, 30, 375, 290), 'scroll_view_localmusic')
    view.block[O['VIEW_VERTICAL']] = b'\1'  # yslidable: a list the wheel moves
    for _ in range(3):
        ui.add('button', ui.add('list_item', view, (0, 0, 375, 70)))
    stock = Stock(fw, ui, answer=claimed)
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=100, y=100))
    # the list draws its touch look, the same touch going on to stock's tap and drag
    assert view.props.get('_ringnav_touch', 0) == (0 if claimed else 1)
    assert (view.addr in [a[0] for _, a in fw.calls('widget_invalidate_force')]) == (not claimed)


def test_a_touch_cancels_a_pending_untouch(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    stock = Stock(fw, ui)
    st('touch_mode').value, st('untouch_timer').value = 1, 6
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=1, y=100))
    assert st('untouch_timer').value == 0 and st('touch_mode').value == 1
    assert [a[0] for _, a in fw.calls('timer_remove')][:1] == [6] and fw.calls('widget_invalidate_force')[0][1][0] == page.addr


def test_untouch_brings_the_outline_back(fw, st):
    ui = Ui(fw)
    page = ui.window('localmusic_page')
    st('touch_mode').value, st('untouch_timer').value = 1, 6
    assert fw.fn('navigation__untouch', C.c_int, C.c_void_p)(None) == 0
    assert (st('touch_mode').value, st('untouch_timer').value) == (0, 0)
    assert [a[0] for _, a in fw.calls('widget_invalidate_force')] == [page.addr]


class Scrub:
    """Now Playing after a paused seek: the settle wait running, its slider at (20, 200, 335, 30)."""
    def __init__(self, fw, ui, st, status):
        page = ui.window('playing_page')
        self.slider = ui.add('slider', page, (20, 200, 335, 30))
        st('np_slider', C.c_void_p).value = self.slider.addr
        st('settle_timer').value = 8
        fw.mock('mclGetPlayStatus', lambda *_: status)


@pytest.mark.parametrize('status, at, keeps', [(3, (100, 100), 1), (3, (100, 210), 0), (1, (100, 100), 0)])
def test_a_swipe_keeps_a_paused_seeks_preview(fw, st, status, at, keeps):
    ui = Ui(fw)
    Scrub(fw, ui, st, status)
    stock = Stock(fw, ui)
    stock.touch(None, event(O['EVT_POINTER_DOWN'], x=at[0], y=at[1]))
    # off the slider while paused the seek waits on; on it, or playing, the wait ends as any scrub's
    assert st('settle_timer').value == (8 if keeps else 0)


# The rows' layouts: stock's default row layouter with ours in its slot, and the settings lists'.
VTABLE, LIST_LAYOUT = O['DEFAULT_LAYOUT_VTABLE'], O['LIST_VIEW_LAYOUT']
MARGIN, SPACING = 8, 6


@pytest.fixture
def stand_in():
    """place(address, bytes): stock's memory at a fixed address, its page made executable for stand-in code; put
    back after the test, as conftest's map outlives a load."""
    saved = []
    def place(address, data):
        claim(address)
        saved.append((address, C.string_at(address, len(data))))
        code_at(address, data)
    yield place
    for address, data in reversed(saved):
        code_at(address, data)


def logger(log, result=0):
    """Stand-in code: its first two arguments into log[0] and log[1], then result."""
    def call(a, b, _):
        log[0], log[1] = a, b
        return result & 0xffffffff
    return callback_code(call)


@pytest.fixture
def default_layout(stand_in):
    """Stock's default row layouter: its vtable's third slot a stand-in logging (layout, row) and returning 4."""
    log = (C.c_uint64 * 2)()
    code = VTABLE + 0x100
    stand_in(code, logger(log, 4))
    stand_in(VTABLE, struct.pack('<8I', *(0x5f0000 + 4 * i for i in range(8))))
    stand_in(VTABLE + 8, struct.pack('<I', code))
    return log


def layout():
    lay = C.create_string_buffer(0x40)
    struct.pack_into('<B', lay, O['DEFAULT_LAYOUT_X_MARGIN'], MARGIN)
    struct.pack_into('<B', lay, O['DEFAULT_LAYOUT_SPACING'], SPACING)
    return lay


class Rows:
    """A list's rows in a window, laid out by compact_row_layout."""
    def __init__(self, fw, ui, st, geometry, window='localmusic_page'):
        names, self.rows = geometry
        self.names = names
        Config(fw, THEME=b'%d' % OLED)
        fw.mock('tk_str_start_with', stock_start_with)
        self.ui, self.fw = ui, fw
        self.view = ui.add('scroll_view', ui.window(window), (0, 30, 375, 290))
        self.lay = layout()
        self.run = fw.fn('navigation__compact_row_layout', C.c_int, C.c_void_p, C.c_void_p)
        self.theme = OLED_THEME

    def row(self, h, *children):
        """A row h high holding children: (kind, box, name) each, a 'view' kind with its own (kind, box, name) list."""
        item = self.ui.add('list_item', self.view, (0, 0, 375, h))
        for kind, box, name, *inner in children:
            w = self.ui.add(kind, item, box, name)
            for k, b, n in (inner[0] if inner else ()):
                self.ui.add(k, w, b, n)
        return item

    def __call__(self, item):
        return self.run(C.addressof(self.lay), item.addr)


ICON = ('image', (MARGIN, 10, 52, 52), 'img_icon')


def test_a_rows_title_takes_the_width_its_siblings_leave(fw, st, geometry, default_layout):
    """A one-line row (the set's pitch): the title's box is the row's less the margins, and less a shown sibling."""
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    pitch = rows.rows['geom_words'][rows.names['G_PITCH']]
    item = rows.row(pitch, ('hscroll_label', (MARGIN, 0, 200, pitch), 'title'), ('label', (300, 0, 20, pitch), 'mark'))
    title = item.children[0]
    assert rows(item) == 4 and list(default_layout) == [C.addressof(rows.lay), item.addr]  # then stock lays it out
    shown = title.box[2]
    assert fw.calls('hscroll_label_set_only_focus')[-1][1][:2] == (title.addr, 1)  # the ellipsis, not stock's scroll
    item.children[1].visible = 0
    rows(item)
    assert title.box[2] == 375 - 2 * MARGIN and shown <= title.box[2] - (20 + SPACING)  # a hidden sibling leaves its width


def test_a_drill_rows_title_ends_clear_of_the_chevron(fw, st, geometry, default_layout):
    ui = Ui(fw)
    plain = Rows(fw, ui, st, geometry)
    pitch = plain.rows['geom_words'][plain.names['G_PITCH']]
    title = ('hscroll_label', (MARGIN, 0, 200, pitch), 'title')
    a = plain.row(pitch, title)
    plain(a)
    drill = Rows(fw, ui, st, geometry, window='home_page')  # DRILL: its rows open lists, with no stock list_into
    b = drill.row(pitch, title)
    drill(b)
    right = [r.children[0].box[0] + r.children[0].box[2] for r in (a, b)]
    assert right[0] == 375 - MARGIN  # a plain row's title runs to the margin
    # a drill row's ends a spacing short of the drawn chevron, whose left edge is CHEVRON_W from the row's right
    # edge (docs/internals.md), as a title ends before a stock img_into
    assert right[1] + SPACING == 375 - O['CHEVRON_W']


TWO_LINES = [('hscroll_label', (0, 0, 200, 16), 'title'), ('hscroll_label', (0, 16, 200, 16), 'artist')]


def test_oleds_one_line_row_hides_its_art_and_second_line(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    pitch = rows.rows['geom_words'][rows.names['G_PITCH']]
    text_x = rows.rows['geom_vals'][rows.names['V_TEXT_X']]
    item = rows.row(pitch, ICON, ('image', (66, 0, 20, 20), 'img_playing'), ('image', (90, 0, 26, 26), 'img_choice0'),
                    ('view', (MARGIN, 0, 200, pitch), 'view_info', TWO_LINES))
    text = item.children[3]
    title, second = text.children
    rows(item)
    assert [c.visible for c in item.children] == [0, 0, 1, 1]  # stock's choice box stays
    assert (title.visible, second.visible) == (1, 0)
    width = 375 - 2 * MARGIN - (26 + SPACING)
    # text leading the row moves to the set's inset, its right edge kept
    assert text.box[0] == text_x and text.box[0] + text.box[2] == MARGIN + width
    assert title.box[2] == width - title.box[0]


def test_oleds_two_line_row_without_art_keeps_both_lines_and_a_chevrons_ends_at_the_art_column(fw, st, geometry,
                                                                                               default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    text_x = rows.rows['geom_vals'][rows.names['V_TEXT_X']]
    item = rows.row(52, ('image', (MARGIN, 0, 0, 52), 'img_icon'), ('view', (MARGIN, 0, 300, 52), 'view_info', TWO_LINES))
    with_chevron = rows.row(52, ('image', (MARGIN, 0, 0, 52), 'img_icon'), ('view', (MARGIN, 0, 300, 52), 'view_info', TWO_LINES),
                            ('image', (300, 0, 58, 52), 'img_into'))
    with_chevron.children[2].props['image'] = b'list_into'
    rows(item)
    rows(with_chevron)
    text = item.children[1]
    title, second = text.children
    assert not item.children[0].visible and second.visible  # the two lines stay
    # the text from the set's inset, both lines as wide as its box
    assert text.box[0] == text_x and title.box[2] == second.box[2] == text.box[2]
    # a chevron's row ends its text where an art row's does, hscroll_label's 2 px margin past the theme's column
    other = with_chevron.children[1]
    assert (other.box[0], other.box[0] + other.box[2]) == (text_x, rows.theme['art_text_end'] + 2)


def test_oleds_art_row_puts_cover_and_text_on_the_themes_columns(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    item = rows.row(52, ('image', (MARGIN, 0, 52, 52), 'img_icon'), ('view', (66, 0, 300, 52), 'view_info',
                    [('hscroll_label', (0, 0, 200, 26), 'title'), ('view', (0, 26, 151, 26), 'meta')]))
    meta_label = ui.add('hscroll_label', item.children[1].children[1], (0, 0, 151, 26))
    rows(item)
    art, text = item.children
    t = rows.theme
    assert art.box[0] == t['art_x'] and text.box[0] == t['art_text_x'] - 2
    assert text.box[0] + text.box[2] == t['art_text_end'] + 2
    # stock's 151 px second line takes the text's width, as the title does
    title, meta = text.children
    assert title.box[2] == meta.box[2] == meta_label.box[2] == text.box[2]
    # the second line takes the theme's muted colour, the title keeps its style's
    assert 'style:normal:text_color' in meta_label.props and 'style:normal:text_color' not in title.props


def test_an_art_row_with_stocks_choice_box_keeps_stocks_places(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    item = rows.row(52, ('image', (MARGIN, 0, 26, 52), 'img_choice0'), ('image', (40, 0, 52, 52), 'img_icon'),
                    ('view', (100, 0, 200, 52), 'view_info', TWO_LINES))
    rows(item)
    assert [c.box[0] for c in item.children] == [MARGIN, 40, 100]


def test_a_rows_second_line_takes_the_meta_grey_once_where_the_theme_has_a_meta_face(fw, st, geometry, default_layout):
    assert OLED_THEME['font[ROLE_META]']  # OLED's own meta face (theme.xml list.row meta_font)
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    item = rows.row(72, ('view', (MARGIN, 0, 300, 72), 'view_info', TWO_LINES))
    rows(item)
    second = item.children[0].children[1]
    assert 'style:normal:text_color' in second.props
    second.props['style:normal:text_color'] = 0
    rows(item)  # already in its face: left as it is
    assert second.props['style:normal:text_color'] == 0


def test_a_playlist_row_keeps_its_own_places(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    h = rows.theme['page_detail']  # pl_row's height: a name over its count
    def playlist(trailing, image):
        item = rows.row(h)
        item.props['_pl_row'] = 1
        button = ui.add('button', item, (0, 0, 375, h))
        mark = ui.add('image', button, (280, 10, 40, 32), trailing)  # stock's own place
        mark.props['image'] = image
        info = ui.add('view', button, (72, 0, 200, h), 'view_info')
        for y in (0, 26):
            ui.add('hscroll_label', info, (0, y, 200, 26))
        return button, mark, info
    dots, tick = playlist('img_more0', b'unselect'), playlist('img_choice0', b'mulselect')  # the list's, a picker's
    for button, _, _ in (dots, tick):
        assert rows(button) == 0
    assert list(default_layout) == [0, 0]  # stock's layout never runs
    for button, mark, info in (dots, tick):
        name, count = info.children
        assert (mark.box[1], mark.box[3]) == (0, h) and mark.box[0] + mark.box[2] <= 375  # the row's height, in it
        assert info.box[0] + info.box[2] <= mark.box[0]  # the text ends clear of it
        assert name.box[1] < count.box[1] and name.box[2] == count.box[2] == info.box[2]
    # the dots and a picker's mark share one column, centred on the tick
    assert dots[1].box[0] + dots[1].box[2] / 2 == tick[1].box[0] + tick[1].box[2] / 2
    assert tick[1].props['image'] == b'select'  # stock's disc drawn as a picker's tick
    # the selected row's chevron shows in the dots' place: the text ends clear of it too
    assert dots[2].box[0] + dots[2].box[2] <= 375 - O['CHEVRON_W']


def test_a_row_of_choice_boxes_alone_is_stocks(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    item = rows.row(52, ('image', (MARGIN, 0, 26, 52), 'img_choice0'), ('image', (40, 0, 26, 52), 'img_choice1'))
    assert rows(item) == 4 and [c.visible for c in item.children] == [1, 1] and [c.box[0] for c in item.children] == [MARGIN, 40]


def test_a_row_with_no_title_is_stocks(fw, st, geometry, default_layout):
    ui = Ui(fw)
    rows = Rows(fw, ui, st, geometry)
    item = rows.row(32, ICON, ('label', (66, 0, 200, 32), 'label'))
    assert rows(item) == 4 and [c.visible for c in item.children] == [1, 1] and item.children[1].box == (66, 0, 200, 32)


def layout_of(fw, row):
    """A row with stock's children layout object at W_CHILDREN_LAYOUT."""
    lay = C.create_string_buffer(0x40)
    C.c_void_p.from_address(row.addr + O['W_CHILDREN_LAYOUT']).value = C.addressof(lay)
    return lay


def test_a_compact_rows_layout_is_stocks_with_ours_in_its_slot(fw, default_layout):
    ui = Ui(fw)
    rows = [ui.add('list_item', ui.window('localmusic_page')) for _ in range(2)]
    lays = [layout_of(fw, r) for r in rows]
    install = fw.fn('compact_set_row_layout', C.c_int, C.c_void_p, C.c_char_p)
    assert [install(r.addr, b'default(h=72)') for r in rows] == [0, 0]
    vtables = [C.c_void_p.from_buffer(lay, O['CHILDREN_LAYOUT_VTABLE']).value or 0 for lay in lays]
    assert vtables[0] == vtables[1]  # one table, made once
    entries = list((C.c_uint * 8).from_address(vtables[0]))
    ours = C.cast(fw.lib.navigation__compact_row_layout, C.c_void_p).value or 0
    assert entries == [0x5f0000 + 4 * i if i != 2 else ours & 0xffffffff for i in range(8)]
    assert [C.string_at(a[1]) for _, a in fw.calls('widget_set_children_layout')] == [b'default(h=72)'] * 2


def test_a_row_stock_refused_a_layout_keeps_its_own(fw, default_layout):
    ui = Ui(fw)
    row = ui.add('list_item', ui.window('localmusic_page'))
    lay = layout_of(fw, row)
    fw.mock('widget_set_children_layout', lambda *_: 3)
    assert fw.fn('compact_set_row_layout', C.c_int, C.c_void_p, C.c_char_p)(row.addr, b'x') == 3
    assert not C.c_void_p.from_buffer(lay, O['CHILDREN_LAYOUT_VTABLE']).value


def test_stocks_table_rows_take_ours_under_a_set_that_repitches_them(fw, st, geometry, default_layout):
    ui = Ui(fw)
    row = ui.add('list_item', ui.window('localmusic_page'))
    lay = layout_of(fw, row)
    assert fw.fn('stock_set_row_layout', C.c_int, C.c_void_p, C.c_char_p)(row.addr, b'x') == 0
    assert C.c_void_p.from_buffer(lay, O['CHILDREN_LAYOUT_VTABLE']).value


def test_queues_label_goes_to_the_sets_text_inset(fw, st, geometry):
    names, rows_ = geometry
    Config(fw, THEME=b'%d' % OLED)
    ui = Ui(fw)
    button = ui.add('button', ui.window('playlist_page'), (O['SET_STOCK_X'], 0, 335, 70))
    label = ui.add('hscroll_label', button, (10, 0, 260, 70))
    other = ui.add('hscroll_label', button, (72, 0, 200, 70))
    text = fw.fn('stock_row_text', C.c_int, C.c_void_p, C.c_char_p)
    assert text(label.addr, b'Queue') == 0 and text(other.addr, b'Song') == 0
    assert (label.text, other.text) == ('Queue', 'Song')
    inset = rows_['geom_vals'][names['V_TEXT_X']]
    assert button.box[0] + label.box[0] == inset and label.box[0] + label.box[2] == 10 + 260  # its right edge kept
    assert other.box == (72, 0, 200, 70) and 'style:normal:font_name' in label.props


def test_the_playing_rows_red_keeps_its_colour_under_a_repitched_set(fw, st, geometry):
    ui = Ui(fw)
    playing, other = (ui.add('hscroll_label', ui.window('playlist_page')) for _ in range(2))
    style = fw.fn('stock_row_style', C.c_int, C.c_void_p, C.c_char_p)
    playing.text, other.text = 'Song', 'Other'
    style(playing.addr, b's_scrlabel_red24l')
    style(other.addr, b's_scrlabel_white24l')
    assert (playing.props['style'], other.props['style']) == ('s_scrlabel_red24l', 's_scrlabel_white24l')
    assert (playing.text, other.text) == ('▶ Song', 'Other')
    red = O['STOCK_RED']
    colour = 'style:normal:text_color'
    assert playing.props[colour] == C.c_int(0xff000000 | (red & 255) << 16 | red & 0xff00 | red >> 16).value
    # every bind writes its colour: another row's is set too, not the red, and a reused row loses the red
    assert colour in other.props and other.props[colour] != playing.props[colour]
    style(playing.addr, b's_scrlabel_white24l')
    assert playing.props[colour] == other.props[colour]


def test_mores_speed_row_keeps_stocks_height_its_labels_before_the_buttons(fw, st, geometry):
    names, rows_ = geometry
    Config(fw, THEME=b'%d' % OLED)
    ui = Ui(fw)
    win = ui.window('playmore_page')
    row = ui.add('list_item', ui.add('scroll_view', win), (0, 0, 375, 32))
    button = ui.add('button', row, (O['SET_STOCK_X'], 0, 335, 70))
    icon = ui.add('image', button, (10, 9, O['SET_STOCK_ICON'], 52))
    speed = ui.add('hscroll_label', button, (72, 5, 120, 30))
    value = ui.add('hscroll_label', button, (72, 40, 120, 20))
    first = ui.add('image', button, (160, 5, 42, 61), 'img_forward_0')
    fw.fn('stock_more_speed', None, C.c_void_p)(win.addr)
    inset = rows_['geom_vals'][names['V_TEXT_X']]
    assert row.box[3] == O['SET_STOCK_ROW'] and not icon.visible
    assert button.box[0] + speed.box[0] == inset and speed.box[0] + speed.box[2] == first.box[0] - 2
    assert value.box[0] == speed.box[0] and value.box[1] > speed.box[1] + speed.box[3] - 1
    assert all('style:normal:font_name' in w.props for w in (speed, value))


# The settings lists (docs/ipod.md#settings): stock's 78 px rows of a 335 x 70 button at x 20, mapped to the set's.
SET_X, SET_W, STOCK_ROW, STOCK_BODY = O['SET_STOCK_X'], O['SET_STOCK_W'], O['SET_STOCK_ROW'], O['SET_STOCK_BODY']
SET_TEXT_X = O['SET_TEXT_X']


class SettingsList:
    """A settings page's list_view (its asset's default_item_height the set's settings row) and scroll view, laid out
    by ipod_list_layout around a stand-in for stock's list layouter, which notes the list's item_height it saw."""
    def __init__(self, fw, ui, st, geometry, stand_in, theme=b'%d' % OLED, view='scroll_view_wireless',
                 item_height=None, row_height=0, window='wireless_page'):
        names, rows = geometry
        Config(fw, THEME=theme)
        self.g = {n: rows['geom_vals'][names[n]] for n in ('V_SET_ROW', 'V_TEXT_X', 'V_RIGHT_X')}
        self.ui, self.fw = ui, fw
        self.win = ui.window(window)
        self.list = ui.add('list_view', self.win, (0, 30, 375, 290))
        struct.pack_into('<ii', self.list.block, O['ROW_HEIGHT'], row_height,
                         self.g['V_SET_ROW'] if item_height is None else item_height)
        self.view = ui.add('scroll_view', self.list, (0, 0, 375, 290), view)
        self.seen = (C.c_int * 1)(-1)
        def layouter(*_):  # notes the item_height it sees, answers 6
            self.seen[0] = C.c_int.from_address(self.list.addr + O['ROW_HEIGHT']).value
            return 6
        stand_in(LIST_LAYOUT, callback_code(layouter))
        self.run = fw.fn('ipod_list_layout', C.c_int, C.c_void_p, C.c_void_p)

    def row(self, *children, button=(SET_X, 0, SET_W, STOCK_BODY), h=STOCK_ROW, kind='button'):
        """A stock row: list_item > button > children, each (kind, box, name, image)."""
        item = self.ui.add('list_item', self.view, (0, 0, 375, h))
        b = self.ui.add(kind, item, button)
        for k, box, name, *image in children:
            w = self.ui.add(k, b, box, name)
            if image:
                w.props['image'] = image[0]
        return item

    def __call__(self):
        assert self.run(None, self.view.addr) == 6  # stock's answer
        return self.seen[0]


STOCK_ICON = ('image', (10, 9, O['SET_STOCK_ICON'], 52), 'icon', b'system_wifi')
STOCK_LABEL = ('hscroll_label', (72, 0, 260, STOCK_BODY), 'label')
STOCK_INTO = ('image', (276, 10, 50, 50), 'into', b'list_into')


def centre(box):
    return box[1] + box[3] / 2


def test_a_settings_row_takes_the_sets_height_icon_text_and_trailing_columns(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    assert lst() == 0
    row_h = lst.g['V_SET_ROW']
    button, (icon, label, into) = item.children[0], item.children[0].children
    assert item.box[3] == row_h and button.box == (0, 0, 375, row_h)  # the button spans the row
    assert not icon.visible  # the set has no settings icons (set_icon 0)
    assert label.box[0] == lst.g['V_TEXT_X'] - 2 and (label.box[1], label.box[3]) == (0, row_h)  # full height fills
    # shorter: its centre kept, its right edge on the set's right column
    assert into.box[0] + into.box[2] == 375 - lst.g['V_RIGHT_X'] and centre(into.box) == row_h / 2
    assert label.box[0] + label.box[2] <= 375 - SET_TEXT_X


def test_a_settings_row_is_mapped_once(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    lst()
    boxes = [c.box for c in item.children[0].children]
    lst()
    assert [c.box for c in item.children[0].children] == boxes


def test_a_hidden_row_is_mapped_after_it_shows(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    item.visible = 0
    lst()
    assert item.box[3] == lst.g['V_SET_ROW'] and item.children[0].box == (SET_X, 0, SET_W, STOCK_BODY)  # stock skips it
    item.visible = 1
    lst()
    assert item.children[0].box == (0, 0, 375, lst.g['V_SET_ROW'])


def test_rows_another_builder_made_keep_their_own(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    wide = lst.row(STOCK_LABEL, button=(0, 0, 375, STOCK_BODY))
    tall = lst.row(STOCK_LABEL, h=90)
    view_row = lst.row(STOCK_LABEL, kind='view')
    speed = lst.row(STOCK_LABEL, ('image', (160, 5, 42, 61), 'img_forward_0'))  # More's speed row: stock_more_speed's
    lst()
    assert [r.box[3] for r in (wide, tall, view_row, speed)] == [STOCK_ROW, 90, STOCK_ROW, STOCK_ROW]
    assert [r.children[0].box[0] for r in (wide, view_row, speed)] == [0, SET_X, SET_X]
    assert tall.children[0].box == (0, 0, 375, 90)  # a stock button in a taller row: mapped at the row's own height


def test_a_detail_line_doubles_a_short_row(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    detail = lst.row(('hscroll_label', (72, 5, 260, 30), 'name'), ('hscroll_label', (72, STOCK_BODY // 2, 260, 30), 'quality'))
    single = lst.row(('hscroll_label', (72, 5, 260, 30), 'name'))
    lst()
    row = lst.g['V_SET_ROW']
    # a row too short for stock's detail line (Bluetooth's Quality) takes two rows' height, or it would run into
    # the next row; a row with no detail line keeps the set's
    assert (detail.box[3], single.box[3]) == (2 * row, row)  # OLED's 32 px settings rows (theme.xml)


def test_oleds_rows_drop_their_icons_and_keep_a_pickers_mark(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    plain = lst.row(STOCK_ICON, STOCK_LABEL, STOCK_INTO)
    device = lst.row(('image', (10, 10, 50, 50), 'device', b'bt_headset'), STOCK_LABEL)
    picker = lst.row(('image', (10, 9, 52, 52), 'mark', b'select'), STOCK_LABEL)
    lst()
    row = lst.g['V_SET_ROW']
    # text starts on the set's text column less hscroll_label's 2 px, a trailing image ends on its right column
    # (navigation.c set_text_x, set_edge)
    text_x, edge = lst.g['V_TEXT_X'] - 2, lst.g['V_RIGHT_X']
    icon, label, into = plain.children[0].children
    assert not icon.visible and label.box[0] == text_x and centre(into.box) == row / 2
    assert not device.children[0].children[0].visible and device.children[0].children[1].box[0] == text_x
    mark = picker.children[0].children[0]
    assert mark.visible and mark.box == (375 - edge - 50, 0, 50, row)  # in the column of stock's own marks


def test_an_icon_sized_image_goes_wherever_it_sits(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_LABEL, ('image', (200, 9, O['SET_STOCK_ICON'], 52), 'badge', b'bt_codec'))
    lst()
    label, badge = item.children[0].children
    assert not badge.visible and label.visible


def test_a_wide_trailing_image_sets_the_right_column(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    item = lst.row(STOCK_LABEL, ('image', (230, 10, 50, 50), 'switch', b'switch_on'), ('image', (290, 10, 50, 50), 'into', b'list_into'))
    lst()
    _, switch, into = item.children[0].children
    # the rightmost trailing image ends on the set's right column; the others move with it
    assert into.box[0] + into.box[2] == 375 - lst.g['V_RIGHT_X'] and into.box[0] - switch.box[0] == 290 - 230


def test_another_list_is_stocks_alone(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, item_height=55)
    item = lst.row(STOCK_ICON, STOCK_LABEL)
    assert lst() == 0
    assert item.box[3] == STOCK_ROW and item.children[0].box == (SET_X, 0, SET_W, STOCK_BODY)


@pytest.mark.parametrize('tile, grid', [(110, 1), (335, 0)])
def test_a_grid_keeps_its_rows_height_under_a_forced_item_height(fw, st, geometry, stand_in, tile, grid):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, item_height=55, row_height=32)
    for _ in range(2):
        lst.row(button=(0, 0, tile, 150), h=160)
    # the artist page's albums: stock forces no item_height on its 160 px rows; a list's rows take it
    assert lst() == (0 if grid else 32)
    assert struct.unpack_from('<i', lst.list.block, O['ROW_HEIGHT'])[0] == 32  # put back after


def test_a_settings_list_takes_the_themes_top(fw, st, geometry, stand_in):
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in)
    top = OLED_THEME['set_top']
    lst.list.box = (0, top + 30, 375, 290)  # anywhere but the theme's top
    lst()
    names, rows = geometry
    # set_top counts from the status row's foot: in this full-bleed window (0, 0, 375, 320), the bar's height down
    assert lst.list.box[1] == top + rows['geom_vals'][names['V_BAR']]


# Hold Return's Now Playing, and the boot resume.
LATCH = O['RETURN_RELEASE_LATCH']


class Hold:
    """Hold Return on top of ui's windows: navigator calls move the window stack as stock's do."""
    def __init__(self, fw, ui, stand_in, hold_home=b'0'):
        self.fw, self.ui = fw, ui
        Config(fw, HOLDRETURN=hold_home, THEME=b'%d' % OLED)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        stand_in(LATCH, b'\1')  # stock's long Return armed it
        self.opened = []
        fw.mock('navigator_switch_to_with_context', self.switch)
        fw.mock('navigator_back', lambda *_: ui.wm.children.pop() and 0)
        fw.mock('navigator_back_to_home', lambda *_: ui.wm.children.__delitem__(slice(1, None)))
        self.run = fw.fn('compact_now_playing', C.c_int)
        self.lands = True

    def switch(self, name, ctx, *_):
        self.opened.append((C.string_at(name).decode(), struct.unpack('<4i', C.string_at(ctx, 16))))
        if self.lands:
            self.ui.window(C.string_at(name).decode())

    def __call__(self):
        assert self.run() == 0
        return [w.name for w in self.ui.wm.children]

    @staticmethod
    def latch():
        return C.c_ubyte.from_address(LATCH).value


def test_hold_return_opens_now_playing_over_the_page_and_leaves_the_latch_to_swallow_its_release(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page')
    songs = ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    assert hold() == ['home_page', 'localmusic_page', 'playing_page']
    assert hold.opened == [('playing_page', (0, 0, 0xff, 2))]  # stock's car-mode context: the queue as it is
    assert st('np_origin', C.c_void_p).value == songs.addr
    assert hold.latch() == 1  # stock's filter swallows the hold's release; cleared, it went back from Now Playing


def test_hold_return_on_now_playing_goes_back_to_the_page_it_opened_over(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    hold()
    assert hold() == ['home_page', 'localmusic_page'] and not st('np_origin', C.c_void_p).value


def test_hold_return_on_now_playing_opened_elsewhere_goes_home(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    hold()
    ui.wm.children.insert(2, W(ui, 'window', None, name='folder_page'))  # another page came between
    assert hold() == ['home_page']


def test_now_playing_not_opened_by_a_hold_goes_home(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page'), ui.window('playing_page')
    hold = Hold(fw, ui, stand_in)
    assert hold() == ['home_page'] and not fw.calls('navigator_back')


def test_hold_return_home_goes_home_from_any_page_but_home(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page'), ui.window('folder_page')
    hold = Hold(fw, ui, stand_in, hold_home=b'1')
    st('np_origin', C.c_void_p).value = 0x1234
    assert hold() == ['home_page'] and not st('np_origin', C.c_void_p).value
    assert hold() == ['home_page', 'playing_page']  # on Home itself it still opens Now Playing


def test_hold_return_does_nothing_dark_or_while_windows_move(fw, st, stand_in):
    ui = Ui(fw)
    ui.window('home_page'), ui.window('localmusic_page')
    hold = Hold(fw, ui, stand_in)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert hold() == ['home_page', 'localmusic_page']
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.mock('window_manager_is_animating', lambda *_: 1)
    assert hold() == ['home_page', 'localmusic_page'] and hold.opened == []


@pytest.mark.parametrize('ctx, forbidden, page, start', [
    ((0x51000, 3, 0xf001, 2), 0, 1, None),  # car mode plays: stock's page
    ((0x51000, 3, 0xf001, 2), 1, 0, (0x51000, 3, 0xf001, 3)),  # Headphones Required refuses it: paused, as below
    ((0x51000, 3, 0xf001, 3), 1, 0, (0x51000, 3, 0xf001, 3)),  # otherwise the paused start, without the page, unasked
    ((0x51000, 3, 0xff, 3), 0, 0, None)])  # no class: nothing to resume
def test_the_boot_resume_starts_paused_without_opening_now_playing(fw, ctx, forbidden, page, start):
    context = (C.c_int * 4)(*ctx)
    fw.mock('check_forbid_play_no_headset', lambda *_: forbidden)
    fw.fn('ringnav_boot', None, C.c_char_p, C.c_void_p)(b'playing_page', context)
    assert len(fw.calls('navigator_to_with_context')) == page
    assert [a[:4] for _, a in fw.calls('player_start')] == ([start] if start else [])


def test_an_unknown_artist_reads_nothing_on_a_text_only_set(fw, st, geometry):
    fw.mock('getFormatString', lambda key, text, size, *_: C.memmove(text, b'Unknown Artist\0', 15) and 0)
    out = C.create_string_buffer(64)  # the row's second line, empty until written
    fw.fn('ringnav_song_unknown', None, C.c_char_p, C.c_char_p, C.c_int)(b'player_unknown', out, 64)
    # a text-only set's second line says only what the record says
    assert out.value == b'' and fw.calls('getFormatString') == []


# Records: stSongInfo as stock's library fills them.
libc = C.CDLL(None)
libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
FIELDS = {k: O['REC_' + k.upper()] for k in ('name', 'path', 'title', 'album', 'artist', 'album_artist')}


class Records:
    """A pointer field is 4 bytes on the Q2 and 8 here, so each read takes the next field's low half as its high one.
    So a string goes where that does no harm: a low one (under 4 GB, its high half 0) keeps the next field readable,
    an aligned one (on a 4 GB boundary, its low half 0, in a page of its own at a high half of this file's:
    conftest.high_halves) the one before. A field and the next both set: the first low, the second aligned. A lone
    name or path is low, so the title after it stays unset; any other lone field is aligned, so the title or path
    before it stays as it was."""
    LOW = ('name', 'path')
    HIGHS = high_halves(0x100)

    def __init__(self):
        self.maps, self.used, self.high = [], 0, iter(self.HIGHS)
        self.low = low_map(1 << 20)
        self.maps.append((self.low, 1 << 20))

    def map(self, at, size, flags):
        p = libc.mmap(at, size, 3, 0x22 | flags, -1, 0)
        assert p and p != C.c_void_p(-1).value and (not at or p == at), f'{at:#x} is taken'
        self.maps.append((p, size))
        return p

    def put(self, at, data):
        C.memmove(at, data + b'\0', len(data) + 1)
        return at

    def take(self, n):
        at = self.low + self.used
        self.used += n + 15 & ~15
        return at

    def string(self, data, low):
        if low:
            return self.put(self.take(len(data) + 1), data)
        return self.put(self.map(next(self.high) << 32, 0x1000, 0x100000), data)  # MAP_FIXED_NOREPLACE

    def song(self, kind=8, id_=0, cue=0, disc=0, **fields):
        r = self.take(0x60)
        values = {FIELDS[k]: (k, v) for k, v in fields.items() if v is not None}
        for o in sorted(values):
            name, value = values[o]
            assert not (o - 4 in values and o + 4 in values), 'three neighbours: no layout'
            low = o + 4 in values or (o - 4 not in values and name in self.LOW)
            C.c_uint64.from_address(r + o).value = self.string(value, low)
        for offset, value in ((O['REC_TYPE'], kind), (O['REC_ID'], id_), (O['REC_CUE_START'], cue), (O['REC_DISC'], disc)):
            C.c_int.from_address(r + offset).value = value
        return r

    def close(self):
        for p, size in self.maps:
            libc.munmap(p, size)


@pytest.fixture
def records():
    r = Records()
    yield r
    r.close()


def field(r, name):
    p = C.c_void_p.from_address(r + FIELDS[name]).value
    return C.string_at(p) if p else None


# The queue menu's checks: what the hold menu was opened on must still be there when an action runs.
# the hold menu's kinds: an album row (all albums, an artist's, composer's or genre's) is QM_ALBUM, a row of the
# artist, composer and genre lists QM_GROUP
QM_SONG, QM_ALBUM, QM_GROUP, QM_COVERFLOW, QM_COVERALBUM, QM_PLAYING, QM_PLAYLIST = 1, 2, 3, 5, 6, 7, 8


def ctx_ptr(fw, name):
    return C.c_uint.from_address(C.addressof(fw.var('fw_ctx_' + name, C.c_char)))


class Menu:
    """The hold menu as its opener leaves it: on row idx of a list, by its count, record and browsing hashes."""
    def __init__(self, fw, st, kind, rows, idx, cls=0):
        self.fw, self.st = fw, st
        self.record = fw.fn('qm_record', C.c_void_p)
        rec_hash = fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)
        st('qm_kind').value, st('qm_idx', C.c_uint).value, st('qm_rows', C.c_uint).value = kind, idx, len(rows)
        st('qm_cls', C.c_uint).value = cls
        st('qm_hash', C.c_uint).value = rec_hash(rows[idx])
        st('qm_browse', C.c_uint).value = fw.fn('navigation__browse_hash', C.c_uint)()


def song_list(fw, records, n=3):
    deques = Deques(fw)
    rows = [records.song(name=b'%d.flac' % i, cue=i) for i in range(n)]
    handle = deques.new(rows)
    ctx_ptr(fw, 'p_deque_showlist').value = handle
    return deques, handle, rows


def test_the_menus_row_is_found_while_the_list_stands(fw, st, records):
    deques, handle, rows = song_list(fw, records)
    menu = Menu(fw, st, QM_SONG, rows, 1)
    assert menu.record() == rows[1]


@pytest.mark.parametrize('change', ['row', 'count', 'browsing', 'index'])
def test_the_menus_row_is_gone_once_the_list_changes(fw, st, records, change):
    deques, handle, rows = song_list(fw, records)
    menu = Menu(fw, st, QM_SONG, rows, 1)
    if change == 'row':
        deques[handle][1] = records.song(name=b'other.flac')
    elif change == 'count':
        deques[handle].append(records.song(name=b'3.flac'))
    elif change == 'browsing':
        fw.var('fw_ctx_g_folder_path', C.c_char * 8).value = b'/mnt/mmc'
    else:
        st('qm_idx', C.c_uint).value = 3
    assert not menu.record()


def test_the_menus_list_by_its_kind(fw, st, records):
    deques = Deques(fw)
    lists = {name: deques.new([records.song(name=name.encode())]) for name in ('p_deque_showlist', 'p_deque_playlist',
                                                                              'mcl_pdeqplaylist')}
    for name, handle in lists.items():
        ctx_ptr(fw, name).value = handle
    qm_list = fw.fn('navigation__qm_list', C.c_void_p)
    for kind, name in ((QM_SONG, 'p_deque_showlist'), (QM_ALBUM, 'p_deque_showlist'), (QM_PLAYLIST, 'p_deque_playlist'),
                       (QM_PLAYING, 'mcl_pdeqplaylist')):
        st('qm_kind').value = kind
        assert qm_list() == lists[name]


@pytest.mark.parametrize('page', ['mp', 'rt', 'other'])
def test_a_coverflow_kind_menu_takes_the_pages_own_tracks(fw, st, records, page):
    ui = Ui(fw)
    deques = Deques(fw)
    rows = [records.song(name=b'%d.flac' % i) for i in range(2)]
    top = ui.window('mostplayed_page')
    tracks = deques.new(rows)
    if page != 'other':
        st(f'{page}_page', C.c_void_p).value, st(f'{page}_list', C.c_void_p).value = top.addr, tracks
    ctx_ptr(fw, 'p_deque_showlist').value = deques.new(rows)  # Coverflow's kinds never read the browse list
    menu = Menu(fw, st, QM_COVERFLOW, rows, 1)
    assert menu.record() == (rows[1] if page != 'other' else None)
    ui.wm.children.pop()
    assert not menu.record()  # its page gone


def test_now_playings_menu_holds_while_the_track_and_queue_stand(fw, st, records, mcl):
    deques = Deques(fw)
    rows = [records.song(name=b'%d.flac' % i) for i in range(3)]
    queue = deques.new(rows)
    ctx_ptr(fw, 'mcl_pdeqplaylist').value = queue
    mcl.pos, mcl.type = 1, 0xf001
    menu = Menu(fw, st, QM_PLAYING, rows, 1)
    st('qm_queue', C.c_void_p).value = queue
    st('qm_queue_hash', C.c_uint).value = fw.fn('queue_hash', C.c_uint, C.c_void_p)(queue)
    assert menu.record() == rows[1]
    mcl.pos = 2  # the next track
    assert not menu.record()
    mcl.pos, mcl.type = 1, 0x0200  # an online queue
    assert not menu.record()
    mcl.type = 1  # a folder's
    assert menu.record() == rows[1]
    fw.mock('airplayGetFlag', lambda *_: 2)  # AirPlay plays
    assert not menu.record()
    fw.mock('airplayGetFlag', lambda *_: 0)
    deques[queue][0] = records.song(name=b'new.flac')  # the queue changed under it
    assert not menu.record()


def test_play_hold_release_matches_only_its_own_press(fw, st):
    status = C.create_string_buffer(O['INPUT_KEYS'] + O['INPUT_KEY_COUNT'] * O['INPUT_KEY_SIZE'])
    play = O['INPUT_KEYS'] + 3 * O['INPUT_KEY_SIZE']
    struct.pack_into('<i', status, play, O['KEY_PLAY'])
    struct.pack_into('<Q', status, play + O['INPUT_KEY_TIME'], 123456)
    fw.mock('window_manager_get_input_device_status', lambda *_: C.addressof(status))
    released = fw.fn('play_hold_released', C.c_int)
    press = st('qm_press', C.c_ulonglong)
    press.value = 123456
    assert released() == 1 and press.value == 0  # the held press's own release: swallowed
    press.value = 123000
    assert released() == 0 and press.value == 0  # a later press's: passes
    press.value = 123456
    struct.pack_into('<i', status, play, O['KEY_RETURN'])
    assert released() == 0 and press.value == 0  # no Play key in the device's state


def test_now_playings_more_while_it_is_open(fw, st):
    ui = Ui(fw)
    win = ui.window('playing_page')
    more = ui.add('image', ui.add('view', win), name='img_more')
    np_more = fw.fn('navigation__np_more', C.c_void_p)
    assert not np_more()
    st('np_win', C.c_void_p).value = win.addr
    assert np_more() == more.addr


def test_the_menu_closes_on_return_or_its_arrow_and_forgets_a_closed_dialog(fw, st):
    ui = Ui(fw)
    dialog = ui.window('sortselect_dialog')
    st('qm_dialog', C.c_void_p).value = dialog.addr
    back = fw.fn('navigation__qm_back', C.c_int, C.c_void_p, C.c_void_p)
    assert back(dialog.addr, event(O['EVT_KEY_UP'], O['KEY_CENTER'])) == 0 and st('qm_dialog', C.c_void_p).value
    assert back(dialog.addr, event(O['EVT_KEY_UP'], O['KEY_RETURN'])) == STOP
    # closed through menu_modal.c modal_close (no sheet here, so at once) and forgotten at once, so the next hold can
    # open another menu before stock destroys this one
    assert not st('qm_dialog', C.c_void_p).value and [a[0] for _, a in fw.calls('window_close')] == [dialog.addr]
    st('qm_dialog', C.c_void_p).value = dialog.addr
    assert back(dialog.addr, event(EVT_CLICK)) == STOP  # the title bar's arrow
    gone = fw.fn('navigation__qm_gone', C.c_int, C.c_void_p, C.c_void_p)
    other = ui.window('msginfo_dialog')
    st('qm_dialog', C.c_void_p).value = dialog.addr
    gone(other.addr, None)
    assert st('qm_dialog', C.c_void_p).value == dialog.addr
    gone(dialog.addr, None)
    assert not st('qm_dialog', C.c_void_p).value
    fw.fn('navigation__qm_close', None)()
    assert len(fw.calls('window_close')) == 2  # nothing more to close


def queried(fw):
    """Each library query run: (its name, its string arguments)."""
    names = ('getMusicByAlbum', 'getMusicBySonger', 'getMusicByAlbumArtist', 'getMusicByComposer', 'getMusicByGenre',
             'getMusicByAlbumAndSonger', 'getMusicByAlbumAndAlbumSonger', 'getMusicByAlbumAndComposer',
             'getMusicByAlbumAndGenre', 'getMusicByPlayList')
    return [(n, a[0]) if n == 'getMusicByPlayList' else
            (n, *(C.string_at(v) if v else None for v in a[:2 if 'AlbumAnd' in n else 1])) for n, a in fw.calls() if n in names]


def put(records, r, offset, value):
    """A string at r + offset that no stock field name covers (+0x1c a genre row's genre, +0x20 a composer's)."""
    C.c_uint64.from_address(r + offset).value = records.string(value, low=False)


@pytest.mark.parametrize('cls, artist_type, query', [
    (0xf004, 0, ('getMusicBySonger', b'Miles Davis')), (0xf004, 1, ('getMusicByAlbumArtist', b'Miles Davis')),
    (0xf005, 0, ('getMusicByComposer', b'Gil Evans')), (0xf006, 0, ('getMusicByGenre', b'Jazz'))])
def test_a_group_rows_menu_runs_the_query_its_row_opens(fw, st, records, cls, artist_type, query):
    fw.var('fw_ctx_g_artist_type', C.c_int).value = artist_type
    r = records.song(artist=b'Miles Davis') if cls == 0xf004 else records.song()
    if cls != 0xf004:
        put(records, r, {0xf005: 0x20, 0xf006: 0x1c}[cls], {0xf005: b'Gil Evans', 0xf006: b'Jazz'}[cls])
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_GROUP, cls
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    assert queried(fw) == [query]


def test_an_unknown_rows_menu_asks_for_the_untagged(fw, st, records):
    r = records.song(id_=-1, artist=b'ignored')
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_GROUP, 0xf004
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, O['CLASS_ALBUMS']
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(records.song(id_=-1, album=b'ignored'))
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(records.song(album=b'Blue'))
    assert queried(fw) == [('getMusicBySonger', None), ('getMusicByAlbum', None), ('getMusicByAlbum', b'Blue')]


@pytest.mark.parametrize('cls, artist_type, unknown, query', [
    (0xff01, 0, 0, ('getMusicByAlbumAndSonger', b'Blue', b'Miles Davis')),
    (0xff01, 1, 0, ('getMusicByAlbumAndAlbumSonger', b'Blue', b'Miles Davis')),
    (0xff01, 0, 1, ('getMusicByAlbumAndSonger', b'Blue', None)),  # an Unknown artist's album
    (0xff03, 0, 0, ('getMusicByAlbumAndGenre', b'Blue', b'Jazz'))])
def test_an_albums_menu_under_a_group_keeps_the_group(fw, st, records, cls, artist_type, unknown, query):
    fw.var('fw_ctx_g_artist_type', C.c_int).value = artist_type
    info = fw.var('fw_ctx_g_local_classinfo_save', C.c_char * 912)
    info[0xa + (cls & 0xf) - 1] = bytes([unknown])
    if cls == 0xff03:
        r = records.song(album=b'Blue')
        put(records, r, 0x1c, b'Jazz')
    else:
        r = records.song(album=b'Blue', artist=b'Miles Davis')
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, cls
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    assert queried(fw) == [query]
    assert fw.calls(query[0])[0][1][2] == 0  # not the Unknown Album row (id -2)


@pytest.mark.parametrize('id_, unknown_album', [(-2, 1), (0, 0), (-1, 0)])
def test_an_artists_unknown_album_rows_menu_asks_for_its_untagged_albums_songs(fw, st, records, id_, unknown_album):
    r = records.song(id_=id_, artist=b'Miles Davis')
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, O['CLASS_ARTIST_ALBUMS']
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    # the artist's songs with no album tag: the query's own flag, as the row opens them (id -2, REC_ID's unknown album)
    ((name, args),) = [(n, a) for n, a in fw.calls() if n.startswith('getMusicByAlbumAnd')]
    assert (name, C.string_at(args[1]), args[2]) == ('getMusicByAlbumAndSonger', b'Miles Davis', unknown_album)


def test_a_playlists_menu_runs_its_playlist(fw, st, records):
    r = records.song(disc=7)
    st('qm_kind').value = QM_PLAYLIST
    fw.fn('navigation__qm_query', C.c_int, C.c_void_p)(r)
    assert queried(fw) == [('getMusicByPlayList', 7)]


def test_the_menus_album_id(fw, st, records):
    r = records.song(album=b'Blue', album_artist=b'Miles Davis', path=b'/m/Blue/1.flac')
    album_id = fw.fn('album_id', C.c_uint, C.c_void_p)(r)
    qm_album_id = fw.fn('qm_album_id', C.c_uint, C.c_void_p)
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_ALBUM, O['CLASS_ALBUMS']
    assert qm_album_id(r) == album_id  # a Library Albums row: the album Coverflow rates
    st('qm_kind').value = QM_COVERALBUM
    assert qm_album_id(r) == album_id
    st('qm_kind').value, st('qm_cls', C.c_uint).value = QM_GROUP, 0xf004
    assert qm_album_id(r) == 0  # an artist's row rates no album
    # an artist's albums tab: the id ringnav_artist_list found for the row, while the tab is the one it read
    ids = (C.c_uint * 3)(11, 22, 33)
    st('qm_kind').value = QM_ALBUM  # its rows are albums
    st('qm_cls', C.c_uint).value, st('art_ids', C.c_void_p).value, st('art_n', C.c_uint).value = O['CLASS_ARTIST_ALBUMS'], C.addressof(ids), 3
    st('qm_rows', C.c_uint).value, st('qm_idx', C.c_uint).value = 3, 1
    assert qm_album_id(r) == 22
    st('qm_rows', C.c_uint).value = 4
    assert qm_album_id(r) == 0
    st('art_ids', C.c_void_p).value, st('art_n', C.c_uint).value = None, 0


# The Library's lists: Albums' split art keys, an album's own songs, the class lists and an artist's page.
CLASS_ALBUMS = O['CLASS_ALBUMS']


class Library:
    """Stock's list globals as Deques: the showlist, the staging deque its queries fill, and the cover tasks."""
    def __init__(self, fw):
        self.fw = fw
        self.d = Deques(fw)
        self.show, self.dir, self.covers = self.d.new(), self.d.new(), self.d.new()
        for name, handle in (('p_deque_showlist', self.show), ('tools_pdeq_directory', self.dir),
                             ('pdeq_albumcoverlist', self.covers)):
            ctx_ptr(fw, name).value = handle
        fw.mock('fw_deque_pop_back', lambda q, *_: self.d[q].pop() and 0)

    def staging(self):
        """The deque a query fills now: staged_into points tools_pdeq_directory at its own."""
        return self.d[ctx_ptr(self.fw, 'tools_pdeq_directory').value]

    def query(self, name, rows):
        """Stock's query name: rows into the staging deque, their count back."""
        self.fw.mock(name, lambda *_: self.staging().extend(rows) or len(rows))

    def database(self, rows):
        """The library's database as era_sql opens it: a select answers rows, (fileurl, time, name) each, through the
        row callback; the queries asked are kept."""
        self.asked = []
        row_cb = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)
        def exec_(db, sql, cb, ctx, *_):
            q = C.string_at(sql).decode()
            if not q.startswith('select') or not cb:
                return 0
            self.asked.append(q)
            for row in rows:
                values = (C.c_char_p * 3)(*[None if v is None else str(v).encode() for v in row])
                row_cb(cb)(ctx, 3, C.addressof(values), None)
            return 0
        self.fw.mock('sqlite3_open', lambda path, db, *_: setattr(C.c_void_p.from_address(db), 'value', 0x5d) or 0)
        self.fw.mock('sqlite3_exec', exec_)

    def tasks(self, *names):
        """Cover tasks as albumcoverinfo_init leaves them: both art flags set, the name a heap copy at +4."""
        strdup = self.fw.fn('strdup', C.c_void_p, C.c_char_p)
        self.task_blocks = [C.create_string_buffer(0x20) for _ in names]
        for block, name in zip(self.task_blocks, names):
            block[0] = block[1] = b'\1'
            C.c_void_p.from_buffer(block, 4).value = strdup(name)
        self.d[self.covers][:] = [C.addressof(b) for b in self.task_blocks]

    def task(self, i):
        block = self.task_blocks[i]
        return C.string_at(C.c_void_p.from_buffer(block, 4).value or 0), (block[0], block[1])


def albums(records):
    """Albums' rows as split leaves them, by name: a name of one album, then three albums of one name."""
    return [records.song(album=b'Blue', album_artist=b'Joni Mitchell', path=b'/m/Joni/Blue/01.flac', kind=4),
            records.song(album=b'Greatest Hits', album_artist=b'ABBA', path=b'/m/ABBA/GH/01.flac', kind=4),
            records.song(album=b'Greatest Hits', album_artist=b'Queen', path=b'/m/Queen/GH/01.flac', kind=4),
            records.song(album=b'Greatest Hits', path=b'/m/Various/GH/CD1/01.flac', kind=4)]


SPLIT_KEYS = [b'Blue', b'Greatest Hits\x1fABBA', b'Greatest Hits\x1fQueen', b'Greatest Hits\x1f/m/Various/GH']


def test_albums_of_one_name_key_their_art_by_album_artist_else_folder(fw, records):
    lib = Library(fw)
    lib.d[lib.show][:] = albums(records)
    art_name = fw.fn('ringnav_art_name', C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_char_p)
    out = C.create_string_buffer(256)
    found = []
    for row in range(4):
        assert art_name(out, 256, row, b'Greatest Hits') == len(out.value)
        found.append(out.value)
    # a lone name keeps stock's key (and cached art); a shared one adds its album artist, else its folder (disc folders as one)
    assert found == SPLIT_KEYS
    art_name(out, 256, 4, b'Elsewhere')  # past the list: stock's own name
    assert out.value == b'Elsewhere'


def test_albums_cover_tasks_take_the_split_keys_and_find_their_art_again(fw, records):
    lib = Library(fw)
    lib.d[lib.show][:] = albums(records)
    lib.tasks(b'Blue', b'Greatest Hits', b'Greatest Hits', b'Greatest Hits')
    covers = fw.fn('ringnav_albumcovers', C.c_int, C.c_void_p)
    fw.mock('albumcoverinfo_init', lambda *_: 9)
    assert covers(lib.show) == 9  # stock's answer
    assert [lib.task(i) for i in range(4)] == [(b'Blue', (b'\1', b'\1'))] + [(k, (b'\0', b'\0')) for k in SPLIT_KEYS[1:]]
    for i in range(1, 4):
        lib.task_blocks[i][0] = b'\1'
    covers(lib.show)  # the same keys again: the art found stays
    assert [lib.task(i)[1][0] for i in range(4)] == [b'\1'] * 4
    assert len(fw.calls('fw_pthread_mutex_lock')) == len(fw.calls('fw_pthread_mutex_unlock')) == 2  # under aclist_mutex


@pytest.mark.parametrize('row, big, key, marked', [(2, 0, b'Greatest Hits\x1fQueen', (1, 0)), (2, 1, b'Greatest Hits\x1fQueen', (0, 1)),
                                                   (2, 0, b'Greatest Hits\x1fABBA', (0, 0)), (2, 2, b'Greatest Hits\x1fQueen', (0, 0)),
                                                   (4, 0, b'Greatest Hits\x1fQueen', (0, 0)), (-1, 0, b'x', (0, 0))])
def test_a_finished_cover_marks_only_the_row_still_holding_its_album(fw, row, big, key, marked):
    lib = Library(fw)
    lib.tasks(*SPLIT_KEYS)
    for b in lib.task_blocks:
        b[0] = b[1] = b'\0'
    fw.fn('ringnav_cover_done', None, C.c_int, C.c_int, C.c_char_p)(row, big, key)
    assert lib.task(2)[1] == tuple(bytes([m]) for m in marked)
    assert all(lib.task(i)[1] == (b'\0', b'\0') for i in (0, 1, 3))


@pytest.mark.parametrize('opened, album, shown', [(2, b'Greatest Hits', b'Greatest Hits\x1fQueen'), (2, b'greatest hits', b'Greatest Hits\x1fQueen'),
                                                  (2, b'Greatest', b'Greatest'), (0, b'Blue', b'Blue'), (7, b'Blue', b'Blue')])
def test_the_album_pages_big_cover_is_its_rows_split_key(fw, stand_in, opened, album, shown):
    lib = Library(fw)
    lib.tasks(*SPLIT_KEYS)
    stand_in(O['ALBUMINFO_ROW'], struct.pack('<i', opened))
    out = C.create_string_buffer(256)
    fw.fn('ringnav_art_header', C.c_int, C.c_char_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 256, b'%s', album)
    assert out.value == shown


def classinfo(fw, name=b'', artist=b'', unknown=0):
    info = fw.var('fw_ctx_g_local_classinfo_save', C.c_char * 912)
    info[9] = bytes([unknown])
    C.memmove(C.addressof(info) + 0xd, name + b'\0', len(name) + 1)
    C.memmove(C.addressof(info) + 0x10d, artist + b'\0', len(artist) + 1)


class Localclass:
    """load_localclass_list: stock fills the showlist (stock_localclass_trampoline) and returns its size."""
    def __init__(self, fw, lib, rows=None):
        self.fw, self.lib, self.rows = fw, lib, rows or []
        fw.mock('stock_localclass_trampoline', lambda cls, *_: self.stock())
        self.load = fw.fn('ringnav_localclass', C.c_int, C.c_int)
        self.timers = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))

    def stock(self):
        self.lib.d[self.lib.show][:] = self.rows
        return len(self.rows)

    def __call__(self, cls):
        return self.load(cls)

    def shown(self, name='title'):
        return [field(r, name) for r in self.lib.d[self.lib.show]]


def gh_songs(records):
    """Every song named Greatest Hits, as 0xff10 loads them: Queen's and ABBA's."""
    return [records.song(title=b'%s %d' % (aa, i), album=b'Greatest Hits', album_artist=aa)
            for aa in (b'Queen', b'ABBA') for i in (1, 2)]


@pytest.mark.parametrize('opened, info, want', [
    ('row', (b'Greatest Hits', b'Queen', 0), [b'Queen 1', b'Queen 2']),  # its own album's songs
    ('row', (b'GREATEST HITS', b'Queen', 0), [b'Queen 1', b'Queen 2']),  # the name, case aside
    ('row', (b'Greatest Hits', b'ABBA', 0), None),  # classinfo another album's: every song of the name
    ('row', (b'Greatest Hits', b'Queen', 1), None),
    ('other class', (b'Greatest Hits', b'Queen', 0), None),  # Go to album from elsewhere: stock's list
    ('unknown', (b'Greatest Hits', b'Queen', 0), None)])
def test_an_albums_row_opens_only_its_own_albums_songs(fw, records, opened, info, want):
    lib = Library(fw)
    songs = gh_songs(records)
    row = records.song(album=b'Greatest Hits', artist=b'Queen', id_=-1 if opened == 'unknown' else 3, kind=4)
    C.c_uint64.from_address(row + FIELDS['album_artist']).value = records.string(b'Queen', low=False)
    fw.mock('stock_detail_trampoline', lambda *_: 5)
    detail = fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)
    assert detail(CLASS_ALBUMS if opened != 'other class' else 0xf004, row) == 5
    classinfo(fw, *info)
    load = Localclass(fw, lib, songs)
    n = load(0xff10)
    assert load.shown() == (want or [field(s, 'title') for s in songs]) and n == len(load.shown())


def test_an_album_opened_from_elsewhere_forgets_the_last_albums_row(fw, records):
    lib = Library(fw)
    songs = gh_songs(records)
    row = records.song(album=b'Greatest Hits', artist=b'Queen', id_=3, kind=4)
    C.c_uint64.from_address(row + FIELDS['album_artist']).value = records.string(b'Queen', low=False)
    fw.mock('stock_detail_trampoline', lambda *_: 0)
    detail = fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)
    classinfo(fw, b'Greatest Hits', b'Queen')
    load = Localclass(fw, lib, songs)
    detail(CLASS_ALBUMS, row)
    load(0xff10)
    assert load.shown() == [b'Queen 1', b'Queen 2']  # the Albums row's own album
    destroyed = len(fw.calls('fw_deque_destroy'))
    detail(0xf004, row)  # the same name and artist, opened from another list
    assert len(fw.calls('fw_deque_destroy')) == destroyed + 1  # the last album's copy goes
    load(0xff10)
    assert load.shown() == [field(s, 'title') for s in songs]  # every song of the name, as stock lists it


def album_db(fw, lib, records, albums):
    """The library database's albums, as coverflow_split's ALBUM_SQL reads them (id, album, songer, a track's path,
    album artist): stock's album_row makes each a record in the staging deque, split_row adds its album artist."""
    def album_row(ctx, n, v, names, *_):
        col = C.cast(v, C.POINTER(C.c_char_p))
        lib.staging().append(records.song(id_=int(col[0]), album=col[1], artist=col[2], path=col[3], kind=4))
        return 0
    def query(db, sql, row, ctx, *_):
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(row)
        names = (C.c_char_p * 5)(b'id', b'album', b'songer', b'fileurl', b'albumsonger')
        for a in albums:
            values = (C.c_char_p * 5)(*a)
            call(ctx, 5, C.addressof(values), C.addressof(names))
        return len(albums)
    fw.mock('album_row', album_row)
    fw.mock('toolsQueryDbTable', query)


def test_albums_from_stocks_names_to_one_albums_songs_and_cover(fw, stand_in, records):
    lib = Library(fw)
    album_db(fw, lib, records, [(b'1', b'Blue', b'Joni Mitchell', b'/m/Joni/Blue/01.flac', b'Joni Mitchell'),
                                (b'2', b'Greatest Hits', b'ABBA', b'/m/ABBA/GH/01.flac', b'ABBA'),
                                (b'3', b'Greatest Hits', b'Queen', b'/m/Queen/GH/01.flac', b'Queen'),
                                (b'4', b'Greatest Hits', b'Various', b'/m/Various/GH/CD1/01.flac', None)])
    # stock's cover tasks, one per row by its name, as albumcoverinfo_init makes them: the art found by name
    fw.mock('albumcoverinfo_init', lambda q, *_: lib.tasks(*(field(r, 'album') for r in lib.d[q])) or 9)
    # Albums: getAllAlbum's rows by name alone, each split into that name's albums in the database's order
    load = Localclass(fw, lib, [records.song(id_=1, album=b'Blue', kind=4), records.song(id_=2, album=b'Greatest Hits', kind=4)])
    assert load(CLASS_ALBUMS) == 4
    shown = list(lib.d[lib.show])
    assert [(field(r, 'album'), field(r, 'album_artist')) for r in shown] == [
        (b'Blue', b'Joni Mitchell'), (b'Greatest Hits', b'ABBA'), (b'Greatest Hits', b'Queen'), (b'Greatest Hits', None)]
    # the renderer's art name for each row: a lone name stock's own, a shared one with its album artist, else folder
    art_name = fw.fn('ringnav_art_name', C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_char_p)
    out = C.create_string_buffer(256)
    keys = []
    for i, r in enumerate(shown):
        art_name(out, 256, i, field(r, 'album'))
        keys.append(out.value)
    assert keys == SPLIT_KEYS
    # the list's cover tasks take those keys: Blue's art, found by its name, stays found; the others look again
    assert fw.fn('ringnav_albumcovers', C.c_int, C.c_void_p)(lib.show) == 9
    assert [lib.task(i) for i in range(4)] == [(b'Blue', (b'\1', b'\1'))] + [(k, (b'\0', b'\0')) for k in SPLIT_KEYS[1:]]
    # Queen's cover comes in for its row; the same key finishing for ABBA's row marks nothing there
    done = fw.fn('ringnav_cover_done', None, C.c_int, C.c_int, C.c_char_p)
    done(2, 0, keys[2])
    done(1, 0, keys[2])
    assert [lib.task(i)[1] for i in (1, 2)] == [(b'\0', b'\0'), (b'\1', b'\0')]
    # Queen's row opens its own songs, under the big cover its row's key names
    fw.mock('stock_detail_trampoline', lambda *_: 0)
    fw.fn('ringnav_album_detail', C.c_int, C.c_int, C.c_void_p)(CLASS_ALBUMS, shown[2])
    classinfo(fw, b'Greatest Hits', b'Queen')
    load.rows = gh_songs(records)
    load(0xff10)
    assert load.shown() == [b'Queen 1', b'Queen 2']
    stand_in(O['ALBUMINFO_ROW'], struct.pack('<i', 2))
    fw.fn('ringnav_art_header', C.c_int, C.c_char_p, C.c_uint, C.c_char_p, C.c_char_p)(out, 256, b'%s', b'Greatest Hits')
    assert out.value == keys[2]


def test_an_unknown_row_whose_query_finds_nothing_goes(fw, records):
    lib = Library(fw)
    rows = [records.song(artist=b'ABBA', kind=4), records.song(artist=b'Queen', kind=4), records.song(id_=-1, kind=4)]
    load = Localclass(fw, lib, rows)
    lib.query('getMusicBySonger', [])
    assert load(0xf004) == 2 and lib.d[lib.show] == rows[:2]
    assert fw.calls('getMusicBySonger')[0][1][0] == 0  # the Unknown row's query: untagged songs
    assert lib.d[lib.dir] == []


def test_an_unknown_row_with_songs_stays(fw, records):
    lib = Library(fw)
    rows = [records.song(artist=b'ABBA', kind=4), records.song(id_=-1, kind=4)]
    load = Localclass(fw, lib, rows)
    lib.query('getMusicBySonger', [records.song(name=b'untagged.flac')])
    assert load(0xf004) == 2 and lib.d[lib.show] == rows and lib.d[lib.dir] == []
    assert load.timers == []


@pytest.mark.parametrize('artist_type', [0, 1])
def test_artists_follow_the_artist_pages_own_setting(fw, records, artist_type):
    lib = Library(fw)
    fw.var('fw_ctx_artist_type', C.c_int).value = artist_type
    load = Localclass(fw, lib, [records.song(id_=-1, kind=4)])
    load(0xf004)
    assert fw.var('fw_ctx_g_artist_type', C.c_int).value == artist_type
    assert len(fw.calls('getMusicByAlbumArtist' if artist_type else 'getMusicBySonger')) == 1
    assert fw.var('navigation__library_music', C.c_int).value == 0  # put back


PAGE_TITLE_NAME = '_page_title'  # peq.h PAGE_TITLE: the caption the status row reads
EMPTY_CLASSES = {CLASS_ALBUMS: ('Albums', b'vinyl-record', b'No Albums', b'Copy music to the SD card'),
                 0xf004: ('Artists', b'microphone-stage', b'No Artists', b'Copy music to the SD card'),
                 0xf005: ('Composers', b'music-notes-simple', b'No Composers', b'Songs need a composer tag'),
                 0xf006: ('Genres', b'guitar', b'No Genres', b'Songs need a genre tag')}


def empty_pages(fw, st, ui):
    """page_open's windows, each over the others; the empty state's view made by the widget factory."""
    use_set(st)
    fw.mock('window_create', lambda *_: ui.add('window', ui.wm, (0, 0, 375, 320)).addr)
    fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: ui.new(C.string_at(kind).decode(), parent, x, y, w, 0))
    return lambda: [w for w in ui.wm.children if w.name == 'empty_page']


def shown_empty(page):
    """page's empty state view: (glyph, title, hint)."""
    art = page.find('_empty_state')
    return art.visible and (art.props['_es_glyph'], art.props['_es_title'], art.props['_es_hint'])


@pytest.mark.parametrize('cls', EMPTY_CLASSES)
def test_an_empty_class_list_closes_its_page_once_open_for_the_empty_state(fw, st, records, cls):
    ui = Ui(fw)
    lib = Library(fw)
    prompts = Prompts(fw)
    pages = empty_pages(fw, st, ui)
    load = Localclass(fw, lib, [records.song(id_=-1, kind=4)])
    assert load(cls) == 0 and [ms for _, ms in load.timers] == [0]
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = cls
    page = ui.window('album_page' if cls == CLASS_ALBUMS else 'localclass_page')
    page.props['stage'] = O['WINDOW_STAGE_OPENED'] - 1
    run(load.timers[-1][0])
    assert [ms for _, ms in load.timers] == [0, O['EMPTY_POLL_MS']] and not pages()  # still opening: polled
    page.props['stage'] = O['WINDOW_STAGE_OPENED']
    run(load.timers[-1][0])
    assert [a[1] for _, a in fw.calls('window_manager_close_window_force')] == [page.addr]
    (empty,) = pages()
    name, *words = EMPTY_CLASSES[cls]
    assert shown_empty(empty) == tuple(words) and prompts.seen == []
    assert empty.find(PAGE_TITLE_NAME).text == name and not empty.find(PAGE_TITLE_NAME).visible  # the status row's


def test_an_empty_list_with_no_card_in_says_so(fw, st, records):
    ui = Ui(fw)
    lib = Library(fw)
    pages = empty_pages(fw, st, ui)
    fw.mock('fw_access', lambda path, *_: -1 if C.string_at(path) == b'/tmp/mmc_add' else 0)
    load = Localclass(fw, lib, [])
    load(0xf004)
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = 0xf004
    ui.window('localmusic_page')
    run(load.timers[-1][0])
    (empty,) = pages()
    assert shown_empty(empty) == (b'sd-card', b'No SD Card', b'Insert an SD card with music')


def test_an_empty_list_whose_page_never_showed_only_opens_the_empty_state(fw, st, records):
    ui = Ui(fw)
    lib = Library(fw)
    pages = empty_pages(fw, st, ui)
    load = Localclass(fw, lib, [])
    load(0xf005)
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = 0xf005
    ui.window('localmusic_page')
    run(load.timers[-1][0])
    assert not fw.calls('window_manager_close_window_force') and shown_empty(pages()[0])[1] == b'No Composers'


@pytest.mark.parametrize('page, shows', [('album_page', CLASS_ALBUMS), ('localclass_page', 0xf004)])
def test_an_empty_list_leaves_a_list_page_showing_another_class(fw, st, records, page, shows):
    ui = Ui(fw)
    lib = Library(fw)
    pages = empty_pages(fw, st, ui)
    load = Localclass(fw, lib, [])
    load(0xf005)  # Composers, empty
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = shows
    ui.window(page).props['stage'] = O['WINDOW_STAGE_OPENED']
    run(load.timers[-1][0])
    # the page on top lists another class: it stays, unpolled, and the empty state says what was empty
    assert not fw.calls('window_manager_close_window_force') and len(load.timers) == 1
    assert shown_empty(pages()[0])[1] == b'No Composers'


def test_songs_load_straight_into_the_showlist(fw, records):
    lib = Library(fw)
    songs = [records.song(title=b'%d' % i, path=b'/m/%d.flac' % i) for i in range(3)]
    lib.d[lib.show][:] = [records.song(title=b'stale')]
    lib.d[lib.dir][:] = [records.song(title=b'staged')]
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_sort_flag', C.c_char))).value = 2
    seen = []
    fw.mock('getAllMusic', lambda order, *_: seen.append((order, ctx_ptr(fw, 'tools_pdeq_directory').value)) or
            lib.staging().extend(songs) or 3)
    load = Localclass(fw, lib)
    assert load(0xf001) == 3 and lib.d[lib.show] == songs
    assert seen == [(2, lib.show)] and ctx_ptr(fw, 'tools_pdeq_directory').value == lib.dir and lib.d[lib.dir] == []
    assert not fw.calls('stock_localclass_trampoline') and fw.calls('music_nagvation_title')[0][1][0] == 0xf001


def test_a_playlist_hides_its_spoken_songs(fw, records):
    lib = Library(fw)
    fw.mock('tk_str_start_with', stock_start_with)
    rows = [records.song(title=b'song', path=b'/mnt/mmc/Music/a.flac'), records.song(title=b'book', path=b'/mnt/mmc/x.m4b'),
            records.song(title=b'episode', path=b'/mnt/mmc/Podcasts/e.mp3'), records.song(title=b'chapter', path=b'/mnt/mmc/Audiobooks/c.mp3'),
            records.song(title=b'folder', path=b'/mnt/mmc/Podcasts', kind=4)]
    load = Localclass(fw, lib, rows)
    assert load(0xff20) == 2 and load.shown() == [b'song', b'folder']


@pytest.mark.parametrize('cls', [0xf00a, 0xf008, 0xf007])
def test_stored_lists_hide_discovery_and_take_the_librarys_titles(fw, records, cls):
    lib = Library(fw)
    Files(fw, {'/mnt/data/q2-library-scope': b'0/mnt/mmc/Discovery\n'})
    played = [records.song(title=b'Kept', path=b'/mnt/mmc/Music/a.flac'),
              records.song(path=b'/mnt/mmc/Music/b.cue', cue=60),  # played from Folder: no title
              records.song(title=b'Hidden', path=b'/mnt/mmc/Discovery/c.flac'),
              records.song(path=b'/mnt/mmc/Music/d.flac')]
    lib.database([('/mnt/mmc/Music/b.cue', 0, 'Track 1'), ('/mnt/mmc/Music/b.cue', 60, 'Track 2'),
                  ('/mnt/mmc/Music/d.flac', 0, 'Dee')])
    load = Localclass(fw, lib, played)
    assert load(cls) == 3 and load.shown() == [b'Kept', b'Track 2', b'Dee']
    assert not fw.calls('getAllMusic')  # the untitled rows' paths asked of the database, not the library loaded
    assert lib.asked == ["select fileurl,time,name from songtable where fileurl in "
                         "('/mnt/mmc/Music/b.cue','/mnt/mmc/Music/d.flac')"]


def test_a_cue_track_takes_its_own_title_not_its_siblings(fw, records):
    lib = Library(fw)
    path = b'/mnt/mmc/Music/b.cue'
    lib.database([(path.decode(), 0, 'Track 1'), (path.decode(), 60, 'Track 2')])
    load = Localclass(fw, lib, [records.song(path=path, cue=60)])
    assert load(0xf007) == 1 and load.shown() == [b'Track 2']


@pytest.mark.parametrize('row', ['folder', 'pathless song'])
def test_a_stored_list_with_nothing_to_look_up_never_reads_the_library(fw, records, row):
    lib = Library(fw)
    # every song titled, but for a folder row, or a song with no path to find it by
    other = records.song(name=b'b.flac', kind=4 if row == 'folder' else 8)
    load = Localclass(fw, lib, [records.song(title=b'A', path=b'/mnt/mmc/a.flac'), other])
    lib.database([])
    assert load(0xf00a) == 2 and not fw.calls('getAllMusic') and not lib.asked


def test_an_untagged_song_with_no_path_reads_its_name_as_stored(fw, records):
    # track_name drops the extension the path's file name ends in; a record with no path keeps the name whole. The
    # stripping itself is test/patch.py's (iPod song rows): name, path and title sit 4 bytes apart on the Q2, which
    # no string placement here keeps readable together (Records)
    lib = Library(fw)
    load = Localclass(fw, lib, [records.song(name=b'01 Intro.flac'), records.song(title=b'Tagged'), records.song(name=b'dir', kind=4)])
    load(0xff11)
    assert load.shown() == [b'01 Intro.flac', b'Tagged', None]  # a tag stays; a folder row is left as it is


def test_a_class_rows_icon_hides_on_oleds_one_line_rows(fw, st, geometry):
    ui = Ui(fw)
    row = ui.add('list_item', ui.window('localclass_page'))
    icon = ui.add('image', row, name='img_icon')
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = 0xf005  # Composers'
    assert fw.fn('ringnav_class_icon', C.c_int, C.c_void_p, C.c_int)(row.addr, 2) == 0
    assert not icon.visible


# An artist's page.
class ArtistPage:
    """artistinfo_page: its navbar (hidden by iPod), tab_control with its pages and the albums grid."""
    def __init__(self, fw, ui, nav_shown=0):
        self.win = ui.window('artistinfo_page')
        self.win.props['anim_hint'] = b'htranslate'
        self.nav = ui.add('view', self.win, (0, 0, 375, 40), 'view_navbar')
        self.nav.visible = nav_shown
        self.tab = ui.add('tab_control', self.win, (0, 0, 375, 290), 'tab_control')
        self.pages = ui.add('pages', self.tab, (0, 50, 375, 240), 'pages_artistinfo')
        self.grid = ui.add('list_view', self.pages, (0, 0, 375, 240), 'list_view_album')


ARTIST_TITLE_SIZE = 0x200  # stock's title buffer, all of which artist_list's getFormatString may write


def artist(fw, stand_in, records, rows, songs, view=b'1', title=b'Queen'):
    lib = Library(fw)
    Config(fw, ARTISTCF=view)
    stand_in(O['ARTIST_TITLE'], title.ljust(ARTIST_TITLE_SIZE, b'\0'))  # the whole buffer, put back whole
    classinfo(fw, artist=b'Queen')
    fw.mock('stock_artist_trampoline', lambda tab, *_: lib.d[lib.show].extend(rows) or len(rows))
    lib.query('getMusicBySonger', songs)
    return lib


def queen(records):
    """The artist's albums tab as stock lists it, by name, and the artist's songs."""
    rows = [records.song(id_=-1, path=b'/m/x.flac', kind=4),
            records.song(album=b'Greatest Hits', artist=b'Queen', path=b'/m/Queen/GH/01.flac', kind=4),
            records.song(album=b'Covers', artist=b'Queen', path=b'/m/Tribute/Covers/01.flac', kind=4),
            records.song(album=b'A Night at the Opera', artist=b'Queen', path=b'/m/Queen/ANATO/01.flac', kind=4)]
    songs = [records.song(album=b'A Night at the Opera', path=b'/m/Queen/ANATO/0%d.flac' % i) for i in (1, 2)] + \
            [records.song(album=b'Greatest Hits', path=b'/m/Queen/GH/01.flac'), records.song(path=b'/m/x.flac')]
    return rows, songs


def test_an_artists_albums_tab_orders_its_albums_and_notes_their_ids(fw, st, stand_in, records):
    ui = Ui(fw)
    ArtistPage(fw, ui)
    ui.window('sortselect_dialog')  # over the page as its tab loads: stock's grid, not the design's page
    rows, songs = queen(records)
    lib = artist(fw, stand_in, records, rows, songs, view=b'0')
    assert fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1) == 4
    # the artist's own albums in the albums' order (no year without the database: as listed), the Unknown album
    # after them, then an album the artist has no song on
    assert lib.d[lib.show] == [rows[1], rows[3], rows[0], rows[2]]
    album_id = fw.fn('album_id', C.c_uint, C.c_void_p)
    ids = C.cast(st('art_ids', C.c_void_p).value, C.POINTER(C.c_uint))
    # each row's album_id for the hold menu's album rating; the Unknown row names no album
    assert st('art_n', C.c_uint).value == 4 and [ids[i] for i in range(4)] == [album_id(rows[1]), album_id(rows[3]), 0, album_id(rows[2])]
    assert fw.var('navigation__library_music', C.c_int).value == 0
    out = C.create_string_buffer(64)
    fw.fn('ringnav_artist_art_name', C.c_int, C.c_char_p, C.c_uint, C.c_uint, C.c_char_p)(out, 64, 1, b'Stock')
    assert out.value == b'Stock'  # no name shared: stock's own key


def test_an_artists_songs_tab_keeps_no_keys(fw, st, stand_in, records):
    ui = Ui(fw)
    ArtistPage(fw, ui)
    ui.window('sortselect_dialog')  # over the page as its tab loads: stock's grid, not the design's page
    rows, songs = queen(records)
    artist(fw, stand_in, records, rows, songs)
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    assert st('art_n', C.c_uint).value == 4
    assert fw.fn('ringnav_artist_list', C.c_int, C.c_int)(0) == 4
    assert (st('art_n', C.c_uint).value, st('art_keys', C.c_void_p).value, st('art_ids', C.c_void_p).value) == (0, None, None)
    lib_covers = []
    fw.mock('albumcoverinfo_init', lambda list_, *_: lib_covers.append(list_) or 0)
    fw.fn('ringnav_artist_covers', C.c_int, C.c_void_p)(0x77)
    assert lib_covers == [0x77]


def test_an_artist_with_no_name_reads_unknown_artist(fw, stand_in, records):
    ui = Ui(fw)
    ArtistPage(fw, ui)
    ui.window('x_page')  # over the page as its tabs load: stock's tabs, not the design's page (artist_local)
    fw.mock('getFormatString', lambda key, out, n, *_: C.memmove(out, b'Unknown Artist\0', 15) and 0)
    artist(fw, stand_in, records, [], [], title=b'(null)')
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    assert C.string_at(O['ARTIST_TITLE']) == b'Unknown Artist'
    assert [(C.string_at(a[0]), a[2]) for _, a in fw.calls('getFormatString')][:1] == [(b'player_unknown', ARTIST_TITLE_SIZE)]


def oled_artist(fw, st, stand_in, records, view=b'1'):
    """An artist page under OLED's set: its page, the Ui and the artist's songs and albums tab as queen's."""
    ui = Ui(fw)
    page = ArtistPage(fw, ui)
    fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: ui.new(C.string_at(kind).decode(), parent, x, y, w, 0))
    for kind in ('scroll_view', 'list_view', 'list_item', 'hscroll_label', 'image', 'button'):  # page_list's and the pair's
        fw.mock(f'{kind}_create', lambda parent, x, y, w, h, *_, k=kind: ui.new(k, parent, x, y, w, h))
    use_set(st)
    rows, songs = queen(records)
    artist(fw, stand_in, records, rows, songs, view=view)
    return ui, page


def texts(w):
    """Every widget under w: (its text or name, its props)."""
    for c in w.children:
        yield c
        yield from texts(c)


def test_under_oled_an_artists_page_is_drawn_as_the_design(fw, st, stand_in, records):
    """The tabs go and the page holds the header (its counts from the artist's songs), Shuffle alone and the
    Albums and All Songs rows with their counts; no Top Songs without a counted play, no Radio, and no Cover Flow
    over it whatever Artist View says."""
    ui, page = oled_artist(fw, st, stand_in, records)
    assert fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1) == 4
    assert not page.tab.visible and not page.nav.visible
    assert fw.fn('artist_local_on', C.c_int, C.c_void_p)(page.win.addr) == 1
    made = list(texts(page.win))
    head = [w for w in made if '_ar_name' in w.props]
    assert [(w.props['_ar_name'], w.props['_ar_sub']) for w in head] == [(b'Queen', b'3 albums \xc2\xb7 4 songs')]
    assert len([w for w in made if '_ar_left' in w.props]) == 1  # Shuffle alone
    assert [(w.children[0].text, w.props.get('_ar_count')) for w in made if w.kind == 'list_item'] == \
        [('Albums', b'3'), ('All Songs', b'4')]
    assert not fw.calls('widget_set_opacity')
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(0)  # the songs tab's load: the page stays as drawn
    assert list(texts(page.win)) == made


def test_a_lone_shuffle_is_one_stop_above_the_rows(fw, st, stand_in, records):
    ui, page = oled_artist(fw, st, stand_in, records)
    fw.fn('ringnav_artist_list', C.c_int, C.c_int)(1)
    rows = next(w for w in texts(page.win) if w.kind == 'scroll_view')
    key = fw.fn('pair_key', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    holds = fw.fn('pair_holds', C.c_int, C.c_void_p)
    assert holds(rows.addr)  # the page opens on Shuffle
    assert key(page.win.addr, rows.addr, -1) == 1 and holds(rows.addr)  # nothing above it
    rows.props['_ringnav_index'] = 1  # a row the stock page's position memory put back
    assert key(page.win.addr, rows.addr, 1) == 1 and not holds(rows.addr)
    assert rows.props['_ringnav_index'] == 0  # down off Shuffle: the first row
    assert key(page.win.addr, rows.addr, -1) == 1 and holds(rows.addr)  # and up from it: Shuffle again


def test_appearance_keeps_its_own_place_not_displays(fw):
    """Appearance is Display's page opened again (display_open): position memory (context_now) keys it apart from
    Display, so it opens on its own first row, not on the row Display was left at (audit2 J9)."""
    ui = Ui(fw)
    page = appearance(fw, ui)
    display_page, sub = page.pages
    now = fw.fn('navigation__context_now', C.c_int, C.POINTER(C.c_uint))
    scopes = []
    for win in (display_page, sub):  # each the top window in turn: the window manager's last
        ui.wm.children.remove(win)
        ui.wm.children.append(win)
        scope = C.c_uint()
        now(C.byref(scope))
        scopes.append(scope.value)
    assert sub.props.get('_sub_page') == 0x10 + 1 and scopes[0] != scopes[1]


# The empty state (feedback.c empty_state): one view over the page holding the page's glyph, title and hint for its
# painter, and at most one button, the whole stack centred from the status row's foot (y 40) or free_top down: the
# 60 px glyph, 14, the title's 24 and the hint's 20 (118), then 20 and the 38 px button (176).
@pytest.mark.parametrize('row, free, top, glyph', [(None, 0, 40 + (280 - 118) // 2, b'cards'),
                                                   ('Scan Music Now', 0, 40 + (280 - 176) // 2, b'cards'),
                                                   # rows above it: the compact form, no glyph (44 px of words)
                                                   ('Scan Music Now', 90, 90 + (230 - 102) // 2, b'')])
def test_the_empty_state_centres_its_stack_and_its_one_row(fw, st, row, free, top, glyph):
    ui = Ui(fw)
    fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: ui.new(C.string_at(kind).decode(), parent, x, y, w, 0))
    use_set(st)
    win = ui.window('coverflow_page')
    body = ui.add('view', win, (0, 58, 375, 262), 'body')
    keep = [C.create_string_buffer(s) for s in (b'cards', b'No Albums Yet', b"Music on the card isn't scanned",
                                                row.encode() if row else b'')]
    e = (C.c_char_p * 6)(*[C.c_char_p(k.value) for k in keep[:3]], C.c_char_p(keep[3].value if row else None), None, None)  # empty_t: foot, bar 0
    clicked = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(lambda *_: 0)
    assert fw.fn('empty_state', C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(body.addr, free, C.addressof(e), C.cast(clicked, C.c_void_p)) == 1
    art = body.find('_empty_state')
    assert art and art.props['_es_glyph'] == glyph and art.props['_es_title'] == b'No Albums Yet' and art.props['_es_top'] == top
    lists = [c for c in body.children if c.kind == 'list_view']
    if not row:
        assert lists == []
    else:
        (lv,) = lists  # the button's band, its words the painter's (the row's own are blank)
        words = 24 + 20 + (60 + 14 if glyph else 0)  # the glyph's part only in the whole form
        assert lv.box[1] == top + words + 20 - 58 and lv.box[3] == 38 and lv.labels()[0].text == ''
        assert art.props['_es_row'] == b'Scan Music Now' and lv.props['style:normal:bg_color'] == 0


def test_power_s_rows_carry_their_captions(fw):
    ui = Ui(fw)
    page = Power(fw, ui, {})
    # In-Vehicle Mode's title is stock's translation key (system_carmode), which this harness shows as it is
    assert captions(fw, page.view) == {
        'Standby Limit': 'Shuts down a Q2 left in Standby this long',
        'Charge Limit': 'Stops charging at 80%, starts again at 75%',
        'Smart Wi-Fi': 'Turns Wi-Fi off after 5 minutes when nothing needs it. AirPlay and Car Remote keep it on.',
        'system_carmode': 'Plays at power-on, shuts down when unplugged'}
