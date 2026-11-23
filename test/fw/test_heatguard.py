"""The overheat guardian's cards in demo (patch/heatguard.c, docs/internals.md#overheat-guardian): what q2guard says
in /tmp/q2guard.state becomes a toast or a card, and demo answers in /tmp/q2guard.ack."""
import ctypes as C

import pytest

STATE, ACK = '/tmp/q2guard.state', '/tmp/q2guard.ack'
PLAYING, PAUSED = 2, 3
ANSWER = C.CFUNCTYPE(None, C.c_void_p, C.c_int, C.c_int)


class Confirm(C.Structure):
    _fields_ = [('title', C.c_char_p), ('name', C.c_char_p), ('body', C.c_char_p), ('code', C.c_char_p),
                ('rows', C.POINTER(C.c_char_p)), ('n_rows', C.c_int), ('kind', C.c_int), ('back', C.c_int),
                ('toggle', C.c_char_p), ('toggle_on', C.c_int), ('timeout_ms', C.c_int), ('timeout_row', C.c_int),
                ('answer', C.c_void_p), ('ctx', C.c_void_p), ('glyph', C.c_char_p)]


class Rig:
    """The state file, the answers written, the cards and toasts, the clock, the player and the pages opened."""
    def __init__(self, fw):
        self.fw, self.files, self.ms, self.status = fw, {}, 100000, PLAYING
        self.cards, self.toasts, self.pages, self.pauses, self.card_up = [], [], [], 0, False
        self.fds, self.written = {}, {}
        fw.mock('fw_open', self.open)
        fw.mock('fw_read', self.read)
        fw.mock('fw_write', self.write)
        fw.mock('fw_close', lambda *_: 0)
        self.installed = True
        fw.mock('fw_access', lambda path, *_: 0 if self.installed and C.string_at(path) == b'/usr/bin/q2guard' else -1)
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_play_pause', self.pause)
        fw.mock('navigator_to', lambda name, *_: self.pages.append(C.string_at(name).decode()) or 0)
        self.seams = [C.CFUNCTYPE(C.c_int, C.c_void_p)(self.confirm),
                      C.CFUNCTYPE(None, C.c_char_p, C.c_char_p)(lambda glyph, text: self.toasts.append(text.decode()))]
        C.c_void_p.in_dll(fw.lib, 'hg_confirm').value = C.cast(self.seams[0], C.c_void_p).value
        C.c_void_p.in_dll(fw.lib, 'hg_toast').value = C.cast(self.seams[1], C.c_void_p).value

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        if flags & 1:  # O_WRONLY: an answer
            fd = 40 + len(self.fds)
            self.fds[fd] = [path, b'']
            return fd
        if path not in self.files:
            return -1
        fd = 40 + len(self.fds)
        self.fds[fd] = [path, self.files[path]]
        return fd

    def read(self, fd, out, n, *_):
        data = self.fds[fd][1][:n]
        C.memmove(out, data, len(data))
        self.fds[fd][1] = self.fds[fd][1][len(data):]
        return len(data)

    def write(self, fd, data, n, *_):
        path = self.fds[fd][0]
        self.written[path] = C.string_at(data, n).decode()
        return n

    def pause(self, *_):
        self.pauses += 1
        self.status = PAUSED
        return 0

    def confirm(self, c):
        if self.card_up:
            return 0
        card = Confirm.from_address(c)
        rows = [card.rows[i].decode() for i in range(card.n_rows)]
        self.cards.append({'title': card.title.decode(), 'body': card.body.decode(), 'rows': rows,
                           'timeout': card.timeout_ms, 'timeout_row': card.timeout_row,
                           'answer': ANSWER(card.answer), 'ctx': card.ctx})
        return 1

    def state(self, seq, card, left=0, state='ok', beat=None, t=-1):
        self.beat = beat if beat is not None else getattr(self, 'beat', 0) + 1
        self.files[STATE] = f'seq={seq} card={card} left={left} state={state} t={t} beat={self.beat}\n'.encode()

    def poll(self, ms=500):
        self.ms += ms
        self.fw.fn('heatguard_poll', None)()

    def answer(self, row):
        card = self.cards[-1]
        card['answer'](card['ctx'], row, 0)


@pytest.fixture
def rig(fw):
    return Rig(fw)


def test_the_warning_is_a_toast_once_per_seq(rig):
    rig.state(1, 'warn')
    for _ in range(4):
        rig.poll()
    assert rig.toasts == ['Q2 is getting hot'] and rig.cards == []
    rig.state(2, 'warn')
    rig.poll()
    assert rig.toasts == ['Q2 is getting hot'] * 2


def test_sensor_down_is_a_card_with_one_ok_that_waits_its_turn(rig):
    """Another card up: tried again on the next poll; then shown once, with a single OK and no timeout."""
    rig.card_up = True
    rig.state(1, 'nosensor', state='nosensor')
    rig.poll()
    assert rig.cards == []
    rig.card_up = False
    rig.poll()
    rig.poll()
    card, = rig.cards
    assert card['title'] == 'No Temperature Sensor' and card['rows'] == ['OK'] and card['timeout'] == 0
    assert 'watch CPU load instead' in card['body']
    assert 'card nosensor\n' in rig.written[ACK]  # what smoke.py confirms at boot
    rig.answer(0)
    assert 'card -\n' in rig.written[ACK]


