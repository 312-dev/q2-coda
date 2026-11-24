"""tools/ipod.py's page rewrites on small synthetic UI trees built to the shape each one checks for,
its UI, style, PNG and code readers and patchers on small synthetic files, and the ImageMagick calls
behind its icons, recorded instead of run."""
import copy
import dataclasses
import math
import re
import shutil
import struct
import subprocess
import zlib

import pytest

import ipod
import theme

OLED_SET = theme.geometry(theme.builtins()[0])  # the one theme's set: its theme.xml


def w(name, *children, kind='view', at=None, **props):
    return [kind, at or [0, 0, 10, 10], {'name': name, **props}, list(children)]


def named(root):
    return {n[2].get('name'): n for n in ipod.walk(root)}


MAGIC = bytes.fromhex('12122211')


def test_decode():
    """A type fills up to 32 bytes, geometry is signed and properties are UTF-8."""
    data = (MAGIC + b'slider'.ljust(32, b'\0') + struct.pack('<4i', -200, 5, 30, 375) + 'text\0单曲\0name\0slider_play\0\0'.encode() +
            b'x' * 32 + struct.pack('<4i', 1, 2, 3, 4) + b'\0\0' + b'\0')
    assert ipod.decode(data) == ['slider', [-200, 5, 30, 375], {'text': '单曲', 'name': 'slider_play'}, [['x' * 32, [1, 2, 3, 4], {}, []]]]


