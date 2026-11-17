"""coda-rc, the daemon whole (patch/coda_rc.c with coda_rc_core.c and coda_rc_lib.c), built for this machine as a
library whose main runs on a thread of this process (no program is executed: conftest.Guarded), with --root under a
temporary folder, --no-ble and a free port, driven over TCP by tools/coda_rc_client.py as a phone would; the test
stands in for demo's shim (/tmp/coda-rc.now in, /tmp/coda-rc.cmd out, /tmp/coda-rc.ack back). The daemon's loop runs
until the session ends. CAP_RADIO: StartRadio's commands to the shim and NowPlaying.radio
(docs/remote-protocol.md#radio)."""
import ctypes
import pathlib
import socket
import subprocess
import threading
import time

import pytest

import coda_rc_client as client

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc.c', PATCH / 'coda_rc_core.c', PATCH / 'coda_rc_lib.c', PATCH / 'monocypher/monocypher.c',
           PATCH / 'monocypher/monocypher-ed25519.c', ROOT / 'test/unit/coda_rc_ble_host.c']
SQLITE = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0'
CAP_RADIO = 16384
NOW = 'seq={seq}\nstate=1\npos=1000\nlen=200\nid=12\nshuffle=0\nrepeat=0\nvolume=40\nhearts=0\nqpos=0\nqlen=3\nqgen=1\n' \
      'radio={radio}\npath=/mnt/mmc/a/2.flac\ntitle=Song\nartist=Ann\nalbum=First\n'


@pytest.fixture(scope='module')
def daemon_bin(tmp_path_factory):
    if not pathlib.Path(SQLITE).exists():
        pytest.skip('no libsqlite3.so.0 to link the daemon against')
    out = tmp_path_factory.mktemp('coda_rc_daemon') / 'coda-rc.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Dmain=coda_main', '-Wall', '-Werror',
                    '-Wno-unused-parameter', '-I', str(PATCH), *map(str, SOURCES), SQLITE, '-o', str(out)], check=True)
    return ctypes.CDLL(str(out))


class Shim:
    """demo's side: Now Playing written, each command answered with code."""
    def __init__(self, root):
        self.root, self.seq, self.code, self.verbs = root, 0, 0, []
        (root / 'tmp').mkdir()
        (root / 'mnt/data').mkdir(parents=True)
        self.now(radio=0)
        self.stop = False
        self.t = threading.Thread(target=self.answer, daemon=True)
        self.t.start()

    def now(self, radio):
        self.seq += 1
        (self.root / 'tmp/coda-rc.now').write_text(NOW.format(seq=self.seq, radio=radio))

    def answer(self):
        cmd = self.root / 'tmp/coda-rc.cmd'
        while not self.stop:
            if cmd.exists():
                seq, verb = cmd.read_text().strip().split(' ', 1)
                cmd.unlink()
                self.verbs.append(verb)
                (self.root / 'tmp/coda-rc.ack').write_text(f'{seq} {self.code}\n')
            time.sleep(0.02)


@pytest.fixture(scope='module')
def q2(daemon_bin, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('q2')
    shim = Shim(tmp_path)
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    args = [b'coda-rc', b'--root', str(tmp_path).encode(), b'--no-ble', b'--port', str(port).encode()]
    argv = (ctypes.c_char_p * len(args))(*args)
    threading.Thread(target=daemon_bin.coda_main, args=(len(args), argv), daemon=True).start()
    for _ in range(200):
        try:
            socket.create_connection(('127.0.0.1', port), timeout=1).close()
            break
        except OSError:
            time.sleep(0.02)

    def code(_prompt):
        for _ in range(200):
            pair = tmp_path / 'tmp/coda-rc.pair'
            if pair.exists():
                return pair.read_text().split('\n')[0]
            time.sleep(0.02)
        raise AssertionError('no pairing code')

    s = client.connect('127.0.0.1', port, tmp_path / 'keys', code=None, ask=code)
    yield s, shim
    shim.stop = True
    s.sock.close()


def test_start_radio_goes_to_the_shim(q2):
    s, shim = q2
    shim.verbs.clear()
    assert s.info['caps'] & CAP_RADIO
    s.start_radio()  # the playing song
    s.start_radio('song:t12')
    s.start_radio('song:c1073741830')  # a server song: the shim gives it to its app
    s.start_radio(stop=True)
    assert shim.verbs == ['radio 0', 'radio 12', 'radio 1073741830', 'radio stop']


def test_start_radio_refused(q2):
    s, shim = q2
    shim.verbs.clear()
    with pytest.raises(client.RemoteError) as e:
        s.start_radio('album:x')
    assert e.value.code == 3  # BAD_REQUEST
    shim.code = 3  # the shim has no such song
    with pytest.raises(client.RemoteError) as e:
        s.start_radio('song:t99')
    assert e.value.code == 4  # NOT_FOUND
    assert shim.verbs == ['radio 99']
    shim.code = 0


def test_now_playing_says_radio(q2):
    s, shim = q2
    assert s.get_state()['now_playing']['radio'] is False
    s.subscribe(position=False)
    shim.now(radio=1)
    for _ in range(20):
        push = s.wait_push(5)
        if push.get('now_playing', {}).get('radio'):
            break
    else:
        raise AssertionError('no push with radio on')
    assert s.get_state()['now_playing']['radio'] is True
