#!/usr/bin/python3
"""Run a bluealsa daemon for the Q2 under qemu-mipsel with no Bluetooth radio, as far as that goes
(docs/bluealsa.md#what-the-emulator-shows): start-up, its D-Bus registration with a stand-in for bluetoothd,
an A2DP source transport per codec, the PCM opened as the ALSA plugin opens it (org.bluealsa.PCM1.Open), and
audio written through the encoder to the transport's socket, with the encoder thread's CPU time beside it.

    python3 test/bluealsa_own.py STOCKFS DAEMON [--stock] [--codecs SBC,AAC,...] [--seconds N] [--json FILE]
                                 [--capture DIR]

STOCKFS is an unpacked V1.36 rootfs (qemu's -L, the libraries the daemon loads); DAEMON the MIPS program:
Vamp's bluealsad (tools/thirdparty.py build bluealsa), or with --stock Shanling's bluealsa 4.0.0, which is
given its own launcher's options and found through its own property names. Both run the same script, so the
two sets of numbers compare. Exit status 0 when every codec asked for streamed.

What it cannot show: anything a radio does. The transport's socket is a local one this script reads, so
there is no AVDTP, no real MTU, no flow control from a controller, no headset and no bluetoothd of Shanling's.
The CPU figure is the host's time in qemu's thread for the encoder, good for comparing two daemons under the
same emulator and nothing else: it is not the Q2's.

--against-stock runs stock's daemon (STOCKFS/usr/bin/bluealsa) and DAEMON with --shanling the same way and checks
the compatibility service (docs/bluealsa.md#the-compatibility-service) says what stock's says: the interface as each
describes it, every StatusChange for a configuration of each codec and a volume change (all 14 bytes, but the last 4
of a codec event, which stock leaves unset), none for a refused configuration, /tmp/bt_startup made, and the bit
rate SBC and LDAC then stream at after each SetSBCQuality and SetLDACQuality the Q2 sends. Exit status 0 when all
match.

--capture DIR writes, for each codec, what the payload's callers read (docs/bluealsa.md#the-callers): the
daemon's org.freedesktop.DBus.ObjectManager.GetManagedObjects on /org/bluealsa as `dbus-send --print-reply`
prints it while the PCM exists, as DIR/<stock|own>-<codec>.txt, the fixtures test/unit/test_q2bt.py,
test/fw/test_bt_battery.py, test/fw/test_bt_ui.py and test/q2_guest.py read. The results also say which PCM
signals the daemon sent (InterfacesAdded and InterfacesRemoved on /org/bluealsa, and 4.0.0's PCMAdded and
PCMRemoved), from dbus-monitor.

Run by the system python3 (dbus-python and PyGObject), as test/guest/fake_bluez.py is."""
import argparse
import json
import math
import os
import pathlib
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time

BUS_CONF = '''<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path={path}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow user="*"/><allow own="*"/><allow send_destination="*"/>
<allow receive_sender="*"/><allow send_type="method_call"/></policy></busconfig>'''
ADAPTER = '/org/bluez/hci0'
DEVICE = ADAPTER + '/dev_02_00_00_00_00_01'  # a locally administered address: nobody's headset
SOURCE_UUID = '0000110a-0000-1000-8000-00805f9b34fb'
MTU = 895  # what BlueZ hands a source on a 3-DH5 link; LDAC refuses one under 679
# An A2DP configuration a 48 kHz stereo sink would be given, per codec: (the codec octet, the vendor's
# id and codec id for 0xff, the configuration's bytes)
CODECS = {
    'SBC': (0x00, None, bytes([0x11, 0x15, 2, 53])),  # 48 kHz joint stereo; 16 blocks, 8 subbands, loudness; bitpool 2-53
    'AAC': (0x02, None, bytes([0x80, 0x00, 0x84, 0x83, 0xa9, 0x80])),  # MPEG-2 LC, 48 kHz, 2 channels, VBR, 240000
    'aptX': (0xff, (0x4f, 0x01), bytes([0x4f, 0, 0, 0, 0x01, 0, 0x12])),  # 48 kHz stereo
    'aptX-HD': (0xff, (0xd7, 0x24), bytes([0xd7, 0, 0, 0, 0x24, 0, 0x12, 0, 0, 0, 0])),
    'LDAC': (0xff, (0x12d, 0xaa), bytes([0x2d, 0x01, 0, 0, 0xaa, 0, 0x10, 0x01])),  # 48 kHz stereo
}


