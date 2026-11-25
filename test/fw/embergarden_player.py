"""Simulated players for Ember Garden (patch/game_embergarden.c, docs/games-engine.md#ember-garden-playtest): a thumb
on the touch wheel and Centre, driving the game's own step through test_game_embergarden's Game, a step a 1/60 s.

    EMBER_PLAYTEST=60 python -m pytest test/fw/test_game_embergarden.py -k playtest -s

plays that many seeded games a profile and prints the tables. A player sees an ember some time after it appears,
picks one, turns the wheel to where it thinks the spurt will meet it, stops, looks, corrects, moves the thumb to
Centre, presses, and watches the spurt before the next. Each of those has a time and an error:

- Reaction: a simple visual reaction is about 190 to 250 ms in young adults and slower in early teens; picking one
  thing of several adds time with their number (Hick's law). NEW: 300 ms, and 90 ms more for each other ember in the
  sky to 360 ms more.
- The wheel: 200 positions a turn (docs/hardware.md), 1.2 degrees of aim a position (G_WHEEL), every position of
  travel reaching the game on its next step (the engine takes wheel_travel whole each step, so the driver's sample
  rate adds a frame or two of delay and loses nothing). A comfortable sweep is half a turn to a turn a second
  (100 to 200 positions), a frantic one 1.5 turns (300); a thumb covers about a third of a turn before it lifts
  and sets down again.
- Aim: a quick move ends off its mark by a share of its length (Schmidt's law, about 5 percent) and a floor, the
  thumb's precision on the ring: about 1 mm on a track about 110 mm round, so 2 positions (2.4 degrees) for a new
  player. The eye judges the sight line against the ember to a degree or two, and a correction starts about 200 ms
  after a move ends.
- Centre is in the middle of the wheel: one thumb leaves the wheel to press it (150 ms each way, the aim still
  meanwhile); a practised player keeps a second thumb on it.
- Lead: a spurt takes up to 0.7 s to arrive, and the thumb's own delays come before it. A new player leads half of
  the ember's fall and 0.4 of its sway over that time, a skilled one nearly all of both.

These are textbook figures and estimates, not measurements of a person on a Q2."""
import ctypes as C
import math
import random
from dataclasses import dataclass

FX = 65536
PX, PY, GROUND = 187.5, 270.0, 258
PLAY, LOST = 1, 2
GROWING = 0
R = (3.0, 4.6, 7.0)
E_CENTRE = 1


@dataclass(frozen=True)
class Profile:
    name: str
    rt: float            # ms from an ember appearing to the player having seen it
    rt_sd: float
    crowd: float         # ms more for each other ember falling, to crowd_max
    crowd_max: float
    decide: float        # ms to settle on the next target
    sweep: float         # wheel positions a second, unhurried
    panic: float         # with an ember under a second and a half from the ground
    reach: int           # positions of one stroke before the thumb lifts
    regrip: float        # ms to set it down again
    rel: float           # a move's end error: this share of its length
    floor: float         # and this many positions
    eye: float           # degrees: how well the sight line is judged against the ember
    feedback: float      # ms from a move's end to its correction
    hop: float           # ms from the wheel to Centre, and back (0: a second thumb)
    lead: float          # the share of the ember's fall during the spurt's flight that is led
    lead_sd: float
    sway: float          # the share of its sideways motion that is led
    watch: bool          # waits to see a spurt arrive before the next
    confirm: float       # ms to see what a spurt did
    wrong: float         # chance of picking the ember nearest the aim, not the one nearest the ground
    tries: int           # corrections before firing anyway


NEW = Profile('14, first games', 300, 60, 90, 360, 150, 130, 300, 65, 160, .07, 2.0, 2.0, 220, 150, .5, .3, .4, True, 280, .3, 2)
KEEN = Profile('14, ten games in', 270, 50, 60, 240, 100, 180, 300, 70, 140, .055, 1.5, 1.4, 190, 110, .8, .2, .7, True, 220, .1, 3)
ADULT = Profile('skilled adult', 220, 40, 40, 160, 70, 240, 300, 80, 120, .04, 1.0, 0.9, 160, 0, .95, .1, .9, False, 180, .0, 3)
PROFILES = (NEW, KEEN, ADULT)


def steps(ms):
    return max(0, round(ms * 60 / 1000))


