#!/usr/bin/env python3
"""The Bluetooth launcher (patch/bt_launch.sh, docs/bluez.md#the-launcher) in a build's own root filesystem, as
demo starts Bluetooth: `bluetoothd &`, then `bluealsa2 &` (the image's script, which runs `bluealsa`), each
with demo's stderr, on the image's own dbus-daemon, BusyBox and dbus-send, every MIPS program under qemu-mipsel
through binfmt in a bwrap chroot of the unpacked image. No radio and no Q2.

    python3 test/bt_launch.py BUILD [--launcher FILE] [--only NAME[,NAME]] [--keep]

BUILD is a build made with --own-bluez --own-bluealsa (any other prints skip and passes). --launcher runs another launcher in place of the build's
at every path it is installed at (how each check was seen to fail: a copy with one thing broken). bluetoothd
reaches test/bluez_own.py's stand-in for the kernel's management socket through its preloaded shim, so either
daemon starts and owns org.bluez; bluealsa has no stand-in and needs none to own org.bluealsa.

Scenarios, each in a fresh chroot with a fresh /tmp and config.ini:

  stock    no setting: stock's daemons, exec'd by the launcher with the caller's arguments; nothing watching;
           pidof finds one of each; stock's bluealsa-aplay and ALSA plugins; nothing logged; Bluetooth off
           (demo's killall -9) leaves nothing running
  own      BLUEZ=1, BLUEALSA=1: Vamp's daemons with their options, each the child of a bt-watch, which killall
           does not match; pidof finds the daemons alone; Vamp's bluealsa-aplay and plugins; "up" logged;
           Bluetooth off ends the watchers too, with no fallback
  notup    BLUEZ=1 and an own bluetoothd that never takes org.bluez: after UP seconds stock's owns it, the
           fallback is logged and marked, and the next Bluetooth on starts stock's directly
  crashes  BLUEALSA=1 and an own bluealsa that exits at once: started again twice, then stock's, logged and
           marked, and bluealsa-aplay and the plugins follow stock's
  restart  BLUEZ=1 and an own bluetoothd that exits once, then runs: started again, up, no fallback

Each launcher line on stderr must be a unified log line (patch/ulog_line.h's shape, source bt).
"""
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
import bluez_own  # noqa: E402  (its stand-in for the kernel, its shim, and tools/thirdparty.py's toolchain)

