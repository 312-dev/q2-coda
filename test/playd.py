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
    return build(tmp_path_factory.mktemp('playd'))


def build(out):
    """q2playd from SOURCE into the folder out (test/dlnad.py builds it this way too)"""
    inc = sorted(pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE', pathlib.Path.home()/'.cache/q2-thirdparty')).glob('out/sqlite-*/usr/include'))
    lib = next(pathlib.Path('/usr/lib').glob('*-linux-gnu/libsqlite3.so.0'))
    src = out/'q2playd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(conformance.PERM), *(['-isystem', str(inc[-1])] if inc else []),
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
def prepare():
    """files a test needs before q2playd starts: a function of the root, or None"""
    return None


@pytest.fixture
def run(binary, shim, tmp_path, config, prepare):
    for d in ('tmp', 'run', 'mnt/data', 'dev'):
        (tmp_path/d).mkdir(parents=True)
    (tmp_path/'dev/shanling_dac').write_text('')
    (tmp_path/'mnt/data/config.ini').write_text(config)
    conformance.accounts(tmp_path)
    db = sqlite3.connect(tmp_path/'mnt/data/database.db')
    db.execute('create table songtable(id integer primary key, fileurl, name, songer, album, track)')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?, 0)', SONGS)
    db.commit()
    db.close()
    if prepare:
        prepare(tmp_path)
    env = dict(os.environ, LD_PRELOAD=str(shim), Q2_IOCTL_LOG=str(tmp_path/'dac.log'))
    p = subprocess.Popen([str(binary), '--root', str(tmp_path)], stderr=subprocess.PIPE, env=env)
    player = Player(tmp_path)
    wait_for(tmp_path/'tmp/coda-rc.cmdq', 5)  # made after the player's socket, which Player waited for
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


def acks(root):
    p = root/'tmp/coda-rc.ack'
    return [tuple(map(int, l.split())) for l in p.read_text().splitlines()] if p.exists() else []


def test_each_fifo_command_is_answered_in_the_ack_file_as_demo_s_shim_answers(run):
    """coda_rc_shim.c cr_ack: "<seq> <code>" for a command and the three before it, newest first; 0 done, 1 not done.
    coda-rc and q2-iap wait for their seq there (coda_rc.c ack_poll)"""
    root, pl = run
    (root/'tmp/coda-rc.list').write_text('1\n2\n')
    fd = os.open(root/'tmp/coda-rc.cmdq', os.O_WRONLY | os.O_NONBLOCK)
    os.write(fd, b'1342177281 play 0 0 2\n')  # q2-iap's numbers start at 0x51000000
    os.close(fd)
    pl.pump()
    assert wait_for_value(lambda: acks(root), [(1342177281, 0)]), acks(root)
    fd = os.open(root/'tmp/coda-rc.cmdq', os.O_WRONLY | os.O_NONBLOCK)
    os.write(fd, b'70 bogus\n71 pause\n72 resume\n73 next\n')
    os.close(fd)
    assert wait_for_value(lambda: acks(root), [(73, 0), (72, 0), (71, 0), (70, 1)]), acks(root)


def wait_for_value(get, want, s=5):
    end = time.monotonic() + s
    while time.monotonic() < end and get() != want:
        time.sleep(0.05)
    return get() == want


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
    land(root, '/mnt/mmc/.navi/stream/acct/hi/a/Cloud One [s1].flac')
    (root/'tmp/navi.m3u').write_text('/mnt/mmc/.navi/stream/acct/hi/a/Cloud One [s1].flac\n')
    (root/'tmp/q2-navi.play').write_text('1 /tmp/navi.m3u\n')
    pl.pump(1.0)
    n = now(root, 0.3)
    assert (n['title'], n['artist'], n['album']) == ('Cloud One', 'Sky', 'Up')
    assert (root/'tmp/externpic.jpg').read_bytes() == b'\xff\xd8 album one'
    sock.close()


# ---- Vamp API 1 (docs/platform/playback.md) ----

def test_the_output_moves_the_song_where_it_is_and_keeps_a_level_each(run):
    """q2btd's `output` (docs/playd.md#the-output): the player told its way, the song started again where it is heard
    (paused if it was), Bluetooth at unity in the player (absolute volume) or at the player's gain (soft), and each
    output's own level kept in stock's file, VOLUME the DAC's and BT_VOLUME Bluetooth's"""
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    fifo(root, 'seek 42')
    pl.pump()
    pl.got.clear()
    fifo(root, 'output bluetooth')
    pl.pump()
    assert pl.got == [('mcl-setoutputway', '1'), ('mcl-setvol', str(100 << 8 | 100)), ('mcl-stop', 'null'),
                      ('set-songurl', '/mnt/mmc/Music/a.flac|ss=42|1024|20|0|17|90')], pl.got
    assert now(root)['volume'] == '37', 'no Bluetooth level saved yet: the DAC\'s'
    before = dac(root)
    fifo(root, 'volume 20')
    pl.pump()
    assert dac(root) == before, 'the DAC left alone on Bluetooth'
    time.sleep(2.5)
    text = (root/'mnt/data/config.ini').read_text()
    assert 'BT_VOLUME=20' in text and 'VOLUME=37' in text.replace('BT_VOLUME', ''), text
    fifo(root, 'pause')
    pl.pump()
    pl.got.clear()
    fifo(root, 'output dac')
    pl.pump()
    assert [n for n, _ in pl.got] == ['mcl-setoutputway', 'mcl-stop', 'set-songurl', 'mcl-pause'], pl.got
    assert now(root)['volume'] == '37' and dac(root)[-1] == (37, 37), "the DAC's level back"
    pl.got.clear()
    fifo(root, 'output bluetooth soft')
    pl.pump()
    assert ('mcl-setvol', str(20 << 8 | 20)) in pl.got and now(root)['volume'] == '20', 'soft: the player\'s gain at the Bluetooth level'
    pl.got.clear()
    fifo(root, 'output bluetooth soft')
    pl.pump()
    assert pl.got == [], 'the output it has: nothing sent'


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


def catalogue(root, listening=True, copies=None):
    """the card's catalogue.db with two of navi's server rows, and servermeta with copies' offline copies ({id: path});
    navi's browse socket there while it listens"""
    (root/'mnt/mmc/.vamp').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'mnt/mmc/.vamp/catalogue.db')
    db.execute('create table servertable(id integer primary key, fileurl, name, songer, album, track)')
    db.executemany('insert into servertable values (?, ?, ?, ?, ?, 0)', [
        (SERVER + 1, '/mnt/mmc/.navi/cat/al1/s1.flac', 'Cloud One', 'Sky', 'Up'),
        (SERVER + 2, '/mnt/mmc/.navi/cat/al1/s2.flac', 'Cloud Two', 'Sky', 'Up')])
    db.execute('create table servermeta(id integer primary key, app, sid, local)')
    db.executemany('insert into servermeta values (?, ?, ?, ?)',
                   [(i, 'navi', f's{i - SERVER}', (copies or {}).get(i)) for i in (SERVER + 1, SERVER + 2)])
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
    land(root, '/mnt/mmc/.navi/cache/s1.flac', '/mnt/mmc/Music/b.mp3')
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


COPY1 = '/mnt/mmc/.navi/stream/acct/hi/a/Cloud One [s1].flac'


def test_a_catplay_unanswered_plays_the_card_songs_and_the_offline_copies(run):
    """docs/playd.md#offline-copies: the list the firmware plays itself, card rows and server rows by their copies"""
    root, pl = run
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'mnt/mmc/.navi/covers').mkdir(parents=True)
    (root/'mnt/mmc/.navi/covers/al1.jpg').write_bytes(b'\xff\xd8 album one')
    play_list(root, [1, SERVER + 1, SERVER + 2, 2], 1)
    assert wait_for(root/'tmp/q2-navi.catplay')
    pl.pump(5.0)
    assert pl.songs() == [('set-songurl', COPY1)], 'after 4 s, the chosen server song by its copy'
    n = now(root, 0.3)
    assert (n['title'], n['artist'], n['album'], n['id']) == ('Cloud One', 'Sky', 'Up', str(SERVER + 1))
    assert (root/'tmp/externpic.jpg').read_bytes() == b'\xff\xd8 album one', "its server row's cover"
    pl.say('player-status', 'exit')
    pl.pump(1.0)
    assert pl.songs()[-1] == ('set-songurl', '/mnt/mmc/Music/b.mp3'), 'the server song with no copy passed over'
    sock.close()


def test_an_app_whose_server_is_down_has_its_list_played_offline_at_once(run):
    root, pl = run
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'tmp/q2-navi.state').write_text('status=idle\nserver=down\n')
    play_list(root, [SERVER + 2, SERVER + 1, 3], 1)
    pl.pump(1.0)
    assert not (root/'tmp/q2-navi.catplay').exists(), 'no catplay to an app whose server is gone'
    assert pl.songs() == [('set-songurl', COPY1)]
    sock.close()


def test_a_server_song_with_no_copy_while_its_server_is_down_is_refused(run):
    root, pl = run
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'tmp/q2-navi.state').write_text('server=down\n')
    play_list(root, [2], 0)
    pl.pump()
    a = api(root)
    a.say('play 0 0 2')
    a.say(str(SERVER + 2))
    a.say(str(SERVER + 1))
    assert a.line() == 'err play', 'the chosen song cannot play: Not available offline'
    pl.pump()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')] and now(root, 0.3)['id'] == '2', 'what played goes on'
    sock.close()


