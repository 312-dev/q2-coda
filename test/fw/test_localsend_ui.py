"""patch/localsend_ui.c, LocalSend's demo side (docs/localsend.md): the state file q2send writes as the pages read it
(devices, trusted devices, the transfers' phases and sizes past 4 GB), sizes as the pages say them, which received
files are songs (a library scan after a receive), and the request it sends the daemon. /tmp is a dict
(conftest.Files)."""
import ctypes as C

from conftest import Files

STATE, CMD = '/tmp/q2send.state', '/tmp/q2send.cmd'
FP = 'A' * 64


class Dev(C.Structure):
    _fields_ = [('fp', C.c_char * 65), ('alias', C.c_char * 40), ('model', C.c_char * 24), ('phone', C.c_int)]


class Xfer(C.Structure):
    _fields_ = [('phase', C.c_char * 16), ('alias', C.c_char * 40), ('name', C.c_char * 64), ('done', C.c_int),
                ('files', C.c_int), ('trusted', C.c_int), ('kb_done', C.c_uint), ('kb', C.c_uint)]


class State(C.Structure):  # localsend_ui.c ls_state
    _fields_ = [('dev', Dev * 8), ('n', C.c_int), ('ntrust', C.c_int), ('trust_fp', (C.c_char * 65) * 8),
                ('trust_alias', (C.c_char * 40) * 8), ('recv', Xfer), ('send', Xfer)]


def read(fw, text):
    files = Files(fw, {STATE: text.encode()})
    s, h = State(), C.c_uint()
    ok = fw.fn('localsend_ui__ls_read', C.c_int, C.c_void_p, C.c_void_p)(C.addressof(s), C.addressof(h))
    return ok, s, h.value, files


def test_the_state_file_is_read_as_the_daemon_writes_it(fw):
    ok, s, _, _ = read(fw, f'me\t{"B" * 64}\tVamp\t53317\n'
                           f'peer\t{FP}\t192.168.1.20\t53317\tdesktop\tMacBook Air\tmacOS\n'
                           f'peer\t{"C" * 64}\t192.168.1.21\t53317\tmobile\tPixel 10 Pro\tAndroid\n'
                           'recv\trun\t5\t12\t21076377\t50541363\tPixel 10 Pro\t01 Speak to Me.flac\t1\n'
                           f'trusted\t{"C" * 64}\tPixel 10 Pro\n'
                           'send\tidle\t0\t0\t0\t0\t\t\n')
    assert ok and s.n == 2 and s.ntrust == 1
    assert [(d.alias, d.model, d.phone) for d in s.dev[:2]] == [(b'MacBook Air', b'macOS', 0), (b'Pixel 10 Pro', b'Android', 1)]
    assert s.dev[0].fp == FP.encode() and s.trust_alias[0].value == b'Pixel 10 Pro'
    r = s.recv
    assert (r.phase, r.done, r.files, r.kb_done, r.kb, r.alias, r.name, r.trusted) == (
        b'run', 5, 12, 20583, 49357, b'Pixel 10 Pro', b'01 Speak to Me.flac', 1)
    assert s.send.phase == b'idle'


def test_sizes_past_4_gb_are_read_without_a_64_bit_divide(fw):
    _, s, _, _ = read(fw, 'recv\trun\t0\t1\t5368709120\t10737418240\tMac\tbig.iso\t0\n')
    assert (s.recv.kb_done, s.recv.kb) == (5 << 20, 10 << 20)


def test_the_lists_hash_moves_with_the_devices_and_not_with_the_progress(fw):
    peer = f'peer\t{FP}\t192.168.1.20\t53317\tdesktop\tMacBook Air\tmacOS\n'
    h1 = read(fw, peer + 'recv\trun\t1\t2\t100\t200\tA\ta\t0\n')[2]
    h2 = read(fw, peer + 'recv\trun\t2\t2\t200\t200\tA\tb\t0\n')[2]
    h3 = read(fw, peer.replace('MacBook Air', 'Work Mac') + 'recv\trun\t2\t2\t200\t200\tA\tb\t0\n')[2]
    assert h1 == h2 != h3


def test_no_file_is_no_daemon(fw):
    Files(fw, {})
    s = State()
    assert fw.fn('localsend_ui__ls_read', C.c_int, C.c_void_p, C.c_void_p)(C.addressof(s), None) == 0 and s.n == 0


