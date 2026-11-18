"""q2eq's request handler, demo's q2eq_poll (patch/peq_ui.c, docs/adb.md#q2eq), in a --dev build's library: a request
file on the card's stand-in is taken through the EQ editor's own actions, with the page closed or open, and answered
with the state. The helper's side is test/unit/test_q2eq.py."""
import ctypes as C
import struct

import pytest

import host
from conftest import O
from test_peq_page import ACTIVE, EQ_3, EQ_OFF, EQ_PEQ, FOLDER, NO, Card, EqState, Page, Preset, apo, peak, saved, state

REQUEST, REPLY, TEXT, IMPORT = '/tmp/q2eq.req', '/tmp/q2eq.rep', '/tmp/q2eq.txt', '/tmp/q2eq.in'
(STATE, LIST, EXPORT, ON, PER_OUTPUT, APPLY, DISCARD, RESET, PREAMP, BALANCE, BAND, CHANNEL, TYPE, FREQ, Q, GAIN, LOAD,
 STOCK, SAVE, SAVE_AS, RENAME, DELETE, IMPORT_AS, SIMPLE, SIMPLE_GAIN, SIMPLE_RESET, SIMPLE_PRESET) = range(27)
NOW, REPLACE = 1, 2


class Request(C.Structure):
    _fields_ = [('nonce', C.c_uint), *((k, C.c_int) for k in 'op band arg flags'.split()), ('output', C.c_char * 20),
                ('name', C.c_char * 256), ('text', C.c_char * 256)]


class Reply(C.Structure):
    _fields_ = [('nonce', C.c_uint), *((k, C.c_int) for k in 'ok page flag per_output dirty'.split()),
                ('message', C.c_char * 160), ('owner', C.c_char * 20), ('name', C.c_char * 256),
                ('applied', C.c_char * 256), ('automatic', C.c_double), ('state', EqState), ('draft', Preset)]


class Q2eq:
    """The EQ page's world (test_peq_page.Page, not opened unless asked) and the request file's owner and mode."""
    def __init__(self, fw, s=None, files=None, values=None):
        self.fw = fw
        self.card = Card(fw, {ACTIVE: saved(s if s is not None else state(fw)), **(files or {})})
        self.page = Page(fw, self.card, values)
        self.uid, self.mode, self.nonce = 0, 0o100600, 7
        fw.mock('fw___xstat', self.stat)

    def stat(self, version, path, out, *_):
        if C.string_at(path).decode() not in self.card.files:
            return -1
        struct.pack_into('<I', (C.c_char * 144).from_address(out), 20, self.mode)  # o32: st_mode word 5, st_uid word 7
        struct.pack_into('<I', (C.c_char * 144).from_address(out), 28, self.uid)
        return 0

    def ask(self, op, band=0, arg=0, flags=0, name='', text='', output=''):
        """One request, one poll: the reply, or None if there was none."""
        self.nonce += 1
        self.card.files[REQUEST] = bytes(Request(self.nonce, op, band, arg, flags, output.encode(), name.encode(), text.encode()))
        self.card.files.pop(REPLY, None)
        self.fw.fn('q2eq_poll', None)()
        data = self.card.files.get(REPLY)
        if data is None:
            return None
        assert data[:8] == b'Q2EQR01\0' and len(data) == 8 + C.sizeof(Reply)
        reply = Reply.from_buffer_copy(data, 8)
        assert reply.nonce == self.nonce
        return reply

    def saved(self):
        return self.card.state()


def test_only_a_request_that_is_root_s_alone_is_read(fw_dev):
    q = Q2eq(fw_dev)
    q.mode = 0o100644
    assert q.ask(STATE) is None and REQUEST in q.card.files
    q.mode, q.uid = 0o100600, 1000
    assert q.ask(STATE) is None
    q.uid, q.mode = 0, 0o040700  # a directory
    assert q.ask(STATE) is None
    q.mode = 0o100600
    r = q.ask(STATE)
    assert r.ok and REQUEST not in q.card.files and r.state.mode == EQ_PEQ and not r.page


