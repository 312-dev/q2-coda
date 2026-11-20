"""bt_cover.c (docs/internals.md#bluetooth-cover-art): the playing song's image handle and cover in /tmp/q2-bip.now,
which bluetoothd's GetElementAttributes (tools/bt_meta.c) and q2-bip read: Cover Flow's cached copy for the song's
artist and album, else a cover beside the song, else no file; a handle that changes exactly when the cover does."""
import ctypes as C

import pytest

from conftest import Files

NOW = '/tmp/q2-bip.now'
ID3_ALBUM, ID3_ARTIST = 0x404, 0x504


def fnv(h, s):
    for b in s.encode():
        h = ((h ^ b) * 16777619) & 0xffffffff
    return (h * 16777619) & 0xffffffff


def key(artist, album):
    """Cover Flow's key (coverflow.c tags_key)"""
    return fnv(fnv(2166136261, artist), album)


class Cover:
    def __init__(self, fw, files=()):
        self.files = Files(fw, {f: b'\xff\xd8' for f in files})
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p).decode() in self.files.files else -1)
        self._note = fw.fn('bt_cover_note', None, C.c_char_p, C.c_char_p)
        self._hold = fw.fn('bt_cover_hold', None, C.c_int)

    def play(self, path, artist='', album=''):
        id3 = C.create_string_buffer(0x700)
        id3[ID3_ARTIST:ID3_ARTIST + len(artist.encode())] = artist.encode()
        id3[ID3_ALBUM:ID3_ALBUM + len(album.encode())] = album.encode()
        self._note(id3, path.encode())
        return self.files.files.get(NOW)

    def hold(self, held):
        self._hold(held)
        return self.files.files.get(NOW)


def flow(artist, album):
    return f'/mnt/mmc/.coverflow/{key(artist, album):08x}.jpg'


def test_cover_flows_copy_and_its_key_as_the_handle(fw):
    art = flow('Bill Withers', 'Menagerie')
    c = Cover(fw, [art])
    want = b'%07d\n%s\n' % (key('Bill Withers', 'Menagerie') % 10**7, art.encode())
    assert c.play('/mnt/mmc/Music/Menagerie/01 Lovely Day.flac', 'Bill Withers', 'Menagerie') == want
    assert len(want.split(b'\n')[0]) == 7  # seven digits, zero padded
    assert c.play('/mnt/mmc/Music/Menagerie/02 Lovely Night.flac', 'Bill Withers', 'Menagerie') == want  # same album
    assert NOW + '.tmp' not in c.files.files  # written beside, then renamed over


def test_another_album_is_another_handle(fw):
    a, b = flow('Bill Withers', 'Menagerie'), flow('Grant Green', 'Idle Moments')
    c = Cover(fw, [a, b])
    first = c.play('/mnt/mmc/a/1.flac', 'Bill Withers', 'Menagerie')
    second = c.play('/mnt/mmc/b/1.flac', 'Grant Green', 'Idle Moments')
    assert first[:7] != second[:7] and second.endswith(b.encode() + b'\n')


@pytest.mark.parametrize('name', ['cover.jpg', 'folder.jpg', 'front.jpg'])
def test_a_cover_beside_the_song(fw, name):
    cover = f'/mnt/mmc/Music/Loose/{name}'
    c = Cover(fw, [cover])
    for artist, album in (('', ''), ('Someone', 'An Album Cover Flow Has No Copy Of')):
        got = c.play('/mnt/mmc/Music/Loose/song.mp3', artist, album)
        assert got == b'%07d\n%s\n' % (key('', cover) % 10**7, cover.encode())  # the file's own key


def test_cover_flows_copy_comes_before_one_beside_the_song(fw):
    art = flow('A', 'B')
    c = Cover(fw, [art, '/mnt/mmc/x/cover.jpg', '/mnt/mmc/x/folder.jpg'])
    assert c.play('/mnt/mmc/x/1.flac', 'A', 'B').endswith(art.encode() + b'\n')
    assert c.play('/mnt/mmc/x/1.flac', 'A', '').endswith(b'/mnt/mmc/x/cover.jpg\n')  # no album: no album's cover
    c.files.files[flow('A', '')] = b'\xff\xd8'  # what Cover Flow keeps for songs with no album is nobody's cover
    assert c.play('/mnt/mmc/z/1.flac', 'A', '') is None


def test_no_cover_removes_the_file(fw):
    art = flow('A', 'B')
    c = Cover(fw, [art])
    assert c.play('/mnt/mmc/x/1.flac', 'A', 'B')
    assert c.play('/mnt/mmc/y/2.flac', 'C', 'D') is None  # the next song has none: the old handle must not stay
    assert c.play('http://127.0.0.1:4533/stream?id=7', 'C', 'D') is None  # a stream, no folder of its own


def test_guest_play_sets_the_owners_cover_aside(fw):
    art = flow('A', 'B')
    c = Cover(fw, [art])
    mine = c.play('/mnt/mmc/x/1.flac', 'A', 'B')
    assert c.hold(1) is None  # the car is told of the guest's song: no cover of the owner's
    assert c.hold(0) == mine  # and the owner's again
    assert c.hold(0) == mine
