"""guest_ui.c: Guest Play's screens on q2-guest's contract. A guest asking: the confirmation card, Allow Once first; a
second guest: Keep or Replace; what ended a guest: a toast or Couldn't Connect; the guest's Now Playing with Stop Guest
Play; Settings > Bluetooth > Guest Play and its pages; the status row's glyph (Home's row: test_home_np.py)."""
import ctypes as C

import pytest

from conftest import STRIP_GO, STRIP_SAVE
from test_guest import Rig
from test_nav_library_pages import Config, Ui

KEY_CENTER, KEY_RETURN, KEY_PLAY, KEY_NEXT = 218, 170, 171, 173
TRUSTED = '/mnt/data/q2-guest/trusted'


class Gui(Rig):
    """Rig's files and FIFO, a Ui for the pages, and the payload's own clock for guest_ui_poll."""
    def __init__(self, fw):
        super().__init__(fw)
        self.fifo_up = True
        self.ui = Ui(fw)
        Config(fw)
        self.now = 1000
        fw.mock('time_now_ms', lambda *_: self.now)
        self.ui.windows.append(self.ui.new('window_create', 0, 0, 0, 0, 0))  # the page in sight
        fw.mock('window_close', lambda w, *_: self.ui.destroy(w))

    def look(self, **kv):
        self.seq = getattr(self, 'seq', 0) + 1
        self.state(**{**kv, 'seq': self.seq})
        self.poll()
        self.now += 300
        self.fw.fn('guest_ui_poll', None)()

    def page(self, name):
        return next((w for w in self.ui.windows if self.ui.name.get(w) == name), None)

    def sent(self):
        return [line.strip() for line in self.lines]


@pytest.fixture
def g(fw):
    return Gui(fw)


@pytest.mark.parametrize('name, short', [("Alex's Pixel", 'Alex'), ('Sam iPhone', 'Sam'), ('Echo', 'Echo')])
def test_a_guest_is_named_short_in_toasts(fw, name, short):
    out = C.create_string_buffer(40)
    fw.fn('guest_short', None, C.c_char_p, C.c_char_p, C.c_uint)(name.encode(), out, 40)
    assert out.value.decode() == short


def test_a_guest_asking_brings_up_the_card(g):
    g.look(state='idle', enabled=1)
    assert 'dialog/sortselect_dialog' not in g.ui.went
    g.look(state='asking', guest="Alex's Pixel", mac='AA:BB', code='482913', countdown=24)
    assert 'dialog/sortselect_dialog' in g.ui.went  # confirm(): the card, as pair_prompt.c asks it
    gu = C.c_char.in_dll(g.fw.lib, 'guest_ui__gu')
    text = C.string_at(C.addressof(gu), g.fw.sizes['guest_ui__gu'])
    assert C.string_at(C.addressof(C.c_char.in_dll(g.fw.lib, 'pair_prompt__pair_name'))) == b"Alex's Pixel"  # its title
    assert b"Wants to play music.\nCheck it's on Alex's phone." in text


@pytest.mark.parametrize('ask, row, line', [(1, 0, 'allow-once'), (1, 1, 'allow-always'), (1, 2, 'decline'),
                                            (2, 0, 'keep'), (2, 1, 'replace')])
def test_the_card_s_answer_goes_to_the_daemon(g, ask, row, line):
    g.look(state='idle', enabled=1)
    g.fw.fn('guest_ui__answered', None, C.c_void_p, C.c_int, C.c_int)(ask, row, 0)
    assert g.sent()[-1] == line


