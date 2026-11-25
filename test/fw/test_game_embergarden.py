"""Ember Garden (patch/game_embergarden.c) on the engine: its art file, the aim and the spurts, the hit test on the lead
drop alone, the embers' sizes, splits and landings, the beds, the waves and the light's turn, the joined rects, saves,
the glass's corners; then through Games' page."""
import ctypes as C
import math
import pathlib
import re
import struct

import pytest

from test_engine import E_CENTRE, E_NEXT, E_PREV, E_TOUCH, ENGINE, RETURN, Eng, GameDef, Input, api, screen
from test_games import PAGE, Canvas, Page, fnv1a, heard

ROOT = pathlib.Path(__file__).resolve().parents[2]
ART = (ROOT / 'assets/games/embergarden.bin').read_bytes()
ART_FILE, FILE, MAGIC = '/usr/share/q2games/embergarden.bin', '/mnt/data/game-embergarden', 0x31474d45
HDR = {k: int(v) for k, v in re.findall(r'^#define (EG_\w+) (\d+)u?\b', (ROOT / 'patch/game_embergarden_art.h').read_text(), re.M)}
PARTS = re.findall(r'^    EG_(\w+), /\*', (ROOT / 'patch/game_embergarden_art.h').read_text(), re.M)
CH_X = [int(v) for v in re.findall(r'EG_CH_X \{ ([\d, ]+) \}', (ROOT / 'patch/game_embergarden_art.h').read_text())[0].split(', ')]
BAND, CH_Y, CH_W, CH_H = HDR['EG_BAND'], HDR['EG_CH_Y'], HDR['EG_CH_W'], HDR['EG_CH_H']
START, PLAY, LOST = range(3)
GROWING, CAUGHT, BURNING, CHARRED = range(4)
DAWN, DAY, DUSK, NIGHT = range(4)
BEDS = [44, 79, 114, 149, 226, 261, 296, 331]
FX = 65536
TICK, TAP, ALERT = 1, 2, 3  # clicker_kind as eng_sfx sets it
R = [3.0, 4.6, 7.0]


class Ember(C.Structure):  # G_EMBERS of them
    _fields_ = [(k, C.c_int) for k in 'bx y vx vy phase amp size alive smoke'.split()]


class Shot(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'x y ux uy run alive'.split()]


class Puff(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'x y f t size pts alive'.split()]


