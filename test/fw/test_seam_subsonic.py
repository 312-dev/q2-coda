"""The seam between the firmware and the Subsonic app it carries, run as it runs on a Q2: the real q2-subsonic (a host
build of the commit under test, or its zip's own MIPS program under qemu) against q2-fakesonic, and on the firmware's
side its own C (addon.c's stream session, catalogue.c's import and press, stream_state.c's .ahead and .stream,
playback.c's play order), sharing one set of files: .catplay, .play and the queue m3u, .playing, .ahead, .stream,
.radio, .coverask and .coverask-iap with their answers in the state file, and the caps line.
docs/building.md#seam-test. Run by test/subsonic_seam.py, which builds both programs and sets Q2_SEAM_*; skipped
without them (test/fw's own run)."""
import ctypes as C
import io
import itertools
import os
import pathlib
import shutil
import time
import zipfile

import pytest

from conftest import O
from seam import App, Firmware

APP_PROGRAM, FAKE, ZIP = (os.environ.get(k) for k in ('Q2_SEAM_APP', 'Q2_SEAM_FAKE', 'Q2_SEAM_ZIP'))
QEMU = os.environ.get('Q2_SEAM_QEMU') or None
pytestmark = [pytest.mark.skipif(not (APP_PROGRAM and FAKE and ZIP), reason='the seam test runs from test/subsonic_seam.py'),
              pytest.mark.timeout(900)]  # real time: each test's waits have deadlines of their own

ROOTS = pathlib.Path.home()/'.cache/q2-seam/r'  # short: the sockets' paths under it must fit sun_path's 108 bytes
STATE = 'q2-subsonic.state'
NEXT = 0  # playback_set's kinds
REPEAT_ALL, SHUFFLE_SONGS = 5, 1  # test_playback.py's REPEAT_ALL; playback.c's Shuffle Songs
_n = itertools.count()


def card_files(zip_path):
    """The zip's .subsonic files but its program: q2app, run, icons, version."""
    with zipfile.ZipFile(zip_path) as z:
        return {n.split('/', 1)[1]: z.read(n) for n in z.namelist()
                if n.startswith('.subsonic/') and n != '.subsonic/q2-subsonic' and not n.endswith('/')}


