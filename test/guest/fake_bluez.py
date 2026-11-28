#!/usr/bin/python3
"""test/q2_guest.py's stand-in for bluetoothd and the bluealsa daemons, on the test's private bus.

`fake_bluez.py main CTL LOG` owns org.bluez (an adapter, devices, MediaPlayer1 players, the agent manager),
shanling.bluez (NotifyChanged) and org.bluealsa (the main daemon's PCMs). `fake_bluez.py sink CTL LOG` owns
org.bluealsa.sink, as the guest's daemon (`bluealsa -B sink`), which q2-guest starts itself. Each reads commands
a line at a time from the FIFO CTL and appends what is called on it to LOG, a line each.

The bluealsa daemons answer as stock's 4.0.0 (Q2_FAKE_BLUEALSA=stock, the default: GetPCMs, PCMAdded and
PCMRemoved, and the same PCMs through org.freedesktop.DBus.ObjectManager, each rate as Sampling) or Vamp's 5.0.0
(Q2_FAKE_BLUEALSA=own: the object manager alone, the rate as Rate), as test/bluealsa_own.py measured each
(docs/bluealsa.md#the-callers).

Run by the system python3 (dbus-python and PyGObject); the helper under test talks libdbus to it."""
import os
import sys

import dbus
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

role, ctl, log_path = sys.argv[1], sys.argv[2], sys.argv[3]
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
bus = dbus.SystemBus()
PROPS = 'org.freedesktop.DBus.Properties'


def log(*words):
    with open(log_path, 'a') as f:
        f.write(' '.join(str(w) for w in words) + '\n')


def dev_path(mac):
    return '/org/bluez/hci0/dev_' + mac.replace(':', '_')


class Obj(dbus.service.Object):
    """An object with properties per interface, Get/GetAll/Set, and PropertiesChanged."""
    def __init__(self, path, props):
        super().__init__(bus, path)
        self.path, self.props = path, props

    @dbus.service.method(PROPS, in_signature='ss', out_signature='v')
    def Get(self, iface, name):
        return self.props[iface][name]

    @dbus.service.method(PROPS, in_signature='s', out_signature='a{sv}')
    def GetAll(self, iface):
        return self.props.get(iface, {})

    @dbus.service.method(PROPS, in_signature='ssv')
    def Set(self, iface, name, value):
        self.props[iface][name] = value
        log('SET', self.path, name, value if not isinstance(value, dbus.Boolean) else bool(value))

    @dbus.service.signal(PROPS, signature='sa{sv}as')
    def PropertiesChanged(self, iface, changed, invalidated):
        pass


class Device(Obj):
    def __init__(self, mac, name):
        super().__init__(dev_path(mac), {'org.bluez.Device1': {'Alias': dbus.String(name), 'Address': mac}})

    @dbus.service.method('org.bluez.Device1')
    def Disconnect(self):
        log('DISCONNECT', self.path)

    @dbus.service.method('org.bluez.Device1', in_signature='s')
    def ConnectProfile(self, uuid):
        log('CONNECTPROFILE', self.path, uuid)


class Player(Obj):
    def __init__(self, mac):
        super().__init__(dev_path(mac) + '/player0', {'org.bluez.MediaPlayer1': {
            'Status': dbus.String('playing'), 'Position': dbus.UInt32(0),
            'Track': dbus.Dictionary({}, signature='sv')}})

    def change(self, **kv):
        p = self.props['org.bluez.MediaPlayer1']
        p.update(kv)
        self.PropertiesChanged('org.bluez.MediaPlayer1', kv, [])

    for _m in ('Play', 'Pause', 'Next', 'Previous'):
        exec(f'''@dbus.service.method('org.bluez.MediaPlayer1')\ndef {_m}(self):\n    log('PLAYER', '{_m}', self.path)''')