def test_an_answer_s_copy_named_by_servermeta_shows_its_server_row(run):
    root, pl = run
    sock = catalogue(root, copies={SERVER + 2: '/mnt/mmc/.navi/cache/s2.flac'})
    play_list(root, [SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    land(root, '/mnt/mmc/.navi/cache/s2.flac')
    (root/'tmp/navi.m3u').write_text('/mnt/mmc/.navi/cache/s2.flac\n')
    (root/'tmp/q2-navi.play').write_text('1 /tmp/navi.m3u\n')
    pl.pump(1.0)
    n = now(root, 0.3)
    assert (n['title'], n['artist'], n['album']) == ('Cloud Two', 'Sky', 'Up')
    sock.close()


class FakePkgd:
    """q2pkgd's socket (Q2PKG/1): the lines it was sent, answered with answer"""
    def __init__(self, root, answer='ok'):
        import threading
        self.lines, self.answer = [], answer
        (root/'run/vamp').mkdir(parents=True, exist_ok=True)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(root/'run/vamp/pkg.sock'))
        self.s.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            f = c.makefile('rw')
            for line in f:
                line = line.rstrip('\n')
                self.lines.append(line)
                f.write(('Q2PKG/1 ok' if line == 'Q2PKG/1' else self.answer) + '\n')
                f.flush()
            c.close()


def test_a_stopped_app_is_started_through_q2pkgd_and_handed_the_list_once_it_listens(run):
    root, pl = run
    pk = FakePkgd(root)
    catalogue(root, listening=False)
    play_list(root, [1, SERVER + 1], 1)
    pl.pump(1.5)
    assert pk.lines == ['Q2PKG/1', 'start navi'], 'q2playd asks q2pkgd to start navi'
    assert not (root/'tmp/q2-navi.catplay').exists(), 'no catplay before the app listens: it would take one as answered'
    assert pl.songs() == [], 'nor anything played'
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(root/'tmp/q2-navi.browse.sock'))
    assert wait_for(root/'tmp/q2-navi.catplay')
    land(root, '/mnt/mmc/.navi/cache/s1.flac')
    (root/'tmp/navi.m3u').write_text('/mnt/mmc/.navi/cache/s1.flac\n')
    (root/'tmp/q2-navi.play').write_text('1 /tmp/navi.m3u\n')
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/.navi/cache/s1.flac')]
    s.close()


def test_an_app_q2pkgd_will_not_start_plays_the_card_songs_at_once(run):
    root, pl = run
    pk = FakePkgd(root, 'err not-added')
    catalogue(root, listening=False)
    play_list(root, [SERVER + 1, 3], 0)
    pl.pump(1.0)
    assert pk.lines == ['Q2PKG/1', 'start navi']
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/c%0x7dd.flac')]


def test_a_started_app_that_does_not_listen_within_15_s_has_its_card_songs_played(run):
    root, pl = run
    FakePkgd(root)
    catalogue(root, listening=False)
    play_list(root, [1, SERVER + 1, 2], 1)
    pl.pump(13.5)
    assert pl.songs() == [], 'it has 15 s'
    pl.pump(3.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')]
    assert not (root/'tmp/q2-navi.catplay').exists()


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



# ---- Wi-Fi's state (docs/playd.md#wi-fis-state) ----

class FakeNet:
    """q2netd's socket (Q2NET/1): the commands it was sent; status answers self.status"""
    def __init__(self, root, status):
        import threading
        self.lines, self.status = [], status
        (root/'run/vamp').mkdir(parents=True, exist_ok=True)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(root/'run/vamp/net.sock'))
        self.s.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            f = c.makefile('rw')
            for line in f:
                line = line.rstrip('\n')
                if line != 'Q2NET/1':
                    self.lines.append(line)
                f.write(('Q2NET/1 ok' if line == 'Q2NET/1' else f'status {self.status}' if line == 'status' else 'ok') + '\n')
                f.flush()
            c.close()


@pytest.mark.parametrize('status', ['state=off ssid= ip= smart=0 held=0', 'state=searching ssid= ip= smart=1 held=0'])
def test_with_no_wi_fi_link_a_server_list_plays_offline_at_once(run, status):
    """the user's Wi-Fi off, or on with no network joined: the firmware's networkGetConnectStatus() 0"""
    root, pl = run
    net = FakeNet(root, status)
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    play_list(root, [SERVER + 1, 3], 0)
    pl.pump(1.0)
    assert not (root/'tmp/q2-navi.catplay').exists() and pl.songs() == [('set-songurl', COPY1)]
    assert 'wake' not in net.lines
    sock.close()


def test_smart_wi_fi_s_radio_off_is_woken_and_the_list_handed_over_once_the_link_is_up(run):
    root, pl = run
    net = FakeNet(root, 'state=off ssid= ip= smart=1 held=0')
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    play_list(root, [SERVER + 1, 3], 0)
    pl.pump(2.0)
    assert 'wake' in net.lines, 'the radio woken for the press'
    assert not (root/'tmp/q2-navi.catplay').exists() and pl.songs() == [], 'nothing until the link'
    net.status = 'state=connected ssid=Home ip=10.0.0.9 smart=1 held=1800'
    assert wait_for(root/'tmp/q2-navi.catplay'), 'handed to the app once connected'
    sock.close()


def test_no_link_within_15_s_of_the_wake_plays_the_list_offline(run):
    root, pl = run
    net = FakeNet(root, 'state=off ssid= ip= smart=1 held=0')
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    play_list(root, [SERVER + 1, 3], 0)
    pl.pump(2.0)
    net.status = 'state=searching ssid= ip= smart=1 held=1800'  # woken, joining nothing
    pl.pump(11.5)
    assert pl.songs() == [], 'it has 15 s'
    pl.pump(3.0)
    assert pl.songs() == [('set-songurl', COPY1)] and not (root/'tmp/q2-navi.catplay').exists()
    sock.close()


# ---- the answer as it grows (docs/playd.md#the-answer-as-it-grows) ----

def land(root, *paths):
    """songs written whole on the stand-in card, as the app renames a download into place"""
    for p in paths:
        f = root/p.lstrip('/')
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b'song')


def answer(root, *lines, seq=1):
    (root/'tmp/navi.m3u').write_text(''.join(l + '\n' for l in lines))
    (root/'tmp/q2-navi.play').write_text(f'{seq} /tmp/navi.m3u\n')


