"""patch/playlist_sort.c: the Playlists list's order, Playback > Library > Sorting's Playlists picker
(docs/ipod.md#settings). Every list of the playlists is getAllPlayList's header query through navigation.c
library_query: by name in its SQL, either way, and for Recently Added by date once the headers are loaded, the
later of the header's time_create and its .m3u's modification time on the card, the undated last."""
import ctypes as C

import pytest

from conftest import O, Rows
from test_nav_hold import field, recs  # noqa: F401 (recs: the fixture)

HEADERS = b'select * from playlist where id <= 100'
BY_NAME = HEADERS + b' order by name collate nocase, id'
REC_TITLE, REC_DISC = O['REC_TITLE'], O['REC_DISC']
ROW = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.POINTER(C.c_char_p))
COLUMNS = (b'id', b'filename', b'fileurl', b'album', b'songer', b'style', b'name', b'composer', b'albumsonger', b'year',
           b'track', b'cd_num', b'time', b'time_end', b'time_create', b'Initial', b'bitdepth', b'samprate')


class Config:
    """config.ini: a key's value, or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


class Headers:
    """getAllPlayList as stock runs it: its query (the text library_query hands the trampoline, logged in sql) calls
    the row callback once a header, with the playlist table's columns, and stock's callback puts a record of it on
    the staging deque (tools_pdeq_directory). headers: (cd_num, name, time_create) in the order the SQL gives them.
    The card's playlist files: path to mtime, through __xstat (o32: st_mtime word 16); stats logs each path asked."""
    def __init__(self, fw, recs, headers, files=None):
        self.fw, self.recs, self.headers, self.files = fw, recs, headers, files or {}
        self.rows, self.sql, self.stats = Rows(fw), [], []
        self.staging = fw.var('fw_ctx_tools_pdeq_directory', C.c_uint)
        self.staging.value = self.rows.rows()
        self.stock_row = ROW(self.stock)
        fw.mock('stock_dbquery_trampoline', self.query)
        fw.mock('fw___xstat', self.stat)
        self.run = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)

    def stock(self, ctx, n, v, c):
        cd_num, name = int(v[11]), v[6].decode()
        self.rows.push(self.staging.value, self.recs(kind=4, disc=cd_num, title=name))
        return 0

    def query(self, db, sql, row, sort, *_):
        self.sql.append(C.string_at(sql))
        call = ROW(row)
        for cd_num, name, when in self.headers:
            values = {b'id': cd_num, b'name': name, b'filename': name, b'cd_num': cd_num, b'time_create': when}
            v = (C.c_char_p * len(COLUMNS))(*(str(values.get(k, '')).encode() for k in COLUMNS))
            c = (C.c_char_p * len(COLUMNS))(*COLUMNS)
            call(None, len(COLUMNS), v, c)
        return len(self.headers)

    def stat(self, version, path, info, *_):
        path = C.string_at(path).decode()
        self.stats.append(path)
        if path not in self.files:
            return -1
        (C.c_uint * 36).from_address(info)[16] = self.files[path]
        return 0

    def load(self):
        """The headers loaded as getAllPlayList loads them: their names in the order the staging deque holds."""
        self.rows.free(self.staging.value)
        assert self.run(b'/mnt/data/database.db', HEADERS, C.cast(self.stock_row, C.c_void_p), 0) == len(self.headers)
        return [field(r, REC_TITLE) for r in self.rows[self.staging.value]]


M3U = '/mnt/mmc/_explaylist_data/'
# (cd_num, name, time_create) as the name order gives them; Car Mix, Mine and Old Rock have their .m3u on the card
HEADERS_BY_NAME = [(4, 'Car Mix', 0), (0, 'Gym', 1700000300), (2, 'Mine', 1700000100), (1, 'Old Rock', 0),
                   (3, 'Unsorted', 0)]
CARD = {M3U + 'Car Mix.m3u': 1700000500, M3U + 'Mine.m3u8': 1700000050, M3U + 'Old Rock.m3u': 1700000200}


def test_name_is_the_default_and_its_sql_is_unchanged(fw, recs):
    """Nothing chosen: A to Z by name, the query stock's with the ORDER BY it always had, no file statted."""
    Config(fw)
    h = Headers(fw, recs, HEADERS_BY_NAME, CARD)
    assert h.load() == ['Car Mix', 'Gym', 'Mine', 'Old Rock', 'Unsorted']
    assert h.sql == [BY_NAME] and h.stats == []


