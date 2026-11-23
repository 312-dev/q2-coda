"""Standby (patch/system.c, docs/internals.md#standby-limit): held while a host has the USB link, where a suspend left the link dead after the wake (a Q2 on a
laptop, 2026-11-22: the gadget still configured on the Q2, the computer never reached it again until the cable was
handled)."""
import ctypes as C

import pytest

from conftest import Files, O

UDC = '/sys/class/udc/13500000.otg/state'


# ---- held while a host has the USB link (standby_host_poll) ----

class Cable:
    def __init__(self, fw, cable=1, udc='configured\n'):
        self.fw, self.now = fw, 100000
        fw.var('g_usbdet_value', C.c_ubyte).value = cable
        self.files = Files(fw, {UDC: udc.encode()} if udc else {})
        fw.mock('time_now_ms', lambda *_: self.now)

    def holds(self, passes=5, step=300):
        """reset_poweroptions_timer(1, 0, 0) calls over passes UI passes step ms apart."""
        before = len(self.fw.calls('reset_poweroptions_timer'))
        for _ in range(passes):
            self.now += step
            self.fw.fn('system__standby_host_poll', None)()
        return [a[:3] for _, a in self.fw.calls('reset_poweroptions_timer')[before:]]


def test_a_computer_on_the_cable_holds_standby_once_a_second(fw):
    """Five passes 300 ms apart: 1.5 s, so the countdown starts again once (the first look) and once more."""
    assert Cable(fw).holds() == [(1, 0, 0), (1, 0, 0)]


@pytest.mark.parametrize('cable, udc', [(1, 'not attached\n'), (1, 'default\n'), (1, None), (0, 'configured\n')])
def test_a_charger_or_no_cable_lets_standby_count(fw, cable, udc):
    """A charger configures nothing; with no cable the controller is not read at all."""
    c = Cable(fw, cable, udc)
    assert c.holds() == []
    if not cable:
        assert not fw.calls('fw_fopen')


# ---- Standby and the USB link (patch/standby_usb.c, docs/internals.md#standby-and-the-usb-link) ----
#
# 2026-11-22: after a forced Standby the laptop never reached the Q2 again (the gadget kept "configured" through the
# suspend), and when the pull-up was dropped and given back by hand right after the resume the host enumerated 0.25 s
# before the card mounted (adbd's key on the card unread: unauthorized). With Car Audio up, q2-iap's log on the card
# stays open through stock's umount.

SOFT = '/sys/class/udc/13500000.otg/soft_connect'
ROLE = '/sys/devices/platform/ahb2/13500000.otg/id_status'
PCM = '/proc/asound/UAC1Gadget/pcm0p/sub0/status'
MOUNTS = '/proc/mounts'
CARD = b'/dev/mmcblk4p1 /mnt/mmc exfat rw,relatime,fmask=0000 0 0\n'
CAR_START = '/usr/bin/q2car start >/dev/null 2>&1'
CAR_STOP = '/usr/bin/q2car stop >/dev/null 2>&1'


class Sleep:
    """A Q2 going into stock's Standby and back: the commands run, demo's sends to the player (a player that lets go
    of the car's PCM at the release, or never), stock's device_standby as a double that unmounts the card, calls
    toolsIntoStandby (ringnav_standby, its suspend a double too) and returns with the card not yet back."""

    def __init__(self, fw, car=True, udc='suspended\n', releases=True, cable=1):
        self.fw, self.now, self.commands, self.sent, self.order = fw, 100000, [], [], []
        fw.var('g_usbdet_value', C.c_ubyte).value = cable
        self.files = Files(fw, {UDC: udc.encode(), SOFT: b'', ROLE: b'device\n', MOUNTS: CARD})
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_usleep', lambda us, *_: setattr(self, 'now', self.now + max(1, us // 1000)) or 0)
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or 0)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files.files else -1)
        C.c_int.from_address(O['MCL_FD']).value = 7

        def send(fd, data, n, *_):
            self.sent.append(C.string_at(data, n).decode())
            if releases:
                self.files.files[PCM] = b'closed\n'
            return n
        fw.mock('fw_send', send)
        fw.mock('stock_device_standby_trampoline', self.stock_standby)
        fw.mock('stock_standby_trampoline', self.suspend)
        if car:
            fw.fn('usb_car_start', None)()
            self.files.files[PCM] = b'state: PAUSED\n'  # a song paused into the car holds it
            self.commands.clear()

    def stock_standby(self, *_):
        self.order.append(('stock', list(self.commands), list(self.sent)))
        self.files.files[MOUNTS] = b''  # stock's umount
        self.fw.fn('ringnav_standby', C.c_int)()
        return 1

    def suspend(self, *_):
        self.order.append(('suspend', self.files.files[SOFT]))
        self.now += 60000
        return 1

    def standby(self):
        assert self.fw.fn('ringnav_device_standby', C.c_int)() == 1

    def poll(self, ms=100):
        self.now += ms
        self.fw.fn('standby_usb_poll', None)()


