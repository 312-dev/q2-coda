"""patch/splash_hold.c: demo's screen shows nothing while q2splash holds its lock, then its current frame
(docs/boot.md#boot-animation)."""
import ctypes as C
import struct

MARK = b'/tmp/q2splash.run'
SWAP, FLUSH = 0x60, 0x74  # offsets.inc LCD_SWAP, LCD_FLUSH
STOCK_SWAP, STOCK_FLUSH = 0x6398d8, 0x6398d9
EAGAIN, EINVAL = 11, 22


class Sh(C.Structure):
    _fields_ = [('lcd', C.c_void_p), ('swap', C.c_void_p), ('flush', C.c_void_p), ('fd', C.c_int), ('told', C.c_int),
                ('since', C.c_ulonglong)]


class Boot:
    """lcd_linux_fb_create's lcd, the marker q2splash keeps, its lock and the clock."""
    def __init__(self, fw, mark=True, lock=EAGAIN):
        self.fw, self.mark, self.lock, self.ms = fw, mark, lock, 5000
        self.raw = C.create_string_buffer(0x200)
        self.set(SWAP, STOCK_SWAP)
        self.set(FLUSH, STOCK_FLUSH)
        self.errno = C.c_int.in_dll(fw.lib, 'fw_errno')
        fw.mock('stock_lcd_create_trampoline', lambda *_: C.addressof(self.raw))
        fw.mock('fw_open', lambda path, *_: 7 if self.mark and C.string_at(path) == MARK else -1)
        fw.mock('fw_fcntl', self.fcntl)
        fw.mock('fw_clock_gettime', lambda which, ts, *_: struct.pack_into(
            '<qq', (C.c_char * 16).from_address(ts), 0, self.ms // 1000, self.ms % 1000 * 1000000))
        fw.mock('window_manager', lambda *_: 0x9000)
        self.create = fw.fn('splash_lcd', C.c_void_p, C.c_char_p)
        self.poll = fw.fn('splash_poll', None)
        self.sh = fw.var('splash_hold__sh', Sh)

    def fcntl(self, fd, cmd, lock, *_):
        assert fd == 7 and cmd == 6 and not any((C.c_char * 64).from_address(lock).raw)  # F_SETLK, F_RDLCK, all of it
        if not self.lock:
            return 0
        self.errno.value = self.lock
        return -1

    def get(self, off):
        return struct.unpack_from('<Q', self.raw, off)[0]

    def set(self, off, value):
        struct.pack_into('<Q', self.raw, off, value)

    def held(self):
        hold = C.cast(self.fw.lib.splash_hold__hold, C.c_void_p).value
        return (self.get(SWAP), self.get(FLUSH)) == (hold, hold)

    def shown(self):
        return (self.get(SWAP), self.get(FLUSH)) == (STOCK_SWAP, STOCK_FLUSH)


def test_held_while_q2splash_plays_then_shown_whole(fw):
    b = Boot(fw)
    assert b.create(b'/dev/fb0') == C.addressof(b.raw) and b.held()
    assert fw.fn('splash_hold__hold', C.c_int, C.c_void_p)(None) == 0  # end_frame goes on
    b.ms += 9999
    b.poll()
    assert b.held() and not fw.calls('fw_unlink') and not fw.calls('widget_invalidate_force')
    b.lock = 0  # q2splash drew its last frame
    b.poll()
    assert b.shown() and b.sh.lcd is None
    assert fw.calls('fw_close')[-1][1][0] == 7 and fw.calls('widget_invalidate_force')[-1][1][:2] == (0x9000, 0)
    assert not fw.calls('fw_unlink')  # q2splash removes its own
    b.poll()
    assert len(fw.calls('widget_invalidate_force')) == 1  # once shown, a flag test


def test_without_q2splash_nothing_is_held(fw):
    b = Boot(fw, mark=False)  # a card logo, Light Mode, a --logo build
    b.create(b'/dev/fb0')
    assert b.shown() and b.sh.lcd is None and not fw.calls('fw_fcntl')
    b.poll()
    assert not fw.calls('widget_invalidate_force')


def test_a_marker_whose_lock_is_free_holds_nothing(fw):
    b = Boot(fw, lock=0)  # q2splash killed before Rockbox: the marker stays, its lock went with it
    b.create(b'/dev/fb0')
    assert b.shown() and b.sh.lcd is None and fw.calls('fw_close')[-1][1][0] == 7


def test_a_lock_that_fails_otherwise_holds_nothing(fw):
    b = Boot(fw, lock=EINVAL)
    b.create(b'/dev/fb0')
    assert b.shown() and b.sh.lcd is None


def test_an_lcd_without_swap_or_flush_is_left(fw):
    for off in (SWAP, FLUSH):
        b = Boot(fw)
        b.set(off, 0)
        b.create(b'/dev/fb0')
        assert b.sh.lcd is None and not b.held()


def test_no_lcd(fw):
    b = Boot(fw)
    fw.mock('stock_lcd_create_trampoline', lambda *_: 0)
    assert b.create(b'/dev/fb0') is None and b.sh.lcd is None


def test_at_its_longest_demo_stops_q2splash_then_shows(fw):
    b = Boot(fw)
    b.create(b'/dev/fb0')
    b.ms += 10000
    b.poll()
    assert b.held() and [C.string_at(a[0]) for _, a in fw.calls('fw_unlink')] == [MARK]
    b.ms += 100
    b.poll()
    assert b.held() and len(fw.calls('fw_unlink')) == 1
    b.lock = 0  # q2splash saw the marker gone
    b.poll()
    assert b.shown() and len(fw.calls('widget_invalidate_force')) == 1


def test_a_q2splash_that_never_stops_is_shown_over(fw):
    b = Boot(fw)
    b.create(b'/dev/fb0')
    b.ms += 10499
    b.poll()
    assert b.held()
    b.ms += 1
    b.poll()
    assert b.shown() and len(fw.calls('fw_unlink')) == 1
