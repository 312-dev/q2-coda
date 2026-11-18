"""tools/q2bt.py: q2guard's Bluetooth link log read back as one segment per codec, the seconds each fell behind (the
radio's, the encoder's or the player's), what the signals did in the 10 s before, and a per-codec summary for an A/B
walk."""
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import q2bt  # noqa: E402


def t(sec):
    return f'2026-11-17T20:{sec // 60:02d}:{sec % 60:02d}Z'


def line(sec, kbps=600, wq=0, rssi=-5, afh=79, cpu=20, own=3, wtx=0, ba=80, er=90, ew=2):
    return (f'{t(sec)} up={1000 + sec} kbps={kbps} acl={kbps // 6} wq={wq} rssi={rssi} lq=255 tpl=4 afh={afh} err=0 '
            f'wtx={wtx} wrx=0 cpu={cpu} ba={ba} own={own} h=1 er={er} ew={ew}\n')


def walk(start, codec, kbps, stall_at=None):
    """A stream of 40 s; from stall_at the RSSI falls over 10 s and then the queue fills and the rate drops for 3 s."""
    log = [f'{t(start)} START h=12 mtu=1021 bufs=8 raw=ok\n', f'{t(start)} CODEC {codec} 48000 Hz delay=235.7 ms\n']
    for i in range(1, 41):
        sec = start + i
        if stall_at is not None and stall_at <= i < stall_at + 10:
            log.append(line(sec, kbps, rssi=-5 - 2 * (i - stall_at)))
        elif stall_at is not None and stall_at + 10 <= i < stall_at + 13:
            log.append(line(sec, kbps // 3, wq=2730, rssi=-25))
        else:
            log.append(line(sec, kbps))
    return log + [f'{t(start + 41)} STOP\n']


def test_an_a_b_walk_flags_the_link_stall_and_what_came_before_it():
    log = walk(0, 'aptX-HD', 600, stall_at=15) + walk(100, 'SBC', 320)
    streams, notes = q2bt.parse(log)
    out = io.StringIO()
    found = q2bt.report(streams, notes, out)
    text = out.getvalue()
    assert [(st.codec, kind, b - a + 1) for st, (kind, a, b) in found] == [('aptX-HD', 'link', 3)]
    assert 'LINK stall 20:00:25, 3 s' in text and 'rssi -5->-25' in text
    rows = {r[0]: r for r in q2bt.summary(streams)}
    assert rows['aptX-HD'][1:6] == (40, 3, 0, 0, 3) and rows['aptX-HD'][6] == 600 and rows['aptX-HD'][8] == -25
    assert rows['SBC'][1:6] == (40, 0, 0, 0, 0)
    assert 'Segment 2: SBC 48000 Hz' in text and 'ba 8.0' not in text and '8.0%' in text and '0.3%' in text


def test_a_slow_second_with_nothing_queued_is_the_q2_s_own():
    log = [f'{t(0)} START h=12 mtu=1021 bufs=8 raw=ok\n'] + [line(i, 600) for i in range(1, 20)]
    log += [line(20, 200, wq=0, cpu=99), line(21, 600)]
    streams, _ = q2bt.parse(log)
    q2bt.classify(streams[0])
    assert q2bt.episodes(streams[0]) == [('source', 19, 19)]


def test_a_pause_is_idle_not_a_stall():
    """Nothing sent and nothing waiting: the keep-alive let the transport go."""
    log = [f'{t(0)} START h=12\n'] + [line(i, 600) for i in range(1, 20)] + [line(i, 0) for i in range(20, 40)]
    streams, _ = q2bt.parse(log)
    q2bt.classify(streams[0])
    assert q2bt.episodes(streams[0]) == [] and streams[0].samples[-1].kind == 'idle'
    assert q2bt.summary(streams)[0][1] == 19  # idle seconds are not counted as streamed


def test_nothing_sent_with_the_queue_full_is_a_link_stall():
    log = [line(i, 600) for i in range(10)] + [line(10, 0, wq=4096)]
    streams, _ = q2bt.parse(log)
    q2bt.classify(streams[0])
    assert q2bt.episodes(streams[0]) == [('link', 10, 10)]


def test_a_log_that_begins_mid_stream_a_gap_and_garbage():
    log = ['garbage\n', line(0), line(1), '== 7 lines lost while the card was away\n', line(9), f'{t(10)} STOP\n']
    streams, notes = q2bt.parse(log)
    assert len(streams) == 1 and streams[0].head == 'log begins mid-stream' and len(streams[0].samples) == 3
    assert notes == [(streams[0].samples[1].when, 'GAP card away, 7 lines lost')]
    assert streams[0].samples[0].f['rssi'] == -5 and streams[0].codec == '?'


def test_dashes_are_none_and_every_second_can_be_printed():
    dash = f'{t(1)} up=5 kbps=- acl=- wq=- rssi=- lq=- tpl=- afh=- err=- wtx=- wrx=- cpu=- ba=- own=- h=- er=- ew=-\n'
    streams, notes = q2bt.parse([f'{t(0)} START h=-1 mtu=0 bufs=0 raw=none\n', dash, line(2)])
    assert streams[0].samples[0].f == {k: None for k in q2bt.SIGNALS}
    out = io.StringIO()
    q2bt.report(streams, notes, out, every=True)
    assert out.getvalue().count('20:00:0') == 3  # the stream's head and its two rows


def test_no_stream_says_so(capsys):
    assert q2bt.report(*q2bt.parse([f'{t(0)} SOMETHING\n'])) == []
    assert 'No stream in the log.' in capsys.readouterr().out


def test_main_reads_the_files_oldest_first_and_standard_input(tmp_path, monkeypatch, capsys):
    old, new = tmp_path / 'q2-bt.1', tmp_path / 'q2-bt.0'
    old.write_text(''.join(walk(0, 'aptX-HD', 600)[:20]))
    new.write_text(''.join(walk(0, 'aptX-HD', 600)[20:]))
    q2bt.main([str(old), str(new)])
    assert 'Segment 1: aptX-HD 48000 Hz' in capsys.readouterr().out
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''.join(walk(0, 'SBC', 320))))
    q2bt.main(['-'])
    assert 'Segment 1: SBC' in capsys.readouterr().out


