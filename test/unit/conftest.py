"""Import tools/ as the tools package, under the plain names its scripts use for each other, and keep
every test away from real processes, sockets and devices.

mutmut only switches on a mutant whose module is tools.<name>, and tools/ imports `build`, not
tools.build. The alias makes both one module, so a test can `import build` as test/*.py do. This
folder has its own rootdir entry on sys.path, so test/build.py can't shadow tools/build.py here."""
import ctypes
import importlib
import os
import pathlib
import resource
import select
import socket
import subprocess
import sys
import threading
import time
import traceback

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
# A mutant can loop forever, growing a list (art's path() with `i = 1` for `i += 1` took 500 MB a second),
# and fill the machine long before mutmut's timeout. Over 4 GB a process raises MemoryError: killed.
# mutmut sets MUTANT_UNDER_TEST before any test session; elsewhere ASan's shadow memory is over 4 GB already.
# A thread reserves its stack in that 4 GB, RLIMIT_STACK's 8 MB: q2remote.find's 64 probe threads took
# 512 MB, past the cap in mutmut's stats run on CI ("can't start new thread"); at 1 MB they take 64.
# Each of those threads also gets its own glibc malloc arena, 64 MB of address space, up to eight per core:
# on 16 cores they took the stats run from 1.4 GB to the cap. Four arenas are shared between them.
if 'MUTANT_UNDER_TEST' in os.environ:
    _, HARD = resource.getrlimit(resource.RLIMIT_AS)
    resource.setrlimit(resource.RLIMIT_AS, (4 << 30 if HARD == resource.RLIM_INFINITY else min(4 << 30, HARD), HARD))
    threading.stack_size(1 << 20)
    ctypes.CDLL(None).mallopt(-8, 4)  # M_ARENA_MAX
sys.path.insert(0, str(ROOT))
# build first: the others import it, or each other in this order, while they load.
for name in ('build', 'signing', 'shelf', 'module_elf', 'rotator', 'ipod', 'theme', 'art', 'peq', 'aic', 'mcu_park', 'pwm', 'q2remote', 'release', 'rebase', 'lastfm_key', 'player', 'scan', 'thirdparty', 'coda_rc_crypto', 'coda_rc_client', 'splash', 'radiodata'):
    sys.modules[name] = importlib.import_module('tools.' + name)

# The compilers a test may run, and openssl, which tools/signing.py signs and checks with in files
# a test made. A mutant changes these tools' commands before they run, and q2remote's are shell
# scripts for a live Q2, reboot among them, so nothing else may execute.
COMPILERS = {'clang', 'ld.lld', 'llvm-objcopy', 'llvm-nm', 'readelf', 'openssl'}
REAL_POPEN = subprocess.Popen


class Guarded(REAL_POPEN):
    def __init__(self, args, *rest, shell=False, **kwargs):
        program = None if shell or isinstance(args, (str, bytes)) else pathlib.PurePath(str(args[0])).name
        if program not in COMPILERS:
            raise AssertionError(f'a test tried to run {args!r}')
        super().__init__(args, *rest, **kwargs)


def refuse(what):
    def refused(*args, **kwargs):
        raise AssertionError(f'a test tried {what}{args!r}')
    return refused


@pytest.fixture(autouse=True, scope='session')
def uncached():
    """mutmut runs each mutant's tests in a fork of a process that has run them all, so a cached function's
    answers from the unmutated code would hide every mutant under it (a functools.cache in tools/ or a test).
    Once a session: within it the cache is the mutant's own, and art's tests stay fast."""
    for m in [m for name, m in sys.modules.items() if name.startswith(('tools.', 'test_'))]:
        for f in list(vars(m).values()):
            if hasattr(f, 'cache_clear'):
                f.cache_clear()


