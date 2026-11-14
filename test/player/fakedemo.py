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
import argparse, os, pathlib, re, select, shutil, socket, subprocess, sys, time

POLL = ('{player-status\\request}', '{media-info\\get-music-info}')


def song(cmd, path, start=0, cache=512, cache_min=20, flag=0, hw=17):
    """set-songurl or set-nexturl as demo's mclStartPlayer builds it (demo 0x5bf504): the start in seconds, the
    cache in KB and its fill percent (512 and 20 for a file on the card), demo's Gapless setting, the hardware
    type (17 on a Q2) and a constant 90."""
    return '{%s\\%s|ss=%d|%d|%d|%d|%d|90}' % (cmd, path.replace('}', '%0x7d'), start, cache, cache_min, flag, hw)


def gapless(path, start=0, flag=0):
    """set-gapless as demo's preload sender builds it (demo 0x5baff8): cache -1, 20 percent."""
    return '{set-gapless\\%s|ss=%d|-1|20|%d|90}' % (path.replace('}', '%0x7d'), start, flag)


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
        # queries with nothing loaded
        'idle': boot + [(1.0, '{player-status\\request}'), (1.2, '{media-info\\get-music-info}'), (1.4, '{mcl-pcmstate\\null}'),
                        (1.6, '{get_cache_buffer\\null}'), (1.8, '{mcl-getuvmeter\\null}'), (2.0, '{mcl-getspectrum\\null}'), (2.2, '{mcl-pause\\null}'),
                        (2.4, '{mcl-play\\null}'), (2.6, '{mcl-seek\\5}'), (2.8, '{mcl-stop\\null}'), (4.0, None)],
    }


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


def launch_emu(emu, binary, music, name='hciplayer', args=('-ao', 'alsa')):
    """binary inside a bwrap sandbox of emu/rootfs under qemu, as run.sh starts demo; /tmp is the session's."""
    emu = pathlib.Path(emu)
    def go(s):
        (s.out / 'pcm').mkdir(exist_ok=True)
        for f in (s.out / 'pcm').glob('*'): f.unlink()
        cmd = ['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-user', '--uid', '0', '--gid', '0',
               '--bind', emu / 'rootfs', '/', '--proc', '/proc', '--dev', '/dev', '--bind', s.tmp, '/tmp',
               '--ro-bind', music, '/mnt/mmc', '--bind', s.out, '/emu-log', '--ro-bind', emu / 'state', '/emu',
               '--ro-bind', emu / 'state/mtd_debug', '/usr/sbin/mtd_debug',
               '--ro-bind', emu / 'qemu-mipsel-static', '/usr/libexec/qemu-binfmt/mipsel-binfmt-P',
               '--ro-bind', emu / 'shim/libq2emu.so', '/lib/libq2emu.so', '--ro-bind', binary, f'/usr/bin/{name}',
               '--setenv', 'PATH', '/usr/bin:/bin:/usr/sbin:/sbin', '--setenv', 'HOME', '/root',
               '--setenv', 'Q2EMU_LOG', '/emu-log/shim.log', '--setenv', 'Q2EMU_PCM', 'null', '--setenv', 'Q2EMU_PCMDIR', '/emu-log/pcm',
               '--setenv', 'LD_PRELOAD', '/lib/libq2emu.so', '--chdir', '/tmp', f'/usr/bin/{name}', *args]
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
    a = ap.parse_args()
    music = str(pathlib.Path(a.music).resolve())
    if a.emu:
        launch, m = launch_emu(a.emu, pathlib.Path(a.hciplayer).resolve(), music), '/mnt/mmc'
    else:
        launch, m = launch_host(a.player, music), music
    s = Session(a.out)
    s.run(scenarios(m)[a.scenario], launch)
    print((s.out / 'transcript.txt').read_text())


if __name__ == '__main__':
    main()
