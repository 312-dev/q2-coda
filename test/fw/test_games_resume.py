"""Quick resume and Restart through Games' page (docs/games-engine.md#quick-resume): a game in progress is kept in
/mnt/data/game-SLUG.run when it is held and read back at its next open, with no question asked; a finished game, a
corrupt or foreign file and Restart start fresh. Pop Stall, Cabin Solitaire, the Evening Puzzle and Ember Garden
here; Caller Ten, whose songs come from a worker, in test_game_callerten.py."""
import ctypes as C
import struct

import pytest

from test_engine import CENTER, ENGINE, NEXT, PLAY, PREV, RETURN, Eng, GameDef, api
from test_game_embergarden import ART as EMBER_ART, ART_FILE as EMBER_FILE, State
from test_game_popstall import ART as STALL_ART, ART_FILE as STALL_FILE, Stall
from test_game_solitaire import ART as SOL_ART, ART_FILE as SOL_FILE, Sol
from test_game_sudoku import Sudoku
from test_games import Page

RUN, HELD = 0, 1  # games.c's paused
ARTS = {STALL_FILE: STALL_ART, SOL_FILE: SOL_ART, EMBER_FILE: EMBER_ART}


def frames(p, n):
    for _ in range(n):
        p.now += 17
        api(p.fw)['frame'](None)


def until(p, ok, most=400):
    for _ in range(most):
        if ok():
            return
        frames(p, 1)
    raise AssertionError('never')


def play_stall(p, s):
    p.key(CENTER)  # launch
    frames(p, 40)
    assert s().mode == 1


def play_solitaire(p, s):
    p.key(PLAY)  # deal
    until(p, lambda: s().mode == 2)
    p.key(NEXT, NEXT)
    frames(p, 2)


def play_sudoku(p, s):
    p.key(CENTER)  # a Gentle puzzle, made over steps
    until(p, lambda: s().mode == 2)
    p.key(NEXT, NEXT, CENTER)  # the cursor on, the dial open
    frames(p, 3)


def play_ember(p, s):
    p.key(CENTER)  # from the title card
    frames(p, 90)
    assert s().mode == 1


# k (eng_games[]), state, slug, how a game gets under way, its mode then a finished one, a fresh open's,
# the mode Restart starts, and whether it is real time (resumed paused)
GAMES = {
    'popstall': (0, Stall, play_stall, 4, 0, 0, True),
    'solitaire': (1, Sol, play_solitaire, 4, 0, 1, False),
    'sudoku': (2, Sudoku, play_sudoku, 3, 0, 1, False),
    'embergarden': (4, State, play_ember, 2, 0, 1, True),
}


class Open:
    def __init__(self, fw, slug, files=None):
        self.k, self.kind, self.play, self.done, self.fresh, self.restarted, self.real = GAMES[slug]
        self.fw, self.slug, self.run = fw, slug, f'/mnt/data/game-{slug}.run'
        self.p = Page(fw, {**ARTS, **(files or {})})
        self.p.go(ENGINE + self.k)

    def s(self):
        en = self.fw.var('engine__en', C.c_void_p).value
        return self.kind.from_address(en + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)

    def keep(self):
        return self.fw.var(f'eng_{self.slug}', GameDef).keep

    def kept(self):
        return C.string_at(C.addressof(self.s()), self.keep())

    def quit(self):
        if self.p.s.paused != 2:
            self.p.key(RETURN)  # the menu
        self.p.key(PREV, CENTER)  # round from Continue to Quit
        self.p.g.show(None)
        assert not self.fw.var('engine__en', C.c_void_p).value

    def again(self):
        self.p.go(ENGINE + self.k)


def run_file(o):
    """The run file whole: its magic, length and seal right, the kept bytes."""
    b = o.p.card.files[o.run]
    magic, n = struct.unpack_from('<II', b)
    assert magic == 0x314e5552 and n == o.keep() and len(b) == n + 12
    return b[8:-4]


