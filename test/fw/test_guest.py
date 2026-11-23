"""guest.c (docs/internals.md#guest-play): Guest Play's side in demo. Commands to q2-guest's FIFO, kept while it starts;
its state read for the UI; the owner's music stopped while a guest plays and back after, as Leave says; the car's keys
to the guest; stock's receiver page kept shut while Guest Play is on."""
import ctypes as C

import pytest

O_UP = 30000  # guest.c GUEST_UP_MS
CMD, STATE, PID, CONFIG = '/tmp/q2-guest.cmd', '/tmp/q2-guest.state', '/tmp/q2-guest.pid', '/mnt/data/q2-guest/config'
PLAYING, PAUSED, STOPPED = 2, 3, 1
NEXT, PLAYPAUSE, PREV, STOP, PAUSE, PLAYCD, PAUSECD, PLAYKEY = 163, 164, 165, 166, 119, 200, 201, 207


class Rig:
    """The files guest.c reads, the FIFO (open answers -1 while nothing reads it), system(), the clock and the
    player's status."""
    def __init__(self, fw):
        self.fw, self.files, self.fifo_up, self.lines, self.commands = fw, {}, False, [], []
        self.ms, self.status = 10000, PLAYING
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fread', self.fread)
        fw.mock('fw_fclose', lambda *_: 0)
        fw.mock('fw_open', self.open)
        # the FIFO's lines; stderr's (fd 2) are the unified log's (headset.c says each AVRCP key there)
        fw.mock('fw_write', lambda fd, data, n, *_: (fd != 2 and self.lines.append(C.string_at(data, n).decode())) or n)
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or 0)
        fw.mock('fw_access', lambda path, *_: 0 if C.string_at(path).decode() == '/proc/77' else -1)
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.handles, self.opened = {}, []

    def fopen(self, path, mode, *_):
        path = C.string_at(path).decode()
        self.opened.append(path)
        if path not in self.files:
            return 0
        h = 0x3000 + len(self.handles)
        self.handles[h] = [self.files[path], 0]
        return h

    def fread(self, out, size, count, f, *_):
        data, at = self.handles[f]
        n = min(size * count, len(data) - at)
        C.memmove(out, data[at:at + n], n)
        self.handles[f][1] += n
        return n // size

    def open(self, path, flags, *_):
        assert C.string_at(path).decode() == CMD and flags == 0x81  # O_WRONLY | O_NONBLOCK: never blocks
        return 9 if self.fifo_up else -1

    def state(self, **kv):
        self.files[STATE] = ''.join(f'{k}={v}\n' for k, v in {'seq': 1, **kv}.items()).encode()

    def poll(self):
        self.ms += 300
        self.fw.fn('guest_poll', None)()

    def send(self, line):
        return self.fw.fn('guest_send', C.c_int, C.c_char_p)(line.encode())

    def field(self, key):
        out = C.create_string_buffer(64)
        self.fw.fn('guest_field', None, C.c_char_p, C.c_char_p, C.c_uint)(key.encode(), out, 64)
        return out.value.decode()

    def calls(self, name):
        return [a[0] for _, a in self.fw.calls(name)]


@pytest.fixture
def rig(fw):
    return Rig(fw)


def test_a_command_goes_to_the_fifo_with_its_newline(rig):
    rig.fifo_up = True
    assert rig.send('allow-once') == 1
    assert rig.lines == ['allow-once\n'] and not rig.commands


def test_with_no_daemon_commands_wait_in_order_and_it_is_started(rig):
    assert rig.send('on') == 0 and rig.send('open') == 0
    assert rig.lines == [] and rig.commands == ['( /usr/bin/q2-guest >/dev/null 2>&1 & )']
    rig.poll()  # not up yet: kept, and not started again so soon
    assert rig.lines == [] and len(rig.commands) == 1
    rig.fifo_up = True
    rig.poll()
    assert rig.lines == ['on\n', 'open\n']
    assert rig.send('close') == 1 and rig.lines[-1] == 'close\n'


def test_a_running_daemon_is_not_started_twice(rig):
    rig.files[PID] = b'77\n'
    rig.send('on')
    assert rig.commands == []


def test_a_line_too_long_is_not_sent(rig):
    rig.fifo_up = True
    assert rig.send('name ' + 'x' * 210) == 0 and rig.lines == []


def test_bluetooth_on_starts_it_when_guest_play_is_on(rig, fw):
    rig.files[CONFIG] = b'enabled=0\nname=\n'
    fw.fn('guest_bt_on', None)()
    assert rig.commands == []
    rig.files[CONFIG] = b'enabled=1\nname=\n'
    fw.fn('guest_bt_on', None)()
    assert rig.commands == ['( /usr/bin/q2-guest >/dev/null 2>&1 & )']


