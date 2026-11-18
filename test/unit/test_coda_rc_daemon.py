"""coda-rc, the daemon whole (patch/coda_rc.c with coda_rc_core.c and coda_rc_lib.c), built for this machine as a
library whose main runs on a thread of this process (no program is executed: conftest.Guarded), with --root under a
temporary folder, --no-ble and a free port, driven over TCP by tools/coda_rc_client.py as a phone would; the test
stands in for demo's shim (/tmp/coda-rc.now in, /tmp/coda-rc.cmd out, /tmp/coda-rc.ack back). The daemon's loop runs
until the session ends. CAP_RADIO: StartRadio's commands to the shim and NowPlaying.radio
(docs/remote-protocol.md#radio). A server album's cover the card lacks, asked of its card app while the answer is
held (docs/internals.md#car-remote-covers)."""
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

    def now(self, radio, extra=''):
        self.seq += 1
        put(self.root / 'tmp/coda-rc.now', NOW.format(seq=self.seq, radio=radio) + extra)

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


def test_now_playing_carries_the_q2_muses_toast(q2):
    """CAP_NOTICE: the car showed nothing when the Q2 Muse toasted that a radio could not start (2026-11-18 00:28);
    Android Auto draws no toast. The shim's note and its count (coda_rc_shim.c coda_rc_note) go out as
    NowPlaying.notice and notice_id at once, the same words again with a new id, and none once the shim clears it."""
    s, shim = q2
    assert s.info['caps'] & 32768
    s.subscribe(position=False)
    seen = []
    for n, note in ((1, "Radio isn't available for this song"), (2, "Radio isn't available for this song"), (2, '')):
        shim.now(radio=0, extra=f'noten={n}\nnote={note}\n')
        for _ in range(20):
            np = s.wait_push(5).get('now_playing', {})
            if np and (np['notice_id'], np['notice']) == (n, note):
                seen.append((n, note))
                break
    assert seen == [(1, "Radio isn't available for this song"), (2, "Radio isn't available for this song"), (2, '')]


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


# ---- covers the card lacks, asked of the card app (docs/internals.md#car-remote-covers) ----

ALBUMS = [('Echo', 'Eve', 'Third', 500), ('Golf', 'Gus', 'Fourth', 600), ('Hotel', 'Hal', 'Fifth', 700),
          ('India', 'Ivy', 'Sixth', 800)]
BIG = b'\xff\xd8' + bytes(range(256)) * 24 + b'\xff\xd9'


class App:
    """q2-subsonic's side (its coverask.go): each new line of /tmp/q2-subsonic.coverask answered in its state file,
    coverack=<seq> <code>, after saving the cover for an album in save, none for one in none; an album in neither is
    never answered."""
    def __init__(self, root):
        self.root, self.lines, self.save, self.none, self.stop = root, [], set(), set(), False
        self.t = threading.Thread(target=self.run, daemon=True)
        self.t.start()

    def run(self):
        ask = self.root / 'tmp/q2-subsonic.coverask'
        while not self.stop:
            for line in ask.read_text().splitlines() if ask.exists() else []:
                if line in self.lines:
                    continue
                self.lines.append(line)
                seq, px, album = line.split()
                if album in self.save:
                    f = self.root / f'mnt/mmc/.subsonic/covers/{"512/" if px == "512" else ""}{album}.jpg'
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(BIG)
                if album in self.save or album in self.none:
                    put(self.root / 'tmp/q2-subsonic.state', f'coverack={seq} {0 if album in self.save else 2}\nversion=x\n')
            WAIT(0.02)


@pytest.fixture(scope='module')
def car(q2):
    s, shim = q2
    from test_coda_rc_lib import Q2 as Library
    Library.files(shim.root, server=ALBUMS, cat=True)
    (shim.root / 'mnt/mmc/.subsonic').mkdir(parents=True, exist_ok=True)
    (shim.root / 'mnt/mmc/.subsonic/q2app').write_text('name=Subsonic\nlyrics=1\ncovers=1\n')
    app = App(shim.root)
    yield s, shim, app
    app.stop = True


def test_a_cover_the_card_lacks_is_fetched_by_its_app_and_sent(car):
    """The car asks for a server album's cover the card has none of: the daemon asks the app (a line in
    /tmp/q2-subsonic.coverask, the size the car wants), has demo start it (/tmp/coda-rc.wake: its socket isn't
    there), holds the answer, and sends the cover once the app has saved it. Then the request is gone, and the next
    GetCover is the file's, with nothing asked."""
    s, shim, app = car
    wake = shim.root / 'tmp/coda-rc.wake'
    app.save.add('third')
    start = time.monotonic()
    data, mime = s.cover('sthird', 350)
    assert data == BIG and mime == 'image/jpeg' and time.monotonic() - start < 2
    assert [line.split()[1:] for line in app.lines] == [['512', 'third']]
    assert wake.read_text() == 'subsonic\n'
    assert not (shim.root / 'tmp/q2-subsonic.coverask').exists()  # answered: nothing waits
    wake.unlink()
    assert s.cover('sthird', 350)[0] == BIG
    assert len(app.lines) == 1 and not wake.exists()


def test_an_app_without_the_cover_is_not_found_at_once_and_not_asked_again(car):
    """The app answers that the server has none: NOT_FOUND goes at once, not at the hold's end, and the album is not
    asked again. An app that runs (its socket there) is not started."""
    s, shim, app = car
    sock = shim.root / 'tmp/q2-subsonic.browse.sock'
    sock.write_text('')
    app.none.add('fourth')
    start = time.monotonic()
    with pytest.raises(client.RemoteError) as e:
        s.cover('sfourth', 160)
    assert e.value.name == 'NOT_FOUND' and time.monotonic() - start < 2
    assert app.lines[-1].split()[1:] == ['160', 'fourth']
    asked = len(app.lines)
    with pytest.raises(client.RemoteError):
        s.cover('sfourth', 160)
    WAIT(0.2)
    assert len(app.lines) == asked and not (shim.root / 'tmp/coda-rc.wake').exists()
    sock.unlink()


def test_a_held_cover_stops_nothing_and_ends_at_the_hold(car):
    """An app that doesn't answer: the GetCover is answered NOT_FOUND after the hold (COVER_HOLD_MS, 5 s, under the
    car's 8 s, past which it closes the session), and meanwhile the daemon answers everything else."""
    s, shim, app = car
    start = time.monotonic()
    req = s.send(client.T['GET_COVER'], client.encode([(1, 'sfifth'), (2, 0), (3, 4096), (4, 350)]))
    WAIT(0.2)
    assert s.browse('albums')['items'] and time.monotonic() - start < 1
    while True:
        t, r, p = s.frame(10)
        if r == req:
            break
    assert t == client.T['ERROR'] and client.ERRORS[client.one(client.decode(p), 1)] == 'NOT_FOUND'
    assert 4.5 < time.monotonic() - start < 7
    assert app.lines[-1].split()[1:] == ['512', 'fifth']


def test_an_app_without_covers_1_is_not_asked(car):
    """An app whose q2app doesn't say covers=1 (one from before) is never asked: NOT_FOUND at once."""
    s, shim, app = car
    q2app = shim.root / 'mnt/mmc/.subsonic/q2app'
    q2app.write_text('name=Subsonic\nlyrics=1\n')
    asked = len(app.lines)
    start = time.monotonic()
    with pytest.raises(client.RemoteError):
        s.cover('ssixth', 350)
    assert time.monotonic() - start < 1 and len(app.lines) == asked
    q2app.write_text('name=Subsonic\nlyrics=1\ncovers=1\n')
