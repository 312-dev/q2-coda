"""coda-rc's library (patch/coda_rc_lib.c, remote/coda-rc.proto "browse"): Browse and Play over a stock database
(and, Local + Server, a card app's catalogue) made here, with no socket: the library built for this machine with test/unit/coda_rc_host.c and coda_rc_lib_host.c
as the daemon around it, its device paths under a temporary root. CODA_RC_LIB_SOURCE builds another copy of
coda_rc_lib.c, to show a broken one fails here."""
import ctypes
import os
import pathlib
import re
import sqlite3
import struct
import subprocess
import sysconfig
import time

import pytest

import coda_rc_client as client

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc_core.c', pathlib.Path(os.environ.get('CODA_RC_LIB_SOURCE') or PATCH / 'coda_rc_lib.c'), PATCH / 'monocypher/monocypher.c',
           PATCH / 'monocypher/monocypher-ed25519.c', ROOT / 'test/unit/coda_rc_host.c',
           ROOT / 'test/unit/coda_rc_lib_host.c']
SQLITE = f"/usr/lib/{sysconfig.get_config_var('MULTIARCH')}/libsqlite3.so.0"  # stock's is 3.54; the host's answers the same SQL
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
        self.files(root, songs, favs, server, cat)
        ctypes.memmove(ctypes.addressof(ctypes.c_char.in_dll(L, 'root')), str(root).encode() + b'\0', len(str(root)) + 1)
        self.s = L.session(0)
        self.out_n = ctypes.c_size_t.in_dll(L, 'out_n')
        self.out = (ctypes.c_uint8 * (1 << 20)).in_dll(L, 'out')
        self.root = root

    @staticmethod
    def files(root, songs=SONGS, favs=(2, 3), server=(), cat=False):
        """The device's library under root: stock's database.db, and with server the card app's catalogue (serve).
        test_coda_rc_daemon.py makes its daemon's with it too."""
        (root / 'mnt/data').mkdir(parents=True, exist_ok=True)
        (root / 'tmp').mkdir(exist_ok=True)
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
            Q2.serve(root, server, cat)

    @staticmethod
    def serve(root, songs, cat=False):
        """A signed-in server: its catalogue on the card, read whatever an older build saved as LIBRARYSOURCE (0 here:
        there is no setting since 2026-11-19), made with catalogue.c's
        own schema (servertable, and servermeta, which has a track column of its own); cat: the songs at catalogue.c's
        own paths, /mnt/mmc/.subsonic/cat/<album>/<sid>.flac, whose album is the art id's."""
        (root / 'mnt/data/config.ini').write_text('[Q2POD]\nLIBRARYSOURCE=0\n')
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
    (2026-11-16: every browse answered INTERNAL "query" once a card app's catalogue was in)."""
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
    """No covers/<album>.jpg: the album's cover is the picture in the song streaming, the one the Vamp shows."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    image = b'\xff\xd8' + bytes(range(256)) * 4 + b'\xff\xd9'
    f = tmp_path / STREAM.format('foxtrot', 's2').lstrip('/')
    f.parent.mkdir(parents=True)
    f.write_bytes(flac_with_picture(image))
    t, m = q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 160)])
    assert t == T['COVER'] and client.one(m, 1) == len(image) and client.one(m, 3) == image[:4096]
    f.unlink()
    assert q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 160)]) == 'NOT_FOUND'


@pytest.mark.parametrize('shard, quality', [('a', 'original'), ('0', 'mp3-320'), ('f', 'original')])
def test_a_streamed_albums_cover_is_found_in_any_of_the_caches_folders(lib, tmp_path, shard, quality):
    """q2-subsonic keeps a stream at .subsonic/stream/<account>/<quality>/<x>/<title> [<sid>].<ext>, x one hex digit
    of the sid's hash (its cache.go cachePath: 16 folders); the cover's look-up searched only original/0/, so the
    picture in 15 of 16 downloads went unseen and the car showed no cover (2026-11-18 00:24)."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    image = b'\xff\xd8' + bytes(range(256)) * 2 + b'\xff\xd9'
    f = tmp_path / f'mnt/mmc/.subsonic/stream/9f2c/{quality}/{shard}/foxtrot [s2].flac'
    f.parent.mkdir(parents=True)
    f.write_bytes(flac_with_picture(image))
    t, m = q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 160)])
    assert t == T['COVER'] and client.one(m, 3) == image


