"""The server library (patch/catalogue.c, docs/server-library.md): a card app's catalogue imported
into a real SQLite file, the duplicate rule's three layers, the match again after the card's library
changes, availability, the union the Library reads, the offline queue and shuffle's filter, the
play hand-off, and the ratings files."""
import ctypes as C
import ctypes.util
import sqlite3
import struct

import pytest

from conftest import FNV_SEED, O, Files, Rows, in_fork, stock_start_with
from test_nav_library_pages import EVT_CLICK, song, ui  # noqa: F401  the Now Playing heart's fixtures
from test_nav_painters import low, scratch, st  # noqa: F401  navigation.c's st by field; strings under 4 GB
from test_nav_hold import recs  # noqa: F401  records whose fields all read as strings

APP = 'subsonic'
CARD_DB = b'/mnt/mmc/.q2pod/'
TSV = f'/mnt/mmc/.{APP}/catalogue.tsv'
DONE = f'/tmp/q2-{APP}.catalogue.done'
STATE = f'/tmp/q2-{APP}.state'
AVAIL = f'/mnt/mmc/.{APP}/catalogue.avail'
SONGTABLE = ('create table songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename, fileurl, album, songer, '
             'style, name, composer, albumsonger, year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, '
             'time_end INTEGER, time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)')
PLAYLIST = SONGTABLE.replace('songtable', 'playlist').replace(' AUTOINCREMENT', '')
MYFAV = SONGTABLE.replace('songtable', 'myfav')


def esc(s):
    return s.replace('%', '%25').replace('\t', '%09').replace('\n', '%0A').replace('\r', '%0D')


def song_row(sid, title, artist, album, track, seconds, size=0, mbid='', suffix='flac', local='', album_id='al1',
             album_artist=''):
    return 's\t' + '\t'.join(esc(str(v)) for v in (sid, title, artist, album_artist, album, album_id, 2001, 1,
                                                    track, seconds, 'Rock', suffix, local, size, mbid)) + '\n'


class CatApp(C.Structure):
    _fields_ = [('id', C.c_char * 16), ('ratings', C.c_int), ('dl', C.c_char * 32), ('seq', C.c_char * 24),
                ('avail_at', C.c_long), ('down', C.c_int), ('play', C.c_uint)]


class CatSet(C.Structure):
    _fields_ = [('h', C.c_void_p), ('n', C.c_uint)]


class CatIx(C.Structure):
    _fields_ = [('e', C.c_void_p), ('n', C.c_uint), ('str', C.c_void_p), ('len', C.c_uint), ('cap', C.c_uint),
                ('by_sid', C.c_void_p), ('cards', CatSet * 3), ('servers', CatSet * 3), ('apps', C.c_uint)]


class CatHead(C.Structure):
    """catalogue.c's state up to its index (cat.ix): a prefix of the struct, so read with in_dll, not fw.var."""
    _fields_ = [('app', CatApp * 4), ('napps', C.c_int), ('scanned', C.c_uint), ('polled', C.c_uint), ('online', C.c_int),
                ('thread', C.c_ulong), ('running', C.c_int), ('done', C.c_int), ('job_app', C.c_int),
                ('job_seq', C.c_char * 24), ('job_why', C.c_char * 96), ('job_rows', C.c_uint), ('job_ix', CatIx),
                ('gen', C.c_uint), ('pending', C.c_int), ('away', C.c_int), ('stop', C.c_int), ('ix', CatIx)]


def cat_head(fw):
    return CatHead.in_dll(fw.lib, 'catalogue__cat')


def catalogue(seq, rows):
    return (f'#Q2CAT/1\tseq={seq}\taccount=0123abcd\trows={len(rows)}\n' + ''.join(rows)).encode()


def listen_key(path, cue=0):
    h = FNV_SEED
    for c in path.encode():
        h = (h ^ c) * 16777619 & 0xffffffff
    h = h * 16777619 & 0xffffffff
    assert not cue
    return h or 1


class Rig:
    """A card with one catalogue app, stock's database.db and the server library's file as real SQLite
    files in tmp_path, the card's files (q2app, catalogue, state) in a Files dict, the job's thread run at
    once, and card files' sizes, lengths and tags as the test gives them."""
    def __init__(self, fw, tmp_path, online=False):
        self.fw, self.tmp = fw, tmp_path
        self.now, self.online, self.toasts, self.played, self.keep = 100000, online, [], [], []
        self.source = 0  # an older build's LIBRARYSOURCE as saved: never read, the server's songs join anyway
        self.card = {}  # a card file's path: (size, seconds, bytes at its start)
        self.lib = C.CDLL(ctypes.util.find_library('sqlite3'))
        self.lib.sqlite3_open.argtypes = [C.c_char_p, C.c_void_p]
        self.lib.sqlite3_exec.argtypes = [C.c_void_p] * 5
        self.lib.sqlite3_close.argtypes = [C.c_void_p]
        self.sqls = []
        self.files = Files(fw, {
            f'/mnt/mmc/.{APP}/q2app': b'name=Subsonic\ncatalogue=1\nratings=2\n',  # likes and stars apart
            f'/mnt/mmc/.{APP}/credentials': b'{}',  # signed in: its catalogue is in the Library
            '/proc/mounts': b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'})
        db = sqlite3.connect(tmp_path/'database.db')
        db.executescript(f'{SONGTABLE};{PLAYLIST};{MYFAV}')
        db.commit()
        db.close()
        fw.var('g_backlight_status', C.c_ubyte).value = 1  # lit: the poll runs (dark, it waits for an index)
        fw.mock('sqlite3_open', self.open)
        fw.mock('sqlite3_exec', self.exec)
        fw.mock('sqlite3_close', lambda db, *_: self.lib.sqlite3_close(db))
        fw.mock('fw_access', self.access)
        fw.mock('fw_rename', self.rename)
        fw.mock('fw_unlink', self.unlink)
        self.names = []
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: C.CFUNCTYPE(C.c_void_p, C.c_void_p)(fn)(arg) and 0)
        fw.mock('tk_str_start_with', stock_start_with)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_time', lambda *_: 1700000000)
        fw.mock('networkGetConnectStatus', lambda *_: 3 if self.online else 0)
        fw.mock('toolsReadConfig', self.config)
        fw.mock('navigator_to_with_context', self.navigate)
        fw.mock('ringnav_statx', self.statx)
        self.opened: dict[int, str] = {}
        self.length = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda p: self.card[self.opened[p]][1])
        fw.mock('taglib_file_new', self.taglib_open)
        fw.mock('taglib_file_audioproperties', lambda f, *_: f)
        self.rows = OwnRows(fw)


    def navigate(self, name, info, *_):
        """A toast's dialog, or play_folder's Now Playing with its queue and start."""
        if C.string_at(name) == b'playing_page':
            q, at = C.c_void_p.from_address(info).value, C.c_int.from_address(info + 8).value
            self.played.append(([C.string_at(C.c_void_p.from_address(r + O['REC_PATH']).value or 0)
                                 for r in self.rows[q]], at))
        else:
            self.toasts.append(C.string_at(info + 8))
        return 0

    # stock's SQLite on the real files
    def real(self, sql):
        return sql.replace(CARD_DB, str(self.tmp).encode() + b'/')

    def open(self, name, db, *_):
        name = C.string_at(name)
        assert name == b'/mnt/data/database.db', name
        return self.lib.sqlite3_open(str(self.tmp/'database.db').encode(), db)

    def exec(self, db, sql, cb, ctx, err, *_):
        sql = C.string_at(sql)
        self.sqls.append(sql)
        fixed = C.create_string_buffer(self.real(sql))
        return self.lib.sqlite3_exec(db, fixed, cb, ctx, err)

    def host(self, path):
        # the catalogue's database files, on disk for sqlite; card_store.c's records there stay in the Files dict
        return self.tmp/path[len(CARD_DB):].decode() if path.startswith(CARD_DB + b'catalogue') else None

    def access(self, path, mode, *_):
        path = C.string_at(path)
        real = self.host(path)
        return 0 if (real.exists() if real else path.decode() in self.files.files or path.decode() in self.card) else -1

    def rename(self, old, new, *_):
        old, new = C.string_at(old), C.string_at(new)
        if self.host(old):
            self.host(old).replace(self.host(new))
            return 0
        self.files.files[new.decode()] = self.files.files.pop(old.decode())
        return 0

    def unlink(self, path, *_):
        path = C.string_at(path)
        if self.host(path):
            return 0 if self.host(path).unlink(missing_ok=True) is None else -1
        return 0 if self.files.files.pop(path.decode(), None) is not None else -1

    def opendir(self, path, *_):
        self.names = [f'.{APP}'.encode(), b'Music'] if C.string_at(path) == b'/mnt/mmc' else []
        return 0x77 if self.names else 0

    def readdir(self, d, *_):
        if not self.names:
            return 0
        name = self.names.pop(0)
        ent = C.create_string_buffer(268)
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.keep.append(ent)
        return C.addressof(ent)

    def config(self, path, section, key, out, default, *_):
        v = str(self.source).encode() if C.string_at(key) == b'LIBRARYSOURCE' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)

    def statx(self, dirfd, path, flags, mask, out, *_):
        path = C.string_at(path).decode()
        if path not in self.card:
            return 2
        sx = (C.c_uint * 64).from_address(out)
        sx[7], sx[10] = 0o100644, self.card[path][0]
        return 0

    def taglib_open(self, path, kind, *_):
        path = C.string_at(path).decode()
        if self.card.get(path, (0, None))[1] is None:
            return 0
        vtable = (C.c_void_p * 4)(0, 0, 0, C.cast(self.length, C.c_void_p).value)
        props = C.c_void_p(C.addressof(vtable))
        self.keep += [vtable, props]
        self.opened[C.addressof(props)] = path
        return C.addressof(props)

    # the card
    def card_song(self, path, title, artist, album, track, seconds, size, head=b''):
        """A card file in songtable and on the card: its size, length and first bytes (its tags)."""
        self.card[path] = (size, seconds, head)
        self.files.files[path] = head
        db = sqlite3.connect(self.tmp/'database.db')
        db.execute('insert into songtable(filename,fileurl,album,songer,name,albumsonger,track,time) '
                   'values (?,?,?,?,?,?,?,0)', (path.rsplit('/', 1)[1], path, album, artist, title, '', track))
        db.commit()
        db.close()

    def write(self, seq, rows):
        self.files.files[TSV] = catalogue(seq, rows)

    def sign_out(self, n=2):
        """The app signed out, as q2-subsonic's Sign Out leaves the card: no credentials, no catalogue; n passes."""
        self.files.files.pop(f'/mnt/mmc/.{APP}/credentials', None)
        self.files.files.pop(TSV, None)
        self.poll(n)

    def poll(self, n=2):
        """n passes of catalogue_poll a second apart: a job starts in one and is taken in the next."""
        for _ in range(n):
            self.now += 1100
            self.fw.fn('catalogue_poll', None)()

    def query(self, sql):
        db = sqlite3.connect(self.tmp/'database.db')
        db.execute(f"attach '{self.tmp}/catalogue.db' as cat")
        rows = db.execute(sql).fetchall()
        db.close()
        return rows

    def shown(self):
        return sorted(r[0] for r in self.query('select name from cat.servertable'))

    def record(self, path):
        r = C.create_string_buffer(O['REC_SIZE'] + 8)
        s = C.create_string_buffer(path.encode())
        self.keep += [r, s]
        struct.pack_into('<Q', r, O['REC_PATH'], C.addressof(s))
        struct.pack_into('<i', r, O['REC_TYPE'], 8)
        return C.addressof(r)

    def unavailable(self, path):
        return self.fw.fn('catalogue_unavailable', C.c_int, C.c_void_p)(self.record(path))


class OwnRows(Rows):
    """Rows whose copies own their path, as stock's copy does (0x5c65ac): the firmware may free and
    replace it."""
    def __init__(self, fw):
        super().__init__(fw)
        self.fw_strdup = fw.fn('strdup', C.c_void_p, C.c_void_p)
    def push(self, q, v):
        super().push(q, v)
        if (q & 0xffffffff) in self.held:
            at = self[q][-1] + O['REC_PATH']
            old = C.c_void_p.from_address(at).value
            if old:
                C.c_void_p.from_address(at).value = self.strdup(old)

    def strdup(self, s):
        return self.fw_strdup(s)


def url(sid, album_id='al1', suffix='flac'):
    return f'/mnt/mmc/.{APP}/cat/{album_id}/{sid}.{suffix}'


@pytest.fixture
def rig(fw, tmp_path):
    return Rig(fw, tmp_path)


def norm(fw, s):
    out = C.create_string_buffer(256)
    fw.fn('cat_norm', C.c_uint, C.c_char_p, C.c_char_p, C.c_uint)(s.encode(), out, 256)
    return out.value.decode()


def test_names_are_normalised_for_the_tag_layer(fw):
    assert norm(fw, 'Wild Honey (2012 Stereo Mix)') == 'wildhoney'
    assert norm(fw, 'Rubber Soul (Remastered 2009)') == 'rubbersoul'
    assert norm(fw, "Don't Worry Baby - 2001 Remaster") == 'dontworrybaby'
    assert norm(fw, 'Stick Season feat. Someone') == 'stickseason'
    assert norm(fw, 'Gravity (Live in LA)') == 'gravityliveinla'  # a live take is another recording
    assert norm(fw, 'Help! [Deluxe Edition]') == 'help'


def test_a_catalogue_is_imported_answered_and_shown_beside_the_card(rig):
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Me', 'Mine', 1, 200, 1000)
    rig.write(7, [song_row('s1', 'Server One', 'Band', 'Album', 1, 180, 5000),
                  song_row('s2', 'Server Two', 'Band', 'Album', 2, 190, 5100),
                  'p\tpl1\tRoad Trip\n', 'i\tpl1\ts2\t\n', 'i\tpl1\tx\t/mnt/mmc/Music/a.flac\n'])
    rig.poll()
    assert rig.files.files[DONE] == b'7 ok 5\n'
    assert rig.shown() == ['Server One', 'Server Two']
    meta = rig.query('select id,fileurl,sid,seconds,size from cat.servermeta order by id')
    assert meta == [(0x40000000, url('s1'), 's1', 180, 5000), (0x40000001, url('s2'), 's2', 190, 5100)]
    union = rig.fw.fn('catalogue_union', C.c_void_p, C.c_char_p)(b'select name from songtable order by name')
    sql = C.string_at(union).decode()
    assert sql == 'select name from (select * from songtable union all select * from servertable) order by name'
    assert [r[0] for r in rig.query(sql)] == ['Card Song', 'Server One', 'Server Two']
    # the playlist: its server row and the synced card file, in order, under a stock header
    db = sqlite3.connect(rig.tmp/'database.db')
    head = db.execute('select id,filename,cd_num from playlist where id<100').fetchall()
    items = db.execute('select fileurl,cd_num from playlist where id>100 order by id').fetchall()
    assert head == [(0, 'Road Trip', 0)] and items == [(url('s2'), 0), ('/mnt/mmc/Music/a.flac', 0)]
    # the same seq is not imported again; a new one replaces the app's rows
    rig.poll(3)
    assert rig.shown() == ['Server One', 'Server Two']
    rig.write(8, [song_row('s3', 'Server Three', 'Band', 'Album', 3, 200, 5200), 'p\tpl1\tRoad Trip\n'])
    rig.poll()
    assert rig.files.files[DONE] == b'8 ok 2\n' and rig.shown() == ['Server Three']
    db = sqlite3.connect(rig.tmp/'database.db')
    assert db.execute('select count(*) from playlist where id>100').fetchone() == (0,)


