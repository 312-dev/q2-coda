"""Car Remote's side in demo, patch/coda_rc_shim.c (docs/internals.md#car-remote): the daemon started only while
the switch is on, Now Playing written for it, its play, Play/Pause, Next and Previous, seek and volume commands,
the pairing code's Dialog and Settings > Car Remote with its phones and Forget. /tmp and /mnt/data are a dict (conftest.Files); AWTK is
test_nav_library_pages' fake, and the songs are test_nav_hold's Records, laid out as P() reads them."""
import ctypes as C
import pytest

from conftest import O, Files, mcl  # noqa: F401
from test_nav_library_pages import KEY_RETURN, Config, Ui, call, empty_state, ui  # noqa: F401
from test_nav_hold import recs  # noqa: F401

NOW, CMD, LIST, ACK, QUEUE = '/tmp/coda-rc.now', '/tmp/coda-rc.cmd', '/tmp/coda-rc.list', '/tmp/coda-rc.ack', '/tmp/coda-rc.queue'
PAIR, PAIR_END, PAIR_CANCEL, PEERS = '/tmp/coda-rc.pair', '/tmp/coda-rc.pair-end', '/tmp/coda-rc.pair-cancel', '/mnt/data/coda-rc/peers'


class Q2:
    def __init__(self, fw, ui, recs, on=1, songs=4):
        self.fw, self.ui, self.ms = fw, ui, 1_000_000
        self.files = Files(fw).files
        self.config = Config(fw, CARREMOTE=str(on).encode())
        self.system = []
        fw.mock('time_now_ms', lambda *_: self.ms & 0xffffffff)
        fw.mock('fw_system', lambda cmd, *_: self.system.append(C.string_at(cmd).decode()) or 0)
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p).decode() in self.files else -1)
        self.songs = [recs(id_=10 + i, path=f'/mnt/mmc/a/{i}.flac', name=f'{i}.flac', title=f'Song {i}', artist='Artist',
                           album='Album') for i in range(songs)]
        fw.mock('getAllMusic', lambda *_: ui.deques[self.directory()].extend(self.songs) or len(self.songs))
        self.status, self.sec, self.total = 2, 30, 200
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        fw.mock('player_playtime_and_length', self.times)
        self.poll = fw.fn('coda_rc_poll', None)
        fw.mock('window_close', lambda w, *_: ui.destroy(w))  # AWTK's: the window and its destroy handlers

    def directory(self):
        return self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value

    def times(self, sec, total, *_):
        C.c_int.from_address(sec).value, C.c_int.from_address(total).value = self.sec, self.total
        return 0

    def queue(self, at=1):
        self.fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.ui.deques.new(self.songs)
        C.c_int.from_address(O['MCL_POS']).value = at

    def tick(self, ms=600):
        self.ms += ms
        self.poll()

    def now(self):
        return dict(line.split('=', 1) for line in self.files[NOW].decode().splitlines())


def test_off_starts_nothing_and_writes_nothing(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs, on=0)
    q2.queue()
    for _ in range(3):
        q2.tick()
    assert q2.system == [] and not any(p.startswith('/tmp/coda-rc') for p in q2.files)


