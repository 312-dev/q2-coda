#!/usr/bin/env python3
"""q2libd (libd/q2libd.c, docs/libd.md) built for this machine against its own FFmpeg and SQLite, run with --once on a
stand-in card: the songs' tags into /mnt/data/vamp/library.db's songtable (stock's columns), ids kept across scans,
unchanged files not read again, a changed one read again, a removed one gone, dot folders and other files left out;
a signed-in app's Q2CAT/1 catalogue into /mnt/mmc/.vamp/catalogue.db's servertable, a signed-out or cut-short one
left out; stock's database.db untouched; and q2ui's own query (ui/lib.c's union) over the two. Needs ffmpeg to make
the songs. Run it as python3 test/libd.py (pytest). Q2LIBD_SOURCE builds another copy, to show a broken one fails."""
import os
import pathlib
import platform
import sqlite3
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2LIBD_SOURCE') or ROOT/'libd/q2libd.c')
# ui/lib.c's query with a server catalogue attached as cat, as q2ui runs it
Q2UI_SQL = ('select name, songer, id, album, track from (select name, songer, id, album, track from main.songtable union all '
            'select name, songer, id, album, track from cat.servertable) order by name collate nocase, id')


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('libd')
    src = out/'q2libd.c'
    src.write_bytes(SOURCE.read_bytes())
    inc = sorted(pathlib.Path(os.environ.get('Q2_THIRDPARTY_CACHE', pathlib.Path.home()/'.cache/q2-thirdparty')).glob('out/sqlite-*/usr/include'))
    lib = next(pathlib.Path('/usr/lib').glob('*-linux-gnu/libsqlite3.so.0'))
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', *(['-isystem', str(inc[-1])] if inc else []), '-o',
                    str(out/'q2libd'), str(src), '-lavformat', '-lavcodec', '-lavutil', str(lib)], check=True)
    return out/'q2libd'


def song(path, seconds=1, **tags):
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = [a for k, v in tags.items() for a in ('-metadata', f'{k}={v}')]
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', *meta, str(path)],
                   check=True)


@pytest.fixture
def card(tmp_path):
    m = tmp_path/'mnt/mmc'
    song(m/'Music/Ann/First/01 Alpha.flac', title='Alpha', artist='Ann', album='First', track='1', date='2001', album_artist='Ann')
    song(m/'Music/Bob/b.mp3', title='Beta', artist='Bob', album='Second', track='7/12')
    song(m/'loose.wav')
    song(m/'.hidden/c.flac', title='Hidden')
    (m/'Music/notes.txt').write_text('not music')
    (tmp_path/'mnt/data').mkdir(parents=True)
    stock = tmp_path/'mnt/data/database.db'
    sqlite3.connect(stock).execute('create table songtable(id integer primary key, name)').connection.commit()
    return tmp_path


