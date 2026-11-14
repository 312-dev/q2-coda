#!/usr/bin/env python3
"""A stand-in for demo's side of the player socket (docs/player.md#the-socket).

demo listens on /tmp/sock_play, starts the player, and the player connects. This does the same for
either player, sends a scenario's commands at their times, polls as demo's status thread does
(player-status and media-info every 200 ms while it believes a song is playing) and writes
everything sent and received, with times, as a transcript.

    # the new player, built for this machine
    python3 test/player/fakedemo.py --player player/build/host/q2player --music DIR --out OUT SCENARIO
    # stock hciplayer under qemu in a sandbox of an unpacked rootfs (see docs/player.md#capturing)
    python3 test/player/fakedemo.py --emu ~/q2-emu-player --hciplayer STOCKFS/usr/bin/hciplayer --music DIR --out OUT SCENARIO

OUT gets transcript.txt, player.log and, when the sandbox's shim captures it, pcm/*.raw.
Transcript lines are `seconds direction text`: `>` is demo to player, `<` player to demo. An answer to
the 200 ms poll is written only when it changes; media-info's position is written as POS and
sampled in the last line.
"""
import argparse, os, pathlib, re, select, shutil, socket, subprocess, sys, threading, time

POLL = ('{player-status\\request}', '{media-info\\get-music-info}')


def song(cmd, path, start=0, cache=512, cache_min=20, flag=0, hw=17):
    """set-songurl or set-nexturl as demo's mclStartPlayer builds it (demo 0x5bf504): the start in seconds, the
    cache in KB and its fill percent (512 and 20 for a file on the card), demo's Gapless setting, the hardware
    type (17 on a Q2) and a constant 90."""
    return '{%s\\%s|ss=%d|%d|%d|%d|%d|90}' % (cmd, path.replace('}', '%0x7d'), start, cache, cache_min, flag, hw)


def gapless(path, start=0, flag=0):
    """set-gapless as demo's preload sender builds it (demo 0x5baff8): cache -1, 20 percent."""
    return '{set-gapless\\%s|ss=%d|-1|20|%d|90}' % (path.replace('}', '%0x7d'), start, flag)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from music import FORMATS, PAIRS  # noqa: E402

# formats-all's files, in the order they play
ALL_FORMATS = ['a1.flac', 'f24.flac', 'b1.mp3', 'd.m4a', 'e.ogg', *(n for n, _ in FORMATS)]


