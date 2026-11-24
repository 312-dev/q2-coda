"""patch/playback.c: the order Shuffle plays a local queue in (shuffle_own, playback_shuffle), as a property of
every queue size and seed: a permutation with the playing song first, walked by Next and Previous, redrawn at
each turn on, kept through queue edits, uniform, and held in 4 bytes a song."""
import ctypes as C
import random

import pytest

from conftest import libc
from test_playback import Player, LIBRARY, player  # noqa: F401

SIZES = (1, 2, 3, 7, 64, 999, 1000, 9198, 20000)
# rand_below's generator starts from any nonzero word: these are fixed, so every run draws the same orders
SEEDS = [random.Random(7).getrandbits(32) | 1 for _ in range(4)]


def shuffled(player, n, pos=0, seed=None):
    """A library queue of n songs with Shuffle on, taken as Shuffle Songs while song pos plays."""
    if seed is not None:
        player.rng(seed)
    player.clear()
    player.load([player.song(b'/m/%03d/%05d.flac' % (i % 613, i), track=i % 19) for i in range(n)], pos)
    player.mcl.type, player.mcl.mode = LIBRARY, 2
    assert player.fw.fn('playback_shuffle', C.c_int, C.c_int)(2) == 1
    return player


def check(player, n, pos):
    order = player.order()
    assert sorted(order) == list(range(n)), 'every song once'
    assert order[player.s.cursor] == pos == player.mcl.pos, 'the cursor is on the song playing'
    return order


@pytest.mark.parametrize('n', SIZES)
def test_the_order_is_a_permutation_with_the_playing_song_first(player, n):
    for seed in SEEDS:
        pos = seed % n
        order = check(shuffled(player, n, pos, seed), n, pos)
        assert order[0] == pos and player.s.cursor == 0 and player.history() == [pos]
        assert player.seen() == {pos}


