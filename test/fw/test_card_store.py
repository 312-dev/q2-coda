"""card_store.c (docs/internals.md#where-vamp-writes): a card file with a RAM copy while the card is away, written
when it is back, and an appended log whose lines wait in a RAM ring, in order."""
import ctypes as C

from conftest import Files

MOUNTS = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'
CARD, FLASH, LOG = '/mnt/mmc/.q2pod/rec', '/mnt/data/rec', '/mnt/mmc/.q2pod/log'


class Blob(C.Structure):
    _fields_ = [('card', C.c_char_p), ('flash', C.c_char_p), ('cap', C.c_uint), ('ram', C.c_void_p),
                ('len', C.c_uint), ('room', C.c_uint), ('held', C.c_int), ('moved', C.c_int)]


class Log(C.Structure):
    _fields_ = [('card', C.c_char_p), ('cap', C.c_uint), ('ring', C.c_void_p), ('len', C.c_uint), ('dropped', C.c_uint)]


class Store:
    def __init__(self, fw, files=None):
        self.files = Files(fw, files)
        self.card(True)
        self.write = fw.fn('card_blob_write', C.c_int, C.c_void_p, C.c_char_p, C.c_uint)
        self.read_ = fw.fn('card_blob_read', C.c_int, C.c_void_p, C.c_char_p, C.c_uint)
        self.flush = fw.fn('card_blob_flush', C.c_int, C.c_void_p)
        self.append_ = fw.fn('card_log_append', C.c_int, C.c_void_p, C.c_char_p, C.c_uint)
        self.log_flush = fw.fn('card_log_flush', C.c_int, C.c_void_p)
        self.blob = Blob(CARD.encode(), FLASH.encode(), 64)
        self.log = Log(LOG.encode(), 16)

    def card(self, there):
        if there:
            self.files.files['/proc/mounts'] = MOUNTS
        else:
            self.files.files.pop('/proc/mounts', None)

    def read(self):
        out = C.create_string_buffer(64)
        n = self.read_(C.byref(self.blob), out, 64)
        return None if n < 0 else out.raw[:n]

    def append(self, line):
        return self.append_(C.byref(self.log), line, len(line))


def test_a_blob_with_the_card_away_is_held_read_back_and_written_when_it_returns(fw):
    s = Store(fw)
    s.write(C.byref(s.blob), b'one', 3)
    s.card(False)
    s.write(C.byref(s.blob), b'two', 3)
    s.write(C.byref(s.blob), b'three', 5)
    assert s.files.files[CARD] == b'one' and s.read() == b'three'  # the newest, from RAM
    assert s.flush(C.byref(s.blob)) == 0  # still away: still held
    s.card(True)
    assert s.flush(C.byref(s.blob)) == 1 and s.files.files[CARD] == b'three'
    assert FLASH not in s.files.files


def test_a_blob_larger_than_its_cap_is_not_held(fw):
    s = Store(fw)
    s.card(False)
    assert s.write(C.byref(s.blob), b'x' * 65, 65) == 0 and s.read() is None


def test_the_flash_file_reads_with_no_card_and_is_copied_once_left_unwritten(fw):
    s = Store(fw, {FLASH: b'old'})
    s.card(False)
    assert s.read() == b'old' and CARD not in s.files.files  # boot with no card
    s.card(True)
    assert s.read() == b'old' and s.files.files[CARD] == b'old'  # the migration
    s.write(C.byref(s.blob), b'new', 3)
    assert s.read() == b'new' and s.files.files[FLASH] == b'old'


def test_log_lines_wait_in_order_and_go_before_the_next_once_the_card_is_back(fw):
    s = Store(fw)
    s.append(b'1\n')
    s.card(False)
    s.append(b'2\n')
    s.append(b'3\n')
    assert s.files.files[LOG] == b'1\n'
    s.card(True)
    s.append(b'4\n')
    assert s.files.files[LOG] == b'1\n2\n3\n4\n'


def test_a_full_ring_drops_its_oldest_whole_lines_and_says_so(fw):
    s = Store(fw)
    s.card(False)
    for line in (b'aaaaa\n', b'bbbbb\n', b'ccccc\n'):  # 18 bytes into 16
        s.append(line)
    s.card(True)
    assert s.log_flush(C.byref(s.log)) == 1
    assert s.files.files[LOG] == b'== 1 lines lost while the card was away\nbbbbb\nccccc\n'
