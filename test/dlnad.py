#!/usr/bin/env python3
"""q2dlnad (dlnad/q2dlnad.c, docs/dlnad.md) built for this machine against stand-ins for Q2PLAY/1 and Q2NET/1: its
switch in config.ini, SSDP's answers, the device and service descriptions, the AVTransport, RenderingControl and
ConnectionManager actions and what each sends Q2PLAY/1, GENA's events, and the radio held while it plays; and once
against the real q2playd (test/playd.py's build and stand-in player), the URL reaching the player. Run it as
python3 test/dlnad.py (pytest). Q2DLNAD_SOURCE builds another copy of q2dlnad.c, to show a broken one fails here."""
import http.server
import os
import pathlib
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2DLNAD_SOURCE') or ROOT/'dlnad/q2dlnad.c')
UPNP = {'d': 'urn:schemas-upnp-org:device-1-0', 's': 'urn:schemas-upnp-org:service-1-0'}
UUID = 'uuid:0a2f2a3e-5661-6d70-0000-aabbcc001122'
CONFIG_ON = '[PLAYSET]\nVOLUME=30\n[SYSSET]\nLANGUAGE=2\nDLNA=1\n'


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('dlnad')
    src = out/'q2dlnad.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2dlnad'), str(src)], check=True)
    return out/'q2dlnad'


def free_port(kind=socket.SOCK_STREAM):
    s = socket.socket(socket.AF_INET, kind)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Play:
    """Q2PLAY/1's side: what q2dlnad sent, and lines to say to it"""
    def __init__(self, sock_path):
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(sock_path))
        self.srv.listen(1)
        self.lines, self.c = [], None
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        self.c, _ = self.srv.accept()
        try:
            self.read(self.c.makefile('r'))
        except OSError:  # q2dlnad stopped
            pass

    def read(self, f):
        for line in f:
            line = line.rstrip('\n')
            self.lines.append(line)
            if line == 'Q2PLAY/1':
                self.say('Q2PLAY/1 ok')
            elif line.startswith('source '):
                self.say('ok')
                self.say('now title=T artist=A album=B state=1 pos=0 len=0 volume=30 id=0')
            else:
                self.say('ok')

    def say(self, line):
        self.c.sendall((line + '\n').encode())

    def wait(self, pred, secs=3.0):
        end = time.time() + secs
        while time.time() < end:
            hit = [l for l in self.lines if pred(l)]
            if hit:
                return hit[-1]
            time.sleep(0.02)
        raise AssertionError(f'not sent; sent: {self.lines}')


class Net:
    """Q2NET/1's side: the holds asked for"""
    def __init__(self, sock_path):
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(sock_path))
        self.srv.listen(4)
        self.lines = []
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            c, _ = self.srv.accept()
            self.lines += c.makefile('r').read().splitlines()
            c.close()


class Dlna:
    def __init__(self, root, port, ssdp):
        self.root, self.port, self.ssdp = root, port, ssdp

    def url(self, p):
        return f'http://127.0.0.1:{self.port}{p}'

    def get(self, p):
        with urllib.request.urlopen(self.url(p), timeout=3) as r:
            return r.read().decode()

    def soap(self, service, action, **args):
        body = (f'<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body><u:{action} '
                f'xmlns:u="urn:schemas-upnp-org:service:{service}:1"><InstanceID>0</InstanceID>'
                + ''.join(f'<{k}>{v}</{k}>' for k, v in args.items()) + f'</u:{action}></s:Body></s:Envelope>')
        req = urllib.request.Request(self.url(f'/ctl/{service}'), data=body.encode(), method='POST', headers={
            'Content-Type': 'text/xml; charset="utf-8"', 'SOAPACTION': f'"urn:schemas-upnp-org:service:{service}:1#{action}"'})
        try:
            with urllib.request.urlopen(req, timeout=3) as r:
                text = r.read().decode()
        except urllib.error.HTTPError as e:
            text = e.read().decode()
            return 'fault', re.search(r'<errorCode>(\d+)</errorCode>', text).group(1)
        resp = ET.fromstring(text).find('.//{urn:schemas-upnp-org:service:%s:1}%sResponse' % (service, action))
        return {c.tag: c.text or '' for c in resp}

    def avt(self, action, **args):
        return self.soap('AVTransport', action, **args)

    def state(self):
        return self.avt('GetTransportInfo')['CurrentTransportState']

    def until(self, want, secs=3.0):
        end = time.time() + secs
        while time.time() < end and self.state() != want:
            time.sleep(0.05)
        return self.state()


