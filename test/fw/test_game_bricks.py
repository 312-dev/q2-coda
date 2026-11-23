"""Bricks (patch/game_bricks.c) on the engine: the lattice and its levels, the ball's swept path, the paddle's
inputs, the keystone, lives, the fade, saves, and the glass's corners; then through Games' page."""
import ctypes as C
import math
import random

import pytest

from conftest import O
from test_engine import (CENTER, DEMO, E_CENTRE, E_NEXT, E_PLAY, E_PREV, E_TOUCH, ENGINE, PLAY, RETURN, SHEET, Eng,
                         GameDef, Input, api, screen)
from test_games import PAGE, Canvas, Page, fnv1a, paint, texts, words

ROWS, COLS, PX, PY, X0, Y0, RB, RC = 7, 16, 20, 17, 37.5, 48, 5, 8
L, R, TOP, PAD_W, PAD_H = 24, 351, 26, 60, 8
READY, PLAYING, FADE, OVER = range(4)
NONE, WHITE, GREY, KEY = range(4)
MAGIC, FILE = 0x314b5242, '/mnt/data/game-bricks'
FX = 65536
TICK, TAP, ALERT = 1, 2, 3  # clicker_kind as eng_sfx sets it


class Bricks(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'x y spd ang pad h top hud mode t level lives side left seq runs wait'.split()),
                ('score', C.c_uint), ('seed', C.c_uint), ('cell', C.c_ubyte * (ROWS * COLS)),
                *((k, C.c_uint) for k in 'magic best best_level sum saved'.split())]


def cx(r, c):
    return X0 + (r & 1) * PX / 2 + c * PX


def cy(r):
    return Y0 + r * PY


def cols(r):
    return COLS - (r & 1)


def on_lattice():
    return [(r, c) for r in range(ROWS) for c in range(cols(r))]


class Game:
    """Bricks open on the engine over Page's clock, timers and card, at sheet height h; the sounds it made and the
    rects each frame marked are kept."""
    def __init__(self, fw, h=290, files=None):
        self.fw, self.e, self.page = fw, api(fw), Page(fw, files)
        self.rects, self.sounds = [], []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.rects.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('buzzeer_switch', lambda *_: self.sounds.append(fw.var('clicker_kind', C.c_int).value))
        self.done = C.CFUNCTYPE(None)(lambda: None)
        self.game = fw.var('eng_bricks', GameDef)
        assert self.e['eng_open'](self.game, PAGE, SHEET, h, self.done, self.done) == 1
        self.b = Bricks.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
        self.fn = {k: fw.fn(f'game_bricks__{k}', *v) for k, v in {
            'build': (None, C.c_void_p), 'around': (C.c_int, C.c_int, C.c_int, C.POINTER(C.c_int)), 'steep': (C.c_int, C.c_int),
            'step': (C.c_int, C.c_void_p, C.POINTER(Input))}.items()}

    def step(self, wheel=0, held=0, pressed=0, tx=0):
        return self.fn['step'](C.addressof(self.b), C.byref(Input(wheel, 0, 0, held, pressed, 0, tx, 0)))

    def frame(self, ms=16):
        """A pass of the frame timer: the rects it marked."""
        self.rects.clear()
        self.page.now += ms
        self.e['frame'](None)
        return list(self.rects)

    def key(self, key):
        self.e['eng_key'](key, 1), self.e['eng_key'](key, 0)

    def build(self, seed, level):
        self.b.seed, self.b.level = seed, level
        self.fn['build'](C.addressof(self.b))
        return bytes(self.b.cell)

    def live(self):
        return [(r, c) for r, c in on_lattice() if self.b.cell[r * COLS + c]]

    def launch(self, x, y, ang, spd=5):
        self.b.mode, self.b.x, self.b.y, self.b.ang, self.b.spd = PLAYING, int(x * FX), int(y * FX), ang, spd * FX

    def close(self):
        self.e['eng_close']()


