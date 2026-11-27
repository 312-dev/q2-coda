#!/usr/bin/env python3
"""q2playd (playd/q2playd.c, docs/playd.md) built for this machine, with a stand-in for q2player on its socket:
what it sends the player for each command from q2ui's FIFO and list file and from Q2PLAY/1, how it moves on when a
song ends, is cut or fails, what it writes for q2ui in /tmp/coda-rc.now, and what Q2PLAY/1's watchers are told.
The player's side, the real q2player against q2playd, is test/os_image.py's boot. Run it as python3 test/playd.py
(pytest). Q2PLAYD_SOURCE builds another copy of q2playd.c, to show a broken one fails here."""
import os
import urllib.parse
import pathlib
import re
import socket
import sqlite3
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2PLAYD_SOURCE') or ROOT/'playd/q2playd.c')
SONGS = [(1, '/mnt/mmc/Music/a.flac', 'Alpha', 'Ann', 'First'), (2, '/mnt/mmc/Music/b.mp3', 'Beta', 'Bob', 'First'),
         (3, '/mnt/mmc/Music/c}d.flac', 'Gamma', 'Cy', 'Second'), (5, '/mnt/mmc/Music/e.dsf', 'Epsilon', 'Eve', 'Third'),
         (6, '/mnt/mmc/Music/f.flac', 'Cut state=2 id=9 50%\nsecond line', 'A "B" C', 'x=y')]


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('playd')
    inc = sorted(pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE', pathlib.Path.home()/'.cache/q2-thirdparty')).glob('out/sqlite-*/usr/include'))
    lib = next(pathlib.Path('/usr/lib').glob('*-linux-gnu/libsqlite3.so.0'))
    src = out/'q2playd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', *(['-isystem', str(inc[-1])] if inc else []),
                    '-o', str(out/'q2playd'), str(src), str(lib)], check=True)
    return out/'q2playd'


SHIM = r"""
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
/* the DAC's ioctl recorded (Q2_IOCTL_LOG), every other passed on */
int ioctl(int fd, unsigned long req, ...) {
    va_list ap;
    va_start(ap, req);
    void *arg = va_arg(ap, void *);
    va_end(ap);
    if (req == 0xc0044d00ul) {
        FILE *f = fopen(getenv("Q2_IOCTL_LOG"), "a");
        if (f) fprintf(f, "%d\n", *(int *)arg), fclose(f);
        return 0;
    }
    int (*real)(int, unsigned long, ...) = (int (*)(int, unsigned long, ...))dlsym(RTLD_NEXT, "ioctl");
    return real(fd, req, arg);
}
"""


@pytest.fixture(scope='session')
def shim(tmp_path_factory):
    out = tmp_path_factory.mktemp('shim')
    (out/'shim.c').write_text(SHIM)
    subprocess.run(['cc', '-shared', '-fPIC', '-o', str(out/'shim.so'), str(out/'shim.c'), '-ldl'], check=True)
    return out/'shim.so'


CONFIG = '[SYSSET]\nLANGUAGE=2\n[PLAYSET]\nVOLUME=37\nMAXVOLUME=80\nGAPLESS=1\n[Q2POD]\nTHEME=0\n'


