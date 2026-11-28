#!/usr/bin/env python3
"""Vamp's own bluetoothd (tools/thirdparty.py's `bluez`, docs/bluez.md) beside stock's, each run as MIPS under
qemu-mipsel on a D-Bus bus of its own, with a stand-in for the kernel: no radio, no Q2.

    python3 test/bluez_own.py BUILD|STOCKFS [--only own|stock] [--own BLUETOOTHD] [--keep]

BUILD is a build (tools/build.py's --out): stock's programs and libraries are unpacked from its stock.squashfs,
and when it was made with --own-bluez the own daemon is the one its rootfs.squashfs carries; otherwise, and
with STOCKFS (an unpacked stock root filesystem), it is tools/thirdparty.py's build, made if the cache has
none. Needs qemu-mipsel, bwrap, dbus-daemon, gdbus, unsquashfs for a BUILD, and the Codescape toolchain
tools/thirdparty.py caches.

What stands in for the kernel: a preloaded library (SHIM, compiled for MIPS) hands the daemon a UNIX socket
wherever it asks for a Bluetooth one. The management socket (HCI_CHANNEL_CONTROL) reaches Kernel below, which
answers the management protocol for one powered-off dual-mode controller, hci0, in the shapes Linux 5.10's
revision 18 uses, and keeps every command it was sent; L2CAP, RFCOMM and SCO sockets bind and listen to
nothing. So this proves what the daemon does with its configuration, its store and its D-Bus callers, and
what it asks of the kernel, never what a kernel or a radio answers.

Checked for each daemon: it starts on Vamp's main.conf and owns org.bluez; hci0 carries the interfaces the
firmware's callers use; the name, class and discoverable timeout come from main.conf; a pairing record in the
store (/mnt/data/bluetooth, a made-up device) becomes a paired Device1 and its link key is loaded into the
kernel; the calls the payload makes (docs/bluez.md#what-depends-on-it) are answered; nothing is bound on the LE
attribute channel (CID 4: the shim records every L2CAP bind), and the own daemon, with main.conf's [GATT]
Server = false, has no GattManager1 or LEAdvertisingManager1, as stock has none. Then the store one
daemon has written is read by the other, both ways: the key is loaded again and the alias set under the
first is the one the second reports.
"""
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))  # first: test/ has a thirdparty.py of its own
import thirdparty  # noqa: E402

