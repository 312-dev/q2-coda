"""coda-rc, the daemon whole (patch/coda_rc.c with coda_rc_core.c and coda_rc_lib.c), built for this machine as a
library whose main runs on a thread of this process (no program is executed: conftest.Guarded), with --root under a
temporary folder, --no-ble and a free port, driven over TCP by tools/coda_rc_client.py as a phone would; the test
stands in for demo's shim (/tmp/coda-rc.now in, /tmp/coda-rc.cmd out, /tmp/coda-rc.ack back). The daemon's loop runs
until the session ends. CAP_RADIO: StartRadio's commands to the shim and NowPlaying.radio
(docs/remote-protocol.md#radio). A server album's cover the card lacks, asked of its card app while the answer is
held (docs/internals.md#car-remote-covers)."""
import ctypes
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import threading
import time

import pytest

import coda_rc_client as client

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
SOURCES = [PATCH / 'coda_rc.c', PATCH / 'coda_rc_core.c', PATCH / 'coda_rc_lib.c', PATCH / 'coda_rc_mdns.c',
           PATCH / 'monocypher/monocypher.c',
           PATCH / 'monocypher/monocypher-ed25519.c', PATCH / 'coda_rc_ble.c']
SQLITE = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0'
CAP_RADIO = 16384
CAP_IROH_ALLOW = 131072
CONNECT = socket.socket.connect  # conftest refuses it to a test; the daemons here listen on loopback alone


def LOOPBACK(addr, timeout):
    """A connection to one of this module's daemons, past conftest's guard."""
    so = socket.socket()
    so.settimeout(timeout)
    CONNECT(so, addr)
    return so
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


def started(root):
    """The ports a daemon under root listens on, from its start line (--port 0 and --iroh-port any: the kernel's
    picks). A port chosen here and closed for the daemon to bind could be given to another process in between."""
    log = root / 'tmp/coda-rc.log'
    for _ in range(500):
        for line in log.read_text().splitlines() if log.exists() else []:
            m = re.search(r'start: tcp (\d+) listening, q2-iroh.s port (-?\d+)', line)
            if m:
                return int(m[1]), int(m[2])
            assert 'start failed' not in line, line
        WAIT(0.02)
    raise AssertionError('the daemon never said it listens')


def put(path, text):
    """Written as demo's shim writes (coda_rc_shim.c blob_io): a temporary file renamed into place."""
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    tmp.rename(path)


class Shim:
    """demo's side: Now Playing written, each command answered with code."""
    def __init__(self, root):
        self.root, self.seq, self.code, self.verbs = root, 0, 0, []
        self.before = ''  # answers to other commands, kept above this one's in the ack file (coda_rc_shim.c cr_ack)
        self.loop: threading.Thread | None = None  # the daemon's loop, once started
        self.tell_r = -1  # the read end of the pipe the daemon tells the shim on
        self.iroh_port = 0  # the daemon's port for q2-iroh's streams, each named by its phone's key
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
                put(self.root / 'tmp/coda-rc.ack', f'{self.before}{seq} {self.code}\n')
            time.sleep(0.02)