def test_name_z_to_a_is_the_sql_s_order_reversed(fw, recs):
    Config(fw, PLAYLISTORDER=b'0', PLAYLISTDOWN=b'1')
    h = Headers(fw, recs, HEADERS_BY_NAME[::-1], CARD)
    h.load()
    assert h.sql == [HEADERS + b' order by name collate nocase desc, id'] and h.stats == []


def test_recently_added_puts_the_newest_first_by_file_or_header_date_the_undated_last(fw, recs):
    """Recently Added, newest first: Car Mix's file (…500), Gym's header (…300), Old Rock's file (…200), Mine's header
    (…100, later than its .m3u8), then Unsorted, which has neither, last. One stat per playlist, .m3u then .m3u8."""
    Config(fw, PLAYLISTORDER=b'1', PLAYLISTDOWN=b'1')
    h = Headers(fw, recs, HEADERS_BY_NAME, CARD)
    assert h.load() == ['Car Mix', 'Gym', 'Old Rock', 'Mine', 'Unsorted']
    assert h.sql == [BY_NAME]  # the name order under it: its ties' order
    assert h.stats == [M3U + n for n in ('Car Mix.m3u', 'Gym.m3u', 'Gym.m3u8', 'Mine.m3u', 'Mine.m3u8',
                                          'Old Rock.m3u', 'Unsorted.m3u', 'Unsorted.m3u8')]


def test_recently_added_oldest_first_keeps_the_undated_last_and_ties_by_name(fw, recs):
    Config(fw, PLAYLISTORDER=b'1', PLAYLISTDOWN=b'0')
    headers = HEADERS_BY_NAME + [(5, 'Zed', 1700000200)]  # the same date as Old Rock's file: by name after it
    h = Headers(fw, recs, headers, CARD)
    assert h.load() == ['Mine', 'Old Rock', 'Zed', 'Gym', 'Car Mix', 'Unsorted']


def test_a_name_fat_refuses_is_looked_for_as_the_card_holds_it(fw, recs):
    """A playlist named with a character FAT refuses (an export writes it as _): its file found as the card's."""
    Config(fw, PLAYLISTORDER=b'1', PLAYLISTDOWN=b'1')
    h = Headers(fw, recs, [(0, 'A', 0), (1, 'AC/DC', 0)], {M3U + 'AC_DC.m3u': 1700000000})
    assert h.load() == ['AC/DC', 'A']


def test_the_headers_off_the_ui_thread_keep_the_name_order(fw, recs):
    """Another thread's load (none reads the headers today) is left in the SQL's order: no stat, no reorder."""
    Config(fw, PLAYLISTORDER=b'1', PLAYLISTDOWN=b'1')
    fw.mock('fw_pthread_self', lambda *_: 7)  # not ui_thread
    h = Headers(fw, recs, HEADERS_BY_NAME, CARD)
    assert h.load() == ['Car Mix', 'Gym', 'Mine', 'Old Rock', 'Unsorted'] and h.stats == []


def test_rows_already_staged_stay_where_they_are(fw, recs):
    """Only the records this load added move: what the staging deque held before stays first, in its order."""
    Config(fw, PLAYLISTORDER=b'1', PLAYLISTDOWN=b'1')
    h = Headers(fw, recs, HEADERS_BY_NAME, CARD)
    before = recs(kind=4, disc=9, title='Kept')
    h.rows.push(h.staging.value, before)
    h.run(b'/mnt/data/database.db', HEADERS, C.cast(h.stock_row, C.c_void_p), 0)
    assert [field(r, REC_TITLE) for r in h.rows[h.staging.value]] == ['Kept', 'Car Mix', 'Gym', 'Old Rock', 'Mine',
                                                                      'Unsorted']


@pytest.mark.parametrize('source, function', [('playlists', 'int ringnav_playlist_create('),
                                              ('catalogue', 'static int pl_create(')])
def test_a_playlist_made_on_the_q2_is_stamped_with_the_time(source, function):
    """ringnav_playlist_create (New Playlist, On-The-Go's save, an import) and a server playlist's first appearance
    (catalogue.c pl_create) write the header's time_create as SQLite's clock reads it, not 0, so a playlist with no
    file on the card is still dated."""
    import pathlib
    text = (pathlib.Path(__file__).resolve().parents[2]/'patch'/f'{source}.c').read_text()
    body = text[text.index(function):]
    body = body[:body.index('\n}\n')]
    assert 'insert into playlist values' in body and "cast(strftime('%%s','now') as integer)" in body