def test_a_file_that_is_not_a_catalogue_or_is_cut_short_is_answered_with_its_error(rig):
    rig.files.files[TSV] = b'#Q2CAT/1\tseq=3\taccount=1\trows=0\n'  # an empty server: nothing to show
    rig.poll()
    assert rig.files.files[DONE] == b'3 ok 0\n'
    rig.files.files[TSV] = catalogue(4, [song_row('s1', 'One', 'B', 'A', 1, 100, 1)]).replace(b'rows=1', b'rows=2')
    rig.poll()
    assert rig.files.files[DONE] == b'4 error Read 1 of 2 rows\n'
    rig.files.files[TSV] = b'#Q2CAT/2\tseq=5\taccount=1\trows=0\n'
    rig.poll(3)
    assert rig.files.files[DONE] == b'4 error Read 1 of 2 rows\n'  # not a Q2CAT/1 file: never read
    # the last row without its newline is still a row
    rig.files.files[TSV] = catalogue(6, [song_row('s1', 'One', 'B', 'A', 1, 100, 1)]).rstrip(b'\n')
    rig.poll()
    assert rig.files.files[DONE] == b'6 ok 1\n' and rig.shown() == ['One']


MBID = '0f8c1b2e-0000-4000-8000-0000000000a1'


@pytest.mark.parametrize('layer', ['mbid', 'file', 'tags', 'untagged file'])
def test_each_layer_hides_a_server_song_that_is_a_card_file(rig, layer):
    """The server's song: "Wild Honey (2012 Stereo Mix)", The Beach Boys, 50 Big Ones, track 3, 161 s, 7777
    bytes. Each card file matches it by one layer alone, the others failing."""
    card = {  # title, artist, album, seconds, size, the file's first bytes
        'mbid': ('Wild Honey', 'Beach Boys', 'Other Album', 300, 1, b'fLaC..MUSICBRAINZ_TRACKID=' + MBID.encode()),
        'file': ('Wild Honey', 'The Beach Boys', 'Greatest Hits', 160, 7777, b''),
        'tags': ('Wild Honey', 'The Beach Boys', '50 Big Ones', 160, 4000, b''),
        'untagged file': ('', '', '', 162, 7777, b''),
    }[layer]
    title, artist, album, seconds, size, head = card
    rig.card_song('/mnt/mmc/Music/x.flac', title, artist, album, 3, seconds, size, head)
    rig.write(1, [song_row('s1', 'Wild Honey (2012 Stereo Mix)', 'The Beach Boys', '50 Big Ones', 3, 161, 7777,
                           MBID if layer == 'mbid' else ''),
                  song_row('s2', 'Kept', 'The Beach Boys', '50 Big Ones', 4, 100, 1)])
    rig.poll()
    assert rig.shown() == ['Kept']
    assert rig.query("select dup from cat.servermeta where sid='s1'") == [('/mnt/mmc/Music/x.flac',)]
    # the linked file's sid: availability is the card's, a rating goes out with the sid
    assert not rig.unavailable('/mnt/mmc/Music/x.flac')
    rig.fw.fn('catalogue_rated', None, C.c_void_p, C.c_int)(rig.record('/mnt/mmc/Music/x.flac'), 4)
    key = listen_key('/mnt/mmc/Music/x.flac')
    assert rig.files.files[f'/mnt/mmc/.{APP}/ratings.out'] == f'{key:08x}\ts1\tstars=4\t1700000000\n'.encode()


@pytest.mark.parametrize('case', ['other mbid', 'live take', 'longer', 'unreadable length'])
def test_tags_alone_never_merge_when_a_stronger_layer_disagrees(rig, case):
    """Equal tags, but the MusicBrainz ids differ, or the lengths are more than 2 s apart (or unknown)."""
    head = b'MUSICBRAINZ_TRACKID=' + MBID.replace('a1', 'b2').encode() if case == 'other mbid' else b''
    seconds = {'other mbid': 160, 'live take': 230, 'longer': 164, 'unreadable length': None}[case]
    rig.card_song('/mnt/mmc/Music/x.flac', 'Wild Honey', 'The Beach Boys', '50 Big Ones', 3, seconds, 4000, head)
    rig.write(1, [song_row('s1', 'Wild Honey', 'The Beach Boys', '50 Big Ones', 3, 160, 9000,
                           MBID if case == 'other mbid' else '')])
    rig.poll()
    assert rig.shown() == ['Wild Honey']


def test_a_song_copied_to_the_card_later_leaves_the_server_rows(rig, fw):
    rig.write(1, [song_row('s1', 'Stick Season', 'Noah Kahan', 'Stick Season', 1, 182, 30000000)])
    rig.poll()
    assert rig.shown() == ['Stick Season']
    rig.poll(2)  # nothing changed: no job (the file is not rewritten)
    imports = sum(s.startswith(b'attach') and b'.new' in s for s in rig.sqls)
    assert imports == 1
    # a USB copy and the scan after it: songtable has it, library_gen moves
    rig.card_song('/mnt/mmc/Music/Noah Kahan/01 Stick Season.flac', 'Stick Season', 'Noah Kahan', 'Stick Season',
                  1, 182, 30000000)
    fw.var('library_gen', C.c_uint).value += 2
    rig.poll()
    assert rig.shown() == [] and rig.files.files[DONE] == b'1 ok 1\n'


def test_availability_offline_online_copies_and_the_server_state(rig):
    rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac'),
                  song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    assert not rig.unavailable(url('s1')) and rig.unavailable(url('s2'))
    assert not rig.unavailable('/mnt/mmc/Music/card.flac')
    # stock's play check on a Library row (CATALOGUE_ACCESS): a server row passes only when it can play
    check = rig.fw.fn('catalogue_access', C.c_int, C.c_char_p, C.c_int)
    assert check(url('s1').encode(), 4) == 0 and check(url('s2').encode(), 4) == -1
    assert check(b'/mnt/mmc/Music/card.flac', 4) == -1  # no such card file: access's own answer
    rig.online = True
    rig.poll()
    assert not rig.unavailable(url('s2'))
    assert check(url('s2').encode(), 4) == 0
    rig.files.files[STATE] = b'hold=0\nserver=down\n'
    rig.poll()
    assert rig.unavailable(url('s2'))
    # the stream cache's changes: one copy gained, one evicted
    rig.files.files[AVAIL] = b's2\t/mnt/mmc/.subsonic/stream/s2.flac\n-s1\n'
    rig.poll()
    assert rig.unavailable(url('s1')) and not rig.unavailable(url('s2'))
    rig.files.files[AVAIL] += b'-s2\n'
    rig.poll()
    assert rig.unavailable(url('s2'))


def queue(rig, paths):
    return rig.rows.rows([rig.record(p) for p in paths])


def load(rig, q, at):
    at_ = C.c_int(at)
    own = C.c_void_p()
    kind = rig.fw.fn('catalogue_load', C.c_int, C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p))(
        q, C.byref(at_), C.byref(own))
    paths = [C.string_at(C.c_void_p.from_address(r + O['REC_PATH']).value or 0).decode()
             for r in rig.rows[own.value]] if own.value else None
    return kind, at_.value, paths


def test_offline_a_list_plays_its_card_rows_and_copies_and_shuffle_leaves_the_rest_out(rig):
    rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac'),
                  song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    q = queue(rig, ['/mnt/mmc/Music/a.flac', url('s2'), url('s1'), '/mnt/mmc/Music/b.flac'])
    assert load(rig, q, 2) == (1, 1, ['/mnt/mmc/Music/a.flac', '/mnt/mmc/.subsonic/stream/s1.flac',
                                       '/mnt/mmc/Music/b.flac'])
    # Shuffle Songs plays the whole library through play_folder, so through this same load
    assert load(rig, q, 0)[2] == ['/mnt/mmc/Music/a.flac', '/mnt/mmc/.subsonic/stream/s1.flac',
                                  '/mnt/mmc/Music/b.flac']
    # a greyed row pressed: nothing loads, and it says why
    assert load(rig, q, 1)[0] == 2 and rig.toasts[-1] == b'Not available offline'
    # no server row: stock's load, untouched
    assert load(rig, queue(rig, ['/mnt/mmc/Music/a.flac']), 0) == (0, 0, None)


@pytest.mark.parametrize('pressed', ['s2', 'card'])
def test_a_press_that_loads_nothing_does_not_start_the_queue_stock_had_again(rig, fw, pressed):
    """Stock's player_start loads the list (ringnav_load), then starts the queue at its position, whatever the load
    did. A server row that loaded nothing (handed to its app, or here one that can't play offline) left the old
    queue there, and its song started again from 0: a paused song was heard until np_stop's stop reached the player
    (the device, 2026-11-18). That start is refused; a card row's press starts as before, and so does a start after."""
    rig.write(1, [song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    q = queue(rig, ['/mnt/mmc/Music/a.flac', url('s2')])
    at = 1 if pressed == 's2' else 0
    order = []
    load = fw.fn('ringnav_load', C.c_int, C.c_void_p, C.c_int, C.c_int)
    start = fw.fn('ringnav_start_player', C.c_int)
    fw.mock('stock_load_trampoline', lambda *_: order.append('load') or 1)
    fw.mock('stock_start_player_trampoline', lambda *_: order.append('start') or 1)

    def stock_player_start(q, at, cls, mode, *_):  # player_start (0x520b4c): mclLoadPlayList, then mclStartPlayer
        load(q, at, cls)
        order.append(start())
        return 1
    fw.mock('stock_player_start_trampoline', stock_player_start)
    fw.fn('ringnav_player_start', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int)(q, at, 0xf001, 2)
    assert order == ([-1] if pressed == 's2' else ['load', 'start', 1])
    order.clear()
    assert start() == 1 and order == ['start']  # the next start, a Play press, is stock's


def app_running(rig):
    """The catalogue app on the card and running: its binary, and its launcher's pid file."""
    rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    rig.files.files[f'/tmp/q2-{APP}'] = b'42'


def test_online_a_list_is_handed_to_the_app_and_played_offline_if_it_does_not_answer(rig):
    rig.online = True
    app_running(rig)
    rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac'),
                  song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    q = queue(rig, ['/mnt/mmc/Music/a.flac', url('s2'), url('s1')])
    assert load(rig, q, 1)[0] == 2
    assert rig.files.files[f'/tmp/q2-{APP}.catplay'] == \
        f'1 1\n/mnt/mmc/Music/a.flac\n{url("s2")}\n{url("s1")}\n'.encode()
    rig.poll(2)  # 2.2 s: still waiting
    assert not rig.played
    rig.poll(2)
    assert rig.played == [(['/mnt/mmc/Music/a.flac'.encode(), b'/mnt/mmc/.subsonic/stream/s1.flac'], 1)]
    # answered in time: the app's .play plays it (addon.c), nothing here
    assert load(rig, q, 0)[0] == 2
    rig.files.files[f'/tmp/q2-{APP}.play'] = b'2 /mnt/mmc/.subsonic/queue.m3u'
    rig.poll(5)
    assert len(rig.played) == 1


@pytest.mark.parametrize('n', [0, 2])
def test_a_catplay_tells_the_app_download_ahead_for_its_list_from_the_start(rig, fw, n):
    """The seam test (fix/seam-tests, 2026-11-22): with Download Ahead Off the app still fetched two songs after a
    list's first, as it had no .ahead for the list's seq until the first song was whole and queued (ahead_poll), and
    with none it keeps its default of two in list order. The .ahead for the catplay's seq (the .play's, as the app
    answers with it) is written with the catplay: the setting's n, and no lines, as the firmware's queue holds none
    of the list yet (the app takes the songs after the first in the catplay's order itself)."""
    ss = (C.c_int * 2).in_dll(fw.lib, 'stream_state__ss')
    ss[0], ss[1] = n, 1  # Download Ahead, read
    rig.online = True
    app_running(rig)
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1), song_row('s2', 'Two', 'B', 'A', 2, 100, 2)])
    rig.poll()
    assert load(rig, queue(rig, [url('s1'), url('s2')]), 0)[0] == 2
    assert rig.files.files[f'/tmp/q2-{APP}.catplay'].startswith(b'1 0\n')
    assert rig.files.files.get(f'/tmp/q2-{APP}.ahead') == f'#Q2AHEAD/1\tseq=1\tn={n}\n'.encode()


