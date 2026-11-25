"""No playback without an output (patch/output_rule.c, docs/internals.md#no-playback-without-an-output): stock's
Headphones Required held on after config_init, the car's USB audio counted as an output beside stock's own, and the
starts of Videos, Internet Radio and Radio asking stock's gate, check_forbid_play_no_headset, as the Library's do."""
import ctypes as C

import pytest

from conftest import O
from test_radio import Vid, exec_args, page as radio_page, play as radio_station, rd
from test_radio_tuner import TUNER, Tuner
from test_usb_car import auto, state

KEY_PLAY = O['KEY_PLAY']


def flag(fw):
    return fw.var('g_block_withoutphone_flag', C.c_ubyte)


# ---- the switch held on ----

def test_the_switch_is_held_on_whatever_config_ini_says(fw):
    """config.ini's WITHOUTPHONE=0 (every Q2 until now) read by stock's config_init: Vamp sets it on after."""
    f = flag(fw)
    f.value = 0
    fw.mock('stock_usb_config_trampoline', lambda *_: (setattr(f, 'value', 0), 0)[1])
    fw.mock('stock_usb_detect_trampoline', lambda *_: 0)
    fw.fn('ringnav_usb_config', C.c_int)()
    assert f.value == 1


# ---- the car's USB audio is an output (ringnav_valid_output, has_valid_output_device's hook) ----

def valid(fw, stock):
    fw.mock('stock_valid_output_trampoline', lambda *_: stock)
    return fw.fn('ringnav_valid_output', C.c_int)()


@pytest.mark.parametrize('stock', [0, 1])
def test_stock_s_outputs_answer_as_before_with_no_car(fw, stock):
    assert valid(fw, stock) == stock


def test_the_car_asking_for_audio_is_an_output(fw):
    """2026-11-21's drive: the car's Play would have been refused with the switch on and no jack in."""
    u = auto(fw)
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    assert valid(fw, 0) == 1


def test_a_car_on_the_cable_that_takes_no_audio_is_none(fw):
    u = auto(fw)
    state(u, verdict='car', host=1, name='BMW 83828', audio=0, flowing=0, troubles=0)
    assert valid(fw, 0) == 0


def test_a_relink_keeps_the_car_an_output_until_it_gives_up(fw):
    """The car's audio gone while q2car.sh shows the car the iPhone again (USB_CAR_RELINK): still an output, so stock's
    restarts do not pause under it, until the relink ends without it (usb_car_gone's own wait)."""
    u = auto(fw)
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    u.files.files['/tmp/q2car.relink'] = b''
    fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in u.files.files else -1)
    state(u, verdict='car', host=1, name='BMW 83828', audio=0, flowing=0, troubles=0)
    assert valid(fw, 0) == 1
    del u.files.files['/tmp/q2car.relink']
    state(u, verdict='car', host=1, name='BMW 83828', audio=0, flowing=0, troubles=0)
    assert valid(fw, 0) == 0


def test_the_cable_out_ends_the_car_s_output(fw):
    u = auto(fw)
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    u.unplug()
    assert valid(fw, 0) == 0


# ---- the starts of Vamp's own players ----

def forbid(fw, forbidden):
    asked = []
    fw.mock('check_forbid_play_no_headset', lambda quiet, *_: asked.append(quiet) or forbidden)
    return asked


@pytest.mark.parametrize('forbidden', [1, 0])
def test_a_video_starts_only_with_an_output(fw, forbidden):
    asked = forbid(fw, forbidden)
    forks = []
    fw.mock('fw_fork', lambda *_: forks.append(1) or 4242)
    fw.mock('fw_socket', lambda *_: 9)
    path = C.create_string_buffer(b'/mnt/mmc/Videos/clip.mp4')
    assert fw.fn('video_click', C.c_int, C.c_char_p)(path) == 1  # a video: never left to stock's folder click
    assert asked == [0] and len(forks) == (not forbidden)
    assert fw.fn('video_status', C.c_int)() == (0 if forbidden else 2)
    fw.var('videos__vid', Vid).pid = 0  # the next test's video_play starts from none


@pytest.mark.parametrize('forbidden', [1, 0])
def test_a_paused_video_resumes_only_with_an_output(fw, forbidden):
    """Play on the video (video_key) or a headset's or Car Remote's play (video_set_playing): a resume is a start."""
    v = fw.var('videos__vid', Vid)
    sent = []
    fw.mock('fw_sendto', lambda s, data, n, *_: sent.append(C.string_at(data, n)) or n)
    for resume in (lambda: fw.fn('video_key', None, C.c_uint)(KEY_PLAY),
                   lambda: fw.fn('video_set_playing', None, C.c_int)(1)):
        v.pid, v.sock, v.paused = 4242, 9, 1
        asked = forbid(fw, forbidden)
        sent.clear()
        resume()
        assert asked == [0] and v.paused == forbidden and (sent == []) == bool(forbidden)
    v.pid, v.paused = 4242, 0
    asked = forbid(fw, 1)
    fw.fn('video_set_playing', None, C.c_int)(0)  # a pause always goes through
    assert asked == [] and v.paused == 1
    v.pid = 0


