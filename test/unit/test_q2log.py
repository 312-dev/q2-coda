"""The unified log's core (patch/q2log_core.c, patch/ulog_line.h, docs/internals.md#unified-log): the line, a raw line's
level and source, demo's stderr file read as it grows and across the shell's roll, the kernel's records, the four
files' rotation, the RAM ring while the card is away and its ordered flush, and the snapshot. Built for this machine
with test/unit/q2guard_host.c."""
import ctypes as C
import re
import subprocess

import pytest

from test_q2guard import ROOT

WALL = 1794926102  # 2026-11-17T14:35:02Z
TIME = '2026-11-17T14:35:02Z'
FILE_MAX, RAM, PART = 262144, 65536, 512
LINE = re.compile(r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ \d+ [EWI] (ui|player|usb|bt|wifi|guard|kernel) \S.*$')


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2log') / 'q2log.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2log_core.c', ROOT / 'patch/q2guard_core.c',
                    ROOT / 'patch/card_store.c', ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.log_named.restype = lib.log_ram.restype = C.c_void_p
    lib.log_named.argtypes = [C.c_char_p, C.c_char_p, C.c_uint]
    lib.log_used.argtypes = lib.log_ram.argtypes = lib.log_dropped.argtypes = [C.c_void_p]
    lib.ulog_add.argtypes = [C.c_void_p, C.c_long, C.c_uint, C.c_char, C.c_char_p, C.c_char_p]
    lib.ulog_add_raw.argtypes = [C.c_void_p, C.c_long, C.c_uint, C.c_char_p, C.c_int]
    lib.ulog_add_raw.restype = C.c_char
    lib.ulog_guess.argtypes = [C.c_char_p, C.c_int, C.POINTER(C.c_char_p)]
    lib.ulog_guess.restype = C.c_char
    lib.ulog_tail_at.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
    lib.ulog_tail.argtypes = [C.c_void_p, C.c_void_p, C.c_long, C.c_uint]
    if hasattr(lib, 'ulog_apps'):
        lib.ulog_apps.argtypes = [C.c_void_p, C.c_void_p, C.c_char_p, C.c_long, C.c_uint]
    lib.ulog_kmsg.argtypes = [C.c_void_p, C.c_long, C.c_char_p, C.c_int]
    lib.ulog_kmsg.restype = C.c_char
    lib.ulog_flush.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_long, C.c_int]
    lib.ulog_repeats.argtypes = [C.c_void_p]
    lib.ulog_snapshot.argtypes = [C.c_void_p, C.c_long, C.c_uint, C.c_char, C.c_char_p, C.c_char_p, C.c_char_p,
                                  C.c_char_p]
    return lib


class Tail(C.Structure):
    _fields_ = [('path', C.c_char_p), ('off', C.c_long), ('plen', C.c_int), ('part', C.c_char * PART),
                ('src', C.c_char_p)]


APPS, APP_ID, APP_PATH = 4, 24, 160


class Apps(C.Structure):
    """q2log.h's struct ulog_apps."""
    _fields_ = [('t', Tail * APPS), ('id', (C.c_char * APP_ID) * APPS), ('path', (C.c_char * APP_PATH) * APPS),
                ('from_end', C.c_int)]


class Log:
    """A ulog ring named dir/q2-log.0, as q2guard.c's; the card down while lines are added, up for a flush."""
    def __init__(self, L, d, cap=RAM):
        self.L, self.d = L, d
        L.card(0)
        self.log = L.log_named(str(d).encode(), b'q2-log.0', cap)

    def ram(self):
        n = self.L.log_used(self.log)
        return C.string_at(self.L.log_ram(self.log), n).decode() if n else ''

    def add(self, level, src, msg, up=5123456, wall=WALL):
        self.L.ulog_add(self.log, wall, up, level.encode(), src.encode(), msg.encode())

    def flush(self, card=True):
        self.L.card(1 if card else 0)
        try:
            return self.L.ulog_flush(self.log, str(self.d).encode(), b'q2-log', FILE_MAX, 4)
        finally:
            self.L.card(0)


