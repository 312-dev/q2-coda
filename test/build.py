#!/usr/bin/env python3
"""JPEG checks; optionally pass the stock ZIP to test packaging with a custom logo and quoted paths too."""
import io, math, re, sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first: test/build.py must import tools/build.py
from build import KB_ASSETS, KB_OPEN_HINT, KB_OPEN_SITES, check_keyboard_opens, jal, j, LABELS, relabel, ROOT, STOCK_EQ, STOCK_PAGES, TAG, dev_tag, jpeg_size, FONT, TAG_C, patch_m4b_cover, DEFAULT_CONFIG, default_config

logo = (ROOT/'assets/boot-logo.jpg').read_bytes()
assert jpeg_size(logo) == (320, 375)
# JPEG permits extra FF fill bytes before a marker.
assert jpeg_size(logo[:2] + b'\xff' + logo[2:]) == (320, 375)
frame = b'\xff\xd8\xff\xc0\x00\x11\x08\x01\x77\x01\x40'
# The stock display_logo reads three bytes per pixel without converting grayscale/CMYK.
# It also requires the documented 8-bit baseline format.
def sof(marker=0xc0, precision=8, components=3):
    return (b'\xff\xd8\xff' + bytes([marker]) + (8+3*components).to_bytes(2,'big') +
            bytes([precision]) + b'\x01\x77\x01\x40' + bytes([components]) +
            b''.join(bytes([i+1,0x11,0]) for i in range(components)))
assert jpeg_size(sof()) == (320,375)
for data in (b'', b'not a JPEG', frame, frame + bytes(8),
             b'\xff\xd8\xff', b'\xff\xd8\xff\xe0\x00',
             b'\xff\xd8\xff\xe0\x00\x01', b'\xff\xd8\xff\xd9' + logo[2:],
             sof(components=1), sof(components=4), sof(precision=12),
             sof(marker=0xc2), sof(marker=0xc3)):
    try:
        jpeg_size(data)
    except ValueError:
        continue
    raise AssertionError(f'Accepted malformed JPEG header: {data!r}')
print('JPEG header regression checks passed.')

# --dev tags: five characters, one per build number, never a release's.
tags = [dev_tag(n) for n in range(1, 36**3)]
assert tags[:2] == ['V001i', 'V002i'] and tags[35] == 'V010i' and tags[-1] == 'VZZZi'
assert len(set(tags)) == len(tags) and all(len(t) == 5 for t in tags) and TAG not in tags
assert dev_tag(36**3) == 'V001i'
assert re.fullmatch(r'V\d{4}', TAG), TAG  # a release's V1800, its beta 2's V1802
print('Dev build tag checks passed.')

