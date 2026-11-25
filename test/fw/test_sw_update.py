"""patch/sw_update.c: SD Card Update's check of the card's update.tar (the tar read as stock's update_firmware will take
it), stock's answers as its pages, the words its pages say, and the flow from the row to the install."""
import ctypes as C
import hashlib
import io
import tarfile

import pytest

import host
from conftest import O, Files

(SW_CHECKING, SW_OK, SW_LATEST, SW_NONE, SW_NOCARD, SW_LOW, SW_BAD, SW_FULL, SW_CANCELLED, SW_ASKING, SW_OFFER_NET,
 SW_OFFER_SD, SW_DOWNLOADING) = range(13)
ON_NONE, ON_OFF, ON_CHECKING = -2, -1, 0
NET_NEWER, NET_LATEST, NET_FAILED = 1, 2, 3
NOTE_NONE, NOTE_SD, NOTE_WIFI, NOTE_RETRY = range(4)
TAR = '/mnt/mmc/update.tar'
INSTALLED = b'V0JYi'


class Sw(C.Structure):
    _fields_ = [('thread', C.c_ulong), *((n, C.c_int) for n in ('state', 'cancel', 'result', 'level', 'frame')),
                ('timer', C.c_uint), ('size_kb', C.c_uint), ('parts', C.c_longlong),
                *((n, C.c_void_p) for n in ('page', 'restart')), ('tag', C.c_char * 24),
                *((n, C.c_int) for n in ('online', 'net_up', 'sd_up')), ('net_thread', C.c_ulong),
                ('net_kb', C.c_uint), ('net_tag', C.c_char * 32),
                *((n, C.c_int) for n in ('note', 'focus', 'percent', 'verifying')),
                ('button', C.c_int * 4), ('link', C.c_int * 4)]


def carded(fw):
    """Whether the confirmation card asked (feedback.c confirm opens stock's sortselect_dialog for it)."""
    return any(C.string_at(a[0]) == b'dialog/sortselect_dialog' for _, a in fw.calls('navigator_to'))


def update_tar(version='V0JZi', model='Shanling Q2', image=b'k' * 700, rootfs=b'r' * 1500, md5s=None, members=None,
               keep=0):
    """An update.tar as tools/build.py writes one (GNU format, firmware_v20.info first, then the recovery's folder and
    its two images, the info's MD5 lines naming them); md5s replaces the two MD5 lines, members the files, keep cuts it
    to that many bytes."""
    sums = md5s or [hashlib.md5(image).hexdigest(), hashlib.md5(rootfs).hexdigest()]
    info = f'{model}\n{version}\n{sums[0]}  recovery-update/xImage\n{sums[1]}  recovery-update/rootfs.squashfs\n'
    files = members if members is not None else [('firmware_v20.info', info.encode()), ('recovery-update', None),
                                                 ('recovery-update/xImage', image),
                                                 ('recovery-update/rootfs.squashfs', rootfs)]
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.GNU_FORMAT) as t:
        for name, data in files:
            m = tarfile.TarInfo(name)
            if data is None:
                m.type = tarfile.DIRTYPE
            else:
                m.size = len(data)
            t.addfile(m, io.BytesIO(data) if data is not None else None)
    blob = out.getvalue()
    return blob[:keep] if keep else blob


def md5(fw):
    """demo's libcrypto MD5, as hashlib's."""
    sums = {}
    fw.mock('fw_MD5_Init', lambda ctx, *_: sums.__setitem__(ctx, hashlib.md5()))
    fw.mock('fw_MD5_Update', lambda ctx, data, n, *_: sums[ctx].update(C.string_at(data, n)))
    fw.mock('fw_MD5_Final', lambda out, ctx, *_: C.memmove(out, sums.pop(ctx).digest(), 16))


def check(fw, blob):
    """sw_check_file of blob as the card's update.tar (None: no file), and the state it leaves."""
    Files(fw, {} if blob is None else {TAR: blob})
    md5(fw)
    r = fw.fn('sw_check_file', C.c_int, C.c_char_p, C.c_char_p)(TAR.encode(), INSTALLED)
    return r, fw.var('sw_update__swu', Sw)


