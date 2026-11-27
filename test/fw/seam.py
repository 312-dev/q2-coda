"""The firmware's half of the Subsonic seam test (test/fw/test_seam_subsonic.py, run by test/subsonic_seam.py): the
payload's C as test/fw builds it, with its card, /tmp and flash calls on real files under one per-test root, so the
real app, running in a namespace where that root's card/ is /mnt/mmc and its tmp/ is /tmp, reads and writes the very
same files. Nothing here touches this machine's /tmp or /mnt: a firmware path is always mapped into the root.

The pieces of stock the C calls (its queue, Now Playing, the player, SQLite's database.db) are stood in for here as
test/fw's own tests do (test_addon.Stream, test_catalogue.Rig), on real files and the real clock: the app runs in
real time, so every wait is a poll for a state with a deadline, never a sleep that asserts."""
import ctypes as C
import ctypes.util
import errno
import http.client
import json
import os
import pathlib
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
import urllib.parse

from conftest import O, claim, code_at, high_halves, libc, low_map, ret_code

ADDR = '10.77.0.1'  # the fake server's, on the namespace's own loopback: an RFC 1918 address the app takes http:// to
PORT = 4533
SONGTABLE = ('create table songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename, fileurl, album, songer, '
             'style, name, composer, albumsonger, year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, '
             'time_end INTEGER, time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)')
CARD_DB = b'/mnt/mmc/.q2pod/'
# MIPS o32's open flags, as the payload is compiled with them (patch/q2guard.h)
O_FLAGS = {0x1: os.O_WRONLY, 0x2: os.O_RDWR, 0x8: os.O_APPEND, 0x100: os.O_CREAT, 0x200: os.O_TRUNC, 0x400: os.O_EXCL}
REC_SIZE = O['REC_SIZE']


def now_ms():
    return int(time.monotonic() * 1000) & 0xffffffff


class Records:
    """Stock's song records (stSongInfo) by path, each made once. On the Q2 REC_NAME and REC_PATH are 4-byte
    pointers a word apart; here P() reads 8, so REC_NAME's high half is REC_PATH's low half (test_addon.Songs):
    the names live under 2 GB (high half 0, conftest.low_map) and each path at a multiple of 4 GB (low half 0),
    at high halves from conftest.high_halves, taken 256 at a time as the paths come."""
    def __init__(self):
        self.maps, self.by_path, self.keep, self.used, self.highs = [], {}, [], 0, iter(())
        self.names = low_map(1 << 22)
        self.maps.append((self.names, 1 << 22))

    def high(self):
        try:
            return next(self.highs)
        except StopIteration:
            self.highs = iter(high_halves(0x100))
            return next(self.highs)

    def __call__(self, path):
        if path not in self.by_path:
            name = path.rsplit(b'/', 1)[-1]
            assert self.used + len(name) + 1 < 1 << 22, 'out of name room'
            at, high = self.names + self.used, self.high()
            C.memmove(at, name + b'\0', len(name) + 1)
            self.used += len(name) + 1
            page = high << 32
            assert libc.mmap(page, 0x1000, 3, 0x100022, -1, 0) == page
            self.maps.append((page, 0x1000))
            C.memmove(page, path + b'\0', len(path) + 1)
            r = C.create_string_buffer(REC_SIZE + 8)
            C.c_uint64.from_address(C.addressof(r) + O['REC_NAME']).value = at
            C.c_uint32.from_address(C.addressof(r) + O['REC_PATH'] + 4).value = high
            C.c_int32.from_address(C.addressof(r) + O['REC_TYPE']).value = 8  # a song
            self.keep.append(r)
            self.by_path[path] = C.addressof(r)
        return self.by_path[path]

    def free(self):
        libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
        for address, size in self.maps:
            libc.munmap(address, size)


def path_of(record):
    p = C.c_uint64.from_address(record + O['REC_PATH']).value
    return C.string_at(p) if p else b''