def test_the_state_is_read_for_the_ui(rig, fw):
    seq = fw.fn('guest_seq', C.c_uint)
    rig.poll()
    assert seq() == 0 and rig.field('state') == ''
    rig.send('on')  # the daemon starts
    rig.state(seq=7, state='asking', guest="Alex's Pixel", code='482913')
    rig.poll()
    assert seq() == 7 and rig.field('state') == 'asking' and rig.field('guest') == "Alex's Pixel"
    assert rig.field('code') == '482913'


def test_it_is_read_no_more_than_every_quarter_second(rig, fw):
    rig.state(seq=1, state='idle')
    rig.poll()
    rig.state(seq=2, state='idle')
    rig.ms += 100
    fw.fn('guest_poll', None)()
    assert fw.fn('guest_seq', C.c_uint)() == 1


def reads(rig):
    return rig.opened.count(STATE)


def test_with_no_daemon_the_state_file_is_not_asked_for(rig):
    rig.poll()  # the first pass looks: an earlier demo's daemon may still run
    for _ in range(20):
        rig.poll()
    assert reads(rig) == 1


def test_a_daemon_an_earlier_demo_started_is_read_from_the_first_pass(rig, fw):
    rig.state(seq=3, state='idle')
    rig.poll()
    rig.state(seq=4, state='idle')
    rig.poll()
    assert fw.fn('guest_seq', C.c_uint)() == 4


def test_a_started_daemon_is_read_until_it_ends(rig, fw):
    rig.poll()
    rig.files[CONFIG] = b'enabled=1\nname=\n'
    fw.fn('guest_bt_on', None)()  # Bluetooth on starts it; its first state comes a while later
    for _ in range(30):
        rig.poll()
    rig.state(seq=1, state='idle')
    rig.ms += 20000
    rig.poll()
    assert fw.fn('guest_seq', C.c_uint)() == 1
    rig.ms += 60000  # past its start's window, read while the file is there
    rig.state(seq=2, state='idle')
    rig.poll()
    assert fw.fn('guest_seq', C.c_uint)() == 2
    del rig.files[STATE]  # q2-guest removes it as it ends
    rig.poll()
    before = reads(rig)
    for _ in range(20):
        rig.poll()
    assert fw.fn('guest_seq', C.c_uint)() == 0 and reads(rig) == before


def test_a_daemon_that_never_comes_up_is_given_up_after_its_wait(rig, fw):
    rig.send('on')  # started, nothing reads the FIFO: the command waits, and is kept trying
    rig.fifo_up = True
    rig.poll()  # sent: nothing waits now
    rig.ms += O_UP
    before = reads(rig)
    for _ in range(20):
        rig.poll()
    assert reads(rig) == before


def session(rig, status=PLAYING, leave='resume'):
    rig.status = status
    rig.state(state='playing', leave=leave)
    rig.poll()


def test_a_guest_stops_the_owners_local_music_keeping_its_place(rig, fw):
    session(rig)
    assert fw.fn('guest_active', C.c_int)() == 1
    assert len(rig.fw.calls('player_save_stop')) == 1 and not rig.fw.calls('player_play_pause')
    rig.state(state='paused', leave='resume')  # the guest paused: still a session, nothing again
    rig.poll()
    assert len(rig.fw.calls('player_save_stop')) == 1


def test_the_guest_leaving_resumes_it_and_sends_the_owners_song_to_the_car(rig, fw):
    session(rig)
    rig.state(state='idle', leave='resume', event='left')
    rig.poll()
    assert fw.fn('guest_active', C.c_int)() == 0
    assert rig.calls('player_save_start') == [0]
    assert len(rig.fw.calls('sendBtHeadsetPlayInfo')) == 1 and len(rig.fw.calls('sendBtHeadsetPlayStatus')) == 1


def test_stay_paused_brings_it_back_paused(rig):
    session(rig, leave='stay')
    rig.state(state='idle', leave='stay')
    rig.poll()
    assert rig.calls('player_save_start') == [1]


def test_music_that_was_paused_comes_back_paused(rig):
    session(rig, status=PAUSED)
    assert len(rig.fw.calls('player_save_stop')) == 1
    rig.state(state='idle', leave='resume')
    rig.poll()
    assert rig.calls('player_save_start') == [1]


def test_a_stopped_player_is_left_alone(rig):
    session(rig, status=STOPPED)
    rig.state(state='off')
    rig.poll()
    assert not rig.fw.calls('player_save_stop') and not rig.fw.calls('player_save_start')
    assert not rig.fw.calls('player_play_pause') and len(rig.fw.calls('sendBtHeadsetPlayInfo')) == 1