@pytest.mark.parametrize('data,message', [
    (b'\x12\x12\x22\x12' + ipod.encode(w('view'))[4:], 'Unexpected AWTK UI magic'),
    (MAGIC + bytes(47), 'Truncated UI widget'),
    (MAGIC + b'view'.ljust(48, b'\0') + b'name\0a\0name\0b\0\0\0', 'Duplicate UI property'),
    (ipod.encode(w('view')) + b'\0', 'Trailing UI data'),
])
def test_decode_refuses(data, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.decode(data)


STATES = ('normal', 'disable', 'focused')


def clear_borders(props):
    return all(props[f'style:{s}:border_color'] == '#00000000' for s in STATES)


def local_card():
    return w('btn_localmusic',
             w('img_localmusic', kind='image', **{'style:normal:bg_image': 'menu_music', 'style:focused:bg_image': 'menu_musicdown', 'draw': 'menu_music'}),
             w('label_localmusic', kind='label', text='stock'))


def menu():
    return w('slide_menu', w('btn_playing'), local_card(), w('btn_folder'), w('btn_stream'), w('btn_playset'), w('btn_sysset'), kind='slide_menu')


def home():
    m = menu()
    m[3].insert(2, w('btn_coverflow', w('img_coverflow', kind='image'), w('label_coverflow', kind='label', text='Album Wall')))  # stock V1.36's own card
    return w('home', m, w('img_left', kind='image'), w('img_right', kind='image'))


def test_ipod_home():
    root = home()
    ipod.ipod_home(root, OLED_SET)
    assert [n[2]['name'] for n in root[3]] == ['img_homeart', 'list_view_home', 'list_view_homeset', 'slide_menu', 'img_left', 'img_right']
    art = root[3][0]
    assert art[:3] == ['image', [0, 0, 375, 320 - OLED_SET.bar], {'name': 'img_homeart', 'draw_type': 'fill'}]
    assert root[3][2][2]['visible'] == 'false' and 'visible' not in root[3][1][2]
    rows = root[3][1][3][0][3]
    assert [r[2]['name'] for r in rows] == ['btn_' + r for r in ipod.HOME_ROWS]
    assert [r[3][0][2].get('text') for r in rows] == [None, 'Library', 'Radio', 'Games', 'Folders', 'Cover Flow', 'Search',
                                                     'Settings']  # 2026-11-19: Games, not Streaming; 2026-11-20: Radio
    assert [r[3][1][2]['name'] for r in rows][2:7] == ['img_radio', 'img_games', 'img_folder', 'img_cover_flow', 'img_home_search']
    sets = root[3][2][3][0][3]
    assert [r[2]['name'] for r in sets] == ['btn_set_wifi', 'btn_set_bt', 'btn_sysset', 'btn_playset', 'btn_set_stream',
                                            'btn_set_car', 'btn_set_display', 'btn_set_controls', 'btn_set_power']
    assert [r[3][0][2]['text'] for r in sets] == ['Wi-Fi', 'Bluetooth', 'General', 'Playback', 'Streaming', 'Car Remote',
                                                  'Display', 'Controls', 'Battery']  # Streaming after Playback
    stock = home()[3][0][3]
    assert root[3][3][3] == stock[:2] + stock[3:]  # stock's cards, Album Wall's gone, the carousel only hidden
    for n in root[3][3:]:
        assert n[2]['visible'] == 'false' and n[2]['sensitive'] == 'false'


def test_ipod_home_wants_the_carousel():
    root = home()
    root[3].pop()
    with pytest.raises(ValueError, match='^Unexpected home carousel$'):
        ipod.ipod_home(root, OLED_SET)


def test_ipod_home_wants_the_cards():
    root = home()
    root[3][0][3].pop()
    with pytest.raises(ValueError, match='^Unexpected home cards$'):
        ipod.ipod_home(root, OLED_SET)


def battery():
    return w('label_battery', kind='label', at=[0, 0, 40, 30], **{f'style:{s}:font_size': '18' for s in STATES})


def bar():
    left = w('view_left', w('img_state', at=[0, 0, 20, 30]), w('label_eq', at=[0, 0, 50, 30]), w('img_vol', at=[5, 5, 30, 20]),
             w('label_vol', at=[6, 6, 30, 20]), at=[0, 0, 187, 30], children_layout='default(c=3,xm=50,s=5)')
    right = w('view_right', w('img_bt', at=[0, 0, 20, 30]), w('img_wifi', at=[0, 0, 40, 30]), battery(), w('img_battery', at=[0, 0, 30, 30]),
              w('img_synclink', at=[1, 1, 20, 20]), at=[187, 0, 188, 30], children_layout='default(c=3,xm=50,s=5)')
    return w('system_bar', left, right, at=[0, 0, 375, 30])


def test_status_bar_colours():
    root = bar()
    ipod.status_bar(root, OLED_SET)
    # every state: the bar is disabled, so AWTK paints its disable style (a black one was the band over full-bleed pages)
    assert all(root[2][f'style:{s}:bg_color'] == '#00000000' for s in STATES) and clear_borders(root[2])


def test_status_bar_widgets():
    root = bar()
    ipod.status_bar(root, OLED_SET)
    n = named(root)
    assert n['img_wifi'][1][2] == 14 and n['label_eq'][1][2] == 28
    for s in STATES:
        assert n['label_eq'][2][f'style:{s}:font_size'] == '16'
        assert n['label_battery'][2][f'style:{s}:font_size'] == str(ipod.BATT_PCT_PX)
        assert n['label_battery'][2][f'style:{s}:text_align_h'] == 'right'
    assert n['label_battery'][1][2] == ipod.BATT_PCT_W and n['label_battery'][2]['visible'] == 'false'
    assert n['view_battery'][:3] == ['view', [0, 0, ipod.BATT_H_W, 0], {'name': 'view_battery', 'visible': 'false'}]


def test_status_bar_hidden_stay_off_screen():
    root = bar()
    ipod.status_bar(root, OLED_SET)
    n = named(root)
    assert [c[2]['name'] for c in root[3][2:5]] == ipod.STATUS_HIDDEN
    bar_h = OLED_SET.bar
    assert n['img_vol'][1] == [-200, 5, 30, bar_h] and n['label_vol'][1] == [-200, 6, 30, bar_h] and n['img_synclink'][1] == [-200, 1, 20, bar_h]




def test_status_bar_wants_its_views():
    root = bar()
    root[3][0][2]['name'] = 'x'
    with pytest.raises(ValueError, match='^Unexpected status bar$'):
        ipod.status_bar(root, OLED_SET)


def test_status_bar_wants_its_widgets():
    root = bar()
    root[3][0][3].pop()
    with pytest.raises(ValueError, match='^Unexpected status bar widgets$'):
        ipod.status_bar(root, OLED_SET)


@pytest.mark.parametrize('state', STATES)
def test_status_bar_wants_the_stock_battery_size(state):
    root = bar()
    named(root)['label_battery'][2][f'style:{state}:font_size'] = '12'
    with pytest.raises(ValueError, match='^Unexpected battery label$'):
        ipod.status_bar(root, OLED_SET)


def test_status_bar_wants_the_stock_layout():
    root = bar()
    root[3][1][2]['children_layout'] = 'default(c=3,xm=40,s=5)'
    with pytest.raises(ValueError, match='^Unexpected status bar layout$'):
        ipod.status_bar(root, OLED_SET)














SLIDER = {'style:normal:bg_image': 'a', 'style:normal:fg_image': 'b', 'style:normal:icon': 'c', 'style:normal:y_offset': '6',
          'style:normal:bg_color': '#111', 'style:pressed:fg_color': '#222', 'style:normal:round_radius': '9', 'slide_with_bar': 'true'}


def test_plain_slider_is_used_by_np_slider():
    slider: list = ['slider', [0, 0, 1, 1], dict(SLIDER), []]
    ipod.np_slider(slider, 10, 100, 50, 4)
    assert slider[1] == [10, 100 - ipod.NP_TOUCH // 2, 50, ipod.NP_TOUCH]
    props = slider[2]
    assert not any(k.endswith((':bg_image', ':fg_image', ':icon', ':y_offset')) for k in props)
    assert props['style:normal:bg_color'] == ipod.NP_TRACK and props['style:pressed:fg_color'] == ipod.NP_FILL
    assert props['style'] == 's_ipod_progress' and props['bar_size'] == '4' and props['slide_with_bar'] == 'true'
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        assert props[f'style:{state}:border_color'] == '#00000000'
    assert props['style:normal:round_radius'] == '2'


# Stock's info page's labels, in its order
STOCK_INFO = ('label_album', 'label_genre', 'label_size', 'label_year', 'label_encode', 'label_bits', 'label_kbps',
              'label_sample', 'label_location')


def playing(info_names=()):
    buttons = w('view_buttons', w('img_return', at=[1, 2, 3, 4], self_layout='default(x=1,y=2,w=3,h=4)'), w('img_spectrum', kind='image'),
                w('img_fav', kind='image', image='fav', x_offset='1', **{'style:normal:text_align_h': 'left', 'keep': 'yes'}),
                w('img_more', kind='image', image='more', y_offset='1', self_layout='default(x=9,y=9,w=9,h=9)'), w('img_vu', kind='image'),
                w('img_playmode', kind='image', y_offset='1'))
    title = w('scrlabel_title', kind='hscroll_label', at=[9, 9, 9, 9], **{'style:normal:font_size': '24', 'style:disable:font_size': '24'})
    artist = w('scrlabel_artist', kind='hscroll_label', **{'style:normal:font_size': '20', 'style:normal:text_color': '#123456',
                                                           'style:normal:text_align_h': 'center', 'text': 'x'})
    lrc = w('view_lrc', w('scroll_lrc', kind='scroll_view', self_layout='default(x=0,y=0,w=-10,h=-20)'), self_layout='default(x=0,y=0,w=100%,h=178)')
    info = w('view_info', *([w(k, kind='label', at=[0, 22 * i, 225, 22], text=k) for i, k in enumerate(info_names)] or
                            [w('label_a', at=[5, 6, 7, 8]), w('label_b', at=[10, 6, 7, 8])]))
    album = w('view_album', w('img_cover', kind='image'), w('img_playstate', kind='image'))
    slide = w('slide_view_view', w('slide_view', album, lrc, info, w('label_lyricmsg', at=[3, 4, 5, 6])),
              w('slide_indicator1', at=[0, 1, 2, 3], **{'style:normal:fg_color': '#1', 'style:normal:selected_fg_color': '#2'}))
    return w('playing_page', buttons, title, artist, w('label_playtime', **{'style:normal:text_color': '#1'}),
             w('label_playlen', at=[1, 1, 1, 1], **{'style:normal:text_color': '#1'}), slide,
             w('slider_play', kind='slider', **SLIDER), w('img_repeata', at=[1, 2, 3, 4]), w('img_repeatb', at=[5, 6, 7, 8]), w('image_wait'))


@pytest.fixture
def page():
    root = playing(STOCK_INFO)
    ipod.playing_page(root, OLED_SET)
    ipod.encode(root)  # whole pixels, or stock's format can't hold them
    return root, named(root)


def test_playing_page_top_row(page):
    root, n = page
    buttons = n['view_buttons']
    assert buttons[1] == [0, 0, 375, ipod.NP_TOP] and n['img_return'][1] == [-200, 2, 3, 4]
    assert n['img_more'][2] == {'name': 'img_more', 'image': 'more', 'draw_type': 'center'}
    assert n['img_playmode'][2] == {'name': 'img_playmode', 'opacity': '0'}
    assert n['img_fav'][2] == {'name': 'img_fav', 'image': 'fav', 'keep': 'yes', 'draw_type': 'center'}
    assert [c[2]['name'] for c in buttons[3]] == ['img_return']  # More and the heart go to the art's page
    assert 'label_ipod_pos' not in n  # no queue place ("3 of 12")


def test_playing_page_moves_controls_beside_the_art(page):
    _, n = page
    assert [c[2].get('name') for c in n['view_album'][3]] == ['img_cover', 'img_playstate', 'scrlabel_title', 'scrlabel_artist',
                                                              'label_ipod_album', 'img_fav', 'img_more']


def test_playing_page_text_column(page):
    _, n = page
    title, artist, album = n['scrlabel_title'][2], n['scrlabel_artist'][2], n['label_ipod_album'][2]
    assert title['style'] == 's_scrlabel_white20l'
    for s in STATES:  # every state: the labels are disabled, so a lone normal size would not apply
        assert title[f'style:{s}:font_size'] == str(ipod.NP_GRID_TITLE_PX)
    assert artist['style:normal:text_align_h'] == 'left'
    assert album['name'] == 'label_ipod_album' and album['text'] == '' and artist['text'] == 'x'


def test_playing_page_art_and_spinners(page):
    _, n = page
    art, x = OLED_SET.np_art, OLED_SET.text_x
    assert n['img_cover'][1] == [x, 0, art, art]
    assert n['img_playstate'][1] == [x + (art - 120) // 2, (art - 120) // 2, 120, 120]
    assert n['img_playstate'][2]['opacity'] == '0'
    assert n['image_wait'][1] == [x + (art - 54) // 2, (art - 54) // 2, 54, 54]


def test_playing_page_lyrics_and_info(page):
    _, n = page
    assert n['label_lyricmsg'][1] == [3 + 75, 4, 5, 6]
    assert [n[k][1][0] for k in STOCK_INFO] == [75] * len(STOCK_INFO)  # stock's 225 px column, centred
    lrc = n['scroll_lrc'][2]
    assert lrc['self_layout'] == 'default(x=0,y=0,w=100%,h=100%)'
    assert lrc['children_layout'] == f'list_view(x=0,y={ipod.inc("LYRIC_SLOT")},s=0)'
    assert n['view_lrc'][1][1] == 0


def test_playing_page_swipe_area(page):
    _, n = page
    dots = n['slide_indicator1']
    assert dots[2]['style:normal:fg_color'] == '#555555' and dots[2]['style:normal:selected_fg_color'] == '#eeeeee'
    assert dots[2]['anchor_x'] == '187px' and dots[2]['anchor_y'] == '0px'


def test_playing_page_progress_row(page):
    root, n = page
    assert n['label_ipod_remain'][2]['text'] == '' and 'visible' not in n['label_ipod_remain'][2]
    assert n['label_playlen'][2]['visible'] == 'false'
    assert n['label_playlen'][2]['style:normal:text_color'] == '#1'


def test_playing_page_oled_grid():
    """OLED's grid (np_art): the art at the text column on the window's top, the words 18 px right of it on the
    design's line tops, their boxes ending on the right column (the margins stock's hscroll_label keeps inside
    them), More and the heart at the right end of the art's bottom row on the art's page; the bar from the
    text column to the right column at np_bar_y on screen, the times under its ends, the play mode's target centred
    under them, the window's last child, the dots under it, and the slide stopping short of the bar."""
    g = theme.geometry(theme.builtins()[0])
    root = playing(STOCK_INFO)
    ipod.playing_page(root, g)
    ipod.encode(root)
    n, right = named(root), 375 - g.right_x
    assert (g.np_art, g.np_bar_y, g.text_x, g.right_x) == (150, 228, 24, 28)
    assert n['img_cover'][1] == [24, 0, 150, 150] and n['slide_view_view'][1][:2] == [0, 0]
    tx = 24 + 150 + ipod.NP_GRID_GAP
    for name, top, h in (('scrlabel_title', 6, 27), ('scrlabel_artist', 38, 22), ('label_ipod_album', 62, 22)):
        box = n[name][1]
        assert box == [tx - 2, top, right - tx + 4, h] and g.bar + top in (64, 96, 120), name  # the design's line tops
        assert {v for k, v in n[name][2].items() if k.endswith(':font_size')} == {str(h)}, name
    assert n['scrlabel_artist'][2]['style:normal:text_color'] == ipod.NP_GRID_SECONDARY == n['label_ipod_album'][2]['style:normal:text_color']
    page = n['view_album'][3]
    assert 'label_ipod_pos' not in n and n['img_more'] in page and n['img_fav'] in page and n['img_playmode'] not in page
    more, fav = n['img_more'][1], n['img_fav'][1]
    assert more[0] + more[2] // 2 == right - ipod.NP_GRID_MORE_INSET
    assert more[1] + more[3] // 2 == 150 - ipod.NP_GRID_ROW_ASCENT + ipod.NP_GRID_ROW_PX // 2  # on the art's bottom row
    assert more[1] + more[3] <= n["slider_play"][1][1]  # clear of the bar's target
    # the heart under the album (the user's mock-up, 2026-11-18): its target x 182 to 238, y 142 to 184 on screen,
    # the 34 px glyph centred, x 193 to 227, y 146 to 180, clear of More's target and the bar's
    assert [fav[0], g.bar + fav[1], fav[0] + fav[2], g.bar + fav[1] + fav[3]] == [182, 142, 238, 184]
    assert fav[0] + fav[2] <= more[0] and fav[1] + fav[3] <= n['slider_play'][1][1]
    assert fav[1] == n['label_ipod_album'][1][1] + n['label_ipod_album'][1][3]  # on the album line's foot
    bar_y = g.np_bar_y - g.bar
    slider = n['slider_play']
    assert slider[1] == [24, bar_y + 3 - ipod.NP_GRID_TOUCH // 2, right - 24, ipod.NP_GRID_TOUCH] and slider[2]['bar_size'] == '6'
    assert n['slide_view'][1][3] == bar_y - 5 < slider[1][1] + (slider[1][3] - 6) // 2
    assert n['view_lrc'][2]['self_layout'] == f'default(x=0,y=0,w=375,h={bar_y - 5})'
    play, remain = n['label_playtime'], n['label_ipod_remain']
    assert play[1] == [24, bar_y + 22 - 9, 80, 19] and remain[1] == [right - 80, bar_y + 22 - 9, 80, 19]
    assert play[2]['style:normal:font_name'] == 'inter' == remain[2]['style:normal:font_name']
    assert play[2]['style:normal:text_color'] == '#FFFFFF' == remain[2]['style:normal:text_color']
    mode = n['img_playmode']
    assert root[3][-1] is mode and mode[2]['opacity'] == '0' and mode[1] == [53, bar_y + ipod.NP_GRID_MODES_DY - 22, 269, 44]
    assert g.bar + mode[1][1] + mode[1][3] // 2 == 286  # the four buttons' row (np_bar.c np_modes)
    dots = n['slide_indicator1'][1]
    assert dots[1] == bar_y + ipod.NP_GRID_DOTS_DY and n['slide_indicator1'][2]['visible'] == 'false'  # the row's on dots take their place
    assert n['slide_view_view'][1][3] == dots[1] + 10
    assert g.bar + dots[1] + 10 <= 320 - 8


def test_playing_page_oled_info_values():
    """OLED's info page is the hidden source of About's grid (np_about.c paints it): each of stock's labels gets a
    hidden <name>_value hscroll_label beside it, which ringnav_np_info fills with the value."""
    g = theme.geometry(theme.builtins()[0])
    root = playing(STOCK_INFO)
    ipod.playing_page(root, g)
    n = named(root)
    info = n['view_info'][3]
    assert {c[2]['name'] for c in info} == {k for k in ipod.NP_INFO_NAMES} | {k + '_value' for k in ipod.NP_INFO_NAMES}
    for k in ipod.NP_INFO_NAMES:
        value = n[k + '_value']
        assert value[0] == 'hscroll_label' and value[2]['visible'] == 'false' and value in info, k
    assert n['view_info'][2]['visible'] == 'false'


def test_playing_page_final_order(page):
    root, _ = page
    assert [c[2].get('name') for c in root[3]] == ['view_info', 'view_buttons', 'label_playtime', 'label_playlen', 'label_ipod_remain',
                                                   'slide_view_view', 'slider_play', 'img_repeata', 'img_repeatb', 'image_wait',
                                                   'img_playmode']


def test_playing_page_takes_the_details_out_of_the_swipe(page):
    """⋯'s About shows the info page (np_about.c): it leaves the slide_view for the window, first, hidden, at the
    slide's place in the window, its own layout gone, so the slide_view holds the art and lyrics pages alone."""
    root, n = page
    info, box, slide = n['view_info'], n['slide_view_view'][1], n['slide_view'][1]
    assert root[3][0] is info and info[2]['visible'] == 'false' and 'self_layout' not in info[2]
    assert info[1] == [box[0] + slide[0], box[1] + slide[1], slide[2], slide[3]]
    assert [c[2]['name'] for c in n['slide_view'][3]] == ['view_album', 'view_lrc', 'view_rate', 'label_lyricmsg']


def test_playing_page_puts_the_rating_page_after_the_lyrics(page):
    """The rating page (patch/np_rate.c) is an empty view the swipe reaches after the lyrics' page, the slide_view's
    third: art, lyrics, rating. It fills the slide_view, whose size full screen sets at run time."""
    _, n = page
    pages = n['slide_view'][3]
    rate = n['view_rate']
    assert pages.index(rate) == pages.index(n['view_lrc']) + 1 == 2 and rate[0] == 'view' and rate[3] == []
    assert rate[2]['self_layout'] == 'default(x=0,y=0,w=100%,h=100%)' and rate[1][2:] == n['slide_view'][1][2:]


def test_the_rating_page_follows_a_lyrics_page_that_holds_its_list():
    """Stock's own lyrics page is a view holding view_lrc (with label_lyricmsg): the rating page goes after that
    page, not inside it."""
    root = playing(STOCK_INFO)
    named_ = named(root)
    slide = named_['slide_view']
    lrc = named_['view_lrc']
    lyric = w('view_lyric', named_['label_lyricmsg'], lrc)
    slide[3] = [named_['view_album'], lyric, named_['view_info']]
    ipod.playing_page(root, OLED_SET)
    assert [c[2]['name'] for c in slide[3]] == ['view_album', 'view_lyric', 'view_rate']


def test_playing_page_wants_the_stock_children():
    root = playing()
    root[3].pop()
    with pytest.raises(ValueError, match='^Unexpected Now Playing page$'):
        ipod.playing_page(root, OLED_SET)


def test_playing_page_wants_every_state_size():
    root = playing()
    named(root)['scrlabel_artist'][2]['style:normal:font_size'] = '20'
    root[3][1][2]['style:extra:font_size'] = '1'
    root[3][1][2]['style:other:font_size'] = '2'
    with pytest.raises(ValueError, match='^Now Playing text keeps a state size$'):
        ipod.playing_page(root, OLED_SET)


@pytest.mark.parametrize('layout', ['default(x=1,y=0,w=-10,h=1)', 'other'])
def test_playing_page_wants_the_stock_lyrics(layout):
    root = playing()
    named(root)['scroll_lrc'][2]['self_layout'] = layout
    with pytest.raises(ValueError, match='^Unexpected lyrics scroll view$'):
        ipod.playing_page(root, OLED_SET)


def test_playing_page_refuses_a_lyrics_layout():
    root = playing()
    named(root)['scroll_lrc'][2]['children_layout'] = 'x'
    with pytest.raises(ValueError, match='^Unexpected lyrics scroll view$'):
        ipod.playing_page(root, OLED_SET)


def test_playing_page_three_lyric_lines_must_fit(monkeypatch):
    monkeypatch.setitem(ipod.O, 'LYRIC_SLOT', 60)
    with pytest.raises(ValueError, match='^Three lyric lines must fit the page$'):
        ipod.playing_page(playing(STOCK_INFO), OLED_SET)


def test_playing_page_three_lyric_lines_just_fit(monkeypatch):
    slot = 178 // 3
    monkeypatch.setitem(ipod.O, 'LYRIC_SLOT', slot)
    ipod.playing_page(playing(STOCK_INFO), OLED_SET)


def cover():
    return w('bigcover', w('img_bigcover', kind='image', self_layout='default(x=0,y=0,w=100%,h=100%)', draw_type='scale_auto'),
             w('view_shade', at=[0, 270, 375, 50]), w('scrlabel_title', at=[42, 281, 290, 28]), fullscreen='true')


def test_bigcover_page():
    root = cover()
    ipod.bigcover_page(root)
    art, shade, title = root[3]
    size = ipod.BC_ART
    x, y = (375 - size) // 2, (320 - size) // 2
    assert art[1] == [x, y, size, size] and art[2]['self_layout'] == f'default(x={x},y={y},w={size},h={size})'
    foot = y + size - 50
    assert shade[1] == [x, foot, size, 50]
    assert title[1] == [x + ipod.BC_TITLE_PAD, foot + 11, size - 2 * ipod.BC_TITLE_PAD, 28]


@pytest.mark.parametrize('change,message', [
    (lambda r: r[2].update(fullscreen='false'), 'Unexpected full-screen art page'),
    (lambda r: r[3].pop(), 'Unexpected full-screen art page'),
    (lambda r: r[3][0][2].update(self_layout='x'), 'Unexpected full-screen art layout'),
    (lambda r: r[3][0][2].update(draw_type='x'), 'Unexpected full-screen art layout'),
    (lambda r: r[3][1].__setitem__(1, [0, 271, 375, 50]), 'Unexpected full-screen art layout'),
    (lambda r: r[3][2].__setitem__(1, [42, 282, 290, 28]), 'Unexpected full-screen art layout'),
])
def test_bigcover_page_wants_stock(change, message):
    root = cover()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.bigcover_page(root)


def quick():
    icons, labels = [], []
    for i, (icon, label) in enumerate(ipod.QS_GRID.items()):
        x, y = 20 + i % 4 * 85, (0, 112)[i // 4]
        icons.append(w('img_' + icon, kind='image', at=[x, y, ipod.QS_ICON, ipod.QS_ICON]))
        labels.append(w('label_' + label, kind='label', style='s_label_white18c'))
    slider = w('slider_backlight', kind='slider', slide_with_bar='true', **{'style:normal:bg_color': '#1', 'style:normal:fg_color': '#2'})
    light = w('view_backlight', slider, w('image0', kind='image', at=[0, 0, ipod.QS_SUN, 9]), w('image1', kind='image', at=[0, 0, ipod.QS_SUN, 9]))
    return w('statusbar_dialog', w('view_menu', *icons, *labels), light)


def test_quick_settings_grid():
    root = quick()
    ipod.quick_settings(root)
    ipod.encode(root)
    n = named(root)
    assert n['view_menu'][1] == [0, ipod.QS_TOP, 375, 2 * ipod.QS_PITCH - ipod.QS_ROW_GAP]
    for i, (icon, label) in enumerate(ipod.QS_GRID.items()):
        row = i // 4
        x = 20 + i % 4 * 85
        assert n['img_' + icon][1] == [x, row * ipod.QS_PITCH, ipod.QS_ICON, ipod.QS_ICON]
        assert n['label_' + label][1] == [x + (ipod.QS_ICON - ipod.QS_LABEL_W) // 2, row * ipod.QS_PITCH + ipod.QS_ICON + ipod.QS_LABEL_GAP,
                                          ipod.QS_LABEL_W, ipod.QS_LABEL_H]
        assert n['label_' + label][2]['style'] == 's_label_white16c' and n['label_' + label][2]['style:normal:text_align_v'] == 'top'


def test_quick_settings_brightness_row():
    root = quick()
    ipod.quick_settings(root)
    n = named(root)
    y = ipod.QS_TOP + 2 * ipod.QS_PITCH - ipod.QS_ROW_GAP + ipod.QS_ROW_GAP
    assert n['view_backlight'][1] == [0, y, 375, ipod.QS_TOUCH]
    sun_y = (ipod.QS_TOUCH - ipod.QS_SUN) // 2
    assert n['image0'][1] == [ipod.QS_EDGE, sun_y, ipod.QS_SUN, ipod.QS_SUN]
    assert n['image1'][1] == [375 - ipod.QS_EDGE - ipod.QS_SUN, sun_y, ipod.QS_SUN, ipod.QS_SUN]
    left = ipod.QS_EDGE + ipod.QS_SUN + 12
    assert n['slider_backlight'][1] == [left, 0, 375 - 2 * left, ipod.QS_TOUCH]
    props = n['slider_backlight'][2]
    assert props['self_layout'] == f'default(x={left},y=0,w={375 - 2 * left},h={ipod.QS_TOUCH})'
    assert props['dragger_size'] == str(ipod.QS_TOUCH) and props['bar_size'] == str(ipod.QS_BAR)
    assert props['style:normal:bg_color'] == ipod.NP_TRACK and props['style:normal:fg_color'] == ipod.NP_FILL


@pytest.mark.parametrize('change,message', [
    (lambda r: r[3][0][2].update(name='x'), 'Unexpected quick settings'),
    (lambda r: r[3][1][2].update(name='x'), 'Unexpected quick settings'),
    (lambda r: r[3][0][3].pop(), 'Unexpected quick settings controls'),
    (lambda r: named(r)['img_lock'][1].__setitem__(2, 50), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['img_lock'][1].__setitem__(3, 50), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['img_lock'][1].__setitem__(1, 1), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['img_usbmode'][1].__setitem__(1, 0), 'usbmode: unexpected quick settings control'),
    (lambda r: named(r)['label_lock'][2].update(style='x'), 'lock: unexpected quick settings control'),
    (lambda r: named(r)['image0'][2].update(name='x'), 'Unexpected brightness row'),
    (lambda r: named(r)['image0'][1].__setitem__(2, 1), 'Unexpected brightness row'),
    (lambda r: named(r)['image1'][1].__setitem__(2, 1), 'Unexpected brightness row'),
    (lambda r: named(r)['slider_backlight'][2].update(slide_with_bar='false'), 'Unexpected brightness slider'),
])
def test_quick_settings_wants_stock(change, message):
    root = quick()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.quick_settings(root)


def test_quick_settings_volume_panel_must_cover_brightness(monkeypatch):
    monkeypatch.setitem(ipod.O, 'VOL_PANEL_Y', ipod.inc('VOL_PANEL_Y') + 1)
    with pytest.raises(ValueError, match='^The volume panel must cover the brightness row$'):
        ipod.quick_settings(quick())


def dialog():
    return w('confirminfo_dialog', w('img_cancel', kind='image', at=[0, 0, 80, 80], other='x'), w('img_enter', kind='image', at=[0, 0, 80, 80]))


def test_confirm_dialog():
    root = dialog()
    ipod.confirm_dialog(root)
    assert clear_borders(root[2])
    for i, (n, text) in enumerate(zip(root[3], ('Cancel', 'OK'))):
        assert n[0] == 'button' and n[1] == [0, ipod.CONFIRM_TOP + i * ipod.CONFIRM_ROW, 375, ipod.CONFIRM_ROW]
        assert 'other' not in n[2]
        assert (n[2]['name'], n[2]['text'], n[2]['focusable']) == (('img_cancel', 'img_enter')[i], text, 'false')
        assert n[2]['style:normal:font_size'] == '20' and n[2]['style:normal:text_align_h'] == 'center'
        for state in ('normal', 'pressed', 'over', 'focused'):
            assert n[2][f'style:{state}:bg_color'] == ('#333333' if state == 'pressed' else '#00000000')
            assert n[2][f'style:{state}:border_color'] == '#00000000' and n[2][f'style:{state}:border_width'] == '0'
            assert n[2][f'style:{state}:text_color'] == '#FFFFFF'


@pytest.mark.parametrize('change', [lambda r: r[3][0][2].update(name='x'), lambda r: r[3].pop(),
                                    lambda r: r[3][1][1].__setitem__(2, 79), lambda r: r[3][0][1].__setitem__(3, 79)])
def test_confirm_dialog_wants_stock(change):
    root = dialog()
    change(root)
    with pytest.raises(ValueError, match='^Unexpected confirm dialog$'):
        ipod.confirm_dialog(root)


def settings_page(*images):
    return w('page', w('holder', w('slider', kind='slider', **SLIDER)),
             *[w(f'img{i}', kind='image', at=[1, 2, 3, 4], image=name) for i, name in enumerate(images)],
             w('other', kind='image', at=[5, 6, 7, 8], image='keep'), w('plain', kind='label', image='vol_left'))


@pytest.mark.parametrize('left,right', [('drop_lighleft', 'drop_lightright'), ('balance_left', 'balance_right'), ('vol_left', 'vol_right')])
def test_slider_plain(left, right):
    root = settings_page(left, right)
    ipod.slider_plain(ipod.SETTINGS_SLIDERS[0], root)
    n = named(root)
    slider = n['slider'][2]
    assert 'style:normal:bg_image' not in slider and slider['bar_size'] == '8'
    assert slider['style:normal:bg_color'] == ipod.NP_TRACK and slider['style:pressed:fg_color'] == ipod.NP_FILL
    for state in ('normal', 'pressed', 'over', 'disable', 'focused'):
        assert slider[f'style:{state}:round_radius'] == '4' and slider[f'style:{state}:border_color'] == '#00000000'
    for name in ('img0', 'img1'):
        assert 'image' not in n[name][2] and n[name][1] == [1, 2, 0, 0]
    assert n['other'][2]['image'] == 'keep' and n['other'][1] == [5, 6, 7, 8] and n['plain'][2]['image'] == 'vol_left'


def test_slider_plain_other_pages_stay():
    root = settings_page('vol_left', 'vol_right')
    before = copy.deepcopy(root)
    ipod.slider_plain('other_page.bin', root)
    assert root == before


def test_slider_plain_wants_two_endpoints(monkeypatch):
    monkeypatch.setattr(ipod, 'SETTINGS_SLIDERS', ('x',))
    with pytest.raises(ValueError, match=r'^x: unexpected slider endpoints$'):
        ipod.slider_plain('x', settings_page('vol_left'))


def test_sized_art(monkeypatch):
    calls = []
    monkeypatch.setattr(ipod, 'imagemagick', lambda *args, data: calls.append((args, data)) or b'png')
    assert ipod.sized_art(b'in', 52) == b'png'
    assert ipod.sized_art(b'in', (140, 100)) == b'png'
    assert calls == [(('png:-', '-filter', 'Lanczos', '-resize', '52x52!', '-background', 'none', '-gravity', 'center', '-extent', '52x52', *ipod.PNG_OUT), b'in'),
                     (('png:-', '-filter', 'Lanczos', '-resize', '100x100!', '-background', 'black', '-gravity', 'center', '-extent', '140x100', *ipod.PNG_OUT), b'in')]


STOCK_GRID_VALUES = dict(BOTTOM=290, PITCH=72, BODY=68, ART=52, ART_INSET=8, NP_ICONS_END=353,
                   NP_BAR_X=21, NP_BAR=[21, 253, 333, 4], NP_TIME_X=46)


def test_with_geometry_stock_grid_matches_module():
    assert vars(ipod.with_geometry(ipod.STOCK_GRID)) == STOCK_GRID_VALUES
    assert {n: getattr(ipod, n) for n in STOCK_GRID_VALUES} == STOCK_GRID_VALUES   # today's names still import
    assert ipod.STOCK_GRID == ipod.Geometry() and vars(ipod.IPOD) == STOCK_GRID_VALUES and ipod.STOCK_GRID.name == 'ipod'


def test_with_geometry_follows_the_bar():
    assert vars(ipod.with_geometry(ipod.Geometry(name='t', bar=26))) == dict(
        STOCK_GRID_VALUES, BOTTOM=294, NP_ICONS_END=350, NP_BAR_X=18, NP_BAR=[18, 253, 339, 4], NP_TIME_X=39)


def test_with_geometry_takes_the_row_sizes():
    ns = ipod.with_geometry(ipod.Geometry(name='t', pitch=64, body=60, art=48, art_inset=6))
    assert (ns.PITCH, ns.BODY, ns.ART, ns.ART_INSET, ns.BOTTOM) == (64, 60, 48, 6, 290)


T26 = ipod.Geometry(name='t', bar=26, pitch=64, body=60, art=52, art_inset=4)


def test_ipod_home_art_fills_the_client_height():
    root = home(); ipod.ipod_home(root, g=T26)
    assert root[3][0][1] == [0, 0, 375, 294]


def test_oled_home_rows_are_32():
    root = home(); ipod.ipod_home(root, g=OLED_SET); n = named(root)
    assert n['list_view_home'][1] == [0, ipod.HOME_TOP, 375, 7 * 32] and n['list_view_home'][2]['item_height'] == '32'
    rows = named(n['list_view_home'])
    assert [rows['btn_' + r][1] for r in ipod.HOME_ROWS] == [[0, 32 * i, 375, 32] for i in range(8)]
    assert named(n['list_view_home'])['scroll_view_home'][1] == [0, 0, 375, 7 * 32]  # seven at rest, the eighth scrolls in
    foot = 320 - OLED_SET.bar - ipod.HOME_TOP  # Settings' list runs on to the window's foot: its eighth row peeks
    assert n['list_view_homeset'][1] == [0, ipod.HOME_TOP, 375, foot] and n['list_view_homeset'][2]['item_height'] == '32'


def test_no_oled_home_list_ends_on_whole_rows_above_the_list_end():
    # The main list's box is its seven rows, which home_overflow runs on to the foot at run time; every other Home list
    # (Settings', Library's, Streaming's) is built reaching the window's foot, so a row past list_end peeks through the
    # fade (navigation.c list_fade) instead of being cut off at a whole row (a list cut there gets no fade at all).
    g = dataclasses.replace(OLED_SET, home_lib=1)
    root = home(); ipod.ipod_home(root, g=g); n = named(root)
    lists = [k for k in n if k.startswith('list_view_home') and k != 'list_view_home']
    assert lists == ['list_view_homeset', 'list_view_homelib', 'list_view_homestream']
    for k in lists:
        assert ipod.HOME_TOP + n[k][1][3] >= 320 - g.bar, k  # Library's and Streaming's are window-high, from HOME_TOP


def cut_page(h, name='list_view', y=0, **props):
    return w('a_page', w(name, kind='list_view', at=[0, y, 375, h], **props), kind='window')


@pytest.mark.parametrize('h, y, cut', [(224, 0, True), (192, 32, True), (225, 0, False), (262, 0, False),
                                       (100, 140, False)])
def test_cut_lists_names_a_list_ending_on_a_row_above_the_list_end(h, y, cut):
    """Any list: a box stopping at or above list_end (224 in OLED's window) ends its rows over a
    black band, unfaded (navigation.c list_fade leaves such a box alone); one running past it is the fade's."""
    data = ipod.encode(cut_page(h, y=y))
    assert ipod.cut_lists('a_page.bin', data, OLED_SET) == (['list_view'] if cut else [])


def test_cut_lists_skips_lists_it_cannot_see_end():
    page = w('a_page', w('box', w('table_view', kind='table_view', at=[0, 8, 375, 100]), at=[0, 40, 375, 108]),
             w('hidden', kind='list_view', at=[0, 0, 375, 100], visible='false'),
             w('laid', kind='list_view', at=[0, 0, 375, 100], self_layout='default(x=0,y=0,w=100%,h=100%)'), kind='window')
    assert ipod.cut_lists('a_page.bin', ipod.encode(page), OLED_SET) == ['table_view']  # its parent's y counts
    for path, name in ipod.UNFIT_LISTS:
        assert ipod.cut_lists(path, ipod.encode(cut_page(100, name)), OLED_SET) == []
    assert ipod.cut_lists(ipod.MODAL_PAGES[0], ipod.encode(cut_page(100)), OLED_SET) == []  # an inset menu's panel


def test_ui_pages_refuses_a_list_cut_above_the_list_end(monkeypatch):
    monkeypatch.setattr(ipod, 'UI_ASSETS', {'a_page.bin': ''})
    monkeypatch.setattr(ipod, 'STOCK_ROWS', {})
    monkeypatch.setattr(ipod, 'patch_asset', lambda rel, data, g=ipod.STOCK_GRID: data)
    for h, ok in ((224, False), (262, True)):
        cat = lambda p, h=h: ipod.encode(cut_page(h))
        if ok:
            assert ipod.ui_pages(cat, OLED_SET) == {'a_page.bin': cat('')}
        else:
            with pytest.raises(Exception, match='a_page.bin: list_view end above list_end under oled'):
                ipod.ui_pages(cat, OLED_SET)


def test_status_bar_takes_the_bar_height():
    root = bar(); ipod.status_bar(root, OLED_SET); n = named(root)
    assert [root[1][3], n['view_left'][1][3]] == [OLED_SET.bar] * 2
    assert n['label_idle'][1] == [-200, 0, 28, OLED_SET.bar]
    assert [n[x][1][3] for x in ipod.STATUS_HIDDEN] == [OLED_SET.bar] * 3


def test_status_bar_wants_the_stock_height():
    root = bar(); root[1][3] = 32
    with pytest.raises(ValueError, match='^Unexpected status bar height$'):
        ipod.status_bar(root, OLED_SET)


def test_fallback_scale_matches_the_faces():  # a face's fallback at its cap height: stb's scale, a font's ascent - descent its size
    from fontTools.ttLib import TTFont
    def cap(path):
        f = TTFont(path, lazy=True)
        return f['OS/2'].sCapHeight / (f['hhea'].ascent - f['hhea'].descent)
    # every face's, to the nearest point; stock's default.ttf: cap 183 of 299
    for face in ipod.FACES.values():
        if face.fallback:
            fallback = ipod.FACES[face.fallback]
            ratio = 100*cap(ipod.ROOT/'assets/fonts'/face.source)/(cap(ipod.ROOT/'assets/fonts'/fallback.source) if fallback.source else 183/299)
            assert abs(face.fallback_pct - ratio) < 1, face.name




def test_patch_asset_rows_and_lists_follow_the_geometry(monkeypatch):
    page = w('folder_page', w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', w('row', w('art', kind='image', at=[8, 9, 52, 52]), kind='list_item', at=[0, 0, 375, 78]),
               kind='list_view', at=[0, 50, 375, 240], default_item_height='78'), kind='window')
    data = ipod.encode(page)
    monkeypatch.setitem(ipod.UI_ASSETS, 'folder_page.bin', ipod.sha(data))
    n = named(ipod.decode(ipod.patch_asset('folder_page.bin', data, g=T26)))
    assert n['list_view'][1] == [0, 0, 375, 294] and n['list_view'][2]['default_item_height'] == '72'
    assert n['row'][1][3] == 72 and n['art'][1][1] == 4


# Streaming's rows are the grid's stream_row, OLED's 32 px. The list keeps the settings lists' box, on to the window's foot.
def test_stream_page_rows_per_set(monkeypatch):
    page = w('stream_page', w('view_navbar', at=[0, 0, 370, 50]),
             w('list_view', kind='list_view', at=[0, 50, 375, 240], default_item_height=str(ipod.SET_STOCK_ROW)), kind='window')
    data = ipod.encode(page)
    monkeypatch.setitem(ipod.UI_ASSETS, 'stream_page.bin', ipod.sha(data))
    assert OLED_SET.stream_row == 32
    n = named(ipod.decode(ipod.patch_asset('stream_page.bin', data, g=OLED_SET)))
    assert n['list_view'][2]['default_item_height'] == '32' and n['list_view'][1][3] == 320 - 58  # its rows past list_end fading


PAGE_T = ipod.Geometry(name='t', pitch=28, body=26, art_pitch=52, art_body=50, art=44, art_inset=3,
                       text_art_inset=5, title24_y=2, title_y=4, meta_y=6, count_y=6)


def list_page(name):
    row = w('row', w('button', w('gif', kind='image', at=[0, 9, 50, 52]),
                     w('title', kind='hscroll_label', at=[0, 11, 197, 20]), w('meta', at=[0, 40, 197, 16]),
                     kind='button', at=[20, 0, 335, 70]), kind='list_item', at=[0, 0, 375, 78])
    return w(name, w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', row, kind='list_view', at=[0, 50, 375, 240], default_item_height='78'), kind='window')


@pytest.mark.parametrize('path, kind', [('localmusic/albuminfo_page.bin', 'text'), ('localmusic/album_page.bin', 'art'),
                                        ('folder_page.bin', 'folder')])
def test_patch_asset_rows_by_page_kind(monkeypatch, path, kind):
    # A text page's rows take pitch/body, an art page's art_pitch/art_body, Folder's art_pitch/art_body while the
    # grid gives no folder_pitch; each moves its text to the grid's y.
    data = ipod.encode(list_page(path))
    monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    pitch, body, inset = (28, 26, 5) if kind == 'text' else (52, 50, 3)
    assert ipod.PAGE_KIND[path] == kind and ipod.page_rows(PAGE_T, kind) == (pitch, body, inset)
    n = named(ipod.decode(ipod.patch_asset(path, data, g=PAGE_T)))
    assert n['list_view'][2]['default_item_height'] == str(pitch) and n['row'][1][3] == pitch
    assert (n['button'][1][3], n['gif'][1][1], n['title'][1][1], n['meta'][1][1]) == (body, inset, 4, 6)


def test_folder_rows_are_their_own_kind():
    # A set's folder_pitch and folder_body, else its art rows'; the immediates follow.
    g = dataclasses.replace(PAGE_T, folder_pitch=32, folder_body=30)
    assert ipod.page_rows(g, 'folder') == (32, 30, 3) and ipod.page_rows(PAGE_T, 'folder') == (52, 50, 3)
    assert (ipod.immediate_values(g)['folder_pitch'], ipod.immediate_values(g)['folder_body']) == (32, 30)
    assert (ipod.immediate_values(PAGE_T)['folder_pitch'], ipod.immediate_values(PAGE_T)['folder_body']) == (52, 50)


def test_songs_rows_are_their_own_kind():
    assert ipod.PAGE_KIND['localmusic/allmusic_page.bin'] == 'song'
    assert ipod.page_rows(dataclasses.replace(PAGE_T, song_pitch=52, song_body=50), 'song') == (52, 50, 5)


def test_text_y():
    assert [ipod.text_y(ipod.STOCK_GRID, y) for y in (11, 14, 21, 40, 41)] == [10, 13, 20, 39, 40]
    assert [ipod.text_y(PAGE_T, y) for y in (11, 14, 40, 41)] == [4, 2, 6, 6]


STOCK_GRID_WORDS = {'lib_body': 68, 'lib_inset': 8, 'pitch': 72, 'body': 68, 'art': 52, 'art_inset': 8, 'text_art': 52, 'text_art_inset': 8, 'scroll': 240,
              'title24_y': 13, 'title_y': 10, 'meta_y': 39, 'count_y': 40}
SPLIT = ('pitch', 'body', 'title24_y', 'title_y', 'meta_y', 'count_y')


def test_immediates_every_site_once():
    sites = [a for g in ipod.AUDIT['immediates'] for a, _ in g['sites']]
    assert len(sites) == 100 and len(set(sites)) == 100


def test_immediate_values_stock_grid_are_the_baked_values():
    values = ipod.immediate_values(ipod.STOCK_GRID)
    assert set(values) == set(ipod.VALUE_NAMES)
    assert (values['np_bar_x'], values['np_bar_w']) == (ipod.NP_BAR[0], ipod.NP_BAR[2])
    assert ipod.VALUE_NAMES[0] == 'pitch'
    for name in ipod.VALUE_NAMES:
        if not name.startswith(('np_bar', 'stock_', 'search_')):
            assert values[name] == STOCK_GRID_WORDS[name if name in STOCK_GRID_WORDS else name.removeprefix('art_').removeprefix('song_').removeprefix('folder_')], name


def test_only_split_names_take_the_art_prefix():
    assert {n for n in ipod.VALUE_NAMES if n.startswith('art_')} - {'art_inset'} <= {'art_' + n for n in SPLIT}
    assert {n for n in ipod.VALUE_NAMES if n.startswith('song_')} <= {'song_' + n for n in SPLIT}


def test_geometry_has_every_value_name():
    assert set(ipod.VALUE_NAMES) - {'scroll', 'np_bar_x', 'np_bar_w'} <= {f.name for f in dataclasses.fields(ipod.Geometry)}


TEXT_SITES = {'0x4b3a80': 'pitch', '0x4b2a48': 'pitch', '0x4b3ab8': 'body', '0x4b3b64': 'text_art',
              '0x4ac6fc': 'text_art', '0x4afca4': 'text_art', '0x4ac774': 'text_art_inset', '0x4afd14': 'text_art_inset'}


def test_localclass_sites_are_text():
    # Artists, Composers and Genres (localclass_page), and the artwork of Songs and an artist's songs, follow
    # the text rows, so a set can make them 28 px text while Albums keep 52 px art rows.
    got = {a: g['value'] for g in ipod.AUDIT['immediates'] for a, _ in g['sites'] if a in TEXT_SITES}
    assert got == TEXT_SITES and ipod.PAGE_KIND['localmusic/localclass_page.bin'] == 'text'


UI = 'release/assets/default/raw/ui/'


@pytest.fixture
def fake_assets(monkeypatch):
    monkeypatch.setattr(ipod, 'UI_ASSETS', {'system_bar.bin': '', 'dialog/volume_dialog.bin': '', 'home_page.bin': ''})
    monkeypatch.setattr(ipod, 'STOCK_ROWS', {'list_page.bin': '', 'clock_page.bin': ''})  # a stock page the set leaves as it is is not written
    monkeypatch.setattr(ipod, 'patch_asset', lambda rel, data, g=ipod.STOCK_GRID: data + (b'!' if g.bar != 30 and rel not in ('home_page.bin', 'clock_page.bin') else b''))
    monkeypatch.setattr(ipod, 'cut_lists', lambda rel, data, g: [])  # the pages are not UI files


def test_ui_pages_are_every_ui_asset_and_the_stock_rows_the_set_changes(fake_assets):
    got = ipod.ui_pages(lambda p: p.encode(), ipod.Geometry(name='test', bar=26))
    assert got == {'system_bar.bin': (UI + 'system_bar.bin').encode() + b'!',
                   'dialog/volume_dialog.bin': (UI + 'dialog/volume_dialog.bin').encode() + b'!',
                   'home_page.bin': (UI + 'home_page.bin').encode(), 'list_page.bin': (UI + 'list_page.bin').encode() + b'!'}



# A set without home_lib (STOCK_GRID's, OLED's) keeps stock's bindings: Home's Library and Streaming rows are stock's own,
# and no hidden list of the Library's or Streaming's rows is built.
def test_home_lists_keep_stock_bindings():
    for g in (ipod.STOCK_GRID, OLED_SET):
        assert g.home_lib == 0
        root = home(); ipod.ipod_home(root, g=g); rows = named(root[3][1])
        assert 'list_view_homelib' not in named(root) and 'img_library' not in rows and 'img_localmusic' in rows
        # Streaming is Settings' row (2026-11-19), its click the payload's (coverflow.c coverflow_home)
        assert 'list_view_homestream' not in named(root) and 'img_stream' not in rows and 'img_set_stream' in named(root[3][2])

# Songs' action bar under OLED: a row of the set's action_row, the list and the empty panel under it, and stock's recreated
# scroll bar spans that (the payload shapes what stock builds in it, navigation.c action_row).
def test_songs_action_row(monkeypatch):
    path = 'localmusic/allmusic_page.bin'
    page = w('allmusic_page', w('view_navbar', at=[0, 0, 375, 50]), w('view_navbar_allplay', at=[0, 50, 375, 50]),
             w('view', at=[0, 100, 375, 193]), w('table_view', kind='table_view', at=[0, 100, 375, 190]), kind='window')
    data = ipod.encode(page); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=OLED_SET)))
    assert n['view_navbar_allplay'][1] == [0, 0, 375, 32] and n['table_view'][1] == [0, 32, 375, 230] and n['view'][1][1] == 32  # on to the window's foot (fit_rows)
    assert ipod.immediate_values(OLED_SET)['scroll'] == 230


def test_settings_lists_follow_the_set(monkeypatch):
    # Settings lists hold the grid's rows from its top: OLED's 32 px rows from 0, on to the window's foot.
    path = 'systemset/sysset_page.bin'
    page = w('sysset_page', w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', kind='list_view', at=[0, 50, 375, 240], default_item_height='78'), kind='window')
    data = ipod.encode(page); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=OLED_SET)))
    assert n['list_view'][1] == [0, 0, 375, 262] and n['list_view'][2]['default_item_height'] == '32' and 'item_height' not in n['list_view'][2]


# Stock's own list pages, each as its bin has it, under OLED: the rows of 78 take the approved pitch, by the one rule (stock_row), and the toolbars go.
STOCK_ROW_REVERSED = dataclasses.replace(OLED_SET, stock_row=78, stock_body=70, stock_title_y=11, stock_title_h=20)


def stock_list_page(name, *rows):
    return w(name, w('view_navbar', at=[0, 0, 375, 50]),
             w('list_view', w('scroll_view', *rows, kind='scroll_view', at=[0, 0, 375, 100]), kind='list_view', at=[0, 50, 375, 210], default_item_height='78'), kind='window')


def stock_table_page(name, *more):
    return w(name, w('view_navbar', at=[0, 0, 375, 50]),
             w('table_view', w('table_client', kind='table_client', at=[0, 0, 365, 100], row_height='78', self_layout='default(x=0,y=0,w=365,h=100%)'), kind='table_view', at=[0, 50, 375, 190]), *more, kind='window')


def audited_rows(monkeypatch, path, page):
    data = ipod.encode(page); monkeypatch.setitem(ipod.STOCK_ROWS, path, ipod.sha(data))
    return data


@pytest.mark.parametrize('path', ['playermore_page.bin', 'updatemusic_page.bin'])
def test_stock_lists_take_settings_rows(monkeypatch, path):
    label = w('hscroll_label_1', kind='hscroll_label', at=[10, 25, 266, 20])
    row = w('list_item_1', w('button_1', label, kind='button', at=[20, 0, 335, 70]), kind='list_item', at=[0, 0, 0, 78])
    data = audited_rows(monkeypatch, path, stock_list_page(path[:-4], row))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=OLED_SET)))
    assert n['view_navbar'][2]['visible'] == n['view_navbar'][2]['enable'] == 'false'
    assert n['list_view'][1] == [0, 0, 375, 262] and n['list_view'][2]['default_item_height'] == '32' and 'item_height' not in n['list_view'][2]
    assert n['hscroll_label_1'][1] == [10, 0, 266, 70]  # takes the row, whose font a 20 px box wouldn't hold
    assert 'anim_hint' not in ipod.decode(ipod.patch_asset(path, data, g=OLED_SET))[2]
    n = named(ipod.decode(ipod.patch_asset(path, data, g=STOCK_ROW_REVERSED)))  # the rule reversed: stock's pitch, the toolbar still hidden
    assert n['view_navbar'][2]['visible'] == 'false' and n['list_view'][2]['default_item_height'] == '78' and n['list_view'][1] == [0, 0, 375, 262]  # on to the window's foot


