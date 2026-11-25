"""tools/art.py: the theme images it draws, from the shipped theme's values."""
import ast
import math
import pathlib
import re
import struct
import sys
import zlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[2]/'tools'))
import art
import theme


def pixels(data):
    w, h = struct.unpack('>II', data[16:24])
    idat = data.index(b'IDAT')
    raw = zlib.decompress(data[idat + 4:idat + 4 + struct.unpack('>I', data[idat - 4:idat])[0]])
    return w, h, lambda x, y: tuple(raw[y * (4 * w + 1) + 1 + 4 * x + i] for i in range(4))


def test_sort_is_three_white_bars():
    w, h, at = pixels(art.images(theme.builtins()[0])['sort_bars'])
    assert (w, h) == (16, 12)
    for y in range(h):
        ink = [x for x in range(w) if at(x, y) == (255, 255, 255, 255)]
        assert ink == list(range({0: 16, 1: 16, 5: 11, 6: 11, 10: 6, 11: 6}.get(y, 0))), y
        assert all(at(x, y)[3] == 0 for x in range(w) if x not in ink), y


def test_path_reads_arcs_and_further_subpaths():
    polys = art.path('M0 0H10V10H0ZM20 0H30V10H20Z')
    assert len(polys) == 2 and polys[1][0] == (20, 0)
    arc = art.path('M0 0A5 5 0 1 1 10 0Z')[0]
    assert min(y for _, y in arc) >= -5.01 and max(abs(x - 5) for x, _ in arc) <= 5.01 and len(arc) > 40
    assert art.coverage([[(0, 0), (2, 0), (2, 2), (0, 2)]], (0, 0, 3, 1)) == [1.0, 1.0, 0.0]
    # a span inside another adds nothing: the union, not a sum
    assert art.coverage([[(0, 0), (3, 0), (3, 1), (0, 1)], [(1, 0), (1.5, 0), (1.5, 1), (1, 1)]], (0, 0, 3, 1)) == [1.0, 1.0, 1.0]


def test_path_s_mirrors_the_last_curves_control_point():
    """S (Phosphor's guitar has one) is a C whose first control is the previous C's second mirrored through the current
    point, or the current point itself after anything else; relative s counts from the current point."""
    assert art.path('M0 0C0 10 10 10 10 0S20 -10 20 0Z') == art.path('M0 0C0 10 10 10 10 0C10 -10 20 -10 20 0Z')
    assert art.path('M0 0C0 10 10 10 10 0s10 -10 10 0Z') == art.path('M0 0C0 10 10 10 10 0C10 -10 20 -10 20 0Z')
    assert art.path('M0 0L10 0S20 10 20 0Z') == art.path('M0 0L10 0C10 0 20 10 20 0Z')


@pytest.mark.parametrize('rx, ry, sweep', [(1, 1, 1), (1, 2, 1), (2, 1, 0)])
def test_path_arc_too_small_for_its_chord_grows_to_fit(rx, ry, sweep):
    # SVG's rule (F.6.6): radii that can't span the ends scale up, in proportion, until they just do: half an
    # ellipse about the chord's middle, on the side sweep says (1: the positive angles, up in SVG's y-down space)
    arc, = art.path(f'M0 0A{rx} {ry} 0 0 {sweep} 10 0')
    rx, ry = 5, 5 * ry / rx  # the chord is 10 along x
    assert math.dist(arc[-1], (10, 0)) < 1e-9
    assert all(abs(((x - 5) / rx)**2 + (y / ry)**2 - 1) < 1e-9 for x, y in arc)
    assert all(y * (1 if sweep else -1) < 1e-9 for _, y in arc) and abs(max(abs(y) for _, y in arc) - ry) < 1e-9


def test_gradient_holds_the_last_stops_colour_past_it():
    g = art.gradient(((0, 0x000000), (50, 0x2080ff)), 10, 20)  # as an SVG linearGradient's: its end pads
    assert g(10) == 0 and g(15) == g(17.5) == g(20) == g(30) == 0x2080ff


def test_gradient_holds_the_first_stops_colour_before_it():
    g = art.gradient(((50, 0x204060), (100, 0xffffff)), 0, 100)
    assert g(10) == g(50) == 0x204060


def test_the_round_corners_cover_their_arcs():
    for r in (2, 3, 4, 5, 9, 16, 21):  # patch/corner_cover.c makes any radius; test/fw/test_corners.py holds it to these
        cover = art.corner_cover(r)
        assert len(cover) == r * r and cover == bytes(cover[x * r + y] for y in range(r) for x in range(r))  # about its diagonal
        assert cover[0] == {2: 81, 3: 6}.get(r, 0) and cover[-1] == 255  # the small corners' outer pixels keep some cover
        n = 64  # a finer grid than corner_cover's: the arc's own cover, its sampling apart
        for y in range(r):
            for x in range(r):
                inside = sum(math.hypot(r - x - (i + .5) / n, r - y - (j + .5) / n) <= r for i in range(n) for j in range(n))
                assert abs(cover[y * r + x] - inside * 255 / n ** 2) <= 16, (r, x, y)


# A whole theme renders in about 3 s plain and about 9 s under coverage (its pixel loops are traced); this test needs
# four distinct renders (36 s under coverage), so the 60 s default leaves no room when other suites share the CPUs.
@pytest.mark.timeout(110)
def test_a_theme_drawing_anothers_art_shares_its_images(monkeypatch):
    """A theme stating art="oled" draws OLED's images, by OLED's names and bytes, packaged once; a value that would
    draw them otherwise stops the build, as the payload loads one image per name."""
    render, rendered = art.images, {}  # one render for each distinct theme, whichever call asks for it

    def once(t):
        if (key := repr(t)) not in rendered:
            rendered[key] = render(t)
        return rendered[key]

    monkeypatch.setattr(art, 'images', once)
    oled = theme.builtins()[0]
    other = {**oled, 'id': 'other'}  # OLED's values under another id, as a theme extending it resolves
    mine = art.images(oled)
    assert set(art.images({**other, 'art_id': 'other'})) == {n.replace('oled_', 'other_') for n in mine}
    assert art.theme_images([oled, other]) == mine  # its art_id, OLED's: the same names and bytes
    with pytest.raises(ValueError, match="other: oled_mode_shuffle_on differs from oled's, whose art it draws by name"):
        art.theme_images([oled, {**other, 'schemes': [*oled['schemes'][:9], {**oled['schemes'][9], 'tones': (0x123456,) * 5}]}])


