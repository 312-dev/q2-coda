"""usb_keys.c (docs/internals.md#usb-headset-buttons): a USB-C headset's buttons, read through usbfs from its HID
interface. A fake device answers the worker's sysfs reads, its usbfs descriptors, the claim, the report descriptor's
GET_DESCRIPTOR and the interrupt URB's submit, select and reap, from a script of timed reports; the actions the worker
posts are taken off its ring as it goes, and the UI loop's side (usb_keys_poll) acts on them."""
import ctypes as C
import re

import pytest

from conftest import O, ROOT

SOURCE = (ROOT/'patch/usb_keys.c').read_text()
D = {k: int(v, 0) for k, v in re.findall(r'^#define (UK_\w+) +(0x[0-9a-f]+u?|\d+)', SOURCE, re.M) for v in [v.rstrip('u')]}
CONTROL, SUBMIT, REAP, CLAIM = D['UK_CONTROL'], D['UK_SUBMITURB'], D['UK_REAPURBNDELAY'], D['UK_DISCONNECT_CLAIM']
VOLUP, VOLDOWN, PLAYPAUSE, PLAY, PAUSE, NEXT, PREV, SAVE = (D[f'UK_{k}'] for k in
                                                         'VOLUP VOLDOWN PLAYPAUSE PLAY PAUSE NEXT PREV SAVE'.split())
FIRST, REPEAT, MULTI = D['UK_REPEAT_FIRST_MS'], D['UK_REPEAT_MS'], D['UK_MULTI_MS']
ENODEV, EAGAIN = 19, 11
USBFS = 50  # the fake's usbfs descriptor


class Uk(C.Structure):
    _fields_ = [('generation', C.c_uint), ('head', C.c_uint), ('tail', C.c_uint), ('ring', C.c_int * 16),
                ('tried', C.c_int)]


class Urb(C.Structure):  # struct usbdevfs_urb as this machine lays out the C's own struct
    _fields_ = [('type', C.c_ubyte), ('endpoint', C.c_ubyte), ('status', C.c_int), ('flags', C.c_uint),
                ('buffer', C.c_void_p), ('length', C.c_int), ('actual', C.c_int), ('start', C.c_int),
                ('packets', C.c_int), ('errors', C.c_int), ('signr', C.c_uint), ('context', C.c_void_p)]


class Ctrl(C.Structure):
    _fields_ = [('type', C.c_ubyte), ('request', C.c_ubyte), ('value', C.c_ushort), ('index', C.c_ushort),
                ('length', C.c_ushort), ('timeout', C.c_uint), ('data', C.c_void_p)]


class Claim(C.Structure):
    _fields_ = [('interface', C.c_uint), ('flags', C.c_uint), ('driver', C.c_char * 256)]


def mips_ioc(direction, nr, size):
    """_IOC as MIPS has it: direction 1 none, 2 read, 4 write, 6 both, in bits 31..29."""
    return direction << 29 | size << 16 | ord('U') << 8 | nr


def test_the_ioctl_numbers_are_mips_o32_s():
    # struct usbdevfs_urb is 44 bytes on o32, usbdevfs_ctrltransfer 16, usbdevfs_disconnect_claim 264
    assert (CONTROL, SUBMIT, REAP, CLAIM) == (mips_ioc(6, 0, 16), mips_ioc(2, 10, 44), mips_ioc(4, 13, 4),
                                              mips_ioc(2, 27, 264))


# Report descriptors.

# Android's USB headset and USB-C to 3.5 mm adapter specs (source.android.com, accessories/headset): the four
# functions as 1-bit consumer variables, Play/Pause (A), Volume Up (B), Volume Down (C), Voice Command (D), in one byte
# with no report ID. An active adapter that reads a wired remote reports its presses so.
BITMAP = bytes.fromhex('050c 0901 a101 1500 2501 7501 9504 09cd 09e9 09ea 09cf 8102 9504 8101 c0')
# A 16-bit usage array: two fields, each the usage held (0 none), as many headsets' DSP firmware sends.
ARRAY16 = bytes.fromhex('050c 0901 a101 1500 26ff03 1900 2aff03 7510 9502 8100 c0')
# A keyboard (report ID 1, its eight modifiers and six key codes) and a consumer bitmap (report ID 2: Next, Previous,
# Stop, Play/Pause, Mute, Volume Up, Volume Down, Play) in one descriptor.
COMBINED = bytes.fromhex('0501 0906 a101 8501 0507 19e0 29e7 1500 2501 7501 9508 8102 7508 9506 1500 2565 1900 2965'
                         '8100 c0'
                         '050c 0901 a101 8502 1500 2501 7501 9508 09b5 09b6 09b7 09cd 09e2 09e9 09ea 09b0 8102 c0')
