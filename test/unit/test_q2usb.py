"""tools/q2usb.py: the USB record (patch/usb_record.c) read back, with the back-feed, an empty port powered and a plug
that never enumerated flagged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import q2usb  # noqa: E402


def line(ms, event, pc20=0, vbus=0, udc='not_attached', pd18=1, hostdev='?', wall=1763400000):
    # the format usb_record.c writes
    return (f'{wall} {ms} {event} PD18={pd18} PD17={0 if pc20 or vbus else 1} PD19=1 PC20={pc20} PE22={pc20} '
            f'otg={pc20} id={"host" if pd18 == 0 else "device"} mode=? conn=0 vbus={vbus} udc={udc} hostdev={hostdev}\n')


def kinds(lines):
    return [text.split()[0] for _, text in q2usb.flags(q2usb.parse(lines))]


def test_a_clean_plug_and_unplug_flags_nothing():
    log = [line(1000, 'in', vbus=1, udc='default'), line(6000, 'in+5s', vbus=1, udc='configured'),
           line(60000, 'out'), line(65000, 'out+5s'),
           '== 3 lines lost while the card was away\n']
    assert kinds(log) == [] and len(q2usb.parse(log)) == 4


def test_bus_power_then_a_computer_as_it_goes_off_is_a_backfeed():
    """The 20:46 replug: the ID low with nothing seen, the Q2 powered the bus, the laptop took over as it went off."""
    log = [line(1000, 'out'), line(5400, 'otg-on', pc20=1, pd18=0), line(6500, 'otg-off'),
           line(7000, 'in', vbus=1, udc='default'), line(12000, 'in+5s', vbus=1, udc='configured')]
    assert kinds(log) == ['BACKFEED']


def test_bus_power_with_vbus_seen_is_a_backfeed():
    assert kinds([line(1000, 'otg-on', pc20=1, pd18=0, vbus=1)]) == ['BACKFEED']


def test_bus_power_to_an_empty_port_and_a_plug_that_never_enumerates():
    log = [line(1000, 'otg-on', pc20=1, pd18=0), line(2100, 'otg-off'),
           line(9000, 'in', vbus=1), line(14000, 'in+5s', vbus=1, udc='not_attached'),
           line(30000, 'out'), line(35000, 'out+5s', pd18=0)]
    assert kinds(log) == ['EMPTY', 'NOENUM', 'IDLOW']


def test_a_dac_on_an_otg_adapter_is_not_flagged():
    log = [line(1000, 'otg-on', pc20=1, pd18=0, hostdev='262a'), line(90000, 'otg-off', hostdev='262a')]
    assert kinds(log) == []


def pins(ms, hostdev, wall=1763400000):
    # usb_record_pins's line, usb_otg.c's trace
    return f'{wall} {ms} pins PD18=0 PD17=0 PC20=1 PE22=1 otg=1 hostdev={hostdev} otgst=2\n'


def test_bus_power_whose_device_the_trace_saw_is_not_empty():
    """2026-11-23 V1SSi: the earbuds enumerated after otg-on and had left by otg-off, so both lines read hostdev=?;
    the trace's pins lines between them saw 1-1."""
    log = [line(1000, 'otg-on', pc20=1, pd18=0), pins(1250, 0), pins(3000, 1), pins(8000, 0),
           line(9500, 'otg-off why=gone')]
    rows = q2usb.parse(log)
    assert kinds(log) == [] and rows[-1].event == 'otg-off' and rows[-1].f['why'] == 'gone'
    assert kinds([line(1000, 'otg-on', pc20=1, pd18=0), pins(1250, 0), line(9500, 'otg-trial-off')]) == ['EMPTY']


def test_main_exits_1_when_anything_is_flagged(tmp_path, capsys):
    f = tmp_path / 'q2-usb.log'
    f.write_text(line(1000, 'otg-on', pc20=1, pd18=0, vbus=1))
    assert q2usb.main([str(f)]) == 1 and 'BACKFEED' in capsys.readouterr().out
    f.write_text(line(1000, 'in', vbus=1))
    assert q2usb.main([str(f)]) == 0


def test_a_wake_that_drops_the_link_is_flagged_and_one_that_keeps_it_is_not():
    up = dict(vbus=1, udc='configured')
    kept = [line(1000, 'standby', **up), line(90000, 'wake', **up), line(95000, 'wake+5s', **up)]
    dropped = [line(1000, 'standby', **up), line(90000, 'wake', vbus=1, udc='not_attached', pd18=0),
               line(95000, 'wake+5s', vbus=1, udc='default')]
    assert kinds(kept) == [] and kinds(dropped) == ['WAKEDROP', 'WAKEDROP']


def test_the_pull_up_found_off_at_boot_or_a_wake_is_flagged():
    log = [line(500, 'boot').replace('\n', ' pull=input_pull_up\n'),
           line(900000, 'wake+5s-pull-off').replace('\n', ' pull=input_pull_hiz\n')]
    assert kinds(log) == ['PULLOFF'] and q2usb.parse(log)[0].f['pull'] == 'input_pull_up'


def test_a_trial_that_ends_with_nothing_enumerated_is_an_empty_port():
    log = [line(1000, 'otg-on', pc20=1, pd18=0), line(9000, 'otg-trial-off', pd18=0)]
    assert kinds(log) == ['EMPTY']


def test_a_row_s_time_is_utc_or_since_start_when_the_clock_was_never_set():
    assert q2usb.parse([line(1500, 'in', wall=1763400000)])[0].when() == '2025-11-17 17:20:00Z'
    assert q2usb.parse([line(1500, 'in', wall=100)])[0].when() == '+1.5s'
