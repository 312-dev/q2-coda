"""bt_pair.c: a device pairing by numeric comparison (docs/internals.md#bluetooth-pairing-card). libbtctl's agent is
doubled: its image is a buffer holding the pending request's word (BTCTL_PENDING) and agent_response's word that
vouches for it, and the request is a DBusMessage laid out as libdbus 1.12 lays one, body (device path, passkey). The
card is confirm()'s (test_confirm.py Card); btctl_reply's answers are recorded."""
import ctypes as C
import struct

import pytest

from conftest import Files, threads_now
from test_confirm import Card
from test_nav_hold import Ui, ui  # noqa: F401 (fixtures)
from test_nav_painters import scratch, st, theme  # noqa: F401 (fixtures)
from test_q2update import Q2update

PENDING, CHECK, CHECK_WORD = 0x45900, 0xe3b0, 0x8e245900
TIMEOUT_MS = 60000  # BlueZ 5.66's REQUEST_TIMEOUT
PATH = '/org/bluez/hci0/dev_4C_9B_1F_22_7A_03'
NAME_FILE = '/tmp/q2-btpair-name'


class DbmString(C.Structure):
    _fields_ = [('str', C.c_void_p), ('len', C.c_int), ('allocated', C.c_int), ('flags', C.c_uint)]


class DbmMessage(C.Structure):
    _fields_ = [('refcount', C.c_int), ('header', DbmString), ('fields', C.c_int * 10), ('bits', C.c_uint),
                ('body', DbmString)]


class Bp(C.Structure):
    """bt_pair.c's bp."""
    _fields_ = [('seq', C.c_uint), ('msg', C.c_void_p), ('code', C.c_char * 8), ('path', C.c_char * 64),
                ('base', C.c_ulong), ('taken', C.c_uint), ('live', C.c_int), ('dialog', C.c_void_p),
                ('want', C.c_void_p), ('at', C.c_uint), ('name', C.c_char * 64), ('body', C.c_char * 112),
                ('mac', C.c_char * 18), ('job_running', C.c_int), ('job_done', C.c_uint), ('seen', C.c_uint),
                ('asked_at', C.c_uint), ('named', C.c_int)]


def confirmation(path, passkey):
    """RequestConfirmation(path, passkey) as libdbus holds it: the header's bytes and the body's."""
    p = path.encode()
    body = struct.pack('<I', len(p)) + p + b'\0'
    body += b'\0' * (-len(body) % 4) + struct.pack('<I', passkey)
    header = b'l\x01\x00\x01' + struct.pack('<II', len(body), 7)
    keep = [C.create_string_buffer(header), C.create_string_buffer(body)]
    m = DbmMessage()
    m.header.str, m.body.str, m.body.len = C.addressof(keep[0]), C.addressof(keep[1]), len(body)
    return m, keep


class Pairing:
    """libbtctl's image and agent doubled, the card's dialog routed (Card), the clock, the Name's shell and its file."""
    def __init__(self, fw, ui, name=None):
        theme(fw)
        self.fw, self.ui, self.now, self.replies, self.name = fw, ui, [1000], [], name
        self.card = Card(fw, ui, self.now)
        self.image = C.create_string_buffer(PENDING + 8)
        struct.pack_into('<I', self.image, CHECK, CHECK_WORD)
        self.bp = fw.var('bt_pair__bp', Bp)
        self.bp.base = C.addressof(self.image)
        self.files = Files(fw, {})
        threads_now(fw)
        fw.mock('fw_system', self.system)
        fw.mock('btctl_reply', lambda t, s, *_: self.replies.append((t, C.string_at(s).decode())) or 1)
        self.poll = fw.fn('bt_pair_poll', None)

    def system(self, cmd, *_):
        command = C.string_at(cmd).decode()
        if NAME_FILE in command and self.name:
            self.files.files[NAME_FILE] = f'{PATH} {self.name}\n'.encode()
        return 0

    @property
    def pending(self):
        return struct.unpack_from('<Q', self.image, PENDING)[0]

    @pending.setter
    def pending(self, v):
        struct.pack_into('<Q', self.image, PENDING, v)

    def ask(self, code='239014', path=PATH):
        """BlueZ's RequestConfirmation through libbtctl: pending set, then the prompt; its answer."""
        self.msg, self.keep = confirmation(path, int(code))
        self.pending = C.addressof(self.msg)
        return self.fw.fn('bt_pair_prompt', C.c_int, C.c_int, C.c_char_p, C.c_void_p)(5, code.encode(), None)

    def raised(self):
        """Two passes: the first asks BlueZ the Name, the second reads its answer and opens the card."""
        self.poll()
        self.poll()

    def cancel(self):
        """libbtctl's Cancel: its pending request dropped, and nothing said to demo."""
        self.pending = 0

    def up(self):
        return bool(self.fw.fn('confirm_dialog', C.c_void_p)())

    def title(self):
        return C.string_at(C.addressof(C.c_char.in_dll(self.fw.lib, 'pair_prompt__pair_name'))).decode()


@pytest.fixture
def pairing(fw, ui, st):
    return Pairing(fw, ui, name='Pixel 10')