@pytest.mark.parametrize('leave', ['quit', 'home', 'screen off'])
@pytest.mark.parametrize('slug', GAMES)
def test_a_game_left_opens_again_where_it_was_with_no_question(fw, slug, leave):
    o = Open(fw, slug)
    assert o.p.s.paused == RUN and o.run not in o.p.card.files
    o.play(o.p, o.s)
    if leave == 'quit':
        o.p.key(RETURN)  # held under the menu: written then
        snap = o.kept()
        assert run_file(o) == snap
        o.quit()
    elif leave == 'screen off':
        fw.var('g_backlight_status', C.c_ubyte).value = 0
        frames(o.p, 1)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        assert o.p.s.paused == HELD
        snap = o.kept()
        assert run_file(o) == snap
        o.p.close()
    else:  # Home: the page closes over a running game
        o.p.close()
        snap = run_file(o)
    o = Open(fw, slug, {o.run: o.p.card.files[o.run]}) if leave != 'quit' else (o.again(), o)[1]
    assert o.kept() == snap and o.s().mode not in (o.done, o.fresh)
    assert o.p.s.paused == (HELD if o.real else RUN)  # a real-time game waits paused, its pause showing
    o.p.key(CENTER if o.real else NEXT)
    assert o.p.s.paused == RUN


@pytest.mark.parametrize('slug', GAMES)
def test_the_run_file_survives_a_restart_and_a_bad_one_starts_fresh(fw, slug):
    o = Open(fw, slug)
    o.play(o.p, o.s)
    o.p.key(RETURN)
    snap, good = o.kept(), o.p.card.files[o.run]
    o.p.close()
    for bad in (good[:-1] + bytes([good[-1] ^ 1]), good[:-4] + b'\0' * 8, b'RUN2' + good[4:], good[:4] + b'\1' + good[5:]):
        o = Open(fw, slug, {o.run: bad})  # torn, longer, foreign, another length
        assert o.s().mode == o.fresh and o.kept() != snap
        o.p.close()
    o = Open(fw, slug, {o.run: good})  # a power-off and on again
    assert o.kept() == snap


@pytest.mark.parametrize('slug', GAMES)
def test_a_finished_game_leaves_no_run_file_and_opens_fresh(fw, slug):
    o = Open(fw, slug)
    o.play(o.p, o.s)
    o.p.key(RETURN)
    assert o.run in o.p.card.files
    o.p.key(RETURN)  # Continue
    o.s().mode = o.done  # won, lost, solved
    o.quit()
    assert o.run not in o.p.card.files
    o.again()
    assert o.s().mode == o.fresh and o.p.s.paused == RUN


@pytest.mark.parametrize('slug', GAMES)
def test_restart_starts_a_new_game_of_the_same_mode_and_keeps_it_in_place_of_the_old(fw, slug):
    o = Open(fw, slug)
    o.play(o.p, o.s)
    o.p.key(RETURN)
    snap = o.kept()
    o.p.key(NEXT)
    assert o.p.s.choice == 1  # Continue, Restart, Quit
    o.p.key(CENTER)
    s = o.s()
    assert o.p.s.paused == RUN and s.mode == o.restarted and o.kept() != snap
    assert run_file(o) == o.kept()  # the old game is not what a power loss brings back
    if slug == 'popstall':
        assert (s.level, s.score, s.lives) == (1, 0, 3)
    if slug == 'solitaire':
        assert s.g.live and s.g.moves == 0 and s.nfl  # dealing, with the same Deal and Draw
    if slug == 'sudoku':
        assert s.glevel == s.v.level  # a new puzzle of its level being made
    if slug == 'embergarden':
        assert (s.score, s.wave) == (0, 1)


def test_ember_gardens_charred_beds_and_light_come_back_with_it(fw):
    o = Open(fw, 'embergarden')
    o.play(o.p, o.s)
    s = o.s()
    s.bed[0], s.bed_t[0] = 2, 1  # BURNING, its last step
    frames(o.p, 1)
    assert s.bed[0] == 3  # CHARRED: its box charred in the scene
    scene, pal = bytes(s.art), bytes(s.pal)
    o.p.key(RETURN)
    o.quit()
    o.again()
    s = o.s()
    assert bytes(s.art) == scene and bytes(s.pal) == pal
