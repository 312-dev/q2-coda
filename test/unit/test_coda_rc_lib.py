"""coda-rc's library (patch/coda_rc_lib.c, remote/coda-rc.proto "browse"): Browse and Play over a stock database
(and, Local + Server, a card app's catalogue) made here, with no socket: the library built for this machine with test/unit/coda_rc_host.c and coda_rc_lib_host.c
as the daemon around it, its device paths under a temporary root."""
import ctypes
import pathlib
import re
import sqlite3
import struct
import subprocess

import pytest

import coda_rc_client as client

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc_core.c', PATCH / 'coda_rc_lib.c', PATCH / 'monocypher/monocypher.c',
           PATCH / 'monocypher/monocypher-ed25519.c', ROOT / 'test/unit/coda_rc_host.c',
           ROOT / 'test/unit/coda_rc_lib_host.c']
SQLITE = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0'  # stock's is 3.54; the host's answers the same SQL
T = client.T


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    if not pathlib.Path(SQLITE).exists():
        pytest.skip('no libsqlite3.so.0 to link the library against')
    so = tmp_path_factory.mktemp('coda_rc_lib') / 'lib.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-I', str(PATCH), *map(str, SOURCES), SQLITE, '-o', str(so)], check=True)
    L = ctypes.CDLL(str(so))
    L.session.restype = ctypes.c_void_p
    L.session.argtypes = [ctypes.c_int]
    L.lib_request.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint, ctypes.c_char_p, ctypes.c_size_t,
                              ctypes.c_int, ctypes.c_int]
    return L


SONGS = [  # (id, file, title, artist, album, added): the card's songtable
    (1, 'a/1.flac', 'Alpha', 'Ann', 'First', 100),
    (2, 'a/2.flac', 'Bravo', 'Ann', 'First', 300),
    (3, 'b/3.flac', 'Charlie', 'Bob', 'Second', 350),
    (4, 'b/4.flac', 'Delta', 'Bob', 'Second', 400),
]


class Q2:
    """The device's files under root (a stock database.db), and one session's requests answered in place."""

    def __init__(self, L, root, songs=SONGS, favs=(2, 3), server=()):
        self.L, self.req = L, 0
        (root / 'mnt/data').mkdir(parents=True)
        (root / 'tmp').mkdir()
        ctypes.create_string_buffer(str(root).encode())
        ctypes.memmove(ctypes.addressof(ctypes.c_char.in_dll(L, 'root')), str(root).encode() + b'\0', len(str(root)) + 1)
        db = sqlite3.connect(root / 'mnt/data/database.db')
        cols = ('id integer primary key, filename text, fileurl text, album text, songer text, style text, name text, '
                'albumsonger text, year text, track integer, cd_num integer, time_create integer')
        db.execute(f'create table songtable ({cols})')
        db.execute(f'create table myfav ({cols})')
        db.execute('create table playlist (id integer primary key, name text, fileurl text, cd_num integer)')
        for i, f, title, artist, album, added in songs:
            row = (i, f.split('/')[-1], '/mnt/mmc/' + f, album, artist, 'Rock', title, artist, '2001', i, 1, added)
            db.execute('insert into songtable values (?,?,?,?,?,?,?,?,?,?,?,?)', row)
            if i in favs:
                db.execute('insert into myfav values (?,?,?,?,?,?,?,?,?,?,?,?)', row)
        db.commit()
        db.close()
        if server:
            self.serve(root, server)
        self.s = L.session(0)
        self.out_n = ctypes.c_size_t.in_dll(L, 'out_n')
        self.out = (ctypes.c_uint8 * (1 << 20)).in_dll(L, 'out')
        self.root = root

    @staticmethod
    def serve(root, songs):
        """Local + Server: config.ini's LIBRARYSOURCE=1 and the catalogue on the card, made with catalogue.c's
        own schema (servertable, and servermeta, which has a track column of its own)."""
        (root / 'mnt/data/config.ini').write_text('[Q2POD]\nLIBRARYSOURCE=1\n')
        src = (PATCH / 'catalogue.c').read_text()
        schema = src[src.index('static const char *const SCHEMA ='):]
        schema = ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', schema[:schema.index(';\n')])).replace('nw.', '')
        (root / 'mnt/mmc/.q2pod').mkdir(parents=True)
        cat = sqlite3.connect(root / 'mnt/mmc/.q2pod/catalogue.db')
        cat.executescript(schema)
        for k, (title, artist, album, added) in enumerate(songs, 1):
            url = f'/mnt/mmc/.subsonic/{k}.flac'
            cat.execute('insert into servertable(id, filename, fileurl, album, songer, style, name, albumsonger, year, '
                        'track, cd_num, time_create) values (?,?,?,?,?,?,?,?,?,?,?,?)',
                        (0x40000000 + k, f'{k}.flac', url, album, artist, 'Pop', title, artist, 2002, k, 1, added))
            cat.execute('insert into servermeta(id, fileurl, app, sid, seconds, track) values (?,?,?,?,?,?)',
                        (k, url, 'subsonic', f's{k}', 200, k))
        cat.commit()
        cat.close()

    def ask(self, type_, fields):
        self.req += 1
        self.out_n.value = 0
        p = client.encode(fields)
        assert self.L.lib_request(self.s, type_, self.req, p, len(p), 0, 0) == 0
        data = bytes(self.out[:self.out_n.value])
        n, t, _, req = struct.unpack('>HBBH', data[:6])
        assert req == self.req and len(data) == 2 + n
        m = client.decode(data[6:])
        if t == T['ERROR']:
            return client.ERRORS[client.one(m, 1)]
        return t, m

    def browse(self, node, sort=None, offset=0, limit=50):
        got = self.ask(T['BROWSE'], [(1, node), (2, sort), (3, offset), (4, limit)])
        if isinstance(got, str):
            return got
        _, m = got
        return {'title': client.text(m, 2), 'total': client.one(m, 3, 0),
                'items': [client.item(x) for x in m.get(5, [])], 'sort': client.text(m, 6),
                'sorts': [client.text(client.decode(x), 1) for x in m.get(7, [])], 'actions': m.get(8, [])}

    def play(self, node, sort=None):
        got = self.ask(T['PLAY'], [(1, node), (2, sort)])
        if isinstance(got, str):
            return got
        return (self.root / 'tmp/coda-rc.list').read_text().split()


