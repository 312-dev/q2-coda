#!/usr/bin/env python3
"""Bind a built usb_f_uac1.ko (tools/uac1_module) under Unicorn as the probe's uac1 phase sets it up, and check the
interfaces of configuration 2 (docs/iphone-mode.md#the-uac1-module).

    python3 test/uac1_bind.py ~/.cache/q2-uac1/module/usb_f_uac1.ko

The module's own f_audio_alloc_inst, f_audio_alloc and f_audio_bind run, every instruction and relocation of the .ko
retained; only the kernel functions they call are modeled (Machine.service): usb_interface_id hands out 0, 1, ... as
composite.c does for one configuration, and usb_assign_descriptors keeps the full-speed descriptor list, which is
what the car reads in the configuration descriptor. The attributes are tools/iap_probe/q2-iap-probe.sh's (c_chmask 0,
p_chmask 3, p_ssize 2, p_srate 44100). The probe links ffs.c2 (the HID) after uac1.car, so the HID's interface is the
next number usb_interface_id gives. Checked: the HID is interface 2, as on a real iPhone (the BMW reads interface 2's
HID descriptor), UAC1's interfaces are the numbers it was given and no others, and the AC header's bInCollection,
baInterfaceNr and wTotalLength agree with the descriptors that follow it, and the AS IN format lists 32, 44.1 and 48
kHz (docs/iphone-mode.md#per-song-rate). Instructions are stepped one at a time with
a fresh integer context after each branch, as test/aic.py does, for Unicorn's MIPS chaining bug. Exit 0 when all hold.
"""
import argparse
import importlib.util
from pathlib import Path
import struct
import sys

from unicorn import Uc, UC_ARCH_MIPS, UC_MODE_MIPS32, UC_MODE_LITTLE_ENDIAN, UC_TLB_VIRTUAL
from unicorn.mips_const import UC_CPU_MIPS32_24KF, UC_MIPS_REG_0, UC_MIPS_REG_PC, UC_MIPS_REG_HI, UC_MIPS_REG_LO

from module_mips import Module

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location('uac1_module_build', ROOT/'tools/uac1_module/uac1_build.py')
assert _spec and _spec.loader
uac1_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(uac1_build)
OFF_FUNC_FI = uac1_build.STOCK['q2_off_func_fi'][0]          # usb_function.fi, which usb_get_function sets
OFF_CONFIG_CDEV = uac1_build.STOCK['q2_off_config_cdev'][0]  # usb_configuration.cdev
OFF_FUNC_CONFIG = uac1_build.STOCK['q2_off_func_config'][0]  # usb_function.config
# f_uac1_opts after func_inst, as f_audio_alloc_inst sets them (u_uac1.h's UAC1_DEF_*): c_chmask, c_srate, c_ssize,
# p_chmask, p_srate, p_ssize, req_number
DEFAULTS = (3, 48000, 2, 3, 48000, 2, 2)
PROBE = {'c_chmask': 0, 'p_chmask': 3, 'p_ssize': 2, 'p_srate': 44100}  # q2-iap-probe.sh's uac1 attributes
IPHONE_HID = 2
# The AS IN format's rates: the module's p_srates when p_srate is one of them (docs/iphone-mode.md#per-song-rate),
# the three a BMW's iAP2 identification lists (rate indexes 6, 7 and 8)
RATES = [32000, 44100, 48000]
IN_EP = 0x81  # the address usb_ep_autoconfig gives the playback endpoint (modeled)

REGS = [UC_MIPS_REG_0 + n for n in range(1, 32)] + [UC_MIPS_REG_HI, UC_MIPS_REG_LO]
reg = lambda n: UC_MIPS_REG_0 + n
KERNEL, HEAP, STACK = 0x80000000, 0x05000000, 0x040f8000
USB_DT_INTERFACE, USB_DT_CS_INTERFACE, UAC_HEADER, UAC_FORMAT_TYPE = 4, 0x24, 1, 2


