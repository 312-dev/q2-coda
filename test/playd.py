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
def run(binary, shim, tmp_path, config):
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
