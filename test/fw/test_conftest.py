"""conftest.py's shared fakes themselves: what the other test files stand on."""
import ctypes as C

from conftest import Files, stock_prop_str
from test_nav_lists import Ui


def test_files_open_together_never_share_a_handle(fw):
    files = Files(fw, {'/a': b'first', '/b': b'second'})
    a, b = files.fopen(b'/a', b'rb'), files.fopen(b'/b', b'rb')
    files.fclose(a)  # the first closes while the second stays open
    c = files.fopen(b'/c', b'wb')
    assert c != b  # numbered by the count of open files alone, the third took the second's handle
    out = C.create_string_buffer(6)
    assert files.fread(out, 1, 6, b) == 6 and out.raw == b'second'
    assert files.fwrite(b'third', 1, 5, c) == 5
    files.fclose(c)
    files.fclose(b)
    assert files.files == {'/a': b'first', '/b': b'second', '/c': b'third'} and files.open == {}


def test_widget_props_read_as_strings_are_null_where_stocks_are():
    """Stock's widget_get_prop_str hands back NULL for a field the widget answers itself and has not set, for text
    (a wide string) and for an int; the default only for a prop never set. A fake that gave the default hid a
    NULL read on every Now Playing open (2026-11-17)."""
    assert stock_prop_str(b'label', b'name', None, b'') is None
    assert stock_prop_str(b'label', b'name', b'row', b'') == b'row'
    assert stock_prop_str(b'label', b'text', b'shown', b'') is None
    assert stock_prop_str(b'label', b'value', 3, b'') is None
    assert stock_prop_str(b'label', b'custom', None, b'd') == b'd'
    assert stock_prop_str(b'window', b'open_anim_hint', None, b'') is None
    assert stock_prop_str(b'label', b'open_anim_hint', None, b'd') == b'd'
    assert stock_prop_str(b'image', b'image', None, b'') is None


def test_the_shared_ui_answers_names_and_strcmp_as_stock(fw):
    ui = Ui(fw)
    get = fw.fn('widget_get_prop_str', C.c_void_p, C.c_void_p, C.c_char_p, C.c_char_p)
    unnamed, named = ui.widget('label', ui.wm), ui.widget('label', ui.wm, name='row')
    assert get(unnamed.at, b'name', b'') is None
    assert C.string_at(get(named.at, b'name', b'')) == b'row'
    start = fw.fn('tk_str_start_with', C.c_long, C.c_void_p, C.c_char_p)
    assert start(None, b'x') == 0
    cmp = fw.fn('tk_strcmp', C.c_long, C.c_void_p, C.c_void_p)
    a = C.create_string_buffer(b'a')
    assert (cmp(None, C.addressof(a)), cmp(C.addressof(a), None), cmp(None, None)) == (-1, 1, 0)
