"""leak_hunt.py's decisions on made-up metric series: what is a leak, and the exit status of a run that
finds one, finds none, or cannot start. No emulator, no build.

    python3 -m pytest tools/ci/test_leak_hunt.py -q
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import leak_hunt  # noqa: E402
import smoke  # noqa: E402


def s(windows=3, widgets=400, timers=10, rss=100000):
    return {'windows': windows, 'widgets': widgets, 'timers': timers, 'rss': rss}


def test_flat_series_is_no_leak():
    assert leak_hunt.verdict([s(), s(), s(), s(), s()]) == []


def test_a_count_that_rises_every_loop_is_a_leak_named_with_its_first_loop():
    found = leak_hunt.verdict([s(timers=10), s(timers=11), s(timers=12), s(timers=13)])
    assert len(found) == 1 and found[0].startswith('timers grew from loop 2: 10 -> 11 -> 12 -> 13')
    found = leak_hunt.verdict([s(), s(), s(widgets=412), s(widgets=424)])
    assert len(found) == 1 and found[0].startswith('widgets grew from loop 3: 400 -> 400 -> 412 -> 424')
    found = leak_hunt.verdict([s(), s(windows=4), s(windows=5)])
    assert found and found[0].startswith('windows grew from loop 2')


def test_a_count_that_rises_once_and_stays_is_a_cache_not_a_leak():
    assert leak_hunt.verdict([s(widgets=400), s(widgets=440), s(widgets=440), s(widgets=440)]) == []


def test_a_count_that_comes_back_to_its_baseline_is_not_a_leak():
    assert leak_hunt.verdict([s(timers=10), s(timers=12), s(timers=11), s(timers=10)]) == []


def test_rss_is_a_leak_only_past_the_tolerance_over_the_baseline():
    tol = leak_hunt.RSS_TOL_KB
    assert leak_hunt.verdict([s(rss=100000), s(rss=100000 + tol), s(rss=100000 + tol - 1)]) == []
    found = leak_hunt.verdict([s(rss=100000), s(rss=100000 + tol // 2), s(rss=100000 + tol + 1), s(rss=100000 + 2 * tol)])
    assert len(found) == 1 and found[0].startswith('rss grew from loop 3')
    assert leak_hunt.verdict([s(rss=100000), s(rss=100000 + 2 * tol), s(rss=100000)]) == []  # a spike that went back


def test_every_leaking_metric_is_named():
    found = leak_hunt.verdict([s(), s(timers=11, rss=200000), s(timers=12, rss=300000)])
    assert [f.split()[0] for f in found] == ['timers', 'rss']


def test_too_few_samples_find_nothing():
    assert leak_hunt.verdict([]) == [] and leak_hunt.verdict([s()]) == []


class FakeEmu:
    def __init__(self, slot, log=print):
        self.slot, self.copy, self.pid = slot, pathlib.Path('/nonexistent'), 1

    def boot(self):
        pass

    def alive(self):
        pass


@pytest.fixture
def run(tmp_path, monkeypatch):
    """main() on a fake build, a fake lease and a tour that does nothing; returns (exit, puts)."""
    (tmp_path/'rootfs.squashfs').write_text('')
    puts: list = []
    monkeypatch.setattr(leak_hunt.emu_pool, 'put', lambda k: puts.append(k))
    monkeypatch.setattr(leak_hunt.emu_pool, 'copy_dir', lambda k: tmp_path)
    monkeypatch.setattr(leak_hunt, 'SETTLE_S', 0)
    monkeypatch.setattr(leak_hunt, 'tour', lambda e, say: None)
    monkeypatch.setattr(smoke, 'Emulator', FakeEmu)

    def go(lease=lambda *a: 1, samples=None, loops=4):
        monkeypatch.setattr(smoke, 'lease', lease)
        it = iter(samples or [s()] * loops)
        monkeypatch.setattr(leak_hunt, 'sample', lambda pid: next(it))
        return leak_hunt.main([str(tmp_path), '--loops', str(loops)]), puts
    return go


def test_exit_0_when_nothing_grew_and_the_slot_goes_back(run):
    assert run() == (0, [1])


def test_exit_1_when_something_grew(run):
    assert run(samples=[s(), s(timers=11), s(timers=12), s(timers=13)]) == (1, [1])


def test_exit_3_when_no_emulator_came_free(run):
    def none(*a):
        raise smoke.Unavailable('no pool emulator came free')
    assert run(lease=none) == (3, [])


def test_exit_3_and_the_slot_back_when_the_tour_cannot_finish(run, monkeypatch):
    def lost(e, say):
        raise smoke.Failed('expected playing_page on top')
    monkeypatch.setattr(leak_hunt, 'tour', lost)
    assert run() == (3, [1])