class Machine:
    def __init__(self, data):
        self.uc = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
        self.uc.ctl_set_cpu_model(UC_CPU_MIPS32_24KF)
        self.uc.ctl_set_tlb_mode(UC_TLB_VIRTUAL)
        self.clean = self.uc.context_save()
        self.uc.mem_map(KERNEL, 0x10000)
        self.uc.mem_map(0x04000000, 0x100000)
        self.uc.mem_map(HEAP, 0x100000)
        self.halt = KERNEL + 0xff00
        self.handlers, self.externals, self.events = {}, {}, []
        self.brk = HEAP
        self.interfaces = 0
        self.fs = None
        self.symbols, _ = Module(data).load(self, 0xc0001000, init_base=0xd0001000)
        # u_audio's ALSA card, which needs no USB: the module's own g_audio_setup is modeled as a kernel call
        self.handlers[self.symbols['q2_g_audio_setup']] = 'q2_g_audio_setup'

    def external(self, name):
        if name not in self.externals:
            a = KERNEL + 0x1000 + len(self.externals) * 0x40
            self.handlers[a] = name
            self.externals[name] = a
        return self.externals[name]

    def read(self, a, n=4):
        return bytes(self.uc.mem_read(a, n)) if n != 4 else struct.unpack('<I', self.uc.mem_read(a, 4))[0]

    def write(self, a, v):
        self.uc.mem_write(a, struct.pack('<I', v & 0xffffffff))

    def alloc(self, size):
        a, self.brk = self.brk, (self.brk + size + 15) & ~15
        assert self.brk < HEAP + 0x100000, 'heap exhausted'
        self.uc.mem_write(a, bytes(size))
        return a

    def service(self, name, a):
        if name in ('kmem_cache_alloc_trace', '__kmalloc', 'kmem_cache_alloc'):
            return self.alloc({'kmem_cache_alloc_trace': a[2], '__kmalloc': a[0]}.get(name, 0x400))
        if name == 'usb_gstrings_attach':  # n struct usb_string {u8 id; const char *s;}, ids from 0x10
            us = self.alloc(8 * a[2])
            for i in range(a[2]):
                self.uc.mem_write(us + 8 * i, bytes([0x10 + i]))
            return us
        if name == 'usb_interface_id':
            self.interfaces += 1
            return self.interfaces - 1
        if name == 'usb_ep_autoconfig':  # the endpoint's address, as the UDC's would be
            self.uc.mem_write(a[1] + 2, bytes([IN_EP if self.read(a[1] + 2, 1)[0] & 0x80 else 0x01]))
            return self.alloc(0x40)
        if name == 'usb_assign_descriptors':
            self.fs = []
            p = a[1]
            while self.read(p):
                d = self.read(p)
                self.fs.append(self.read(d, self.read(d, 1)[0]))
                p += 4
            return 0
        if name == 'memcpy':
            self.uc.mem_write(a[0], self.read(a[1], a[2]))
            return a[0]
        if name == 'memset':
            self.uc.mem_write(a[0], bytes([a[1] & 0xff]) * a[2])
            return a[0]
        if name in ('_dev_err', 'printk'):
            raise AssertionError(f'{name} during the bind')
        if name in ('__mutex_init', 'mutex_lock', 'mutex_unlock', 'config_group_init_type_name', 'kfree',
                    'q2_g_audio_setup', '__raw_spin_lock_init', '__dynamic_dev_dbg', '__dynamic_pr_debug'):
            return 0
        raise AssertionError(('kernel call not modeled', name, [hex(x) for x in a]))

    def call(self, fn, *args):
        u = self.uc
        u.context_restore(self.clean)
        for n, v in enumerate(args):
            u.reg_write(reg(4 + n), v)
        u.reg_write(reg(29), STACK)
        u.reg_write(reg(31), self.halt)
        pc = self.symbols[fn] if isinstance(fn, str) else fn  # a name, or an address (a function pointer)
        for _ in range(200000):
            if pc == self.halt:
                return u.reg_read(reg(2))
            if pc in self.handlers:
                name = self.handlers[pc]
                a = [u.reg_read(reg(n)) for n in range(4, 8)]
                self.events.append(name)
                result = self.service(name, a)
                ra = u.reg_read(reg(31))
                regs = {r: u.reg_read(r) for r in REGS}
                u.context_restore(self.clean)
                for r, v in regs.items():
                    u.reg_write(r, v)
                u.reg_write(reg(2), result & 0xffffffff)
                pc = ra
                continue
            word = self.read(pc)
            op = word >> 26
            transfer = op in (1, 2, 3, 4, 5, 6, 7, 20, 21, 22, 23) or op == 0 and word & 63 in (8, 9)
            u.emu_start(pc, self.halt, count=1)
            pc = u.reg_read(UC_MIPS_REG_PC)
            if transfer:
                regs = {r: u.reg_read(r) for r in REGS}
                u.context_restore(self.clean)
                for r, v in regs.items():
                    u.reg_write(r, v)
        raise AssertionError(f'{fn}: instruction budget exhausted')