class Seam:
    """One firmware and one app, started, signed in, with the server's library imported into the Library."""
    def __init__(self, fw, mcl, songs=30, album=10, size=64 << 10):
        self.root = ROOTS/f'{os.getpid()}-{next(_n)}'
        shutil.rmtree(self.root, ignore_errors=True)
        self.fwr = Firmware(fw, mcl, self.root)
        self.fw, self.mcl = fw, mcl
        self.app = App(self.root, APP_PROGRAM, FAKE, card_files(ZIP), songs=songs, album=album, size=size, qemu=QEMU)
        self.fake = self.app.fake
        self.fwr.explain = self.why

    def start(self, imported=True):
        f = self.fwr
        assert self.fw.fn('addon_scan', C.c_int)() >= 1  # the caps line and the library source first, as on the Q2
        assert b'catalogue' in f.tmp('q2-addon.caps') and b'ahead' in f.tmp('q2-addon.caps')
        self.app.start()
        f.until('the app listening', lambda: f.tmp('q2-subsonic.browse.sock') is not None or
                (self.root/'tmp/q2-subsonic.browse.sock').exists() or not self.app.running(), timeout=30)
        assert self.app.running() and self.app.proc, self.why()
        (self.root/'tmp/q2-subsonic').write_text(str(self.app.proc.pid))  # its launcher's pid file (run)
        if imported:
            f.until('the catalogue imported', lambda: self.imported(), timeout=60)
        return self

    def imported(self):
        done = self.fwr.tmp('q2-subsonic.catalogue.done')
        return done is not None and b' ok ' in done and self.fwr.query('select count(*) from cat.servermeta')[0][0] > 0

    def why(self):
        out = (self.root/'app.out').read_text(errors='replace') if (self.root/'app.out').exists() else ''
        err = (self.root/'ns.err').read_text(errors='replace') if (self.root/'ns.err').exists() else ''
        return f'app log:\n{self.app.log()[-3000:]}\napp stdout:\n{out[-2000:]}\nnamespace:\n{err[-1000:]}'

    def close(self):
        self.app.stop()
        self.fwr.close()

    # ---- the Library ----
    def album_rows(self, album):
        """Album `album`'s server rows' paths in track order, as the Library's album list holds them."""
        return [r[0].encode() for r in self.fwr.query(
            'select m.fileurl from cat.servermeta m join cat.servertable t on t.id = m.id where t.album = ? '
            'order by t.track', f'Album {album}')]

    def all_rows(self):
        """Every server row's path, s1 first (the fake's library order)."""
        return [r[0].encode() for r in self.fwr.query(
            'select fileurl from cat.servermeta order by cast(substr(sid, 2) as integer)')]

    def press(self, paths, at=0):
        """A Library list's song pressed: ringnav_load's catalogue_load with the list, as stock's player_start calls it."""
        q = self.fwr.queue_of(paths)
        at_, own = C.c_int(at), C.c_void_p()
        kind = self.fw.fn('catalogue_load', C.c_int, C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p))(
            q, C.byref(at_), C.byref(own))
        assert kind == 2, f'catalogue_load answered {kind}: the list was not handed to the app'
        return q

    # ---- the stream ----
    def state(self):
        raw = self.fwr.tmp(STATE) or b''
        return dict(l.split('=', 1) for l in raw.decode(errors='replace').splitlines() if '=' in l)

    def stream_lines(self):
        """.stream's lines as the firmware reads them: path -> (state, have, total, ms), by stream_state.c's reader."""
        raw = self.fwr.tmp('q2-subsonic.stream') or b''
        out = {}
        for line in raw.decode(errors='replace').splitlines()[1:]:
            path = line.split('\t')[-1].encode()
            got = self.line(path)
            if got:
                out[path] = got
        return out

    def line(self, path):
        class SLine(C.Structure):
            _fields_ = [('state', C.c_int), ('have', C.c_uint), ('total', C.c_uint), ('ms', C.c_uint)]
        out = SLine()
        found = self.fw.fn('stream_line', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p)(b'subsonic', path, C.byref(out))
        return ('none wait get stall done fail'.split()[out.state], out.have, out.total, out.ms) if found else None

    def startable(self, path):
        """stream_startable's answer for path: whether m3u_songs may queue it now."""
        return bool(self.fw.fn('stream_startable', C.c_int, C.c_char_p, C.c_char_p)(b'subsonic', path))

    def ahead(self):
        raw = self.fwr.tmp('q2-subsonic.ahead')
        return raw.decode().splitlines()[1:] if raw else None

    def playing(self):
        raw = self.fwr.tmp('q2-subsonic.playing')
        return raw.rstrip(b'\n') if raw else None

    def now(self):
        q = self.fwr.queued()
        return q[self.mcl.pos] if 0 <= self.mcl.pos < len(q) else None

    def sid(self, path):
        """The server id in a streamed song's file name, "... [s12].flac"."""
        p = path.decode() if isinstance(path, bytes) else path
        return p.rsplit('[', 1)[1].split(']', 1)[0] if '[' in p else None

    def fetched(self, sid):
        return [h for h in self.fake.tries(sid) if h['op'] in ('stream', 'download')]

    def change(self, forward):
        """Next or Previous pressed: player_change_music's hook (playback.c ringnav_change), which may keep the press
        itself (a Next that waits for its song, addon_next_waits); otherwise it reaches stock's change, done here as
        stock does it: advanced play's next or previous through the hooks stock's calls (ringnav_next,
        ringnav_prev), else the position on or back by one, wrapping under Repeat All (MCL_MODE 3)."""
        before = self.fwr.changes
        self.fw.fn('ringnav_change', C.c_int, C.c_int)(1 if forward else 0)
        if self.fwr.changes == before:
            return False  # kept by the payload
        active = self.fw.fn('playback_active', C.c_int)()
        n = len(self.fwr.queued())
        if forward:
            if active:
                self.fw.fn('ringnav_next', C.c_int, C.c_int)(0)
            else:
                self.mcl.pos = (self.mcl.pos + 1) % n if self.mcl.mode == 3 else min(self.mcl.pos + 1, n - 1)
        elif active:
            self.fw.fn('ringnav_prev', C.c_int)()
        else:
            self.mcl.pos = max(self.mcl.pos - 1, 0)
        return True

    def next(self):
        return self.change(True)

    def song_ends(self):
        """The song playing ends: mclAutoChange's next, through playback.c's hook (ringnav_next(1)) in advanced
        play, else stock's own (the position on by one; at the end, the first under Repeat All)."""
        if self.fw.fn('playback_active', C.c_int)():
            self.fw.fn('ringnav_next', C.c_int, C.c_int)(1)
        else:
            n = len(self.fwr.queued())
            self.mcl.pos = (self.mcl.pos + 1) % n if self.mcl.mode == 3 else min(self.mcl.pos + 1, n - 1)

    def prev(self):
        return self.change(False)

    def play_to(self, sid, timeout=60):
        """Next, each once its song is queued, until the song playing (by .playing) is sid's."""
        f = self.fwr
        end = time.monotonic() + timeout
        while self.sid(self.playing() or b'') != sid:
            assert time.monotonic() < end, f'never reached {sid}: playing {self.playing()}'
            f.until('.playing naming the song playing', lambda: self.playing() and self.playing() == self.now(), timeout=timeout)
            cur = self.playing()
            f.until(f'the song after {cur} queued', lambda: self.mcl.pos + 1 < len(f.queued()), timeout=timeout)
            n, pos = len(f.queued()), self.mcl.pos
            assert self.next(), f'Next at {pos} of {n} kept by the payload, playing {cur}, toasts {f.toasts}'
            f.until(f'.playing moved on from {cur}', lambda: self.playing() != cur, timeout=timeout)