def test_an_update_stock_will_take(fw):
    blob = update_tar()
    r, sw = check(fw, blob)
    assert r == SW_OK and sw.tag == b'V0JZi' and sw.parts == 700 + 1500 and sw.size_kb == len(blob) >> 10


def test_no_file(fw):
    assert check(fw, None)[0] == SW_NONE


def test_this_builds_own_version_is_up_to_date_before_its_md5s_are_read(fw):
    r, sw = check(fw, update_tar(version='V0JYi', md5s=['0' * 32] * 2))
    assert r == SW_LATEST and sw.tag == b'V0JYi' and not fw.calls('fw_MD5_Init')


def test_the_first_two_lines_are_trimmed_as_stocks(fw):
    assert check(fw, update_tar(version=' V0JYi \r', model='Shanling Q2 \r'))[0] == SW_LATEST


@pytest.mark.parametrize('kw', [dict(model='Shanling Q3'), dict(version='1.36'),
                                dict(md5s=['0' * 32, hashlib.md5(b'r' * 1500).hexdigest()]),
                                dict(md5s=[hashlib.md5(b'k' * 700).hexdigest(), '0' * 32]), dict(keep=4000)])  # keep: into rootfs.squashfs, from byte 3584
def test_another_devices_or_a_damaged_file_cant_be_used(fw, kw):
    assert check(fw, update_tar(**kw))[0] == SW_BAD


def test_an_image_missing_cant_be_used(fw):
    blob = update_tar()
    info = host.tools_build.need(tarfile.open(fileobj=io.BytesIO(blob)).extractfile('firmware_v20.info'), 'no info').read()
    assert check(fw, update_tar(members=[('firmware_v20.info', info), ('recovery-update/xImage', b'k' * 700)]))[0] == SW_BAD


def test_the_info_must_come_first(fw):
    blob = update_tar()
    info = host.tools_build.need(tarfile.open(fileobj=io.BytesIO(blob)).extractfile('firmware_v20.info'), 'no info').read()
    assert check(fw, update_tar(members=[('recovery-update/xImage', b'k' * 700), ('firmware_v20.info', info)]))[0] == SW_BAD


def test_a_short_info_cant_be_used(fw):
    assert check(fw, update_tar(members=[('firmware_v20.info', b'Shanling Q2\nV0JZi\n')]))[0] == SW_BAD


def test_leaving_the_page_ends_the_check(fw):
    fw.var('sw_update__swu', Sw).cancel = 1
    assert check(fw, update_tar())[0] == SW_CANCELLED


@pytest.mark.parametrize('code, page', [(1, SW_CHECKING), (-15, SW_LOW), (-2, SW_NOCARD), (-11, SW_NONE),
                                        (-12, SW_FULL), (-3, SW_BAD)])
def test_stocks_checks_as_pages(fw, code, page):
    assert fw.fn('sw_stock_answer', C.c_int, C.c_int)(code) == page


@pytest.mark.parametrize('tag, name', [(b'V1820', 'Vamp 1.8.2 · V1820'), (b'V1822', 'Vamp 1.8.2 beta 2 · V1822'),
                                       (b'V101I', 'Vamp 1.0.1 · V101I'), (b'V0JZi', 'Vamp Dev · V0JZi'),
                                       (b'V1.36', 'V1.36'), (b'V18200', 'V18200')])
def test_the_updates_name(fw, tag, name):
    out = C.create_string_buffer(64)
    fw.fn('sw_update_name', None, C.c_char_p, C.c_uint, C.c_char_p)(out, 64, tag)
    assert out.value.decode() == name


def test_this_build_as_up_to_date_says_it(fw):
    out = C.create_string_buffer(64)
    fw.fn('sw_installed', None, C.c_char_p, C.c_uint)(out, 64)
    # host.py builds as a release: VERSION and TAG, no Dev
    assert out.value.decode() == f'{host.tools_build.VERSION} ({host.tools_build.TAG}) is installed'