# A scenario is a list of (seconds from start, command or callable(session)). Paths are the player's.
def scenarios(m):
    a1, a2, b1, b2, c96, m4a, ogg = (f'{m}/{n}' for n in ('a1.flac', 'a2.flac', 'b1.mp3', 'b2.mp3', 'c96.flac', 'd.m4a', 'e.ogg'))
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    return {
        # start, pause, resume, seek forward and back, stop
        'transport': boot + [(1.0, song('set-songurl', a1)), (4.0, '{mcl-pause\\null}'), (6.0, '{mcl-play\\null}'),
                             (8.0, '{mcl-seek\\15}'), (10.0, '{mcl-seek\\2}'), (12.0, '{mcl-stop\\null}'), (14.0, None)],
        # a preloaded natural change, as Gapless on sends it about 6 s before the end
        'gapless-flac': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(a2, flag=1)), (14.0, '{mcl-seek\\17}'), (20.0, None)],
        'gapless-mp3': boot + [(1.0, song('set-songurl', b1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(b2, flag=1)), (14.0, '{mcl-seek\\17}'), (20.0, None)],
        # the whole of two tracks, no seek: the PCM is compared with a reference decode
        'gapless-flac-full': boot + [(1.0, song('set-songurl', a1, flag=1)), (16.0, gapless(a2, flag=1)), (44.0, None)],
        'gapless-mp3-full': boot + [(1.0, song('set-songurl', b1, flag=1)), (16.0, gapless(b2, flag=1)), (56.0, None)],
        # Next while playing (set-nexturl), then a natural end with nothing preloaded
        'skip': boot + [(1.0, song('set-songurl', a1)), (4.0, song('set-nexturl', b1)), (7.0, song('set-nexturl', a2)), (9.0, '{mcl-seek\\16}'), (16.0, None)],
        # volume, EQ, the meters and the odd queries while playing
        'controls': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-setvol\\40}'), (3.5, '{mcl-softvolflag\\1}'), (4.0, '{mcl-setvol\\25700}'),
                            (5.0, '{mcl-seteq\\6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|1}'),
                            (6.0, '{mcl-seteq\\6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|0}'),
                            (6.5, '{mcl-setexplayinfo\\1}'), (7.0, '{mcl-getuvmeter\\null}'), (7.2, '{mcl-setexplayinfo\\2}'), (7.6, '{mcl-getspectrum\\null}'),
                            (8.0, '{mcl-pcmstate\\null}'), (8.2, '{get_cache_buffer\\null}'), (8.4, '{mcl-playspeed\\1}'), (8.6, '{mcl-replaygain\\1}'),
                            (8.8, '{mcl-closegapless\\null}'), (9.0, '{mcl-mqadecodemode\\0}'), (9.5, '{mcl-orderstop\\null}'), (10.0, '{mcl-stop\\null}'), (12.0, None)],
        # a file that is not there, and one that is not audio
        'errors': boot + [(1.0, song('set-songurl', f'{m}/missing.flac')), (4.0, song('set-songurl', f'{m}/junk.flac')), (7.0, song('set-songurl', a1)), (10.0, '{mcl-stop\\null}'), (12.0, None)],
        # one song to its end with nothing preloaded, then what demo sends at a queue's end
        'end': boot + [(1.0, song('set-songurl', a1)), (2.0, '{mcl-seek\\16}'), (7.5, '{mcl-orderstop\\null}'), (9.0, None)],
        # pause, seek while paused, play
        'pause-seek': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-pause\\null}'), (4.0, '{mcl-seek\\10}'), (6.0, '{mcl-play\\null}'), (8.0, '{mcl-stop\\null}'), (10.0, None)],
        # set-songurl while a song plays, which demo sends only when it believes nothing is playing
        'replace': boot + [(1.0, song('set-songurl', a1)), (4.0, song('set-songurl', b1)), (7.0, '{mcl-stop\\null}'), (9.0, None)],
        # other formats: what media-info names them and how the PCM is opened
        'formats': boot + [(1.0, song('set-songurl', c96)), (4.0, song('set-nexturl', m4a)), (7.0, song('set-nexturl', ogg)), (10.0, '{mcl-stop\\null}'), (12.0, None)],
        # a whole 24-bit 96 kHz song
        'hires': boot + [(1.0, song('set-songurl', c96)), (19.0, None)],
        # every format demo lists, one after another by set-nexturl: CODEC, BITS, LENGTH, BITRATE and the PCM opened
        'formats-all': boot + [(1.0 + 2.5 * i, song('set-songurl' if i == 0 else 'set-nexturl', f'{m}/{f}')) for i, f in enumerate(ALL_FORMATS)]
                       + [(1.0 + 2.5 * len(ALL_FORMATS), '{mcl-stop\\null}'), (3.0 + 2.5 * len(ALL_FORMATS), None)],
        # a start offset (a resumed song): from 7 s to the end
        'start': boot + [(1.0, song('set-songurl', a1, start=7)), (16.0, None)],
        # a CUE sheet's three tracks of one file: demo starts each by ss= and cuts the one before with set-nexturl when
        # the position reaches the next track's start (demo 0x5c126c)
        'cue': boot + [(1.0, song('set-songurl', a1)), (6.0, song('set-nexturl', a1, start=5)), (11.0, song('set-nexturl', a1, start=10)), (24.0, None)],
        # a seek, then to the end: the PCM after it is the reference from the second asked
        'seek': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-seek\\9}'), (16.0, None)],
        # a preload that is not audio: the song plays to its end, then the error
        'badnext': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(f'{m}/junk.flac', flag=1)), (14.0, None)],
        # a preload cancelled by a queue edit: the song ends with nothing after it, and demo starts the next itself
        'cancel': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(a2, flag=1)), (5.0, '{mcl-closegapless\\null}'),
                          (13.0, song('set-songurl', b1)), (16.0, '{mcl-stop\\null}'), (18.0, None)],
        # a preload that opens and has no audio: two ends, which must reach demo at least 300 ms apart
        'emptynext': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\14}'), (4.0, gapless(f'{m}/empty.flac', flag=1)), (12.0, None)],
        # an MP3 with 3 MB of junk after its frames, whole: the junk does not play
        'tail-full': boot + [(1.0, song('set-songurl', f'{m}/tail.mp3')), (25.0, None)],
        # a preload whose open blocks (a FIFO no one writes yet), cancelled while it blocks; the file then arrives and
        # must not play. Host only: feed() writes the FIFO from this side.
        'cancel-open': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(f'{m}/slow.flac', flag=1)),
                               (5.0, '{mcl-closegapless\\null}'), (6.0, feed('a2.flac')), (13.0, song('set-songurl', b1)), (16.0, '{mcl-stop\\null}'), (18.0, None)],
        # a preload the song outruns: its file arrives two seconds after the song's end, and plays then
        'outrun': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(f'{m}/slow.flac', flag=1)),
                          (12.0, feed('a2.flac')), (36.0, None)],
        # a path with a } in it, which demo writes as %0x7d
        'brace': boot + [(1.0, song('set-songurl', f'{m}/a}}1.flac')), (3.0, '{mcl-stop\\null}'), (5.0, None)],
        # every lossless and every lossy format but MP3 and FLAC at 16 bits, whole, each preloaded half a second into
        # the one before: the PCM is compared with the reference decodes
        'chain-lossless': chain(m, [n for n, _ in FORMATS if n.rsplit('.', 1)[1] in ('wav', 'aiff', 'm4a', 'wv', 'flac')]),
        'chain-lossy': chain(m, [n for n, _ in FORMATS if n.rsplit('.', 1)[1] in ('mp2', 'aac', 'opus', 'wma', 'ac3', 'dts')]),
        **{f'pair-{n}': boot + [(1.0, song('set-songurl', f'{m}/{n}1.{e}', flag=1)), (16.0, gapless(f'{m}/{n}2.{e}', flag=1)), (44.0, None)]
           for n, e in PAIRS.items()},
        # queries with nothing loaded
        'idle': boot + [(1.0, '{player-status\\request}'), (1.2, '{media-info\\get-music-info}'), (1.4, '{mcl-pcmstate\\null}'),
                        (1.6, '{get_cache_buffer\\null}'), (1.8, '{mcl-getuvmeter\\null}'), (2.0, '{mcl-getspectrum\\null}'), (2.2, '{mcl-pause\\null}'),
                        (2.4, '{mcl-play\\null}'), (2.6, '{mcl-seek\\5}'), (2.8, '{mcl-stop\\null}'), (4.0, None)],
    }


