"""System journeys through the firmware's real entry points, stock's calls stubbed: a Q2 that boots and loads its
theme, opens Settings and saves a choice, powers off and on again with the same flash, locks, sleeps, wakes and plays.
A restart is a fresh load of the library sharing the flash, so every global starts again as the Q2's do."""
import ctypes as C
import struct

import pytest

import host
from conftest import O, Files, in_fork, stock_prop_str

W_PARENT, EVT_CLICK, TIMER_CTX = O['W_PARENT'], O['EVT_CLICK'], O['TIMER_CTX']
EVT_KEY_DOWN, EVT_KEY_UP = O['EVT_KEY_DOWN'], O['EVT_KEY_UP']
KEY_CENTER, KEY_RETURN, KEY_PLAY = O['KEY_CENTER'], O['KEY_RETURN'], O['KEY_PLAY']
WAKE_MS, IDLE_MS = O['WAKE_MS'], O['IDLE_MS']
STOPPED, PLAYING, PAUSED = 1, 2, 3  # mclGetPlayStatus
RTC_NOW, RTC_ALARM, STATE = '/sys/class/rtc/rtc0/since_epoch', '/sys/class/rtc/rtc0/wakealarm', '/mnt/data/q2-state'
NOON, NIGHT = 1699963200, 1699999200  # 2023-11-14, 12:00 and 22:00 UTC
TIMEZONES = host.tools_build.TIMEZONES  # g_time_zone's zones: (label, TZ) by index


class Flash:
    """/mnt/data, which a power-off keeps: config.ini as {(section, key): text}, and the files."""
    def __init__(self, **ini):
        self.ini, self.files = {}, {}
        for name, value in ini.items():
            section, key = name.split('_', 1)
            self.ini[section.encode(), key.encode()] = value.encode()

    def get(self, section, key):
        value = self.ini.get((section.encode(), key.encode()))
        return value.decode() if value is not None else None


class Disk(Files):
    """conftest's files, each write kept in order as (path, bytes) when its file closes."""
    def __init__(self, fw, files):
        super().__init__(fw, files)
        self.written = []

    def fclose(self, f, *_):
        path, _, data = self.open[f]
        if data is not None:
            self.written.append((path, data))
        return super().fclose(f)


class Screen:
    """AWTK's widget tree as the payload reads it: each widget a zeroed block holding its box (W_X..W_H) and its
    parent (W_PARENT), with a name, a type, children, text, visibility, properties and handlers kept here."""
    def __init__(self, fw):
        self.blocks, self.kids, self.names, self.types, self.texts = [], {}, {}, {}, {}
        self.shown, self.props, self.handlers, self.strings = {}, {}, [], {}
        self.wm = self.new(0, 'window_manager', 0, 0, 375, 320)
        self.top = 0
        fw.mock('window_manager', lambda *_: self.wm)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('widget_lookup', lambda w, name, *_: self.lookup(w, C.string_at(name).decode()))
        fw.mock('widget_set_name', lambda w, name, *_: self.names.__setitem__(w, C.string_at(name).decode()))
        fw.mock('widget_get_type', lambda w, *_: self.string(self.types.get(w, '')))
        fw.mock('widget_count_children', lambda w, *_: len(self.kids.get(w, ())))
        fw.mock('widget_get_child', lambda w, i, *_: self.child(w, i))
        fw.mock('widget_destroy_children', lambda w, *_: self.kids.__setitem__(w, []))
        for kind in ('list_item', 'button', 'image', 'hscroll_label', 'label'):
            fw.mock(f'{kind}_create', lambda p, x, y, w, h, *_, k=kind: self.new(p, k, x, y, w, h))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_set_visible', lambda w, on, *_: self.shown.__setitem__(w, C.c_int(on).value))
        fw.mock('widget_get_visible', lambda w, *_: self.shown.get(w, 1))
        fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: self.box(w, x, y, ww, h))
        fw.mock('widget_resize', lambda w, ww, h, *_: self.resize(w, ww, h))
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_get_prop_int', lambda w, key, default, *_: self.prop(w, key, default))
        fw.mock('widget_set_prop_str', lambda w, key, v, *_: self.set_prop(w, C.string_at(key).decode(),
                                                                           C.string_at(v).decode()))
        fw.mock('widget_set_prop_int', lambda w, key, v, *_: self.set_prop(w, C.string_at(key).decode(),
                                                                           C.c_int(v).value))
        fw.mock('widget_restack', lambda w, i, *_: self.restack(w, i))
        fw.mock('image_base_set_image', lambda w, name, *_: self.set_prop(w, 'image', C.string_at(name).decode()))

    def new(self, parent, kind, x, y, w, h, name=None):
        block = C.create_string_buffer(0x400)
        self.blocks.append(block)
        a = C.addressof(block)
        self.box(a, x, y, w, h)
        C.c_void_p.from_address(a + W_PARENT).value = parent or None
        self.kids[a], self.types[a] = [], kind
        if parent:
            self.kids.setdefault(parent, []).append(a)
        if name:
            self.names[a] = name
        return a

    def window(self, name, *children, parent=None):
        """A window named name on top, holding a widget for each child: a name, or (name, type, its parent's name)."""
        win = self.new(parent or self.wm, 'window', 0, 0, 375, 320, name)
        self.props[win] = {'name': name}
        for c in children:
            cname, kind, under = (c, 'view', None) if isinstance(c, str) else c
            self.new(self.lookup(win, under) if under else win, kind, 0, 0, 375, 320, cname)
        if not parent:
            self.top = win
        return win

    def string(self, text):
        if text not in self.strings:
            self.strings[text] = C.create_string_buffer(text.encode())
        return C.addressof(self.strings[text])

    def child(self, w, i):
        kids = self.kids.get(w, ())
        return kids[i] if 0 <= i < len(kids) else 0

    def parent(self, w):
        return C.c_void_p.from_address(w + W_PARENT).value

    def lookup(self, root, name):
        todo = list(self.kids.get(root, ()))
        while todo:
            w = todo.pop(0)
            if self.names.get(w) == name:
                return w
            todo += self.kids.get(w, ())
        return 0

    def at(self, w):
        """w's box: x, y, width, height."""
        return struct.unpack_from('<iiii', (C.c_char * 16).from_address(w))

    def box(self, w, x, y, ww, h):
        struct.pack_into('<iiii', (C.c_char * 16).from_address(w), 0, *(C.c_int(v).value for v in (x, y, ww, h)))

    def resize(self, w, ww, h):
        x, y, _, _ = self.at(w)
        self.box(w, x, y, ww, h)

    def prop(self, w, key, default):
        value = self.props.get(w, {}).get(C.string_at(key).decode())
        return default if value is None else value

    def prop_str(self, w, key, default, *_):
        if w not in self.types:
            return default  # stock: widget_get_prop fails on a NULL widget
        key = C.string_at(key).decode()
        value = self.names.get(w) if key == 'name' else self.props.get(w, {}).get(key)
        value = stock_prop_str(self.types[w].encode(), key.encode(), value.encode() if isinstance(value, str) else value, default)
        return 0 if value is None else self.string(value.decode()) if isinstance(value, bytes) else value

    def set_prop(self, w, key, value):
        self.props.setdefault(w, {})[key] = value

    def restack(self, w, i):
        kids = self.kids[self.parent(w)]
        kids.remove(w)
        kids.insert(min(i, len(kids)), w)

    def subtree(self, root):
        for w in self.kids.get(root, ()):
            yield w
            yield from self.subtree(w)

    def labels(self, root):
        for w in self.subtree(root):
            if self.types[w] == 'hscroll_label' and w in self.texts and self.names.get(w) != '_setting_value':
                value = self.lookup(self.parent(w), '_setting_value')
                yield w, self.texts[w] + (': ' + self.texts[value] if value and value in self.texts else '')

    def rows(self, root=None):
        """Each row's text under root (the top window), in order: a label's, and the value beside it."""
        return [text for _, text in self.labels(root or self.top)]

    def row(self, starts, root=None):
        found = [text for text in self.rows(root) if text.startswith(starts)]
        assert len(found) == 1, f'{starts!r} in {self.rows(root)}'
        return found[0]

    def button(self, starts, root=None, exact=False):
        for w, text in self.labels(root or self.top):
            if text == starts if exact else text.startswith(starts):
                return self.parent(w)
        raise AssertionError(f'no row {starts!r} in {self.rows(root)}')

    def switch(self, starts):
        """The switch image beside the row whose text starts with starts (a list_toggle row)."""
        return self.props.get(self.lookup(self.button(starts), '_setting_switch'), {}).get('image')

    def click(self, starts):
        """Centre on the row whose text starts with starts: its button's click handler, as stock dispatches it."""
        button = self.button(starts)
        (fn, ctx), = [(fn, ctx) for w, evt, fn, ctx in self.handlers if w == button and evt == EVT_CLICK]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0)


