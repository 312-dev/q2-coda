"""storage.c: Settings > Storage from a card app's storage.tsv and the card's statfs (the rows, their sizes, the used
line), its Downloads page (Limit and the items), and the requests it appends to storage.out: a new limit, an item
held to remove it (docs/internals.md#storage)."""
import ctypes as C
import struct

import pytest

from conftest import O, Files
from test_battery import MOUNTS, PAGE, SEL
from test_peq_page import Canvas

APP = '/mnt/mmc/.subsonic'
TSV = ('#Q2STORE/1\tlimit=16000\tdownloads=8200000000\tcache=1600000000\n'
       'i\tpick\talbum:al1\t1200000000\talbum\talbum:al1\tCaracal (Deluxe)\n'
       'i\tauto\tfav:all\t2100000000\t\t\tFavorites\n'
       'i\tpick\tplaylist:p1\t3400000000\tplaylist\tplaylist:p1\tRoad%09Trip\n')
MIB = 1 << 20
EVT_CLICK = O['EVT_CLICK']


class Page:
    """A list page on stubs: its rows' labels, a row's tap and the toasts (the stubs Battery History's list page had,
    test_battery's before its graph page)."""
    def __init__(self, fw):
        self.fw, self.texts, self.children, self.items, self.toasts, self.boxes = fw, {}, {}, [], [], {}
        self.buffers, self.lists = [], []
        fw.mock('window_create', lambda *_: PAGE)
        self.top = PAGE  # the window on top: a toast's, while its modal loop runs
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('list_view_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h))
        fw.mock('scroll_view_create', self.scroll)
        fw.mock('list_item_create', lambda *_: self.items.append(self.widget()) or self.items[-1])
        fw.mock('hscroll_label_create', self.label)
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)  # an empty page's note reads its caption back (page_empty)
        self.props = {}
        fw.mock('widget_get_prop_int', self.prop)
        fw.mock('widget_set_prop_int', lambda w, key, v, *_: self.props.__setitem__((w, C.string_at(key)), C.c_int(v).value))
        fw.mock('widget_move_resize', lambda w, x, y, width, h, *_: self.boxes.__setitem__(w, (x, y, width, h)))
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('navigator_window_is_exist', lambda *_: 0)
        fw.mock('fw_statfs', self.statfs)
        self.free = 64 << 20

    def widget(self, x=0, y=0, w=0, h=0):
        self.buffers.append(C.create_string_buffer(0x400))
        p = C.addressof(self.buffers[-1])
        struct.pack_into('<iiii', self.buffers[-1], 0, x, y, w, h)
        self.boxes[p] = (x, y, w, h)
        return p

    def scroll(self, parent, x, y, w, h, *_):
        self.items.clear()
        p = self.widget(x, y, w, h)
        self.lists.append(p)
        C.c_void_p.from_address(p + O['W_PARENT']).value = parent
        return p

    def get_text(self, w, out, n, *_):
        data = self.texts.get(w, '').encode()[:n - 1] + b'\0'
        C.memmove(out, data, len(data))
        return 0

    def label(self, parent, *_):
        p = self.widget()
        self.children.setdefault(parent, []).append(p)
        return p

    def prop(self, w, key, default, *_):
        name = C.string_at(key)
        return 290 if name == b'h' else self.props.get((w, name), C.c_int(default).value)

    @property
    def view(self):
        """The list's scroll view, the latest built."""
        return self.lists[-1]

    def statfs(self, path, out, *_):
        C.c_uint.from_address(out + 4).value, C.c_uint.from_address(out + 28).value = 1, self.free
        return 0

    @property
    def captions(self):
        return [self.texts.get(self.children[item][0]) for item in self.items]

    def tap(self, row):
        (fn, ctx), = [(a[2], a[3]) for _, a in self.fw.calls('widget_on') if a[0] == self.items[row] and a[1] == EVT_CLICK]
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)