@pytest.fixture(autouse=True)
def contained(monkeypatch):
    monkeypatch.setattr(subprocess, 'Popen', Guarded)
    for name in ('system', 'popen', 'kill', 'killpg', 'execv', 'execve', 'execvp', 'execvpe', 'spawnv', 'spawnvp'):
        monkeypatch.setattr(os, name, refuse(f'os.{name}'))
    monkeypatch.setattr(socket.socket, 'connect', refuse('socket.connect'))
    monkeypatch.setattr(socket.socket, 'connect_ex', refuse('socket.connect_ex'))
    monkeypatch.setattr(socket, 'create_connection', refuse('socket.create_connection'))
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    for name in ('Q2_HOST', 'Q2_KEY', 'Q2_KEY_FILE', 'Q2_BUILD', 'Q2_PROF_BIN'):
        monkeypatch.delenv(name, raising=False)


# A stall's record (STATUS.md: a test timing out at 60 s inside a plain read of a tracked file, seen only in a full run
# after test/sendd.py). With Q2_STALL_DIR set, a test still running at Q2_STALL_S seconds (50, before pytest-timeout's
# 60) has written there: every thread's Python stack, and per OS thread its state, kernel wait channel and system call
# with its arguments (/proc/self/task/*/syscall: a read's first argument is its fd), each open fd's target, and the mount
# each target is on, so the record names the file, its mount (the container's virtiofs share or not) and where the read
# waits. The test's own thread comes first: its Python stack (faulthandler stops at 100 threads, and a full run has more,
# its leftover stand-ins' accept threads), then ten samples half a second apart of its state, wait channel, system call and
# /proc I/O counters (rchar, syscr), which tell one request that never returns (the counters still) from many slow ones
# (the counters climbing). Nothing is retried or hidden: the test goes on to its timeout as before.
@pytest.fixture(autouse=True)
def _stall_record(request):
    where = os.environ.get('Q2_STALL_DIR')
    if not where:
        yield
        return
    import faulthandler

    def record():
        out = pathlib.Path(where) / f'stall-{time.strftime("%Y%m%d-%H%M%S")}-{os.getpid()}.txt'
        with open(out, 'w') as f:
            f.write(f'{request.node.nodeid}\nat {time.strftime("%Y-%m-%d %H:%M:%S %z")}\n\n')
            main = threading.main_thread()
            frame = sys._current_frames().get(main.ident or 0)
            f.write('test thread stack:\n' + ''.join(traceback.format_stack(frame)) + '\ntest thread samples:\n')
            task = pathlib.Path('/proc/self/task') / str(main.native_id)
            for _ in range(10):
                got = []
                for n in ('wchan', 'syscall', 'io'):
                    try:
                        text = (task / n).read_text().strip()
                    except OSError as e:
                        text = f'({e.strerror})'
                    if n == 'io':
                        text = ' '.join(l for l in text.splitlines() if l.startswith(('rchar', 'syscr')))
                    got.append(f'{n} {text}')
                f.write(f'  {time.monotonic():.1f}: ' + ', '.join(got) + '\n')
                f.flush()
                select.select([], [], [], 0.5)  # time.sleep may be a test's stand-in
            f.write('\n')
            for task in sorted(pathlib.Path('/proc/self/task').iterdir()):
                fields = {}
                for n in ('comm', 'wchan', 'syscall'):
                    try:
                        fields[n] = (task / n).read_text().strip()
                    except OSError as e:
                        fields[n] = f'({e.strerror})'
                try:
                    state = next(l for l in (task / 'status').read_text().splitlines() if l.startswith('State:'))
                except (OSError, StopIteration):
                    state = 'State: ?'
                f.write(f'task {task.name} {fields["comm"]}: {state}, wchan {fields["wchan"]}, syscall {fields["syscall"]}\n')
            f.write('\nfds:\n')
            for fd in sorted(pathlib.Path('/proc/self/fd').iterdir(), key=lambda p: int(p.name)):
                try:
                    f.write(f'  {fd.name} -> {os.readlink(fd)}\n')
                except OSError:
                    pass
            f.write('\nmounts:\n' + pathlib.Path('/proc/self/mountinfo').read_text() + '\nstacks:\n')
            f.flush()
            faulthandler.dump_traceback(file=f, all_threads=True)

    timer = threading.Timer(float(os.environ.get('Q2_STALL_S', '50')), record)
    timer.daemon = True
    timer.start()
    yield
    timer.cancel()