def fake_bluez(address):
    """The stand-in for bluetoothd: an adapter with Media1, where the daemon registers its endpoints either
    way (RegisterEndpoint, stock's; RegisterApplication, 5.0.0's), ProfileManager1, and a transport whose
    Acquire hands over one end of a socket pair this process drains and counts. Commands come a line at a
    time on stdin (`config CODEC`, `stat`, `quit`); each answer is a line of JSON on stdout."""
    import dbus
    import dbus.mainloop.glib
    import dbus.service
    from gi.repository import GLib
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.bus.BusConnection(address)
    name = dbus.service.BusName('org.bluez', bus)  # noqa: F841 (held)
    props_iface, om_iface = 'org.freedesktop.DBus.Properties', 'org.freedesktop.DBus.ObjectManager'
    state = dict(endpoints=[], profiles=[], how=None, packets=0, bytes=0, acquired=0, released=0, transport=None)

    def say(**kv):
        print(json.dumps(kv), flush=True)

    class Root(dbus.service.Object):
        @dbus.service.method(om_iface, out_signature='a{oa{sa{sv}}}')
        def GetManagedObjects(self):
            return {ADAPTER: {'org.bluez.Adapter1': {'Address': '02:00:00:00:00:00'}, 'org.bluez.Media1': {}},
                    DEVICE: {'org.bluez.Device1': {'Address': '02:00:00:00:00:01', 'Adapter': dbus.ObjectPath(ADAPTER),
                                                   'Alias': 'Test Sink', 'Connected': True}}}

    class Profiles(dbus.service.Object):
        @dbus.service.method('org.bluez.ProfileManager1', in_signature='osa{sv}')
        def RegisterProfile(self, path, uuid, options):
            state['profiles'].append(str(uuid).lower()[4:8])

    class Adapter(dbus.service.Object):
        @dbus.service.method('org.bluez.Media1', in_signature='oa{sv}', sender_keyword='sender')
        def RegisterEndpoint(self, path, props, sender=None):
            state['how'] = 'RegisterEndpoint'
            state['endpoints'].append(dict(owner=sender, path=str(path), uuid=str(props['UUID']).lower(),
                                           codec=int(props['Codec']), caps=bytes(props['Capabilities'])))

        @dbus.service.method('org.bluez.Media1', in_signature='oa{sv}', sender_keyword='sender')
        def RegisterApplication(self, root, options, sender=None):
            state['how'] = 'RegisterApplication'

            def listed(objects):
                for path, ifaces in objects.items():
                    ep = ifaces.get('org.bluez.MediaEndpoint1')
                    if ep:
                        state['endpoints'].append(dict(owner=sender, path=str(path), uuid=str(ep['UUID']).lower(),
                                                       codec=int(ep['Codec']), caps=bytes(ep['Capabilities'])))
            bus.get_object(sender, root).GetManagedObjects(dbus_interface=om_iface, reply_handler=listed,
                                                           error_handler=lambda e: say(error=str(e)))

    class Transport(dbus.service.Object):
        def __init__(self, path, codec, config):
            super().__init__(bus, path)
            self.p = {'Device': dbus.ObjectPath(DEVICE), 'UUID': SOURCE_UUID, 'Codec': dbus.Byte(codec),
                      'Configuration': dbus.ByteArray(config), 'State': 'idle', 'Volume': dbus.UInt16(100)}

        def acquire(self):
            ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
            ours.setblocking(False)
            state['acquired'] += 1

            def readable(_fd, _cond):
                try:
                    while True:
                        data = ours.recv(4096)
                        if not data:
                            return False
                        state['packets'] += 1
                        state['bytes'] += len(data)
                except BlockingIOError:
                    return True
                except OSError:
                    return False
            GLib.io_add_watch(ours.fileno(), GLib.IO_IN | GLib.IO_HUP, readable)
            self.sock = ours
            fd = dbus.types.UnixFd(theirs.fileno())
            theirs.close()
            GLib.idle_add(self.active)
            return fd, dbus.UInt16(MTU), dbus.UInt16(MTU)

        def active(self):
            self.p['State'] = 'active'
            self.PropertiesChanged('org.bluez.MediaTransport1', {'State': 'active'}, [])
            return False

        @dbus.service.method('org.bluez.MediaTransport1', out_signature='hqq')
        def Acquire(self):
            return self.acquire()

        @dbus.service.method('org.bluez.MediaTransport1', out_signature='hqq')
        def TryAcquire(self):
            return self.acquire()

        @dbus.service.method('org.bluez.MediaTransport1')
        def Release(self):
            state['released'] += 1
            self.p['State'] = 'idle'

        @dbus.service.method(props_iface, in_signature='ss', out_signature='v')
        def Get(self, iface, prop):
            return self.p[prop]

        @dbus.service.method(props_iface, in_signature='s', out_signature='a{sv}')
        def GetAll(self, iface):
            return self.p

        @dbus.service.method(props_iface, in_signature='ssv')
        def Set(self, iface, prop, value):
            self.p[prop] = value

        @dbus.service.signal(props_iface, signature='sa{sv}as')
        def PropertiesChanged(self, iface, changed, invalidated):
            pass

    def vendor(caps):
        return struct.unpack_from('<IH', caps) if len(caps) >= 6 else None

    def command(line):
        words = line.split()
        if not words or words[0] == 'quit':
            loop.quit()
        elif words[0] == 'stat':
            say(how=state['how'], endpoints=len(state['endpoints']), profiles=sorted(state['profiles']), packets=state['packets'], bytes=state['bytes'],
                acquired=state['acquired'], released=state['released'])
        elif words[0] == 'config':
            octet, ids, config = CODECS[words[1]]
            found = [e for e in state['endpoints'] if e['uuid'] == SOURCE_UUID and e['codec'] == octet
                     and (ids is None or vendor(e['caps']) == ids)]
            if not found:
                return say(error=f'no source endpoint for {words[1]}')
            path = f'{DEVICE}/fd{len(Transport.made)}'
            Transport.made.append(Transport(path, octet, config))
            state.update(packets=0, bytes=0)
            bus.get_object(found[0]['owner'], found[0]['path']).SetConfiguration(
                dbus.ObjectPath(path), Transport.made[-1].p, dbus_interface='org.bluez.MediaEndpoint1',
                reply_handler=lambda: say(configured=path, endpoint=found[0]['path']),
                error_handler=lambda e: say(error=str(e)))
        elif words[0] == 'volume':  # the headset's absolute volume changed: BlueZ's PropertiesChanged on the transport
            t = Transport.made[-1]
            t.p['Volume'] = dbus.UInt16(int(words[1]))
            t.PropertiesChanged('org.bluez.MediaTransport1', {'Volume': t.p['Volume']}, [])
            say(volume=int(words[1]))
        elif words[0] == 'badconfig':  # a configuration the daemon must refuse: CODEC's one byte short, or (zero)
            octet, ids, config = CODECS[words[1]]  # as long as it should be with no sampling frequency or mode bit set
            bad = bytes(len(config)) if words[2:] == ['zero'] else config[:-1]
            found = [e for e in state['endpoints'] if e['uuid'] == SOURCE_UUID and e['codec'] == octet
                     and (ids is None or vendor(e['caps']) == ids)]
            path = f'{DEVICE}/fd{len(Transport.made)}'
            Transport.made.append(Transport(path, octet, bad))
            bus.get_object(found[0]['owner'], found[0]['path']).SetConfiguration(
                dbus.ObjectPath(path), Transport.made[-1].p, dbus_interface='org.bluez.MediaEndpoint1',
                reply_handler=lambda: say(configured=path), error_handler=lambda e: say(refused=str(e)))
        elif words[0] == 'select':  # SelectConfiguration with capabilities the daemon shares nothing with
            octet, ids, config = CODECS[words[1]]
            found = [e for e in state['endpoints'] if e['uuid'] == SOURCE_UUID and e['codec'] == octet
                     and (ids is None or vendor(e['caps']) == ids)]
            bus.get_object(found[0]['owner'], found[0]['path']).SelectConfiguration(
                dbus.ByteArray(bytes(len(config))), dbus_interface='org.bluez.MediaEndpoint1',
                reply_handler=lambda c: say(selected=bytes(c).hex()), error_handler=lambda e: say(refused=str(e)))
        elif words[0] == 'clear':
            t = Transport.made[-1]
            for e in state['endpoints']:
                bus.get_object(e['owner'], e['path']).ClearConfiguration(
                    dbus.ObjectPath(t._object_path), dbus_interface='org.bluez.MediaEndpoint1',
                    reply_handler=lambda: None, error_handler=lambda e: None)
            say(cleared=str(t._object_path))

    Transport.made = []
    keep = (Root(bus, '/'), Profiles(bus, '/org/bluez'), Adapter(bus, ADAPTER))  # noqa: F841 (held)
    loop = GLib.MainLoop()

    def stdin_ready(_fd, cond):
        line = sys.stdin.readline()
        if not line:
            loop.quit()
            return False
        command(line)
        return True
    GLib.io_add_watch(sys.stdin.fileno(), GLib.IO_IN | GLib.IO_HUP, stdin_ready)
    say(ready=True)
    loop.run()