def test_a_catplay_with_a_path_longer_than_twice_its_first_room_is_whole(rig):
    """catplay_write's buffer, 64 + 160 a song, grows until each path fits: a two-song list whose card path is
    900 bytes (its first room 384, doubled once 768) is written whole, its length the bytes written."""
    rig.online = True
    app_running(rig)
    rig.write(1, [song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    long_path = '/mnt/mmc/Music/' + 'x' * 880 + '.flac'
    q = queue(rig, [long_path, url('s2')])
    assert load(rig, q, 0)[0] == 2
    assert rig.files.files[f'/tmp/q2-{APP}.catplay'] == f'1 0\n{long_path}\n{url("s2")}\n'.encode()


def test_shuffle_songs_hands_the_app_every_song_in_a_shuffled_order(rig, fw, mcl):
    """The drive, 2026-11-19: Library > Songs > Shuffle over a library of card and server songs played "rockman",
    "rockstar", "rockstar", "rockstar", and pressed again, "the best is yet to come", "the best of my love", "best
    of times": the Songs list's title order from a random start. Online the list goes to its app as a catplay,
    which answers with an m3u from <start> on in the catplay's order, and Shuffle Songs' order (playback_shuffle)
    was over the old queue, not the app's. The app is handed every song, card and server rows, already shuffled:
    few songs follow their list neighbour."""
    rig.online = True
    app_running(rig)
    sids = [f's{i:03}' for i in range(150)]
    rig.write(1, [song_row(s, f'Title {s}', 'B', 'A', i + 1, 100, i + 1) for i, s in enumerate(sids)])
    rig.poll()
    rows = [url(s) if i % 4 else f'/mnt/mmc/Music/c{i:03}.flac' for i, s in enumerate(sids)]  # the Songs list
    assert fw.fn('shuffle_play', C.c_int, C.c_void_p)(queue(rig, rows)) == len(rows)
    # stock's playing_page loads the queue play_folder handed it (player_start: ringnav_load, catalogue_load)
    paths, at = rig.played[-1]
    assert load(rig, queue(rig, [p.decode() for p in paths]), at)[0] == 2
    head, *lines = rig.files.files[f'/tmp/q2-{APP}.catplay'].decode().splitlines()
    run = lines[int(head.split()[1]):]  # what the app's m3u plays: the rows from <start> on
    assert sorted(run) == sorted(rows), f'the shuffle plays every song: {len(run)} of {len(rows)}'
    place = {r: i for i, r in enumerate(rows)}
    neighbours = sum(place[b] == place[a] + 1 for a, b in zip(run, run[1:]))
    assert neighbours < 10, f'{neighbours} of {len(run) - 1} songs follow their list neighbour'


def test_a_catalogue_app_not_running_is_started_and_handed_the_list_once_it_listens(rig, fw):
    """After a boot, or the app's own idle exit, nothing answers a catplay: the press starts the app, as its
    Streaming row does (the capability line written first, which it reads at its start), and the catplay is
    written once its socket is there (it takes a catplay present at its start as answered); the 4 s wait
    runs from then. An app that never listens: the list plays offline CATPLAY_START_MS on."""
    rig.online = True
    rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac'),
                  song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    sock, catplay = f'/tmp/q2-{APP}.browse.sock', f'/tmp/q2-{APP}.catplay'
    rig.files.files[sock] = b''  # a killed app's
    started = []
    fw.mock('fw_system', lambda cmd, *_: started.append(C.string_at(cmd)) or 0)
    q = queue(rig, ['/mnt/mmc/Music/a.flac', url('s2'), url('s1')])
    assert load(rig, q, 1)[0] == 2
    assert started == [f'/bin/sh /mnt/mmc/.{APP}/run &'.encode()] and b'catalogue' in rig.files.files['/tmp/q2-addon.caps']
    assert catplay not in rig.files.files and sock not in rig.files.files
    rig.poll(6)  # 6.6 s starting: no catplay yet, and nothing played meanwhile
    assert catplay not in rig.files.files and not rig.played
    rig.files.files[f'/tmp/q2-{APP}'] = b'42'
    rig.files.files[sock] = b''  # it listens
    rig.poll(1)
    assert rig.files.files[catplay] == f'1 1\n/mnt/mmc/Music/a.flac\n{url("s2")}\n{url("s1")}\n'.encode()
    rig.files.files[f'/tmp/q2-{APP}.play'] = b'1 /mnt/mmc/.subsonic/queue.m3u'  # answered
    rig.poll(5)
    assert not rig.played and len(started) == 1
    # running now: the next press writes its catplay at once, starting nothing
    assert load(rig, q, 2)[0] == 2 and rig.files.files[catplay].startswith(b'2 2\n') and len(started) == 1
    rig.files.files[f'/tmp/q2-{APP}.play'] = b'2 /mnt/mmc/.subsonic/queue.m3u'
    rig.poll(1)
    # its idle exit: started again by the next press; one that never listens plays offline
    del rig.files.files[f'/tmp/q2-{APP}'], rig.files.files[sock]
    assert load(rig, q, 1)[0] == 2 and len(started) == 2
    rig.poll(13)  # 14.3 s
    assert not rig.played
    rig.poll(1)
    assert rig.played == [(['/mnt/mmc/Music/a.flac'.encode(), b'/mnt/mmc/.subsonic/stream/s1.flac'], 1)]
    assert rig.files.files[catplay].startswith(b'2 2\n')  # never written for it


@pytest.mark.parametrize('case', ['offline', 'signed out', 'no app'])
def test_no_app_is_started_offline_signed_out_or_without_a_catalogue_app(rig, fw, case):
    """Offline the list plays from the card at once, a signed-out app's row is gone from the Library and starts
    nothing, and a catalogue app whose binary is not on the card is never started (its catplay goes unanswered, as
    before)."""
    rig.online = case != 'offline'
    rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac')])
    rig.poll()
    if case != 'no app':
        rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    if case == 'signed out':
        rig.sign_out()
    started = []
    fw.mock('fw_system', lambda cmd, *_: started.append(C.string_at(cmd)) or 0)
    load(rig, queue(rig, [url('s1')]), 0)
    rig.poll(2)
    assert not started


class RadioOff:
    """The app's idle exit, then Smart Wi-Fi's (docs/internals.md#smart-wi-fi): Power's row on, nothing needing the
    network for SMART_IDLE_MS, the radio off through stock's switch (a stand-in toggling g_wififlag) and no link."""
    def __init__(self, rig, fw, st):
        self.rig, self.wifi, self.switched, self.started = rig, fw.var('g_wififlag', C.c_ubyte), [], []
        fw.mock('wifi_switch', self.switch)
        C.c_int.from_address(O['NET_TYPE']).value = 2
        config = rig.config  # SMARTWIFI as pod_settings reads it
        fw.mock('toolsReadConfig', lambda path, section, key, out, default, *_: C.memmove(out, b'1\0', 2)
                if C.string_at(key) == b'SMARTWIFI' else config(path, section, key, out, default))
        fw.mock('get_wifisignal', lambda *_: 3 if rig.online else -1)  # the status bar's bars
        st('smart_wifi').value, self.wifi.value, rig.online = 1, 1, True
        self.smart_poll = fw.fn('smart_poll', None)
        rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac'),
                      song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
        rig.poll()
        rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''  # on the card, not running
        # the app's start; the scan hold's wpa_cli (smart_wifi.c, this rig's wpa_supplicant socket) is not one
        fw.mock('fw_system', lambda cmd, *_: 0 if C.string_at(cmd).startswith(b'wpa_cli ')
                else self.started.append(C.string_at(cmd)) or 0)
        self.tick(O['SMART_IDLE_MS'] // 1000 + 2)
        rig.online = False  # networkStop's: no link
        self.tick(2)
        assert self.switched == [0] and fw.fn('smart_wifi_off', C.c_int)()
        self.q = queue(rig, ['/mnt/mmc/Music/a.flac', url('s2'), url('s1')])

    def switch(self, *_):
        self.wifi.value ^= 1
        self.switched.append(self.wifi.value)
        return 0

    def tick(self, seconds):
        for _ in range(seconds):
            self.smart_poll()
            self.rig.poll(1)


def test_a_server_song_pressed_while_smart_wi_fi_has_the_radio_off_waits_for_the_link(rig, fw, st):
    """The radio comes back for a server row, so the row is not greyed, and a press wakes it and, once the link is
    up, starts the app and hands it the list, as online."""
    off = RadioOff(rig, fw, st)
    assert not rig.unavailable(url('s2'))
    assert load(rig, off.q, 1)[0] == 2 and b'Not available offline' not in rig.toasts
    assert off.switched == [0, 1] and not off.started  # the radio back first, the app once there is a link
    off.tick(3)
    assert not off.started and not rig.played
    rig.online = True
    off.tick(2)
    assert off.started == [f'/bin/sh /mnt/mmc/.{APP}/run &'.encode()]
    rig.files.files[f'/tmp/q2-{APP}'] = b'42'
    rig.files.files[f'/tmp/q2-{APP}.browse.sock'] = b''  # it listens
    off.tick(1)
    assert rig.files.files[f'/tmp/q2-{APP}.catplay'] == \
        f'1 1\n/mnt/mmc/Music/a.flac\n{url("s2")}\n{url("s1")}\n'.encode()


def test_a_wake_that_brings_no_link_plays_the_list_offline(rig, fw, st):
    """No link SMART_CONNECT_MS after the press: the card rows and copies play, and the app is never started."""
    off = RadioOff(rig, fw, st)
    assert load(rig, off.q, 1)[0] == 2 and off.switched == [0, 1]
    off.tick(O['SMART_CONNECT_MS'] // 1000 - 2)
    assert not rig.played
    off.tick(4)
    assert not off.started and rig.played == [(['/mnt/mmc/Music/a.flac'.encode(), b'/mnt/mmc/.subsonic/stream/s1.flac'], 1)]


def test_a_listen_of_a_server_songs_offline_copy_counts_as_its_server_row(rig, fw, st):
    """A server song plays from its Stream Cache copy (the app's .play, or the offline queue), a file no Library
    row has: its play counts under the server row's key (its fileurl's listen_key), so Most Played and Cover
    Flow's album plays find the song; a card file's and a server row's own count under their paths. Most
    Played's kept tracks don't take the copy's record, which would never match the row's count."""
    copy = '/mnt/mmc/.subsonic/stream/x/s1.flac'
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, local=copy)])
    rig.poll()
    key = fw.fn('listen_key', C.c_uint, C.c_void_p)
    count = fw.fn('navigation__play_count', None, C.c_void_p, C.c_uint)
    table = st('plays', C.c_uint * 2)
    for path, counted in ((copy, url('s1')), ('/mnt/mmc/Music/a.flac', None), (url('s1'), None)):
        r = rig.record(path)
        count(r, key(r))
        assert table[0] == key(rig.record(counted or path))
    assert table[1] == 2  # the copy's listen and the row's own: one song, two plays
    st('mp_list', C.c_void_p).value = kept = rig.rows.new([rig.record('/mnt/mmc/Music/a.flac')])
    r = rig.record(copy)
    count(r, key(r))
    assert len(rig.rows[kept]) == 1  # not the copy: the next open reads the library again


def test_ratings_in_are_applied_unless_the_q2_s_own_change_is_newer(rig, fw):
    """An app with ratings=1 writes one number: five its star (the like, a's stars left as they were), under five
    that many stars and no like. A line of one number in ratings.out is newer for both."""
    rig.card_song('/mnt/mmc/Music/a.flac', 'A', 'B', 'C', 1, 100, 1)
    rig.card_song('/mnt/mmc/Music/b.flac', 'B', 'B', 'C', 2, 100, 1)
    rig.write(1, [song_row('s1', 'Server', 'B', 'A', 1, 100, 1)])
    rig.poll()
    ka, kb, ks = (listen_key(p) for p in ('/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.flac', url('s1')))
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.out'] = f'{kb:08x}\t\t2\t1700000100\n'.encode()
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = (f'{ka:08x}\t\t5\t1700000000\n'
                                                      f'{kb:08x}\t\t5\t1700000000\n'
                                                      f'{ks:08x}\ts1\t3\t0\n').encode()
    rig.files.files['/mnt/data/ringnav-ratings'] = struct.pack('<I', ka & ~7 | 2).ljust(O['RATE_SLOTS'] * 4, b'\0')
    rig.poll()
    assert f'/mnt/mmc/.{APP}/ratings.in' not in rig.files.files
    assert f'/mnt/mmc/.{APP}/ratings.in.taking' not in rig.files.files
    db = sqlite3.connect(rig.tmp/'database.db')
    assert db.execute('select fileurl from myfav').fetchall() == [('/mnt/mmc/Music/a.flac',)]
    rate = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    assert rate(0, ka) == 2 and rate(0, ks) == 3 and rate(0, kb) == 0  # b's newer change on the Q2 kept


def test_likes_and_stars_from_the_server_are_applied_apart(rig, fw):
    """ratings=2's lines: like= puts a song in or out of My Fav and nothing else, stars= keeps its stars and
    nothing else, and a newer change of the Q2's holds back only a line of its own kind."""
    rig.card_song('/mnt/mmc/Music/a.flac', 'A', 'B', 'C', 1, 100, 1)
    rig.card_song('/mnt/mmc/Music/b.flac', 'B', 'B', 'C', 2, 100, 1)
    rig.write(1, [song_row('s1', 'Server', 'B', 'A', 1, 100, 1)])
    rig.poll()
    ka, kb, ks = (listen_key(p) for p in ('/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.flac', url('s1')))
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.out'] = f'{kb:08x}\t\tlike=0\t1700000100\n'.encode()
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = (f'{ka:08x}\t\tlike=1\t1700000000\n'
                                                      f'{ka:08x}\t\tstars=4\t1700000000\n'
                                                      f'{kb:08x}\t\tlike=1\t1700000000\n'
                                                      f'{kb:08x}\t\tstars=3\t1700000000\n'
                                                      f'{ks:08x}\ts1\tstars=5\t0\n'
                                                      f'{ks:08x}\ts1\tlike=2\t0\n'  # no such like: passed over
                                                      f'{ks:08x}\ts1\tfoo=1\t0\n').encode()
    rig.files.files['/mnt/data/ringnav-ratings'] = b''
    rig.poll()
    assert f'/mnt/mmc/.{APP}/ratings.in' not in rig.files.files
    db = sqlite3.connect(rig.tmp/'database.db')
    assert db.execute('select fileurl from myfav').fetchall() == [('/mnt/mmc/Music/a.flac',)]  # five stars: no like
    rate = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    assert rate(0, ka) == 4 and rate(0, kb) == 3 and rate(0, ks) == 5


def test_a_thousand_ratings_from_the_server_are_one_database_write_and_one_table_write(rig, fw):
    """A first sync of a server's stars and hearts: 1,000 lines in one ratings.in, beside a ratings.out of 1,000 of
    the Q2's own. They are applied with one connection and one transaction for My Fav, the ratings table written
    once, and ratings.out read and indexed once, on the UI thread's pass that takes them; the pass's time is
    printed (test/fw at -O0, for comparing builds, not the Q2's)."""
    import time
    sids = [f's{i:04}' for i in range(1000)]
    rig.write(1, [song_row(sid, f'T {sid}', 'B', 'A', i + 1, 100, i + 1) for i, sid in enumerate(sids)])
    rig.poll()
    keys = [listen_key(url(sid)) for sid in sids]
    lines = ''.join(f'{k:08x}\t{sid}\t' + (f'stars={i % 5 + 1}' if i % 2 else f'like={i % 4 // 2}') + '\t1700000000\n'
                    for i, (k, sid) in enumerate(zip(keys, sids)))
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.out'] = ''.join(f'{0x10000 + i:08x}\t\tstars=3\t1700000100\n'
                                                               for i in range(1000)).encode()
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = lines.encode()
    rig.files.files['/mnt/data/ringnav-ratings'] = b''
    opens, renames = [], []  # counted here: the call log keeps only the latest calls
    fw.mock('sqlite3_open', lambda name, db, *_: opens.append(C.string_at(name)) or rig.open(name, db))
    fw.mock('fw_rename', lambda old, new, *_: renames.append(C.string_at(new)) or rig.rename(old, new))
    t = time.perf_counter()
    rig.poll(1)
    took = time.perf_counter() - t
    print(f'ratings_in of 1,000 lines: {took * 1000:.0f} ms, {len(opens)} database opens, '
          f'{renames.count(b"/mnt/data/ringnav-ratings")} ratings table writes')
    assert f'/mnt/mmc/.{APP}/ratings.in' not in rig.files.files
    db = sqlite3.connect(rig.tmp/'database.db')
    liked = {r[0] for r in db.execute('select fileurl from myfav')}
    assert liked == {url(sid) for i, sid in enumerate(sids) if not i % 2 and i % 4 // 2}
    rate = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    assert all(rate(0, k) == i % 5 + 1 for i, k in enumerate(keys) if i % 2)
    assert len(opens) <= 2  # the songtable lookup's and My Fav's
    assert renames.count(b'/mnt/data/ringnav-ratings') == 1


def test_a_scan_that_ends_while_a_job_runs_is_matched_after_it(rig, fw):
    """A USB copy's scan moves library_gen while the import reads the card's songs: the swap must not
    count it as matched, so the next poll matches again and finds the new file."""
    rig.write(1, [song_row('s1', 'Stick Season', 'Noah Kahan', 'Stick Season', 1, 182, 30000000)])
    gen = fw.var('library_gen', C.c_uint)
    real = rig.exec

    def exec_(db, sql, cb, ctx, err, *_):
        result = real(db, sql, cb, ctx, err)
        if C.string_at(sql) == b'commit':  # the job has matched against songtable
            rig.card_song('/mnt/mmc/Music/01 Stick Season.flac', 'Stick Season', 'Noah Kahan', 'Stick Season',
                          1, 182, 30000000)
            gen.value += 2
            fw.mock('sqlite3_exec', real)
        return result
    fw.mock('sqlite3_exec', exec_)
    rig.poll()
    assert rig.shown() == ['Stick Season']  # matched against the card before the copy
    rig.poll(2)
    assert rig.shown() == []


def playlists(rig):
    db = sqlite3.connect(rig.tmp/'database.db')
    heads = db.execute('select id,name from playlist where id<100 order by id').fetchall()
    items = {h: [r[0] for r in db.execute('select fileurl from playlist where id>100 and cd_num=? order by id', (h,))]
             for h, _ in heads}
    db.close()
    return heads, items


def test_a_playlist_synced_as_an_m3u_is_shown_once_as_the_catalogue_s(rig):
    """The app synced "Road Trip" to the card (an .m3u the library extension imported: a stock playlist of
    card files) and lists it in the catalogue too. The catalogue's takes the twin's place; "Mine", the
    user's own playlist that shares a catalogue playlist's name but not its songs, keeps its place."""
    a, b = '/mnt/mmc/Subsonic/a.flac', '/mnt/mmc/Subsonic/b.flac'
    rig.card_song(a, 'A', 'B', 'C', 1, 100, 1)
    rig.card_song(b, 'B', 'B', 'C', 2, 100, 2)
    db = sqlite3.connect(rig.tmp/'database.db')
    for row in [(3, 'Road Trip', 3), (101, a, 3), (102, b, 3), (5, 'Mine', 5), (103, a, 5)]:
        db.execute('insert into playlist(id,filename,fileurl,name,cd_num) values (?,?,?,?,?)',
                   (row[0], row[1] if row[0] < 100 else 'x', row[1] if row[0] > 100 else '', row[1], row[2]))
    db.commit()
    db.close()
    rig.write(1, [song_row('s9', 'Server', 'B', 'A', 1, 100, 9),
                  'p\tpl1\tRoad Trip\n', f'i\tpl1\tsa\t{a}\n', f'i\tpl1\tsb\t{b}\n',
                  'p\tpl2\tMine\n', 'i\tpl2\ts9\t\n'])
    rig.poll()
    heads, items = playlists(rig)
    assert heads == [(3, 'Road Trip'), (5, 'Mine')]  # one Road Trip, and no second Mine
    assert items[3] == [a, b] and items[5] == [a]
    assert rig.query('select app,pid,list,twin from cat.serverlists') == [(APP, 'pl1', 3, 1)]
    # the server adds a song to it: the catalogue's list is the one shown
    rig.write(2, [song_row('s9', 'Server', 'B', 'A', 1, 100, 9),
                  'p\tpl1\tRoad Trip\n', f'i\tpl1\tsa\t{a}\n', f'i\tpl1\tsb\t{b}\n', 'i\tpl1\ts9\t\n'])
    rig.poll()
    heads, items = playlists(rig)
    assert heads == [(3, 'Road Trip'), (5, 'Mine')] and items[3] == [a, b, url('s9')]
    # gone from the server: the twin is the user's synced file's, so it stays; a playlist the
    # catalogue made goes
    rig.write(3, ['p\tpl3\tNew\n', 'i\tpl3\ts9\t\n', song_row('s9', 'Server', 'B', 'A', 1, 100, 9)])
    rig.poll()
    assert playlists(rig)[0] == [(0, 'New'), (3, 'Road Trip'), (5, 'Mine')]
    rig.write(4, [song_row('s9', 'Server', 'B', 'A', 1, 100, 9)])
    rig.poll()
    assert playlists(rig)[0] == [(3, 'Road Trip'), (5, 'Mine')]


STOCK_SLOT = 0xa56000  # stock's shared connection's global: in demo's image, below the payload, so catalogue_attach keeps it


def test_the_library_s_queries_read_the_server_s_songs_only_while_its_app_is_signed_in(rig, fw):
    """No setting (the user's, 2026-11-19): signed in, the server's songs are in the Library, an older build's
    LIBRARYSOURCE=0 in config.ini notwithstanding (rig.source); signed out, a job takes them out."""
    from test_nav_lists import Loads
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Me', 'Album', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'Server One', 'Band', 'Album', 2, 180, 5000)])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    attach = fw.fn('catalogue_attach', None, C.c_void_p)
    attach(STOCK_SLOT)  # stock_db_open on stock's own handle
    loads = Loads(fw)
    loads.music("select * from songtable where album='Album'")
    sql = loads.ran[-1]
    assert 'union all select * from servertable' in sql
    assert sorted(r[6] for r in rig.query(sql)) == ['Card Song', 'Server One']
    # signed out: the card's songs alone, and the server's file let go
    rig.sign_out()
    loads.music("select * from songtable where album='Album'")
    assert 'servertable' not in loads.ran[-1] and not fw.fn('catalogue_has', C.c_int)()
    assert b'detach cat' in rig.sqls
    assert rig.query('select count(*) from cat.servermeta') == [(0,)]
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def test_a_bound_row_is_greyed_while_its_server_song_cannot_play(rig, fw):
    """Stock's pooled rows of a Library list (p_deque_showlist's records, bound by ringnav_table_load): the
    text of one bound to an unplayable server row is painted grey; the same row bound to a card song, or the
    list once online, is not."""
    rig.write(1, [song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.poll()
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = rig.rows.new(
        [rig.record('/mnt/mmc/Music/a.flac'), rig.record(url('s2'))])
    row, label = (C.create_string_buffer(0x400) for _ in range(2))
    struct.pack_into('<Q', label, O['W_PARENT'], C.addressof(row))
    bind = fw.fn('catalogue_bind', None, C.c_void_p, C.c_uint)
    greyed = fw.fn('catalogue_greyed', C.c_int, C.c_void_p, C.POINTER(C.c_uint))

    def colour():
        c = C.c_uint(0xffffffff)
        return c.value if greyed(C.addressof(label), C.byref(c)) else None
    bind(C.addressof(row), 1)
    assert colour() == 0xff666666
    bind(C.addressof(row), 0)  # the pool reuses the row for a card song
    assert colour() is None
    rig.online = True
    rig.poll()
    bind(C.addressof(row), 1)
    assert colour() is None


def test_under_oled_a_song_row_says_where_it_lives(rig, fw):
    """Under OLED (docs/oled-design.md#song-rows) a bound song row carries a mark without being greyed: a server
    row that can play the cloud, a card file in the app's downloads folder (q2app downloads=) the arrow, the
    user's own file none; a server row that can't play is greyed with the offline mark as before."""
    rig.files.files[f'/mnt/mmc/.{APP}/q2app'] = b'name=Subsonic\ncatalogue=1\ndownloads=Subsonic\n'
    def config(path, section, key, out, default, *_):
        name = C.string_at(key)
        v = b'3' if name == b'THEME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    fw.mock('toolsReadConfig', config)
    rig.write(1, [song_row('s2', 'Streamed', 'B', 'A', 2, 100, 2)])
    rig.online = True
    rig.poll()
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = rig.rows.new(
        [rig.record('/mnt/mmc/Music/a.flac'), rig.record('/mnt/mmc/Subsonic/B/A/b.flac'), rig.record(url('s2')),
         rig.record('/mnt/mmc/Subsonicx/c.flac')])
    row, label = (C.create_string_buffer(0x400) for _ in range(2))
    struct.pack_into('<Q', label, O['W_PARENT'], C.addressof(row))
    bind = fw.fn('catalogue_bind', None, C.c_void_p, C.c_uint)
    greyed = fw.fn('catalogue_greyed', C.c_int, C.c_void_p, C.POINTER(C.c_uint))
    loaded = []
    fw.mock('widget_load_image', lambda w, name, bm, *_: loaded.append(C.string_at(name).decode()) or 1)
    mark = fw.fn('catalogue_row_mark', None, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int, C.c_int)

    def seen(i):
        bind(C.addressof(row), i)
        loaded.clear()
        mark(None, None, C.addressof(row), 300, 16)
        c = C.c_uint(0xffffffff)
        return loaded, greyed(C.addressof(label), C.byref(c))
    assert seen(0) == ([], 0)
    assert seen(1) == (['oled_mark_down_d'], 0)
    assert seen(2) == (['oled_mark_cloud_d'], 0)
    assert seen(3) == ([], 0)  # Subsonicx/ is not Subsonic/
    rig.online = False
    rig.poll()
    assert seen(2) == ([], 1)  # greyed: the offline mark is the payload's own, no art


def test_search_finds_server_songs_while_signed_in(rig, fw):
    """Stock's Search runs SearchMusicByName's SQL through toolsQueryDbTable (library_query) outside a music
    load: with its app signed in it reads the union, so a server song is found; signed out, as stock's."""
    from test_nav_lists import Loads
    rig.card_song('/mnt/mmc/Music/a.flac', 'Wild Honey', 'Me', 'Album', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'Wild Horses', 'Band', 'Sticky', 2, 180, 5000)])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    loads = Loads(fw)
    search = "select * from songtable where (name like '%wild%') or (name==\"\" and filename like '%wild%')"
    loads.query(b'db', search.encode(), None, 0)
    assert sorted(r[6] for r in rig.query(loads.ran[-1])) == ['Wild Honey', 'Wild Horses']
    # its row names it by filename, as a card file's: a server row's is its title, no suffix
    assert sorted(r[1] for r in rig.query(loads.ran[-1])) == ['Wild Horses', 'a.flac']
    loads.query(b'db', b"select * from songtable where album like 'Sticky'", None, 0)  # not a search: as it is
    assert 'servertable' not in loads.ran[-1]
    rig.sign_out()
    loads.query(b'db', search.encode(), None, 0)
    assert loads.ran[-1] == search
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def album_record(rig, album, rid=1):
    """An album list's row as getAllAlbum fills it: its id and album, not a song (REC_TITLE, which holds the
    album too, overlaps REC_ALBUM's host pointer, so it is left out)."""
    r = C.create_string_buffer(O['REC_SIZE'] + 8)
    s = C.create_string_buffer(album.encode())
    rig.keep += [r, s]
    struct.pack_into('<i', r, O['REC_ID'], rid)
    struct.pack_into('<Q', r, O['REC_ALBUM'], C.addressof(s))
    return C.addressof(r)


def test_an_album_row_is_greyed_offline_when_none_of_its_songs_can_play(rig, fw):
    """Library > Albums (and an artist's albums): offline, an album with no card song and no server song with
    an offline copy is greyed; one with either is not, by its name as the list groups it (case folded)."""
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Me', 'Mine', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'Streamed', 'B', 'Server Only', 1, 100, 1, album_id='al1'),
                  song_row('s2', 'Cached', 'B', 'Some Cached', 1, 100, 2, album_id='al2',
                           local='/mnt/mmc/.subsonic/stream/s2.flac'),
                  song_row('s3', 'Streamed Too', 'B', 'Some Cached', 2, 100, 3, album_id='al2'),
                  song_row('s4', 'Beside Mine', 'Me', 'MINE', 2, 100, 4, album_id='al3')])
    rig.poll()
    records = [album_record(rig, a) for a in ('server only', 'Some Cached', 'mine', 'Not Known')]
    records.append(album_record(rig, 'Server Only', -1))  # the Unknown Album row
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = rig.rows.new(records)
    fw.var('fw_ctx_g_class_type', C.c_int).value = 0xf003  # CLASS_ALBUMS
    from test_nav_painters import Ui
    ui = Ui(fw)
    row = ui.add('list_item', None)
    label = ui.add('label', row)
    bind = fw.fn('catalogue_bind', None, C.c_void_p, C.c_uint)
    greyed = fw.fn('catalogue_greyed', C.c_int, C.c_void_p, C.POINTER(C.c_uint))

    def grey(i):
        bind(row.addr, i)
        return bool(greyed(label.addr, C.byref(C.c_uint(0xffffffff))))
    assert [grey(i) for i in range(5)] == [True, False, False, False, False]
    # the cached copy evicted: that album has nothing left to play
    rig.files.files[AVAIL] = b'-s2\n'
    rig.poll()
    assert [grey(i) for i in range(5)] == [True, True, False, False, False]
    # an artist's albums tab: the artist page's classinfo says so, g_class_type still the Artists list's
    page = ui.add('view', ui.window('artistinfo_page'))
    fw.var('fw_ctx_g_class_type', C.c_int).value = 0xf004
    info = fw.var('fw_ctx_g_local_classinfo_save', C.c_char * 912)
    struct.pack_into('<i', info, 0, 0xff01)  # CLASS_ARTIST_ALBUMS
    assert not grey(0)  # the row on no page: the Artists list's rows are not albums
    C.c_void_p.from_address(row.addr + O['W_PARENT']).value = page.addr
    assert grey(0) and not grey(2)
    struct.pack_into('<i', info, 0, 0xff07)  # the artist's songs tab
    assert not grey(0)
    # a row destroyed with its page is forgotten: a widget made later at its address is not greyed
    struct.pack_into('<i', info, 0, 0xff01)
    assert grey(0)
    ui.fire(row, O['EVT_DESTROY'])
    assert not greyed(label.addr, C.byref(C.c_uint(0xffffffff)))
    fw.var('fw_ctx_g_class_type', C.c_int).value = 0xf003
    rig.online = True
    rig.poll()
    assert [grey(i) for i in range(5)] == [False] * 5


