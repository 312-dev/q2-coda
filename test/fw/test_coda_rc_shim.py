"""Car Remote's side in demo, patch/coda_rc_shim.c (docs/internals.md#car-remote): the daemon started only while
the switch is on, Now Playing written for it, its play, Play/Pause, Next and Previous, seek and volume commands,
the pairing code's Dialog (Settings > Car Remote is test_carremote_page.py's). /tmp and /mnt/data are a dict (conftest.Files); AWTK is
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


def test_car_audio_gets_now_playing_and_buttons_with_car_remote_off(fw, ui, mcl, recs):
    """Car Audio's q2-iap (patch/q2_iap.c) reads Now Playing and writes the car's buttons in Car Remote's files: while
    its iPhone is on the cable (usb_car.c) they work with the switch off, and no daemon starts; once it is off, none."""
    q2 = Q2(fw, ui, recs, on=0)
    q2.queue()
    fw.fn('usb_car_start', None)()
    q2.system.clear()
    q2.tick()
    assert q2.now()['title'] == 'Song 1' and q2.system == []
    q2.files[CMD] = b'1359998977 next\n'
    q2.tick()
    assert len(fw.calls('player_next_music')) == 1 and q2.files.pop(ACK) == b'1359998977 0\n'
    fw.fn('usb_car_stop', None, C.c_int)(1)
    q2.files.pop(NOW)
    q2.files[CMD] = b'1359998978 next\n'
    q2.tick()
    assert NOW not in q2.files and len(fw.calls('player_next_music')) == 1


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


IROH_START = ('( /usr/bin/q2-iroh serve --key /mnt/data/coda-rc/iroh.key --relay-file /mnt/data/coda-rc/relay '
              '--info /tmp/coda-rc.iroh --pid /tmp/q2-iroh.pid --nice 19 >/tmp/q2-iroh.log 2>&1 & )')


def test_q2_iroh_runs_while_car_remote_is_on(fw, ui, mcl, recs):
    """docs/remote.md#car-remote-across-networks: the sidecar starts with the daemon, a stale id file gone first, and
    stops with it; nothing of it is left once Car Remote is off."""
    q2 = Q2(fw, ui, recs)
    q2.files['/usr/bin/q2-iroh'] = b''
    q2.files['/tmp/coda-rc.iroh'] = b'id dead\n'  # a killed sidecar's
    q2.tick()
    assert q2.system == [IROH_START, '( /usr/bin/coda-rc >/dev/null 2>&1 & )']
    assert '/tmp/coda-rc.iroh' not in q2.files
    q2.files['/tmp/coda-rc.iroh'] = b'id 8aac\n'
    fw.fn('coda_rc_toggle', None)()
    assert q2.system[-1] == 'kill $(cat /tmp/coda-rc.pid /tmp/q2-iroh.pid 2>/dev/null) 2>/dev/null'
    assert '/tmp/coda-rc.iroh' not in q2.files


def test_a_dead_q2_iroh_is_started_again_beside_a_live_daemon(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.files['/usr/bin/q2-iroh'] = b''
    q2.tick()
    q2.files.update({'/tmp/coda-rc.pid': b'4242\n', '/proc/4242': b'', '/tmp/q2-iroh.pid': b'4343\n', '/proc/4343': b''})
    q2.tick(31000)
    assert len(q2.system) == 2  # both running: neither started again
    del q2.files['/proc/4343']
    q2.tick(31000)
    assert q2.system[2:] == [IROH_START]  # only the sidecar


def test_an_image_without_q2_iroh_starts_the_daemon_alone(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.files.update({'/tmp/coda-rc.pid': b'4242\n', '/proc/4242': b''})
    q2.tick(31000)
    assert q2.system == ['( /usr/bin/coda-rc >/dev/null 2>&1 & )']


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


def card_route(fw, ui):
    """navigator_to opening stock's sortselect_dialog, as confirm() (feedback.c) asks for its card."""
    def go(name, *_):
        name = C.string_at(name).decode()
        ui.went.append(name)
        if name == 'dialog/sortselect_dialog':
            d = ui.new('window_create', 0, 0, 0, 375, 320)
            ui.name[d] = 'sortselect_dialog'
            nav = ui.new('view', d, 0, 0, 375, 44)
            ui.name[nav] = 'view_navbar'
            ui.name[ui.new('image_create', nav, 0, 0, 44, 44)] = 'img_return'
            lv = ui.new('list_view', d, 0, 44, 375, 210)
            ui.name[lv] = 'list_view'
            ui.name[ui.new('scroll_view_create', lv, 0, 0, 375, 210)] = 'scroll_view'
    fw.mock('navigator_to', go)


