#!/usr/bin/env python3
"""Build usb_f_uac1.ko for the Q2's kernel (docs/iphone-mode.md#the-uac1-module) and check it against the stock one.

    python3 tools/uac1_module/uac1_build.py [--work DIR] [--vmlinux ELF] [--stock-module KO]

Linux 5.10.21 from kernel.org (hash checked) is unpacked into --work (default ~/.cache/q2-uac1) without the other
architectures, Documentation or most drivers' sources, configured as malta_defconfig plus q2.config, and prepared for
modules; then this directory's Kbuild builds f_uac1.c, with the two upstream commits in patches/ applied (PATCHES),
and its own renamed u_audio.c. Needs mipsel-linux-gnu-gcc and binutils (Ubuntu: gcc-mipsel-linux-gnu), flex, bison,
bc, patch.

Checked, each printed: the vermagic is the stock modules'; struct module's size and the offsets of init and exit are
--stock-module's (a vendor .ko, default ~/q2-emu/stockfs/module_driver/soc_aic.ko); every symbol the module needs is
exported by the kernel (--vmlinux: the stock kernel turned into an ELF with symbols by vmlinux-to-elf, its __ksymtab_
names); no symbol the module exports is one the kernel has. The field offsets of q2_layout.ko are printed for the
disassembly check docs/iphone-mode.md describes. The kernel's own sources are not in this repository, but for the two
upstream commits in patches/; the tarball is where the rest come from."""
import argparse
import pathlib
import shutil
import subprocess
import sys
import urllib.request


HERE = pathlib.Path(__file__).resolve().parent
VERSION = '5.10.21'
URL = f'https://cdn.kernel.org/pub/linux/kernel/v5.x/linux-{VERSION}.tar.xz'
SHA256 = '9e7fe6ff1665bb578e8a88e761f6727b7f170a81563b93c1d49bd0bb7d7de2af'  # kernel.org's sha256sums.asc
VERMAGIC = '5.10.21+ SMP preempt mod_unload MIPS32_R5 32BIT '  # the trailing space is in the stock modules too
ARCHES = ['alpha', 'arc', 'arm', 'arm64', 'c6x', 'csky', 'h8300', 'hexagon', 'ia64', 'm68k', 'microblaze', 'nds32',
          'nios2', 'openrisc', 'parisc', 'powerpc', 'riscv', 's390', 'sh', 'sparc', 'um', 'x86', 'xtensa']
SOURCES = ['f_uac1.c', 'u_audio.c', 'u_audio.h', 'u_uac1.h']
# Linux commits applied to f_uac1.c, from patches/ (each upstream's own patch, unchanged). 254cb1e0d78c gives the
# function no AS OUT interface when c_chmask is 0, so with capture off UAC1 takes interfaces 0 (control) and 1 (AS IN)
# and a function linked after it starts at 2, as a real iPhone's HID does in configuration 2; 5.10.21's f_uac1.c
# always took three, and the BMW's GET_DESCRIPTOR(HID) for interface 2 went to f_uac1, which stalled it
# (docs/iphone-mode.md#the-uac1-module). a59c68a6a3d1 is the commit before it, which it needs to apply.
PATCHES = ['0001-usb-gadget-f_uac1-validate-input-parameters.patch',  # a59c68a6a3d1
           '0002-usb-gadget-f_uac1-disable-IN-OUT-ep-if-unused.patch']  # 254cb1e0d78c