class Player:
    """q2player's side of /tmp/sock_play: what it was sent, and what it says."""
    def __init__(self, root):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        end = time.monotonic() + 5
        while True:
            try:
                self.s.connect(str(root/'tmp/sock_play'))
                break
            except OSError:
                if time.monotonic() > end:
                    raise
                time.sleep(0.02)
        self.s.settimeout(0.05)
        self.got, self.buf = [], b''
        self.info = '0|0|0|0|FLAC|16'

    def pump(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                data = self.s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            self.buf += data
            for m in re.finditer(rb'\{([^\\}]*)\\([^}]*)\}', self.buf):
                name, arg = m.group(1).decode(), m.group(2).decode()
                if name == 'player-status':
                    self.say('player-status', 'playing')
                elif name == 'media-info':
                    self.say('media-info', self.info)
                else:
                    self.got.append((name, arg))
            self.buf = self.buf[self.buf.rfind(b'}') + 1:]

    def say(self, name, arg):
        self.s.sendall(('{%s\\%s}' % (name, arg)).encode())

    def songs(self):
        return [(n, a.split('|')[0]) for n, a in self.got if n in ('set-songurl', 'set-nexturl')]

    def names(self):
        return [n for n, _ in self.got]


@pytest.fixture
def config():
    return CONFIG


@pytest.fixture
def run(binary, shim, tmp_path, config):
    for d in ('tmp', 'run', 'mnt/data', 'dev'):
        (tmp_path/d).mkdir(parents=True)
    (tmp_path/'dev/shanling_dac').write_text('')
    (tmp_path/'mnt/data/config.ini').write_text(config)
    db = sqlite3.connect(tmp_path/'mnt/data/database.db')
    db.execute('create table songtable(id integer primary key, fileurl, name, songer, album, track)')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?, 0)', SONGS)
    db.commit()
    db.close()
    env = dict(os.environ, LD_PRELOAD=str(shim), Q2_IOCTL_LOG=str(tmp_path/'dac.log'))
    p = subprocess.Popen([str(binary), '--root', str(tmp_path)], stderr=subprocess.PIPE, env=env)
    player = Player(tmp_path)
    yield tmp_path, player
    p.terminate()
    assert p.wait(5) == 0, 'q2playd ends on SIGTERM'


def fifo(root, *lines):
    fd = os.open(root/'tmp/coda-rc.cmdq', os.O_WRONLY | os.O_NONBLOCK)
    for i, line in enumerate(lines):
        os.write(fd, f'{i + 1} {line}\n'.encode())
    os.close(fd)


def play_list(root, ids, at, shuffle=0):
    (root/'tmp/coda-rc.list').write_text(''.join(f'{i}\n' for i in ids))
    fifo(root, f'play {at} {shuffle} {len(ids)}')


def now(root, wait=1.0):
    time.sleep(wait)
    text = (root/'tmp/coda-rc.now').read_text()
    return dict(line.split('=', 1) for line in text.splitlines())


def test_a_play_from_q2ui_starts_its_song_with_demo_s_fields(run):
    root, pl = run
    play_list(root, [1, 2, 3], 1)
    pl.pump()
    assert pl.got[0] == ('set-songurl', '/mnt/mmc/Music/b.mp3|ss=0|512|20|0|17|90')
    n = now(root)
    assert (n['title'], n['artist'], n['album'], n['state'], n['id']) == ('Beta', 'Bob', 'First', '1', '2')


def test_the_cache_follows_the_file_kind_and_a_brace_is_escaped(run):
    root, pl = run
    play_list(root, [3, 5], 0)
    pl.pump()
    assert pl.got[0] == ('set-songurl', '/mnt/mmc/Music/c%0x7dd.flac|ss=0|1024|20|0|17|90')
    pl.say('player-status', 'exit')
    pl.pump()
    assert pl.got[1] == ('set-songurl', '/mnt/mmc/Music/e.dsf|ss=0|4096|50|0|17|90')


def test_a_song_s_end_plays_the_next_and_the_queue_s_end_stops(run):
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    pl.say('player-status', 'exit')
    pl.pump()
    pl.say('player-status', 'exit')
    pl.pump()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/a.flac'), ('set-songurl', '/mnt/mmc/Music/b.mp3')]
    assert pl.names()[-1] == 'mcl-orderstop' and now(root)['state'] == '0'


def test_a_song_that_fails_is_passed_over(run):
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    pl.say('player-status', 'error')
    pl.pump()
    assert pl.songs()[-1] == ('set-songurl', '/mnt/mmc/Music/b.mp3')


def test_an_id_not_in_the_library_is_passed_over(run):
    root, pl = run
    play_list(root, [99, 2], 0)
    pl.pump()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')]