@pytest.mark.parametrize('forbidden', [1, 0])
def test_a_station_starts_only_with_an_output(fw, forbidden):
    p = radio_page(fw)
    asked = forbid(fw, forbidden)
    player = radio_station(fw, p, 3)
    assert asked == [0] and player.started == ([] if forbidden else [4242])
    assert rd(fw).pid == (0 if forbidden else 4242)


def test_a_stopped_station_plays_again_only_with_an_output(fw):
    p = radio_page(fw)
    forbid(fw, 0)
    player = radio_station(fw, p, 2)
    assert fw.fn('radio_media', C.c_int, C.c_uint)(KEY_PLAY) == 1 and rd(fw).pid == 0  # stopped
    asked = forbid(fw, 1)
    player.started.clear()
    fw.fn('radio_media', C.c_int, C.c_uint)(KEY_PLAY)
    assert asked == [0] and player.started == [] and rd(fw).pid == 0


@pytest.mark.parametrize('forbidden', [1, 0])
def test_radio_s_play_on_its_page_and_from_a_headset_starts_only_with_an_output(fw, forbidden):
    t = Tuner(fw)
    try:
        rt = t.rt
        for resume in (lambda: t.key(KEY_PLAY), lambda: fw.fn('tuner_set_playing', None, C.c_int)(1)):
            rt.screen, rt.freq, rt.playing = TUNER, 9310, 0
            asked = forbid(fw, forbidden)
            resume()
            assert asked == [0] and rt.playing == (not forbidden)
        if not forbidden:  # playing: a pause always goes through
            asked = forbid(fw, 1)
            t.key(KEY_PLAY)
            assert asked == [] and rt.playing == 0
    finally:
        fw.var('radio_tuner__rt', C.c_void_p).value = None


