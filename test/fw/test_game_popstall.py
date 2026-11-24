"""Pop Stall (patch/game_popstall.c) on the engine: the lattice and its levels with sandbags and the star, the
balls' swept paths, the trampoline's inputs and dip, combos, tickets, lives, the level's clear and the game's end,
saves, the pause, the glass's corners, the art and its stand-ins; then through Games' page."""
import ctypes as C
import math
import os
import random
import struct
import zlib
from pathlib import Path

import pytest

from conftest import O
from test_engine import CENTER, E_CENTRE, E_NEXT, E_PLAY, E_PREV, E_TOUCH, ENGINE, RETURN, SHEET, Eng, GameDef, Input, api, screen
from test_games import PAGE, Canvas, Page, fnv1a, paint, texts

ROOT = Path(__file__).resolve().parents[2]
ART_FILE = '/usr/share/q2games/popstall/art.bin'
ART = (ROOT / 'assets/popstall/art.bin').read_bytes()
ROWS, COLS, PX, PY, X0, Y0, RB, RC = 7, 16, 20, 17, 37.5, 78, 7, 8
L, R, TOP, MAT, REST, PAD_W, WIDE_W = 24, 351, 56, 280, 273, 60, 90
READY, PLAYING, CLEAR, INFLATE, OVER = range(5)
NONE, BAL, DBL, STAR, SACK = range(5)
T_WIDE, T_TRIO, T_GLUE, T_SLOW, T_LIFE = range(1, 6)
F_POP, F_STRIP, F_BAG, F_DUST, F_SCORE, F_CALL, F_SPARK, F_STARS, F_KNOCK = range(1, 10)
MAGIC, FILE = 0x31535450, '/mnt/data/game-popstall'
FULL = 4  # ENG_FULL
FX = 65536
TICK, TAP, ALERT = 1, 2, 3  # clicker_kind as eng_sfx sets it
SHOTS = os.environ.get('POPSTALL_SHOTS')  # a folder: the screens are written there as PNGs


class Ball(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'x y ang live spin bounce stuck held cx'.split()]


class Effect(C.Structure):
    _fields_ = [*((k, C.c_short) for k in 'kind t x y'.split()), ('v', C.c_int)]


class Ticket(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'kind x t y'.split()]


class Stall(C.Structure):
    _fields_ = [('ball', Ball * 3), ('spd', C.c_int),
                *((k, C.c_int) for k in 'pad h mode t level lives side left combo swell seq runs wait'.split()),
                *((k, C.c_uint) for k in 'score seed rs'.split()),
                ('cell', C.c_ubyte * 112), ('squash', C.c_ubyte * 112), ('lean_x', C.c_byte * 112), ('lean_y', C.c_byte * 112),
                *((k, C.c_int) for k in 'dip_t dip_x dip_q wide_t slow_t glue_n frame'.split()),
                ('tk', Ticket * 2), ('fx', Effect * 20), ('chain', C.c_int * 6),
                *((k, C.c_int) for k in 'chain_n chain_t star_fx'.split()), ('bulbs', C.c_uint), ('flip', C.c_uint),
                *((k, C.c_int) for k in 'flip_t allon_t brown_t chase'.split()),
                *((k, C.c_int * 4) for k in 'hop_t hop_amp hop_wait'.split()),
                *((k, C.c_int) for k in 'lost_t gain_t slots paused best_new marks'.split()), ('mark', C.c_short * 32),
                *((k, C.c_uint) for k in 'magic best best_level sum saved'.split()), ('fresh', C.c_int), ('art_ok', C.c_int)]


def cx(r, c):
    return X0 + (r & 1) * PX / 2 + c * PX


def cy(r):
    return Y0 + r * PY


def cols(r):
    return COLS - (r & 1)


def on_lattice():
    return [(r, c) for r in range(ROWS) for c in range(cols(r))]


def png(path, buf, w=375, h=320):
    """The screen's BGRA8888 words as an RGB PNG."""
    raw = b''.join(b'\0' + b''.join(struct.pack('>I', buf[y * w + x])[1:] for x in range(w)) for y in range(h))
    chunk = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) +
                           chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


class Game:
    """Pop Stall open on the engine over Page's clock, timers and card, on the whole panel (320 rows); the sounds it
    made and the rects each frame marked are kept. art=False: no art on the card."""
    def __init__(self, fw, files=None, art=True, h=320):
        files = dict(files or {})
        if art:
            files.setdefault(ART_FILE, ART)
        self.fw, self.e, self.page = fw, api(fw), Page(fw, files)
        self.rects, self.sounds = [], []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.rects.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('buzzeer_switch', lambda *_: self.sounds.append(fw.var('clicker_kind', C.c_int).value))
        self.done = C.CFUNCTYPE(None)(lambda: None)
        self.game = fw.var('eng_popstall', GameDef)
        assert self.e['eng_open'](self.game, PAGE, SHEET, h, self.done, self.done) == 1
        self.b = Stall.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
        self.fn = {k: fw.fn(f'game_popstall__{k}', *v) for k, v in {
            'build': (None, C.c_void_p, C.c_int), 'around': (C.c_int, C.c_int, C.c_int, C.POINTER(C.c_int)),
            'steep': (C.c_int, C.c_int), 'step': (C.c_int, C.c_void_p, C.POINTER(Input)), 'pause': (None, C.c_void_p),
            'mat_depth': (C.c_int, C.c_void_p, C.c_int, C.c_int), 'lit': (C.c_uint, C.c_void_p),
            'hit': (None, C.c_void_p, C.c_int, C.c_int), 'lean': (None, C.c_void_p, C.c_int, C.c_int)}.items()}
        self.canvas = Canvas(fw)

    def step(self, wheel=0, held=0, pressed=0, tx=0, n=1):
        for _ in range(n):
            r = self.fn['step'](C.addressof(self.b), C.byref(Input(wheel, 0, 0, held, pressed, 0, tx, 0)))
            wheel = pressed = 0
        return r

    def frame(self, ms=16):
        """A pass of the frame timer: the rects it marked."""
        self.rects.clear()
        self.page.now += ms
        self.e['frame'](None)
        return list(self.rects)

    def key(self, key):
        self.e['eng_key'](key, 1), self.e['eng_key'](key, 0)

    def build(self, seed, level, bags=-1):
        self.b.seed, self.b.level = seed, level
        self.fn['build'](C.addressof(self.b), bags)
        return bytes(self.b.cell)

    def live(self):
        return [(r, c) for r, c in on_lattice() if self.b.cell[r * COLS + c]]

    def kinds(self):
        return {(r, c): self.b.cell[r * COLS + c] for r, c in self.live()}

    def clear_cells(self, keep=()):
        for i in range(ROWS * COLS):
            self.b.cell[i] = NONE
        for (r, c), k in dict(keep).items():
            self.b.cell[r * COLS + c] = k
        self.b.left = sum(1 for k in dict(keep).values() if k != SACK)

    def launch(self, x, y, ang, spd=5, k=0):
        self.b.mode = PLAYING
        o = self.b.ball[k]
        o.x, o.y, o.ang, o.live, o.stuck, o.bounce = int(x * FX), int(y * FX), ang, 1, 0, 0
        self.b.spd = int(spd * FX)

    def paint(self, clip=(0, 0, 375, 320)):
        """The sheet painted through eng_paint into a fresh screen; the buffer."""
        buf = screen(self.canvas, clip=clip, origin=(0, 0))
        self.canvas.ops.clear()
        self.e['eng_paint'](self.canvas.p)
        return buf

    def shot(self, name, buf=None):
        buf = buf if buf is not None else self.paint()
        if SHOTS:
            Path(SHOTS).mkdir(parents=True, exist_ok=True)
            png(Path(SHOTS) / f'{name}.png', buf)
        return buf

    def close(self):
        self.e['eng_close']()