def test_a_line_is_time_uptime_level_source_message(L, tmp_path):
    log = Log(L, tmp_path)
    log.add('W', 'usb', 'plug, 40 ms')
    assert log.ram() == f'{TIME} 5123456 W usb plug, 40 ms\n'
    assert LINE.match(log.ram().rstrip('\n'))


def test_a_message_keeps_to_one_line_and_is_cut_to_fit(L, tmp_path):
    log = Log(L, tmp_path)
    log.add('I', 'ui', 'two\nlines\twith a tab\n')
    long = 'x' * 2000
    log.add('E', 'player', long)
    first, second = log.ram().splitlines()
    assert first == f'{TIME} 5123456 I ui two lines with a tab'
    assert second.startswith(f'{TIME} 5123456 E player xxx') and len(second) < 600


@pytest.mark.parametrize('text, level, src', [
    ('q2player: hw:0 takes no format 2', 'I', 'player'),
    ('hciplayer: open /mnt/mmc/a.flac failed', 'E', 'player'),
    ('rtk_hciattach: Failed to read local version', 'E', 'bt'),
    ('Bluetooth: hci0 command 0x0c03 tx timeout', 'E', 'bt'),
    ('wpa_supplicant: wlan0: WARNING: reason 3', 'W', 'wifi'),
    ('udhcpc: sending discover', 'I', 'wifi'),
    ('usb 1-1: new high-speed USB device', 'I', 'usb'),
    ('subtitle track 2 chosen', 'I', 'ui'),  # "bt" inside a word names no Bluetooth
    ('page home opened', 'I', 'ui'),
    ('Selected audio codec: [ffflac] afm: ffmpeg (FFmpeg FLAC audio)', 'I', 'player'),  # hciplayer's mplayer
    ('Starting playback...', 'I', 'player'),
])
def test_a_raw_line_gets_a_level_and_source_from_its_words(L, text, level, src):
    s = C.c_char_p()
    got = L.ulog_guess(text.encode(), len(text), C.byref(s))
    assert (got.decode(), (s.value or b'').decode()) == (level, src)


def test_a_ulog_line_passes_through_as_it_was_and_a_raw_one_is_stamped(L, tmp_path):
    """A raw line is stamped when it is read, or, after a stamped line read into the same ring no more than a minute
    before, with that line's time: it was written between that line and the read."""
    log = Log(L, tmp_path)
    raw = 'rtk_hciattach: Failed to read local version\n'
    assert L.ulog_add_raw(log.log, WALL, 777, raw.encode(), len(raw)) == b'E'
    mine = '2026-11-17T14:00:00Z 4200 W usb plug, 40 ms\n'
    assert L.ulog_add_raw(log.log, WALL, 7000, mine.encode(), len(mine)) == b'W'
    after = 'rtk_hciattach: Failed to download firmware\n'
    assert L.ulog_add_raw(log.log, WALL, 7000, after.encode(), len(after)) == b'E'
    late = 'rtk_hciattach: Failed again\n'
    assert L.ulog_add_raw(log.log, WALL, 70000, late.encode(), len(late)) == b'E'  # 65.8 s on: its own read time
    assert L.ulog_add_raw(log.log, WALL, 777, b'   \n', 4) == b'\x00'  # blank: dropped
    assert log.ram() == (f'{TIME} 777 E bt rtk_hciattach: Failed to read local version\n' + mine +
                         '2026-11-17T14:00:00Z 4200 E bt rtk_hciattach: Failed to download firmware\n' +
                         f'{TIME} 70000 E bt rtk_hciattach: Failed again\n')


