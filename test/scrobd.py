#!/usr/bin/env python3
"""q2scrobd (scrobd/q2scrobd.c, docs/vamp-os-services.md#scrobbling) built for this machine and run against a stand-in
Q2PLAY/1 that sends it now lines: a listen (half the song, at most 240 s, for a song over 30 s) logged once in Rockbox's
AudioScrobbler 1.1 format with the library row's track number; seeks and pauses not counted; a short song, an
artist-less one not logged; .scrobble.ini's ALBUM_ARTIST and PERCENT; a song played again logged again. Run it as
python3 test/scrobd.py (pytest). Q2SCROBD_SOURCE builds another copy, to show a broken one fails here."""
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2SCROBD_SOURCE') or ROOT/'scrobd/q2scrobd.c')


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('scrobd')
    (out/'q2scrobd.c').write_bytes(SOURCE.read_bytes())
    inc = sorted(pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE', pathlib.Path.home()/'.cache/q2-thirdparty')).glob('out/sqlite-*/usr/include'))
    lib = next(pathlib.Path('/usr/lib').glob('*-linux-gnu/libsqlite3.so.0'))
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', *(['-isystem', str(inc[-1])] if inc else []),
                    '-o', str(out/'q2scrobd'), str(out/'q2scrobd.c'), str(lib)], check=True)
    return out/'q2scrobd'


class Play:
    """Q2PLAY/1's socket: greets, takes watch and now, then sends what the test gives it"""
    def __init__(self, root):
        (root/'run/vamp').mkdir(parents=True)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(root/'run/vamp/play.sock'))
        self.s.listen(1)
        self.s.settimeout(5)

    def accept(self):
        self.c, _ = self.s.accept()
        self.c.recv(100)
        self.c.sendall(b'Q2PLAY/1 ok\nok\n')

    def now(self, id, pos, len, state=1, title='Alpha', artist='Ann', album='First%20Light'):
        self.c.sendall(f'now title={title} artist={artist} album={album} state={state} pos={pos} len={len} volume=30 id={id}\n'.encode())


@pytest.fixture
def run(binary, tmp_path):
    for d in ('mnt/mmc', 'mnt/data/vamp'):
        (tmp_path/d).mkdir(parents=True)
    db = sqlite3.connect(tmp_path/'mnt/data/vamp/library.db')
    db.execute('create table songtable(id integer primary key, albumsonger, track)')
    db.executemany('insert into songtable values (?, ?, ?)', [(1, 'The Anns', 3), (2, '', 0)])
    db.commit()
    play = Play(tmp_path)
    p = subprocess.Popen([str(binary), '--root', str(tmp_path)], stderr=subprocess.PIPE, text=True)
    play.accept()
    yield tmp_path, play
    p.terminate()
    p.wait(5)


def log(root, wait=0.4):
    time.sleep(wait)
    f = root/'mnt/mmc/.scrobbler.log'
    return f.read_text().splitlines() if f.exists() else []


def heard(play, id, seconds, len, start=0, **kw):
    for pos in range(start, start + seconds + 1):
        play.now(id, pos, len, **kw)


def test_half_the_song_is_a_listen_logged_once_in_rockbox_s_format(run):
    root, play = run
    heard(play, 1, 19, 40)
    assert log(root) == [], '19 s of a 40 s song is not yet half'
    t = int(time.time())
    heard(play, 1, 10, 40, start=19)
    lines = log(root)
    assert lines[:3] == ['#AUDIOSCROBBLER/1.1', '#TZ/UTC', '#CLIENT/Vamp'] and len(lines) == 4, lines
    f = lines[3].split('\t')
    assert f[:6] == ['Ann', 'First Light', 'Alpha', '3', '40', 'L'] and f[7] == '' and abs(int(f[6]) - (t - 20)) <= 2


def test_long_songs_need_240_s(run):
    root, play = run
    heard(play, 1, 239, 1000)
    assert log(root) == []
    play.now(1, 240, 1000)
    assert len(log(root)) == 4


def test_seeks_and_pauses_are_not_heard(run):
    root, play = run
    play.now(1, 0, 40)
    play.now(1, 30, 40)  # a seek
    for pos in range(30, 40):
        play.now(1, pos, 40, state=2)  # paused, the position moving (a scrub)
    assert log(root) == []


def test_a_short_song_and_one_with_no_artist_are_not_logged(run):
    root, play = run
    heard(play, 2, 30, 30)
    heard(play, 1, 30, 40, artist='')
    assert log(root) == []


def test_album_artist_and_percent_from_the_ini(run):
    root, play = run
    (root/'mnt/mmc/.scrobble.ini').write_text('[SCROBBLE]\r\nALBUM_ARTIST=1\nPERCENT=90\n')
    heard(play, 1, 33, 40)
    assert log(root) == []
    play.now(1, 34, 40)
    lines = log(root)
    assert len(lines) == 4 and lines[3].startswith('The Anns\t')


def test_a_song_played_again_is_logged_again(run):
    root, play = run
    heard(play, 1, 20, 40)
    heard(play, 1, 20, 40)
    assert len(log(root)) == 5


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