class Firmware:
    """The payload with its files on disk under root (card/, tmp/, data/), stock's queue, Now Playing and the
    player stood in for, and step() as a UI loop pass: addon_poll, the timers it set, catalogue_poll."""
    def __init__(self, fw, mcl, root):
        self.fw, self.mcl, self.root = fw, mcl, pathlib.Path(root)
        for d in ('card', 'tmp', 'data', 'other'):
            (self.root/d).mkdir(parents=True, exist_ok=True)
        self.files, self.dirs, self.fds = {}, {}, {}
        self.log, self.toasts, self.loads, self.stderr, self.started = [], [], [], [], []
        self.changes, self.handed = 0, None
        self.records = Records()
        self.lists, self.cells, self.rows = {}, [], set()
        self.timers, self.timer_ids = {}, 0
        self.settings: dict = {}
        self.explain = lambda: ''  # what a timeout adds to its message (the seam test: the app's logs)
        db = sqlite3.connect(self.host(b'/mnt/data/database.db'))
        db.executescript(f'{SONGTABLE};' + SONGTABLE.replace('songtable', 'playlist').replace(' AUTOINCREMENT', '') + ';' +
                         SONGTABLE.replace('songtable', 'myfav'))
        db.commit()
        db.close()
        self.sqlite = C.CDLL(ctypes.util.find_library('sqlite3'))
        self.sqlite.sqlite3_open.argtypes = [C.c_char_p, C.c_void_p]
        self.sqlite.sqlite3_exec.argtypes = [C.c_void_p] * 5
        self.sqlite.sqlite3_close.argtypes = [C.c_void_p]
        m = fw.mock
        for name, f in (('fw_fopen', self.fopen), ('fw_fread', self.fread), ('fw_fwrite', self.fwrite),
                        ('fw_fseek', self.fseek), ('fw_ftell', self.ftell), ('fw_fclose', self.fclose),
                        ('fw_fflush', lambda *_: 0), ('fw_fsync', lambda *_: 0), ('fw_fileno', lambda f, *_: f),
                        ('fw_ferror', lambda *_: 0), ('fw_fputs', self.fputs),
                        ('fw_open', self.open), ('fw_read', self.read), ('fw_write', self.write), ('fw_close', self.fd_close),
                        ('fw_lseek64', self.lseek), ('fw_ftruncate', self.ftruncate),
                        ('fw_rename', self.rename), ('fw_unlink', self.unlink), ('fw_access', self.access),
                        ('fw___xstat', self.stat), ('fw_mkdir', self.mkdir), ('fw_rmdir', self.rmdir),
                        ('fw_opendir', self.opendir), ('fw_readdir', self.readdir), ('fw_closedir', self.closedir),
                        ('ringnav_statx', self.statx), ('fw_system', self.system),
                        ('fw_time', lambda *_: int(time.time())), ('time_now_ms', lambda *_: now_ms()),
                        ('fw_clock_gettime', self.clock), ('fw_usleep', lambda *_: 0),
                        ('fw_pthread_create', lambda t, attr, fn, arg, *_: C.CFUNCTYPE(C.c_void_p, C.c_void_p)(fn)(arg) and 0),
                        ('tk_str_start_with', lambda s, p, *_: bool(s and p) and C.string_at(s).startswith(C.string_at(p))),
                        ('sqlite3_open', self.db_open), ('sqlite3_exec', self.db_exec),
                        ('sqlite3_close', lambda db, *_: self.sqlite.sqlite3_close(db)),
                        ('networkGetConnectStatus', lambda *_: 3), ('toolsReadConfig', self.config),
                        ('fw__create_deque', self.new_deque), ('fw_deque_size', lambda q, *_: len(self[q])),
                        ('fw_deque_at', lambda q, i, *_: self[q][i]), ('fw__deque_push_back', self.push),
                        ('fw_deque_clear', lambda q, *_: self[q].clear()), ('fw_deque_assign', self.assign),
                        ('fw_deque_init_copy', self.assign), ('fw_deque_destroy', lambda q, *_: self.lists.pop(q & 0xffffffff, None) and 0),
                        ('toolsLoadDirectory', self.listing), ('timer_add', self.timer_add),
                        ('timer_remove', lambda t, *_: self.timers.pop(t, None)),
                        ('navigator_to_with_context', self.navigate), ('player_start_queue', self.start_queue),
                        ('stock_change_trampoline', self.stock_change), ('navigator_window_is_exist', lambda *_: 0)):
            if hasattr(fw.lib, f'fw_mock_{name}'):  # a call the payload makes; the others it never reaches
                m(name, f)
        fw.var('g_backlight_status', C.c_ubyte).value = 1  # lit: catalogue_poll runs
        self.queue, self.pool = self.new_deque(0), self.new_deque(0)
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.queue
        C.c_uint.from_address(O['MCL_POOL']).value = self.pool
        self.loaded = fw.var('playback_loads', C.c_uint)
        # stock's toolsFreeStSongInfo, which addon.c calls at its address (want_show), not through a stub: a return
        # there, as stock_memory zeroes these pages before each test (the record it frees is the C's own)
        claim(0x5d2e10)
        code_at(0x5d2e10, ret_code())
        (self.root/'other/proc').mkdir(parents=True, exist_ok=True)
        (self.root/'other/proc/mounts').write_bytes(b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n')

    # ---- paths: a firmware path is always one under root ----
    def host(self, path):
        p = path.decode() if isinstance(path, bytes) else path
        for prefix, d in (('/mnt/mmc', 'card'), ('/tmp', 'tmp'), ('/mnt/data', 'data')):
            if p == prefix or p.startswith(prefix + '/'):
                return str(self.root/d) + p[len(prefix):]
        return str(self.root/'other') + (p if p.startswith('/') else '/' + p)

    # ---- stdio ----
    def fopen(self, path, mode, *_):
        how = C.string_at(mode).decode().replace('b', '')
        real = self.host(C.string_at(path))
        try:
            f = open(real, how + 'b')
        except OSError:
            return 0
        h = 0x3000 + len(self.files)
        while h in self.files:
            h += 1
        self.files[h] = f
        return h

    def fread(self, buf, size, n, f, *_):
        data = self.files[f].read(size * n)
        C.memmove(buf, data, len(data))
        return len(data) // size if size else 0

    def fwrite(self, buf, size, n, f, *_):
        self.files[f].write(C.string_at(buf, size * n))
        return n

    def fputs(self, s, f, *_):
        self.files[f].write(C.string_at(s))
        return 1

    def fseek(self, f, off, whence, *_):
        try:
            self.files[f].seek(C.c_long(off).value, whence)
            return 0
        except OSError:
            return -1

    def ftell(self, f, *_):
        return self.files[f].tell()

    def fclose(self, f, *_):
        self.files.pop(f).close()
        return 0

    # ---- file descriptors (o32 flags); fd 2 is the payload's log ----
    def open(self, path, flags, mode=0o644, *_):
        f = os.O_RDONLY
        for bit, flag in O_FLAGS.items():
            if flags & bit:
                f |= flag
        try:
            fd = os.open(self.host(C.string_at(path)), f, 0o644)
        except OSError:
            return -1
        self.fds[fd + 1000] = fd
        return fd + 1000

    def read(self, fd, buf, n, *_):
        if fd not in self.fds:
            return -1
        data = os.read(self.fds[fd], n)
        C.memmove(buf, data, len(data))
        return len(data)

    def write(self, fd, data, n, *_):
        if fd == 2:
            self.stderr.append(C.string_at(data, n).decode(errors='replace'))
            return n
        return os.write(self.fds[fd], C.string_at(data, n)) if fd in self.fds else -1

    def fd_close(self, fd, *_):
        if fd in self.fds:
            os.close(self.fds.pop(fd))
        return 0

    def lseek(self, fd, off, whence, *_):
        return os.lseek(self.fds[fd], C.c_long(off).value, whence) if fd in self.fds else -1

    def ftruncate(self, fd, size, *_):
        os.ftruncate(self.fds[fd], size)
        return 0

    # ---- the file system ----
    def rename(self, a, b, *_):
        self.log.append(('rename', C.string_at(a), C.string_at(b)))
        try:
            os.replace(self.host(C.string_at(a)), self.host(C.string_at(b)))
            return 0
        except OSError:
            return -1

    def unlink(self, p, *_):
        self.log.append(('unlink', C.string_at(p)))
        try:
            os.unlink(self.host(C.string_at(p)))
            return 0
        except OSError:
            return -1

    def access(self, p, *_):
        return 0 if os.path.lexists(self.host(C.string_at(p))) else -1

    def stat(self, version, path, info, *_):
        """__xstat as o32 lays struct stat out (test_addon.World, conftest.Externpic): mode word 5, size 12,
        mtime 16 and its nanoseconds 17."""
        try:
            st = os.stat(self.host(C.string_at(path)))
        except OSError:
            C.c_int.from_address(self.fw.fn('fw___errno_location', C.c_void_p)()).value = errno.ENOENT
            return -1
        words = (C.c_uint * 36).from_address(info)
        words[5], words[12] = st.st_mode & 0xffffffff, min(st.st_size, 0xffffffff)
        words[16], words[17] = int(st.st_mtime), st.st_mtime_ns % 1000000000
        return 0

    def statx(self, dirfd, path, flags, mask, out, *_):
        try:
            st = os.stat(self.host(C.string_at(path)))
        except OSError:
            return 2
        sx = (C.c_uint * 64).from_address(out)
        sx[7], sx[10] = st.st_mode & 0xffff, min(st.st_size, 0xffffffff)
        return 0

    def mkdir(self, p, *_):
        try:
            os.mkdir(self.host(C.string_at(p)))
            return 0
        except OSError:
            return -1

    def rmdir(self, p, *_):
        try:
            os.rmdir(self.host(C.string_at(p)))
            return 0
        except OSError:
            return -1

    def opendir(self, path, *_):
        real = self.host(C.string_at(path))
        try:
            names = sorted(os.listdir(real))
        except OSError:
            return 0
        d = 0x5000 + len(self.dirs)
        while d in self.dirs:
            d += 1
        self.dirs[d] = [(n, os.path.isdir(os.path.join(real, n))) for n in names]
        return d

    def readdir(self, d, *_):
        rows = self.dirs.get(d)
        if not rows:
            return 0
        name, folder = rows.pop(0)
        ent = C.create_string_buffer(268)  # host.py's struct dirent: d_type at 10, d_name at 11
        ent[10] = b'\x04' if folder else b'\x08'
        raw = name.encode()[:255]
        C.memmove(C.addressof(ent) + 11, raw, len(raw))
        self.keep_ent = ent
        return C.addressof(ent)

    def closedir(self, d, *_):
        self.dirs.pop(d, None)
        return 0

    def system(self, cmd, *_):
        self.log.append(('system', C.string_at(cmd)))
        return 0

    def clock(self, which, ts, *_):
        t = C.cast(ts, C.POINTER(C.c_long))
        ns = time.monotonic_ns() if which in (1, 4, 7) else time.time_ns()
        t[0], t[1] = ns // 1000000000, ns % 1000000000
        return 0

    def config(self, path, section, key, out, default, *_):
        """config.ini as set in self.settings ({b'AHEAD': b'3'}), else each key's default."""
        v = self.settings.get(C.string_at(key), C.string_at(default) if default else b'')
        C.memmove(out, v + b'\0', len(v) + 1)
        return 0

    # ---- stock's SQLite on the real files ----
    def db_open(self, name, db, *_):
        return self.sqlite.sqlite3_open(self.host(C.string_at(name)).encode(), db)

    def db_exec(self, db, sql, cb, ctx, err, *_):
        fixed = C.create_string_buffer(C.string_at(sql).replace(CARD_DB, self.host(CARD_DB).encode() + b'/'))
        return self.sqlite.sqlite3_exec(db, fixed, cb, ctx, err)

    def query(self, sql, *args):
        db = sqlite3.connect(self.host(b'/mnt/data/database.db'))
        db.execute(f"attach '{self.host(CARD_DB)}/catalogue.db' as cat")
        rows = db.execute(sql, args).fetchall()
        db.close()
        return rows

    # ---- stock's libcstl deques: a stSongInfo deque copies its rows in (stock's copy, 0x5c65ac) ----
    def new_deque(self, name, *_):
        handle = 0x1000 + len(self.lists)
        while handle in self.lists:
            handle += 1
        self.lists[handle] = []
        if name and C.string_at(name) == b'stSongInfo':
            self.rows.add(handle)
        return handle

    def __getitem__(self, q):
        return self.lists[q & 0xffffffff]

    def copy(self, v):
        r = C.create_string_buffer(C.string_at(v, REC_SIZE), REC_SIZE + 8)
        self.records.keep.append(r)
        return C.addressof(r)

    def push(self, q, v, *_):
        q &= 0xffffffff
        if q == self.pool:  # a deque of ints: deque_at gives an element's address
            self.cells.append(C.c_int(v))
            v = C.addressof(self.cells[-1])
        elif q in self.rows:
            v = self.copy(v)
        self.lists[q].append(v)

    def assign(self, q, other, *_):
        self[q][:] = [self.copy(v) if (q & 0xffffffff) in self.rows else v for v in self[other]]

    def queue_of(self, paths):
        """A stSongInfo deque of these paths' rows, as a Library list hands ringnav_load: a record with its path
        alone (test_catalogue.Rig.record), as nothing on that path reads a Library row's name."""
        q = self.new_deque(C.create_string_buffer(b'stSongInfo'))
        for p in paths:
            r, s = C.create_string_buffer(REC_SIZE + 8), C.create_string_buffer(p)
            self.records.keep += [r, s]
            C.c_uint64.from_address(C.addressof(r) + O['REC_PATH']).value = C.addressof(s)
            C.c_int32.from_address(C.addressof(r) + O['REC_TYPE']).value = 8
            self.push(q, C.addressof(r))
        return q

    # ---- stock's folder listing, timers and Now Playing ----
    def listing(self, path, *_):
        real = self.host(C.string_at(path))
        try:
            names = sorted(n for n in os.listdir(real) if os.path.isfile(os.path.join(real, n)))
        except OSError:
            names = []
        rows = [self.records(C.string_at(path).rstrip(b'/') + b'/' + n.encode()) for n in names]
        self[self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value][:] = rows
        return len(rows)

    def timer_add(self, fn, *_):
        self.timer_ids += 1
        self.timers[self.timer_ids] = fn
        return self.timer_ids

    def navigate(self, name, ctx, *_):
        if C.string_at(name) == b'playing_page':  # Now Playing copies the queue it is handed, at once
            self.handed = ([path_of(r) for r in self[C.c_void_p.from_address(ctx).value]], C.c_int.from_address(ctx + 8).value)
            self[self.queue][:] = self[C.c_void_p.from_address(ctx).value]
            self.mcl.pos = C.c_int.from_address(ctx + 8).value
            self.loaded.value += 1
            self.loads.append(C.c_int.from_address(ctx + 12).value)
        elif ctx:
            self.toasts.append(C.string_at(ctx + 8))
        return 0

    def stock_change(self, forward, *_):
        """Stock's player_change_music past the payload's hook, counted (fw.calls stops at 8,192 calls): the
        test does stock's move itself (test_seam_subsonic.Seam.change)."""
        self.changes += 1
        return 0

    def start_queue(self, at, *_):
        """Stock's player_start_queue: the queue's song at plays."""
        self.mcl.pos = C.c_int(at).value
        self.started.append(self.mcl.pos)
        return 0

    def fire(self):
        due, self.timers = self.timers, {}
        for fn in due.values():
            C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)

    def step(self):
        """A UI loop pass: addon_poll, the timers it set, catalogue_poll."""
        self.fw.fn('addon_poll', None)()
        self.fire()
        self.fw.fn('catalogue_poll', None)()

    def until(self, what, ok, timeout=30.0, every=0.02):
        """Steps the firmware until ok() holds; fails with what after timeout seconds."""
        end = time.monotonic() + timeout
        while True:
            self.step()
            got = ok()
            if got:
                return got
            if time.monotonic() > end:
                raise AssertionError(f'timed out after {timeout:.0f} s waiting for {what}\n' + self.explain())
            time.sleep(every)

    # ---- what the tests read ----
    def queued(self):
        return [path_of(r) for r in self[self.queue]]

    def tmp(self, name):
        try:
            return (self.root/'tmp'/name).read_bytes()
        except OSError:
            return None

    def card(self, rel):
        return self.root/'card'/rel

    def close(self):
        for f in self.files.values():
            f.close()
        for fd in self.fds.values():
            os.close(fd)
        self.records.free()


