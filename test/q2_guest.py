#!/usr/bin/env python3
"""q2-guest (patch/q2_guest.c, docs/internals.md#guest-play) on this machine, against stand-ins for bluetoothd,
the main bluealsa daemon and the guest's (test/guest/fake_bluez.py) on a private D-Bus bus, with stand-in
bluealsa and bluealsa-aplay programs that log how they were started. It runs the contract a UI relies on: the
state file's keys, each command, Let a Guest Connect and its pairing agent, the prompts and their 30 s answers
(shortened here), Replace, Trusted Guests, the relay's processes, the guest's song to the car through
shanling.bluez (byte for byte as libbtctl builds it) and the car's keys to the guest's phone.

Needs cc, dbus-daemon, the host's libdbus-1.so.3, and dbus-python with PyGObject for /usr/bin/python3; no build,
no stock ZIP."""
import os
import re
import pathlib
import struct
import subprocess as S
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
CC = ['cc', '-Wall', '-Wextra', '-Werror']
SOURCE = pathlib.Path(os.environ.get('Q2GUEST_SRC') or ROOT / 'patch/q2_guest.c')  # another copy, to show a gap fails
PHONE, SAM, CAR = 'AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02', '11:22:33:44:55:66'
SBC_48K = '11150235'  # 48 kHz joint stereo, 16 blocks, 8 subbands, bit pool 2 to 53
AAC_256K = '40010483e800'  # MPEG-4 AAC LC, 44.1 kHz, stereo, VBR up to 256,000 bit/s (octets 3 to 5, less VBR's bit)


def fails(what):
    print('FAIL ' + what)
    sys.exit(1)


def check(what, ok):
    if not ok:
        fails(what)
    print('ok   ' + what)


def sbc_kbps(rate, blocks, subbands, pool, joint=True):
    """A2DP's SBC frame length (the spec's formula for joint stereo) as a bit rate."""
    frame = 4 + 4 * subbands * 2 // 8 + -(-((subbands if joint else 0) + blocks * pool) // 8)
    return round(8 * frame * rate / (subbands * blocks) / 1000)