def test_queue_under_the_bar(monkeypatch):
    path = 'playerqueue_page.bin'
    data = audited_rows(monkeypatch, path, stock_table_page('playerqueue_page'))
    n = named(ipod.decode(ipod.patch_asset(path, data, g=OLED_SET)))
    assert n['view_navbar'][2]['visible'] == 'false' and n['table_view'][1] == [0, 0, 375, 262]
    assert n['table_client'][2]['row_height'] == '32' and n['table_client'][1][2] == 375


@pytest.mark.parametrize('path', ['playerjumpinfo_page.bin', 'specfolder_page.bin'])
def test_stock_tables_keep_their_toolbar_and_boxes(monkeypatch, path):
    page = stock_table_page(path[:-4], w('startscan_view', at=[0, 210, 375, 60]))
    data = audited_rows(monkeypatch, path, page)
    n = named(ipod.decode(ipod.patch_asset(path, data, g=OLED_SET)))
    assert 'visible' not in n['view_navbar'][2] and n['table_view'][1] == [0, 50, 375, 190] and n['startscan_view'][1] == [0, 210, 375, 60]
    assert n['table_client'][2]['row_height'] == '32'
    assert ipod.patch_asset(path, data, g=STOCK_ROW_REVERSED) == ipod.encode(ipod.decode(data))