def test_pairing_asks_on_the_pairing_card_and_cancel_tells_the_daemon(fw, ui, mcl, recs):
    """The code on pair_prompt.c's card (docs/oled-design.md#pairing): the phone in its title, Cancel its one answer
    and Return's; the daemon is told."""
    q2 = Q2(fw, ui, recs)
    card_route(fw, ui)
    q2.files[PAIR] = b'123456\nPixel 8\n'
    q2.tick()
    card = ui.top()
    assert ui.name[card] == 'sortselect_dialog' and 'carpair_page' not in ui.went
    assert C.string_at(C.addressof(C.c_char.in_dll(fw.lib, 'pair_prompt__pair_name'))) == b'Pixel 8'
    event = C.create_string_buffer(0x40)
    C.c_int.from_buffer(event, O['EVENT_TYPE']).value = O['EVT_KEY_UP']
    C.c_int.from_buffer(event, O['EVENT_KEY']).value = KEY_RETURN
    ui.handler(card, O['EVT_KEY_UP'])(C.addressof(event))
    ui.fire('card_answer')
    assert q2.files[PAIR_CANCEL] == b'1\n' and card not in ui.windows


def test_pairing_shows_a_new_code_that_came_between_two_polls(fw, ui, mcl, recs):
    """The daemon ended one code and showed another inside one poll: the card must not keep the old one."""
    q2 = Q2(fw, ui, recs)
    card_route(fw, ui)
    q2.files[PAIR] = b'242610\nPixel 10 Pro\n'
    q2.tick()
    old = ui.top()
    q2.files[PAIR] = b'852219\nPixel 10 Pro\n'
    q2.tick()
    q2.tick()
    new = ui.top()
    assert old not in ui.windows and new != old and ui.name[new] == 'sortselect_dialog'
    assert ui.went.count('dialog/sortselect_dialog') == 2  # a second card, for the new code
    assert PAIR_CANCEL not in q2.files  # the daemon's own change: nothing cancelled


