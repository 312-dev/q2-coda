"""The server library (patch/catalogue.c, docs/server-library.md): a card app's catalogue imported
into a real SQLite file, the duplicate rule's three layers, the match again after the card's library
changes, availability, the union the Library reads, the offline queue and shuffle's filter, the
play hand-off, and the ratings files."""
import ctypes as C
import ctypes.util
import sqlite3
import struct

import pytest

from conftest import FNV_SEED, O, Files, Rows

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
        self.source = 1  # LIBRARYSOURCE: Local + Server
        self.card = {}  # a card file's path: (size, seconds, bytes at its start)
        self.lib = C.CDLL(ctypes.util.find_library('sqlite3'))
        self.lib.sqlite3_open.argtypes = [C.c_char_p, C.c_void_p]
        self.lib.sqlite3_exec.argtypes = [C.c_void_p] * 5
        self.lib.sqlite3_close.argtypes = [C.c_void_p]
        self.sqls = []
        self.files = Files(fw, {
            f'/mnt/mmc/.{APP}/q2app': b'name=Subsonic\ncatalogue=1\nratings=1\n',
            '/proc/mounts': b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'})
        db = sqlite3.connect(tmp_path/'database.db')
        db.executescript(f'{SONGTABLE};{PLAYLIST};{MYFAV}')
        db.commit()
        db.close()
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
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
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
        return self.tmp/path[len(CARD_DB):].decode() if path.startswith(CARD_DB) else None

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
    assert rig.files.files[f'/mnt/mmc/.{APP}/ratings.out'] == f'{key:08x}\ts1\t4\t1700000000\n'.encode()


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


def test_online_a_list_is_handed_to_the_app_and_played_offline_if_it_does_not_answer(rig):
    rig.online = True
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


def test_ratings_in_are_applied_unless_the_q2_s_own_change_is_newer(rig, fw):
    rig.card_song('/mnt/mmc/Music/a.flac', 'A', 'B', 'C', 1, 100, 1)
    rig.card_song('/mnt/mmc/Music/b.flac', 'B', 'B', 'C', 2, 100, 1)
    rig.write(1, [song_row('s1', 'Server', 'B', 'A', 1, 100, 1)])
    rig.poll()
    ka, kb, ks = (listen_key(p) for p in ('/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.flac', url('s1')))
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.out'] = f'{kb:08x}\t\t2\t1700000100\n'.encode()
    rig.files.files[f'/mnt/mmc/.{APP}/ratings.in'] = (f'{ka:08x}\t\t5\t1700000000\n'
                                                      f'{kb:08x}\t\t5\t1700000000\n'
                                                      f'{ks:08x}\ts1\t3\t0\n').encode()
    rig.files.files['/mnt/data/ringnav-ratings'] = b''
    rig.poll()
    assert f'/mnt/mmc/.{APP}/ratings.in' not in rig.files.files
    assert f'/mnt/mmc/.{APP}/ratings.in.taking' not in rig.files.files
    db = sqlite3.connect(rig.tmp/'database.db')
    assert db.execute('select fileurl from myfav').fetchall() == [('/mnt/mmc/Music/a.flac',)]
    rate = fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)
    assert rate(0, ks) == 3 and rate(0, kb) == 0  # b's newer change on the Q2 kept


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


STOCK_SLOT = 0xbff000  # stock's shared connection's global: a stock address, so catalogue_attach keeps it


def test_the_library_s_queries_read_the_server_s_songs_only_with_local_and_server(rig, fw):
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
    # Local only: the card's songs alone, and the server's file let go
    fw.var('catalogue_source', C.c_int).value = 0  # pod_click's change, then its call
    fw.fn('catalogue_changed', None)()
    loads.music("select * from songtable where album='Album'")
    assert 'servertable' not in loads.ran[-1]
    assert b'detach cat' in rig.sqls
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


def test_search_finds_server_songs_with_local_and_server(rig, fw):
    """Stock's Search runs SearchMusicByName's SQL through toolsQueryDbTable (library_query) outside a music
    load: with Local + Server it reads the union, so a server song is found; Local only, as stock's."""
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
    loads.query(b'db', b"select * from songtable where album like 'Sticky'", None, 0)  # not a search: as it is
    assert 'servertable' not in loads.ran[-1]
    fw.var('catalogue_source', C.c_int).value = 0
    fw.fn('catalogue_changed', None)()
    loads.query(b'db', search.encode(), None, 0)
    assert loads.ran[-1] == search
    rig.lib.sqlite3_close(slot)
    slot.value = 0