def test_toolbar_switch_pages(monkeypatch):
    # The Sleep Timer's panel makes room for the row its switch becomes, and ends with the window. Crossfade's page keeps its toolbar.
    sleep, boot = 'systemset/sleepshutdown_page.bin', 'playset/bootvol_page.bin'
    pages = {sleep: w('sleepshutdown_page', w('view_navbar', at=[0, 0, 375, 50]), w('view_sleep', at=[0, 50, 375, 240]), kind='window'),
             boot: w('bootvol_page', w('view_navbar', at=[0, 0, 375, 50]), w('view_bootvol', at=[0, 50, 375, 240]),
                     w('slider', kind='slider', at=[50, 100, 275, 8]), w('a', kind='image', at=[0, 0, 50, 50], image='vol_left'), w('b', kind='image', at=[0, 0, 50, 50], image='vol_right'), kind='window')}
    for path, page in pages.items():
        monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(ipod.encode(page)))
    data = {path: ipod.encode(page) for path, page in pages.items()}
    n = named(ipod.decode(ipod.patch_asset(sleep, data[sleep], g=OLED_SET)))
    # under the switch's row, and cut to end at the window's foot (320 - 58 = 262), not 10 px past it
    assert n['view_navbar'][2]['visible'] == 'false' and n['view_sleep'][1] == [0, 32, 375, 262 - 32]
    assert 'visible' not in named(ipod.decode(ipod.patch_asset(boot, data[boot], g=OLED_SET)))['view_navbar'][2]