# Offsets and sizes read in the stock kernel's own code (vmlinux-to-elf of the #303 kernel, docs/iphone-mode.md#the-uac1-module),
# each with the instruction it came from: q2_layout.ko must agree with every one.
STOCK = {
    'q2_size_page': (0x1000, 'alloc_pages_exact 0x8012c1d0 srl $2, $2, 0xc (get_order of the size)'),
    'q2_off_req_buf': (0x0, 'u_audio_start_playback 0x804bd868 sw $4, 0x0($2)'),
    'q2_off_req_length': (0x4, 'u_audio_start_playback 0x804bd848 sw $19, 0x4($2)'),
    'q2_off_req_complete': (0x1c, 'u_audio_start_playback 0x804bd844 sw $20, 0x1c($2)'),
    'q2_off_req_context': (0x20, 'u_audio_start_playback 0x804bd840 sw $21, 0x20($2)'),
    'q2_off_req_status': (0x30, 'u_audio_iso_complete 0x804be7cc lw $7, 0x30($5)'),
    'q2_off_req_actual': (0x34, 'u_audio_iso_complete 0x804beac0 sw $6, 0x34($16)'),
    'q2_off_ep_ops': (0x8, 'usb_ep_queue 0x804ab5e0 lw $2, 0x8($4)'),
    'q2_off_ep_desc': (0x24, 'usb_ep_enable 0x804abb90 lw $5, 0x24($4)'),
    'q2_off_gadget_speed': (0x24, 'config_ep_by_speed_and_alt 0x804a4024 lw $2, 0x24($4)'),
    'q2_off_gadget_max_speed': (0x28, 'maximum_speed_show 0x804ab8d4 lw $4, 0x28($2)'),
    'q2_off_gadget_state': (0x34, 'usb_gadget_set_state 0x804ab6bc sw $2, 0x34($6)'),
    'q2_off_gadget_dev': (0x40, 'g_audio_setup 0x804beec4-0x804beee4: snd_card_new(gadget + 0x40, ...)'),
    'q2_off_dev_driver_data': (0x40, 'composite_setup 0x804a5658 lw $16, 0x80($4): the gadget dev\'s driver_data'),
    'q2_off_gadget_ep0': (0x18, 'composite_setup 0x804a56b0 lw $3, 0x18($9); 0x804a56b4 sw $16, 0x0($3)'),
    'q2_off_cdev_req': (0x4, 'composite_setup 0x804a5668 lw $20, 0x4($16)'),
    'q2_off_config_cdev': (0x1c, 'afunc_setup 0x804bffcc lw $22, 0x1c($8)'),
    'q2_off_func_fs_descriptors': (0x8, 'usb_assign_descriptors 0x804a3b78 sw $2, 0x8($16)'),
    'q2_off_func_hs_descriptors': (0xc, 'usb_assign_descriptors 0x804a3bf4 sw $2, 0xc($16)'),
    'q2_off_func_ss_descriptors': (0x10, 'usb_assign_descriptors 0x804a3c14 sw $2, 0x10($16)'),
    'q2_off_func_ssp_descriptors': (0x14, 'usb_assign_descriptors 0x804a3c2c sw $2, 0x14($16)'),
    'q2_off_func_config': (0x18, 'usb_add_function 0x804a465c sw $17, 0x18($16)'),
    'q2_off_func_bind': (0x24, 'usb_add_function 0x804a4688 lw $2, 0x24($16)'),
    'q2_off_func_unbind': (0x28, 'usb_remove_function 0x804a4980 lw $2, 0x28($16)'),
    'q2_off_func_free_func': (0x2c, 'usb_put_function 0x804a7f18 lw $25, 0x2c($4)'),
    'q2_off_func_disable': (0x3c, 'usb_remove_function 0x804a493c lw $2, 0x3c($5)'),
    'q2_off_func_list': (0x58, 'usb_add_function 0x804a4670 sw $3, 0x58($16)'),
    'q2_off_func_fi': (0x64, 'usb_get_function 0x804a7f54 sw $16, 0x64($2)'),
    'q2_off_fi_fd': (0x58, 'usb_get_function 0x804a7f30 lw $2, 0x58($4)'),
    'q2_off_fi_free_func_inst': (0x60, 'usb_put_function_instance 0x804a7f80 lw $2, 0x60($4)'),
    'q2_off_fd_alloc_func': (0x14, 'usb_get_function 0x804a7f3c lw $2, 0x14($2)'),
    'q2_off_card_driver': (0x14, 'g_audio_setup 0x804bf118 addiu $4, $4, 0x14 (strscpy)'),
    'q2_off_card_shortname': (0x24, 'g_audio_setup 0x804bf12c addiu $4, $4, 0x24 (strscpy)'),
    'q2_off_card_longname': (0x44, 'g_audio_setup 0x804bf148 addiu $4, $4, 0x44 (snprintf)'),
    'q2_off_card_dev': (0x414, 'g_audio_setup 0x804bf144 lw $2, 0x414($4)'),
    'q2_off_dev_id': (0x1cc, 'g_audio_setup 0x804bf14c lw $2, 0x1cc($2)'),
    'q2_off_pcm_name': (0x58, 'g_audio_setup 0x804bef34 addiu $4, $4, 0x58 (strscpy)'),
    'q2_off_pcm_private_data': (0x51c, 'g_audio_setup 0x804bef44 sw $16, 0x51c($4); snd_pcm_attach_substream 0x8058c0ec'),
    'q2_size_snd_pcm_str': (0x220, 'snd_pcm_attach_substream 0x8058bf44: stream * 17 * 32'),
    'q2_off_sub_private_data': (0x8, 'uac_pcm_pointer 0x804bccd0 lw $2, 0x8($4)'),
    'q2_off_sub_stream': (0x30, 'uac_pcm_pointer 0x804bccd4 lw $3, 0x30($4)'),
    'q2_off_sub_runtime': (0x70, 'uac_pcm_pointer 0x804bccd8 lw $4, 0x70($4)'),
    'q2_off_rt_frame_bits': (0x68, 'uac_pcm_pointer 0x804bccec lw $3, 0x68($4)'),
    'q2_off_rt_hw': (0x100, 'uac_pcm_open 0x804bdc80 sw $2, 0x100($18) (hw.info)'),
    'q2_off_rt_dma_area': (0x244, 'u_audio_iso_complete 0x804beac8 lw $2, 0x244($fp)'),
    'q2_off_rt_dma_bytes': (0x24c, 'u_audio_iso_complete 0x804beacc lw $3, 0x24c($fp)'),
    'q2_size_spinlock': (0x10, 'do_raw_spin_lock in the kernel: CONFIG_DEBUG_SPINLOCK'),
}
ENV = {'ARCH': 'mips', 'CROSS_COMPILE': 'mipsel-linux-gnu-'}
# Ubuntu 24.04's packages for the toolchain, unpacked rather than installed (toolchain())
DEBS = ['flex', 'bison', 'gcc-12-mipsel-linux-gnu-base', 'cpp-12-mipsel-linux-gnu', 'gcc-12-cross-base-mipsen',
        'binutils-mipsel-linux-gnu', 'gcc-14-cross-base-mipsen', 'libc6-mipsel-cross', 'libgcc-s1-mipsel-cross',
        'libgomp1-mipsel-cross', 'libatomic1-mipsel-cross', 'libgcc-12-dev-mipsel-cross', 'gcc-12-mipsel-linux-gnu',
        'linux-libc-dev-mipsel-cross', 'libc6-dev-mipsel-cross']
