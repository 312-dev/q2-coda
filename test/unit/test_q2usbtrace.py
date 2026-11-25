"""q2-usbtrace (patch/q2usbtrace.c, q2usbtrace_core.c, docs/internals.md#usb-tracer) run on this machine over a
simulated Q2 (test/unit/q2usbtrace_host.c records each device call it makes and answers it): that it only reads
(/dev/mem O_RDONLY and mapped read-only, GPIO lines asked for only as input event requests, never PD17, the PMIC only
read), what its trace and its unified log lines say for a plug, a DAC and a disagreement with stock, its idle cost,
the card's rotation, the flag that stops it, a lost interrupt and a Standby."""
import ctypes as C
import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools'))
import usbtrace_report as R  # noqa: E402

HEADER = (ROOT / 'patch/q2usbtrace.h').read_text()


def define(name):
    return int(HEADER.split(f'#define {name} ', 1)[1].split()[0].rstrip('u'), 0)


GPIO_PAGE, CPM_PAGE = define('UT_GPIO_PAGE'), define('UT_CPM_PAGE')
LINEEVENT, SMBUS, SLAVE_FORCE = define('UT_GPIO_GET_LINEEVENT'), define('UT_I2C_SMBUS'), define('UT_I2C_SLAVE_FORCE')
O_SYNC = define('UT_O_SYNC')
FILE_MAX = define('UT_FILE_MAX')
S, MS = 1_000_000_000, 1_000_000
ONLINE = '/sys/class/power_supply/usb/online'
OTG = '/sys/bus/platform/devices/13500000.otg/'
HOSTDEV = '/sys/bus/usb/devices/1-1/idVendor'
UDC = '/sys/class/udc/13500000.otg/state'
REGDUMP = '/sys/kernel/debug/usb/13500000.otg/regdump'
STOP = '/mnt/data/q2-usbtrace.off'


class Call(C.Structure):
    _fields_ = [('what', C.c_char * 12), ('path', C.c_char * 96), ('a', C.c_long), ('b', C.c_long), ('c', C.c_long)]


class PollFd(C.Structure):
    _fields_ = [('fd', C.c_int), ('events', C.c_short), ('revents', C.c_short)]


class Edge(C.Structure):
    _fields_ = [('timestamp', C.c_ulonglong), ('id', C.c_uint), ('pad', C.c_uint)]


FILE = C.CFUNCTYPE(C.c_int, C.c_char_p, C.c_void_p, C.c_int)
POLL = C.CFUNCTYPE(C.c_int, C.POINTER(PollFd), C.c_int, C.c_int)
LINE = C.CFUNCTYPE(C.c_int, C.c_int, C.POINTER(Edge))
RECV = C.CFUNCTYPE(C.c_int, C.c_int, C.c_void_p, C.c_int)
FD_LINE, FD_UDC, FD_EDGES, FD_UEV = 20, 30, 40, 41


@pytest.fixture
def L(tmp_path):
    out = tmp_path / 'ut.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2USBTRACE_HOST', '-DQ2GUARD_HOST', '-Wall', '-Wextra',
                    '-Werror', '-Wno-unused-parameter', f'-DPEQ_ROOT="{tmp_path}/root"',
                    *(ROOT / f'patch/{n}.c' for n in ('q2usbtrace', 'q2usbtrace_core', 'usb_fsm', 'card_store')),
                    ROOT / 'test/unit/q2usbtrace_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.root = tmp_path / 'root'  # type: ignore[attr-defined]  # where PEQ_ROOT put the card
    (lib.root / 'mnt/mmc').mkdir(parents=True)  # the card's mount point
    lib.q2usbtrace_main.argtypes = [C.c_int, C.c_void_p]
    return lib


