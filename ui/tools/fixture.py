#!/usr/bin/env python3
"""Make a library database of N made-up songs in stock's schema (songtable and its indexes), for q2ui's
spike and for timing demo's Songs list against it (docs/ui-rewrite.md#measurements).

    python3 ui/tools/fixture.py N OUT.db [--seed S]

Titles, artists and albums are generated from word lists here; nothing is read from a real library. Every
40th title is long enough to be cut with an ellipsis, and a few carry accents and CJK so the text path is
not ASCII alone. The files named do not exist: a list only reads the table.
"""
import argparse
import random
import sqlite3

ADJ = ('Amber Silent Paper Northern Velvet Hollow Electric Winter Distant Golden Crooked Borrowed Quiet Salt '
       'Copper Midnight Marble Feral Tidal Lantern Glass Slow Bright Fallow Hidden Ivory Rust Cobalt Open Little').split()
NOUN = ('Harbor Engine Static Garden Ferry Orchard Signal Meadow Lines River Window Atlas Thread Season Motel '
        'Arcade Compass Hours Letters Parade Field Station Kites Bridges Embers Rooms Maps Coast Weather Echo').split()
EXTRA = ('(Live at the Tin Pavilion, Second Night, Complete Take)', '(Remastered for an Imaginary Anniversary Edition)',
         'Pt. 2: The Long Way Round the Reservoir at Dusk')
ODD = ('Café Lumière', 'Señal Perdida', 'Über den Dächern', '夜の庭', '风中的信', 'Zażółć gęślą jaźń')
COLUMNS = ('filename fileurl album songer style name composer albumsonger year track cd_num time time_end '
           'time_create Initial bitdepth samprate').split()
SCHEMA = '''CREATE TABLE songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename VARCHAR(512), fileurl VARCHAR(1024),
 album VARCHAR(512), songer VARCHAR(256), style VARCHAR(128), name VARCHAR(512), composer VARCHAR(256),
 albumsonger VARCHAR(256), year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, time_end INTEGER,
 time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)'''
INDEXES = ('CREATE INDEX songtable_album on songtable(album collate nocase)',
           'CREATE INDEX songtable_filename on songtable(filename)',
           'CREATE INDEX songtable_time_create on songtable(time_create desc)')
OTHERS = 'baidu_downend myfav playlist recentplay oftenplay'.split()


def songs(n, seed=1):
    rng = random.Random(seed)
    artists = [f'{rng.choice(ADJ)} {rng.choice(NOUN)}' for _ in range(max(4, n // 25))]
    for i in range(n):
        artist = rng.choice(artists)
        album = f'{rng.choice(NOUN)} {rng.choice(NOUN)}'
        title = f'{rng.choice(ADJ)} {rng.choice(NOUN)}'
        if i % 40 == 7:
            title += ' ' + rng.choice(EXTRA)
        elif i % 97 == 11:
            title = rng.choice(ODD)
        elif rng.random() < 0.5:
            title += ' ' + rng.choice(NOUN)
        track = i % 12 + 1
        name = f'{track:02d} {title}.flac'
        first = ord(title[0].upper()) if title[0].isascii() and title[0].isalpha() else 35
        yield (name, f'/mnt/mmc/Music/{artist}/{album}/{name}', album, artist, 'Test Tones', title, '', artist,
               2000 + i % 25, track, 1000, 0, 0, 1794857326 + i, first, 16, 44100)


def make(n, out, seed=1):
    db = sqlite3.connect(out)
    db.execute(SCHEMA)
    for t in OTHERS:
        db.execute(SCHEMA.replace('songtable', t))
    for index in INDEXES:
        db.execute(index)
    db.executemany(f'insert into songtable({",".join(COLUMNS)}) values ({",".join("?" * len(COLUMNS))})', songs(n, seed))
    db.commit()
    db.close()


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('n', type=int)
    ap.add_argument('out')
    ap.add_argument('--seed', type=int, default=1)
    a = ap.parse_args()
    make(a.n, a.out, a.seed)