def test_on_starts_the_daemon_once_and_writes_now_playing(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.queue(at=2)
    q2.tick()
    assert q2.system == ['( /usr/bin/coda-rc >/dev/null 2>&1 & )']
    now = q2.now()
    assert now['state'] == '1' and now['pos'] == '30000' and now['len'] == '200' and now['id'] == '12'
    assert now['title'] == 'Song 2' and now['artist'] == 'Artist' and now['path'] == '/mnt/mmc/a/2.flac'
    assert now['qpos'] == '2' and now['qlen'] == '4'
    assert q2.files[QUEUE].decode().split('\n')[:3] == ['1 2 4', '10', '11']
    seq = now['seq']
    q2.tick()
    assert q2.now()['seq'] == seq  # nothing changed: nothing written
    q2.status = 3
    q2.tick()
    assert q2.now()['state'] == '2' and int(q2.now()['seq']) == int(seq) + 1
    q2.files['/tmp/coda-rc.pid'] = b'4242\n'
    q2.files['/proc/4242'] = b''
    q2.tick(31000)
    assert len(q2.system) == 1  # running: not started again


def test_a_dead_daemon_is_started_again_after_30_s(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.tick(10000)
    assert len(q2.system) == 1
    q2.tick(21000)
    assert len(q2.system) == 2


def test_play_queues_the_listed_ids_from_one(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.files[LIST] = b'13\n99\n11\n10\n'  # 99 is not in the library: passed over
    q2.files[CMD] = b'7 play 2 0 4\n'
    q2.tick()
    ui.fire('cr_play')  # from a timer: play_folder opens a window
    (queue, at, cls), = ui.played
    assert [C.c_int.from_address(r).value for r in queue] == [13, 11, 10] and at == 1 and cls == 0xf001
    assert q2.files[ACK] == b'7 0\n' and CMD not in q2.files


def test_play_while_usb_storage_is_busy(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    fw.mock('navigator_window_is_exist', lambda name, *_: C.string_at(name) == b'usbmode_page')
    q2.files[LIST] = b'10\n'
    q2.files[CMD] = b'3 play 0 0 1\n'
    q2.tick()
    ui.fire('cr_play')
    assert not ui.played and q2.files[ACK] == b'3 2\n'


def test_seek_and_volume(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    fw.var('g_maxvolume', C.c_ubyte).value = 60
    q2.files[CMD] = b'4 seek 75\n'
    q2.tick()
    assert [a[0] for _, a in fw.calls('player_seek_time')] == [75] and q2.files[ACK] == b'4 0\n'
    q2.files[CMD] = b'5 volume 90\n'
    q2.tick()
    assert fw.var('g_volume', C.c_ubyte).value == 60  # up to Volume Limit
    assert [a[:2] for _, a in fw.calls('device_set_volume')] == [(60, 1)] and q2.files[ACK] == b'5 0\n'
    q2.files[CMD] = b'6 volume 101\n'
    q2.tick()
    assert q2.files[ACK] == b'6 1\n'


def test_pairing_shows_the_code_and_cancel_tells_the_daemon(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.files[PAIR] = b'123456\nPixel 8\n'
    q2.tick()
    page = ui.top()
    assert ui.name[page] == 'carpair_page'
    assert {'Pair with "Pixel 8"?', '123 456', 'Enter this code on your phone'} <= set(ui.shown(page))
    assert ui.captions(page) == ['Cancel']
    ui.key(page, KEY_RETURN)
    assert q2.files[PAIR_CANCEL] == b'1\n' and page not in ui.windows


def test_pairing_closes_when_the_daemon_ends_it_and_toasts(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.files[PAIR] = b'654321\nPhone\n'
    q2.tick()
    page = ui.top()
    del q2.files[PAIR]
    q2.files[PAIR_END] = b'1\n'
    q2.tick()
    assert page not in ui.windows and PAIR_END not in q2.files
    ui.fire('cr_toast')
    assert ui.toasts == ['Phone paired']


def peer(name):
    return bytes(8) + bytes(32) + name.encode().ljust(32, b'\0') + bytes(4)


def test_settings_page_lists_phones_and_forgets_one(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.files[PEERS] = peer('Pixel 8') + peer('Car tablet')
    call(fw, 'carremote_open')
    page = ui.top()
    assert ui.name[page] == 'carremote_page'
    rows = ui.captions(page)
    assert rows[0] == 'Car Remote' and rows[2:] == ['Pixel 8', 'Car tablet']
    ui.click(page, 3)
    ui.fire('cr_page_show')
    assert ui.shown(page)[0] == 'Forget "Car tablet"?' or 'Forget "Car tablet"?' in ui.shown(page)
    assert ui.captions(page) == ['Cancel', 'Forget']
    ui.click(page, 1)
    ui.fire('cr_page_show')
    assert q2.files[PEERS] == peer('Pixel 8') and ui.captions(page)[2:] == ['Pixel 8']


def test_settings_switch_turns_it_on_and_off(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs, on=0)
    call(fw, 'carremote_open')
    page = ui.top()
    # no phone: the empty state under the switch, with no row (a phone pairs from its own app)
    assert len(ui.rows(page)) == 2 and 'No paired phones' not in ui.shown(page)  # the switch and its gap
    assert empty_state(ui, page) == ('car-profile', 'No Paired Phones', 'Open the Car Remote app on your phone', None)
    ui.click(page, 0)
    assert [a[:1] for _, a in fw.calls('write_int_config')][-1] == (1,) and q2.system[-1].startswith('( /usr/bin/coda-rc')
    ui.click(page, 0)
    assert [a[:1] for _, a in fw.calls('write_int_config')][-1] == (0,) and q2.system[-1].startswith('kill $(cat /tmp/coda-rc.pid')


def test_pause_and_play_go_to_the_player_and_ack_once_it_shows_them(fw, ui, mcl, recs):
    """Not a key press: the toast's msginfo_dialog_on_keyup swallows the Q2's own Play/Pause button."""
    q2 = Q2(fw, ui, recs)
    q2.queue()
    q2.tick()
    q2.files[CMD] = b'8 pause\n'
    q2.tick()
    assert len(fw.calls('player_play_pause')) == 1 and ACK not in q2.files  # the player has not paused yet
    q2.status = 3
    q2.tick()
    assert q2.files.pop(ACK) == b'8 0\n'
    q2.files[CMD] = b'9 pause\n'  # already paused: nothing pressed, answered at once
    q2.tick()
    assert len(fw.calls('player_play_pause')) == 1 and q2.files.pop(ACK) == b'9 0\n'
    q2.files[CMD] = b'10 toggle\n'
    q2.tick()
    q2.status = 2
    q2.tick()
    assert len(fw.calls('player_play_pause')) == 2 and q2.files.pop(ACK) == b'10 0\n'


def test_a_play_the_player_never_shows_is_refused(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.status = 3
    q2.tick()
    q2.files[CMD] = b'11 resume\n'
    q2.tick()
    q2.tick(2000)
    assert ACK not in q2.files
    q2.tick(1100)
    assert q2.files.pop(ACK) == b'11 1\n'
    q2.tick(5000)
    assert ACK not in q2.files


def test_a_newer_command_drops_the_waiting_ones_ack(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.files[CMD] = b'12 pause\n'
    q2.tick()
    q2.files[CMD] = b'13 seek 5\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'13 0\n'
    q2.status = 3
    q2.tick(4000)
    assert ACK not in q2.files


def test_next_and_previous_go_to_the_player(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.files[CMD] = b'14 next\n'
    q2.tick()
    assert len(fw.calls('player_next_music')) == 1 and q2.files.pop(ACK) == b'14 0\n'
    q2.files[CMD] = b'15 previous\n'
    q2.tick()
    assert len(fw.calls('player_prev_music')) == 1 and q2.files.pop(ACK) == b'15 0\n'


def test_mode_sets_shuffle_and_repeat_as_the_hold_menu(fw, ui, mcl, recs):
    """SetMode's "mode <shuffle> <repeat>": the hold menu's playback_set, the nearest Q2 mode, -1 left alone."""
    q2 = Q2(fw, ui, recs)
    q2.queue()
    q2.tick()
    option = fw.fn('playback_option', C.c_int, C.c_int)
    q2.files[CMD] = b'20 mode 1 2\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'20 0\n'
    assert (option(0), option(1)) == (1, 5)  # All Songs, Repeat All
    assert q2.now()['shuffle'] == '1' and q2.now()['repeat'] == '2'  # written before the ack
    q2.files[CMD] = b'21 mode -1 1\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'21 0\n' and (option(0), option(1)) == (1, 3)  # shuffle kept, Repeat Song
    q2.files[CMD] = b'22 mode 0 0\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'22 0\n' and (option(0), option(1)) == (0, 2)  # Off, Play All
    fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(0, 3)  # Albums, from the hold menu
    fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(1, 4)  # Repeat Album
    q2.files[CMD] = b'23 mode 1 2\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'23 0\n' and (option(0), option(1)) == (3, 4)  # both kept: already on


def test_rate_hearts_the_playing_song_and_now_playing_shows_them(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.queue(at=2)
    C.c_int.from_address(O['MCL_TYPE']).value = 1
    q2.tick()
    assert q2.now()['hearts'] == '0'
    q2.files[CMD] = b'30 rate 3 12\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'30 0\n' and q2.now()['hearts'] == '3'
    assert not fw.calls('batch_add_file')
    q2.files[CMD] = b'31 rate 5 12\n'  # Favorites: batch-select's Add on the queue's row, as the menu's
    q2.tick()
    assert q2.files.pop(ACK) == b'31 0\n'
    (_, args), = fw.calls('batch_add_file')
    assert args[:2] == (0xf001, 0xf00a) and [a[0] for _, a in fw.calls('batch_set_selectitem')] == [2]
    q2.files[CMD] = b'32 rate 4 11\n'  # not the playing song: NOT_FOUND, nothing kept
    q2.tick()
    assert q2.files.pop(ACK) == b'32 3\n'


def test_a_stream_has_no_hearts(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.queue()
    C.c_int.from_address(O['MCL_TYPE']).value = 0xf1
    q2.tick()
    assert q2.now()['hearts'] == '-1'


def test_jump_plays_a_queue_entry_if_it_is_still_that_song(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.queue(at=0)
    q2.tick()
    q2.files[CMD] = b'40 jump 3 13\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'40 0\n' and [a[0] for _, a in fw.calls('player_start_queue')] == [3]
    q2.files[CMD] = b'41 jump 2 13\n'  # the queue moved under the phone
    q2.tick()
    assert q2.files.pop(ACK) == b'41 3\n' and len(fw.calls('player_start_queue')) == 1
    q2.files[CMD] = b'42 jump 9 13\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'42 3\n'
    fw.mock('check_forbid_play_no_headset', lambda *_: 1)  # Headphones required
    q2.files[CMD] = b'43 jump 1 11\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'43 1\n' and len(fw.calls('player_start_queue')) == 1


AVRCP = '( /usr/bin/q2-avrcp >/dev/null 2>&1 & )'


def test_bluetooth_on_writes_now_playing_for_q2_avrcp_without_car_remote(fw, ui, mcl, recs):
    """docs/internals.md#track-metadata: with Bluetooth on, Now Playing goes to the file and q2-avrcp is started (once,
    and again within 30 s of dying), Car Remote off; its queue file is Car Remote's alone."""
    q2 = Q2(fw, ui, recs, on=0)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    q2.queue(at=1)
    q2.tick()
    assert q2.system == [AVRCP] and q2.now()['title'] == 'Song 1' and QUEUE not in q2.files
    q2.files['/tmp/q2-avrcp.pid'] = b'4343\n'
    q2.files['/proc/4343'] = b''
    q2.tick(31000)
    assert q2.system == [AVRCP]  # running
    del q2.files['/proc/4343']
    q2.tick(31000)
    assert q2.system == [AVRCP] * 2  # gone: started again
    q2.status = 3
    q2.tick()
    assert q2.now()['state'] == '2'


def test_bluetooth_and_car_remote_start_both(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    q2.queue()
    q2.tick()
    assert sorted(q2.system) == sorted([AVRCP, '( /usr/bin/coda-rc >/dev/null 2>&1 & )']) and QUEUE in q2.files