@pytest.fixture
def seam(fw, mcl, request):
    marker = request.node.get_closest_marker('library')
    s = Seam(fw, mcl, **(marker.kwargs if marker else {}))
    before = request.session.testsfailed
    yield s
    s.close()
    if request.session.testsfailed == before:
        shutil.rmtree(s.root, ignore_errors=True)


def started_album(seam, album=1, at=0):
    """Album `album` pressed at song `at` in the Library, and its first song playing."""
    seam.start()
    rows = seam.album_rows(album)
    assert len(rows) == 10, rows
    seam.press(rows, at)
    f = seam.fwr
    head = f.until('.catplay written', lambda: f.tmp('q2-subsonic.catplay'), timeout=5).split(b'\n', 1)[0]
    assert head.split()[1] == str(at).encode()
    f.until('the first song queued and Now Playing open', lambda: f.loads and f.queued(), timeout=30)
    return rows


# (a) An album started from the Library: the first song downloads, the firmware's gate opens on it, the queue
# advances, and the songs after it are fetched ahead in the firmware's play order.
def test_an_album_list_plays_and_its_next_songs_are_fetched_ahead(seam):
    started_album(seam)
    f = seam.fwr
    first = f.queued()[0]
    assert seam.sid(first) == 's11'  # Album 1's first song (q2-fakesonic: s1-s10 are Album 0)
    # Queued only once it can start, as stream_startable lets m3u_songs: done, or arriving with its start's bytes
    # there. A song whole at its path a moment before the app's done line (renamed, the line still get with every
    # byte) is one; a run under load read the line in that moment and failed here (2026-11-23).
    st = seam.line(first)
    assert st is None or st[0] == 'done' or st[0] in ('get', 'stall') and seam.startable(first), \
        f'queued before it could start: {st}'
    assert f.until('.playing names it', lambda: seam.playing() == first, timeout=10)
    ahead = f.until('.ahead', lambda: seam.ahead(), timeout=10)
    assert f.tmp('q2-subsonic.ahead').startswith(b'#Q2AHEAD/1\tseq=')
    f.until('s12 and s13 fetched ahead', lambda: seam.fetched('s12') and seam.fetched('s13'), timeout=30)
    f.until('s12 queued', lambda: len(f.queued()) >= 2, timeout=30)
    assert seam.next()
    f.until('.playing follows the queue to s12', lambda: seam.sid(seam.playing() or b'') == 's12', timeout=10)
    f.until('s14 fetched once s12 plays', lambda: seam.fetched('s14'), timeout=30)
    assert not seam.fetched('s18'), 'fetched past Download Ahead\'s two'
    assert ahead is not None


