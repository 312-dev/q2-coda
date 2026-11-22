"""The Bluetooth event log (patch/q2log_core.c ulog_bt_to and ulog_bt_wanted, docs/internals.md#bluetooth-event-log):
each Bluetooth line that goes into the unified log's ring copied, as it was written, into q2guard's q2-btev ring;
the other sources' lines left out; a repeated line's count following it; the four files' size cap; and a restart of
q2guard (a reboot) appending to the files the last run wrote, nothing before it lost. Built for this machine with
test/unit/q2guard_host.c, as test_q2log.py."""
import ctypes as C

import pytest

from test_q2log import TIME, WALL, L, Log, Tail  # noqa: F401  (L: the module's library fixture)

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
    u.add('I', 'guard', 'boot build=V1JBi last=forced', up=9800000)
    u.add('I', 'ui', 'page home opened', up=9810000)
    u.add('I', 'bt', f'connected {BUDS} "Grayson\'s Pixel Buds" by=device', up=9824000)
    raw(L, u, "2026-11-19T14:21:35Z 9824703 I bt auto-connect (auto) picks 60:E6:F0:A1:1F:09: the saved device\n")
    raw(L, u, 'parse_bluealsa_status_msg event = 2, len = 14\n', up=9839000)
    raw(L, u, "bluealsa: E: Couldn't acquire transport: GDBus.Error:org.bluez.Error.Failed: Input/output error\n",
        up=9839000)
    raw(L, u, "q2player: plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp' 44100 Hz 2 ch 2 bytes: Device or resource busy\n",
        up=9839000)
    raw(L, u, '2026-11-19T14:21:50Z 9839712 E player q2player: open failed (missing): /mnt/mmc/Music/a.flac\n')
    raw(L, u, '2026-11-19T14:21:51Z 9839800 I player q2player: set-songurl /mnt/mmc/Music/b.flac\n')
    u.add('W', 'usb', 'cable in, seen after its edge, 9 ms', up=9839850)
    raw(L, u, 'wpa_supplicant: wlan0: WARNING: reason 3\n', up=9839860)
    rec = '3,900,9839900123,-;Bluetooth: hci0: command 0x0c03 tx timeout\n'
    L.ulog_kmsg(u.log, WALL, rec.encode(), len(rec))
    u.add('I', 'bt', f'transport {BUDS} active: AVDTP started /sep1/fd0', up=9840000)
    u.add('W', 'bt', f'disconnected {BUDS} "Grayson\'s Pixel Buds" reason=1 timeout or link loss', up=9850000)
    u.add('I', 'guard', 'snapshot power-off', up=9860000)
    u.add('I', 'ui', 'shutdown: power-off', up=9860001)
    assert p.flush() == 0
    log, ev = p.text('q2-log.0').splitlines(), p.text('q2-btev.0').splitlines()
    left_out = ('page home opened', 'set-songurl', 'cable in', 'wpa_supplicant', 'parse_bluealsa_status_msg')
    assert len(log) == 16 and len(ev) == 11
    assert ev == [line for line in log if not any(w in line for w in left_out)]
    assert ev[1].endswith('connected 04:00:6E:D3:96:6D "Grayson\'s Pixel Buds" by=device')
    assert ' E kernel Bluetooth: hci0: command 0x0c03 tx timeout' in ev[6]
    assert " bt bluealsa: E: Couldn't acquire transport" in ev[3]  # bluealsa's own line is Bluetooth's


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
    ('I', 'player', 'parse_bluealsa_status_msg event = 10, len = 14', 0),  # the player's, not a Bluetooth event
    ('E', 'kernel', "debugfs: File 'force_bredr_smp' in directory 'hci0' already present!", 0),  # each stack start
    ('E', 'kernel', 'Bluetooth: Peer acked invalid packet', 1),
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
    assert tail == ["I bt bluealsa: E: Couldn't release transport: UnknownObject",
                    'I bt last line repeated 4 more times', 'I bt scan ended']
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


def bt_lines(p):
    return [line.split(' ', 1)[1] for line in p.text('q2-btev.0').splitlines()]


def test_lines_from_two_feeds_go_in_by_their_uptime(L, tmp_path):
    """The drive of 2026-11-22: q2guard's own lines (the kernel's connect, BlueZ's transports) go into the ring as they
    happen, demo's are read from its stderr file at the next 5 s tick with their own earlier stamps, and were
    written after them. In q2-btev each goes in by its uptime; the unified log keeps the order it read them in."""
    p = Pair(L, tmp_path)
    u = p.u
    u.add('I', 'bt', 'connected 60:E6:F0:A1:1F:09 "BMW 83828" by=device', up=2071325)
    u.add('I', 'bt', 'transport 60:E6:F0:A1:1F:09 "BMW 83828" opened: A2DP stream configured /fd0', up=2072086)
    raw(L, u, '2026-11-22T05:56:59Z 2071502 I bt auto-connect (usb) skips 60:E6:F0:A1:1F:09\n')
    raw(L, u, '2026-11-22T05:56:59Z 2071586 I bt saved device 60:E6:F0:A1:1F:09\n')
    u.add('I', 'bt', 'transport 60:E6:F0:A1:1F:09 "BMW 83828" active: AVDTP started /fd0', up=2072822)
    assert p.flush() == 0
    ups = [int(line.split(' ')[0]) for line in bt_lines(p)]
    assert ups == [2071325, 2071502, 2071586, 2072086, 2072822]
    assert [int(line.split(' ')[1]) for line in p.text('q2-log.0').splitlines()] == [2071325, 2072086, 2071502,
                                                                                    2071586, 2072822]


