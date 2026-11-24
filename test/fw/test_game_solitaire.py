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
from conftest import ROOT
from test_engine import (CENTER, E_CENTRE, E_NEXT, E_PLAY, E_PREV, E_TOUCH, ENGINE, RETURN, SHEET, Eng, GameDef,
                         Input, api, screen)
from test_games import PAGE, Canvas, Page, fnv1a, texts

ART_FILE, SAVE = '/usr/share/q2games/solitaire.bin', '/mnt/data/game-solitaire'
ART = (ROOT/'assets/games/solitaire.bin').read_bytes()
LAYOUT = {k: int(v) for k, v in __import__('re').findall(r'#define (ART_\w+) (\d+)', (ROOT/'patch/game_solitaire_art.h').read_text())}
MAGIC = 0x31534343
UP, NTAB, UNDO, MAXF, MAXSRC = 0x40, 20, 64, 32, 96
P_STOCK, P_WASTE, P_FOUND, P_TAB, PLACES = 0, 1, 2, 6, 13
START, DEAL, PLAY, AUTO, WON = range(5)
M_DRAW, M_RECYCLE, M_MOVE = range(3)
CW, CH, TOP_Y, TAB_Y = 43, 58, 26, 88
COLX = [19, 68, 116, 165, 213, 262, 310]
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
                ('last_ms', C.c_uint), *((k, C.c_int) for k in 'shown_s since_save unsaved'.split()),
                ('tile', C.c_ubyte * 4096), ('felt_lut', C.c_uint * (LAYOUT['ART_LUT_LEVELS'] * 15)),
                ('a_o', (C.c_ubyte * 5) * 5), ('a_f', (C.c_ubyte * 5) * 5), ('file', C.c_uint * (LAYOUT['ART_BYTES'] // 4))]


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
        assert 0 < w and 0 < h and start + (w * h + 1) // 2 <= LAYOUT['ART_TILE'], e
    g = Game(fw)
    assert g.b.art == 1 and set(g.b.tile) <= set(range(15))
    g.close()


@pytest.mark.parametrize('broken', [ART[:100], ART[:-1] + bytes([ART[-1] ^ 1]), b''])
def test_without_its_art_the_game_still_plays_and_draws_plainly(fw, broken):
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
