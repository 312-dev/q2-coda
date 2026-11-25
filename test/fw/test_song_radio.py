"""Song radio, patch/song_radio.c (docs/internals.md#radio): Now Playing's Radio on a song of the card plays the
library's radio of it (the seed's artist, then its genre, each shuffled, nothing played lately), the seed playing on
where it was; the pill lights while it plays and a tap on it stops it; a server song goes to its app, and one the
app answers is not its own falls back to the library's; a streamed song's request names its stream file, whose name
carries the server id; an app too old for radios says so. The rig is test_coda_rc_shim's: /tmp is a dict, AWTK
test_nav_library_pages' fake and the songs test_nav_hold's Records."""
import ctypes as C

import pytest

from conftest import O, mcl  # noqa: F401
from test_coda_rc_shim import ACK, CMD, Q2
from test_nav_library_pages import ui  # noqa: F401
from test_nav_hold import recs  # noqa: F401
from test_addon import Qa, ap, card, carry, scan

SONGS = [  # (id, artist, genre)
    (10, 'Ann', 'Rock'),  # the seed
    (11, 'Ann', 'Rock'), (12, 'ann', 'Pop'), (13, 'Ann', 'Rock'),
    (20, 'Bob', 'rock'), (21, 'Bob', 'Rock'),
    (30, 'Cy', 'Jazz'),
]


class Radio(Q2):
    def __init__(self, fw, ui, recs):
        super().__init__(fw, ui, recs, on=1, songs=0)
        self.songs = [recs(id_=i, path=f'/mnt/mmc/m/{i}.flac', name=f'{i}.flac', title=f'Song {i}', artist=a, genre=g)
                      for i, a, g in SONGS]
        self.queue(at=0)
        self.tap = fw.fn('song_radio_tap', C.c_int, C.c_void_p)
        self.on = fw.fn('song_radio_on', C.c_int)
        self.radio_poll = fw.fn('song_radio_poll', None)
        self.loads = fw.var('playback_loads', C.c_uint)
        def navigate(name, info, *_):  # a play loads its queue at once (playback.c mclLoadPlayList's count)
            ui.navigate(name, info)
            if C.string_at(name) == b'playing_page':
                self.loads.value += 1
        fw.mock('navigator_to_with_context', navigate)
        # stock shuffle's pool, which an edit keeps: a deque of ints, deque_at a pointer to one, as libcstl's
        self.pool, self.cells = ui.deques.new(), []
        C.c_uint.from_address(O['MCL_POOL']).value = self.pool
        fw.mock('fw__deque_push_back', lambda q, v, *_: ui.deques[q].append(self.cell(v) if q == self.pool else v))
        fw.mock('fw_deque_pop_back', lambda q, *_: ui.deques[q].pop() and 0)

    def cell(self, v):
        self.cells.append(C.c_int(v))
        return C.addressof(self.cells[-1])

    def ids(self, queue):
        return [C.c_int.from_address(r).value for r in queue]

    def playing(self):
        """The queue playing now: the one started mid-song is the same deque, the seed at its front."""
        return self.ui.deques[self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value]