@pytest.mark.parametrize('n', (2, 3, 7, 64, 9198))
def test_next_and_previous_walk_the_order_and_its_history(player, n):
    order = check(shuffled(player, n, n // 2, SEEDS[0]), n, n // 2)
    steps = min(n - 1, 150)
    assert player.up_next(steps) == order[1:steps + 1]
    played = (C.c_uint * 4096)()
    for k in range(1, steps + 1):
        player.next(0)
        assert player.mcl.pos == order[k] and player.s.cursor == k
        assert player.history() == order[:k + 1]
        assert list(played[:player.fw.fn('playback_played', C.c_uint, C.POINTER(C.c_uint), C.c_uint)(played, 4096)]) == order[:k]
    for k in range(steps - 1, -1, -1):  # Previous is the row above, and the order below it stands
        player.prev()
        assert player.mcl.pos == order[k] and player.s.cursor == k
        assert player.history() == order[:k + 1] and player.up_next(steps - k) == order[k + 1:steps + 1]
    assert player.order() == order
    player.next(0)
    assert player.mcl.pos == order[1]  # and Next plays the same row again


@pytest.mark.parametrize('n', (1, 2, 5, 64, 300))
def test_a_pass_plays_every_song_once_and_the_next_pass_is_another_order(player, n):
    order = check(shuffled(player, n, 0, SEEDS[1]), n, 0)
    listed = player.up_next(2 * n - 1)
    assert listed[:n - 1] == order[1:] and sorted(listed[n - 1:]) == list(range(n)), 'Up Next runs on into the next pass'
    played = [player.mcl.pos] + player.run(2 * n - 1)
    assert played == order + listed[n - 1:], 'and what plays is what it listed'
    assert sorted(played[:n]) == sorted(played[n:]) == list(range(n))
    assert n < 8 or played[:n] != played[n:]


def test_turning_shuffle_off_and_on_draws_a_new_order_each_time(player):
    orders = set()
    player.rng(SEEDS[2])
    for _ in range(50):
        orders.add(tuple(check(shuffled(player, 24, 5), 24, 5)))
        player.clear()  # Shuffle off (ringnav_mode's playback_clear): the queue's own order plays again
        assert not player.s.active and not player.s.order
    assert len(orders) == 50


@pytest.mark.parametrize('n', (2, 9, 300, 9198))
def test_queue_edits_keep_the_order_a_permutation_in_its_sequence(fw, player, n):
    insert = fw.fn('playback_insert', None, C.c_uint, C.c_uint, C.c_int)
    move = fw.fn('playback_move', C.c_int, C.c_uint)
    poll = fw.fn('playback_poll', None)
    order = check(shuffled(player, n, 1, SEEDS[3]), n, 1)
    rows = player.deques[player.handle]
    before = [rows[i] for i in order[1:]]
    # Add to Queue: three songs at the queue's end, last in this pass, in queue order
    new = [player.song(b'/new/%d.flac' % i) for i in range(3)]
    rows[n:n] = new
    insert(n, 3, 0)
    check(player, n + 3, 1)
    assert [rows[i] for i in player.order()[1:]] == before + new
    # Play Next: one of them plays next, the rest as they stood
    assert move(n + 1) == n + 1
    assert [rows[i] for i in player.up_next(n + 2)] == [new[1]] + before + [new[0], new[2]]
    player.next(0)
    assert rows[player.mcl.pos] == new[1]
    check(player, n + 3, n + 1)
    # Remove from Queue: a song not playing leaves, the rest keep their sequence
    gone = player.order()[-1]
    kept = [rows[i] for i in player.order() if i != gone]
    at = player.mcl.pos
    del rows[gone]
    player.mcl.pos = at - (gone < at)
    poll()
    check(player, n + 2, player.mcl.pos)
    assert [rows[i] for i in player.order()] == kept


def chi_square(counts, expected):
    return sum((c - expected) ** 2 / expected for row in counts for c in row)


def test_every_song_is_as_likely_in_every_place(player):
    """7 songs after the playing one, over 7,000 draws from one generator as the Q2 runs it, seeded once: each
    song's count in each place against 1,000, chi-square with 36 degrees of freedom (63.0 is its 0.3% point)."""
    n, draws = 8, 7000
    counts = [[0] * n for _ in range(n)]
    shuffled(player, n, 0, SEEDS[0])
    redraw = player.fw.fn('playback_shuffle', C.c_int, C.c_int)
    for _ in range(draws):
        assert redraw(2) == 1
        for place, song in enumerate(player.order()):
            counts[song][place] += 1
    assert counts[0][0] == draws
    rest = [row[1:] for row in counts[1:]]
    assert chi_square(rest, draws / (n - 1)) < 63.0, rest


def test_a_song_of_a_large_queue_lands_evenly_and_beside_no_neighbour(player):
    """9,198 songs, 400 draws: where song 1,234 lands, in 10 equal bands (9 degrees of freedom, 25.5 its 0.25%
    point), and how often a song is followed by the queue's next, which a uniform order does once a draw."""
    n, draws, bands = 9198, 400, 10
    counts = [0] * bands
    shuffled(player, n, 0, SEEDS[1])
    redraw = player.fw.fn('playback_shuffle', C.c_int, C.c_int)
    runs = 0
    for _ in range(draws):
        assert redraw(2) == 1
        order = player.order()
        counts[(order.index(1234) - 1) * bands // (n - 1)] += 1
        runs += sum(b == a + 1 for a, b in zip(order[1:], order[2:]))
    assert chi_square([counts], draws / bands) < 25.5, counts
    assert 0.8 * draws < runs < 1.2 * draws, runs


@pytest.mark.parametrize('n', (1000, 9198, 20000))
def test_the_order_holds_four_bytes_a_song(player, n):
    libc.malloc_usable_size.restype, libc.malloc_usable_size.argtypes = C.c_size_t, [C.c_void_p]
    usable = lambda p: libc.malloc_usable_size(C.cast(p, C.c_void_p))
    s = shuffled(player, n, 0, SEEDS[0]).s
    assert not s.cycle and not s.groups, 'no next pass and no album table until something reads one'
    assert usable(s.order) + usable(s.mark) <= 4 * n + 64
    assert usable(s.history) <= 4 * 4096 + 32  # what played, whatever the queue's size
    player.up_next(n + 1)  # a listing past this pass's end draws the next one: 2 bytes a song more
    assert s.cycle and usable(s.cycle) <= 2 * n + 32
    player.run(n)  # into the next pass: it is this one, and the one after is not drawn
    assert not s.cycle and sorted(player.order()) == list(range(n))


def test_a_queue_short_enough_to_save_holds_its_next_pass_too(player):
    """Up to SAVE_LIMIT (999) songs the snapshot holds both passes, so the save draws the next one: 6 bytes a song."""
    libc.malloc_usable_size.restype, libc.malloc_usable_size.argtypes = C.c_size_t, [C.c_void_p]
    s = shuffled(player, 999, 0, SEEDS[0]).s
    assert s.cycle and not s.groups
    assert sum(libc.malloc_usable_size(C.cast(p, C.c_void_p)) for p in (s.order, s.mark, s.cycle)) <= 6 * 999 + 96