def test_a_server_playlist_s_art_is_its_server_cover(lib, tmp_path):
    """A server playlist (cat.serverlists) shows its server's cover, the app's covers/pl-<pid>.jpg (Navidrome's
    collage), once the app has it; until then, and for a card playlist always, its first song's album's
    (2026-11-16: every playlist showed its first album's cover in the car)."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    db = sqlite3.connect(tmp_path / 'mnt/data/database.db')
    db.executemany('insert into playlist values (?,?,?,?)', [
        (3, 'Mix', None, None), (4, 'Card', None, None),
        (101, None, '/mnt/mmc/.subsonic/cat/third/s2.flac', 3), (102, None, '/mnt/mmc/a/1.flac', 3),
        (103, None, '/mnt/mmc/b/3.flac', 4)])
    db.commit()
    cat = sqlite3.connect(tmp_path / 'mnt/mmc/.q2pod/catalogue.db')
    cat.executemany('insert into serverlists values (?,?,?,?)', [('subsonic', 'pl7', 3, 0)])
    cat.commit()
    def arts():
        return {i['title']: i.get('art') for i in q2.browse('playlists')['items']}
    first = arts()
    assert first['Mix'] == 'sthird' and first['Card'].startswith('l')  # no cover yet: the first album's
    collage = b'\xff\xd8' + bytes(range(256)) * 20 + b'\xff\xd9'
    cover = tmp_path / 'mnt/mmc/.subsonic/covers/pl-pl7.jpg'
    cover.parent.mkdir(parents=True)
    cover.write_bytes(collage)
    assert arts() == {'Mix': 'ppl7', 'Card': first['Card']}
    t, m = q2.ask(T['GET_COVER'], [(1, 'ppl7'), (2, 0), (3, 4096), (4, 96)])
    assert t == T['COVER'] and client.one(m, 1) == len(collage) and client.one(m, 3) == collage[:4096]
    assert client.text(m, 4) == 'image/jpeg'
    t, m = q2.ask(T['GET_COVER'], [(1, 'ppl7'), (2, 4096), (3, 4096), (4, 96)])
    assert client.one(m, 3) == collage[4096:]
    assert q2.ask(T['GET_COVER'], [(1, 'pnope'), (2, 0), (3, 4096), (4, 96)]) == 'NOT_FOUND'
    cover.unlink()
    assert q2.ask(T['GET_COVER'], [(1, 'ppl7'), (2, 0), (3, 4096), (4, 96)]) == 'NOT_FOUND'


def test_a_big_ask_gets_the_app_s_512_px_cover(lib, tmp_path):
    """The app keeps each server album's cover at 160 px (covers/<album>.jpg, the Q2's) and, after those, at 512 px
    (covers/512/<album>.jpg): a phone asking for more than 160 px gets the 512 one when it is there, else the
    160 one (2026-11-16 17:04: album art looked low-res in the car, a 160 px cover in a ~350 px box)."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    small, big = b'\xff\xd8small\xff\xd9', b'\xff\xd8' + bytes(range(256)) * 30 + b'\xff\xd9'
    covers = tmp_path / 'mnt/mmc/.subsonic/covers'
    (covers / '512').mkdir(parents=True)
    (covers / 'third.jpg').write_bytes(small)
    (covers / '512/third.jpg').write_bytes(big)
    def got(max_px):
        t, m = q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, max_px)])
        assert t == T['COVER']
        return client.one(m, 1), client.one(m, 3)
    assert got(350) == (len(big), big[:4096])
    assert got(160) == (len(small), small)  # the Q2's size: the small one
    (covers / '512/third.jpg').unlink()
    assert got(350) == (len(small), small)  # none at 512 yet


def test_an_artists_radio_is_one_of_its_songs(lib, tmp_path):
    """StartRadio (CAP_RADIO) with an artist node: the shim gets a radio of one of the artist's songs, picked at random
    (song_radio.c seeds by a song); a song id goes as it is, no seed is the playing song, and anything else is refused."""
    q2 = Q2(lib, tmp_path)
    shim_verb = ctypes.c_char.in_dll(lib, 'shim_verb')
    artists = {i['title']: i['id'] for i in q2.browse('artists')['items']}
    seen = set()
    for _ in range(40):
        assert q2.ask(T['START_RADIO'], [(1, artists['Bob'])])[0] == T['ACK']
        seen.add(ctypes.string_at(ctypes.addressof(shim_verb)).decode())
    assert seen == {'radio 3', 'radio 4'}  # Charlie and Delta, Bob's
    for seed, verb in (('song:t2', 'radio 2'), (None, 'radio 0')):
        assert q2.ask(T['START_RADIO'], [(1, seed)])[0] == T['ACK']
        assert ctypes.string_at(ctypes.addressof(shim_verb)).decode() == verb
    assert q2.ask(T['START_RADIO'], [(2, True)])[0] == T['ACK']
    assert ctypes.string_at(ctypes.addressof(shim_verb)).decode() == 'radio stop'
    assert q2.ask(T['START_RADIO'], [(1, 'album:x')]) == 'BAD_REQUEST'
    assert q2.ask(T['START_RADIO'], [(1, 'artist:nobody')]) == 'NOT_FOUND'