def test_a_run_of_one_line_is_that_line_once_and_a_count(L, tmp_path):
    """The emulator's demo printed `gpio: failed to open device` seven times a second: 2171 of 2400 lines in 5
    minutes. A run is the line once, then `last line repeated N more times` at the last one's time, when another
    line comes or ulog_repeats is called (before a flush); a repeat is not an E line to flush for."""
    log = Log(L, tmp_path)
    spam = b'gpio: failed to open device, No such file or directory\n'
    assert L.ulog_add_raw(log.log, WALL, 100, spam, len(spam)) == b'E'
    for up in range(101, 106):
        assert L.ulog_add_raw(log.log, WALL, up, spam, len(spam)) == b'\x00'  # counted, nothing to flush for
    log.add('I', 'usb', 'cable in', up=200)
    for up in (300, 301):
        L.ulog_add_raw(log.log, WALL, up, spam, len(spam))
    L.ulog_repeats(log.log)  # a flush: the open run's count goes with it
    L.ulog_repeats(log.log)  # nothing more counted: nothing more said
    mine = '2026-11-17T14:00:00Z 42 W usb plug\n'
    for _ in range(3):
        L.ulog_add_raw(log.log, WALL, 999, mine.encode(), len(mine))  # demo's stamped lines repeat the same way
    L.ulog_repeats(log.log)
    assert [l.split(' ', 1)[1] for l in log.ram().splitlines()] == [
        '100 E ui gpio: failed to open device, No such file or directory',
        '105 E ui last line repeated 5 more times',
        '200 I usb cable in',
        '300 E ui gpio: failed to open device, No such file or directory',
        '301 E ui last line repeated 1 more times',
        '42 W usb plug',
        '42 W usb last line repeated 2 more times']


def tail(L, path, from_end=False):
    t = Tail()
    L.ulog_tail_at(C.byref(t), str(path).encode(), int(from_end))
    t.keep = str(path).encode()  # the struct's char * must outlive the call
    t.path = t.keep
    return t


def test_demo_stderr_is_read_as_it_grows_a_line_held_until_its_end(L, tmp_path):
    log, err = Log(L, tmp_path / 'logs'), tmp_path / 'q2-demo.err'
    t = tail(L, err)
    assert L.ulog_tail(C.byref(t), log.log, WALL, 1000) == 0  # no file yet
    err.write_text('q2player: started\nhalf a li')
    assert L.ulog_tail(C.byref(t), log.log, WALL, 1000) == 1
    assert log.ram() == f'{TIME} 1000 I player q2player: started\n'
    with err.open('a') as f:
        f.write('ne\nudhcpc: lease fail\n')
    assert L.ulog_tail(C.byref(t), log.log, WALL, 2000) == 3  # a line, and an E one: flush now
    assert log.ram().splitlines()[1:] == [f'{TIME} 2000 I ui half a line', f'{TIME} 2000 E wifi udhcpc: lease fail']
    assert L.ulog_tail(C.byref(t), log.log, WALL, 3000) == 0  # nothing new


def test_a_restarted_reader_starts_at_the_end_and_the_roll_loses_nothing(L, tmp_path):
    """q2guard started again reads only what is new; the shell's roll (copy to .1, then empty) is read across: the
    end of .1 first, then the emptied file from its start."""
    log, err = Log(L, tmp_path / 'logs'), tmp_path / 'q2-demo.err'
    err.write_text('old 1\nold 2\n')
    t = tail(L, err, from_end=True)
    with err.open('a') as f:
        f.write('before roll\n')
    L.ulog_tail(C.byref(t), log.log, WALL, 1)
    with err.open('a') as f:
        f.write('after the read\n')
    (tmp_path / 'q2-demo.err.1').write_bytes(err.read_bytes())  # the roll: cp, then : >
    err.write_text('new 1\n')
    L.ulog_tail(C.byref(t), log.log, WALL, 2)
    assert [l.split(' ', 4)[4] for l in log.ram().splitlines()] == ['before roll', 'after the read', 'new 1']