def up(port, secs=8.0):
    end = time.time() + secs
    while time.time() < end:
        try:
            socket.create_connection(('127.0.0.1', port), 0.2).close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


@pytest.fixture
def config():
    return CONFIG_ON


@pytest.fixture
def world(binary, tmp_path, config):
    for d in ('run/vamp', 'mnt/data', 'sys/class/net/wlan0'):
        (tmp_path/d).mkdir(parents=True)
    (tmp_path/'sys/class/net/wlan0/address').write_text('AA:BB:CC:00:11:22\n')
    (tmp_path/'mnt/data/config.ini').write_text(config)
    play, net = Play(tmp_path/'run/vamp/play.sock'), Net(tmp_path/'run/vamp/net.sock')
    port, ssdp = free_port(), free_port(socket.SOCK_DGRAM)
    p = subprocess.Popen([str(binary), '--root', str(tmp_path), '--port', str(port), '--ssdp-port', str(ssdp)],
                         stderr=subprocess.PIPE)
    d = Dlna(tmp_path, port, ssdp)
    if config == CONFIG_ON:
        assert up(port), 'on with DLNA=1'
        play.wait(lambda l: l == 'watch')
    yield d, play, net
    p.terminate()
    assert p.wait(5) == 0, 'q2dlnad ends on SIGTERM'


# ---- on and off ----

@pytest.mark.parametrize('config', [CONFIG_ON.replace('DLNA=1', 'DLNA=0')])
def test_it_listens_only_while_its_setting_is_on(world):
    d, play, net = world
    assert not up(d.port, 1.5), "off: stock's default"
    (d.root/'mnt/data/config.ini').write_text(CONFIG_ON)
    assert up(d.port), 'on within the 5 s it reads the setting'
    (d.root/'mnt/data/config.ini').write_text(CONFIG_ON.replace('DLNA=1', 'DLNA=0'))
    end = time.time() + 8
    while time.time() < end and up(d.port, 0.2):
        time.sleep(0.3)
    assert not up(d.port, 0.2), 'off again'


# ---- SSDP ----

def search(d, st, man='"ssdp:discover"'):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.7)
    s.sendto((f'M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: {man}\r\nMX: 1\r\nST: {st}\r\n\r\n').encode(),
             ('127.0.0.1', d.ssdp))
    out = []
    try:
        while True:
            out.append(s.recv(2048).decode())
    except socket.timeout:
        pass
    return [dict(l.split(': ', 1) if ': ' in l else (l.rstrip(':'), '') for l in m.split('\r\n')[1:] if l) for m in out]


def test_a_search_for_a_renderer_is_answered_with_its_description(world):
    d, play, net = world
    got = search(d, 'urn:schemas-upnp-org:device:MediaRenderer:1')
    assert len(got) == 1
    a = got[0]
    assert a['LOCATION'] == f'http://127.0.0.1:{d.port}/description.xml'
    assert a['USN'] == UUID + '::urn:schemas-upnp-org:device:MediaRenderer:1' and 'EXT' in a
    assert a['CACHE-CONTROL'] == 'max-age=1800'
    assert {x['ST'] for x in search(d, 'ssdp:all')} == {
        'upnp:rootdevice', UUID, 'urn:schemas-upnp-org:device:MediaRenderer:1',
        *(f'urn:schemas-upnp-org:service:{s}:1' for s in ('AVTransport', 'RenderingControl', 'ConnectionManager'))}
    assert search(d, 'urn:schemas-upnp-org:device:MediaServer:1') == [], 'not a server'
    assert search(d, 'ssdp:all', man='ssdp:discover') == [], 'MAN must be quoted (UDA 1.0)'


# ---- descriptions ----

REQUIRED = {  # UPnP AV 1: each service's required actions
    'AVTransport': {'SetAVTransportURI', 'GetMediaInfo', 'GetTransportInfo', 'GetPositionInfo', 'GetDeviceCapabilities',
                    'GetTransportSettings', 'Stop', 'Play', 'Seek', 'Next', 'Previous'},
    'RenderingControl': {'ListPresets', 'SelectPreset'},
    'ConnectionManager': {'GetProtocolInfo', 'GetCurrentConnectionIDs', 'GetCurrentConnectionInfo'}}