class Fake:
    """q2-fakesonic's control socket (its -ctl): what it was asked, and the cuts, delays and rates it is scripted with."""
    def __init__(self, path):
        self.path = path

    def call(self, method, what, **form):
        c = http.client.HTTPConnection('fake')
        c.sock = socket.socket(socket.AF_UNIX)
        c.sock.settimeout(10)
        c.sock.connect(self.path)
        body = urllib.parse.urlencode(form)
        c.request(method, what, body, {'Content-Type': 'application/x-www-form-urlencoded'} if form else {})
        r = c.getresponse()
        data = r.read()
        c.close()
        assert r.status == 200, (what, r.status, data)
        return data

    def hits(self):
        return json.loads(self.call('GET', '/log'))

    def tries(self, sid):
        return [h for h in self.hits() if h['id'] == sid]

    def cut(self, sid, at, hold_ms):
        self.call('POST', '/cut', id=sid, at=at, hold=hold_ms)

    def delay(self, op, ms):
        self.call('POST', '/delay', op=op, ms=ms)

    def rate(self, sid, bps):
        """sid's bytes sent at about bps bytes a second from its next request on (0: full speed)."""
        self.call('POST', '/rate', id=sid, bps=bps)


def die_with_parent():
    """In the child before exec: SIGKILL when the test's process ends, however it ends."""
    C.CDLL(None).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG


