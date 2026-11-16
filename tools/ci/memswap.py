#!/usr/bin/env python3
"""Hot swap spike: write a rebuilt payload's text into a running emulated demo (docs/hotswap-discovery.md).

    python3 tools/ci/memswap.py info COPY BUILD             # our demo's pid, guest_base, threads, check bytes
    python3 tools/ci/memswap.py bench COPY BUILD [N]        # stop, rewrite the payload unchanged, continue: timed
    python3 tools/ci/memswap.py peek COPY BUILD             # the spike hook's cells (hotswap_cell)
    python3 tools/ci/memswap.py poke COPY BUILD ADDR HEX    # stop, write bytes at a guest address, continue
    python3 tools/ci/memswap.py inval COPY BUILD WAY        # ask the hook to drop qemu's translations (1 or 2)
    python3 tools/ci/memswap.py swap COPY OLD NEW [WAY]     # write NEW's changed payload words, then inval
    python3 tools/ci/memswap.py stacks COPY BUILD           # return addresses into the payload on each stack
    python3 tools/ci/memswap.py ptrs COPY BUILD             # payload code and rodata addresses held in memory
    python3 tools/ci/memswap.py uistack COPY BUILD          # the UI thread's frames at the hook
    python3 tools/ci/memswap.py risk COPY OLD NEW           # live references a swap from OLD to NEW strands

COPY is an emulator copy (~/q2-emu-pool1); BUILD a build directory holding demo and patch.elf. The pid is
found by walking up to COPY's own bwrap (demo_pid), never by command line alone: every copy's demo has the
same one. Memory goes through /proc/PID/mem with every thread stopped by SIGSTOP; qemu-user maps guest
address G at host G + guest_base (guest_base, read from /proc/PID/maps and checked against the build).
"""
import os
import pathlib
import signal
import struct
import subprocess
import sys
import time

BASE = 0xb00000      # the payload's first byte (patch/link.ld)
SCRATCH = 0xc80000   # its zero-filled state, never written by a swap


def demo_pid(copy):
    """The pid of demo under this copy's sandbox, or None: each demo's parents walked up to the copy's bwrap."""
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


def payload_segment(demo):
    """(file offset, vaddr, filesz) of demo's PT_LOAD at BASE."""
    raw = open(demo, 'rb').read(4096)
    phoff, = struct.unpack_from('<I', raw, 28)
    phnum, = struct.unpack_from('<H', raw, 44)
    for i in range(phnum):
        t, off, va, _, fsz, _, _, _ = struct.unpack_from('<8I', raw, phoff + 32 * i)
        if t == 1 and va == BASE:
            return off, va, fsz
    raise SystemExit(f'{demo}: no PT_LOAD at {BASE:#x}')


def guest_base(pid, demo):
    """Host address of guest 0: the mapping of demo's payload file offset, less BASE."""
    off, va, _ = payload_segment(demo)
    for line in open(f'/proc/{pid}/maps'):
        f = line.split()
        if len(f) >= 6 and f[5] == '/release/bin/demo' and int(f[2], 16) == off:
            return int(f[0].split('-')[0], 16) - va
    raise SystemExit(f'pid {pid}: no mapping of /release/bin/demo at offset {off:#x}')


def symbols(build):
    out = subprocess.run(['llvm-nm', str(pathlib.Path(build)/'patch.elf')], capture_output=True, text=True).stdout
    return {f[2]: int(f[0], 16) for f in (l.split() for l in out.splitlines()) if len(f) == 3}


def text_end(build):
    """End of .text+.rodata: the first byte past them, before .got (patch/link.ld)."""
    out = subprocess.run(['llvm-readelf', '-SW', str(pathlib.Path(build)/'patch.elf')], capture_output=True, text=True).stdout
    end = 0
    for l in out.splitlines():
        f = l.replace('[ ', '[').split()
        if len(f) > 5 and f[1] in ('.text', '.rodata'):
            end = max(end, int(f[3], 16) + int(f[5], 16))
    return end


def states(pid):
    out = {}
    for t in os.listdir(f'/proc/{pid}/task'):
        try:
            out[int(t)] = open(f'/proc/{pid}/task/{t}/stat').read().rsplit(')', 1)[1].split()[0]
        except OSError:
            pass
    return out


class Stopped:
    """Every thread of pid stopped (SIGSTOP) for the block, then continued."""
    def __init__(self, pid):
        self.pid = pid

    def __enter__(self):
        t = time.monotonic()
        os.kill(self.pid, signal.SIGSTOP)
        while not all(s in 'Tt' for s in states(self.pid).values()):
            if time.monotonic() - t > 2:
                os.kill(self.pid, signal.SIGCONT)
                raise SystemExit(f'pid {self.pid}: not every thread stopped: {states(self.pid)}')
            time.sleep(0.0005)
        self.stop_s = time.monotonic() - t
        return self

    def __exit__(self, *exc):
        os.kill(self.pid, signal.SIGCONT)