# A headset whose HID is a vendor page's alone (no consumer usage): nothing to read.
VENDOR = bytes.fromhex('0600ff 0901 a101 1500 26ff00 7508 9508 0901 8102 c0')

PRESS = {  # the report holding each key down, per layout; the release is the layout's all-up report
    'bitmap': ({'pp': b'\x01', 'up': b'\x02', 'down': b'\x04'}, b'\x00'),
    'array16': ({'pp': b'\xcd\x00\x00\x00', 'up': b'\xe9\x00\x00\x00', 'down': b'\x00\x00\xea\x00',
                 'next': b'\xb5\x00\x00\x00', 'prev': b'\xb6\x00\x00\x00', 'play': b'\xb0\x00\x00\x00',
                 'pause': b'\xb1\x00\x00\x00'}, b'\x00\x00\x00\x00'),
    'combined': ({'next': b'\x02\x01', 'prev': b'\x02\x02', 'pp': b'\x02\x08', 'up': b'\x02\x20', 'down': b'\x02\x40',
                  'play': b'\x02\x80'}, b'\x02\x00'),
}
LAYOUTS = {'bitmap': BITMAP, 'array16': ARRAY16, 'combined': COMBINED}


def descriptors(hid_len, hid=True, iface=2):
    """usbfs's read: a device descriptor, then a configuration of audio control (0), audio streaming (1, alt 0 and an
    alt 1 with its isochronous OUT endpoint) and, with hid, a HID interface `iface` with its interrupt IN 0x83."""
    body = (bytes([9, 4, 0, 0, 0, 1, 1, 0, 0]) + bytes([9, 4, 1, 0, 0, 1, 2, 0, 0]) + bytes([9, 4, 1, 1, 1, 1, 2, 0, 0])
            + bytes([9, 5, 0x01, 0x05, 0xc0, 0, 1, 0, 0]))
    if hid:
        body += (bytes([9, 4, iface, 0, 1, 3, 0, 0, 0]) + bytes([9, 0x21, 0x11, 0x01, 0, 1, 0x22, hid_len & 0xff,
                                                                    hid_len >> 8]) + bytes([7, 5, 0x83, 3, 8, 0, 4]))
    config = bytes([9, 2, (9 + len(body)) & 0xff, (9 + len(body)) >> 8, 3 if hid else 2, 1, 0, 0x80, 50]) + body
    return bytes([18, 1, 0, 2, 0, 0, 0, 64, 0x0a, 0x12, 0x01, 0x40, 0, 1, 1, 2, 0, 1]) + config