def test_oled_draws_its_glyphs_from_phosphor_in_each_halfs_ink():
    """OLED's own images: the caret in Chevron grey (each half's) and white for the selection, the play state in each
    half's ink, Now Playing's shuffle and repeat in Text Secondary (each half's) and Accent Blue when on, the Shuffle row's
    glyph (regular) in Accent Blue and white for the selection, Folder view's folder (regular) in Text Secondary (each
    half's) and white for the selection, a disabled row's caret in Text Tertiary (each half's), the pickers' check
    (bold) in Accent Blue and white for the selection, the Wi-Fi page's signal and lock (regular) in Text Primary
    (each half's) and its failed join's warning-circle in Negative (each half's) and white, the shuffle confirmation's glyph (bold) in Accent Blue, Now Playing's heart and More (regular) in
    Text Primary (each half's), the heart 34 px and its full one (fill) in Text Primary too, More pressed in Text Secondary, and the
    Ratings rows' filled and outlined stars (14 px, and white on the bar), Now Playing's rating page's stars (filled and outlined, 44 px and the
    wheel's 52 px target) in Text Primary (each half's), and Streaming's service marks in the folder's inks, all from the vendored
    Phosphor SVGs but TIDAL's mark, drawn in art.py, and Subsonic's, q2-coda's own SVG, and the menu sheet's symbols
    (regular, 19 px) in Text Secondary (each half's) and white for the selection and the picker's check in Accent Blue,
    Unfavorite's heart (fill, 19 px) in Favorite pink-red (both halves) and white for the selection,
    the volume's two speakers (fill, 16 px) in Text Secondary (each half's), an artist page's Shuffle and Radio
    (regular, 16 px) in Accent Blue and white for the lit one, About's quality badge's waveform (bold, 12 px) in Text
    Primary (each half's), Buttons Lock's padlock and its card's padlock in each half's ink, the playlist pages' tiles
    (fill, 20 px) in Text Tertiary (each half's) and white, their action glyphs (regular, 18 px) in Accent Blue and
    white, Add Songs' marks (fill, 20 px) in Text Secondary, green and red (each half's) and white, the song rows' cloud
    and arrow (regular, 18 px) in Text Tertiary (each half's) and white for the selection, and Downloads' picked arrow
    in Accent Blue and white and its kept-by-the-app arrows in Text Secondary (each half's) and white, and a card app's
    sign-in steps (fill, the done check bold, 44 px) in white for their solid circles, Control Center's glyphs (fill,
    white, its sliders' in the block's grey), the USB pages' rows, mode glyphs and toasts, and Up Next's bump arrow and
    Now Playing's queue and radio glyphs, charging's toasts and a card app's server error's (fill, 15 px, each in its
    colour, the battery's in each half's Text Primary) and the plug-in screen's bolt (fill, 26 px, Positive green), and Software Update's result glyphs
    (white, 44 px), its offer's (white, 30 px) and its spinner, and Car Remote's phone (20 px) in Text Secondary (each
    half's) and white. The status battery's inset glyphs have their own test."""
    t = theme.builtins()[0]
    got = art.images(t)
    blue = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    modes = [f'mode_{n}_{k}' for n in ('shuffle', 'repeat') for k in ('d', 'l', 'on')] + ['mode_repeat1_on']
    wifi = [f'{n}_{k}' for n in ('wifi_1', 'wifi_2', 'wifi_3', 'lock', 'trash') for k in ('d', 'l')]
    warn = {f'wifi_warn_{k}': c for k, c in art.inks('negative', 'd', 'l', 'w').items()}  # a failed join's mark (wifi_rows.c)
    # Now Playing's heart (test_now_playings_heart): on in Favorite pink-red, both halves; off in Text Secondary
    hearts = {'np_fav_d': 0xff375f, 'np_fav_l': 0xff375f, 'np_unfav_d': 0xababab, 'np_unfav_l': 0x5c5c5c}
    glyphs = {'np_more_d': 0xffffff, 'np_more_l': 0,
              'np_moredown_d': 0xababab, 'np_moredown_l': 0x5c5c5c}
    rates = [f'rate_{on}_{k}' for on in ('on', 'off') for k in ('d', 'l', 'w')]
    stars = {f'npstar_{on}{big}_{k}': (px, 0xffffff if k == 'd' else 0) for on in ('on', 'off')
             for big, px in (('', art.NP_STAR_PX), ('_big', art.NP_STAR_BIG_PX)) for k in ('d', 'l')}  # np_rate.c's
    marks = [f'stream_{n}_{k}' for n in ('tidal', 'spotify', 'app', 'radio', 'airplay', 'config', 'subsonic') for k in ('d', 'l', 'w')]
    menus = [f'menu_{n}_{k}' for n in art.MENU_ICONS for k in ('d', 'l', 'w')] + ['menu_check_a']
    menus += [f'menu_grip_{k}' for k in ('d', 'l', 'w')]  # Move's grip (hold_menu.c), q2-coda's own glyph
    # the menu sheet's Unfavorite (hold_menu.c): heart (fill) in Favorite pink-red, both halves, white on the bar
    menufav = {'menu_heart-fill_d': 0xff375f, 'menu_heart-fill_l': 0xff375f, 'menu_heart-fill_w': 0xffffff}
    speakers = [f'vol_{n}_{k}' for n in ('lo', 'hi') for k in ('d', 'l')]  # the volume's ends on Now Playing (fill, 16 px)
    pair = [f'pair_{n}_{k}' for n in ('shuffle', 'radio', 'play') for k in ('a', 'w')]  # artist_page.c's action pairs
    quality = ['quality_d', 'quality_l']  # np_about.c's badge
    lives = [f'mark_{n}_{k}' for n in ('cloud', 'down') for k in ('d', 'l', 'w')]  # catalogue.c's song marks
    store = ['store_pick_a', 'store_pick_w'] + [f'store_auto_{k}' for k in ('d', 'l', 'w')]  # storage.c's Downloads
    modepages = [f'modepage_{n}' for n in art.MODE_GLYPHS]  # every mode page's glyph (feedback.c mode_page_paint)
    offers = [f'swoffer_{n}' for n in art.SW_OFFER]  # Software Update's offer (sw_update.c sw_offer_paint)
    cc = [f'cc_{n}{k}' for n in art.CC_ICONS for k in ('', '_l')]  # control_center.c's glyphs, each half's
    usb = [*(f'usb_{n}_{k}' for n in art.USB_ROWS for k in ('d', 'l', 'w')),
           *(f'usbtoast_{n}' for n in art.USB_TOASTS)]  # usb_ui.c's chooser rows and toasts
    usb += [f'empty_{n}_{k}' for n in art.EMPTY_GLYPHS for k in ('d', 'l')]  # feedback.c empty_state's glyphs, per half
    usb += [f'scan_{n}_{k}' for n in ('done', 'card', 'folder') for k in ('d', 'l')]  # scan_page.c's figures
    upnext = [f'bump_{k}' for k in ('d', 'l', 'w')] + [f'mode_{n}' for n in ('queue_d', 'queue_l', 'radio_d', 'radio_l', 'radio_w',
                                                                              'shuffle_xd', 'shuffle_xl', 'repeat_xd', 'repeat_xl')]
    charge = {**{f'chargetoast_{n}': c for n, (_, c) in art.CHARGE_TOASTS.items()},
              'chargetoast_battery_d': 0xffffff, 'chargetoast_battery_l': 0,  # charging.c's toasts
              'srvtoast': 0xff9f0a}  # addon.c's server error: cloud-slash in Warning
    inset = [f'batt{n}_{k}' for n in art.BATT_GLYPHS for k in ('d', 'l')]  # list_marks.c battery_slim
    keylock = [f'keylock_{k}' for k in ('d', 'l')]  # status_row.c's padlock (the card's below)
    status = [f'status_{n}_{k}' for n in ('wifi_1', 'wifi_2', 'wifi_3', 'wifi_x', 'bt', 'btdim', 'headphones', 'wired', 'car', 'down',
                                          'vol', 'mute', 'offline',
                                          *(f'sync_{f}' for f in range(art.STATUS_SPIN)))
              for k in ('d', 'l')]  # status_row.c's other glyphs: their own test
    # bt_ui.c's device kinds, its scan spinner and the connection toasts (a drop's in Text Secondary)
    btdev = {f'btdev_{n}_{k}': c for n in art.BT_ROW_GLYPHS for k, c in (('d', 0xababab), ('l', 0x5c5c5c), ('w', 0xffffff))}
    btspin = [f'btspin_{k}_{f}' for k in ('d', 'l') for f in range(art.SPIN_SPOKES)]
    guest = ['guestphone_a', 'pairing_phone', 'status_guest', 'status_relay_d', 'status_relay_l']  # guest_ui.c's, pair_prompt.c's, status_row.c's
    guest += [f'adb{n}_{k}' for n in ('card', 'pill') for k in 'dl']  # usb_ui.c's Allow this computer? and its pill
    # localsend_ui.c's: a device's glyph per row, on the transfer page, on the card and its toast; Send Logs' plane; the file line
    lsend = {**{f'lsdev_{n}_{k}': (art.CR_PHONE_PX, c) for n in art.LS_DEVICES for k, c in (('d', 0xababab), ('l', 0x5c5c5c), ('w', 0xffffff))},
             **{f'lsbig_{n}_{k}': (art.LS_BIG_PX, c) for n in art.LS_DEVICES for k, c in (('d', 0xababab), ('l', 0x5c5c5c))},
             **{f'lscard_{n}': (art.PAIR_GLYPH_PX, blue) for n in art.LS_DEVICES},
             **{f'lstoast_{n}': (art.USB_TOAST_PX, 0xffffff) for n in art.LS_DEVICES},
             'lssend_a': (art.LS_SEND_PX, blue), 'lssend_w': (art.LS_SEND_PX, 0xffffff),
             'lsfile_d': (art.LS_FILE_PX, 0x858585), 'lsfile_l': (art.LS_FILE_PX, 0x6e6e6e)}
    bttoast = {**{f'bttoast_{n}': c for n, (_, c) in art.BT_TOASTS.items()},
               **{f'bttoast_{n}_off': 0xababab for n in art.BT_TOASTS if n != 'low'}}
    circles = {'circle_check_a': (art.CIRCLE_CHECK_PX, blue), 'circle_check_w': (art.CIRCLE_CHECK_PX, 0xffffff),
               'circle_lock_d': (art.CIRCLE_LOCK_PX, 0xababab), 'circle_lock_l': (art.CIRCLE_LOCK_PX, 0x5c5c5c)}  # list_marks.c
    card = [f'keylock_card_{k}' for k in ('d', 'l')]
    tiles = {f'pltile_{n}_{k}': c for n in ('playlist', 'otg') for k, c in (('d', 0x858585), ('l', 0x6e6e6e), ('w', 0xffffff))}
    acts = {f'plact_{n}_{k}': c for n in ('shuffle', 'add', 'save', 'clear', 'search') for k, c in (('a', blue), ('w', 0xffffff))}
    pmarks = {f'plmark_{n}_{k}': c for n, d, l in (('in', 0xababab, 0x5c5c5c), ('add', 0x34c759, 0x34c759), ('out', 0xff6b5e, 0xff6b5e))
              for k, c in (('d', d), ('l', l), ('w', 0xffffff))}
    assert set(got) == {'sort_bars'} | {f'oled_{n}' for n in ('chevron_d', 'chevron_l', 'chevron_w', 'chevron_td', 'chevron_tl', 'play_d', 'play_l',
                                              'pause_d', 'pause_l', 'shuffle_a', 'shuffle_w', 'folder_d', 'folder_l', 'folder_w',
                                              'crphone_d', 'crphone_l', 'crphone_w',
                                              *(f'ctaring_{s}_{k}' for s in 'lr' for k in 'dl'),  # the buttons' focus ring's ends
                                              'check_a', 'check_w', 'confirm_shuffle', *modes, *wifi, *warn, *glyphs, *hearts, *menufav, *rates, *stars, *marks, *menus, *speakers, *pair, *quality,
                                              *keylock, *status, *card, *circles, *tiles, *acts, *pmarks, *lives, *store, *modepages, *cc, *usb, *upnext, *charge, *inset, 'chargebolt', 'radio_tower',
                                              'search_pop', 'search_pop_letters', *(f'search_return_{m}_{k}' for m in ('search', 'go', 'save') for k in 'wa'), 'holdback',
                                              *(f'spin_{f}' for f in range(art.SPIN_SPOKES)),
                                              *offers, *(f'swspin_{k}_{f}' for k in ('d', 'l') for f in range(art.SW_SPIN_SPOKES)),
                                              *(f'npspin_{k}_{f}' for k in ('d', 'l') for f in range(art.SW_SPIN_SPOKES)),
                                              *btdev, *btspin, *bttoast, *guest, *lsend)}
    assert {'funnel-simple', 'arrow-bend-down-right', 'queue', 'heart', 'list-plus', 'check', 'caret-up', 'caret-down'} <= set(art.MENU_ICONS)
    for name, px, rgb in (('chevron_d', art.CARET_PX, 0x999999), ('chevron_l', art.CARET_PX, 0x8a8a8a), ('chevron_w', art.CARET_PX, 0xffffff),
                          ('play_d', art.STATE_PX, 0xffffff), ('play_l', art.STATE_PX, 0),
                          ('shuffle_a', art.ROW_ICON_PX, blue), ('shuffle_w', art.ROW_ICON_PX, 0xffffff),
                          ('folder_d', art.ROW_ICON_PX, 0xababab), ('folder_l', art.ROW_ICON_PX, 0x5c5c5c),
                          ('folder_w', art.ROW_ICON_PX, 0xffffff), ('confirm_shuffle', art.CONFIRM_GLYPH_PX, blue),
                          ('crphone_d', art.CR_PHONE_PX, 0xababab), ('crphone_l', art.CR_PHONE_PX, 0x5c5c5c),
                          ('crphone_w', art.CR_PHONE_PX, 0xffffff),  # Car Remote's phone rows
                          *((n, px, c) for n, (px, c) in lsend.items()),  # LocalSend's
                          ('chevron_td', art.CARET_PX, 0x858585), ('chevron_tl', art.CARET_PX, 0x6e6e6e),
                          ('check_a', art.CHECK_PX, blue), ('check_w', art.CHECK_PX, 0xffffff),
                          ('holdback', art.HOLD_BACK_PX, 0xffffff),  # search_strip.c's Return-held card
                          *((n, art.ROW_ICON_PX, 0 if n.endswith('_l') else 0xffffff) for n in wifi),
                          *((n, art.ROW_ICON_PX, c) for n, c in warn.items()),
                          *((n, 22, {'d': 0xababab, 'l': 0x5c5c5c, 'on': blue}[n.rsplit('_', 1)[1]]) for n in modes),
                          *((n, art.ROW_ICON_PX, rgb) for n, rgb in glyphs.items()),
                          *((n, art.NP_HEART_PX, rgb) for n, rgb in hearts.items()),
                          *((n, art.MENU_ICON_PX, rgb) for n, rgb in menufav.items()),
                          *((n, art.RATE_PX, {'d': 0xffffff, 'l': 0, 'w': 0xffffff}[n[-1]]) for n in rates),  # Text Primary
                          *((n, px, c) for n, (px, c) in stars.items()),
                          *((n, art.STREAM_PX, {'d': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff}[n[-1]]) for n in marks),
                          *((n, art.MENU_ICON_PX, {'d': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff, 'a': blue}[n.rsplit('_', 1)[1]]) for n in menus),
                          *((n, 16, {'d': 0xababab, 'l': 0x5c5c5c}[n[-1]]) for n in speakers),
                          *((n, art.PAIR_ICON_PX, blue if n.endswith('_a') else 0xffffff) for n in pair),
                          *((n, art.QUALITY_PX, 0xffffff if n.endswith('_d') else 0) for n in quality),
                          *((n, art.STATE_PX, 0 if n.endswith('_l') else 0xffffff) for n in keylock),
                          *((n, art.KEYLOCK_CARD_PX, 0 if n.endswith('_l') else 0xffffff) for n in card),
                          *((n, px, c) for n, (px, c) in circles.items()),
                          *((n, art.TILE_PX, c) for n, c in tiles.items()), *((n, art.PL_ACTION_PX, c) for n, c in acts.items()),
                          *((n, art.PL_MARK_PX, c) for n, c in pmarks.items()),
                          *((n, art.MARK_PX, {'d': 0x858585, 'l': 0x6e6e6e, 'w': 0xffffff}[n[-1]]) for n in lives),
                          *((n, art.MARK_PX, {'a': blue, 'd': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff}[n[-1]]) for n in store),
                          *((n, art.MODE_GLYPH_PX, 0xffffff) for n in modepages),
                          *((n, art.SW_OFFER_PX, 0xffffff) for n in offers),
                          *((n, art.USB_TOAST_PX, c) for n, c in charge.items()), ('chargebolt', art.CHARGE_BOLT_PX, 0x34c759),
                          ('radio_tower', art.RADIO_TOWER_PX, 0xffffff),  # Home's pane while Radio plays (radio_pane.c)
                          *((n, art.ROW_ICON_PX, c) for n, c in btdev.items()), *((n, art.USB_TOAST_PX, c) for n, c in bttoast.items()),
                          *((f'cc_{n}', px, c) for n, (_, px, c) in art.CC_ICONS.items()),
                          *((f'cc_{n}_l', px, art.CC_LIGHT[n]) for n, (_, px, _) in art.CC_ICONS.items())):
        w, h, at = pixels(got[f'oled_{name}'])
        inked = {at(x, y)[:3] for y in range(h) for x in range(w) if at(x, y)[3]}  # straight alpha: one colour, any coverage
        assert (w, h) == (px, px) and inked == {tuple(rgb >> s & 255 for s in (16, 8, 0))}
        assert max(at(x, y)[3] for y in range(h) for x in range(w)) > 200