def test_search_rows_are_stocks_under_stock_grid():
    values, want = ipod.immediate_values(ipod.STOCK_GRID), {'search_row': 78, 'search_body': 70, 'search_art': 50, 'search_title_y': 11, 'search_title24_y': 14, 'search_title_h': 24, 'search_meta_y': 41, 'search_meta_h': 16}
    assert {n: values[n] for n in want} == want
    for group in ipod.AUDIT['immediates']:
        if group['value'] in want:
            assert all(int(word, 16) & 0xffff == want[group['value']] for _, word in group['sites']), group['value']
    assert (OLED_SET.search_row, OLED_SET.search_body, OLED_SET.search_art, OLED_SET.search_title_h, OLED_SET.search_meta_h) == (52, 52, 0, 28, 20)
    assert OLED_SET.search_title_y + OLED_SET.search_title_h <= OLED_SET.search_body and OLED_SET.search_meta_y + OLED_SET.search_meta_h <= OLED_SET.search_body


def test_stock_rows_are_stocks_under_stock_grid():
    values = ipod.immediate_values(ipod.STOCK_GRID)
    assert (values['stock_row'], values['stock_body'], values['stock_title_y'], values['stock_title_h']) == (78, 70, 11, 20)
    for group in ipod.AUDIT['immediates']:
        if group['value'] in ('stock_row', 'stock_body', 'stock_title_y', 'stock_title_h'):
            assert all(int(word, 16) & 0xffff == values[group['value']] for _, word in group['sites'])
    assert (OLED_SET.stock_row, OLED_SET.stock_body) == (32, 32)
    assert OLED_SET.stock_title_h >= theme.builtins()[0]['font_px[ROLE_TITLE]'] and OLED_SET.stock_title_y + OLED_SET.stock_title_h <= OLED_SET.stock_body  # the box holds the row's font


