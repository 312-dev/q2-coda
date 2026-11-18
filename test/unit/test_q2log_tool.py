"""tools/q2log.py: the unified log merged by time with the heat and Bluetooth logs into one timeline, and its filters."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import q2log  # noqa: E402

LOG = """2026-11-17T14:35:01Z 5121000 I guard boot build=V001i last=forced
2026-11-17T14:35:02Z 5122500 W usb cable in, seen after its edge, 9 ms
== 3 lines lost while the card was away
2026-11-17T14:35:07Z 5127001 E bt rtk_hciattach: Failed to read local version
2026-11-17T14:35:07Z 5127900 I guard snapshot power-off
""".splitlines(True)
HEAT = """2026-11-17T14:35:00Z BOOT build=V001i last=forced
2026-11-17T14:35:02Z up=5122 t=41.5 die=41.5 g=ok
2026-11-17T14:35:07Z up=5127 t=41.6 die=41.6 g=ok
""".splitlines(True)
BT = """2026-11-17T14:35:03Z START codec=AAC
2026-11-17T14:35:03Z up=5123 kbps=256 acl=8 wq=0
""".splitlines(True)


def timeline(*args, **kw):
    out = io.StringIO()
    q2log.show([l for l in q2log.merge(q2log.parse(LOG, 'log'), q2log.parse(HEAT, 'heat'), q2log.parse(BT, 'bt'))
                if q2log.keep(l, kw.get('level', 'I'), kw.get('srcs', set()))], out)
    return out.getvalue().splitlines()


def test_the_three_logs_merge_by_time_then_uptime():
    assert [' '.join(l.split()) for l in timeline()] == [
        '2026-11-17T14:35:00Z heat BOOT build=V001i last=forced',
        '2026-11-17T14:35:01Z 5121.000 log I guard boot build=V001i last=forced',
        '2026-11-17T14:35:02Z 5122 heat up=5122 t=41.5 die=41.5 g=ok',  # 5122000 ms: before the log's 5122500
        '2026-11-17T14:35:02Z 5122.500 log W usb cable in, seen after its edge, 9 ms',
        '2026-11-17T14:35:02Z 5122.500 log GAP card away, 3 lines lost',
        '2026-11-17T14:35:03Z bt START codec=AAC',  # a note, no uptime: first in its second
        '2026-11-17T14:35:03Z 5123 bt up=5123 kbps=256 acl=8 wq=0',
        '2026-11-17T14:35:07Z 5127 heat up=5127 t=41.6 die=41.6 g=ok',
        '2026-11-17T14:35:07Z 5127.001 log E bt rtk_hciattach: Failed to read local version',
        '2026-11-17T14:35:07Z 5127.900 log I guard snapshot power-off',
    ]


def test_a_level_or_source_keeps_only_those_unified_lines():
    assert [l.split(None, 3)[3] for l in timeline(level='W')] == [
        'W usb cable in, seen after its edge, 9 ms', 'GAP card away, 3 lines lost',
        'E bt rtk_hciattach: Failed to read local version']
    assert [l.split(None, 3)[3] for l in timeline(srcs={'bt'})] == ['E bt rtk_hciattach: Failed to read local version']


def test_adb_dumps_through_q2guard_and_reads_the_others_oldest_file_first(monkeypatch, capsys):
    asked = []
    answers = {'q2guard --dump 2>/dev/null': LOG,
               f'cat {q2log.LOGS}/q2-heat.1 {q2log.LOGS}/q2-heat.0 2>/dev/null': HEAT,
               f'cat {q2log.LOGS}/q2-bt.1 {q2log.LOGS}/q2-bt.0 2>/dev/null': BT}
    monkeypatch.setattr(q2log, 'adb', lambda command, serial=None: asked.append((command, serial)) or answers[command])
    assert q2log.main(['--adb', '-s', 'Q2CODA', '--level', 'E']) == 0
    assert [c for c, _ in asked] == list(answers) and {s for _, s in asked} == {'Q2CODA'}
    assert capsys.readouterr().out.split(None, 3)[3] == 'E bt rtk_hciattach: Failed to read local version\n'