def boot_check():
    """S90play's dual boot (build.py BOOT_HOOK) under the host sh, with stand-in programs."""
    import os, subprocess, tempfile, pathlib
    from build import BOOT_HOOK, WHEEL_LOG_HOOK, ROCKBOX_FLAG, WIFI_OFF
    with tempfile.TemporaryDirectory(prefix='q2-boot-') as tmp:
        r = pathlib.Path(tmp)
        def host(hook):
            script = hook.decode().replace(' &\n', '\n')
            for old in ('/mnt/', '/tmp/mmc_add', ROCKBOX_FLAG, '/usr/bin/q2boot', '/release/bin/demo', '/proc/mounts', '/tmp/q2-demo.log',
                        '/tmp/q2-scan-file', '/tmp/q2-scan.log', '/tmp/q2-art-file', '/tmp/q2-art.log', '/release/assets/'):
                script = script.replace(old, f'{r}{old}')
            script = script.replace('usleep 200000\n', ':\n').replace('usleep 500000', ':')
            # q2guard's respawn loop (build.py GUARD) never ends, and & is stripped above: it logs its start instead
            loop = '(while :; do nice -n -5 /usr/bin/q2guard'
            assert script.count(loop) == 1
            start = script.index(loop)
            script = script[:start] + f'echo "guard $e" >> {r}/guard' + script[script.index('\n', start):]
            # demo's stderr file, and its roll's minute: one pass here while {r}/minute exists
            script = script.replace('/tmp/q2-demo.err', f'{r}/tmp/q2-demo.err')
            # the crash's request to q2guard (docs/internals.md#unified-log): a stand-in on PATH takes the signal,
            # keeps the request it found and answers (removes it)
            script = script.replace('/tmp/q2guard.', f'{r}/tmp/q2guard.')
            assert script.count('kill -USR1 "$(cat ') == 1
            script = script.replace('kill -USR1 "$(cat ', 'usr1 "$(cat ')
            assert script.count('while sleep 60; do') == 1
            script = script.replace('while sleep 60; do', f'while [ -f {r}/minute ] && rm -f {r}/minute; do')
        # This script runs under the PC's own sh: the real killall -9 and ifconfig would take down the PC's network,
        # not a Q2's. So they never run here: a log line stands in, and the text must be gone before anything starts.
            assert script.count(WIFI_OFF) == 2  # before Rockbox, and the hand-over's late pass
            script = script.replace(WIFI_OFF, f'echo wifi >> {r}/order')
            assert script.count('/usr/bin/cmd_gpio set_func PE22 output0') == 1  # Rockbox starts with charging allowed (S12 inhibits it)
            script = script.replace('/usr/bin/cmd_gpio', 'echo gpio >> ' + str(r) + '/order; : ')
            assert 'usleep 300000' in script  # Home's hand-over pauses before Rockbox: logged, its place is the point
            script = script.replace('usleep 300000', f'echo settle >> {r}/order')
            assert 'usleep 2000000' in script and '\n            rockbox\n            kill $! 2>/dev/null\n' in script  # called off once Rockbox is gone
            script = script.replace('usleep 2000000', f'echo late >> {r}/order')  # runs in line here: & is stripped above
            # nor the PC's own reboot: demo's last end logs it, and the clean end's 10 s wait before it
            assert script.count('\n        [ "$s" ] || sleep 10\n        reboot\n') == 1
            script = script.replace('\n        [ "$s" ] || sleep 10\n        reboot\n',
                                    f'\n        [ "$s" ] || echo sleep10 >> {r}/end\n        echo reboot >> {r}/end\n')
            for real in ('killall -9', 'ifconfig', 'wlan0', 'wpa_supplicant', 'udhcpc', 'wifi_connect'): assert real not in script, real
            assert not re.search(r'(?m)^\s*(reboot|sleep 10)\b', script)
            return script
        script = host(BOOT_HOOK[1])
        for d in ('proc', 'mnt/data', 'mnt/mmc/.rockbox', 'tmp', 'usr/bin', 'release/bin', 'bin'): (r/d).mkdir(parents=True)
        def exe(path, body): (r/path).write_text('#!/bin/sh\n' + body + '\n'); (r/path).chmod(0o755)
        # demo takes General's Rockbox row (leaves the flag) as many times as the shortcuts file says
        flag, shortcuts = f'{r}{ROCKBOX_FLAG}', r/'shortcuts'
        # a demo that leaves q2-state (r/keep): it ended without ringnav_clean_end
        demo = (f'echo demo >> {r}/ran; echo demo >> {r}/order; n=$(cat {shortcuts}); '
                f'[ -f {r}/keep ] && echo running > {r}/mnt/data/q2-state; '
                f'[ "$n" -gt 0 ] && echo $((n - 1)) > {shortcuts} && : > {flag}; '
                f's=$(head -n1 {r}/code); [ $(wc -l < {r}/code) -gt 1 ] && sed -i 1d {r}/code; exit $s')  # one status per run, the last repeats
        exe('release/bin/demo', demo)
        rb, target = r/'mnt/mmc/.rockbox/rockbox', r/'mnt/data/boot-target'
        def boot(held, card, code=81, shortcut=0, status=137):
            shortcuts.write_text(f'{shortcut}\n')
            (r/'code').write_text(''.join(f'{c}\n' for c in (status if isinstance(status, (list, tuple)) else [status])))
            exe('usr/bin/q2boot', 'exit ' + ('0' if held else '1'))
            if card: exe('mnt/mmc/.rockbox/rockbox', f'pwd >> {r}/ran; echo rockbox >> {r}/order; exit {code}')
            else: rb.unlink(missing_ok=True)
            (r/'ran').write_text(''); (r/'order').write_text(''); (r/'end').write_text('')
            subprocess.run(['sh', '-c', script], check=True, env=dict(os.environ, PATH=f'{r}/bin:' + os.environ['PATH']))
            return (r/'ran').read_text().split(), target.exists() and target.read_text().strip()
        (r/'proc/mounts').write_text('')
        # the first boot over a config.ini from before the q2-defaults mark (stock's, or an earlier build's) puts Vamp's
        # defaults in its place, before demo; the saved Wi-Fi stays; every later boot keeps the user's settings
        ours, config, wifi, mark = r/'release/assets/default/raw/config.ini', r/'mnt/data/config.ini', r/'mnt/data/wpa_supplicant.conf', r/'mnt/data/q2-defaults'
        ours.parent.mkdir(parents=True)
        ours.write_text('[PLAYSET]\nCOVERMODE=0\n[SYSSET]\nLANGUAGE=2\n')
        config.write_text('[PLAYSET]\nCOVERMODE=1\n[SYSSET]\nLANGUAGE=1\n[Q2POD]\nCARREMOTE=1\n')
        wifi.write_text('network={ssid="Home"}\n')
        assert boot(False, False) == (['demo'], False)
        assert config.read_text() == ours.read_text() and mark.exists() and wifi.read_text() == 'network={ssid="Home"}\n'
        config.write_text('[PLAYSET]\nCOVERMODE=1\n')  # the user's own choice since
        assert boot(False, False) == (['demo'], False) and config.read_text() == '[PLAYSET]\nCOVERMODE=1\n'
        rockbox = [str(rb.parent), 'demo']
        assert boot(False, False) == (['demo'], False)          # no Rockbox on the card: Vamp
        assert boot(False, True, 1) == (rockbox, False)         # Rockbox by default; a crash starts Vamp
        assert (r/'mnt/mmc/.rockbox/rockbox.log').read_text() == 'exit 1\n'
        assert boot(False, True) == (rockbox, False)            # Boot stock OS (0x51): Vamp starts, nothing saved
        assert boot(True, True, 1) == (['demo'], False)         # Play/Pause held: Vamp, this session
        assert boot(False, True, 1) == (rockbox, False)         # the next power-on is Rockbox again
        target.write_text('stock\n')                            # a stale V8.4 choice is ignored
        assert boot(False, True, 1) == (rockbox, 'stock')
        assert boot(True, True, 1) == (['demo'], 'stock')
        for value in ('q2pod', 'rockbox', 'bad', '', 'q2pod\n', 'q2pod-extra'):
            target.write_text(value)
            expected = ['demo'] if value.rstrip('\n') == 'q2pod' else rockbox
            assert boot(False, True, 0) == (expected, value.strip())
            assert boot(False, True, 1) == (expected, value.strip())  # restart preserves choice
            assert boot(True, True) == (['demo'], value.strip())
            assert boot(False, False) == (['demo'], value.strip())
            assert boot(True, True, 81, 1) == (['demo', *rockbox], value.strip())
        target.unlink()
        # Home's Rockbox row: Rockbox, then Vamp again, as often as it is taken; the flag is gone after
        assert boot(True, True, 81, 1) == (['demo', *rockbox], False)
        assert boot(False, True, 81, 2) == (rockbox * 3, False) and not pathlib.Path(flag).exists()
        assert boot(True, False, 81, 1) == (['demo', 'demo'], False)  # no Rockbox on the card: Vamp again
        # Wi-Fi goes off before every start of Rockbox and at no other time; demo follows Rockbox whatever its status
        def order(*args): boot(*args); return (r/'order').read_text().split()
        session = ['gpio', 'wifi', 'rockbox', 'demo']  # charging is allowed before Rockbox starts
        for code in (0, 1, 81, 137, 255): assert order(False, True, code) == session
        handover = ['settle', 'late', 'wifi']
        assert order(True, True, 81, 1) == ['demo', *handover, *session]  # Home's Rockbox row waits for what demo launched
        assert order(False, True, 81, 2) == session + [*handover, *session] * 2  # power-on doesn't wait, the row twice does
        assert order(False, False) == ['demo']                  # no Rockbox on the card
        assert order(True, False, 81, 1) == ['demo', *handover, 'demo']  # nor when the row is taken
        assert order(True, True) == ['demo']                    # Play/Pause held
        target.write_text('q2pod'); assert order(False, True) == ['demo']; target.unlink()
        # demo's end, other than for Rockbox, reboots, as stock's watchdog (checkappprocess.sh) did when its pgrep found
        # no demo: at once once a crash's log is synced; after a clean end only if the Q2 is still up 10 s on
        def ended(status, shortcut=0): boot(True, False, shortcut=shortcut, status=status); return (r/'end').read_text().split()
        assert ended(137) == ['reboot'] and ended(139) == ['reboot']
        assert ended(0) == ['sleep10', 'reboot'] and ended(143) == ['sleep10', 'reboot']
        assert ended([137, 139], 1) == ['reboot']  # the Rockbox hand-over's kill doesn't end the loop: one reboot, after
        (r/'keep').write_text(''); assert ended(0) == ['reboot']; (r/'keep').unlink(); (r/'mnt/data/q2-state').unlink()  # no clean end
        for f in (r/'mnt/data/q2-crash.log', r/'mnt/mmc/q2-crash.log'): f.unlink(missing_ok=True)
        # an abnormal end of demo leaves its status on /mnt/data, which the next boot copies to a mounted card;
        # 0, 143 and the Rockbox hand-over leave nothing
        crash, copy, mounts = r/'mnt/data/q2-crash.log', r/'mnt/mmc/q2-crash.log', r/'proc/mounts'
        def logged(status, card, shortcut=0):
            for f in (crash, copy): f.unlink(missing_ok=True)
            mounts.write_text(f'tmpfs /tmp tmpfs rw 0 0\n/dev/mmcblk0p1 {r}/mnt/mmc vfat rw 0 0\n' if card else '')
            boot(True, False, shortcut=shortcut, status=status)
            return crash.exists() and crash.read_text(), copy.exists() and copy.read_text()
        out, cp = logged(137, True)
        assert 'demo exit 137' in out and '== dmesg' in out and '== meminfo' in out and '== ps' in out and cp is False  # no card write while dying
        assert not crash.with_name('q2-crash.log.tmp').exists()  # written whole, then renamed
        mounts.write_text(''); boot(True, False, status=0); assert not copy.exists() and crash.exists()  # no card: kept for a later boot
        mounts.write_text(f'/dev/mmcblk0p1 {r}/mnt/mmc vfat rw 0 0\n'); boot(True, False, status=0)
        assert copy.read_text() == out and not crash.exists()  # the next boot with a card moves it there
        copy.write_text('kept'); boot(True, False, status=0); assert copy.read_text() == 'kept'  # and never brings it back
        # a crash before anyone reads the card keeps the one there as q2-crash.log.1, and that one as .2
        logs = copy, copy.with_name('q2-crash.log.1'), copy.with_name('q2-crash.log.2')
        def card(): return [f.exists() and f.read_text() for f in logs]
        def crashed(status): boot(True, False, status=status); return crash.read_text()
        for f in (crash, *logs): f.unlink(missing_ok=True)
        first, second = crashed(134), crashed(137)  # the second boot moves the first to the card
        boot(True, False, status=0)  # and this one the second
        assert card() == [second, first, False] and not crash.exists()
        third, fourth = crashed(138), crashed(139)
        boot(True, False, status=0)
        assert card() == [fourth, third, second]  # two kept before the newest, the oldest dropped
        boot(True, False, status=0); assert card() == [fourth, third, second]  # nothing new: nothing moves
        # a full card: the copy fails, so nothing moves and the log stays on /mnt/data for the next boot
        fifth = crashed(140); exe('bin/cp', 'exit 1')
        boot(True, False, status=0); assert card() == [fourth, third, second] and crash.read_text() == fifth
        (r/'bin/cp').unlink(); copy.with_name('q2-crash.log.new').unlink(missing_ok=True)
        boot(True, False, status=0); assert card() == [fifth, fourth, third] and not crash.exists()
        for f in logs: f.unlink()
        out, cp = logged(139, False)
        assert 'demo exit 139' in out and cp is False
        assert logged(0, True) == (False, False) and logged(143, True) == (False, False)
        assert logged([137, 0], True, 1) == (False, False)  # the Rockbox hand-over's kill, then a clean demo
        # 0 or 143 with q2-state still there was no clean end (ringnav_clean_end removes it first): logged as a crash
        keep, state = r/'keep', r/'mnt/data/q2-state'
        keep.write_text('')
        for status in (0, 143):
            out, cp = logged(status, False)
            assert f'== demo exit {status}\n' in out and '== dmesg' in out and cp is False and state.exists()
        keep.unlink(); state.unlink()
        # a library scan's and Coverflow's art build's records (tags.c crash_note): the file in progress and
        # the last 60 memory lines
        for name, last, every in (('scan', 'last scanned', 500), ('art', 'last art', 50)):
            note_file, note_log = r/f'tmp/q2-{name}-file', r/f'tmp/q2-{name}.log'
            note_file.write_text('/mnt/mmc/Music/a.opus\n')
            note_log.write_text(''.join(f'{n * every} {91000 + n} /mnt/mmc/{n}.opus\n' for n in range(1, 71)))
            out, cp = logged(137, False)
            assert f'== {last}\n/mnt/mmc/Music/a.opus\n' in out
            lines = out.split(f'== {name}\n')[1].split('== ')[0].splitlines()
            assert len(lines) == 60 and lines[0].startswith(f'{11 * every} 91011 ') and lines[-1] == f'{70 * every} 91070 /mnt/mmc/70.opus'
            note_file.unlink(); note_log.unlink()
            out, cp = logged(137, False)
            assert f'== {last}' not in out and f'== {name}\n' not in out
        # demo's stderr (build.py BOOT_HOOK): kept in RAM across its runs, its last 60 lines end the crash log; the
        # once-a-minute roll (one pass here while {r}/minute exists) copies it to .1 and empties it past 256 KB and
        # leaves a smaller one alone
        err, minute, noise = r/'tmp/q2-demo.err', r/'minute', ''.join(f'err {i}\n' for i in range(1, 71))
        exe('release/bin/demo', 'i=0; while [ $i -lt 70 ]; do i=$((i+1)); echo "err $i" >&2; done; ' + demo)
        err.unlink(missing_ok=True); out, cp = logged(137, False)
        lines = out.split('== stderr\n')[1].splitlines()
        assert len(lines) == 60 and lines[0] == 'err 11' and lines[-1] == 'err 70' and err.read_text() == noise
        out, cp = logged(137, False)
        assert err.read_text() == noise * 2 and out.split('== stderr\n')[1].splitlines() == lines  # appended across demo's runs
        err1 = r/'tmp/q2-demo.err.1'
        err.write_bytes(b'x' * 262145); minute.write_text(''); boot(True, False, status=0)
        assert not minute.exists() and err.read_text() == noise  # past the cap: rolled before demo ran again
        assert err1.read_bytes() == b'x' * 262145  # moved whole to .1, not lost
        err1.unlink()
        err.write_bytes(b'x' * 262144); minute.write_text(''); boot(True, False, status=0)
        assert not minute.exists() and err.read_bytes() == b'x' * 262144 + noise.encode()  # at the cap: kept

        exe('release/bin/demo', demo); err.unlink(); out, cp = logged(137, False)
        assert out.endswith('== stderr\n') and err.read_text() == ''  # the shell makes the file as demo starts: an empty section says so
        # a crash just after a roll: the crash log's tail reads across .1 and the new file
        err1.write_text(''.join(f'old {i}\n' for i in range(1, 71))); err.write_text('new 1\nnew 2\n')
        out, cp = logged(137, False)
        assert out.split('== stderr\n')[1].splitlines() == [f'old {i}' for i in range(13, 71)] + ['new 1', 'new 2']
        err1.unlink()
        # a crash asks q2guard (the pid in /tmp/q2guard.lock) for the unified log's snapshot, and waits for its answer
        snap, requests = r/'tmp/q2guard.snap', r/'requests'
        (r/'tmp/q2guard.lock').write_text('4242\n')
        exe('bin/usr1', f'echo "$1 $(cat {snap})" >> {requests}; rm -f {snap}')
        logged(139, False)
        assert requests.read_text() == '4242 crash demo exit 139\n' and not snap.exists()
        requests.unlink(); boot(True, False, status=0)
        assert not requests.exists() and not snap.exists()  # a clean end: demo's own request (ulog.c), none here
        (r/'bin/usr1').unlink(); exe('bin/usr1', 'exit 1')  # no q2guard: nothing waits, nothing is left
        logged(137, False); assert not snap.exists()
        (r/'bin/usr1').unlink(); (r/'tmp/q2guard.lock').unlink()
        err.unlink()
        # an OOM kill (5.10's wording) whose task dump is longer than the 200-line tail: its own lines, from all of
        # dmesg (a stand-in on PATH) go ahead of the tail
        head = '[  900.100000] demo invoked oom-killer: gfp_mask=0x100cca(GFP_HIGHUSER_MOVABLE), order=0, oom_score_adj=0'
        dump = [f'[  900.2{n:05}] [{300 + n:7}]     0 {300 + n:7}     2000      500    16384        0             0 task{n}' for n in range(250)]
        kill = ('[  900.300000] Out of memory: Killed process 300 (demo) total-vm:150000kB, anon-rss:92000kB, file-rss:1000kB, '
                'shmem-rss:0kB, UID:0 pgtables:120kB oom_score_adj:0')
        summary = ('[  900.290000] oom-kill:constraint=CONSTRAINT_NONE,nodemask=(null),cpuset=/,mems_allowed=0,'
                   'global_oom,task_memcg=/,task=demo,pid=300,uid=0')
        reap = '[  900.400000] oom_reaper: reaped process 300 (demo), now anon-rss:0kB, file-rss:0kB, shmem-rss:0kB'
        (r/'dmesg').write_text('\n'.join([head, *dump, summary, kill, reap]) + '\n'); exe('bin/dmesg', f'cat {r}/dmesg')
        out, cp = logged(137, False)
        assert '== oom\n' in out
        oom, tail = out.split('== oom\n')[1].split('== dmesg\n')
        assert oom == f'{head}\n{summary}\n{kill}\n{reap}\n' and head not in tail and kill in tail
        (r/'bin/dmesg').unlink(); (r/'dmesg').unlink()
        # /mnt/data/q2-state (system.c state_poll) still there at boot: the last session ended in a forced
        # restart, or the battery. It goes into q2-crash.log, a new one that says so or the crash's, and goes.
        state, line = r/'mnt/data/q2-state', 'screen:off power:suspending cable:out battery:57 play:paused out:jack\n'
        state.write_text(line); out, cp = logged(0, False)
        assert 'did not end' in out and '== last state' in out and line in out and not state.exists() and cp is False
        # a Coverflow open that never finished (coverflow.c cf_trace) goes in after the state, and goes
        trace = r/'mnt/data/q2-cf-trace'
        state.write_text(line); trace.write_text('start albums=0 0 ms\nstaged albums=20000 900 ms\n'); out, cp = logged(0, False)
        assert '== last state' in out and out.endswith('== cover flow\nstart albums=0 0 ms\nstaged albums=20000 900 ms\n') and not trace.exists()
        trace.write_text('stale\n'); out, cp = logged(0, False)
        assert out is False and not trace.exists()  # no forced restart: dropped, not reported
        # the UI loop's stall record (system.c stall_write) goes in last, and goes
        stall, record = r/'mnt/data/q2-stall', 'age 20000 ms\ntoasts 1\nstate D\nwchan wait_on_page_bit_common\n'
        state.write_text(line); trace.write_text('start albums=0 0 ms\n'); stall.write_text(record); out, cp = logged(0, False)
        assert out.endswith('== cover flow\nstart albums=0 0 ms\n== stall\n' + record) and not stall.exists() and not trace.exists()
        state.write_text(line); stall.write_text(record); out, cp = logged(0, False)
        assert out.endswith(line + '== stall\n' + record) and '== cover flow' not in out and not stall.exists()
        stall.write_text('stale\n'); out, cp = logged(0, False)
        assert out is False and not stall.exists()  # no forced restart: dropped, not reported
        # an unclean end found at boot keeps its evidence on the card (docs/internals.md#unified-log): the unified log's
        # files, the car's newest log and dmesg in a numbered folder under .q2muse/crashes, named in q2-crash.log; a
        # clean boot keeps nothing; the newest five are kept, a sixth drops the oldest; a card short of room keeps none
        ulogs, kept = r/'mnt/mmc/.q2muse/logs', r/'mnt/mmc/.q2muse/crashes'
        ulogs.mkdir(parents=True, exist_ok=True)
        evidence = ['q2-log.0', 'q2-log.1', 'q2-heat.0', 'q2-bt.0', 'q2-btev.0', 'q2car.log', 'q2car.log.dmesg']
        for f in (*evidence, 'q2car.1.log', 'other.txt'):
            (ulogs/f).write_text(f + ' lines\n')
        exe('bin/dmesg', 'echo kernel ring')
        def folders(): return sorted(p.name for p in kept.iterdir() if p.is_dir()) if kept.exists() else []
        for f in (crash, copy): f.unlink(missing_ok=True)
        logged(0, True); assert folders() == []  # a clean end
        state.write_text(line); out, cp = logged(0, True)
        (one,) = folders()
        assert sorted(p.name for p in (kept/one).iterdir()) == sorted([*evidence, 'dmesg'])
        assert (kept/one/'q2-log.0').read_text() == 'q2-log.0 lines\n' and (kept/one/'dmesg').read_text() == 'kernel ring\n'
        assert cp.endswith(f'== evidence .q2muse/crashes/{one}\n') and not state.exists()
        for _ in range(5):
            state.write_text(line); logged(0, True)
        assert len(folders()) == 5 and one not in folders() and folders() == sorted(folders())
        exe('bin/df', 'echo "Filesystem 1K-blocks Used Available Use% Mounted on"; echo "card 100 90 10 90% /mnt/mmc"')
        before = folders(); state.write_text(line); out, cp = logged(0, True)
        assert folders() == before and '== evidence' not in cp  # no room: nothing copied, nothing named
        (r/'bin/df').unlink(); (r/'bin/dmesg').unlink(); mounts.write_text('')
        for f in (crash, copy): f.unlink(missing_ok=True)
        crash.write_text('== demo exit 137\n'); state.write_text(line); boot(True, False, status=0)
        assert crash.read_text().startswith('== demo exit 137\n== last state\n') and line in crash.read_text() and not state.exists()
        for f in (crash, copy): f.unlink(missing_ok=True)
        # the wheel's stale-press notes (navigation.c stale_note): a release build leaves them alone, and a --dev one
        # takes them to the card, appended, on their own
        assert 'wheel' not in script
        wheel, wcopy = r/'mnt/data/q2-wheel.log', r/'mnt/mmc/q2-wheel.log'
        script = host(BOOT_HOOK[1].replace(*WHEEL_LOG_HOOK))
        wheel.write_text('== wheel: one\n'); mounts.write_text(''); boot(True, False, status=0)
        assert wheel.exists() and not wcopy.exists() and not copy.exists()  # no card: kept
        mounts.write_text(f'/dev/mmcblk0p1 {r}/mnt/mmc vfat rw 0 0\n'); wcopy.write_text('== wheel: earlier\n'); boot(True, False, status=0)
        assert wcopy.read_text() == '== wheel: earlier\n== wheel: one\n' and not wheel.exists() and not copy.exists()
        wheel.write_text('== wheel: two\n'); crash.write_text('== demo exit 137\n'); boot(True, False, status=0)
        assert wcopy.read_text().endswith('== wheel: two\n') and copy.read_text() == '== demo exit 137\n' and not wheel.exists() and not crash.exists()
        wcopy.write_bytes(b'x' * 262144); wheel.write_text('== wheel: three\n'); boot(True, False, status=0)
        assert wcopy.stat().st_size == 262144 and not wheel.exists()  # the card's file stops growing
        wcopy.write_bytes(b'x' * 262143); wheel.write_text('== wheel: four\n'); boot(True, False, status=0)
        assert wcopy.read_bytes().endswith(b'== wheel: four\n') and not wheel.exists() and not (r/'mnt/data/q2-wheel.log.m').exists()
        for f in (crash, copy, wheel, wcopy): f.unlink(missing_ok=True)
    print('Dual boot: Rockbox by default, one-session Vamp, stale choice ignored, card fallback, Home Rockbox row and Wi-Fi off before Rockbox passed.')