class Mem:
    def __init__(self, pid, base):
        self.f = os.open(f'/proc/{pid}/mem', os.O_RDWR)
        self.base = base

    def read(self, addr, n):
        return os.pread(self.f, n, addr + self.base)

    def write(self, addr, data):
        assert os.pwrite(self.f, data, addr + self.base) == len(data)

    def u32(self, addr):
        return struct.unpack('<I', self.read(addr, 4))[0]

    def put32(self, addr, v):
        self.write(addr, struct.pack('<I', v))


def open_copy(copy, build):
    copy = pathlib.Path(copy).expanduser().resolve()
    pid = demo_pid(copy)
    if not pid:
        raise SystemExit(f'no demo under {copy}')
    demo = pathlib.Path(build)/'demo'
    return pid, Mem(pid, guest_base(pid, demo)), demo


def changed_runs(old, new, gap=16):
    """(offset, bytes) runs where new differs from old, runs closer than gap merged."""
    runs, i, n = [], 0, len(new)
    while i < n:
        if old[i] == new[i]:
            i += 1
            continue
        j = i
        last = i
        while j < n and j - last <= gap:
            if old[j] != new[j]:
                last = j
            j += 1
        runs.append((i, new[i:last + 1]))
        i = last + 1
    return runs


def cells(mem, sym):
    a = sym['hotswap_cell']
    return [mem.u32(a + 4 * i) for i in range(8)]


def inval(mem, sym, way, start, end, timeout=5.0):
    """Ask the hook (ringnav_sleep's hotswap_poll) to invalidate [start, end); seconds until it has."""
    a = sym['hotswap_cell']
    t = time.monotonic()
    req = mem.u32(a) + 1
    mem.put32(a + 8, way)
    mem.put32(a + 12, start)
    mem.put32(a + 16, end)
    mem.put32(a, req)
    while mem.u32(a + 4) != req:
        if time.monotonic() - t > timeout:
            raise SystemExit('the hook did not answer: is the screen dark, or the build not --dev?')
        time.sleep(0.001)
    return time.monotonic() - t


def main():
    cmd, copy, build, *rest = sys.argv[1:]
    pid, mem, demo = open_copy(copy, build)
    off, va, fsz = payload_segment(demo)
    if cmd == 'info':
        print(f'pid {pid}, guest_base {mem.base:#x}, {len(states(pid))} threads')
        image = open(demo, 'rb').read()[off:off + fsz]
        live = mem.read(va, fsz)
        diff = sum(1 for i in range(0, fsz, 4) if live[i:i + 4] != image[i:i + 4])
        print(f'payload {va:#x}+{fsz:#x}: {diff} words differ from {demo}')
    elif cmd == 'bench':
        image = open(demo, 'rb').read()[off:off + fsz]
        end = text_end(build)
        for _ in range(int(rest[0]) if rest else 5):
            t = time.monotonic()
            with Stopped(pid) as s:
                w = time.monotonic()
                mem.write(va, image[:end - va])
                w = time.monotonic() - w
            print(f'stop {s.stop_s * 1000:.2f} ms, write {end - va} bytes {w * 1000:.2f} ms, '
                  f'paused {(time.monotonic() - t) * 1000:.2f} ms')
    elif cmd == 'peek':
        print(' '.join(f'{v:#x}' for v in cells(mem, symbols(build))))
    elif cmd == 'poke':
        addr, data = int(rest[0], 16), bytes.fromhex(rest[1])
        with Stopped(pid):
            old = mem.read(addr, len(data))
            mem.write(addr, data)
        print(f'{addr:#x}: {old.hex()} -> {data.hex()}')
    elif cmd == 'inval':
        sym = symbols(build)
        print(f'way {rest[0]}: {inval(mem, sym, int(rest[0]), BASE, text_end(build)) * 1000:.1f} ms; cells',
              ' '.join(f'{v:#x}' for v in cells(mem, sym)))
    elif cmd == 'swap':
        new_build = rest[0]
        way = int(rest[1]) if len(rest) > 1 else 2
        t0 = time.monotonic()
        noff, _, nfsz = payload_segment(pathlib.Path(new_build)/'demo')
        end_old, end_new = text_end(build), text_end(new_build)
        old = open(demo, 'rb').read()[off:off + end_old - va]
        new = open(pathlib.Path(new_build)/'demo', 'rb').read()[noff:noff + end_new - va]
        assert end_new < SCRATCH
        if len(new) > len(old):
            old += bytes(len(new) - len(old))
        runs = changed_runs(old, new)
        with Stopped(pid):
            for o, data in runs:
                mem.write(va + o, data)
        tw = time.monotonic() - t0
        sym = symbols(new_build)
        ti = inval(mem, sym, way, BASE, max(end_old, end_new))
        print(f'{len(runs)} runs, {sum(len(d) for _, d in runs)} bytes written in {tw * 1000:.1f} ms; '
              f'invalidated (way {way}) in {ti * 1000:.1f} ms; total {(time.monotonic() - t0) * 1000:.1f} ms')
    elif cmd == 'stacks':
        stacks(pid, mem, build)
    elif cmd == 'ptrs':
        ptrs(pid, mem, build)
    elif cmd == 'uistack':
        uistack(pid, mem, build)
    elif cmd == 'risk':
        risk(pid, mem, build, rest[0])