def test_a_queued_stream_download_is_named_as_its_server_song(lib, tmp_path):
    """The car, 2026-11-18 00:24: every server song in the Vamp queue read "[Not in the library]". A card app's
    playlist session queues its stream downloads, which have no library id: the shim's queue line is 0 and, after
    tabs, the download's path, title and artist (coda_rc_shim.c cr_queue). The sid in the path's brackets names the
    catalogue's song, as for NowPlaying (lib_stream_song): its id, title, artist, cover and length; a download the
    catalogue has not is the shim's title and artist, not playable by id; a library id that is gone is still said so."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    (tmp_path / 'tmp/coda-rc.queue').write_text(
        '3 1 5\n1\n'
        f'0\t{STREAM.format("foxtrot", "s2")}\tfoxtrot\t\n'
        f'0\t{STREAM.format("hotel", "s9")}\tHotel\tHank\n'
        '77\n'
        '0\n')
    t, m = q2.ask(T['GET_QUEUE'], [(1, 0), (2, 10)])
    assert t == T['QUEUE_RESP'] and (client.one(m, 1), client.one(m, 2), client.one(m, 3)) == (3, 1, 5)
    items = [client.item(x) for x in m.get(5, [])]
    assert [(i['id'], i['title'], i.get('subtitle'), i.get('art')) for i in items] == [
        ('song:t1', 'Alpha', 'Ann', items[0].get('art')),
        (f'song:c{0x40000002}', 'Foxtrot', 'Eve', 'sthird'),
        ('', 'Hotel', 'Hank', None),
        ('', '[Not in the library]', None, None),
        ('', '[Not in the library]', None, None)]
    assert items[1]['duration_ms'] == 200000 and items[1]['playable'] and not items[2]['playable']
    # PlayQueueItem names a row by GetQueue's id: a download's server song is its record's 0 to the shim (cr_jump)
    assert [lib.lib_queue_rid(i, rid) for i, rid in ((1, 0x40000002), (1, 0x40000001), (0, 1), (2, 5))] == [
        0, 0x40000001, 1, 5]


def test_a_server_album_with_no_cover_is_asked_of_its_app(lib, tmp_path):
    """The car wants a server album's cover and the card has none (no covers/<album>.jpg at any size, no picture in a
    streamed song of it): GetCover asks the album's app for it at the car's size (crc_cover_ask, coda_rc.c), and while
    the daemon holds the answer nothing is sent; then lib_cover answers again, without asking, from the file the app
    saved. A card album, a later piece and an album the card has a cover of ask nothing."""
    q2 = Q2(lib, tmp_path, server=SERVER, cat=True)
    asked, hold = ctypes.c_char.in_dll(lib, 'cover_asked'), ctypes.c_int.in_dll(lib, 'cover_hold')
    def last():
        got = ctypes.string_at(ctypes.addressof(asked)).decode()
        ctypes.memset(ctypes.addressof(asked), 0, 1)
        return got
    hold.value = 0
    assert q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 350)]) == 'NOT_FOUND'
    assert last() == 'subsonic third 512'
    assert q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 96)]) == 'NOT_FOUND'
    assert last() == 'subsonic third 160'
    assert q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 10), (3, 4096), (4, 350)]) == 'NOT_FOUND'
    card = [i['art'] for i in q2.browse('albums')['items'] if i['title'] == 'First'][0]
    assert q2.ask(T['GET_COVER'], [(1, card), (2, 0), (3, 4096), (4, 350)]) == 'NOT_FOUND'
    assert last() == ''  # a later piece, a card album: not asked
    song = tmp_path / STREAM.format('foxtrot', 's2').lstrip('/')
    song.parent.mkdir(parents=True)
    song.write_bytes(flac_with_picture(b'\xff\xd8pic\xff\xd9'))
    t, _ = q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 350)])
    assert t == T['COVER'] and last() == ''  # a streamed song's picture: not asked
    song.unlink()
    hold.value = 1
    q2.out_n.value = 0
    p = client.encode([(1, 'sthird'), (2, 0), (3, 4096), (4, 350)])
    assert lib.lib_request(q2.s, T['GET_COVER'], 9, p, len(p), 0, 0) == 0
    assert q2.out_n.value == 0 and last() == 'subsonic third 512'  # held: the daemon answers later
    big = b'\xff\xd8' + bytes(range(256)) * 3 + b'\xff\xd9'
    f = tmp_path / 'mnt/mmc/.subsonic/covers/512/third.jpg'
    f.parent.mkdir(parents=True)
    f.write_bytes(big)
    lib.lib_cover.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_char_p, ctypes.c_uint, ctypes.c_uint,
                              ctypes.c_uint, ctypes.c_int]
    assert lib.lib_cover(q2.s, 9, b'sthird', 0, 4096, 350, 0) == 0
    data = bytes(q2.out[:q2.out_n.value])
    assert data[2] == T['COVER'] and struct.unpack('>H', data[4:6])[0] == 9
    assert client.one(client.decode(data[6:]), 3) == big
    t, m = q2.ask(T['GET_COVER'], [(1, 'sthird'), (2, 0), (3, 4096), (4, 350)])
    assert t == T['COVER'] and last() == ''  # there now: nothing asked
    hold.value = 0


def test_a_page_reuses_the_librarys_tables(lib, tmp_path):
    """The library's lists read songs with their sort keys (s2) and albums: made per request, a Browse page at 9,199
    songs (the owner's) took 35 ms here, and coda-rc 37 % of the Q2's core while a phone browsed. They are made once
    for the files as they are, so a later page is a few ms; a changed catalogue is seen at the next request."""
    import time as clock
    server = [(f'Song {k}', f'Artist {k // 120}', f'Album {k // 12}', k) for k in range(9199)]
    q = Q2(lib, tmp_path, server=server, cat=True)
    assert q.browse('albums')['total']  # the tables made
    took = []
    for _ in range(5):
        t0 = clock.perf_counter()
        q.browse('albums')
        took.append(clock.perf_counter() - t0)
    assert min(took) < 0.01, took
    cat = sqlite3.connect(tmp_path / 'mnt/mmc/.q2pod/catalogue.db')
    cat.execute("update servertable set album = 'Zebra' where album = 'Album 0'")
    cat.commit()
    cat.close()
    assert 'Z' in titles(q.browse('albums'))


def open_catalogues(root):
    """This process's descriptors on root's catalogue.db (the library under test runs in it; other modules' daemons,
    loaded in the same process, hold their own roots')."""
    out = []
    for fd in os.listdir('/proc/self/fd'):
        try:
            target = os.readlink(f'/proc/self/fd/{fd}')
        except OSError:
            continue
        if target == str(root / 'mnt/mmc/.q2pod/catalogue.db'):
            out.append(target)
    return out


def test_the_card_is_let_go_of_while_demo_holds_it(lib, tmp_path):
    """Standby on the owner's Q2 (2026-11-23): coda-rc's library, kept open between requests, held the server catalogue
    on the card, so stock's umount failed and the card's driver was unloaded under a live mount. demo's card_release.c
    writes its pid to /tmp/mmc_hold and sends a SIGUSR1: the connection is closed (lib_release), and while the hold is
    live a request reads the database alone; once it is gone the catalogue is attached again."""
    q = Q2(lib, tmp_path, server=SERVER, cat=True)
    both = titles(q.browse('tracks'))
    assert open_catalogues(tmp_path), 'the connection keeps the catalogue open between requests'
    hold = tmp_path / 'tmp/mmc_hold'
    hold.write_text(f'{os.getpid()}\n')
    lib.lib_release()
    assert not open_catalogues(tmp_path)
    local = titles(q.browse('tracks'))
    assert not open_catalogues(tmp_path), 'nothing on the card opened while it is held'
    assert set(local) == {s[2] for s in SONGS} and set(local) < set(both)
    hold.unlink()
    assert titles(q.browse('tracks')) == both and open_catalogues(tmp_path)


def test_a_hold_whose_process_is_gone_holds_nothing(lib, tmp_path):
    """demo restarted with its hold left in /tmp: the pid names no process, the catalogue is read as before."""
    q = Q2(lib, tmp_path, server=SERVER, cat=True)
    child = gone_pid()
    (tmp_path / 'tmp/mmc_hold').write_text(f'{child}\n')
    assert lib.lib_card_held() == 0
    assert len(titles(q.browse('tracks'))) > len(SONGS)


def gone_pid():
    """A pid no process has (the top of pid_max downwards), as a restarted demo's old one."""
    top = int(pathlib.Path('/proc/sys/kernel/pid_max').read_text())
    return next(p for p in range(top - 1, 1, -1) if not pathlib.Path(f'/proc/{p}').exists())


# ---- Vamp OS's library (--library vampos, docs/carrd.md#the-library) ----

def vampos_files(root):
    """q2libd's library.db (docs/libd.md: stock's songtable, every column, its time in seconds; stock's playlist; no
    myfav), beside a stock database.db and today's Vamp's card catalogue that Vamp OS leaves alone: neither is read."""
    Q2.files(root, songs=[(9, 'z/9.flac', 'Stock Only', 'Zed', 'Old', 1)], favs=(9,), server=SERVER)
    (root / 'mnt/data/vamp').mkdir(parents=True)
    db = sqlite3.connect(root / 'mnt/data/vamp/library.db')
    db.execute('create table songtable(id integer primary key, filename varchar(256), fileurl varchar(256), '
               'album varchar(256), songer varchar(256), style varchar(256), name varchar(256), composer varchar(256), '
               'albumsonger varchar(256), year integer, track integer, cd_num integer, time integer, time_end integer, '
               'time_create integer, Initial varchar(8), bitdepth integer, samprate integer)')
    db.execute('create table playlist(id integer, name varchar(256), filename varchar(256), fileurl varchar(256), '
               'cd_num integer)')
    for i, f, title, artist, album, added in SONGS:
        db.execute('insert into songtable(id, filename, fileurl, album, songer, style, name, albumsonger, year, track, '
                   'cd_num, time, time_create) values (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (i, f.split('/')[-1], '/mnt/mmc/' + f, album, artist, 'Rock', title, artist, 2001, i, 1, 60 * i, added))
    db.commit()
    db.close()


@pytest.fixture
def vampos(lib, tmp_path):
    flag = ctypes.c_int.in_dll(lib, 'crc_vampos')
    flag.value = 1
    vampos_files(tmp_path)
    q2 = Q2.__new__(Q2)
    Q2.__init__(q2, lib, tmp_path / 'unused')  # a session; its own files under a root not used
    ctypes.memmove(ctypes.addressof(ctypes.c_char.in_dll(lib, 'root')), str(tmp_path).encode() + b'\0',
                   len(str(tmp_path)) + 1)
    q2.root = tmp_path
    yield q2
    flag.value = 0


def test_vamp_os_s_library_is_q2libd_s_with_its_lengths_and_nothing_of_stock_s(vampos):
    """Under Vamp OS coda-rc reads q2libd's /mnt/data/vamp/library.db: its songs, each with its length from the time
    column (no durations file there), and no stock database.db's songs or today's Vamp's catalogue on the card"""
    tracks = vampos.browse('tracks')
    assert titles(tracks) == ['Alpha', 'Bravo', 'Charlie', 'Delta'], titles(tracks)
    assert [i.get('duration_ms') for i in tracks['items']] == [60000, 120000, 180000, 240000], tracks['items']
    assert vampos.play('tracks') == ['1', '2', '3', '4']


def test_vamp_os_has_no_favorites_yet_so_its_list_is_empty(vampos):
    """q2libd keeps no myfav: Favorites is an empty list, not an error"""
    assert vampos.browse('favorites')['total'] == 0


def test_vamp_os_s_library_is_read_again_once_q2libd_has_changed_it(vampos):
    """the tables are kept while the library's file is unchanged (lib_open's key): q2libd's file, not stock's"""
    assert len(titles(vampos.browse('tracks'))) == 4
    time.sleep(0.01)
    db = sqlite3.connect(vampos.root / 'mnt/data/vamp/library.db')
    db.execute("insert into songtable(id, filename, fileurl, album, songer, name, time, time_create) "
               "values (7, '7.flac', '/mnt/mmc/c/7.flac', 'Third', 'Cy', 'Echo', 30, 700)")
    db.commit()
    db.close()
    assert titles(vampos.browse('tracks')) == ['Alpha', 'Bravo', 'Charlie', 'Delta', 'Echo']


def test_the_playlists_list_answers_with_no_server_signed_in(lib, tmp_path):
    """the art's coalesce() has the server's cover first only with a catalogue attached: without one it must still be
    a call SQLite takes (one argument is refused: "wrong number of arguments to function coalesce()")"""
    q2 = Q2(lib, tmp_path)
    db = sqlite3.connect(tmp_path / 'mnt/data/database.db')
    db.execute("insert into playlist values (1, 'Road', '', 1)")
    db.execute("insert into playlist values (101, 'Alpha', '/mnt/mmc/a/1.flac', 1)")
    db.commit()
    db.close()
    listing = q2.browse('playlists')
    assert not isinstance(listing, str) and titles(listing) == ['Road'], listing

