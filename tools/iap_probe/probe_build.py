#!/usr/bin/env python3
"""Build route 1's experiment 1 tooling for the Q2 (docs/iphone-mode.md): q2-iap-probe cross-compiled the way
tools/build.py's compile_helper builds the helpers (clang for MIPS32 little endian, linked by ld.lld against the stock
rootfs's own glibc, entered through patch/start.c), plus the gadget script and a test tone, into one directory that
q2iap pushes to the Q2's /tmp. Nothing here enters an update image.

    python3 tools/iap_probe/probe_build.py [--rootfs DIR] [--out DIR]

--rootfs is an unpacked stock rootfs holding lib/libc-2.28.so (default ~/q2-emu/stockfs); --out defaults to
~/.cache/q2-iap-probe/out."""
import argparse
import math
import pathlib
import shutil
import struct
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
# tools/build.py FLAGS for the helpers (compile_helper drops -mno-abicalls and -G0 and adds the rest)
CFLAGS = ['--target=mipsel-linux-gnu', '-march=mips32r2', '-mabi=32', '-mfp64', '-mnan=2008', '-mabs=2008', '-mabicalls',
          '-fno-pic', '-ffreestanding', '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables',
          '-fno-asynchronous-unwind-tables', '-Oz', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter']
LIBS = ['lib/libc-2.28.so', 'usr/lib/libsqlite3.so.0.8.6']  # SQLite: the library on iDrive (patch/q2_iap_lib.c)
SOURCES = ['q2_iap', 'iap2_link', 'iap2_names', 'iap2_hid', 'iap2_msg', 'iap2_xfer', 'iap2_library', 'iap2_nowplaying', 'iap2_session', 'q2_iap_lib']  # under patch/: tools/build.py's Q2IAP_SOURCES
TONE_RATE, TONE_HZ, TONE_DB, TONE_S = 44100, 1000, -20, 2


def run(*cmd):  # pragma: no mutate block (build()'s, the cross toolchain)
    subprocess.run([str(c) for c in cmd], check=True)


def tone():
    """TONE_S seconds of a TONE_HZ sine at TONE_DB dBFS, S16_LE stereo at TONE_RATE: a whole number of cycles, so
    aplay can loop it with no click."""
    amp = 32767 * 10 ** (TONE_DB / 20)
    frames = TONE_RATE * TONE_S
    return b''.join(struct.pack('<hh', s, s) for s in
                    (round(amp * math.sin(2 * math.pi * TONE_HZ * n / TONE_RATE)) for n in range(frames)))


def build(rootfs, out):  # pragma: no mutate block (clang, ld.lld and the stock rootfs's glibc: test/iap_probe.py)
    out.mkdir(parents=True, exist_ok=True)
    obj = out/'obj'
    obj.mkdir(exist_ok=True)
    for rel in LIBS:
        shutil.copyfile(rootfs/rel, obj/pathlib.Path(rel).name)
    objs = [obj/f'{s}.o' for s in (*SOURCES, 'start')]
    for s, o in zip((*SOURCES, 'start'), objs):
        run('clang', *CFLAGS, '-c', ROOT/'patch'/f'{s}.c', '-o', o)
    run('ld.lld', '-m', 'elf32ltsmip', '-e', '__start', '--dynamic-linker', '/lib/ld-linux-mipsn8.so.1',
        '--image-base=0x400000', '-z', 'noexecstack', '--gc-sections', '-s', '--hash-style=sysv', '--build-id=none',
        *objs, f'-L{obj}', *(f'-l:{pathlib.Path(r).name}' for r in LIBS), '-o', out/'q2-iap-probe')
    shutil.copyfile(HERE/'q2-iap-probe.sh', out/'q2-iap-probe.sh')
    (out/'q2-iap-tone.raw').write_bytes(tone())
    for f in ('q2-iap-probe', 'q2-iap-probe.sh'):
        (out/f).chmod(0o755)
    shutil.rmtree(obj)
    return out


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--rootfs', type=pathlib.Path, default=pathlib.Path.home()/'q2-emu/stockfs')
    ap.add_argument('--out', type=pathlib.Path, default=pathlib.Path.home()/'.cache/q2-iap-probe/out')
    a = ap.parse_args(argv)
    if not (a.rootfs/LIBS[0]).is_file():
        sys.exit(f'build.py: no {LIBS[0]} under {a.rootfs} (pass --rootfs, an unpacked stock rootfs)')
    print(build(a.rootfs, a.out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