def test_a_running_card_app_s_own_log_comes_in_under_its_id(L, tmp_path):
    """docs/internals.md#unified-log: a card app logs to /tmp/q2-<id>.log (q2-subsonic's SetupLog), in RAM, which no
    log on the card held: a hotspot's failed downloads left nothing to read (the user's logs, 2026-11-19). The log of
    each running app (its launcher's pid file /tmp/q2-<id> beside it) comes in as it grows, Go's date left out, the
    source the id, the level guessed; the firmware's own /tmp/q2-*.log (no pid file) stay out; an app stopped is read
    once more, then let go."""
    log, tmp = Log(L, tmp_path / 'logs'), tmp_path / 'tmp'
    tmp.mkdir()
    (tmp / 'q2-demo.log').write_text('demo line\n')  # demo's own --dev file: q2guard tails it already
    (tmp / 'q2-subsonic').write_text('1234\n')
    app = tmp / 'q2-subsonic.log'
    app.write_text('2026/11/19 23:42:49 started, server music.example.net\n')
    a = Apps()
    poll = lambda up: L.ulog_apps(C.byref(a), log.log, str(tmp).encode(), WALL, up)  # noqa: E731
    assert poll(1000) == 1
    with app.open('a') as f:
        f.write('2026/11/19 23:42:55 download 41: dial tcp: i/o timeout\n')
    assert poll(2000) == 3  # an E line: flush now
    (tmp / 'q2-subsonic').unlink()  # stopped: its last words still come in
    with app.open('a') as f:
        f.write('2026-11-19 23:43:00 stopped\n')  # applog's date, as q2-subsonic's card log writes it
    poll(3000)
    with app.open('a') as f:
        f.write('2026/11/19 23:43:01 not read\n')
    poll(4000)
    assert log.ram().splitlines() == [f'{TIME} 1000 I subsonic started, server music.example.net',
                                      f'{TIME} 2000 E subsonic download 41: dial tcp: i/o timeout',
                                      f'{TIME} 3000 I subsonic stopped']


def test_a_line_longer_than_the_hold_is_cut_not_lost(L, tmp_path):
    log, err = Log(L, tmp_path / 'logs'), tmp_path / 'q2-demo.err'
    t = tail(L, err)
    err.write_text('y' * 3000 + '\nnext\n')
    L.ulog_tail(C.byref(t), log.log, WALL, 1)
    lines = log.ram().splitlines()
    assert len(lines) == 2 and lines[0].endswith('y' * 100) and lines[1].endswith(' I ui next')


@pytest.mark.parametrize('rec, want', [
    ('4,120,5140900123,-;usb 1-1: device descriptor read/64, error -71\n SUBSYSTEM=usb\n',
     f'{TIME} 5140900 W kernel usb 1-1: device descriptor read/64, error -71\n'),
    ('3,121,999,-;mmc0: card never left busy state\n', f'{TIME} 0 E kernel mmc0: card never left busy state\n'),
    ('0,2,12345678,-;Kernel panic\n', f'{TIME} 12345 E kernel Kernel panic\n'),
    ('6,122,5140900123,-;NET: Registered protocol family 10\n', ''),  # info: not kept
    ('12,123,5140900123,-;a program wrote warning\n', ''),  # facility 1 (a program's): not the kernel's
    ('garbage', ''),
])
def test_kernel_records_of_warning_and_worse_are_kept_at_their_own_uptime(L, tmp_path, rec, want):
    log = Log(L, tmp_path)
    level = L.ulog_kmsg(log.log, WALL, rec.encode(), len(rec))
    assert log.ram() == want and (level != b'\x00') == bool(want)