class Fake:
    def __init__(self, address):
        self.p = subprocess.Popen([sys.executable, __file__, '--fake-bluez', address], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True)
        assert self.read().get('ready')

    def read(self):
        line = self.p.stdout.readline()
        if not line:
            raise SystemExit('the stand-in for bluetoothd ended')
        return json.loads(line)

    def ask(self, line):
        self.p.stdin.write(line + '\n')
        self.p.stdin.flush()
        return self.read()

    def close(self):
        try:
            self.p.stdin.write('quit\n')
            self.p.stdin.flush()
        except OSError:
            pass
        self.p.wait(5)


def threads(pid):
    """{tid: (comm, cpu seconds)} for each of pid's threads."""
    out, tick = {}, os.sysconf('SC_CLK_TCK')
    for t in pathlib.Path(f'/proc/{pid}/task').iterdir():
        try:
            stat = (t/'stat').read_text()
        except OSError:
            continue
        comm = stat[stat.index('(') + 1:stat.rindex(')')]
        fields = stat[stat.rindex(')') + 2:].split()
        out[int(t.name)] = (comm, (int(fields[11]) + int(fields[12])) / tick)
    return out


def rss_kb(pid):
    for line in pathlib.Path(f'/proc/{pid}/status').read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1])
    return 0


def audio(rate, channels, bits, width, seconds):
    """seconds of a busy signal (three tones a channel and noise, at about -9 dBFS), as the PCM's samples."""
    rng, n = random.Random(1), int(rate * seconds)
    full, fmt = (1 << (bits - 1)) - 1, {2: 'h', 4: 'i'}[width]
    out = bytearray()
    for i in range(n):
        t = i / rate
        for c in range(channels):
            v = (0.15 * math.sin(2 * math.pi * (220 + 110 * c) * t) + 0.1 * math.sin(2 * math.pi * 1870 * t)
                 + 0.05 * math.sin(2 * math.pi * (9100 + 400 * c) * t) + 0.05 * (rng.random() * 2 - 1))
            out += struct.pack('<' + fmt, int(v * full))
    return bytes(out)