def opened(fw):
    """page_open's pages, each a zeroed window alive for the test, and the widgets page_list makes in them (a
    scroll view's parent its list view); the pages' names from widget_set_name."""
    made, kids = [], []
    def widget(parent, *_):
        kids.append(C.create_string_buffer(0x400))
        C.c_void_p.from_address(C.addressof(kids[-1]) + O['W_PARENT']).value = parent
        return C.addressof(kids[-1])
    for name in ('list_view_create', 'scroll_view_create', 'hscroll_label_create', 'list_item_create'):
        fw.mock(name, widget)
    fw.mock('window_create', lambda *_: made.append(C.create_string_buffer(0x400)) or C.addressof(made[-1]))
    return lambda: [C.string_at(c[1][1]).decode() for c in fw.calls('widget_set_name')
                    if c[1][0] in {C.addressof(m) for m in made}]


@pytest.mark.parametrize('code, page', [(-15, SW_LOW), (-11, SW_NONE), (-12, SW_FULL)])
def test_a_failed_stock_check_is_its_page_with_no_thread(fw, code, page):
    pages = opened(fw)
    fw.mock('check_updatefw_exist', lambda *_: code)
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    sw = fw.var('sw_update__swu', Sw)
    assert pages() == ['swupdate_page'] and sw.result == page
    assert not fw.calls('fw_pthread_create') and not sw.timer


def test_a_found_file_is_checked_on_a_thread_and_one_at_a_time(fw):
    pages = opened(fw)
    fw.mock('check_updatefw_exist', lambda *_: 1)
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.mock('timer_add', lambda *_: 5)
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    sw = fw.var('sw_update__swu', Sw)
    assert pages() == ['swupdate_page'] and sw.result == SW_CHECKING and sw.state == SW_CHECKING and sw.timer == 5
    assert len(fw.calls('fw_pthread_create')) == 1 and len(fw.calls('check_updatefw_exist')) == 1


@pytest.mark.parametrize('state, confirm', [(SW_OK, True), (SW_BAD, False), (SW_LATEST, False)])
def test_the_checks_answer(fw, state, confirm):
    """An update stock will take asks Install Update? on the confirmation card over the check's page; any other answer
    is its result's page."""
    pages = opened(fw)
    fw.mock('check_updatefw_exist', lambda *_: 1)
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.mock('timer_add', lambda *_: 5)
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    sw = fw.var('sw_update__swu', Sw)
    tick = fw.fn('sw_update__sw_tick', C.c_int, C.c_void_p)
    assert tick(None) == 8 and sw.frame == 1  # still checking: the spinner's next frame
    sw.state = state
    assert tick(None) == 7 and len(fw.calls('fw_pthread_join')) == 1 and not sw.timer
    assert pages() == ['swupdate_page'] and carded(fw) == confirm
    assert (sw.state == SW_ASKING) == confirm and (confirm or sw.result == state)


