"""The card across Standby, power-off and restart (patch/card_release.c, docs/internals.md#the-card-across-standby).

On the owner's Q2 (2026-11-23, five forced Standby cycles): stock's plain `umount /mnt/mmc` failed while coda-rc held
the server catalogue open, the card's driver was unloaded under the live mount, and the wake mounted the card again on
top of it: /proc/mounts listed /mnt/mmc twice, and coda-rc's descriptor pointed into the first mount. Here stock's
device_standby is a double that runs its umount where stock does (the call tools/build.py CARD_UMOUNT_CALL hands to
ringnav_card_umount), unloads the driver (whatever is still mounted stays mounted, on a card gone), suspends through
ringnav_standby and reloads the driver (one more mount). The processes and their open files are a fake /proc, and a
umount fails while any of them holds a file on the card, as the kernel's does."""
import ctypes as C

from conftest import Files

MOUNTS = '/proc/mounts'
HOLD = '/tmp/mmc_hold'
SNAP = '/tmp/q2guard.snap'
CARD_LINE = '/dev/mmcblk4p1 /mnt/mmc exfat rw,relatime,fmask=0000 0 0\n'
ROOT_LINE = '/dev/root / squashfs ro,relatime 0 0\n'
UMOUNT = 'umount /mnt/mmc'
CATALOGUE = '/mnt/mmc/.q2pod/catalogue.db'
DEMO_PID = 1110


class Q2:
    """The card's mounts, the processes that hold it, the commands demo runs and the lines it logs."""

    def __init__(self, fw, mounts=1, procs=None, coda_rc_lets_go=True, ro_ok=True):
        self.fw, self.now, self.commands, self.lines, self.order = fw, 100000, [], [], []
        self.mounts = [CARD_LINE] * mounts
        # pid: (comm, {fd: target}, cwd)
        self.procs = procs if procs is not None else {
            DEMO_PID: ('demo', {3: '/mnt/data/database.db'}, '/'),
            1249: ('coda-rc', {23: '/mnt/data/database.db', 24: CATALOGUE}, '/'),
            1077: ('q2guard', {}, '/'),
        }
        self.coda_rc_lets_go, self.ro_ok, self.readonly = coda_rc_lets_go, ro_ok, False
        self.files = Files(fw, {'/tmp/coda-rc.pid': b'1249\n', '/tmp/q2guard.lock': b'1077\n'})
        self.files.files.update({f'/proc/{pid}/comm': f'{comm}\n'.encode() for pid, (comm, _, _) in self.procs.items()})
        self.sync()
        self.dirs, self.keep = {}, []
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_usleep', lambda us, *_: setattr(self, 'now', self.now + max(1, us // 1000)) or 0)
        fw.mock('fw_system', self.system)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files.files else -1)
        fw.mock('fw_getpid', lambda *_: DEMO_PID)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d, None) and 0)
        fw.mock('fw_readlink', self.readlink)
        fw.mock('fw_write', self.write)
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', self.read)
        fw.mock('stock_device_standby_trampoline', self.stock_standby)
        fw.mock('stock_standby_trampoline', self.suspend)

    # ---- the fakes ----
    def sync(self):
        self.files.files[MOUNTS] = (ROOT_LINE + ''.join(self.mounts)).encode()

    def holders(self):
        return [pid for pid, (_, fds, cwd) in self.procs.items()
                if cwd.startswith('/mnt/mmc') or any(t.startswith('/mnt/mmc/') for t in fds.values())]

    def system(self, cmd, *_):
        cmd = C.string_at(cmd).decode()
        self.commands.append(cmd)
        if cmd == UMOUNT:
            if not self.mounts or self.holders():
                return 1 << 8
            self.mounts.pop()
        elif cmd == 'umount -l /mnt/mmc':
            if not self.mounts:
                return 1 << 8
            self.mounts.pop()
        elif cmd == 'mount -o remount,ro /mnt/mmc':
            writers = [pid for pid in self.holders() if 'w' in self.procs[pid][0]]
            if not self.ro_ok or writers:
                return 1 << 8
            self.readonly = True
        elif cmd.startswith('kill -USR1 $(cat /tmp/coda-rc.pid)') and self.coda_rc_lets_go and 1249 in self.procs:
            self.procs[1249][1].pop(24, None)  # coda_rc.c lib_release: the catalogue closed
        elif cmd.startswith('kill -USR1 1077'):
            self.files.files.pop(SNAP, None)  # q2guard answers the flush
        self.sync()
        return 0

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        if path == '/proc':
            names = [str(p) for p in self.procs] + ['self', 'mounts']
        elif path.startswith('/proc/') and path.endswith('/fd') and int(path.split('/')[2]) in self.procs:
            names = [str(fd) for fd in self.procs[int(path.split('/')[2])][1]]
        else:
            return 0
        handle = 0x7000 + len(self.dirs) + len(self.keep)
        self.dirs[handle] = names
        return handle

    def readdir(self, d, *_):
        names = self.dirs.get(d)
        if not names:
            return 0
        ent = C.create_string_buffer(268)
        name = names.pop(0).encode()
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.keep.append(ent)
        return C.addressof(ent)

    def readlink(self, path, buf, cap, *_):
        parts = C.string_at(path).decode().split('/')  # /proc/PID/cwd, /proc/PID/exe, /proc/PID/fd/N
        proc = self.procs.get(int(parts[2]))
        if not proc:
            return -1
        target = proc[2] if parts[3] == 'cwd' else '/bin/' + proc[0] if parts[3] == 'exe' else proc[1].get(int(parts[4]))
        if target is None:
            return -1
        data = target.encode()[:cap]
        C.memmove(buf, data, len(data))
        return len(data)

    def write(self, fd, data, n, *_):
        if fd == 2:
            self.lines.append(C.string_at(data, n).decode())
        return n

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        if path == SNAP:
            self.files.files[SNAP] = b'flush'
            return 9
        if path == '/tmp/q2guard.lock':
            return 10  # read below: q2guard's pid
        return -1

    def read(self, fd, buf, n, *_):
        data = b'1077\n'[:n] if fd == 10 else b''
        C.memmove(buf, data, len(data))
        return len(data)

    # ---- stock's device_standby, as it runs ----
    def stock_standby(self, *_):
        self.order.append(('umount at', list(self.holders()), len(self.mounts), dict(self.files.files)))
        umount = getattr(self.fw.lib, 'ringnav_card_umount', None)
        if umount:  # the call the build hands to card_release.c
            self.fw.fn('ringnav_card_umount', C.c_int, C.c_char_p)(UMOUNT.encode())
        else:  # stock's own system("umount /mnt/mmc")
            self.system(C.c_char_p(UMOUNT.encode()))
        self.order.append(('driver unloaded', len(self.mounts), list(self.holders())))
        self.fw.fn('ringnav_standby', C.c_int)()
        self.mounts.append(CARD_LINE)  # the driver back: mdev mounts the card again, on whatever is left
        self.sync()
        return 1

    def suspend(self, *_):
        self.order.append(('suspend', len(self.mounts), dict(self.files.files)))
        self.now += 60000
        return 1

    def standby(self):
        assert self.fw.fn('ringnav_device_standby', C.c_int)() == 1

    def poll(self, ms=100):
        self.now += ms
        self.fw.fn('standby_usb_poll', None)()

    def logged(self, word):
        return [line for line in self.lines if word in line]


