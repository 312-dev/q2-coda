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

    def state(self, seq, card, left=0, state='ok', beat=None):
        self.beat = beat if beat is not None else getattr(self, 'beat', 0) + 1
        self.files[STATE] = f'seq={seq} card={card} left={left} state={state} t=-1 beat={self.beat}\n'.encode()

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
