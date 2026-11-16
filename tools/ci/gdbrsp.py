"""A minimal GDB remote serial protocol client for qemu-user's gdbstub (QEMU_GDB=port): stop, continue,
read and write memory, list threads and read their registers. Enough for tools/ci/q2live.py's hot swap."""
import socket
import time


class Rsp:
    def __init__(self, port, host='127.0.0.1', timeout=10.0):
        self.s = socket.create_connection((host, port), timeout=timeout)
        self.buf = b''
        self.noack = False
        self.s.sendall(b'+')
        self.noack = self.cmd('QStartNoAckMode') == b'OK'

    def _read_packet(self, timeout=None):
        self.s.settimeout(timeout)
        while True:
            start = self.buf.find(b'$')
            end = self.buf.find(b'#', start + 1) if start >= 0 else -1
            if start >= 0 and end >= 0 and len(self.buf) >= end + 3:
                body = self.buf[start + 1:end]
                self.buf = self.buf[end + 3:]
                if not self.noack:
                    self.s.sendall(b'+')
                return body
            chunk = self.s.recv(65536)
            if not chunk:
                raise ConnectionError('gdbstub closed the connection')
            self.buf += chunk

    def send(self, body):
        data = body.encode() if isinstance(body, str) else body
        self.s.sendall(b'$%s#%02x' % (data, sum(data) & 0xff))

    def cmd(self, body, timeout=10.0):
        self.send(body)
        reply = self._read_packet(timeout)
        while reply.startswith(b'O') and len(reply) > 1 and reply != b'OK':  # console output
            reply = self._read_packet(timeout)
        return reply

    def cont(self):
        """Resume every thread; the stop reply comes later (interrupt() reads it)."""
        self.send('c')

    def interrupt(self, timeout=10.0):
        self.s.sendall(b'\x03')
        return self._read_packet(timeout)

    def wait_stop(self, timeout=None):
        return self._read_packet(timeout)

    def read(self, addr, n):
        out = b''
        while len(out) < n:
            k = min(0x800, n - len(out))
            r = self.cmd('m%x,%x' % (addr + len(out), k))
            if r.startswith(b'E'):
                raise IOError(f'read {addr + len(out):#x}: {r!r}')
            out += bytes.fromhex(r.decode())
        return out

    def write(self, addr, data):
        for i in range(0, len(data), 0x800):
            part = data[i:i + 0x800]
            r = self.cmd(b'M%x,%x:' % (addr + i, len(part)) + part.hex().encode())
            if r != b'OK':
                raise IOError(f'write {addr + i:#x}: {r!r}')

    def threads(self):
        out, r = [], self.cmd('qfThreadInfo')
        while r.startswith(b'm'):
            out += [t.decode() for t in r[1:].split(b',')]
            r = self.cmd('qsThreadInfo')
        return out

    def regs(self, thread):
        """MIPS o32 register file of a thread as 32-bit little-endian words (gdb's order: r0-r31, sr, lo, hi,
        bad, cause, pc, ...)."""
        assert self.cmd('Hg' + thread) == b'OK'
        raw = bytes.fromhex(self.cmd('g').decode())
        return [int.from_bytes(raw[i:i + 4], 'little') for i in range(0, len(raw), 4)]

    def close(self):
        try:
            self.s.close()
        except OSError:
            pass


def connect(port, timeout=30.0):
    """An Rsp on port, retried until qemu listens. Never probe the port first: qemu-user's gdbstub accepts
    one connection only, and a probe would take it."""
    end = time.monotonic() + timeout
    while True:
        try:
            return Rsp(port)
        except ConnectionRefusedError:
            if time.monotonic() > end:
                raise
            time.sleep(0.1)


def demo_pid(copy):
    """The pid of demo under this emulator copy's sandbox, or None: found by walking each demo's parents up to
    the copy's own bwrap, as q2emu does, never by name alone (every copy's demo has the same command line)."""
    import subprocess
    sandbox = {int(p) for p in subprocess.run(['pgrep', '-f', f'^bwrap --die-with-parent .*--bind {copy}/rootfs / '],
                                               capture_output=True, text=True).stdout.split()}
    for d in subprocess.run(['pgrep', '-f', '^/usr/libexec/qemu-binfmt/mipsel-binfmt-P /release/bin/demo'],
                            capture_output=True, text=True).stdout.split():
        p = int(d)
        while p > 1:
            if p in sandbox:
                return int(d)
            try:
                p = int(open(f'/proc/{p}/stat').read().rsplit(')', 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
    return None
