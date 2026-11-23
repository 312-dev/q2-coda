"""Car Audio, the USB setting's Automatic (patch/usb_car.c, patch/usb_ui.c, docs/iphone-mode.md#car-audio-in-the-image):
the iPhone shown at a cable in, q2-iap's verdict read from its state file, a car kept and its toasts, any other host
handed to Transfer Files, a charger left to charge, the gadget stopped with the cable or before a picked mode."""
import ctypes as C

from conftest import O  # noqa: F401
from test_coverflow import Ui  # noqa: F401
from test_nav_painters import Canvas, scratch, st  # noqa: F401 (fixtures)
from test_usb_ui import CAR_START, UDC, Usb, picker

STATE = '/tmp/q2car.state'
CAR_STOP_BG = '( /usr/bin/q2car stop >/dev/null 2>&1; : > /tmp/q2car.stopped ) &'
CAR_STOP_START = ('( /usr/bin/q2car stop >/dev/null 2>&1; : > /tmp/q2car.stopped; /usr/bin/q2car start >/dev/null 2>&1 )'
                  ' &')
CAR_STOP = '/usr/bin/q2car stop >/dev/null 2>&1'  # the blocking form usb_ui.c's pick once ran on the UI thread
STOPPED = '/tmp/q2car.stopped'
PCM = '/proc/asound/UAC1Gadget/pcm0p/sub0/status'
RELEASE = '{mcl-carrelease\\null}'


def auto(fw, **kw):
    """A cable in on Automatic (USBPICK 0, the setting's default)."""
    u = Usb(fw, ini={'USBPICK': 0}, **kw)
    u.plug(host=False)
    return u


def state(u, ms=200, **kv):
    """q2-iap's state file (patch/q2_iap.c state_write) as given, then a UI pass USB_CAR_LOOK_MS on."""
    u.files.files[STATE] = ''.join(f'{k}={v}\n' for k, v in kv.items()).encode()
    u.now += ms
    u.poll()


def test_automatic_shows_the_iphone_and_holds_stock_out(fw):
    """No chooser, no storage probe, no adb: q2car's iPhone, and stock kept off the cable (g_usblink_status 3)."""
    u = auto(fw)
    assert u.commands == [CAR_START] and u.link.value == 3
    assert not u.page('usbchoose_page') and not u.page('usbdebug_page')
    state(u, verdict='wait', host=1)
    assert u.commands == [CAR_START] and u.link.value == 3 and not u.toasts


def test_a_computer_gets_transfer_files_once_the_iphone_is_gone(fw):
    """q2-iap's computer verdict is acted on only once q2car.sh has removed the gadget (down=1): then stock's storage
    gadget, in Files mode for this cable (g_usblink_status 0, g_usbmode 2), and nothing saved."""
    u = auto(fw, tile=1)
    state(u, verdict='computer', why='configuration 1', host=1)
    assert u.link.value == 3, 'the iPhone is still on the controller'
    state(u, verdict='computer', why='configuration 1', host=1, down=1)
    assert (u.link.value, u.mode.value, u.saved) == (0, 2, [])
    state(u, verdict='computer', why='configuration 1', host=1, down=1)
    assert u.link.value == 0 and not u.page('usbchoose_page')


def test_a_silent_host_gets_transfer_files_too(fw):
    u = auto(fw, tile=1)
    state(u, verdict='silent', why='nothing Apple\'s in 3000 ms', host=1, down=1)
    assert (u.link.value, u.mode.value) == (0, 2)


def test_a_car_keeps_the_iphone_and_says_when_it_plays(fw):
    """A car: nothing changes on the cable; the toast once its audio flows, with the car's own name."""
    u = auto(fw)
    state(u, verdict='car', why="Apple's C0 53", host=1, name='BMW 83828', audio=1, flowing=0, troubles=0)
    u.poll()
    assert u.link.value == 3 and not u.toasts
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    u.poll()
    assert u.toasts == ['Playing through BMW 83828 (USB)']
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    u.poll()
    assert u.toasts == ['Playing through BMW 83828 (USB)'], 'once per start of the flow'


