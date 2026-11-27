#!/usr/bin/env python3
"""q2-iroh's allow-list end to end on this machine (docs/remote-protocol.md#across-networks-only-paired-phones): a host
build of coda-rc (patch/coda_rc*.c), a host q2-iroh serve beside it as demo's shim starts it (--forward-named,
--allow-file, --open-while), and q2-iroh dial as the phone, with a kept key as the Vamp app keeps one, through
n0's public relay, driven by tools/coda_rc_client.py.

usage: coda_rc_iroh_e2e.py [--iroh BIN]

BIN is a host build of remote/iroh-sidecar (default ~/.cache/q2-rust/target-host/release/q2-iroh: `cargo +nightly
build --release` there, docs/building.md). Needs clang, the host's libsqlite3 and the network: with no BIN, or no
relay reached within 30 s (offline), it prints why and exits 0 as skipped.

Checks, in order: a key no paired phone registered is refused at the handshake; while Pair a Phone shows its code
(/tmp/coda-rc.qr) a phone pairs through iroh and its key is listed; it comes back with no code shown; a new key (an
app from before RegisterRemote) is refused though its pairing is good; a second key is refused, then let in after
RegisterRemote over the phone's plain TCP session; after Forget (the peers file replaced without the phone) both
keys are refused. Each step prints one line; the exit status is the number of failures."""
import argparse
import pathlib
import re
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import coda_rc_client as client  # noqa: E402

PATCH = ROOT / 'patch'
SOURCES = ['coda_rc.c', 'coda_rc_core.c', 'coda_rc_lib.c', 'coda_rc_mdns.c', 'monocypher/monocypher.c',
           'monocypher/monocypher-ed25519.c', 'coda_rc_ble.c']
SQLITE = f"/usr/lib/{sysconfig.get_config_var('MULTIARCH')}/libsqlite3.so.0"


def daemon_ports(root):
    """The TCP port and q2-iroh's port the daemon got (--port 0, --iroh-port any), from its start line."""
    log = root / 'tmp/coda-rc.log'
    for _ in range(300):
        for line in log.read_text().splitlines() if log.exists() else []:
            m = re.search(r'start: tcp (\d+) listening, q2-iroh.s port (\d+)', line)
            if m:
                return int(m[1]), int(m[2])
            if 'start failed' in line:
                raise RuntimeError(line)
        time.sleep(0.1)
    raise RuntimeError('coda-rc never said it listens')


