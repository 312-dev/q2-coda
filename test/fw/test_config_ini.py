"""config.ini's sections each start a line (patch/config_ini.c, docs/internals.md#config-ini): toolsWriteConfig's hook puts
the file right before stock's own write, here a model of it that does what V1.36's did in the emulator."""
import ctypes as C

import pytest

from conftest import Files
from test_nav_library_pages import scratch, st  # noqa: F401  navigation.c's state by field

INI = '/mnt/data/config.ini'
# The config.ini a reset copies in, cut short: stock's ends on this line with no newline.
RESET = b'[PLAYSET]\nVOLUME=40\n[SYSSET]\nWHEELMODE=0\nNAVHIDE=0'


def sections(data):
    """[(header, [(key, value)])], as toolsReadConfig splits the lines: a section only at a line's start."""
    out = [(None, [])]
    for line in data.decode().split('\n'):
        if line.startswith('['):
            out.append((line, []))
        elif '=' in line:
            out[-1][1].append(tuple(line.split('=', 1)))
    return out


def read(data, section, key):
    """Stock's reader: the key's first line in any section of that name, else None."""
    for header, keys in sections(data):
        for k, v in keys:
            if header and header.lower() == f'[{section}]'.lower() and k.lower() == key.lower():
                return v
    return None


def stock_write(data, section, key, value):
    """V1.36's toolsWriteConfig as the emulator showed it: the key's first line in a section of that name rewritten,
    else the key added at the end of that section's first copy, else "[%s]\\n%s=%s\\n" appended to the file as it is."""
    lines, header, first, end = data.split(b'\n'), False, None, None
    for i, line in enumerate(lines):
        if line.startswith(b'['):
            if first is not None and end is None:
                end = i
            header = line.lower() == b'[%s]' % section.lower()
            first = i if header and first is None else first
        elif header and line.split(b'=', 1)[0].lower() == key.lower():
            lines[i] = b'%s=%s' % (key, value)
            return b'\n'.join(lines)
    if first is not None:
        end = end if end is not None else len(lines) - (lines[-1] == b'')
        return b'\n'.join(lines[:end] + [b'%s=%s' % (key, value)] + lines[end:])
    return data + b'[%s]\n%s=%s\n' % (section, key, value)


class Config:
    def __init__(self, fw, data):
        self.files = Files(fw, {INI: data})
        self.write = fw.fn('ringnav_ini_write', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p)
        fw.mock('stock_ini_write_trampoline', self.stock)
        self.renames = 0
        rename = self.files.rename

        def counted(*args):
            self.renames += 1
            return rename(*args)
        fw.mock('fw_rename', counted)

    def stock(self, path, section, key, value, *_):
        path = C.string_at(path).decode()
        self.files.files[path] = stock_write(self.files.files[path], C.string_at(section), C.string_at(key),
                                             C.string_at(value))
        return 0

    def __call__(self, section, key, value):
        return self.write(INI.encode(), section.encode(), key.encode(), value.encode())

    @property
    def data(self):
        return self.files.files[INI]


def test_a_section_added_after_a_reset_starts_its_own_line(fw):
    """Boot after a reset: usb_charge.c writes DACCHARGEOWN, then synclink.c SYNCLINKOWN. Unhealed, the first ran into
    NAVHIDE and the second found no [Q2POD], appended another, and the next boot wrote DACCHARGEOWN again."""
    q = Config(fw, RESET)
    q('Q2POD', 'DACCHARGEOWN', '1')
    q('Q2POD', 'SYNCLINKOWN', '1')
    assert q.data == RESET + b'\n[Q2POD]\nDACCHARGEOWN=1\nSYNCLINKOWN=1\n'
    assert read(q.data, 'Q2POD', 'DACCHARGEOWN') == '1' and read(q.data, 'SYSSET', 'NAVHIDE') == '0'


def test_a_run_on_file_is_healed_at_the_next_write(fw):
    """What V1.36 left on every emulator copy: the stray first copy's DACCHARGEOWN, which no read ever reached, goes, as
    the second copy's is the line stock reads and writes; the file then holds each key once and reads as before."""
    broken = (RESET + b'[Q2POD]\nDACCHARGEOWN=1\n[Q2POD]\nLIBRARYSOURCE=1\nDACCHARGEOWN=1\nSYNCLINKOWN=1\n'
              b'[IPOD]\nTHEME=3\n')
    q = Config(fw, broken)
    q('IPOD', 'THEME', '4')
    assert q.data == (RESET + b'\n[Q2POD]\n[Q2POD]\nLIBRARYSOURCE=1\nDACCHARGEOWN=1\nSYNCLINKOWN=1\n'
                      b'[IPOD]\nTHEME=4\n')
    assert [read(q.data, 'Q2POD', k) for k in ('LIBRARYSOURCE', 'DACCHARGEOWN', 'SYNCLINKOWN')] == ['1', '1', '1']


