"""Cabin Solitaire's winnable deals: the solver (solitaire_solver.c) built and run, and the seed table it makes.

    python3 test/fw/solitaire_solver.py --write

solves seeds from 1 up, in draw three and in draw one, and writes the first SEEDS that the solver won in both to
patch/game_solitaire_seeds.h (the firmware's table, docs/games-engine.md#cabin-solitaire). test_game_solitaire.py
imports solve() to win a sample of the table again and replay each win through the game's own moves."""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
SOURCE = pathlib.Path(__file__).with_name('solitaire_solver.c')
HEADER = ROOT/'patch/game_solitaire_seeds.h'
SEEDS, STATES = 256, 200_000


def build(out):
    """The solver compiled into the directory out; its path."""
    exe = pathlib.Path(out)/'solitaire_solver'
    subprocess.run(['cc', '-O2', '-o', str(exe), str(SOURCE)], check=True)
    return exe


def solve(exe, draw, seeds, states=STATES):
    """{seed: [move, ...] or None}: 'D' for Play's draw or recycle, (from, to, n) for a move of n cards."""
    out = subprocess.run([str(exe), str(draw), str(states), *map(str, seeds)], check=True, capture_output=True,
                         text=True).stdout
    won = {}
    for line in out.splitlines():
        seed, verdict, *moves = line.split()
        won[int(seed)] = None if verdict == 'none' else [m if m == 'D' else tuple(map(int, m.split('-'))) for m in moves]
    return won


def table(exe):
    """The first SEEDS seeds the solver won in both draws."""
    found, seed = [], 1
    while len(found) < SEEDS:
        batch = list(range(seed, seed + 200))
        three, one = solve(exe, 3, batch), solve(exe, 1, batch)
        found += [s for s in batch if three[s] and one[s]]
        seed += 200
    return found[:SEEDS]


def header(seeds):
    rows = [', '.join(str(s) for s in seeds[i:i + 16]) for i in range(0, len(seeds), 16)]
    body = ',\n    '.join(rows)
    return ('/* Cabin Solitaire\'s winnable deals: seeds of game_solitaire.c\'s deal that the test tree\'s solver\n'
            ' * (test/fw/solitaire_solver.c) won in draw three and in draw one. Written by\n'
            ' * `python3 test/fw/solitaire_solver.py --write`; test_game_solitaire.py wins a sample again. */\n'
            f'#define SOL_SEEDS {len(seeds)}\n'
            f'static const unsigned short sol_seeds[SOL_SEEDS] = {{\n    {body}\n}};\n')


if __name__ == '__main__':
    if sys.argv[1:] != ['--write']:
        sys.exit(__doc__)
    with tempfile.TemporaryDirectory() as tmp:
        seeds = table(build(tmp))
    HEADER.write_text(header(seeds))
    print(f'{len(seeds)} seeds, the last {seeds[-1]}, in {HEADER.relative_to(ROOT)}')
