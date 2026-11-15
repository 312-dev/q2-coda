#!/usr/bin/env python3
"""coda-rc end to end against an emulated Q2 (docs/remote-protocol.md, docs/internals.md#car-remote): the daemon and
demo's shim from a build, driven over TCP by tools/coda_rc_client.py as a phone would.

usage: coda_rc_e2e.py --emu DIR [--build BUILD] [--port 7720] [--shots OUT_DIR]

DIR is a q2-emu copy (its q2emu script, state/ and rootfs/) whose state/data/config.ini has CARREMOTE=1 under
[Q2POD]; with --build it boots that build first (q2emu start BUILD), else it uses the one already running. The
emulator shares this machine's network, so the daemon is on 127.0.0.1. The pairing code is read where the Q2
shows it from, the daemon's /tmp/coda-rc.pair, through /proc/PID/root of the process listening on the port.

Checks, in order: pairing (a fresh key, the Dialog's screenshot) and a second session on the kept key; browse
from the root to an album; Play of the album from its first song, then the State push that follows with that
song, and the queue at it; Pause and Play as pushes; Next to another of the album's songs; Previous back; the album's
cover as an image. Each step prints one line; the exit status is the number of failures."""
import argparse
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
import coda_rc_client as client  # noqa: E402

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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--emu', required=True, type=pathlib.Path)
    ap.add_argument('--build')
    ap.add_argument('--port', type=int, default=client.PORT)
    ap.add_argument('--shots', type=pathlib.Path)
    a = ap.parse_args(argv)
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

    s.subscribe(True)
    s.play(album['id'], start=tracks[0]['id'])
    p = until(s, lambda p: p.get('now_playing', {}).get('track_id') == tracks[0]['id']
              and p['now_playing']['play_state'] == 1)
    check('Play: the album from its first song', p is not None, f"{tracks[0]['title']}")
    q = s.queue(limit=3)
    check('the queue', q['total'] == len(tracks) and q['index'] == 0, f"{q['index'] + 1} of {q['total']}")
    p = until(s, lambda p: p.get('position', {}).get('position_ms', 0) > 0, 10)
    check('the position moves', p is not None, f"{p and p['position']['position_ms']} ms")

    s.transport('pause')
    check('Pause', until(s, lambda p: p.get('now_playing', {}).get('play_state') == 2) is not None)
    s.transport('play')
    check('Play again', until(s, lambda p: p.get('now_playing', {}).get('play_state') == 1) is not None)
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
    s.transport('pause')
    s.sock.close()
    forget_runs(a.emu)
    shutil.rmtree(keys)  # the run's key, of a pairing just forgotten
    print(f'{len(FAILS)} failed; screenshots and cover in {shots}')
    return len(FAILS)


if __name__ == '__main__':
    sys.exit(main())
