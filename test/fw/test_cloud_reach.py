"""patch/cloud_reach.c (docs/internals.md#cloud-reach): the status row's offline glyph as one OR over every service the
Q2 asks of the internet while Wi-Fi is connected, a card app's server and each of demo's own transfers, by what each
transfer's end says of its server; and each of those transfers noting it."""
import ctypes as C

import pytest

from test_status_row import BODY, GAP, WIFI_W, Row, state
from test_nav_painters import scratch, st  # noqa: F401 (fixtures)

REACH_SCROBBLE, REACH_ONLINE, REACH_UPDATES, REACH_ZONE, REACH_RADIO, REACH_TIDAL, REACH_N = range(7)
OFFLINE_W = 14  # SR_OFFLINE
CURLE_COULDNT_RESOLVE_HOST, CURLE_COULDNT_CONNECT, CURLE_OPERATION_TIMEDOUT = 6, 7, 28


@pytest.fixture
def row(fw, st):
    r = Row(fw, st)
    r.status = 0  # no play state: Wi-Fi's fan and the offline cloud alone
    return r


def note(fw, service, rc, http=0):
    fw.fn('reach_note', None, C.c_int, C.c_int, C.c_long)(service, rc, http)


def down(fw, service):
    return fw.fn('reach_is_down', C.c_int, C.c_int)(service)


def offline(fw):
    return fw.fn('cloud_offline', C.c_int)()


@pytest.mark.parametrize('service', range(REACH_N))
def test_any_service_out_of_reach_shows_the_offline_glyph_until_it_answers(fw, row, service):
    """One OR: each service alone lights it, as a card app's reach=down does, and its next answer puts it out."""
    row.wifi(3)
    assert row.left() == BODY - GAP - WIFI_W
    note(fw, service, CURLE_COULDNT_CONNECT)
    assert row.left() == BODY - GAP - WIFI_W - GAP - OFFLINE_W
    assert row.paint()[1] == ['oled_status_wifi_3_d', 'oled_status_offline_d']
    assert state(fw)['offline'] == '1'  # q2ui's row says it too
    note(fw, service, 0, 200)
    assert row.left() == BODY - GAP - WIFI_W and state(fw)['offline'] == '0'


@pytest.mark.parametrize('rc, http, out', [
    (CURLE_COULDNT_RESOLVE_HOST, 0, 1), (CURLE_COULDNT_CONNECT, 0, 1), (CURLE_OPERATION_TIMEDOUT, 0, 1),
    (35, 0, 1), (60, 0, 1), (56, 0, 1),  # TLS that never finished, a certificate not the server's (a portal), a cut
    (0, 502, 1), (0, 504, 1),            # a gateway that could not reach the server
    (0, 503, 0), (0, 500, 0), (0, 404, 0), (0, 401, 0), (0, 302, 0), (0, 200, 0)])  # the server's own answers
def test_what_a_transfers_end_says_of_its_server(fw, rc, http, out):
    note(fw, REACH_ONLINE, rc, http)
    assert down(fw, REACH_ONLINE) == out


@pytest.mark.parametrize('rc', [23, 42])
@pytest.mark.parametrize('before', [0, 1])
def test_a_transfer_its_own_caller_stopped_says_nothing(fw, rc, before):
    """A body over its buffer or a full card (CURLE_WRITE_ERROR), a cancel (CURLE_ABORTED_BY_CALLBACK): the state
    stays as it was."""
    note(fw, REACH_UPDATES, CURLE_COULDNT_CONNECT if before else 0, 200)
    note(fw, REACH_UPDATES, rc)
    assert down(fw, REACH_UPDATES) == before


def test_the_glyph_needs_wifi_and_a_lost_link_starts_every_service_unknown(fw, row):
    note(fw, REACH_SCROBBLE, CURLE_COULDNT_CONNECT)
    note(fw, REACH_ZONE, CURLE_OPERATION_TIMEDOUT)
    row.wifi(3)
    assert offline(fw) == 1
    row.wifi(None)  # no link: the missing fan says why, and nothing is known of any server
    assert offline(fw) == 0
    row.wifi(3)
    assert offline(fw) == 0 and not down(fw, REACH_SCROBBLE) and not down(fw, REACH_ZONE)


def test_a_card_apps_server_is_in_the_or(fw, row):
    row.wifi(3)
    row.app(0)
    row.ap.a[0].reach = 1
    assert offline(fw) == 1
    row.ap.a[0].reach = 0
    assert offline(fw) == 0


class Curl:
    """libcurl answering every transfer with rc and, when 0, http; its options as set."""
    def __init__(self, fw, rc, http=0):
        self.rc, self.http, self.opts = rc, http, {}
        def setopt(c, opt, value, *_):
            self.opts[opt] = value
        def info(c, what, out, *_):
            if what == 0x200002:  # RESPONSE_CODE
                C.cast(out, C.POINTER(C.c_long))[0] = self.http
            else:
                C.cast(out, C.POINTER(C.c_void_p))[0] = 0
            return 0
        fw.mock('fw_curl_easy_init', lambda *_: self.opts.clear() or 1)
        fw.mock('fw_curl_easy_setopt', setopt)
        fw.mock('fw_curl_easy_perform', lambda *_: self.rc)
        fw.mock('fw_curl_easy_getinfo', info)
        fw.mock('fw_curl_slist_append', lambda *_: 1)


def scrobble(fw):
    reply = C.create_string_buffer(1 << 16)
    fw.fn('scrobble__post', C.c_long, C.c_char_p, C.c_char_p, C.c_char_p, C.c_void_p)(
        b'https://ws.audioscrobbler.com/2.0/', b'x', None, reply)


def lyrics(fw):
    fw.fn('onlinematch__get', C.c_void_p, C.c_char_p, C.c_uint, C.c_void_p)(b'https://lrclib.net/api/search', 4096, None)


def cover(fw):
    out = C.create_string_buffer(0x200)
    fw.fn('onlinematch__caa', C.c_int, C.c_char_p, C.c_char_p)(b'0' * 36, out)


def update_check(fw):
    tag, code = C.create_string_buffer(32), C.c_long(0)
    fw.fn('netupdate__head', C.c_int, C.c_char_p, C.c_long, C.c_char_p, C.c_uint, C.c_void_p, C.c_void_p)(
        b'https://github.com/x/releases/latest', 10, tag, 32, None, C.byref(code))


def update_fetch(fw):
    code, xfer = C.c_long(0), (C.c_int * 2)()
    fw.fn('netupdate__fetch', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p, C.c_void_p)(
        b'https://github.com/x/update.tar', None, C.byref(code), xfer)


def zone(fw):
    fw.fn('autozone__az_worker', C.c_void_p, C.c_void_p)(None)


def radio(fw):
    fw.fn('radio__fetch_worker', C.c_void_p, C.c_void_p)(None)


@pytest.mark.parametrize('transfer, service', [
    (scrobble, REACH_SCROBBLE), (lyrics, REACH_ONLINE), (cover, REACH_ONLINE), (update_check, REACH_UPDATES),
    (update_fetch, REACH_UPDATES), (zone, REACH_ZONE), (radio, REACH_RADIO)])
def test_each_of_demos_transfers_notes_its_service(fw, transfer, service):
    """No answer puts the transfer's own service down and no other; the server's answer, even an error of its own,
    puts it up again."""
    Curl(fw, CURLE_COULDNT_CONNECT)
    transfer(fw)
    assert [down(fw, s) for s in range(REACH_N)] == [int(s == service) for s in range(REACH_N)]
    Curl(fw, 0, 404)
    transfer(fw)
    assert not down(fw, service)