class Q2:
    """One Q2: its flash and RTC, its player and output, and the firmware as loaded at its last power-on, stock's
    calls answered by the models here. Its write_int_config is the hook's (ringnav_write_config at its entry, as
    tools/build.py patches it) and its player_play_pause ringnav_play_pause; config.ini and the RTC outlive a load."""
    def __init__(self, fw, library, tmp_path, flash=None):
        self.library, self.tmp, self.flash, self.loads = library, tmp_path, flash or Flash(), [fw]
        self.now, self.wall, self.rtc, self.zone = 100000, 1700000000, 1700000000, ''
        self.daylight: set[str] = set()  # the zones on daylight saving now
        self.status, self.way, self.confirm, self.asleep = STOPPED, 0, 10, 0
        self.pages, self.dialogs = [], []
        self.asleep_state, self.protect = None, []
        self.wire(fw)

    def power_on(self):
        """Power off and on again: the same flash, a fresh load of the firmware."""
        if len(self.loads) > 1:
            self.loads[-1].close()
        boot = self.tmp / f'boot{len(self.loads)}'
        boot.mkdir()
        fw = host.Fw(self.library, boot)
        self.loads.append(fw)
        self.wire(fw)
        return fw

    def close(self):
        for fw in self.loads[1:]:
            fw.close()

    def wire(self, fw):
        self.fw, self.timers = fw, {}
        self.screen = Screen(fw)
        self.disk = Disk(fw, self.flash.files)
        self.flash.files[RTC_NOW] = b'%d\n' % self.rtc
        fw.mock('toolsReadConfig', self.read_config)
        fw.mock('toolsWriteConfig', lambda p, s, k, v, *_: self.put(s, k, C.string_at(v)))
        hook = fw.fn('ringnav_write_config', C.c_int, C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('write_int_config', lambda v, s, k, *_: hook(C.c_int(v).value, s, k))
        fw.mock('stock_write_config_trampoline', lambda v, s, k, *_: self.put(s, k, b'%d' % C.c_int(v).value))
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_time', lambda *_: self.wall)
        fw.mock('fw_localtime_r', self.localtime)
        fw.mock('toolsSetTimeZone', lambda name, *_: setattr(self, 'zone', C.string_at(name).decode()))
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        pause = fw.fn('ringnav_play_pause', C.c_int)
        fw.mock('player_play_pause', lambda *_: pause())
        fw.mock('stock_pause_trampoline', lambda *_: setattr(self, 'status',
                                                             PAUSED if self.status == PLAYING else PLAYING))
        fw.mock('mclGetOutputWay', lambda *_: self.way)
        fw.mock('stock_output_trampoline', lambda way, *_: setattr(self, 'way', way))
        fw.mock('fw___xstat', lambda *_: -1)  # no Rockbox on the card
        fw.mock('navigator_to', lambda name, *_: self.open(C.string_at(name).decode()) and 0)
        fw.mock('navigator_to_with_context', lambda name, ctx, *_: self.dialog(C.string_at(name).decode()))
        fw.mock('timer_add', self.timer_add)
        fw.mock('timer_remove', lambda t, *_: self.timers.pop(t, None) and 0)
        fw.mock('stock_standby_trampoline', self.suspend)
        self.builders = {
            'systemset/display_page': lambda: self.build('ringnav_display', 'display_page', 'scroll_view_display'),
            'systemset/systemset_page': lambda: self.build('ringnav_systemset', 'systemset_page', 'scroll_view_sysset'),
            'systemset/powermanager_page': lambda: self.build('ringnav_powermanager', 'powermanager_page',
                                                              'scroll_view_powermanager'),
        }

    # config.ini
    def read_config(self, path, section, key, out, default, *_):
        """Stock's: the value, else the default; a missing key with an empty default leaves out alone."""
        name = C.string_at(section), C.string_at(key)
        value = self.flash.ini.get(name, C.string_at(default))
        if value or name in self.flash.ini:
            C.memmove(out, value + b'\0', len(value) + 1)
        return 0

    def put(self, section, key, value):
        self.flash.ini[C.string_at(section), C.string_at(key)] = value
        return 0

    # the clock: UTC wall seconds, local by the zone's standard offset (its TIMEZONES label, "GMT-5 New York"), an
    # hour on for a zone on daylight saving; struct tm as the payload reads it (tm_gmtoff its tenth int)
    def localtime(self, when, out, *_):
        t = C.c_long.from_address(when).value + self.offset()
        tm = (C.c_int * 11).from_address(out)
        tm[0], tm[1], tm[2], tm[9] = t % 60, t // 60 % 60, t // 3600 % 24, self.offset()
        return out

    def offset(self):
        for label, tz in TIMEZONES:
            if tz == self.zone:
                return host.tools_build.timezone_offset(label) * 60 + 3600 * (tz in self.daylight)
        return 0

    # pages, dialogs and timers
    def open(self, name):
        """Stock opening a page: its init, hooked, builds it."""
        self.pages.append(name)
        if name in self.builders:
            self.builders[name]()
        return self.screen.top

    def build(self, init, name, view, *more):
        win = self.screen.window(name, view, *more)
        self.fw.fn(init, C.c_int, C.c_void_p, C.c_void_p)(win, 0)
        return win

    def dialog(self, name):
        self.dialogs.append(name)
        return self.confirm if name == 'dialog/confirminfo_dialog' else 0

    def timer_add(self, fn, ctx, ms, *_):
        t = max(self.timers, default=0) + 1
        self.timers[t] = (fn, ctx)
        return t

    def run_timers(self):
        """Each timer once, as the UI loop dispatches them: one asking again (RET_REPEAT, 8) stays."""
        info = C.create_string_buffer(0x40)
        for t, (fn, ctx) in list(self.timers.items()):
            if t in self.timers:
                C.c_void_p.from_address(C.addressof(info) + TIMER_CTX).value = ctx or None
                if C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info)) != 8:
                    self.timers.pop(t, None)

    def suspend(self, *_):
        """Stock's Standby, suspended to RAM until something resumes it, asleep seconds on by the RTC."""
        self.asleep_state = self.flash.files.get(STATE)
        self.rtc += self.asleep
        self.flash.files[RTC_NOW] = b'%d\n' % self.rtc
        return 0

    # the UI loop and input
    def var(self, name, ctype=C.c_ubyte):
        return self.fw.var(name, ctype)

    def loop(self, ms=100):
        """One UI loop pass (main_loop_sleep_default, hooked: ringnav_sleep), ms after the last."""
        self.now += ms
        return self.fw.fn('ringnav_sleep', C.c_int, C.c_void_p)(0x5100)

    def key(self, key, kind, ms=50):
        """A key event reaching window_manager_dispatch_input_event (hooked: ringnav_input) ms after the last thing;
        whether it went on to stock, and so to the UI."""
        self.now += ms
        event = C.create_string_buffer(0x40)
        struct.pack_into('<I', event, O['EVENT_TYPE'], kind)
        struct.pack_into('<I', event, O['EVENT_TIME'], self.now & 0xffffffff)
        struct.pack_into('<I', event, O['EVENT_KEY'], key)
        before = len(self.fw.calls('stock_input_trampoline'))
        self.fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)(self.screen.wm, event)
        return len(self.fw.calls('stock_input_trampoline')) > before

    def press(self, key, hold=80):
        """A press and, hold ms later, its release: whether each went on."""
        return self.key(key, EVT_KEY_DOWN), self.key(key, EVT_KEY_UP, hold)

    def sleeps(self):
        """Each pass's own idle (sleep_ms), in ms."""
        return [args[0] for _, args in self.fw.calls('sleep_ms')]