def test_next_cuts_the_song_and_its_close_is_no_end(run):
    root, pl = run
    play_list(root, [1, 2, 3], 0)
    pl.pump()
    fifo(root, 'next')
    pl.pump()
    pl.say('player-nextstatus', 'exit')  # the cut song's close (docs/player.md#events)
    pl.pump()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/a.flac'), ('set-nexturl', '/mnt/mmc/Music/b.mp3')]
    assert now(root)['id'] == '2'


def test_toggle_pauses_and_plays(run):
    root, pl = run
    play_list(root, [1], 0)
    pl.pump()
    fifo(root, 'toggle')
    pl.pump()
    assert pl.names()[-1] == 'mcl-pause' and now(root)['state'] == '2'
    fifo(root, 'toggle')
    pl.pump()
    assert pl.names()[-1] == 'mcl-play' and now(root)['state'] == '1'


def test_previous_restarts_a_song_past_three_seconds_else_goes_back(run):
    root, pl = run
    play_list(root, [1, 2], 1)
    pl.info = '900000|44100|200|12|FLAC|16'
    pl.pump(1.5)  # a status poll: 12 s in
    fifo(root, 'previous')
    pl.pump()
    assert pl.got[-1] == ('mcl-seek', '0')
    fifo(root, 'previous')
    pl.pump()
    assert pl.songs()[-1] == ('set-nexturl', '/mnt/mmc/Music/a.flac')


def test_the_position_and_length_come_from_media_info(run):
    root, pl = run
    play_list(root, [1], 0)
    pl.info = '900000|44100|200|42|FLAC|16'
    pl.pump(1.5)
    n = now(root, 0.6)
    assert (n['pos'], n['len']) == ('42000', '200')


def dac(root):
    """the levels the DAC was set to, each as (left, right)"""
    return [(int(v) >> 8, int(v) & 0xff) for v in (root/'dac.log').read_text().split()] if (root/'dac.log').exists() else []


def test_the_volume_starts_as_saved_reaches_the_dac_is_capped_and_is_saved(run):
    root, pl = run
    assert now(root, 0.5)['volume'] == '37' and dac(root) == [(37, 37)]
    fifo(root, 'volume 61')
    assert now(root)['volume'] == '61' and dac(root)[-1] == (61, 61)
    fifo(root, 'volume 101')
    assert now(root)['volume'] == '61', 'out of range: refused'
    fifo(root, 'volume 95')
    assert now(root)['volume'] == '80' and dac(root)[-1] == (80, 80), "Max Volume's cap"
    time.sleep(2.5)
    assert (root/'mnt/data/config.ini').read_text() == CONFIG.replace('VOLUME=37', 'VOLUME=80'), 'only the one line changed'


@pytest.mark.parametrize('config', [CONFIG.replace('MAXVOLUME=80', 'MAXVOLUME=80\nBOOTVOLFLAG=1\nBOOTVOLUME=12')])
def test_default_volume_sets_the_start(run):
    root, pl = run
    assert now(root, 0.5)['volume'] == '12' and dac(root) == [(12, 12)]


def test_a_player_that_goes_away_leaves_nothing_playing_and_the_next_is_taken(run):
    root, pl = run
    play_list(root, [1], 0)
    pl.pump()
    pl.s.close()
    assert now(root)['state'] == '0'
    again = Player(root)
    fifo(root, 'resume')
    again.pump()
    assert again.songs() == [('set-songurl', '/mnt/mmc/Music/a.flac')]


def test_shuffle_starts_with_the_chosen_song_and_plays_each_once(run):
    root, pl = run
    play_list(root, [1, 2, 3, 5], 2, shuffle=1)
    pl.pump()
    for _ in range(3):
        pl.say('player-status', 'exit')
        pl.pump()
    files = [f for _, f in pl.songs()]
    assert files[0] == '/mnt/mmc/Music/c%0x7dd.flac' and len(files) == 4 and len(set(files)) == 4


