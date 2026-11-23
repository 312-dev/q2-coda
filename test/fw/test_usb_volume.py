"""A USB DAC's volume (usb_volume.c, docs/player.md#usb-volume): ringnav_usb_setvol over a fake ALSA control.

Stock's mclUsbAudioSetVol (0x5bec64, V1.36), as disassembled: for each numid its finder recorded (USB_MIXER of them at
0xa50030), x = trunc(range * (0.4 + 0.6 * v / 100)) for range = max - min of 255 or more (single-precision v / 100,
the rest in double), else trunc(range * (0.2 + 0.8 * v / 100)); x = min for v 0; then clamped to [min, max]. The
double below computes that and writes the result, as stock's snd_ctl_elem_write would."""
import ctypes as C
import struct

import pytest

USB_MIXER, NUMIDS = 0xa50044, 0xa50030
ELEM_INFO, TLV_READ = 0xc1105511, 0xc008551a
DB_MINMAX, DB_SCALE = 4, 1


def stock_law(v, lo, hi):
    """mclUsbAudioSetVol's value for volume v on a control lo..hi."""
    rng = hi - lo
    f = lambda x: struct.unpack('f', struct.pack('f', x))[0]
    if v == 0:
        x = 0
    elif rng >= 255:
        x = int(f(rng) * 0.4 + f(v / 100) * (f(rng) * 0.6))
    else:
        x = int(f(rng) * 0.2 + f(v / 100) * (f(rng) * 0.8))
    return max(lo, min(hi, x))


class Dac:
    def __init__(self, fw, lo=0, hi=11520, tlv=(DB_MINMAX, 8, -4500, 0), count=1, kind=2):
        self.fw, self.lo, self.hi, self.tlv, self.kind = fw, lo, hi, tlv, kind
        self.written, self.stock = [], []
        C.c_int.from_address(USB_MIXER).value = count
        C.c_int.from_address(NUMIDS).value = 7
        fw.mock('fw_open', lambda path, *_: 5 if C.string_at(path) == b'/dev/snd/controlC1' else -1)
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('fw_ioctl', self.ioctl)
        fw.mock('stock_usb_setvol_trampoline', self.stock_pass)
        self.set = fw.fn('ringnav_usb_setvol', C.c_int, C.c_int, C.c_int, C.c_char_p)

    def ioctl(self, fd, req, arg, *_):
        req &= 0xffffffff
        if req == ELEM_INFO:
            assert C.c_uint.from_address(arg).value == 7
            C.c_int.from_address(arg + 64).value = self.kind
            C.c_int.from_address(arg + 80).value, C.c_int.from_address(arg + 84).value = self.lo, self.hi
            return 0
        if req == TLV_READ:
            if not self.tlv:
                return -1
            assert C.c_uint.from_address(arg).value == 7 and C.c_uint.from_address(arg + 4).value >= 4 * len(self.tlv)
            for i, w in enumerate(self.tlv):
                C.c_int.from_address(arg + 8 + 4 * i).value = w
            return 0
        return -1

    def stock_pass(self, v, right, card, *_):
        v, right = C.c_int(v).value, C.c_int(right).value  # ints in 64-bit registers
        self.stock.append((v, right, C.string_at(card).decode()))
        if C.c_int.from_address(USB_MIXER).value == -1:  # the finder, on the first call
            C.c_int.from_address(USB_MIXER).value = 1
        self.written.append(stock_law(v, self.lo, self.hi))
        return 0

    def level(self, v):
        self.written.clear()
        self.set(v, v, b'hw:1')
        return self.written[-1]


# Stock's law moves 0.6 % of the range a volume step, and its 0 is the minimum, so a level is one such step out at worst
# (0.27 dB on the AB13X's 45 dB).
STEP = 0.006


def db(raw, lo=0, hi=11520, dblo=-45.0, dbhi=0.0):
    return dblo + (raw - lo) * (dbhi - dblo) / (hi - lo)


def test_stock_s_law_on_the_ab13x_puts_volume_1_at_minus_26_7_db():
    assert round(db(stock_law(1, 0, 11520)), 1) == -26.7
    assert stock_law(100, 0, 11520) == 11520 and stock_law(0, 0, 11520) == 0


def test_the_ab13x_s_volume_is_spread_in_db_from_its_minimum_to_its_maximum(fw):
    d = Dac(fw)
    assert d.level(100) == 11520
    assert d.level(0) == 0
    assert abs(db(d.level(1)) - -45.0) <= STEP * 45, db(d.level(1))  # max(-45 dB, -60 dB)
    for v in (10, 25, 50, 75, 90):
        want = -45.0 * (100 - v) / 99
        assert abs(db(d.level(v)) - want) <= STEP * 45, (v, db(d.level(v)), want)
    levels = [d.level(v) for v in range(101)]
    assert levels == sorted(levels), 'the volume never steps down as it goes up'


def test_a_control_reaching_below_minus_60_db_starts_at_minus_60(fw):
    d = Dac(fw, lo=0, hi=25600, tlv=(DB_MINMAX, 8, -10000, 0))
    assert abs(db(d.level(1), 0, 25600, -100.0) - -60.0) <= STEP * 100
    assert d.level(100) == 25600


def test_a_db_scale_tlv_in_a_container_is_read(fw):
    d = Dac(fw, lo=0, hi=300, tlv=(0, 16, DB_SCALE, 8, -6000, 20))  # -60 dB in 0.2 dB steps: 0..300 is -60..0 dB
    assert d.level(100) == 300 and abs(d.level(50) - 300 * (1 - 50 / 99)) <= STEP * 300


@pytest.mark.parametrize('case', ['no dB', 'small range', 'no control', 'not integer'])
def test_otherwise_stock_s_law_is_kept_exactly(fw, case):
    d = Dac(fw, tlv=None if case == 'no dB' else (DB_MINMAX, 8, -4500, 0), hi=100 if case == 'small range' else 11520,
            count=-2 if case == 'no control' else 1, kind=3 if case == 'not integer' else 2)
    for v in (0, 1, 37, 100):
        d.stock.clear()
        d.set(v, v, b'hw:1')
        assert d.stock == [(v, v, 'hw:1')], (case, v, d.stock)


def test_the_first_set_at_attach_runs_stock_s_finder_then_sets_the_db_level(fw):
    """device_set_volume at the DAC's discovery, before its first song: stock's call with USB_MIXER -1 finds the
    control (and writes its law's level), then the level is the dB law's in the same call."""
    d = Dac(fw, count=-1)
    d.set(1, 1, b'hw:1')
    assert len(d.stock) == 2 and d.stock[0] == (1, 1, 'hw:1')
    assert abs(db(d.written[-1]) - -45.0) <= STEP * 45