# The namespace: a user and mount namespace of its own, where root's card/ is /mnt/mmc and its tmp/ is /tmp, and
# a network namespace whose loopback carries ADDR, so the fake server's fixed port is no other agent's, and a PID
# namespace that ends with the test (App.start).
NAMESPACE = r'''set -e
ip link set lo up
ip addr add {addr}/32 dev lo
mount -t tmpfs seam /mnt
mkdir -p /mnt/mmc
mount --bind {root}/card /mnt/mmc
mount --bind {root}/tmp /tmp
{fake} -listen {addr}:{port} -ctl {root}/ctl.sock -songs {songs} -album {album} -size {size} > {root}/fake.out 2>&1 &
i=0; while [ ! -S {root}/ctl.sock ] && [ $i -lt 200 ]; do sleep 0.05; i=$((i+1)); done
cd /mnt/mmc/.subsonic
exec {run} /mnt/mmc/.subsonic/q2-subsonic -dir /mnt/mmc/.subsonic -card /mnt/mmc -tmp /tmp > {root}/app.out 2>&1
'''


class App:
    """The real q2-subsonic and q2-fakesonic, in the namespace above: the app's card files from its zip (q2app,
    icons, run), its program the one under test (a host build, or the zip's own MIPS one under qemu), signed in
    to the fake server with a test account (no real credential: the fake takes only "seam")."""
    def __init__(self, root, program, fake, zip_files, songs=30, album=10, size=64 << 10, qemu=None):
        self.root = pathlib.Path(root)
        sub = self.root/'card/.subsonic'
        sub.mkdir(parents=True, exist_ok=True)
        for name, data in zip_files.items():
            (sub/name).write_bytes(data)
        shutil.copy(program, sub/'q2-subsonic')
        os.chmod(sub/'q2-subsonic', 0o755)
        (sub/'credentials').write_text(json.dumps({'Server': f'http://{ADDR}:{PORT}', 'User': 'seam', 'Password': 'seam'}))
        self.script = NAMESPACE.format(addr=ADDR, port=PORT, root=self.root, fake=fake, songs=songs, album=album, size=size,
                                       run=qemu or '')
        self.fake = Fake(str(self.root/'ctl.sock'))
        self.proc = None

    def start(self):
        assert self.proc is None
        (self.root/'ctl.sock').unlink(missing_ok=True)
        # a PID namespace too, whose processes all go when its first one does (--kill-child), and that one goes
        # when unshare does, which dies with this process (PR_SET_PDEATHSIG): a test that crashes leaves nothing
        self.proc = subprocess.Popen(['unshare', '--user', '--map-root-user', '--mount', '--net', '--pid', '--fork',
                                      '--kill-child', '--mount-proc', 'sh', '-c', self.script],
                                     start_new_session=True, stdout=subprocess.DEVNULL, stderr=open(self.root/'ns.err', 'wb'),
                                     preexec_fn=die_with_parent,
                                     # q2-heavy's host preload leaves a process unable to write its uid map
                                     env={k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'})

    def stop(self):
        """Ends the namespace: SIGTERM to its group (the app removes its socket and state as on the Q2), then
        SIGKILL to what is left."""
        if not self.proc:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
            self.proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=5)
        self.proc = None

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def log(self):
        try:
            return (self.root/'card/.subsonic/log.txt').read_text(errors='replace')
        except OSError:
            return ''