def test_radio_s_page_does_not_open_with_nothing_to_play_to(fw):
    """Home's Radio row: refused, stock's message and no page, no receiver, no fetch (as stock's Now Playing closes)."""
    asked = forbid(fw, 1)
    forks = []
    fw.mock('fw_fork', lambda *_: forks.append(1) or 4242)
    fw.var('radio_tuner__rt', C.c_void_p).value = None
    assert fw.fn('tuner_open', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert asked == [0] and forks == [] and fw.var('radio_tuner__rt', C.c_void_p).value is None
    assert fw.fn('tuner_state', C.c_int)() == 0


@pytest.mark.parametrize('car', [1, 0])
def test_with_no_jack_or_bluetooth_a_station_plays_into_the_car_or_is_refused_once(fw, car):
    """No jack and no Bluetooth. With the car taking the USB audio (q2-iap's car file there, the car asking for audio),
    the car counts as an output: an Internet Radio station starts, routed into the car (q2video on q2car), with no
    toast. With no car either, stock's gate refuses it with its one message and nothing starts."""
    u = auto(fw)  # the car first: its stand-ins for the timers and files, then the page's over them
    state(u, verdict='car', host=1, name='BMW 83828', audio=car, flowing=car, troubles=0)
    p = radio_page(fw, {'/tmp/q2-iap.audio': b'hw:CARD=UAC1Gadget,0\n44100\n'} if car else None)
    fw.mock('fw_system', lambda *_: 0)
    fw.mock('stock_valid_output_trampoline', lambda *_: 0)  # no jack, no Bluetooth
    refused = []

    def gate(quiet, *_):  # stock's check_forbid_play_no_headset: its message (msg_noheadset) when it refuses
        ok = fw.fn('ringnav_valid_output', C.c_int)()
        if not ok and not quiet:
            refused.append(1)
        return int(not ok)
    fw.mock('check_forbid_play_no_headset', gate)
    fw.mock('fw_fork', lambda *_: 0)  # the child's side
    got = exec_args(fw)
    p.menu('Featured')
    p.rows('station_click')[3]()
    assert p.toasts == []
    if car:
        assert refused == [] and got and got[0][3:4] == ['q2car'], got
    else:
        assert refused == [1] and got == [], got


# ---- Now Playing opened to start a track: asked at the navigator's door (ringnav_nav_to) ----

def nav_to(fw, forbidden, page=b'playing_page', cls=0xf001):
    """navigator_to_with_context(page, {queue, 0, cls, 2}) through the hook: (what the gate was asked, the pages
    stock's navigator was asked to open, the result)."""
    asked = forbid(fw, forbidden)
    opened = []
    fw.mock('stock_nav_to_trampoline', lambda name, *_: opened.append(C.string_at(name)) or 0)
    ctx = (C.c_int * 4)(0x1234, 0, cls, 2)
    ret = fw.fn('ringnav_nav_to', C.c_int, C.c_char_p, C.c_void_p)(page, ctx)
    return asked, opened, ret


def test_a_start_with_nothing_to_play_to_opens_no_now_playing(fw):
    """The owner's report (2026-11-22): a song's tap with no output slid in Now Playing with stock's placeholder title
    under the toast. Refused at the navigator, the page is never made (STOP, as playing_page_init answered)."""
    assert nav_to(fw, 1) == ([0], [], 11)


def test_a_start_with_an_output_opens_now_playing(fw):
    assert nav_to(fw, 0) == ([0], [b'playing_page'], 0)


@pytest.mark.parametrize('page, cls', [(b'playing_page', 0xff), (b'localmusic/album_page', 0xf001)])
def test_now_playing_to_show_what_plays_and_other_pages_ask_nothing(fw, page, cls):
    """0xff opens Now Playing on what plays or is paused, starting nothing (the status bar's button, Control Center);
    another page's context may not be a queue's at all (the playlist page's is a number), so it is never read."""
    assert nav_to(fw, 1, page, cls) == ([], [page], 0)


# ---- the payload's starts that set something on the way to the navigator ----

@pytest.fixture
def refusing(fw, mcl):
    """A fake AWTK whose navigator goes through ringnav_nav_to, as the device's does, with nothing to play to: the
    UI, what the gate was asked and the stock calls a start makes."""
    from test_nav_library_pages import Ui
    ui = Ui(fw)
    asked = forbid(fw, 1)
    fw.mock('stock_nav_to_trampoline', lambda name, info, *_: ui.navigate(name, info) or 0)
    nav_to = fw.fn('ringnav_nav_to', C.c_int, C.c_void_p, C.c_void_p)
    fw.mock('navigator_to_with_context', lambda name, info, *_: nav_to(name, info))
    fw.mock('mclLoadPlayList', lambda *_: 0)
    fw.mock('window_manager_close_window_force', lambda *_: 0)
    return ui, asked


def songs_of(fw, ui, n=4):
    from test_nav_library_pages import Songs
    song = Songs()
    fw.songs_kept = song  # the records' strings live as long as the test
    return [song(b'/m/%c/%d.flac' % (65 + i % 2, i), album=b'%c' % (65 + i % 2)) for i in range(n)]


def unchanged(fw, ui):
    """What a refused start must leave: the advanced play mode, stock's play mode, the resume record, and no page."""
    return ([fw.fn('playback_option', C.c_int, C.c_int)(k) for k in range(3)], fw.calls('config_playmode'),
            fw.calls('mclLoadPlayList'), C.string_at(C.addressof(C.c_char.in_dll(fw.lib, 'fw_ctx_g_memory_info')), 3476),
            ui.played, ui.toasts, [ui.name.get(w) for w in ui.windows])


def test_a_refused_shuffle_changes_no_mode_and_shows_only_the_gate_s_message(fw, refusing):
    """The emulator (2026-11-22, the refused-tap agent): Songs' Shuffle with no output switched to Shuffle and showed
    "Shuffled Songs" under the toast. shuffle_play (Songs' and Home's Shuffle Songs, an album's, an artist's and a
    playlist's Shuffle) asks the gate before its mode changes; its answer is still the list's size, so no caller
    adds "No songs"."""
    ui, asked = refusing
    songs = songs_of(fw, ui)
    assert fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(1, 4) == 1  # an advanced repeat the user chose
    before = unchanged(fw, ui)
    assert fw.fn('shuffle_play', C.c_int, C.c_void_p)(ui.deques.new(songs)) == 4
    assert asked == [0] and unchanged(fw, ui) == before


def test_a_refused_shuffle_songs_row_says_nothing_of_the_library(fw, refusing):
    from test_nav_library_pages import Library, shuffle_all
    ui, asked = refusing
    Library(fw, ui, songs_of(fw, ui))
    before = unchanged(fw, ui)
    shuffle_all(fw)
    assert asked == [0] and unchanged(fw, ui) == before


@pytest.mark.parametrize('folders', [0, 1])
def test_a_refused_shuffle_albums_or_folders_loads_no_queue(fw, refusing, folders):
    """Shuffle Albums and Folders loaded the whole library as the queue (playback_groups' mclLoadPlayList) before
    the navigator refused its Now Playing: asked first, before the library is read."""
    from test_nav_library_pages import Library, shuffle_all
    ui, asked = refusing
    library = Library(fw, ui, songs_of(fw, ui))
    before = unchanged(fw, ui)
    shuffle_all(fw, 1 + folders)
    assert asked == [0] and unchanged(fw, ui) == before and library.asked == []


def test_a_refused_play_folder_keeps_now_playing_and_the_resume_album(fw, refusing):
    """play_folder closed a Now Playing on top, and wrote an album queue's album into the resume record, before the
    navigator refused the new one: Cover Flow's tracks and an album's Play over Now Playing left Return without it."""
    ui, asked = refusing
    songs = songs_of(fw, ui)
    np = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[np] = 'playing_page'
    before = unchanged(fw, ui)
    fw.fn('play_folder', None, C.c_void_p, C.c_int, C.c_int)(ui.deques.new(songs), 1, 0xff10)
    assert asked == [0] and unchanged(fw, ui) == before and not fw.calls('window_manager_close_window_force')