def test_car_audio_goes_before_stock_s_standby_and_the_link_comes_back_after_the_card(fw):
    """Car Audio up on a bus the car has suspended (parked: Standby's host hold, which looks for configured, lets it
    count): the player's release and q2car stop both before stock's device_standby; the pull-up let go only for the
    suspend; after the wake nothing until the card is mounted read-write, then the pull-up back and Car Audio again."""
    s = Sleep(fw)
    s.standby()
    stock = s.order[0]
    assert stock == ('stock', [CAR_STOP], ['{mcl-carrelease\\null}']), 'released and stopped before stock unmounts'
    assert s.order[1] == ('suspend', b'disconnect')
    assert s.files.files[SOFT] == b'disconnect', 'not in the call that suspends'
    s.poll()
    s.poll(1500)
    assert s.files.files[SOFT] == b'disconnect' and CAR_START not in s.commands, 'the card not back yet'
    s.files.files[MOUNTS] = CARD
    s.poll()
    assert s.files.files[SOFT] == b'connect'
    assert s.commands[-1] == CAR_START and s.commands.count(CAR_START) == 1
    s.poll(5000)
    s.poll(5000)
    assert s.commands.count(CAR_START) == 1 and s.files.files[SOFT] == b'connect'


def test_a_card_not_back_within_3_s_reconnects_then(fw):
    s = Sleep(fw, car=False)
    s.standby()
    s.poll(2900)
    assert s.files.files[SOFT] == b'disconnect'
    s.poll(200)
    assert s.files.files[SOFT] == b'connect' and not [c for c in s.commands if 'q2car' in c]


def test_a_q2_with_no_card_before_the_standby_reconnects_at_once(fw):
    """No card mounted as the Standby began: nothing to wait for, the pull-up back in the first pass after the wake."""
    s = Sleep(fw, car=False)
    s.files.files[MOUNTS] = b'rootfs / rootfs rw 0 0\n'
    s.standby()
    s.poll()
    assert s.files.files[SOFT] == b'connect'


def test_a_card_mounted_read_only_is_not_the_card_back(fw):
    """exFAT's errors=remount-ro: a card that came back read-only is waited past as none, the 3 s."""
    s = Sleep(fw, car=False)
    s.standby()
    s.files.files[MOUNTS] = CARD.replace(b' rw,', b' ro,')
    s.poll(1000)
    assert s.files.files[SOFT] == b'disconnect'
    s.poll(2100)
    assert s.files.files[SOFT] == b'connect'


def test_the_cable_out_through_the_sleep_starts_no_car_audio(fw):
    s = Sleep(fw)
    s.standby()
    fw.var('g_usbdet_value', C.c_ubyte).value = 0
    s.files.files[MOUNTS] = CARD
    s.poll()
    assert s.files.files[SOFT] == b'connect' and CAR_START not in s.commands


def test_a_player_that_never_lets_go_still_gets_standby_after_2_s(fw):
    """The release asked and not seen: 2 s of 50 ms looks, then q2car stop (its gadget_down asks again), then stock."""
    s = Sleep(fw, releases=False)
    before = s.now
    fw.mock('stock_device_standby_trampoline', lambda *_: s.order.append(('stock', s.now - before, list(s.commands))) or 1)
    s.standby()
    took, commands = s.order[0][1], s.order[0][2]
    assert commands == [CAR_STOP] and 2000 <= took < 2200, (took, commands)


def test_without_car_audio_nothing_is_stopped(fw):
    s = Sleep(fw, car=False, udc='configured\n')
    s.standby()
    assert s.order[0] == ('stock', [], [])


def test_a_role_left_at_host_is_held_at_device_and_connected_again(fw):
    """A wake that brought the controller back as host (2026-11-17): logged, held at device (usb_otg.c otg_hold's
    devmem) and the pull-up given again."""
    s = Sleep(fw, car=False)
    s.standby()
    s.files.files[ROLE] = b'host\n'
    s.files.files[MOUNTS] = CARD
    s.poll()
    assert any('devmem 0x10000040' in c for c in s.commands), s.commands
    assert s.files.files[SOFT] == b'connect'