def test_a_short_request_is_dropped_unanswered(fw_dev):
    q = Q2eq(fw_dev)
    q.card.files[REQUEST] = b'\0' * 8
    fw_dev.fn('q2eq_poll', None)()
    assert REQUEST not in q.card.files and REPLY not in q.card.files


def test_a_band_edit_is_the_draft_s_until_applied(fw_dev):
    q = Q2eq(fw_dev)
    before = q.card.files[ACTIVE]
    r = q.ask(GAIN, band=0, text='3')
    assert r.ok and r.dirty and r.draft.bands[0].gain == 3 and r.draft.bands[0].enabled == 1  # a picked gain uses the band
    assert r.draft.automatic == 1 and r.draft.preamp == pytest.approx(r.automatic) and r.automatic < 0  # Auto follows
    assert q.card.files[ACTIVE] == before and not q.page.said  # nothing plays it, no toast
    r = q.ask(APPLY)
    assert r.ok and r.message == b'Applied' and not r.dirty
    assert q.saved().peq.bands[0].gain == 3 and q.saved().mode == EQ_PEQ
    assert q.ask(APPLY).ok == 0  # Nothing to apply


def test_now_applies_after_the_change(fw_dev):
    q = Q2eq(fw_dev)
    r = q.ask(GAIN, band=1, text='-2.5', flags=NOW)
    assert r.ok and not r.dirty and q.saved().peq.bands[1].gain == -2.5


@pytest.mark.parametrize('op, text', [(GAIN, '3.3'), (GAIN, '24.5'), (GAIN, 'x'), (PREAMP, '12.5'), (PREAMP, '-3.25'),
                                      (BALANCE, '13'), (BALANCE, 'auto'), (GAIN, '1e300'), (BALANCE, '-1e300')])
def test_a_value_off_the_picker_s_rows_is_refused(fw_dev, op, text):
    q = Q2eq(fw_dev)
    r = q.ask(op, band=0, text=text)
    assert not r.ok and r.message == b'Not a value the picker offers' and not r.dirty


def test_a_gainless_type_has_no_gain_to_set(fw_dev):
    q = Q2eq(fw_dev)
    assert q.ask(TYPE, band=0, arg=NO).ok
    r = q.ask(GAIN, band=0, text='3')
    assert not r.ok and r.draft.bands[0].gain == 0


def test_preamp_and_balance_as_their_pickers_set_them(fw_dev):
    q = Q2eq(fw_dev, state(fw_dev, peq=peak(fw_dev)))
    r = q.ask(PREAMP, text='-3')
    assert r.ok and r.draft.automatic == 2 and r.draft.preamp == -3
    r = q.ask(PREAMP, text='auto')
    assert r.draft.automatic == 1 and r.draft.preamp == pytest.approx(r.automatic) and r.automatic < -5
    r = q.ask(BALANCE, text='-1.5')
    assert r.ok and r.draft.balance == -1.5


@pytest.mark.parametrize('op, text, field, value', [(FREQ, '50000', 'frequency', 20000), (FREQ, '5', 'frequency', 20),
                                                    (FREQ, '440,5', 'frequency', 440.5), (Q, '25', 'q', 10), (Q, '0', 'q', 0.1)])
def test_frequency_and_q_take_the_editor_s_typed_value_and_clamps(fw_dev, op, text, field, value):
    q = Q2eq(fw_dev)
    r = q.ask(op, band=2, text=text)
    assert r.ok and getattr(r.draft.bands[2], field) == value


def test_a_notch_s_q_goes_to_30_and_back_to_10_off_it(fw_dev):
    q = Q2eq(fw_dev)
    q.ask(TYPE, band=0, arg=NO)
    assert q.ask(Q, band=0, text='25').draft.bands[0].q == 25
    r = q.ask(TYPE, band=0, arg=0)
    assert r.draft.bands[0].type == 0 and r.draft.bands[0].q == 10