def test_now_playings_info_page_has_the_catalogues_genre_and_year_for_a_server_song(rig, fw):
    """A server song plays from its Stream Cache copy, a file no songtable row describes: the info page takes
    the catalogue's genre and year for it (by the copy's path, or the row's own), and nothing for a card file."""
    copy = '/mnt/mmc/.subsonic/stream/s1.flac'
    rig.write(1, [song_row('s1', 'Streamed', 'B', 'A', 1, 100, 1, local=copy)])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    info = fw.fn('catalogue_np_info', C.c_int, C.c_char_p, C.c_char_p, C.POINTER(C.c_int))

    def ask(path):
        genre, year = C.create_string_buffer(96), C.c_int(-1)
        return (genre.value.decode(), year.value) if info(path.encode(), genre, C.byref(year)) else None
    assert ask(copy) == ('Rock', 2001)
    assert ask(url('s1')) == ('Rock', 2001)
    assert ask('/mnt/mmc/Music/a.flac') is None
    assert ask(copy) == ('Rock', 2001)  # asked again: the kept answer
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def test_a_streamed_song_not_yet_played_is_named_by_its_server_row(rig, fw, low):
    """The device, 2026-11-17 23:51: a radio's Up Next named its songs by their downloads' file names, the title
    in lower case and the server's id. A song's download that the catalogue knows takes its server row's title,
    album, artist and album artist before it plays (catalogue_copy_tags), with no read of a file that may not be
    whole yet; a song the catalogue doesn't know is left to its file."""
    copy = '/mnt/mmc/.subsonic/stream/acct/original/ab/take it easy [s1].flac'
    rig.write(1, [song_row('s1', 'Take It Easy', 'Eagles', 'Eagles', 1, 100, 1, album_artist='The Eagles', local=copy)])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    tags = fw.fn('catalogue_copy_tags', C.c_int, C.c_void_p)

    def named(path):
        record = C.create_string_buffer(0x60)
        struct.pack_into('<I', record, O['REC_PATH'], low(path.encode()))
        found = tags(record)
        field = lambda o: C.string_at(struct.unpack_from('<Q', record, o)[0])  # the artist, the album artist: no
        return found, (field(O['REC_ARTIST']), field(O['REC_ALBUM_ARTIST'])) if found else None  # field after them
    assert named(copy) == (1, (b'Eagles', b'The Eagles'))
    assert named('/mnt/mmc/.subsonic/stream/acct/original/ab/other [s9].flac') == (0, None)
    assert rig.sqls[-1].startswith(b'select name,album,songer,albumsonger from cat.serverall where fileurl=')
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def test_now_playings_info_query_ends_at_the_rows_path(rig, fw):
    """The query is built on the stack: a shorter path asked after a longer one must not carry the longer one's
    tail into its SQL (the query failed, so the page had no genre or year, wherever the stack held old bytes)."""
    rig.write(1, [song_row('s1', 'Short', 'B', 'A', 1, 100, 1),
                  song_row('s22', 'Long', 'B', 'A', 1, 100, 2, album_id='al1-with-a-long-id')])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    info = fw.fn('catalogue_np_info', C.c_int, C.c_char_p, C.c_char_p, C.POINTER(C.c_int))

    def ask(path):
        genre, year = C.create_string_buffer(96), C.c_int(-1)
        return (genre.value.decode(), year.value) if info(path.encode(), genre, C.byref(year)) else None
    assert ask(url('s22', 'al1-with-a-long-id')) == ('Rock', 2001)
    assert ask(url('s1')) == ('Rock', 2001)
    assert rig.sqls[-1].endswith(b"where fileurl='" + url('s1').encode() + b"'")
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def test_a_server_albums_cover_is_the_apps_by_its_album_id(rig, fw):
    """The app keeps /mnt/mmc/.<id>/covers/<album id>.jpg (docs/server-library.md#covers): a server row's path,
    or its offline copy's, names it, and the Library's album rows and grids (grid_cover) thumbnail it with
    stock's thumbnailer, as they would a card album's art."""
    copy = '/mnt/mmc/.subsonic/stream/s1.flac'
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, album_id='al1', local=copy),
                  song_row('s2', 'Two', 'B', 'C', 1, 100, 2, album_id='al2')])
    rig.poll()
    cover = '/mnt/mmc/.subsonic/covers/al1.jpg'
    rig.files.files[cover] = b'jpeg'
    find = fw.fn('catalogue_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_uint)

    def ask(path):
        out = C.create_string_buffer(512)
        return out.value.decode() if find(path.encode(), out, 512) else None
    assert ask(url('s1', 'al1')) == cover and ask(copy) == cover
    assert ask(url('s2', 'al2')) is None  # not fetched yet: the placeholder
    assert ask('/mnt/mmc/Music/a.flac') is None
    thumbs = []
    fw.mock('toolsThumbSpecCover', lambda src, dst, *_: thumbs.append(C.string_at(src).decode()) or 1)
    album = fw.fn('coverflow_album_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)
    assert album(url('s1', 'al1').encode(), b'/tmp/x', 100, 100) == 1 and thumbs == [cover]
    assert album(url('s2', 'al2').encode(), b'/tmp/x', 100, 100) == 0 and thumbs == [cover]


def test_a_streaming_songs_download_is_its_server_row_by_the_sid_in_its_name(rig, fw):
    """The app streams a server song from its stream cache, /mnt/mmc/.<app>/stream/<hash>/original/0/<title>
    [<sid>].<suffix>, a file catalogue.avail does not name (the player's id 0; the device, 2026-11-16: Control
    Center's art blank for 'Heart Shaped Box'). The sid in its name makes it the server row (stream_path.h), so its
    album's cover, its play key and its hearts are the row's, as for an offline copy; a sid the catalogue lacks, a
    sid with a glob or path character, another app's folder or a card file are none."""
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, album_id='al1'),
                  song_row('s-2_X', 'Two', 'B', 'C', 1, 100, 2, album_id='al2')])
    rig.poll()
    rig.files.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'jpeg'
    rig.files.files['/mnt/mmc/.subsonic/covers/al2.jpg'] = b'jpeg'
    find = fw.fn('catalogue_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_uint)

    def ask(path):
        out = C.create_string_buffer(512)
        return out.value.decode() if find(path.encode(), out, 512) else None
    stream = '/mnt/mmc/.subsonic/stream/9f3a/original/0/'
    assert ask(stream + 'One [s1].flac') == '/mnt/mmc/.subsonic/covers/al1.jpg'
    assert ask(stream + 'Two (live) [x] [s-2_X].mp3') == '/mnt/mmc/.subsonic/covers/al2.jpg'  # the last brackets
    for other in (stream + 'Three [s3].flac', stream + 'One [s*].flac', stream + 'One [].flac', stream + 'One.flac',
                  '/mnt/mmc/.other/stream/9f3a/original/0/One [s1].flac', '/mnt/mmc/.subsonic/cache/One [s1].flac',
                  '/mnt/mmc/Music/One [s1].flac'):
        assert ask(other) is None, other
    key = fw.fn('catalogue_song_key', C.c_uint, C.c_void_p, C.c_uint)
    assert key(rig.record(stream + 'One [s1].flac'), 7) == listen_key(url('s1'))
    assert key(rig.record('/mnt/mmc/Music/One [s1].flac'), 7) == 7


def test_a_streaming_songs_cover_is_the_players_else_its_apps(rig, fw):
    """song_cover_url (coverflow.c), which Control Center and the modal header share with Home's pane: a streaming
    song's download the player parsed shows the player's file (its embedded picture); one it found no picture in,
    or has not parsed yet, its app's cover of the album; with none fetched, nothing (the placeholder). The player's
    cover thread finds the app's cover for it too (coverflow_player_cover), so Home and Now Playing show it."""
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, album_id='al1')])
    rig.poll()
    path = '/mnt/mmc/.subsonic/stream/9f3a/original/0/One [s1].flac'
    r = rig.record(path)
    cover = fw.fn('song_cover_url', C.c_int, C.c_void_p, C.c_char_p, C.c_uint)

    def ask(kind):
        fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = path.encode() if kind else b''
        fw.var('g_playcover_type', C.c_ubyte).value = kind
        out = C.create_string_buffer(600)
        return out.value.decode() if cover(r, out, 600) else None
    assert ask(3) is None and ask(0) is None  # no app cover fetched: the placeholder
    rig.files.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'jpeg'
    assert ask(1) == 'file:///tmp/coverpic.jpg'  # the stream file's embedded picture
    assert ask(3) == ask(0) == 'file:///mnt/mmc/.subsonic/covers/al1.jpg'
    # the player's cover thread, after the download's embedded art failed: the app's cover goes to ffmpeg first
    fw.mock('toolsGetExternCover', lambda *_: 0)
    forks = []
    fw.mock('fw_fork', lambda *_: forks.append(1) and -1)
    fw.fn('coverflow_player_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(path.encode(), b'/tmp/externpic.jpg', 300, 300)
    assert len(forks) == 2  # the app's cover, then the probe; before, the probe alone


def group_record(rig, field, name, rid=1):
    """A row of Search's Artists, Albums or Genres tab as its callback fills it: a directory's type, its id
    and the name in its tab's field alone (the others overlap it as host pointers)."""
    r = C.create_string_buffer(O['REC_SIZE'] + 8)
    s = C.create_string_buffer(name.encode())
    rig.keep += [r, s]
    struct.pack_into('<i', r, O['REC_ID'], rid)
    struct.pack_into('<i', r, O['REC_TYPE'], 4)
    struct.pack_into('<Q', r, O[field], C.addressof(s))
    return C.addressof(r)


def test_searchs_artists_albums_and_genres_rows_are_greyed_offline_when_nothing_of_theirs_can_play(rig, fw):
    """Search's other tabs list artists, albums and genres (one record each, the name in its tab's field):
    offline, one none of whose songs is on the card or has an offline copy is greyed, by its name as the
    Library groups it (an album artist counts as the artist); online, none is."""
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Card Band', 'Mine', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'Streamed', 'Server Band', 'Server Only', 1, 100, 1, album_id='al1'),
                  song_row('s2', 'Cached', 'Cached Band', 'Some Cached', 1, 100, 2, album_id='al2',
                           local='/mnt/mmc/.subsonic/stream/s2.flac', album_artist='Various'),
                  song_row('s3', 'Beside Mine', 'card band', 'Elsewhere', 2, 100, 3, album_id='al3')])
    rig.poll()
    from test_nav_painters import Ui
    ui = Ui(fw)
    row = ui.add('list_item', None)
    label = ui.add('label', row)
    mark = fw.fn('catalogue_mark', None, C.c_void_p, C.c_void_p, C.c_int)
    greyed = fw.fn('catalogue_greyed', C.c_int, C.c_void_p, C.POINTER(C.c_uint))
    tabs = {'REC_ARTIST': 1, 'REC_ALBUM': 2, 'REC_STYLE': 3}

    def grey(field, name, rid=1):
        mark(row.addr, group_record(rig, field, name, rid), tabs[field])
        return bool(greyed(label.addr, C.byref(C.c_uint(0xffffffff))))
    rows = [('REC_ARTIST', 'server band'), ('REC_ARTIST', 'Card Band'), ('REC_ARTIST', 'Various'),
            ('REC_ARTIST', 'Cached Band'), ('REC_ARTIST', 'Not Known'),
            ('REC_ALBUM', 'Server Only'), ('REC_ALBUM', 'some cached'), ('REC_ALBUM', 'Mine'),
            ('REC_STYLE', 'Rock')]
    # Rock: the server's genre, one song of it cached
    assert [grey(*r) for r in rows] == [True, False, False, False, False, True, False, False, False]
    assert not grey('REC_ARTIST', 'Server Band', -1)  # an Unknown row
    # the copy evicted: its album, its artists and the genre have nothing left to play
    rig.files.files[AVAIL] = b'-s2\n'
    rig.poll()
    assert [grey(*r) for r in rows] == [True, False, True, True, False, True, True, False, True]
    # the card's songs have no genre: give one a server genre's name and it plays
    db = sqlite3.connect(rig.tmp/'database.db')
    db.execute("update songtable set style='ROCK'")
    db.commit()
    db.close()
    rig.write(2, [song_row('s1', 'Streamed', 'Server Band', 'Server Only', 1, 100, 1, album_id='al1')])
    rig.poll()
    assert not grey('REC_STYLE', 'Rock') and grey('REC_ARTIST', 'Server Band')
    rig.online = True
    rig.poll()
    assert not any(grey(*r) for r in rows)