def chain(m, names, length=6):
    """names, each about `length` seconds, one after another: the second's set-gapless half a second into the first,
    each later one's as the song before it starts (at the exit before), since a change of width drains the PCM first
    and the songs drift from any fixed times."""
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    def queue(s): s.on_exit = [gapless(f'{m}/{n}', flag=1) for n in names[2:]]
    steps = [(1.0, song('set-songurl', f'{m}/{names[0]}', flag=1)), (1.5, gapless(f'{m}/{names[1]}', flag=1)), (1.6, queue)]
    return boot + steps + [(3.0 + (length + 1) * len(names), None)]


def feed(name):
    """A step: write the music directory's `name` into its FIFO slow.flac, on a thread, since the write waits for the
    player to open the FIFO."""
    def go(s):
        def write():
            try:
                with open(pathlib.Path(s.music)/'slow.flac', 'wb') as f: f.write((pathlib.Path(s.music)/name).read_bytes())
            except BrokenPipeError: pass  # the player gave up reading: a cancelled preload
        threading.Thread(target=write, daemon=True).start()
    return go


def cpu_seconds(pid):
    """User and system time so far of pid and everything under it (the sandbox's qemu is a grandchild)."""
    tick, total, kids = os.sysconf('SC_CLK_TCK'), 0.0, {}
    for d in pathlib.Path('/proc').iterdir():
        if not d.name.isdigit(): continue
        try: f = (d / 'stat').read_text().rsplit(')', 1)[1].split()
        except OSError: continue
        kids.setdefault(int(f[1]), []).append((int(d.name), (int(f[11]) + int(f[12])) / tick))
    todo = [pid]
    while todo:
        for child, t in kids.get(todo.pop(), []):
            total += t; todo.append(child)
    return total


