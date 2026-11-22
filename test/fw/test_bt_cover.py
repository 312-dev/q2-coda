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
        return self.head()

    def head(self):
        """The file's handle and cover lines, those bluetoothd and q2-bip serve by; its last two, where the cover
        came from and the song's path, which q2-bip's log names, in self.tail."""
        data = self.files.files.get(NOW)
        lines = data.split(b'\n') if data is not None else None
        self.tail = lines[2:4] if lines else None
        return b'\n'.join(lines[:2]) + b'\n' if lines else None

    def hold(self, held):
        self._hold(held)
        return self.head()


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


def syncsafe(n):
    return bytes([(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127])


def id3(version=3, picture=True):
    """An MP3's start: an ID3v2 tag (2.2, 2.3 or 2.4) with a title frame, then a picture frame (APIC, or PIC in
    2.2) when asked, then padding; the audio after it is left out."""
    def frame(name, body):
        if version == 2:
            return name[:3] + len(body).to_bytes(3, 'big') + body
        size = syncsafe(len(body)) if version == 4 else len(body).to_bytes(4, 'big')
        return name + size + b'\0\0' + body
    frames = frame(b'TIT2', b'\0Ribs')
    if picture:
        frames += frame(b'PIC', b'\0JPG\3\0\xff\xd8') if version == 2 else frame(b'APIC', b'\0image/jpeg\0\3\0\xff\xd8')
    frames += bytes(32)
    return b'ID3' + bytes([version, 0, 0]) + syncsafe(len(frames)) + frames + b'\xff\xfb' + bytes(64)


def atom(kind, body=b'', full=False):
    return (8 + (4 if full else 0) + len(body)).to_bytes(4, 'big') + kind + (bytes(4) if full else b'') + body


def m4a(picture=True, moov_last=False):
    """An M4A: ftyp, mdat and moov, whose udta/meta/ilst holds a title and, when asked, a covr; the moov after the
    audio, as an encoder that writes it last leaves it."""
    items = atom(b'\xa9nam', atom(b'data', b'\0\0\0\1\0\0\0\0Ribs'))
    if picture:
        items += atom(b'covr', atom(b'data', b'\0\0\0\x0d\0\0\0\0\xff\xd8'))
    moov = atom(b'moov', atom(b'mvhd', bytes(100)) + atom(b'udta', atom(b'meta', atom(b'hdlr', bytes(25)) +
                                                                  atom(b'ilst', items), full=True)))
    head, mdat = atom(b'ftyp', b'M4A \0\0\0\0'), atom(b'mdat', bytes(5000))
    return head + (mdat + moov if moov_last else moov + mdat)


@pytest.mark.parametrize('data', [id3(3), id3(4), id3(2), m4a(), m4a(moov_last=True)],
                         ids=['id3v2.3', 'id3v2.4', 'id3v2.2', 'm4a', 'm4a-moov-last'])
def test_an_mp3_s_or_m4a_s_own_picture_is_the_cover(fw, data):
    """An MP3's APIC (PIC in ID3v2.2) or an M4A's covr: the song itself, as for a FLAC's PICTURE."""
    path = '/mnt/mmc/.subsonic/stream/11c3e037/original/f/ribs [0S2sKobzoSngoRjq2oIqxT].m4a'
    c = Cover(fw)
    c.files.files[path] = data
    assert c.play(path, 'Lorde', 'Pure Heroine') == b'%07d\n%s\n' % (key('Lorde', 'Pure Heroine') % 10**7,
                                                                     path.encode())


@pytest.mark.parametrize('data', [id3(3, picture=False), id3(4, picture=False), id3(2, picture=False),
                                  m4a(picture=False), m4a(picture=False, moov_last=True), b'ID3\3\0\0', b'\0\0\0\x08ftyp'],
                         ids=['id3v2.3', 'id3v2.4', 'id3v2.2', 'm4a', 'm4a-moov-last', 'id3-cut', 'm4a-cut'])
def test_an_mp3_or_m4a_without_a_picture_has_none(fw, data):
    c = Cover(fw)
    c.files.files['/mnt/mmc/y/1.m4a'] = data
    assert c.play('/mnt/mmc/y/1.m4a', 'C', 'D') is None


def test_cover_flows_no_art_marker_is_no_cover(fw):
    """Cover Flow keeps an empty file for an album its build found no art for (coverflow.c): that is no cover, so
    the song's own picture is looked for, and with none the file is removed, not a handle q2-bip cannot serve."""
    art = flow('A', 'B')
    c = Cover(fw)
    c.files.files[art] = b''
    assert c.play('/mnt/mmc/x/1.flac', 'A', 'B') is None
    c.files.files['/mnt/mmc/x/1.flac'] = flac()
    assert c.play('/mnt/mmc/x/1.flac', 'A', 'B') == b'%07d\n/mnt/mmc/x/1.flac\n' % (key('A', 'B') % 10**7)


def test_the_file_names_where_the_cover_came_from_and_the_song(fw):
    """Its third and fourth lines, for q2-bip's log: coverflow, app, folder or own, then the song's path."""
    c = Cover(fw, [flow('A', 'B'), '/mnt/mmc/x/cover.jpg'])
    c.play('/mnt/mmc/y/1.flac', 'A', 'B')
    assert c.tail == [b'coverflow', b'/mnt/mmc/y/1.flac']
    c.play('/mnt/mmc/x/2.flac', 'C', 'D')
    assert c.tail == [b'folder', b'/mnt/mmc/x/2.flac']
    c.files.files['/mnt/mmc/z/3.mp3'] = id3(4)
    c.play('/mnt/mmc/z/3.mp3', 'E', 'F')
    assert c.tail == [b'own', b'/mnt/mmc/z/3.mp3']