@pytest.mark.parametrize('page', ['mostplayed_page', 'rated_page', 'search_dialog'])
def test_most_played_the_ratings_lists_and_searchs_tabs_grey_their_rows_as_they_paint(rig, fw, st, page):
    """Most Played's and a Ratings list's songs (page_tracks) and Search's results (each tab's rows in
    p_deque_searchshowlist) are a scroll_view's rows, one record each: as the list paints, a row that can't
    play now is greyed (grey_rows), the others not."""
    from test_nav_painters import Canvas, Ui, library
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Card Band', 'Mine', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'Streamed', 'Server Band', 'Server Only', 1, 100, 1)])
    rig.poll()
    ui = Ui(fw)
    view, rows = library(fw, ui, 2, name=page)
    win = C.c_void_p.from_address(view.addr + O['W_PARENT']).value
    if page == 'search_dialog':  # the Albums tab lit
        tabs = ui.add('view', ui.nodes[win], (0, 0, 375, 26), 'tab_button_group')
        for lit in (0, 0, 1, 0):
            ui.add('button', tabs, (0, 0, 90, 26), value=lit)
        records = [group_record(rig, 'REC_ALBUM', 'Mine'), group_record(rig, 'REC_ALBUM', 'Server Only')]
        fw.var('fw_ctx_p_deque_searchshowlist', C.c_uint).value = rig.rows.new(records)
    else:
        st('mp_page' if page == 'mostplayed_page' else 'rt_page', C.c_void_p).value = win
        st('mp_list' if page == 'mostplayed_page' else 'rt_list', C.c_void_p).value = rig.rows.new(
            [rig.record('/mnt/mmc/Music/a.flac'), rig.record(url('s1'))])
    fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(view.addr, Canvas(fw).addr)
    greyed = fw.fn('catalogue_greyed', C.c_int, C.c_void_p, C.POINTER(C.c_uint))
    assert [bool(greyed(b.children[0].addr, C.byref(C.c_uint(0xffffffff)))) for b in rows] == [False, True]



def test_now_playing_shows_a_pressed_server_song_until_the_player_parses_one(rig, fw):
    """Online, a press hands the list to the app and nothing loads until its .play (or the 4 s fallback):
    meanwhile Now Playing shows the pressed song, not the last one, with the app's cover of its album, and
    the song playing stops. It shows the player's song again once the player parses one, or when nothing
    could play, or after CATPLAY_SHOW_MS, and stock's page writes its own title and artist then."""
    rig.online = True
    rig.write(1, [song_row('s1', 'Cached', 'B', 'A', 1, 100, 1, local='/mnt/mmc/.subsonic/stream/s1.flac'),
                  song_row('s2', 'Streamed', 'Band', 'Album', 2, 100, 2, album_id='al2')])
    rig.poll()
    rig.files.files['/mnt/mmc/.subsonic/covers/al2.jpg'] = b'jpeg'
    status = [2]
    fw.mock('mclGetPlayStatus', lambda *_: status[0])
    fw.mock('mclStop', lambda *_: status.__setitem__(0, 1))
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(fn) or len(timers))
    from test_nav_painters import Ui
    ui = Ui(fw)
    win = ui.window('playing_page')
    labels = [ui.add('hscroll_label', win, name=n) for n in ('scrlabel_title', 'scrlabel_artist')]
    for label, old in zip(labels, ('Last Song', 'Last Band')):
        ui.set_text(label, old)
    now = fw.fn('catalogue_np_now', C.c_void_p, C.POINTER(C.c_uint), C.POINTER(C.c_uint))
    cover = fw.fn('catalogue_np_cover', C.c_char_p)
    text = fw.fn('catalogue_np_text', None, C.c_void_p)
    refresh = fw.var('g_forcerefresh_flag', C.c_ubyte)
    parses = fw.var('id3_parses', C.c_uint)

    def shown():
        at, n = C.c_uint(), C.c_uint()
        r = now(C.byref(at), C.byref(n))
        return r and (r, at.value, n.value, cover().decode())

    pressed = rig.record(url('s2', 'al2'))
    q = rig.rows.rows([rig.record('/mnt/mmc/Music/a.flac'), pressed, rig.record(url('s1'))])
    assert not shown()
    assert load(rig, q, 1)[0] == 2
    assert status == [2]  # stock's player_start, after the load, plays the last queue on
    C.CFUNCTYPE(C.c_int, C.c_void_p)(timers.pop())(None)
    assert status == [1]  # then it stops
    r, *place = shown()
    assert place == [1, 3, 'file:///mnt/mmc/.subsonic/covers/al2.jpg']
    assert C.c_char_p.from_address(r + O['REC_PATH']).value == url('s2', 'al2').encode()
    # its title and artist, as the pressed row has them (the title's host pointer overlaps the path's,
    # which is not read again)
    tags = [C.create_string_buffer(t) for t in (b'Streamed', b'Band')]
    rig.keep += tags
    C.c_void_p.from_address(r + O['REC_TITLE']).value = C.addressof(tags[0])
    C.c_void_p.from_address(r + O['REC_ARTIST']).value = C.addressof(tags[1])
    text(win.addr)
    assert [ui.text(label) for label in labels] == ['Streamed', 'Band']
    ui.set_text(labels[0], 'Last Song')  # stock's own refresh wrote its title again: written back
    text(win.addr)
    assert [ui.text(label) for label in labels] == ['Streamed', 'Band']
    rig.files.files[f'/tmp/q2-{APP}.play'] = b'2 /mnt/mmc/.subsonic/queue.m3u'
    rig.poll(5)  # answered: still shown while the app's song loads
    assert shown() and not refresh.value
    parses.value += 1  # the player parsed the app's first song
    assert not shown() and refresh.value == 1
    ui.set_text(labels[0], 'Next Song')  # stock's refresh: its labels are left alone
    text(win.addr)
    assert [ui.text(label) for label in labels] == ['Next Song', 'Band']
    # no cover fetched: stock's placeholder; nothing playable when the 4 s pass: the last song's again
    refresh.value = 0
    assert load(rig, rig.rows.rows([rig.record(url('s1'))]), 0)[0] == 2 and shown()[3] == 'play_defaultcover'
    rig.online = False
    rig.files.files[AVAIL] = b'-s1\n'
    rig.poll(5)
    assert rig.toasts[-1] == b'Not available offline' and not shown() and refresh.value == 1
    # never parsed: given up after CATPLAY_SHOW_MS
    rig.online = True
    rig.poll()
    assert load(rig, q, 1)[0] == 2 and shown()
    rig.files.files[f'/tmp/q2-{APP}.play'] = b'3 /mnt/mmc/.subsonic/queue.m3u'
    rig.now += 59000
    assert shown()
    rig.now += 1000
    assert not shown()