@pytest.mark.parametrize('row, installs', [(0, False), (1, True)])
def test_the_cards_answer_closes_the_page_and_install_runs_stocks_dialog_next(fw, row, installs):
    opened(fw)
    fw.mock('check_updatefw_exist', lambda *_: 1)
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    sw = fw.var('sw_update__swu', Sw)
    assert sw.page
    fw.fn('sw_update__sw_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, 0)
    assert not sw.page and bool(fw.calls('idle_add')) == installs


def test_the_answer_waits_for_the_check_page_to_finish_sliding_in(fw):
    pages = opened(fw)
    fw.mock('check_updatefw_exist', lambda *_: 1)
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.mock('timer_add', lambda *_: 5)
    sliding = [1]
    fw.mock('window_manager_is_animating', lambda *_: sliding[0])
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    sw = fw.var('sw_update__swu', Sw)
    sw.state = SW_OK
    tick = fw.fn('sw_update__sw_tick', C.c_int, C.c_void_p)
    assert tick(None) == 8 and pages() == ['swupdate_page'] and not fw.calls('fw_pthread_join') and not carded(fw)
    sliding[0] = 0
    assert tick(None) == 7 and carded(fw)


def test_leaving_while_it_checks_cancels(fw):
    opened(fw)
    fw.mock('check_updatefw_exist', lambda *_: 1)
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.fn('ringnav_fwupdate_tf', C.c_int)()
    sw = fw.var('sw_update__swu', Sw)
    fw.fn('sw_update__sw_gone', C.c_int, C.c_void_p, C.c_void_p)(sw.page, None)
    assert sw.cancel == 1 and not sw.page


@pytest.mark.parametrize('quit, page', [(9, SW_NONE), (13, SW_LATEST), (18, SW_BAD), (0, None)])
def test_stocks_dialog_answers_as_a_page(fw, quit, page):
    pages = opened(fw)
    fw.mock('navigator_to', lambda *_: quit)
    assert fw.fn('sw_update__sw_install', C.c_int, C.c_void_p)(None) == 7
    assert C.string_at(fw.calls('navigator_to')[0][1][0]) == b'dialog/checkfw_dialog'
    assert len(pages()) == (page is not None) and (page is None or fw.var('sw_update__swu', Sw).result == page)


def test_the_restart_dialog_hides_stocks_label(fw):
    label, win = C.create_string_buffer(0x400), C.create_string_buffer(0x400)
    fw.mock('widget_lookup', lambda w, name, *_: C.addressof(label) if C.string_at(name) == b'scrlabel_checkfw' else 0)
    fw.fn('sw_update_restarting', None, C.c_void_p)(C.addressof(win))
    assert fw.var('sw_update__swu', Sw).restart == C.addressof(win)
    assert fw.calls('widget_set_visible')[0][1][:2] == (C.addressof(label), 0)


@pytest.mark.parametrize('have, part', [(0, 0), (1100, 90), (2200, 180), (9000, 180)])
def test_the_restarts_bar_is_what_stocks_tar_unpacked(fw, have, part):
    Files(fw, {'/mnt/mmc/recovery-update/xImage': b'k' * min(have, 700),
               '/mnt/mmc/recovery-update/rootfs.squashfs': b'r' * max(0, have - 700)} if have else {})
    fw.var('sw_update__swu', Sw).parts = 2200
    assert fw.fn('sw_update__sw_unpacked', C.c_int, C.c_int)(180) == part


# Software Update (29 v3): General's one row checks online and the card at once, each apart from the other.

def offer(fw, sd, net):
    note = C.c_int(-1)
    return fw.fn('sw_offer', C.c_int, C.c_int, C.c_int, C.POINTER(C.c_int))(sd, net, C.byref(note)), note.value


@pytest.mark.parametrize('sd, net, page, note', [
    (SW_OK, NET_FAILED, SW_OFFER_SD, NOTE_RETRY),  # online timed out or failed: the card's update, and Try Again
    (SW_NONE, NET_FAILED, SW_LATEST, NOTE_RETRY),  # nothing on the card: up to date, and the note
    (SW_NOCARD, NET_FAILED, SW_LATEST, NOTE_RETRY),
    (SW_OK, NET_NEWER, SW_OFFER_NET, NOTE_SD),  # both: online first, the card a line away
    (SW_NONE, NET_NEWER, SW_OFFER_NET, NOTE_NONE),
    (SW_BAD, NET_NEWER, SW_OFFER_NET, NOTE_NONE),
    (SW_OK, ON_OFF, SW_OFFER_SD, NOTE_WIFI),  # Wi-Fi off: the card's, and Turn on Wi-Fi
    (SW_NONE, ON_OFF, SW_LATEST, NOTE_WIFI),
    (SW_OK, NET_LATEST, SW_OFFER_SD, NOTE_NONE),
    (SW_LATEST, NET_LATEST, SW_LATEST, NOTE_NONE),
    (SW_BAD, NET_LATEST, SW_BAD, NOTE_NONE), (SW_FULL, NET_FAILED, SW_FULL, NOTE_RETRY),  # a file that can't be used
    (SW_LOW, NET_NEWER, SW_LOW, NOTE_NONE),  # stock's updater refuses either while the battery is low
])
def test_the_page_weighs_the_card_and_the_internet_apart(fw, sd, net, page, note):
    assert offer(fw, sd, net) == (page, note)


def both(fw, wifi=1):
    """sw_update_open with the card's file found, threads and timers held: (the state, its tick)."""
    opened(fw)
    fw.var('g_wififlag', C.c_ubyte).value = wifi
    fw.mock('check_updatefw_exist', lambda *_: 1)
    fw.mock('fw_pthread_create', lambda *_: 0)
    fw.mock('timer_add', lambda *_: 5)
    fw.fn('sw_update_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    return fw.var('sw_update__swu', Sw), fw.fn('sw_update__sw_tick', C.c_int, C.c_void_p)


def test_opening_checks_online_and_the_card_at_once(fw):
    sw, tick = both(fw)
    assert len(fw.calls('fw_pthread_create')) == 2 and sw.result == SW_CHECKING and sw.timer == 5
    assert sw.online == ON_CHECKING and sw.state == SW_CHECKING
    sw.state = SW_OK  # the card answers first: the page waits for the internet
    assert tick(None) == 8 and sw.result == SW_CHECKING
    sw.online, sw.net_tag, sw.net_kb = NET_NEWER, b'1.8.3', 56000
    assert tick(None) == 7 and len(fw.calls('fw_pthread_join')) == 2
    assert (sw.result, sw.note, sw.focus) == (SW_OFFER_NET, NOTE_SD, 0) and not carded(fw)


def test_a_failed_online_check_never_holds_the_card_up(fw):
    sw, tick = both(fw)
    sw.online = NET_FAILED  # no DNS, a timeout: answered before the card's
    assert tick(None) == 8 and sw.result == SW_CHECKING
    sw.state = SW_OK
    assert tick(None) == 7 and (sw.result, sw.note) == (SW_OFFER_SD, NOTE_RETRY)


def test_with_wifi_off_only_the_card_is_checked(fw):
    sw, tick = both(fw, wifi=0)
    assert len(fw.calls('fw_pthread_create')) == 1 and sw.online == ON_OFF
    sw.state = SW_NONE
    assert tick(None) == 7 and (sw.result, sw.note) == (SW_LATEST, NOTE_WIFI)


def test_the_switch_row_offers_the_card_and_install_asks_on_the_card(fw):
    sw, tick = both(fw)
    sw.state, sw.online = SW_OK, NET_NEWER
    tick(None)
    key = fw.fn('sw_update_key', C.c_int, C.c_void_p, C.c_uint)
    assert key(sw.page, O['KEY_NEXT']) == 1 and sw.focus == 1  # the wheel: the line under the button
    assert key(sw.page, O['KEY_NEXT']) == 1 and sw.focus == 1  # and no further
    assert key(sw.page, O['KEY_CENTER']) == 1 and (sw.result, sw.note, sw.focus) == (SW_OFFER_SD, NOTE_NONE, 0)
    assert key(sw.page, O['KEY_CENTER']) == 1 and carded(fw) and sw.state == SW_ASKING


def test_try_again_checks_online_again_and_keeps_the_cards_answer(fw):
    sw, tick = both(fw)
    sw.state, sw.online = SW_NONE, NET_FAILED
    tick(None)
    assert (sw.result, sw.note) == (SW_LATEST, NOTE_RETRY)
    key = fw.fn('sw_update_key', C.c_int, C.c_void_p, C.c_uint)
    key(sw.page, O['KEY_NEXT'])
    assert key(sw.page, O['KEY_CENTER']) == 1 and sw.result == SW_CHECKING and sw.online == ON_CHECKING
    assert len(fw.calls('fw_pthread_create')) == 3 and sw.state == SW_NONE
    sw.online = NET_LATEST
    assert tick(None) == 7 and (sw.result, sw.note) == (SW_LATEST, NOTE_NONE)
