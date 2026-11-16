"""storage.c: Settings > Storage from a card app's storage.tsv and the card's statfs (the rows, their sizes, the used
line), its Downloads page (Limit and the items), and the requests it appends to storage.out: a new limit, an item
held to remove it (docs/internals.md#storage)."""
import ctypes as C

import pytest

from conftest import O, Files
from test_battery import MOUNTS, SEL, Page
from test_peq_page import Canvas

APP = '/mnt/mmc/.subsonic'
TSV = ('#Q2STORE/1\tlimit=16000\tdownloads=8200000000\tcache=1600000000\n'
       'i\tpick\talbum:al1\t1200000000\talbum\talbum:al1\tCaracal (Deluxe)\n'
       'i\tauto\tfav:all\t2100000000\t\t\tFavorites\n'
       'i\tpick\tplaylist:p1\t3400000000\tplaylist\tplaylist:p1\tRoad%09Trip\n')
MIB = 1 << 20


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