def size(fw, kb):
    out = C.create_string_buffer(16)
    fw.fn('localsend_ui__ls_size', None, C.c_char_p, C.c_uint, C.c_uint)(out, 16, kb)
    return out.value.decode()


def test_sizes_are_said_as_the_mock_up_has_them(fw):
    assert [size(fw, k) for k in (0, 4, 1023, 1126, 49357, 3 << 20)] == ['1 KB', '4 KB', '1023 KB', '1.0 MB', '48.2 MB', '3.0 GB']


def test_a_receive_brings_a_scan_only_for_songs(fw):
    audio = fw.fn('localsend_ui__ls_audio', C.c_int, C.c_char_p)
    assert [audio(n) for n in (b'/mnt/mmc/Received/01.FLAC', b'a/b.mp3', b'x.pdf', b'noext', b'.flac/readme')] == [1, 1, 0, 0, 0]


def test_a_send_asks_the_daemon_with_each_path_on_its_line(fw):
    files = Files(fw, {})
    paths = (C.c_char * 64 * 16)()
    for k, p in enumerate((b'/mnt/mmc/.q2muse/logs/q2-log.0', b'/tmp/battery-log.txt')):
        paths[k].value = p
    fw.fn('localsend_ui__ls_send', None, C.c_char_p, C.c_char_p, C.c_int, C.c_void_p, C.c_int)(
        FP.encode(), b'MacBook Air', 0, C.addressof(paths), 2)
    assert files.files[CMD] == f'send\t{FP}\n/mnt/mmc/.q2muse/logs/q2-log.0\n/tmp/battery-log.txt\n'.encode()


def test_send_logs_takes_each_card_app_s_own_log_from_ram_too(fw):
    """docs/localsend.md: a card app's own logs, /tmp/q2-<id>.log in RAM and .<id>/log.txt on the card (q2-subsonic's,
    where its fetches' failures go), each with the .1 it rolls to, were not sent: a hotspot's failed Subsonic downloads
    could not be seen from Send Logs (the user's, 2026-11-19). Send Logs takes the Q2's logs, then each app's own as
    they are now; an app with none adds nothing."""
    from test_games import Dirent
    dirs = {b'/mnt/mmc/.q2muse/logs': [b'.', b'q2-log.0', b'q2-heat.0'], b'/mnt/mmc': [b'.subsonic', b'.deezer', b'Music']}
    handles, listing = {}, {}

    def opendir(path, *_):
        names = dirs.get(C.string_at(path))
        if names is None:
            return 0
        h = 0x6000 + len(handles)
        handles[h], listing[h] = [Dirent(d_name=n) for n in names], 0
        return h

    def readdir(d, *_):
        k = listing[d]
        listing[d] = k + 1
        return C.addressof(handles[d][k]) if k < len(handles[d]) else 0
    fw.mock('fw_opendir', opendir)
    fw.mock('fw_readdir', readdir)
    Files(fw, {'/mnt/mmc/.subsonic/q2app': b'name=Subsonic\n', '/mnt/mmc/.deezer/q2app': b'name=Deezer\n'})
    fw.fn('addon_scan', C.c_int)()
    there = {'/mnt/mmc/.q2muse/logs/q2-log.0': 300, '/mnt/mmc/.q2muse/logs/q2-heat.0': 20,
             '/tmp/q2-subsonic.log': 34, '/tmp/q2-subsonic.log.1': 256, '/mnt/mmc/.subsonic/log.txt': 900}  # KB

    def statx(dirfd, path, flags, mask, out, *_):
        size = there.get(C.string_at(path).decode())
        if size is None:
            return 2
        sx = (C.c_uint * 64).from_address(out)
        sx[10], sx[11] = size * 1024, 0
        return 0
    fw.mock('ringnav_statx', statx)
    paths, kb = (C.c_char * 64 * 24)(), C.c_uint()
    n = fw.fn('localsend_ui__ls_logs', C.c_int, C.c_void_p, C.c_int, C.c_void_p)(C.addressof(paths), 23, C.byref(kb))
    assert [paths[k].value.decode() for k in range(n)] == [
        '/mnt/mmc/.q2muse/logs/q2-log.0', '/mnt/mmc/.q2muse/logs/q2-heat.0', '/tmp/q2-subsonic.log',
        '/tmp/q2-subsonic.log.1', '/mnt/mmc/.subsonic/log.txt']  # deezer has none
    assert kb.value == sum(there.values())