def test_pairing_closes_when_the_daemon_ends_it_and_names_the_phone(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    card_route(fw, ui)
    q2.files[PAIR] = b'654321\nPixel 8\n'
    q2.tick()
    card = ui.top()
    del q2.files[PAIR]
    q2.files[PAIR_END] = b'1\n'
    q2.tick()
    assert card not in ui.windows and PAIR_END not in q2.files
    ui.fire('cr_toast')
    assert ui.toasts == ['Pixel 8 paired']


def peer(name, id_=bytes(8)):
    return id_ + bytes(32) + name.encode().ljust(32, b'\0') + bytes(4)


ONLINE = '/tmp/coda-rc.online'


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


def test_scan_is_car_audio_s_held_button(fw, ui, mcl, recs):
    """scan 1, -1 and 0 (q2_iap2.c's held Next or Previous, and its release) go to seek.c's seek_car and are acked;
    another number is refused."""
    q2 = Q2(fw, ui, recs)
    q2.tick()
    for seq, verb, ack in ((21, 'scan 1', 0), (22, 'scan 0', 0), (23, 'scan -1', 0), (24, 'scan 2', 1)):
        q2.files[CMD] = f'{seq} {verb}\n'.encode()
        q2.tick()
        assert q2.files.pop(ACK) == f'{seq} {ack}\n'.encode(), verb


def test_next_and_previous_go_to_the_player(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.files[CMD] = b'14 next\n'
    q2.tick()
    assert len(fw.calls('player_next_music')) == 1 and q2.files.pop(ACK) == b'14 0\n'
    q2.files[CMD] = b'15 previous\n'
    q2.tick()
    assert len(fw.calls('player_prev_music')) == 1 and q2.files.pop(ACK) == b'15 0\n'


class Tell:
    """The daemon's pipe (docs/internals.md#car-remote-tell): pipe2 answers fds 8 and 9, pthread_create keeps the
    thread to run by hand, and each run of it reads `bytes` once, then the daemon's end closing."""
    R, W = 8, 9

    def __init__(self, fw):
        self.fw, self.threads, self.bytes = fw, [], 0
        def syscall(nr, a, *_):
            assert nr == 4328  # pipe2
            C.c_int.from_address(a).value, C.c_int.from_address(a + 4).value = self.R, self.W
            return 0
        fw.mock('cr_syscall', syscall)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(arg) or
                C.c_ulong.from_address(t).__setattr__('value', 7) or 0)
        fw.mock('fw_read', lambda fd, *_: self.take() if fd == self.R else -1)
        self.worker = fw.fn('coda_rc_shim__cr_tell', C.c_void_p, C.c_void_p)

    def take(self):
        n, self.bytes = self.bytes, 0
        return n

    def tell(self):
        """The daemon wrote a file the shim reads, then went: the thread counts it and ends."""
        self.bytes = 1
        self.worker(self.threads[-1])


def test_the_daemons_tell_reads_its_files_on_the_next_pass_else_every_5_s(fw, ui, mcl, recs):
    tell = Tell(fw)
    q2 = Q2(fw, ui, recs)
    q2.tick()
    assert q2.system == ['( /usr/bin/coda-rc --tell-fd 9 >/dev/null 2>&1 & )']
    assert [a[:3] for _, a in fw.calls('fw_fcntl')] == [(9, 2, 0)]  # inheritable for that start only
    assert [a[0] for _, a in fw.calls('fw_close')] == [9] and tell.threads == [8]
    q2.files[CMD] = b'14 next\n'
    q2.tick()
    assert ACK not in q2.files  # no tell: Now Playing's pass leaves the files
    q2.tick(4500)
    assert q2.files.pop(ACK) == b'14 0\n'  # CR_FILES_MS after the last look
    q2.files[CMD] = b'15 previous\n'
    tell.tell()
    q2.tick(50)
    assert q2.files.pop(ACK) == b'15 0\n' and len(fw.calls('player_prev_music')) == 1  # the pass after the tell
    q2.files[CMD] = b'16 next\n'
    q2.tick(600)
    assert q2.files.pop(ACK) == b'16 0\n'  # the daemon's end closed: every CR_POLL_MS again


def test_without_the_pipe_the_files_are_read_every_poll(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs)  # pipe2 refused (the stub's 0 leaves no fds)
    q2.tick()
    assert q2.system == ['( /usr/bin/coda-rc >/dev/null 2>&1 & )']
    q2.files[CMD] = b'14 next\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'14 0\n'


def test_mode_sets_shuffle_and_repeat_as_the_hold_menu(fw, ui, mcl, recs):
    """SetMode's "mode <shuffle> <repeat>" while the hold menu's extended play runs: its playback_set, the nearest Q2
    mode, -1 left alone (outside it, Now Playing's own pair: test_modes_follow_now_playings_buttons_both_ways)."""
    q2 = Q2(fw, ui, recs)
    q2.queue()
    q2.tick()
    option = fw.fn('playback_option', C.c_int, C.c_int)
    fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(1, 5)  # Repeat All from the hold menu: extended play runs
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
    q2.files[CMD] = b'31 rate 5 12\n'  # five stars, kept as the others: no like (My Fav is the heart's alone)
    q2.tick()
    assert q2.files.pop(ACK) == b'31 0\n' and q2.now()['hearts'] == '5'
    assert not fw.calls('batch_add_file') and not fw.calls('deleteMusicFromFav')
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


def test_modes_follow_now_playings_buttons_both_ways(fw, ui, mcl, recs):
    """Shuffle and repeat set on the Q2 Muse's Now Playing (np_bar.c: stock's MCL_MODE and [IPOD] NPSHUFONE/NPREPOFF)
    never reached the car, and the car's SetMode never moved Now Playing's buttons (2026-11-16 14:38): the shim read
    and set only playback.c's extended modes, which play only while the hold menu's shuffle is on. Outside it,
    both go through Now Playing's own pair."""
    modes = []
    fw.mock('config_playmode', lambda m, notify, *_: modes.append(m) or setattr(mcl, 'mode', m) or 0)
    mcl.mode = 0
    q2 = Q2(fw, ui, recs)
    q2.queue()
    q2.tick()
    assert (q2.now()['shuffle'], q2.now()['repeat']) == ('0', '0')
    set_np = fw.fn('np_mode_set', None, C.c_int, C.c_int)
    set_np(1, 1)  # Now Playing: shuffle on, repeat All (NP_REPEAT_ALL)
    q2.tick()
    assert (q2.now()['shuffle'], q2.now()['repeat']) == ('1', '2')
    set_np(0, 2)  # repeat One (NP_REPEAT_ONE), shuffle off
    q2.tick()
    assert (q2.now()['shuffle'], q2.now()['repeat']) == ('0', '1') and mcl.mode == 1
    q2.files[CMD] = b'30 mode 1 0\n'  # the car: shuffle on, repeat Off
    q2.tick()
    assert q2.files.pop(ACK) == b'30 0\n' and mcl.mode == 2  # stock's Shuffle: Now Playing's shuffle lit
    assert (q2.now()['shuffle'], q2.now()['repeat']) == ('1', '0')
    q2.files[CMD] = b'31 mode -1 2\n'  # repeat All, shuffle kept
    q2.tick()
    assert q2.files.pop(ACK) == b'31 0\n' and (q2.now()['shuffle'], q2.now()['repeat']) == ('1', '2')
    assert fw.fn('playback_option', C.c_int, C.c_int)(0) == 0  # the hold menu's shuffle not started


def test_a_card_apps_streaming_song_can_be_rated(fw, ui, mcl, recs):
    """A card app's song streamed from its download (no library record: id 0) plays as a card list (0xf001), so it
    has stars and rate_song rates it (ratings.c, through its catalogue row); coda-rc names it by its
    server id and sends the record's id, 0, which is what the shim compares."""
    q2 = Q2(fw, ui, recs, songs=0)
    q2.songs = [recs(id_=0, path='/mnt/mmc/.subsonic/stream/9f2c/original/0/hello [VcF5].flac', name='hello [VcF5].flac',
                     title='Hello', artist='Adele', album='Hello Album')]
    q2.queue(at=0)
    C.c_int.from_address(O['MCL_TYPE']).value = 0xf001
    q2.tick()
    assert q2.now()['id'] == '0' and q2.now()['hearts'] == '0'
    q2.files[CMD] = b'40 rate 3 0\n'
    q2.tick()
    assert q2.files.pop(ACK) == b'40 0\n' and q2.now()['hearts'] == '3'


def test_a_streaming_song_the_player_waits_for_is_buffering(fw, ui, mcl, recs):
    """docs/server-library.md#download-ahead-and-the-streaming-state: the song playing is a card app's download that
    q2player waits for (/tmp/q2player.wait names it): Now Playing says so with its .stream line's bytes and length,
    for coda-rc's NowPlaying.buffering; once it plays on, it doesn't."""
    q2 = Q2(fw, ui, recs, songs=0)
    song = '/mnt/mmc/.subsonic/stream/9f2c/original/0/hello [VcF5].flac'
    q2.songs = [recs(id_=0, path=song, name='hello [VcF5].flac', title='Hello', artist='Adele', album='Hello Album')]
    q2.queue(at=0)
    def stat(v, p, st, *_):  # its mtime now (time() is 0 here): a fresh line
        C.memset(st, 0, 36 * 4)
        return 0 if C.string_at(p).decode() in q2.files else -1
    fw.mock('fw___xstat', stat)
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))
    q2.files['/tmp/q2-subsonic.stream'] = f'#Q2STREAM/1\tseq=1\nget\t7000000\t20000000\t780000\t{song}\n'.encode()
    q2.files['/tmp/q2player.wait'] = f'{song}\n'.encode()
    q2.tick()
    now = q2.now()
    assert (now['buf'], now['bhave'], now['btotal'], now['bms']) == ('1', '7000000', '20000000', '780000')
    assert now['len'] == '780'  # still arriving: the server's length, not the player's (the bytes so far)
    q2.files['/tmp/q2-subsonic.stream'] = q2.files['/tmp/q2-subsonic.stream'].replace(b'780000', b'0')
    q2.tick()
    assert q2.now()['len'] == '0'  # no server length: unknown, not the player's
    q2.files['/tmp/q2-subsonic.stream'] = q2.files['/tmp/q2-subsonic.stream'].replace(b'\t0\t/', b'\t780000\t/')
    q2.tick()
    del q2.files['/tmp/q2player.wait']
    q2.tick()
    assert (q2.now()['buf'], q2.now()['bhave']) == ('0', '0')