def rgb(buf, x, y):
    return buf[y * 375 + x] & 0xffffff


# ---- the art ----

def test_the_art_file_is_sealed_and_the_size_the_game_reads():
    head = (ROOT / 'patch/game_popstall_art.h').read_text()
    size = int(head.split('#define ART_BYTES ')[1].split('u')[0])
    assert len(ART) == size and ART[:4] == b'PSA1' and int.from_bytes(ART[-4:], 'little') == fnv1a(ART[:-4])


def test_a_game_opens_ready_on_the_whole_panel_with_its_art_and_fits_its_heap(fw):
    g = Game(fw)
    b = g.b
    assert g.game.name == b'Pop Stall' and g.game.flags == 1 | FULL  # ENG_RAW, the whole panel
    assert g.game.size == C.sizeof(Stall) + len(ART) and g.game.size < 256 * 1024
    assert (b.mode, b.lives, b.level, b.score, b.h, b.pad, b.art_ok) == (READY, 3, 1, 0, 320, 187, 1)
    kinds = list(g.kinds().values())
    assert kinds.count(STAR) == 1 and SACK not in kinds and len(kinds) == b.left >= 12
    o = b.ball[0]
    assert o.live and o.y == REST * FX and o.x == (187 + b.side * 20) * FX  # on the mat, a third out
    g.close()


def test_without_its_art_the_game_still_plays_and_draws_flat_stand_ins(fw):
    g = Game(fw, art=False)
    assert g.b.art_ok == 0
    buf = g.paint()
    assert rgb(buf, 187, 150) == 0xb5885a and rgb(buf, 10, 150) == 0x7e4e2c  # the board and a wall, flat
    g.key(CENTER)
    g.frame()
    assert g.b.mode == PLAYING
    g.close()


def test_a_corrupt_or_short_art_file_is_not_drawn(fw):
    g = Game(fw, files={ART_FILE: ART[:-5] + b'\1' + ART[-4:]})
    assert g.b.art_ok == 0
    g.close()
    g = Game(fw, files={ART_FILE: ART[:1000]})
    assert g.b.art_ok == 0
    g.close()


# ---- levels ----

