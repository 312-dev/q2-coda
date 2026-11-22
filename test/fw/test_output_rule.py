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
    """config.ini's WITHOUTPHONE=0 (every Q2 until now) read by stock's config_init: Q2 Muse sets it on after."""
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


# ---- the starts of Q2 Muse's own players ----

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
