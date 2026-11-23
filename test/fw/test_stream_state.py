"""stream_state.c with addon.c: Download Ahead's .ahead, a card app's .stream, and the stream session's wait for
a song arriving in place (docs/server-library.md#download-ahead-and-the-streaming-state)."""
import ctypes as C

import pytest

from conftest import O
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
    ('get', 524285, 20 * MB, 120000, 0),     # 3 s of a 2-minute 20 MB song: 174,762 bytes a second, 3 times
    ('get', 524286, 20 * MB, 120000, 1),
    ('stall', 524286, 20 * MB, 120000, 1),
    ('get', 333728, 31259447, 281000, 0),    # a Q2's streamed FLAC (2026-11-22): 111,243 bytes a second, 3 times
    ('get', 333729, 31259447, 281000, 1),
    ('get', 262143, 20 * MB, 780000, 0),     # 3 s of a 13-minute one is 80,658 bytes: at least 256 KB
    ('get', 262144, 20 * MB, 780000, 1),
    ('get', 200 << 10, 3 * MB, 300000, 0),   # 3 s is 31,455 bytes: at least 256 KB
    ('get', 256 << 10, 3 * MB, 300000, 1),
    ('get', 100 << 10, 100 << 10, 2000, 1),  # a short song: all of it
    ('get', MB, 2 * MB, 0, 0),               # no length: all of it
    ('get', 2 * MB, 2 * MB, 0, 1),
    ('get', 5 * MB, 0, 300000, 0),           # no total: never before it is done
    ('done', 0, 0, 0, 1), ('fail', 0, 0, 0, 1),
])
def test_with_grow_an_arriving_song_starts_once_three_seconds_are_there(fw, state, have, total, ms, ok):
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


def reach(fw, total, path=CACHED):
    return fw.fn('stream_reach', C.c_int, C.c_char_p, C.c_int)(path, total)


@pytest.mark.parametrize('line_, total, want', [
    (('get', 1000000, 4000000, 400000), 400, 99),   # a quarter of the bytes: a quarter of the server's 400 s
    (('get', 1000000, 4000000, 400000), 61, 60),    # never past the player's own length
    (('get', 1000000, 0, 400000), 61, 60),          # no total bytes: the player's length is what has arrived
    (('get', 1000000, 4000000, 0), 200, 49),        # no server length: a quarter of the player's
    (('wait', 0, 4000000, 400000), 0, 0),           # nothing there yet: its start
    (('stall', 4000000000, 4000000000, 3600000), 3600, 3599),  # past 32 bits in the sum: still whole
])
def test_a_seek_in_a_song_arriving_reaches_only_what_is_on_the_card(fw, line_, total, want):
    world = World(fw, now=NOW)
    world.files[STREAM] = status(line_ + (CACHED,))
    world.mtime[STREAM] = NOW
    assert reach(fw, total) == want


def test_a_whole_song_or_one_outside_a_stream_cache_has_no_reach_of_its_own(fw):
    world = World(fw, now=NOW)
    world.files[STREAM] = status(('done', 4000000, 4000000, 400000, CACHED), ('get', 9, 99, 0, A))
    world.mtime[STREAM] = NOW
    assert reach(fw, 400) == -1
    assert reach(fw, 400, A) == -1


D4 = b'/mnt/mmc/.foo/B/4.flac'


