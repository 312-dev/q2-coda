#!/usr/bin/env python3
"""Radio's FM receiver (patch/fmrx.c, docs/internals.md#radio-static) against the approved prototype:
test/fmrx_test.c measures it as the prototype's measurements were made and this holds each figure to the
prototype's own (static-model.md's table, and where a scenario here differs from how that table was taken, the
prototype's fmRxWorklet run in Node on the same scenario, 2026-11-20: REFERENCE).

    python3 test/fmrx.py                 # build for this machine, measure, compare; prints the CPU per second of audio
    python3 test/fmrx.py --mips OUT      # the same measurer built for the Q2 at OUT (fmrx_test --bench 20 there)
"""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
# name: (expected dBFS or ratio, tolerance). From static-model.md's table unless marked (Node: the worklet itself).
REFERENCE = {
    'hiss_dbfs': (-25.9, 1.0), 'hiss_windows_median': (-26.0, 1.0), 'hiss_windows_p10': (-26.7, 1.0),
    'hiss_windows_min': (-28.4, 1.5), 'hiss_max_step': (0.084, 0.02),
    'hiss_spectrum_500': (0.0, 1.0), 'hiss_spectrum_2122': (-2.4, 1.0), 'hiss_spectrum_4000': (-6.1, 1.0),
    'hiss_spectrum_8000': (-10.6, 1.0), 'hiss_spectrum_15000': (-16.7, 1.5), 'hiss_spectrum_18000': (-31.0, 3.0),
    'noise_at_0': (-30.8, 1.5),   # Node (the table's -32 was taken another way)
    'noise_at_4': (-35.1, 1.5),   # Node (the table's -37.5)
    'noise_at_24': (-37.3, 1.0), 'noise_at_28': (-39.0, 1.0), 'noise_at_36': (-46.4, 1.0), 'noise_at_44': (-51.6, 1.0),
    'noise_at_60': (-54.1, 1.0), 'noise_at_80': (-68.2, 1.0),
    'tone_off0_out': (-12.1, 0.3), 'tone_off0_fund': (-12.1, 0.3), 'tone_off0_residue': (-66.0, 3.0),
    'tone_off100_fund': (-12.1, 0.5),  # "100 kHz clean"
    'tone_off150_fund': (-14.3, 1.0), 'tone_off150_second': (-30.0, 2.0), 'tone_off150_residue': (-24.0, 2.0),
    'tone_off175_fund': (-23.0, 1.5), 'tone_off175_residue': (-21.0, 1.5),
    'tone_off200_out': (-28.0, 3.0),
    'appear_20ms': (-27.4, 1.5),   # Node (the table's "-47 in 20 ms" was not this scenario: the level rises 70 ms)
    'appear_60ms': (-48.5, 1.5), 'appear_300ms': (-57.6, 1.5),  # Node
    'lost_40ms': (-27.6, 1.5),     # Node; the table: back to -26 in about 40 ms
    'fringe44_median': (-51.8, 1.0), 'fringe44_p99': (-42.4, 2.0),  # Node (the table: -52, -40)
    'fringe24_median': (-38.0, 1.0), 'fringe24_p99': (-37.0, 2.0),  # Node (the table: -36, -31)
    'paused_busy': (0, 0),
}
CC = ['-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter']


def build(out):
    exe = out/'fmrx_test'
    subprocess.run(['clang', *CC, ROOT/'test/fmrx_test.c', ROOT/'patch/fmrx.c', '-o', exe, '-lm'], check=True)
    return exe


def mips(out):
    """fmrx_test for the Q2, linked as tools/build.py links its helpers, against a stock rootfs's libc and libm in
    ~/.cache/q2-thirdparty/stockfs (tools/thirdparty.py extracts it)."""
    lib = pathlib.Path.home()/'.cache/q2-thirdparty/stockfs/lib'
    flags = ['--target=mipsel-linux-gnu', '-march=mips32r2', '-mabi=32', '-mfp64', '-fno-pic', '-ffreestanding',
             '-fno-builtin', '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables', '-O2',
             '-mnan=2008', '-mabs=2008', '-mabicalls', '-Wno-unused-parameter']
    with tempfile.TemporaryDirectory() as d:
        objs = []
        for src in ('test/fmrx_test.c', 'patch/fmrx.c', 'patch/start.c'):
            o = pathlib.Path(d)/(pathlib.Path(src).stem + '.o')
            subprocess.run(['clang', *flags, '-c', ROOT/src, '-o', o], check=True)
            objs.append(o)
        subprocess.run(['ld.lld', '-m', 'elf32ltsmip', '-e', '__start', '--dynamic-linker', '/lib/ld-linux-mipsn8.so.1',
                        '--image-base=0x400000', '-z', 'noexecstack', '--hash-style=sysv', '-s', *objs, f'-L{lib}',
                        '-l:libc-2.28.so', '-l:libm-2.28.so', '-o', out], check=True)


def main(argv):
    if argv[:1] == ['--mips']:
        return mips(pathlib.Path(argv[1]))
    with tempfile.TemporaryDirectory(prefix='q2-fmrx-') as d:
        exe = build(pathlib.Path(d))
        got = dict(line.split() for line in subprocess.run([exe], capture_output=True, text=True,
                                                            check=True).stdout.splitlines())
        bad = []
        for name, (want, tol) in REFERENCE.items():
            value = float(got[name])
            ok = abs(value - want) <= tol
            print(f'{"ok " if ok else "BAD"} {name:22} {value:8.2f}  (want {want} +/- {tol})')
            if not ok:
                bad.append(name)
        bench = subprocess.run([exe, '--bench', '10'], capture_output=True, text=True, check=True).stdout
        print(bench.strip())
        assert not bad, f'outside the prototype\'s figures: {", ".join(bad)}'
        print(f'{len(REFERENCE)} figures within the prototype\'s')


if __name__ == '__main__':
    main(sys.argv[1:])