def test_a_car_audio_problem_toasts_each_new_one(fw):
    u = auto(fw)
    state(u, verdict='car', name='BMW 83828', flowing=1, troubles=0)
    u.poll()
    state(u, verdict='car', name='BMW 83828', flowing=1, trouble='dropouts', troubles=1)
    u.poll()
    state(u, verdict='car', name='BMW 83828', flowing=1, trouble='dropouts', troubles=1)
    u.poll()
    state(u, verdict='car', name='BMW 83828', flowing=0, trouble='nostream', troubles=2)
    u.poll()
    assert u.toasts == ['Playing through BMW 83828 (USB)', 'Car audio problem: dropouts', 'Car audio problem: no sound']


def test_the_state_file_is_read_at_most_every_200_ms(fw):
    u = auto(fw)
    state(u, ms=100, verdict='computer', host=1, down=1)
    assert u.link.value == 3
    state(u, ms=100, verdict='computer', host=1, down=1)
    assert u.link.value == 0


def test_a_charger_only_charges(fw):
    """No host ever (no request, no address): after USB_CAR_CHARGER_MS the charge, once; the gadget stays, unseen."""
    u = auto(fw)
    state(u, ms=1000, verdict='wait', host=0)
    toasts = list(u.toasts)
    state(u, ms=2100, verdict='wait', host=0)
    u.poll()
    assert u.link.value == 3 and u.commands == [CAR_START]
    charged = u.toasts[len(toasts):]
    assert not toasts and len(charged) == 1 and charged[0].startswith('Charging'), charged
    state(u, ms=5000, verdict='wait', host=0)
    assert u.toasts[len(toasts):] == charged, 'told once'


def test_the_cable_out_stops_the_gadget_in_the_background(fw):
    """The stop does not hold the UI pass; a verdict left in the file after it changes nothing."""
    u = auto(fw, tile=1)
    u.unplug()
    assert u.commands == [CAR_START, CAR_STOP_BG]
    state(u, verdict='computer', host=1, down=1)
    assert u.mode.value == 1 and u.commands == [CAR_START, CAR_STOP_BG]


def test_a_pick_with_the_iphone_shown_stops_it_first(fw):
    """Charge Only picked while Automatic's iPhone is on the cable, the car's PCM closed: q2car stopped in the
    background, and the pick made once that stop has returned (STOPPED), remembered at once."""
    u = auto(fw)
    u.ui.click(u.row(picker(u), 'Charge Only'))
    assert u.commands == [CAR_START, CAR_STOP_BG] and u.saved == [('Q2POD', 'USBPICK', 1)]
    u.files.files[STOPPED] = b''
    u.poll()
    assert u.commands == [CAR_START, CAR_STOP_BG] and u.saved == [('Q2POD', 'USBPICK', 1)], 'remembered once'
    u.unplug()
    assert u.commands.count(CAR_STOP_BG) == 1, 'stopped once'


# ---- the player's release of the car's PCM before the stop (usb_car.c usb_car_stop, docs at its top) ----

def holding(fw, u, status=b'state: PAUSED\n'):
    """The paused player holding the car's PCM (its /proc status not "closed"), and demo's socket to it."""
    C.c_int.from_address(O['MCL_FD']).value = 7
    u.files.files[PCM] = status
    u.sent.clear()


def test_a_pick_never_blocks_on_the_player_holding_the_car_s_pcm(fw):
    """2026-11-22: the gadget's unbind sat in snd_card_free while the car's PCM was held open, and this pick ran it on
    the UI thread. Now the player is asked to release it and nothing runs until it has: the pick waits, the UI pass
    goes on; once the PCM reads closed, the stop in the background, and the pick after it."""
    u = auto(fw)
    holding(fw, u)
    u.ui.click(u.row(picker(u), 'Transfer Files'))
    assert u.sent == [RELEASE] and u.commands == [CAR_START], 'nothing run while the player holds it'
    assert CAR_STOP not in u.commands
    u.now += 500
    u.poll()
    assert u.commands == [CAR_START] and u.link.value == 3
    u.files.files[PCM] = b'closed\n'
    u.now += 100
    u.poll()
    assert u.commands == [CAR_START, CAR_STOP_BG] and u.link.value == 3, 'the stop, and the pick still waiting'
    u.files.files[STOPPED] = b''
    u.poll()
    assert u.link.value == 0 and u.mode.value == 2, "then Transfer Files: stock's storage gadget for this cable"
    assert u.sent == [RELEASE]