# where the build installs the launcher, and the daemons' paths (tools/build.py BT_WATCH, STOCK_*_DIR, OWN_*)
LAUNCHER_PATHS = ['usr/bin/bluetoothd', 'usr/bin/bluealsa', 'usr/bin/bluealsa-aplay', 'usr/lib/vamp-bt/bt-watch']
OWN_BLUEZ, STOCK_BLUEZ = '/usr/lib/vamp-bluez/bluetoothd', '/usr/lib/bluez-stock/bluetoothd'
OWN_BLUEALSA, STOCK_BLUEALSA = '/usr/lib/vamp-bluealsa/bluealsa', '/usr/lib/bluealsa-stock/bluealsa'
OWN_APLAY, STOCK_APLAY = OWN_BLUEALSA + '-aplay', STOCK_BLUEALSA + '-aplay'
# the own bluetoothd where restart's stand-in runs it from: still named bluetoothd, as demo's killall needs
REAL_BLUEZ = '/usr/lib/vamp-bluez/real/bluetoothd'
UP = 20  # patch/bt_launch.sh's
ULOG = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ \d+ [EWI] bt \S')
EXTRACT = ['bin', 'sbin', 'lib', 'usr/bin', 'usr/sbin', 'usr/lib', 'usr/share/alsa', 'etc', 'var']
BUS = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path=/run/dbus/system_bus_socket</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/>
<allow own="*"/><allow user="*"/></policy></busconfig>
"""
# The driver's helpers, in the image's BusyBox sh: owner NAME (the pid owning a bus name), waitown NAME S, and
# snap TAG (every process as "pid ppid comm cmdline", the two names' owners, the launcher's files)
PRELUDE = r'''
O=/tmp/out
mkdir -p $O /run/dbus
exec 2>>$O/err
dbus-daemon --config-file=/tmp/bus.conf --nofork 2>>$O/dbus.err &
i=0; while [ ! -S /run/dbus/system_bus_socket ] && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
owner() { dbus-send --system --print-reply --dest=org.freedesktop.DBus / \
    org.freedesktop.DBus.GetConnectionUnixProcessID string:$1 2>/dev/null | awk '/uint32/ { print $2 }'; }
waitown() { i=0; while [ $i -lt $2 ]; do [ "$(owner $1)" ] && return 0; sleep 1; i=$((i+1)); done; return 1; }
snap() {
    for p in /proc/[0-9]*; do
        c=$(tr '\0' ' ' < $p/cmdline 2>/dev/null) || continue
        echo "${p#/proc/} $(awk '{ print $4 }' $p/stat 2>/dev/null) $(cat $p/comm 2>/dev/null) $c"
    done > $O/$1.ps
    for n in org.bluez org.bluealsa; do echo "$n $(owner $n)"; done > $O/$1.own
    ls /tmp/q2-bt > $O/$1.files 2>/dev/null
    cat /tmp/q2-bt/bluealsa.which > $O/$1.which 2>/dev/null
    readlink /tmp/q2-bt/libasound_module_pcm_bluealsa.so > $O/$1.link 2>/dev/null
}
'''
DEMO_ON = 'bluetoothd &\nbluealsa2 &\n'
DEMO_OFF = 'killall -9 bluealsa; killall -9 bluetoothd; sleep 4\n'
SCENARIOS = {
    'stock': ('', DEMO_ON + 'waitown org.bluez 60; waitown org.bluealsa 60; sleep 3; snap up\n'
              f'bluealsa-aplay --version > $O/aplay 2>&1; {STOCK_APLAY} --version > $O/aplay.stock 2>&1\n'
              'LD_DEBUG=files aplay -q -D bluealsa -d 1 /dev/zero > $O/alsa 2>&1\n' + DEMO_OFF + 'snap off\n'),
    'own': ('BLUEZ=1\nBLUEALSA=1\n', DEMO_ON + 'waitown org.bluez 60; waitown org.bluealsa 60; sleep 3; snap up\n'
            f'bluealsa-aplay --version > $O/aplay 2>&1; {OWN_APLAY} --version > $O/aplay.own 2>&1\n'
            'LD_DEBUG=files aplay -q -D bluealsa -d 1 /dev/zero > $O/alsa 2>&1\n' + DEMO_OFF + 'snap off\n'),
    'notup': ('BLUEZ=1\n', 'bluetoothd &\nsleep 3; snap early\ni=0; while [ $i -lt 60 ]; do '
              '[ -e /tmp/q2-bt/bluez.fallback ] && [ "$(owner org.bluez)" ] && break; sleep 1; i=$((i+1)); done\n'
              'sleep 2; snap up\nkillall -9 bluetoothd; sleep 2; bluetoothd &\nwaitown org.bluez 60; sleep 2; snap again\n'
              'killall -9 bluetoothd; sleep 2\n'),
    'crashes': ('BLUEALSA=1\n', DEMO_ON + 'waitown org.bluez 60\ni=0; while [ $i -lt 60 ]; do '
                '[ -e /tmp/q2-bt/bluealsa.fallback ] && [ "$(owner org.bluealsa)" ] && break; sleep 1; i=$((i+1)); done\n'
                'sleep 2; snap up\n' + DEMO_OFF),
    'restart': ('BLUEZ=1\n', 'bluetoothd &\nwaitown org.bluez 60; sleep 3; snap up\n' + DEMO_OFF + 'snap off\n'),
}
# what stands in for the own daemon in a scenario: (its path, the script)
FAKES = {
    'notup': (OWN_BLUEZ, '#!/bin/sh\nexec sleep 600\n'),
    'crashes': (OWN_BLUEALSA, '#!/bin/sh\nexit 3\n'),
    'restart': (OWN_BLUEZ, '#!/bin/sh\n[ -e /tmp/crashed ] || { : > /tmp/crashed; exit 3; }\n'
                           f'exec {REAL_BLUEZ} "$@"\n'),
}

RESULTS = []


def check(label, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + label + (f': {detail}' if detail and not ok else ''), flush=True)
    RESULTS.append(ok)


class Seen:
    """One scenario's record: its snapshots, the launcher's stderr lines"""
    def __init__(self, out):
        self.out = out
        self.err = (out / 'err').read_text(errors='replace') if (out / 'err').exists() else ''
        self.lines = [line for line in self.err.splitlines() if ' bt ' in line]

    def text(self, name):
        f = self.out / name
        return f.read_text(errors='replace').strip() if f.exists() else ''

    def ps(self, tag):
        """{pid: (ppid, comm, cmdline)}"""
        rows = {}
        for line in self.text(f'{tag}.ps').splitlines():
            parts = line.split(' ', 3)
            if len(parts) == 4:
                rows[parts[0]] = (parts[1], parts[2], parts[3])
        return rows

    def owner(self, tag, name):
        """(pid, ppid, comm, cmdline) of name's owner, or None"""
        for line in self.text(f'{tag}.own').splitlines():
            n, _, pid = line.partition(' ')
            if n == name and pid:
                return (pid, *self.ps(tag).get(pid, ('?', '?', '?')))
        return None

    def comms(self, tag, comm):
        return [p for p, (_, c, _) in self.ps(tag).items() if c == comm]