# q2-heavy's preload makes bwrap non-dumpable and its uid map is then refused (test/thirdparty.py)
NO_PRELOAD = {k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'}

OWN = 'usr/lib/vamp-bluez/bluetoothd'  # tools/build.py OWN_BLUETOOTHD
# what the own daemon is started with: the plugins 5.88 compiles in that stock has no like of (docs/bluez.md#build)
OWN_OPTIONS = ['--noplugin=hfp,rap,fastpair']
ATT_CID = 4  # the LE attribute channel, which coda-rc serves itself (docs/bluez.md#the-le-attribute-channel)
ADAPTER = '00:11:22:33:44:55'  # made up, as is the device
DEVICE = 'AA:BB:CC:DD:EE:01'
DEVICE_PATH = '/org/bluez/hci0/dev_' + DEVICE.replace(':', '_')
LINK_KEY = '00112233445566778899AABBCCDDEEFF'
INFO = f"""[General]
Name=Fixture Buds
Class=0x240404
SupportedTechnologies=BR/EDR;
Trusted=true
Blocked=false
Services=0000110b-0000-1000-8000-00805f9b34fb;0000110e-0000-1000-8000-00805f9b34fb;

[LinkKey]
Key={LINK_KEY}
Type=4
PINLength=0
"""
# Vamp's edits to stock's main.conf (tools/build.py BT_DISCOVERABLE, BT_NAME, BT_EXPERIMENTAL, and BT_GATT_SERVER
# for an --own-bluez build), made here on the stock file the same way
sys.path.insert(1, str(ROOT / 'tools'))
from build import BT_DISCOVERABLE, BT_NAME, BT_EXPERIMENTAL, BT_GATT_SERVER  # noqa: E402
MAIN_EDITS = (BT_DISCOVERABLE, BT_NAME, BT_EXPERIMENTAL, BT_GATT_SERVER)

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path={path}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/>
<allow own="*"/><allow user="*"/></policy></busconfig>
"""

SHIM = r'''
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#define AF_BT 31
#define BTPROTO_HCI 1
#define HCI_CHANNEL_CONTROL 3
enum { NONE, HCI, OTHER };
static unsigned char kind[1024];

static void *next(const char *name) { return dlsym(RTLD_NEXT, name); }
static int ours(int fd) { return fd >= 0 && fd < (int)sizeof kind ? kind[fd] : NONE; }

int socket(int domain, int type, int protocol) {
    int (*real)(int, int, int) = next("socket");
    if (domain != AF_BT) return real(domain, type, protocol);
    int fd = real(AF_UNIX, SOCK_SEQPACKET | (type & (SOCK_CLOEXEC | SOCK_NONBLOCK)), 0);
    if (fd >= 0 && fd < (int)sizeof kind) kind[fd] = protocol == BTPROTO_HCI ? HCI : OTHER;
    return fd;
}

/* struct sockaddr_hci: family u16, device u16, channel u16. struct sockaddr_l2: family u16, psm u16, bdaddr 6,
 * cid u16, bdaddr_type u8: each L2CAP bind is written to Q2_FAKE_BINDS as "PSM CID TYPE", so the test sees which
 * channels the daemon would have served (CID 4 is the LE attribute channel) */
int bind(int fd, const struct sockaddr *addr, socklen_t len) {
    int (*real)(int, const struct sockaddr *, socklen_t) = next("bind");
    if (!ours(fd)) return real(fd, addr, len);
    if (ours(fd) == OTHER && len >= 13 && getenv("Q2_FAKE_BINDS")) {
        const unsigned char *a = (const void *)addr;
        char line[48];
        int out = open(getenv("Q2_FAKE_BINDS"), O_WRONLY | O_CREAT | O_APPEND, 0600);
        int n = snprintf(line, sizeof line, "%u %u %u\n", a[2] | a[3] << 8, a[10] | a[11] << 8, a[12]);
        if (out >= 0) {
            if (write(out, line, n) < 0) {}
            close(out);
        }
    }
    if (ours(fd) == HCI && len >= 6 && ((const unsigned short *)addr)[2] == HCI_CHANNEL_CONTROL) {
        int (*conn)(int, const struct sockaddr *, socklen_t) = next("connect");
        struct sockaddr_un un = {.sun_family = AF_UNIX};
        const char *path = getenv("Q2_FAKE_MGMT");
        int flags = fcntl(fd, F_GETFL);
        if (!path) return errno = ENODEV, -1;
        strncpy(un.sun_path, path, sizeof un.sun_path - 1);
        fcntl(fd, F_SETFL, flags & ~O_NONBLOCK);
        int r = conn(fd, (void *)&un, sizeof un);
        fcntl(fd, F_SETFL, flags);
        return r;
    }
    return 0;
}

/* a listener nothing can reach: an address of the kernel's choosing in the abstract namespace, so the daemon's
 * main loop sleeps on it as it would on a Bluetooth one (a socket not listening polls as hung up) */
int listen(int fd, int backlog) {
    int (*real)(int, int) = next("listen");
    if (ours(fd)) {
        int (*real_bind)(int, const struct sockaddr *, socklen_t) = next("bind");
        struct sockaddr_un un = {.sun_family = AF_UNIX};
        real_bind(fd, (void *)&un, sizeof(sa_family_t));
    }
    return real(fd, backlog);
}

int setsockopt(int fd, int level, int name, const void *value, socklen_t len) {
    int (*real)(int, int, int, const void *, socklen_t) = next("setsockopt");
    return ours(fd) && level != SOL_SOCKET ? 0 : real(fd, level, name, value, len);
}

int getsockopt(int fd, int level, int name, void *value, socklen_t *len) {
    int (*real)(int, int, int, void *, socklen_t *) = next("getsockopt");
    if (!ours(fd) || level == SOL_SOCKET) return real(fd, level, name, value, len);
    memset(value, 0, *len);
    return 0;
}

/* a program built with 64-bit time_t against glibc 2.34 or later (VampOS's) calls these in place of the two
 * above; stock's glibc 2.28 has neither, and nothing there calls them */
int __setsockopt64(int fd, int level, int name, const void *value, socklen_t len) {
    int (*real)(int, int, int, const void *, socklen_t) = next("__setsockopt64");
    return ours(fd) && level != SOL_SOCKET ? 0 : real(fd, level, name, value, len);
}

int __getsockopt64(int fd, int level, int name, void *value, socklen_t *len) {
    int (*real)(int, int, int, void *, socklen_t *) = next("__getsockopt64");
    if (!ours(fd) || level == SOL_SOCKET) return real(fd, level, name, value, len);
    memset(value, 0, *len);
    return 0;
}

int getsockname(int fd, struct sockaddr *addr, socklen_t *len) {
    int (*real)(int, struct sockaddr *, socklen_t *) = next("getsockname");
    if (!ours(fd)) return real(fd, addr, len);
    memset(addr, 0, *len);
    addr->sa_family = AF_BT;
    return 0;
}

int close(int fd) {
    int (*real)(int) = next("close");
    if (ours(fd)) kind[fd] = NONE;
    return real(fd);
}
'''

# the management protocol (BlueZ doc/mgmt-api.txt): opcodes this stand-in answers
READ_VERSION, READ_COMMANDS, READ_INDEX_LIST, READ_INFO = 1, 2, 3, 4
SET_DEV_CLASS, SET_LOCAL_NAME, ADD_UUID, REMOVE_UUID = 0x0e, 0x0f, 0x10, 0x11
LOAD_LINK_KEYS, LOAD_LTKS, GET_CONNECTIONS, SET_IO_CAPABILITY, UNPAIR_DEVICE = 0x12, 0x13, 0x15, 0x18, 0x1b
READ_ADV_FEATURES, REMOVE_ADVERTISING = 0x3d, 0x3f
EV_CMD_COMPLETE, EV_CMD_STATUS, EV_NEW_SETTINGS = 1, 2, 6
# {opcode: its bit in the settings word}: powered, discoverable, connectable, fast connectable, bondable, link
# security, SSP, high speed, LE, advertising, BR/EDR, secure connections, debug keys, privacy, static address
SETTING = {0x05: 0x1, 0x06: 0x8, 0x07: 0x2, 0x08: 0x4, 0x09: 0x10, 0x0a: 0x20, 0x0b: 0x40, 0x0c: 0x100, 0x0d: 0x200,
           0x29: 0x400, 0x2a: 0x80, 0x2d: 0x800, 0x2e: 0x1000, 0x2f: 0x2000, 0x2c: 0x8000}
# answered with success and no parameters: disconnect, PIN and confirm replies, device id, scan parameters, IRKs,
# connection parameters, the public address, appearance, blocked keys; and, echoing the address they were sent:
# unpair, block, unblock, add and remove device
EMPTY = (0x14, 0x16, 0x17, 0x1c, 0x1d, 0x28, 0x2b, 0x30, 0x35, 0x39, 0x43, 0x46)
ECHO_ADDRESS = (UNPAIR_DEVICE, 0x26, 0x27, 0x33, 0x34)
SUPPORTED = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x40 | 0x80 | 0x200 | 0x400 | 0x800 | 0x1000 | 0x2000 | 0x8000


class Kernel:
    """One controller's management socket, for as many connections as a daemon opens; `log` is every command
    as (opcode, index, parameters)."""
    def __init__(self, path):
        self.log, self.settings, self.dev_class = [], 0x80 | 0x200 | 0x40 | 0x800, bytes(3)
        self.name = bytes(260)
        self.lock = threading.Lock()
        self.peers = []
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.listener.bind(str(path))
        self.listener.listen(8)
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                peer, _ = self.listener.accept()
            except OSError:
                return
            with self.lock:
                self.peers.append(peer)
            threading.Thread(target=self.serve, args=(peer,), daemon=True).start()

    def serve(self, peer):
        while True:
            try:
                frame = peer.recv(4096)
            except OSError:
                frame = b''
            if len(frame) < 6:
                with self.lock:
                    self.peers.remove(peer)
                return
            opcode, index, _ = struct.unpack_from('<HHH', frame)
            with self.lock:
                self.log.append((opcode, index, frame[6:]))
                answer = self.answer(opcode, index, frame[6:], peer)
            try:
                if answer is None:  # unknown command
                    peer.send(struct.pack('<HHHHB', EV_CMD_STATUS, index, 3, opcode, 1))
                else:
                    peer.send(struct.pack('<HHHHB', EV_CMD_COMPLETE, index, 3 + len(answer), opcode, 0) + answer)
            except OSError:
                pass

    def answer(self, opcode, index, params, peer):
        if opcode == READ_VERSION:
            return struct.pack('<BH', 1, 18)
        if opcode == READ_COMMANDS:
            commands = sorted({READ_VERSION, READ_COMMANDS, READ_INDEX_LIST, READ_INFO, SET_DEV_CLASS, SET_LOCAL_NAME,
                               ADD_UUID, REMOVE_UUID, LOAD_LINK_KEYS, LOAD_LTKS, GET_CONNECTIONS, SET_IO_CAPABILITY,
                               READ_ADV_FEATURES, REMOVE_ADVERTISING, 0x23, 0x24, *SETTING, *EMPTY, *ECHO_ADDRESS})
            events = list(range(1, 0x26))
            return struct.pack(f'<HH{len(commands)}H{len(events)}H', len(commands), len(events), *commands, *events)
        if opcode == READ_INDEX_LIST:
            return struct.pack('<HH', 1, 0)
        if index != 0:
            return None
        if opcode == READ_INFO:
            address = bytes.fromhex(ADAPTER.replace(':', ''))[::-1]
            return (address + struct.pack('<BHII', 0x0b, 0x005d, SUPPORTED, self.settings) + self.dev_class
                    + self.name)
        if opcode in SETTING:
            before, bit = self.settings, SETTING[opcode]
            self.settings = self.settings | bit if params[:1] not in (b'', b'\0') else self.settings & ~bit
            if self.settings != before:
                for other in self.peers:
                    if other is not peer:
                        other.send(struct.pack('<HHHI', EV_NEW_SETTINGS, 0, 4, self.settings))
            return struct.pack('<I', self.settings)
        if opcode == SET_DEV_CLASS:
            self.dev_class = bytes([0, params[1], params[0]])
            return self.dev_class
        if opcode in (ADD_UUID, REMOVE_UUID):
            return self.dev_class
        if opcode == SET_LOCAL_NAME:
            self.name = params[:260].ljust(260, b'\0')
            return self.name
        if opcode == GET_CONNECTIONS:
            return struct.pack('<H', 0)
        if opcode == READ_ADV_FEATURES:
            return struct.pack('<IBBBB', 0x7f, 31, 31, 5, 0)
        if opcode == REMOVE_ADVERTISING:
            return params[:1]
        if opcode in (0x23, 0x24):  # start and stop discovery: the address types asked
            return params[:1]
        if opcode in ECHO_ADDRESS:
            return params[:7]
        if opcode in (LOAD_LINK_KEYS, LOAD_LTKS, SET_IO_CAPABILITY, *EMPTY):
            return b''
        return None

    def sent(self, opcode):
        with self.lock:
            return [params for op, _, params in self.log if op == opcode]

    def close(self):
        self.listener.close()


class Failed(Exception):
    pass


class Run:
    """One daemon on its own bus and kernel stand-in, over a shared store."""
    def __init__(self, name, binary, rig):
        self.name, self.rig = name, rig
        self.dir = rig.tmp / f'{name}-{len(list(rig.tmp.glob(name + "-*")))}'
        self.dir.mkdir()
        bus = self.dir / 'bus'
        (self.dir / 'bus.conf').write_text(BUS_CONFIG.format(path=bus))
        self.address = f'unix:path={bus}'
        self.bus = subprocess.Popen(['dbus-daemon', '--nofork', '--nopidfile', f'--config-file={self.dir / "bus.conf"}'],
                                    stderr=subprocess.DEVNULL)
        wait(bus.exists, 'dbus-daemon made no socket')
        self.kernel = Kernel(self.dir / 'mgmt')
        self.log = open(self.dir / 'bluetoothd.log', 'wb')
        # --die-with-parent: qemu goes when bwrap does, however this script ends.
        # /mnt/data is the store's place and holds nothing else here; the sysroot answers /etc/bluetooth and the
        # libraries. bluetoothd -n logs to stderr. The host's DMI tables are hidden, as the Q2 has none: without
        # hostnamed, bluez's hostname plugin reads /sys/class/dmi/id/chassis_type and replaces main.conf's Class
        # with the host's (Computer/Desktop for a 3).
        dmi = ['--tmpfs', '/sys/class/dmi'] if os.path.isdir('/sys/class/dmi') else []
        self.proc = subprocess.Popen(
            ['bwrap', '--die-with-parent', '--dev-bind', '/', '/', *dmi, '--tmpfs', '/mnt', '--bind', str(rig.data), '/mnt/data',
             'qemu-mipsel', '-L', str(rig.sysroot), '-E', f'LD_PRELOAD={rig.shim}',
             '-E', f'Q2_FAKE_MGMT={self.dir / "mgmt"}', '-E', f'Q2_FAKE_BINDS={self.dir / "binds"}',
             '-E', f'DBUS_SYSTEM_BUS_ADDRESS={self.address}',
             str(binary), '-n', '-d', *rig.options.get(name, [])],
            stdout=self.log, stderr=subprocess.STDOUT, env=NO_PRELOAD)

    def l2cap_binds(self):
        """[(psm, cid, address type)] of the L2CAP sockets the daemon bound, from the shim's record"""
        f = self.dir / 'binds'
        return [tuple(map(int, line.split())) for line in f.read_text().splitlines()] if f.exists() else []

    def gdbus(self, *args, check=True):
        r = subprocess.run(['gdbus', *args[:1], '--address', self.address, *args[1:]], capture_output=True, text=True,
                           timeout=20)
        if check and r.returncode:
            raise Failed(f'{self.name}: gdbus {" ".join(args)}: {r.stderr.strip()}')
        return r.stdout.strip() if check else r

    def call(self, path, method, *args, check=True):
        return self.gdbus('call', '--dest', 'org.bluez', '--object-path', path, '--method', method, *args, check=check)

    def get(self, path, interface, name):
        return self.call(path, 'org.freedesktop.DBus.Properties.Get', interface, name)

    def set(self, path, interface, name, value, check=True):
        return self.call(path, 'org.freedesktop.DBus.Properties.Set', interface, name, value, check=check)

    def owns(self, name):
        r = self.gdbus('call', '--dest', 'org.freedesktop.DBus', '--object-path', '/org/freedesktop/DBus', '--method',
                       'org.freedesktop.DBus.NameHasOwner', name, check=False)
        return r.returncode == 0 and 'true' in r.stdout

    def interfaces(self, path):
        xml = self.call(path, 'org.freedesktop.DBus.Introspectable.Introspect')
        return {part.split('"')[1] for part in xml.split('<interface name=')[1:]}

    def text(self):
        self.log.flush()
        return (self.dir / 'bluetoothd.log').read_text(errors='replace')

    def stop(self):
        for p in (self.proc, self.bus):
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        self.kernel.close()
        self.log.close()


def wait(done, message, seconds=30):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if done():
            return
        time.sleep(0.1)
    raise Failed(message)


class Rig:
    def __init__(self, source, tmp):
        self.tmp = tmp
        own = None
        if (source / 'stock.squashfs').is_file():
            stockfs = tmp / 'stockfs'
            subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', stockfs, source / 'stock.squashfs', 'lib', 'usr/lib',
                            'usr/bin/bluetoothd', 'etc/bluetooth'], check=True, stdout=subprocess.DEVNULL)
            if 'own_bluez' in json.loads((source / 'manifest.json').read_text()):
                subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', tmp / 'built', source / 'rootfs.squashfs', OWN],
                               check=True, stdout=subprocess.DEVNULL)
                own = tmp / 'built' / OWN
                print(f'own: the build\'s {OWN}')
        else:
            stockfs = source
        # The store is on a tmpfs: bluetoothd is a 32-bit program whose readdir refuses the 64-bit directory
        # offsets ext4 hands a 64-bit host (EOVERFLOW, and the store reads as empty). The Q2's kernel is 32-bit.
        self.data = pathlib.Path(tempfile.mkdtemp(prefix='q2-bluez-own-', dir='/dev/shm'))
        # a sysroot of stock's directories with Vamp's main.conf in place of stock's
        self.sysroot = tmp / 'sysroot'
        self.sysroot.mkdir()
        for entry in stockfs.iterdir():
            if entry.name not in ('etc', 'mnt', 'tmp', 'proc', 'sys', 'dev', 'run', 'var'):
                (self.sysroot / entry.name).symlink_to(entry)
        (self.sysroot / 'etc/bluetooth').mkdir(parents=True)
        conf = (stockfs / 'etc/bluetooth/main.conf').read_bytes()
        for old, new in MAIN_EDITS:
            if conf.count(old) != 1:
                raise SystemExit(f'stock main.conf has no single {old!r}')
            conf = conf.replace(old, new)
        (self.sysroot / 'etc/bluetooth/main.conf').write_bytes(conf)
        env = thirdparty.Env({}, 0)
        (tmp / 'shim.c').write_text(SHIM)
        self.shim = tmp / 'shim.so'
        subprocess.run([*env.cc.split(), '-shared', '-fPIC', '-O1', '-o', str(self.shim), str(tmp / 'shim.c'), '-ldl'],
                       check=True)
        self.binaries = {'own': own or thirdparty.build('bluez') / 'usr/bin/bluetoothd', 'stock': stockfs / 'usr/bin/bluetoothd'}
        self.options = {'own': OWN_OPTIONS}

    def fresh_store(self):
        shutil.rmtree(self.data, ignore_errors=True)
        device = self.data / 'bluetooth' / ADAPTER / DEVICE
        device.mkdir(parents=True)
        (device / 'info').write_text(INFO)

    def start(self, name):
        return Run(name, self.binaries[name], self)