def test_the_log_rolls_through_four_files_and_drops_the_oldest(L, tmp_path):
    d = tmp_path / 'logs'
    log = Log(L, d)
    one = 'z' * 1020
    names = [d / f'q2-log.{k}' for k in range(4)]
    firsts = []
    for gen in range(5):
        log.add('I', 'ui', f'gen {gen}')
        firsts.append(log.ram())
        assert log.flush() == 0
        while (d / 'q2-log.0').stat().st_size < FILE_MAX:  # fill .0: a ring's worth at a time
            for k in range(60):
                log.add('I', 'ui', f'{k:02} {one}')  # each its own: a repeat would be counted, not written
            assert log.flush() == 0
    log.add('I', 'ui', 'last')
    assert log.flush() == 0
    assert names[0].read_text().endswith(' I ui last\n') and all(n.exists() for n in names)
    assert not (d / 'q2-log.4').exists()
    assert [n.read_text().splitlines()[0].split(' ', 4)[4] for n in names[1:]] == ['gen 4', 'gen 3', 'gen 2']
    for n in names[1:]:  # each moved whole, a line never split
        assert FILE_MAX <= n.stat().st_size < FILE_MAX + RAM and n.read_text().endswith('\n')


def test_the_card_away_lines_wait_in_ram_and_go_out_in_order_when_it_is_back(L, tmp_path):
    d = tmp_path / 'logs'
    log = Log(L, d, cap=110)
    log.add('I', 'ui', 'before')
    assert log.flush() == 0
    for i in range(6):
        log.add('W', 'usb', f'away {i}', up=i)
    assert log.flush(card=False) == -1 and (d / 'q2-log.0').read_text().endswith('before\n')
    assert L.log_dropped(log.log) == 3  # 36 bytes a line, 110 held: the three oldest went
    assert log.flush() == 0 and L.log_used(log.log) == 0
    got = [l.split(' ', 4)[4] if not l.startswith('==') else l for l in (d / 'q2-log.0').read_text().splitlines()]
    assert got == ['before', '== 3 lines lost while the card was away', 'away 3', 'away 4', 'away 5']


MEMINFO = 'MemTotal:         120000 kB\nMemFree:           9000 kB\nMemAvailable:     40000 kB\nBuffers: 1 kB\nCached:  25000 kB\n'
HEAT = '2026-11-17T14:35:00Z up=5120 t=41.5 die=41.5 adc=6900 batt_raw=27 level=87 g=ok\n'


def test_a_shutdown_snapshot_says_why_and_what_the_q2_was_doing_and_reaches_the_card(L, tmp_path):
    d = tmp_path / 'logs'
    log = Log(L, d)
    L.ulog_snapshot(log.log, WALL, 9000, b'I', b'power-off', MEMINFO.encode(), b'0.52 0.40 0.31 2/61 812\n',
                    HEAT.encode())
    assert log.flush() == 0
    assert [l.split(' ', 2)[2] for l in (d / 'q2-log.0').read_text().splitlines()] == [
        'I guard snapshot power-off',
        'I guard mem total=120000 free=9000 available=40000 cached=25000 kB',
        'I guard load 0.52 0.40 0.31 2/61 812',
        'I guard state up=5120 t=41.5 die=41.5 adc=6900 batt_raw=27 level=87 g=ok']
    L.ulog_snapshot(log.log, WALL, 9100, b'E', b'crash demo exit 139', None, None, None)
    assert log.ram() == f'{TIME} 9100 E guard snapshot crash demo exit 139\n'


def test_a_stamped_line_whose_uptime_runs_past_the_cut_is_kept_cut(L, tmp_path):
    """A ulog line longer than ULOG_PART is cut there; when the cut falls inside its uptime (500 digits), the level
    and source are past it, so the message is empty, never a negative length."""
    log, bt = Log(L, tmp_path), Log(L, tmp_path / 'bt')
    L.ulog_bt_to.argtypes = [C.c_void_p, C.c_void_p]
    L.ulog_bt_to(log.log, bt.log)  # the Bluetooth ring's check reads the message too
    try:
        line = ('2026-11-22T10:00:00Z ' + '7' * 500 + ' I x y\n').encode()
        assert L.ulog_add_raw(log.log, WALL, 100, line, len(line)) == b'I'
        assert L.ulog_add_raw(log.log, WALL, 200, line, len(line)) == b'\x00'  # the same again: a repeat, counted
        assert log.ram() == line[:PART].decode() + '\n' and bt.ram() == ''
    finally:
        L.ulog_bt_to(None, None)