class Session:
    def __init__(self, out):
        self.out = pathlib.Path(out); self.out.mkdir(parents=True, exist_ok=True)
        self.tmp = self.out / 'tmp'; shutil.rmtree(self.tmp, ignore_errors=True); self.tmp.mkdir()
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(self.tmp / 'sock_play')); self.srv.listen(1)
        self.lines, self.seen, self.t0, self.conn, self.playing, self.preload, self.speed = [], {}, None, None, False, False, 1
        self.music = None  # the music directory on this machine, for steps that act on its files
        self.on_exit = []  # set-gapless commands sent one at each {player-status\\exit}, as demo sends one per song

    def note(self, direction, text):
        self.lines.append(f'{self.now():8.3f} {direction} {text}')

    def now(self):
        return (time.monotonic() - self.t0) * self.speed  # in the player's seconds

    def send(self, text, quiet=False):
        if not quiet: self.note('>', text)
        try: self.conn.sendall(text.encode())
        except OSError: pass

    def pump(self, until):
        """Read replies until the given monotonic time, polling as demo does while a song plays."""
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= until: return
            if self.playing and now >= next_poll:
                for p in POLL: self.send(p, quiet=True)
                next_poll = now + 0.2 / self.speed
            r, _, _ = select.select([self.conn], [], [], min(0.02 / self.speed, until - now))
            if not r: continue
            data = self.conn.recv(4096)
            if not data:
                self.note('<', '(closed)'); self.playing = False; time.sleep(0.05); continue
            for m in re.findall(r'\{[^}]*\}', data.decode(errors='replace')):
                name = m[1:m.index('\\')]
                shown = m
                if name == 'media-info' and '|' in m:
                    # bitrate|rate|length|position|codec|bits: the position moves every poll, so it is kept apart
                    f = m[12:-1].split('|')
                    self.positions.append((self.now(), int(f[3])))
                    shown = '{media-info\\' + '|'.join(f[:3] + ['POS'] + f[4:]) + '}'
                # an answer to the 200 ms poll is written only when it differs from the last one of its kind
                if name in ('media-info', 'player-status') and self.seen.get(name) == shown: pass
                else: self.note('<', shown)
                self.seen[name] = shown
                # demo stops polling at an exit unless it had sent a preload: then mclNextSong(1) sends nothing and
                # the player goes on with the preloaded file alone
                if m == '{player-status\\exit}' and self.on_exit: self.send(self.on_exit.pop(0)); continue
                if m == '{player-status\\exit}' and self.preload: self.preload = False
                elif m in ('{player-status\\exit}', '{player-stopstatus\\exit}', '{player-status\\error}'): self.playing = False

    def run(self, steps, launch, speed=1):
        """speed: the player's clock runs this many times faster (its file sink's Q2PLAYER_SPEED), so the steps' times
        and the poll's period shrink by it."""
        self.positions, self.speed = [], speed
        proc = launch(self)
        self.srv.settimeout(30)
        self.conn, _ = self.srv.accept()
        self.t0 = time.monotonic()
        for at, what in steps:
            self.pump(self.t0 + at / speed)
            if what is None: break
            if callable(what): what(self); continue
            if what.startswith(('{set-songurl', '{set-nexturl')): self.playing, self.preload = True, False
            if what.startswith('{set-gapless'): self.preload = True
            if what.startswith('{mcl-closegapless'): self.preload = False
            self.send(what)
        cpu = cpu_seconds(proc.pid)
        self.conn.close(); proc.terminate()
        try: proc.wait(5)
        except subprocess.TimeoutExpired: proc.kill()
        pos = ' '.join(f'{t:.1f}:{p}' for t, p in self.positions[::5])  # one a second
        (self.out / 'transcript.txt').write_text('\n'.join(self.lines) + f'\n# positions (every fifth poll, time:seconds) {pos}\n# cpu {cpu:.2f} s\n')


def launch_host(player, music, speed=1):
    def go(s):
        (s.out / 'pcm').mkdir(exist_ok=True)
        for f in (s.out / 'pcm').glob('*'): f.unlink()
        env = dict(os.environ, Q2PLAYER_SOCK=str(s.tmp / 'sock_play'), Q2PLAYER_PCM=str(s.out / 'pcm'), Q2PLAYER_SPEED=str(speed))
        return subprocess.Popen([player], stdout=open(s.out / 'player.log', 'w'), stderr=subprocess.STDOUT, env=env)
    return go