def shanling_signals(path):
    """[member, int arguments..., hex of the byte array] of each shanling.bluealsa signal dbus-monitor wrote to path"""
    out, cur, raw = [], None, None
    for line in pathlib.Path(path).read_text(errors='replace').splitlines():
        w = line.split()
        if line.startswith('signal ') and 'interface=shanling.bluealsa;' in line:
            cur, raw = [line.split('member=')[1].strip()], None
            out.append(cur)
        elif line.startswith(('signal ', 'method ', 'error ')):
            cur = None
        elif cur is not None and w[:1] == ['int32']:
            cur.append(int(w[1]))
        elif cur is not None and w[:3] == ['array', 'of', 'bytes']:
            raw = []
            cur.append(raw)
        elif cur is not None and raw is not None and w and w[0] != ']':
            raw.extend(w)
    return [[x if not isinstance(x, list) else ''.join(x) for x in sig] for sig in out]


def signals(path):
    """(member, object path) of each org.bluealsa signal dbus-monitor wrote to path"""
    out = []
    for line in pathlib.Path(path).read_text(errors='replace').splitlines():
        if line.startswith('signal ') and ' member=' in line:
            fields = dict(f.split('=', 1) for f in line.split() if '=' in f)
            out.append((fields.get('member', ''), fields.get('path', '')))
    return out