class Player:
    def __init__(self, prof, rng):
        self.p, self.rng = prof, rng
        self.seen = {}      # slot: the step the player has seen its ember from
        self.sizes = {}
        self.busy = {}      # slot: a spurt is on its way to it until this step
        self.phase, self.t, self.target = 'idle', 0, None
        self.goal = self.carry = 0.0  # where this move ends (hundredths of a degree), the wheel's part position
        self.stroke, self.fixes, self.now = 0.0, 0, 0
        self.shots = 0

    def embers(self, g):
        out = []
        for k, e in enumerate(g.em):
            if not e.alive:
                self.seen.pop(k, None), self.sizes.pop(k, None), self.busy.pop(k, None)
                continue
            if k not in self.seen or self.sizes[k] != e.size:  # new, or a cinder's pieces
                others = sum(1 for _ in self.seen)
                late = self.rng.gauss(self.p.rt, self.p.rt_sd) + min(self.p.crowd * others, self.p.crowd_max)
                self.seen[k], self.sizes[k] = self.now + steps(max(120, late)), e.size
                if self.target == k:
                    self.target = None
            ang = e.phase * 2 * math.pi / 180
            x = e.bx / FX + e.amp * math.sin(ang)
            vx = e.amp * math.cos(ang) * 2 * math.pi / 180 + e.vx / FX
            out.append((k, x, e.y / FX, vx, e.vy / FX, e.size))
        return out

    def aim_for(self, e, lead, wait=0):
        """The aim (hundredths of a degree) that would meet ember e: where it will be after the player's own wait
        (steps) and the spurt's flight, by the shares of its fall and of its sway the player leads."""
        k, x, y, vx, vy, size = e
        ahead = wait + max(0.0, math.hypot(x - PX, y - PY) - 22) / 6
        tx, ty = x + vx * ahead * self.p.sway, min(y + vy * ahead * lead, GROUND)
        return max(-17000.0, min(-1000.0, math.degrees(math.atan2(min(ty - PY, -1), tx - PX)) * 100))

    def pick(self, alive):
        known = [e for e in alive if self.seen[e[0]] <= self.now]
        free = [e for e in known if self.busy.get(e[0], 0) <= self.now] or known
        if not free:
            return None
        if self.rng.random() < self.p.wrong:
            return min(free, key=lambda e: abs(self.aim_for(e, 0) - self.aim))[0]
        return min(free, key=lambda e: (GROUND - e[2]) / max(e[4], .01))[0]

    def step(self, g):
        """One step's input: (wheel, held, pressed)."""
        p, self.now, self.aim = self.p, self.now + 1, g.aim
        alive = self.embers(g)
        by = {e[0]: e for e in alive}
        known = [e for e in alive if self.seen[e[0]] <= self.now]
        low = any((GROUND - e[2]) / max(e[4], .01) < 90 for e in known)
        if self.phase != 'idle' and self.target not in by:  # it went out, landed or cracked meanwhile
            self.target, self.phase = None, 'idle'
        wheel = held = pressed = 0
        if self.t > 0:
            self.t -= 1
            return wheel, held, pressed
        if self.phase == 'idle':
            self.target = self.pick(alive)
            if self.target is not None:
                self.phase, self.t, self.fixes = 'plan', steps(p.decide), 0
        elif self.phase == 'plan':
            e = by.get(self.target)
            if e is None:
                self.phase = 'idle'
            else:
                want = self.aim_for(e, self.rng.gauss(p.lead, p.lead_sd), steps(p.feedback + p.hop))
                size = abs(want - self.aim) / 120
                err = self.rng.gauss(0, p.rel * size + p.floor * (1.3 if low else 1))
                self.goal = max(-17000.0, min(-1000.0, want + err * 120))
                self.phase, self.stroke = 'move', 0.0
        elif self.phase == 'move':
            rate = (p.panic if low else p.sweep) / 60  # positions a step
            left = (self.goal - self.aim) / 120
            if abs(left) < .5:
                self.phase, self.t = 'look', steps(p.feedback)
            elif self.stroke >= p.reach:
                self.stroke, self.t = 0.0, steps(p.regrip)
            else:
                move = max(-rate, min(rate, left)) + self.carry
                wheel = int(move)
                self.carry = move - wheel
                self.stroke += abs(wheel)
        elif self.phase == 'look':
            e = by[self.target]
            want = self.aim_for(e, p.lead, steps(p.hop))
            off = (want - self.aim) / 100 + self.rng.gauss(0, p.eye)
            dist = max(30.0, math.hypot(e[1] - PX, e[2] - PY))
            wide = math.degrees((4.5 + R[e[5]]) / dist)
            at_end = self.aim in (-17000, -1000)
            if abs(off) > .8 * wide and self.fixes < p.tries and not at_end:
                self.fixes += 1
                self.phase = 'plan'
            else:
                self.phase, self.t = 'fire', steps(p.hop)
        elif self.phase == 'fire':
            if sum(h.alive for h in g.sh) < 3:
                pressed = held = E_CENTRE
                self.shots += 1
                e = by[self.target]
                flight = math.hypot(e[1] - PX, e[2] - PY) / 6
                self.busy[self.target] = self.now + int(flight) + steps(p.confirm)
                if p.watch and not low:
                    self.phase, self.t = 'idle', steps(p.hop) + int(flight) + steps(p.confirm)
                else:
                    self.phase, self.t = 'idle', steps(p.hop) + steps(60)
        return wheel, held, pressed