def test_a_queued_stream_download_carries_its_path_title_and_artist(fw, ui, mcl, recs):
    """The car, 2026-11-18 00:24: Shuffle Songs over server songs, and the car's queue listed every one as "[Not in
    the library]". The app's playlist session queues its stream downloads, records with no library id (0), named by
    their server rows' tags (tags.c card_record_tags); the queue file had only the 0. Such a line carries the
    record's path, title and artist after tabs, a tab or line break in them made a space; a library song keeps
    its id alone."""
    q2 = Q2(fw, ui, recs, songs=1)
    song = '/mnt/mmc/.subsonic/stream/9f2c/original/0/make it with you [VcF5].flac'
    q2.songs += [recs(id_=0, path=song, name='make it with you [VcF5].flac', title='Make It\twith You', artist='Bread',
                      album='Manna'),
                 recs(id_=0, path='/mnt/mmc/.subsonic/stream/9f2c/original/0/x [Q1].flac', name='x [Q1].flac',
                      title='', artist='', album='')]
    q2.queue(at=1)
    q2.tick()
    assert q2.files[QUEUE].decode().split('\n') == [
        '1 1 3', '10', f'0\t{song}\tMake It with You\tBread',
        '0\t/mnt/mmc/.subsonic/stream/9f2c/original/0/x [Q1].flac\tx [Q1].flac\t', '']


