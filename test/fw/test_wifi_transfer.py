"""Wi-Fi transfer's PIN and idle timer: the real wifi_transfer.c around stock's server switch, with /tmp, /proc
and /dev/urandom as a dict and the window manager as one page (docs/internals.md#wi-fi-transfer-pin)."""
import ctypes as C

import pytest

DIR = '/tmp/q2wifi'
PAGE, WM, PARENT = 0x5100, 0x5200, 0x5400
IDLE_MS = 10 * 60 * 1000


class Q2:
    """files: path -> bytes; dirs: the folders; random: what /dev/urandom gives, in order; procs: live pids."""
    def __init__(self, fw, random=bytes(range(48))):
        self.fw, self.random = fw, bytearray(random)
        self.files, self.dirs, self.procs = {}, set(), set()
        self.fds, self.listing, self.keep = {}, {}, []
        self.server, self.page_open, self.ms = [], True, 1_000_000
        self.texts, self.closed, self.mtime = {}, 0, {}
        self.switch = fw.fn('ringnav_wifi_transfer', C.c_int, C.c_int)
        self.page_init = fw.fn('ringnav_wifi_transfer_page', C.c_int, C.c_void_p, C.c_void_p)
        self.poll = fw.fn('wifi_transfer_poll', None)
        self.flag = fw.var('g_wifitransport_flag', C.c_ubyte)
        fw.mock('time_now_ms', lambda *_: self.ms & 0xffffffff)
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', self.read)
        fw.mock('fw_write', self.write)
        fw.mock('fw_close', lambda fd, *_: self.fds.pop(fd, None) and 0)
        fw.mock('fw_mkdir', self.mkdir)
        fw.mock('fw_rmdir', self.rmdir)
        fw.mock('fw_unlink', self.unlink)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda *_: 0)
        fw.mock('fw_access', lambda p, *_: 0 if self.exists(s(p)) else -1)
        fw.mock('ringnav_statx', self.statx)
        fw.mock('stock_wifi_transfer_trampoline', self.stock_switch)
        fw.mock('stock_wifi_transfer_page_trampoline', self.stock_page)
        self.name = C.create_string_buffer(b'wifitransport_page')
        self.ip = (C.c_char * 0x400)()  # img_wifitransport's widget: zeroed, its parent read through P()
        fw.mock('window_manager', lambda *_: WM)
        fw.mock('widget_count_children', lambda w, *_: 1 if w == WM and self.page_open else 0)
        fw.mock('widget_get_child', lambda *_: PAGE)
        fw.mock('widget_get_prop_str', lambda *_: C.addressof(self.name))
        fw.mock('tk_strcmp', lambda a, b, *_: (C.string_at(a) != C.string_at(b)))
        fw.mock('window_close', self.close)
        fw.mock('widget_lookup', lambda w, n, *_: C.addressof(self.ip) if s(n) == 'img_wifitransport' else 0)
        fw.mock('widget_set_text_utf8', lambda w, t, *_: self.texts.__setitem__(w, s(t)))

    # the stock side
    def stock_switch(self, on, *_):
        self.server.append(on)
        self.flag.value = on
        return 1

    def stock_page(self, win, ctx, *_):
        self.switch(1)
        return 7

    def close(self, win, *_):
        assert win == PAGE
        self.closed += 1
        self.page_open = False
        self.switch(0)  # the page's EVT_WINDOW_CLOSE handler
        return 0

    # the filesystem
    def exists(self, p):
        return p in self.files or p in self.dirs or (p.startswith('/proc/') and int(p[6:]) in self.procs)

    def open(self, path, flags, mode, *_):
        p = s(path)
        if p == '/dev/urandom':
            fd = 100 + len(self.fds)
            self.fds[fd] = 'random'
            return fd
        assert flags == 0x501 and mode == 0o600, (p, hex(flags))
        if p.rsplit('/', 1)[0] not in self.dirs or p in self.files:
            return -1
        self.files[p] = b''
        fd = 100 + len(self.fds)
        self.fds[fd] = p
        return fd

    def read(self, fd, buf, n, *_):
        assert self.fds[fd] == 'random'
        n = min(n, len(self.random))
        C.memmove(buf, bytes(self.random[:n]), n)
        del self.random[:n]
        return n

    def write(self, fd, buf, n, *_):
        self.files[self.fds[fd]] += C.string_at(buf, n)
        return n

    def mkdir(self, path, mode, *_):
        p = s(path)
        if p in self.dirs:
            return -1
        assert mode == 0o700
        self.dirs.add(p)
        return 0

    def rmdir(self, path, *_):
        p = s(path)
        if p not in self.dirs or any(k.startswith(p + '/') for k in [*self.files, *self.dirs]):
            return -1
        self.dirs.remove(p)
        return 0

    def unlink(self, path, *_):
        return 0 if self.files.pop(s(path), None) is not None else -1

    def opendir(self, path, *_):
        p = s(path)
        if p not in self.dirs:
            return 0
        names = [k[len(p) + 1:] for k in [*self.files, *self.dirs] if k.startswith(p + '/')]
        d = 0x700 + len(self.listing)
        self.listing[d] = [b'.', b'..', *(n.encode() for n in names)]
        return d

    def readdir(self, d, *_):
        if not self.listing[d]:
            return 0
        name = self.listing[d].pop(0)
        ent = C.create_string_buffer(268)
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.keep.append(ent)
        return C.addressof(ent)

    def statx(self, dirfd, path, flags, mask, buf, *_):
        p = s(path)
        if p not in self.files:
            return -1
        words = (C.c_uint * 64).from_address(buf)
        words[28], words[30] = self.mtime.get(p, (0, 0))
        return 0

    # what q2cgi does
    def request(self):
        self.files[f'{DIR}/seen'] = b''
        self.mtime = {**self.mtime, f'{DIR}/seen': (self.ms // 1000, self.ms % 1000 * 1000000)}

    def wait(self, ms, step=1000):
        for _ in range(ms // step):
            self.ms += step
            self.poll()

    def session(self):
        return {k[len(DIR) + 1:]: v for k, v in self.files.items() if k.startswith(DIR + '/')}


def s(p):
    return C.string_at(p).decode()


def start(fw, **kw):
    q = Q2(fw, **kw)
    fw.mock('hscroll_label_create', lambda parent, x, y, *_: PARENT + y)
    assert q.page_init(PAGE, 0) == 7
    return q


def test_a_session_is_written_before_the_server_starts_and_shown(fw):
    q = Q2(fw)
    seen = []
    fw.mock('stock_wifi_transfer_trampoline', lambda on, *_: seen.append(dict(q.session())) or 1)
    fw.mock('hscroll_label_create', lambda parent, x, y, *_: PARENT + y)
    q.page_init(PAGE, 0)
    first = seen[0]
    assert set(first) == {'pin', 'token'} and DIR in q.dirs
    # the token is the first 16 random bytes in hex; the PIN the next little-endian word mod a million
    assert first['token'] == bytes(range(16)).hex().encode()
    word = int.from_bytes(bytes(range(16, 20)), 'little')
    assert first['pin'] == b'%06d' % (word % 1000000)
    pin = first['pin'].decode()
    assert f'{pin[:3]} {pin[3:]}' in q.texts.values()


def test_a_draw_past_the_last_whole_million_is_drawn_again(fw):
    over = (4294000000).to_bytes(4, 'little')
    q = start(fw, random=bytes(16) + over * 3 + (1234567).to_bytes(4, 'little') + bytes(16))
    assert q.session()['pin'] == b'234567'


def test_eight_draws_over_the_line_start_nothing(fw):
    q = start(fw, random=bytes(16) + b'\xff' * 32)
    assert q.server == [] and DIR not in q.dirs and 'No PIN' in q.texts.values()


def test_no_random_bytes_no_server_and_the_page_says_so(fw):
    q = start(fw, random=b'\x01' * 20)
    assert q.server == [] and not q.session() and 'Wi-Fi transfer could not start' in q.texts.values()


def test_leaving_the_page_kills_the_server_then_removes_the_session(fw):
    q = start(fw)
    q.dirs.add(f'{DIR}/try.1')
    q.files[f'{DIR}/busy.42'] = b''
    order = []
    fw.mock('stock_wifi_transfer_trampoline', lambda on, *_: order.append((on, dict(q.session()))) or 1)
    q.switch(0)
    assert order[0][0] == 0 and 'pin' in order[0][1]  # the server dies while the session is still there
    assert not q.session() and not any(d.startswith(DIR) for d in q.dirs)


def test_a_session_left_by_a_crash_is_replaced_whole(fw):
    q = Q2(fw)
    q.dirs |= {DIR, f'{DIR}/try.1', f'{DIR}/try.5'}
    q.files.update({f'{DIR}/pin': b'111111', f'{DIR}/token': b'f' * 32, f'{DIR}/seen': b''})
    fw.mock('hscroll_label_create', lambda parent, x, y, *_: PARENT + y)
    q.page_init(PAGE, 0)
    assert set(q.session()) == {'pin', 'token'} and q.session()['pin'] != b'111111'
    assert f'{DIR}/try.5' not in q.dirs and q.server == [1]


def test_ten_idle_minutes_close_the_page_and_end_the_session(fw):
    q = start(fw)
    q.wait(IDLE_MS - 2000)
    assert q.page_open and q.server == [1]
    q.wait(3000)
    assert not q.page_open and q.server == [1, 0] and not q.session()
    assert q.closed == 1
    q.wait(IDLE_MS)
    assert q.closed == 1


def test_a_request_restarts_the_count(fw):
    q = start(fw)
    q.wait(IDLE_MS - 5000)
    q.request()
    q.wait(IDLE_MS - 5000)
    assert q.page_open
    q.wait(10000)
    assert not q.page_open


def test_a_running_request_is_never_idle_and_a_dead_ones_mark_counts_for_nothing(fw):
    q = start(fw)
    q.files[f'{DIR}/busy.42'] = b''
    q.procs.add(42)
    q.wait(3 * IDLE_MS)
    assert q.page_open  # a long upload
    q.procs.clear()  # killed before its trap removed the mark
    q.wait(IDLE_MS + 2000)
    assert not q.page_open


def test_the_count_is_monotonic_whatever_the_seen_marks_clock_says(fw):
    q = start(fw)
    q.wait(IDLE_MS // 2)
    q.request()
    q.mtime = {f'{DIR}/seen': (5, 0)}  # a time sync moved the wall clock back: still a change, still a request
    q.wait(IDLE_MS - 3000)
    assert q.page_open


def test_five_tries_spent_show_locked(fw):
    q = start(fw)
    q.dirs |= {f'{DIR}/try.{k}' for k in range(1, 5)}
    q.wait(2000)
    assert 'Locked' not in q.texts.values()
    q.dirs.add(f'{DIR}/try.5')
    q.wait(2000)
    assert 'Locked' in q.texts.values()


def test_no_session_no_poll(fw):
    q = Q2(fw)
    q.wait(3 * IDLE_MS)
    assert not q.closed and not fw.calls('fw_opendir')