def free_port():
    """A port free now, for q2-iroh dial's --listen (see Rig.dial)."""
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class Rig:
    def __init__(self, iroh, work):
        self.iroh, self.work, self.procs, self.failures = iroh, work, [], 0
        self.root = work / 'root'
        (self.root / 'tmp').mkdir(parents=True)
        (self.root / 'mnt/data').mkdir(parents=True)

    def start(self, *argv, log):
        p = subprocess.Popen(list(argv), stdout=open(self.work / f'{log}.out', 'w'), stderr=subprocess.STDOUT)
        self.procs.append(p)
        return p

    def check(self, name, ok):
        print(('ok    ' if ok else 'FAIL  ') + name, flush=True)
        self.failures += not ok

    def dial(self, key):
        """q2-iroh dial to the Q2 (a kept key, or None for a new one), a local port in front of it, and that port. A
        port free when chosen may be given to another process before dial binds it: dial then exits, and another
        port is tried."""
        for _ in range(5):
            port = free_port()
            argv = [self.iroh, 'dial', self.q2id, '--listen', f'127.0.0.1:{port}'] + (['--key', str(key)] if key else [])
            d = self.start(*argv, log=f'dial{port}')
            for _ in range(300):
                if 'listening' in (self.work / f'dial{port}.out').read_text():
                    return d, port
                if d.poll() is not None:
                    break
                time.sleep(0.1)
            if d.poll() is None:
                return d, port
        raise RuntimeError('q2-iroh dial never listened')

    def session(self, port):
        return client.Session(socket.create_connection(('127.0.0.1', port), timeout=10), 'iroh e2e phone')

    def refused(self, key, peer_id=None):
        """True when a session through iroh from key gets no HelloResp."""
        d, port = self.dial(key)
        try:
            s = self.session(port)
            s.hello(peer_id)
            s.sock.close()
            return False
        except (OSError, ConnectionError, ValueError, client.RemoteError):
            return True
        finally:
            d.terminate()
            d.wait()

    def allowed(self):
        f = self.root / 'tmp/coda-rc.iroh-allow'
        time.sleep(0.5)
        return f.read_text().split() if f.exists() else []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--iroh', default=str(pathlib.Path.home() / '.cache/q2-rust/target-host/release/q2-iroh'))
    a = ap.parse_args(argv)
    if not pathlib.Path(a.iroh).is_file():
        print(f'skipped: no host q2-iroh at {a.iroh} (cargo +nightly build --release, docs/building.md)')
        return 0
    work = pathlib.Path(tempfile.mkdtemp(prefix='q2-iroh-e2e-'))
    rig = Rig(a.iroh, work)
    try:
        subprocess.run(['clang', '-O1', '-DCODA_HOST', '-w', '-I', str(PATCH), *(str(PATCH / f) for f in SOURCES),
                        SQLITE, '-o', str(work / 'coda-rc')], check=True)
        root = rig.root
        rig.start(str(work / 'coda-rc'), '--root', str(root), '--no-ble', '--no-mdns', '--port', '0',
                  '--iroh-port', 'any', log='coda-rc')
        port, iport = daemon_ports(root)
        rig.start(a.iroh, 'serve', '--key', str(work / 'q2.key'), '--info', str(work / 'info'),
                  '--forward-named', f'127.0.0.1:{iport}', '--allow-file', str(root / 'tmp/coda-rc.iroh-allow'),
                  '--open-while', str(root / 'tmp/coda-rc.qr'), '--log', str(work / 'q2-iroh.log'), log='serve')
        for _ in range(300):
            if (work / 'info').exists() and 'relay' in (work / 'info').read_text():
                break
            time.sleep(0.1)
        else:
            print('skipped: q2-iroh reached no relay in 30 s (offline?)')
            return 0
        rig.q2id = (work / 'info').read_text().split()[1]
        key = work / 'phone.key'
        rig.check('a key no paired phone registered is refused outside Pair a Phone', rig.refused(key))

        (root / 'tmp/coda-rc.qr').write_text('424242\n')  # Pair a Phone shows its code
        d, p = rig.dial(key)
        s = rig.session(p)
        info = s.hello()
        point = s.pair_start()
        peer_id, ltk = s.pair_finish('424242', point)
        s.auth(ltk)
        s.sock.close()
        d.terminate()
        d.wait()
        rig.check('pairing through iroh during Pair a Phone registers the key',
                  len(rig.allowed()) == 1 and info['caps'] & 131072)
        (root / 'tmp/coda-rc.qr').unlink(missing_ok=True)

        d, p = rig.dial(key)
        s = rig.session(p)
        ok = s.hello(peer_id)['paired']
        s.auth(ltk)
        ok = ok and 'now_playing' in s.get_state()
        s.sock.close()
        d.terminate()
        d.wait()
        rig.check('the paired phone comes back through iroh with no code shown', ok)
        rig.check('a new key (an app from before RegisterRemote) is refused', rig.refused(None, peer_id))

        key2 = work / 'phone2.key'
        rig.check('a second, unregistered key is refused at the handshake', rig.refused(key2, peer_id))
        refused = [ln.split()[1] for ln in (work / 'q2-iroh.log').read_text().splitlines() if ln.startswith('refused ')]
        tcp = client.Session(socket.create_connection(('127.0.0.1', port), timeout=10), 'iroh e2e phone')
        tcp.hello(peer_id)
        tcp.auth(ltk)
        tcp.call(client.T['REGISTER_REMOTE'], client.encode([(1, bytes.fromhex(refused[-1]))]), client.T['ACK'])
        tcp.sock.close()
        d, p = rig.dial(key2)
        s = rig.session(p)
        s.hello(peer_id)
        s.auth(ltk)
        ok = 'now_playing' in s.get_state()
        s.sock.close()
        d.terminate()
        d.wait()
        rig.check('after RegisterRemote over TCP the second key comes in through iroh', ok)

        peers = root / 'mnt/data/coda-rc/peers'
        t = peers.with_name('peers.tmp')
        t.write_bytes(b'')
        t.rename(peers)  # Forget: the page replaces the file without the phone
        rig.check('after Forget the list is empty', rig.allowed() == [])
        rig.check('after Forget the second key is refused', rig.refused(key2, peer_id))
        rig.check('after Forget the first key is refused', rig.refused(key, peer_id))
    finally:
        for p in rig.procs:
            if p.poll() is None:
                p.terminate()
                p.wait()
        log = work / 'q2-iroh.log'
        if rig.failures and log.exists():
            print(log.read_text())
        shutil.rmtree(work, ignore_errors=True)
    print(f'{rig.failures} failed')
    return rig.failures


if __name__ == '__main__':
    sys.exit(main())