def test_healing_keeps_the_value_stock_has_been_reading(fw):
    """A key in both copies keeps the later copy's value, the one in use (an older value surfacing would undo a choice);
    a key only the run-on copy holds comes back."""
    q = Config(fw, RESET + b'[IPOD]\nTHEME=1\nROUNDED=1\n[IPOD]\nTHEME=3\n')
    assert read(q.data, 'IPOD', 'ROUNDED') is None
    q('IPOD', 'THEME', '5')
    assert q.data == RESET + b'\n[IPOD]\nROUNDED=1\n[IPOD]\nTHEME=5\n'
    assert (read(q.data, 'IPOD', 'THEME'), read(q.data, 'IPOD', 'ROUNDED')) == ('5', '1')


def test_a_sound_file_is_left_alone(fw):
    """Nothing to heal, nothing rewritten: a value that merely ends in brackets is a value."""
    good = b'[SYSSET]\nNAVHIDE=0\n[Q2POD]\nFOLDERROOT=Live [2019]\nSTREAMHIDE=[Q2POD]x\n'
    q = Config(fw, good)
    q('Q2POD', 'MONO', '1')
    assert q.renames == 0 and q.data == good.replace(b'x\n', b'x\nMONO=1\n')


# Settled writes (docs/internals.md#where-q2-muse-writes): a key the wheel steps is written once it settles.
class Settle:
    def __init__(self, fw, way=0):
        self.fw, self.ms, self.saved = fw, 1000, []
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('mclGetOutputWay', lambda *_: way)
        fw.mock('stock_write_config_trampoline', lambda v, s, k, *_: self.saved.append(
            (C.c_int(v).value, C.string_at(s).decode(), C.string_at(k).decode())) and 0)
        self.write = fw.fn('ringnav_write_config', C.c_int, C.c_int, C.c_char_p, C.c_char_p)
        self.settle = fw.fn('config_settle', None, C.c_int)

    def step(self, value, section=b'PLAYSET', key=b'VOLUME', ms=100):
        self.ms += ms
        self.write(value, section, key)
        self.settle(0)  # power_poll's pass after it


def test_a_turn_of_volume_steps_is_one_write_once_it_settles(fw):
    q2 = Settle(fw)
    for v in range(40, 50):
        q2.step(v)
    assert q2.saved == []  # ten steps 100 ms apart: nothing on the flash yet
    q2.ms += 2999
    q2.settle(0)
    assert q2.saved == []
    q2.ms += 1
    q2.settle(0)
    assert q2.saved == [(49, 'PLAYSET', 'VOLUME')]  # the last step's value, once
    q2.ms += 10000
    q2.settle(0)
    assert q2.saved == [(49, 'PLAYSET', 'VOLUME')]


def test_bluetooth_holds_its_own_key_and_a_toggle_is_written_at_once(fw):
    q2 = Settle(fw, way=1)
    q2.step(30)
    q2.step(1, b'SYSSET', b'CARMODE')  # a setting's one write per change is not held
    assert q2.saved == [(1, 'SYSSET', 'CARMODE')]
    q2.settle(1)  # a clean end, Standby, or a read of it
    assert q2.saved[1:] == [(30, 'PLAYSET', 'BT_VOLUME')]