class Api:
    def __init__(self, root, hello='Q2PLAY/1'):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(str(root/'run/vamp/play.sock'))
        self.s.settimeout(3)
        self.f = self.s.makefile('rw')
        if hello:
            self.say(hello)

    def say(self, line):
        self.f.write(line + '\n')
        self.f.flush()

    def line(self):
        return self.f.readline().rstrip('\n')


def test_q2play1_greets_refuses_another_version_and_plays(run):
    root, pl = run
    a = Api(root)
    assert a.line() == 'Q2PLAY/1 ok'
    a.say('play 0 0 2')
    a.say('5')
    a.say('1')
    assert a.line() == 'ok'
    pl.pump()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/e.dsf')]
    a.say('now')
    assert a.line().startswith('now title=Epsilon artist=Eve album=Third state=1 ')
    a.say('bogus')
    assert a.line() == 'err bogus'
    b = Api(root, 'Q2PLAY/2')
    assert b.line() == 'err version' and b.line() == ''


def test_q2play1_watchers_hear_each_change(run):
    root, pl = run
    a = Api(root)
    assert a.line() == 'Q2PLAY/1 ok'
    a.say('watch')
    assert a.line() == 'ok'
    play_list(root, [2], 0)
    pl.pump()
    line = a.line()
    assert line.startswith('now title=Beta ') and ' state=1 ' in line and line.endswith(' id=2')


def fields(line):
    """A Q2PLAY/1 now line's fields, split on spaces and percent-decoded, as a client would"""
    words = line.split(' ')
    assert words[0] == 'now'
    return {k: urllib.parse.unquote(v) for k, v in (w.split('=', 1) for w in words[1:])}


def test_q2play1_free_text_is_percent_encoded_and_cannot_forge_a_field(run):
    root, pl = run
    a = Api(root)
    assert a.line() == 'Q2PLAY/1 ok'
    a.say('play 0 0 1')
    a.say('6')
    assert a.line() == 'ok'
    pl.pump()
    a.say('now')
    line = a.line()
    assert '\n' not in line and line.count(' state=') == 1 and line.count('=') == 8
    got = fields(line)
    assert (got['title'], got['artist'], got['album']) == ('Cut state=2 id=9 50%\nsecond line', 'A "B" C', 'x=y')
    assert (got['state'], got['id']) == ('1', '6')
    time.sleep(1)  # the now file: twice a second at most
    text = (root/'tmp/coda-rc.now').read_text().splitlines()
    assert 'title=Cut state=2 id=9 50% second line' in text  # one line: its line end a space
    assert [l for l in text if l.startswith('state=')] == ['state=1'] and [l for l in text if l.startswith('id=')] == ['id=6']



# ---- the cover (docs/playd.md#the-cover) ----

def test_the_playing_song_s_cover_is_q2ui_s_and_none_removes_it(run):
    root, pl = run
    db = sqlite3.connect(root/'mnt/data/database.db')
    db.execute('create table vamp_cover(id integer primary key, file text)')
    db.execute("insert into vamp_cover values (1, '/mnt/mmc/.vamp/covers/0000abcd.jpg')")
    db.commit()
    (root/'mnt/mmc/.vamp/covers').mkdir(parents=True)
    (root/'mnt/mmc/.vamp/covers/0000abcd.jpg').write_bytes(b'\xff\xd8 a cover')
    play_list(root, [1, 2], 0)
    pl.pump(0.6)
    assert (root/'tmp/externpic.jpg').read_bytes() == b'\xff\xd8 a cover'
    pl.say('player-status', 'exit')
    pl.pump(0.6)
    assert pl.songs()[-1][1] == '/mnt/mmc/Music/b.mp3' and not (root/'tmp/externpic.jpg').exists(), 'song 2 has none'