@pytest.mark.parametrize('event, name, leave, toast', [
    ('left', "Alex's Pixel", 'resume', 'Alex left. Resuming your music'),
    ('left', "Alex's Pixel", 'stay', 'Alex left'),
    ('lost', "Alex's Pixel", 'resume', 'Lost Alex. Resuming your music'),
    ('declined', "Alex's Pixel", 'resume', "Declined Alex's Pixel"),
    ('timeout', "Alex's Pixel", 'resume', "Declined Alex's Pixel"),
    ('refused', 'Sam iPhone', 'resume', "Sam iPhone isn't a trusted guest"),
    ('kept', 'Sam iPhone', 'resume', 'Turned away Sam iPhone'),
])
def test_what_ended_a_guest_is_a_toast(g, event, name, leave, toast):
    g.look(state='idle', enabled=1, event_seq=3, event='left', eventname='old')  # the last one, said already
    assert g.ui.toasts == []
    g.look(state='idle', enabled=1, event_seq=4, event=event, eventname=name, leave=leave)
    assert g.ui.toasts == [toast]
    g.look(state='idle', enabled=1, event_seq=4, event=event, eventname=name, leave=leave)
    assert g.ui.toasts == [toast]  # once per event_seq


def test_a_failed_pairing_offers_try_again(g):
    g.look(state='open', enabled=1, event_seq=1, event='left', eventname='x')
    g.look(state='open', enabled=1, event_seq=2, event='failed', eventname="Alex's Pixel")
    page = g.page('guestfail_page')
    assert page and g.ui.toasts == []
    g.ui.key(page, KEY_CENTER)
    assert g.sent()[-1] == 'open' and not g.page('guestfail_page')


def test_a_guest_playing_opens_its_now_playing_and_centre_stops_it(g):
    g.look(state='idle', enabled=1)
    g.look(state='playing', enabled=1, guest="Alex's Pixel", title='Levitating', out='BMW 6R81',
           outcodec='SBC 48000 328', **{'in': 'LDAC 96000 990'})
    page = g.page('guestnp_page')
    assert page
    for key, line in ((KEY_PLAY, 'key toggle'), (KEY_NEXT, 'key next'), (KEY_CENTER, 'stop')):
        g.ui.key(page, key)
        assert g.sent()[-1] == line
    g.look(state='idle', enabled=1)
    assert not g.page('guestnp_page')


def test_a_receiver_s_guest_opens_no_page(g):
    g.look(state='idle', enabled=1)
    g.look(state='playing', enabled=1, guest='Spotify Connect', source='spotify')
    assert not g.page('guestnp_page')


@pytest.mark.parametrize('leg, words', [('LDAC 96000 990', 'LDAC · 96 kHz · 990 kbps'), ('AAC 44100 0', 'AAC · 44.1 kHz'),
                                        ('SBC 48000 328', 'SBC · 48 kHz · 328 kbps'), ('', '')])
def test_a_leg_s_codec_line(fw, leg, words):
    out = C.create_string_buffer(48)
    fw.fn('guest_ui__leg_words', None, C.c_char_p, C.c_char_p, C.c_uint)(leg.encode(), out, 48)
    assert out.value.decode() == words


