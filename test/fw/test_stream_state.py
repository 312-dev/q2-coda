"""stream_state.c with addon.c: Download Ahead's .ahead, a card app's .stream, and the stream session's wait for
a song arriving in place (docs/server-library.md#download-ahead-and-the-streaming-state)."""
import ctypes as C

import pytest

from test_addon import A, B, C3, LIBRARY, PLAYLIST, World, playlist, songs  # noqa: F401 (songs: a fixture)

AHEAD = b'/tmp/q2-foo.ahead'
STREAM = b'/tmp/q2-foo.stream'
NOW = 1000


class SLine(C.Structure):
    _fields_ = [('state', C.c_int), ('have', C.c_uint), ('total', C.c_uint), ('ms', C.c_uint)]


SL_NONE, SL_WAIT, SL_GET, SL_STALL, SL_DONE, SL_FAIL = range(6)


def status(*lines, head=b'#Q2STREAM/1\tseq=1\n'):
    return head + b''.join(b'\t'.join(map(lambda v: v if isinstance(v, bytes) else str(v).encode(), l)) + b'\n' for l in lines)


def line(fw, path, app=b'foo'):
    out = SLine()
    found = fw.fn('stream_line', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p)(app, path, C.byref(out))
    return (out.state, out.have, out.total, out.ms) if found else None


def grow(fw, world, on):
    if on:
        world.files[b'/tmp/q2clicks'] = b''
    else:
        world.files.pop(b'/tmp/q2clicks', None)
    return C.string_at(fw.fn('ahead_caps', C.c_void_p)())


def startable(fw, path, app=b'foo'):
    return fw.fn('stream_startable', C.c_int, C.c_char_p, C.c_char_p)(app, path)


def test_the_caps_say_ahead_and_grow_only_while_q2player_plays(fw):
    world = World(fw)
    assert grow(fw, world, False) == b' ahead'
    assert grow(fw, world, True) == b' ahead grow'


def test_a_line_is_found_by_its_path_and_read(fw):
    world = World(fw, now=NOW)
    world.files[STREAM] = status(('done', 9, 9, 1000, B), ('get', 700, 20000, 780000, A), ('wait', 0, 0, 0, C3))
    world.mtime[STREAM] = NOW
    assert line(fw, A) == (SL_GET, 700, 20000, 780000)
    assert line(fw, B) == (SL_DONE, 9, 9, 1000)
    assert line(fw, C3) == (SL_WAIT, 0, 0, 0)
    assert line(fw, b'/mnt/mmc/.foo/other.flac') is None
    assert line(fw, A, app=b'bar') is None  # another app's file
    assert line(fw, A, app=b'') is None


def test_an_escaped_path_and_a_bad_state_or_header(fw):
    world = World(fw, now=NOW)
    tab = b'/mnt/mmc/.foo/a\tb.flac'
    world.files[STREAM] = status(('nope', 1, 1, 1, A), ('get', 5, 10, 0, b'/mnt/mmc/.foo/a%09b.flac'))
    world.mtime[STREAM] = NOW
    assert line(fw, A) is None and line(fw, tab) == (SL_GET, 5, 10, 0)
    world.files[STREAM] = status(('get', 5, 10, 0, A), head=b'#Q2OTHER/1\n')
    assert line(fw, A) is None


def test_an_arriving_song_in_a_file_left_for_three_seconds_has_failed(fw):
    world = World(fw, now=NOW)
    world.files[STREAM] = status(('get', 5, 10, 0, A), ('stall', 5, 10, 0, B), ('done', 9, 9, 0, C3))
    world.mtime[STREAM] = NOW - 3
    assert [line(fw, p)[0] for p in (A, B, C3)] == [SL_GET, SL_STALL, SL_DONE]
    world.mtime[STREAM] = NOW - 4
    assert [line(fw, p)[0] for p in (A, B, C3)] == [SL_FAIL, SL_FAIL, SL_DONE]


MB = 1 << 20


@pytest.mark.parametrize('state,have,total,ms,ok', [
    ('wait', 0, 20 * MB, 780000, 0),
    ('get', 1398095, 20 * MB, 120000, 0),    # 8 s of a 2-minute 20 MB song: 174,762 bytes a second, 8 times
    ('get', 1398096, 20 * MB, 120000, 1),
    ('stall', 1398096, 20 * MB, 120000, 1),
    ('get', 262143, 20 * MB, 780000, 0),     # 8 s of a 13-minute one is 215,088 bytes: at least 256 KB
    ('get', 262144, 20 * MB, 780000, 1),
    ('get', 200 << 10, 3 * MB, 300000, 0),   # 8 s is 83,880 bytes: at least 256 KB
    ('get', 256 << 10, 3 * MB, 300000, 1),
    ('get', 100 << 10, 100 << 10, 2000, 1),  # a short song: all of it
    ('get', MB, 2 * MB, 0, 0),               # no length: all of it
    ('get', 2 * MB, 2 * MB, 0, 1),
    ('get', 5 * MB, 0, 300000, 0),           # no total: never before it is done
    ('done', 0, 0, 0, 1), ('fail', 0, 0, 0, 1),
])
def test_with_grow_an_arriving_song_starts_once_eight_seconds_are_there(fw, state, have, total, ms, ok):
    world = World(fw, now=NOW)
    grow(fw, world, True)
    world.files[STREAM] = status((state, have, total, ms, A))
    world.mtime[STREAM] = NOW
    assert startable(fw, A) == ok
    assert startable(fw, B) == 1  # no line: a whole file
    grow(fw, world, False)
    assert startable(fw, A) == 1  # without grow a file there is whole, as before