def risk(pid, mem, old_build, new_build):
    """Live references a swap from old_build to new_build would strand: words in writable guest memory equal to
    the entry of a function that moves, and stack words that are return addresses (after a jal or jalr) inside a
    function that moves or changes. Stacks are scanned whole, so dead frames count too."""
    import bisect

    def funcs(b):
        out = subprocess.run(['llvm-nm', '-S', '--defined-only', str(pathlib.Path(b)/'patch.elf')], capture_output=True, text=True).stdout
        return {f[3]: (int(f[0], 16), int(f[1], 16)) for f in (l.split() for l in out.splitlines()) if len(f) == 4 and f[2] in 'Tt'}
    fo, fn = funcs(old_build), funcs(new_build)
    ob = open(pathlib.Path(old_build)/'demo', 'rb').read()
    nb = open(pathlib.Path(new_build)/'demo', 'rb').read()
    off, va, _ = payload_segment(pathlib.Path(old_build)/'demo')
    noff, _, _ = payload_segment(pathlib.Path(new_build)/'demo')
    bad = {}
    for k, (a, n) in fo.items():
        if k not in fn:
            bad[k] = 'gone'
        elif fn[k][0] != a:
            bad[k] = f'moved {fn[k][0] - a:+d}'
        elif ob[off + a - va:off + a - va + n] != nb[noff + a - va:noff + a - va + n]:
            bad[k] = 'changed'
    starts = sorted((a, k) for k, (a, n) in fo.items())
    entries = {a: k for k, (a, n) in fo.items() if k in bad and bad[k] != 'changed'}
    ptr, ra = {}, {}
    with Stopped(pid):
        for line in open(f'/proc/{pid}/maps'):
            f = line.split()
            lo, hi = (int(x, 16) for x in f[0].split('-'))
            if not f[1].startswith('rw') or lo - mem.base >= 0x80000000 or (len(f) >= 6 and f[5].startswith('[')):
                continue
            try:
                data = mem.read(lo - mem.base, hi - lo)
            except OSError:
                continue
            for (v,) in struct.iter_unpack('<I', data[:len(data) & ~3]):
                if v in entries:
                    ptr[entries[v]] = ptr.get(entries[v], 0) + 1
                elif BASE + 8 <= v < SCRATCH and v % 4 == 0 and off + v - 8 - va + 4 <= len(ob):
                    ins, = struct.unpack_from('<I', ob, off + v - 8 - va)
                    if ins >> 26 == 3 or (ins >> 26 == 0 and ins & 0x3f == 9):
                        j = bisect.bisect_right(starts, (v, '\xff')) - 1
                        k = starts[j][1] if j >= 0 else None
                        if k in bad and v < fo[k][0] + fo[k][1]:
                            ra[k] = ra.get(k, 0) + 1
    print(f'{len(bad)} functions changed or moved; held as pointers: {ptr or "none"}')
    print(f'return addresses into them (dead frames too): {ra or "none"}')
    print('swap is', 'UNSAFE' if ptr or ra else 'safe by this check')


def uistack(pid, mem, build):
    """The UI thread's call chain at the idle hook, walked frame by frame from hotswap_poll's $sp (cell 8):
    each frame's size and return-address slot from its prologue, found by reading back from the pc to the
    function's `addiu $sp, $sp, -N` (the heuristic of gdb's MIPS unwinder). Prints every caller, stock's or
    the payload's, out to main."""
    import bisect
    sym = symbols(build)
    funcs = sorted((a, n) for n, a in sym.items() if BASE <= a < SCRATCH)
    with Stopped(pid):
        sp = mem.u32(sym['hotswap_cell'] + 32)
        pc = sym['hotswap_poll'] + 8  # past its prologue, which the walk reads back to
        for depth in range(64):
            name = 'stock'
            if BASE <= pc < SCRATCH:
                k = bisect.bisect_right(funcs, (pc, '\xff')) - 1
                name = 'PAYLOAD ' + funcs[k][1]
            size = ra_off = None
            a = pc - 4
            while a > pc - 0x8000:
                ins = mem.u32(a)
                if ins >> 16 == 0xafbf and ra_off is None:      # sw $ra, off($sp)
                    ra_off = ins & 0xffff
                if ins >> 16 == 0x27bd and ins & 0x8000:        # addiu $sp, $sp, -N
                    size = 0x10000 - (ins & 0xffff)
                    break
                a -= 4
            print(f'   #{depth} pc {pc:#x} sp {sp:#x} {name}' + (f' (frame {size}, ra at +{ra_off})' if size else ''))
            if size is None or ra_off is None:
                print('   (no prologue found: the walk stops)')
                break
            pc = mem.u32(sp + ra_off)
            sp += size
            if pc < 0x400000:
                break

if __name__ == '__main__':
    main()