TOOLS = ['as', 'ld', 'ar', 'nm', 'objcopy', 'objdump', 'readelf', 'strip']


def run(*cmd, cwd=None):  # pragma: no mutate block (the cross toolchain)
    import os
    env = {**os.environ, **ENV}
    env['PATH'] = f"{ENV['TC']}/bin:{env['PATH']}"
    env['BISON_PKGDATADIR'] = f"{ENV['TC']}/root/usr/share/bison"
    env['LD_LIBRARY_PATH'] = f"{ENV['TC']}/root/usr/lib/x86_64-linux-gnu"
    subprocess.run([str(c) for c in cmd], check=True, cwd=cwd, env=env)


def toolchain(work):  # pragma: no mutate block (apt-get download and dpkg-deb)
    """The cross gcc, binutils, flex and bison unpacked under work/tc, with a bin of the names the kernel uses."""
    tc = work/'tc'
    gcc = tc/'bin/mipsel-linux-gnu-gcc'
    if not gcc.exists():
        debs, root, bin_ = tc/'debs', tc/'root', tc/'bin'
        for d in (debs, root, bin_):
            d.mkdir(parents=True, exist_ok=True)
        subprocess.run(['apt-get', 'download', *DEBS], check=True, cwd=debs)
        for deb in sorted(debs.glob('*.deb')):
            subprocess.run(['dpkg-deb', '-x', str(deb), str(root)], check=True)
        links = {'mipsel-linux-gnu-gcc': 'mipsel-linux-gnu-gcc-12', 'flex': 'flex', 'bison': 'bison',
                 **{f'mipsel-linux-gnu-{t}': f'mipsel-linux-gnu-{t}' for t in TOOLS}}
        for name, target in links.items():
            (bin_/name).unlink(missing_ok=True)
            (bin_/name).symlink_to(f'../root/usr/bin/{target}')
        shutil.rmtree(debs)
    ENV['TC'] = str(tc)
    return tc


def sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def tree(work):  # pragma: no mutate block (kernel.org's tarball and make)
    """The trimmed, configured and prepared 5.10.21 tree under work."""
    tar, src = work/f'linux-{VERSION}.tar.xz', work/f'linux-{VERSION}'
    if not tar.exists():
        urllib.request.urlretrieve(URL, tar)
    if sha256(tar) != SHA256:
        sys.exit(f'build.py: {tar} is not kernel.org\'s linux-{VERSION}.tar.xz')
    if not (src/'scripts/mod/modpost').exists():
        top = f'linux-{VERSION}'
        excl = [f'--exclude={top}/arch/{a}' for a in ARCHES] + [f'--exclude={top}/{d}' for d in ('Documentation', 'tools')]
        excl += [f'--exclude={top}/{d}/*.c' for d in ('drivers', 'fs', 'net', 'sound', 'crypto', 'lib', 'mm')]
        run('tar', '-xJf', tar, *excl, cwd=work)
        # what Kconfig sources from the trimmed parts, the USB gadget and ALSA sources, the host tools' headers
        run('tar', '-xJf', tar, f'{top}/arch/x86/ras/Kconfig', f'{top}/Documentation/Kconfig', f'{top}/drivers/usb/gadget',
            f'{top}/sound/core', f'{top}/tools/include', f'{top}/tools/arch/mips', f'{top}/tools/arch/x86/include', cwd=work)
        # Malta offers no suspend with SMP (no CPU hotplug), and without PM_SLEEP struct device is smaller than the
        # Q2's, which moves struct snd_pcm's private_data (two struct devices sit before it). Only this throwaway tree
        # is changed, to lay the structures out; nothing from it runs but the module.
    # Shanling's struct usb_gadget has two more 32-bit fields after max_speed (upstream 5.12's ssp_rate and
    # max_ssp_rate, where they sit): stock usb_gadget_set_state stores state at 0x34 (0x804ab6bc), and g_audio_setup
    # passes snd_card_new the gadget's dev at 0x40 (0x804beee4). Without them the module's dev was at 0x38, and
    # snd_card_register oopsed on a parent that was no device (the desk, 2026-11-20). Unread by the module.
    g = src/'include/linux/usb/gadget.h'
    field = '\tenum usb_device_speed\t\tmax_speed;\n'
    pad = field + '\tunsigned int\t\t\tq2_ssp_rate, q2_max_ssp_rate; /* tools/uac1_module/uac1_build.py */\n'
    text = g.read_text()
    # A tree padded by an earlier run carries the fields already, whatever its comment names the script
    if 'q2_ssp_rate, q2_max_ssp_rate;' not in text:
        if text.count(field) != 1:
            sys.exit('build.py: include/linux/usb/gadget.h has no single max_speed field to pad after')
        g.write_text(text.replace(field, pad))
    k = src/'arch/mips/Kconfig'
    gate = 'config ARCH_SUSPEND_POSSIBLE\n\tdef_bool y\n\tdepends on SYS_SUPPORTS_HOTPLUG_CPU || !SMP\n'
    text = k.read_text()
    if gate in text:
        k.write_text(text.replace(gate, 'config ARCH_SUSPEND_POSSIBLE\n\tdef_bool y\n'))
    elif 'config ARCH_SUSPEND_POSSIBLE\n\tdef_bool y\n' not in text:
        sys.exit('build.py: arch/mips/Kconfig has no ARCH_SUSPEND_POSSIBLE gate to relax')
    if True:  # configured every run, so a change to q2.config always takes
        run('make', '-s', 'malta_defconfig', cwd=src)
        fragment = [line for line in (HERE/'q2.config').read_text().splitlines()]
        fragment += ['# CONFIG_MODVERSIONS is not set', '# CONFIG_MODULE_SIG is not set', '# CONFIG_CPU_BIG_ENDIAN is not set',
                     '# CONFIG_CPU_MIPS32_R2 is not set', '# CONFIG_PREEMPT_NONE is not set', '# CONFIG_PREEMPT_VOLUNTARY is not set']
        (work/'q2.fragment').write_text('\n'.join(fragment) + '\n')
        run('scripts/kconfig/merge_config.sh', '-m', '.config', work/'q2.fragment', cwd=src)
        run('make', '-s', 'olddefconfig', cwd=src)
        run('make', '-s', '-j8', 'modules_prepare', cwd=src)
    return src