def test_an_answer_s_song_not_there_yet_is_waited_for_and_played_as_it_lands(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    one, two = '/mnt/mmc/.navi/stream/a/hi/x/Cloud One [s1].flac', '/mnt/mmc/.navi/stream/a/hi/x/Cloud Two [s2].flac'
    land(root, one)
    answer(root, one, two)
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', one)]
    pl.say('player-status', 'exit')
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', one)] and pl.names()[-1] != 'mcl-orderstop', 'the next not there yet: no end, no skip'
    n = now(root, 0.3)
    assert (n['state'], n['title']) == ('1', 'Cloud Two'), 'Now Playing on the song waited for'
    land(root, two)
    pl.pump(1.0)
    assert pl.songs()[-1] == ('set-songurl', two), 'played as it lands'
    sock.close()


def test_next_before_the_next_song_lands_waits_for_it(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    one, two = '/mnt/mmc/.navi/stream/a/hi/x/Cloud One [s1].flac', '/mnt/mmc/.navi/stream/a/hi/x/Cloud Two [s2].flac'
    land(root, one)
    answer(root, one, two)
    pl.pump(1.0)
    a = api(root)
    a.say('next')
    assert a.line() == 'ok'
    pl.pump(0.5)
    assert pl.names()[-1] == 'mcl-stop', 'the song playing stopped'
    n = now(root, 0.3)
    assert (n['state'], n['title']) == ('1', 'Cloud Two')
    sent = len(pl.got)
    a.say('next')
    assert a.line() == 'ok', 'a next while it waits: nothing changes (addon_next_waits answers it the same)'
    pl.pump(0.3)
    assert len(pl.got) == sent
    land(root, two)
    pl.pump(1.0)
    assert pl.songs()[-1] == ('set-songurl', two)
    sock.close()

# ---- Download Ahead (docs/server-library.md#download-ahead-and-the-streaming-state, docs/playd.md#download-ahead) ----

def ahead(root, secs=2.0, want=None):
    """navi's .ahead as (seq, n, lines), waited for until it is want when one is given"""
    p = root/'tmp/q2-navi.ahead'
    end = time.time() + secs
    while True:
        got = None
        if p.exists():
            head, *lines = p.read_text().splitlines()
            f = dict(x.split('=', 1) for x in head.split('\t')[1:])
            assert head.split('\t')[0] == '#Q2AHEAD/1'
            got = (int(f['seq']), int(f['n']), lines)
        if want is None or got == want or time.time() >= end:
            return got
        time.sleep(0.05)


def test_download_ahead_is_written_before_the_catplay_with_n_and_no_songs(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    assert ahead(root, 0) == (1, 2, []), "the catplay's seq, the default 2, and no songs: the queue holds none yet"
    sock.close()


def test_download_ahead_names_the_songs_after_the_one_playing_as_they_land_and_move(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    one, two, three = (f'/mnt/mmc/.navi/stream/a/hi/x/S{i} [s{i}].flac' for i in (1, 2, 3))
    land(root, one, two)
    answer(root, one, two, three)
    pl.pump(1.0)
    assert ahead(root, want=(1, 2, [two])) == (1, 2, [two]), 'only the songs the queue holds: three is not there yet'
    land(root, three)
    pl.pump(0.6)
    assert ahead(root, want=(1, 2, [two, three])) == (1, 2, [two, three]), 'three landed: queued, so named'
    pl.say('player-status', 'exit')
    pl.pump(0.6)
    assert ahead(root, want=(1, 2, [three])) == (1, 2, [three]), 'two playing: the one after it'
    sock.close()


@pytest.mark.parametrize('config, n', [(CONFIG + 'AHEAD=0\n', 0), (CONFIG + 'AHEAD=1\n', 1), (CONFIG + 'AHEAD=3\n', 3),
                                       (CONFIG + 'AHEAD=4\n', 2)])
def test_download_ahead_s_setting_is_stock_s_q2pod_ahead(run, n):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    assert ahead(root, 0)[1] == n, 'Off, 1, 2 or 3; anything else the default 2'
    songs = [f'/mnt/mmc/.navi/stream/a/hi/x/S{i} [s{i}].flac' for i in range(1, 6)]
    land(root, *songs)
    answer(root, *songs)
    pl.pump(1.0)
    assert ahead(root, want=(1, n, songs[1:1 + n])) == (1, n, songs[1:1 + n])
    sock.close()


def test_a_change_of_download_ahead_is_written_while_the_list_plays(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    songs = [f'/mnt/mmc/.navi/stream/a/hi/x/S{i} [s{i}].flac' for i in range(1, 5)]
    land(root, *songs)
    answer(root, *songs)
    pl.pump(1.0)
    assert ahead(root, want=(1, 2, songs[1:3])) == (1, 2, songs[1:3])
    (root/'mnt/data/config.ini').write_text(CONFIG + 'AHEAD=1\n')
    pl.pump(1.2)
    assert ahead(root, want=(1, 1, songs[1:2])) == (1, 1, songs[1:2])
    sock.close()


def test_an_answer_with_nothing_there_yet_plays_its_first_as_it_lands(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    one = '/mnt/mmc/.navi/stream/a/hi/x/Cloud One [s1].flac'
    answer(root, one)
    pl.pump(1.0)
    assert pl.songs() == []
    land(root, one)
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', one)]
    sock.close()


def test_the_app_may_rewrite_the_lines_after_those_taken_and_off_card_lines_are_passed_over(run):
    root, pl = run
    sock = catalogue(root)
    play_list(root, [SERVER + 1, SERVER + 2], 0)
    assert wait_for(root/'tmp/q2-navi.catplay')
    land(root, '/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.mp3')
    answer(root, '#EXTM3U', '/tmp/elsewhere.flac', '/mnt/mmc/../etc/passwd', '/mnt/mmc/Music/a.flac', '/mnt/mmc/.navi/later.flac')
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/a.flac')], 'the off-card lines passed over'
    (root/'tmp/navi.m3u').write_text('/tmp/elsewhere.flac\n/mnt/mmc/../etc/passwd\n/mnt/mmc/Music/a.flac\n/mnt/mmc/Music/b.mp3\n')
    pl.pump(0.6)
    pl.say('player-status', 'exit')
    pl.pump(1.0)
    assert pl.songs()[-1] == ('set-songurl', '/mnt/mmc/Music/b.mp3'), 'the line after those taken, rewritten'
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


# ---- AirPlay's tap, the host's half (docs/playd.md#airplay) ----

import mmap  # noqa: E402
import struct  # noqa: E402
import threading  # noqa: E402

TAP_SIZE = 160 + 262144 * 4
OFF = {'want': 8, 'state': 16, 'spk': 124, 'beat': 136, 'status': 140, 'volume': 144, 'maxvol': 148, 'track': 152, 'spk_seq': 156}


class Tap:
    """the q2-airplay app's side of /tmp/q2cast: made whole and renamed in, beaten while self.beating"""
    def __init__(self, root, state=1):
        tmp = root/'tmp/q2cast.tmp'
        with open(tmp, 'wb') as f:
            f.write(struct.pack('<II', 0x31544351, TAP_SIZE) + bytes(TAP_SIZE - 8))
        os.rename(tmp, root/'tmp/q2cast')
        self.f = open(root/'tmp/q2cast', 'r+b')
        self.m = mmap.mmap(self.f.fileno(), TAP_SIZE)
        self.set('state', state)
        self.beating = True
        threading.Thread(target=self.beat, daemon=True).start()

    def set(self, k, v):
        self.m[OFF[k]:OFF[k] + 4] = struct.pack('<i' if k in ('state', 'spk', 'status', 'volume', 'maxvol') else '<I', v)

    def get(self, k):
        return struct.unpack('<i' if k in ('state', 'spk', 'status', 'volume', 'maxvol') else '<I', self.m[OFF[k]:OFF[k] + 4])[0]

    def beat(self):
        while True:
            if self.beating:
                self.set('want', (time.clock_gettime_ns(time.CLOCK_MONOTONIC) >> 20) & 0xffffffff)
            time.sleep(0.2)


def test_the_tap_is_told_what_plays_and_beaten(run):
    root, pl = run
    tap = Tap(root)
    play_list(root, [2], 0)
    pl.pump(1.6)
    beat = tap.get('beat')
    assert (tap.get('status'), tap.get('volume'), tap.get('maxvol')) == (2, -1, 80), 'playing; no speaker: -1, the Q2 heard'
    time.sleep(0.6)
    assert tap.get('beat') > beat > 0
    assert (root/'tmp/q2cast.track').read_text() == 'path=/mnt/mmc/Music/b.mp3\ntitle=Beta\nartist=Bob\nalbum=First\nlength=0\n'
    assert tap.get('track') == 1
    fifo(root, 'pause')
    time.sleep(0.6)
    assert tap.get('status') == 3 and tap.get('track') == 1, 'paused; the same song not written again'


def test_a_speaker_takes_the_volume_and_the_earphones_get_theirs_back(run):
    root, pl = run
    assert dac(root) == [(37, 37)]
    tap = Tap(root, state=2)
    time.sleep(1.5)
    assert now(root, 0)['volume'] == '35' and tap.get('volume') == 35, "no level from the app: AIRVOL's default"
    fifo(root, 'volume 90')
    time.sleep(0.6)
    assert tap.get('volume') == 90 and dac(root) == [(37, 37)], "Max Volume lifted, the DAC left at the earphones' level"
    time.sleep(2.5)
    cfg = (root/'mnt/data/config.ini').read_text()
    assert 'AIRVOL=90' in cfg and 'VOLUME=37\n' in cfg, "the speaker's saved as AIRVOL, the earphones' kept"
    tap.set('state', 0)
    time.sleep(0.8)
    assert now(root, 0)['volume'] == '37' and dac(root)[-1] == (37, 37) and tap.get('volume') == -1


def test_a_speaker_over_bluetooth_keeps_the_headset_s_level_and_leaves_the_dac(run):
    """AirPlay's speaker while the output is Bluetooth: the headset's level is BT_VOLUME's, kept and given back, and
    neither VOLUME (the DAC's) nor the DAC itself is touched"""
    root, pl = run
    fifo(root, 'output bluetooth')
    pl.pump()
    fifo(root, 'volume 20')
    pl.pump()
    before = dac(root)
    tap = Tap(root, state=2)
    time.sleep(1.5)
    assert tap.get('volume') == 35
    tap.set('state', 0)
    time.sleep(0.8)
    pl.pump()
    cfg = (root/'mnt/data/config.ini').read_text()
    assert now(root, 0)['volume'] == '20' and dac(root) == before, (now(root, 0)['volume'], dac(root), before)
    assert 'BT_VOLUME=20' in cfg and 'VOLUME=37' in cfg.replace('BT_VOLUME', ''), cfg
    assert pl.got[-1] == ('mcl-setvol', str(100 << 8 | 100)), "the headset's level, the player at unity"


def test_the_speaker_s_own_level_is_taken_and_a_stale_beat_lets_go(run):
    root, pl = run
    tap = Tap(root)
    tap.set('spk', 60), tap.set('spk_seq', 1), tap.set('state', 2)
    time.sleep(1.5)
    assert now(root, 0)['volume'] == '60'
    tap.set('spk', 20), tap.set('spk_seq', 2)
    time.sleep(0.8)
    assert now(root, 0)['volume'] == '20', 'a reconnect: the speaker says again'
    tap.beating = False
    time.sleep(2.2)
    assert now(root, 0)['volume'] == '37' and tap.get('volume') == -1, 'the app gone: the earphones back within 1.5 s'


def test_the_tap_s_layout_is_peq_h_s():
    def fields(text):
        body = text[:text.index('} cast_tap;')]
        body = body[body.rindex('typedef struct {'):]
        body = re.sub(r'/\*.*?\*/', '', body, flags=re.S)
        return re.findall(r'(\w+)(?:\[[^\]]*\])*\s*(?=[,;])', body)
    peq, ours = (ROOT/'patch/peq.h').read_text(), SOURCE.read_text()
    assert fields(ours) == fields(peq) and len(fields(peq)) > 20
    for name in ('CAST_MAGIC', 'CAST_RING', 'CAST_STALE'):
        assert re.search(rf'#define {name} (\w+)', ours).group(1) == re.search(rf'#define {name} (\w+)', peq).group(1), name


# ---- Car Remote's fields (docs/playd.md#car-remote): what coda-rc reads of the now and queue files ----

def queue_file(root, wait=1.0):
    time.sleep(wait)  # written with the now file, at most every NOW_EVERY_MS
    lines = (root/'tmp/coda-rc.queue').read_text().splitlines()
    return tuple(map(int, lines[0].split())), lines[1:]


def send(root, *lines):
    """FIFO lines with seqs of their own ("<seq> <verb>"), as coda-rc numbers its commands"""
    fd = os.open(root/'tmp/coda-rc.cmdq', os.O_WRONLY | os.O_NONBLOCK)
    for line in lines:
        os.write(fd, f'{line}\n'.encode())
    os.close(fd)


def test_the_now_file_s_seq_moves_with_each_change_and_carries_car_remote_s_fields(run):
    """coda-rc reads Now Playing again only when seq= moved (coda_rc.c np_read), so q2playd writes it first, as demo's
    shim does (coda_rc_shim.c cr_now), with the queue's place and length, its generation and list, the modes, the
    path and hearts (the stars, 0 for none)"""
    root, pl = run
    play_list(root, [1, 2, 3], 1)
    pl.pump()
    text = (root/'tmp/coda-rc.now').read_text()
    a = now(root)
    assert text.startswith('seq='), text
    assert (a['qpos'], a['qlen'], a['shuffle'], a['repeat'], a['hearts']) == ('1', '3', '0', '0', '0'), a
    assert a['path'] == '/mnt/mmc/Music/b.mp3' and a['qgen'] == str(queue_file(root)[0][0]), a
    assert now(root)['seq'] == a['seq'], 'nothing changed: the same seq'
    fifo(root, 'next')
    pl.pump()
    b = now(root)
    assert int(b['seq']) > int(a['seq']) and b['qpos'] == '2' and b['id'] == '3', b
    assert b['qlist'] == a['qlist'], 'a next is the same queue'
    play_list(root, [5, 1], 0)
    pl.pump()
    assert now(root)['qlist'] != a['qlist'], 'a new play is another queue'


def test_the_queue_file_lists_the_play_order_from_the_song_playing(run):
    """coda_rc_shim.c cr_queue: "gen place lines", then a song's library id a line, in the order it plays (a shuffle's
    too), rewritten with gen moved only when it changes; coda-rc's GetQueue reads it (coda_rc_lib.c)"""
    root, pl = run
    play_list(root, [1, 2, 3, 5], 2, shuffle=1)
    pl.pump()
    (gen, place, lines), ids = queue_file(root)
    assert (place, lines, ids[0], sorted(ids)) == (0, 4, '3', ['1', '2', '3', '5']), (gen, place, ids)
    fifo(root, 'next')
    pl.pump()
    (gen2, place2, _), ids2 = queue_file(root)
    assert (gen2, place2, ids2) == (gen + 1, 1, ids), (gen2, place2, ids2)
    pl.info = '900000|44100|200|12|FLAC|16'
    pl.pump(1.5)  # the position moves; the queue does not
    assert queue_file(root)[0] == (gen2, 1, 4)


def test_a_jump_plays_that_line_of_the_queue_and_a_stale_one_is_refused(run):
    """coda_rc.c queue_item sends "jump INDEX ID" for a row of GetQueue's list; the song plays if that line still holds
    it, else the answer is 3, as the shim's cr_jump gives (the phone's queue is stale)"""
    root, pl = run
    play_list(root, [1, 2, 3], 0)
    pl.pump()
    queue_file(root)  # the lines the phone was given
    send(root, '40 jump 2 3')
    pl.pump()
    assert wait_for_value(lambda: acks(root)[:1], [(40, 0)]), acks(root)
    assert pl.songs()[-1][1] == '/mnt/mmc/Music/c%0x7dd.flac' and now(root)['qpos'] == '2'
    send(root, '41 jump 1 5', '42 jump 7 1')
    assert wait_for_value(lambda: acks(root)[:2], [(42, 3), (41, 3)]), acks(root)
    send(root, '43 pause')
    pl.pump()
    send(root, '44 jump 2 3')  # the one playing, paused: it plays on where it was
    pl.pump()
    assert wait_for_value(lambda: acks(root)[:1], [(44, 0)]) and pl.names()[-1] == 'mcl-play', pl.got[-3:]


# ---- Play Next and Play Last (q2ui's hold menu: ui/player.c player_enqueue, the shim's "queue next|last N") ----

def enqueue(root, ids, where, seq):
    (root/'tmp/coda-rc.list').write_text(''.join(f'{i}\n' for i in ids))
    send(root, f'{seq} queue {where} {len(ids)}')


def test_play_next_goes_after_the_song_playing_and_play_last_at_the_end(run):
    """library_pages.c queue_apply: the list's ids inserted after the song playing (next) or after the last (last),
    the song playing left alone"""
    root, pl = run
    play_list(root, [1, 2, 3], 0)
    pl.pump()
    enqueue(root, [5], 'next', 50)
    assert wait_for_value(lambda: acks(root)[:1], [(50, 0)]), acks(root)
    enqueue(root, [6], 'last', 51)
    assert wait_for_value(lambda: acks(root)[:1], [(51, 0)]), acks(root)
    assert [s for _, s in pl.songs()] == ['/mnt/mmc/Music/a.flac'], 'the song playing goes on'
    for _ in range(4):
        pl.say('player-status', 'exit')
        pl.pump()
    assert [s for _, s in pl.songs()] == ['/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/e.dsf', '/mnt/mmc/Music/b.mp3',
                                          '/mnt/mmc/Music/c%0x7dd.flac', '/mnt/mmc/Music/f.flac'], pl.songs()
    assert queue_file(root)[1] == ['1', '5', '2', '3', '6']


def test_play_last_with_nothing_queued_loads_the_songs_without_playing(run):
    """queue_apply with no queue: mclLoadPlayList(add, 0, ...), loaded and not started"""
    root, pl = run
    enqueue(root, [2, 3], 'last', 60)
    assert wait_for_value(lambda: acks(root)[:1], [(60, 0)]), acks(root)
    pl.pump()
    n = now(root)
    assert pl.songs() == [] and n['state'] == '0' and n['qlen'] == '2', (pl.got, n)
    send(root, '61 resume')
    pl.pump()
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')]


def test_play_next_into_a_shuffle_comes_next_in_its_order(run):
    root, pl = run
    play_list(root, [1, 2, 3], 1, shuffle=1)
    pl.pump()
    enqueue(root, [6], 'next', 70)
    assert wait_for_value(lambda: acks(root)[:1], [(70, 0)]), acks(root)
    pl.say('player-status', 'exit')
    pl.pump()
    assert pl.songs()[-1][1] == '/mnt/mmc/Music/f.flac', pl.songs()


# ---- the play mode (docs/playd.md#the-play-mode): stock's [PLAYSET] PLAYMODE, Now Playing's [IPOD] NPSHUFONE, NPREPOFF ----

def ini(root):
    out, sec = {}, ''
    for line in (root/'mnt/data/config.ini').read_text().splitlines():
        if line.startswith('['):
            sec = line.strip('[]')
        elif '=' in line:
            out[f'{sec}.{line.split("=", 1)[0]}'] = line.split('=', 1)[1]
    return out


@pytest.mark.parametrize('config', [CONFIG.replace('[PLAYSET]\n', '[PLAYSET]\nPLAYMODE=1\n')])
def test_repeat_one_plays_the_song_again_at_its_end_and_next_still_moves_on(run):
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    pl.say('player-status', 'exit')
    pl.pump()
    assert [s for _, s in pl.songs()] == ['/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/a.flac'], pl.songs()
    assert now(root)['repeat'] == '1'
    fifo(root, 'next')
    pl.pump()
    assert pl.songs()[-1][1] == '/mnt/mmc/Music/b.mp3'


@pytest.mark.parametrize('config', [CONFIG.replace('[PLAYSET]\n', '[PLAYSET]\nPLAYMODE=3\n')])
def test_repeat_all_plays_the_queue_again_from_its_first_song(run):
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    for _ in range(2):
        pl.say('player-status', 'exit')
        pl.pump()
    assert [s for _, s in pl.songs()] == ['/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.mp3', '/mnt/mmc/Music/a.flac'], pl.songs()
    assert now(root)['repeat'] == '2' and now(root, 0)['state'] == '1'


def test_set_mode_saves_stock_s_keys_as_now_playing_s_two_controls_do(run):
    """Car Remote's "mode SHUFFLE REPEAT" (-1 leaves one; repeat 0 Off, 1 One, 2 All) as np_bar.c np_mode_set: Repeat
    One plays as stock's 1 whatever shuffle says (NPSHUFONE keeps it), shuffle with Off or All as 2 (NPREPOFF says
    which), All alone as 3, neither as 0"""
    root, pl = run
    play_list(root, [1, 2, 3], 0)
    pl.pump()
    steps = [('mode -1 2', '3', '0', '2'), ('mode 1 -1', '2', '1', '2'), ('mode -1 0', '2', '1', '0'),
             ('mode -1 1', '1', '1', '1'), ('mode 0 0', '0', '0', '0')]
    for k, (verb, playmode, shuffle, repeat) in enumerate(steps):
        send(root, f'{80 + k} {verb}')
        assert wait_for_value(lambda: acks(root)[:1], [(80 + k, 0)]), (verb, acks(root))
        n = now(root)
        assert (ini(root).get('PLAYSET.PLAYMODE'), n['shuffle'], n['repeat']) == (playmode, shuffle, repeat), (verb, ini(root), n)
    c = ini(root)
    assert (c['IPOD.NPSHUFONE'], c['IPOD.NPREPOFF']) == ('0', '1'), c
    send(root, '90 mode 2 0', '91 mode -1 3')
    assert wait_for_value(lambda: acks(root)[:2], [(91, 1), (90, 1)]), acks(root)


def test_shuffle_turned_on_keeps_the_song_playing_first_and_off_gives_the_order_back(run):
    root, pl = run
    play_list(root, [1, 2, 3, 5, 6], 1)
    pl.pump()
    send(root, '95 mode 1 -1')
    assert wait_for_value(lambda: acks(root)[:1], [(95, 0)])
    (_, place, _), ids = queue_file(root)
    assert (place, ids[0], sorted(ids)) == (0, '2', ['1', '2', '3', '5', '6']), (place, ids)  # the song playing first, the rest drawn
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')], 'it plays on'
    send(root, '96 mode 0 -1')
    assert wait_for_value(lambda: acks(root)[:1], [(96, 0)])
    (_, place, _), ids = queue_file(root)
    assert (ids, place) == (['1', '2', '3', '5', '6'], 1), (ids, place)


# ---- ratings and likes (docs/playd.md#ratings-and-likes) ----

RATE_LAYOUT = 0x48415356


def listen_key(path):
    """patch/nav/chapters_streams_folders.c listen_key: the path's FNV-1a and coverflow.c fnv's separator, never 0"""
    h = 2166136261
    for b in path.encode():
        h = ((h ^ b) * 16777619) & 0xffffffff
    h = (h * 16777619) & 0xffffffff
    return h or 1


def rate_words(root):
    data = (root/'mnt/data/ringnav-ratings').read_bytes()
    return list(struct.unpack(f'<{len(data) // 4}I', data))


def rate_find(words, key):
    """patch/ratings.c rate_slot's probe over a laid-out table: the key's stars, 0 for none"""
    m = len(words) - 2
    k = (((key >> 3) * 2654435761) & 0xffffffff) % m
    for _ in range(m):
        if not words[k]:
            return 0
        if words[k] != 7 and (words[k] ^ key) < 8:
            return words[k] & 7
        k = (k + 1) % m
    return 0


def laid_out(cap, ratings):
    """a table as today's Vamp writes it (ratings.c rate_io): cap words, each (key, stars) at its probe, the trailer"""
    t, m = [0] * cap, cap - 2
    for key, n in ratings:
        k = (((key >> 3) * 2654435761) & 0xffffffff) % m
        while t[k]:
            k = (k + 1) % m
        t[k] = (key & ~7 & 0xffffffff) | n
    t[m], t[m + 1] = RATE_LAYOUT, len(ratings) << 3 | 6
    return struct.pack(f'<{cap}I', *t)


def test_rate_keeps_the_playing_song_s_stars_in_today_s_table_and_the_now_file_shows_them(run):
    """ratings.c rate_keep / rate_io: listen_key with its low 3 bits the stars, a 4096-word table laid out by its hash
    with the layout mark and the slots in use last; the shim's hearts= the stars (cr_hearts), 0 for none"""
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    assert now(root)['hearts'] == '0', 'a song with no stars'
    send(root, '80 rate 4 1')
    assert wait_for_value(lambda: acks(root)[:1], [(80, 0)]), acks(root)
    w = rate_words(root)
    assert len(w) == 4096 and w[4094] == RATE_LAYOUT and w[4095] == (1 << 3 | 6), (len(w), w[-2:])
    assert rate_find(w, listen_key('/mnt/mmc/Music/a.flac')) == 4
    assert now(root)['hearts'] == '4'
    send(root, '81 rate 3 2')
    assert wait_for_value(lambda: acks(root)[:1], [(81, 3)]), 'not the song playing: the shim\'s 3'
    send(root, '82 rate 6 1')
    assert wait_for_value(lambda: acks(root)[:1], [(82, 1)]), 'past five: refused'
    send(root, '83 rate 0 1')
    assert wait_for_value(lambda: acks(root)[:1], [(83, 0)])
    w = rate_words(root)
    assert rate_find(w, listen_key('/mnt/mmc/Music/a.flac')) == 0 and 7 in w, 'taken out: its slot RATE_GONE'
    assert now(root)['hearts'] == '0'
    fifo(root, 'next')
    pl.pump()
    send(root, '84 rate 5 2')
    assert wait_for_value(lambda: acks(root)[:1], [(84, 0)])
    w = rate_words(root)
    assert w[4095] == (2 << 3 | 6), 'the gone slot still counts as in use, as rate_keep counts it'
    assert rate_find(w, listen_key('/mnt/mmc/Music/b.mp3')) == 5


def test_a_table_today_s_vamp_wrote_is_read_as_it_is_and_an_older_one_kept_beside(run):
    """ratings.c rates(): a laid-out file as it is; one from before the hashing (ratings from the first slot, 16 KB)
    laid out at its first use and kept once as ringnav-ratings.v1"""
    root, pl = run
    old = struct.pack('<4096I', (listen_key('/mnt/mmc/Music/b.mp3') & ~7 & 0xffffffff) | 3, *[0] * 4095)
    (root/'mnt/data/ringnav-ratings').write_bytes(old)
    play_list(root, [1, 2], 1)
    pl.pump()
    assert now(root)['hearts'] == '3', 'the older file\'s stars'
    send(root, '85 rate 2 2')
    assert wait_for_value(lambda: acks(root)[:1], [(85, 0)])
    assert (root/'mnt/data/ringnav-ratings.v1').read_bytes() == old
    w = rate_words(root)
    assert w[4094] == RATE_LAYOUT and rate_find(w, listen_key('/mnt/mmc/Music/b.mp3')) == 2


def test_a_laid_out_table_is_read_as_is_and_grows_past_three_quarters(run):
    """ratings.c rate_slot/rate_grow: a new rating past three quarters of the slots in use doubles the table"""
    root, pl = run
    a = listen_key('/mnt/mmc/Music/a.flac')
    filler = [(((i + 1) << 3) & 0xffffffff, 1) for i in range(3068)] + [(a, 2)]
    (root/'mnt/data/ringnav-ratings').write_bytes(laid_out(4096, filler))
    play_list(root, [1, 2], 0)
    pl.pump()
    assert now(root)['hearts'] == '2', 'read as it is'
    fifo(root, 'next')
    pl.pump()
    send(root, '86 rate 4 2')
    assert wait_for_value(lambda: acks(root)[:1], [(86, 0)])
    w = rate_words(root)
    assert len(w) == 8192 and w[8191] >> 3 == 3070, (len(w), w[-1] >> 3)
    assert rate_find(w, a) == 2 and rate_find(w, listen_key('/mnt/mmc/Music/b.mp3')) == 4


def test_a_full_table_refuses_a_new_song_and_keeps_a_kept_one_s_change(run):
    """at RATE_SLOTS (16384 words, 12285 ratings) a new song's rating is refused and nothing written"""
    root, pl = run
    a = listen_key('/mnt/mmc/Music/a.flac')
    full = laid_out(16384, [(((i + 1) << 3) & 0xffffffff, 1) for i in range(12284)] + [(a, 2)])
    (root/'mnt/data/ringnav-ratings').write_bytes(full)
    play_list(root, [2, 1], 0)
    pl.pump()
    send(root, '87 rate 4 2')
    assert wait_for_value(lambda: acks(root)[:1], [(87, 1)]), 'Ratings are full'
    assert (root/'mnt/data/ringnav-ratings').read_bytes() == full
    fifo(root, 'next')
    pl.pump()
    send(root, '88 rate 5 1')
    assert wait_for_value(lambda: acks(root)[:1], [(88, 0)]), 'a kept song\'s can still change'
    assert rate_find(rate_words(root), a) == 5


def test_a_server_song_by_its_offline_copy_is_rated_under_its_server_row(run):
    """catalogue.c catalogue_song_key: a server song playing from its copy keeps its stars under its row's fileurl"""
    root, pl = run
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'tmp/q2-navi.state').write_text('status=idle\nserver=down\n')
    play_list(root, [SERVER + 1, 2], 0)
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', COPY1)]
    send(root, f'89 rate 3 {SERVER + 1}')
    assert wait_for_value(lambda: acks(root)[:1], [(89, 0)])
    w = rate_words(root)
    assert rate_find(w, listen_key('/mnt/mmc/.navi/cat/al1/s1.flac')) == 3 and rate_find(w, listen_key(COPY1)) == 0
    sock.close()


def likes(root):
    db = sqlite3.connect(root/'mnt/data/vamp/likes.db')
    try:
        return sorted(r[0] for r in db.execute('select fileurl from myfav'))
    finally:
        db.close()


def test_like_flips_the_heart_apart_from_the_stars(run):
    """the pin's shim (9af5a6f5 coda_rc_shim.c "like ID", ratings.c rate_now_like): the like flipped, the stars left
    alone; liked= in the now file after hearts=; 3 when the id is not the song playing"""
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    send(root, '90 rate 2 1')
    assert wait_for_value(lambda: acks(root)[:1], [(90, 0)])
    assert now(root)['liked'] == '0'
    send(root, '91 like 1')
    assert wait_for_value(lambda: acks(root)[:1], [(91, 0)])
    assert likes(root) == ['/mnt/mmc/Music/a.flac']
    n = now(root)
    assert (n['liked'], n['hearts']) == ('1', '2'), n
    send(root, '92 like 2')
    assert wait_for_value(lambda: acks(root)[:1], [(92, 3)])
    send(root, '93 like 1')
    assert wait_for_value(lambda: acks(root)[:1], [(93, 0)])
    assert likes(root) == [] and now(root)['liked'] == '0'
    text = (root/'tmp/coda-rc.now').read_text()
    assert text.index('\nhearts=') < text.index('\nliked='), text


def test_stock_s_favorites_are_the_likes_and_an_unlike_takes_one_out_of_them(run):
    """a switch either way loses nothing: stock's and today's Vamp's My Fav (database.db's myfav) are the likes at the
    start, and an unlike under Vamp OS takes the song out of it too"""
    root, pl = run
    stock_myfav(root, ['/mnt/mmc/Music/b.mp3'])
    play_list(root, [2, 1], 0)
    pl.pump()
    assert now(root)['liked'] == '0', 'myfav made after the start: taken at the next one'
    send(root, '94 like 2')
    assert wait_for_value(lambda: acks(root)[:1], [(94, 0)])
    assert now(root)['liked'] == '1' and [f[0] for f in stock_favs(root)] == ['/mnt/mmc/Music/b.mp3']
    send(root, '95 like 2')
    assert wait_for_value(lambda: acks(root)[:1], [(95, 0)])
    assert likes(root) == [] and stock_favs(root) == [], 'out of both'


def test_a_source_s_url_has_no_stars_and_refuses_rate_and_like(run):
    """the queue put aside: its song is not what plays"""
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    a = api(root)
    a.say('source http://192.0.2.1/a.flac title=Far')
    assert a.line() == 'ok'
    pl.pump()
    n = now(root)
    assert (n['hearts'], n['liked']) == ('-1', '-1'), n
    send(root, '95 rate 3 0', '96 like 0')
    assert wait_for_value(lambda: acks(root)[:2], [(96, 3), (95, 3)]), acks(root)


# ---- play counts (docs/playd.md#play-counts) ----

def card_in(root, mounted=True):
    """root/proc/mounts, which q2playd reads for the card (as q2libd does)"""
    (root/'proc').mkdir(exist_ok=True)
    (root/'proc/mounts').write_text('/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n' if mounted else 'ubi0:data /mnt/data ubifs rw 0 0\n')
    (root/'mnt/mmc').mkdir(parents=True, exist_ok=True)


def heard(root, pl, length, secs):
    """the player at each second of secs in turn, each told at once by a push (docs/internals.md#player-status-push)"""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    for sec in secs:
        pl.info = f'900000|44100|{length}|{sec}|FLAC|16'
        s.sendto(b'1', str(root/'tmp/q2player.push'))
        pl.pump(0.06)
    s.close()


def plays(root, rel='mnt/mmc/.q2pod/ringnav-plays'):
    data = (root/rel).read_bytes()
    pairs = struct.unpack(f'<{len(data) // 4}I', data)
    return [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]


def test_a_listen_counts_once_half_the_song_is_heard_and_a_repeat_counts_again(run):
    """docs/internals.md#play-counts: half the length (a song over 30 s) heard counts once a play, in the card's
    .q2pod/ringnav-plays, 512 key/count pairs, 4 KB, the song counted moved first; back under 2 s from past 30 s is a
    new play"""
    root, pl = run
    card_in(root)
    play_list(root, [1, 2], 0)
    pl.pump()
    heard(root, pl, 62, range(0, 31))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists(), '30 s of 62: not yet'
    heard(root, pl, 62, range(31, 40))
    p = plays(root)
    assert len(p) == 512 and p[0] == (listen_key('/mnt/mmc/Music/a.flac'), 1) and p[1] == (0, 0), p[:2]
    heard(root, pl, 62, range(40, 62))
    assert plays(root)[0][1] == 1, 'once a play'
    heard(root, pl, 62, range(0, 32))
    assert plays(root)[0] == (listen_key('/mnt/mmc/Music/a.flac'), 2), 'played again from its start'


def test_seeks_pauses_and_short_songs_count_nothing(run):
    root, pl = run
    card_in(root)
    play_list(root, [2, 1], 0)
    pl.pump()
    heard(root, pl, 62, [0, 20] + list(range(21, 33)))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists(), 'a seek of 20 s is no listen: 12 s heard'
    fifo(root, 'pause')
    pl.pump()
    heard(root, pl, 62, range(33, 60))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists(), 'paused'
    fifo(root, 'next')
    pl.pump()
    heard(root, pl, 30, range(0, 30))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists(), 'a song of 30 s never counts'


def test_a_long_song_counts_after_four_minutes(run):
    root, pl = run
    card_in(root)
    play_list(root, [1], 0)
    pl.pump()
    heard(root, pl, 900, range(0, 238, 3))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists()
    heard(root, pl, 900, [240, 243])
    assert plays(root)[0] == (listen_key('/mnt/mmc/Music/a.flac'), 1)


def test_a_full_table_replaces_the_least_recently_played_of_the_lowest_counts(run):
    """playback_power.c play_count: the slot replaced is the furthest back of the lowest counts"""
    root, pl = run
    card_in(root)
    table = [(1000 + i, 5) for i in range(512)]
    table[300], table[400] = (7, 2), (8, 2)
    (root/'mnt/mmc/.q2pod').mkdir(parents=True)
    (root/'mnt/mmc/.q2pod/ringnav-plays').write_bytes(struct.pack('<1024I', *[w for pair in table for w in pair]))
    play_list(root, [1], 0)
    pl.pump()
    heard(root, pl, 62, range(0, 33))
    p = plays(root)
    assert p[0] == (listen_key('/mnt/mmc/Music/a.flac'), 1) and (8, 2) not in p and (7, 2) in p, p[:2]
    assert p[1:301] == table[:300] and p[302:401] == table[301:400], 'the rest moved down one, in order'


def test_the_flash_file_of_a_release_before_is_read_until_the_card_has_one(run):
    """card_store.c card_blob_read: no card copy, /mnt/data/ringnav-plays read and copied to the card, left as it was"""
    root, pl = run
    card_in(root)
    old = struct.pack('<1024I', listen_key('/mnt/mmc/Music/a.flac'), 4, *[0] * 1022)
    (root/'mnt/data/ringnav-plays').write_bytes(old)
    play_list(root, [1], 0)
    pl.pump()
    heard(root, pl, 62, range(0, 33))
    assert plays(root)[0] == (listen_key('/mnt/mmc/Music/a.flac'), 5)
    assert (root/'mnt/data/ringnav-plays').read_bytes() == old


def test_with_the_card_away_a_count_waits_for_it(run):
    root, pl = run
    card_in(root, mounted=False)
    play_list(root, [1], 0)
    pl.pump()
    heard(root, pl, 62, range(0, 33))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists(), 'nothing written where no card is'
    card_in(root)
    assert wait_for(root/'mnt/mmc/.q2pod/ringnav-plays', 5)
    assert plays(root)[0] == (listen_key('/mnt/mmc/Music/a.flac'), 1)


def test_podcasts_audiobooks_and_m4b_files_are_not_counted(run):
    """navigation.c spoken: under the card's Podcasts or Audiobooks, or an M4B"""
    root, pl = run
    card_in(root)
    db = sqlite3.connect(root/'mnt/data/database.db')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?, 0)', [
        (20, '/mnt/mmc/podcasts/ep.mp3', 'Ep', 'Host', 'Show'), (21, '/mnt/mmc/Music/book.M4B', 'Book', 'Au', 'B')])
    db.commit()
    db.close()
    play_list(root, [20, 21, 1], 0)
    pl.pump()
    heard(root, pl, 62, range(0, 33))
    fifo(root, 'next')
    pl.pump()
    heard(root, pl, 62, range(0, 33))
    assert not (root/'mnt/mmc/.q2pod/ringnav-plays').exists()
    fifo(root, 'next')
    pl.pump()
    heard(root, pl, 62, range(0, 33))
    assert plays(root)[0] == (listen_key('/mnt/mmc/Music/a.flac'), 1)


# ---- Memory Playback (docs/playd.md#memory-playback) ----

MEMORY = CONFIG.replace('GAPLESS=1\n', 'GAPLESS=1\nMEMORYPLAY=2\n')
TRACK = CONFIG.replace('GAPLESS=1\n', 'GAPLESS=1\nMEMORYPLAY=1\n')
CAR = CONFIG.replace('LANGUAGE=2\n', 'LANGUAGE=2\nCARMODE=1\n')


def fnv_bytes(b, h=2166136261):
    for x in b:
        h = ((h ^ x) * 16777619) & 0xffffffff
    return h


def snapshot(paths, pos, elapsed=0, version=4, kind=0xf001):
    """playback.c playback_save's plain snapshot (version 4): the header, the queue's order twice and n zeros, each
    song's identity (path length with its NUL, CUE start and end) and path; the checksum FNV-1a over it all with
    its own word 0"""
    n = len(paths)
    idx = list(range(n)) * 2 + [0] * n
    recs = b''.join(struct.pack('<3I', len(q.encode()) + 1, 0, 0) + q.encode() + b'\0' for q in paths)
    size = 56 + len(idx) * 4 + len(recs)
    buf = struct.pack('<14I', 0x5132524e, version, size, 0, n, pos, elapsed, 0, 2, 0, pos, 0, 0, kind)
    buf += struct.pack(f'<{len(idx)}I', *idx) + recs
    total = fnv_bytes(buf)
    return buf[:12] + struct.pack('<I', total) + buf[16:], total


def checkpoint(total, pos, elapsed):
    c = struct.pack('<3I', total, pos, elapsed)
    return struct.pack('<I', fnv_bytes(c)) + c


def read_snapshot(root):
    buf = (root/'mnt/mmc/.q2pod/ringnav-queue').read_bytes()
    h = struct.unpack('<14I', buf[:56])
    assert h[0] == 0x5132524e and h[2] == len(buf), h
    assert fnv_bytes(buf[:12] + b'\0' * 4 + buf[16:]) == h[3], 'its checksum'
    n = h[4]
    idx = list(struct.unpack(f'<{3 * n}I', buf[56:56 + 12 * n]))
    p, paths = 56 + 12 * n, []
    for _ in range(n):
        length, start, end = struct.unpack('<3I', buf[p:p + 12])
        paths.append(buf[p + 12:p + 12 + length - 1].decode())
        assert (start, end, buf[p + 11 + length]) == (0, 0, 0)
        p += 12 + length
    assert p == len(buf)
    return {'version': h[1], 'n': n, 'pos': h[5], 'elapsed': h[6], 'shuffle': h[7], 'repeat': h[8], 'folder': h[9],
            'cursor': h[10], 'hn': h[11], 'npend': h[12], 'type': h[13], 'idx': idx, 'paths': paths, 'sum': h[3]}


def read_checkpoint(root):
    c = (root/'mnt/mmc/.q2pod/ringnav-queue-elapsed').read_bytes()
    w = struct.unpack('<4I', c)
    assert fnv_bytes(c[4:]) == w[0], 'its checksum'
    return w[1:]


@pytest.mark.parametrize('config', [MEMORY])
def test_memory_playback_keeps_the_queue_in_today_s_plain_snapshot_and_its_second_beside(run):
    """playback.c playback_save, version 4 (a queue without advanced play): written as the queue or its place changes,
    the second in ringnav-queue-elapsed (checkpoint: the snapshot's checksum, the place, the second) at a pause"""
    root, pl = run
    card_in(root)
    play_list(root, [1, 2, 3], 1)
    pl.pump()
    assert wait_for(root/'mnt/mmc/.q2pod/ringnav-queue', 3)
    h = read_snapshot(root)
    assert (h['version'], h['n'], h['pos'], h['cursor'], h['shuffle'], h['repeat'], h['folder'], h['hn'], h['npend'],
            h['type']) == (4, 3, 1, 1, 0, 2, 0, 0, 0, 0xf001), h
    assert h['idx'] == [0, 1, 2, 0, 1, 2, 0, 0, 0]
    assert h['paths'] == ['/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.mp3', '/mnt/mmc/Music/c}d.flac']
    pl.info = '900000|44100|200|42|FLAC|16'
    pl.pump(1.5)
    fifo(root, 'pause')
    pl.pump()
    assert wait_for_value(lambda: (root/'mnt/mmc/.q2pod/ringnav-queue-elapsed').exists() and read_checkpoint(root),
                          (h['sum'], 1, 42)), 'paused at 42 s'
    fifo(root, 'next')
    pl.pump()
    assert wait_for_value(lambda: read_snapshot(root)['pos'], 2), 'the place moved'


@pytest.mark.parametrize('config', [CONFIG])
def test_with_memory_playback_off_no_queue_is_kept(run):
    root, pl = run
    card_in(root)
    play_list(root, [1, 2], 0)
    pl.pump(1.5)
    assert not (root/'mnt/mmc/.q2pod/ringnav-queue').exists()


def kept(paths, pos, elapsed, cp=None, version=4):
    def prep(root):
        card_in(root)
        (root/'mnt/mmc/.q2pod').mkdir(parents=True, exist_ok=True)
        buf, total = snapshot(paths, pos, 0, version)
        (root/'mnt/mmc/.q2pod/ringnav-queue').write_bytes(buf)
        (root/'mnt/mmc/.q2pod/ringnav-queue-elapsed').write_bytes(checkpoint(*cp) if cp else checkpoint(total, pos, elapsed))
    return prep


THREE = ['/mnt/mmc/Music/a.flac', '/mnt/mmc/Music/b.mp3', '/mnt/mmc/Music/c}d.flac']


@pytest.mark.parametrize('config, prepare', [(MEMORY, kept(THREE, 2, 77))])
def test_memory_playback_s_location_brings_the_queue_back_paused_at_its_second(run):
    """stock's memeory_startplayer and ringnav_boot: the queue and song back paused, Location at its second"""
    root, pl = run
    n = now(root, 0.3)
    assert (n['id'], n['state'], n['pos'], n['qlen']) == ('3', '2', '77000', '3'), n
    assert pl.songs() == [], 'nothing loaded'
    fifo(root, 'resume')
    pl.pump()
    assert pl.got[-1] == ('set-songurl', '/mnt/mmc/Music/c%0x7dd.flac|ss=77|1024|20|0|17|90'), pl.got


@pytest.mark.parametrize('config, prepare', [(TRACK, kept(THREE, 1, 77))])
def test_memory_playback_s_track_brings_the_song_back_from_its_start(run):
    root, pl = run
    n = now(root, 0.3)
    assert (n['id'], n['state'], n['pos']) == ('2', '2', '0'), n


@pytest.mark.parametrize('config, prepare', [(CAR, kept(THREE, 1, 33))])
def test_in_vehicle_mode_brings_the_queue_back_playing_at_its_second(run):
    """car mode: memeory_startplayer's seek and a start that is not paused (ringnav_boot's stock call)"""
    root, pl = run
    pl.pump(1.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/Music/b.mp3')] and pl.got[-1][1].split('|')[1] == 'ss=33', pl.got
    assert now(root, 0.3)['state'] == '1'


@pytest.mark.parametrize('config, prepare', [(MEMORY, kept(['/mnt/mmc/Music/a.flac', '/mnt/mmc/gone.flac', '/mnt/mmc/Music/b.mp3'], 2, 5))])
def test_a_kept_song_no_longer_in_the_library_is_left_out(run):
    root, pl = run
    n = now(root, 0.3)
    assert (n['id'], n['qpos'], n['qlen'], n['pos']) == ('2', '1', '2', '5000'), n


@pytest.mark.parametrize('config, prepare', [(MEMORY, kept(THREE, 1, 50, cp=(0, 1, 50)))])
def test_a_checkpoint_of_another_snapshot_is_not_its_second(run):
    """ringnav_memory: the checkpoint counts only for the snapshot whose checksum it names, at its place"""
    root, pl = run
    n = now(root, 0.3)
    assert (n['id'], n['pos']) == ('2', '0'), n


def damaged(root):
    kept(THREE, 1, 9)(root)
    p = root/'mnt/mmc/.q2pod/ringnav-queue'
    b = bytearray(p.read_bytes())
    b[-3] ^= 1
    p.write_bytes(bytes(b))


@pytest.mark.parametrize('config, prepare', [(MEMORY, damaged)])
def test_a_damaged_snapshot_brings_nothing_back(run):
    root, pl = run
    n = now(root, 0.3)
    assert (n['qlen'], n['state']) == ('0', '0'), n


@pytest.mark.parametrize('config, prepare', [(MEMORY, kept(THREE, 1, 12, version=3))])
def test_a_snapshot_with_advanced_play_comes_back_in_its_queue_order(run):
    """version 3, today's with advanced play: the queue's rows and place; its own order is not q2playd's"""
    root, pl = run
    n = now(root, 0.3)
    assert (n['id'], n['qlen'], n['pos']) == ('2', '3', '12000'), n


def flash_only(root):
    card_in(root)
    buf, total = snapshot(THREE, 0, 0)
    (root/'mnt/data/ringnav-queue').write_bytes(buf)
    (root/'mnt/data/ringnav-queue-elapsed').write_bytes(checkpoint(total, 0, 21))


@pytest.mark.parametrize('config, prepare', [(MEMORY, flash_only)])
def test_the_flash_snapshot_of_a_release_before_is_read_when_the_card_has_none(run):
    root, pl = run
    n = now(root, 0.3)
    assert (n['id'], n['pos']) == ('1', '21000'), n


@pytest.mark.parametrize('config', [MEMORY])
def test_a_queue_past_999_songs_is_not_kept(run):
    """playback.c SAVE_LIMIT: past it the kept queue is dropped"""
    root, pl = run
    card_in(root)
    play_list(root, [1, 2], 0)
    pl.pump()
    assert wait_for(root/'mnt/mmc/.q2pod/ringnav-queue', 3)
    play_list(root, [1, 2] * 500, 0)
    pl.pump(1.5)
    assert not (root/'mnt/mmc/.q2pod/ringnav-queue').exists()


# ---- likes both ways (docs/playd.md#ratings-and-likes) ----

def stock_myfav(root, rows=()):
    """stock's myfav in database.db (today's Vamp's My Fav), some of its columns, with rows' fileurls"""
    db = sqlite3.connect(root/'mnt/data/database.db')
    db.execute('create table myfav(id integer primary key, filename, fileurl, album, songer, name)')
    db.executemany('insert into myfav(fileurl) values (?)', [(r,) for r in rows])
    db.commit()
    db.close()


def stock_favs(root):
    db = sqlite3.connect(root/'mnt/data/database.db')
    try:
        return sorted(db.execute('select fileurl, name, songer, album from myfav'))
    finally:
        db.close()


def test_a_like_under_vamp_os_is_written_through_to_stock_s_my_fav(run):
    """catalogue.c fav_set_on: the song's row copied into stock's myfav (its columns that the library has), taken
    out at an unlike, so today's Vamp finds it"""
    root, pl = run
    stock_myfav(root)
    play_list(root, [1, 2], 0)
    pl.pump()
    send(root, '97 like 1')
    assert wait_for_value(lambda: acks(root)[:1], [(97, 0)])
    assert stock_favs(root) == [('/mnt/mmc/Music/a.flac', 'Alpha', 'Ann', 'First')]
    send(root, '98 like 1')
    assert wait_for_value(lambda: acks(root)[:1], [(98, 0)])
    assert stock_favs(root) == []


def back_from_today(root):
    """likes.db as Vamp OS left it (a.flac liked), then today's Vamp's My Fav changed: a.flac out, b.mp3 in"""
    (root/'mnt/data/vamp').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'mnt/data/vamp/likes.db')
    db.execute('create table myfav(fileurl text primary key)')
    db.execute("insert into myfav values ('/mnt/mmc/Music/a.flac')")
    db.commit()
    db.close()
    stock_myfav(root, ['/mnt/mmc/Music/b.mp3'])


@pytest.mark.parametrize('prepare', [back_from_today])
def test_likes_changed_under_today_s_vamp_are_taken_at_the_next_start(run):
    root, pl = run
    assert likes(root) == ['/mnt/mmc/Music/b.mp3']
    play_list(root, [1, 2], 0)
    pl.pump()
    assert now(root)['liked'] == '0'
    fifo(root, 'next')
    pl.pump()
    assert now(root)['liked'] == '1'


def test_a_like_stock_cannot_take_yet_waits_and_is_written_once_it_can(run):
    """no myfav to write (stock's database.db without it yet, or the song's row on a card that is away): the like is
    kept as waiting in likes.db and written through once it can be"""
    root, pl = run
    play_list(root, [1, 2], 0)
    pl.pump()
    send(root, '99 like 1')
    assert wait_for_value(lambda: acks(root)[:1], [(99, 0)])
    assert now(root)['liked'] == '1'
    stock_myfav(root)
    assert wait_for_value(lambda: stock_favs(root), [('/mnt/mmc/Music/a.flac', 'Alpha', 'Ann', 'First')], 6)


def test_a_server_song_s_like_is_written_through_from_its_catalogue_row(run):
    """a server song (here by its offline copy) is its server row in stock's myfav, copied from the catalogue's
    servertable as today's fav_set_on copies it"""
    root, pl = run
    stock_myfav(root)
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'tmp/q2-navi.state').write_text('status=idle\nserver=down\n')
    play_list(root, [SERVER + 1, 2], 0)
    pl.pump(1.0)
    send(root, f'60 like {SERVER + 1}')
    assert wait_for_value(lambda: acks(root)[:1], [(60, 0)])
    assert stock_favs(root) == [('/mnt/mmc/.navi/cat/al1/s1.flac', 'Cloud One', 'Sky', 'Up')]
    sock.close()


def test_a_like_whose_row_is_on_a_card_away_waits_for_the_card(run):
    """the catalogue (on the card) gone when the like is made: nothing to copy, so it waits and is written once the
    catalogue is back"""
    root, pl = run
    stock_myfav(root)
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'tmp/q2-navi.state').write_text('status=idle\nserver=down\n')
    play_list(root, [SERVER + 1, 2], 0)
    pl.pump(1.0)
    cat = root/'mnt/mmc/.vamp/catalogue.db'
    cat.rename(root/'catalogue.away')
    send(root, f'61 like {SERVER + 1}')
    assert wait_for_value(lambda: acks(root)[:1], [(61, 0)])
    assert stock_favs(root) == []
    (root/'catalogue.away').rename(cat)
    assert wait_for_value(lambda: stock_favs(root), [('/mnt/mmc/.navi/cat/al1/s1.flac', 'Cloud One', 'Sky', 'Up')], 6)
    sock.close()


