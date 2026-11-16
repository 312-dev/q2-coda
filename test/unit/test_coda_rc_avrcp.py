"""AVRCP browsing for the rebuilt bluetoothd (patch/coda_rc_avrcp.c, docs/bluetoothd.md#browsing): a car's
SetBrowsedPlayer, GetFolderItems, ChangePath, GetItemAttributes, GetTotalNumberOfItems, Search and PlayItem, as
bluetoothd hands them to coda-rc --avrcp, answered from a library in stock's schema. Built for this machine with
coda_rc_lib.c, coda_rc_core.c and test/unit/coda_rc_avrcp_host.c as the platform, against the host's SQLite."""
import ctypes
import pathlib
import sqlite3
import struct
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc_avrcp.c', PATCH / 'coda_rc_lib.c', PATCH / 'coda_rc_core.c', PATCH / 'monocypher/monocypher.c',
           PATCH / 'monocypher/monocypher-ed25519.c', ROOT / 'test/unit/coda_rc_avrcp_host.c']

SET_BROWSED_PLAYER, GET_FOLDER_ITEMS, CHANGE_PATH, GET_ITEM_ATTRIBUTES, PLAY_ITEM, GET_TOTAL = range(0x70, 0x76)
SEARCH = 0x80
SUCCESS, INVALID_PARAM, INVALID_COMMAND = 4, 1, 0
INVALID_DIRECTION, NOT_A_DIRECTORY, DOES_NOT_EXIST, INVALID_SCOPE, OUT_OF_BOUNDS, NOT_PLAYABLE, MEDIA_IN_USE = (
    7, 8, 9, 0x0a, 0x0b, 0x0c, 0x0d)
INVALID_PLAYER, NO_RESULTS = 0x11, 0x14
PLAYERS, VFS, SEARCHED, NOW_PLAYING = range(4)

ALBUMS = [('Menagerie', 'Bill Withers', 1977, ['Lovely Day', 'I Want to Spend the Night', 'Let Me Be the One You Need']),
          ('Blue', 'Joni Mitchell', 1971, ['All I Want', 'My Old Man', 'Little Green', 'Carey'])]
FILLER = 130  # past coda_rc_lib.c's SPLIT: Tracks opens as letter buckets


@pytest.fixture(scope='module')
def lib(tmp_path_factory):
    so = tmp_path_factory.mktemp('avrcp') / 'avrcp.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-I', str(PATCH), *map(str, SOURCES), '-l:libsqlite3.so.0', '-o', str(so)], check=True)
    L = ctypes.CDLL(str(so))
    L.avrcp_request.restype = ctypes.c_size_t
    L.avrcp_request.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
    L.avrcp_forget.argtypes = [ctypes.c_uint32]
    return L


def library(root):
    """A Q2's card library under root, in stock's schema: the two albums, then FILLER songs of one more"""
    (root / 'mnt/data').mkdir(parents=True)
    (root / 'tmp').mkdir()
    db = sqlite3.connect(root / 'mnt/data/database.db')
    cols = ('filename, fileurl, album, songer, style, name, composer, albumsonger, year, track, cd_num, time, '
            'time_end, time_create, Initial, bitdepth, samprate')
    db.execute(f'create table songtable(id integer primary key autoincrement, {cols})')
    db.execute(f'create table playlist(id integer primary key, {cols})')
    rows = [(album, artist, year, n + 1, title) for album, artist, year, titles in ALBUMS for n, title in enumerate(titles)]
    rows += [('Filler', 'Nobody', 2001, n + 1, f'{chr(65 + n % 26)} Filler {n:03}') for n in range(FILLER)]
    for album, artist, year, track, title in rows:
        f = f'{track:02}. {title}.flac'
        db.execute(f'insert into songtable ({cols}) values (?,?,?,?,?,?,?,?,?,?,?,0,0,0,0,16,44100)',
                   (f, f'/mnt/mmc/{artist}/{album}/{f}', album, artist, 'Soul', title, '', artist, year, track, 1))
    db.commit()
    db.close()


@pytest.fixture
def q2(lib, tmp_path):
    library(tmp_path)
    (ctypes.c_char * 512).in_dll(lib, 'root').value = str(tmp_path).encode()
    for name in ('qgen', 'qpos', 'play_state'):
        ctypes.c_int.in_dll(lib, name).value = 0
    ctypes.c_int.in_dll(lib, 'shim_alive').value = 1
    (ctypes.c_char * 128).in_dll(lib, 'cmd').value = b''
    for tag in range(1, 6):
        lib.avrcp_forget(tag)
    return Car(lib, tmp_path)


