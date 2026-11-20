"""Car Audio, the USB setting's Automatic (patch/usb_car.c, patch/usb_ui.c, docs/iphone-mode.md#car-audio-in-the-image):
the iPhone shown at a cable in, q2-iap's verdict read from its state file, a car kept and its toasts, any other host
handed to Transfer Files, a charger left to charge, the gadget stopped with the cable or before a picked mode."""
import ctypes as C

from conftest import O  # noqa: F401
from test_coverflow import Ui  # noqa: F401
from test_nav_painters import Canvas, scratch, st  # noqa: F401 (fixtures)
from test_usb_ui import CAR_START, UDC, Usb, picker

STATE = '/tmp/q2car.state'
CAR_STOP_BG = '( /usr/bin/q2car stop >/dev/null 2>&1 & )'
CAR_STOP = '/usr/bin/q2car stop >/dev/null 2>&1'


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
    """Charge Only picked while Automatic's iPhone is on the cable: q2car stopped here and now, then the pick."""
    u = auto(fw)
    u.ui.click(u.row(picker(u), 'Charge Only'))
    assert u.commands == [CAR_START, CAR_STOP] and u.saved == [('Q2POD', 'USBPICK', 1)]
    u.unplug()
    assert CAR_STOP_BG not in u.commands, 'stopped once'