def scan(binary, root):
    r = subprocess.run([str(binary), '--root', str(root), '--once'], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stderr


def rows(root, sql='select fileurl, name, songer, album, track, year, albumsonger, id from songtable order by fileurl'):
    return sqlite3.connect(root/'mnt/data/vamp/library.db').execute(sql).fetchall()


def test_the_card_s_songs_with_their_tags(binary, card):
    stock = (card/'mnt/data/database.db').read_bytes()
    log = scan(binary, card)
    got = {r[0]: r[1:7] for r in rows(card)}
    assert got == {'/mnt/mmc/Music/Ann/First/01 Alpha.flac': ('Alpha', 'Ann', 'First', 1, 2001, 'Ann'),
                   '/mnt/mmc/Music/Bob/b.mp3': ('Beta', 'Bob', 'Second', 7, 0, ''),
                   '/mnt/mmc/loose.wav': ('loose', '', '', 0, 0, '')}
    assert 'card: 3 songs, 3 read' in log
    assert (card/'mnt/data/database.db').read_bytes() == stock, "stock's database is never touched"
    seconds = dict(sqlite3.connect(card/'mnt/data/vamp/library.db').execute('select fileurl, time from songtable').fetchall())
    assert seconds['/mnt/mmc/loose.wav'] == 1


def test_ids_stay_unchanged_files_are_not_read_and_changes_are(binary, card):
    scan(binary, card)
    ids = {r[0]: r[7] for r in rows(card)}
    log = scan(binary, card)
    assert 'card: 3 songs, 0 read' in log and {r[0]: r[7] for r in rows(card)} == ids
    m = card/'mnt/mmc'
    (m/'loose.wav').unlink()
    song(m/'Music/Bob/b.mp3', title='Beta Two', artist='Bob', album='Second')
    os.utime(m/'Music/Bob/b.mp3', (time.time() + 5, time.time() + 5))
    song(m/'new.flac', title='Gamma')
    log = scan(binary, card)
    got = {r[0]: (r[1], r[7]) for r in rows(card)}
    assert '/mnt/mmc/loose.wav' not in got and got['/mnt/mmc/Music/Bob/b.mp3'] == ('Beta Two', ids['/mnt/mmc/Music/Bob/b.mp3'])
    assert got['/mnt/mmc/Music/Ann/First/01 Alpha.flac'][1] == ids['/mnt/mmc/Music/Ann/First/01 Alpha.flac']
    assert got['/mnt/mmc/new.flac'][1] > max(ids.values()) and 'card: 3 songs, 2 read' in log


def catalogue(root, app, lines, rows=None, signed_in=True):
    d = root/'mnt/mmc'/f'.{app}'
    d.mkdir(parents=True, exist_ok=True)
    body = ''.join(l + '\n' for l in lines)
    n = rows if rows is not None else sum(1 for l in lines if l[:2] in ('s\t', 'p\t', 'i\t'))
    (d/'catalogue.tsv').write_text(f'#Q2CAT/1\tseq=1\taccount=0123abcd\trows={n}\n' + body)
    if signed_in:
        (d/'credentials').write_text('x')


S1 = 's\tsid1\tServer%09Song\tSam\tSam\tLive\talb1\t1999\t1\t3\t200\tRock\tflac\t\t1000\t'
S2 = 's\tsid2\tOther\tSue\t\tB-Sides\talb2\t0\t0\t1\t100\tPop\tmp3'


def test_a_signed_in_app_s_catalogue_and_only_it(binary, card):
    catalogue(card, 'subsonic', [S1, S2, 'p\tpl1\tMix', 'i\tpl1\tsid1\t'])
    catalogue(card, 'other', [S1], signed_in=False)
    catalogue(card, 'zshort', [S1, S2], rows=5)  # after subsonic: slots go by name, so subsonic is slot 0
    scan(binary, card)
    db = sqlite3.connect(card/'mnt/mmc/.vamp/catalogue.db')
    got = db.execute('select id, name, songer, album, track, year, fileurl, style from servertable order by id').fetchall()
    assert got == [(0x40000000, 'Server\tSong', 'Sam', 'Live', 3, 1999, '/mnt/mmc/.subsonic/cat/alb1/sid1.flac', 'Rock'),
                   (0x40000001, 'Other', 'Sue', 'B-Sides', 1, 0, '/mnt/mmc/.subsonic/cat/alb2/sid2.mp3', 'Pop')]


def test_q2ui_s_query_reads_both(binary, card):
    catalogue(card, 'subsonic', [S2])
    scan(binary, card)
    db = sqlite3.connect(card/'mnt/data/vamp/library.db')
    db.execute('attach ? as cat', (str(card/'mnt/mmc/.vamp/catalogue.db'),))
    assert [r[0] for r in db.execute(Q2UI_SQL)] == ['Alpha', 'Beta', 'loose', 'Other']


def test_as_a_service_an_empty_library_first_then_the_card_once_mounted(binary, card):
    after = card/'after'
    p = subprocess.Popen([str(binary), '--root', str(card), '--after', f'echo x >> {after}'], stderr=subprocess.PIPE)
    try:
        end = time.monotonic() + 5
        while not after.exists() and time.monotonic() < end:
            time.sleep(0.1)
        assert rows(card) == [] and after.read_text() == 'x\n', 'no card yet: an empty library, and --after run'
        (card/'proc').mkdir()
        (card/'proc/mounts').write_text('/dev/mmcblk4p1 /mnt/mmc vfat rw 0 0\n')
        end = time.monotonic() + 15
        while len(rows(card)) < 3 and time.monotonic() < end:
            time.sleep(0.2)
        assert len(rows(card)) == 3
        time.sleep(0.5)
        assert after.read_text().count('x') >= 2, 'and --after again once the card is read'
    finally:
        p.terminate()
        p.wait(10)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