# ---- Resume (docs/playd.md#resume) ----

def path_key(path):
    """playback_power.c rs_file: the path's FNV-1a with fnv's separator (listen_key with no CUE start)"""
    return listen_key(path)


def spots(root, rel='mnt/mmc/.q2pod/ringnav-resume'):
    data = (root/rel).read_bytes()
    w = struct.unpack(f'<{len(data) // 4}I', data)
    return [(w[i], w[i + 1]) for i in range(0, len(w), 2)]


def ring(entries):
    w = [x for e in entries for x in e] + [0] * (1024 - 2 * len(entries))
    return struct.pack('<1024I', *w)


def test_a_long_song_s_place_is_kept_every_minute_of_play_and_at_a_pause(run):
    """docs/internals.md#resume: a song of at least Resume Songs' minutes (30 by default) keeps its place by its path's
    hash in the card's .q2pod/ringnav-resume, 512 places, 4 KB, the most recent first: each 60 s of movement and once
    the second stands still two reads (a pause)"""
    root, pl = run
    card_in(root)
    play_list(root, [1, 2], 0)
    pl.pump()
    heard(root, pl, 3600, range(0, 58, 3))
    assert not (root/'mnt/mmc/.q2pod/ringnav-resume').exists()
    heard(root, pl, 3600, [60, 63, 66])
    r = spots(root)
    assert len(r) == 512 and r[0] == (path_key('/mnt/mmc/Music/a.flac'), 63) and r[1] == (0, 0), r[:2]  # 60 s on from 3 s, where it was first read
    heard(root, pl, 3600, [70, 70, 70])
    assert spots(root)[0] == (path_key('/mnt/mmc/Music/a.flac'), 70), 'paused at 70 s'