@pytest.mark.parametrize('half, primary, secondary, unlit', [('d', 0xffffff, 0xababab, 0x3a3a3c), ('l', 0, 0x5c5c5c, 0xc7c7cc)])
def test_the_status_rows_glyphs_are_the_clocks_digits_height_in_each_halfs_ink(half, primary, secondary, unlit):
    """status_row.c's slots, every one 11 px tall (STATUS_INK, the clock's digits') with its ink's top on STATUS_TOP,
    so on the row it covers rows 17 to 27 with the digits: Wi-Fi iOS's fan, 15.6 x 11 in a 16 px box, its levels
    lighting the dot and bands over the rest in STATUS_UNLIT, the shape one box in every level; wifi-x, a headset's
    headphones, the car and the volume pill's speakers (vol_pill.c) sized by their height (wifi-x and the car are wider
    than tall: by the longer side Wi-Fi came out 8 px tall) in Text Primary; the offline cloud in Warning in both halves; the sync arrows' STATUS_SPIN frames in Text Primary (the user's, 2026-11-20: 30 fps, white), 30 to the half turn, each turned from the last."""
    got = art.images(theme.builtins()[0])
    def ink(name, cut=64):
        w, h, at = pixels(got[f'oled_status_{name}_{half}'])
        lit = [(x, y) for y in range(h) for x in range(w) if at(x, y)[3] > cut]
        colours = {at(x, y)[:3] for y in range(h) for x in range(w) if at(x, y)[3] > 250}
        return (w, h), lit, colours
    rgb = lambda c: tuple(c >> s & 255 for s in (16, 8, 0))  # noqa: E731
    box = lambda lit: (min(x for x, _ in lit), max(x for x, _ in lit), min(y for _, y in lit), max(y for _, y in lit))  # noqa: E731
    boxes = set()
    for level in (1, 2, 3):
        size, lit, colours = ink(f'wifi_{level}', 8)
        boxes.add(box(lit))
        near = lambda c: any(max(abs(a - b) for a, b in zip(rgb(c), k)) <= 16 for k in colours)  # noqa: E731 (blended edges)
        assert size == (art.STATUS_WIFI_W, art.STATUS_PX) and near(primary) and near(unlit) == (level < 3)
    assert boxes == {(0, 15, 1, 11)}  # 16 columns (15.6 of ink), rows 1 to 11: 11 tall
    for name, w in (('wifi_x', art.STATUS_WIFI_W), ('headphones', art.STATUS_PX), ('car', art.STATUS_CAR_W),
                    ('vol', art.STATUS_PX), ('mute', art.STATUS_PX)):
        size, lit, colours = ink(name, 8)
        assert size == (w, art.STATUS_PX) and (name == 'btdim' or colours == {rgb(primary)})
        x0, x1, y0, y1 = box(lit)
        assert (y0, y1) == (1, 11) and x1 - x0 + 1 <= w
    x0, x1, _, _ = box(ink('wifi_x', 8)[1])
    assert x1 - x0 + 1 == 14  # wider than its 11 rows
    size, lit, colours = ink('offline', 8)  # a card app's server out of reach: cloud-slash in Warning, both halves
    assert size == (art.STATUS_PX, art.STATUS_PX) and colours == {rgb(0xff9f0a)} and box(lit)[2:] == (1, 11)
    frames = [got[f'oled_status_sync_{f}_{half}'] for f in range(art.STATUS_SPIN)]
    assert art.STATUS_SPIN == 30
    assert len(set(frames)) == art.STATUS_SPIN and all(ink(f'sync_{f}')[2] == {rgb(primary)} for f in range(art.STATUS_SPIN))