RESULTS = []


def check(label, ok, detail=''):
    print(('ok   ' if ok else 'FAIL ') + label + (f': {detail}' if detail and not ok else ''), flush=True)
    RESULTS.append((label, ok))


def key_loaded(run):
    """Whether the store's link key reached the kernel: LOAD_LINK_KEYS is {debug u8, count u16, then per key
    address 6, address type 1, key type 1, the key 16, PIN length 1}."""
    want = bytes.fromhex(DEVICE.replace(':', ''))[::-1]
    for params in run.kernel.sent(LOAD_LINK_KEYS):
        count = struct.unpack_from('<H', params, 1)[0]
        for i in range(count):
            entry = params[3 + 25 * i:3 + 25 * (i + 1)]
            if entry[:6] == want and entry[8:24] == bytes.fromhex(LINK_KEY):
                return True
    return False


def started(run):
    wait(lambda: run.owns('org.bluez') or run.proc.poll() is not None, f'{run.name}: org.bluez never owned')
    if run.proc.poll() is not None:
        raise Failed(f'{run.name}: bluetoothd exited {run.proc.returncode}: {run.text()[-600:]}')
    wait(lambda: run.gdbus('introspect', '--dest', 'org.bluez', '--object-path', '/org/bluez/hci0', check=False)
         .stdout.count('org.bluez.Adapter1'), f'{run.name}: no hci0')


