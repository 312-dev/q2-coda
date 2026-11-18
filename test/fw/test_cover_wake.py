"""patch/cover_wake.c: stock's two cover threads park on a futex until a request wakes them, instead of their 50 ms
and 100 ms polls (docs/internals.md#cover-threads)."""
import ctypes as C

import pytest

ALBUM, PLAY = 0, 1
WAIT, WAKE = 128, 129  # FUTEX_WAIT_PRIVATE, FUTEX_WAKE_PRIVATE


class Covers:
    """The two threads' stock state as the test sets it, and the futex calls the waits and wakes make."""
    def __init__(self, fw):
        self.fw, self.tasks, self.futex = fw, [0], []
        self.gen = fw.var('cover_wake__cover_gen', C.c_int * 2)
        self.parked = fw.var('cover_wake__cover_parked', C.c_int * 2)
        self.state = fw.var('fw_ctx_albumcover_taskstate', C.c_int)
        fw.var('fw_ctx_plist_albumtasklist', C.c_ulong).value = 0x5000  # the task list, as platform_init made it
        self.flag = fw.var('fw_ctx_g_play_cover_info', C.c_ubyte)
        self.during = None  # called inside a futex wait: what another thread does while this one sleeps
        fw.mock('fw_list_size', lambda d, *_: self.tasks.pop(0) if len(self.tasks) > 1 else self.tasks[0])
        fw.mock('cover_futex', self.call)
        fw.mock('stock_set_coverinfo_trampoline', self.set_info)
        self.album = fw.fn('ringnav_album_cover_wait', C.c_int, C.c_uint)
        self.play = fw.fn('ringnav_play_cover_wait', C.c_int, C.c_uint)
        self.set = fw.fn('cover_set_info', C.c_int, C.c_char_p, C.c_char_p, C.c_int)
        self.poll = fw.fn('cover_wake_poll', None)

    def call(self, word, op, val, timeout, *_):
        which = [C.addressof(self.gen) + 4 * i for i in (0, 1)].index(word)
        ts = tuple((C.c_long * 2).from_address(timeout)) if timeout else None
        self.futex.append((which, op, val, ts, self.parked[which]))
        if op == WAIT and self.during:
            during, self.during = self.during, None
            during()
        return 0

    def set_info(self, *_):
        self.flag.value = 1  # stock's player_set_coverinfo: the start flag, under its lock
        return 0

    def sleeps(self):
        return [c[1][0] for c in self.fw.calls('fw_usleep')]


@pytest.fixture
def cv(fw):
    return Covers(fw)


def test_with_no_request_the_player_thread_parks_instead_of_polling(cv):
    for _ in range(3):
        cv.play(100000)
    assert cv.sleeps() == []  # stock's thread slept 100 ms each round
    assert cv.futex == [(PLAY, WAIT, 0, (5, 0), 1)] * 3  # parked, COVER_WAIT_S at most
    assert list(cv.parked) == [0, 0]


def test_a_cover_request_wakes_the_parked_player_thread(cv):
    cv.during = lambda: cv.set(b'/mnt/mmc/a.flac', b'a', 0)  # Now Playing's next track, while it sleeps
    cv.play(100000)
    assert cv.futex[1][:3] == (PLAY, WAKE, 1) and cv.gen[PLAY] == 1
    cv.play(100000)  # the thread found the flag; with it still up it keeps stock's pace
    assert cv.sleeps() == [100000]


def test_a_request_before_the_wait_moves_the_word_it_waits_on(cv):
    cv.set(b'/mnt/mmc/a.flac', b'a', 0)  # not parked: no wake call, but the generation moves
    assert cv.futex == [] and cv.gen[PLAY] == 1
    cv.flag.value = 0  # the thread took it (player_get_coverinfo)
    cv.play(100000)
    assert cv.futex == [(PLAY, WAIT, 1, (5, 0), 1)]


def test_the_album_thread_parks_with_no_tasks_and_while_paused(cv):
    for state, tasks in ((0, 0), (1, 0), (2, 3)):  # idle, done, a page scrolling (stock's pause) with rows queued
        cv.state.value, cv.tasks = state, [tasks]
        cv.album(50000)
    assert cv.sleeps() == [] and [f[1] for f in cv.futex] == [WAIT] * 3


def test_the_album_thread_keeps_stocks_pace_while_it_has_work(cv):
    for state, tasks in ((1, 4), (0, 1), (3, 0), (6, 0)):  # rows to cover, or a cache to clear
        cv.state.value, cv.tasks = state, [tasks]
        cv.album(50000)
    assert cv.sleeps() == [50000] * 4 and cv.futex == []


def test_the_ui_pass_wakes_the_parked_album_thread_for_a_request(cv):
    cv.state.value, cv.tasks = 1, [0]
    cv.parked[ALBUM] = 1  # the thread is in its wait
    cv.poll()
    assert cv.futex == []  # nothing asked: no syscall on the pass
    cv.state.value = 2
    cv.tasks = [5]
    cv.poll()
    assert cv.futex == []  # paused: the thread would only park again
    cv.state.value = 1  # the page's scroll ended: its rows' tasks go
    cv.poll()
    assert [f[:3] for f in cv.futex] == [(ALBUM, WAKE, 1)] and cv.gen[ALBUM] == 1 and cv.parked[ALBUM] == 0
    cv.poll()
    assert len(cv.futex) == 1  # one wake a park


def test_the_ui_pass_leaves_a_busy_album_thread_alone(cv):
    cv.state.value, cv.tasks = 1, [5]
    cv.poll()
    assert cv.futex == [] and cv.gen[ALBUM] == 0


def test_a_task_queued_as_the_album_thread_parks_is_not_lost(cv):
    cv.state.value, cv.tasks = 1, [0, 1]  # empty at its first look, queued by its second
    cv.album(50000)
    assert cv.futex == [] and cv.sleeps() == []  # it goes straight back to work