def test_a_stream_cache_copy_shows_its_server_row_and_cover(run):
    root, pl = run
    sock = catalogue(root)
    (root/'mnt/mmc/.navi/covers').mkdir(parents=True)
    (root/'mnt/mmc/.navi/covers/al1.jpg').write_bytes(b'\xff\xd8 album one')
    play_list(root, [SERVER + 1], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    (root/'tmp/navi.m3u').write_text('/mnt/mmc/.navi/stream/acct/hi/a/Cloud One [s1].flac\n')
    (root/'tmp/q2-navi.play').write_text('1 /tmp/navi.m3u\n')
    pl.pump(1.0)
    n = now(root, 0.3)
    assert (n['title'], n['artist'], n['album']) == ('Cloud One', 'Sky', 'Up')
    assert (root/'tmp/externpic.jpg').read_bytes() == b'\xff\xd8 album one'
    sock.close()


# ---- Vamp API 1 (docs/platform/playback.md) ----

def test_conformance_every_command_is_on_the_page():
    conformance.check_coverage('playback.md')


@pytest.mark.parametrize('n', range(len(conformance.blocks('playback.md'))))
def test_conformance_playback(run, n):
    root, pl = run
    conformance.replay(root/'run/vamp/play.sock', conformance.blocks('playback.md')[n])


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))


# ---- server songs: the catplay hand-off (docs/playd.md#server-songs) ----

SERVER = 0x40000000


def catalogue(root, listening=True):
    """the card's catalogue.db with two of navi's server rows; navi's browse socket there while it listens"""
    (root/'mnt/mmc/.vamp').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'mnt/mmc/.vamp/catalogue.db')
    db.execute('create table servertable(id integer primary key, fileurl, name, songer, album, track)')
    db.executemany('insert into servertable values (?, ?, ?, ?, ?, 0)', [
        (SERVER + 1, '/mnt/mmc/.navi/cat/al1/s1.flac', 'Cloud One', 'Sky', 'Up'),
        (SERVER + 2, '/mnt/mmc/.navi/cat/al1/s2.flac', 'Cloud Two', 'Sky', 'Up')])
    db.commit()
    db.close()
    if listening:
        s = socket.socket(socket.AF_UNIX)
        s.bind(str(root/'tmp/q2-navi.browse.sock'))
        return s


def wait_for(p, secs=3.0):
    end = time.time() + secs
    while time.time() < end and not p.exists():
        time.sleep(0.05)
    return p.exists()


def test_a_list_with_server_songs_is_handed_to_their_app_and_its_m3u_played(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [1, SERVER + 1, 2, SERVER + 2], 1)
    cp = root/'tmp/q2-navi.catplay'
    assert wait_for(cp), 'the catplay is written for navi'
    assert cp.read_text().splitlines() == ['1 1', '/mnt/mmc/Music/a.flac', '/mnt/mmc/.navi/cat/al1/s1.flac',
                                           '/mnt/mmc/Music/b.mp3', '/mnt/mmc/.navi/cat/al1/s2.flac']
    pl.pump(0.5)
    assert pl.songs() == [], 'nothing plays before the app answers'
    (root/'tmp/navi.m3u').write_text('#EXTM3U\n/mnt/mmc/.navi/cache/s1.flac\n/mnt/mmc/Music/b.mp3\n')
    (root/'tmp/q2-navi.play').write_text('1 /tmp/navi.m3u\n')
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/.navi/cache/s1.flac')]
    pl.say('player-status', 'exit')
    pl.pump(1.0)
    assert pl.songs()[-1] == ('set-songurl', '/mnt/mmc/Music/b.mp3'), "the m3u's next song follows"
    sock.close()