@pytest.mark.parametrize('half, primary, dim', [('d', 0xffffff, 0x858585), ('l', 0, 0x6e6e6e)])
def test_the_bluetooth_rune_and_wired_headphones_stand_as_tall_as_the_battery(half, primary, dim):
    """The Bluetooth rune (dim while connecting) and wired headphones (Phosphor's regular headphones) are 12 px tall,
    the battery's body's height, from their box's top row: status_row.c puts the box on row 16, so their ink is on rows
    16 to 27 and centres on row 21.5 with the battery's and Wi-Fi's (the user's mockup, 2026-11-25). Each box is as
    wide as its ink: the rune 9 (8.4 of ink), the headphones 14 (13.6)."""
    got = art.images(theme.builtins()[0])
    for name, w, ink in (('bt', 9, primary), ('btdim', 9, dim), ('wired', art.STATUS_PX, primary)):
        width, h, at = pixels(got[f'oled_status_{name}_{half}'])
        lit = [(x, y) for y in range(h) for x in range(width) if at(x, y)[3] > 128]  # over half covered
        rows = sorted({y for _, y in lit})
        assert (width, h) == (w, art.STATUS_PX) and (rows[0], rows[-1]) == (0, 11), name
        assert 16 + (rows[0] + rows[-1]) / 2 == 21.5, name  # on the row: its ink's centre
        assert {at(x, y)[:3] for x, y in lit if at(x, y)[3] > 250} == {tuple(ink >> s & 255 for s in (16, 8, 0))}, name
        assert max(x for x, _ in lit) - min(x for x, _ in lit) + 1 >= w - 1, name  # the box hugs the ink


