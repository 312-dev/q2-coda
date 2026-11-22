"""The Bluetooth event log (patch/q2log_core.c ulog_bt_to and ulog_bt_wanted, docs/internals.md#bluetooth-event-log):
each Bluetooth line that goes into the unified log's ring copied, as it was written, into q2guard's q2-btev ring;
the other sources' lines left out; a repeated line's count following it; the four files' size cap; and a restart of
q2guard (a reboot) appending to the files the last run wrote, nothing before it lost. Built for this machine with
test/unit/q2guard_host.c, as test_q2log.py."""
import ctypes as C

import pytest

from test_q2log import WALL, L, Log  # noqa: F401  (L: the module's library fixture)

BTEV_FILE_MAX, BTEV_FILES, BTEV_RAM = 131072, 4, 32768  # q2log.h
BUDS = '04:00:6E:D3:96:6D'


class Pair:
    """The unified ring and the Bluetooth ring beside it, as q2guard.c sets them up at its start."""
    def __init__(self, L, d):
        self.L, self.d = L, d
        self.u = Log(L, d)
        self.ev = L.log_named(str(d).encode(), b'q2-btev.0', BTEV_RAM)
        L.ulog_bt_to.argtypes = [C.c_void_p, C.c_void_p]
        L.ulog_bt_to(self.u.log, self.ev)

    def flush(self):
        """q2guard's writing child: both rings to their files."""
        assert self.u.flush() == 0
        self.L.card(1)
        try:
            return self.L.ulog_flush(self.ev, str(self.d).encode(), b'q2-btev', BTEV_FILE_MAX, BTEV_FILES)
        finally:
            self.L.card(0)

    def text(self, name):
        p = self.d / name
        return p.read_text() if p.exists() else ''


@pytest.fixture(autouse=True)
def unhook(L):
    yield
    L.ulog_bt_to.argtypes = [C.c_void_p, C.c_void_p]
    L.ulog_bt_to(None, None)


def raw(L, log, text, up=7000):
    b = text.encode()
    return L.ulog_add_raw(log.log, WALL, up, b, len(b))


def test_the_bluetooth_lines_are_copied_as_written_and_the_rest_left_out(L, tmp_path):
    """The day of 2026-11-19 in miniature: its kernel, BlueZ, bluealsa, the trace's and the player's lines beside
    the UI's, USB's and Wi-Fi's. q2-btev.0 holds exactly the Bluetooth ones, byte for byte as q2-log.0 has them,
    in order."""
    p = Pair(L, tmp_path)
    u = p.u
    u.add('I', 'guard', 'boot build=V1JBi last=forced')
    u.add('I', 'ui', 'page home opened')
    u.add('I', 'bt', f'connected {BUDS} "Grayson\'s Pixel Buds" by=device')
    raw(L, u, "2026-11-19T14:21:35Z 9824703 I bt auto-connect (auto) picks 60:E6:F0:A1:1F:09: the saved device\n")
    raw(L, u, 'parse_bluealsa_status_msg event = 2, len = 14\n')
    raw(L, u, "bluealsa: E: Couldn't acquire transport: GDBus.Error:org.bluez.Error.Failed: Input/output error\n")
    raw(L, u, "q2player: plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp' 44100 Hz 2 ch 2 bytes: Device or resource busy\n")
    raw(L, u, '2026-11-19T14:21:50Z 9839712 E player q2player: open failed (missing): /mnt/mmc/Music/a.flac\n')
    raw(L, u, '2026-11-19T14:21:51Z 9839800 I player q2player: set-songurl /mnt/mmc/Music/b.flac\n')
    u.add('W', 'usb', 'cable in, seen after its edge, 9 ms')
    raw(L, u, 'wpa_supplicant: wlan0: WARNING: reason 3\n')
    rec = '3,900,9839900123,-;Bluetooth: hci0: command 0x0c03 tx timeout\n'
    L.ulog_kmsg(u.log, WALL, rec.encode(), len(rec))
    u.add('I', 'bt', f'transport {BUDS} active: AVDTP started /sep1/fd0')
    u.add('W', 'bt', f'disconnected {BUDS} "Grayson\'s Pixel Buds" reason=1 timeout or link loss')
    u.add('I', 'guard', 'snapshot power-off')
    u.add('I', 'ui', 'shutdown: power-off')
    assert p.flush() == 0
    log, ev = p.text('q2-log.0').splitlines(), p.text('q2-btev.0').splitlines()
    left_out = ('page home opened', 'set-songurl', 'cable in', 'wpa_supplicant')
    assert len(log) == 16 and len(ev) == 12
    assert ev == [line for line in log if not any(w in line for w in left_out)]
    assert ev[1].endswith('connected 04:00:6E:D3:96:6D "Grayson\'s Pixel Buds" by=device')
    assert ' E kernel Bluetooth: hci0: command 0x0c03 tx timeout' in ev[7]