class Car:
    def __init__(self, lib, root, tag=1):
        self.lib, self.root, self.tag = lib, root, tag

    def ask(self, pdu, params=b'', room=1000):
        req = bytes([pdu]) + struct.pack('>IH', self.tag, room) + params
        out = ctypes.create_string_buffer(2048)
        n = self.lib.avrcp_request(req, len(req), out, len(out))
        assert out.raw[0] == pdu
        return out.raw[1:n]

    def status(self, pdu, params=b'', **kw):
        return self.ask(pdu, params, **kw)[0]

    def set_player(self, player=1):
        return self.ask(SET_BROWSED_PLAYER, struct.pack('>H', player))

    def items(self, scope=VFS, start=0, end=99, attrs=None, room=1000):
        a = b'\0' if attrs is None else bytes([len(attrs)]) + b''.join(struct.pack('>I', x) for x in attrs)
        r = self.ask(GET_FOLDER_ITEMS, bytes([scope]) + struct.pack('>II', start, end) + a, room=room)
        if r[0] != SUCCESS:
            return r[0], []
        count, at, out = struct.unpack_from('>H', r, 3)[0], 5, []
        for _ in range(count):
            kind, n = r[at], struct.unpack_from('>H', r, at + 1)[0]
            out.append(parse_item(kind, r[at + 3:at + 3 + n]))
            at += 3 + n
        assert at == len(r)
        return SUCCESS, out

    def cd(self, uid=0, up=False):
        r = self.ask(CHANGE_PATH, struct.pack('>HBQ', 0, 0 if up else 1, uid))
        return r[0], (struct.unpack_from('>I', r, 1)[0] if r[0] == SUCCESS else None)

    def cd_to(self, *titles):
        for t in titles:
            uid = next(i['uid'] for i in self.items()[1] if i['name'] == t)
            assert self.cd(uid)[0] == SUCCESS
        return self

    def play(self, uid, scope=VFS):
        return self.status(PLAY_ITEM, bytes([scope]) + struct.pack('>QH', uid, 0))

    def cmd(self):
        return (ctypes.c_char * 128).in_dll(self.lib, 'cmd').value.decode()

    def queued(self):
        return [int(x) for x in (self.root / 'tmp/coda-rc.list').read_text().split()]


def parse_item(kind, b):
    if kind == 1:
        pid, major, sub, status = struct.unpack_from('>HBIB', b)
        features = b[8:24]
        charset, n = struct.unpack_from('>HH', b, 24)
        return {'kind': 'player', 'id': pid, 'status': status, 'features': features, 'name': b[28:28 + n].decode()}
    uid = struct.unpack_from('>Q', b)[0]
    if kind == 2:
        ftype, playable, charset, n = struct.unpack_from('>BBHH', b, 8)
        assert charset == 0x6a
        return {'kind': 'folder', 'uid': uid, 'type': ftype, 'playable': playable, 'name': b[14:14 + n].decode()}
    assert kind == 3
    media, charset, n = struct.unpack_from('>BHH', b, 8)
    name, at = b[13:13 + n].decode(), 13 + n
    attrs = {}
    for _ in range(b[at]):
        aid, cs, vn = struct.unpack_from('>IHH', b, at + 1)
        attrs[aid] = b[at + 9:at + 9 + vn].decode()
        at += 8 + vn
    assert at + 1 == len(b)
    return {'kind': 'song', 'uid': uid, 'name': name, 'attrs': attrs}


def rids(title_prefix=None):
    """song ids as the fixture inserted them: Menagerie 1-3, Blue 4-7, Filler 8 on"""
    return {t: i + 1 for i, t in enumerate(t for _, _, _, ts in ALBUMS for t in ts)}


ROOT_LISTS = ['Tracks', 'Playlists', 'All artists', 'Albums', 'Genres', 'Years', 'Folders']


def test_one_player_named_q2_that_browses_and_searches(q2):
    ctypes.c_int.in_dll(q2.lib, 'play_state').value = 2
    st, items = q2.items(PLAYERS, 0, 9)
    assert st == SUCCESS and len(items) == 1
    p = items[0]
    assert (p['kind'], p['id'], p['name'], p['status']) == ('player', 1, 'Q2', 2)
    bit = lambda n: p['features'][n >> 3] >> (n & 7) & 1  # noqa: E731
    assert [bit(n) for n in (40, 42, 44, 45, 58, 59, 60, 65, 67)] == [1] * 9  # play, pause ... NumberOfItems
    assert bit(61) == 0  # no AddToNowPlaying
    assert q2.items(PLAYERS, 1, 9)[0] == OUT_OF_BOUNDS