class Rig:
    procs: list = []

    def __init__(self, d):
        self.d = d
        (d / 'bin').mkdir()
        (d / 'data').mkdir()
        self.log = d / 'events.log'
        self.log.touch()
        self.at = 0
        conf = d / 'bus.conf'
        conf.write_text(f'''<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path={d}/bus</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*"/><allow receive_sender="*"/><allow own="*"/>
<allow user="*"/></policy></busconfig>''')
        self.procs.append(S.Popen(['dbus-daemon', f'--config-file={conf}', '--nofork', '--nopidfile']))
        self.env = {**os.environ, 'DBUS_SYSTEM_BUS_ADDRESS': f'unix:path={d}/bus'}
        self.wait(lambda: (d / 'bus').exists(), 'the bus is up')
        fake = ROOT / 'test/guest/fake_bluez.py'
        for role in ('main', 'sink'):
            os.mkfifo(d / f'{role}.ctl')
        (d / 'bin/bluealsa').write_text(f'#!/bin/sh\necho "SINKD $*" >> {self.log}\n'
                                        f'exec /usr/bin/python3 {fake} sink {d}/sink.ctl {self.log}\n')
        (d / 'bin/bluealsa-aplay').write_text(f'#!/bin/sh\necho "APLAY $*" >> {self.log}\n'
                                              f"trap 'echo APLAY-END >> {self.log}; exit 0' TERM\n"
                                              'while :; do sleep 0.1; done\n')
        (d / 'bin/q2-hotspot').write_text(f'#!/bin/sh\necho "HOTSPOT $*" >> {self.log}\n'
                                          '[ "$1" = check ] && echo 2\nexit 0\n')
        for n in ('bluealsa', 'bluealsa-aplay', 'q2-hotspot'):
            (d / 'bin' / n).chmod(0o755)
        self.helper = d / 'q2-guest'
        defs = {'QG_RUN': f'"{d}/run"', 'QG_DATA': f'"{d}/data"', 'QG_BIN': f'"{d}/bin/"', 'QG_ASK_S': '2',
                'QG_GIVEUP_MS': '3000', 'QG_ARRIVE_MS': '3000', 'QG_OUT_WAIT_MS': '1500',
                'QG_MAIN_SOURCE': f'"echo MAIN source >> {self.log}"', 'QG_MAIN_STOCK': f'"echo MAIN stock >> {self.log}"',
                'QG_HOTSPOT': f'"{d}/bin/q2-hotspot"', 'QG_HOTSPOT_CHECK_MS': '1000', 'QG_SPOT': f'"{d}/spot"'}
        S.run(CC + ['-DCODA_HOST', *(f'-D{k}={v}' for k, v in defs.items()), '-o', str(self.helper),
                    str(SOURCE), '-I', str(ROOT / 'patch'), '-l:libdbus-1.so.3'], check=True)
        self.main = S.Popen(['/usr/bin/python3', str(fake), 'main', str(d / 'main.ctl'), str(self.log)], env=self.env)
        self.procs.append(self.main)
        self.expect('UP main')
        self.ctl = {r: open(d / f'{r}.ctl', 'w') for r in ('main',)}

    def wait(self, pred, what, timeout=6.0):
        end = time.time() + timeout
        while time.time() < end:
            got = pred()
            if got:
                return got
            time.sleep(0.05)
        print('\n'.join(self.lines()[-12:]) if self.log.exists() else '')  # what the stand-ins saw last
        fails(what + ' (timed out)')

    def lines(self):
        return self.log.read_text().splitlines()

    def expect(self, start, what=None):
        """The next log line from here on that starts with start; later lines stay unread."""
        def find():
            ls = self.lines()
            for i in range(self.at, len(ls)):
                if ls[i].startswith(start):
                    self.at = i + 1
                    return ls[i]
            return None
        line = self.wait(find, what or f'log line {start!r}')
        if what:
            print('ok   ' + what)
        return line

    def seen(self, start):
        """A log line from here on starts with start (cursor kept)."""
        return any(l.startswith(start) for l in self.lines()[self.at:])

    def mark(self):
        self.at = len(self.lines())

    def send(self, role, line):
        if role == 'sink' and role not in self.ctl:
            self.ctl[role] = open(self.d / 'sink.ctl', 'w')
        self.ctl[role].write(line + '\n')
        self.ctl[role].flush()
        self.expect('DONE ' + line)

    def state(self):
        try:
            raw = (self.d / 'run.state').read_text()
        except FileNotFoundError:
            return {}
        return dict(l.split('=', 1) for l in raw.splitlines() if '=' in l)

    def until(self, what, **want):
        got = self.wait(lambda: (lambda s: s if all(s.get(k) == v for k, v in want.items()) else None)(self.state()),
                        what + f' {want} (state was {self.state()})')
        print('ok   ' + what)
        return got

    def cmd(self, line):
        fd = os.open(self.d / 'run.cmd', os.O_WRONLY | os.O_NONBLOCK)
        os.write(fd, (line + '\n').encode())
        os.close(fd)

    def start_helper(self, *args):
        self.q2 = S.Popen([str(self.helper), *args], env=self.env)
        self.procs.append(self.q2)

    def close(self):
        for p in reversed(self.procs):
            p.kill()
            p.wait()
        S.run(['pkill', '-f', f'{self.d}/'], check=False)  # the sink stand-in and aplay, children of the helper


def track_record(track, tracks, ms, title, artist='', album='', genre=''):
    """libbtctl's btctl_notify_track_changed record, as its disassembly (0x2a3b8) builds it."""
    rest = [(i, s) for i, s in ((2, artist), (3, album), (6, genre)) if s]
    b = struct.pack('<4I', track, tracks, ms, 1 + len(rest))
    t = title.encode() + b'\0'
    b += struct.pack('<HH', 1, len(t)) + t
    for i, s in rest:
        s = s.encode() + b'\0'
        b += struct.pack('<BBH', i, 0, len(s)) + s
    return b.hex()