def test_lists_beside_settings_rows_take_item_height(monkeypatch):
    # A library list with the set's settings row height is told apart by its item_height (OLED's 32 px rows).
    path = 'localmusic/albuminfo_page.bin'
    data = ipod.encode(list_page(path)); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    assert named(ipod.decode(ipod.patch_asset(path, data, g=OLED_SET)))['list_view'][2]['item_height'] == '32'


def test_oled_playlists_list_takes_its_two_line_rows(monkeypatch):
    # OLED's Playlists rows are page_detail's 52 px (playlists.c pl_row): its list is pitched at playlist_pitch, holds
    # whole rows of it, and takes no item_height, which would force them to 32. Its 32 px text lists still take one.
    oled = next(theme.geometry(t) for t in theme.builtins() if t['id'] == 'oled')
    t = next(t for t in theme.builtins() if t['id'] == 'oled')
    assert ipod.page_rows(oled, 'playlist')[0] == t['page_detail'] == 52 != oled.song_pitch == oled.set_row
    for path in ('localmusic/playlist_page.bin', 'localmusic/albuminfo_page.bin'):
        data = ipod.encode(list_page(path)); monkeypatch.setitem(ipod.UI_ASSETS, path, ipod.sha(data))
    data = ipod.encode(list_page('localmusic/playlist_page.bin'))
    n = named(ipod.decode(ipod.patch_asset('localmusic/playlist_page.bin', data, g=oled)))
    assert n['list_view'][1] == [0, 0, 375, 262] and n['list_view'][2]['default_item_height'] == '52'
    assert 'item_height' not in n['list_view'][2] and n['row'][1][3] == 52
    unset = dataclasses.replace(oled, playlist_pitch=0)  # song_pitch's rows, the settings rows' height: told apart
    assert named(ipod.decode(ipod.patch_asset('localmusic/playlist_page.bin', data, g=unset)))['list_view'][2]['item_height'] == '32'
    data = ipod.encode(list_page('localmusic/albuminfo_page.bin'))
    assert named(ipod.decode(ipod.patch_asset('localmusic/albuminfo_page.bin', data, g=oled)))['list_view'][2]['item_height'] == '32'


def elf(size):
    """A little MIPS ELF: one LOAD segment, at 0x400000 from file offset 0x1000."""
    data = bytearray(0x1000 + size)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, 1)
    struct.pack_into('<8I', data, 52, 1, 0x1000, 0x400000, 0x400000, size, size, 5, 0x1000)
    return data


SYMBOLS = {'compact_set_row_layout': 0x40a000, 'ipod_list_layout': 0x40b000, 'ringnav_boot': 0x40c000,
           'stock_set_row_layout': 0x40d000, 'stock_row_text': 0x40e000, 'stock_row_style': 0x40f000}
# STOCK_GRID's word for each named immediate, as test_immediate_values_stock_grid_are_the_baked_values reads STOCK_GRID_WORDS.
# The stock_ and search_ rows' words are STOCK_GRID's fields, as immediate_values gives them; that test leaves them out too.
IMMEDIATES = ipod.immediate_values(ipod.STOCK_GRID) | {name: STOCK_GRID_WORDS[name if name in STOCK_GRID_WORDS else name.removeprefix('art_').removeprefix('song_').removeprefix('folder_')]
              for name in ipod.VALUE_NAMES if not name.startswith(('np_bar', 'stock_', 'search_'))} | {'np_bar_x': ipod.NP_BAR[0], 'np_bar_w': ipod.NP_BAR[2]}


def code_patches():
    """(address, stock word, patched word, purpose) for each word patch_code changes, in its order."""
    lookups = [(at + 4 * i, old, new, why) for at, slot in ((0x4a7ed4, 0x468), (0x4af83c, 0x468), (0x537fbc, 0x420))
               for i, (old, new, why) in enumerate(((0x8f99af04, ipod.CLOSE_LOAD, 'lookup unwind uses navigator_close'),
                                                    (0x0320f809, 0x0320f809, 'lookup close call'),
                                                    (0, 0x8fa40000 | slot, 'lookup close receives the top window name')))]
    immediates = [(int(at, 16), int(old, 16), int(old, 16) & 0xffff0000 | (IMMEDIATES[v] if isinstance(v := group['value'], str) else v),
                   group['purpose']) for group in ipod.AUDIT['immediates'] for at, old in group['sites']]
    rows = ipod.AUDIT['row_layout_calls']
    tiles = [site for icon, style in ((0x4b56c0, 0x4b5748), (0x4b57e8, 0x4b5874)) for site in (
        (icon, 0x24070032, 0x24070000, 'playlist tile icon width 0'),
        (icon + 0x54, 0x2407005b, 0x240700a4, 'playlist tile label spans the tile'),
        (icon + 0x60, 0x2405003f, 0x24050000, 'playlist tile label at the tile edge'),
        (style, 0x2505c434, 0x2505c990, 'playlist tile label centred'))]
    # toolsTimeItoa's two formats, back to back
    times = zip(struct.unpack('<7I', b'%02d:%02d:%02d\0\0%02d:%02d\0\0\0'), struct.unpack('<7I', b'%d:%02d:%02d\0\0\0\0%d:%02d\0\0\0\0\0'))
    return [*lookups, *immediates, *[(int(at, 16), int(old, 16), 0x0c102800, rows['purpose']) for at, old in rows['sites']],
            *[(int(at, 16), int(old, 16), jal, ipod.AUDIT[name]['purpose']) for name, jal in (('stock_row_layout_calls', 0x0c103400), ('stock_row_text_calls', 0x0c103800),
                                                                                         ('stock_row_style_calls', 0x0c103c00)) for at, old in ipod.AUDIT[name]['sites']],
            (0x532d3c, 0x0320f809, 0, 'folder rebind retains the width owned by its row layouter'),
            *[(int(at, 16), int(old, 16), ipod.DUMMY_LOAD, ipod.AUDIT['navbar_resize_calls']['purpose']) for at, old in ipod.AUDIT['navbar_resize_calls']['sites']], *tiles,
            (ipod.inc('LIST_VIEW_LAYOUT_SLOT'), ipod.inc('LIST_VIEW_LAYOUT'), 0x40b000, 'settings lists stack SET_ROW rows and map their children'),
            *[(0x78bba0 + 4 * i, old, new, 'times without a leading zero') for i, (old, new) in enumerate(times)],
            (0x534cf8, 0x0320f809, 0x0c103000, 'boot resume restores the queue paused and stays on Home'),
            (0x534c4c, 0x1440003c, 0, 'boot resume with Headphones Required on')]


def test_patch_code():
    patches = code_patches()
    assert len({at for at, *_ in patches}) == len(patches)
    data = elf(0x540000)
    for at, old, _, _ in patches:
        struct.pack_into('<I', data, at - 0x3ff000, old)
    assert ipod.patch_code(data, dict(SYMBOLS), ipod.STOCK_GRID) == [{'address': hex(at), 'original': hex(old), 'patched': hex(new), 'purpose': why}
                                                     for at, old, new, why in patches]
    assert [struct.unpack_from('<I', data, at - 0x3ff000)[0] for at, *_ in patches] == [new for _, _, new, _ in patches]


# Artist detail: stock's tabs carry literal Chinese text in every language; they take the string table's keys,
# and the Albums tab and its page (the pages' second) start active.
def artist_page():
    tabs = w('view_tabs', w('btn_track', kind='tab_button', text='单曲', style='s_tab'), w('btn_album', kind='tab_button', text='专辑'))
    return w('artistinfo_page', tabs, w('pages', w('view_songs'), w('view_albums'), kind='pages'))


def test_artist_tabs():
    root = artist_page()
    ipod.artist_tabs(root)
    n = named(root)
    assert n['btn_track'][2] == {'name': 'btn_track', 'tr_text': 'local_allsongs', 'style': 's_tab'}
    assert n['btn_album'][2] == {'name': 'btn_album', 'tr_text': 'album', 'value': 'true'}
    assert n['pages'][2] == {'name': 'pages', 'value': '1'} and n['pages'][3][1][2]['name'] == 'view_albums'
    assert n['view_tabs'][2] == {'name': 'view_tabs'} and n['view_albums'][2] == {'name': 'view_albums'}


@pytest.mark.parametrize('change,message', [
    (lambda n: n['btn_track'].__setitem__(0, 'button'), 'btn_track: unexpected tab'),
    (lambda n: n['btn_album'][2].update(text='Albums'), 'btn_album: unexpected tab'),
    (lambda n: n['pages'][2].update(value='0'), 'Unexpected artist pages'),
    (lambda n: n['pages'].__setitem__(0, 'view'), 'Unexpected artist tabs'),
    (lambda n: n['artistinfo_page'][3].pop(), 'Unexpected artist tabs'),
    (lambda n: n['view_tabs'][3].pop(0), 'Unexpected artist tabs'),
    (lambda n: n['view_tabs'][3].append(copy.deepcopy(n['btn_album'])), 'Unexpected artist tabs'),
])
def test_artist_tabs_wants_stock(change, message):
    root = artist_page()
    change(named(root))
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.artist_tabs(root)


def volume():
    return w('volume_dialog', w('slider_vol', kind='slider', value='30'), w('label_vol', kind='label', text='30'),
             kind='dialog', highlight='default(alpha=200)', anim_hint='popup')


def test_volume_dialog():
    # No highlighter, so nothing under the dialog dims; no border, which rings the screen in paper under Light Mode;
    # stock's controls hidden from the first paint, the payload draws the volume.
    root = volume()
    ipod.volume_dialog(root)
    assert root[2] == {'name': 'volume_dialog', 'anim_hint': 'popup',
                       **{f'style:{s}:border_color': '#00000000' for s in ('normal', 'disable', 'focused')}}
    assert [c[2] for c in root[3]] == [{'name': 'slider_vol', 'value': '30', 'visible': 'false'}, {'name': 'label_vol', 'text': '30', 'visible': 'false'}]
    assert [c[:2] for c in root[3]] == [c[:2] for c in volume()[3]]


@pytest.mark.parametrize('change,message', [
    (lambda r: r.__setitem__(0, 'window'), 'Unexpected volume dialog'),
    (lambda r: r[2].update(name='x'), 'Unexpected volume dialog'),
    (lambda r: r[2].update(highlight='default(alpha=100)'), 'Unexpected volume dialog'),
    (lambda r: r[2].pop('highlight'), 'Unexpected volume dialog'),
    (lambda r: r[3].reverse(), 'Unexpected volume controls'),
    (lambda r: r[3][0].__setitem__(0, 'image'), 'Unexpected volume controls'),
    (lambda r: r[3][1].__setitem__(0, 'hscroll_label'), 'Unexpected volume controls'),
    (lambda r: r[3][1][2].update(name='x'), 'Unexpected volume controls'),
    (lambda r: r[3].append(w('img_vol', kind='image')), 'Unexpected volume controls'),
])
def test_volume_dialog_wants_stock(change, message):
    root = volume()
    change(root)
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.volume_dialog(root)


def style_file(entries):
    """An AWTK style file: magic, version and count; a 100-byte entry per (widget, style, state), the offset of its
    properties, then its state, style and widget names in 32 bytes each; at that offset the properties' count, then
    each one's type, key length (its NUL counted), value size, key and value."""
    table, blocks, start = b'', b'', 12 + 100 * len(entries)
    for (widget, style, state), props in entries.items():
        table += struct.pack('<I32s32s32s', start + len(blocks), state.encode(), style.encode(), widget.encode())
        blocks += struct.pack('<I', len(props))
        for key, value in props.items():
            blocks += struct.pack('<BBH', 1, len(key) + 1, len(value)) + key.encode() + b'\0' + value
    return struct.pack('<3I', 0xfafbfcfd, 1, len(entries)) + table + blocks