def test_a_slow_second_with_the_encoder_busy_is_the_encoder_s():
    log = [f'{t(0)} START h=1\n'] + [line(i, 600) for i in range(1, 20)]
    log += [line(20, 300, er=950), line(21, 300, ew=400), line(22, 600)]
    streams, _ = q2bt.parse(log)
    q2bt.classify(streams[0])
    assert q2bt.episodes(streams[0]) == [('encoder', 19, 20)]


def test_an_older_line_without_the_encoder_s_fields_still_reads():
    old = f'{t(1)} up=5 kbps=600 acl=96 wq=0 rssi=-5 lq=0 tpl=4 afh=79 err=0 wtx=0 wrx=0 cpu=20 ba=90 own=0\n'
    streams, _ = q2bt.parse([old])
    assert streams[0].samples[0].f['ba'] == 90 and streams[0].samples[0].f['er'] is None


def test_an_encoder_note_cuts_the_stream_at_the_change():
    """bluealsa's encoder thread is named for its codec and noted within a second: SBC, the reconnect, aptX HD."""
    log = [f'{t(0)} START h=1\n', f'{t(0)} ENCODER ba-a2dp-sbc\n'] + [line(i, 260) for i in range(1, 30)]
    log += [line(30, 40), line(31, 0), f'{t(32)} ENCODER ba-a2dp-aptx-hd\n', f'{t(32)} LINK h=2\n',
            line(32, 300), line(33, 614)] + [line(i, 614) for i in range(34, 60)]
    log += [f'{t(60)} CODEC aptX-HD 48000 Hz delay=200.9 ms\n']
    streams, notes = q2bt.parse(log)
    assert [(s.codec, len(s.samples), s.hz) for s in streams] == [('SBC', 31, 0), ('aptX-HD', 28, 48000)]
    for st in streams:
        q2bt.classify(st)
    assert [s.kind for s in streams[0].samples[-2:]] == ['switch', 'switch'] and streams[1].samples[0].kind == 'switch'
    assert q2bt.episodes(streams[0]) == q2bt.episodes(streams[1]) == [] and notes[0][1] == 'LINK h=2'


def test_a_late_codec_note_takes_the_seconds_since_the_reconnect():
    """The walk of 2026-11-18: the switch at 04:47:13, its CODEC note at 04:47:19 (the minute's poll), no ENCODER."""
    log = [f'{t(0)} START h=1\n', f'{t(0)} CODEC SBC 48000 Hz delay=200.1 ms\n'] + [line(i, 260) for i in range(1, 30)]
    log += [line(30, 2), line(31, 0), line(32, 383)] + [line(i, 614) for i in range(33, 38)]
    log += [f'{t(38)} CODEC aptX-HD 48000 Hz delay=200.9 ms\n'] + [line(i, 614) for i in range(38, 50)]
    streams, _ = q2bt.parse(log)
    assert [(s.codec, len(s.samples)) for s in streams] == [('SBC', 31), ('aptX-HD', 18)]
    assert streams[1].start == streams[1].samples[0].when and streams[1].samples[0].f['kbps'] == 383
    for st in streams:
        q2bt.classify(st)
    assert all(q2bt.episodes(st) == [] for st in streams)  # the reconnect and the ramp are the switch


def test_the_edge_of_a_pause_is_not_a_stall():
    log = [f'{t(0)} START h=1\n'] + [line(i, 260) for i in range(1, 20)] + [line(20, 188)] + [line(i, 0) for i in range(21, 30)]
    streams, _ = q2bt.parse(log)
    q2bt.classify(streams[0])
    assert q2bt.episodes(streams[0]) == [] and streams[0].samples[19].kind == 'idle'