# (b) Next onto a song not yet downloaded (s14 held by the server, so the firmware's queue ends at s13 when Next is
# pressed on it): the press waits for it (addon_next_waits), the app fetches it, and it plays. Never a stop, never
# stock's wrap to the queue's first song.
def test_next_onto_a_song_not_yet_downloaded_fetches_it_and_plays_it(seam):
    seam.start()
    seam.fake.cut('s14', 0, 5000)  # its first request answers nothing for 5 s, then closes
    t0 = time.monotonic()
    seam.press(seam.album_rows(1), 0)
    f = seam.fwr
    f.until('s11 to s13 queued', lambda: len(f.queued()) == 3, timeout=30)
    seam.play_to('s13')
    f.until('s14 asked for', lambda: seam.fetched('s14'), timeout=10)
    assert len(f.queued()) == 3, f's14 there already: nothing to wait for; {seam.fetched("s14")} {time.monotonic() - t0:.1f} s'
    assert not seam.next(), 'the Next reached stock: it wraps to the queue\'s first song'
    f.until('s14 queued and playing', lambda: seam.sid(seam.now() or b'') == 's14', timeout=30)
    f.until('.playing names s14', lambda: seam.sid(seam.playing() or b'') == 's14', timeout=10)
    assert [h['status'] for h in seam.fetched('s14')][-1] in (200, 206)
    f.until('s15 fetched ahead of s14', lambda: seam.fetched('s15'), timeout=30)


def queue_all_played(seam, most=60):
    """Every song in the firmware's queue played once, each to its end, in its play order (a pass's turn may play
    the song that ended again: Repeat All's next pass can start with it). Each end waits for .playing to name the
    song then playing, as the app follows that."""
    f = seam.fwr
    played = {seam.sid(seam.playing())}
    for _ in range(most):
        if len(played) >= len(f.queued()):
            return played
        seam.song_ends()
        f.until('.playing naming the song playing', lambda: seam.playing() == seam.now(), timeout=10)
        played.add(seam.sid(seam.playing()))
    raise AssertionError(f'{most} songs ended and only {sorted(played)} of {len(f.queued())} played')


# (c) Repeat All, every song downloaded so far played: the firmware's play order runs into its next pass, whose songs
# are already whole, and names them in .ahead; the app must still fetch the list's next song (the drive of
# 2026-11-22: a radio that stopped at its fourth song). Red with q2-subsonic 32bd2cd, green from cef5763. Under
# stock's Repeat All (MCL_MODE 3, the order the queue's), and under Shuffle Songs with Repeat All (playback.c's
# advanced play, its order drawn from a fixed seed so the run is the same each time).
@pytest.mark.library(songs=24, album=12)
@pytest.mark.parametrize('mode', ['repeat all', 'shuffle songs, repeat all'])
def test_repeat_all_keeps_fetching_once_every_downloaded_song_has_played(seam, mode):
    seam.fwr.settings[b'AHEAD'] = b'1'  # one place: a whole song of the next pass fills it
    seam.start()
    rows = seam.album_rows(0)
    assert len(rows) == 12
    seam.press(rows, 0)
    f = seam.fwr
    f.until('the first song queued', lambda: f.loads and f.queued(), timeout=30)
    if 'shuffle' in mode:
        seam.fw.var('playback__rng', C.c_uint).value = 0x2545f491
        assert seam.fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(0, SHUFFLE_SONGS)
        assert seam.fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(1, REPEAT_ALL)
    else:
        seam.mcl.mode = 3
    while len(f.queued()) < 12:  # each time every song downloaded so far has played to its end
        settle(seam)
        queue_all_played(seam)
        have = {seam.sid(p) for p in f.queued()}
        nxt = next((f's{i}' for i in range(1, 13) if f's{i}' not in have), None)
        if not nxt:
            break
        names = [seam.sid(p) for p in f.until('.ahead', lambda: seam.ahead(), timeout=10)]
        assert set(names) <= have, f'.ahead {names}: the next pass, songs already whole'
        f.until(f'{nxt}, the list\'s next, fetched though .ahead names only whole songs ({names})',
                lambda: seam.fetched(nxt), timeout=20)
        f.until(f'{nxt} joins the queue', lambda: nxt in {seam.sid(p) for p in f.queued()}, timeout=20)
    assert {seam.sid(p) for p in f.queued()} == {f's{i}' for i in range(1, 13)}, 'the whole list fetched and queued'