# what the firmware's callers need of hci0 (docs/bluez.md#what-depends-on-it)
NEEDED = {'org.bluez.Adapter1', 'org.bluez.Media1', 'org.freedesktop.DBus.Properties'}
AGENT = '/vamp/test/agent'


def exercise(run):
    """The checks one daemon gets on a fresh store."""
    n = run.name
    started(run)
    check(f'{n}: starts on Vamp\'s main.conf and owns org.bluez', True)
    # bluez_main, in libbtctl.so (stock's, from the sysroot): the D-Bus service demo calls, which the own daemon
    # starts too for the stock base (tools/bluez/shanling.patch, docs/bluez.md#shanlings-changes)
    for _ in range(50):
        if run.owns('shanling.bluez'):
            break
        time.sleep(0.1)
    check(f'{n}: owns shanling.bluez, as libbtctl.so\'s bluez_main makes it', run.owns('shanling.bluez'))
    faces = run.interfaces('/org/bluez/hci0')
    check(f'{n}: hci0 has Adapter1 and Media1', NEEDED <= faces, str(sorted(faces)))
    root = run.interfaces('/org/bluez')
    check(f'{n}: /org/bluez has AgentManager1 and ProfileManager1',
          {'org.bluez.AgentManager1', 'org.bluez.ProfileManager1'} <= root, str(sorted(root)))
    check(f'{n}: Name is main.conf\'s', "'Vamp'" in run.get('/org/bluez/hci0', 'org.bluez.Adapter1', 'Name'))
    check(f'{n}: DiscoverableTimeout is main.conf\'s 180',
          'uint32 180' in run.get('/org/bluez/hci0', 'org.bluez.Adapter1', 'DiscoverableTimeout'))
    check(f'{n}: Address is the controller\'s', ADAPTER in run.get('/org/bluez/hci0', 'org.bluez.Adapter1', 'Address'))
    classes = run.kernel.sent(SET_DEV_CLASS)
    check(f'{n}: main.conf\'s Class 0x20041C is set on the controller', bool(classes) and classes[-1][:2] == b'\x04\x1c',
          str(classes))
    check(f'{n}: the stored device is a paired Device1',
          'true' in run.get(DEVICE_PATH, 'org.bluez.Device1', 'Paired')
          and 'Fixture Buds' in run.get(DEVICE_PATH, 'org.bluez.Device1', 'Name'))
    check(f'{n}: its link key is loaded into the kernel', key_loaded(run))
    # the payload's calls: the adapter's alias (bt_name.c), a device's (bt_detail.c), a pairing agent (libbtctl's
    # and q2_guest.c's), pairable and discoverable (q2_guest.c), an A2DP endpoint (bluealsa), every object
    # (q2_guest.c), a battery read that has no answer (bt_battery.c), forgetting a device (bt_ui.c, bt_detail.c)
    run.set('/org/bluez/hci0', 'org.bluez.Adapter1', 'Alias', "<'Renamed'>")
    check(f'{n}: Adapter1.Alias set and read back', "'Renamed'" in run.get('/org/bluez/hci0', 'org.bluez.Adapter1', 'Alias'))
    names = run.kernel.sent(SET_LOCAL_NAME)
    check(f'{n}: the alias reaches the controller', bool(names) and names[-1].startswith(b'Renamed\0'), str(names[-1:]))
    run.set(DEVICE_PATH, 'org.bluez.Device1', 'Alias', "<'My Buds'>")
    check(f'{n}: Device1.Alias set and read back', "'My Buds'" in run.get(DEVICE_PATH, 'org.bluez.Device1', 'Alias'))
    run.call('/org/bluez', 'org.bluez.AgentManager1.RegisterAgent', f"objectpath '{AGENT}'", 'DisplayYesNo')
    check(f'{n}: an agent registers (it goes with its caller)', True)
    run.set('/org/bluez/hci0', 'org.bluez.Adapter1', 'Pairable', '<true>')
    check(f'{n}: Pairable set', 'true' in run.get('/org/bluez/hci0', 'org.bluez.Adapter1', 'Pairable'))
    run.set('/org/bluez/hci0', 'org.bluez.Adapter1', 'Discoverable', '<true>')
    check(f'{n}: Discoverable set is taken', True)
    objects = run.gdbus('call', '--dest', 'org.bluez', '--object-path', '/', '--method',
                        'org.freedesktop.DBus.ObjectManager.GetManagedObjects')
    check(f'{n}: GetManagedObjects lists the adapter and the device', '/org/bluez/hci0' in objects and DEVICE_PATH in objects)
    battery = run.call(DEVICE_PATH, 'org.freedesktop.DBus.Properties.Get', 'org.bluez.Battery1', 'Percentage', check=False)
    check(f'{n}: a battery read of a device with none fails cleanly', battery.returncode != 0)
    return faces


