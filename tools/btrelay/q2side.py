"""`vamp bt`: the Q2's half of the Bluetooth relay, over adb. See docs/bt-relay.md#on-the-q2.

usage: vamp bt status                        the adapter, the daemons, the links and bluealsa's PCMs (reads only)
       vamp bt discoverable on [S] | off     let the laptop find the Q2 for S seconds (120 unless given)
       vamp bt connect ADDR | disconnect ADDR | forget ADDR
                                             bluetoothd's Connect and Disconnect for that device; forget removes
                                             its pairing from the Q2
       vamp bt codec [NAME]                  the stream's codec and those the sink offers; NAME selects one
                                             (SBC, AAC, aptX, aptX-HD, LDAC)
       vamp bt play [FILE.wav]               play the test signal (or FILE) through bluealsa, at the stream's rate
       vamp bt snoop install BUILD | start | stop [OUT]
                                             stock's hcidump from BUILD/stock.squashfs to the Q2; the HCI capture
                                             begun, and ended and pulled to OUT (btrelay.snoop unless given)

Everything a command runs on the Q2 is built here as shell text, so the unit tests read it; `dev` is vamp's
Device (shell, put) or a stand-in."""
import pathlib
import re
import subprocess

import btsignal

HCIDUMP = '/tmp/hcidump'
SNOOP = '/tmp/btrelay.snoop'
WAV = '/tmp/btrelay.wav'
# bluealsa's ALSA PCM for whichever sink is connected, as q2player opens it (docs/bluealsa.md#the-callers)
PCM = "plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp'"
CODECS = ('SBC', 'AAC', 'aptX', 'aptX-HD', 'LDAC')  # bluealsa's names, which SelectCodec takes
DBUS = 'dbus-send --system --print-reply --reply-timeout=30000'
BLUEZ = f'{DBUS} --dest=org.bluez'
PCMS = f'{DBUS} --dest=org.bluealsa /org/bluealsa org.freedesktop.DBus.ObjectManager.GetManagedObjects'
STATUS = '''echo "== adapter"; hciconfig hci0 2>&1 | head -4
echo "== daemons"; for d in bluetoothd bluealsa; do for p in $(pidof $d); do echo "$d $p $(readlink /proc/$p/exe)"; done; done
echo "== links"; hcitool con 2>&1
echo "== capture"; pidof hcidump >/dev/null && echo running || echo off; ls -l %s 2>/dev/null
echo "== pcms"; %s 2>&1''' % (SNOOP, PCMS)


def device_path(addr):
    """bluetoothd's object path for an address, which is checked: it goes into a shell command."""
    if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', addr):
        raise ValueError(f'not a Bluetooth address (AA:BB:CC:DD:EE:FF): {addr!r}')
    return '/org/bluez/hci0/dev_' + addr.upper().replace(':', '_')


def parse_pcms(text):
    """bluealsa's PCMs from dbus-send's print of GetManagedObjects: path, transport, mode, codec and rate
    (`Rate` in bluez-alsa 5, `Sampling` in stock's 4.0)."""
    pcms = []
    parts = re.split(r'object path "(/org/bluealsa/[^"]+)"\s+array \[', text)
    for path, part in zip(parts[1::2], parts[2::2]):
        def value(key, kind, part=part):
            m = re.search(rf'string "{key}"\s+variant\s+{kind}', part)
            return m.group(1) if m else None

        rate = value('Rate', r'uint32 (\d+)') or value('Sampling', r'uint32 (\d+)')
        pcms.append({'path': path, 'transport': value('Transport', 'string "([^"]*)"'),
                     'mode': value('Mode', 'string "([^"]*)"'), 'codec': value('Codec', 'string "([^"]*)"'),
                     'rate': int(rate) if rate else None})
    return [p for p in pcms if p.get('transport')]


def source_pcm(dev):
    """The PCM the Q2 plays into (A2DP source, playback), or None with no sink connected."""
    pcms = parse_pcms(dev.shell(PCMS + ' 2>&1').decode('utf-8', 'replace'))
    return next((p for p in pcms if p['transport'] == 'A2DP-source' and p['mode'] == 'sink'), None)


def _dbus(dev, out, command, timeout=40):
    """Run a dbus-send; its answer to `out`. 1 when D-Bus answered with an error."""
    text = dev.shell(command + ' 2>&1', timeout=timeout).decode('utf-8', 'replace').strip()
    if text:
        out(text)
    return 1 if re.search(r'^Error |\bError org\.', text, re.M) else 0


def _set(path, interface, name, value):
    return f'{BLUEZ} {path} org.freedesktop.DBus.Properties.Set string:{interface} string:{name} variant:{value}'


def _rc(text):
    """(output, status) from a command that ended with `echo rc=$?`."""
    m = re.search(r'\n?rc=(\d+)\s*\Z', text)
    return (text[:m.start()], int(m.group(1))) if m else (text, 0)


