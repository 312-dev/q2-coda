#!/usr/bin/env python3
"""q2send against the reference LocalSend app (docs/localsend.md): patch/localsend.c and localsend_core.c built for
this machine (host OpenSSL), run as a program with --run, --data and --recv in a temporary folder, beside the
LocalSend CLI driven through a pseudo-terminal as a user at its keyboard. This script stands in for demo: it reads
q2send.state and q2send.ask and writes q2send.answer and q2send.cmd.

usage: localsend_e2e.py CLI|BUILD [--keep DIR] [--q2send 'COMMAND']

CLI is the LocalSend CLI program (LocalSend-CLI-1.18.2-linux-x86-64, its SHA-256 f5a986e0...2b08 the release's).
Given a build folder instead (how tools/ci/gate.py runs a test/ script), the CLI is $Q2_LOCALSEND_CLI, else
~/.cache/q2-localsend/localsend-cli, the machine's copy; with neither the run says so and passes, as the CLI is not
in the repository.
--q2send runs that command for q2send instead of building it, for the build's own MIPS program under
qemu-mipsel: --q2send 'qemu-mipsel -L ROOTFS ROOTFS/usr/bin/q2send' (ROOTFS holding at least the build's lib,
usr/lib/libssl.so.1.1, usr/lib/libcrypto.so.1.1 and usr/bin/q2send). Both bind multicast's 53317 beside each other and serve on ports of their own (Q2PORT, CLIPORT).

Checks, in order: each finds the other (the CLI's announcement answered by q2send's register, and q2send's
announcement answered by the CLI's); the CLI sends a file, demo is asked and accepts, the file lands whole in
Received and is listed in q2send.received; the CLI sends again and demo declines (the CLI is refused, nothing
lands); with q2send.hold present a request is refused without asking; accepted with "Always accept", the CLI is
listed as trusted by its certificate's fingerprint and its next request lands without asking, while the same name
with a new certificate is asked again, and untrust takes it off the list; q2send sends two files to the CLI, whose
user accepts, and both land whole there; the same send declined at the CLI ends "declined"; SIGTERM ends q2send
with its state, ask and pid files gone. The exit status is the number of failures."""
import argparse
import fcntl
import os
import pathlib
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
Q2PORT, CLIPORT = 53331, 53332
FAILS = []


def check(name, ok, detail=''):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)
    return ok


def until(fn, secs, step=0.1, pump=None):
    end = time.time() + secs
    while time.time() < end:
        v = fn()
        if v:
            return v
        if pump:
            pump(step)
        else:
            time.sleep(step)
    return fn()


class Cli:
    """The reference CLI in a 120x40 pseudo-terminal; out is all it has drawn."""

    def __init__(self, exe, work, files=(), config='cli-config'):
        self.dest = work / 'cli-received'
        self.dest.mkdir(exist_ok=True)
        env = dict(os.environ, XDG_CONFIG_HOME=str(work / config), HOME=str(work), TERM='xterm')
        args = [exe, '--alias', 'Reference CLI', '--port', str(CLIPORT), '--destination', str(self.dest)]
        for f in files:
            args += ['-f', str(f)]
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.execve(exe, args, env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 120, 0, 0))
        self.out = b''

    def pump(self, secs):
        end = time.time() + secs
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    self.out += os.read(self.fd, 65536)
                except OSError:
                    return

    def key(self, k):
        os.write(self.fd, k.encode())

    def stop(self):
        Cli.n = getattr(Cli, 'n', 0) + 1
        (self.dest.parent / f'cli-screen-{Cli.n}.txt').write_bytes(self.out)
        try:
            os.kill(self.pid, signal.SIGTERM)
            os.waitpid(self.pid, 0)
        except OSError:
            pass


class Q2:
    def __init__(self, exe, work):
        self.run, self.data, self.recv = work / 'run', work / 'data', work / 'mmc/Received'
        self.run.mkdir()
        (work / 'mmc').mkdir()
        self.p = subprocess.Popen([*exe, '--run', str(self.run), '--data', str(self.data), '--recv', str(self.recv),
                                   '--port', str(Q2PORT), '--alias', 'Vamp'])

    def f(self, name):
        return self.run / f'q2send.{name}'

    def put(self, name, text):
        tmp = self.f(name + '.tmp')
        tmp.write_text(text)
        tmp.rename(self.f(name))

    def state(self):
        try:
            lines = self.f('state').read_text().splitlines()
        except OSError:
            return {}
        out = {'peers': []}
        for ln in lines:
            w = ln.split('\t')
            if w[0] == 'peer':
                out['peers'].append(w[1:])
            elif w[0] in ('recv', 'send'):
                out[w[0]] = w[1:]
            elif w[0] == 'me':
                out['me'] = w[1:]
        return out

    def trusted(self):
        try:
            return [ln.split('\t')[1:] for ln in self.f('state').read_text().splitlines() if ln.startswith('trusted\t')]
        except OSError:
            return []

    def phase(self, which):
        return self.state().get(which, ['?'])[0]