def test_the_car_remote_names_a_pressed_server_song_at_the_press_not_once_it_plays(rig, fw, recs):
    """The device and the car, 2026-11-18 00:24: Shuffle Songs from the car pressed a server song still to download;
    the Q2 Muse's Now Playing named it at once (catalogue_np_now) but the car's NowPlaying (coda_rc_shim.c's
    /tmp/coda-rc.now) kept the queue's song until the player started it, 2 to 3 s later, after its buffering. The
    shim writes the pressed song from the press: its id, title, artist, album, path (the daemon's art id) and its
    server length, at 0, unrated while it is not the player's; then the player's song again."""
    rig.online = True
    rig.write(1, [song_row('s2', 'Streamed', 'Band', 'Album', 2, 100, 2, album_id='al2')])
    rig.poll()
    status = [2]
    fw.mock('mclGetPlayStatus', lambda *_: status[0])
    fw.mock('mclStop', lambda *_: status.__setitem__(0, 1))
    fw.mock('timer_add', lambda fn, ctx, ms, *_: 1)
    config = rig.config
    fw.mock('toolsReadConfig', lambda path, section, key, out, default, *_: C.memmove(out, b'1\0', 2)
            if C.string_at(key) == b'CARREMOTE' else config(path, section, key, out, default))
    poll = fw.fn('coda_rc_poll', None)

    def now():
        rig.now += 600
        poll()
        return dict(line.split('=', 1) for line in rig.files.files['/tmp/coda-rc.now'].decode().splitlines())

    assert now()['title'] == ''  # nothing playing
    pressed = rig.record(url('s2', 'al2'))
    C.c_int.from_address(pressed + O['REC_ID']).value = 0x40000002
    assert load(rig, rig.rows.rows([pressed]), 0)[0] == 2
    at, n = C.c_uint(), C.c_uint()
    shown = fw.fn('catalogue_np_now', C.c_void_p, C.POINTER(C.c_uint), C.POINTER(C.c_uint))(C.byref(at), C.byref(n))
    # its path and tags on the shown copy as test_nav_hold's Records lay them out (a 64-bit P() reads each field
    # with the next as its high half)
    named = recs(id_=0x40000002, path=url('s2', 'al2'), name='s2.flac', title='Streamed', artist='Band', album='Album')
    C.memmove(shown + O['REC_NAME'], named + O['REC_NAME'], 0x2c + 4 - O['REC_NAME'])
    np = now()
    assert (np['id'], np['title'], np['artist'], np['album'], np['path']) == (
        str(0x40000002), 'Streamed', 'Band', 'Album', url('s2', 'al2'))
    assert (np['pos'], np['len'], np['hearts']) == ('0', '100', '-1')
    fw.var('id3_parses', C.c_uint).value += 1  # the player parsed a song: its own again
    assert now()['title'] == ''


def test_the_players_title_comes_back_after_a_pressed_song_though_stocks_flag_is_taken(rig, fw, mcl):
    """The device, 2026-11-17: Shuffle Songs pressed a server song still to download, Next played another, and the
    pressed song's title stayed on it. Stock's update writes its title only on g_forcerefresh_flag, and the Songs
    page's timer under Now Playing clears that flag first. The player's title and artist are written back, once
    the player has parsed the song playing (g_play_id3_info names its path), not before."""
    rig.online = True
    rig.write(1, [song_row('s2', 'Streamed', 'Band', 'Album', 2, 100, 2, album_id='al2')])
    rig.poll()
    fw.mock('mclGetPlayStatus', lambda *_: 1)
    fw.mock('timer_add', lambda *_: 1)
    from test_nav_painters import Ui
    ui = Ui(fw)
    win = ui.window('playing_page')
    labels = [ui.add('hscroll_label', win, name=n) for n in ('scrlabel_title', 'scrlabel_artist')]
    text = fw.fn('catalogue_np_text', None, C.c_void_p)
    parses, refresh = fw.var('id3_parses', C.c_uint), fw.var('g_forcerefresh_flag', C.c_ubyte)
    assert load(rig, rig.rows.rows([rig.record(url('s2', 'al2'))]), 0)[0] == 2
    at, n = C.c_uint(), C.c_uint()
    shown = fw.fn('catalogue_np_now', C.c_void_p, C.POINTER(C.c_uint), C.POINTER(C.c_uint))(C.byref(at), C.byref(n))
    # its title and artist, set on the shown copy after the load (the title's host pointer overlaps the path's)
    tags = [C.create_string_buffer(t) for t in (b'Streamed', b'Band')]
    rig.keep += tags
    C.c_void_p.from_address(shown + O['REC_TITLE']).value = C.addressof(tags[0])
    C.c_void_p.from_address(shown + O['REC_ARTIST']).value = C.addressof(tags[1])
    text(win.addr)
    assert [ui.text(label) for label in labels] == ['Streamed', 'Band']
    # Next: another queue's song, B, plays; the player parses it, which ends the pressed song
    b = rig.record('/mnt/mmc/Music/B.flac')
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = rig.rows.rows([b])
    mcl.pos = 0
    info = fw.var('fw_ctx_g_play_id3_info', C.c_char * 2716)
    parses.value += 1
    text(win.addr)  # its tags not parsed yet (the path is written last): nothing written
    assert [ui.text(label) for label in labels] == ['Streamed', 'Band'] and refresh.value == 1
    refresh.value = 0  # the Songs page's timer took the flag: stock's update never writes its title
    C.memmove(C.addressof(info) + O['ID3_TITLE'], b'Song B\0', 7)
    C.memmove(C.addressof(info) + O['ID3_ARTIST'], b'Band B\0', 7)
    info.value = b'/mnt/mmc/Music/B.flac'
    text(win.addr)
    assert [ui.text(label) for label in labels] == ['Song B', 'Band B']
    ui.set_text(labels[0], 'Stock')  # written once: stock's own writes after are left alone
    text(win.addr)
    assert ui.text(labels[0]) == 'Stock'


def test_cover_flow_shows_server_albums_with_the_apps_cover_and_greys_them_offline(rig, fw):
    """With its app signed in, Cover Flow's album queries read the server's songs too (as the Library's
    Albums list does), so its carousel has the server's albums; a server album's art is its app's cover,
    built only once the app has fetched it (the placeholder until then, no marker); and the centred
    album's captions are greyed, with the offline mark, while none of its songs can play."""
    from test_coverflow import Cf
    from test_nav_lists import Loads
    from test_nav_painters import Canvas, Ui
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Card Band', 'Mine', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'Streamed', 'Band', 'Server Only', 1, 100, 1, album_id='al1'),
                  song_row('s2', 'Other', 'Band', 'Not Fetched', 1, 100, 2, album_id='al2')])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    loads = Loads(fw)
    query = fw.fn('ringnav_library_query', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_int)
    fw.mock('toolsQueryDbTable', lambda db, sql, row, arg, *_: query(db, sql, row, arg))
    split = fw.fn('coverflow__split', C.c_void_p, C.c_void_p)  # ALBUM_SQL: each album's own row
    split(rig.rows.rows([]))
    assert sorted(r[1] for r in rig.query(loads.ran[-1])) == ['Mine', 'Not Fetched', 'Server Only']
    tsv = rig.files.files[TSV]
    rig.sign_out()
    split(rig.rows.rows([]))
    assert 'servertable' not in loads.ran[-1]  # signed out: the card's, as before
    rig.files.files[f'/mnt/mmc/.{APP}/credentials'], rig.files.files[TSV] = b'{}', tsv  # signed in again
    rig.poll()

    # the art: the app's cover, thumbnailed into the cache; none fetched yet, nothing written
    rig.files.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'\xff\xd8 baseline'
    fw.mock('toolsThumbSpecCover', lambda src, dst, w, h, *_: rig.files.files.__setitem__(
        C.string_at(dst).decode(), C.string_at(src) + b' %d' % w) or 1)

    class Job(C.Structure):
        _fields_ = [('track', C.c_char_p), ('key', C.c_uint), ('shared', C.c_int)]
    build = fw.fn('coverflow__build_art', None, C.c_void_p)
    for key, sid, album in ((0x11, 's1', 'al1'), (0x22, 's2', 'al2')):
        build(C.byref(Job(url(sid, album).encode(), key, 1)))
    art = {k: v for k, v in rig.files.files.items() if k.startswith('/mnt/mmc/.coverflow/')}
    assert art == {'/mnt/mmc/.coverflow/00000011.jpg': b'/mnt/mmc/.subsonic/covers/al1.jpg 160'}

    # the captions: greyed, the name narrowed a mark's width a side, the mark at its right
    ui = Ui(fw)
    covers = ui.add('view', ui.window('coverflow_page'), (0, 0, 375, 300))
    name, artist = ui.add('hscroll_label', covers, (36, 250, 303, 26)), ui.add('label', covers, (36, 276, 303, 20))
    cf = fw.var('cf', Cf)
    cf.covers, cf.name, cf.artist = covers.addr, name.addr, artist.addr
    caption = fw.fn('coverflow__caption_offline', None, C.c_int)
    greyed = fw.fn('catalogue_greyed', C.c_int, C.c_void_p, C.POINTER(C.c_uint))
    caption(1)
    assert cf.offline == 1 and name.box[0] == 64 and name.box[2] == 247
    assert greyed(name.addr, C.byref(C.c_uint(0xffffffff))) and greyed(artist.addr, C.byref(C.c_uint(0xff8a8a8a)))
    canvas = Canvas(fw)
    fw.fn('coverflow_paint', None, C.c_void_p, C.c_void_p)(covers.addr, canvas.addr)
    xs = {x for (x, y) in canvas.px if 250 <= y < 276}
    assert xs and min(xs) >= 64 + 247 and max(xs) < 375 - 36 + 5
    caption(0)
    assert cf.offline == 0 and name.box[0] == 36 and name.box[2] == 303
    assert not greyed(name.addr, C.byref(C.c_uint(0xffffffff)))
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def test_a_server_track_pressed_in_cover_flow_plays_rather_than_saying_storage_unavailable(rig, fw):
    """Cover Flow's track rows check the card is there before they play: a server row has no file, so it
    goes on to play_folder (ringnav_load hands it to the app, or says why it can't), as a card track does."""
    from test_coverflow import Cf
    rig.write(1, [song_row('s1', 'Streamed', 'Band', 'Server Only', 1, 100, 1)])
    rig.poll()
    cf = fw.var('cf', Cf)
    cf.tracks = rig.rows.rows([rig.record(url('s1')), rig.record('/mnt/mmc/Music/gone.flac')])
    play = fw.fn('coverflow__play', C.c_int, C.c_void_p, C.c_void_p)
    play(0, None)
    assert len(rig.played) == 1 and rig.played[-1][1] == 0
    play(1, None)  # a card track whose file is gone: as before, nothing plays
    assert len(rig.played) == 1


def test_now_playing_takes_the_apps_cover_for_a_stream_cache_copy_without_art(rig, fw):
    """A server song plays from its Stream Cache copy (coverflow_player_cover, stock's cover thread, after its
    embedded art failed): stock's folder image and beside_art find nothing in the cache's folder, so the app's
    cover of its album goes to ffmpeg next, before the copy's own embedded probe; with none fetched, only the
    probe runs. Each ffmpeg run is a fork, refused here."""
    copy = '/mnt/mmc/.subsonic/stream/x/s1.flac'
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, album_id='al1', local=copy)])
    rig.poll()
    fw.mock('toolsGetExternCover', lambda *_: 0)
    forks = []
    fw.mock('fw_fork', lambda *_: forks.append(1) and -1)
    player = fw.fn('coverflow_player_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)
    assert player(copy.encode(), b'/tmp/externpic.jpg', 300, 300) == 0 and len(forks) == 1  # the probe
    rig.files.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'\xff\xd8'
    ff = []
    fw.mock('fw_fork', lambda *_: ff.append(1) and -1)
    player(copy.encode(), b'/tmp/externpic.jpg', 300, 300)
    assert len(ff) == 2  # the app's cover, then the probe
    rig.files.files['/mnt/mmc/Music/a.flac'] = b''
    ff.clear()
    player(b'/mnt/mmc/Music/a.flac', b'/tmp/externpic.jpg', 300, 300)  # a card file: the probe alone
    assert len(ff) == 1


def myfav(rig):
    return [u for u, in sqlite3.connect(rig.tmp/'database.db').execute('select fileurl from myfav order by id')]


def heart_on_copy(rig, fw, ui, song, mcl, server_fav=False, **kw):
    """Now Playing on a server song's Stream Cache copy (the app's .play plays the copy, a file no Library row
    has), with the heart bound and stock's My Fav toggle stubbed."""
    from test_nav_library_pages import FAV_CLICK, Heart, stock_stub
    copy = '/mnt/mmc/.subsonic/stream/acct/orig/o/One [s1].flac'
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, local=copy)])
    rig.poll()
    if server_fav:  # starred on the server: the row's path in myfav (ratings_in), not the copy's
        rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = f'{listen_key(url("s1")):08x}\ts1\tlike=1\t1700000000\n'.encode()
        rig.poll(3)
        assert myfav(rig) == [url('s1')]
    heart = Heart(fw, ui, song, mcl, path=copy.encode(), **kw)
    heart.log = (C.c_long * 3)()  # the stub writes its arguments there: it must outlive the stub
    stock_stub(FAV_CLICK, 0, heart.log)
    return heart


@in_fork
def test_stars_set_on_a_stream_copy_are_the_server_rows_and_go_to_the_server(rig, fw, ui, song, mcl):
    """Three stars set on the rating page while the copy plays are kept under the server row's key (its
    fileurl's listen_key, as play counts are), and its ratings.out line (stars=) names the row's sid, so the app
    sends them and the Library's row has them; the copy's own path keys nothing."""
    heart = heart_on_copy(rig, fw, ui, song, mcl)
    assert heart.rate(3) == 1
    kept = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    row = listen_key(url('s1'))
    assert kept(0, row) == 3 and kept(0, listen_key('/mnt/mmc/.subsonic/stream/acct/orig/o/One [s1].flac')) == 0
    assert heart.files.files[f'/mnt/mmc/.{APP}/ratings.out'] == f'{row:08x}\ts1\tstars=3\t1700000000\n'.encode()
    assert heart.stars() == 3


@in_fork
def test_an_app_that_takes_one_number_is_sent_nothing(rig, fw, ui, song, mcl):
    """ratings=1 (one number, five its star) can carry a like or stars only by changing the other: the Q2 writes it
    no line, and its ratings.in is still read (test_ratings_in_are_applied_unless_the_q2_s_own_change_is_newer)."""
    rig.files.files[f'/mnt/mmc/.{APP}/q2app'] = b'name=Subsonic\ncatalogue=1\nratings=1\n'
    heart = heart_on_copy(rig, fw, ui, song, mcl)
    heart.rate(3)
    heart.fav = True
    ui.handler(heart.heart, EVT_CLICK)(C.addressof(C.create_string_buffer(0x40)))
    assert f'/mnt/mmc/.{APP}/ratings.out' not in heart.files.files


@in_fork
def test_a_heart_tapped_on_a_stream_copy_is_the_server_rows_favourite(rig, fw, ui, song, mcl):
    """A tap is stock's My Fav toggle, which puts the copy's path in myfav (the Favorites list shows the song
    once, as before): its line carries the row's sid, like=1 and then like=0, and the Ratings lists count the
    copy's My Fav as the row's like."""
    from test_nav_library_pages import LIKED, rate_all
    heart = heart_on_copy(rig, fw, ui, song, mcl)
    path = C.c_void_p.from_address(heart.track + O['REC_PATH']).value
    assert path
    copy = C.string_at(path).decode()
    event = C.create_string_buffer(0x40)
    heart.fav = True  # stock's toggle has run when the click reads it back
    ui.handler(heart.heart, EVT_CLICK)(C.addressof(event))
    heart.fav = False
    ui.handler(heart.heart, EVT_CLICK)(C.addressof(event))
    row = listen_key(url('s1'))
    assert heart.files.files[f'/mnt/mmc/.{APP}/ratings.out'] == (f'{row:08x}\ts1\tlike=1\t1700000000\n'
                                                                  f'{row:08x}\ts1\tlike=0\t1700000000\n').encode()
    assert rate_all(fw, ui, [rig.record(url('s1')), rig.record(url('s2'))], favs=[rig.record(copy)]) == [LIKED, 0]