def settle(seam, quiet=1.5, timeout=30):
    """Steps until no song has joined the queue for quiet seconds: what the app fetches unasked is in."""
    f, end = seam.fwr, time.monotonic() + timeout
    n, since = len(f.queued()), time.monotonic()
    while time.monotonic() - since < quiet:
        assert time.monotonic() < end, 'the queue never settled'
        f.step()
        if len(f.queued()) != n:
            n, since = len(f.queued()), time.monotonic()
        time.sleep(0.05)


def m3u_lines(seam):
    """The queue file .play names, as the firmware reads it: its card paths, up to its read limit (M3U_MAX)."""
    path = seam.fwr.tmp('q2-subsonic.play').split(b' ', 1)[1].strip()
    raw = pathlib.Path(seam.fwr.host(path)).read_bytes()
    return [l for l in raw[:256 << 10].split(b'\n') if l.startswith(b'/')], len(raw)


# (d) The owner's library, 9,199 songs, played as one list: the app's queue file names a window of it that moves on
# as it plays (q2-subsonic queueAhead, 1,000 songs past the furthest played) instead of every song, which the
# firmware's 256 KB read (addon.c M3U_MAX) cut at about 2,300. Started far past that: the firmware lists the whole
# window (its queue and Up Next's pending songs, addon_pending_paths), an Up Next tap near the window's end moves
# it on to the list's end, Next goes past the first window's edge and Previous back across it.
@pytest.mark.library(songs=9199, album=10, size=4 << 10)
def test_a_9199_song_list_is_reachable_across_its_queue_files_windows(seam):
    seam.fwr.settings[b'AHEAD'] = b'3'
    seam.start()
    rows = seam.all_rows()
    assert len(rows) == 9199
    f = seam.fwr
    seam.press(rows, 8190)
    f.until('the session playing s8191', lambda: seam.sid(seam.playing() or b'') == 's8191', timeout=60)
    lines, size = m3u_lines(seam)
    assert [seam.sid(l) for l in lines] == [f's{i}' for i in range(8191, 9192)] and size < 256 << 10
    assert listed(seam) == [f's{i}' for i in range(8191, 9192)], 'the firmware lists the whole window'
    edge = next(p for p in pending(seam) if seam.sid(p) == 's9188')
    assert seam.fw.fn('addon_pending_play', C.c_int, C.c_char_p)(edge)  # Up Next: s9188 tapped
    f.until('s9188 playing', lambda: seam.sid(seam.now() or b'') == 's9188', timeout=60)
    f.until('the window moved on to the list\'s end', lambda: len(m3u_lines(seam)[0]) == 9199 - 8190, timeout=30)
    lines, size = m3u_lines(seam)
    assert [seam.sid(l) for l in lines] == [f's{i}' for i in range(8191, 9200)] and size < 256 << 10
    assert listed(seam)[-12:] == [f's{i}' for i in range(9188, 9200)], 'the firmware lists the moved window to its end'
    seam.play_to('s9193')  # past the first window's last song, s9191
    for want in ('s9192', 's9191', 's9190'):  # Previous back across that edge
        seam.prev()
        f.until(f'Previous to {want}', lambda: seam.sid(seam.playing() or b'') == want, timeout=10)


def pending(seam):
    """Up Next's pending songs as the firmware lists them (addon_pending_paths): the window's songs not queued."""
    n, size = C.c_uint(), C.c_uint()
    got = seam.fw.fn('addon_pending_paths', C.c_void_p, C.c_uint, C.POINTER(C.c_uint), C.POINTER(C.c_uint))(
        20000, C.byref(n), C.byref(size))
    if not got:
        return []
    out = C.string_at(got, size.value).split(b'\0')[:n.value]
    seam.fw.fn('free', None, C.c_void_p)(got)
    return out


