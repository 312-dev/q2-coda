"""tools/q2tap.py: the output tap's reader (docs/usb-audio.md#the-tap), against streams built here as player/tap.c
writes them, and vamp audio over the fake adbd of test_vamp.py. The player's own side, and the two together bit for
bit, are test/player/run.py's test_tap_*."""
import io
import struct
import sys
import wave

import pytest

from test_vamp import ROOT, adbd, dev  # noqa: F401  the fake adbd and its Device

sys.path.insert(0, str(ROOT / 'tools'))
import q2tap  # noqa: E402
import vamp  # noqa: E402


def rec(tag, body):
    return tag + struct.pack('<I', len(body)) + body


def fmt(rate=44100, ch=2, width=2, kind=0):
    return rec(b'Q2TF', struct.pack('<IHBB', rate, ch, width, kind))


def frames(*values, width=2):
    """Stereo frames, both channels the value."""
    return b''.join(struct.pack('<hh' if width == 2 else '<ii', v, v) for v in values)


class Mem:
    def __init__(self, f, runs):
        self.f, self.data, self.closed = f, bytearray(), False
        runs.append(self)

    def write(self, b):
        self.data += b

    def close(self):
        self.closed = True


def tap(hold_s=q2tap.HOLD_S):
    runs = []
    return q2tap.Tap(lambda f: Mem(f, runs), hold_s), runs


def test_records_split_anywhere_give_the_same_frames():
    """An earlier reader's record before the first format is skipped; a record cut anywhere waits for its rest."""
    stream = rec(b'Q2TD', frames(9)) + fmt() + rec(b'Q2TD', frames(1, 2, 3)) + rec(b'Q2TD', frames(4))
    for step in (1, 3, 7, len(stream)):
        t, runs = tap()
        for i in range(0, len(stream), step):
            t.feed(stream[i:i + step])
        t.close()
        assert [bytes(r.data) for r in runs] == [frames(1, 2, 3, 4)] and runs[0].closed and t.frames == 4


def test_a_take_back_replaces_the_newest_frames_held():
    t, runs = tap()
    t.feed(fmt() + rec(b'Q2TD', frames(1, 2, 3, 4)) + rec(b'Q2TB', struct.pack('<I', 2)) + rec(b'Q2TD', frames(7, 8)))
    t.close()
    assert bytes(runs[0].data) == frames(1, 2, 7, 8) and t.back == 2 and t.unheld == 0 and t.frames == 4


def test_frames_past_the_hold_go_out_and_a_take_back_past_them_is_counted():
    t, runs = tap(hold_s=2 / 44100)  # two frames held
    t.feed(fmt() + rec(b'Q2TD', frames(1, 2, 3, 4)))
    assert bytes(runs[0].data) == frames(1, 2)
    t.feed(rec(b'Q2TB', struct.pack('<I', 3)))
    t.close()
    assert bytes(runs[0].data) == frames(1, 2) and t.back == 2 and t.unheld == 1
    assert q2tap.summary(t) == '2 frames (0.0 s), 2 taken back, 1 taken back past what was held'


def test_a_record_of_a_later_kind_is_passed_over():
    t, runs = tap()
    t.feed(fmt() + rec(b'Q2TX', b'abc') + rec(b'Q2TD', frames(1)))
    t.close()
    assert bytes(runs[0].data) == frames(1)


def test_dropped_frames_are_silence_and_counted():
    t, runs = tap()
    t.feed(fmt() + rec(b'Q2TD', frames(1)) + rec(b'Q2TL', struct.pack('<I', 2)) + rec(b'Q2TD', frames(5)))
    t.close()
    assert bytes(runs[0].data) == frames(1, 0, 0, 5) and t.lost == 2 and t.frames == 4
    assert '2 dropped' in q2tap.summary(t) and 'taken back' not in q2tap.summary(t)


def test_a_new_format_is_a_new_run_and_the_same_one_is_not():
    t, runs = tap()
    t.feed(fmt() + rec(b'Q2TD', frames(1)) + fmt() + rec(b'Q2TD', frames(2)) + fmt(96000, 2, 4) +
           rec(b'Q2TD', frames(3, width=4)))
    t.close()
    assert [str(r.f) for r in runs] == ['44100 Hz, 2 ch, 16-bit integer', '96000 Hz, 2 ch, 32-bit integer']
    assert bytes(runs[0].data) == frames(1, 2) and runs[0].closed and bytes(runs[1].data) == frames(3, width=4)
    assert t.seconds() == pytest.approx(3 / 96000)


def test_format_names():
    assert q2tap.Format(48000, 2, 4, 1).ffmpeg() == 'f32le' and q2tap.Format(48000, 2, 3, 0).ffmpeg() == 's24le'
    assert str(q2tap.Format(176400, 2, 4, 2)) == '176400 Hz, 2 ch, 32-bit DoP'
    assert str(q2tap.Format(1, 1, 1, 9)) == '1 Hz, 1 ch, 8-bit kind 9'
    t, _ = tap()
    assert t.seconds() == 0.0 and q2tap.summary(t) == '0 frames (0.0 s)'


def test_wavs_one_a_format_readable_by_python(tmp_path):
    said = []
    t = q2tap.Tap(q2tap.sinks(str(tmp_path / 'cap.wav'), say=said.append))
    t.feed(fmt() + rec(b'Q2TD', frames(1, 2, 3)) + fmt(48000, 2, 4, 1) + rec(b'Q2TD', struct.pack('<ff', .5, -.5)))
    t.close()
    with wave.open(str(tmp_path / 'cap.wav')) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (44100, 2, 2)
        assert w.readframes(10) == frames(1, 2, 3)
    raw = (tmp_path / 'cap-2.wav').read_bytes()
    assert struct.unpack_from('<H', raw, 20)[0] == 3 and raw[-8:] == struct.pack('<ff', .5, -.5)  # IEEE float
    assert said == [f'{tmp_path}/cap.wav: 44100 Hz, 2 ch, 16-bit integer', f'{tmp_path}/cap-2.wav: 48000 Hz, 2 ch, 32-bit float']
    assert next(q2tap.wav_paths('x')) == 'x.wav'