def run(stockfs, daemon, stock, codecs, seconds, qemu, capture=None, events=False, calls=(), own_defaults=False):
    import dbus
    results, work = {}, pathlib.Path(tempfile.mkdtemp(prefix='q2-bluealsa-'))
    address = f'unix:path={work}/bus'
    (work/'bus.conf').write_text(BUS_CONF.format(path=work/'bus'))
    env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=address)
    busd = subprocess.Popen(['dbus-daemon', '--nofork', f'--config-file={work}/bus.conf'], stderr=subprocess.DEVNULL)
    procs, fake, log = [busd], None, open(work/'daemon.log', 'wb')
    try:
        for _ in range(100):
            if (work/'bus').exists():
                break
            time.sleep(0.05)
        fake = Fake(address)
        names = ['-c', 'LDAC', '-c', 'aptX-HD', '-c', 'aptX', '-c', 'AAC']
        # stock: its launcher's line as Vamp ships it; ours: the same choices, in 5.0.0's words
        options = (['--keep-alive=5', '--aac-afterburner', *names] if stock else
                   ['-p', 'a2dp-source', '--keep-alive=5', '--aac-afterburner', '--aac-vbr', '--aac-bitrate=240000',
                    *(['--sbc-quality=medium', '--ldac-quality=standard'] if own_defaults else ['--ldac-quality=high']),
                    *names] + (['--shanling'] if events or calls or own_defaults else []))  # stock's start, or 990 kbps
        # 5.0.0 keeps each device's volume and delay under its --localstatedir (tools/thirdparty.py BLUEALSA_OPTIONS),
        # and makes only the last directory of that path itself: its launcher makes the rest, as here
        os.makedirs('/tmp/q2-bluealsa/lib', exist_ok=True)
        started = time.monotonic()
        d = subprocess.Popen([qemu, '-L', stockfs, daemon, *options], env=env, stdout=log, stderr=subprocess.STDOUT)
        procs.append(d)
        bus = dbus.bus.BusConnection(address)
        seen, named = [], 0
        for _ in range(200):  # until it has registered, and a second has added no endpoint to those
            if bus.name_has_owner('org.bluealsa'):
                named = named or time.monotonic()
                seen.append(fake.ask('stat')['endpoints'])
            if len(seen) > 20 and seen[-1] >= 5 and seen[-1] == seen[-21]:
                break
            if d.poll() is not None:
                raise SystemExit(f'the daemon ended ({d.returncode}):\n' + (work/'daemon.log').read_text(errors='replace')[-2000:])
            time.sleep(0.05)
        else:
            raise SystemExit('the daemon registered no endpoints:\n' + (work/'daemon.log').read_text(errors='replace')[-2000:])
        reg = fake.ask('stat')
        results['startup'] = dict(seconds_to_bus_name=round(named - started, 2), registered_by=reg['how'],
                                  endpoints=reg['endpoints'], profiles=reg['profiles'], rss_kb=rss_kb(d.pid),
                                  shanling_service=bool(bus.name_has_owner('shanling.bluealsa')))
        manager = dbus.Interface(bus.get_object('org.bluealsa', '/org/bluealsa'), 'org.freedesktop.DBus.ObjectManager')
        for call in calls:  # the compatibility service's methods, as demo calls them: METHOD=INT (stock, or ours with --shanling)
            method, value = call.split('=')
            sl_obj = dbus.Interface(bus.get_object('shanling.bluealsa', '/shanling/bluealsa'), 'shanling.bluealsa')
            getattr(sl_obj, method)(dbus.Int32(int(value)))
            results.setdefault('calls', []).append(call)
        if bus.name_has_owner('shanling.bluealsa'):  # the compatibility service's interface, as it describes itself
            xml = str(dbus.Interface(bus.get_object('shanling.bluealsa', '/shanling/bluealsa'),
                                     'org.freedesktop.DBus.Introspectable').Introspect())
            i = xml.find('<interface name="shanling.bluealsa">')
            results['shanling_interface'] = ' '.join(xml[i:xml.find('</interface>', i) + 12].split()) if i >= 0 else None
        owner = bus.get_name_owner('org.bluealsa')
        monitor = subprocess.Popen(['dbus-monitor', '--address', address, f"type='signal',sender='{owner}'"],
                                   stdout=open(work/'signals', 'w'), stderr=subprocess.DEVNULL)
        procs.append(monitor)
        sl = subprocess.Popen(['dbus-monitor', '--address', address, "type='signal',interface='shanling.bluealsa'"],
                              stdout=open(work/'shanling', 'w'), stderr=subprocess.DEVNULL)
        procs.append(sl)
        time.sleep(0.3)  # their matches in place before the first configuration
        for codec in codecs:
            r = results[codec] = {}
            answer = fake.ask(f'config {codec}')
            if 'error' in answer:
                r['error'] = answer['error']
                continue
            pcm = None
            for _ in range(100):
                for path, ifaces in manager.GetManagedObjects().items():
                    p = ifaces.get('org.bluealsa.PCM1')
                    if p and str(p['Mode']) == 'sink' and 'A2DP' in str(p['Transport']):
                        pcm = (path, p)
                if pcm:
                    break
                time.sleep(0.05)
            if not pcm:
                r['error'] = 'no PCM appeared'
                continue
            path, p = pcm
            if events:  # what the compatibility service says: a configuration, a volume change, nothing at a clear
                time.sleep(0.3)
                fake.ask('volume 64')
                time.sleep(0.3)
                fake.ask('clear')
                for _ in range(100):
                    if not any('org.bluealsa.PCM1' in i for i in manager.GetManagedObjects().values()):
                        break
                    time.sleep(0.05)
                r['ok'] = True
                continue
            if capture:
                reply = subprocess.run(['dbus-send', '--system', '--print-reply', '--dest=org.bluealsa', '/org/bluealsa',
                                        'org.freedesktop.DBus.ObjectManager.GetManagedObjects'],
                                       env=env, capture_output=True, text=True, check=True).stdout
                pathlib.Path(capture).mkdir(parents=True, exist_ok=True)
                (pathlib.Path(capture) / f'{"stock" if stock else "own"}-{codec}.txt').write_text(reply)
                if stock:  # what the callers read before: 4.0.0's GetPCMs, to compare (5.0.0 has none)
                    old = subprocess.run(['dbus-send', '--system', '--print-reply', '--dest=org.bluealsa', '/org/bluealsa',
                                          'org.bluealsa.Manager1.GetPCMs'], env=env, capture_output=True, text=True).stdout
                    (pathlib.Path(capture) / f'stock-{codec}-GetPCMs.txt').write_text(old)
            rate = int(p['Sampling' if stock else 'Rate'])
            fmt, channels = int(p['Format']), int(p['Channels'])
            bits, width = fmt & 0xff, fmt >> 8 & 0x3f
            r.update(pcm=str(path), codec=str(p['Codec']), rate=rate, channels=channels, bits=bits, bytes_a_sample=width)
            try:
                fd_pcm, fd_ctrl = dbus.Interface(bus.get_object('org.bluealsa', path), 'org.bluealsa.PCM1').Open()
            except dbus.DBusException as e:
                r['error'] = f'Open: {e.get_dbus_message()}'
                fake.ask('clear')
                continue
            fd_pcm, fd_ctrl = fd_pcm.take(), fd_ctrl.take()
            block = audio(rate, channels, bits, width, 1.0)
            os.write(fd_pcm, block)  # a first second: the encoder starts, qemu translates its loop
            before, t0, stat0 = threads(d.pid), time.monotonic(), fake.ask('stat')
            for _ in range(seconds):
                os.write(fd_pcm, block)  # blocks while the FIFO is full: the encoder's own pace
            wall = time.monotonic() - t0
            after, stat1 = threads(d.pid), fake.ask('stat')
            enc = {tid: (comm, cpu) for tid, (comm, cpu) in after.items() if comm.startswith('ba-a2dp')}
            cpu = sum(c - before.get(tid, ('', 0))[1] for tid, (_, c) in enc.items())
            whole = sum(c for _, c in after.values()) - sum(c for _, c in before.values())
            r.update(audio_seconds=seconds, wall_seconds=round(wall, 2), encoder_threads=sorted({c for c, _ in enc.values()}),
                     encoder_cpu_ms_a_second=round(1000 * cpu / seconds, 1), daemon_cpu_ms_a_second=round(1000 * whole / seconds, 1),
                     packets_a_second=round((stat1['packets'] - stat0['packets']) / seconds, 1),
                     kbps=round((stat1['bytes'] - stat0['bytes']) * 8 / seconds / 1000, 1),
                     rss_kb=rss_kb(d.pid), acquired=stat1['acquired'])
            ctrl = socket.socket(fileno=fd_ctrl)
            ctrl.settimeout(5)
            ctrl.send(b'Drop')
            r['drop'] = ctrl.recv(32).decode(errors='replace')
            os.close(fd_pcm)
            ctrl.close()
            fake.ask('clear')
            for _ in range(100):  # the PCM gone before the next codec's appears
                if not any('org.bluealsa.PCM1' in i for i in manager.GetManagedObjects().values()):
                    break
                time.sleep(0.05)
            r['ok'] = r['drop'] == 'OK' and r['packets_a_second'] > 0 and d.poll() is None
        if events:
            results['badconfig'] = []
            for how in ('short', 'zero'):
                results['badconfig'].append('refused' in fake.ask(f'badconfig SBC {how}'))
                time.sleep(0.5)
            results['badselect'] = 'refused' in fake.ask('select SBC')
            time.sleep(0.5)
        results['alive_at_end'] = d.poll() is None
        time.sleep(0.3)
        results['shanling_signals'] = shanling_signals(work/'shanling')
        results['signals'] = sorted({m for m, path in signals(work/'signals') if path.startswith('/org/bluealsa')}
                                    & {'InterfacesAdded', 'InterfacesRemoved', 'PCMAdded', 'PCMRemoved'})
    finally:
        if fake:
            fake.close()
        for p in reversed(procs):
            if p.poll() is None:
                p.send_signal(signal.SIGTERM)
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        log.close()
        results['daemon_log_tail'] = (work/'daemon.log').read_text(errors='replace')[-1500:]
        shutil.rmtree(work, ignore_errors=True)
    return results