def launch_emu(emu, binary, music, name='hciplayer', args=('-ao', 'alsa'), stock=None, switch=False, killall=None):
    """binary inside a bwrap sandbox of emu/rootfs under qemu, as run.sh starts demo; /tmp is the session's.
    For q2player's fallback (player/main.c): stock is a player to put at /usr/lib/hciplayer-stock/hciplayer, switch
    makes /mnt/data/q2player, and killall is when, in seconds, a shell in the sandbox runs demo's `killall -9
    hciplayer`, after writing each process's name and command line to ps.txt."""
    emu = pathlib.Path(emu)
    def go(s):
        (s.out / 'pcm').mkdir(exist_ok=True)
        for f in (s.out / 'pcm').glob('*'): f.unlink()
        data = s.out / 'data'
        shutil.rmtree(data, ignore_errors=True); data.mkdir()
        if switch: (data / 'q2player').touch()
        extra = ['--bind', data, '/mnt/data'] + (['--ro-bind', stock, '/usr/lib/hciplayer-stock/hciplayer'] if stock else [])
        run = [f'/usr/bin/{name}', *args]
        if killall is not None:
            run = ['/bin/sh', '-c', f'{" ".join(run)} & sleep {killall}; for p in /proc/[0-9]*; do echo "$(cat $p/comm): '
                   f'$(tr "\\0" " " < $p/cmdline)"; done > /emu-log/ps.txt; killall -9 {name}; echo "killall $?" >> /emu-log/ps.txt; wait']
        cmd = ['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-user', '--uid', '0', '--gid', '0',
               '--bind', emu / 'rootfs', '/', '--proc', '/proc', '--dev', '/dev', '--bind', s.tmp, '/tmp',
               '--ro-bind', music, '/mnt/mmc', '--bind', s.out, '/emu-log', '--ro-bind', emu / 'state', '/emu',
               '--ro-bind', emu / 'state/mtd_debug', '/usr/sbin/mtd_debug',
               '--ro-bind', emu / 'qemu-mipsel-static', '/usr/libexec/qemu-binfmt/mipsel-binfmt-P',
               '--ro-bind', emu / 'shim/libq2emu.so', '/lib/libq2emu.so', '--ro-bind', binary, f'/usr/bin/{name}', *extra,
               '--setenv', 'PATH', '/usr/bin:/bin:/usr/sbin:/sbin', '--setenv', 'HOME', '/root',
               '--setenv', 'Q2EMU_LOG', '/emu-log/shim.log', '--setenv', 'Q2EMU_PCM', 'null', '--setenv', 'Q2EMU_PCMDIR', '/emu-log/pcm',
               '--setenv', 'LD_PRELOAD', '/lib/libq2emu.so', '--chdir', '/tmp', *run]
        return subprocess.Popen([str(c) for c in cmd], stdout=open(s.out / 'player.log', 'w'), stderr=subprocess.STDOUT)
    return go


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scenario')
    ap.add_argument('--out', required=True)
    ap.add_argument('--music', required=True, help='the songs test/player/music.py makes')
    ap.add_argument('--player', help='a player built for this machine')
    ap.add_argument('--emu', help='an emulator directory (rootfs/, state/, shim/, qemu-mipsel-static)')
    ap.add_argument('--hciplayer', help='with --emu: the MIPS player binary to run as /usr/bin/hciplayer')
    ap.add_argument('--stock', help='with --emu: the player q2player falls back to, at /usr/lib/hciplayer-stock/hciplayer')
    ap.add_argument('--switch', action='store_true', help='with --emu: make /mnt/data/q2player, which turns q2player on')
    ap.add_argument('--killall', type=float, help="with --emu: run demo's killall -9 hciplayer this many seconds in")
    a = ap.parse_args()
    music = str(pathlib.Path(a.music).resolve())
    if a.emu:
        launch, m = launch_emu(a.emu, pathlib.Path(a.hciplayer).resolve(), music, stock=a.stock and pathlib.Path(a.stock).resolve(),
                               switch=a.switch, killall=a.killall), '/mnt/mmc'
    else:
        launch, m = launch_host(a.player, music), music
    s = Session(a.out)
    s.music = pathlib.Path(a.music).resolve()
    s.run(scenarios(m)[a.scenario], launch)
    print((s.out / 'transcript.txt').read_text())


if __name__ == '__main__':
    main()