@pytest.mark.parametrize('code, line', [(NEXT, 'key next'), (PREV, 'key prev'), (PLAYPAUSE, 'key toggle'),
                                        (PAUSE, 'key pause'), (PAUSECD, 'key pause'), (STOP, 'key pause'),
                                        (PLAYCD, 'key play'), (PLAYKEY, 'key play')])
def test_the_cars_keys_go_to_the_guest_while_one_plays(rig, fw, code, line):
    key = fw.fn('guest_key', C.c_int, C.c_int)
    rig.fifo_up = True
    assert key(code) == 0 and rig.lines == []  # no guest: the owner's, as before
    session(rig)
    assert key(code) == 1 and rig.lines == [line + '\n']


def test_other_codes_stay_the_owners(rig, fw):
    session(rig)
    rig.fifo_up = True
    assert fw.fn('guest_key', C.c_int, C.c_int)(115) == 0 and rig.lines == []


def test_a_guest_play_key_is_not_the_owners(rig, fw):
    """headset_command hands the key over before any of its own handling."""
    session(rig)
    rig.fifo_up = True
    fw.fn('headset_command', None, C.c_int)(NEXT)
    assert rig.lines == ['key next\n'] and not rig.fw.calls('player_next_music')


def test_the_receiver_page_stays_shut_while_guest_play_is_on(rig, fw):
    a2dp = fw.fn('guest_a2dp', C.c_int, C.c_int, C.c_void_p, C.c_void_p)
    rig.poll()  # no daemon: stock's receiver mode
    assert a2dp(0x103, None, None) == 0 and len(rig.fw.calls('stock_btrecv_trampoline')) == 1
    rig.send('on')  # the daemon starts
    rig.state(state='off')
    rig.poll()
    a2dp(0x103, None, None)
    assert len(rig.fw.calls('stock_btrecv_trampoline')) == 2
    rig.state(state='idle')
    rig.poll()
    assert a2dp(0x103, None, None) == 0 and len(rig.fw.calls('stock_btrecv_trampoline')) == 2
    assert rig.fw.calls('stock_btrecv_trampoline')[0][1][0] == 0x103  # passed on as it came


# Receivers (docs/internals.md#guest-play-receivers): a Spotify Connect session waits for the owner.

class Spot:
    """librespot's control socket: the letters sent to it."""
    def __init__(self, fw):
        self.sent = []
        fw.mock('fw_socket', lambda *_: 5)
        fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: self.sent.append(
            (C.string_at(to + 2).decode(), C.string_at(data, n).decode())) or n)


def net(fw):
    return fw.fn('guest_net', C.c_int, C.c_char_p, C.c_char_p, C.c_char_p)(b'spotify', b'connect', b'Spotify Connect')


def test_without_guest_play_a_receiver_plays_as_before(rig, fw):
    rig.fifo_up = True
    rig.poll()  # no daemon
    assert net(fw) == 0 and rig.lines == []
    rig.state(state='off')
    rig.poll()
    assert net(fw) == 0 and rig.lines == []


def test_a_receiver_is_held_and_the_owner_asked_once(rig, fw):
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    assert net(fw) == 1 and net(fw) == 1
    assert rig.lines == ['net spotify connect Spotify Connect\n']


def test_allowed_it_is_released_and_plays_on(rig, fw):
    spot = Spot(fw)
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    net(fw)
    rig.state(state='asking', source='spotify')
    rig.poll()
    assert spot.sent == []
    rig.state(state='playing', source='spotify')
    rig.poll()
    assert spot.sent == [('/tmp/q2-librespot.sock', 'P')]
    assert net(fw) == 0  # its guest now: spot_take goes on
    assert not rig.fw.calls('player_save_stop')  # the receiver stops the owner's player itself


def test_refused_it_is_paused(rig, fw):
    spot = Spot(fw)
    rig.fifo_up = True
    rig.state(state='idle', netdeny_seq=0)
    rig.poll()
    net(fw)
    rig.state(state='idle', netdeny='spotify', netdeny_seq=1, event='declined')
    rig.poll()
    assert spot.sent == [('/tmp/q2-librespot.sock', 's')]
    rig.poll()
    assert len(spot.sent) == 1  # once a refusal
    assert net(fw) == 1 and rig.lines[-1] == 'net spotify connect Spotify Connect\n'  # a new session asks again