@pytest.mark.parametrize('level, src, msg, want', [
    ('I', 'bt', 'link list 0', 1),
    ('E', 'player', 'q2player: open failed (unsupported): /mnt/mmc/x.dsf', 1),
    ('I', 'player', 'q2player: set-songurl /mnt/mmc/x.flac', 0),
    ('I', 'guard', 'boot build=V last=clean', 1),
    ('I', 'guard', 'BOOT build=V last=clean', 0),  # the heat log's note; the lower-case marker follows it
    ('I', 'guard', 'state up=671 t=34.6 bt=1', 0),
    ('W', 'kernel', 'l2cap: chan 3 timeout', 1),
    ('I', 'ui', 'subtitle track 2', 0),
    ('I', 'ui', 'hci0x is not hci0', 1),
    ('I', 'ui', 'hci0x only', 0),  # hci0 only as a word of its own
    ('I', 'usb', 'gadget car: A2DP off while the car plays', 1),
])
def test_what_counts_as_a_bluetooth_line(L, level, src, msg, want):
    L.ulog_bt_wanted.argtypes = [C.c_char, C.c_char_p, C.c_char_p, C.c_int]
    assert L.ulog_bt_wanted(level.encode(), src.encode(), msg.encode(), len(msg)) == want


def test_a_repeated_bluetooth_line_is_counted_in_both_and_a_repeated_other_in_neither(L, tmp_path):
    p = Pair(L, tmp_path)
    for _ in range(5):
        raw(L, p.u, "bluealsa: E: Couldn't release transport: UnknownObject\n")
    for _ in range(3):
        p.u.add('E', 'ui', 'gpio: failed to open device')
    p.u.add('I', 'bt', 'scan ended')
    assert p.flush() == 0
    tail = [line.split(' ', 2)[2] for line in p.text('q2-btev.0').splitlines()]
    assert tail == ["I player bluealsa: E: Couldn't release transport: UnknownObject",
                    'I player last line repeated 4 more times', 'I bt scan ended']
    assert 'I ui last line repeated' not in p.text('q2-btev.0')
    assert 'E ui last line repeated 2 more times' in p.text('q2-log.0')


def test_the_four_files_hold_about_512_kb_and_drop_the_oldest(L, tmp_path):
    """A week of Bluetooth lines, flushed as q2guard does (a minute's worth at a time): never more than four files,
    each moved on once it reaches 128 KB, so the whole stays under 4 x 128 KB plus one flush; the newest kept."""
    p = Pair(L, tmp_path)
    n = 0
    for _ in range(300):
        for _ in range(40):
            p.u.add('I', 'bt', f'found {BUDS} "Buds" rssi=-52 BR/EDR seq {n:07}')
            n += 1
        assert p.flush() == 0
    files = sorted(tmp_path.glob('q2-btev.*'))
    assert [f.name for f in files] == [f'q2-btev.{k}' for k in range(BTEV_FILES)]
    sizes = [f.stat().st_size for f in files]
    chunk = 40 * 100  # a flush's bytes, about
    assert all(s < BTEV_FILE_MAX + chunk for s in sizes) and all(s >= BTEV_FILE_MAX for s in sizes[1:])
    assert sum(sizes) < BTEV_FILES * BTEV_FILE_MAX + chunk
    newest = p.text('q2-btev.0').splitlines()[-1]
    assert newest.endswith(f'seq {n - 1:07}')
    oldest = (tmp_path / 'q2-btev.3').read_text().splitlines()[0]
    assert int(oldest.rsplit(' ', 1)[1]) > 0  # the first lines are gone, the file never split mid-line
    for f in files:
        assert f.read_text().endswith('\n')


def test_a_reboot_keeps_the_lines_written_before_it(L, tmp_path):
    """One run's lines written at its SIGTERM or power-off (flush_final), then a reboot: a new q2guard, new rings,
    its boot marker and lines after the last run's in the same file, nothing lost; a line the first run held
    when it was killed without warning is the one thing missing (the 60 s timer bounds how many)."""
    first = Pair(L, tmp_path)
    first.u.add('I', 'bt', f'connected {BUDS} "Buds" by=q2')
    first.u.add('W', 'bt', f'transport {BUDS} idle: AVDTP suspended or not started /sep1/fd0')
    first.u.add('I', 'guard', 'snapshot shutdown signal')
    assert first.flush() == 0
    first.u.add('I', 'bt', 'held when the power went')  # never flushed: the Q2 died here
    second = Pair(L, tmp_path)  # the next boot: rings start empty, the files are the card's
    second.u.add('W', 'guard', 'boot build=V1JBi last=forced')
    second.u.add('I', 'bt', 'auto-connect (auto) picks 60:E6:F0:A1:1F:09: the saved device, paired, nothing connected')
    assert second.flush() == 0
    got = [line.split(' ', 3)[3] for line in second.text('q2-btev.0').splitlines()]
    assert got == [f'bt connected {BUDS} "Buds" by=q2',
                   f'bt transport {BUDS} idle: AVDTP suspended or not started /sep1/fd0',
                   'guard snapshot shutdown signal', 'guard boot build=V1JBi last=forced',
                   'bt auto-connect (auto) picks 60:E6:F0:A1:1F:09: the saved device, paired, nothing connected']


def test_with_the_card_away_the_lines_wait_and_follow_in_order(L, tmp_path):
    p = Pair(L, tmp_path)
    p.u.add('I', 'bt', 'before')
    assert p.flush() == 0
    for i in range(3):
        p.u.add('I', 'bt', f'away {i}')
    L.card(0)
    assert L.ulog_flush(p.ev, str(tmp_path).encode(), b'q2-btev', BTEV_FILE_MAX, BTEV_FILES) == -1
    assert p.flush() == 0
    assert [line.split(' ', 4)[4] for line in p.text('q2-btev.0').splitlines()] == ['before', 'away 0', 'away 1',
                                                                                   'away 2']