def hotspot_script(d):
    """patch/q2_hotspot.sh under the host's sh with stand-in wpa_cli, wpa_supplicant, ifconfig and udhcpd."""
    b, hs, log = d / 'hsbin', d / 'hs', d / 'hs.log'
    b.mkdir()
    stand_ins = {
        'wpa_cli': f'echo "wpa_cli $*" >> {log}\ncase "$*" in *wlan0*status*) [ -s {d}/freq ] && echo "freq=$(cat {d}/freq)"; '
                   'echo wpa_state=COMPLETED;; *all_sta*) printf "aa:bb:cc:00:00:01\\nflags=x\\nde:ad:be:ef:00:02\\n";; esac',
        # its "daemon" a sleep of its own, so stop's kill reaches nothing else on this machine
        'wpa_supplicant': f'echo "wpa_supplicant $*" >> {log}\nsleep 30 & while [ $# -gt 0 ]; do [ "$1" = -P ] && echo $! > "$2"; '
                          'shift; done',
        'ifconfig': f'echo "ifconfig $*" >> {log}', 'udhcpd': f'echo "udhcpd $*" >> {log}'}
    for n, body in stand_ins.items():
        (b / n).write_text('#!/bin/sh\n' + body + '\n')
        (b / n).chmod(0o755)
    env = {'PATH': f'{b}:/usr/bin:/bin', 'Q2_HOTSPOT_DIR': str(hs)}
    script = str(ROOT / 'patch/q2_hotspot.sh')

    def run(*args):
        return S.run(['sh', script, *args], env=env, capture_output=True, text=True)
    (d / 'freq').write_text('5180\n')
    ssid = 'Car Q2'.encode().hex()
    check('start: exits 0', run('start', ssid, 'tulip-river-42').returncode == 0)
    conf = (hs / 'ap.conf').read_text()
    for want in (f'ssid={ssid}\n', 'mode=2\n', 'frequency=5180\n', 'key_mgmt=WPA-PSK\n', 'proto=RSN\n', 'pairwise=CCMP\n',
                 'psk="tulip-river-42"\n'):
        check(f'... the access point\'s settings hold {want.strip()} (wlan0\'s channel)', want in conf)
    check('... readable by root alone (the password)', (hs / 'ap.conf').stat().st_mode & 0o077 == 0)
    calls = log.read_text()
    check('... wlan1 addressed and up', 'ifconfig wlan1 192.168.43.1 netmask 255.255.255.0 up' in calls)
    check('... wpa_supplicant in the background on wlan1 over nl80211',
          f'wpa_supplicant -B -i wlan1 -D nl80211 -c {hs}/ap.conf -P {hs}/ap.pid' in calls)
    udhcpd = (hs / 'udhcpd.conf').read_text()
    check('... udhcpd on wlan1, .20 to .50', f'udhcpd {hs}/udhcpd.conf' in calls and 'interface wlan1' in udhcpd
          and 'start 192.168.43.20' in udhcpd and 'end 192.168.43.50' in udhcpd)
    log.write_text('')
    got = run('check')
    check('check: two stations joined, counted', got.stdout.strip() == '2' and 'wpa_supplicant' not in log.read_text())
    (d / 'freq').write_text('2412\n')
    got = run('check')
    check('... wlan0 moved channel: started again on it', 'frequency=2412\n' in (hs / 'ap.conf').read_text()
          and f'ssid={ssid}\n' in (hs / 'ap.conf').read_text() and 'psk="tulip-river-42"' in (hs / 'ap.conf').read_text()
          and 'wpa_supplicant -B' in log.read_text())
    (d / 'freq').unlink()
    run('start', ssid, 'tulip-river-42')
    check('wlan0 not connected: channel 6', 'frequency=2437\n' in (hs / 'ap.conf').read_text())
    log.write_text('')
    ap = int((hs / 'ap.pid').read_text())
    check('stop: exits 0', run('stop').returncode == 0)
    time.sleep(0.2)
    check('... the access point killed', not pathlib.Path(f'/proc/{ap}').exists()
          or pathlib.Path(f'/proc/{ap}/stat').read_text().split()[2] == 'Z')
    check('... wlan1 down, the pid files gone', 'ifconfig wlan1 down' in log.read_text() and not (hs / 'ap.pid').exists())
    check('check with nothing running: 1', run('check').returncode == 1)


def device_socket_constants():
    """The device build has no libc header (CODA_HOST takes the host's), so its hand-written MSG_DONTWAIT is
    what the helper sends spot_send's datagram with: it must be Linux's value on every architecture, as the
    host's, not MSG_EOR's (0x80), which would block the one thread on librespot's full queue."""
    import socket
    m = re.search(r'^#define MSG_DONTWAIT (\S+)', (ROOT / 'patch/q2_guest.c').read_text(), re.M)
    check(f'the device\'s MSG_DONTWAIT ({m and m.group(1)}) is Linux\'s {socket.MSG_DONTWAIT:#x}',
          bool(m) and int(m.group(1), 0) == socket.MSG_DONTWAIT)


