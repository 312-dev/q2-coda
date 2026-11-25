"""Cabin Solitaire (patch/game_solitaire.c) on the engine: the deal, the rules, the cursor's places and the hand's
destinations against a brute-force scan, undo, draw one and three, the fan, the art file, the screens' pixels, saves,
the solver's wins replayed through the game's own moves, and the glass's corners; then through Games' page."""
import ctypes as C
import os
import pathlib
import random
import struct
import zlib

import pytest

import solitaire_solver
from conftest import O, ROOT
from test_engine import (BACK_BTN, CENTER, E_CENTRE, FWD_BTN, NEXT, PREV, E_NEXT, E_PLAY, E_PREV, E_TOUCH, ENGINE, RETURN, SHEET, Eng, GameDef,
                         Input, api, engine, screen, seconds)
from test_engine import PLAY as PLAY_KEY
from test_games import PAGE, Canvas, Page, fnv1a, texts

SLIP_X1, RAIL_H = 281, 22  # game_solitaire.c: the slip's right edge, the rail's height

ART_FILE, SAVE = '/usr/share/q2games/solitaire.bin', '/mnt/data/game-solitaire'
ART = (ROOT/'assets/games/solitaire.bin').read_bytes()
LAYOUT = {k: int(v) for k, v in __import__('re').findall(r'#define (ART_\w+) (\d+)', (ROOT/'patch/game_solitaire_art.h').read_text())}
MAGIC = 0x31534343
UP, NTAB, UNDO, MAXF, MAXSRC = 0x40, 20, 64, 32, 96
P_STOCK, P_WASTE, P_FOUND, P_TAB, PLACES = 0, 1, 2, 6, 13
START, DEAL, PLAY, AUTO, WON = range(5)
M_DRAW, M_RECYCLE, M_MOVE = range(3)
CW, CH, TOP_Y, TAB_Y = 43, 58, 26, 88
COLX = [19, 68, 116, 165, 213, 262, 309]
TICK, TAP, ALERT = 1, 2, 3  # clicker_kind as eng_sfx sets it
SHOTS = os.environ.get('SOL_SHOTS')  # a folder: each screen the tests draw is written there as a PNG


class Move(C.Structure):
    _fields_ = [(k, C.c_ubyte) for k in 'kind from_ to n turned'.split()] + [('pad', C.c_ubyte * 3)]


class Flight(C.Structure):
    _fields_ = [(k, C.c_ubyte) for k in 'card place idx flags'.split()] + [(k, C.c_short) for k in 'x0 y0 x1 y1'.split()] + \
        [(k, C.c_ubyte) for k in 't dur wait arc'.split()]


class Record(C.Structure):
    _fields_ = [('magic', C.c_uint), ('tab', (C.c_ubyte * NTAB) * 7), ('tlen', C.c_ubyte * 7), ('stock', C.c_ubyte * 24),
                ('waste', C.c_ubyte * 24), ('found', C.c_ubyte * 4),
                *((k, C.c_ubyte) for k in 'slen wlen draw3 live opt_draw3 opt_random won pad'.split()),
                *((k, C.c_uint) for k in 'seed moves time_ms wins played best_ms fewest sum'.split())]


