"""A MIPS bluetoothd's functions under Unicorn, one call at a time (test/bluez_port.py): the binary loaded at a load
address of its own with its relocations and GOT as ld.so leaves them, every imported function a stand-in Python may
answer (a few of libc's and pthread's answered here: malloc and friends, the mutexes), libbtctl's objects in memory
the tests set, and any function, static ones too, called by its symbol."""
import struct
import subprocess

from unicorn import UC_ARCH_MIPS, UC_HOOK_CODE, UC_MODE_LITTLE_ENDIAN, UC_MODE_MIPS32, Uc
from unicorn.mips_const import (UC_MIPS_REG_2, UC_MIPS_REG_4, UC_MIPS_REG_5, UC_MIPS_REG_6, UC_MIPS_REG_7,
                                UC_MIPS_REG_25, UC_MIPS_REG_29, UC_MIPS_REG_31, UC_MIPS_REG_PC)

BIAS = 0x10000000
STUBS, HEAP, DATA, STACK = 0x40000000, 0x41000000, 0x42000000, 0x43000000
RET = STUBS - 0x1000


def readelf(path, *options):
    return subprocess.check_output(['readelf', '-W', *options, str(path)]).decode()


class Bluetoothd:
    def __init__(self, path, imports=None):
        self.path, self.raw = path, open(path, 'rb').read()
        self.u = u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        dyn = readelf(path, '-h').split('Type:')[1].split()[0] == 'DYN'
        self.bias = BIAS if dyn else 0
        phoff = struct.unpack_from('<I', self.raw, 28)[0]
        for i in range(struct.unpack_from('<H', self.raw, 44)[0]):
            t, o, v, _, f, m, _, _ = struct.unpack_from('<8I', self.raw, phoff + 32 * i)
            if t != 1:
                continue
            v += self.bias
            start = v & ~0xfff
            u.mem_map(start, ((v + m + 0xfff) & ~0xfff) - start)
            u.mem_write(v, self.raw[o:o + f])
        for region in (STUBS - 0x1000, HEAP, DATA, STACK):
            u.mem_map(region, 0x100000)
        u.mem_write(RET, struct.pack('<2I', 0x03e00008, 0))
        self.sym, self.all = {}, []
        for line in readelf(path, '-s').splitlines():
            f = line.split()
            if len(f) >= 8 and f[0].endswith(':') and f[0][:-1].isdigit() and f[6] != 'UND':
                name, address = f[7].split('@')[0], int(f[1], 16) + self.bias
                self.sym.setdefault(name, address)
                self.all.append((name, address, int(f[2], 0)))
        self.imports, self.stub_of, self.objects = {}, {}, {}
        self.answers = dict(imports or {})
        self.heap = HEAP
        self.calls = []
        # libbtctl's objects (docs/bluetoothd.md): zeroed memory the tests fill
        at = DATA
        for name, size in (('g_track_meta', 4), ('g_play_status', 4), ('g_notify_mutex', 24), ('g_avrcp_event_fun', 4),
                           ('g_high_priority_lcodec', 1), ('g_synclink_mode', 1)):
            self.objects[name] = at
            at += 32
        self._got()
        self._relocate()
        u.hook_add(UC_HOOK_CODE, self._hook, begin=STUBS, end=STUBS + 0xfffff)
        for name in ('btd_debug', 'btd_error', 'btd_warn', 'btd_info', 'bt_log_vprintf', 'bt_log_printf'):
            if name in self.sym:  # logging: nothing to see here
                self.answer(name, lambda *_: 0)

    def _stub(self, name):
        if name not in self.stub_of:
            a = STUBS + 8 * len(self.stub_of)
            self.u.mem_write(a, struct.pack('<2I', 0x03e00008, 0x00001025))  # jr $ra; move $v0, $zero
            self.stub_of[name], self.imports[a] = a, name
        return self.stub_of[name]

    def _got(self):
        gotinfo = readelf(self.path, '-A')
        self.gp = int(gotinfo.split('Canonical gp value:')[1].split()[0], 16) + self.bias
        local = True
        for line in gotinfo.splitlines():
            if 'Global entries' in line:
                local = False
            f = line.split()
            if len(f) < 3 or not f[1].endswith('(gp)'):
                continue
            slot, value = self.gp + int(f[1][:-4]), int(f[2], 16)
            if local:
                if value:
                    self.word(slot, value + self.bias)
                continue
            name = f[-1]
            if 'UND' in f:
                self.word(slot, self.objects[name] if name in self.objects else self._stub(name))
            else:
                self.word(slot, value + self.bias)

    def _relocate(self):
        for line in readelf(self.path, '-r').splitlines():
            f = line.split()
            if len(f) >= 3 and f[2] == 'R_MIPS_REL32':
                at = int(f[0], 16) + self.bias
                name = f[4] if len(f) >= 5 else None
                if name and name in self.objects:
                    self.word(at, self.read(at) + self.objects[name])
                elif name and len(f) >= 5 and f[3] != '00000000':
                    self.word(at, self.read(at) + int(f[3], 16) + self.bias)
                else:
                    self.word(at, self.read(at) + self.bias)

    def _hook(self, u, address, size, _):
        name = self.imports.get(address)
        if name is None:
            return
        args = [u.reg_read(r) for r in (UC_MIPS_REG_4, UC_MIPS_REG_5, UC_MIPS_REG_6, UC_MIPS_REG_7)]
        self.calls.append((name, args))
        answer = self.answers.get(name) or getattr(self, '_' + name, None)
        if answer:
            u.reg_write(UC_MIPS_REG_2, (answer(*args) or 0) & 0xffffffff)
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_31))

    # libc's allocator: a bump heap
    def _malloc(self, n, *_):
        a = self.heap
        self.heap += (n + 15) & ~15
        return a
    def _calloc(self, n, m, *_):
        a = self._malloc(n * m)
        self.u.mem_write(a, bytes(n * m))
        return a
    _g_malloc, _g_malloc0 = _malloc, lambda self, n, *_: self._calloc(n, 1)
    def _free(self, *_): return 0
    _g_free = _free

    # libc's memory and string functions, as libc does them
    def _memset(self, d, c, n, *_):
        self.u.mem_write(d, bytes([c & 0xff]) * n)
        return d
    def _memcpy(self, d, s, n, *_):
        self.u.mem_write(d, bytes(self.u.mem_read(s, n)))
        return d
    _memmove = _memcpy
    def _strlen(self, s, *_):
        n = 0
        while self.u.mem_read(s + n, 1) != b'\0':
            n += 1
        return n
    def cstr(self, a):
        return bytes(self.u.mem_read(a, self._strlen(a))) if a else b''

    # GLib's GString {str, len, allocated_len} and the string helpers BlueZ calls
    def _g_string_new(self, init, *_):
        text = self.cstr(init)
        buf = self._calloc(4096, 1)
        self.u.mem_write(buf, text + b'\0')
        return self.alloc(struct.pack('<3I', buf, len(text), 4096))
    def _g_string_truncate(self, gs, n, *_):
        buf = self.read(gs)
        self.word(gs + 4, n)
        self.u.mem_write(buf + n, b'\0')
        return gs
    def _g_string_append_len(self, gs, val, n, *_):
        buf, ln = self.read(gs), self.read(gs + 4)
        self.u.mem_write(buf + ln, bytes(self.u.mem_read(val, n)) + b'\0')
        self.word(gs + 4, ln + n)
        return gs
    def _g_strndup(self, s, n, *_):
        text = bytes(self.u.mem_read(s, n)).split(b'\0')[0]
        return self.alloc(text + b'\0')
    def _strtol(self, s, end, base, *_):
        text = self.cstr(s).strip()
        digits = len(text) - len(text.lstrip(b'-0123456789'))
        return int(text[:digits] or b'0', base or 10)

    def _sprintf(self, out, fmt, *args):  # "%d", as the attributes' numbers use; any other format writes ""
        text = str(struct.unpack('<i', struct.pack('<I', args[0]))[0]).encode() if self.cstr(fmt) == b'%d' else b''
        self.u.mem_write(out, text + b'\0')
        return len(text)

    def _memcmp(self, a, c, n, *_):
        x, y = bytes(self.u.mem_read(a, n)), bytes(self.u.mem_read(c, n))
        return 0 if x == y else (1 if x > y else -1)

    def answer(self, name, fn):
        """bluetoothd's own function name answered by fn(a0..a3) in place of its code."""
        address = self.sym[name]
        def hook(u, at, size, _):
            sp = u.reg_read(UC_MIPS_REG_29)
            args = [u.reg_read(r) for r in (UC_MIPS_REG_4, UC_MIPS_REG_5, UC_MIPS_REG_6, UC_MIPS_REG_7)]
            args += [self.read(sp + 16 + 4 * i) for i in range(3)]  # o32's fifth on, in the caller's frame
            self.calls.append((name, args))
            u.reg_write(UC_MIPS_REG_2, (fn(*args) or 0) & 0xffffffff)
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_31))
        self.u.hook_add(UC_HOOK_CODE, hook, begin=address, end=address)

    def word(self, a, v): self.u.mem_write(a, struct.pack('<I', v & 0xffffffff))
    def read(self, a): return struct.unpack('<I', self.u.mem_read(a, 4))[0]
    def byte(self, a, v): self.u.mem_write(a, bytes([v]))
    def alloc(self, data): a = self._malloc(len(data)); self.u.mem_write(a, data); return a

    def functions(self, name):
        """Every symbol called name (static ones in several files): (name, address, size)."""
        return [x for x in self.all if x[0] == name]

    def call(self, name, *args):
        """name(args): its return value."""
        return self.call_at(self.sym[name], *args)

    def call_at(self, address, *args):
        u = self.u
        for r, v in zip((UC_MIPS_REG_4, UC_MIPS_REG_5, UC_MIPS_REG_6, UC_MIPS_REG_7), args):
            u.reg_write(r, v & 0xffffffff)
        sp = STACK + 0xff000
        for i, v in enumerate(args[4:]):  # o32: the fifth on in the caller's frame, past the four slots
            self.word(sp + 16 + 4 * i, v)
        u.reg_write(UC_MIPS_REG_29, sp)
        u.reg_write(UC_MIPS_REG_31, RET)
        u.reg_write(UC_MIPS_REG_25, address)
        self.calls = []
        u.emu_start(address, RET, count=1000000)
        assert u.reg_read(UC_MIPS_REG_PC) == RET, f'{address:#x} did not return'
        return u.reg_read(UC_MIPS_REG_2)