def test_spot_take_holds_a_guest_session(rig, fw):
    spot = Spot(fw)
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    fw.fn('spotify__spot_take', None)()
    assert spot.sent == [('/tmp/q2-librespot.sock', 's')] and not rig.fw.calls('player_stop')
    rig.state(state='playing', source='spotify')
    rig.poll()
    fw.fn('spotify__spot_take', None)()
    assert spot.sent[-1] == ('/tmp/q2-librespot.sock', 'P')


# AirPlay: stock's take-over (airplay_mainscheduling) waits for the owner.

def airplay(fw, flag):
    fw.mock('airplayGetFlag', lambda *_: flag)
    return fw.fn('guest_airplay', C.c_int)()


def test_airplay_without_guest_play_goes_on_as_stock(rig, fw):
    rig.poll()
    airplay(fw, 1)
    assert len(rig.fw.calls('stock_airplay_sched_trampoline')) == 1 and rig.lines == []


def test_airplay_is_held_until_allowed(rig, fw):
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    assert airplay(fw, 1) == 0 and airplay(fw, 1) == 0
    assert not rig.fw.calls('stock_airplay_sched_trampoline') and rig.lines == ['net airplay client AirPlay\n']
    rig.state(state='playing', source='airplay')
    rig.poll()
    airplay(fw, 1)
    assert len(rig.fw.calls('stock_airplay_sched_trampoline')) == 1


def test_airplay_refused_is_stopped(rig, fw):
    rig.fifo_up = True
    rig.state(state='idle', netdeny_seq=0)
    rig.poll()
    airplay(fw, 1)
    rig.state(state='idle', netdeny='airplay', netdeny_seq=1)
    rig.poll()
    assert len(rig.fw.calls('airplaySendStop')) == 1


def test_airplays_end_ends_the_guest(rig, fw):
    rig.fifo_up = True
    rig.state(state='playing', source='airplay')
    rig.poll()
    airplay(fw, 0)
    assert rig.lines == ['net-end airplay\n'] and len(rig.fw.calls('stock_airplay_sched_trampoline')) == 1


def test_the_cars_keys_stay_demos_for_airplay_and_dlna(rig, fw):
    rig.fifo_up = True
    for source in ('airplay', 'dlna'):
        rig.state(state='playing', source=source)
        rig.poll()
        assert fw.fn('guest_active', C.c_int)() == 1 and fw.fn('guest_key', C.c_int, C.c_int)(NEXT) == 0
    assert rig.lines == []


# DLNA: the renderer's actions wait on their own thread for the owner's answer.

class States:
    """The state file as q2-guest writes it over the wait: each read takes the next, the last stays."""
    def __init__(self, rig, *states):
        self.rig, self.states, self.reads = rig, list(states), 0
        rig.fw.mock('fw_usleep', lambda *_: self.next() or 0)
        self.next()

    def next(self):
        self.rig.state(**self.states[min(self.reads, len(self.states) - 1)])
        self.reads += 1


def dlna_play(fw):
    return fw.fn('guest_dlna_play', C.c_int)()


def test_dlna_without_guest_play_plays_at_once(rig, fw):
    rig.poll()
    dlna_play(fw)
    assert len(rig.fw.calls('stock_dlna_play_trampoline')) == 1 and rig.lines == []


def test_dlna_asks_and_plays_once_allowed(rig, fw):
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    States(rig, dict(state='idle'), dict(state='asking', source='dlna'), dict(state='playing', source='dlna'))
    dlna_play(fw)
    assert rig.lines == ['net dlna renderer DLNA\n'] and len(rig.fw.calls('stock_dlna_play_trampoline')) == 1


def test_dlna_refused_does_nothing(rig, fw):
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    States(rig, dict(state='idle', netdeny_seq=4), dict(state='asking', source='dlna', netdeny_seq=4),
           dict(state='idle', netdeny='dlna', netdeny_seq=5))
    assert dlna_play(fw) == 0 and not rig.fw.calls('stock_dlna_play_trampoline')


def test_dlna_no_answer_does_nothing_after_its_wait(rig, fw):
    rig.fifo_up = True
    rig.state(state='idle')
    rig.poll()
    s = States(rig, dict(state='asking', source='dlna'))
    fw.fn('guest_dlna_uri', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert not rig.fw.calls('stock_dlna_uri_trampoline') and s.reads == 141
    assert rig.lines == []  # already asking about DLNA: not asked twice


def test_dlnas_stop_ends_the_guest(rig, fw):
    rig.fifo_up = True
    rig.state(state='playing', source='dlna')
    rig.poll()
    fw.fn('guest_dlna_stop', C.c_int)()
    assert rig.lines == ['net-end dlna\n'] and len(rig.fw.calls('stock_dlna_stop_trampoline')) == 1