def test_style_props():
    entries = {('button', 's_btn_listitem', 'normal'): {'bg_color': bytes.fromhex('2b2b2bff'), 'font_size': struct.pack('<I', 20)},
               ('label', 'default', 'focused'): {'text_color': bytes.fromhex('ffffffff')}, ('view', 'empty', 'normal'): {}}
    data = style_file(entries)
    got = list(ipod.style_props(data))
    assert [g[:4] + g[5:] for g in got] == [(*entry, key, value) for entry, props in entries.items() for key, value in props.items()]
    for *_, key, at, value in got:  # each offset its value's own bytes, after its key
        assert data[at - len(key) - 1:at + len(value)] == key.encode() + b'\0' + value


def test_style_props_wants_the_magic():
    data = bytearray(style_file({('button', 'default', 'normal'): {'bg_color': bytes(4)}}))
    data[0] ^= 1
    with pytest.raises(ValueError, match='^Unexpected AWTK style magic$'):
        list(ipod.style_props(bytes(data)))


GREY, RED, CLEAR, PRESSED = (bytes.fromhex(c) for c in ('2b2b2bff', '3d1920ff', '00000000', '333333ff'))
CLEAR_ROWS = ('button', 's_btn_listitem', 'bg_color', '2b2b2bff', '00000000', 2)


def list_styles(normal=GREY, pressed=RED, focused=GREY):
    # The edited style's bg_color in three states; the same value under another property, style or widget stays.
    return style_file({('button', 's_btn_listitem', 'normal'): {'bg_color': normal, 'text_color': GREY},
                       ('button', 's_btn_listitem', 'pressed'): {'bg_color': pressed},
                       ('button', 's_btn_listitem', 'focused'): {'bg_color': focused},
                       ('button', 'default', 'normal'): {'bg_color': GREY},
                       ('label', 's_btn_listitem', 'normal'): {'bg_color': GREY}})


def style_audit(*edits):
    return {'sha256': ipod.sha(list_styles()), 'edits': [list(e) for e in edits]}


def test_patch_style():
    assert ipod.patch_style(list_styles(), style_audit(CLEAR_ROWS)) == list_styles(normal=CLEAR, focused=CLEAR)
    pressed = ('button', 's_btn_listitem', 'bg_color', '3d1920ff', '333333ff', 1)
    assert ipod.patch_style(list_styles(), style_audit(CLEAR_ROWS, pressed)) == list_styles(CLEAR, PRESSED, CLEAR)


def test_patch_style_takes_one_state_and_then_the_rest():
    # stock V1.36's rows: every state grey. An edit that names a state changes that one; the next sees what is left.
    def audit(*edits): return {'sha256': ipod.sha(list_styles(pressed=GREY)), 'edits': [list(e) for e in edits]}
    one = ('button', 's_btn_listitem', 'bg_color', '2b2b2bff', '333333ff', 1, 'pressed')
    assert ipod.patch_style(list_styles(pressed=GREY), audit(one)) == list_styles(pressed=PRESSED)
    assert ipod.patch_style(list_styles(pressed=GREY), audit(one, CLEAR_ROWS)) == list_styles(CLEAR, PRESSED, CLEAR)
    with pytest.raises(ValueError, match='^s_btn_listitem.bg_color: expected 1 states with 2b2b2bff, found 0$'):
        ipod.patch_style(list_styles(pressed=GREY), audit(one[:6] + ('over',)))
    with pytest.raises(ValueError, match='^s_btn_listitem.bg_color: expected 2 states with 2b2b2bff, found 3$'):
        ipod.patch_style(list_styles(pressed=GREY), audit(CLEAR_ROWS))


@pytest.mark.parametrize('edit,message', [
    (CLEAR_ROWS[:4] + ('000000', 2), 's_btn_listitem.bg_color: edit changes size'),
    (CLEAR_ROWS[:5] + (3,), 's_btn_listitem.bg_color: expected 3 states with 2b2b2bff, found 2'),
    (CLEAR_ROWS[:5] + (1,), 's_btn_listitem.bg_color: expected 1 states with 2b2b2bff, found 2'),
    (('button', 's_btn_listitem', 'bg_color', 'ffffffff', '00000000', 1), 's_btn_listitem.bg_color: expected 1 states with ffffffff, found 0'),
    (('button', 's_btn_listitem', 'border_color', '2b2b2bff', '00000000', 1), 's_btn_listitem.border_color: expected 1 states with 2b2b2bff, found 0'),
])
def test_patch_style_wants_each_edit_as_audited(edit, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        ipod.patch_style(list_styles(), style_audit(edit))


def test_patch_style_is_audited():
    with pytest.raises(ValueError, match='^Unaudited style file$'):
        ipod.patch_style(list_styles(pressed=GREY), style_audit(CLEAR_ROWS))


def png(width, height, depth=8, colour=6):
    """A whole PNG, every sample 0: the signature, IHDR, one IDAT and IEND."""
    def chunk(kind, body):
        return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    row = 1 + (width * {0: 1, 2: 3, 4: 2, 6: 4}[colour] * depth + 7) // 8  # a filter byte, then the samples
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, depth, colour, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(bytes(row * height))) + chunk(b'IEND', b''))


@pytest.mark.parametrize('header', [(52, 52, 8, 6), (40, 3, 8, 2), (1, 7, 16, 0), (300, 2, 16, 4)])
def test_png_header(header):
    assert ipod.png_header(png(*header)) == header


@pytest.mark.parametrize('data', [b'', b'\xff\xd8\xff\xe0' + bytes(30), b'\x89PNG\r\n\x1a\r' + png(4, 4)[8:],
                                  png(4, 4)[:12] + b'IDAT' + png(4, 4)[16:]])  # IHDR must come first
def test_png_header_wants_a_png(data):
    with pytest.raises(ValueError, match='^Not a PNG$'):
        ipod.png_header(data)


@pytest.mark.parametrize('found,tool', [({'magick': '/usr/bin/magick', 'convert': '/usr/bin/convert'}, '/usr/bin/magick'),
                                        ({'convert': '/usr/bin/convert'}, '/usr/bin/convert')])
def test_imagemagick_runs_magick_or_convert(monkeypatch, found, tool):
    # ImageMagick 7's magick, else 6's convert: the arguments after it, the input on stdin, its stdout the result, a failure raised.
    runs = []
    monkeypatch.setattr(shutil, 'which', lambda name: found.get(name))
    monkeypatch.setattr(subprocess, 'run', lambda args, **kw: runs.append((args, kw)) or subprocess.CompletedProcess(args, 0, stdout=b'out'))
    assert ipod.imagemagick('png:-', '-trim', 'png:-', data=b'in') == b'out'
    (args, kw), = runs
    assert args == [tool, 'png:-', '-trim', 'png:-'] and kw == {'input': b'in', 'stdout': subprocess.PIPE, 'check': True}


def test_imagemagick_is_required(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kw: pytest.fail('ran without ImageMagick'))
    with pytest.raises(ValueError, match=r'^ImageMagick \(magick or convert\) is required for the iPod settings icons$'):
        ipod.imagemagick('-version', data=b'')


class Magick:
    """Stands in for ipod.imagemagick: keeps each call's arguments and input, and answers reply(args, data)."""
    def __init__(self, reply=lambda args, data: b'png'):
        self.calls, self.reply = [], reply

    def __call__(self, *args, data):
        self.calls.append((args, data))
        return self.reply(args, data)


@pytest.fixture
def magick(monkeypatch):
    m = Magick()
    monkeypatch.setattr(ipod, 'imagemagick', m)
    return m


def status_drawn(monkeypatch, magick, name):
    """name's icon made from its audited stock bytes: its -draw script, ImageMagick's arguments and input."""
    stock = b'stock ' + name.encode()
    monkeypatch.setitem(ipod.STATUS_ICONS, name, ipod.sha(stock))
    assert ipod.status_icon(name, stock) == b'png'
    args, data = magick.calls.pop()
    return args[args.index('-draw') + 1], args, data


def icon_size(args):
    """The width and height the icon is shrunk to."""
    width, height = args[args.index('-resize') + 1].removesuffix('!').split('x')
    return int(width), int(height)


@pytest.mark.parametrize('name', ['bar_wifi0.png', 'bar_wifi4.png', 'bar_bt.png', 'bar_btcon.png', 'bar_play.png', 'bar_pause.png'])
def test_status_icon_drawn_at_4x(monkeypatch, magick, name):
    # Drawn on a clear canvas four times the bar's 16 px icon, then shrunk to it: nothing of stock's image is kept.
    draw, args, data = status_drawn(monkeypatch, magick, name)
    width, height = icon_size(args)
    assert height == 16 and args[:3] == ('-size', f'{4 * width}x{4 * height}', 'xc:none') and draw.startswith('scale 4,4 ') and data == b''
    assert args[-len(ipod.PNG_OUT):] == tuple(ipod.PNG_OUT)


def wifi_inks(monkeypatch, magick, level):
    """The dot's colour, then its three arcs' from the innermost out."""
    draw, _, _ = status_drawn(monkeypatch, magick, f'bar_wifi{level}.png')
    arcs = sorted((float(r), colour) for colour, r in re.findall(r"stroke '([^']+)' path 'M[\d.,]+ A([\d.]+)", draw))
    dot, = re.findall(r"fill '([^']+)' circle", draw)
    return [dot] + [colour for _, colour in arcs]


@pytest.mark.parametrize('level', range(5))
def test_status_icon_wifi_lights_from_the_dot_out(monkeypatch, magick, level):
    # A signal level lights that many of the dot and its three arcs, innermost first; the rest are unlit.
    (lit,), (unlit,) = set(wifi_inks(monkeypatch, magick, 4)), set(wifi_inks(monkeypatch, magick, 0))
    assert lit != unlit and wifi_inks(monkeypatch, magick, level) == [lit] * level + [unlit] * (4 - level)


def test_status_icon_bluetooth(monkeypatch, magick):
    # Lit as Wi-Fi's ink when connected, unlit when not; its ink, round caps and all, in the canvas's last BT_REACH
    # columns, as stock's.
    (lit,), (unlit,) = set(wifi_inks(monkeypatch, magick, 4)), set(wifi_inks(monkeypatch, magick, 0))
    for name, colour in (('bar_btcon.png', lit), ('bar_bt.png', unlit)):
        draw, args, _ = status_drawn(monkeypatch, magick, name)
        (stroke, path), = re.findall(r"stroke '([^']+)' path '([^']+)'", draw)
        (width,) = re.findall(r'stroke-width ([\d.]+)', draw)
        xs, half, canvas = [float(x) for x in re.findall(r'[ML]([\d.]+),', path)], float(width) / 2, icon_size(args)[0]
        assert stroke == colour and canvas - ipod.inc('BT_REACH') <= min(xs) - half and max(xs) + half <= canvas, name


def test_status_icon_play_and_pause(monkeypatch, magick):
    play, _, _ = status_drawn(monkeypatch, magick, 'bar_play.png')
    (path,) = re.findall(r"path '([^']+)'", play)
    assert 'fill white' in play and len(re.findall(r'[ML]', path)) == 3 and path.endswith('Z')  # a filled triangle
    pause, _, _ = status_drawn(monkeypatch, magick, 'bar_pause.png')
    bars = re.findall(r'M([\d.]+),([\d.]+) L([\d.]+),([\d.]+)', pause)
    assert len(bars) == 2 and all(x0 == x1 and y0 != y1 for x0, y0, x1, y1 in bars)  # two upright bars


@pytest.mark.parametrize('name,data', [('bar_wifi2.png', b'other'), ('bar_other.png', b'stock')])
def test_status_icon_is_audited(monkeypatch, magick, name, data):
    monkeypatch.setitem(ipod.STATUS_ICONS, 'bar_wifi2.png', ipod.sha(b'stock'))
    with pytest.raises(ValueError, match=f'^{name}: unaudited status icon$'):
        ipod.status_icon(name, data)
    assert magick.calls == []