def test_the_catalogue_coda_rc_holds_is_let_go_before_the_umount_and_the_card_is_mounted_once_after(fw):
    """The owner's Q2: coda-rc's library holds the catalogue. It is asked to close it, the umount waits for that, the
    card is off before the driver goes and the suspend, and after the wake it is mounted once, the hold gone and
    q2guard asked to write out its lines. Red before card_release.c: the umount met coda-rc's descriptor and the wake
    stacked a second mount."""
    q = Q2(fw)
    q.standby()
    unloaded = next(o for o in q.order if o[0] == 'driver unloaded')
    assert unloaded[1:] == (0, []), 'the card off, nothing holding it, before its driver goes'
    suspend = next(o for o in q.order if o[0] == 'suspend')
    assert suspend[1] == 0
    assert HOLD in suspend[2] and suspend[2][HOLD] == b'%d\n' % DEMO_PID, 'q2guard and coda-rc told to keep off'
    kill = q.commands.index('kill -USR1 $(cat /tmp/coda-rc.pid) 2>/dev/null')
    assert kill < q.commands.index('sync') < q.commands.index(UMOUNT), q.commands
    assert q.commands.count(UMOUNT) == 1, 'it came off at the first try'
    assert q.logged('card: unmounted (1 mount) for standby')
    q.poll()
    assert q.files.files[MOUNTS].decode().count(' /mnt/mmc ') == 1
    assert HOLD not in q.files.files
    assert q.logged('card: back, mounted once')
    assert 'kill -USR1 1077 2>/dev/null' in q.commands, 'q2guard asked for the flush'


