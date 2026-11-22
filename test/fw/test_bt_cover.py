"""bt_cover.c (docs/internals.md#bluetooth-cover-art): the playing song's image handle and cover in /tmp/q2-bip.now,
which bluetoothd's GetElementAttributes (tools/bt_meta.c) and q2-bip read: Cover Flow's cached copy for the song's
artist and album, else the app's cover for a server song, else a cover beside the song, else a FLAC's own picture,
else no file; a handle that changes exactly when the cover does."""
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


def flac(picture=True, blocks_after=0):
    """A FLAC's start: STREAMINFO, then a PICTURE (a front cover, image/jpeg), then padding blocks; the audio after
    the last is left out, as nothing past the metadata is read."""
    def block(kind, body, last):
        return bytes([kind | (128 if last else 0)]) + len(body).to_bytes(3, 'big') + body
    pic = (3).to_bytes(4, 'big') + (10).to_bytes(4, 'big') + b'image/jpeg' + (0).to_bytes(4, 'big') + bytes(16) + \
        (4).to_bytes(4, 'big') + b'\xff\xd8\xff\xd9'
    out = [block(0, bytes(34), False)] + [block(1, bytes(8), False) for _ in range(blocks_after)]
    out.append(block(6, pic, True) if picture else block(1, bytes(4), True))
    return b'fLaC' + b''.join(out)


STREAMED = '/mnt/mmc/.subsonic/stream/11c3e037/original/f/ribs [0S2sKobzoSngoRjq2oIqxT].flac'


def test_a_song_with_only_its_own_picture_names_itself_by_its_albums_key(fw):
    """The drive of 2026-11-22: a server song streamed from the app's cache (Lorde's Pure Heroine) has its cover in
    the FLAC alone, Cover Flow keeps no copy and the app had fetched none, so the file was removed and the car went
    on showing the last cover it was given, the first song's. The song itself is the cover (q2-bip's ffmpeg takes its
    attached picture), its handle the album's key, so the album's songs share it."""
    c = Cover(fw)
    c.files.files[STREAMED] = flac(blocks_after=2)
    want = b'%07d\n%s\n' % (key('Lorde', 'Pure Heroine') % 10**7, STREAMED.encode())
    assert c.play(STREAMED, 'Lorde', 'Pure Heroine') == want
    other = STREAMED.replace('ribs [0S2', 'buzzcut season [RjY')
    c.files.files[other] = flac()
    assert c.play(other, 'Lorde', 'Pure Heroine') == want.replace(STREAMED.encode(), other.encode())


def test_a_song_s_own_picture_without_an_album_is_its_own_key(fw):
    c = Cover(fw)
    c.files.files['/mnt/mmc/x/1.flac'] = flac()
    assert c.play('/mnt/mmc/x/1.flac') == b'%07d\n/mnt/mmc/x/1.flac\n' % (key('', '/mnt/mmc/x/1.flac') % 10**7)


@pytest.mark.parametrize('data', [flac(picture=False), b'ID3\x03' + bytes(40), b'fLaC', b''])
def test_a_song_with_no_picture_of_its_own_still_has_none(fw, data):
    c = Cover(fw, [flow('A', 'B')])
    assert c.play('/mnt/mmc/x/0.flac', 'A', 'B')  # a cover first, which the next song must not keep
    c.files.files['/mnt/mmc/y/1.flac'] = data
    assert c.play('/mnt/mmc/y/1.flac', 'C', 'D') is None


def test_cover_flows_copy_comes_before_the_songs_own_picture(fw):
    art = flow('A', 'B')
    c = Cover(fw, [art])
    c.files.files['/mnt/mmc/x/1.flac'] = flac()
    assert c.play('/mnt/mmc/x/1.flac', 'A', 'B').endswith(art.encode() + b'\n')