def test_the_status_rows_wifi_is_a_fan_of_a_dot_and_two_bands_with_gaps():
    """iOS's fan up its centre line from the apex on the ink's bottom (the clock's baseline): the dot, a 1.4 px gap,
    a band, a gap, a band, each as STATUS_FAN cuts them (rows more than half covered)."""
    w, h, at = pixels(art.status_wifi(3, 0xffffff, 0x3a3a3c))
    column = [at(7, y)[3] + at(8, y)[3] for y in range(h)]  # the two columns either side of the centre line
    on = [y for y in range(h) if column[y] > 255]
    runs, start = [], on[0]
    for a, b in zip(on, on[1:] + [-1]):  # -1 ends the last run, as no row is -1
        if b != a + 1:
            runs.append((start, a))
            start = b
    assert runs == [(1, 2), (5, 6), (8, 11)]  # outer band, middle band, the dot down to row 11, the ink's bottom


@pytest.mark.parametrize('half, ink, gap', [('d', (255, 255, 255), (0, 0, 0)), ('l', (0, 0, 0), (255, 255, 255))])
def test_the_status_batterys_glyph_is_inside_a_gap_in_the_canvas(half, ink, gap):
    """list_marks.c battery_slim's bolt, plug and warning: BATT_GLYPH_BOX, the glyph solid round its centre in Text Primary, a
    ring of the Canvas round its edge (the gap), and the box's corners clear."""
    got = art.images(theme.builtins()[0])
    for n in art.BATT_GLYPHS:
        w, h, at = pixels(got[f'oled_batt{n}_{half}'])
        assert (w, h) == art.BATT_GLYPH_BOX
        cx, cy = (int(v) for v in art.BATT_GLYPH_AT)
        assert any(at(x, y) == (*ink, 255) for y in range(cy - 2, cy + 3) for x in range(cx - 2, cx + 3))
        assert at(0, 0)[3] == at(w - 1, 0)[3] == at(0, h - 1)[3] == at(w - 1, h - 1)[3] == 0
        ring = [(x, y) for y in range(h) for x in range(w) if at(x, y)[:3] == gap and at(x, y)[3] > 100]
        assert ring and all(abs(x - cx) >= 2 or abs(y - cy) >= 2 for x, y in ring), f'{n}: no gap round the glyph'
        assert all(at(x, 0)[3] < 128 for x in range(w))  # the 7 px glyph and its 1 px gap end inside the box


