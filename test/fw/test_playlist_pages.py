"""playlist_pages.c and the On-The-Go and server-sync parts of playlists.c and catalogue.c: a playlist's own page
under OLED (Shuffle, Add Songs, Save as Playlist and Clear for On-The-Go, then the songs), On-The-Go as entries with
cd_num 100 and no header, Save as Playlist's "On-The-Go N", and the lines a server playlist's change writes to its
app's playlists.out (docs/internals.md#on-the-go, docs/server-library.md#playlists-from-the-q2)."""
import ctypes as C
import re

import pytest

from conftest import O, Files
from test_nav_hold import Editor, Table, recs, ui  # noqa: F401  (fixtures)

OTG = 100


class Pv(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view songs'.split()),
                *((k, C.c_int) for k in 'dest selected pending acts'.split()), ('act', C.c_int * 4),
                ('timer', C.c_uint), ('name', C.c_char * 128)]


class Lists(Table):
    """The playlist table with its headers too (id 0 to 99, name): New's and Save's lookups and inserts, On-The-Go's
    move and Clear, and the server's mapping (cat.serverlists: list -> app, pid; servermeta: path -> sid)."""
    def __init__(self, fw, recs, deques, titles):
        super().__init__(fw, recs, deques, titles)
        self.headers, self.servers, self.sids = {}, {}, {}

    def exec(self, db, sql, callback, ctx, *_):
        text = C.string_at(sql).decode()
        rows: list = []
        if text.startswith(('attach ', 'detach ', 'PRAGMA ')):  # stock_db_open's catalogue and wait
            return 0
        if text == 'select id from playlist where id <= 100':
            rows = [[b'%d' % i] for i in self.headers]
        elif m := re.fullmatch(r"select count\(\*\) from playlist where id <= 100 and filename='(.*)'", text):
            rows = [[b'%d' % (m.group(1) in self.headers.values())]]
        elif m := re.fullmatch(r"insert into playlist values \((\d+),'(.*?)',.*;select changes\(\)", text):
            self.headers[int(m.group(1))] = m.group(2)
            rows = [[b'1']]
        elif m := re.fullmatch(r"select count\(\*\) from playlist where id<=100 and id<>(\d+) and filename='(.*)'", text):
            rows = [[b'%d' % any(n == m.group(2) and i != int(m.group(1)) for i, n in self.headers.items())]]
        elif m := re.fullmatch(r"update playlist set name='(.*)',filename='(.*)' where id=(\d+)", text):
            self.headers[int(m.group(3))] = m.group(1)
        elif m := re.fullmatch(r'delete from playlist where id=(\d+);select changes\(\);'
                               r'delete from playlist where id>100 and cd_num=(\d+)', text):
            rows = [[b'%d' % (self.headers.pop(int(m.group(1)), None) is not None)]]
            self.rows = {i: (c, p) for i, (c, p) in self.rows.items() if c != int(m.group(2))}
        elif m := re.fullmatch(r'update playlist set cd_num=(\d+) where id>100 and cd_num=100', text):
            for i, (c, p) in self.rows.items():
                if c == OTG:
                    self.rows[i] = (int(m.group(1)), p)
        elif text == 'delete from playlist where id>100 and cd_num=100':
            self.rows = {i: (c, p) for i, (c, p) in self.rows.items() if c != OTG}
        elif text == 'select list,app,pid from cat.serverlists':
            rows = [[b'%d' % n, a.encode(), p.encode()] for n, (a, p) in self.servers.items()]
        elif m := re.fullmatch(r'select app,pid from cat.serverlists where list=(\d+)', text):
            n = int(m.group(1))
            rows = [[a.encode(), p.encode()] for a, p in [self.servers[n]]] if n in self.servers else []
        elif text.startswith('select ifnull((select sid from cat.servermeta'):
            dest = int(re.search(r'p.cd_num=(\d+)', text).group(1))
            rows = [[self.sids.get(p, '').encode() or None] for i, (c, p) in sorted(self.rows.items()) if c == dest]
        else:
            return super().exec(db, sql, callback, ctx)
        self.log.append(text)
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback) if callback else None
        for row in rows:
            if call and call(ctx, len(row), (C.c_char_p * len(row))(*row), None):
                return 4
        return 0


