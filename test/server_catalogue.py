#!/usr/bin/env python3
"""Write a server library catalogue (docs/server-library.md) for a card, to drive the firmware's
import at scale without a server: N made-up server songs in albums of 12, a few playlists, and,
with --card, the card's own FLAC files listed again as server songs (as a server holding the same
files would), so the duplicate rule has real files to hide.

    test/server_catalogue.py CARD_ROOT --songs 10000 [--app subsonic] [--seq N] [--mbid-every 40]

CARD_ROOT is the folder that is /mnt/mmc on the Q2 (an emulator's state/mmc). It writes
CARD_ROOT/.<app>/q2app (catalogue=1 ratings=1) when there is none and CARD_ROOT/.<app>/catalogue.tsv,
aside and renamed, with a seq one past the file's. No server and no credentials are involved.

The card's files are listed with their real size and FLAC length and the suffix "flac", so the size
layer matches them; every third one carries its title (the file's name less its track number, as the
user's files are tagged) and another artist and album, to show a size match needs only the title
that makes the file a candidate. --mbid-every gives every Nth made-up song a MusicBrainz id."""
import argparse
import os
import pathlib
import struct
import uuid


def esc(s):
    return str(s).replace('%', '%25').replace('\t', '%09').replace('\n', '%0A').replace('\r', '%0D')


def row(*fields):
    return '\t'.join(esc(f) for f in fields) + '\n'


def flac_seconds(path):
    """A FLAC's length from its STREAMINFO, rounded down; 0 when it is not one."""
    with open(path, 'rb') as f:
        head = f.read(42)
    if head[:4] != b'fLaC' or len(head) < 42:
        return 0
    rate_ch_bits_total = struct.unpack('>Q', head[18:26])[0]
    rate = rate_ch_bits_total >> 44
    total = rate_ch_bits_total & ((1 << 36) - 1)
    return total // rate if rate else 0


def card_songs(card):
    for p in sorted(pathlib.Path(card).rglob('*.flac')):
        if any(part.startswith('.') for part in p.relative_to(card).parts):
            continue  # hidden folders: the scan skips them, as the stream cache's
        yield p


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('card')
    ap.add_argument('--songs', type=int, default=100)
    ap.add_argument('--app', default='subsonic')
    ap.add_argument('--seq', type=int)
    ap.add_argument('--mbid-every', type=int, default=40)
    ap.add_argument('--no-card', action='store_true', help="don't list the card's files")
    a = ap.parse_args()
    folder = pathlib.Path(a.card)/f'.{a.app}'
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder/'q2app').exists():
        (folder/'q2app').write_text('name=Server Test\ncatalogue=1\nratings=1\n')
    tsv = folder/'catalogue.tsv'
    seq = a.seq
    if seq is None:
        seq = 1
        if tsv.exists():
            first = tsv.open().readline()
            seq = next((int(kv[4:]) + 1 for kv in first.split('\t') if kv.startswith('seq=')), 1)
    rows = []
    for n in range(a.songs):
        album, track = divmod(n, 12)
        mbid = str(uuid.UUID(int=n + 1)) if a.mbid_every and n % a.mbid_every == 0 else ''
        rows.append(row('s', f'gen{n}', f'Server Song {n}', f'Artist {album % 400}', f'Artist {album % 400}',
                        f'Album {album}', f'al{album}', 2000 + album % 25, 1, track + 1, 150 + n % 200, 'Rock',
                        'flac', '', 20_000_000 + n, mbid))
    listed = []
    if not a.no_card:
        for i, p in enumerate(card_songs(a.card)):
            sec = flac_seconds(p)
            title = p.stem.split(' - ', 1)[-1]
            artist, album = ('Some Artist', 'Some Album') if i % 3 == 0 else (p.parent.parent.name, p.parent.name)
            rows.append(row('s', f'card{i}', title, artist, '', album, 'cardal', 2020, 1, i + 1, sec,
                            'Pop', 'flac', '', p.stat().st_size, ''))
            listed.append(f'card{i}')
    for k in range(min(5, max(1, a.songs // 100))):
        sids = [f'gen{n}' for n in range(k * 37, min(a.songs, k * 37 + 25))] + listed[:3]
        rows.append(row('p', f'pl{k}', f'Server Mix {k}'))
        rows += [row('i', f'pl{k}', sid, '') for sid in sids]
    tmp = tsv.with_suffix('.tsv.tmp')
    with tmp.open('w') as f:
        f.write(f'#Q2CAT/1\tseq={seq}\taccount=0123abcd\trows={len(rows)}\n')
        f.writelines(rows)
    os.replace(tmp, tsv)
    print(f'{tsv}: seq {seq}, {len(rows)} rows ({a.songs} made up, {len(listed)} card files)')


if __name__ == '__main__':
    main()