class Storage(Page):
    """The pages on Page's stubs: a card with .subsonic's q2app (storage=1) and its file, 64 GB (61,035 MiB) of
    which 25.6 GB (24,414 MiB) are free; each page its own window."""
    def __init__(self, fw, tsv=TSV):
        said = []
        super().__init__(fw)
        self.toasts = said
        fw.mock('navigator_to_with_context', lambda name, info, *_: said.append(C.string_at(info + 8).decode()) or 0)
        self.files = Files(fw, {'/proc/mounts': MOUNTS, f'{APP}/q2app': b'name=Subsonic\nstorage=1\n'})
        if tsv is not None:
            self.files.files[f'{APP}/storage.tsv'] = tsv.encode()
        self.pages = []
        fw.mock('window_create', self.window)
        self.names, self.keep = [], []
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
        self.views = []
        fw.mock('widget_factory_create_widget', self.made)
        fw.mock('fw_access', lambda path, *_: -1)  # the app isn't running: addon_wake launches it

    def window(self, *_):
        self.pages.append(self.widget(0, 0, 375, 320))
        return self.pages[-1]

    def opendir(self, path, *_):
        self.names[:] = [b'.subsonic', b'Music'] if C.string_at(path) == b'/mnt/mmc' else []
        return 0x77 if self.names else 0

    def readdir(self, d, *_):
        if not self.names:
            return 0
        name = self.names.pop(0)
        ent = C.create_string_buffer(268)
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.keep.append(ent)
        return C.addressof(ent)

    def statfs(self, path, out, *_):
        words = C.c_uint * 8
        w = words.from_address(out)
        w[1], w[3], w[7] = MIB, 61035, 24414
        return 0

    def open(self):
        assert self.fw.fn('storage_open', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0

    def made(self, factory, kind, parent, x, y, *_):
        """The header's and the footer's views, in order."""
        self.views.append(self.widget(x, y))
        return self.views[-1]

    def paint(self, w):
        canvas = Canvas(self.fw)
        self.fw.fn('storage_paint', None, C.c_void_p, C.c_void_p)(w, canvas.p)
        return [t[0] for t in canvas.texts]

    def out(self):
        return self.files.files.get(f'{APP}/storage.out', b'').decode()


def rows(st, fw):
    """Each row of the latest page painted: the texts drawn on it, its value."""
    for item in st.items:
        C.c_void_p.from_address(item + O['W_PARENT']).value = st.view
    return [st.paint(item) for item in st.items]


def test_the_page_shows_the_card_s_use_from_statfs_and_the_app_s_file(fw):
    st = Storage(fw)
    st.open()
    assert st.captions == ['Your Music', 'Downloads', 'Stream Cache', 'Free']
    # 64.0 GB of the card, 25.6 GB free; Your Music is the used less the app's 8.2 GB and 1.6 GB
    assert rows(st, fw) == [['28.6 GB'], ['8.2 GB'], ['1.6 GB'], ['25.6 GB']]
    head, foot = st.views
    assert st.paint(head) == ['38.4 GB of 64 GB used']
    assert st.paint(foot) == ['Your Music is never changed by Sync.', 'Downloads stay under the limit you set.']


def test_without_the_app_s_file_downloads_and_the_cache_read_nothing(fw):
    st = Storage(fw, tsv=None)
    st.open()
    assert rows(st, fw) == [['38.4 GB'], ['0 MB'], ['0 MB'], ['25.6 GB']]


def test_downloads_lists_the_limit_and_each_item_its_size_escapes_undone(fw):
    st = Storage(fw)
    st.open()
    st.tap(1)
    assert st.captions == ['Limit', 'Caracal (Deluxe)', 'Favorites', 'Road\tTrip']
    assert rows(st, fw) == [['16 GB'], ['1.2 GB'], ['2.1 GB'], ['3.4 GB']]
    # its footer promises nothing the app does not do: no auto items refill yet
    assert st.paint(st.views[-1]) == ['Hold Play/Pause on an item to remove it.']


def test_a_limit_picked_is_asked_of_the_app_and_shown_at_once(fw):
    st = Storage(fw)
    st.open()
    st.tap(1)
    fw.fn('storage__st_limit_picked', None, C.c_int)(2)  # 8 GB
    assert st.out() == '\nlimit\t8000\n'
    assert rows(st, fw)[0] == ['8 GB']


def test_play_pause_held_on_an_item_asks_the_app_to_remove_it(fw):
    st = Storage(fw)
    st.open()
    st.tap(1)
    dl = st.top = st.pages[1]  # the Downloads page, on top
    hold = fw.fn('storage_hold', C.c_int, C.c_void_p)
    st.props[(st.view, SEL)] = 0  # Limit: not an item
    assert hold(dl) == 0 and st.out() == ''
    st.props[(st.view, SEL)] = 3
    assert hold(dl) == 1
    assert st.out() == '\nremove\tplaylist:p1\n'
    assert st.captions == ['Limit', 'Caracal (Deluxe)', 'Favorites'] and st.toasts[-1] == 'Removed from Sync'
    assert hold(st.pages[0]) == 0  # the Storage page itself holds nothing


@pytest.mark.parametrize('head', ['#Q2STORE/2\tlimit=1\n', 'garbage'])
def test_a_file_of_another_version_is_read_as_none(fw, head):
    st = Storage(fw, tsv=head + 'i\tpick\talbum:a\t1\talbum\talbum:a\tA\n')
    st.open()
    st.tap(1)
    assert st.captions == ['Limit']