@pytest.fixture
def pages(fw, ui, recs):  # noqa: F811
    """Road Trip (playlist 3: One, Two, Three) and an On-The-Go of Five."""
    e = Editor(fw, ui, recs)
    e.table.__class__ = Lists
    e.table.headers, e.table.servers, e.table.sids = {1: 'Gym', 3: 'Road Trip'}, {}, {}
    fw.mock('sqlite3_exec', e.table.exec)
    e.table.put(OTG, '/mnt/mmc/e.flac')
    return e


def open_page(fw, dest, name):
    fw.fn('playlist_page_open', None, C.c_int, C.c_char_p)(dest, name)
    return fw.var('playlist_pages__pv', Pv)


def test_a_playlist_s_page_is_shuffle_and_add_songs_then_its_songs(fw, ui, pages):
    pv = open_page(fw, 3, b'Road Trip')
    assert ui.nodes[ui.top()].name == 'playlistsongs_page' and pv.dest == 3
    assert ui.rows(pv.view) == ['Shuffle', 'Add Songs', 'One', 'Two', 'Three']


def test_on_the_go_adds_save_and_clear_and_empty_it_offers_add_songs_alone(fw, ui, pages):
    pv = open_page(fw, OTG, b'On-The-Go')
    assert ui.rows(pv.view) == ['Shuffle', 'Add Songs', 'Save as Playlist', 'Clear', 'Five']
    ui.close(ui.top())
    pages.table.rows.clear()
    pv = open_page(fw, OTG, b'On-The-Go')
    assert ui.rows(pv.view) == ['Add Songs']


def test_a_song_plays_its_list_as_stock_plays_a_playlist(fw, ui, pages):
    played = []
    ui.routes['playing_page'] = lambda ctx: played.append(tuple((C.c_int * 2).from_address(ctx + C.sizeof(C.c_void_p))))
    pv = open_page(fw, 3, b'Road Trip')
    ui.click(ui.find(pv.view, 'Two'))
    ui.due('pv_run')
    assert played == [(1, 0xff13)]  # play_folder's {dq, idx, cls, mode}: from Two, a playlist's own list
    assert fw.var('fw_ctx_g_local_classinfo_save', C.c_int * 2)[1] == 3  # the playlist the class plays


def test_save_as_playlist_names_it_on_the_go_n_and_moves_the_songs(fw, ui, pages):
    pages.table.headers[2] = 'On-The-Go 1'  # taken: the next
    pv = open_page(fw, OTG, b'On-The-Go')
    ui.click(ui.find(pv.view, 'Save as Playlist'))
    ui.due('pv_run')
    assert pages.table.headers[0] == 'On-The-Go 2' and pages.table.songs(0) == ['Five'] and not pages.table.songs(OTG)
    assert ui.toasts[-1] == 'Saved as On-The-Go 2' and ui.went[-1] == 'back'


@pytest.mark.parametrize('answer, left', [(10, []), (11, ['Five'])])
def test_clear_asks_first(fw, ui, pages, answer, left):
    ui.answers['dialog/confirminfo_dialog'] = answer
    pv = open_page(fw, OTG, b'On-The-Go')
    ui.click(ui.find(pv.view, 'Clear'))
    ui.due('pv_run')
    assert pages.table.songs(OTG) == left
    assert (ui.toasts[-1:] == ['On-The-Go cleared']) == (answer == 10)