@pytest.fixture
def q2(fw, library, tmp_path):
    q = Q2(fw, library, tmp_path)
    yield q
    q.close()


def appearance(q2):
    """System settings → Display → Appearance."""
    q2.open('systemset/display_page')
    q2.screen.click('Appearance')


def controls(q2):
    """Settings → Controls, Home's Settings row (navigation.c set_open)."""
    q2.fw.fn('navigation__set_open', C.c_int, C.c_void_p, C.c_void_p)(4, None)


def dark(q2):
    """Stock turns the screen off; a UI loop pass follows."""
    q2.var('g_backlight_status').value = 0
    q2.loop()


def lit(q2):
    """Stock lights the screen; the next UI loop pass is the first lit one."""
    q2.var('g_backlight_status').value = 1
    q2.loop()


def red(q2):
    """What the payload draws stock's red as: the accent's red tone (accent_tone's TONE_RED column)."""
    return q2.fw.fn('accent_tone', C.c_uint, C.c_int)(3)


def ink(q2, color=0xff000000):
    """What the payload draws color as (every colour it draws passes ui_ink): black by default."""
    return q2.fw.fn('ui_ink', C.c_uint, C.c_uint)(color)


def invalidated(q2):
    return [args[:2] for _, args in q2.fw.calls('widget_invalidate_force')]


# The boot and the theme (docs/internals.md#themes): theme_boot, from the first window's load, migrates a unit an older
# build left on a retired theme.

def boot(q2):
    """Power-on up to the first window: application_init's first window, the status bar, loads (ringnav_ui_load)."""
    q2.fw.fn('ringnav_ui_load', C.c_void_p, C.c_char_p, C.c_void_p)(b'system_bar', 0)


@in_fork
def test_a_unit_left_on_another_theme_takes_oleds_preset_once(fw, library, tmp_path):
    """theme_boot's migration: THEME 1 (Classic, an older build's) takes OLED's preset and THEME 3, then boots."""
    q2 = Q2(fw, library, tmp_path, Flash(IPOD_THEME='1', IPOD_LIGHT='1', IPOD_HOME='1'))
    boot(q2)
    assert q2.flash.get('IPOD', 'THEME') == '3'
    # OLED's preset (its theme.xml): Light Mode off, Home Split
    assert q2.flash.get('IPOD', 'LIGHT') == '0' and q2.flash.get('IPOD', 'HOME') == '0'
    appearance(q2)
    assert q2.screen.row('Light Mode') == 'Light Mode: Off' and q2.screen.row('Home') == 'Home: Split'
    q2.screen.click('Light Mode')
    assert q2.flash.get('IPOD', 'LIGHT') == '1'
    q2.power_on()
    boot(q2)
    appearance(q2)
    assert q2.screen.row('Light Mode') == 'Light Mode: On'  # the preset was the migration's alone
    assert q2.flash.get('IPOD', 'THEME') == '3'
    q2.close()


# Home (docs/ipod.md#home): Split, Full or Carousel, and its Shortcut row.