class Device:
    """The headset on the Q2's port, through sysfs and usbfs. `script` is [(ms after the start, report or 'gone')]:
    each report completes the URB in flight at its time; past the script the device is unplugged."""
    def __init__(self, fw, rdesc=BITMAP, script=(), hid=True, driver_busy=False, dev='1-1'):
        self.fw, self.rdesc, self.script, self.hid, self.dev = fw, rdesc, list(script), hid, dev
        self.now = self.start = 50000
        self.posted, self.ioctls, self.claimed, self.urb, self.gone, self.opened = [], [], [], None, False, []
        self.driver_busy = driver_busy
        self.errno = C.c_int(0)
        self.uk = fw.var('usb_keys__uk', Uk)
        ifaces = {f'{dev}:1.0': '01', f'{dev}:1.1': '01', **({f'{dev}:1.2': '03'} if hid else {})}
        self.sys = {f'/sys/bus/usb/devices/{n}/bInterfaceClass': c for n, c in ifaces.items()}
        self.sys |= {f'/sys/bus/usb/devices/{dev}/busnum': '1', f'/sys/bus/usb/devices/{dev}/devnum': '5'}
        self.fds = {}
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw___errno_location', lambda *_: C.addressof(self.errno))
        fw.mock('fw_readlink', self.readlink)
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', self.read)
        fw.mock('fw_ioctl', self.ioctl)
        fw.mock('fw_select', self.select)

    def readlink(self, path, buf, n, *_):
        """card1's device: the USB DAC's audio control interface, as the kernel links it"""
        assert C.string_at(path) == b'/sys/class/sound/card1/device'
        link = f'../../../{self.dev}:1.0'.encode()[:n]
        C.memmove(buf, link, len(link))
        return len(link)

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        self.opened.append(path)
        if path == '/dev/bus/usb/001/005':
            self.fds[USBFS] = path
            return USBFS
        if path in self.sys:
            fd = 100 + len(self.fds)
            self.fds[fd] = path
            return fd
        return -1

    def read(self, fd, buf, n, *_):
        data = descriptors(len(self.rdesc), self.hid) if fd == USBFS else (self.sys[self.fds[fd]] + '\n').encode()
        data = data[:n]
        C.memmove(buf, data, len(data))
        return len(data)

    def ioctl(self, fd, request, arg, *_):
        request &= 0xffffffff
        self.ioctls.append(request)
        if self.gone:
            self.errno.value = ENODEV
            return -1
        if request == CLAIM:
            claim = Claim.from_address(arg)
            self.claimed.append((claim.interface, claim.flags, claim.driver))
            return -1 if self.driver_busy else 0
        if request == CONTROL:
            ctrl = Ctrl.from_address(arg)
            assert (ctrl.type, ctrl.request, ctrl.value, ctrl.index) == (0x81, 6, 0x2200, 2)
            data = self.rdesc[:ctrl.length]
            C.memmove(ctrl.data, data, len(data))
            return len(data)
        if request == SUBMIT:
            self.urb = Urb.from_address(arg)
            assert (self.urb.type, self.urb.endpoint) == (1, 0x83)
            return 0
        if request == REAP:
            if not self.ready:
                self.errno.value = EAGAIN
                return -1
            report, self.ready = self.ready, None
            C.memmove(self.urb.buffer, report, len(report))
            self.urb.status, self.urb.actual = 0, len(report)
            C.c_void_p.from_address(arg).value = C.addressof(self.urb)
            return 0
        raise AssertionError(f'ioctl {request:#x}')

    ready = None

    def drain(self):
        while self.uk.tail != self.uk.head:
            self.posted.append((self.now - self.start, self.uk.ring[self.uk.tail % 16]))
            self.uk.tail += 1

    def select(self, nfds, r, w, e, tv, *_):
        self.drain()
        timeout = None if not tv else C.c_long.from_address(tv).value * 1000 + C.c_long.from_address(tv + 8).value // 1000
        due = self.start + self.script[0][0] if self.script else None
        if due is not None and (timeout is None or due <= self.now + timeout):
            self.now = max(self.now, due)
            event = self.script.pop(0)[1]
            if event == 'gone':
                self.gone = True
            else:
                self.ready = event
            return 1
        if timeout is None:  # nothing more: the device leaves, as an unplug wakes a select
            self.gone = True
            return 1
        self.now += timeout
        return 0

    def run(self, generation=7):
        self.uk.generation = generation
        self.fw.fn('usb_keys__uk_worker', C.c_void_p, C.c_void_p)(generation)
        self.drain()
        return self.posted


def press(layout, key, at, hold=40):
    keys, up = PRESS[layout]
    return [(at, keys[key]), (at + hold, up)]


@pytest.mark.parametrize('layout', ['bitmap', 'array16', 'combined'])
def test_each_layout_s_volume_keys_step_and_save_on_release(fw, layout):
    dev = Device(fw, LAYOUTS[layout], press(layout, 'up', 100) + press(layout, 'down', 300))
    assert dev.run() == [(100, VOLUP), (140, SAVE), (300, VOLDOWN), (340, SAVE)]


@pytest.mark.parametrize('layout', ['array16', 'combined'])
@pytest.mark.parametrize('key, code', [('next', NEXT), ('prev', PREV), ('play', PLAY)])
def test_media_keys_the_headset_sends(fw, layout, key, code):
    if key not in PRESS[layout][0]:
        pytest.skip('not in this layout')
    assert Device(fw, LAYOUTS[layout], press(layout, key, 100)).run() == [(100, code)]


def test_pause_in_a_usage_array(fw):
    assert Device(fw, ARRAY16, press('array16', 'pause', 100)).run() == [(100, PAUSE)]


