"""Sudoku (patch/game_sudoku.c) on the engine: the generator and its slices, each level's puzzles checked by an
independent solver, the wheel's cursor and the dial of digits on its sticky note, pencil notes, the peer wash a step
behind the cursor, conflicts, a finished unit's wave, the finish and its best, the pause's blotter, Play held for the
levels, touch, rests counted as time, saves, the glass's corners; then through Games' page, full screen. Words and
digits are the packed masks of patch/game_sudoku_art.inc, checked by their solid pixels."""
import ctypes as C
import math
import random
import re

import pytest

import game_art
from host import ROOT
from test_engine import (CENTER, E_CENTRE, E_NEXT, E_PLAY, E_PREV, E_TOUCH, ENGINE, NEXT, PLAY, PREV, RETURN, SHEET, Eng,
                         GameDef, Input, api, screen)
from conftest import O
from test_games import BAR, PAGE, Canvas, Page, fnv1a, paint

GENTLE, STEADY, SHARP = range(3)
CHOOSE, MAKE, PLAYING, DONE = range(4)
G_FILL, G_PICK, G_TEST, G_FINAL, G_DONE = range(5)
TARGET = (36, 30, 24)
WORK, TRIES = 7200, 9
LATE = 135 * WORK
MAGIC, FILE, OLD = 0x314b4453, '/mnt/data/game-sudoku', '/mnt/data/ringnav-sudoku'
TICK, TAP, ALERT = 1, 2, 3  # clicker_kind as eng_sfx sets it
TIME_MAX = 99 * 3600 * 60
REST_MS = 300_000  # the most one rest adds to the time
SUDOKU = ENGINE + 1  # games.c's screen of the engine's second game


_ART = (ROOT/'patch/game_sudoku_art.inc').read_text()
_COUNT, _SPANS = re.search(r'A_COUNT = (\d+)', _ART), re.search(r'#define A_SPAN_BYTES (\d+)', _ART)
assert _COUNT and _SPANS
A_COUNT, A_SPAN_BYTES = int(_COUNT[1]), int(_SPANS[1])


class Solver(C.Structure):
    _fields_ = [('g', C.c_ubyte * 81), ('at', C.c_ubyte * 81), ('used', C.c_ushort * 27), ('left', C.c_ushort * 81),
                *((k, C.c_int) for k in 'depth down found want n'.split())]


class Save(C.Structure):
    _fields_ = [('magic', C.c_uint), *((k, C.c_ubyte * 81) for k in 'clue sol ent'.split()),
                *((k, C.c_ubyte) for k in 'level done cur slips'.split()), ('note', C.c_ushort * 81), ('elapsed', C.c_uint),
                ('best', C.c_uint * 3), ('sum', C.c_uint)]


class Sudoku(C.Structure):
    _fields_ = [('v', Save), *((k, C.c_int) for k in
                                'mode sel dial pencil pick sx sy last dirty record held seq runs wait timed'.split()),
                ('at', C.c_uint), ('owed', C.c_uint), *((k, C.c_int) for k in 'wash wcur wave wt'.split()),
                ('key', C.c_uint * 81), ('side', C.c_ulonglong), ('sv', Solver), ('rng', C.c_uint),
                *((k, C.c_int) for k in 'glevel gphase gpair gclues gtries gdeep'.split()), ('gwork', C.c_uint), ('order', C.c_ubyte * 41),
                ('puz', C.c_ubyte * 81), ('gsol', C.c_ubyte * 81), ('cells', (C.c_ubyte * 9) * 27), ('of', (C.c_ubyte * 3) * 81),
                ('slip', C.c_int), ('busy', C.c_int), ('art_at', C.c_ushort * A_COUNT), ('span_at', C.c_ushort * A_COUNT),
                ('span', C.c_ubyte * A_SPAN_BYTES)]


# An independent solver and grader, in Python.