class Q2:
    """The simulated port: pins, files and queues, and a script of (time, change) the program's poll() reaches."""

    def __init__(self, L, end_s=120):
        self.L = L
        self.now = C.c_longlong.in_dll(L, 'ut_now')
        self.now.value = 1000 * S
        self.gpio = (C.c_uint * 1024).in_dll(L, 'ut_gpio')
        self.cpm = (C.c_uint * 1024).in_dll(L, 'ut_cpm')
        self.cpm[0x3c // 4], self.cpm[0x40 // 4] = 0xE09919B8, 0x03800096
        axp = (C.c_int * 256).in_dll(L, 'ut_axp')
        for r in range(256):
            axp[r] = -1
        for r, v in ((0x00, 0x38), (0x40, 0), (0x41, 3), (0x42, 0), (0x48, 0), (0x49, 0x8b), (0x4a, 8)):
            axp[r] = v
        self.axp = axp
        self.pins = {17: 1, 18: 1, 19: 1, 'PC20': 0, 'PE22': 0}
        self.online, self.udc, self.dev, self.snd = 0, 'not_attached', 0, 0
        self.udc_changed = False
        self.forward = True  # demo's --dev build sends PD17's edges
        self.lines: dict[int, list] = {18: [], 19: []}
        self.fwd: list = []
        self.uev: list = []
        self.script: list = []
        self.end = self.now.value + end_s * S
        self.stop = False
        self.debugfs_ready = False
        self._cbs = [FILE(self.file), POLL(self.poll), LINE(self.line), RECV(self.recv)]
        for name, cb in zip(('py_file', 'py_poll', 'py_line', 'py_recv'), self._cbs):
            C.c_void_p.in_dll(L, name).value = C.cast(cb, C.c_void_p).value
        self.set_pins()

    # ---- the device ----
    def set_pins(self):
        d = sum(1 << b for b in (17, 18, 19) if self.pins[b])
        self.gpio[0x300 // 4] = d
        self.gpio[0x200 // 4] = self.pins['PC20'] << 20
        self.gpio[0x400 // 4] = self.pins['PE22'] << 22

    def requested(self, line):
        return self.gpio[(0x300 + 0x10) // 4] >> line & 1

    def file(self, path, buf, n):
        p = path.decode()
        text = {ONLINE: f'{self.online}\n', OTG + 'id_status': 'device\n', OTG + 'dwc2_mode': 'otg\n',
                OTG + 'connected': 'connect\n', UDC: self.udc + '\n',
                '/proc/asound/cards': ' 0 [ecodec         ]: x - x\n' + (' 1 [Device         ]: USB-Audio - USB\n' if self.snd else '')}.get(p)
        if p == HOSTDEV and self.dev:
            text = '0a12\n'
        if p == REGDUMP and C.c_int.in_dll(self.L, 'ut_debugfs').value:
            text = 'GOTGCTL = 0x000d00fc\nGOTGINT = 0x00000000\nGINTSTS = 0x04008028\nDSTS = 0x0024f900\nHPRT0 = 0x00000000\n'
        if p == STOP and self.stop:
            text = ''
        if text is None:
            return -1
        data = text.encode()[:n]
        C.memmove(buf, data, len(data))
        return len(data)

    def ready(self):
        r = {}
        for line in (18, 19):
            if self.lines[line]:
                r[FD_LINE + line - 17] = 1
        if self.fwd:
            r[FD_EDGES] = 1
        if self.uev:
            r[FD_UEV] = 1
        if self.udc_changed:
            r[FD_UDC] = 2
        return r

    def poll(self, p, n, timeout):
        target = self.now.value + timeout * MS
        while True:
            ready = self.ready()
            if ready:
                k = 0
                for i in range(n):
                    p[i].revents = ready.get(p[i].fd, 0) & p[i].events
                    if p[i].fd == FD_UDC and p[i].revents:
                        self.udc_changed = False
                    k += bool(p[i].revents)
                if k:
                    return k
            if self.script and self.script[0][0] <= target:
                t, fn = self.script.pop(0)
                self.now.value = max(self.now.value, t)
                fn()
                continue
            self.now.value = target
            if not self.script and self.now.value >= self.end:
                self.stop = True
            return 0

    def line(self, line, e):
        if not self.lines.get(line):
            return 0
        ts, level = self.lines[line].pop(0)
        e[0].timestamp, e[0].id = ts, 1 if level else 2
        return 1

    def recv(self, which, buf, n):
        q = self.uev if which else self.fwd
        if not q:
            return -1
        data = q.pop(0)
        if not which:
            ts, level = data
            data = bytes(Edge(ts, 1 if level else 2, 0))
        C.memmove(buf, data, len(data))
        return len(data)

    # ---- a scenario ----
    def at(self, s, fn):
        self.script.append((1000 * S + int(s * S), fn))
        self.script.sort(key=lambda x: x[0])
        return self

    def edge(self, s, pin, level):
        def go():
            self.pins[pin] = level
            self.set_pins()
            if pin == 17 and self.forward:
                self.fwd.append((self.now.value, level))
            elif pin in (18, 19) and self.requested(pin):
                self.lines[pin].append((self.now.value, level))
        return self.at(s, go)

    def set(self, s, **kv):
        def go():
            for k, v in kv.items():
                if k in ('PC20', 'PE22'):
                    self.pins[k] = v
                    self.set_pins()
                else:
                    setattr(self, k, v)
        return self.at(s, go)

    def uevent(self, s, action, devpath, subsystem, devtype='', devname=''):
        fields = [f'{action}@{devpath}', f'ACTION={action}', f'DEVPATH={devpath}', f'SUBSYSTEM={subsystem}']
        fields += [f'DEVTYPE={devtype}'] * bool(devtype) + [f'DEVNAME={devname}'] * bool(devname)
        return self.at(s, lambda: self.uev.append('\0'.join(fields).encode() + b'\0'))

    def udc_state(self, s, state):
        def go():
            self.udc, self.udc_changed = state, True
        return self.at(s, go)

    def run(self):
        assert self.L.q2usbtrace_main(0, None) == 0
        return self

    # ---- what it did ----
    def calls(self):
        n = C.c_int.in_dll(self.L, 'ut_ncalls').value
        assert n < C.c_int.in_dll(self.L, 'ut_calls_cap').value, 'the record of calls filled up'
        arr = (Call * n).in_dll(self.L, 'ut_calls')
        return [(c.what.decode(), c.path.decode(), c.a, c.b, c.c) for c in arr[:n]]

    def trace(self):
        logs = self.L.root / 'mnt/mmc/.q2muse/logs'
        return ''.join((logs / f).read_text() for f in ('q2-usbtrace.1', 'q2-usbtrace.0') if (logs / f).exists())

    def ulog(self):
        return C.string_at(C.addressof(C.c_char.in_dll(self.L, 'ut_ulogbuf'))).decode()


def dac(q, t=5.0):
    """USB-C headphones on an OTG adapter, nothing unplugged lately: ID low, stock's power 3 s on (its settle), the
    kernel's device 0.56 s after, the PCM 0.82 s after; out 20 s later."""
    q.edge(t, 18, 0)
    q.set(t + 3.0, PC20=1, PE22=1).edge(t + 3.0, 17, 0)
    q.set(t + 3.56, dev=1)  # sysfs first, then the uevent, as the kernel does
    q.uevent(t + 3.56, 'add', '/devices/platform/ahb2/13500000.otg/usb1/1-1', 'usb', 'usb_device', 'bus/usb/001/002')
    q.uevent(t + 3.82, 'add', '/devices/platform/ahb2/13500000.otg/usb1/1-1/1-1:1.0/sound/card1', 'sound')
    q.uevent(t + 3.83, 'add', '/devices/platform/ahb2/13500000.otg/usb1/1-1/1-1:1.0/sound/card1/pcmC1D0p', 'sound',
             devname='snd/pcmC1D0p')
    q.set(t + 3.83, snd=1)
    q.set(t + 23.0, dev=0, snd=0)
    q.uevent(t + 23.0, 'remove', '/devices/platform/ahb2/13500000.otg/usb1/1-1', 'usb', 'usb_device')
    q.edge(t + 23.05, 18, 1)
    q.set(t + 23.6, PC20=0, PE22=0).edge(t + 25.2, 17, 1)
    return q


# ---- it only reads ----

DEVICE = re.compile(r'^/(dev|sys|proc)/')


def test_it_opens_every_device_file_read_only_and_asks_only_input_events_and_smbus_reads(L):
    q = dac(Q2(L)).run()
    calls = q.calls()
    opens = [(p, a) for w, p, a, _, _ in calls if w == 'open']
    assert opens and all(a & 3 == 0 for p, a in opens if DEVICE.match(p)), [o for o in opens if o[1] & 3]
    assert ('/dev/mem', O_SYNC) in opens
    maps = [(a, c) for w, _, a, _, c in calls if w == 'mmap']
    assert sorted(maps) == sorted([(4096, CPM_PAGE), (4096, GPIO_PAGE)])
    lines = [(b, c) for w, p, a, b, c in calls if w == 'ioctl' and p == 'line']
    assert lines and all(a == LINEEVENT for w, p, a, _, _ in calls if w == 'ioctl' and p == 'line')
    assert {b for b, _ in lines} == {18, 19}, 'PD17 is demo\'s: never asked for'
    assert all(c == (1 << 8 | 3) for _, c in lines), 'input, both edges: nothing else'
    reads = [(b, c) for w, p, a, b, c in calls if w == 'ioctl' and p == 'smbus']
    assert reads and all(c == (1 << 8 | 2) for _, c in reads), 'SMBus byte reads only'
    assert {b for b, _ in reads} == {0x00, 0x40, 0x41, 0x42, 0x48, 0x49, 0x4a}
    others = [(p, a) for w, p, a, _, _ in calls if w == 'ioctl' and p == 'other']
    assert others == [('other', SLAVE_FORCE)]
    assert {p for w, p, *_ in calls if w == 'append'} == {'/tmp/q2-demo.log'}
    assert {w for w, *_ in calls} <= {'open', 'close', 'mmap', 'ioctl', 'socket', 'bind', 'poll', 'append', 'mount'}


def test_its_device_calls_cannot_write_to_a_device():
    """The device's own t_* definitions (the part the host test replaces): the mapping is PROT_READ, open passes the
    caller's flags, write() is reached only from t_append (demo's console log, no O_CREAT), and nothing in the
    program can ask for a line handle (which could drive a pin), a writable mapping or an SMBus write."""
    src = (ROOT / 'patch/q2usbtrace.c').read_text()
    device = src[src.index('#ifndef Q2USBTRACE_HOST\n/* ---- the device'):]
    assert 'mmap(0, len, UT_PROT_READ, UT_MAP_SHARED' in device and len(re.findall(r'\bmmap\(', device)) == 1
    body = re.sub(r'int t_append\(.*?\n}\n', '', device, flags=re.S)
    assert 'write(' not in body and 'O_WRONLY' not in body and 'O_RDWR' not in body
    found = re.search(r'int t_append\(.*?\n}\n', device, re.S)
    assert found
    t_append = found.group()
    assert re.findall(r'open\(path, ([^)]*)\)', t_append) == ['O_WRONLY | O_APPEND | O_CLOEXEC']
    every = ''.join((ROOT / f'patch/{n}').read_text() for n in ('q2usbtrace.c', 'q2usbtrace_core.c', 'q2usbtrace.h'))
    every = re.sub(r'/\*.*?\*/', '', every, flags=re.S)  # the code, not what its comments say it never does
    for banned in ('LINEHANDLE', 'PROT_WRITE', 'O_RDWR', 'I2C_SMBUS_WRITE', 'OUTPUT', 'I2C_RDWR'):
        assert banned not in every, banned
    assert 'UT_GPIOHANDLE_REQUEST_INPUT 1u' in HEADER and 'UT_I2C_SMBUS_READ 1' in HEADER


# ---- what it writes ----

def test_a_dac_s_cycle_has_its_edges_uevents_stock_s_power_and_the_shadow_s_grant(L):
    q = dac(Q2(L)).run()
    lines = R.parse(q.trace().splitlines())
    kinds = [ln.kind for ln in lines]
    assert kinds[0] == 'start' and 'pd17=forwarded pd18=edges pd19=edges mem=ok i2c=ok regdump=ok' in lines[0].rest
    assert any(ln.kind == 'e' and ln.rest == 'PD18 0' for ln in lines)
    assert any(ln.kind == 'e' and ln.rest == 'PD17 0 fwd' for ln in lines)
    assert [ln.rest for ln in lines if ln.kind == 'stock'] == ['otg-on', 'otg-off']
    assert any(ln.kind == 'u' and ln.rest == 'add usb 1-1' for ln in lines)
    assert any(ln.kind == 'u' and ln.rest == 'add sound pcmC1D0p' for ln in lines)
    would = [ln.rest.split(' at ')[0] for ln in lines if ln.kind == 'shadow:' and ln.rest.startswith('would')]
    assert would[:3] == ['would grant', 'would dac', 'would revoke'], would
    grant = next(ln.t for ln in lines if ln.kind == 'shadow:' and ln.rest.startswith('would grant'))
    on = next(ln.t for ln in lines if ln.kind == 'stock' and ln.rest == 'otg-on')
    assert abs((on - grant) - (3.0 - 0.2) * S) < 60 * MS, 'the shadow 200 ms into the low, stock at its 3 s settle'
    assert any(ln.kind == 'axp' for ln in lines) and any(ln.kind == 'otg' and 'gotgctl=000d00fc' in ln.rest for ln in lines)
    cyc = [ln for ln in lines if ln.kind == 'cycle']
    assert cyc and 'type=dac' in cyc[0].rest and 'stock=grant shadow=grant agree' in cyc[0].rest
    assert re.search(r'^\S+Z \d+ I usbtrace usbtrace cycle 1 dac: stock grant at 3\.\d+ s, shadow grant at 0\.\d+ s; agree$',
                     q.ulog(), re.M), q.ulog()
    out = []
    assert R.report(R.cycles(lines), [], type('W', (), {'write': lambda self, s: out.append(s)})()) == 0
    assert 'cycle 1 dac' in ''.join(out)


def test_stock_powering_a_low_the_shadow_would_not_is_a_disagreement(L):
    """An ID low 2 s after a computer's cable came out (inside the window), stock's power 3 s into it: the shadow waits
    UF_ID_PHANTOM_MS there, so stock's otg-on is a disagreement, logged at once and as a W line."""
    q = Q2(L)
    q.edge(1, 17, 0).set(1, online=1).udc_state(1.1, 'configured')
    q.edge(10, 17, 1).set(10, online=0).udc_state(10.1, 'suspended')
    q.edge(12, 18, 0).set(15, PC20=1).edge(15, 17, 0).edge(16, 18, 1).set(17, PC20=0).edge(18.6, 17, 1)
    q.run()
    text = q.trace()
    assert re.search(r'shadow: disagree stock=otg-on at \S+ shadow=none \(src-candidate\)', text), text
    assert 'stock=grant shadow=none DISAGREE' in text
    assert re.search(r' W usbtrace usbtrace cycle \d+ \S+: stock grant at .*disagree', q.ulog())


def test_bus_power_with_online_1_is_flagged_unsafe(L):
    q = Q2(L)
    q.edge(2, 18, 0).set(5, PC20=1).edge(5, 17, 0).set(6, online=1)
    q.run()
    assert 'unsafe: bus power on with online 1' in q.trace() and ' UNSAFE' in q.trace()
    assert 'bus power with online 1' in q.ulog()


# ---- what it costs ----

def test_idle_it_wakes_once_a_second_and_writes_almost_nothing(L):
    """Ten minutes with nothing on the port: a poll a second (each sample's), no PMIC register read after the first,
    and the trace's start, its first sample and its clock line."""
    q = Q2(L, end_s=600).run()
    calls = q.calls()
    polls = [c for c in calls if c[0] == 'poll']
    assert 590 <= len(polls) <= 610, len(polls)
    assert all(c[3] <= 1000 for c in polls)
    assert len([c for c in calls if c[1] == 'smbus']) == 7, 'the PMIC asked once, at the start'
    kinds = [ln.kind for ln in R.parse(q.trace().splitlines())]
    assert kinds[:4] == ['start', 'udc', 's', 'axp'] and set(kinds) <= {'start', 's', 'axp', 'otg', 'udc', 'clock', 'stop'}
    assert len(kinds) <= 8


def test_an_edge_starts_20_samples_a_second_for_30_s(L):
    q = Q2(L, end_s=100)
    q.edge(10, 19, 0)
    q.run()
    times = [c for c in q.calls() if c[0] == 'open' and c[1] == ONLINE]
    assert 590 <= len(times) <= 720, len(times)  # 30 s at 20 a second, the rest at one a second


# ---- the card, the flags, the lines, a Standby ----

def test_the_trace_moves_to_dot_1_once_full(L):
    logs = L.root / 'mnt/mmc/.q2muse/logs'
    logs.mkdir(parents=True)
    (logs / 'q2-usbtrace.0').write_text('x' * FILE_MAX)
    Q2(L, end_s=5).run()
    assert (logs / 'q2-usbtrace.1').stat().st_size == FILE_MAX
    assert (logs / 'q2-usbtrace.0').read_text().split(' ', 2)[1] == 'start'


def test_with_the_card_away_lines_wait_in_ram(L):
    ready = C.c_int.in_dll(L, 'ut_card_ready')
    ready.value = 0
    q = Q2(L, end_s=200)
    q.edge(10, 18, 0).edge(11, 18, 1)
    q.at(150, lambda: setattr(ready, 'value', 1))
    q.run()
    assert 'e PD18 0' in q.trace() and 'cycle n=1' in q.trace()


def test_the_flag_file_stops_it_before_it_starts(L):
    q = Q2(L)
    q.stop = True
    assert L.q2usbtrace_main(0, None) == 0
    assert q.calls() == [] and q.trace() == ''


def test_a_line_already_held_is_read_from_its_register(L):
    C.c_int.in_dll(L, 'ut_line_busy').value = 1 << 18
    q = Q2(L, end_s=30)
    q.edge(5, 18, 0)
    q.run()
    lines = R.parse(q.trace().splitlines())
    assert 'pd18=sampled' in lines[0].rest
    assert any(ln.kind == 's' and 'PD18=0' in ln.rest for ln in lines)


def test_a_cleared_interrupt_is_asked_for_again(L):
    """demo's set_func at a wake leaves the pin a plain input: no edges come, so the line is asked for again."""
    q = Q2(L, end_s=30)
    q.at(5, lambda: q.gpio.__setitem__((0x300 + 0x10) // 4, q.gpio[(0x300 + 0x10) // 4] & ~(1 << 18)))
    q.edge(12, 18, 0)
    q.run()
    assert "note PD18's events asked for again" in q.trace()
    assert 'e PD18 0' in q.trace()


def test_a_standby_is_a_wake_line_and_the_port_read_again(L):
    q = Q2(L, end_s=60)
    q.at(20, lambda: setattr(C.c_longlong.in_dll(L, 'ut_slept'), 'value', 95 * S))
    q.run()
    assert re.search(r' wake slept=95\.\d{3}\n', q.trace())


def test_without_debugfs_it_mounts_it_for_the_regdump(L):
    q = Q2(L, end_s=3).run()
    assert ('mount', '/sys/kernel/debug', 0, 0, 0) in q.calls()
    assert 'regdump=ok' in q.trace()
