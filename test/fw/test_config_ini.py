"""config.ini's sections each start a line (patch/config_ini.c, docs/internals.md#config-ini): toolsWriteConfig's hook puts
the file right before stock's own write, here a model of it that does what V1.36's did in the emulator."""
import ctypes as C

from conftest import Files

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