@pytest.mark.parametrize('layout', ['bitmap', 'array16', 'combined'])
@pytest.mark.parametrize('presses, code', [(1, PLAYPAUSE), (2, NEXT), (3, PREV)])
def test_play_pause_once_twice_three_times(fw, layout, presses, code):
    """Play/Pause acts UK_MULTI_MS after its last release; twice is Next; three times is Previous, at the third
    release."""
    script = sum((press(layout, 'pp', 100 + 200 * i, hold=60) for i in range(presses)), [])
    last_release = 100 + 200 * (presses - 1) + 60
    at = last_release if presses == 3 else last_release + MULTI
    assert Device(fw, LAYOUTS[layout], script).run() == [(at, code)]


def test_presses_further_apart_are_separate(fw):
    script = press('bitmap', 'pp', 100) + press('bitmap', 'pp', 100 + 40 + MULTI + 10)
    assert [c for _, c in Device(fw, BITMAP, script).run()] == [PLAYPAUSE, PLAYPAUSE]


def test_a_held_volume_key_repeats_at_the_q2_s_key_rate(fw):
    """A step at the press, then after UK_REPEAT_FIRST_MS one every UK_REPEAT_MS until the release."""
    dev = Device(fw, BITMAP, press('bitmap', 'up', 100, hold=1000))
    posted = dev.run()
    steps = [t for t, c in posted if c == VOLUP]
    assert steps == [100, *range(100 + FIRST, 1100, REPEAT)] and posted[-1] == (1100, SAVE)


def test_unplugged_mid_press_stops_and_saves(fw):
    dev = Device(fw, BITMAP, [(100, b'\x04'), (500, 'gone'), (2000, b'\x00')])
    posted = dev.run()
    assert [t for t, c in posted if c == VOLDOWN] == [100, *range(100 + FIRST, 500, REPEAT)]
    assert posted[-1] == (500, SAVE) and dev.script  # it left at the unplug: the later report never read


def test_a_headset_behind_a_hub(fw):
    """The DAC's device named by its port path through a hub, "1-1.3": its HID interface is found the same way."""
    dev = Device(fw, BITMAP, press('bitmap', 'up', 100), dev='1-1.3')
    assert dev.run() == [(100, VOLUP), (140, SAVE)] and '/dev/bus/usb/001/005' in dev.opened


def test_a_report_id_s_keys_are_its_own(fw):
    """The keyboard's report (ID 1) changes no consumer key, whatever its bytes: Volume Up stays held through it."""
    script = [(100, b'\x02\x20'), (150, b'\x01\x00\x00\xe9\xea\xcd\x00\x00\x00'), (300, b'\x02\x00')]
    assert Device(fw, COMBINED, script).run() == [(100, VOLUP), (300, SAVE)]


def test_a_headset_without_hid_is_left_alone(fw):
    dev = Device(fw, BITMAP, press('bitmap', 'up', 100), hid=False)
    assert dev.run() == [] and '/dev/bus/usb/001/005' not in dev.opened and dev.ioctls == []


def test_a_hid_with_no_media_keys_is_let_go(fw):
    dev = Device(fw, VENDOR, press('bitmap', 'up', 100))
    assert dev.run() == [] and SUBMIT not in dev.ioctls


def test_only_the_hid_interface_is_claimed_and_only_from_usbhid(fw):
    """Audio interfaces 0 and 1 are never claimed nor set; the HID interface is taken from usbhid (flag 1,
    USBDEVFS_DISCONNECT_CLAIM_IF_DRIVER), so another driver bound to it keeps it."""
    dev = Device(fw, BITMAP, press('bitmap', 'up', 100))
    dev.run()
    assert dev.claimed == [(2, 1, b'usbhid')]
    assert set(dev.ioctls) <= {CLAIM, CONTROL, SUBMIT, REAP}


def test_a_claim_refused_reads_nothing(fw):
    dev = Device(fw, BITMAP, press('bitmap', 'up', 100), driver_busy=True)
    assert dev.run() == [] and set(dev.ioctls) == {CLAIM}


def test_a_newer_attach_s_worker_takes_over(fw):
    """A worker whose generation is no longer the current one posts nothing and ends."""
    dev = Device(fw, BITMAP, press('bitmap', 'up', 100))
    dev.uk.generation = 8
    fw.fn('usb_keys__uk_worker', C.c_void_p, C.c_void_p)(7)
    dev.drain()
    assert dev.posted == []


# The UI loop's side.

def poll(fw):
    fw.fn('usb_keys_poll', None)()


