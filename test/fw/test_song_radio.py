"""Song radio, patch/song_radio.c (docs/internals.md#radio): Now Playing's Radio on a song of the card plays the
library's radio of it (the seed's artist, then its genre, each shuffled, nothing played lately), the seed playing on
where it was; the pill lights while it plays and a tap on it stops it; a server song goes to its app, and one the
app answers is not its own falls back to the library's. The rig is test_coda_rc_shim's: /tmp is a dict, AWTK
test_nav_library_pages' fake and the songs test_nav_hold's Records."""
import ctypes as C

from conftest import O, mcl  # noqa: F401
from test_coda_rc_shim import ACK, CMD, Q2
from test_nav_library_pages import ui  # noqa: F401
from test_nav_hold import recs  # noqa: F401
from test_addon import card, scan

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

    def ids(self, queue):
        return [C.c_int.from_address(r).value for r in queue]


def test_a_card_songs_radio_is_its_artist_then_its_genre(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.sec = 42
    q2.tap(None)
    (queue, at, cls), = ui.played
    ids = q2.ids(queue)
    assert ids[0] == 10 and at == 0 and cls == 0xf001  # the seed first
    assert sorted(ids[1:4]) == [11, 12, 13] and sorted(ids[4:]) == [20, 21]  # Ann's (any case), then Rock's; no Jazz
    assert q2.on() == 1  # the pill
    q2.radio_poll()
    assert [a[0] for _, a in fw.calls('player_seek_time')] == [42]  # the seed plays on where it was


def test_the_pill_stops_the_radio_and_another_queue_ends_it(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.tap(None)
    q2.radio_poll()
    assert q2.on() == 1
    q2.tap(None)  # the pill
    assert q2.on() == 0 and ui.toasts[-1] == 'Radio Off' and len(ui.played) == 1
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
    assert not ui.played and q2.on() == 0 and ui.toasts == ['No similar songs on this Q2 Muse']


def test_the_car_starts_and_stops_a_radio(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.files[CMD] = b'5 radio 20\n'  # Bob's song: the seed, from its start
    q2.tick()
    ui.fire('cr_radio')  # from a timer, as Play's
    (queue, _, _), = ui.played
    assert q2.ids(queue)[0] == 20 and q2.files[ACK] == b'5 0\n'
    assert q2.now()['radio'] == '1'
    q2.files[CMD] = b'6 radio stop\n'
    q2.tick()
    ui.fire('cr_radio')
    assert q2.files[ACK] == b'6 0\n' and q2.on() == 0
    q2.tick()
    assert q2.now()['radio'] == '0'
    q2.files[CMD] = b'7 radio 99\n'  # no such song
    q2.tick()
    ui.fire('cr_radio')
    assert q2.files[ACK] == b'7 3\n'


def test_a_radio_adds_songs_as_they_run_out_and_none_twice(fw, ui, mcl, recs):
    q2 = Radio(fw, ui, recs)
    q2.songs += [recs(id_=100 + i, path=f'/mnt/mmc/m/a{i}.flac', name=f'a{i}.flac', title=f'A {i}', artist='Ann',
                      genre='Rock') for i in range(40)]
    q2.tap(None)
    (queue, _, _), = ui.played
    assert len(queue) == 26  # the seed and SR_BATCH
    played = fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint)
    played.value = ui.deques.new(queue)  # the radio's queue, as playing_page loads it
    C.c_uint.from_address(O['MCL_POOL']).value = ui.deques.new()  # stock shuffle's pool, which an insert keeps
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
    (queue, _, _), = ui.played
    paths = [C.string_at(C.c_void_p.from_address(r + O['REC_PATH']).value or 0) for r in queue]
    assert paths.count(b'/mnt/mmc/m/10.flac') == 1 and len(queue) == 6


# A song the Subsonic app streams: its stream cache's file (stream_path.h), which no library record has (the player's
# id 0), the server's id between its name's last brackets. The user's, 2026-11-16: Radio on a streamed 10 Years song.
STREAMED = '/mnt/mmc/.subsonic/stream/5f3a/original/0/Wasteland [tr-77].flac'


def streaming(fw, ui, recs, q2app):
    """The Radio rig playing STREAMED, with the Subsonic app on the card (its q2app's text), running."""
    q2 = Radio(fw, ui, recs)
    q2.songs = q2.songs[-1:]  # the card's library: Cy's Jazz, nothing like the seed
    world = card(fw, subsonic=q2app)  # the card's files from here on, and the clock
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


def test_an_app_too_old_for_radio_says_so(fw, ui, mcl, recs):
    """q2-subsonic before q2-v1.0 (the firmware carried q2-v0.9 until p32) has no radio=1: no app is asked, the
    library has nothing like a server song, and "No similar songs on this Q2 Muse" read as the server having none."""
    q2, world = streaming(fw, ui, recs, b'name=Subsonic\n')
    q2.tap(None)
    assert b'/tmp/q2-subsonic.radio' not in world.files and not ui.played
    assert ui.toasts == ['Update Subsonic to use Radio']