def test_not_a_number_is_refused(fw_dev):
    q = Q2eq(fw_dev)
    r = q.ask(FREQ, band=0, text='0x10')
    assert not r.ok and r.message == b'Not a number' and r.draft.bands[0].frequency == 31


def test_a_band_s_switch_and_channels(fw_dev):
    q = Q2eq(fw_dev)
    assert not q.ask(CHANNEL, band=4, arg=2).ok  # an off band has no Channels row
    assert q.ask(BAND, band=4, arg=1).draft.bands[4].enabled == 1
    assert q.ask(CHANNEL, band=4, arg=3).draft.bands[4].enabled == 3
    assert q.ask(CHANNEL, band=4, arg=2).draft.bands[4].enabled == 2
    assert q.ask(BAND, band=4, arg=1).draft.bands[4].enabled == 2  # already on: unchanged
    assert q.ask(BAND, band=4, arg=0).draft.bands[4].enabled == 0


def test_bands_are_offered_as_the_main_screen_offers_them(fw_dev):
    q = Q2eq(fw_dev)
    assert not q.ask(BAND, band=11, arg=1).ok  # the 12th, past the spare 11th
    r = q.ask(BAND, band=10, arg=1)
    assert r.ok and r.draft.count == 11
    assert q.ask(BAND, band=11, arg=1).draft.count == 12
    assert not q.ask(BAND, band=30, arg=1).ok and not q.ask(BAND, band=-1, arg=1).ok


def test_unknown_requests_are_refused(fw_dev):
    q = Q2eq(fw_dev)
    assert q.ask(99).message == b'Unknown request'
    assert q.ask(TYPE, arg=8).message == b'No such type'


def test_the_switch_reset_and_discard(fw_dev):
    q = Q2eq(fw_dev)
    r = q.ask(ON, arg=0)
    assert r.ok and q.saved().mode == EQ_OFF and r.draft.bypass and ('PLAYSET', 'EQFLAG', 0) in q.page.config
    assert q.ask(ON, arg=1).ok and q.saved().mode == EQ_PEQ
    q.ask(GAIN, band=0, text='6')
    r = q.ask(DISCARD)
    assert not r.dirty and r.draft.bands[0].gain == 0
    q.ask(GAIN, band=0, text='6', flags=NOW)
    r = q.ask(RESET)
    assert r.dirty and r.draft.bands[0].gain == 0 and q.saved().peq.bands[0].gain == 6


def test_off_turns_simple_s_3eq_off_too(fw_dev):
    q = Q2eq(fw_dev, state(fw_dev, mode=EQ_3, gains=(3, 0, 0)))
    assert q.ask(ON, arg=0).ok and q.saved().mode == EQ_OFF and not q.saved().hold
    assert q.ask(ON, arg=0).ok and q.saved().mode == EQ_OFF  # already off: nothing


def test_a_draft_made_with_the_page_closed_opens_in_it(fw_dev):
    q = Q2eq(fw_dev)
    q.ask(GAIN, band=0, text='4')
    q.page.open()
    assert q.page.ui.dirty and q.page.ui.draft.bands[0].gain == 4


def test_unapplied_page_edits_go_when_it_closes(fw_dev):
    q = Q2eq(fw_dev)
    q.page.advanced()
    q.ask(GAIN, band=0, text='4')
    q.page.handler(q.page.page, O['EVT_DESTROY'])()
    r = q.ask(STATE)
    assert not r.dirty and r.draft.bands[0].gain == 0 and not r.page


def test_an_open_page_renders_the_change_on_its_own_screen_without_a_toast(fw_dev):
    q = Q2eq(fw_dev)
    q.page.advanced()
    q.page.click('Presets')
    r = q.ask(GAIN, band=3, text='2', flags=NOW)
    assert r.ok and r.page and r.message == b'Applied' and not q.page.said
    assert q.page.ui.screen == 2 and q.page.pending('render')  # PRESETS kept, drawn again
    q.page.settle()
    assert q.page.ui.draft.bands[3].gain == 2