def main(dev, argv, out=print, pull=None):
    """One `vamp bt` command. `pull(remote, local)` fetches a file from the Q2 (vamp's pull)."""
    cmd, a = (argv[0], argv[1:]) if argv else ('', [])
    if cmd == 'status' and not a:
        text = dev.shell(STATUS).decode('utf-8', 'replace')
        head, _, objects = text.partition('== pcms')
        out(head.rstrip())
        pcms = parse_pcms(objects)
        for p in pcms:
            out(f'pcm {p["transport"]} {p["mode"]}: {p["codec"]} {p["rate"]} Hz  {p["path"]}')
        if not pcms:
            out('pcm: none (no audio device connected, or bluealsa is not running)')
        return 0
    if cmd == 'discoverable' and a and a[0] == 'on' and len(a) <= 2:
        seconds = int(a[1]) if len(a) == 2 else 120
        return _dbus(dev, out, _set('/org/bluez/hci0', 'org.bluez.Adapter1', 'DiscoverableTimeout', f'uint32:{seconds}')
                     + ' && ' + _set('/org/bluez/hci0', 'org.bluez.Adapter1', 'Discoverable', 'boolean:true'))
    if cmd == 'discoverable' and a == ['off']:
        return _dbus(dev, out, _set('/org/bluez/hci0', 'org.bluez.Adapter1', 'Discoverable', 'boolean:false'))
    if cmd in ('connect', 'disconnect') and len(a) == 1:
        return _dbus(dev, out, f'{BLUEZ} {device_path(a[0])} org.bluez.Device1.{cmd.capitalize()}')
    if cmd == 'forget' and len(a) == 1:
        return _dbus(dev, out, f'{BLUEZ} /org/bluez/hci0 org.bluez.Adapter1.RemoveDevice objpath:{device_path(a[0])}')
    if cmd == 'codec' and len(a) <= 1:
        name = next((c for c in CODECS if a and c.lower() == a[0].lower()), None)
        if a and not name:
            raise ValueError(f'codec is one of {", ".join(CODECS)}')
        pcm = source_pcm(dev)
        if not pcm:
            out('no A2DP source PCM: connect a sink first (vamp bt connect ADDR)')
            return 1
        if name:
            text, status = _rc(dev.shell(f'q2bt-codec {pcm["path"]} {name} 2>&1; echo rc=$?', timeout=40).decode('utf-8', 'replace'))
            if status:
                out(f'{name} refused: {text.strip() or "q2bt-codec exit " + str(status)}')
                return 1
            pcm = source_pcm(dev) or pcm
        out(f'{pcm["codec"]} {pcm["rate"]} Hz  {pcm["path"]}')
        offered = dev.shell(f'{DBUS} --dest=org.bluealsa {pcm["path"]} org.bluealsa.PCM1.GetCodecs 2>&1').decode('utf-8', 'replace')
        names = [n for n in re.findall(r'string "([^"]+)"', offered) if n in CODECS]
        out(f'offered: {" ".join(dict.fromkeys(names)) or "unknown (GetCodecs gave no names)"}')
        return 0
    if cmd == 'play' and len(a) <= 1:
        pcm = source_pcm(dev)
        if not pcm or not pcm['rate']:
            out('no A2DP source PCM: connect a sink first (vamp bt connect ADDR)')
            return 1
        data = pathlib.Path(a[0]).read_bytes() if a else btsignal.wav(pcm['rate'], 16)
        dev.put(data, WAV)
        out(f'playing {a[0] if a else "the test signal"} as {pcm["codec"]} at {pcm["rate"]} Hz')
        text, status = _rc(dev.shell(f'aplay -q -D "{PCM}" {WAV} 2>&1; echo rc=$?', timeout=120).decode('utf-8', 'replace'))
        if status:
            out(text.strip())
            out('aplay failed. bluealsa gives its PCM to one client: pause the Q2 first when it says busy')
            return 1
        return 0
    if cmd == 'snoop' and len(a) == 2 and a[0] == 'install':
        image = pathlib.Path(a[1]) / 'stock.squashfs'
        if not image.is_file():
            raise ValueError(f'{image}: a build directory holds stock.squashfs (tools/ci/rebuild.py prints one)')
        dev.put(subprocess.check_output(['unsquashfs', '-cat', str(image), 'usr/bin/hcidump']), HCIDUMP, 0o755)
        out(f'stock\'s hcidump is at {HCIDUMP} until the Q2 restarts')
        return 0
    if cmd == 'snoop' and a == ['start']:
        text, status = _rc(dev.shell(
            f'[ -x {HCIDUMP} ] || {{ echo "no {HCIDUMP}"; echo rc=1; exit; }}\n'
            f'pidof hcidump >/dev/null && {{ echo "a capture is already running"; echo rc=1; exit; }}\n'
            f'setsid sh -c "exec {HCIDUMP} -w {SNOOP}" </dev/null >/dev/null 2>&1 &\n'
            'sleep 1; pidof hcidump >/dev/null; echo rc=$?').decode('utf-8', 'replace'))
        if status:
            out(text.strip() or 'hcidump did not stay up')
            if 'no ' + HCIDUMP in text:
                out('push it first: vamp bt snoop install BUILD')
            return 1
        out(f'capturing to {SNOOP} on the Q2 (RAM: stop it within a few minutes of audio)')
        return 0
    if cmd == 'snoop' and a[:1] == ['stop'] and len(a) <= 2:
        dev.shell('p=$(pidof hcidump) && kill -INT $p; sleep 1')
        local = a[1] if len(a) == 2 else 'btrelay.snoop'
        if pull is None:
            raise ValueError('snoop stop needs a way to pull the capture: run it as `vamp bt snoop stop`')
        pull(SNOOP, local)
        out(f'wrote {local}: python3 tools/btrelay/btsnoop.py {local}')
        return 0
    raise ValueError(__doc__[__doc__.index('usage:'):])
