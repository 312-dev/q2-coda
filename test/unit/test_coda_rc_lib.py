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

    def __init__(self, L, root, songs=SONGS, favs=(2, 3), server=(), cat=False):
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
            self.serve(root, server, cat)
        self.s = L.session(0)
        self.out_n = ctypes.c_size_t.in_dll(L, 'out_n')
        self.out = (ctypes.c_uint8 * (1 << 20)).in_dll(L, 'out')
        self.root = root

    @staticmethod
    def serve(root, songs, cat=False):
        """Local + Server: config.ini's LIBRARYSOURCE=1 and the catalogue on the card, made with catalogue.c's
        own schema (servertable, and servermeta, which has a track column of its own); cat: the songs at catalogue.c's
        own paths, /mnt/mmc/.subsonic/cat/<album>/<sid>.flac, whose album is the art id's."""
        (root / 'mnt/data/config.ini').write_text('[Q2POD]\nLIBRARYSOURCE=1\n')
        src = (PATCH / 'catalogue.c').read_text()
        schema = src[src.index('static const char *const SCHEMA ='):]
        schema = ''.join(re.findall(r'"((?:[^"\\]|\\.)*)"', schema[:schema.index(';\n')])).replace('nw.', '')
        (root / 'mnt/mmc/.q2pod').mkdir(parents=True)
        cat = sqlite3.connect(root / 'mnt/mmc/.q2pod/catalogue.db')
        cat.executescript(schema)
        for k, (title, artist, album, added) in enumerate(songs, 1):
            url = f'/mnt/mmc/.subsonic/cat/{album.lower()}/s{k}.flac' if cat else f'/mnt/mmc/.subsonic/{k}.flac'
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


class Song(ctypes.Structure):
    _fields_ = [('id', ctypes.c_int), ('secs', ctypes.c_int), ('track', ctypes.c_int), ('title', ctypes.c_char * 160),
                ('artist', ctypes.c_char * 160), ('album', ctypes.c_char * 160), ('art', ctypes.c_char * 48)]


STREAM = '/mnt/mmc/.subsonic/stream/9f2c/original/0/{} [{}].flac'


def flac_with_picture(image):
    """A FLAC's head: the marker and one last metadata block, a PICTURE holding image as a JPEG."""
    mime = b'image/jpeg'
    pic = struct.pack('>II', 3, len(mime)) + mime + struct.pack('>I', 0) + struct.pack('>IIIII', 1, 1, 24, 0, len(image)) + image
    return b'fLaC' + bytes([0x86]) + len(pic).to_bytes(3, 'big') + pic


def test_a_streaming_songs_file_is_its_server_song(lib, tmp_path):
    """A card app's streaming song plays from its download, /mnt/mmc/.<app>/stream/.../<title> [<sid>].flac, with no
    library record: coda-rc sent the file's name as the title, no artist, no id (no heart) and no cover
    (Android Auto, 2026-11-16 14:34). The sid in the brackets names the catalogue's song."""
    Q2(lib, tmp_path, server=SERVER, cat=True)
    lib.lib_stream_song.argtypes = [ctypes.c_char_p, ctypes.POINTER(Song)]
    s = Song()
    assert lib.lib_stream_song(STREAM.format('foxtrot', 's2').encode(), ctypes.byref(s)) == 1
    assert (s.id, s.title, s.artist, s.album, s.secs, s.track, s.art) == (
        0x40000002, b'Foxtrot', b'Eve', b'Third', 200, 2, b'sthird')
    for path in (STREAM.format('foxtrot', 's9'),           # not in the catalogue
                 '/mnt/mmc/.subsonic/cat/third/s2.flac',    # a downloaded song: the library has it
                 '/mnt/mmc/Music/foxtrot [s2].flac',        # brackets in a card file's name
                 STREAM.format('foxtrot', 's2*')):          # not an id
        assert lib.lib_stream_song(path.encode(), ctypes.byref(s)) == 0, path


def test_a_streamed_albums_cover_is_its_stream_files_picture(lib, tmp_path):
    """No covers/<album>.jpg: the album's cover is the picture in the song streaming, the one the Q2 Muse shows."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    image = b'\xff\xd8' + bytes(range(256)) * 4 + b'\xff\xd9'
    f = tmp_path / STREAM.format('foxtrot', 's2').lstrip('/')
    f.parent.mkdir(parents=True)
    f.write_bytes(flac_with_picture(image))
    t, m = q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 160)])
    assert t == T['COVER'] and client.one(m, 1) == len(image) and client.one(m, 3) == image[:4096]
    f.unlink()
    assert q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 160)]) == 'NOT_FOUND'