def bind(data):
    """(full-speed descriptors, interface numbers UAC1 took) of the module bound with the probe's attributes."""
    m = bound(data)
    return m.fs, list(range(m.interfaces))


def bound(data, cls=Machine, opts=PROBE):
    """A cls whose module is bound with opts: its function at .function, the composite device at .cdev."""
    m = cls(data)
    fi = m.call('f_audio_alloc_inst')
    words = [m.read(fi + 4 * i) for i in range(0x60)]
    at = next(i for i in range(len(words)) if tuple(words[i:i + 7]) == DEFAULTS)  # c_chmask
    names = ['c_chmask', 'c_srate', 'c_ssize', 'p_chmask', 'p_srate', 'p_ssize', 'req_number']
    for k, v in opts.items():
        m.write(fi + 4 * (at + names.index(k)), v)
    f = m.call('f_audio_alloc', fi)
    m.write(f + OFF_FUNC_FI, fi)
    gadget = m.alloc(0x200)
    cdev = m.alloc(0x100)
    m.write(cdev, gadget)  # usb_composite_dev.gadget, its first field
    c = m.alloc(0x100)
    m.write(c + OFF_CONFIG_CDEV, cdev)
    m.write(f + OFF_FUNC_CONFIG, c)  # as usb_add_function sets it
    status = m.call('f_audio_bind', c, f)
    assert status == 0, f'f_audio_bind returned {status - (1 << 32) if status >> 31 else status}'
    assert m.fs is not None, 'f_audio_bind assigned no descriptors'
    m.function, m.cdev, m.gadget = f, cdev, gadget
    return m


def check(fs, taken):
    """What is wrong with configuration 2 (UAC1's descriptors, then the HID at the next interface), one line each."""
    wrong = []
    hid = len(taken)
    ifaces = sorted({d[2] for d in fs if d[1] == USB_DT_INTERFACE})
    if hid != IPHONE_HID:
        wrong.append(f'the HID is interface {hid}, a real iPhone\'s is {IPHONE_HID} (UAC1 took {taken})')
    if ifaces != taken:
        wrong.append(f'UAC1\'s descriptors name interfaces {ifaces}, it was given {taken}')
    i = next(n for n, d in enumerate(fs) if d[1] == USB_DT_CS_INTERFACE and d[2] == UAC_HEADER)
    h = fs[i]
    total, n = struct.unpack_from('<H', h, 5)[0], h[7]
    streaming = sorted(d[2] for d in fs if d[1] == USB_DT_INTERFACE and d[5:7] == b'\x01\x02' and d[3] == 0)
    if list(h[8:8 + n]) != streaming or len(h) != 8 + n:
        wrong.append(f'AC header: bInCollection {n}, baInterfaceNr {list(h[8:])}, the streaming interfaces {streaming}')
    cs = i
    while cs < len(fs) and fs[cs][1] == USB_DT_CS_INTERFACE:
        cs += 1
    if total != sum(len(d) for d in fs[i:cs]):
        wrong.append(f'AC header: wTotalLength {total}, the class-specific AC descriptors {sum(len(d) for d in fs[i:cs])}')
    fmt = [d for d in fs if d[1] == USB_DT_CS_INTERFACE and d[2] == UAC_FORMAT_TYPE]
    rates = [int.from_bytes(fmt[-1][8 + 3 * k:11 + 3 * k], 'little') for k in range(fmt[-1][7])] if fmt else []
    if rates != RATES or fmt and len(fmt[-1]) != 8 + 3 * len(rates):
        wrong.append(f'AS IN format: rates {rates} (bLength {len(fmt[-1]) if fmt else None}), not {RATES}')
    return wrong, hid, ifaces, list(h[8:8 + n]), total, rates


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('ko', type=Path, help='a usb_f_uac1.ko from tools/uac1_module/uac1_build.py')
    a = ap.parse_args(argv)
    fs, taken = bind(a.ko.read_bytes())
    wrong, hid, ifaces, ba, total, rates = check(fs, taken)
    print(f'{a.ko}: {len(fs)} descriptors; UAC1 interfaces {ifaces}, baInterfaceNr {ba}, wTotalLength {total}; '
          f'HID at interface {hid}; playback rates {rates}')
    for w in wrong:
        print('FAIL  ' + w)
    print('ok' if not wrong else f'{len(wrong)} wrong')
    return 1 if wrong else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