def test_a_raw_line_takes_the_stamp_of_the_line_before_it(L, tmp_path):
    """A line demo's file holds without a stamp (stock's printf, hciconfig's) was stamped when q2guard read it, up to
    a tick later than it was written: 13040 for a "====auto bt" printed between demo's 8493 and 8520 lines. It takes
    the time and uptime of the stamped line before it in the file instead, so it stays where it was written."""
    p = Pair(L, tmp_path)
    u = p.u
    raw(L, u, '2026-11-22T05:22:38Z 8493 I bt auto-connect (auto) picks 20:AF:1B:18:FD:03\n', up=13040)
    raw(L, u, '====auto bt[20:AF:1B:18:FD:03]==\n', up=13040)
    raw(L, u, '2026-11-22T05:22:38Z 8520 I bt stack status 0\n', up=13040)
    assert p.flush() == 0
    lines = p.text('q2-btev.0').splitlines()
    assert lines[1].startswith('2026-11-22T05:22:38Z 8493 I bt ====auto bt[')
    assert [line.split(' ')[1] for line in lines] == ['8493', '8493', '8520']


def test_a_raw_line_with_no_stamped_line_before_it_keeps_its_read_time(L, tmp_path):
    p = Pair(L, tmp_path)
    raw(L, p.u, '====auto bt[20:AF:1B:18:FD:03]==\n', up=13040)
    assert p.flush() == 0
    assert p.text('q2-btev.0').split(' ')[1] == '13040'


def test_the_every_boot_hciconfig_line_says_it_is_expected(L, tmp_path):
    """openBluetooth's hciconfig hci0 reset fails this way at every boot and at no relaunch (2026-11-22, builds 1126
    and 1127); the name is written after it (bt_name.c). An I line that says so, not an error."""
    p = Pair(L, tmp_path)
    raw(L, p.u, "Can't init device hci0: Function not implemented (89)\n")
    assert p.flush() == 0
    (line,) = bt_lines(p)
    assert line.startswith("7000 I bt Can't init device hci0: Function not implemented (89)") and 'expected' in line


def test_a_periodic_flush_holds_back_the_last_seconds(L, tmp_path):
    """q2guard hands its writer only the lines older than the newest few seconds (ulog_settled), so one read late
    from demo's file still finds the lines it belongs before; a final flush takes them all."""
    p = Pair(L, tmp_path)
    for up in (1000, 2000, 9000, 9500):
        p.u.add('I', 'bt', f'line {up}', up=up)
    L.ulog_settled.argtypes, L.ulog_settled.restype = [C.c_void_p, C.c_uint], C.c_uint
    first_two = len(b''.join(f'{TIME} {up} I bt line {up}\n'.encode() for up in (1000, 2000)))
    assert L.ulog_settled(p.ev, 9500 - 6000) == first_two
    assert L.ulog_settled(p.ev, 10000) == L.log_used(p.ev)
    assert L.ulog_settled(p.ev, 500) == 0


def test_a_late_line_never_goes_into_the_part_being_written(L, tmp_path):
    """While q2guard's writer has the ring's first bytes (ulog_bt_freeze), a line older than them goes in after them:
    the watcher drops exactly those bytes when the writer is done."""
    p = Pair(L, tmp_path)
    p.u.add('I', 'bt', 'line 1000', up=1000)
    p.u.add('I', 'bt', 'line 3000', up=3000)
    frozen = len(f'{TIME} 1000 I bt line 1000\n')
    L.ulog_bt_freeze.argtypes = [C.c_uint, C.c_uint]
    L.ulog_bt_freeze(frozen, L.log_dropped(p.ev))
    p.u.add('I', 'bt', 'line 500', up=500)
    L.ulog_bt_freeze(0, 0)
    p.u.add('I', 'bt', 'line 2000', up=2000)
    assert p.flush() == 0
    assert [line.split(' ', 4)[4] for line in p.text('q2-btev.0').splitlines()] == ['line 1000', 'line 500',
                                                                                   'line 2000', 'line 3000']


def test_coda_rc_s_bluetooth_le_lines_come_in(L, tmp_path):
    """coda-rc's own log (/tmp/coda-rc.log, its lines after a UTC stamp) is a feed of its own under source coda-rc,
    its stamp left out as a card app's date is, so its advertising (a Bluetooth LE ADD_ADVERTISING each time) can be
    set beside the storms the kernel logs; its ble: lines go to q2-btev too, its others only to the unified log."""
    p = Pair(L, tmp_path)
    f = tmp_path / 'coda-rc.log'
    f.write_text('2026-11-22T06:07:24Z ble: Bluetooth on; advertising in 2 s\n2026-11-22T06:07:26Z ble: advertising\n'
                 '2026-11-22T06:07:30Z tcp: phone connected\n')
    t = Tail()
    L.ulog_tail_at(C.byref(t), str(f).encode(), 0)
    t.src = b'coda-rc'
    L.ulog_tail(C.byref(t), p.u.log, WALL, 2700000)
    assert p.flush() == 0
    ev = [line.split(' ', 2)[2] for line in p.text('q2-btev.0').splitlines()]
    assert ev == ['I coda-rc ble: Bluetooth on; advertising in 2 s', 'I coda-rc ble: advertising']
    assert p.text('q2-log.0').splitlines()[-1].endswith('I coda-rc tcp: phone connected')