def test_set_browsed_player_opens_the_library_at_its_root(q2):
    r = q2.set_player()
    assert r[0] == SUCCESS and struct.unpack('>HIHB', r[1:]) == (0, len(ROOT_LISTS), 0x6a, 0)
    assert q2.set_player(2)[0] == INVALID_PLAYER


def test_the_root_lists_the_library_as_folders(q2):
    st, items = q2.items()
    assert st == SUCCESS
    assert [i['name'] for i in items] == ROOT_LISTS
    assert [i['uid'] for i in items] == list(range(1, 8))
    assert [i['type'] for i in items] == [1, 5, 3, 2, 4, 6, 0]  # titles, playlists, artists, albums, genres, years
    assert [i['playable'] for i in items] == [1, 0, 0, 0, 0, 0, 0]
    assert q2.items(start=7)[0] == OUT_OF_BOUNDS
    assert q2.items(start=5, end=4)[0] == OUT_OF_BOUNDS


def test_into_an_album_and_back(q2):
    st, n = q2.cd(4)  # Albums
    assert (st, n) == (SUCCESS, 3)
    albums = q2.items()[1]
    assert [a['name'] for a in albums] == ['Blue', 'Filler', 'Menagerie']
    # an album holds songs: Titles (a folder's type is what it lists, as Android's BT_FOLDER_TYPE_* reads it)
    assert all(a['kind'] == 'folder' and a['type'] == 1 and a['playable'] for a in albums)
    assert q2.cd(3) == (SUCCESS, 3)
    songs = q2.items()[1]
    assert [s['name'] for s in songs] == ALBUMS[0][3]
    assert songs[0]['attrs'] == {1: 'Lovely Day', 2: 'Bill Withers', 3: 'Menagerie'}
    assert q2.cd(1)[0] == NOT_A_DIRECTORY  # a song
    assert q2.cd(up=True) == (SUCCESS, 3)
    assert q2.cd(up=True) == (SUCCESS, len(ROOT_LISTS))
    assert q2.cd(up=True)[0] == INVALID_DIRECTION
    assert q2.cd(99)[0] == DOES_NOT_EXIST
    assert q2.cd(0)[0] == DOES_NOT_EXIST


def test_a_long_list_opens_as_letter_buckets_and_pages(q2):
    total = q2.cd(1)[1]  # Tracks: 137 songs
    assert total == 26  # A to Z (the albums' songs start with A, C, I, L, M)
    first = q2.items(end=4)[1]
    assert [b['name'] for b in first] == ['A', 'B', 'C', 'D', 'E'] and all(b['kind'] == 'folder' for b in first)
    assert [b['uid'] for b in q2.items(start=24, end=30)[1]] == [25, 26]
    assert q2.cd(12)[0] == SUCCESS  # L: Filler's six, Let Me Be, Little Green, Lovely Day
    names = [s['name'] for s in q2.items()[1]]
    assert 'Lovely Day' in names and all(n[0] == 'L' for n in names)


def test_a_reply_holds_only_what_fits_the_cars_room(q2):
    q2.cd(4)
    q2.cd(3)
    st, few = q2.items(room=150)
    assert st == SUCCESS and 0 < len(few) < 3
    assert q2.items(room=10)[0] == 3  # not even one: an internal error, never a cut item


def test_item_attributes_and_the_ones_asked_for(q2):
    q2.cd(4)
    q2.cd(1)  # Blue
    r = q2.ask(GET_ITEM_ATTRIBUTES, bytes([VFS]) + struct.pack('>QHB', 3, 0, 0))
    assert r[0] == SUCCESS and r[1] == 3
    assert b'Little Green' in r and b'Joni Mitchell' in r
    r = q2.ask(GET_ITEM_ATTRIBUTES, bytes([VFS]) + struct.pack('>QHBI', 3, 0, 1, 1))
    assert r == bytes([SUCCESS, 1]) + struct.pack('>IHH', 1, 0x6a, 12) + b'Little Green'
    assert q2.status(GET_ITEM_ATTRIBUTES, bytes([VFS]) + struct.pack('>QHB', 9, 0, 0)) == DOES_NOT_EXIST
    assert q2.status(GET_ITEM_ATTRIBUTES, bytes([7]) + struct.pack('>QHB', 1, 0, 0)) == INVALID_SCOPE