def test_a_kept_place_is_jumped_to_as_the_song_starts(run):
    """a long song with a place, started near its beginning (under 5 s), seeks there"""
    root, pl = run
    card_in(root)
    (root/'mnt/mmc/.q2pod').mkdir(parents=True)
    (root/'mnt/mmc/.q2pod/ringnav-resume').write_bytes(ring([(path_key('/mnt/mmc/Music/b.mp3'), 500)]))
    play_list(root, [2], 0)
    pl.pump()
    heard(root, pl, 3600, [0, 1, 2])
    assert ('mcl-seek', '500') in pl.got, pl.got


def test_a_place_near_either_end_is_forgotten_and_a_short_song_keeps_none(run):
    root, pl = run
    card_in(root)
    a, b = path_key('/mnt/mmc/Music/a.flac'), path_key('/mnt/mmc/Music/b.mp3')
    (root/'mnt/mmc/.q2pod').mkdir(parents=True)
    (root/'mnt/mmc/.q2pod/ringnav-resume').write_bytes(ring([(b, 40), (a, 900)]))
    play_list(root, [1, 2], 0)
    pl.pump()
    heard(root, pl, 3600, [1000, 3575, 3578, 3578, 3578])
    assert a not in [k for k, _ in spots(root)], 'within 30 s of the end: forgotten'
    fifo(root, 'next')
    pl.pump()
    heard(root, pl, 600, [0, 1, 2, 70, 73, 140, 140, 140])
    assert ('mcl-seek', '40') not in pl.got and spots(root)[0] == (b, 40), 'a 10 minute song: neither kept nor jumped to'