boot_check()


def usb_charge_check():
    """S12usbcharge (patch/usb_charge.sh) under the host sh, cmd_gpio a stand-in that logs its arguments: the charge
    inhibit, then PD18's pull-up (docs/internals.md#otg-host-power), each once, and nothing for stop."""
    import subprocess, tempfile, pathlib
    with tempfile.TemporaryDirectory(prefix='q2-s12-') as tmp:
        r = pathlib.Path(tmp)
        script = (ROOT/'patch/usb_charge.sh').read_text()
        assert script.count('/usr/bin/cmd_gpio') == 2
        (r/'s12').write_text(script.replace('/usr/bin/cmd_gpio', f'echo >> {r}/log'))
        for arg in ('start', 'stop'):
            subprocess.run(['sh', str(r/'s12'), arg], check=True)
        assert (r/'log').read_text().splitlines() == ['set_func PE22 output1', 'set_func PD18 input pull_up']
    print('S12usbcharge: charge inhibit and the ID pin pull-up passed.')

usb_charge_check()

def logo_check():
    """S11logo_display_shell's splash choice (build.py LOGO_HOOK) under the host sh: the card's logo, else the
    boot animation, else the built-in still, in Light Mode as in dark (docs/boot.md#light-mode)."""
    import subprocess, tempfile, pathlib
    from build import LOGO_HOOK, LOGO, LOGO_ANIM, LOGO_BLACK
    with tempfile.TemporaryDirectory(prefix='q2-logo-') as tmp:
        tmp = pathlib.Path(tmp)
        user, config, dark = tmp/'boot-logo.jpg', tmp/'config.ini', tmp/'logo.jpg'
        anim, black = tmp/'logo.q2a', tmp/'logo-black.jpg'
        script = (LOGO_HOOK[1].decode().replace('/mnt/data/boot-logo.jpg', str(user)).replace('/mnt/data/config.ini', str(config))
                  .replace('/'+LOGO_ANIM, str(anim)).replace('/'+LOGO_BLACK, str(black))
                  .replace('/'+LOGO, str(dark)).replace('display_logo', 'echo').replace('/usr/bin/q2splash', 'q2splash'))
        def shown(): return subprocess.run(['sh', '-c', script], check=True, capture_output=True, text=True).stdout.strip()
        assert shown() == str(dark)  # no card logo, no animation (custom --logo): the built-in splash
        for light in ('1', '2'):
            config.write_text(f'[IPOD]\nLIGHT={light}\n')
            assert shown() == str(dark)  # Light Mode and Auto boot dark as well
        user.write_bytes(b'')
        assert shown() == str(user)  # the card's logo wins
        user.unlink()
        # The boot animation (docs/boot.md#boot-animation), on builds that ship it, in either mode
        anim.write_bytes(b'')
        def played(status=0): return subprocess.run(['sh', '-c', f'q2splash() {{ echo play $1; return {status}; }}; ' + script], check=True, capture_output=True, text=True).stdout.split('\n')[:-1]
        for light in ('0', '1', '2'):
            config.write_text(f'[IPOD]\nLIGHT={light}\n')
            assert played() == [str(black), f'play {anim}'], light  # the black splash turns the screen on, then the animation
        assert played(1) == [str(black), f'play {anim}', str(dark)]  # q2splash could not start: the still logo
        user.write_bytes(b'')
        assert played() == [str(user)]
    print('Boot logo: the saved card logo, else the boot animation or the built-in one, dark in Light Mode too, passed.')
logo_check()

