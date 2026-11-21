"""patch/now_source.c (docs/internals.md#now-playing-source): Now Playing, one answer for the readers outside the
Q2's own pages. The source as it moves (the Library's song playing, paused, sought, the next song; Radio over it,
then the Library again), and each reader's view of it: bluetoothd's record, status and BT_PLAY_FILE for
GetPlayStatus (now_poll), and Car Remote's and the iPhone cable's /tmp/coda-rc.now (coda_rc_shim.c cr_now).
The Library's songs and player are test_coda_rc_shim's Q2; Radio's page is test_radio_tuner's Rt on a heap of
the test's."""
import ctypes as C
import struct

from conftest import O, mcl  # noqa: F401
from test_coda_rc_shim import NOW, Q2
from test_nav_library_pages import ui  # noqa: F401
from test_nav_hold import recs  # noqa: F401
from test_radio_tuner import Rt

PLAY, COVER = '/tmp/q2-bt.play', '/tmp/q2-bip.now'
# iHeart's StreamTitle as q2video writes it from the stream (the drive of 2026-11-20)
ICY = 'title="Toxicity",artist="SYSTEM OF A DOWN",url="song_spot=\\"M\\" MediaBaseId=\\"1218\\" itunesTrackId=\\"0\\""'


class Meta(C.Structure):
    """libbtctl's track record as btctl_notify_track_changed takes it (now_source.c struct np_meta)"""
    _fields_ = [('track', C.c_int), ('total', C.c_int), ('ms', C.c_int), ('title', C.c_char_p), ('artist', C.c_char_p),
                ('album', C.c_char_p), ('genre', C.c_char_p)]


class Car(Q2):
    """The Library's Q2 with a monotonic clock that is the test's, Radio's page shut, and bluetoothd's side seen."""
    def __init__(self, fw, ui, recs):
        super().__init__(fw, ui, recs, on=0)
        fw.mock('fw_clock_gettime', lambda which, ts, *_: C.memmove(ts, struct.pack('<qq', self.ms // 1000,
                                                                                    self.ms % 1000 * 1000000), 16) and 0)
        self.tracks = []
        fw.mock('btctl_notify_track_changed', self.track)
        self.rt = None
        self.now_poll = fw.fn('now_poll', None)
        self.files[COVER] = b'1234567\n/mnt/mmc/a/cover.jpg\n'  # the Library song's cover (bt_cover.c)
        self.queue()

    def track(self, m, *_):
        r = Meta.from_address(m)
        self.tracks.append((r.track, r.total, r.ms, r.title.decode(), r.artist.decode(), r.album.decode(), r.genre))
        return 0

    def step(self, ms=500):
        self.ms += ms
        self.now_poll()

    def play_file(self):
        pos, length, at = self.files[PLAY].decode().split()
        return int(pos), int(length), int(at)

    def radio(self, playing=1, title=ICY, name=b'93XRT', call=b'WXRT', freq=9310):
        """Radio's page open on a station's stream (test_radio_tuner's Rt), as tuner_poll leaves it"""
        self.rt = Rt()
        self.rt.playing, self.rt.audio, self.rt.feed, self.rt.freq = playing, 999, 0, freq
        self.rt.st[0].freq, self.rt.st[0].name, self.rt.st[0].call = freq, name, call
        self.rt.title_text = title.encode()
        self.fw.var('radio_tuner__rt', C.c_void_p).value = C.addressof(self.rt)

    def radio_off(self):
        self.fw.var('radio_tuner__rt', C.c_void_p).value = 0
        self.rt = None


def test_the_library_position_is_written_with_its_stamp_and_moves_only_by_events(fw, ui, mcl, recs):
    car = Car(fw, ui, recs)
    car.step()
    assert car.play_file() == (30000, 200000, car.ms)
    stamp = car.ms
    car.step()  # the player still at 30 s: the time since the stamp is bluetoothd's to add
    assert car.play_file()[2] == stamp
    car.sec = 31
    car.step()  # its first new second after a write: the second's start, stamped
    assert car.play_file() == (31000, 200000, car.ms)
    stamp = car.ms
    for s in (32, 33, 34):
        car.sec = s
        car.step(1000)
    assert car.play_file()[2] == stamp, 'locked on: the seconds as the time since predicts them write nothing'


def test_pause_seek_and_the_next_song_are_written_at_once(fw, ui, mcl, recs):
    car = Car(fw, ui, recs)
    car.step()
    car.status = 3  # paused
    car.step()
    assert car.play_file() == (30000, 200000, car.ms)
    car.sec = 120  # a seek while paused: no time passes for a paused song, so any move is one
    car.step(3000)
    assert car.play_file() == (120000, 200000, car.ms)
    car.status, car.sec = 2, 121
    car.step()
    assert car.play_file()[0] == 121000
    car.sec = 10  # a seek back while playing
    car.step()
    assert car.play_file() == (10000, 200000, car.ms)
    car.queue(at=2)  # the next song
    car.sec, car.total = 0, 180
    car.step()
    assert car.play_file() == (0, 180000, car.ms)
    car.status = 1  # stopped: no position
    car.step()
    assert car.play_file()[0] == -1