def test_a_holder_that_never_lets_go_is_named_and_the_card_goes_read_only_then_detached(fw):
    """coda-rc not answering: 3 s for the holders, three umounts with the holder named each time, then the last resort:
    remounted read-only, synced and detached, an E line saying so. Nothing is mounted at the suspend, so the wake
    mounts it once."""
    q = Q2(fw, coda_rc_lets_go=False)
    start = q.now
    q.standby()
    assert q.commands.count(UMOUNT) == 3
    assert q.logged('card: still held after 3 s for standby: 1249 coda-rc ' + CATALOGUE)
    assert len(q.logged('umount failed')) == 3 and all('1249 coda-rc' in line for line in q.logged('umount failed'))
    ro = q.commands.index('mount -o remount,ro /mnt/mmc')
    assert q.commands[ro + 1] == 'sync' and q.commands[ro + 2] == 'umount -l /mnt/mmc'
    assert q.readonly
    line = q.logged('card: would not unmount')[0]
    assert ' E ui ' in line and 'remounted read-only, synced and detached' in line and '1249 coda-rc' in line
    assert next(o for o in q.order if o[0] == 'suspend')[1] == 0
    assert q.order[0][0] == 'umount at'
    q.poll()
    assert q.logged('card: back, mounted once')
    assert 3000 <= q.now - start - 60000 - 100 < 6000


def test_a_writer_that_never_lets_go_is_detached_read_write_and_said_so(fw):
    """A file open for writing (here a process whose name says so): the read-only remount refused, the card detached
    anyway so the wake's mount is the only one, and the E line says it was read-write."""
    procs = {DEMO_PID: ('demo', {}, '/'), 4242: ('writer', {5: '/mnt/mmc/.subsonic/log.txt'}, '/')}
    q = Q2(fw, procs=procs)
    q.standby()
    assert not q.readonly
    assert q.logged('would not unmount or go read-only: synced and detached read-write for standby: 4242 writer')
    q.poll()
    assert q.files.files[MOUNTS].decode().count(' /mnt/mmc ') == 1


def test_a_stale_mount_under_the_card_comes_off_too(fw):
    """A Q2 that slept on the old firmware: the card mounted twice already. Both come off, so the wake leaves one."""
    q = Q2(fw, mounts=2)
    q.standby()
    assert q.commands.count(UMOUNT) == 2 and q.logged('card: unmounted (2 mounts) for standby')
    q.poll()
    assert q.files.files[MOUNTS].decode().count(' /mnt/mmc ') == 1


def test_a_working_folder_on_the_card_holds_it_and_is_waited_for(fw):
    """A shell whose working folder is on the card (a card app's launcher before it moved to /tmp) holds it as a file
    does: named in the log, waited for, then the last resort."""
    procs = {DEMO_PID: ('demo', {}, '/'), 900: ('sh', {}, '/mnt/mmc/.subsonic')}
    q = Q2(fw, procs=procs)
    q.standby()
    assert q.logged('still held after 3 s for standby: 900 sh /mnt/mmc/.subsonic')


def test_no_card_nothing_is_done(fw):
    q = Q2(fw, mounts=0)
    q.standby()
    assert UMOUNT not in q.commands and 'sync' not in q.commands
    assert HOLD not in next(o for o in q.order if o[0] == 'suspend')[2]


def test_a_card_not_back_after_the_wake_ends_the_hold_after_3_s(fw):
    q = Q2(fw)
    fw.mock('stock_device_standby_trampoline', lambda *_: (fw.fn('ringnav_card_umount', C.c_int, C.c_char_p)(
        UMOUNT.encode()), fw.fn('ringnav_standby', C.c_int)(), 1)[-1])
    q.standby()
    q.poll(1000)
    assert HOLD in q.files.files, 'waiting for the card'
    q.poll(2100)
    assert HOLD not in q.files.files and q.logged('card: not back after the wake')


def test_power_off_and_restart_unmount_the_card_before_the_command(fw):
    """device_poweroff's system("poweroff") and device_reboot's system("reboot"): the card let go of and off first."""
    for cmd in ('poweroff', 'reboot'):
        q = Q2(fw)
        assert fw.fn('ringnav_card_down', C.c_int, C.c_char_p)(cmd.encode()) == 0
        assert q.commands[-1] == cmd
        assert q.commands.index(UMOUNT) < q.commands.index(cmd) and not q.mounts
        assert q.logged(f'card: unmounted (1 mount) for {cmd}')