def test_the_device_and_its_services_are_described(world):
    d, play, net = world
    dev = ET.fromstring(d.get('/description.xml')).find('d:device', UPNP)
    assert dev.findtext('d:deviceType', namespaces=UPNP) == 'urn:schemas-upnp-org:device:MediaRenderer:1'
    assert dev.findtext('d:UDN', namespaces=UPNP) == UUID and dev.findtext('d:friendlyName', namespaces=UPNP) == 'Vamp Q2'
    services = dev.findall('d:serviceList/d:service', UPNP)
    assert [s.findtext('d:serviceType', namespaces=UPNP) for s in services] == [
        f'urn:schemas-upnp-org:service:{n}:1' for n in REQUIRED]
    for s in services:
        name = s.findtext('d:serviceType', namespaces=UPNP).split(':')[3]
        scpd = ET.fromstring(d.get(s.findtext('d:SCPDURL', namespaces=UPNP)))
        actions = {a.findtext('s:name', namespaces=UPNP): a for a in scpd.findall('s:actionList/s:action', UPNP)}
        assert REQUIRED[name] <= set(actions), name
        variables = {v.findtext('s:name', namespaces=UPNP) for v in scpd.findall('s:serviceStateTable/s:stateVariable', UPNP)}
        for a in actions.values():
            for arg in a.findall('s:argumentList/s:argument', UPNP):
                assert arg.findtext('s:direction', namespaces=UPNP) in ('in', 'out')
                assert arg.findtext('s:relatedStateVariable', namespaces=UPNP) in variables, (name, arg.findtext('s:name', namespaces=UPNP))
    assert d.soap('ConnectionManager', 'GetProtocolInfo')['Sink'].startswith('http-get:*:audio/mpeg:*,')


# ---- transport ----

DIDL = ('&lt;DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/"&gt;&lt;item id="1" parentID="0" restricted="1"&gt;'
        '&lt;dc:title&gt;Caf&amp;amp;eacute; &amp;amp; Bar&lt;/dc:title&gt;&lt;upnp:artist&gt;Ann Lee&lt;/upnp:artist&gt;'
        '&lt;upnp:album&gt;First=One&lt;/upnp:album&gt;&lt;res protocolInfo="http-get:*:audio/flac:*"&gt;x&lt;/res&gt;'
        '&lt;/item&gt;&lt;/DIDL-Lite&gt;')
URL = 'http://10.0.0.5:9000/music/22.flac?a=1&amp;b=2'


def test_a_uri_and_play_become_a_q2play1_source_and_the_transport_follows_it(world):
    d, play, net = world
    assert d.state() == 'NO_MEDIA_PRESENT'
    assert d.avt('Play', Speed=1) == ('fault', '701'), 'nothing to play'
    assert d.avt('SetAVTransportURI', CurrentURI=URL, CurrentURIMetaData=DIDL) == {}
    assert d.state() == 'STOPPED' and not [l for l in play.lines if l.startswith('source')], 'set, not played'
    assert d.avt('Play', Speed=1) == {}
    line = play.wait(lambda l: l.startswith('source '))
    assert line == 'source http://10.0.0.5:9000/music/22.flac?a%3D1&b%3D2 title=Caf&eacute;%20&%20Bar artist=Ann%20Lee album=First%3DOne'
    assert d.until('PLAYING') == 'PLAYING'
    assert d.avt('GetMediaInfo')['CurrentURI'] == 'http://10.0.0.5:9000/music/22.flac?a=1&b=2'
    play.say('now title=T artist=A album=B state=1 pos=12 len=200 volume=30 id=0')
    time.sleep(0.3)
    pos = d.avt('GetPositionInfo')
    assert (pos['RelTime'], pos['TrackDuration'], pos['Track']) == ('0:00:12', '0:03:20', '1')
    assert 'Ann Lee' in pos['TrackMetaData'], 'the metadata as it came'
    assert d.avt('GetCurrentTransportActions')['Actions'] == 'Pause,Stop,Seek'
    assert d.avt('Pause') == {}
    play.wait(lambda l: l == 'pause')
    play.say('now title=T artist=A album=B state=2 pos=12 len=200 volume=30 id=0')
    assert d.until('PAUSED_PLAYBACK') == 'PAUSED_PLAYBACK'
    assert d.avt('Play', Speed=1) == {}
    play.wait(lambda l: l == 'resume')
    assert d.avt('Seek', Unit='REL_TIME', Target='1:01:05.250') == {}
    play.wait(lambda l: l == 'seek 3665')
    assert d.avt('Seek', Unit='TRACK_NR', Target='2') == ('fault', '710')
    assert d.avt('Next') == ('fault', '701')
    assert d.avt('Stop') == {}
    play.wait(lambda l: l == 'focus release')
    assert d.state() == 'STOPPED'