def test_remove_takes_the_held_song_out(fw, ui, pages):
    pv = open_page(fw, 3, b'Road Trip')
    view = pv.view
    ui.nodes[view].props['_ringnav_index'] = 3  # Two, under Shuffle and Add Songs
    fw.var('playlist_pages__pv_held', C.c_void_p).value = pages.deques[pv.songs][1]  # the hold's song
    fw.fn('playlist_pages__pv_remove', None, C.c_int)(0)
    assert pages.table.songs(3) == ['One', 'Three'] and ui.toasts[-1] == 'Removed'
    assert ui.rows(pv.view) == ['Shuffle', 'Add Songs', 'One', 'Three']


def server(fw, files, pages):
    fw.fn('pod_settings', None)()  # read, so the source stays as set
    fw.var('catalogue_source', C.c_int).value = 1
    pages.table.servers[3] = ('subsonic', 'pl7')
    pages.table.sids = {'/mnt/mmc/a.flac': 's1', '/mnt/mmc/c.flac': 's%3'}
    return lambda: files.files.get('/mnt/mmc/.subsonic/playlists.out', b'')


def test_a_server_playlist_s_songs_go_to_its_app_in_their_order(fw, ui, pages):
    out = server(fw, Files(fw), pages)
    pv = open_page(fw, 3, b'Road Trip')
    ui.nodes[pv.view].props['_ringnav_index'] = 3
    fw.var('playlist_pages__pv_held', C.c_void_p).value = pages.deques[pv.songs][1]
    fw.fn('playlist_pages__pv_remove', None, C.c_int)(0)
    assert out() == b'set\tpl7\ts1\ts%253\n'  # Two's gone; a song with no sid (none here) is left out


def test_a_rename_and_a_delete_of_a_server_playlist_go_to_its_app(fw, ui, pages):
    out = server(fw, Files(fw), pages)
    assert fw.fn('playlist_rename', C.c_int, C.c_int, C.c_char_p)(3, b'Road\tTrip') == 1
    assert fw.fn('playlist_delete', C.c_int, C.c_int)(3) == 1
    assert out() == b'name\tpl7\tRoad%09Trip\ndelete\tpl7\n'


def test_a_local_playlist_or_local_only_writes_nothing(fw, ui, pages):
    files = Files(fw)
    server(fw, files, pages)
    fw.var('catalogue_source', C.c_int).value = 0
    fw.fn('playlist_rename', C.c_int, C.c_int, C.c_char_p)(3, b'New')
    fw.var('catalogue_source', C.c_int).value = 1
    fw.fn('playlist_rename', C.c_int, C.c_int, C.c_char_p)(1, b'Gym 2')  # not a server playlist
    assert not files.files


def test_a_server_playlist_s_cover_is_its_app_s_pl_file_once_it_is_there(fw, ui, pages):
    files = Files(fw)
    server(fw, files, pages)
    cover = fw.fn('catalogue_playlist_cover', C.c_int, C.c_int, C.c_char_p)
    url = C.create_string_buffer(600)
    fw.fn('catalogue_playlist_covers', None)()
    assert cover(3, url) == 0  # none yet
    files.files['/mnt/mmc/.subsonic/covers/pl-pl7.jpg'] = b'\xff\xd8jpeg'
    assert cover(3, url) == 1 and url.value == b'file:///mnt/mmc/.subsonic/covers/pl-pl7.jpg'
    assert cover(1, url) == 0  # a local playlist


def test_a_rename_to_a_taken_name_is_refused(fw, ui, pages):
    assert fw.fn('playlist_rename', C.c_int, C.c_int, C.c_char_p)(3, b'Gym') == -2


def test_add_songs_row_opens_add_songs_for_the_playlist(fw, ui, pages):
    pv = open_page(fw, 3, b'Road Trip')
    ui.click(ui.find(pv.view, 'Add Songs'))
    ui.due('pv_run')
    assert ui.nodes[ui.top()].name == 'addsongs_page' and 'Add to Road Trip' in [n.text for n in ui.nodes.values()]
