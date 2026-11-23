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
from test_radio import exec_args

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
        self.rt: Rt | None = None
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

    def radio(self, playing=1, title=ICY, name=b'93XRT', call=b'WXRT', freq=9310) -> Rt:
        """Radio's page open on a station's stream (test_radio_tuner's Rt), as tuner_poll leaves it: that Rt"""
        rt = self.rt = Rt()
        self.rt.playing, self.rt.audio, self.rt.feed, self.rt.freq = playing, 999, 0, freq
        self.rt.st[0].freq, self.rt.st[0].name, self.rt.st[0].call = freq, name, call
        self.rt.title_text = title.encode()
        self.fw.var('radio_tuner__rt', C.c_void_p).value = C.addressof(rt)
        return rt

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
    rt = car.radio()
    car.step()
    assert car.tracks == [(-1, -1, -1, 'Toxicity', 'System of a Down', '93XRT 93.1 FM', None)]
    assert [a[0] for _, a in fw.calls('btctl_notify_playback_status_changed')] == [1]
    assert COVER not in car.files and COVER + '.held' in car.files, 'the Library\'s cover set aside: none for a stream'
    assert car.play_file()[:2] == (-1, -1)
    car.step()
    assert len(car.tracks) == 1, 'nothing new: nothing sent'
    rt.title_text = b'title="Chop Suey!",artist="SYSTEM OF A DOWN",url="x"'
    car.step()
    assert car.tracks[-1][3:6] == ('Chop Suey!', 'System of a Down', '93XRT 93.1 FM')
    rt.playing = 0
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