@pytest.fixture
def q2(lib, tmp_path):
    return Q2(lib, tmp_path)


def titles(listing):
    return [i['title'] for i in listing['items']]


SERVER = [('Echo', 'Eve', 'Third', 500), ('Foxtrot', 'Eve', 'Third', 600)]


@pytest.mark.parametrize('node, want', [
    ('tracks', ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo', 'Foxtrot']),
    ('albums', ['First', 'Second', 'Third']),
    ('artists', ['Ann', 'Bob', 'Eve']),
])
def test_local_and_server_lists_hold_both_libraries(lib, tmp_path, node, want):
    """A catalogue's servermeta has a track column as servertable does: the songs' view must name whose it reads
    (2026-11-16: every browse answered INTERNAL "query" once a card app's catalogue was in, LIBRARYSOURCE=1)."""
    q2 = Q2(lib, tmp_path, server=SERVER)
    assert titles(q2.browse(node)) == want


def test_a_server_album_lists_its_songs_in_track_order(lib, tmp_path):
    q2 = Q2(lib, tmp_path, server=SERVER)
    album, = [i for i in q2.browse('albums')['items'] if i['title'] == 'Third']
    assert titles(q2.browse(album['id'])) == ['Echo', 'Foxtrot']


def test_the_library_offers_favorites_and_recently_added_after_folders(q2):
    lib = q2.browse('library')
    assert [i['id'] for i in lib['items']][-3:] == ['dir:', 'favorites', 'recent'] and lib['total'] == 9
    favorites, recent = lib['items'][-2:]
    assert (favorites['title'], favorites['icon'], favorites['browsable']) == ('Favorites', 8, True)
    assert (recent['title'], recent['icon'], recent['browsable']) == ('Recently Added', 9, True)


def test_favorites_lists_the_hearted_songs_in_the_tracks_orders(q2):
    fav = q2.browse('favorites')
    assert fav['title'] == 'Favorites' and fav['total'] == 2 and titles(fav) == ['Bravo', 'Charlie']
    assert [i['id'] for i in fav['items']] == ['song:t2', 'song:t3'] and all(i['playable'] for i in fav['items'])
    assert fav['sorts'] == ['title', 'artist', 'album', 'added'] and fav['actions'] == [1, 2]  # Play, Shuffle
    assert titles(q2.browse('favorites', sort='added')) == ['Charlie', 'Bravo']  # newest first
    assert q2.play('favorites') == ['2', '3']


def test_favorites_with_none_hearted_is_an_empty_list(lib, tmp_path):
    q2 = Q2(lib, tmp_path, favs=())
    assert q2.browse('favorites')['total'] == 0 and q2.play('favorites') == 'NOT_FOUND'


def test_recently_added_is_the_newest_songs_newest_first(q2):
    recent = q2.browse('recent')
    assert recent['title'] == 'Recently Added' and titles(recent) == ['Delta', 'Charlie', 'Bravo', 'Alpha']
    assert recent['sorts'] == [] and recent['actions'] == [1, 2]
    assert q2.play('recent') == ['4', '3', '2', '1']


def test_recently_added_stops_at_its_cap_with_no_letters(lib, tmp_path):
    many = [(i, f'c/{i}.flac', f'Song {i:03}', 'Cat', 'Big', i) for i in range(1, 151)]
    q2 = Q2(lib, tmp_path, songs=many, favs=())
    recent = q2.browse('recent', limit=100)
    assert recent['total'] == 100 and len(recent['items']) == 100  # RECENT_CAP, and no buckets past SPLIT (120)
    assert recent['items'][0]['title'] == 'Song 150' and recent['items'][-1]['title'] == 'Song 051'
    assert len(q2.play('recent')) == 100


def test_the_proto_names_the_lists_bits_and_icons_as_the_c_does():
    proto = (ROOT / 'remote/coda-rc.proto').read_text()
    header, c = (PATCH / 'coda_rc.h').read_text(), (PATCH / 'coda_rc_lib.c').read_text()
    for name, value in (('CAP_FAVORITES', 2048), ('CAP_RECENT', 4096)):
        assert f'  {name} = {value};' in proto and re.search(rf'#define {name} {value}u', header)
    for name, value in (('ICON_FAVORITES', 8), ('ICON_RECENT', 9)):
        assert f'  {name} = {value};' in proto and re.search(rf'#define {name}_ {value}\b', c)
    daemon = (PATCH / 'coda_rc.c').read_text()
    caps = daemon[daemon.index('uint32_t crc_caps(void)'):]
    assert 'CAP_FAVORITES' in caps[:400] and 'CAP_RECENT' in caps[:400]  # always served: the library alone