def home(q2):
    """Home (home_page_init, hooked: coverflow_home) with its list, art panel, carousel and Shortcut row."""
    win = q2.screen.window('home_page', 'img_homeart', 'list_view_home', 'slide_menu', 'img_left', 'img_right',
                           ('btn_rockbox', 'button', 'list_view_home'), 'label_rockbox')
    q2.fw.fn('coverflow_home', C.c_int, C.c_void_p, C.c_void_p)(win, 0)
    return {name: q2.screen.lookup(win, name)
            for name in ('img_homeart', 'list_view_home', 'slide_menu', 'label_rockbox')}


def test_home_takes_a_new_layout_at_once_and_boots_into_it(q2):
    w = home(q2)
    shown, width = q2.screen.shown, lambda: q2.screen.at(w['list_view_home'])[2]
    assert shown[w['img_homeart']] and not shown[w['slide_menu']]  # Split: the list beside the art
    split = width()
    assert split < 375
    appearance(q2)
    q2.screen.click('Home')
    assert q2.screen.row('Home') == 'Home: Full' and q2.flash.get('IPOD', 'HOME') == '1'
    assert not shown[w['img_homeart']] and width() == 375 > split  # the same Home, never rebuilt, relaid at once
    q2.power_on()
    w = home(q2)
    shown = q2.screen.shown
    assert not shown[w['img_homeart']] and width() == 375
    appearance(q2)
    q2.screen.click('Home')
    assert q2.screen.row('Home') == 'Home: Carousel'
    assert shown[w['slide_menu']] and not shown[w['list_view_home']] and not shown[w['img_homeart']]


# Display settings saved and read back after a restart.

def grey(color):
    return color & 255 == color >> 8 & 255 == color >> 16 & 255


def test_light_mode_turned_on_redraws_the_screen_light_and_stays_on_after_a_restart(q2):
    appearance(q2)
    assert q2.screen.row('Light Mode') == 'Light Mode: Off' and ink(q2) == 0xff000000
    q2.screen.click('Light Mode')
    assert q2.screen.row('Light Mode') == 'Light Mode: On' and q2.flash.get('IPOD', 'LIGHT') == '1'
    paper = ink(q2)
    assert paper >> 24 == 0xff and grey(paper) and paper & 255 > 0x80  # black is the light half's paper
    assert ink(q2, 0xffffffff) & 255 < 0x80  # and white its ink
    # at once: the cached images, which bake the mode in, dropped and the whole screen repainted
    assert q2.fw.calls('image_manager_unload_all') and (q2.screen.wm, 0) in invalidated(q2)
    q2.power_on()
    assert ink(q2) == paper
    appearance(q2)
    assert q2.screen.row('Light Mode') == 'Light Mode: On'


@pytest.mark.parametrize('wall, light', [(NOON, True), (NIGHT, False)])
def test_light_mode_auto_follows_the_clock_after_a_restart(q2, wall, light):
    q2.wall = NOON
    appearance(q2)
    q2.screen.click('Light Mode')
    q2.screen.click('Light Mode')
    assert q2.screen.row('Light Mode') == 'Light Mode: Auto' and q2.flash.get('IPOD', 'LIGHT') == '2'
    assert ink(q2) != 0xff000000  # light from 7:00 to 19:00
    q2.wall = wall
    q2.power_on()
    assert (ink(q2) != 0xff000000) == light


def test_the_battery_style_reaches_the_bar_at_once(q2):
    bar = q2.screen.window('system_bar', parent=q2.screen.wm)
    q2.fw.var('fw_ctx_system_bar', C.c_void_p).value = bar
    appearance(q2)
    q2.screen.click('Battery')
    assert q2.screen.row('Battery') == 'Battery: Percent' and q2.flash.get('IPOD', 'BATTERY') == '1'
    assert invalidated(q2)[-1] == (bar, 0)  # the bar repaints


# The time zone (docs/internals.md#clock).

def status_bar(q2):
    """The status bar and its clock, which the bar's first paint (ringnav_paint_bg) starts: the clock's label."""
    bar = q2.screen.window('system_bar', 'label_clock', parent=q2.screen.wm)
    q2.fw.var('fw_ctx_system_bar', C.c_void_p).value = bar
    q2.fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(bar, C.create_string_buffer(0x100))
    q2.run_timers()
    return q2.screen.lookup(bar, 'label_clock')


def select_timezone(q2, zone):
    """Stock setting the zone: g_time_zone, then select_timezone, hooked (ringnav_timezone). config_init does it at
    boot with the saved index, the Time Zone page's click with its row's."""
    q2.var('g_time_zone').value = zone
    q2.fw.fn('ringnav_timezone', C.c_int)()


def zone_rows(q2):
    """System settings → Date and Time → Time Zone (autotime_page): stock builds its rows in order, row 0 the
    automatic switch, each through its row builder, hooked (ringnav_timezone_row), which hands stock the index it
    names, ticks and clicks the row by (row k: zone k - 1, as stock's table); stock's row is a button holding a
    label, and the hook adds its value. [(the label's text, the value's, stock's index)] top to bottom."""
    given: list[int] = []
    def stock_row(item, index, *_):
        given.append(index)
        q2.screen.new(q2.screen.new(item, 'button', 0, 0, 375, 70), 'hscroll_label', 0, 0, 246, 70)
    q2.fw.mock('stock_timezone_row_trampoline', stock_row)
    view = q2.screen.lookup(q2.screen.window('autotime_page', 'list_view_zone'), 'list_view_zone')
    build = q2.fw.fn('ringnav_timezone_row', None, C.c_void_p, C.c_int)
    rows = []
    for i in range(len(TIMEZONES) + 1):
        item = q2.screen.new(view, 'list_item', 0, 0, 375, 70)
        build(item, i)
        label, *value = [w for w in q2.screen.subtree(item) if q2.screen.types[w] == 'hscroll_label']
        # '': stock's text, which the model leaves out
        rows.append((q2.screen.texts.get(label, ''), q2.screen.texts.get(value[0], '') if value else '', given[-1]))
    return rows


@pytest.mark.parametrize('daylight, york, clock', [(set(), ('New York', 'UTC-5'), '07:00'),
                                                   ({'America/New_York'}, ('New York', 'UTC-4'), '08:00')],
                         ids=['standard', 'daylight-saving'])