def test_the_ratings_lists_have_rated_server_songs_while_signed_in(rig, fw, ui, song):
    """A Ratings list reads every card song (spoken ones too: getAllMusic(0)'s SQL, no music filter) and, with
    its app signed in, the server's songs beside them, so a server song rated here or on the server is listed
    with its stars; signed out it is not."""
    from test_nav_library_pages import RATINGS, Library, call, ratings
    rig.card_song('/mnt/mmc/audiobooks/b.m4b', 'Book', 'Reader', 'Tales', 1, 900, 1)
    rig.write(1, [song_row('s1', 'Streamed', 'Server Band', 'Server Only', 1, 100, 1),
                  song_row('s2', 'Unrated', 'Server Band', 'Server Only', 2, 100, 2)])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    library = Library(fw, ui)
    query = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)
    ran = []

    def stock_query(db, sql, *_):  # stock's toolsQueryDbTable: the rows into the load's deque
        sql = C.string_at(sql).decode()
        ran.append(sql)
        for path, title in rig.query(sql.replace('select *', 'select fileurl, name', 1)):
            library.deques[library.directory()].append(song(path.encode(), title.encode()))
        return 0
    fw.mock('stock_dbquery_trampoline', stock_query)
    fw.mock('getAllMusic', lambda sort, *_: query(b'db', b'select * from songtable order by name', None, sort))
    fw.mock('getAllFav', lambda *_: 0)
    rig.files.files[RATINGS] = ratings((listen_key('/mnt/mmc/audiobooks/b.m4b'), 2), (listen_key(url('s1')), 4))
    call(fw, 'rated_open', 3)  # All Rated
    assert [t for t, _ in ui.rows(ui.top())] == ['Streamed', 'Book']
    assert 'servertable' in ran[-1]
    ui.close(ui.top())
    rig.sign_out()
    call(fw, 'rated_open', 3)
    assert [t for t, _ in ui.rows(ui.top())] == ['Book']
    rig.lib.sqlite3_close(slot)
    slot.value = 0


@pytest.mark.parametrize('first', [False, True])
def test_a_server_songs_five_hearts_from_the_server_are_its_my_fav(rig, fw, first):
    """ratings.in's five hearts for a server row put the row in My Fav, by its path from the index: whether the
    line comes after the catalogue's import or with it, at the first poll after a boot (the index is built by
    that poll's job, so the line waits for it)."""
    rig.write(1, [song_row('s1', 'Server', 'B', 'A', 1, 100, 1)])
    if not first:
        rig.poll()
    ks = listen_key(url('s1'))
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = f'{ks:08x}\ts1\t5\t1700000000\n'.encode()
    rig.poll(3)
    assert f'/mnt/mmc/.{APP}/ratings.in' not in rig.files.files
    db = sqlite3.connect(rig.tmp/'database.db')
    assert db.execute('select fileurl from myfav').fetchall() == [(url('s1'),)]


@in_fork
def test_a_server_favourite_fills_the_heart_while_its_copy_plays(rig, fw, ui, song, mcl):
    """A star from the server puts the row's path in My Fav; stock draws Now Playing's heart from the copy's own
    path, which isn't there, so the heart's tick fills it by the row. The like is no rating: no stars."""
    heart = heart_on_copy(rig, fw, ui, song, mcl, server_fav=True)
    assert ui.image[heart.heart] == 'play_fav' and heart.stars() == 0


@in_fork
def test_a_tap_on_a_server_favourite_s_heart_takes_the_row_out(rig, fw, ui, song, mcl):
    """The heart filled by the row is emptied by a tap: the row leaves My Fav, the copy never goes in (stock's
    toggle isn't run, so the Favorites list doesn't get the copy), and the server is told like=0."""
    from test_nav_library_pages import FAV_CLICK, stock_stub
    heart = heart_on_copy(rig, fw, ui, song, mcl, server_fav=True)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    ui.handler(heart.heart, EVT_CLICK)(C.addressof(C.create_string_buffer(0x40)))
    assert list(log) == [0, 0, 0] and myfav(rig) == [] and ui.image[heart.heart] == 'play_unfav'
    row = listen_key(url('s1'))
    assert heart.files.files[f'/mnt/mmc/.{APP}/ratings.out'] == f'{row:08x}\ts1\tlike=0\t1700000000\n'.encode()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    ui.fire('rate_tick')  # the row out: the tick leaves it empty
    assert ui.image[heart.heart] == 'play_unfav'


@in_fork
def test_stars_on_a_server_favourite_leave_it_liked(rig, fw, ui, song, mcl):
    """Three stars set on the row's favourite keep three under the row's key; the row stays in My Fav and the
    heart full, stock's toggle never run."""
    from test_nav_library_pages import FAV_CLICK, stock_stub
    heart = heart_on_copy(rig, fw, ui, song, mcl, server_fav=True)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    assert heart.rate(3) == 1
    row = listen_key(url('s1'))
    assert list(log) == [0, 0, 0] and myfav(rig) == [url('s1')] and ui.image[heart.heart] == 'play_fav'
    assert fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)(0, row) == 3 and heart.stars() == 3


@in_fork
@pytest.mark.parametrize('hearts', [4, 0])
def test_a_server_rating_under_five_takes_the_copy_out_of_my_fav_too(rig, fw, ui, song, mcl, hearts):
    """A copy hearted here (stock's toggle puts the copy's own path in myfav) and then rated 4, or unstarred, on
    the server by an app with one number (ratings=1: under five is no star): ratings_in takes the copy's path out
    with the row's, the tick empties the heart stock drew from the copy, and the rating page shows the server's 4
    stars."""
    copy = '/mnt/mmc/.subsonic/stream/acct/orig/o/One [s1].flac'
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, local=copy)])
    rig.poll()
    db = sqlite3.connect(rig.tmp/'database.db')
    db.execute('insert into myfav(fileurl) values (?)', (copy,))
    db.commit()
    db.close()
    from test_nav_library_pages import Heart
    heart = Heart(fw, ui, song, mcl, fav=True, path=copy.encode())
    rig.files.files.update(heart.files.files)
    heart.files.files = rig.files.files  # one card: the rig's access and rename, the heart's reads and writes
    fw.mock('checkFavExist', lambda *_: copy in myfav(rig))  # stock's, by the copy's own path
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    ui.fire('rate_tick')
    assert ui.image[heart.heart] == 'play_fav' and heart.stars() == 0
    row = listen_key(url('s1'))
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = f'{row:08x}\ts1\t{hearts}\t1700000000\n'.encode()
    rig.poll(3)
    assert myfav(rig) == []
    ui.fire('rate_tick')
    assert ui.image[heart.heart] == 'play_unfav'
    assert heart.stars() == hearts


@in_fork
def test_five_hearts_from_the_server_put_the_row_in_my_fav_in_place_of_its_copy(rig, fw, ui, song, mcl):
    """A copy already in myfav by its own path and starred on the server: the row goes in and the copy comes out,
    so the Favorites list has the song once, and the heart stays full."""
    copy = '/mnt/mmc/.subsonic/stream/acct/orig/o/One [s1].flac'
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, local=copy)])
    rig.poll()
    db = sqlite3.connect(rig.tmp/'database.db')
    db.execute('insert into myfav(fileurl) values (?)', (copy,))
    db.commit()
    db.close()
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = f'{listen_key(url("s1")):08x}\ts1\t5\t1700000000\n'.encode()
    rig.poll(3)
    assert myfav(rig) == [url('s1')]
    from test_nav_library_pages import Heart
    heart = Heart(fw, ui, song, mcl, path=copy.encode())  # stock drew it from the copy's own path: empty
    fw.mock('checkFavExist', lambda *_: copy in myfav(rig))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    ui.fire('rate_tick')
    assert ui.image[heart.heart] == 'play_fav' and heart.stars() == 0


# The server library has no setting (the user's, 2026-11-19): a signed-in catalogue app's songs are in the Library,
# a signed-out one's are not. Card apps that read /tmp/q2-addon.library (the caps word libsource) find "server".
LIBRARY = '/tmp/q2-addon.library'


def test_the_state_file_says_server_with_the_caps_whatever_an_older_build_saved(rig):
    """Before any app starts (each start scans first, writing the caps); an older build's LIBRARYSOURCE=0 is not read."""
    rig.source = 0
    rig.fw.fn('pod_settings', None)()
    assert LIBRARY not in rig.files.files
    rig.fw.fn('addon_scan', C.c_int)()
    assert rig.files.files[LIBRARY] == b'server\n'


def test_a_signed_out_apps_catalogue_is_not_imported(rig, fw):
    """Its catalogue.tsv on the card without its credentials (an older app that kept it): nothing joins the Library
    and nothing is answered; once signed in, it is imported."""
    del rig.files.files[f'/mnt/mmc/.{APP}/credentials']
    rig.write(1, [song_row('s1', 'Server One', 'Band', 'Album', 2, 180, 5000)])
    rig.poll(3)
    assert not fw.fn('catalogue_has', C.c_int)() and f'/tmp/q2-{APP}.catalogue.done' not in rig.files.files
    rig.files.files[f'/mnt/mmc/.{APP}/credentials'] = b'{}'
    rig.poll()
    assert fw.fn('catalogue_has', C.c_int)() and rig.files.files[f'/tmp/q2-{APP}.catalogue.done'].startswith(b'1 ok')


def test_a_dark_q2_with_nothing_indexed_leaves_the_card_alone(rig, fw):
    """Screen off and no app's rows indexed, the poll looks at no file (17_power_smart_boot's screen-off pass); once
    a signed-in app's rows are in, it polls dark too, as it must to take them out at a sign-out."""
    opened = []
    fw.mock('fw_opendir', lambda path, *_: opened.append(C.string_at(path)) or 0)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    rig.write(1, [song_row('s1', 'Server One', 'Band', 'Album', 2, 180, 5000)])
    rig.poll(3)
    assert opened == [] and not fw.fn('catalogue_has', C.c_int)()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.mock('fw_opendir', rig.opendir)
    rig.poll()
    assert fw.fn('catalogue_has', C.c_int)()
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    del rig.files.files[f'/mnt/mmc/.{APP}/credentials']
    rig.poll(13)
    assert not fw.fn('catalogue_has', C.c_int)()


@pytest.mark.parametrize('how', ['signed out', 'file gone'])
def test_signing_out_takes_the_apps_songs_out_of_the_library(rig, fw, how):
    """Sign Out deletes the credentials and the catalogue (q2-subsonic); either alone is enough: a job with no seq
    copies the old file less the app's rows, answers nothing, and the Library reads the card's alone."""
    rig.write(1, [song_row('s1', 'Server One', 'Band', 'Album', 2, 180, 5000)])
    rig.poll()
    assert fw.fn('catalogue_has', C.c_int)() and rig.query('select count(*) from cat.servermeta') == [(1,)]
    assert cat_head(fw).ix.n == 1 and cat_head(fw).ix.apps == 1  # the mirror (other files' tests use it) reads true
    done = rig.files.files[f'/tmp/q2-{APP}.catalogue.done']
    del rig.files.files[f'/mnt/mmc/.{APP}/credentials' if how == 'signed out' else TSV]
    rig.poll()
    assert not fw.fn('catalogue_has', C.c_int)() and rig.query('select count(*) from cat.servermeta') == [(0,)]
    assert rig.files.files[f'/tmp/q2-{APP}.catalogue.done'] == done  # no answer for a drop
    rig.poll(3)
    assert not fw.fn('catalogue_has', C.c_int)()  # dropped once: no job again


# Cover Flow's empty page (cf_empty.c) and autostart: a catalogue app is signed in while its card folder holds its
# credentials file, looked for and never opened (its state file is in /tmp, gone at boot).
@pytest.mark.parametrize('creds, name', [(True, b'Subsonic'), (False, None)])
def test_a_catalogue_app_is_signed_in_while_its_credentials_file_is_on_the_card(rig, creds, name):
    if not creds:
        del rig.files.files[f'/mnt/mmc/.{APP}/credentials']
    opened = []
    rig.fw.mock('fw_fopen', lambda path, mode, *_: opened.append(C.string_at(path)) or rig.files.fopen(path, mode))
    out, title = C.create_string_buffer(16), C.create_string_buffer(32)
    assert rig.fw.fn('catalogue_signed', C.c_int, C.c_char_p, C.c_char_p, C.c_uint)(out, title, 32) == bool(creds)
    if creds:
        assert out.value == APP.encode() and title.value == name
    assert not [p for p in opened if p.endswith(b'credentials')]  # never read


def test_an_app_without_catalogue_1_is_never_signed_in(rig):
    rig.files.files[f'/mnt/mmc/.{APP}/q2app'] = b'name=Subsonic\n'
    rig.files.files[f'/mnt/mmc/.{APP}/credentials'] = b'{}'
    out, title = C.create_string_buffer(16), C.create_string_buffer(32)
    assert rig.fw.fn('catalogue_signed', C.c_int, C.c_char_p, C.c_char_p, C.c_uint)(out, title, 32) == 0


@pytest.mark.parametrize('tree, music', [
    ({'/mnt/mmc': [('Music', 4)], '/mnt/mmc/Music': [('A', 4)], '/mnt/mmc/Music/A': [('01 Song.FLAC', 8)]}, True),
    ({'/mnt/mmc': [('.subsonic', 4), ('notes.txt', 8)], '/mnt/mmc/.subsonic': [('song.mp3', 8)]}, False),  # a dot-folder
    ({'/mnt/mmc': [('Music', 4)], '/mnt/mmc/Music': [('cover.jpg', 8), ('playlist.m3u', 8)]}, False),
    ({'/mnt/mmc': [('song.opus', 0)]}, True),  # d_type unknown: by its name
])
def test_cover_flow_s_empty_page_looks_for_music_on_the_card(rig, tree, music):
    dirs, opened = {}, []

    def opendir(path, *_):
        p = C.string_at(path).decode()
        opened.append(p)
        if p not in tree:
            return 0
        dirs[len(dirs) + 0x100] = list(tree[p])
        return len(dirs) - 1 + 0x100

    def readdir(d, *_):
        if not dirs.get(d):
            return 0
        name, kind = dirs[d].pop(0)
        ent = C.create_string_buffer(268)
        C.memmove(C.addressof(ent) + 11, name.encode(), len(name))
        ent[10] = kind
        rig.keep.append(ent)
        return C.addressof(ent)
    rig.fw.mock('fw_opendir', opendir)
    rig.fw.mock('fw_readdir', readdir)
    assert rig.fw.fn('cf_empty__card_has_music', C.c_int)() == music
    assert not [p for p in opened if '/.' in p]


def autostart_rig(rig, fw, creds=True):
    rig.files.files[f'/mnt/mmc/.{APP}/q2-{APP}'] = b''
    if not creds:
        del rig.files.files[f'/mnt/mmc/.{APP}/credentials']
    started = []
    fw.mock('fw_system', lambda cmd, *_: started.append(C.string_at(cmd)) or 0)
    return started


def test_a_signed_in_catalogue_app_starts_once_at_boot(rig, fw):
    """Its catalogue, covers and play reports catch up without its page opened; it idle-exits as before."""
    started = autostart_rig(rig, fw)
    rig.poll(1)
    assert started == [f'/bin/sh /mnt/mmc/.{APP}/run &'.encode()] and b'libsource' in rig.files.files['/tmp/q2-addon.caps']
    assert rig.files.files['/tmp/q2-addon.library'] == b'server\n'
    rig.poll(25)  # past the next scans: started once
    assert len(started) == 1