def listed(seam):
    """The songs the firmware has of the list, by server id: its queue, then Up Next's pending songs."""
    return [seam.sid(p) for p in seam.fwr.queued() + pending(seam)]


@pytest.mark.library(songs=9199, album=10, size=4 << 10)
def test_a_shuffled_9199_song_list_is_handed_whole_and_its_window_read(seam):
    seam.start()
    rows = seam.all_rows()
    f = seam.fwr
    assert seam.fw.fn('shuffle_play', C.c_int, C.c_void_p)(f.queue_of(rows)) == 9199
    paths, at = f.handed  # Shuffle Songs' Now Playing, which loads it (stock's player_start: ringnav_load)
    seam.press(paths, at)
    head, *sent = f.until('.catplay', lambda: f.tmp('q2-subsonic.catplay'), timeout=10).decode().splitlines()
    assert sorted(sent) == sorted(r.decode() for r in rows), 'Shuffle Songs hands the app every song'
    start = int(head.split()[1])
    order = [p.rsplit('/', 1)[1].split('.')[0] for p in sent[start:]]
    f.until('the session playing', lambda: seam.playing() and seam.playing() == seam.now(), timeout=60)
    lines, size = m3u_lines(seam)
    assert [seam.sid(l) for l in lines] == order[:1001] and size < 256 << 10
    assert listed(seam) == order[:1001]
    seam.play_to(order[3])
    assert seam.sid(seam.playing()) == order[3]


# (e) A download cut mid-song: the app's .stream line goes stall (no byte for 2 s) while the firmware's reader sees
# a song still arriving, never failed; the app resumes it with Range from the byte it had; the file is whole.
@pytest.mark.library(songs=10, album=10, size=1 << 20)
def test_a_download_cut_mid_song_stalls_then_resumes_with_range(seam):
    f = seam.fwr
    (f.root/'tmp/q2clicks').write_bytes(b'')  # q2player is the player: the caps say grow, songs arrive in place
    seam.fwr.settings[b'AHEAD'] = b'1'
    seam.start()
    assert b'grow' in f.tmp('q2-addon.caps')
    seam.fake.cut('s2', 600 << 10, 4000)
    seam.press(seam.album_rows(0), 0)
    f.until('s1 queued', lambda: f.queued(), timeout=30)
    lines = f.until('s2 stalled', lambda: {p: l for p, l in seam.stream_lines().items()
                                           if seam.sid(p) == 's2' and l[0] == 'stall'}, timeout=30)
    (path, (state, have, total, ms)), = lines.items()
    assert have == 600 << 10 and total == 1 << 20, (have, total)
    assert seam.fw.fn('stream_startable', C.c_int, C.c_char_p, C.c_char_p)(b'subsonic', path) == 1
    f.until('s2 done', lambda: (seam.line(path) or ('',))[0] == 'done', timeout=30)
    tries = seam.fetched('s2')
    assert [(t['from'], t['status']) for t in tries] == [(0, 200), (600 << 10, 206)], tries
    data = pathlib.Path(f.host(path)).read_bytes()
    assert len(data) == 1 << 20 and data[:4] == b'fLaC'


# (f) Car Audio's cover asked through .coverask-iap (q2-coda's q2_iap.c writes it: "<seq> <px> <album id>") while a
# song downloads: answered (coverack-iap=<seq> 0) with the cover where the firmware's GetCover reads it
# (covers/512/<album>.jpg, coda_rc.c), the download still going.
@pytest.mark.library(songs=10, album=10, size=1 << 20)
def test_a_cover_asked_through_coverask_iap_while_a_song_downloads_is_answered(seam):
    f = seam.fwr
    seam.start()
    seam.fake.cut('s1', 300 << 10, 6000)  # s1 held mid-song for 6 s
    seam.press(seam.album_rows(0), 0)
    f.until('s1 arriving', lambda: any(seam.sid(p) == 's1' and l[0] in ('get', 'stall') for p, l in
                                       seam.stream_lines().items()), timeout=30)
    (f.root/'tmp/q2-subsonic.coverask-iap').write_bytes(b'7 512 al0\n')
    ack = f.until('coverack-iap', lambda: seam.state().get('coverack-iap'), timeout=10)
    assert ack == '7 0', ack
    assert any(seam.sid(p) == 's1' and l[0] in ('get', 'stall') for p, l in seam.stream_lines().items()), \
        'answered only after the download'
    assert (f.root/'card/.subsonic/covers/512/al0.jpg').read_bytes()[:2] == b'\xff\xd8'


