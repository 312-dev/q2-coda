"""tools/q2log.py: the unified log merged by time with the heat and Bluetooth logs into one timeline, its filters, and its
readers (adb, saved files, standard input).
"""
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
               f'cd {q2log.LOGS}; cat q2-heat.3 q2-heat.2 q2-heat.1 q2-heat.0 2>/dev/null': HEAT,
               f'cd {q2log.LOGS}; cat q2-bt.3 q2-bt.2 q2-bt.1 q2-bt.0 2>/dev/null': BT}
    monkeypatch.setattr(q2log, 'adb', lambda command, serial=None: asked.append((command, serial)) or answers[command])
    assert q2log.main(['--adb', '-s', 'Q2CODA', '--level', 'E']) == 0
    assert [c for c, _ in asked] == list(answers) and {s for _, s in asked} == {'Q2CODA'}
    assert capsys.readouterr().out.split(None, 3)[3] == 'E bt rtk_hciattach: Failed to read local version\n'


def test_a_gap_before_any_line_and_lines_with_no_time_are_dropped():
    """card_store.c's lost-lines note needs a line before it to take its time from; anything without a time is no
    line of either kind."""
    lines = q2log.parse(['== 4 lines lost while the card was away\n', 'not a log line\n', *LOG[:1]], 'log')
    assert [l.text for l in lines] == ['I guard boot build=V001i last=forced']


def test_adb_runs_exec_out_with_the_serial_only_when_given(monkeypatch):
    runs = []

    class Done:
        stdout = 'a\nb\xe9\n'.encode('latin-1')  # bytes not UTF-8 come back replaced, not raised

    monkeypatch.setattr(q2log.subprocess, 'run', lambda args, **kw: runs.append((args, kw)) or Done())
    assert q2log.adb('q2guard --dump') == ['a\n', 'b�\n']
    assert q2log.adb('cat x', 'Q2CODA') == ['a\n', 'b�\n']
    assert [r[0] for r in runs] == [['adb', 'exec-out', 'q2guard --dump'], ['adb', '-s', 'Q2CODA', 'exec-out', 'cat x']]
    assert all(r[1] == dict(check=True, capture_output=True) for r in runs)


def test_saved_files_and_standard_input_merge_as_the_q2_s_do(tmp_path, monkeypatch, capsys):
    """The unified log's files oldest first (here one of them on standard input), with the heat and Bluetooth logs."""
    old, new, heat, bt = (tmp_path / n for n in ('q2-log.1', 'q2-log.0', 'q2-heat.0', 'q2-bt.0'))
    old.write_text(''.join(LOG[:2])), heat.write_text(''.join(HEAT)), bt.write_text(''.join(BT))
    monkeypatch.setattr(q2log.sys, 'stdin', io.StringIO(''.join(LOG[2:])))
    assert q2log.main([str(old), '-', '--heat', str(heat), '--bt', str(bt)]) == 0
    assert capsys.readouterr().out.splitlines() == timeline()
    new.write_text(''.join(LOG))
    assert q2log.main([str(new), '--src', 'usb']) == 0
    assert [l.split(None, 3)[3] for l in capsys.readouterr().out.splitlines()] == [
        'W usb cable in, seen after its edge, 9 ms']


def test_with_nothing_to_read_it_says_what_it_needs(capsys):
    try:
        q2log.main([])
    except SystemExit as e:
        assert e.code == 2
    else:
        raise AssertionError('no files and no --adb ran on')
    assert 'files, --heat, --bt or --adb' in capsys.readouterr().err


def test_btev_over_adb_reads_the_bluetooth_event_log_alone(monkeypatch, capsys):
    """q2guard --dump-bt flushes what q2guard holds, then prints q2-btev.3 to .0: unified lines, shown and filtered
    as the unified log's, with no heat or link log merged in (they would repeat nothing it lacks)."""
    asked = []
    btev = [l for l in LOG if ' bt ' in l or ' guard ' in l]
    monkeypatch.setattr(q2log, 'adb', lambda command, serial=None: asked.append(command) or btev)
    assert q2log.main(['--adb', '--btev', '--level', 'W']) == 0
    assert asked == ['q2guard --dump-bt 2>/dev/null']
    assert [' '.join(l.split()[3:]) for l in capsys.readouterr().out.splitlines()] == [
        'E bt rtk_hciattach: Failed to read local version']