def test_a_catplay_unanswered_plays_the_card_songs_alone(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [1, SERVER + 1, 2], 1)
    assert wait_for(root/'tmp/q2-navi.catplay')
    pl.pump(5.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')], 'after 4 s, the card song at or after the one chosen'
    sock.close()


def test_a_list_whose_app_is_not_running_plays_its_card_songs_at_once(run):
    root, pl = run
    catalogue(root, listening=False)
    play_list(root, [SERVER + 1, 3], 0)
    pl.pump(1.0)
    assert not (root/'tmp/q2-navi.catplay').exists()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/c%0x7dd.flac')]


def test_an_answer_to_an_older_catplay_is_not_taken(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    (root/'tmp/navi.m3u').write_text('/mnt/mmc/.navi/cache/old.flac\n')
    (root/'tmp/q2-navi.play').write_text('7 /tmp/navi.m3u\n')
    pl.pump(1.0)
    assert pl.songs() == []
    sock.close()


# ---- sources and audio focus (docs/playd.md#sources-and-audio-focus) ----

def api(root):
    a = Api(root)
    assert a.line() == 'Q2PLAY/1 ok'
    return a


def queue_at_42(root, pl):
    """songs 1 and 2 queued, song 2 playing 42 s in"""
    play_list(root, [1, 2], 1)
    pl.info = '900000|44100|200|42|FLAC|16'
    pl.pump(1.5)
    assert now(root, 0.6)['pos'] == '42000'


def test_a_source_plays_its_url_and_gives_the_queue_back_paused_where_it_was(run):
    root, pl = run
    queue_at_42(root, pl)
    a = api(root)
    a.say('source http://10.0.0.9:8200/22.mp3?id%3D7 title=Delta%20Dawn artist=Dee album=Phone')
    assert a.line() == 'ok'
    pl.pump()
    assert pl.got[-1] == ('set-nexturl', 'http://10.0.0.9:8200/22.mp3?id=7|ss=0|512|20|0|17|90'), 'the URL decoded, a cut'
    pl.say('player-nextstatus', 'exit')
    a.say('now')
    got = fields(a.line())
    assert (got['title'], got['artist'], got['album'], got['state'], got['id']) == ('Delta Dawn', 'Dee', 'Phone', '1', '0')
    a.say('next')
    assert a.line() == 'err next', 'a source is one URL'
    pl.pump(1.5)  # a status poll: 42 s in
    a.say('previous')
    assert a.line() == 'err previous', 'not a restart of the URL'
    a.say('focus')
    assert a.line() == 'focus source=1 held=1'
    b = api(root)
    b.say('focus')
    assert b.line() == 'focus source=1 held=0'
    b.say('focus release')
    assert b.line() == 'err focus release', 'only its holder gives it back'
    a.say('focus release')
    assert a.line() == 'ok'
    pl.pump()
    assert pl.names()[-1] == 'mcl-stop'
    a.say('now')
    got = fields(a.line())
    assert (got['title'], got['state'], got['pos'], got['len'], got['id']) == ('Beta', '2', '42', '200', '2'), 'paused, where it was'
    a.say('resume')
    assert a.line() == 'ok'
    pl.pump()
    assert pl.got[-1] == ('set-songurl', '/mnt/mmc/Music/b.mp3|ss=42|512|20|0|17|90'), 'from where it was put aside'


def test_a_source_that_plays_out_tells_its_holder_and_gives_the_queue_back(run):
    root, pl = run
    queue_at_42(root, pl)
    a = api(root)
    a.say('source https://radio.example/live')
    assert a.line() == 'ok'
    pl.pump()
    pl.say('player-nextstatus', 'exit')
    pl.pump()
    a.say('now')
    assert fields(a.line())['title'] == 'live', "no title: the URL's last part"
    pl.say('player-status', 'exit')
    assert a.line() == 'focus lost why=end'
    n = now(root)
    assert (n['title'], n['state'], n['pos']) == ('Beta', '2', '42000')
    assert pl.songs()[-1] == ('set-nexturl', 'https://radio.example/live'), 'the queue is not started again on its own'


def test_a_source_that_does_not_open_says_error(run):
    root, pl = run
    a = api(root)
    a.say('source http://h/x.flac')
    assert a.line() == 'ok'
    pl.pump()
    assert pl.got[-1] == ('set-songurl', 'http://h/x.flac|ss=0|1024|20|0|17|90'), 'nothing played: no cut'
    pl.say('player-status', 'error')
    assert a.line() == 'focus lost why=error'
    assert now(root)['state'] == '0', 'an empty queue comes back stopped'


def test_a_play_takes_the_focus_and_the_queue_put_aside_is_dropped(run):
    root, pl = run
    queue_at_42(root, pl)
    a = api(root)
    a.say('source http://h/x.mp3')
    assert a.line() == 'ok'
    play_list(root, [3], 0)
    assert a.line() == 'focus lost why=taken'
    pl.pump()
    assert pl.songs()[-1] == ('set-nexturl', '/mnt/mmc/Music/c%0x7dd.flac')
    a.say('focus')
    assert a.line() == 'focus source=0 held=0'
    a.say('focus release')
    assert a.line() == 'err focus release'
    assert now(root)['id'] == '3'


def test_another_source_takes_it_and_the_first_queue_still_comes_back(run):
    root, pl = run
    queue_at_42(root, pl)
    a, b = api(root), api(root)
    a.say('source http://h/a.mp3')
    assert a.line() == 'ok'
    b.say('source http://h/b.mp3')
    assert b.line() == 'ok'
    assert a.line() == 'focus lost why=taken'
    b.say('source http://h/c.mp3')
    assert b.line() == 'ok', 'its holder moves on with no word'
    b.say('focus release')
    assert b.line() == 'ok'
    n = now(root)
    assert (n['title'], n['state'], n['pos']) == ('Beta', '2', '42000')


def test_a_stop_ends_the_source_and_the_queue_comes_back_stopped(run):
    root, pl = run
    queue_at_42(root, pl)
    a = api(root)
    a.say('source http://h/a.mp3')
    assert a.line() == 'ok'
    fifo(root, 'stop')
    assert a.line() == 'focus lost why=stop'
    n = now(root)
    assert (n['title'], n['state'], n['pos'], n['id']) == ('Beta', '0', '0', '2')
    fifo(root, 'resume')
    pl.pump()
    assert pl.got[-1] == ('set-songurl', '/mnt/mmc/Music/b.mp3|ss=0|512|20|0|17|90')


def test_a_holder_that_goes_away_gives_the_queue_back(run):
    root, pl = run
    queue_at_42(root, pl)
    a = api(root)
    a.say('source http://h/a.mp3')
    assert a.line() == 'ok'
    a.f.close(), a.s.close()  # the file holds the socket open too
    n = now(root)
    assert (n['title'], n['state'], n['pos']) == ('Beta', '2', '42000')
    pl.pump()
    assert pl.names()[-1] == 'mcl-stop'


@pytest.mark.parametrize('url', ['ftp://h/a.mp3', '/mnt/mmc/Music/a.flac', 'http://h/a%20b.mp3', 'http://h/a%7Cb.mp3', 'http://h/a%0Ab'])
def test_a_source_the_player_cannot_take_is_refused(run, url):
    root, pl = run
    a = api(root)
    a.say(f'source {url} title=X')
    assert a.line() == 'err source'
    a.say('focus')
    assert a.line() == 'focus source=0 held=0'


def test_q2play1_gives_the_position_in_seconds_and_the_now_file_in_ms(run):
    root, pl = run
    a = api(root)
    a.say('watch')
    assert a.line() == 'ok'
    play_list(root, [1], 0)
    pl.info = '900000|44100|200|42|FLAC|16'
    pl.pump(1.5)
    a.say('now')
    line = a.line()
    while not line.startswith('now ') or fields(line)['pos'] == '0':
        line = a.line()
    assert fields(line)['pos'] == '42', 'seconds, as docs/platform/playback.md says (q2scrobd counts them so)'
    assert now(root, 0.6)['pos'] == '42000', "the now file keeps the shim's milliseconds"