# (g) Radio started on the song playing, stopped, and started again on it: each answered and played (the "radio
# isn't available" of a second start, fixed in 32bd2cd). Then a slow Radio answer and a list pressed after it: the
# list plays, and the radio arriving later does not replace it (fixed in 7a2f446).
@pytest.mark.library(songs=40, album=10)
def test_radio_starts_stops_and_starts_again_and_a_slow_one_never_replaces_a_later_list(seam):
    started_album(seam)
    f = seam.fwr
    song = f.until('.playing', lambda: seam.playing(), timeout=10)
    start = seam.fw.fn('addon_radio_start', C.c_int, C.c_char_p, C.c_uint)
    stop = seam.fw.fn('addon_radio_stop', C.c_int, C.c_uint)
    answer = seam.fw.fn('addon_radio_answer', C.c_int, C.c_uint)
    for seq in (101, 103):
        play = f.tmp('q2-subsonic.play')
        assert start(song, seq)
        f.until(f'radio {seq} answered', lambda: answer(seq) >= 0, timeout=20)
        assert answer(seq) == 0, f'radio {seq}: {seam.state()}'
        f.until('the radio playing', lambda: seam.state().get('radio'), timeout=20)
        f.until('the radio\'s queue file', lambda: f.tmp('q2-subsonic.play') != play, timeout=30)
        assert stop(seq + 1)
        f.until(f'stop {seq + 1} answered', lambda: answer(seq + 1) >= 0, timeout=20)
        assert answer(seq + 1) == 0 and not seam.state().get('radio')
    seam.fake.delay('getSimilarSongs2', 4000)
    assert start(song, 105)
    time.sleep(0.2)  # the request read, the slow answer begun (an ordering, not an assertion)
    seam.press(seam.album_rows(3), 0)
    f.until('the list\'s first song playing', lambda: seam.sid(seam.now() or b'') == 's31', timeout=30)
    f.until('radio 105 answered', lambda: answer(105) >= 0, timeout=20)
    for _ in range(20):  # 2 s more of the UI loop: nothing replaces the list
        f.step()
        time.sleep(0.1)
    assert seam.sid(seam.now() or b'') == 's31' and not seam.state().get('radio'), (seam.now(), seam.state())


# Two bugs this seam found (2026-11-22), each fixed on fix/ui-sweep and kept here as the test that showed it.

def up_next(seam, most=16):
    out = (C.c_uint * most)()
    k = seam.fw.fn('playback_upcoming', C.c_uint, C.c_void_p, C.c_uint)(out, most)
    q = seam.fwr.queued()
    return [seam.sid(q[i]) for i in out[:k]]