def test_other_prompt_types_stay_libbtctl_s(pairing):
    prompt = pairing.fw.fn('bt_pair_prompt', C.c_int, C.c_int, C.c_char_p, C.c_void_p)
    for t in (1, 2, 3, 4, 6, 7):
        assert prompt(t, b'0000', None) == 0  # libbtctl answers these itself, as stock's callback left them
    pairing.poll()
    assert not pairing.up() and pairing.replies == []


def test_a_confirmation_raises_the_card_naming_the_device(pairing):
    assert pairing.ask() == 1
    assert pairing.bp.path.decode() == PATH
    pairing.poll()  # BlueZ asked the Name: the card waits for its answer
    assert not pairing.up()
    pairing.poll()  # the answer is in: the card opens naming the device
    assert pairing.up() and 'dialog/sortselect_dialog' in pairing.ui.went
    assert pairing.title() == 'Pixel 10'
    canvas = pairing.card.paint()
    texts = [t[0] for t in canvas.texts]
    assert 'Pair with Pixel 10?' in texts and '239 014' in texts


def test_the_card_opens_unnamed_when_bluez_does_not_answer(fw, ui, st):
    p = Pairing(fw, ui)
    fw.mock('fw_system', lambda *_: 0)
    fw.mock('fw_pthread_create', lambda *_: 0)  # the shell's run never ends
    p.ask()
    p.poll()
    assert not p.up()
    p.now[0] += 1000  # BTP_NAME_MS
    p.poll()
    assert p.up() and p.title() == 'this device'


def test_a_cancel_closes_the_card_and_answers_nothing(pairing):
    pairing.ask()
    pairing.raised()
    assert pairing.up()
    pairing.cancel()
    pairing.poll()
    assert not pairing.up() and pairing.replies == []  # refused by BlueZ already; never a yes
    pairing.ui.due()
    assert pairing.card.answers == [] and pairing.replies == []


def test_no_cancel_the_card_refuses_at_bluez_s_timeout(pairing):
    pairing.ask()
    pairing.raised()
    pairing.now[0] += TIMEOUT_MS - 1
    pairing.poll()
    assert pairing.up() and pairing.replies == []
    pairing.now[0] += 1
    pairing.poll()
    assert not pairing.up() and pairing.replies == [(5, 'no')]
    pairing.poll()
    assert pairing.replies == [(5, 'no')]  # once


@pytest.mark.parametrize('row, word', [(0, 'no'), (1, 'yes')])
def test_the_answer_goes_to_libbtctl(pairing, row, word):
    pairing.ask()
    pairing.raised()
    pairing.fw.fn('bt_pair__answered', None, C.c_void_p, C.c_int, C.c_int)(pairing.bp.seq, row, 0)
    assert pairing.replies == [(5, word)]


def test_an_answer_after_the_cancel_says_nothing(pairing):
    pairing.ask()
    pairing.raised()
    pairing.cancel()
    pairing.fw.fn('bt_pair__answered', None, C.c_void_p, C.c_int, C.c_int)(pairing.bp.seq, 1, 0)
    assert pairing.replies == []  # the yes would answer a request that is no longer there


def test_a_device_with_no_name_yet_is_unknown_with_its_address(fw, ui, st):
    p = Pairing(fw, ui, name=None)
    p.ask()
    p.raised()
    assert p.title() == 'this device'
    texts = [t[0] for t in p.card.paint().texts]
    assert 'Pair with this device?' in texts and 'Unknown device' in texts and '4C:9B:1F:22:7A:03' in texts
    p.name = 'Pixel 10'  # BlueZ read it: the card follows
    p.now[0] += 1000
    p.poll()
    p.now[0] += 1
    p.poll()
    texts = [t[0] for t in p.card.paint().texts]
    assert 'Pair with Pixel 10?' in texts and not any('4C:9B' in t for t in texts)


@pytest.mark.parametrize('path', ['/org/bluez/hci0/dev_4C_9B_1F_22_7A_03/extra', '/not/bluez/dev_4C_9B_1F_22_7A_03'])
def test_a_request_that_does_not_check_out_names_no_device(fw, ui, st, path):
    p = Pairing(fw, ui, name='Pixel 10')
    asked = []
    fw.mock('fw_system', lambda cmd, *_: asked.append(C.string_at(cmd)) or 0)
    p.ask(path=path)
    p.raised()
    assert asked == []  # no device to ask BlueZ about
    texts = [t[0] for t in p.card.paint().texts]
    assert 'Pair with this device?' in texts and 'Unknown device' in texts


def test_the_passkey_must_match_the_message(fw, ui, st):
    p = Pairing(fw, ui)
    p.msg, p.keep = confirmation(PATH, 111111)
    p.pending = C.addressof(p.msg)
    fw.fn('bt_pair_prompt', C.c_int, C.c_int, C.c_char_p, C.c_void_p)(5, b'239014', None)
    assert p.bp.path == b''


def test_the_update_is_busy_only_while_the_card_is_up(fw_dev):
    u = Ui(fw_dev)
    theme(fw_dev)
    q = Q2update(fw_dev)
    p = Pairing(fw_dev, u, name='Pixel 10')  # its dialog routes and files over q2update's
    q.files = p.files
    p.ask()
    p.raised()
    assert p.up()
    assert q.ask('21 check')[:2] == ['21', 'busy']
    p.cancel()
    p.poll()
    assert q.ask('22 check')[1] != 'busy'  # taken: stock's own checks answer next (no update file here: nofile)