def test_row_icon(magick):
    assert ipod.row_icon(b'in') == b'png' and ipod.row_icon(b'in', 48, sharp=True) == b'png'
    head = ('png:-', '-alpha', 'on', '-filter', 'Lanczos', '-resize')
    (plain, _), (sharp, data) = magick.calls
    assert plain == (*head, f'{ipod.SET_ICON}x{ipod.SET_ICON}!', *ipod.PNG_OUT) and data == b'in'
    assert sharp[:len(head) + 1] == (*head, '48x48!') and sharp[len(head) + 1] == '-unsharp' and sharp[-len(ipod.PNG_OUT):] == tuple(ipod.PNG_OUT)  # sharpened after the shrink


STOCK_ICON, SMALL_ICON = png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON), png(ipod.SET_ICON, ipod.SET_ICON)


def settings_magick(monkeypatch, stock=STOCK_ICON, out=SMALL_ICON, opaque=(b'False', b'False')):
    """playset_eq.png audited as stock; ImageMagick's resize gives out, its %[opaque] opaque[0] for stock and opaque[1] for out."""
    monkeypatch.setitem(ipod.SETTINGS_ICONS, 'playset_eq.png', ipod.sha(stock))
    m = Magick(lambda args, data: (opaque[0] if data == stock else opaque[1]) if '%[opaque]' in args else out)
    monkeypatch.setattr(ipod, 'imagemagick', m)
    return m


def test_settings_icon(monkeypatch):
    m = settings_magick(monkeypatch)
    assert ipod.settings_icon('playset_eq.png', STOCK_ICON) == SMALL_ICON
    (resize, data), *opaque = m.calls
    assert data == STOCK_ICON and f'{ipod.SET_ICON}x{ipod.SET_ICON}!' in resize  # row_icon's, to SET_ICON
    assert [d for _, d in opaque] == [STOCK_ICON, SMALL_ICON]  # the transparency, before and after


@pytest.mark.parametrize('name,data', [('playset_eq.png', STOCK_ICON + b'\0'), ('other.png', STOCK_ICON)])
def test_settings_icon_is_audited(monkeypatch, name, data):
    m = settings_magick(monkeypatch)
    with pytest.raises(ValueError, match=f'^{name}: unaudited settings icon$'):
        ipod.settings_icon(name, data)
    assert m.calls == []


@pytest.mark.parametrize('stock', [png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON, colour=2), png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON + 1),
                                   png(ipod.SET_STOCK_ICON - 1, ipod.SET_STOCK_ICON), png(ipod.SET_STOCK_ICON, ipod.SET_STOCK_ICON, depth=16)])
def test_settings_icon_wants_stock_rgba(monkeypatch, stock):
    m = settings_magick(monkeypatch, stock=stock)
    with pytest.raises(ValueError, match='^playset_eq.png: unexpected stock icon format$'):
        ipod.settings_icon('playset_eq.png', stock)
    assert m.calls == []


@pytest.mark.parametrize('out', [png(ipod.SET_ICON + 1, ipod.SET_ICON), png(ipod.SET_ICON, ipod.SET_ICON, colour=4),
                                 png(ipod.SET_ICON, ipod.SET_ICON, colour=2), png(ipod.SET_ICON, ipod.SET_ICON, depth=16)])
def test_settings_icon_wants_set_icon_rgba(monkeypatch, out):
    settings_magick(monkeypatch, out=out)
    with pytest.raises(ValueError, match=f'^playset_eq.png: filtered icon is not {ipod.SET_ICON}px RGBA$'):
        ipod.settings_icon('playset_eq.png', STOCK_ICON)


def test_settings_icon_keeps_the_transparency(monkeypatch):
    settings_magick(monkeypatch, opaque=(b'False', b'True'))
    with pytest.raises(ValueError, match='^playset_eq.png: filtering changed the transparency$'):
        ipod.settings_icon('playset_eq.png', STOCK_ICON)


def test_quiet_icon(monkeypatch, magick):
    # Stock's control icons trimmed, shrunk into a square, one flat grey (More's pressed state darker), centred on a
    # larger square.
    fills = {}
    for name in ('play_more.png', 'play_fav.png', 'play_moredown.png'):
        stock = b'stock ' + name.encode()
        monkeypatch.setitem(ipod.QUIET_ICONS, name, ipod.sha(stock))
        assert ipod.quiet_icon(name, stock) == b'png'
        (args, data), = magick.calls; magick.calls.clear()
        (w, h), (ew, eh) = (map(int, args[args.index(k) + 1].split('x')) for k in ('-resize', '-extent'))
        assert data == stock and w == h and ew == eh > w, name
        assert args.index('-trim') < args.index('-resize') < args.index('-extent') and args[-len(ipod.PNG_OUT):] == tuple(ipod.PNG_OUT)
        assert args[args.index('-colorize') + 1] == '100' and args[args.index('-gravity') + 1] == 'center', name
        assert args[args.index('-background') + 1] == 'none', name  # on a clear square
        fills[name] = args[args.index('-fill') + 1]
    grey = lambda c: int(c.lstrip('#')[:2], 16)
    assert fills['play_more.png'] == fills['play_fav.png'] and grey(fills['play_moredown.png']) < grey(fills['play_more.png'])


def test_quiet_icon_is_audited(monkeypatch, magick):
    monkeypatch.setitem(ipod.QUIET_ICONS, 'play_fav.png', ipod.sha(b'stock'))
    with pytest.raises(ValueError, match='^play_fav.png: unaudited control icon$'):
        ipod.quiet_icon('play_fav.png', b'other')
    assert magick.calls == []


# MIPS branches count words from the delay slot, at + 4, in a signed 16-bit offset.
@pytest.mark.parametrize('op,at,target,word', [
    (0x10000000, 0x400000, 0x400004, 0x10000000),
    (0x10000000, 0x400000, 0x400000, 0x1000ffff),  # b . (a branch to itself)
    (0x10000000, 0x400000, 0x400010, 0x10000003),
    (0x04110000, 0x400010, 0x400000, 0x0411fffb),
    (0x10000000, 0x400000, 0x400004 + 4 * 32767, 0x10007fff),
    (0x04110000, 0x400000, 0x400004 - 4 * 32768, 0x04118000),
])
def test_branch(op, at, target, word):
    assert ipod.branch(op, at, target) == word


@pytest.mark.parametrize('target', [0x400004 + 4 * 32768, 0x400004 - 4 * 32769])
def test_branch_out_of_range(target):
    with pytest.raises(ValueError, match='^0x400000: branch out of range$'):
        ipod.branch(0x10000000, 0x400000, target)


def test_artist_albums_branch():
    # The artist page's init: stock's bal builds the Songs view (0x4af8ac); the patch's calls the Albums tab handler (0x4aeb1c).
    at, stock, patched = ipod.ARTIST_ALBUMS[1]
    assert (stock, patched) == (ipod.branch(0x04110000, at, 0x4af8ac), ipod.branch(0x04110000, at, 0x4aeb1c))


def program(*words, base=0x400000):
    """A MIPS ELF with one LOAD segment: words at base, from file offset 0x100."""
    data = bytearray(0x100) + struct.pack(f'<{len(words)}I', *words)
    struct.pack_into('<I', data, 28, 52)  # e_phoff
    struct.pack_into('<HH', data, 42, 32, 1)  # e_phentsize, e_phnum
    struct.pack_into('<8I', data, 52, 1, 0x100, base, base, 4 * len(words), 4 * len(words), 5, 0x1000)
    return data


def test_patch_word():
    data, changes = program(0x0320f809, 0x0411fc39, 0), []
    ipod.patch_word(data, 0x400004, 0x0411fc39, 0x0411f705, 'artist detail opens on Albums', changes)
    assert data == program(0x0320f809, 0x0411f705, 0)
    assert changes == [{'address': '0x400004', 'original': '0x411fc39', 'patched': '0x411f705', 'purpose': 'artist detail opens on Albums'}]
    ipod.patch_word(data, 0x400008, 0, 0x27bdffe0, 'not recorded')
    assert data == program(0x0320f809, 0x0411f705, 0x27bdffe0) and len(changes) == 1


def test_patch_word_wants_the_stock_instruction():
    # the message names what the word patches, not its address alone, so a stock rebase that moved it is easy to place
    data, changes = program(0x0320f809, 0x0411fc39), []
    with pytest.raises(ValueError, match=r'^0x400004: unexpected instruction \(LDAC HQ sends 990 kbps\)$'):
        ipod.patch_word(data, 0x400004, 0x0320f809, 0, 'LDAC HQ sends 990 kbps', changes)
    assert data == program(0x0320f809, 0x0411fc39) and changes == []


def test_status_row_names_the_page_inside_the_glass_s_top_curve():
    """A set's status row (OLED's): the bar black and 58 px, its title and clock sharing one box whose text starts
    STATUS_ROW_INSET in, in Text Secondary at bar_px, centred on status_y; the battery ending STATUS_ROW_INSET from the
    edge, not on the list's columns (the payload's slots left of it, status_row.c); the play state, Bluetooth, Wi-Fi
    and EQ off-screen, still there for stock."""
    g = dataclasses.replace(ipod.STOCK_GRID, bar=58, status_y=23, text_x=24, right_x=28, bar_px=18)
    root = bar()
    ipod.status_bar(root, g)
    n = named(root)
    left, right = root[3][:2]
    assert left[3] == [] and [c[2]['name'] for c in right[3]] == ipod.STATUS_ROW_RIGHT == ['label_battery', 'view_battery', 'img_battery']
    assert 'img_keylock' not in n
    assert ipod.inc('STATUS_ROW_INSET') == 62
    assert right[2]['children_layout'] == f'default(c=3,xm=62,s={ipod.STATUS_ROW_GAP})' and right[1][1::2] == [15, 16]
    for name in ('label_title', 'label_clock'):
        assert n[name][1] == [60, 11, ipod.STATUS_ROW_TITLE_W, 24]
        assert {n[name][2][f'style:{s}:{k}'] for s in STATES for k in ('font_size', 'text_color')} == {'18', '#ABABAB'}
    assert all(n[k][1][0] == -200 for k in ('img_state', 'img_bt', 'img_wifi', 'label_eq', 'label_idle', *ipod.STATUS_HIDDEN))
    assert root[1][3] == 58


def test_status_row_refuses_an_inset_under_the_glass():
    """Its ends clear the glass at the line box's top: a row so high that the curve reaches past the inset fails."""
    g = dataclasses.replace(ipod.STOCK_GRID, bar=58, status_y=8, text_x=24, right_x=28, bar_px=18)
    with pytest.raises(Exception, match='Status row inset 62 is under the glass at y -4'):
        ipod.status_bar(bar(), g)


@pytest.mark.parametrize('pitch, top', [(32, 0), (52, 0), (32, 50)])
def test_fit_rows_runs_a_list_on_to_the_window_s_foot(pitch, top):
    g = dataclasses.replace(ipod.STOCK_GRID, list_end=224)
    assert ipod.fit_rows(g, top, pitch) == 320 - g.bar - top  # whatever the pitch: the rows past list_end fade


def test_full_bleed_moves_a_window_content_under_the_status_row():
    """Every page is full-bleed (tools/build.py FULL_BLEED): its top-level children move down the bar, so each rests
    where it did when pages began under the status row; self_layout ones in their params."""
    page = ['window', [0, 0, 375, 262], {'name': 'p'}, [
        ['view', [0, 0, 375, 100], {'name': 'a'}, [['label', [4, 6, 10, 10], {'name': 'inner'}, []]]],
        ['image', [0, 0, 100, 100], {'name': 'b', 'self_layout': 'default(x=0,y=0,w=100%,h=100%)'}, []],
        ['label', [0, 60, 260, 32], {'name': 'c', 'self_layout': 'default(x=c,y=60,w=260,h=32)'}, []]]]
    out = ipod.decode(ipod.full_bleed(ipod.encode(page), 58))
    a, b, c = out[3]
    assert a[1] == [0, 58, 375, 100] and a[3][0][1] == [4, 6, 10, 10]  # only the window's own children move
    assert b[2]['self_layout'] == 'default(x=0,y=58,w=100%,h=-58)'
    assert c[2]['self_layout'] == 'default(x=c,y=118,w=260,h=32)'
    dialog = ['dialog', [0, 0, 375, 320], {'name': 'd'}, [['view', [0, 0, 10, 10], {}, []]]]
    assert ipod.full_bleed(ipod.encode(dialog), 58) == ipod.encode(dialog)