def test_a_toast_goes_to_the_car_for_four_seconds(fw, ui, mcl, recs):
    """The Q2 Muse toasted "Radio isn't available for this song" and the car, which draws no toast, showed nothing
    (2026-11-18 00:28). Every toast (library_pages.c toast) is Now Playing's note for CR_NOTE_MS, counted, so the
    daemon sends it as NowPlaying.notice; none is kept while Car Remote is off."""
    q2 = Q2(fw, ui, recs)
    q2.queue()
    fw.mock('navigator_to_with_context', lambda *_: 0)
    toast = fw.fn('toast', None, C.c_char_p)
    q2.tick()
    assert (q2.now()['note'], q2.now()['noten']) == ('', '0')
    toast(b"Radio isn't available for this song")
    q2.tick()
    assert (q2.now()['note'], q2.now()['noten']) == ("Radio isn't available for this song", '1')
    q2.tick(3000)
    assert q2.now()['note'] == "Radio isn't available for this song"
    q2.tick(1000)
    assert (q2.now()['note'], q2.now()['noten']) == ('', '1')
    toast(b"Radio isn't available for this song")
    q2.tick()
    assert (q2.now()['note'], q2.now()['noten']) == ("Radio isn't available for this song", '2')


def test_no_toast_is_kept_while_car_remote_is_off(fw, ui, mcl, recs):
    q2 = Q2(fw, ui, recs, on=0)
    fw.mock('navigator_to_with_context', lambda *_: 0)
    fw.fn('toast', None, C.c_char_p)(b'Radio Off')
    q2.config.values[b'CARREMOTE'] = b'1'
    fw.fn('coda_rc_toggle', None)()  # the switch on: Now Playing written from now
    q2.tick()
    assert (q2.now()['note'], q2.now()['noten']) == ('', '0')