def test_radio_holds_the_library_paused_where_it_was(fw, ui, mcl, recs):
    """The Library playing at 61 s, then Radio's page opens: the player is stopped with its place kept
    (player_save_stop: a paused q2player would keep the PCM Radio needs), and the Library reads paused at 61 s to
    every reader for the whole of Radio, whatever the time; Radio's page shut, the song is loaded again paused at
    61 s (player_save_start(1)), not playing and not at 0, and the car is told so."""
    car = Car(fw, ui, recs)
    fw.fn('usb_car_start', None)()  # the cable's Now Playing too (coda-rc.now, q2-iap's)
    car.sec = 61
    stops, saves, starts = [], [], []

    def stop(*_):  # stock's: the player stopped, its place gone
        stops.append(1)
        car.status, car.sec = 1, -1
    def save_stop(*_):
        saves.append((car.status, car.sec))
        car.status, car.sec = 1, -1
    def save_start(paused, *_):
        starts.append(paused)
        car.status, car.sec = 3 if paused else 2, 61
    fw.mock('player_stop', lambda *_: stop() or 0)
    fw.mock('player_save_stop', lambda *_: save_stop() or 0)
    fw.mock('player_save_start', lambda p, *_: save_start(p) or 0)
    fw.mock('fw_fork', lambda *_: 4242)
    fw.mock('fw_waitpid', lambda *_: 4242)  # the receiver goes when asked
    fw.fn('tuner_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert saves == [(2, 61)] and car.status == 1, 'the Library stopped with its place kept, not lost'
    sec, total = C.c_int(), C.c_int()
    time_ = fw.fn('now_time', C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int))
    lyrics_sec = fw.fn('lyrics_sec', C.c_int)
    for _ in range(4):
        car.step(5000)  # Radio plays on: the Library's second stands, on the Library's own pages too
        assert time_(C.byref(sec), C.byref(total)) == 3 and sec.value == 61 and total.value == 200
        assert lyrics_sec() == 61000
        car.tick(0)
        assert car.now()['source'] == 'radio'
    fw.fn('radio_tuner__closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert starts == [1], 'loaded again paused, for the user\'s Play'
    car.step()
    assert time_(C.byref(sec), C.byref(total)) == 3 and sec.value == 61
    assert car.play_file()[:2] == (61000, 200000) and len(fw.calls('sendBtHeadsetPlayStatus')) == 1
    assert fw.fn('now_status', C.c_int)() == 2
    car.tick()
    assert (car.now()['source'], car.now()['state'], car.now()['pos']) == ('library', '2', '61000')


def test_radio_plays_to_the_car_while_the_car_takes_the_audio(fw, ui, mcl, recs):
    """The drive of 2026-11-22: Radio opened while the car played the Q2 over USB; its sound went to demo's output (A2DP
    to the car) and iDrive showed its station over silence. While the car takes the audio (q2-iap's car file), the
    receiver plays to ALSA's q2car, the player's feed to the car (docs/iphone-mode.md#other-sources-over-usb); the
    Library is held as for any Radio."""
    car = Car(fw, ui, recs)
    fw.fn('usb_car_start', None)()  # Car Audio's iPhone on the cable
    car.files['/tmp/q2-iap.audio'] = b'hw:CARD=UAC1Gadget,0\n44100\n'
    fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in car.files else -1)
    fw.mock('fw_fork', lambda *_: 0)  # the child's side: q2video's arguments
    got = exec_args(fw)
    fw.fn('tuner_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert got[0] == ['/usr/bin/q2video', '/usr/bin/q2video', '-t', 'q2car', 'h100'], got
    assert ui.toasts == []


def test_the_owner_s_state_for_home_control_center_and_the_status_row(fw, ui, mcl, recs):
    """Home's first row, Control Center and the status row's play glyph read now_status: Radio playing is
    playing (they read the Library alone, stopped by then, before), paused is paused; the Library's own as it was."""
    car = Car(fw, ui, recs)
    hn = fw.fn('home_np__hn_state', C.c_int)
    cc = fw.fn('control_center__cc_np_loaded', C.c_int)
    bar = fw.fn('bar_status', C.c_int)
    assert (hn(), cc(), bar()) == (0, 1, 2)  # the Library playing: HN_PLAYING, loaded, 2
    car.status = 3
    assert (hn(), cc(), bar()) == (1, 1, 3)
    car.status = 1  # Radio's page stopped the player
    rt = car.radio()
    assert (hn(), cc(), bar()) == (0, 1, 2), 'Radio playing'
    rt.playing = 0
    assert (hn(), cc(), bar()) == (1, 1, 3), 'Radio paused'
    car.radio_off()
    assert (hn(), cc(), bar()) == (1, 0, 1), 'nothing loaded'


def test_only_the_library_is_scrobbled(fw, ui, mcl, recs):
    car = Car(fw, ui, recs)
    scrobbles = fw.fn('now_scrobbles', C.c_int)
    assert scrobbles() == 1
    car.radio()
    assert scrobbles() == 0
    car.radio_off()
    assert scrobbles() == 1


def test_radio_left_plays_on_and_comes_back_where_it_was(fw, ui, mcl, recs):
    """Radio's page shut on a station (Back, Home, another page): the receiver and its station stay, Radio stays
    the owner, the Library stays held; the page opened again lands on that frequency with the same receiver (no new
    q2video, no new search). A page opened with nothing running starts on the station last landed on (the card's
    /mnt/mmc/Radio/.local/freq), as the Library resumes."""
    car = Car(fw, ui, recs)
    forks = []
    fw.mock('fw_fork', lambda *_: forks.append(1) or 4242)
    fw.mock('fw_waitpid', lambda *_: 0)  # the receiver runs
    car.files['/mnt/mmc/Radio/.local/freq'] = b'10590'
    open_ = fw.fn('tuner_open', C.c_int, C.c_void_p, C.c_void_p)
    open_(None, None)
    rt_var = fw.var('radio_tuner__rt', C.c_void_p)
    rt = Rt.from_address(rt_var.value)
    assert rt.freq == 10590, 'the last station, kept across a restart'
    rt.screen, rt.n, rt.freq, rt.feed, rt.st[0].freq = 1, 1, 9312, 0, 9310  # the tuner, on 93.12 by 93.1
    rt.st[0].url = b'https://xrt'
    page = rt.page
    ui.close(page)  # Back: stock closes the window, closed runs
    assert rt_var.value and not Rt.from_address(rt_var.value).page
    assert fw.fn('now_owner', C.c_int)() == 2 and fw.fn('tuner_state', C.c_int)() == 1
    n = len(forks)
    open_(None, None)
    rt = Rt.from_address(rt_var.value)
    assert rt.page and rt.screen == 1 and rt.freq == 9312 and len(forks) == n


def test_the_status_glyph_follows_radio_off_its_page_until_the_library_is_chosen(fw, ui, mcl, recs):
    """The status row's play glyph (bar_status) and Home's first row (hn_state) through Radio's life: the Library
    playing; Radio playing, between stations and with its receiver gone too (playing is the user's Play: "as long as
    in radio, whether on station or not... now playing"); Radio paused; its page left, still paused; Play from the
    car, playing; a Library pick, the Library's own again."""
    car = Car(fw, ui, recs)
    hn, bar = fw.fn('home_np__hn_state', C.c_int), fw.fn('bar_status', C.c_int)
    assert (hn(), bar()) == (0, 2), 'the Library playing'
    car.status = 1  # Radio's page stopped the player
    rt = car.radio()
    libc = C.CDLL(None)
    libc.calloc.restype = C.c_void_p
    heap = libc.calloc(1, C.sizeof(Rt))  # tuner_open's: tuner_yield frees it
    C.memmove(heap, C.addressof(rt), C.sizeof(Rt))
    fw.var('radio_tuner__rt', C.c_void_p).value = heap
    rt = Rt.from_address(heap)
    rt.n, rt.sock = 1, -1
    assert (hn(), bar()) == (0, 2), 'Radio playing'
    rt.feed, rt.audio = -1, 0  # between stations, the receiver gone a moment: still playing
    assert (hn(), bar()) == (0, 2)
    rt.audio = 999
    fw.mock('fw_waitpid', lambda *_: 999)
    fw.fn('media_set_playing', None, C.c_int)(0)  # Pause
    assert (hn(), bar()) == (1, 3), 'Radio paused'
    fw.fn('radio_tuner__closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)  # Back to Home
    assert (hn(), bar()) == (1, 3), 'its page left, still paused'
    fw.fn('media_set_playing', None, C.c_int)(1)  # the car's Play: Radio's
    assert (hn(), bar()) == (0, 2) and car.status == 1
    fw.fn('tuner_yield', None)()  # a Library pick (ringnav_load)
    assert not fw.var('radio_tuner__rt', C.c_void_p).value
    car.status = 2
    assert (hn(), bar()) == (0, 2) and fw.fn('now_owner', C.c_int)() == 1, 'the Library\'s own again'


def test_a_song_loaded_paused_at_a_place_reads_it_until_heard_and_another_song_does_not(fw, ui, mcl, recs):
    """now_starting notes where the Library's song starts (MCL_START_SEC); loaded paused there, the player reads 0
    until heard, and now_time reads the place: never once it plays, nor for the next song."""
    car = Car(fw, ui, recs)
    car.status, car.sec = 3, 0
    mcl.start_sec = 61
    fw.fn('now_starting', None)()
    mcl.start_sec = -1
    sec, total = C.c_int(), C.c_int()
    now_time = fw.fn('now_time', C.c_int, C.c_void_p, C.c_void_p)
    assert now_time(C.byref(sec), C.byref(total)) == 3 and sec.value == 61
    mcl.pos += 1  # the next song, loaded paused from its start
    assert now_time(C.byref(sec), C.byref(total)) == 3 and sec.value == 0
    mcl.pos -= 1
    assert now_time(C.byref(sec), C.byref(total)) == 3 and sec.value == 0  # forgotten, not back
    fw.fn('now_starting', None)()  # a start with no place (-1): the player's own
    assert now_time(C.byref(sec), C.byref(total)) == 3 and sec.value == 0
