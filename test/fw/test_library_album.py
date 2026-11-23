"""library_album.c: stock's album pages (albuminfo_page, playerjumpinfo_page) dressed as OLED's album page."""
import ctypes as C

import pytest

from conftest import O, Deques
from test_nav_display_library import Ui, records, scratch, st, use_set  # noqa: F401 (fixtures)

DOT = ' \xb7 '.encode('utf-8')


def album_page(fw, st, name='albuminfo_page'):
    """A stock album page as its UI loads, over Home, under OLED's set: its navbar, Songs' action row and list."""
    ui = Ui(fw)
    fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_:
            ui.new(C.string_at(kind).decode(), parent, x, y, w, 0))
    fw.mock('button_create', lambda parent, x, y, w, h, *_: ui.new('button', parent, x, y, w, h))
    ui.window('home_page')
    win = ui.window(name)
    jump = name == 'playerjumpinfo_page'
    nav = ui.add('view', win, (0, 0, 375, 50), 'view_navbar')
    row = None if jump else ui.add('view', win, (0, 0, 375, 32), 'view_navbar_allplay')
    lv = ui.add('table_view' if jump else 'list_view', win, (0, 32, 375, 224), 'table_view' if jump else 'list_view')
    rows = ui.add('table_client' if jump else 'scroll_view', lv, (0, 0, 365, 224),
                  'music_table_client' if jump else 'scroll_view')
    use_set(st)
    info = (C.c_char * 912).in_dll(fw.lib, 'fw_ctx_g_local_classinfo_save')
    C.memmove(C.addressof(info) + 0xd, b'Caracal\0\0', 9)
    return ui, win, nav, row, lv, rows


def made(win):
    for c in win.children:
        yield c
        yield from made(c)