@pytest.mark.parametrize('config', [CONFIG.replace('THEME=0\n', 'THEME=0\nRESUMEMIN=20\n')])
def test_resume_songs_minutes_are_q2pod_resumemin(run):
    root, pl = run
    card_in(root)
    play_list(root, [1], 0)
    pl.pump()
    heard(root, pl, 1200, [90, 95, 100, 100, 100])
    assert spots(root)[0] == (path_key('/mnt/mmc/Music/a.flac'), 100)


@pytest.mark.parametrize('config', [CONFIG.replace('THEME=0\n', 'THEME=0\nRESUMEMIN=0\n')])
def test_resume_songs_off_keeps_no_place_but_for_spoken_files(run):
    root, pl = run
    card_in(root)
    db = sqlite3.connect(root/'mnt/data/database.db')
    db.execute("insert into songtable values (21, '/mnt/mmc/Music/book.M4B', 'Book', 'Au', 'B', 0)")
    db.commit()
    db.close()
    play_list(root, [1, 21], 0)
    pl.pump()
    heard(root, pl, 3600, [90, 95, 100, 100, 100])
    assert not (root/'mnt/mmc/.q2pod/ringnav-resume').exists()
    fifo(root, 'next')
    pl.pump()
    heard(root, pl, 900, [0, 95, 100, 100, 100])
    assert spots(root)[0] == (path_key('/mnt/mmc/Music/book.M4B'), 100), 'an M4B is long whatever its length'


