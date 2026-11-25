#!/usr/bin/env python3
"""coda-rc end to end against an emulated Q2 (docs/remote-protocol.md, docs/internals.md#car-remote): the daemon and
demo's shim from a build, driven over TCP by tools/coda_rc_client.py as a phone would.

usage: coda_rc_e2e.py --emu DIR [--build BUILD] [--port 7720] [--shots OUT_DIR]
       coda_rc_e2e.py --pool BUILD [--wait S] [--shots OUT_DIR]

DIR is a q2-emu copy (its q2emu script, state/ and rootfs/) whose state/data/config.ini has CARREMOTE=1 under
[Q2POD]; with --build it boots that build first (q2emu start BUILD), else it uses the one already running. With
--pool it leases a pooled emulator instead (tools/ci/emu_pool.py, never slot 2, as the gate runs it), writes
CARREMOTE=1 into the copy's config.ini after the lease's reset of the settings and before the boot (the lease's
prepare), boots BUILD there, and talks to the daemon on that copy's own port (emu_pool.coda_port, 7700 plus the
slot); the lease is returned and the file put back whatever happens. Exit 3: no pooled emulator came free in --wait
seconds. The emulator shares this machine's network, so the daemon is on 127.0.0.1. The pairing code is read where the Q2
shows it from, the daemon's /tmp/coda-rc.pair, through /proc/PID/root of the process listening on the port.

Checks, in order: pairing (a fresh key, the Dialog's screenshot) and a second session on the kept key; browse
from the root to an album, its rows numbered apart from their titles, no number on search or Tracks rows, and
NOT_FOUND for ids that name nothing; Play of the album from its first song, then the State push that follows with
that song, and the queue at it; Pause, Play and Play/Pause, each played by the time its Ack comes; a Seek while paused, which stays paused at
the target; Next to another of the album's songs; Previous back; the album's
cover as an image; then SetMode (shuffle on and off, repeat one, all and off, each in the next State), Rate (three
hearts, Favorites, none; another song's id NOT_FOUND), PlayQueueItem (the third song; a stale id NOT_FOUND), and Resume
(the album again after another list, from the song it was left on; Resume while it plays carries on). Each step prints
one line; the exit status is the number of failures. A call that raises keeps the daemon's log, demo's (its unified-log
lines, again 15 s later as .later), the shim's answers and the card's unified log in a logs-HHMMSS folder of the run's
(keep_logs)."""
import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools/ci'))
import coda_rc_client as client  # noqa: E402
import emu_pool  # noqa: E402

FAILS = []
NAME = b'coda_rc_e2e'


def check(name, ok, detail=''):
    print(f"{'ok  ' if ok else 'FAIL'} {name}{': ' + detail if detail else ''}", flush=True)
    if not ok:
        FAILS.append(name)
    return ok


def listener(port, wait=90):
    """The pid listening on port, once one is."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        out = subprocess.run(['ss', '-Hltnp', f'sport = :{port}'], capture_output=True, text=True).stdout
        m = re.search(r'pid=(\d+)', out)
        if m:
            return int(m.group(1))
        time.sleep(1)
    return None


def until(s, want, wait=15):
    """State pushes until one whose parts satisfy want; that one, or None."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            p = s.wait_push(max(0.1, end - time.monotonic()))
        except TimeoutError:
            return None
        if want(p):
            return p
    return None


def playing(s):
    return s.get_state().get('now_playing', {})


def forget_runs(emu):
    """Earlier runs' pairings out of the Q2's peers file (coda_rc.c crc_peers_save: id[8] ltk[32] name[32]
    created[4] a phone), as Forget in Settings > Car Remote takes one out; the daemon rereads it within a second.
    Without this every run is one more phone, and the ninth is refused (PAIRING_FULL)."""
    path = emu / 'state/data/coda-rc/peers'
    if not path.exists():
        return
    raw = path.read_bytes()
    keep = b''.join(r for r in (raw[i:i + 76] for i in range(0, len(raw), 76)) if r[40:72].rstrip(b'\0') != NAME)
    if keep != raw:
        path.with_suffix('.e2e').write_bytes(keep)
        path.with_suffix('.e2e').chmod(0o600)
        path.with_suffix('.e2e').rename(path)
        time.sleep(1.5)


def car_remote_on(ini):
    """config.ini's text with Car Remote on: CARREMOTE=1 under [Q2POD]."""
    text, n = re.subn(r'(?m)^CARREMOTE=.*$', 'CARREMOTE=1', ini)
    if n:
        return text
    text, n = re.subn(r'(?m)^\[Q2POD\]\s*$', '[Q2POD]\nCARREMOTE=1', ini, count=1)
    return text if n else ini.rstrip('\n') + '\n[Q2POD]\nCARREMOTE=1\n'


