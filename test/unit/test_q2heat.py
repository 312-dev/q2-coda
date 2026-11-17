"""tools/q2heat.py: q2guard's heat log read back as a timeline, with the minute the temperature (or, with none, the
CPU) rose fastest and the processes on top then."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import q2heat  # noqa: E402


def line(sec, up, t='-', cpu='5', top='-'):
    return (f'2026-11-17T14:{sec // 60:02d}:{sec % 60:02d}Z up={up} t={t} raw=- lv=80 mv=4100 st=Charging cab=1 '
            f'udc=configured gad=adb scr=1 br=80 wifi=0 bt=0 mhz=- cpu={cpu} top={top} thr=27 np=60 g=ok\n')


def test_the_fastest_temperature_minute_and_its_top_processes():
    log = ['2026-11-17T14:00:00Z BOOT build=1.8.2 last=forced\n']
    temps = [40.0, 40.2, 40.3, 40.5, 40.6, 40.8, 41.0, 41.1, 41.3, 41.5, 41.6, 41.8, 42.0,   # slow: 2 C in a minute
             44.0, 46.0, 48.0, 50.0, 52.0, 54.0, 55.0, 55.5, 56.0, 56.2, 56.4, 56.5]         # fast
    for i, t in enumerate(temps):
        top = 'adbd:80.5,demo:10.0' if 12 <= i <= 24 else 'demo:3.0'
        log.append(line(i * 5, 100 + i * 5, t=t, top=top))
    samples, notes = q2heat.parse(log)
    a, b, rise, kind = q2heat.fastest_minute(samples)
    assert kind == 'temp' and 150 <= a.up <= 165 and 55 <= b.up - a.up <= 65 and rise > 12
    assert q2heat.top_during(samples, a, b)[0][0] == 'adbd'
    assert [n.text for n in notes] == ['BOOT build=1.8.2 last=forced']


def test_with_no_temperature_the_cpu_rise_is_flagged_instead():
    log = [line(i * 5, i * 5, cpu=c, top='scan:90.0' if c > 50 else 'demo:2.0')
           for i, c in enumerate([5] * 12 + [95] * 13)]
    samples, _ = q2heat.parse(log)
    a, b, rise, kind = q2heat.fastest_minute(samples)
    assert kind == 'cpu' and rise == 90 and q2heat.top_during(samples, a, b)[0][0] == 'scan'


def test_a_rise_never_spans_a_reboot():
    """Even where the uptimes would make a minute across it: a short session, then a reboot into a hot one."""
    log = [line(0, 0, t=30.0), '2026-11-17T14:01:00Z BOOT build=x last=forced\n', line(60, 60, t=60.0),
           line(120, 120, t=60.5)]
    samples, _ = q2heat.parse(log)
    a, b, rise, _ = q2heat.fastest_minute(samples)
    assert a.boot == b.boot and rise < 5


def test_the_report_reads_a_real_line_and_skips_garbage():
    log = ['garbage\n', line(0, 1, t=41.5, top='hciplayer:12.3,demo:8.0'), line(60, 61, t=43.0),
           '2026-11-17T14:02:00Z GAP card away since 2026-11-17T13:50:00Z held=3 dropped=0\n']
    out = io.StringIO()
    samples, notes = q2heat.parse(log)
    q2heat.report(samples, notes, out)
    text = out.getvalue()
    assert 'hciplayer 12.3%' in text and '** GAP card away' in text and 'Fastest rise: +1.5 C' in text


def test_under_a_minute_of_samples_flags_the_steepest_step():
    """A log shorter than a minute (a quick reboot loop, say): the steepest step between neighbours instead."""
    log = [line(0, 10, t=30.0), line(5, 15, t=30.5), line(10, 20, t=33.0), line(15, 25, t=33.2)]
    samples, _ = q2heat.parse(log)
    a, b, rise, kind = q2heat.fastest_minute(samples)
    assert kind == 'temp' and (a.up, b.up) == (15, 20) and round(rise, 1) == 2.5


def test_a_log_with_no_samples_says_so_and_leaves_unknown_notes_out():
    out = io.StringIO()
    samples, notes = q2heat.parse(['2026-11-17T14:00:00Z BOOT build=x last=clean\n',
                                   '2026-11-17T14:00:01Z SOMETHING else\n'])
    assert q2heat.report(samples, notes, out) is None
    text = out.getvalue()
    assert '** BOOT build=x' in text and 'SOMETHING' not in text and 'Too few samples to find a rise.' in text


def test_main_reads_the_files_oldest_first_and_standard_input(tmp_path, monkeypatch, capsys):
    old, new = tmp_path / 'q2-heat.1', tmp_path / 'q2-heat.0'
    old.write_text(line(0, 100, t=40.0))
    new.write_text(line(60, 160, t=45.0, top='adbd:50.0'))
    q2heat.main([str(old), str(new)])
    assert 'Fastest rise: +5 C' in capsys.readouterr().out
    monkeypatch.setattr(sys, 'stdin', io.StringIO(line(0, 1, t=40.0) + line(60, 61, t=41.0)))
    q2heat.main(['-'])
    assert 'Fastest rise: +1 C' in capsys.readouterr().out