def test_a_signed_out_app_starts_nothing(rig, fw):
    started = autostart_rig(rig, fw, creds=False)
    rig.poll(25)
    assert started == []


def test_a_server_songs_row_is_the_playing_one_by_its_path(rig, fw, mcl, low):
    """docs/oled-design.md#song-rows: a song list names a server song by its row (its fileurl), the queue by the
    Stream Cache copy that plays it; song_playing maps the copy back to its row, by path. Another row of the same
    title is not the playing one, nor is anything while stopped."""
    copy = '/mnt/mmc/.subsonic/stream/s1.flac'
    rig.write(1, [song_row('s1', 'Same Title', 'B', 'A', 1, 100, 1, local=copy),
                  song_row('s2', 'Same Title', 'B', 'A', 2, 100, 2)])
    rig.poll()
    deques, keep = Rows(fw), []

    def record(path):
        r = C.create_string_buffer(O['REC_SIZE'] + 8)
        struct.pack_into('<I', r, O['REC_PATH'], low(path.encode()))
        keep.append(r)
        return C.addressof(r)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new([record(copy)])
    mcl.pos = 0
    status = [2]
    fw.mock('mclGetPlayStatus', lambda *_: status[0])
    playing = fw.fn('song_playing', C.c_int, C.c_void_p)
    assert [playing(record(p)) for p in (url('s1'), url('s2'), copy, '/mnt/mmc/Music/s1.flac')] == [1, 0, 1, 0]
    status[0] = 1
    assert playing(record(url('s1'))) == 0


def test_bluetooth_names_a_server_songs_cover_as_its_app_keeps_it(rig, fw):
    """bt_cover.c: a streamed server song whose album Cover Flow has no copy of gets the cover the app keeps
    (catalogue_cover), the one Now Playing shows, as the car's handle and file; the handle is that file's key."""
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1, album_id='al1')])
    rig.poll()
    cover = '/mnt/mmc/.subsonic/covers/al1.jpg'
    rig.files.files[cover] = b'\xff\xd8'
    id3 = C.create_string_buffer(0x700)
    id3[0x504], id3[0x404] = b'B', b'A'  # ID3_ARTIST, ID3_ALBUM
    stream = b'/mnt/mmc/.subsonic/stream/9f3a/original/0/One [s1].flac'
    fw.fn('bt_cover_note', None, C.c_char_p, C.c_char_p)(id3, stream)
    h = (2166136261 * 16777619) & 0xffffffff  # tags_key('', cover) (coverflow.c): the empty artist, then the path
    for b in cover.encode():
        h = ((h ^ b) * 16777619) & 0xffffffff
    h = (h * 16777619) & 0xffffffff
    assert rig.files.files.get('/tmp/q2-bip.now') == b'%07d\n%s\napp\n%s\n' % (h % 10**7, cover.encode(), stream)


def test_an_untitled_server_song_in_my_fav_takes_its_title_from_the_server_library(rig, fw):
    """My Fav's, Frequent's and Recent's rows are stock's copies of the songs; an untitled one takes the library's
    title by path and CUE start (nav/library_pages.c fav_titles): a server song's from servertable, a card song's
    from songtable, asked of the database for those rows alone, no whole-library load."""
    rig.card_song('/mnt/mmc/Music/a.flac', 'Card Song', 'Me', 'Mine', 1, 200, 1000)
    rig.write(1, [song_row('s1', 'One', 'B', 'A', 1, 100, 1), song_row('s2', "It's Two", 'B', 'A', 2, 100, 1)])
    rig.poll()
    loads = []
    fw.mock('getAllMusic', lambda sort, *_: loads.append(sort) or 0)
    # Records read as the C reads them: a 64-bit P() at REC_PATH takes REC_TITLE's word as its high half, so the
    # strings sit under 4 GB and an untitled row's title word is 0 (a NULL title, which fav_titles may free)
    libc = C.CDLL(None)
    libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]
    libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
    low = 0x3e000000
    assert libc.mmap(low, 0x10000, 3, 0x100022, -1, 0) == low  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE
    at, keep = [low], []

    def text(b):
        C.memmove(at[0], b + b'\0', len(b) + 1)
        at[0] += len(b) + 16
        return at[0] - len(b) - 16

    def row(path, title=None):
        r = C.create_string_buffer(O['REC_SIZE'] + 8)
        keep.append(r)
        struct.pack_into('<I', r, O['REC_PATH'], text(path.encode()))
        if title:
            struct.pack_into('<I', r, O['REC_TITLE'], text(title))
        struct.pack_into('<i', r, O['REC_TYPE'], 8)
        return C.addressof(r)

    rows = [row(url('s1')), row(url('s2')), row('/mnt/mmc/Music/a.flac'), row('/mnt/mmc/Music/gone.flac'),
            row(url('s1'), b'Kept')]
    try:
        fw.fn('navigation__fav_titles', None, C.c_void_p, C.c_int)(rig.rows.new(rows), len(rows))
        titles = [(lambda v: C.string_at(v) if v else None)(C.c_void_p.from_address(r + O['REC_TITLE']).value) for r in rows]
        assert titles == [b'One', b"It's Two", b'Card Song', None, b'Kept'] and not loads
    finally:
        libc.munmap(low, 0x10000)


def test_now_playing_fits_a_server_albums_cover_once_for_all_its_tracks(rig, fw):
    """A server song's Now Playing art is its app's cover of the album through ffmpeg (coverflow_player_cover, under
    g_playcover_mutex, where stock's thumbnailer and its parse_cover_mutex cannot go): the fit is kept, so the
    album's next tracks at that size copy it, no fork; another album, or another size, runs ffmpeg again."""
    copies = [f'/mnt/mmc/.subsonic/stream/x/s{i}.flac' for i in range(1, 5)]
    rig.write(1, [song_row(f's{i}', f'T{i}', 'B', 'A', i, 100, i, local=copies[i - 1]) for i in (1, 2, 3)] +
              [song_row('s4', 'T4', 'B', 'Other', 1, 100, 4, album_id='al2', local=copies[3])])
    rig.poll()
    rig.files.files['/mnt/mmc/.subsonic/covers/al1.jpg'] = b'\xff\xd8one'
    rig.files.files['/mnt/mmc/.subsonic/covers/al2.jpg'] = b'\xff\xd8two'
    fw.mock('toolsGetExternCover', lambda *_: 0)
    forks, dst = [], '/tmp/externpic.jpg'
    fw.mock('fw_fork', lambda *_: forks.append(1) or 4242)
    def waitpid(pid, status, *_):  # ffmpeg's run: its picture at dst.ff, the cover it was asked for
        C.c_int.from_address(status).value = 0
        rig.files.files[dst + '.ff'] = b'fit %d' % len(forks)
        return pid
    fw.mock('fw_waitpid', waitpid)
    player = fw.fn('coverflow_player_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)
    for c in copies[:3]:
        assert player(c.encode(), dst.encode(), 300, 300) == 1 and rig.files.files[dst] == b'fit 1'
    assert len(forks) == 1  # three tracks of al1: one ffmpeg run
    assert player(copies[3].encode(), dst.encode(), 300, 300) == 1 and len(forks) == 2  # al2's own
    assert player(copies[0].encode(), dst.encode(), 300, 300) == 1 and len(forks) == 3  # one album kept
    assert player(copies[1].encode(), dst.encode(), 166, 166) == 1 and len(forks) == 4  # another size


def test_dark_and_idle_the_catalogue_poll_reads_the_app_s_files_every_ten_seconds(rig, fw):
    """The owner's case, a server signed in and indexed: with the screen dark, nothing playing and no catplay
    waiting, catalogue_poll looked at the app's state, catalogue.avail, catalogue.tsv (twice) and ratings.in every
    second. It now does once each CAT_DARK_MS; lit, or playing, still each second. Counted over a dark minute."""
    rig.write(1, [song_row('s1', 'A', 'B', 'C', 1, 100, 1)])
    rig.poll()
    opened, looked = [], []
    files = rig.files
    fw.mock('fw_fopen', lambda path, mode, *_: opened.append(C.string_at(path)) or files.fopen(path, mode))
    fw.mock('fw_access', lambda path, mode, *_: looked.append(C.string_at(path)) or rig.access(path, mode))
    poll = fw.fn('catalogue_poll', None)

    def minute():
        opened.clear(), looked.clear()
        for _ in range(60):
            rig.now += 1000
            poll()
        return len(opened) + len(looked)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    dark = minute()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    lit = minute()
    print(f'catalogue_poll, a minute: dark and idle {dark} opens and accesses, lit {lit}')
    assert dark <= lit // 8
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    fw.mock('mclGetPlayStatus', lambda *_: 2)  # playing: each second, as lit
    assert minute() >= lit // 2


def test_serverall_is_looked_up_by_fileurl_through_an_index(rig, fw):
    """Up Next's rows (catalogue_copy_tags), Now Playing's info (catalogue_np_info) and fav_set read serverall by
    fileurl: an index makes each a search, where the plan was a scan of every row. A catalogue made before the
    index gets it from the next job, which a boot's first poll starts."""
    rig.write(1, [song_row(f's{i}', f'T{i}', 'B', 'A', i + 1, 100, i + 1) for i in range(20)])
    rig.poll()
    plan = lambda: ' '.join(r[3] for r in rig.query(
        f"explain query plan select name,album,songer,albumsonger from cat.serverall where fileurl='{url('s3')}'"))
    print('serverall by fileurl:', plan())
    assert 'USING INDEX' in plan() and 'SCAN' not in plan()
    db = sqlite3.connect(rig.tmp/'catalogue.db')
    db.execute('drop index serverall_url')
    db.commit()
    db.close()
    assert 'SCAN' in plan()  # a file from before the index
    rig.write(2, [song_row(f's{i}', f'T{i}', 'B', 'A', i + 1, 100, i + 1) for i in range(21)])
    rig.poll()
    assert 'USING INDEX' in plan()


def test_an_offline_copy_is_found_by_its_path_s_hash(rig, fw):
    """cat_copy_of walked every index entry with a strcmp per offline copy; it is now a binary search on the copy
    path's hash, its time per lookup printed (9,199 rows, 2,000 of them with an offline copy)."""
    import time
    n = 9199
    rig.write(1, [song_row(f's{i:05}', f'T{i}', 'B', 'A', i + 1, 100, i + 1,
                           local=f'/mnt/mmc/.subsonic/stream/acct/q/{i % 16:x}/T{i} [s{i:05}].flac' if i < 2000 else '')
                  for i in range(n)])
    rig.poll()
    seconds = fw.fn('catalogue_copy_seconds', C.c_int, C.c_char_p)
    paths = [f'/mnt/mmc/.subsonic/stream/acct/q/{i % 16:x}/T{i} [s{i:05}].flac'.encode() for i in range(0, 2000, 7)]
    t = time.perf_counter()
    got = [seconds(p) for p in paths]
    took = (time.perf_counter() - t) / len(paths)
    print(f'cat_copy_of: {took * 1e6:.1f} us a lookup')
    assert got == [100] * len(paths)
    assert seconds(b'/mnt/mmc/.subsonic/stream/acct/q/0/none.flac') == -1


def test_a_catalogue_avail_line_naming_the_copy_a_row_has_adds_no_string(rig, fw):
    """catalogue.avail is appended to as the app syncs, and a song synced again is named again: a line naming the
    copy its row already has adds nothing to the index's strings, which grew by every such line until the next
    import."""
    rig.write(1, [song_row(f's{i}', f'T{i}', 'B', 'A', i + 1, 100, i + 1) for i in range(200)])
    rig.poll()
    avail = f'/mnt/mmc/.{APP}/catalogue.avail'
    lines = ''.join(f's{i}\t/mnt/mmc/.subsonic/stream/a/q/0/T{i} [s{i}].flac\n' for i in range(200)).encode()
    rig.files.files[avail] = lines
    rig.poll(1)
    once = cat_head(fw).ix.len
    rig.files.files[avail] = lines * 3  # synced again, twice
    rig.poll(1)
    print('index strings: after the copies', once, 'after naming them twice more', cat_head(fw).ix.len)
    assert cat_head(fw).ix.len == once


def catalogue_fds(rig):
    """This process's descriptors on the rig's catalogue.db (and .new): what stock's and the job's connections hold."""
    import os
    out = []
    for fd in os.listdir('/proc/self/fd'):
        try:
            target = os.readlink(f'/proc/self/fd/{fd}')
        except OSError:
            continue
        if target.startswith(str(rig.tmp / 'catalogue.db')):
            out.append(target)
    return out


def test_the_card_let_go_of_closes_every_catalogue_connection_until_it_is_back(rig, fw):
    """The first Standby after a boot on the owner's Q2 (2026-11-23) met demo itself holding catalogue.db, so its
    umount failed. catalogue_card_release (card_release.c's) detaches stock's connection and attaches it to no other
    (stock_db_open's, Era's and the counts' connections) until catalogue_card_back; then stock's next query attaches
    it again. On the real SQLite files, by this process's open descriptors."""
    rig.write(1, [song_row('s1', 'Server One', 'Band', 'Album', 2, 180, 5000)])
    rig.poll()
    slot = C.c_void_p.from_address(STOCK_SLOT)
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(slot))
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)
    assert catalogue_fds(rig), "stock's connection holds the catalogue while a server is signed in"
    release = fw.fn('catalogue_card_release', C.c_int)
    assert release() == 0, 'no job running'
    assert not catalogue_fds(rig)
    attach_db = fw.fn('catalogue_attach_db', None, C.c_void_p)
    other = C.c_void_p()
    rig.lib.sqlite3_open(str(rig.tmp/'database.db').encode(), C.byref(other))
    attach_db(other)  # Era's or the counts' connection, opened meanwhile
    fw.fn('catalogue_attach', None, C.c_void_p)(STOCK_SLOT)  # stock_db_open again
    assert not fw.fn('catalogue_rows', C.c_char_p)(), "stock's queries read the card's songs alone"
    assert not catalogue_fds(rig), 'nothing attached while the card is let go of'
    rig.lib.sqlite3_close(other)
    fw.fn('catalogue_card_back', None)()
    assert fw.fn('catalogue_rows', C.c_char_p)() == b' union all select * from servertable'
    assert catalogue_fds(rig)
    rig.lib.sqlite3_close(slot)
    slot.value = 0


def test_a_job_under_way_stops_for_the_card_and_runs_again_once_it_is_back(rig, fw):
    """A boot's import (or a match) on its thread holds catalogue.db and writes catalogue.db.new, a file open for
    writing that the kernel will not remount read-only under. The release stops it at its next statement: its
    connection closed, the new file removed, the app not answered with an error; once the card is back the next poll
    starts it again and it completes."""
    rig.write(1, [song_row('s1', 'Server One', 'Band', 'Album', 2, 180, 5000)])
    stopped = []

    def exec_(db, sql, cb, ctx, err, *_):
        text = C.string_at(sql)
        if text.startswith(b'insert') and not stopped:  # mid-import: the UI thread lets the card go
            stopped.append(fw.fn('catalogue_card_release', C.c_int)())
        return rig.exec(db, sql, cb, ctx, err)
    fw.mock('sqlite3_exec', exec_)
    rig.poll()
    assert stopped == [1], 'a job was running when the card was let go of'
    assert DONE not in rig.files.files, 'the app not answered'
    assert not (rig.tmp / 'catalogue.db.new').exists() and not catalogue_fds(rig)
    assert not fw.fn('catalogue_has', C.c_int)()
    rig.poll(3)
    assert DONE not in rig.files.files, 'no job while the card is let go of'
    fw.fn('catalogue_card_back', None)()
    rig.poll()
    assert rig.files.files[DONE] == b'1 ok 1\n' and rig.shown() == ['Server One']