# Under Shuffle Songs, Next on the song at the queue's last index (s4 of s1-s4, Up Next s2 then s3) plays Up Next's
# first song. It was taken by addon_next_waits (addon.c), which waited for and played the list's next song not yet
# queued (s5); playback.c's change_music now lets it wait only when no song of the shuffle's pass is queued unplayed.
@pytest.mark.library(songs=24, album=12)
def test_under_shuffle_next_plays_up_nexts_first_song_at_the_queues_last_index_too(seam):
    seam.fwr.settings[b'AHEAD'] = b'3'
    seam.start()
    for sid in ('s5', 's6', 's7', 's8'):  # the list's next songs held at the server: the queue stays s1 to s4
        seam.fake.cut(sid, 0, 60000)
    seam.press(seam.album_rows(0), 0)
    f = seam.fwr
    f.until('s1 to s4 queued', lambda: len(f.queued()) == 4, timeout=30)
    seam.fw.var('playback__rng', C.c_uint).value = 0x2545f491
    assert seam.fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(0, SHUFFLE_SONGS)
    for _ in range(4):  # Next in Up Next's order, until a Next has been pressed on the queue's last song
        f.until('.playing naming the song playing', lambda: seam.playing() == seam.now(), timeout=10)
        last = seam.mcl.pos == len(f.queued()) - 1
        want, cur = up_next(seam)[0], seam.playing()
        seam.next()
        f.until(f'Next from {seam.sid(cur)} to Up Next\'s {want}', lambda: seam.playing() != cur and
                seam.playing() == seam.now(), timeout=10)
        assert seam.sid(seam.playing()) == want, f'Up Next said {want}; Next played {seam.sid(seam.playing())}'
        if last:
            return
    raise AssertionError(f'no Next was pressed on the queue\'s last song in four (order {up_next(seam)})')


# Download Ahead Off (n=0: "the app fetches only the song playing", docs/server-library.md): the firmware writes an
# .ahead for the list's seq just before its .catplay (stream_state.c ahead_start), so the app knows n before the
# first song is whole. Without it the app kept its default of two ahead and every list fetched two songs.
def test_download_ahead_off_fetches_only_the_song_playing(seam):
    seam.fwr.settings[b'AHEAD'] = b'0'
    started_album(seam)
    f = seam.fwr
    f.until('.ahead says Off', lambda: (f.tmp('q2-subsonic.ahead') or b'').split(b'\n')[0].endswith(b'n=0'), timeout=10)
    settle(seam)
    assert [s for s in ('s12', 's13') if seam.fetched(s)] == [], 'fetched ahead with Download Ahead Off'


# (j) A pressed song from a slow server, with q2player the player (grow): Now Playing's amount (np_stream_loading,
# what np_stream.c paints as "1.2 of 4.2 MB") counts up while the song downloads and has not started, never held at
# what came first (asked after the owner's V1UYi report, "the buffering progress is still hanging at like < 1MB
# until the song starts playing", which this chain passes). The stand-in player never parses a song, so the press
# shows for CATPLAY_SHOW_MS.
@pytest.mark.library(songs=20, album=10, size=4 << 20)
def test_a_pressed_songs_amount_counts_up_while_a_slow_server_sends_it(seam):
    f = seam.fwr
    (f.root/'tmp/q2clicks').write_bytes(b'')  # q2player is the player: the caps say grow, songs arrive in place
    seam.start()
    assert b'grow' in f.tmp('q2-addon.caps')
    seam.fake.rate('s11', 256 << 10)  # 4 MB in 16 s; its 180 s start at 256 KB, after about a second
    seam.press(seam.album_rows(1), 0)
    loading = seam.fw.fn('np_stream_loading', C.c_int, C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint))
    have, total, ms = C.c_uint(), C.c_uint(), C.c_uint()
    seen, lines = [], []  # (seconds since the first byte shown, have) as Now Playing reads it, and the app's line
    t0 = None
    end = time.monotonic() + 20
    while time.monotonic() < end:
        f.step()
        on = loading(C.byref(have), C.byref(total), C.byref(ms))
        row = next((l for p, l in seam.stream_lines().items() if seam.sid(p) == 's11'), None)
        if on and have.value and t0 is None:
            t0 = time.monotonic()
        if t0 is not None:
            t = time.monotonic() - t0
            seen.append((round(t, 1), have.value if on else None))
            lines.append((round(t, 1), row))
            if t > 6:
                break
        time.sleep(0.1)
    assert t0 is not None, f'the amount never showed: {seam.stream_lines()}\n{seam.why()}'
    shown = [h for _, h in seen if h is not None]
    assert f.queued(), 'never queued: the start gate did not open'
    # 6 s at 256 KB/s is about 1.5 MB: the amount must have gone well past the 256 KB the song starts at
    assert shown and max(shown) >= 1 << 20, f'amount held at {max(shown or [0])} bytes\nshown {seen}\nlines {lines}'