@pytest.mark.parametrize('level', [1, 2, 3, 7, 18, 40])
def test_levels_come_from_the_seed_and_the_level_alone_mirrored_and_never_empty(fw, level):
    g = Game(fw)
    seen = set()
    for seed in [1, 2, 3, 0x9e3779b9, 0xffffffff, *range(1000, 1060)]:
        a = g.build(seed, level)
        assert g.build(seed, level) == a
        seen.add(a)
        k = g.kinds()
        assert len([v for v in k.values() if v != SACK]) == g.b.left >= 10 and list(k.values()).count(STAR) == 1
        for (r, c), v in k.items():  # the mask is the mirror's; only the star may stand off the middle column alone
            m = k.get((r, cols(r) - 1 - c))
            assert m == v or STAR in (m, v), (seed, r, c)
        axis = [r for r in range(1, ROWS, 2) if (r, 7) in k]
        star = next(p for p, v in k.items() if v == STAR)
        assert not axis or star[1] == 7 and star[0] % 2 == 1  # on the middle column where it has a cell
    assert len(seen) > 20
    assert g.b.spd == min(int(2.5 * FX) + (level - 1) * (15 * FX // 100), 5 * FX)
    g.close()


def neighbours(r, c):
    return {(rr, cc) for rr, cc in on_lattice() if 0 < math.hypot(cx(rr, cc) - cx(r, c), cy(rr) - cy(r)) < 21}


@pytest.mark.parametrize('level, bags', [(1, 0), (2, 0), (3, 2), (4, 2), (5, 4), (7, 6), (30, 6)])
def test_sandbags_hang_in_mirrored_pairs_from_level_3_never_touching_a_bag_or_the_star(fw, level, bags):
    g = Game(fw)
    counts = []
    for seed in range(1, 120):
        g.build(seed * 2654435761 % 2**32 or 1, level)
        k = g.kinds()
        sacks = [p for p, v in k.items() if v == SACK]
        counts.append(len(sacks))
        assert len(sacks) <= bags and len(sacks) % 2 == 0
        star = next(p for p, v in k.items() if v == STAR)
        for r, c in sacks:
            assert 1 <= r <= 5 and k.get((r, cols(r) - 1 - c)) == SACK  # rows 2 to 6, the mirror a bag too
            assert star not in neighbours(r, c)
            assert not any(k.get(n) == SACK for n in neighbours(r, c))
    assert max(counts) == bags and (bags == 0 or sum(counts) / len(counts) > bags / 2)
    g.close()


@pytest.mark.parametrize('r, c', [(0, 0), (0, 7), (0, 15), (1, 0), (1, 7), (1, 14), (2, 0), (3, 14), (4, 15), (5, 3), (6, 0),
                                  (6, 15)])
def test_a_stars_neighbours_are_the_six_touching_cells_on_either_parity(fw, r, c):
    g = Game(fw)
    out = (C.c_int * 6)()
    n = g.fn['around'](r, c, out)
    got = {(i // COLS, i % COLS) for i in out[:n]}
    assert got == neighbours(r, c) and (n == 6) == (0 < r < ROWS - 1 and 0 < c < cols(r) - 1)
    g.close()


# ---- the ball ----

@pytest.mark.parametrize('ang', range(0, 1024, 7))
def test_no_path_is_shallower_than_15_degrees_from_the_horizontal(fw, ang):
    g = Game(fw)
    out = g.fn['steep'](ang)
    h = out & 511
    assert 0 <= out < 1024 and 43 <= h <= 512 - 43 and (out < 512) == (ang % 1024 < 512)
    if 43 <= ang % 512 <= 512 - 43:
        assert out == ang % 1024
    g.close()


def segment_gap(p, q, c):
    (px, py), (qx, qy), (cx_, cy_) = p, q, c
    dx, dy = qx - px, qy - py
    t = 0 if not (dx or dy) else max(0, min(1, ((cx_ - px) * dx + (cy_ - py) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px + t * dx - cx_, py + t * dy - cy_)


def test_a_ball_at_top_speed_never_tunnels_through_a_full_lattice(fw):
    """200 seeded launches at 5 px a step into a lattice of sandbags (they never pop, so it stays full): no step ends
    inside a cell, and a step that turned nothing never crossed one."""
    g = Game(fw)
    b = g.b
    rnd = random.Random(2024)
    hits = 0
    for launch in range(200):
        g.clear_cells({p: SACK for p in on_lattice()})
        b.left, b.level = 109, 30
        g.launch(rnd.uniform(L + RB, R - RB), rnd.uniform(cy(ROWS - 1) + 16, MAT - 20), rnd.randrange(512 + 43, 1024 - 43))
        o = b.ball[0]
        for _ in range(80):
            before, ang = (o.x / FX, o.y / FX), o.ang
            b.pad = max(54, min(321, o.x // FX))
            g.step()
            after = (o.x / FX, o.y / FX)
            assert math.hypot(after[0] - before[0], after[1] - before[1]) <= 5.01
            for r, c in on_lattice():
                if abs(cx(r, c) - after[0]) < 40 and abs(cy(r) - after[1]) < 40:
                    assert math.hypot(after[0] - cx(r, c), after[1] - cy(r)) >= RB + RC - .1, (launch, r, c)
                    if ang == o.ang:
                        assert segment_gap(before, after, (cx(r, c), cy(r))) >= RB + RC - .1, (launch, r, c)
            hits += ang != o.ang
            assert L + RB - .1 <= after[0] <= R - RB + .1 and after[1] >= TOP + RB - .1
            if not o.live or b.mode != PLAYING:
                break
    assert hits > 400
    g.close()


def test_the_trampoline_sends_the_ball_up_by_where_it_hit_and_dips_there(fw):
    g = Game(fw)
    b = g.b
    for off, want in ((0, 768), (37, 768 + 171), (-37, 768 - 171), (20, 768 + 20 * 171 // 37)):
        b.pad = 187
        g.launch(187 + off, MAT - RB - 1, 256, spd=3)
        g.step()
        o = b.ball[0]
        assert o.ang == want and o.bounce == 4 and o.cx == 187 + off, off
        assert b.dip_t == 0 and b.dip_x == 187 + off and b.dip_q == 8
    g.close()


def test_the_dip_goes_down_4_px_overshoots_and_rests_in_11_frames_pinned_at_the_ends(fw):
    g = Game(fw)
    b = g.b
    b.pad, b.dip_x, b.dip_q = 187, 187, 8
    depth = lambda px: g.fn['mat_depth'](C.addressof(b), px, PAD_W)
    curve = (2, 4, 3.75, 2.75, 1.25, 0, -1, -1.5, -1.25, -.75, -.25, 0)  # fair.py DIP_CURVE

    def design(px, t):  # fair.py mat_dip at the column's middle, the trampoline's middle 187.5
        u, edge = (px + .5 - 187) / 20, max(0, min(1, (30 - abs(px + .5 - 187.5)) / 6))
        return curve[t] * max(0, 1 - u * u) ** 2 * edge
    for t in range(12):
        b.dip_t = t
        for px in range(150, 225):
            want = design(px, t)
            assert abs(depth(px) - want) <= .5 + 1e-9, (t, px, depth(px), want)
        assert depth(187 - 29) == 0 and depth(187 + 29) == 0  # the mat's ends stay put
        assert depth(187 + 21) == 0  # the hollow is 20 px either side
    assert [(setattr(b, 'dip_t', t), depth(187))[1] for t in range(12)][:3] == [2, 4, 4]  # down 4 px in two frames
    b.dip_t = 12
    assert depth(187) == 0
    g.close()


def test_the_trampoline_follows_the_wheel_the_keys_and_a_finger_and_play_does_nothing(fw):
    g = Game(fw)
    b = g.b
    g.step(wheel=5)
    assert b.pad == 197 and b.ball[0].x == (197 + b.side * 20) * FX
    g.step(wheel=-500)
    assert b.pad == L + PAD_W // 2
    g.step(held=E_NEXT), g.step(held=E_NEXT)
    assert b.pad == L + PAD_W // 2 + 8
    g.step(held=E_PREV)
    assert b.pad == L + PAD_W // 2 + 4
    g.step(held=E_TOUCH, tx=300)
    assert b.pad == 300
    g.step(held=E_TOUCH, tx=900)
    assert b.pad == R - PAD_W // 2
    g.sounds.clear()
    g.step(pressed=E_PLAY, held=E_PLAY)
    assert b.mode == READY and not g.sounds
    fw.var('wheel_travel', C.c_int).value = 3  # through the engine: the wheel's x falls clockwise
    g.frame()
    assert b.pad == R - PAD_W // 2 - 6
    g.close()


def test_centre_launches_at_the_level_speed(fw):
    g = Game(fw)
    b = g.b
    g.frame()
    g.key(CENTER)
    g.frame()
    o = b.ball[0]
    assert b.mode == PLAYING and o.ang == 768 + b.side * 171 * 20 // 37
    x, y = o.x, o.y
    g.frame()
    assert math.hypot(o.x - x, o.y - y) / FX == pytest.approx(2.5, abs=.02)
    g.close()


# ---- balloons, combos, the star ----

def test_a_pop_scores_the_level_times_the_combo_and_the_trampoline_resets_it(fw):
    g = Game(fw)
    b = g.b
    row = [(4, c) for c in range(3, 13)]
    g.clear_cells({p: BAL for p in row})
    b.level, b.score = 2, 0
    for n, (r, c) in enumerate(row[:4]):
        g.launch(cx(r, c), cy(r) + 16, 768, spd=2)
        g.sounds.clear()
        g.step()
        assert b.cell[r * COLS + c] == NONE and b.combo == n + 1 and g.sounds == [TAP]
    assert b.score == 2 * (1 + 2 + 3 + 4) and b.left == 6
    pops = [e for e in b.fx if e.kind == F_POP]
    assert len(pops) == 4 and all(e.v & 255 == 4 for e in pops)  # the blue row's pops
    g.launch(187, MAT - RB - 1, 256, spd=2)
    g.step()
    assert b.combo == 0  # a trampoline touch
    g.close()


def test_the_combo_counts_to_ten_for_points_and_shows_past_it(fw):
    g = Game(fw)
    b = g.b
    row = [(2, c) for c in range(16)]
    g.clear_cells({p: BAL for p in row})
    b.level, b.score = 1, 0
    for n, (r, c) in enumerate(row[:12]):
        g.launch(cx(r, c), cy(r) + 16, 768, spd=2)
        g.step()
    assert b.combo == 12 and b.score == sum(min(k, 10) for k in range(1, 13))
    assert g.fn['lit'](C.addressof(b)) == 0xffffffff  # x10: all 32 bulbs on
    g.close()


def test_a_double_skin_takes_two_hits_and_its_strip_counts_in_the_combo(fw):
    g = Game(fw)
    b = g.b
    g.clear_cells({(6, 7): DBL, (0, 0): BAL})
    score = b.score
    g.launch(cx(6, 7), cy(6) + 16, 768, spd=2)
    g.sounds.clear()
    g.step()
    assert b.cell[6 * COLS + 7] == BAL and b.squash[6 * COLS + 7] == 3 and b.left == 2 and b.combo == 1
    assert b.score == score + 1 and g.sounds == [TICK] and b.ball[0].ang < 512
    assert any(e.kind == F_STRIP for e in b.fx)
    g.close()


def test_a_sandbag_never_pops_swings_away_and_scores_nothing(fw):
    g = Game(fw)
    b = g.b
    g.clear_cells({(3, 7): SACK, (0, 0): BAL})
    g.launch(cx(3, 7) - 4, cy(3) + 15, 768, spd=2)
    g.step()
    assert b.cell[3 * COLS + 7] == SACK and b.combo == 0 and b.score == 0 and b.left == 1
    for _ in range(3):
        g.step()
    assert b.lean_x[3 * COLS + 7] > 0  # hit from its left: it swings right
    for _ in range(10):
        g.step()
    assert b.lean_x[3 * COLS + 7] == 0
    g.close()


def test_the_star_pops_its_ring_two_frames_apart_counting_up_the_combo_with_no_ticket(fw):
    g = Game(fw)
    b = g.b
    ring = neighbours(3, 7)
    g.clear_cells({(3, 7): STAR, **{p: BAL for p in ring}, (0, 0): BAL})
    b.level, b.score, b.rs = 3, 0, 8  # rs: a ticket on every pop it is asked for
    g.launch(cx(3, 7), 250, 768, spd=2)
    g.fn['hit'](C.addressof(b), 3 * COLS + 7, 187)  # the ring hems it in: the touch itself
    assert b.cell[3 * COLS + 7] == NONE and b.chain_n == 6 and b.combo == 1
    order = [(i // COLS, i % COLS) for i in b.chain[:6]]
    assert set(order) == ring and order[0] == (2, 7) and order[-1] == (3, 6)  # from the upper left round to the left
    gone = []
    for _ in range(12):
        g.step()
        gone.append(sum(b.cell[r * COLS + c] == NONE for r, c in ring))
    assert gone == [0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6]  # two frames apart
    assert b.combo == 7 and b.score == 3 * sum(range(1, 8)) and b.left == 1
    assert not any(t.kind for t in b.tk)  # the chain drops no tickets
    assert b.allon_t > 0 and any(e.kind == F_CALL and e.v == 5 for e in b.fx)  # GRAND PRIZE!
    g.close()


# ---- tickets ----

def drop(g, kind, x=None):
    b = g.b
    b.tk[0].kind, b.tk[0].x, b.tk[0].y, b.tk[0].t = kind, x if x is not None else b.pad, (MAT - 20) * FX, 0


@pytest.mark.parametrize('kind', [T_WIDE, T_TRIO, T_GLUE, T_SLOW, T_LIFE])
def test_a_ticket_caught_on_the_trampoline_takes_effect(fw, kind):
    g = Game(fw)
    b = g.b
    g.launch(100, 150, 800, spd=2)
    drop(g, kind)
    for _ in range(12):
        g.step()
    assert b.tk[0].kind == 0
    if kind == T_WIDE:
        assert b.wide_t > 0
        b.pad = 187
        g.launch(187 + 50, MAT - RB - 1, 256, spd=2)
        g.step()
        assert b.ball[0].bounce == 4  # 45 px out: on the 90 px trampoline
    elif kind == T_TRIO:
        live = [o for o in b.ball if o.live]
        assert len(live) == 3 and sorted({o.ang for o in live}) == sorted({o.ang for o in live})
        angs = sorted(o.ang for o in live)
        assert angs[1] - angs[0] == 85 or angs[2] - angs[1] == 85
    elif kind == T_GLUE:
        assert b.glue_n == 3
        g.launch(b.pad + 5, MAT - RB - 1, 256, spd=2)
        g.step()
        o = b.ball[0]
        assert o.stuck and o.y == REST * FX and b.glue_n == 2
        g.step(wheel=10)
        assert o.x == (b.pad + 5) * FX  # it rides the mat
        g.step(pressed=E_CENTRE)
        assert not o.stuck and o.ang < 768 + 171
    elif kind == T_SLOW:
        assert 590 < b.slow_t <= 600
        o = b.ball[0]
        x, y = o.x, o.y
        g.step()
        assert math.hypot(o.x - x, o.y - y) / FX == pytest.approx(1.2, abs=.02)
    else:
        assert b.lives == 4 and b.slots == 4
    g.close()


def test_glue_lets_go_by_itself_after_two_seconds(fw):
    g = Game(fw)
    b = g.b
    b.glue_n = 3
    g.launch(b.pad, MAT - RB - 1, 256, spd=2)
    g.step()
    assert b.ball[0].stuck
    g.step(n=119)
    assert b.ball[0].stuck
    g.step()
    assert not b.ball[0].stuck


def test_a_ticket_missed_falls_away_and_a_sixth_life_scores_instead(fw):
    g = Game(fw)
    b = g.b
    g.launch(100, 150, 800, spd=2)
    drop(g, T_LIFE, x=b.pad + 120)
    for _ in range(40):
        g.step()
    assert b.tk[0].kind == 0 and b.lives == 3
    b.lives, b.score = 5, 0
    drop(g, T_LIFE)
    for _ in range(12):
        g.step()
    assert b.lives == 5 and b.score == 50
    g.close()


def test_a_popped_balloon_drops_a_ticket_now_and_then_two_at_most(fw):
    g = Game(fw)
    b = g.b
    dropped = 0
    for seed in range(1, 400):
        b.rs = seed
        g.clear_cells({(4, 7): BAL, (0, 0): BAL})
        b.tk[0].kind = b.tk[1].kind = 0
        g.launch(cx(4, 7), cy(4) + 16, 768, spd=2)
        g.step()
        dropped += b.tk[0].kind != 0
    assert 25 < dropped < 75  # one in eight
    for seed in range(1, 100):  # two already falling: no third
        b.rs, b.tk[0].kind, b.tk[1].kind, b.tk[0].y, b.tk[1].y = seed, T_WIDE, T_SLOW, 100 * FX, 100 * FX
        g.clear_cells({(4, 7): BAL, (0, 0): BAL})
        g.launch(cx(4, 7), cy(4) + 16, 768, spd=2)
        g.step()
        assert (b.tk[0].kind, b.tk[1].kind) == (T_WIDE, T_SLOW)
        b.tk[0].kind = b.tk[1].kind = 0
    g.close()


# ---- lives, the clear, the end ----

def test_a_life_is_lost_only_when_the_last_ball_goes_behind_the_counter(fw):
    g = Game(fw)
    b = g.b
    g.launch(300, 290, 256 + 20, spd=5, k=0)
    g.launch(100, 150, 800, spd=2, k=1)
    b.wide_t = 500
    for _ in range(8):
        g.step()
    assert not b.ball[0].live and b.ball[1].live and b.lives == 3 and b.wide_t > 0
    b.ball[1].y, b.ball[1].ang = 302 * FX, 256
    b.pad = 40
    g.sounds.clear()
    g.step()
    assert b.lives == 2 and b.mode == READY and g.sounds[0] == ALERT and b.wide_t == 0 and b.brown_t == 29
    assert b.ball[0].live and b.ball[0].bounce == 4 and b.dip_t == 0  # the next ball drops onto the mat
    g.close()


def test_the_third_miss_ends_the_game_with_a_save_when_beaten(fw):
    g = Game(fw)
    b = g.b
    b.score = 120
    for life in (2, 1, 0):
        g.launch(b.pad + 100, 300, 256, spd=5)
        g.step()
        assert b.lives == life
    assert b.mode == OVER and b.best_new
    saved = g.page.card.files[FILE]
    assert saved[:4] == b'PTS1' and int.from_bytes(saved[4:8], 'little') == 120 and int.from_bytes(saved[8:12], 'little') == 1
    assert int.from_bytes(saved[12:], 'little') == fnv1a(saved[:12]) and len(saved) == 16
    g.step(pressed=E_CENTRE)
    assert b.mode == OVER  # the ticket is still sliding up
    g.step(n=16)
    g.step(pressed=E_CENTRE)
    assert (b.mode, b.lives, b.score, b.level) == (READY, 3, 0, 1)
    g.close()


def test_clearing_the_balloons_drops_the_banner_then_inflates_the_next_level_row_by_row(fw):
    g = Game(fw)
    b = g.b
    g.clear_cells({(6, 7): BAL, (3, 2): SACK, (3, 12): SACK})
    b.score, b.level = 0, 3
    g.launch(cx(6, 7), cy(6) + 16, 768, spd=2)
    g.sounds.clear()
    g.step()
    assert b.mode == CLEAR and b.level == 4 and b.score == 3 + 300 and g.sounds == [TAP, TICK]
    g.step(n=99)
    assert b.mode == CLEAR
    g.step()
    assert b.mode == INFLATE and b.left >= 10
    while b.mode == INFLATE:
        g.step()
    assert b.mode == READY and b.t == 21
    g.close()


def blob(best, level, magic=MAGIC):
    raw = b''.join(w.to_bytes(4, 'little') for w in (magic, best, level))
    return raw + fnv1a(raw).to_bytes(4, 'little')


@pytest.mark.parametrize('files, best, level', [
    ({}, 0, 0),
    ({FILE: blob(4321, 6)}, 4321, 6),
    ({FILE: blob(4321, 6)[:9] + b'\x01' + blob(4321, 6)[10:]}, 0, 0),  # torn or flipped
    ({FILE: blob(4321, 6)[:12]}, 0, 0),  # short
    ({FILE: blob(4321, 6, magic=0x314b5242)}, 0, 0),  # Bricks' save, still on the card: not read
    ({FILE: blob(100000000, 6)}, 0, 0),  # sealed but no game's
    ({FILE: blob(10, 100)}, 0, 0),
])
def test_a_save_loads_only_whole_and_sane(fw, files, best, level):
    g = Game(fw, files=files)
    assert (g.b.best, g.b.best_level, g.b.saved) == (best, level, best + level)
    g.close()


def test_a_save_that_fails_at_the_end_is_tried_again_at_a_pause_and_nothing_saves_mid_play(fw):
    g = Game(fw)
    b = g.b
    fw.mock('navigator_to_with_context', lambda *_: 0)  # the toast
    b.score = 50
    g.e['eng_hold'](), g.e['eng_run']()
    assert FILE not in g.page.card.files
    g.page.card.fail = True
    b.lives = 1
    g.launch(b.pad + 100, 300, 256)
    g.step()
    assert b.mode == OVER and FILE not in g.page.card.files and b.saved == 0
    g.page.card.fail = False
    g.e['eng_hold']()
    assert int.from_bytes(g.page.card.files[FILE][4:8], 'little') == 50 and b.saved == 51
    g.close()


# ---- frames: what a step marks ----

def test_a_frame_of_play_marks_only_what_moved(fw):
    g = Game(fw)
    b = g.b
    g.frame(), g.frame()
    assert g.frame() == []  # ready and still: nothing to paint
    g.key(CENTER)
    g.frame()
    for _ in range(5):
        rects = g.frame()
        assert 1 <= len(rects) <= 2 and all(w * h < 60 * 60 for _, _, w, h in rects), rects
    g.close()


# ---- drawing ----

def lit_under_glass(points):
    out = []
    for x, y in points:
        ox, oy = (80 - x if x < 80 else x - 295), (80 - y if y < 80 else y - 240)
        if ox > 0 and oy > 0 and math.hypot(ox, oy) > 80:
            out.append((x, y))
    return out


def test_nothing_the_player_reads_or_the_ball_touches_falls_under_the_glass(fw):
    """The stall itself runs into the corners (the glass hides its roof and counter there); the HUD, the text, every
    balloon and wherever the ball can be stay clear of them."""
    g = Game(fw)
    b = g.b
    inset = lambda y: g.e['eng_inset'](y)
    for r, c in on_lattice():  # every balloon's box
        for x, y in ((cx(r, c) - 9, cy(r) - 10), (cx(r, c) + 9, cy(r) - 10)):
            assert inset(int(y)) <= x <= 375 - inset(int(y))
    for y in range(TOP + RB, MAT):  # the ball's edge between the walls, wherever it is
        assert inset(y) <= L
    g.paint()  # the HUD's plaques: no ink past the glass at their rows
    for y in (14, 28):
        assert inset(y) <= 36 + 1 and 375 - inset(y) >= 339 - 1
    g.close()


def play_cells(g):
    """render.py play_cells(): level 3 of seed 0x51AA with four bags, the lower right carved out."""
    b = g.b
    b.seed, b.level = 0x51AA, 3
    g.fn['build'](C.addressof(b), 4)
    for r, c in on_lattice():
        if b.cell[r * COLS + c] != SACK and math.hypot(cx(r, c) - 258, (cy(r) - 176) * 1.1) < 50:
            b.cell[r * COLS + c] = NONE
    b.left = sum(1 for r, c in on_lattice() if b.cell[r * COLS + c] in (BAL, DBL, STAR))


def nearest(g, x, y, kinds=(BAL,)):
    return min(((r, c) for r, c in on_lattice() if g.b.cell[r * COLS + c] in kinds), key=lambda p: math.hypot(cx(*p) - x, cy(p[0]) - y))


def fx(g, kind, t, x, y, v):
    e = next(e for e in g.b.fx if not e.kind)
    e.kind, e.t, e.x, e.y, e.v = kind, t, int(x), int(y), v


def ball(g, x, y, vx=0.0, vy=0.0, k=0, **kw):
    o = g.b.ball[k]
    o.x, o.y, o.live = int(x * FX), int(y * FX), 1
    o.ang = round(math.atan2(vy, vx) * 512 / math.pi) % 1024 if vx or vy else 768
    for key, v in kw.items():
        setattr(o, key, v)


def pop_at(g, x, y, f, kinds=(BAL,)):
    """The balloon nearest x, y popped f frames ago (render.py's pop frame 2 is f 4 to 6): its neighbours leaning."""
    r, c = nearest(g, x, y, kinds)
    i = r * COLS + c
    gold = g.b.cell[i] == STAR
    g.b.cell[i] = NONE
    if f < 4:
        g.fn['lean'](C.addressof(g.b), i, 1)
    fx(g, F_POP, f, cx(r, c), cy(r), (7 if gold else r) | i << 8)
    return r, c


def scene_ready(g):
    b = g.b
    b.seed, b.level, b.side = 0x51AC, 1, 1
    g.fn['build'](C.addressof(b), -1)
    ball(g, 187 + 20, REST)


def scene_play(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.pad = PLAYING, 384, 232
    ball(g, 214, 228, 2.2, -2.0)


def scene_pop(g):
    play_cells(g)
    b = g.b
    r, c = pop_at(g, 240, 150, 4)
    b.mode, b.score, b.combo, b.swell, b.pad = PLAYING, 438, 2, 4, 268
    fx(g, F_SCORE, 0, cx(r, c) + 3, cy(r) - 17, 6)
    ball(g, cx(r, c) + 10, cy(r) + 23, 1.4, 2.6)


def scene_skin(g):
    play_cells(g)
    b = g.b
    r, c = nearest(g, 150, 170, (DBL,))
    b.cell[r * COLS + c], b.squash[r * COLS + c] = BAL, 3
    fx(g, F_STRIP, 1, cx(r, c), cy(r), r)
    fx(g, F_SCORE, 0, cx(r, c) + 2, cy(r) - 18, 9)
    b.mode, b.score, b.combo, b.swell, b.pad = PLAYING, 630, 3, 4, 120
    ball(g, cx(r, c) - 9, cy(r) + 22, -1.3, 2.7)


def scene_bag(g):
    play_cells(g)
    b = g.b
    r, c = nearest(g, 200, 150, (SACK,))
    b.lean_x[r * COLS + c] = 3
    fx(g, F_DUST, 2, cx(r, c) - 4, cy(r) + 9, 0x100 | 5)
    b.mode, b.score, b.combo, b.pad = PLAYING, 630, 3, 150
    ball(g, cx(r, c) - 11, cy(r) + 22, -1.6, 2.5)


def scene_star(g):
    play_cells(g)
    b = g.b
    r, c = nearest(g, 0, 0, (STAR,))
    ring = [p for p in neighbours(r, c) if b.cell[p[0] * COLS + p[1]] in (BAL, DBL)]
    ring.sort(key=lambda p: math.atan2(cy(p[0]) - cy(r), cx(*p) - cx(r, c)))
    b.cell[r * COLS + c] = NONE
    fx(g, F_POP, 2, cx(r, c), cy(r), 7 | (r * COLS + c) << 8)
    for k, (rr, cc) in enumerate(ring):  # the chain round the ring, two frames a balloon
        b.cell[rr * COLS + cc] = NONE
        fx(g, F_POP, (5, 5, 3, 3, 1, 1)[k % 6], cx(rr, cc), cy(rr), rr | (rr * COLS + cc) << 8)
    fx(g, F_STARS, 4, cx(r, c), cy(r), 0)
    fx(g, F_CALL, 0, cx(r, c), cy(r) - 44, 5)
    fx(g, F_SCORE, 0, cx(r, c), cy(r) - 29, 165 | 1 << 24)
    b.mode, b.score, b.combo, b.pad, b.allon_t = PLAYING, 1284, 10, 150, 30
    for k, d in enumerate((-2, 2, 2, -2)):
        b.hop_t[k], b.hop_amp[k] = 7, -d
    ball(g, cx(r, c) - 17, cy(r) + 36, -1.6, 2.4)


def scene_combo(g, n):
    play_cells(g)
    b = g.b
    for r, cc in on_lattice():
        if b.cell[r * COLS + cc] in (BAL, DBL) and math.hypot(cx(r, cc) - 150, cy(r) - 178) < (30 if n < 10 else 52):
            b.cell[r * COLS + cc] = NONE
    r, c = pop_at(g, 150, 150 if n < 10 else 128, 2)
    fx(g, F_SCORE, 0, cx(r, c) + 2, cy(r) - 18, 3 * n | (n >= 10) << 24)
    b.mode, b.score, b.combo, b.swell, b.pad = PLAYING, 921 if n < 10 else 1176, n, 4, 200
    if n >= 10:
        b.hop_t[0], b.hop_amp[0], b.hop_t[2], b.hop_amp[2] = 7, 2, 7, 2
    ball(g, cx(r, c) + 9, cy(r) + 22, 1.2, 2.8)


def scene_ticket(g):
    play_cells(g)
    b = g.b
    r, c = pop_at(g, 118, 160, 8)
    b.tk[0].kind, b.tk[0].x, b.tk[0].y, b.tk[0].t = T_WIDE, int(cx(r, c)) + 5, int((cy(r) + 56) * FX), 0
    b.mode, b.score, b.combo, b.pad = PLAYING, 512, 3, 150
    ball(g, 214, 228, 2.2, -2.0)


def caught(g, pad, kind, callout):
    play_cells(g)
    b = g.b
    r, c = nearest(g, 118, 160)
    b.cell[r * COLS + c] = NONE
    b.mode, b.pad = PLAYING, pad
    fx(g, F_CALL, 0, pad, 256, callout)
    if kind in (T_WIDE, T_LIFE):
        fx(g, F_SPARK, 0, pad, 280, kind)


def scene_wide(g):
    caught(g, 126, T_WIDE, 0)
    g.b.score, g.b.combo, g.b.wide_t, g.b.dip_t, g.b.dip_x, g.b.dip_q = 562, 3, 1000, 1, 126, 3
    g.b.hop_t[0], g.b.hop_amp[0] = 7, 3
    ball(g, 262, 166, 1.8, -2.4)


def scene_trio(g):
    caught(g, 205, T_TRIO, 1)
    g.b.score, g.b.combo = 846, 4
    play_cells(g)
    for k, (x, y, vx, vy) in enumerate(((150, 206, -2.3, -1.9), (204, 226, .2, -3), (262, 212, 2.4, -1.8))):
        ball(g, x, y, vx, vy, k=k)


def scene_glue(g):
    caught(g, 168, T_GLUE, 2)
    g.b.ball[0].live = 0
    g.b.score, g.b.glue_n = 904, 2
    g.b.fx[0].y = 226
    ball(g, 180, REST, stuck=1, cx=12)


def scene_slow(g):
    caught(g, 250, T_SLOW, 3)
    g.b.score, g.b.combo, g.b.slow_t, g.b.frame = 958, 2, 500, 3
    ball(g, 236, 222, -.9, -1.6)


def scene_life(g):
    caught(g, 140, T_LIFE, 4)
    g.b.score, g.b.combo, g.b.lives, g.b.slots, g.b.gain_t = 1012, 2, 4, 4, 30
    for k in (1, 3):
        g.b.hop_t[k], g.b.hop_amp[k] = 7, 3
    ball(g, 250, 190, 2, -2.2)


def scene_bounce(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.pad, g.b.dip_t, g.b.dip_x, g.b.dip_q = PLAYING, 1284, 150, 1, 167, 8
    ball(g, 168, REST - 3, 1.5, -3, bounce=3)


def scene_wall(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.pad = PLAYING, 1284, 250
    fx(g, F_KNOCK, 0, 354, 212, 1)
    fx(g, F_DUST, 3, 349, 214, 0x200 | 5)
    ball(g, 336, 200, -2.6, -2.0)


def scene_beam(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.combo, g.b.pad, g.b.flip, g.b.flip_t = PLAYING, 1284, 1, 250, 3 << 10, 5
    fx(g, F_KNOCK, 0, 289, 53, 2)
    fx(g, F_DUST, 3, 290, 58, 0x200 | 5)
    ball(g, 298, 74, 1.6, 2.8)


def scene_miss(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.lives, g.b.pad = PLAYING, 1284, 2, 150
    ball(g, 96, 284, .9, 3.8)


def scene_new_ball(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.lives, g.b.pad, g.b.brown_t, g.b.lost_t = READY, 1284, 2, 150, 20, 20
    g.b.dip_t, g.b.dip_x, g.b.dip_q, g.b.side, g.b.fresh = 2, 150, 8, 0, 0
    for k in range(4):
        g.b.hop_t[k], g.b.hop_amp[k] = 7, -2
    fx(g, F_DUST, 3, 99, 289, 0x300 | 7)
    ball(g, 150, REST, bounce=4, cx=150)


def scene_clear(g):
    g.clear_cells({})
    g.b.mode, g.b.t, g.b.score, g.b.level, g.b.pad = CLEAR, 20, 2980, 4, 215
    for k, a in enumerate((3, 1, 1, 3)):
        g.b.hop_t[k], g.b.hop_amp[k] = 7, a
    ball(g, 215, REST)


def scene_inflate(g):
    b = g.b
    b.seed, b.level = 0x51AD, 4
    g.fn['build'](C.addressof(b), -1)
    b.mode, b.t, b.score, b.pad = INFLATE, 12, 2980, 215
    ball(g, 235, REST)


def scene_over(g, best):
    play_cells(g)
    b = g.b
    b.mode, b.t, b.score, b.lives, b.pad, b.best_new = OVER, 20, 3120, 0, 150, best
    b.ball[0].live = 0
    b.best, b.best_level = (3120, 3) if best else (4820, 5)
    for k in range(4):
        b.hop_t[k], b.hop_amp[k] = 7, -2


def scene_paused(g):
    play_cells(g)
    g.b.mode, g.b.score, g.b.lives, g.b.pad, g.b.paused = PLAYING, 1284, 2, 150, 1
    ball(g, 214, 228, 2.2, -2.0)


SCENES = {'01-ready': scene_ready, '02-play': scene_play, '03-pop': scene_pop, '04-double-skin': scene_skin,
          '05-sandbag': scene_bag, '06-star-balloon': scene_star, '07-combo-x5': lambda g: scene_combo(g, 5),
          '08-combo-x10': lambda g: scene_combo(g, 10), '09-ticket-fall': scene_ticket, '10-wide': scene_wide,
          '11-trio': scene_trio, '12-glue': scene_glue, '13-slow': scene_slow, '14-life': scene_life,
          '15-bounce': scene_bounce, '16-wall-hit': scene_wall, '17-beam-hit': scene_beam, '18-miss': scene_miss,
          '19-new-ball': scene_new_ball, '20-level-clear': scene_clear, '21-next-level': scene_inflate,
          '22-game-over': lambda g: scene_over(g, 0), '23-high-score': lambda g: scene_over(g, 1), '24-paused': scene_paused}


@pytest.mark.parametrize('name', SCENES)
def test_each_screen_of_the_design_paints_whole_with_its_parts_in_place(fw, name):
    """Each of the design's 24 screens set up as render.py draws it and painted whole (with POPSTALL_SHOTS set,
    written there to compare with the mockups): the stall's art behind, the HUD on the roof, and the ball,
    balloons and text all clear of the glass."""
    g = Game(fw)
    b = g.b
    for e in b.fx:
        e.kind = 0
    b.combo = b.swell = 0
    SCENES[name](g)
    if b.mode != OVER:
        b.bulbs = g.fn['lit'](C.addressof(b))
    if b.paused:
        g.e['eng_hold']()
    buf = g.shot(name)
    inset = lambda y: g.e['eng_inset'](y)
    ink = 0x1b1240
    y = 38 if b.paused else 21
    row = [x for x in range(375) if rgb(buf, x, y) == ink]
    assert row and min(row) >= inset(y - 7) and max(row) <= 375 - inset(y - 7)  # the plaques clear of the glass
    if b.paused:
        assert rgb(buf, 187, 10) == 0  # the status row's band is stock's: nothing drawn there
    g.close()


# ---- Games' page ----

def test_pop_stall_is_games_first_engine_row_on_the_whole_panel_and_pauses_its_own_way(fw):
    p = Page(fw, {ART_FILE: ART})
    assert p.s.engines == 1 and p.s.rows == 1
    moved, shown = [], []
    fw.mock('widget_move_resize', lambda w, x, y, ww, hh, *_: moved.append((x, y, ww, hh)) or 0)
    bar = C.create_string_buffer(0x400)  # stock's system_bar, the status row's window
    fw.var('fw_ctx_system_bar', C.c_void_p).value = C.addressof(bar)
    fw.mock('widget_set_visible', lambda w, on, *_: shown.append(on) if w == C.addressof(bar) else None)
    p.go(ENGINE)
    assert shown[-1] == 0  # running: no status row over the stall
    assert p.s.screen == ENGINE and api(fw)['eng_live']() and moved[-1] == (0, 0, 375, 320)
    assert Eng.from_address(fw.var('engine__en', C.c_void_p).value).h == 320
    canvas = Canvas(fw)
    buf = screen(canvas, origin=(0, 0))

    def said():
        canvas.ops.clear()
        return [t.lower() for t in texts(paint(p, canvas))]
    assert not said()  # all of it the stall's own art
    p.key(RETURN)  # Return: Games' Continue / Quit, over the stall's own pause
    assert p.s.paused == 2 and not api(fw)['eng_live']() and 'continue' in said() and shown[-1] == 1
    p.key(RETURN)
    assert p.s.paused == 0 and shown[-1] == 0
    fw.var('g_backlight_status', C.c_ubyte).value = 0  # the screen off: held, with no pop-up over the stall's sign
    api(fw)['frame'](None)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert p.s.paused == 1 and not said() and buf[150 * 375 + 187] != 0
    p.key(CENTER)
    assert p.s.paused == 0
    p.go(0)
    assert moved[-1] == (0, 58, 375, 262) and shown[-1] == 1  # the chooser's sheet under the status row again
    p.close()
    assert not fw.var('engine__en', C.c_void_p).value