def test_the_wheel_stays_on_the_tab_bar(fw_dev):
    q = Q2eq(fw_dev)
    q.page.advanced()
    q.page.ui.tabs = 1  # the wheel on Advanced's tab bar
    assert q.ask(GAIN, band=0, text='2').ok and q.page.ui.tabs == 1


def test_a_keyboard_up_on_the_page_holds_requests_off(fw_dev):
    q = Q2eq(fw_dev)
    q.page.advanced()
    q.page.click('1 ')
    q.page.click('Frequency')
    r = q.ask(GAIN, band=0, text='2')
    assert not r.ok and r.message == b'A keyboard is up on the EQ page' and r.draft.bands[0].gain == 0


def test_only_the_output_playing_can_be_changed(fw_dev):
    files = {'/mnt/data/peq-outputs/owner': b'Q2OWNER\0' + b'4.4mm'.ljust(20, b'\0')}
    q = Q2eq(fw_dev, files=files, values={('Q2POD', 'EQOUTPUT'): '1'})
    assert not q.ask(GAIN, text='2', output='3.5mm').ok
    r = q.ask(GAIN, text='2', output='4.4mm')
    assert r.ok and r.owner == b'4.4mm' and r.per_output


def test_with_eq_per_output_off_no_output_is_playing(fw_dev):
    q = Q2eq(fw_dev, files={'/mnt/data/peq-outputs/owner': b'Q2OWNER\0' + b'4.4mm'.ljust(20, b'\0')})
    r = q.ask(GAIN, text='2', output='4.4mm')
    assert r.message == b'Only the output playing can be changed' and not r.dirty


def test_presets_list_load_and_stock(fw_dev):
    files = {f'{FOLDER}/Bass.txt': apo(fw_dev, peak(fw_dev, 5.0, 60.0)), f'{FOLDER}/air.TXT': apo(fw_dev, peak(fw_dev)),
             f'{FOLDER}/notes.md': b'x'}
    q = Q2eq(fw_dev, files=files)
    assert q.ask(LIST).ok and q.card.files[TEXT] == b'Bass.txt\nair.TXT\n'
    r = q.ask(LOAD, name='Bass')
    assert r.ok and r.name == b'Bass' and r.draft.bands[0].gain == 5 and r.dirty
    assert q.ask(LOAD, name='air').name == b'air'
    assert q.ask(LOAD, name='Treble').message == b'No such preset'
    r = q.ask(STOCK, name='rock', flags=NOW)
    assert r.ok and r.applied == b'Rock' and q.saved().peq.bands[2].gain == 2
    assert q.ask(STOCK, name='Disco').message == b'No such stock preset'


def test_a_preset_the_parser_refuses_says_its_line(fw_dev):
    q = Q2eq(fw_dev, files={f'{FOLDER}/Bad.txt': b'Preamp: 99 dB\n'})
    r = q.ask(LOAD, name='Bad')
    assert not r.ok and r.message.startswith(b'Line 1: ')


def test_saving_to_a_slot_and_by_name_asks_before_replacing(fw_dev):
    q = Q2eq(fw_dev)
    q.ask(GAIN, band=0, text='3')
    assert q.ask(SAVE, arg=0).ok and f'{FOLDER}/Manual 01.txt' in q.card.files
    r = q.ask(SAVE, arg=0)
    assert not r.ok and r.message == b'A preset has that name'
    assert q.ask(SAVE, arg=0, flags=REPLACE).ok
    assert q.ask(SAVE_AS, name='  Warm. ').ok and f'{FOLDER}/Warm.txt' in q.card.files
    for bad in ('a/b', '.hidden', '   ', 'x?'):
        assert q.ask(SAVE_AS, name=bad).message == b'Name not allowed'
    assert q.ask(SAVE, arg=10).message == b'No such slot'