def test_the_daemons_wake_names_the_app_to_start(fw, ui, mcl, recs):
    """The daemon asked a card app that doesn't run for a car's cover (coda_rc.c crc_cover_ask): /tmp/coda-rc.wake
    names it, and the shim hands that to addon_cover_wake (test_addon.py's test_an_app_is_started_for_the_cars_cover)
    and removes the file, so it is taken once."""
    from test_addon import ap
    q2 = Q2(fw, ui, recs)
    q2.tick()
    q2.files['/tmp/coda-rc.wake'] = b'foo\n'
    q2.tick()
    assert '/tmp/coda-rc.wake' not in q2.files
    a = ap(fw)
    assert [bytes(a.a[i].id) for i in range(a.n)] == [b'foo']


def test_car_audio_s_commands_are_read_every_pass_beside_the_daemon_s_pipe(fw, ui, mcl, recs):
    """The drive of 2026-11-22 (q2muse-1126): with Car Remote on, its daemon's pipe live, the shim read the command
    file only after a tell or every CR_FILES_MS (5 s), and q2-iap, which never tells, writes Car Audio's buttons
    there: the car's held Next ("scan 1" every 500 ms) reached seek.c every 5 s, its scan ended 2 s after each
    (SEEK_CAR_LOST_MS) and began again at the next, in bursts. While the iPhone gadget is on the cable the file is
    read every CR_POLL_MS."""
    tell = Tell(fw)
    q2 = Q2(fw, ui, recs)
    q2.tick()
    assert tell.threads == [8]
    fw.fn('usb_car_start', None)()  # a cable in on Automatic: q2car's iPhone (usb_car.c)
    q2.files[CMD] = b'21 scan 1\n'
    q2.tick(600)
    assert q2.files.pop(ACK) == b'21 0\n'
    q2.files[CMD] = b'22 scan 1\n'
    q2.tick(600)
    assert q2.files.pop(ACK) == b'22 0\n'


def test_resume_and_pause_say_what_the_player_was_and_press_only_to_change_it(fw, ui, mcl, recs):
    """The drive of 2026-11-22: the phone's resume, and the player paused 0.4 s later. Resume on a song playing
    presses nothing (it is no toggle), and each says the player's status and what it did."""
    from test_headset import said_on_stderr
    q2 = Q2(fw, ui, recs)
    said = said_on_stderr(fw, 'remote')
    q2.queue()
    q2.tick()
    q2.files[CMD] = b'31 resume\n'
    q2.tick()
    assert fw.calls('player_play_pause') == [] and q2.files.pop(ACK) == b'31 0\n'
    q2.status = 3
    q2.files[CMD] = b'32 resume\n'
    q2.tick()
    assert len(fw.calls('player_play_pause')) == 1
    assert said[-2:] == ['resume from the car or phone: the player 2, nothing pressed',
                         'resume from the car or phone: the player 3, Play/Pause pressed'], said
