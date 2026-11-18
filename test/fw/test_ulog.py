"""demo's side of the unified log (patch/ulog.c, docs/internals.md#unified-log): a line on stderr as q2guard reads it,
and a clean end's snapshot request (/tmp/q2guard.snap and a SIGUSR1, through the shell's kill, to the pid in
/tmp/q2guard.lock), waited for."""
import ctypes as C

SNAP, LOCK = '/tmp/q2guard.snap', '/tmp/q2guard.lock'


class Rig:
    """The files ulog.c opens, its stderr, the signals sent and a q2guard that answers after `answer_after` polls."""
    def __init__(self, fw, pid=b'4242\n', answer_after=3):
        self.fw, self.files, self.fds, self.err, self.kills, self.sleeps = fw, {LOCK: pid}, {}, [], [], 0
        self.answer_after, self.polls = answer_after, 0
        fw.mock('fw_time', lambda *_: 1794926102)  # 2026-11-17T14:35:02Z
        fw.mock('fw_clock_gettime', lambda clock, ts, *_: C.memmove(ts, bytes(C.c_long(77)) + bytes(C.c_long(5_000_000)),
                                                                    2 * C.sizeof(C.c_long)) and 0)
        fw.mock('fw_open', self.open)
        fw.mock('fw_write', self.write)
        fw.mock('fw_read', self.read)
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('fw_unlink', lambda path, *_: self.files.pop(C.string_at(path).decode(), None) and 0)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() in self.files else -1)
        fw.mock('fw_system', self.system)
        fw.mock('fw_usleep', self.usleep)
        self.end = fw.fn('ulog_end', None, C.c_char_p)
        self.ulog = fw.fn('ulog', None, C.c_char, C.c_char_p, C.c_char_p)

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        if not flags & 3 and path not in self.files:
            return -1
        if flags & 0x200:  # O_TRUNC
            self.files[path] = b''
        fd = 50 + len(self.fds)
        self.fds[fd] = [path, 0]
        return fd

    def write(self, fd, data, n, *_):
        data = C.string_at(data, n)
        if fd == 2:
            self.err.append(data.decode())
        else:
            self.files[self.fds[fd][0]] += data
        return n

    def read(self, fd, out, n, *_):
        path, at = self.fds[fd]
        data = self.files[path][at:at + n]
        C.memmove(out, data, len(data))
        self.fds[fd][1] += len(data)
        return len(data)

    def system(self, cmd, *_):
        cmd = C.string_at(cmd).decode()
        pid = int(cmd.split()[2])
        assert cmd == f'kill -USR1 {pid} 2>/dev/null'
        self.kills.append(pid)
        self.request = self.files.get(SNAP)
        return 0 if pid == 4242 else 256  # kill's exit 1: no such process

    def usleep(self, us, *_):
        self.sleeps += us
        self.polls += 1
        if self.answer_after is not None and self.polls >= self.answer_after:
            self.files.pop(SNAP, None)  # q2guard's answer: the request removed once its flush ended
        return 0


def test_a_line_on_stderr_is_stamped_with_time_uptime_level_and_source(fw):
    r = Rig(fw)
    r.ulog(b'W', b'bt', b'headset gone\n')
    assert r.err == ['2026-11-17T14:35:02Z 77005 W bt headset gone\n']


def test_a_power_off_asks_q2guard_for_its_snapshot_and_waits_for_the_answer(fw):
    r = Rig(fw)
    r.end(b'power-off')
    assert r.err == ['2026-11-17T14:35:02Z 77005 I ui shutdown: power-off\n']
    assert r.kills == [4242] and r.request == b'power-off'  # written before the signal
    assert SNAP not in r.files and r.sleeps == 3 * 50000  # waited for the answer, no longer
    r.end(b'restart')
    assert len(r.kills) == 1 and len(r.err) == 1  # once a run: the first clean end's


def test_with_no_q2guard_nothing_waits_and_no_request_is_left(fw):
    r = Rig(fw, pid=b'')
    r.end(b'update')
    assert r.kills == [] and SNAP not in r.files and r.sleeps == 0
    assert r.err == ['2026-11-17T14:35:02Z 77005 I ui shutdown: update\n']


def test_a_q2guard_that_never_answers_holds_the_power_off_two_seconds_at_most(fw):
    r = Rig(fw, answer_after=None)
    r.end(b'power-off')
    assert r.kills == [4242] and r.sleeps == 2_000_000 and SNAP in r.files


def test_a_dead_q2guard_s_pid_is_not_waited_for(fw):
    r = Rig(fw, pid=b'999\n')
    r.end(b'power-off')
    assert r.kills == [999] and r.sleeps == 0 and SNAP not in r.files