def test_a_player_that_never_lets_go_is_given_2_s(fw):
    u = auto(fw)
    holding(fw, u, b'state: RUNNING\n')
    u.ui.click(u.row(picker(u), 'Charge Only'))
    u.now += 1900
    u.poll()
    assert u.commands == [CAR_START]
    u.now += 100
    u.poll()
    assert u.commands == [CAR_START, CAR_STOP_BG]


def test_the_cable_out_asks_the_player_to_let_go_first(fw):
    """A song paused into the car and the cable out: the release, then the stop once the PCM is closed."""
    u = auto(fw)
    holding(fw, u)
    u.unplug()
    assert u.sent == [RELEASE] and u.commands == [CAR_START]
    u.files.files[PCM] = b'closed\n'
    u.now += 50
    u.poll()
    assert u.commands == [CAR_START, CAR_STOP_BG]


def test_no_car_card_needs_no_release(fw):
    """The function not bound (no card): nothing to let go of, the stop at once and nothing sent."""
    u = auto(fw)
    C.c_int.from_address(O['MCL_FD']).value = 7
    u.sent.clear()
    u.unplug()
    assert u.sent == [] and u.commands == [CAR_START, CAR_STOP_BG]


def test_a_cable_back_before_the_release_stops_then_starts(fw):
    """Out and in again while the player still holds the PCM: the old gadget's stop goes first, then the start, in
    one background run, so the new runner never meets the old gadget."""
    u = auto(fw)
    holding(fw, u)
    u.unplug()
    u.plug(host=False)
    assert u.commands == [CAR_START, CAR_STOP_START]


# ---- nowhere to play once the car's audio goes (usb_car.c usb_car_gone, docs/iphone-mode.md#nowhere-to-play) ----

RELINK = '/tmp/q2car.relink'