def test_a_game_opens_ready_on_a_lattice_with_one_keystone_and_fits_its_heap(fw):
    g = Game(fw)
    b = g.b
    assert g.game.name == b'Bricks' and g.game.flags == 1 and g.game.size == C.sizeof(Bricks) == 208  # ENG_RAW; of the brief's 2 KB
    assert (b.mode, b.lives, b.level, b.score, b.h, b.top, b.pad) == (READY, 3, 1, 0, 290, 250, 187)
    assert b.spd == int(2.5 * FX) and b.seed & 1 and fw.var('wheel_raw', C.c_int).value == 1
    cells = [b.cell[r * COLS + c] for r, c in g.live()]
    assert cells.count(KEY) == 1 and len(cells) == b.left >= 12
    assert all(b.cell[r * COLS + COLS - 1] == NONE for r in range(1, ROWS, 2))  # an odd row's slot past its end
    assert b.y == (250 - RB) * FX and b.x == (187 + b.side * 20) * FX  # on the paddle, a third out
    g.close()


@pytest.mark.parametrize('level', [1, 2, 7, 18, 40])
def test_levels_come_from_the_seed_and_the_level_alone_mirrored_and_never_empty(fw, level):
    g = Game(fw)
    seen = set()
    for seed in [1, 2, 3, 0x9e3779b9, 0xffffffff, *range(1000, 1060)]:
        a = g.build(seed, level)
        assert g.build(seed, level) == a and a not in (None, b'')
        seen.add(a)
        live = g.live()
        assert len(live) == g.b.left >= 12 and [g.b.cell[r * COLS + c] for r, c in live].count(KEY) == 1
        for r, c in live:  # the mask is the mirror's; only the keystone may stand off the middle column alone
            mirror = g.b.cell[r * COLS + cols(r) - 1 - c]
            k = g.b.cell[r * COLS + c]
            assert mirror == k or KEY in (mirror, k), (seed, r, c)
        axis = [r for r in range(1, ROWS, 2) if g.b.cell[r * COLS + 7]]
        key = next((r, c) for r, c in live if g.b.cell[r * COLS + c] == KEY)
        assert not axis or key[1] == 7 and key[0] % 2 == 1  # on the middle column where it has a cell
    assert len(seen) > 20  # seeds make other lattices
    assert g.b.spd == min(int(2.5 * FX) + (level - 1) * (15 * FX // 100), 5 * FX)
    g.close()


def test_the_four_masks_take_turns_and_greys_grow_with_the_level(fw):
    g = Game(fw)
    greys = []
    for level in range(1, 9):
        g.build(77, level)
        greys.append(sum(g.b.cell[r * COLS + c] == GREY for r, c in g.live()) / g.b.left)
    assert sum(greys[:2]) / 2 < sum(greys[-2:]) / 2
    shapes = {g.build(77, level) for level in range(1, 5)}
    assert len(shapes) == 4
    g.close()


def test_a_mask_that_leaves_too_few_cells_is_drawn_again(fw):
    g = Game(fw)
    counts = [len(g.live()) for s in range(400) if g.build(s * 2654435761 % 2**32 or 1, 1 + s % 9) is not None]
    assert min(counts) >= 12 and max(counts) <= 109
    g.close()


@pytest.mark.parametrize('r, c', [(0, 0), (0, 7), (0, 15), (1, 0), (1, 7), (1, 14), (2, 0), (3, 14), (4, 15), (5, 3), (6, 0),
                                  (6, 15)])
def test_a_keystones_neighbours_are_the_six_touching_cells_on_either_parity(fw, r, c):
    g = Game(fw)
    out = (C.c_int * 6)()
    n = g.fn['around'](r, c, out)
    got = {(i // COLS, i % COLS) for i in out[:n]}
    want = {(rr, cc) for rr, cc in on_lattice() if 0 < math.hypot(cx(rr, cc) - cx(r, c), cy(rr) - cy(r)) < 21}
    assert got == want and n == len(want) and (n == 6) == (0 < r < ROWS - 1 and 0 < c < cols(r) - 1)
    g.close()


def test_a_keystone_takes_its_neighbours_in_one_rect_and_scores_them_at_the_levels_rate(fw):
    g = Game(fw)
    b = g.b
    for i in range(ROWS * COLS):
        b.cell[i] = WHITE if (i // COLS, i % COLS) in on_lattice() else NONE
    b.cell[3 * COLS + 7] = KEY
    b.cell[2 * COLS + 7] = GREY  # a grey neighbour goes too, at once
    b.left, b.level, b.score = 109, 3, 10
    g.launch(cx(3, 7), cy(3) + 13 + 2, 768)  # under the keystone, rising
    g.sounds.clear()
    rects = g.frame()
    gone = {(r, c) for r, c in on_lattice() if not b.cell[r * COLS + c]}
    assert gone == {(3, 7), (3, 6), (3, 8), (2, 7), (2, 8), (4, 7), (4, 8)} and b.left == 102 and b.score == 10 + 7 * 3
    joined = [x for x in rects if 40 < x[2] < 100]
    assert len(joined) == 1 and len(rects) <= 4  # the seven's rect, the ball's and the score's
    x, y, w, h = joined[0]
    for r, c in gone:
        assert x <= cx(r, c) - RC - 1 and cx(r, c) + RC + 1 <= x + w and y <= cy(r) - RC - 1 and cy(r) + RC + 1 <= y + h
    assert (0, 0, 375, 24) in rects and g.sounds == [TAP] and b.ang < 512  # turned back down
    for _ in range(6):
        g.frame()
    assert g.sounds == [TAP, TAP]  # two taps for a keystone, a run six frames apart
    g.close()


def test_a_grey_takes_two_hits_and_turns_white_at_the_first(fw):
    g = Game(fw)
    b = g.b
    for i in range(ROWS * COLS):
        b.cell[i] = NONE
    b.cell[6 * COLS + 7], b.cell[0] = GREY, WHITE
    b.left, score = 2, b.score
    g.launch(cx(6, 7), cy(6) + 14, 768, spd=2)
    g.sounds.clear()
    g.frame()
    assert b.cell[6 * COLS + 7] == WHITE and b.left == 2 and b.score == score and b.ang < 512 and g.sounds == [TICK]
    g.close()


@pytest.mark.parametrize('ang', range(0, 1024, 7))
def test_no_path_is_shallower_than_15_degrees_from_the_horizontal(fw, ang):
    g = Game(fw)
    out = g.fn['steep'](ang)
    h = out & 511
    assert 0 <= out < 1024 and 43 <= h <= 512 - 43 and (out < 512) == (ang % 1024 < 512)
    if 43 <= ang % 512 <= 512 - 43:
        assert out == ang % 1024
    g.close()


def test_the_paddle_sends_the_ball_up_by_where_it_hit(fw):
    g = Game(fw)
    b = g.b
    for off, want in ((0, 768), (35, 768 + 171), (-35, 768 - 171), (20, 768 + 20 * 171 // 35), (-60, None)):
        b.pad = 187
        g.launch(187 + off, b.top - RB - 1, 256, spd=3)
        g.step()
        if want is None:
            assert 256 - 43 <= b.ang <= 256 + 43 or b.ang in range(213, 300)  # past the end: no bounce
        else:
            assert b.ang == want, off
    g.close()


def segment_gap(p, q, c):
    (px, py), (qx, qy), (cx_, cy_) = p, q, c
    dx, dy = qx - px, qy - py
    t = 0 if not (dx or dy) else max(0, min(1, ((cx_ - px) * dx + (cy_ - py) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px + t * dx - cx_, py + t * dy - cy_)


def test_a_ball_at_top_speed_never_tunnels_through_a_full_lattice(fw):
    """200 seeded launches, the ball at 5 px a step into a lattice of greys (each takes two hits, so it stays
    full): no step ends inside a cell, and a step that turned nothing never crossed one."""
    g = Game(fw)
    b = g.b
    rnd = random.Random(2024)
    hits = 0
    for launch in range(200):
        for i in range(ROWS * COLS):
            b.cell[i] = GREY if (i // COLS, i % COLS) in on_lattice() else NONE
        b.left, b.level = 109, 30
        g.launch(rnd.uniform(L + RB, R - RB), rnd.uniform(cy(ROWS - 1) + 14, b.top - 20), rnd.randrange(512 + 43, 1024 - 43))
        for _ in range(80):
            before, ang, cells = (b.x / FX, b.y / FX), b.ang, bytes(b.cell)
            b.pad = max(54, min(321, b.x // FX))
            g.step()
            after = (b.x / FX, b.y / FX)
            assert math.hypot(after[0] - before[0], after[1] - before[1]) <= 5.01, (launch, before, after, b.mode, ang, b.ang)
            near = [(r, c) for r, c in on_lattice() if b.cell[r * COLS + c] and abs(cx(r, c) - after[0]) < 40 and abs(cy(r) - after[1]) < 40]
            for r, c in near:
                assert math.hypot(after[0] - cx(r, c), after[1] - cy(r)) >= RB + RC - .1, (launch, r, c)
                if ang == b.ang and cells == bytes(b.cell):
                    assert segment_gap(before, after, (cx(r, c), cy(r))) >= RB + RC - .1, (launch, r, c)
            hits += cells != bytes(b.cell)
            assert L + RB - .1 <= after[0] <= R - RB + .1 and after[1] >= TOP + RB - .1
            if b.mode != PLAYING:
                break
    assert hits > 400  # the lattice was hit, often
    g.close()


def test_the_paddle_follows_the_wheel_the_keys_and_a_finger_and_play_does_nothing(fw):
    g = Game(fw)
    b = g.b
    g.step(wheel=5)
    assert b.pad == 197 and b.x == (197 + b.side * 20) * FX  # 2 px a position; the ball rides it while ready
    g.step(wheel=-500)
    assert b.pad == L + PAD_W // 2
    g.step(held=E_NEXT), g.step(held=E_NEXT)
    assert b.pad == L + PAD_W // 2 + 8  # 4 px a step held
    g.step(held=E_PREV)
    assert b.pad == L + PAD_W // 2 + 4
    g.step(held=E_TOUCH, tx=300)
    assert b.pad == 300
    g.step(held=E_TOUCH, tx=900)
    assert b.pad == R - PAD_W // 2
    g.step(pressed=E_PLAY, held=E_PLAY)
    assert b.mode == READY and not g.sounds
    g.rects.clear()
    fw.var('wheel_travel', C.c_int).value = -3  # through the engine: the wheel's x falls clockwise
    rects = g.frame()
    assert b.pad == R - PAD_W // 2 and any(h == PAD_H for _, _, _, h in rects)  # clockwise at the end: held there
    fw.var('wheel_travel', C.c_int).value = 3
    rects = g.frame()
    assert b.pad == R - PAD_W // 2 - 6 and (b.pad - 30, b.top, 66, PAD_H) in rects  # old and new, one rect
    g.close()


def test_centre_launches_at_the_level_speed_and_a_frame_marks_ball_and_paddle_only(fw):
    g = Game(fw)
    b = g.b
    g.frame()
    g.key(CENTER)
    rects = g.frame()
    assert b.mode == PLAYING and b.ang == 768 + b.side * 171 * 20 // 35
    x, y = b.x, b.y
    rects = g.frame()
    v = math.hypot(b.x - x, b.y - y) / FX
    assert v == pytest.approx(2.5, abs=.02) and len(rects) == 1 and rects[0][2] <= 20 and rects[0][3] <= 20
    g.close()


def test_a_miss_costs_a_life_and_the_third_ends_the_game_with_a_save_when_beaten(fw):
    g = Game(fw)
    b = g.b
    for life in (2, 1):
        g.launch(b.pad + 100, b.top + 4, 256, spd=5)
        g.sounds.clear()
        rects = g.frame()
        assert b.lives == life and b.mode == READY and g.sounds == [ALERT] and (0, 0, 375, 24) in rects
    b.score = 120
    g.launch(b.pad + 100, b.top + 4, 256, spd=5)
    rects = g.frame()
    assert b.mode == OVER and rects == [(0, 0, 375, 290)]
    saved = g.page.card.files[FILE]
    assert saved[:4] == b'BRK1' and int.from_bytes(saved[4:8], 'little') == 120 and int.from_bytes(saved[8:12], 'little') == 1
    assert int.from_bytes(saved[12:], 'little') == fnv1a(saved[:12]) and len(saved) == 16
    g.frame()
    assert b.mode == OVER  # waits for Centre
    g.key(CENTER)
    g.frame()
    assert (b.mode, b.lives, b.score, b.level) == (READY, 3, 0, 1)
    del g.page.card.files[FILE]
    for _ in range(3):  # a worse game: nothing written
        g.launch(b.pad + 100, b.top + 4, 256)
        g.frame()
    assert b.mode == OVER and FILE not in g.page.card.files and b.best == 120
    g.close()


def test_a_save_that_fails_at_the_end_is_tried_again_at_a_pause_and_nothing_saves_mid_play(fw):
    g = Game(fw)
    b = g.b
    fw.mock('navigator_to_with_context', lambda *_: 0)  # the toast
    b.score = 50
    g.e['eng_hold'](), g.e['eng_run']()  # a pause mid-play
    assert FILE not in g.page.card.files
    g.page.card.fail = True
    b.lives = 1
    g.launch(b.pad + 100, b.top + 4, 256)
    g.frame()
    assert b.mode == OVER and FILE not in g.page.card.files and b.saved == 0
    g.page.card.fail = False
    g.e['eng_hold']()
    assert int.from_bytes(g.page.card.files[FILE][4:8], 'little') == 50 and b.saved == 51
    del g.page.card.files[FILE]
    g.e['eng_run'](), g.e['eng_hold']()
    assert FILE not in g.page.card.files  # written once
    g.close()


def blob(best, level, magic=MAGIC):
    words = [magic, best, level]
    raw = b''.join(w.to_bytes(4, 'little') for w in words)
    return raw + fnv1a(raw).to_bytes(4, 'little')


@pytest.mark.parametrize('files, best, level', [
    ({}, 0, 0),
    ({FILE: blob(4321, 6)}, 4321, 6),
    ({FILE: blob(4321, 6)[:9] + b'\x01' + blob(4321, 6)[10:]}, 0, 0),  # torn or flipped
    ({FILE: blob(4321, 6)[:12]}, 0, 0),  # short
    ({FILE: blob(4321, 6, magic=0x314f4d44)}, 0, 0),  # the demo's
    ({FILE: blob(100000000, 6)}, 0, 0),  # sealed but no game's
    ({FILE: blob(10, 100)}, 0, 0),
])
def test_a_save_loads_only_whole_and_sane(fw, files, best, level):
    g = Game(fw, files=files)
    assert (g.b.best, g.b.best_level, g.b.saved) == (best, level, best + level)
    g.close()


def test_clearing_the_lattice_fades_the_next_in_over_20_frames_two_rows_at_most_a_frame(fw):
    g = Game(fw)
    b = g.b
    for i in range(ROWS * COLS):
        b.cell[i] = NONE
    b.cell[6 * COLS + 7] = WHITE
    b.left, b.score = 1, 0
    g.launch(cx(6, 7), cy(6) + 14, 768, spd=2)
    g.sounds.clear()
    g.frame()
    assert b.mode == FADE and b.level == 2 and b.score == 1 and b.left >= 12 and g.sounds == [TAP, TICK]
    rows = []
    for k in range(20):
        rects = g.frame()
        fading = [x for x in rects if x[:3:2] == (L, R - L) and x[3] == 2 * RC + 3]
        assert len(fading) <= 2 and len(rects) <= 4, (k, rects)
        rows.append([(y + RC + 1 - Y0) // PY for _, y, _, _ in fading])
    assert b.mode == READY and sorted(r for f in rows for r in f) == sorted(list(range(ROWS)) * 4)  # each row 4 times
    assert g.sounds == [TAP, TICK, TICK, TICK]  # the last cell's tap, then three ticks
    g.close()


def lit_outside_glass(buf, h):
    """Lit pixels the glass's 80 px corners hide: (x, sheet row)."""
    out = []
    for y in range(320 - h, 320):
        for x in (*range(0, 80), *range(295, 375)):
            v = buf[y * 375 + x]
            if v & 0xffffff:
                ox, oy = (80 - x - .5 if x < 80 else x + .5 - 295), (80 - y - .5 if y < 80 else y + .5 - 240)
                if ox > 0 and oy > 0 and math.hypot(ox, oy) > 80:
                    out.append((x, y - (320 - h)))
    return out


@pytest.mark.parametrize('h', [262, 276, 290])
def test_nothing_lit_falls_under_the_glass_in_any_screen(fw, h):
    g = Game(fw, h=h)
    b = g.b
    canvas = Canvas(fw)
    def whole():
        buf = screen(canvas, origin=(0, 320 - h))
        canvas.ops.clear()
        g.e['eng_paint'](canvas.p)
        bad = lit_outside_glass(buf, h)
        for o in canvas.ops:  # text, at the width the mock measures, clear of the corners at its middle row
            if o.kind == 'text':
                x, y, w, hh = o.rect
                inset = g.e['eng_inset'](y + hh // 2)
                assert inset <= x and x + w <= 375 - inset, o
        return bad, buf
    for i in range(ROWS * COLS):
        b.cell[i] = WHITE if (i // COLS, i % COLS) in on_lattice() else NONE
    assert whole()[0] == []
    # the paddle driven to each end, and the ball let fly at the shallowest angle into each corner, as far as
    # the game's own walls and its miss let it go: low past the paddle to where it is lost, and along the top
    for side in (-1, 1):
        g.step(wheel=500 * side)
        for start, ang in ((b.top - 30, 512 - 43 if side < 0 else 43), (TOP + 30, 512 + 43 if side < 0 else 1024 - 43)):
            g.launch(187, start, ang)
            for k in range(200):
                b.cell[0] = b.cell[COLS - 1] = NONE  # nothing at the top corners to turn it
                g.step()
                if b.mode != PLAYING:
                    break
                if k % 3 == 0 or abs(b.x / FX - 187) > 150:
                    assert whole()[0] == [], (side, ang, b.x / FX, b.y / FX)
    b.mode = OVER
    assert whole()[0] == []
    g.close()


def test_draw_shows_the_score_line_the_ready_hint_and_the_end(fw):
    g = Game(fw)
    b = g.b
    canvas = Canvas(fw)
    b.score, b.lives = 12345, 2
    buf = screen(canvas)
    g.e['eng_paint'](canvas.p)
    said = [(t.lower(), ink & 0xffffff) for t, ink, _ in words(canvas.ops)]  # this build's theme may write capitals
    assert said == [('12,345', 0xababab), ('level 1', 0xababab), ('center to launch', 0x858585)]
    right = 375 - b.hud - 4
    assert buf[(30 + 12) * 375 + right] == 0xffababab and buf[(30 + 12) * 375 + right - 24] == 0xff333333  # two of three
    key = next((r, c) for r, c in g.live() if b.cell[r * COLS + c] == KEY)
    assert buf[(30 + cy(key[0])) * 375 + int(cx(*key))] == 0xff2160d8
    assert buf[(30 + b.top + 4) * 375 + b.pad] == 0xffffffff and buf[(30 + 200) * 375 + 5] == 0xff000000
    b.mode, b.score, b.best = OVER, 900, 900
    canvas.ops.clear()
    g.e['eng_paint'](canvas.p)
    assert [t.lower() for t in texts(canvas.ops)] == ['900', 'new best', 'center: play again']
    b.best = 1500
    canvas.ops.clear()
    g.e['eng_paint'](canvas.p)
    assert texts(canvas.ops)[1].lower() == 'best 1,500'
    b.score = 0
    canvas.ops.clear()
    g.e['eng_paint'](canvas.p)
    assert [t.lower() for t in texts(canvas.ops)[:2]] == ['0', 'best 1,500']
    g.close()


def test_bricks_is_games_first_engine_row_and_pauses_under_its_menu(fw):
    p = Page(fw)
    assert p.s.engines == 1 and p.s.rows == 1  # the chooser's one row: Games' own are not shipped
    p.go(ENGINE)
    assert p.s.screen == ENGINE and api(fw)['eng_live']()
    canvas = Canvas(fw)
    buf = screen(canvas)  # held: the paint fills it
    assert 'center to launch' in [t.lower() for t in texts(paint(p, canvas))]
    p.key(RETURN)
    assert p.s.paused == 2 and not api(fw)['eng_live']() and '/mnt/data/game-bricks' not in p.card.files
    p.key(RETURN)  # Continue
    assert p.s.paused == 0 and api(fw)['eng_live']()
    p.close()
    assert not fw.var('engine__en', C.c_void_p).value
    assert DEMO == ENGINE + 1 and PLAY and E_CENTRE


def test_a_fading_row_is_drawn_in_its_colours_scaled_toward_black(fw):
    g = Game(fw)
    b = g.b
    canvas = Canvas(fw)
    for i in range(ROWS * COLS):
        b.cell[i] = WHITE if (i // COLS, i % COLS) in on_lattice() else NONE
    b.cell[0], b.cell[1] = GREY, KEY
    b.mode = FADE
    for t, row0, row1 in ((0, 0, 0), (1, 64, 0), (2, 128, 0), (3, 192, 64), (4, 256, 128), (20, 256, 256)):
        b.t = t  # row 0 starts at 0, row 1 at 2: a quarter of the way a frame
        buf = screen(canvas)
        g.e['eng_paint'](canvas.p)
        at = lambda r, c: buf[(30 + int(cy(r))) * 375 + int(cx(r, c))] & 0xffffff
        dim = lambda rgb, a: rgb if a >= 256 else sum(((rgb >> s & 255) * a >> 8) << s for s in (0, 8, 16))
        assert (at(0, 0), at(0, 1), at(0, 2), at(1, 0)) == (dim(0xababab, row0), dim(0x2160d8, row0), dim(0xffffff, row0),
                                                           dim(0xffffff, row1)), t
    g.close()