def play(game, prof, seed, seconds=300):
    """One game from wave 1 on the seed: what happened, a dict."""
    g = game.g
    game.fn_new(C.addressof(g), PLAY)
    g.seed = seed | 1
    rng = random.Random(seed * 7919 + len(prof.name))
    pl = Player(prof, rng)
    alive_at, first, over, splits, spawned, landed = [], None, None, 0, 0, 0
    left, n_alive, score, hits = g.left, 0, 0, 0
    wave_at = {}
    for t in range(seconds * 60):
        w, h, p = pl.step(g)
        game.step(wheel=w, held=h, pressed=p)
        n = sum(e.alive for e in g.em)
        came = (left - g.left) if g.left <= left else 0  # a new wave resets left
        spawned += came
        if g.score != score:
            hits += 1
        if n > n_alive + came - 0 and g.score != score:
            splits += 1  # a hit that left more embers than before: a cinder cracked
        left, n_alive, score = g.left, n, g.score
        wave_at.setdefault(g.wave, t)
        burnt = sum(b != GROWING for b in g.bed)
        if burnt > landed:
            landed = burnt
            first = t if first is None else first
        if t % 60 == 0:
            alive_at.append(n)
        if g.mode == LOST:
            over = t
            break
    return dict(first=first, over=over, splits=splits, spawned=spawned, hits=hits, shots=pl.shots, score=g.score,
                wave=g.wave, alive=alive_at, beds=landed, wave_at=wave_at)


def report(game, games=60, seconds=300, profiles=PROFILES):
    """The tables: each profile over `games` seeded games of at most `seconds`."""
    lines = []
    for prof in profiles:
        runs = [play(game, prof, 1000 + 37 * k, seconds) for k in range(games)]
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else float('nan')
        cap = seconds * 60
        over = [r['over'] if r['over'] is not None else cap for r in runs]
        first = [r['first'] if r['first'] is not None else cap for r in runs]
        surv = {s: sum(o >= s * 60 for o in over) * 100 // games for s in (30, 60, 90, 120, 180, 240, 300) if s <= seconds}
        sky = lambda a, b: [n for r in runs for n in r['alive'][a:b]]
        mean = lambda xs: sum(xs) / len(xs) if xs else float('nan')
        acc = sum(r['hits'] for r in runs) * 100 // max(1, sum(r['shots'] for r in runs))
        lines.append(
            f"{prof.name}: first bed lost {med(first) / 60:.0f} s (median), garden lost {med(over) / 60:.0f} s; "
            f"alive at {surv}; embers in the sky 0-30 s mean {mean(sky(0, 30)):.1f} max {max(sky(0, 30), default=0)}, "
            f"60-120 s mean {mean(sky(60, 120)):.1f}, 180-300 s mean {mean(sky(180, 300)):.1f} max {max(sky(180, 300), default=0)}; "
            f"embers a minute of play: {sum(r['spawned'] for r in runs) * 3600 / sum(over):.0f} from the cloud, "
            f"{sum(r['splits'] for r in runs) * 3600 / sum(over):.1f} more from cracks; hits a spurt {acc}%; "
            f"score {med([r['score'] for r in runs])}, wave {med([r['wave'] for r in runs])}; "
            f"wave 2 at {med([r['wave_at'].get(2, cap) for r in runs]) / 60:.0f} s, wave 5 at {med([r['wave_at'].get(5, cap) for r in runs]) / 60:.0f} s")
    return lines