def main():
    device_socket_constants()
    with tempfile.TemporaryDirectory(prefix='q2-hotspot-') as tmp:
        hotspot_script(pathlib.Path(tmp))
    # each bluealsa the helper may meet (docs/bluealsa.md#the-callers): stock's 4.0.0, and Vamp's 5.0.0, which has no
    # GetPCMs, PCMAdded or PCMRemoved and calls the rate Rate
    for daemon in ('stock', 'own'):
        print(f'== bluealsa as {"stock\'s 4.0.0" if daemon == "stock" else "Vamp\'s 5.0.0"}')
        os.environ['Q2_FAKE_BLUEALSA'] = daemon
        with tempfile.TemporaryDirectory(prefix='q2-guest-') as tmp:
            d = pathlib.Path(tmp)
            rig = Rig.__new__(Rig)
            try:
                rig.__init__(d)
                run(rig, d)
                asked = rig.log.read_text()
                check('the helper lists PCMs with the object manager and never GetPCMs',
                      'BLUEALSA GetManagedObjects' in asked and 'BLUEALSA GetPCMs' not in asked)
            finally:
                rig.close()


def run(rig, d):
    (d / 'data/config').write_text("enabled=1\nname=Gray's Q2\nallow=ask\nleave=resume\n")
    for mac, name in ((PHONE, "Alex's Pixel"), (SAM, "Sam's iPhone"), (CAR, 'BMW 6R81')):
        rig.send('main', f'device {mac} {name}')
    rig.send('main', f'pcm {CAR} SBC 48000 {SBC_48K}')
    rig.start_helper()
    rig.wait(lambda: 'hotspot_pass=' in (d / 'data/config').read_text() and
             re.search(r'^hotspot_pass=[a-z]+-[a-z]+-\d\d$', (d / 'data/config').read_text(), re.M), 'a password made')
    print('ok   no hotspot password saved: one is made (word-word-NN) and saved')
    s = rig.until('starts on, idle, the settings in the state', state='idle', enabled='1', name="Gray's Q2",
                  allow='ask', leave='resume')
    check('... names where it plays: the car, its codec line', s['out'] == 'BMW 6R81' and s['outmac'] == CAR and
          s['outcodec'] == f'SBC 48000 {sbc_kbps(48000, 16, 8, 53)}')
    rig.expect('MAIN source', 'on with Bluetooth up: the main daemon is set to the source role')
    rig.expect('SINKD -B sink -p a2dp-sink -c AAC', 'the car is connected, so the sink daemon starts: AAC and SBC only')
    rig.expect('UP sink')

    # Let a Guest Connect, a first pairing with a code, Allow Once
    rig.mark()
    rig.cmd('open')
    s = rig.until('open: the window is open', state='open')
    check('... for two minutes', 115 <= int(s['window']) <= 120)
    rig.expect('REGISTERAGENT /org/q2pod/guest_agent DisplayYesNo', '... with its own agent, which compares codes')
    rig.expect('DEFAULTAGENT /org/q2pod/guest_agent', '... as the default agent')
    rig.expect("SET /org/bluez/hci0 Alias Gray's Q2", '... named the Guest Play name')
    rig.expect('SET /org/bluez/hci0 Pairable True')
    rig.expect('SET /org/bluez/hci0 Discoverable True', '... discoverable')
    rig.send('main', f'confirm {PHONE} 482913')
    s = rig.until('a pairing asks, with the code', state='asking', guest="Alex's Pixel", mac=PHONE, code='482913')
    check('... and its countdown', s['countdown'] in ('1', '2') and s['trusted'] == '0')
    rig.mark()
    rig.cmd('allow-once')
    rig.expect('AGENTREPLY RequestConfirmation ok', 'Allow Once: the pairing is accepted')
    rig.until('... and the guest is the session (connecting)', state='playing', guest="Alex's Pixel", status='')
    rig.expect('SET /org/bluez/hci0 Discoverable False', '... the window closes')
    rig.expect('SET /org/bluez/hci0 Alias Q2', "... the Q2's own name back")
    rig.expect('UNREGISTERAGENT', '... its agent gone')
    rig.mark()
    rig.send('sink', f'pcm {PHONE} SBC 48000 {SBC_48K}')
    rig.expect(f'APLAY -B sink -D bluealsa:DEV={CAR},PROFILE=a2dp {PHONE}',
               'its stream: bluealsa-aplay relays the sink daemon\'s PCM to the car')
    s = rig.until('... not asked again: playing', state='playing', status='playing')
    check('... with the guest leg\'s codec line', s['in'] == f'SBC 48000 {sbc_kbps(48000, 16, 8, 53)}')

    # the guest's song to the car, the car's keys to the guest
    rig.mark()
    rig.send('main', f'player {PHONE}')
    for k, v in (('Title', 'Levitating'), ('Artist', 'Dua Lipa'), ('Album', 'Future Nostalgia'), ('Genre', 'Pop'),
                 ('TrackNumber', '5'), ('NumberOfTracks', '11'), ('Duration', '203000')):
        rig.send('main', f'track {PHONE} {k} {v}')
    s = rig.until('the guest\'s song in the state', title='Levitating', artist='Dua Lipa', album='Future Nostalgia',
                  genre='Pop', track='5', tracks='11', duration='203000')
    want = track_record(5, 11, 203000, 'Levitating', 'Dua Lipa', 'Future Nostalgia', 'Pop')
    rig.wait(lambda: any(l == 'NOTIFY 0x108 ' + want for l in rig.lines()), 'the car gets the whole song')
    print('ok   ... to the car through shanling.bluez, byte for byte as libbtctl builds it')
    check('... and the play status, 1', 'NOTIFY 0x107 01' in rig.lines())
    rig.mark()
    rig.send('main', f'track {PHONE} Album ')
    rig.send('main', f'track {PHONE} Genre ')
    want = track_record(5, 11, 203000, 'Levitating', 'Dua Lipa')
    rig.wait(lambda: any(l == 'NOTIFY 0x108 ' + want for l in rig.lines()[rig.at:]), 'the record without album and genre')
    print('ok   ... an empty album or genre is left out, as libbtctl leaves it')
    for key, method in (('next', 'Next'), ('prev', 'Previous'), ('pause', 'Pause'), ('play', 'Play'),
                        ('toggle', 'Pause')):
        rig.mark()
        rig.cmd('key ' + key)
        rig.expect(f'PLAYER {method} /org/bluez/hci0/dev_AA_BB_CC_DD_EE_01/player0', f'key {key}: the guest\'s {method}')
    rig.mark()
    rig.send('main', f'status {PHONE} paused')
    rig.until('the guest pauses: paused', state='paused', status='paused')
    rig.wait(lambda: 'NOTIFY 0x107 02' in rig.lines()[rig.at:], 'the car hears paused')
    print('ok   ... and the car hears it')
    rig.mark()
    rig.cmd('key toggle')
    rig.expect('PLAYER Play', 'toggle while paused: Play')
    rig.send('main', f'status {PHONE} playing')
    rig.until('playing again', state='playing')

    # a second guest: Replace, Keep
    rig.mark()
    rig.send('sink', f'pcm {SAM} AAC 44100 {AAC_256K}')
    s = rig.until('a second guest: the Replace prompt', state='replace', new="Sam's iPhone", newmac=SAM,
                  guest="Alex's Pixel")
    check('... with its countdown', s['countdown'] in ('1', '2'))
    rig.cmd('keep')
    rig.expect('DISCONNECT /org/bluez/hci0/dev_AA_BB_CC_DD_EE_02', 'Keep: the second guest is disconnected')
    s = rig.until('... the first plays on', state='playing', guest="Alex's Pixel", event='kept', eventname="Sam's iPhone")
    seq = int(s['event_seq'])
    rig.send('sink', f'unpcm {SAM}')

    # Stop Guest Play
    rig.mark()
    rig.cmd('stop')
    rig.expect('APLAY-END', 'stop: the relay stops')
    rig.expect('DISCONNECT /org/bluez/hci0/dev_AA_BB_CC_DD_EE_01', '... the guest is disconnected')
    s = rig.until('... idle, the event for the toast', state='idle', event='stopped', eventname="Alex's Pixel")
    check('... counted', int(s['event_seq']) == seq + 1)
    check('... the song cleared', s['title'] == '' and s['guest'] == '')
    rig.send('sink', f'unpcm {PHONE}')

    # a known guest (paired, not trusted) streams: asked without a code; Decline; then no answer
    rig.mark()
    rig.send('sink', f'pcm {SAM} AAC 44100 {AAC_256K}')
    s = rig.until('a paired guest streams: asked, no code', state='asking', guest="Sam's iPhone", code='')
    rig.cmd('decline')
    rig.expect('DISCONNECT /org/bluez/hci0/dev_AA_BB_CC_DD_EE_02', 'Decline: disconnected')
    rig.until('... event declined', state='idle', event='declined')
    rig.send('sink', f'unpcm {SAM}')
    rig.mark()
    rig.send('sink', f'pcm {SAM} AAC 44100 {AAC_256K}')
    rig.until('asked again', state='asking')
    rig.expect('DISCONNECT /org/bluez/hci0/dev_AA_BB_CC_DD_EE_02', 'no answer: declined when the countdown ends')
    rig.until('... event timeout', state='idle', event='timeout')
    rig.send('sink', f'unpcm {SAM}')

    # Always Allow: a Trusted Guest plays without a prompt next time
    rig.send('sink', f'pcm {SAM} AAC 44100 {AAC_256K}')
    rig.until('asked', state='asking', guest="Sam's iPhone")
    rig.mark()
    rig.cmd('allow-always')
    rig.expect(f'APLAY -B sink -D bluealsa:DEV={CAR},PROFILE=a2dp {SAM}', 'Always Allow: it plays')
    s = rig.until('... as a Trusted Guest', state='playing', trusted='1')
    check('... its AAC leg', s['in'] == 'AAC 44100 256')
    trusted = (d / 'data/trusted').read_text().split('\t')
    check('... saved: MAC, ask 0, the time, the name', trusted[0] == SAM and trusted[1] == '0'
          and abs(int(trusted[2]) - time.time()) < 60 and trusted[3] == "Sam's iPhone\n")
    rig.send('sink', f'unpcm {SAM}')
    rig.until('its stream ends while playing: lost', state='idle', event='lost')
    rig.mark()
    rig.send('sink', f'pcm {SAM} AAC 44100 {AAC_256K}')
    rig.until('the Trusted Guest again: plays without a prompt', state='playing', guest="Sam's iPhone")
    rig.cmd(f'ask {SAM} 1')
    rig.wait(lambda: (d / 'data/trusted').read_text().split('\t')[1] == '1', 'Ask Before Playing saved')
    print('ok   ask MAC 1: Ask Before Playing saved')
    rig.cmd(f'rename {SAM} Sam')
    rig.wait(lambda: (d / 'data/trusted').read_text().endswith('\tSam\n'), 'renamed')
    print('ok   rename MAC: the list\'s name')

    # paused too long: given up
    rig.send('main', f'player {SAM}')
    rig.mark()
    rig.send('main', f'status {SAM} paused')
    rig.until('paused', state='paused')
    rig.expect('DISCONNECT /org/bluez/hci0/dev_AA_BB_CC_DD_EE_02', 'paused past the limit: disconnected')
    rig.until('... event gaveup', state='idle', event='gaveup')
    rig.send('sink', f'unpcm {SAM}')

    # Trusted Guests Only; settings
    rig.cmd('allow trusted')
    rig.cmd('leave stay')
    rig.cmd('name Car Q2')
    rig.until('the settings', allow='trusted', leave='stay', name='Car Q2')
    check('... saved', (d / 'data/config').read_text() ==
          f"enabled=1\nname=Car Q2\nallow=trusted\nleave=stay\nhotspot=0\nhotspot_pass={rig.state()['hotspot_pass']}\n")
    rig.mark()
    rig.send('sink', f'pcm {PHONE} SBC 48000 {SBC_48K}')
    rig.expect('DISCONNECT /org/bluez/hci0/dev_AA_BB_CC_DD_EE_01', 'Trusted Guests Only: an untrusted guest is refused')
    rig.until('... without a prompt', state='idle', event='refused', eventname="Alex's Pixel")
    rig.send('sink', f'unpcm {PHONE}')
    rig.mark()
    rig.cmd('open')
    rig.until('open', state='open')
    rig.send('main', f'authorize {PHONE}')
    rig.until('... a pairing in the window still asks (no code: Just Works)', state='asking', code='', mac=PHONE)
    rig.cmd('decline')
    rig.expect('AGENTREPLY RequestAuthorization org.bluez.Error.Rejected', 'Decline: the pairing is rejected')
    rig.until('... the window stays open', state='open', event='declined')
    rig.send('main', f'confirm {PHONE} 111111')
    rig.until('asked', state='asking', code='111111')
    rig.send('main', 'cancel')
    rig.until('the phone cancels the pairing: failed', state='open', event='failed')
    rig.cmd('close')
    rig.until('close', state='idle', window='0')

    # allowed while pairing but no stream comes
    rig.cmd('open')
    rig.until('open again', state='open')
    rig.send('main', f'confirm {PHONE} 222222')
    rig.until('asked', state='asking')
    rig.cmd('allow-once')
    rig.until('allowed', state='playing')
    rig.until('no stream in time: failed', state='idle', event='failed')

    # the hotspot
    pw = rig.state()['hotspot_pass']
    check('the hotspot starts off', rig.state()['hotspot'] == '0' and not rig.seen('HOTSPOT start'))
    rig.mark()
    rig.cmd('hotspot on')
    rig.expect(f"HOTSPOT start {'Car Q2'.encode().hex()} {pw}", 'hotspot on: q2-hotspot starts, the name as hex')
    s = rig.until('... up, its address and join code in the state', hotspot='1', hotspot_up='1', hotspot_ip='192.168.43.1',
                  hotspot_name='Car Q2')
    check('... the QR text', s['hotspot_qr'] == f'WIFI:T:WPA;S:Car Q2;P:{pw};;')
    rig.until('... checked every so often: the phones joined', hotspot_clients='2')
    rig.mark()
    rig.cmd('hotspot-pass too"bad"pw')
    rig.cmd('hotspot-pass short')
    rig.cmd("name Q2: Gray's")
    rig.expect(f"HOTSPOT start {'Q2: Gray\'s'.encode().hex()} {pw}", 'a new name restarts it under that name')
    s = rig.until('... its QR text escaped', hotspot_name="Q2: Gray's")
    check('... : escaped in the QR text', s['hotspot_qr'] == f"WIFI:T:WPA;S:Q2\\: Gray's;P:{pw};;")
    check('a password with a quote, or under 8 characters, is refused', rig.state()['hotspot_pass'] == pw)
    rig.mark()
    rig.cmd('hotspot-pass river-tulip-07')
    rig.expect("HOTSPOT start", 'a new password restarts it')
    rig.until('... saved', hotspot_pass='river-tulip-07')
    check('... in the config', 'hotspot_pass=river-tulip-07\n' in (d / 'data/config').read_text())

    # a Spotify Connect guest (docs/internals.md#guest-play-receivers)
    import socket
    spot = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    spot.bind(str(d / 'spot.sock'))
    spot.settimeout(5)
    rig.cmd('allow ask')
    rig.cmd('net spotify connect Spotify Connect')
    s = rig.until('a receiver\'s session asks too', state='asking', source='spotify', guest='Spotify Connect',
                  mac='spotify:connect', code='')
    seq = int(s['netdeny_seq'])
    rig.cmd('decline')
    s = rig.until('Decline: the payload is told to stop it', state='idle', event='declined', netdeny='spotify')
    check('... counted', int(s['netdeny_seq']) == seq + 1)
    (d / 'spot.state').write_text('state=playing\ntitle=Levitating\nartist=Dua Lipa\nalbum=Future Nostalgia\n'
                                  'duration=203000\nposition=1000\nat=0\n')
    rig.mark()
    rig.cmd('net spotify connect Spotify Connect')
    rig.until('asked again', state='asking', source='spotify')
    rig.cmd('allow-always')
    s = rig.until('Always Allow: it plays, its song from librespot', state='playing', source='spotify',
                  title='Levitating', artist='Dua Lipa', album='Future Nostalgia', duration='203000')
    check('... no relay for a receiver', not rig.seen('APLAY'))
    want = track_record(0, 0, 203000, 'Levitating', 'Dua Lipa', 'Future Nostalgia')
    rig.wait(lambda: any(l == 'NOTIFY 0x108 ' + want for l in rig.lines()[rig.at:]), 'its song to the car')
    print('ok   ... and to the car')
    for key, letter in (('next', b'n'), ('prev', b'b'), ('pause', b's'), ('play', b'P'), ('toggle', b'p')):
        rig.cmd('key ' + key)
        check(f'key {key}: librespot\'s {letter.decode()}', spot.recv(8) == letter)
    (d / 'spot.state').write_text('state=none\n')
    rig.until('the phone picks another device: left', state='idle', event='left', eventname='Spotify Connect')
    (d / 'spot.state').write_text('state=playing\ntitle=Levitating\n')
    rig.cmd('net spotify connect Spotify Connect')
    rig.until('trusted now: plays without asking', state='playing', source='spotify')
    rig.cmd('net-end spotify')
    rig.until('net-end: the session ends', state='idle', event='left')
    spot.close()

    # AirPlay and DLNA: approval alike; AirPlay's title is "AirPlay" (shairport tells none), DLNA's song is demo's
    rig.mark()
    rig.cmd('net airplay client AirPlay')
    rig.until('AirPlay asks', state='asking', source='airplay', mac='airplay:client')
    rig.cmd('allow-once')
    rig.until('... allowed', state='playing', source='airplay', title='AirPlay')
    want = track_record(0, 0, 0, 'AirPlay')
    rig.wait(lambda: any(l == 'NOTIFY 0x108 ' + want for l in rig.lines()[rig.at:]), 'AirPlay to the car')
    print('ok   ... the car shows AirPlay')
    rig.cmd('stop')
    s = rig.until('stop: AirPlay is the payload\'s to end', state='idle', event='stopped', netdeny='airplay')
    rig.mark()
    rig.cmd('net dlna renderer DLNA')
    rig.until('DLNA asks', state='asking', source='dlna')
    rig.cmd('allow-once')
    rig.until('... allowed', state='playing', source='dlna')
    time.sleep(2)
    check('... and nothing of DLNA\'s to the car: demo sends its own song', not rig.seen('NOTIFY'))
    rig.cmd('net-end dlna')
    rig.until('net-end dlna', state='idle', event='left')

    # forget, off
    rig.mark()
    rig.cmd(f'forget {SAM}')
    rig.expect('REMOVEDEVICE /org/bluez/hci0/dev_AA_BB_CC_DD_EE_02', 'forget: its pairing removed')
    rig.wait(lambda: SAM not in (d / 'data/trusted').read_text(), 'gone from the list')
    check('... the Spotify Connect guest still in it', (d / 'data/trusted').read_text().startswith('spotify:connect\t0\t'))
    print('ok   ... and from the list')
    rig.mark()
    rig.cmd('off')
    rig.until('off', state='off', enabled='0')
    rig.expect('MAIN stock', '... the main daemon back to stock\'s roles')
    rig.expect('HOTSPOT stop', '... and the hotspot stopped')
    rig.wait(lambda: rig.q2.poll() is None and not S.run(['pgrep', '-f', f'fake_bluez.py sink {d}'],
                                                         capture_output=True).stdout, 'the sink daemon stopped')
    print('ok   ... the sink daemon stopped')
    check('... saved off', (d / 'data/config').read_text().startswith('enabled=0\n'))
    rig.q2.terminate()
    rig.q2.wait(5)
    check('SIGTERM: the state file goes (reads as off)', not (d / 'run.state').exists())


