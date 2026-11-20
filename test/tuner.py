#!/usr/bin/env python3
"""Radio's receiver on a dropout (patch/tuner_core.c with patch/fmrx.c, docs/internals.md#radio-static), built for
this machine: test/tuner_test.c plays a station's stream through the writer's decisions and the FM receiver, drops
the stream for 3 s and brings it back, then makes a stream end again and again.

    python3 test/tuner.py

What it holds the receiver to: the prototype's cut-out (~/q2-emu/design/radio-tuner/static-model.md, "Cut-outs"):
a stream that stops delivering drops the carrier, so the dropout is the empty channel's hiss (the model's -25.9
dBFS), never silence, and the station fades back in when its sound returns; and a stream that stays dead is
started again for as long as the station is tuned, each wait longer, at most RETRY_MAX_MS, the count starting
over once it sounds. And the dial's click (the prototype's click(): a 4 ms decaying noise burst through a highpass,
louder and duller on a detent)."""
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
HISS_DBFS = -25.9   # static-model.md's empty-channel hiss
RETRY_MAX_MS = 15000


def main():
    with tempfile.TemporaryDirectory(prefix='q2-tuner-') as d:
        exe = pathlib.Path(d)/'tuner_test'
        subprocess.run(['clang', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter', ROOT/'test/tuner_test.c',
                        ROOT/'patch/tuner_core.c', ROOT/'patch/fmrx.c', '-o', exe, '-lm'], check=True)
        lines = subprocess.run([exe], capture_output=True, text=True, check=True).stdout.splitlines()
    got = {line.split()[0]: line.split()[1:] for line in lines}
    for k, v in got.items():
        print(k, ' '.join(v))
    num = lambda k: float(got[k][0])
    bad = []

    def check(ok, what):
        print('ok ' if ok else 'BAD', what)
        if not ok:
            bad.append(what)
    check(-16 < num('before_dbfs') < -14, 'the station plays its programme before the dropout (a tone of -15 dBFS RMS)')
    check(3000 < num('dry_ms') < 5300, 'the carrier drops when the ring has run dry, the buffered sound played out')
    check(abs(num('dropout_dbfs') - HISS_DBFS) <= 1.5, f'the dropout is the empty channel\'s hiss ({HISS_DBFS} dBFS)')
    check(num('dropout_quietest_window_dbfs') > -30, 'no 50 ms of the dropout is silent')
    check(6000 < num('sounds_again_ms') <= 6300, 'the station sounds again 250 ms after its stream comes back')
    check(-16 < num('after_dbfs') < -14, 'and plays its programme as before')
    waits = [int(w) for w in got['waits_ms']]
    check(all(0 < w <= RETRY_MAX_MS for w in waits), f'a dead stream is started again every time, within '
          f'{RETRY_MAX_MS} ms (waits {waits})')
    check(waits == sorted(waits) and waits[0] <= 1000, 'each wait as long as the last or longer, the first a second')
    check(num('tries_after_sound') == 0, 'a stream that sounds again starts the count over')
    check(num('wait_after_nudge_ms') == 1000, 'demo\'s nudge (Wi-Fi back) starts the count over too')
    for t in (6, 10, 14):
        check(3 < num(f'click{t}_ms') < 6, f'a click ({t}) is a 4 ms burst')
        check(num(f'click{t}_tail_db') < -80, f'and leaves nothing ringing after 10 ms ({t})')
        check(num(f'click{t}_stereo') == 1, f'in both channels alike ({t})')
    check(num('click6_peak_db') < num('click10_peak_db') < num('click14_peak_db'), 'a held detent ticks softest, '
          'landing on a station loudest')
    check(-40 < num('click10_peak_db') < -6, 'a step clicks well under full scale')
    check(num('click14_crossings_per_ms') < num('click10_crossings_per_ms'), 'landing on a station clicks duller '
          '(highpassed at 900 Hz, a step at 2500)')
    assert not bad, f'{len(bad)} failed: {bad}'
    print('all passed')


if __name__ == '__main__':
    main()
    sys.exit(0)
