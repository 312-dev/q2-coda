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


def test_cpu_in_percent_and_in_older_tenths_read_the_same():
    new = f'{t(1)} up=5 kbps=614 acl=96 wq=0 rssi=-5 lq=0 tpl=4 afh=79 err=0 wtx=0 wrx=0 cpu=20 ba=9.0 own=0.1 h=1 ' \
          f'er=88 ew=2 fcc=0 done=96 fly=5\n'
    old = f'{t(2)} up=6 kbps=614 acl=96 wq=0 rssi=-5 lq=0 tpl=4 afh=79 err=0 wtx=0 wrx=0 cpu=20 ba=90 own=1\n'
    streams, _ = q2bt.parse([new, old])
    a, b = streams[0].samples
    assert (a.f['ba'], a.f['own'], b.f['ba'], b.f['own']) == (90, 1, 90, 1)
    assert (a.f['fcc'], a.f['done'], a.f['fly'], b.f['done']) == (0, 96, 5, None)


# ---- the link's capacity: basic rate or EDR ----

def test_the_slots_a_packet_takes_on_basic_rate_and_on_edr():
    """An ACL packet is cut into baseband packets, each followed by the headset's slot: 790 bytes of aptX HD are DH5,
    DH5 and DH3 on basic rate (16 slots) and 2-DH5 and 2-DH3 on EDR 2 Mbps (10)."""
    assert q2bt.slots(790, q2bt.BASIC) == 16 and q2bt.slots(790, q2bt.EDR2) == 10
    assert q2bt.slots(498, q2bt.BASIC) == 10 and q2bt.slots(498, q2bt.EDR2) == 6   # SBC's 260 kbps
    assert q2bt.slots(20, q2bt.BASIC) == 2 and q2bt.slots(339, q2bt.BASIC) == 6 and q2bt.slots(340, q2bt.BASIC) == 8
    assert q2bt.slots(0, q2bt.BASIC) == 0


def sat(sec, done, fly=5, kbps=614, acl=96):
    return (f'{t(sec)} up={1000 + sec} kbps={kbps} acl={acl} wq=7936 rssi=-20 lq=0 tpl=4 afh=79 err=0 wtx=0 wrx=0 '
            f'cpu=20 ba=9.0 own=0.1 h=1 er=90 ew=2 fcc=0 done={done} fly={fly}\n')


def capacity_of(done_values, fly=5):
    log = [f'{t(0)} START h=1 mtu=1021 bufs=5 raw=ok\n', f'{t(0)} ENCODER ba-a2dp-aptx-hd\n']
    log += [sat(i, 96, fly=1) for i in range(1, 20)] + [sat(20 + i, d, fly=fly) for i, d in enumerate(done_values)]
    streams, _ = q2bt.parse(log)
    return q2bt.capacity(streams[0])


def test_a_saturated_controller_at_basic_rate_s_ceiling_is_basic_rate():
    c = capacity_of([97, 99, 100, 98, 96, 101])
    assert (c.seconds, c.median, c.ceiling_basic, c.ceiling_edr) == (6, 98.5, 100, 160) and c.verdict == 'basic rate'


def test_one_well_over_it_is_edr():
    assert capacity_of([140, 150, 155, 148, 152]).verdict == 'EDR 2 Mbps'


def test_between_the_ceilings_or_too_few_seconds_says_so():
    assert capacity_of([120, 125, 122, 124, 121]).verdict == 'undecided'
    assert capacity_of([98, 99, 97]).verdict == 'too few saturated seconds'
    assert capacity_of([98] * 6, fly=4).seconds == 0  # buffers not all full: the radio was not the limit
    log = [f'{t(1)} up=5 kbps=600 acl=96 wq=0 rssi=-5 lq=0 tpl=4 afh=79 err=0 wtx=0 wrx=0 cpu=20 ba=90 own=1\n']
    assert q2bt.capacity(q2bt.parse(log)[0][0]) is None  # an older log: no done, no fly


def test_the_report_names_the_verdict_per_segment():
    log = [f'{t(0)} START h=1 mtu=1021 bufs=5 raw=ok\n', f'{t(0)} ENCODER ba-a2dp-aptx-hd\n']
    log += [sat(i, 96, fly=1) for i in range(1, 20)] + [sat(20 + i, 99) for i in range(6)]
    out = io.StringIO()
    streams, notes = q2bt.parse(log)
    q2bt.report(streams, notes, out)
    text = out.getvalue()
    assert 'saturated: 6 s with all 5 ACL buffers full' in text and 'basic rate' in text and '100/s' in text


def test_a_source_stall_shows_the_player_s_side():
    """The player's fields (pl, pr, pd, pw, dk, gw) are read, pl in tenths as ba, and a source stall's 10 s before
    shows them; a line without them reads them as None."""
    log = [f'{t(0)} START h=1 mtu=1021 bufs=5 raw=ok\n', f'{t(0)} CODEC aptX-HD 44100 Hz delay=202.1 ms\n',
           f'{t(0)} PLAY /mnt/mmc/Music/one.flac\n']
    for i in range(1, 31):
        short = 20 <= i < 26
        log.append(line(i, 340 if short else 567, er=50 if short else 80).rstrip('\n') +
                   f' fcc=0 done=96 fly=2 bp=- pl={1.5 if short else 2.5} pr={100 if short else 176} pd=0 '
                   f'pw={100 if short else 172} dk={950 if short else 20} gw=0\n')
    streams, notes = q2bt.parse(log)
    s = streams[0].samples[19]
    assert (s.f['pl'], s.f['pr'], s.f['pd'], s.f['pw'], s.f['dk'], s.f['gw']) == (15, 100, 0, 100, 950, 0)
    assert any('PLAY /mnt/mmc/Music/one.flac' in text for _, text in notes)
    out = io.StringIO()
    q2bt.report(streams, notes, out)
    stall = [ln for ln in out.getvalue().splitlines() if 'SOURCE stall' in ln]
    assert stall and 'pw 172->100' in stall[0] and 'dk 20->950' in stall[0]
    old = q2bt.parse([line(1)])[0][0].samples[0]
    assert old.f['pl'] is None and old.f['gw'] is None
