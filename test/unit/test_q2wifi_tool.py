"""tools/q2wifi.py: the Wi-Fi fault recorder's log read back, on a log the recorder itself wrote (test_q2wifi_rec.py's
rig: the driver that never came up, then a deaf period, then associations), and on the odd lines a log can hold."""
import io
import sys

from test_q2wifi import ROOT
from test_q2wifi_rec import BSSID, HEADER, RESULTS, play, rig, status  # noqa: F401 (rig is a fixture)

sys.path.insert(0, str(ROOT / 'tools'))
import q2wifi  # noqa: E402


def recorded(rig):
    (rig.root / 'sys/class/net/wlan0/operstate').unlink()
    (rig.root / 'sys/class/net/wlan0').rmdir()
    rig.start()
    wpa = rig.up()
    wpa.answers['SCAN_RESULTS'] = RESULTS
    play(rig, 1000, 126000)
    wpa.answers['SCAN_RESULTS'] = HEADER
    play(rig, 126000, 800000)
    wpa.answers['STATUS'] = status('COMPLETED', 5240)
    wpa.event(f'CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id=0 id_str=]')
    rig.tick(800000)
    wpa.event(f'CTRL-EVENT-DISCONNECTED bssid={BSSID} reason=3 locally_generated=1')
    rig.L.set_clock(900000)
    rig.L.drain()
    wpa.event(f'CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id=0 id_str=]')
    rig.L.set_clock(905000)
    rig.L.drain()
    return rig.log()


def test_a_recorded_log(rig, tmp_path, capsys):
    log = tmp_path / 'q2-wifi.0'
    log.write_text(recorded(rig))
    q2wifi.main([str(log)])
    text = capsys.readouterr().out
    assert 'up 31 s  wlan0=no after=30 s module=8733bs' in text
    assert 'insmod: vmalloc: allocation failure: 0 bytes' in text and 'random: crng' not in text
    assert 'kind nojoin  lasted 679 s, ended by assoc' in text
    rows = [l.split() for l in text.splitlines() if l.startswith('     20')]
    assert [r[2] for r in rows] == ['start', 'empty', 'again', 'end']
    assert rows[0][3:8] == ['DISCONNECTED', '2', '1/-55', '1/-61', '0'] and rows[1][4] == '0'
    assert rows[0][9:11] == ['123456', '-'] and rows[1][9:11] == ['123456', '+0'] and rows[0][11:] == ['4100', '41.2']
    assert rows[3][3] == 'COMPLETED'
    assert 'left, reason 3 (deauthenticated: station leaving), by the Q2 after 100 s joined; driver: last disconnect' \
        in text
    assert 'joined #' in text and 'on 5240 MHz after 5 s down' in text


def test_odd_logs(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(
        'garbage\n'
        '2026-11-25T10:00:00Z up=5 K before any boot\n'
        '2026-11-25T10:00:00Z up=5 DEAF again n=2 for=300 s\n'  # a period whose start rolled off
        '2026-11-25T10:00:00Z up=5 SNAP status state=SCANNING\n'
        '2026-11-25T10:01:00Z up=65 DEAF start kind=empty after=60 s state=SCANNING nets=0 2.4=0 5=0 6=0 known=0\n'
        '2026-11-25T10:01:00Z up=65 F irq none here\n'
        '2026-11-25T10:01:00Z up=65 F fwstate fwstate=0x8\n'
        '2026-11-25T10:02:00Z up=125 DISASSOC bssid=#123456 reason=99\n'))
    q2wifi.main(['-'])
    text = capsys.readouterr().out
    assert 'Boots\n  none in the log' in text
    assert '1. 2026-11-25T10:01:00Z  up 65 s  kind empty  not ended in the log' in text
    assert 'reason 99 (other), by the AP; driver: -' in text
    q2wifi.report([], [], [], out=io.StringIO())
    out = io.StringIO()
    q2wifi.report([], [], [], out=out)
    assert 'Deaf periods\n  none' in out.getvalue() and 'Associations\n  none' in out.getvalue()
