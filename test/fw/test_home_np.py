"""patch/home_np.c: Home's first row led by the play state. Its words are Playing, Paused or Guest Play (Playing in Edit
Home), it is there only while something is loaded or a guest plays, it opens the guest's Now Playing while one plays,
and its glyph is Up Next's level bars in the accent or the pause's two bars in Text Secondary, before words moved past
it."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_guest import PAUSED, PLAYING
from test_guest_ui import Gui
from test_nav_library_pages import stock_page
from test_nav_painters import Canvas, theme

HOME = ['playing', 'localmusic', 'stream', 'folder', 'coverflow', 'home_search', 'settings']
EVT_CLICK = O['EVT_CLICK']
CLICK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
LABEL_X = 24  # Now Playing's label in its row; the text column is 2 px in (hscroll_label's margin)


class Home(Gui):
    """Home's window and main list: the rows as coverflow_home tags them, each with its label (Now Playing's as stock
    translates it) and the row's image, stock's click on Now Playing's."""
    def __init__(self, fw, reduce=False):
        super().__init__(fw)
        theme(fw, **({'REDUCEMOTION': b'1'} if reduce else {}))
        self.win, self.view = stock_page(self.ui, 'scroll_view_home')
        self.ui.windows.append(self.win)
        self.stock: list = []
        self.fn = CLICK(lambda ctx, e: self.stock.append(ctx) or 0)
        for row in HOME:
            item = self.ui.new('view_create', self.view, 0, 0, 375, 32)
            self.ui.name[item] = 'btn_' + row
            label = self.ui.new('hscroll_label_create', item, LABEL_X, 0, 300, 32)
            self.ui.name[label] = 'label_' + row
            self.ui.text[label] = 'Now Playing' if row == 'playing' else row
            img = self.ui.new('image_create', item, 0, 0, 375, 32)
            self.ui.name[img] = 'img_' + row
            if row == 'playing':
                self.label, self.img = label, img
        self.emitter, self.item = C.create_string_buffer(0x40), C.create_string_buffer(0x40)
        struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
        struct.pack_into('<Q', self.item, 0, 0x77)  # emitter_on_with_tag's item: {ctx, id, type, handler...}
        struct.pack_into('<i', self.item, O['EMIT_TYPE'], EVT_CLICK)
        struct.pack_into('<Q', self.item, 12, C.cast(self.fn, C.c_void_p).value)
        C.c_void_p.from_address(self.img + O['W_EMITTER']).value = C.addressof(self.emitter)
        fw.fn('lr_home_tag', None, C.c_void_p)(self.view)
        fw.fn('home_np_bind', None, C.c_void_p)(self.win)

    def apply(self, np=1, playing=PLAYING):
        """The rows shown and Now Playing's row's words, Home laid out again."""
        self.status = playing
        self.fw.fn('lr_home_apply', None, C.c_void_p, C.c_int)(self.view, np)
        return [self.ui.name[i][4:] for i in self.ui.kids[self.view] if i not in self.ui.hidden], self.ui.text[self.label]

    def sync(self):
        """A bar paint: the row follows the play state."""
        self.fw.fn('home_np_sync', None)()

    def margin(self):
        return self.ui.props.get((self.label, 'style:normal:margin_left'))

    def paint(self):
        canvas = Canvas(self.fw)
        self.fw.fn('home_np_paint', None, C.c_void_p, C.c_void_p)(self.img, canvas.addr)
        return canvas.boxes


@pytest.fixture
def home(fw):
    return Home(fw)


SHOWN = [r for r in HOME if r != 'folder']  # Folders' own rule: the card has none here


@pytest.mark.parametrize('playing, words', [(PLAYING, 'Playing'), (PAUSED, 'Paused')])
def test_the_row_reads_its_play_state_and_its_words_start_past_the_glyph(home, playing, words):
    assert home.apply(playing=playing) == (SHOWN, words)
    assert home.margin() == 2 + 18


def test_with_nothing_loaded_the_row_goes(home):
    assert home.apply(np=0)[0] == SHOWN[1:]


@pytest.mark.parametrize('np', [1, 0])
def test_a_guest_playing_makes_it_guest_play_shown_with_nothing_of_the_owner_s_loaded(home, np):
    home.look(state='playing', enabled=1, guest='Alex', source='spotify')
    assert home.apply(np) == (SHOWN, 'Guest Play')
    home.look(state='paused', enabled=1, guest='Alex', source='spotify')
    assert home.apply(np) == (SHOWN, 'Guest Play')
    home.look(state='idle', enabled=1)
    assert home.apply(np) == (SHOWN if np else SHOWN[1:], 'Playing')


def test_edit_home_lists_it_as_playing_without_the_glyph(fw, home):
    home.apply(playing=PAUSED)
    fw.fn('home_np_row', None, C.c_void_p, C.c_int)(home.ui.lookup(home.view, 'btn_playing', 1), 1)
    assert home.ui.text[home.label] == 'Playing' and home.margin() == 2
    assert home.paint() == []