def test_play_alone_and_both(tmp_path):
    opened = []

    class P(Mem):
        def __init__(self, f):
            super().__init__(f, opened)
    said = []
    t = q2tap.Tap(q2tap.sinks(None, True, said.append, player=P))
    t.feed(fmt() + rec(b'Q2TD', frames(4)))
    t.close()
    assert said == ['playing: 44100 Hz, 2 ch, 16-bit integer'] and bytes(opened[0].data) == frames(4)
    t = q2tap.Tap(q2tap.sinks(str(tmp_path / 'b.wav'), True, None, player=P))
    t.feed(fmt() + rec(b'Q2TD', frames(5)))
    t.close()
    assert bytes(opened[1].data) == frames(5) and (tmp_path / 'b.wav').read_bytes().endswith(frames(5))


def test_the_file_reader(tmp_path, capsys):
    (tmp_path / 's.tap').write_bytes(fmt() + rec(b'Q2TD', frames(1, 2)))
    assert q2tap.main([str(tmp_path / 's.tap'), str(tmp_path / 'o.wav')]) == 0
    assert '2 frames' in capsys.readouterr().err
    assert (tmp_path / 'o.wav').read_bytes().endswith(frames(1, 2))
    with pytest.raises(SystemExit):
        q2tap.main([])


# ---- vamp audio

def test_vamp_audio_reads_the_fifo_into_a_wav(adbd, dev, tmp_path):  # noqa: F811
    stream = fmt() + rec(b'Q2TD', frames(*range(100)))
    adbd.handler = lambda cmd: stream if 'cat /tmp/q2tap.pcm' in cmd and 'mkfifo' in cmd else b''
    said = []
    line = vamp.audio(dev, ['--out', str(tmp_path / 'a.wav')], said.append)
    assert line == '100 frames (0.0 s)' and said[0].startswith('vamp audio: listening')
    assert (tmp_path / 'a.wav').read_bytes().endswith(frames(*range(100)))


def test_vamp_audio_stops_after_its_seconds(adbd, dev, tmp_path):  # noqa: F811
    stream = fmt(rate=10) + rec(b'Q2TD', frames(*range(10))) * 5
    adbd.handler = lambda cmd: stream
    line = vamp.audio(dev, ['--out', str(tmp_path / 'a.wav'), '--seconds', '2'], lambda t: None)
    assert line.startswith(('20 frames', '30 frames', '40 frames', '50 frames'))


def test_vamp_audio_through_googles_adb(tmp_path, monkeypatch):
    """--adb: Google's adb runs the same reader on the Q2 (exec-out, no terminal) and its stdout is the stream."""
    runs = []

    class Adb:
        def __init__(self, args, stdout):
            runs.append(args)
            self.stdout, self.killed = io.BytesIO(fmt() + rec(b'Q2TD', frames(3, 4))), False

        def kill(self):
            self.killed = True

        def wait(self):
            assert self.killed
    monkeypatch.setattr(vamp.subprocess, 'Popen', Adb)
    line = vamp.audio(None, ['--adb', 'adb.exe', '--out', str(tmp_path / 'g.wav')], lambda t: None)
    assert line == '2 frames (0.0 s)' and (tmp_path / 'g.wav').read_bytes().endswith(frames(3, 4))
    assert runs == [['adb.exe', 'exec-out', vamp.TAP_READ]]
    line = vamp.audio(None, ['--adb', 'adb.exe', '--out', str(tmp_path / 'h.wav'), '--seconds', '0'], lambda t: None)
    assert line == '0 frames (0.0 s)'


def test_vamp_audio_ctrl_c_keeps_what_came(tmp_path, monkeypatch):
    class D:
        dropped = False

        def exec_out(self, cmd, timeout, sink, done):
            sink.write(fmt() + rec(b'Q2TD', frames(1)))
            sink.flush()
            raise KeyboardInterrupt

        def drop(self):
            D.dropped = True
    line = vamp.audio(D(), ['--out', str(tmp_path / 'c.wav')], lambda t: None)
    assert D.dropped and line == '1 frames (0.0 s)'


def test_vamp_audio_flags():
    assert vamp.audio_args([]) == (None, True, None, None)
    assert vamp.audio_args(['--out', 'x.wav']) == ('x.wav', False, None, None)
    assert vamp.audio_args(['--out', 'x.wav', '--play', '--seconds', '3', '--adb', 'adb.exe']) == ('x.wav', True, 3.0, 'adb.exe')
    for bad in (['--nope'], ['--out']):
        with pytest.raises(ValueError, match='usage: vamp audio'):
            vamp.audio_args(bad)


def test_vamp_main_routes_audio(monkeypatch):
    calls = []
    monkeypatch.setattr(vamp, 'audio', lambda d, a: calls.append((d, a)))
    monkeypatch.setattr(vamp, 'resolve', lambda spec: {'name': 'q', 'ip': '127.0.0.1', 'port': 5555, 'id': ''})
    monkeypatch.setattr(vamp, 'load_key', lambda *a: None)
    assert vamp.main(['audio', '--adb', 'adb']) == 0 and calls[-1] == (None, ['--adb', 'adb'])
    assert vamp.main(['audio', '--seconds', '1']) == 0 and calls[-1][1] == ['--seconds', '1']
    assert isinstance(calls[-1][0], vamp.Device)