def against_stock(stockfs, daemon, qemu):
    """The compatibility service of DAEMON (--shanling) against stock's: 0 when every check matches"""
    failed = []

    def check(what, ok, detail=''):
        print(('ok   ' if ok else 'FAIL ') + what + ('' if ok else f': {detail}'), flush=True)
        if not ok:
            failed.append(what)
    stock_bin, codecs = os.path.join(stockfs, 'usr/bin/bluealsa'), list(CODECS)
    got = {}
    for who, binary in (('stock', stock_bin), ('own', daemon)):
        if os.path.exists('/tmp/bt_startup'):
            os.unlink('/tmp/bt_startup')
        got[who] = run(stockfs, binary, who == 'stock', codecs, 1, qemu, events=True)
        got[who]['startup_file'] = os.path.exists('/tmp/bt_startup')
    s, o = got['stock'], got['own']
    check('own: owns shanling.bluealsa and makes /tmp/bt_startup', o['startup']['shanling_service'] and o['startup_file'],
          (o['startup'].get('shanling_service'), o['startup_file']))
    def members(xml):  # each method's and signal's name and arguments, however the library writes the XML
        import xml.etree.ElementTree as ET
        if not xml:
            return None
        return sorted((m.tag, m.get('name'), tuple((a.get('type'), a.get('name'), a.get('direction')) for a in m))
                      for m in ET.fromstring(xml))
    ours, theirs = members(o.get('shanling_interface')), members(s.get('shanling_interface'))
    check('the interface, as each describes it, is stock\'s', bool(theirs) and ours == theirs, f'{ours} against {theirs}')

    def comparable(sig):  # a codec event's last 4 bytes are what stock left in memory: compare the first 10
        return [sig[0], sig[1], sig[2][:20] if sig[1] == 257 else sig[2]] if len(sig) == 3 else sig
    check('every StatusChange stock sends for five codecs and a volume change, and in order, none at a refusal',
          [comparable(x) for x in o['shanling_signals']] == [comparable(x) for x in s['shanling_signals']]
          and len(s['shanling_signals']) == 2 * len(codecs),
          f"{o['shanling_signals']} against {s['shanling_signals']}")
    for codec, calls in (('SBC', [None, 'SetSBCQuality=0', 'SetSBCQuality=1', 'SetSBCQuality=2']),
                         ('LDAC', ['SetLDACQuality=21', 'SetLDACQuality=22', 'SetLDACQuality=23'])):
        for call in calls:
            rates = [run(stockfs, b, who == 'stock', [codec], 3, qemu, calls=[call] if call else [],
                         own_defaults=who == 'own')[codec].get('kbps', 0)
                     for who, b in (('stock', stock_bin), ('own', daemon))]
            check(f'{codec} after {call or "no call"}: within 3% of stock\'s bit rate ({rates[0]} kbit/s)',
                  rates[0] > 0 and abs(rates[1] - rates[0]) <= 0.03 * rates[0], rates)
    print(f'{len(failed)} failed' if failed else 'all matched')
    return 1 if failed else 0