def test_the_row_opens_stock_s_now_playing_or_the_guest_s(home):
    assert home.ui.offs == [(home.img, EVT_CLICK, C.cast(home.fn, C.c_void_p).value, 0x77)]
    click = home.ui.handler(home.img, EVT_CLICK)
    click()
    assert home.stock == [0x77] and not home.page('guestnp_page')
    home.look(state='playing', enabled=1, guest='Alex', source='spotify')  # a receiver's: its page does not open by itself
    click()
    assert home.stock == [0x77] and home.page('guestnp_page')
    home.look(state='idle', enabled=1)
    click()
    assert home.stock == [0x77, 0x77]


def test_playing_leads_with_up_next_s_bars_in_the_accent(fw, home):
    home.apply(playing=PLAYING)
    boxes = home.paint()
    x = LABEL_X + 2
    assert [b[0] for b in boxes] == [(x + i * 9 // 2, 16 + 6 - h, 3, h) for i, h in enumerate((7, 11, 5))]
    canvas = Canvas(fw)  # Up Next's own bars, drawn at the same place, frame and colour
    fw.fn('un_bars', None, C.c_void_p, C.c_int, C.c_int)(canvas.addr, x, 16 + 6)
    assert boxes == canvas.boxes and {b[1] for b in boxes} != {0xffababab, 0xffffffff}


def test_paused_leads_with_the_pause_s_two_bars_in_text_secondary(home):
    home.apply(playing=PAUSED)
    boxes = home.paint()
    x = LABEL_X + 2
    assert [b[0] for b in boxes] == [(x + 1, 10, 4, 12), (x + 7, 10, 4, 12)]
    assert {b[1] for b in boxes} == {0xffababab}


def test_the_status_row_s_play_state_moves_to_the_row_only_while_home_shows_it(fw, home):
    lead = fw.fn('home_np_lead', C.c_int)
    home.apply()
    assert lead() == 1
    home.ui.windows.append(home.ui.new('window_create', 0, 0, 0, 0, 0))  # a page over Home
    assert lead() == 0
    home.ui.windows.pop()
    home.apply(np=0)  # the row gone
    assert lead() == 0


PAUSE = [(LABEL_X + 3, 10, 4, 12), (LABEL_X + 9, 10, 4, 12)]


def words(home):
    """The row's words shown: [(text, label)], the old ones' copy first while they fade."""
    row = home.ui.lookup(home.view, 'btn_playing', 1)
    return [home.ui.text.get(k) for k in home.ui.kids[row] if home.ui.kind[k] == 'hscroll_label_create']


def test_pausing_morphs_the_bars_into_the_pause_and_cross_fades_the_words(fw):
    home = Home(fw)
    home.apply(playing=PLAYING)
    bars = home.paint()
    home.status = PAUSED
    home.sync()
    assert home.paint() == bars  # the morph's start: the bars, still
    assert words(home) == ['Paused', 'Playing']  # the label, then the old words' copy over it
    home.now += 125
    mid = home.paint()
    assert len(mid) == 3 and mid[1][0][2] < 3 and mid[1][1] >> 24 < 0xff  # the middle narrows and fades
    assert bars[0][1] != mid[0][1] != 0xffababab  # on its way from the accent to Text Secondary
    home.now += 125
    assert [b[0] for b in home.paint()] == PAUSE and {b[1] for b in home.paint()} == {0xffababab}
    home.sync()
    assert words(home) == ['Paused']  # the copy gone once faded


def test_resuming_mid_morph_turns_back_from_where_it_is_and_then_the_bars_move(fw):
    home = Home(fw)
    home.apply(playing=PLAYING)
    bars = home.paint()
    home.status = PAUSED
    home.sync()
    home.now += 125
    mid = home.paint()
    home.status = PLAYING
    home.sync()
    assert home.paint() == mid  # back from where it stood
    home.now += 250
    assert home.paint() == bars
    home.sync()
    home.ui.fire('hn_tick')
    assert home.paint() != bars  # the bars move again


def test_under_reduce_motion_the_switch_is_a_cut(fw):
    home = Home(fw, reduce=True)
    home.apply(playing=PLAYING)
    home.status = PAUSED
    home.sync()
    assert [b[0] for b in home.paint()] == PAUSE
    assert words(home) == ['Paused']


def test_the_bars_timer_runs_only_while_they_move_on_home(home):
    """No timer of its own while paused or with a page over Home: the suite's pages count their timers."""
    home.apply(playing=PAUSED)
    home.sync()
    assert not home.ui.pending('hn_tick')
    home.status = PLAYING
    home.sync()
    home.now += 250
    home.sync()
    assert home.ui.pending('hn_tick')
    home.ui.windows.append(home.ui.new('window_create', 0, 0, 0, 0, 0))  # a page over Home
    assert home.ui.fire('hn_tick') == 0 and not home.ui.pending('hn_tick')
    home.sync()
    assert not home.ui.pending('hn_tick')