class Adapter(Obj):
    def __init__(self):
        super().__init__('/org/bluez/hci0', {'org.bluez.Adapter1': {
            'Alias': dbus.String('Q2'), 'Discoverable': dbus.Boolean(False), 'Pairable': dbus.Boolean(False)}})

    @dbus.service.method('org.bluez.Adapter1', in_signature='o')
    def RemoveDevice(self, path):
        log('REMOVEDEVICE', path)


class Root(dbus.service.Object):
    """/ (ObjectManager) and /org/bluez (AgentManager1) of BlueZ."""
    def __init__(self, objs):
        super().__init__(bus, '/')
        self.objs = objs

    @dbus.service.method('org.freedesktop.DBus.ObjectManager', out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        return {o.path: o.props for o in self.objs.values()}

    @dbus.service.signal('org.freedesktop.DBus.ObjectManager', signature='oa{sa{sv}}')
    def InterfacesAdded(self, path, ifaces):
        pass


class AgentManager(dbus.service.Object):
    def __init__(self):
        super().__init__(bus, '/org/bluez')
        self.agent = None

    @dbus.service.method('org.bluez.AgentManager1', in_signature='os', sender_keyword='sender')
    def RegisterAgent(self, path, cap, sender=None):
        self.agent = (sender, path)
        log('REGISTERAGENT', path, cap)

    @dbus.service.method('org.bluez.AgentManager1', in_signature='o')
    def RequestDefaultAgent(self, path):
        log('DEFAULTAGENT', path)

    @dbus.service.method('org.bluez.AgentManager1', in_signature='o')
    def UnregisterAgent(self, path):
        self.agent = None
        log('UNREGISTERAGENT', path)


class Shanling(dbus.service.Object):
    def __init__(self):
        super().__init__(bus, '/shanling/bluez')

    @dbus.service.method('shanling.bluez', in_signature='iay')
    def NotifyChanged(self, event, data):
        log('NOTIFY', hex(event), bytes(data).hex())


OWN = os.environ.get('Q2_FAKE_BLUEALSA') == 'own'
OM = 'org.freedesktop.DBus.ObjectManager'


class Alsa(dbus.service.Object):
    """One bluealsa daemon's /org/bluealsa: its object manager, and 4.0.0's Manager1 unless OWN."""
    def __init__(self):
        super().__init__(bus, '/org/bluealsa')
        self.pcms = {}

    @dbus.service.method(OM, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        log('BLUEALSA GetManagedObjects')
        return {path: {'org.bluealsa.PCM1': props} for path, props in self.pcms.items()}

    @dbus.service.signal(OM, signature='oa{sa{sv}}')
    def InterfacesAdded(self, path, ifaces):
        pass

    @dbus.service.signal(OM, signature='oas')
    def InterfacesRemoved(self, path, ifaces):
        pass

    @dbus.service.method('org.bluealsa.Manager1', out_signature='a{oa{sv}}')
    def GetPCMs(self):
        if OWN:
            raise dbus.exceptions.DBusException('no such method in 5.0.0', name='org.freedesktop.DBus.Error.UnknownMethod')
        log('BLUEALSA GetPCMs')
        return self.pcms

    @dbus.service.signal('org.bluealsa.Manager1', signature='oa{sv}')
    def PCMAdded(self, path, props):
        pass

    @dbus.service.signal('org.bluealsa.Manager1', signature='o')
    def PCMRemoved(self, path):
        pass

    def add(self, mac, sink, codec, rate, cfg):
        path = '/org/bluealsa/hci0/dev_%s/%s' % (mac.replace(':', '_'), 'a2dpsnk/source' if sink else 'a2dpsrc/sink')
        props = {'Device': dbus.ObjectPath(dev_path(mac)), 'Transport': 'A2DP-sink' if sink else 'A2DP-source',
                 'Mode': 'source' if sink else 'sink', 'Codec': codec, 'Rate' if OWN else 'Sampling': dbus.UInt32(int(rate)),
                 'CodecConfiguration': dbus.ByteArray(bytes.fromhex(cfg))}
        self.pcms[path] = props
        self.InterfacesAdded(path, {'org.bluealsa.PCM1': props})
        if not OWN:
            self.PCMAdded(path, props)

    def remove(self, mac):
        for path in [p for p in self.pcms if dev_path(mac).split('/')[-1] in p]:
            del self.pcms[path]
            self.InterfacesRemoved(path, ['org.bluealsa.PCM1'])
            if not OWN:
                self.PCMRemoved(path)


objs = {}
alsa = Alsa()
if role == 'main':
    objs['adapter'] = Adapter()
    root, agents, shanling = Root(objs), AgentManager(), Shanling()
    names = [dbus.service.BusName(n, bus) for n in ('org.bluez', 'shanling.bluez', 'org.bluealsa')]
else:
    names = [dbus.service.BusName('org.bluealsa.sink', bus)]


def agent_call(method, mac, *args):
    sender, path = agents.agent
    proxy = bus.get_object(sender, path)
    getattr(proxy, method)(dbus.ObjectPath(dev_path(mac)), *args, dbus_interface='org.bluez.Agent1',
                           reply_handler=lambda *_: log('AGENTREPLY', method, 'ok'),
                           error_handler=lambda e: log('AGENTREPLY', method, e.get_dbus_name()), timeout=120)


def command(line):
    w = line.split(' ')
    if w[0] == 'device':  # device MAC NAME...
        objs[w[1]] = Device(w[1], ' '.join(w[2:]))
    elif w[0] == 'pcm':  # pcm MAC CODEC RATE CFGHEX: a stream to the Q2 (sink) or, on main, an outbound one
        alsa.add(w[1], role == 'sink', w[2], w[3], w[4])
    elif w[0] == 'pcmin':  # pcmin MAC CODEC RATE CFGHEX: a stream to the Q2 on this daemon, as VampOS's one bluealsa has
        alsa.add(w[1], True, w[2], w[3], w[4])
    elif w[0] == 'unpcm':
        alsa.remove(w[1])
    elif w[0] == 'player':  # player MAC: its MediaPlayer1 appears
        p = objs['player ' + w[1]] = Player(w[1])
        root.InterfacesAdded(p.path, p.props)
    elif w[0] == 'track':  # track MAC Title|Artist|Album|Genre|TrackNumber|NumberOfTracks|Duration VALUE...
        p = objs['player ' + w[1]]
        t = dict(p.props['org.bluez.MediaPlayer1']['Track'])
        v = ' '.join(w[3:])
        t[w[2]] = dbus.UInt32(int(v)) if w[2] in ('TrackNumber', 'NumberOfTracks', 'Duration') else dbus.String(v)
        p.change(Track=dbus.Dictionary(t, signature='sv'))
    elif w[0] == 'status':  # status MAC playing|paused
        objs['player ' + w[1]].change(Status=dbus.String(w[2]))
    elif w[0] == 'confirm':
        agent_call('RequestConfirmation', w[1], dbus.UInt32(int(w[2])))
    elif w[0] == 'authorize':
        agent_call('RequestAuthorization', w[1])
    elif w[0] == 'cancel':
        sender, path = agents.agent
        bus.get_object(sender, path).Cancel(dbus_interface='org.bluez.Agent1')
    elif w[0] == 'quit':
        loop.quit()
    log('DONE', line)


fd = os.open(ctl, os.O_RDWR | os.O_NONBLOCK)
buf = b''


def readable(*_):
    global buf
    try:
        buf += os.read(fd, 4096)
    except BlockingIOError:
        return True
    while b'\n' in buf:
        line, buf = buf.split(b'\n', 1)
        command(line.decode())
    return True


GLib.io_add_watch(fd, GLib.IO_IN, readable)
log('UP', role)
loop = GLib.MainLoop()
loop.run()