def validate_assets(directory):
    import functools, json, re, struct, subprocess
    from build import sha, run, fileoff, symbols, BLUEALSA, AAC_44K1, GRADIENT_LEAF, WM_PAINT_LEAF, QUEUE_LEAF, HELPER, HELPER_LIKE, BOOT, SPLASH, LOGO_ANIM, LOGO_BLACK, BOOT_HOOK, GUARD, GUARD_DEV_HOOK, REMOTE, Q2EQ, REMOTE_HOOK, LOG_HOOK, WHEEL_LOG_HOOK, S90PLAY, S11LOGO, LOGO_HOOK, BLUEALSA2, BT_KEEPALIVE, RTC_WRITE, WIFI_LPS, HOOKS, SAVER_OFF, FULL_BLEED, HTRANSLATE_VT, WATCHDOG, WATCHDOG_STUB, DROP_CACHES, STREAM_POLL_CALLS, DARK_POLLS, COVER_WAITS, BATTERY_READS, WHEEL_TS_READ, PDR
    from ipod import (ART_PLACEHOLDERS, sized_art, AUDIT, TOOLBAR_SWITCHES, CONFIRM, VOLUME, BIGCOVER, BC_ART, BC_TITLE_PAD, QUICK_SETTINGS, QS_TOP, QS_LABEL_GAP, QS_LABEL_H,
                      QS_LABEL_W, QS_ROW_GAP, QS_PITCH, QS_BAR, QS_TOUCH, QS_EDGE, QS_SUN, HOME_LABEL_END, HOME_LIST_W, HOME_TEXT_X, HOME_TOP, ARTIST_PAGE, HOME_OURS, HOME_PAGE, HOME_ROWS, HOME_SETS, NAVBAR_ONLY, PLAYING_PAGE, SLEEP_TIMER, UI_ASSETS,
                      STATUS_BAR, STATUS_ROW_RIGHT, corner_inset, corner_x, immediate_values,
                      STATUS_ICONS, status_icon, QUIET_ICONS, quiet_icon, SET_ICON, SET_STOCK_ICON, SETTINGS_ICONS, SETTINGS_SLIDERS, decode, slider_plain, imagemagick, inc, png_header, settings_icon, walk,
                      patch_asset, patch_code, patch_style, style_props, SLIDE, STOCK_ROWS, MODAL_PAGES, KEEP_TOOLBAR,
                      STATUS_ROW_GAP, STATUS_ROW_LINE, full_bleed)
    manifest = json.loads((directory/'manifest.json').read_text())
    stock = (directory/'stock-demo').read_bytes()
    demo = (directory/'demo').read_bytes()
    # platform_init's PD18 input: pull_hiz (0) in stock, pull_up (2) here (docs/internals.md#otg-host-power)
    from build import ID_PULL_UP
    at, old, new = ID_PULL_UP
    assert struct.unpack_from('<I', stock, fileoff(stock, at))[0] == old and struct.unpack_from('<I', demo, fileoff(demo, at))[0] == new
    from build import patch_timezones, TIMEZONES, CJK, english_ui
    from build import ADB_HOOK, ADBD, ADB_GADGET, Q2UPDATE, USB_SCRIPTS, adb_wrapper, USBTRACE, USBTRACE_HOOK
    zones = bytearray(stock)
    patch_timezones(zones)
    start = fileoff(stock, 0x77efb0)
    labels = [zones[start+i*32:start+(i+1)*32].split(b'\0')[0] for i in range(1+len(TIMEZONES))]
    assert labels == [b'timedate_auto'] + [label.encode() for label, _ in TIMEZONES]
    assert demo[start:start+28*64] == zones[start:start+28*64]
    for address in (0x4c1bd8, 0x4c1ed8, 0x4c23f4, 0x504eec):
        off = fileoff(stock, address)
        assert demo[off:off+4] == zones[off:off+4]
    assert manifest['version'].encode()+b'\0' in demo
    # A widget's style, as stock's widget_update_style passes it to the style's notify: lw $4, W_ASTYLE($16).
    astyle = int(re.search(r'#define W_ASTYLE (0x[0-9a-f]+)', (ROOT/'patch/offsets.inc').read_text())[1], 16)
    off = fileoff(stock, 0x6717d8)
    assert int.from_bytes(stock[off:off+4], 'little') == 0x8e040000 | astyle
    # .pdr (build.py PDR), past every LOAD segment, ships zeroed.
    assert demo[PDR[0]:PDR[0]+PDR[1]] == bytes(PDR[1]) and any(stock[PDR[0]:PDR[0]+PDR[1]])
    # The RTC is written in UTC: the one hwclock -w literal gains -u in its own padding.
    assert demo.index(RTC_WRITE[1]) == stock.index(RTC_WRITE[0])
    # Stock's HTTPS verifies (docs/internals.md#stock-https): each setopt that turned a check off calls ringnav_stock_tls,
    # and the Tidal API URLs that were plain HTTP are https://, in place.
    from build import STOCK_TLS_CALLS, stock_tls_site, TIDAL_HTTP
    stock_tls = jal(symbols(directory/'patch.elf')['ringnav_stock_tls'])
    for address, option in STOCK_TLS_CALLS:
        assert stock_tls_site(stock, address, option)
        assert struct.unpack_from('<I', demo, fileoff(stock, address))[0] == stock_tls, hex(address)
    for path in TIDAL_HTTP:
        assert demo.index(b'\0https://api.tidal.com' + path + b'\0') == stock.index(b'\0http://api.tidal.com' + path + b'\0')
    assert b'http://api.tidal.com' not in demo
    # Network time asks the worldwide NTP pool, not China's: one server name, the same length, in place.
    from build import NTP_POOL
    assert demo.index(NTP_POOL[1]) == stock.index(NTP_POOL[0]) == fileoff(stock, 0x7836bc) - 1 and b'cn.pool.ntp.org' not in demo
    # Wi-Fi keeps its power save: network_checkstatus's lps=0 becomes lps=2, the one byte at file
    # offset 0x3852bd, and networkGetWifiSignal jumps to the payload, which reads rx_signal instead.
    assert demo.index(WIFI_LPS[1]) == stock.index(WIFI_LPS[0]) == 0x3852bd - 25 and WIFI_LPS[0] not in demo
    assert [i for i in range(0x385290, 0x3852f0) if demo[i] != stock[i]] == [0x3852bd] and demo[0x3852bd:0x3852be] == b'2'
    off = fileoff(stock, HOOKS['networkGetWifiSignal'][0])
    assert demo[off:off+8] == j(symbols(directory/'patch.elf')['ringnav_wifi_signal']).to_bytes(4, 'little') + bytes(4)
    # Stock's lock screen stays off: config_init stores 0 in g_screensaver_mode whatever SYSSET SCREENSAVER says.
    off = fileoff(stock, SAVER_OFF[0])
    assert struct.unpack_from('<I', stock, off)[0] == SAVER_OFF[1] and struct.unpack_from('<I', demo, off)[0] == SAVER_OFF[2]
    # Every page window is the whole screen (docs/oled-design.md#full-bleed): window_manager_default_layout_child's
    # branch to the client rect under the system bar is a nop, and the status row paints over both pages of a slide,
    # htranslate's draw_curr slot (+0x1c) page_parallax.c's parallax_curr.
    off = fileoff(stock, FULL_BLEED[0])
    assert struct.unpack_from('<I', stock, off)[0] == FULL_BLEED[1] and struct.unpack_from('<I', demo, off)[0] == FULL_BLEED[2]
    off = fileoff(stock, HTRANSLATE_VT + 0x1c)
    assert struct.unpack_from('<I', stock, off)[0] == 0x696c40
    assert struct.unpack_from('<I', demo, off)[0] == symbols(directory/'patch.elf')['parallax_curr']
    # The slide hint names a stock animator and gives it a duration (a missing one means stock's 500 ms).
    assert re.fullmatch(r'htranslate\(duration=\d+\)', SLIDE) and b'\0htranslate\0' in stock
    # The animated lookup pages need synchronous unwind.
    for address, slot in ((0x4a7ed4, 0x468), (0x4af83c, 0x468), (0x537fbc, 0x420)):
        off = fileoff(stock, address)
        want = struct.pack('<III', 0x8f99a914, 0x0320f809, 0x8fa40000 | slot)
        assert demo[off:off+12] == want
    # Every hook and leaf jumps to its payload function (build.py pins the leaves' words): the
    # leaves are window_manager_paint (Videos), style_get_gradient and main_loop_queue_event.
    for address, name in [*(h[:2] for h in HOOKS.values()), WM_PAINT_LEAF[1:3], GRADIENT_LEAF[1:3], QUEUE_LEAF[1:3]]:
        off = fileoff(stock, address)
        assert demo[off:off+8] == j(symbols(directory/'patch.elf')[name]).to_bytes(4, 'little') + bytes(4)
    from build import AUDIO_EXTENSION_CALLS
    for entry, address in zip(manifest['audio_extension_calls'], AUDIO_EXTENSION_CALLS, strict=True):
        off = fileoff(stock, address)
        assert entry['address'] == hex(address)
        assert struct.unpack_from('<I', stock, off)[0] == int(entry['original'], 16)
        assert struct.unpack_from('<I', demo, off)[0] == int(entry['replacement'], 16)
        assert stock[off-8:off-4] == struct.pack('<I', 0x8f99fa14)
    changed = manifest['changed_assets']
    xx = 'release/assets/default/raw/images/xx/'
    def read(image, rel):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory/image), rel])
    # Each card app the image carries (BUNDLED_APPS) is its pinned zip's program, byte for byte, with a bundle file
    # naming the zip and both hashes (build.py bundle_record), as manifest.json's bundled_apps does.
    import zipfile
    from build import BUNDLED_APPS, BUNDLED_DIR, APPS_DIR, bundle_fields
    for app_id, (_, zip_name, _, digest, _) in BUNDLED_APPS.items():
        binary = read('rootfs.squashfs', f'{BUNDLED_DIR}/{app_id}/q2-{app_id}')
        record = bundle_fields(read('rootfs.squashfs', f'{BUNDLED_DIR}/{app_id}/bundle'))
        entry = manifest['bundled_apps'][app_id]
        assert record['zip'] == zip_name == entry['zip'] and record['zip_sha256'] == digest == entry['sha256'], record
        assert record[f'q2-{app_id}_sha256'] == sha(binary) == entry['binary_sha256'], f'{app_id}: not the program its bundle names'
        assert record['build'] == entry['build']
        pinned = APPS_DIR/zip_name
        assert pinned.is_file() and sha(pinned.read_bytes()) == digest, f'{pinned}: missing or not the pinned zip'
        with zipfile.ZipFile(pinned) as z:
            assert z.read(f'.{app_id}/q2-{app_id}') == binary, f'{app_id}: the image\'s program is not the pinned zip\'s'
    # Every stock UI asset with Chinese text is given in English (english_ui), the edited ones after their edit.
    ui = 'release/assets/default/raw/ui/'
    # Every stock UI asset the image keeps (the stock EQ pages and STOCK_PAGES go): the build writes each one full-bleed
    # (tools/ipod.py full_bleed), so each is a changed asset.
    kept_ui = {l.removeprefix('squashfs-root/'+ui) for l in run('unsquashfs', '-l', directory/'stock.squashfs').splitlines()
               if l.startswith('squashfs-root/'+ui) and l.endswith('.bin') and l.removeprefix('squashfs-root/') not in STOCK_EQ + STOCK_PAGES + KB_ASSETS}
    chinese = {rel for rel in kept_ui if CJK.search(read('stock.squashfs', ui+rel).decode('utf-8', 'replace'))}
    assert chinese and {'testmode/test_key.bin', 'dialog/search_dialog.bin'} <= chinese
    from theme import builtins, built_geometry
    GEOM = built_geometry(builtins())  # the set the build bakes: OLED's
    # every UI asset, and the stock list pages OLED's set changes (tools/ipod.py ui_pages)
    patched_ui = set(UI_ASSETS) | {p for p in STOCK_ROWS if patch_asset(p, stock_ui := read('stock.squashfs', ui+p), g=GEOM) != stock_ui}
    assert patched_ui | chinese <= kept_ui
    assert set(changed) == {ui+p for p in kept_ui} | {
        'release/assets/default/raw/styles/'+p for p in AUDIT['styles']} | {
        xx+n for n in SETTINGS_ICONS | QUIET_ICONS | STATUS_ICONS | ART_PLACEHOLDERS} | {'release/assets/default/raw/strings/en_US.bin', FONT, TAG_C} | ({'usr/bin/' + n for n in USB_SCRIPTS} if manifest['dev'] else set())  # adb_wrapper
    master = (ROOT/'assets/icons/default_album_home.png').read_bytes()
    for name, size in ART_PLACEHOLDERS.items():
        old, new = read('stock.squashfs', xx+name), read('rootfs.squashfs', xx+name)
        assert png_header(new)[:2] == (size if isinstance(size, tuple) else (size, size)), name
        assert new == sized_art(master, size), name
        assert changed[xx+name] == dict(original_sha256=sha(old), sha256=sha(new))
        if not isinstance(size, tuple):  # transparent corners, not black (alpha 255), over a selected row; Lanczos
            # leaves a trace of the art's rounded edge in some sizes' corner pixel (60px: alpha 2 on ImageMagick 6.9.12)
            assert float(imagemagick('png:-', '-format', '%[fx:p{0,0}.a]', 'info:', data=new)) < 0.05, name
    games = read('rootfs.squashfs', xx+'local_games.png')
    assert games == (ROOT/'assets/icons/local_games.png').read_bytes()
    assert png_header(games) == (52, 52, 8, 6)
    assert imagemagick('png:-', '-format', '%[fx:p{0,0}.a]', 'info:', data=games) == b'0'
    flow = read('rootfs.squashfs', xx+'default_album_flow.png')
    assert flow == sized_art(master, 160) and png_header(flow) == (160, 160, 8, 6)
    assert read('rootfs.squashfs', xx+'default_album_home.png') == master
    # Stock's own card icons (Home's hidden carousel) as stock's: V1.36's Album Wall icons, menu_coverflow*.png, untouched
    for name in ('menu_coverflow.png', 'menu_coverflowdown.png'):
        assert read('rootfs.squashfs', xx+name) == read('stock.squashfs', xx+name) and xx+name not in changed, name
    tag_old, tag_new = read('stock.squashfs', TAG_C), read('rootfs.squashfs', TAG_C)
    assert tag_new == patch_m4b_cover(tag_old)
    assert changed[TAG_C] == dict(original_sha256=sha(tag_old), sha256=sha(tag_new))
    # Wi-Fi File Transfer is gone (docs/localsend.md#what-it-replaces): stock's thttpd conf, its page and its CGIs
    # stay as stock shipped them, and nothing starts the server.
    for path in ('etc/thttpd.conf', 'usr/share/web/index.html', 'usr/share/web/cgi-bin/upload.cgi'):
        assert read('rootfs.squashfs', path) == read('stock.squashfs', path) and path not in changed, path
    from build import segments
    tag_segment = segments(tag_new)[-1][1]
    assert tag_segment[0] == 1 and tag_segment[2] == 0x30000 and tag_segment[4:7] == (48, 48, 5)
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.ttf') as font:
        font.write(read('stock.squashfs','release/assets/default/raw/fonts/default.ttf')); font.flush()
        for text,size,room in [('100%',inc('BATT_PCT_PX'),inc('BATT_PCT_W'))]:  # the Battery setting's widest percentage
            rendered=imagemagick('-font',font.name,'-pointsize',str(size),'label:'+text,'png:-',data=b'')
            assert png_header(rendered)[0]-1<=room,(text,size,room,png_header(rendered))
    # Now Playing's queue reads "Queue", Idle "Power" and standby/shutdown "Standby" (LABELS); the table's later offsets move with them and nothing else changes.
    strings = 'release/assets/default/raw/strings/en_US.bin'
    old, new = read('stock.squashfs', strings), read('rootfs.squashfs', strings)
    assert new == relabel(old)
    n = 2 * struct.unpack_from('<H', new, 2)[0]
    table = dict(zip(*[iter(new[o:new.index(b'\0', o)] for o in struct.unpack_from(f'<{n}I', new, 4))] * 2))
    assert [table[k] for k, _, _ in LABELS] == [v for _, _, v in LABELS] and table[b'powerm_sleep'] == b'Sleep Timer'
    # Settings icons: the audited 52px artwork, packaged as SET_ICON RGBA with the same transparency
    # and, on a plain background, the same average colour. OLED (list.icons.mono) greys them
    # as they load (navigation.c ringnav_image_add: every disc its scheme's chevron, MINIMAL_CHEVRON, glyphs white).
    def mean(png, bg):
        cmd = ['png:-', '-background', bg, '-flatten', '-format', '%[fx:mean.r],%[fx:mean.g],%[fx:mean.b]', 'info:']
        return [float(v) for v in imagemagick(*cmd, data=png).split(b',')]
    for name, digest in SETTINGS_ICONS.items():
        old, new = read('stock.squashfs', xx+name), read('rootfs.squashfs', xx+name)
        assert sha(old) == digest and png_header(old) == (SET_STOCK_ICON, SET_STOCK_ICON, 8, 6), name
        assert changed[xx+name] == dict(original_sha256=digest, sha256=sha(new)) and new == settings_icon(name, old), name
        assert png_header(new) == (SET_ICON, SET_ICON, 8, 6), name
        try: settings_icon(name, old[:-1] + b'x')
        except ValueError: pass
        else: raise AssertionError(f'{name}: accepted a changed icon')
        for bg in ('#000000', '#2160d8'):  # the list, and OLED's selection bar (its Blue scheme's accent)
            assert max(abs(a - b) for a, b in zip(mean(old, bg), mean(new, bg))) < 0.02, (name, bg)
        px = bytearray(imagemagick('png:-', '-depth', '8', 'rgba:-', data=new))
        sat = lambda i: max(px[i:i+3]) - min(px[i:i+3])
        disc = max([1] + [sat(i) for i in range(0, len(px), 4)])
        assert disc > 100, name  # a coloured disc to grey
        grey = inc('MINIMAL_CHEVRON') & 255
        for i in range(0, len(px), 4): px[i:i+3] = bytes([255 - (255 - grey)*min(sat(i), disc)//disc])*3
        runtime = imagemagick('-size', f'{SET_ICON}x{SET_ICON}', '-depth', '8', 'rgba:-', '-define', 'png:color-type=6', 'png:-', data=bytes(px))
        values = mean(runtime, '#000000')  # OLED's black list: neutral grey
        assert max(values) - min(values) < 0.001, name
    # Videos' player: an ELF with the stock binaries' ABI flags (nan2008, o32, mips32r2).
    helper = read('rootfs.squashfs', HELPER)
    assert sha(helper) == manifest['q2video_sha256'] and helper[:4] == b'\x7fELF'
    assert helper[36:40] == read('stock.squashfs', HELPER_LIKE)[36:40]
    # Rockbox's dual boot: q2boot likewise, and S90play differs from stock only in starting demo.
    boot = read('rootfs.squashfs', BOOT)
    assert sha(boot) == manifest['q2boot_sha256'] and boot[:4] == b'\x7fELF' and boot[36:40] == helper[36:40]
    # The boot animation's player (docs/boot.md#boot-animation)
    splash = read('rootfs.squashfs', SPLASH)
    assert sha(splash) == manifest['q2splash_sha256'] and splash[:4] == b'\x7fELF' and splash[36:40] == helper[36:40]
    # Guest Play's daemon and its sink daemon's D-Bus policy (docs/internals.md#guest-play)
    from build import GUEST, GUEST_DBUS, GUEST_HOTSPOT, BT_GUEST_SOURCE
    assert read('rootfs.squashfs', GUEST_HOTSPOT) == (ROOT/'patch/q2_hotspot.sh').read_bytes()
    guest = read('rootfs.squashfs', GUEST)
    assert sha(guest) == manifest['q2_guest_sha256'] and guest[:4] == b'\x7fELF' and guest[36:40] == helper[36:40]
    assert read('rootfs.squashfs', GUEST_DBUS) == (ROOT/'assets/dbus/bluealsa-sink.conf').read_bytes()
    # Car Remote across networks (docs/remote.md#car-remote-across-networks): the pinned q2-iroh (tools/iroh.py), in
    # every image, with the Q2's ABI flags
    from build import IROH
    import iroh
    sidecar = read('rootfs.squashfs', IROH)
    assert sha(sidecar) == manifest['q2_iroh_sha256'] and len(sidecar) == manifest['q2_iroh_bytes']
    assert sidecar[:4] == b'\x7fELF' and sidecar[36:40] == helper[36:40]
    assert (sha(sidecar), len(sidecar)) == iroh.PIN[:2]
    guard = read('rootfs.squashfs', GUARD)
    assert guard[:4] == b'\x7fELF'  # q2guard, which S90play starts (build.py BOOT_HOOK)
    assert b'/q2-wifi.0' in guard and b'CTRL-EVENT-DISCONNECTED' in guard  # the Wi-Fi recorder, release and --dev (patch/q2wifi.c)
    from build import BT_CODEC  # the link monitor's codec switch, which q2guard runs (docs/internals.md#bluetooth-link-monitor)
    codec = read('rootfs.squashfs', BT_CODEC)
    assert codec[:4] == b'\x7fELF' and codec[36:40] == helper[36:40] and b'libdbus-1.so' in codec
    expected_start = read('stock.squashfs', S90PLAY).replace(*BOOT_HOOK)
    if manifest['dev']:
        remote = read('rootfs.squashfs', REMOTE)
        assert sha(remote) == manifest['q2remote_sha256'] and remote[:4] == b'\x7fELF'
        assert remote[36:40] == helper[36:40]
        q2eq = read('rootfs.squashfs', Q2EQ)  # docs/adb.md#q2eq
        assert sha(q2eq) == manifest['q2eq_sha256'] and q2eq[:4] == b'\x7fELF' and q2eq[36:40] == helper[36:40]
        assert b'Q2EQR01' in demo
        usbtrace = read('rootfs.squashfs', USBTRACE)  # docs/internals.md#usb-tracer
        assert sha(usbtrace) == manifest['q2_usbtrace_sha256'] and usbtrace[:4] == b'\x7fELF'
        assert usbtrace[36:40] == helper[36:40]
        assert b'q2-usbtrace-pd17' in demo  # usb_event.c's forwarding of PD17's edges
        expected_start = expected_start.replace(*REMOTE_HOOK).replace(*ADB_HOOK).replace(*USBTRACE_HOOK).replace(*LOG_HOOK).replace(*WHEEL_LOG_HOOK).replace(*GUARD_DEV_HOOK)
        assert b'q2-wheel.log' in demo
        # adbd (docs/adb.md): an ELF with the stock binaries' ABI flags and glibc alone, its gadget script, stock's USB
        # scripts wrapped and kept beside the wrappers
        adbd = read('rootfs.squashfs', ADBD)
        assert sha(adbd) == manifest['adbd']['sha256'] and len(adbd) == manifest['adbd']['bytes'] and adbd[:4] == b'\x7fELF'
        (directory/'adbd-check').write_bytes(adbd)
        dynamic = subprocess.run(['readelf', '-dl', str(directory/'adbd-check')], capture_output=True, text=True, check=True).stdout
        (directory/'adbd-check').unlink()
        assert adbd[36:40] == helper[36:40] and '[Requesting program interpreter: /lib/ld-linux-mipsn8.so.1]' in dynamic
        assert sorted(re.findall(r'\(NEEDED\)\s+Shared library: \[(.+)\]', dynamic)) == ['libc.so.6', 'libpthread.so.0']
        assert read('rootfs.squashfs', ADB_GADGET) == (ROOT/'patch/adb_gadget.sh').read_bytes()
        assert read('rootfs.squashfs', Q2UPDATE) == (ROOT/'patch/q2update.sh').read_bytes()
        for n in USB_SCRIPTS:
            assert read('rootfs.squashfs', 'usr/bin/' + n) == adb_wrapper(n)
            assert read('rootfs.squashfs', 'usr/bin/' + n.replace('.sh', '_stock.sh')) == read('stock.squashfs', 'usr/bin/' + n)
    else:
        assert b'q2-wheel.log' not in demo and b'q2-wheel.log' not in expected_start  # a test build's diagnostic
        assert 'q2remote_sha256' not in manifest and 'q2eq_sha256' not in manifest
        assert not (directory/'q2remote').exists() and not (directory/'q2eq').exists()
        # the USB tracer is --dev only: no binary, no start line, no PD17 forwarding in demo
        assert 'q2_usbtrace_sha256' not in manifest and not (directory/'q2-usbtrace').exists()
        assert b'q2-usbtrace' not in expected_start and b'q2-usbtrace-pd17' not in demo
        for path in (REMOTE, Q2EQ, USBTRACE):
            absent = subprocess.run(['unsquashfs', '-cat', str(directory/'rootfs.squashfs'), path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert absent.returncode != 0, f'release rootfs contains {path}'
        assert b'Q2EQR01' not in demo  # q2eq_poll is compiled out
        # No ADB in a release: no adbd, no gadget, stock's USB scripts as they were
        assert 'adbd' not in manifest and not (directory/'adbd').exists()
        assert b'q2update.req' not in demo  # q2update_poll is compiled out
        for path in (ADBD, ADB_GADGET, Q2UPDATE, *('usr/bin/' + n.replace('.sh', '_stock.sh') for n in USB_SCRIPTS)):
            absent = subprocess.run(['unsquashfs', '-cat', str(directory/'rootfs.squashfs'), path],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            assert absent.returncode != 0, f'release rootfs contains {path}'
        for n in USB_SCRIPTS:
            assert read('rootfs.squashfs', 'usr/bin/' + n) == read('stock.squashfs', 'usr/bin/' + n), f'release {n} is not stock\'s'
    assert read('rootfs.squashfs', S90PLAY) == expected_start
    assert read('rootfs.squashfs', S11LOGO) == read('stock.squashfs', S11LOGO).replace(*LOGO_HOOK)  # the card's logo, if saved
    # keep-alive 5 s, the source role alone while Guest Play is on, and q2-bip started on the line before bluealsa's
    from build import BIP, BT_COVER_START
    assert read('rootfs.squashfs', BLUEALSA2) == read('stock.squashfs', BLUEALSA2).replace(*BT_KEEPALIVE).replace(*BT_GUEST_SOURCE).replace(*BT_COVER_START)
    assert BT_COVER_START[1].startswith(b'q2-bip &\n')
    bip = read('rootfs.squashfs', BIP)  # cover art's responder, with the Q2's ABI flags
    assert bip[:4] == b'\x7fELF' and bip[36:40] == read('rootfs.squashfs', GUEST)[36:40] and b'x-bt/img-thm' in bip
    # No password logs in and no getty runs on the serial pad (docs/building.md#what-the-rootfs-drops).
    from build import SHADOW, INITTAB, SERIAL_GETTY, lock_root, password_logins
    shadow = read('rootfs.squashfs', SHADOW)
    assert password_logins(read('stock.squashfs', SHADOW)) == ['root'] and password_logins(shadow) == []
    assert shadow == lock_root(read('stock.squashfs', SHADOW))
    inittab = read('rootfs.squashfs', INITTAB)
    assert b'getty' not in inittab and inittab == read('stock.squashfs', INITTAB).replace(*SERIAL_GETTY)
    # The debugging and network tools stock ships are gone.
    from build import DEBUG_TOOLS, DEBUG_LINKS
    def paths(image): return {l.removeprefix('squashfs-root/') for l in run('unsquashfs', '-l', directory/image).splitlines()}
    listing, stock_listing = paths('rootfs.squashfs'), paths('stock.squashfs')
    for path in DEBUG_TOOLS + DEBUG_LINKS:
        assert path in stock_listing and path not in listing, path
    print('Removed from the rootfs:', ' '.join(DEBUG_TOOLS + DEBUG_LINKS))
    # Bluetooth is discoverable for 180 s, not for as long as it is on, named Q2, with BlueZ's experimental interfaces.
    from build import BT_MAIN, BT_DISCOVERABLE, BT_NAME, BT_EXPERIMENTAL, BT_GATT_SERVER
    edited = read('stock.squashfs', BT_MAIN).replace(*BT_DISCOVERABLE).replace(*BT_NAME).replace(*BT_EXPERIMENTAL)
    # an --own-bluez build also turns its bluetoothd's GATT server off (docs/bluez.md#the-le-attribute-channel)
    assert read('rootfs.squashfs', BT_MAIN) == (edited.replace(*BT_GATT_SERVER) if 'own_bluez' in manifest else edited)
    assert b'\nExperimental = true\n' in read('rootfs.squashfs', BT_MAIN)
    assert b'\nDiscoverableTimeout = 180\n' in read('rootfs.squashfs', BT_MAIN)
    assert b'\nName = Vamp\n' in read('rootfs.squashfs', BT_MAIN) and b'Shanling' not in read('rootfs.squashfs', BT_MAIN)
    # The boot deletes Baidu Netdisk's saved sign-in.
    assert expected_start.count(b'\n        rm -f /mnt/data/baidu_token.conf\n') == 1
    # bluealsa: AAC capability, AAC's peak and VBR mode, the two DROP jumps, the silence sites, and one appended LOAD payload.
    from build import BT_AAC_SITES, BT_DROP, BT_DROP_HANDLER, BT_DROP_PAYLOAD, BT_SILENCE_SITES, patch_bluealsa, segments
    old, new = read('stock.squashfs', BLUEALSA), read('rootfs.squashfs', BLUEALSA)
    header = segments(old)[-1][0]
    allowed = {AAC_44K1, *range(header, header+32)}
    for address, size in ((BT_DROP, 8), (BT_DROP_HANDLER, 8), *((a, 4) for a, *_ in BT_SILENCE_SITES), *((a, 4) for a, *_ in BT_AAC_SITES)):
        off = fileoff(old, address)
        allowed.update(range(off, off+size))
    assert {i for i in range(len(old)) if old[i] != new[i]} <= allowed
    assert new == patch_bluealsa(old)
    assert segments(new)[-1][1][2] == BT_DROP_PAYLOAD
    assert new[AAC_44K1] == 0 and manifest['bluealsa_sha256'] == sha(new)
    assert all(struct.unpack_from('<I', old, fileoff(old, a))[0] == w0 and struct.unpack_from('<I', new, fileoff(old, a))[0] == w1
               for a, w0, w1 in BT_AAC_SITES)  # the stock words where the build expects them, and the new ones
    from bluealsa import check as check_bluetooth_drop
    check_bluetooth_drop(directory)
    # bluetoothd: the control channel's GetElementAttributes from g_track_meta (tools/bt_meta.c)
    from build import BLUETOOTHD, patch_bluetoothd
    assert read('rootfs.squashfs', BLUETOOTHD) == patch_bluetoothd(read('stock.squashfs', BLUETOOTHD))
    assert manifest['bluetoothd_sha256'] == sha(read('rootfs.squashfs', BLUETOOTHD))
    from bluetoothd import check as check_bluetoothd
    check_bluetoothd(directory)
    from build import SCROBBLE_CA
    assert read('rootfs.squashfs', SCROBBLE_CA) == (ROOT/'assets/scrobble-ca.pem').read_bytes()
    # The whole Mozilla bundle, for stock's HTTPS and the player's.
    from build import STOCK_CA, STOCK_CA_SHA
    assert sha(read('rootfs.squashfs', STOCK_CA)) == STOCK_CA_SHA
    # each subset face ships, cut to what its <NAME>_RANGES lists, with its licence files
    from build import FACES, FONT_DIR, FONT_NOTICES, face_font
    for face in FACES.values():
        if face.source:
            assert read('rootfs.squashfs', face.path) == face_font(face)[0]
    for name in FONT_NOTICES:
        assert read('rootfs.squashfs', FONT_DIR + name) == (ROOT/'assets/fonts'/name).read_bytes()
    # Curly quotes draw the straight glyphs and ♡ takes ♥'s box and advance; other tables keep their
    # stock bytes (head: its checksum).
    from build import QUOTES
    from fontTools.ttLib import TTFont
    old, new = (TTFont(io.BytesIO(read(i, FONT))) for i in ('stock.squashfs', 'rootfs.squashfs'))
    assert all(new.getBestCmap()[c] == old.getBestCmap()[q] for c, q in QUOTES.items())
    full, empty = (new.getBestCmap()[c] for c in (0x2665, 0x2661))
    g = new['glyf']
    assert new['hmtx'][empty] == new['hmtx'][full] == (old['hmtx'][full][0] + 32, g[full].xMin)
    assert abs(g[empty].xMin - g[full].xMin) <= 1 and abs(g[empty].xMax - g[full].xMax) <= 1
    assert abs(g[empty].yMin - g[full].yMin) <= 1 and abs(g[empty].yMax - g[full].yMax) <= 1
    assert all(new.reader[t] == old.reader[t] for t in old.reader.keys() if t not in ('cmap', 'head', 'glyf', 'loca', 'hmtx'))
    # The crash watchdog ends at once, as S90play reboots when demo ends (boot_check); what it did is still stock's:
    # reboot when no /release/bin/demo runs. check_mem_thd returns on entry, so it never drops caches.
    old, new = read('stock.squashfs', WATCHDOG), read('rootfs.squashfs', WATCHDOG)
    assert b'pgrep "/release/bin/demo"' in old and b'reboot' in old and new == WATCHDOG_STUB
    for address, word, patched in DROP_CACHES:
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, off)[0] == word and struct.unpack_from('<I', demo, off)[0] == patched
    # The stream threads' idle polls (usleep, jalr $25) park in tidal.c instead, which is the only way out of them.
    for address, name in STREAM_POLL_CALLS:
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, off)[0] == 0x0320f809
        assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')[name])
    # Stock's status sync sleeps through the dark usleep (playback_power.c), its cover threads through
    # cover_wake.c's waits.
    for address, name in (*DARK_POLLS, *COVER_WAITS):
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, off)[0] == 0x0320f809
        assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')[name])
    # get_battery_capacity's three reads go through system.c's dark pacing (ringnav_batt_level and its two).
    for address, load, word, name in BATTERY_READS:
        off = fileoff(stock, address)
        assert struct.unpack_from('<I', stock, fileoff(stock, load))[0] == word
        assert struct.unpack_from('<I', stock, off)[0] == 0x0320f809
        assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')[name])
    # The encoder thread's ts_read call goes through ringnav_ts_read (playback.c wheel_sample).
    off = fileoff(stock, WHEEL_TS_READ[0])
    assert struct.unpack_from('<I', stock, off)[0] == WHEEL_TS_READ[1]
    assert struct.unpack_from('<I', demo, off)[0] == jal(symbols(directory/'patch.elf')['ringnav_ts_read'])
    # hciplayer's code (.text) differs from stock only in the VBR scan branch, now a nop, the 48 kHz EQ gates,
    # the ID3v2 walker calls, now id3_gain (MP3 ReplayGain), the pause and idle loops' futex waits and their wakes, gapless MP3 allocation/pre-filter/last-frame
    # hooks, the USB DAC buffer's two stores, now nops, the wired unmute calls, now the DAC release wait,
    # q2player is the player, at stock's path, and stock's hciplayer and libmad, its only user, are gone
    assert manifest['q2player_sha256'] == sha(read('rootfs.squashfs', 'usr/bin/hciplayer'))
    rootfs_names = set(run('unsquashfs', '-l', directory/'rootfs.squashfs').splitlines())
    assert not {n for n in rootfs_names if 'libmad' in n or 'hciplayer-stock' in n}, 'stock player files remain'
    # Include every excluded UI screen and saved-preference defaults in byte parity checks.
    paths = [l.removeprefix('squashfs-root/') for l in run('unsquashfs', '-l', directory/'stock.squashfs').splitlines()
             if ('/raw/ui/' in l or '/raw/styles/' in l) and l.endswith('.bin') or l.endswith('/config.ini')]
    names = set(run('unsquashfs', '-l', directory/'rootfs.squashfs').splitlines())
    stock_names = set(run('unsquashfs', '-l', directory/'stock.squashfs').splitlines())
    # The boot animation ships with the default logo only (build.py LOGO_HOOK), with the black splash it starts from
    default = manifest['logo_sha256'] == sha((ROOT/'assets/boot-logo.jpg').read_bytes())
    assert 'squashfs-root/release/assets/default/raw/images/xx/logo-light.jpg' not in names  # no light splash: the boot is dark
    for path, source in ((LOGO_ANIM, 'assets/boot-anim.q2a'), (LOGO_BLACK, 'assets/boot-black.jpg')):
        assert ('squashfs-root/'+path in names) == default and (not default or read('rootfs.squashfs', path) == (ROOT/source).read_bytes())
    assert not {'squashfs-root/'+rel for rel in STOCK_EQ} & names, 'Stock EQ assets remain'
    # Stock's keyboard: its page, style and key images gone, and every call that opened a keyboard window opens the
    # letter strip's (build.py check_keyboard_opens), the stock words replaced by jal kb_host_open
    assert not {'squashfs-root/'+rel for rel in KB_ASSETS} & names and {'squashfs-root/'+rel for rel in KB_ASSETS} <= stock_names
    kb_stock, kb_demo = read('stock.squashfs', 'release/bin/demo'), read('rootfs.squashfs', 'release/bin/demo')
    opens = check_keyboard_opens(kb_stock)
    host = jal(symbols(directory/'patch.elf')['kb_host_open'])
    for address, word in KB_OPEN_SITES:
        assert address in opens and struct.unpack_from('<I', kb_stock, address - 0x400000)[0] == word
        assert struct.unpack_from('<I', kb_demo, address - 0x400000)[0] == host, hex(address)
    assert struct.unpack_from('<I', kb_stock, KB_OPEN_HINT[0] - 0x400000)[0] == KB_OPEN_HINT[1]
    assert struct.unpack_from('<I', kb_demo, KB_OPEN_HINT[0] - 0x400000)[0] == jal(symbols(directory/'patch.elf')['kb_open_hint'])
    # Album Wall, the spectrum and the VU meter: their pages and images are gone, and no page that ships names what opened them
    assert not {'squashfs-root/'+rel for rel in STOCK_PAGES} & names and {'squashfs-root/'+rel for rel in STOCK_PAGES} <= stock_names
    for rel in (n.removeprefix('squashfs-root/') for n in names if '/raw/ui/' in n and n.endswith('.bin')):
        assert not re.search(rb'img_spectrum|img_vu|img_coverflow|label_coverflow', read('rootfs.squashfs', rel)), rel
    # The Library's rows, stock's and list_row_core's alike, draw each local_* icon centred in one 52px
    # box (x 8): each disc's alpha-weighted centre must sit on its image's.
    for rel in sorted(n.removeprefix('squashfs-root/') for n in names if n.startswith('squashfs-root/'+xx+'local_')):
        png = read('rootfs.squashfs', rel)
        w, h = png_header(png)[:2]
        alpha = imagemagick('png:-', '-alpha', 'extract', '-depth', '8', 'gray:-', data=png)
        off = [sum(a*(i % w if axis else i // w) for i, a in enumerate(alpha))/sum(alpha) - ((w if axis else h)-1)/2 for axis in (1, 0)]
        assert max(map(abs, off)) < 0.5, (rel, off)
    # Stock's hidden carousel keeps its images and arrows; none of the payload's cards is added.
    assert {'squashfs-root/release/assets/default/raw/images/xx/'+n for n in ['menu_music.png', 'menu_left.png', 'menu_right.png']} <= names
    assert not {'squashfs-root/release/assets/default/raw/images/xx/'+n for n in ['menu_rockbox.png', 'menu_rockboxdown.png']} & names
    # Colours the firmware will paint (inline style props, else the theme entry for the widget's style):
    # no screen may gain a light background or border, or dark text, that stock did not already paint.
    def palette(path):
        return {(w, s, p): v.hex() for w, s, state, p, _, v in style_props(read(path, 'release/assets/default/raw/styles/default.bin'))
                if state == 'normal' and p in ('bg_color', 'border_color', 'text_color')}
    themes = palette('stock.squashfs'), palette('rootfs.squashfs')
    def painted(root, theme):
        out = set()
        for kind, _, props, _ in walk(root):
            style = props.get('style', 'default')
            for p in ('bg_color', 'border_color', 'text_color'):
                v = props.get('style:normal:'+p, '').lstrip('#').lower() or theme.get((kind, style, p))
                if v: out.add((kind, props.get('name', ''), p, v if len(v) == 8 else v+'ff'))
        return out
    def luma(v): return sum(k*int(v[i:i+2], 16) for k, i in ((0.2126, 0), (0.7152, 2), (0.0722, 4))) / 255
    def jarring(c):
        kind, _, p, v = c
        if int(v[6:], 16) <= 0x40: return False
        return luma(v) < 0.25 if p == 'text_color' else luma(v) > 0.35 and kind != 'image'
    # iPod: text and icons must clear the glass's rounded corners (ipod.CORNER_R); backgrounds,
    # bars and full-bleed art may reach into them. Content is a label's font-high band, an image drawn centred at its
    # size, a slider's bar, else the widget; the window clips it, and row layouts place their children.
    # List rows scroll, so only fixed widgets are checked.
    fonts = {(w, s): int.from_bytes(v, 'little') for w, s, state, p, _, v in style_props(
        read('rootfs.squashfs', 'release/assets/default/raw/styles/default.bin')) if state == 'normal' and p == 'font_size'}
    @functools.cache
    def image_size(name):
        try: return struct.unpack('>II', read('rootfs.squashfs', f'release/assets/default/raw/images/xx/{name}.png')[16:24])
        except subprocess.CalledProcessError: return None
    def content(kind, props, x, y, w, h):
        if kind in ('label', 'hscroll_label'):
            size = int(props.get('style:normal:font_size') or fonts.get((kind, props.get('style', 'default')), 18))
            return x, y if props.get('style:normal:text_align_v') == 'top' else y + (h - size) // 2, w, size
        if kind == 'slider':
            bar = int(props.get('bar_size', h))
            return x, y + (h - bar) // 2, w, bar
        if kind == 'progress_bar': return x, y, w, h
        name = props.get('image') or props.get('style:normal:bg_image')
        if kind not in ('image', 'gif', 'image_animation') or not name: return None
        draw = props.get('draw_type') if 'image' in props else props.get('style:normal:bg_image_draw_type', 'center')
        if draw == 'fill': return None  # full-bleed art, like a background: the glass rounds its corners
        size = image_size(name) if draw == 'center' else None
        return (x + (w - size[0]) // 2, y + (h - size[1]) // 2, *size) if size else (x, y, w, h)
    def corners(short, n, x0, y0, window, slot=None):
        kind, (x, y, w, h), props, children = n
        if props.get('visible') == 'false' or kind in ('list_item', 'table_row'): return  # hidden, or rows that scroll
        # ponytail: Now Playing's A-B markers keep stock's y (window 250, screen 308), inside the glass's bottom curve at the
        # bar's ends, while OLED's bar is at np_bar_y; they show only in A-B repeat.
        if short == PLAYING_PAGE and props.get('name') in ('img_repeata', 'img_repeatb'): return
        x, y, w, h = slot or (x, y, w, h)
        if at := re.match(r'default\(x=(-?\d+),y=(-?\d+),', props.get('self_layout', '')):
            x, y = int(at[1]), int(at[2])
        x, y = x0 + x, y0 + y
        if box := content(kind, props, x, y, w, h):
            bx, by, bw, bh = box
            top, bottom = max(by, window[0]), min(by + bh, window[1])
            inset = max(corner_inset(top), corner_inset(bottom))
            assert bx + bw <= 0 or bx >= 375 or top >= bottom or inset <= bx and min(bx + bw, 375) <= 375 - inset, (
                short, props.get('name'), box, inset)
        slots, row = {}, re.fullmatch(r'default\(r=1,c=0,(a=right,)?xm=(\d+),s=(\d+)\)', props.get('children_layout', ''))
        if row:  # AWTK's row layout, every child shown
            gap, cx = int(row[3]), int(row[2])
            if row[1]: cx = w - cx - sum(c[1][2] for c in children) - gap * (len(children) - 1)
            for c in children:
                slots[id(c)] = (cx, 0, c[1][2], h)
                cx += c[1][2] + gap
        if kind == 'scroll_view':  # its rows past its box scroll in: only what shows inside it is on the glass there
            window = (max(window[0], y), min(window[1], y + h))
        for c in children: corners(short, c, x, y, window, slots.get(id(c)))
    full_bleed_windows = 0
    for rel in paths:
        if rel in STOCK_EQ + STOCK_PAGES + KB_ASSETS: continue
        original, new = read('stock.squashfs', rel), read('rootfs.squashfs', rel)
        if '/raw/ui/' in rel and new[:4] == bytes.fromhex('12122211'):
            now = painted(decode(new), themes[1])
            bad = [c for c in now - painted(decode(original), themes[0]) if jarring(c)
                   and (c[2] != 'text_color' or c[0] in ('label', 'hscroll_label', 'button', 'edit', 'tab_button'))]
            # The rows are transparent, so a light list container stock hid behind them would show.
            bad += [c for c in now if c[2] == 'bg_color' and jarring(c)
                    and c[0] in ('list_view', 'list_item', 'scroll_view', 'table_view', 'table_client', 'view')]
            # The More menus' dialog (tools/ipod.py MODAL_PAGES): the payload clears its fill and border as it frames the
            # sheet (menu_modal.c modal_frame).
            if rel.split('/raw/ui/')[1] in MODAL_PAGES:
                bad = [c for c in bad if c[0] != 'dialog' or c[2] not in ('bg_color', 'border_color')]
            assert not bad, (rel, bad)
        if rel not in changed:
            assert new == (default_config(original) if rel == DEFAULT_CONFIG else original), rel
            continue
        assert changed[rel] == dict(original_sha256=sha(original), sha256=sha(new))
        if '/raw/styles/' in rel:
            audit = AUDIT['styles'][rel.split('/raw/styles/')[1]]
            assert new == patch_style(original, audit) and len(new) == len(original)
            wrong = [*audit['edits'][0][:3], 'ffffffff', *audit['edits'][0][4:]]
            try:
                patch_style(original, dict(audit, edits=[wrong]))
                raise AssertionError('Accepted a wrong old style value')
            except ValueError:
                pass
            continue
        if '/raw/images/' in rel:
            name=rel.rsplit('/',1)[-1]
            if name in STATUS_ICONS:
                assert new == status_icon(name, original)
                assert png_header(new) == (14 if name.startswith('bar_wifi') else 42 if name.startswith('bar_bt') else 12, 16, 8, 6)
            elif name in QUIET_ICONS:
                assert sha(original)==QUIET_ICONS[name] and new==quiet_icon(name,original)
                assert png_header(new)==(28,28,8,6)
            else: assert name in SETTINGS_ICONS and new==settings_icon(name,original)
            continue
        short = rel.split('/raw/ui/')[1]
        edited = patch_asset(short, original, g=GEOM) if short in patched_ui else original
        before_bleed = english_ui(short, edited) if short in chinese else edited
        assert new == full_bleed(before_bleed, GEOM.bar), short
        # Full-bleed (docs/oled-design.md#full-bleed): a window's top-level children sit bar px lower, so they stay on
        # the screen rows they had under the status row; anything else (dialogs, the keyboard) is as it was.
        built, pre = decode(new), decode(before_bleed)
        if pre[0] == 'window':
            assert [c[1][1] for c in built[3]] == [c[1][1] + GEOM.bar for c in pre[3]], short
            full_bleed_windows += 1
        else:
            assert new == before_bleed, short
        if short not in UI_ASSETS:
            continue
        # The checks below are of the transform under OLED's grid; the grid's own measurements, of the pages as built,
        # follow this loop.
        new = edited
        if short in chinese:
            new = english_ui(short, new)
        if short in chinese:  # the checks below are of the edits: compare with stock given in English
            original = english_ui(short, original)
        if short == 'dialog/addplaylist_dialog.bin':
            expected = decode(original)
            expected[3][0][3][0][2]['sensitive'] = 'true'
            assert decode(new) == expected
            continue
        root = decode(new)
        if short in SETTINGS_SLIDERS:
            before = {n[2].get('name'): n for n in walk(decode(original))}
            assert not [n for n in walk(root) if n[0] == 'image' and n[2].get('image')]  # no endpoint glyphs
            gone = [n for n in walk(root) if n[2].get('name') in before and before[n[2]['name']][2].get('image')]
            assert len(gone) == 2 and all(n[1][2:] == [0, 0] and n[1][:2] == before[n[2]['name']][1][:2] for n in gone)
            for n in walk(root):
                if n[0] == 'slider':
                    props = n[2]
                    old = before[props['name']]
                    assert n[1] == old[1] and props['bar_size'] == '8'
                    assert not [k for k in props if k.endswith((':bg_image', ':fg_image', ':icon', ':y_offset'))]
                    assert props['style'] == 's_ipod_progress'
                    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
                        assert props[f'style:{state}:bg_color'] == '#1c1c1c'
                        assert props[f'style:{state}:fg_color'] == '#6e6e6e'
                        assert props[f'style:{state}:round_radius'] == '4'
                        assert props[f'style:{state}:border_color'] == '#00000000'
                    assert {k: v for k, v in props.items() if not k.startswith('style') and k != 'bar_size'} == {
                        k: v for k, v in old[2].items() if not k.startswith('style') and k != 'bar_size'}
                    continue
                if n[2].get('name') in ('img_add', 'img_dec'):
                    assert n == before[n[2]['name']]  # ranges, touch and wheel controls stay stock
        if short == VOLUME:  # Hidden before the first paint, including transition snapshots; no border, paper in Light Mode.
            plain = decode(original); del plain[2]['highlight']
            plain[2].update({f'style:{s}:border_color': '#00000000' for s in ('normal', 'disable', 'focused')})
            for child in plain[3]: child[2]['visible'] = 'false'
            assert root == plain
            continue
        # Browsing, settings and the PEQ editor slide; Home, Now Playing, the bar and dialogs (the More menus among them) don't.
        slides = short not in (HOME_PAGE, STATUS_BAR, PLAYING_PAGE, QUICK_SETTINGS, CONFIRM, BIGCOVER, *MODAL_PAGES)
        assert root[2].get('anim_hint') == (SLIDE if slides else None), short
        # screen coordinates: the bar, full-screen dialogs (quick settings slides down from -320), windows
        origin, window = {STATUS_BAR: (0, (0, GEOM.bar)), QUICK_SETTINGS: (320, (0, 320)), CONFIRM: (0, (0, 320)),
                          BIGCOVER: (0, (0, 320))}.get(short, (GEOM.bar, (GEOM.bar, 320)))
        if short not in AUDIT['slide_only']:  # the PEQ editor's children are the payload's (peq_ui.c destroys stock's)
            corners(short, root, 0, origin, window)
        if short == QUICK_SETTINGS:  # iPod only: the stock 4x2 grid, even label areas, a slim brightness track
            menu, light = root[3]
            icons = [n for n in menu[3] if n[0] == 'image']; labels = [n for n in menu[3] if n[0] == 'label']
            assert len(icons) == len(labels) == 8 and menu[1][1] >= QS_TOP
            assert sorted({n[1][0] for n in icons}) == sorted({n[1][0] for n in decode(original)[3][0][3] if n[0] == 'image'})
            for icon, label in zip(icons, labels):
                x, y, w, h = icon[1]
                # The same 16px two-line area, top-aligned, centred under its icon and clear of the next row.
                assert label[1] == [x + (w - QS_LABEL_W) // 2, y + h + QS_LABEL_GAP, QS_LABEL_W, QS_LABEL_H]
                assert label[2]['style'] == 's_label_white16c' and label[2]['style:normal:text_align_v'] == 'top'
                assert 2 * (16 + 2) <= QS_LABEL_H and label[2]['line_wrap'] == label[2]['word_wrap'] == 'true'
            assert {n[1][1] for n in icons} == {0, QS_PITCH}
            slider, dim, bright = light[3]
            assert light[1][1] == menu[1][1] + menu[1][3] + QS_ROW_GAP and light[1][1] + light[1][3] <= 320 - 16
            assert slider[2]['bar_size'] == str(QS_BAR) and slider[1][3] == QS_TOUCH >= 44 and slider[2]['slide_with_bar'] == 'true'
            assert not [k for k in slider[2] if k.endswith((':bg_image', ':fg_image', ':icon'))]
            assert slider[2]['style'].encode() not in read('rootfs.squashfs', 'release/assets/default/raw/styles/default.bin')
            assert dim[1][0] == QS_EDGE and bright[1][0] + bright[1][2] == 375 - QS_EDGE and dim[2]['image'] == 'drop_lighleft'
            assert dim[1][0] + QS_SUN < slider[1][0] and slider[1][0] + slider[1][2] < bright[1][0]
            continue
        if short == CONFIRM:  # iPod only: the stock pair as two full-width text rows under the prompt
            cancel, enter = root[3]
            assert [n[0] for n in root[3]] == ['button', 'button'] and [n[2]['name'] for n in root[3]] == ['img_cancel', 'img_enter']
            assert cancel[1] == [0, 208, 375, 48] and enter[1] == [0, 256, 375, 48] and enter[1][1] + enter[1][3] <= 320
            assert [n[2]['text'] for n in root[3]] == ['Cancel', 'OK']
            continue
        if short == BIGCOVER:  # iPod only: a centred square of art clear of the glass (corners above), the name on its foot
            art, shade, title = root[3]
            x, y = (375 - BC_ART) // 2, (320 - BC_ART) // 2
            assert root[2] == decode(original)[2] and art[2]['image'] == 'default_bigcover' and art[2]['draw_type'] == 'scale_auto'
            assert art[1] == [x, y, BC_ART, BC_ART] and art[2]['self_layout'] == f'default(x={x},y={y},w={BC_ART},h={BC_ART})'
            assert abs((375 - x - BC_ART) - x) <= 1 and 320 - y - BC_ART == y and BC_ART > 280  # centred, and still most of the height
            assert (375 - BC_ART - 2) // 2 < corner_x((320 - BC_ART - 2) // 2, BC_ART + 2)  # the largest that clears
            assert shade[1] == [x, y + BC_ART - 50, BC_ART, 50] and shade[2] == decode(original)[3][1][2]
            assert title[1] == [x + BC_TITLE_PAD, y + BC_ART - 50 + 11, BC_ART - 2 * BC_TITLE_PAD, 28] and title[2] == decode(original)[3][2][2]
            continue
        if short == HOME_PAGE:  # seven rows with the stock names, beside the art; bytes equal patch_asset above
            art, (lv, lg, _, [sv]), (sl, sg, sp, [ss]), *carousel = root[3]
            assert lv == 'list_view' and sv[0] == 'scroll_view' and art[2]['name'] == 'img_homeart'
            # The Settings list: eight rows, stock's Playback and System setting rows among them (stock binds them; their
            # labels are literal), hidden in the list's place until Home's Settings row slides it in.
            assert sl == 'list_view' and sp['name'] == 'list_view_homeset' and sp['visible'] == 'false'
            assert sg == [0, HOME_TOP, HOME_LIST_W, 320 - GEOM.bar - HOME_TOP] and [r[2]['name'] for r in ss[3]] == ['btn_'+n for n in HOME_SETS]
            assert [r[3][1][2]['name'] for r in ss[3]][2:4] == ['img_sysset', 'img_playset'] and [r[3][0][2]['text'] for r in ss[3]][2:4] == ['General', 'Playback']
            # The art fills the right panel below the status bar; the payload fits and crops it.
            assert art[0] == 'image' and art[1] == [0, 0, 375, 320 - GEOM.bar] and art[2]['draw_type'] == 'fill' and HOME_LIST_W == 375
            assert all(v == '#00000000' for k,v in root[3][1][2].items() if k.startswith('style:'))
            assert [r[2]['name'] for r in sv[3]] == ['btn_'+n for n in HOME_ROWS] and HOME_ROWS[5] == 'coverflow'
            for name, (_, _, _, (label, image)) in zip(HOME_ROWS, sv[3]):
                ours = HOME_OURS.get(name, name)  # stock V1.36 binds img_coverflow and translates label_coverflow: ours are named apart
                assert label[2]['name'] == {'localmusic': 'label_library', 'folder': 'label_folders'}.get(name, 'label_'+ours)
                assert image[2] == {'name': 'img_'+ours, 'clickable': 'true'}
                # Labels end before the chevron's glyph; longer ones scroll (only_focus) or end in an ellipsis.
                assert label[1][0] + label[1][2] == image[1][2] - HOME_LABEL_END and label[1][2] >= 100
                assert label[2]['only_focus'] == 'true' and label[2]['ellipses'] == 'true'
            assert [r[3][0][2].get('text') for r in sv[3]] == [None, 'Library', 'Radio', 'Games', 'Folders', 'Cover Flow', 'Search',
                                                              'Settings']
            # Seven rows, all in the box at rest (HOME_REST): the payload would scroll any more (home.c home_overflow).
            assert lg == [0, HOME_TOP, HOME_LIST_W, 7*GEOM.home_row] and HOME_TOP + 7*GEOM.home_row <= 320 - GEOM.bar - HOME_TOP
            # Stock's carousel and arrows after the lists, hidden and taking no taps (tools/ipod.py ipod_home: left
            # for home_page_init's visitor, never shown), its cards stock's but the Album Wall's
            stock_root = decode(original)
            assert [n[2]['name'] for n in carousel] == ['slide_menu', 'img_left', 'img_right']
            for n, old in zip(carousel, stock_root[3]):
                assert n[:2] == old[:2] and n[2] == {**old[2], 'visible': 'false', 'sensitive': 'false'}
            cards = stock_root[3][0][3]
            assert carousel[0][3] == cards[:2] + cards[3:] and carousel[1:] == [[*n[:2], n[2] | {'visible': 'false', 'sensitive': 'false'}, n[3]] for n in stock_root[3][1:3]]
            dot = inc('HOME_DOT_X'), inc('HOME_DOT')
            assert HOME_TEXT_X == sum(dot) + inc('HOME_DOT_GAP')
            # The dot and the labels clear the bottom row's corner.
            row = GEOM.home_row
            assert dot[0] >= corner_x(GEOM.bar + HOME_TOP + 6*row + (row - dot[1])//2, dot[1])
            assert HOME_TEXT_X >= corner_x(GEOM.bar + HOME_TOP + 6*row + (row - 20)//2, 20)
            # Split (coverflow_home_layout) gives the menu half the screen, the art the rest;
            # Split's glyph ends 13px before the art, as in the Home concept.
            assert inc('HOME_SPLIT_W') == 375 // 2
            continue
        if short == STATUS_BAR:  # the status row (tools/ipod.py status_row): the battery right, the rest off-screen for stock
            left, right, *rest = root[3]
            assert all(root[2][f'style:{s}:border_color'] == '#00000000' for s in ('normal', 'disable', 'focused'))  # Light Mode: no paper line
            assert left[3] == [] and [n[2]['name'] for n in right[3]] == STATUS_ROW_RIGHT
            shown = {n[2]['name'] for n in rest if n[1][0] >= 0}
            assert shown == {'label_title', 'label_clock'}, shown  # the payload places both (navigation.c clock_place)
            # The Battery setting's percentage and payload battery start hidden (navigation.c bar_sync).
            pct, slot = right[3][:2]
            assert pct[1][2] == inc('BATT_PCT_W') and pct[2]['visible'] == 'false' and pct[2]['style:normal:text_align_h'] == 'right' and pct[2]['style:normal:font_size'] == str(inc('BATT_PCT_PX'))
            assert slot == ['view', [0, 0, inc('BATT_BODY_W') + inc('BATT_NUB_W') + inc('BATT_GAP') + inc('BATT_PCT_W'), 0], {'name': 'view_battery', 'visible': 'false'}, []]
            continue
        if short == PLAYING_PAGE:  # OLED's grid (tools/ipod.py np_grid; its boxes are test/unit/test_ipod.py's)
            named = {n[2].get('name'): n for n in walk(root)}
            # stock names kept, but the two buttons that open its spectrum and VU pages, which Vamp does not offer
            assert {n[2].get('name') for n in walk(decode(original))} - set(named) == {'img_spectrum', 'img_vu'}
            # the info page first and hidden: out of the swipe, for ⋯'s About (np_about.c); the play mode's target last
            assert [n[2].get('name') for n in root[3]] == ['view_info', 'view_buttons', 'label_playtime', 'label_playlen', 'label_ipod_remain',
                                                           'slide_view_view', 'slider_play', 'img_repeata', 'img_repeatb', 'image_wait',
                                                           'img_playmode']
            assert root[3][0][2]['visible'] == 'false' and 'label_ipod_pos' not in named
            # the swipe's pages: the art, the lyrics, then the rating page (np_rate.c)
            assert [n[2]['name'] for n in named['slide_view'][3]] == ['view_album', 'view_lyric', 'view_rate']
            assert named['img_return'][1][0] < 0 and [n[2].get('name') for n in named['view_buttons'][3]] == ['img_return']
            album = named['view_album'][3]
            assert [n[2]['name'] for n in album] == ['img_cover', 'img_playstate', 'scrlabel_title', 'scrlabel_artist', 'label_ipod_album',
                                                     'img_fav', 'img_more']
            assert album[1][2]['opacity'] == '0'  # playback gestures retain their original target
            # The lyrics page (patch/lyrics.c): its rows span the page, a LYRIC_SLOT each after a blank one.
            lrc, slot, page_h = named['view_lrc'][3][0][2], inc('LYRIC_SLOT'), named['view_lrc'][1][3]
            assert named['view_lrc'][2]['self_layout'] == f'default(x=0,y=0,w=375,h={page_h})'
            assert lrc['self_layout'] == 'default(x=0,y=0,w=100%,h=100%)' and lrc['children_layout'] == f'list_view(x=0,y={slot},s=0)'
            slider = named['slider_play']
            assert slider[2]['slide_with_bar'] == 'true' and not [k for k in slider[2] if k.endswith((':bg_image', ':fg_image', ':icon'))]
            assert all(slider[2][f'style:{s}:border_color'] == '#00000000' for s in ('normal','pressed','over','disable','focused'))
            # No theme style of that name, so no thumb icon: stock fills exactly to the value.
            assert slider[2]['style'].encode() not in read('rootfs.squashfs', 'release/assets/default/raw/styles/default.bin')
            assert named['label_playlen'][2]['visible'] == 'false'
            # Stock places the A-B markers at y 250 and x = 50 + t * 290 / length; the patched words follow np_bar_x and np_bar_w.
            values = immediate_values(GEOM)
            assert [int.from_bytes(demo[fileoff(demo, a):fileoff(demo, a)+4], 'little') for a in (0x53ba88, 0x53baa0)] == [
                0x24020000 | values['np_bar_w'], 0x24420000 | values['np_bar_x']]
            continue
        if short == ARTIST_PAGE:
            assert [n[2]['value'] for n in walk(root) if n[0] == 'pages'] == ['1'], 'Artist page must show Albums'
        if short in AUDIT['slide_only'] or short in TOOLBAR_SWITCHES and short in KEEP_TOOLBAR:
            # Only the hint and settings slider styling change.
            root[2].pop('anim_hint')
            kept = decode(original)
            slider_plain(short, kept)
            assert root == kept, short
            continue
        nav = next(n for n in root[3] if n[2].get('name') == 'view_navbar')
        if short in NAVBAR_ONLY:  # content moves up 50 (the Sleep Timer's under its switch's row); lists take the grid's settings rows
            for old, node in zip(decode(original)[3], root[3]):
                if node is nav: continue
                if node[0] == 'list_view':  # its box: the grid's checks after this loop
                    assert node[2] == {**old[2], 'default_item_height': str(GEOM.set_row)} and node[3] == old[3], short
                    continue
                assert node[1][1] == old[1][1] - 50 + (GEOM.set_row if short == SLEEP_TIMER else 0) and node[2] == old[2], short
        assert nav[2]['visible'] == 'false' and nav[2]['enable'] == 'false'
        assert not [v for n in walk(root) if n[0] in ('button', 'list_item', 'table_row')
                    for k, v in n[2].items() if k.endswith(':bg_color') and v == '#000000'], 'Opaque inline row background'
        old_nodes, new_nodes = list(walk(decode(original))), list(walk(root))
        assert len(old_nodes) == len(new_nodes)
        for old, node in zip(old_nodes, new_nodes):
            for key, value in old[2].items():
                if short in SETTINGS_SLIDERS and node[0] == 'slider' and key == 'style':
                    value = 's_ipod_progress'
                if 'font' in key or key == 'style': assert node[2][key] == value
        if short in ('folder_page.bin', 'localmusic_page.bin', 'localmusic/localclass_page.bin', 'localmusic/playlist_page.bin'):
            surface = next(n for n in root[3] if n[0] in ('list_view', 'table_view'))
            assert surface[1][1:] == [0, 375, 320 - GEOM.bar], 'Lists must fill the page from the status row'
        # Corrupt inputs must be rejected, never silently patched.
        try: patch_asset(short, original[:-1]+b'x', GEOM)
        except ValueError: pass
        else: raise AssertionError('Accepted a changed asset')
    # every window the image ships went through the shift above (a skipped asset would lower the count); the two stock
    # windows patch_asset makes dialogs (MODAL_PAGES) keep their boxes
    assert full_bleed_windows == sum(decode(read('rootfs.squashfs', ui+p))[0] == 'window' for p in kept_ui) > 0, full_bleed_windows

    def built_page(short):
        """A built UI asset in the rows of a window under the status row: the full-bleed shift (proven exact above)
        taken off its top-level children, so the measurements below read as the grid defines them."""
        root = decode(read('rootfs.squashfs', ui + short))
        if root[0] == 'window':
            for child in root[3]:
                child[1][1] -= GEOM.bar
        return root
    # The built list windows take the row height of the geometry set's pitch immediates (art pages take art_pitch),
    # and its rows fit.
    from theme import geometry
    from ipod import PAGE_KIND as page_kind, page_rows, fit_rows
    # With no toolbars the Playlists rows (pl_row) are the theme's page_detail high in a list pitched at the grid's playlist
    # pitch (page_rows): one height.
    for t in builtins():
        g = geometry(t)
        assert page_rows(g, 'playlist')[0] == t['page_detail'], (t['name'], g.name)
    for g in [GEOM]:
        assert g.body <= g.pitch and g.art_body <= g.art_pitch and g.song_body <= g.song_pitch, g.name
        assert g.art + 2*g.art_inset <= g.art_body, g.name
        assert g.text_art + 2*g.text_art_inset <= g.body or not g.text_art, g.name   # text rows' artwork fits, or none
        for y, h, body in ((g.title24_y, 24, g.body), (g.title_y, 20, g.body), (g.meta_y, 16, g.body), (g.count_y, 16, g.body),
                           (g.art_title24_y, 24, g.art_body), (g.art_meta_y, 16, g.art_body),
                           (g.song_title24_y, 24, g.song_body), (g.song_meta_y, 16, g.song_body)):
            assert 0 <= y and y + h <= body, (g.name, y, h, body)                    # every stock row label inside its body
        checked = set()
        for short, kind in page_kind.items():
            new = decode(read('rootfs.squashfs', ui + short))
            old = {n[2].get('name'): n[2] for n in walk(decode(read('stock.squashfs', ui + short)))}
            for n in walk(new):
                for prop in ('row_height', 'default_item_height'):
                    if old.get(n[2].get('name'), {}).get(prop) == '78':
                        assert n[2][prop] == str(page_rows(g, kind)[0]), (g.name, short, prop)
                        checked.add(short)
        assert checked == set(page_kind) - {'localmusic/album_page.bin'}, checked  # album_page's grid keeps 160
        # Playlists' toolbar hides, stock's widgets alive for its rows' clicks, its list takes the bar's room with rows of
        # their own heights (no item_height forces them); More's rows start at the top.
        for short in ('localmusic/playlist_page.bin', 'localmusic/playlistsmore_page.bin'):
            new = built_page(short)
            nav = next(n for n in new[3] if n[2].get('name') == 'view_navbar')
            assert nav[2]['visible'] == 'false' and nav[2]['enable'] == 'false', short
            if short == 'localmusic/playlist_page.bin':
                surface = next(n for n in new[3] if n[0] == 'list_view')
                assert surface[1][1] == 0 and surface[2]['default_item_height'] == str(page_rows(g, 'playlist')[0]) and 'item_height' not in surface[2]
            else:
                assert [n[1][1] for n in new[3] if n[0] == 'list_item'] == [0, g.pitch]
        assert g.set_top + g.set_rows*g.set_row <= 320 - g.bar, g.name
        # Home's rows are the set's and fit.
        listed = {n[2].get('name'): n for n in walk(built_page(HOME_PAGE))}['list_view_home']
        assert listed[2]['item_height'] == str(g.home_row) and HOME_TOP + 7*g.home_row <= 320 - g.bar, g.name
        for rel in UI_ASSETS:
            for n in walk(built_page(rel)):
                if n[0] != 'list_view' or n[2].get('default_item_height') != str(g.set_row): continue
                if rel in NAVBAR_ONLY:  # settings lists: the grid's rows from its top, on to the window's foot
                    assert n[1] == [0, g.set_top, 375, fit_rows(g, g.set_top, g.set_row)] and 'item_height' not in n[2], (g.name, rel)
                else:                   # any other list with that row height is not one (settings_list)
                    assert n[2].get('item_height') == str(g.set_row), (g.name, rel)
        # text from text_x clears the glass at the first row and at list_end's last; the lists take the window
        assert g.text_x >= corner_x(g.bar + 6, g.pitch - 12) and g.list_end % g.pitch == 0, g.name
        assert corner_x(g.bar + g.list_end - g.pitch + 6, g.pitch - 12) <= g.text_x < corner_x(g.bar + g.list_end + 6, g.pitch - 12), g.name
        ends = {}
        for rel in UI_ASSETS:  # every list box the transform resizes ends at the window's end
            if rel in NAVBAR_ONLY: continue
            was = {n[2].get('name'): n[1] for n in decode(read('stock.squashfs', ui + rel))[3]}
            for n in built_page(rel)[3]:
                old = was.get(n[2].get('name'))
                if n[0] in ('list_view', 'table_view') and old and old[1] in (50, 100) and n[1] != old:
                    ends[rel] = n[1][1] + n[1][3], n[1][3], page_rows(g, page_kind.get(rel, 'text'))[0]
        # on to the window's end: its rows past list_end fade (navigation.c list_fade)
        assert ends and {end for end, _, _ in ends.values()} == {320 - g.bar}, (g.name, ends)
        # the status row: its title's text and its battery's nub STATUS_ROW_INSET in, clear of the glass at its line's top
        bar = {n[2].get('name'): n for n in walk(decode(read('rootfs.squashfs', ui + 'system_bar.bin')))}
        inset, top = inc('STATUS_ROW_INSET'), g.status_y - STATUS_ROW_LINE // 2
        assert inset >= corner_x(top, STATUS_ROW_LINE) and bar['label_title'][1][:2] == [inset - 2, top], g.name
        assert bar['view_right'][2]['children_layout'].endswith(f'xm={inset},s={STATUS_ROW_GAP})'), g.name
    payload_symbols = symbols(directory/'patch.elf')
    for address in [AUDIT['immediates'][0]['sites'][0][0], '0x532d3c', '0x534cf8', '0x4b5874', AUDIT['row_layout_calls']['sites'][0][0],
                    hex(inc('LIST_VIEW_LAYOUT_SLOT'))]:
        damaged = bytearray(stock)
        damaged[fileoff(stock, int(address, 16))] ^= 1
        try: patch_code(damaged, payload_symbols, GEOM)
        except ValueError: pass
        else: raise AssertionError(f'Accepted a changed instruction at {address}')
    print('Asset geometry, exclusion parity and mismatch rejection passed.')

if __name__ == '__main__':
    import argparse, json, pathlib, subprocess, tempfile
    from unittest.mock import patch
    from build import build, run, sha
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('zip',type=pathlib.Path,nargs='?')
    ap.add_argument('--build', type=pathlib.Path)
    args=ap.parse_args()
    if args.build: validate_assets(args.build)
    if args.zip:
        # Reproducibility and update.tar's MD5s are release.py package's; this covers the logo and quoting.
        with tempfile.TemporaryDirectory(prefix='q2-package-') as tmp:
            root=pathlib.Path(tmp)
            custom=root/"custom ' logo.jpg"
            custom.write_bytes(logo)
            def change_source(*command):
                if command[0]=='mksquashfs': custom.write_bytes(b'edited during compression')
                return run(*command)
            a=root/"build ' a"
            with patch('build.run',side_effect=change_source):
                build(args.zip,a,custom)
            validate_assets(a)
            manifest=json.loads((a/'manifest.json').read_text())
            assert manifest['logo_sha256']==sha(logo)
            for rel,expected in [('release/assets/default/raw/images/xx/logo.jpg',logo),
                                 ('release/bin/demo',(a/'demo').read_bytes())]:
                assert subprocess.check_output(['unsquashfs','-cat',str(a/'rootfs.squashfs'),rel])==expected
            print('Packaging, mutable logo and quoted paths checks passed.')