def main():
    if sys.argv[1:2] == ['--fake-bluez']:
        return fake_bluez(sys.argv[2])
    a = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    a.add_argument('stockfs')
    a.add_argument('daemon')
    a.add_argument('--stock', action='store_true')
    a.add_argument('--codecs', default=','.join(CODECS))
    a.add_argument('--seconds', type=int, default=15)
    a.add_argument('--qemu', default='qemu-mipsel')
    a.add_argument('--json')
    a.add_argument('--capture')
    a.add_argument('--events', action='store_true')
    a.add_argument('--call', action='append', default=[], help='METHOD=INT on shanling.bluealsa before the codecs')
    a.add_argument('--against-stock', action='store_true')
    args = a.parse_args()
    if args.against_stock:
        return against_stock(args.stockfs, args.daemon, args.qemu)
    codecs = args.codecs.split(',')
    results = run(args.stockfs, args.daemon, args.stock, codecs, args.seconds, args.qemu, args.capture, args.events,
                  args.call)
    tail = results.pop('daemon_log_tail')
    print(json.dumps(results, indent=1))
    if args.json:
        pathlib.Path(args.json).write_text(json.dumps(results, indent=1) + '\n')
    # the object manager's two signals, which the payload's callers follow (docs/bluealsa.md#the-callers)
    ok = (results.get('alive_at_end') and all(results.get(c, {}).get('ok') for c in codecs) and
          {'InterfacesAdded', 'InterfacesRemoved'} <= set(results.get('signals', [])))
    if not ok:
        print(tail, file=sys.stderr)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