def run(scenario, rootfs, tmp, launcher, shim, keep):
    config, script = SCENARIOS[scenario]
    work = tmp / scenario
    shutil.rmtree(work, ignore_errors=True)
    (work / 'tmp/out').mkdir(parents=True)
    (work / 'run').mkdir()
    data = work / 'data'
    (data / 'bluetooth').mkdir(parents=True)
    (data / 'config.ini').write_text('[SYSSET]\nLANGUAGE=2\n' + (f'[Q2POD]\n{config}' if config else ''))
    (work / 'tmp/bus.conf').write_text(BUS)
    kernel = bluez_own.Kernel(work / 'tmp/mgmt')
    binds = []
    if launcher:
        for path in LAUNCHER_PATHS:
            binds += ['--ro-bind', launcher, '/' + path]
    if scenario in FAKES:
        path, body = FAKES[scenario]
        fake = work / 'fake'
        fake.write_text(body)
        fake.chmod(0o755)
        binds += ['--ro-bind', fake, path]
        if REAL_BLUEZ in body:
            binds += ['--ro-bind', rootfs / path.lstrip('/'), REAL_BLUEZ]
    cmd = ['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-user', '--uid', '0', '--gid', '0',
           '--bind', rootfs, '/', '--proc', '/proc', '--dev', '/dev', '--bind', work / 'tmp', '/tmp',
           '--bind', work / 'run', '/run', '--bind', data, '/mnt/data',
           '--ro-bind', shutil.which('qemu-mipsel-static'), '/usr/libexec/qemu-binfmt/mipsel-binfmt-P',
           '--ro-bind', shim, '/lib/libq2btshim.so', *binds,
           '--setenv', 'PATH', '/bin:/sbin:/usr/bin:/usr/sbin', '--setenv', 'HOME', '/root',
           '--setenv', 'LD_PRELOAD', '/lib/libq2btshim.so', '--setenv', 'Q2_FAKE_MGMT', '/tmp/mgmt',
           '--chdir', '/', '/bin/sh', '-c', PRELUDE + script]
    env = {k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'}
    try:
        r = subprocess.run([str(c) for c in cmd], env=env, timeout=400, capture_output=True, text=True)
        if r.returncode:
            print(f'{scenario}: the chroot ended {r.returncode}: {r.stderr[-400:]}')
    finally:
        kernel.close()
    seen = Seen(work / 'tmp/out')
    if keep:
        print(f'{scenario}: kept in {work}')
    return seen


def lines_are_unified(s, scenario):
    bad = [line for line in s.lines if not ULOG.match(line)]
    check(f'{scenario}: every launcher line on stderr is a unified log line', not bad, str(bad[:2]))


def stock(s):
    o = s.owner('up', 'org.bluez')
    check('stock: org.bluez is stock\'s bluetoothd, exec\'d by the launcher with demo\'s arguments',
          bool(o) and o[2] == 'bluetoothd' and STOCK_BLUEZ in o[3] and o[3].rstrip().endswith(STOCK_BLUEZ), str(o))
    a = s.owner('up', 'org.bluealsa')
    check('stock: org.bluealsa is stock\'s bluealsa with bluealsa2\'s arguments',
          bool(a) and a[2] == 'bluealsa' and STOCK_BLUEALSA in a[3] and '--keep-alive=5' in a[3]
          and '--shanling' not in a[3], str(a))
    check('stock: nothing watches them (no bt-watch)', not s.comms('up', 'bt-watch'), str(s.comms('up', 'bt-watch')))
    check('stock: pidof finds one bluetoothd and one bluealsa',
          len(s.comms('up', 'bluetoothd')) == 1 and len(s.comms('up', 'bluealsa')) == 1)
    check('stock: bluealsa-aplay is stock\'s', s.text('aplay') and s.text('aplay') == s.text('aplay.stock'),
          f'{s.text("aplay")!r} against {s.text("aplay.stock")!r}')
    check('stock: the ALSA plugins are stock\'s, through the launcher\'s link',
          s.text('up.which') == 'stock' and s.text('up.link') == '/usr/lib/alsa-lib/libasound_module_pcm_bluealsa.so'
          and '/tmp/q2-bt/libasound_module_pcm_bluealsa.so' in s.text('alsa'), s.text('up.link'))
    check('stock: the launcher logs nothing', not s.lines, str(s.lines[:2]))
    left = [c for c in ('bluetoothd', 'bluealsa', 'bt-watch') if s.comms('off', c)]
    check('stock: Bluetooth off (killall -9) leaves nothing running', not left, str(left))


def own(s):
    o = s.owner('up', 'org.bluez')
    check('own: org.bluez is Vamp\'s bluetoothd with its plugins left out',
          bool(o) and o[2] == 'bluetoothd' and OWN_BLUEZ in o[3] and '--noplugin=hfp,rap,fastpair' in o[3], str(o))
    a = s.owner('up', 'org.bluealsa')
    check('own: org.bluealsa is Vamp\'s bluealsa with stock\'s roles, the compatibility service and bluealsa2\'s options',
          bool(a) and a[2] == 'bluealsa' and OWN_BLUEALSA in a[3] and all(
              w in a[3] for w in ('--shanling', '-p a2dp-source', '-p a2dp-sink', '-p hfp-ag', '--keep-alive=5',
                                  '--aac-vbr', '-c LDAC')), str(a))
    ps = s.ps('up')
    for o2, n in ((o, 'bluetoothd'), (a, 'bluealsa')):
        parent = ps.get(o2[1], ('', '', '')) if o2 else ('', '', '')
        check(f'own: {n}\'s parent is a bt-watch', parent[1] == 'bt-watch', str(parent))
    check('own: pidof finds one bluetoothd and one bluealsa, the daemons',
          bool(o and a) and s.comms('up', 'bluetoothd') == [o[0]] and s.comms('up', 'bluealsa') == [a[0]],
          f"{s.comms('up', 'bluetoothd')} {s.comms('up', 'bluealsa')}")
    check('own: bluealsa-aplay is Vamp\'s', s.text('aplay') and s.text('aplay') == s.text('aplay.own'),
          f'{s.text("aplay")!r} against {s.text("aplay.own")!r}')
    check('own: the ALSA plugins are Vamp\'s', s.text('up.which') == 'own'
          and s.text('up.link') == '/usr/lib/vamp-bluealsa/libasound_module_pcm_bluealsa.so', s.text('up.link'))
    check('own: each daemon\'s start and its name taken are logged',
          sum('up after' in line for line in s.lines) == 2 and sum("Vamp's" in line for line in s.lines) == 2,
          str(s.lines))
    left = [c for c in ('bluetoothd', 'bluealsa', 'bt-watch') if s.comms('off', c)]
    check('own: Bluetooth off ends the daemons and their watchers, with no restart', not left, str(left))
    check('own: no fallback', not any(f.endswith('.fallback') for f in s.text('off.files').split())
          and not any(' E ' in line or 'again' in line for line in s.lines), s.text('off.files'))


def started(s):
    """The pids the launcher logged starting Vamp's daemon at"""
    return [m.group(1) for line in s.lines if (m := re.search(r"Vamp's \S+, pid (\d+)$", line))]


def notup(s):
    early, pids = s.ps('early'), started(s)
    check('notup: Vamp\'s stand-in ran, the child of a bt-watch',
          len(pids) == 1 and pids[0] in early and early.get(early[pids[0]][0], ('', ''))[1] == 'bt-watch',
          f'{pids} {early.get(pids[0]) if pids else None}')
    o = s.owner('up', 'org.bluez')
    check(f'notup: stock\'s bluetoothd owns org.bluez after {UP} s', bool(o) and STOCK_BLUEZ in o[3], str(o))
    check('notup: the stand-in is gone', bool(pids) and pids[0] not in s.ps('up'))
    check('notup: the fallback is logged as an error and marked',
          any(' E bt ' in line and f'{UP}s' in line and 'org.bluez' in line for line in s.lines)
          and 'bluez.fallback' in s.text('up.files').split(), str(s.lines))
    o = s.owner('again', 'org.bluez')
    check('notup: the next Bluetooth on starts stock\'s directly', bool(o) and STOCK_BLUEZ in o[3]
          and not s.comms('again', 'bt-watch') and sum("Vamp's" in line for line in s.lines) == 1, str(o))


def crashes(s):
    a = s.owner('up', 'org.bluealsa')
    check('crashes: stock\'s bluealsa owns org.bluealsa', bool(a) and STOCK_BLUEALSA in a[3], str(a))
    again = [line for line in s.lines if 'started again' in line]
    check('crashes: started again twice, then the fallback, logged and marked',
          len(again) == 2 and all(' W bt ' in line and 'status 3' in line for line in again)
          and any(' E bt ' in line and 'ended 3 times' in line for line in s.lines)
          and 'bluealsa.fallback' in s.text('up.files').split(), str(s.lines))
    check('crashes: bluealsa-aplay and the plugins follow stock\'s', s.text('up.which') == 'stock'
          and s.text('up.link') == '/usr/lib/alsa-lib/libasound_module_pcm_bluealsa.so', s.text('up.link'))


def restart(s):
    o = s.owner('up', 'org.bluez')
    check('restart: Vamp\'s bluetoothd owns org.bluez after one end', bool(o) and REAL_BLUEZ in o[3], str(o))
    check('restart: the end is logged once, as a warning, and nothing falls back',
          sum('started again' in line and ' W bt ' in line for line in s.lines) == 1
          and not any(' E bt ' in line for line in s.lines) and 'bluez.fallback' not in s.text('up.files').split(),
          str(s.lines))
    check('restart: Bluetooth off ends it and its watcher', not s.comms('off', 'bt-watch') and not s.comms('off', 'bluetoothd'))


CHECKS = {'stock': stock, 'own': own, 'notup': notup, 'crashes': crashes, 'restart': restart}


def main(argv):
    keep = '--keep' in argv
    values = {argv[i + 1] for i, a in enumerate(argv[:-1]) if a in ('--launcher', '--only')}
    launcher = argv[argv.index('--launcher') + 1] if '--launcher' in argv else None
    only = argv[argv.index('--only') + 1].split(',') if '--only' in argv else list(SCENARIOS)
    args = [a for a in argv if not a.startswith('--') and a not in values]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    for tool in ('qemu-mipsel-static', 'bwrap', 'unsquashfs'):
        if not shutil.which(tool):
            print(f'skip: no {tool} on this machine')
            return 0
    build = pathlib.Path(args[0]).resolve()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='q2-bt-launch-'))  # tmpfs: a 32-bit readdir on ext4 fails
    try:
        rootfs = tmp / 'rootfs'
        subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', rootfs, build / 'rootfs.squashfs', *EXTRACT],
                       check=True, stdout=subprocess.DEVNULL)
        for d in ('run', 'tmp', 'proc', 'dev', 'mnt/data', 'usr/libexec/qemu-binfmt'):
            (rootfs / d).mkdir(parents=True, exist_ok=True)
        (rootfs / 'usr/libexec/qemu-binfmt/mipsel-binfmt-P').touch()
        (rootfs / 'lib/libq2btshim.so').touch()
        missing = [p for p in LAUNCHER_PATHS + [OWN_BLUEZ[1:], STOCK_BLUEZ[1:], OWN_BLUEALSA[1:], STOCK_BLUEALSA[1:]]
                   if not (rootfs / p).exists()]
        if missing:  # the gate runs every build through here; only these carry the launcher
            print(f'skip: {build} is not an --own-bluez --own-bluealsa build (no {", ".join(missing)})')
            return 0
        (rootfs / REAL_BLUEZ[1:]).parent.mkdir(parents=True, exist_ok=True)
        (rootfs / REAL_BLUEZ[1:]).touch()
        env = bluez_own.thirdparty.Env({}, 0)
        (tmp / 'shim.c').write_text(bluez_own.SHIM)
        shim = tmp / 'shim.so'
        subprocess.run([*env.cc.split(), '-shared', '-fPIC', '-O1', '-o', str(shim), str(tmp / 'shim.c'), '-ldl'],
                       check=True)
        for scenario in only:
            seen = run(scenario, rootfs, tmp, launcher and pathlib.Path(launcher).resolve(), shim, keep)
            CHECKS[scenario](seen)
            lines_are_unified(seen, scenario)
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    print(f'{sum(RESULTS)} of {len(RESULTS)} checks passed')
    return 0 if RESULTS and all(RESULTS) else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