def build(out):
    exe = out / 'q2send'
    subprocess.run(['clang', '-DLS_HOST', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-deprecated-declarations', '-I', str(ROOT / 'patch'), str(ROOT / 'patch/localsend.c'),
                    str(ROOT / 'patch/localsend_core.c'), '-lssl', '-lcrypto', '-lpthread', '-o', str(exe)], check=True)
    return exe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cli')
    ap.add_argument('--keep')
    ap.add_argument('--q2send')
    a = ap.parse_args()
    if (pathlib.Path(a.cli) / 'manifest.json').exists():  # a build: the machine's CLI
        cli = pathlib.Path(os.environ.get('Q2_LOCALSEND_CLI', pathlib.Path.home() / '.cache/q2-localsend/localsend-cli'))
        if not cli.is_file():
            print(f'skip: no LocalSend CLI at {cli} (set Q2_LOCALSEND_CLI)')
            return 0
        a.cli = str(cli)
    work = pathlib.Path(a.keep or tempfile.mkdtemp(prefix='localsend-e2e-'))
    work.mkdir(parents=True, exist_ok=True)
    exe = a.q2send.split() if a.q2send else [str(build(work))]
    payload = work / 'to-q2.flac'
    payload.write_bytes(os.urandom(300_000))
    q2 = Q2(exe, work)
    cli = Cli(a.cli, work, files=[payload])
    try:
        st = until(lambda: q2.state().get('me'), 5)
        check('q2send starts and writes its state', bool(st), str(st))
        q2fp = st[0] if st else ''
        q2.put('cmd', 'scan\n')
        peer = until(lambda: [p for p in q2.state().get('peers', []) if p[4] == 'Reference CLI'], 8, pump=cli.pump)
        check('q2send lists the CLI', bool(peer), str(peer))
        clifp = peer[0][0] if peer else ''
        cli.pump(1)
        check('the CLI lists q2send', b'Vamp' in cli.out)

        # the CLI sends (it was started with -f: its device list is open; Enter sends to the first)
        cli.key('\r')
        ask = until(lambda: q2.f('ask').exists() and q2.f('ask').read_text(), 10, pump=cli.pump)
        w = ask.rstrip('\n').split('\t') if ask else []
        check('demo is asked: who, how many, how big', len(w) == 6 and w[1] == 'Reference CLI' and w[4:] ==
              ['1', str(payload.stat().st_size)], repr(ask))
        check('state says ask', until(lambda: q2.phase('recv') == 'ask', 2))
        q2.put('answer', f'{w[0]} 1\n' if w else 'x 1\n')
        got = q2.recv / payload.name
        until(lambda: q2.phase('recv') == 'done', 15, pump=cli.pump)
        check('accepted: the file lands whole in Received', got.exists() and got.read_bytes() == payload.read_bytes())
        check('and is listed for the library scan', q2.f('received').exists() and
              q2.f('received').read_text().strip() == str(got))
        check('no .part file is left', not list(q2.recv.rglob('*.part')))
        rs = q2.state().get('recv', [])
        check('state: done, 1 of 1, every byte, not trusted', rs[-1:] == ['0'] and rs[:5] == ['done', '1', '1', str(payload.stat().st_size),
                                                           str(payload.stat().st_size)], str(rs))
        cli.stop()

        # again, declined
        payload2 = work / 'second.txt'
        payload2.write_text('second\n' * 100)
        cli = Cli(a.cli, work, files=[payload2])
        q2.put('cmd', 'scan\n')
        cli.pump(4)
        cli.key('\r')
        ask = until(lambda: q2.f('ask').exists() and q2.f('ask').read_text(), 10, pump=cli.pump)
        sid = ask.split('\t')[0] if ask else 'x'
        q2.put('answer', f'{sid} 0\n')
        until(lambda: q2.phase('recv') == 'declined', 5, pump=cli.pump)
        cli.pump(1)
        check('declined: nothing lands, the state says declined', not (q2.recv / payload2.name).exists() and
              q2.phase('recv') == 'declined')
        check('the ask file is gone once answered', not q2.f('ask').exists())
        cli.stop()

        # held (the card is a computer's over USB): refused without asking
        (q2.f('hold')).write_text('')
        cli = Cli(a.cli, work, files=[payload2])
        q2.put('cmd', 'scan\n')
        cli.pump(4)
        cli.key('\r')
        until(lambda: q2.phase('recv') == 'refused-hold', 8, pump=cli.pump)
        check('held: refused without asking', q2.phase('recv') == 'refused-hold' and not q2.f('ask').exists(),
              q2.phase('recv'))
        q2.f('hold').unlink()
        cli.stop()

        # trusted: accepted with "Always accept", the next request from that certificate is not asked about
        def send_from_cli(files, config='cli-config'):
            c = Cli(a.cli, work, files=files, config=config)
            q2.put('cmd', 'scan\n')
            c.pump(4)
            c.key('\r')
            return c
        cli = send_from_cli([payload2])
        ask = until(lambda: q2.f('ask').exists() and q2.f('ask').read_text(), 10, pump=cli.pump)
        check('an untrusted sender gets the card', bool(ask))
        q2.put('answer', f"{ask.split(chr(9))[0] if ask else 'x'} 1 trust\n")
        until(lambda: q2.phase('recv') == 'done', 10, pump=cli.pump)
        check('accepted with trust: the sender is listed by its fingerprint', until(
            lambda: [t for t in q2.trusted() if t == [clifp, 'Reference CLI']], 3), str(q2.trusted()))
        cli.stop()
        (q2.recv / payload2.name).unlink()
        cli = send_from_cli([payload2])
        landed = until(lambda: (q2.recv / payload2.name).exists() and q2.state().get('recv', [''])[-1:] == ['1'] and
                       q2.phase('recv') == 'done', 10, pump=cli.pump)
        rs = q2.state().get('recv', [])
        check('a trusted sender is not asked, and its file lands', bool(landed) and not q2.f('ask').exists() and
              (q2.recv / payload2.name).read_text() == payload2.read_text(), str(rs))
        check('and the state says trusted, so demo can show "Receiving from"', rs[-1:] == ['1'], str(rs))
        cli.stop()
        # the same name with another certificate: asked again
        cli = send_from_cli([payload2], config='cli-config-other')
        ask = until(lambda: q2.f('ask').exists() and q2.f('ask').read_text(), 10, pump=cli.pump)
        check('a trusted name with a different fingerprint gets the card', bool(ask) and
              ask.split('\t')[1] == 'Reference CLI', repr(ask))
        q2.put('answer', f"{ask.split(chr(9))[0] if ask else 'x'} 0\n")
        until(lambda: q2.phase('recv') == 'declined', 5, pump=cli.pump)
        cli.stop()
        q2.put('cmd', f'untrust\t{clifp}\n')
        check('untrust removes it', until(lambda: not q2.trusted(), 3), str(q2.trusted()))

        # q2send sends two files to the CLI, whose user accepts
        cli = Cli(a.cli, work)
        q2.put('cmd', 'scan\n')
        until(lambda: [p for p in q2.state().get('peers', []) if p[4] == 'Reference CLI'], 8, pump=cli.pump)
        logs = work / 'logs'
        logs.mkdir()
        f1, f2 = logs / 'q2-log.0', logs / 'battery-log.txt'
        f1.write_bytes(os.urandom(700_000))
        f2.write_text('Nov 18 08:10 64%\n' * 50)
        q2.put('cmd', f'send\t{clifp}\n{f1}\n{f2}\n')
        until(lambda: b'Accept?' in cli.out, 10, pump=cli.pump)
        check('the CLI is asked to accept', b'Accept?' in cli.out)
        check('state says wait meanwhile', until(lambda: q2.phase('send') == 'wait', 2), q2.phase('send'))
        cli.key('y')
        until(lambda: q2.phase('send') in ('done', 'failed'), 20, pump=cli.pump)
        ok = all((cli.dest / f.name).exists() and (cli.dest / f.name).read_bytes() == f.read_bytes() for f in (f1, f2))
        ss = q2.state().get('send', [])
        check('sent: both land whole at the CLI', ok and ss[0] == 'done', str(ss))
        tot = str(f1.stat().st_size + f2.stat().st_size)
        check('state: 2 of 2, every byte', ss[1:5] == ['2', '2', tot, tot], str(ss))
        seen = len(cli.out)
        q2.put('cmd', f'send\t{clifp}\n{f2}\n')
        until(lambda: b'Accept?' in cli.out[seen:], 10, pump=cli.pump)
        cli.key('n')
        until(lambda: q2.phase('send') == 'declined', 10, pump=cli.pump)
        check('declined at the CLI: state says declined', q2.phase('send') == 'declined', q2.phase('send'))
        cli.stop()
        check('the identity is kept: same fingerprint after a restart', restart_same(q2, exe, q2fp))
    finally:
        cli.stop()
        if q2.p.poll() is None:
            q2.p.terminate()
            q2.p.wait(5)
    print(f'{len(FAILS)} failed' if FAILS else 'all ok', '' if a.keep else f'(work in {work})')
    return len(FAILS)


def restart_same(q2, exe, fp):
    q2.p.send_signal(signal.SIGTERM)
    code = q2.p.wait(5)
    check('SIGTERM ends q2send cleanly, its files gone', code == 0 and not q2.f('state').exists() and
          not q2.f('pid').exists(), f'exit {code}')
    q2.p = subprocess.Popen([*exe, '--run', str(q2.run), '--data', str(q2.data), '--recv', str(q2.recv),
                             '--port', str(Q2PORT)])
    st = until(lambda: q2.state().get('me'), 5)
    return bool(st) and st[0] == fp and len(fp) == 64 and fp == fp.upper()


if __name__ == '__main__':
    sys.exit(main())