def test_the_settings_page_follows_the_state(g):
    g.files[TRUSTED] = b"AA:BB\t0\t1763300000\tSam's iPhone\n"
    g.look(state='idle', enabled=1, allow='ask', leave='resume', hotspot=0)
    g.fw.fn('guest_settings_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    page = g.page('guestset_page')
    captions = g.ui.captions(page)
    assert captions[:3] == ['Guest Play', 'Name', 'Let a Guest Connect']
    assert {'Allow', 'When a Guest Leaves', 'Trusted Guests', "Sam's iPhone", 'Hotspot'} <= set(captions)
    g.ui.click(page, 0)
    assert g.sent()[-1] == 'off'
    g.ui.click(page, captions.index('Trusted Guests Only'))
    assert g.sent()[-1] == 'allow trusted'
    g.ui.click(page, captions.index('Stay Paused'))
    assert g.sent()[-1] == 'leave stay'
    g.look(state='off', enabled=0)
    assert g.ui.captions(page)[0] == 'Guest Play' and 'Name' not in g.ui.captions(page)


def test_a_trusted_guest_s_page_forgets_and_asks(g):
    g.files[TRUSTED] = b"AA:BB\t0\t1763300000\tSam's iPhone\n"
    g.look(state='idle', enabled=1)
    g.fw.fn('guest_settings_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    set_page = g.page('guestset_page')
    g.ui.click(set_page, g.ui.captions(set_page).index("Sam's iPhone"))
    page = g.page('guesttrust_page')
    assert g.ui.captions(page)[:3] == ['Rename', 'Ask Before Playing', 'Forget']
    g.ui.click(page, 1)
    assert g.sent()[-1] == 'ask AA:BB 1'
    g.fw.fn('guest_ui__forgot', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert g.sent()[-1] == 'forget AA:BB'


@pytest.mark.parametrize('state, enabled, on, value', [('off', 0, 0, 'Off'), ('idle', 1, 1, 'Ask'), ('idle', 0, 0, 'Off')])
def test_on_is_the_switch(g, state, enabled, on, value):
    g.look(state=state, enabled=enabled, allow='ask')
    assert g.fw.fn('guest_ui_on', C.c_int)() == on
    assert g.fw.fn('guest_ui_value', C.c_char_p)().decode() == value


def test_the_replace_card_keeps_the_guest_s_name_it_shows(g):
    """confirm() keeps the card's strings by pointer: the guest's name stays its own while later reads go on."""
    g.look(state='idle', enabled=1)
    g.look(state='replace', guest="Alex's Pixel", mac='AA', new="Sam's iPhone", newmac='BB', countdown=30)
    for key in ('title', 'artist', 'out', 'state', 'event'):
        g.fw.fn('guest_field', None, C.c_char_p, C.c_char_p, C.c_uint)(key.encode(), C.create_string_buffer(8), 8)
    gu = C.string_at(C.addressof(C.c_char.in_dll(g.fw.lib, 'guest_ui__gu')), g.fw.sizes['guest_ui__gu'])
    assert b"Keep Alex's Pixel" in gu and b"\x00Alex's Pixel\x00" in gu and b"Sam's iPhone wants to play music" in gu


def test_a_guest_playing_on_its_now_playing_keeps_the_screen_lit(g):
    """Smart Awake (navigation.c keep_lit_poll) counts the guest's Now Playing as the owner's: lit while the guest's
    song plays with its page in front; paused, or another page in front, it times out as usual."""
    lit = g.fw.fn('guest_ui_keeps_lit', C.c_int, C.c_void_p)
    g.look(state='idle', enabled=1)
    g.look(state='playing', enabled=1, guest="Alex's Pixel", title='Levitating')
    page = g.page('guestnp_page')
    assert lit(page) == 1
    assert lit(g.ui.windows[0]) == 0  # the page under it
    g.look(state='paused', enabled=1, guest="Alex's Pixel", title='Levitating')
    assert g.page('guestnp_page') == page and lit(page) == 0


def strip_ret(g):
    from test_search_strip import Ss
    ss = g.fw.var('search_strip__ss', Ss)
    return ss.ret if ss.page else None


def test_guest_play_s_name_gets_the_strip_s_check(g):
    """Naming Guest Play saves a name: the letter strip's return key shows the check (search_strip.c ss_ret_kind)."""
    g.look(state='idle', enabled=1, allow='ask', leave='resume', hotspot=0)
    g.fw.fn('guest_settings_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    page = g.page('guestset_page')
    g.ui.click(page, g.ui.captions(page).index('Name'))
    assert strip_ret(g) == STRIP_SAVE


@pytest.mark.parametrize('row, want', [(1, STRIP_SAVE), (2, STRIP_GO)])
def test_the_hotspot_s_name_gets_the_check_and_its_password_the_arrow(g, row, want):
    g.fw.fn('guest_ui__hot_click', C.c_int, C.c_void_p, C.c_void_p)(row, None)
    assert strip_ret(g) == want


def test_renaming_a_trusted_guest_gets_the_strip_s_check(g):
    """A trusted guest's Rename saves a name: the return key shows the check (search_strip.c ss_ret_kind)."""
    g.files[TRUSTED] = b"AA:BB\t0\t1763300000\tSam's iPhone\n"
    g.look(state='idle', enabled=1)
    g.fw.fn('guest_settings_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    set_page = g.page('guestset_page')
    g.ui.click(set_page, g.ui.captions(set_page).index("Sam's iPhone"))
    g.ui.click(g.page('guesttrust_page'), 0)
    assert strip_ret(g) == STRIP_SAVE