def test_a_time_zone_picked_from_its_list_moves_the_clock_at_once_and_is_set_again_at_boot(q2, daylight, york,
                                                                                            clock):
    zones = [tz for _, tz in TIMEZONES]
    q2.wall, q2.daylight = NOON, daylight
    q2.var('g_time24h_flag').value = 1
    select_timezone(q2, zones.index('Europe/London'))  # the zone saved, at boot
    bar = status_bar(q2)
    assert q2.screen.texts[bar] == '12:00'
    rows = zone_rows(q2)
    assert rows[0] == ('', '', 0)  # the switch: stock's own
    # the list read each zone's offset now; the saved zone is set again
    assert q2.zone == 'Europe/London' and q2.screen.texts[bar] == '12:00'
    listed = rows[1:]
    assert listed[0] == ('London', 'UTC+0', zones.index('Europe/London') + 1)  # the saved zone, pinned first
    offsets = [host.tools_build.timezone_offset('GMT' + value[3:]) for _, value, _ in listed[1:]]
    assert offsets == sorted(offsets)  # then west to east, by the offset each has now
    assert sorted(index for *_, index in listed) == list(range(1, len(TIMEZONES) + 1))  # each zone once
    # each row clicks the zone it names: its city is its index's
    assert all(city == TIMEZONES[index - 1][0].split(' ', 1)[1] for city, _, index in listed)
    picked, = [index for city, value, index in listed if (city, value) == york]
    assert picked - 1 == zones.index('America/New_York')
    tzsets = len(q2.fw.calls('fw_tzset'))
    select_timezone(q2, picked - 1)  # stock's click on that row
    assert q2.zone == 'America/New_York'
    assert len(q2.fw.calls('fw_tzset')) > tzsets  # localtime_r reloads no TZ itself
    assert q2.screen.texts[bar] == clock  # at once
    q2.power_on()
    select_timezone(q2, picked - 1)  # stock saved the index; config_init reads it back
    assert q2.zone == 'America/New_York'
    q2.var('g_time24h_flag').value = 1
    assert q2.screen.texts[status_bar(q2)] == clock


def test_a_time_zone_index_past_the_table_is_stocks_own(q2):
    q2.var('g_time_zone').value = len(TIMEZONES)
    q2.fw.fn('ringnav_timezone', C.c_int)()
    assert q2.fw.calls('stock_timezone_trampoline') and not q2.fw.calls('toolsSetTimeZone')


# Screen off and the UI loop's sleep (docs/internals.md#battery).

def painter(q2):
    """The window manager's own paint (its vtable's +0xc): the frames it painted."""
    frames = []
    paint = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda wm: frames.append(wm) or 0)
    vt = C.create_string_buffer(0x40)
    C.c_void_p.from_address(C.addressof(vt) + 0xc).value = C.cast(paint, C.c_void_p).value
    C.c_void_p.from_address(q2.screen.wm + 0x94).value = C.addressof(vt)
    q2.screen.blocks += [paint, vt]
    return frames


def frame(q2):
    """window_manager_paint, a leaf hooked: ringnav_wm_paint, each loop pass."""
    return q2.fw.fn('ringnav_wm_paint', C.c_int, C.c_void_p)(q2.screen.wm)


def test_the_screen_off_draws_nothing_and_the_first_lit_pass_redraws_all_of_it(q2):
    frames = painter(q2)
    lit(q2)
    frame(q2)
    assert len(frames) == 1
    dark(q2)  # stock's screen_action(0): the backlight off, the framebuffer blanked
    assert frame(q2) == 0 and len(frames) == 1  # Now Playing's timer drawing into a blank screen: no paint
    q2.loop()
    before = len(invalidated(q2))
    lit(q2)
    assert invalidated(q2)[before:] == [(q2.screen.wm, 0)]
    frame(q2)
    assert len(frames) == 2


def test_a_dark_pass_sleeps_longer_once_nothing_plays_and_the_dac_is_off(q2):
    usleep = q2.fw.fn('ringnav_dark_usleep', C.c_int, C.c_uint)  # stock's 200 ms status poller sleeps here
    polls = lambda: [args[0] for _, args in q2.fw.calls('fw_usleep')]  # noqa: E731
    q2.status = PLAYING
    dark(q2)
    playing = q2.sleeps()[-1]
    usleep(200000)
    q2.status = PAUSED
    q2.loop()
    paused = q2.sleeps()[-1]
    q2.fw.var('fw_ctx_g_dacoff_time', C.c_int).value = -1  # check_dacoff_state powered the DAC down
    q2.loop()
    quiet = q2.sleeps()[-1]
    usleep(200000)
    assert playing == paused < quiet
    assert polls()[:2] == [O['DARK_POLL_US']] * 2  # dark, playing or quiet, stock's status poll slows down too
    lit(q2)
    usleep(200000)
    assert polls()[-1] == 200000


def test_a_quiet_dark_pass_keeps_the_quick_pace_while_a_second_wake_press_may_come(q2):
    q2.flash.ini[b'Q2POD', b'WAKEDOUBLE'] = b'1'
    q2.fw.var('fw_ctx_g_dacoff_time', C.c_int).value = -1
    q2.loop()
    dark(q2)
    quiet = q2.sleeps()[-1]
    assert q2.key(KEY_CENTER, EVT_KEY_DOWN) is False  # the first press: a second may follow within WAKE_MS
    pressed = q2.now
    assert q2.key(KEY_CENTER, EVT_KEY_UP, 80) is False
    q2.loop(ms=pressed + WAKE_MS - 1 - q2.now)
    assert q2.sleeps()[-1] < quiet  # the second press is timed as before
    q2.loop(ms=1)
    assert q2.sleeps()[-1] == quiet  # WAKE_MS after it, none can come


def test_a_lit_screen_idles_only_once_input_has_been_still_for_a_while(q2):
    lit(q2)
    q2.press(KEY_PLAY)
    count = len(q2.sleeps())
    q2.loop(ms=50)
    assert len(q2.sleeps()) == count  # input a moment ago: stock's pace
    q2.loop(ms=IDLE_MS - 50)
    assert len(q2.sleeps()) == count + 1  # still for IDLE_MS
    idle = q2.sleeps()[-1]
    dark(q2)
    assert 0 < idle < q2.sleeps()[-1]  # lit, an idle pass is shorter than a dark one
    assert len(q2.fw.calls('stock_sleep_trampoline')) == 4  # stock's own pacing follows each of the four passes


# Waking (docs/internals.md#wake).

def test_wake_double_press_chosen_on_controls_wakes_only_on_a_second_press_after_a_restart(q2):
    controls(q2)
    assert q2.screen.row('Wake') == 'Wake: Any Button'
    q2.screen.click('Wake')
    assert q2.screen.row('Wake') == 'Wake: Double Press' and q2.flash.get('Q2POD', 'WAKEDOUBLE') == '1'
    q2.power_on()
    q2.loop()  # the first pass reads the settings
    dark(q2)
    assert q2.press(KEY_CENTER) == (False, False)  # a pocket bump stays dark
    # WAKE_MS after the first: too late for it, a lone press again
    assert (q2.key(KEY_CENTER, EVT_KEY_DOWN, WAKE_MS - 80), q2.key(KEY_CENTER, EVT_KEY_UP, 80)) == (False, False)
    assert q2.press(KEY_CENTER) == (True, True)  # a second within WAKE_MS wakes, press and release