def test_a_song_s_place_is_kept_as_the_next_one_starts(run):
    root, pl = run
    card_in(root)
    play_list(root, [1, 2], 0)
    pl.pump()
    heard(root, pl, 3600, [0, 3, 6, 9, 40, 43])
    fifo(root, 'next')
    pl.pump()
    heard(root, pl, 200, [0])
    assert spots(root)[0] == (path_key('/mnt/mmc/Music/a.flac'), 43)


def test_the_flash_ring_of_a_release_before_is_read_until_the_card_has_one(run):
    root, pl = run
    card_in(root)
    (root/'mnt/data/ringnav-resume').write_bytes(ring([(path_key('/mnt/mmc/Music/a.flac'), 321)]))
    play_list(root, [1], 0)
    pl.pump()
    heard(root, pl, 3600, [0, 1, 2])
    assert ('mcl-seek', '321') in pl.got


def test_a_paused_start_waits_for_play_before_it_jumps(run):
    """resume_poll: not playing yet (a paused Memory Playback start, a pause near the start) waits for Play"""
    root, pl = run
    card_in(root)
    (root/'mnt/mmc/.q2pod').mkdir(parents=True)
    (root/'mnt/mmc/.q2pod/ringnav-resume').write_bytes(ring([(path_key('/mnt/mmc/Music/b.mp3'), 500)]))
    play_list(root, [2], 0)
    pl.pump()
    fifo(root, 'pause')
    pl.pump()
    heard(root, pl, 3600, [0, 1, 2])
    assert ('mcl-seek', '500') not in pl.got
    fifo(root, 'resume')
    pl.pump()
    heard(root, pl, 3600, [2, 3])
    assert ('mcl-seek', '500') in pl.got