def config_mismatches(src):
    """q2.config's settings the final .config did not take."""
    have = {}
    for line in (src/'.config').read_text().splitlines():
        if line.startswith('CONFIG_'):
            k, v = line.split('=', 1)
            have[k] = v
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            have[line[2:-11]] = 'n'
    bad = []
    for line in (HERE/'q2.config').read_text().splitlines():
        if line.startswith('CONFIG_'):
            k, v = line.split('=', 1)
        elif line.startswith('# CONFIG_') and line.endswith(' is not set'):
            k, v = line[2:-11], 'n'
        else:
            continue
        if have.get(k, 'n') != v:
            bad.append(f'{k}={v} (got {have.get(k, "n")})')
    return bad


def this_module(path):  # pragma: no mutate block (a built .ko or a vendor one, Shanling's)
    """(size of .gnu.linkonce.this_module, {symbol: offset} of its relocations, vermagic) of a .ko."""
    from elftools.elf.elffile import ELFFile  # type: ignore[import-not-found]  # pyelftools: here alone, so the tests import this without it
    from elftools.elf.relocation import RelocationSection  # type: ignore[import-not-found]
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        size = elf.get_section_by_name('.gnu.linkonce.this_module')['sh_size']
        syms = elf.get_section_by_name('.symtab')
        offs = {}
        for sec in elf.iter_sections():
            if isinstance(sec, RelocationSection) and sec.name.endswith('.gnu.linkonce.this_module'):
                for r in sec.iter_relocations():
                    offs[syms.get_symbol(r['r_info_sym']).name] = r['r_offset']
        info = elf.get_section_by_name('.modinfo').data().split(b'\0')
        vermagic = next((i[9:].decode() for i in info if i.startswith(b'vermagic=')), None)
        return size, offs, vermagic


def symbols(path):  # pragma: no mutate block (a built .ko)
    """(undefined, defined global) symbol names of a .ko."""
    from elftools.elf.elffile import ELFFile
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        und, defd = set(), set()
        for s in elf.get_section_by_name('.symtab').iter_symbols():
            if not s.name or s['st_info']['bind'] != 'STB_GLOBAL':
                continue
            (und if s['st_shndx'] == 'SHN_UNDEF' else defd).add(s.name)
        return und, defd


def kernel_exports(vmlinux):  # pragma: no mutate block (the stock kernel, Shanling's)
    out = subprocess.run(['llvm-nm', str(vmlinux)], check=True, capture_output=True, text=True).stdout
    names = {line.split()[-1] for line in out.splitlines() if line.split()}
    return {n[len('__ksymtab_'):] for n in names if n.startswith('__ksymtab_')}, names


def layout(path):  # pragma: no mutate block (a built q2_layout.ko)
    from elftools.elf.elffile import ELFFile
    with open(path, 'rb') as f:
        elf = ELFFile(f)
        syms = elf.get_section_by_name('.symtab')
        out = {}
        for s in syms.iter_symbols():
            if s.name.startswith(('q2_off_', 'q2_size_')):
                sec = elf.get_section(s['st_shndx'])
                d = sec.data()
                out[s.name] = int.from_bytes(d[s['st_value']:s['st_value'] + 4], 'little')
        return out