def test_a_single_press_wakes_and_a_blind_second_press_only_wakes(q2):
    q2.loop()
    dark(q2)
    assert q2.press(KEY_CENTER) == (True, True)
    lit(q2)
    assert invalidated(q2).count((q2.screen.wm, 0)) == 1  # the first lit pass repaints everything once
    assert q2.press(KEY_CENTER) == (False, False)  # made during the wake's stall: no click on the row
    # WAKE_MS after that pass, a press is the user's own
    assert (q2.key(KEY_CENTER, EVT_KEY_DOWN, WAKE_MS - 130), q2.key(KEY_CENTER, EVT_KEY_UP, 80)) == (True, True)
    q2.loop()
    assert invalidated(q2).count((q2.screen.wm, 0)) == 1


def test_dark_return_is_dropped_but_play_works(q2):
    q2.loop()
    dark(q2)
    assert q2.press(KEY_RETURN) == (False, False)  # a bump of Back leaves the page alone
    assert q2.press(KEY_PLAY) == (True, True)
    lit(q2)
    assert q2.press(KEY_RETURN) == (True, True)


# Standby limit (docs/internals.md#standby-limit).

def standby(q2, asleep):
    """Stock's Standby timer ran out: toolsIntoStandby, hooked (ringnav_standby), suspends for asleep seconds."""
    q2.asleep = asleep
    return q2.fw.fn('ringnav_standby', C.c_int)()


def alarms(q2):
    return [data for path, data in q2.disk.written if path == RTC_ALARM]


@pytest.mark.parametrize('presses, hours, late', [(1, O['STANDBY_LIMIT_1'], 5), (2, O['STANDBY_LIMIT_2'], 0),
                                                 (3, O['STANDBY_LIMIT_3'], 5)])
def test_a_standby_limit_chosen_on_power_ends_that_long_a_standby_in_a_power_off(q2, presses, hours, late):
    q2.open('systemset/powermanager_page')
    assert q2.screen.row('Standby Limit') == 'Standby Limit: Off'
    for _ in range(presses):
        q2.screen.click('Standby Limit')
    assert q2.screen.row('Standby Limit') == f'Standby Limit: {hours}h'
    assert q2.flash.get('Q2POD', 'STANDBYLIMIT') == str(presses)
    q2.power_on()
    q2.loop()
    start = q2.rtc
    standby(q2, hours * 3600 + late)  # on a Q2 the alarm's resume came 5 s after it
    # rtc-sysfs refuses a new alarm while one is set: cleared, set, and cleared on the resume
    assert alarms(q2) == [b'0', b'%d' % (start + hours * 3600), b'0']
    assert b'power:suspending' in q2.asleep_state  # what a resume that never came leaves behind
    assert 'poweroff_page' not in q2.pages
    q2.loop()  # the resume stays dark; the next pass powers off as stock's Auto power-off
    assert q2.pages.count('poweroff_page') == 1
    q2.loop()
    assert q2.pages.count('poweroff_page') == 1


LIMIT_1 = O['STANDBY_LIMIT_1'] * 3600  # STANDBYLIMIT 1, in seconds