def kept_with_place(root):
    kept(THREE, 2, 77)(root)
    (root/'mnt/mmc/.q2pod/ringnav-resume').write_bytes(ring([(path_key('/mnt/mmc/Music/c}d.flac'), 500)]))


@pytest.mark.parametrize('config, prepare', [(MEMORY, kept_with_place)])
def test_a_song_started_past_its_first_seconds_keeps_where_it_starts(run):
    """only a start under 5 s jumps: Memory Playback's Location start at 77 s stays there"""
    root, pl = run
    fifo(root, 'resume')
    pl.pump()
    heard(root, pl, 3600, [77, 78, 79])
    assert ('mcl-seek', '500') not in pl.got, pl.got


# ---- ratings to the servers (docs/playd.md#ratings-and-likes, docs/server-library.md#ratings-and-favorites) ----

def apps(root, **ratings):
    for app, n in ratings.items():
        (root/f'mnt/mmc/.{app}').mkdir(parents=True, exist_ok=True)
        (root/f'mnt/mmc/.{app}/q2app').write_text(f'name={app}\nratings={n}\n')


def out_lines(root, app):
    p = root/f'mnt/mmc/.{app}/ratings.out'
    return [l.split('\t') for l in p.read_text().splitlines()] if p.exists() else []


def test_stars_and_likes_go_to_every_app_that_takes_them(run):
    """catalogue.c rated_line: "<listen_key>\\t<sid>\\t<stars=N|like=0|1>\\t<time>" appended to each ratings=2 app's
    ratings.out; a ratings=1 app is sent nothing; a card song has no sid"""
    root, pl = run
    apps(root, navi=2, old=1)
    play_list(root, [1], 0)
    pl.pump()
    t0 = int(time.time())
    send(root, '62 rate 4 1', '63 like 1')
    assert wait_for_value(lambda: acks(root)[:2], [(63, 0), (62, 0)])
    lines = out_lines(root, 'navi')
    key = '%08x' % listen_key('/mnt/mmc/Music/a.flac')
    assert [l[:3] for l in lines] == [[key, '', 'stars=4'], [key, '', 'like=1']], lines
    assert all(t0 <= int(l[3]) <= t0 + 5 for l in lines)
    assert out_lines(root, 'old') == []


def test_a_server_song_s_line_carries_its_sid_for_its_own_app(run):
    root, pl = run
    apps(root, navi=2, other=2)
    sock = catalogue(root, copies={SERVER + 1: COPY1})
    (root/'tmp/q2-navi.state').write_text('status=idle\nserver=down\n')
    play_list(root, [SERVER + 1], 0)
    pl.pump(1.0)
    send(root, f'64 rate 5 {SERVER + 1}')
    assert wait_for_value(lambda: acks(root)[:1], [(64, 0)])
    key = '%08x' % listen_key('/mnt/mmc/.navi/cat/al1/s1.flac')
    assert [l[:3] for l in out_lines(root, 'navi')] == [[key, 's1', 'stars=5']]
    assert [l[:3] for l in out_lines(root, 'other')] == [[key, '', 'stars=5']]
    sock.close()


def ratings_in(root, app, lines):
    tmp = root/f'mnt/mmc/.{app}/ratings.in.tmp'
    tmp.write_text(''.join('\t'.join(map(str, l)) + '\n' for l in lines))
    tmp.rename(root/f'mnt/mmc/.{app}/ratings.in')


def test_a_server_s_stars_and_likes_come_in_from_ratings_in(run):
    """docs/server-library.md#ratings-and-favorites In: stars=N by listen_key, like=0|1 to My Fav by the song's path (a
    card file found by its key, a server row by its sid, its offline copy taken out); the file deleted once read"""
    root, pl = run
    apps(root, navi=2)
    stock_myfav(root)
    copy2 = '/mnt/mmc/.navi/stream/acct/hi/a/Cloud Two [s2].flac'
    sock = catalogue(root, copies={SERVER + 2: copy2})
    db = sqlite3.connect(root/'mnt/data/vamp/likes.db')  # a heart given to the copy on Now Playing: its own path
    db.execute('insert into myfav values (?)', (copy2,))
    db.commit()
    db.close()
    a, s2 = listen_key('/mnt/mmc/Music/a.flac'), listen_key('/mnt/mmc/.navi/cat/al1/s2.flac')
    ratings_in(root, 'navi', [('%08x' % a, '', 'stars=3', 100), ('%08x' % a, '', 'like=1', 100),
                              ('%08x' % s2, 's2', 'like=1', 100)])
    assert wait_for_value(lambda: (root/'mnt/mmc/.navi/ratings.in').exists(), False, 6)
    assert likes(root) == ['/mnt/mmc/.navi/cat/al1/s2.flac', '/mnt/mmc/Music/a.flac']
    assert [f[0] for f in stock_favs(root)] == ['/mnt/mmc/.navi/cat/al1/s2.flac', '/mnt/mmc/Music/a.flac']
    play_list(root, [1], 0)
    pl.pump()
    n = now(root)
    assert (n['hearts'], n['liked']) == ('3', '1'), n
    sock.close()


def test_a_change_made_here_after_the_server_s_wins(run):
    """a line is skipped when the song's own ratings.out line of the same kind is newer"""
    root, pl = run
    apps(root, navi=2)
    play_list(root, [1], 0)
    pl.pump()
    send(root, '65 rate 4 1')
    assert wait_for_value(lambda: acks(root)[:1], [(65, 0)])
    a = '%08x' % listen_key('/mnt/mmc/Music/a.flac')
    ratings_in(root, 'navi', [(a, '', 'stars=2', 1000), (a, '', 'like=1', 1000)])
    assert wait_for_value(lambda: (root/'mnt/mmc/.navi/ratings.in').exists(), False, 6)
    n = now(root)
    assert (n['hearts'], n['liked']) == ('4', '1'), n


def test_a_ratings_1_app_s_one_number_is_its_star_or_stars(run):
    """five is its star (the like, the stars left as they are); under five that many stars and no like"""
    root, pl = run
    apps(root, old=1)
    a, b = '%08x' % listen_key('/mnt/mmc/Music/a.flac'), '%08x' % listen_key('/mnt/mmc/Music/b.mp3')
    ratings_in(root, 'old', [(a, '', '5', 100), (b, '', '3', 100)])
    assert wait_for_value(lambda: (root/'mnt/mmc/.old/ratings.in').exists(), False, 6)
    assert likes(root) == ['/mnt/mmc/Music/a.flac']
    play_list(root, [1, 2], 0)
    pl.pump()
    assert (now(root)['hearts'], now(root)['liked']) == ('0', '1')
    fifo(root, 'next')
    pl.pump()
    assert (now(root)['hearts'], now(root)['liked']) == ('3', '0')