class State(C.Structure):
    _fields_ = [('art', C.c_uint * (HDR['EG_ART_BYTES'] // 4)), ('pal', C.c_uint * (320 // BAND * 256)),
                ('grow', C.c_ubyte * (8 * CH_W * CH_H)), ('ok', C.c_int), ('em', Ember * 12), ('sh', Shot * 3), ('pf', Puff * 3),
                *((k, C.c_int) for k in 'mode aim recoil spray repeat stop h tick'.split()), ('bed', C.c_int * 8),
                ('bed_t', C.c_int * 8), ('land_x', C.c_int), ('land_t', C.c_int), ('last', C.c_int * 2), ('blink', C.c_int),
                ('flame', C.c_ubyte * 8), *((k, C.c_int) for k in 'wave lull left spawn gap banner cleared light frm k strip dim bolt bolt_x '
                                       'bolt_seed bolt_wait card seq runs wait'.split()),
                ('score', C.c_uint), ('seed', C.c_uint), ('boxes', C.c_int), ('box', (C.c_int * 4) * 64),
                *((k, C.c_uint) for k in 'magic best best_wave sum saved'.split())]


def part(k):
    """The art file's part named k: offset, w, h, ax, ay, kind, adv."""
    at, w, h, ax, ay, kind, _, adv = struct.unpack_from('<IHHhhBBH', ART, 16 + 16 * PARTS.index(k))
    return at, w, h, ax, ay, kind, adv


SCENE = ART[part('SCENE')[0]:part('SCENE')[0] + 375 * 320]
PAL = ART[part('PALETTE')[0]:part('PALETTE')[0] + 4 * 10 * 256 * 3]


def colour(light, y, i):
    o = ((light * 10 + y // BAND) * 256 + i) * 3
    return PAL[o] << 16 | PAL[o + 1] << 8 | PAL[o + 2]


class Game:
    """Ember Garden open on the engine over Page's clock, timers and files (the art file among them unless art is
    given), the sheet the whole screen; the sounds it made and the rects each frame marked are kept."""
    def __init__(self, fw, files=None, art=ART):
        self.fw, self.e = fw, api(fw)
        self.page = Page(fw, {**({ART_FILE: art} if art is not None else {}), **(files or {})})
        self.rects, self.sounds = [], []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.rects.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('buzzeer_switch', lambda *_: self.sounds.append(fw.var('clicker_kind', C.c_int).value))
        self.done = C.CFUNCTYPE(None)(lambda: None)
        self.game = fw.var('eng_embergarden', GameDef)
        assert self.e['eng_open'](self.game, PAGE, 0x7300, 320, self.done, self.done) == 1
        self.g = State.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
        self.fn = {k: fw.fn(f'game_embergarden__{k}', *v) for k, v in {
            'step': (C.c_int, C.c_void_p, C.POINTER(Input)), 'draw': (None, C.c_void_p, C.c_void_p),
            'spawn': (None, C.c_void_p), 'land': (None, C.c_void_p, C.c_int), 'next_wave': (None, C.c_void_p),
            'flush': (None, C.c_void_p), 'mark': (None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)}.items()}

    def step(self, wheel=0, held=0, pressed=0, tx=0, ty=0):
        return self.fn['step'](C.addressof(self.g), C.byref(Input(wheel, 0, 0, held, pressed, 0, tx, ty)))

    def frame(self, ms=16):
        """A pass of the frame timer: the rects it marked."""
        self.rects.clear()
        self.page.now += ms
        self.e['frame'](None)
        return list(self.rects)

    def play(self):
        """Center on the title card: wave 1, the banner up, nothing falling yet."""
        self.step(pressed=E_CENTRE)
        assert self.g.mode == PLAY and self.g.wave == 1

    def ember(self, k, x, y, size=1, amp=0, vy=0.5, vx=0):
        e = self.g.em[k]
        e.bx, e.y, e.vx, e.vy, e.phase, e.amp, e.size, e.alive = int(x * FX), int(y * FX), int(vx * FX), int(vy * FX), 0, amp, size, 1
        return e

    def alive(self):
        return [e for e in self.g.em if e.alive]

    def quiet(self):
        """No more embers this wave, no banner: only what a test puts there moves."""
        self.g.left, self.g.banner, self.g.spawn, self.g.runs = 1, 0, 100000, 0  # one ember owed: the wave goes on
        for e in self.g.em:
            e.alive = 0

    def paint(self, buf_canvas, clip=(0, 0, 375, 320)):
        canvas, buf = buf_canvas
        self.fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, (C.c_int * 4)(*clip), 16) and 0)
        self.e['eng_paint'](canvas.p)
        return buf

    def close(self):
        self.e['eng_close']()


def canvas(fw):
    c = Canvas(fw)
    return c, screen(c, origin=(0, 0))


def test_the_game_opens_on_its_title_card_full_screen_with_its_art_in_its_heap(fw):
    g = Game(fw)
    s = g.g
    assert g.game.name == b'Ember Garden' and g.game.flags == 1 | 4  # ENG_RAW, ENG_FULL
    assert g.game.size == C.sizeof(State) <= 256 * 1024  # the art read whole, the brief's 256 KB at most
    assert s.ok == 1 and bytes(s.art)[:len(ART)] == ART and (s.mode, s.wave, s.score, s.light, s.k, s.h) == (START, 0, 0, DAWN, 32, 320)
    assert s.aim == -9000 and all(b == GROWING for b in s.bed) and not g.alive()
    assert fw.var('wheel_raw', C.c_int).value == 1
    for k, x in enumerate(CH_X):  # each bed's box of the scene, kept for a new game
        assert bytes(s.grow[k * CH_W * CH_H:(k + 1) * CH_W * CH_H]) == b''.join(
            SCENE[(CH_Y + j) * 375 + x:(CH_Y + j) * 375 + x + CH_W] for j in range(CH_H))
    g.close()


@pytest.mark.parametrize('art', [None, ART[:-4], b'X' + ART[1:], ART[:8] + b'\0\0\0\0' + ART[12:]])
def test_a_missing_or_damaged_art_file_says_so_and_leaves(fw, art):
    toasts = heard(fw)
    g = Game(fw, art=art)
    assert g.g.ok == 0 and toasts == [b"Ember Garden's art is missing"]
    c, buf = canvas(fw)
    g.paint((c, buf))
    assert set(buf) == {0xff000000}  # black until it leaves
    assert g.step(pressed=E_CENTRE) == 0 and Eng.from_address(fw.var('engine__en', C.c_void_p).value).quit == 1
    g.close()


def test_the_wheel_aims_1_2_degrees_a_position_and_stops_at_both_ends_of_the_arc(fw):
    g = Game(fw)
    g.play()
    g.step(wheel=10)
    assert g.g.aim == -9000 + 1200
    g.step(wheel=-3)
    assert g.g.aim == -9000 + 840
    g.step(wheel=1000)
    assert g.g.aim == -1000
    g.step(wheel=-5000)
    assert g.g.aim == -17000
    g.step(held=E_NEXT)
    assert g.g.aim == -16800  # 2 degrees a step held
    g.step(held=E_PREV), g.step(held=E_PREV)
    assert g.g.aim == -17000
    g.close()


@pytest.mark.parametrize('tx, ty, aim', [(187, 100, -9000), (287, 170, -4500), (87, 170, -13500), (370, 268, -1000), (0, 268, -17000)])
def test_a_finger_aims_at_itself_and_a_press_fires(fw, tx, ty, aim):
    g = Game(fw)
    g.play()
    g.step(held=E_TOUCH, pressed=E_TOUCH, tx=tx, ty=ty)
    assert abs(g.g.aim - aim) <= 40 and sum(h.alive for h in g.g.sh) == 1
    g.step(held=E_TOUCH, tx=tx, ty=ty)
    assert sum(h.alive for h in g.g.sh) == 1  # a held finger aims; it fires on its press only
    g.g.aim = -9000 if aim != -9000 else -4000
    g.step(pressed=E_TOUCH, tx=tx, ty=ty)  # a tap: pressed and lifted within one frame, so never held
    assert abs(g.g.aim - aim) <= 40 and sum(h.alive for h in g.g.sh) == 2
    g.close()


def test_three_spurts_at_most_and_a_held_center_repeats_every_12_steps(fw):
    g = Game(fw)
    g.play()
    g.quiet()
    g.sounds.clear()
    g.step(held=E_CENTRE, pressed=E_CENTRE)
    shots = [sum(h.alive for h in g.g.sh)]
    for _ in range(40):
        g.step(held=E_CENTRE)
        shots.append(sum(h.alive for h in g.g.sh))
    assert shots[0] == 1 and shots[11] == 1 and shots[12] == 2 and shots[24] == 3 and max(shots) == 3
    h = g.g.sh[0]
    assert h.run == 6 * 41 or not h.alive  # 6 px a step, straight up
    g.step(pressed=E_CENTRE)
    assert sum(h.alive for h in g.g.sh) <= 3
    assert g.sounds.count(TICK) >= 3
    g.close()


def test_a_spurt_flies_straight_along_the_aim_from_the_nozzle(fw):
    g = Game(fw)
    g.play()
    g.quiet()
    g.g.aim = -4500
    g.step(pressed=E_CENTRE)
    h = next(h for h in g.g.sh if h.alive)
    x0, y0 = h.x / FX, h.y / FX
    for _ in range(10):
        g.step()
    assert h.run == 66 and abs(h.x / FX - x0 - 60 * math.cos(math.radians(-45))) < 1 and abs(h.y / FX - y0 + 60 * math.sin(math.radians(45))) < 1
    # from the nozzle, 22 px out, and its first 6 px in the step that fired it
    assert abs(x0 - (187.5 + 28 * math.cos(math.radians(-45)))) < 1 and abs(y0 - (270 - 28 * math.sin(math.radians(45)))) < 1
    g.close()


def test_only_the_lead_drop_puts_an_ember_out(fw):
    """Spurt V1: the trail is drawn only. Aimed at 45 degrees, the trail sags below the lead's line (7 px at its last
    drop); an ember 11 px under the line overlaps the trail as it passes but is 7.8 px from the lead's path, more
    than the 4.5 and 3 px of the two discs, and stays. 9 px under it, the lead meets it."""
    g = Game(fw)
    g.play()
    g.quiet()
    g.g.aim = -4500
    d = 120 / math.sqrt(2)
    for under, hit in ((11, 0), (9, 1)):
        e = g.ember(0, 187.5 + d, 270 - d + under, size=0, vy=0)
        g.step(pressed=E_CENTRE)
        for _ in range(40):
            g.step()
        assert (not e.alive, g.g.score) == (bool(hit), hit), under
        g.g.repeat = 0
    g.close()


@pytest.mark.parametrize('size, lull, pts', [(0, 0, 1), (1, 0, 2), (2, 0, 3), (2, 1, 6), (0, 1, 2)])
def test_a_hit_scores_by_size_and_double_in_a_lull(fw, size, lull, pts):
    g = Game(fw)
    g.play()
    g.quiet()
    g.g.lull = lull
    g.ember(0, 187.5, 150, size=size, vy=0)
    g.g.aim = -9000
    g.sounds.clear()
    g.step(pressed=E_CENTRE)
    for _ in range(30):
        g.step()
    assert g.g.score == pts and any(p.pts == pts for p in g.g.pf)
    assert g.sounds[:2] == [TICK, TAP]
    g.close()


def test_a_cinder_cracks_into_two_small_embers_that_fly_apart_inside_the_field_after_a_hit_stop(fw):
    g = Game(fw)
    g.play()
    g.quiet()
    g.ember(0, 187.5, 150, size=2, vy=0.5)
    g.g.aim = -9000
    g.step(pressed=E_CENTRE)
    for _ in range(30):
        g.step()
        if g.g.stop:
            break
    small = g.alive()
    assert len(small) == 2 and all(e.size == 0 and e.amp == 0 for e in small) and g.g.stop == 2  # held this step and 2 more
    assert sorted(e.vx for e in small) == [-int(1.6 * FX), int(1.6 * FX)]
    y = [e.y for e in small]
    g.step(), g.step()
    assert [e.y for e in small] == y  # held for the stop
    for _ in range(40):
        g.step()
    xs = sorted(e.bx / FX for e in small)
    assert xs[0] < 187.5 - 10 and xs[1] > 187.5 + 10  # apart, then falling
    # at the field's edge a split stays inside it
    g.quiet()
    g.ember(0, 30, 150, size=2, vy=0)
    g.g.em[0].vx = -int(1.6 * FX)
    for _ in range(60):
        g.step()
    assert all(24 + 3 <= e.bx / FX <= 351 - 3 for e in g.alive())
    g.close()


def test_an_ember_landing_lights_the_nearest_growing_bed_on_its_side_which_burns_then_chars(fw):
    g = Game(fw)
    g.play()
    g.quiet()
    g.sounds.clear()
    g.ember(0, 120, 257, size=1, vy=1)
    g.step()
    assert g.g.bed[2] == CAUGHT and g.g.land_x == 120 and g.sounds == [ALERT]
    for _ in range(7):
        g.step()
    assert g.g.bed[2] == BURNING
    for _ in range(89):
        g.step()
    assert g.g.bed[2] == BURNING
    g.step()
    assert g.g.bed[2] == CHARRED
    charred = ART[part('CHARRED')[0] + 2 * CH_W * CH_H:part('CHARRED')[0] + 3 * CH_W * CH_H]
    scene = bytes(g.g.art)[part('SCENE')[0]:]
    assert b''.join(scene[(CH_Y + j) * 375 + CH_X[2]:(CH_Y + j) * 375 + CH_X[2] + CH_W] for j in range(CH_H)) == charred
    g.ember(0, 120, 257, size=0, vy=1)  # the next on that side: the nearest bed still growing
    g.step()
    assert g.g.bed[1] == CAUGHT or g.g.bed[3] == CAUGHT
    g.ember(0, 300, 257, size=0, vy=1)
    g.step()
    assert g.g.bed[6] == CAUGHT  # the other side's own
    g.close()


def test_the_third_bed_lost_on_a_side_frames_its_last_and_the_fourth_ends_the_game_saving_a_best(fw):
    g = Game(fw)
    g.play()
    g.quiet()
    g.g.score = 70
    for x in (40, 80, 115):
        g.ember(0, x, 257, size=0, vy=1)
        g.step()
    assert g.g.last[0] == 3 and g.g.last[1] == -1 and g.g.mode == PLAY
    for x in (300, 260):
        g.ember(0, x, 257, size=0, vy=1)
        g.step()
    assert g.g.mode == PLAY and g.g.last[1] == -1
    g.g.bed[4], g.g.bed_t[4] = BURNING, 1  # a bed of the right side charred, its box in the scene
    g.step()
    assert g.g.bed[4] == CHARRED and bytes(g.g.art)[part('SCENE')[0]:part('SCENE')[0] + 375 * 320] != SCENE
    g.sounds.clear()
    g.ember(0, 10, 257, size=0, vy=1)  # the far left: the last left bed is the nearest growing one
    g.step()
    assert g.g.bed[3] == BURNING and g.g.mode == LOST and g.g.dim == 1 and not g.alive()
    assert g.sounds[:2] == [ALERT, ALERT]
    raw = g.page.card.files[FILE]
    assert struct.unpack('<4I', raw) [:3] == (MAGIC, 70, 1) and g.g.saved == 71
    for _ in range(14):
        g.step()
    assert g.g.card == 14
    g.step(pressed=E_CENTRE)  # replant: a new game at dawn, every bed growing, wave 1 begun
    assert g.g.mode == PLAY and g.g.wave == 1 and all(b == GROWING for b in g.g.bed) and g.g.dim == 0 and g.g.score == 0
    scene = bytes(g.g.art)[part('SCENE')[0]:part('SCENE')[0] + 375 * 320]
    assert scene == SCENE  # the charred boxes put back
    g.close()


def test_a_save_that_fails_at_the_end_is_tried_again_at_a_pause_and_nothing_saves_mid_play(fw):
    g = Game(fw)
    fw.mock('navigator_to_with_context', lambda *_: 0)  # the toast
    g.play()
    g.quiet()
    g.g.score = 50
    g.e['eng_hold'](), g.e['eng_run']()
    assert FILE not in g.page.card.files
    g.page.card.fail = True
    for k in range(4):
        g.g.bed[k] = CHARRED if k else GROWING
    g.ember(0, 40, 257, size=0, vy=1)
    g.step()
    assert g.g.mode == LOST and FILE not in g.page.card.files and g.g.saved == 0
    g.page.card.fail = False
    g.e['eng_hold']()
    assert struct.unpack('<4I', g.page.card.files[FILE])[1] == 50 and g.g.saved == 51
    del g.page.card.files[FILE]
    g.e['eng_run'](), g.e['eng_hold']()
    assert FILE not in g.page.card.files  # written once
    g.close()


def blob(best, wave, magic=MAGIC):
    raw = struct.pack('<3I', magic, best, wave)
    return raw + struct.pack('<I', fnv1a(raw))


@pytest.mark.parametrize('files, best, wave', [
    ({}, 0, 0),
    ({FILE: blob(4820, 12)}, 4820, 12),
    ({FILE: blob(4820, 12)[:9] + b'\x01' + blob(4820, 12)[10:]}, 0, 0),  # torn or flipped
    ({FILE: blob(4820, 12)[:12]}, 0, 0),  # short
    ({FILE: blob(4820, 12, magic=0x314b5242)}, 0, 0),  # another game's magic
    ({FILE: blob(100000000, 6)}, 0, 0),  # sealed but no game's
    ({FILE: blob(10, 10000)}, 0, 0),
])
def test_a_save_loads_only_whole_and_sane(fw, files, best, wave):
    g = Game(fw, files=files)
    assert (g.g.best, g.g.best_wave, g.g.saved) == (best, wave, best + wave)
    g.close()


def run_waves(fw, seed, steps):
    g = Game(fw)
    g.play()
    g.g.seed = seed
    seen = []
    for k in range(steps):
        g.step(wheel=1 if k % 50 < 25 else -1, held=E_CENTRE)
        seen.append(tuple((e.bx, e.y, e.size) for e in g.g.em if e.alive))
    out = (seen, g.g.wave, g.g.score)
    g.close()
    return out


def test_waves_are_paced_by_the_seed_alone(fw):
    a, b, c = run_waves(fw, 12345, 900), run_waves(fw, 12345, 900), run_waves(fw, 777, 900)
    assert a == b and a[0] != c[0]


def test_waves_grow_denser_and_faster_and_every_tenth_is_a_lull_of_slow_cinders(fw):
    g = Game(fw)
    g.play()
    rows = []
    for wave in range(1, 22):
        g.g.wave = wave - 1
        g.fn['next_wave'](C.addressof(g.g))
        rows.append((g.g.wave, g.g.lull, g.g.left, g.g.gap))
        if wave in (10, 20):
            g.quiet()
            g.g.left, g.g.spawn = 5, 1
            g.step()
            e = g.alive()[0]
            assert e.size == 2 and e.vy == FX // 2
    assert [r[2] for r in rows if not r[1]] == sorted(r[2] for r in rows if not r[1])  # more embers each wave, to 40
    assert [r[3] for r in rows if not r[1]] == sorted((r[3] for r in rows if not r[1]), reverse=True)
    assert [r[0] for r in rows if r[1]] == [10, 20] and all(r[2] == 5 for r in rows if r[1])
    g.quiet()
    for wave, fall in ((1, 0.5), (11, 1.1), (40, 2.2)):
        g.g.wave, g.g.lull, g.g.left, g.g.spawn = wave, 0, 1, 1
        g.step()
        e = g.alive()[-1]
        assert abs(e.vy / FX - min(0.5 + 0.06 * (wave - 1), 2.2)) < 0.001 and 30 <= e.bx / FX - 18 and e.bx / FX + 18 <= 345
        e.alive = 0
    g.close()


@pytest.mark.parametrize('wave, light', [(1, DAWN), (2, DAWN), (3, DAY), (5, DAY), (6, DUSK), (7, DUSK), (8, NIGHT), (10, NIGHT),
                                         (11, DAWN), (13, DAY)])
def test_the_light_follows_the_waves(fw, wave, light):
    g = Game(fw)
    g.play()
    g.g.wave = wave - 1
    g.fn['next_wave'](C.addressof(g.g))
    assert g.g.light == light
    g.close()


def test_the_light_turns_a_strip_of_8_rows_a_step_the_palette_a_32nd_further_each_pass_never_the_whole_sheet(fw):
    g = Game(fw)
    g.play()
    g.frame()  # the whole sheet the new game asked for
    g.quiet()
    g.g.wave = 2
    g.fn['next_wave'](C.addressof(g.g))  # wave 3: day
    assert (g.g.frm, g.g.light, g.g.k, g.g.strip) == (DAWN, DAY, 0, 0)
    g.quiet()
    c, buf = canvas(fw)
    g.paint((c, buf))
    seen = []
    for k in range(40 * 32 + 2):
        rects = g.frame()
        assert (0, 0, 375, 320) not in rects
        seen.append(rects)
        for r in rects:
            g.paint((c, buf), r)
        if k == 0:
            assert g.g.k == 1 and (0, 0, 375, 8) in rects, rects
        if k == 39:  # a pass's end: every strip painted in the palette of this pass, as a whole paint would
            assert g.g.k == 1 and any(r[1] <= 312 and r[1] + r[3] >= 320 for r in rects)
            after = list(buf)
            g.paint((c, buf))
            assert list(buf) == after
        if k == 40:
            assert g.g.k == 2
    assert g.g.k == 32 and g.g.strip == 0
    assert seen[-1] == []  # done: nothing more to paint
    y = 100
    i = SCENE[y * 375 + 50]
    assert g.g.pal[(y // BAND) * 256 + i] & 0xffffff == colour(DAY, y, i)
    g.close()


def test_a_full_sky_leaves_the_extras_for_a_step_with_room(fw):
    """Twelve embers with long tails falling mark more than the step's budget: the light's strip, a due puff and the banner's fade
    wait, and go on once the sky is quiet."""
    g = Game(fw)
    g.play()
    g.quiet()
    g.g.frm, g.g.light, g.g.k, g.g.strip = DAWN, DAY, 3, 5
    for k in range(12):
        g.ember(k, 40 + 25 * k, 60 + 9 * k, size=2 if k < 6 else 1, amp=18, vy=2).smoke = 1
    g.g.pf[0].alive, g.g.pf[0].x, g.g.pf[0].y, g.g.pf[0].f, g.g.pf[0].t = 1, 100, 100, 1, 9
    g.g.banner = 50
    g.step()
    assert (g.g.k, g.g.strip, g.g.pf[0].f, g.g.banner) == (3, 5, 1, 50)
    for e in g.g.em:
        e.alive = 0
    g.step(), g.step()
    assert g.g.strip > 5 and g.g.pf[0].f == 2 and g.g.banner > 50
    g.close()


def test_a_turn_halfway_mixes_the_two_times_of_day(fw):
    g = Game(fw)
    g.g.frm, g.g.light, g.g.k = DAY, DUSK, 16
    fw.fn('game_embergarden__palette', None, C.c_void_p)(C.addressof(g.g))
    for y, x in ((10, 30), (150, 300), (280, 100)):
        i = SCENE[y * 375 + x]
        a, b = colour(DAY, y, i), colour(DUSK, y, i)
        mid = sum(((a >> s & 255) + int(((b >> s & 255) - (a >> s & 255)) * 16 / 32)) << s for s in (16, 8, 0))
        assert g.g.pal[(y // BAND) * 256 + i] == 0xff000000 | mid
    g.close()


def test_a_frame_marks_at_most_8_rects_and_they_hold_every_changed_pixel(fw):
    """Painted whole and painted by the rects the frame marked, the screen is the same after every frame of a busy
    scripted run: embers falling, spurts, hits and splits, landings, flames, the light turning, the aim moving."""
    g = Game(fw)
    c, buf = canvas(fw)
    g.page.now += 16
    g.e['frame'](None)
    g.step(pressed=E_CENTRE)
    g.g.wave = 5
    g.fn['next_wave'](C.addressof(g.g))  # dusk: lightning may strike
    g.g.frm, g.g.k = g.g.light, 32  # at dusk already: no turn (a turn is its own test) through waves 6 and 7
    g.g.spawn = 1
    g.g.gap = 6
    g.g.left = 40
    g.paint((c, buf))
    worst = 0
    for k in range(700):
        rects = g.frame()
        if k % 7 == 0:
            g.e['eng_key'](218, 1), g.e['eng_key'](218, 0)  # Center
        g.fw.var('wheel_travel', C.c_int).value = 3 if k % 40 < 20 else -3
        assert len(rects) <= 8
        worst = max(worst, len(rects))
        for x, y, w, h in rects:
            g.paint((c, buf), (x, y, w, h))
        after = bytes(buf)
        g.paint((c, buf))
        if bytes(buf) != after:
            import array
            a1, a2 = array.array('I', after), array.array('I', bytes(buf))
            bad = [(i % 375, i // 375) for i in range(len(a1)) if a1[i] != a2[i]]
            print('DEBUG', k, len(bad), bad[:5], bad[-5:], rects, g.g.aim, [(h.x/FX, h.y/FX, h.run, h.alive) for h in g.g.sh],
                  [(e.bx/FX, e.y/FX, e.size, e.amp, e.phase, e.vx/FX) for e in g.g.em if e.alive], [(q.x, q.y, q.t) for q in g.g.pf if q.alive],
                  g.g.spray, g.g.recoil, g.g.bolt, g.g.land_t)
        assert bytes(buf) == after, k
        if g.g.mode != PLAY:
            break
        assert g.g.wave < 8
    assert worst >= 4
    g.close()


def under_glass(x, y):
    ox, oy = (80 - x - .5 if x < 80 else x + .5 - 295), (80 - y - .5 if y < 80 else y + .5 - 240)
    return ox > 0 and oy > 0 and math.hypot(ox, oy) > 80


def test_nothing_the_player_reads_or_aims_at_falls_under_the_glass(fw):
    """The scene runs to the panel's edge, under the glass's corners too. The title card and the result card leave the
    corners to the scene, and no ember's body ever reaches under the glass: they fall from x 30 to 345, swaying and
    split, through many waves of play (a spurt may fly off through a corner, as it leaves the field)."""
    g = Game(fw)
    c, buf = canvas(fw)
    corners = [(x, y) for y in (*range(0, 80), *range(240, 320)) for x in (*range(0, 80), *range(295, 375)) if under_glass(x, y)]
    def cards():
        g.paint((c, buf))
        return [(x, y) for x, y in corners if buf[y * 375 + x] != g.g.pal[(y // BAND) * 256 + SCENE[y * 375 + x]]]
    assert cards() == []  # the title card
    g.play()
    g.g.seed = 99
    worst = 99.0
    for k in range(6000):
        g.step(wheel=(1 if k % 120 < 60 else -1) * 9, held=E_CENTRE)
        for e in g.alive():
            x, y, r = (e.bx + e.amp * g.e['eng_sin'](e.phase * 1024 // 180)) / FX, e.y / FX, R[e.size]
            inset = max(g.e['eng_inset'](int(y - r)), g.e['eng_inset'](min(319, int(y + r))))
            worst = min(worst, x - r - inset, 375 - inset - x - r)
        if g.g.mode != PLAY:
            for k in range(8):
                g.g.bed[k] = GROWING
            g.g.mode = PLAY
    assert worst > 0 and g.g.wave >= 3, (worst, g.g.wave)
    for k in range(4):
        g.g.bed[k] = CHARRED if k < 3 else GROWING
    g.quiet()
    g.ember(0, 40, 257, size=0, vy=1)
    g.step()
    for _ in range(15):
        g.step()
    assert g.g.mode == LOST and cards() == []  # the result card
    g.close()


def test_the_title_card_the_score_and_the_result_card_are_drawn(fw):
    g = Game(fw)
    c, buf = canvas(fw)
    g.paint((c, buf))
    assert buf[60 * 375 + 150] & 0xffffff == 0xe0503a and buf[120 * 375 + 115] & 0xffffff == 0xfff6dc  # the packet
    g.play()
    g.quiet()
    g.paint((c, buf))
    zero = list(buf[282 * 375:300 * 375])
    g.g.score = 1240
    g.paint((c, buf))
    assert list(buf[282 * 375:300 * 375]) != zero
    ink = [buf[y * 375 + x] & 0xffffff for y in range(284, 298) for x in range(60, 110)]
    assert 0x381e11 in ink  # the score in the marker's ink, #3A2414 in dawn's light
    g.close()


@pytest.mark.parametrize('aim', [-17000, -13000, -9000, -6000, -2300, -1000])
def test_the_sprinklers_barrel_lies_along_the_aim(fw, aim):
    """The barrel, a stroke 6.4 px wide from 5 px behind the pivot to 20 px out, in brass lit by the time of day: its
    middle is brass, 6 px to either side of it is not."""
    g = Game(fw)
    c, buf = canvas(fw)
    g.play()
    g.quiet()
    g.g.aim = aim
    g.paint((c, buf))
    a = math.radians(aim / 100)
    ux, uy = math.cos(a), math.sin(a)
    brass = 0xd9 * 251 >> 8 << 16 | 0xa6 * 220 >> 8 << 8 | 0x32 * 225 >> 8  # #D9A632 in dawn's light
    at = lambda d, side: buf[int(270 + uy * d + ux * side) * 375 + int(187.5 + ux * d - uy * side)] & 0xffffff
    assert at(14, 0) == brass and at(14, 6) != brass and at(14, -6) != brass
    g.close()


def test_ember_garden_is_games_fifth_engine_row_full_screen_and_pauses_under_its_menu(fw):
    p = Page(fw, {ART_FILE: ART})
    p.go(ENGINE + 4)
    assert p.s.screen == ENGINE + 4 and api(fw)['eng_live']()
    s = State.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
    assert s.ok == 1 and s.h == 320
    p.key(RETURN)
    assert p.s.paused == 2 and not api(fw)['eng_live']() and FILE not in p.card.files
    p.key(RETURN)  # Continue
    assert p.s.paused == 0 and api(fw)['eng_live']()
    p.close()
    assert not fw.var('engine__en', C.c_void_p).value


def test_a_dev_build_starts_play_at_the_wave_config_asks_in_its_light(fw_dev):
    fw_dev.mock('toolsReadConfig', lambda path, sec, key, out, d, *_: C.memmove(out, b'7\0', 2) and 0)
    g = Game(fw_dev)
    g.step(pressed=E_CENTRE)
    assert (g.g.mode, g.g.wave, g.g.light, g.g.frm, g.g.k) == (PLAY, 7, DUSK, DUSK, 32)
    g.close()
