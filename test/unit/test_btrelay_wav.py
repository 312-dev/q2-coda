"""tools/btrelay/btsignal.py and wavcheck.py: the Bluetooth relay's test signal and the check of what came back
(docs/bt-relay.md#checking-the-audio): each channel's tone and its level, distortion and noise, the bits a sample
really uses, the band the codec kept, and silence where the stream dropped out. The received files are made here
from the signal itself: quantized, band-limited, with channels swapped or a gap cut in."""
import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools' / 'btrelay'))
import btsignal  # noqa: E402
import wavcheck  # noqa: E402

RATE = 48000


def floats(rate=RATE):
    return btsignal.samples(rate)


def test_the_signal_is_a_wav_of_the_asked_rate_and_depth(tmp_path):
    for bits in (16, 24):
        data = btsignal.wav(44100, bits)
        fmt = struct.unpack_from('<HHIIHH', data, 20)
        assert fmt == (1, 2, 44100, 44100 * 2 * bits // 8, 2 * bits // 8, bits)
        assert len(data) == 44 + int(btsignal.SECONDS * 44100) * 2 * bits // 8
    assert btsignal.wav(RATE, 16) == btsignal.wav(RATE, 16)  # the noise is seeded: the same file each time
    with pytest.raises(ValueError):
        btsignal.wav(RATE, 8)


def test_a_clean_copy_passes():
    r = wavcheck.check(wavcheck.read(btsignal.wav(RATE, 16)))
    assert r['problems'] == []
    assert [round(c['tone_hz']) for c in r['channels']] == [1000, 1500]
    assert all(abs(c['tone_dbfs'] - btsignal.TONE_DBFS) < 0.2 for c in r['channels'])
    assert all(c['sinad_db'] > 80 for c in r['channels'])
    assert r['bits_used'] == 16 and r['bandwidth_hz'] > 20000 and r['dropouts'] == []


def test_24_bits_of_a_24_bit_file_and_16_of_one_padded():
    assert wavcheck.check(wavcheck.read(btsignal.wav(RATE, 24)))['bits_used'] == 24
    left, right = floats()
    padded = wavcheck.encode([[round(v * 32767) / 32768 for v in ch] for ch in (left, right)], RATE, 24)
    r = wavcheck.check(wavcheck.read(padded))
    assert r['bits_used'] == 16 and r['bits'] == 24


def test_a_float_capture_as_wasapi_writes_it():
    """Format tag 0xfffe (extensible) with the float subtype: Windows' mix format."""
    left, right = floats()
    data = wavcheck.encode([left, right], RATE, 32, floating=True)
    assert struct.unpack_from('<H', data, 20)[0] == 0xfffe
    r = wavcheck.check(wavcheck.read(data))
    assert r['problems'] == [] and r['float'] and r['channels'][0]['sinad_db'] > 80


def test_swapped_channels_and_a_wrong_pitch_are_problems():
    left, right = floats()
    r = wavcheck.check(wavcheck.read(wavcheck.encode([right, left], RATE, 16)))
    assert any('swapped' in p for p in r['problems'])
    slow = wavcheck.check(wavcheck.read(wavcheck.encode([left, right], 44100, 16)))  # played at the wrong rate
    assert any('left tone' in p for p in slow['problems'])


def test_distortion_lowers_sinad():
    left, right = floats()
    clipped = [[max(-0.3, min(0.3, v)) for v in ch] for ch in (left, right)]
    r = wavcheck.check(wavcheck.read(wavcheck.encode(clipped, RATE, 16)))
    assert r['channels'][0]['sinad_db'] < 30 and any('SINAD' in p for p in r['problems'])


def lowpass(ch, cutoff, rate=RATE):
    """A 16th-order Butterworth low-pass (an 8th-order cascade of biquads, twice), as a codec that drops the top
    of the band: 12 dB down about 7% above the cutoff."""
    w = 2 * math.pi * cutoff / rate
    for q in (0.5098, 0.6013, 0.9000, 2.5629) * 2:
        alpha = math.sin(w) / (2 * q)
        b1 = (1 - math.cos(w)) / (1 + alpha)
        a1, a2 = -2 * math.cos(w) / (1 + alpha), (1 - alpha) / (1 + alpha)
        out, x1, x2, y1, y2 = [], 0.0, 0.0, 0.0, 0.0
        for x in ch:
            y = b1 / 2 * (x + x2) + b1 * x1 - a1 * y1 - a2 * y2
            x2, x1, y2, y1 = x1, x, y1, y
            out.append(y)
        ch = out
    return ch


def test_the_band_a_codec_kept():
    left, right = floats()
    start = int((btsignal.NOISE_AT - 0.1) * RATE)   # only the noise needs filtering: the band is read from it
    cut = left[:start] + lowpass(left[start:], 14500)
    data = wavcheck.encode([cut, right], RATE, 16)  # the band is the left channel's
    r = wavcheck.check(wavcheck.read(data))
    assert 14500 < r['bandwidth_hz'] < 16500
    assert wavcheck.check(wavcheck.read(data), min_bandwidth=19000)['problems'] == [
        f'bandwidth {r["bandwidth_hz"]:.0f} Hz, under 19000']


def test_a_dropout_is_found_with_its_time():
    left, right = floats()
    a, b = int(2.0 * RATE), int(2.05 * RATE)
    gap = [ch[:a] + [0.0] * (b - a) + ch[b:] for ch in (left, right)]
    r = wavcheck.check(wavcheck.read(wavcheck.encode(gap, RATE, 16)))
    assert len(r['dropouts']) == 1
    at, length = r['dropouts'][0]
    assert at == pytest.approx(2.0, abs=0.01) and length == pytest.approx(0.05, abs=0.01)
    assert any('dropout' in p for p in r['problems'])


def test_a_capture_with_lead_in_and_no_signal():
    left, right = floats()
    lead = [[0.0] * RATE + ch for ch in (left, right)]
    r = wavcheck.check(wavcheck.read(wavcheck.encode(lead, RATE, 16)))
    assert r['problems'] == [] and r['signal_at'] == pytest.approx(1.0 + btsignal.TONE_AT, abs=0.06)
    silent = wavcheck.check(wavcheck.read(wavcheck.encode([[0.0] * RATE * 3] * 2, RATE, 16)))
    assert silent['problems'] == ['no test tone found: nothing was received, or not the test signal']
    assert silent['bits_used'] == 0
    quiet = wavcheck.check(wavcheck.read(wavcheck.encode([[0.0] * RATE] * 2, RATE, 32, floating=True)))
    assert quiet['bits_used'] == 0 and quiet['float']


def test_not_a_wav_and_the_command_line(tmp_path, capsys):
    with pytest.raises(ValueError, match='not a WAV'):
        wavcheck.read(b'btsnoop\0' + bytes(40))
    good, bad = tmp_path / 'good.wav', tmp_path / 'bad.wav'
    assert btsignal.main([str(good), '--rate', '44100']) == 0
    assert wavcheck.main([str(good)]) == 0
    assert 'PASS' in capsys.readouterr().out
    bad.write_bytes(wavcheck.encode([[0.0] * 44100] * 2, 44100, 16))
    assert wavcheck.main([str(bad)]) == 1
    assert 'FAIL' in capsys.readouterr().out


def test_other_containers_and_files_that_are_not_read():
    left, right = floats()
    wide = wavcheck.read(wavcheck.encode([left, right], RATE, 32))
    assert wide['bits'] == 32 and wavcheck.check(wide)['bits_used'] == 32
    grid24 = [[round(v * 8388608) / 8388608 for v in ch] for ch in (left, right)]
    assert wavcheck.check(wavcheck.read(wavcheck.encode(grid24, RATE, 32, floating=True)))['bits_used'] == 24
    plain = btsignal.wav(RATE, 16)
    listed = plain[:12] + b'LIST' + struct.pack('<I', 3) + b'abc\0' + plain[12:]   # an odd chunk, padded, first
    assert wavcheck.check(wavcheck.read(listed))['problems'] == []
    with pytest.raises(ValueError, match='no fmt or data'):
        wavcheck.read(plain[:36])
    eight = bytearray(plain)
    struct.pack_into('<H', eight, 34, 8)
    with pytest.raises(ValueError, match='at 8 bits is not read'):
        wavcheck.read(bytes(eight))
    mono = wavcheck.check(wavcheck.read(wavcheck.encode([left], RATE, 16)))
    assert mono['problems'] == ['one channel: the test signal is stereo']


def test_a_recording_that_ends_early():
    left, right = floats()
    tone_only = [ch[:int(4.6 * RATE)] for ch in (left, right)]          # stopped before the noise
    r = wavcheck.check(wavcheck.read(wavcheck.encode(tone_only, RATE, 16)))
    assert r['bandwidth_hz'] == 0.0 and r['problems'] == []
    no_noise = [ch[:int(4.6 * RATE)] + [0.0] * (4 * RATE) for ch in (left, right)]
    assert wavcheck.check(wavcheck.read(wavcheck.encode(no_noise, RATE, 16)))['bandwidth_hz'] == 0.0
    lost_tail = [ch[:int(3.0 * RATE)] + [0.0] * len(ch[int(3.0 * RATE):]) for ch in (left, right)]
    r = wavcheck.check(wavcheck.read(wavcheck.encode(lost_tail, RATE, 16)))
    at, length = r['dropouts'][-1]
    assert at == pytest.approx(3.0, abs=0.01) and length == pytest.approx(1.47, abs=0.02)


def test_the_command_lines_options(tmp_path, capsys):
    path = tmp_path / 's.wav'
    path.write_bytes(btsignal.wav(RATE, 16))
    assert wavcheck.main([str(path), '--min-sinad', '120']) == 1
    assert 'SINAD' in capsys.readouterr().out
    assert wavcheck.main([str(path), '--json', '--min-bandwidth', '20000']) == 0
    assert '"bits_used": 16' in capsys.readouterr().out
    for main in (wavcheck.main, btsignal.main):
        with pytest.raises(ValueError, match='usage:'):
            main([])