def test_play_a_song_plays_its_album_from_it(q2):
    q2.cd_to('Albums', 'Menagerie')
    assert q2.play(2) == SUCCESS
    assert q2.cmd() == 'play 1 0 3'
    assert q2.queued() == [1, 2, 3]


def test_play_a_folder_plays_it_from_the_start_and_a_list_of_lists_is_not_playable(q2):
    q2.cd_to('Albums')
    assert q2.play(1) == SUCCESS  # Blue
    assert q2.cmd() == 'play 0 0 4' and q2.queued() == [4, 5, 6, 7]
    q2.cd(up=True)
    assert q2.play(4) == NOT_PLAYABLE  # Albums itself
    assert q2.play(40) == DOES_NOT_EXIST


def test_play_while_demo_is_not_answering_is_media_in_use(q2):
    ctypes.c_int.in_dll(q2.lib, 'shim_alive').value = 0
    q2.cd_to('Albums', 'Blue')
    assert q2.play(1) == MEDIA_IN_USE


def test_search_then_its_results_then_play_one(q2):
    q = b'lovely'
    r = q2.ask(SEARCH, struct.pack('>HH', 0x6a, len(q)) + q)
    assert r[0] == SUCCESS and struct.unpack('>HI', r[1:]) == (0, 1)
    st, items = q2.items(SEARCHED)
    assert st == SUCCESS and [(i['kind'], i['name']) for i in items] == [('song', 'Lovely Day')]
    assert q2.status(GET_TOTAL, bytes([SEARCHED])) == SUCCESS
    assert q2.play(1, SEARCHED) == SUCCESS and q2.cmd() == 'play 0 0 1' and q2.queued() == [1]
    q = b'joni'
    r = q2.ask(SEARCH, struct.pack('>HH', 0x6a, len(q)) + q)
    kinds = [(i['kind'], i['name']) for i in q2.items(SEARCHED)[1]]
    assert ('folder', 'Joni Mitchell') in kinds  # an artist: a folder
    assert q2.status(SEARCH, struct.pack('>HH', 0x6a, 3) + b'zzz') == NO_RESULTS
    assert q2.status(SEARCH, struct.pack('>HH', 3, 1) + b'a') == INVALID_PARAM  # ASCII: UTF-8 only


def test_now_playing_is_the_queue_and_plays_by_place(q2):
    (q2.root / 'tmp/coda-rc.queue').write_text('5 0 3\n4\n5\n6\n')
    ctypes.c_int.in_dll(q2.lib, 'qgen').value = 5
    st, items = q2.items(NOW_PLAYING)
    assert st == SUCCESS and [i['name'] for i in items] == ['All I Want', 'My Old Man', 'Little Green']
    r = q2.ask(GET_TOTAL, bytes([NOW_PLAYING]))
    assert r == bytes([SUCCESS]) + struct.pack('>HI', 0, 3)
    assert q2.play(2, NOW_PLAYING) == SUCCESS and q2.cmd() == 'jump 1 5'
    assert q2.play(4, NOW_PLAYING) == DOES_NOT_EXIST


def test_totals_for_each_scope(q2):
    assert q2.ask(GET_TOTAL, bytes([PLAYERS]))[3:] == struct.pack('>I', 1)
    assert q2.ask(GET_TOTAL, bytes([VFS]))[3:] == struct.pack('>I', len(ROOT_LISTS))
    assert q2.status(GET_TOTAL, bytes([9])) == INVALID_SCOPE


def test_each_car_walks_its_own_path(q2):
    other = Car(q2.lib, q2.root, tag=2)
    q2.cd_to('Albums', 'Blue')
    assert [i['name'] for i in other.items()[1]] == ROOT_LISTS
    assert len(q2.items()[1]) == 4
    q2.lib.avrcp_forget(1)
    assert [i['name'] for i in q2.items()[1]] == ROOT_LISTS


def test_bad_requests(q2):
    assert q2.status(GET_FOLDER_ITEMS, b'\x01\0\0') == INVALID_PARAM
    assert q2.status(CHANGE_PATH, b'\0') == INVALID_PARAM
    assert q2.status(PLAY_ITEM, b'\x01') == INVALID_PARAM
    assert q2.status(0x7f) == INVALID_COMMAND
    assert q2.status(GET_FOLDER_ITEMS, bytes([9]) + struct.pack('>II', 0, 9) + b'\0') == INVALID_SCOPE
    assert q2.status(GET_FOLDER_ITEMS, bytes([VFS]) + struct.pack('>II', 0, 9) + b'\x02\0\0\0\x01') == INVALID_PARAM