def test_an_app_of_a_path(fw):
    World(fw)
    of = fw.fn('stream_app_of', C.c_int, C.c_char_p, C.c_char_p, C.c_uint)
    out = C.create_string_buffer(16)
    assert of(b'/mnt/mmc/.subsonic/stream/x.flac', out, 16) == 1 and out.value == b'subsonic'
    for p in (b'/mnt/mmc/Music/x.flac', b'/mnt/mmc/./x', b'/mnt/mmc/.x', b'/mnt/mmc/.' + b'a' * 16 + b'/x'):
        assert of(p, out, 16) == 0, p


def test_the_player_waits_for_the_song_its_file_names(fw):
    world = World(fw)
    waits = fw.fn('stream_player_waits', C.c_int, C.c_char_p)
    assert waits(A) == 0
    world.files[b'/tmp/q2player.wait'] = A + b'\n'
    assert waits(A) == 1 and waits(B) == 0


def test_a_playlist_session_waits_at_a_song_arriving_short_of_its_start(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A, B, C3)
    grow(fw, s.world, True)
    s.land(A, B)  # B is there, in place, still arriving
    s.world.files[STREAM] = status(('done', 9, 9, 0, A), ('get', 1000, 20 * MB, 780000, B))
    s.world.mtime[STREAM] = s.world.now
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    s.step()
    assert s.queued() == [A] and s.loads == [(LIBRARY, 1)]
    s.world.files[STREAM] = status(('done', 9, 9, 0, A), ('get', 300000, 20 * MB, 780000, B))
    s.step()
    assert s.queued() == [A, B]


def test_the_session_tells_the_app_its_next_songs_in_play_order(fw, mcl, songs):
    s = playlist(fw, mcl, songs, A, B, C3)
    s.land(A, B, C3)
    s.play(b'7 ' + PLAYLIST + b'\n')
    s.step()
    s.step()
    assert s.queued() == [A, B, C3]
    assert s.world.files[AHEAD] == b'#Q2AHEAD/1\tseq=7\tn=2\n' + B + b'\n' + C3 + b'\n'
    s.world.log.clear()
    s.step()
    assert ('rename', AHEAD + b'.tmp', AHEAD) not in s.world.log  # unchanged: not written again
    mcl.pos = 1
    s.step()
    assert s.world.files[AHEAD] == b'#Q2AHEAD/1\tseq=7\tn=2\n' + C3 + b'\n'


def test_download_ahead_steps_off_one_two_three(fw):
    World(fw)
    writes = []
    fw.mock('write_int_config', lambda v, sec, key, *_: writes.append((v, C.string_at(sec), C.string_at(key))))
    n = fw.fn('ahead_n', C.c_int)
    step = fw.fn('ahead_step', C.c_int)
    assert n() == 2
    assert [step() for _ in range(4)] == [3, 0, 1, 2]
    assert writes[0] == (3, b'Q2POD', b'AHEAD') and n() == 2


CACHED = b'/mnt/mmc/.foo/stream/9f2c/original/0/hello [VcF5].mp3'


def length(fw, path=CACHED, app=b'foo'):
    ms = C.c_uint(0)
    how = fw.fn('stream_length', C.c_int, C.c_char_p, C.c_void_p)(path, C.byref(ms))
    return how, ms.value


@pytest.mark.parametrize('state, ms, want', [(b'get', 0, (2, 0)), (b'stall', 0, (2, 0)), (b'wait', 0, (2, 0)),
                                             (b'get', 215000, (1, 215000)), (b'stall', 215000, (1, 215000)),
                                             (b'done', 0, (0, 0)), (b'done', 215000, (0, 0))])
def test_the_length_of_a_song_arriving_is_the_servers_or_unknown_and_a_whole_one_the_players(fw, state, ms, want):
    world = World(fw, now=NOW)
    world.files[STREAM] = status((state, 700, 20000, ms, CACHED))
    world.mtime[STREAM] = NOW
    assert length(fw) == want


def test_a_song_outside_a_stream_cache_or_with_no_line_is_the_players(fw):
    world = World(fw, now=NOW)
    world.files[STREAM] = status(('get', 700, 20000, 0, A))
    world.mtime[STREAM] = NOW
    assert length(fw, A) == (0, 0)  # a card file, not the app's stream cache
    assert length(fw, CACHED) == (0, 0)  # no line for it
    assert length(fw, b'/mnt/mmc/Music/a.mp3') == (0, 0)
    assert length(fw, b'/mnt/mmc/.foo/cat/x.mp3') == (0, 0)  # a catalogue row: its server length is the library's
