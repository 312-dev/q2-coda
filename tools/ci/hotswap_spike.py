# Spike (2026-11-16) for tools/ci/q2live.py hot swap: run as python3 -P tools/ci/hotswap_spike.py BUILD with pool copy 1 leased.
# Boots the hotswap build under qemu's gdbstub on pool copy 1, runs it to Home, stops it at a stock idle point (a
# breakpoint on main_loop_sleep_default's address), reports every thread's pc and ra, changes the payload's
# "Resume Songs" literal in memory, and leaves demo running for a screenshot of that page.
import sys, time, subprocess, os, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent)); import gdbrsp  # noqa: E402
B = sys.argv[1]; E = pathlib.Path.home()/'q2-emu-pool1'; PORT = 2461; IDLE = 0x65c470
env = {**os.environ, 'Q2EMU_GDB': str(PORT), 'Q2EMU_PORT': '2401'}
subprocess.Popen(['setsid', str(E/'run.sh'), B], stdout=open(E/'state/run.out', 'w'), stderr=subprocess.STDOUT, env=env, start_new_session=True)
subprocess.Popen(['setsid', 'python3', str(E/'viewer.py'), 'serve', '2401'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
r = gdbrsp.connect(PORT)
r.cmd('?'); r.cont(); time.sleep(12)
t = time.monotonic()
# a breakpoint must be set while stopped: qemu-user reads the socket only when the guest stops, so stop it with
# a real signal (SIGINT to the qemu process), which the stub reports
pid = gdbrsp.demo_pid(E); print('our demo', pid); assert pid
os.kill(pid, 2); print('stop by SIGINT:', r.wait_stop(10), f'{time.monotonic()-t:.2f} s')
print('Z0:', r.cmd('Z0,%x,4' % IDLE)); r.cont(); print('idle stop:', r.wait_stop(10), f'{time.monotonic()-t:.2f} s'); print('z0:', r.cmd('z0,%x,4' % IDLE))
ths = r.threads(); print(len(ths), 'threads')
for th in ths:
    g = r.regs(th); pc, ra = g[37], g[31]
    print(' ', th, 'pc %08x ra %08x' % (pc, ra), 'PC-IN-PAYLOAD' if 0xb00000 <= pc < 0xc80000 else '', 'RA-IN-PAYLOAD' if 0xb00000 <= ra < 0xc80000 else '')
mem = r.read(0xb00000, 0x180000)
at = mem.find(b'Resume Songs\0'); print('literal at %#x' % (0xb00000 + at))
w = time.monotonic(); r.write(0xb00000 + at, b'Resume SongZ\0'); print(f'write {time.monotonic()-w:.3f} s; read back', r.read(0xb00000 + at, 13))
r.cont(); print(f'paused {time.monotonic()-t:.2f} s in all'); time.sleep(1); r.close()