def test_a_die_temperature_reading_raises_no_card(rig):
    """The Q2's PMIC die read and trusted (q2guard_pmic.c): q2guard goes from pending to ok with no card, so demo
    shows nothing, for minutes, whatever the temperature as long as it is under the warning."""
    rig.state(0, 'none', state='pending')
    rig.poll()
    for i in range(360):  # 3 minutes of 500 ms polls, a new beat each
        rig.state(1, 'none', state='ok', t=381 + i % 3)
        rig.poll()
    assert rig.cards == [] and rig.toasts == []
    assert 'card -' in rig.written.get(ACK, 'card -')


def test_a_die_sensor_that_fails_later_brings_the_card_once(rig):
    """The die read for a while, then the PMIC reader turned off (q2guard logs why): the card once, CPU load from
    then on."""
    for _ in range(10):
        rig.state(1, 'none', state='ok', t=381)
        rig.poll()
    for _ in range(10):
        rig.state(2, 'nosensor', state='nosensor')
        rig.poll()
    assert [c['title'] for c in rig.cards] == ['No Temperature Sensor']


def test_the_no_sensor_card_closes_by_itself_once_the_sensor_is_back(rig, fw):
    """The owner's Q2 on 2026-11-23: "No Temperature Sensor" raised after a Standby (SENSOR none usable: jumps), the
    sensor ok 15 s later, and the card still on the screen an hour on, holding the update check busy. Once q2guard
    says the sensor is trusted again (card none, state ok) the card closes with no OK and the ack says no card is up;
    a guardian still down keeps it. Red before: the card stayed and the ack said nosensor."""
    lines = []
    fw.mock('fw_write', lambda fd, data, n, *_: (lines.append(C.string_at(data, n).decode()) if fd == 2 else
                                                 rig.write(fd, data, n)) or n)
    rig.state(1, 'none', state='ok', t=401)
    rig.poll()
    rig.state(2, 'nosensor', state='nosensor')
    rig.poll()
    assert [c['title'] for c in rig.cards] == ['No Temperature Sensor'] and 'card nosensor\n' in rig.written[ACK]
    for _ in range(4):  # still down: it stays
        rig.state(2, 'nosensor', state='nosensor')
        rig.poll()
    assert 'card nosensor\n' in rig.written[ACK]
    rig.state(3, 'none', state='ok', t=365)
    rig.poll()
    assert 'card -\n' in rig.written[ACK], rig.written[ACK]
    assert any('No Temperature Sensor closed: the sensor is back' in line for line in lines)
    assert len(rig.cards) == 1, 'nothing raised again'


def test_the_load_warning_counts_down_and_its_cancel_is_answered(rig):
    rig.state(3, 'loadwarn', left=60000, state='loadwarn')
    rig.poll()
    card, = rig.cards
    assert card['rows'] == ['Cancel', 'Shut Down'] and card['timeout'] == 60000 and card['timeout_row'] == 1
    rig.answer(0)
    assert 'cancel 3' in rig.written[ACK]


def test_the_load_warning_running_out_does_not_cancel(rig):
    rig.state(3, 'loadwarn', left=60000, state='loadwarn')
    rig.poll()
    rig.answer(1)  # the countdown's end
    assert 'cancel 3' not in rig.written.get(ACK, '')


def test_the_power_off_answers_pauses_counts_down_then_shuts_down_stocks_way(rig):
    rig.state(5, 'off', left=10000, state='poweroff-hot')
    rig.poll()
    assert 'ack 5' in rig.written[ACK] and rig.pauses == 1
    card, = rig.cards
    assert card['title'] == 'Q2 is too hot' and card['body'] == 'Shutting down to cool off.'
    assert card['rows'] == ['Shutting Down'] and 0 < card['timeout'] <= 10000
    rig.answer(0)
    assert rig.pages == ['poweroff_page']


def test_a_power_off_with_its_card_blocked_still_shuts_down_on_time_and_pauses_once(rig):
    rig.card_up = True
    rig.state(5, 'off', left=10000, state='poweroff-hot')
    for _ in range(19):
        rig.poll()
    assert rig.pages == [] and rig.pauses == 1
    rig.poll(1000)
    assert rig.pages == ['poweroff_page']
    rig.poll()
    assert rig.pages == ['poweroff_page']


def test_paused_audio_is_not_toggled_back_on(rig):
    rig.status = PAUSED
    rig.state(5, 'off', left=10000)
    rig.poll()
    assert rig.pauses == 0


def test_a_silent_guardian_after_a_minute_is_a_card_once(rig):
    rig.state(0, 'none', beat=7)
    rig.poll()
    for _ in range(130):  # its beat never moves for 65 s
        rig.poll()
    assert [c['title'] for c in rig.cards] == ["Overheat protection isn't running"]


def test_a_guardian_that_beats_raises_no_card(rig):
    for i in range(140):
        rig.state(0, 'none')
        rig.poll()
    assert rig.cards == []


