#!/usr/bin/env python3
"""The test songs for test/player, made with ffmpeg and lame: `python3 test/player/music.py DIR`.

a1.flac + a2.flac and b1.mp3 + b2.mp3 are one 40 s signal cut in two at 20 s, so a gapless change is
exact when the two decode to the signal with nothing added or lost at the cut. The signal is a 441 Hz
tone under a slow sweep: periodic enough to compress, and any missing or repeated sample shows.
"""
import pathlib, subprocess, sys


def ff(*a):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-bitexact', *a], check=True)


def make(d):
    d = pathlib.Path(d); d.mkdir(parents=True, exist_ok=True)
    if (d/'.made').exists(): return d
    w = d/'whole.wav'
    ff('-f', 'lavfi', '-i', 'sine=f=441:r=44100:d=40', '-f', 'lavfi', '-i', 'aevalsrc=0.2*sin(2*PI*(200+20*t)*t):s=44100:d=40',
       '-filter_complex', 'amix=inputs=2:normalize=0,aformat=channel_layouts=stereo', '-c:a', 'pcm_s16le', w)
    tags = lambda title, album, n: ['-metadata', f'title={title}', '-metadata', 'artist=Tester', '-metadata', f'album={album}', '-metadata', f'track={n}']
    ff('-i', w, '-t', '20', '-c:a', 'flac', *tags('A One', 'Gapless FLAC', 1), d/'a1.flac')
    ff('-ss', '20', '-i', w, '-c:a', 'flac', *tags('A Two', 'Gapless FLAC', 2), d/'a2.flac')
    ff('-i', w, '-t', '20', '-c:a', 'pcm_s16le', d/'p1.wav')
    ff('-ss', '20', '-i', w, '-c:a', 'pcm_s16le', d/'p2.wav')
    for n in '12':
        subprocess.run(['lame', '--quiet', '-V2', '--tt', f'B {n}', '--ta', 'Tester', '--tl', 'Gapless MP3', '--tn', n, d/f'p{n}.wav', d/f'b{n}.mp3'], check=True)
    ff('-f', 'lavfi', '-i', 'anoisesrc=d=15:c=pink:r=96000:a=0.3:seed=7', '-ac', '2', '-c:a', 'flac', '-sample_fmt', 's32',
       '-bits_per_raw_sample', '24', *tags('Hi Res', 'HiRes', 1), d/'c96.flac')
    ff('-i', d/'p1.wav', '-c:a', 'aac', '-b:a', '128k', *tags('M4A', 'AAC', 1), d/'d.m4a')
    ff('-i', d/'p1.wav', '-c:a', 'libvorbis', *tags('Ogg', 'Vorbis', 1), d/'e.ogg')
    (d/'junk.flac').write_bytes(bytes((i * 7919 + (i >> 3)) & 255 for i in range(200000)))  # not audio
    (d/'.made').write_text('')
    return d


if __name__ == '__main__':
    print(make(sys.argv[1]))