@pytest.mark.parametrize('asleep, cable, status', [(LIMIT_1 // 2, 0, STOPPED), (LIMIT_1 - 1, 0, STOPPED),
                                                   (LIMIT_1 + 5, 1, STOPPED), (LIMIT_1 + 5, 0, PLAYING)])
def test_a_standby_ended_sooner_or_with_a_cable_in_or_music_playing_stays_on(q2, asleep, cable, status):
    q2.flash.ini[b'Q2POD', b'STANDBYLIMIT'] = b'1'
    q2.loop()
    standby(q2, asleep)
    q2.var('g_usbdet_value').value, q2.status = cable, status
    q2.loop()
    q2.loop()
    assert 'poweroff_page' not in q2.pages and alarms(q2)[-1] == b'0'
    q2.var('g_usbdet_value').value, q2.status = 0, STOPPED
    start = q2.rtc
    standby(q2, LIMIT_1 // 2)
    assert alarms(q2)[-2] == b'%d' % (start + LIMIT_1)  # the next Standby counts from zero
    q2.loop()
    assert 'poweroff_page' not in q2.pages


@pytest.mark.parametrize('limit, rtc, alarm', [('0', True, True), ('1', False, True), ('1', True, False)])
def test_without_a_limit_a_clock_or_an_alarm_standby_is_stocks(q2, limit, rtc, alarm):
    q2.flash.ini[b'Q2POD', b'STANDBYLIMIT'] = limit.encode()
    if not rtc:
        q2.flash.files.pop(RTC_NOW)
    if not alarm:  # rtc-sysfs refuses it
        fopen = q2.disk.fopen
        q2.fw.mock('fw_fopen', lambda path, mode, *_: 0 if C.string_at(path) == RTC_ALARM.encode()
                   else fopen(path, mode))
    q2.loop()
    standby(q2, 7 * 24 * 3600)
    q2.loop()
    q2.loop()
    assert alarms(q2) == [] and 'poweroff_page' not in q2.pages
    assert q2.fw.calls('stock_standby_trampoline')


# General's Restart and Shut Down (power_rows.c): last on the page after a gap, each behind its confirm.

def power_page(q2, answer):
    """Settings → General, its confirms answered answer (10 the verb); each prompt as (page, kind, ms, text)."""
    q2.open('systemset/systemset_page')
    asked = []

    def prompt(name, ctx, *_):
        kind, ms = struct.unpack('<ii', C.string_at(ctx, 8))
        asked.append((C.string_at(name).decode(), kind, ms, C.string_at(ctx + 8).decode()))
        return answer

    def words(key, out, size, *_):  # stock's English msg_poweroff, as tools/build.py renames it
        if C.string_at(key) == b'msg_poweroff':
            C.memmove(out, b'Shut Down?\0', 11)
        return 0
    q2.fw.mock('navigator_to_with_context', prompt)
    q2.fw.mock('getFormatString', words)
    return asked


def test_power_ends_with_restart_and_shut_down_after_a_gap(q2):
    power_page(q2, 0)
    assert q2.screen.rows()[-2:] == ['Restart', 'Shut Down']
    s = q2.screen
    gap, *rows = s.kids[s.lookup(s.top, 'scroll_view_sysset')][-3:]
    assert s.types[gap] == 'label' and s.at(gap)[3] == 12  # space, never a row
    tapped = {w for w, *_ in s.handlers}
    assert not ({gap} | set(s.subtree(gap))) & tapped  # nothing the wheel stops on
    for row in rows:  # no chevron: Centre opens a confirm
        assert s.types[row] == 'list_item' and set(s.subtree(row)) & tapped
        assert all(s.props.get(w, {}).get('image') != 'list_into' for w in s.subtree(row))


@pytest.mark.parametrize('row, question', [('Restart', 'Restart?'), ('Shut Down', 'Shut Down?')])
def test_a_cancelled_power_row_leaves_the_page_as_it_was(q2, row, question):
    asked = power_page(q2, 0)  # Cancel, or Return out of the prompt
    q2.status = PLAYING
    top, rows = q2.screen.top, q2.screen.rows()
    q2.screen.click(row)
    assert asked == [('dialog/confirminfo_dialog', 1, 0, question)]
    assert not q2.fw.calls('stock_reboot_trampoline') and not q2.fw.calls('save_memoryplay_info')
    assert q2.pages == ['systemset/systemset_page'] and (q2.screen.top, q2.screen.rows()) == (top, rows)


def test_restart_reboots_as_a_theme_change_does(q2):
    asked = power_page(q2, 10)
    q2.status = PLAYING
    q2.screen.click('Restart')
    assert asked == [('dialog/confirminfo_dialog', 1, 0, 'Restart?')]
    # ringnav_reboot, restart_confirm's: the queue saved, then stock's device_reboot
    assert len(q2.fw.calls('save_memoryplay_info')) == 1 and len(q2.fw.calls('stock_reboot_trampoline')) == 1
    assert 'poweroff_page' not in q2.pages


def test_shut_down_powers_off_as_the_power_keys_prompt_does(q2):
    asked = power_page(q2, 10)
    q2.screen.click('Shut Down')
    assert asked == [('dialog/confirminfo_dialog', 1, 0, 'Shut Down?')]  # stock's own msg_poweroff
    # stock's power-off page, which saves the queue and powers off (into_poweroff), as on_wm_keylong_fun opens it
    assert q2.pages == ['systemset/systemset_page', 'poweroff_page']
    assert not q2.fw.calls('stock_reboot_trampoline')


# Power: the charge limit, the watchdog and the state record (docs/internals.md#charge-limit, #watchdog).

def test_a_charge_limit_chosen_on_power_holds_charging_between_its_resume_and_stop_levels_after_a_restart(q2):
    stop, resume, poll = O['CHARGE_STOP'], O['CHARGE_RESUME'], O['CHARGE_POLL_MS']
    enable = lambda: [args[0] for _, args in q2.fw.calls('switch_charge_enable')]  # noqa: E731
    q2.fw.var('fw_ctx_g_power_capacity', C.c_int).value = stop + 5
    q2.fw.var('fw_ctx_g_power_chargestate', C.c_int).value = 1  # charging
    q2.loop()
    q2.open('systemset/powermanager_page')
    assert q2.screen.row('Charge Limit') == 'Charge Limit: Off'  # a key/value row, Off or its level (pod_text)
    q2.screen.click('Charge Limit')
    assert q2.screen.row('Charge Limit') == f'Charge Limit: {stop}%' and q2.flash.get('Q2POD', 'CHARGELIMIT') == '1'
    q2.loop()
    assert enable() == [0]  # from the next pass, though the last poll was a moment ago
    q2.power_on()
    level = q2.fw.var('fw_ctx_g_power_capacity', C.c_int)
    state = q2.fw.var('fw_ctx_g_power_chargestate', C.c_int)
    level.value, state.value = stop - 1, 1
    q2.loop()
    assert enable() == []  # charging on, short of the stop level
    level.value = stop
    q2.loop(ms=poll - 1)
    assert enable() == []  # polled every CHARGE_POLL_MS
    q2.loop(ms=1)
    assert enable() == [0]  # stopped at the stop level
    state.value = -1
    q2.loop(ms=poll)
    assert enable() == [0]
    level.value, state.value = resume + 3, 1  # USB mode's exit turned it back on
    q2.loop(ms=poll)
    assert enable() == [0, 0]
    level.value, state.value = resume + 1, -1
    q2.loop(ms=poll)
    assert enable() == [0, 0]  # held until the resume level
    level.value = resume
    q2.loop(ms=poll)
    assert enable() == [0, 0, 1]  # handed back


WDT_START, WDT_STOP, WDT_FEED = 0x8004570d, 0x2000570e, 0x2000570f  # docs/internals.md#watchdog


def watchdog(q2):
    """/dev/jz_watchdog: its ioctls in order, Standby's suspend among them."""
    events: list[object] = []
    q2.fw.mock('fw_open', lambda path, flags, *_: 9 if C.string_at(path) == b'/dev/jz_watchdog' else -1)
    q2.fw.mock('fw_ioctl', lambda fd, req, arg, *_: events.append(
        (fd, req & 0xffffffff, C.c_int.from_address(arg).value if req & 0xffffffff == WDT_START else None)) or 0)
    suspend = q2.suspend
    q2.fw.mock('stock_standby_trampoline', lambda *_: events.append('suspend') or suspend())
    return events


def test_the_watchdog_starts_with_the_ui_loop_is_fed_each_pass_and_rests_through_standby(q2):
    events = watchdog(q2)
    q2.loop()
    q2.loop()
    assert events == [(9, WDT_START, O['WDT_MS']), (9, WDT_FEED, None)]
    del events[:]
    standby(q2, 3600)  # it counts on the RTC through a suspend
    q2.loop()
    assert events == [(9, WDT_STOP, None), 'suspend', (9, WDT_START, O['WDT_MS']), (9, WDT_FEED, None)]


def test_the_state_record_says_what_the_q2_does_and_a_power_off_removes_it(q2):
    events = watchdog(q2)
    q2.fw.var('fw_ctx_g_power_capacity', C.c_int).value = 64
    q2.loop()
    assert q2.flash.files[STATE] == b'screen:off power:running cable:out battery:64 play:stopped out:jack\n'
    q2.loop()
    assert [path for path, _ in q2.disk.written].count(STATE) == 1  # rewritten only on a change
    q2.status, q2.way = PLAYING, 1
    lit(q2)
    q2.loop(ms=5000)  # a change is written once its burst settles (system.c STATE_SETTLE_MS)
    assert q2.flash.files[STATE] == b'screen:on power:running cable:out battery:64 play:playing out:bluetooth\n'
    q2.status = PAUSED
    q2.loop()  # a change still settling: the clean end below does not wait for it
    del events[:]
    q2.fw.fn('ringnav_savequeue', C.c_int)()  # the power-off page's into_poweroff saves, hooked
    assert STATE not in q2.flash.files and events == [(9, WDT_STOP, None)]  # a clean end: nothing to report
    q2.loop()
    assert STATE not in q2.flash.files and events == [(9, WDT_STOP, None)]  # nothing fed or recorded after


# The headset (docs/internals.md#headset-controls-and-volume).

@pytest.mark.parametrize('status, after', [(PLAYING, PAUSED), (PAUSED, PAUSED), (STOPPED, STOPPED)])
def test_a_bluetooth_headset_going_pauses_what_plays_and_coming_back_resumes_nothing(q2, status, after):
    q2.var('g_bluetoothflag').value = q2.var('bt_linkstatus').value = 1
    q2.status = status
    q2.loop()
    q2.loop()
    assert q2.status == status
    q2.var('bt_linkstatus').value = 0  # the link drops
    q2.loop()
    assert q2.status == after
    q2.var('bt_linkstatus').value = 1
    q2.loop()
    q2.loop()
    assert q2.status == after and len(q2.fw.calls('stock_pause_trampoline')) == (status == PLAYING)


# Bluetooth volume (docs/internals.md#headset-controls-and-volume).

def output(q2, way):
    """Stock's config_outputchannel moving the output: mclSetOutputWay, hooked (ringnav_output)."""
    q2.fw.fn('ringnav_output', C.c_int, C.c_int)(way)


def set_volume(q2, v):
    """Stock's volume dialog: g_volume, then its save, write_int_config's PLAYSET VOLUME."""
    q2.var('g_volume').value = v
    q2.fw.fn('write_int_config', C.c_long, C.c_long, C.c_char_p, C.c_char_p)(v, b'PLAYSET', b'VOLUME')


def test_bluetooth_and_the_wire_keep_their_own_volumes_across_a_restart(q2):
    volume = lambda: q2.var('g_volume').value  # noqa: E731
    q2.var('g_maxvolume').value = 100
    set_volume(q2, 30)
    output(q2, 1)  # a headset connects
    assert q2.way == 1 and volume() == 30  # no Bluetooth level saved yet: the wired one carries over
    set_volume(q2, 55)
    assert q2.flash.get('PLAYSET', 'BT_VOLUME') is None  # held: a turn is one write, once it settles
    q2.loop(ms=3000)
    assert q2.flash.get('PLAYSET', 'VOLUME') == '30' and q2.flash.get('PLAYSET', 'BT_VOLUME') == '55'
    output(q2, 0)  # it goes
    assert volume() == 30 and q2.fw.calls('device_set_volume')[-1][1][0] == 30
    q2.power_on()
    q2.var('g_maxvolume').value = 100
    q2.var('g_volume').value = int(q2.flash.get('PLAYSET', 'VOLUME'))  # stock's boot reads the wired key
    output(q2, 1)
    assert volume() == 55 and q2.fw.calls('device_set_volume')[-1][1][0] == 55
    q2.var('g_maxvolume').value = 40  # a lower volume limit since
    output(q2, 0)
    output(q2, 1)
    assert volume() == 40


class Headset:
    """A Bluetooth headset with its own absolute volume (0-127), and stock's device_set_volume as far as it reaches
    it: both channels at the volume (Balance centred) to mclSetBtVol, hooked (ringnav_btvol)."""
    def __init__(self, q2, level):
        self.level, self.gain = level, []
        btvol = q2.fw.fn('ringnav_btvol', C.c_int, C.c_int, C.c_int)
        q2.fw.mock('btctl_transport_get_volume', lambda *_: self.level)
        q2.fw.mock('btctl_transport_set_volume', lambda v, *_: setattr(self, 'level', v) or 0)
        q2.fw.mock('device_set_volume', lambda v, *_: btvol(v, v) if q2.way == 1 else 0)
        q2.fw.mock('stock_btvol_trampoline', lambda left, right, *_: self.gain.append((left, right)) or 0)


def curve(v):
    """The headset's level for Q2 volume v on its 40 dB curve (docs/internals.md#headset-controls-and-volume)."""
    return 0 if v == 0 else max(1, round(127 * 10 ** (-40 * (100 - v) / 2000)), round(127 * v / 400))


def reads_as(level):
    """The Q2 volume a headset level reads as: the nearest on the curve, the lowest of those sharing it."""
    return min(range(101), key=lambda v: (abs(curve(v) - level), v))


@pytest.fixture
def response_level():
    """Stock's responseSetVolume level, a byte at a fixed address the poll writes: put back after."""
    saved = C.c_byte.from_address(0xa4cbb5).value
    yield
    C.c_byte.from_address(0xa4cbb5).value = saved


def test_control_device_volume_hands_the_volume_to_the_headset_and_follows_its_keys(q2, response_level):
    q2.var('g_bluetoothflag').value = q2.var('bt_linkstatus').value = q2.var('g_backlight_status').value = 1
    q2.fw.var('fw_ctx_bt_showcoding', C.c_int).value = 1  # A2DP streams: stock shows its codec
    q2.var('g_maxvolume').value = 100
    q2.way = 1
    set_volume(q2, 55)
    headset = Headset(q2, 64)
    q2.loop()
    q2.loop(ms=500)
    assert headset.level == 64 and q2.var('g_volume').value == 55  # Off: each keeps its own
    q2.build('ringnav_btrows', 'bluetooth_page', 'scroll_view_bluetooth',  # System settings → Bluetooth
             *((f'stock_{i}', 'list_item', 'scroll_view_bluetooth') for i in range(4)))
    q2.screen.click('Link Volume')
    assert q2.screen.switch('Link Volume') == 'switch_on' and q2.flash.get('Q2POD', 'BTVOLLINK') == '1'
    # the Q2's level sent, on the curve, and the Q2's own gain full
    assert headset.level == curve(55) == 17 and headset.gain[-1] == (100, 100)
    headset.level = 100  # its own keys: 100 of its 127, which reads as the Q2's 95
    q2.loop(ms=500)
    assert q2.var('g_volume').value == reads_as(100) == 95
    q2.loop(ms=3000)  # saved once it settles (config_ini.c config_settle)
    assert q2.flash.get('PLAYSET', 'BT_VOLUME') == '95'
    q2.var('g_maxvolume').value = 60
    headset.level = 127
    q2.loop(ms=500)
    # the volume limit holds, the headset pulled back to it
    assert q2.var('g_volume').value == 60 and headset.level == curve(60)
    q2.loop(ms=500)
    assert q2.var('g_volume').value == 60 and headset.level == curve(60)