def test_with_no_guardian_installed_there_is_no_card(rig):
    """A build or a test world without /usr/bin/q2guard has nothing to miss."""
    rig.installed = False
    for _ in range(140):
        rig.poll()
    assert rig.cards == []


class Bt:
    """Stock's Bluetooth switch recorded, and the globals the relaunch's gate reads."""
    def __init__(self, fw, rig):
        self.fw, self.rig, self.calls = fw, rig, []
        fw.mock('switchBluetooth', lambda off, a1, *_: self.calls.append((rig.ms, off, a1)) or 0)
        self.set(on=1)

    def set(self, on=1, search=0, init=0, connect=0, direction=0):
        self.fw.var('g_bluetoothflag', C.c_ubyte).value = on
        self.fw.var('bt_searchtime', C.c_ubyte).value = search
        self.fw.var('fw_ctx_bt_initstatus', C.c_int).value = init
        self.fw.var('fw_ctx_bt_connectstatus', C.c_int).value = connect
        self.fw.var('fw_ctx_bt_a2dp_direction', C.c_int).value = direction


def bt_state(rig, req, card='none', seq=0):
    rig.state(seq, card)
    rig.files[STATE] = rig.files[STATE].replace(b'\n', f' bt={req}\n'.encode())


def test_a_relaunch_is_stocks_switch_off_then_on(rig, fw):
    """q2guard's bt=N (docs/internals.md#bluetooth-controller-watchdog): switchBluetooth(1, 0), answered at once,
    then a second later switchBluetooth(0, 0), which starts the stack again and reconnects as any Bluetooth on."""
    bt = Bt(fw, rig)
    bt_state(rig, 0)
    rig.poll()
    bt_state(rig, 1)
    rig.poll()
    off_at = rig.ms
    assert bt.calls == [(off_at, 1, 0)] and rig.written[ACK].endswith('bt 1 done\n')
    rig.poll()  # 500 ms: the radio still off
    assert len(bt.calls) == 1
    rig.poll()
    assert bt.calls == [(off_at, 1, 0), (off_at + 1000, 0, 0)]
    for _ in range(4):  # the same ask is not made twice
        rig.poll()
    assert len(bt.calls) == 2
    bt_state(rig, 2)
    rig.poll()
    assert len(bt.calls) == 3 and rig.written[ACK].endswith('bt 2 done\n')


@pytest.mark.parametrize('busy', [dict(init=1), dict(init=-102), dict(connect=1), dict(direction=1)])
def test_never_while_bluetooth_is_busy(rig, fw, busy):
    """The stack starting (or failed), a link thread connecting or pairing, a link being set up: the ask waits for the
    next poll, unanswered."""
    bt = Bt(fw, rig)
    bt_state(rig, 0)
    rig.poll()
    bt.set(**busy)
    bt_state(rig, 1)
    for _ in range(6):
        rig.poll()
    assert bt.calls == [] and not rig.written.get(ACK, '').endswith('bt 1 done\n')
    bt.set()
    rig.poll()
    assert [c[1:] for c in bt.calls] == [(1, 0)] and rig.written[ACK].endswith('bt 1 done\n')


def test_a_scan_does_not_hold_off_a_relaunch(rig, fw):
    """2026-11-19, V1JAi: a storm from 20:36:00Z, BT RESET asked at 20:36:26Z, and demo relaunched only at 20:37:26Z,
    when the pairing page's scan ended: its commands had timed out in the kernel (0x0401 and 0x2041 tx timeout), as a
    wedged controller answers none. A scan cannot finish on it, and stock's switch off stops the scan first
    (btStopSearch), so a scan running is no reason to wait: the ask is answered on the next poll."""
    bt = Bt(fw, rig)
    bt_state(rig, 0)
    rig.poll()
    bt.set(search=50)
    bt_state(rig, 1)
    rig.poll()
    assert [c[1:] for c in bt.calls] == [(1, 0)] and rig.written[ACK].endswith('bt 1 done\n')


def test_bluetooth_off_is_answered_with_nothing_done(rig, fw):
    bt = Bt(fw, rig)
    bt_state(rig, 0)
    rig.poll()
    bt.set(on=0)
    bt_state(rig, 1)
    rig.poll()
    rig.poll()
    rig.poll()
    assert bt.calls == [] and rig.written[ACK].endswith('bt 1 off\n')


def test_an_ask_from_before_demo_started_is_old(rig, fw):
    """demo started again finds bt=3 in the state file: that ask was answered by the demo before it, or given up."""
    bt = Bt(fw, rig)
    bt_state(rig, 3)
    for _ in range(4):
        rig.poll()
    assert bt.calls == []
    bt_state(rig, 4)
    rig.poll()
    assert [c[1:] for c in bt.calls] == [(1, 0)]


def test_not_while_powering_off(rig, fw):
    bt = Bt(fw, rig)
    bt_state(rig, 0)
    rig.poll()
    bt_state(rig, 1, card='off', seq=1)
    for _ in range(4):
        rig.poll()
    assert bt.calls == []