class Sol(C.Structure):
    _fields_ = [('g', Record), *((k, C.c_int) for k in 'h rail limit mode sel art nsrc cur hand hfrom hn hover ndst'.split()),
                ('src', (C.c_ubyte * 2) * MAXSRC), ('dst', C.c_ubyte * 8), *((k, C.c_int) for k in 'hx hy hx0 hy0 ht'.split()),
                ('fan_d', C.c_int * 7), ('fan_u', C.c_int * 7), ('undo', Move * UNDO), ('uhead', C.c_int), ('ucount', C.c_int),
                ('fl', Flight * MAXF), ('nfl', C.c_int),
                *((k, C.c_int) for k in 'flash_t flash_p spark_t spark_p flip_t flip_p flip_i msg msg_t stuck t kings '
                                       'best_new deal_left auto_wait seq runs wait'.split()),
                ('last_ms', C.c_uint), *((k, C.c_int) for k in 'shown_s since_save unsaved nmark all'.split()),
                ('marks', (C.c_short * 4) * 9),
                ('tile', C.c_ubyte * 4096), ('shade', C.c_ushort * (LAYOUT['ART_SHADE_W'] * LAYOUT['ART_SHADE_H'])), ('felt_lut', C.c_uint * (LAYOUT['ART_LUT_LEVELS'] * 15)), ('back', (C.c_uint * 41) * 56), ('deco_p', ((C.c_uint * 57) * 83) * 5), ('deco_t', ((C.c_ubyte * 57) * 83) * 5),
                ('deco_run', ((C.c_ubyte * 4) * 83) * 5), ('deco_tmp', (C.c_uint * 57) * 83),
                ('file', C.c_uint * (LAYOUT['ART_BYTES'] // 4))]


def rank(c):
    return (c & 63) % 13


def suit(c):
    return (c & 63) // 13


def red(c):
    return suit(c) % 2 == 0


def png(path, buf, w=375, h=320):
    """buf (0xAARRGGBB words) as an RGB PNG."""
    raw = b''.join(b'\0' + b''.join(struct.pack('>I', buf[y * w + x])[1:] for x in range(w)) for y in range(h))
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    pathlib.Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                                   chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))


class Game:
    """Cabin Solitaire open on the engine, the whole screen its sheet (ENG_FULL), over Page's clock, timers and card
    (the art file on it unless art=False); the sounds it made and the rects each frame marked are kept."""
    def __init__(self, fw, h=320, files=None, art=True):
        files = dict(files or {})
        if art:
            files.setdefault(ART_FILE, ART)
        self.fw, self.e, self.page = fw, api(fw), Page(fw, files)
        self.rects, self.sounds = [], []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.rects.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('buzzeer_switch', lambda *_: self.sounds.append(fw.var('clicker_kind', C.c_int).value))
        self.done = C.CFUNCTYPE(None)(lambda: None)
        self.game = fw.var('eng_solitaire', GameDef)
        assert self.e['eng_open'](self.game, PAGE, SHEET, h, self.done, self.done) == 1
        self.b = Sol.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
        p, i = C.c_void_p, C.c_int
        self.fn = {k: fw.fn(f'game_solitaire__{k}', *v) for k, v in {
            'step': (i, p, C.POINTER(Input)), 'deal': (None, p, C.c_uint, i), 'apply': (None, p, C.POINTER(Move)),
            'unapply': (None, p, C.POINTER(Move)), 'dests': (i, p, i, i, C.POINTER(C.c_ubyte)), 'sources': (None, p),
            'changed': (None, p), 'draw_move': (i, p, C.POINTER(Move)), 'fan': (None, i, i, i, C.POINTER(C.c_int), C.POINTER(C.c_int)),
            'place_xy': (None, p, i, i, C.POINTER(C.c_int), C.POINTER(C.c_int)), 'sane': (i, p), 'can_move': (i, p),
            'auto_next': (i, p)}.items()}
        self.canvas = None

    @property
    def p(self):
        return C.addressof(self.b)

    def step(self, ticks=0, held=0, pressed=0, tx=0, ty=0):
        return self.fn['step'](self.p, C.byref(Input(0, ticks, ticks, held, pressed, 0, tx, ty)))

    def frame(self, ms=16):
        """A pass of the frame timer: the rects it marked."""
        self.rects.clear()
        self.page.now += ms
        self.e['frame'](None)
        return list(self.rects)

    def key(self, key):
        self.e['eng_key'](key, 1), self.e['eng_key'](key, 0)

    def press(self, bit, ticks=0, tx=0, ty=0):
        """One step with bit pressed (and held), then the animations it started run out."""
        r = self.step(ticks=ticks, held=bit, pressed=bit, tx=tx, ty=ty)
        self.idle()
        return r

    def idle(self, limit=600):
        for _ in range(limit):
            if not self.step():
                return
        raise AssertionError('still moving')

    def deal(self, seed, draw3=0, mode=PLAY):
        self.fn['deal'](self.p, seed, draw3)
        self.b.g.live, self.b.mode, self.b.ucount, self.b.hand, self.b.nfl = 1, mode, 0, 0, 0
        self.fn['changed'](self.p)

    def sources(self):
        self.fn['sources'](self.p)
        return [(self.b.src[k][0], self.b.src[k][1]) for k in range(self.b.nsrc)]

    def dests(self, place, n):
        out = (C.c_ubyte * 8)()
        k = self.fn['dests'](self.p, place, n, out)
        return list(out[:k])

    def move(self, kind, frm=0, to=0, n=0):
        m = Move(kind, frm, to, n, 0)
        if kind != M_MOVE:
            self.fn['draw_move'](self.p, C.byref(m))
        self.fn['apply'](self.p, C.byref(m))
        return m

    def xy(self, place, i):
        x, y = C.c_int(), C.c_int()
        self.fn['place_xy'](self.p, place, i, C.byref(x), C.byref(y))
        return x.value, y.value

    def cards(self, place):
        g = self.b.g
        if place == P_STOCK:
            return list(g.stock[:g.slen])
        if place == P_WASTE:
            return [c | UP for c in g.waste[:g.wlen]]
        if place < P_TAB:
            return [((place - P_FOUND) * 13 + r) | UP for r in range(g.found[place - P_FOUND])]
        return list(g.tab[place - P_TAB][:g.tlen[place - P_TAB]])

    def everything(self):
        return sorted(c & 63 for p in range(PLACES) for c in self.cards(p))

    def shot(self, name=None, clip=(0, 0, 375, 320)):
        """The whole sheet painted (or clip of it): the screen's buffer."""
        self.canvas = self.canvas or Canvas(self.fw)
        buf = screen(self.canvas, clip=clip, origin=(0, 0))
        self.e['eng_paint'](self.canvas.p)
        if SHOTS and name:
            png(os.path.join(SHOTS, name + '.png'), buf)
        return buf

    def close(self):
        self.e['eng_close']()


# ---- a brute-force reference for the rules ----

def ref_accepts(game, place, c, n):
    g = game.b.g
    if P_FOUND <= place < P_TAB:
        return n == 1 and suit(c) == place - P_FOUND and rank(c) == g.found[place - P_FOUND]
    if place < P_TAB:
        return False
    col = game.cards(place)
    if not col:
        return rank(c) == 12
    t = col[-1]
    return bool(t & UP) and red(t) != red(c) and rank(t) == rank(c) + 1


def ref_run(game, place, n):
    cards = game.cards(place)
    if place == P_STOCK or n < 1 or n > len(cards) or (place < P_TAB and n != 1):
        return None
    run = cards[-n:]
    if not all(c & UP for c in run):
        return None
    if any(red(a) == red(b) or rank(a) != rank(b) + 1 for a, b in zip(run, run[1:])):
        return None
    return run


def ref_dests(game, place, n):
    run = ref_run(game, place, n)
    if not run:
        return set()
    whole = place >= P_TAB and n == len(game.cards(place))
    out = set()
    for d in range(P_FOUND, PLACES):
        if d == place or (d >= P_TAB and whole and not game.cards(d)):
            continue
        if ref_accepts(game, d, run[0], n):
            out.add(d)
    return out


def ref_sources(game):
    out = []
    for p in range(P_WASTE, PLACES):
        for n in (range(len(game.cards(p)), 0, -1) if p >= P_TAB else [1]):
            if ref_dests(game, p, n):
                out.append((p, n))
    return out


def random_play(game, rnd, moves):
    """moves random legal moves (or draws) from the game's own lists."""
    for _ in range(moves):
        src = game.sources()
        if src and rnd.random() < .7:
            p, n = rnd.choice(src)
            game.move(M_MOVE, p, rnd.choice(game.dests(p, n)), n)
        elif game.b.g.slen or game.b.g.wlen:
            game.move(M_DRAW)
        game.fn['changed'](game.p)


# ---- the deal and the rules ----

def test_a_deal_is_a_whole_deck_seven_columns_and_the_stock_and_the_same_seed_deals_it_again(fw):
    g = Game(fw)
    g.deal(12345)
    first = bytes(g.b.g)
    assert g.everything() == list(range(52))
    for k in range(7):
        col = g.cards(P_TAB + k)
        assert len(col) == k + 1 and col[-1] & UP and not any(c & UP for c in col[:-1])
    assert g.b.g.slen == 24 and g.b.g.wlen == 0 and list(g.b.g.found) == [0] * 4
    g.deal(12345)
    assert bytes(g.b.g) == first
    g.deal(12346)
    assert bytes(g.b.g) != first
    g.close()


def test_the_game_deals_as_the_solver_does(fw, tmp_path):
    """The solver's deal is its own copy of the game's: the same layout for a seed, or its wins mean nothing."""
    exe = solitaire_solver.build(tmp_path)
    g = Game(fw)
    for seed in (1, 2, 552, 65535, 0xdeadbeef):
        win = solitaire_solver.solve(exe, 1, [seed], states=2000)[seed]
        g.deal(seed)
        moves = 0
        for m in win or []:  # whatever it found, every move is one the game takes
            assert m == 'D' or (m[0], m[2]) in g.sources() and m[1] in g.dests(m[0], m[2]), (seed, m)
            g.move(M_DRAW) if m == 'D' else g.move(M_MOVE, *m)
            g.fn['changed'](g.p)
            moves += 1
    g.close()


@pytest.mark.parametrize('seed', range(500))
def test_the_cursors_places_and_each_ones_destinations_are_exactly_the_legal_ones(fw, seed):
    """500 seeded positions, some way into a game: the game's lists against a brute-force scan of every place."""
    g = Game(fw)
    rnd = random.Random(seed)
    g.deal(rnd.randrange(1, 2**32), rnd.randrange(2))
    random_play(g, rnd, rnd.randrange(0, 80))
    src = g.sources()
    assert src == ref_sources(g)
    for p, n in src:
        d = g.dests(p, n)
        assert set(d) == ref_dests(g, p, n) and len(d) == len(set(d))
        home = [x for x in d if x < P_TAB]
        assert d[:len(home)] == home  # home first
        built = [x for x in d if x >= P_TAB and g.cards(x)]
        assert d[len(home):len(home) + len(built)] == built and \
            [len(g.cards(x)) for x in built] == sorted((len(g.cards(x)) for x in built), reverse=True)  # the longest next
    g.close()


def test_builds_alternate_colours_down_and_only_a_king_takes_an_empty_column(fw):
    g = Game(fw)
    g.deal(7)
    b = g.b.g
    for k in range(7):
        b.tlen[k] = 0
    b.tab[0][0], b.tab[1][0], b.tab[2][0], b.tab[3][0] = 25 | UP, 11 | UP, 10 | UP, 36 | UP  # K spades, Q hearts, J hearts, Q clubs... ranks
    b.tlen[0] = b.tlen[1] = b.tlen[2] = b.tlen[3] = 1
    # K spades (25) takes the red queen (Q hearts 11) but not a black one (Q clubs 37 would be 3*13+11)
    assert P_TAB + 0 in g.dests(P_TAB + 1, 1)
    b.tab[3][0] = (3 * 13 + 11) | UP
    assert P_TAB + 0 not in g.dests(P_TAB + 3, 1)
    assert P_TAB + 0 not in g.dests(P_TAB + 2, 1)  # J hearts on a king: two ranks down
    assert g.dests(P_TAB + 0, 1) == []  # a king alone in its column: no empty column is somewhere new
    b.tab[0][1], b.tlen[0] = 2 | UP, 2  # under the king, a 3 of hearts: the king now moves to an empty column
    b.tab[0][0], b.tab[0][1] = 2, 25 | UP
    assert set(g.dests(P_TAB + 0, 1)) == {P_TAB + k for k in range(4, 7)}
    g.close()


def test_foundations_build_up_by_suit_from_the_ace(fw):
    g = Game(fw)
    g.deal(7)
    b = g.b.g
    b.waste[0], b.wlen = 13, 1  # the ace of spades
    assert g.dests(P_WASTE, 1)[0] == P_FOUND + 1
    g.move(M_MOVE, P_WASTE, P_FOUND + 1, 1)
    assert list(b.found) == [0, 1, 0, 0]
    b.waste[0], b.wlen = 15, 1  # the three of spades: not yet
    assert P_FOUND + 1 not in g.dests(P_WASTE, 1)
    b.waste[0] = 14
    assert g.dests(P_WASTE, 1)[0] == P_FOUND + 1
    b.waste[0] = 1  # two of hearts on an empty hearts foundation: no
    assert not [d for d in g.dests(P_WASTE, 1) if d < P_TAB]
    g.close()


@pytest.mark.parametrize('draw3', [0, 1])
def test_the_stock_draws_one_or_three_and_the_waste_goes_back_over_in_order(fw, draw3):
    g = Game(fw)
    g.deal(99, draw3)
    stock = g.cards(P_STOCK)
    seen = []
    while g.b.g.slen:
        m = g.move(M_DRAW)
        assert m.kind == M_DRAW and m.n == min(3 if draw3 else 1, len(stock) - len(seen))
        seen += g.cards(P_WASTE)[-m.n:]
    assert seen == [c | UP for c in stock[::-1]]  # top first, each lands on the waste in turn
    m = g.move(M_DRAW)
    assert m.kind == M_RECYCLE and m.n == 24 and g.cards(P_STOCK) == stock and g.b.g.wlen == 0  # the same order again
    g.close()


def test_undo_restores_the_state_byte_for_byte_whatever_the_move(fw):
    g = Game(fw)
    rnd = random.Random(5)
    for game in range(40):
        g.deal(rnd.randrange(1, 2**32), game % 2)
        for _ in range(60):
            before = bytes(g.b.g)
            src = g.sources()
            if src and rnd.random() < .6:
                p, n = rnd.choice(src)
                m = g.move(M_MOVE, p, rnd.choice(g.dests(p, n)), n)
            else:
                m = g.move(M_DRAW)
            after = bytes(g.b.g)
            g.fn['unapply'](g.p, C.byref(m))
            assert bytes(g.b.g) == before
            g.fn['apply'](g.p, C.byref(m))
            assert bytes(g.b.g) == after
            g.fn['changed'](g.p)
    g.close()


def test_a_move_off_a_face_down_card_turns_it(fw):
    g = Game(fw)
    g.deal(7)
    b = g.b.g
    b.tlen[6] = 3
    b.tab[6][0], b.tab[6][1], b.tab[6][2] = 0, 5, 12 | UP  # a king of hearts over two face down
    for k in range(6):
        b.tlen[k] = 0
    m = g.move(M_MOVE, P_TAB + 6, P_TAB + 0, 1)
    assert m.turned == 1 and b.tab[6][1] == 5 | UP and b.tlen[6] == 2
    g.close()


# ---- the fan ----

def fan(g, nd, nu, room=296 - TAB_Y - CH):
    d, u = C.c_int(), C.c_int()
    g.fn['fan'](nd, nu, room, C.byref(d), C.byref(u))
    return d.value, u.value


def test_the_fan_is_7_and_17_with_room_and_tightens_by_the_specs_rule(fw):
    g = Game(fw)
    assert fan(g, 0, 1) == (7, 17) and fan(g, 6, 1) == (7, 17) and fan(g, 3, 8) == (7, 17)
    for nd in range(7):
        for nu in range(1, 14):
            d, u = fan(g, nd, nu)
            for dd in (7, 5, 3):  # SPEC.md's rule, in Python
                uu = 17 if nu <= 1 else min(17, (296 - TAB_Y - CH - dd * nd) // (nu - 1))
                if uu >= 12 or dd == 3:
                    break
            assert (d, u) == (dd, max(uu, 11)), (nd, nu)
            assert TAB_Y + nd * d + (nu - 1) * u + CH <= 296 or (nd, nu) == (6, 13) and TAB_Y + nd * d + (nu - 1) * u + CH == 296
    assert fan(g, 6, 13) == (3, 11)  # the longest pile there can be
    g.close()


def test_the_longest_pile_ends_on_the_sheet_above_the_rail(fw):
    g = Game(fw)
    g.deal(7)
    b = g.b.g
    b.tlen[6] = 19
    for i in range(6):
        b.tab[6][i] = 40 + i  # six face down
    for i in range(13):  # king to ace, alternating
        b.tab[6][6 + i] = ((1 if i % 2 == 0 else 0) * 13 + 12 - i) | UP
    g.fn['changed'](g.p)
    x, y = g.xy(P_TAB + 6, 18)
    assert (g.b.fan_d[6], g.b.fan_u[6]) == (3, 11) and y + CH == 296 < g.b.rail
    g.close()


# ---- the art ----

def test_the_art_file_is_whole_sealed_and_laid_out_as_its_header_says(fw):
    assert len(ART) == LAYOUT['ART_BYTES'] and ART[:4] == struct.pack('<I', 0x314c4f53)
    assert int.from_bytes(ART[-4:], 'little') == fnv1a(ART[:-4])
    for e in range(LAYOUT['ART_ENTRIES']):
        off, w, h, dx, dy = struct.unpack_from('<IBBbb', ART, 4 + 8 * e)
        start = (off & 0x7fffffff) + (64 if off >> 31 else 0)
        assert 0 < w and 0 < h and start + h * ((w + 1) // 2) <= LAYOUT['ART_TILE'], e  # rows from a byte's start
    g = Game(fw)
    assert g.b.art == 1 and set(g.b.tile) <= set(range(0, 60, 4))  # byte offsets of 15 steps
    g.close()


@pytest.mark.parametrize('broken', [ART[:100], ART[:-1] + bytes([ART[-1] ^ 1]), b''])
def test_without_its_art_the_game_keeps_its_state_and_shows_a_plain_felt(fw, broken):
    g = Game(fw, files={ART_FILE: broken} if broken else None, art=bool(broken))
    assert g.b.art == 0
    g.deal(7)
    buf = g.shot()
    assert buf[200 * 375 + 360] == 0xff1b5c3e  # a flat felt
    g.close()


def test_every_rank_of_the_index_ends_above_the_tightest_fans_next_card(fw):
    """At 11 px a card (the tightest fan) the next card's edge is row 11 and its contact shade from row 9.8: every
    rank's index, the queen's tail too, is inked only in rows 0 to 10."""
    for r in range(13):
        off, w, h, dx, dy = struct.unpack_from('<IBBbb', ART, 4 + 8 * r)
        assert dy >= 0 and dy + h <= 11, (r, dy, h)


# ---- the screens ----

def test_play_draws_the_table_as_the_mockup_lays_it_out(fw):
    g = Game(fw)
    g.deal(7)
    buf = g.shot('03-play')
    for k in range(7):  # each column's top card: cream face at its middle
        x, y = g.xy(P_TAB + k, g.b.g.tlen[k] - 1)
        assert buf[(y + CH - 3) * 375 + x + 21] & 0xffffff == 0xf6eedc, k
    assert buf[(TOP_Y + 20) * 375 + 22 + 20] & 0xffffff not in (0xf6eedc,)  # the stock's back
    g.close()


# ---- the mockups' tables (the design's src/render.py), set on the game ----

SUITS, RANKS = 'hsdc', ['A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K']


def cid(name):
    """'10d' -> the game's card."""
    return SUITS.index(name[-1]) * 13 + RANKS.index(name[:-1])


DEFAULT = dict(stock=3, waste=['4c', 'Jd', '7s'], found=dict(h=3, s=1, d=3, c=2),
               piles=[(0, ['Ks']), (1, ['7d']), (2, ['Qh', 'Jc']), (3, ['10d', '9c', '8h']), (4, ['5s']), (3, ['6h', '5c', '4d']),
                      (6, ['9s'])])
LONG = dict(stock=0, waste=['2d'], found=dict(h=0, s=0, d=1, c=0),
            piles=[(0, ['Kd', 'Qs', 'Jh']), (1, ['9d']), (0, []), (3, ['5h', '4s']),
                   (2, ['Js', '10h', '9c', '8h', '7c', '6d', '5c']), (2, ['Qd', 'Jc', '10d']),
                   (6, [r + ('c' if i % 2 == 0 else 'h') for i, r in enumerate(RANKS[::-1])])])


def table(g, t, draw3=0, mode=PLAY):
    """The game's state set to a mockup's table: its face-down cards whatever the deck has left over."""
    b = g.b.g
    g.deal(1, draw3)
    used = {cid(c) for c in t['waste']} | {k * 13 + r for k, s in enumerate(SUITS) for r in range(t['found'][s])} | \
        {cid(c) for _, up in t['piles'] for c in up}
    spare = [c for c in range(52) if c not in used]
    for k, (down, up) in enumerate(t['piles']):
        col = [spare.pop() for _ in range(down)] + [cid(c) | UP for c in up]
        b.tlen[k] = len(col)
        for i in range(NTAB):
            b.tab[k][i] = col[i] if i < len(col) else 0
    for i in range(24):
        b.waste[i] = cid(t['waste'][i]) if i < len(t['waste']) else 0
    b.wlen = len(t['waste'])
    stock = spare if t['stock'] else []  # what is left over is the stock (a table with none drops it)
    for i in range(24):
        b.stock[i] = stock[i] if i < len(stock) else 0
    b.slen = len(stock)
    for k, s in enumerate(SUITS):
        b.found[k] = t['found'][s]
    b.live, b.moves, b.time_ms = 1, 23, 754000
    g.b.mode = mode
    g.fn['changed'](g.p)
    return g


def cursor_on(g, place, n):
    g.b.cur = g.sources().index((place, n))


def frames(g, n):
    for _ in range(n):
        g.step()


def scene(g, name):
    """The game in one of the design's screens (mockups/NAME.png)."""
    b = g.b
    if name == '01-start':
        b.g.wins, b.g.played, b.g.best_ms, b.g.fewest, b.g.live, b.g.time_ms = 12, 30, 400000, 104, 1, 754000
    elif name == '02-dealing':
        g.press(0)
        g.step(pressed=E_PLAY, held=E_PLAY)
        frames(g, 39)
    elif name in ('03-play', '04-picked-hints', '05-glide', '06-home', '07-undo'):
        table(g, DEFAULT)
        cursor_on(g, P_TAB + 5, 1)
        if name != '03-play':
            g.press(E_CENTRE)
        if name in ('05-glide', '06-home', '07-undo'):
            g.step(pressed=E_CENTRE, held=E_CENTRE)
            frames(g, 4 if name == '05-glide' else 7)
        if name == '07-undo':
            g.idle()
            g.step(pressed=E_PREV, held=E_PREV)
            frames(g, 2)
    elif name == '08-long-pile':
        table(g, LONG)
        b.g.moves, b.g.time_ms = 141, 598000
        cursor_on(g, P_TAB + 4, 5)
    elif name == '09-draw-three':
        table(g, dict(DEFAULT, stock=0), draw3=1)
        cursor_on(g, P_WASTE, 1)
    elif name == '10-auto-finish':
        table(g, dict(stock=0, waste=[], found=dict(h=9, s=8, d=9, c=7),
                      piles=[(0, ['Ks', 'Qh', 'Js']), (0, ['Kh', 'Qc']), (0, []), (0, ['Kd', 'Qs', 'Jd', '10s', '9s']),
                             (0, ['Kc', 'Qd', 'Jc', '10h', '9c', '8c']), (0, []), (0, ['Jh', '10c', '10d'])]))
        g.step(pressed=E_PLAY, held=E_PLAY)
        frames(g, 9)
    elif name.startswith('11-win'):
        table(g, dict(stock=0, waste=[], found=dict(h=13, s=13, d=12, c=13), piles=[(0, [])] * 6 + [(0, ['Kd'])]))
        b.g.wins, b.g.played, b.g.moves, b.g.time_ms = 12, 31, 125, 472000
        b.g.best_ms = 500000 if name.endswith('best') else 400000
        cursor_on(g, P_TAB + 6, 1)
        g.press(E_NEXT)
    return g


SCREENS = ['01-start', '02-dealing', '03-play', '04-picked-hints', '05-glide', '06-home', '07-undo', '08-long-pile',
           '09-draw-three', '10-auto-finish', '11-win', '11-win-new-best']


@pytest.mark.parametrize('name', SCREENS)
def test_each_screen_paints_whole_and_nothing_but_felt_and_the_rail_lies_under_the_glass(fw, name):
    g = scene(Game(fw), name)
    buf = g.shot(name)
    assert all(v >> 24 == 0xff for v in buf)
    g.close()


# ---- play through the inputs ----

def test_the_wheel_walks_the_cursor_through_the_sources_both_ways_and_wraps(fw):
    g = table(Game(fw), DEFAULT)
    src = g.sources()
    assert len(src) >= 3 and g.b.cur == 0
    g.press(0, ticks=1)
    assert g.b.cur == 1 and g.sounds[-1] == TICK
    g.press(0, ticks=-2)
    assert g.b.cur == len(src) - 1
    g.press(0, ticks=len(src))
    assert g.b.cur == len(src) - 1
    g.close()


def test_centre_picks_up_and_the_hand_hovers_over_the_best_place_the_wheel_moves_it_and_centre_drops(fw):
    g = table(Game(fw), DEFAULT)
    cursor_on(g, P_TAB + 5, 1)  # the four of diamonds: home or on the five of spades
    g.sounds.clear()
    g.press(E_CENTRE)
    b = g.b
    assert b.hand and (b.hfrom, b.hn) == (P_TAB + 5, 1) and list(b.dst[:b.ndst]) == [P_FOUND + 2, P_TAB + 4]
    assert b.hover == 0 and (b.hx, b.hy) == g.xy(P_FOUND + 2, 3) and g.sounds == [TAP]
    g.press(0, ticks=1)
    assert b.hover == 1 and (b.hx, b.hy) == g.xy(P_TAB + 4, 5)
    g.press(0, ticks=1)
    assert b.hover == 0
    g.press(0, ticks=1)
    moves = b.g.moves
    g.press(E_CENTRE)
    assert not b.hand and g.cards(P_TAB + 4)[-1] == cid('4d') | UP and b.g.moves == moves + 1 and b.ucount == 1
    g.close()


def test_previous_puts_the_hand_back_and_otherwise_undoes(fw):
    g = table(Game(fw), DEFAULT)
    cursor_on(g, P_TAB + 5, 1)
    before = bytes(g.b.g)
    g.press(E_CENTRE)
    g.press(E_PREV)
    assert not g.b.hand and bytes(g.b.g) == before
    g.press(E_CENTRE)
    g.press(E_CENTRE)  # home
    assert g.b.g.found[2] == 4
    g.step(pressed=E_PREV, held=E_PREV)
    assert g.b.msg == 1  # "Undo: 23 moves" for a second and a half
    g.idle()
    assert bytes(g.b.g)[:-36] == before[:-36] and g.b.g.moves == 23 and g.b.msg == 0
    for _ in range(3):
        g.press(E_PREV)  # nothing left to undo: nothing happens
    assert bytes(g.b.g)[:-36] == before[:-36]
    g.close()


def test_undo_reaches_back_64_moves_and_no_further(fw):
    g = Game(fw)
    g.deal(3)
    start = bytes(g.b.g)[:-36]
    for _ in range(70):
        g.press(E_PLAY)
    assert g.b.ucount == 64
    for _ in range(70):
        g.press(E_PREV)
    assert g.b.ucount == 0 and bytes(g.b.g)[:-36] != start  # the first six draws stay
    g.close()


def test_next_sends_the_cursors_card_home_when_it_can_go(fw):
    g = table(Game(fw), DEFAULT)
    cursor_on(g, P_TAB + 5, 1)
    g.sounds.clear()
    g.press(E_NEXT)
    assert g.b.g.found[2] == 4 and g.sounds[0] == TAP
    cursor_on(g, P_WASTE, 1)  # the seven of spades cannot go home
    found = list(g.b.g.found)
    g.press(E_NEXT)
    assert list(g.b.g.found) == found
    g.press(E_CENTRE)  # with a hand: Next takes it home only if home is one of its places
    assert g.b.hand
    g.press(E_NEXT)
    assert g.b.hand and list(g.b.g.found) == found
    g.close()


def test_play_draws_and_once_everything_is_up_finishes_every_card_home(fw):
    g = Game(fw)
    g.deal(11)
    w = g.b.g.wlen
    g.press(E_PLAY)
    assert g.b.g.wlen == w + 1 and g.b.g.moves == 1
    scene(g, '10-auto-finish')
    assert g.b.mode == AUTO
    g.idle(2000)
    assert g.b.mode == WON and list(g.b.g.found) == [13] * 4 and g.b.g.wins == 1
    g.close()


def test_centre_in_the_finish_sends_the_rest_home_at_once(fw):
    g = scene(Game(fw), '10-auto-finish')
    g.step(pressed=E_CENTRE, held=E_CENTRE)
    assert list(g.b.g.found) == [13] * 4
    g.idle()
    assert g.b.mode == WON
    g.close()


def test_a_finger_on_the_stock_draws_on_a_card_picks_it_and_on_a_place_drops_it(fw):
    g = table(Game(fw), DEFAULT)
    x, y = g.xy(P_TAB + 5, 5)  # the four of diamonds
    g.press(E_TOUCH, tx=x + 20, ty=y + 30)
    assert g.b.hand and g.b.hfrom == P_TAB + 5
    fx, fy = g.xy(P_FOUND + 2, 3)
    g.press(E_TOUCH, tx=fx + 20, ty=fy + 20)
    assert not g.b.hand and g.b.g.found[2] == 4
    x, y = g.xy(P_TAB + 4, 4)
    g.press(E_TOUCH, tx=x + 20, ty=y + 30)  # the five of spades has nowhere to go: nothing
    assert not g.b.hand
    w = g.b.g.wlen
    g.press(E_TOUCH, tx=30, ty=40)
    assert g.b.g.wlen == w + 1
    x, y = g.xy(P_WASTE, g.b.g.wlen - 1)
    g.close()


def test_a_finger_away_from_every_place_puts_the_hand_back(fw):
    g = table(Game(fw), DEFAULT)
    cursor_on(g, P_TAB + 5, 1)
    g.press(E_CENTRE)
    g.press(E_TOUCH, tx=180, ty=250)
    assert not g.b.hand and g.b.g.found[2] == 3
    g.close()


def test_with_nothing_to_move_the_slip_says_so_once(fw):
    g = Game(fw)
    g.deal(7)
    b = g.b.g
    for k in range(7):  # every column a lone king, nothing in the stock or the waste
        b.tlen[k] = 1
        b.tab[k][0] = (k % 4 * 13 + 12) | UP
    b.slen = b.wlen = 0
    g.sounds.clear()
    g.fn['changed'](g.p)
    assert g.b.stuck and g.b.msg == 2 and g.sounds == [ALERT]
    g.fn['changed'](g.p)
    assert g.sounds == [ALERT]
    g.close()


# ---- the start board ----

def test_the_start_board_changes_its_rows_and_play_deals_by_them(fw):
    g = Game(fw)
    b = g.b
    assert b.mode == START and b.sel == 0 and not b.g.live
    g.press(E_CENTRE)
    assert b.g.opt_random == 1
    g.press(0, ticks=1)
    g.press(E_CENTRE)
    assert b.g.opt_draw3 == 1 and b.sel == 1
    g.press(0, ticks=1)
    assert b.sel == 0  # two rows: no game to resume
    g.page.card.files.clear()
    g.step(pressed=E_PLAY, held=E_PLAY)
    assert b.mode == DEAL and b.g.draw3 == 1 and b.g.live and b.g.played == 1
    g.idle()
    assert b.mode == PLAY and g.everything() == list(range(52))
    g.close()


def test_a_winnable_deal_is_one_of_the_tables_seeds(fw):
    g = Game(fw)
    seeds = [int(s) for s in __import__('re').findall(r'\d+', (ROOT/'patch/game_solitaire_seeds.h').read_text().split('{')[1])]
    assert len(seeds) == 256 == len(set(seeds))
    for _ in range(5):
        g.b.mode = START
        g.step(pressed=E_PLAY, held=E_PLAY)
        assert g.b.g.seed in seeds
        g.idle()
    g.close()


def test_resume_returns_to_the_game_in_progress(fw):
    g = Game(fw)
    g.step(pressed=E_PLAY, held=E_PLAY)
    g.idle()
    g.press(E_PLAY)
    saved = bytes(g.b.g)
    g.e['eng_hold']()
    g.close()
    g = Game(fw, files=g.page.card.files)
    assert g.b.mode == START and g.b.g.live and bytes(g.b.g)[4:-4] == saved[4:-4]
    g.press(0, ticks=2)
    g.press(E_CENTRE)
    assert g.b.mode == PLAY
    g.close()


# ---- time, saves and pauses ----

def test_time_counts_play_alone_a_rest_five_minutes_after_the_last_input_at_most(fw):
    g = Game(fw)
    g.deal(5)
    g.step()
    g.page.now += 5000
    g.step()
    assert g.b.g.time_ms == 5000
    g.page.now += 700000  # a long while with the screen on: counted up to five minutes after the last input
    g.step()
    assert g.b.g.time_ms == 300000
    g.e['eng_hold']()
    g.page.now += 60000
    g.e['eng_run']()  # play again: as an input
    g.step()
    assert g.b.g.time_ms == 300000
    g.page.now += 2000
    g.step()
    assert g.b.g.time_ms == 302000
    g.close()


def test_at_rest_the_time_moves_a_second_at_a_time_on_its_figure_alone_until_five_minutes_after_the_last_input(fw):
    """The owner's rule (2026-11-24): a clock that stands while the player thinks looks broken. At rest the frame timer
    is off and the engine's second timer steps the game once a second (ENG_CLOCK): the slip's time follows, only its
    figure repainted. It stands five minutes after the last input, and nothing wakes while held or dark."""
    g = Game(fw)
    removed = []
    fw.mock('timer_remove', lambda t, *_: removed.append(t) or 1)
    g.deal(5)
    while g.e['eng_live']():
        g.frame()
    assert [ms for _, ms in seconds(fw, g.page.timers)] == [1000]
    en, t0, s0 = engine(fw), g.b.g.time_ms, g.b.shown_s
    for _ in range(5):  # five seconds of thinking
        g.rects.clear()
        g.page.now += 1000
        assert g.e['second'](None) == 8 and not g.e['eng_live']()
        assert g.rects == [(205, g.b.rail + 3, SLIP_X1 - 205, RAIL_H - 3)]  # the time's figure alone
    assert g.b.g.time_ms == t0 + 5000 and g.b.shown_s == s0 + 5
    played = en.play
    g.page.now += 400_000  # walked away with the screen on
    assert g.e['second'](None) == 0 and not en.second  # the clock stands: no more passes
    assert en.play == 300_000 and g.b.g.time_ms == t0 + 5000 + 300_000 - played
    stood = g.b.g.time_ms
    g.key(NEXT)  # back: the next input
    while g.e['eng_live']():
        g.frame()
    assert g.b.g.time_ms - stood < 1000 and len(seconds(fw, g.page.timers)) == 2  # a second timer again
    g.e['eng_hold']()  # held: its timer removed, none added
    assert removed and not en.second and len(seconds(fw, g.page.timers)) == 2
    g.page.now += 60_000
    assert g.b.g.time_ms - stood < 1000
    g.e['eng_run']()
    while g.e['eng_live']():
        g.frame()
    fw.var('g_backlight_status', C.c_ubyte).value = 0  # dark: the pass ends and holds the game
    before = g.b.g.time_ms
    g.page.now += 1000
    assert g.e['second'](None) == 0 and not en.second and g.b.g.time_ms == before
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    g.close()


def test_every_tenth_move_is_saved_after_its_glide_and_a_pause_saves_the_rest(fw):
    g = Game(fw)
    g.deal(5)
    for k in range(9):
        g.press(E_PLAY)
    assert SAVE not in g.page.card.files
    g.step(pressed=E_PLAY, held=E_PLAY)
    assert SAVE not in g.page.card.files and g.b.nfl  # not while the card glides
    g.idle()
    saved = g.page.card.files[SAVE]
    assert saved[:4] == struct.pack('<I', MAGIC) and len(saved) == C.sizeof(Record)
    assert int.from_bytes(saved[-4:], 'little') == fnv1a(saved[:-4])
    g.press(E_PLAY)
    assert g.page.card.files[SAVE] == saved
    g.e['eng_hold']()
    assert g.page.card.files[SAVE] != saved and Record.from_buffer_copy(g.page.card.files[SAVE]).moves == 11
    g.close()


def record(g, **kw):
    r = Record.from_buffer_copy(bytes(g.b.g))
    for k, v in kw.items():
        setattr(r, k, v)
    raw = bytearray(bytes(r))
    raw[:4] = struct.pack('<I', MAGIC)
    raw[-4:] = struct.pack('<I', fnv1a(bytes(raw[:-4])))
    return bytes(raw)


def test_a_save_loads_only_whole_sane_and_a_whole_deck(fw):
    g = Game(fw)
    g.deal(77)
    g.press(E_PLAY)
    good = record(g, wins=3, played=9)
    g.close()
    tab = bytes(Record.from_buffer_copy(good).tab)
    twice = bytearray(good)
    twice[4 + 6 * NTAB + 1] = twice[4 + 6 * NTAB]  # a card in two places
    twice[-4:] = struct.pack('<I', fnv1a(bytes(twice[:-4])))
    cases = [(good, True), (good[:-1] + bytes([good[-1] ^ 1]), False), (good[:50], False), (bytes(twice), False),
             (b'XXXX' + good[4:], False)]
    for raw, ok in cases:
        g = Game(fw, files={SAVE: raw})
        assert (g.b.g.played == 9) == ok and g.b.mode == START
        assert g.everything() == list(range(52))
        g.close()
    g = Game(fw)
    g.deal(77)
    for bad in (dict(wins=10, played=9), dict(slen=25), dict(draw3=2), dict(time_ms=400000000)):
        assert not g.fn['sane'](C.addressof(Record.from_buffer_copy(record(g, **bad))))
    assert g.fn['sane'](C.addressof(Record.from_buffer_copy(record(g))))
    r = Record.from_buffer_copy(record(g))
    r.tab[6][0] |= UP  # a face-up card under a face-down one
    assert not g.fn['sane'](C.addressof(r))
    g.close()


def test_the_old_solitaires_save_is_left_alone(fw):
    old = b'old built-in game'
    g = Game(fw, files={'/mnt/data/ringnav-solitaire': old})
    g.step(pressed=E_PLAY, held=E_PLAY)
    g.idle()
    g.e['eng_hold']()
    assert g.page.card.files['/mnt/data/ringnav-solitaire'] == old and SAVE in g.page.card.files
    g.close()


def test_a_win_counts_keeps_the_best_and_is_saved_at_once(fw):
    g = scene(Game(fw), '11-win-new-best')
    b = g.b
    assert b.mode == WON and b.best_new and b.g.best_ms == 472000 and b.g.wins == 13 and b.g.fewest == 126
    assert Record.from_buffer_copy(g.page.card.files[SAVE]).wins == 13 and not b.g.live
    g.close()
    g = scene(Game(fw), '11-win')
    assert not g.b.best_new and g.b.g.best_ms == 400000
    g.step(pressed=E_CENTRE, held=E_CENTRE)
    assert g.b.mode == DEAL and g.b.g.played == 32
    g.close()


# ---- the solver's wins, replayed through the game ----

@pytest.mark.parametrize('draw', [3, 1])
def test_the_tables_deals_are_won_again_and_the_wins_play_through_the_games_own_moves(fw, tmp_path, draw):
    exe = solitaire_solver.build(tmp_path)
    seeds = [int(s) for s in __import__('re').findall(r'\d+', (ROOT/'patch/game_solitaire_seeds.h').read_text().split('{')[1])]
    sample = random.Random(draw).sample(seeds, 12)
    wins = solitaire_solver.solve(exe, draw, sample)
    g = Game(fw)
    for seed in sample:
        assert wins[seed], seed
        g.deal(seed, draw == 3)
        for m in wins[seed]:
            if m == 'D':
                assert g.b.g.slen or g.b.g.wlen
                g.move(M_DRAW)
            else:
                frm, to, n = m
                assert to in g.dests(frm, n), (seed, m)
                g.move(M_MOVE, frm, to, n)
            g.fn['changed'](g.p)
        assert list(g.b.g.found) == [13] * 4, seed
    g.close()


# ---- the engine's side ----

def test_at_rest_the_frame_timer_stops_and_a_key_answers_on_the_next_pass(fw):
    g = Game(fw)
    g.frame()
    assert not g.e['eng_live']()
    g.key(CENTER)  # the start board's row changes
    rects = g.frame()
    assert g.b.g.opt_random == 1 and rects and not g.e['eng_live']()
    g.close()


def felt(x, y):
    """The felt's pixel at x, y as the art file makes it (cards.felt_image, the shade map stepped by its 4 px cells)."""
    t = ART[LAYOUT['ART_TILE'] + (y % 64 * 64 + x % 64) // 2] >> (4 * (x & 1)) & 15
    level = ART[LAYOUT['ART_SHADE'] + (y >> 2) * LAYOUT['ART_SHADE_W'] + (x >> 2)] - LAYOUT['ART_LUT_LO']
    return 0xff000000 | struct.unpack_from('<I', ART, LAYOUT['ART_LUT'] + 4 * (level * 15 + t))[0]


def glass(x, y):
    """Under the glass's 80 px corners (a pixel's centre outside the round)."""
    ox = 80 - x - .5 if x < 80 else x + .5 - 295
    oy = 80 - y - .5 if y < 80 else y + .5 - 240
    return ox > 0 and oy > 0 and (ox * ox + oy * oy) ** .5 > 80


@pytest.mark.parametrize('name', ['01-start', '03-play', '04-picked-hints', '08-long-pile', '09-draw-three', '11-win-new-best'])
def test_nothing_but_the_felt_and_the_rails_wood_falls_under_the_glass(fw, name):
    """Each screen against the same sheet with nothing on it: no pixel that differs is in a corner the glass hides."""
    g = scene(Game(fw), name)
    buf = g.shot()
    rail = g.b.rail
    bad = [(x, y) for y in range(rail - 2) for x in (*range(80), *range(295, 375))
           if glass(x, y) and buf[y * 375 + x] != felt(x, y)]
    assert bad == [], bad[:10]
    g.close()


def test_cabin_solitaire_is_games_second_engine_row_and_opens_over_the_whole_screen(fw):
    """Its sheet is the whole screen and the system bar (a window over every page) is hidden while it runs, shown
    again while it is paused (its clock and battery then) and for the list."""
    BAR = 0x7700
    fw.var('fw_ctx_system_bar', C.c_void_p).value = BAR
    p = Page(fw, {ART_FILE: ART})
    moved, bar = [], []
    fw.mock('widget_move_resize', lambda w, x, y, ww, hh, *_: moved.append((w, x, y, ww, hh)) and 0)
    fw.mock('widget_set_visible', lambda w, v, *_: bar.append(v) if w == BAR else None)
    assert p.s.engines >= 2 and fw.var('eng_solitaire', GameDef).name == b'Cabin Solitaire'
    p.go(ENGINE + 1)
    assert p.s.screen == ENGINE + 1 and api(fw)['eng_live']()
    assert moved[-1] == (p.s.sheet, 0, 0, 375, 320)  # the page is the window, from the screen's top
    en = Eng.from_address(fw.var('engine__en', C.c_void_p).value)
    assert en.h == 320 and bar == [0]
    p.key(RETURN)
    assert p.s.paused == 2 and not api(fw)['eng_live']() and bar == [0, 1]
    p.key(RETURN)  # Continue
    assert bar == [0, 1, 0]
    p.go(0)  # the list: under the status row again
    assert moved[-1][3:] == (375, p.s.h) and moved[-1][2] > 0 and bar == [0, 1, 0, 1]
    p.close()
    assert not fw.var('engine__en', C.c_void_p).value


@pytest.mark.parametrize('seed', [11, 12, 13])
def test_a_frame_repaints_every_pixel_that_changed(fw, seed):
    """Two screens kept side by side through a game played by its keys: one painted only in the rects each frame
    marked, the other painted whole; they never differ, so no change goes unmarked."""
    g = Game(fw)
    canvas = Canvas(fw)
    part, whole = (C.c_uint * (375 * 320))(), (C.c_uint * (375 * 320))()
    clip = [(0, 0, 375, 320)]
    screen(canvas, origin=(0, 0))
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, (C.c_int * 4)(*clip[0]), 16) and 0)

    def paint(buf, rect):
        C.c_void_p.from_buffer(canvas.lcd, O['LCD_OFFLINE_FB']).value = C.addressof(buf)
        clip[0] = rect
        g.e['eng_paint'](canvas.p)
    paint(part, (0, 0, 375, 320))
    rnd, moves = random.Random(seed), 0
    keys = [CENTER] * 5 + [PLAY_KEY] * 3 + [NEXT, NEXT, PREV, BACK_BTN, FWD_BTN]
    for k in range(700):
        if k % 3 == 0:
            last = rnd.choice(keys)
            g.key(last)
        for r in g.frame():
            paint(part, r)
        C.memset(whole, 0x5a, C.sizeof(whole))  # a pixel the paint leaves would show
        paint(whole, (0, 0, 375, 320))
        if bytes(part) != bytes(whole):
            diff = [i for i in range(375 * 320) if part[i] != whole[i]]
            xs, ys = [i % 375 for i in diff], [i // 375 for i in diff]
            if SHOTS:
                png(os.path.join(SHOTS, 'part.png'), part), png(os.path.join(SHOTS, 'whole.png'), whole)
            raise AssertionError((k, g.b.mode, last, len(diff), (min(xs), min(ys), max(xs), max(ys)), g.rects))
        if g.b.mode == WON and g.b.t > 60:
            g.key(CENTER)
        moves = max(moves, g.b.g.moves)
    assert moves > 40, moves  # the game was played, not left on its board
    g.close()