def peers(i):
    r, c = divmod(i, 9)
    out = {r * 9 + k for k in range(9)} | {k * 9 + c for k in range(9)}
    out |= {(r // 3 * 3 + a) * 9 + c // 3 * 3 + b for a in range(3) for b in range(3)}
    return out - {i}


PEERS = [peers(i) for i in range(81)]
UNITS = [[r * 9 + k for k in range(9)] for r in range(9)] + [[k * 9 + c for k in range(9)] for c in range(9)] + \
        [[(b // 3 * 3 + k // 3) * 9 + b % 3 * 3 + k % 3 for k in range(9)] for b in range(9)]


def cands(g, i):
    return set(range(1, 10)) - {g[p] for p in PEERS[i]}


def count(grid, limit=2):
    """Solutions of grid, up to limit, by plain recursion (fewest candidates first)."""
    g = list(grid)
    if any(g[i] and g[i] in {g[p] for p in PEERS[i]} for i in range(81)):
        return 0
    found = [0]

    def go():
        empty = [i for i in range(81) if not g[i]]
        if not empty:
            found[0] += 1
            return
        i = min(empty, key=lambda i: len(cands(g, i)))
        for d in sorted(cands(g, i)):
            g[i] = d
            go()
            g[i] = 0
            if found[0] >= limit:
                return
    go()
    return found[0]


def singles(grid, hidden):
    """Whether naked singles (and hidden ones, with hidden) alone fill grid."""
    g = list(grid)
    while True:
        placed = False
        for i in range(81):
            if not g[i]:
                c = cands(g, i)
                if not c:
                    return False
                if len(c) == 1:
                    g[i] = c.pop()
                    placed = True
        if hidden:
            for u in UNITS:
                for d in range(1, 10):
                    if d in [g[i] for i in u]:
                        continue
                    where = [i for i in u if not g[i] and d in cands(g, i)]
                    if len(where) == 1:
                        g[where[0]] = d
                        placed = True
        if all(g):
            return True
        if not placed:
            return False


def whole(grid):
    return all(sorted(grid[i] for i in u) == list(range(1, 10)) for u in UNITS)


class Game:
    """Sudoku open on the engine over Page's clock, timers and card, its sheet h high (the whole screen, ENG_FULL); the
    sounds it made and the
    rects each frame marked are kept."""
    def __init__(self, fw, h=320, files=None):
        self.fw, self.e, self.page = fw, api(fw), Page(fw, files)
        self.rects, self.sounds = [], []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.rects.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('buzzeer_switch', lambda *_: self.sounds.append(fw.var('clicker_kind', C.c_int).value))
        self.done = C.CFUNCTYPE(None)(lambda: None)
        self.game = fw.var('eng_sudoku', GameDef)
        assert self.e['eng_open'](self.game, PAGE, SHEET, h, self.done, self.done) == 1
        self.s = Sudoku.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
        self.fn = {k: fw.fn(f'game_sudoku__{k}', *v) for k, v in {
            'gen_run': (C.c_int, C.c_void_p, C.c_int),
            'seen': (C.c_uint, C.c_void_p, C.c_int, C.c_int), 'sane': (C.c_int, C.c_void_p),
            'clock': (C.c_void_p, C.c_char_p, C.c_uint), 'step': (C.c_int, C.c_void_p, C.POINTER(Input)),
            'tally': (C.c_uint, C.c_void_p)}.items()}
        self.frame()

    def step(self, ticks=0, fast=None, held=0, pressed=0, released=0, tx=0, ty=0, rested=0):
        """One step, rested ms after the last (Page's clock)."""
        fast = ticks if fast is None else fast
        self.page.now += rested
        return self.fn['step'](C.addressof(self.s), C.byref(Input(0, ticks, fast, held, pressed, released, tx, ty)))

    def press(self, bit, **kw):
        self.step(held=bit, pressed=bit, **kw)
        return self.step(released=bit, **({'tx': kw['tx'], 'ty': kw['ty']} if 'tx' in kw else {}))

    def frame(self, ms=16):
        """A pass of the frame timer: the rects it marked."""
        self.rects.clear()
        self.page.now += ms
        if self.e['eng_live']():
            self.e['frame'](None)
        return list(self.rects)

    def key(self, key):
        self.e['eng_key'](key, 1), self.e['eng_key'](key, 0)

    def make(self, level, seed, budget=10**9):
        """A puzzle of level from seed, budget a slice: the slices it took."""
        self.s.glevel, self.s.gtries, self.s.gwork, self.s.rng = level, 0, 0, seed
        self.fw.fn('game_sudoku__gen_fill', None, C.c_void_p)(C.addressof(self.s))
        slices = 0
        while self.s.gphase != G_DONE:
            self.fn['gen_run'](C.addressof(self.s), budget)
            slices += 1
            assert slices < 10**6
        return slices

    def puzzle(self, clue, sol, ent=None, cur=40, level=GENTLE):
        """A puzzle on the board, as a finished generator leaves it."""
        v = self.s.v
        v.clue[:], v.sol[:], v.ent[:], v.note[:] = clue, sol, ent or [0] * 81, [0] * 81
        v.level, v.done, v.cur, v.elapsed = level, 0, cur, 0
        self.s.mode, self.s.dial = PLAYING, 0
        self.step()
        C.c_int.from_address(self.fw.var('engine__en', C.c_void_p).value + Eng.rects.offset).value = 0  # no frame took them

    def close(self):
        self.e['eng_close']()


SOLVED = [(r * 3 + r // 3 + c) % 9 + 1 for r in range(9) for c in range(9)]  # a shifted Latin square: a whole grid


def half(sol, gaps):
    return [0 if i in gaps else d for i, d in enumerate(sol)]


@pytest.fixture
def made(fw):
    """A Gentle puzzle the generator made, on the board."""
    g = Game(fw)
    g.make(GENTLE, 0x1234567)
    g.puzzle(list(g.s.puz), list(g.s.gsol))
    yield g
    g.close()


GX, GY, CS, NOTE, CARD = 39, 12, 33, 61, 102  # the grid's left and top rules, its cells; half the dial's note; a level card
DIAL_X, DIAL_Y = (0, 26, 40, 36, 14, -14, -36, -40, -26), (-41, -31, -7, 21, 39, 39, 21, -7, -31)
PAPER, GRAIN, FLECK, INK, SOFT, RULE = 0xefe5cd, 0xdacdb0, 0xf8f0de, 0x1e1b18, 0x5a5248, 0x5d584e
PEN, PENCIL, RED, CURSOR, PEER, TWIN, WAVE = 0x1a3fb0, 0x6e685c, 0xc8202c, 0xffe14d, 0xfbf0b4, 0xcfe0ff, 0xb8f0a0
TRACK, NOTE_Y, BLOT, BLOT_IN, STRIP = 0xb9ae98, 0xffe873, 0x2e5a48, 0x3a6e58, 0xf8f0de
B_PEER, B_TWIN, B_CUR, B_WAVE = 1, 2, 4, 8
K_CLUE, K_ENTRY, K_CLASH = 1, 2, 3
MARGIN = (337, 60, 38, 210)


class Art:
    """game_sudoku_art.inc's masks as the payload holds them, read back with tools/game_art.py's unpack: where a glyph
    or a string lands, as the game's put, mid and say place them."""
    def __init__(self, fw):
        text = (ROOT/'patch/game_sudoku_art.inc').read_text()
        self.id = {k: int(v) for k, v in re.findall(r'(A_\w+) = (\d+)', text)}
        self.sets = dict(re.findall(r'#define (A_\w+)_SET "([^"]*)"', text))
        size = re.search(r'art\[(\d+)\]', text)
        assert size
        self.data = bytes(fw.var('game_sudoku__art', C.c_ubyte * int(size.group(1))))
        self.at = game_art.walk(self.data)
        assert len(self.at) == self.id['A_COUNT']

    def glyph(self, k):
        """(x, y, advance, w, h, alpha 0-15)"""
        d = self.data[self.at[k]:self.at[k + 1] if k + 1 < len(self.at) else len(self.data)]
        w, h, a = game_art.unpack(d[3:])
        return C.c_byte(d[0]).value, C.c_byte(d[1]).value, d[2], w, h, a

    def mid(self, k, x2, y2):
        """[(k, left, top)]: glyph k's ink centred on x2, y2 in half pixels"""
        _, _, _, w, h, _ = self.glyph(k)
        return [(k, (x2 - w) >> 1, (y2 - h) >> 1)]

    def say(self, t, kind, x, y, align=0):
        """[(k, left, top)]: t in the A_HAND or A_CAPS set on the baseline y, from x, centred or ending there"""
        first, chars, out, w = self.id[kind], self.sets[kind], [], 0
        for ch in t:
            if ch == ' ':
                w += 4
                continue
            k = first + chars.index(ch)
            gx, gy, adv, *_ = self.glyph(k)
            out.append((k, w + gx, gy + y))
            w += adv
        shift = x - (w // 2 if align == 1 else w if align == 2 else 0)
        return [(k, l + shift, t) for k, l, t in out]

    def written(self, buf, placed, rgb, under=(PAPER, GRAIN, FLECK), share=0.9):
        """Whether the placed glyphs are on buf in rgb: each pixel they ink is the engine's blend of rgb over one of the
        under colours, for share of them at least (a stroke drawn over a glyph takes the rest)."""
        hit = n = 0
        for k, left, top in placed:
            _, _, _, w, h, a = self.glyph(k)
            for i, v in enumerate(a):
                if v:
                    n += 1
                    hit += buf[(top + i // w) * 375 + left + i % w] in {blend(b, rgb, v) for b in under}
        return n > 0 and hit >= share * n


def blend(dst, rgb, a):
    """game_sudoku.c's mix of rgb over dst at a of 15, as its stamp scales it; solid is rgb."""
    a = a * 17 + (a >> 3)
    if a >= 256:
        return rgb
    rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff
    return rb | (((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00)


def draw(g, canvas=None):
    """The whole sheet painted: its 375 x 320 pixels, 0xRRGGBB."""
    canvas = canvas or Canvas(g.fw)
    buf = screen(canvas, origin=(0, 0))
    g.e['eng_paint'](canvas.p)
    return [v & 0xffffff for v in buf]


def box(i):
    return GX + i % 9 * CS, GY + i // 9 * CS


def centre2(i):
    x, y = box(i)
    return 2 * x + CS, 2 * y + CS


def covers(rects, x, y, w, h):
    return any(rx <= x and ry <= y and x + w <= rx + rw and y + h <= ry + rh for rx, ry, rw, rh in rects)


def reddish(v):
    return v >> 16 > 150 and v >> 8 & 255 < 90 and v & 255 < 90


def test_a_fresh_game_opens_on_the_levels_and_rests(fw):
    g = Game(fw)
    s = g.s
    assert g.game.name == b'Evening Puzzle' and g.game.flags == 6 and g.game.size == C.sizeof(Sudoku) <= 24 * 1024  # ENG_STILL, ENG_FULL; 2 KB and the masks unpacked
    assert C.sizeof(Save) == 436 and (s.mode, s.sel) == (CHOOSE, GENTLE)
    assert not g.e['eng_live']()  # the first frame painted; nothing moves: no timer
    assert fw.var('wheel_raw', C.c_int).value == 0  # ticks
    g.close()


def test_the_grid_fills_the_height_in_33_px_cells_ruled_as_print(fw):
    g = Game(fw)
    g.puzzle(half(SOLVED, set(range(81))), SOLVED, cur=80)
    px = draw(g)
    at = lambda x, y: px[y * 375 + x]
    for k in range(10):  # the boxes' 2 px rules, the cells' 1 px
        x, y = GX + k * CS, GY + k * CS
        assert (at(x - 1, 100), at(x, 100)) == ((INK, INK) if k % 3 == 0 else (at(x - 1, 100), RULE)), k
        assert (at(100, y - 1), at(100, y)) == ((INK, INK) if k % 3 == 0 else (at(100, y - 1), RULE)), k
    assert at(GX - 2, 100) in (PAPER, GRAIN, FLECK) and at(GX + 9 * CS + 1, 100) in (PAPER, GRAIN, FLECK)
    assert GY - 1 == 11 and GY + 9 * CS + 1 == 310  # 299 of the 320 rows
    paper = [at(x, y) for y in range(320) for x in range(0, 30)]
    assert {PAPER, GRAIN, FLECK} >= set(paper[:30 * 60]) and paper.count(GRAIN) + paper.count(FLECK) > 10  # grained
    assert draw(g) == px  # the grain is the same each paint
    g.close()


@pytest.mark.parametrize('level', [GENTLE, STEADY, SHARP])
def test_200_puzzles_a_level_each_have_one_solution_and_need_their_levels_technique(fw, level):
    """An independent solver counts each puzzle's solutions, and singles alone grade it: Gentle by naked singles,
    Steady needing hidden ones too, Sharp needing more than singles (unless the generator ran out of fresh grids, a
    rare case counted here, as is one that ran past the work after which no fresh grid is tried). Each is symmetric, its clues its solution's, and made within 3 s of 150-step slices."""
    g = Game(fw)
    rnd, slices, kept, clues = random.Random(level), [], 0, []
    for k in range(200):
        n = g.make(level, rnd.getrandbits(32) | 1, WORK)
        clue, sol = list(g.s.puz), list(g.s.gsol)
        assert whole(sol) and all(c in (0, d) for c, d in zip(clue, sol))
        assert all(bool(clue[i]) == bool(clue[80 - i]) for i in range(81)), k  # removed in symmetric pairs
        assert count(clue) == 1, k
        n_clues = sum(map(bool, clue))
        assert n_clues >= TARGET[level] - 1  # a pair at a time: one under where the centre went last
        if level == GENTLE:
            assert singles(clue, False)
        elif level == STEADY:
            assert singles(clue, True) and (not singles(clue, False) or g.s.gtries == TRIES or g.s.gwork >= LATE)
            kept += singles(clue, False)
        else:
            assert not singles(clue, True) or g.s.gtries == TRIES or g.s.gwork >= LATE
            kept += singles(clue, True)
        slices.append(n)
        clues.append(n_clues)
    assert max(slices) <= 180, max(slices)  # 3 s of steps
    assert kept <= 8  # kept at a limit, though singles solve it: 4 % at most (7 of these 200 measured, Sharp)
    g.close()


@pytest.mark.parametrize('level', [GENTLE, STEADY, SHARP])
def test_a_sliced_generator_makes_the_same_puzzle_as_one_unsliced(fw, level):
    g = Game(fw)
    for seed in (1, 77, 0xdeadbeef):
        g.make(level, seed)
        whole_run = (bytes(g.s.puz), bytes(g.s.gsol))
        for budget in (WORK, 997, 1):
            g.make(level, seed, budget)
            assert (bytes(g.s.puz), bytes(g.s.gsol)) == whole_run, (seed, budget)
    g.close()


def test_a_slice_overruns_its_work_by_one_piece_at_most(fw):
    """gen_run stops once its work is spent; the last piece (a cell scan, one pass of singles) may take it under 0,
    by a pass of singles at most: 160 for its cells and 20 a unit for its hidden singles."""
    g = Game(fw)
    worst = 0
    for level in (GENTLE, STEADY, SHARP):
        g.s.glevel, g.s.gtries, g.s.gwork, g.s.rng = level, 0, 0, 99
        fw.fn('game_sudoku__gen_fill', None, C.c_void_p)(C.addressof(g.s))
        while g.s.gphase != G_DONE:
            left = g.fn['gen_run'](C.addressof(g.s), WORK)
            if g.s.gphase != G_DONE:
                assert -(160 + 27 * 20) <= left <= 0
                worst = min(worst, left)
    assert worst < -50  # it does overrun, a little
    g.close()


def test_centre_on_a_level_makes_a_puzzle_over_steps_with_a_progress_rule_then_plays_it(fw):
    g = Game(fw)
    s, art = g.s, Art(fw)
    g.key(NEXT)
    rects = g.frame()
    assert s.sel == STEADY and rects == [(30, 62, 316, 158)]
    g.key(NEXT), g.key(NEXT)
    g.frame()
    assert s.sel == SHARP  # held at the last
    g.key(CENTER)
    g.frame()
    assert s.mode == MAKE and g.e['eng_live']()
    rule = (29, 68, 6, 184)
    frames, shown = 0, False
    while s.mode == MAKE:
        rects = g.frame()
        frames += 1
        assert rects in ([rule], [(0, 0, 375, 320)]) and frames <= 180
        if s.mode == MAKE and s.gphase == G_TEST and s.gpair > 10 and not shown:  # the rule: the pairs tried, of 41
            px = draw(g)
            top = 250 - 180 * s.gpair // 41
            assert px[249 * 375 + 31] == px[top * 375 + 31] == INK and px[(top - 1) * 375 + 31] == px[70 * 375 + 31] == TRACK
            assert art.written(px, art.say('SETTING THE PUZZLE', 'A_CAPS', 187, 156, 1), INK, (STRIP,))
            assert not any(px[y * 375 + x] == INK for y in range(GY + 1, GY + 30) for x in range(GX + 2, GX + 30))  # no clues yet
            shown = True
    assert shown
    assert s.mode == PLAYING and s.v.level == SHARP and s.v.cur == 40 and s.v.elapsed <= 1 and s.v.slips == 0
    assert bytes(s.v.clue) == bytes(s.puz) and bytes(s.v.sol) == bytes(s.gsol) and not any(s.v.ent)
    g.frame()
    assert not g.e['eng_live']()  # at rest on the board
    g.close()


def test_the_wheel_walks_the_cells_in_reading_order_fast_for_long_travel_and_prev_next_jump_a_box(made):
    g, v = made, made.s.v
    g.step(ticks=1)
    assert v.cur == 41
    g.step(ticks=2, fast=30)
    assert v.cur == 71  # fast: the wheel's speed
    g.step(ticks=1, fast=15)
    assert v.cur == 5  # wraps
    g.step(ticks=-1, fast=-6)
    assert v.cur == 80
    v.cur = 4  # box 1, its middle top
    g.press(E_NEXT)
    assert v.cur == 7
    g.press(E_NEXT)
    assert v.cur == 28  # box 3, the next band
    g.press(E_PREV), g.press(E_PREV), g.press(E_PREV)
    assert v.cur == 1  # back through boxes 2 and 1 to 0, its place kept


def legal(g, i):
    clues = {g.s.v.clue[p] for p in PEERS[i]} - {0}
    return [d for d in range(1, 10) if d not in clues]


def test_the_dial_offers_exactly_the_digits_the_cells_clues_leave(made):
    g, s = made, made.s
    rnd = random.Random(5)
    blanks = [i for i in range(81) if not s.v.clue[i]]
    for i in rnd.sample(blanks, 25):
        s.v.cur = i
        g.press(E_CENTRE)
        assert s.dial
        seen = [s.pick]
        for _ in range(12):
            g.step(ticks=1)
            seen.append(s.pick)
        assert sorted(set(seen)) == legal(g, i)
        back = [s.pick]
        for _ in range(len(legal(g, i))):
            g.step(ticks=-1)
            back.append(s.pick)
        assert back[-1] == back[0] and sorted(set(back)) == legal(g, i)
        clued = g.fn['seen'](C.addressof(s), i, 1)
        assert [d for d in range(1, 10) if not clued >> (d - 1) & 1] == legal(g, i)
        g.step(ticks=3)
        assert s.pick in legal(g, i)
        g.press(E_CENTRE)
        assert not s.dial and s.v.ent[i] == s.pick
    clue = next(i for i in range(81) if s.v.clue[i])
    s.v.cur = clue
    g.press(E_CENTRE)
    assert not s.dial  # a clue takes no digit


def test_centre_places_the_digit_with_a_tap_and_the_dial_opens_at_the_cells_entry_or_the_last_digit(made):
    g, s = made, made.s
    i, j = [k for k in range(81) if not s.v.clue[k]][:2]
    s.v.cur = i
    g.sounds.clear()
    g.press(E_CENTRE)
    first = s.pick
    assert first == legal(g, i)[0]
    g.step(ticks=1)
    want = s.pick
    g.step(held=E_CENTRE, pressed=E_CENTRE)
    assert s.v.ent[i] == want and s.wash == 1  # the new digit's twins wait a step: not on the note's frame
    g.step(released=E_CENTRE)
    assert s.wash == 2 and s.v.ent[i] == want and s.last == want and not s.dial and g.sounds[-1] in (TAP, ALERT)
    g.press(E_CENTRE)
    assert s.pick == want  # its entry
    g.press(E_CENTRE)
    assert s.v.ent[i] == want  # the same digit: no change
    s.v.cur = j
    g.press(E_CENTRE)
    assert s.pick == (want if want in legal(g, j) else legal(g, j)[0])


def test_the_dial_sits_on_a_note_in_the_4x4_block_sharing_neither_the_cursors_row_nor_column(fw):
    g = Game(fw)
    g.puzzle([0] * 81, SOLVED)
    s = g.s
    for i in range(81):
        s.v.cur = i
        g.press(E_CENTRE)
        assert s.dial and (s.sx, s.sy) == (106 if i % 9 >= 4 else 269, 78 if i // 9 >= 4 else 242)
        x, y = box(i)
        assert s.sx + NOTE + 3 <= x or x + CS <= s.sx - NOTE, i  # clear of the cursor's column, shadow and all
        assert s.sy + NOTE + 4 <= y or y + CS <= s.sy - NOTE, i  # and its row
        assert 0 <= s.sx - NOTE and s.sx + NOTE + 3 <= 375 and 0 <= s.sy - NOTE and s.sy + NOTE + 4 <= 320
        g.press(E_NEXT)
    g.close()


def test_play_on_the_dial_takes_the_pencil_and_centre_then_notes_a_digit_drawn_small_in_its_ninth(made):
    g, s = made, made.s
    art = Art(g.fw)
    i = next(k for k in range(81) if not s.v.clue[k] and len(legal(g, k)) >= 2)
    s.v.cur = i
    g.press(E_CENTRE)
    a = s.pick
    g.press(E_PLAY)
    assert s.dial and s.pencil and not s.v.note[i]  # pencil: nothing written yet
    g.press(E_CENTRE)
    g.step(ticks=1)
    b = s.pick
    g.press(E_CENTRE)
    assert s.v.note[i] == (1 << (a - 1)) | (1 << (b - 1)) and s.dial and not s.v.ent[i]
    g.press(E_CENTRE)
    assert s.v.note[i] == 1 << (a - 1)
    g.press(E_NEXT)
    assert not s.dial and not s.v.ent[i] and s.v.cur == i  # Previous or Next shuts the dial, nothing placed
    g.press(E_CENTRE)
    assert s.pencil  # the pencil kept until Play again
    g.press(E_PLAY)
    assert not s.pencil and s.dial
    g.press(E_NEXT)
    g.step()
    assert s.key[i] >> 11 == s.v.note[i] and s.key[i] >> 6 & 15 == 0  # its look: the notes, no digit
    x, y = box(i)
    k = a - 1
    px = draw(g)
    assert art.written(px, art.mid(art.id['A_HAND'] + a, 2 * x + 22 * (k % 3) + 12, 2 * y + 22 * (k // 3) + 12), PENCIL,
                       (CURSOR,))  # on the cursor


def test_a_digit_placed_leaves_the_notes_of_its_row_column_and_box(fw):
    g = Game(fw)
    g.puzzle(half(SOLVED, set(range(81))), SOLVED, cur=40)
    s = g.s
    d = SOLVED[40]
    s.v.note[:] = [0x1ff] * 81
    g.press(E_CENTRE)
    while s.pick != d:
        g.step(ticks=1)
    g.press(E_CENTRE)
    assert s.v.ent[40] == d
    for j in range(81):
        assert bool(s.v.note[j] >> (d - 1) & 1) == (j not in PEERS[40] and j != 40), j
        assert s.v.note[j] | 1 << (d - 1) == 0x1ff
    g.close()


def test_play_with_the_dial_shut_clears_the_entry_then_the_notes_and_the_levels_keep_it_unfinished(made):
    g, s = made, made.s
    i = next(k for k in range(81) if not s.v.clue[k])
    s.v.cur, s.v.ent[i], s.v.note[i] = i, 4, 0b101
    g.step(held=E_PLAY, pressed=E_PLAY)  # at the press
    assert s.v.ent[i] == 0 and s.v.note[i] == 0b101 and s.mode == PLAYING
    g.step(released=E_PLAY)
    g.press(E_PLAY)
    assert s.v.note[i] == 0
    puzzle = bytes(s.v)
    g.e['eng_hold'](), g.e['eng_close']()  # Quit, and the game again: the levels, the puzzle kept
    g.e['eng_open'](g.game, PAGE, SHEET, 320, g.done, g.done)
    g.s = s = Sudoku.from_address(g.fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
    assert s.mode == CHOOSE and bytes(s.v)[4:-20] == puzzle[4:-20]  # (but its magic, its time and its seal)
    art = Art(g.fw)
    px = draw(g)
    assert art.written(px, art.say('CENTER: START      PLAY: CONTINUE THE UNFINISHED ONE', 'A_CAPS', 187, 296, 1), SOFT)
    assert art.written(px, art.say('UNFINISHED', 'A_CAPS', 96, 249), SOFT)
    g.press(E_PLAY)
    assert s.mode == PLAYING
    s.v.cur = next(k for k in range(81) if s.v.clue[k])
    clue = s.v.clue[s.v.cur]
    g.press(E_PLAY)
    assert s.v.clue[s.v.cur] == clue  # a clue stays


def test_conflicts_show_the_slip_red_circled_and_what_it_clashes_with_underlined(fw):
    g = Game(fw)
    gaps = {0, 1, 2, 9, 10, 11, 18, 19, 20, 27, 36, 45, 54, 60}
    g.puzzle(half(SOLVED, gaps), SOLVED, cur=80)
    s, art = g.s, Art(fw)

    def red_cells():
        g.step()
        return {i for i in range(81) if s.key[i] >> 4 & 3 == K_CLASH}
    s.v.ent[0], s.v.ent[2] = 2, 2  # a row (no clue of cell 0's is a 2)
    assert red_cells() == {0, 2}
    s.v.ent[2], s.v.ent[27] = 0, 2  # a column
    assert red_cells() == {0, 27}
    s.v.ent[27], s.v.ent[19] = 0, 2  # a box
    assert red_cells() == {0, 19}
    s.v.ent[19] = 0
    assert red_cells() == set() and not any(k >> 10 & 1 for k in s.key)
    s.v.ent[0] = 9
    assert red_cells() == {0} and s.key[8] >> 4 & 3 == K_CLUE
    under = [i for i in range(81) if s.key[i] >> 10 & 1]
    assert under and under == sorted(i for i in PEERS[0] if s.v.clue[i] == 9)
    px = draw(g)
    x2, y2 = centre2(0)
    assert art.written(px, art.mid(art.id['A_PEN'] + 9, x2 - 1, y2 - 1), RED)  # the hand wanders: cell 0's is -1, -1
    x, y = box(0)
    assert reddish(px[(y + 16) * 375 + x + 30])  # its circle, 13.9 px round
    ux, uy = box(under[0])
    assert reddish(px[(uy + 28) * 375 + ux + 16])  # the clue's underline
    g.sounds.clear()
    s.v.ent[0], s.v.ent[1], s.v.cur = 2, 0, 1
    g.press(E_CENTRE)
    assert legal(g, 1) == [2] and s.pick == 2  # 2 is cell 1's alone by its clues, and cell 0 holds one
    g.press(E_CENTRE)
    assert g.sounds == [ALERT] and s.v.ent[1] == 2 and s.v.slips == 1  # placing a clash beeps, a slip counted
    assert s.slip == 2 and red_cells() == {1} and s.key[0] >> 10 & 1  # the slip red, the entry it clashes with underlined
    px = draw(g)
    ux, uy = box(0)
    assert reddish(px[(uy + 28) * 375 + ux + 16]) and s.key[0] >> 4 & 3 == K_ENTRY  # in pen, its underline red
    assert art.written(px, art.say('1 slip', 'A_HAND', 356, 256, 1), RED)
    s.v.slips = 12
    assert art.written(draw(g), art.say('12 slips', 'A_HAND', 356, 256, 1), RED)
    g.close()


def test_the_board_is_drawn_in_its_inks_and_washes(fw):
    g = Game(fw)
    g.puzzle(half(SOLVED, {0, 1, 40, 41}), SOLVED, cur=40)
    s, art = g.s, Art(fw)
    s.v.ent[41] = SOLVED[41]
    g.step(), g.step()
    px = draw(g)
    at = lambda i, dx=5, dy=4: px[(box(i)[1] + dy) * 375 + box(i)[0] + dx]
    assert at(40) == CURSOR  # the cursor's cell
    assert px[(box(40)[1] - 1) * 375 + box(40)[0] + 10] == PEN  # its pen box
    peers = sorted(PEERS[40])
    assert all(at(i) == PEER for i in peers) and at(0) != PEER and at(80) != PEER  # its row, column and box
    x2, y2 = centre2(41)
    assert art.written(px, art.mid(art.id['A_PEN'] + SOLVED[41], x2 + 41 * 7 % 3 - 1, y2 + 41 * 5 % 3 - 1), PEN, (PEER,))  # an entry
    x2, y2 = centre2(2)
    assert art.written(px, art.mid(art.id['A_PRINT'] + SOLVED[2] - 1, x2, y2 + 1), INK)  # a clue, printed
    s.v.cur = 2  # a clue: every other cell holding its digit takes the blue highlighter
    g.step(), g.step(), g.step()
    px = draw(g)
    like = [i for i in range(81) if i != 2 and (s.v.clue[i] or s.v.ent[i]) == SOLVED[2]]
    assert like and all(at(i, 6, 4) == TWIN for i in like)
    assert all(at(i, 6, 4) in (PAPER, GRAIN, FLECK, PEER) for i in range(81) if i != 2 and i not in like)
    g.close()


def test_a_cursor_move_takes_the_old_wash_off_and_puts_the_new_one_on_a_stage_a_frame(fw):
    g = Game(fw)
    g.puzzle(half(SOLVED, set(range(27, 81))), SOLVED, cur=0)  # the first three rows printed
    s = g.s
    g.frame(), g.frame()
    assert s.wash == 2 and s.wcur == 0 and g.frame() == []
    twins = lambda c: [i for i in range(81) if i != c and SOLVED[i] == SOLVED[c] and s.v.clue[i]]
    assert twins(0) and twins(1) and all(s.key[i] & B_TWIN for i in twins(0))
    g.key(NEXT)
    first = g.frame()  # the cursor moved; the old twins off
    assert s.v.cur == 1 and s.wash == 1 and s.wcur == 0 and g.e['eng_live']()
    assert not any(s.key[i] & B_TWIN for i in twins(0)) and all(s.key[i] & B_PEER for i in PEERS[0])
    assert all(covers(first, box(i)[0], box(i)[1], CS, CS) for i in twins(0) + [0, 1])
    second = g.frame()  # the old peers off
    assert s.wash == 0 and not any(s.key[i] & B_PEER for i in range(81))
    assert all(covers(second, box(i)[0], box(i)[1], CS, CS) for i in PEERS[0] - {1})
    assert len(second) <= 8 and all(GX - 2 <= x and x + w <= GX + 9 * CS + 3 for x, _, w, _ in second)
    third = g.frame()  # the new peers on
    assert s.wash == 1 and s.wcur == 1 and all(s.key[i] & B_PEER for i in PEERS[1])
    assert all(covers(third, box(i)[0], box(i)[1], CS, CS) for i in PEERS[1] - {0})
    assert not any(s.key[i] & B_TWIN for i in twins(1))
    fourth = g.frame()  # and their twins
    assert s.wash == 2 and all(s.key[i] & B_TWIN for i in twins(1))
    assert all(covers(fourth, box(i)[0], box(i)[1], CS, CS) for i in twins(1)) and len(fourth) == len(twins(1))
    assert g.frame() == [] and not g.e['eng_live']()  # at rest, nothing
    s.v.ent[:] = [0] * 81
    s.v.clue[:] = half(SOLVED, set(range(81)) - {0, 1, 2, 9})
    s.v.cur = 0
    for _ in range(5):
        g.step()
    g.page.now += 16
    g.e['frame'](None)
    g.key(NEXT), g.frame()  # 0 to 1: its twins off, then its peers: row 0's wash, then column 0's a row at a time
    rects = g.frame()  # nine rows, the last two joined as the engine joins past eight
    assert (GX - 2, GY - 2, 9 * CS + 5, CS + 5) in rects and len(rects) == 8
    assert rects[7] == (GX - 2, GY + 7 * CS - 2, CS + 5, 2 * CS + 5)
    g.close()


def test_the_wash_waits_while_the_dial_is_open_and_on_the_frame_that_puts_it_away(made):
    g, s = made, made.s
    s.v.cur = next(k for k in range(81) if not s.v.clue[k] and k % 9 < 8 and not s.v.clue[k + 1])
    for _ in range(5):
        g.step()
    g.step(ticks=1)
    assert s.wash == 1  # moved: the old twins off
    assert g.step(pressed=E_CENTRE, held=E_CENTRE) == 0  # the dial opened: the wash waits, and nothing moves
    assert s.dial and s.wash == 1
    assert g.step(ticks=1) == 0 and g.step(released=E_CENTRE) == 0 and s.wash == 1  # turned: still waiting
    g.step(pressed=E_NEXT, held=E_NEXT)  # put away, nothing placed: that frame is the note's
    assert not s.dial and s.wash == 1
    assert g.step() == 1 and s.wash == 0  # then the old peers off
    assert g.step() == 1 and s.wash == 1 and g.step() == 0 and s.wash == 2  # and the new on


def test_the_dial_turning_repaints_the_hub_and_two_digits_and_opening_and_shutting_its_note(made):
    g, s = made, made.s
    s.v.cur = next(k for k in range(81) if not s.v.clue[k] and len(legal(g, k)) >= 2)
    g.step(), g.step()
    g.page.now += 16
    g.e['frame'](None)  # the cursor's move painted
    g.key(CENTER)
    rects = g.frame()
    note = (s.sx - NOTE - 1, s.sy - NOTE - 1, 2 * NOTE + 6, 2 * NOTE + 7)
    assert note in rects
    was = s.pick
    g.key(NEXT)
    rects = [r for r in g.frame() if r != MARGIN]  # a second may pass
    hub = (s.sx - 17, s.sy + 3 - 17, 35, 42)
    spot = lambda d: (s.sx + DIAL_X[d - 1] - 14, s.sy + 3 + DIAL_Y[d - 1] - 14, 29, 29)
    assert sorted(rects) == sorted([hub, spot(was), hub, spot(s.pick)])
    g.key(CENTER)
    rects = g.frame()
    assert note in rects


def test_the_dial_strikes_the_ruled_out_digits_and_rings_the_noted(fw):
    g = Game(fw)
    g.puzzle(half(SOLVED, {40}), SOLVED, cur=40)
    s, art = g.s, Art(fw)
    s.v.note[40] = 1 << (SOLVED[40] - 1)
    g.press(E_CENTRE)
    assert s.pick == SOLVED[40] and legal(g, 40) == [SOLVED[40]]
    px = draw(g)
    cx, cy = s.sx, s.sy + 3
    for d in range(1, 10):
        x, y = cx + DIAL_X[d - 1], cy + DIAL_Y[d - 1]
        pick = d == s.pick  # on the highlighter's disc; the rest on the note, struck across
        assert art.written(px, art.mid(art.id['A_DIAL'] + d - 1, 2 * x + 1, 2 * y + 1), PEN if pick else 0xa9a08c,
                           (CURSOR if pick else NOTE_Y,), 0.9 if pick else 0.6), d
    x, y = cx + DIAL_X[s.pick - 1], cy + DIAL_Y[s.pick - 1]
    assert art.written(px, art.mid(art.id['A_PEN'] + s.pick, 2 * cx + 1, 2 * cy + 1), PEN, (NOTE_Y,))  # the hub
    assert px[(cy - 55) * 375 + cx] != NOTE_Y and px[(cy - 41) * 375 + cx + 20] == NOTE_Y  # the outer pen ring
    g.close()


def lit_outside_glass(px):
    """Pixels the glass's corners hide that are not newsprint: ink, beyond a pixel past the corner's edge (the grid's
    corners reach 0.1 px under it), and not the coffee ring, a stain that runs under it."""
    out = []
    for y in range(320):
        for x in (*range(0, 80), *range(295, 375)):
            if px[y * 375 + x] not in (PAPER, GRAIN, FLECK) and abs(math.hypot(x + .5 - 322, y + .5 - 292) - 24) > 3:
                ox, oy = (80 - x - .5 if x < 80 else x + .5 - 295), (80 - y - .5 if y < 80 else y + .5 - 240)
                if ox > 0 and oy > 0 and math.hypot(ox, oy) > 81:
                    out.append((x, y))
    return out


def test_nothing_inked_falls_under_the_glass_in_any_screen(fw):
    g = Game(fw)
    s = g.s
    s.v.best[:] = [3600 * 60 * 12, 61 * 60, 5]
    assert lit_outside_glass(draw(g)) == []  # the levels
    g.puzzle([0] * 81, SOLVED)
    s.v.note[:] = [0x1ff] * 81
    for i in (0, 8, 72, 80):
        s.v.cur = i
        g.step(), g.step()
        assert lit_outside_glass(draw(g)) == []
        g.press(E_CENTRE)
        assert lit_outside_glass(draw(g)) == []  # the dial open for a corner cell
        g.press(E_NEXT)
    g.puzzle(SOLVED[:80] + [0], SOLVED, cur=80)
    g.press(E_CENTRE)
    while s.pick != SOLVED[80]:
        g.step(ticks=1)
    g.press(E_CENTRE)
    assert s.mode == DONE
    assert lit_outside_glass(draw(g)) == []
    g.close()


def test_finishing_stamps_the_page_and_notes_the_time_and_the_levels_best_and_saves_them(fw):
    g = Game(fw)
    s, art = g.s, Art(fw)
    g.puzzle(SOLVED[:80] + [0], SOLVED, cur=80, level=STEADY)
    s.v.elapsed = 60 * 125  # 2:05
    g.sounds.clear()
    g.press(E_CENTRE)
    while s.pick != SOLVED[80]:
        g.step(ticks=1)
    g.press(E_CENTRE)
    assert s.mode == DONE and s.v.done and s.record and s.v.best[STEADY] == s.v.elapsed < 60 * 126 and g.sounds == [TAP]
    raw = g.page.card.files[FILE]
    assert len(raw) == 436 and raw[:4] == b'SDK1' and int.from_bytes(raw[-4:], 'little') == fnv1a(raw[:-4])
    px = draw(g)
    pen = art.id['A_PEN']
    time = []
    x = 121
    for ch in '2:05':
        k = pen + '0123456789:'.index(ch)
        gx, gy, adv, *_ = art.glyph(k)
        time.append((k, x + gx, 231 + gy))
        x += adv
    assert art.written(px, time, PEN, (NOTE_Y,))  # on the result's sticky note
    assert art.written(px, art.say('new best!', 'A_HAND', 255, 222, 2), RED, (NOTE_Y,))
    assert art.written(px, art.say('Steady', 'A_HAND', 255, 238, 2), PENCIL, (NOTE_Y,))
    assert art.written(px, art.say('CENTER: A NEW PUZZLE', 'A_CAPS', 187, 258, 1), INK, (NOTE_Y,))
    stamp = art.glyph(art.id['A_STAMP'])
    assert art.written(px, [(art.id['A_STAMP'], 187 - stamp[3] // 2, 132 - stamp[4] // 2)], RED)
    for _ in range(12):
        g.step()
    assert g.sounds == [TAP, TAP, TAP]  # three taps, six steps apart
    assert g.step() == 0  # then nothing moves
    g.press(E_CENTRE)
    assert s.mode == CHOOSE and s.sel == STEADY
    g.puzzle(SOLVED[:80] + [0], SOLVED, cur=80, level=STEADY)
    s.v.elapsed = 60 * 200
    s.pick, s.dial = SOLVED[80], 1
    g.press(E_CENTRE)
    assert s.mode == DONE and not s.record and s.v.best[STEADY] < 60 * 126
    assert art.written(draw(g), art.say('best 2:05', 'A_HAND', 255, 222, 2), PEN, (NOTE_Y,))
    g.close()


def test_a_unit_filled_takes_the_green_wave_a_cell_each_three_steps_never_checked_against_the_solution(fw):
    g = Game(fw)
    wrong = [2, 1, 3, 4, 5, 6, 7, 8, 9]  # row 0, its first two swapped: nine digits, none twice, not the solution's
    assert SOLVED[:9] == list(range(1, 10))
    g.puzzle([0] * 81, SOLVED, cur=8)
    s = g.s
    for k in range(8):
        s.v.ent[k] = wrong[k]
    g.press(E_CENTRE)
    while s.pick != wrong[8]:
        g.step(ticks=1)
    g.press(E_CENTRE)
    assert s.mode == PLAYING and s.wave == 1 and s.wt == 2  # row 0, unit 0, two steps on (the press and the release)
    waved = lambda: [i for i in range(81) if s.key[i] & B_WAVE]
    assert waved() == [0]
    for _ in range(3):
        assert g.step() == 1
    assert waved() == [0, 1]
    while s.wave:
        g.step()
    assert s.wt == 9 * 3 + 13 and waved() == [] and g.step() == 0
    g.close()


def test_the_margin_repaints_when_its_second_tally_or_slips_change(made):
    g, s = made, made.s
    art = Art(g.fw)
    g.frame()
    s.v.elapsed = 60 * 754
    g.key(NEXT)
    assert covers(g.frame(), *MARGIN)  # 12:34, the rows' rects before it
    px = draw(g)
    assert art.written(px, art.say('12:34', 'A_HAND', 356, 218, 1), PEN)
    assert art.written(px, art.say('GENTLE', 'A_CAPS', 356, 235, 1), SOFT)
    g.key(NEXT)
    assert not covers(g.frame(), *MARGIN)  # the same second
    s.v.ent[:] = [0 if s.v.clue[i] or s.v.sol[i] != 5 else 5 for i in range(81)]
    g.key(NEXT)
    assert covers(g.frame(), *MARGIN) and g.fn['tally'](C.addressof(s)) == 1 << 4  # every 5: struck
    g.close()


def test_a_pause_lays_the_blotter_over_the_grid_and_play_lifts_it(made):
    g, s = made, made.s
    art = Art(g.fw)
    s.v.elapsed = 60 * 754
    g.e['eng_hold']()
    assert s.held
    px = draw(g)
    assert px[(GY + 1) * 375 + GX + 1] == BLOT and art.written(px, art.say('12:34 so far', 'A_HAND', 187, 250, 1), CURSOR,
                                                                 (BLOT_IN,))
    assert all(px[y * 375 + x] not in (INK, PEN) for y in range(GY + 8, GY + 9 * CS - 8, 5) for x in range(GX + 8, GX + 9 * CS - 8, 5)
               if not 230 <= y <= 256)  # not a digit shows
    g.e['eng_run']()
    rects = g.frame()
    assert not s.held and rects == [(0, 0, 375, 320)]  # the whole sheet, and nothing over it
    s.mode = CHOOSE
    g.e['eng_hold']()
    assert not s.held  # the levels have no board to hide


@pytest.mark.parametrize('steps, text', [(0, '0:00'), (59, '0:00'), (60, '0:01'), (60 * 599, '9:59'), (60 * 600, '10:00'),
                                         (60 * 3599, '59:59'), (60 * 3600, '60:00'), (60 * 36061, '601:01'),
                                         (TIME_MAX, '5940:00')])
def test_a_time_reads_as_minutes_and_seconds(fw, steps, text):
    g = Game(fw)
    out = C.create_string_buffer(24)
    g.fn['clock'](out, steps)
    assert out.value.decode() == text
    g.close()


def test_time_counts_the_clock_between_steps_a_rest_five_minutes_at_most_and_stops_at_99_hours(made):
    g, s = made, made.s
    s.v.elapsed = s.owed = 0
    g.step()
    g.step(rested=10_000)  # ten seconds at rest: 600 steps
    assert s.v.elapsed == 600
    g.step(rested=51)  # 3.06 steps: the part is kept for the next
    assert s.v.elapsed == 603 and s.owed == 60
    g.step(rested=REST_MS * 4)  # a rest the screen may have gone dark in counts five minutes
    assert s.v.elapsed == 603 + REST_MS * 60 // 1000
    s.v.elapsed = TIME_MAX - 1
    g.step(rested=100)
    assert s.v.elapsed == TIME_MAX
    s.mode = CHOOSE
    g.step(rested=100)
    assert s.v.elapsed == TIME_MAX
    s.v.elapsed, s.mode = 0, PLAYING
    g.step(rested=5_000)  # the first step of play after the levels: no time before it counts
    assert s.v.elapsed == 0
    g.frame()  # through the engine: ten seconds at rest, then a key
    g.page.now += 10_000
    g.key(NEXT)
    g.frame()
    assert 600 <= s.v.elapsed <= 602
    g.e['eng_hold']()  # a pause: its time does not count
    g.page.now += 60_000
    g.e['eng_run']()
    g.frame()
    assert 600 <= s.v.elapsed <= 603


def test_touch_moves_the_cursor_opens_the_dial_places_a_digit_and_a_tap_off_the_note_shuts_it(made):
    g, s = made, made.s
    i = next(k for k in range(81) if not s.v.clue[k] and len(legal(g, k)) < 9)
    x, y = box(i)
    g.press(E_TOUCH, tx=x + 16, ty=y + 16)
    assert s.v.cur == i and s.dial
    spot = lambda d: dict(tx=s.sx + DIAL_X[d - 1], ty=s.sy + 3 + DIAL_Y[d - 1])
    d = legal(g, i)[-1]
    bad = [k for k in range(1, 10) if k not in legal(g, i)][0]
    g.press(E_TOUCH, **spot(bad))
    assert s.dial and not s.v.ent[i]  # an impossible digit takes no tap
    g.press(E_TOUCH, tx=s.sx, ty=s.sy + 3)
    assert s.dial  # the hub: nothing
    g.press(E_TOUCH, tx=s.sx + NOTE - 1, ty=s.sy - NOTE)
    assert s.dial  # the note's corner, still on it
    g.press(E_TOUCH, **spot(d))
    assert not s.dial and s.v.ent[i] == d
    g.press(E_TOUCH, tx=x + 16, ty=y + 16)
    assert s.dial
    g.press(E_TOUCH, tx=s.sx + NOTE, ty=s.sy)
    assert not s.dial and s.v.ent[i] == d and s.v.cur == i  # just off the note: shut, the cursor stays
    clue = next(k for k in range(81) if s.v.clue[k])
    g.press(E_TOUCH, tx=box(clue)[0] + 3, ty=box(clue)[1] + 3)
    assert s.v.cur == clue and not s.dial
    g.press(E_TOUCH, tx=2, ty=2)
    assert s.v.cur == clue  # off the board: nothing
    s.mode = CHOOSE
    g.press(E_TOUCH, tx=187, ty=30)
    assert s.mode == CHOOSE  # the heading is no level
    g.press(E_TOUCH, tx=38 + 2 * CARD + 40, ty=150)
    assert s.mode == MAKE and s.sel == SHARP and s.glevel == SHARP


def blob(v):
    raw = bytearray(bytes(v))
    raw[:4] = MAGIC.to_bytes(4, 'little')
    raw[-4:] = fnv1a(bytes(raw[:-4])).to_bytes(4, 'little')
    return bytes(raw)


def a_save(**kw):
    v = Save()
    v.clue[:] = half(SOLVED, set(range(0, 81, 3)))
    v.sol[:] = SOLVED
    v.ent[3], v.note[6], v.cur, v.level, v.elapsed = SOLVED[3], 0b11, 6, SHARP, 4321
    v.best[:] = [100, 0, 7000]
    for k, val in kw.items():
        if '.' in k:
            name, at = k.split('.')
            getattr(v, name)[int(at)] = val
        else:
            setattr(v, k, val)
    return v


def test_a_save_comes_back_with_its_entries_notes_cursor_time_and_bests(fw):
    g = Game(fw, files={FILE: blob(a_save())})
    s = g.s
    assert s.mode == CHOOSE and s.v.cur == 6 and s.v.ent[3] == SOLVED[3] and s.v.note[6] == 0b11  # the levels first
    g.press(E_PLAY)  # the unfinished one
    assert s.mode == PLAYING and s.v.elapsed == 4321  # the first step of play starts the clock
    assert list(s.v.best) == [100, 0, 7000] and s.v.level == SHARP
    s.v.note[9] = 0b100000001
    g.e['eng_hold']()  # a pause: written
    g.close()
    again = Game(fw, files=dict(g.page.card.files))
    assert again.s.v.note[9] == 0b100000001 and again.s.v.note[6] == 0b11 and again.s.mode == CHOOSE
    again.close()


def test_a_finished_save_opens_on_the_levels(fw):
    g = Game(fw, files={FILE: blob(a_save(done=1))})
    assert g.s.mode == CHOOSE and g.s.sel == SHARP and list(g.s.v.best) == [100, 0, 7000]
    g.close()


@pytest.mark.parametrize('change', [
    dict(level=3), dict(done=2), dict(cur=81), dict(elapsed=TIME_MAX + 1), {'best.1': TIME_MAX + 1},
    {'sol.0': SOLVED[1]},  # no longer a whole grid
    {'clue.0': SOLVED[0] % 9 + 1},  # a clue against its solution
    {'ent.1': 2},  # an entry on a clue
    {'ent.0': 10}, {'note.2': 0x200},
])
def test_a_sealed_save_that_no_game_made_reads_as_none(fw, change):
    g = Game(fw, files={FILE: blob(a_save(**change))})
    assert g.s.mode == CHOOSE and not any(bytes(g.s.v)[4:-4]) and not g.fn['sane'](C.addressof(g.s))
    g.close()


@pytest.mark.parametrize('kind', ['torn', 'short', 'foreign'])
def test_a_torn_short_or_foreign_save_reads_as_none(fw, kind):
    raw = blob(a_save())
    raw = {'torn': raw[:100] + bytes([raw[100] ^ 1]) + raw[101:], 'short': raw[:300],
           'foreign': (0x314b5242).to_bytes(4, 'little') + raw[4:-4] + fnv1a((0x314b5242).to_bytes(4, 'little') + raw[4:-4]).to_bytes(4, 'little')}[kind]
    g = Game(fw, files={FILE: raw})
    assert g.s.mode == CHOOSE and not any(g.s.v.best)
    g.close()


def test_a_pause_saves_only_what_changed_and_never_an_empty_board(fw):
    g = Game(fw)
    g.e['eng_hold'](), g.e['eng_run']()
    assert FILE not in g.page.card.files  # nothing to keep
    g.close()
    g = Game(fw, files={FILE: blob(a_save())})
    del g.page.card.files[FILE]
    g.e['eng_hold']()
    assert FILE not in g.page.card.files  # the levels: nothing played
    g.e['eng_run'](), g.press(E_PLAY), g.e['eng_hold']()
    assert FILE in g.page.card.files  # a step of play: its time
    del g.page.card.files[FILE]
    g.e['eng_run'](), g.e['eng_hold']()
    assert FILE not in g.page.card.files
    g.page.card.fail = True
    fw.mock('navigator_to_with_context', lambda *_: 0)  # the toast
    g.e['eng_run']()
    g.step()
    g.e['eng_hold']()
    assert g.s.dirty == 1  # failed: tried again at the next pause
    g.page.card.fail = False
    g.e['eng_run'](), g.e['eng_hold']()
    assert FILE in g.page.card.files and g.s.dirty == 0
    g.close()


def test_the_old_games_sudoku_save_is_left_untouched(fw):
    old = bytes(range(92))
    p = Page(fw, {OLD: old})
    p.go(SUDOKU)
    s = Sudoku.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
    assert s.mode == CHOOSE
    p.key(RETURN), p.key(RETURN)  # its menu, Continue
    p.go(0)
    p.close()
    assert p.card.files[OLD] == old and {f for f in p.card.files if 'sudoku' in f} == {OLD}


def test_sudoku_is_the_engines_second_row_full_screen_with_the_status_row_at_a_pause(fw):
    moved, shown, bar = [], [], C.create_string_buffer(0x100)
    fw.mock('widget_move_resize', lambda w, x, y, ww, hh, *_: moved.append((w, x, y, ww, hh)))
    fw.mock('widget_set_visible', lambda w, on, *_: shown.append((w, on)))
    fw.var('fw_ctx_system_bar', C.c_void_p).value = C.addressof(bar)
    p = Page(fw)
    assert p.s.engines == 2 and p.s.rows == 2
    C.memmove(PAGE + O['W_X'], (C.c_int * 4)(0, 30, 375, 290), 16)  # a page under a 30 px bar
    sheet, foot = p.s.sheet, BAR  # the sheet from the status row's foot (OLED's page is full-bleed, under the row)
    moved.clear(), shown.clear()
    p.go(SUDOKU)
    assert p.s.screen == SUDOKU and p.s.full and list(p.s.box) == [0, 30, 375, 290]
    assert (PAGE, 0, 0, 375, 320) in moved and (sheet, 0, 0, 375, 320) in moved
    assert (C.addressof(bar), 0) in shown  # the status row hidden
    assert C.c_int.from_address(fw.var('engine__en', C.c_void_p).value + Eng.h.offset).value == 320
    art = Art(fw)
    canvas = Canvas(fw)
    buf = screen(canvas, origin=(0, 0))
    paint(p, canvas)
    assert art.written([v & 0xffffff for v in buf], art.mid(art.id['A_TITLE'], 375, 60), INK)  # the levels, at the top
    shown.clear()
    p.key(CENTER)  # a release: Centre on Gentle
    p.key(RETURN)
    assert p.s.paused == 2 and not api(fw)['eng_live']() and shown[-1] == (C.addressof(bar), 1)  # paused: the bar back
    p.key(RETURN)
    assert p.s.paused == 0 and shown[-1] == (C.addressof(bar), 0)
    moved.clear(), shown.clear()
    p.go(0)
    assert not p.s.full and moved[0] == (PAGE, 0, 30, 375, 290) and moved[1][0] == sheet and moved[1][3:] == (375, p.s.h)
    assert (C.addressof(bar), 1) in shown
    p.go(SUDOKU)
    shown.clear()
    p.close()
    assert shown == [(C.addressof(bar), 1)]  # the page closed under it: the bar stays
    assert not fw.var('engine__en', C.c_void_p).value and PLAY and E_CENTRE and PREV and NEXT