def test_radio_is_the_car_s_song_and_the_library_comes_back_after(fw, ui, mcl, recs):
    """Radio over the Library: bluetoothd told the stream's song apart from iHeart's junk, the station and its
    frequency as the album, no number and no length, playing; no cover; GetPlayStatus live. Paused, paused. Shut,
    the Library's song and status again, as Guest Play hands back, and its cover."""
    car = Car(fw, ui, recs)
    car.status = 1  # video_start stopped the player
    car.step()
    car.radio()
    car.step()
    assert car.tracks == [(-1, -1, -1, 'Toxicity', 'System of a Down', '93XRT 93.1 FM', None)]
    assert [a[0] for _, a in fw.calls('btctl_notify_playback_status_changed')] == [1]
    assert COVER not in car.files and COVER + '.held' in car.files, 'the Library\'s cover set aside: none for a stream'
    assert car.play_file()[:2] == (-1, -1)
    car.step()
    assert len(car.tracks) == 1, 'nothing new: nothing sent'
    car.rt.title_text = b'title="Chop Suey!",artist="SYSTEM OF A DOWN",url="x"'
    car.step()
    assert car.tracks[-1][3:6] == ('Chop Suey!', 'System of a Down', '93XRT 93.1 FM')
    car.rt.playing = 0
    car.step()
    assert [a[0] for _, a in fw.calls('btctl_notify_playback_status_changed')] == [1, 2]
    assert not fw.calls('sendBtHeadsetPlayInfo')
    car.radio_off()
    car.status, car.sec = 3, 30
    car.step()
    assert len(fw.calls('sendBtHeadsetPlayInfo')) == 1 and len(fw.calls('sendBtHeadsetPlayStatus')) == 1
    assert COVER in car.files and COVER + '.held' not in car.files
    assert car.play_file()[:2] == (30000, 200000)
    car.step()
    assert len(fw.calls('sendBtHeadsetPlayInfo')) == 1, 'the Library is stock\'s to tell from here'


def test_between_stations_the_dial_names_it(fw, ui, mcl, recs):
    car = Car(fw, ui, recs)
    car.radio(title='', name=b'', call=b'WCFS')
    car.step()
    assert car.tracks == [(-1, -1, -1, 'WCFS', '93.1 FM', 'WCFS 93.1 FM', None)]


def test_a_guest_s_song_is_left_to_the_guest(fw, ui, mcl, recs):
    car = Car(fw, ui, recs)
    fw.var('guest__gp', C.c_int * 519)[518] = 1  # gp.active (guest.c), at 2072
    car.radio()
    car.step()
    assert not car.tracks and PLAY not in car.files


def test_car_remote_and_the_cable_see_radio_as_the_song(fw, ui, mcl, recs):
    """cr_now's /tmp/coda-rc.now from now_get: the station live, with no length, position, hearts or path (so no
    cover), and the Library's song again after."""
    car = Car(fw, ui, recs)
    fw.fn('usb_car_start', None)()
    car.radio()
    car.tick()
    now = car.now()
    assert (now['source'], now['live'], now['station'], now['state']) == ('radio', '1', '93XRT', '1')
    assert (now['title'], now['artist'], now['album']) == ('Toxicity', 'System of a Down', '93XRT 93.1 FM')
    assert (now['len'], now['pos'], now['hearts'], now['path'], now['id']) == ('0', '0', '-1', '', '0')
    car.radio_off()
    car.tick()
    now = car.now()
    assert (now['source'], now['live'], now['title'], now['pos']) == ('library', '0', 'Song 1', '30000')


def test_tuner_song_takes_the_artist_and_the_song_apart(fw):
    song = fw.fn('tuner_song', None, C.c_char_p, C.c_char_p, C.c_char_p, C.c_uint)
    def parts(raw):
        a, s = C.create_string_buffer(160), C.create_string_buffer(160)
        song(raw.encode(), a, s, 160)
        return a.value.decode(), s.value.decode()
    assert parts(ICY) == ('System of a Down', 'Toxicity')
    assert parts('Bill Withers - Lovely Day') == ('Bill Withers', 'Lovely Day')
    assert parts('WXRT Chicago') == ('', 'WXRT Chicago')
    assert parts('text="Spot Block" amgTrackId="9"') == ('', 'Spot Block')
    assert parts('') == ('', '')