def reread(run, alias):
    """The store another daemon wrote, read by this one."""
    started(run)
    check(f'{run.name}: reads the other\'s store: the device is paired',
          'true' in run.get(DEVICE_PATH, 'org.bluez.Device1', 'Paired'))
    check(f'{run.name}: reads the other\'s store: the key is loaded', key_loaded(run))
    got = run.get(DEVICE_PATH, 'org.bluez.Device1', 'Alias')
    check(f'{run.name}: reads the other\'s store: the alias set there', f"'{alias}'" in got, got)
    got = run.get('/org/bluez/hci0', 'org.bluez.Adapter1', 'Alias')
    check(f'{run.name}: reads the other\'s store: the adapter\'s alias', "'Renamed'" in got, got)


def forget(run):
    run.call('/org/bluez/hci0', 'org.bluez.Adapter1.RemoveDevice', f"objectpath '{DEVICE_PATH}'")
    info = run.rig.data / 'bluetooth' / ADAPTER / DEVICE / 'info'
    for label, done in ((f'{run.name}: RemoveDevice removes the record from the store', lambda: not info.exists()),
                        (f'{run.name}: RemoveDevice unpairs it in the kernel', lambda: bool(run.kernel.sent(UNPAIR_DEVICE)))):
        try:  # the call returns before the daemon has done either
            wait(done, label, 5)
            check(label, True)
        except Failed:
            check(label, False)


