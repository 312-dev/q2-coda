"""bt_recv.c: Bluetooth receiver mode (a phone playing through the Q2) as a mode page: stock's picture, labels, codec
and gear hidden, its back arrow unseen but there; the accent's circle with the Bluetooth glyph, the phone's title and
artist or what to do, and the codec in the pill."""
import ctypes as C

import pytest

from conftest import O
from test_coverflow import Ui, themed

EVT_DESTROY = O['EVT_DESTROY']
PARTS = ('img_bticon', 'label_title', 'label_artist', 'img_btcode', 'img_setup')


class Page:
    """Stock's bluetoothrecv_page as its asset lays it out, the asset's sample title and artist in its labels."""
    def __init__(self, fw, name='bluetoothrecv_page'):
        themed(fw)
        self.fw, self.ui = fw, Ui(fw)
        self.win = self.ui.make(0, 'window', 0, 0, 375, 320, name=name)
        self.parts = {n: self.ui.make(self.win, 'image', name=n) for n in PARTS}
        self.ui[self.parts['label_title']].text = 'Shaped by time,perfected in chime'
        self.ui[self.parts['label_artist']].text = 'Shanling'
        self.back = self.ui.make(self.win, 'image', name='img_return')
        self.opacity = {}
        fw.mock('widget_set_opacity', lambda w, a, *_: self.opacity.__setitem__(w, a & 0xff))
        fw.fn('bt_recv_load', None, C.c_void_p)(self.win)

    def paint(self):
        lcd, canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
        C.c_void_p.from_address(C.addressof(canvas) + O['CANVAS_LCD']).value = C.addressof(lcd)
        texts, art = [], []
        self.fw.mock('canvas_draw_text_in_rect', lambda c, s, n, *_: texts.append(''.join(map(chr, (C.c_uint * n).from_address(s)))) or 0)
        self.fw.mock('canvas_draw_image', lambda *a: art.append(a) or 0)
        self.fw.fn('bt_recv_paint', None, C.c_void_p, C.c_void_p)(self.win, C.addressof(canvas))
        return texts


def test_the_page_hides_stock_s_face_and_its_sample_words(fw):
    p = Page(fw)
    assert not any(p.ui[w].visible for w in p.parts.values())
    assert p.opacity == {p.back: 0} and p.ui[p.back].visible  # Return's arrow: there for stock's Return, unseen
    assert p.ui[p.parts['label_title']].text in ('', None) and p.ui[p.parts['label_artist']].text in ('', None)


def test_another_page_is_left_alone(fw):
    p = Page(fw, name='usbmode_page')
    assert all(p.ui[w].visible for w in p.parts.values()) and not p.opacity
    assert p.paint() == []


def test_before_the_phone_names_a_song_it_says_where_the_sound_comes_from(fw):
    assert Page(fw).paint() == ['Playing from Phone', 'Wheel sets the volume']


@pytest.mark.parametrize('image, codec', [('btrecv_ldac', 'LDAC'), ('btrecv_aac', 'AAC'), ('btrecv_aptxhd', 'aptX HD'),
                                          ('btrecv_aptx', 'aptX'), ('btrecv_sbc', 'SBC')])
def test_the_phone_s_song_and_the_codec(fw, image, codec):
    p = Page(fw)
    p.ui[p.parts['label_title']].text, p.ui[p.parts['label_artist']].text = 'Lovely Day', 'Bill Withers'
    p.ui[p.parts['img_btcode']].image = image
    assert p.paint() == ['Lovely Day', 'Bill Withers', codec]


def test_stock_showing_its_face_again_is_hidden_at_the_next_paint(fw):
    p = Page(fw)
    p.ui[p.parts['img_bticon']].visible = 1
    p.paint()
    assert not p.ui[p.parts['img_bticon']].visible


def test_its_title_and_no_paint_once_gone_or_while_animating(fw):
    p = Page(fw)
    title = fw.fn('bt_recv_title', C.c_char_p, C.c_char_p)
    assert (title(b'bluetoothrecv_page'), title(b'bluetooth_page')) == (b'Bluetooth', None)
    p.ui.animating = 1
    assert p.paint() == []
    p.ui.animating = 0
    for evt, fn, ctx in p.ui[p.win].on:
        if evt == EVT_DESTROY:
            Ui.HANDLER(fn)(ctx, None)
    assert p.paint() == []