def test_a_source_that_ends_or_is_taken_stops_the_transport(world):
    d, play, net = world
    d.avt('SetAVTransportURI', CurrentURI='http://10.0.0.5/a.mp3', CurrentURIMetaData='')
    d.avt('Play', Speed=1)
    assert d.until('PLAYING') == 'PLAYING'
    play.say('focus lost why=end')
    assert d.until('STOPPED') == 'STOPPED'
    n = len([l for l in play.lines if l.startswith('source')])
    d.avt('Play', Speed=1)
    play.wait(lambda l: len([x for x in play.lines if x.startswith('source')]) > n)
    assert d.until('PLAYING') == 'PLAYING', 'played again as a new source'
    play.say('focus lost why=taken')
    assert d.until('STOPPED') == 'STOPPED'
    assert d.avt('Pause') == ('fault', '701'), 'no longer its to pause'


def test_a_new_uri_while_playing_plays_at_once(world):
    d, play, net = world
    d.avt('SetAVTransportURI', CurrentURI='http://10.0.0.5/a.mp3', CurrentURIMetaData='')
    d.avt('Play', Speed=1)
    assert d.until('PLAYING') == 'PLAYING'
    d.avt('SetAVTransportURI', CurrentURI='http://10.0.0.5/b.mp3', CurrentURIMetaData='')
    play.wait(lambda l: l == 'source http://10.0.0.5/b.mp3')


def test_the_volume_and_the_mute(world):
    d, play, net = world
    rc = lambda a, **k: d.soap('RenderingControl', a, Channel='Master', **k)  # noqa: E731
    assert rc('SetVolume', DesiredVolume=40) == {}
    play.wait(lambda l: l == 'volume 40')
    play.say('now title= artist= album= state=0 pos=0 len=0 volume=40 id=0')
    time.sleep(0.3)
    assert rc('GetVolume')['CurrentVolume'] == '40'
    assert rc('SetVolume', DesiredVolume=101) == ('fault', '402')
    assert rc('SetMute', DesiredMute=1) == {}
    play.wait(lambda l: l == 'volume 0')
    play.say('now title= artist= album= state=0 pos=0 len=0 volume=0 id=0')
    time.sleep(0.3)
    assert (rc('GetMute')['CurrentMute'], rc('GetVolume')['CurrentVolume']) == ('1', '40'), 'muted, the level kept'
    assert rc('SetMute', DesiredMute=0) == {}
    play.wait(lambda l: l == 'volume 40' and play.lines.count('volume 40') == 2)
    assert d.soap('RenderingControl', 'Dance') == ('fault', '401')


def test_the_radio_is_held_while_it_plays(world):
    d, play, net = world
    d.avt('SetAVTransportURI', CurrentURI='http://10.0.0.5/a.mp3', CurrentURIMetaData='')
    time.sleep(0.8)
    assert net.lines == [], 'not before it plays'
    d.avt('Play', Speed=1)
    end = time.time() + 3
    while time.time() < end and 'hold 120' not in net.lines:
        time.sleep(0.05)
    assert net.lines[:2] == ['Q2NET/1', 'hold 120']


# ---- events ----