def threads(fw):
    started = []
    def create(t, attr, fn, arg, *_):
        started.append(arg)
        C.c_ulong.from_address(t).value = 0x2a
        return 0
    fw.mock('fw_pthread_create', create)
    return started


def test_nothing_runs_without_a_usb_dac(fw):
    started = threads(fw)
    fw.var('g_otg_status', C.c_ubyte).value = 0
    for _ in range(3):
        poll(fw)
    assert started == [] and fw.calls('fw_readlink') == [] and fw.calls('fw_open') == []


def test_one_worker_per_attach(fw):
    started, status = threads(fw), fw.var('g_otg_status', C.c_ubyte)
    status.value = 2
    poll(fw), poll(fw)
    status.value = 0
    poll(fw)
    status.value = 2
    poll(fw)
    uk = fw.var('usb_keys__uk', Uk)
    assert started == [1, 2] and uk.generation == 2


def post(fw, *codes):
    uk = fw.var('usb_keys__uk', Uk)
    for code in codes:
        uk.ring[uk.head % 16] = code
        uk.head += 1
    fw.var('g_otg_status', C.c_ubyte).value = 2
    threads(fw)
    poll(fw)


@pytest.mark.parametrize('lit', [0, 1])
def test_volume_steps_play_at_once_and_save_on_release(fw, lit):
    """Lit or dark alike: the screen is not lit by a headset's key."""
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    fw.var('g_maxvolume', C.c_ubyte).value = 100
    fw.var('g_volume', C.c_ubyte).value = 40
    post(fw, VOLUP, VOLUP, VOLDOWN, VOLUP, SAVE)
    assert fw.var('g_volume', C.c_ubyte).value == 42
    assert [a[:2] for _, a in fw.calls('device_set_volume')] == [(41, 1), (42, 1), (41, 1), (42, 1)]
    saves = fw.calls('write_int_config')
    assert len(saves) == 1 and saves[0][1][0] == 42 and C.string_at(saves[0][1][2]) == b'VOLUME'
    assert fw.calls('awake_screen') == []


def test_volume_stops_at_max_volume_and_zero(fw):
    fw.var('g_maxvolume', C.c_ubyte).value = 60
    volume = fw.var('g_volume', C.c_ubyte)
    volume.value = 59
    post(fw, VOLUP, VOLUP, VOLUP)
    assert volume.value == 60 and len(fw.calls('device_set_volume')) == 1
    volume.value = 1
    post(fw, VOLDOWN, VOLDOWN)
    assert volume.value == 0


def test_a_volume_over_the_limit_is_not_pulled_down_by_volume_up(fw):
    fw.var('g_maxvolume', C.c_ubyte).value = 50
    volume = fw.var('g_volume', C.c_ubyte)
    volume.value = 70
    post(fw, VOLUP)
    assert volume.value == 70 and fw.calls('device_set_volume') == []
    post(fw, VOLDOWN)
    assert volume.value == 69  # a step down from there, as the wheel's (vol_next)


class Player:
    def __init__(self, fw, status=2):
        self.status = status
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_play_pause', lambda *_: self.toggle())

    def toggle(self):
        self.status = 3 if self.status == 2 else 2


@pytest.mark.parametrize('code, stock', [(NEXT, 'player_next_music'), (PREV, 'player_prev_music')])
def test_next_and_previous_skip_as_the_q2_s_buttons(fw, code, stock):
    Player(fw)
    post(fw, code)
    assert len(fw.calls(stock)) == 1


@pytest.mark.parametrize('status, code, toggles', [(2, PLAYPAUSE, 1), (3, PLAYPAUSE, 1), (2, PLAY, 0), (3, PLAY, 1),
                                                   (2, PAUSE, 1), (3, PAUSE, 0)])
def test_play_pause_play_and_pause(fw, status, code, toggles):
    player = Player(fw, status)
    post(fw, code)
    assert len(fw.calls('player_play_pause')) == toggles and (player.status != status) == bool(toggles)


def test_a_variable_field_past_its_usages_takes_the_last(fw):
    """Three 1-bit fields, two usages (Play/Pause, Volume Up): the third field is Volume Up too, as the HID parser
    gives the last usage to the fields after it."""
    rdesc = bytes.fromhex('050c 0901 a101 1500 2501 7501 9503 09cd 09e9 8102 9505 8101 c0')
    assert Device(fw, rdesc, [(100, b'\x04'), (140, b'\x00')]).run() == [(100, VOLUP), (140, SAVE)]