def test_conformance():
    """docs/platform/guest.md's transcripts against q2-guest's Q2GUEST/1 socket (--socket), each block on a daemon
    started afresh from the same settings and Trusted Guests, beside the stand-in BlueZ with no device"""
    sys.path.insert(0, str(ROOT / 'test'))
    import conformance as C
    os.environ.setdefault('Q2_FAKE_BLUEALSA', 'own')
    with tempfile.TemporaryDirectory(prefix='q2-guest-api-') as tmp:
        d = pathlib.Path(tmp)
        rig = Rig.__new__(Rig)
        try:
            rig.__init__(d)
            sock = d / 'guest.sock'
            for block in C.blocks('guest.md'):
                if getattr(rig, 'q2', None):
                    rig.q2.terminate()
                    rig.q2.wait(5)
                (d / 'data/config').write_text('enabled=1\nname=Test Q2\nallow=ask\nleave=resume\nhotspot=0\n'
                                               'hotspot_pass=tulip-river-42\n')
                (d / 'data/trusted').write_text("AA:BB:CC:DD:EE:03\t0\t1700000000\tSam's iPhone\n")
                rig.start_helper('--socket', str(sock))
                rig.wait(sock.exists, 'the socket is there')
                C.replay(sock, block)
            C.check_coverage('guest.md')
        finally:
            rig.close()


if __name__ == '__main__':
    main()
    test_conformance()
    print('ok   docs/platform/guest.md\'s transcripts')