def pooled(build, wait_s, argv):
    """main on a pooled emulator leased for build with Car Remote on (the module docstring's --pool)."""
    saved = {}
    os.environ.pop('LD_PRELOAD', None)  # q2-heavy's host preload: bwrap could not write the emulator's uid map

    def prepare(k):
        ini = emu_pool.copy_dir(k) / 'state/data/config.ini'
        saved[k] = ini.read_bytes() if ini.exists() else None
        ini.write_text(car_remote_on(saved[k].decode('utf-8', 'replace') if saved[k] else ''))

    end, slot = time.monotonic() + wait_s, None
    while slot is None:
        try:
            slot = emu_pool.get(build, f'coda-rc-e2e-{os.getpid()}', 0.5, False, avoid=(2,), prepare=prepare)
        except SystemExit as e:
            if 'leased' not in str(e) or time.monotonic() > end:
                print(f'coda_rc_e2e: could not run: {e}' if 'leased' not in str(e) else
                      f'coda_rc_e2e: could not run: no pooled emulator came free in {wait_s:.0f} s')
                return 3
            time.sleep(10)
    try:
        print(f'slot {slot}: {emu_pool.copy_dir(slot)}, Car Remote on port {emu_pool.coda_port(slot)}', flush=True)
        return main([*argv, '--emu', str(emu_pool.copy_dir(slot)), '--port', str(emu_pool.coda_port(slot))])
    finally:
        emu_pool.put(slot)
        if saved.get(slot) is not None:
            (emu_pool.copy_dir(slot) / 'state/data/config.ini').write_bytes(saved[slot])


LATE_S = 15  # keep_logs copies demo's log again this much later
# Where the run's daemon is, once known (run): keep_logs copies the logs from there when a call raises.
WHERE: dict = {}


def keep_logs(why):
    """The daemon's log, demo's (its unified-log lines: S90play keeps demo's stderr there, docs/internals.md#unified-log),
    the shim's last answers and the card's unified log into the run's folder, while the emulator still runs: a call
    that raised (a RemoteError's UNAVAILABLE) is told apart there, a refusal from the shim's own log line, a command
    never taken from its absence."""
    pid, emu, shots = WHERE.get('pid'), WHERE.get('emu'), WHERE.get('shots')
    if not shots or WHERE.get('kept'):  # once: --pool's main runs main, and both see the same error
        return
    WHERE['kept'] = True
    out = shots / f'logs-{time.strftime("%H%M%S")}'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'why.txt').write_text(why + '\n')
    sources = [pathlib.Path(f'/proc/{pid}/root/tmp/{n}') for n in ('coda-rc.log', 'q2-demo.log', 'q2-demo.err',
                                                                   'coda-rc.ack', 'coda-rc.now')] if pid else []
    if emu:
        sources += sorted((emu / 'state/mmc/.q2muse/logs').glob('q2-log.*'))
    for f in sources:
        try:
            shutil.copyfile(f, out / f.name)
        except OSError:
            pass
    # demo's log again a while later: a UI pass that held the loop past the call's 4 s is logged only once it ends
    # ("ui pass N ms"), after the copy above
    time.sleep(LATE_S)
    for f in sources[1:3]:
        try:
            shutil.copyfile(f, out / (f.name + '.later'))
        except OSError:
            pass
    print(f'logs kept in {out}', flush=True)


def main(argv=None):
    """run, its logs kept when a call raises."""
    try:
        return run(argv)
    except Exception as e:
        keep_logs(f'{type(e).__name__}: {e}')
        raise


