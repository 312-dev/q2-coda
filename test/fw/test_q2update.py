"""q2update's request handler, demo's q2update_poll (patch/sw_update.c, docs/adb.md#installing-an-update-over-adb), in a
--dev build's library: a request file runs Software Update's own SD card path, answered in the ack file at each step;
`install` goes on to stock's updater with no Install Update? card, `check` (the dry run) stops before it. The helper's
side is test/q2update.py; the host's, test/unit/test_q2flash.py."""
import ctypes as C
import struct

import pytest

import host
from conftest import Files
from test_misc import toasts
from test_sw_update import SW_BAD, SW_CHECKING, SW_OK, Sw, carded, opened, update_tar

REQUEST, ACK = '/tmp/q2update.req', '/tmp/q2update.ack'
INSTALLED = host.tools_build.dev_tag(1)  # the dev library's tag (conftest dev_library)


class Q2update:
    """The card's and /tmp's files, the request file's owner and mode, and the threads and timers held so a test steps
    the check itself."""
    def __init__(self, fw, stock=1, files=None):
        self.fw = fw
        self.files = Files(fw, files if files is not None else {})
        self.uid, self.mode = 0, 0o100600
        self.pages = opened(fw)
        fw.mock('fw___xstat', self.stat)
        fw.mock('check_updatefw_exist', lambda *_: stock)
        fw.mock('fw_pthread_create', lambda *_: 0)
        fw.mock('timer_add', lambda *_: 5)
        self.sw = fw.var('sw_update__swu', Sw)

    def stat(self, version, path, out, *_):
        if C.string_at(path).decode() not in self.files.files:
            return -1
        struct.pack_into('<I', (C.c_char * 144).from_address(out), 20, self.mode)  # o32: st_mode word 5, st_uid word 7
        struct.pack_into('<I', (C.c_char * 144).from_address(out), 28, self.uid)
        return 0

    def ask(self, text):
        """A request, one poll: the ack's words, or None."""
        self.files.files[REQUEST] = text.encode()
        self.files.files.pop(ACK, None)
        self.fw.fn('q2update_poll', None)()
        return self.ack()

    def ack(self):
        data = self.files.files.get(ACK)
        return None if data is None else data.decode().split()

    def tick(self):
        return self.fw.fn('sw_update__sw_tick', C.c_int, C.c_void_p)(None)


def test_only_a_request_that_is_root_s_alone_is_read(fw_dev):
    q = Q2update(fw_dev)
    q.mode = 0o100644
    assert q.ask('41 version') is None and REQUEST in q.files.files
    q.mode, q.uid = 0o100600, 1000
    assert q.ask('41 version') is None
    q.uid, q.mode = 0, 0o040700
    assert q.ask('41 version') is None
    q.mode = 0o100600
    assert q.ask('41 version') == ['41', 'version', INSTALLED, '-'] and REQUEST not in q.files.files


def test_version_answers_without_opening_anything(fw_dev):
    q = Q2update(fw_dev)
    assert q.ask('7 version') == ['7', 'version', INSTALLED, '-']
    assert not q.pages() and not fw_dev.calls('check_updatefw_exist')


@pytest.mark.parametrize('text', ['', 'x check', '7', '7 flash'])
def test_a_request_it_cannot_read_is_answered_unknown_or_dropped(fw_dev, text):
    q = Q2update(fw_dev)
    words = q.ask(text)
    assert REQUEST not in q.files.files and not q.pages()
    assert words is None if not text[:1].isdigit() else words[:2] == ['7', 'unknown']


def test_install_runs_the_cards_check_then_stocks_updater_with_no_card(fw_dev):
    q = Q2update(fw_dev, files={'/mnt/mmc/update.tar': update_tar()})
    assert q.ask('9 install') == ['9', 'taken', INSTALLED, '-']
    assert q.pages() == ['swupdate_page'] and q.sw.state == SW_CHECKING and q.sw.timer == 5
    q.sw.state, q.sw.tag = SW_OK, b'V0JZi'
    assert q.tick() == 7
    assert q.ack() == ['9', 'installing', INSTALLED, 'V0JZi']
    assert not carded(fw_dev) and not q.sw.page  # no Install Update? card: the page closes for stock's dialog
    assert len(fw_dev.calls('idle_add')) == 1  # sw_install, as Install and Restart runs it


def test_check_is_the_dry_run_it_stops_before_the_updater(fw_dev):
    q = Q2update(fw_dev)
    said = toasts(fw_dev)
    q.ask('10 check')
    q.sw.state, q.sw.tag = SW_OK, b'V0JZi'
    assert q.tick() == 7
    assert q.ack() == ['10', 'ok', INSTALLED, 'V0JZi']
    assert not carded(fw_dev) and not fw_dev.calls('idle_add') and not q.sw.page
    assert said == [b'Update checked: ready to install']


@pytest.mark.parametrize('state, word', [(SW_BAD, 'bad'), (2, 'latest'), (3, 'nofile')])
def test_a_check_that_fails_shows_its_page_and_says_why(fw_dev, state, word):
    q = Q2update(fw_dev)
    q.ask('11 install')
    q.sw.state = state
    q.tick()
    assert q.ack()[:2] == ['11', word] and q.sw.page and q.sw.result == state and not fw_dev.calls('idle_add')


@pytest.mark.parametrize('code, word', [(-15, 'low'), (-2, 'nocard'), (-11, 'nofile'), (-12, 'full')])
def test_stocks_own_checks_answer_at_once(fw_dev, code, word):
    q = Q2update(fw_dev, stock=code)
    q.ask('12 install')
    assert q.ack()[:2] == ['12', word] and q.pages() == ['swupdate_page'] and not fw_dev.calls('fw_pthread_create')


def test_leaving_the_page_mid_check_is_cancelled(fw_dev):
    q = Q2update(fw_dev)
    q.ask('13 install')
    fw_dev.fn('sw_update__sw_gone', C.c_int, C.c_void_p, C.c_void_p)(q.sw.page, None)
    q.sw.state = 8  # SW_CANCELLED, as the thread answers
    q.tick()
    assert q.ack()[:2] == ['13', 'cancelled'] and not fw_dev.calls('idle_add')


def test_one_at_a_time(fw_dev):
    q = Q2update(fw_dev)
    q.ask('14 check')
    assert q.ask('15 install')[:2] == ['15', 'busy']
    assert len(fw_dev.calls('check_updatefw_exist')) == 1


def test_the_updaters_own_refusal_is_said_after_installing(fw_dev):
    q = Q2update(fw_dev)
    q.ask('16 install')
    q.sw.state, q.sw.tag = SW_OK, b'V0JZi'
    q.tick()
    fw_dev.mock('navigator_to', lambda *_: 18)
    fw_dev.fn('sw_update__sw_install', C.c_int, C.c_void_p)(None)
    assert q.ack()[:2] == ['16', 'failed'] and q.sw.result == SW_BAD


def test_a_release_build_has_no_handler(fw):
    with pytest.raises(AttributeError):
        fw.fn('q2update_poll', None)
