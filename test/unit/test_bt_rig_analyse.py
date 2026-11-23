"""tools/bt_rig_analyse.py: song changes in a recording of a Q2's Bluetooth output (docs/bt-rig.md#analysis).

The calibration cases (a gapless cut, a 5 s equal-power fade, a 2 s silence) are made here with no codec, as
WAVs, since a test may not run ffmpeg; `bt_rig.py calibrate` runs the same cases through SBC or AAC."""
import array
import cmath
import json
import math
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import bt_rig_analyse as ba  # noqa: E402


def wav_bytes(tag, bits, ch, rate, payload, extensible=False):
    if extensible:
        fmt = struct.pack('<HHIIHH', 0xFFFE, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
        fmt += struct.pack('<HHI', 22, bits, 3) + struct.pack('<H', tag) + bytes(14)
    else:
        fmt = struct.pack('<HHIIHH', tag, ch, rate, rate * ch * bits // 8, ch * bits // 8, bits)
    body = b'WAVE' + b'fmt ' + struct.pack('<I', len(fmt)) + fmt + b'LIST' + struct.pack('<I', 3) + b'abc\0'
    body += b'data' + struct.pack('<I', len(payload)) + payload
    return b'RIFF' + struct.pack('<I', len(body)) + body


@pytest.fixture(autouse=True)
def no_ffmpeg(monkeypatch):
    """A test may not run ffmpeg: WAVs are read here, as on a machine without it."""
    monkeypatch.setattr(ba.shutil, 'which', lambda name: None)


@pytest.fixture(scope='module')
def cases(tmp_path_factory):
    d = tmp_path_factory.mktemp('cal')
    a, b, made = ba.make(d, codec='none', seconds=24, flac=False)
    return a, b, made


def test_calibration_tells_a_cut_a_fade_and_a_gap_apart(cases):
    a, b, made = cases
    got = {}
    for case, wav in made.items():
        c = ba.analyse(wav, [a, b])['changes'][0]
        got[case] = c
        (olo, ohi), (glo, ghi) = ba.EXPECT[case]
        assert olo <= c['overlap_s'] <= ohi and glo <= c['gap_s'] <= ghi, (case, c)
        assert c['a_gain_db'] == pytest.approx(-3.0, abs=0.3) and c['b_gain_db'] == pytest.approx(-3.0, abs=0.3)
        assert c['fit_db_median'] > 15
    # B found where it starts: 0.37 s of lead plus A's 24 s (less for the fade, more for the gap), on a clock 60 ppm fast
    starts = {k: v['b_enters_s'] for k, v in got.items()}
    assert starts['cut'] == pytest.approx(24.37, abs=0.1)
    assert starts['fade5'] == pytest.approx(19.37, abs=0.6)  # the fade-in is under PRESENT_BELOW for its first moments
    assert starts['gap2'] == pytest.approx(26.37, abs=0.1)
    # an equal-power fade is 3 dB down half way: 2.5 s before A's end; B reaches its level about then too
    assert got['fade5']['a_fade_from_before_file_end_s'] == pytest.approx(2.5, abs=0.3)
    assert got['fade5']['b_fade_in_s'] == pytest.approx(2.5, abs=0.4)
    assert got['cut']['b_fade_in_s'] == 0.0


def test_the_calibration_check_fails_when_the_analysis_is_wrong(cases, monkeypatch, tmp_path):
    """A presence threshold of 3 dB in place of 30 hides the fade: the check must go red, not pass anyway."""
    monkeypatch.setattr(ba.presence, '__defaults__', (3.0, ba.TRUST_DB))
    rows = dict((r[0], r[3]) for r in ba.calibrate(tmp_path, codec='none', seconds=24, flac=False))
    assert rows == {'cut': True, 'fade5': False, 'gap2': True}


def test_calibrate_reports_each_case(tmp_path):
    rows = ba.calibrate(tmp_path, codec='none', seconds=24, flac=False)
    assert [r[0] for r in rows] == ['cut', 'fade5', 'gap2'] and all(r[3] for r in rows)


def test_read_wav_formats(tmp_path):
    f = array.array('f', [0.5, -0.25])
    (tmp_path / 'f.wav').write_bytes(wav_bytes(3, 32, 2, 48000, f.tobytes()))
    assert ba.read_wav(tmp_path / 'f.wav') == (48000, 2, f)
    (tmp_path / 'x.wav').write_bytes(wav_bytes(3, 32, 2, 48000, f.tobytes(), extensible=True))
    assert ba.read_wav(tmp_path / 'x.wav')[2] == f
    (tmp_path / 'h.wav').write_bytes(wav_bytes(1, 16, 1, 8000, array.array('h', [16384, -32768]).tobytes()))
    assert list(ba.read_wav(tmp_path / 'h.wav')[2]) == [0.5, -1.0]
    (tmp_path / 'i.wav').write_bytes(wav_bytes(1, 32, 1, 8000, array.array('i', [1 << 30]).tobytes()))
    assert list(ba.read_wav(tmp_path / 'i.wav')[2]) == [0.5]
    (tmp_path / 'u.wav').write_bytes(wav_bytes(1, 8, 1, 8000, b'\x80'))
    with pytest.raises(ValueError, match='format 1 at 8 bits'):
        ba.read_wav(tmp_path / 'u.wav')
    (tmp_path / 'n.wav').write_bytes(b'RIFF\0\0\0\0WAVE')
    with pytest.raises(ValueError, match='no fmt or data'):
        ba.read_wav(tmp_path / 'n.wav')
    (tmp_path / 'r.wav').write_bytes(b'not a wav at all')
    with pytest.raises(ValueError, match='not a WAV'):
        ba.read_wav(tmp_path / 'r.wav')


def test_read_wav_on_a_big_endian_machine(tmp_path, monkeypatch):
    f = array.array('f', [0.5])
    f.byteswap()
    (tmp_path / 'f.wav').write_bytes(wav_bytes(3, 32, 1, 8000, f.tobytes()))
    monkeypatch.setattr(ba.sys, 'byteorder', 'big')
    assert list(ba.read_wav(tmp_path / 'f.wav')[2]) == [0.5]


def test_envelope_silences_clipping_and_dip():
    rate = 1000
    loud = [0.5, -0.5] * 25          # 50 ms at 0.5: -6 dBFS
    s = array.array('f', loud * 4 + [0.0] * 150 + loud * 2 + [1.0, 1.2, -1.0] + [0.0] * 47)
    env = ba.envelope(s, rate, 1)
    assert [round(v) for v in env[:4]] == [-6] * 4 and env[4:7] == [-200.0] * 3 and len(env) == 10
    assert ba.silences(env) == [(0.2, 0.15)]
    assert ba.silences(env, min_s=0.2) == []
    assert ba.silences([-70.0, -10, -70, -70]) == [(0.1, 0.1)]
    c = ba.clipping(s, rate, 1)
    assert c['clipped'] == 3 and c['over_full_scale'] == 1 and c['peak_dbfs'] == pytest.approx(1.58, abs=0.01)
    assert c['first_clipped_s'] == [0.45, 0.451, 0.452]
    many = ba.clipping(array.array('f', [1.0] * 7), rate, 1)
    assert len(many['first_clipped_s']) == 5
    assert ba.clipping(array.array('f', [0.0]), rate, 1)['peak_dbfs'] == -200.0
    d = ba.dip([-20.0] * 60 + [-50.0] * 4 + [-20.0] * 60, 3.0, around_s=2.5)
    assert d == {'at_s': 3.0, 'before_db': -20.0, 'after_db': -20.0, 'lowest_db': -50.0, 'dip_db': 30.0, 'dip_s': 0.2}
    assert ba.dip([], 1.0)['lowest_db'] == -200.0


def test_fft_matches_a_dft_and_inverts():
    x = [complex(math.sin(i * 0.7), math.cos(i * 0.3)) for i in range(16)]
    want = [sum(x[n] * cmath.exp(-2j * math.pi * k * n / 16) for n in range(16)) for k in range(16)]
    got = ba.fft(list(x))
    assert all(abs(a - b) < 1e-9 for a, b in zip(got, want))
    assert all(abs(a - b) < 1e-9 for a, b in zip(ba.fft(got, inverse=True), x))


def test_locate_corr_refine_and_segment():
    import random
    r = random.Random(3)
    ref = array.array('f', [r.uniform(-1, 1) * (1 + math.sin(i / 300)) * (i % 400 < 200) for i in range(16000)])
    rec = array.array('f', [0.0] * 2400) + ref
    off, sharp = ba.locate(ba.onsets(rec), ba.onsets(ref))
    assert abs(off - 2400) <= ba.ONSET_HOP and sharp > 3
    assert ba.refine(rec, ref, off + 37, 0.6) == 2400
    assert ba.corr(rec, -1, ref, 0, 10) == 0.0 and ba.corr(rec, 0, ref, 0, 10) == 0.0  # out of range; silent
    assert ba.segment([1.0, 2.0], -1, 4) == [0.0, 1.0, 2.0, 0.0]
    early, _ = ba.locate(ba.onsets(ref), ba.onsets(rec))  # the reference starts before the recording
    assert abs(early + 2400) <= ba.ONSET_HOP


def test_resample():
    x = array.array('f', range(10))
    assert ba.resample(x, 8000, 8000) == x
    assert ba.resample(array.array('f', [1.0]), 48000, 8000) == array.array('f')
    y = ba.resample(array.array('f', [1.0] * 48), 48000, 8000)
    assert len(y) == 8 and all(v == 1.0 for v in y)


def test_decode_mono_reads_wavs_here_and_asks_ffmpeg_for_the_rest(tmp_path, monkeypatch):
    st = array.array('f', [0.5, 0.25] * 8000)
    (tmp_path / 's.wav').write_bytes(wav_bytes(3, 32, 2, 8000, st.tobytes()))
    assert set(ba.decode_mono(tmp_path / 's.wav')) == {0.375}
    (tmp_path / 'm.wav').write_bytes(wav_bytes(3, 32, 1, 8000, array.array('f', [0.25] * 10).tobytes()))
    assert list(ba.decode_mono(tmp_path / 'm.wav')) == [0.25] * 10
    seen = []

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=array.array('f', [0.125]).tobytes())
    monkeypatch.setattr(ba.subprocess, 'run', fake_run)
    assert list(ba.decode_mono(tmp_path / 'song.flac')) == [0.125]
    assert seen[0][:2] == ['ffmpeg', '-nostdin'] and seen[0][-1] == '-' and '8000' in seen[0]
    monkeypatch.setattr(ba.shutil, 'which', lambda name: '/usr/bin/ffmpeg')  # with ffmpeg, a WAV goes through it too
    assert list(ba.decode_mono(tmp_path / 's.wav')) == [0.125] and len(seen) == 2


def test_change_from_rows_refuses_what_it_cannot_read():
    def row(t, rec, a_gain, b_gain):
        return (t, rec, 20.0, -20.0 if a_gain is not None else None, a_gain, -20.0,
                -20.0 if b_gain is not None else None, b_gain, -20.0)
    only_a = [row(i * 0.05, -20.0, 0.0, None) for i in range(400)]
    assert 'error' in ba.change_from_rows(only_a, 10.0)
    flicker = [row(i * 0.05, -20.0, 0.0, 0.0 if i % 2 else -60.0) for i in range(800)]
    assert 'error' in ba.change_from_rows(flicker, 10.0)
    # B steady only close after its start (a short recording): the near medians are used
    short = [row(i * 0.05, -20.0, 0.0 if i < 100 else None, None if i < 100 else -1.0) for i in range(200)]
    c = ba.change_from_rows(short, 5.0)
    assert c['b_enters_s'] == 5.0 and c['overlap_s'] == 0.0 and c['b_gain_db'] == -1.0
    # A silent in its file for a while, then far under B: it still plays (the answer held) but is not seen, so
    # A ends where it was last seen
    held = [row(i * 0.05, -20.0, 0.0, None) for i in range(100)]
    held += [(i * 0.05, -20.0, 20.0, -80.0, None, -200.0, -20.0, 0.0, -20.0) for i in range(100, 120)]
    held += [(i * 0.05, -20.0, 20.0, -50.0, -60.0, -110.0, -20.0, 0.0, -20.0) for i in range(120, 140)]
    held += [row(i * 0.05, -20.0, None, 0.0) for i in range(140, 500)]
    plays, seen = ba.presence(held, ba.A_OWN, ba.A_GAIN, 0.0)
    assert plays[100:140] == [True] * 40 and seen[100:140] == [False] * 40 and plays[140] is False
    c = ba.change_from_rows(held, 5.0)
    assert c['overlap_s'] == 0.0 and c['a_ends_s'] == 5.0 and c['b_enters_s'] == 5.0
    # B before A's end: the overlap is from B's entry to A's last window seen; A 3 dB and 20 dB down read off it
    both = [row(i * 0.05, -20.0, 0.0 if i < 140 else (-10.0 if i < 160 else None), None if i < 100 else 0.0)
            for i in range(500)]
    c = ba.change_from_rows(both, 5.0)
    assert c['overlap_s'] == 3.0 and c['a_ends_s'] == 8.0 and c['a_fade_from_s'] == 7.0 and c['a_fade_20db_s'] == 8.0
    # a gap: A ends, the recording silent, then B
    gapped = [row(i * 0.05, -20.0, 0.0, None) for i in range(100)] + [row(i * 0.05, -90.0, None, None) for i in range(100, 120)]
    gapped += [row(i * 0.05, -20.0, None, 0.0) for i in range(120, 500)]
    c = ba.change_from_rows(gapped, 6.0)
    assert c['gap_s'] == 1.0 and c['overlap_s'] == 0.0 and c['a_fade_20db_s'] == 5.0
    # B never reaches 3 dB of its steady gain after it enters: no full point
    ramp = [row(i * 0.05, -20.0, 0.0 if i < 100 else None, None if i < 100 else (-10.0 if i < 450 else 0.0))
            for i in range(800)]
    ramp_c = ba.change_from_rows(ramp, 5.0)
    assert ramp_c['b_full_s'] == 22.5
    never = [row(i * 0.05, -20.0, 0.0 if i < 100 else None, None if i < 100 else -10.0) for i in range(500)]
    never[-1] = row(24.95, -20.0, None, -40.0)
    assert ba.presence(never, ba.B_OWN, ba.B_GAIN, 0.0)[0][-1] is False


def test_change_from_rows_with_no_steady_gain_anywhere():
    rows = [(i * 0.05, -20.0, 20.0, None, None, -200.0, None, None, -200.0) for i in range(100)]
    assert ba.change_from_rows(rows, 2.0) == {'error': 'a song never plays alone in the fitted span'}


def test_fit_change_on_one_song_alone_and_past_the_recording(cases):
    a, b, made = cases
    rec = ba.decode_mono(made['cut'])
    A = ba.decode_mono(a)
    rows = ba.fit_change(rec, A, int(0.37 * 8000), A, 10 ** 9, 1.0, 2.0)  # B's file nowhere near: A alone
    assert all(r[ba.B_OWN] is None for r in rows) and all(r[ba.A_GAIN] == pytest.approx(-3.0, abs=0.5) for r in rows)
    end = len(rec) / 8000
    assert len(ba.fit_change(rec, A, 0, A, 0, end - 0.1, end + 5)) <= 2
    same = ba.fit_change(rec, A, int(0.37 * 8000), A, int(0.37 * 8000), 1.0, 1.2)  # A against itself: singular
    assert len(same) == 4


def test_analyse_without_songs_and_main(cases, capsys):
    _, _, made = cases
    out = ba.analyse(made['gap2'], at=[26.0])
    assert out['silences'][0] == (0.0, 0.35) and out['silences'][1][1] == pytest.approx(2.0, abs=0.1)
    assert out['changes'][0]['dip']['dip_db'] > 100 and 'songs' not in out
    assert ba.main([str(made['cut']), '--at', '24.4']) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed['changes'][0]['dip']['at_s'] == 24.4


def test_analyse_with_rows_and_a_song_never_heard(cases, tmp_path):
    a, b, made = cases
    out = ba.analyse(made['cut'], [a, b], want_rows=True)
    assert len(out['changes'][0]['rows']) > 100 and len(out['songs']) == 2
    silent = tmp_path / 'silent.wav'
    ba.write_wav(silent, array.array('f', [0.0] * 48000 * 50), 48000)
    out = ba.analyse(silent, [a, b])
    assert 'error' in out['changes'][0]


def test_codec_round_trip_and_flac_through_ffmpeg(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(Path(cmd[cmd.index('-i') + 1]).read_bytes())
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(ba.subprocess, 'run', fake_run)
    p = tmp_path / 'x.wav'
    ba.write_wav(p, array.array('f', [0.1] * 10), 48000)
    ba.codec_round_trip(p, 'none')
    assert calls == []
    ba.codec_round_trip(p, 'sbc')
    assert calls[0][calls[0].index('-c:a') + 1] == 'sbc' and calls[1][-1] == str(p) and not (tmp_path / 'x.sbc').exists()
    ba.codec_round_trip(p, 'aac')
    assert calls[2][calls[2].index('-f') + 1] == 'adts'
    a, b, made = ba.make(tmp_path / 'm', codec='none', cases=('cut',), seconds=1)
    assert a.name == 'A.flac' and b.name == 'B.flac' and not (tmp_path / 'm' / 'A.wav').exists()


def test_mix_refuses_an_unknown_case():
    with pytest.raises(ValueError):
        ba.mix(array.array('f', [0.0]), array.array('f', [0.0]), 'swap')