def run(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    where = ap.add_mutually_exclusive_group(required=True)
    where.add_argument('--emu', type=pathlib.Path)
    where.add_argument('--pool', metavar='BUILD', help='lease a pooled emulator and boot BUILD on it')
    ap.add_argument('--build')
    ap.add_argument('--port', type=int, default=client.PORT)
    ap.add_argument('--wait', type=float, default=600, help='--pool: seconds to wait for a free emulator (600)')
    ap.add_argument('--shots', type=pathlib.Path)
    a = ap.parse_args(argv)
    if a.pool:
        rest = ['--shots', str(a.shots)] if a.shots else []
        return pooled(a.pool, a.wait, rest)
    q2emu = a.emu / 'q2emu'
    if not re.search(r'^CARREMOTE=1$', (a.emu / 'state/data/config.ini').read_text(errors='replace'), re.M):
        sys.exit(f'{a.emu}/state/data/config.ini: set CARREMOTE=1 under [Q2POD] first')
    if a.build:
        subprocess.run([str(q2emu), 'start', a.build], check=True)
    pid = listener(a.port)
    if not check('the daemon listens', pid is not None, f'port {a.port}, pid {pid}'):
        return 1
    shots = a.shots or pathlib.Path(tempfile.mkdtemp(prefix='coda-rc-e2e-'))
    shots.mkdir(parents=True, exist_ok=True)
    WHERE.update(pid=pid, emu=a.emu, shots=shots)
    keys = pathlib.Path(tempfile.mkdtemp(prefix='coda-rc-keys-'))
    forget_runs(a.emu)

    def code(prompt):
        time.sleep(1.5)  # the shim's poll shows the Dialog within 500 ms
        subprocess.run([str(q2emu), 'shot', str(shots / 'pairing.png')], check=False, capture_output=True)
        raw = (pathlib.Path(f'/proc/{pid}/root/tmp/coda-rc.pair')).read_text()
        check('the Q2 shows a code', re.fullmatch(r'\d{6}\ncoda_rc_e2e\n', raw) is not None, repr(raw))
        return raw.split()[0]

    s = client.connect('127.0.0.1', a.port, keys, 'coda_rc_e2e', pair=True, ask=code)
    check('paired', len(list(keys.iterdir())) == 1, f"device {s.info['device_id']}, model {s.info['model']}")
    time.sleep(1)
    s.sock.close()
    s = client.connect('127.0.0.1', a.port, keys, 'coda_rc_e2e', ask=lambda p: sys.exit('asked to pair again'))
    check('a second session on the kept key', s.kc is not None)

    root = s.browse('root')
    library = s.browse(root['items'][0]['id'])
    albums = s.browse('albums', limit=5)
    check('browse', [i['id'] for i in library['items']][:1] == ['tracks'] and albums['total'] > 0,
          f"root {[i['id'] for i in root['items']]}, library {len(library['items'])} rows, {albums['total']} albums")
    album = next(i for i in albums['items'] if i['browsable'])
    songs = s.browse(album['id'])
    tracks = [i for i in songs['items'] if i['playable']]
    check('an album lists its songs', len(tracks) >= 2, f"{album['title']}: {len(tracks)} songs")
    check('album rows: the title alone, the track number apart',
          all(t.get('number', 0) > 0 and not re.match(r'\d+ \u2022 ', t['title']) for t in tracks),
          f"{tracks[0].get('number')} / {tracks[0]['title']}")
    hits = [i for g in s.search(tracks[0]['title'].split()[0]) for i in g['items']]
    flat = s.browse('tracks', limit=20)['items']
    check('no number outside an album', hits != [] and not any(i.get('number') for i in hits + flat),
          f"{len(hits)} search rows, {len(flat)} Tracks rows")
    for node in ('album:no-such-album', 'artist:rnope', 'genre:gnope', 'year:1066', 'dir:no/such', 'pl:99',
                 'albums|name|ZZZ'):
        try:
            got = f"{len(s.browse(node)['items'])} rows"
        except client.RemoteError as e:
            got = e.name
        check(f'{node} names nothing', got == 'NOT_FOUND', got)

    s.subscribe(True)
    s.play(album['id'], start=tracks[0]['id'])
    p = until(s, lambda p: p.get('now_playing', {}).get('track_id') == tracks[0]['id']
              and p['now_playing']['play_state'] == 1)
    check('Play: the album from its first song', p is not None, f"{tracks[0]['title']}")
    q = s.queue(limit=3)
    check('the queue', q['total'] == len(tracks) and q['index'] == 0, f"{q['index'] + 1} of {q['total']}")
    p = until(s, lambda p: p.get('position', {}).get('position_ms', 0) > 0, 10)
    check('the position moves', p is not None, f"{p and p['position']['position_ms']} ms")

    s.transport('pause')  # its Ack comes once the player has paused
    check('Pause', playing(s).get('play_state') == 2)
    s.transport('pause')
    check('Pause when paused', playing(s).get('play_state') == 2)
    s.transport('play')
    check('Play again', playing(s).get('play_state') == 1)
    s.transport('toggle')
    check('Play/Pause', playing(s).get('play_state') == 2)
    # a seek while paused (the phone's scrubber, Android Auto's, the car's in iPhone mode) moves the song and leaves it
    # paused, the position on the target (docs/internals.md#scrub-ipod); the Play/Pause after it plays on from there
    st = s.get_state()
    before = st.get('position', {}).get('position_ms', 0) // 1000
    to = 1 if before >= 3 else before + 2
    s.transport('seek', to)
    time.sleep(3)  # demo's status poll, paused once a second, and the shim's state after it
    st = s.get_state()
    at = st.get('position', {}).get('position_ms', -1)
    check('Seek while paused stays paused', st.get('now_playing', {}).get('play_state') == 2 and at // 1000 == to,
          f"play_state {st.get('now_playing', {}).get('play_state')}, {before} s to {to} s: at {at} ms")
    s.transport('toggle')
    # Next and Previous as the wheel's buttons: with Shuffle on, Next is another of the album's songs, not the second
    ids = {t['id']: t['title'] for t in tracks}
    s.transport('next')
    p = until(s, lambda p: p.get('now_playing', {}).get('track_id') not in (None, '', tracks[0]['id']))
    after = p and p['now_playing']['track_id']
    check('Next', after in ids, f"{ids.get(after, after)} (shuffle {p and p['now_playing']['shuffle']})")
    s.transport('previous')  # within the song's first seconds: the one before it
    p = until(s, lambda p: p.get('now_playing', {}).get('track_id') == tracks[0]['id'])
    check('Previous', p is not None, tracks[0]['title'])

    art = playing(s).get('art') or album.get('art')
    data, mime = s.cover(art)
    magic = data[:2] == b'\xff\xd8' if mime == 'image/jpeg' else data[:8] == b'\x89PNG\r\n\x1a\n'
    (shots / 'cover').write_bytes(data)
    check('the cover', bool(data) and magic, f'{art}: {len(data)} bytes, {mime}')
    subprocess.run([str(q2emu), 'shot', str(shots / 'playing.png')], check=False, capture_output=True)

    caps = s.info.get('caps', 0)
    check('caps: modes, queue jump, rate, resume', caps & 0x780 == 0x780, f'{caps:#x}')

    def np_is(want, wait=8):
        return until(s, lambda p: 'now_playing' in p and want(p['now_playing']), wait) or {'now_playing': playing(s)}

    for shuffle, repeat in ((1, None), (None, 1), (None, 2), (0, 0)):
        s.set_mode(shuffle, repeat)
        np = playing(s)  # the shim wrote Now Playing before its ack
        ok = (shuffle is None or np['shuffle'] == shuffle) and (repeat is None or np['repeat'] == repeat)
        check(f'SetMode shuffle {shuffle} repeat {repeat}', ok, f"shuffle {np['shuffle']}, repeat {np['repeat']}")
        # A UI pass or two before the next command, which takes a stock Shuffle as its play order (playback.c
        # shuffle_own) and lists it to the car: the Resume below must still find the album's queue its own. Without the
        # wait that pass came between the commands only under load, and the Resume failed then alone (2026-11-24).
        time.sleep(1.5)
    np = playing(s)
    check('the playing song can be rated', np.get('rateable') is True, np['track_id'])
    for hearts in (3, 5, 0):
        s.rate(np['track_id'], hearts)
        got = np_is(lambda n: n.get('hearts') == hearts)['now_playing'].get('hearts')
        check(f'Rate {hearts}', got == hearts, f'hearts {got}')
    other = next(t['id'] for t in tracks if t['id'] != np['track_id'])
    try:
        s.rate(other, 2)
        got = 'Ack'
    except client.RemoteError as e:
        got = e.name
    check("Rate another song's id", got == 'NOT_FOUND', got)
    q = s.queue(limit=50)
    s.jump(2, q['items'][2]['id'])
    p = until(s, lambda p: p.get('now_playing', {}).get('track_id') == q['items'][2]['id'])
    check('PlayQueueItem: the third song', p is not None, q['items'][2]['title'])
    try:
        s.jump(1, q['items'][0]['id'])  # the first song's id at the second's place: stale (the pool's album has 3)
        got = 'Ack'
    except client.RemoteError as e:
        got = e.name
    check('PlayQueueItem with a stale id', got == 'NOT_FOUND', got)
    left = playing(s)['track_id']
    s.play(album['id'], resume=True)  # still the album's queue: it carries on
    check('Resume while the list plays', playing(s)['track_id'] == left, left)
    s.play('tracks', shuffle=True)
    until(s, lambda p: p.get('now_playing', {}).get('track_id') not in (None, left), 8)
    s.play(album['id'], resume=True)
    p = until(s, lambda p: p.get('now_playing', {}).get('track_id') == left)
    check('Resume after another list: the song it was left on', p is not None, ids.get(left, left))
    s.transport('pause')
    s.sock.close()
    forget_runs(a.emu)
    shutil.rmtree(keys)  # the run's key, of a pairing just forgotten
    print(f'{len(FAILS)} failed; screenshots and cover in {shots}')
    return len(FAILS)


if __name__ == '__main__':
    sys.exit(main())