def test_a_read_through_config_value_sees_the_held_value(fw):
    q2 = Settle(fw)
    files = {INI: b'[PLAYSET]\nVOLUME=10\n'}
    fw.mock('toolsReadConfig', lambda path, s, k, out, d, *_: C.memmove(out, read(files[INI], C.string_at(s).decode(), C.string_at(k).decode()).encode() + b'\0', 3) and 0)
    fw.mock('stock_write_config_trampoline', lambda v, s, k, *_: files.__setitem__(INI, stock_write(
        files[INI], C.string_at(s), C.string_at(k), b'%d' % C.c_int(v).value)) or 0)
    q2.step(55)
    assert fw.fn('config_value', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(b'PLAYSET', b'VOLUME', 101, 0) == 55


# A Shuffle or Repeat tap on Now Playing (np_bar.c np_mode_set): the mode takes effect at once, and its three keys
# (stock's PLAYSET PLAYMODE through config_playmode, and np_bar.c's IPOD NPSHUFONE and NPREPOFF) are held by
# write_int_config's hook, so the tap's own pass rewrites no file. Each is written MODE_SETTLE_MS later, one a pass;
# a power-off inside that window writes them at once, and a restart reads the mode back. In the emulator each of
# the two writes in the tap's pass was 60-181 ms of it (docs/inefficiencies.md).
MODE_SETTLE_MS = 1000
# Shuffle on with Repeat Off: np_bar.c's pair (Repeat Off, kept as NPREPOFF 1) and stock's mode
TAPPED = [(1, 'IPOD', 'NPREPOFF'), (1, 'IPOD', 'NPSHUFONE'), (2, 'PLAYSET', 'PLAYMODE')]


class Tap(Settle):
    """np_mode_set over the device's wiring: write_int_config is the hook (ringnav_write_config), stock's
    config_playmode sets the mode word and writes PLAYMODE through it; the file is config.ini as stock writes it,
    and every file opened for writing, renamed or synced is counted."""
    def __init__(self, fw, mcl, st):
        super().__init__(fw)
        self.mcl, self.st, self.file, self.touched = mcl, st, b'[PLAYSET]\nPLAYMODE=0\n[IPOD]\nNPSHUFONE=0\n', []
        hook = fw.fn('ringnav_write_config', C.c_int, C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('write_int_config', lambda v, s, k, *_: hook(v, s, k))
        keys = [C.c_char_p(b'PLAYSET'), C.c_char_p(b'PLAYMODE')]
        fw.mock('config_playmode', lambda m, notify, *_: setattr(mcl, 'mode', m) or hook(
            m, C.cast(keys[0], C.c_void_p), C.cast(keys[1], C.c_void_p)))
        fw.mock('stock_write_config_trampoline', self.write_file)
        fw.mock('toolsReadConfig', lambda path, s, k, out, d, *_: C.memmove(out, (read(
            self.file, C.string_at(s).decode(), C.string_at(k).decode()) or C.string_at(d).decode()).encode() + b'\0',
            4) and 0)
        for name in ('fw_fopen', 'fw_open', 'fw_rename', 'fw_fsync', 'fw_system'):  # sync is a system("sync")
            if hasattr(fw.lib, 'fw_mock_' + name):
                fw.mock(name, lambda *a, n=name: self.touched.append(n) and 0)
        self.set = fw.fn('np_mode_set', None, C.c_int, C.c_int)

    def write_file(self, v, s, k, *_):
        self.saved.append((C.c_int(v).value, C.string_at(s).decode(), C.string_at(k).decode()))
        self.file = stock_write(self.file, C.string_at(s), C.string_at(k), b'%d' % C.c_int(v).value)
        self.touched.append('config.ini')
        return 0

    def restart(self):
        """A new demo: Now Playing's pair read back from config.ini, the mode from its PLAYMODE."""
        self.st('np_modes_read', C.c_ubyte).value = 0
        self.mcl.mode = int(read(self.file, 'PLAYSET', 'PLAYMODE'))


def test_a_shuffle_tap_writes_no_file_in_its_pass(fw, mcl, st):
    q2 = Tap(fw, mcl, st)
    mcl.mode = 0
    q2.set(1, 0)  # Shuffle on, Repeat Off
    assert mcl.mode == 2 and q2.touched == [] and q2.saved == []  # the mode at once, no file touched


def test_a_shuffle_tap_reaches_the_file_a_key_a_pass_and_survives_a_restart(fw, mcl, st):
    q2 = Tap(fw, mcl, st)
    mcl.mode = 0
    q2.set(1, 0)
    q2.ms += MODE_SETTLE_MS - 1
    q2.settle(0)
    assert q2.saved == []
    q2.ms += 1
    for n in (1, 2, 3, 3):  # power_poll's passes after it: one rewrite each, then nothing held
        q2.settle(0)
        assert len(q2.saved) == n and len(q2.touched) == n
    assert sorted(q2.saved) == TAPPED
    q2.restart()
    assert mcl.mode == 2 and fw.fn('np_mode_shuffle', C.c_int)() == 1


@pytest.mark.parametrize('rep, mode', [(2, 1), (1, 3)])
def test_a_repeat_tap_is_held_the_same_way(fw, mcl, st, rep, mode):
    q2 = Tap(fw, mcl, st)
    mcl.mode = 0
    q2.set(0, rep)  # Repeat One, or Repeat All
    assert mcl.mode == mode and q2.touched == []
    q2.ms += MODE_SETTLE_MS
    for _ in range(3):
        q2.settle(0)
    q2.restart()
    assert mcl.mode == mode and fw.fn('np_mode_repeat', C.c_int)() == rep


def test_a_power_off_inside_the_window_still_saves_the_mode(fw, mcl, st):
    q2 = Tap(fw, mcl, st)
    mcl.mode = 0
    q2.set(1, 0)
    q2.ms += 100
    fw.fn('ringnav_clean_end', None)()  # power-off, Restart or an update's shutdown, 100 ms after the tap
    assert sorted(q2.saved) == TAPPED
    q2.restart()
    assert mcl.mode == 2 and fw.fn('np_mode_shuffle', C.c_int)() == 1