@pytest.mark.parametrize('advanced', [False, True], ids=['repeat all', 'shuffle and repeat all'])
def test_repeat_all_names_whole_songs_never_one_still_to_land(fw, mcl, songs, advanced):
    """The contract q2-subsonic's plan reads (docs/server-library.md#download-ahead-and-the-streaming-state), as it
    is: under Repeat All, plain or with Shuffle, .ahead is always full, Repeat All's next pass naming songs already
    queued (whole on the card) once the songs to come run out, and never D4, the list's song still to land. So the
    app gives a whole song behind the one playing no Download Ahead slot (the drive of 2026-11-22: a radio stopped
    at its fourth song, D4 never fetched)."""
    s = playlist(fw, mcl, songs, A, B, C3, D4)
    s.land(A, B, C3)
    s.play(b'7 ' + PLAYLIST + b'\n')
    s.step()
    s.step()
    assert s.queued() == [A, B, C3]
    if advanced:
        put = fw.fn('playback_set', C.c_int, C.c_int, C.c_int)
        assert put(0, 1) and put(1, 5)  # Shuffle Songs, Repeat All: q2car's snapshot of 2026-11-22 (1, 5)
    else:
        C.c_int.from_address(O['MCL_MODE']).value = 3
        mcl.pos = 2  # the queue's last song
    s.step()
    ahead = [x for x in s.world.files[AHEAD].split(b'\n')[1:] if x]
    assert s.world.files[AHEAD].startswith(b'#Q2AHEAD/1\tseq=7\tn=2\n') and len(ahead) == 2, ahead
    assert D4 not in ahead and set(ahead) <= {A, B, C3}, ahead
    if not advanced:
        assert ahead == [A, B]  # the next pass from the queue's first



def test_a_queue_file_past_256_kb_is_read_only_that_far(fw, mcl, songs):
    """addon.c reads a session's playlist file to M3U_MAX (256 KB), its cut last line dropped: songs named past it
    are never queued and Up Next never lists them (addon_pending, the same read as m3u_songs). A 9,199-song
    Shuffle's cache paths are about 1 MB; q2-subsonic writes a window of the list, QUEUE_AHEAD (1,000) songs past
    the one playing, so the file stays well inside it. This is the firmware's side of that contract."""
    first = A
    rest = [b'/mnt/mmc/.foo/stream/11c3e037/original/f%02d/a song title long as the user\'s are %04d '
            b'[VdsIsB1X5aqa0jZXUmsd5M].flac' % (i // 100, i) for i in range(3000)]
    s = playlist(fw, mcl, songs, first, *rest)
    s.land(first)
    s.play(b'7 ' + PLAYLIST + b'\n')
    s.step()
    s.step()
    assert s.queued() == [first]
    n = C.c_uint(0)
    fw.fn('addon_pending', C.c_void_p, C.c_uint, C.POINTER(C.c_uint))(10000, C.byref(n))  # left: freeing needs stock's
    body = s.world.files[PLAYLIST]
    fits = body[:256 << 10].count(b'\n') - 2  # the #EXTM3U line and A's
    assert len(body) > 256 << 10 and 2000 < fits < 3000
    assert n.value == fits, (n.value, fits)


def test_a_streams_start_logs_where_its_time_went(fw, mcl, songs):
    """docs/internals.md#ui-pass-watchdog: the first start's UI-thread steps, timed: the folder listings and tag
    reads of m3u_songs, then play_folder (Now Playing's open)."""
    s = playlist(fw, mcl, songs, A, B)
    lines = []

    def write(fd, data, n, *_):
        if fd == 2:
            lines.append(C.string_at(data, n).decode().rstrip('\n').split(' ', 4)[4])
        return n
    fw.mock('fw_write', write)
    listing, navigate = s.listing, s.navigate

    def slow_listing(path, *_):
        s.world.ms += 40
        return listing(path)

    def slow_navigate(name, ctx, *_):
        s.world.ms += 300
        return navigate(name, ctx)
    fw.mock('toolsLoadDirectory', slow_listing)
    fw.mock('navigator_to_with_context', slow_navigate)
    s.land(A)
    s.play(b'1 ' + PLAYLIST + b'\n')
    s.step()
    assert s.queued() == [A]
    assert [l for l in lines if l.startswith('stream start:')] == ['stream start: 1 songs in 40 ms (list 40, tags 0), play 300 ms']
    s.land(B)
    s.step()
    assert len([l for l in lines if l.startswith('stream start:')]) == 1  # a song joining the queue is no start