@pytest.mark.parametrize('half, ink, gap', [('d', (255, 255, 255), (0, 0, 0)), ('l', (0, 0, 0), (255, 255, 255))])
def test_the_status_batterys_plug_is_5_by_7_on_whole_pixels(half, ink, gap):
    """Full or held at Charge Limit (list_marks.c battery_glyph): the plug's ink is box columns 2 to 6 and rows 2 to 8
    (panel 295 to 299, 19 to 25 at the box's 293, 17), its prongs solid in columns 3 and 5 with the slot between them
    the gap, and its cord the one column under the body's centre."""
    w, h, at = pixels(art.images(theme.builtins()[0])[f'oled_battplug_{half}'])
    near = lambda c, to: sum(abs(a - b) for a, b in zip(c[:3], to)) < 3 * 64 and c[3] >= 128  # noqa: E731
    inked = {(x, y) for y in range(h) for x in range(w) if near(at(x, y), ink)}  # the prongs' round tops: most of a pixel
    assert (min(x for x, _ in inked), max(x for x, _ in inked), min(y for _, y in inked), max(y for _, y in inked)) == (2, 6, 2, 8)
    assert {(3, 3), (5, 3), (3, 4), (5, 4)} <= inked and near(at(4, 3), gap)  # two prongs, a slot between
    assert {x for x, y in inked if y == 8} == {4}  # the cord
    assert {x for x, y in inked if y == 5} == {2, 3, 4, 5, 6}  # the body's full width