def main(argv):  # pragma: no mutate block (all of the above)
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--work', type=pathlib.Path, default=pathlib.Path.home()/'.cache/q2-uac1')
    ap.add_argument('--vmlinux', type=pathlib.Path, default=pathlib.Path.home()/'.cache/q2-uac1/k/vmlinux.elf')
    ap.add_argument('--stock-module', type=pathlib.Path,
                    default=pathlib.Path.home()/'q2-emu/stockfs/module_driver/soc_aic.ko')
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    toolchain(a.work)
    src = tree(a.work)
    bad = config_mismatches(src)
    mod = a.work/'module'
    mod.mkdir(exist_ok=True)
    for f in SOURCES:
        shutil.copyfile(src/'drivers/usb/gadget/function'/f, mod/f)
    # Upstream's own commits from 5.12, in order, on the module's copy of f_uac1.c (PATCHES)
    for p in PATCHES:
        run('patch', '-p5', '-F0', '--no-backup-if-mismatch', '-d', mod, '-i', HERE/'patches'/p)
    # u_audio's PCM buffer from vmalloc: 5.10.21 asks ALSA for contiguous pages at each hw_params, 64 kB (order 4) for
    # the player's buffer, and the stock kernel has no compaction to make such a block on a fragmented Q2 (128 MB, no
    # swap). u_audio copies the samples with the CPU (memcpy into each request), so the buffer need not be
    # contiguous. The stock kernel's ALSA has the type: snd_dma_alloc_pages calls __vmalloc (0x80597f90),
    # snd_dma_free_pages vfree (0x8059806c), and snd_pcm_mmap_data_fault vmalloc_to_page for type 7 (0x8058f66c).
    ua = mod/'u_audio.c'
    call = '\tsnd_pcm_set_managed_buffer_all(pcm, SNDRV_DMA_TYPE_CONTINUOUS,\n\t\t\t\t       NULL, 0, BUFF_SIZE_MAX);'
    text = ua.read_text()
    if text.count(call) != 1:
        sys.exit('build.py: u_audio.c has not the one snd_pcm_set_managed_buffer_all call 5.10.21 has')
    ua.write_text(text.replace(call, call.replace('SNDRV_DMA_TYPE_CONTINUOUS', 'SNDRV_DMA_TYPE_VMALLOC')))
    for f in ('Kbuild', 'q2_rename.h', 'q2_layout.c'):
        shutil.copyfile(HERE/f, mod/f)
    run('make', '-s', '-C', src, f'M={mod}', 'modules')
    ko = mod/'usb_f_uac1.ko'
    ok = True

    def check(cond, what):
        nonlocal ok
        print(('ok    ' if cond else 'FAIL  ') + what)
        ok &= bool(cond)

    check(not bad, 'q2.config taken whole' + (': ' + ', '.join(bad) if bad else ''))
    size, offs, vermagic = this_module(ko)
    ssize, soffs, svermagic = this_module(a.stock_module)
    check(vermagic == VERMAGIC == svermagic, f'vermagic "{vermagic}"')
    check(size == ssize, f'struct module {size:#x} bytes, stock {ssize:#x}')
    check(offs.get('init_module') == soffs.get('init_module') and offs.get('cleanup_module') == soffs.get('cleanup_module'),
          f'init at {offs.get("init_module", 0):#x}, exit at {offs.get("cleanup_module", 0):#x} '
          f'(stock {soffs.get("init_module", 0):#x}, {soffs.get("cleanup_module", 0):#x})')
    und, defd = symbols(ko)
    if a.vmlinux.exists():
        exports, names = kernel_exports(a.vmlinux)
        missing = sorted(und - exports)
        check(not missing, f'{len(und)} symbols needed, all exported by the stock kernel' +
              (f'; not exported: {", ".join(missing)}' if missing else ''))
        clash = sorted(defd & exports)
        check(not clash, 'no export clashes with the kernel' + (f': {", ".join(clash)}' if clash else ''))
    else:
        print(f'skip  symbol checks: no {a.vmlinux}')
    lay = layout(mod/'q2_layout.ko')
    # ep->maxpacket, a bit-field offsetof cannot name: the halfword q2_layout.c's reader loads (stock
    # usb_ep_set_maxpacket_limit 0x804ab334: sh $5, 0x1a($4))
    dis = subprocess.run([ENV['TC'] + '/bin/mipsel-linux-gnu-objdump', '-d', str(mod/'q2_layout.ko')], check=True,
                         capture_output=True, text=True,
                         env={'LD_LIBRARY_PATH': ENV['TC'] + '/root/usr/lib/x86_64-linux-gnu'}).stdout
    body = dis[dis.index('<q2_maxpacket>:'):].split('\n\n')[0]
    check('lhu' in body and ',26(' in body, 'ep->maxpacket read as the halfword at 0x1a, as stock writes it')
    wrong = [f'{k} {lay.get(k, -1):#x}, stock {v:#x} ({why})' for k, (v, why) in STOCK.items() if lay.get(k) != v]
    check(not wrong, f'{len(STOCK)} offsets agree with the stock kernel\'s code' + ('; ' + '; '.join(wrong) if wrong else ''))
    print(f'module: {ko} ({ko.stat().st_size} bytes)')
    for k, v in sorted(lay.items()):
        print(f'  {k} = {v:#x}' + ('  (stock-checked)' if k in STOCK else ''))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