class Callback(http.server.BaseHTTPRequestHandler):
    got = []

    def do_NOTIFY(self):
        body = self.rfile.read(int(self.headers['Content-Length'])).decode()
        Callback.got.append((dict(self.headers), body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


def transport_states():
    out = []
    for _, body in Callback.got:
        last = ET.fromstring(body).find('.//LastChange')
        if last is not None and last.text and 'TransportState' in last.text:
            out.append(ET.fromstring(last.text).find('.//{urn:schemas-upnp-org:metadata-1-0/AVT/}TransportState').get('val'))
    return out


def test_subscribers_hear_the_transport_change(world):
    d, play, net = world
    Callback.got = []
    srv = http.server.HTTPServer(('127.0.0.1', 0), Callback)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    cb = f'<http://127.0.0.1:{srv.server_address[1]}/cb>'
    req = urllib.request.Request(d.url('/evt/AVTransport'), method='SUBSCRIBE',
                                 headers={'CALLBACK': cb, 'NT': 'upnp:event', 'TIMEOUT': 'Second-300'})
    with urllib.request.urlopen(req, timeout=3) as r:
        sid, timeout = r.headers['SID'], r.headers['TIMEOUT']
    assert sid.startswith('uuid:') and timeout == 'Second-300'
    end = time.time() + 3
    while time.time() < end and not Callback.got:
        time.sleep(0.05)
    head, _ = Callback.got[0]
    assert (head['SID'], head['SEQ'], head['NT'], head['NTS']) == (sid, '0', 'upnp:event', 'upnp:propchange')
    assert transport_states() == ['NO_MEDIA_PRESENT'], 'the first event: every evented variable'
    d.avt('SetAVTransportURI', CurrentURI='http://10.0.0.5/a.mp3', CurrentURIMetaData='')
    d.avt('Play', Speed=1)
    end = time.time() + 3
    while time.time() < end and 'PLAYING' not in transport_states():
        time.sleep(0.05)
    assert transport_states()[-1] == 'PLAYING' and 'STOPPED' in transport_states()
    seqs = [int(h['SEQ']) for h, _ in Callback.got]
    assert seqs == list(range(len(seqs))), 'SEQ counts up from 0'
    renew = urllib.request.Request(d.url('/evt/AVTransport'), method='SUBSCRIBE', headers={'SID': sid, 'TIMEOUT': 'Second-60'})
    with urllib.request.urlopen(renew, timeout=3) as r:
        assert r.headers['TIMEOUT'] == 'Second-60'
    unsub = urllib.request.Request(d.url('/evt/AVTransport'), method='UNSUBSCRIBE', headers={'SID': sid})
    assert urllib.request.urlopen(unsub, timeout=3).status == 200
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(unsub, timeout=3)
    assert e.value.code == 412, 'gone'
    srv.shutdown()


# ---- with the real q2playd ----

def test_with_q2playd_the_url_reaches_the_player_and_its_end_stops_the_transport(binary, tmp_path, tmp_path_factory):
    import playd
    pbin = playd.build(tmp_path_factory.mktemp('playd'))
    for d in ('tmp', 'run/vamp', 'mnt/data', 'dev', 'sys/class/net/wlan0'):
        (tmp_path/d).mkdir(parents=True, exist_ok=True)
    (tmp_path/'mnt/data/config.ini').write_text(CONFIG_ON)
    playd.conformance.accounts(tmp_path)
    q2playd = subprocess.Popen([str(pbin), '--root', str(tmp_path)], stderr=subprocess.DEVNULL)
    player = playd.Player(tmp_path)
    port, ssdp = free_port(), free_port(socket.SOCK_DGRAM)
    dl = subprocess.Popen([str(binary), '--root', str(tmp_path), '--port', str(port), '--ssdp-port', str(ssdp)], stderr=subprocess.DEVNULL)
    try:
        assert up(port)
        d = Dlna(tmp_path, port, ssdp)
        time.sleep(0.5)
        d.avt('SetAVTransportURI', CurrentURI=URL, CurrentURIMetaData=DIDL)
        d.avt('Play', Speed=1)
        player.pump(1.0)
        assert player.got[-1] == ('set-songurl', 'http://10.0.0.5:9000/music/22.flac?a=1&b=2|ss=0|512|20|0|17|90')
        assert d.until('PLAYING') == 'PLAYING'
        player.say('player-status', 'exit')
        player.pump(0.5)
        assert d.until('STOPPED') == 'STOPPED', "the URL's end: focus lost why=end"
    finally:
        dl.terminate(), q2playd.terminate()
        dl.wait(5), q2playd.wait(5)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