@pytest.fixture(scope='module')
def q2(daemon_bin, tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('q2')
    shim = Shim(tmp_path)
    shim.tell_r, tell_w = os.pipe()  # the shim's pipe (coda_rc_shim.c cr_start); the daemon runs in this process
    os.set_blocking(shim.tell_r, False)
    args = [b'coda-rc', b'--root', str(tmp_path).encode(), b'--no-ble', b'--port', b'0',
            b'--tell-fd', str(tell_w).encode(), b'--iroh-port', b'any']
    argv = (ctypes.c_char_p * len(args))(*args)
    loop = threading.Thread(target=daemon_bin.coda_main, args=(len(args), argv), daemon=True)
    loop.start()
    port, shim.iroh_port = started(tmp_path)

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


def test_get_remote_names_the_sidecars_endpoint(q2):
    """CAP_REMOTE (docs/remote.md#car-remote-across-networks): a paired phone learns the q2-iroh endpoint it can reach
    the Q2 Muse by from another network, read from the sidecar's --info file; UNAVAILABLE without one."""
    s, shim = q2
    assert s.info['caps'] & 65536
    info = shim.root / 'tmp/coda-rc.iroh'
    with pytest.raises(client.RemoteError) as e:
        s.remote()
    assert e.value.name == 'UNAVAILABLE'
    endpoint = '8aace45aa34b357c67fae6d1433685921c74f979a8be842802efc17807939913'
    put(info, f'id {endpoint}\n')
    assert s.remote() == {'endpoint_id': endpoint, 'relay_url': ''}
    put(info, f'id {endpoint}\nrelay https://use1-1.relay.n0.iroh.link./\n')
    assert s.remote() == {'endpoint_id': endpoint, 'relay_url': 'https://use1-1.relay.n0.iroh.link./'}
    info.unlink()


@pytest.fixture(scope='module')
def scanned(q2, tmp_path_factory):
    """A second phone that scanned Pair a Phone's QR code: /tmp/coda-rc.qr holds the code it carries, and the phone
    pairs on that code with no card to read. Connected here: a test may not open a socket (conftest.contained)."""
    s, shim = q2
    qr = shim.root / 'tmp/coda-rc.qr'
    put(qr, '042917\n')
    p = client.connect('127.0.0.1', s.sock.getpeername()[1], tmp_path_factory.mktemp('scanned'), name='Pixel QR',
                       code='042917')
    yield p, qr
    p.sock.close()


def test_a_code_pair_a_phone_shows_is_the_pairings_and_is_spent(q2, scanned):
    """docs/remote-protocol.md#pairing-by-qr-code: the code in /tmp/coda-rc.qr pairs the phone that scanned it, and
    the daemon removes the file when that pairing ends, so the page shows a new one."""
    s, _ = q2
    p, qr = scanned
    assert p.info['device_id'] == s.info['device_id'] and not qr.exists()
    assert p.get_state()  # authenticated on the key that pairing made


def sleeps(tid):
    """How many times the daemon's loop has slept and woken: its thread's voluntary context switches."""
    status = pathlib.Path(f'/proc/self/task/{tid}/status').read_text()
    return int(status.split('voluntary_ctxt_switches:')[1].split()[0])


def define(path, name):
    """A number the daemon's source #defines: the tests take the daemon's own pace, not a copy of it."""
    m = re.search(rf'#define {name} (\d+)', (PATCH / path).read_text())
    assert m, name
    return int(m[1])


def paced(loop, work, period_ms, timers):
    """work() run while the daemon's loop thread is counted: (its wakeups, the most a loop may have that wakes only for
    its `timers` looks, each every period_ms on a phase of its own, and the seconds work took). The bound follows the
    time measured on the monotonic clock, not the time work meant to take: on a loaded machine (18 suites starting
    beside this one, 2026-11-22) 10 s of writes took over 12, and a fixed count failed a daemon that did nothing
    wrong. Each timer looks at most once per whole period and once more for the parts at either end."""
    before, start = sleeps(loop.native_id), time.monotonic()
    work()
    elapsed = time.monotonic() - start
    return sleeps(loop.native_id) - before, timers * (int(elapsed * 1000 // period_ms) + 2), elapsed


def test_with_nothing_to_do_the_loop_wakes_once_a_second(q2):
    """No phone following the position, nothing from demo: its poll sleeps until the second's tick (TICK_MS), the one
    timer this daemon has (no mDNS here); a 250 ms poll woke 12 times in 3 s."""
    s, shim = q2
    s.subscribe(position=False)
    WAIT(1.2)
    assert shim.loop and shim.loop.native_id
    woke, most, elapsed = paced(shim.loop, lambda: WAIT(3), define('coda_rc.c', 'TICK_MS'), 1)
    assert woke <= most, f'{woke} wakeups in {elapsed:.1f} s; a loop that wakes only for its tick has {most} at most'


def test_demos_files_end_the_loops_sleep(q2):
    """Now Playing and a command's ack, renamed into /tmp by the shim, reach the phone at once (inotify), not at the
    loop's next look. A daemon that saw the files only at its tick would take anywhere from nothing to TICK_MS, evenly:
    each of six tries must take under half of it, which such a daemon passes once in 64 runs, while a loaded machine
    (0.28 s for a command on 2026-11-22, against the 0.15 s this test once allowed) stays well under."""
    s, shim = q2
    half = define('coda_rc.c', 'TICK_MS') / 2000
    s.subscribe(position=False)
    for radio in (1, 0, 1, 0, 1, 0):
        WAIT(0.3)  # into the loop's sleep
        start = time.monotonic()
        shim.now(radio=radio)
        while s.wait_push(5).get('now_playing', {}).get('radio') != bool(radio):
            pass
        took = time.monotonic() - start
        assert took < half, f'Now Playing reached the phone {took:.2f} s after the shim wrote it'
    for _ in range(6):
        WAIT(0.3)
        start = time.monotonic()
        s.start_radio(stop=True)  # the shim answers within its 20 ms look
        took = time.monotonic() - start
        assert took < half, f'a command answered {took:.2f} s after it was sent'


def told(shim, wait=0.5):
    """The bytes the daemon has told the shim since the last look, after waiting for them."""
    WAIT(wait)
    try:
        return os.read(shim.tell_r, 4096)
    except BlockingIOError:
        return b''


def test_the_shim_is_told_of_each_command_and_of_nothing_else(q2):
    """docs/internals.md#car-remote-tell: a byte on --tell-fd after each write or removal of a file the shim reads
    (.cmd here; .pair, .pair-end and .wake too), so the shim reads them then; Now Playing and the ack, which the
    daemon reads, and its own files tell nothing."""
    s, shim = q2
    told(shim, 0)
    s.subscribe(position=False)
    shim.now(radio=1)
    assert told(shim, 1.2) == b''  # Now Playing in, a push out, the second's tick: nothing for the shim
    for _ in range(3):
        s.start_radio(stop=True)
        assert told(shim, 0.1) == b'1'  # the command's file, written before the shim's ack came


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


# ---- the shim's command queue (patch/coda_rc_cmdq.h, docs/internals.md#car-remote-commands) ----

def test_a_command_goes_down_the_shims_queue_while_it_reads_one(q2):
    """With the shim's FIFO there and read, a phone's command is a line down it, not the single file that q2-iap's
    and the daemon's commands overwrote each other in."""
    s, shim = q2
    fifo = shim.root / 'tmp/coda-rc.cmdq'
    os.mkfifo(fifo, 0o600)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)  # the shim's reader (cr_cmdq_start)
    errors = []
    t = threading.Thread(target=lambda: errors.extend(_call(s.transport, 'next')))
    try:
        t.start()
        line = b''
        for _ in range(200):
            try:
                line += os.read(fd, 64)
            except BlockingIOError:
                pass
            if line.endswith(b'\n'):
                break
            WAIT(0.01)
        seq, verb = line.decode().split(' ', 1)
        assert verb == 'next\n' and not (shim.root / 'tmp/coda-rc.cmd').exists()
        put(shim.root / 'tmp/coda-rc.ack', f'{seq} 0\n')
        t.join(5)
        assert errors == []
    finally:
        os.close(fd)
        fifo.unlink()


def _call(fn, *args):
    try:
        fn(*args)
        return []
    except Exception as e:  # the thread's failure, for the test to see
        return [e]


def test_a_command_with_no_reader_on_the_queue_goes_to_the_file(q2):
    """A FIFO with nobody reading it (demo gone, or one from before the queue left it): the open is refused, and the
    command goes to the single file as before."""
    s, shim = q2
    fifo = shim.root / 'tmp/coda-rc.cmdq'
    os.mkfifo(fifo, 0o600)
    try:
        shim.verbs.clear()
        s.transport('previous')
        assert shim.verbs == ['previous']
    finally:
        fifo.unlink()


def test_the_daemon_finds_its_answer_among_the_shims_last(q2):
    """The shim keeps its last few answers in the ack file, newest first: a car's command answered after the phone's
    is above it, and the phone's is still found (the single answer it replaced timed out as UNAVAILABLE, 4 s on)."""
    s, shim = q2
    shim.before = '1359998999 0\n'
    try:
        start = time.monotonic()
        s.transport('next')
        assert time.monotonic() - start < 1
    finally:
        shim.before = ''


@pytest.fixture(scope='module')
def lonely(daemon_bin, tmp_path_factory):
    """A second daemon, in a copy of the library (its statics its own), that no phone has connected to. Made for the
    module, before conftest's guard refuses sockets to a test: a test reaches it with LOOPBACK, its loopback port."""
    tmp_path = tmp_path_factory.mktemp('lonely')
    lib = tmp_path / 'coda-rc-lonely.so'
    shutil.copy(daemon_bin._name, lib)
    d = ctypes.CDLL(str(lib))
    root = tmp_path / 'root'
    (root / 'tmp').mkdir(parents=True)
    (root / 'mnt/data').mkdir(parents=True)
    args = [b'coda-rc', b'--root', str(root).encode(), b'--no-ble', b'--port', b'0',
            b'--mdns-if', b'nowhere0', b'--iroh-port', b'0']
    argv = (ctypes.c_char_p * len(args))(*args)
    loop = threading.Thread(target=d.coda_main, args=(len(args), argv), daemon=True)
    loop.start()
    port, _ = started(root)
    WAIT(0.5)
    yield root, loop, port
    d.crc_host_quit()
    loop.join(10)


def test_with_no_phone_the_loop_sleeps_through_demos_files(lonely):
    """2026-11-22's sweep: with Car Remote on and no phone, coda-rc woke every second and at every file renamed into
    /tmp, the shim's Now Playing twice a second while a song plays: 159 wakeups a minute measured on this machine.
    With no phone connected and no pairing, it wakes every TICK_IDLE_MS (5 s) for its own looks.
    Its timers then are the tick and the mDNS address look (on an interface with no address here), each every
    TICK_IDLE_MS on a phase of its own. Now Playing is renamed in every 0.2 s for 10 s by the monotonic clock: a
    daemon that wakes for each file wakes at least once a write, and the writes are kept to more than twice the
    bound, so such a daemon fails it however slow the machine is."""
    root, loop, port = lonely
    writes = []

    def song_playing():
        start = time.monotonic()
        while time.monotonic() - start < 10:
            put(root / 'tmp/coda-rc.now', NOW.format(seq=len(writes), radio=0))
            writes.append(1)
            WAIT(0.2)
    tick = define('coda_rc.c', 'TICK_IDLE_MS')
    assert tick == define('coda_rc_mdns.c', 'MDNS_CHECK_IDLE_MS')  # the two looks share the period the bound uses
    woke, most, elapsed = paced(loop, song_playing, tick, 2)
    assert len(writes) > 2 * most, f'{len(writes)} writes in {elapsed:.1f} s: too few to tell a wake per write apart'
    assert woke <= most, f'{woke} wakeups for {len(writes)} writes in {elapsed:.1f} s; its two looks give {most} at most'


def test_a_phone_connecting_ends_the_quiet_at_once(lonely):
    """Quiet is only while nobody is there: a connection is answered at once, and the session then has the second's
    tick and the /tmp watch back."""
    root, loop, port = lonely
    WAIT(1)
    start = time.monotonic()
    with LOOPBACK(('127.0.0.1', port), timeout=2) as so:
        p = client.encode([(1, 1), (2, 'probe'), (3, bytes(16))])
        so.sendall(struct.pack('>HBBH', len(p) + 4, client.T['HELLO'], 0, 1) + p)  # as the client's --hello prints
        so.settimeout(2)
        assert so.recv(4096)  # HelloResp
    assert time.monotonic() - start < 0.5


# ---- q2-iroh's allow-list (docs/remote-protocol.md#across-networks-only-paired-phones) ----

def through_iroh(shim, key, name='iroh phone'):
    """A stream as q2-iroh carries it to the daemon: its port for q2-iroh, the phone's key first."""
    so = LOOPBACK(('127.0.0.1', shim.iroh_port), timeout=5)
    so.sendall(key)
    return client.Session(so, name)


def allowed(shim):
    f = shim.root / 'tmp/coda-rc.iroh-allow'
    WAIT(0.3)
    return f.read_text().split() if f.exists() else []


def test_an_unknown_iroh_key_gets_nothing_outside_pair_a_phone(q2):
    """Defence beside q2-iroh's own list: a stream from a key no paired phone registered is refused at its first
    frame, Hello included, while Pair a Phone shows no code."""
    s, shim = q2
    t = through_iroh(shim, b'\x11' * 32)
    with pytest.raises((client.RemoteError, ConnectionError, OSError, ValueError)):
        t.hello()
    t.sock.close()


def test_pairing_through_iroh_registers_the_phones_key_and_forget_removes_it(q2):
    """While Pair a Phone shows its code a new phone pairs through q2-iroh; its key then is a paired phone's, listed
    for q2-iroh, and it comes back with no code shown. Forget on the Q2 (the peers file rewritten without it) takes it
    off the list at once."""
    s, shim = q2
    key = bytes(range(32))
    qr = shim.root / 'tmp/coda-rc.qr'
    put(qr, '246810\n')
    t = through_iroh(shim, key)
    info = t.hello()
    point = t.pair_start()
    peer_id, ltk = t.pair_finish('246810', point)
    t.auth(ltk)
    t.sock.close()
    assert key.hex() in allowed(shim)
    qr.unlink(missing_ok=True)
    t = through_iroh(shim, key)
    assert t.hello(peer_id)['paired']
    t.auth(ltk)
    t.sock.close()
    peers = (shim.root / 'mnt/data/coda-rc/peers').read_bytes()
    kept = b''.join(peers[i:i + 76] for i in range(0, len(peers), 76) if peers[i:i + 8] != peer_id)
    tmp = shim.root / 'mnt/data/coda-rc/peers.tmp'  # the page's Forget: the file replaced by a rename
    tmp.write_bytes(kept)
    tmp.rename(shim.root / 'mnt/data/coda-rc/peers')
    assert key.hex() not in allowed(shim)
    t = through_iroh(shim, key)
    with pytest.raises((client.RemoteError, ConnectionError, OSError, ValueError)):
        t.hello(peer_id)
    t.sock.close()
    assert info['caps'] & CAP_IROH_ALLOW


def test_a_paired_phone_registers_its_key_over_any_session(q2):
    """RegisterRemote over the phone's ordinary TCP session (or Bluetooth): from then on q2-iroh lets that key in."""
    s, shim = q2
    key = b'\x5a' * 32
    s.call(client.T['REGISTER_REMOTE'], client.encode([(1, key)]), client.T['ACK'])
    assert key.hex() in allowed(shim)
    t = through_iroh(shim, key)
    assert t.hello(None)['caps'] & CAP_IROH_ALLOW  # past the gate: a registered key
    t.sock.close()
    with pytest.raises(client.RemoteError) as e:
        s.call(client.T['REGISTER_REMOTE'], client.encode([(1, key[:31])]), client.T['ACK'])
    assert e.value.code == 3  # BAD_REQUEST


def test_a_key_that_left_pair_a_phone_without_pairing_is_held_off(q2):
    """During Pair a Phone an unknown key may come in; one that leaves without pairing or Auth is refused at its next
    connections for a while (10 s, doubling), so it cannot keep opening sessions while the page is up."""
    s, shim = q2
    key = b'\x77' * 32
    qr = shim.root / 'tmp/coda-rc.qr'
    put(qr, '135790\n')
    try:
        t = through_iroh(shim, key)
        t.hello()
        t.sock.close()
        WAIT(0.3)
        t = through_iroh(shim, key)
        with pytest.raises((client.RemoteError, ConnectionError, OSError, ValueError)):
            t.hello()
        t.sock.close()
    finally:
        qr.unlink(missing_ok=True)