def test_a_card_songs_radio_is_its_artist_then_its_genre(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.sec = 42
    q2.tap(None)
    assert not ui.played  # the seed plays on: the songs after it are the radio's (test_..._mid_song)
    ids = q2.ids(q2.playing())
    assert ids[0] == 10  # the seed first
    assert sorted(ids[1:4]) == [11, 12, 13] and sorted(ids[4:]) == [20, 21]  # Ann's (any case), then Rock's; no Jazz
    assert q2.on() == 1  # the pill
    q2.radio_poll()
    assert not fw.calls('player_seek_time')


def test_the_pill_stops_the_radio_and_another_queue_ends_it(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.tap(None)
    q2.radio_poll()
    assert q2.on() == 1
    q2.tap(None)  # the pill
    assert q2.on() == 0 and ui.toasts[-1] == 'Radio Off' and not ui.played
    q2.tap(None)
    q2.radio_poll()
    assert q2.on() == 1
    q2.loads.value += 1  # the user played another list
    q2.radio_poll()
    assert q2.on() == 0


def test_a_song_with_nothing_like_it_starts_nothing(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    C.c_int.from_address(O['MCL_POS']).value = 6  # Cy's, the only Jazz
    q2.tap(None)
    assert not ui.played and q2.on() == 0 and ui.toasts == ['No similar songs on this Vamp']


def test_the_car_starts_and_stops_a_radio(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.files[CMD] = b'5 radio 20\n'  # Bob's song: the seed, from its start
    q2.tick()
    ui.fire('cr_radio')  # from a timer, as Play's
    (queue, _, _), = ui.played
    assert q2.ids(queue)[0] == 20 and q2.files[ACK] == b'5 0\n5 9\n'  # done, and taken before it (CRC_ACK_TAKEN)
    assert q2.now()['radio'] == '1'
    q2.files[CMD] = b'6 radio stop\n'
    q2.tick()
    ui.fire('cr_radio')
    assert q2.files[ACK] == b'6 0\n6 9\n5 0\n5 9\n' and q2.on() == 0  # the last answers, newest first
    q2.tick()
    assert q2.now()['radio'] == '0'
    q2.files[CMD] = b'7 radio 99\n'  # no such song
    q2.tick()
    ui.fire('cr_radio')
    assert q2.files[ACK].startswith(b'7 3\n')  # newest first


def test_a_radio_adds_songs_as_they_run_out_and_none_twice(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.songs += [recs(id_=100 + i, path=f'/mnt/mmc/m/a{i}.flac', name=f'a{i}.flac', title=f'A {i}', artist='Ann',
                      genre='Rock') for i in range(40)]
    q2.tap(None)
    assert len(q2.playing()) == 26  # the seed and SR_BATCH
    played = fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint)
    C.c_int.from_address(O['MCL_POS']).value = 10  # 15 left: nothing yet
    q2.radio_poll()
    assert len(ui.deques[played.value]) == 26
    C.c_int.from_address(O['MCL_POS']).value = 15  # 10 left
    q2.radio_poll()
    ids = q2.ids(ui.deques[played.value])
    assert len(ids) == 46 and len(set(ids)) == len(ids)  # the other 20 like it (not Cy's Jazz); none twice
    assert q2.on() == 1  # an insert is not another queue


def test_the_seed_is_never_queued_again_whatever_its_record_id(fw, ui, mcl, recs):
    """A queue's record and the library's (staged) can number one song differently: the emulator's Songs list played
    Song 1, and its radio queued Song 1 again after Song 2 (2026-11-16). Songs are told apart by path."""
    q2 = Radio(fw, ui, recs)
    seed = recs(id_=999, path='/mnt/mmc/m/10.flac', name='10.flac', title='Song 10', artist='Ann', genre='Rock')
    q2.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([seed])
    q2.tap(None)
    paths = [C.string_at(C.c_void_p.from_address(r + O['REC_PATH']).value or 0) for r in q2.playing()]
    assert paths.count(b'/mnt/mmc/m/10.flac') == 1 and len(q2.playing()) == 6


# A song the Subsonic app streams: its stream cache's file (stream_path.h), which no library record has (the player's
# id 0), the server's id between its name's last brackets. The user's, 2026-11-16: Radio on a streamed 10 Years song.
STREAMED = '/mnt/mmc/.subsonic/stream/5f3a/original/0/Wasteland [tr-77].flac'


def streaming(fw, ui, recs, q2app, builtin=None):
    """The Radio rig playing STREAMED, with the Subsonic app on the card (its q2app's text), running; builtin: the
    firmware's own copy's q2app as well, the one that runs (tools/build.py BUNDLED_APPS)."""
    q2 = Radio(fw, ui, recs)
    q2.songs = q2.songs[-1:]  # the card's library: Cy's Jazz, nothing like the seed
    world = card(fw, subsonic=q2app)  # the card's files from here on, and the clock
    if builtin is not None:
        carry(world, 'subsonic', b'q2-v1.0\n', builtin, builtin=True)
    fw.mock('fw_fclose', lambda *_: 0)  # Q2's Files' close and tell, of files World opened
    fw.mock('fw_ftell', lambda f, *_: world.at[f])
    world.files.update({b'/tmp/q2-subsonic': b'1', b'/tmp/q2-subsonic.browse.sock': b''})
    scan(fw)
    song = recs(id_=0, path=STREAMED, name=STREAMED.rsplit('/', 1)[1], title='Wasteland', artist='10 Years',
                genre='Rock')
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([song])
    C.c_int.from_address(O['MCL_POS']).value = 0
    return q2, world


def test_a_streamed_songs_radio_is_asked_of_its_app_by_its_path(fw, ui, mcl, recs):
    """The seed is the stream file's path, which carries the server id: the app reads it there (q2-subsonic's
    songOf, streamID), as catalogue.c and coda-rc do (stream_path.h)."""
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\nradio=1\n')
    q2.tap(None)
    assert ui.toasts == ['Starting Radio…'] and not ui.played  # nothing local: the app plays it
    seq, op, path = world.files[b'/tmp/q2-subsonic.radio'].decode().rstrip('\n').split(' ', 2)
    assert op == 'start' and path == STREAMED and path.endswith('[tr-77].flac')
    world.files[b'/tmp/q2-subsonic.state'] = f'version=1\nradioack={seq} 0\n'.encode()
    q2.radio_poll()
    assert ui.toasts == ['Starting Radio…'] and not ui.played


def test_a_server_with_nothing_like_it_is_told_apart_from_the_library(fw, ui, mcl, recs):
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\nradio=1\n')
    q2.tap(None)
    seq = world.files[b'/tmp/q2-subsonic.radio'].split(b' ')[0].decode()
    world.files[b'/tmp/q2-subsonic.state'] = f'version=1\nradioack={seq} 2\n'.encode()  # no radio: the server's none
    q2.radio_poll()
    assert ui.toasts[-1] == "Radio isn't available for this song" and not ui.played


def test_a_signed_out_app_asks_for_a_sign_in_and_starts_no_radio(fw, ui, mcl, recs):
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\nradio=1\n')
    q2.tap(None)
    seq = world.files[b'/tmp/q2-subsonic.radio'].split(b' ')[0].decode()
    world.files[b'/tmp/q2-subsonic.state'] = f'version=1\nradioack={seq} 3\n'.encode()  # nobody signed in
    q2.radio_poll()
    assert ui.toasts[-1] == 'Sign in to use Radio' and not ui.played and q2.on() == 0


def test_an_app_too_old_for_radio_says_so(fw, ui, mcl, recs):
    """q2-subsonic before q2-v1.0 (the firmware carried q2-v0.9 until p32) has no radio=1: no app is asked, the
    library has nothing like a server song, and "No similar songs on this Vamp" read as the server having none."""
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\n')
    q2.tap(None)
    assert b'/tmp/q2-subsonic.radio' not in world.files and not ui.played
    assert ui.toasts == ['Update Subsonic to use Radio']


def test_a_stale_card_copy_never_asks_for_an_update_of_the_builtin_app(fw, ui, mcl, recs):
    """The user's, 2026-11-17: "Update Subsonic to use Radio" with an old Subsonic on the card. The firmware's own
    q2-v1.0 runs whatever the card holds, and it plays radios: its radio is asked, no update is."""
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\n', builtin=b'name=Subsonic\nradio=1\n')
    q2.tap(None)
    assert ui.toasts == ['Starting Radio…'] and b'/tmp/q2-subsonic.radio' in world.files


def splice_rig(fw, ui, q2, recs, rows, at):
    """The queue playing rows (records of their own, not the library's: a splice moves the playing row's bytes),
    rows[at] playing."""
    played = fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint)
    played.value = ui.deques.new(rows)
    C.c_int.from_address(O['MCL_POS']).value = at
    return played


def test_a_radio_started_mid_song_leaves_the_song_playing(fw, ui, mcl, recs):
    """The user's, 2026-11-17: Radio "jumped backwards": the radio's queue was loaded with the seed first, which
    started again and was sought back to its place. The seed now plays on untouched: no queue loads, nothing is
    sought or started, and only the songs after it are the radio's."""
    q2 = Radio(fw, ui, recs)
    rows = [recs(id_=i, path=f'/mnt/mmc/m/{i}.flac', name=f'{i}.flac', title=f'Song {i}', artist=a, genre=g)
            for i, a, g in [(30, 'Cy', 'Jazz'), (10, 'Ann', 'Rock'), (21, 'Bob', 'Rock')]]
    played = splice_rig(fw, ui, q2, recs, rows, 1)
    seed = rows[1]
    q2.sec = 42
    q2.tap(None)
    assert not ui.played and q2.loads.value == 0  # no load: the player plays on
    ids = q2.ids(ui.deques[played.value])
    assert ids[0] == 10 and C.c_int.from_address(O['MCL_POS']).value == 0  # the same song, still the one playing
    assert ui.deques[played.value][0] in (seed, rows[0])  # its row, kept (moved to the front, not copied)
    assert sorted(ids[1:4]) == [11, 12, 13] and sorted(ids[4:]) == [20, 21]  # Cy's Jazz before it is gone
    assert q2.on() == 1
    q2.ms += 1000
    q2.radio_poll()
    assert not fw.calls('player_seek_time') and not fw.calls('player_start_queue')


def app_radio_rig(fw, ui, mcl, recs, mode):
    """streaming's, Now Playing's mode set to mode (stock's MCL_MODE: 1 Repeat One, 2 shuffle, 3 Repeat All), and
    the Radio tapped: the app is asked, and its answer's seq returned."""
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\nradio=1\n')
    fw.mock('config_playmode', lambda m, notify, *_: setattr(mcl, 'mode', m) or 0)
    fw.mock('write_int_config', lambda *_: 0)
    mcl.mode = mode
    q2.tap(None)
    seq = world.files[b'/tmp/q2-subsonic.radio'].split(b' ')[0].decode()
    return q2, world, seq


def app_radio_plays(fw, q2, world, seq, title='Wasteland'):
    """The app answers that its radio started, its queue playing (addon.c's qa: the app's, no load since), and says
    radio=<title> in its state file while it plays; a poll a second on reads it (addon_radio)."""
    world.files[b'/tmp/q2-subsonic.state'] = f'version=1\nradioack={seq} 0\nradio={title}\n'.encode()
    qa = fw.var('addon__qa', Qa)
    qa.app, qa.loads = C.addressof(ap(fw).a[0]), q2.loads.value
    world.ms += 1000  # the card's clock (World's time_now_ms)
    q2.radio_poll()


@pytest.mark.parametrize('mode', [1, 2, 3], ids=['repeat one', 'shuffle', 'repeat all'])
def test_an_apps_radio_plays_in_its_order_and_puts_the_modes_back(fw, ui, mcl, recs, mode):
    """Repeat One left on under the app's radio played each of its songs again at its end, twice in a row, and
    Now Playing's repeat and shuffle are dimmed while a radio plays, so it could not be turned off there. The app's
    radio plays as the library's does: shuffle and repeat off while it plays, put back when it ends."""
    q2, world, seq = app_radio_rig(fw, ui, mcl, recs, mode)
    app_radio_plays(fw, q2, world, seq)
    assert q2.on() == 1 and mcl.mode == 0  # in the radio's order, no song again
    world.files[b'/tmp/q2-subsonic.state'] = b'version=1\n'  # the radio ended (stopped, or its songs ran out)
    world.ms += 1000
    q2.radio_poll()
    assert q2.on() == 0 and mcl.mode == mode


def test_another_list_ending_an_apps_radio_gets_the_modes_back(fw, ui, mcl, recs):
    q2, world, seq = app_radio_rig(fw, ui, mcl, recs, 1)
    app_radio_plays(fw, q2, world, seq)
    assert mcl.mode == 0
    q2.loads.value += 1  # the user played another list: the queue is no longer the app's
    world.ms += 1000
    q2.radio_poll()
    assert q2.on() == 0 and mcl.mode == 1