@pytest.mark.parametrize('name', ('albuminfo_page', 'playerjumpinfo_page'))
def test_under_oled_an_album_page_is_dressed_as_it_loads(fw, st, name):
    """The navbar and the Shuffle row go, the list moves under the pair at y 168, down to the screen's foot (its rows
    past y 282 fading, navigation.c list_fade), and the
    window holds the header (the album the page opened, its cover's square) and Play and Shuffle; the page has no
    title and opens on Play."""
    ui, win, nav, row, lv, rows = album_page(fw, st, name)
    fw.fn('album_local_load', None, C.c_void_p)(win.addr)
    assert not nav.visible and (row is None or not row.visible)
    assert lv.box == (0, 168, 375, 320 - 168)  # the window is the whole screen (full-bleed): its rows are the screen's
    head = [w for w in made(win) if '_ar_name' in w.props]
    assert [(w.props['_ar_name'], w.props.get('_ar_square')) for w in head] == [(b'Caracal', 1)]
    assert [w.props['_ar_left'] for w in made(win) if '_ar_left' in w.props] == [b'Play', b'Shuffle']
    assert fw.fn('album_local_on', C.c_int, C.c_void_p)(win.addr) == 1
    assert fw.fn('pair_holds', C.c_int, C.c_void_p)(rows.addr) == 1  # Play lit
    key = fw.fn('pair_key', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    assert key(win.addr, rows.addr, 1) == 1 and key(win.addr, rows.addr, 1) == 1  # Shuffle, then the first track
    assert fw.fn('pair_holds', C.c_int, C.c_void_p)(rows.addr) == 0


def test_another_page_is_left_alone(fw, st):
    ui, win, nav, row, lv, rows = album_page(fw, st, 'allmusic_page')
    fw.fn('album_local_load', None, C.c_void_p)(win.addr)
    assert row.visible and fw.fn('album_local_on', C.c_int, C.c_void_p)(win.addr) == 0


def test_the_header_reads_the_pages_songs(fw, st, records):
    """At the header's paint with the page on top: the songs' album and album artist, their count and summed length
    (the year needs the library's database, here none: left out), the cover's placeholder without a cached one;
    read again only when the count changes."""
    ui, win, nav, row, lv, rows = album_page(fw, st)
    fw.fn('album_local_load', None, C.c_void_p)(win.addr)
    d = Deques(fw)
    songs = [records.song(album=b'Caracal (Deluxe)', artist=b'Disclosure', path=b'/m/D/C/%02d.flac' % i)
             for i in range(3)]
    for r, sec in zip(songs, (404, 237, 305)):  # CUE tracks' lengths: no file to measure
        C.c_int.from_address(r + O['REC_CUE_END']).value = sec
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = d.new(songs)
    (head,) = [w for w in made(win) if '_ar_name' in w.props]
    paint = fw.fn('album_local_paint', None, C.c_void_p, C.c_void_p)
    canvas = C.create_string_buffer(0x200)  # no LCD: artist_paint draws nothing
    paint(head.addr, C.addressof(canvas))
    assert (head.props['_ar_name'], head.props['_ar_sub'], head.props['_ar_meta']) == \
        (b'Caracal (Deluxe)', b'Disclosure', b'3 songs' + DOT + b'16 min')
    assert head.props['_ar_cover'] == b''
    head.props['_ar_meta'] = b'kept'
    paint(head.addr, C.addressof(canvas))
    assert head.props['_ar_meta'] == b'kept'  # the same three songs: not read again


@pytest.mark.parametrize('secs, span', [((20,), b'1 min'), ((3570,), b'1h'), ((3600, 1200), b'1h20m')])
def test_the_headers_span_takes_minutes_hours_or_both(fw, st, records, secs, span):
    """The songs' summed length, rounded to the minute: under an hour "N min", whole hours "Nh", else "NhMm"
    (docs/oled-design.md#type)."""
    ui, win, *_ = album_page(fw, st)
    fw.fn('album_local_load', None, C.c_void_p)(win.addr)
    songs = [records.song(album=b'Caracal', artist=b'Disclosure', path=b'/m/D/C/%02d.flac' % i)
             for i in range(len(secs))]
    for r, sec in zip(songs, secs):
        C.c_int.from_address(r + O['REC_CUE_END']).value = sec
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = Deques(fw).new(songs)
    (head,) = [w for w in made(win) if '_ar_name' in w.props]
    canvas = C.create_string_buffer(0x200)
    fw.fn('album_local_paint', None, C.c_void_p, C.c_void_p)(head.addr, C.addressof(canvas))
    assert head.props['_ar_meta'].endswith(DOT + span)


def test_an_album_page_over_another_hands_the_design_back(fw, st, records):
    """Look up Album over a Library album: the new page is drawn, and the first, back on top, is dressed again
    (one header, its own) with the stop it had."""
    ui, first, *_, rows = album_page(fw, st)
    load = fw.fn('album_local_load', None, C.c_void_p)
    load(first.addr)
    key = fw.fn('pair_key', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    assert key(first.addr, rows.addr, 1) == 1  # Shuffle lit
    second = ui.window('playerjumpinfo_page')
    lv = ui.add('table_view', second, (0, 50, 375, 210), 'table_view')
    ui.add('table_client', lv, (0, 0, 365, 210), 'music_table_client')
    load(second.addr)
    on = fw.fn('album_local_on', C.c_int, C.c_void_p)
    assert on(first.addr) and on(second.addr)
    ui.wm.children.remove(second)  # closed: the first on top again
    (front,) = [(fn, ctx) for w, evt, fn, ctx in ui.handlers if w == first.addr and evt == O['EVT_WINDOW_FOREGROUND']]
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(front[0])(front[1], None)
    assert len([c for c in first.children if '_ar_name' in c.props]) == 1
    holds = fw.fn('pair_holds', C.c_int, C.c_void_p)
    assert holds(rows.addr) and key(first.addr, rows.addr, -1) == 1 and holds(rows.addr)  # Shuffle, then Play
    song = records.song(album=b'Caracal', artist=b'Disclosure', path=b'/m/D/C/01.flac')
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = Deques(fw).new([song])
    fw.mock('navigator_to_with_context', lambda *_: 0)
    assert key(first.addr, rows.addr, 0) == 1  # Play pressed: the first page's songs, from the first
    assert [C.string_at(a[0]) for _, a in fw.calls('navigator_to_with_context')] == [b'playing_page']


@pytest.mark.parametrize('extra', range(-2, 12))
def test_lb_year_s_query_never_passes_its_buffer(fw, extra):
    """lb_year's songtable query in sql[640]: a card path of 577 to 581 bytes left its end at 631 to 635 and the
    tail ("' limit 1" and its NUL, 10 bytes) passed the buffer by up to 5. A path too long for the query is not
    asked; one that fits is asked whole."""
    prefix = len("select substr(year,1,4) from songtable where fileurl='")
    path = b'/mnt/mmc/' + b'a' * (640 - 10 - prefix - 9 + extra)
    asked = []
    fw.mock('toolsQueryDbTable', lambda db, sql, cb, ctx, *_: asked.append(C.string_at(sql)) or 0)
    rec = C.create_string_buffer(0x60)
    keep = C.create_string_buffer(path)
    C.c_void_p.from_address(C.addressof(rec) + O['REC_PATH']).value = C.addressof(keep)
    fw.fn('library_album__lb_year', C.c_int, C.c_void_p)(rec)
    assert all(len(q) < 640 for q in asked), [len(q) for q in asked]
    assert not asked or asked[0] == b"select substr(year,1,4) from songtable where fileurl='" + path + b"' limit 1"