def test_oleds_spinner_turns_its_bright_spoke_clockwise_a_frame_at_a_time():
    """Cover Flow's opening spinner (list_marks.c spinner_icon): eight frames, the caret's 15 px box, white alone;
    frame f's brightest spoke f eighths of a turn clockwise from 12 o'clock, the spoke after it (the one it left,
    anticlockwise) a step dimmer, the spoke ahead of it the dimmest."""
    got = art.images(theme.builtins()[0])
    for f in range(art.SPIN_SPOKES):
        w, h, at = pixels(got[f'oled_spin_{f}'])
        assert (w, h) == (art.CARET_PX, art.CARET_PX)
        assert {at(x, y)[:3] for y in range(h) for x in range(w) if at(x, y)[3]} == {(255, 255, 255)}
        def spoke(k):  # the alpha at spoke k's middle
            a = 2 * math.pi * k / art.SPIN_SPOKES - math.pi / 2
            r = sum(art.SPIN_R) / 2
            return at(int(w / 2 + r * math.cos(a)), int(h / 2 + r * math.sin(a)))[3]
        alphas = [spoke((f - d) % art.SPIN_SPOKES) for d in range(art.SPIN_SPOKES)]
        assert alphas == sorted(alphas, reverse=True) and alphas[0] > 240 and alphas[-1] < alphas[0] * 0.5
        assert at(w // 2, h // 2)[3] == 0  # the hub is clear


def test_the_software_updates_spinner_has_twelve_spokes_in_each_halfs_ink():
    """SD Card Update's check (sw_update.c): twelve frames per half, 48 px, Text Primary alone (white on black, black on
    paper), the head f twelfths of a turn clockwise from 12 o'clock and each spoke behind it dimmer, to a quarter."""
    got = art.images(theme.builtins()[0])
    for k, ink in (('d', (255, 255, 255)), ('l', (0, 0, 0))):
        for f in range(art.SW_SPIN_SPOKES):
            w, h, at = pixels(got[f'oled_swspin_{k}_{f}'])
            assert (w, h) == (art.SW_SPIN_PX, art.SW_SPIN_PX)
            assert {at(x, y)[:3] for y in range(h) for x in range(w) if at(x, y)[3]} == {ink}
            def spoke(n):
                a = 2 * math.pi * n / art.SW_SPIN_SPOKES - math.pi / 2
                r = sum(art.SW_SPIN_R) / 2
                return at(int(w / 2 + r * math.cos(a)), int(h / 2 + r * math.sin(a)))[3]
            alphas = [spoke((f - d) % art.SW_SPIN_SPOKES) for d in range(art.SW_SPIN_SPOKES)]
            assert alphas == sorted(alphas, reverse=True) and alphas[0] > 240 and 50 < alphas[-1] < 80, alphas
            assert at(w // 2, h // 2)[3] == 0


def test_every_empty_states_glyph_is_drawn_and_every_drawn_one_is_named():
    """The glyphs the payload's empty states name (each empty_t's first string, feedback.c empty_state) are
    EMPTY_GLYPHS' keys, no more and no fewer but for Sync's states, which a card app's status reply names at run time
    (addon.c, docs/oled-design.md#sync): a page naming one the build never drew paints no glyph."""
    named = set()
    for c in (pathlib.Path(__file__).parents[2]/'patch').glob('**/*.c'):
        src = c.read_text()
        for m in re.finditer(r'empty_t\b[^;(]*?=\s*\{|\(empty_t\)\s*\{', src):
            named |= set(re.findall(r'\{\s*"([a-z][a-z-]*)"', src[m.start():src.index(';', m.end())]))
    sync = {'arrows-clockwise', 'check-circle', 'arrow-circle-down', 'cloud'}
    assert named <= set(art.EMPTY_GLYPHS) and set(art.EMPTY_GLYPHS) - named <= sync


def test_phosphor_fills_nonzero_so_an_inner_contour_is_a_hole():
    """A ring as two contours wound opposite ways, as Phosphor's outlines are: its middle stays clear."""
    ring = art.path('M2 2H14V14H2ZM5 5V11H11V5Z')
    cov = art.coverage_nonzero(ring, (0, 0, 16, 16))
    assert cov[8 * 16 + 8] == 0 and cov[3 * 16 + 3] == 1 and cov[0] == 0
    assert art.coverage(ring, (0, 0, 16, 16))[8 * 16 + 8] == 1  # the union rule would fill it


def test_oleds_search_pop_is_one_bubble_and_a_strip_of_every_character():
    """Search's key pop (search_strip.c): the bubble, 30 px wide to the key's foot 66 px down, in Accent Blue, a
    rounded head over a neck narrowing to the 18 px key, clear beside the key; the characters' strip, a head's cell per
    character of the strip's sets, white; each glyph's ink centred on its cell's middle within a tenth of a pixel, read
    from the edge columns' coverage (H: two upright stems)."""
    t = theme.builtins()[0]
    got = art.images(t)
    blue = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    w, h, at = pixels(got['oled_search_pop'])
    assert (w, h) == (30, 66) and at(15, 15) == (*blue.to_bytes(3, 'big'), 255) and at(15, 60) == (*blue.to_bytes(3, 'big'), 255)
    assert at(0, 0)[3] == 0 and at(0, 15)[3] == 255 and at(29, 15)[3] == 255  # the head's rounded corner; its sides
    assert at(3, 60)[3] == 0 and at(26, 60)[3] == 0 and at(6, 60)[3] == 255 and at(23, 60)[3] == 255  # the key, 6 px in
    n, cell = len(art.POP_CHARS), 30
    w, h, at = pixels(got['oled_search_pop_letters'])
    assert (w, h) == (cell * n, 30)
    for ch in 'H|':
        i = art.POP_CHARS.index(ch)
        cols = [max(at(cell * i + x, y)[3] for y in range(h)) / 255 for x in range(cell)]
        assert max(cols) == pytest.approx(1, abs=0.01) and {at(cell * i + x, y)[:3] for x in range(cell) for y in range(h) if at(cell * i + x, y)[3]} == {(255, 255, 255)}
        lit = [x for x, c in enumerate(cols) if c > 0.01]
        left, right = lit[0] + 1 - cols[lit[0]], cell - (lit[-1] + cols[lit[-1]])
        assert left == pytest.approx(right, abs=0.1)


def test_the_pop_s_characters_are_the_strip_s_sets_in_order():
    """search_strip.c finds a character's cell by its sets' order (ss_pop_cell): POP_CHARS must be ss_sets joined."""
    src = (art.ROOT / 'patch/search_strip.c').read_text()
    found = re.search(r'ss_sets\[SS_SETS\] = \{(.*?)\};', src, re.S)
    assert found, 'ss_sets moved: update this test'
    body = found.group(1)
    sets = [ast.literal_eval(m) for m in re.findall(r'"(?:[^"\\]|\\.)*"', body)]
    assert len(sets) == 4 and ''.join(sets) == art.POP_CHARS and len(set(art.POP_CHARS)) == 94


# each return mark's pixels it inks and those it leaves clear (20 px, the mock-up's 24-unit grid at 20/24)
RETURN_INKED = {'search': ((8, 4), (14, 14)), 'go': ((6, 9), (11, 5)), 'save': ((8, 13), (12, 9))}
RETURN_CLEAR = {'search': ((8, 8), (6, 9)), 'go': ((8, 4), (8, 13)), 'save': ((8, 4), (6, 9), (11, 5))}


@pytest.mark.parametrize('kind', ['search', 'go', 'save'])
def test_the_return_key_s_marks_are_a_magnifier_an_arrow_and_a_check_white_and_the_accent_s_in_20_px(kind):
    """The user's mock-up (returnkey context.html): the magnifier a ring, hollow, its handle to the bottom right; the
    arrow a bar at the middle with its head to the right; the check's vertex low left of centre."""
    t = theme.builtins()[0]
    got = art.images(t)
    blue = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    for name, rgb in (('w', 0xffffff), ('a', blue)):
        w, h, at = pixels(got[f'oled_search_return_{kind}_{name}'])
        assert (w, h) == (20, 20)
        for p in RETURN_INKED[kind]:
            assert at(*p) == (*rgb.to_bytes(3, 'big'), 255), p
        for p in RETURN_CLEAR[kind] + ((0, 0), (19, 19)):
            assert at(*p)[3] == 0, p


def test_oleds_ratings_rows_stars_are_phosphors_filled_and_outlined_star():
    """A Ratings row's filled star inks its middle, the outlined one leaves it clear."""
    got = art.images(theme.builtins()[0])
    mid = art.RATE_PX // 2
    for name, middle in (('oled_rate_on_d', True), ('oled_rate_off_d', False)):
        w, h, at = pixels(got[name])
        assert bool(at(mid, mid)[3]) == middle


def test_oleds_tidal_mark_is_four_diamonds_three_across_one_under():
    """TIDAL's mark, at its optical size (ROW_INK across, centred): the three top diamonds' centres and the lower one's
    are inked, the corners between them clear."""
    w, h, at = pixels(art.images(theme.builtins()[0])['oled_stream_tidal_d'])
    assert all(at(x, 7)[3] > 200 for x in (4, 10, 15)) and at(10, 12)[3] > 200
    assert at(4, 12)[3] == 0 and at(15, 12)[3] == 0 and at(10, 2)[3] == 0


def test_the_radio_tower_is_a_mast_under_a_dot_between_two_pairs_of_waves():
    """Home's pane's tower (RADIO_TOWER, the Car Remote app's ic_radio): the dot and the mast's foot inked down the
    middle, a wave either side, the gaps between them clear."""
    w, h, at = pixels(art.images(theme.builtins()[0])['oled_radio_tower'])
    c = art.RADIO_TOWER_PX // 2
    assert (w, h) == (art.RADIO_TOWER_PX, art.RADIO_TOWER_PX)
    assert at(c, 16)[3] > 200 and at(c - 5, 29)[3] > 200 and at(c, 26)[3] > 200  # the dot, a leg, the crossbar
    assert at(7, 17)[3] > 200 and at(31, 17)[3] > 200 and at(13, 17)[3] > 200  # the outer waves, an inner one
    assert at(c, 3)[3] == 0 and at(c, 36)[3] == 0 and at(10, 17)[3] == 0 and at(c, 29)[3] == 0  # clear around


def test_oleds_subsonic_mark_is_q2_codas_submarine():
    """The q2-subsonic card app's mark (assets/icons/q2coda/subsonic.svg): the hull's top and periscope inked, the sea
    above the hull and inside it, between the portholes, clear."""
    w, h, at = pixels(art.images(theme.builtins()[0])['oled_stream_subsonic_d'])
    assert (w, h) == (art.STREAM_PX, art.STREAM_PX)
    assert all(at(x, y)[3] > 200 for x, y in ((6, 9), (8, 5), (3, 10), (12, 10), (16, 9)))  # at its optical size
    assert all(at(x, y)[3] == 0 for x, y in ((3, 5), (16, 5), (10, 10), (10, 17)))


@pytest.mark.parametrize('name', ['wifi_3_d', 'lock_d', 'trash_d', 'folder_d', 'shuffle_w', 'np_more_d',
                                  'stream_spotify_d', 'stream_tidal_d'])
def test_a_row_symbol_is_drawn_at_its_optical_size(name):
    """Every 20 px symbol (rows, Now Playing's More, Streaming's marks) is scaled about its ink's centre
    until the ink's longer side is ROW_INK (15.5 px), centred in its box, so a wide glyph and a tall one read as one
    size (docs/oled-design.md Icons)."""
    w, h, at = pixels(art.images(theme.builtins()[0])['oled_' + name])
    xs = [x for y in range(h) for x in range(w) if at(x, y)[3] > 24]
    ys = [y for y in range(h) for x in range(w) if at(x, y)[3] > 24]
    long = max(max(xs) - min(xs), max(ys) - min(ys)) + 1
    assert (w, h) == (art.ROW_ICON_PX, art.ROW_ICON_PX) and abs(long - art.ROW_INK) <= 1.5, long
    assert abs((min(xs) + max(xs) + 1) / 2 - w / 2) <= 1 and abs((min(ys) + max(ys) + 1) / 2 - h / 2) <= 1


def test_a_theme_draws_only_the_images_its_keys_ask_for():
    """Each optional group is its key's: a theme without Now Playing's own glyphs, the Shuffle row, the switch rows,
    or iPod text entry draws none of their images, and keeps the rest."""
    oled = theme.builtins()[0]
    bare = {**oled, 'np_modes': 0, 'shuffle_row': 0, 'set_switch': 0, 'entry_ipod': 0}
    full, got = set(art.images(oled)), set(art.images(bare))
    groups = ('oled_mode_', 'oled_np_', 'oled_vol_', 'oled_shuffle_', 'oled_check_', 'oled_wifi_', 'oled_lock_', 'oled_trash_',
              'oled_search_pop', 'oled_search_return', 'oled_holdback')
    assert got == {n for n in full if not n.startswith(groups)} and all(any(n.startswith(p) for n in full) for p in groups)


@pytest.mark.parametrize('right', [False, True])
def test_the_focus_rings_ends_are_its_annulus_supersampled_and_meet_its_straight_runs(right):
    """cta_ring_end (ui_kit.c cta_ring; patch/ui_kit.h CTA_RING_W, CTA_RING_H): 24 x 48, each pixel the share of it
    between the circles of radius 24 and 22 about the end's centre, so the column beside the straight run is two
    whole rows at the top and foot and clear between, the edges graded, and nothing outside the ring drawn. Two
    one-pixel AWTK strokes left the arcs stepped and uneven at 6x (the user, 2026-11-16)."""
    header = (pathlib.Path(__file__).parents[2]/'patch'/'ui_kit.h').read_text()
    assert re.search(r'#define CTA_RING_W 2\b', header) and (art.CTA_RING_H, art.CTA_RING_W) == (38 + 2 * (3 + 2), 2)
    w, h, at = pixels(art.cta_ring_end(right, 0xffffff))
    assert (w, h) == (24, 48)
    def alpha(x, y):  # the left end's frame
        return at(w - 1 - x if right else x, y)[3]
    assert [alpha(23, y) for y in (0, 1, 2, 45, 46, 47)] == [255, 255, 0, 0, 255, 255]
    for y in range(h):
        for x in range(w):
            d = math.hypot(x + .5 - 24, y + .5 - 24)
            if d > 24 + .75 or d < 22 - .75:
                assert alpha(x, y) == 0, (x, y)  # wholly outside the ring
            elif 22 + .75 < d < 24 - .75:
                assert alpha(x, y) == 255, (x, y)  # wholly inside it
    assert len({alpha(x, y) for y in range(h) for x in range(w)}) > 20  # graded edges, not steps


def test_now_playings_heart():
    """The user's mock-up (design/mockups/mock_np_heart.py, approved 2026-11-19): Now Playing's heart 18 px of ink tall,
    not about 32, its ink's left at screen x 194 and its middle at y 164, in its 34 px image centred in the 56 x 42
    target at x 182, y 142 (tools/ipod.py NP_GRID_HEART): image x 1, y 18. Off, Phosphor's heart (bold) in Text
    Secondary; on, its heart (fill) in the Favorite token, iOS's dark systemPink #FF375F, the same in Light Mode."""
    got = art.images(theme.builtins()[0])
    for name, rgb in (('np_unfav_d', 0xababab), ('np_unfav_l', 0x5c5c5c), ('np_fav_d', 0xff375f), ('np_fav_l', 0xff375f)):
        w, h, at = pixels(got[f'oled_{name}'])
        assert (w, h) == (art.NP_HEART_PX, art.NP_HEART_PX)
        ink = [(x, y) for y in range(h) for x in range(w) if at(x, y)[3] > 127]
        xs, ys = [x for x, _ in ink], [y for _, y in ink]
        assert max(ys) - min(ys) + 1 in (18, 19), (name, min(ys), max(ys))
        assert min(xs) == 1 and abs((min(ys) + max(ys) + 1) / 2 - 18) <= 0.5, (name, min(xs), min(ys), max(ys))
        assert {at(x, y)[:3] for x, y in ink if at(x, y)[3] == 255} == {(rgb >> 16, rgb >> 8 & 255, rgb & 255)}, name
    assert art.token('favorite') == art.token('favorite', True) == 0xff375f


def test_stroke_skips_a_zero_length_segment():
    """A repeated point draws no side quad, only the round join there."""
    out = art.stroke([[(0, 0), (0, 0), (4, 0)]], 2)
    quads = [p for p in out if len(p) == 4]
    assert len(quads) == 1 and len(out) == 4  # the start cap, a join at the repeat, one quad, the end cap


def test_laid_leaves_a_clear_paint_unpainted():
    """A layer whose paint has no alpha at a pixel leaves what is under it there."""
    under = ([1, 1], lambda x, y: (0xff0000, 1.0))
    clear = ([1, 1], lambda x, y: (0x00ff00, 0.0 if x < 1 else 1.0))
    assert art.laid(2, 1, [under, clear]) == bytes((255, 0, 0, 255, 0, 255, 0, 255))
