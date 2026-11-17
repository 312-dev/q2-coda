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
SOURCES = [PATCH / 'coda_rc.c', PATCH / 'coda_rc_core.c', PATCH / 'coda_rc_lib.c', PATCH / 'coda_rc_mdns.c',
           PATCH / 'monocypher/monocypher.c',
           PATCH / 'monocypher/monocypher-ed25519.c', ROOT / 'test/unit/coda_rc_ble_host.c']
SQLITE = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0'
CAP_RADIO = 16384
WAIT = time.sleep  # conftest makes time.sleep return at once during a test; the loop's pace needs real time
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


def put(path, text):
    """Written as demo's shim writes (coda_rc_shim.c blob_io): a temporary file renamed into place."""
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    tmp.rename(path)


class Shim:
    """demo's side: Now Playing written, each command answered with code."""
    def __init__(self, root):
        self.root, self.seq, self.code, self.verbs = root, 0, 0, []
        self.loop: threading.Thread | None = None  # the daemon's loop, once started
        (root / 'tmp').mkdir()
        (root / 'mnt/data').mkdir(parents=True)
        self.now(radio=0)
        self.stop = False
        self.t = threading.Thread(target=self.answer, daemon=True)
        self.t.start()

    def now(self, radio):
        self.seq += 1
        put(self.root / 'tmp/coda-rc.now', NOW.format(seq=self.seq, radio=radio))

    def answer(self):
        cmd = self.root / 'tmp/coda-rc.cmd'
        while not self.stop:
            if cmd.exists():
                seq, verb = cmd.read_text().strip().split(' ', 1)
                cmd.unlink()
                self.verbs.append(verb)
                put(self.root / 'tmp/coda-rc.ack', f'{seq} {self.code}\n')
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
    loop = threading.Thread(target=daemon_bin.coda_main, args=(len(args), argv), daemon=True)
    loop.start()
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
    shim.loop = loop
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


def sleeps(tid):
    """How many times the daemon's loop has slept and woken: its thread's voluntary context switches."""
    status = pathlib.Path(f'/proc/self/task/{tid}/status').read_text()
    return int(status.split('voluntary_ctxt_switches:')[1].split()[0])


def test_with_nothing_to_do_the_loop_wakes_once_a_second(q2):
    """No phone following the position, nothing from demo: its poll sleeps until the second's tick (TICK_MS)."""
    s, shim = q2
    s.subscribe(position=False)
    WAIT(1.2)
    assert shim.loop and shim.loop.native_id
    before = sleeps(shim.loop.native_id)
    WAIT(3)
    assert sleeps(shim.loop.native_id) - before <= 5  # about 3; a 250 ms poll woke 12 times


def test_demos_files_end_the_loops_sleep(q2):
    """Now Playing and a command's ack, renamed into /tmp by the shim, reach the phone at once (inotify), not at the
    loop's next look."""
    s, shim = q2
    s.subscribe(position=False)
    for radio in (1, 0, 1, 0):
        WAIT(0.3)  # into the loop's sleep
        start = time.monotonic()
        shim.now(radio=radio)
        while s.wait_push(5).get('now_playing', {}).get('radio') != bool(radio):
            pass
        assert time.monotonic() - start < 0.1
    for _ in range(3):
        WAIT(0.3)
        start = time.monotonic()
        s.start_radio(stop=True)  # the shim answers within its 20 ms look
        assert time.monotonic() - start < 0.15