def test_rename_and_delete_follow_the_editor_s_rules(fw_dev):
    files = {f'{FOLDER}/A.txt': apo(fw_dev, peak(fw_dev)), f'{FOLDER}/B.txt': apo(fw_dev, peak(fw_dev))}
    q = Q2eq(fw_dev, files=files)
    q.ask(LOAD, name='A', flags=NOW)
    r = q.ask(RENAME, name='A', text='C')
    assert r.ok and r.message == b'Renamed to C' and r.applied == b'C' and f'{FOLDER}/C.txt' in q.card.files
    assert not q.ask(RENAME, name='C', text='B').ok  # Name taken
    assert not q.ask(RENAME, name='C', text='x:y').ok
    r = q.ask(DELETE, name='B')
    assert r.ok and f'{FOLDER}/B.txt' not in q.card.files
    assert q.ask(DELETE, name='B').message == b'No such preset'


def test_import_saves_the_text_as_it_is_then_loads_it(fw_dev):
    text = b'# from AutoEQ\nPreamp: -6 dB\nFilter 1: ON PK Fc 100 Hz Gain 6 dB Q 0.7\n'
    q = Q2eq(fw_dev, files={IMPORT: text})
    assert q.ask(IMPORT_AS, name='Phones').message == b'No card'
    q.card.files['/proc/mounts'] = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'
    q.card.files[IMPORT] = text
    r = q.ask(IMPORT_AS, name='Phones')
    assert r.ok and q.card.files[f'{FOLDER}/Phones.txt'] == text and r.name == b'Phones' and r.draft.bands[0].gain == 6
    assert IMPORT not in q.card.files
    q.card.files[IMPORT] = text
    assert q.ask(IMPORT_AS, name='Phones').message == b'A preset has that name'
    q.card.files[IMPORT] = b'GraphicEQ: 20 0\n'
    r = q.ask(IMPORT_AS, name='Other', flags=REPLACE)
    assert not r.ok and f'{FOLDER}/Other.txt' not in q.card.files


def test_export_is_the_draft_as_equalizer_apo_text(fw_dev):
    q = Q2eq(fw_dev)
    q.ask(GAIN, band=0, text='3')
    assert q.ask(EXPORT).ok
    p = Preset()
    error = C.create_string_buffer(64)
    data = q.card.files[TEXT]
    assert fw_dev.fn('peq_parse', C.c_int, C.c_char_p, C.c_uint, C.c_void_p, C.c_void_p)(data, len(data), C.byref(p), error)
    assert p.bands[0].gain == 3


def test_simple_s_switch_sliders_reset_and_presets(fw_dev):
    q = Q2eq(fw_dev)
    r = q.ask(SIMPLE_GAIN, band=0, text='3')  # a slider over a playing PEQ turns 3EQ on first
    assert r.ok and q.saved().mode == EQ_3 and q.saved().eq3.bands[0].gain == 3
    assert q.ask(SIMPLE_GAIN, band=2, text='9').ok and q.saved().eq3.bands[2].gain == 6  # e3_set's clamp
    assert not q.ask(SIMPLE_GAIN, band=3, text='1').ok
    r = q.ask(SIMPLE, arg=0)
    assert r.ok and q.saved().mode == EQ_OFF and not q.saved().hold  # no page to compare on: nothing held
    assert q.ask(SIMPLE, arg=1).ok and q.saved().mode == EQ_3
    assert q.ask(SIMPLE_RESET).ok and [b.gain for b in q.saved().eq3.bands[:3]] == [0, 0, 0]
    r = q.ask(SIMPLE_PRESET, name='Jazz')
    assert r.ok and q.saved().mode == EQ_PEQ and r.applied == b'Jazz' and q.saved().peq.bands[3].gain == 2
    assert q.ask(SIMPLE_PRESET, name='Polka').message == b'No such stock preset'


def test_per_output_s_switch(fw_dev):
    q = Q2eq(fw_dev)
    assert q.ask(PER_OUTPUT, arg=1).per_output and q.page.config == [('Q2POD', 'EQOUTPUT', 1)]
    assert q.ask(PER_OUTPUT, arg=1).per_output and len(q.page.config) == 1  # already on
    assert not q.ask(PER_OUTPUT, arg=0).per_output and q.page.config[-1] == ('Q2POD', 'EQOUTPUT', 0)
