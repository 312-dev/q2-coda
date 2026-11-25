"""tools/usbtrace_report.py: a pulled q2-usbtrace log read back per plug cycle (docs/building.md#usb-trace-report),
the unified log's usb lines set in each, and the trace turned into the state machine's events."""
import io
import sys

import usbtrace_report as R

TRACE = """\
== 3 lines lost while the card was away
# a comment
not-a-time start
1000.000000 start pd17=forwarded pd18=edges pd19=edges mem=ok i2c=ok regdump=ok uevent=ok udc=ok wall=1795400000
1000.000000 udc not_attached
1000.000000 s PD17=1 PD18=1 PD19=1 PC20=0 PE22=0 on=0 id=device mode=otg conn=connect dev=0 snd=0
1005.000000 e PD18 0
1005.000000 on 0
1005.200000 shadow: would grant at 1005.200000; src-trial; ID low: grant
1008.000000 e PD17 0 fwd
1008.000000 s PC20=1 PE22=1
1008.000000 stock otg-on
1008.560000 u add usb 1-1
1008.830000 u add sound pcmC1D0p
1008.830000 u add sound card1
1009.000000 e PD19 0
1028.000000 u remove usb 1-1
1028.000000 u remove sound pcmC1D0p
1028.000000 u change power_supply usb
1028.000000 shadow: would revoke at 1028.000000; host-end; gone
1028.600000 s PC20=0 PE22=0
1028.600000 stock otg-off
1030.200000 s PD17=1
1060.200000 cycle n=1 at=1005.000000 type=dac from=idle vbus+=8.000 vbus-=30.200 id-=5.000 id+=- on1=- addr=- conf=- dev+=8.560 dev-=28.000 pcm+=8.830 stock-on=8.000 stock-off=28.600 shadow-on=0.200 shadow-replug=- stock=grant shadow=grant agree
1100.000000 udc configured
1100.000000 s PD17=0
1100.000000 s on=?
1101.000000 shadow: disagree stock=otg-on at 1101.000000 shadow=none (src-candidate)
1101.000000 unsafe: bus power on with online 1
1131.000000 cycle n=2 type=computer from=idle stock=grant shadow=none DISAGREE UNSAFE
1200.000000 wake slept=5.000
1200.000000 s PD17=1 on=0
1201.000000 e PD17 0 fwd
1202.000000 clock wall=1795400200
"""
ULOG = """\
2026-11-25T05:35:05Z 1008000 I usb otg-on PD18=0 PD17=0
2026-11-25T05:35:05Z 1008100 I player q2player: mcl-stop
2026-11-25T05:35:06Z 1060200 I usbtrace usbtrace cycle 1 dac: stock grant at 8.000 s, shadow grant at 0.200 s; agree
not a line
2026-11-25T06:00:00Z 9999999 I usb far away
"""


def test_parse_keeps_timed_lines_and_their_fields():
    lines = R.parse(TRACE.splitlines())
    assert lines[0].kind == 'start' and lines[0].t == 1000 * 10**9
    assert lines[2].fields()['PD17'] == '1'
    assert R.stamp('12.5') == 12_500_000_000 and R.stamp('12') == 12 * 10**9 and R.stamp('x') is None
    assert R.parse(['5.0']) == []  # no kind


def test_fsm_events_feed_each_level_once_and_every_online_read():
    ev = list(R.fsm_events(R.parse(TRACE.splitlines())))
    t0 = 1000 * 10**9
    assert ev[0] == (t0, 'start', 0)
    assert (t0, R.POWER, 0) in ev and (t0, R.VBUS, 0) in ev and (t0, R.ONLINE, 0) in ev and (t0, R.IDLOW, 0) in ev
    assert (t0, R.UDC, 0) in ev and (t0, R.HOSTDEV, 0) in ev
    assert (1005 * 10**9, R.IDLOW, 1) in ev and (1005 * 10**9, R.ONLINE, 0) in ev
    assert (1008 * 10**9, R.VBUS, 1) in ev and (1008 * 10**9, R.POWER, 1) in ev
    assert [e for e in ev if e[1] == R.HOSTDEV] == [(t0, R.HOSTDEV, 0), (1008_560_000_000, R.HOSTDEV, 1),
                                                     (1028 * 10**9, R.HOSTDEV, 0)]
    assert [e[2] for e in ev if e[1] == R.PCM] == [1, 0]
    assert (1100 * 10**9, R.UDC, 1) in ev
    assert (1100 * 10**9, R.ONLINE, 0) in ev, 'a VBUS change with no read in its line: the last read again'
    assert (1200 * 10**9, R.WAKE, 0) in ev and (1200 * 10**9, R.VBUS, 0) in ev, 'a wake feeds every level again'
    assert not [e for e in ev if e[0] == 1009 * 10**9], 'PD19 feeds nothing'


def test_cycles_group_the_lines_by_the_tracer_s_summaries():
    cs = R.cycles(R.parse(TRACE.splitlines()))
    assert [c.n for c in cs] == [1, 2, 0]
    assert cs[0].summary['type'] == 'dac' and cs[0].summary['verdict'] == 'agree' and not cs[0].bad
    assert cs[1].bad and cs[1].summary['verdict'] == 'DISAGREE UNSAFE'
    assert cs[2].summary['type'] == 'open' and cs[2].start == 1200 * 10**9
    assert R.Cycle(5, {}).start == 0 and R.Cycle(5, {}).end == 0
    assert R.cycles(R.parse(['1.0 start', '2.0 clock wall=1'])) == []


def test_the_report_prints_each_cycle_with_stock_s_words_and_counts_the_bad_ones():
    out = io.StringIO()
    bad = R.report(R.cycles(R.parse(TRACE.splitlines())), R.read_ulog(ULOG.splitlines()), out)
    text = out.getvalue()
    assert bad == 1
    assert 'cycle 1 dac at 1005.000 (from idle): stock grant, shadow grant, agree' in text
    assert 'milestones: vbus+ 8.000, vbus- 30.200, id- 5.000, dev+ 8.560' in text
    assert '+3.000 stock otg-on' in text and '+0.200 shadow: would grant' in text
    assert '+3.000 ulog I usb otg-on PD18=0 PD17=0' in text
    assert 'usbtrace cycle 1 dac' in text and 'mcl-stop' not in text and 'far away' not in text
    assert '+1.000 unsafe: bus power on with online 1' in text
    assert text.splitlines()[-1] == '3 cycles: 1 computer, 1 dac, 1 open; 1 disagreed or unsafe'


def test_main_reads_files_and_stdin(tmp_path, monkeypatch, capsys):
    log, ulog = tmp_path / 'q2-usbtrace.0', tmp_path / 'q2-log.0'
    log.write_text(TRACE)
    ulog.write_text(ULOG)
    assert R.main([str(log), '--ulog', str(ulog)]) == 1
    assert 'ulog I usb otg-on' in capsys.readouterr().out
    monkeypatch.setattr(sys, 'stdin', io.StringIO('1.0 start\n2.0 cycle n=1 type=charger stock=none shadow=none agree\n'))
    assert R.main(['-']) == 0
    assert '1 cycles: 1 charger; 0 disagreed or unsafe' in capsys.readouterr().out