def main(argv):
    keep = '--keep' in argv
    only = argv[argv.index('--only') + 1] if '--only' in argv else None
    own = argv[argv.index('--own') + 1] if '--own' in argv else None  # another own daemon than the build's
    args = [a for a in argv if not a.startswith('--') and a not in (only, own)]
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    for tool in ('qemu-mipsel', 'bwrap', 'dbus-daemon', 'gdbus', 'unsquashfs'):
        if not shutil.which(tool):
            print(f'skip: no {tool} on this machine')
            return 0
    tmp = pathlib.Path(tempfile.mkdtemp(prefix='q2-bluez-own-'))
    runs = []
    rig = Rig(pathlib.Path(args[0]).resolve(), tmp)
    if own:
        rig.binaries['own'] = pathlib.Path(own)
    try:
        faces, binds = {}, {}
        order = [only] if only else ['own', 'stock']
        for name in order:
            rig.fresh_store()
            run = rig.start(name)
            runs.append(run)
            faces[name] = exercise(run)
            binds[name] = run.l2cap_binds()
            run.stop()
            if not only:  # the store this one wrote, under the other, then forgotten there
                other = rig.start('stock' if name == 'own' else 'own')
                runs.append(other)
                reread(other, 'My Buds')
                forget(other)
                other.stop()
        if not only:
            extra = sorted(faces['own'] - faces['stock'])
            missing = sorted(faces['stock'] - faces['own'])
            print(f'hci0 interfaces only the own daemon has: {", ".join(extra) or "none"}')
            check('own: hci0 lacks no interface stock\'s has', not missing, str(missing))
            # docs/bluez.md#the-le-attribute-channel: main.conf's [GATT] Server = false (tools/bluez/vamp.patch) leaves
            # the GATT database and the advertising manager out, as stock's build does, so coda-rc keeps CID 4
            le = {'org.bluez.GattManager1', 'org.bluez.LEAdvertisingManager1'}
            check('own: no GattManager1 or LEAdvertisingManager1, as stock has none ([GATT] Server = false)',
                  not le & faces['own'], str(sorted(le & faces['own'])))
            check('stock: neither is there', not le & faces['stock'])
            for name in order:
                att = [b for b in binds[name] if b[1] == ATT_CID]
                check(f'{name}: nothing bound on the LE attribute channel (CID 4), coda-rc\'s', not att, str(att))
    except Failed as e:
        check(str(e), False)
        for run in runs:
            if not run.log.closed:
                print(f'--- {run.name} log tail\n{run.text()[-1500:]}')
    finally:
        for run in runs:
            if not run.log.closed:
                run.stop()
        if keep:
            print(f'kept {tmp} and {rig.data}')
        else:
            shutil.rmtree(tmp, ignore_errors=True)
            shutil.rmtree(rig.data, ignore_errors=True)
    failed = [label for label, ok in RESULTS if not ok]
    print(f'{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