class Player:
    """Stock's play status and pause, and has_valid_output_device's answer (either jack, a Bluetooth link with its
    codec up, a USB DAC), for the car's audio going."""

    def __init__(self, fw, u, output):
        self.status, self.pauses, self.output = 2, 0, output
        for name, fn in (('mclGetPlayStatus', lambda *_: self.status), ('has_valid_output_device', lambda *_: self.output),
                         ('player_play_pause', lambda *_: self.pause() or 0),
                         ('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in u.files.files else -1)):
            try:
                fw.mock(name, fn)
            except ValueError:  # a build of usb_car.c that calls none of it (how this test was first seen red)
                pass

    def pause(self):
        self.pauses += 1
        self.status = 3


def car_playing(fw, output=0):
    """A car on the cable taking the Q2's audio, a song playing."""
    u = auto(fw)
    p = Player(fw, u, output)
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    return u, p


def test_the_cable_out_with_nowhere_else_to_play_pauses(fw):
    """2026-11-21: the cable out at 18:03, and the song played on through the DAC, no jack in, for 2 h 09. Now a
    song playing is paused where it is (stock's pause, as at a jack's eject), once."""
    u, p = car_playing(fw, output=0)
    u.unplug()
    assert p.pauses == 1 and p.status == 3
    u.poll()
    assert p.pauses == 1


def test_the_cable_out_with_headphones_in_plays_on(fw):
    """A jack in (has_valid_output_device: g_po_status or g_bal_status): the song goes on there, as today."""
    u, p = car_playing(fw, output=1)
    u.unplug()
    assert p.pauses == 0 and p.status == 2


def test_the_cable_out_with_bluetooth_audio_linked_plays_on(fw):
    """A Bluetooth link with its codec up (has_valid_output_device: bt_linkstatus, bt_showcoding): the song goes on
    there; the player's way is demo's Bluetooth once the car's file is gone (player/engine.c car_way)."""
    u, p = car_playing(fw, output=1)
    u.unplug()
    assert p.pauses == 0


def test_a_song_not_playing_is_left_as_it_is(fw):
    u, p = car_playing(fw, output=0)
    p.status = 3
    u.unplug()
    assert p.pauses == 0


def test_a_car_that_never_took_audio_pauses_nothing_at_the_cable_out(fw):
    """The player was never on the car's way: what plays plays where it did."""
    u = auto(fw)
    p = Player(fw, u, 0)
    state(u, verdict='car', host=1, name='BMW 83828', audio=0, flowing=0, troubles=0)
    u.unplug()
    assert p.pauses == 0


def test_the_car_stopping_its_audio_pauses_with_nowhere_else_to_play(fw):
    """The cable stays, the car stops asking for audio (another source, StopUSBDeviceModeAudio): the same pause."""
    u, p = car_playing(fw, output=0)
    state(u, verdict='car', host=1, name='BMW 83828', audio=0, flowing=0, troubles=0)
    assert p.pauses == 1


def test_a_relink_holds_the_pause_and_the_song_plays_on_into_the_car(fw):
    """q2-iap's exit 22 (a lost link): its note first, then q2car.sh's gadget off and on (the state file gone, then a
    new session's). The song plays on, and the car's audio comes back to it: no pause at any step."""
    u, p = car_playing(fw, output=0)
    u.files.files[RELINK] = b'22\n'
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)  # q2-iap ending
    u.files.files.pop(STATE)  # gadget_up's rm -f $STATE
    u.now += 200
    u.poll()
    state(u, ms=2000, verdict='wait', host=1)
    state(u, ms=2000, verdict='car', host=1, name='BMW 83828', audio=0, flowing=0, troubles=0)
    u.files.files.pop(RELINK)  # q2-iap's io_audio(1): the relink over
    state(u, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    state(u, ms=40000, verdict='car', host=1, name='BMW 83828', audio=1, flowing=1, troubles=0)
    assert p.pauses == 0 and p.status == 2


def test_a_relink_that_ends_without_the_car_s_audio_pauses(fw):
    """The runner gave up (RELINKS reached, its note removed) with the car's audio still gone: the pause then."""
    u, p = car_playing(fw, output=0)
    u.files.files[RELINK] = b'22\n'
    u.files.files.pop(STATE)
    u.now += 200
    u.poll()
    state(u, ms=3000, verdict='wait', host=1)
    assert p.pauses == 0
    u.files.files.pop(RELINK)
    state(u, verdict='wait', host=1)
    assert p.pauses == 1


def test_a_relink_that_never_brings_the_audio_back_pauses_in_30_s(fw):
    u, p = car_playing(fw, output=0)
    u.files.files[RELINK] = b'22\n'
    u.files.files.pop(STATE)
    u.now += 200
    u.poll()
    state(u, ms=20000, verdict='car', host=1, name='BMW 83828', audio=0)
    assert p.pauses == 0
    state(u, ms=10000, verdict='car', host=1, name='BMW 83828', audio=0)
    assert p.pauses == 1



def test_another_source_played_to_the_car_pauses_when_the_car_s_audio_goes(fw):
    """Spotify (or Radio, Internet Radio, a video) plays to the car's feed (videos.c output_device), which cannot
    follow to another output mid-stream: at the cable out it is paused, headphones in or not, and the Library is
    left alone."""
    from test_radio import Sp
    u, p = car_playing(fw, output=1)
    sp = fw.var('spotify__sp', Sp)
    sp.active, sp.playing = 1, 1
    sent = []
    fw.mock('fw_sendto', lambda fd, data, n, *_: sent.append(C.string_at(data, n)) or n)
    u.unplug()
    assert sent and sent[0].startswith(b's'), sent  # librespot told to pause, as spot_yield tells it
    assert p.pauses == 0, "the Library's pause is not asked"


def test_a_cable_in_while_stock_holds_the_link_says_so(fw):
    """The drive of 2026-11-22: the car's first cable showed no iPhone, and nothing said why. A cable that comes while
    stock's link status is not 0 gets no choice at all (usb_ui_status); the log says the status it found."""
    from test_headset import said_on_stderr
    u = Usb(fw, ini={'USBPICK': 0})
    said = said_on_stderr(fw, 'usb')
    u.status()  # a pass with no cable first, so the plug is an edge
    u.link.value = 2
    u.plug(host=False)
    assert u.commands == [] and "cable in with stock's link status 2: no choice made" in said, said
    u.unplug()
    said.clear()
    u.plug(host=False)
    assert u.commands == [CAR_START] and not any('link status' in s for s in said), said
